from __future__ import annotations

import time


SESSION_TTL_SECONDS = 30 * 60

def _owner(request: dict) -> tuple[int, int]:
    return int(request.get("chat_id") or 0), int(request.get("user_id") or 0)


class SyncConfigWizard:
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
        return {
            "actions": [{
                "kind": "send_message",
                "text": (
                    "sync 配置\n\n"
                    f"Plex：{'已配置' if plex.get('token') else '未配置'}\n"
                    f"TMDB：{'已配置' if tmdb.get('api_key') else '未配置'}\n"
                    f"Fanart：{'已配置' if fanart.get('api_key') else '未配置'}\n\n"
                    "请选择要修改的配置。内部参数请直接编辑 YAML。"
                ),
                "data": {"keyboard": [
                    [{"text": "Plex", "callback_data": "sync:config:plex"}],
                    [{"text": "TMDB", "callback_data": "sync:config:tmdb"}],
                    [{"text": "Fanart", "callback_data": "sync:config:fanart"}],
                    [{"text": "退出", "callback_data": "sync:config:cancel"}],
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
            return self._message("已退出 sync 配置。", "close", edit=True)
        target = {"tmdb":"shared_tmdb","shared_tmdb":"shared_tmdb","plex":"sync_plex","fanart":"sync_fanart"}.get(action) or self._old_target(session)
        if target:
            return self._navigate(key,target)
        return self._message("⚠️ 配置操作与当前步骤不匹配。", "open")

    def message(self, request):
        key = _owner(request)
        session = self._get_session(key)
        target = self._old_target(session or {})
        if target:
            return self._navigate(key,target)
        return self._message("请选择当前配置面板上的操作。", "open" if session else "close")

    @staticmethod
    def _old_target(session):
        for section,target in [("tmdb","shared_tmdb"),("fanart","sync_fanart"),("plex","sync_plex")]:
            if section in (session.get("patch") or {}) or session.get("section")==section or str(session.get("stage")).startswith(section+"_"):
                return target
        return None

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
                "callback_data": "sync:config:cancel",
            }]]}
        return {
            "actions": [action],
            "session": {"state": state},
        }
