import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


class TelegramPipelinePressureTest(unittest.IsolatedAsyncioTestCase):
    def test_cli_exits_nonzero_when_correctness_fails(self):
        from tools.pressure_telegram_pipeline import main

        args = SimpleNamespace(
            pipelines=1,
            concurrency=1,
            telegram_latency_ms=0,
            busy_latency_ms=0,
            search_latency_ms=0,
            download_latency_ms=0,
            rename_latency_ms=0,
            duplicate_clicks=1,
            timeout_seconds=1,
            frontend_mode="direct",
            cancelled_busy_late_apply_ms=None,
            output=None,
        )
        with (
            patch(
                "tools.pressure_telegram_pipeline._parse_args",
                return_value=args,
            ),
            patch(
                "tools.pressure_telegram_pipeline._run",
                AsyncMock(return_value={"correctness": {"passed": False}}),
            ),
            patch("builtins.print"),
        ):
            with self.assertRaises(SystemExit) as raised:
                main()

        self.assertEqual(raised.exception.code, 1)

    def test_percentile_summary_uses_nearest_rank(self):
        from tools.pressure_telegram_pipeline import percentile_summary

        self.assertEqual(
            percentile_summary([1, 2, 3, 4, 100]),
            {
                "n": 5,
                "min": 1.0,
                "mean": 22.0,
                "p50": 3.0,
                "p95": 100.0,
                "p99": 100.0,
                "max": 100.0,
            },
        )

    async def test_real_ptb_updates_complete_once_under_duplicate_clicks(self):
        from tools.pressure_telegram_pipeline import _run

        result = await _run(
            pipelines=2,
            concurrency=2,
            telegram_latency_ms=0,
            busy_latency_ms=0,
            search_latency_ms=0,
            download_latency_ms=0,
            rename_latency_ms=0,
            duplicate_clicks=2,
        )

        self.assertEqual(result["schema_version"], "telepiplex.telegram_pressure.v1")
        self.assertEqual(result["frontend"], "python-telegram-bot.Application.process_update")
        self.assertEqual(result["frontend_mode"], "direct")
        self.assertEqual(
            result["frontend_semantics"],
            "direct handler concurrency; bypasses the default polling update_queue",
        )
        self.assertEqual(result["correctness"]["completed_operations"], 2)
        self.assertEqual(result["correctness"]["callback_dispatches"], 2)
        self.assertEqual(result["correctness"]["duplicate_callbacks_rejected"], 2)
        self.assertEqual(result["correctness"]["download_effects"], 2)
        self.assertEqual(result["correctness"]["rename_effects"], 2)
        self.assertEqual(result["correctness"]["event_deliveries"], 2)
        self.assertEqual(result["correctness"]["terminal_owners"], ["rename"])
        self.assertEqual(result["correctness"]["sealed_segments"], 6)
        self.assertEqual(result["correctness"]["durably_sealed_segments"], 6)
        self.assertEqual(
            result["correctness"]["operations_without_active_segment"],
            2,
        )
        self.assertEqual(result["correctness"]["published_milestones"], 2)
        self.assertEqual(result["correctness"]["delivered_milestones"], 2)
        self.assertEqual(result["correctness"]["exactly_once_milestones"], 2)
        self.assertEqual(
            result["correctness"]["milestone_delivery_counts"],
            {
                "telegram-pressure-0000": 1,
                "telegram-pressure-0001": 1,
            },
        )
        self.assertEqual(result["correctness"]["ordered_milestones"], 2)
        self.assertEqual(result["correctness"]["terminal_projections"], 2)
        self.assertEqual(result["correctness"]["final_segment_visible"], 6)
        self.assertEqual(result["correctness"]["final_terminal_visible"], 2)
        self.assertTrue(result["correctness"]["operation_drain_completed"])
        self.assertTrue(result["correctness"]["milestone_drain_completed"])
        self.assertEqual(result["correctness"]["failures"], 0)
        self.assertTrue(result["correctness"]["passed"])
        self.assertEqual(result["resources"]["tasks"]["final_delta"], 0)
        self.assertEqual(result["resources"]["render_locks"]["final"], 0)
        if result["resources"]["fds"]["final_delta"] is not None:
            self.assertEqual(result["resources"]["fds"]["final_delta"], 0)
        self.assertEqual(result["telegram"]["actions"]["answerCallbackQuery"], 4)
        for metric in (
            "command_to_candidate_ms",
            "callback_ack_ms",
            "callback_to_feature_rpc_ms",
            "terminal_internal_ms",
            "foreground_complete_ms",
        ):
            self.assertEqual(result["latency_ms"][metric]["n"], 2)
            self.assertGreaterEqual(result["latency_ms"][metric]["min"], 0)

    async def test_default_update_queue_mode_starts_and_drains_ptb_application(self):
        from tools.pressure_telegram_pipeline import _run

        result = await _run(
            pipelines=3,
            concurrency=3,
            telegram_latency_ms=0,
            busy_latency_ms=0,
            search_latency_ms=0,
            download_latency_ms=0,
            rename_latency_ms=0,
            duplicate_clicks=2,
            frontend_mode="queue",
        )

        self.assertEqual(result["frontend_mode"], "queue")
        self.assertEqual(
            result["frontend_semantics"],
            "Application.start default serial update_queue/BaseUpdateProcessor",
        )
        self.assertEqual(result["scenario"]["update_processor_concurrency"], 1)
        self.assertEqual(result["correctness"]["callback_dispatches"], 3)
        self.assertEqual(result["correctness"]["duplicate_callbacks_rejected"], 3)
        self.assertEqual(result["resources"]["tasks"]["final_delta"], 0)
        self.assertEqual(
            result["resources"]["lifecycle"]["tasks"]["unexpected_final"],
            [],
        )
        self.assertFalse(any(
            "update_fetcher" in task.get_name()
            for task in asyncio.all_tasks()
        ))

    async def test_sequential_single_task_drains_feedback_and_stays_within_call_budget(self):
        from tools.pressure_telegram_pipeline import _run

        result = await _run(
            pipelines=1,
            concurrency=1,
            telegram_latency_ms=0,
            busy_latency_ms=0,
            search_latency_ms=0,
            download_latency_ms=0,
            rename_latency_ms=0,
            duplicate_clicks=1,
            frontend_mode="queue",
        )

        self.assertTrue(
            result["correctness"]["callback_feedback_drain_completed"]
        )
        self.assertLessEqual(
            result["telegram"]["api_calls_per_pipeline"],
            9,
        )
        self.assertTrue(result["correctness"]["passed"])

    async def test_queue_teardown_leak_fails_lifecycle_resource_gate(self):
        from telegram.ext import Application
        from tools.pressure_telegram_pipeline import _run

        async def skip_lifecycle_cleanup(_application):
            return None

        leaked_tasks = []
        try:
            with (
                patch.object(Application, "stop", skip_lifecycle_cleanup),
                patch.object(Application, "shutdown", skip_lifecycle_cleanup),
            ):
                result = await _run(
                    pipelines=1,
                    concurrency=1,
                    telegram_latency_ms=0,
                    busy_latency_ms=0,
                    search_latency_ms=0,
                    download_latency_ms=0,
                    rename_latency_ms=0,
                    frontend_mode="queue",
                )
            leaked_tasks = [
                task
                for task in asyncio.all_tasks()
                if "update_fetcher" in task.get_name()
            ]
        finally:
            for task in leaked_tasks:
                task.cancel()
            await asyncio.gather(*leaked_tasks, return_exceptions=True)

        lifecycle_tasks = result["resources"]["lifecycle"]["tasks"]
        self.assertEqual(lifecycle_tasks["final_delta"], 1)
        self.assertTrue(any(
            "update_fetcher" in task["name"]
            for task in lifecycle_tasks["unexpected_final"]
        ))
        self.assertFalse(result["correctness"]["passed"])

    async def test_stale_edit_after_terminal_fails_final_visibility_gate(self):
        from app.handlers import interaction_handler
        from tools.pressure_telegram_pipeline import _run

        original_drain = interaction_handler.drain_callback_feedback

        async def drain_then_overwrite_terminal(application, timeout=None):
            completed = await original_drain(application, timeout=timeout)
            for message in application.bot.request.messages.values():
                if message.get("text") == "整理完成。":
                    message["text"] = "正在整理…"
            return completed

        with patch.object(
            interaction_handler,
            "drain_callback_feedback",
            drain_then_overwrite_terminal,
        ):
            result = await _run(
                pipelines=1,
                concurrency=1,
                telegram_latency_ms=0,
                busy_latency_ms=0,
                search_latency_ms=0,
                download_latency_ms=0,
                rename_latency_ms=0,
            )

        self.assertEqual(result["correctness"]["terminal_projections"], 1)
        self.assertEqual(result["correctness"]["final_terminal_visible"], 0)
        self.assertFalse(result["correctness"]["passed"])

    async def test_delayed_stale_edit_fails_quiescent_visibility_gate(self):
        from app.handlers import interaction_handler
        from tools.pressure_telegram_pipeline import _run

        original_drain = interaction_handler.drain_callback_feedback

        async def drain_then_apply_delayed_stale_delivery(
            application,
            timeout=None,
        ):
            completed = await original_drain(application, timeout=timeout)
            await asyncio.sleep(0.025)
            for message in application.bot.request.messages.values():
                if message.get("text") == "整理完成。":
                    message["text"] = "正在整理…"
            return completed

        with patch.object(
            interaction_handler,
            "drain_callback_feedback",
            drain_then_apply_delayed_stale_delivery,
        ):
            result = await _run(
                pipelines=1,
                concurrency=1,
                telegram_latency_ms=0,
                busy_latency_ms=0,
                search_latency_ms=0,
                download_latency_ms=0,
                rename_latency_ms=0,
            )

        self.assertEqual(result["correctness"]["terminal_projections"], 1)
        self.assertEqual(result["correctness"]["final_terminal_visible"], 0)
        self.assertEqual(result["resources"]["tasks"]["final_delta"], 0)
        self.assertFalse(result["correctness"]["passed"])

    async def test_duplicate_milestone_delivery_fails_exactly_once_gate(self):
        from app.handlers import interaction_handler
        from tools.pressure_telegram_pipeline import _run

        original_deliver = interaction_handler.deliver_operation_milestone

        async def deliver_twice(*args, **kwargs):
            first = await original_deliver(*args, **kwargs)
            await original_deliver(*args, **kwargs)
            return first

        with patch.object(
            interaction_handler,
            "deliver_operation_milestone",
            deliver_twice,
        ):
            result = await _run(
                pipelines=1,
                concurrency=1,
                telegram_latency_ms=0,
                busy_latency_ms=0,
                search_latency_ms=0,
                download_latency_ms=0,
                rename_latency_ms=0,
            )

        self.assertEqual(result["correctness"]["published_milestones"], 1)
        self.assertEqual(result["correctness"]["delivered_milestones"], 2)
        self.assertEqual(result["correctness"]["exactly_once_milestones"], 0)
        self.assertEqual(
            result["correctness"]["milestone_delivery_counts"],
            {"telegram-pressure-0000": 2},
        )
        self.assertFalse(result["correctness"]["passed"])

    async def test_uncleared_terminal_controls_fail_final_visibility_gate(self):
        from app.handlers import interaction_handler
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        from tools.pressure_telegram_pipeline import _run

        original_drain = interaction_handler.drain_callback_feedback

        async def drain_then_restore_terminal_controls(
            application,
            timeout=None,
        ):
            completed = await original_drain(application, timeout=timeout)
            for message in application.bot.request.messages.values():
                if message.get("text") != "整理完成。":
                    continue
                await application.bot.edit_message_reply_markup(
                    chat_id=message["chat_id"],
                    message_id=message["message_id"],
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("取消", callback_data="cancel"),
                    ]]),
                )
            return completed

        with patch.object(
            interaction_handler,
            "drain_callback_feedback",
            drain_then_restore_terminal_controls,
        ):
            result = await _run(
                pipelines=1,
                concurrency=1,
                telegram_latency_ms=0,
                busy_latency_ms=0,
                search_latency_ms=0,
                download_latency_ms=0,
                rename_latency_ms=0,
            )

        self.assertEqual(result["correctness"]["durably_sealed_segments"], 3)
        self.assertEqual(result["correctness"]["terminal_projections"], 1)
        self.assertEqual(result["correctness"]["final_terminal_visible"], 0)
        failures = result["correctness"]["final_terminal_failures"]
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]["operation_id"], "telegram-pressure-0000")
        self.assertTrue(failures[0]["segment_id"])
        self.assertEqual(
            failures[0]["reasons"],
            ["terminal_controls_still_visible"],
        )
        self.assertFalse(result["correctness"]["passed"])

    async def test_forged_seal_ack_fails_durable_coordinator_gate(self):
        from tools.pressure_telegram_pipeline import _run
        from telepiplex_plugin_sdk.host_client import HostClient

        original_seal = HostClient.seal_operation_segment

        async def forge_rename_seal(self, operation_id, role, *, deadline=10):
            if role == "rename":
                return {
                    "accepted": True,
                    "segment": {
                        "segment_id": f"forged-{operation_id}-rename",
                        "generation": 1,
                        "state": "sealed",
                    },
                }
            return await original_seal(
                self,
                operation_id,
                role,
                deadline=deadline,
            )

        with patch.object(
            HostClient,
            "seal_operation_segment",
            forge_rename_seal,
        ):
            result = await _run(
                pipelines=1,
                concurrency=1,
                telegram_latency_ms=0,
                busy_latency_ms=0,
                search_latency_ms=0,
                download_latency_ms=0,
                rename_latency_ms=0,
            )

        self.assertEqual(result["correctness"]["sealed_segments"], 3)
        self.assertEqual(result["correctness"]["durably_sealed_segments"], 2)
        self.assertEqual(
            result["correctness"]["operations_without_active_segment"],
            0,
        )
        self.assertFalse(result["correctness"]["passed"])

    async def test_one_pending_task_fails_zero_resource_delta_gate(self):
        from app.handlers import interaction_handler
        from tools.pressure_telegram_pipeline import _run

        original_deliver = interaction_handler.deliver_operation_milestone
        leaked_tasks = []

        async def deliver_and_leak(*args, **kwargs):
            if not leaked_tasks:
                leaked_tasks.append(asyncio.create_task(
                    asyncio.Event().wait(),
                    name="intentional-telegram-pressure-leak",
                ))
            return await original_deliver(*args, **kwargs)

        try:
            with patch.object(
                interaction_handler,
                "deliver_operation_milestone",
                deliver_and_leak,
            ):
                result = await _run(
                    pipelines=1,
                    concurrency=1,
                    telegram_latency_ms=0,
                    busy_latency_ms=0,
                    search_latency_ms=0,
                    download_latency_ms=0,
                    rename_latency_ms=0,
                )
        finally:
            for task in leaked_tasks:
                task.cancel()
            await asyncio.gather(*leaked_tasks, return_exceptions=True)

        self.assertEqual(result["resources"]["tasks"]["final_delta"], 1)
        self.assertEqual(
            result["resources"]["tasks"]["unexpected_final"],
            [{
                "name": "intentional-telegram-pressure-leak",
                "coroutine": "Event.wait",
            }],
        )
        self.assertFalse(result["correctness"]["passed"])

    async def test_replacement_task_leak_fails_identity_resource_gate(self):
        from app.handlers import interaction_handler
        from tools.pressure_telegram_pipeline import (
            SimulatedTelegramRequest,
            _run,
        )

        original_initialize = SimulatedTelegramRequest.initialize
        original_deliver = interaction_handler.deliver_operation_milestone
        baseline_release = asyncio.Event()
        baseline_tasks = []
        leaked_tasks = []

        async def initialize_with_baseline_task(self):
            result = await original_initialize(self)
            baseline_tasks.append(asyncio.create_task(
                baseline_release.wait(),
                name="short-baseline-pressure-task",
            ))
            return result

        async def deliver_and_replace_task(*args, **kwargs):
            if not leaked_tasks:
                leaked_tasks.append(asyncio.create_task(
                    asyncio.Event().wait(),
                    name="replacement-telegram-pressure-leak",
                ))
                baseline_release.set()
            return await original_deliver(*args, **kwargs)

        try:
            with (
                patch.object(
                    SimulatedTelegramRequest,
                    "initialize",
                    initialize_with_baseline_task,
                ),
                patch.object(
                    interaction_handler,
                    "deliver_operation_milestone",
                    deliver_and_replace_task,
                ),
            ):
                result = await _run(
                    pipelines=1,
                    concurrency=1,
                    telegram_latency_ms=0,
                    busy_latency_ms=0,
                    search_latency_ms=0,
                    download_latency_ms=0,
                    rename_latency_ms=0,
                )
        finally:
            baseline_release.set()
            for task in leaked_tasks:
                task.cancel()
            await asyncio.gather(
                *baseline_tasks,
                *leaked_tasks,
                return_exceptions=True,
            )

        self.assertEqual(result["resources"]["tasks"]["final_delta"], 0)
        self.assertEqual(
            result["resources"]["tasks"]["unexpected_final"],
            [{
                "name": "replacement-telegram-pressure-leak",
                "coroutine": "Event.wait",
            }],
        )
        self.assertFalse(result["correctness"]["passed"])

    async def test_false_milestone_drain_result_fails_correctness_gate(self):
        from app.handlers import interaction_handler
        from tools.pressure_telegram_pipeline import _run

        original_drain = interaction_handler.OperationMilestoneSink.drain

        async def drain_then_report_failure(self, *args, **kwargs):
            await original_drain(self, *args, **kwargs)
            return False

        with patch.object(
            interaction_handler.OperationMilestoneSink,
            "drain",
            drain_then_report_failure,
        ):
            result = await _run(
                pipelines=1,
                concurrency=1,
                telegram_latency_ms=0,
                busy_latency_ms=0,
                search_latency_ms=0,
                download_latency_ms=0,
                rename_latency_ms=0,
            )

        self.assertFalse(result["correctness"]["milestone_drain_completed"])
        self.assertFalse(result["correctness"]["passed"])

    async def test_slow_busy_edit_does_not_block_business_pipeline(self):
        from tools.pressure_telegram_pipeline import _run

        result = await _run(
            pipelines=1,
            concurrency=1,
            telegram_latency_ms=0,
            busy_latency_ms=100,
            search_latency_ms=0,
            download_latency_ms=0,
            rename_latency_ms=0,
            duplicate_clicks=1,
        )

        self.assertEqual(result["correctness"]["completed_operations"], 1)
        self.assertEqual(result["correctness"]["callback_dispatches"], 1)
        self.assertEqual(result["correctness"]["failures"], 0)
        self.assertTrue(result["correctness"]["passed"])
        self.assertLess(
            result["latency_ms"]["callback_to_feature_rpc_ms"]["max"],
            80,
        )
        self.assertTrue(
            result["correctness"]["callback_feedback_drain_completed"]
        )
        self.assertEqual(result["telegram"]["outcomes"].get("cancelled", 0), 0)

    async def test_slow_busy_delivery_restores_all_segment_visibility(self):
        from tools.pressure_telegram_pipeline import _run

        result = await _run(
            pipelines=1,
            concurrency=1,
            telegram_latency_ms=0,
            busy_latency_ms=100,
            search_latency_ms=0,
            download_latency_ms=0,
            rename_latency_ms=0,
            duplicate_clicks=1,
            cancelled_busy_late_apply_ms=0,
        )

        self.assertEqual(result["correctness"]["completed_operations"], 1)
        self.assertEqual(result["correctness"]["final_terminal_visible"], 1)
        self.assertEqual(result["correctness"]["final_segment_visible"], 3)
        self.assertEqual(result["correctness"]["final_segment_failures"], [])
        self.assertTrue(
            result["correctness"]["late_telegram_drain_completed"]
        )
        self.assertEqual(
            result["telegram"]["outcomes"].get(
                "cancelled_late_applied",
                0,
            ),
            0,
        )
        self.assertTrue(result["correctness"]["passed"])


if __name__ == "__main__":
    unittest.main()
