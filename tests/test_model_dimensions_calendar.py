import pandas as pd

from scripts.builders.build_model_dimensions import _build_dmonth


def test_dmonth_does_not_include_future_learning_months():
    current_month = pd.Timestamp.today().normalize().replace(day=1)
    future_month = current_month + pd.offsets.MonthBegin(4)
    tables = [
        (
            "learning_monthly.parquet",
            pd.DataFrame({"MonthStart": [pd.Timestamp("2026-01-01"), future_month]}),
        )
    ]

    result = _build_dmonth(tables)

    assert result["MonthStart"].max() == current_month
