from telepiplex_rename.file_facts import (
    build_file_facts,
    parse_file_evidence,
)
from telepiplex_rename.content_probe import build_metadata_probe


def _facts(*nodes):
    return build_file_facts(
        list(nodes),
        root_path="/Downloads/乱序根目录",
        provider="115",
        snapshot_id="snapshot-1",
    )


def test_veep_year_is_qualifier_not_part_of_title_key():
    video, subtitle = _facts(
        {
            "file_id": "video-1",
            "relative_path": "wrong/Veep.S07E01.mkv",
            "name": "Veep.S07E01.mkv",
            "is_dir": False,
        },
        {
            "file_id": "subtitle-1",
            "relative_path": "also-wrong/Veep (2012) S07E01.chs.srt",
            "name": "Veep (2012) S07E01.chs.srt",
            "is_dir": False,
        },
    )

    video_evidence = parse_file_evidence(video)
    subtitle_evidence = parse_file_evidence(subtitle)

    assert video_evidence.title_key == "veep"
    assert subtitle_evidence.title_key == "veep"
    assert video_evidence.year_hint is None
    assert subtitle_evidence.year_hint == 2012
    assert video_evidence.directory_hints == ("wrong",)
    assert subtitle_evidence.directory_hints == ("also-wrong",)


def test_release_year_and_source_tag_are_not_part_of_title_identity():
    fact = _facts({
        "file_id": "raw-movie",
        "relative_path": "任意目录/Raw.Movie.2024.Source.mkv",
        "is_dir": False,
    })[0]

    evidence = parse_file_evidence(fact)

    assert evidence.title_candidates == ("Raw Movie",)
    assert evidence.title_key == "rawmovie"
    assert evidence.year_hint == 2024


def test_video_and_subtitle_keep_independent_filename_identity():
    video, subtitle = _facts(
        {
            "file_id": "v",
            "relative_path": "Mixed/Veep.S07E01.mkv",
            "is_dir": False,
        },
        {
            "file_id": "s",
            "relative_path": "Mixed/Honey.and.Clover.S01E01.eng.ass",
            "is_dir": False,
        },
    )

    video_evidence = parse_file_evidence(video)
    subtitle_evidence = parse_file_evidence(subtitle)

    assert video_evidence.title_candidates == ("Veep",)
    assert subtitle_evidence.title_candidates == ("Honey and Clover",)
    assert video_evidence.title_key == "veep"
    assert subtitle_evidence.title_key == "honeyandclover"


def test_honey_and_clover_dash_episode_is_file_level_episode_evidence():
    fact = _facts({
        "file_id": "honey-1",
        "relative_path": "混乱/Honey and Clover S1 - 01.mkv",
        "is_dir": False,
    })[0]

    evidence = parse_file_evidence(fact)

    assert evidence.title_candidates == ("Honey and Clover",)
    assert evidence.season_number == 1
    assert evidence.episode_number == 1
    assert evidence.confidence == "high"


def test_source_id_prefers_provider_id_and_fallback_is_stable():
    facts = _facts(
        {
            "file_id": "provider-file-id",
            "relative_path": "A/Veep.mkv",
            "is_dir": False,
        },
        {
            "relative_path": "B/Veep.mkv",
            "is_dir": False,
        },
        {
            "relative_path": "B/Veep.mkv",
            "is_dir": False,
        },
    )

    assert facts[0].source_id == "provider-file-id"
    assert facts[1].source_id == facts[2].source_id
    assert facts[1].source_id.startswith("path:")


def test_non_media_node_is_retained_without_mutation_identity():
    fact = _facts({
        "file_id": "readme",
        "relative_path": "Release/README.txt",
        "size": 12,
        "is_dir": False,
    })[0]

    evidence = parse_file_evidence(fact)

    assert fact.media_kind == "non_media"
    assert fact.size == 12
    assert evidence.title_candidates == ()
    assert evidence.title_key == ""
    assert evidence.content_role == "unknown"


def test_legacy_probe_exposes_video_and_subtitle_file_evidence():
    probe = build_metadata_probe({
        "provider": "115",
        "download_root": "/Downloads/Raw.Release",
        "file_tree": [{
            "file_id": "video",
            "relative_path": "Video/Veep.S07E01.mkv",
            "is_dir": False,
        }, {
            "file_id": "subtitle",
            "relative_path": "Subs/Honey.and.Clover.S01E01.eng.ass",
            "is_dir": False,
        }],
    })

    assert [item["source_id"] for item in probe["file_evidence"]] == [
        "video",
        "subtitle",
    ]
    assert [item["title_key"] for item in probe["file_evidence"]] == [
        "veep",
        "honeyandclover",
    ]


def test_legacy_probe_does_not_conflict_on_missing_versus_explicit_year():
    probe = build_metadata_probe({
        "provider": "115",
        "download_root": "/Downloads/Raw.Release",
        "file_tree": [{
            "file_id": "video",
            "relative_path": "Video/Veep.S07E01.mkv",
            "is_dir": False,
        }, {
            "file_id": "subtitle",
            "relative_path": "Subs/Veep (2012) S07E01.chs.srt",
            "is_dir": False,
        }],
    })

    assert probe["identity_query"] == "Veep"
    assert "identity_conflict" not in probe["recovery_reasons"]
    assert {item["year_hint"] for item in probe["file_evidence"]} == {
        None,
        2012,
    }
