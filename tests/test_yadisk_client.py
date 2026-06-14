import pytest

from src.yadisk_client import YaDiskClient


@pytest.mark.parametrize(
    ("filename", "spec_number", "expected"),
    [
        ("ДОПП 47589 от 123456.pdf", "47589", True),
        ("ДОПП 47589/1 от 123456.pdf", "47589", True),
        ("document.pdf", "47589", False),
        ("47589.pdf", "47589", True),
        ("софт AVN2218.pdf", "AVN2218", True),
    ],
)
def test_filename_matches_spec(filename, spec_number, expected):
    assert YaDiskClient._filename_matches_spec(None, filename, spec_number) is expected
