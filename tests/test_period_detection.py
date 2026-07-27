from datetime import date
from pathlib import Path

import pandas as pd

from scripts.parsers.kpi_parser import _detect_period as kpi_detect_period
from scripts.parsers.okk_parser import detect_period as okk_detect_period
from scripts.parsers.oed_parser import detect_period as oed_detect_period
from scripts.parsers.enps_parser import _detect_period_from_date, _normalize_period
from scripts.parsers.attestations_parser import _extract_period


def test_kpi_detect_period_valid():
    period, year, month = kpi_detect_period("SUPERVISOR Май 05.2026.xlsx")
    assert (period, year, month) == ("2026_05", 2026, 5)


def test_kpi_detect_period_no_match_falls_back_to_unknown():
    period, year, month = kpi_detect_period("файл без даты.xlsx")
    assert period == "Unknown"
    assert month == 0


def test_okk_detect_period_valid_year_and_month():
    path = Path("data/raw/okk/2025/Сводная H&N МАЙ 02.06.xlsx")
    period, year, month = okk_detect_period(path)
    assert (period, year, month) == ("2025_05", 2025, 5)


def test_okk_detect_period_bad_year_folder_falls_back_loudly(capsys):
    path = Path("data/raw/okk/not_a_year/Сводная H&N МАЙ.xlsx")
    _, year, month = okk_detect_period(path)
    assert year == date.today().year
    assert month == 5
    assert "не удалось определить год" in capsys.readouterr().out


def test_oed_detect_period_valid():
    period, year, quarter = oed_detect_period("Q1_2026.xlsx")
    assert (period, year, quarter) == ("Q1_2026", 2026, 1)


def test_oed_detect_period_no_match_returns_zeros():
    _, year, quarter = oed_detect_period("random.xlsx")
    assert (year, quarter) == (0, 0)


def test_enps_detect_period_from_date():
    assert _detect_period_from_date("2026-02-18") == "2026_Q1"


def test_enps_detect_period_from_invalid_date():
    assert _detect_period_from_date("not a date") == "Unknown"


def test_enps_normalize_period_passthrough():
    assert _normalize_period("2026_Q1") == "2026_Q1"


def test_enps_normalize_period_archive_format():
    assert _normalize_period("Q3_21") == "2021_Q3"


def test_attestations_extract_period_from_value():
    _, year_quarter, label = _extract_period("Q2 2025", "irrelevant.xlsx")
    assert year_quarter == 20252
    assert label == "Q2 2025"


def test_attestations_extract_period_no_match_returns_na():
    _, year_quarter, _label = _extract_period(None, "no period here")
    assert pd.isna(year_quarter)
