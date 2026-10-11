"""Entry point of the parser: options, limits, outcome and format dispatch."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Self

from app.core.config import Settings
from app.services.parsing import csv_parser, xls_parser, xlsx_parser
from app.services.parsing.header import ColumnInfo, SampleRow, SheetInfo

ALLOWED_DELIMITERS: frozenset[str] = frozenset({",", ";", "\t", "|"})
ALLOWED_ENCODINGS: tuple[str, ...] = (
    "utf-8",
    "utf-8-sig",
    "utf-16",
    "cp1252",
    "latin-1",
    "iso-8859-15",
)
MAX_SHEET_NAME_CHARS = 255
MAX_HEADER_ROW = 1000


@dataclass(frozen=True, slots=True)
class ParseOptions:
    """Caller-chosen options; `None` means "detect". Format-irrelevant options are ignored.

    Values are validated here as a second line of defence behind the API schema; an invalid
    value is a programming error (`ValueError`), not a user-facing parse failure.
    """

    sheet_name: str | None = None
    header_row: int | None = None
    delimiter: str | None = None
    encoding: str | None = None

    def __post_init__(self) -> None:
        if self.sheet_name is not None and not 1 <= len(self.sheet_name) <= MAX_SHEET_NAME_CHARS:
            raise ValueError("sheet_name must be 1-255 characters")
        if self.header_row is not None and not 1 <= self.header_row <= MAX_HEADER_ROW:
            raise ValueError("header_row must be between 1 and 1000")
        if self.delimiter is not None and self.delimiter not in ALLOWED_DELIMITERS:
            raise ValueError("delimiter is not one of the allowed delimiters")
        if self.encoding is not None and self.encoding not in ALLOWED_ENCODINGS:
            raise ValueError("encoding is not one of the allowed encodings")


@dataclass(frozen=True, slots=True)
class ParseLimits:
    """Resource ceilings applied while parsing. Defaults match the settings defaults.

    The empty-row and CSV-record bounds only stop crafted files from burning time and memory;
    they sit far above anything a real BOM contains.
    """

    max_rows: int = 50_000
    max_columns: int = 200
    max_cell_chars: int = 32_767
    sample_rows: int = 100
    max_uncompressed_bytes: int = 200 * 1024 * 1024
    max_zip_members: int = 10_000
    max_compression_ratio: int = 100
    max_sheets_listed: int = 50
    sample_cell_chars: int = 500
    header_scan_rows: int = 20
    # Consecutive empty rows tolerated before the header is found.
    max_leading_empty_rows: int = 10_000
    # Empty rows tolerated below the header: max(10 x max_rows, 100,000).
    max_skipped_empty_rows: int = 500_000
    # Delimiter characters (quoted or not) tolerated in one CSV record.
    max_csv_record_delimiters: int = 100_000

    @classmethod
    def from_settings(cls, settings: Settings) -> Self:
        return cls(
            max_rows=settings.parse_max_rows,
            max_columns=settings.parse_max_columns,
            max_cell_chars=settings.parse_max_cell_chars,
            sample_rows=settings.parse_sample_rows,
            max_uncompressed_bytes=settings.parse_max_uncompressed_bytes,
            max_skipped_empty_rows=max(10 * settings.parse_max_rows, 100_000),
        )


@dataclass(frozen=True, slots=True)
class ParseOutcome:
    """Result of a successful parse.

    `effective_options` records what was actually used (detected or explicit), so a re-parse
    with these options reproduces the same result. `sheets` is empty for CSV.
    """

    effective_options: ParseOptions
    sheets: list[SheetInfo]
    columns: list[ColumnInfo]
    row_count: int
    column_count: int
    sample_rows: list[SampleRow] = field(default_factory=list)
    skipped_empty_rows: int = 0


def parse_file(
    path: Path,
    file_format: str,
    options: ParseOptions | None = None,
    limits: ParseLimits | None = None,
) -> ParseOutcome:
    """Parse the file at `path` (`CSV`, `XLSX` or `XLS`) into a bounded preview outcome.

    Raises `ParseError` for every user-actionable failure.
    """
    opts = options or ParseOptions()
    lim = limits or ParseLimits()
    match file_format.upper():
        case "CSV":
            csv_result = csv_parser.read_csv(
                path,
                delimiter=opts.delimiter,
                encoding=opts.encoding,
                header_row=opts.header_row,
                limits=lim,
            )
            table = csv_result.table
            effective = ParseOptions(
                header_row=table.header_row,
                delimiter=csv_result.delimiter,
                encoding=csv_result.encoding,
            )
            sheets: list[SheetInfo] = []
        case "XLSX" | "XLS":
            reader = xlsx_parser.read_xlsx if file_format.upper() == "XLSX" else xls_parser.read_xls
            book = reader(path, sheet_name=opts.sheet_name, header_row=opts.header_row, limits=lim)
            table = book.table
            effective = ParseOptions(sheet_name=book.sheet_name, header_row=table.header_row)
            sheets = book.sheets
        case _:
            raise ValueError(f"unsupported file format {file_format!r}")
    return ParseOutcome(
        effective_options=effective,
        sheets=sheets,
        columns=table.columns,
        row_count=table.row_count,
        column_count=table.column_count,
        sample_rows=table.sample_rows,
        skipped_empty_rows=table.skipped_empty_rows,
    )
