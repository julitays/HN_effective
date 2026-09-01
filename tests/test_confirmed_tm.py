import pandas as pd

from scripts.parsers.parse_hr_registry import _confirmed_tm_pair
from scripts.staffing_utils import normalize_confirmed_tm


def test_missing_tm_stays_blank():
    source = pd.DataFrame(
        {
            "ID территориального менеджера": [pd.NA],
            "Территориальный менеджер": [pd.NA],
        }
    )

    result = normalize_confirmed_tm(source)

    assert pd.isna(result.loc[0, "ID территориального менеджера"])
    assert pd.isna(result.loc[0, "Территориальный менеджер"])


def test_explicit_vacancy_is_preserved():
    source = pd.DataFrame(
        {
            "ID территориального менеджера": [pd.NA],
            "Территориальный менеджер": ["Вакансия"],
        }
    )

    result = normalize_confirmed_tm(source)

    assert result.loc[0, "ID территориального менеджера"] == "NO_TM"
    assert result.loc[0, "Территориальный менеджер"] == "Вакансия / нет ТМ"


def test_hr_tm_pair_does_not_mix_vacancy_with_named_manager():
    tm_id, tm_name = _confirmed_tm_pair(
        {
            "ID территориального менеджера": "NO_TM",
            "Территориальный менеджер": "Вакансия / нет ТМ",
        },
        {
            "ID территориального менеджера": "TM-001",
            "Территориальный менеджер": "Иванов Иван",
        },
    )

    assert tm_id == "NO_TM"
    assert tm_name == "Вакансия / нет ТМ"
