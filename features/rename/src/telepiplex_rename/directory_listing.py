"""Private, fail-closed parsing of storage directory listing pages."""

from dataclasses import dataclass


class DirectoryListingError(ValueError):
    pass


@dataclass(frozen=True)
class DirectoryPage:
    items: list[dict]
    count: int | None
    has_more: bool | None


def _nonnegative_integer(value):
    if type(value) is int and value >= 0:
        return value
    if isinstance(value, str) and value.isascii() and value.isdigit():
        return int(value)
    raise DirectoryListingError("invalid directory pagination integer")


def parse_directory_page(response, *, offset: int) -> DirectoryPage:
    """Validate every wrapper, including conflicting list/data aliases."""

    counts, more_values = set(), set()
    active_wrappers = set()

    def unwrap(value, depth=0):
        if isinstance(value, list):
            if any(not isinstance(item, dict) for item in value):
                raise DirectoryListingError("malformed directory entry")
            return value
        if not isinstance(value, dict) or depth >= 8 or id(value) in active_wrappers:
            raise DirectoryListingError("malformed directory wrapper")
        active_wrappers.add(id(value))
        try:
            for field in ("state", "success"):
                if field in value and value[field] is not True:
                    raise DirectoryListingError("directory listing failed")
            if "code" in value and _nonnegative_integer(value["code"]) != 0:
                raise DirectoryListingError("directory listing provider error")
            if "count" in value:
                counts.add(_nonnegative_integer(value["count"]))
            if "offset" in value and _nonnegative_integer(value["offset"]) != offset:
                raise DirectoryListingError("directory listing offset changed")
            if "has_more" in value:
                if type(value["has_more"]) is not bool:
                    raise DirectoryListingError("invalid directory has_more")
                more_values.add(value["has_more"])
            children = [value[key] for key in ("list", "data") if key in value]
            if not children:
                raise DirectoryListingError("directory listing has no records")
            pages = [unwrap(child, depth + 1) for child in children]
            if any(page != pages[0] for page in pages[1:]):
                raise DirectoryListingError("conflicting directory record aliases")
            return pages[0]
        finally:
            active_wrappers.remove(id(value))

    items = unwrap(response)
    if len(counts) > 1 or len(more_values) > 1:
        raise DirectoryListingError("conflicting directory pagination wrappers")
    count = next(iter(counts), None)
    has_more = next(iter(more_values), None)
    end = offset + len(items)
    if count is not None and (end > count or offset > count):
        raise DirectoryListingError("directory page exceeds declared count")
    if not items and (has_more is True or (count is not None and offset != count)):
        raise DirectoryListingError("directory listing ended before declared count")
    if count is not None:
        if has_more is False and end != count:
            raise DirectoryListingError("terminal page disagrees with declared count")
        if has_more is True and end >= count:
            raise DirectoryListingError("continuation disagrees with declared count")
    return DirectoryPage(items, count, has_more)


def read_directory_items(storage, directory_id: str, *, item_facts) -> list[dict]:
    """Read complete pages with stable counts and non-repeating identities."""

    collected, seen_ids = [], set()
    expected_count = None
    promised_more = False
    offset = 0
    for _page in range(100):
        page = parse_directory_page(storage.get_file_list({
            "cid": directory_id, "offset": offset, "limit": 1000, "show_dir": 1,
        }), offset=offset)
        if promised_more and not page.items:
            raise DirectoryListingError("directory continuation returned no records")
        if page.count is not None:
            if expected_count is not None and page.count != expected_count:
                raise DirectoryListingError("directory listing count changed")
            expected_count = page.count
        end = offset + len(page.items)
        if expected_count is not None and end > expected_count:
            raise DirectoryListingError("directory listing exceeds prior count")
        for item in page.items:
            provider_id, name, _sha1, _size = item_facts(item)
            if provider_id in seen_ids:
                raise DirectoryListingError("directory listing repeats an object")
            seen_ids.add(provider_id)
        collected.extend(page.items)
        if not page.items or page.has_more is False:
            if expected_count is not None and end != expected_count:
                raise DirectoryListingError("directory listing ended before prior count")
            return collected
        # A short list-only page is not evidence of completion. Some providers
        # enforce a smaller page size than requested; prove its empty tail.
        offset = end
        promised_more = page.has_more is True
    raise DirectoryListingError("directory listing exceeds bounded page count")
