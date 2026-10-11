"""Conversion of spreadsheet cell values to the text shown in previews and used downstream."""

import datetime as dt
import math
from decimal import Decimal

# Decimal exponents within this range are written out in full ("0.000001", "120000000");
# anything outside keeps scientific notation so a pathological value stays short.
_PLAIN_EXPONENT_MIN = -20
_PLAIN_EXPONENT_MAX = 20


def cell_to_text(value: object) -> str:
    """Render one cell value as text.

    Strings lose a trailing carriage return, integers stay integers, floats use their shortest
    round-tripping decimal form without a trailing `.0` or exponent (`4.0` -> `4`, `0.1` -> `0.1`,
    `1e-07` -> `0.0000001`), booleans become `TRUE`/`FALSE`, dates and datetimes use ISO 8601
    (a datetime at midnight is shown as a date, because spreadsheets store plain dates as
    midnight datetimes) and empty cells become `""`.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value.rstrip("\r")
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int | float | Decimal):
        return number_to_text(value)
    if isinstance(value, dt.datetime | dt.date | dt.time):
        return temporal_to_text(value)
    return str(value)


def number_to_text(value: int | float | Decimal) -> str:
    """Shortest exact decimal form of a number, without exponent for normal magnitudes."""
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            return str(value)
        number = Decimal(repr(value))
    else:
        if not value.is_finite():
            return str(value)
        number = value
    if number.is_zero():
        return "0"
    normalized = number.normalize()
    if _PLAIN_EXPONENT_MIN <= normalized.adjusted() <= _PLAIN_EXPONENT_MAX:
        return format(normalized, "f")
    return str(normalized)


def temporal_to_text(value: dt.datetime | dt.date | dt.time) -> str:
    """ISO 8601 text for a date, datetime or time of day."""
    if isinstance(value, dt.datetime):
        if value.time() == dt.time(0, 0) and value.tzinfo is None:
            return value.date().isoformat()
        return value.isoformat()
    return value.isoformat()
