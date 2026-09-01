import re
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

if __package__ is None or __package__ == "":
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from scripts.utils import (
    canonical_region_from_text,
    enrich_region_columns,
    get_active_users_scope,
    load_settings,
    normalize_dim,
    save_parquet,
)
from scripts.staffing_utils import mode_or_first
from scripts.rtm_utils import load_login_employee_map, load_rtm_employee_visits
from scripts.kpi_org_mapping import load_current_tm_assignments, attach_kpi_rtm_org
from scripts.cache_utils import load_or_build_parquet_bundle, source_set_digest
from scripts.client_sql_exports import load_client_sql_visits


KPI_METRICS = ("PICOS", "OSA", "TOP16")
KPI_VALUE_COLUMNS = [
    f"{metric} {value}"
    for metric in KPI_METRICS
    for value in ("план %", "факт %", "выполнение %")
]


def _directory_dimension(dim: pd.DataFrame | None) -> pd.DataFrame:
    if dim is None or dim.empty:
        return pd.DataFrame()
    if "ID сотрудника" in dim.columns:
        return dim.copy()
    renamed = dim.rename(
        columns={
            "employee_id": "ID сотрудника",
            "full_name": "ФИО",
            "city": "Город",
            "region": "Регион",
            "project": "Проект",
            "groups": "Группы",
            "attribute": "Атрибут",
            "manager_id": "ID руководителя",
            "is_active": "Активен",
        }
    )
    return enrich_region_columns(renamed)


def _log_phase(label: str, started: float) -> float:
    now = time.perf_counter()
    print(f"  KPI | {label}: {now - started:.2f} сек.")
    return now


def _detect_period(filename: str) -> tuple[str, int, int]:
    stem = Path(filename).stem.lower()
    match = re.search(r"(?<!\d)(\d{1,2})\.(20\d{2})(?!\d)", stem)
    if match:
        month = int(match.group(1))
        year = int(match.group(2))
        return f"{year}_{month:02d}", year, month
    return "Unknown", 2026, 0


def _normalize_label(value) -> str:
    text = str(value or "").replace("\xa0", " ").strip().lower()
    text = re.sub(r"\s+", " ", text)
    return text


def _flatten_columns(columns) -> list[str]:
    flat: list[str] = []
    for col in columns:
        if isinstance(col, tuple):
            parts = []
            for part in col:
                part_str = str(part).strip()
                if not part_str or part_str.lower().startswith("unnamed"):
                    continue
                parts.append(part_str)
            flat.append(" | ".join(parts))
        else:
            flat.append(str(col).strip())
    return flat


def _find_first(columns: list[str], *needles: str) -> str | None:
    normalized_needles = [_normalize_label(n) for n in needles]
    for col in columns:
        label = _normalize_label(col)
        if all(needle in label for needle in normalized_needles):
            return col
    return None


def _kpi_number_index(column_name: str) -> int:
    normalized = _normalize_label(column_name)
    match = re.search(r"kpi №\s*(\d+)", normalized)
    if match:
        return int(match.group(1))
    match = re.search(r"\.(\d+)$", normalized)
    if match:
        return int(match.group(1)) + 1
    return 1


def _to_numeric(series: pd.Series) -> pd.Series:
    cleaned = (
        series.astype(str)
        .str.replace("\xa0", " ", regex=False)
        .str.replace("%", "", regex=False)
        .str.replace(",", ".", regex=False)
        .str.strip()
    )
    cleaned = cleaned.replace({"": pd.NA, "nan": pd.NA, "None": pd.NA})
    return pd.to_numeric(cleaned, errors="coerce")


def _normalize_pct(series: pd.Series) -> pd.Series:
    num = _to_numeric(series)
    return num.where(num <= 1.5, num / 100.0)


def _valid_kpi_assignment(metric: pd.Series, goal: pd.Series) -> pd.Series:
    return metric.notna() & goal.notna() & goal.gt(0)


def _calculate_metric_execution(plan: pd.Series, fact: pd.Series) -> pd.Series:
    numeric_plan = pd.to_numeric(plan, errors="coerce")
    numeric_fact = pd.to_numeric(fact, errors="coerce")
    valid = numeric_plan.gt(0) & numeric_fact.notna()
    ratio = (numeric_fact / numeric_plan.where(numeric_plan.gt(0))).round(10)
    result = pd.Series(np.nan, index=plan.index, dtype="float64")
    result.loc[valid & ratio.lt(0.75)] = 0.0
    result.loc[valid & ratio.ge(0.75)] = ratio.loc[valid & ratio.ge(0.75)].clip(upper=1.0)
    return result


def _normalize_tt_code(series: pd.Series) -> pd.Series:
    num = _to_numeric(series)
    as_int = num.dropna().round().astype("Int64").astype(str)
    result = series.astype(str).str.strip()
    result.loc[num.notna()] = as_int
    result = result.str.replace(r"\.0+$", "", regex=True).str.strip()
    result = result.replace({"": pd.NA, "nan": pd.NA, "<NA>": pd.NA})
    return result


def _weighted_mean(values: pd.Series, weights: pd.Series):
    mask = values.notna() & weights.notna() & (weights > 0)
    if not mask.any():
        return pd.NA
    return float((values[mask] * weights[mask]).sum() / weights[mask].sum())


def _calculate_project_kpi(tt_fact: pd.DataFrame) -> pd.Series:
    picos = pd.to_numeric(tt_fact["PICOS выполнение %"], errors="coerce")
    osa = pd.to_numeric(tt_fact["OSA выполнение %"], errors="coerce")
    top16 = pd.to_numeric(tt_fact["TOP16 выполнение %"], errors="coerce")
    result = picos.astype("float64").copy()
    picos_available = picos.notna()
    osa_top16_available = (
        osa.notna()
        & top16.notna()
    )
    result.loc[~picos_available & osa_top16_available] = (
        osa.loc[~picos_available & osa_top16_available]
        + top16.loc[~picos_available & osa_top16_available]
    ) / 2.0
    osa_only = ~picos_available & osa.notna() & top16.isna()
    top16_only = ~picos_available & osa.isna() & top16.notna()
    result.loc[osa_only] = osa.loc[osa_only]
    result.loc[top16_only] = top16.loc[top16_only]
    return result


def _canonical_kpi_name(value) -> str | None:
    if pd.isna(value):
        return None
    normalized = re.sub(r"[^A-ZА-Я0-9]+", "", str(value).upper())
    if "PICOS" in normalized:
        return "PICOS"
    if normalized == "OSA":
        return "OSA"
    if "TOP16" in normalized or "ТОП16" in normalized:
        return "TOP16"
    return None


def _build_city_region_lookup(dim: pd.DataFrame) -> dict[str, str]:
    if dim is None or dim.empty:
        return {}
    work = dim.copy()
    if "Город" not in work.columns or "Регион BI" not in work.columns:
        return {}
    work["city_norm"] = work["Город"].astype(str).str.strip().str.upper()
    work = work[work["city_norm"].ne("") & work["Регион BI"].notna()].copy()
    if work.empty:
        return {}
    grouped = (
        work.groupby("city_norm", dropna=False)["Регион BI"]
        .agg(mode_or_first)
        .to_dict()
    )
    return grouped


def _map_client_region(
    source_region: pd.Series,
    city: pd.Series,
    city_region_lookup: dict[str, str],
) -> pd.Series:
    normalized_region = source_region.astype("string").str.strip()
    unique_regions = normalized_region.dropna().loc[lambda values: values.ne("")].unique()
    source_lookup = {
        value: canonical_region_from_text(value)
        for value in unique_regions
    }
    region_from_source = normalized_region.map(source_lookup)
    region_from_city = city.astype("string").str.strip().str.upper().map(city_region_lookup)
    return region_from_source.combine_first(region_from_city)


def _load_client_kpi_file(path: Path, city_region_lookup: dict[str, str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    period, year, month = _detect_period(path.name)
    xl = pd.ExcelFile(path)
    sheet = next((name for name in xl.sheet_names if "адрес" in _normalize_label(name)), xl.sheet_names[0])
    raw = xl.parse(sheet, header=[1, 2])
    raw.columns = _flatten_columns(raw.columns)

    cols = list(raw.columns)
    tt_col = _find_first(cols, "уникальный номер")
    network_col = _find_first(cols, "наименование сети")
    city_col = _find_first(cols, "город")
    address_col = _find_first(cols, "адрес торговой точки")
    scenario_col = _find_first(cols, "сценарий")
    agency_sv_col = _find_first(cols, "agency sv")
    region_col = _find_first(cols, "регион")
    sg_col = _find_first(cols, "группа продаж")

    if tt_col is None:
        return pd.DataFrame(), pd.DataFrame()

    work = raw.copy()
    work["ТТ"] = _normalize_tt_code(work[tt_col])
    work = work[work["ТТ"].notna()].copy()
    if work.empty:
        return pd.DataFrame(), pd.DataFrame()

    work["Период"] = period
    work["Год"] = year
    work["Месяц"] = month
    work["MonthStart"] = pd.Timestamp(year=year, month=month, day=1)
    work["YearMonth"] = year * 100 + month
    work["Сеть"] = work[network_col] if network_col else pd.NA
    work["Город"] = work[city_col] if city_col else pd.NA
    work["Адрес"] = work[address_col] if address_col else pd.NA
    work["Сценарий"] = work[scenario_col] if scenario_col else pd.NA
    work["Код маршрута СВ"] = work[agency_sv_col] if agency_sv_col else pd.NA
    work["Регион"] = work[region_col] if region_col else pd.NA
    work["Группа продаж"] = work[sg_col] if sg_col else pd.NA

    goal_cols = sorted(
        [c for c in cols if "цель по kpi №" in _normalize_label(c)],
        key=_kpi_number_index,
    )
    name_cols = sorted(
        [
            c
            for c in cols
            if "наименование по kpi" in _normalize_label(c)
            and "целевые показатели (kpi) факт" not in _normalize_label(c)
        ],
        key=_kpi_number_index,
    )
    fact_cols = sorted(
        [
            c
            for c in cols
            if _normalize_label(c).split("|")[-1].strip().startswith("факт")
            and "kpi" in _normalize_label(c).split("|")[-1]
        ],
        key=_kpi_number_index,
    )
    long_rows: list[pd.DataFrame] = []

    for idx in range(3):
        name_col = name_cols[idx] if idx < len(name_cols) else None
        goal_col = goal_cols[idx] if idx < len(goal_cols) else None
        fact_col = fact_cols[idx] if idx < len(fact_cols) else None

        source_name = work[name_col] if name_col else pd.Series(pd.NA, index=work.index)
        block_name = source_name.map(_canonical_kpi_name).astype("string")
        block_goal = _normalize_pct(work[goal_col]) if goal_col else pd.Series(float("nan"), index=work.index, dtype="float64")
        block_fact = _normalize_pct(work[fact_col]) if fact_col else pd.Series(float("nan"), index=work.index, dtype="float64")
        block_scale = _calculate_metric_execution(block_goal, block_fact)
        block_weight = block_name.map({"PICOS": 1.0, "OSA": 0.5, "TOP16": 0.5}).astype("float64")

        part = pd.DataFrame(
            {
                "Период": period,
                "Год": year,
                "Месяц": month,
                "MonthStart": pd.Timestamp(year=year, month=month, day=1),
                "YearMonth": year * 100 + month,
                "ТТ": work["ТТ"],
                "Сеть": work["Сеть"],
                "Город": work["Город"],
                "Адрес": work["Адрес"],
                "Сценарий": work["Сценарий"],
                "Код маршрута СВ": work["Код маршрута СВ"],
                "Регион": work["Регион"],
                "Группа продаж": work["Группа продаж"],
                "Метрика KPI": block_name,
                "Вес KPI": block_weight,
                "План KPI": block_goal,
                "Факт KPI": block_fact,
                "Выполнение KPI %": block_scale,
            }
        )
        long_rows.append(
            part[_valid_kpi_assignment(part["Метрика KPI"], part["План KPI"])].copy()
        )

    work["Регион BI"] = _map_client_region(
        work["Регион"],
        work["Город"],
        city_region_lookup,
    )
    long_fact = pd.concat(long_rows, ignore_index=True)
    metric_sets = (
        long_fact.groupby(["MonthStart", "ТТ"], dropna=False)["Метрика KPI"]
        .agg(lambda values: frozenset(values.dropna().astype(str)))
    )
    metric_key = pd.MultiIndex.from_frame(long_fact[["MonthStart", "ТТ"]])
    row_metric_sets = metric_sets.reindex(metric_key).reset_index(drop=True)
    has_picos = row_metric_sets.map(lambda values: "PICOS" in values)
    has_osa = row_metric_sets.map(lambda values: "OSA" in values)
    has_top16 = row_metric_sets.map(lambda values: "TOP16" in values)
    long_fact["Вес KPI"] = 0.0
    long_fact.loc[has_picos & long_fact["Метрика KPI"].eq("PICOS"), "Вес KPI"] = 1.0
    no_picos = ~has_picos
    both_osa_top16 = no_picos & has_osa & has_top16
    long_fact.loc[
        both_osa_top16 & long_fact["Метрика KPI"].isin(["OSA", "TOP16"]),
        "Вес KPI",
    ] = 0.5
    long_fact.loc[
        no_picos & has_osa & ~has_top16 & long_fact["Метрика KPI"].eq("OSA"),
        "Вес KPI",
    ] = 1.0
    long_fact.loc[
        no_picos & ~has_osa & has_top16 & long_fact["Метрика KPI"].eq("TOP16"),
        "Вес KPI",
    ] = 1.0
    long_fact["Регион BI"] = _map_client_region(
        long_fact["Регион"],
        long_fact["Город"],
        city_region_lookup,
    )
    long_fact = long_fact.drop_duplicates(["MonthStart", "ТТ", "Метрика KPI"], keep="first")

    tt_fact = work[
        [
            "MonthStart",
            "YearMonth",
            "ТТ",
            "Сеть",
            "Город",
            "Адрес",
            "Регион BI",
            "Код маршрута СВ",
        ]
    ].drop_duplicates(["MonthStart", "ТТ"])

    for metric in KPI_METRICS:
        metric_rows = long_fact[long_fact["Метрика KPI"].eq(metric)][
            ["MonthStart", "ТТ", "План KPI", "Факт KPI", "Выполнение KPI %"]
        ].rename(
            columns={
                "План KPI": f"{metric} план %",
                "Факт KPI": f"{metric} факт %",
                "Выполнение KPI %": f"{metric} выполнение %",
            }
        )
        tt_fact = tt_fact.merge(metric_rows, on=["MonthStart", "ТТ"], how="left")

    tt_fact["KPI проекта %"] = _calculate_project_kpi(tt_fact)
    tt_fact = tt_fact[tt_fact[KPI_VALUE_COLUMNS].notna().any(axis=1)].copy()

    long_fact = long_fact[
        [
            "MonthStart",
            "YearMonth",
            "ТТ",
            "Сеть",
            "Город",
            "Адрес",
            "Регион BI",
            "Метрика KPI",
            "Вес KPI",
            "План KPI",
            "Факт KPI",
            "Выполнение KPI %",
        ]
    ]
    return tt_fact.reset_index(drop=True), long_fact.reset_index(drop=True)


def _build_client_tt_fact(
    kpi_root: Path,
    dim: pd.DataFrame,
    cache_root: Path | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    client_dir = kpi_root / "fact kpi"

    files = []
    if client_dir.exists():
        files.extend(client_dir.glob("*.xlsx"))
    files.extend(
        path
        for path in kpi_root.glob("*.xlsx")
        if "fact" in _normalize_label(path.name) and "kpi" in _normalize_label(path.name)
    )
    files = sorted({path.resolve(): path for path in files}.values())
    if not files:
        return pd.DataFrame(), pd.DataFrame()

    city_region_lookup = _build_city_region_lookup(dim)

    def build_frames() -> dict[str, pd.DataFrame]:
        tt_frames: list[pd.DataFrame] = []
        long_frames: list[pd.DataFrame] = []
        for path in files:
            try:
                tt_part, long_part = _load_client_kpi_file(path, city_region_lookup)
            except Exception as exc:
                print(f"    KPI: пропущен файл {path.name} — {exc.__class__.__name__}: {exc}")
                continue
            if not tt_part.empty:
                tt_frames.append(tt_part)
            if not long_part.empty:
                long_frames.append(long_part)
        tt_result = pd.concat(tt_frames, ignore_index=True) if tt_frames else pd.DataFrame()
        long_result = pd.concat(long_frames, ignore_index=True) if long_frames else pd.DataFrame()
        _, tt_result, long_result = _force_kpi_output_types(
            pd.DataFrame(),
            tt_result,
            long_result,
        )
        return {"tt": tt_result, "long": long_result}

    lookup_key = "|".join(
        f"{key}={value}"
        for key, value in sorted(city_region_lookup.items(), key=lambda item: str(item[0]))
    )
    cache_key = source_set_digest(files, kpi_root, "client-kpi-v1", extra_key=lookup_key)
    bundle, cache_hit = load_or_build_parquet_bundle(
        cache_root,
        cache_key,
        ("tt", "long"),
        build_frames,
    )
    print(f"  Клиентский KPI: {'кеш parquet' if cache_hit else 'прочитаны Excel'}")
    return bundle["tt"], bundle["long"]


def _enrich_tt_regions_from_okk(tt_fact: pd.DataFrame, okk: pd.DataFrame) -> pd.DataFrame:
    if tt_fact.empty or okk.empty:
        return tt_fact
    tt_region = (
        okk.dropna(subset=["MonthStart", "Код ТТ"])
        .groupby(["MonthStart", "YearMonth", "Код ТТ"], dropna=False)
        .agg(
            **{
                "Регион BI okk": ("Регион BI", mode_or_first),
                "Город okk": ("Город", mode_or_first) if "Город" in okk.columns else ("Адрес", lambda s: pd.NA),
            }
        )
        .reset_index()
        .rename(columns={"Код ТТ": "ТТ"})
    )
    merged = tt_fact.merge(tt_region, on=["MonthStart", "YearMonth", "ТТ"], how="left")
    if "Регион BI" in merged.columns:
        merged["Регион BI"] = merged["Регион BI"].combine_first(merged["Регион BI okk"])
    else:
        merged["Регион BI"] = merged["Регион BI okk"]
    if "Город" in merged.columns:
        merged["Город"] = merged["Город"].combine_first(merged["Город okk"])
    else:
        merged["Город"] = merged["Город okk"]
    return merged.drop(columns=["Регион BI okk", "Город okk"], errors="ignore")


def _build_merch_kpi_fact(tt_fact: pd.DataFrame, visits: pd.DataFrame, dim: pd.DataFrame) -> pd.DataFrame:
    if tt_fact.empty or visits.empty:
        return pd.DataFrame()

    visits = (
        visits.dropna(subset=["MonthStart", "ТТ", "ID сотрудника"])
        .groupby(["MonthStart", "YearMonth", "ТТ", "ID сотрудника"], dropna=False)
        .agg(
            **{
                "Визиты KPI": ("Ключ визита RTM", "nunique"),
                "Мерчендайзер": ("ФИО из логинов", "first"),
                "Логин": ("Логин", "first"),
                "ID супервайзера": ("ID супервайзера", "first"),
                "Супервайзер": ("Супервайзер", "first"),
                "ID территориального менеджера": ("ID территориального менеджера", "first"),
                "Территориальный менеджер": ("Территориальный менеджер", "first"),
                "Регион BI RTM": ("Регион BI", "first"),
            }
        )
        .reset_index()
        .rename(columns={"ID сотрудника": "ID мерчендайзера"})
    )

    merged = visits.merge(
        tt_fact[
            [
                "MonthStart",
                "YearMonth",
                "ТТ",
                "Сеть",
                "Город",
                "Код маршрута СВ",
                "KPI проекта %",
                *KPI_VALUE_COLUMNS,
                "Регион BI",
            ]
        ],
        on=["MonthStart", "YearMonth", "ТТ"],
        how="left",
        suffixes=("_visit", ""),
    )
    merged = merged[
        merged["KPI проекта %"].notna()
        | merged[KPI_VALUE_COLUMNS].notna().any(axis=1)
    ].copy()
    if merged.empty:
        return pd.DataFrame()

    merged["Регион BI"] = merged["Регион BI RTM"].combine_first(merged["Регион BI"])

    dim_work = pd.DataFrame() if dim is None else (normalize_dim(dim.copy()) if not dim.empty and "employee_id" not in dim.columns else dim.copy())
    if dim_work is not None and not dim_work.empty:
        dim_lookup = dim_work.rename(
            columns={
                "employee_id": "ID мерчендайзера",
                "full_name": "Мерчендайзер dim",
                "city": "Город dim",
            }
        )
        keep = [c for c in ["ID мерчендайзера", "Мерчендайзер dim", "Город dim"] if c in dim_lookup.columns]
        if keep:
            dim_lookup = dim_lookup[keep].drop_duplicates("ID мерчендайзера")
            merged = merged.merge(dim_lookup, on="ID мерчендайзера", how="left")
            if "Мерчендайзер dim" in merged.columns:
                merged["Мерчендайзер"] = merged["Мерчендайзер dim"].combine_first(merged["Мерчендайзер"])
            if "Город dim" in merged.columns:
                merged["Город"] = merged["Город"].combine_first(merged["Город dim"])

    group_columns = ["MonthStart", "YearMonth", "ID мерчендайзера"]
    weights = pd.to_numeric(merged["Визиты KPI"], errors="coerce").fillna(0)
    grouped = merged.groupby(group_columns, dropna=False, sort=False)
    result = grouped.agg(
        **{
            "Мерчендайзер": ("Мерчендайзер", "first"),
            "Город": ("Город", "first"),
            "Код маршрута СВ": ("Код маршрута СВ", "first"),
            "Визиты KPI": ("Визиты KPI", "sum"),
            "ТТ KPI": ("ТТ", "nunique"),
        }
    )

    org_columns = [
        "ID супервайзера",
        "Супервайзер",
        "ID территориального менеджера",
        "Территориальный менеджер",
        "Регион BI",
    ]
    org_weight = (
        merged[group_columns + org_columns]
        .assign(**{"_Визиты": weights})
        .groupby(group_columns + org_columns, dropna=False, sort=False)["_Визиты"]
        .sum()
        .reset_index()
        .sort_values(
            group_columns + ["_Визиты", "Территориальный менеджер", "Супервайзер"],
            ascending=[True, True, True, False, True, True],
            kind="mergesort",
        )
        .drop_duplicates(group_columns, keep="first")
        .set_index(group_columns)
    )
    for column in org_columns:
        result[column] = org_weight[column]

    groupers = [merged[column] for column in group_columns]
    for column in ["KPI проекта %", *KPI_VALUE_COLUMNS]:
        values = pd.to_numeric(merged[column], errors="coerce")
        valid = values.notna() & weights.gt(0)
        numerator = (values * weights).where(valid).groupby(
            groupers, dropna=False, sort=False
        ).sum(min_count=1)
        denominator = weights.where(valid).groupby(
            groupers, dropna=False, sort=False
        ).sum(min_count=1)
        result[column] = numerator / denominator

    picos_available = pd.to_numeric(merged["PICOS выполнение %"], errors="coerce").notna()
    osa_available = ~picos_available & pd.to_numeric(
        merged["OSA выполнение %"], errors="coerce"
    ).notna()
    top16_available = ~picos_available & pd.to_numeric(
        merged["TOP16 выполнение %"], errors="coerce"
    ).notna()
    valid_scenario = picos_available | osa_available | top16_available
    valid_visit_weight = weights.where(valid_scenario)
    scenario_denominator = valid_visit_weight.groupby(
        groupers,
        dropna=False,
        sort=False,
    ).sum(min_count=1)
    component_factors = {
        "PICOS вес в KPI %": picos_available.astype(float),
        "OSA вес в KPI %": (
            osa_available.astype(float)
            * np.where(top16_available, 0.5, 1.0)
        ),
        "TOP16 вес в KPI %": (
            top16_available.astype(float)
            * np.where(osa_available, 0.5, 1.0)
        ),
    }
    for column, factor in component_factors.items():
        numerator = (weights * factor).where(valid_scenario).groupby(
            groupers,
            dropna=False,
            sort=False,
        ).sum(min_count=1)
        result[column] = numerator / scenario_denominator

    result["Визиты KPI"] = pd.to_numeric(result["Визиты KPI"], errors="coerce").fillna(0).astype(int)
    result["ТТ KPI"] = pd.to_numeric(result["ТТ KPI"], errors="coerce").fillna(0).astype(int)
    return result.reset_index()


def _build_visit_kpi_timeline(
    tt_fact: pd.DataFrame,
    visits: pd.DataFrame,
) -> pd.DataFrame:
    if tt_fact.empty or visits.empty:
        return pd.DataFrame()

    visit_columns = [
        "MonthStart",
        "YearMonth",
        "Ключ визита RTM",
        "Дата визита",
        "ТТ",
        "ID сотрудника",
        "ФИО из логинов",
        "ID супервайзера",
        "Супервайзер",
        "ID территориального менеджера",
        "Территориальный менеджер",
        "Регион BI",
    ]
    mapped = visits[visit_columns].dropna(
        subset=["MonthStart", "Дата визита", "ТТ", "ID сотрудника"]
    ).copy()
    mapped = mapped.merge(
        tt_fact[
            [
                "MonthStart",
                "YearMonth",
                "ТТ",
                "Сеть",
                "Город",
                "Регион BI",
                "KPI проекта %",
                *KPI_VALUE_COLUMNS,
            ]
        ],
        on=["MonthStart", "YearMonth", "ТТ"],
        how="inner",
    )
    mapped["Регион BI"] = mapped.get("Регион BI_x").combine_first(mapped.get("Регион BI_y"))
    mapped["Мерчендайзер"] = mapped["ФИО из логинов"]
    mapped = mapped.rename(
        columns={
            "Ключ визита RTM": "ID визита",
            "ID сотрудника": "ID мерчендайзера",
        }
    )
    public_columns = [
        "MonthStart",
        "YearMonth",
        "Дата визита",
        "ID визита",
        "ТТ",
        "Сеть",
        "Город",
        "Регион BI",
        "ID мерчендайзера",
        "Мерчендайзер",
        "ID супервайзера",
        "Супервайзер",
        "ID территориального менеджера",
        "Территориальный менеджер",
        "KPI проекта %",
        *KPI_VALUE_COLUMNS,
    ]
    return (
        mapped[public_columns]
        .drop_duplicates(["ID визита", "ID мерчендайзера", "ТТ"])
        .sort_values(["Дата визита", "ID визита"], kind="mergesort")
        .reset_index(drop=True)
    )


def _force_kpi_output_types(
    merch_fact: pd.DataFrame,
    tt_fact: pd.DataFrame,
    tt_long: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    merch = merch_fact.copy()
    tt = tt_fact.copy()
    long = tt_long.copy()

    string_cols_common = [
        "ID супервайзера",
        "ID мерчендайзера",
        "ID территориального менеджера",
        "Супервайзер",
        "Мерчендайзер",
        "Территориальный менеджер",
        "Регион BI",
        "Город",
        "Код маршрута СВ",
        "Группа региона",
    ]
    float_cols_common = ["KPI проекта %", *KPI_VALUE_COLUMNS]

    for col in string_cols_common:
        if col in merch.columns:
            merch[col] = merch[col].astype("string").fillna("")
    for col in float_cols_common:
        if col in merch.columns:
            merch[col] = pd.to_numeric(merch[col], errors="coerce").astype("float64")
            if merch[col].notna().sum() == 0:
                merch[col] = pd.Series(np.nan, index=merch.index, dtype="float64")
    for col in ["YearMonth", "Визиты KPI", "ТТ KPI"]:
        if col in merch.columns:
            merch[col] = pd.to_numeric(merch[col], errors="coerce").astype("Int64")

    tt_string_cols = [
        "ТТ",
        "Сеть",
        "Город",
        "Адрес",
        "Код маршрута СВ",
        "Регион BI",
        "Группа региона",
    ]
    tt_float_cols = ["KPI проекта %", *KPI_VALUE_COLUMNS]
    for col in tt_string_cols:
        if col in tt.columns:
            tt[col] = tt[col].astype("string").fillna("")
    for col in tt_float_cols:
        if col in tt.columns:
            tt[col] = pd.to_numeric(tt[col], errors="coerce").astype("float64")
            if tt[col].notna().sum() == 0:
                tt[col] = pd.Series(np.nan, index=tt.index, dtype="float64")
    for col in ["YearMonth"]:
        if col in tt.columns:
            tt[col] = pd.to_numeric(tt[col], errors="coerce").astype("Int64")

    long_string_cols = [
        "ТТ",
        "Сеть",
        "Город",
        "Адрес",
        "Метрика KPI",
        "Регион BI",
        "Группа региона",
    ]
    long_float_cols = ["Вес KPI", "План KPI", "Факт KPI", "Выполнение KPI %"]
    for col in long_string_cols:
        if col in long.columns:
            long[col] = long[col].astype("string").fillna("")
    for col in long_float_cols:
        if col in long.columns:
            long[col] = pd.to_numeric(long[col], errors="coerce").astype("float64")
            if long[col].notna().sum() == 0:
                long[col] = pd.Series(np.nan, index=long.index, dtype="float64")
    for col in ["YearMonth"]:
        if col in long.columns:
            long[col] = pd.to_numeric(long[col], errors="coerce").astype("Int64")

    return merch, tt, long


def parse_kpi(dim: pd.DataFrame = None) -> None:
    phase_started = time.perf_counter()
    settings = load_settings()
    kpi_root = Path(settings["sources"]["kpi"]["folder"])
    output = settings["sources"]["kpi"]["output"]
    out_dir = Path(settings["paths"]["out"])

    if not kpi_root.exists():
        print("  KPI: папка не найдена")
        return

    if dim is None or dim.empty:
        dim_path = Path(settings["sources"]["users"]["output"])
        if dim_path.exists():
            dim = pd.read_parquet(dim_path)
            print(f"  KPI: загружен dim_employees ({len(dim)} записей)")
        else:
            dim = pd.DataFrame()
    dim = _directory_dimension(dim)

    okk_path = Path(settings["sources"]["okk"]["output"])
    okk = pd.read_parquet(okk_path) if okk_path.exists() else pd.DataFrame()
    teams_path = out_dir / "dim_teams.parquet"
    current_teams = pd.read_parquet(teams_path) if teams_path.exists() else pd.DataFrame()

    cache_base = Path(settings["paths"].get("cache", "data/cache"))
    tt_fact, tt_long = _build_client_tt_fact(
        kpi_root,
        dim,
        cache_root=cache_base / "client_kpi",
    )
    if tt_fact.empty:
        print("  KPI: клиентские файлы fact kpi не найдены или пустые")
        return
    phase_started = _log_phase("клиентские KPI-файлы", phase_started)

    tt_fact = _enrich_tt_regions_from_okk(tt_fact, okk)
    active_dim = dim.copy()
    if "Активен" in active_dim.columns:
        active_dim = active_dim[active_dim["Активен"].fillna(False).eq(True)].copy()
    sql_export_root = Path(
        settings["sources"]["kpi"].get(
            "sql_export_folder",
            kpi_root / "sql_exports",
        )
    )
    sql_visits, sql_agent_audit, sql_month_audit, sql_months = (
        load_client_sql_visits(sql_export_root, dim)
    )
    phase_started = _log_phase("SQL-пакеты визитов", phase_started)

    legacy_visits = pd.DataFrame()
    legacy_rtm_audit = pd.DataFrame()
    legacy_login_audit = pd.DataFrame()
    login_root = kpi_root / "Логины"
    rtm_root = kpi_root / "RTM"
    if login_root.exists() and rtm_root.exists():
        login_map, legacy_login_audit = load_login_employee_map(
            login_root,
            active_dim,
            cache_root=cache_base / "login_map",
        )
        legacy_visits, legacy_rtm_audit = load_rtm_employee_visits(
            rtm_root,
            login_map,
            cache_root=cache_base / "rtm",
        )
        if sql_months:
            legacy_visits = legacy_visits[
                ~legacy_visits["YearMonth"].isin(sql_months)
            ].copy()
            if "YearMonth" in legacy_rtm_audit.columns:
                legacy_rtm_audit = legacy_rtm_audit[
                    ~pd.to_numeric(
                        legacy_rtm_audit["YearMonth"], errors="coerce"
                    ).isin(sql_months)
                ].copy()
            if "YearMonth" in legacy_login_audit.columns:
                legacy_login_audit = legacy_login_audit[
                    ~pd.to_numeric(
                        legacy_login_audit["YearMonth"], errors="coerce"
                    ).isin(sql_months)
                ].copy()
        legacy_visits["Источник визитов"] = "Excel RTM (история)"
        legacy_rtm_audit["Источник визитов"] = "Excel RTM (история)"
        legacy_login_audit["Источник данных"] = "Excel логины (история)"

    visit_frames = [frame for frame in [legacy_visits, sql_visits] if not frame.empty]
    if not visit_frames:
        raise FileNotFoundError("Не найдены ни SQL-пакеты визитов, ни исторические RTM")
    rtm_visits = pd.concat(visit_frames, ignore_index=True, sort=False)
    rtm_visits = rtm_visits.sort_values(
        ["YearMonth", "Ключ визита RTM", "Источник визитов"],
        kind="mergesort",
    ).drop_duplicates(["YearMonth", "Ключ визита RTM"], keep="last")

    rtm_audit_frames = [
        frame for frame in [legacy_rtm_audit, sql_month_audit] if not frame.empty
    ]
    rtm_audit = (
        pd.concat(rtm_audit_frames, ignore_index=True, sort=False)
        if rtm_audit_frames
        else pd.DataFrame()
    )
    login_audit_frames = [
        frame for frame in [legacy_login_audit, sql_agent_audit] if not frame.empty
    ]
    login_audit = (
        pd.concat(login_audit_frames, ignore_index=True, sort=False)
        if login_audit_frames
        else pd.DataFrame()
    )
    phase_started = _log_phase("визиты и сотрудники", phase_started)
    current_tm_map, current_tm_audit = load_current_tm_assignments(
        Path(settings["sources"]["tm_assignments"]["folder"]),
        active_dim,
        cache_root=cache_base / "current_tm_assignments",
    )
    phase_started = _log_phase("актуальная оргструктура", phase_started)
    rtm_visits, org_audit = attach_kpi_rtm_org(
        rtm_visits, current_tm_map, current_teams
    )
    merch_fact_full = _build_merch_kpi_fact(tt_fact, rtm_visits, dim)
    visit_timeline = _build_visit_kpi_timeline(tt_fact, rtm_visits)
    phase_started = _log_phase("связка KPI / RTM / оргструктура", phase_started)
    merch_fact = merch_fact_full.copy()
    kpi_score_weight_columns = [
        "PICOS вес в KPI %",
        "OSA вес в KPI %",
        "TOP16 вес в KPI %",
    ]

    if merch_fact.empty:
        print("  KPI: не удалось собрать слой МЕ из клиентского KPI")
        return

    active_merch_ids: set[str] = set()
    if dim is not None and not dim.empty and "ID мерчендайзера" in merch_fact.columns:
        scope = get_active_users_scope(dim)
        active_merch_ids = set(scope["merch_ids"])
        before = len(merch_fact)
        merch_fact = merch_fact[merch_fact["ID мерчендайзера"].astype(str).isin(active_merch_ids)].copy()
        print(f"  KPI: фильтр по активным USERS {before} -> {len(merch_fact)} строк")

    employee_monthly_source = merch_fact_full.copy()
    if active_merch_ids:
        employee_monthly_source = employee_monthly_source[
            employee_monthly_source["ID мерчендайзера"].astype(str).isin(active_merch_ids)
        ].copy()
    merch_fact = merch_fact.drop(columns=kpi_score_weight_columns, errors="ignore")
    merch_history = merch_fact_full.drop(columns=kpi_score_weight_columns, errors="ignore")
    merch_fact, tt_fact, tt_long = _force_kpi_output_types(merch_fact, tt_fact, tt_long)
    merch_history, _, _ = _force_kpi_output_types(merch_history, pd.DataFrame(), pd.DataFrame())

    save_parquet(merch_fact, output)
    save_parquet(merch_history, str(out_dir / "kpi_employee_history_fact.parquet"))
    save_parquet(tt_fact, str(out_dir / "kpi_client_tt_fact.parquet"))
    save_parquet(tt_long, str(out_dir / "kpi_client_tt_long.parquet"))
    save_parquet(rtm_visits, str(out_dir / "rtm_employee_visits.parquet"))
    save_parquet(visit_timeline, str(out_dir / "kpi_employee_visit_timeline.parquet"))
    employee_monthly = employee_monthly_source.rename(
        columns={
            "ID мерчендайзера": "ID сотрудника",
            "Мерчендайзер": "ФИО сотрудника",
            "Визиты KPI": "Визитов",
            "ТТ KPI": "ТТ",
        }
    )[
        [
            "MonthStart",
            "YearMonth",
            "ID сотрудника",
            "ФИО сотрудника",
            "KPI проекта %",
            *KPI_VALUE_COLUMNS,
            *kpi_score_weight_columns,
            "Визитов",
            "ТТ",
        ]
    ]
    save_parquet(employee_monthly, str(out_dir / "kpi_employee_monthly_metrics.parquet"))
    save_parquet(login_audit, str(out_dir / "kpi_login_mapping_audit.parquet"))
    save_parquet(rtm_audit, str(out_dir / "kpi_rtm_mapping_audit.parquet"))
    save_parquet(org_audit, str(out_dir / "kpi_rtm_tm_mapping_audit.parquet"))
    save_parquet(current_tm_audit, str(out_dir / "current_tm_assignment_audit.parquet"))
    _log_phase("сохранение KPI-витрин", phase_started)

    print(f"\n  KPI client TT: {len(tt_fact)} строк")
    print(f"  KPI client TT long: {len(tt_long)} строк")
    print(f"  KPI fact (МЕ-слой): {len(merch_fact)} строк")
    print(f"  KPI history (визиты): {len(merch_fact_full)} строк")
    print(f"  KPI visit timeline: {len(visit_timeline)} строк")
    mapped_visits = int(rtm_visits["ID сотрудника"].notna().sum())
    print(f"  Визиты -> сотрудник: {mapped_visits}/{len(rtm_visits)} подтверждённых визитов")
    mapped_tm = int(rtm_visits["Территориальный менеджер"].notna().sum())
    print(
        f"  Визиты -> USERS -> текущая привязка ТМ: "
        f"{mapped_tm}/{len(rtm_visits)} подтверждённых визитов"
    )
    print(f"  Колонок kpi_fact: {len(merch_fact.columns)}")
