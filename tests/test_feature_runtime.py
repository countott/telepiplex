import ast
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import yaml

from telepiplex_plex.feature import PlexFeature
from telepiplex_plex.jobs import PlexJobRepository


ROOT = Path(__file__).resolve().parents[1]


class FakeCore:
    def __init__(self):
        self.notifications = []

    async def notify_user(self, user_id, text, **kwargs):
        self.notifications.append((user_id, text, kwargs))
        return {"accepted": True}


class FakeRuntime:
    def __init__(self):
        self.tasks = {}

    def spawn(self, awaitable, *, task_id):
        self.tasks[task_id] = awaitable


class FakeService:
    def __init__(self, jobs):
        self.jobs = jobs
        self.runs = 0
        self.batches = []

    def enqueue_organized_event(self, payload):
        return self.jobs.create_or_get(
            str(payload.get("job_id") or "job"),
            {
                "user_id": payload.get("user_id"),
                "resource_name": payload.get("resource_name") or "Movie",
                "final_path": payload.get("final_path"),
            },
        )

    def enqueue_organized_event_jobs(self, payload):
        return [self.enqueue_organized_event(payload)]

    def run_job(self, job_id):
        self.runs += 1
        return self.jobs.update(job_id, state="completed")

    def run_batch(self, job_ids):
        self.batches.append(list(job_ids))
        return [self.run_job(job_id) for job_id in job_ids]

    def list_jobs(self, limit=5):
        return self.jobs.list(limit)

    def get_job(self, job_id):
        return self.jobs.get(job_id)


class PlexFeatureRuntimeTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.jobs = PlexJobRepository(self.root / "jobs.db")
        self.service = FakeService(self.jobs)
        self.runtime = FakeRuntime()
        self.feature = PlexFeature(
            config={},
            core=FakeCore(),
            state_path=self.root / "state",
            repository=self.jobs,
            service_factory=lambda: self.service,
        )
        self.feature.bind_runtime(self.runtime)

    async def asyncTearDown(self):
        for awaitable in self.runtime.tasks.values():
            if hasattr(awaitable, "close"):
                awaitable.close()
        self.temp.cleanup()

    async def test_duplicate_media_event_executes_job_once_and_completed_is_terminal(self):
        request = {"payload": {
            "job_id": "job-1",
            "user_id": 123,
            "resource_name": "Movie",
            "final_path": "/Movies/Movie",
        }}
        first = await self.feature.media_organized(request)
        second = await self.feature.media_organized(request)

        self.assertEqual(first["state"], "running")
        self.assertTrue(second["duplicate"])
        self.assertEqual(len(self.runtime.tasks), 1)
        await self.runtime.tasks.pop("plex-batch-job-1")
        third = await self.feature.media_organized(request)
        self.assertEqual(third["state"], "completed")
        self.assertEqual(self.service.runs, 1)
        self.assertEqual(self.service.batches, [[first["job_id"]]])

    async def test_in_progress_job_is_marked_interrupted_then_resumed(self):
        job = self.jobs.create_or_get("old", {"final_path": "/Movies/Old"})
        self.jobs.update(job["id"], state="scanning")
        feature = PlexFeature(
            config={}, core=FakeCore(), state_path=self.root / "state2",
            repository=self.jobs, service_factory=lambda: self.service,
        )
        runtime = FakeRuntime()
        feature.bind_runtime(runtime)
        self.assertEqual(self.jobs.get(job["id"])["state"], "interrupted")

        await runtime.tasks.pop("plex-resume")
        await runtime.tasks.pop("plex-resume-batch")
        self.assertEqual(self.jobs.get(job["id"])["state"], "completed")

    async def test_enabled_ai_with_missing_credentials_does_not_break_feature_startup(self):
        from telepiplex_plex.runtime import main

        config_path = self.root / "config.yaml"
        config_path.write_text(yaml.safe_dump({
            "plex": {"base_url": "", "token": ""},
            "ai": {"enabled": True, "api_url": "", "api_key": "", "model": ""},
        }), encoding="utf-8")
        context = SimpleNamespace(
            manifest={"plugin_id": "plex-management", "version": "1.0.0"},
            token="token",
            core=FakeCore(),
            config_path=config_path,
            state_path=self.root / "runtime-state",
        )
        runtime = main(context)
        self.assertEqual(runtime.state, "starting")
        self.assertIn("media.organized", runtime.events)

    async def test_management_capability_is_read_only_and_whitelisted(self):
        job = self.jobs.create_or_get("visible", {"final_path": "/Movies/Visible"})

        status = await self.feature.management_capability({
            "method": "get_job",
            "payload": {"job_id": job["id"]},
        })
        listing = await self.feature.management_capability({
            "method": "list_jobs",
            "payload": {"limit": 1},
        })

        self.assertEqual(status["job"]["id"], job["id"])
        self.assertEqual(listing["jobs"][0]["id"], job["id"])
        with self.assertRaises(ValueError):
            await self.feature.management_capability({"method": "run_job", "payload": {}})


class FeatureSourceContractTest(unittest.TestCase):
    def test_readme_build_example_uses_current_version(self):
        source = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("dist/plex-management-1.0.2.tpx", source)
        self.assertNotIn("dist/plex-management-1.0.0.tpx", source)

    def test_mcp_uses_auth_token_config_key(self):
        config = yaml.safe_load((ROOT / "config.default.yaml").read_text())

        self.assertIn("auth_token", config["mcp"])
        self.assertNotIn("api_key", config["mcp"])

    def test_source_has_no_core_telegram_or_init_imports(self):
        forbidden = []
        for path in (ROOT / "src").rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names = ([item.name for item in node.names] if isinstance(node, ast.Import)
                         else [node.module] if isinstance(node, ast.ImportFrom) and node.module else [])
                forbidden.extend(name for name in names if name.split(".", 1)[0] in {"app", "init", "telegram"})
        self.assertEqual(forbidden, [])


if __name__ == "__main__":
    unittest.main()
