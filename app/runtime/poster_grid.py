"""Build a numbered Telegram candidate poster grid."""

from __future__ import annotations

from io import BytesIO
from urllib.request import Request, urlopen

from PIL import Image, ImageDraw, ImageFont, ImageOps


CARD_WIDTH = 360
CARD_HEIGHT = 540
LABEL_HEIGHT = 72
GAP = 18
MAX_IMAGE_BYTES = 6 * 1024 * 1024


def _font(size: int):
    for path in (
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _download_image(url: str, *, timeout: float) -> Image.Image | None:
    if not str(url or "").startswith("https://"):
        return None
    request = Request(
        url,
        headers={"User-Agent": "telepiplex/3.4 poster-grid"},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            final_url = str(response.geturl() or "")
            if not final_url.startswith("https://"):
                return None
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > MAX_IMAGE_BYTES:
                return None
            payload = response.read(MAX_IMAGE_BYTES + 1)
        if len(payload) > MAX_IMAGE_BYTES:
            return None
        image = Image.open(BytesIO(payload))
        image.load()
        return image.convert("RGB")
    except Exception:
        return None


def _placeholder(number: int) -> Image.Image:
    card = Image.new("RGB", (CARD_WIDTH, CARD_HEIGHT), "#20242b")
    draw = ImageDraw.Draw(card)
    font = _font(112)
    label = str(number)
    box = draw.textbbox((0, 0), label, font=font)
    draw.text(
        (
            (CARD_WIDTH - (box[2] - box[0])) / 2,
            (CARD_HEIGHT - LABEL_HEIGHT - (box[3] - box[1])) / 2,
        ),
        label,
        fill="#f3f4f6",
        font=font,
    )
    return card


def _card(item: dict, *, timeout: float) -> Image.Image:
    number = int(item.get("number") or 0)
    image = _download_image(
        str(item.get("poster_url") or ""),
        timeout=timeout,
    )
    if image is None:
        card = _placeholder(number)
    else:
        card = ImageOps.fit(
            image,
            (CARD_WIDTH, CARD_HEIGHT - LABEL_HEIGHT),
            method=Image.Resampling.LANCZOS,
        )
        canvas = Image.new("RGB", (CARD_WIDTH, CARD_HEIGHT), "#111318")
        canvas.paste(card, (0, 0))
        card = canvas
    draw = ImageDraw.Draw(card)
    draw.rectangle(
        (0, CARD_HEIGHT - LABEL_HEIGHT, CARD_WIDTH, CARD_HEIGHT),
        fill="#111318",
    )
    font = _font(44)
    label = str(number)
    box = draw.textbbox((0, 0), label, font=font)
    draw.text(
        (
            (CARD_WIDTH - (box[2] - box[0])) / 2,
            CARD_HEIGHT - LABEL_HEIGHT + 8,
        ),
        label,
        fill="#ffffff",
        font=font,
    )
    return card


def build_poster_grid(
    poster_items: list[dict],
    *,
    timeout: float = 10,
) -> BytesIO:
    if not isinstance(poster_items, list) or not 1 <= len(poster_items) <= 6:
        raise ValueError("poster_grid_item_count_invalid")
    items = [
        dict(item)
        for item in poster_items
        if isinstance(item, dict)
    ]
    if len(items) != len(poster_items):
        raise ValueError("poster_grid_item_count_invalid")
    columns = 1 if len(items) == 1 else 2 if len(items) <= 4 else 3
    rows = (len(items) + columns - 1) // columns
    width = columns * CARD_WIDTH + (columns - 1) * GAP
    height = rows * CARD_HEIGHT + (rows - 1) * GAP
    grid = Image.new("RGB", (width, height), "#0b0d10")
    for index, item in enumerate(items):
        x = (index % columns) * (CARD_WIDTH + GAP)
        y = (index // columns) * (CARD_HEIGHT + GAP)
        grid.paste(_card(item, timeout=timeout), (x, y))
    output = BytesIO()
    grid.save(output, format="JPEG", quality=88, optimize=True)
    output.seek(0)
    output.name = "telepiplex-candidates.jpg"
    return output
