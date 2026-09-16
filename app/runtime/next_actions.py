"""Disposable post-rename choices, separate from immutable completion cards."""
from __future__ import annotations

import asyncio
import json
import logging
import math
import sqlite3
import time
import uuid
from weakref import WeakValueDictionary

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest


NEXT_ACTIONS_KEY = "telepiplex_next_actions"
CALLBACK_PREFIX = "host-next:"
CHOICE_SECONDS = 60
_LOGGER = logging.getLogger(__name__)


class NextActions:
    def __init__(self, application, coordinator, router, *, clock=time.time):
        self.application = application
        self.coordinator = coordinator
        self.router = router
        self.clock = clock
        self.db = sqlite3.connect(coordinator.database_path, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("CREATE TABLE IF NOT EXISTS next_action_cards ("
                        "operation_id TEXT PRIMARY KEY, token TEXT UNIQUE NOT NULL, "
                        "chat_id INTEGER NOT NULL, user_id INTEGER NOT NULL, payload TEXT NOT NULL, "
                        "actions TEXT NOT NULL DEFAULT '[]', state TEXT NOT NULL, "
                        "message_id INTEGER, expires REAL NOT NULL, last_tick INTEGER DEFAULT 60, "
                        "delete_pending INTEGER NOT NULL DEFAULT 0, next_delete REAL NOT NULL DEFAULT 0, "
                        "choice TEXT NOT NULL DEFAULT '', next_operation_id TEXT NOT NULL DEFAULT '')")
        self._task = None
        self._locks = WeakValueDictionary()

    def start(self):
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.run(), name="telepiplex-next-actions")

    async def close(self):
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self.db.close()

    def get(self, token):
        row = self.db.execute("SELECT * FROM next_action_cards WHERE token=?", (token,)).fetchone()
        return dict(row) if row else None

    def _lock(self, token):
        return self._locks.setdefault(token, asyncio.Lock())

    async def _available(self, plugin, capability, method, payload):
        route = self.router.plugin_route(plugin)
        if route is None:
            return False
        try:
            result = await route.client.request("capability.call", {
                "capability": capability, "method": method, "payload": payload,
            }, deadline=3)
            return isinstance(result, dict) and result.get("available") is True
        except Exception:
            return False

    def markup(self, card, remaining):
        labels = {"continue": "继续找这部剧", "scan": "扫描 Plex", "exit": f"退出（{remaining}s）"}
        return InlineKeyboardMarkup([
            [InlineKeyboardButton(labels[action], callback_data=f"{CALLBACK_PREFIX}{card['token']}:{action}")]
            for action in json.loads(card["actions"])
        ])

    async def offer(self, record):
        """Called only for sealed successful rename results; INSERT is the replay guard."""
        payload = dict(record.details.get("next_actions") or {})
        if payload.get("kind") != "post_rename":
            return
        token = uuid.uuid4().hex[:20]
        inserted = self.db.execute(
            "INSERT OR IGNORE INTO next_action_cards "
            "(operation_id,token,chat_id,user_id,payload,state,expires) VALUES (?,?,?,?,?,'preparing',?)",
            (record.operation_id, token, record.chat_id, record.user_id,
             json.dumps(payload), self.clock() + CHOICE_SECONDS),
        ).rowcount
        if not inserted:
            return
        owner = {"chat_id": record.chat_id, "user_id": record.user_id,
                 "resume_operation_id": record.operation_id}
        can_continue, can_scan = await asyncio.gather(
            self._available("search", "media.search", "continuation_status", owner),
            self._available("sync", "library.sync", "scan_status", {}),
        )
        actions = (["continue"] if can_continue else []) + (["scan"] if can_scan else []) + ["exit"]
        self.db.execute("UPDATE next_action_cards SET actions=? WHERE token=?", (json.dumps(actions), token))
        if self.coordinator.active(record.chat_id, record.user_id) is not None:
            self.db.execute("UPDATE next_action_cards SET state='closed' WHERE token=?", (token,))
            return
        card = self.get(token)
        try:
            # Do not resend an ambiguous Telegram send after a crash or timeout.
            self.db.execute("UPDATE next_action_cards SET state='sending' WHERE token=?", (token,))
            sent = await asyncio.wait_for(self.application.bot.send_message(
                chat_id=record.chat_id,
                text="请选择下一步。60 秒内未选择将自动退出并删除此消息。",
                reply_markup=self.markup(card, CHOICE_SECONDS),
            ), 10)
            message_id = int(sent.message_id)
            self.db.execute("UPDATE next_action_cards SET state='open',message_id=?,expires=? WHERE token=?",
                            (message_id, self.clock() + CHOICE_SECONDS, token))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.db.execute("UPDATE next_action_cards SET state='uncertain' WHERE token=?", (token,))
            _LOGGER.warning("Telepiplex next-action delivery failed: %s", type(exc).__name__)

    def _close_card(self, token, choice="exit"):
        return self.db.execute("UPDATE next_action_cards SET state='closed',delete_pending=1,choice=? "
                               "WHERE token=? AND state='open'", (choice, token)).rowcount == 1

    async def _delete(self, card):
        if not card["message_id"]:
            return
        try:
            result = await asyncio.wait_for(self.application.bot.delete_message(
                chat_id=card["chat_id"], message_id=card["message_id"],
            ), 5)
            if result is False or result is None:
                raise RuntimeError("delete_not_acknowledged")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if not (isinstance(exc, BadRequest) and "message to delete not found" in str(exc).casefold()):
                self.db.execute("UPDATE next_action_cards SET next_delete=? WHERE token=?",
                                (self.clock() + 5, card["token"]))
                return
        self.db.execute("UPDATE next_action_cards SET delete_pending=0 WHERE token=?", (card["token"],))

    async def callback(self, update):
        query = update.callback_query
        parts = str(query.data or "").split(":")
        if len(parts) != 3:
            return
        _, token, action = parts
        async with self._lock(token):
            card = self.get(token)
            if (card is None or card["chat_id"] != update.effective_chat.id
                    or card["user_id"] != update.effective_user.id
                    or card["message_id"] != getattr(query.message, "message_id", None)):
                await query.answer("此选择不属于当前任务。")
                return
            if card["state"] != "open" or action not in json.loads(card["actions"]):
                await query.answer("此选择已结束。")
                return
            expired = self.clock() >= card["expires"]
            # Consumption is durable before any Telegram or Feature I/O.
            if not self._close_card(token, "exit" if expired else action):
                await query.answer("此选择已结束。")
                return
            try:
                await asyncio.wait_for(query.answer("已自动退出。" if expired else None), 2)
            except Exception:
                pass
            await self._delete(card)
        if expired or action == "exit":
            return
        if self.coordinator.active(card["chat_id"], card["user_id"]) is not None:
            await self.application.bot.send_message(chat_id=card["chat_id"], text="请先完成或退出当前任务。")
            return
        command = "s" if action == "continue" else "scan"
        route = self.router.command_route(command)
        try:
            if route is None:
                raise RuntimeError("feature_unavailable")
            params = {"command": command, "args": [], "chat_id": card["chat_id"],
                      "user_id": card["user_id"], "update_id": getattr(update, "update_id", None),
                      "parent_operation_id": card["operation_id"]}
            if action == "continue":
                params["resume_operation_id"] = card["operation_id"]
            else:
                params["post_rename"] = json.loads(card["payload"])
            result = await route.client.request("command.dispatch", params, deadline=30,
                                                 idempotency_key=f"next-action:{token}")
            await self._deliver_result(route, result, card)
        except Exception as exc:
            _LOGGER.warning("Telepiplex next-action launch failed: %s", type(exc).__name__)
            await self.application.bot.send_message(chat_id=card["chat_id"],
                text=f"未能确认下一步任务已启动，请查看当前任务状态；可使用 /{command} 重新进入。")

    async def _deliver_result(self, route, result, card):
        from app.handlers.interaction_handler import render_operation
        from app.handlers.plugin_handler import _keyboard_markup, _with_rendered_keyboard

        operation = result.get("operation")
        if operation is not None:
            if (operation.get("chat_id"), operation.get("user_id")) != (card["chat_id"], card["user_id"]):
                raise ValueError("next_action_owner_conflict")
            self.db.execute("UPDATE next_action_cards SET next_operation_id=? WHERE token=?",
                            (str(operation.get("operation_id") or ""), card["token"]))
            operation = _with_rendered_keyboard(route, result, operation)
            if operation.get("segment"):
                record, _segment = self.coordinator.accept_segment_report(route.plugin_id, operation)
            else:
                record = self.coordinator.report(route.plugin_id, operation)
            await render_operation(self.application, self.router, record)
            return
        for action in result.get("actions") or []:
            markup = _keyboard_markup(route, action.get("data") or {})
            if markup is False:
                raise ValueError("invalid_next_action_keyboard")
            await self.application.bot.send_message(chat_id=card["chat_id"], text=action["text"],
                                                    reply_markup=markup)

    async def run_once(self):
        now = self.clock()
        # Old successful jobs must not generate fresh prompts after a restart.
        rows = self.db.execute(
            "SELECT o.operation_id FROM operations o WHERE o.plugin_id='rename' AND o.state='completed' "
            "AND o.updated_at>=? AND NOT EXISTS (SELECT 1 FROM next_action_cards n WHERE n.operation_id=o.operation_id) "
            "AND EXISTS (SELECT 1 FROM operation_message_segments s WHERE s.operation_id=o.operation_id "
            "AND s.role='rename' AND s.state='sealed')", (now - CHOICE_SECONDS,),
        ).fetchall()
        for row in rows:
            record = self.coordinator.get(row["operation_id"])
            if record is not None and record.details.get("next_actions"):
                await self.offer(record)
        for row in self.db.execute("SELECT token FROM next_action_cards WHERE state='open' OR delete_pending=1").fetchall():
            token = row["token"]
            async with self._lock(token):
                card = self.get(token)
                now = self.clock()
                if card["state"] == "open":
                    remaining = max(0, math.ceil(card["expires"] - now))
                    if remaining == 0 or self.coordinator.active(card["chat_id"], card["user_id"]) is not None:
                        self._close_card(token)
                        card = self.get(token)
                    elif card["last_tick"] - remaining >= 5:
                        self.db.execute("UPDATE next_action_cards SET last_tick=? WHERE token=?", (remaining, token))
                        try:
                            await asyncio.wait_for(self.application.bot.edit_message_reply_markup(
                                chat_id=card["chat_id"], message_id=card["message_id"],
                                reply_markup=self.markup(card, remaining),
                            ), 3)
                        except Exception:
                            pass
                if card["delete_pending"] and card["next_delete"] <= now:
                    await self._delete(card)
        self.db.execute("DELETE FROM next_action_cards WHERE expires<? AND delete_pending=0", (now - 7 * 86400,))

    async def run(self):
        while True:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _LOGGER.warning("Telepiplex next-action worker failed: %s", type(exc).__name__)
            await asyncio.sleep(1)
