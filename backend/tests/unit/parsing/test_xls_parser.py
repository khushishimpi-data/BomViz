from pathlib import Path

import pytest
import xlrd

from app.services.parsing import ParseError, ParseErrorCode, ParseLimits, ParseOptions, parse_file

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "boms"
EXPECTED_ROWS = [
    (4, ["P-001", "Bolt", "4", "0.1", "TRUE", "2026-10-11"]),
    (5, ["P-002", "Nut", "2.5", "1234.5", "FALSE", "2026-10-11T08:30:00"]),
]


def test_parses_typed_cells_below_a_title_row() -> None:
    outcome = parse_file(FIXTURES / "basic.xls", "XLS")

    assert outcome.effective_options == ParseOptions(sheet_name="BOM", header_row=3)
    assert [c.name for c in outcome.columns] == [
        "Part Number",
        "Name",
        "Qty",
        "Unit Cost",
        "Active",
        "Released",
    ]
    assert outcome.row_count == 2
    assert [(r.row_number, r.cells) for r in outcome.sample_rows] == EXPECTED_ROWS


def test_dates_use_the_workbook_1904_date_system() -> None:
    path = FIXTURES / "dates_1904.xls"
    book = xlrd.open_workbook(str(path), on_demand=True)
    assert book.datemode == 1  # precondition: the fixture really uses the 1904 system
    book.release_resources()

    outcome = parse_file(path, "XLS")

    assert [(r.row_number, r.cells) for r in outcome.sample_rows] == EXPECTED_ROWS


def test_default_sheet_skips_hidden_and_empty_sheets() -> None:
    outcome = parse_file(FIXTURES / "multi_sheet.xls", "XLS")

    assert outcome.effective_options.sheet_name == "Parts"
    assert [(s.name, s.visible) for s in outcome.sheets] == [
        ("Secret", False),
        ("Empty", True),
        ("Parts", True),
    ]
    assert outcome.sample_rows[0].cells == ["A-1", "3"]


def test_hidden_sheet_can_be_chosen_explicitly() -> None:
    outcome = parse_file(FIXTURES / "multi_sheet.xls", "XLS", ParseOptions(sheet_name="Secret"))

    assert [c.name for c in outcome.columns] == ["Hidden A", "Hidden B"]


def test_unknown_sheet_fails() -> None:
    with pytest.raises(ParseError) as exc:
        parse_file(FIXTURES / "multi_sheet.xls", "XLS", ParseOptions(sheet_name="Nope"))

    assert exc.value.code is ParseErrorCode.SHEET_NOT_FOUND


def test_row_limit_applies_to_xls() -> None:
    with pytest.raises(ParseError) as exc:
        parse_file(FIXTURES / "basic.xls", "XLS", limits=ParseLimits(max_rows=1))

    assert exc.value.code is ParseErrorCode.TOO_MANY_ROWS


@pytest.mark.parametrize(
    "data",
    [
        bytes.fromhex("D0CF11E0A1B11AE1") + b"\x00" * 600,
        b"not an excel file at all" * 40,
    ],
    ids=["ole-header-garbage-body", "plain-garbage"],
)
def test_damaged_xls_is_corrupt(tmp_path: Path, data: bytes) -> None:
    path = tmp_path / "bad.xls"
    path.write_bytes(data)

    with pytest.raises(ParseError) as exc:
        parse_file(path, "XLS")

    assert exc.value.code is ParseErrorCode.FILE_CORRUPT


def test_truncated_xls_is_corrupt(tmp_path: Path) -> None:
    path = tmp_path / "cut.xls"
    path.write_bytes((FIXTURES / "basic.xls").read_bytes()[:1500])

    with pytest.raises(ParseError) as exc:
        parse_file(path, "XLS")

    assert exc.value.code is ParseErrorCode.FILE_CORRUPT


def test_value_beyond_the_column_limit_fails() -> None:
    with pytest.raises(ParseError) as exc:
        parse_file(FIXTURES / "basic.xls", "XLS", limits=ParseLimits(max_columns=5))

    assert exc.value.code is ParseErrorCode.TOO_MANY_COLUMNS
