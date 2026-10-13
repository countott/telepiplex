"""Private validation of 115 listing envelopes and scan continuations."""

from dataclasses import dataclass

from telepiplex_plugin_sdk.storage_tree import TreeIntegrityError


class ListingIntegrityError(ValueError):
    pass


def validate_status(value):
    if not isinstance(value, dict):
        raise ListingIntegrityError("malformed provider response")
    for key in ("state", "success"):
        if key in value and value[key] is not True:
            raise ListingIntegrityError("provider response reports failure")
    if "code" in value and (isinstance(value["code"], bool) or str(value["code"]) != "0"):
        raise ListingIntegrityError("provider response has a nonzero or malformed code")


def _integer(value):
    if type(value) is int and value >= 0:
        return value
    if isinstance(value, str) and value.isascii() and value.isdigit():
        return int(value)
    raise ListingIntegrityError("invalid listing pagination integer")


@dataclass(frozen=True)
class ListingPage:
    items: list[dict]
    count: int | None
    has_more: bool | None


def parse_listing_page(response, *, offset=0):
    offset = _integer(offset)
    counts, continuations = set(), set()
    active = set()

    def unwrap(value, depth=0):
        if isinstance(value, list):
            if any(not isinstance(item, dict) for item in value):
                raise ListingIntegrityError("malformed listing node")
            return value
        if not isinstance(value, dict) or depth >= 8 or id(value) in active:
            raise ListingIntegrityError("malformed listing wrapper")
        active.add(id(value))
        try:
            validate_status(value)
            if "count" in value:
                counts.add(_integer(value["count"]))
            if "offset" in value and _integer(value["offset"]) != offset:
                raise ListingIntegrityError("listing offset disagrees with request")
            if "has_more" in value:
                if type(value["has_more"]) is not bool:
                    raise ListingIntegrityError("invalid listing continuation flag")
                continuations.add(value["has_more"])
            children = [value[key] for key in ("list", "data") if key in value]
            if not children:
                raise ListingIntegrityError("listing wrapper has no records")
            items = [unwrap(child, depth + 1) for child in children]
            if any(alias != items[0] for alias in items[1:]):
                raise ListingIntegrityError("conflicting listing record aliases")
            return items[0]
        finally:
            active.remove(id(value))

    items = unwrap(response)
    if len(counts) > 1 or len(continuations) > 1:
        raise ListingIntegrityError("conflicting listing pagination wrappers")
    count, has_more = next(iter(counts), None), next(iter(continuations), None)
    end = offset + len(items)
    if count is not None and (offset > count or end > count):
        raise ListingIntegrityError("listing exceeds declared count")
    if not items and (has_more is True or (count is not None and offset != count)):
        raise ListingIntegrityError("listing ended before its continuation or count")
    if count is not None and (
        (has_more is False and end != count) or (has_more is True and end >= count)
    ):
        raise ListingIntegrityError("listing continuation contradicts declared count")
    return ListingPage(items, count, has_more)


class TreeScanStorage:
    """One scan's continuation state; never shared between concurrent scans."""

    def __init__(self, client):
        self.client = client
        self.promised_more = {}
        self.expected_counts = {}

    def get_file_info(self, path):
        return self.client.get_file_info(path)

    def get_file_list(self, params):
        response = self.client.get_file_list(params)
        try:
            page = parse_listing_page(response, offset=params.get("offset", 0))
            directory_id = str(params["cid"])
            offset = _integer(params.get("offset", 0))
            expected_count = self.expected_counts.get(directory_id)
            if page.count is not None:
                if expected_count is not None and expected_count != page.count:
                    raise ListingIntegrityError("listing count changed during scan")
                expected_count = page.count
                self.expected_counts[directory_id] = expected_count
            if expected_count is not None and (
                offset + len(page.items) > expected_count or
                (not page.items and offset != expected_count)
            ):
                raise ListingIntegrityError("listing scan contradicts its previously declared count")
            previous_continuation = self.promised_more.get(directory_id)
            if previous_continuation is True and not page.items:
                raise ListingIntegrityError("promised listing continuation returned no records")
            if previous_continuation is False and page.items:
                raise ListingIntegrityError("records followed an explicit final listing page")
            self.promised_more[directory_id] = page.has_more
        except ListingIntegrityError as exc:
            raise TreeIntegrityError(str(exc)) from exc
        return response
