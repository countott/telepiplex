import unittest


class FakeHost:
    def __init__(self, *, current_id="file-1", original_id=None):
        self.files = {"/Downloads/renamed.mkv": current_id}
        if original_id:
            self.files["/Downloads/original.mkv"] = original_id
        self.calls = []

    async def call_capability(self, capability, method, payload, **_kwargs):
        self.calls.append((capability, method, payload))
        args = payload.get("args") or []
        if method == "get_file_info":
            file_id = self.files.get(args[0])
            return {"value": {"file_id": file_id} if file_id else None}
        if method == "rename":
            source, name = args
            file_id = self.files.pop(source, None)
            target = source.rsplit("/", 1)[0] + "/" + name
            if file_id:
                self.files[target] = file_id
            return {"value": bool(file_id)}
        raise AssertionError(method)


class RenameOperationJournalTest(unittest.IsolatedAsyncioTestCase):
    async def test_verified_rename_inverse_restores_original_path(self):
        from telepiplex_rename.operations import RenameOperationJournal

        journal = RenameOperationJournal()
        self.assertTrue(journal.record_rename(
            source_path="/Downloads/original.mkv",
            target_path="/Downloads/renamed.mkv",
            source_id="file-1",
            target_id="file-1",
        ))

        outcome = await journal.rollback(FakeHost())

        self.assertEqual(outcome["state"], "rolled_back")
        self.assertEqual(outcome["restored"], ["/Downloads/original.mkv"])
        self.assertEqual(outcome["remaining"], [])

    async def test_identity_conflict_stops_without_guessing_inverse(self):
        from telepiplex_rename.operations import RenameOperationJournal

        journal = RenameOperationJournal()
        journal.record_rename(
            source_path="/Downloads/original.mkv",
            target_path="/Downloads/renamed.mkv",
            source_id="file-1",
            target_id="file-1",
        )
        host = FakeHost(current_id="different-file")

        outcome = await journal.rollback(host)

        self.assertEqual(outcome["state"], "partially_rolled_back")
        self.assertEqual(outcome["restored"], [])
        self.assertEqual(outcome["remaining"], ["/Downloads/renamed.mkv"])
        self.assertFalse(any(method == "rename" for _, method, _ in host.calls))

    async def test_original_path_conflict_stops_without_mutation(self):
        from telepiplex_rename.operations import RenameOperationJournal

        journal = RenameOperationJournal()
        journal.record_rename(
            source_path="/Downloads/original.mkv",
            target_path="/Downloads/renamed.mkv",
            source_id="file-1",
            target_id="file-1",
        )
        host = FakeHost(original_id="other-file")

        outcome = await journal.rollback(host)

        self.assertEqual(outcome["state"], "partially_rolled_back")
        self.assertEqual(outcome["restored"], [])
        self.assertEqual(outcome["remaining"], ["/Downloads/renamed.mkv"])
        self.assertFalse(any(method == "rename" for _, method, _ in host.calls))
