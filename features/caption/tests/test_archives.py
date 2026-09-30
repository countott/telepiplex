import gzip
import io
import stat
import struct
import shutil
import zipfile
import zlib

import pytest

from telepiplex_caption.archive import ArchiveError, ExtractionLimits, extract_subtitles


def zip_bytes(members):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in members:
            archive.writestr(name, content)
    return output.getvalue()


def test_extracts_supported_text_only_and_preserves_member_identity():
    docs = extract_subtitles("download.zip", zip_bytes([("Season 1/Example.S01E02.srt", b"subtitle"), ("Example.S01E03.ass", b"ass"), ("installer.exe", b"no"), ("cover.jpg", b"no")]))
    assert [doc.filename for doc in docs] == ["Season 1/Example.S01E02.srt", "Example.S01E03.ass"]
    assert docs[0].content == b"subtitle"


@pytest.mark.parametrize("name", ["../escape.srt", "/tmp/escape.srt", "C:\\escape.srt", "safe/../../escape.srt", "safe\\..\\escape.srt"])
def test_traversal_rejected_without_writing(name):
    with pytest.raises(ArchiveError, match="unsafe_archive_path"):
        extract_subtitles("download.zip", zip_bytes([(name, b"subtitle")]))


def test_symlink_rejected():
    info = zipfile.ZipInfo("outside.srt")
    info.create_system = 3
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    with pytest.raises(ArchiveError, match="archive_link"):
        extract_subtitles("download.zip", zip_bytes([(info, b"/tmp/target")]))


def test_zip_bomb_and_member_limits():
    with pytest.raises(ArchiveError, match="archive_compression_ratio"):
        extract_subtitles("download.zip", zip_bytes([("bomb.srt", b"a" * 1_000_000)]))
    with pytest.raises(ArchiveError, match="archive_member_limit"):
        extract_subtitles("download.zip", zip_bytes([("1.srt", b"1"), ("2.srt", b"2")]), ExtractionLimits(max_members=1))


def test_single_and_total_size_limits():
    with pytest.raises(ArchiveError, match="archive_size_limit"):
        extract_subtitles("download.zip", zip_bytes([("1.srt", b"1234")]), ExtractionLimits(max_file_bytes=3))
    with pytest.raises(ArchiveError, match="archive_size_limit"):
        extract_subtitles("download.zip", zip_bytes([("1.srt", b"1234"), ("2.srt", b"5678")]), ExtractionLimits(max_total_bytes=6))


def test_large_ignored_attachment_does_not_fail_small_subtitle():
    docs = extract_subtitles("download.zip", zip_bytes([("movie.srt", b"123"), ("movie.sub", bytes(range(20)))]), ExtractionLimits(max_file_bytes=3))
    assert len(docs) == 1 and docs[0].content == b"123"


def test_duplicate_paths_rejected():
    with pytest.raises(ArchiveError, match="duplicate_archive_path"):
        extract_subtitles("download.zip", zip_bytes([("a.srt", b"1"), ("A.srt", b"2")]))


def test_gzip_and_direct_subtitle():
    assert extract_subtitles("Example.srt.gz", gzip.compress(b"subtitle"))[0].content == b"subtitle"
    assert extract_subtitles("Example.ass", b"[Script Info]")[0].format == "ass"


def test_runtime_in_download_title_and_dot_prefix_are_harmless_in_memory():
    docs = extract_subtitles("Example.02:04:32.zip", zip_bytes([("./Example.02:04:32.srt", b"subtitle")]))
    assert docs[0].filename == "Example.02:04:32.srt"


def test_nested_archive_is_not_extracted():
    with pytest.raises(ArchiveError, match="no_supported_subtitles"):
        extract_subtitles("download.zip", zip_bytes([("inner.zip", zip_bytes([("hidden.srt", b"payload")]))]))


def test_broken_archive_rejected():
    with pytest.raises(ArchiveError, match="invalid_archive"):
        extract_subtitles("download.zip", b"PK\x03\x04broken")


def test_real_seven_zip_in_memory_extraction():
    py7zr = pytest.importorskip("py7zr")
    output = io.BytesIO()
    with py7zr.SevenZipFile(output, "w") as archive:
        archive.writestr(b"subtitle", "Example.S01E02.srt")
    docs = extract_subtitles("download.7z", output.getvalue())
    assert docs[0].filename == "Example.S01E02.srt"
    assert docs[0].content == b"subtitle"


def seven_zip_bytes(members):
    py7zr = pytest.importorskip("py7zr")
    output = io.BytesIO()
    with py7zr.SevenZipFile(output, "w") as archive:
        for name, content in members:
            archive.writestr(content, name)
    return output.getvalue()


def dense_ass():
    # Synthetic ASS effects, no externally authored subtitle text.
    header = b"[Script Info]\nScriptType: v4.00+\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    lines = [f"Dialogue: 0,0:00:{i % 50:02}.00,0:00:{i % 50 + 1:02}.00,Default,,0,0,0,,".encode()
             + b"{\\p1}m 0 0 " + b"l 20 0 20 20 0 20 " * 12 + b"{\\p0} Synthetic test " + str(i).encode() + b"\n" for i in range(4000)]
    return header + b"".join(lines)


def test_dense_ass_seven_zip_is_bounded_and_validated_without_allowing_generic_bombs():
    payload = dense_ass()
    archive = seven_zip_bytes([("Example.ass", payload)])
    assert len(payload) / len(archive) > 200
    docs = extract_subtitles("effects.7z", archive)
    assert docs[0].content == payload
    with pytest.raises(ArchiveError, match="archive_size_limit"):
        extract_subtitles("effects.7z", archive, ExtractionLimits(max_total_bytes=len(payload) - 1))
    with pytest.raises(ArchiveError, match="archive_compression_ratio"):
        extract_subtitles("bomb.7z", seven_zip_bytes([("fake.ass", b"a" * 120_000)]))
    with pytest.raises(ArchiveError, match="archive_compression_ratio"):
        extract_subtitles("bomb.7z", seven_zip_bytes([("fake.srt", b"a" * 1_000_000)]))


def test_mixed_archive_and_explicit_stricter_ratio_do_not_use_dense_ass_exception():
    payload = dense_ass()
    with pytest.raises(ArchiveError, match="archive_compression_ratio"):
        extract_subtitles("mixed.7z", seven_zip_bytes([("Example.ass", payload), ("attachment.bin", b"attachment")]))
    with pytest.raises(ArchiveError, match="archive_compression_ratio"):
        extract_subtitles("effects.7z", seven_zip_bytes([("Example.ass", payload)]), ExtractionLimits(max_compression_ratio=100))


def test_seven_zip_native_decoding_has_hard_timeout():
    archive = seven_zip_bytes([("Example.srt", b"synthetic subtitle")])
    with pytest.raises(ArchiveError, match="archive_decompression_timeout"):
        extract_subtitles("download.7z", archive, ExtractionLimits(max_decompression_seconds=0.01))


def stored_rar(payload=b"authored subtitle test fixture"):
    """Small authored RAR3 store fixture, with real header/content checksums."""
    name = b"Example.srt"
    def header(kind, flags, body):
        data = struct.pack("<BHH", kind, flags, 7 + len(body)) + body
        return struct.pack("<H", zlib.crc32(data) & 0xFFFF) + data
    member = struct.pack("<LLBLLBBHL", len(payload), len(payload), 3, zlib.crc32(payload), 0, 20, 0x30, len(name), 0o100644) + name
    return b"Rar!\x1a\x07\x00" + header(0x73, 0, b"\x00" * 6) + header(0x74, 0x8000, member) + payload + header(0x7b, 0, b"")


def test_rar_storage_member_extracts_without_external_backend(monkeypatch):
    rarfile = pytest.importorskip("rarfile")
    monkeypatch.setattr(rarfile, "tool_setup", lambda: (_ for _ in ()).throw(AssertionError("stored RAR does not need a tool")))
    assert extract_subtitles("fixture.rar", stored_rar())[0].content == b"authored subtitle test fixture"


def test_bsdtar_fallback_has_correct_argument_order_and_crc_validation():
    rarfile = pytest.importorskip("rarfile")
    backend = shutil.which("bsdtar")
    if not backend:
        pytest.skip("bsdtar backend is not installed")
    from telepiplex_caption.archive import _rar_bsdtar
    content = stored_rar()
    with rarfile.RarFile(io.BytesIO(content)) as archive:
        infos = archive.infolist()
    docs = _rar_bsdtar(content, infos, ExtractionLimits(), backend)
    assert docs[0].content == b"authored subtitle test fixture"
    with pytest.raises(ArchiveError, match="archive_size_limit"):
        _rar_bsdtar(content, infos, ExtractionLimits(max_file_bytes=3), backend)
