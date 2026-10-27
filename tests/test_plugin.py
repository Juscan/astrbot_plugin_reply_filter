"""Hook regressions and explicitly simulated core delivery using public API stubs.

No real AstrBot installation, OneBot connection, browser, or cache writes are used.
``simulated_respond`` models only decorator ordering, one normal send and after
callbacks; it is not AstrBot's real pipeline or a OneBot integration test.
The import patch restores sys.modules; only the loaded main module is retained.
"""

import asyncio
from dataclasses import dataclass
import importlib
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, call, patch

from astrbot_plugin_reply_filter.rendering import MarkdownDocument


@dataclass
class Plain:
    text: str


@dataclass
class At:
    qq: str = "12345"


@dataclass
class Reply:
    id: str = "original-message"


@dataclass
class Image:
    data: bytes = b"image"
    file: str = ""

    @classmethod
    def fromBytes(cls, data):
        return cls(data=data)


@dataclass
class Face:
    id: str = "123"


@dataclass
class File:
    file: str
    name: str


@dataclass
class Media:
    """An opaque existing non-text component whose identity must be preserved."""

    label: str


@dataclass
class MessageChain:
    chain: list


class Star:
    def __init__(self, context):
        self.context = context


def _decorator(*args, **kwargs):
    return lambda function: function


def _hook_decorator(name):
    def decorate(*args, **kwargs):
        def record(function):
            function._stub_hook_name = name
            function._stub_decorator_args = args
            function._stub_decorator_kwargs = kwargs.copy()
            return function
        return record
    return decorate


def _load_plugin():
    package = importlib.import_module("astrbot_plugin_reply_filter")
    absent = object()
    previous_main = getattr(package, "main", absent)
    modules = {name: ModuleType(name) for name in (
        "astrbot", "astrbot.api", "astrbot.api.message_components",
        "astrbot.api.event", "astrbot.api.star",
    )}
    modules["astrbot"].__path__ = []
    modules["astrbot.api"].__path__ = []
    modules["astrbot"].api = modules["astrbot.api"]
    api = modules["astrbot.api"]
    api.AstrBotConfig = dict
    api.logger = SimpleNamespace(warning=Mock(), error=Mock(), info=Mock())
    api.message_components = modules["astrbot.api.message_components"]
    api.event = modules["astrbot.api.event"]
    api.star = modules["astrbot.api.star"]
    for component in (Plain, At, Reply, Image, Face, File):
        setattr(api.message_components, component.__name__, component)
    api.event.AstrMessageEvent = object
    api.event.MessageChain = MessageChain
    api.event.filter = SimpleNamespace(
        event_message_type=_hook_decorator("event_message_type"),
        on_decorating_result=_hook_decorator("on_decorating_result"),
        EventMessageType=SimpleNamespace(ALL=object()),
    )
    api.star.Context = object
    api.star.Star = Star
    api.star.StarTools = SimpleNamespace(
        get_data_dir=Mock(return_value=Path(__file__).resolve().parent / "unused-cache"),
    )
    api.star.register = _decorator
    try:
        with patch.dict(sys.modules, modules):
            sys.modules.pop("astrbot_plugin_reply_filter.main", None)
            loaded = importlib.import_module("astrbot_plugin_reply_filter.main")
    finally:
        if previous_main is absent:
            if hasattr(package, "main"):
                delattr(package, "main")
        else:
            package.main = previous_main
    return loaded, modules


PLUGIN, API_STUBS = _load_plugin()
PNG = b"\x89PNG\r\n\x1a\nrendered image"
PDF = b"%PDF-1.7\nrendered document"
LLM_MARKER = object()
COMMAND_MARKER = object()


class FakeResult:
    """Only the result APIs used by the hook; deliberately no result.split()."""

    def __init__(self, chain, llm=True):
        self.chain = chain
        self.llm = llm
        self.result_content_type = LLM_MARKER if llm else COMMAND_MARKER

    def is_llm_result(self):
        return self.llm


class FakeEvent:
    def __init__(self, result=None, platform="aiocqhttp", session="same-session"):
        self.result = result
        self.platform = platform
        self.unified_msg_origin = session
        self.extras = {}
        self.clear_count = 0
        self.send = AsyncMock()
        self.stop_event = Mock()

    def get_platform_name(self):
        return self.platform

    def get_result(self):
        return self.result

    def clear_result(self):
        self.clear_count += 1
        self.result = None

    def set_extra(self, key, value):
        self.extras[key] = value


def assert_no_hook_delivery(event):
    """Fail before simulated core sending, so an old self-sending hook cannot pass."""
    event.send.assert_not_called()
    event.send.assert_not_awaited()
    if event.clear_count:
        raise AssertionError("decorating hook must not clear the result")
    event.stop_event.assert_not_called()


async def simulated_respond(event, *, decorating_hooks=(), after_hooks=()):
    """Minimal, explicit simulation, not a real AstrBot or segmented-send API.

    Descending decorator priority is modeled here. Only this helper performs the
    normal send and invokes the supplied after hooks. The result has no split API.
    Each decorator must leave delivery to the core: no sends, clears or stops are
    accepted before the single normal send, even if a decorator empties the body.
    """
    hooks = sorted(
        decorating_hooks,
        key=lambda hook: getattr(hook, "_stub_decorator_kwargs", {}).get("priority", 0),
        reverse=True,
    )
    for hook in hooks:
        await hook(event)
        assert_no_hook_delivery(event)
    result = event.get_result()
    if result is None or not any(
        not isinstance(component, (At, Reply))
        and (not isinstance(component, Plain) or bool(component.text.strip()))
        for component in result.chain
    ):
        return
    # Only model the successful normal-send path. Empty/header-only replies do
    # not guarantee an after callback in the real core either.
    await event.send(MessageChain(chain=list(result.chain)))
    for hook in after_hooks:
        await hook(event)


class PluginFixtures:
    def make_plugin(self, **config):
        config.setdefault("send_interval", 0)
        context = SimpleNamespace(get_registered_star=Mock(return_value=None))
        plugin = PLUGIN.ReplyFilterPlugin(context, config)
        plugin.renderer = SimpleNamespace(
            document=MarkdownDocument(), render=AsyncMock(return_value=PNG),
        )
        return plugin

    @staticmethod
    def event(text, **kwargs):
        return FakeEvent(FakeResult([Plain(text)]), **kwargs)

    @staticmethod
    def plain_text(chain):
        return "".join(component.text for component in chain if isinstance(component, Plain))

    def assert_in_place(self, event, original_result, original_chain):
        self.assertIs(event.result, original_result)
        self.assertIs(event.result.chain, original_chain)
        self.assertIs(event.result.result_content_type, LLM_MARKER)
        self.assertIs(event.result.llm, True)
        self.assertTrue(event.result.is_llm_result())
        assert_no_hook_delivery(event)


class PluginTests(PluginFixtures, unittest.IsolatedAsyncioTestCase):
    """Direct hook tests: inspect result.chain, never simulate sending here."""

    def test_stub_import_restores_global_module_registry(self):
        for name, module in API_STUBS.items():
            with self.subTest(name=name):
                self.assertIsNot(sys.modules.get(name), module)
        self.assertIsNot(sys.modules.get("astrbot_plugin_reply_filter.main"), PLUGIN)

    def test_decorating_priority_precedes_common_99999_meme_hook(self):
        hook = PLUGIN.ReplyFilterPlugin.filter_reply
        self.assertEqual(hook._stub_hook_name, "on_decorating_result")
        self.assertEqual(hook._stub_decorator_kwargs, {"priority": 100000})
        early = PLUGIN.ReplyFilterPlugin.request_non_streaming
        self.assertEqual(early._stub_decorator_kwargs, {"priority": 10000})

    async def test_non_onebot_reply_is_passed_through(self):
        plugin = self.make_plugin()
        event = self.event("未经处理，文本。", platform="telegram")
        original, chain = event.result, event.result.chain
        await plugin.filter_reply(event)
        self.assert_in_place(event, original, chain)
        self.assertEqual(chain, [Plain("未经处理，文本。")])
        plugin.renderer.render.assert_not_awaited()

    async def test_non_llm_command_result_is_passed_through_including_media(self):
        plugin = self.make_plugin()
        image, face, file = Image(), Face(), File("command.txt", "command.txt")
        event = FakeEvent(FakeResult([Reply(), Plain("命令插件回复。"), image, face, file], llm=False))
        original, chain = event.result, event.result.chain
        before = chain.copy()
        plugin._prepare = AsyncMock()
        await plugin.filter_reply(event)
        self.assertIs(event.result, original)
        self.assertIs(original.chain, chain)
        self.assertIs(original.result_content_type, COMMAND_MARKER)
        self.assertEqual(len(chain), len(before))
        self.assertTrue(all(a is b for a, b in zip(chain, before)))
        plugin._prepare.assert_not_awaited()
        assert_no_hook_delivery(event)

    async def test_disabled_filter_is_passed_through(self):
        plugin = self.make_plugin(enabled=False)
        event = self.event("原样，保留。")
        original, chain = event.result, event.result.chain
        await plugin.filter_reply(event)
        self.assert_in_place(event, original, chain)
        self.assertEqual(chain, [Plain("原样，保留。")])

    async def test_missing_result_and_non_plain_results_are_not_touched(self):
        for result in (None, FakeResult([Image()]), FakeResult([Media("audio"), Face()]),
                       FakeResult([Reply(), At()])):
            with self.subTest(result=result):
                plugin = self.make_plugin()
                plugin._prepare = AsyncMock()
                event = FakeEvent(result)
                chain = None if result is None else result.chain
                before = None if chain is None else chain.copy()
                await plugin.filter_reply(event)
                self.assertIs(event.result, result)
                if result is not None:
                    self.assertIs(result.chain, chain)
                    self.assertEqual(len(chain), len(before))
                    self.assertTrue(all(a is b for a, b in zip(chain, before)))
                plugin._prepare.assert_not_awaited()
                assert_no_hook_delivery(event)

    async def test_early_hook_forces_non_streaming_without_sending_or_stopping(self):
        plugin = self.make_plugin()
        event = self.event("not ready to send")
        original, chain = event.result, event.result.chain
        await plugin.request_non_streaming(event)
        self.assertIs(event.extras["enable_streaming"], False)
        self.assert_in_place(event, original, chain)

    async def test_early_hook_respects_platform_and_both_enable_flags(self):
        cases = (({}, "telegram"), ({"enabled": False}, "aiocqhttp"),
                 ({"force_non_streaming": False}, "aiocqhttp"))
        for config, platform in cases:
            with self.subTest(config=config, platform=platform):
                plugin = self.make_plugin(**config)
                event = self.event("text", platform=platform)
                await plugin.request_non_streaming(event)
                self.assertEqual(event.extras, {})
                assert_no_hook_delivery(event)

    async def test_150_is_plain_and_151_uses_png_renderer(self):
        for length in (150, 151):
            with self.subTest(length=length):
                plugin = self.make_plugin()
                event = self.event("字" * length)
                original, chain = event.result, event.result.chain
                await plugin.filter_reply(event)
                self.assert_in_place(event, original, chain)
                if length == 150:
                    plugin.renderer.render.assert_not_awaited()
                    self.assertEqual(chain, [Plain("字" * 150)])
                else:
                    plugin.renderer.render.assert_awaited_once_with("字" * 151, "image")
                    self.assertIsInstance(chain[0], Image)
                    self.assertEqual(chain[0].data, PNG)

    async def test_short_markdown_routes_to_renderer_without_symbol_stripping(self):
        plugin = self.make_plugin()
        event = self.event("**重要**")
        original, chain = event.result, event.result.chain
        await plugin.filter_reply(event)
        plugin.renderer.render.assert_awaited_once_with("**重要**", "image")
        self.assertIsInstance(chain[0], Image)
        self.assert_in_place(event, original, chain)

    async def test_pdf_mode_routes_to_pdf_and_preserves_generated_file(self):
        plugin = self.make_plugin(render_mode="pdf")
        plugin.renderer.render.return_value = PDF
        event = self.event("# 文档")
        original, chain = event.result, event.result.chain
        path = Path(__file__).resolve().parent / "reply-test.pdf"
        with patch.object(PLUGIN, "save_pdf", return_value=path) as save:
            await plugin.filter_reply(event)
        plugin.renderer.render.assert_awaited_once_with("# 文档", "pdf")
        save.assert_called_once_with(PDF, plugin.cache_dir, plugin.settings.cache_retention_hours)
        self.assertIsInstance(chain[0], File)
        self.assertEqual(chain[0].name, "模型回复.pdf")
        self.assertTrue(chain[0].file.endswith("reply-test.pdf"))
        self.assert_in_place(event, original, chain)

    async def test_completely_removed_thinking_leaves_empty_original_result(self):
        plugin = self.make_plugin()
        event = self.event("<think>do not leak</think>")
        original, chain = event.result, event.result.chain
        await plugin.filter_reply(event)
        plugin.renderer.render.assert_not_awaited()
        self.assertEqual(chain, [])
        self.assert_in_place(event, original, chain)

    async def test_adjacent_plain_components_merge_split_tags_and_blocked_words(self):
        plugin = self.make_plugin(blocked_words=["secret"])
        event = FakeEvent(FakeResult([
            Plain("A<th"), Plain("ink>hidden</think>Bse"), Plain("cretC"),
        ]))
        original, chain = event.result, event.result.chain
        await plugin.filter_reply(event)
        self.assertEqual(chain, [Plain("ABC")])
        plugin.renderer.render.assert_not_awaited()
        self.assert_in_place(event, original, chain)

    async def test_thinking_state_survives_an_intervening_non_text_component(self):
        plugin = self.make_plugin()
        media = Image()
        event = FakeEvent(FakeResult([
            Plain("A<think>hidden"), media, Plain("still hidden</think>B"),
        ]))
        original, chain = event.result, event.result.chain
        await plugin.filter_reply(event)
        self.assertEqual(chain, [Plain("A"), media, Plain("B")])
        self.assertIs(chain[1], media)
        self.assert_in_place(event, original, chain)

    async def test_image_face_file_and_opaque_media_keep_original_identity_and_order(self):
        plugin = self.make_plugin(max_messages=1)
        image, face = Image(), Face()
        file, opaque = File("existing.bin", "existing.bin"), Media("audio")
        components = [Plain("A"), image, Plain("B"), face, file, opaque]
        event = FakeEvent(FakeResult(components))
        original, chain = event.result, event.result.chain
        await plugin.filter_reply(event)
        self.assertEqual(chain, [Plain("A"), image, Plain("B"), face, file, opaque])
        retained = [value for value in chain if not isinstance(value, Plain)]
        self.assertTrue(all(a is b for a, b in zip(retained, (image, face, file, opaque))))
        self.assert_in_place(event, original, chain)

    async def test_short_segments_have_readable_newlines_but_not_after_last_segment(self):
        plugin = self.make_plugin()
        event = self.event("one,two,three")
        original, chain = event.result, event.result.chain
        await plugin.filter_reply(event)
        self.assertEqual(chain, [Plain("one\n"), Plain("two\n"), Plain("three")])
        self.assertEqual(self.plain_text(chain), "one\ntwo\nthree")
        self.assert_in_place(event, original, chain)

    async def test_newline_is_per_text_run_and_never_added_across_media(self):
        plugin = self.make_plugin()
        media = Image()
        event = FakeEvent(FakeResult([Plain("A,B"), media, Plain("C,D")]))
        original, chain = event.result, event.result.chain
        await plugin.filter_reply(event)
        self.assertEqual(chain, [Plain("A\n"), Plain("B"), media, Plain("C\n"), Plain("D")])
        self.assertIs(chain[2], media)
        self.assert_in_place(event, original, chain)

    async def test_max_messages_coalesces_only_excess_plain_without_duplicate_newlines(self):
        plugin = self.make_plugin(max_messages=2)
        event = self.event("A,B,C,D,E")
        original, chain = event.result, event.result.chain
        await plugin.filter_reply(event)
        self.assertEqual(chain, [Plain("A\n"), Plain("B\nC\nD\nE")])
        self.assertEqual(self.plain_text(chain), "A\nB\nC\nD\nE")
        self.assert_in_place(event, original, chain)

    async def test_soft_text_cap_does_not_merge_across_or_delete_media(self):
        plugin = self.make_plugin(max_messages=2)
        image, face, file = Image(), Face(), File("keep.txt", "keep.txt")
        event = FakeEvent(FakeResult([
            Plain("A,B"), image, Plain("C,D"), face, Plain("E,F"), file,
        ]))
        original, chain = event.result, event.result.chain
        await plugin.filter_reply(event)
        self.assertEqual(chain, [Plain("A\n"), Plain("B"), image,
                                 Plain("C\nD"), face, Plain("E\nF"), file])
        self.assertGreater(sum(isinstance(value, Plain) for value in chain), 2)
        self.assertTrue(all(a is b for a, b in zip(
            [value for value in chain if not isinstance(value, Plain)], (image, face, file))))
        self.assert_in_place(event, original, chain)

    async def test_reply_and_all_mentions_retain_their_original_positions(self):
        plugin = self.make_plugin()
        reply, mention, trailing = Reply(), At("1"), At("2")
        event = FakeEvent(FakeResult([reply, mention, Plain("one,two"), trailing]))
        original, chain = event.result, event.result.chain
        await plugin.filter_reply(event)
        self.assertEqual(chain, [reply, mention, Plain("one\n"), Plain("two"), trailing])
        self.assertIs(chain[0], reply)
        self.assertIs(chain[1], mention)
        self.assertIs(chain[-1], trailing)
        self.assert_in_place(event, original, chain)

    async def test_header_only_chain_after_filtering_is_preserved(self):
        plugin = self.make_plugin()
        reply, mention = Reply(), At()
        event = FakeEvent(FakeResult([reply, mention, Plain("<analysis>secret</analysis>")]))
        original, chain = event.result, event.result.chain
        await plugin.filter_reply(event)
        self.assertEqual(chain, [reply, mention])
        self.assertIs(chain[0], reply)
        self.assertIs(chain[1], mention)
        self.assert_in_place(event, original, chain)

    async def test_image_and_pdf_failure_fall_back_to_lossless_original_markdown(self):
        plugin = self.make_plugin()
        source = "```python\nprint('保留！')\n```"
        plugin.renderer.render.side_effect = [TimeoutError("image"), RuntimeError("pdf")]
        event = self.event(source)
        original, chain = event.result, event.result.chain
        await plugin.filter_reply(event)
        self.assertEqual(plugin.renderer.render.await_args_list, [call(source, "image"), call(source, "pdf")])
        self.assertEqual(chain, [Plain(source)])
        self.assert_in_place(event, original, chain)

    async def test_invalid_png_falls_back_to_plain_text(self):
        plugin = self.make_plugin(pdf_on_image_error=False)
        plugin.renderer.render.return_value = b"<html>not a PNG</html>"
        event = self.event("**must remain readable**")
        original, chain = event.result, event.result.chain
        await plugin.filter_reply(event)
        plugin.renderer.render.assert_awaited_once_with("**must remain readable**", "image")
        self.assertEqual(chain, [Plain("**must remain readable**")])
        self.assert_in_place(event, original, chain)

    async def test_max_render_chars_skips_rendering_without_truncation(self):
        plugin = self.make_plugin(max_render_chars=1000)
        source = "字" * 1001
        event = self.event(source)
        original, chain = event.result, event.result.chain
        await plugin.filter_reply(event)
        plugin.renderer.render.assert_not_awaited()
        self.assertEqual(chain, [Plain("字" * 1000), Plain("字")])
        self.assertEqual(self.plain_text(chain), source)
        self.assert_in_place(event, original, chain)

    async def test_capped_render_fallback_preserves_original_chunk_boundary_without_added_newline(self):
        plugin = self.make_plugin(max_render_chars=1000, max_messages=1)
        source = "字" * 999 + "甲乙\n**保留**"
        event = self.event(source)
        original, chain = event.result, event.result.chain
        await plugin.filter_reply(event)
        plugin.renderer.render.assert_not_awaited()
        self.assertEqual(chain, [Plain(source)])
        self.assert_in_place(event, original, chain)

    async def test_prepare_failure_preserves_original_result_and_chain_and_warns_for_upstream_retry(self):
        plugin = self.make_plugin()
        event = self.event("secret unfiltered original")
        original, chain = event.result, event.result.chain
        before = chain.copy()

        async def fail_after_mutating_snapshot(snapshot):
            self.assertIsNot(snapshot, chain)
            snapshot.clear()
            raise RuntimeError("processing failed")

        plugin._prepare = AsyncMock(side_effect=fail_after_mutating_snapshot)
        with patch.object(PLUGIN.logger, "warning") as warning:
            await plugin.filter_reply(event)
        self.assertEqual(chain, before)
        self.assertIs(chain[0], before[0])
        self.assert_in_place(event, original, chain)
        warning.assert_called()
        self.assertIn("RuntimeError", str(warning.call_args))
        self.assertNotIn("secret unfiltered original", str(warning.call_args))
        plugin._prepare = AsyncMock(return_value=[Plain("retried")])
        await plugin.filter_reply(event)
        self.assertEqual(chain, [Plain("retried")])
        self.assert_in_place(event, original, chain)

    async def test_cap_failure_does_not_partially_commit_prepared_chain(self):
        plugin = self.make_plugin()
        event = self.event("one,two")
        original, chain = event.result, event.result.chain
        before = chain.copy()
        plugin._cap_text_segments = Mock(side_effect=RuntimeError("cap failed"))
        with patch.object(PLUGIN.logger, "warning") as warning:
            await plugin.filter_reply(event)
        self.assertEqual(chain, before)
        self.assertIs(chain[0], before[0])
        warning.assert_called()
        self.assert_in_place(event, original, chain)

    async def test_reused_event_formats_a_later_final_llm_result_without_delivery_flags(self):
        plugin = self.make_plugin()
        event = self.event("first,result")
        first, first_chain = event.result, event.result.chain
        await plugin.filter_reply(event)
        self.assertEqual(first_chain, [Plain("first\n"), Plain("result")])
        self.assert_in_place(event, first, first_chain)
        final = FakeResult([Plain("final,result")])
        event.result = final
        final_chain = final.chain
        await plugin.filter_reply(event)
        self.assertEqual(final_chain, [Plain("final\n"), Plain("result")])
        self.assert_in_place(event, final, final_chain)
        self.assertEqual(event.extras, {})

    async def test_new_result_installed_while_prepare_waits_is_not_overwritten(self):
        plugin = self.make_plugin()
        event = self.event("old,result")
        original, chain = event.result, event.result.chain
        before = chain.copy()
        replacement = FakeResult([Plain("independent replacement"), Image()])
        replacement_chain = replacement.chain
        entered, release = asyncio.Event(), asyncio.Event()

        async def delayed_prepare(snapshot):
            entered.set()
            await release.wait()
            return [Plain("formatted old result")]

        plugin._prepare = AsyncMock(side_effect=delayed_prepare)
        task = asyncio.create_task(plugin.filter_reply(event))
        try:
            await asyncio.wait_for(entered.wait(), timeout=2)
            event.result = replacement
        finally:
            release.set()
            await asyncio.wait_for(task, timeout=2)
        self.assertIs(event.result, replacement)
        self.assertIs(replacement.chain, replacement_chain)
        self.assertEqual(replacement_chain[0], Plain("independent replacement"))
        self.assertIs(original.chain, chain)
        self.assertEqual(chain, before)
        self.assertIs(chain[0], before[0])
        assert_no_hook_delivery(event)

    async def test_source_list_or_prefix_edits_during_render_are_not_overwritten(self):
        for edit_kind in ("replace_list", "edit_plain", "replace_component", "remove_component"):
            with self.subTest(edit_kind=edit_kind):
                plugin = self.make_plugin()
                plain, media = Plain("**old**"), Image()
                event = FakeEvent(FakeResult([plain, media]))
                original, source_chain = event.result, event.result.chain
                entered, release = asyncio.Event(), asyncio.Event()

                async def delayed_render(text, kind):
                    entered.set()
                    await release.wait()
                    return PNG

                plugin.renderer.render.side_effect = delayed_render
                task = asyncio.create_task(plugin.filter_reply(event))
                try:
                    await asyncio.wait_for(entered.wait(), timeout=2)
                    if edit_kind == "replace_list":
                        original.chain = [Plain("independent list"), Face()]
                    elif edit_kind == "edit_plain":
                        plain.text = "independently edited text"
                    elif edit_kind == "replace_component":
                        source_chain[0] = Plain("independent component")
                    else:
                        source_chain.pop()
                    expected_chain = original.chain
                    expected_items = expected_chain.copy()
                    expected_text = self.plain_text(expected_chain)
                finally:
                    release.set()
                    await asyncio.wait_for(task, timeout=2)
                self.assertIs(event.result, original)
                self.assertIs(original.chain, expected_chain)
                self.assertEqual(len(expected_chain), len(expected_items))
                self.assertTrue(all(a is b for a, b in zip(expected_chain, expected_items)))
                self.assertEqual(self.plain_text(expected_chain), expected_text)
                self.assertIs(original.result_content_type, LLM_MARKER)
                assert_no_hook_delivery(event)

    async def test_attachments_appended_during_render_survive_in_place_commit(self):
        plugin = self.make_plugin()
        existing = Image(data=b"existing")
        appended = [Image(data=b"appended"), Face(), File("pending.bin", "pending.bin")]
        event = FakeEvent(FakeResult([Plain("**text**"), existing]))
        original, chain = event.result, event.result.chain
        entered, release = asyncio.Event(), asyncio.Event()

        async def delayed_render(text, kind):
            entered.set()
            await release.wait()
            return PNG

        plugin.renderer.render.side_effect = delayed_render
        task = asyncio.create_task(plugin.filter_reply(event))
        try:
            await asyncio.wait_for(entered.wait(), timeout=2)
            chain.extend(appended)
        finally:
            release.set()
            await asyncio.wait_for(task, timeout=2)
        self.assertEqual(len(chain), 5)
        self.assertIsInstance(chain[0], Image)
        self.assertEqual(chain[0].data, PNG)
        self.assertIs(chain[1], existing)
        self.assertTrue(all(a is b for a, b in zip(chain[2:], appended)))
        self.assert_in_place(event, original, chain)

    async def test_cancellation_during_prepare_propagates_and_preserves_original_result(self):
        plugin = self.make_plugin()
        event = self.event("one,two")
        original, chain = event.result, event.result.chain
        before = chain.copy()
        entered, release = asyncio.Event(), asyncio.Event()

        async def wait_forever(snapshot):
            entered.set()
            await release.wait()
            return [Plain("should not commit")]

        plugin._prepare = AsyncMock(side_effect=wait_forever)
        task = asyncio.create_task(plugin.filter_reply(event))
        try:
            await asyncio.wait_for(entered.wait(), timeout=2)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        finally:
            release.set()
            if not task.done():
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
        self.assertEqual(chain, before)
        self.assertIs(chain[0], before[0])
        self.assert_in_place(event, original, chain)

    async def test_render_cancellation_is_not_swallowed_or_converted_to_text_fallback(self):
        plugin = self.make_plugin()
        event = self.event("**must remain original on cancellation**")
        original, chain = event.result, event.result.chain
        before = chain.copy()
        plugin.renderer.render.side_effect = asyncio.CancelledError()
        with patch.object(PLUGIN.logger, "warning") as warning:
            with self.assertRaises(asyncio.CancelledError):
                await plugin.filter_reply(event)
        plugin.renderer.render.assert_awaited_once_with(before[0].text, "image")
        warning.assert_not_called()
        self.assertEqual(chain, before)
        self.assertIs(chain[0], before[0])
        self.assert_in_place(event, original, chain)

    async def test_same_and_different_session_events_can_prepare_concurrently(self):
        for second_session in ("same-session", "different-session"):
            with self.subTest(second_session=second_session):
                plugin = self.make_plugin()
                first = self.event("A1,A2")
                second = self.event("B1,B2", session=second_session)
                first_result, first_chain = first.result, first.result.chain
                second_result, second_chain = second.result, second.result.chain
                entered, release = asyncio.Event(), asyncio.Event()
                real_prepare = plugin._prepare

                async def delayed_first(snapshot):
                    if snapshot[0] is first_chain[0]:
                        entered.set()
                        await release.wait()
                    return await real_prepare(snapshot)

                plugin._prepare = AsyncMock(side_effect=delayed_first)
                first_task = asyncio.create_task(plugin.filter_reply(first))
                try:
                    await asyncio.wait_for(entered.wait(), timeout=2)
                    await asyncio.wait_for(plugin.filter_reply(second), timeout=2)
                    self.assertEqual(second_chain, [Plain("B1\n"), Plain("B2")])
                    self.assertEqual(first_chain, [Plain("A1,A2")])
                finally:
                    release.set()
                    await asyncio.wait_for(first_task, timeout=2)
                self.assertEqual(first_chain, [Plain("A1\n"), Plain("A2")])
                self.assert_in_place(first, first_result, first_chain)
                self.assert_in_place(second, second_result, second_chain)

    async def test_waiting_render_does_not_leak_thinking_state_into_another_event(self):
        plugin = self.make_plugin()
        media = Image()
        first = FakeEvent(FakeResult([Plain("**A**<think>hidden"), media, Plain("still hidden")]))
        second = self.event("B1,B2")
        first_result, first_chain = first.result, first.result.chain
        second_result, second_chain = second.result, second.result.chain
        entered, release = asyncio.Event(), asyncio.Event()

        async def delayed_render(text, kind):
            entered.set()
            await release.wait()
            return PNG

        plugin.renderer.render.side_effect = delayed_render
        task = asyncio.create_task(plugin.filter_reply(first))
        try:
            await asyncio.wait_for(entered.wait(), timeout=2)
            await asyncio.wait_for(plugin.filter_reply(second), timeout=2)
            self.assertEqual(second_chain, [Plain("B1\n"), Plain("B2")])
        finally:
            release.set()
            await asyncio.wait_for(task, timeout=2)
        plugin.renderer.render.assert_awaited_once_with("**A**", "image")
        self.assertEqual(len(first_chain), 2)
        self.assertIs(first_chain[1], media)
        self.assert_in_place(first, first_result, first_chain)
        self.assert_in_place(second, second_result, second_chain)

    async def test_legacy_send_interval_is_read_but_does_not_delay_or_send(self):
        plugin = self.make_plugin(send_interval=0.25)
        event = self.event("one,two,three")
        original, chain = event.result, event.result.chain
        with patch.object(asyncio, "sleep", new_callable=AsyncMock) as sleep:
            await plugin.filter_reply(event)
        self.assertEqual(plugin.settings.send_interval, 0.25)
        sleep.assert_not_awaited()
        self.assertEqual(chain, [Plain("one\n"), Plain("two\n"), Plain("three")])
        self.assertFalse(hasattr(plugin, "_session_lock"))
        self.assertFalse(hasattr(plugin, "_delivery_groups"))
        self.assert_in_place(event, original, chain)


class SimulatedCoreTests(PluginFixtures, unittest.IsolatedAsyncioTestCase):
    """Compatibility models only; these are not real AstrBot/OneBot delivery."""

    async def test_simulated_normal_send_keeps_short_segments_readable_without_segmentation(self):
        plugin = self.make_plugin()
        event = self.event("one,two,three")
        original, chain = event.result, event.result.chain
        await simulated_respond(event, decorating_hooks=[plugin.filter_reply])
        event.send.assert_awaited_once()
        delivered = event.send.await_args.args[0].chain
        self.assertEqual(self.plain_text(delivered), "one\ntwo\nthree")
        self.assertIs(event.result, original)
        self.assertIs(original.chain, chain)
        self.assertIs(original.result_content_type, LLM_MARKER)
        self.assertEqual(event.clear_count, 0)
        event.stop_event.assert_not_called()

    async def test_later_decorator_can_append_an_original_image_to_formatted_result(self):
        plugin = self.make_plugin()
        event = self.event("one,two")
        original, chain = event.result, event.result.chain
        meme = Image(data=b"later-meme")

        async def append_meme(current):
            self.assert_in_place(current, original, chain)
            self.assertEqual(chain, [Plain("one\n"), Plain("two")])
            chain.append(meme)

        await simulated_respond(event, decorating_hooks=[plugin.filter_reply, append_meme])
        event.send.assert_awaited_once()
        self.assertIs(event.send.await_args.args[0].chain[-1], meme)
        self.assertIs(event.result, original)
        self.assertIs(original.chain, chain)

    async def test_priority_99999_meme_can_make_fully_filtered_body_image_only(self):
        plugin = self.make_plugin()
        event = self.event("<think>hidden</think>")
        original, chain = event.result, event.result.chain
        meme = Image(data=b"image-only-meme")
        seen = []
        decorator = API_STUBS["astrbot.api.event"].filter.on_decorating_result

        @decorator(priority=99999)
        async def meme_99999(current):
            self.assert_in_place(current, original, chain)
            self.assertEqual(chain, [])
            seen.append("meme")
            chain.append(meme)

        # Intentionally reversed: recorded priorities, not argument order, decide.
        await simulated_respond(event, decorating_hooks=[meme_99999, plugin.filter_reply])
        self.assertEqual(seen, ["meme"])
        self.assertEqual(chain, [meme])
        event.send.assert_awaited_once()
        self.assertIs(event.send.await_args.args[0].chain[0], meme)
        self.assertIs(event.result, original)
        self.assertIs(original.result_content_type, LLM_MARKER)

    async def test_simulated_core_invokes_after_once_and_each_pending_image_is_sent_once(self):
        plugin = self.make_plugin()
        event = self.event("one,two")
        original, chain = event.result, event.result.chain
        pending = [Image(data=b"pending-one"), Image(data=b"pending-two")]
        expected = pending.copy()
        order = []
        after = AsyncMock()
        event.after_message_sent = after

        async def decorate_pending(current):
            self.assert_in_place(current, original, chain)
            after.assert_not_awaited()
            current.extras["pending_images"] = pending
            order.append("decorate")

        async def send(message):
            order.append("send")

        async def flush_pending(current):
            order.append("after")
            self.assertEqual(current.send.await_count, 1)
            while current.extras["pending_images"]:
                image = current.extras["pending_images"].pop(0)
                await current.send(MessageChain(chain=[image]))

        after.side_effect = flush_pending
        event.send.side_effect = send
        await simulated_respond(
            event, decorating_hooks=[plugin.filter_reply, decorate_pending], after_hooks=[after],
        )
        after.assert_awaited_once_with(event)
        self.assertEqual(order, ["decorate", "send", "after", "send", "send"])
        delivered = [args.args[0].chain for args in event.send.await_args_list]
        self.assertEqual(delivered[0], [Plain("one\n"), Plain("two")])
        self.assertIs(delivered[1][0], expected[0])
        self.assertIs(delivered[2][0], expected[1])
        for image in expected:
            self.assertEqual(sum(component is image for group in delivered for component in group), 1)
        self.assertEqual(pending, [])
        self.assertIs(event.result, original)
        self.assertIs(original.chain, chain)
        self.assertIs(original.result_content_type, LLM_MARKER)
        self.assertEqual(event.clear_count, 0)
        event.stop_event.assert_not_called()

    async def test_simulated_empty_or_header_only_body_does_not_send_or_guarantee_after(self):
        for result in (None, FakeResult([]), FakeResult([Plain(" \n\t")]),
                       FakeResult([At(), Reply()]), FakeResult([Plain("<think>hidden</think>")])):
            with self.subTest(result=result):
                plugin = self.make_plugin()
                event = FakeEvent(result)
                original_chain = None if result is None else result.chain
                after = AsyncMock()
                await simulated_respond(
                    event, decorating_hooks=[plugin.filter_reply], after_hooks=[after],
                )
                after.assert_not_awaited()
                assert_no_hook_delivery(event)
                self.assertIs(event.result, result)
                if result is not None:
                    self.assertIs(result.chain, original_chain)
                    self.assertFalse(any(isinstance(value, Plain) and value.text.strip()
                                         for value in result.chain))
                    self.assertIs(result.result_content_type, LLM_MARKER)

    async def test_simulated_core_send_failure_is_not_a_plugin_retry_or_result_clear(self):
        plugin = self.make_plugin()
        event = self.event("one filtered reply")
        original, chain = event.result, event.result.chain
        event.send.side_effect = RuntimeError("acknowledgement lost")
        with self.assertRaisesRegex(RuntimeError, "acknowledgement lost"):
            await simulated_respond(event, decorating_hooks=[plugin.filter_reply])
        event.send.assert_awaited_once()
        self.assertIs(event.result, original)
        self.assertIs(original.chain, chain)
        self.assertEqual(event.clear_count, 0)
        event.stop_event.assert_not_called()

    async def test_simulation_rejects_self_sending_hook_instead_of_masking_its_bug(self):
        event = self.event("text")

        async def wrong_hook(current):
            await current.send(MessageChain(chain=[Plain("wrong direct send")]))
            current.clear_result()

        with self.assertRaises(AssertionError):
            await simulated_respond(event, decorating_hooks=[wrong_hook])
        event.send.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
