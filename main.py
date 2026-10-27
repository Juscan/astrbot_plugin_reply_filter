"""OneBot-only LLM reply filtering, segmentation and browser rendering."""

from __future__ import annotations

import asyncio
from pathlib import Path
from weakref import WeakValueDictionary

import astrbot.api.message_components as Comp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star, StarTools, register

from .processing import (
    Settings,
    cap_messages,
    clean_text,
    fallback_chunks,
    markdown_probe,
    render_kind,
    split_short_text,
)
from .rendering import BrowserRenderer
from .storage import save_pdf


@register(
    "astrbot_plugin_reply_filter", "Jusc",
    "OneBot 大模型回复过滤、分段与浏览器图片/PDF渲染", "1.1.0",
)
class ReplyFilterPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig) -> None:
        super().__init__(context)
        self.settings = Settings.from_mapping(config)
        self.renderer = BrowserRenderer(context, self.settings)
        self.cache_dir = Path(StarTools.get_data_dir("astrbot_plugin_reply_filter")) / "pdf"
        self._cache_lock = asyncio.Lock()
        self._session_locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()

    def _session_lock(self, session: str) -> asyncio.Lock:
        lock = self._session_locks.get(session)
        if lock is None:
            lock = asyncio.Lock()
            self._session_locks[session] = lock
        return lock

    async def _render_component(self, text: str, kind: str):
        # A whole-operation deadline includes queue wait in the shared service.
        data = await asyncio.wait_for(
            self.renderer.render(text, kind), timeout=self.settings.render_timeout,
        )
        if kind == "image":
            if not data.startswith(b"\x89PNG\r\n\x1a\n"):
                raise ValueError("浏览器没有返回有效的 PNG 文件头")
            return Comp.Image.fromBytes(data)
        async with self._cache_lock:
            path = await asyncio.to_thread(
                save_pdf, data, self.cache_dir, self.settings.cache_retention_hours,
            )
        return Comp.File(file=str(path), name="模型回复.pdf")

    async def _text_components(self, text: str, kind: str | None) -> list:
        if kind is None:
            return [Comp.Plain(text=value) for value in split_short_text(text, self.settings)]
        if len(text) <= self.settings.max_render_chars:
            try:
                return [await self._render_component(text, kind)]
            except Exception as error:
                # Do not log model content or an exception's potentially embedded HTML.
                logger.warning(f"[reply_filter] {kind} 渲染失败：{type(error).__name__}")
            if kind == "image" and self.settings.pdf_on_image_error:
                try:
                    return [await self._render_component(text, "pdf")]
                except Exception as error:
                    logger.warning(f"[reply_filter] PDF 回退失败：{type(error).__name__}")
        else:
            logger.warning("[reply_filter] 内容超出 max_render_chars，直接回退文本，不截断")
        return [Comp.Plain(text=value) for value in fallback_chunks(text)]

    async def _prepare(self, chain: list) -> list:
        # Coalesce adjacent Plain components before filtering and Markdown parsing;
        # a think block, Markdown fence or blocked word can cross component borders.
        runs: list = []
        pending: list[str] = []
        thinking_stack: list[str] = []

        def flush() -> None:
            if pending:
                value = clean_text("".join(pending), self.settings, thinking_stack)
                if value:
                    runs.append(value)
                pending.clear()

        for component in chain:
            if isinstance(component, Comp.Plain):
                pending.append(component.text)
            else:
                flush()
                runs.append(component)
        flush()
        texts = [run for run in runs if isinstance(run, str)]
        combined = "\n".join(texts)
        kind = render_kind(
            combined,
            any(self.renderer.document.contains_markdown(markdown_probe(text, self.settings)) for text in texts),
            self.settings,
        )
        prepared: list = []
        for run in runs:
            if isinstance(run, str):
                prepared.extend(await self._text_components(run, kind))
            else:
                # Non-text media, mentions and reply components keep their order.
                prepared.append(run)
        return prepared

    def _delivery_groups(self, prepared: list) -> list[list]:
        messages: list[list] = []
        headers: list = []
        for component in prepared:
            if isinstance(component, (Comp.At, Comp.Reply)):
                headers.append(component)
            else:
                messages.append([*headers, component])
                headers.clear()
        if headers and messages:
            messages[-1].extend(headers)
        # A mention/reply-only chain is not a deliverable OneBot message.
        return [
            [component for message in group for component in message]
            for group in cap_messages(messages, self.settings.max_messages)
        ]

    @filter.event_message_type(filter.EventMessageType.ALL, priority=10000)
    async def request_non_streaming(self, event: AstrMessageEvent) -> None:
        if (
            self.settings.enabled and self.settings.force_non_streaming
            and event.get_platform_name() == "aiocqhttp"
        ):
            # Read by AstrBot's agent before on_llm_request. Not effective for
            # Live Mode, and another plugin can intentionally override it later.
            event.set_extra("enable_streaming", False)

    @filter.on_decorating_result()
    async def filter_reply(self, event: AstrMessageEvent) -> None:
        if not self.settings.enabled or event.get_platform_name() != "aiocqhttp":
            return
        result = event.get_result()
        # STREAMING_RESULT has already bypassed this hook; STREAMING_FINISH is
        # intentionally ignored. Command/plugin replies are never intercepted.
        if result is None or not result.is_llm_result():
            return
        if not any(isinstance(component, Comp.Plain) for component in result.chain):
            return
        lock = self._session_lock(event.unified_msg_origin)
        async with lock:
            try:
                prepared = await self._prepare(list(result.chain))
                groups = self._delivery_groups(prepared)
                for index, components in enumerate(groups):
                    if index and self.settings.send_interval:
                        await asyncio.sleep(self.settings.send_interval)
                    # Separate adjacent Plain components with a newline when the
                    # message cap coalesces excess segments into the last message.
                    chain: list = []
                    for component in components:
                        if chain and isinstance(chain[-1], Comp.Plain) and isinstance(component, Comp.Plain):
                            chain.append(Comp.Plain(text="\n"))
                        chain.append(component)
                    try:
                        await event.send(MessageChain(chain=chain))
                    except Exception as error:
                        # Don't automatically resend: OneBot may have accepted an
                        # upload even if its acknowledgement was lost.
                        logger.error(
                            f"[reply_filter] 第 {index + 1}/{len(groups)} 条发送失败："
                            f"{type(error).__name__}；未自动重试以避免重复消息",
                        )
            except Exception as error:
                logger.error(f"[reply_filter] 处理失败：{type(error).__name__}；已阻止未过滤的原文发送")
            finally:
                # The result has been taken over even when cleaning removes all
                # content. Never leak the unfiltered original on an exception.
                # Don't erase a new result installed by an independent callback.
                if event.get_result() is result:
                    event.clear_result()
