"""Legacy .xls (BIFF) reading with xlrd, loading only the sheets that are needed.

Each row is converted only up to `max_columns` cells; a non-empty cell beyond that fails the
parse with `PARSE_TOO_MANY_COLUMNS`.
"""

from __future__ import annotations

import datetime as dt
import os
import struct
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import xlrd
from xlrd.biffh import XLRDError, error_text_from_code
from xlrd.compdoc import CompDocError
from xlrd.xldate import XLDateError, xldate_as_datetime

from app.services.parsing.cells import cell_to_text
from app.services.parsing.errors import ParseError, ParseErrorCode
from app.services.parsing.header import (
    EXCEL_MAX_ROWS,
    HeaderScorer,
    RowSource,
    SheetInfo,
    WorkbookTable,
    build_table,
    select_sheet,
    too_complex,
)

if TYPE_CHECKING:
    from xlrd.book import Book
    from xlrd.sheet import Sheet

    from app.services.parsing.parse import ParseLimits

# xlrd reports damage through many exception types (it has no single base class for
# malformed input); these are the ones observed for truncated or garbled files.
_CORRUPT_ERRORS = (
    XLRDError,
    CompDocError,
    struct.error,
    ValueError,
    IndexError,
    KeyError,
    AssertionError,
    EOFError,
)
_CORRUPT_MESSAGE = "The Excel file is damaged or is not a valid .xls workbook."
_ENCRYPTED_MESSAGE = "The workbook is password-protected. Remove the password and upload it again."
_VISIBLE = 0
_EMPTY_TYPES = frozenset({xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK})


def read_xls(
    path: Path,
    *,
    sheet_name: str | None,
    header_row: int | None,
    limits: ParseLimits,
    header_scorer: HeaderScorer | None = None,
) -> WorkbookTable:
    """Parse one sheet of an .xls workbook; dates use the workbook's own date system."""
    with Path(os.devnull).open("w") as quiet:
        try:
            book = xlrd.open_workbook(
                str(path), logfile=quiet, verbosity=0, on_demand=True, formatting_info=False
            )
        except XLRDError as exc:
            if "encrypted" in str(exc).lower():
                raise ParseError(ParseErrorCode.ENCRYPTED_WORKBOOK, _ENCRYPTED_MESSAGE) from exc
            raise ParseError(ParseErrorCode.FILE_CORRUPT, _CORRUPT_MESSAGE) from exc
        except _CORRUPT_ERRORS as exc:
            raise ParseError(ParseErrorCode.FILE_CORRUPT, _CORRUPT_MESSAGE) from exc
        try:
            return _read_book(book, sheet_name, header_row, limits, header_scorer)
        finally:
            book.release_resources()


def _read_book(
    book: Book,
    sheet_name: str | None,
    header_row: int | None,
    limits: ParseLimits,
    header_scorer: HeaderScorer | None,
) -> WorkbookTable:
    names = book.sheet_names()
    # Visibility is read from the workbook globals so hidden sheets need not be loaded.
    visibility: list[int] = getattr(book, "_sheet_visibility", [])
    sheets = [
        SheetInfo(
            name=name,
            visible=(visibility[index] if index < len(visibility) else _VISIBLE) == _VISIBLE,
        )
        for index, name in enumerate(names)
    ]

    chosen, rows = select_sheet(
        sheets, sheet_name, lambda name: _iter_rows(book, _load_sheet(book, name), limits), limits
    )
    table = build_table(rows, header_row, limits, header_scorer)
    return WorkbookTable(sheets=sheets[: limits.max_sheets_listed], sheet_name=chosen, table=table)


def _load_sheet(book: Book, name: str) -> Sheet:
    try:
        return book.sheet_by_name(name)
    except _CORRUPT_ERRORS as exc:
        raise ParseError(ParseErrorCode.FILE_CORRUPT, _CORRUPT_MESSAGE) from exc


def _iter_rows(book: Book, sheet: Sheet, limits: ParseLimits) -> RowSource:
    if sheet.nrows > EXCEL_MAX_ROWS:
        raise too_complex(f"The sheet has rows beyond row {EXCEL_MAX_ROWS:,}.")
    width = limits.max_columns
    try:
        for index in range(sheet.nrows):
            types = sheet.row_types(index, 0, width)
            if all(cell_type in _EMPTY_TYPES for cell_type in types):
                cells: list[str] = []
            else:
                values = sheet.row_values(index, 0, width)
                cells = [
                    _cell_text(cell_type, value, book.datemode)
                    for cell_type, value in zip(types, values, strict=True)
                ]
            if sheet.row_len(index) > width:
                _check_extra_cells(book, sheet, index, width)
            yield index + 1, cells
    finally:
        # A loaded sheet keeps all its cells in memory; free it once it has been read
        # (or abandoned while choosing the default sheet).
        book.unload_sheet(sheet.name)


def _check_extra_cells(book: Book, sheet: Sheet, index: int, width: int) -> None:
    types = sheet.row_types(index, width)
    values = sheet.row_values(index, width)
    for cell_type, value in zip(types, values, strict=True):
        if cell_type not in _EMPTY_TYPES and _cell_text(cell_type, value, book.datemode).strip():
            raise ParseError(
                ParseErrorCode.TOO_MANY_COLUMNS,
                f"The file has more than {width:,} columns.",
            )


def _cell_text(cell_type: int, value: object, datemode: Literal[0, 1]) -> str:
    if cell_type == xlrd.XL_CELL_DATE and isinstance(value, float):
        return cell_to_text(_xls_date(value, datemode))
    if cell_type == xlrd.XL_CELL_BOOLEAN:
        return cell_to_text(bool(value))
    if cell_type == xlrd.XL_CELL_ERROR and isinstance(value, int):
        return error_text_from_code.get(value, "#ERROR")
    if cell_type in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
        return ""
    return cell_to_text(value)


def _xls_date(value: float, datemode: Literal[0, 1]) -> dt.datetime | dt.time | float:
    """A serial date as a datetime, a fraction of a day as a time; the number if invalid."""
    try:
        moment = xldate_as_datetime(value, datemode)
    except XLDateError:
        return value
    if 0 <= value < 1:
        return moment.time()
    return moment
