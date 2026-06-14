import os
from dataclasses import dataclass

os.environ.setdefault("BOT_TOKEN", "test-bot-token")
os.environ.setdefault("YANDEX_DISK_TOKEN", "test-yadisk-token")

import pytest

from src.handlers import get_driver_info, validate_spec_number


@dataclass
class MockUser:
    id: int
    username: str | None = None
    first_name: str | None = None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("47589", "47589"),
        ("47589/1", "47589/1"),
        ("AVN2218", "AVN2218"),
        ("А1842/2", "А1842/2"),
        (" 47589 ", "47589"),
    ],
)
def test_validate_spec_number_accepts_valid_values(text, expected):
    assert validate_spec_number(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "",
        "47589 abc",
        "47589!",
        "47589/1/2",
    ],
)
def test_validate_spec_number_rejects_invalid_values(text):
    assert validate_spec_number(text) is None


def test_get_driver_info_returns_username_when_present():
    user = MockUser(id=123, username="username", first_name="Ivan")

    assert get_driver_info(user) == "@username"


def test_get_driver_info_returns_first_name_when_username_missing():
    user = MockUser(id=123, username=None, first_name="Ivan")

    assert get_driver_info(user) == "Ivan"


def test_get_driver_info_returns_id_when_username_and_first_name_missing():
    user = MockUser(id=123, username=None, first_name=None)

    assert get_driver_info(user) == "ID: 123"
