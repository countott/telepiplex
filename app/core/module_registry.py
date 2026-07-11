# -*- coding: utf-8 -*-

from __future__ import annotations

from dataclasses import dataclass, field
from inspect import signature
from typing import Any, Callable

try:
    from telegram import BotCommand
except ModuleNotFoundError:
    @dataclass
    class BotCommand:
        command: str
        description: str


class DownloadProviderUnavailable(RuntimeError):
    pass


@dataclass
class DownloadRequest:
    link: str
    selected_path: str
    user_id: int
    naming_metadata: dict | None = None
    metadata: dict | None = None
    source: str = ""


@dataclass
class DownloadCompletedEvent:
    link: str
    selected_path: str
    user_id: int
    final_path: str
    resource_name: str
    naming_metadata: dict | None = None
    metadata: dict | None = None
    provider: str = "115"
    storage: Any = None


@dataclass
class PostDownloadResult:
    handled: bool
    final_path: str | None = None
    message: str | None = None
    should_stop: bool = False
    metadata: dict | None = None


@dataclass(frozen=True)
class DownloadPipelineCompletion:
    event: DownloadCompletedEvent
    result: PostDownloadResult
    terminal_processor: str | None = None


@dataclass(order=True)
class PostDownloadProcessor:
    priority: int
    name: str
    processor: Callable[[DownloadCompletedEvent], PostDownloadResult | None] = field(compare=False)


class ModuleRegistry:
    def __init__(self):
        self._commands: list[BotCommand] = []
        self.handler_registrars: list[Callable] = []
        self.startup_hooks: list[Callable] = []
        self.config_sections: list[str] = []
        self.download_provider = None
        self.storage_provider = None
        self.post_download_processors: list[PostDownloadProcessor] = []
        self.download_completion_hooks: list[tuple[str, Callable]] = []

    def add_commands(self, commands):
        for command in commands or []:
            self._commands.append(self._normalize_command(command))

    def _normalize_command(self, command) -> BotCommand:
        if isinstance(command, BotCommand):
            return command
        if isinstance(command, dict):
            return BotCommand(str(command["command"]), str(command["description"]))
        name, description = command
        return BotCommand(str(name), str(description))

    def bot_commands(self) -> list[BotCommand]:
        return list(self._commands)

    def add_handlers(self, register_handlers: Callable):
        self.handler_registrars.append(register_handlers)

    def register_handlers(self, application):
        for register_handlers in self.handler_registrars:
            register_handlers(application)

    def add_startup_hook(self, hook: Callable):
        self.startup_hooks.append(hook)

    def run_startup_hooks(self, application=None):
        for hook in self.startup_hooks:
            if application is None:
                hook()
                continue

            try:
                hook_signature = signature(hook)
            except (TypeError, ValueError):
                hook(application)
                continue

            try:
                hook_signature.bind(application)
            except TypeError:
                hook()
            else:
                hook(application)

    def add_config_sections(self, section_names):
        for section_name in section_names or []:
            section_name = str(section_name)
            if section_name not in self.config_sections:
                self.config_sections.append(section_name)

    def set_download_provider(self, provider):
        self.download_provider = provider

    def set_storage_provider(self, provider):
        self.storage_provider = provider

    def dispatch_download(self, request: DownloadRequest):
        if self.download_provider is None:
            raise DownloadProviderUnavailable("未注册下载 provider，无法处理下载请求。")
        if hasattr(self.download_provider, "submit"):
            return self.download_provider.submit(request)
        return self.download_provider(request)

    def add_post_download_processor(self, processor: Callable, priority: int, name: str):
        self.post_download_processors.append(
            PostDownloadProcessor(priority=int(priority), name=str(name), processor=processor)
        )
        self.post_download_processors.sort()

    def add_download_completion_hook(self, hook: Callable, name: str):
        self.download_completion_hooks.append((str(name), hook))

    def run_post_download_pipeline(self, event: DownloadCompletedEvent) -> PostDownloadResult:
        final_result = PostDownloadResult(False, final_path=event.final_path)
        terminal_processor = None
        for item in self.post_download_processors:
            try:
                result = item.processor(event)
            except Exception as exc:
                try:
                    import init

                    if init.logger:
                        init.logger.warn(f"post_download_processor_failed name={item.name}: {exc}")
                except Exception:
                    pass
                continue
            if result is None:
                continue
            if result.final_path:
                event.final_path = result.final_path
            if result.metadata:
                event.metadata = result.metadata
            if result.handled:
                final_result = result
            if result.should_stop:
                terminal_processor = item.name
                break
        completion = DownloadPipelineCompletion(
            event=event,
            result=final_result,
            terminal_processor=terminal_processor,
        )
        for name, hook in self.download_completion_hooks:
            try:
                hook(completion)
            except Exception as exc:
                try:
                    import init

                    if init.logger:
                        init.logger.warn(f"download_completion_hook_failed name={name}: {exc}")
                except Exception:
                    pass
        return final_result
