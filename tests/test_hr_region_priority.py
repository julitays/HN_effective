import pandas as pd

from scripts.parsers.parse_hr_registry import _resolve_hr_region


def test_detailed_hr_region_has_first_priority() -> None:
    row = pd.Series(
        {
            "Регион": "Урал",
            "Город": "Екатеринбург",
            "Макрорегион клиента": "Восток",
        }
    )

    result = _resolve_hr_region(row, {"Регион BI": "Москва"}, {})

    assert result == "Урал"


def test_exact_employee_region_precedes_ambiguous_macroregion() -> None:
    row = pd.Series(
        {
            "Регион": "Свердловская область",
            "Город": "Екатеринбург",
            "Макрорегион клиента": "Восток",
        }
    )

    result = _resolve_hr_region(row, {"Регион BI": "Урал"}, {})

    assert result == "Урал"


def test_vladimir_region_is_assigned_to_moscow() -> None:
    row = pd.Series(
        {
            "Регион": "Владимирская область",
            "Город": "Муром",
            "Макрорегион клиента": "Region_Moscow_Area",
        }
    )

    assert _resolve_hr_region(row, {"Регион BI": "Центр"}, {}) == "Москва"


def test_ambiguous_east_macroregion_is_not_used_without_exact_match() -> None:
    row = pd.Series(
        {
            "Регион": "Неизвестная область",
            "Город": "Неизвестный город",
            "Макрорегион клиента": "Восток",
        }
    )

    assert _resolve_hr_region(row, {}, {}) is None


def test_unambiguous_macroregion_is_used_last() -> None:
    row = pd.Series(
        {
            "Регион": pd.NA,
            "Город": pd.NA,
            "Макрорегион клиента": "Region_West",
        }
    )

    assert _resolve_hr_region(row, {}, {}) == "Северо-Запад"
