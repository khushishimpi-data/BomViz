"""CSV reading: encoding and delimiter detection, then a single streaming pass over the rows.

Row numbers are physical line numbers: a record's `row_number` (and the `header_row` option,
explicit or detected) is the 1-based line on which the record starts, as a text editor shows
it. A quoted field spanning lines makes the next record start further down, so row numbers
can skip values.

The csv module builds a whole record before it can be checked, so the lines fed to it are
read in bounded pieces and a record with more than `max_csv_record_delimiters` delimiter
characters (quoted or not) fails with `PARSE_TOO_MANY_COLUMNS` before it is parsed. Fields
beyond `max_columns` must be empty; they are dropped before conversion.
"""

from __future__ import annotations

import codecs
import csv
import io
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, TextIO

from charset_normalizer import from_bytes

from app.services.parsing.cells import cell_to_text
from app.services.parsing.errors import ParseError, ParseErrorCode
from app.services.parsing.header import Row, TableShape, build_table

if TYPE_CHECKING:
    from app.services.parsing.parse import ParseLimits

_CHUNK_BYTES = 64 * 1024
# Lines are read in pieces of at most this many characters.
_LINE_PIECE_CHARS = 64 * 1024
_SNIFF_CHARS = 64 * 1024
_DETECT_SAMPLE_BYTES = 1024 * 1024
_SNIFF_DELIMITERS = ",;\t|"
_DEFAULT_DELIMITER = ","
_UTF16_BOMS = (codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)
# Single-byte encodings from the allowlist that charset-normalizer may choose between.
_LEGACY_ENCODINGS = ("cp1252", "latin-1", "iso-8859-15")
_LEGACY_BY_CODEC = {codecs.lookup(name).name: name for name in _LEGACY_ENCODINGS}


@dataclass(frozen=True, slots=True)
class CsvTable:
    """A parsed CSV with the delimiter and encoding that were used."""

    table: TableShape
    delimiter: str
    encoding: str


def read_csv(
    path: Path,
    *,
    delimiter: str | None,
    encoding: str | None,
    header_row: int | None,
    limits: ParseLimits,
) -> CsvTable:
    """Parse a CSV file. Explicit `delimiter`/`encoding` win over detection."""
    used_encoding = encoding or detect_encoding(path)
    used_delimiter = delimiter or _sniff_delimiter(path, used_encoding)
    with _field_size_limit(limits.max_cell_chars):
        try:
            table = build_table(
                _iter_rows(path, used_encoding, used_delimiter, limits),
                header_row,
                limits,
            )
        except UnicodeDecodeError as exc:
            raise _decode_error(used_encoding) from exc
    return CsvTable(table=table, delimiter=used_delimiter, encoding=used_encoding)


def detect_encoding(path: Path) -> str:
    """Byte-order mark, else strict UTF-8, else the best allowlisted single-byte encoding."""
    with path.open("rb") as handle:
        head = handle.read(4)
    if head.startswith(codecs.BOM_UTF8):
        return "utf-8-sig"
    if head.startswith(_UTF16_BOMS):
        return "utf-16"
    if _is_utf8(path):
        return "utf-8"
    with path.open("rb") as handle:
        sample = handle.read(_DETECT_SAMPLE_BYTES)
    best = from_bytes(sample, cp_isolation=list(_LEGACY_ENCODINGS)).best()
    if best is not None:
        detected = _LEGACY_BY_CODEC.get(codecs.lookup(best.encoding).name)
        if detected is not None:
            return detected
    raise ParseError(
        ParseErrorCode.ENCODING_UNKNOWN,
        "The text encoding of the file could not be detected. Choose the encoding explicitly.",
    )


def _is_utf8(path: Path) -> bool:
    decoder = codecs.getincrementaldecoder("utf-8")(errors="strict")
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(_CHUNK_BYTES):
                decoder.decode(chunk)
        decoder.decode(b"", final=True)
    except UnicodeDecodeError:
        return False
    return True


def _sniff_delimiter(path: Path, encoding: str) -> str:
    try:
        with path.open(encoding=encoding, newline="") as handle:
            sample = handle.read(_SNIFF_CHARS)
            truncated = bool(handle.read(1))
    except UnicodeDecodeError as exc:
        raise _decode_error(encoding) from exc
    if truncated and "\n" in sample:
        sample = sample[: sample.rindex("\n")]
    try:
        return csv.Sniffer().sniff(sample, delimiters=_SNIFF_DELIMITERS).delimiter
    except csv.Error:
        # Sniffer demands ~90% identical lines, which a title row above a few data rows breaks.
        return _delimiter_by_consistency(sample)


def _delimiter_by_consistency(sample: str) -> str:
    """Pick the candidate that splits the most records, then the most consistently.

    Records are read quote-aware once per candidate. Records with no occurrence of a candidate
    (blank lines, single-cell titles) do not count for it. Ties keep the order of
    `_SNIFF_DELIMITERS`; with no occurrence of any candidate the default is returned.
    """
    best = _DEFAULT_DELIMITER
    best_score = (0, 0)
    for delimiter in _SNIFF_DELIMITERS:
        counts: Counter[int] = Counter()
        try:
            for record in csv.reader(io.StringIO(sample), delimiter=delimiter):
                if len(record) > 1:
                    counts[len(record) - 1] += 1
        except csv.Error:
            continue
        if not counts:
            continue
        score = (counts.total(), counts.most_common(1)[0][1])
        if score > best_score:
            best, best_score = delimiter, score
    return best


def _iter_rows(path: Path, encoding: str, delimiter: str, limits: ParseLimits) -> Iterator[Row]:
    with path.open(encoding=encoding, errors="strict", newline="") as handle:
        lines = _GuardedLines(handle, delimiter, limits)
        reader = csv.reader(lines, delimiter=delimiter, strict=False)
        while True:
            line_number = reader.line_num + 1
            lines.start_record()
            try:
                record = next(reader)
            except StopIteration:
                return
            except csv.Error as exc:
                raise _csv_error(exc, limits.max_cell_chars) from exc
            if len(record) > limits.max_columns:
                if any(field.strip() for field in record[limits.max_columns :]):
                    raise _too_many_columns(limits)
                del record[limits.max_columns :]
            yield line_number, [cell_to_text(value) for value in record]


class _GuardedLines:
    """Physical lines for `csv.reader`, refusing a record with too many delimiters.

    Lines are read in pieces of `_LINE_PIECE_CHARS`, so an oversized line is rejected after
    reading one piece beyond the budget rather than after loading all of it. Delimiters are
    counted without regard to quoting, which over-counts only for quoted text full of them.
    """

    def __init__(self, handle: TextIO, delimiter: str, limits: ParseLimits) -> None:
        self._handle = handle
        self._delimiter = delimiter
        self._limits = limits
        self._carry = ""
        self._delimiters = 0

    def start_record(self) -> None:
        self._delimiters = 0

    def __iter__(self) -> _GuardedLines:
        return self

    def __next__(self) -> str:
        pieces: list[str] = []
        while True:
            if self._carry:
                piece, self._carry = self._carry, ""
            else:
                piece = self._handle.readline(_LINE_PIECE_CHARS)
            if not piece:
                break
            if pieces and pieces[-1].endswith("\r"):
                # The previous piece was cut right after a CR: a following LF belongs to
                # this line; anything else starts the next one (a CR-only line ending).
                if piece == "\n":
                    pieces.append(piece)
                else:
                    self._carry = piece
                break
            self._count(piece)
            pieces.append(piece)
            if piece.endswith("\n"):
                break
            if piece.endswith("\r") and len(piece) < _LINE_PIECE_CHARS:
                break
        if not pieces:
            raise StopIteration
        return "".join(pieces)

    def _count(self, piece: str) -> None:
        self._delimiters += piece.count(self._delimiter)
        if self._delimiters > self._limits.max_csv_record_delimiters:
            raise _too_many_columns(self._limits)


def _too_many_columns(limits: ParseLimits) -> ParseError:
    return ParseError(
        ParseErrorCode.TOO_MANY_COLUMNS,
        f"The file has more than {limits.max_columns:,} columns.",
    )


def _csv_error(exc: csv.Error, max_cell_chars: int) -> ParseError:
    # The csv module signals an oversized field only through its message.
    if "field larger than field limit" in str(exc):
        return ParseError(
            ParseErrorCode.CELL_TOO_LONG, f"A cell is longer than {max_cell_chars:,} characters."
        )
    return ParseError(ParseErrorCode.FILE_CORRUPT, "The CSV file is malformed.")


def _decode_error(encoding: str) -> ParseError:
    return ParseError(
        ParseErrorCode.ENCODING_UNKNOWN,
        f"The file is not valid {encoding} text. Choose another encoding.",
    )


@contextmanager
def _field_size_limit(limit: int) -> Iterator[None]:
    """Cap the csv module's per-field size for this parse (the setting is process-wide)."""
    previous = csv.field_size_limit(limit)
    try:
        yield
    finally:
        csv.field_size_limit(previous)
