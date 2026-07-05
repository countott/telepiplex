import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))

from app.utils.local_ocr import LocalOCRError, extract_text_from_image


class LocalOCRTest(unittest.TestCase):
    @patch("app.utils.local_ocr.subprocess.run")
    def test_extract_text_from_image_calls_tesseract_stdout(self, run_mock):
        run_mock.return_value.stdout = "The Grand Budapest Hotel\n2014\n"
        run_mock.return_value.stderr = ""

        text = extract_text_from_image("/tmp/screenshot.jpg")

        self.assertEqual(text, "The Grand Budapest Hotel\n2014")
        run_mock.assert_called_once()
        command = run_mock.call_args.args[0]
        self.assertEqual(command[:3], ["tesseract", "/tmp/screenshot.jpg", "stdout"])
        self.assertIn("-l", command)
        self.assertIn("eng+chi_sim", command)

    @patch("app.utils.local_ocr.subprocess.run", side_effect=FileNotFoundError)
    def test_extract_text_from_image_raises_readable_error_when_tesseract_missing(self, run_mock):
        with self.assertRaisesRegex(LocalOCRError, "tesseract 未安装"):
            extract_text_from_image("/tmp/screenshot.jpg")


if __name__ == "__main__":
    unittest.main()
