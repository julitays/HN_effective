import pandas as pd
import pytest

from scripts.kpi_metric_utils import (
    aggregate_employee_kpi_to_org,
    pivot_employee_kpi_metrics,
    pivot_tt_kpi_metrics,
)


def _metrics():
    return pd.DataFrame(
        {
            "MonthStart": pd.to_datetime(["2026-07-01"] * 2),
            "YearMonth": [202607] * 2,
            "ID сотрудника": ["E1", "E2"],
            "ТТ": ["T1", "T2"],
            "KPI проекта %": [0.8, 0.6],
            "PICOS план": [850.0, 850.0],
            "PICOS факт": [800.0, 600.0],
            "PICOS выполнение %": [0.80, 0.60],
            "OSA план %": [pd.NA, 0.85],
            "OSA факт %": [pd.NA, 0.70],
            "OSA выполнение %": [pd.NA, 0.70],
            "TOP16 план %": [0.12, pd.NA],
            "TOP16 факт %": [0.15, pd.NA],
            "TOP16 выполнение %": [0.75, pd.NA],
            "PICOS вес в KPI %": [1.0, 0.5],
            "OSA вес в KPI %": [0.0, 0.5],
            "TOP16 вес в KPI %": [0.0, 0.0],
            "Визитов": [3, 1],
        }
    )


def test_pivots_employee_and_tt_detail_metrics():
    employee = pivot_employee_kpi_metrics(_metrics())
    tt = pivot_tt_kpi_metrics(_metrics())

    assert set(["PICOS выполнение %", "OSA выполнение %", "TOP16 выполнение %"]).issubset(employee.columns)
    assert employee.loc[employee["ID сотрудника"] == "E1", "TOP16 факт %"].iloc[0] == 0.15
    assert tt.loc[tt["ТТ"] == "T2", "OSA выполнение %"].iloc[0] == 0.7


def test_aggregates_employee_metrics_to_org_using_visit_weights():
    assignment = pd.DataFrame(
        {
            "MonthStart": pd.to_datetime(["2026-07-01", "2026-07-01"]),
            "YearMonth": [202607, 202607],
            "ID мерчендайзера": ["E1", "E2"],
            "ID супервайзера": ["SV1", "SV1"],
        }
    )
    result = aggregate_employee_kpi_to_org(_metrics(), assignment, "ID супервайзера")

    assert result["ID супервайзера"].tolist() == ["SV1"]
    assert result["KPI проекта %"].iloc[0] == pytest.approx(0.75)
    assert result["PICOS выполнение %"].iloc[0] == pytest.approx(0.75)
    assert result["PICOS вес в KPI %"].iloc[0] == pytest.approx(0.875)
    assert result["OSA вес в KPI %"].iloc[0] == pytest.approx(0.125)
