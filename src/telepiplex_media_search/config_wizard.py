from __future__ import annotations

from copy import deepcopy
import time
from urllib.parse import urlparse


SESSION_TTL_SECONDS = 30 * 60

def _owner(request: dict) -> tuple[int, int]:
    return int(request.get("chat_id") or 0), int(request.get("user_id") or 0)


def _text(value) -> str:
    value = str(value or "").strip().strip("`").strip('"').strip("'")
    if not value or "\n" in value or "\r" in value:
        raise ValueError("value must be one non-empty line")
    return value


def _url(value) -> str:
    value = _text(value).rstrip("/")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("URL must use http or https")
    return value


class MediaSearchConfigWizard:
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
        search = (self.config.get("search") or {}).get("prowlarr") or {}
        tvdb = (self.config.get("metadata") or {}).get("tvdb") or {}
        ai = self.config.get("ai") or {}
        return {
            "actions": [{
                "kind": "send_message",
                "text": (
                    "media-search 配置\n\n"
                    f"Prowlarr：{'已配置' if search.get('api_key') else '未配置'}\n"
                    f"TVDB：{'启用' if tvdb.get('enable') else '停用'}，"
                    f"{'已配置' if tvdb.get('api_key') else '未配置'}\n"
                    f"AI：{'启用' if ai.get('enable') else '停用'}，"
                    f"{'已配置' if ai.get('api_key') else '未配置'}\n\n"
                    "请选择要修改的配置。内部参数请直接编辑 YAML。"
                ),
                "data": {"keyboard": [
                    [{"text": "Prowlarr", "callback_data": "media-search:config:prowlarr"}],
                    [{"text": "TVDB", "callback_data": "media-search:config:tvdb"}],
                    [{"text": "AI", "callback_data": "media-search:config:ai"}],
                    [{"text": "取消", "callback_data": "media-search:config:cancel"}],
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
            return self._message("已取消 media-search 配置。", "close", edit=True)
        if session.get("stage") == "confirm" and action == "confirm":
            patch = deepcopy(session["patch"])
            self.sessions.pop(key, None)
            return {
                "actions": [],
                "session": {"state": "close"},
                "config_patch": patch,
            }
        if session.get("stage") == "choose" and action == "prowlarr":
            self._replace_session(key, {"stage": "prowlarr_url", "values": {}})
            return self._message(
                "请发送 Prowlarr 地址，例如 http://prowlarr:9696。",
                "open",
                edit=True,
            )
        if session.get("stage") == "choose" and action in {"tvdb", "ai"}:
            self._replace_session(key, {
                "stage": "boolean",
                "section": action,
                "values": {},
            })
            return {
                "actions": [{
                    "kind": "edit_message",
                    "text": f"是否启用 {action.upper()}？",
                    "data": {"keyboard": [[
                        {"text": "启用", "callback_data": "media-search:config:boolean:on"},
                        {"text": "停用", "callback_data": "media-search:config:boolean:off"},
                    ]]},
                }],
                "session": {"state": "open"},
            }
        if session.get("stage") == "boolean" and action in {
            "boolean:on", "boolean:off"
        }:
            enabled = action.endswith(":on")
            section = session["section"]
            if not enabled:
                patch = (
                    {"metadata": {"tvdb": {"enable": False}}}
                    if section == "tvdb"
                    else {"ai": {"enable": False}}
                )
                return self._finish(key, patch)
            session["values"]["enable"] = True
            session["stage"] = "tvdb_api_key" if section == "tvdb" else "ai_url"
            prompt = (
                "请发送 TVDB API Key。发送 - 保留当前值。"
                if section == "tvdb"
                else "请发送 AI API 地址，例如 https://api.example/v1。"
            )
            return self._message(prompt, "open", edit=True)
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
            if stage == "prowlarr_url":
                values["base_url"] = _url(raw)
                session["stage"] = "prowlarr_api_key"
                return self._message(
                    "请发送 Prowlarr API Key。发送 - 保留当前值。",
                    "open",
                )
            if stage == "prowlarr_api_key":
                current = ((self.config.get("search") or {}).get("prowlarr") or {})
                values["api_key"] = self._secret(raw, current.get("api_key"))
                return self._finish(key, {
                    "search": {
                        "enable": True,
                        "prowlarr": deepcopy(values),
                    },
                })
            if stage == "tvdb_api_key":
                current = ((self.config.get("metadata") or {}).get("tvdb") or {})
                values["api_key"] = self._secret(raw, current.get("api_key"))
                session["stage"] = "tvdb_pin"
                return self._message(
                    "请发送 TVDB Subscriber PIN；发送 - 保留，发送 clear 清空。",
                    "open",
                )
            if stage == "tvdb_pin":
                current = ((self.config.get("metadata") or {}).get("tvdb") or {})
                values["subscriber_pin"] = self._optional_secret(
                    raw, current.get("subscriber_pin")
                )
                return self._finish(key, {"metadata": {"tvdb": deepcopy(values)}})
            if stage == "ai_url":
                values["api_url"] = _url(raw)
                session["stage"] = "ai_api_key"
                return self._message(
                    "请发送 AI API Key。发送 - 保留当前值。",
                    "open",
                )
            if stage == "ai_api_key":
                current = self.config.get("ai") or {}
                values["api_key"] = self._secret(raw, current.get("api_key"))
                session["stage"] = "ai_model"
                return self._message("请发送 AI 模型名称。", "open")
            if stage == "ai_model":
                values["model"] = _text(raw)
                return self._finish(key, {"ai": deepcopy(values)})
        except ValueError:
            return self._message("⚠️ 输入无效，请按提示重新发送。", "open")
        return self._message("⚠️ 配置会话已失效，请重新打开 /config。", "close")

    @staticmethod
    def _secret(raw: str, current) -> str:
        if raw == "-":
            return _text(current)
        return _text(raw)

    @staticmethod
    def _optional_secret(raw: str, current) -> str:
        if raw == "-":
            return str(current or "")
        if raw.lower() == "clear":
            return ""
        return _text(raw)

    def _finish(self, key, patch: dict) -> dict:
        self._replace_session(
            key, {"stage": "confirm", "patch": deepcopy(patch)}
        )
        return {
            "actions": [{
                "kind": "send_message",
                "text": "配置已收集，敏感值不会回显。确认保存并重新加载 Feature？",
                "data": {"keyboard": [[
                    {"text": "确认保存", "callback_data": "media-search:config:confirm"},
                    {"text": "取消", "callback_data": "media-search:config:cancel"},
                ]]},
            }],
            "session": {"state": "open"},
        }

    @staticmethod
    def _message(text: str, state: str, *, edit=False) -> dict:
        return {
            "actions": [{
                "kind": "edit_message" if edit else "send_message",
                "text": text,
            }],
            "session": {"state": state},
        }
