import pandas as pd

from scripts.utils import (
    extract_sv_code,
    first_notna,
    last_notna,
    normalize_pct,
    normalize_person_name,
    normalize_valid_pct,
)


def test_normalize_pct_leaves_fractions_untouched():
    result = normalize_pct(pd.Series([0.5, 1.0, 1.5]))
    assert result.tolist() == [0.5, 1.0, 1.5]


def test_normalize_pct_converts_percent_scale():
    result = normalize_pct(pd.Series([50, 100, 150]))
    assert result.tolist() == [0.5, 1.0, 1.5]


def test_normalize_pct_coerces_invalid_to_nan():
    result = normalize_pct(pd.Series(["abc", None]))
    assert result.isna().all()


def test_normalize_valid_pct_drops_out_of_range():
    result = normalize_valid_pct(pd.Series([50, 250, -10]))
    assert result.iloc[0] == 0.5
    assert pd.isna(result.iloc[1])
    assert pd.isna(result.iloc[2])


def test_first_notna_returns_first_value():
    assert first_notna(pd.Series([None, "b", "c"])) == "b"


def test_first_notna_all_missing_returns_na():
    assert pd.isna(first_notna(pd.Series([None, None])))


def test_last_notna_returns_last_value():
    assert last_notna(pd.Series(["a", "b", None])) == "b"


def test_last_notna_treats_blank_string_as_missing():
    assert last_notna(pd.Series(["a", ""])) == "a"


def test_extract_sv_code_matches_trailing_digits():
    result = extract_sv_code(pd.Series(["СВ 123", "Иванов 4567", "нет кода"]))
    assert result.tolist()[0] == "СВ-123"
    assert result.tolist()[1] == "СВ-4567"
    assert pd.isna(result.tolist()[2])


def test_normalize_person_name_collapses_whitespace_and_yo():
    assert normalize_person_name("  Ёжиков   Иван  ") == "ежиков иван"


def test_normalize_person_name_none_passthrough():
    assert normalize_person_name(None) is None
    assert normalize_person_name("") is None
