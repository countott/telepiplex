from copy import deepcopy
from types import SimpleNamespace

from telepiplex_rename.service import RenameFeature


def test_only_successful_complete_rename_offers_next_actions_and_receipt_replay_is_stable():
    for organized, cleanup, partial, expected in ((True, True, False, True), (False, True, False, False),
                                                 (True, False, False, False), (True, True, True, False)):
        feature = RenameFeature(config={}, host=SimpleNamespace())
        operation = feature._new_operation({"chat_id": 10, "user_id": 1}, state="running",
            stage="organizing", status_text="整理中", control="cancel", kind="organization")
        outcome = {"organized": organized, "cleanup_complete": cleanup, "partial_completed": partial,
                   "final_path": "/TV/Show", "event_payload": {"media_metadata": {"schema_version": 2}},
                   "file_results": {"media_files_total": 1}}
        feature._prepare_terminal_outcome("job", outcome, operation["operation_id"])
        report = outcome["terminal_operation_report"]
        assert ("next_actions" in report["details"]) is expected
        saved = deepcopy(report)
        feature._prepare_terminal_outcome("job", outcome, operation["operation_id"])
        assert outcome["terminal_operation_report"] == saved
        assert report["details"]["effect_receipt"]["state"] == ("completed" if organized and cleanup else "failed")
