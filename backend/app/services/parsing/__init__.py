"""Parsing of uploaded BOM files (CSV, XLSX, XLS) into a bounded preview.

Pure and synchronous: no database or object-storage access. Callers pass a local file path.
"""

from app.services.parsing.errors import ParseError, ParseErrorCode
from app.services.parsing.header import ColumnInfo, SampleRow, SheetInfo
from app.services.parsing.parse import (
    ALLOWED_DELIMITERS,
    ALLOWED_ENCODINGS,
    ParseLimits,
    ParseOptions,
    ParseOutcome,
    parse_file,
)

__all__ = [
    "ALLOWED_DELIMITERS",
    "ALLOWED_ENCODINGS",
    "ColumnInfo",
    "ParseError",
    "ParseErrorCode",
    "ParseLimits",
    "ParseOptions",
    "ParseOutcome",
    "SampleRow",
    "SheetInfo",
    "parse_file",
]
