import unittest

from PIL import Image

from app.runtime.poster_grid import (
    CARD_HEIGHT,
    CARD_WIDTH,
    GAP,
    build_poster_grid,
)


class PosterGridTest(unittest.TestCase):
    def test_missing_posters_render_numbered_placeholder_grid(self):
        output = build_poster_grid([
            {"number": 1, "title": "One", "poster_url": ""},
            {"number": 2, "title": "Two", "poster_url": ""},
            {"number": 3, "title": "Three", "poster_url": ""},
        ])

        image = Image.open(output)
        self.assertEqual(
            image.size,
            (CARD_WIDTH * 2 + GAP, CARD_HEIGHT * 2 + GAP),
        )
        self.assertEqual(image.format, "JPEG")
        self.assertEqual(output.name, "telepiplex-candidates.jpg")

    def test_grid_rejects_more_than_six_items(self):
        with self.assertRaisesRegex(ValueError, "poster_grid_item_count_invalid"):
            build_poster_grid([
                {"number": index, "title": str(index), "poster_url": ""}
                for index in range(1, 8)
            ])


if __name__ == "__main__":
    unittest.main()
