# -*- coding: utf-8 -*-

from __future__ import annotations

import requests


class TmdbAdapter:
    API_BASE = "https://api.themoviedb.org/3"
    IMAGE_BASE = "https://image.tmdb.org/t/p/original"

    def __init__(self, api_key, timeout=15):
        self.api_key = str(api_key or "")
        self.timeout = int(timeout)

    @property
    def _headers(self):
        return {"Authorization": f"Bearer {self.api_key}"}

    def details(self, media_type, tmdb_id):
        response = requests.get(
            f"{self.API_BASE}/{media_type}/{tmdb_id}",
            headers=self._headers,
            timeout=self.timeout,
        )
        response.raise_for_status()
        return dict(response.json())

    def textless_posters(self, media_type, tmdb_id):
        response = requests.get(
            f"{self.API_BASE}/{media_type}/{tmdb_id}/images",
            headers=self._headers,
            params={"include_image_language": "null"},
            timeout=self.timeout,
        )
        response.raise_for_status()
        posters = []
        for raw in response.json().get("posters", []):
            if raw.get("iso_639_1") is not None:
                continue
            poster = dict(raw)
            poster["url"] = f"{self.IMAGE_BASE}{poster.get('file_path', '')}"
            posters.append(poster)
        return posters
