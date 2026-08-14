from telepiplex_rename.subtitles import (
    build_movie_subtitle_plan,
    build_series_subtitle_plan,
    collect_subtitle_evidence,
)


def _series_plan(tree):
    return build_series_subtitle_plan(
        final_path="/Unsorted/Show",
        target_root="/TV/Show",
        series_name="Show",
        file_tree=tree,
        allowed_targets={(1, 1)},
    )


def test_known_subtitle_languages_are_all_preserved_with_iso_639_2b_suffix():
    markers = [
        ("CHS", "chi"),
        ("CHT", "chi"),
        ("CHS&ENG", "chi"),
        ("ENG", "eng"),
        ("JPN", "jpn"),
        ("KOR", "kor"),
        ("FRA", "fre"),
        ("DEU", "ger"),
        ("SPA", "spa"),
        ("ITA", "ita"),
        ("RUS", "rus"),
        ("ARA", "ara"),
        ("THA", "tha"),
        ("VIE", "vie"),
    ]
    tree = [{
        "file_id": f"source-{index:02d}",
        "relative_path": f"Show.S01E01.{marker}.{index:02d}.srt",
        "is_dir": False,
    } for index, (marker, _code) in enumerate(markers)]

    plan = _series_plan(tree)

    assert len(plan["operations"]) == len(markers)
    assert sorted(item["language_code"] for item in plan["operations"]) == sorted(
        code for _marker, code in markers
    )
    assert plan["discard_sources"] == []
    assert plan["kept_sources"] == []
    assert plan["unresolved_sources"] == []


def test_forced_sdh_and_cc_markers_are_removed_from_target_name():
    plan = _series_plan([{
        "file_id": "english-forced",
        "relative_path": "Show.S01E01.ENG.forced.sdh.cc.ass",
        "is_dir": False,
    }])

    operation = plan["operations"][0]
    assert operation["rename_to"] == "Show S01E01.eng.ass"
    assert operation["language_code"] == "eng"
    assert "forced" not in operation["rename_to"].casefold()
    assert "sdh" not in operation["rename_to"].casefold()
    assert "cc" not in operation["rename_to"].casefold()


def test_duplicate_language_and_extension_get_stable_variant_names():
    tree = [{
        "file_id": "source-b",
        "relative_path": "B/Show.S01E01.CHS.srt",
        "is_dir": False,
    }, {
        "file_id": "source-a",
        "relative_path": "A/Show.S01E01.CHT.srt",
        "is_dir": False,
    }, {
        "file_id": "source-c",
        "relative_path": "C/Show.S01E01.CHS&ENG.srt",
        "is_dir": False,
    }]

    forward = _series_plan(tree)
    reverse = _series_plan(list(reversed(tree)))

    assert {
        item["source_id"]: item["rename_to"]
        for item in forward["operations"]
    } == {
        "source-a": "Show S01E01.chi.srt",
        "source-b": "Show S01E01.variant-02.chi.srt",
        "source-c": "Show S01E01.variant-03.chi.srt",
    }
    assert {
        item["source_id"]: item["rename_to"]
        for item in reverse["operations"]
    } == {
        item["source_id"]: item["rename_to"]
        for item in forward["operations"]
    }


def test_unknown_language_and_missing_episode_stay_in_place_independently():
    plan = _series_plan([{
        "file_id": "unknown-language",
        "relative_path": "Show.S01E01.commentary.srt",
        "is_dir": False,
    }, {
        "file_id": "missing-episode",
        "relative_path": "Show.CHS.ass",
        "is_dir": False,
    }, {
        "file_id": "resolved",
        "relative_path": "Show.S01E01.ENG.srt",
        "is_dir": False,
    }])

    assert [item["source_id"] for item in plan["operations"]] == ["resolved"]
    assert plan["kept_sources"] == [
        "Show.CHS.ass",
        "Show.S01E01.commentary.srt",
    ]
    assert plan["discard_sources"] == []
    assert plan["unresolved_sources"] == []


def test_movie_subtitle_duplicates_keep_real_extensions_and_variants():
    plan = build_movie_subtitle_plan(
        final_path="/Unsorted/Movie",
        target_dir="/Movies/Movie",
        target_stem="Movie",
        file_tree=[{
            "file_id": "ass",
            "relative_path": "Movie.CHS.ass",
            "is_dir": False,
        }, {
            "file_id": "srt-a",
            "relative_path": "Movie.CHS.srt",
            "is_dir": False,
        }, {
            "file_id": "srt-b",
            "relative_path": "Movie.CHT.srt",
            "is_dir": False,
        }],
    )

    assert {
        item["source_id"]: item["rename_to"]
        for item in plan["operations"]
    } == {
        "ass": "Movie.chi.ass",
        "srt-a": "Movie.chi.srt",
        "srt-b": "Movie.variant-02.chi.srt",
    }


def test_language_evidence_retains_chinese_variant_for_audit():
    evidence = collect_subtitle_evidence([{
        "file_id": "traditional",
        "relative_path": "Show.S01E01.CHT.srt",
        "is_dir": False,
    }, {
        "file_id": "bilingual",
        "relative_path": "Show.S01E01.CHS&ENG.ass",
        "is_dir": False,
    }])

    assert [item["language_code"] for item in evidence] == ["chi", "chi"]
    assert [item["subtitle_variant"] for item in evidence] == [
        "traditional",
        "bilingual",
    ]
