import pandas as pd
import pytest

from scripts.builders.build_page3_data import (
    _first_confirmed_visits_for_current_episode,
    _merch_personal_metric_label,
    _merch_personal_reason_from_row,
    _merch_personal_score_from_row,
    _merch_personal_status_from_row,
)


def test_first_visit_uses_only_completed_confirmed_visit_from_current_episode():
    dim = pd.DataFrame(
        {
            "ID сотрудника": ["EMP_1"],
            "Дата приёма": [pd.Timestamp("2026-06-15")],
        }
    )
    visits = pd.DataFrame(
        {
            "ID сотрудника": ["EMP_1", "EMP_1", "EMP_1", "EMP_1"],
            "Дата визита": [
                pd.Timestamp("2026-05-10"),
                pd.Timestamp("2026-06-16"),
                pd.Timestamp("2026-06-17"),
                pd.Timestamp("2026-06-18"),
            ],
            "Визит выполнен": [True, True, False, True],
            "Визит подтверждён": [True, True, True, False],
        }
    )

    result = _first_confirmed_visits_for_current_episode(visits, dim)

    assert result.loc[0, "Дата первого подтверждённого визита"] == pd.Timestamp("2026-06-16")


def test_first_visit_is_empty_when_employee_has_only_pretraining_and_no_rtm_visit():
    dim = pd.DataFrame(
        {
            "ID сотрудника": ["EMP_1"],
            "Дата приёма": [pd.Timestamp("2026-08-03")],
        }
    )
    visits = pd.DataFrame(
        columns=["ID сотрудника", "Дата визита", "Визит выполнен", "Визит подтверждён"]
    )

    result = _first_confirmed_visits_for_current_episode(visits, dim)

    assert result.empty


def _row(**overrides):
    values = {
        "KPI проекта %": 0.99,
        "ОКК %": 0.60,
        "Обучение %": 0.95,
        "Аттестация клиента %": 0.95,
        "PICOS выполнение %": 0.99,
        "OSA выполнение %": pd.NA,
        "TOP16 выполнение %": pd.NA,
        "PICOS вес в KPI %": 1.0,
        "OSA вес в KPI %": 0.0,
        "TOP16 вес в KPI %": 0.0,
    }
    values.update(overrides)
    return pd.Series(values)


def test_merch_score_keeps_agreed_weights_with_full_client_kpi():
    assert _merch_personal_score_from_row(_row()) == pytest.approx(1.0)


def test_merch_kpi_below_red_threshold_zeroes_only_kpi_block():
    result = _merch_personal_score_from_row(
        _row(**{"KPI проекта %": 0.94, "PICOS выполнение %": 0.94})
    )

    assert result == pytest.approx(0.60)


def test_merch_score_uses_osa_and_top16_as_separate_components():
    row = _row(
        **{
            "KPI проекта %": 0.88,
            "PICOS выполнение %": pd.NA,
            "OSA выполнение %": 1.0,
            "TOP16 выполнение %": 0.89,
            "PICOS вес в KPI %": 0.0,
            "OSA вес в KPI %": 0.5,
            "TOP16 вес в KPI %": 0.5,
        }
    )

    assert _merch_personal_score_from_row(row) == pytest.approx(0.80)


def test_merch_score_uses_available_client_kpi_component():
    row = _row(
        **{
            "KPI проекта %": pd.NA,
            "PICOS выполнение %": pd.NA,
            "OSA выполнение %": 1.0,
            "TOP16 выполнение %": pd.NA,
            "PICOS вес в KPI %": 0.0,
            "OSA вес в KPI %": 0.5,
            "TOP16 вес в KPI %": 0.0,
        }
    )

    assert _merch_personal_score_from_row(row) == pytest.approx(1.0)


def test_merch_score_does_not_round_yellow_component_to_one_hundred():
    row = _row(
        **{
            "PICOS выполнение %": 0.96,
            "TOP16 выполнение %": 0.949,
            "PICOS вес в KPI %": 0.99,
            "TOP16 вес в KPI %": 0.01,
        }
    )

    assert _merch_personal_score_from_row(row) < 1.0


def test_merch_personal_reason_ignores_yellow_metrics():
    row = _row(
        **{
            "PICOS выполнение %": 0.97,
            "ОКК %": 0.50,
            "Обучение %": 0.92,
            "Аттестация клиента %": 0.92,
        }
    )

    assert pd.isna(_merch_personal_reason_from_row(row))


def test_merch_personal_reason_lists_only_red_metrics():
    row = _row(
        **{
            "PICOS выполнение %": 0.94,
            "ОКК %": 0.39,
            "Обучение %": 0.92,
            "Аттестация клиента %": 0.94,
        }
    )

    result = _merch_personal_reason_from_row(row)
    assert "PICOS" in result
    assert "ОКК" in result
    assert "Обучение" not in result
    assert "Аттестация" not in result


def test_merch_red_metric_cannot_have_role_compliance_status():
    row = _row(
        **{
            "ОКК %": 0.39,
            "Личная эффективность МЕ %": 0.85,
            "Доступность личных метрик %": 1.0,
        }
    )

    assert _merch_personal_status_from_row(row) == "Зона развития"


def test_merch_yellow_metric_can_keep_role_compliance_status():
    row = _row(
        **{
            "ОКК %": 0.50,
            "Личная эффективность МЕ %": 0.92,
            "Доступность личных метрик %": 1.0,
        }
    )

    assert _merch_personal_status_from_row(row) == "Соответствует роли"


def test_merch_low_score_without_red_flags_is_insufficient_data():
    row = _row(
        **{
            "Обучение %": pd.NA,
            "Личная эффективность МЕ %": 0.75,
            "Доступность личных метрик %": 0.80,
        }
    )

    assert _merch_personal_status_from_row(row) == "Недостаточно данных"
    assert pd.isna(_merch_personal_reason_from_row(row))


def test_merch_with_less_than_three_months_tenure_is_newcomer():
    row = _row(
        **{
            "Стаж МЕ, мес.": 2.9,
            "ОКК %": 0.20,
            "Личная эффективность МЕ %": 0.40,
            "Доступность личных метрик %": 1.0,
        }
    )

    assert _merch_personal_status_from_row(row) == "Новичок"


def test_merch_with_three_months_tenure_uses_regular_status_rules():
    row = _row(
        **{
            "Стаж МЕ, мес.": 3.0,
            "Личная эффективность МЕ %": 1.0,
            "Доступность личных метрик %": 1.0,
        }
    )

    assert _merch_personal_status_from_row(row) == "Высокая личная готовность"
