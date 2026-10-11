"""The progressive login delay schedule and the (email, IP) throttle key."""

import re

import pytest

from app.core.config import Settings
from app.services.login_throttle import delay_seconds, throttle_key
from tests.conftest import make_settings


@pytest.fixture
def throttle_settings() -> Settings:
    return make_settings(
        login_free_attempts=4, login_base_delay_seconds=30, login_max_delay_seconds=900
    )


@pytest.mark.parametrize(
    ("failure_count", "expected"),
    [
        (0, 0),
        (1, 0),
        (2, 0),
        (3, 0),
        (4, 30),
        (5, 60),
        (6, 120),
        (7, 240),
        (8, 480),
        (9, 900),
        (10, 900),
    ],
)
def test_delay_schedule_is_free_then_doubles_up_to_the_cap(
    throttle_settings: Settings, failure_count: int, expected: int
) -> None:
    assert delay_seconds(failure_count, throttle_settings) == expected


def test_delay_stays_at_the_cap_for_huge_failure_counts(throttle_settings: Settings) -> None:
    assert delay_seconds(2_147_483_647, throttle_settings) == 900


def test_delay_follows_configured_settings() -> None:
    settings = make_settings(
        login_free_attempts=2, login_base_delay_seconds=10, login_max_delay_seconds=25
    )

    assert [delay_seconds(count, settings) for count in range(1, 6)] == [0, 10, 20, 25, 25]


def test_throttle_key_is_deterministic() -> None:
    assert throttle_key("user@example.com", "203.0.113.7") == throttle_key(
        "user@example.com", "203.0.113.7"
    )


def test_throttle_key_ignores_email_case_and_surrounding_whitespace() -> None:
    assert throttle_key("  User@Example.COM ", "203.0.113.7") == throttle_key(
        "user@example.com", "203.0.113.7"
    )


def test_throttle_key_differs_per_ip() -> None:
    assert throttle_key("user@example.com", "203.0.113.7") != throttle_key(
        "user@example.com", "198.51.100.9"
    )


def test_throttle_key_differs_per_email() -> None:
    assert throttle_key("a@example.com", "203.0.113.7") != throttle_key(
        "b@example.com", "203.0.113.7"
    )


def test_throttle_key_is_a_sha256_hex_without_the_raw_inputs() -> None:
    key = throttle_key("user@example.com", "203.0.113.7")

    assert re.fullmatch(r"[0-9a-f]{64}", key)
    assert "user" not in key
    assert "example" not in key
    assert "203.0.113.7" not in key
