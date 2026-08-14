from telepiplex_rename.file_facts import build_file_facts, parse_file_evidence
from telepiplex_rename.file_plan import plan_file_resolutions


def _inputs(*nodes):
    facts = build_file_facts(
        list(nodes),
        root_path="/Downloads",
        provider="115",
        snapshot_id="snapshot",
    )
    return facts, {fact.source_id: parse_file_evidence(fact) for fact in facts}


def _confirmed(*source_ids):
    return {
        source_id: {"source": "tvdb", "external_id": "123"}
        for source_id in source_ids
    }


def test_action_is_derived_from_normalized_source_and_target_paths():
    facts, evidence = _inputs(
        {"file_id": "noop", "path": "/TV/Veep/Veep S07E01.mkv"},
        {"file_id": "rename", "path": "/TV/Veep/old.mkv"},
        {"file_id": "move", "path": "/Downloads/Veep S07E02.mkv"},
        {"file_id": "both", "path": "/Downloads/old-03.mkv"},
    )
    targets = {
        "noop": "/TV/Veep/Veep S07E01.mkv",
        "rename": "/TV/Veep/Veep S07E04.mkv",
        "move": "/TV/Veep/Veep S07E02.mkv",
        "both": "/TV/Veep/Veep S07E03.mkv",
    }

    resolutions = plan_file_resolutions(
        facts,
        evidence,
        targets,
        _confirmed("noop", "rename", "move", "both"),
    )

    assert {item.source_id: item.action for item in resolutions} == {
        "noop": "no_op",
        "rename": "rename_only",
        "move": "move_only",
        "both": "rename_and_move",
    }


def test_existing_same_provider_identity_is_no_op():
    facts, evidence = _inputs({
        "file_id": "source",
        "path": "/Downloads/Veep.S07E01.mkv",
    })
    target = "/TV/Veep/Veep S07E01.mkv"

    resolution = plan_file_resolutions(
        facts,
        evidence,
        {"source": target},
        _confirmed("source"),
        existing_targets={target: {"file_id": "source"}},
    )[0]

    assert resolution.status == "resolved"
    assert resolution.action == "no_op"
    assert resolution.target_path == target
    assert resolution.reason_codes == ("target_same_provider_identity",)


def test_existing_different_identity_conflicts_only_that_file():
    facts, evidence = _inputs(
        {"file_id": "conflict", "path": "/Downloads/old-01.mkv"},
        {"file_id": "safe", "path": "/Downloads/old-02.mkv"},
    )
    conflict_target = "/TV/Veep/Veep S07E01.mkv"
    safe_target = "/TV/Veep/Veep S07E02.mkv"

    resolutions = plan_file_resolutions(
        facts,
        evidence,
        {"conflict": conflict_target, "safe": safe_target},
        _confirmed("conflict", "safe"),
        existing_targets={conflict_target: {"file_id": "someone-else"}},
    )

    assert {item.source_id: (item.status, item.action) for item in resolutions} == {
        "conflict": ("ambiguous", "keep_original"),
        "safe": ("resolved", "rename_and_move"),
    }
    assert resolutions[0].reason_codes == ("target_conflict",)


def test_same_hash_with_distinct_identity_never_authorizes_source_deletion():
    facts, evidence = _inputs({
        "file_id": "source",
        "path": "/Downloads/old.mkv",
        "sha1": "same-hash",
    })
    target = "/TV/Veep/Veep S07E01.mkv"

    resolution = plan_file_resolutions(
        facts,
        evidence,
        {"source": target},
        _confirmed("source"),
        existing_targets={
            target: {"file_id": "other", "sha1": "same-hash"},
        },
    )[0]

    assert resolution.action == "keep_original"
    assert resolution.reason_codes == (
        "target_conflict",
        "duplicate_hash_distinct_identity",
    )


def test_two_sources_claiming_same_target_are_both_preserved():
    facts, evidence = _inputs(
        {"file_id": "edition-a", "path": "/Downloads/a.mkv"},
        {"file_id": "edition-b", "path": "/Downloads/b.mkv"},
    )
    target = "/Movies/Movie/Movie.mkv"

    resolutions = plan_file_resolutions(
        facts,
        evidence,
        {"edition-a": target, "edition-b": target},
        _confirmed("edition-a", "edition-b"),
    )

    assert all(item.action == "keep_original" for item in resolutions)
    assert all(item.reason_codes == ("planned_target_collision",) for item in resolutions)


def test_non_media_and_unresolved_subtitle_do_not_enter_mutation_plan():
    facts, evidence = _inputs(
        {"file_id": "readme", "path": "/Downloads/README.txt"},
        {"file_id": "subtitle", "path": "/Downloads/Veep.S07E01.srt"},
    )

    resolutions = plan_file_resolutions(
        facts,
        evidence,
        {},
        {},
    )

    assert {item.source_id: (item.status, item.action, item.reason_codes) for item in resolutions} == {
        "readme": ("unsupported", "keep_original", ("non_media",)),
        "subtitle": (
            "ambiguous",
            "keep_original",
            ("work_identity_unresolved", "target_unresolved"),
        ),
    }
