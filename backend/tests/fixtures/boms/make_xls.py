"""Regenerate the committed legacy `.xls` test fixtures (xlwt is a dev-only dependency).

Run from `backend/`: `uv run --frozen python tests/fixtures/boms/make_xls.py`.
"""

import datetime as dt
from pathlib import Path
from typing import Any

import xlwt

HERE = Path(__file__).resolve().parent
DATE_STYLE = xlwt.easyxf(num_format_str="YYYY-MM-DD")
DATETIME_STYLE = xlwt.easyxf(num_format_str="YYYY-MM-DD HH:MM:SS")

HEADER = ["Part Number", "Name", "Qty", "Unit Cost", "Active", "Released"]


def _write_bom(sheet: Any) -> None:
    """Title row, blank row, header on source row 3, two data rows."""
    sheet.write(0, 0, "Assembly BOM")
    for col, name in enumerate(HEADER):
        sheet.write(2, col, name)
    rows = [
        ("P-001", "Bolt", 4, 0.1, True, dt.date(2026, 10, 11)),
        ("P-002", "Nut", 2.5, 1234.5, False, dt.datetime(2026, 10, 11, 8, 30)),
    ]
    for offset, row in enumerate(rows):
        for col, value in enumerate(row):
            if isinstance(value, dt.datetime):
                sheet.write(3 + offset, col, value, DATETIME_STYLE)
            elif isinstance(value, dt.date):
                moment = dt.datetime.combine(value, dt.time())
                sheet.write(3 + offset, col, moment, DATE_STYLE)
            else:
                sheet.write(3 + offset, col, value)


def make_basic() -> None:
    book = xlwt.Workbook()
    _write_bom(book.add_sheet("BOM"))
    book.save(str(HERE / "basic.xls"))


def make_dates_1904() -> None:
    book = xlwt.Workbook()
    book.dates_1904 = True
    _write_bom(book.add_sheet("BOM"))
    book.save(str(HERE / "dates_1904.xls"))


def make_multi_sheet() -> None:
    """Hidden sheet with data first, then an empty sheet, then the visible data sheet."""
    book = xlwt.Workbook()
    hidden = book.add_sheet("Secret")
    hidden.write(0, 0, "Hidden A")
    hidden.write(0, 1, "Hidden B")
    hidden.write(1, 0, "x")
    hidden.visibility = 1
    book.add_sheet("Empty")
    parts = book.add_sheet("Parts")
    parts.write(0, 0, "Part")
    parts.write(0, 1, "Qty")
    parts.write(1, 0, "A-1")
    parts.write(1, 1, 3)
    book.save(str(HERE / "multi_sheet.xls"))


if __name__ == "__main__":
    make_basic()
    make_dates_1904()
    make_multi_sheet()
