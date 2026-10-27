"""OneBot-only LLM reply filtering, segmentation and browser rendering."""

from __future__ import annotations

import asyncio
from pathlib import Path

import astrbot.api.message_components as Comp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools, register

from .processing import (
    Settings,
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
    "OneBot 大模型回复过滤、分段与浏览器图片/PDF渲染", "1.1.1",
)
class ReplyFilterPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig) -> None:
        super().__init__(context)
        self.settings = Settings.from_mapping(config)
        self.renderer = BrowserRenderer(context, self.settings)
        self.cache_dir = Path(StarTools.get_data_dir("astrbot_plugin_reply_filter")) / "pdf"
        self._cache_lock = asyncio.Lock()

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
            values = split_short_text(text, self.settings)
            # Keep paragraph breaks when core segmented replies are disabled.
            # With core segmentation these are just trailing line breaks.
            return [
                Comp.Plain(text=value + ("\n" if index < len(values) - 1 else ""))
                for index, value in enumerate(values)
            ]
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

    def _cap_text_segments(self, prepared: list) -> list:
        """Soft-limit Plain segments without moving or discarding attachments."""
        capped: list = []
        text_count = 0
        for component in prepared:
            if isinstance(component, Comp.Plain):
                if (
                    text_count >= self.settings.max_messages
                    and capped and isinstance(capped[-1], Comp.Plain)
                ):
                    # Short segments already include newlines; fallback chunks
                    # must concatenate verbatim to preserve Markdown and code.
                    capped[-1].text += component.text
                    continue
                text_count += 1
            capped.append(component)
        return capped

    @filter.event_message_type(filter.EventMessageType.ALL, priority=10000)
    async def request_non_streaming(self, event: AstrMessageEvent) -> None:
        if (
            self.settings.enabled and self.settings.force_non_streaming
            and event.get_platform_name() == "aiocqhttp"
        ):
            # Read by AstrBot's agent before on_llm_request. Not effective for
            # Live Mode, and another plugin can intentionally override it later.
            event.set_extra("enable_streaming", False)

    @filter.on_decorating_result(priority=100000)
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
        source_chain = result.chain
        snapshot = list(source_chain)
        text_snapshot = [
            component.text if isinstance(component, Comp.Plain) else None
            for component in snapshot
        ]
        try:
            prepared = self._cap_text_segments(await self._prepare(snapshot))
        except Exception as error:
            # This is a display formatter, not a security boundary. On failure,
            # keep the original result so attachments and later hooks still work.
            logger.warning(
                f"[reply_filter] 处理失败：{type(error).__name__}；保留原结果交由框架发送",
            )
            return
        if event.get_result() is not result:
            return
        if result.chain is not source_chain or len(source_chain) < len(snapshot):
            return
        if any(
            source_chain[index] is not component
            or (isinstance(component, Comp.Plain) and component.text != text_snapshot[index])
            for index, component in enumerate(snapshot)
        ):
            # A concurrent callback changed the source during browser rendering.
            # Do not overwrite its edits; append-only attachments are kept below.
            return
        prepared.extend(source_chain[len(snapshot):])
        # Preserve result identity, LLM metadata and the list itself. Never send,
        # clear_result or stop here: core RespondStage must run normally so that
        # later image decorators and after_message_sent meme senders can execute.
        # Run before the known meme-manager decorator (99999), including when
        # filtering removes all text and it needs to switch to an image-only reply.
        source_chain[:] = prepared
