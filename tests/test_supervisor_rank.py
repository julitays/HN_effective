import pandas as pd

from scripts.builders.build_page5_sv_oed_data import _add_supervisor_monthly_rank


def test_supervisor_rank_is_unique_and_complete_with_ties():
    monthly = pd.DataFrame(
        {
            "MonthStart": pd.to_datetime(
                ["2026-07-01", "2026-07-01", "2026-07-01", "2026-08-01"]
            ),
            "ID супервайзера": ["SV-2", "SV-1", "SV-3", "SV-1"],
            "Индекс эффективности СВ %": [0.9, 0.9, 0.8, 0.7],
            "KPI месяца %": [0.95, 0.95, 0.9, 0.8],
            "ОКК команды %": [0.6, 0.6, 0.5, 0.4],
            "Обучение команды %": [1.0, 1.0, 0.9, 0.8],
            "Стабильность команды %": [0.9, 0.9, 0.8, 0.7],
        }
    )

    ranked = _add_supervisor_monthly_rank(monthly)
    july = ranked[ranked["MonthStart"] == pd.Timestamp("2026-07-01")]
    august = ranked[ranked["MonthStart"] == pd.Timestamp("2026-08-01")]

    assert july["Ранг СВ"].tolist() == [1, 2, 3]
    assert july["ID супервайзера"].tolist() == ["SV-1", "SV-2", "SV-3"]
    assert august["Ранг СВ"].tolist() == [1]
    assert str(ranked["Ранг СВ"].dtype) == "Int64"
