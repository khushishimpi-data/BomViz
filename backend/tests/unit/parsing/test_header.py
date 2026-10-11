from collections.abc import Sequence

import pytest

from app.services.parsing import ParseError, ParseErrorCode, ParseLimits
from app.services.parsing.header import Row, build_table, looks_like_header, name_columns

LIMITS = ParseLimits()


def numbered(*rows: Sequence[str]) -> list[Row]:
    return [(index, list(row)) for index, row in enumerate(rows, start=1)]


def test_detects_header_below_title_and_blank_rows() -> None:
    table = build_table(
        numbered(["Assembly BOM"], [], ["", ""], ["Part", "Qty"], ["A-1", "2"]), None, LIMITS
    )

    assert table.header_row == 4
    assert [c.name for c in table.columns] == ["Part", "Qty"]
    assert [(r.row_number, r.cells) for r in table.sample_rows] == [(5, ["A-1", "2"])]


def test_numeric_row_is_not_taken_as_header() -> None:
    table = build_table(
        numbered(["1", "2", "3"], ["Part", "Qty", "Cost"], ["A", "1", "2"]), None, LIMITS
    )

    assert table.header_row == 2


def test_explicit_header_row_overrides_detection() -> None:
    table = build_table(numbered(["Part", "Qty"], ["Sub", "Count"], ["A", "1"]), 2, LIMITS)

    assert table.header_row == 2
    assert [c.name for c in table.columns] == ["Sub", "Count"]
    assert table.row_count == 1


def test_explicit_header_row_that_is_empty_fails() -> None:
    with pytest.raises(ParseError) as exc:
        build_table(numbered(["Part", "Qty"], [], ["A", "1"]), 2, LIMITS)

    assert exc.value.code is ParseErrorCode.NO_HEADER


def test_explicit_header_row_beyond_the_data_fails() -> None:
    with pytest.raises(ParseError) as exc:
        build_table(numbered(["Part", "Qty"], ["A", "1"]), 9, LIMITS)

    assert exc.value.code is ParseErrorCode.NO_HEADER


def test_no_header_within_scan_window_fails() -> None:
    rows = numbered(*([["1", "2"]] * 3), ["Part", "Qty"])

    with pytest.raises(ParseError) as exc:
        build_table(rows, None, ParseLimits(header_scan_rows=3))

    assert exc.value.code is ParseErrorCode.NO_HEADER


def test_only_empty_rows_is_an_empty_file() -> None:
    with pytest.raises(ParseError) as exc:
        build_table(numbered([], ["", " "]), None, LIMITS)

    assert exc.value.code is ParseErrorCode.EMPTY_FILE


def test_long_header_names_are_capped_and_stay_unique() -> None:
    long_name = "x" * 1000

    columns = name_columns([long_name, long_name, "x" * 255], 3)

    assert [len(c.name) for c in columns] == [255, 255, 255]
    assert columns[0].name == "x" * 255
    assert columns[1].name.endswith(" (2)")
    assert columns[2].name.endswith(" (3)")
    assert len({c.name for c in columns}) == 3


def test_blank_and_duplicate_header_names_are_made_unique() -> None:
    columns = name_columns([" Part ", "", "Qty", "Part", "Part", "Column 2"], 6)

    assert [c.name for c in columns] == [
        "Part",
        "Column 2",
        "Qty",
        "Part (2)",
        "Part (3)",
        "Column 2 (2)",
    ]
    assert [c.index for c in columns] == [0, 1, 2, 3, 4, 5]


def test_trailing_columns_empty_everywhere_are_dropped() -> None:
    table = build_table(numbered(["Part", "Qty", "", ""], ["A", "1", "", " "]), None, LIMITS)

    assert table.column_count == 2
    assert [c.name for c in table.columns] == ["Part", "Qty"]
    assert table.sample_rows[0].cells == ["A", "1"]


def test_data_beyond_the_header_width_gets_generated_names_and_padding() -> None:
    table = build_table(
        numbered(["Part", "Qty"], ["A", "1", "", "extra"], ["B", "2"]), None, LIMITS
    )

    assert [c.name for c in table.columns] == ["Part", "Qty", "Column 3", "Column 4"]
    assert table.sample_rows[1].cells == ["B", "2", "", ""]


def test_empty_data_rows_are_skipped_and_counted() -> None:
    table = build_table(
        numbered(["Part", "Qty"], ["A", "1"], [], ["", ""], ["B", "2"]), None, LIMITS
    )

    assert table.row_count == 2
    assert table.skipped_empty_rows == 2
    assert [r.row_number for r in table.sample_rows] == [2, 5]


def test_sample_is_bounded_in_rows_and_cell_length() -> None:
    rows = numbered(["Part", "Note"], *[[f"P{i}", "x" * 20] for i in range(5)])

    table = build_table(rows, None, ParseLimits(sample_rows=2, sample_cell_chars=10))

    assert table.row_count == 5
    assert len(table.sample_rows) == 2
    assert table.sample_rows[0].cells == ["P0", "x" * 10]


def test_row_limit_allows_exactly_the_maximum() -> None:
    table = build_table(
        numbered(["Part", "Qty"], ["A", "1"], ["B", "2"]), None, ParseLimits(max_rows=2)
    )

    assert table.row_count == 2


def test_row_limit_rejects_one_more_than_the_maximum() -> None:
    rows = numbered(["Part", "Qty"], ["A", "1"], ["B", "2"], ["C", "3"])

    with pytest.raises(ParseError) as exc:
        build_table(rows, None, ParseLimits(max_rows=2))

    assert exc.value.code is ParseErrorCode.TOO_MANY_ROWS


def test_column_limit_rejects_wide_header() -> None:
    with pytest.raises(ParseError) as exc:
        build_table(numbered(["A", "B", "C"], ["1", "2", "3"]), None, ParseLimits(max_columns=2))

    assert exc.value.code is ParseErrorCode.TOO_MANY_COLUMNS


def test_column_limit_rejects_wide_data_row() -> None:
    with pytest.raises(ParseError) as exc:
        build_table(numbered(["A", "B"], ["1", "2", "3"]), None, ParseLimits(max_columns=2))

    assert exc.value.code is ParseErrorCode.TOO_MANY_COLUMNS


def test_cell_length_limit_is_inclusive() -> None:
    table = build_table(
        numbered(["Part", "Qty"], ["12345", "1"]), None, ParseLimits(max_cell_chars=5)
    )

    assert table.row_count == 1


def test_cell_longer_than_the_limit_fails() -> None:
    with pytest.raises(ParseError) as exc:
        build_table(numbered(["Part", "Qty"], ["123456", "1"]), None, ParseLimits(max_cell_chars=5))

    assert exc.value.code is ParseErrorCode.CELL_TOO_LONG


@pytest.mark.parametrize(
    ("cells", "expected"),
    [
        (["Part", "Qty"], True),
        (["Part", "10"], True),
        (["Part"], False),
        (["1", "2.5", "Part"], False),
        (["1,200", "3%", "-4e2"], False),
    ],
)
def test_header_heuristic(cells: list[str], expected: bool) -> None:
    assert looks_like_header(cells) is expected


def test_consecutive_empty_rows_before_the_header_up_to_the_limit_are_accepted() -> None:
    limits = ParseLimits(max_leading_empty_rows=2)

    table = build_table(numbered(["Title"], [], [], ["Part", "Qty"], ["A", "1"]), None, limits)

    assert table.header_row == 4


def test_more_consecutive_empty_rows_before_the_header_than_the_limit_fail() -> None:
    limits = ParseLimits(max_leading_empty_rows=2)

    with pytest.raises(ParseError) as exc:
        build_table(numbered([], [], [], ["Part", "Qty"], ["A", "1"]), None, limits)

    assert exc.value.code is ParseErrorCode.FILE_TOO_COMPLEX


def test_a_non_empty_row_resets_the_leading_empty_row_count() -> None:
    limits = ParseLimits(max_leading_empty_rows=2)
    rows = numbered(["Title"], [], [], ["Note"], [], [], ["Part", "Qty"], ["A", "1"])

    table = build_table(rows, None, limits)

    assert table.header_row == 7


def test_empty_rows_below_the_header_up_to_the_limit_are_accepted() -> None:
    limits = ParseLimits(max_skipped_empty_rows=2)

    table = build_table(numbered(["Part", "Qty"], [], ["A", "1"], [], ["B", "2"]), None, limits)

    assert table.skipped_empty_rows == 2


def test_more_empty_rows_below_the_header_than_the_limit_fail() -> None:
    limits = ParseLimits(max_skipped_empty_rows=2)
    rows = numbered(["Part", "Qty"], [], ["A", "1"], [], ["B", "2"], [])

    with pytest.raises(ParseError) as exc:
        build_table(rows, None, limits)

    assert exc.value.code is ParseErrorCode.FILE_TOO_COMPLEX


def test_explicit_header_row_that_does_not_start_a_row_fails() -> None:
    rows: list[Row] = [(1, ["Title"]), (3, ["Part", "Qty"]), (4, ["A", "1"])]

    with pytest.raises(ParseError) as exc:
        build_table(rows, 2, LIMITS)

    assert exc.value.code is ParseErrorCode.NO_HEADER
