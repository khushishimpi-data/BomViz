import pytest

from app.services.parsing import ALLOWED_ENCODINGS, ParseLimits, ParseOptions
from tests.conftest import make_settings


def test_encoding_allowlist_matches_the_api_contract() -> None:
    assert set(ALLOWED_ENCODINGS) == {
        "utf-8",
        "utf-8-sig",
        "utf-16",
        "cp1252",
        "latin-1",
        "iso-8859-15",
    }


@pytest.mark.parametrize(
    "options",
    [
        {"encoding": "utf-32"},
        {"encoding": "UTF8"},
        {"delimiter": ":"},
        {"header_row": 0},
        {"header_row": 1001},
        {"sheet_name": ""},
        {"sheet_name": "x" * 256},
    ],
)
def test_options_outside_the_allowlist_are_rejected(options: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        ParseOptions(**options)  # type: ignore[arg-type]


def test_limits_come_from_settings() -> None:
    settings = make_settings(
        parse_max_rows=10,
        parse_max_columns=5,
        parse_max_cell_chars=50,
        parse_sample_rows=3,
        parse_max_uncompressed_bytes=1000,
    )

    limits = ParseLimits.from_settings(settings)

    assert (
        limits.max_rows,
        limits.max_columns,
        limits.max_cell_chars,
        limits.sample_rows,
        limits.max_uncompressed_bytes,
    ) == (10, 5, 50, 3, 1000)
