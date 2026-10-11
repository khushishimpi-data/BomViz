import csv
import time
import tracemalloc
from pathlib import Path

import pytest

from app.services.parsing import (
    ParseError,
    ParseErrorCode,
    ParseLimits,
    ParseOptions,
    ParseOutcome,
    parse_file,
)

BOM_TEXT = "Part Number,Name,Qty\r\nP-001,Bolt,4\r\nP-002,Nut,2.5\r\n"


def write(tmp_path: Path, data: bytes) -> Path:
    path = tmp_path / "bom.csv"
    path.write_bytes(data)
    return path


def parse(
    path: Path,
    *,
    header_row: int | None = None,
    delimiter: str | None = None,
    encoding: str | None = None,
) -> ParseOutcome:
    options = ParseOptions(header_row=header_row, delimiter=delimiter, encoding=encoding)
    return parse_file(path, "CSV", options)


def test_parses_a_simple_utf8_csv(tmp_path: Path) -> None:
    outcome = parse(write(tmp_path, BOM_TEXT.encode()))

    assert outcome.effective_options == ParseOptions(header_row=1, delimiter=",", encoding="utf-8")
    assert outcome.sheets == []
    assert [c.name for c in outcome.columns] == ["Part Number", "Name", "Qty"]
    assert outcome.row_count == 2
    assert outcome.column_count == 3
    assert [(r.row_number, r.cells) for r in outcome.sample_rows] == [
        (2, ["P-001", "Bolt", "4"]),
        (3, ["P-002", "Nut", "2.5"]),
    ]


def test_utf8_byte_order_mark_is_detected_and_not_part_of_the_header(tmp_path: Path) -> None:
    outcome = parse(write(tmp_path, BOM_TEXT.encode("utf-8-sig")))

    assert outcome.effective_options.encoding == "utf-8-sig"
    assert outcome.columns[0].name == "Part Number"


def test_utf16_with_byte_order_mark_is_detected(tmp_path: Path) -> None:
    outcome = parse(write(tmp_path, "Teil,Menge\nSchraube ä,4\n".encode("utf-16")))

    assert outcome.effective_options.encoding == "utf-16"
    assert outcome.sample_rows[0].cells == ["Schraube ä", "4"]


def test_cp1252_euro_sign_is_detected(tmp_path: Path) -> None:
    text = "Part,Description,Price\nA-1,Schraube für Gehäuse,€5\nB-2,Mutter,10 €\n"

    outcome = parse(write(tmp_path, text.encode("cp1252")))

    assert outcome.effective_options.encoding == "cp1252"
    assert outcome.sample_rows[0].cells == ["A-1", "Schraube für Gehäuse", "€5"]


def test_explicit_encoding_overrides_detection(tmp_path: Path) -> None:
    outcome = parse(write(tmp_path, "Part,Price\nA,€5\n".encode("cp1252")), encoding="latin-1")

    assert outcome.effective_options.encoding == "latin-1"
    assert outcome.sample_rows[0].cells == ["A", "\x805"]


def test_bytes_invalid_for_the_explicit_encoding_fail(tmp_path: Path) -> None:
    path = write(tmp_path, "Part,Price\nA,€5\n".encode("cp1252"))

    with pytest.raises(ParseError) as exc:
        parse(path, encoding="utf-8")

    assert exc.value.code is ParseErrorCode.ENCODING_UNKNOWN
    assert str(path) not in exc.value.message


@pytest.mark.parametrize("delimiter", [";", "\t", "|"])
def test_delimiter_is_sniffed(tmp_path: Path, delimiter: str) -> None:
    text = BOM_TEXT.replace(",", delimiter)

    outcome = parse(write(tmp_path, text.encode()))

    assert outcome.effective_options.delimiter == delimiter
    assert outcome.column_count == 3


@pytest.mark.parametrize("delimiter", [";", "\t", "|"])
def test_delimiter_is_detected_below_a_title_row_with_few_data_rows(
    tmp_path: Path, delimiter: str
) -> None:
    lines = ["Bill of materials", "", "Part;Name;Qty", "A;Bolt;1", "B;Nut;2", "C;Washer;3"]
    text = "\n".join(line.replace(";", delimiter) for line in lines) + "\n"

    outcome = parse(write(tmp_path, text.encode()))

    assert outcome.effective_options.delimiter == delimiter
    assert outcome.effective_options.header_row == 3
    assert [c.name for c in outcome.columns] == ["Part", "Name", "Qty"]
    assert outcome.row_count == 3


def test_delimiter_detection_prefers_the_delimiter_present_on_most_lines(tmp_path: Path) -> None:
    text = "Assembly\nPart;Description;Qty\nA;Bolt, zinc;1\nB;Nut;2\nC;Washer, flat;3\n"

    outcome = parse(write(tmp_path, text.encode()))

    assert outcome.effective_options.delimiter == ";"
    assert outcome.sample_rows[0].cells == ["A", "Bolt, zinc", "1"]


def test_delimiter_detection_ignores_delimiters_inside_quotes(tmp_path: Path) -> None:
    text = 'Assembly\nPart|Note\nA|"x;y;z"\nB|"p;q"\n'

    outcome = parse(write(tmp_path, text.encode()))

    assert outcome.effective_options.delimiter == "|"


def test_single_column_falls_back_to_comma(tmp_path: Path) -> None:
    outcome = parse(write(tmp_path, b"Part\nA\nB\n"), header_row=1)

    assert outcome.effective_options.delimiter == ","
    assert outcome.column_count == 1


def test_explicit_delimiter_overrides_sniffing(tmp_path: Path) -> None:
    outcome = parse(write(tmp_path, b"Part;Qty,Name\nA;1,Bolt\n"), delimiter=";")

    assert [c.name for c in outcome.columns] == ["Part", "Qty,Name"]


def test_quoted_fields_may_contain_delimiters_and_newlines(tmp_path: Path) -> None:
    outcome = parse(write(tmp_path, b'Part,Note\nA,"line one\r\nline two, cont"\n'))

    assert outcome.sample_rows[0].cells == ["A", "line one\r\nline two, cont"]


def test_cell_over_the_limit_fails(tmp_path: Path) -> None:
    path = write(tmp_path, b"Part,Note\nA," + b"x" * 11 + b"\n")

    with pytest.raises(ParseError) as exc:
        parse_file(path, "CSV", limits=ParseLimits(max_cell_chars=10))

    assert exc.value.code is ParseErrorCode.CELL_TOO_LONG


def test_cell_at_the_limit_is_accepted(tmp_path: Path) -> None:
    path = write(tmp_path, b"Part,Note\nA," + b"x" * 10 + b"\n")

    outcome = parse_file(path, "CSV", limits=ParseLimits(max_cell_chars=10))

    assert outcome.sample_rows[0].cells == ["A", "x" * 10]


def test_process_wide_csv_field_limit_is_restored(tmp_path: Path) -> None:
    before = csv.field_size_limit()

    parse_file(write(tmp_path, BOM_TEXT.encode()), "CSV", limits=ParseLimits(max_cell_chars=99))

    assert csv.field_size_limit() == before


def test_too_many_rows_fails(tmp_path: Path) -> None:
    with pytest.raises(ParseError) as exc:
        parse_file(write(tmp_path, BOM_TEXT.encode()), "CSV", limits=ParseLimits(max_rows=1))

    assert exc.value.code is ParseErrorCode.TOO_MANY_ROWS


def test_too_many_columns_fails(tmp_path: Path) -> None:
    with pytest.raises(ParseError) as exc:
        parse_file(write(tmp_path, BOM_TEXT.encode()), "CSV", limits=ParseLimits(max_columns=2))

    assert exc.value.code is ParseErrorCode.TOO_MANY_COLUMNS


def test_empty_rows_are_skipped_and_counted(tmp_path: Path) -> None:
    outcome = parse(write(tmp_path, b"Part,Qty\n\nA,1\n,\nB,2\n"))

    assert outcome.row_count == 2
    assert outcome.skipped_empty_rows == 2
    assert [r.row_number for r in outcome.sample_rows] == [3, 5]


@pytest.mark.parametrize("data", [b"", b"\n\n,,\n"])
def test_file_without_data_is_empty(tmp_path: Path, data: bytes) -> None:
    with pytest.raises(ParseError) as exc:
        parse(write(tmp_path, data))

    assert exc.value.code is ParseErrorCode.EMPTY_FILE


def test_line_of_twenty_megabytes_of_delimiters_fails_fast_with_bounded_memory(
    tmp_path: Path,
) -> None:
    path = tmp_path / "commas.csv"
    with path.open("wb") as handle:
        handle.write(b"Part,Qty\n")
        for _ in range(20):
            handle.write(b"," * (1024 * 1024))
    tracemalloc.start()
    started = time.perf_counter()
    try:
        with pytest.raises(ParseError) as exc:
            parse(path)
        elapsed = time.perf_counter() - started
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()

    assert exc.value.code is ParseErrorCode.TOO_MANY_COLUMNS
    assert elapsed < 2.0
    assert peak < 50 * 1024 * 1024


def test_record_over_the_delimiter_budget_fails(tmp_path: Path) -> None:
    path = write(tmp_path, b"Part,Qty\n" + b"A,1" + b"," * 10 + b"\n")

    with pytest.raises(ParseError) as exc:
        parse_file(path, "CSV", limits=ParseLimits(max_csv_record_delimiters=10))

    assert exc.value.code is ParseErrorCode.TOO_MANY_COLUMNS


def test_empty_fields_beyond_the_column_limit_are_dropped(tmp_path: Path) -> None:
    path = write(tmp_path, b"Part,Name,Qty,,, ,\nA,Bolt,4,,,,\n")

    outcome = parse_file(path, "CSV", limits=ParseLimits(max_columns=3))

    assert [c.name for c in outcome.columns] == ["Part", "Name", "Qty"]
    assert outcome.sample_rows[0].cells == ["A", "Bolt", "4"]


def test_non_empty_field_beyond_the_column_limit_fails(tmp_path: Path) -> None:
    path = write(tmp_path, b"Part,Name,Qty\nA,Bolt,4,,extra\n")

    with pytest.raises(ParseError) as exc:
        parse_file(path, "CSV", limits=ParseLimits(max_columns=3))

    assert exc.value.code is ParseErrorCode.TOO_MANY_COLUMNS


def test_row_numbers_are_the_physical_lines_where_records_start(tmp_path: Path) -> None:
    text = b'"Assembly\nBOM"\nPart,Note\nA,"line one\nline two"\nB,x\n'

    outcome = parse(write(tmp_path, text))

    assert outcome.effective_options.header_row == 3
    assert [r.row_number for r in outcome.sample_rows] == [4, 6]


def test_explicit_header_row_is_a_physical_line(tmp_path: Path) -> None:
    text = b'"Assembly\nBOM"\nPart,Note\nA,x\n'

    outcome = parse(write(tmp_path, text), header_row=3)

    assert [c.name for c in outcome.columns] == ["Part", "Note"]


def test_explicit_header_row_inside_a_multi_line_record_fails(tmp_path: Path) -> None:
    text = b'"Assembly\nBOM"\nPart,Note\nA,x\n'

    with pytest.raises(ParseError) as exc:
        parse(write(tmp_path, text), header_row=2)

    assert exc.value.code is ParseErrorCode.NO_HEADER


@pytest.mark.parametrize("newline", ["\r\n", "\r"])
def test_line_ending_at_a_read_boundary_does_not_add_a_row(tmp_path: Path, newline: str) -> None:
    # Lines are read in 64 Ki-character pieces; this line's CR is the last character of one.
    long_line = "A," + "x" * (64 * 1024 - 3)
    text = newline.join(["Part,Note", long_line, "B,y", ""])
    path = write(tmp_path, text.encode())

    outcome = parse_file(path, "CSV", limits=ParseLimits(max_cell_chars=100_000))

    assert outcome.skipped_empty_rows == 0
    assert [r.row_number for r in outcome.sample_rows] == [2, 3]


def test_many_blank_lines_before_the_header_fail(tmp_path: Path) -> None:
    path = write(tmp_path, b"\n" * 11 + b"Part,Qty\nA,1\n")

    with pytest.raises(ParseError) as exc:
        parse_file(path, "CSV", limits=ParseLimits(max_leading_empty_rows=10))

    assert exc.value.code is ParseErrorCode.FILE_TOO_COMPLEX
