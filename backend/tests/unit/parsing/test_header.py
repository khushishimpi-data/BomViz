from collections.abc import Callable, Sequence

import pytest

from app.services.mapping_suggestions import count_standard_field_matches
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


# --- header detection by standard-field matches ----------------------------------------------

FAN_HEADER = [
    "Level",
    "Item Code",
    "Component / Material",
    "Specification / Notes",
    "UOM",
    "Qty per Finished Unit",
    "Scrap %",
    "Effective Qty per Unit",
    "Batch Qty Required",
    "Unit Cost (₹)",
]


def fan_rows() -> list[Row]:
    """A title and a key/value information block above the table, as in manufacturing exports."""
    return numbered(
        ["MANUFACTURING BILL OF MATERIALS"],
        [],
        ["Product", "Electric Table Fan — 400 mm"],
        ["Finished Good Code", "FG-FAN-400"],
        ["BOM Revision", "A"],
        ["BOM Type", "Manufacturing BOM"],
        ["Production Batch Size (units)", "100"],
        ["Currency", "INR (₹)"],
        [],
        FAN_HEADER,
        ["1", "RM-001", "Front grill", "Steel wire", "Nos", "1", "0.02", "", "", "85"],
        ["1", "RM-002", "Rear grill", "Steel wire", "Nos", "1", "0.02", "", "", "80"],
    )


def scores_from(table: dict[str, int]) -> Callable[[Sequence[str]], int]:
    """A scorer that knows a fixed score per first cell (0 for anything else)."""
    return lambda cells: table.get(cells[0], 0) if cells else 0


def test_scored_detection_skips_an_information_block_above_the_table() -> None:
    table = build_table(fan_rows(), None, LIMITS, count_standard_field_matches)

    assert table.header_row == 10
    assert [c.name for c in table.columns] == FAN_HEADER
    assert [r.row_number for r in table.sample_rows] == [11, 12]
    assert table.row_count == 2


def test_without_a_scorer_the_information_block_is_taken_as_the_header() -> None:
    assert build_table(fan_rows(), None, LIMITS).header_row == 3


def test_scored_detection_keeps_a_header_on_the_first_row() -> None:
    rows = numbered(["Part Number", "Name", "Qty"], ["A-1", "Bolt", "2"], ["A-2", "Nut", "4"])

    table = build_table(rows, None, LIMITS, count_standard_field_matches)

    assert table.header_row == 1
    assert table.row_count == 2


def test_rows_scoring_below_two_fall_back_to_the_text_heuristic() -> None:
    rows = numbered(["Assembly BOM"], ["Colour", "Shade"], ["Red", "Dark"])
    scorer = scores_from({"Colour": 1, "Red": 1})

    with_scorer = build_table(rows, None, LIMITS, scorer)

    assert with_scorer == build_table(rows, None, LIMITS)
    assert with_scorer.header_row == 2


def test_fallback_without_a_header_like_row_fails_as_before() -> None:
    rows = numbered(*([["1", "2"]] * 3), ["Part", "Qty"])

    with pytest.raises(ParseError) as exc:
        build_table(rows, None, ParseLimits(header_scan_rows=3), scores_from({}))

    assert exc.value.code is ParseErrorCode.NO_HEADER


def test_the_highest_score_wins_over_an_earlier_header_like_row() -> None:
    rows = numbered(["Part", "Qty"], ["Item", "Count", "Name"], ["A", "1", "x"])

    table = build_table(rows, None, LIMITS, scores_from({"Part": 2, "Item": 3}))

    assert table.header_row == 2


def test_equal_scores_go_to_the_row_with_more_text_cells() -> None:
    rows = numbered(["Part", "Qty", "1"], ["Item", "Count", "Name"], ["A", "1", "x"])

    table = build_table(rows, None, LIMITS, scores_from({"Part": 2, "Item": 2}))

    assert table.header_row == 2


def test_equal_scores_and_text_cells_go_to_the_earlier_row() -> None:
    rows = numbered(["Part", "Qty"], ["Item", "Count"], ["A", "1"])

    table = build_table(rows, None, LIMITS, scores_from({"Part": 2, "Item": 2}))

    assert table.header_row == 1
    assert [(r.row_number, r.cells) for r in table.sample_rows] == [
        (2, ["Item", "Count"]),
        (3, ["A", "1"]),
    ]


def test_explicit_header_row_wins_over_the_scorer() -> None:
    table = build_table(fan_rows(), 3, LIMITS, count_standard_field_matches)

    assert table.header_row == 3


def test_only_rows_within_the_score_window_are_scored() -> None:
    rows = numbered(["Part", "Qty"], ["A", "1"], ["Item", "Count"])
    scorer = scores_from({"Part": 2, "Item": 5})
    limits = ParseLimits(header_scan_rows=1, header_score_scan_rows=2)

    table = build_table(rows, None, limits, scorer)

    assert table.header_row == 1
    assert table.row_count == 2


def test_scanned_rows_below_the_scored_header_keep_their_empty_rows_counted() -> None:
    rows = numbered(["Note"], [], ["Part", "Qty"], [], ["A", "1"], [], [])

    table = build_table(rows, None, LIMITS, scores_from({"Part": 2}))

    assert table.header_row == 3
    assert table.row_count == 1
    assert table.skipped_empty_rows == 3


def test_scored_detection_of_only_empty_rows_is_an_empty_file() -> None:
    with pytest.raises(ParseError) as exc:
        build_table(numbered([], [""]), None, LIMITS, scores_from({}))

    assert exc.value.code is ParseErrorCode.EMPTY_FILE


def test_scored_detection_keeps_the_leading_empty_row_limit() -> None:
    rows = numbered(*([[]] * 4), ["Part", "Qty"])

    with pytest.raises(ParseError) as exc:
        build_table(rows, None, ParseLimits(max_leading_empty_rows=3), scores_from({"Part": 2}))

    assert exc.value.code is ParseErrorCode.FILE_TOO_COMPLEX


def test_scored_header_wider_than_the_column_limit_fails() -> None:
    rows = numbered(["Part", "Qty", "Cost"], ["A", "1", "2"])

    with pytest.raises(ParseError) as exc:
        build_table(rows, None, ParseLimits(max_columns=2), scores_from({"Part": 2}))

    assert exc.value.code is ParseErrorCode.TOO_MANY_COLUMNS
