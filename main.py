"""OneBot-only LLM reply filtering, segmentation and browser rendering."""

from __future__ import annotations

import asyncio
from pathlib import Path

import astrbot.api.message_components as Comp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
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
    "OneBot 大模型回复过滤、分段与浏览器图片/PDF渲染", "1.1.3",
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

    async def _text_components(self, text: str, kind: str | None, *, core_only: bool = False) -> list:
        if kind is None:
            values = split_short_text(text, self.settings)
            # Only the optional core-only mode needs separators in one chain.
            # Independent bubbles contain the segment itself, no trailing newline.
            return [
                Comp.Plain(text=value + (
                    "\n" if (core_only or self.settings.send_mode == "core") and index < len(values) - 1 else ""
                ))
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
        core_only = False
        for run in runs:
            if isinstance(run, str):
                prepared.extend(await self._text_components(run, kind, core_only=core_only))
            else:
                # Never pre-send another plugin's media or move text across it.
                # Everything from this boundary onward stays with normal core.
                core_only = True
                prepared.append(run)
        return self._cap_text_segments(prepared, short=kind is None)

    def _cap_text_segments(self, prepared: list, *, short: bool = False) -> list:
        """Soft-limit Plain segments without moving or discarding attachments."""
        capped: list = []
        text_count = 0
        for component in prepared:
            if isinstance(component, Comp.Plain):
                if (
                    text_count >= self.settings.max_messages
                    and capped and isinstance(capped[-1], Comp.Plain)
                ):
                    # Independent short segments need a separator only when
                    # coalesced. Render-fallback chunks must concatenate verbatim.
                    separator = (
                        "\n" if short and self.settings.send_mode == "segments"
                        and not capped[-1].text.endswith("\n") else ""
                    )
                    capped[-1].text += separator + component.text
                    continue
                text_count += 1
            capped.append(component)
        return capped

    @staticmethod
    def _delivery_groups(chain: list) -> list[list]:
        """Split only the leading Plain run; all media belongs to normal core."""
        leading = 0
        for component in chain:
            if not isinstance(component, Comp.Plain):
                break
            leading += 1
        if leading < 2:
            return [list(chain)] if chain else []
        return [[component] for component in chain[:leading - 1]] + [list(chain[leading - 1:])]

    @staticmethod
    def _check_cancelled() -> None:
        # Some services catch CancelledError and return anyway. Do not commit or
        # send more content if this task still has an outstanding cancel request.
        task = asyncio.current_task()
        cancelling = getattr(task, "cancelling", None)
        if cancelling is not None and cancelling():
            raise asyncio.CancelledError

    @staticmethod
    def _discard_cancelled_reply(event: AstrMessageEvent) -> None:
        # In AstrBot 4.29.3 the decorator catches BaseException, while a STOP
        # breaks out of nested async generators without explicitly closing them.
        # Drop this reply without setting STOP so that version can exhaust the
        # upstream agent and release its session lock after catching cancellation.
        # The caller must still propagate cancellation, not re-cancel or uncancel.
        # The existing 4.29.3 agent flag requests cooperative runner shutdown;
        # it is event-local and does not stop the next message's agent.
        event.set_extra("agent_stop_requested", True)
        result = event.get_result()
        if result is not None:
            result.chain.clear()
        event.clear_result()

    async def _send_prefix(self, event: AstrMessageEvent, result, source_chain: list) -> None:
        """Send early bubbles, leaving the last group for normal core + after."""
        groups = self._delivery_groups(source_chain)
        planned = [
            (group, [component.text if isinstance(component, Comp.Plain) else None for component in group])
            for group in groups[:-1]
        ]
        for group, texts in planned:
            self._check_cancelled()
            if event.is_stopped():
                return
            if event.get_result() is not result or result.chain is not source_chain:
                return
            if len(source_chain) < len(group) or any(
                source_chain[index] is not component
                or (isinstance(component, Comp.Plain) and component.text != texts[index])
                for index, component in enumerate(group)
            ):
                return
            # Remove before awaiting: an acknowledgement can fail after OneBot
            # accepts a message. Never leave an attempted prefix for core retry.
            message = MessageChain(chain=list(group))
            del source_chain[:len(group)]
            try:
                await event.send(message)
            except Exception as error:
                logger.warning(
                    f"[reply_filter] 分段发送失败：{type(error).__name__}；"
                    "未自动重试，未尝试的剩余内容交由框架发送",
                )
                return
            self._check_cancelled()
            if event.is_stopped():
                return
            if self.settings.send_interval:
                await asyncio.sleep(self.settings.send_interval)
            self._check_cancelled()
        # No final send, clear_result or stop_event on the normal path: a later
        # meme decorator can append an image. Core owns final delivery and after.

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
        if event.is_stopped():
            return
        try:
            self._check_cancelled()
            await self._format_reply(event, result)
            self._check_cancelled()
        except asyncio.CancelledError:
            self._discard_cancelled_reply(event)
            # Propagate after cleanup; never swallow cancellation in the plugin.
            # AstrBot 4.29.3 catches it itself, then sees no result and exhausts
            # the upstream agent instead of sending the stale tail. Frameworks
            # that propagate cancellation keep their own cancellation semantics.
            raise

    async def _format_reply(self, event: AstrMessageEvent, result) -> None:
        source_chain = result.chain
        snapshot = list(source_chain)
        text_snapshot = [
            component.text if isinstance(component, Comp.Plain) else None
            for component in snapshot
        ]
        try:
            prepared = await self._prepare(snapshot)
            self._check_cancelled()
        except Exception as error:
            # This is a display formatter, not a security boundary. On failure,
            # keep the original result so attachments and later hooks still work.
            logger.warning(
                f"[reply_filter] 处理失败：{type(error).__name__}；保留原结果交由框架发送",
            )
            return
        if event.is_stopped() or event.get_result() is not result:
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
        # Preserve result identity, LLM metadata and the list itself. In default
        # segments mode only early groups are sent now; the final group stays for
        # later meme decorators (99999), normal core delivery and the after hook.
        # Core-only mode retains every component and never sends from this hook.
        source_chain[:] = prepared
        if self.settings.send_mode == "segments":
            await self._send_prefix(event, result, source_chain)
