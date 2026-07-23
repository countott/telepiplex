import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))


class PlexJobRepositoryTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        from telepiplex_sync.jobs import PlexJobRepository

        self.repo = PlexJobRepository(Path(self.temp_dir.name) / "plex.db")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_create_or_get_deduplicates_jobs(self):
        first = self.repo.create_or_get(
            "115:/Movies/Cars",
            {"final_path": "/Movies/Cars"},
        )
        second = self.repo.create_or_get(
            "115:/Movies/Cars",
            {"final_path": "/Movies/Cars"},
        )

        self.assertEqual(first["id"], second["id"])
        self.assertEqual(first["state"], "queued")
        self.assertEqual(first["payload"]["final_path"], "/Movies/Cars")

    def test_update_persists_state_rating_key_results_and_error(self):
        job = self.repo.create_or_get("key", {"final_path": "/x"})

        updated = self.repo.update(
            job["id"],
            state="artwork",
            rating_key="42",
            step_results={"scanning": {"status": "success"}},
            error="temporary",
        )

        self.assertEqual(updated["state"], "artwork")
        self.assertEqual(updated["rating_key"], "42")
        self.assertEqual(updated["step_results"]["scanning"]["status"], "success")
        self.assertEqual(self.repo.get(job["id"])["error"], "temporary")

    def test_confirmation_token_is_single_use(self):
        job = self.repo.create_or_get("key", {"final_path": "/x"})
        token = self.repo.issue_confirmation(
            job["id"],
            "plex_scan_library",
            {"rating_key": "42"},
        )

        payload = self.repo.consume_confirmation(token, "plex_scan_library")

        self.assertEqual(payload["job_id"], job["id"])
        self.assertEqual(payload["rating_key"], "42")
        self.assertIsNone(
            self.repo.consume_confirmation(token, "plex_scan_library")
        )

    def test_list_returns_newest_jobs_first(self):
        first = self.repo.create_or_get("first", {"final_path": "/first"})
        second = self.repo.create_or_get("second", {"final_path": "/second"})

        jobs = self.repo.list(limit=2)

        self.assertEqual([job["id"] for job in jobs], [second["id"], first["id"]])

    def test_list_for_owner_filters_persisted_owner_before_limit(self):
        owner_old = self.repo.create_or_get(
            "owner-old",
            {"chat_id": 10, "user_id": 1, "final_path": "/owner-old"},
        )
        self.repo.create_or_get(
            "other",
            {"chat_id": 99, "user_id": 2, "final_path": "/other"},
        )
        owner_new = self.repo.create_or_get(
            "owner-new",
            {"chat_id": 10, "user_id": 1, "final_path": "/owner-new"},
        )

        jobs = self.repo.list_for_owner(10, 1, limit=2)

        self.assertEqual(
            [job["id"] for job in jobs],
            [owner_new["id"], owner_old["id"]],
        )

    def test_expired_confirmation_token_is_rejected(self):
        from telepiplex_sync.jobs import PlexJobRepository

        now = [100.0]
        repo = PlexJobRepository(
            Path(self.temp_dir.name) / "expiry.db",
            clock=lambda: now[0],
        )
        job = repo.create_or_get("key", {"final_path": "/x"})
        token = repo.issue_confirmation(
            job["id"],
            "plex_scan_library",
            {},
            ttl_seconds=10,
        )
        now[0] = 111.0

        self.assertIsNone(
            repo.consume_confirmation(token, "plex_scan_library")
        )

    def test_claim_prevents_duplicate_execution_and_completed_never_reopens(self):
        job = self.repo.create_or_get("key", {"final_path": "/x"})
        self.assertTrue(self.repo.claim(job["id"]))
        self.assertFalse(self.repo.claim(job["id"]))
        self.repo.update(job["id"], state="completed")
        self.assertFalse(self.repo.claim(job["id"]))

    def test_retry_claim_is_atomic_and_limited_to_retryable_terminal_states(self):
        for state in ("failed", "interrupted", "cancelled"):
            with self.subTest(state=state):
                job = self.repo.create_or_get(
                    f"retryable-{state}",
                    {"final_path": f"/{state}"},
                )
                self.repo.update(job["id"], state=state)

                self.assertTrue(self.repo.claim_retry(job["id"]))
                self.assertEqual(self.repo.get(job["id"])["state"], "running")
                self.assertFalse(self.repo.claim_retry(job["id"]))

        for state in (
            "queued",
            "running",
            "scanning",
            "artwork",
            "audio",
            "subtitle",
            "awaiting_selection",
            "completed",
        ):
            with self.subTest(state=state):
                job = self.repo.create_or_get(
                    f"not-retryable-{state}",
                    {"final_path": f"/{state}"},
                )
                self.repo.update(job["id"], state=state)

                self.assertFalse(self.repo.claim_retry(job["id"]))
                self.assertEqual(self.repo.get(job["id"])["state"], state)

    def test_restart_interrupts_current_and_retired_in_progress_states(self):
        active_states = (
            "running",
            "scanning",
            "artwork",
            "audio",
            "subtitle",
            "retired_stage",
        )
        active_jobs = []
        for state in active_states:
            job = self.repo.create_or_get(state, {"final_path": f"/{state}"})
            self.repo.update(job["id"], state=state)
            active_jobs.append(job)
        stable_jobs = {}
        for state in (
            "queued",
            "awaiting_selection",
            "completed",
            "failed",
            "interrupted",
            "cancelled",
        ):
            job = self.repo.create_or_get(
                f"stable-{state}",
                {"final_path": f"/{state}"},
            )
            self.repo.update(job["id"], state=state)
            stable_jobs[state] = job

        interrupted = self.repo.mark_incomplete_interrupted()

        self.assertEqual(interrupted, [job["id"] for job in active_jobs])
        self.assertTrue(all(
            self.repo.get(job["id"])["state"] == "interrupted"
            for job in active_jobs
        ))
        for state, job in stable_jobs.items():
            with self.subTest(state=state):
                self.assertEqual(self.repo.get(job["id"])["state"], state)


if __name__ == "__main__":
    unittest.main()
