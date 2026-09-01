import pandas as pd

from scripts.staffing_utils import attach_last_quarter_metric


def test_quarter_metric_is_attached_by_region_not_source_index():
    monthly = pd.DataFrame(
        {
            "Регион BI": ["Москва", "Волга", "Москва", "Волга"],
            "MonthStart": pd.to_datetime(
                ["2026-01-01", "2026-01-01", "2026-02-01", "2026-02-01"]
            ),
        },
        index=[9, 2, 7, 4],
    )
    quarterly = pd.DataFrame(
        {
            "Регион BI": ["Москва", "Волга"],
            "QuarterStart": pd.to_datetime(["2026-01-01", "2026-01-01"]),
            "Риск ухода региона %": [0.12, 0.25],
        }
    )

    result = attach_last_quarter_metric(
        monthly,
        quarterly,
        "Риск ухода региона %",
        period="year",
    )

    values = result.groupby("Регион BI")["Риск ухода региона %"].unique().to_dict()
    assert values["Москва"].tolist() == [0.12]
    assert values["Волга"].tolist() == [0.25]
