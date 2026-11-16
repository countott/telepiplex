from __future__ import annotations

import time


SESSION_TTL_SECONDS = 30 * 60

def _owner(request: dict) -> tuple[int, int]:
    return int(request.get("chat_id") or 0), int(request.get("user_id") or 0)


class RenameConfigWizard:
    def __init__(self, config: dict):
        self.config = config
        self.sessions: dict[tuple[int, int], dict] = {}

    def has_session(self, request: dict) -> bool:
        return self._get_session(_owner(request)) is not None

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
        ai = self.config.get("ai") or {}
        return {
            "actions": [{
                "kind": "send_message",
                "text": (
                    "rename 配置\n\n"
                    f"AI：{'启用' if ai.get('enable') else '停用'}，"
                    f"{'已配置' if ai.get('api_key') else '未配置'}\n\n"
                    "请选择要修改的配置。内部参数请直接编辑 YAML。"
                ),
                "data": {"keyboard": [
                    [{"text": "AI", "callback_data": "rename:config:ai"}],
                    [{"text": "退出", "callback_data": "rename:config:cancel"}],
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
            return self._message("已退出 rename 配置。", "close", edit=True)
        target = "rename_ai" if action=="ai" or session.get("stage")!="choose" else None
        if target:
            return self._navigate(key,target)
        return self._message("⚠️ 配置操作与当前步骤不匹配。", "open")

    def message(self, request):
        key = _owner(request)
        session = self._get_session(key)
        target = "rename_ai" if session and session.get("stage")!="choose" else None
        if target:
            return self._navigate(key,target)
        return self._message("请选择当前配置面板上的操作。", "open" if session else "close")

    def _navigate(self,key,target):
        self.sessions.pop(key,None)
        result = self._message("请使用 /config 编辑此项基础配置。","close")
        result["config_navigation"] = target
        return result

    @staticmethod
    def _message(text: str, state: str, *, edit=False) -> dict:
        action = {
            "kind": "edit_message" if edit else "send_message",
            "text": text,
        }
        if state == "open":
            action["data"] = {"keyboard": [[{
                "text": "退出",
                "callback_data": "rename:config:cancel",
            }]]}
        return {
            "actions": [action],
            "session": {"state": state},
        }
