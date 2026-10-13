"""Regressions found by exercising provider boundaries and rendered choices."""

import asyncio
from copy import deepcopy
from unittest.mock import patch

import pytest

from telepiplex_search import audit_transport
from telepiplex_search.audit_transport import (
    AuditHost,
    AuditRuntime,
    FixtureProviders,
    OfflineNetworkGuard,
    audit_config,
)
from telepiplex_search.context import runtime_context
from telepiplex_search.live_pipeline_audit import run_business_case
from telepiplex_search.release_gate import gate_releases
from telepiplex_search.service import SearchFeature


def fargo_case(**changes):
    return {
        "case_id": "fargo-episode",
        "query": "Fargo S02E03",
        "expected_titles": ["Fargo"],
        "year": "2014",
        "media_type": "series",
        "scope": "episode",
        "season_number": 2,
        "episode_number": 3,
        **changes,
    }


def release(title, suffix="a"):
    return {
        "title": title,
        "magnet_url": "magnet:?xt=urn:btih:" + suffix * 40,
        "seeders": 10,
        "size": 1024**3,
        "indexer": "audit-fixture",
    }


@pytest.mark.parametrize("title,scope,season,episode", [
    ("Fargo_S02E03_1080p_WEB-DL", "episode", 2, 3),
    ("Fargo_2x03_1080p_WEB-DL", "episode", 2, 3),
    ("Fargo_S02_1080p_WEB-DL", "season", 2, None),
    ("Fargo_S01-S02_1080p_WEB-DL", "work", None, None),
])
def test_underscore_separators_preserve_confirmed_release_scope(title, scope, season, episode):
    case = fargo_case(
        query="Fargo", scope=scope, season_number=season, episode_number=episode,
    )
    with patch.object(FixtureProviders, "releases", lambda *args: [release(title)]):
        report = asyncio.run(run_business_case(case))
    assert report["outcome"] == "business_success", report
    assert report["submission"]["media_metadata"]["scope"] == {
        "kind": "whole_series" if scope == "work" else scope,
        "season_number": season,
        "episode_number": episode,
    }


@pytest.mark.parametrize("title", [
    "Fargo_S01E03_1080p_WEB-DL",
    "Fargo_S02E02_1080p_WEB-DL",
    "Fargo_S02E01-E03_1080p_WEB-DL",
    "Fargo_S02E02E03_1080p_WEB-DL",
    "Fargo_S01E03-S02E03_1080p_WEB-DL",
    "Fargo_S02_1080p_WEB-DL",
    "Fargo_S01-S02_1080p_WEB-DL",
])
def test_underscore_variants_cannot_widen_an_episode_selection(title):
    with patch.object(FixtureProviders, "releases", lambda *args: [release(title)]):
        report = asyncio.run(run_business_case(fargo_case(expected_outcome="safe_rejection")))
    assert report["outcome"] == "safe_rejection", report
    assert report["reason_code"] == "no_eligible_release"
    assert report["submission_count"] == 0


@pytest.mark.parametrize("title", ["Special", "Extras", "Special Ops"])
def test_special_words_in_verified_series_title_are_normal_content(title):
    case = fargo_case(
        query=title, expected_titles=[title], year="2019", scope="work",
        season_number=None, episode_number=None,
    )
    fixture_work = ("Q91000009", title, title, "2019", "series", 2)
    with patch.object(audit_transport, "WORKS", audit_transport.WORKS + (fixture_work,)):
        report = asyncio.run(run_business_case(case))
    assert report["outcome"] == "business_success", report
    assert report["submission"]["media_metadata"]["identity"]["provider_refs"]["wikidata"] == "Q91000009"


@pytest.mark.parametrize("title", ["Special", "Extras", "Special Ops"])
@pytest.mark.parametrize("marker", ["Specials", "Extras", "Bonus", "S00"])
def test_special_markers_after_verified_title_remain_rejected(title, marker):
    contract = {
        "identity": {"english_title": title, "year": "2019"},
        "retrieval": {"media_type": "series", "scope": "season"},
        "placement": {"library_type": "series", "season_number": 1},
        "items": [{"season_number": 1, "episode_number": 1, "aired": "2019-01-01"}],
    }
    result = gate_releases([release(f"{title}.S01.{marker}.1080p")], contract)
    assert result.eligible == ()
    assert result.rejection_counts == {"unsupported_special_content": 1}


async def select_rendered_scope(query, *, episode=None):
    case = fargo_case(query=query)
    fixture = FixtureProviders(case)
    config = audit_config()
    previous_config = runtime_context.config
    host, runtime = AuditHost(), AuditRuntime()
    feature = SearchFeature(config=config, host=host, release_resolver=lambda item: item.get("magnet_url") or "")
    feature.bind_runtime(runtime)
    request = {"chat_id": 91001, "user_id": 91001}
    runtime_context.configure(config)
    try:
        with OfflineNetworkGuard(), fixture.active():
            await feature.command({**request, "command": "s", "args": [query]})
            await runtime.drain()
            plan_id, stored = next(iter(feature.plans.items()))
            index = next(i for i, candidate in enumerate(stored["candidates"])
                if (candidate["media_metadata"]["identity"]["external_ids"].get("wikidata")) == "Q91000002")
            await feature.callback({**request, "payload": f"select:{plan_id}:{index}"})
            await runtime.drain()
            original_identity = deepcopy(stored["plan"]["media_metadata"]["identity"])
            original_items = deepcopy(stored["plan"]["media_metadata"]["items"])

            def buttons():
                operation = feature.operations[stored["operation_id"]]
                return [button for row in operation.get("details", {}).get("keyboard", []) for button in row]

            async def click(label):
                button = next(button for button in buttons() if button["text"] == label)
                await feature.callback({**request, "payload": button["callback_data"].removeprefix("search:")})
                await runtime.drain()

            if query == "Fargo":
                await click("第二季")
            operation = feature.operations[stored["operation_id"]]
            assert operation["stage"] == "series_scope"
            assert [button["text"] for button in buttons()] == [
                "第二季 全季", "第一集", "第二集", "第三集", "返回",
            ]
            # Opening a scope menu must keep the complete frozen facts intact.
            assert stored["plan"]["media_metadata"]["identity"] == original_identity
            assert stored["plan"]["media_metadata"]["items"] == original_items
            assert not stored.get("confirmed_contract")
            assert not host.submissions
            await click("第三集" if episode is not None else "第二季 全季")
            assert feature.operations[stored["operation_id"]]["stage"] == "release_selection"
            release_button = next(button for button in buttons() if button["callback_data"].startswith("search:release:"))
            await feature.callback({**request, "payload": release_button["callback_data"].removeprefix("search:")})
            await runtime.drain()
            assert len(host.submissions) == 1
            metadata = host.submissions[0][0]["media_metadata"]
            assert metadata["identity"]["year"] == 2014
            assert metadata["identity"]["provider_refs"]["wikidata"] == "Q91000002"
            assert metadata["scope"] == {
                "kind": "episode" if episode is not None else "season",
                "season_number": 2,
                "episode_number": episode,
            }
            return metadata
    finally:
        await runtime.close(feature)
        runtime_context.configure(previous_config)


@pytest.mark.parametrize("episode", [None, 3])
def test_root_and_direct_season_have_same_rendered_menu_and_frozen_submission(episode):
    root = asyncio.run(select_rendered_scope("Fargo", episode=episode))
    direct = asyncio.run(select_rendered_scope("Fargo S02", episode=episode))
    assert root == direct


@pytest.mark.parametrize("query,scope,episode", [
    ("Fargo", "season", None),
    ("Fargo S02", "season", None),
    ("Fargo", "episode", 3),
])
def test_business_audit_uses_only_rendered_scope_callbacks(query, scope, episode):
    original_callback = SearchFeature.callback

    async def check_rendered(feature, request):
        payload = request["payload"]
        if payload.startswith("scope:"):
            stored = feature.plans[payload.split(":")[1]]
            operation = feature.operations[stored["operation_id"]]
            rendered = {
                button["callback_data"]
                for row in (operation.get("details") or {}).get("keyboard") or ()
                for button in row
            }
            assert "search:" + payload in rendered, "scope_callback_not_rendered"
        return await original_callback(feature, request)

    with patch.object(SearchFeature, "callback", check_rendered):
        report = asyncio.run(run_business_case(fargo_case(
            query=query, scope=scope, episode_number=episode,
        )))
    assert report["outcome"] == "business_success", report
