"""Real Task.cancel regressions against a deliberately limited framework MODEL.

The conceptual decorator catches BaseException, logs, checks STOP after each
handler, and re-reads the result. The onion scheduler resumes a lock-owning agent
async generator only if STOP is absent. Normal generator exhaustion, not a
finally around the scheduler, must release the lock before pipeline completion.
Generators stay strongly referenced so garbage collection cannot fake success.

This is original test-model code, NOT real AstrBot/OneBot integration or proof of
real framework lock cleanup, delivery acknowledgement, or exactly-once delivery.
The meme substitute, like a result-dependent hook, ignores a missing result; an
arbitrary plugin that invents a new result is outside this guarantee. External
STOP still need not exhaust upstream generators; the cancellation fix must not
introduce STOP. The agent-stop extra models cooperative upstream shutdown.

Cancellation uses create_task + Task.cancel at asyncio.Event barriers, not mocks
that merely raise CancelledError. wait_for only bounds test coordination, never
production sending. No detached sender/timeout disguises a dependency that blocks
forever or suppresses cancellation without returning.
"""

import asyncio
from contextlib import suppress
import inspect
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from astrbot_plugin_reply_filter.rendering import MarkdownDocument
from astrbot_plugin_reply_filter.tests import test_plugin as stubs


class CancellationBarrier:
    """A cooperative dependency stopped at a known await, not a timing guess."""

    def __init__(self, value=None, *, swallow=False):
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.cancel_seen = asyncio.Event()
        self.value = value
        self.swallow = swallow
        self.cancelling_seen = 0

    async def __call__(self, *args):
        self.entered.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            task = asyncio.current_task()
            self.cancelling_seen = task.cancelling() if hasattr(task, "cancelling") else 0
            self.cancel_seen.set()
            if not self.swallow:
                raise
        return self.value


class ModelPipeline:
    """Original, reduced onion/lock model; no framework source is vendored here."""

    def __init__(self, plugin):
        self.plugin = plugin
        self.locks = {}
        self.trace = []
        self.generators = []
        self.logged = []
        self.log = Mock()
        self.meme = AsyncMock(side_effect=self._append_meme)
        self.meme_work = Mock()
        self.after = AsyncMock()

    async def _append_meme(self, event):
        result = event.get_result()
        if result is None or not result.chain:
            return
        self.meme_work(event)
        result.chain.append(stubs.Image(data=b"model-meme"))

    async def decorate(self, event):
        if event.get_result() is None:
            return
        # Relevant 4.29.3 behavior: BaseException catch, log, then STOP check.
        # Clearing a result does not prevent later handlers from being called.
        for handler in (self.plugin.filter_reply, self.meme):
            try:
                await handler(event)
            except BaseException as error:
                self.logged.append(error)
                self.log(error)
                self.trace.append(("decorator_logged", event))
            if event.is_stopped():
                self.trace.append(("decorator_stop", event))
                return
        if event.get_result() is None:
            self.trace.append(("decorator_no_result", event))
            return

    async def respond(self, event):
        result = event.get_result()
        if result is None or not result.chain:
            return
        self.trace.append(("respond", event))
        await event.send(stubs.MessageChain(chain=list(result.chain)))
        await self.after(event)
        event.clear_result()

    async def agent(self, event, *, waiting=None, acquired=None, released=None, intermediate=False):
        lock = self.locks.setdefault(event.unified_msg_origin, asyncio.Lock())
        if waiting is not None:
            waiting.set()
        async with lock:
            self.trace.append(("lock_acquired", event))
            if acquired is not None:
                acquired.set()
            try:
                # Lock ownership spans downstream processing at this yield.
                yield
                self.trace.append(("agent_resumed", event))
                if event.extras.get("agent_stop_requested"):
                    self.trace.append(("agent_stop_requested", event))
                elif intermediate:
                    event.result = stubs.FakeResult([stubs.Plain("must not generate again")])
                    self.trace.append(("agent_generated_again", event))
                    yield
            finally:
                self.trace.append(("agent_finally", event))
        self.trace.append(("lock_released", event))
        if released is not None:
            released.set()

    async def scheduler(self, event, stages, start=0):
        # Simplified original implementation of recursive onion scheduling.
        for index in range(start, len(stages)):
            work = stages[index](event)
            if inspect.isasyncgen(work):
                self.generators.append(work)
                async for _ in work:
                    if event.is_stopped():
                        self.trace.append(("scheduler_stop", event))
                        break
                    await self.scheduler(event, stages, index + 1)
                    if event.is_stopped():
                        self.trace.append(("scheduler_stop", event))
                        break
            else:
                await work
                if event.is_stopped():
                    self.trace.append(("scheduler_stop", event))
                    break

    async def run(self, event, **agent_options):
        def start_agent(current):
            return self.agent(current, **agent_options)

        await self.scheduler(event, (start_agent, self.decorate, self.respond))
        self.trace.append(("pipeline_done", event))

    async def close(self):
        # Test teardown ONLY. Assertions must pass before explicit aclose.
        for generator in reversed(self.generators):
            await generator.aclose()


class CancellationTests(stubs.PluginFixtures, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tasks = []
        self.barriers = []
        self.pipelines = []

    async def asyncTearDown(self):
        for barrier in self.barriers:
            barrier.release.set()
        for task in self.tasks:
            if not task.done():
                task.cancel()
        for task in self.tasks:
            with suppress(asyncio.CancelledError):
                await task
        for pipeline in self.pipelines:
            await pipeline.close()

    def make_plugin(self, **config):
        config.setdefault("send_interval", 0)
        context = SimpleNamespace(get_registered_star=Mock(return_value=None))
        plugin = stubs.PLUGIN.ReplyFilterPlugin(context, config)
        plugin.renderer = SimpleNamespace(
            document=MarkdownDocument(), render=AsyncMock(return_value=stubs.PNG),
        )
        return plugin

    def model(self, plugin):
        pipeline = ModelPipeline(plugin)
        self.pipelines.append(pipeline)
        return pipeline

    def barrier(self, value=None, *, swallow=False):
        barrier = CancellationBarrier(value, swallow=swallow)
        self.barriers.append(barrier)
        return barrier

    def start(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.tasks.append(task)
        return task

    async def reached(self, barrier):
        await asyncio.wait_for(barrier.entered.wait(), timeout=2)

    async def finish(self, task):
        return await asyncio.wait_for(task, timeout=2)

    async def cancel_in_model(self, task, barrier):
        await self.reached(barrier)
        self.assertTrue(task.cancel())
        await self.finish(task)
        self.assertTrue(barrier.cancel_seen.is_set())
        # The modeled decorator catches cancellation; nobody may uncancel it.
        self.assertFalse(task.cancelled())
        self.assert_cancelling(task, 1)

    def assert_model_discarded(self, pipeline, event, result, chain):
        self.assert_discarded_reply(event, result, chain)
        pipeline.meme_work.assert_not_called()
        pipeline.after.assert_not_awaited()
        self.assertNotIn(("respond", event), pipeline.trace)
        self.assertIn(("decorator_logged", event), pipeline.trace)
        self.assertNotIn(("scheduler_stop", event), pipeline.trace)
        self.assertIn(("agent_stop_requested", event), pipeline.trace)
        self.assertNotIn(("agent_generated_again", event), pipeline.trace)
        self.assertLess(pipeline.trace.index(("agent_resumed", event)),
                        pipeline.trace.index(("lock_released", event)))
        self.assertLess(pipeline.trace.index(("lock_released", event)),
                        pipeline.trace.index(("pipeline_done", event)))
        self.assertFalse(pipeline.locks[event.unified_msg_origin].locked())
        self.assertTrue(all(isinstance(error, asyncio.CancelledError) for error in pipeline.logged))

    @staticmethod
    def deliveries(event):
        return [entry.args[0].chain for entry in event.send.await_args_list]

    async def test_sleep_cancel_discards_tail_and_releases_model_lock_before_next_session_request(self):
        plugin = self.make_plugin(send_interval=0.6)
        pipeline = self.model(plugin)
        old = self.event("A,B,C")
        old.result.chain.append(stubs.Image(data=b"unsent attachment"))
        original, chain = old.result, old.result.chain
        barrier = self.barrier()
        waiting, acquired, released = asyncio.Event(), asyncio.Event(), asyncio.Event()
        with patch.object(stubs.PLUGIN.asyncio, "sleep", new=AsyncMock(side_effect=barrier.__call__)):
            old_task = self.start(pipeline.run(old, released=released, intermediate=True))
            await self.reached(barrier)
            self.assertEqual(self.deliveries(old), [[stubs.Plain("A")]])
            self.assertTrue(pipeline.locks[old.unified_msg_origin].locked())
            fresh = self.event("fresh request", session=old.unified_msg_origin)
            self.assertFalse(fresh.is_stopped())
            self.assertNotIn("agent_stop_requested", fresh.extras)
            fresh_task = self.start(pipeline.run(fresh, waiting=waiting, acquired=acquired))
            await asyncio.wait_for(waiting.wait(), timeout=2)
            self.assertFalse(acquired.is_set())
            self.assertFalse(fresh_task.done())
            fresh.send.assert_not_awaited()
            self.assertTrue(old_task.cancel())
            await self.finish(old_task)
            # Assert old cleanup before allowing fresh-event assertions to mask it.
            self.assert_discarded_reply(old, original, chain)
            self.assertTrue(released.is_set())
            await self.finish(fresh_task)
        self.assertTrue(barrier.cancel_seen.is_set())
        self.assert_cancelling(old_task, 1)
        self.assertFalse(old_task.cancelled())
        self.assertEqual(self.deliveries(old), [[stubs.Plain("A")]])
        self.assertTrue(acquired.is_set())
        self.assertLess(pipeline.trace.index(("lock_released", old)),
                        pipeline.trace.index(("pipeline_done", old)))
        self.assertLess(pipeline.trace.index(("lock_released", old)),
                        pipeline.trace.index(("lock_acquired", fresh)))
        self.assertNotIn(("respond", old), pipeline.trace)
        self.assertNotIn(("agent_generated_again", old), pipeline.trace)
        pipeline.meme_work.assert_called_once_with(fresh)
        pipeline.after.assert_awaited_once_with(fresh)
        self.assertEqual(self.plain_text(self.deliveries(fresh)[0]), "fresh request")
        self.assertIsInstance(self.deliveries(fresh)[0][-1], stubs.Image)
        self.assertFalse(fresh.is_stopped())
        self.assertNotIn("agent_stop_requested", fresh.extras)
        fresh.stop_event.assert_not_called()

    async def test_repeated_interruptions_do_not_poison_fresh_same_session_events(self):
        plugin = self.make_plugin(send_interval=0.6)
        pipeline = self.model(plugin)
        for index in range(4):
            with self.subTest(round=index):
                event = self.event(f"round{index},tail,never")
                self.assertFalse(event.is_stopped())
                self.assertNotIn("agent_stop_requested", event.extras)
                result, chain = event.result, event.result.chain
                barrier = self.barrier()
                with patch.object(stubs.PLUGIN.asyncio, "sleep", new=AsyncMock(side_effect=barrier.__call__)):
                    task = self.start(pipeline.run(event, intermediate=True))
                    await self.cancel_in_model(task, barrier)
                self.assert_model_discarded(pipeline, event, result, chain)
                self.assertEqual(self.deliveries(event), [[stubs.Plain(f"round{index}")]])
        fresh = self.event("fresh request")
        await self.finish(self.start(pipeline.run(fresh)))
        self.assertFalse(fresh.is_stopped())
        self.assertNotIn("agent_stop_requested", fresh.extras)
        pipeline.meme_work.assert_called_once_with(fresh)
        pipeline.after.assert_awaited_once_with(fresh)
        fresh.send.assert_awaited_once()

    async def test_send_cancel_with_zero_or_positive_interval_never_retries_tail(self):
        for interval in (0, 0.6):
            with self.subTest(send_interval=interval):
                pipeline = self.model(self.make_plugin(send_interval=interval))
                event = self.event("A,B,C")
                result, chain = event.result, event.result.chain
                barrier = self.barrier()
                event.send.side_effect = barrier.__call__
                with patch.object(stubs.PLUGIN.asyncio, "sleep", new_callable=AsyncMock) as sleep:
                    await self.cancel_in_model(self.start(pipeline.run(event)), barrier)
                self.assert_model_discarded(pipeline, event, result, chain)
                self.assertEqual(self.deliveries(event), [[stubs.Plain("A")]])
                sleep.assert_not_awaited()

    async def test_render_cancel_has_no_fallback_or_delivery(self):
        plugin = self.make_plugin()
        pipeline = self.model(plugin)
        event = self.event("**render first**")
        result, chain = event.result, event.result.chain
        barrier = self.barrier(stubs.PNG)
        plugin.renderer.render.side_effect = barrier.__call__
        with patch.object(stubs.PLUGIN.logger, "warning") as warning:
            task = self.start(pipeline.run(event))
            await self.cancel_in_model(task, barrier)
        self.assert_model_discarded(pipeline, event, result, chain)
        event.send.assert_not_awaited()
        plugin.renderer.render.assert_awaited_once_with("**render first**", "image")
        warning.assert_not_called()

    @unittest.skipUnless(hasattr(asyncio.Task, "cancelling"), "Task.cancelling requires Python 3.11+")
    async def test_swallowed_cancel_is_detected_after_prepare_send_and_sleep(self):
        for phase, interval in (("prepare", 0), ("send", 0), ("send", 0.6), ("sleep", 0.6)):
            with self.subTest(phase=phase, send_interval=interval):
                plugin = self.make_plugin(send_interval=interval)
                pipeline = self.model(plugin)
                event = self.event("A,B,C")
                result, chain = event.result, event.result.chain
                value = [stubs.Plain("must not commit")] if phase == "prepare" else None
                barrier = self.barrier(value, swallow=True)
                if phase == "prepare":
                    plugin._prepare = AsyncMock(side_effect=barrier.__call__)
                elif phase == "send":
                    event.send.side_effect = barrier.__call__
                sleep = AsyncMock(side_effect=barrier.__call__ if phase == "sleep" else None)
                with patch.object(stubs.PLUGIN.asyncio, "sleep", new=sleep):
                    await self.cancel_in_model(self.start(pipeline.run(event)), barrier)
                self.assertEqual(barrier.cancelling_seen, 1)
                self.assert_model_discarded(pipeline, event, result, chain)
                self.assertEqual(self.deliveries(event), [] if phase == "prepare" else [[stubs.Plain("A")]])
                if phase == "sleep":
                    sleep.assert_awaited_once_with(0.6)
                else:
                    sleep.assert_not_awaited()

    @unittest.skipUnless(hasattr(asyncio.Task, "cancelling"), "Task.cancelling requires Python 3.11+")
    async def test_direct_hook_reraises_even_when_send_swallows_real_task_cancel(self):
        plugin = self.make_plugin(send_interval=0)
        event = self.event("A,B,C")
        result, chain = event.result, event.result.chain
        barrier = self.barrier(swallow=True)
        event.send.side_effect = barrier.__call__
        task = self.start(plugin.filter_reply(event))
        await self.reached(barrier)
        self.assertTrue(task.cancel())
        with self.assertRaises(asyncio.CancelledError):
            await self.finish(task)
        self.assertTrue(task.cancelled())
        self.assert_cancelling(task, 1)
        self.assertEqual(barrier.cancelling_seen, 1)
        self.assert_discarded_reply(event, result, chain)
        self.assertEqual(self.deliveries(event), [[stubs.Plain("A")]])

    async def test_direct_render_component_propagates_real_task_cancel(self):
        plugin = self.make_plugin()
        barrier = self.barrier(stubs.PNG)
        plugin.renderer.render.side_effect = barrier.__call__
        task = self.start(plugin._render_component("**render**", "image"))
        await self.reached(barrier)
        self.assertTrue(task.cancel())
        with self.assertRaises(asyncio.CancelledError):
            await self.finish(task)
        self.assertTrue(task.cancelled())
        self.assertTrue(barrier.cancel_seen.is_set())
        plugin.renderer.render.assert_awaited_once_with("**render**", "image")

    async def test_cancellation_clears_current_replacement_not_stale_snapshot(self):
        plugin = self.make_plugin()
        pipeline = self.model(plugin)
        event = self.event("old reply")
        stale_result, stale_chain = event.result, event.result.chain
        barrier = self.barrier([stubs.Plain("old prepared")])
        plugin._prepare = AsyncMock(side_effect=barrier.__call__)
        task = self.start(pipeline.run(event))
        await self.reached(barrier)
        current = stubs.FakeResult([stubs.Plain("replacement tail"), stubs.Image()])
        current_chain = current.chain
        event.result = current
        self.assertTrue(task.cancel())
        await self.finish(task)
        self.assert_model_discarded(pipeline, event, current, current_chain)
        self.assertIs(stale_result.chain, stale_chain)
        self.assertEqual(stale_chain, [stubs.Plain("old reply")])
        event.send.assert_not_awaited()

    async def test_model_detects_stop_break_without_upstream_lock_exhaustion(self):
        # Negative control: the MODEL must not falsely release via outer finally.
        plugin = self.make_plugin()
        plugin.filter_reply = AsyncMock(side_effect=lambda event: event.stop_event())
        pipeline = self.model(plugin)
        event = self.event("stopped")
        await self.finish(self.start(pipeline.run(event)))
        self.assertTrue(pipeline.locks[event.unified_msg_origin].locked())
        self.assertNotIn(("agent_resumed", event), pipeline.trace)
        self.assertNotIn(("lock_released", event), pipeline.trace)
        self.assertIn(("scheduler_stop", event), pipeline.trace)
        event.send.assert_not_awaited()
        # asyncTearDown explicitly closes the retained generator AFTER assertions.

    async def test_model_baseexception_catch_really_continues_after_unhandled_cancel(self):
        barrier = self.barrier()
        plugin = self.make_plugin()
        plugin.filter_reply = AsyncMock(side_effect=barrier.__call__)
        pipeline = self.model(plugin)
        event = self.event("unsafe old tail")
        await self.cancel_in_model(self.start(pipeline.run(event)), barrier)
        pipeline.log.assert_called_once()
        self.assertIsInstance(pipeline.logged[0], asyncio.CancelledError)
        pipeline.meme_work.assert_called_once_with(event)
        pipeline.after.assert_awaited_once_with(event)
        self.assertEqual(self.plain_text(self.deliveries(event)[0]), "unsafe old tail")
        self.assertFalse(pipeline.locks[event.unified_msg_origin].locked())

    async def test_explicit_stop_before_hook_skips_prepare_without_task_cancel(self):
        plugin = self.make_plugin()
        event = self.event("A,B,C")
        chain = event.result.chain
        plugin._prepare = AsyncMock()
        event.stop_event()
        task = self.start(plugin.filter_reply(event))
        await self.finish(task)
        self.assert_cancelling(task, 0)
        self.assertFalse(task.cancelled())
        self.assertIs(event.result.chain, chain)
        self.assertEqual(chain, [stubs.Plain("A,B,C")])
        plugin._prepare.assert_not_awaited()
        event.send.assert_not_awaited()
        event.stop_event.assert_called_once_with()
        event.clear_result.assert_not_called()

    async def test_explicit_stop_during_prepare_does_not_commit_or_send_prefix(self):
        plugin = self.make_plugin()
        event = self.event("A,B,C")
        result, chain = event.result, event.result.chain
        barrier = self.barrier([stubs.Plain("wrong"), stubs.Plain("prefix")])
        plugin._prepare = AsyncMock(side_effect=barrier.__call__)
        task = self.start(plugin.filter_reply(event))
        await self.reached(barrier)
        event.stop_event()
        barrier.release.set()
        await self.finish(task)
        self.assert_cancelling(task, 0)
        self.assertFalse(task.cancelled())
        self.assertIs(event.result, result)
        self.assertIs(result.chain, chain)
        self.assertEqual(chain, [stubs.Plain("A,B,C")])
        event.send.assert_not_awaited()
        event.clear_result.assert_not_called()
        self.assertNotIn("agent_stop_requested", event.extras)

    async def test_explicit_stop_during_send_or_sleep_never_sends_next_prefix(self):
        for phase, interval in (("send", 0), ("send", 0.6), ("sleep", 0.6)):
            with self.subTest(phase=phase, send_interval=interval):
                plugin = self.make_plugin(send_interval=interval)
                event = self.event("A,B,C")
                result, chain = event.result, event.result.chain
                barrier = self.barrier()
                if phase == "send":
                    event.send.side_effect = barrier.__call__
                sleep = AsyncMock(side_effect=barrier.__call__ if phase == "sleep" else None)
                with patch.object(stubs.PLUGIN.asyncio, "sleep", new=sleep):
                    task = self.start(plugin.filter_reply(event))
                    await self.reached(barrier)
                    event.stop_event()
                    barrier.release.set()
                    await self.finish(task)
                self.assert_cancelling(task, 0)
                self.assertFalse(task.cancelled())
                self.assertFalse(barrier.cancel_seen.is_set())
                self.assertTrue(event.is_stopped())
                event.stop_event.assert_called_once_with()
                self.assertIs(event.result, result)
                self.assertIs(result.chain, chain)
                self.assertEqual(chain, [stubs.Plain("B"), stubs.Plain("C")])
                event.clear_result.assert_not_called()
                self.assertNotIn("agent_stop_requested", event.extras)
                self.assertEqual(self.deliveries(event), [[stubs.Plain("A")]])
                if phase == "send":
                    sleep.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
