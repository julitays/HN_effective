import sys
from pathlib import Path

import numpy as np
import pandas as pd

if __package__ is None or __package__ == "":
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from scripts.utils import (
    load_settings,
    mean_numeric,
    save_parquet,
    normalize_pct as _normalize_pct,
    normalize_valid_pct as _normalize_valid_pct,
    normalize_person_name as _normalize_name,
)
from scripts.staffing_utils import normalize_confirmed_tm


OED_PERSONAL_COMPONENTS = [
    "KPI ОЭД %",
    "Стандарты ОЭД %",
    "Продукт ОЭД %",
    "Управление ОЭД %",
    "Аттестация ОЭД %",
]

NO_TM_ID = "NO_TM"
NO_TM_NAME = "Вакансия / нет ТМ"


def _normalize_oed_role(role: str | None, position: str | None) -> str:
    role_text = str(role or "").lower()
    position_text = str(position or "").lower()

    if "территори" in position_text or position_text.strip() == "tm":
        return "ТМ"
    if "супервайзер" in position_text:
        return "СВ"
    if "мерч" in position_text:
        return "МЕ"
    if role_text == "sv":
        return "СВ"
    if role_text == "sv-1":
        return "МЕ"
    return "Прочее"


def _class_group(oed_class: str | None) -> str:
    text = str(oed_class or "").strip().lower().replace("ё", "е")
    if not text:
        return "Недостаточно данных"
    if "топ" in text:
        return "ТОП"
    if "мастер" in text:
        return "Мастер"
    if "специалист" in text:
        return "Специалист"
    if "нович" in text:
        return "Новичок"
    if "требует" in text:
        return "Требует развития"
    return str(oed_class).strip()


def _personal_effectiveness(row: pd.Series):
    values = [row.get(column) for column in OED_PERSONAL_COMPONENTS]
    numeric = [float(value) for value in values if pd.notna(value)]
    if not numeric:
        return pd.NA
    return float(np.mean(numeric))


def _build_team_lookup(teams: pd.DataFrame, supervisors: pd.DataFrame, tms: pd.DataFrame) -> pd.DataFrame:
    me_lookup = teams[
        [
            "ID мерчендайзера",
            "ID супервайзера",
            "Супервайзер",
            "ID территориального менеджера",
            "Территориальный менеджер",
            "Регион BI",
            "Группа региона",
        ]
    ].dropna(subset=["ID мерчендайзера"]).copy()
    me_lookup = me_lookup.rename(columns={"ID мерчендайзера": "ID сотрудника"})
    me_lookup["ID территориального менеджера"] = me_lookup["ID территориального менеджера"].replace("", pd.NA)
    me_lookup = normalize_confirmed_tm(me_lookup)

    sv_lookup = supervisors[
        [
            "ID супервайзера",
            "Супервайзер",
            "Код СВ",
            "СВ / Объект",
            "ID территориального менеджера",
            "Территориальный менеджер",
            "Регион BI",
            "Группа региона",
        ]
    ].dropna(subset=["ID супервайзера"]).copy()
    sv_lookup = sv_lookup.rename(columns={"ID супервайзера": "ID сотрудника"})
    sv_lookup["ID супервайзера"] = sv_lookup["ID сотрудника"]
    sv_lookup["ID территориального менеджера"] = sv_lookup["ID территориального менеджера"].replace("", pd.NA)
    sv_lookup = normalize_confirmed_tm(sv_lookup)

    tm_lookup = tms[
        [
            "ID территориального менеджера",
            "Территориальный менеджер",
            "Регион BI",
            "Группа региона",
        ]
    ].dropna(subset=["ID территориального менеджера"]).copy()
    tm_lookup = tm_lookup.rename(columns={"ID территориального менеджера": "ID сотрудника"})
    tm_lookup["ID территориального менеджера"] = tm_lookup["ID сотрудника"]

    lookup = pd.concat([me_lookup, sv_lookup, tm_lookup], ignore_index=True, sort=False)
    return (
        lookup.replace("", pd.NA)
        .sort_values(["ID сотрудника", "Регион BI"])
        .drop_duplicates("ID сотрудника", keep="first")
    )


def _build_sv_team_aggregates(oed: pd.DataFrame) -> pd.DataFrame:
    team_base = oed[
        oed["Уровень ОЭД"].eq("МЕ")
        & oed["ID супервайзера"].notna()
    ].copy()
    if team_base.empty:
        return pd.DataFrame(columns=["YearQuarter", "ID супервайзера"])

    grouped = (
        team_base.groupby(["QuarterStart", "YearQuarter", "QuarterLabel", "ID супервайзера"], dropna=False)
        .agg(
            **{
                "МЕ с ОЭД": ("ID сотрудника", "nunique"),
                "Средний KPI ОЭД команды МЕ %": ("KPI ОЭД %", mean_numeric),
                "Средняя личная эффективность команды МЕ %": ("Личная эффективность ОЭД %", mean_numeric),
                "Средний рейтинг команды МЕ ОЭД": ("Рейтинг ОЭД", mean_numeric),
                "ТОП/Мастер в команде ОЭД": ("Класс ОЭД", lambda s: s.map(_class_group).isin(["ТОП", "Мастер"]).sum()),
                "Требует развития в команде ОЭД": ("Класс ОЭД", lambda s: s.map(_class_group).eq("Требует развития").sum()),
            }
        )
        .reset_index()
    )
    grouped["Доля ТОП/Мастер в команде ОЭД %"] = (
        grouped["ТОП/Мастер в команде ОЭД"] / grouped["МЕ с ОЭД"].replace(0, np.nan)
    )
    grouped["Доля требует развития в команде ОЭД %"] = (
        grouped["Требует развития в команде ОЭД"] / grouped["МЕ с ОЭД"].replace(0, np.nan)
    )
    return grouped


def build_oed_quarterly_snapshot() -> pd.DataFrame:
    settings = load_settings()
    out_dir = Path(settings["paths"]["out"])

    fact_oed = pd.read_parquet(out_dir / "fact_oed.parquet")
    dim_employees = pd.read_parquet(out_dir / "dim_employees.parquet")
    teams = pd.read_parquet(out_dir / "dim_teams.parquet")
    supervisors = pd.read_parquet(out_dir / "dSupervisor.parquet")
    tms_path = out_dir / "dTM.parquet"
    tms = pd.read_parquet(tms_path) if tms_path.exists() else pd.DataFrame(
        columns=["ID территориального менеджера", "Территориальный менеджер", "Регион BI", "Группа региона"]
    )

    dim = dim_employees[
        [
            "ID сотрудника",
            "ФИО",
            "Должность",
            "Проект",
            "Активен",
            "Авторизация",
            "Дата приёма",
            "Стаж (дней)",
            "Стаж (месяцев)",
            "Регион BI",
            "Группа региона",
            "ID руководителя",
            "ФИО руководителя",
        ]
    ].copy()
    dim = dim[
        dim["Активен"].fillna(False).eq(True)
        & dim["Проект"].astype(str).eq("H&N")
    ].copy()

    org_lookup = _build_team_lookup(teams, supervisors, tms)

    oed = fact_oed.copy().rename(
        columns={
            "Роль": "Роль источника",
            "Балл KPI": "KPI ОЭД %",
            "Стандарты": "Стандарты ОЭД %",
            "Продукт": "Продукт ОЭД %",
            "Управление": "Управление ОЭД %",
            "Аттестация": "Аттестация ОЭД %",
            "Команда": "Команда ОЭД %",
            "Рейтинг": "Рейтинг ОЭД",
            "Класс": "Класс ОЭД",
            "ID руководителя": "ID руководителя ОЭД",
        }
    )
    oed = oed.merge(dim, on="ID сотрудника", how="inner", suffixes=("", "_users"))
    oed = oed.merge(org_lookup, on="ID сотрудника", how="left", suffixes=("", "_org"))

    for column in ["Регион BI", "Группа региона"]:
        org_column = f"{column}_org"
        if org_column in oed.columns:
            oed[column] = oed[org_column].combine_first(oed[column])

    oed["ID руководителя USERS"] = oed["ID руководителя"]
    oed["ФИО руководителя USERS"] = oed["ФИО руководителя"]
    oed["Сотрудник"] = oed["ФИО"]
    oed["Сотрудник норм"] = oed["Сотрудник"].map(_normalize_name)
    oed["Уровень ОЭД"] = oed.apply(lambda row: _normalize_oed_role(row.get("Роль источника"), row.get("Должность")), axis=1)

    for column in OED_PERSONAL_COMPONENTS + ["Команда ОЭД %"]:
        if column in oed.columns:
            oed[column] = _normalize_valid_pct(oed[column])
    oed["Рейтинг ОЭД"] = pd.to_numeric(oed["Рейтинг ОЭД"], errors="coerce")
    oed["Изменение рейтинга"] = pd.to_numeric(oed["Изменение рейтинга"], errors="coerce")
    oed["Изменение рейтинга (итого)"] = pd.to_numeric(oed["Изменение рейтинга (итого)"], errors="coerce")
    oed["Периодов снижения подряд"] = pd.to_numeric(oed["Периодов снижения подряд"], errors="coerce")
    oed["Периодов 'Требует развития' подряд"] = pd.to_numeric(
        oed["Периодов 'Требует развития' подряд"],
        errors="coerce",
    )

    oed["Личная эффективность ОЭД %"] = oed.apply(_personal_effectiveness, axis=1)
    oed["Класс ОЭД группа"] = oed["Класс ОЭД"].map(_class_group)
    oed["Есть оценка ОЭД"] = oed["Рейтинг ОЭД"].notna()
    oed["Риск оттока ОЭД"] = oed["Риск оттока"].fillna(False).eq(True)

    team_aggregates = _build_sv_team_aggregates(oed)
    oed = oed.merge(
        team_aggregates,
        on=["QuarterStart", "YearQuarter", "QuarterLabel", "ID супервайзера"],
        how="left",
    )

    columns = [
        "QuarterStart",
        "YearQuarter",
        "QuarterLabel",
        "Период",
        "Год",
        "Квартал",
        "ID сотрудника",
        "Сотрудник",
        "Должность",
        "Уровень ОЭД",
        "Роль источника",
        "Активен",
        "Авторизация",
        "Дата приёма",
        "Стаж (дней)",
        "Стаж (месяцев)",
        "Регион BI",
        "Группа региона",
        "ID супервайзера",
        "Супервайзер",
        "Код СВ",
        "СВ / Объект",
        "ID территориального менеджера",
        "Территориальный менеджер",
        "ID руководителя ОЭД",
        "ID руководителя USERS",
        "ФИО руководителя USERS",
        "KPI ОЭД %",
        "Стандарты ОЭД %",
        "Продукт ОЭД %",
        "Управление ОЭД %",
        "Аттестация ОЭД %",
        "Команда ОЭД %",
        "Личная эффективность ОЭД %",
        "Рейтинг ОЭД",
        "Класс ОЭД",
        "Класс ОЭД группа",
        "Комментарий",
        "Дата договора",
        "Первый период",
        "Изменение рейтинга",
        "Изменение рейтинга (итого)",
        "Периодов снижения подряд",
        "Периодов 'Требует развития' подряд",
        "Риск оттока ОЭД",
        "Есть оценка ОЭД",
        "МЕ с ОЭД",
        "Средний KPI ОЭД команды МЕ %",
        "Средняя личная эффективность команды МЕ %",
        "Средний рейтинг команды МЕ ОЭД",
        "ТОП/Мастер в команде ОЭД",
        "Требует развития в команде ОЭД",
        "Доля ТОП/Мастер в команде ОЭД %",
        "Доля требует развития в команде ОЭД %",
    ]
    snapshot = oed[[c for c in columns if c in oed.columns]].copy()

    numeric_columns = [
        "YearQuarter",
        "Год",
        "Квартал",
        "Стаж (дней)",
        "Стаж (месяцев)",
        "KPI ОЭД %",
        "Стандарты ОЭД %",
        "Продукт ОЭД %",
        "Управление ОЭД %",
        "Аттестация ОЭД %",
        "Команда ОЭД %",
        "Личная эффективность ОЭД %",
        "Рейтинг ОЭД",
        "Изменение рейтинга",
        "Изменение рейтинга (итого)",
        "Периодов снижения подряд",
        "Периодов 'Требует развития' подряд",
        "МЕ с ОЭД",
        "Средний KPI ОЭД команды МЕ %",
        "Средняя личная эффективность команды МЕ %",
        "Средний рейтинг команды МЕ ОЭД",
        "ТОП/Мастер в команде ОЭД",
        "Требует развития в команде ОЭД",
        "Доля ТОП/Мастер в команде ОЭД %",
        "Доля требует развития в команде ОЭД %",
    ]
    for column in numeric_columns:
        if column in snapshot.columns:
            snapshot[column] = pd.to_numeric(snapshot[column], errors="coerce")

    snapshot = snapshot.sort_values(["QuarterStart", "Уровень ОЭД", "Регион BI", "Сотрудник"]).reset_index(drop=True)
    save_parquet(snapshot, str(out_dir / "oed_quarterly_snapshot.parquet"))

    print(f"\n  OED quarterly snapshot: {len(snapshot)} строк")
    print(f"  OED active employees: {snapshot['ID сотрудника'].nunique()} сотрудников")
    return snapshot


if __name__ == "__main__":
    build_oed_quarterly_snapshot()
