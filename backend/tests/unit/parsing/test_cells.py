import datetime as dt
from decimal import Decimal

import pytest

from app.services.parsing.cells import cell_to_text


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, ""),
        ("Bolt", "Bolt"),
        ("Bolt\r", "Bolt"),
        ("  padded  ", "  padded  "),
        (42, "42"),
        (-7, "-7"),
    ],
)
def test_strings_and_integers_render_as_is(value: object, expected: str) -> None:
    assert cell_to_text(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (4.0, "4"),
        (2.5, "2.5"),
        (0.1, "0.1"),
        (1234.5, "1234.5"),
        (100.0, "100"),
        (1e-07, "0.0000001"),
        (123456789012.0, "123456789012"),
        (-0.0, "0"),
        (Decimal("1.2300"), "1.23"),
    ],
)
def test_floats_render_without_noise_or_exponent(value: object, expected: str) -> None:
    assert cell_to_text(value) == expected


def test_extreme_floats_keep_scientific_notation() -> None:
    assert cell_to_text(1e300) == "1E+300"


@pytest.mark.parametrize(("value", "expected"), [(True, "TRUE"), (False, "FALSE")])
def test_booleans_render_as_excel_literals(value: bool, expected: str) -> None:
    assert cell_to_text(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (dt.date(2026, 10, 11), "2026-10-11"),
        (dt.datetime(2026, 10, 11, 8, 30), "2026-10-11T08:30:00"),
        (dt.datetime(2026, 10, 11, 0, 0), "2026-10-11"),
        (dt.time(8, 30), "08:30:00"),
    ],
)
def test_dates_render_as_iso_8601(value: object, expected: str) -> None:
    assert cell_to_text(value) == expected
