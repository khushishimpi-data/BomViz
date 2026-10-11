"""Parse failures with stable codes and messages that are safe to show to users."""

from enum import StrEnum


class ParseErrorCode(StrEnum):
    """Job-level error codes produced by the parser."""

    ENCODING_UNKNOWN = "PARSE_ENCODING_UNKNOWN"
    CELL_TOO_LONG = "PARSE_CELL_TOO_LONG"
    FILE_TOO_COMPLEX = "PARSE_FILE_TOO_COMPLEX"
    FILE_CORRUPT = "PARSE_FILE_CORRUPT"
    ENCRYPTED_WORKBOOK = "ENCRYPTED_WORKBOOK"
    SHEET_NOT_FOUND = "PARSE_SHEET_NOT_FOUND"
    EMPTY_FILE = "PARSE_EMPTY_FILE"
    NO_HEADER = "PARSE_NO_HEADER"
    TOO_MANY_ROWS = "PARSE_TOO_MANY_ROWS"
    TOO_MANY_COLUMNS = "PARSE_TOO_MANY_COLUMNS"


class ParseError(Exception):
    """The file cannot be parsed for a reason the user can act on.

    `message` is shown to users as is: it must never contain file paths, library internals or
    raw cell content. The underlying exception (if any) is chained as `__cause__` for logs.
    """

    def __init__(self, code: ParseErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def __repr__(self) -> str:
        return f"ParseError(code={self.code.value!r}, message={self.message!r})"
