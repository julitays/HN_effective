import pandas as pd
import pytest

from scripts.builders.build_page3_data import (
    _merch_personal_available_weight_from_row,
    _merch_personal_reason_from_row,
    _merch_personal_score_from_row,
    _merch_personal_status_from_row,
    _merch_personal_weak_metric_records,
)
from scripts.builders.build_page5_sv_oed_data import (
    _available_weight_from_row as _sv_available_weight,
    _personal_available_weight_from_row as _sv_personal_available_weight,
    _personal_reason_from_row as _sv_personal_reason,
    _personal_score_from_row as _sv_personal_score,
    _personal_status_from_row as _sv_personal_status,
    _status_from_row as _sv_status,
    _status_reason_from_row as _sv_reason,
    _sv_signal_records,
    _weighted_score_from_row as _sv_score,
)
from scripts.builders.build_page7_tm_data import (
    _tm_kpi_target_from_row,
    _tm_operational_result_from_row,
    _status_from_row as _tm_status,
    _status_reason_from_row as _tm_reason,
    _tm_available_weight_from_row as _tm_available_weight,
    _tm_effectiveness_score_from_row as _tm_score,
    _tm_metric_signal_records,
)


def test_merch_missing_okk_costs_points_but_does_not_block_status():
    row = pd.Series(
        {
            "PICOS выполнение %": 0.99,
            "OSA выполнение %": pd.NA,
            "TOP16 выполнение %": pd.NA,
            "PICOS вес в KPI %": 1.0,
            "OSA вес в KPI %": 0.0,
            "TOP16 вес в KPI %": 0.0,
            "ОКК %": pd.NA,
            "Обучение %": 0.95,
            "Аттестация клиента %": 0.95,
        }
    )
    row["Доступность личных метрик %"] = _merch_personal_available_weight_from_row(row)
    row["Личная эффективность МЕ %"] = _merch_personal_score_from_row(row)
    row["Статус личной эффективности"] = _merch_personal_status_from_row(row)

    assert row["Личная эффективность МЕ %"] == pytest.approx(0.85)
    assert row["Статус личной эффективности"] != "Недостаточно данных"
    assert pd.isna(_merch_personal_reason_from_row(row))


def test_merch_partial_kpi_components_do_not_block_status():
    row = pd.Series(
        {
            "PICOS выполнение %": pd.NA,
            "OSA выполнение %": 0.99,
            "TOP16 выполнение %": pd.NA,
            "PICOS вес в KPI %": 0.0,
            "OSA вес в KPI %": 0.5,
            "TOP16 вес в KPI %": 0.0,
            "ОКК %": 0.60,
            "Обучение %": 0.95,
            "Аттестация клиента %": 0.95,
        }
    )
    row["Доступность личных метрик %"] = _merch_personal_available_weight_from_row(row)
    row["Личная эффективность МЕ %"] = _merch_personal_score_from_row(row)
    row["Статус личной эффективности"] = _merch_personal_status_from_row(row)

    assert row["Личная эффективность МЕ %"] == pytest.approx(1.0)
    assert row["Статус личной эффективности"] == "Высокая личная готовность"


def test_merch_missing_okk_is_not_added_to_red_reason():
    row = pd.Series(
        {
            "PICOS выполнение %": 0.96,
            "OSA выполнение %": pd.NA,
            "TOP16 выполнение %": pd.NA,
            "PICOS вес в KPI %": 1.0,
            "OSA вес в KPI %": 0.0,
            "TOP16 вес в KPI %": 0.0,
            "ОКК %": pd.NA,
            "Обучение %": 0.89,
            "Аттестация клиента %": 0.89,
            "Аттестация клиента Q1 2026 %": 0.89,
            "YearMonth": 202606,
        }
    )
    row["Доступность личных метрик %"] = _merch_personal_available_weight_from_row(row)
    row["Личная эффективность МЕ %"] = _merch_personal_score_from_row(row)
    row["Статус личной эффективности"] = _merch_personal_status_from_row(row)

    reason = _merch_personal_reason_from_row(row)
    assert "Обучение" in reason
    assert "Аттестация" in reason
    assert "нет проверок ОКК" not in reason


def test_merch_red_signal_with_low_score_goes_to_development_zone():
    row = pd.Series(
        {
            "PICOS выполнение %": 0.99,
            "OSA выполнение %": pd.NA,
            "TOP16 выполнение %": pd.NA,
            "PICOS вес в KPI %": 1.0,
            "OSA вес в KPI %": 0.0,
            "TOP16 вес в KPI %": 0.0,
            "ОКК %": 0.60,
            "Обучение %": 0.95,
            "Аттестация клиента %": 0.89,
        }
    )
    row["Доступность личных метрик %"] = _merch_personal_available_weight_from_row(row)
    row["Личная эффективность МЕ %"] = _merch_personal_score_from_row(row)

    assert row["Личная эффективность МЕ %"] == pytest.approx(0.75)
    assert _merch_personal_status_from_row(row) == "Зона развития"


def test_merch_red_signal_with_score_above_role_threshold_goes_to_development_zone():
    row = pd.Series(
        {
            "PICOS выполнение %": 0.99,
            "OSA выполнение %": pd.NA,
            "TOP16 выполнение %": pd.NA,
            "PICOS вес в KPI %": 1.0,
            "OSA вес в KPI %": 0.0,
            "TOP16 вес в KPI %": 0.0,
            "ОКК %": 0.39,
            "Обучение %": 0.95,
            "Аттестация клиента %": pd.NA,
        }
    )
    row["Доступность личных метрик %"] = _merch_personal_available_weight_from_row(row)
    row["Личная эффективность МЕ %"] = _merch_personal_score_from_row(row)

    assert row["Личная эффективность МЕ %"] == pytest.approx(0.85)
    assert _merch_personal_status_from_row(row) == "Зона развития"


def test_sv_missing_okk_costs_points_but_does_not_block_status():
    row = pd.Series(
        {
            "KPI месяца %": 0.99,
            "PICOS выполнение %": 0.99,
            "ОКК команды %": pd.NA,
            "Обучение команды %": 0.95,
            "Фрод %": 0.10,
            "Стабильность команды %": 0.95,
            "Текучесть команды %": 0.10,
        }
    )
    row["Доступность метрик СВ %"] = _sv_available_weight(row)
    row["Индекс эффективности СВ %"] = _sv_score(row)
    row["Статус эффективности СВ"] = _sv_status(row)

    assert row["Индекс эффективности СВ %"] == pytest.approx(0.85)
    assert row["Статус эффективности СВ"] != "Недостаточно данных"
    assert pd.isna(_sv_reason(row))


def test_sv_red_signal_with_high_score_goes_to_risk_zone():
    row = pd.Series(
        {
            "KPI месяца %": 0.99,
            "PICOS выполнение %": 0.99,
            "ОКК команды %": 0.39,
            "Обучение команды %": 0.95,
            "Фрод %": 0.10,
            "Стабильность команды %": 0.95,
            "Текучесть команды %": 0.10,
        }
    )
    row["Доступность метрик СВ %"] = _sv_available_weight(row)
    row["Индекс эффективности СВ %"] = _sv_score(row)
    row["Статус эффективности СВ"] = _sv_status(row)

    assert row["Индекс эффективности СВ %"] == pytest.approx(0.85)
    assert row["Статус эффективности СВ"] == "Зона развития"
    assert _sv_reason(row) == "ОКК команды"


def test_sv_yellow_signal_stays_control_without_red_reason():
    row = pd.Series(
        {
            "KPI месяца %": 0.97,
            "PICOS выполнение %": 0.97,
            "ОКК команды %": 0.60,
            "Обучение команды %": 0.95,
            "Фрод %": 0.10,
            "Стабильность команды %": 0.95,
            "Текучесть команды %": 0.10,
        }
    )
    row["Доступность метрик СВ %"] = _sv_available_weight(row)
    row["Индекс эффективности СВ %"] = _sv_score(row)
    row["Статус эффективности СВ"] = _sv_status(row)

    assert row["Статус эффективности СВ"] == "Соответствует роли"
    assert pd.isna(_sv_reason(row))


def test_sv_personal_red_signal_cannot_keep_role_status():
    row = pd.Series(
        {
            "Есть ОЭД СВ": True,
            "Класс ОЭД": "ТОП",
            "Аттестация клиента %": 0.89,
            "Аттестация ОЭД %": 0.95,
            "Продукт ОЭД %": 0.95,
            "Управление ОЭД %": 0.95,
        }
    )
    row["Доступность личных метрик %"] = _sv_personal_available_weight(row)
    row["Личная эффективность СВ %"] = _sv_personal_score(row)
    row["Статус личной эффективности"] = _sv_personal_status(row)

    assert row["Статус личной эффективности"] == "Зона развития"
    assert _sv_personal_reason(row) == "Аттестация клиента"


def test_sv_personal_yellow_signal_has_no_red_reason():
    row = pd.Series(
        {
            "Есть ОЭД СВ": True,
            "Класс ОЭД": "МАСТЕР",
            "Аттестация клиента %": 0.92,
            "Аттестация ОЭД %": 0.95,
            "Продукт ОЭД %": 0.95,
            "Управление ОЭД %": 0.95,
        }
    )
    row["Доступность личных метрик %"] = _sv_personal_available_weight(row)
    row["Личная эффективность СВ %"] = _sv_personal_score(row)
    row["Статус личной эффективности"] = _sv_personal_status(row)

    assert row["Статус личной эффективности"] == "Соответствует роли"
    assert pd.isna(_sv_personal_reason(row))


def test_tm_missing_okk_costs_points_but_does_not_block_status():
    row = pd.Series(
        {
            "KPI месяца территории %": 0.99,
            "Целевой порог KPI территории %": 0.98,
            "PICOS выполнение %": 0.99,
            "Качество команды %": pd.NA,
            "Обучение команды %": 0.95,
            "Фрод %": 0.10,
            "Стабильность команды %": 0.95,
            "Текучесть %": 0.10,
        }
    )
    row["Доступность индекса ТМ %"] = _tm_available_weight(row)
    row["Балл эффективности %"] = _tm_score(row)
    row["Статус ТМ"] = _tm_status(row)

    assert row["Балл эффективности %"] == pytest.approx(0.80)
    assert row["Статус ТМ"] != "Недостаточно данных"
    assert _tm_reason(row) == "(нет проверок ОКК)"


def test_tm_all_green_metrics_have_high_effectiveness_status():
    row = pd.Series(
        {
            "KPI месяца территории %": 0.98,
            "Целевой порог KPI территории %": 0.98,
            "PICOS выполнение %": 0.98,
            "Качество команды %": 0.60,
            "Обучение команды %": 0.95,
            "Фрод %": 0.15,
            "Стабильность команды %": 0.95,
            "Текучесть %": 0.10,
        }
    )
    row["Доступность индекса ТМ %"] = _tm_available_weight(row)
    row["Балл эффективности %"] = _tm_score(row)

    assert _tm_status(row) == "Высокая эффективность"


def test_tm_yellow_metric_without_red_flags_has_high_effectiveness_status():
    row = pd.Series(
        {
            "KPI месяца территории %": 0.99,
            "Целевой порог KPI территории %": 0.98,
            "PICOS выполнение %": 0.99,
            "Качество команды %": 0.61,
            "Обучение команды %": 0.85,
            "Фрод %": 0.06,
            "Стабильность команды %": 0.98,
            "Текучесть %": 0.08,
        }
    )
    row["Доступность индекса ТМ %"] = _tm_available_weight(row)
    row["Балл эффективности %"] = _tm_score(row)

    assert _tm_status(row) == "Высокая эффективность"


def test_tm_red_metric_with_score_at_least_eighty_has_development_status():
    row = pd.Series(
        {
            "KPI месяца территории %": 0.99,
            "Целевой порог KPI территории %": 0.98,
            "PICOS выполнение %": 0.99,
            "Качество команды %": 0.61,
            "Обучение команды %": 0.79,
            "Фрод %": 0.06,
            "Стабильность команды %": 0.98,
            "Текучесть %": 0.08,
        }
    )
    row["Доступность индекса ТМ %"] = _tm_available_weight(row)
    row["Балл эффективности %"] = _tm_score(row)

    assert row["Балл эффективности %"] >= 0.80
    assert _tm_status(row) == "Зона развития"


@pytest.mark.parametrize(
    ("value", "expected_level"),
    [(0.799, "hard"), (0.80, "soft"), (0.899, "soft"), (0.90, None)],
)
def test_tm_learning_signal_uses_agreed_boundaries(value, expected_level):
    row = pd.Series({"Обучение команды %": value})
    levels = [record["level"] for record in _tm_metric_signal_records(row)]

    assert (levels[0] if levels else None) == expected_level


@pytest.mark.parametrize(
    ("value", "expected_level"),
    [(0.949, "hard"), (0.95, "soft"), (0.979, "soft"), (0.98, None)],
)
def test_sv_picos_signal_uses_agreed_boundaries(value, expected_level):
    sv = pd.Series({"KPI месяца %": value, "PICOS выполнение %": value})
    sv_levels = [record["level"] for record in _sv_signal_records(sv)]

    assert (sv_levels[0] if sv_levels else None) == expected_level


@pytest.mark.parametrize(
    ("component", "value", "expected_level"),
    [
        ("OSA", 0.949, "hard"),
        ("OSA", 0.95, None),
        ("TOP16", 0.949, "hard"),
        ("TOP16", 0.95, None),
    ],
)
def test_sv_osa_and_top16_are_green_from_ninety_five_percent(component, value, expected_level):
    row = pd.Series({f"{component} выполнение %": value})
    levels = [record["level"] for record in _sv_signal_records(row)]

    assert (levels[0] if levels else None) == expected_level


@pytest.mark.parametrize(
    ("value", "expected_level"),
    [(0.949, "hard"), (0.95, "soft"), (0.979, "soft"), (0.98, None)],
)
def test_tm_picos_signal_uses_agreed_boundaries(value, expected_level):
    tm = pd.Series({"PICOS выполнение %": value})
    tm_levels = [record["level"] for record in _tm_metric_signal_records(tm)]

    assert (tm_levels[0] if tm_levels else None) == expected_level


@pytest.mark.parametrize(
    ("component", "value", "expected_level"),
    [
        ("OSA", 0.949, "hard"),
        ("OSA", 0.95, None),
        ("TOP16", 0.949, "hard"),
        ("TOP16", 0.95, None),
    ],
)
def test_tm_osa_and_top16_are_green_from_ninety_five_percent(component, value, expected_level):
    row = pd.Series({f"{component} выполнение %": value})
    levels = [record["level"] for record in _tm_metric_signal_records(row)]

    assert (levels[0] if levels else None) == expected_level


def test_tm_kpi_target_respects_territory_component_mix():
    row = pd.Series(
        {
            "PICOS выполнение %": 0.98,
            "OSA выполнение %": 0.95,
            "TOP16 выполнение %": 0.95,
            "PICOS вес в KPI %": 0.50,
            "OSA вес в KPI %": 0.25,
            "TOP16 вес в KPI %": 0.25,
        }
    )

    assert _tm_kpi_target_from_row(row) == pytest.approx(0.965)


def test_tm_operational_result_is_one_hundred_when_all_blocks_reach_target():
    row = pd.Series(
        {
            "KPI месяца территории %": 0.98,
            "Целевой порог KPI территории %": 0.98,
            "Качество команды %": 0.60,
            "Обучение команды %": 0.95,
            "Фрод %": 0.15,
        }
    )

    assert _tm_operational_result_from_row(row) == pytest.approx(1.0)


def test_sv_reason_names_red_client_kpi_component():
    row = pd.Series(
        {
            "KPI месяца %": 0.97,
            "PICOS выполнение %": 0.94,
            "OSA выполнение %": 0.97,
            "TOP16 выполнение %": 0.99,
            "ОКК команды %": 0.60,
            "Обучение команды %": 0.95,
            "Фрод %": 0.10,
            "Стабильность команды %": 0.95,
            "Текучесть команды %": 0.10,
        }
    )
    row["Доступность метрик СВ %"] = _sv_available_weight(row)
    row["Индекс эффективности СВ %"] = _sv_score(row)

    assert _sv_status(row) == "Зона развития"
    assert _sv_reason(row) == "PICOS"


@pytest.mark.parametrize(
    ("value", "expected_severity"),
    [(0.949, "red"), (0.95, "yellow"), (0.989, "yellow"), (0.99, None)],
)
def test_merch_kpi_signal_uses_the_same_boundaries(value, expected_severity):
    row = pd.Series(
        {
            "PICOS выполнение %": value,
            "OSA выполнение %": pd.NA,
            "TOP16 выполнение %": pd.NA,
            "PICOS вес в KPI %": 1.0,
            "OSA вес в KPI %": 0.0,
            "TOP16 вес в KPI %": 0.0,
        }
    )
    records = _merch_personal_weak_metric_records(row)

    assert (records[0]["severity"] if records else None) == expected_severity


@pytest.mark.parametrize(
    ("value", "expected_level"),
    [(0.399, "hard"), (0.40, "soft"), (0.599, "soft"), (0.60, None)],
)
def test_tm_quality_uses_okk_boundaries(value, expected_level):
    row = pd.Series({"Качество команды %": value})
    levels = [record["level"] for record in _tm_metric_signal_records(row)]

    assert (levels[0] if levels else None) == expected_level


@pytest.mark.parametrize(
    ("value", "expected_level"),
    [(0.15, None), (0.151, "soft"), (0.20, "soft"), (0.201, "hard")],
)
def test_tm_fraud_uses_supervisor_boundaries(value, expected_level):
    row = pd.Series({"Фрод %": value})
    levels = [record["level"] for record in _tm_metric_signal_records(row)]

    assert (levels[0] if levels else None) == expected_level
