from __future__ import annotations

from copy import deepcopy
import time
from urllib.parse import urlparse


SESSION_TTL_SECONDS = 30 * 60

def _owner(request: dict) -> tuple[int, int]:
    return int(request.get("chat_id") or 0), int(request.get("user_id") or 0)


def _line(value) -> str:
    value = str(value or "").strip().strip("`").strip('"').strip("'")
    if not value or "\n" in value or "\r" in value:
        raise ValueError("one non-empty line required")
    return value


def _url(value) -> str:
    value = _line(value).rstrip("/")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("http(s) URL required")
    return value


class PlexConfigWizard:
    def __init__(self, config: dict):
        self.config = config
        self.sessions: dict[tuple[int, int], dict] = {}

    def has_session(self, request: dict) -> bool:
        return self._get_session(_owner(request)) is not None

    def clear(self, request: dict):
        self.sessions.pop(_owner(request), None)

    def _replace_session(self, key, value: dict):
        self.sessions[key] = {
            **value,
            "expires_at": time.monotonic() + SESSION_TTL_SECONDS,
        }

    def _get_session(self, key):
        session = self.sessions.get(key)
        if not session:
            return None
        if float(session.get("expires_at") or 0) <= time.monotonic():
            self.sessions.pop(key, None)
            return None
        return session

    def start(self, request: dict) -> dict:
        key = _owner(request)
        self._replace_session(key, {"stage": "choose"})
        plex = self.config.get("plex") or {}
        tmdb = self.config.get("tmdb") or {}
        fanart = self.config.get("fanart") or {}
        ai = self.config.get("ai") or {}
        return {
            "actions": [{
                "kind": "send_message",
                "text": (
                    "plex-management 配置\n\n"
                    f"Plex：{'已配置' if plex.get('token') else '未配置'}\n"
                    f"TMDB：{'已配置' if tmdb.get('api_key') else '未配置'}\n"
                    f"Fanart：{'已配置' if fanart.get('api_key') else '未配置'}\n"
                    f"AI：{'启用' if ai.get('enabled') else '停用'}，"
                    f"{'已配置' if ai.get('api_key') else '未配置'}\n\n"
                    "请选择要修改的配置。内部参数请直接编辑 YAML。"
                ),
                "data": {"keyboard": [
                    [{"text": "Plex", "callback_data": "plex:config:plex"}],
                    [{"text": "TMDB", "callback_data": "plex:config:tmdb"}],
                    [{"text": "Fanart", "callback_data": "plex:config:fanart"}],
                    [{"text": "AI", "callback_data": "plex:config:ai"}],
                    [{"text": "取消", "callback_data": "plex:config:cancel"}],
                ]},
            }],
            "session": {"state": "open"},
        }

    def callback(self, request: dict) -> dict:
        key = _owner(request)
        session = self._get_session(key)
        payload = str(request.get("payload") or "")
        if not session or not payload.startswith("config:"):
            return self._message("⚠️ 配置会话已失效，请重新打开 /config。", "close")
        action = payload.split(":", 1)[1]
        if action == "cancel":
            self.sessions.pop(key, None)
            return self._message("已取消 plex-management 配置。", "close", edit=True)
        if session.get("stage") == "confirm" and action == "confirm":
            patch = deepcopy(session["patch"])
            self.sessions.pop(key, None)
            return {
                "actions": [],
                "session": {"state": "close"},
                "config_patch": patch,
            }
        if session.get("stage") == "choose" and action == "plex":
            self._replace_session(key, {"stage": "plex_url", "values": {}})
            return self._message("请发送 Plex 地址，例如 http://plex:32400。", "open", edit=True)
        if session.get("stage") == "choose" and action in {"tmdb", "fanart"}:
            self._replace_session(
                key, {"stage": "provider_key", "section": action}
            )
            return self._message(
                f"请发送 {action.upper()} API Key。发送 - 保留当前值。",
                "open",
                edit=True,
            )
        if session.get("stage") == "choose" and action == "ai":
            self._replace_session(key, {"stage": "ai_boolean", "values": {}})
            return {
                "actions": [{
                    "kind": "edit_message",
                    "text": "是否启用 AI？",
                    "data": {"keyboard": [[
                        {"text": "启用", "callback_data": "plex:config:boolean:on"},
                        {"text": "停用", "callback_data": "plex:config:boolean:off"},
                    ]]},
                }],
                "session": {"state": "open"},
            }
        if session.get("stage") == "ai_boolean" and action in {
            "boolean:on", "boolean:off"
        }:
            if action.endswith(":off"):
                return self._finish(key, {"ai": {"enabled": False}})
            session["values"]["enabled"] = True
            session["stage"] = "ai_url"
            return self._message(
                "请发送 AI API 地址，例如 https://api.example/v1。",
                "open",
                edit=True,
            )
        return self._message("⚠️ 配置操作与当前步骤不匹配。", "open")

    def message(self, request: dict) -> dict:
        key = _owner(request)
        session = self._get_session(key)
        if not session:
            return self._message("⚠️ 配置会话已失效，请重新打开 /config。", "close")
        raw = str(request.get("text") or "").strip()
        stage = session.get("stage")
        values = session.setdefault("values", {})
        try:
            if stage == "plex_url":
                values["base_url"] = _url(raw)
                session["stage"] = "plex_token"
                return self._message("请发送 Plex Token。发送 - 保留当前值。", "open")
            if stage == "plex_token":
                values["token"] = self._secret(
                    raw, (self.config.get("plex") or {}).get("token")
                )
                return self._finish(key, {"plex": deepcopy(values)})
            if stage == "provider_key":
                section = session["section"]
                secret = self._secret(
                    raw, (self.config.get(section) or {}).get("api_key")
                )
                return self._finish(key, {section: {"api_key": secret}})
            if stage == "ai_url":
                values["api_url"] = _url(raw)
                session["stage"] = "ai_key"
                return self._message("请发送 AI API Key。发送 - 保留当前值。", "open")
            if stage == "ai_key":
                values["api_key"] = self._secret(
                    raw, (self.config.get("ai") or {}).get("api_key")
                )
                session["stage"] = "ai_model"
                return self._message("请发送 AI 模型名称。", "open")
            if stage == "ai_model":
                values["model"] = _line(raw)
                return self._finish(key, {"ai": deepcopy(values)})
        except ValueError:
            return self._message("⚠️ 输入无效，请按提示重新发送。", "open")
        return self._message("⚠️ 配置会话已失效，请重新打开 /config。", "close")

    @staticmethod
    def _secret(raw: str, current) -> str:
        return _line(current) if raw == "-" else _line(raw)

    def _finish(self, key, patch: dict) -> dict:
        self._replace_session(
            key, {"stage": "confirm", "patch": deepcopy(patch)}
        )
        return {
            "actions": [{
                "kind": "send_message",
                "text": "配置已收集，敏感值不会回显。确认保存并重新加载 Feature？",
                "data": {"keyboard": [[
                    {"text": "确认保存", "callback_data": "plex:config:confirm"},
                    {"text": "取消", "callback_data": "plex:config:cancel"},
                ]]},
            }],
            "session": {"state": "open"},
        }

    @staticmethod
    def _message(text: str, state: str, *, edit=False) -> dict:
        return {
            "actions": [{"kind": "edit_message" if edit else "send_message", "text": text}],
            "session": {"state": state},
        }
