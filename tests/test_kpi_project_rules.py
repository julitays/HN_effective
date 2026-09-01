import pandas as pd

from scripts.parsers.kpi_parser import (
    KPI_VALUE_COLUMNS,
    _build_merch_kpi_fact,
    _calculate_metric_execution,
    _calculate_project_kpi,
    _canonical_kpi_name,
    _map_client_region,
    _valid_kpi_assignment,
)


def test_project_kpi_uses_picos_as_full_result():
    source = pd.DataFrame(
        {
            "PICOS выполнение %": [0.82],
            "OSA выполнение %": [0.95],
            "TOP16 выполнение %": [0.70],
        }
    )

    assert _calculate_project_kpi(source).iloc[0] == 0.82


def test_project_kpi_uses_equal_osa_and_top16_weights():
    source = pd.DataFrame(
        {
            "PICOS выполнение %": [pd.NA],
            "OSA выполнение %": [1.00],
            "TOP16 выполнение %": [0.76],
        }
    )

    assert _calculate_project_kpi(source).iloc[0] == 0.88


def test_project_kpi_uses_osa_as_full_result_when_top16_is_not_assigned():
    source = pd.DataFrame(
        {
            "PICOS выполнение %": [pd.NA],
            "OSA выполнение %": [1.00],
            "TOP16 выполнение %": [pd.NA],
        }
    )

    assert _calculate_project_kpi(source).iloc[0] == 1.0


def test_source_kpi_names_are_normalized():
    assert _canonical_kpi_name("PICoS") == "PICOS"
    assert _canonical_kpi_name("OSA") == "OSA"
    assert _canonical_kpi_name("TOP_16") == "TOP16"


def test_technical_zero_scale_without_kpi_plan_is_not_an_assignment():
    valid = _valid_kpi_assignment(
        pd.Series(["PICOS", "TOP16", pd.NA], dtype="string"),
        pd.Series([0.95, pd.NA, pd.NA], dtype="Float64"),
    )

    assert valid.tolist() == [True, False, False]


def test_execution_is_calculated_from_plan_and_fact_without_client_scale():
    result = _calculate_metric_execution(
        pd.Series([pd.NA, 0.80, 0.80, 0.80], dtype="Float64"),
        pd.Series([0.60, 0.59, 0.60, 0.90], dtype="Float64"),
    )

    assert pd.isna(result.iloc[0])
    assert result.iloc[1] == 0.0
    assert result.iloc[2] == 0.75
    assert result.iloc[3] == 1.0


def test_client_region_prefers_source_and_falls_back_to_city():
    result = _map_client_region(
        pd.Series(["WEST", pd.NA]),
        pd.Series(["UNKNOWN", "МОСКВА"]),
        {"МОСКВА": "Москва"},
    )

    assert result.tolist() == ["Северо-Запад", "Москва"]


def test_merch_fact_keeps_tm_name_and_id_from_the_same_dominant_territory():
    month = pd.Timestamp("2026-06-01")
    tt_fact = pd.DataFrame(
        [
            {
                "MonthStart": month,
                "YearMonth": 202606,
                "ТТ": tt,
                "Сеть": "CHAIN",
                "Город": "CITY",
                "Код маршрута СВ": sv_code,
                "KPI проекта %": 0.95,
                "Регион BI": region,
                **{column: (0.95 if column == "PICOS выполнение %" else pd.NA) for column in KPI_VALUE_COLUMNS},
            }
            for tt, sv_code, region in [
                ("TT_DOMINANT", "SV_A", "Москва"),
                ("TT_SECONDARY", "SV_B", "Волга"),
            ]
        ]
    )
    visits = pd.DataFrame(
        [
            {
                "MonthStart": month,
                "YearMonth": 202606,
                "ТТ": "TT_DOMINANT",
                "ID сотрудника": "EMP_1",
                "Ключ визита RTM": f"A{visit}",
                "ФИО из логинов": "Employee One",
                "Логин": "login",
                "ID супервайзера": "SV_A_ID",
                "Супервайзер": "Supervisor A",
                "ID территориального менеджера": pd.NA,
                "Территориальный менеджер": "Erantsev Stanislav",
                "Регион BI": "Москва",
            }
            for visit in range(5)
        ]
        + [
            {
                "MonthStart": month,
                "YearMonth": 202606,
                "ТТ": "TT_SECONDARY",
                "ID сотрудника": "EMP_1",
                "Ключ визита RTM": f"B{visit}",
                "ФИО из логинов": "Employee One",
                "Логин": "login",
                "ID супервайзера": "SV_B_ID",
                "Супервайзер": "Supervisor B",
                "ID территориального менеджера": "TM_B_ID",
                "Территориальный менеджер": "Lebedev Alexander",
                "Регион BI": "Волга",
            }
            for visit in range(2)
        ]
    )

    result = _build_merch_kpi_fact(tt_fact, visits, pd.DataFrame())

    assert result.loc[0, "Территориальный менеджер"] == "Erantsev Stanislav"
    assert pd.isna(result.loc[0, "ID территориального менеджера"])
    assert result.loc[0, "Супервайзер"] == "Supervisor A"
    assert result.loc[0, "Регион BI"] == "Москва"
