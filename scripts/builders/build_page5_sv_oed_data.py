import sys
from pathlib import Path

import numpy as np
import pandas as pd

if __package__ is None or __package__ == "":
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from scripts.utils import (
    load_settings,
    save_parquet,
    normalize_pct as _normalize_pct,
    normalize_valid_pct as _normalize_valid_pct,
    normalize_person_name as _normalize_name,
    mean_numeric as _mean_numeric,
    last_notna as _first_notna,
    extract_sv_code as _extract_sv_code,
)
from scripts.staffing_utils import (
    is_tm_role,
    missing_supervisor_key,
    normalize_confirmed_tm,
    score_higher_is_better as _score_higher_is_better,
    score_lower_is_better as _score_lower_is_better,
)
from scripts.kpi_metric_utils import aggregate_employee_kpi_to_org


CLIENT_ATTESTATION_QUARTERS = load_settings()["reporting"]["client_attestation_quarters"]

SV_MIN_AVAILABLE_WEIGHT = 0.60
SV_STATUS_STABLE_MIN_SCORE = 0.90

SV_KPI_GREEN_MIN = 0.99
SV_KPI_RED_MIN = 0.95
SV_KPI_COMPONENT_GREEN_MINS = {
    "PICOS": 0.98,
    "OSA": 0.95,
    "TOP16": 0.95,
}
SV_OKK_GREEN_MIN = 0.60
SV_OKK_RED_MIN = 0.40
SV_LEARNING_SOFT_MIN = 0.95
SV_LEARNING_HARD_MIN = 0.90
SV_FRAUD_GREEN_MAX = 0.15
SV_FRAUD_RED_MAX = 0.20
SV_STAFFING_SOFT_MIN = 0.95
SV_STAFFING_HARD_MIN = 0.90
SV_TURNOVER_GREEN_MAX = 0.10
SV_TURNOVER_RED_MAX = 0.15

SV_EFFECTIVENESS_WEIGHTS = {
    "KPI месяца %": 0.35,
    "ОКК команды %": 0.15,
    "Обучение команды %": 0.15,
    "Фрод %": 0.15,
    "Стабильность команды %": 0.15,
    "Текучесть команды %": 0.05,
}
SV_SIGNAL_WEIGHTS = {
    "ОКК команды": SV_EFFECTIVENESS_WEIGHTS["ОКК команды %"],
    "Обучение команды": SV_EFFECTIVENESS_WEIGHTS["Обучение команды %"],
    "Фрод": SV_EFFECTIVENESS_WEIGHTS["Фрод %"],
    "Стабильность команды": SV_EFFECTIVENESS_WEIGHTS["Стабильность команды %"],
    "Текучесть": SV_EFFECTIVENESS_WEIGHTS["Текучесть команды %"],
}
SV_KPI_SIGNAL_COLUMNS = {
    "PICOS": "PICOS выполнение %",
    "OSA": "OSA выполнение %",
    "TOP16": "TOP16 выполнение %",
}
SV_EFFECTIVENESS_CRITICAL_COLUMNS = ["KPI месяца %"]

SV_PERSONAL_MIN_AVAILABLE_WEIGHT = 0.60
SV_PERSONAL_HIGH_MIN_SCORE = 0.95
SV_PERSONAL_ROLE_MIN_SCORE = 0.90
SV_PERSONAL_GREEN_MIN = 0.95
SV_PERSONAL_RED_MIN = 0.90

SV_PERSONAL_EFFECTIVENESS_WEIGHTS = {
    "Аттестация клиента %": 0.40,
    "Аттестация ОЭД %": 0.20,
    "Продукт ОЭД %": 0.20,
    "Управление ОЭД %": 0.20,
}

OED_WEIGHT_START = 0.35
OED_WEIGHT_STEP = 0.05
OED_WEIGHT_FLOOR = 0.10

RESERVE_CANDIDATE_MIN_SCORE = 0.90
RESERVE_CANDIDATE_MIN_STAFFING = 0.80

NO_TM_ID = "NO_TM"
NO_TM_NAME = "Вакансия / нет ТМ"
NO_SV_ID = "NO_SV"
NO_SV_NAME = "Вакансия / нет СВ"


def _build_no_sv_supervisor_rows(teams: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    if teams is None or teams.empty or "ID супервайзера" not in teams.columns:
        return pd.DataFrame(columns=columns)

    work = teams.replace("", pd.NA).copy()
    missing_sv = work["ID супервайзера"].isna() | work["ID супервайзера"].astype("string").str.strip().eq(NO_SV_ID).fillna(False)
    work = work[missing_sv].copy()
    if work.empty:
        return pd.DataFrame(columns=columns)

    work["Регион BI"] = work.get("Регион BI", pd.Series(index=work.index, dtype="object")).replace("", pd.NA).fillna("Без региона")
    work["Группа региона"] = work.get("Группа региона", pd.Series(index=work.index, dtype="object")).replace("", pd.NA).fillna("core")
    work["ID территориального менеджера"] = (
        work.get("ID территориального менеджера", pd.Series(index=work.index, dtype="object"))
        .replace("", pd.NA)
    )
    work = normalize_confirmed_tm(work)
    work["ID супервайзера"] = work.apply(
        lambda row: missing_supervisor_key(row.get("Регион BI"), row.get("ID территориального менеджера")),
        axis=1,
    )
    work["Супервайзер"] = NO_SV_NAME
    work["Дата приема СВ"] = pd.NaT
    work["Стаж СВ (дней)"] = pd.NA
    work["Стаж СВ (месяцев)"] = pd.NA
    work["Код СВ"] = NO_SV_ID
    work["СВ / Объект"] = NO_SV_NAME
    if "Показывать в срезе" in columns:
        work["Показывать в срезе"] = True

    return work[columns].drop_duplicates("ID супервайзера")


def _effective_oed_month(series: pd.Series) -> pd.Series:
    quarter_start = pd.to_datetime(series, errors="coerce")
    return quarter_start + pd.DateOffset(months=1)


def _months_after(month_series: pd.Series, oed_month_series: pd.Series) -> pd.Series:
    month_start = pd.to_datetime(month_series, errors="coerce")
    oed_month = pd.to_datetime(oed_month_series, errors="coerce")
    months = (
        (month_start.dt.year - oed_month.dt.year) * 12
        + (month_start.dt.month - oed_month.dt.month)
    )
    return months.where(month_start.notna() & oed_month.notna())


def _available_weight_from_row(row: pd.Series) -> float:
    return sum(weight for column, weight in SV_EFFECTIVENESS_WEIGHTS.items() if pd.notna(row.get(column)))


def _weighted_score_from_row(row: pd.Series):
    available_weight = _available_weight_from_row(row)
    if available_weight < SV_MIN_AVAILABLE_WEIGHT:
        return pd.NA
    if any(pd.isna(row.get(column)) for column in SV_EFFECTIVENESS_CRITICAL_COLUMNS):
        return pd.NA

    score = 0.0
    score += _score_higher_is_better(
        row.get("KPI месяца %"),
        SV_KPI_GREEN_MIN,
        SV_KPI_RED_MIN,
        SV_EFFECTIVENESS_WEIGHTS["KPI месяца %"],
    )
    score += _score_higher_is_better(
        row.get("ОКК команды %"),
        SV_OKK_GREEN_MIN,
        SV_OKK_RED_MIN,
        SV_EFFECTIVENESS_WEIGHTS["ОКК команды %"],
    )
    score += _score_higher_is_better(
        row.get("Обучение команды %"),
        SV_LEARNING_SOFT_MIN,
        SV_LEARNING_HARD_MIN,
        SV_EFFECTIVENESS_WEIGHTS["Обучение команды %"],
    )
    score += _score_lower_is_better(
        row.get("Фрод %"),
        SV_FRAUD_GREEN_MAX,
        SV_FRAUD_RED_MAX,
        SV_EFFECTIVENESS_WEIGHTS["Фрод %"],
    )
    score += _score_higher_is_better(
        row.get("Стабильность команды %"),
        SV_STAFFING_SOFT_MIN,
        SV_STAFFING_HARD_MIN,
        SV_EFFECTIVENESS_WEIGHTS["Стабильность команды %"],
    )
    score += _score_lower_is_better(
        row.get("Текучесть команды %"),
        SV_TURNOVER_GREEN_MAX,
        SV_TURNOVER_RED_MAX,
        SV_EFFECTIVENESS_WEIGHTS["Текучесть команды %"],
    )
    return round(max(0.0, min(1.0, score)), 4)


def _personal_available_weight_from_row(row: pd.Series) -> float:
    return sum(weight for column, weight in SV_PERSONAL_EFFECTIVENESS_WEIGHTS.items() if pd.notna(row.get(column)))


def _personal_score_from_row(row: pd.Series):
    available_weight = _personal_available_weight_from_row(row)
    if available_weight <= 0:
        return pd.NA
    if bool(row.get("Есть ОЭД СВ")) and available_weight < SV_PERSONAL_MIN_AVAILABLE_WEIGHT:
        return pd.NA

    weighted_sum = 0.0
    for column, weight in SV_PERSONAL_EFFECTIVENESS_WEIGHTS.items():
        value = row.get(column)
        if pd.notna(value):
            value = float(value)
            weighted_sum += (0.0 if value < SV_PERSONAL_RED_MIN else value) * weight
    return weighted_sum / available_weight


def _client_attestation_reason_label(row: pd.Series) -> str:
    quarter_label = row.get("QuarterLabel аттестации клиента")
    if pd.notna(quarter_label) and str(quarter_label).strip():
        return f"Аттестация клиента {str(quarter_label).strip()}"
    return "Аттестация клиента"


def _personal_weak_metric_records(row: pd.Series) -> list[dict]:
    records: list[dict] = []
    for order, (column, label) in enumerate(
        [
            ("Аттестация клиента %", _client_attestation_reason_label(row)),
            ("Аттестация ОЭД %", "Аттестация ОЭД"),
            ("Продукт ОЭД %", "Продукт ОЭД"),
            ("Управление ОЭД %", "Управление ОЭД"),
        ],
        start=1,
    ):
        value = row.get(column)
        if pd.notna(value) and float(value) < SV_PERSONAL_GREEN_MIN:
            level = "hard" if float(value) < SV_PERSONAL_RED_MIN else "soft"
            weight = SV_PERSONAL_EFFECTIVENESS_WEIGHTS[column]
            severity = 1.0 if level == "hard" else 0.5
            records.append(
                {
                    "metric": label,
                    "level": level,
                    "weight": weight,
                    "priority": weight * severity,
                    "order": order,
                }
            )

    return sorted(records, key=lambda record: (-record["priority"], -record["weight"], record["order"]))


def _personal_status_from_row(row: pd.Series) -> str:
    score = row.get("Личная эффективность СВ %")
    available_weight = row.get("Доступность личных метрик %")
    has_oed = bool(row.get("Есть ОЭД СВ"))
    oed_class = str(row.get("Класс ОЭД") or "").strip().upper()

    if not has_oed:
        return "Новичок"
    if pd.isna(score) or pd.isna(available_weight) or available_weight <= 0:
        return "Недостаточно данных"
    if available_weight < SV_PERSONAL_MIN_AVAILABLE_WEIGHT:
        return "Недостаточно данных"

    records = _personal_weak_metric_records(row)
    hard = [record for record in records if record["level"] == "hard"]
    soft = [record for record in records if record["level"] == "soft"]

    if hard or score < SV_PERSONAL_ROLE_MIN_SCORE:
        return "Зона развития"
    if (
        score >= SV_PERSONAL_HIGH_MIN_SCORE
        and not soft
        and available_weight >= 1
        and oed_class == "ТОП"
    ):
        return "Высокая личная готовность"
    if score >= SV_PERSONAL_ROLE_MIN_SCORE:
        return "Соответствует роли"
    return "Зона развития"


def _personal_reason_from_row(row: pd.Series):
    hard_parts = list(
        dict.fromkeys(
            record["metric"]
            for record in _personal_weak_metric_records(row)
            if record["level"] == "hard"
        )
    )
    if hard_parts:
        return ", ".join(hard_parts)
    return pd.NA


def _score_zone(score) -> str:
    if pd.isna(score):
        return "Недостаточно данных"
    if score >= SV_STATUS_STABLE_MIN_SCORE:
        return "Высокая готовность"
    if score >= 0.80:
        return "Соответствует роли"
    return "Зона развития"


def _staffing_signal_level(row: pd.Series) -> str | None:
    staffing = row.get("Стабильность команды %")
    if pd.isna(staffing):
        return None
    if staffing < SV_STAFFING_HARD_MIN:
        return "hard"
    if staffing < SV_STAFFING_SOFT_MIN:
        return "soft"
    return None


def _sv_signal_records(row: pd.Series) -> list[dict]:
    okk = row.get("ОКК команды %")
    learning = row.get("Обучение команды %")
    fraud_pct = row.get("Фрод %")
    turnover = row.get("Текучесть команды %")

    records: list[dict] = []

    def add_signal(metric: str, level: str, order: int, weight: float | None = None):
        weight = SV_SIGNAL_WEIGHTS[metric] if weight is None else weight
        severity = 1.0 if level == "hard" else 0.5
        records.append(
            {
                "metric": metric,
                "level": level,
                "weight": weight,
                "priority": weight * severity,
                "order": order,
            }
        )

    active_kpi_components = [
        (label, pd.to_numeric(row.get(column), errors="coerce"))
        for label, column in SV_KPI_SIGNAL_COLUMNS.items()
        if pd.notna(pd.to_numeric(row.get(column), errors="coerce"))
    ]
    kpi_signal_weight = (
        SV_EFFECTIVENESS_WEIGHTS["KPI месяца %"] / len(active_kpi_components)
        if active_kpi_components
        else 0.0
    )
    for order, (label, value) in enumerate(active_kpi_components, start=1):
        if value < SV_KPI_RED_MIN:
            add_signal(label, "hard", order, kpi_signal_weight)
        elif value < SV_KPI_COMPONENT_GREEN_MINS[label]:
            add_signal(label, "soft", order, kpi_signal_weight)

    if pd.notna(okk):
        if okk < SV_OKK_RED_MIN:
            add_signal("ОКК команды", "hard", 4)
        elif okk < SV_OKK_GREEN_MIN:
            add_signal("ОКК команды", "soft", 4)

    if pd.notna(learning):
        if learning < SV_LEARNING_HARD_MIN:
            add_signal("Обучение команды", "hard", 5)
        elif learning < SV_LEARNING_SOFT_MIN:
            add_signal("Обучение команды", "soft", 5)

    if pd.notna(fraud_pct):
        if fraud_pct > SV_FRAUD_RED_MAX:
            add_signal("Фрод", "hard", 6)
        elif fraud_pct > SV_FRAUD_GREEN_MAX:
            add_signal("Фрод", "soft", 6)

    staffing_level = _staffing_signal_level(row)
    if staffing_level == "hard":
        add_signal("Стабильность команды", "hard", 7)
    elif staffing_level == "soft":
        add_signal("Стабильность команды", "soft", 7)

    if pd.notna(turnover):
        if turnover > SV_TURNOVER_RED_MAX:
            add_signal("Текучесть", "hard", 8)
        elif turnover > SV_TURNOVER_GREEN_MAX:
            add_signal("Текучесть", "soft", 8)

    return records


def _sv_signal_lists(row: pd.Series) -> tuple[list[str], list[str]]:
    records = sorted(
        _sv_signal_records(row),
        key=lambda record: (-record["priority"], -record["weight"], record["order"]),
    )
    hard = [record["metric"] for record in records if record["level"] == "hard"]
    soft = [record["metric"] for record in records if record["level"] == "soft"]
    return hard, soft


def _signal_weight_denominator(available_weight, records: list[dict]) -> float:
    if pd.notna(available_weight) and float(available_weight) > 0:
        return float(available_weight)
    unique_weights = {record["metric"]: record["weight"] for record in records}
    total = sum(unique_weights.values())
    return total if total > 0 else 1.0


def _sv_signal_weight_summary(row: pd.Series) -> pd.Series:
    records = _sv_signal_records(row)
    denominator = _signal_weight_denominator(row.get("Доступность метрик СВ %"), records)
    red_weight = sum(record["weight"] for record in records if record["level"] == "hard") / denominator
    yellow_weight = sum(record["weight"] for record in records if record["level"] == "soft") / denominator
    issue_weight = sum(record["priority"] for record in records) / denominator
    return pd.Series(
        {
            "Вес красных флагов СВ %": round(red_weight, 4),
            "Вес желтых флагов СВ %": round(yellow_weight, 4),
            "Приоритетный вес проблем СВ %": round(issue_weight, 4),
        }
    )


def _status_from_row(row: pd.Series) -> str:
    available_weight = row.get("Доступность метрик СВ %")
    score = row.get("Индекс эффективности СВ %")

    if pd.isna(available_weight) or available_weight < SV_MIN_AVAILABLE_WEIGHT:
        return "Недостаточно данных"
    if pd.isna(score):
        return "Недостаточно данных"
    if any(pd.isna(row.get(column)) for column in SV_EFFECTIVENESS_CRITICAL_COLUMNS):
        return "Недостаточно данных"

    hard, soft = _sv_signal_lists(row)
    if hard or score < 0.80:
        return "Зона развития"
    if score >= SV_STATUS_STABLE_MIN_SCORE and not soft:
        return "Высокая готовность"
    return "Соответствует роли"


def _signal_text(row: pd.Series, level: str) -> str:
    hard, soft = _sv_signal_lists(row)
    values = hard if level == "hard" else soft
    return ", ".join(values) if values else pd.NA


def _signal_count(row: pd.Series, level: str) -> int:
    hard, soft = _sv_signal_lists(row)
    return len(hard if level == "hard" else soft)


def _status_reason_from_row(row: pd.Series):
    hard, _ = _sv_signal_lists(row)
    reasons = list(dict.fromkeys(hard))
    if reasons:
        return ", ".join(dict.fromkeys(reasons))
    return pd.NA


def _reserve_from_row(row: pd.Series) -> str:
    oed_class = str(row.get("Класс ОЭД") or "").strip().upper()
    score = row.get("Индекс эффективности СВ %")
    staffing = row.get("Кадровая устойчивость %")
    status = row.get("Статус эффективности СВ")

    if not oed_class:
        return "нет оценки ОЭД"
    if oed_class != "ТОП":
        return "вне резерва"
    if (
        pd.notna(score)
        and score >= RESERVE_CANDIDATE_MIN_SCORE
        and (pd.isna(staffing) or staffing >= RESERVE_CANDIDATE_MIN_STAFFING)
        and status == "Высокая готовность"
    ):
        return "кандидат"
    return "развитие на текущей роли"


def _staffing_score_from_row(row: pd.Series):
    active_me = row.get("Активных МЕ")
    open_me = row.get("Открытых вакансий МЕ")
    hired = row.get("Нанято")
    fired = row.get("Уволено")

    if pd.isna(active_me) and pd.isna(open_me) and pd.isna(hired) and pd.isna(fired):
        return pd.NA

    active_me = max(float(active_me), 0.0) if pd.notna(active_me) else 0.0
    open_me = max(float(open_me), 0.0) if pd.notna(open_me) else 0.0
    hired = max(float(hired), 0.0) if pd.notna(hired) else 0.0
    fired = max(float(fired), 0.0) if pd.notna(fired) else 0.0

    planned_team = active_me + open_me
    if planned_team <= 0:
        planned_team = max(hired + fired, 1.0)

    vacancy_share = open_me / planned_team
    turnover = fired / planned_team
    net_outflow_share = max(0.0, fired - hired) / planned_team
    penalty = vacancy_share * 0.70 + turnover * 0.20 + net_outflow_share * 0.10
    return max(0.0, min(1.0, 1.0 - penalty))


def _build_supervisor_directory(
    page2_sv: pd.DataFrame,
    dim_employees: pd.DataFrame,
    teams: pd.DataFrame,
) -> pd.DataFrame:
    base_source = page2_sv[
            [
                "MonthStart",
                "ID супервайзера",
                "Супервайзер",
                "Код СВ",
                "Регион BI",
                "Группа региона",
                "ID территориального менеджера",
                "Территориальный менеджер",
            ]
        ].copy()
    for column in [
        "Супервайзер",
        "Код СВ",
        "Регион BI",
        "Группа региона",
        "ID территориального менеджера",
        "Территориальный менеджер",
    ]:
        if column in base_source.columns:
            base_source[column] = base_source[column].replace("", pd.NA)

    base = (
        base_source
        .sort_values(["ID супервайзера", "MonthStart"])
        .groupby("ID супервайзера", dropna=False)
        .agg(
            **{
                "Супервайзер": ("Супервайзер", lambda s: s.dropna().iloc[-1] if not s.dropna().empty else pd.NA),
                "Код СВ": ("Код СВ", lambda s: s.dropna().iloc[-1] if not s.dropna().empty else pd.NA),
                "Регион BI": ("Регион BI", lambda s: s.dropna().iloc[-1] if not s.dropna().empty else pd.NA),
                "Группа региона": ("Группа региона", lambda s: s.dropna().iloc[-1] if not s.dropna().empty else pd.NA),
                "ID территориального менеджера": (
                    "ID территориального менеджера",
                    lambda s: s.dropna().iloc[-1] if not s.dropna().empty else pd.NA,
                ),
                "Территориальный менеджер": (
                    "Территориальный менеджер",
                    lambda s: s.dropna().iloc[-1] if not s.dropna().empty else pd.NA,
                ),
            }
        )
        .reset_index()
    )

    dim = dim_employees.rename(
        columns={
            "ID сотрудника": "ID супервайзера",
            "ФИО": "Супервайзер",
        }
    )
    if {"Активен", "Проект", "Должность"}.issubset(dim.columns):
        dim = dim[
            dim["Активен"].fillna(False).eq(True)
            & dim["Проект"].astype(str).eq("H&N")
            & dim["Должность"].astype(str).str.lower().str.contains("супервайзер", na=False)
        ].copy()
    elif "Должность" in dim.columns:
        dim = dim[dim["Должность"].astype(str).str.lower().str.contains("супервайзер", na=False)].copy()
    tm_dim = dim_employees.copy()
    if {"Активен", "Должность"}.issubset(tm_dim.columns):
        tm_dim = tm_dim[tm_dim["Активен"].fillna(False).eq(True) & tm_dim["Должность"].map(is_tm_role)].copy()
    else:
        tm_dim = tm_dim[tm_dim["Должность"].map(is_tm_role)].copy()
    valid_tm_ids = set(tm_dim["ID сотрудника"].dropna().astype(str).str.strip()) if "ID сотрудника" in tm_dim.columns else set()
    tm_lookup = (
        tm_dim[["ID сотрудника", "ФИО"]]
        .dropna(subset=["ID сотрудника"])
        .drop_duplicates("ID сотрудника")
        .rename(
            columns={
                "ID сотрудника": "ID территориального менеджера dim",
                "ФИО": "Территориальный менеджер dim",
            }
        )
    )
    dim_keep = [
        c
        for c in [
            "ID супервайзера",
            "Супервайзер",
            "Регион BI",
            "Группа региона",
            "ID руководителя",
            "ФИО руководителя",
            "Дата приёма",
            "Стаж (дней)",
            "Стаж (месяцев)",
        ]
        if c in dim.columns
    ]
    dim = dim[dim_keep].dropna(subset=["ID супервайзера"]).drop_duplicates("ID супервайзера")
    dim = dim.rename(
        columns={
            "ID руководителя": "ID территориального менеджера dim raw",
            "ФИО руководителя": "Территориальный менеджер dim raw",
            "Дата приёма": "Дата приема СВ",
            "Стаж (дней)": "Стаж СВ (дней)",
            "Стаж (месяцев)": "Стаж СВ (месяцев)",
        }
    )
    dim = dim.merge(
        tm_lookup,
        left_on="ID территориального менеджера dim raw",
        right_on="ID территориального менеджера dim",
        how="left",
    )
    if "Территориальный менеджер dim" in dim.columns:
        dim["Территориальный менеджер dim"] = dim["Территориальный менеджер dim"].combine_first(
            dim.get("Территориальный менеджер dim raw")
        )

    teams_work = teams.replace("", pd.NA).copy()
    tm_ids = teams_work["ID территориального менеджера"].astype("string").str.strip()
    invalid_tm = tm_ids.notna() & ~tm_ids.isin(valid_tm_ids) & tm_ids.ne(NO_TM_ID)
    teams_work.loc[invalid_tm, "ID территориального менеджера"] = pd.NA
    teams_work.loc[invalid_tm, "Территориальный менеджер"] = pd.NA
    teams_dir = (
        teams_work
        .dropna(subset=["ID супервайзера"])
        .groupby("ID супервайзера", dropna=False)
        .agg(
            **{
                "ID территориального менеджера": ("ID территориального менеджера", "first"),
                "Территориальный менеджер": ("Территориальный менеджер", "first"),
                "Регион BI teams": ("Регион BI", lambda s: s.mode().iloc[0] if not s.mode().empty else s.dropna().iloc[0] if s.dropna().any() else pd.NA),
                "Группа региона teams": ("Группа региона", lambda s: s.mode().iloc[0] if not s.mode().empty else s.dropna().iloc[0] if s.dropna().any() else pd.NA),
            }
        )
        .reset_index()
    )

    directory = base.merge(dim, on="ID супервайзера", how="outer", suffixes=("", "_dim"))
    directory = directory.merge(teams_dir, on="ID супервайзера", how="left", suffixes=("", "_teams"))
    directory["Супервайзер"] = directory.get("Супервайзер_dim").combine_first(directory["Супервайзер"])
    directory["Регион BI"] = (
        directory.get("Регион BI teams")
        .combine_first(directory.get("Регион BI_dim"))
    )
    directory["Группа региона"] = (
        directory.get("Группа региона teams")
        .combine_first(directory.get("Группа региона_dim"))
    )
    directory["ID территориального менеджера"] = directory.get("ID территориального менеджера_teams").combine_first(
        directory.get("ID территориального менеджера dim raw")
    )
    directory["Территориальный менеджер"] = directory.get("Территориальный менеджер_teams").combine_first(
        directory.get("Территориальный менеджер dim")
    )
    directory["ID территориального менеджера"] = directory["ID территориального менеджера"].replace("", pd.NA)
    directory["Территориальный менеджер"] = directory["Территориальный менеджер"].replace("", pd.NA)
    tm_ids = directory["ID территориального менеджера"].astype("string").str.strip()
    invalid_tm = tm_ids.notna() & ~tm_ids.isin(valid_tm_ids)
    directory.loc[invalid_tm, "ID территориального менеджера"] = pd.NA
    directory.loc[invalid_tm, "Территориальный менеджер"] = pd.NA
    directory = normalize_confirmed_tm(directory)
    directory["Код СВ"] = directory["Код СВ"].combine_first(_extract_sv_code(directory["ID супервайзера"]))
    directory["СВ / Объект"] = directory["Код СВ"].combine_first(directory["Супервайзер"]).fillna("СВ")
    directory["Имя норм"] = directory["Супервайзер"].map(_normalize_name)
    valid_ids = set(dim["ID супервайзера"].dropna().astype(str)) | set(teams_dir["ID супервайзера"].dropna().astype(str))
    directory = directory[directory["ID супервайзера"].astype(str).isin(valid_ids)].copy()
    directory = directory.drop(
        columns=[
            c
            for c in directory.columns
            if c.endswith("_dim") or c.endswith(" teams") or c.endswith("_teams") or c.endswith(" dim raw")
        ],
        errors="ignore",
    )
    return directory.drop_duplicates("ID супервайзера")


def _build_team_size_monthly(teams: pd.DataFrame, month_source: pd.DataFrame) -> pd.DataFrame:
    months = month_source[["MonthStart", "YearMonth"]].drop_duplicates().copy()
    current_team = (
        teams.replace("", pd.NA)
        .dropna(subset=["ID супервайзера", "ID мерчендайзера"])
        .groupby("ID супервайзера", dropna=False)["ID мерчендайзера"]
        .nunique()
        .reset_index(name="Размер команды")
    )
    if current_team.empty or months.empty:
        return pd.DataFrame(columns=["MonthStart", "YearMonth", "ID супервайзера", "Размер команды"])
    return months.merge(current_team, how="cross")


def _max_numeric(series: pd.Series):
    numeric = pd.to_numeric(series, errors="coerce")
    return numeric.max() if numeric.notna().any() else pd.NA


def _add_supervisor_monthly_rank(monthly: pd.DataFrame) -> pd.DataFrame:
    if monthly.empty:
        result = monthly.copy()
        result["Ранг СВ"] = pd.Series(dtype="Int64")
        return result

    pieces = []
    for _, month_df in monthly.groupby("MonthStart", sort=True, dropna=False):
        ranked = month_df.sort_values(
            [
                "Индекс эффективности СВ %",
                "KPI месяца %",
                "ОКК команды %",
                "Обучение команды %",
                "Стабильность команды %",
                "ID супервайзера",
            ],
            ascending=[False, False, False, False, False, True],
            na_position="last",
            kind="mergesort",
        ).copy()
        ranked["Ранг СВ"] = pd.array(range(1, len(ranked) + 1), dtype="Int64")
        pieces.append(ranked)

    return pd.concat(pieces, ignore_index=True)


def _refresh_sv_effectiveness(monthly: pd.DataFrame) -> pd.DataFrame:
    result = monthly.copy()
    result["KPI месяца %"] = _normalize_pct(result["KPI месяца %"])
    result["Доступность метрик СВ %"] = result.apply(_available_weight_from_row, axis=1)
    result["Индекс эффективности СВ %"] = pd.to_numeric(
        result.apply(_weighted_score_from_row, axis=1),
        errors="coerce",
    ).round(4)
    result["Зона эффективности СВ"] = result["Индекс эффективности СВ %"].map(_score_zone)
    result["Красных сигналов СВ"] = result.apply(lambda row: _signal_count(row, "hard"), axis=1)
    result["Мягких сигналов СВ"] = result.apply(lambda row: _signal_count(row, "soft"), axis=1)
    result["Красные сигналы СВ"] = result.apply(lambda row: _signal_text(row, "hard"), axis=1)
    result["Мягкие сигналы СВ"] = result.apply(lambda row: _signal_text(row, "soft"), axis=1)
    result[
        [
            "Вес красных флагов СВ %",
            "Вес желтых флагов СВ %",
            "Приоритетный вес проблем СВ %",
        ]
    ] = result.apply(_sv_signal_weight_summary, axis=1)
    result["Статус эффективности СВ"] = result.apply(_status_from_row, axis=1)
    result["Зона эффективности СВ"] = result["Статус эффективности СВ"]
    result["Причина статуса СВ"] = result.apply(_status_reason_from_row, axis=1)
    result["Операционная эффективность %"] = result["Индекс эффективности СВ %"]
    result["Score месяца"] = result["Индекс эффективности СВ %"]
    result["Балл эффективности %"] = result["Индекс эффективности СВ %"]
    result["Балл эффективности"] = np.floor(result["Балл эффективности %"] * 100 + 1e-9)
    result["Score резерва"] = result["Балл эффективности %"]
    result["Статус резерва СВ"] = result.apply(_reserve_from_row, axis=1)
    result["Резерв"] = result["Статус резерва СВ"]
    result["Статус"] = result["Статус эффективности СВ"]
    return _add_supervisor_monthly_rank(result)


def _sum_numeric(series: pd.Series):
    numeric = pd.to_numeric(series, errors="coerce")
    return numeric.sum() if numeric.notna().any() else pd.NA


def _collapse_monthly_sv_rows(monthly: pd.DataFrame) -> pd.DataFrame:
    keys = ["MonthStart", "YearMonth", "ID супервайзера"]
    if not set(keys).issubset(monthly.columns):
        return monthly

    work = monthly.drop_duplicates().sort_values(keys).copy()
    mean_columns = {
        "KPI месяца %",
        "OSA %",
        "PICOS %",
        "ОКК команды %",
        "Обучение команды %",
        "Фрод %",
        "Риск ухода региона %",
    }
    max_columns = {"Фрод команды"}

    aggregations = {}
    for column in work.columns:
        if column in keys:
            continue
        if column in mean_columns:
            aggregations[column] = (column, _mean_numeric)
        elif column in max_columns:
            aggregations[column] = (column, _max_numeric)
        else:
            aggregations[column] = (column, _first_notna)

    return (
        work.groupby(keys, dropna=False)
        .agg(**aggregations)
        .reset_index()
    )


def _build_oed_quarterly(
    fact_oed: pd.DataFrame,
    directory: pd.DataFrame,
) -> pd.DataFrame:
    sv = fact_oed[fact_oed["Роль"].astype(str).str.contains("SV", na=False)].copy()
    sv["ID супервайзера"] = sv["ID сотрудника"]
    sv["KPI ОЭД %"] = _normalize_valid_pct(sv["Балл KPI"])
    sv["Команда ОЭД %"] = _normalize_valid_pct(sv["Команда"])
    sv["Класс ОЭД"] = sv["Класс"]
    sv["Аттестация %"] = _normalize_valid_pct(sv["Аттестация"])
    sv["Знание продукта %"] = _normalize_valid_pct(sv["Продукт"])
    sv["Управление личное %"] = _normalize_valid_pct(sv["Управление"])
    sv["Управление %"] = sv["Управление личное %"].combine_first(sv["Команда ОЭД %"])
    sv["Рейтинг ОЭД"] = pd.to_numeric(sv["Рейтинг"], errors="coerce")
    sv["Имя норм"] = sv["ID супервайзера"].map(dict(zip(directory["ID супервайзера"], directory["Имя норм"])))
    sv["Супервайзер"] = sv["ID супервайзера"].map(dict(zip(directory["ID супервайзера"], directory["Супервайзер"])))
    sv["Код СВ"] = sv["ID супервайзера"].map(dict(zip(directory["ID супервайзера"], directory["Код СВ"])))
    sv["Регион BI"] = sv["ID супервайзера"].map(dict(zip(directory["ID супервайзера"], directory["Регион BI"])))
    sv["Группа региона"] = sv["ID супервайзера"].map(dict(zip(directory["ID супервайзера"], directory["Группа региона"])))

    sv["_role_priority"] = sv["Роль"].astype(str).str.upper().map({"SV": 0, "SV-1": 1}).fillna(2)
    sv = (
        sv.sort_values(
            ["ID супервайзера", "QuarterStart", "_role_priority", "Рейтинг ОЭД"],
            ascending=[True, True, True, False],
            na_position="last",
        )
        .drop_duplicates(["ID супервайзера", "QuarterStart"], keep="first")
        .drop(columns=["_role_priority"], errors="ignore")
    )

    return sv[
        [
            "QuarterStart",
            "YearQuarter",
            "QuarterLabel",
            "ID супервайзера",
            "Супервайзер",
            "Код СВ",
            "Имя норм",
            "Регион BI",
            "Группа региона",
            "KPI ОЭД %",
            "Команда ОЭД %",
            "Класс ОЭД",
            "Аттестация %",
            "Знание продукта %",
            "Управление личное %",
            "Управление %",
            "Рейтинг ОЭД",
        ]
    ].copy()


def _merge_asof_supervisor(
    monthly: pd.DataFrame,
    quarterly: pd.DataFrame,
) -> pd.DataFrame:
    attach_cols = [
        "KPI ОЭД %",
        "Команда ОЭД %",
        "Класс ОЭД",
        "Аттестация %",
        "Знание продукта %",
        "Управление личное %",
        "Управление %",
        "Рейтинг ОЭД",
    ]

    pieces: list[pd.DataFrame] = []
    for sv_id, base in monthly.groupby("ID супервайзера", dropna=False):
        base_sorted = base.sort_values("MonthStart").copy()
        q = quarterly[quarterly["ID супервайзера"] == sv_id].sort_values("QuarterStart").copy()
        if q.empty:
            for col in ["QuarterStart", "YearQuarter", "QuarterLabel"] + attach_cols:
                base_sorted[col] = np.nan if col != "QuarterLabel" else pd.NA
        else:
            q_merge = q.rename(
                columns={
                    "QuarterStart": "QuarterStart_match",
                    "YearQuarter": "YearQuarter_match",
                    "QuarterLabel": "QuarterLabel_match",
                }
            )
            merged = pd.merge_asof(
                base_sorted,
                q_merge[["QuarterStart_match", "YearQuarter_match", "QuarterLabel_match"] + attach_cols],
                left_on="MonthStart",
                right_on="QuarterStart_match",
                direction="backward",
            )
            base_sorted["QuarterStart"] = merged["QuarterStart_match"].values
            base_sorted["YearQuarter"] = merged["YearQuarter_match"].values
            base_sorted["QuarterLabel"] = merged["QuarterLabel_match"].values
            for col in attach_cols:
                base_sorted[col] = merged[col].values
        pieces.append(base_sorted)

    result = pd.concat(pieces, ignore_index=True)

    missing = result["KPI ОЭД %"].isna()
    if missing.any():
        quarterly_name = quarterly.dropna(subset=["Имя норм"]).copy()
        fallback_pieces: list[pd.DataFrame] = []
        for name_norm, base in result[missing].groupby("Имя норм", dropna=False):
            base_sorted = base.sort_values("MonthStart").copy()
            base_sorted = base_sorted.drop(
                columns=["QuarterStart", "YearQuarter", "QuarterLabel"] + attach_cols,
                errors="ignore",
            )
            q = quarterly_name[quarterly_name["Имя норм"] == name_norm].sort_values("QuarterStart").copy()
            if q.empty:
                fallback_pieces.append(base_sorted)
                continue
            q_merge = q.rename(
                columns={
                    "QuarterStart": "QuarterStart_match",
                    "YearQuarter": "YearQuarter_match",
                    "QuarterLabel": "QuarterLabel_match",
                }
            )
            merged = pd.merge_asof(
                base_sorted,
                q_merge[["QuarterStart_match", "YearQuarter_match", "QuarterLabel_match"] + attach_cols],
                left_on="MonthStart",
                right_on="QuarterStart_match",
                direction="backward",
            )
            base_sorted["QuarterStart"] = merged["QuarterStart_match"].values
            base_sorted["YearQuarter"] = merged["YearQuarter_match"].values
            base_sorted["QuarterLabel"] = merged["QuarterLabel_match"].values
            for col in attach_cols:
                base_sorted[col] = merged[col].values
            fallback_pieces.append(base_sorted)

        kept = result[~missing].copy()
        if fallback_pieces:
            result = pd.concat([kept, *fallback_pieces], ignore_index=True)

    return result


def _build_client_attestation_quarterly(attestations: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "ID супервайзера",
        "QuarterStart аттестации клиента",
        "YearQuarter аттестации клиента",
        "QuarterLabel аттестации клиента",
        "Аттестация клиента %",
        "Статус аттестации клиента",
        "Дата аттестации клиента",
    ]
    if attestations is None or attestations.empty:
        return pd.DataFrame(columns=columns)

    required = {"ID сотрудника", "QuarterStart", "Уровень сотрудника"}
    if not required.issubset(attestations.columns):
        return pd.DataFrame(columns=columns)

    work = attestations.copy().replace("", pd.NA)
    work = work[
        work["Уровень сотрудника"].eq("СВ")
        & work["ID сотрудника"].notna()
        & pd.to_datetime(work["QuarterStart"], errors="coerce").notna()
    ].copy()
    if work.empty:
        return pd.DataFrame(columns=columns)

    work["ID супервайзера"] = work["ID сотрудника"].astype(str).str.strip()
    work["QuarterStart аттестации клиента"] = pd.to_datetime(work["QuarterStart"], errors="coerce")
    work["YearQuarter аттестации клиента"] = pd.to_numeric(work.get("YearQuarter"), errors="coerce").astype("Int64")
    work["QuarterLabel аттестации клиента"] = work.get("QuarterLabel", pd.Series(pd.NA, index=work.index))
    work["Аттестация клиента %"] = _normalize_pct(work.get("Аттестация клиента %", pd.Series(np.nan, index=work.index)))
    work["Дата аттестации клиента"] = pd.to_datetime(
        work.get("Дата завершения", pd.Series(pd.NaT, index=work.index)),
        errors="coerce",
    )
    for date_column in ["Дата начала", "Дата заявки"]:
        if date_column in work.columns:
            fallback_date = pd.to_datetime(work[date_column], errors="coerce")
            work["Дата аттестации клиента"] = work["Дата аттестации клиента"].combine_first(fallback_date)

    work = work.sort_values(
        ["ID супервайзера", "QuarterStart аттестации клиента", "Дата аттестации клиента"],
        na_position="first",
    )
    result = (
        work.groupby(["ID супервайзера", "QuarterStart аттестации клиента"], dropna=False)
        .agg(
            **{
                "YearQuarter аттестации клиента": ("YearQuarter аттестации клиента", _first_notna),
                "QuarterLabel аттестации клиента": ("QuarterLabel аттестации клиента", _first_notna),
                "Аттестация клиента %": ("Аттестация клиента %", _max_numeric),
                "Статус аттестации клиента": ("Статус аттестации клиента", _first_notna),
                "Дата аттестации клиента": ("Дата аттестации клиента", "max"),
            }
        )
        .reset_index()
    )
    return result[[c for c in columns if c in result.columns]].copy()


def _build_client_attestation_wide(attestation_quarterly: pd.DataFrame) -> pd.DataFrame:
    columns = ["ID супервайзера"] + list(CLIENT_ATTESTATION_QUARTERS.values())
    if attestation_quarterly is None or attestation_quarterly.empty:
        return pd.DataFrame(columns=columns)

    work = attestation_quarterly.copy()
    work["YearQuarter аттестации клиента"] = pd.to_numeric(
        work["YearQuarter аттестации клиента"],
        errors="coerce",
    )
    quarter_map = CLIENT_ATTESTATION_QUARTERS
    work = work[work["YearQuarter аттестации клиента"].isin(quarter_map)].copy()
    if work.empty:
        return pd.DataFrame(columns=columns)

    work["Аттестация квартал"] = work["YearQuarter аттестации клиента"].map(quarter_map)
    wide = (
        work.pivot_table(
            index="ID супервайзера",
            columns="Аттестация квартал",
            values="Аттестация клиента %",
            aggfunc="max",
        )
        .reset_index()
        .rename_axis(columns=None)
    )
    for column in columns:
        if column not in wide.columns:
            wide[column] = pd.NA
    return wide[columns].copy()


def _merge_asof_client_attestation(
    monthly: pd.DataFrame,
    attestation_quarterly: pd.DataFrame,
) -> pd.DataFrame:
    attach_cols = [
        "YearQuarter аттестации клиента",
        "QuarterLabel аттестации клиента",
        "Аттестация клиента %",
        "Статус аттестации клиента",
        "Дата аттестации клиента",
    ]
    if attestation_quarterly is None or attestation_quarterly.empty:
        result = monthly.copy()
        result["QuarterStart аттестации клиента"] = pd.NaT
        for col in attach_cols:
            result[col] = pd.NA
        return result

    pieces: list[pd.DataFrame] = []
    for sv_id, base in monthly.groupby("ID супервайзера", dropna=False):
        base_sorted = base.sort_values("MonthStart").copy()
        q = attestation_quarterly[
            attestation_quarterly["ID супервайзера"].astype(str) == str(sv_id)
        ].sort_values("QuarterStart аттестации клиента").copy()
        if q.empty:
            base_sorted["QuarterStart аттестации клиента"] = pd.NaT
            for col in attach_cols:
                base_sorted[col] = pd.NA
        else:
            merged = pd.merge_asof(
                base_sorted,
                q[["QuarterStart аттестации клиента"] + attach_cols],
                left_on="MonthStart",
                right_on="QuarterStart аттестации клиента",
                direction="backward",
            )
            base_sorted["QuarterStart аттестации клиента"] = merged["QuarterStart аттестации клиента"].values
            for col in attach_cols:
                base_sorted[col] = merged[col].values
        pieces.append(base_sorted)

    return pd.concat(pieces, ignore_index=True)


def _build_monthly_snapshot(
    page2_sv: pd.DataFrame,
    fact_oed: pd.DataFrame,
    dim_employees: pd.DataFrame,
    teams: pd.DataFrame,
    attestations: pd.DataFrame,
    out_dir: Path,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    directory = _build_supervisor_directory(page2_sv, dim_employees, teams)
    team_size = _build_team_size_monthly(teams, page2_sv)
    oed_quarterly = _build_oed_quarterly(fact_oed, directory)
    client_attestation_quarterly = _build_client_attestation_quarterly(attestations)
    client_attestation_wide = _build_client_attestation_wide(client_attestation_quarterly)

    monthly = page2_sv.copy().rename(
        columns={
            "ОКК %": "ОКК команды %",
            "Обучение %": "Обучение команды %",
            "Фрод кол-во": "Фрод команды",
            "Статус команды": "Статус команды ETL",
            "KPI проекта %": "KPI месяца %",
        }
    )
    for column in ["Регион BI", "ID территориального менеджера", "Территориальный менеджер", "Супервайзер", "Код СВ"]:
        if column in monthly.columns:
            monthly[column] = monthly[column].replace("", pd.NA)
    monthly = _collapse_monthly_sv_rows(monthly)
    monthly["Имя норм"] = monthly["Супервайзер"].map(_normalize_name)
    monthly = monthly.merge(
        team_size,
        on=["MonthStart", "YearMonth", "ID супервайзера"],
        how="left",
    )
    monthly = _merge_asof_supervisor(monthly, oed_quarterly)
    oed_presence_columns = [
        "KPI ОЭД %",
        "Команда ОЭД %",
        "Аттестация %",
        "Знание продукта %",
        "Управление личное %",
        "Управление %",
        "Рейтинг ОЭД",
    ]
    monthly["Есть ОЭД СВ"] = monthly[[c for c in oed_presence_columns if c in monthly.columns]].notna().any(axis=1)
    monthly["Аттестация ОЭД %"] = monthly.get("Аттестация %")
    monthly = _merge_asof_client_attestation(monthly, client_attestation_quarterly)
    if not client_attestation_wide.empty:
        monthly = monthly.merge(client_attestation_wide, on="ID супервайзера", how="left")
    else:
        for col in CLIENT_ATTESTATION_QUARTERS.values():
            monthly[col] = pd.NA
    monthly["Аттестация %"] = monthly["Аттестация клиента %"]
    monthly["Источник аттестации"] = np.where(
        monthly["Аттестация клиента %"].notna(),
        "Клиент",
        "Нет результата",
    )
    monthly.loc[~monthly["Есть ОЭД СВ"], "Класс ОЭД"] = "Новичок"
    monthly = monthly.merge(
        directory[
            [
                "ID супервайзера",
                "Супервайзер",
                "Дата приема СВ",
                "Стаж СВ (дней)",
                "Стаж СВ (месяцев)",
                "Код СВ",
                "СВ / Объект",
                "Регион BI",
                "Группа региона",
                "ID территориального менеджера",
                "Территориальный менеджер",
            ]
        ],
        on="ID супервайзера",
        how="left",
        suffixes=("", "_dir"),
    )
    active_sv_ids = set(directory["ID супервайзера"].dropna().astype(str))
    monthly = monthly[monthly["ID супервайзера"].astype(str).isin(active_sv_ids)].copy()
    if "Супервайзер_dir" in monthly.columns:
        monthly["Супервайзер"] = monthly["Супервайзер_dir"].combine_first(monthly["Супервайзер"])
    if "Код СВ_dir" in monthly.columns:
        monthly["Код СВ"] = monthly["Код СВ_dir"].combine_first(monthly["Код СВ"])
    if "СВ / Объект_dir" in monthly.columns:
        monthly["СВ / Объект"] = monthly["СВ / Объект_dir"].combine_first(monthly["СВ / Объект"])
    if "Регион BI_dir" in monthly.columns:
        monthly["Регион BI"] = monthly["Регион BI_dir"].combine_first(monthly["Регион BI"])
    if "Группа региона_dir" in monthly.columns:
        monthly["Группа региона"] = monthly.get("Группа региона_dir")
    if "ID территориального менеджера_dir" in monthly.columns:
        monthly["ID территориального менеджера"] = monthly["ID территориального менеджера_dir"].combine_first(
            monthly.get("ID территориального менеджера")
        )
    if "Территориальный менеджер_dir" in monthly.columns:
        monthly["Территориальный менеджер"] = monthly["Территориальный менеджер_dir"].combine_first(
            monthly.get("Территориальный менеджер")
        )
    monthly = monthly.drop(columns=[c for c in monthly.columns if c.endswith("_dir")], errors="ignore")
    monthly = normalize_confirmed_tm(monthly)
    monthly["KPI месяца %"] = _normalize_pct(monthly["KPI месяца %"])
    monthly["ОКК команды %"] = _normalize_pct(monthly["ОКК команды %"])
    monthly["Обучение команды %"] = _normalize_pct(monthly["Обучение команды %"])
    monthly["Фрод %"] = _normalize_pct(monthly["Фрод %"])
    monthly = _attach_staffing_metrics(monthly, out_dir)
    monthly["Стабильность команды %"] = monthly["Кадровая устойчивость %"]
    monthly["Антифрод %"] = 1 - monthly["Фрод %"]
    monthly["Месяц ОЭД"] = _effective_oed_month(monthly["QuarterStart"])
    monthly["Месяцев после ОЭД"] = _months_after(monthly["MonthStart"], monthly["Месяц ОЭД"])
    monthly["Вес ОЭД"] = (
        OED_WEIGHT_START - monthly["Месяцев после ОЭД"].fillna(99).clip(lower=0) * OED_WEIGHT_STEP
    ).clip(lower=OED_WEIGHT_FLOOR, upper=OED_WEIGHT_START)
    monthly.loc[monthly["KPI ОЭД %"].isna(), "Вес ОЭД"] = pd.NA
    monthly["Доступность метрик СВ %"] = monthly.apply(_available_weight_from_row, axis=1)
    monthly["Индекс эффективности СВ %"] = monthly.apply(_weighted_score_from_row, axis=1)
    monthly["Индекс эффективности СВ %"] = pd.to_numeric(
        monthly["Индекс эффективности СВ %"],
        errors="coerce",
    ).round(4)
    monthly["Зона эффективности СВ"] = monthly["Индекс эффективности СВ %"].map(_score_zone)
    monthly["Красных сигналов СВ"] = monthly.apply(lambda row: _signal_count(row, "hard"), axis=1)
    monthly["Мягких сигналов СВ"] = monthly.apply(lambda row: _signal_count(row, "soft"), axis=1)
    monthly["Красные сигналы СВ"] = monthly.apply(lambda row: _signal_text(row, "hard"), axis=1)
    monthly["Мягкие сигналы СВ"] = monthly.apply(lambda row: _signal_text(row, "soft"), axis=1)
    monthly[
        [
            "Вес красных флагов СВ %",
            "Вес желтых флагов СВ %",
            "Приоритетный вес проблем СВ %",
        ]
    ] = monthly.apply(_sv_signal_weight_summary, axis=1)
    monthly["Статус эффективности СВ"] = monthly.apply(_status_from_row, axis=1)
    monthly["Зона эффективности СВ"] = monthly["Статус эффективности СВ"]
    monthly["Причина статуса СВ"] = monthly.apply(_status_reason_from_row, axis=1)
    monthly["Продукт ОЭД %"] = monthly.get("Знание продукта %", pd.Series(pd.NA, index=monthly.index))
    monthly["Управление ОЭД %"] = monthly.get("Управление личное %", pd.Series(pd.NA, index=monthly.index))
    monthly["Доступность личных метрик %"] = monthly.apply(_personal_available_weight_from_row, axis=1)
    monthly["Личная эффективность СВ %"] = monthly.apply(_personal_score_from_row, axis=1)
    monthly["Личная эффективность СВ %"] = pd.to_numeric(
        monthly["Личная эффективность СВ %"],
        errors="coerce",
    ).round(4)
    monthly["Балл личной эффективности"] = np.floor(
        monthly["Личная эффективность СВ %"] * 100 + 1e-9
    )
    monthly["Статус личной эффективности"] = monthly.apply(_personal_status_from_row, axis=1)
    monthly["Причина личной эффективности"] = monthly.apply(_personal_reason_from_row, axis=1)
    monthly["Операционная эффективность %"] = monthly["Индекс эффективности СВ %"]
    monthly["Score месяца"] = monthly["Индекс эффективности СВ %"]
    monthly["Личная оценка %"] = monthly["Личная эффективность СВ %"]
    monthly["Балл эффективности %"] = monthly["Индекс эффективности СВ %"]
    monthly["Балл эффективности"] = np.floor(monthly["Балл эффективности %"] * 100 + 1e-9)
    monthly["Score резерва"] = monthly["Балл эффективности %"]
    monthly["Статус резерва СВ"] = monthly.apply(_reserve_from_row, axis=1)
    monthly["Резерв"] = monthly["Статус резерва СВ"]
    monthly["Статус"] = monthly["Статус эффективности СВ"]
    monthly = _add_supervisor_monthly_rank(monthly)

    columns = [
        "MonthStart",
        "YearMonth",
        "Ранг СВ",
        "QuarterStart",
        "YearQuarter",
        "QuarterLabel",
        "Регион BI",
        "Группа региона",
        "ID супервайзера",
        "Супервайзер",
        "Дата приема СВ",
        "Стаж СВ (дней)",
        "Стаж СВ (месяцев)",
        "ID территориального менеджера",
        "Территориальный менеджер",
        "Код СВ",
        "СВ / Объект",
        "Размер команды",
        "Плановая команда",
        "Активных МЕ",
        "Открытых вакансий",
        "Открытых вакансий МЕ",
        "Открытых вакансий СВ",
        "Приостановленных вакансий",
        "Нанято",
        "Уволено",
        "Чистый отток",
        "Баланс персонала",
        "Доля вакансий от плановой команды %",
        "Текучесть команды %",
        "Стабильность команды %",
        "KPI месяца %",
        "ОКК команды %",
        "Обучение команды %",
        "Фрод команды",
        "Фрод %",
        "Доступность метрик СВ %",
        "Индекс эффективности СВ %",
        "Балл эффективности %",
        "Балл эффективности",
        "Красных сигналов СВ",
        "Мягких сигналов СВ",
        "Красные сигналы СВ",
        "Мягкие сигналы СВ",
        "Вес красных флагов СВ %",
        "Вес желтых флагов СВ %",
        "Приоритетный вес проблем СВ %",
        "Статус эффективности СВ",
        "Причина статуса СВ",
        "Класс ОЭД",
        "Есть ОЭД СВ",
        "KPI ОЭД %",
        "Аттестация ОЭД %",
        "Продукт ОЭД %",
        "Управление ОЭД %",
        "Аттестация клиента %",
        *CLIENT_ATTESTATION_QUARTERS.values(),
        "Статус аттестации клиента",
        "Дата аттестации клиента",
        "QuarterLabel аттестации клиента",
        "Источник аттестации",
        "Доступность личных метрик %",
        "Личная эффективность СВ %",
        "Балл личной эффективности",
        "Статус личной эффективности",
        "Причина личной эффективности",
        "Статус резерва СВ",
        "Резерв",
    ]
    monthly = monthly[[c for c in columns if c in monthly.columns]].copy()

    numeric_columns = [
        "YearMonth",
        "Ранг СВ",
        "YearQuarter",
        "KPI ОЭД %",
        "Стаж СВ (дней)",
        "Стаж СВ (месяцев)",
        "Размер команды",
        "Плановая команда",
        "Активных МЕ",
        "Открытых вакансий",
        "Открытых вакансий МЕ",
        "Открытых вакансий СВ",
        "Приостановленных вакансий",
        "Нанято",
        "Уволено",
        "Чистый отток",
        "Баланс персонала",
        "Доля вакансий от плановой команды %",
        "Текучесть команды %",
        "Стабильность команды %",
        "KPI месяца %",
        "ОКК команды %",
        "Обучение команды %",
        "Фрод команды",
        "Фрод %",
        "Доступность метрик СВ %",
        "Индекс эффективности СВ %",
        "Балл эффективности %",
        "Балл эффективности",
        "Красных сигналов СВ",
        "Мягких сигналов СВ",
        "Вес красных флагов СВ %",
        "Вес желтых флагов СВ %",
        "Приоритетный вес проблем СВ %",
        "Аттестация ОЭД %",
        "Продукт ОЭД %",
        "Управление ОЭД %",
        "Аттестация клиента %",
        *CLIENT_ATTESTATION_QUARTERS.values(),
        "Доступность личных метрик %",
        "Личная эффективность СВ %",
        "Балл личной эффективности",
    ]
    for column in numeric_columns:
        if column in monthly.columns:
            monthly[column] = pd.to_numeric(monthly[column], errors="coerce")

    d_supervisor = (
        monthly[
            [
                "ID супервайзера",
                "Супервайзер",
                "Дата приема СВ",
                "Стаж СВ (дней)",
                "Стаж СВ (месяцев)",
                "Код СВ",
                "СВ / Объект",
                "Регион BI",
                "Группа региона",
            ]
        ]
        .dropna(subset=["ID супервайзера"])
        .sort_values(["Регион BI", "Супервайзер", "Код СВ"])
        .drop_duplicates("ID супервайзера", keep="first")
    )

    quarterly_out = oed_quarterly.merge(
        d_supervisor[["ID супервайзера", "СВ / Объект"]],
        on="ID супервайзера",
        how="inner",
    )
    quarterly_numeric = [
        "YearQuarter",
        "KPI ОЭД %",
        "Команда ОЭД %",
        "Аттестация %",
        "Знание продукта %",
        "Управление личное %",
        "Управление %",
        "Рейтинг ОЭД",
    ]
    for column in quarterly_numeric:
        if column in quarterly_out.columns:
            quarterly_out[column] = pd.to_numeric(quarterly_out[column], errors="coerce")

    return monthly, quarterly_out


def _build_legend() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Порядок": 1,
                "Категория": "Формула",
                "Описание": "Балл эффективности СВ: клиентский KPI 35% (PICOS либо OSA + TOP16 по правилам ТТ), ОКК команды 15%, обучение команды 15%, фрод 15%, стабильность команды 15%, текучесть 5%; клиентская аттестация и личная эффективность в этом балле не участвуют",
            },
            {
                "Порядок": 2,
                "Категория": "Личная эффективность",
                "Описание": "Личная эффективность: аттестация клиента 40%, аттестация ОЭД 20%, продукт ОЭД 20%, управление ОЭД 20%; KPI ОЭД пока справочно и в балл не входит",
            },
            {
                "Порядок": 3,
                "Категория": "Высокая готовность",
                "Описание": "балл от 90%, нет желтых и красных управленческих флагов",
            },
            {
                "Порядок": 4,
                "Категория": "Соответствует роли",
                "Описание": "данных достаточно, красных флагов нет, но есть желтая зона или балл ниже 90%",
            },
            {
                "Порядок": 5,
                "Категория": "Зона развития",
                "Описание": "итоговый балл ниже 80% или есть хотя бы один красный управленческий флаг",
            },
            {
                "Порядок": 6,
                "Категория": "Причины статуса",
                "Описание": "выводятся только красные управленческие метрики; если красных флагов нет, поле пустое",
            },
            {
                "Порядок": 7,
                "Категория": "Резерв СВ",
                "Описание": "кандидатом становится только класс ОЭД ТОП при статусе Высокая готовность и стабильности команды от 80%",
            },
            {
                "Порядок": 8,
                "Категория": "Личные статусы",
                "Описание": "высокая личная готовность: балл от 95%, все личные метрики зеленые и класс ОЭД ТОП; соответствует роли: балл от 90% без красных флагов; зона развития: балл ниже 90% или красный флаг; новичок: ОЭД еще не проходил",
            },
        ]
    )


def _build_me_flow_by_sv(out_dir: Path) -> pd.DataFrame:
    path = out_dir / "fact_hr_registry.parquet"
    if not path.exists():
        return pd.DataFrame(columns=["MonthStart", "YearMonth", "ID супервайзера"])

    hr = pd.read_parquet(path).replace("", pd.NA)
    if hr.empty or "ID супервайзера" not in hr.columns:
        return pd.DataFrame(columns=["MonthStart", "YearMonth", "ID супервайзера"])

    role_text = (
        hr.get("Должность", pd.Series(index=hr.index, dtype="object"))
        .astype(str)
        .str.lower()
    )
    me = hr[role_text.str.contains("мерч", na=False)].copy()
    me = me[me["ID супервайзера"].notna()].copy()
    if me.empty:
        return pd.DataFrame(columns=["MonthStart", "YearMonth", "ID супервайзера"])

    hires = me.dropna(subset=["MonthStart найм"]).copy()
    hires["MonthStart"] = pd.to_datetime(hires["MonthStart найм"], errors="coerce")
    hires["YearMonth"] = pd.to_numeric(hires["YearMonth найм"], errors="coerce")
    hires["Нанято МЕ"] = 1
    hires["Уволено МЕ"] = 0

    fired_state = me.get("Состояние", pd.Series(index=me.index, dtype="object")).astype(str)
    fires = me[
        fired_state.str.contains("увольнение", case=False, na=False)
        & me["MonthStart увольнение"].notna()
    ].copy()
    fires["MonthStart"] = pd.to_datetime(fires["MonthStart увольнение"], errors="coerce")
    fires["YearMonth"] = pd.to_numeric(fires["YearMonth увольнение"], errors="coerce")
    fires["Нанято МЕ"] = 0
    fires["Уволено МЕ"] = 1

    flow = pd.concat(
        [
            hires[["MonthStart", "YearMonth", "ID супервайзера", "Нанято МЕ", "Уволено МЕ"]],
            fires[["MonthStart", "YearMonth", "ID супервайзера", "Нанято МЕ", "Уволено МЕ"]],
        ],
        ignore_index=True,
    )
    if flow.empty:
        return pd.DataFrame(columns=["MonthStart", "YearMonth", "ID супервайзера"])

    flow["MonthStart"] = pd.to_datetime(flow["MonthStart"], errors="coerce").dt.normalize()
    flow["YearMonth"] = pd.to_numeric(flow["YearMonth"], errors="coerce").astype("Int64")
    return (
        flow.dropna(subset=["MonthStart", "YearMonth", "ID супервайзера"])
        .groupby(["MonthStart", "YearMonth", "ID супервайзера"], dropna=False)
        .agg(
            **{
                "Нанято МЕ": ("Нанято МЕ", "sum"),
                "Уволено МЕ": ("Уволено МЕ", "sum"),
            }
        )
        .reset_index()
    )


def _attach_staffing_metrics(monthly: pd.DataFrame, out_dir: Path) -> pd.DataFrame:
    result = monthly.copy()
    staffing_path = out_dir / "org_staffing_monthly_snapshot.parquet"
    if staffing_path.exists():
        staffing = pd.read_parquet(staffing_path)
        sv_staffing = staffing[
            staffing["Уровень анализа"].eq("СВ")
            & staffing["ID супервайзера"].notna()
        ].copy()
        sv_staffing = (
            sv_staffing.groupby(["MonthStart", "YearMonth", "ID супервайзера"], dropna=False)
            .agg(
                **{
                    "Активных МЕ": ("Активных МЕ", _sum_numeric),
                    "Активных СВ": ("Активных СВ", _sum_numeric),
                    "Открытых вакансий": ("Открытых вакансий", _sum_numeric),
                    "Открытых вакансий МЕ": ("Открытых вакансий МЕ", _sum_numeric),
                    "Открытых вакансий СВ": ("Открытых вакансий СВ", _sum_numeric),
                    "Приостановленных вакансий": ("Приостановленных вакансий", _sum_numeric),
                    "Нанято": ("Нанято", _sum_numeric),
                    "Уволено": ("Уволено", _sum_numeric),
                }
            )
            .reset_index()
        )
        sv_staffing["Чистый отток"] = sv_staffing["Уволено"] - sv_staffing["Нанято"]
        sv_staffing["Баланс персонала"] = sv_staffing["Нанято"] - sv_staffing["Уволено"]
        keep = [
            "MonthStart",
            "YearMonth",
            "ID супервайзера",
            "Активных МЕ",
            "Активных СВ",
            "Открытых вакансий",
            "Открытых вакансий МЕ",
            "Открытых вакансий СВ",
            "Приостановленных вакансий",
            "Нанято",
            "Уволено",
            "Чистый отток",
            "Баланс персонала",
        ]
        result = result.merge(
            sv_staffing[[c for c in keep if c in sv_staffing.columns]],
            on=["MonthStart", "YearMonth", "ID супервайзера"],
            how="left",
        )
    else:
        for column in [
            "Активных МЕ",
            "Активных СВ",
            "Открытых вакансий",
            "Открытых вакансий МЕ",
            "Открытых вакансий СВ",
            "Приостановленных вакансий",
            "Нанято",
            "Уволено",
            "Чистый отток",
            "Баланс персонала",
        ]:
            result[column] = pd.NA

    for column in [
        "Активных МЕ",
        "Активных СВ",
        "Нанято",
        "Уволено",
        "Чистый отток",
        "Баланс персонала",
        "Открытых вакансий",
        "Открытых вакансий МЕ",
        "Открытых вакансий СВ",
        "Приостановленных вакансий",
    ]:
        if column in result.columns:
            result[column] = pd.to_numeric(result[column], errors="coerce")

    result["Нанято всего"] = result["Нанято"]
    result["Уволено всего"] = result["Уволено"]

    me_flow = _build_me_flow_by_sv(out_dir)
    if not me_flow.empty:
        result = result.merge(
            me_flow,
            on=["MonthStart", "YearMonth", "ID супервайзера"],
            how="left",
        )
    else:
        result["Нанято МЕ"] = pd.NA
        result["Уволено МЕ"] = pd.NA

    for column in ["Нанято МЕ", "Уволено МЕ"]:
        result[column] = pd.to_numeric(result[column], errors="coerce").fillna(0)
    result["Нанято"] = result["Нанято МЕ"]
    result["Уволено"] = result["Уволено МЕ"]

    result["Размер команды"] = pd.to_numeric(result.get("Размер команды"), errors="coerce")
    result["Команда"] = result["Активных МЕ"]
    result["Плановая команда"] = result["Команда"].fillna(0) + result["Открытых вакансий МЕ"].fillna(0)
    result["Плановая команда"] = result["Плановая команда"].where(result["Плановая команда"].gt(0))
    result["Доля вакансий к активным МЕ %"] = result["Открытых вакансий МЕ"] / result["Команда"].replace(0, np.nan)
    result["Доля вакансий от плановой команды %"] = (
        result["Открытых вакансий МЕ"] / result["Плановая команда"].replace(0, np.nan)
    )
    result["Текучесть команды %"] = result["Уволено"] / result["Команда"].replace(0, np.nan)
    result["Чистый отток"] = result["Уволено"] - result["Нанято"]
    result["Кадровый отток"] = (result["Уволено"] - result["Нанято"]).clip(lower=0)
    result["Кадровый приток"] = (result["Нанято"] - result["Уволено"]).clip(lower=0)
    result["Баланс персонала"] = result["Нанято"] - result["Уволено"]
    result["Кадровая устойчивость %"] = result.apply(_staffing_score_from_row, axis=1)
    return result


def build_page5_sv_oed_data() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    settings = load_settings()
    out_dir = Path(settings["paths"]["out"])

    page2_sv = pd.read_parquet(out_dir / "page2_sv_monthly_snapshot.parquet")
    fact_oed = pd.read_parquet(out_dir / "fact_oed.parquet")
    dim_employees = pd.read_parquet(out_dir / "dim_employees.parquet")
    teams = pd.read_parquet(out_dir / "dim_teams.parquet")
    attestations_path = out_dir / "attestations_fact.parquet"
    attestations = pd.read_parquet(attestations_path) if attestations_path.exists() else pd.DataFrame()

    monthly, quarterly = _build_monthly_snapshot(page2_sv, fact_oed, dim_employees, teams, attestations, out_dir)
    employee_kpi_path = out_dir / "kpi_employee_monthly_metrics.parquet"
    page3_path = out_dir / "page3_merch_monthly_snapshot.parquet"
    if employee_kpi_path.exists() and page3_path.exists():
        sv_kpi_metrics = aggregate_employee_kpi_to_org(
            pd.read_parquet(employee_kpi_path),
            pd.read_parquet(page3_path),
            "ID супервайзера",
        )
        monthly = monthly.merge(
            sv_kpi_metrics,
            on=["MonthStart", "YearMonth", "ID супервайзера"],
            how="left",
        )
        monthly["KPI месяца %"] = monthly["KPI проекта %"]
        monthly = _refresh_sv_effectiveness(monthly)
    public_columns = [
        "MonthStart",
        "YearMonth",
        "Ранг СВ",
        "Регион BI",
        "Группа региона",
        "ID супервайзера",
        "Супервайзер",
        "Дата приема СВ",
        "Стаж СВ (месяцев)",
        "ID территориального менеджера",
        "Территориальный менеджер",
        "Код СВ",
        "Размер команды",
        "Плановая команда",
        "Открытых вакансий МЕ",
        "Нанято",
        "Уволено",
        "Баланс персонала",
        "Текучесть команды %",
        "Стабильность команды %",
        "KPI месяца %",
        "ОКК команды %",
        "Обучение команды %",
        "Фрод команды",
        "Фрод %",
        "Балл эффективности",
        "Статус эффективности СВ",
        "Причина статуса СВ",
        "Класс ОЭД",
        "Есть ОЭД СВ",
        "KPI ОЭД %",
        "Аттестация ОЭД %",
        "Продукт ОЭД %",
        "Управление ОЭД %",
        "Аттестация клиента %",
        "Аттестация клиента Q4 2025 %",
        "Аттестация клиента Q1 2026 %",
        "Балл личной эффективности",
        "Статус личной эффективности",
        "Причина личной эффективности",
        "Резерв",
        "PICOS план %",
        "PICOS факт %",
        "PICOS выполнение %",
        "OSA план %",
        "OSA факт %",
        "OSA выполнение %",
        "TOP16 план %",
        "TOP16 факт %",
        "TOP16 выполнение %",
    ]
    monthly = monthly[[column for column in public_columns if column in monthly.columns]].copy()
    d_supervisor = _build_supervisor_directory(page2_sv, dim_employees, teams)[
        [
            "ID супервайзера",
            "Супервайзер",
            "Дата приема СВ",
            "Стаж СВ (дней)",
            "Стаж СВ (месяцев)",
            "Код СВ",
            "СВ / Объект",
            "Регион BI",
            "Группа региона",
            "ID территориального менеджера",
            "Территориальный менеджер",
        ]
    ].drop_duplicates("ID супервайзера").copy()
    latest_source_hierarchy = (
        monthly.sort_values(["ID супервайзера", "MonthStart"], kind="mergesort")
        .drop_duplicates("ID супервайзера", keep="last")
        [[
            "ID супервайзера",
            "Регион BI",
            "Группа региона",
            "ID территориального менеджера",
            "Территориальный менеджер",
        ]]
    )
    d_supervisor = d_supervisor.drop(
        columns=["Регион BI", "Группа региона", "ID территориального менеджера", "Территориальный менеджер"],
        errors="ignore",
    ).merge(latest_source_hierarchy, on="ID супервайзера", how="left")
    d_supervisor["Код СВ"] = d_supervisor["Код СВ"].replace("", pd.NA).fillna("Нет кода СВ")
    region_present = d_supervisor["Регион BI"].astype("string").str.strip().notna() & d_supervisor["Регион BI"].astype("string").str.strip().ne("")
    tm_present = d_supervisor["Территориальный менеджер"].astype("string").str.strip().notna() & d_supervisor["Территориальный менеджер"].astype("string").str.strip().ne("")
    d_supervisor["Показывать в срезе"] = region_present & tm_present
    no_sv_rows = _build_no_sv_supervisor_rows(teams, d_supervisor.columns.tolist())
    if not no_sv_rows.empty:
        no_sv_rows["Показывать в срезе"] = True
        d_supervisor = pd.concat([d_supervisor, no_sv_rows], ignore_index=True)
        d_supervisor = d_supervisor.drop_duplicates("ID супервайзера", keep="first")
    legend = _build_legend()

    save_parquet(monthly, str(out_dir / "page5_sv_monthly_snapshot.parquet"))
    save_parquet(quarterly, str(out_dir / "page5_sv_oed_quarterly.parquet"))
    save_parquet(d_supervisor, str(out_dir / "dSupervisor.parquet"))
    save_parquet(legend, str(out_dir / "page5_sv_legend.parquet"))

    print(f"\n  Page5 SV snapshot: {len(monthly)} строк")
    print(f"  Page5 SV OED quarterly: {len(quarterly)} строк")
    print(f"  dSupervisor: {len(d_supervisor)} строк")
    print(f"  Page5 SV legend: {len(legend)} строк")
    return monthly, quarterly, d_supervisor, legend


if __name__ == "__main__":
    build_page5_sv_oed_data()
