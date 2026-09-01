import pandas as pd

from scripts.builders.build_org_staffing_monthly_snapshot import (
    _build_historical_headcount,
    _build_open_vacancy_monthly,
)
from scripts.builders.build_page1_monthly_snapshot import _build_staffing_page1


def test_historical_headcount_uses_hire_and_termination_dates():
    hr = pd.DataFrame(
        [
            {
                "ID сотрудника": "ME-1",
                "Сотрудник": "Первый Сотрудник",
                "Роль": "МЕ",
                "Дата приема": "2025-12-15",
                "Дата увольнения": "2026-02-15",
                "Регион BI": "Москва",
                "ID территориального менеджера": "TM-1",
                "Территориальный менеджер": "ТМ Один",
                "ID супервайзера": "SV-1",
                "Супервайзер": "СВ Один",
            },
            {
                "ID сотрудника": "ME-2",
                "Сотрудник": "Второй Сотрудник",
                "Роль": "МЕ",
                "Дата приема": "2026-02-10",
                "Дата увольнения": pd.NaT,
                "Регион BI": "Москва",
                "ID территориального менеджера": "TM-1",
                "Территориальный менеджер": "ТМ Один",
                "ID супервайзера": "SV-1",
                "Супервайзер": "СВ Один",
            },
        ]
    )

    result = _build_historical_headcount(hr, pd.Timestamp("2026-03-01"))
    tm_rows = result[
        result["Уровень анализа"].eq("ТМ")
        & result["ID территориального менеджера"].eq("TM-1")
    ].set_index("YearMonth")

    assert tm_rows.loc[202601, "Активных МЕ"] == 1
    assert tm_rows.loc[202602, "Активных МЕ"] == 1


def test_historical_headcount_does_not_extend_into_current_users_month():
    hr = pd.DataFrame(
        [
            {
                "ID сотрудника": "ME-1",
                "Сотрудник": "Первый Сотрудник",
                "Роль": "МЕ",
                "Дата приема": "2025-12-15",
                "Дата увольнения": pd.NaT,
                "Регион BI": "Москва",
                "ID территориального менеджера": "TM-1",
                "Территориальный менеджер": "ТМ Один",
                "ID супервайзера": "SV-1",
                "Супервайзер": "СВ Один",
            }
        ]
    )

    result = _build_historical_headcount(hr, pd.Timestamp("2026-03-01"))

    assert set(result["YearMonth"].unique()) == {202601, 202602}


def test_open_vacancies_are_month_end_stock_not_monthly_opening_flow():
    current = pd.DataFrame(
        [
            {
                "ID вакансии": "VAC-OPEN",
                "Дата открытия": "2026-01-10",
                "Роль вакансии": "МЕ",
                "Приостановлена": False,
                "Регион BI": "Москва",
                "ID территориального менеджера": "TM-1",
                "Территориальный менеджер": "ТМ Один",
                "ID супервайзера": "SV-1",
                "Супервайзер": "СВ Один",
            }
        ]
    )
    closed = pd.DataFrame(
        [
            {
                "ID вакансии": "VAC-CLOSED",
                "Дата открытия": "2026-01-05",
                "Дата закрытия": "2026-03-15",
                "Роль вакансии": "МЕ",
                "Регион BI": "Москва",
                "ID территориального менеджера": "TM-1",
                "Территориальный менеджер": "ТМ Один",
                "ID супервайзера": "SV-1",
                "Супервайзер": "СВ Один",
            },
            {
                "ID вакансии": "VAC-MONTH-END",
                "Дата открытия": "2026-02-01",
                "Дата закрытия": "2026-02-28",
                "Роль вакансии": "МЕ",
                "Регион BI": "Москва",
                "ID территориального менеджера": "TM-1",
                "Территориальный менеджер": "ТМ Один",
                "ID супервайзера": "SV-1",
                "Супервайзер": "СВ Один",
            },
        ]
    )

    result = _build_open_vacancy_monthly(
        current,
        closed,
        active_month=pd.Timestamp("2026-03-01"),
    )
    monthly = result.groupby("YearMonth")["Открытых вакансий"].sum().to_dict()

    assert monthly == {202601: 2, 202602: 2, 202603: 1}
    assert result["Источник вакансии"].eq("незакрытый остаток на конец месяца").all()


def test_closed_vacancy_wins_over_stale_current_record_with_same_id():
    current = pd.DataFrame(
        [
            {
                "ID вакансии": "VAC-1",
                "Дата открытия": "2026-01-01",
                "Роль вакансии": "МЕ",
                "Регион BI": "Москва",
            }
        ]
    )
    closed = pd.DataFrame(
        [
            {
                "ID вакансии": "VAC-1",
                "Дата открытия": "2026-01-01",
                "Дата закрытия": "2026-02-10",
                "Роль вакансии": "МЕ",
                "Регион BI": "Москва",
            }
        ]
    )

    result = _build_open_vacancy_monthly(
        current,
        closed,
        active_month=pd.Timestamp("2026-03-01"),
    )

    assert set(result["YearMonth"].unique()) == {202601}


def test_page1_uses_headcount_from_same_month(tmp_path):
    staffing = pd.DataFrame(
        [
            {
                "MonthStart": pd.Timestamp("2026-01-01"),
                "YearMonth": 202601,
                "Регион BI": "Москва",
                "Уровень анализа": "Регион",
                "Активных МЕ": 100,
                "Активных СВ": 10,
                "Активных ТМ": 2,
                "Открытых вакансий": 10,
                "Открытых вакансий МЕ": 10,
                "Открытых вакансий СВ": 0,
                "Приостановленных вакансий": 0,
                "Нанято": 5,
                "Уволено": 8,
                "Чистый отток": 3,
                "Баланс персонала": -3,
            },
            {
                "MonthStart": pd.Timestamp("2026-02-01"),
                "YearMonth": 202602,
                "Регион BI": "Москва",
                "Уровень анализа": "Регион",
                "Активных МЕ": 200,
                "Активных СВ": 20,
                "Активных ТМ": 3,
                "Открытых вакансий": 20,
                "Открытых вакансий МЕ": 20,
                "Открытых вакансий СВ": 0,
                "Приостановленных вакансий": 0,
                "Нанято": 6,
                "Уволено": 7,
                "Чистый отток": 1,
                "Баланс персонала": -1,
            },
        ]
    )
    staffing.to_parquet(tmp_path / "org_staffing_report_snapshot.parquet", index=False)

    monthly, current = _build_staffing_page1(tmp_path)

    assert monthly.set_index("YearMonth").loc[202601, "Активных МЕ"] == 100
    assert monthly.set_index("YearMonth").loc[202602, "Активных МЕ"] == 200
    assert list(current.columns) == ["Регион BI"]
    assert current.empty
