"""Async hook regressions with temporarily installed public AstrBot API stubs.

No real AstrBot installation, OneBot connection, browser, or cache writes are used.
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
    for component in (Plain, At, Reply, Image, File):
        setattr(api.message_components, component.__name__, component)
    api.event.AstrMessageEvent = object
    api.event.MessageChain = MessageChain
    api.event.filter = SimpleNamespace(
        event_message_type=_decorator,
        on_decorating_result=_decorator,
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


class FakeResult:
    def __init__(self, chain, llm=True):
        self.chain = chain
        self.llm = llm

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


class PluginTests(unittest.IsolatedAsyncioTestCase):
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
    def sent_chains(event):
        return [awaited.args[0].chain for awaited in event.send.await_args_list]

    @classmethod
    def sent_plain_text(cls, event):
        return "".join(component.text for chain in cls.sent_chains(event)
                       for component in chain if isinstance(component, Plain))

    def test_stub_import_restores_global_module_registry(self):
        for name, module in API_STUBS.items():
            with self.subTest(name=name):
                self.assertIsNot(sys.modules.get(name), module)
        self.assertIsNot(sys.modules.get("astrbot_plugin_reply_filter.main"), PLUGIN)

    async def test_non_onebot_reply_is_passed_through(self):
        plugin = self.make_plugin()
        event = self.event("未经处理，文本。", platform="telegram")
        original = event.result
        await plugin.filter_reply(event)
        self.assertIs(event.result, original)
        event.send.assert_not_awaited()
        self.assertEqual(event.clear_count, 0)
        plugin.renderer.render.assert_not_awaited()

    async def test_non_llm_reply_is_passed_through(self):
        plugin = self.make_plugin()
        event = FakeEvent(FakeResult([Plain("命令插件回复。")], llm=False))
        original = event.result
        await plugin.filter_reply(event)
        self.assertIs(event.result, original)
        event.send.assert_not_awaited()
        self.assertEqual(event.clear_count, 0)

    async def test_disabled_filter_is_passed_through(self):
        plugin = self.make_plugin(enabled=False)
        event = self.event("原样，保留。")
        original = event.result
        await plugin.filter_reply(event)
        self.assertIs(event.result, original)
        event.send.assert_not_awaited()
        self.assertEqual(event.clear_count, 0)

    async def test_missing_result_and_media_only_result_are_not_taken_over(self):
        plugin = self.make_plugin()
        for result in (None, FakeResult([Media("audio")])):
            with self.subTest(result=result):
                event = FakeEvent(result)
                await plugin.filter_reply(event)
                self.assertIs(event.result, result)
                event.send.assert_not_awaited()
                self.assertEqual(event.clear_count, 0)

    async def test_early_hook_forces_non_streaming_without_sending_or_stopping(self):
        plugin = self.make_plugin()
        event = self.event("not ready to send")
        original = event.result
        await plugin.request_non_streaming(event)
        self.assertIs(event.extras["enable_streaming"], False)
        self.assertIs(event.result, original)
        event.send.assert_not_awaited()
        event.stop_event.assert_not_called()
        self.assertEqual(event.clear_count, 0)

    async def test_early_hook_respects_platform_and_both_enable_flags(self):
        cases = (({}, "telegram"), ({"enabled": False}, "aiocqhttp"),
                 ({"force_non_streaming": False}, "aiocqhttp"))
        for config, platform in cases:
            with self.subTest(config=config, platform=platform):
                plugin = self.make_plugin(**config)
                event = self.event("text", platform=platform)
                await plugin.request_non_streaming(event)
                self.assertEqual(event.extras, {})
                event.send.assert_not_awaited()
                event.stop_event.assert_not_called()

    async def test_150_is_plain_and_151_uses_png_renderer(self):
        for length in (150, 151):
            with self.subTest(length=length):
                plugin = self.make_plugin()
                event = self.event("字" * length)
                await plugin.filter_reply(event)
                self.assertIsNone(event.result)
                self.assertEqual(event.clear_count, 1)
                if length == 150:
                    plugin.renderer.render.assert_not_awaited()
                    self.assertEqual(self.sent_plain_text(event), "字" * 150)
                else:
                    plugin.renderer.render.assert_awaited_once_with("字" * 151, "image")
                    self.assertIsInstance(self.sent_chains(event)[0][0], Image)
                    self.assertEqual(self.sent_chains(event)[0][0].data, PNG)

    async def test_short_markdown_routes_to_renderer_without_symbol_stripping(self):
        plugin = self.make_plugin()
        event = self.event("**重要**")
        await plugin.filter_reply(event)
        plugin.renderer.render.assert_awaited_once_with("**重要**", "image")
        self.assertIsInstance(self.sent_chains(event)[0][0], Image)
        self.assertIsNone(event.result)

    async def test_pdf_mode_routes_to_pdf_and_preserves_generated_file(self):
        plugin = self.make_plugin(render_mode="pdf")
        plugin.renderer.render.return_value = PDF
        event = self.event("# 文档")
        path = Path(__file__).resolve().parent / "reply-test.pdf"
        with patch.object(PLUGIN, "save_pdf", return_value=path) as save:
            await plugin.filter_reply(event)
        plugin.renderer.render.assert_awaited_once_with("# 文档", "pdf")
        save.assert_called_once_with(PDF, plugin.cache_dir, plugin.settings.cache_retention_hours)
        delivered = self.sent_chains(event)[0][0]
        self.assertIsInstance(delivered, File)
        self.assertEqual(delivered.name, "模型回复.pdf")
        self.assertTrue(delivered.file.endswith("reply-test.pdf"))

    async def test_completely_removed_thinking_sends_nothing_and_clears_result(self):
        plugin = self.make_plugin()
        event = self.event("<think>do not leak</think>")
        await plugin.filter_reply(event)
        event.send.assert_not_awaited()
        plugin.renderer.render.assert_not_awaited()
        self.assertIsNone(event.result)
        self.assertEqual(event.clear_count, 1)

    async def test_adjacent_plain_components_merge_split_tags_and_blocked_words(self):
        plugin = self.make_plugin(blocked_words=["secret"])
        event = FakeEvent(FakeResult([
            Plain("A<th"), Plain("ink>hidden</think>Bse"), Plain("cretC"),
        ]))
        await plugin.filter_reply(event)
        self.assertEqual(self.sent_plain_text(event), "ABC")
        event.send.assert_awaited_once()
        plugin.renderer.render.assert_not_awaited()

    async def test_thinking_state_survives_an_intervening_non_text_component(self):
        plugin = self.make_plugin()
        media = Media("existing-image")
        event = FakeEvent(FakeResult([
            Plain("A<think>hidden"), media, Plain("still hidden</think>B"),
        ]))
        await plugin.filter_reply(event)
        self.assertEqual(self.sent_plain_text(event), "AB")
        chains = self.sent_chains(event)
        self.assertEqual(len(chains), 3)
        self.assertIs(chains[1][0], media)
        self.assertEqual(chains[0], [Plain("A")])
        self.assertEqual(chains[2], [Plain("B")])

    async def test_non_text_components_keep_identity_and_order(self):
        plugin = self.make_plugin()
        first, second = Media("image"), Media("audio")
        event = FakeEvent(FakeResult([Plain("A"), first, Plain("B"), second]))
        await plugin.filter_reply(event)
        chains = self.sent_chains(event)
        self.assertEqual(len(chains), 4)
        self.assertEqual(chains[0], [Plain("A")])
        self.assertIs(chains[1][0], first)
        self.assertEqual(chains[2], [Plain("B")])
        self.assertIs(chains[3][0], second)

    async def test_max_messages_coalesces_excess_without_losing_any_text(self):
        plugin = self.make_plugin(max_messages=2)
        event = self.event("A,B,C,D,E")
        await plugin.filter_reply(event)
        self.assertEqual(event.send.await_count, 2)
        self.assertEqual(self.sent_chains(event)[0], [Plain("A")])
        self.assertEqual(self.sent_chains(event)[1], [
            Plain("B"), Plain("\n"), Plain("C"), Plain("\n"),
            Plain("D"), Plain("\n"), Plain("E"),
        ])
        self.assertEqual(self.sent_plain_text(event), "AB\nC\nD\nE")

    async def test_reply_and_mentions_attach_to_content_instead_of_sending_alone(self):
        plugin = self.make_plugin()
        reply, mention, trailing = Reply(), At("1"), At("2")
        event = FakeEvent(FakeResult([reply, mention, Plain("one,two"), trailing]))
        await plugin.filter_reply(event)
        self.assertEqual(self.sent_chains(event), [
            [reply, mention, Plain("one")], [Plain("two"), trailing],
        ])

    async def test_headers_left_after_filtering_are_not_sent_without_a_body(self):
        plugin = self.make_plugin()
        event = FakeEvent(FakeResult([Reply(), At(), Plain("<analysis>secret</analysis>")]))
        await plugin.filter_reply(event)
        event.send.assert_not_awaited()
        self.assertIsNone(event.result)

    async def test_image_and_pdf_failure_fall_back_to_lossless_original_markdown(self):
        plugin = self.make_plugin()
        source = "```python\nprint('保留！')\n```"
        plugin.renderer.render.side_effect = [TimeoutError("image"), RuntimeError("pdf")]
        event = self.event(source)
        await plugin.filter_reply(event)
        self.assertEqual(plugin.renderer.render.await_args_list, [
            call(source, "image"), call(source, "pdf"),
        ])
        self.assertEqual(self.sent_plain_text(event), source)
        self.assertIsNone(event.result)

    async def test_invalid_png_is_not_sent_as_an_image(self):
        plugin = self.make_plugin(pdf_on_image_error=False)
        plugin.renderer.render.return_value = b"<html>not a PNG</html>"
        event = self.event("**must remain readable**")
        await plugin.filter_reply(event)
        plugin.renderer.render.assert_awaited_once_with("**must remain readable**", "image")
        self.assertEqual(self.sent_chains(event), [[Plain("**must remain readable**")]])
        self.assertIsNone(event.result)

    async def test_max_render_chars_skips_rendering_without_truncation(self):
        plugin = self.make_plugin(max_render_chars=1000)
        source = "字" * 1001
        event = self.event(source)
        await plugin.filter_reply(event)
        plugin.renderer.render.assert_not_awaited()
        self.assertEqual(self.sent_plain_text(event), source)
        self.assertEqual(event.send.await_count, 2)
        self.assertIsNone(event.result)

    async def test_send_failure_is_not_retried_and_original_result_is_cleared(self):
        plugin = self.make_plugin()
        event = self.event("one filtered reply")
        event.send.side_effect = RuntimeError("acknowledgement lost")
        await plugin.filter_reply(event)
        event.send.assert_awaited_once()
        self.assertIsNone(event.result)
        self.assertEqual(event.clear_count, 1)
        event.stop_event.assert_not_called()

    async def test_prepare_failure_still_clears_unfiltered_original(self):
        plugin = self.make_plugin()
        plugin._prepare = AsyncMock(side_effect=RuntimeError("processing failed"))
        event = self.event("secret unfiltered original")
        await plugin.filter_reply(event)
        event.send.assert_not_awaited()
        self.assertIsNone(event.result)
        self.assertEqual(event.clear_count, 1)

    async def test_reused_event_does_not_swallow_a_later_final_llm_result(self):
        plugin = self.make_plugin()
        event = self.event("first result")
        await plugin.filter_reply(event)
        event.result = FakeResult([Plain("final result")])
        await plugin.filter_reply(event)
        self.assertEqual(self.sent_chains(event), [[Plain("first result")], [Plain("final result")]])
        self.assertEqual(event.clear_count, 2)
        self.assertIsNone(event.result)

    async def test_new_result_installed_during_send_is_not_cleared(self):
        plugin = self.make_plugin()
        event = self.event("old result")
        replacement = FakeResult([Plain("independent replacement")])

        async def replace_result(chain):
            event.result = replacement

        event.send.side_effect = replace_result
        await plugin.filter_reply(event)
        self.assertIs(event.result, replacement)
        self.assertEqual(event.clear_count, 0)
        event.send.assert_awaited_once()

    async def test_same_session_concurrent_replies_do_not_interleave(self):
        plugin = self.make_plugin()
        first = self.event("A1,A2")
        second = self.event("B1,B2")
        entered, release = asyncio.Event(), asyncio.Event()
        order = []

        async def send_first(chain):
            text = "".join(component.text for component in chain.chain)
            order.append(text)
            if text == "A1":
                entered.set()
                await release.wait()

        async def send_second(chain):
            order.append("".join(component.text for component in chain.chain))

        first.send.side_effect = send_first
        second.send.side_effect = send_second
        first_task = asyncio.create_task(plugin.filter_reply(first))
        second_task = None
        try:
            await asyncio.wait_for(entered.wait(), timeout=2)
            second_task = asyncio.create_task(plugin.filter_reply(second))
            await asyncio.sleep(0)
            self.assertEqual(order, ["A1"])
        finally:
            release.set()
            await asyncio.wait_for(asyncio.gather(first_task, *([second_task] if second_task else [])), timeout=2)
        self.assertEqual(order, ["A1", "A2", "B1", "B2"])
        self.assertIsNone(first.result)
        self.assertIsNone(second.result)

    async def test_different_sessions_can_deliver_while_another_session_is_waiting(self):
        plugin = self.make_plugin()
        first = self.event("A1,A2", session="session-A")
        second = self.event("B1,B2", session="session-B")
        entered, release = asyncio.Event(), asyncio.Event()
        order = []

        async def send_first(chain):
            text = "".join(component.text for component in chain.chain)
            order.append(text)
            if text == "A1":
                entered.set()
                await release.wait()

        async def send_second(chain):
            order.append("".join(component.text for component in chain.chain))

        first.send.side_effect = send_first
        second.send.side_effect = send_second
        first_task = asyncio.create_task(plugin.filter_reply(first))
        try:
            await asyncio.wait_for(entered.wait(), timeout=2)
            await asyncio.wait_for(plugin.filter_reply(second), timeout=2)
            self.assertEqual(order, ["A1", "B1", "B2"])
        finally:
            release.set()
            await asyncio.wait_for(first_task, timeout=2)
        self.assertEqual(order, ["A1", "B1", "B2", "A2"])

    async def test_delivery_interval_is_applied_between_messages_only(self):
        plugin = self.make_plugin(send_interval=0.25)
        event = self.event("one,two,three")
        with patch.object(PLUGIN.asyncio, "sleep", new_callable=AsyncMock) as sleep:
            await plugin.filter_reply(event)
        self.assertEqual(sleep.await_args_list, [call(0.25), call(0.25)])
        self.assertEqual(event.send.await_count, 3)


if __name__ == "__main__":
    unittest.main()
