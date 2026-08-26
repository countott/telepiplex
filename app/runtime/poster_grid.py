"""Build a numbered Telegram candidate poster grid."""

from __future__ import annotations

from collections import Counter
from io import BytesIO
import socket
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from PIL import Image, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError

try:
    import init
except ModuleNotFoundError:  # pragma: no cover - package-imported test/runtime fallback
    from app import init


CARD_WIDTH = 360
CARD_HEIGHT = 540
LABEL_HEIGHT = 72
GAP = 18
MAX_IMAGE_BYTES = 6 * 1024 * 1024
_CIRCLED_NUMBERS = tuple("①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳")


class PosterGridUnavailable(RuntimeError):
    """No supplied remote poster could be decoded into the candidate grid."""


def _number_label(number: int) -> str:
    if 1 <= number <= len(_CIRCLED_NUMBERS):
        return _CIRCLED_NUMBERS[number - 1]
    return str(number)


def _font(size: int):
    for path in (
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _douban_host(host: str) -> bool:
    host = str(host or "").rstrip(".").casefold()
    return (
        host in {"douban.com", "doubanio.com"}
        or host.endswith(".douban.com")
        or host.endswith(".doubanio.com")
    )


def _request_headers(url: str) -> dict[str, str]:
    headers = {
        "User-Agent": "telepiplex/3.4 poster-grid",
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    }
    host = urlsplit(url).hostname or ""
    if _douban_host(host):
        headers.update({
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": "https://movie.douban.com/",
        })
    return headers


def _download_image(
    url: str,
    *,
    timeout: float,
) -> tuple[Image.Image | None, str]:
    if not str(url or "").startswith("https://"):
        return None, "url_missing"
    request = Request(
        url,
        headers=_request_headers(url),
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            final_url = str(response.geturl() or "")
            if not final_url.startswith("https://"):
                return None, "insecure_redirect"
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > MAX_IMAGE_BYTES:
                return None, "image_too_large"
            payload = response.read(MAX_IMAGE_BYTES + 1)
        if len(payload) > MAX_IMAGE_BYTES:
            return None, "image_too_large"
        image = Image.open(BytesIO(payload))
        image.load()
        return image.convert("RGB"), ""
    except HTTPError as exc:
        return None, f"http_status:{int(exc.code or 0)}"
    except (TimeoutError, socket.timeout):
        return None, "timeout"
    except URLError as exc:
        if isinstance(getattr(exc, "reason", None), (TimeoutError, socket.timeout)):
            return None, "timeout"
        return None, "network_error"
    except UnidentifiedImageError:
        return None, "invalid_image"
    except (Image.DecompressionBombError, OSError):
        return None, "image_error"
    except (TypeError, ValueError):
        return None, "invalid_response"
    except Exception as exc:
        return None, f"unexpected:{type(exc).__name__}"


def _wrapped_title(
    draw: ImageDraw.ImageDraw,
    title: str,
    font,
    *,
    max_width: int,
    max_lines: int,
) -> list[str]:
    title = " ".join(str(title or "").split()) or "未知作品"
    lines = []
    current = ""
    for character in title:
        candidate = current + character
        box = draw.textbbox((0, 0), candidate, font=font)
        if current and box[2] - box[0] > max_width:
            lines.append(current.rstrip())
            current = character.lstrip()
        else:
            current = candidate
    if current:
        lines.append(current.rstrip())
    if len(lines) <= max_lines:
        return lines
    lines = lines[:max_lines]
    last = lines[-1].rstrip()
    while last:
        box = draw.textbbox((0, 0), last + "…", font=font)
        if box[2] - box[0] <= max_width:
            break
        last = last[:-1].rstrip()
    lines[-1] = (last + "…") if last else "…"
    return lines


def _placeholder(number: int, title: str) -> Image.Image:
    card = Image.new("RGB", (CARD_WIDTH, CARD_HEIGHT), "#20242b")
    draw = ImageDraw.Draw(card)
    font = _font(46)
    lines = _wrapped_title(
        draw,
        title,
        font,
        max_width=CARD_WIDTH - 48,
        max_lines=4,
    )
    line_boxes = [
        draw.textbbox((0, 0), line, font=font)
        for line in lines
    ]
    line_gap = 14
    total_height = sum(
        box[3] - box[1]
        for box in line_boxes
    ) + line_gap * max(0, len(lines) - 1)
    y = (CARD_HEIGHT - LABEL_HEIGHT - total_height) / 2
    for line, box in zip(lines, line_boxes):
        width = box[2] - box[0]
        height = box[3] - box[1]
        draw.text(
            ((CARD_WIDTH - width) / 2, y - box[1]),
            line,
            fill="#f3f4f6",
            font=font,
        )
        y += height + line_gap
    return card


def _log_download_failure(
    number: int,
    poster_url: str,
    reason: str,
) -> None:
    logger = getattr(init, "logger", None)
    method = (
        getattr(logger, "warning", None)
        or getattr(logger, "warn", None)
        or getattr(logger, "info", None)
    )
    if not callable(method):
        return
    host = (urlsplit(poster_url).hostname or "unknown").casefold()
    method(
        "poster_grid_download_failed "
        f"candidate={number} host={host} reason={reason}"
    )


def _card(
    item: dict,
    *,
    timeout: float,
) -> tuple[Image.Image, bool, bool, tuple[str, str] | None]:
    number = int(item.get("number") or 0)
    title = " ".join(str(item.get("title") or "").split())
    poster_url = str(item.get("poster_url") or "")
    requested = poster_url.startswith("https://")
    image, failure_reason = _download_image(
        poster_url,
        timeout=timeout,
    )
    failure = None
    if requested and image is None:
        host = (urlsplit(poster_url).hostname or "unknown").casefold()
        failure = (host, failure_reason)
        _log_download_failure(number, poster_url, failure_reason)
    if image is None:
        card = _placeholder(number, title)
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
    label = _number_label(number)
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
    return card, image is not None, requested, failure


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
    cards = []
    requested = 0
    successful = 0
    failures = []
    for item in items:
        card, downloaded, remote_requested, failure = _card(
            item,
            timeout=timeout,
        )
        cards.append(card)
        requested += int(remote_requested)
        successful += int(downloaded)
        if failure:
            failures.append(failure)
    if requested and not successful:
        failure_counts = Counter(failures)
        details = ",".join(
            f"{host}:{reason}={count}"
            for (host, reason), count in sorted(failure_counts.items())
        )
        raise PosterGridUnavailable(
            f"poster_grid_no_images failures={details or 'unknown'}"
        )
    grid = Image.new("RGB", (width, height), "#0b0d10")
    for index, card in enumerate(cards):
        x = (index % columns) * (CARD_WIDTH + GAP)
        y = (index // columns) * (CARD_HEIGHT + GAP)
        grid.paste(card, (x, y))
    output = BytesIO()
    grid.save(output, format="JPEG", quality=88, optimize=True)
    output.seek(0)
    output.name = "telepiplex-candidates.jpg"
    return output
