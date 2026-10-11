"""Receiving an uploaded BOM file: bounded streaming, hashing, type detection, display names.

Everything here treats the upload as untrusted bytes. The size is counted while streaming (the
request's `Content-Length` is only an early hint), the format comes from the extension *and* the
file's magic bytes (never the client's content type), and the client's filename is reduced to a
display-only basename that never reaches an object key or a filesystem path.
"""

import asyncio
import codecs
import hashlib
import re
import unicodedata
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path
from tempfile import NamedTemporaryFile

from fastapi import UploadFile

from app.core.exceptions import AppError
from app.models import FileFormat

CHUNK_BYTES = 64 * 1024
SNIFF_BYTES = 64 * 1024
# Allowance for the multipart framing and the small form fields sent alongside the file.
FORM_OVERHEAD_BYTES = 64 * 1024
MAX_FILENAME_CHARS = 255
_MAX_EXTENSION_CHARS = 16

XLSX_SIGNATURE = b"PK\x03\x04"
OLE_SIGNATURE = bytes.fromhex("D0CF11E0A1B11AE1")
UTF16_BOMS = (b"\xff\xfe", b"\xfe\xff")  # little-endian, big-endian
_UTF16_CODECS = {b"\xff\xfe": "utf-16-le", b"\xfe\xff": "utf-16-be"}
_BOM_BYTES = 2
# Control, format (bidi overrides, zero-width), line- and paragraph-separator characters.
_STRIPPED_CATEGORIES = frozenset({"Cc", "Cf", "Zl", "Zp"})
_ASCII_DIGITS = re.compile(r"[0-9]+")

FILE_EXTENSIONS: dict[FileFormat, str] = {
    FileFormat.CSV: "csv",
    FileFormat.XLSX: "xlsx",
    FileFormat.XLS: "xls",
}
CONTENT_TYPES: dict[FileFormat, str] = {
    FileFormat.CSV: "text/csv",
    FileFormat.XLSX: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    FileFormat.XLS: "application/vnd.ms-excel",
}


def _megabytes(size_bytes: int) -> str:
    megabytes = size_bytes / (1024 * 1024)
    return f"{megabytes:g} MB"


class FileTooLargeError(AppError):
    status_code = HTTPStatus.REQUEST_ENTITY_TOO_LARGE
    code = "FILE_TOO_LARGE"

    def __init__(self, max_bytes: int) -> None:
        super().__init__(f"The file is larger than the {_megabytes(max_bytes)} limit.")


class InvalidContentLengthError(AppError):
    status_code = HTTPStatus.BAD_REQUEST
    code = "BAD_REQUEST"

    def __init__(self) -> None:
        super().__init__("The Content-Length header is not a valid number of bytes.")


class EmptyFileError(AppError):
    status_code = HTTPStatus.BAD_REQUEST
    code = "EMPTY_FILE"

    def __init__(self) -> None:
        super().__init__("The file is empty.")


class UnsupportedFileTypeError(AppError):
    status_code = HTTPStatus.BAD_REQUEST
    code = "UNSUPPORTED_FILE_TYPE"

    def __init__(self) -> None:
        super().__init__(
            "Unsupported file type. Upload a .csv, .xlsx or .xls file whose contents match "
            "its extension."
        )


class EncryptedWorkbookError(AppError):
    status_code = HTTPStatus.BAD_REQUEST
    code = "ENCRYPTED_WORKBOOK"

    def __init__(self) -> None:
        super().__init__(
            "The workbook appears to be password-protected. Remove the password and upload "
            "it again."
        )


@dataclass(frozen=True, slots=True)
class ReceivedFile:
    """An upload copied to a local temporary file, with what was learned while copying."""

    path: Path
    size_bytes: int
    checksum_sha256: str
    head: bytes  # The first `SNIFF_BYTES` bytes, for type detection.


def request_exceeds_limit(content_length: str | None, max_upload_bytes: int) -> bool:
    """True when a declared `Content-Length` is larger than the file limit plus form overhead.

    A missing header is not judged here; the streamed byte count still applies. A present header
    must be ASCII digits only (RFC 9110 8.6), else `InvalidContentLengthError` (400): `int()`
    would accept other Unicode digits, signs or underscores, and `str.isdigit` accepts
    characters such as superscripts that `int()` then rejects.
    """
    if content_length is None:
        return False
    value = content_length.strip(" \t")
    if not _ASCII_DIGITS.fullmatch(value):
        raise InvalidContentLengthError()
    return int(value) > max_upload_bytes + FORM_OVERHEAD_BYTES


def _clean_basename(raw: str) -> str:
    """NFC-normalized last path component of `raw`, without control, format or line/paragraph
    separator characters, and without surrounding whitespace."""
    text = unicodedata.normalize("NFC", raw)
    text = "".join(ch for ch in text if unicodedata.category(ch) not in _STRIPPED_CATEGORIES)
    return text.replace("\\", "/").rsplit("/", 1)[-1].strip()


def clean_filename(raw: str | None, max_chars: int) -> str:
    """A client filename reduced to a safe display basename of at most `max_chars` characters.

    The one sanitizer for client filenames (stored display names and download dispositions):
    NFC, last path component only, `Cc`/`Cf`/`Zl`/`Zp` characters removed, padding stripped.
    Truncation keeps a short extension. Returns "" when nothing usable is left (including
    "." and ".."); callers choose their own fallback.
    """
    name = _clean_basename(raw or "")
    if name in {"", ".", ".."}:
        return ""
    if len(name) > max_chars:
        stem, dot, extension = name.rpartition(".")
        if dot and stem and len(extension) < _MAX_EXTENSION_CHARS:
            suffix = f".{extension}"
            name = stem[: max_chars - len(suffix)] + suffix
        else:
            name = name[:max_chars]
    return name


def file_extension(filename: str | None) -> str | None:
    """Lower-cased extension of the cleaned basename of `filename`, without the dot."""
    name = _clean_basename(filename or "")
    _, dot, extension = name.rpartition(".")
    return extension.lower() if dot and extension else None


def sanitize_filename(filename: str | None, file_format: FileFormat) -> str:
    """Display-only name (see `clean_filename`), at most 255 characters.

    Falls back to `upload.<ext>` when nothing usable is left.
    """
    return clean_filename(filename, MAX_FILENAME_CHARS) or f"upload.{FILE_EXTENSIONS[file_format]}"


def _is_utf16_text(head: bytes) -> bool:
    """True when `head` starts with a UTF-16 BOM and the rest decodes as strict UTF-16.

    `head` may be cut mid-character when the file is longer than the sniffed prefix: the
    incremental decoder (not final) holds back a trailing partial code unit, or a high surrogate
    whose pair was cut off, instead of failing on it. Unpaired surrogates elsewhere still fail.
    """
    codec = _UTF16_CODECS.get(head[:_BOM_BYTES])
    if codec is None:
        return False
    decoder = codecs.getincrementaldecoder(codec)(errors="strict")
    try:
        decoder.decode(head[_BOM_BYTES:], final=False)
    except UnicodeDecodeError:
        return False
    return True


def detect_file_format(filename: str | None, head: bytes) -> FileFormat:
    """The file's format from its extension and its leading bytes; both must agree.

    `.xlsx` must be a zip (`PK\\x03\\x04`); an OLE container named `.xlsx` is how Excel stores
    an encrypted workbook, so it gets its own error. `.xls` must be an OLE container. `.csv`
    must carry neither signature and no NUL byte in the first 64 KB, unless it starts with a
    UTF-16 byte-order mark (UTF-16 text is full of NUL bytes); then the sniffed head must decode
    as strict UTF-16 in the BOM's byte order, so a binary that happens to start with `FF FE` or
    `FE FF` is not accepted as text.
    """
    extension = file_extension(filename)
    if extension == "xlsx":
        if head.startswith(XLSX_SIGNATURE):
            return FileFormat.XLSX
        if head.startswith(OLE_SIGNATURE):
            raise EncryptedWorkbookError()
    elif extension == "xls":
        if head.startswith(OLE_SIGNATURE):
            return FileFormat.XLS
    elif extension == "csv":
        sniffed = head[:SNIFF_BYTES]
        if sniffed.startswith((XLSX_SIGNATURE, OLE_SIGNATURE)):
            raise UnsupportedFileTypeError()
        if sniffed.startswith(UTF16_BOMS):
            if _is_utf16_text(sniffed):
                return FileFormat.CSV
        elif b"\x00" not in sniffed:
            return FileFormat.CSV
    raise UnsupportedFileTypeError()


@asynccontextmanager
async def receive_upload(upload: UploadFile, max_bytes: int) -> AsyncIterator[ReceivedFile]:
    """Stream `upload` into a temporary file, counting and hashing; delete the file on exit.

    Stops reading as soon as more than `max_bytes` arrived (`FileTooLargeError`); an empty
    upload raises `EmptyFileError`. The temporary file can be reopened by path while the
    context is active (also on Windows) and is removed when it exits, whatever happens.
    """
    with NamedTemporaryFile(
        prefix="bomviz-upload-", suffix=".part", delete=True, delete_on_close=False
    ) as temp:
        digest = hashlib.sha256()
        size = 0
        head = bytearray()
        while chunk := await upload.read(CHUNK_BYTES):
            size += len(chunk)
            if size > max_bytes:
                raise FileTooLargeError(max_bytes)
            digest.update(chunk)
            if len(head) < SNIFF_BYTES:
                head += chunk[: SNIFF_BYTES - len(head)]
            await asyncio.to_thread(temp.write, chunk)
        if size == 0:
            raise EmptyFileError()
        await asyncio.to_thread(temp.flush)
        yield ReceivedFile(
            path=Path(temp.name),
            size_bytes=size,
            checksum_sha256=digest.hexdigest(),
            head=bytes(head),
        )
