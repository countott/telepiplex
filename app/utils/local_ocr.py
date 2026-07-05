# -*- coding: utf-8 -*-

import subprocess


class LocalOCRError(Exception):
    """Raised when local OCR cannot extract text from an image."""


def extract_text_from_image(image_path: str, languages: str = "eng+chi_sim", timeout: int = 30) -> str:
    command = [
        "tesseract",
        image_path,
        "stdout",
        "-l",
        languages,
        "--psm",
        "6",
    ]

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=True,
        )
    except FileNotFoundError as e:
        raise LocalOCRError("tesseract 未安装，无法识别截图。请在运行环境安装 tesseract-ocr。") from e
    except subprocess.TimeoutExpired as e:
        raise LocalOCRError("截图 OCR 超时，请裁剪后重试。") from e
    except subprocess.CalledProcessError as e:
        detail = (e.stderr or str(e)).strip()
        raise LocalOCRError(f"截图 OCR 失败: {detail}") from e

    return (result.stdout or "").strip()
