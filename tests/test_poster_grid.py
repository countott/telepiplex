import hashlib
from io import BytesIO
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from PIL import Image

from app.runtime import poster_grid
from app.runtime.poster_grid import (
    CARD_HEIGHT,
    CARD_WIDTH,
    GAP,
    build_poster_grid,
)


class ImageResponse:
    def __init__(self, payload: bytes, url: str):
        self.payload = payload
        self.url = url
        self.headers = {}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def geturl(self):
        return self.url

    def read(self, limit: int):
        return self.payload[:limit]


def jpeg_payload() -> bytes:
    output = BytesIO()
    Image.new("RGB", (32, 48), "#4c6fff").save(output, format="JPEG")
    return output.getvalue()


class PosterGridTest(unittest.TestCase):
    def test_identity_poster_upload_has_no_candidate_number_or_strip(self):
        with patch.object(poster_grid, "urlopen", return_value=ImageResponse(
            jpeg_payload(), "https://img.example/selected.jpg",
        )), patch.object(poster_grid, "_number_label", side_effect=AssertionError("candidate label")):
            poster = poster_grid.build_identity_poster("Selected", "https://img.example/selected.jpg")
        self.assertEqual(Image.open(poster).size, (32, 48))
        self.assertEqual(poster.name, "telepiplex-identity.jpg")

    def test_missing_identity_poster_uses_selected_title_without_number(self):
        with patch.object(poster_grid, "_number_label", side_effect=AssertionError("candidate label")):
            first = poster_grid.build_identity_poster("Selected movie")
            second = poster_grid.build_identity_poster("Different movie")
        self.assertNotEqual(first.getvalue(), second.getvalue())

    def test_blocked_identity_poster_is_a_valid_selected_title_image(self):
        with patch.object(poster_grid, "urlopen", side_effect=HTTPError(
            "https://img.example/selected.jpg", 403, "Forbidden", {}, None,
        )), patch.object(poster_grid, "_number_label", side_effect=AssertionError("candidate number")):
            poster = poster_grid.build_identity_poster("所选电影", "https://img.example/selected.jpg")
        self.assertEqual(poster.getvalue(), poster_grid.build_identity_poster("所选电影").getvalue())
        image = Image.open(poster)
        image.verify()
        self.assertEqual(image.format, "JPEG")

    def test_selected_font_has_distinct_chinese_glyphs(self):
        font = poster_grid._font(48)

        self.assertNotEqual(
            hashlib.sha256(bytes(font.getmask("想"))).hexdigest(),
            hashlib.sha256(bytes(font.getmask("见"))).hexdigest(),
        )

    def test_missing_poster_placeholder_uses_candidate_title(self):
        first = build_poster_grid([{
            "number": 1,
            "title": "想见你",
            "poster_url": "",
        }])
        second = build_poster_grid([{
            "number": 1,
            "title": "另一部作品",
            "poster_url": "",
        }])

        first_image = Image.open(first)
        second_image = Image.open(second)
        self.assertNotEqual(
            hashlib.sha256(first_image.tobytes()).hexdigest(),
            hashlib.sha256(second_image.tobytes()).hexdigest(),
        )
        self.assertEqual(
            first_image.size,
            (CARD_WIDTH, CARD_HEIGHT),
        )
        self.assertEqual(first_image.format, "JPEG")
        self.assertEqual(first.name, "telepiplex-candidates.jpg")

    def test_candidate_numbers_are_drawn_as_circled_labels(self):
        drawn_text = []
        original_text = poster_grid.ImageDraw.ImageDraw.text

        def capture_text(draw, xy, text, *args, **kwargs):
            drawn_text.append(text)
            return original_text(draw, xy, text, *args, **kwargs)

        with patch.object(
            poster_grid.ImageDraw.ImageDraw,
            "text",
            new=capture_text,
        ):
            build_poster_grid([
                {"number": 1, "title": "作品甲", "poster_url": ""},
                {"number": 2, "title": "作品乙", "poster_url": ""},
            ])

        self.assertEqual(
            [text for text in drawn_text if text in {"1", "2", "①", "②"}],
            ["①", "②"],
        )

    @patch("app.runtime.poster_grid.urlopen")
    def test_douban_poster_request_uses_provider_headers(self, urlopen):
        urlopen.return_value = ImageResponse(
            jpeg_payload(),
            "https://img9.doubanio.com/view/photo/poster/public/p1.jpg",
        )

        build_poster_grid([{
            "number": 1,
            "title": "想见你",
            "poster_url": (
                "https://img9.doubanio.com/view/photo/poster/public/p1.jpg"
            ),
        }])

        request = urlopen.call_args.args[0]
        self.assertEqual(
            request.get_header("Referer"),
            "https://movie.douban.com/",
        )
        self.assertIn("image/", request.get_header("Accept"))
        self.assertIn("zh-CN", request.get_header("Accept-language"))

    @patch("app.runtime.poster_grid.urlopen")
    def test_partial_download_failure_still_returns_grid(self, urlopen):
        urlopen.side_effect = [
            ImageResponse(
                jpeg_payload(),
                "https://img.example/one.jpg",
            ),
            HTTPError(
                "https://img.example/two.jpg",
                403,
                "Forbidden",
                {},
                None,
            ),
        ]

        output = build_poster_grid([
            {
                "number": 1,
                "title": "作品一",
                "poster_url": "https://img.example/one.jpg",
            },
            {
                "number": 2,
                "title": "作品二",
                "poster_url": "https://img.example/two.jpg",
            },
        ])

        image = Image.open(output)
        self.assertEqual(
            image.size,
            (CARD_WIDTH * 2 + GAP, CARD_HEIGHT),
        )

    @patch("app.runtime.poster_grid.urlopen")
    def test_all_remote_downloads_failing_raise_sanitized_error(self, urlopen):
        urlopen.side_effect = HTTPError(
            "https://img9.doubanio.com/private/poster.jpg",
            403,
            "Forbidden",
            {},
            None,
        )

        logger = Mock()
        with patch.object(poster_grid.init, "logger", logger):
            with self.assertRaises(RuntimeError) as caught:
                build_poster_grid([{
                    "number": 1,
                    "title": "想见你",
                    "poster_url": (
                        "https://img9.doubanio.com/private/poster.jpg"
                    ),
                }])

        self.assertEqual(
            type(caught.exception).__name__,
            "PosterGridUnavailable",
        )
        self.assertIn("http_status:403", str(caught.exception))
        self.assertNotIn("https://", str(caught.exception))
        self.assertTrue(
            hasattr(poster_grid, "PosterGridUnavailable"),
        )
        logged = logger.warning.call_args.args[0]
        self.assertIn("host=img9.doubanio.com", logged)
        self.assertIn("reason=http_status:403", logged)
        self.assertNotIn("https://", logged)

    def test_grid_rejects_more_than_six_items(self):
        with self.assertRaisesRegex(ValueError, "poster_grid_item_count_invalid"):
            build_poster_grid([
                {"number": index, "title": str(index), "poster_url": ""}
                for index in range(1, 8)
            ])


if __name__ == "__main__":
    unittest.main()
