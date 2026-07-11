# -*- coding: utf-8 -*-

from __future__ import annotations

import requests


class FanartAdapter:
    API_BASE = "https://webservice.fanart.tv/v3"

    def __init__(self, api_key, timeout=15):
        self.api_key = str(api_key or "")
        self.timeout = int(timeout)

    def textless_posters(self, media_type, external_ids):
        is_movie = str(media_type).lower() == "movie"
        resource = "movies" if is_movie else "tv"
        media_id = external_ids.get("tmdb") if is_movie else external_ids.get("tvdb")
        if not media_id:
            return []
        response = requests.get(
            f"{self.API_BASE}/{resource}/{media_id}",
            params={"api_key": self.api_key},
            timeout=self.timeout,
        )
        response.raise_for_status()
        key = "movieposter" if is_movie else "tvposter"
        return [
            dict(poster)
            for poster in response.json().get(key, [])
            if poster.get("lang") == "00"
        ]
