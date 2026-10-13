"""Async inventory must prove complete pages before offering media groups."""

import asyncio

import pytest

from telepiplex_plugin_sdk import FeatureError
from telepiplex_rename.operations import OperationCancelled
from telepiplex_rename.service import RenameFeature


def node(number, **changes):
    return {"file_id": str(number), "name": f"{number}.mkv", "is_dir": False,
            "size": 1024, **changes}


class ListingHost:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def call_capability(self, capability, method, payload, **options):
        assert (capability, method) == ("storage.provider", "get_file_list")
        params = payload["args"][0]
        self.calls.append(dict(params))
        await asyncio.sleep(0)
        value = self.response(params) if callable(self.response) else self.response
        return {"value": value}


def feature_for(response):
    host = ListingHost(response)
    return RenameFeature(config={"storage_timeout": 3}, host=host), host


@pytest.mark.parametrize("response", [
    {"state": False, "list": [node(1)]},
    {"state": True, "code": 50001, "list": [node(1)]},
    {"state": True, "code": "50001", "data": {"list": [node(1)]}},
    {"data": {"code": 50001, "list": [node(1)]}},
    {"data": {"data": {"state": False, "list": [node(1)]}}},
    {"list": [node(1)], "has_more": "false"},
    {"list": [], "has_more": True},
    {"list": [node(1)], "has_more": False, "data": {"list": [node(1)], "has_more": True}},
    {"list": [node(1)], "data": {"list": [node(2)]}},
    {"list": [node(1)], "count": 2, "has_more": False},
    {"count": 1, "data": {"count": 2, "list": [node(1)]}},
    {"list": [node(1)], "offset": 1},
    {"offset": 0, "data": {"offset": 1, "list": [node(1)]}},
    {"list": [node(1)], "count": True},
    {"list": [node(1)], "code": True},
    {"list": [node(1), "not-a-node"]},
    {"list": [node(1), None]},
])
def test_inventory_rejects_failed_malformed_or_conflicting_pages(response):
    feature, _ = feature_for(response)
    with pytest.raises(FeatureError) as rejected:
        asyncio.run(feature._inventory_directory_items("root"))
    assert rejected.value.code == "inventory_tree_incomplete"


@pytest.mark.parametrize("response", [
    {"list": [], "incomplete": True},
    {"data": {"list": [], "snapshot_complete": False}},
    {"data": {"data": {"list": [], "complete": "false"}}},
])
def test_inventory_retains_incomplete_flags_at_wrapper_depth(response):
    feature, _ = feature_for(response)
    with pytest.raises(FeatureError) as rejected:
        asyncio.run(feature._inventory_directory_items("root"))
    assert rejected.value.code == "inventory_tree_incomplete"


@pytest.mark.parametrize("with_count", [False, True])
def test_inventory_short_pages_and_count_only_pages_require_empty_tail(with_count):
    rows = [node(number) for number in range(3)]

    def response(params):
        page = rows[params["offset"]:params["offset"] + 1]
        return {"data": {"list": page, "count": "3", "offset": params["offset"]}} if with_count else page

    feature, host = feature_for(response)
    assert asyncio.run(feature._inventory_directory_items("root")) == rows
    assert [params["offset"] for params in host.calls] == [0, 1, 2, 3]


def test_inventory_consistent_explicit_terminal_page_can_finish_without_empty_tail():
    rows = [node(1)]
    feature, host = feature_for({
        "count": 1, "offset": 0, "has_more": False,
        "list": rows, "data": {"count": "1", "has_more": False, "list": rows},
    })
    assert asyncio.run(feature._inventory_directory_items("root")) == rows
    assert len(host.calls) == 1


def test_inventory_underreported_count_cannot_hide_next_page():
    feature, host = feature_for(lambda params: {
        "list": [node(params["offset"] + 1)], "count": 1, "offset": params["offset"],
    })
    with pytest.raises(FeatureError) as rejected:
        asyncio.run(feature._inventory_directory_items("root"))
    assert rejected.value.code == "inventory_tree_incomplete"
    assert [params["offset"] for params in host.calls] == [0, 1]


def test_inventory_continuation_must_return_records():
    feature, host = feature_for(lambda params: {
        "list": [node(1)] if params["offset"] == 0 else [],
        "has_more": params["offset"] == 0,
    })
    with pytest.raises(FeatureError) as rejected:
        asyncio.run(feature._inventory_directory_items("root"))
    assert rejected.value.code == "inventory_tree_incomplete"
    assert len(host.calls) == 2


@pytest.mark.parametrize("across_pages", [False, True])
def test_inventory_duplicate_id_is_rejected_even_when_name_changes(across_pages):
    rows = [node(1), node(1, name="replacement.mkv", is_dir=True)]
    response = (lambda params: {"list": rows[params["offset"]:params["offset"] + 1],
        "has_more": params["offset"] == 0}) if across_pages else {"list": rows, "has_more": False}
    feature, _ = feature_for(response)
    with pytest.raises(FeatureError) as rejected:
        asyncio.run(feature._inventory_directory_items("root"))
    assert rejected.value.code == "inventory_tree_incomplete"


@pytest.mark.parametrize("identity", [None, False, "", [], {"id": 1}])
def test_inventory_requires_scalar_stable_object_identity(identity):
    feature, _ = feature_for({"list": [node(identity)], "has_more": False})
    # Override node's string construction to retain malformed provider data.
    feature.host.response["list"][0]["file_id"] = identity
    with pytest.raises(FeatureError) as rejected:
        asyncio.run(feature._inventory_directory_items("root"))
    assert rejected.value.code == "inventory_tree_incomplete"


@pytest.mark.parametrize("malformed", [True, False])
def test_inventory_malformed_primary_id_cannot_be_hidden_by_valid_alias(malformed):
    feature, _ = feature_for({
        "list": [node(1, file_id=malformed, fid="valid")], "has_more": False,
    })
    with pytest.raises(FeatureError) as rejected:
        asyncio.run(feature._inventory_directory_items("root"))
    assert rejected.value.code == "inventory_tree_incomplete"


def test_inventory_absent_primary_id_uses_same_typed_alias_in_tree():
    feature, _ = feature_for({
        "list": [node(1, file_id=None, fid="valid")], "has_more": False,
    })
    tree = asyncio.run(feature._inventory_file_tree({"file_id": "root"}, "/Library"))
    assert len(tree) == 1
    assert tree[0]["file_id"] == "valid"


@pytest.mark.parametrize("cancelled", [asyncio.CancelledError, OperationCancelled])
def test_inventory_cancellation_propagates_during_pagination(cancelled):
    rows = [node(number) for number in range(1000)]

    def response(params):
        if params["offset"]:
            raise cancelled()
        return rows

    feature, host = feature_for(response)
    with pytest.raises(cancelled):
        asyncio.run(feature._inventory_directory_items("root"))
    assert [params["offset"] for params in host.calls] == [0, 1000]


def test_inventory_capacity_is_still_checked_by_tree_caller():
    feature, _ = feature_for({"list": [node(1), node(2)], "has_more": False})
    with pytest.raises(FeatureError) as rejected:
        asyncio.run(feature._inventory_file_tree({"file_id": "root"}, "/Library", max_nodes=1))
    assert rejected.value.code == "inventory_too_large"
