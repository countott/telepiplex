import asyncio
import json
from pathlib import Path
import tempfile
import time
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

from app.runtime.interaction_coordinator import InteractionCoordinator
from app.runtime.next_actions import NextActions


class NextActionsTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.coordinator = InteractionCoordinator(Path(self.temp.name) / "host.db")
        self.now = time.time()
        self.bot = SimpleNamespace(
            send_message=AsyncMock(return_value=SimpleNamespace(message_id=200)),
            delete_message=AsyncMock(return_value=True),
            edit_message_reply_markup=AsyncMock(return_value=True),
        )
        self.application = SimpleNamespace(bot=self.bot, bot_data={
            "telepiplex_interaction_coordinator": self.coordinator,
        })
        self.client = SimpleNamespace(request=AsyncMock(return_value={"available": True}))
        self.route = SimpleNamespace(plugin_id="search", client=self.client)
        self.router = SimpleNamespace(plugin_route=Mock(return_value=self.route),
                                      command_route=Mock(return_value=self.route))
        self.worker = NextActions(self.application, self.coordinator, self.router, clock=lambda: self.now)
        self.record = SimpleNamespace(operation_id="old", chat_id=10, user_id=1, details={
            "next_actions": {"kind": "post_rename", "final_path": "/TV/Show"},
        })

    async def asyncTearDown(self):
        await self.worker.close()
        self.coordinator.close()
        self.temp.cleanup()

    async def offer(self):
        await self.worker.offer(self.record)
        row = self.worker.db.execute("SELECT token FROM next_action_cards").fetchone()
        return self.worker.get(row[0])

    def update(self, card, action, *, user_id=1, message_id=200):
        return SimpleNamespace(update_id=1, effective_chat=SimpleNamespace(id=10),
            effective_user=SimpleNamespace(id=user_id), callback_query=SimpleNamespace(
                data=f"host-next:{card['token']}:{action}", answer=AsyncMock(),
                message=SimpleNamespace(message_id=message_id)))

    async def test_hide_unconfigured_scan_without_invoking_scan_or_contacting_plex(self):
        async def status(_method, params, **_kwargs):
            return {"available": params["method"] == "continuation_status"}
        self.client.request.side_effect = status
        card = await self.offer()
        assert json.loads(card["actions"]) == ["continue", "exit"]
        assert all(call.args[0] == "capability.call" for call in self.client.request.await_args_list)
        assert "60s" in self.bot.send_message.await_args.kwargs["reply_markup"].inline_keyboard[-1][0].text

    async def test_all_buttons_delete_choice_first_and_only_continue_launches_fresh_search(self):
        for action in ("continue", "scan", "exit"):
            with self.subTest(action=action):
                self.record.operation_id = action
                await self.worker.offer(self.record)
                token = self.worker.db.execute("SELECT token FROM next_action_cards WHERE operation_id=?", (action,)).fetchone()[0]
                card = self.worker.get(token)
                self.client.request.reset_mock()
                self.bot.delete_message.reset_mock()
                async def launch(method, params, **kwargs):
                    self.bot.delete_message.assert_awaited_once_with(chat_id=10, message_id=200)
                    assert method == "command.dispatch"
                    assert params["command"] == ("s" if action == "continue" else "scan")
                    assert bool(params.get("resume_operation_id")) == (action == "continue")
                    return {"actions": []}
                self.client.request.side_effect = launch
                self.worker._deliver_result = AsyncMock()
                await self.worker.callback(self.update(card, action))
                assert self.worker.get(token)["state"] == "closed"
                self.bot.delete_message.assert_awaited_once_with(chat_id=10, message_id=200)
                assert self.client.request.await_count == (0 if action == "exit" else 1)
                self.client.request.side_effect = None

    async def test_duplicate_click_and_timeout_race_dispatch_at_most_once(self):
        card = await self.offer()
        self.worker._deliver_result = AsyncMock()
        self.client.request.reset_mock()
        await asyncio.gather(self.worker.callback(self.update(card, "continue")),
                             self.worker.callback(self.update(card, "scan")), self.worker.run_once())
        assert self.client.request.await_count == 1
        assert self.bot.delete_message.await_count == 1

    async def test_expired_click_exits_and_never_launches(self):
        card = await self.offer()
        self.now = card["expires"]
        self.client.request.reset_mock()
        await self.worker.callback(self.update(card, "continue"))
        self.client.request.assert_not_awaited()
        self.bot.delete_message.assert_awaited_once_with(chat_id=10, message_id=200)

    async def test_countdown_and_expiry_survive_restart_without_touching_completion(self):
        card = await self.offer()
        self.now += 5
        await self.worker.run_once()
        assert "55s" in self.bot.edit_message_reply_markup.await_args.kwargs["reply_markup"].inline_keyboard[-1][0].text
        await self.worker.close()
        self.worker = NextActions(self.application, self.coordinator, self.router, clock=lambda: self.now)
        self.now = card["expires"]
        await self.worker.run_once()
        self.bot.delete_message.assert_awaited_once_with(chat_id=10, message_id=200)
        assert self.bot.send_message.await_count == 1
        assert self.worker.get(card["token"])["state"] == "closed"

    async def test_wrong_owner_message_or_action_cannot_consume_the_card(self):
        card = await self.offer()
        for update in (self.update(card, "continue", user_id=2),
                       self.update(card, "continue", message_id=201),
                       self.update(card, "forged")):
            await self.worker.callback(update)
        assert self.worker.get(card["token"])["state"] == "open"
        self.bot.delete_message.assert_not_awaited()

    async def test_failed_delete_is_retried_after_restart_without_replaying_action(self):
        card = await self.offer()
        self.bot.delete_message.side_effect = RuntimeError("offline")
        await self.worker.callback(self.update(card, "exit"))
        assert self.worker.get(card["token"])["delete_pending"] == 1
        await self.worker.close()
        self.worker = NextActions(self.application, self.coordinator, self.router, clock=lambda: self.now)
        self.now += 6
        self.bot.delete_message.side_effect = None
        await self.worker.run_once()
        assert self.worker.get(card["token"])["delete_pending"] == 0
        assert self.bot.send_message.await_count == 1

    async def test_ambiguous_send_is_not_repeated(self):
        self.bot.send_message.side_effect = TimeoutError()
        card = await self.offer()
        assert card["state"] == "uncertain"
        await self.worker.offer(self.record)
        assert self.bot.send_message.await_count == 1

    async def test_only_sealed_completed_rename_produces_one_prompt(self):
        report = {"operation_id": "real", "chat_id": 10, "user_id": 1,
                  "state": "completed", "stage": "completed", "control": "", "revision": 1,
                  "status_text": "整理完成", "details": self.record.details,
                  "segment": {"role": "rename", "presentation_kind": "text"}}
        record, segment = self.coordinator.accept_segment_report("rename", report)
        segment = self.coordinator.bind_segment_message(segment.segment_id, owner_plugin_id="rename",
                                                        generation=segment.generation, chat_id=10, message_id=99)
        self.coordinator.record_segment_rendered(segment.segment_id, owner_plugin_id="rename",
            generation=segment.generation, business_revision=1, projection_hash=segment.projection_hash)
        await self.worker.run_once()
        self.bot.send_message.assert_not_awaited()
        self.coordinator.seal_segment("rename", "real", "rename")
        self.coordinator.ack_message_cleanup("real", 99)
        self.coordinator.complete_segment_seal(segment.segment_id, owner_plugin_id="rename", generation=segment.generation)
        await self.worker.run_once()
        await self.worker.run_once()
        self.bot.send_message.assert_awaited_once()
        assert self.coordinator.get("real").state == "completed"
        self.bot.delete_message.assert_not_awaited()

    async def test_real_search_result_creates_a_new_text_message_and_bound_scope_buttons(self):
        root = Path(__file__).resolve().parents[1]
        sys.path.insert(0, str(root / "features/search/src"))
        try:
            from telepiplex_search.service import SearchFeature
        finally:
            sys.path.pop(0)
        feature = SearchFeature(config={}, host=SimpleNamespace())
        contract = {
            "schema_version": 1, "metadata_id": "old", "confirmed": False,
            "identity": {"content_kind": "series", "english_title": "Series", "year": "2020"},
            "placement": {"library_type": "series", "category_kind": "live_action_series"},
            "retrieval": {"media_type": "series", "scope": "work"},
            "items": [{"season_number": season, "episode_number": 1, "aired": "2020-01-01"}
                      for season in (1, 2)],
            "evidence": {"series_inventory": {"season_totals": {1: 1, 2: 1}}},
        }
        feature._remember_series({"owner": (10, 1), "operation_id": "old", "plan": {}},
                                 {"media_metadata": contract})
        card = await self.offer()
        self.route.manifest = SimpleNamespace(callbacks=("search",))
        async def dispatch(method, params, **_kwargs):
            assert method == "command.dispatch"
            return await feature.command(params)
        self.client.request.side_effect = dispatch
        self.bot.send_message.return_value = SimpleNamespace(message_id=201)
        await self.worker.callback(self.update(card, "continue"))
        new = self.coordinator.active(10, 1)
        assert new is not None and new.operation_id != "old"
        assert new.stage == "series_scope"
        assert new.details["parent_operation_id"] == "old"
        assert self.coordinator.get_active_segment(new.operation_id).message_id == 201
        self.bot.delete_message.assert_awaited_once_with(chat_id=10, message_id=200)
        sent = self.bot.send_message.await_args.kwargs
        assert "Series" in sent["text"]
        assert sent["reply_markup"] is not None
        assert all("old" not in button.callback_data for row in sent["reply_markup"].inline_keyboard for button in row)
