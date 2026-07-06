import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))

from app.utils.media_metadata import (
    build_external_metadata,
    build_search_metadata,
)


class MediaMetadataTest(unittest.TestCase):
    def test_build_search_metadata_keeps_ids_titles_and_query(self):
        metadata = build_search_metadata(
            source="douban",
            media_type="series",
            chinese_title="嗜血法医",
            english_title="Dexter",
            year="2006",
            query="Dexter 2006",
            original_url="https://movie.douban.com/subject/1234567/",
            external_ids={"douban_subject": "1234567"},
            evidence=[{"source": "douban", "field": "subject"}],
        )

        self.assertEqual(
            metadata,
            {
                "source": "douban",
                "media_type": "series",
                "chinese_title": "嗜血法医",
                "english_title": "Dexter",
                "year": "2006",
                "query": "Dexter 2006",
                "original_url": "https://movie.douban.com/subject/1234567/",
                "external_ids": {"douban_subject": "1234567"},
                "evidence": [{"source": "douban", "field": "subject"}],
            },
        )

    def test_build_external_metadata_uses_english_title_year_query(self):
        metadata = build_external_metadata(
            source="imdb",
            title="Dexter",
            year="2006",
            external_id="tt0773262",
            original_url="https://www.imdb.com/title/tt0773262/",
        )

        self.assertEqual(metadata["query"], "Dexter 2006")
        self.assertEqual(metadata["external_ids"], {"imdb": "tt0773262"})
        self.assertEqual(metadata["evidence"][0]["source"], "imdb")


if __name__ == "__main__":
    unittest.main()
