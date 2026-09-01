import pandas as pd

from scripts.parsers.parse_hr_registry import classify_hr_events


def _hr_row(**overrides):
    row = {
        "Сотрудник": "Тестовый Сотрудник",
        "СНИЛС": "111-222-333 44",
        "ID сотрудника": "EMP-1",
        "Вид договора": "УД",
        "Состояние": "Увольнение",
        "Дата рождения": "1990-01-01",
        "Дата приема": "2026-05-01",
        "Дата увольнения": "2026-06-30",
        "Активен в USERS": True,
    }
    row.update(overrides)
    return row


def test_active_users_employee_is_not_counted_as_termination():
    hr = pd.DataFrame([_hr_row()])
    users = pd.DataFrame(
        [
            {
                "ID сотрудника": "EMP-1",
                "Дата приёма": "2026-05-01",
                "Активен": True,
            }
        ]
    )

    result = classify_hr_events(hr, users, pd.Timestamp("2026-08-20"))

    assert not bool(result.loc[0, "Учитывать в увольнении"])
    assert result.loc[0, "Тип кадрового движения"] == "Переоформление договора"


def test_repeated_episode_is_excluded_from_hires_and_prior_termination():
    hr = pd.DataFrame(
        [
            _hr_row(
                **{
                    "Вид договора": "УД",
                    "Дата приема": "2026-05-01",
                    "Дата увольнения": "2026-05-31",
                    "Активен в USERS": False,
                }
            ),
            _hr_row(
                **{
                    "Вид договора": "ТД",
                    "Состояние": "Работа",
                    "Дата приема": "2026-06-01",
                    "Дата увольнения": pd.NaT,
                    "Активен в USERS": True,
                }
            ),
        ]
    )
    users = pd.DataFrame(
        [
            {
                "ID сотрудника": "EMP-1",
                "Дата приёма": "2026-06-01",
                "Активен": True,
            }
        ]
    )

    result = classify_hr_events(hr, users, pd.Timestamp("2026-08-20"))

    assert bool(result.loc[0, "Учитывать в найме"])
    assert not bool(result.loc[0, "Учитывать в увольнении"])
    assert not bool(result.loc[1, "Учитывать в найме"])
    assert result.loc[1, "Тип кадрового движения"] == "Повторный выход на проект"


def test_future_termination_is_not_counted_before_snapshot_date():
    hr = pd.DataFrame(
        [
            _hr_row(
                **{
                    "Дата увольнения": "2026-08-25",
                    "Активен в USERS": True,
                }
            )
        ]
    )
    users = pd.DataFrame(
        [
            {
                "ID сотрудника": "EMP-1",
                "Дата приёма": "2026-05-01",
                "Активен": True,
            }
        ]
    )

    result = classify_hr_events(hr, users, pd.Timestamp("2026-08-20"))

    assert not bool(result.loc[0, "Учитывать в увольнении"])


def test_existing_users_tenure_marks_new_hr_record_as_reissue():
    hr = pd.DataFrame(
        [
            _hr_row(
                **{
                    "Вид договора": "ТД",
                    "Состояние": "Работа",
                    "Дата приема": "2026-06-30",
                    "Дата увольнения": pd.NaT,
                }
            )
        ]
    )
    users = pd.DataFrame(
        [
            {
                "ID сотрудника": "EMP-1",
                "Дата приёма": "2026-03-01",
                "Активен": True,
            }
        ]
    )

    result = classify_hr_events(hr, users, pd.Timestamp("2026-08-20"))

    assert not bool(result.loc[0, "Учитывать в найме"])
    assert result.loc[0, "Тип кадрового движения"] == "Переоформление кадровой записи"


def test_short_preemployment_window_remains_a_real_hire():
    hr = pd.DataFrame(
        [
            _hr_row(
                **{
                    "Вид договора": "ТД",
                    "Состояние": "Работа",
                    "Дата приема": "2026-06-30",
                    "Дата увольнения": pd.NaT,
                }
            )
        ]
    )
    users = pd.DataFrame(
        [
            {
                "ID сотрудника": "EMP-1",
                "Дата приёма": "2026-06-20",
                "Активен": True,
            }
        ]
    )

    result = classify_hr_events(hr, users, pd.Timestamp("2026-08-20"))

    assert bool(result.loc[0, "Учитывать в найме"])
