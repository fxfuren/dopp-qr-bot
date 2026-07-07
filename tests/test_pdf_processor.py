import pytest

from src.pdf_processor import (
    extract_spec_number_from_text,
    extract_vehicle_registration,
    find_spec_number_in_text,
    find_vehicle_in_text,
)


@pytest.mark.parametrize(
    ("search_number", "text"),
    [
        ("47589", "Спецификация 47589 от 26.05.2026"),
        ("47589", "Спецификация 47589/1 от 26.05.2026"),
        ("47589", "Спецификация 47589/2 от 26.05.2026"),
        ("47589/1", "6.1А НОМЕР: 47589/1 6.1Б ДАТА:"),
        ("475892", "6.1А НОМЕР: 47589/2 6.1Б ДАТА:"),
        ("А1842", "6.1А НОМЕР: А1842 6.1Б ДАТА:"),
        ("А1842", "6.1А НОМЕР: А1842/2 6.1Б ДАТА:"),
        ("AVN2218", "6.1А НОМЕР: AVN2218 6.1Б ДАТА:"),
        ("AVN2218", "6.1А НОМЕР: AVN2218/1 6.1Б ДАТА:"),
        ("47943", "6.1АНОМЕР: 47943 6.1БДАТА:"),
        ("47943", "Спецификация 47943 от"),
        ("47943", "Спецификация 47943."),
        ("47943", "Спецификация 47943,"),
    ],
)
def test_find_spec_number_in_text_matches_supported_formats(search_number, text):
    assert find_spec_number_in_text(text, search_number) is True


@pytest.mark.parametrize(
    ("text", "search_number"),
    [
        ("", "47589"),
        ("Спецификация 47589", ""),
        ("Спецификация 47589", "99999"),
        ("47943.84 Нал. база", "47943"),
        ("Стоимость 47943.16", "47943"),
        ("Декларация/47943/123", "47943"),
    ],
)
def test_find_spec_number_in_text_returns_false_when_not_found_or_empty(text, search_number):
    assert find_spec_number_in_text(text, search_number) is False


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("6.1А НОМЕР: AVN2218/1 6.1Б ДАТА:", "AVN2218/1"),
        ("6.1АНОМЕР: А1842/2 6.1БДАТА:", "А1842/2"),
        ("6.1АНОМЕР: 6.1БДАТА: 26.05.2026\n47589/2\n", "47589/2"),
        ("6.1 Спецификация No 47589/2 от 26.05.2026", "47589/2"),
        ("6.1Спецификация№AVN2218/1от", "AVN2218/1"),
    ],
)
def test_extract_spec_number_from_text_extracts_supported_patterns(text, expected):
    assert extract_spec_number_from_text(text) == expected


@pytest.mark.parametrize("text", ["", "Документ без номера спецификации"])
def test_extract_spec_number_from_text_returns_none_when_not_found_or_empty(text):
    assert extract_spec_number_from_text(text) is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "4.1А АВТО: РЕГИСТРАЦИОННЫЙ ЗНАК 4.1Б НОМЕР ПРИЦЕПА\n"
            "С542ОА67 А4351А-2\n",
            {"vehicle": "С542ОА67", "trailer": "А4351А-2"},
        ),
        (
            "4.1ААВТО:РЕГИСТРАЦИОННЫЙЗНАК 4.1БНОМЕРПРИЦЕПА\n"
            "С542ОА67 А4351А-2\n",
            {"vehicle": "С542ОА67", "trailer": "А4351А-2"},
        ),
        (
            "4.1А АВТО: РЕГИСТРАЦИОННЫЙ ЗНАК 4.1Б НОМЕР ПРИЦЕПА\n"
            "519ATP05/46BSA05\n",
            {"vehicle": "519ATP05", "trailer": "46BSA05"},
        ),
        (
            "4.1А АВТО: РЕГИСТРАЦИОННЫЙ ЗНАК 4.1Б НОМЕР ПРИЦЕПА\n"
            "С542ОА67\n",
            {"vehicle": "С542ОА67", "trailer": None},
        ),
        (
            "4.1А АВТО: РЕГИСТРАЦИОННЫЙ ЗНАК 4.1Б НОМЕР ПРИЦЕПА\n"
            "BA 5118 5 A 1295 K 5\n",
            {"vehicle": "BA51185", "trailer": "A1295K5"},
        ),
    ],
)
def test_extract_vehicle_registration_extracts_supported_patterns(text, expected):
    assert extract_vehicle_registration(text) == expected


@pytest.mark.parametrize("text", ["", "Документ без данных об автомобиле"])
def test_extract_vehicle_registration_returns_none_when_not_found_or_empty(text):
    assert extract_vehicle_registration(text) is None


@pytest.mark.parametrize(
    ("text", "search_number", "expected"),
    [
        (
            "4.1А АВТО: РЕГИСТРАЦИОННЫЙ ЗНАК 4.1Б НОМЕР ПРИЦЕПА\nBA 5118 5\n4.2",
            "BA 5118 5",
            True
        ),
        (
            "4.1А АВТО: РЕГИСТРАЦИОННЫЙ ЗНАК\nBA51185\nРаздел 5",
            "ba51185",
            True
        ),
        (
            "Какой-то текст в разделе 2: BA51185. 4.1А АВТО: РЕГИСТРАЦИОННЫЙ ЗНАК\nXX1234X\n4.2",
            "BA51185",
            False
        ),
        (
            "4.1А АВТО: РЕГИСТРАЦИОННЫЙ ЗНАК\nC542OA67\n4.2",
            "C 542 OA 67",
            True
        ),
    ]
)
def test_find_vehicle_in_text(text, search_number, expected):
    assert find_vehicle_in_text(text, search_number) is expected
