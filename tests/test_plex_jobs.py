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
        from app.repositories.plex_jobs import PlexJobRepository

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

    def test_create_or_get_with_status_marks_only_first_insert_created(self):
        first, first_created = self.repo.create_or_get_with_status(
            "115:/Movies/Cars",
            {"final_path": "/Movies/Cars"},
        )
        second, second_created = self.repo.create_or_get_with_status(
            "115:/Movies/Cars",
            {"final_path": "/Movies/Cars"},
        )

        self.assertTrue(first_created)
        self.assertFalse(second_created)
        self.assertEqual(first["id"], second["id"])

    def test_mark_active_interrupted_leaves_terminal_and_waiting_jobs_unchanged(self):
        active_states = [
            "queued",
            "scanning",
            "locating",
            "matching",
            "localizing",
            "artwork",
            "streams",
        ]
        active_jobs = []
        for state in active_states:
            job = self.repo.create_or_get(f"active-{state}", {})
            active_jobs.append(self.repo.update(job["id"], state=state))
        preserved = []
        for state in ("completed", "failed", "waiting_match_confirmation", "interrupted"):
            job = self.repo.create_or_get(f"preserved-{state}", {})
            preserved.append(self.repo.update(job["id"], state=state))

        changed = self.repo.mark_active_interrupted("process_restarted")

        self.assertEqual(changed, len(active_states))
        for job in active_jobs:
            current = self.repo.get(job["id"])
            self.assertEqual(current["state"], "interrupted")
            self.assertEqual(current["error"], "process_restarted")
        for job in preserved:
            self.assertEqual(self.repo.get(job["id"])["state"], job["state"])

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
        from app.repositories.plex_jobs import PlexJobRepository

        now = [100.0]
        repo = PlexJobRepository(
            Path(self.temp_dir.name) / "expiry.db",
            clock=lambda: now[0],
        )
        job = repo.create_or_get("key", {"final_path": "/x"})
        token = repo.issue_confirmation(job["id"], "fix_match", {}, ttl_seconds=10)
        now[0] = 111.0

        self.assertIsNone(repo.consume_confirmation(token, "fix_match"))


if __name__ == "__main__":
    unittest.main()
