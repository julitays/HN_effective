import sys
from pathlib import Path

import numpy as np
import pandas as pd

if __package__ is None or __package__ == "":
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from scripts.utils import load_region_map, load_settings, save_parquet


REPORT_START_YEARMONTH = load_settings()["reporting"]["start_yearmonth"]

KPI_RED_MAX = 0.90
KPI_YELLOW_MAX = 0.95

SOFT_KPI_MIN = KPI_YELLOW_MAX
SOFT_OKK_MIN = 0.55
SOFT_LEARN_MIN = 0.70
SOFT_FRAUD_MAX = 0.18
SOFT_RISK_MAX = 0.22
SOFT_VACANCY_SHARE_MAX = 0.12
SOFT_TURNOVER_MAX = 0.08
SOFT_NET_OUTFLOW_MIN = 3
SOFT_STAFFING_MIN = 0.75

HARD_KPI_MIN = KPI_RED_MAX
HARD_OKK_MIN = 0.45
HARD_LEARN_MIN = 0.55
HARD_FRAUD_MAX = 0.25
HARD_RISK_MAX = 0.30
HARD_VACANCY_SHARE_MAX = 0.20
HARD_TURNOVER_MAX = 0.15
HARD_NET_OUTFLOW_MIN = 10
HARD_STAFFING_MIN = 0.60

REGION_OPERATIONAL_WEIGHT = 0.65
REGION_STAFFING_WEIGHT = 0.35
MIN_REGION_METRIC_AVAILABILITY = 0.60
REGION_STABLE_MIN = 0.88
REGION_CONTROL_MIN = 0.80

OPERATIONAL_WEIGHTS = {
    "KPI проекта %": 0.30,
    "Качество визитов %": 0.20,
    "Обязательное обучение %": 0.15,
    "Антифрод %": 0.15,
    "Климат %": 0.10,
    "Оценка команды %": 0.10,
}

SIGNAL_LABELS = {
    "risk": "риск eNPS",
    "okk": "ОКК",
    "learn": "обучение",
    "fraud": "фрод",
    "kpi": "KPI",
    "vacancy": "вакансии",
    "turnover": "текучесть",
    "outflow": "кадровый отток",
    "staffing": "кадровая устойчивость",
}


def _clean_region_values(df: pd.DataFrame) -> pd.DataFrame:
    if "Регион BI" not in df.columns:
        return df

    cleaned = df.copy()
    cleaned["Регион BI"] = cleaned["Регион BI"].astype("string").str.strip()
    cleaned["Регион BI"] = cleaned["Регион BI"].replace({"": pd.NA, "nan": pd.NA, "None": pd.NA})
    return cleaned


def _core_regions() -> set[str]:
    region_map = load_region_map()
    return set(region_map.loc[region_map["region_group"].eq("core"), "canonical_region"].dropna().astype(str))


def _build_kpi_monthly(kpi: pd.DataFrame, kpi_tt_direct: pd.DataFrame | None = None) -> pd.DataFrame:
    if kpi_tt_direct is not None and not kpi_tt_direct.empty:
        work = _clean_region_values(kpi_tt_direct)
        work = work[work["Регион BI"].notna()].copy()
        result = (
            work.groupby(["MonthStart", "YearMonth", "Регион BI"], dropna=False)
            .agg(**{"KPI проекта %": ("KPI 1", "mean")})
            .reset_index()
        )
        return result

    work = _clean_region_values(kpi)
    if "Вакансия" in work.columns:
        work = work[work["Вакансия"] != True].copy()
    work = work[work["Регион BI"].notna()].copy()
    result = (
        work.groupby(["MonthStart", "YearMonth", "Регион BI"], dropna=False)
        .agg(
            **{
                "KPI проекта %": ("KPI 1", "mean"),
            }
        )
        .reset_index()
    )
    return result


def _build_okk_monthly(okk: pd.DataFrame) -> pd.DataFrame:
    okk = _clean_region_values(okk)
    okk = okk[okk["Регион BI"].notna()].copy()
    result = (
        okk.groupby(["MonthStart", "YearMonth", "Регион BI"], dropna=False)
        .agg(
            **{
                "Качество визитов %": ("Качество визита", "mean"),
                "Фрод %": ("Флаг фальсификации", "mean"),
                "Фрод кол-во": ("Флаг фальсификации", lambda s: s.fillna(False).eq(True).sum()),
            }
        )
        .reset_index()
    )
    return result


def _build_learning_monthly(learning_monthly: pd.DataFrame) -> pd.DataFrame:
    keep = ["MonthStart", "YearMonth", "Регион BI", "Обязательное обучение %"]
    result = learning_monthly[[c for c in keep if c in learning_monthly.columns]].copy()
    result = _clean_region_values(result)
    return result[result["Регион BI"].notna()].copy()


def _build_enps_quarterly(enps: pd.DataFrame) -> pd.DataFrame:
    enps = _clean_region_values(enps)
    enps = enps[enps["Регион BI"].notna()].copy()
    result = (
        enps.groupby(["QuarterStart", "YearQuarter", "Регион BI"], dropna=False)
        .agg(
            **{
                "Риск ухода структуры eNPS %": (
                    "Уровень риска ухода",
                    lambda s: s.eq("Высокий").mean()
                ),
            }
        )
        .reset_index()
    )
    return result


def _build_oed_quarterly(oed: pd.DataFrame) -> pd.DataFrame:
    oed = _clean_region_values(oed)
    oed = oed[oed["Регион BI"].notna()].copy()
    result = (
        oed.groupby(["QuarterStart", "YearQuarter", "Регион BI"], dropna=False)
        .agg(
            **{
                "Оценка команды %": ("Аттестация", "mean"),
            }
        )
        .reset_index()
    )
    result["Оценка команды %"] = result["Оценка команды %"] / 100
    return result


def _attach_last_quarter_metric(
    monthly_base: pd.DataFrame,
    quarterly_df: pd.DataFrame,
    value_col: str,
) -> pd.DataFrame:
    if quarterly_df.empty:
        monthly_base[value_col] = np.nan
        return monthly_base

    pieces = []
    for region, region_base in monthly_base.groupby("Регион BI", dropna=False):
        base_sorted = region_base.sort_values("MonthStart").copy()
        quarter_sorted = quarterly_df[quarterly_df["Регион BI"] == region].sort_values("QuarterStart").copy()
        if quarter_sorted.empty:
            base_sorted[value_col] = np.nan
        else:
            merged = pd.merge_asof(
                base_sorted,
                quarter_sorted[["QuarterStart", value_col]],
                left_on="MonthStart",
                right_on="QuarterStart",
                direction="backward",
            )
            base_sorted[value_col] = merged[value_col].values
        pieces.append(base_sorted)

    return pd.concat(pieces, ignore_index=True)


def _build_staffing_page1(out_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    staffing_path = out_dir / "org_staffing_report_snapshot.parquet"
    if not staffing_path.exists():
        empty_monthly = pd.DataFrame(
            columns=[
                "MonthStart",
                "YearMonth",
                "Регион BI",
                "Нанято",
                "Уволено",
                "Чистый отток",
                "Баланс персонала",
            ]
        )
        empty_current = pd.DataFrame(columns=["Регион BI"])
        return empty_monthly, empty_current

    staffing = pd.read_parquet(staffing_path)
    staffing = _clean_region_values(staffing)
    region_staffing = staffing[
        staffing["Уровень анализа"].eq("Регион")
        & staffing["Регион BI"].notna()
    ].copy()

    monthly_cols = [
        "MonthStart",
        "YearMonth",
        "Регион BI",
        "Открытых вакансий",
        "Открытых вакансий МЕ",
        "Открытых вакансий СВ",
        "Приостановленных вакансий",
        "Нанято",
        "Уволено",
        "Чистый отток",
        "Баланс персонала",
    ]
    monthly = region_staffing[[c for c in monthly_cols if c in region_staffing.columns]].copy()
    for column in [
        "Открытых вакансий",
        "Открытых вакансий МЕ",
        "Открытых вакансий СВ",
        "Приостановленных вакансий",
        "Нанято",
        "Уволено",
        "Чистый отток",
        "Баланс персонала",
    ]:
        if column in monthly.columns:
            monthly[column] = pd.to_numeric(monthly[column], errors="coerce").fillna(0)

    latest_staffing = pd.DataFrame(columns=["Регион BI"])
    if not region_staffing.empty:
        latest_month = region_staffing["YearMonth"].max()
        latest_staffing = region_staffing[region_staffing["YearMonth"].eq(latest_month)].copy()
        latest_staffing = latest_staffing[
            [
                c
                for c in [
                    "Регион BI",
                    "Активных МЕ",
                    "Активных СВ",
                    "Активных ТМ",
                    "YearMonth",
                    "MonthStart",
                ]
                if c in latest_staffing.columns
            ]
        ].rename(
            columns={
                "YearMonth": "YearMonth кадрового среза",
                "MonthStart": "MonthStart кадрового среза",
            }
        )

    current = latest_staffing
    for column in [
        "Активных МЕ",
        "Активных СВ",
        "Активных ТМ",
    ]:
        if column in current.columns:
            current[column] = pd.to_numeric(current[column], errors="coerce").fillna(0)

    return monthly, current


def _clip_pct(value):
    if pd.isna(value):
        return pd.NA
    return min(max(float(value), 0.0), 1.0)


def _weighted_score(row: pd.Series, weights: dict[str, float]):
    weighted_sum = 0.0
    available_weight = 0.0
    for column, weight in weights.items():
        value = _clip_pct(row.get(column))
        if pd.notna(value):
            weighted_sum += float(value) * weight
            available_weight += weight

    if available_weight == 0:
        return pd.NA, 0.0
    return weighted_sum / available_weight, available_weight


def _operational_score(row: pd.Series):
    score, _ = _weighted_score(row, OPERATIONAL_WEIGHTS)
    return score


def _region_metric_availability(row: pd.Series) -> float:
    _, operational_available = _weighted_score(row, OPERATIONAL_WEIGHTS)
    staffing = _clip_pct(row.get("Кадровая устойчивость %"))
    availability = operational_available * REGION_OPERATIONAL_WEIGHT
    if pd.notna(staffing):
        availability += REGION_STAFFING_WEIGHT
    return round(availability, 4)


def _region_index(row: pd.Series):
    weighted_sum = 0.0
    available_weight = 0.0

    for column, weight in OPERATIONAL_WEIGHTS.items():
        value = _clip_pct(row.get(column))
        if pd.notna(value):
            component_weight = weight * REGION_OPERATIONAL_WEIGHT
            weighted_sum += float(value) * component_weight
            available_weight += component_weight

    staffing = _clip_pct(row.get("Кадровая устойчивость %"))
    if pd.notna(staffing):
        weighted_sum += float(staffing) * REGION_STAFFING_WEIGHT
        available_weight += REGION_STAFFING_WEIGHT

    if available_weight == 0:
        return pd.NA
    return weighted_sum / available_weight


def _breach_flags(row: pd.Series, level: str = "soft") -> dict[str, bool]:
    if level == "hard":
        kpi_min = HARD_KPI_MIN
        okk_min = HARD_OKK_MIN
        learn_min = HARD_LEARN_MIN
        fraud_max = HARD_FRAUD_MAX
        risk_max = HARD_RISK_MAX
        vacancy_share_max = HARD_VACANCY_SHARE_MAX
        turnover_max = HARD_TURNOVER_MAX
        net_outflow_min = HARD_NET_OUTFLOW_MIN
        staffing_min = HARD_STAFFING_MIN
    else:
        kpi_min = SOFT_KPI_MIN
        okk_min = SOFT_OKK_MIN
        learn_min = SOFT_LEARN_MIN
        fraud_max = SOFT_FRAUD_MAX
        risk_max = SOFT_RISK_MAX
        vacancy_share_max = SOFT_VACANCY_SHARE_MAX
        turnover_max = SOFT_TURNOVER_MAX
        net_outflow_min = SOFT_NET_OUTFLOW_MIN
        staffing_min = SOFT_STAFFING_MIN

    kpi = row.get("KPI проекта %")
    okk = row.get("Качество визитов %")
    learn = row.get("Обязательное обучение %")
    fraud = row.get("Фрод %")
    risk = row.get("Риск ухода структуры eNPS %")
    vacancy_share = row.get("Доля вакансий к активным МЕ %")
    turnover = row.get("Текучесть %")
    net_outflow = row.get("Кадровый отток")
    staffing = row.get("Кадровая устойчивость %")

    return {
        "risk": pd.notna(risk) and risk >= risk_max,
        "okk": pd.notna(okk) and okk < okk_min,
        "learn": pd.notna(learn) and learn < learn_min,
        "fraud": pd.notna(fraud) and fraud > fraud_max,
        "kpi": pd.notna(kpi) and kpi < kpi_min,
        "vacancy": pd.notna(vacancy_share) and vacancy_share >= vacancy_share_max,
        "turnover": pd.notna(turnover) and turnover >= turnover_max,
        "outflow": pd.notna(net_outflow) and net_outflow >= net_outflow_min,
        "staffing": pd.notna(staffing) and staffing < staffing_min,
    }


def _status(row: pd.Series) -> str | None:
    region_index = row.get("Индекс региона %")
    availability = row.get("Доступность метрик %")

    if pd.isna(region_index) or pd.isna(availability):
        return None
    if float(availability) < MIN_REGION_METRIC_AVAILABILITY:
        return "Недостаточно данных"

    soft_flags = _breach_flags(row, level="soft")
    hard_flags = _breach_flags(row, level="hard")
    soft_count = sum(soft_flags.values())
    hard_count = sum(hard_flags.values())
    staffing_collapse = hard_flags.get("turnover", False) and hard_flags.get("outflow", False)

    if float(region_index) < REGION_CONTROL_MIN or hard_count >= 2 or staffing_collapse:
        return "Высокий риск"
    if float(region_index) < REGION_STABLE_MIN or hard_count >= 1 or soft_count >= 2:
        return "Контроль"
    return "Стабильно"


def _index_zone(row: pd.Series) -> str | None:
    region_index = row.get("Индекс региона %")
    availability = row.get("Доступность метрик %")
    if pd.isna(region_index) or pd.isna(availability):
        return None
    if float(availability) < MIN_REGION_METRIC_AVAILABILITY:
        return "Недостаточно данных"
    if float(region_index) < REGION_CONTROL_MIN:
        return "Красная зона"
    if float(region_index) < REGION_STABLE_MIN:
        return "Желтая зона"
    return "Зеленая зона"


def _signal_count(row: pd.Series, level: str) -> int:
    return int(sum(_breach_flags(row, level=level).values()))


def _signal_text(row: pd.Series, level: str) -> str | None:
    flags = _breach_flags(row, level=level)
    labels = [SIGNAL_LABELS[key] for key, flagged in flags.items() if flagged]
    if not labels:
        return None
    return ", ".join(labels)


def _priority_score(row: pd.Series) -> float:
    status = row.get("Статус региона")
    if status in ["Стабильно", "Недостаточно данных"] or pd.isna(status):
        return 0.0

    region_index = row.get("Индекс региона %")
    kpi = row.get("KPI проекта %")
    okk = row.get("Качество визитов %")
    learn = row.get("Обязательное обучение %")
    fraud = row.get("Фрод %")
    risk = row.get("Риск ухода структуры eNPS %")
    vacancy_share = row.get("Доля вакансий к активным МЕ %")
    turnover = row.get("Текучесть %")
    net_outflow = row.get("Кадровый отток")
    staffing = row.get("Кадровая устойчивость %")

    score = 0.0
    if pd.notna(region_index):
        score += max(0.0, REGION_STABLE_MIN - region_index) * 100
    if pd.notna(kpi):
        score += max(0.0, SOFT_KPI_MIN - kpi) * 120
    if pd.notna(okk):
        score += max(0.0, SOFT_OKK_MIN - okk) * 150
    if pd.notna(learn):
        score += max(0.0, SOFT_LEARN_MIN - learn) * 100
    if pd.notna(fraud):
        score += max(0.0, fraud - SOFT_FRAUD_MAX) * 180
    if pd.notna(risk):
        score += max(0.0, risk - SOFT_RISK_MAX) * 160
    if pd.notna(vacancy_share):
        score += max(0.0, vacancy_share - SOFT_VACANCY_SHARE_MAX) * 120
    if pd.notna(turnover):
        score += max(0.0, turnover - SOFT_TURNOVER_MAX) * 120
    if pd.notna(net_outflow):
        score += max(0.0, net_outflow - SOFT_NET_OUTFLOW_MIN) * 2
    if pd.notna(staffing):
        score += max(0.0, SOFT_STAFFING_MIN - staffing) * 120
    return round(score, 4)


def _priority_text(row: pd.Series) -> str | None:
    region = row.get("Регион BI")
    if pd.isna(region):
        return None
    if row.get("Статус региона") in ["Стабильно", "Недостаточно данных"]:
        return None

    reason_scores = [
        (
            max(0.0, REGION_STABLE_MIN - row.get("Индекс региона %", np.nan)) * 100
            if pd.notna(row.get("Индекс региона %"))
            else 0.0,
            "снижен индекс региона",
        ),
        (
            max(0.0, row.get("Риск ухода структуры eNPS %", np.nan) - SOFT_RISK_MAX) * 160
            if pd.notna(row.get("Риск ухода структуры eNPS %"))
            else 0.0,
            "повышенный риск eNPS",
        ),
        (
            max(0.0, SOFT_OKK_MIN - row.get("Качество визитов %", np.nan)) * 150
            if pd.notna(row.get("Качество визитов %"))
            else 0.0,
            "слабое ОКК",
        ),
        (
            max(0.0, SOFT_LEARN_MIN - row.get("Обязательное обучение %", np.nan)) * 100
            if pd.notna(row.get("Обязательное обучение %"))
            else 0.0,
            "слабое обучение",
        ),
        (
            max(0.0, row.get("Фрод %", np.nan) - SOFT_FRAUD_MAX) * 180
            if pd.notna(row.get("Фрод %"))
            else 0.0,
            "высокий фрод",
        ),
        (
            max(0.0, SOFT_KPI_MIN - row.get("KPI проекта %", np.nan)) * 120
            if pd.notna(row.get("KPI проекта %"))
            else 0.0,
            "снижен KPI",
        ),
        (
            max(0.0, row.get("Доля вакансий к активным МЕ %", np.nan) - SOFT_VACANCY_SHARE_MAX) * 120
            if pd.notna(row.get("Доля вакансий к активным МЕ %"))
            else 0.0,
            "много открытых вакансий",
        ),
        (
            max(0.0, row.get("Текучесть %", np.nan) - SOFT_TURNOVER_MAX) * 120
            if pd.notna(row.get("Текучесть %"))
            else 0.0,
            "высокие увольнения",
        ),
        (
            max(0.0, row.get("Кадровый отток", np.nan) - SOFT_NET_OUTFLOW_MIN) * 2
            if pd.notna(row.get("Кадровый отток"))
            else 0.0,
            "есть чистый отток",
        ),
        (
            max(0.0, SOFT_STAFFING_MIN - row.get("Кадровая устойчивость %", np.nan)) * 120
            if pd.notna(row.get("Кадровая устойчивость %"))
            else 0.0,
            "низкая кадровая устойчивость",
        ),
    ]
    messages = [message for score, message in sorted(reason_scores, reverse=True) if score > 0]
    if not messages:
        return None

    return f"{region}: {', '.join(messages[:2])}."


def _kpi_traffic_light(value: float | None) -> str | None:
    if pd.isna(value):
        return None
    if value < KPI_RED_MAX:
        return "Красный"
    if value < KPI_YELLOW_MAX:
        return "Желтый"
    return "Зеленый"


def build_page1_monthly_snapshot() -> pd.DataFrame:
    settings = load_settings()
    out_dir = Path(settings["paths"]["out"])

    kpi = pd.read_parquet(out_dir / "kpi_fact.parquet")
    kpi_tt_path = out_dir / "kpi_client_tt_fact.parquet"
    kpi_tt_direct = pd.read_parquet(kpi_tt_path) if kpi_tt_path.exists() else pd.DataFrame()
    okk = pd.read_parquet(out_dir / "okk_fact.parquet")
    learning_monthly = pd.read_parquet(out_dir / "learning_monthly.parquet")
    enps = pd.read_parquet(out_dir / "enps_fact.parquet")
    oed = pd.read_parquet(out_dir / "fact_oed.parquet")

    kpi_monthly = _build_kpi_monthly(kpi, kpi_tt_direct=kpi_tt_direct)
    okk_monthly = _build_okk_monthly(okk)
    learn_monthly = _build_learning_monthly(learning_monthly)
    enps_quarterly = _build_enps_quarterly(enps)
    oed_quarterly = _build_oed_quarterly(oed)
    staffing_monthly, staffing_current = _build_staffing_page1(out_dir)
    staffing_signal_cols = [
        "Открытых вакансий",
        "Открытых вакансий МЕ",
        "Открытых вакансий СВ",
        "Приостановленных вакансий",
        "Нанято",
        "Уволено",
        "Чистый отток",
        "Баланс персонала",
    ]
    staffing_base = staffing_monthly.copy()
    available_signal_cols = [c for c in staffing_signal_cols if c in staffing_base.columns]
    if available_signal_cols:
        staffing_base = staffing_base[
            staffing_base[available_signal_cols]
            .apply(pd.to_numeric, errors="coerce")
            .fillna(0)
            .abs()
            .sum(axis=1)
            .gt(0)
        ].copy()

    base_keys = pd.concat(
        [
            kpi_monthly[["MonthStart", "YearMonth", "Регион BI"]],
            okk_monthly[["MonthStart", "YearMonth", "Регион BI"]],
            staffing_base[["MonthStart", "YearMonth", "Регион BI"]],
        ],
        ignore_index=True,
    ).dropna(subset=["MonthStart", "YearMonth", "Регион BI"]).drop_duplicates()
    base_keys = base_keys[pd.to_numeric(base_keys["YearMonth"], errors="coerce").ge(REPORT_START_YEARMONTH)].copy()

    snapshot = base_keys.merge(
        kpi_monthly,
        on=["MonthStart", "YearMonth", "Регион BI"],
        how="left",
    ).merge(
        okk_monthly,
        on=["MonthStart", "YearMonth", "Регион BI"],
        how="left",
    ).merge(
        learn_monthly,
        on=["MonthStart", "YearMonth", "Регион BI"],
        how="left",
    )

    snapshot = _attach_last_quarter_metric(snapshot, enps_quarterly, "Риск ухода структуры eNPS %")
    snapshot = _attach_last_quarter_metric(snapshot, oed_quarterly, "Оценка команды %")
    snapshot = snapshot.merge(
        staffing_monthly,
        on=["MonthStart", "YearMonth", "Регион BI"],
        how="left",
    ).merge(
        staffing_current,
        on="Регион BI",
        how="left",
    )
    snapshot = _clean_region_values(snapshot)
    snapshot = snapshot[snapshot["Регион BI"].notna()].copy()
    snapshot = snapshot[snapshot["Регион BI"].isin(_core_regions())].copy()

    for column in ["Нанято", "Уволено", "Чистый отток", "Баланс персонала"]:
        if column in snapshot.columns:
            snapshot[column] = pd.to_numeric(snapshot[column], errors="coerce").fillna(0)
    if {"Нанято", "Уволено"}.issubset(snapshot.columns):
        snapshot["Кадровый отток"] = (snapshot["Уволено"] - snapshot["Нанято"]).clip(lower=0)
        snapshot["Кадровый приток"] = (snapshot["Нанято"] - snapshot["Уволено"]).clip(lower=0)
        snapshot["Баланс персонала"] = snapshot["Нанято"] - snapshot["Уволено"]
    if {"Уволено", "Активных МЕ"}.issubset(snapshot.columns):
        snapshot["Текучесть %"] = snapshot["Уволено"] / snapshot["Активных МЕ"].replace(0, np.nan)
    if {"Открытых вакансий МЕ", "Активных МЕ"}.issubset(snapshot.columns):
        snapshot["Доля вакансий к активным МЕ %"] = (
            snapshot["Открытых вакансий МЕ"] / snapshot["Активных МЕ"].replace(0, np.nan)
        )
    if {"Доля вакансий к активным МЕ %", "Текучесть %", "Кадровый отток", "Активных МЕ"}.issubset(snapshot.columns):
        net_outflow_share = snapshot["Кадровый отток"] / snapshot["Активных МЕ"].replace(0, np.nan)
        staffing_penalty = (
            snapshot["Доля вакансий к активным МЕ %"].fillna(0) * 0.50
            + snapshot["Текучесть %"].fillna(0) * 0.30
            + net_outflow_share.fillna(0) * 0.20
        )
        snapshot["Кадровая устойчивость %"] = (1 - staffing_penalty).clip(lower=0, upper=1)
    if {"Риск ухода структуры eNPS %", "Текучесть %", "Доля вакансий к активным МЕ %"}.issubset(snapshot.columns):
        snapshot["Кадровый риск %"] = snapshot[["Текучесть %", "Доля вакансий к активным МЕ %"]].max(axis=1)
        snapshot["Риск ухода общий %"] = snapshot[["Риск ухода структуры eNPS %", "Кадровый риск %"]].max(axis=1)

    if "Фрод %" in snapshot.columns:
        snapshot["Антифрод %"] = (1 - snapshot["Фрод %"]).clip(lower=0, upper=1)
    if "Риск ухода структуры eNPS %" in snapshot.columns:
        snapshot["Климат %"] = (1 - snapshot["Риск ухода структуры eNPS %"]).clip(lower=0, upper=1)

    snapshot["Операционная эффективность %"] = snapshot.apply(_operational_score, axis=1)
    snapshot["Доступность метрик %"] = snapshot.apply(_region_metric_availability, axis=1)
    snapshot["Индекс региона %"] = snapshot.apply(_region_index, axis=1)
    snapshot["Зона индекса"] = snapshot.apply(_index_zone, axis=1)
    snapshot["Красных сигналов"] = snapshot.apply(lambda row: _signal_count(row, "hard"), axis=1)
    snapshot["Мягких сигналов"] = snapshot.apply(lambda row: _signal_count(row, "soft"), axis=1)
    snapshot["Красные сигналы"] = snapshot.apply(lambda row: _signal_text(row, "hard"), axis=1)
    snapshot["Мягкие сигналы"] = snapshot.apply(lambda row: _signal_text(row, "soft"), axis=1)

    numeric_columns = [
        "KPI проекта %",
        "Качество визитов %",
        "Фрод %",
        "Фрод кол-во",
        "Обязательное обучение %",
        "Риск ухода структуры eNPS %",
        "Оценка команды %",
        "Антифрод %",
        "Климат %",
        "Операционная эффективность %",
        "Доступность метрик %",
        "Индекс региона %",
        "Красных сигналов",
        "Мягких сигналов",
        "Скоринг приоритета",
        "Нанято",
        "Уволено",
        "Чистый отток",
        "Баланс персонала",
        "Кадровый отток",
        "Кадровый приток",
        "Активных МЕ",
        "Активных СВ",
        "Активных ТМ",
        "Открытых вакансий",
        "Открытых вакансий МЕ",
        "Открытых вакансий СВ",
        "Приостановленных вакансий",
        "Доля вакансий к активным МЕ %",
        "Текучесть %",
        "Кадровая устойчивость %",
        "Кадровый риск %",
        "Риск ухода общий %",
    ]
    for column in numeric_columns:
        if column in snapshot.columns:
            snapshot[column] = pd.to_numeric(snapshot[column], errors="coerce")

    snapshot["Год"] = snapshot["MonthStart"].dt.year.astype("Int64")
    snapshot["Месяц номер"] = snapshot["MonthStart"].dt.month.astype("Int64")
    month_full_map = {
        1: "январь",
        2: "февраль",
        3: "март",
        4: "апрель",
        5: "май",
        6: "июнь",
        7: "июль",
        8: "август",
        9: "сентябрь",
        10: "октябрь",
        11: "ноябрь",
        12: "декабрь",
    }
    month_short_map = {
        1: "янв",
        2: "фев",
        3: "мар",
        4: "апр",
        5: "май",
        6: "июн",
        7: "июл",
        8: "авг",
        9: "сен",
        10: "окт",
        11: "ноя",
        12: "дек",
    }
    snapshot["Название месяца"] = snapshot["Месяц номер"].map(month_full_map)
    snapshot["Месяц коротко"] = snapshot["Месяц номер"].map(month_short_map)
    snapshot["Месяц"] = snapshot["Месяц коротко"] + " " + snapshot["Год"].astype(str)
    snapshot["Ключ регион-месяц"] = snapshot["YearMonth"].astype("Int64").astype(str) + "|" + snapshot["Регион BI"].astype(str)

    snapshot["Статус региона"] = snapshot.apply(_status, axis=1)
    snapshot["Скоринг приоритета"] = snapshot.apply(_priority_score, axis=1)
    snapshot["KPI светофор"] = snapshot["KPI проекта %"].apply(_kpi_traffic_light)
    snapshot["KPI светофор порядок"] = snapshot["KPI светофор"].map(
        {"Красный": 1, "Желтый": 2, "Зеленый": 3}
    ).astype("Int64")

    snapshot["Текст приоритета"] = snapshot.apply(_priority_text, axis=1)

    pieces = []
    for month, month_df in snapshot.groupby("MonthStart"):
        ranked = month_df.copy()
        ranked["Ранг приоритета"] = pd.NA

        actionable = ranked[ranked["Текст приоритета"].notna()].copy()
        actionable = actionable.sort_values(
            ["Скоринг приоритета", "Регион BI"],
            ascending=[False, True],
        )
        actionable["Ранг приоритета"] = range(1, len(actionable) + 1)

        ranked = ranked.merge(
            actionable[["MonthStart", "Регион BI", "Ранг приоритета"]],
            on=["MonthStart", "Регион BI"],
            how="left",
            suffixes=("", "_new"),
        )
        ranked["Ранг приоритета"] = ranked["Ранг приоритета_new"].combine_first(ranked["Ранг приоритета"])
        ranked = ranked.drop(columns=["Ранг приоритета_new"])
        pieces.append(ranked)
    snapshot = pd.concat(pieces, ignore_index=True)
    snapshot["Ранг приоритета"] = pd.to_numeric(snapshot["Ранг приоритета"], errors="coerce").astype("Int64")

    snapshot = snapshot.drop(
        columns=[
            "YearMonth кадрового среза",
            "MonthStart кадрового среза",
            "Кадровый приток",
            "Ключ регион-месяц",
            "Зона индекса",
        ],
        errors="ignore",
    )

    output = out_dir / "page1_region_monthly_snapshot.parquet"
    save_parquet(snapshot, str(output))

    print(f"\n  Page1 snapshot: {len(snapshot)} строк")
    print(
        "  Периоды: "
        f"{snapshot['MonthStart'].min():%Y-%m} -> {snapshot['MonthStart'].max():%Y-%m}"
    )
    return snapshot


if __name__ == "__main__":
    build_page1_monthly_snapshot()
