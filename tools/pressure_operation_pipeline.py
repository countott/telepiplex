#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
import json
from pathlib import Path
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
SDK_SOURCE = ROOT / "sdk/src"
for source in (ROOT, SDK_SOURCE):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Pressure-test the complete search -> download -> rename "
            "operation contract over real Unix RPC."
        )
    )
    parser.add_argument("--pipelines", type=int, default=200)
    parser.add_argument("--concurrency", type=int, default=32)
    parser.add_argument(
        "--milestone-faults",
        type=int,
        default=0,
        help=(
            "Inject this many Host milestone completion interruptions and "
            "verify same-ID recovery over real RPC."
        ),
    )
    return parser.parse_args()


async def _run(
    pipelines: int,
    concurrency: int,
    milestone_faults: int = 0,
) -> dict:
    from telepiplex_plugin_sdk.host_client import HostClient
    from tests.test_operation_pipeline_e2e import (
        OperationPipelineEndToEndTest,
    )

    if pipelines <= 0:
        raise ValueError("pipelines must be positive")
    if concurrency <= 0:
        raise ValueError("concurrency must be positive")
    if milestone_faults < 0:
        raise ValueError("milestone_faults must not be negative")

    harness = OperationPipelineEndToEndTest(
        methodName="test_full_pipeline_ends_at_rename_without_sync_or_plex"
    )
    await harness.asyncSetUp()
    event_deliveries = []
    host_api_calls = 0
    duplicate_milestones = 0
    recovered_milestones = 0
    semaphore = asyncio.Semaphore(concurrency)
    original_complete_milestone = (
        harness.coordinator.complete_milestone_delivery
    )
    remaining_milestone_faults = milestone_faults
    interrupted_milestones = set()
    recovered_milestone_keys = set()

    def complete_then_interrupt(*args):
        nonlocal remaining_milestone_faults
        nonlocal recovered_milestones
        milestone_key = tuple(str(value) for value in args[:3])
        if (
            remaining_milestone_faults > 0
            and milestone_key not in interrupted_milestones
        ):
            interrupted_milestones.add(milestone_key)
            remaining_milestone_faults -= 1
            raise RuntimeError("injected milestone completion interruption")
        result = original_complete_milestone(*args)
        if (
            milestone_key in interrupted_milestones
            and milestone_key not in recovered_milestone_keys
        ):
            recovered_milestone_keys.add(milestone_key)
            recovered_milestones += 1
        return result

    if milestone_faults:
        harness.coordinator.complete_milestone_delivery = (
            complete_then_interrupt
        )

    async def record_event(owner: str, request: dict) -> dict:
        event_deliveries.append((
            owner,
            str(request.get("event_type") or ""),
            str((request.get("payload") or {}).get("operation_id") or ""),
        ))
        return {"accepted": True}

    try:
        search_manifest = harness._manifest(
            "search",
            requires=("download.provider",),
        )
        download_manifest = harness._manifest(
            "download",
            publishes=("download.completed",),
            provides=("download.provider",),
        )
        rename_manifest = harness._manifest(
            "rename",
            subscribes=("download.completed",),
        )
        clients = {
            "search": HostClient(
                harness.broker.socket_path, "search-pressure-token"
            ),
            "download": HostClient(
                harness.broker.socket_path, "download-pressure-token"
            ),
            "rename": HostClient(
                harness.broker.socket_path, "rename-pressure-token"
            ),
        }

        async def report(
            plugin_id: str,
            operation_id: str,
            chat_id: int,
            user_id: int,
            revision: int,
            state: str,
            stage: str,
            *,
            next_plugin_id: str = "",
        ) -> None:
            nonlocal host_api_calls
            response = await clients[plugin_id].report_operation({
                "operation_id": operation_id,
                "chat_id": chat_id,
                "user_id": user_id,
                "state": state,
                "stage": stage,
                "status_text": f"pressure {plugin_id} {stage}",
                "control": "" if state == "completed" else "cancel",
                "revision": revision,
                "next_plugin_id": next_plugin_id,
            })
            host_api_calls += 1
            if response.get("accepted") is not True:
                raise AssertionError(
                    f"operation report rejected: {plugin_id} {stage} {response}"
                )

        async def milestone(
            plugin_id: str,
            operation_id: str,
            milestone_id: str,
            *,
            identity: bool = False,
        ) -> None:
            nonlocal duplicate_milestones
            nonlocal host_api_calls
            nonlocal recovered_milestones
            client = clients[plugin_id]
            if identity:
                call = client.publish_operation_milestone
                kwargs = {
                    "photo_url": "https://img.example/pressure.jpg",
                }
            else:
                call = client.seal_operation_stage
                kwargs = {}
            first = await call(
                operation_id,
                milestone_id,
                f"pressure milestone {milestone_id}",
                **kwargs,
            )
            host_api_calls += 1
            replay = await call(
                operation_id,
                milestone_id,
                f"pressure milestone {milestone_id}",
                **kwargs,
            )
            host_api_calls += 1
            if first.get("accepted") is not True:
                raise AssertionError(
                    f"first milestone delivery was not accepted: {first}"
                )
            if replay.get("duplicate") is not True:
                raise AssertionError(
                    f"milestone replay was not idempotent: {replay}"
                )
            duplicate_milestones += 1

        async def publish_event(
            plugin_id: str,
            event_type: str,
            operation_id: str,
            revision: int,
        ) -> None:
            nonlocal host_api_calls
            response = await clients[plugin_id].publish_event(
                event_type,
                {
                    "operation_id": operation_id,
                    "operation_revision": revision,
                },
                idempotency_key=f"{operation_id}:{event_type}",
            )
            host_api_calls += 1
            if not response.get("event_id"):
                raise AssertionError(
                    f"event publication returned no event_id: {response}"
                )

        async def download_capability(request: dict) -> dict:
            payload = request["payload"]
            operation_id = str(payload["operation_id"])
            chat_id = int(payload["chat_id"])
            user_id = int(payload["user_id"])
            index = int(payload["pressure_index"])
            await report(
                "download", operation_id, chat_id, user_id,
                5, "running", "downloading",
            )
            await report(
                "download", operation_id, chat_id, user_id,
                6, "handed_off", "handoff_rename",
                next_plugin_id="rename",
            )
            await milestone(
                "download", operation_id, f"download-stage:{index}"
            )
            await publish_event(
                "download", "download.completed", operation_id, 6
            )
            return {"accepted": True}

        async def rename_event(request: dict) -> dict:
            await record_event("rename", request)
            payload = request["payload"]
            operation_id = str(payload["operation_id"])
            index = int(operation_id.rsplit("-", 1)[-1])
            record = harness.coordinator.get(operation_id)
            await report(
                "rename", operation_id, record.chat_id, record.user_id,
                7, "running", "organizing",
            )
            await report(
                "rename", operation_id, record.chat_id, record.user_id,
                8, "completed", "completed",
            )
            await milestone(
                "rename", operation_id, f"rename-stage:{index}"
            )
            return {"accepted": True}

        await harness._start_runtime(
            download_manifest,
            "download-pressure-token",
            capabilities={"download.provider": download_capability},
        )
        await harness._start_runtime(
            rename_manifest,
            "rename-pressure-token",
            events={"download.completed": rename_event},
        )
        await harness._start_runtime(
            search_manifest,
            "search-pressure-token",
        )

        async def pipeline(index: int) -> None:
            nonlocal host_api_calls
            async with semaphore:
                operation_id = f"pressure-operation-{index:04d}"
                chat_id = 10_000 + index
                user_id = 20_000 + index
                await report(
                    "search", operation_id, chat_id, user_id,
                    1, "awaiting_input", "confirmation",
                )
                await report(
                    "search", operation_id, chat_id, user_id,
                    2, "running", "identity_confirmation",
                )
                await milestone(
                    "search",
                    operation_id,
                    f"identity:{index}",
                    identity=True,
                )
                await report(
                    "search", operation_id, chat_id, user_id,
                    3, "running", "prowlarr_search",
                )
                await report(
                    "search", operation_id, chat_id, user_id,
                    4, "handed_off", "handoff_download",
                    next_plugin_id="download",
                )
                await milestone(
                    "search", operation_id, f"search-stage:{index}"
                )
                response = await clients["search"].call_capability(
                    "download.provider",
                    "submit",
                    {
                        "operation_id": operation_id,
                        "chat_id": chat_id,
                        "user_id": user_id,
                        "pressure_index": index,
                    },
                    idempotency_key=f"{operation_id}:download-submit",
                )
                host_api_calls += 1
                if response.get("accepted") is not True:
                    raise AssertionError(
                        f"download capability rejected: {response}"
                    )
                async with asyncio.timeout(10):
                    while True:
                        record = harness.coordinator.get(operation_id)
                        if record is not None and record.state == "completed":
                            break
                        await asyncio.sleep(0.01)

        started = time.perf_counter()
        results = await asyncio.gather(
            *(pipeline(index) for index in range(pipelines)),
            return_exceptions=True,
        )
        failures = [result for result in results if isinstance(result, Exception)]
        if failures:
            raise AssertionError(
                f"{len(failures)} pipeline(s) failed; first={failures[0]!r}; "
                f"event_deliveries={len(event_deliveries)}; "
                f"host_api_calls={host_api_calls}"
            )
        expected_milestone_deliveries = pipelines * 4
        expected_faults = min(milestone_faults, expected_milestone_deliveries)
        expected_host_api_calls = pipelines * 18
        async with asyncio.timeout(10):
            while duplicate_milestones != expected_milestone_deliveries:
                await asyncio.sleep(0.01)
        await harness.milestone_sink.drain()
        elapsed = time.perf_counter() - started

        expected_operations = {
            f"pressure-operation-{index:04d}" for index in range(pipelines)
        }
        delivered_by_owner = Counter(
            owner for owner, _, _ in event_deliveries
        )
        delivered_operations = {
            operation_id for _, _, operation_id in event_deliveries
        }
        final_records = [
            harness.coordinator.get(operation_id)
            for operation_id in expected_operations
        ]
        completed = sum(
            record is not None
            and record.plugin_id == "rename"
            and record.state == "completed"
            for record in final_records
        )
        terminal_owners = sorted({
            record.plugin_id
            for record in final_records
            if record is not None and record.state == "completed"
        })
        if host_api_calls != expected_host_api_calls:
            raise AssertionError(
                f"host API call count {host_api_calls} != {expected_host_api_calls}"
            )
        if len(harness.milestone_deliveries) != expected_milestone_deliveries:
            raise AssertionError(
                "milestone delivery count "
                f"{len(harness.milestone_deliveries)} "
                f"!= {expected_milestone_deliveries}"
            )
        if duplicate_milestones != expected_milestone_deliveries:
            raise AssertionError("not every milestone replay was deduplicated")
        if recovered_milestones != expected_faults:
            raise AssertionError(
                "milestone recovery count "
                f"{recovered_milestones} != {expected_faults}"
            )
        if remaining_milestone_faults != milestone_faults - expected_faults:
            raise AssertionError("unexpected milestone fault injection count")
        if delivered_by_owner != Counter({
            "rename": pipelines,
        }):
            raise AssertionError(
                f"unexpected event deliveries: {dict(delivered_by_owner)}"
            )
        if delivered_operations != expected_operations:
            raise AssertionError("event operation identities were lost or mixed")
        if completed != pipelines:
            raise AssertionError(
                f"completed operation count {completed} != {pipelines}"
            )

        return {
            "pipelines": pipelines,
            "concurrency": concurrency,
            "host_api_calls": host_api_calls,
            "milestone_requests": pipelines * 8,
            "milestone_deliveries": len(harness.milestone_deliveries),
            "duplicate_milestones": duplicate_milestones,
            "injected_milestone_faults": expected_faults,
            "recovered_milestones": recovered_milestones,
            "event_deliveries": len(event_deliveries),
            "event_types": sorted({
                event_type for _, event_type, _ in event_deliveries
            }),
            "completed_operations": completed,
            "terminal_owners": terminal_owners,
            "failures": 0,
            "elapsed_seconds": round(elapsed, 3),
            "pipelines_per_second": round(pipelines / elapsed, 2),
        }
    finally:
        await harness.asyncTearDown()


def main() -> None:
    args = _parse_args()
    result = asyncio.run(_run(
        args.pipelines,
        args.concurrency,
        args.milestone_faults,
    ))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
