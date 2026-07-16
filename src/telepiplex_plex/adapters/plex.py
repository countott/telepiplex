# -*- coding: utf-8 -*-

from __future__ import annotations

from plexapi.server import PlexServer


class PlexAdapter:
    def __init__(self, base_url, token, timeout=30):
        self.server = PlexServer(
            str(base_url or "").rstrip("/"),
            str(token or ""),
            timeout=int(timeout),
        )

    def server_status(self):
        return {
            "online": True,
            "name": str(self._value(self.server, "friendlyName", "") or ""),
            "version": str(self._value(self.server, "version", "") or ""),
            "machine_identifier": str(
                self._value(self.server, "machineIdentifier", "") or ""
            ),
        }

    def scan_library(self, library_id):
        self.server.library.sectionByID(int(library_id)).update()

    def list_libraries(self):
        libraries = []
        for section in self.server.library.sections():
            locations = getattr(section, "locations", []) or []
            libraries.append(
                {
                    "id": str(self._value(section, "key", "") or ""),
                    "title": str(self._value(section, "title", "") or ""),
                    "media_type": str(self._value(section, "type", "") or ""),
                    "locations": [str(path) for path in locations],
                }
            )
        return libraries

    def snapshot_recent(self, library_id, limit=50):
        section = self.server.library.sectionByID(int(library_id))
        return {
            str(self._value(item, "ratingKey", "") or "")
            for item in section.recentlyAdded(maxresults=int(limit))
            if self._value(item, "ratingKey", "")
        }

    def locate_candidates(self, library_id, before_rating_keys=None, limit=50):
        section = self.server.library.sectionByID(int(library_id))
        before = {str(key) for key in (before_rating_keys or set())}
        return [
            self._item_dict(item)
            for item in section.recentlyAdded(maxresults=int(limit))
            if str(self._value(item, "ratingKey", "") or "") not in before
        ]

    def find_item_by_path(self, library_id, final_path):
        section = self.server.library.sectionByID(int(library_id))
        section_type = str(self._value(section, "type", "") or "")
        if section_type == "movie":
            library_types = ("movie",)
        elif section_type == "show":
            library_types = ("episode",)
        else:
            library_types = ("movie", "episode")
        matches = []
        for library_type in library_types:
            for candidate in section.search(libtype=library_type):
                item = self._item_dict(candidate)
                if any(
                    self._media_path_matches(part.get("file"), final_path)
                    for part in item.get("parts") or []
                ):
                    matches.append(item)
        return matches[0] if len(matches) == 1 else None

    @staticmethod
    def _media_path_matches(actual_path, expected_path):
        actual = str(actual_path or "").replace("\\", "/").rstrip("/")
        expected = str(expected_path or "").replace("\\", "/").rstrip("/")
        if not actual or not expected:
            return False
        return actual == expected or actual.endswith(expected) or f"{expected}/" in actual

    def find_movie(self, library_id, *, title="", year="", expected_final_paths=()):
        section = self.server.library.sectionByID(int(library_id))
        kwargs = {"libtype": "movie"}
        if str(year or "").strip():
            kwargs["year"] = int(year)
        try:
            candidates = section.search(title=str(title or "") or None, **kwargs)
        except Exception:
            return None
        expected = [str(path) for path in expected_final_paths or [] if str(path or "").strip()]
        matches = []
        for candidate in candidates:
            item = self._item_dict(candidate)
            actual_paths = [part.get("file") for part in item.get("parts") or []]
            if expected and not any(
                self._media_path_matches(actual, wanted)
                for actual in actual_paths for wanted in expected
            ):
                continue
            matches.append(item)
        return matches[0] if len(matches) == 1 else None

    def find_series_episode(
        self,
        library_id,
        *,
        tvdb_series_id="",
        title="",
        year="",
        season_number=0,
        episode_number=0,
        expected_final_paths=(),
    ):
        section = self.server.library.sectionByID(int(library_id))
        try:
            if str(tvdb_series_id or "").strip():
                show = section.getGuid(f"tvdb://{str(tvdb_series_id).strip()}")
            else:
                kwargs = {"libtype": "show"}
                if str(year or "").strip():
                    kwargs["year"] = int(year)
                shows = section.search(title=str(title or "") or None, **kwargs)
                if len(shows) != 1:
                    return None
                show = shows[0]
            episode = show.episode(
                season=int(season_number),
                episode=int(episode_number),
            )
        except Exception:
            return None

        item = self._item_dict(episode)
        expected = [
            str(path).replace("\\", "/").rstrip("/")
            for path in expected_final_paths
            if str(path or "").strip()
        ]
        actual = [
            str(part.get("file") or "").replace("\\", "/").rstrip("/")
            for part in item.get("parts") or []
        ]
        if expected and not any(
            self._media_path_matches(actual_path, expected_path)
            for actual_path in actual
            for expected_path in expected
        ):
            return None
        return item

    @staticmethod
    def _value(obj, name, default=None):
        value = getattr(obj, name, default)
        return value if isinstance(value, (str, int, float, bool)) or value is None else default

    @classmethod
    def _stream_dict(cls, stream, *, external=False):
        key = cls._value(stream, "key", "")
        return {
            "id": int(cls._value(stream, "id", 0) or 0),
            "language_code": str(cls._value(stream, "languageCode", "") or "").lower(),
            "codec": str(cls._value(stream, "codec", "") or "").lower(),
            "codec_profile": str(cls._value(stream, "profile", "") or ""),
            "display_title": str(cls._value(stream, "displayTitle", "") or ""),
            "channels": int(cls._value(stream, "channels", 0) or 0),
            "bitrate": int(cls._value(stream, "bitrate", 0) or 0),
            "selected": bool(cls._value(stream, "selected", False)),
            "external": bool(key) if external else False,
            "transient": bool(cls._value(stream, "transient", False)),
        }

    @classmethod
    def _part_dict(cls, part):
        return {
            "id": int(cls._value(part, "id", 0) or 0),
            "file": str(cls._value(part, "file", "") or ""),
            "audio_streams": [
                cls._stream_dict(stream)
                for stream in part.audioStreams()
            ],
            "subtitle_streams": [
                cls._stream_dict(stream, external=True)
                for stream in part.subtitleStreams()
            ],
        }

    @classmethod
    def _item_dict(cls, item):
        guids = [
            str(cls._value(guid, "id", "") or "")
            for guid in getattr(item, "guids", []) or []
        ]
        parts = []
        for media in getattr(item, "media", []) or []:
            parts.extend(cls._part_dict(part) for part in getattr(media, "parts", []) or [])
        return {
            "rating_key": str(cls._value(item, "ratingKey", "") or ""),
            "parent_rating_key": str(
                cls._value(item, "parentRatingKey", "") or ""
            ),
            "grandparent_rating_key": str(
                cls._value(item, "grandparentRatingKey", "") or ""
            ),
            "title": str(cls._value(item, "title", "") or ""),
            "original_title": str(cls._value(item, "originalTitle", "") or ""),
            "year": int(cls._value(item, "year", 0) or 0),
            "media_type": str(cls._value(item, "type", "") or ""),
            "summary": str(cls._value(item, "summary", "") or ""),
            "guids": [guid for guid in guids if guid],
            "parts": parts,
        }

    def get_item(self, rating_key):
        return self._item_dict(self.server.fetchItem(int(rating_key)))

    @classmethod
    def _poster_dict(cls, poster):
        return {
            "key": str(cls._value(poster, "key", "") or ""),
            "provider": str(cls._value(poster, "provider", "") or ""),
            "rating_key": str(cls._value(poster, "ratingKey", "") or ""),
            "thumb": str(cls._value(poster, "thumb", "") or ""),
            "selected": bool(cls._value(poster, "selected", False)),
        }

    def list_posters(self, rating_key):
        item = self.server.fetchItem(int(rating_key))
        return [self._poster_dict(poster) for poster in item.posters()]

    def set_poster_url(self, rating_key, url):
        item = self.server.fetchItem(int(rating_key))
        item.uploadPoster(url=str(url))
        return self._item_dict(item.reload())

    def list_streams(self, rating_key):
        return self.get_item(rating_key)["parts"]

    @staticmethod
    def _find_part(item, part_id):
        for media in getattr(item, "media", []) or []:
            for part in getattr(media, "parts", []) or []:
                if int(getattr(part, "id", 0) or 0) == int(part_id):
                    return part
        raise LookupError(f"Plex media part not found: {part_id}")

    def select_audio(self, rating_key, part_id, stream_id):
        item = self.server.fetchItem(int(rating_key))
        part = self._find_part(item, part_id)
        stream = next(
            stream
            for stream in part.audioStreams()
            if int(getattr(stream, "id", 0) or 0) == int(stream_id)
        )
        part.setSelectedAudioStream(stream)

    def select_subtitle(self, rating_key, part_id, stream_id):
        item = self.server.fetchItem(int(rating_key))
        part = self._find_part(item, part_id)
        stream = next(
            stream
            for stream in part.subtitleStreams()
            if int(getattr(stream, "id", 0) or 0) == int(stream_id)
        )
        part.setSelectedSubtitleStream(stream)
