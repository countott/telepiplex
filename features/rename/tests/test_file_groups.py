from dataclasses import replace

from telepiplex_rename.file_facts import ParsedFileEvidence
from telepiplex_rename.file_groups import (
    build_provisional_groups,
    build_verified_groups,
)


def _evidence(source_id, title, title_key, year=None):
    return ParsedFileEvidence(
        source_id=source_id,
        title_candidates=(title,) if title else (),
        title_key=title_key,
        year_hint=year,
        season_number=1,
        episode_number=1,
        absolute_episode=None,
        content_role="main",
        subtitle_language="unknown",
        subtitle_variant="unknown",
        confidence="high" if title else "low",
        evidence=("filename:title",) if title else (),
        directory_hints=("arbitrary-parent",),
    )


def test_missing_year_and_explicit_year_share_one_provisional_group():
    groups = build_provisional_groups([
        _evidence("video", "Veep", "veep"),
        _evidence("subtitle", "Veep", "veep", 2012),
    ])

    assert len(groups) == 1
    assert groups[0].status == "ready"
    assert groups[0].title_key == "veep"
    assert groups[0].year_hints == (2012,)
    assert groups[0].source_ids == ("subtitle", "video")
    assert groups[0].query_candidates == ("Veep",)


def test_two_explicit_years_do_not_merge_through_yearless_file():
    groups = build_provisional_groups([
        _evidence("old", "Veep", "veep", 2012),
        _evidence("new", "Veep", "veep", 2019),
        _evidence("yearless", "Veep", "veep"),
    ])

    ready = [group for group in groups if group.status == "ready"]
    ambiguous = [group for group in groups if group.status == "ambiguous_year"]
    assert [(group.year_hints, group.source_ids) for group in ready] == [
        ((2012,), ("old",)),
        ((2019,), ("new",)),
    ]
    assert len(ambiguous) == 1
    assert ambiguous[0].source_ids == ("yearless",)


def test_parent_folders_do_not_join_or_split_title_groups():
    groups = build_provisional_groups([
        _evidence("mixed-veep", "Veep", "veep", 2012),
        replace(
            _evidence("mixed-honey", "Honey and Clover", "honeyandclover"),
            directory_hints=("same-parent",),
        ),
        replace(
            _evidence("sibling-veep", "Veep", "veep"),
            directory_hints=("different-parent",),
        ),
    ])

    assert [(group.title_key, group.source_ids) for group in groups] == [
        ("honeyandclover", ("mixed-honey",)),
        ("veep", ("mixed-veep", "sibling-veep")),
    ]


def test_subtitle_only_evidence_can_form_group_and_missing_title_is_local():
    subtitle = replace(
        _evidence("subtitle", "Veep", "veep", 2012),
        content_role="subtitle",
        subtitle_language="eng",
    )
    missing = _evidence("opaque", "", "")

    groups = build_provisional_groups([missing, subtitle])

    assert [(group.status, group.source_ids) for group in groups] == [
        ("unresolved_title", ("opaque",)),
        ("ready", ("subtitle",)),
    ]


def test_auxiliary_only_evidence_never_becomes_independent_work_group():
    opening = replace(
        _evidence("opening", "Honey and Clover", "honeyandclover"),
        episode_number=None,
        content_role="opening",
    )

    groups = build_provisional_groups([opening])

    assert len(groups) == 1
    assert groups[0].status == "unresolved_auxiliary"
    assert groups[0].source_ids == ("opening",)


def test_group_order_and_ids_are_independent_of_input_order():
    evidence = [
        _evidence("b", "Veep", "veep", 2012),
        _evidence("a", "Veep", "veep"),
        _evidence("c", "Honey and Clover", "honeyandclover"),
    ]

    forward = build_provisional_groups(evidence)
    reverse = build_provisional_groups(list(reversed(evidence)))

    assert forward == reverse


def test_confirmed_aliases_merge_only_on_same_external_identity():
    groups = build_provisional_groups([
        _evidence("english", "Honey and Clover", "honeyandclover"),
        _evidence("chinese", "蜂蜜与四叶草", "蜂蜜与四叶草"),
        _evidence("other", "Honey", "honey"),
    ])
    by_title = {group.title_key: group for group in groups}
    confirmed = {
        by_title["honeyandclover"].group_id: {
            "source": "tvdb", "external_id": "79155",
        },
        by_title["蜂蜜与四叶草"].group_id: {
            "source": "tvdb", "external_id": "79155",
        },
        by_title["honey"].group_id: {
            "source": "tvdb", "external_id": "99999",
        },
    }

    verified = build_verified_groups(groups, confirmed)

    assert [(group.external_identity, group.source_ids) for group in verified] == [
        ("tvdb:79155", ("chinese", "english")),
        ("tvdb:99999", ("other",)),
    ]


def test_ten_thousand_compatible_files_share_one_query_group():
    evidence = [
        _evidence(f"source-{index:05d}", "Veep", "veep", 2012)
        for index in range(10_000)
    ]

    groups = build_provisional_groups(evidence)

    assert len(groups) == 1
    assert len(groups[0].source_ids) == 10_000
    assert groups[0].query_candidates == ("Veep",)
