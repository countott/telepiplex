"""Pure parsing and reconciliation for Wikipedia episode tables."""

from __future__ import annotations

import html as html_module
import re
from html.parser import HTMLParser


def _text(value) -> str:
    return " ".join(
        html_module.unescape(str(value or "")).replace("\xa0", " ").split()
    )


def _attrs(values) -> dict[str, str]:
    return {
        str(key): str(value or "")
        for key, value in values or ()
        if key
    }


class _EpisodeTableParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.current_heading = ""
        self._heading_tag = ""
        self._heading_parts: list[str] = []
        self._table_depth = 0
        self._table: dict | None = None
        self._row: dict | None = None
        self._cell: dict | None = None
        self.tables: list[dict] = []

    def handle_starttag(self, tag, attrs):
        tag = tag.casefold()
        attributes = _attrs(attrs)
        if tag in {"h2", "h3", "h4"} and self._table_depth == 0:
            self._heading_tag = tag
            self._heading_parts = []
            return
        if tag == "table":
            if self._table_depth == 0:
                classes = set(attributes.get("class", "").split())
                if classes.intersection({"wikitable", "wikiepisodetable"}):
                    self._table = {
                        "heading": self.current_heading,
                        "classes": classes,
                        "rows": [],
                    }
            self._table_depth += 1
            return
        if self._table is None or self._table_depth != 1:
            return
        if tag == "tr":
            self._row = {"attrs": attributes, "cells": []}
        elif tag in {"th", "td"} and self._row is not None:
            self._cell = {
                "tag": tag,
                "attrs": attributes,
                "parts": [],
            }
        elif tag == "br" and self._cell is not None:
            self._cell["parts"].append(" ")

    def handle_endtag(self, tag):
        tag = tag.casefold()
        if tag == self._heading_tag:
            self.current_heading = _text("".join(self._heading_parts))
            self._heading_tag = ""
            self._heading_parts = []
            return
        if tag in {"th", "td"} and self._cell is not None:
            self._cell["text"] = _text("".join(self._cell.pop("parts")))
            if self._row is not None:
                self._row["cells"].append(self._cell)
            self._cell = None
            return
        if tag == "tr" and self._row is not None:
            if self._table is not None and self._row["cells"]:
                self._table["rows"].append(self._row)
            self._row = None
            return
        if tag == "table" and self._table_depth:
            self._table_depth -= 1
            if self._table_depth == 0 and self._table is not None:
                self.tables.append(self._table)
                self._table = None

    def handle_data(self, data):
        if self._heading_tag:
            self._heading_parts.append(data)
        if self._cell is not None:
            self._cell["parts"].append(data)


def _integer(value) -> int | None:
    match = re.search(r"(?<!\d)(\d{1,4})(?!\d)", _text(value))
    if match is None:
        return None
    parsed = int(match.group(1))
    return parsed if parsed > 0 else None


def _date(value) -> str:
    match = re.search(r"(?<!\d)((?:19|20)\d{2}-\d{2}-\d{2})(?!\d)", _text(value))
    return match.group(1) if match else ""


def _season_from_heading(value) -> int | None:
    text = _text(value)
    for pattern in (
        r"\bSeason\s+(\d+)\b",
        r"\bSeries\s+(\d+)\b",
        r"第\s*(\d+)\s*季",
    ):
        if match := re.search(pattern, text, re.IGNORECASE):
            return int(match.group(1))
    return None


def _header_index(headers: list[str], signals: tuple[str, ...]) -> int | None:
    for index, value in enumerate(headers):
        normalized = _text(value).casefold()
        if any(signal in normalized for signal in signals):
            return index
    return None


def _series_overview_totals(table: dict) -> dict[int, int]:
    heading = _text(table.get("heading")).casefold()
    rows = table.get("rows") or []
    if not rows or not (
        "series overview" in heading
        or "season overview" in heading
        or "剧集总览" in heading
        or "劇集總覽" in heading
    ):
        return {}
    headers = [cell.get("text", "") for cell in rows[0]["cells"]]
    season_index = _header_index(headers, ("series", "season", "季度"))
    episode_index = _header_index(headers, ("episodes", "集数", "集數"))
    if season_index is None or episode_index is None:
        return {}
    totals = {}
    for row in rows[1:]:
        cells = row["cells"]
        if max(season_index, episode_index) >= len(cells):
            continue
        season = _integer(cells[season_index].get("text"))
        total = _integer(cells[episode_index].get("text"))
        if season and total:
            totals[season] = total
    return totals


def _episode_items(
    table: dict,
    *,
    language: str,
    source_url: str,
    revision_id: int,
) -> list[dict]:
    rows = table.get("rows") or []
    if not rows:
        return []
    headers = [cell.get("text", "") for cell in rows[0]["cells"]]
    overall_index = _header_index(headers, ("overall", "总集", "總集"))
    local_index = _header_index(headers, ("in season", "本季", "季内", "季內"))
    number_index = _header_index(headers, ("集数", "集數", "episode", "no."))
    title_index = _header_index(headers, ("title", "标题", "標題"))
    date_index = _header_index(
        headers,
        (
            "release date",
            "air date",
            "上线日期",
            "上線日期",
            "首播",
            "播出日期",
        ),
    )
    season_number = _season_from_heading(table.get("heading"))
    if overall_index is None and local_index is None:
        if season_number is not None:
            local_index = number_index
        else:
            overall_index = number_index
    items = []
    for row in rows[1:]:
        row_classes = set((row.get("attrs") or {}).get("class", "").split())
        cells = row.get("cells") or []
        is_episode_row = "module-episode-list-row" in row_classes or any(
            str((cell.get("attrs") or {}).get("id", "")).startswith("ep")
            for cell in cells
        )
        if not is_episode_row:
            continue
        overall = (
            _integer(cells[overall_index].get("text"))
            if overall_index is not None and overall_index < len(cells)
            else None
        )
        episode = (
            _integer(cells[local_index].get("text"))
            if local_index is not None and local_index < len(cells)
            else None
        )
        if overall is None and episode is None:
            continue
        air_date = (
            _date(cells[date_index].get("text"))
            if date_index is not None and date_index < len(cells)
            else ""
        )
        title = (
            _text(cells[title_index].get("text"))
            if title_index is not None and title_index < len(cells)
            else ""
        )
        items.append({
            "season_number": season_number if episode is not None else None,
            "episode_number": episode,
            "overall_number": overall,
            "title": title,
            "air_date": air_date,
            "source_language": _text(language).casefold(),
            "source_url": _text(source_url),
            "revision_id": int(revision_id or 0),
        })
    return items


def parse_wikipedia_episode_html(
    html: str,
    *,
    language: str,
    source_url: str,
    revision_id: int,
) -> dict:
    parser = _EpisodeTableParser()
    try:
        parser.feed(str(html or ""))
        parser.close()
    except (TypeError, ValueError) as exc:
        return {
            "status": "parse_error",
            "items": [],
            "season_totals": {},
            "source_url": _text(source_url),
            "source_language": _text(language).casefold(),
            "revision_id": int(revision_id or 0),
            "error": f"wikipedia_parse_error:{type(exc).__name__}",
        }
    episode_tables = [
        table
        for table in parser.tables
        if "wikiepisodetable" in (table.get("classes") or set())
    ]
    season_totals = {}
    for table in parser.tables:
        season_totals.update(_series_overview_totals(table))
    items = [
        item
        for table in episode_tables
        for item in _episode_items(
            table,
            language=language,
            source_url=source_url,
            revision_id=revision_id,
        )
    ]
    if not episode_tables:
        status = "absent"
        error = "wikipedia_table_absent"
    elif not items:
        status = "parse_error"
        error = "wikipedia_parse_error"
    else:
        coordinates = {
            (item["season_number"], item["episode_number"])
            for item in items
            if item["season_number"] and item["episode_number"]
        }
        expected = sum(season_totals.values())
        status = (
            "complete"
            if season_totals
            and len(coordinates) == expected
            and all(
                sum(1 for season, _episode in coordinates if season == key)
                == total
                for key, total in season_totals.items()
            )
            else "partial"
        )
        error = ""
    return {
        "status": status,
        "items": items,
        "season_totals": season_totals,
        "source_url": _text(source_url),
        "source_language": _text(language).casefold(),
        "revision_id": int(revision_id or 0),
        "error": error,
    }


def _conflict_result(primary: dict, secondary: dict | None) -> dict:
    sources = [
        item for item in (primary, secondary) if isinstance(item, dict)
    ]
    return {
        "status": "conflict",
        "items": [],
        "season_totals": {},
        "source_urls": [
            value
            for item in sources
            if (value := _text(item.get("source_url")))
        ],
        "source_revisions": {
            language: int(item.get("revision_id") or 0)
            for item in sources
            if (language := _text(item.get("source_language")))
        },
        "error": "wikipedia_fact_conflict",
    }


def merge_wikipedia_episode_results(
    primary: dict,
    secondary: dict | None = None,
    *,
    expected_qid: str,
) -> dict:
    sources = [
        item for item in (primary, secondary) if isinstance(item, dict)
    ]
    expected_qid = _text(expected_qid)
    qids = {
        _text(item.get("wikibase_item"))
        for item in sources
        if _text(item.get("wikibase_item"))
    }
    if (
        len(qids) > 1
        or (expected_qid and qids and qids != {expected_qid})
    ):
        return _conflict_result(primary, secondary)

    season_totals = {}
    for source in sources:
        for season, total in (source.get("season_totals") or {}).items():
            try:
                season_number = int(season)
                episode_total = int(total)
            except (TypeError, ValueError):
                continue
            existing = season_totals.get(season_number)
            if existing is not None and existing != episode_total:
                return _conflict_result(primary, secondary)
            season_totals[season_number] = episode_total

    by_coordinate: dict[tuple[int, int], dict] = {}
    idless = []
    fact_conflicts = []
    for source in sources:
        for raw in source.get("items") or ():
            if not isinstance(raw, dict):
                continue
            try:
                season = int(raw.get("season_number"))
                episode = int(raw.get("episode_number"))
            except (TypeError, ValueError):
                idless.append(dict(raw))
                continue
            if season < 1 or episode < 1:
                continue
            key = (season, episode)
            current = by_coordinate.get(key)
            if current is None:
                by_coordinate[key] = dict(raw)
                continue
            current_date = _text(current.get("air_date"))
            incoming_date = _text(raw.get("air_date"))
            if current_date and incoming_date and current_date != incoming_date:
                current["air_date"] = ""
                current["air_date_conflict"] = True
                fact_conflicts.append(f"S{season:02d}E{episode:02d}")
            elif not current_date and incoming_date:
                current["air_date"] = incoming_date
            languages = list(current.get("source_languages") or ())
            for value in (
                current.get("source_language"),
                raw.get("source_language"),
            ):
                value = _text(value)
                if value and value not in languages:
                    languages.append(value)
            current["source_languages"] = languages

    by_overall = {
        int(item["overall_number"]): item
        for item in by_coordinate.values()
        if str(item.get("overall_number") or "").isdigit()
    }
    for raw in idless:
        try:
            overall = int(raw.get("overall_number"))
        except (TypeError, ValueError):
            continue
        current = by_overall.get(overall)
        if current is None:
            continue
        current_date = _text(current.get("air_date"))
        incoming_date = _text(raw.get("air_date"))
        if current_date and incoming_date and current_date != incoming_date:
            current["air_date"] = ""
            current["air_date_conflict"] = True
            fact_conflicts.append(f"overall:{overall}")

    items = []
    for (season, episode), raw in sorted(by_coordinate.items()):
        air_date = _text(raw.get("air_date"))
        items.append({
            **raw,
            "item_id": f"wikipedia:S{season:02d}E{episode:03d}",
            "content_role": "main_episode",
            "season_number": season,
            "episode_number": episode,
            "air_date": air_date,
            "aired": air_date,
            "inventory_source": "wikipedia",
        })
    complete = bool(
        items
        and season_totals
        and len(items) == sum(season_totals.values())
        and all(
            sum(1 for item in items if item["season_number"] == season)
            == total
            for season, total in season_totals.items()
        )
    )
    status = "complete" if complete and not fact_conflicts else (
        "partial" if items else next(
            (
                _text(source.get("status"))
                for source in sources
                if _text(source.get("status")) in {
                    "parse_error", "absent", "unavailable",
                    "timeout", "rate_limited", "server_down",
                }
            ),
            "absent",
        )
    )
    source_revisions = {
        language: int(source.get("revision_id") or 0)
        for source in sources
        if (language := _text(source.get("source_language")))
    }
    return {
        "status": status,
        "items": items,
        "season_totals": season_totals,
        "source_urls": [
            value
            for source in sources
            if (value := _text(source.get("source_url")))
        ],
        "source_revisions": source_revisions,
        "wikibase_item": expected_qid or next(iter(qids), ""),
        "fact_conflicts": sorted(set(fact_conflicts)),
        "error": (
            "wikipedia_fact_conflict"
            if fact_conflicts
            else next(
                (
                    _text(source.get("error"))
                    for source in sources
                    if _text(source.get("error"))
                ),
                "",
            )
        ),
    }
