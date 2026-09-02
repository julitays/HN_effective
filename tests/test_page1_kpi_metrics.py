import pandas as pd
import pytest

from scripts.builders.build_page1_monthly_snapshot import _build_kpi_monthly


def test_page1_kpi_monthly_keeps_all_three_kpi_components():
    direct = pd.DataFrame(
        {
            "MonthStart": pd.to_datetime(["2026-07-01", "2026-07-01"]),
            "YearMonth": [202607, 202607],
            "Регион BI": ["Москва", "Москва"],
            "KPI проекта %": [0.80, 1.00],
            "PICOS план": [900.0, 900.0],
            "PICOS факт": [800.0, 1000.0],
            "PICOS выполнение %": [0.80, 1.00],
            "OSA план %": [0.85, pd.NA],
            "OSA факт %": [0.75, pd.NA],
            "OSA выполнение %": [0.75, pd.NA],
            "TOP16 план %": [0.15, pd.NA],
            "TOP16 факт %": [0.12, pd.NA],
            "TOP16 выполнение %": [0.80, pd.NA],
        }
    )

    result = _build_kpi_monthly(pd.DataFrame(), kpi_tt_direct=direct).iloc[0]

    assert result["KPI проекта %"] == pytest.approx(0.90)
    assert result["PICOS выполнение %"] == pytest.approx(0.90)
    assert result["OSA выполнение %"] == pytest.approx(0.75)
    assert result["TOP16 выполнение %"] == pytest.approx(0.80)
    assert result["TOP16 план %"] == pytest.approx(0.15)
    assert result["ТТ с валидным KPI"] == 2


def test_page1_kpi_count_excludes_tt_without_valid_client_kpi():
    direct = pd.DataFrame(
        {
            "MonthStart": pd.to_datetime(["2026-07-01", "2026-07-01"]),
            "YearMonth": [202607, 202607],
            "Регион BI": ["Москва", "Москва"],
            "KPI проекта %": [0.80, pd.NA],
        }
    )

    result = _build_kpi_monthly(pd.DataFrame(), kpi_tt_direct=direct).iloc[0]

    assert result["KPI проекта %"] == pytest.approx(0.80)
    assert result["ТТ с валидным KPI"] == 1
