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
)


_REPORTING_CFG = load_settings()["reporting"]
REPORT_START_YEAR = _REPORTING_CFG["start_yearmonth"] // 100
CLIENT_ATTESTATION_QUARTERS = _REPORTING_CFG["client_attestation_quarters"]

TARGET_KPI = 0.75
TARGET_OKK = 0.60
TARGET_LEARN = 0.75
TARGET_FRAUD = 0.10
TARGET_OSA = 0.85
TARGET_PICOS = 0.85

MERCH_PERSONAL_HIGH_MIN_SCORE = 0.90
MERCH_PERSONAL_ROLE_MIN_SCORE = 0.80
MERCH_PERSONAL_MIN_AVAILABLE_WEIGHT = 0.60
MERCH_PERSONAL_SCORE_POINTS = {
    "KPI проекта %": 40,
    "ОКК %": 15,
    "Обучение %": 20,
    "Аттестация клиента %": 25,
}
MERCH_PERSONAL_TARGETS = {
    "KPI проекта %": 0.95,
    "ОКК %": 0.60,
    "Обучение %": 0.95,
    "Аттестация клиента %": 0.95,
}
MERCH_PERSONAL_RED_THRESHOLDS = {
    "KPI проекта %": 0.90,
    "ОКК %": 0.40,
    "Обучение %": 0.90,
    "Аттестация клиента %": 0.90,
}
MERCH_PERSONAL_CRITICAL_COLUMNS = ["KPI проекта %", "ОКК %"]


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

    pieces: list[pd.DataFrame] = []
    for merch_id, base in monthly.groupby("ID мерчендайзера", dropna=False):
        base_sorted = base.sort_values("MonthStart").copy()
        q = attestation_quarterly[
            attestation_quarterly["ID мерчендайзера"].astype(str) == str(merch_id)
        ].sort_values("QuarterStart аттестации клиента").copy()
        if q.empty:
            base_sorted["QuarterStart аттестации клиента"] = pd.NaT
            for column in attach_cols:
                base_sorted[column] = pd.NA
        else:
            merged = pd.merge_asof(
                base_sorted,
                q[["QuarterStart аттестации клиента"] + attach_cols],
                left_on="MonthStart",
                right_on="QuarterStart аттестации клиента",
                direction="backward",
            )
            base_sorted["QuarterStart аттестации клиента"] = merged["QuarterStart аттестации клиента"].values
            for column in attach_cols:
                base_sorted[column] = merged[column].values
        pieces.append(base_sorted)

    return pd.concat(pieces, ignore_index=True)


def _merch_personal_metric_label(row: pd.Series, metric: str) -> str:
    if metric != "Аттестация клиента %":
        return metric.replace(" %", "")

    quarter_label = row.get("QuarterLabel аттестации клиента")
    if pd.notna(quarter_label) and str(quarter_label).strip():
        return f"Аттестация клиента {str(quarter_label).strip()}"
    attestation_value = row.get("Аттестация клиента %")
    for column in CLIENT_ATTESTATION_QUARTERS.values():
        label = column.removeprefix("Аттестация клиента ").removesuffix(" %")
        quarter_value = row.get(column)
        if pd.notna(attestation_value) and pd.notna(quarter_value):
            if abs(float(attestation_value) - float(quarter_value)) <= 0.0001:
                return f"Аттестация клиента {label}"
    return "Аттестация клиента"


def _merch_personal_available_weight_from_row(row: pd.Series) -> float:
    available_points = 0.0
    for metric, points in MERCH_PERSONAL_SCORE_POINTS.items():
        if pd.notna(row.get(metric)):
            available_points += points
    return round(available_points / 100, 4)


def _merch_personal_score_from_row(row: pd.Series) -> float:
    score_value = 0.0
    has_metric = False
    for metric, points in MERCH_PERSONAL_SCORE_POINTS.items():
        value = row.get(metric)
        if pd.isna(value):
            if metric == "Аттестация клиента %":
                score_value += points
                has_metric = True
            continue
        has_metric = True
        target = MERCH_PERSONAL_TARGETS[metric]
        red_threshold = MERCH_PERSONAL_RED_THRESHOLDS[metric]
        if float(value) < red_threshold:
            continue
        normalized = max(0.0, min(1.0, float(value) / target))
        score_value += normalized * points
    return round(score_value / 100, 4) if has_metric else np.nan


def _merch_personal_weak_metric_records(row: pd.Series) -> list[dict]:
    records = []
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
    score = row.get("Личная эффективность МЕ %")
    available_weight = row.get("Доступность личных метрик %")
    missing_critical = any(pd.isna(row.get(metric)) for metric in MERCH_PERSONAL_CRITICAL_COLUMNS)

    if pd.isna(score) or pd.isna(available_weight) or available_weight < MERCH_PERSONAL_MIN_AVAILABLE_WEIGHT:
        return "Недостаточно данных"
    if missing_critical:
        return "Недостаточно данных"

    weak_records = _merch_personal_weak_metric_records(row)
    weak_count = len(weak_records)
    red_weight = sum(record["weight"] for record in weak_records if record["severity"] == "red")

    if (
        score < MERCH_PERSONAL_ROLE_MIN_SCORE
        or red_weight > 0
    ):
        return "Зона развития"
    if (
        score >= MERCH_PERSONAL_HIGH_MIN_SCORE
        and available_weight >= 0.999
        and weak_count == 0
    ):
        return "Высокая личная готовность"
    return "Соответствует роли"


def _merch_personal_reason_from_row(row: pd.Series) -> str:
    status = row.get("Статус личной эффективности")
    reason_records = []
    for record in _merch_personal_weak_metric_records(row):
        if (
            record["metric"] == "Аттестация клиента %"
            and record["severity"] != "red"
        ):
            continue
        reason_records.append(record)
    red_records = [record for record in reason_records if record["severity"] == "red"]
    yellow_records = [record for record in reason_records if record["severity"] != "red"]
    weak_labels = [record["label"] for record in (red_records + yellow_records)[:3]]

    if status == "Недостаточно данных":
        if weak_labels:
            return ", ".join(["Недостаточно данных"] + weak_labels[:2])
        return "Недостаточно данных"
    if weak_labels:
        return ", ".join(weak_labels[:3])
    if pd.isna(row.get("Аттестация клиента %")):
        return "Новичок"
    return "Метрики в целевой зоне"


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


def _normalized_range_score(series: pd.Series, scale: float) -> float:
    clean = series.dropna()
    if len(clean) <= 1:
        return 0.0
    value_range = float(clean.max() - clean.min())
    return max(0.0, min(1.0, value_range / scale))


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
                "МЕ на ТТ": ("ID мерчендайзера", lambda s: s.dropna().nunique()),
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

    rows: list[dict] = []
    for tt_code, tt_df in tt_monthly.groupby("Код ТТ", dropna=False):
        tt_sorted = tt_df.sort_values("MonthStart").reset_index(drop=True)
        tt_visits = work[work["Код ТТ"] == tt_code].copy()

        for idx, row in tt_sorted.iterrows():
            history = tt_sorted.iloc[max(0, idx - 2): idx + 1].copy()
            history_months = history["MonthStart"].dropna().unique().tolist()
            history_visits = tt_visits[tt_visits["MonthStart"].isin(history_months)].copy()

            repeated_problem_score = float(history["Проблема KPI/ТТ"].mean()) * 35

            osa_instability = _normalized_range_score(history["OSA %"], 0.25)
            picos_instability = _normalized_range_score(history["PICOS %"], 0.25)
            instability_parts = [v for v in [osa_instability, picos_instability] if pd.notna(v)]
            instability_score = (sum(instability_parts) / len(instability_parts)) * 25 if instability_parts else 0.0

            okk_repeat_score = float(history["OKK нарушение"].mean()) * 20

            shelf_series = history["Золотая полка %"].dropna()
            shelf_score = (1 - float(shelf_series.mean())) * 10 if not shelf_series.empty else 0.0

            unique_merch = history_visits["ID мерчендайзера"].dropna().nunique()
            route_instability_score = min(1.0, max(0.0, (unique_merch - 1) / 2)) * 10

            complexity_score = (
                repeated_problem_score
                + instability_score
                + okk_repeat_score
                + shelf_score
                + route_instability_score
            )

            rows.append(
                {
                    "MonthStart": row["MonthStart"],
                    "YearMonth": row["YearMonth"],
                    "Код ТТ": tt_code,
                    "Сложность ТТ score": round(complexity_score, 1),
                    "Сложная ТТ": complexity_score >= 45,
                }
            )

    return pd.DataFrame(rows)


def _build_page3_snapshot(
    kpi: pd.DataFrame,
    okk: pd.DataFrame,
    learning_fact: pd.DataFrame,
    enps: pd.DataFrame,
    dim: pd.DataFrame,
    teams: pd.DataFrame,
    attestations: pd.DataFrame,
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
        dim[["ID сотрудника", "ФИО", "Город", "Регион BI", "Стаж (месяцев)"]]
        .dropna(subset=["ID сотрудника"])
        .drop_duplicates("ID сотрудника")
        .rename(
            columns={
                "ID сотрудника": "ID мерчендайзера",
                "ФИО": "ФИО dim",
                "Город": "Город dim",
                "Регион BI": "Регион BI dim",
                "Стаж (месяцев)": "Стаж МЕ, мес.",
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
                "Регион BI": ("Регион BI", "first"),
                "Город": ("Город", "first"),
                "KPI проекта %": ("KPI 1", "mean"),
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
    snapshot = snapshot.merge(merch_team_dir, on="ID мерчендайзера", how="inner", suffixes=("", "_teams"))
    merch_attestation_quarterly = _build_merch_client_attestation_quarterly(attestations)
    merch_attestation = _build_merch_client_attestation_wide(merch_attestation_quarterly)
    snapshot = snapshot.merge(merch_attestation, on="ID мерчендайзера", how="left")
    snapshot = _merge_asof_merch_client_attestation(snapshot, merch_attestation_quarterly)
    snapshot["Мерчендайзер"] = snapshot["ФИО dim"].combine_first(snapshot["Мерчендайзер"])
    snapshot["Город"] = snapshot["Город dim"].combine_first(snapshot["Город"])
    snapshot["ID супервайзера"] = snapshot["ID супервайзера_teams"]
    snapshot["Супервайзер"] = snapshot["Супервайзер_teams"]
    snapshot["Регион BI"] = (
        snapshot["Регион BI teams"]
    )
    snapshot = snapshot.drop(
        columns=[
            "ФИО dim",
            "Город dim",
            "Регион BI dim",
            "ID супервайзера_teams",
            "Супервайзер_teams",
            "Регион BI teams",
        ],
        errors="ignore",
    )

    if "Территориальный менеджер" in snapshot.columns:
        snapshot["Территориальный менеджер"] = (
            snapshot["Территориальный менеджер"]
            .replace("", pd.NA)
            .fillna("Вакансия / нет ТМ")
        )
    if "ID территориального менеджера" in snapshot.columns:
        snapshot["ID территориального менеджера"] = (
            snapshot["ID территориального менеджера"]
            .replace("", pd.NA)
            .fillna("NO_TM")
        )

    enps_quarterly = _build_enps_quarterly(enps)
    snapshot = _attach_last_quarter_metric(snapshot, enps_quarterly, "Риск ухода региона %")

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
        visible_breach_count += int(pd.notna(kpi_val) and kpi_val < TARGET_KPI)
        visible_breach_count += int(pd.notna(okk_val) and okk_val < TARGET_OKK)
        visible_breach_count += int(pd.notna(learn_val) and learn_val < TARGET_LEARN)
        visible_breach_count += int(pd.notna(fraud_val) and fraud_val > TARGET_FRAUD)

        if (
            visible_full_profile
            and pd.notna(kpi_val) and kpi_val >= TARGET_KPI
            and pd.notna(okk_val) and okk_val >= TARGET_OKK
            and pd.notna(learn_val) and learn_val >= TARGET_LEARN
            and pd.notna(fraud_val) and fraud_val <= TARGET_FRAUD
        ):
            return "Сильная практика"
        if visible_breach_count >= 2:
            return "Требует вмешательства"
        return "Контроль"

    snapshot["Статус профиля"] = snapshot.apply(_profile_status, axis=1)

    def _comment(row: pd.Series) -> str:
        reasons = []
        if pd.notna(row.get("KPI проекта %")) and row["KPI проекта %"] < TARGET_KPI:
            reasons.append(f"KPI {_fmt_pct(row['KPI проекта %'])}")
        if pd.notna(row.get("ОКК %")) and row["ОКК %"] < TARGET_OKK:
            reasons.append(f"ОКК {_fmt_pct(row['ОКК %'])}")
        if pd.notna(row.get("Фрод %")) and row["Фрод %"] > TARGET_FRAUD:
            reasons.append(f"фрод {_fmt_pct(row['Фрод %'])}")
        if pd.notna(row.get("Обучение %")) and row["Обучение %"] < TARGET_LEARN:
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
            ["Score", "KPI проекта %", "ОКК %"],
            ascending=[False, False, False],
            na_position="last",
        ).copy()
        ranked["Ранг"] = ranked["Score"].rank(method="dense", ascending=False).astype("Int64")
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
        "OSA из ОКК %",
        "PICOS из ОКК %",
        "ОКК %",
        "Обучение %",
        "Аттестация клиента %",
        *CLIENT_ATTESTATION_QUARTERS.values(),
        "Личная эффективность МЕ %",
        "Балл личной эффективности",
        "Статус личной эффективности",
        "Причина личной эффективности",
        "Фрод %",
        "Фрод кол-во",
        "Сложных ТТ",
        "Статус профиля",
        "Риск ухода региона %",
        "Комментарий аналитика",
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

    snapshot = _build_page3_snapshot(kpi, okk, learning_fact, enps, dim, teams, attestations)
    save_parquet(snapshot, str(out_dir / "page3_merch_monthly_snapshot.parquet"))

    print(f"\n  Page3 merch snapshot: {len(snapshot)} строк")
    return snapshot


if __name__ == "__main__":
    build_page3_data()
