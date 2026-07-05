# -*- coding: utf-8 -*-

from __future__ import annotations

import os
import subprocess
import tempfile
from collections.abc import Iterable


class LocalOCRError(Exception):
    """Raised when local OCR cannot extract text from an image."""


def _get_runtime_ocr_config() -> dict:
    try:
        import init
    except Exception:
        return {}

    return ((getattr(init, "bot_config", {}) or {}).get("search") or {}).get("ocr") or {}


def _prepare_image_for_ocr(image_path: str, scale: int = 2) -> tuple[str, bool]:
    try:
        from PIL import Image, ImageFilter, ImageOps
    except Exception:
        return image_path, False

    try:
        image = Image.open(image_path).convert("RGB")
        if scale > 1:
            image = image.resize((image.width * scale, image.height * scale), Image.Resampling.LANCZOS)
        image = ImageOps.autocontrast(image)
        image = image.filter(ImageFilter.SHARPEN)

        tmp_file = tempfile.NamedTemporaryFile(prefix="ocr_enhanced_", suffix=".png", delete=False)
        tmp_file.close()
        image.save(tmp_file.name)
        return tmp_file.name, True
    except Exception:
        return image_path, False


def _extract_texts_from_paddle_result(result) -> list[str]:
    texts = []

    def visit(value):
        if value is None:
            return

        if isinstance(value, str):
            text = " ".join(value.split())
            if text:
                texts.append(text)
            return

        if isinstance(value, dict):
            for key in ("rec_texts", "texts", "text", "transcription"):
                if key in value:
                    visit(value[key])
                    return
            for nested in value.values():
                visit(nested)
            return

        json_value = getattr(value, "json", None)
        if callable(json_value):
            try:
                visit(json_value())
                return
            except Exception:
                pass
        elif isinstance(json_value, dict):
            visit(json_value)
            return

        if isinstance(value, tuple) and len(value) >= 2:
            candidate = value[1]
            if isinstance(candidate, (list, tuple)) and candidate and isinstance(candidate[0], str):
                visit(candidate[0])
                return

        if isinstance(value, Iterable) and not isinstance(value, (bytes, bytearray)):
            for nested in value:
                visit(nested)

    visit(result)
    deduped = []
    for text in texts:
        if text not in deduped:
            deduped.append(text)
    return deduped


def _extract_text_with_paddleocr(image_path: str, config: dict) -> str:
    try:
        from paddleocr import PaddleOCR
    except Exception as e:
        raise LocalOCRError("PaddleOCR 未安装，无法使用高质量截图识别。") from e

    init_kwargs = {
        "use_doc_orientation_classify": False,
        "use_doc_unwarping": False,
        "use_textline_orientation": False,
    }
    device = str(config.get("device") or "").strip()
    if device:
        init_kwargs["device"] = device

    try:
        ocr = PaddleOCR(**init_kwargs)
        result = ocr.predict(input=image_path)
    except AttributeError:
        result = ocr.ocr(image_path, cls=False)
    except Exception as e:
        raise LocalOCRError(f"PaddleOCR 识别失败: {e}") from e

    texts = _extract_texts_from_paddle_result(result)
    if not texts:
        raise LocalOCRError("PaddleOCR 未识别到文本，请裁剪标题区域后重试。")
    return "\n".join(texts)


def _extract_text_with_tesseract(image_path: str, languages: str = "eng+chi_sim", timeout: int = 30) -> str:
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


def extract_text_from_image(
    image_path: str,
    engine: str | None = None,
    languages: str = "eng+chi_sim",
    timeout: int = 30,
) -> str:
    config = _get_runtime_ocr_config()
    selected_engine = (engine or config.get("engine") or "paddleocr").lower()
    fallback_engine = str(config.get("fallback_engine") or "tesseract").lower()
    preprocess = config.get("preprocess", True)

    ocr_image_path = image_path
    should_remove_ocr_image = False
    if preprocess:
        ocr_image_path, should_remove_ocr_image = _prepare_image_for_ocr(image_path, int(config.get("scale", 2) or 2))

    try:
        if selected_engine == "paddleocr":
            try:
                return _extract_text_with_paddleocr(ocr_image_path, config).strip()
            except LocalOCRError:
                if fallback_engine and fallback_engine != "paddleocr":
                    return _extract_text_with_tesseract(ocr_image_path, languages, timeout)
                raise

        if selected_engine == "tesseract":
            return _extract_text_with_tesseract(ocr_image_path, languages, timeout)

        raise LocalOCRError(f"未知 OCR 引擎: {selected_engine}")
    finally:
        if should_remove_ocr_image and os.path.exists(ocr_image_path):
            os.remove(ocr_image_path)
