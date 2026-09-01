import os
import re
import uuid
from datetime import date
from functools import lru_cache
import yaml
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from pathlib import Path
REGION_MAP_PATH = "config/region_map.csv"
REGION_SORT_ORDER = {
    "Москва": 1,
    "Юг": 2,
    "Сибирь": 3,
    "Волга": 4,
    "Северо-Запад": 5,
    "Урал": 6,
    "Центр": 7,
    "Дальний Восток": 8,
    "Не определён": 99,
}


def load_settings(path: str = "config/settings.yml") -> dict:
    with open(path, encoding="utf-8") as f:
        settings = yaml.safe_load(f)

    override_out = os.environ.get("HN_OUT_DIR", "").strip()
    if not override_out:
        return settings

    configured_out = Path(settings["paths"]["out"])
    override_root = Path(override_out)
    settings["paths"]["out"] = str(override_root)
    for source_config in settings.get("sources", {}).values():
        if not isinstance(source_config, dict) or "output" not in source_config:
            continue
        source_output = Path(source_config["output"])
        try:
            relative_output = source_output.relative_to(configured_out)
        except ValueError:
            continue
        source_config["output"] = str(override_root / relative_output)
    return settings


def get_as_of_date() -> pd.Timestamp:
    """Единая дата расчёта, фиксируемая для воспроизводимого запуска ETL."""
    raw_value = os.environ.get("HN_AS_OF_DATE", "").strip()
    if raw_value:
        parsed = pd.to_datetime(raw_value, errors="raise")
        return pd.Timestamp(parsed).normalize()
    return pd.Timestamp(date.today()).normalize()


@lru_cache(maxsize=8)
def _load_region_map_cached(path: str, modified_ns: int) -> pd.DataFrame:
    region_map = pd.read_csv(path, dtype=str).fillna("")
    for col in ["source_region", "canonical_region", "region_group", "comment"]:
        if col not in region_map.columns:
            region_map[col] = ""
    region_map["source_region"] = region_map["source_region"].astype(str).str.strip()
    region_map["canonical_region"] = region_map["canonical_region"].astype(str).str.strip()
    region_map["region_group"] = region_map["region_group"].astype(str).str.strip()
    return region_map


def load_region_map(path: str = REGION_MAP_PATH) -> pd.DataFrame:
    source = Path(path)
    modified_ns = source.stat().st_mtime_ns
    return _load_region_map_cached(str(source), modified_ns).copy()


@lru_cache(maxsize=8)
def _region_patterns(path: str, modified_ns: int) -> tuple[tuple[str, str], ...]:
    region_map = _load_region_map_cached(path, modified_ns)
    candidates = []
    for source, canonical in zip(
        region_map["source_region"],
        region_map["canonical_region"],
    ):
        source_normalized = normalize_text_value(source, upper=True)
        canonical_normalized = normalize_text_value(canonical)
        if source_normalized and canonical_normalized:
            candidates.append((source_normalized, canonical_normalized))
    return tuple(sorted(candidates, key=lambda item: len(item[0]), reverse=True))


def _normalize_region_value(value) -> str | None:
    if pd.isna(value):
        return None
    value = str(value).strip()
    return value or None


def normalize_text_value(value, *, upper: bool = False) -> str | None:
    if pd.isna(value) or value is None:
        return None
    text = str(value).replace("\xa0", " ").strip()
    text = re.sub(r"\s+", " ", text)
    if not text:
        return None
    text = text.replace("Ё", "Е").replace("ё", "е")
    return text.upper() if upper else text


def normalize_employee_id(value, *, missing: str | None = "") -> str | None:
    """Нормализует идентификатор сотрудника без изменения бизнес-значения."""
    if value is None or pd.isna(value):
        return missing
    text = str(value).strip().upper()
    return text or missing


def coerce_bool_like(series: pd.Series) -> pd.Series:
    if pd.api.types.is_bool_dtype(series):
        return series.astype("boolean")

    non_null = series.dropna()
    if non_null.empty:
        return series.astype("boolean")

    if non_null.map(lambda x: isinstance(x, bool)).all():
        return series.astype("boolean")

    normalized = series.astype("string").str.strip().str.lower()
    mapping = {
        "true": True,
        "false": False,
        "1": True,
        "0": False,
        "да": True,
        "нет": False,
    }
    unique_values = set(v for v in normalized.dropna().unique().tolist() if v != "")
    if unique_values and unique_values.issubset(set(mapping.keys())):
        return normalized.map(mapping).astype("boolean")

    return series


def _canonical_region_group_lookup(region_map: pd.DataFrame | None = None) -> dict:
    region_map = load_region_map() if region_map is None else region_map.copy()
    priority = {"core": 1, "extended": 2, "outlier": 3, "": 9}
    ranked = region_map.copy()
    ranked["group_rank"] = ranked["region_group"].map(lambda x: priority.get(x, 9))
    ranked = ranked.sort_values(["canonical_region", "group_rank", "source_region"])
    return ranked.drop_duplicates("canonical_region").set_index("canonical_region")["region_group"].to_dict()


def _get_region_lookups() -> tuple[dict, dict]:
    region_map = load_region_map()
    canonical_lookup = dict(zip(region_map["source_region"], region_map["canonical_region"]))
    group_lookup = _canonical_region_group_lookup(region_map)
    return canonical_lookup, group_lookup


def map_region_series(series: pd.Series) -> pd.Series:
    canonical_lookup, _ = _get_region_lookups()
    normalized = series.apply(_normalize_region_value)
    return normalized.map(canonical_lookup)


def canonical_region_from_text(value) -> str | None:
    text_norm = normalize_text_value(value, upper=True)
    if not text_norm:
        return None

    special_patterns = {
        "REGION_MOSCOW_AREA": "Москва",
        "REGION_MOSCOW": "Москва",
        "REGION_SOUTH": "Юг",
        "REGION_WEST": "Северо-Запад",
        "REGION_SIBERIA": "Сибирь",
        "REGION_VOLGA": "Волга",
        "REGION_URAL": "Урал",
        "REGION_CENTER": "Центр",
        "REGION_EAST": "Дальний Восток",
        "МОСКВА": "Москва",
        "SOUTH": "Юг",
        "WEST": "Северо-Запад",
        "SIBERIA": "Сибирь",
        "VOLGA": "Волга",
        "URAL": "Урал",
        "CENTER": "Центр",
        "EAST": "Дальний Восток",
    }
    for pattern, canonical in special_patterns.items():
        if pattern in text_norm:
            return canonical

    source = Path(REGION_MAP_PATH)
    for pattern, canonical in _region_patterns(str(source), source.stat().st_mtime_ns):
        if pattern in text_norm:
            return canonical
    return None


def _load_dim_region_map() -> pd.DataFrame:
    settings = load_settings()
    dim_path = Path(settings["sources"]["users"]["output"])
    if not dim_path.exists():
        return pd.DataFrame(columns=["ID сотрудника", "Регион", "Регион BI"])

    dim = pd.read_parquet(dim_path)
    if "Регион BI" not in dim.columns and "Регион" in dim.columns:
        dim["Регион BI"] = map_region_series(dim["Регион"])

    keep = [c for c in ["ID сотрудника", "Регион", "Регион BI"] if c in dim.columns]
    if not keep:
        return pd.DataFrame(columns=["ID сотрудника", "Регион", "Регион BI"])
    return dim[keep].drop_duplicates("ID сотрудника")


def enrich_region_columns(df: pd.DataFrame, output_path: str | None = None) -> pd.DataFrame:
    enriched = df.copy()
    _, group_lookup = _get_region_lookups()
    output_name = Path(output_path).name if output_path else ""

    if "Регион BI" not in enriched.columns:
        if "Регион" in enriched.columns:
            enriched["Регион BI"] = map_region_series(enriched["Регион"])
        elif "ID сотрудника" in enriched.columns and (not output_path or Path(output_path).name != "dim_employees.parquet"):
            dim_region = _load_dim_region_map()
            if not dim_region.empty:
                enriched = enriched.merge(dim_region, on="ID сотрудника", how="left")

    if "Регион BI" in enriched.columns:
        enriched["Регион BI"] = enriched["Регион BI"].where(enriched["Регион BI"].notna(), None)
        if output_name != "page3_merch_monthly_snapshot.parquet":
            enriched["Группа региона"] = enriched["Регион BI"].map(group_lookup)

    return enriched


def _make_month_start(year_series: pd.Series, month_series: pd.Series) -> pd.Series:
    year_num = pd.to_numeric(year_series, errors="coerce")
    month_num = pd.to_numeric(month_series, errors="coerce")
    return pd.to_datetime(
        {"year": year_num, "month": month_num, "day": 1},
        errors="coerce"
    )


def _parse_quarter_num(series: pd.Series) -> pd.Series:
    if pd.api.types.is_numeric_dtype(series):
        return pd.to_numeric(series, errors="coerce")
    extracted = series.astype(str).str.extract(r"([1-4])", expand=False)
    return pd.to_numeric(extracted, errors="coerce")


def _make_quarter_start(year_series: pd.Series, quarter_series: pd.Series) -> pd.Series:
    year_num = pd.to_numeric(year_series, errors="coerce")
    quarter_num = _parse_quarter_num(quarter_series)
    month_num = ((quarter_num - 1) * 3) + 1
    return pd.to_datetime(
        {"year": year_num, "month": month_num, "day": 1},
        errors="coerce"
    )


def _parse_period_month(series: pd.Series) -> pd.Series:
    extracted = series.astype(str).str.extract(r"^(?P<year>\d{4})[_-](?P<month>\d{1,2})$", expand=True)
    if extracted.empty:
        return pd.Series(pd.NaT, index=series.index)
    return _make_month_start(extracted["year"], extracted["month"])


def _parse_period_quarter(series: pd.Series) -> pd.Series:
    raw = series.astype(str).str.strip()
    q_first = raw.str.extract(r"^Q(?P<quarter>[1-4])[_-](?P<year>\d{4})$", expand=True)
    y_first = raw.str.extract(r"^(?P<year>\d{4})[_-]Q(?P<quarter>[1-4])$", expand=True)
    extracted = q_first.where(q_first.notna(), y_first)
    return _make_quarter_start(extracted["year"], extracted["quarter"])


def enrich_period_columns(df: pd.DataFrame) -> pd.DataFrame:
    enriched = df.copy()

    if "Дата начала" in enriched.columns and "StartMonth" not in enriched.columns:
        start_dates = pd.to_datetime(enriched["Дата начала"], errors="coerce")
        enriched["StartMonth"] = start_dates.dt.to_period("M").dt.to_timestamp()

    if "StartMonth" in enriched.columns:
        start_month = pd.to_datetime(enriched["StartMonth"], errors="coerce")
        enriched["StartMonth"] = start_month
        enriched["StartYearMonth"] = (start_month.dt.year * 100 + start_month.dt.month).astype("Int64")

    if "MonthStart" not in enriched.columns:
        if {"Год", "Месяц"}.issubset(enriched.columns):
            enriched["MonthStart"] = _make_month_start(enriched["Год"], enriched["Месяц"])
        elif "Период" in enriched.columns:
            month_start = _parse_period_month(enriched["Период"])
            if month_start.notna().any():
                enriched["MonthStart"] = month_start
        elif "Дата завершения" in enriched.columns:
            dates = pd.to_datetime(enriched["Дата завершения"], errors="coerce")
            enriched["MonthStart"] = dates.dt.to_period("M").dt.to_timestamp()
        elif "Дата визита" in enriched.columns:
            dates = pd.to_datetime(enriched["Дата визита"], errors="coerce")
            enriched["MonthStart"] = dates.dt.to_period("M").dt.to_timestamp()

    if "MonthStart" in enriched.columns:
        month_start = pd.to_datetime(enriched["MonthStart"], errors="coerce")
        enriched["MonthStart"] = month_start
        enriched["YearMonth"] = (month_start.dt.year * 100 + month_start.dt.month).astype("Int64")
    elif "YearMonth" in enriched.columns:
        enriched["YearMonth"] = pd.to_numeric(enriched["YearMonth"], errors="coerce").astype("Int64")

    if "QuarterStart" not in enriched.columns:
        if {"Год", "Квартал"}.issubset(enriched.columns):
            enriched["QuarterStart"] = _make_quarter_start(enriched["Год"], enriched["Квартал"])
        elif "Период" in enriched.columns:
            quarter_start = _parse_period_quarter(enriched["Период"])
            if quarter_start.notna().any():
                enriched["QuarterStart"] = quarter_start

    if "QuarterStart" in enriched.columns:
        quarter_start = pd.to_datetime(enriched["QuarterStart"], errors="coerce")
        enriched["QuarterStart"] = quarter_start
        quarter_num = quarter_start.dt.quarter
        enriched["YearQuarter"] = (quarter_start.dt.year * 10 + quarter_num).astype("Int64")
        enriched["QuarterLabel"] = (
            "Q" + quarter_num.astype("Int64").astype(str) + " " + quarter_start.dt.year.astype("Int64").astype(str)
        ).where(quarter_start.notna())
    elif "YearQuarter" in enriched.columns:
        enriched["YearQuarter"] = pd.to_numeric(enriched["YearQuarter"], errors="coerce").astype("Int64")

    if "QuarterStart ОЭД" in enriched.columns:
        quarter_start_oed = pd.to_datetime(enriched["QuarterStart ОЭД"], errors="coerce")
        enriched["QuarterStart ОЭД"] = quarter_start_oed
        quarter_num_oed = quarter_start_oed.dt.quarter
        enriched["YearQuarter ОЭД"] = (quarter_start_oed.dt.year * 10 + quarter_num_oed).astype("Int64")
    elif "Период ОЭД" in enriched.columns:
        quarter_start_oed = _parse_period_quarter(enriched["Период ОЭД"])
        if quarter_start_oed.notna().any():
            quarter_num_oed = quarter_start_oed.dt.quarter
            enriched["QuarterStart ОЭД"] = quarter_start_oed
            enriched["YearQuarter ОЭД"] = (quarter_start_oed.dt.year * 10 + quarter_num_oed).astype("Int64")
    elif "YearQuarter ОЭД" in enriched.columns:
        enriched["YearQuarter ОЭД"] = pd.to_numeric(enriched["YearQuarter ОЭД"], errors="coerce").astype("Int64")

    return enriched


def enrich_for_output(df: pd.DataFrame, output_path: str | None = None) -> pd.DataFrame:
    enriched = enrich_region_columns(df, output_path=output_path)
    enriched = enrich_period_columns(enriched)
    return enriched


def save_parquet(
    df: pd.DataFrame,
    output_path: str,
    exclude_columns: set[str] | None = None,
) -> None:
    df = enrich_for_output(df, output_path=output_path)
    if exclude_columns:
        df = df.drop(columns=list(exclude_columns), errors="ignore")
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)

    arrays = []
    names = []
    for col in df.columns:
        series = df[col]
        names.append(col)

        if pd.api.types.is_datetime64_any_dtype(series):
            arrays.append(pa.array(pd.to_datetime(series, errors="coerce"), type=pa.timestamp("us"), from_pandas=True))
        elif pd.api.types.is_bool_dtype(series):
            arrays.append(pa.array(series.astype("boolean"), type=pa.bool_(), from_pandas=True))
        elif pd.api.types.is_integer_dtype(series):
            arrays.append(pa.array(pd.to_numeric(series, errors="coerce"), type=pa.int64(), from_pandas=True))
        elif pd.api.types.is_float_dtype(series):
            arrays.append(pa.array(pd.to_numeric(series, errors="coerce"), type=pa.float64(), from_pandas=True))
        else:
            coerced_bool = coerce_bool_like(series)
            if pd.api.types.is_bool_dtype(coerced_bool):
                arrays.append(pa.array(coerced_bool.astype("boolean"), type=pa.bool_(), from_pandas=True))
            else:
                text = series.astype("string")
                arrays.append(pa.array(text, type=pa.string(), from_pandas=True))

    table = pa.Table.from_arrays(arrays, names=names)
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    try:
        pq.write_table(table, temporary, compression="snappy")
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    print(f"  Сохранено: {output_path} ({len(df)} строк)")


# Обратный маппинг: русские названия → внутренние английские (для парсеров)
_RU_TO_EN = {
    "ID сотрудника":  "employee_id",
    "ФИО":            "full_name",
    "Фамилия":        "last_name",
    "Имя":            "first_name",
    "Отчество":       "middle_name",
    "Должность":      "position",
    "Подразделение":  "org_unit",
    "Город":          "city",
    "Регион":         "region",
    "Проект":         "project",
    "Электронная почта":"email",
    "Группы":         "groups",
    "ID руководителя":"manager_id",
    "Дата приёма":    "hire_date",
    "Стаж (дней)":    "tenure_days",
    "Стаж (месяцев)": "tenure_months",
    "Активен":        "is_active",
    "ФИО руководителя":"manager_full_name",
}


def normalize_dim(dim: pd.DataFrame) -> pd.DataFrame:
    """Приводит dim_employees к внутренним именам колонок (если загружен из parquet)."""
    return dim.rename(columns={k: v for k, v in _RU_TO_EN.items() if k in dim.columns})


def get_active_users_scope(dim: pd.DataFrame, project: str | None = None) -> dict[str, set[str] | pd.DataFrame]:
    """Возвращает активный периметр USERS и наборы ID по ролям."""
    if project is None:
        project = load_settings().get("project", "H&N")
    if dim is None or dim.empty:
        empty = pd.DataFrame()
        return {
            "frame": empty,
            "all_ids": set(),
            "merch_ids": set(),
            "sv_ids": set(),
            "tm_ids": set(),
        }

    work = normalize_dim(dim.copy()) if "employee_id" not in dim.columns else dim.copy()

    if "employee_id" not in work.columns:
        empty = pd.DataFrame()
        return {
            "frame": empty,
            "all_ids": set(),
            "merch_ids": set(),
            "sv_ids": set(),
            "tm_ids": set(),
        }

    if "is_active" in work.columns:
        work["is_active"] = coerce_bool_like(work["is_active"])
        work = work[work["is_active"].fillna(False).eq(True)].copy()
    if "project" in work.columns or "groups" in work.columns:
        project_norm = str(project).strip().lower()
        project_mask = pd.Series(False, index=work.index)
        if "project" in work.columns:
            project_mask = project_mask | work["project"].astype(str).str.strip().str.lower().eq(project_norm)
        if "groups" in work.columns:
            project_mask = project_mask | work["groups"].astype(str).str.contains(
                re.escape(project),
                case=False,
                na=False,
            )
        work = work[project_mask].copy()

    position = work["position"].astype(str).str.lower() if "position" in work.columns else pd.Series("", index=work.index)
    all_ids = set(work["employee_id"].dropna().astype(str).str.strip())
    merch_ids = set(work.loc[position.str.contains("мерч", na=False), "employee_id"].dropna().astype(str).str.strip())
    sv_ids = set(work.loc[position.str.contains("супервайзер", na=False), "employee_id"].dropna().astype(str).str.strip())
    tm_ids = set(
        work.loc[
            position.str.contains("территориальный", na=False)
            | position.str.fullmatch("tm", na=False)
            | position.str.fullmatch("rm", na=False)
            | position.str.contains("менеджер", na=False),
            "employee_id",
        ].dropna().astype(str).str.strip()
    )

    return {
        "frame": work,
        "all_ids": all_ids,
        "merch_ids": merch_ids,
        "sv_ids": sv_ids,
        "tm_ids": tm_ids,
    }


# ── Общие хелперы для build_page*-скриптов (вынесены сюда, т.к. были
#    независимо продублированы в нескольких файлах) ──────────────────────────

def normalize_pct(series: pd.Series) -> pd.Series:
    """0..100 или 0..1 → 0..1 (значения >1.5 считаются процентной шкалой 0..100)."""
    num = pd.to_numeric(series, errors="coerce")
    return num.where(num <= 1.5, num / 100.0)


def normalize_valid_pct(series: pd.Series) -> pd.Series:
    pct = normalize_pct(series)
    return pct.where(pct.between(0, 1))


def normalize_person_name(value: str | None) -> str | None:
    if pd.isna(value) or value is None:
        return None
    text = str(value).lower().replace("ё", "е").strip()
    text = re.sub(r"\s+", " ", text)
    return text or None


def first_notna(series: pd.Series):
    values = series.dropna()
    return values.iloc[0] if not values.empty else pd.NA


def mean_numeric(series: pd.Series):
    values = pd.to_numeric(series, errors="coerce")
    return values.mean() if values.notna().any() else pd.NA


def parse_mixed_date_series(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce", format="mixed", dayfirst=True)


def normalized_range_score(series: pd.Series, scale: float) -> float:
    clean = series.dropna()
    if len(clean) <= 1:
        return 0.0
    value_range = float(clean.max() - clean.min())
    return max(0.0, min(1.0, value_range / scale))


def last_notna(series: pd.Series):
    """Как first_notna, но берёт последнее значение и считает пустую строку пропуском."""
    clean = series.replace("", pd.NA).dropna()
    return clean.iloc[-1] if not clean.empty else pd.NA


def extract_sv_code(series: pd.Series) -> pd.Series:
    suffix = series.astype(str).str.extract(r"(\d{3,4})$", expand=False)
    return suffix.map(lambda x: f"СВ-{x}" if pd.notna(x) else pd.NA)
