"""Public score-table behavior, source ordering and hard rejection."""

import pytest
import json
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

from telepiplex_search.context import runtime_context
from telepiplex_search.release_score import rank_releases, score_release_details


@pytest.fixture(autouse=True)
def isolated_config():
    original = runtime_context.config
    runtime_context.configure({"search": {"scoring": {
        "keyword_scores": {"BluRay": 12, "BDRip": 10, "WEB": 6, "Remux": -1},
        "reject_keywords": ["720p", "576p", "480p", "CAM", "TS", "TC"],
    }}})
    yield
    runtime_context.config = original


def keyword_points(title):
    _, details = score_release_details({"title": title})
    return sum(item["score"] for item in details if item["kind"] == "keyword")


@pytest.mark.parametrize("source,expected", (
    ("BluRay", 12), ("Blu-Ray", 12), ("蓝光", 12), ("藍光", 12),
    ("BDRip", 10), ("BRRip", 10), ("UHD-BDRip", 10),
    ("BluRay.BDRip", 10),
    ("BDRemux", 11), ("BluRay.Remux", 11), ("BDRip.Remux", 9),
    ("WEB-DL", 6), ("WEB_DL", 6), ("WEBDL", 6), ("WEBRip", 6),
    ("WEBMux", 6), ("WEB-DLmux", 6),
    ("WEBRemux", 6), ("WEB-DL.Remux", 6),
    ("REMUX", 0), ("UHDRemux", 0), ("HDTV", 0), ("", 0),
))
def test_source_families_score_once_and_only_bd_remux_loses_points(source, expected):
    assert keyword_points(f"Feature.1080p.{source}.HEVC.Atmos") == expected


def test_otherwise_identical_releases_follow_source_preference():
    titles = [f"Feature.1080p.{source}" for source in (
        "WEBRip", "BDRip.Remux", "BDRemux", "BluRay", "WEB-DL", "BDRip",
    )]
    ranked = rank_releases([
        {"title": title, "magnet_url": "magnet:?x", "seeders": 10, "size": 10 * 1024**3}
        for title in titles
    ], 12)
    assert [item["title"] for item in ranked] == [titles[i] for i in (3, 2, 5, 1, 0, 4)]


@pytest.mark.parametrize("title", (
    "Feature.720P.Remastered.BluRay", "Feature_576p_BluRay", "Feature.480p",
    "Feature.1280x720.BluRay", "Feature.720x576", "Feature.640x480",
    "Feature.CAM", "Feature.TS", "Feature.TC",
))
def test_rejected_candidates_never_enter_even_an_otherwise_empty_result(title):
    assert rank_releases([{"title": title, "magnet_url": "magnet:?x"}], 12) == []


def test_reject_overrides_arbitrarily_high_positive_score():
    runtime_context.config["search"]["scoring"]["keyword_scores"]["CAM"] = 1000000
    assert rank_releases([{"title": "Feature.CAM", "magnet_url": "magnet:?x"}], 12) == []


def test_reject_tokens_do_not_match_inside_other_words():
    assert len(rank_releases([
        {"title": "Scam.1080p.WEB-DL.DTS.Atmos", "magnet_url": "magnet:?x"},
    ], 12)) == 1


def test_explicit_empty_table_does_not_restore_default_preferences():
    runtime_context.configure({"search": {"scoring": {"keyword_scores": {}}}})
    assert keyword_points("Feature.2160p.WEB-DL.HEVC.Atmos.CAM") == 0


def test_legacy_explicit_prefer_lists_still_work():
    runtime_context.configure({"search": {"scoring": {
        "keyword_scores": {}, "prefer_resolution": ["2160p"],
    }}})
    assert keyword_points("Feature.2160p") == 35


def test_copyable_example_validates_and_rejects_low_quality_before_ranking():
    root = Path(__file__).resolve().parents[1]
    example = yaml.safe_load((root / "examples/scoring.yaml").read_text())
    schema = json.loads((root / "config.schema.json").read_text())
    scoring_schema = schema["properties"]["search"]["properties"]["scoring"]
    Draft202012Validator(scoring_schema).validate(example["scoring"])
    runtime_context.configure({"search": example})
    assert set(example["scoring"]) == {"indexer_scores", "reject_keywords", "keyword_scores"}
    titles = ["Feature.1080p." + source for source in (
        "BluRay", "BDRemux", "BDRip", "BDRip.Remux", "WEB-DL", "WEBRip",
    )]
    items = [{"title": title, "magnet_url": "magnet:?x"} for title in titles]
    items.insert(0, {"title": "Feature.720p.Remastered.BluRay", "magnet_url": "magnet:?x"})
    assert [item["title"] for item in rank_releases(items, 12)] == titles


def test_downscale_is_judged_by_output_resolution_not_4k_source():
    assert len(rank_releases([
        {"title": "Feature.4Kto1080p.BluRay", "magnet_url": "magnet:?x"},
    ], 12)) == 1
