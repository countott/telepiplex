# -*- coding: utf-8 -*-

from __future__ import annotations

import json

import requests


PLEX_AI_SYSTEM_PROMPT = """你是 Telepiplex 的 Plex 管理助手。
只使用提供的工具读取或准备 Plex 操作，不得编造服务器状态、媒体信息或操作结果。
任何写操作都只能生成待确认预览；必须由用户通过确认按钮后才能执行。
回答保持简洁，并明确说明操作是否仍需确认。"""


class PlexAIOrchestrator:
    def __init__(self, ai_config, tool_dispatcher, max_tool_rounds=3):
        self.config = dict(ai_config or {})
        self.dispatcher = tool_dispatcher
        self.max_tool_rounds = min(max(int(max_tool_rounds), 1), 3)
        self.tool_schemas = tool_dispatcher.tool_schemas()

    def _endpoint(self):
        url = str(self.config.get("api_url") or self.config.get("base_url") or "").rstrip("/")
        if not url:
            raise ValueError("Plex AI api_url is missing")
        if "chat/completions" not in url:
            url += "/chat/completions"
        return url

    def _complete(self, messages):
        response = requests.post(
            self._endpoint(),
            headers={
                "Authorization": f"Bearer {self.config.get('api_key') or ''}",
                "Content-Type": "application/json",
            },
            json={
                "model": str(self.config.get("model") or ""),
                "messages": messages,
                "tools": self.tool_schemas,
                "tool_choice": "auto",
            },
            timeout=int(self.config.get("timeout") or 60),
        )
        response.raise_for_status()
        return response.json()

    @staticmethod
    def _message(response):
        try:
            return dict(response["choices"][0]["message"])
        except (KeyError, IndexError, TypeError):
            return {}

    def run(self, user_text):
        messages = [
            {"role": "system", "content": PLEX_AI_SYSTEM_PROMPT},
            {"role": "user", "content": str(user_text or "").strip()},
        ]
        tool_results = []
        confirmation = None
        for _round_index in range(self.max_tool_rounds):
            message = self._message(self._complete(messages))
            calls = message.get("tool_calls") or []
            if not calls:
                content = str(message.get("content") or "").strip()
                if not content:
                    content = "当前 AI 接口未返回可用的文本或工具调用。"
                result = {"message": content, "tool_results": tool_results}
                if confirmation:
                    result["confirmation"] = confirmation
                return result
            messages.append({
                "role": "assistant",
                "content": message.get("content"),
                "tool_calls": calls,
            })
            for call in calls:
                function = call.get("function") or {}
                name = str(function.get("name") or "")
                arguments = json.loads(function.get("arguments") or "{}")
                # AI may prepare writes, but only a human callback may consume a token.
                arguments.pop("confirmation_token", None)
                dispatched = self.dispatcher.dispatch(name, arguments)
                tool_results.append({"name": name, "result": dispatched})
                if isinstance(dispatched, dict) and dispatched.get("status") == "confirmation_required":
                    confirmation = dispatched
                messages.append({
                    "role": "tool",
                    "tool_call_id": str(call.get("id") or ""),
                    "content": json.dumps(dispatched, ensure_ascii=False, default=str),
                })
        result = {
            "error": "tool_round_limit",
            "message": "Plex 工具调用超过三轮，已停止。",
            "tool_results": tool_results,
        }
        if confirmation:
            result["confirmation"] = confirmation
        return result
