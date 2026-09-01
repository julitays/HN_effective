import pandas as pd
import pytest

from scripts.builders.build_page7_tm_data import (
    _append_missing_entity_mapping,
    _refresh_tm_effectiveness,
)


def test_appends_only_missing_entity_month_mapping():
    primary = pd.DataFrame(
        {
            "MonthStart": pd.to_datetime(["2026-07-01"]),
            "YearMonth": [202607],
            "ID супервайзера": ["SV1"],
            "ID территориального менеджера": ["TM-HISTORY"],
        }
    )
    fallback = pd.DataFrame(
        {
            "MonthStart": pd.to_datetime(["2026-07-01", "2026-08-01"]),
            "YearMonth": [202607, 202608],
            "ID супервайзера": ["SV1", "SV1"],
            "ID территориального менеджера": ["TM-CURRENT", "TM-CURRENT"],
        }
    )

    result = _append_missing_entity_mapping(
        primary,
        fallback,
        "ID супервайзера",
    ).sort_values("YearMonth")

    assert result["YearMonth"].tolist() == [202607, 202608]
    assert result["ID территориального менеджера"].tolist() == ["TM-HISTORY", "TM-CURRENT"]


def test_tm_kpi_target_remains_numeric_after_refresh():
    frame = pd.DataFrame(
        {
            "KPI проекта %": [0.97],
            "PICOS выполнение %": [0.98],
            "PICOS вес в KPI %": [1.0],
            "OSA выполнение %": [pd.NA],
            "OSA вес в KPI %": [0.0],
            "TOP16 выполнение %": [pd.NA],
            "TOP16 вес в KPI %": [0.0],
            "Качество команды %": [0.70],
            "Обучение команды %": [0.95],
            "Фрод %": [0.05],
            "Стабильность команды %": [0.96],
            "Текучесть %": [0.02],
        }
    )

    result = _refresh_tm_effectiveness(frame)

    assert pd.api.types.is_numeric_dtype(result["Целевой порог KPI территории %"])
    assert result.loc[0, "Целевой порог KPI территории %"] == pytest.approx(0.98)
