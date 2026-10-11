"""Format-independent table shaping: header detection, column naming and row limits.

Every format reader turns its source into a stream of `(row_number, cells)` pairs (1-based
source row, cell texts) and hands it to `build_table`, which consumes the stream once and keeps
only the sample rows in memory. Readers yield at most `max_columns` cells per row (they reject
a row with data beyond that themselves) and may yield an empty sequence for an empty row.

Empty rows cost work without producing data, so their number is bounded: more than
`max_leading_empty_rows` consecutive empty rows while looking for the header, or more than
`max_skipped_empty_rows` empty rows below it, fail with `PARSE_FILE_TOO_COMPLEX`.
"""

from __future__ import annotations

import itertools
import re
from collections.abc import Callable, Generator, Iterable, Iterator, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.services.parsing.errors import ParseError, ParseErrorCode

if TYPE_CHECKING:
    from app.services.parsing.parse import ParseLimits

_NUMBER_RE = re.compile(r"[+-]?(?:\d[\d,]*(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?%?")
_MIN_HEADER_CELLS = 2
MAX_COLUMN_NAME_CHARS = 255
# The largest row number a spreadsheet application can address; workbook readers reject
# anything beyond it.
EXCEL_MAX_ROWS = 1_048_576

Row = tuple[int, Sequence[str]]
RowSource = Generator[Row, None, None]


@dataclass(frozen=True, slots=True)
class SheetInfo:
    """A worksheet of a workbook, in workbook order."""

    name: str
    visible: bool


@dataclass(frozen=True, slots=True)
class ColumnInfo:
    """A detected column: 0-based position in the source and its display name."""

    index: int
    name: str


@dataclass(frozen=True, slots=True)
class SampleRow:
    """A data row kept for the preview: 1-based source row number and one text per column."""

    row_number: int
    cells: list[str]


@dataclass(frozen=True, slots=True)
class TableShape:
    """Everything `build_table` learned from one pass over the rows."""

    header_row: int
    columns: list[ColumnInfo]
    row_count: int
    column_count: int
    sample_rows: list[SampleRow]
    skipped_empty_rows: int


@dataclass(frozen=True, slots=True)
class WorkbookTable:
    """Result of reading one sheet of a workbook."""

    sheets: list[SheetInfo]
    sheet_name: str
    table: TableShape


def select_sheet(
    sheets: Sequence[SheetInfo],
    requested: str | None,
    open_rows: Callable[[str], RowSource],
    limits: ParseLimits,
) -> tuple[str, Iterator[Row]]:
    """The requested sheet, else the first visible sheet with data, else any sheet with data.

    Returns the chosen name and its rows. Sheets are probed only up to their first non-empty
    row and the chosen sheet's rows continue from there, so no sheet is read twice.
    """
    if requested is not None:
        if any(sheet.name == requested for sheet in sheets):
            return requested, open_rows(requested)
        raise ParseError(ParseErrorCode.SHEET_NOT_FOUND, "The selected sheet does not exist.")
    ordered = [s for s in sheets if s.visible] + [s for s in sheets if not s.visible]
    for sheet in ordered:
        rows = open_rows(sheet.name)
        first = _first_non_empty_row(rows, limits)
        if first is not None:
            return sheet.name, itertools.chain([first], rows)
        rows.close()
    raise ParseError(ParseErrorCode.EMPTY_FILE, "The workbook contains no data.")


def too_complex(message: str) -> ParseError:
    """A `PARSE_FILE_TOO_COMPLEX` failure with a user-facing message."""
    return ParseError(ParseErrorCode.FILE_TOO_COMPLEX, message)


def _first_non_empty_row(rows: Iterator[Row], limits: ParseLimits) -> Row | None:
    for consecutive_empty, row in enumerate(rows, start=1):
        if _trimmed_width(row[1]):
            return row
        _check_leading_empty_rows(consecutive_empty, limits)
    return None


def build_table(rows: Iterable[Row], header_row: int | None, limits: ParseLimits) -> TableShape:
    """Locate the header, then count and sample the data rows below it.

    `header_row` is the explicit 1-based source row of the header; `None` auto-detects it.
    Raises `ParseError` for an empty source, a missing header or an exceeded limit.
    """
    stream = iter(rows)
    header_number, header_cells = _find_header(stream, header_row, limits)
    header_width = _trimmed_width(header_cells)

    row_count = 0
    skipped = 0
    data_width = 0
    samples: list[tuple[int, list[str]]] = []
    for row_number, cells in stream:
        # An empty sequence is the readers' cheap form of an empty row.
        width = _trimmed_width(cells) if cells else 0
        if width == 0:
            skipped += 1
            if skipped > limits.max_skipped_empty_rows:
                raise too_complex(
                    f"The file has more than {limits.max_skipped_empty_rows:,} empty rows."
                )
            continue
        _check_cell_lengths(cells, limits)
        _check_column_count(width, limits)
        row_count += 1
        if row_count > limits.max_rows:
            raise ParseError(
                ParseErrorCode.TOO_MANY_ROWS,
                f"The file has more than {limits.max_rows:,} data rows.",
            )
        data_width = max(data_width, width)
        if len(samples) < limits.sample_rows:
            samples.append(
                (row_number, [cell[: limits.sample_cell_chars] for cell in cells[:width]])
            )

    column_count = max(header_width, data_width)
    padding = [""] * column_count
    return TableShape(
        header_row=header_number,
        columns=name_columns(header_cells[:column_count], column_count),
        row_count=row_count,
        column_count=column_count,
        sample_rows=[
            SampleRow(row_number=number, cells=(cells + padding)[:column_count])
            for number, cells in samples
        ],
        skipped_empty_rows=skipped,
    )


def name_columns(header_cells: Sequence[str], column_count: int) -> list[ColumnInfo]:
    """Column names from the header: stripped text, `Column N` when blank, `Name (2)` on repeats.

    Names are capped at `MAX_COLUMN_NAME_CHARS`, suffix included, and stay unique.
    """
    used: set[str] = set()
    columns: list[ColumnInfo] = []
    for index in range(column_count):
        text = header_cells[index].strip() if index < len(header_cells) else ""
        base = (text or f"Column {index + 1}")[:MAX_COLUMN_NAME_CHARS]
        name = base
        suffix = 2
        while name in used:
            tail = f" ({suffix})"
            name = base[: MAX_COLUMN_NAME_CHARS - len(tail)] + tail
            suffix += 1
        used.add(name)
        columns.append(ColumnInfo(index=index, name=name))
    return columns


def looks_like_header(cells: Sequence[str]) -> bool:
    """At least two filled cells, and at least half of them are text rather than numbers."""
    filled = [cell.strip() for cell in cells if cell.strip()]
    if len(filled) < _MIN_HEADER_CELLS:
        return False
    textual = sum(1 for cell in filled if not _NUMBER_RE.fullmatch(cell))
    return textual * 2 >= len(filled)


def _find_header(
    stream: Iterator[Row], header_row: int | None, limits: ParseLimits
) -> tuple[int, Sequence[str]]:
    non_empty_seen = 0
    consecutive_empty = 0
    for row_number, cells in stream:
        _check_cell_lengths(cells, limits)
        width = _trimmed_width(cells)
        if width:
            non_empty_seen += 1
            consecutive_empty = 0
        else:
            consecutive_empty += 1
            _check_leading_empty_rows(consecutive_empty, limits)
        if header_row is not None:
            if row_number < header_row:
                continue
            _check_explicit_header(row_number, width, header_row)
            _check_column_count(width, limits)
            return row_number, cells
        if width == 0:
            continue
        if looks_like_header(cells):
            _check_column_count(width, limits)
            return row_number, cells
        if non_empty_seen >= limits.header_scan_rows:
            break
    else:
        if non_empty_seen == 0:
            raise ParseError(ParseErrorCode.EMPTY_FILE, "The file contains no data.")
        if header_row is not None:
            raise ParseError(
                ParseErrorCode.NO_HEADER,
                f"Header row {header_row} is beyond the end of the data.",
            )
    raise ParseError(
        ParseErrorCode.NO_HEADER,
        f"No header row was found in the first {limits.header_scan_rows} non-empty rows. "
        "Choose the header row explicitly.",
    )


def _check_explicit_header(row_number: int, width: int, header_row: int) -> None:
    if row_number > header_row:
        # Only CSV numbers rows by physical line, where a quoted multi-line record can span
        # the requested line.
        raise ParseError(
            ParseErrorCode.NO_HEADER,
            f"Header row {header_row} is inside a multi-line row. "
            "Choose the line where the header starts.",
        )
    if width == 0:
        raise ParseError(ParseErrorCode.NO_HEADER, f"Header row {header_row} is empty.")


def _trimmed_width(cells: Sequence[str]) -> int:
    """Number of cells up to and including the last non-blank one."""
    for position in range(len(cells) - 1, -1, -1):
        if cells[position].strip():
            return position + 1
    return 0


def _check_leading_empty_rows(consecutive_empty: int, limits: ParseLimits) -> None:
    if consecutive_empty > limits.max_leading_empty_rows:
        raise too_complex(
            f"The file has more than {limits.max_leading_empty_rows:,} consecutive empty rows "
            "before its header."
        )


def _check_cell_lengths(cells: Sequence[str], limits: ParseLimits) -> None:
    if any(len(cell) > limits.max_cell_chars for cell in cells):
        raise ParseError(
            ParseErrorCode.CELL_TOO_LONG,
            f"A cell is longer than {limits.max_cell_chars:,} characters.",
        )


def _check_column_count(width: int, limits: ParseLimits) -> None:
    if width > limits.max_columns:
        raise ParseError(
            ParseErrorCode.TOO_MANY_COLUMNS,
            f"The file has more than {limits.max_columns:,} columns.",
        )
