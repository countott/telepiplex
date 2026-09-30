"""Bounded, memory-only extraction of subtitle downloads.

Never writes untrusted archive paths to disk. Nested archives and attachments
are skipped; encrypted archives require the user to obtain an accessible copy.
"""
from __future__ import annotations

import gzip
import io
import json
import math
import os
import re
import selectors
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import zipfile
import zlib
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath

from .models import SubtitleDocument

_EXTENSIONS = {".ass", ".ssa", ".srt"}


class ArchiveError(ValueError):
    pass


@dataclass(frozen=True)
class ExtractionLimits:
    max_download_bytes: int = 32 * 1024 * 1024
    max_file_bytes: int = 8 * 1024 * 1024
    max_total_bytes: int = 64 * 1024 * 1024
    max_members: int = 300
    max_compression_ratio: int = 200
    max_decompression_seconds: float = 30.0


def _safe_name(name: str) -> str:
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    if (not normalized or len(normalized.encode("utf-8")) > 4096 or normalized.startswith("/") or ".." in normalized.split("/")
            or re.match(r"^[A-Za-z]:", normalized) or "\x00" in normalized or any(ord(char) < 32 for char in normalized)):
        raise ArchiveError("unsafe_archive_path")
    return str(path)


def _subtitle(name: str) -> bool:
    return PurePosixPath(name).suffix.casefold() in _EXTENSIONS and not PurePosixPath(name).name.startswith("._")


def _document(name: str, content: bytes) -> SubtitleDocument:
    return SubtitleDocument(name, PurePosixPath(name).suffix.casefold().lstrip("."), content)


def _read_bounded(stream, limit: int) -> bytes:
    result = stream.read(limit + 1)
    if len(result) > limit:
        raise ArchiveError("archive_size_limit")
    return result


def _check_metadata(members: list[tuple[str, int, int]], limits: ExtractionLimits, download_size: int,
                    *, dense_ass: bool = False) -> bool:
    if len(members) > limits.max_members:
        raise ArchiveError("archive_member_limit")
    total = 0
    high_ratio = False
    names: set[str] = set()
    for name, unpacked, packed in members:
        safe = _safe_name(name)
        if safe.casefold() in names:
            raise ArchiveError("duplicate_archive_path")
        names.add(safe.casefold())
        if unpacked < 0 or (_subtitle(name) and unpacked > limits.max_file_bytes):
            raise ArchiveError("archive_size_limit")
        total += unpacked
        if packed > 0 and unpacked / packed > limits.max_compression_ratio:
            high_ratio = True
    if total > limits.max_total_bytes:
        raise ArchiveError("archive_size_limit")
    if total / max(1, download_size) > limits.max_compression_ratio:
        high_ratio = True
    if high_ratio:
        # A narrowly bounded ASS-only path handles highly repetitive vector
        # effects. It is used only by the isolated 7z decoder, never by ZIP,
        # gzip, RAR, mixed archives, or arbitrary renamed binary content.
        allowed = (dense_ass and limits.max_compression_ratio >= 200 and len(members) <= 64 and total <= 64 * 1024 * 1024
                   and all(not size or PurePosixPath(name).suffix.casefold() in {".ass", ".ssa"}
                           for name, size, _ in members)
                   and all(size <= 8 * 1024 * 1024 for _, size, _ in members)
                   and total / max(1, download_size) <= 1024
                   and all(not packed or size / packed <= 1024 for _, size, packed in members))
        if not allowed:
            raise ArchiveError("archive_compression_ratio")
    return high_ratio


def _zip(content: bytes, limits: ExtractionLimits) -> list[SubtitleDocument]:
    documents = []
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        infos = archive.infolist()
        _check_metadata([(info.filename, info.file_size, info.compress_size) for info in infos], limits, len(content))
        for info in infos:
            if info.flag_bits & 1:
                raise ArchiveError("encrypted_archive")
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode) or (stat.S_IFMT(mode) and not (stat.S_ISREG(mode) or stat.S_ISDIR(mode))):
                raise ArchiveError("archive_link_or_special_file")
            if info.is_dir() or not _subtitle(info.filename):
                continue
            with archive.open(info) as stream:
                documents.append(_document(_safe_name(info.filename), _read_bounded(stream, limits.max_file_bytes)))
    return documents


def _rar(content: bytes, limits: ExtractionLimits) -> list[SubtitleDocument]:
    try:
        import rarfile
    except ImportError as error:
        raise ArchiveError("rar_support_unavailable") from error
    documents = []
    try:
        with rarfile.RarFile(io.BytesIO(content)) as archive:
            if archive.needs_password():
                raise ArchiveError("encrypted_archive")
            infos = archive.infolist()
            _check_metadata([(info.filename, info.file_size, info.compress_size) for info in infos], limits, len(content))
            chosen = []
            for info in infos:
                if info.is_symlink() or getattr(info, "file_redir", None):
                    raise ArchiveError("archive_link_or_special_file")
                if info.isdir() or not _subtitle(info.filename):
                    continue
                chosen.append(info)
            if not chosen:
                return []
            setup = rarfile.tool_setup() if any(info.compress_type != rarfile.RAR_M0 for info in chosen) else None
            # rarfile's generic "--" insertion follows BSDTAR's -f option,
            # turning "--" into the archive filename on macOS libarchive.
            # Use the same available backend with correctly ordered arguments.
            if setup and setup.setup.get("open_cmd", (None,))[0] == "BSDTAR_TOOL":
                return _rar_bsdtar(content, chosen, limits, rarfile.BSDTAR_TOOL)
            for info in chosen:
                with archive.open(info) as stream:
                    documents.append(_document(_safe_name(info.filename), _read_bounded(stream, limits.max_file_bytes)))
    except rarfile.RarCannotExec as error:
        raise ArchiveError("rar_decompressor_unavailable") from error
    return documents


def _rar_bsdtar(content: bytes, infos: list, limits: ExtractionLimits, executable: str) -> list[SubtitleDocument]:
    """Extract stdout only; the temporary file is compressed input, not output."""
    executable = shutil.which(executable) or executable
    documents = []
    archive_deadline = time.monotonic() + 60
    with tempfile.NamedTemporaryFile(prefix="telepiplex-caption-", suffix=".rar") as compressed:
        compressed.write(content)
        compressed.flush()
        for info in infos:
            if time.monotonic() >= archive_deadline:
                raise ArchiveError("rar_decompression_timeout")
            process = subprocess.Popen([executable, "-x", "--to-stdout", "-f", compressed.name, "--", info.filename],
                                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            output = bytearray()
            deadline = min(time.monotonic() + 30, archive_deadline)
            try:
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout, selectors.EVENT_READ)
                    while True:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0 or not selector.select(remaining):
                            raise ArchiveError("rar_decompression_timeout")
                        chunk = os.read(process.stdout.fileno(), min(65536, limits.max_file_bytes - len(output) + 1))
                        if not chunk:
                            break
                        output.extend(chunk)
                        if len(output) > limits.max_file_bytes:
                            raise ArchiveError("archive_size_limit")
                if process.wait(timeout=max(0.1, deadline - time.monotonic())) != 0:
                    raise ArchiveError("invalid_archive")
                if len(output) != info.file_size or (info.CRC is not None and zlib.crc32(output) != info.CRC):
                    raise ArchiveError("archive_checksum_mismatch")
                documents.append(_document(_safe_name(info.filename), bytes(output)))
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=3)
                if process.stdout:
                    process.stdout.close()
    return documents


def _seven_zip_inner(content: bytes, limits: ExtractionLimits) -> list[SubtitleDocument]:
    try:
        import py7zr
        from py7zr.io import Py7zIO, WriterFactory
    except ImportError as error:
        raise ArchiveError("seven_zip_support_unavailable") from error

    class BoundedBuffer(Py7zIO):
        def __init__(self, owner):
            self.buffer = io.BytesIO()
            self.owner = owner

        def write(self, data):
            previous = self.size()
            projected = max(previous, self.buffer.tell() + len(data))
            if projected > limits.max_file_bytes or self.owner.total + projected - previous > limits.max_total_bytes:
                raise ArchiveError("archive_size_limit")
            written = self.buffer.write(data)
            self.owner.total += self.size() - previous
            return written

        def read(self, size=None):
            return self.buffer.read(-1 if size is None else size)

        def seek(self, offset, whence=0):
            return self.buffer.seek(offset, whence)

        def flush(self):
            self.buffer.flush()

        def size(self):
            return self.buffer.getbuffer().nbytes

    class Factory(WriterFactory):
        def __init__(self):
            self.products: dict[str, BoundedBuffer] = {}
            self.total = 0

        def create(self, filename):
            filename = _safe_name(filename)
            if filename in self.products or len(self.products) >= limits.max_members:
                raise ArchiveError("duplicate_archive_path")
            product = BoundedBuffer(self)
            self.products[filename] = product
            return product

    factory = Factory()
    with py7zr.SevenZipFile(io.BytesIO(content), mode="r") as archive:
        if archive.needs_password():
            raise ArchiveError("encrypted_archive")
        infos = archive.list()
        dense = _check_metadata([(info.filename, info.uncompressed or 0, info.compressed or 0) for info in infos],
                                limits, len(content), dense_ass=True)
        # ArchiveFile exposes link flags in addition to the public size list.
        if any(item.is_symlink or item.is_junction or item.is_socket for item in archive.files):
            raise ArchiveError("archive_link_or_special_file")
        targets = [info.filename for info in infos if not info.is_directory and _subtitle(info.filename)]
        if targets:
            archive.extract(targets=targets, factory=factory)
    documents = [_document(name, product.buffer.getvalue()) for name, product in factory.products.items() if _subtitle(name)]
    if dense:
        from .quality import decode_subtitle
        for document in documents:
            text, _ = decode_subtitle(document.content)
            lines = text.splitlines()
            if (not re.search(r"(?im)^\s*\[Script Info\]\s*$", text)
                    or not re.search(r"(?im)^\s*\[Events\]\s*$", text)
                    or not re.search(r"(?im)^Format:.*\bStart\b.*\bEnd\b.*\bText\s*$", text)
                    or sum(line.lstrip().casefold().startswith("dialogue:") for line in lines) < 20):
                raise ArchiveError("archive_compression_ratio")
    return documents


def _seven_zip_worker() -> None:
    """Private subprocess entry point; compressed input and stdout only."""
    try:
        limits = ExtractionLimits(**json.loads(sys.argv[2]))
        # Native decoder allocations are also limited on the Linux host. macOS
        # does not support a useful RLIMIT_AS; dictionary/output bounds below
        # and the parent's hard deadline remain enforced there.
        try:
            import resource
            if sys.platform.startswith("linux"):
                resource.setrlimit(resource.RLIMIT_AS, (768 * 1024 * 1024, 768 * 1024 * 1024))
            seconds = max(1, math.ceil(limits.max_decompression_seconds))
            resource.setrlimit(resource.RLIMIT_CPU, (seconds, seconds))
        except (ImportError, OSError, ValueError):
            pass
        try:
            import py7zr.archiveinfo as archiveinfo
            import py7zr.compressor as compressor
        except ImportError as error:
            raise ArchiveError("seven_zip_support_unavailable") from error
        original = compressor.SevenZipDecompressor

        class BoundedDecoder(original):
            def __init__(self, coders, packsize, unpacksizes, *args, **kwargs):
                if any(size < 0 or size > limits.max_total_bytes for size in unpacksizes):
                    raise ArchiveError("archive_size_limit")
                for coder in coders:
                    method, properties = coder["method"], coder.get("properties") or b""
                    if method == b"\x21":  # LZMA2 dictionary property
                        if len(properties) != 1 or properties[0] > 40:
                            raise ArchiveError("invalid_archive")
                        prop = properties[0]
                        dictionary = (2 | (prop & 1)) << (prop // 2 + 11)
                        if dictionary > 64 * 1024 * 1024:
                            raise ArchiveError("archive_dictionary_limit")
                    elif method == b"\x03\x01\x01":  # LZMA1
                        if len(properties) != 5 or int.from_bytes(properties[1:], "little") > 64 * 1024 * 1024:
                            raise ArchiveError("archive_dictionary_limit")
                    elif method not in {b"\x00", b"\x04\x01\x08", b"\x04\x02\x02", b"\x03", b"\x03\x03\x01\x03"}:
                        raise ArchiveError("seven_zip_codec_unavailable")
                super().__init__(coders, packsize, unpacksizes, *args, **kwargs)

        # Header decoding uses archiveinfo's imported alias; guard both header
        # and body before the library creates any native decompressor.
        compressor.SevenZipDecompressor = BoundedDecoder
        archiveinfo.SevenZipDecompressor = BoundedDecoder
        with open(sys.argv[1], "rb") as stream:
            content = _read_bounded(stream, limits.max_download_bytes)
        documents = _seven_zip_inner(content, limits)
        metadata = [{"name": item.filename, "size": len(item.content)} for item in documents]
        sys.stdout.buffer.write(json.dumps({"documents": metadata}).encode() + b"\n")
        for item in documents:
            sys.stdout.buffer.write(item.content)
    except Exception as error:
        message = str(error) if isinstance(error, ArchiveError) else "invalid_archive"
        sys.stdout.buffer.write(json.dumps({"error": message}).encode() + b"\n")


def _seven_zip(content: bytes, limits: ExtractionLimits) -> list[SubtitleDocument]:
    """Hard wall-clock and output bounds also cover header/native decoding."""
    timeout = min(60.0, max(0.01, limits.max_decompression_seconds))
    output_limit = limits.max_total_bytes + limits.max_members * 32_768 + 1024
    with tempfile.NamedTemporaryFile(prefix="telepiplex-caption-", suffix=".7z") as compressed:
        compressed.write(content)
        compressed.flush()
        process = subprocess.Popen(
            [sys.executable, "-c", "from telepiplex_caption.archive import _seven_zip_worker; _seven_zip_worker()",
             compressed.name, json.dumps(asdict(limits))], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        output = bytearray()
        deadline = time.monotonic() + timeout
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or not selector.select(remaining):
                        raise ArchiveError("archive_decompression_timeout")
                    chunk = os.read(process.stdout.fileno(), min(65536, output_limit - len(output) + 1))
                    if not chunk:
                        break
                    output.extend(chunk)
                    if len(output) > output_limit:
                        raise ArchiveError("archive_size_limit")
            if process.wait(timeout=max(0.1, deadline - time.monotonic())) != 0:
                raise ArchiveError("invalid_archive")
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=3)
            if process.stdout:
                process.stdout.close()
    header_end = output.find(b"\n")
    if not 0 <= header_end <= limits.max_members * 32_768 + 1024:
        raise ArchiveError("invalid_archive")
    header = json.loads(output[:header_end])
    if "error" in header:
        raise ArchiveError(header["error"])
    metadata = header["documents"]
    if len(metadata) > limits.max_members:
        raise ArchiveError("archive_member_limit")
    offset = header_end + 1
    result = []
    for item in metadata:
        size = item["size"]
        if not isinstance(size, int) or not 0 <= size <= limits.max_file_bytes or offset + size > len(output):
            raise ArchiveError("archive_size_limit")
        result.append(_document(_safe_name(item["name"]), bytes(output[offset:offset + size])))
        offset += size
    if offset != len(output):
        raise ArchiveError("invalid_archive")
    return result


def extract_subtitles(filename: str, content: bytes, limits: ExtractionLimits | None = None) -> list[SubtitleDocument]:
    limits = limits or ExtractionLimits()
    if not content:
        raise ArchiveError("empty_download")
    if len(content) > limits.max_download_bytes:
        raise ArchiveError("download_size_limit")
    name = _safe_name(filename)
    try:
        if content.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")):
            result = _zip(content, limits)
        elif content.startswith(b"Rar!\x1a\x07"):
            result = _rar(content, limits)
        elif content.startswith(b"7z\xbc\xaf\x27\x1c"):
            result = _seven_zip(content, limits)
        elif content.startswith(b"\x1f\x8b"):
            uncompressed_name = name[:-3] if name.casefold().endswith(".gz") else name
            with gzip.GzipFile(fileobj=io.BytesIO(content)) as archive:
                unpacked = _read_bounded(archive, limits.max_file_bytes)
            if len(unpacked) / len(content) > limits.max_compression_ratio:
                raise ArchiveError("archive_compression_ratio")
            result = [_document(uncompressed_name, unpacked)] if _subtitle(uncompressed_name) else []
        elif _subtitle(name):
            if len(content) > limits.max_file_bytes:
                raise ArchiveError("archive_size_limit")
            result = [_document(name, content)]
        else:
            raise ArchiveError("unsupported_download_format")
    except ArchiveError:
        raise
    except Exception as error:
        # Codec errors, corrupt headers and failed CRC checks are never treated
        # as valid subtitles, but avoid leaking external command internals.
        raise ArchiveError("invalid_archive") from error
    if not result:
        raise ArchiveError("no_supported_subtitles")
    if sum(len(document.content) for document in result) > limits.max_total_bytes:
        raise ArchiveError("archive_size_limit")
    return result
