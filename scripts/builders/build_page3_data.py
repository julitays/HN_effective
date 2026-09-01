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
    first_notna as _first_notna,
    extract_sv_code as _extract_sv_code,
)
from scripts.staffing_utils import (
    attach_last_quarter_metric as _attach_last_quarter_metric,
    build_enps_quarterly as _build_enps_quarterly,
    missing_supervisor_keys,
    normalize_confirmed_tm,
)
from scripts.kpi_metric_utils import (
    KPI_COMPONENT_COLUMNS,
    KPI_SCORE_WEIGHT_COLUMNS,
    pivot_employee_kpi_metrics,
)
from scripts.kpi_org_mapping import build_rtm_month_org


_REPORTING_CFG = load_settings()["reporting"]
REPORT_START_YEAR = _REPORTING_CFG["start_yearmonth"] // 100
CLIENT_ATTESTATION_QUARTERS = _REPORTING_CFG["client_attestation_quarters"]

TARGET_KPI = 0.75
TARGET_OKK = 0.60
TARGET_LEARN = 0.75
TARGET_FRAUD = 0.10
TARGET_OSA = 0.85
TARGET_PICOS = 0.85
NO_SV_ID = "NO_SV"
NO_SV_NAME = "Вакансия / нет СВ"

MERCH_PERSONAL_HIGH_MIN_SCORE = 0.90
MERCH_PERSONAL_ROLE_MIN_SCORE = 0.80
MERCH_PERSONAL_MIN_AVAILABLE_WEIGHT = 0.60
MERCH_KPI_SCORE_POINTS = 40
MERCH_KPI_TARGET = 0.99
MERCH_KPI_RED_THRESHOLD = 0.95
MERCH_PERSONAL_SCORE_POINTS = {
    "ОКК %": 15,
    "Обучение %": 20,
    "Аттестация клиента %": 25,
}
MERCH_PERSONAL_TARGETS = {
    "ОКК %": 0.60,
    "Обучение %": 0.95,
    "Аттестация клиента %": 0.95,
}
MERCH_PERSONAL_RED_THRESHOLDS = {
    "ОКК %": 0.40,
    "Обучение %": 0.90,
    "Аттестация клиента %": 0.90,
}
MERCH_KPI_COMPONENTS = {
    "PICOS выполнение %": "PICOS вес в KPI %",
    "OSA выполнение %": "OSA вес в KPI %",
    "TOP16 выполнение %": "TOP16 вес в KPI %",
}


def _max_numeric(series: pd.Series):
    values = pd.to_numeric(series, errors="coerce").dropna()
    return values.max() if not values.empty else pd.NA


def _short_fi(series: pd.Series) -> pd.Series:
    parts = series.fillna("").astype(str).str.split()
    return parts.map(lambda items: " ".join(items[:2]) if items else "")


def _build_merch_client_attestation_quarterly(attestations: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "ID мерчендайзера",
        "QuarterStart аттестации клиента",
        "YearQuarter аттестации клиента",
        "QuarterLabel аттестации клиента",
        "Аттестация клиента %",
        "Статус аттестации клиента",
        "Дата аттестации клиента",
    ]
    if attestations is None or attestations.empty:
        return pd.DataFrame(columns=columns)

    required = {"ID сотрудника", "Уровень сотрудника", "QuarterStart"}
    if not required.issubset(attestations.columns):
        return pd.DataFrame(columns=columns)

    work = attestations.copy().replace("", pd.NA)
    work = work[
        work["Уровень сотрудника"].eq("МЕ")
        & work["ID сотрудника"].notna()
        & pd.to_datetime(work["QuarterStart"], errors="coerce").notna()
    ].copy()
    if work.empty:
        return pd.DataFrame(columns=columns)

    work["ID мерчендайзера"] = work["ID сотрудника"].astype(str).str.strip()
    work["QuarterStart аттестации клиента"] = pd.to_datetime(work["QuarterStart"], errors="coerce")
    work["YearQuarter аттестации клиента"] = pd.to_numeric(work.get("YearQuarter"), errors="coerce").astype("Int64")
    work["QuarterLabel аттестации клиента"] = work.get("QuarterLabel", pd.Series(pd.NA, index=work.index))
    work["Аттестация клиента %"] = _normalize_pct(work.get("Аттестация клиента %", pd.Series(pd.NA, index=work.index)))
    work["Статус аттестации клиента"] = work.get("Статус аттестации клиента", pd.Series(pd.NA, index=work.index))
    work["Дата аттестации клиента"] = pd.to_datetime(
        work.get("Дата завершения", pd.Series(pd.NaT, index=work.index)),
        errors="coerce",
    )
    for date_column in ["Дата начала", "Дата заявки"]:
        if date_column in work.columns:
            fallback_date = pd.to_datetime(work[date_column], errors="coerce")
            work["Дата аттестации клиента"] = work["Дата аттестации клиента"].combine_first(fallback_date)

    result = (
        work.sort_values(["ID мерчендайзера", "QuarterStart аттестации клиента", "Дата аттестации клиента"])
        .groupby(["ID мерчендайзера", "QuarterStart аттестации клиента"], dropna=False)
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


def _build_merch_client_attestation_wide(attestation_quarterly: pd.DataFrame) -> pd.DataFrame:
    columns = ["ID мерчендайзера"] + list(CLIENT_ATTESTATION_QUARTERS.values())
    if attestation_quarterly is None or attestation_quarterly.empty:
        return pd.DataFrame(columns=columns)

    quarter_map = CLIENT_ATTESTATION_QUARTERS
    work = attestation_quarterly.copy()
    work["YearQuarter аттестации клиента"] = pd.to_numeric(work["YearQuarter аттестации клиента"], errors="coerce")
    work = work[work["YearQuarter аттестации клиента"].isin(quarter_map)].copy()
    if work.empty:
        return pd.DataFrame(columns=columns)

    work["Аттестация квартал"] = work["YearQuarter аттестации клиента"].map(quarter_map)

    wide = (
        work.pivot_table(
            index="ID мерчендайзера",
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


def _merge_asof_merch_client_attestation(
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
        for column in attach_cols:
            result[column] = pd.NA
        return result

    left = monthly.copy()
    left["_merch_key"] = left["ID мерчендайзера"].astype("string")
    left["_source_order"] = np.arange(len(left))
    right = attestation_quarterly.copy()
    right["_merch_key"] = right["ID мерчендайзера"].astype("string")

    merged = pd.merge_asof(
        left.sort_values(["MonthStart", "_merch_key"]),
        right.sort_values(["QuarterStart аттестации клиента", "_merch_key"])[
            ["_merch_key", "QuarterStart аттестации клиента"] + attach_cols
        ],
        left_on="MonthStart",
        right_on="QuarterStart аттестации клиента",
        by="_merch_key",
        direction="backward",
    )
    return (
        merged.sort_values("_source_order")
        .drop(columns=["_merch_key", "_source_order"])
        .reset_index(drop=True)
    )


def _merch_personal_metric_label(row: pd.Series, metric: str) -> str:
    if metric == "KPI проекта %":
        weak_components = []
        for component in ["PICOS", "OSA", "TOP16"]:
            value = pd.to_numeric(row.get(f"{component} выполнение %"), errors="coerce")
            if pd.notna(value) and float(value) < MERCH_PERSONAL_TARGETS["KPI проекта %"]:
                weak_components.append((float(value), component))
        if weak_components:
            labels = [component for _, component in sorted(weak_components)[:2]]
            return "KPI — " + " / ".join(labels)
        return "KPI проекта"

    if metric != "Аттестация клиента %":
        return metric.replace(" %", "")

    quarter_label = row.get("QuarterLabel аттестации клиента")
    if pd.notna(quarter_label) and str(quarter_label).strip():
        return f"Аттестация клиента {str(quarter_label).strip()}"
    attestation_value = row.get("Аттестация клиента %")
    month_start = pd.to_datetime(row.get("MonthStart"), errors="coerce")
    row_year_quarter = None
    if pd.notna(month_start):
        row_year_quarter = int(month_start.year * 10 + ((month_start.month - 1) // 3 + 1))

    matching_quarters = []
    for year_quarter, column in CLIENT_ATTESTATION_QUARTERS.items():
        label = column.removeprefix("Аттестация клиента ").removesuffix(" %")
        quarter_value = row.get(column)
        if pd.notna(attestation_value) and pd.notna(quarter_value):
            if abs(float(attestation_value) - float(quarter_value)) <= 0.0001:
                matching_quarters.append((int(year_quarter), label))
    if matching_quarters:
        if row_year_quarter is not None:
            eligible = [item for item in matching_quarters if item[0] <= row_year_quarter]
            if eligible:
                return f"Аттестация клиента {max(eligible)[1]}"
        return f"Аттестация клиента {max(matching_quarters)[1]}"
    return "Аттестация клиента"


def _merch_personal_available_weight_from_row(row: pd.Series) -> float:
    available_points = float(MERCH_KPI_SCORE_POINTS) if _merch_kpi_block_available(row) else 0.0
    for metric, points in MERCH_PERSONAL_SCORE_POINTS.items():
        if pd.notna(row.get(metric)):
            available_points += points
    return round(available_points / 100, 4)


def _merch_metric_score_factor(value, target: float, red_threshold: float) -> float:
    if pd.isna(value) or float(value) < red_threshold:
        return 0.0
    return max(0.0, min(1.0, float(value) / target))


def _merch_kpi_block_available(row: pd.Series) -> bool:
    return _merch_kpi_active_weight(row) > 0


def _merch_kpi_active_weight(row: pd.Series) -> float:
    total_weight = 0.0
    for metric, weight_column in MERCH_KPI_COMPONENTS.items():
        value = pd.to_numeric(row.get(metric), errors="coerce")
        weight = pd.to_numeric(row.get(weight_column), errors="coerce")
        if pd.notna(value) and pd.notna(weight) and float(weight) > 0:
            total_weight += float(weight)
    return total_weight


def _merch_kpi_score_factor(row: pd.Series) -> float:
    active_weight = _merch_kpi_active_weight(row)
    if active_weight <= 0:
        return np.nan
    result = 0.0
    for metric, weight_column in MERCH_KPI_COMPONENTS.items():
        value = pd.to_numeric(row.get(metric), errors="coerce")
        weight = pd.to_numeric(row.get(weight_column), errors="coerce")
        if pd.isna(value) or pd.isna(weight) or float(weight) <= 0:
            continue
        result += float(weight) * _merch_metric_score_factor(
            value,
            MERCH_KPI_TARGET,
            MERCH_KPI_RED_THRESHOLD,
        )
    return max(0.0, min(1.0, result / active_weight))


def _merch_personal_score_from_row(row: pd.Series) -> float:
    kpi_factor = _merch_kpi_score_factor(row)
    if pd.isna(kpi_factor):
        return np.nan
    score_value = float(MERCH_KPI_SCORE_POINTS) * kpi_factor
    has_metric = True
    for metric, points in MERCH_PERSONAL_SCORE_POINTS.items():
        value = row.get(metric)
        if pd.isna(value):
            if metric == "Аттестация клиента %":
                score_value += points
                has_metric = True
            continue
        has_metric = True
        score_value += _merch_metric_score_factor(
            value,
            MERCH_PERSONAL_TARGETS[metric],
            MERCH_PERSONAL_RED_THRESHOLDS[metric],
        ) * points
    return round(score_value / 100, 8) if has_metric else np.nan


def _merch_personal_weak_metric_records(row: pd.Series) -> list[dict]:
    records = []
    for metric, weight_column in MERCH_KPI_COMPONENTS.items():
        value = pd.to_numeric(row.get(metric), errors="coerce")
        component_weight = pd.to_numeric(row.get(weight_column), errors="coerce")
        if (
            pd.isna(value)
            or pd.isna(component_weight)
            or float(component_weight) <= 0
            or float(value) >= MERCH_KPI_TARGET
        ):
            continue
        severity = (
            "red"
            if float(value) < MERCH_KPI_RED_THRESHOLD
            else "yellow"
        )
        gap = (MERCH_KPI_TARGET - float(value)) / MERCH_KPI_TARGET
        records.append(
            {
                "metric": metric,
                "label": metric.replace(" выполнение %", ""),
                "weight": (MERCH_KPI_SCORE_POINTS / 100) * float(component_weight),
                "severity": severity,
                "priority": (MERCH_KPI_SCORE_POINTS / 100) * float(component_weight) * gap,
            }
        )
    for metric, target in MERCH_PERSONAL_TARGETS.items():
        value = row.get(metric)
        if pd.isna(value) or value >= target:
            continue
        red_threshold = MERCH_PERSONAL_RED_THRESHOLDS[metric]
        weight = MERCH_PERSONAL_SCORE_POINTS[metric] / 100
        severity = "red" if value < red_threshold else "yellow"
        gap = (target - float(value)) / target
        records.append(
            {
                "metric": metric,
                "label": _merch_personal_metric_label(row, metric),
                "weight": weight,
                "severity": severity,
                "priority": weight * gap,
            }
        )
    return sorted(records, key=lambda item: item["priority"], reverse=True)


def _merch_personal_status_from_row(row: pd.Series) -> str:
    tenure_months = pd.to_numeric(row.get("Стаж МЕ, мес."), errors="coerce")
    if pd.notna(tenure_months) and float(tenure_months) < 3:
        return "Новичок"

    score = row.get("Личная эффективность МЕ %")
    available_weight = row.get("Доступность личных метрик %")
    missing_critical = not _merch_kpi_block_available(row)

    if pd.isna(score) or pd.isna(available_weight) or available_weight < MERCH_PERSONAL_MIN_AVAILABLE_WEIGHT:
        return "Недостаточно данных"
    if missing_critical:
        return "Недостаточно данных"

    weak_records = _merch_personal_weak_metric_records(row)
    weak_count = len(weak_records)
    has_red_metric = any(record["severity"] == "red" for record in weak_records)

    if has_red_metric:
        return "Зона развития"
    if score < MERCH_PERSONAL_ROLE_MIN_SCORE:
        return "Недостаточно данных"
    if (
        score >= MERCH_PERSONAL_HIGH_MIN_SCORE
        and available_weight >= 0.999
        and weak_count == 0
    ):
        return "Высокая личная готовность"
    return "Соответствует роли"


def _merch_personal_reason_from_row(row: pd.Series):
    red_records = [
        record
        for record in _merch_personal_weak_metric_records(row)
        if record["severity"] == "red"
    ]
    labels = list(dict.fromkeys(record["label"] for record in red_records))
    if not labels:
        return pd.NA
    return ", ".join(labels[:3])


def _build_learning_merch_cumulative(
    learning_fact: pd.DataFrame,
    allowed_months: list[pd.Timestamp],
) -> pd.DataFrame:
    work = learning_fact.copy()
    work = work[work["Обязательный"] == True].copy()
    work["StartMonth"] = pd.to_datetime(work["StartMonth"], errors="coerce")

    rows: list[pd.DataFrame] = []
    for month_start in allowed_months:
        snapshot = work[work["StartMonth"] <= month_start].copy()
        if snapshot.empty:
            continue
        grouped = (
            snapshot.groupby(
                ["ID сотрудника", "Регион BI"],
                dropna=False,
            )
            .agg(
                **{
                    "Назначено обязательных курсов": ("Номер курса", "count"),
                    "Пройдено обязательных курсов": ("Пройдено", lambda s: s.eq(True).sum()),
                }
            )
            .reset_index()
        )
        grouped["MonthStart"] = month_start
        grouped["YearMonth"] = month_start.year * 100 + month_start.month
        grouped["Обучение %"] = grouped["Пройдено обязательных курсов"] / grouped["Назначено обязательных курсов"]
        rows.append(grouped)

    if not rows:
        return pd.DataFrame(
            columns=[
                "ID сотрудника",
                "Регион BI",
                "MonthStart",
                "YearMonth",
                "Назначено обязательных курсов",
                "Пройдено обязательных курсов",
                "Обучение %",
            ]
        )

    return pd.concat(rows, ignore_index=True)


def _build_tt_complexity_monthly(
    okk: pd.DataFrame,
    allowed_months: list[pd.Timestamp],
) -> pd.DataFrame:
    if okk.empty:
        return pd.DataFrame(
            columns=[
                "MonthStart",
                "YearMonth",
                "Код ТТ",
                "Сложность ТТ score",
                "Сложная ТТ",
            ]
        )

    work = okk[okk["MonthStart"].isin(set(allowed_months))].copy()
    if work.empty:
        return pd.DataFrame(
            columns=[
                "MonthStart",
                "YearMonth",
                "Код ТТ",
                "Сложность ТТ score",
                "Сложная ТТ",
            ]
        )

    tt_monthly = (
        work.groupby(["MonthStart", "YearMonth", "Код ТТ"], dropna=False)
        .agg(
            **{
                "OSA %": ("% наличия товара на полке", "mean"),
                "PICOS %": ("% наличия PICoS", "mean"),
                "ОКК %": ("Качество визита", "mean"),
                "Фрод %": ("Флаг фальсификации", "mean"),
                "Золотая полка %": (
                    "Стандарты: ТОП-16 на золотых полках",
                    "mean",
                ),
                "МЕ на ТТ": ("ID мерчендайзера", "nunique"),
            }
        )
        .reset_index()
    )

    tt_monthly["Проблема KPI/ТТ"] = (
        (tt_monthly["ОКК %"] < TARGET_OKK)
        | (tt_monthly["OSA %"] < TARGET_OSA)
        | (tt_monthly["PICOS %"] < TARGET_PICOS)
        | (tt_monthly["Фрод %"] > TARGET_FRAUD)
    )
    tt_monthly["OKK нарушение"] = tt_monthly["ОКК %"] < TARGET_OKK

    tt_monthly = tt_monthly.sort_values(["Код ТТ", "MonthStart"]).reset_index(drop=True)
    grouped = tt_monthly.groupby("Код ТТ", dropna=False, sort=False)

    def rolling_mean(column: str) -> pd.Series:
        return grouped[column].rolling(3, min_periods=1).mean().reset_index(level=0, drop=True)

    def rolling_range(column: str) -> pd.Series:
        rolling = grouped[column].rolling(3, min_periods=1)
        return (
            (rolling.max() - rolling.min())
            .reset_index(level=0, drop=True)
            .div(0.25)
            .clip(lower=0.0, upper=1.0)
            .fillna(0.0)
        )

    repeated_problem_score = rolling_mean("Проблема KPI/ТТ") * 35
    osa_instability = rolling_range("OSA %")
    picos_instability = rolling_range("PICOS %")
    instability_score = (osa_instability + picos_instability) / 2 * 25
    okk_repeat_score = rolling_mean("OKK нарушение") * 20
    shelf_score = (1 - rolling_mean("Золотая полка %")) * 10
    shelf_score = shelf_score.fillna(0.0)

    merch_sets = (
        work.dropna(subset=["ID мерчендайзера"])
        .groupby(["Код ТТ", "MonthStart"], dropna=False)["ID мерчендайзера"]
        .agg(lambda values: frozenset(values.unique()))
        .to_dict()
    )
    route_scores = np.zeros(len(tt_monthly), dtype=float)
    for _, indexes in grouped.indices.items():
        ordered_indexes = list(indexes)
        recent_sets: list[frozenset] = []
        for row_index in ordered_indexes:
            row = tt_monthly.iloc[row_index]
            recent_sets.append(merch_sets.get((row["Код ТТ"], row["MonthStart"]), frozenset()))
            recent_sets = recent_sets[-3:]
            unique_merch = len(frozenset().union(*recent_sets)) if recent_sets else 0
            route_scores[row_index] = min(1.0, max(0.0, (unique_merch - 1) / 2)) * 10

    complexity_score = (
        repeated_problem_score
        + instability_score
        + okk_repeat_score
        + shelf_score
        + route_scores
    )
    return pd.DataFrame(
        {
            "MonthStart": tt_monthly["MonthStart"],
            "YearMonth": tt_monthly["YearMonth"],
            "Код ТТ": tt_monthly["Код ТТ"],
            "Сложность ТТ score": complexity_score.round(1),
            "Сложная ТТ": complexity_score.ge(45),
        }
    )


def _first_confirmed_visits_for_current_episode(
    rtm_visits: pd.DataFrame,
    dim_employees: pd.DataFrame,
) -> pd.DataFrame:
    required_rtm = {
        "ID сотрудника",
        "Дата визита",
        "Визит выполнен",
        "Визит подтверждён",
    }
    missing_rtm = required_rtm.difference(rtm_visits.columns)
    if missing_rtm:
        raise KeyError(f"В RTM отсутствуют обязательные поля: {sorted(missing_rtm)}")
    required_users = {"ID сотрудника", "Дата приёма"}
    missing_users = required_users.difference(dim_employees.columns)
    if missing_users:
        raise KeyError(f"В USERS отсутствуют обязательные поля: {sorted(missing_users)}")

    visits = rtm_visits[
        rtm_visits["Визит выполнен"].fillna(False).eq(True)
        & rtm_visits["Визит подтверждён"].fillna(False).eq(True)
    ][["ID сотрудника", "Дата визита"]].copy()
    visits["Дата визита"] = pd.to_datetime(visits["Дата визита"], errors="coerce")
    visits = visits.dropna(subset=["ID сотрудника", "Дата визита"])

    hires = (
        dim_employees[["ID сотрудника", "Дата приёма"]]
        .dropna(subset=["ID сотрудника"])
        .drop_duplicates("ID сотрудника")
        .copy()
    )
    hires["Дата приёма"] = pd.to_datetime(hires["Дата приёма"], errors="coerce")
    visits = visits.merge(hires, on="ID сотрудника", how="inner")
    visits = visits[
        visits["Дата приёма"].isna()
        | visits["Дата визита"].ge(visits["Дата приёма"])
    ].copy()

    return (
        visits.groupby("ID сотрудника", as_index=False)["Дата визита"]
        .min()
        .rename(
            columns={
                "ID сотрудника": "ID мерчендайзера",
                "Дата визита": "Дата первого подтверждённого визита",
            }
        )
    )


def _build_page3_snapshot(
    kpi: pd.DataFrame,
    okk: pd.DataFrame,
    learning_fact: pd.DataFrame,
    enps: pd.DataFrame,
    dim: pd.DataFrame,
    teams: pd.DataFrame,
    attestations: pd.DataFrame,
    rtm_visits: pd.DataFrame,
    employee_kpi_metrics: pd.DataFrame | None = None,
) -> pd.DataFrame:
    kpi_work = kpi.copy()
    if "Вакансия" in kpi_work.columns:
        kpi_work = kpi_work[kpi_work["Вакансия"] != True].copy()

    allowed_months = set(pd.to_datetime(kpi_work["MonthStart"], errors="coerce").dropna().unique())
    allowed_months |= set(pd.to_datetime(okk["MonthStart"], errors="coerce").dropna().unique())
    allowed_months = {month for month in allowed_months if pd.notna(month) and month.year >= REPORT_START_YEAR}
    allowed_months_list = sorted(pd.to_datetime(list(allowed_months)))
    okk = okk[okk["MonthStart"].isin(allowed_months)].copy()

    if allowed_months_list:
        max_allowed_month = max(allowed_months_list)
        learning_fact = learning_fact[
            pd.to_datetime(learning_fact["StartMonth"], errors="coerce") <= max_allowed_month
        ].copy()

    employee_dir = (
        dim[["ID сотрудника", "ФИО", "Город", "Регион BI", "Дата приёма"]]
        .dropna(subset=["ID сотрудника"])
        .drop_duplicates("ID сотрудника")
        .rename(
            columns={
                "ID сотрудника": "ID мерчендайзера",
                "ФИО": "ФИО dim",
                "Город": "Город dim",
                "Регион BI": "Регион BI dim",
                "Дата приёма": "Дата приёма МЕ",
            }
        )
    )

    merch_team_dir = (
        teams.replace("", pd.NA)
        .dropna(subset=["ID мерчендайзера"])
        .groupby("ID мерчендайзера", dropna=False)
        .agg(
            **{
                "ID супервайзера": ("ID супервайзера", "first"),
                "Супервайзер": ("Супервайзер", "first"),
                "ID территориального менеджера": ("ID территориального менеджера", "first"),
                "Территориальный менеджер": ("Территориальный менеджер", "first"),
                "Регион BI teams": ("Регион BI", lambda s: s.mode().iloc[0] if not s.mode().empty else s.dropna().iloc[0] if s.dropna().any() else pd.NA),
                "Группа региона": ("Группа региона", lambda s: s.mode().iloc[0] if not s.mode().empty else s.dropna().iloc[0] if s.dropna().any() else pd.NA),
            }
        )
        .reset_index()
    )

    kpi_merch = (
        kpi_work.groupby(
            [
                "MonthStart",
                "YearMonth",
                "ID мерчендайзера",
            ],
            dropna=False,
        )
        .agg(
            **{
                "Мерчендайзер": ("Мерчендайзер", "first"),
                "ID супервайзера": ("ID супервайзера", "first"),
                "Супервайзер": ("Супервайзер", "first"),
                "ID территориального менеджера": ("ID территориального менеджера", _first_notna),
                "Территориальный менеджер": ("Территориальный менеджер", _first_notna),
                "Регион BI": ("Регион BI", "first"),
                "Город": ("Город", "first"),
                "KPI проекта %": ("KPI проекта %", "mean"),
                "Код маршрута СВ": ("Код маршрута СВ", "first"),
            }
        )
        .reset_index()
    )

    tt_complexity = _build_tt_complexity_monthly(okk, allowed_months_list)

    okk_merch = (
        okk.groupby(
            [
                "MonthStart",
                "YearMonth",
                "ID мерчендайзера",
            ],
            dropna=False,
        )
        .agg(
            **{
                "Мерчендайзер": ("Мерчендайзер", "first"),
                "ID супервайзера": ("ID супервайзера", "first"),
                "Супервайзер": ("Супервайзер", "first"),
                "Регион BI": ("Регион BI", "first"),
                "OSA из ОКК %": ("% наличия товара на полке", "mean"),
                "PICOS из ОКК %": ("% наличия PICoS", "mean"),
                "ОКК %": ("Качество визита", "mean"),
                "Фрод %": ("Флаг фальсификации", "mean"),
                "Фрод кол-во": ("Флаг фальсификации", lambda s: s.fillna(False).eq(True).sum()),
                "Визитов": ("Дата визита", "count"),
            }
        )
        .reset_index()
    )

    merch_tt = (
        okk.groupby(
            [
                "MonthStart",
                "YearMonth",
                "ID мерчендайзера",
                "Код ТТ",
            ],
            dropna=False,
        )
        .agg(
            **{
                "Мерчендайзер": ("Мерчендайзер", "first"),
            }
        )
        .reset_index()
    )

    okk_complex = merch_tt.merge(
        tt_complexity,
        on=["MonthStart", "YearMonth", "Код ТТ"],
        how="left",
    )
    complex_visits = (
        okk_complex.groupby(
            [
                "MonthStart",
                "YearMonth",
                "ID мерчендайзера",
            ],
            dropna=False,
        )
        .agg(
            **{
                "Мерчендайзер": ("Мерчендайзер", "first"),
                "Сложных ТТ": ("Сложная ТТ", lambda s: s.fillna(False).eq(True).sum()),
                "Средняя сложность ТТ": ("Сложность ТТ score", "mean"),
            }
        )
        .reset_index()
    )

    learning_merch = _build_learning_merch_cumulative(learning_fact, allowed_months_list)
    learning_merch = learning_merch.rename(columns={"ID сотрудника": "ID мерчендайзера"})

    base_merch = pd.concat(
        [
            kpi_merch[["MonthStart", "YearMonth", "ID мерчендайзера"]],
            okk_merch[["MonthStart", "YearMonth", "ID мерчендайзера"]],
            learning_merch[["MonthStart", "YearMonth", "ID мерчендайзера"]],
        ],
        ignore_index=True,
    ).drop_duplicates()

    snapshot = (
        base_merch.merge(
            kpi_merch,
            on=[
                "MonthStart",
                "YearMonth",
                "ID мерчендайзера",
            ],
            how="left",
        )
        .merge(
            okk_merch,
            on=[
                "MonthStart",
                "YearMonth",
                "ID мерчендайзера",
            ],
            suffixes=("", "_okk"),
            how="left",
        )
        .merge(
            complex_visits,
            on=[
                "MonthStart",
                "YearMonth",
                "ID мерчендайзера",
            ],
            suffixes=("", "_complex"),
            how="left",
        )
        .merge(
            learning_merch[
                [
                    "MonthStart",
                    "YearMonth",
                    "ID мерчендайзера",
                    "Обучение %",
                ]
            ],
            on=["MonthStart", "YearMonth", "ID мерчендайзера"],
            how="left",
        )
    )

    for base_col, alt_col in [
        ("Мерчендайзер", "Мерчендайзер_okk"),
        ("Мерчендайзер", "Мерчендайзер_complex"),
        ("ID супервайзера", "ID супервайзера_okk"),
        ("Супервайзер", "Супервайзер_okk"),
        ("Регион BI", "Регион BI_okk"),
    ]:
        if base_col not in snapshot.columns and alt_col in snapshot.columns:
            snapshot[base_col] = snapshot[alt_col]
        elif base_col in snapshot.columns and alt_col in snapshot.columns:
            snapshot[base_col] = snapshot[base_col].combine_first(snapshot[alt_col])

    snapshot = snapshot.drop(
        columns=[
            "Мерчендайзер_okk",
            "Мерчендайзер_complex",
            "ID супервайзера_okk",
            "Супервайзер_okk",
            "Регион BI_okk",
        ],
        errors="ignore",
    )

    snapshot = snapshot.merge(
        employee_dir,
        on="ID мерчендайзера",
        how="left",
    )
    snapshot["Дата приёма МЕ"] = pd.to_datetime(snapshot["Дата приёма МЕ"], errors="coerce")
    month_end = pd.to_datetime(snapshot["MonthStart"], errors="coerce") + pd.offsets.MonthEnd(0)
    first_visits = _first_confirmed_visits_for_current_episode(rtm_visits, dim)
    snapshot = snapshot.merge(first_visits, on="ID мерчендайзера", how="left")
    snapshot = snapshot[
        snapshot["Дата первого подтверждённого визита"].notna()
        & snapshot["Дата первого подтверждённого визита"].le(month_end)
    ].copy()
    month_end = pd.to_datetime(snapshot["MonthStart"], errors="coerce") + pd.offsets.MonthEnd(0)
    tenure_days = (month_end - snapshot["Дата приёма МЕ"]).dt.days
    snapshot["Стаж МЕ, мес."] = (tenure_days / 30.44).round(1)
    snapshot.loc[tenure_days.lt(0) | tenure_days.isna(), "Стаж МЕ, мес."] = np.nan
    snapshot = snapshot.merge(merch_team_dir, on="ID мерчендайзера", how="inner", suffixes=("", "_teams"))
    merch_attestation_quarterly = _build_merch_client_attestation_quarterly(attestations)
    merch_attestation = _build_merch_client_attestation_wide(merch_attestation_quarterly)
    snapshot = snapshot.merge(merch_attestation, on="ID мерчендайзера", how="left")
    snapshot = _merge_asof_merch_client_attestation(snapshot, merch_attestation_quarterly)
    snapshot["Мерчендайзер"] = snapshot["ФИО dim"].combine_first(snapshot["Мерчендайзер"])
    snapshot["Город"] = snapshot["Город dim"].combine_first(snapshot["Город"])
    snapshot["ID супервайзера"] = snapshot["ID супервайзера_teams"]
    snapshot["Супервайзер"] = snapshot["Супервайзер_teams"]
    snapshot["ID территориального менеджера"] = snapshot[
        "ID территориального менеджера_teams"
    ]
    snapshot["Территориальный менеджер"] = snapshot[
        "Территориальный менеджер_teams"
    ]
    snapshot["Регион BI"] = snapshot["Регион BI teams"]
    snapshot = snapshot.drop(
        columns=[
            "Дата первого подтверждённого визита",
            "ФИО dim",
            "Город dim",
            "Регион BI dim",
            "ID супервайзера_teams",
            "Супервайзер_teams",
            "ID территориального менеджера_teams",
            "Территориальный менеджер_teams",
            "Регион BI teams",
        ],
        errors="ignore",
    )

    snapshot = normalize_confirmed_tm(snapshot)
    sv_ids = snapshot["ID супервайзера"].replace("", pd.NA)
    missing_sv = sv_ids.isna() | sv_ids.astype("string").str.strip().eq(NO_SV_ID).fillna(False)
    if missing_sv.any():
        snapshot.loc[missing_sv, "ID супервайзера"] = missing_supervisor_keys(snapshot.loc[missing_sv])
        snapshot.loc[missing_sv, "Супервайзер"] = NO_SV_NAME
    snapshot["Супервайзер"] = snapshot["Супервайзер"].replace("", pd.NA).fillna(NO_SV_NAME)

    enps_quarterly = _build_enps_quarterly(enps)
    snapshot = _attach_last_quarter_metric(
        snapshot, enps_quarterly, "Риск ухода региона %", period="year"
    )

    if employee_kpi_metrics is not None and not employee_kpi_metrics.empty:
        employee_kpi = pivot_employee_kpi_metrics(employee_kpi_metrics).rename(
            columns={
                "ID сотрудника": "ID мерчендайзера",
                "KPI проекта %": "KPI проекта % клиент",
            }
        )
        snapshot = snapshot.merge(
            employee_kpi,
            on=["MonthStart", "YearMonth", "ID мерчендайзера"],
            how="left",
        )
        snapshot["KPI проекта %"] = snapshot["KPI проекта % клиент"]
        snapshot = snapshot.drop(columns=["KPI проекта % клиент"], errors="ignore")

    core_metric_cols = [
        "KPI проекта %",
        "ОКК %",
        "OSA из ОКК %",
        "PICOS из ОКК %",
        "Обучение %",
        "Фрод %",
        "Фрод кол-во",
        "Сложных ТТ",
    ]
    snapshot = snapshot[
        snapshot[core_metric_cols].notna().any(axis=1)
    ].copy()

    snapshot["Код СВ"] = _extract_sv_code(snapshot["Код маршрута СВ"])
    snapshot["Мерчендайзер ФИ"] = _short_fi(snapshot["Мерчендайзер"])
    snapshot["Фрод обратный %"] = 1 - snapshot["Фрод %"]
    snapshot["Источник аттестации"] = snapshot["Аттестация клиента %"].notna().map(
        {True: "Клиент", False: "Нет результата"}
    )
    snapshot["Доступность личных метрик %"] = snapshot.apply(_merch_personal_available_weight_from_row, axis=1)
    snapshot["Личная эффективность МЕ %"] = pd.to_numeric(
        snapshot.apply(_merch_personal_score_from_row, axis=1),
        errors="coerce",
    )
    snapshot["Балл личной эффективности"] = np.floor(snapshot["Личная эффективность МЕ %"] * 100)
    snapshot["Статус личной эффективности"] = snapshot.apply(_merch_personal_status_from_row, axis=1)
    snapshot["Причина личной эффективности"] = snapshot.apply(_merch_personal_reason_from_row, axis=1)
    for column in [
        "Аттестация клиента %",
        *CLIENT_ATTESTATION_QUARTERS.values(),
        "Доступность личных метрик %",
        "Личная эффективность МЕ %",
        "Балл личной эффективности",
    ]:
        snapshot[column] = pd.to_numeric(snapshot[column], errors="coerce")

    snapshot["Score"] = snapshot["Балл личной эффективности"]

    def _profile_status(row: pd.Series) -> str:
        kpi_val = row.get("KPI проекта %")
        okk_val = row.get("ОКК %")
        learn_val = row.get("Обучение %")
        fraud_val = row.get("Фрод %")

        visible_full_profile = all(
            pd.notna(row.get(col))
            for col in [
                "KPI проекта %",
                "ОКК %",
                "Обучение %",
                "Фрод %",
            ]
        )

        visible_breach_count = 0
        visible_breach_count += int(pd.notna(kpi_val) and kpi_val < MERCH_KPI_TARGET)
        visible_breach_count += int(pd.notna(okk_val) and okk_val < TARGET_OKK)
        visible_breach_count += int(pd.notna(learn_val) and learn_val < MERCH_PERSONAL_TARGETS["Обучение %"])
        visible_breach_count += int(pd.notna(fraud_val) and fraud_val > TARGET_FRAUD)

        if (
            visible_full_profile
            and pd.notna(kpi_val) and kpi_val >= MERCH_KPI_TARGET
            and pd.notna(okk_val) and okk_val >= TARGET_OKK
            and pd.notna(learn_val) and learn_val >= MERCH_PERSONAL_TARGETS["Обучение %"]
            and pd.notna(fraud_val) and fraud_val <= TARGET_FRAUD
        ):
            return "Сильная практика"
        if visible_breach_count >= 2:
            return "Требует вмешательства"
        return "Контроль"

    snapshot["Статус профиля"] = snapshot.apply(_profile_status, axis=1)

    def _comment(row: pd.Series) -> str:
        reasons = []
        if pd.notna(row.get("KPI проекта %")) and row["KPI проекта %"] < MERCH_KPI_TARGET:
            reasons.append(f"KPI {_fmt_pct(row['KPI проекта %'])}")
        if pd.notna(row.get("ОКК %")) and row["ОКК %"] < TARGET_OKK:
            reasons.append(f"ОКК {_fmt_pct(row['ОКК %'])}")
        if pd.notna(row.get("Фрод %")) and row["Фрод %"] > TARGET_FRAUD:
            reasons.append(f"фрод {_fmt_pct(row['Фрод %'])}")
        if pd.notna(row.get("Обучение %")) and row["Обучение %"] < MERCH_PERSONAL_TARGETS["Обучение %"]:
            reasons.append(f"обучение {_fmt_pct(row['Обучение %'])}")
        if pd.notna(row.get("Сложных ТТ")) and row["Сложных ТТ"] >= 3:
            reasons.append(f"сложные ТТ {int(row['Сложных ТТ'])}")
        if not reasons:
            return "Стабильный профиль; следить за динамикой KPI, ОКК и фрода."
        return "Фокус: " + ", ".join(reasons[:2]) + "."

    snapshot["Комментарий аналитика"] = snapshot.apply(_comment, axis=1)

    pieces = []
    for month_start, month_df in snapshot.groupby("MonthStart"):
        ranked = month_df.sort_values(
            ["Score", "KPI проекта %", "ОКК %", "Обучение %", "ID мерчендайзера"],
            ascending=[False, False, False, False, True],
            na_position="last",
            kind="mergesort",
        ).copy()
        ranked["Ранг"] = pd.array(range(1, len(ranked) + 1), dtype="Int64")
        pieces.append(ranked)
    snapshot = pd.concat(pieces, ignore_index=True)

    columns = [
        "MonthStart",
        "YearMonth",
        "Ранг",
        "Score",
        "ID мерчендайзера",
        "Мерчендайзер",
        "Мерчендайзер ФИ",
        "Регион BI",
        "Город",
        "Стаж МЕ, мес.",
        "ID супервайзера",
        "Супервайзер",
        "ID территориального менеджера",
        "Территориальный менеджер",
        "Код СВ",
        "KPI проекта %",
        "ОКК %",
        "Обучение %",
        "Аттестация клиента %",
        *CLIENT_ATTESTATION_QUARTERS.values(),
        "Личная эффективность МЕ %",
        "Статус личной эффективности",
        "Причина личной эффективности",
        "Фрод %",
        "Фрод кол-во",
        "Сложных ТТ",
        "Статус профиля",
        "Риск ухода региона %",
        "Комментарий аналитика",
        *KPI_COMPONENT_COLUMNS,
    ]
    return snapshot[[c for c in columns if c in snapshot.columns]].copy()


def _fmt_pct(value: float | None) -> str:
    if pd.isna(value):
        return "—"
    return f"{round(value * 100):.0f}%"


def build_page3_data() -> pd.DataFrame:
    settings = load_settings()
    out_dir = Path(settings["paths"]["out"])

    kpi = pd.read_parquet(out_dir / "kpi_fact.parquet")
    okk = pd.read_parquet(out_dir / "okk_fact.parquet")
    learning_fact = pd.read_parquet(out_dir / "learning_fact.parquet")
    enps = pd.read_parquet(out_dir / "enps_fact.parquet")
    dim = pd.read_parquet(out_dir / "dim_employees.parquet")
    teams = pd.read_parquet(out_dir / "dim_teams.parquet")
    attestations_path = out_dir / "attestations_fact.parquet"
    attestations = pd.read_parquet(attestations_path) if attestations_path.exists() else pd.DataFrame()
    rtm_path = out_dir / "rtm_employee_visits.parquet"
    if not rtm_path.exists():
        raise FileNotFoundError("rtm_employee_visits.parquet обязателен для определения фактического выхода МЕ")
    rtm_visits = pd.read_parquet(rtm_path)

    employee_kpi_path = out_dir / "kpi_employee_monthly_metrics.parquet"
    employee_kpi_metrics = pd.read_parquet(employee_kpi_path) if employee_kpi_path.exists() else pd.DataFrame()
    snapshot = _build_page3_snapshot(
        kpi,
        okk,
        learning_fact,
        enps,
        dim,
        teams,
        attestations,
        rtm_visits,
        employee_kpi_metrics,
    )
    if not rtm_visits.empty:
        source_org = build_rtm_month_org(rtm_visits, "ID сотрудника").rename(
            columns={
                "ID сотрудника": "ID мерчендайзера",
                "ID территориального менеджера": "ID территориального менеджера RTM",
                "Территориальный менеджер": "Территориальный менеджер RTM",
                "ID супервайзера": "ID супервайзера RTM",
                "Супервайзер": "Супервайзер RTM",
                "Регион BI": "Регион BI RTM",
            }
        )
        if not source_org.empty:
            snapshot = snapshot.merge(
                source_org,
                on=["MonthStart", "YearMonth", "ID мерчендайзера"],
                how="left",
            )
            tm_ids = snapshot["ID территориального менеджера"].astype("string").str.strip()
            missing_tm = tm_ids.isna() | tm_ids.eq("")
            snapshot.loc[missing_tm, "ID территориального менеджера"] = snapshot.loc[
                missing_tm, "ID территориального менеджера RTM"
            ]
            snapshot.loc[missing_tm, "Территориальный менеджер"] = snapshot.loc[
                missing_tm, "Территориальный менеджер RTM"
            ]
            missing_sv = snapshot["ID супервайзера"].astype("string").str.startswith("NO_SV", na=False)
            snapshot.loc[missing_sv, "ID супервайзера"] = snapshot.loc[missing_sv, "ID супервайзера RTM"]
            snapshot.loc[missing_sv, "Супервайзер"] = snapshot.loc[missing_sv, "Супервайзер RTM"]
            snapshot["Регион BI"] = snapshot["Регион BI"].combine_first(snapshot["Регион BI RTM"])
            snapshot = normalize_confirmed_tm(snapshot)
            snapshot = snapshot.drop(columns=[column for column in snapshot.columns if column.endswith(" RTM")], errors="ignore")
    save_parquet(snapshot, str(out_dir / "page3_merch_monthly_snapshot.parquet"))

    print(f"\n  Page3 merch snapshot: {len(snapshot)} строк")
    return snapshot


if __name__ == "__main__":
    build_page3_data()
