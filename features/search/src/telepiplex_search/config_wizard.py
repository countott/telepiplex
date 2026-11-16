from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import yaml
import math
import re
import time


SESSION_TTL_SECONDS = 30 * 60


HOST_FIELDS = {
    "prowlarr": ("search_prowlarr", ("base_url","api_key")),
    "tvdb": ("search_tvdb", ("base_url","api_key","subscriber_pin","timeout")),
    "tmdb": ("search_tmdb_endpoint", ("base_url","timeout")),
    "anilist": ("search_anilist", ("endpoint","timeout")),
}


# Only fields consumed by the existing adapters are editable here.
PROVIDER_LABELS = {
    "prowlarr": ("Prowlarr", ("search", "prowlarr"), {
        "timeout": "总超时（秒）",
        "indexer_timeout": "单站超时（秒）",
        "status_timeout": "状态超时（秒）",
        "indexer_ids": "启用站点 IDs",
        "first_wave_indexer_ids": "优先站点 IDs",
        "wave_delay": "其他站点延迟（秒）",
        "result_limit": "结果数量",
    }),
    "tmdb": ("TMDB", ("metadata", "tmdb"), {
        "enable": "启用",
    }),
    "tvdb": ("TVDB", ("metadata", "tvdb"), {
        "enable": "启用",
    }),
    "anilist": ("AniList", ("metadata", "anilist"), {
        "enable": "启用",
    }),
    "wikipedia": ("Wikipedia", ("metadata", "wikipedia"), {
        "enable": "启用",
        "languages": "语言顺序",
        "timeout": "超时（秒）",
        "min_interval": "请求间隔（秒）",
        "max_queries": "每轮查询数",
        "rate_limit_cooldown": "限流冷却（秒）",
    }),
    "douban": ("豆瓣", ("metadata", "douban"), {
        "enable": "启用",
        "timeout": "超时（秒）",
        "cache_ttl": "缓存时长（秒）",
        "max_concurrency": "请求并发数",
        "circuit_breaker_failures": "连续失败阈值",
        "circuit_breaker_seconds": "失败冷却（秒）",
    }),
}

def provider_contract(root):
    """Read only the private Search release's shipped config contract."""
    defaults = yaml.safe_load((root/'config.default.yaml').read_text())
    schema = json.loads((root/'config.schema.json').read_text())
    parsers = {'indexer_ids':'indexers','first_wave_indexer_ids':'ids','languages':'languages'}
    providers = {}
    for provider,(label,path,labels) in PROVIDER_LABELS.items():
        base, section = defaults,schema
        for part in path:
            base,section = base[part],section['properties'][part]
        fields = {}
        for name,title in labels.items():
            spec = section['properties'][name]
            fields[name] = (title,parsers.get(name,spec.get('type')),base[name],spec.get('minimum'),spec.get('maximum'))
        providers[provider] = label,path,fields
    return providers


# Installed processes run in their verified release directory. In a source
# checkout the package files live next to src/, independent of the test cwd.
_source = Path(__file__).resolve().parents[2]
API_PROVIDERS = provider_contract(_source if (_source/'manifest.yaml').is_file() else Path.cwd())


def _owner(request: dict) -> tuple[int, int]:
    return int(request.get("chat_id") or 0), int(request.get("user_id") or 0)


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
        if action in {"shared_tmdb", "field:tmdb:api_key"} or self._stale_tmdb(session):
            return self._shared_tmdb(key)
        target = self._host_target(session,action)
        if target:
            return self._navigate(key,target)
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
        if session.get("stage") == "choose" and action == "tmdb":
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
            return self._finish(key, {"metadata": {section: {"enable": True}}})
        return self._message("⚠️ 配置操作与当前步骤不匹配。", "open")

    def message(self, request: dict) -> dict:
        key = _owner(request)
        session = self._get_session(key)
        if not session:
            return self._message("⚠️ 配置会话已失效，请重新打开 /config。", "close")
        if self._stale_tmdb(session):
            return self._shared_tmdb(key)
        target = self._host_target(session,"")
        if target:
            return self._navigate(key,target)
        raw = str(request.get("text") or "").strip()
        stage = session.get("stage")
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
        except ValueError:
            return self._message("⚠️ 输入无效，请按提示重新发送。", "open")
        return self._message("⚠️ 配置会话已失效，请重新打开 /config。", "close")

    @staticmethod
    def _api_parse(raw, spec):
        _, kind, _, minimum, maximum = spec
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

    @staticmethod
    def _stale_tmdb(session):
        return (session.get("stage") == "tmdb_api_key"
                or (session.get("stage") == "api_value" and session.get("provider") == "tmdb" and session.get("field") == "api_key")
                or any("api_key" in ((session.get(key) or {}).get("metadata") or {}).get("tmdb", {}) for key in ("patch", "api_patch")))

    @staticmethod
    def _host_target(session,action):
        for provider,(target,names) in HOST_FIELDS.items():
            if action == "host:"+provider or (provider in ("tvdb","prowlarr") and action == provider):
                return target
            if action in {"field:"+provider+":"+name for name in names}:
                return target
            stage=session.get("stage","")
            if (stage.startswith(provider+"_") or (stage=="boolean" and session.get("section")=="tvdb" and provider=="tvdb")
                 or (stage=="api_value" and session.get("provider")==provider and session.get("field") in names)):
                return target
            for group in ("patch","api_patch"):
                draft=session.get(group,{})
                for part in API_PROVIDERS[provider][1]:
                    draft=draft.get(part,{})
                if any(name in draft for name in names):
                    return target
        return None

    def _navigate(self,key,target):
        self.sessions.pop(key,None)
        result=self._message("请使用 /config 编辑此项基础配置。","close")
        result["config_navigation"]=target
        return result

    def _shared_tmdb(self, key):
        self.sessions.pop(key, None)
        result = self._message("请使用 /config 编辑共享 TMDB 凭据。", "close")
        result["config_navigation"] = "shared_tmdb"
        return result

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
        if provider in HOST_FIELDS:
            rows.append([{"text":"连接配置","callback_data":"search:config:host:"+provider}])
        if provider == "tmdb":
            lines.append("凭据由共享 TMDB 配置提供。")
            rows.append([{"text": "共享 TMDB 凭据", "callback_data": "search:config:shared_tmdb"}])
        for name, (field_label, kind, _, _, _) in fields.items():
            value = self._api_current(session, provider, name)
            if kind == "boolean":
                display = "启用" if value else "停用"
            else:
                display = ", ".join(map(str,value)) if isinstance(value,list) else str(value)
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
