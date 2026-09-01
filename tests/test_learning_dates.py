import pandas as pd

from scripts.utils import parse_mixed_date_series


def test_learning_dates_use_russian_day_first_order():
    result = parse_mixed_date_series(pd.Series(["03.10.2026", "10.03.2026", "2026-07-31"]))

    assert result.iloc[0] == pd.Timestamp("2026-10-03")
    assert result.iloc[1] == pd.Timestamp("2026-03-10")
    assert result.iloc[2] == pd.Timestamp("2026-07-31")
