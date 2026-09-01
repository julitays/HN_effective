import pandas as pd

import scripts.corporate_university as corporate_university
from scripts.builders.build_org_staffing_monthly_snapshot import _users_snapshot_month
from scripts.parsers.learning_parser import _epoch_to_moscow
from scripts.parsers.users_parser import _build_full_name
from scripts.utils import load_settings


def test_database_sources_are_explicit() -> None:
    settings = load_settings()

    assert settings["sources"]["users"]["source"] == "corporate_university"
    assert settings["sources"]["learning"]["source"] == "corporate_university"


def test_epoch_is_converted_from_utc_to_moscow() -> None:
    epoch = pd.Series([pd.Timestamp("2026-08-31 09:00:00", tz="UTC").timestamp()])

    result = _epoch_to_moscow(epoch)

    assert result.iloc[0] == pd.Timestamp("2026-08-31 12:00:00")
    assert result.dt.tz is None


def test_full_name_ignores_database_nulls() -> None:
    row = pd.Series(
        {"last_name": "Иванов", "first_name": "Иван", "middle_name": pd.NA}
    )

    assert _build_full_name(row) == "Иванов Иван"


def test_database_snapshot_month_uses_calculation_date(monkeypatch) -> None:
    monkeypatch.setenv("HN_AS_OF_DATE", "2026-08-31")
    assert _users_snapshot_month() == pd.Timestamp("2026-08-01")


def test_stored_password_has_priority_over_environment(monkeypatch) -> None:
    settings = load_settings()
    config = settings["corporate_university"]
    corporate_university.clear_password_cache()
    monkeypatch.setattr(
        corporate_university, "get_stored_password", lambda current_config: "stored"
    )
    monkeypatch.setenv(config["password_env"], "environment")

    assert corporate_university._get_password(config) == "stored"

    corporate_university.clear_password_cache()
