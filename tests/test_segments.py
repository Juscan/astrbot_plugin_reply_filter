"""Default segmented-bubble regressions using explicit public-API stubs.

No real OneBot/core/browser is run. The pipeline helper deliberately has core
segmentation OFF: prefix sends come from the hook, the final send from the
model core, and after callbacks only from that model. Direct-hook cancellation
regressions require the public hook to propagate cancellation, empty and clear the
result, and request cooperative agent shutdown without STOP or uncancel. Separate
real-task tests in test_cancellation use a conceptual onion scheduler/agent-lock
model, not real framework integration or proof of OneBot/exactly-once delivery.
External STOP behavior and indefinitely noncooperative dependencies are not fixed.
"""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, call, patch

from astrbot_plugin_reply_filter.processing import Settings
from astrbot_plugin_reply_filter.rendering import MarkdownDocument
from astrbot_plugin_reply_filter.tests import test_plugin as stubs


class SendModeSettingsTests(unittest.TestCase):
    def test_default_and_schema_restore_independent_segments(self):
        schema = json.loads((Path(__file__).resolve().parents[1] / "_conf_schema.json").read_text(encoding="utf-8"))
        for settings in (Settings(), Settings.from_mapping({})):
            with self.subTest(settings=settings):
                self.assertEqual(settings.send_mode, "segments")
                self.assertEqual(settings.send_interval, 0.6)
        self.assertEqual(schema["send_mode"]["default"], "segments")
        self.assertEqual(schema["send_mode"]["options"], ["segments", "core"])
        self.assertEqual(schema["send_interval"]["default"], 0.6)

    def test_both_explicit_modes_and_invalid_modes(self):
        for value in ("segments", "core"):
            self.assertEqual(Settings.from_mapping({"send_mode": value}).send_mode, value)
        for value in ("", "SEGMENTS", "Core", "split", None, True, 1, [], {}):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "send_mode"):
                    Settings.from_mapping({"send_mode": value})


class SegmentDeliveryTests(stubs.PluginFixtures, unittest.IsolatedAsyncioTestCase):
    """Use the real segments default, never the legacy core-only make_plugin."""

    def setUp(self):
        # Do not wait real seconds, but preserve and inspect every requested delay.
        self.sleep = AsyncMock()
        patcher = patch.object(stubs.PLUGIN.asyncio, "sleep", self.sleep)
        patcher.start()
        self.addCleanup(patcher.stop)

    def make_plugin(self, **config):
        context = SimpleNamespace(get_registered_star=Mock(return_value=None))
        plugin = stubs.PLUGIN.ReplyFilterPlugin(context, config)
        plugin.renderer = SimpleNamespace(
            document=MarkdownDocument(), render=AsyncMock(return_value=stubs.PNG),
        )
        return plugin

    def assert_preserved(self, event, result, chain):
        self.assertIs(event.result, result)
        self.assertIs(result.chain, chain)
        self.assertIs(result.result_content_type, stubs.LLM_MARKER)
        self.assertTrue(result.is_llm_result())
        self.assertEqual(event.clear_count, 0)
        event.stop_event.assert_not_called()

    @staticmethod
    def deliveries(event):
        return [entry.args[0].chain for entry in event.send.await_args_list]

    async def test_default_three_bubbles_and_interval_with_core_segmentation_off(self):
        plugin = self.make_plugin()
        self.assertEqual(plugin.settings.send_mode, "segments")
        self.assertEqual(plugin.settings.send_interval, 0.6)
        event = self.event("第一段，第二段！第三段。")
        result, chain = event.result, event.result.chain
        timeline = []

        async def send(message):
            timeline.append(("send", self.plain_text(message.chain)))

        async def sleep(seconds):
            timeline.append(("sleep", seconds))

        event.send.side_effect = send
        self.sleep.side_effect = sleep
        after = AsyncMock()
        await stubs.simulated_respond(
            event, decorating_hooks=[plugin.filter_reply], after_hooks=[after], allow_prefix=True,
        )
        self.assertEqual(timeline, [
            ("send", "第一段"), ("sleep", 0.6),
            ("send", "第二段"), ("sleep", 0.6), ("send", "第三段"),
        ])
        self.assertEqual(self.deliveries(event), [
            [stubs.Plain("第一段")], [stubs.Plain("第二段")], [stubs.Plain("第三段")],
        ])
        self.assertEqual(chain, [stubs.Plain("第三段")])
        after.assert_awaited_once_with(event)
        self.assert_preserved(event, result, chain)

    async def test_zero_interval_still_sends_three_separate_calls_without_sleep(self):
        plugin = self.make_plugin(send_interval=0)
        event = self.event("A,B,C")
        await stubs.simulated_respond(event, decorating_hooks=[plugin.filter_reply], allow_prefix=True)
        self.assertEqual(self.deliveries(event), [[stubs.Plain(value)] for value in ("A", "B", "C")])
        self.sleep.assert_not_awaited()

    async def test_single_real_last_segment_stays_for_core_without_prefix_or_sleep(self):
        for source, remaining in (("一句。", "一句"), ("，，甲！！", "甲")):
            with self.subTest(source=source):
                plugin = self.make_plugin()
                event = self.event(source)
                result, chain = event.result, event.result.chain
                await plugin.filter_reply(event)
                event.send.assert_not_awaited()
                self.assertEqual(chain, [stubs.Plain(remaining)])
                self.assert_preserved(event, result, chain)
        self.sleep.assert_not_awaited()

    async def test_later_99999_mixed_meme_is_only_in_final_core_message(self):
        plugin = self.make_plugin()
        event = self.event("A,B,C")
        result, chain = event.result, event.result.chain
        meme = stubs.Image(data=b"later-meme")
        decorator = stubs.API_STUBS["astrbot.api.event"].filter.on_decorating_result

        @decorator(priority=99999)
        async def append_meme(current):
            self.assertEqual(current.send.await_count, 2)
            self.assertEqual(chain, [stubs.Plain("C")])
            self.assert_preserved(current, result, chain)
            current.extras["meme_processed"] = True
            chain.append(meme)

        await stubs.simulated_respond(
            event, decorating_hooks=[append_meme, plugin.filter_reply], allow_prefix=True,
        )
        groups = self.deliveries(event)
        self.assertEqual(groups, [[stubs.Plain("A")], [stubs.Plain("B")], [stubs.Plain("C"), meme]])
        self.assertIs(groups[-1][-1], meme)
        self.assertTrue(event.extras["meme_processed"])
        self.assert_preserved(event, result, chain)

    async def test_after_pending_images_run_once_after_final_text_without_prefix_replay(self):
        plugin = self.make_plugin()
        event = self.event("A,B,C")
        result, chain = event.result, event.result.chain
        pending = [stubs.Image(data=b"pending-one"), stubs.Image(data=b"pending-two")]
        originals = pending.copy()
        after = AsyncMock()

        async def prepare_pending(current):
            self.assertEqual(current.send.await_count, 2)
            self.assert_preserved(current, result, chain)
            current.extras["pending_images"] = pending
            after.assert_not_awaited()

        async def flush(current):
            self.assertEqual(current.send.await_count, 3)
            self.assertEqual(self.deliveries(current)[-1], [stubs.Plain("C")])
            while current.extras["pending_images"]:
                await current.send(stubs.MessageChain(chain=[current.extras["pending_images"].pop(0)]))

        after.side_effect = flush
        await stubs.simulated_respond(
            event, decorating_hooks=[plugin.filter_reply, prepare_pending],
            after_hooks=[after], allow_prefix=True,
        )
        groups = self.deliveries(event)
        self.assertEqual(groups[:3], [[stubs.Plain(value)] for value in ("A", "B", "C")])
        self.assertEqual(groups[3:], [[originals[0]], [originals[1]]])
        self.assertIs(groups[3][0], originals[0])
        self.assertIs(groups[4][0], originals[1])
        after.assert_awaited_once_with(event)
        self.assertEqual(pending, [])
        self.assert_preserved(event, result, chain)

    async def test_filtered_empty_body_can_still_become_image_only_in_later_decorator(self):
        plugin = self.make_plugin()
        event = self.event("<think>hidden</think>")
        result, chain = event.result, event.result.chain
        image = stubs.Image()

        async def append(current):
            self.assertEqual(chain, [])
            current.send.assert_not_awaited()
            chain.append(image)

        await stubs.simulated_respond(
            event, decorating_hooks=[plugin.filter_reply, append], allow_prefix=True,
        )
        self.assertEqual(self.deliveries(event), [[image]])
        self.assertIs(self.deliveries(event)[0][0], image)
        self.assert_preserved(event, result, chain)
        self.sleep.assert_not_awaited()

    async def test_empty_and_header_only_body_does_not_guarantee_after(self):
        for components in ([stubs.Plain("，。！？")], [stubs.Plain("<think>hidden</think>")],
                           [stubs.At(), stubs.Reply(), stubs.Plain("<think>hidden</think>")]):
            with self.subTest(components=components):
                plugin = self.make_plugin()
                event = stubs.FakeEvent(stubs.FakeResult(components))
                after = AsyncMock()
                await stubs.simulated_respond(
                    event, decorating_hooks=[plugin.filter_reply], after_hooks=[after], allow_prefix=True,
                )
                event.send.assert_not_awaited()
                after.assert_not_awaited()
                self.assertEqual(event.clear_count, 0)
        self.sleep.assert_not_awaited()

    async def test_150_can_split_but_151_and_markdown_render_without_prefix(self):
        for length in (150, 151):
            with self.subTest(length=length):
                plugin = self.make_plugin()
                source = "甲" * (length - 2) + "，乙"
                event = self.event(source)
                result, chain = event.result, event.result.chain
                await plugin.filter_reply(event)
                if length == 150:
                    self.assertEqual(self.deliveries(event), [[stubs.Plain("甲" * 148)]])
                    self.assertEqual(chain, [stubs.Plain("乙")])
                    plugin.renderer.render.assert_not_awaited()
                else:
                    event.send.assert_not_awaited()
                    plugin.renderer.render.assert_awaited_once_with(source, "image")
                    self.assertEqual(len(chain), 1)
                    self.assertIsInstance(chain[0], stubs.Image)
                self.assert_preserved(event, result, chain)
        for mode, data in (("image", stubs.PNG), ("pdf", stubs.PDF)):
            with self.subTest(markdown_mode=mode):
                plugin = self.make_plugin(render_mode=mode)
                plugin.renderer.render.return_value = data
                source = "**甲，乙**"
                event = self.event(source)
                path = Path(__file__).resolve().parent / "reply-stub.pdf"
                with patch.object(stubs.PLUGIN, "save_pdf", return_value=path):
                    await plugin.filter_reply(event)
                plugin.renderer.render.assert_awaited_once_with(source, mode)
                event.send.assert_not_awaited()
                self.assertIsInstance(event.result.chain[0], stubs.Image if mode == "image" else stubs.File)

    async def test_soft_cap_merges_tail_with_one_newline_and_keeps_all_content(self):
        for cap, expected in ((1, ["A\nB\nC\nD"]), (2, ["A", "B\nC\nD"]),
                              (3, ["A", "B", "C\nD"])):
            with self.subTest(cap=cap):
                plugin = self.make_plugin(max_messages=cap)
                event = self.event("A,B,C,D")
                await stubs.simulated_respond(event, decorating_hooks=[plugin.filter_reply], allow_prefix=True)
                self.assertEqual(self.deliveries(event), [[stubs.Plain(value)] for value in expected])
                self.assertFalse(any("\n\n" in self.plain_text(group) for group in self.deliveries(event)))

    async def test_cap_does_not_cross_media_and_suffix_core_newlines_do_not_double(self):
        plugin = self.make_plugin(max_messages=2)
        image, face = stubs.Image(), stubs.Face()
        event = stubs.FakeEvent(stubs.FakeResult([
            stubs.Plain("A,B"), image, stubs.Plain("C,D,E"), face,
        ]))
        await plugin.filter_reply(event)
        self.assertEqual(self.deliveries(event), [[stubs.Plain("A")]])
        self.assertEqual(event.result.chain, [stubs.Plain("B"), image, stubs.Plain("C\nD\nE"), face])
        self.assertIs(event.result.chain[1], image)
        self.assertIs(event.result.chain[-1], face)

    async def test_render_fallback_caps_losslessly_without_inserting_newline(self):
        source = "字" * 999 + "甲乙\n**保留**" + "尾" * 1000
        for cap in (1, 2):
            with self.subTest(cap=cap):
                plugin = self.make_plugin(max_render_chars=1000, max_messages=cap)
                event = self.event(source)
                await stubs.simulated_respond(event, decorating_hooks=[plugin.filter_reply], allow_prefix=True)
                plugin.renderer.render.assert_not_awaited()
                groups = self.deliveries(event)
                self.assertEqual(len(groups), cap)
                self.assertEqual("".join(self.plain_text(group) for group in groups), source)

    async def test_natural_and_custom_atom_protection_survives_bubble_delivery(self):
        url = "https://example.com/a_b?q=1.2#part"
        cases = (
            ({"symbol_filter_mode": "natural"}, "好呀~🙂，>_<，" + url + "！尾", ["好呀~🙂", ">_<", url, "尾"]),
            ({"symbol_filter_mode": "custom", "custom_keep_symbols": "，", "custom_remove_symbols": "🙂"},
             "甲，🙂乙！👩🏽‍💻！尾", ["甲，乙", "👩🏽‍💻", "尾"]),
        )
        for config, source, expected in cases:
            with self.subTest(config=config):
                plugin = self.make_plugin(**config)
                event = self.event(source)
                await stubs.simulated_respond(event, decorating_hooks=[plugin.filter_reply], allow_prefix=True)
                plugin.renderer.render.assert_not_awaited()
                self.assertEqual(self.deliveries(event), [[stubs.Plain(value)] for value in expected])

    async def test_leading_header_or_media_prevents_prefix_and_keeps_suffix_core_readable(self):
        for header in (stubs.At(), stubs.Reply(), stubs.Image(), stubs.Face(),
                       stubs.File("keep.bin", "keep.bin"), stubs.Media("voice")):
            with self.subTest(header=header):
                plugin = self.make_plugin()
                event = stubs.FakeEvent(stubs.FakeResult([header, stubs.Plain("A,B,C")]))
                result, chain = event.result, event.result.chain
                await plugin.filter_reply(event)
                event.send.assert_not_awaited()
                self.assertEqual(chain, [header, stubs.Plain("A\n"), stubs.Plain("B\n"), stubs.Plain("C")])
                self.assertIs(chain[0], header)
                self.assert_preserved(event, result, chain)
        self.sleep.assert_not_awaited()

    async def test_interleaved_media_is_never_presend_or_crossed(self):
        plugin = self.make_plugin()
        image, face = stubs.Image(), stubs.Face()
        event = stubs.FakeEvent(stubs.FakeResult([
            stubs.Plain("A,B,C"), image, stubs.Plain("D,E,F"), face,
        ]))
        result, chain = event.result, event.result.chain
        await stubs.simulated_respond(event, decorating_hooks=[plugin.filter_reply], allow_prefix=True)
        groups = self.deliveries(event)
        self.assertEqual(groups[:2], [[stubs.Plain("A")], [stubs.Plain("B")]])
        self.assertEqual(groups[-1], [stubs.Plain("C"), image, stubs.Plain("D\n"),
                                     stubs.Plain("E\n"), stubs.Plain("F"), face])
        self.assertIs(groups[-1][1], image)
        self.assertIs(groups[-1][-1], face)
        self.assert_preserved(event, result, chain)

    async def test_prefix_removed_before_send_await_and_final_result_still_llm(self):
        plugin = self.make_plugin()
        event = self.event("A,B,C")
        result, chain = event.result, event.result.chain
        seen = []

        async def inspect(message):
            self.assertTrue(all(component is not message.chain[0] for component in chain))
            self.assert_preserved(event, result, chain)
            seen.append((self.plain_text(message.chain), self.plain_text(chain)))

        event.send.side_effect = inspect
        await plugin.filter_reply(event)
        self.assertEqual(seen, [("A", "BC"), ("B", "C")])
        self.assertEqual(chain, [stubs.Plain("C")])
        self.assertEqual(self.sleep.await_args_list, [call(0.6), call(0.6)])

    async def test_send_failure_keeps_only_unattempted_tail_for_core_without_retry(self):
        for failing_attempt in (1, 2):
            with self.subTest(failing_attempt=failing_attempt):
                plugin = self.make_plugin()
                event = self.event("A,B,C")
                result, chain = event.result, event.result.chain
                failures = [None] * (failing_attempt - 1) + [RuntimeError("ack lost after possible acceptance")]
                event.send.side_effect = failures
                self.sleep.reset_mock()
                after = AsyncMock()
                with patch.object(stubs.PLUGIN.logger, "warning") as warning:
                    await plugin.filter_reply(event)
                warning.assert_called_once()
                self.assertEqual(event.send.await_count, failing_attempt)
                expected_tail = [stubs.Plain(value) for value in (("B", "C") if failing_attempt == 1 else ("C",))]
                self.assertEqual(chain, expected_tail)
                self.assertEqual(self.sleep.await_count, failing_attempt - 1)
                self.assert_preserved(event, result, chain)
                # Retry is NOT attempted. The model core sends only unattempted tail.
                event.send.side_effect = None
                await stubs.simulated_respond(event, after_hooks=[after], allow_prefix=True)
                self.assertEqual(self.deliveries(event)[-1], expected_tail)
                self.assertEqual(event.send.await_count, failing_attempt + 1)
                self.assertEqual(sum(self.plain_text(group).count("A") for group in self.deliveries(event)), 1)
                after.assert_awaited_once_with(event)

    async def test_direct_hook_send_cancellation_propagates_and_discards_tail(self):
        plugin = self.make_plugin()
        event = self.event("A,B,C")
        result, chain = event.result, event.result.chain
        entered, release = asyncio.Event(), asyncio.Event()

        async def delayed_send(message):
            entered.set()
            await release.wait()

        event.send.side_effect = delayed_send
        task = asyncio.create_task(plugin.filter_reply(event))
        try:
            await asyncio.wait_for(entered.wait(), timeout=2)
            self.assertTrue(task.cancel())
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=2)
        finally:
            release.set()
            if not task.done():
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(task, timeout=2)
        self.assertTrue(task.cancelled())
        self.assert_cancelling(task, 1)
        self.assertEqual(self.deliveries(event), [[stubs.Plain("A")]])
        self.sleep.assert_not_awaited()
        self.assert_discarded_reply(event, result, chain)

    async def test_direct_hook_interval_cancellation_propagates_and_discards_tail(self):
        plugin = self.make_plugin()
        event = self.event("A,B,C")
        result, chain = event.result, event.result.chain
        entered, release = asyncio.Event(), asyncio.Event()

        async def delayed_sleep(seconds):
            entered.set()
            await release.wait()

        self.sleep.side_effect = delayed_sleep
        task = asyncio.create_task(plugin.filter_reply(event))
        try:
            await asyncio.wait_for(entered.wait(), timeout=2)
            self.assertTrue(task.cancel())
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=2)
        finally:
            release.set()
            if not task.done():
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(task, timeout=2)
        self.assertTrue(task.cancelled())
        self.assert_cancelling(task, 1)
        self.assertEqual(self.deliveries(event), [[stubs.Plain("A")]])
        self.assert_discarded_reply(event, result, chain)

    async def test_concurrent_result_list_prefix_identity_or_text_changes_stop_presends(self):
        for phase in ("send", "sleep"):
            for change in ("result", "list", "replace_prefix", "edit_prefix", "prepend_media", "clear"):
                with self.subTest(phase=phase, change=change):
                    plugin = self.make_plugin()
                    event = self.event("A,B,C")
                    result, original_chain = event.result, event.result.chain
                    expected = {}

                    def mutate():
                        if change == "result":
                            event.result = stubs.FakeResult([stubs.Plain("replacement")])
                        elif change == "list":
                            result.chain = [stubs.Plain("new list")]
                        elif change == "replace_prefix":
                            original_chain[0] = stubs.Plain("new prefix")
                        elif change == "edit_prefix":
                            original_chain[0].text = "edited prefix"
                        elif change == "prepend_media":
                            original_chain.insert(0, stubs.Image())
                        else:
                            original_chain.clear()
                        expected["result"] = event.result
                        expected["chain"] = event.result.chain
                        expected["items"] = event.result.chain.copy()
                        expected["text"] = self.plain_text(event.result.chain)

                    async def after_send(message):
                        if phase == "send":
                            mutate()

                    async def after_sleep(seconds):
                        if phase == "sleep":
                            mutate()

                    event.send.side_effect = after_send
                    self.sleep.side_effect = after_sleep
                    await plugin.filter_reply(event)
                    self.assertEqual(self.deliveries(event), [[stubs.Plain("A")]])
                    self.assertIs(event.result, expected["result"])
                    self.assertIs(event.result.chain, expected["chain"])
                    self.assertEqual(len(event.result.chain), len(expected["items"]))
                    self.assertTrue(all(a is b for a, b in zip(event.result.chain, expected["items"])))
                    self.assertEqual(self.plain_text(event.result.chain), expected["text"])
                    self.assertEqual(event.clear_count, 0)
                    event.stop_event.assert_not_called()

    async def test_append_only_suffix_attachments_during_send_and_sleep_are_retained(self):
        plugin = self.make_plugin()
        event = self.event("A,B,C")
        result, chain = event.result, event.result.chain
        images = [stubs.Image(data=b"during-send"), stubs.Image(data=b"during-sleep")]
        added = {"send": False, "sleep": False}

        async def send(message):
            if not added["send"]:
                chain.append(images[0])
                added["send"] = True

        async def sleep(seconds):
            if not added["sleep"]:
                chain.append(images[1])
                added["sleep"] = True

        event.send.side_effect = send
        self.sleep.side_effect = sleep
        await plugin.filter_reply(event)
        self.assertEqual(self.deliveries(event), [[stubs.Plain("A")], [stubs.Plain("B")]])
        self.assertEqual(chain, [stubs.Plain("C"), *images])
        self.assertIs(chain[1], images[0])
        self.assertIs(chain[2], images[1])
        self.assert_preserved(event, result, chain)

    async def test_core_mode_opt_out_still_does_not_presend_or_wait(self):
        plugin = self.make_plugin(send_mode="core")
        event = self.event("A,B,C")
        await plugin.filter_reply(event)
        self.assertEqual(event.result.chain, [stubs.Plain("A\n"), stubs.Plain("B\n"), stubs.Plain("C")])
        event.send.assert_not_awaited()
        self.sleep.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
