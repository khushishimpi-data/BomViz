import hashlib
import io
from pathlib import Path

import pytest
from fastapi import UploadFile

from app.models import FileFormat
from app.services.upload_intake import (
    FORM_OVERHEAD_BYTES,
    MAX_FILENAME_CHARS,
    OLE_SIGNATURE,
    SNIFF_BYTES,
    XLSX_SIGNATURE,
    EmptyFileError,
    EncryptedWorkbookError,
    FileTooLargeError,
    InvalidContentLengthError,
    UnsupportedFileTypeError,
    clean_filename,
    detect_file_format,
    file_extension,
    receive_upload,
    request_exceeds_limit,
    sanitize_filename,
)

LIMIT = 1024


def _exists(path: Path) -> bool:
    return path.exists()


def _upload(data: bytes, filename: str = "bom.csv") -> UploadFile:
    return UploadFile(file=io.BytesIO(data), filename=filename)


# Content-Length pre-check


def test_a_declared_length_above_limit_plus_overhead_is_rejected() -> None:
    assert request_exceeds_limit(str(LIMIT + FORM_OVERHEAD_BYTES + 1), LIMIT) is True


def test_a_declared_length_at_limit_plus_overhead_is_allowed() -> None:
    assert request_exceeds_limit(str(LIMIT + FORM_OVERHEAD_BYTES), LIMIT) is False


def test_a_missing_length_is_left_to_the_stream_count() -> None:
    assert request_exceeds_limit(None, LIMIT) is False


def test_a_length_padded_with_optional_whitespace_is_accepted() -> None:
    assert request_exceeds_limit(" 10\t", LIMIT) is False


@pytest.mark.parametrize(
    "header",
    ["", "abc", "-5", "+5", "1e9", "1_000", chr(0xB2), chr(0x661), "10 10", "0x10"],
    ids=[
        "empty",
        "letters",
        "negative",
        "plus",
        "exponent",
        "underscore",
        "superscript-two",
        "arabic-indic-one",
        "inner-space",
        "hex",
    ],
)
def test_a_length_that_is_not_ascii_digits_is_a_bad_request(header: str) -> None:
    with pytest.raises(InvalidContentLengthError) as caught:
        request_exceeds_limit(header, LIMIT)

    assert (caught.value.status_code, caught.value.code) == (400, "BAD_REQUEST")


# Filenames


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("bom.csv", "bom.csv"),
        ("/etc/passwd/../bom.csv", "bom.csv"),
        ("C:\\Users\\me\\Desktop\\bom.xlsx", "bom.xlsx"),
        ("  padded name.xls  ", "padded name.xls"),
        ("bo\x00m\r\n\x1b.csv", "bom.csv"),
        ("right\u202eto-left.csv", "rightto-left.csv"),
        ("line\u2028sep.csv", "linesep.csv"),
        ("para\u2029sep.csv", "parasep.csv"),
        ("Stückliste.csv", "Stückliste.csv"),
    ],
)
def test_sanitize_filename_keeps_a_clean_basename(raw: str, expected: str) -> None:
    assert sanitize_filename(raw, FileFormat.CSV) == expected


def test_sanitize_filename_normalizes_unicode_to_nfc() -> None:
    decomposed = "Stu\u0308ckliste.csv"

    assert sanitize_filename(decomposed, FileFormat.CSV) == "St\u00fcckliste.csv"


@pytest.mark.parametrize("raw", [None, "", "   ", "dir/", ".", "..", "\x00\x01"])
def test_sanitize_filename_falls_back_when_nothing_usable_is_left(raw: str | None) -> None:
    assert sanitize_filename(raw, FileFormat.XLSX) == "upload.xlsx"


@pytest.mark.parametrize("raw", [chr(0x2028), f"{chr(0x2029)} {chr(0x2028)}", f"dir/{chr(0x2029)}"])
def test_sanitize_filename_falls_back_when_only_separators_are_left(raw: str) -> None:
    assert sanitize_filename(raw, FileFormat.CSV) == "upload.csv"


def test_clean_filename_returns_empty_when_nothing_usable_is_left() -> None:
    assert clean_filename(f"../{chr(0x2028)}", 50) == ""


def test_clean_filename_truncates_to_the_given_length_keeping_the_extension() -> None:
    assert clean_filename("a" * 60 + ".csv", 20) == "a" * 16 + ".csv"


def test_sanitize_filename_truncates_long_names_keeping_the_extension() -> None:
    name = sanitize_filename("a" * 400 + ".xlsx", FileFormat.XLSX)

    assert len(name) == MAX_FILENAME_CHARS
    assert name.endswith(".xlsx")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("BOM.XLSX", "xlsx"), ("a.b.csv", "csv"), ("noext", None), ("trailing.", None), (None, None)],
)
def test_file_extension_is_lower_cased_from_the_basename(
    raw: str | None, expected: str | None
) -> None:
    assert file_extension(raw) == expected


# Format detection


def test_xlsx_with_a_zip_signature_is_xlsx() -> None:
    assert detect_file_format("bom.xlsx", XLSX_SIGNATURE + b"rest") is FileFormat.XLSX


def test_extension_case_does_not_matter() -> None:
    assert detect_file_format("BOM.XLSX", XLSX_SIGNATURE + b"rest") is FileFormat.XLSX


def test_an_ole_container_named_xlsx_is_an_encrypted_workbook() -> None:
    with pytest.raises(EncryptedWorkbookError):
        detect_file_format("bom.xlsx", OLE_SIGNATURE + b"rest")


def test_xlsx_with_text_contents_is_unsupported() -> None:
    with pytest.raises(UnsupportedFileTypeError):
        detect_file_format("bom.xlsx", b"part,qty\n")


def test_xls_with_an_ole_signature_is_xls() -> None:
    assert detect_file_format("bom.xls", OLE_SIGNATURE + b"rest") is FileFormat.XLS


def test_xls_with_a_zip_signature_is_unsupported() -> None:
    with pytest.raises(UnsupportedFileTypeError):
        detect_file_format("bom.xls", XLSX_SIGNATURE + b"rest")


def test_plain_text_csv_is_csv() -> None:
    assert detect_file_format("bom.csv", b"part,qty\nA-1,2\n") is FileFormat.CSV


@pytest.mark.parametrize("signature", [XLSX_SIGNATURE, OLE_SIGNATURE])
def test_csv_carrying_a_workbook_signature_is_unsupported(signature: bytes) -> None:
    with pytest.raises(UnsupportedFileTypeError):
        detect_file_format("bom.csv", signature + b"rest")


def test_csv_with_a_nul_byte_in_the_sniffed_head_is_unsupported() -> None:
    with pytest.raises(UnsupportedFileTypeError):
        detect_file_format("bom.csv", b"part,qty\n\x00A-1,2\n")


@pytest.mark.parametrize(
    ("byte_order_mark", "encoding"),
    [(b"\xff\xfe", "utf-16-le"), (b"\xfe\xff", "utf-16-be")],
    ids=["utf-16-le", "utf-16-be"],
)
def test_utf16_csv_with_a_byte_order_mark_is_csv(byte_order_mark: bytes, encoding: str) -> None:
    head = byte_order_mark + "Part,Qty\nP-1,2\n".encode(encoding)

    assert detect_file_format("bom.csv", head) is FileFormat.CSV


@pytest.mark.parametrize("encoding", ["utf-16-le", "utf-16-be"])
def test_utf16_csv_without_a_byte_order_mark_is_unsupported(encoding: str) -> None:
    with pytest.raises(UnsupportedFileTypeError):
        detect_file_format("bom.csv", "Part,Qty\nP-1,2\n".encode(encoding))


@pytest.mark.parametrize(
    "head",
    [
        b"\xff\xfe" + b"\x00\xdc" + "Part".encode("utf-16-le"),  # lone low surrogate
        b"\xff\xfe" + b"\x00\xd8" + "Part".encode("utf-16-le"),  # high surrogate without pair
        b"\xfe\xff" + b"\xdc\x00" + "Part".encode("utf-16-be"),  # lone low surrogate
        b"\xff\xfe" + bytes(range(256)) * 8,  # binary that merely starts with FF FE
    ],
    ids=["le-low-surrogate", "le-unpaired-high", "be-low-surrogate", "binary"],
)
def test_csv_with_a_utf16_byte_order_mark_must_decode_as_utf16(head: bytes) -> None:
    with pytest.raises(UnsupportedFileTypeError):
        detect_file_format("bom.csv", head)


@pytest.mark.parametrize(
    ("byte_order_mark", "encoding"),
    [(b"\xff\xfe", "utf-16-le"), (b"\xfe\xff", "utf-16-be")],
    ids=["utf-16-le", "utf-16-be"],
)
def test_utf16_csv_cut_mid_character_by_the_sniffed_head_is_csv(
    byte_order_mark: bytes, encoding: str
) -> None:
    emoji = "\U0001f600".encode(encoding)  # a surrogate pair
    start = byte_order_mark + "Part,Qty\n".encode(encoding)
    cut_inside_the_pair = start + emoji[:2]
    cut_inside_a_code_unit = start + emoji[:3]

    assert detect_file_format("bom.csv", cut_inside_the_pair) is FileFormat.CSV
    assert detect_file_format("bom.csv", cut_inside_a_code_unit) is FileFormat.CSV


def test_utf16_csv_longer_than_the_sniffed_head_is_csv() -> None:
    data = b"\xff\xfe" + ("Part,Qty\n" + "\U0001f600" * SNIFF_BYTES).encode("utf-16-le")

    assert detect_file_format("bom.csv", data) is FileFormat.CSV


def test_a_utf16_byte_order_mark_does_not_exempt_other_extensions() -> None:
    with pytest.raises(UnsupportedFileTypeError):
        detect_file_format("bom.xls", b"\xff\xfe" + "Part".encode("utf-16-le"))


def test_csv_ignores_nul_bytes_beyond_the_sniffed_head() -> None:
    head = b"a" * SNIFF_BYTES + b"\x00"

    assert detect_file_format("bom.csv", head) is FileFormat.CSV


@pytest.mark.parametrize("filename", ["bom.txt", "bom.xlsm", "bom", "", None, "bom.csv.exe"])
def test_other_extensions_are_unsupported(filename: str | None) -> None:
    with pytest.raises(UnsupportedFileTypeError):
        detect_file_format(filename, b"part,qty\n")


# Streaming intake


async def test_receive_upload_copies_counts_and_hashes_the_file() -> None:
    data = b"part,qty\n" * 20_000  # several chunks

    async with receive_upload(_upload(data), max_bytes=len(data)) as received:
        assert received.path.read_bytes() == data
        assert received.size_bytes == len(data)
        assert received.checksum_sha256 == hashlib.sha256(data).hexdigest()
        assert received.head == data[:SNIFF_BYTES]


async def test_receive_upload_rejects_one_byte_over_the_limit() -> None:
    with pytest.raises(FileTooLargeError) as caught:
        async with receive_upload(_upload(b"x" * (LIMIT + 1)), max_bytes=LIMIT):
            pass

    assert caught.value.code == "FILE_TOO_LARGE"
    assert caught.value.status_code == 413


async def test_receive_upload_rejects_an_empty_file() -> None:
    with pytest.raises(EmptyFileError):
        async with receive_upload(_upload(b""), max_bytes=LIMIT):
            pass


async def test_receive_upload_removes_the_temporary_file_on_exit() -> None:
    async with receive_upload(_upload(b"part,qty\n"), max_bytes=LIMIT) as received:
        path: Path = received.path
        assert _exists(path)

    assert not _exists(path)


async def test_receive_upload_removes_the_temporary_file_when_the_body_raises() -> None:
    path: Path | None = None
    with pytest.raises(RuntimeError):
        async with receive_upload(_upload(b"part,qty\n"), max_bytes=LIMIT) as received:
            path = received.path
            raise RuntimeError("storage failed")

    assert path is not None
    assert not _exists(path)
