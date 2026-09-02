import sys
from pathlib import Path

import pandas as pd

if __package__ is None or __package__ == "":
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from scripts.utils import (
    load_settings,
    save_parquet,
    normalize_pct as _normalize_pct,
    normalize_person_name as _normalize_name,
    first_notna as _first_notna,
)
from scripts.staffing_utils import (
    is_tm_role as _is_tm_role,
    mode_or_first as _mode_or_first,
    normalize_confirmed_tm,
    score_higher_is_better as _score_higher_is_better,
    score_lower_is_better as _score_lower_is_better,
)
from scripts.kpi_metric_utils import KPI_SCORE_WEIGHT_COLUMNS, aggregate_employee_kpi_to_org


REPORT_START_YEAR = load_settings()["reporting"]["start_yearmonth"] // 100

TM_OPERATIONAL_KPI_WEIGHT = 0.35
TM_OPERATIONAL_QUALITY_WEIGHT = 0.30
TM_OPERATIONAL_LEARNING_WEIGHT = 0.20
TM_OPERATIONAL_ANTIFRAUD_WEIGHT = 0.15
TM_EFFECTIVENESS_WEIGHTS = {
    "KPI месяца территории %": 0.30,
    "Качество команды %": 0.20,
    "Обучение команды %": 0.15,
    "Фрод %": 0.15,
    "Стабильность команды %": 0.15,
    "Текучесть %": 0.05,
}
TM_SIGNAL_WEIGHTS = {
    "Качество команды": TM_EFFECTIVENESS_WEIGHTS["Качество команды %"],
    "Обучение команды": TM_EFFECTIVENESS_WEIGHTS["Обучение команды %"],
    "Фрод": TM_EFFECTIVENESS_WEIGHTS["Фрод %"],
    "Стабильность команды": TM_EFFECTIVENESS_WEIGHTS["Стабильность команды %"],
    "Текучесть": TM_EFFECTIVENESS_WEIGHTS["Текучесть %"],
}
TM_KPI_COMPONENTS = {
    "PICOS": ("PICOS выполнение %", "PICOS вес в KPI %", 0.98),
    "OSA": ("OSA выполнение %", "OSA вес в KPI %", 0.95),
    "TOP16": ("TOP16 выполнение %", "TOP16 вес в KPI %", 0.95),
}
TM_EFFECTIVENESS_CRITICAL_COLUMNS = [
    "KPI месяца территории %",
    "Целевой порог KPI территории %",
]

TM_MIN_AVAILABLE_WEIGHT = 0.60
TM_STABLE_MIN_SCORE = 0.90
TM_CONTROL_MIN_SCORE = 0.80
TM_KPI_GREEN_MIN = 0.99
TM_KPI_RED_MIN = 0.95
TM_QUALITY_GREEN_MIN = 0.60
TM_QUALITY_RED_MIN = 0.40
TM_LEARNING_GREEN_MIN = 0.90
TM_LEARNING_RED_MIN = 0.80
TM_FRAUD_GREEN_MAX = 0.15
TM_FRAUD_RED_MAX = 0.20
TM_STABILITY_GREEN_MIN = 0.95
TM_STABILITY_RED_MIN = 0.90
TM_TURNOVER_GREEN_MAX = 0.10
TM_TURNOVER_RED_MAX = 0.15

NO_TM_ID = "NO_TM"
NO_TM_NAME = "Вакансия / нет ТМ"
MULTI_REGION_LABEL = "Несколько регионов"


def _short_name(value: str | None) -> str | None:
    norm = _normalize_name(value)
    if not norm:
        return None
    parts = norm.split()
    if len(parts) >= 2:
        return f"{parts[0]} {parts[1]}"
    return parts[0]


def _join_unique(series: pd.Series):
    values = []
    for value in series.dropna():
        for part in str(value).split(","):
            text = part.strip()
            if text:
                values.append(text)
    if not values:
        return pd.NA
    unique = sorted(set(values))
    return ", ".join(unique)


def _single_or_multi(series: pd.Series):
    values = [
        str(value).strip()
        for value in series.dropna()
        if str(value).strip()
    ]
    if not values:
        return pd.NA
    unique = sorted(set(values))
    if len(unique) == 1:
        return unique[0]
    return MULTI_REGION_LABEL


def _safe_mean(values: list[float | int | None]):
    clean = [float(v) for v in values if pd.notna(v)]
    if not clean:
        return pd.NA
    return sum(clean) / len(clean)


def _tenure_months_at(month_start, hire_date):
    month_ts = pd.to_datetime(month_start, errors="coerce")
    hire_ts = pd.to_datetime(hire_date, errors="coerce")
    if pd.isna(month_ts) or pd.isna(hire_ts):
        return pd.NA
    month_ts = month_ts + pd.offsets.MonthEnd(0)
    days = (month_ts - hire_ts).days
    if days < 0:
        return pd.NA
    return round(days / 30.44, 1)


def _weighted_mean(frame: pd.DataFrame, value_col: str, weight_col: str):
    if value_col not in frame.columns or weight_col not in frame.columns:
        return pd.NA
    part = frame[[value_col, weight_col]].copy()
    part[value_col] = pd.to_numeric(part[value_col], errors="coerce")
    part[weight_col] = pd.to_numeric(part[weight_col], errors="coerce")
    part = part[part[value_col].notna() & part[weight_col].notna() & (part[weight_col] > 0)]
    if part.empty:
        return pd.NA
    return float((part[value_col] * part[weight_col]).sum() / part[weight_col].sum())


def _available_weight(values: list[tuple[float | int | None, float]]) -> float:
    return sum(weight for value, weight in values if pd.notna(value))


def _weighted_score(values: list[tuple[float | int | None, float]]):
    total = 0.0
    weights = 0.0
    for value, weight in values:
        if pd.notna(value):
            total += float(value) * weight
            weights += weight
    if weights == 0:
        return pd.NA
    return total / weights


def _tm_available_weight_from_row(row: pd.Series) -> float:
    return sum(weight for column, weight in TM_EFFECTIVENESS_WEIGHTS.items() if pd.notna(row.get(column)))


def _tm_kpi_target_from_row(row: pd.Series):
    weighted_target = 0.0
    total_weight = 0.0
    for _, (value_column, weight_column, green_target) in TM_KPI_COMPONENTS.items():
        value = pd.to_numeric(row.get(value_column), errors="coerce")
        weight = pd.to_numeric(row.get(weight_column), errors="coerce")
        if pd.isna(weight) or float(weight) <= 0:
            continue
        if pd.isna(value):
            return pd.NA
        weighted_target += float(weight) * green_target
        total_weight += float(weight)
    if total_weight <= 0:
        return pd.NA
    return weighted_target / total_weight


def _tm_kpi_score_from_row(row: pd.Series, weight: float):
    return _score_higher_is_better(
        row.get("KPI месяца территории %"),
        row.get("Целевой порог KPI территории %"),
        TM_KPI_RED_MIN,
        weight,
    )


def _tm_operational_result_from_row(row: pd.Series):
    if pd.isna(row.get("KPI месяца территории %")) or pd.isna(row.get("Целевой порог KPI территории %")):
        return pd.NA
    available_values = [
        (row.get("KPI месяца территории %"), TM_OPERATIONAL_KPI_WEIGHT),
        (row.get("Качество команды %"), TM_OPERATIONAL_QUALITY_WEIGHT),
        (row.get("Обучение команды %"), TM_OPERATIONAL_LEARNING_WEIGHT),
        (row.get("Фрод %"), TM_OPERATIONAL_ANTIFRAUD_WEIGHT),
    ]
    available_weight = _available_weight(available_values)
    if available_weight < TM_MIN_AVAILABLE_WEIGHT:
        return pd.NA
    score = _tm_kpi_score_from_row(row, TM_OPERATIONAL_KPI_WEIGHT)
    score += _score_higher_is_better(
        row.get("Качество команды %"),
        TM_QUALITY_GREEN_MIN,
        TM_QUALITY_RED_MIN,
        TM_OPERATIONAL_QUALITY_WEIGHT,
    )
    score += _score_higher_is_better(
        row.get("Обучение команды %"),
        TM_LEARNING_GREEN_MIN,
        TM_LEARNING_RED_MIN,
        TM_OPERATIONAL_LEARNING_WEIGHT,
    )
    score += _score_lower_is_better(
        row.get("Фрод %"),
        TM_FRAUD_GREEN_MAX,
        TM_FRAUD_RED_MAX,
        TM_OPERATIONAL_ANTIFRAUD_WEIGHT,
    )
    return round(max(0.0, min(1.0, score / available_weight)), 4)


def _tm_effectiveness_score_from_row(row: pd.Series):
    available_weight = _tm_available_weight_from_row(row)
    if available_weight < TM_MIN_AVAILABLE_WEIGHT:
        return pd.NA
    if any(pd.isna(row.get(column)) for column in TM_EFFECTIVENESS_CRITICAL_COLUMNS):
        return pd.NA

    score = 0.0
    score += _tm_kpi_score_from_row(
        row,
        TM_EFFECTIVENESS_WEIGHTS["KPI месяца территории %"],
    )
    score += _score_higher_is_better(
        row.get("Качество команды %"),
        TM_QUALITY_GREEN_MIN,
        TM_QUALITY_RED_MIN,
        TM_EFFECTIVENESS_WEIGHTS["Качество команды %"],
    )
    score += _score_higher_is_better(
        row.get("Обучение команды %"),
        TM_LEARNING_GREEN_MIN,
        TM_LEARNING_RED_MIN,
        TM_EFFECTIVENESS_WEIGHTS["Обучение команды %"],
    )
    score += _score_lower_is_better(
        row.get("Фрод %"),
        TM_FRAUD_GREEN_MAX,
        TM_FRAUD_RED_MAX,
        TM_EFFECTIVENESS_WEIGHTS["Фрод %"],
    )
    score += _score_higher_is_better(
        row.get("Стабильность команды %"),
        TM_STABILITY_GREEN_MIN,
        TM_STABILITY_RED_MIN,
        TM_EFFECTIVENESS_WEIGHTS["Стабильность команды %"],
    )
    score += _score_lower_is_better(
        row.get("Текучесть %"),
        TM_TURNOVER_GREEN_MAX,
        TM_TURNOVER_RED_MAX,
        TM_EFFECTIVENESS_WEIGHTS["Текучесть %"],
    )
    return round(max(0.0, min(1.0, score)), 4)


def _staffing_score(active_me, open_me, hired, fired):
    if pd.isna(active_me) and pd.isna(open_me) and pd.isna(hired) and pd.isna(fired):
        return pd.NA
    active_me = max(float(active_me), 0.0) if pd.notna(active_me) else 0.0
    open_me = max(float(open_me), 0.0) if pd.notna(open_me) else 0.0
    hired = max(float(hired), 0.0) if pd.notna(hired) else 0.0
    fired = max(float(fired), 0.0) if pd.notna(fired) else 0.0
    planned_team = active_me + open_me
    if planned_team <= 0:
        planned_team = max(hired + fired, 1.0)
    people_base = active_me if active_me > 0 else planned_team
    vacancy_share = open_me / planned_team
    turnover = fired / people_base
    net_outflow_share = max(0.0, fired - hired) / people_base
    penalty = vacancy_share * 0.70 + turnover * 0.20 + net_outflow_share * 0.10
    return max(0.0, min(1.0, 1.0 - penalty))


def _sum_numeric(frame: pd.DataFrame, column: str, default=0):
    if frame.empty or column not in frame.columns:
        return default
    values = pd.to_numeric(frame[column], errors="coerce")
    if not values.notna().any():
        return default
    return values.fillna(0).sum()


def _build_unassigned_hr_flow_by_month(hr: pd.DataFrame) -> pd.DataFrame:
    columns = ["MonthStart", "YearMonth", "Нанято без ТМ", "Уволено без ТМ"]
    if hr is None or hr.empty:
        return pd.DataFrame(columns=columns)

    work = hr.replace("", pd.NA).copy()
    position = work.get("Должность", pd.Series(index=work.index, dtype="object")).astype(str).str.lower()
    field_team = position.str.contains("мерч", na=False) | position.str.contains("супервайзер", na=False)
    no_tm = work["ID территориального менеджера"].isna()
    core = work.get("Группа региона", pd.Series(index=work.index, dtype="object")).eq("core")
    work = work[no_tm & core & field_team].copy()
    if work.empty:
        return pd.DataFrame(columns=columns)

    hires = work.dropna(subset=["MonthStart найм", "YearMonth найм"]).copy()
    hires["MonthStart"] = hires["MonthStart найм"]
    hires["YearMonth"] = hires["YearMonth найм"]
    hires["Нанято без ТМ"] = 1
    hires["Уволено без ТМ"] = 0

    fires = work[
        work["Состояние"].astype(str).str.contains("увольнение", case=False, na=False)
        & work["MonthStart увольнение"].notna()
        & work["YearMonth увольнение"].notna()
    ].copy()
    fires["MonthStart"] = fires["MonthStart увольнение"]
    fires["YearMonth"] = fires["YearMonth увольнение"]
    fires["Нанято без ТМ"] = 0
    fires["Уволено без ТМ"] = 1

    flow = pd.concat(
        [
            hires[["MonthStart", "YearMonth", "Нанято без ТМ", "Уволено без ТМ"]],
            fires[["MonthStart", "YearMonth", "Нанято без ТМ", "Уволено без ТМ"]],
        ],
        ignore_index=True,
    )
    if flow.empty:
        return pd.DataFrame(columns=columns)

    flow["MonthStart"] = pd.to_datetime(flow["MonthStart"], errors="coerce").dt.normalize()
    flow["YearMonth"] = pd.to_numeric(flow["YearMonth"], errors="coerce").astype("Int64")
    return (
        flow.dropna(subset=["MonthStart", "YearMonth"])
        .groupby(["MonthStart", "YearMonth"], dropna=False)
        .agg(
            **{
                "Нанято без ТМ": ("Нанято без ТМ", "sum"),
                "Уволено без ТМ": ("Уволено без ТМ", "sum"),
            }
        )
        .reset_index()
    )


def _active_tm_ids(dim_employees: pd.DataFrame) -> set[str]:
    if dim_employees.empty or not {"ID сотрудника", "Должность", "Активен"}.issubset(dim_employees.columns):
        return set()
    dim = dim_employees.copy()
    dim["ID сотрудника"] = dim["ID сотрудника"].replace("", pd.NA)
    active_mask = dim["Активен"].fillna(False).eq(True)
    tm_mask = dim["Должность"].map(_is_tm_role)
    return set(dim.loc[active_mask & tm_mask, "ID сотрудника"].dropna().astype(str).str.strip())


def _clear_unconfirmed_tm(teams_work: pd.DataFrame, valid_tm_ids: set[str]) -> pd.DataFrame:
    teams_work = teams_work.copy()
    teams_work["ID территориального менеджера"] = teams_work["ID территориального менеджера"].replace("", pd.NA)
    tm_ids = teams_work["ID территориального менеджера"].astype("string").str.strip()
    invalid_tm = tm_ids.notna() & ~tm_ids.isin(valid_tm_ids) & tm_ids.ne(NO_TM_ID)
    teams_work.loc[invalid_tm, "ID территориального менеджера"] = pd.NA
    teams_work.loc[invalid_tm, "Территориальный менеджер"] = pd.NA
    return normalize_confirmed_tm(teams_work)


def _build_tm_directory(dim_employees: pd.DataFrame, teams: pd.DataFrame) -> pd.DataFrame:
    valid_tm_ids = _active_tm_ids(dim_employees)
    teams_work = _clear_unconfirmed_tm(teams.replace("", pd.NA), valid_tm_ids)
    teams_tm = (
        teams_work.groupby("ID территориального менеджера", dropna=False)
        .agg(
            **{
                "Территориальный менеджер": ("Территориальный менеджер", "first"),
                "Регион BI": ("Регион BI", _single_or_multi),
                "Регионы ТМ": ("Регион BI", _join_unique),
                "Группа региона": ("Группа региона", _mode_or_first),
            }
        )
        .reset_index()
    )

    dim = dim_employees[
        [
            "ID сотрудника",
            "ФИО",
            "Должность",
            "Регион BI",
            "Группа региона",
            "Город",
            "Дата приёма",
            "Активен",
        ]
    ].copy()
    dim = dim[dim["Активен"].fillna(False).eq(True)].copy()
    for column in ["Регион BI", "Группа региона", "ФИО", "Должность", "Город"]:
        if column in dim.columns:
            dim[column] = dim[column].replace("", pd.NA)
    dim = dim.rename(
        columns={
            "ID сотрудника": "ID территориального менеджера",
            "ФИО": "Территориальный менеджер dim",
            "Должность": "Должность ТМ",
            "Регион BI": "Регион BI dim",
            "Группа региона": "Группа региона dim",
            "Город": "Город ТМ",
            "Дата приёма": "Дата приема ТМ",
            "Активен": "Активен ТМ",
        }
    )

    directory = teams_tm.merge(dim, on="ID территориального менеджера", how="left")
    directory = directory[
        directory["ID территориального менеджера"].eq(NO_TM_ID)
        | directory["ID территориального менеджера"].astype(str).str.strip().isin(valid_tm_ids)
    ].copy()
    directory["Территориальный менеджер"] = directory["Территориальный менеджер"].combine_first(directory["Территориальный менеджер dim"])
    directory.loc[directory["ID территориального менеджера"].eq(NO_TM_ID), "Территориальный менеджер"] = NO_TM_NAME
    directory["TM short"] = directory["Территориальный менеджер"].map(_short_name)
    directory = directory.drop(columns=["Территориальный менеджер dim", "Регион BI dim", "Группа региона dim"], errors="ignore")
    directory = directory.drop_duplicates("ID территориального менеджера")
    directory = directory.sort_values(["Регион BI", "Территориальный менеджер"], na_position="last").reset_index(drop=True)
    directory["Код ТМ"] = [f"TM-{idx:02d}" for idx in range(1, len(directory) + 1)]
    directory["ТМ / Объект"] = directory["Территориальный менеджер"]
    return directory


def _cross_join_months(entity: pd.DataFrame, months: pd.DataFrame) -> pd.DataFrame:
    if entity.empty or months.empty:
        return pd.DataFrame(columns=[*months.columns, *entity.columns])
    return months.assign(_join_key=1).merge(
        entity.assign(_join_key=1),
        on="_join_key",
        how="inner",
    ).drop(columns="_join_key")


def _build_sv_tm_map(
    teams: pd.DataFrame,
    tm_directory: pd.DataFrame,
    month_calendar: pd.DataFrame,
) -> pd.DataFrame:
    valid_tm_ids = set(tm_directory["ID территориального менеджера"].dropna().astype(str).str.strip())
    teams_work = _clear_unconfirmed_tm(teams.replace("", pd.NA), valid_tm_ids)
    sv_list = (
        teams_work[["ID супервайзера"]]
        .dropna(subset=["ID супервайзера"])
        .drop_duplicates()
    )
    sv_months = _cross_join_months(sv_list, month_calendar[["MonthStart", "YearMonth"]].drop_duplicates())
    current = (
        teams_work
        .dropna(subset=["ID супервайзера"])
        .groupby("ID супервайзера", dropna=False)
        .agg(
            **{
                "ID территориального менеджера": ("ID территориального менеджера", "first"),
                "Регион BI": ("Регион BI", _mode_or_first),
                "Группа региона": ("Группа региона", _mode_or_first),
            }
        )
        .reset_index()
    )
    result = sv_months.merge(current, on="ID супервайзера", how="left")
    result = result.merge(
        tm_directory[
            ["ID территориального менеджера", "Территориальный менеджер", "Код ТМ", "ТМ / Объект", "Регион BI", "Группа региона"]
        ],
        on="ID территориального менеджера",
        how="left",
        suffixes=("", "_tm"),
    )
    result["Регион BI"] = result["Регион BI"].combine_first(result["Регион BI_tm"])
    result["Группа региона"] = result["Группа региона"].combine_first(result["Группа региона_tm"])
    return result.drop(columns=["Регион BI_tm", "Группа региона_tm"], errors="ignore")


def _build_me_tm_map(
    teams: pd.DataFrame,
    tm_directory: pd.DataFrame,
    month_calendar: pd.DataFrame,
) -> pd.DataFrame:
    valid_tm_ids = set(tm_directory["ID территориального менеджера"].dropna().astype(str).str.strip())
    teams_work = _clear_unconfirmed_tm(teams.replace("", pd.NA), valid_tm_ids)
    me_list = (
        teams_work[["ID мерчендайзера"]]
        .dropna(subset=["ID мерчендайзера"])
        .drop_duplicates()
    )
    me_months = _cross_join_months(me_list, month_calendar[["MonthStart", "YearMonth"]].drop_duplicates())
    current = (
        teams_work
        .dropna(subset=["ID мерчендайзера"])
        .groupby("ID мерчендайзера", dropna=False)
        .agg(
            **{
                "ID территориального менеджера": ("ID территориального менеджера", "first"),
                "Регион BI": ("Регион BI", _mode_or_first),
                "Группа региона": ("Группа региона", _mode_or_first),
            }
        )
        .reset_index()
    )
    result = me_months.merge(current, on="ID мерчендайзера", how="left")
    result = result.merge(
        tm_directory[
            ["ID территориального менеджера", "Территориальный менеджер", "Код ТМ", "ТМ / Объект", "Регион BI", "Группа региона"]
        ],
        on="ID территориального менеджера",
        how="left",
        suffixes=("", "_tm"),
    )
    result["Регион BI"] = result["Регион BI"].combine_first(result["Регион BI_tm"])
    result["Группа региона"] = result["Группа региона"].combine_first(result["Группа региона_tm"])
    return result.drop(columns=["Регион BI_tm", "Группа региона_tm"], errors="ignore")


def _build_source_entity_tm_map(
    source: pd.DataFrame,
    entity_column: str,
    tm_directory: pd.DataFrame,
) -> pd.DataFrame:
    required = {
        "MonthStart",
        "YearMonth",
        entity_column,
        "ID территориального менеджера",
        "Территориальный менеджер",
        "Регион BI",
    }
    if source is None or source.empty or not required.issubset(source.columns):
        return pd.DataFrame(columns=[*required, "Код ТМ", "ТМ / Объект", "Группа региона"])
    work = source[[column for column in [*required, "Группа региона"] if column in source.columns]].copy()
    work = work.replace("", pd.NA).dropna(subset=["MonthStart", "YearMonth", entity_column])
    work = work[
        work["ID территориального менеджера"].notna()
        & work["ID территориального менеджера"].astype(str).ne(NO_TM_ID)
    ].copy()
    if work.empty:
        return pd.DataFrame(columns=[*required, "Код ТМ", "ТМ / Объект", "Группа региона"])

    keys = ["MonthStart", "YearMonth", entity_column]
    tm_count = work.groupby(keys)["ID территориального менеджера"].transform("nunique")
    work = work[tm_count.eq(1)].copy()
    result = (
        work.groupby(keys, dropna=False)
        .agg(
            **{
                "ID территориального менеджера": ("ID территориального менеджера", _mode_or_first),
                "Территориальный менеджер": ("Территориальный менеджер", _mode_or_first),
                "Регион BI": ("Регион BI", _mode_or_first),
                "Группа региона": ("Группа региона", _mode_or_first) if "Группа региона" in work.columns else ("Регион BI", lambda _: pd.NA),
            }
        )
        .reset_index()
    )
    result = result.merge(
        tm_directory[["ID территориального менеджера", "Код ТМ", "ТМ / Объект"]],
        on="ID территориального менеджера",
        how="left",
    )
    result["ТМ / Объект"] = result["ТМ / Объект"].combine_first(result["Территориальный менеджер"])
    return result


def _append_missing_entity_mapping(
    primary: pd.DataFrame,
    fallback: pd.DataFrame,
    entity_column: str,
) -> pd.DataFrame:
    keys = ["MonthStart", "YearMonth", entity_column]
    if fallback is None or fallback.empty:
        return primary
    if primary is None or primary.empty:
        return fallback.copy()

    existing = primary[keys].drop_duplicates()
    missing = fallback.merge(existing, on=keys, how="left", indicator=True)
    missing = missing[missing["_merge"].eq("left_only")].drop(columns="_merge")
    return pd.concat([primary, missing], ignore_index=True, sort=False)


def _build_tm_learning_monthly(learning_fact: pd.DataFrame, team_member_tm_map: pd.DataFrame) -> pd.DataFrame:
    learn = learning_fact[
        ["ID сотрудника", "Номер курса", "Обязательный", "Пройдено", "StartYearMonth", "YearMonth"]
    ].copy()
    learn = learn[learn["Обязательный"] == True].copy()
    learn = learn.rename(
        columns={
            "StartYearMonth": "ГодМесяц назначения",
            "YearMonth": "ГодМесяц завершения",
        }
    )
    learn = learn.dropna(subset=["ГодМесяц назначения", "ID сотрудника"])
    learn["ГодМесяц назначения"] = pd.to_numeric(learn["ГодМесяц назначения"], errors="coerce").astype("Int64")
    learn["ГодМесяц завершения"] = pd.to_numeric(learn["ГодМесяц завершения"], errors="coerce").astype("Int64")

    work = team_member_tm_map[
        [
            "MonthStart",
            "YearMonth",
            "ID сотрудника",
            "ID территориального менеджера",
            "Территориальный менеджер",
            "Код ТМ",
            "ТМ / Объект",
            "Регион BI",
            "Группа региона",
        ]
    ].merge(
        learn[
            [
                "ID сотрудника",
                "Номер курса",
                "Пройдено",
                "ГодМесяц назначения",
                "ГодМесяц завершения",
            ]
        ],
        on="ID сотрудника",
        how="left",
    )
    work["YearMonth"] = pd.to_numeric(work["YearMonth"], errors="coerce").astype("Int64")
    work = work[
        work["ГодМесяц назначения"].notna()
        & work["YearMonth"].notna()
        & (work["ГодМесяц назначения"] <= work["YearMonth"])
    ].copy()
    if work.empty:
        return pd.DataFrame(
            columns=[
                "MonthStart",
                "YearMonth",
                "ID территориального менеджера",
                "Территориальный менеджер",
                "Код ТМ",
                "ТМ / Объект",
                "Назначено обязательных курсов",
                "Пройдено обязательных курсов",
                "Сотрудников с курсами",
                "Регионы ТМ",
                "Группа региона",
                "Обучение %",
            ]
        )

    work["Пройдено числом"] = (
        work["Пройдено"].eq(True)
        & work["ГодМесяц завершения"].notna()
        & (work["ГодМесяц завершения"] <= work["YearMonth"])
    ).astype(int)
    work = work[work["ID территориального менеджера"].notna()].copy()

    grouped = (
        work.groupby(
            [
                "MonthStart",
                "YearMonth",
                "ID территориального менеджера",
                "Территориальный менеджер",
                "Код ТМ",
                "ТМ / Объект",
            ],
            dropna=False,
        )
        .agg(
            **{
                "Назначено обязательных курсов": ("Номер курса", "size"),
                "Пройдено обязательных курсов": ("Пройдено числом", "sum"),
                "Сотрудников с курсами": ("ID сотрудника", "nunique"),
                "Регионы ТМ": ("Регион BI", _join_unique),
                "Группа региона": ("Группа региона", _mode_or_first),
            }
        )
        .reset_index()
    )
    grouped["Обучение %"] = grouped["Пройдено обязательных курсов"] / grouped["Назначено обязательных курсов"]
    return grouped


def _build_tm_personal_learning_monthly(
    learning_fact: pd.DataFrame,
    tm_directory: pd.DataFrame,
) -> pd.DataFrame:
    learn = learning_fact[
        [
            "ID сотрудника",
            "Номер курса",
            "Обязательный",
            "Пройдено",
            "Балл теста",
            "StartMonth",
            "StartYearMonth",
            "MonthStart",
            "YearMonth",
        ]
    ].copy()
    learn = learn[learn["Обязательный"] == True].copy()
    learn = learn.rename(
        columns={
            "ID сотрудника": "ID территориального менеджера",
            "StartMonth": "Месяц назначения",
            "StartYearMonth": "ГодМесяц назначения",
            "MonthStart": "Месяц завершения",
            "YearMonth": "ГодМесяц завершения",
        }
    )
    learn = learn.dropna(subset=["ID территориального менеджера", "Месяц назначения", "ГодМесяц назначения"])
    learn["Балл теста норм"] = _normalize_pct(learn["Балл теста"])

    learn = learn.merge(
        tm_directory[
            [
                "ID территориального менеджера",
                "Территориальный менеджер",
                "Код ТМ",
                "ТМ / Объект",
                "Регион BI",
                "Группа региона",
            ]
        ],
        on="ID территориального менеджера",
        how="inner",
    )
    return learn


def _tm_metric_signal_records(row: pd.Series) -> list[dict]:
    quality = row.get("Качество команды %")
    learning = row.get("Обучение команды %")
    fraud_pct = row.get("Фрод %")
    team_stability = row.get("Стабильность команды %")
    turnover = row.get("Текучесть %")

    records: list[dict] = []

    def add_signal(metric: str, level: str, order: int, weight: float | None = None):
        weight = TM_SIGNAL_WEIGHTS[metric] if weight is None else weight
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
        (label, pd.to_numeric(row.get(value_column), errors="coerce"), green_target)
        for label, (value_column, _, green_target) in TM_KPI_COMPONENTS.items()
        if pd.notna(pd.to_numeric(row.get(value_column), errors="coerce"))
    ]
    kpi_signal_weight = (
        TM_EFFECTIVENESS_WEIGHTS["KPI месяца территории %"] / len(active_kpi_components)
        if active_kpi_components
        else 0.0
    )
    for order, (label, value, green_target) in enumerate(active_kpi_components, start=1):
        if float(value) < TM_KPI_RED_MIN:
            add_signal(label, "hard", order, kpi_signal_weight)
        elif float(value) < green_target:
            add_signal(label, "soft", order, kpi_signal_weight)

    if pd.notna(quality):
        if float(quality) < TM_QUALITY_RED_MIN:
            add_signal("Качество команды", "hard", 4)
        elif float(quality) < TM_QUALITY_GREEN_MIN:
            add_signal("Качество команды", "soft", 4)

    if pd.notna(learning):
        if float(learning) < TM_LEARNING_RED_MIN:
            add_signal("Обучение команды", "hard", 5)
        elif float(learning) < TM_LEARNING_GREEN_MIN:
            add_signal("Обучение команды", "soft", 5)

    if pd.notna(fraud_pct):
        if float(fraud_pct) > TM_FRAUD_RED_MAX:
            add_signal("Фрод", "hard", 6)
        elif float(fraud_pct) > TM_FRAUD_GREEN_MAX:
            add_signal("Фрод", "soft", 6)

    if pd.notna(team_stability):
        if float(team_stability) < TM_STABILITY_RED_MIN:
            add_signal("Стабильность команды", "hard", 7)
        elif float(team_stability) < TM_STABILITY_GREEN_MIN:
            add_signal("Стабильность команды", "soft", 7)

    if pd.notna(turnover):
        if float(turnover) > TM_TURNOVER_RED_MAX:
            add_signal("Текучесть", "hard", 8)
        elif float(turnover) > TM_TURNOVER_GREEN_MAX:
            add_signal("Текучесть", "soft", 8)

    return records


def _tm_metric_signals(row: pd.Series) -> tuple[list[str], list[str]]:
    records = _tm_metric_signal_records(row)
    hard = [record["metric"] for record in records if record["level"] == "hard"]
    soft = [record["metric"] for record in records if record["level"] == "soft"]
    return hard, soft


def _tm_signal_weight_summary(row: pd.Series) -> pd.Series:
    records = _tm_metric_signal_records(row)
    available_weight = row.get("Доступность индекса ТМ %")
    if pd.notna(available_weight) and float(available_weight) > 0:
        denominator = float(available_weight)
    else:
        denominator = sum({record["metric"]: record["weight"] for record in records}.values()) or 1.0
    red_weight = sum(record["weight"] for record in records if record["level"] == "hard") / denominator
    yellow_weight = sum(record["weight"] for record in records if record["level"] == "soft") / denominator
    issue_weight = sum(record["priority"] for record in records) / denominator
    return pd.Series(
        {
            "Вес красных флагов ТМ %": round(red_weight, 4),
            "Вес желтых флагов ТМ %": round(yellow_weight, 4),
            "Приоритетный вес проблем ТМ %": round(issue_weight, 4),
        }
    )


def _status_from_row(row: pd.Series) -> str:
    score = row.get("Балл эффективности %")
    available_weight = row.get("Доступность индекса ТМ %")

    if pd.isna(score) or pd.isna(available_weight) or float(available_weight) < TM_MIN_AVAILABLE_WEIGHT:
        return "Недостаточно данных"
    if any(pd.isna(row.get(column)) for column in TM_EFFECTIVENESS_CRITICAL_COLUMNS):
        return "Недостаточно данных"

    score_value = float(score)
    display_score = round(score_value * 100) if score_value <= 1 else round(score_value)
    hard, _ = _tm_metric_signals(row)
    if not hard:
        return "Высокая эффективность"
    if display_score < round(TM_CONTROL_MIN_SCORE * 100):
        return "Зона риска"
    return "Зона развития"


def _zone_from_score(score) -> str:
    if pd.isna(score):
        return "Недостаточно данных"
    if float(score) >= TM_STABLE_MIN_SCORE:
        return "Высокая эффективность"
    if float(score) >= TM_CONTROL_MIN_SCORE:
        return "Зона развития"
    return "Зона риска"


def _status_reason_from_row(row: pd.Series):
    status = row.get("Статус")
    if pd.isna(status):
        status = row.get("Статус ТМ")

    hard, _ = _tm_metric_signals(row)
    reasons = list(dict.fromkeys(hard))
    if pd.isna(row.get("Качество команды %")):
        reasons.append("(нет проверок ОКК)")
    if reasons:
        return ", ".join(dict.fromkeys(reasons))
    if status == "Недостаточно данных":
        return "Недостаточно данных"
    if _all_tm_effectiveness_metrics_green(row):
        return "Все метрики выше целевого уровня"

    return pd.NA


def _all_tm_effectiveness_metrics_green(row: pd.Series) -> bool:
    active_kpi_components = [
        (pd.to_numeric(row.get(value_column), errors="coerce"), green_target)
        for value_column, _, green_target in TM_KPI_COMPONENTS.values()
        if pd.notna(pd.to_numeric(row.get(value_column), errors="coerce"))
    ]
    if not active_kpi_components or any(value < target for value, target in active_kpi_components):
        return False
    checks = [
        ("Качество команды %", lambda value: value >= TM_QUALITY_GREEN_MIN),
        ("Обучение команды %", lambda value: value >= TM_LEARNING_GREEN_MIN),
        ("Фрод %", lambda value: value <= TM_FRAUD_GREEN_MAX),
        ("Стабильность команды %", lambda value: value >= TM_STABILITY_GREEN_MIN),
        ("Текучесть %", lambda value: value <= TM_TURNOVER_GREEN_MAX),
    ]
    for column, is_green in checks:
        value = row.get(column)
        if pd.isna(value) or not is_green(float(value)):
            return False
    return True


def _refresh_tm_effectiveness(snapshot: pd.DataFrame) -> pd.DataFrame:
    result = snapshot.copy()
    result["KPI месяца территории %"] = _normalize_pct(result["KPI проекта %"])
    result["Целевой порог KPI территории %"] = result.apply(_tm_kpi_target_from_row, axis=1)
    result["Целевой порог KPI территории %"] = pd.to_numeric(
        result["Целевой порог KPI территории %"],
        errors="coerce",
    )
    result["Результат территории %"] = result.apply(_tm_operational_result_from_row, axis=1)
    result["Результат территории %"] = pd.to_numeric(result["Результат территории %"], errors="coerce")
    result["Доступность индекса ТМ %"] = result.apply(_tm_available_weight_from_row, axis=1)
    result["Балл эффективности %"] = result.apply(_tm_effectiveness_score_from_row, axis=1)
    result["Балл эффективности"] = result["Балл эффективности %"].map(
        lambda value: round(value * 100) if pd.notna(value) else pd.NA
    )
    result[
        [
            "Вес красных флагов ТМ %",
            "Вес желтых флагов ТМ %",
            "Приоритетный вес проблем ТМ %",
        ]
    ] = result.apply(_tm_signal_weight_summary, axis=1)
    result["Статус ТМ"] = result.apply(_status_from_row, axis=1)
    result["Причина статуса ТМ"] = result.apply(_status_reason_from_row, axis=1)
    return result


def _build_score_composition() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Порядок": 1,
                "Вес": "30%",
                "Блок": "Клиентские KPI территории",
                "Краткое описание блока": "PICOS, OSA и TOP16 с учетом структуры KPI торговых точек",
                "Формула": "PICOS: зеленый >=98%, желтый 95-98%, красный <95%; OSA/TOP16: зеленый >=95%, красный <95%",
            },
            {
                "Порядок": 2,
                "Вес": "20%",
                "Блок": "Качество команды",
                "Краткое описание блока": "Среднее качество работы команд территории",
                "Формула": "зеленый >=60%, желтый 40-60%, красный <40%",
            },
            {
                "Порядок": 3,
                "Вес": "15%",
                "Блок": "Обучение команды",
                "Краткое описание блока": "Средний процент прохождения обязательного обучения СВ и МЕ",
                "Формула": "зеленый >=90%, желтый 80-90%, красный <80%",
            },
            {
                "Порядок": 4,
                "Вес": "15%",
                "Блок": "Фрод",
                "Краткое описание блока": "Доля фрод-визитов на территории",
                "Формула": "зеленый <=15%, желтый 15-20%, красный >20%",
            },
            {
                "Порядок": 5,
                "Вес": "15%",
                "Блок": "Стабильность команды",
                "Краткое описание блока": "Кадровое состояние территории",
                "Формула": "1 - 70% * доля вакансий - 20% * текучесть - 10% * чистый отток; зеленый >=95%, желтый 90-95%, красный <90%",
            },
            {
                "Порядок": 6,
                "Вес": "5%",
                "Блок": "Текучесть",
                "Краткое описание блока": "Доля уволенных сотрудников от размера команды",
                "Формула": "зеленый <=10%, желтый 10-15%, красный >15%",
            },
            {
                "Порядок": 7,
                "Вес": "-",
                "Блок": "Статус",
                "Краткое описание блока": "Уровень управленческого внимания",
                "Формула": "Высокая эффективность: нет красных флагов; Зона развития: есть красный флаг, но балл >=80%; Зона риска: есть красный флаг и балл <80%; Недостаточно данных: нет KPI или доступно меньше 60% веса; отсутствие ОКК дает 0 баллов, но не блокирует расчет",
            },
        ]
    )


def build_page7_tm_data() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    settings = load_settings()
    out_dir = Path(settings["paths"]["out"])

    kpi = pd.read_parquet(out_dir / "kpi_fact.parquet")
    dim_employees = pd.read_parquet(out_dir / "dim_employees.parquet")
    teams = pd.read_parquet(out_dir / "dim_teams.parquet")
    page2_sv = pd.read_parquet(out_dir / "page2_sv_monthly_snapshot.parquet")
    page5_sv = pd.read_parquet(out_dir / "page5_sv_monthly_snapshot.parquet")
    learning_fact = pd.read_parquet(out_dir / "learning_fact.parquet")
    hr_registry_path = out_dir / "fact_hr_registry.parquet"
    hr_registry = pd.read_parquet(hr_registry_path) if hr_registry_path.exists() else pd.DataFrame()

    tm_directory = _build_tm_directory(dim_employees, teams)
    month_calendar = pd.concat(
        [
            kpi[["MonthStart", "YearMonth"]],
            page2_sv[["MonthStart", "YearMonth"]],
            page5_sv[["MonthStart", "YearMonth"]],
        ],
        ignore_index=True,
    ).dropna(subset=["MonthStart", "YearMonth"]).drop_duplicates()
    month_calendar = month_calendar[pd.to_datetime(month_calendar["MonthStart"], errors="coerce").dt.year >= REPORT_START_YEAR].copy()
    sv_tm_map = _build_source_entity_tm_map(page5_sv, "ID супервайзера", tm_directory)
    me_tm_map = _build_source_entity_tm_map(kpi, "ID мерчендайзера", tm_directory)
    last_kpi_month = pd.to_numeric(kpi["YearMonth"], errors="coerce").max()
    future_months = month_calendar[
        pd.to_numeric(month_calendar["YearMonth"], errors="coerce").gt(last_kpi_month)
    ][["MonthStart", "YearMonth"]].drop_duplicates()
    if not future_months.empty:
        current_sv_map = _build_sv_tm_map(teams, tm_directory, future_months)
        observed_sv = page5_sv[["MonthStart", "YearMonth", "ID супервайзера"]].drop_duplicates()
        current_sv_map = current_sv_map.merge(
            observed_sv,
            on=["MonthStart", "YearMonth", "ID супервайзера"],
            how="inner",
        )
        sv_tm_map = _append_missing_entity_mapping(
            sv_tm_map,
            current_sv_map,
            "ID супервайзера",
        )
        me_tm_map = _append_missing_entity_mapping(
            me_tm_map,
            _build_me_tm_map(teams, tm_directory, future_months),
            "ID мерчендайзера",
        )
    team_member_tm_map = pd.concat(
        [
            me_tm_map.rename(columns={"ID мерчендайзера": "ID сотрудника"}).assign(**{"Роль в команде": "МЕ"}),
            sv_tm_map.rename(columns={"ID супервайзера": "ID сотрудника"}).assign(**{"Роль в команде": "СВ"}),
        ],
        ignore_index=True,
    )
    team_member_tm_map = team_member_tm_map.dropna(subset=["ID сотрудника"]).drop_duplicates(
        subset=["MonthStart", "YearMonth", "ID сотрудника", "ID территориального менеджера"]
    )
    tm_learning = _build_tm_learning_monthly(learning_fact, team_member_tm_map)
    tm_personal_learning = _build_tm_personal_learning_monthly(learning_fact, tm_directory)
    mandatory_tm_courses_count = int(
        learning_fact.loc[learning_fact["Обязательный"].eq(True), "Номер курса"].dropna().nunique()
    )
    unassigned_hr_flow = _build_unassigned_hr_flow_by_month(hr_registry)
    tm_profile = tm_directory.set_index("ID территориального менеджера").to_dict("index")
    staffing_path = out_dir / "org_staffing_monthly_snapshot.parquet"
    if staffing_path.exists():
        staffing = pd.read_parquet(staffing_path)
        tm_staffing = staffing[
            staffing["Уровень анализа"].eq("ТМ")
            & staffing["ID территориального менеджера"].notna()
        ].copy()
    else:
        tm_staffing = pd.DataFrame()


    current_sv_me = (
        teams.replace("", pd.NA)[["ID супервайзера", "ID мерчендайзера"]]
        .dropna(subset=["ID супервайзера", "ID мерчендайзера"])
        .drop_duplicates()
    )
    sv_team_size = (
        _cross_join_months(current_sv_me, month_calendar[["MonthStart", "YearMonth"]].drop_duplicates())
        .groupby(["MonthStart", "YearMonth", "ID супервайзера"], dropna=False)["ID мерчендайзера"]
        .nunique()
        .reset_index(name="МЕ под СВ")
    )

    sv_metrics = page5_sv.merge(
        sv_tm_map[
            [
                "MonthStart",
                "YearMonth",
                "ID супервайзера",
                "ID территориального менеджера",
                "Территориальный менеджер",
                "Код ТМ",
                "ТМ / Объект",
                "Регион BI",
                "Группа региона",
            ]
        ],
        on=["MonthStart", "YearMonth", "ID супервайзера"],
        how="left",
        suffixes=("", "_tm"),
    )
    mapped_tm_id_column = "ID территориального менеджера_tm"
    mapped_tm_name_column = "Территориальный менеджер_tm"
    if mapped_tm_id_column in sv_metrics.columns:
        original_tm_id = sv_metrics["ID территориального менеджера"]
        source_tm_id = original_tm_id.replace(NO_TM_ID, pd.NA)
        use_mapped_tm = source_tm_id.isna() & sv_metrics[mapped_tm_id_column].notna()
        sv_metrics["ID территориального менеджера"] = source_tm_id.combine_first(
            sv_metrics[mapped_tm_id_column]
        ).combine_first(original_tm_id)
        if mapped_tm_name_column in sv_metrics.columns:
            sv_metrics.loc[use_mapped_tm, "Территориальный менеджер"] = sv_metrics.loc[
                use_mapped_tm, mapped_tm_name_column
            ]
    sv_metrics = sv_metrics.merge(
        sv_team_size,
        on=["MonthStart", "YearMonth", "ID супервайзера"],
        how="left",
    )
    sv_metrics["МЕ под СВ"] = pd.to_numeric(sv_metrics["МЕ под СВ"], errors="coerce").fillna(0)
    sv_metrics["KPI месяца %"] = _normalize_pct(sv_metrics["KPI месяца %"])
    if "Команда ОЭД %" not in sv_metrics.columns:
        sv_metrics["Команда ОЭД %"] = pd.NA
    sv_metrics["ОКК команды %"] = _normalize_pct(sv_metrics["ОКК команды %"])
    sv_metrics["Фрод %"] = _normalize_pct(sv_metrics["Фрод %"])
    if "Знание продукта %" not in sv_metrics.columns:
        sv_metrics["Знание продукта %"] = sv_metrics.get("Продукт ОЭД %", pd.Series(pd.NA, index=sv_metrics.index))
    if "Аттестация %" not in sv_metrics.columns:
        sv_metrics["Аттестация %"] = sv_metrics.get("Аттестация ОЭД %", pd.Series(pd.NA, index=sv_metrics.index))
    sv_metrics["Команда ОЭД %"] = _normalize_pct(sv_metrics["Команда ОЭД %"])
    sv_metrics["Знание продукта %"] = _normalize_pct(sv_metrics["Знание продукта %"])
    sv_metrics["Аттестация %"] = _normalize_pct(sv_metrics["Аттестация %"])

    quality_sv = page2_sv.merge(
        sv_tm_map[
            [
                "MonthStart",
                "YearMonth",
                "ID супервайзера",
                "ID территориального менеджера",
            ]
        ],
        on=["MonthStart", "YearMonth", "ID супервайзера"],
        how="left",
    )
    if "ID территориального менеджера_x" in quality_sv.columns:
        original_quality_tm_id = quality_sv["ID территориального менеджера_x"]
        quality_sv["ID территориального менеджера"] = original_quality_tm_id.replace(
            NO_TM_ID, pd.NA
        ).combine_first(
            quality_sv.get("ID территориального менеджера_y")
        ).combine_first(original_quality_tm_id)
        quality_sv = quality_sv.drop(columns=["ID территориального менеджера_x", "ID территориального менеджера_y"], errors="ignore")
    for col in ["OSA %", "PICOS %", "ОКК %", "Фрод %"]:
        if col in quality_sv.columns:
            quality_sv[col] = _normalize_pct(quality_sv[col])
    quality_sv = quality_sv.merge(sv_team_size, on=["MonthStart", "YearMonth", "ID супервайзера"], how="left")
    quality_sv["МЕ под СВ"] = pd.to_numeric(quality_sv["МЕ под СВ"], errors="coerce").fillna(0)

    real_tm_ids = set(tm_directory["ID территориального менеджера"].dropna().astype(str).str.strip())
    sv_tm_map = sv_tm_map[
        sv_tm_map["ID территориального менеджера"].astype(str).str.strip().isin(real_tm_ids)
    ].copy()
    me_tm_map = me_tm_map[
        me_tm_map["ID территориального менеджера"].astype(str).str.strip().isin(real_tm_ids)
    ].copy()
    quality_sv = quality_sv[
        quality_sv["ID территориального менеджера"].astype(str).str.strip().isin(real_tm_ids)
    ].copy()

    base_tm = (
        sv_tm_map.groupby(
            [
                "MonthStart",
                "YearMonth",
                "ID территориального менеджера",
                "Территориальный менеджер",
                "Код ТМ",
                "ТМ / Объект",
            ],
            dropna=False,
        )
        .agg(
            **{
                "СВ": ("ID супервайзера", "nunique"),
                "Регионы ТМ": ("Регион BI", _join_unique),
                "Группа региона": ("Группа региона", _mode_or_first),
            }
        )
        .reset_index()
    )

    me_counts = (
        me_tm_map.groupby(
            [
                "MonthStart",
                "YearMonth",
                "ID территориального менеджера",
                "Территориальный менеджер",
                "Код ТМ",
                "ТМ / Объект",
            ],
            dropna=False,
        )
        .agg(
            **{
                "МЕ": ("ID мерчендайзера", "nunique"),
                "Регионы ТМ": ("Регион BI", _join_unique),
                "Группа региона": ("Группа региона", _mode_or_first),
            }
        )
        .reset_index()
    )

    tm_key_columns = [
        "MonthStart",
        "YearMonth",
        "ID территориального менеджера",
        "Территориальный менеджер",
        "Код ТМ",
        "ТМ / Объект",
    ]
    tm_keys = (
        pd.concat([base_tm[tm_key_columns], me_counts[tm_key_columns]], ignore_index=True)
        .drop_duplicates()
        .reset_index(drop=True)
    )

    rows: list[dict] = []
    for _, key_row in tm_keys.iterrows():
        month_start = key_row["MonthStart"]
        year_month = key_row["YearMonth"]
        tm_id = key_row["ID территориального менеджера"]
        tm_name = key_row["Территориальный менеджер"]
        tm_code = key_row["Код ТМ"]
        tm_object = key_row["ТМ / Объект"]
        key_mask = (
            (base_tm["MonthStart"] == month_start)
            & (base_tm["YearMonth"] == year_month)
            & (base_tm["ID территориального менеджера"] == tm_id)
        )
        tm_row = base_tm[key_mask].copy()
        me_match = me_counts[
            (me_counts["MonthStart"] == month_start)
            & (me_counts["YearMonth"] == year_month)
            & (me_counts["ID территориального менеджера"] == tm_id)
        ].copy()
        regions_tm = _join_unique(
            pd.concat(
                [
                    tm_row["Регионы ТМ"] if "Регионы ТМ" in tm_row.columns else pd.Series(dtype="object"),
                    me_match["Регионы ТМ"] if "Регионы ТМ" in me_match.columns else pd.Series(dtype="object"),
                ],
                ignore_index=True,
            )
        )
        region_group = _mode_or_first(
            pd.concat(
                [
                    tm_row["Группа региона"] if "Группа региона" in tm_row.columns else pd.Series(dtype="object"),
                    me_match["Группа региона"] if "Группа региона" in me_match.columns else pd.Series(dtype="object"),
                ],
                ignore_index=True,
            )
        )

        sv_part = sv_metrics[
            (sv_metrics["MonthStart"] == month_start)
            & (sv_metrics["YearMonth"] == year_month)
            & (sv_metrics["ID территориального менеджера"] == tm_id)
        ].copy()

        quality_part = quality_sv[
            (quality_sv["MonthStart"] == month_start)
            & (quality_sv["YearMonth"] == year_month)
            & (quality_sv["ID территориального менеджера"] == tm_id)
        ].copy()

        learn_part = tm_learning[
            (tm_learning["MonthStart"] == month_start)
            & (tm_learning["YearMonth"] == year_month)
            & (tm_learning["ID территориального менеджера"] == tm_id)
        ].copy()
        personal_learn_part = tm_personal_learning[
            (tm_personal_learning["ID территориального менеджера"] == tm_id)
            & (pd.to_datetime(tm_personal_learning["Месяц назначения"], errors="coerce") <= pd.to_datetime(month_start))
        ].copy()

        sv_count = int(tm_row["СВ"].iloc[0]) if not tm_row.empty else 0
        me_count = int(me_match["МЕ"].iloc[0]) if not me_match.empty else 0
        staff_part = tm_staffing[
            (tm_staffing["MonthStart"] == month_start)
            & (tm_staffing["YearMonth"] == year_month)
            & (tm_staffing["ID территориального менеджера"] == tm_id)
        ].copy() if not tm_staffing.empty else pd.DataFrame()

        kpi_pct = _weighted_mean(sv_part, "KPI месяца %", "МЕ под СВ")
        team_rating_pct = _weighted_mean(sv_part, "Команда ОЭД %", "МЕ под СВ")
        okk_pct = _weighted_mean(sv_part, "ОКК команды %", "МЕ под СВ")
        osa_pct = _weighted_mean(quality_part, "OSA %", "МЕ под СВ")
        picos_pct = _weighted_mean(quality_part, "PICOS %", "МЕ под СВ")
        product_pct = _safe_mean(
            [
                _weighted_mean(sv_part, "Знание продукта %", "МЕ под СВ"),
                _weighted_mean(sv_part, "Аттестация %", "МЕ под СВ"),
            ]
        )
        learning_pct = _first_notna(learn_part["Обучение %"]) if not learn_part.empty else pd.NA
        is_real_tm = str(tm_id) != NO_TM_ID
        if not is_real_tm or mandatory_tm_courses_count <= 0:
            tm_courses_assigned = pd.NA
            tm_courses_done = pd.NA
            tm_learning_pct = pd.NA
            tm_test_score = pd.NA
        else:
            completed_tm = personal_learn_part[
                personal_learn_part["Пройдено"].eq(True)
                & pd.to_datetime(personal_learn_part["Месяц завершения"], errors="coerce").le(pd.to_datetime(month_start))
            ].copy()
            tm_courses_assigned = mandatory_tm_courses_count
            tm_courses_done = int(completed_tm["Номер курса"].dropna().nunique()) if not completed_tm.empty else 0
            tm_learning_pct = (
                tm_courses_done / tm_courses_assigned if tm_courses_assigned > 0 else pd.NA
            )
            tm_test_score = (
                completed_tm["Балл теста норм"].mean()
                if completed_tm["Балл теста норм"].notna().any()
                else pd.NA
            )
        fraud_pct = _weighted_mean(sv_part, "Фрод %", "МЕ под СВ")
        fraud_count = pd.to_numeric(sv_part["Фрод команды"], errors="coerce").fillna(0).sum() if not sv_part.empty else 0
        candidate_count = int(sv_part["Резерв"].eq("кандидат").sum()) if "Резерв" in sv_part.columns else 0
        potential_count = int(sv_part["Резерв"].eq("потенциал").sum()) if "Резерв" in sv_part.columns else 0
        reserve_count = candidate_count + potential_count
        reserve_share = reserve_count / sv_count if sv_count else pd.NA
        active_me = _sum_numeric(staff_part, "Активных МЕ", default=pd.NA)
        active_sv = _sum_numeric(staff_part, "Активных СВ", default=pd.NA)

        hired_assigned = _sum_numeric(staff_part, "Нанято", default=0)
        fired_assigned = _sum_numeric(staff_part, "Уволено", default=0)
        open_total = _sum_numeric(staff_part, "Открытых вакансий", default=0)
        open_me = _sum_numeric(staff_part, "Открытых вакансий МЕ", default=0)
        open_sv = _sum_numeric(staff_part, "Открытых вакансий СВ", default=0)
        suspended_open = _sum_numeric(staff_part, "Приостановленных вакансий", default=0)
        unassigned_match = unassigned_hr_flow[
            (unassigned_hr_flow["MonthStart"] == month_start)
            & (unassigned_hr_flow["YearMonth"] == year_month)
        ]
        hired_without_tm = (
            _sum_numeric(unassigned_match, "Нанято без ТМ", default=0)
            if str(tm_id) == NO_TM_ID
            else 0
        )
        fired_without_tm = (
            _sum_numeric(unassigned_match, "Уволено без ТМ", default=0)
            if str(tm_id) == NO_TM_ID
            else 0
        )
        hired = hired_assigned + hired_without_tm
        fired = fired_assigned + fired_without_tm
        net_flow = fired - hired
        staff_balance = hired - fired
        vacancy_snapshot_date = pd.NaT
        active_team = active_me + active_sv if pd.notna(active_me) and pd.notna(active_sv) else pd.NA
        open_positions = open_me + open_sv if pd.notna(open_me) and pd.notna(open_sv) else pd.NA
        staffing_score = _staffing_score(active_team, open_positions, hired, fired)
        planned_team = active_team + open_positions if pd.notna(active_team) and pd.notna(open_positions) else pd.NA
        if pd.notna(planned_team) and planned_team <= 0:
            planned_team = pd.NA
        vacancy_share = open_positions / active_team if pd.notna(active_team) and active_team else pd.NA
        vacancy_share_planned = open_positions / planned_team if pd.notna(planned_team) and planned_team else pd.NA
        turnover = fired / active_team if pd.notna(active_team) and active_team else pd.NA
        net_outflow = max(0, fired - hired)
        net_outflow_share = net_outflow / active_team if pd.notna(active_team) and active_team else pd.NA
        tm_meta = tm_profile.get(tm_id, {})
        hire_date = tm_meta.get("Дата приема ТМ", pd.NA)
        tenure_months = _tenure_months_at(month_start, hire_date)
        tm_city = tm_meta.get("Город ТМ", pd.NA)
        tm_active = tm_meta.get("Активен ТМ", pd.NA)
        primary_region = tm_meta.get("Регион BI", pd.NA)
        if isinstance(regions_tm, str) and "," in regions_tm:
            region_bi = "Несколько регионов"
        else:
            region_bi = regions_tm if pd.notna(regions_tm) else primary_region

        quality_pct = _safe_mean([okk_pct, osa_pct, picos_pct])
        antifraud_pct = (1 - float(fraud_pct)) if pd.notna(fraud_pct) else pd.NA
        operational_values = [
            (kpi_pct, TM_OPERATIONAL_KPI_WEIGHT),
            (quality_pct, TM_OPERATIONAL_QUALITY_WEIGHT),
            (learning_pct, TM_OPERATIONAL_LEARNING_WEIGHT),
            (antifraud_pct, TM_OPERATIONAL_ANTIFRAUD_WEIGHT),
        ]
        operational_available_weight = _available_weight(operational_values)
        operational_score_raw = _weighted_score(operational_values)
        operational_score = (
            operational_score_raw
            if operational_available_weight >= TM_MIN_AVAILABLE_WEIGHT
            else pd.NA
        )
        personal_score = _safe_mean([tm_learning_pct, tm_test_score, product_pct])
        status_input = pd.Series(
            {
                "KPI месяца территории %": kpi_pct,
                "Качество команды %": quality_pct,
                "Обучение команды %": learning_pct,
                "Фрод %": fraud_pct,
                "Стабильность команды %": staffing_score,
                "Текучесть %": turnover,
            }
        )
        score_available_weight = _tm_available_weight_from_row(status_input)
        score = _tm_effectiveness_score_from_row(status_input)
        status_input["Балл эффективности %"] = score
        status_input["Доступность индекса ТМ %"] = score_available_weight
        signal_weights = _tm_signal_weight_summary(status_input)
        status = _status_from_row(status_input)
        reason_input = pd.Series(
            {
                "Статус": status,
                "Балл эффективности %": score,
                "Результат территории %": operational_score,
                "KPI месяца территории %": kpi_pct,
                "Качество команды %": quality_pct,
                "Обучение команды %": learning_pct,
                "Фрод %": fraud_pct,
                "Стабильность команды %": staffing_score,
                "Текучесть %": turnover,
            }
        )
        reason = _status_reason_from_row(reason_input)

        rows.append(
            {
                "MonthStart": month_start,
                "YearMonth": year_month,
                "ID территориального менеджера": tm_id,
                "Территориальный менеджер": tm_name,
                "Код ТМ": tm_code,
                "ТМ / Объект": tm_object,
                "Регион BI": region_bi,
                "Регионы ТМ": regions_tm,
                "Группа региона": region_group,
                "СВ": sv_count,
                "МЕ": me_count,
                "Размер команды": active_team,
                "Активных СВ": active_sv,
                "Активных МЕ": active_me,
                "Активная команда": active_team,
                "Плановая команда": planned_team,
                "Открытых вакансий": open_total,
                "Открытых вакансий МЕ": open_me,
                "Открытых вакансий СВ": open_sv,
                "Приостановленных вакансий": suspended_open,
                "Дата среза вакансий": vacancy_snapshot_date,
                "Нанято с ТМ": hired_assigned,
                "Уволено с ТМ": fired_assigned,
                "Нанято без ТМ": hired_without_tm,
                "Уволено без ТМ": fired_without_tm,
                "Нанято": hired,
                "Уволено": fired,
                "Чистый отток": net_flow,
                "Баланс персонала": staff_balance,
                "Кадровый отток": net_outflow,
                "Доля вакансий к активным МЕ %": vacancy_share,
                "Доля вакансий от плановой команды %": vacancy_share_planned,
                "Текучесть территории %": turnover,
                "Чистый отток от плановой команды %": net_outflow_share,
                "Кадровая устойчивость %": staffing_score,
                "Статус кадровой устойчивости": _zone_from_score(staffing_score),
                "KPI месяца территории %": kpi_pct,
                "ОКК %": okk_pct,
                "OSA %": osa_pct,
                "PICOS %": picos_pct,
                "Качество территории %": quality_pct,
                "Качество команды %": quality_pct,
                "Обучение %": learning_pct,
                "Обучение команды %": learning_pct,
                "Антифрод %": antifraud_pct,
                "Обучение ТМ %": tm_learning_pct,
                "Назначено обязательных курсов ТМ": tm_courses_assigned,
                "Пройдено обязательных курсов ТМ": tm_courses_done,
                "Средний балл теста ТМ %": tm_test_score,
                "Дата приема ТМ": hire_date,
                "Стаж ТМ, мес.": tenure_months,
                "Стаж, мес.": tenure_months,
                "Город ТМ": tm_city,
                "Активен ТМ": tm_active,
                "Рейтинг команды %": team_rating_pct,
                "Продукт %": product_pct,
                "Фрод": int(fraud_count),
                "Фрод %": fraud_pct,
                "Кандидат СВ": candidate_count,
                "Потенциал СВ": potential_count,
                "Резерв СВ": reserve_count,
                "Резерв СВ %": reserve_share,
                "Доступность индекса ТМ %": score_available_weight,
                "Вес красных флагов ТМ %": signal_weights["Вес красных флагов ТМ %"],
                "Вес желтых флагов ТМ %": signal_weights["Вес желтых флагов ТМ %"],
                "Приоритетный вес проблем ТМ %": signal_weights["Приоритетный вес проблем ТМ %"],
                "Балл эффективности %": score,
                "Балл эффективности": round(score * 100) if pd.notna(score) else pd.NA,
                "Результат территории %": operational_score,
                "Стабильность команды %": staffing_score,
                "Текучесть %": turnover,
                "Доля вакансий %": vacancy_share_planned,
                "Статус ТМ": status,
                "Причина статуса ТМ": reason,
            }
        )

    snapshot = pd.DataFrame(rows).sort_values(["MonthStart", "Территориальный менеджер"]).reset_index(drop=True)
    composition = _build_score_composition()

    numeric_columns = [
        "YearMonth",
        "СВ",
        "МЕ",
        "Размер команды",
        "Активных СВ",
        "Активных МЕ",
        "Активная команда",
        "Плановая команда",
        "Открытых вакансий",
        "Открытых вакансий МЕ",
        "Открытых вакансий СВ",
        "Приостановленных вакансий",
        "Нанято с ТМ",
        "Уволено с ТМ",
        "Нанято без ТМ",
        "Уволено без ТМ",
        "Нанято",
        "Уволено",
        "Чистый отток",
        "Баланс персонала",
        "Кадровый отток",
        "Доля вакансий к активным МЕ %",
        "Доля вакансий от плановой команды %",
        "Текучесть территории %",
        "Чистый отток от плановой команды %",
        "Кадровая устойчивость %",
        "KPI месяца территории %",
        "Целевой порог KPI территории %",
        "ОКК %",
        "OSA %",
        "PICOS %",
        "Качество территории %",
        "Качество команды %",
        "Обучение %",
        "Обучение команды %",
        "Антифрод %",
        "Обучение ТМ %",
        "Назначено обязательных курсов ТМ",
        "Пройдено обязательных курсов ТМ",
        "Средний балл теста ТМ %",
        "Стаж ТМ, мес.",
        "Стаж, мес.",
        "Рейтинг команды %",
        "Продукт %",
        "Фрод",
        "Фрод %",
        "Кандидат СВ",
        "Потенциал СВ",
        "Резерв СВ",
        "Резерв СВ %",
        "Доступность индекса ТМ %",
        "Вес красных флагов ТМ %",
        "Вес желтых флагов ТМ %",
        "Приоритетный вес проблем ТМ %",
        "Балл эффективности %",
        "Балл эффективности",
        "Результат территории %",
        "Стабильность команды %",
        "Текучесть %",
        "Доля вакансий %",
    ]
    for column in numeric_columns:
        if column in snapshot.columns:
            snapshot[column] = pd.to_numeric(snapshot[column], errors="coerce")

    public_columns = [
        "MonthStart",
        "YearMonth",
        "ID территориального менеджера",
        "Код ТМ",
        "Территориальный менеджер",
        "Регион BI",
        "Регионы ТМ",
        "Группа региона",
        "СВ",
        "МЕ",
        "Размер команды",
        "Балл эффективности",
        "Статус ТМ",
        "Причина статуса ТМ",
        "Вес красных флагов ТМ %",
        "Вес желтых флагов ТМ %",
        "Приоритетный вес проблем ТМ %",
        "Доступность индекса ТМ %",
        "Результат территории %",
        "Стабильность команды %",
        "KPI месяца территории %",
        "Целевой порог KPI территории %",
        "Качество команды %",
        "ОКК %",
        "Обучение команды %",
        "Фрод %",
        "Фрод",
        "Открытых вакансий",
        "Открытых вакансий МЕ",
        "Открытых вакансий СВ",
        "Нанято",
        "Уволено",
        "Баланс персонала",
        "Текучесть %",
        "Доля вакансий %",
        "Резерв СВ",
        "Резерв СВ %",
        "Обучение ТМ %",
        "Назначено обязательных курсов ТМ",
        "Пройдено обязательных курсов ТМ",
        "Средний балл теста ТМ %",
        "Стаж, мес.",
        "Дата приема ТМ",
        "Нанято без ТМ",
        "Уволено без ТМ",
    ]
    snapshot = snapshot[[column for column in public_columns if column in snapshot.columns]].copy()
    employee_kpi_path = out_dir / "kpi_employee_monthly_metrics.parquet"
    page3_path = out_dir / "page3_merch_monthly_snapshot.parquet"
    if employee_kpi_path.exists() and page3_path.exists():
        tm_kpi_metrics = aggregate_employee_kpi_to_org(
            pd.read_parquet(employee_kpi_path),
            pd.read_parquet(page3_path),
            "ID территориального менеджера",
        )
        snapshot = snapshot.merge(
            tm_kpi_metrics,
            on=["MonthStart", "YearMonth", "ID территориального менеджера"],
            how="left",
        )
        snapshot = _refresh_tm_effectiveness(snapshot)

    final_public_columns = [
        "MonthStart",
        "YearMonth",
        "ID территориального менеджера",
        "Код ТМ",
        "Территориальный менеджер",
        "Регион BI",
        "Регионы ТМ",
        "Группа региона",
        "СВ",
        "МЕ",
        "Размер команды",
        "Балл эффективности",
        "Статус ТМ",
        "Причина статуса ТМ",
        "Вес красных флагов ТМ %",
        "Вес желтых флагов ТМ %",
        "Приоритетный вес проблем ТМ %",
        "Доступность индекса ТМ %",
        "Результат территории %",
        "Стабильность команды %",
        "KPI месяца территории %",
        "Целевой порог KPI территории %",
        "Качество команды %",
        "Обучение команды %",
        "Фрод %",
        "Фрод",
        "Открытых вакансий",
        "Открытых вакансий МЕ",
        "Открытых вакансий СВ",
        "Нанято",
        "Уволено",
        "Баланс персонала",
        "Текучесть %",
        "Доля вакансий %",
        "Резерв СВ",
        "Резерв СВ %",
        "Обучение ТМ %",
        "Назначено обязательных курсов ТМ",
        "Пройдено обязательных курсов ТМ",
        "Средний балл теста ТМ %",
        "Стаж, мес.",
        "Дата приема ТМ",
        "Нанято без ТМ",
        "Уволено без ТМ",
        "PICOS план",
        "PICOS факт",
        "PICOS выполнение %",
        "OSA план %",
        "OSA факт %",
        "OSA выполнение %",
        "TOP16 план %",
        "TOP16 факт %",
        "TOP16 выполнение %",
    ]
    snapshot = snapshot[[column for column in final_public_columns if column in snapshot.columns]].copy()

    save_parquet(snapshot, str(out_dir / "page7_tm_monthly_snapshot.parquet"))
    save_parquet(tm_directory, str(out_dir / "dTM.parquet"))
    save_parquet(composition, str(out_dir / "page7_tm_score_composition.parquet"))

    print(f"\n  Page7 TM snapshot: {len(snapshot)} строк")
    print(f"  Page7 TM directory: {len(tm_directory)} строк")
    print(f"  Page7 TM composition: {len(composition)} строк")
    return snapshot, tm_directory, composition


if __name__ == "__main__":
    build_page7_tm_data()
