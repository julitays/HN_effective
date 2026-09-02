import pandas as pd

from scripts.builders.build_page4_tt_data import (
    _attach_kpi_detail_metrics,
    _available_weighted_complexity,
    _kpi_gap_score,
    _monthly_kpi_gap,
)
from scripts.kpi_metric_utils import KPI_PUBLIC_COLUMNS


def test_three_kpi_gap_rules():
    assert _kpi_gap_score(0.95) == 0.0
    assert round(_kpi_gap_score(0.85), 4) == 0.5
    assert _kpi_gap_score(0.75) == 1.0
    assert round(_monthly_kpi_gap(pd.Series({"PICOS выполнение %": 0.85})), 4) == 0.5
    assert round(
        _monthly_kpi_gap(pd.Series({"OSA выполнение %": 0.95, "TOP16 выполнение %": 0.75})),
        4,
    ) == 0.5
    assert pd.isna(_monthly_kpi_gap(pd.Series({"OSA выполнение %": 0.95})))


def test_missing_okk_is_excluded_without_zero_substitution():
    score, available_weight = _available_weighted_complexity(
        [
            (0.40, 0.5),
            (0.30, 0.5),
            (0.10, pd.NA),
            (0.10, 0.0),
            (0.10, 0.0),
        ]
    )

    assert available_weight == 0.90
    assert round(score, 4) == 0.3889


def test_attaches_kpi_metrics_and_adds_kpi_only_tt():
    snapshot = pd.DataFrame(
        {
            "MonthStart": pd.to_datetime(["2026-07-01"]),
            "YearMonth": [202607],
            "ТТ": ["T1"],
            "KPI проекта %": [0.8],
            "Регион BI": ["Москва"],
            "Город": ["Москва"],
            "Сеть": [""],
            "Ранг": [1],
            "Визиты": [1],
        }
    )
    metrics = pd.DataFrame(
        {
            "MonthStart": pd.to_datetime(["2026-07-01"] * 2),
            "YearMonth": [202607] * 2,
            "ТТ": ["T1", "T2"],
            "KPI проекта %": [0.9, 0.7],
            "PICOS план": [850.0, pd.NA],
            "PICOS факт": [900.0, pd.NA],
            "PICOS выполнение %": [0.90, pd.NA],
            "OSA план %": [pd.NA, 0.85],
            "OSA факт %": [pd.NA, 0.60],
            "OSA выполнение %": [pd.NA, 0.60],
            "TOP16 план %": [pd.NA, 0.12],
            "TOP16 факт %": [pd.NA, 0.15],
            "TOP16 выполнение %": [pd.NA, 0.80],
            "Регион BI": ["Москва", "Юг"],
            "Город": ["Москва", "Сочи"],
            "Сеть": ["A", "B"],
        }
    )

    result = _attach_kpi_detail_metrics(snapshot, metrics)
    t1 = result[result["ТТ"].eq("T1")].iloc[0]

    assert t1["PICOS выполнение %"] == 0.9
    assert t1["Сеть"] == "A"
    assert "PICOS дельта %" not in result.columns
    assert "ID мерчендайзера" not in result.columns
    assert "Сложность KPI повтор %" not in result.columns
    assert not result["ТТ"].eq("T2").any()
    assert result["ТМ территория"].isna().all()
    assert "Метод привязки ТМ" not in result.columns
    assert "Статус привязки ТМ" not in result.columns


def test_keeps_okk_tt_when_current_month_has_no_rtm_or_kpi():
    snapshot = pd.DataFrame(
        {
            "MonthStart": pd.to_datetime(["2026-08-01"]),
            "YearMonth": [202608],
            "ТТ": ["T-AUG"],
            "KPI проекта %": [pd.NA],
            "ОКК %": [0.72],
            "Регион BI": ["Москва"],
            "Город": ["Москва"],
            "Сеть": ["A"],
            "Ранг": [pd.NA],
            "Визиты": [0],
            "Статус ТТ": ["Недостаточно данных"],
        }
    )
    metrics = pd.DataFrame(
        columns=list(
            dict.fromkeys(
                [
                    "MonthStart",
                    "YearMonth",
                    "ТТ",
                    *KPI_PUBLIC_COLUMNS,
                    "Регион BI",
                    "Город",
                    "Сеть",
                ]
            )
        )
    )

    result = _attach_kpi_detail_metrics(snapshot, metrics)

    assert result["ТТ"].tolist() == ["T-AUG"]
    assert pd.isna(result.loc[0, "KPI проекта %"])
    assert result.loc[0, "ОКК %"] == 0.72


def test_restores_kpi_only_tt_visits_and_org_assignment_from_rtm():
    snapshot = pd.DataFrame(
        columns=[
            "MonthStart",
            "YearMonth",
            "ТТ",
            "KPI проекта %",
            "Регион BI",
            "Город",
            "Сеть",
            "Ранг",
            "Визиты",
        ]
    )
    metrics = pd.DataFrame(
        {
            "MonthStart": pd.to_datetime(["2026-07-01"]),
            "YearMonth": [202607],
            "ТТ": ["T2"],
            "KPI проекта %": [0.9],
            "PICOS план": [850.0],
            "PICOS факт": [900.0],
            "PICOS выполнение %": [0.9],
            "OSA план %": [pd.NA],
            "OSA факт %": [pd.NA],
            "OSA выполнение %": [pd.NA],
            "TOP16 план %": [pd.NA],
            "TOP16 факт %": [pd.NA],
            "TOP16 выполнение %": [pd.NA],
            "Регион BI": ["Юг"],
            "Город": ["Сочи"],
            "Сеть": ["B"],
        }
    )
    visits = pd.DataFrame(
        {
            "MonthStart": pd.to_datetime(["2026-07-01", "2026-07-01"]),
            "YearMonth": [202607, 202607],
            "ТТ": ["T2", "T2"],
            "Ключ визита RTM": ["V1", "V2"],
            "ID сотрудника": ["E1", "E1"],
            "ФИО из логинов": ["Employee", "Employee"],
            "ID супервайзера": ["SV1", "SV1"],
            "Супервайзер": ["Supervisor", "Supervisor"],
            "ID территориального менеджера": ["TM1", "TM1"],
            "Территориальный менеджер": ["Territory manager", "Territory manager"],
            "Регион BI": ["Юг", "Юг"],
        }
    )
    teams = pd.DataFrame()

    result = _attach_kpi_detail_metrics(snapshot, metrics, visits, teams)
    row = result.iloc[0]

    assert row["Визиты"] == 2
    assert row["Ответственный СВ ТТ"] == "Supervisor"
    assert row["ТМ территория"] == "Territory manager"
    assert row["PICOS выполнение %"] == 0.9
    assert row["Сеть"] == "B"


def test_assigns_single_tm_when_rtm_visits_have_multiple_supervisors():
    snapshot = pd.DataFrame(
        columns=[
            "MonthStart",
            "YearMonth",
            "ТТ",
            "KPI проекта %",
            "Ранг",
            "Визиты",
        ]
    )
    metrics = pd.DataFrame(
        {
            "MonthStart": pd.to_datetime(["2026-07-01"]),
            "YearMonth": [202607],
            "ТТ": ["T4"],
            "KPI проекта %": [0.9],
            "PICOS план": [850.0],
            "PICOS факт": [900.0],
            "PICOS выполнение %": [0.9],
            "OSA план %": [pd.NA],
            "OSA факт %": [pd.NA],
            "OSA выполнение %": [pd.NA],
            "TOP16 план %": [pd.NA],
            "TOP16 факт %": [pd.NA],
            "TOP16 выполнение %": [pd.NA],
        }
    )
    visits = pd.DataFrame(
        {
            "MonthStart": pd.to_datetime(["2026-07-01"] * 4),
            "YearMonth": [202607] * 4,
            "ТТ": ["T4"] * 4,
            "Ключ визита RTM": ["V1", "V2", "V3", "V4"],
            "ID сотрудника": ["E1", "E1", "E2", "E2"],
            "ФИО из логинов": ["Employee 1", "Employee 1", "Employee 2", "Employee 2"],
            "ID супервайзера": ["SV1", "SV1", "SV2", "SV2"],
            "Супервайзер": ["Supervisor 1", "Supervisor 1", "Supervisor 2", "Supervisor 2"],
            "ID территориального менеджера": ["TM1", "TM1", "TM1", "TM1"],
            "Территориальный менеджер": ["Territory manager"] * 4,
            "Регион BI": ["Юг"] * 4,
        }
    )
    teams = pd.DataFrame()

    row = _attach_kpi_detail_metrics(snapshot, metrics, visits, teams).iloc[0]

    assert row["ТМ территория"] == "Territory manager"
    assert "Территориальный менеджер" not in row.index
