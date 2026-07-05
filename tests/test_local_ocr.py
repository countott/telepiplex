import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))

from app.utils.local_ocr import LocalOCRError, extract_text_from_image


class LocalOCRTest(unittest.TestCase):
    def test_extract_text_from_image_prefers_paddleocr_by_default(self):
        class FakePaddleOCR:
            init_kwargs = None

            def __init__(self, **kwargs):
                FakePaddleOCR.init_kwargs = kwargs

            def predict(self, input):
                self.input = input
                return [{"rec_texts": ["布达佩斯大饭店", "The Grand Budapest Hotel", "2014"]}]

        fake_module = types.ModuleType("paddleocr")
        fake_module.PaddleOCR = FakePaddleOCR

        with patch.dict(sys.modules, {"paddleocr": fake_module}), patch("app.utils.local_ocr.subprocess.run") as run_mock:
            text = extract_text_from_image("/tmp/screenshot.jpg")

        self.assertEqual(text, "布达佩斯大饭店\nThe Grand Budapest Hotel\n2014")
        self.assertFalse(run_mock.called)
        self.assertEqual(
            FakePaddleOCR.init_kwargs,
            {
                "use_doc_orientation_classify": False,
                "use_doc_unwarping": False,
                "use_textline_orientation": False,
            },
        )

    @patch("app.utils.local_ocr.subprocess.run")
    def test_extract_text_from_image_can_use_tesseract_stdout(self, run_mock):
        run_mock.return_value.stdout = "The Grand Budapest Hotel\n2014\n"
        run_mock.return_value.stderr = ""

        text = extract_text_from_image("/tmp/screenshot.jpg", engine="tesseract")

        self.assertEqual(text, "The Grand Budapest Hotel\n2014")
        run_mock.assert_called_once()
        command = run_mock.call_args.args[0]
        self.assertEqual(command[:3], ["tesseract", "/tmp/screenshot.jpg", "stdout"])
        self.assertIn("-l", command)
        self.assertIn("eng+chi_sim", command)

    @patch("app.utils.local_ocr.subprocess.run", side_effect=FileNotFoundError)
    def test_extract_text_from_image_raises_readable_error_when_tesseract_missing(self, run_mock):
        with self.assertRaisesRegex(LocalOCRError, "tesseract 未安装"):
            extract_text_from_image("/tmp/screenshot.jpg", engine="tesseract")

    @patch("app.utils.local_ocr.subprocess.run")
    def test_extract_text_from_image_falls_back_to_tesseract_when_paddleocr_is_missing(self, run_mock):
        run_mock.return_value.stdout = "The Grand Budapest Hotel\n2014\n"
        run_mock.return_value.stderr = ""

        with patch.dict(sys.modules, {"paddleocr": None}):
            text = extract_text_from_image("/tmp/screenshot.jpg")

        self.assertEqual(text, "The Grand Budapest Hotel\n2014")

    @patch("app.utils.local_ocr.subprocess.run")
    def test_extract_text_from_image_falls_back_when_paddleocr_initialization_fails(self, run_mock):
        class BrokenPaddleOCR:
            def __init__(self, **kwargs):
                raise RuntimeError("model download failed")

        fake_module = types.ModuleType("paddleocr")
        fake_module.PaddleOCR = BrokenPaddleOCR
        run_mock.return_value.stdout = "The Grand Budapest Hotel\n2014\n"
        run_mock.return_value.stderr = ""

        with patch.dict(sys.modules, {"paddleocr": fake_module}):
            text = extract_text_from_image("/tmp/screenshot.jpg")

        self.assertEqual(text, "The Grand Budapest Hotel\n2014")


if __name__ == "__main__":
    unittest.main()
