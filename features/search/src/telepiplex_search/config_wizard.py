from __future__ import annotations

from copy import deepcopy
import math
import re
import time
from urllib.parse import urlparse


SESSION_TTL_SECONDS = 30 * 60


def _field(label, kind, default, minimum=None, maximum=None):
    return label, kind, default, minimum, maximum


# Only fields consumed by the existing adapters are editable here.
API_PROVIDERS = {
    "prowlarr": ("Prowlarr", ("search", "prowlarr"), {
        "base_url": _field("服务地址", "url", "http://127.0.0.1:9696"),
        "api_key": _field("API Key", "secret", ""),
        "timeout": _field("总超时（秒）", "number", 200, 1, 600),
        "indexer_timeout": _field("单站超时（秒）", "number", 75, 1, 600),
        "status_timeout": _field("状态超时（秒）", "number", 15, 1, 300),
        "indexer_ids": _field("启用站点 IDs", "indexers", "-2"),
        "first_wave_indexer_ids": _field("优先站点 IDs", "ids", []),
        "wave_delay": _field("其他站点延迟（秒）", "number", 1.5, 0, 30),
        "result_limit": _field("结果数量", "integer", 12, 1, 12),
    }),
    "tmdb": ("TMDB", ("metadata", "tmdb"), {
        "enable": _field("启用", "boolean", True),
        "base_url": _field("API 地址", "url", "https://api.themoviedb.org/3"),
        "api_key": _field("API Read Access Token", "secret", ""),
        "timeout": _field("超时（秒）", "number", 15, 1, 300),
    }),
    "tvdb": ("TVDB", ("metadata", "tvdb"), {
        "enable": _field("启用", "boolean", True),
        "base_url": _field("API 地址", "url", "https://api4.thetvdb.com/v4"),
        "api_key": _field("API Key", "secret", ""),
        "subscriber_pin": _field("Subscriber PIN", "secret", ""),
        "timeout": _field("超时（秒）", "number", 15, 1, 300),
    }),
    "anilist": ("AniList", ("metadata", "anilist"), {
        "enable": _field("启用", "boolean", True),
        "endpoint": _field("GraphQL 地址", "url", "https://graphql.anilist.co"),
        "timeout": _field("超时（秒）", "number", 15, 1, 300),
    }),
    "wikipedia": ("Wikipedia", ("metadata", "wikipedia"), {
        "enable": _field("启用", "boolean", True),
        "languages": _field("语言顺序", "languages", ["zh", "en"]),
        "timeout": _field("超时（秒）", "number", 10, 1, 300),
        "min_interval": _field("请求间隔（秒）", "number", 3, 0, 30),
        "max_queries": _field("每轮查询数", "integer", 2, 1, 6),
        "rate_limit_cooldown": _field("限流冷却（秒）", "number", 30, 0, 3600),
    }),
    "douban": ("豆瓣", ("metadata", "douban"), {
        "enable": _field("启用", "boolean", True),
        "timeout": _field("超时（秒）", "number", 10, 1, 300),
        "cache_ttl": _field("缓存时长（秒）", "number", 900, 0, 86400),
        "max_concurrency": _field("请求并发数", "integer", 2, 1, 8),
        "circuit_breaker_failures": _field("连续失败阈值", "integer", 3, 1, 20),
        "circuit_breaker_seconds": _field("失败冷却（秒）", "number", 300, 1, 3600),
    }),
}

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
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username or parsed.password or any(c.isspace() for c in value)):
        raise ValueError("URL must use http or https")
    parsed.port  # Validate an explicitly supplied port.
    return value


class SearchConfigWizard:
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
        tmdb = (self.config.get("metadata") or {}).get("tmdb") or {}
        tvdb = (self.config.get("metadata") or {}).get("tvdb") or {}
        return {
            "actions": [{
                "kind": "send_message",
                "text": (
                    "search 配置\n\n"
                    f"Prowlarr：{'已配置' if search.get('api_key') else '未配置'}\n"
                    f"TMDB：{'启用' if tmdb.get('enable') else '停用'}，"
                    f"{'已配置' if tmdb.get('api_key') else '未配置'}\n"
                    f"TVDB：{'启用' if tvdb.get('enable') else '停用'}，"
                    f"{'已配置' if tvdb.get('api_key') else '未配置'}\n"
                    "\n"
                    "请选择快速配置，或进入 Provider 接口参数逐项修改。"
                ),
                "data": {"keyboard": [
                    [{"text": "Prowlarr", "callback_data": "search:config:prowlarr"}],
                    [{"text": "TMDB", "callback_data": "search:config:tmdb"}],
                    [{"text": "TVDB", "callback_data": "search:config:tvdb"}],
                    [{"text": "Provider 接口参数", "callback_data": "search:config:apis"}],
                    [{"text": "退出", "callback_data": "search:config:cancel"}],
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
            return self._message("已退出 search 配置。", "close", edit=True)
        if action == "apis" and session.get("stage") in {"choose", "api_choose", "api_fields", "api_value"}:
            session["stage"] = "api_choose"
            return self._api_menu(key)
        if session.get("stage") in {"api_choose", "api_fields", "api_value"}:
            if action == "api_save":
                patch = session.get("api_patch") or {}
                if not patch:
                    return self._api_menu(key, "尚未修改任何参数。")
                return self._finish(key, patch)
            if action.startswith("api:") and action[4:] in API_PROVIDERS:
                session.update(stage="api_fields", provider=action[4:])
                return self._api_fields(key)
            if action.startswith("field:") and session.get("stage") in {"api_fields", "api_value"}:
                parts = action.split(":")
                if len(parts) == 3 and parts[1] == session.get("provider") and parts[2] in API_PROVIDERS[parts[1]][2]:
                    name = parts[2]
                    session.update(stage="api_value", field=name)
                    label, kind, _, minimum, maximum = API_PROVIDERS[parts[1]][2][name]
                    hint = {
                        "boolean": "发送 on 启用、off 停用。",
                        "secret": "敏感值不会回显；发送 clear 清空。",
                        "ids": "发送正整数 IDs，以逗号分隔；clear 清空优先列表。",
                        "indexers": "发送正整数 IDs，以逗号分隔；-2 表示所有已启用站点。",
                        "languages": "发送语言代码，以逗号分隔，例如 zh,en。",
                    }.get(kind, "")
                    if minimum is not None:
                        hint = f"范围 {minimum}–{maximum}。"
                    return self._message(f"请输入 {API_PROVIDERS[parts[1]][0]} · {label}。\n{hint}\n发送 - 保留当前值。", "open")
            return self._message("⚠️ 参数选择已失效，请返回 Provider 列表。", "open")
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
        if session.get("stage") == "choose" and action in {"tmdb", "tvdb"}:
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
                        {"text": "启用", "callback_data": "search:config:boolean:on"},
                        {"text": "停用", "callback_data": "search:config:boolean:off"},
                    ], [{"text": "退出", "callback_data": "search:config:cancel"}]]},
                }],
                "session": {"state": "open"},
            }
        if session.get("stage") == "boolean" and action in {
            "boolean:on", "boolean:off"
        }:
            enabled = action.endswith(":on")
            section = session["section"]
            if not enabled:
                patch = {"metadata": {section: {"enable": False}}}
                return self._finish(key, patch)
            session["values"]["enable"] = True
            session["stage"] = (
                "tmdb_api_key"
                if section == "tmdb"
                else "tvdb_api_key"
                if section == "tvdb"
                else "tmdb_api_key"
            )
            prompt = (
                "请发送 TMDB API Read Access Token。发送 - 保留当前值。"
                if section == "tmdb"
                else "请发送 TVDB API Key。发送 - 保留当前值。"
                if section == "tvdb"
                else "请发送 TMDB API Read Access Token。发送 - 保留当前值。"
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
            if stage == "api_value":
                provider, name = session["provider"], session["field"]
                spec = API_PROVIDERS[provider][2][name]
                value = self._api_current(session, provider, name) if raw == "-" else self._api_parse(raw, spec)
                path = API_PROVIDERS[provider][1]
                patch = session.setdefault("api_patch", {})
                for part in path:
                    patch = patch.setdefault(part, {})
                patch[name] = deepcopy(value)
                session["stage"] = "api_fields"
                return self._api_fields(key, "修改已暂存，保存后才会生效。")
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
            if stage == "tmdb_api_key":
                current = ((self.config.get("metadata") or {}).get("tmdb") or {})
                values["api_key"] = self._secret(raw, current.get("api_key"))
                return self._finish(
                    key,
                    {"metadata": {"tmdb": deepcopy(values)}},
                )
            if stage == "tvdb_pin":
                current = ((self.config.get("metadata") or {}).get("tvdb") or {})
                values["subscriber_pin"] = self._optional_secret(
                    raw, current.get("subscriber_pin")
                )
                return self._finish(key, {"metadata": {"tvdb": deepcopy(values)}})
        except ValueError:
            return self._message("⚠️ 输入无效，请按提示重新发送。", "open")
        return self._message("⚠️ 配置会话已失效，请重新打开 /config。", "close")

    @staticmethod
    def _api_parse(raw, spec):
        _, kind, _, minimum, maximum = spec
        if kind == "url":
            return _url(raw)
        if kind == "secret":
            return "" if raw.lower() == "clear" else _text(raw)
        if kind == "boolean":
            if raw.lower() not in {"on", "off", "true", "false"}:
                raise ValueError("invalid boolean")
            return raw.lower() in {"on", "true"}
        if kind in {"ids", "indexers", "languages"}:
            if kind == "ids" and raw.lower() == "clear":
                return []
            if kind == "indexers" and raw == "-2":
                return "-2"
            parts = [p for p in re.split(r"[,，\s]+", raw.strip("[] ")) if p]
            if not parts:
                raise ValueError("empty list")
            if kind == "languages":
                if not all(re.fullmatch(r"[a-zA-Z]{2,8}(?:-[a-zA-Z]{2,8})?", p) for p in parts):
                    raise ValueError("invalid language")
                return list(dict.fromkeys(p.lower() for p in parts))
            if not all(re.fullmatch(r"[0-9]+", p) and int(p) > 0 for p in parts):
                raise ValueError("invalid indexer id")
            ids = list(dict.fromkeys(int(p) for p in parts))
            return ids if kind == "ids" else ",".join(map(str, ids))
        value = float(raw)
        if not math.isfinite(value) or not minimum <= value <= maximum or (kind == "integer" and not value.is_integer()):
            raise ValueError("invalid number")
        return int(value) if kind == "integer" else value

    def _api_current(self, session, provider, name):
        path = API_PROVIDERS[provider][1]
        original, staged = self.config, session.get("api_patch") or {}
        for part in path:
            original = original.get(part) or {}
            staged = staged.get(part) or {}
        return staged.get(name, original.get(name, deepcopy(API_PROVIDERS[provider][2][name][2])))

    def _api_menu(self, key, prefix=""):
        rows = [[{"text": label, "callback_data": f"search:config:api:{name}"}]
                for name, (label, _, _) in API_PROVIDERS.items()]
        return self._api_view("\n".join(filter(None, [prefix, "选择 Provider 接口参数。修改暂存后可继续配置其他 Provider，再统一保存。"])), rows)

    def _api_fields(self, key, prefix=""):
        session = self.sessions[key]
        provider = session["provider"]
        label, _, fields = API_PROVIDERS[provider]
        lines = [prefix, f"{label} 接口参数："]
        rows = []
        for name, (field_label, kind, _, _, _) in fields.items():
            value = self._api_current(session, provider, name)
            display = "已配置" if value else "未配置"
            if kind != "secret":
                if kind == "url":
                    try:
                        display = _url(value)
                    except ValueError:
                        display = "未配置或地址无效"
                elif kind == "boolean":
                    display = "启用" if value else "停用"
                else:
                    display = ", ".join(map(str, value)) if isinstance(value, list) else str(value)
            display = display if len(display) <= 180 else display[:177] + "…"
            lines.append(f"{field_label}：{display}")
            rows.append([{"text": field_label, "callback_data": f"search:config:field:{provider}:{name}"}])
        rows.append([{"text": "其他 Provider", "callback_data": "search:config:apis"}])
        return self._api_view("\n".join(filter(None, lines)), rows)

    @staticmethod
    def _api_view(text, rows):
        rows.append([
            {"text": "保存修改", "callback_data": "search:config:api_save"},
            {"text": "退出", "callback_data": "search:config:cancel"},
        ])
        return {"actions": [{"kind": "edit_message", "text": text, "data": {"keyboard": rows}}], "session": {"state": "open"}}

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
                    {"text": "确认保存", "callback_data": "search:config:confirm"},
                    {"text": "退出", "callback_data": "search:config:cancel"},
                ]]},
            }],
            "session": {"state": "open"},
        }

    @staticmethod
    def _message(text: str, state: str, *, edit=False) -> dict:
        action = {
            "kind": "edit_message" if edit else "send_message",
            "text": text,
        }
        if state == "open":
            action["data"] = {"keyboard": [[{
                "text": "退出",
                "callback_data": "search:config:cancel",
            }]]}
        return {
            "actions": [action],
            "session": {"state": state},
        }
