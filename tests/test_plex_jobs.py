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
        from telepiplex_plex.jobs import PlexJobRepository

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
            state="matching",
            rating_key="42",
            step_results={"scanning": {"status": "success"}},
            error="temporary",
        )

        self.assertEqual(updated["state"], "matching")
        self.assertEqual(updated["rating_key"], "42")
        self.assertEqual(updated["step_results"]["scanning"]["status"], "success")
        self.assertEqual(self.repo.get(job["id"])["error"], "temporary")

    def test_confirmation_token_is_single_use(self):
        job = self.repo.create_or_get("key", {"final_path": "/x"})
        token = self.repo.issue_confirmation(
            job["id"],
            "fix_match",
            {"rating_key": "42"},
        )

        payload = self.repo.consume_confirmation(token, "fix_match")

        self.assertEqual(payload["job_id"], job["id"])
        self.assertEqual(payload["rating_key"], "42")
        self.assertIsNone(self.repo.consume_confirmation(token, "fix_match"))

    def test_list_returns_newest_jobs_first(self):
        first = self.repo.create_or_get("first", {"final_path": "/first"})
        second = self.repo.create_or_get("second", {"final_path": "/second"})

        jobs = self.repo.list(limit=2)

        self.assertEqual([job["id"] for job in jobs], [second["id"], first["id"]])

    def test_expired_confirmation_token_is_rejected(self):
        from telepiplex_plex.jobs import PlexJobRepository

        now = [100.0]
        repo = PlexJobRepository(
            Path(self.temp_dir.name) / "expiry.db",
            clock=lambda: now[0],
        )
        job = repo.create_or_get("key", {"final_path": "/x"})
        token = repo.issue_confirmation(job["id"], "fix_match", {}, ttl_seconds=10)
        now[0] = 111.0

        self.assertIsNone(repo.consume_confirmation(token, "fix_match"))

    def test_claim_prevents_duplicate_execution_and_completed_never_reopens(self):
        job = self.repo.create_or_get("key", {"final_path": "/x"})
        self.assertTrue(self.repo.claim(job["id"]))
        self.assertFalse(self.repo.claim(job["id"]))
        self.repo.update(job["id"], state="completed")
        self.assertFalse(self.repo.claim(job["id"]))

    def test_restart_marks_in_progress_jobs_interrupted(self):
        job = self.repo.create_or_get("key", {"final_path": "/x"})
        self.repo.update(job["id"], state="scanning")

        interrupted = self.repo.mark_incomplete_interrupted()

        self.assertEqual(interrupted, [job["id"]])
        self.assertEqual(self.repo.get(job["id"])["state"], "interrupted")


if __name__ == "__main__":
    unittest.main()
