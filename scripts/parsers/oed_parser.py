import re
import pandas as pd
from pathlib import Path
from scripts.utils import load_settings, normalize_dim, normalize_employee_id, save_parquet


# ── Вспомогательные функции ──────────────────────────────────────────────────

_normalize_id = normalize_employee_id

def detect_period(filename: str) -> tuple[str, int, int]:
    """Q1_2026.xlsx → ("Q1_2026", 2026, 1)."""
    stem = Path(filename).stem
    m = re.match(r"Q(\d)_(\d{4})", stem, re.IGNORECASE)
    if not m:
        return stem, 0, 0
    return stem, int(m.group(2)), int(m.group(1))


def _normalize_name(val) -> str:
    if pd.isna(val):
        return ""
    return " ".join(str(val).strip().split()).title()


# ── Матчинг сотрудников ───────────────────────────────────────────────────────

def _build_lookups(dim: pd.DataFrame) -> tuple[dict, dict, dict]:
    id_lookup, name_lookup, partial_lookup = {}, {}, {}
    for _, row in dim.iterrows():
        eid = row["employee_id"]
        if not eid:
            continue
        id_lookup[eid] = eid

        full = row["full_name"].lower().strip()
        if full:
            name_lookup[full] = eid

        short = f"{row['last_name']} {row['first_name']}".strip().lower()
        if short:
            name_lookup[short] = eid

        parts = short.split()
        if len(parts) >= 2 and parts[1]:
            key = f"{parts[0]} {parts[1][0]}"
            if key not in partial_lookup:
                partial_lookup[key] = eid

    return id_lookup, name_lookup, partial_lookup


def _match_row_with_method(id_raw, fio_raw, id_lookup, name_lookup, partial_lookup) -> tuple[str, str]:
    """Возвращает (employee_id, match_method)."""
    if id_raw and id_raw in id_lookup:
        return id_lookup[id_raw], "by_id"
    fio = _normalize_name(fio_raw).lower()
    if fio and fio in name_lookup:
        return name_lookup[fio], "by_name"
    parts = fio.split()
    if len(parts) >= 2 and parts[1]:
        key = f"{parts[0]} {parts[1][0]}"
        if key in partial_lookup:
            return partial_lookup[key], "by_name_partial"
    return "", "unmatched"


# ── Загрузка одного файла ─────────────────────────────────────────────────────

METRIC_COLS = [
    "kpi_score", "standards_score", "product_score",
    "management_score", "attestation_score", "team_score", "rating",
]

# Колонки OED → унифицированные имена
OED_RENAME = {
    "KPI":              "kpi_score",
    "продукт":          "product_score",
    "Продукт":          "product_score",
    "стандарты":        "standards_score",
    "Стандарты":        "standards_score",
    "ATTEST":           "attestation_score",
    "Аттестация":       "attestation_score",
    "управл":           "management_score",
    "Управление":       "management_score",
    "Team":             "team_score",
    "Команда":          "team_score",
    "Рейтинг":          "rating",
    "Класс":            "class",
    "Комментарий":      "comment",
    "Руководитель":     "manager_name_raw",
    "Руководитель ФИО": "manager_name_raw",
    "Дата ТД":          "contract_date",
    "Дата":             "contract_date",
    "ФИО":              "fio_raw",
    "Должность":        "position_raw",
}


def _load_oed_file(
    path: Path,
    role_type: str,
    dim: pd.DataFrame,
    id_lookup: dict,
    name_lookup: dict,
    partial_lookup: dict,
    manager_map: dict,
) -> pd.DataFrame:
    period, year, quarter = detect_period(path.name)

    raw = pd.read_excel(path, dtype=str)
    raw = raw.rename(columns={k: v for k, v in OED_RENAME.items() if k in raw.columns})
    n   = len(raw)

    # Матчинг: employee_id + способ совпадения (нужен для дедупликации)
    id_series  = raw["ID"].apply(_normalize_id)       if "ID"      in raw.columns else pd.Series([""] * n)
    fio_series = raw["fio_raw"].apply(_normalize_name) if "fio_raw" in raw.columns else pd.Series([""] * n)

    employee_ids, match_methods = [], []
    for i, f in zip(id_series, fio_series):
        eid, method = _match_row_with_method(i, f, id_lookup, name_lookup, partial_lookup)
        employee_ids.append(eid)
        match_methods.append(method)

    # manager_id из имени менеджера (актуально для SV-1)
    if "manager_name_raw" in raw.columns:
        manager_ids = raw["manager_name_raw"].apply(
            lambda nm: manager_map.get(_normalize_name(str(nm)).lower(), "") if pd.notna(nm) else ""
        )
    else:
        manager_ids = pd.Series([""] * n)

    # Числовые метрики
    metrics = {}
    for col in METRIC_COLS:
        metrics[col] = pd.to_numeric(raw[col], errors="coerce") if col in raw.columns else pd.Series([pd.NA] * n)

    # Собираем fact из реальных данных
    fact = pd.DataFrame({
        "period":       period,
        "year":         year,
        "quarter":      quarter,
        "role_type":    role_type,
        "employee_id":  pd.array(employee_ids, dtype="object"),
        "_match_method": match_methods,
        "manager_id":   manager_ids.values,
        **{col: metrics[col].values for col in METRIC_COLS},
        "class":   raw["class"].str.strip().values   if "class"   in raw.columns else [pd.NA] * n,
        "comment": raw["comment"].str.strip().values if "comment" in raw.columns else [pd.NA] * n,
        "contract_date": pd.to_datetime(raw["contract_date"], errors="coerce").values
                         if "contract_date" in raw.columns else [pd.NaT] * n,
    })

    fact["employee_id"] = fact["employee_id"].replace("", pd.NA)
    fact["manager_id"]  = fact["manager_id"].replace("",  pd.NA)

    matched   = fact["employee_id"].notna().sum()
    unmatched = fact["employee_id"].isna().sum()
    print(f"    {path.name}: {len(fact)} строк | совпало: {matched} | не найдено: {unmatched}")

    return fact


# ── Аналитические колонки ────────────────────────────────────────────────────

def _consecutive_streak(condition: pd.Series) -> pd.Series:
    """Считает длину текущей непрерывной серии True-значений для каждой строки."""
    cumsum = condition.cumsum()
    reset  = cumsum.where(~condition).ffill().fillna(0)
    return (cumsum - reset).astype(int)


def _add_analytics(df: pd.DataFrame) -> pd.DataFrame:
    # Сортируем: совпавшие по employee_id+time, несовпавшие в конец
    df = df.copy().sort_values(
        ["employee_id", "year", "quarter"], na_position="last"
    ).reset_index(drop=True)

    # Порядковый номер периода у сотрудника (groupby пропускает NA-ключи)
    df["_period_rank"] = df.groupby("employee_id").cumcount() + 1
    df["is_first_period"] = (df["_period_rank"] == 1).astype("boolean")

    # rating_delta: шаг (разница с предыдущим периодом)
    df["rating_delta"] = df.groupby("employee_id")["rating"].diff()

    # rating_delta_total: прогресс от первого периода
    first_rating = df.groupby("employee_id")["rating"].transform("first")
    df["rating_delta_total"] = df["rating"].sub(first_rating)

    # Флаги для серий
    df["_is_declining"] = (df["rating_delta"] < 0).astype("boolean")
    df["_is_treb"] = (
        df["class"].fillna("").str.lower().str.contains("требует|развити", regex=True)
    ).astype("boolean")

    # Непрерывные серии по группе сотрудника
    df["consecutive_decline"] = (
        df.groupby("employee_id", group_keys=False)["_is_declining"]
        .apply(_consecutive_streak)
        .astype("Int64")
    )
    df["consecutive_treb_razvitiya"] = (
        df.groupby("employee_id", group_keys=False)["_is_treb"]
        .apply(_consecutive_streak)
        .astype("Int64")
    )

    # churn_risk: 2+ периода падения ИЛИ 2+ периода "Требует развития"
    df["churn_risk"] = (
        (df["consecutive_decline"] >= 2) | (df["consecutive_treb_razvitiya"] >= 2)
    ).astype("boolean")

    df = df.drop(columns=["_period_rank", "_is_declining", "_is_treb"])
    return df


# ── Основная функция ──────────────────────────────────────────────────────────

def parse_oed(dim: pd.DataFrame = None) -> None:
    settings = load_settings()
    oed_root = Path(settings["sources"]["oed"]["folder"])

    if dim is None or dim.empty:
        out_path = Path(settings["sources"]["users"]["output"])
        if out_path.exists():
            dim = normalize_dim(pd.read_parquet(out_path))
            print(f"  ОЭД: загружен dim_employees ({len(dim)} записей)")
        else:
            print("  ОЭД: dim_employees не найден, матчинг будет пропущен")
            dim = pd.DataFrame()

    if dim.empty:
        id_lookup = name_lookup = partial_lookup = manager_map = {}
    else:
        id_lookup, name_lookup, partial_lookup = _build_lookups(dim)
        # Лукап имя менеджера → employee_id (для SV-1 файлов)
        manager_map = {}
        for _, row in dim.iterrows():
            eid = row["employee_id"]
            manager_map[row["full_name"].lower()] = eid
            manager_map[f"{row['last_name']} {row['first_name']}".strip().lower()] = eid

    all_facts = []

    for role_type in ("SV", "SV-1"):
        folder = oed_root / role_type
        if not folder.exists():
            print(f"  ОЭД: папка {folder} не найдена, пропускаем")
            continue

        files = sorted(folder.glob("*.xlsx"))
        print(f"  ОЭД [{role_type}]: {len(files)} файлов")

        for f in files:
            try:
                fact = _load_oed_file(
                    f, role_type, dim,
                    id_lookup, name_lookup, partial_lookup, manager_map,
                )
            except Exception as exc:
                print(f"    ОЭД: пропущен файл {f.name} — {exc.__class__.__name__}: {exc}")
                continue
            all_facts.append(fact)

    if not all_facts:
        print("  ОЭД: нет данных для обработки")
        return

    fact_oed = pd.concat(all_facts, ignore_index=True)

    # Дедупликация только совпавших (unmatched не трогаем — у них employee_id = NA)
    # Приоритет: by_id > by_name > by_name_partial, при равном методе — выше рейтинг
    _method_rank = {"by_id": 0, "by_name": 1, "by_name_partial": 2}
    matched_mask = fact_oed["employee_id"].notna()
    matched_df   = fact_oed[matched_mask].copy()
    matched_df["_method_rank"] = matched_df["_match_method"].map(_method_rank).fillna(3)
    matched_deduped = (
        matched_df
        .sort_values(["_method_rank", "rating"], ascending=[True, False], na_position="last")
        .drop_duplicates(subset=["employee_id", "period", "role_type"], keep="first")
        .drop(columns=["_method_rank"])
    )
    removed = matched_mask.sum() - len(matched_deduped)
    if removed:
        print(f"  Дедупликация: убрано {removed} дублей (приоритет by_id, затем макс. рейтинг)")
    fact_oed = pd.concat([matched_deduped, fact_oed[~matched_mask]], ignore_index=True)

    fact_oed = _add_analytics(fact_oed)

    # Итоговый порядок колонок
    col_order = [
        "employee_id", "manager_id",
        "period", "year", "quarter", "role_type",
        "kpi_score", "standards_score", "product_score",
        "management_score", "attestation_score", "team_score", "rating",
        "class", "comment", "contract_date",
        "is_first_period",
        "rating_delta", "rating_delta_total",
        "consecutive_decline", "consecutive_treb_razvitiya",
        "churn_risk",
    ]
    fact_oed = fact_oed[[c for c in col_order if c in fact_oed.columns]]

    numeric_columns = [
        "year",
        "quarter",
        "kpi_score",
        "standards_score",
        "product_score",
        "management_score",
        "attestation_score",
        "team_score",
        "rating",
        "rating_delta",
        "rating_delta_total",
        "consecutive_decline",
        "consecutive_treb_razvitiya",
    ]
    for column in numeric_columns:
        if column in fact_oed.columns:
            fact_oed[column] = pd.to_numeric(fact_oed[column], errors="coerce")

    save_parquet(fact_oed.rename(columns={
        "employee_id":              "ID сотрудника",
        "manager_id":               "ID руководителя",
        "period":                   "Период",
        "year":                     "Год",
        "quarter":                  "Квартал",
        "role_type":                "Роль",
        "kpi_score":                "Балл KPI",
        "standards_score":          "Стандарты",
        "product_score":            "Продукт",
        "management_score":         "Управление",
        "attestation_score":        "Аттестация",
        "team_score":               "Команда",
        "rating":                   "Рейтинг",
        "class":                    "Класс",
        "comment":                  "Комментарий",
        "contract_date":            "Дата договора",
        "is_first_period":          "Первый период",
        "rating_delta":             "Изменение рейтинга",
        "rating_delta_total":       "Изменение рейтинга (итого)",
        "consecutive_decline":      "Периодов снижения подряд",
        "consecutive_treb_razvitiya":"Периодов 'Требует развития' подряд",
        "churn_risk":               "Риск оттока",
    }), settings["sources"]["oed"]["output"])

    total   = len(fact_oed)
    matched = fact_oed["employee_id"].notna().sum()
    print(f"\n  ОЭД итого: {total} строк, совпало {matched} ({matched/total*100:.1f}%)")
    churn = fact_oed["churn_risk"].sum()
    print(f"  churn_risk: {churn} сотрудников в зоне риска")
