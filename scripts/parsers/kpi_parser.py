import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

if __package__ is None or __package__ == "":
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from scripts.utils import load_settings, save_parquet, normalize_dim, get_active_users_scope
from scripts.staffing_utils import _mode_or_first


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


def _variant_index(column_name: str, base_label: str) -> int:
    normalized = _normalize_label(column_name)
    normalized_base = _normalize_label(base_label)
    if normalized == normalized_base:
        return 1
    suffix = normalized.replace(normalized_base, "", 1).strip()
    match = re.search(r"\.(\d+)$", suffix)
    if match:
        return int(match.group(1)) + 1
    return 99


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
        .agg(_mode_or_first)
        .to_dict()
    )
    return grouped


def _build_team_lookup(teams: pd.DataFrame) -> pd.DataFrame:
    if teams is None or teams.empty:
        return pd.DataFrame(
            columns=[
                "ID мерчендайзера",
                "ID супервайзера",
                "Супервайзер",
                "ID территориального менеджера",
                "Территориальный менеджер",
                "Регион BI",
                "Группа региона",
            ]
        )
    keep = [
        c
        for c in [
            "ID мерчендайзера",
            "ID супервайзера",
            "Супервайзер",
            "ID территориального менеджера",
            "Территориальный менеджер",
            "Регион BI",
            "Группа региона",
        ]
        if c in teams.columns
    ]
    if not keep:
        return pd.DataFrame()
    return teams[keep].dropna(subset=["ID мерчендайзера"]).drop_duplicates("ID мерчендайзера")


def _load_client_kpi_file(path: Path, city_region_lookup: dict[str, str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    period, year, month = _detect_period(path.name)
    xl = pd.ExcelFile(path)
    sheet = next((name for name in xl.sheet_names if "адрес" in _normalize_label(name)), xl.sheet_names[0])
    raw = pd.read_excel(path, sheet_name=sheet, header=[1, 2])
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
    weight_cols = sorted(
        [c for c in cols if "вес по kpi №" in _normalize_label(c)],
        key=_kpi_number_index,
    )
    name_cols = sorted(
        [c for c in cols if "наименование по kpi" in _normalize_label(c)],
        key=_kpi_number_index,
    )
    fact_cols = sorted(
        [c for c in cols if "факт" in _normalize_label(c) and "kpi" in _normalize_label(c) and "all" in _normalize_label(c)],
        key=_kpi_number_index,
    )
    completion_cols = sorted(
        [c for c in cols if "% выполнения kpi" in _normalize_label(c)],
        key=_kpi_number_index,
    )

    long_rows: list[pd.DataFrame] = []
    pct_matrix = []
    weight_matrix = []
    block_name_matrix = []

    for idx in range(3):
        name_col = name_cols[idx] if idx < len(name_cols) else None
        weight_col = weight_cols[idx] if idx < len(weight_cols) else None
        goal_col = goal_cols[idx] if idx < len(goal_cols) else None
        fact_col = fact_cols[idx] if idx < len(fact_cols) else None
        completion_col = completion_cols[idx] if idx < len(completion_cols) else None

        block_name = work[name_col].astype("string").str.strip() if name_col else pd.Series(pd.NA, index=work.index, dtype="string")
        block_weight = _normalize_pct(work[weight_col]) if weight_col else pd.Series(float("nan"), index=work.index, dtype="float64")
        block_goal = _normalize_pct(work[goal_col]) if goal_col else pd.Series(float("nan"), index=work.index, dtype="float64")
        block_fact = _normalize_pct(work[fact_col]) if fact_col else pd.Series(float("nan"), index=work.index, dtype="float64")
        block_completion = _normalize_pct(work[completion_col]) if completion_col else pd.Series(float("nan"), index=work.index, dtype="float64")

        work[f"KPI блок {idx + 1}"] = block_name
        work[f"KPI вес {idx + 1}"] = block_weight
        work[f"KPI цель {idx + 1}"] = block_goal
        work[f"KPI факт {idx + 1}"] = block_fact
        work[f"KPI выполнение {idx + 1}"] = block_completion

        pct_matrix.append(block_completion)
        weight_matrix.append(block_weight)
        block_name_matrix.append(block_name)

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
                "Блок KPI": block_name,
                "Вес KPI": block_weight,
                "Цель KPI": block_goal,
                "Факт KPI": block_fact,
                "Выполнение KPI %": block_completion,
            }
        )
        long_rows.append(part)

    pct_df = pd.concat(pct_matrix, axis=1)
    weight_df = pd.concat(weight_matrix, axis=1)
    pct_df.columns = list(range(pct_df.shape[1]))
    weight_df.columns = list(range(weight_df.shape[1]))
    available_weight = weight_df.where(pct_df.notna()).sum(axis=1, min_count=1)
    weighted_sum = (pct_df * weight_df).sum(axis=1, min_count=1)
    work["KPI 1"] = weighted_sum / available_weight
    work.loc[available_weight.isna() | (available_weight == 0), "KPI 1"] = pd.NA
    work["KPI 2"] = pd.NA

    def _first_matching_pct(keywords: tuple[str, ...]) -> pd.Series:
        result = pd.Series(float("nan"), index=work.index, dtype="float64")
        for idx, names in enumerate(block_name_matrix, start=1):
            mask = names.fillna("").astype(str).str.lower().map(lambda x: any(word in x for word in keywords))
            result = result.where(~mask, work[f"KPI выполнение {idx}"])
        return result

    work["ОСА (факт)"] = _first_matching_pct(("osa", "налич"))
    work["PICoS (факт)"] = _first_matching_pct(("picos", "picos_", "пикос", "picоs"))
    work["Сервис (факт)"] = _first_matching_pct(("service", "сервис"))
    work["Покрытие (факт)"] = pd.NA
    work["Покрытие (план)"] = pd.NA
    work["Покрытие (итог)"] = pd.NA
    work["PICoS СВ (факт)"] = pd.NA
    work["PICoS СВ (план)"] = pd.NA
    work["PICoS СВ (итог)"] = pd.NA
    work["Вакансия"] = False
    work["Тип маршрута"] = pd.NA
    work["Код маршрута"] = pd.NA
    work["Регион BI"] = work["Город"].astype(str).str.strip().str.upper().map(city_region_lookup)

    tt_fact = work[
        [
            "Период",
            "Год",
            "Месяц",
            "MonthStart",
            "YearMonth",
            "ТТ",
            "Сеть",
            "Город",
            "Адрес",
            "Сценарий",
            "Код маршрута СВ",
            "Регион",
            "Группа продаж",
            "Регион BI",
            "Сервис (факт)",
            "ОСА (факт)",
            "PICoS (факт)",
            "Покрытие (факт)",
            "Покрытие (план)",
            "Покрытие (итог)",
            "PICoS СВ (факт)",
            "PICoS СВ (план)",
            "PICoS СВ (итог)",
            "KPI 1",
            "KPI 2",
            "Вакансия",
            "Тип маршрута",
            "Код маршрута",
        ]
    ].drop_duplicates(["Период", "ТТ"])

    long_fact = pd.concat(long_rows, ignore_index=True)
    long_fact = long_fact[long_fact["Блок KPI"].notna() | long_fact["Выполнение KPI %"].notna()].copy()
    return tt_fact.reset_index(drop=True), long_fact.reset_index(drop=True)


def _build_client_tt_fact(kpi_root: Path, dim: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
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

    tt_fact = pd.concat(tt_frames, ignore_index=True) if tt_frames else pd.DataFrame()
    long_fact = pd.concat(long_frames, ignore_index=True) if long_frames else pd.DataFrame()
    return tt_fact, long_fact


def _enrich_tt_regions_from_okk(tt_fact: pd.DataFrame, okk: pd.DataFrame) -> pd.DataFrame:
    if tt_fact.empty or okk.empty:
        return tt_fact
    tt_region = (
        okk.dropna(subset=["MonthStart", "Код ТТ"])
        .groupby(["MonthStart", "YearMonth", "Код ТТ"], dropna=False)
        .agg(
            **{
                "Регион BI okk": ("Регион BI", _mode_or_first),
                "Город okk": ("Город", _mode_or_first) if "Город" in okk.columns else ("Адрес", lambda s: pd.NA),
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


def _build_merch_kpi_fact(tt_fact: pd.DataFrame, okk: pd.DataFrame, teams: pd.DataFrame, dim: pd.DataFrame) -> pd.DataFrame:
    if tt_fact.empty or okk.empty:
        return pd.DataFrame()

    visits = (
        okk.dropna(subset=["MonthStart", "Код ТТ", "ID мерчендайзера"])
        .groupby(["MonthStart", "YearMonth", "Код ТТ", "ID мерчендайзера"], dropna=False)
        .agg(
            **{
                "Визиты KPI": ("Дата визита", "count"),
                "Мерчендайзер": ("Мерчендайзер", _mode_or_first),
                "ID супервайзера": ("ID супервайзера", _mode_or_first),
                "Супервайзер": ("Супервайзер", _mode_or_first),
                "Регион BI": ("Регион BI", _mode_or_first),
                "Город визита": ("Город", _mode_or_first) if "Город" in okk.columns else ("Адрес", lambda s: pd.NA),
            }
        )
        .reset_index()
        .rename(columns={"Код ТТ": "ТТ"})
    )

    visits_all_tt = (
        okk.dropna(subset=["MonthStart", "ID мерчендайзера"])
        .groupby(["MonthStart", "YearMonth", "ID мерчендайзера"], dropna=False)["Код ТТ"]
        .nunique()
        .reset_index(name="ТТ всего у МЕ")
    )

    merged = visits.merge(
        tt_fact[
            [
                "MonthStart",
                "YearMonth",
                "ТТ",
                "Сеть",
                "Город",
                "Адрес",
                "Код маршрута СВ",
                "Группа продаж",
                "KPI 1",
                "KPI 2",
                "Сервис (факт)",
                "ОСА (факт)",
                "PICoS (факт)",
                "Регион BI",
            ]
        ],
        on=["MonthStart", "YearMonth", "ТТ"],
        how="left",
        suffixes=("_visit", ""),
    )
    merged = merged[merged["KPI 1"].notna()].copy()
    if merged.empty:
        return pd.DataFrame()

    team_lookup = _build_team_lookup(teams)
    merged = merged.merge(team_lookup, on="ID мерчендайзера", how="left", suffixes=("", "_team"))

    merged["Супервайзер"] = merged["Супервайзер"].combine_first(merged.get("Супервайзер_team"))
    merged["ID супервайзера"] = merged["ID супервайзера"].combine_first(merged.get("ID супервайзера_team"))
    merged["Регион BI"] = merged["Регион BI"].combine_first(merged.get("Регион BI_visit")).combine_first(merged.get("Регион BI_team"))
    merged["Город"] = merged["Город"].combine_first(merged.get("Город визита"))

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

    rows: list[dict] = []
    for keys, part in merged.groupby(["MonthStart", "YearMonth", "ID мерчендайзера"], dropna=False):
        month_start, year_month, merch_id = keys
        total_visits = pd.to_numeric(part["Визиты KPI"], errors="coerce").fillna(0)
        matched_tt = part["ТТ"].dropna().nunique()
        row = {
            "MonthStart": month_start,
            "YearMonth": year_month,
            "Период": f"{int(month_start.year)}_{int(month_start.month):02d}" if pd.notna(month_start) else "Unknown",
            "Год": int(month_start.year) if pd.notna(month_start) else pd.NA,
            "Месяц": int(month_start.month) if pd.notna(month_start) else pd.NA,
            "ID супервайзера": _mode_or_first(part["ID супервайзера"]),
            "ID мерчендайзера": merch_id,
            "Супервайзер": _mode_or_first(part["Супервайзер"]),
            "Мерчендайзер": _mode_or_first(part["Мерчендайзер"]),
            "Территориальный менеджер": _mode_or_first(part["Территориальный менеджер"]),
            "Регион": _mode_or_first(part["Регион BI"]),
            "Город": _mode_or_first(part["Город"]),
            "Группа продаж": _mode_or_first(part["Группа продаж"]),
            "Код маршрута СВ": _mode_or_first(part["Код маршрута СВ"]),
            "Код маршрута": pd.NA,
            "Тип маршрута": pd.NA,
            "Вакансия": False,
            "Сервис (факт)": _weighted_mean(part["Сервис (факт)"], total_visits),
            "ОСА (факт)": _weighted_mean(part["ОСА (факт)"], total_visits),
            "PICoS (факт)": _weighted_mean(part["PICoS (факт)"], total_visits),
            "Покрытие (факт)": pd.NA,
            "Покрытие (план)": pd.NA,
            "Покрытие (итог)": pd.NA,
            "PICoS СВ (факт)": pd.NA,
            "PICoS СВ (план)": pd.NA,
            "PICoS СВ (итог)": pd.NA,
            "KPI 1": _weighted_mean(part["KPI 1"], total_visits),
            "KPI 2": pd.NA,
        }
        rows.append(row)

    fact = pd.DataFrame(rows)
    fact = fact.merge(visits_all_tt, on=["MonthStart", "YearMonth", "ID мерчендайзера"], how="left")
    if "ТТ всего у МЕ" in fact.columns:
        fact["Покрытие (факт)"] = 1.0
        fact["Покрытие (план)"] = 1.0
        fact["Покрытие (итог)"] = 1.0
        fact = fact.drop(columns=["ТТ всего у МЕ"], errors="ignore")
    return fact


def _force_kpi_output_types(
    merch_fact: pd.DataFrame,
    tt_fact: pd.DataFrame,
    tt_long: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    merch = merch_fact.copy()
    tt = tt_fact.copy()
    long = tt_long.copy()

    string_cols_common = [
        "Период",
        "ID супервайзера",
        "ID мерчендайзера",
        "Супервайзер",
        "Мерчендайзер",
        "Территориальный менеджер",
        "Регион",
        "Город",
        "Группа продаж",
        "Код маршрута СВ",
        "Код маршрута",
        "Тип маршрута",
        "Регион BI",
        "Группа региона",
    ]
    float_cols_common = [
        "Сервис (факт)",
        "ОСА (факт)",
        "PICoS (факт)",
        "Покрытие (факт)",
        "Покрытие (план)",
        "Покрытие (итог)",
        "PICoS СВ (факт)",
        "PICoS СВ (план)",
        "PICoS СВ (итог)",
        "KPI 1",
        "KPI 2",
    ]

    for col in string_cols_common:
        if col in merch.columns:
            merch[col] = merch[col].astype("string").fillna("")
    for col in float_cols_common:
        if col in merch.columns:
            merch[col] = pd.to_numeric(merch[col], errors="coerce").astype("float64")
            if merch[col].notna().sum() == 0:
                merch[col] = pd.Series(np.nan, index=merch.index, dtype="float64")
    if "Вакансия" in merch.columns:
        merch["Вакансия"] = merch["Вакансия"].astype("boolean")
    for col in ["Год", "Месяц", "YearMonth"]:
        if col in merch.columns:
            merch[col] = pd.to_numeric(merch[col], errors="coerce").astype("Int64")

    tt_string_cols = [
        "Период",
        "ТТ",
        "Сеть",
        "Город",
        "Адрес",
        "Сценарий",
        "Код маршрута СВ",
        "Регион",
        "Группа продаж",
        "Регион BI",
        "Группа региона",
        "Тип маршрута",
        "Код маршрута",
    ]
    tt_float_cols = [
        "Сервис (факт)",
        "ОСА (факт)",
        "PICoS (факт)",
        "Покрытие (факт)",
        "Покрытие (план)",
        "Покрытие (итог)",
        "PICoS СВ (факт)",
        "PICoS СВ (план)",
        "PICoS СВ (итог)",
        "KPI 1",
        "KPI 2",
    ]
    for col in tt_string_cols:
        if col in tt.columns:
            tt[col] = tt[col].astype("string").fillna("")
    for col in tt_float_cols:
        if col in tt.columns:
            tt[col] = pd.to_numeric(tt[col], errors="coerce").astype("float64")
            if tt[col].notna().sum() == 0:
                tt[col] = pd.Series(np.nan, index=tt.index, dtype="float64")
    if "Вакансия" in tt.columns:
        tt["Вакансия"] = tt["Вакансия"].astype("boolean")
    for col in ["Год", "Месяц", "YearMonth"]:
        if col in tt.columns:
            tt[col] = pd.to_numeric(tt[col], errors="coerce").astype("Int64")

    long_string_cols = [
        "Период",
        "ТТ",
        "Сеть",
        "Город",
        "Адрес",
        "Сценарий",
        "Код маршрута СВ",
        "Регион",
        "Группа продаж",
        "Блок KPI",
        "Регион BI",
        "Группа региона",
    ]
    long_float_cols = ["Вес KPI", "Цель KPI", "Факт KPI", "Выполнение KPI %"]
    for col in long_string_cols:
        if col in long.columns:
            long[col] = long[col].astype("string").fillna("")
    for col in long_float_cols:
        if col in long.columns:
            long[col] = pd.to_numeric(long[col], errors="coerce").astype("float64")
            if long[col].notna().sum() == 0:
                long[col] = pd.Series(np.nan, index=long.index, dtype="float64")
    for col in ["Год", "Месяц", "YearMonth"]:
        if col in long.columns:
            long[col] = pd.to_numeric(long[col], errors="coerce").astype("Int64")

    return merch, tt, long


def parse_kpi(dim: pd.DataFrame = None) -> None:
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

    okk_path = Path(settings["sources"]["okk"]["output"])
    teams_path = Path(settings["sources"]["teams"]["output"])
    okk = pd.read_parquet(okk_path) if okk_path.exists() else pd.DataFrame()
    teams = pd.read_parquet(teams_path) if teams_path.exists() else pd.DataFrame()

    tt_fact, tt_long = _build_client_tt_fact(kpi_root, dim)
    if tt_fact.empty:
        print("  KPI: клиентские файлы fact kpi не найдены или пустые")
        return

    tt_fact = _enrich_tt_regions_from_okk(tt_fact, okk)
    merch_fact = _build_merch_kpi_fact(tt_fact, okk, teams, dim)

    if merch_fact.empty:
        print("  KPI: не удалось собрать слой МЕ из клиентского KPI")
        return

    if dim is not None and not dim.empty and "ID мерчендайзера" in merch_fact.columns:
        scope = get_active_users_scope(dim)
        before = len(merch_fact)
        merch_fact = merch_fact[merch_fact["ID мерчендайзера"].astype(str).isin(scope["merch_ids"])].copy()
        print(f"  KPI: фильтр по активным USERS {before} -> {len(merch_fact)} строк")

    merch_fact, tt_fact, tt_long = _force_kpi_output_types(merch_fact, tt_fact, tt_long)

    save_parquet(merch_fact, output)
    save_parquet(tt_fact, str(out_dir / "kpi_client_tt_fact.parquet"))
    save_parquet(tt_long, str(out_dir / "kpi_client_tt_long.parquet"))

    print(f"\n  KPI client TT: {len(tt_fact)} строк")
    print(f"  KPI client TT long: {len(tt_long)} строк")
    print(f"  KPI fact (МЕ-слой): {len(merch_fact)} строк")
    print(f"  Колонок kpi_fact: {len(merch_fact.columns)}")
