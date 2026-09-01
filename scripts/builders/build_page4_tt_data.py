import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

if __package__ is None or __package__ == "":
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from scripts.utils import load_settings, save_parquet
from scripts.staffing_utils import mode_or_first, normalize_confirmed_tm
from scripts.kpi_metric_utils import KPI_COMPONENT_COLUMNS, KPI_PUBLIC_COLUMNS, pivot_tt_kpi_metrics


TARGET_KPI = 0.95
TARGET_OKK = 0.60
TARGET_FRAUD = 0.10

MIN_HISTORY_MONTHS = 2
MIN_HISTORY_VISITS_TOTAL = 3
MIN_OKK_CHECKS = 2
MIN_PEER_TT = 5
MIN_AVAILABLE_COMPLEXITY_WEIGHT = 0.60

KPI_REPEAT_WEIGHT = 0.40
KPI_INSTABILITY_WEIGHT = 0.30
OKK_REPEAT_WEIGHT = 0.10
PEER_GAP_WEIGHT = 0.10
ME_CHANGE_WEIGHT = 0.10

ETALON_COMPLEXITY_MAX = 0.25
COMPLEX_TT_MIN = 0.50
ETALON_KPI_MIN = 0.95

PAGE4_OUTPUT_COLUMNS = [
    "MonthStart",
    "YearMonth",
    "Ранг",
    "Score ТТ",
    "ТТ",
    "Регион BI",
    "Город",
    "Сеть",
    "ТМ территория",
    "Ответственный СВ ТТ",
    "Визиты",
    "KPI проекта %",
    "ОКК %",
    "Фрод %",
    "Фрод кол-во",
    "Сложность %",
    "Статус ТТ",
    "Сложность KPI повтор %",
    "Сложность OSA/PICOS %",
    "Сложность OKK %",
    "Сложность похожие ТТ %",
    "Сложность смена МЕ %",
]

PAGE4_POWERBI_COLUMNS = [
    "MonthStart",
    "Ранг",
    "Score ТТ",
    "ТТ",
    "Регион BI",
    "Город",
    "Сеть",
    "ТМ территория",
    "Ответственный СВ ТТ",
    "Визиты",
    "KPI проекта %",
    *KPI_COMPONENT_COLUMNS,
    "ОКК %",
    "Сложность %",
    "Статус ТТ",
]


def _extract_city(address: str | None) -> str | None:
    if pd.isna(address) or not address:
        return None
    text = str(address).upper().strip()
    patterns = [
        r"Г\.\s*([А-ЯЁA-Z\- ]+?)(?:,| УЛ| МКР| ПР-КТ| ПРОСП| ПЕР| ПЛ| Д\.|$)",
        r"([А-ЯЁA-Z\- ]+?)\s+Г(?:,|\s|$)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            city = match.group(1).strip(" ,.")
            city = re.sub(r"\s+", " ", city)
            return city.title()
    return None


def _normalize_city_for_tm(value: str | None) -> str | None:
    if pd.isna(value) or value is None:
        return None

    text = str(value).upper().replace("Ё", "Е").strip()
    text = re.sub(r"\([^)]*\)", " ", text)
    text = text.replace("Г.", " ")
    text = re.sub(r"[^А-ЯA-Z0-9\- ]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()

    aliases = {
        "СПБ": "САНКТ-ПЕТЕРБУРГ",
        "С ПЕТЕРБУРГ": "САНКТ-ПЕТЕРБУРГ",
        "САНКТ ПЕТЕРБУРГ": "САНКТ-ПЕТЕРБУРГ",
        "Н НОВГОРОД": "НИЖНИЙ НОВГОРОД",
        "НИЖ НОВГОРОД": "НИЖНИЙ НОВГОРОД",
        "РОСТОВ": "РОСТОВ-НА-ДОНУ",
        "РОСТОВ НА ДОНУ": "РОСТОВ-НА-ДОНУ",
    }
    return aliases.get(text, text) or None


def _kpi_gap_score(value) -> float:
    numeric = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(numeric):
        return np.nan
    if numeric >= 0.95:
        return 0.0
    if numeric <= 0.75:
        return 1.0
    return float((0.95 - numeric) / 0.20)


def _kpi_scheme(row: pd.Series) -> str | None:
    if pd.notna(row.get("PICOS выполнение %")):
        return "PICOS"
    if pd.notna(row.get("OSA выполнение %")) and pd.notna(row.get("TOP16 выполнение %")):
        return "OSA+TOP16"
    return None


def _monthly_kpi_gap(row: pd.Series) -> float:
    scheme = _kpi_scheme(row)
    if scheme == "PICOS":
        return _kpi_gap_score(row.get("PICOS выполнение %"))
    if scheme == "OSA+TOP16":
        return float(
            0.5 * _kpi_gap_score(row.get("OSA выполнение %"))
            + 0.5 * _kpi_gap_score(row.get("TOP16 выполнение %"))
        )
    return np.nan


def _available_weighted_complexity(parts: list[tuple[float, float]]) -> tuple[float, float]:
    available = [(weight, score) for weight, score in parts if pd.notna(score)]
    available_weight = sum(weight for weight, _ in available)
    if available_weight < MIN_AVAILABLE_COMPLEXITY_WEIGHT:
        return np.nan, available_weight
    score = sum(weight * float(value) for weight, value in available) / available_weight
    return float(max(0.0, min(1.0, score))), available_weight


def _join_unique_text(series: pd.Series, limit: int = 5) -> str | None:
    values = sorted({str(value).strip() for value in series.dropna() if str(value).strip()})
    if not values:
        return None
    if len(values) <= limit:
        return ", ".join(values)
    return ", ".join(values[:limit]) + f" + еще {len(values) - limit}"


def _build_tt_agency_tm_assignment(
    kpi_tt_direct: pd.DataFrame | None,
    kpi: pd.DataFrame,
    teams: pd.DataFrame | None,
) -> pd.DataFrame:
    columns = [
        "MonthStart",
        "YearMonth",
        "ТТ",
        "AGENCY SV",
        "Группа продаж KPI",
        "Регион BI KPI",
        "AGENCY SV вариантов на ТТ",
        "ID ТМ по AGENCY SV",
        "ТМ по AGENCY SV",
        "Тип привязки ТМ AGENCY SV",
        "ТМ AGENCY SV список",
        "ТМ AGENCY SV кол-во",
        "МЕ в AGENCY SV",
    ]
    route_col = "Код маршрута СВ"
    if (
        kpi_tt_direct is None
        or kpi_tt_direct.empty
        or teams is None
        or teams.empty
        or route_col not in kpi_tt_direct.columns
        or route_col not in kpi.columns
    ):
        return pd.DataFrame(columns=columns)

    tt_required = ["MonthStart", "YearMonth", "ТТ", "Регион BI", route_col]
    if not set(tt_required).issubset(kpi_tt_direct.columns):
        return pd.DataFrame(columns=columns)

    tt_agency = (
        kpi_tt_direct[tt_required + [c for c in ["Группа продаж"] if c in kpi_tt_direct.columns]]
        .replace("", pd.NA)
        .dropna(subset=["MonthStart", "YearMonth", "ТТ", route_col])
        .copy()
    )
    if tt_agency.empty:
        return pd.DataFrame(columns=columns)

    tt_agency["ТТ"] = tt_agency["ТТ"].astype(str)
    tt_agg = (
        tt_agency.groupby(["MonthStart", "YearMonth", "ТТ"], dropna=False)
        .agg(
            **{
                "AGENCY SV": (route_col, mode_or_first),
                "Группа продаж KPI": ("Группа продаж", mode_or_first) if "Группа продаж" in tt_agency.columns else (route_col, lambda s: pd.NA),
                "Регион BI KPI": ("Регион BI", mode_or_first),
                "AGENCY SV вариантов на ТТ": (route_col, "nunique"),
            }
        )
        .reset_index()
    )

    team_columns = [
        "ID мерчендайзера",
        "ID территориального менеджера",
        "Территориальный менеджер",
        "Регион BI",
    ]
    if not set(team_columns).issubset(teams.columns) or "ID мерчендайзера" not in kpi.columns:
        tt_agg["ID ТМ по AGENCY SV"] = "NO_AGENCY_TM"
        tt_agg["ТМ по AGENCY SV"] = "Нет привязки по AGENCY SV"
        tt_agg["Тип привязки ТМ AGENCY SV"] = "Нет данных для маппинга"
        tt_agg["ТМ AGENCY SV список"] = pd.NA
        tt_agg["ТМ AGENCY SV кол-во"] = pd.NA
        tt_agg["МЕ в AGENCY SV"] = pd.NA
        return tt_agg[columns]

    team_dir = (
        teams[team_columns]
        .replace("", pd.NA)
        .dropna(subset=["ID мерчендайзера"])
        .drop_duplicates("ID мерчендайзера", keep="last")
    )
    team_dir = normalize_confirmed_tm(team_dir)

    kpi_routes = (
        kpi[["MonthStart", "YearMonth", route_col, "ID мерчендайзера"]]
        .replace("", pd.NA)
        .dropna(subset=["MonthStart", "YearMonth", route_col, "ID мерчендайзера"])
        .merge(team_dir, on="ID мерчендайзера", how="left")
        .dropna(subset=["Регион BI"])
        .copy()
    )
    if kpi_routes.empty:
        tt_agg["ID ТМ по AGENCY SV"] = "NO_AGENCY_TM"
        tt_agg["ТМ по AGENCY SV"] = "Нет привязки по AGENCY SV"
        tt_agg["Тип привязки ТМ AGENCY SV"] = "Нет совпадения AGENCY SV"
        tt_agg["ТМ AGENCY SV список"] = pd.NA
        tt_agg["ТМ AGENCY SV кол-во"] = pd.NA
        tt_agg["МЕ в AGENCY SV"] = pd.NA
        return tt_agg[columns]

    route_tm = (
        kpi_routes.groupby(["MonthStart", "YearMonth", route_col, "Регион BI"], dropna=False)
        .agg(
            **{
                "МЕ в AGENCY SV": ("ID мерчендайзера", "nunique"),
                "ТМ AGENCY SV кол-во": ("ID территориального менеджера", "nunique"),
                "ТМ AGENCY SV список": ("Территориальный менеджер", _join_unique_text),
                "ID ТМ raw": ("ID территориального менеджера", mode_or_first),
                "ТМ raw": ("Территориальный менеджер", mode_or_first),
            }
        )
        .reset_index()
        .rename(columns={route_col: "AGENCY SV", "Регион BI": "Регион BI KPI"})
    )

    result = tt_agg.merge(
        route_tm,
        on=["MonthStart", "YearMonth", "AGENCY SV", "Регион BI KPI"],
        how="left",
    )
    result["Тип привязки ТМ AGENCY SV"] = np.select(
        [
            result["AGENCY SV вариантов на ТТ"].fillna(0).gt(1),
            result["ТМ AGENCY SV кол-во"].isna(),
            result["ТМ AGENCY SV кол-во"].fillna(0).eq(0),
            result["ТМ AGENCY SV кол-во"].fillna(0).eq(1),
            result["ТМ AGENCY SV кол-во"].fillna(0).gt(1),
        ],
        [
            "Несколько AGENCY SV на ТТ",
            "Нет совпадения AGENCY SV",
            "Нет ТМ по AGENCY SV",
            "Уверенная TM-привязка",
            "Несколько ТМ по AGENCY SV",
        ],
        default="Нет совпадения AGENCY SV",
    )
    unique_mask = result["Тип привязки ТМ AGENCY SV"].eq("Уверенная TM-привязка")
    result["ID ТМ по AGENCY SV"] = np.where(unique_mask, result["ID ТМ raw"], "MULTI_OR_NO_AGENCY_TM")
    result["ТМ по AGENCY SV"] = np.select(
        [
            unique_mask,
            result["Тип привязки ТМ AGENCY SV"].eq("Несколько ТМ по AGENCY SV"),
            result["Тип привязки ТМ AGENCY SV"].eq("Несколько AGENCY SV на ТТ"),
        ],
        [
            result["ТМ raw"],
            "Несколько ТМ по AGENCY SV",
            "Несколько AGENCY SV на ТТ",
        ],
        default="Нет привязки по AGENCY SV",
    )
    return result[columns]


def _build_tt_org_assignment(okk: pd.DataFrame, teams: pd.DataFrame | None) -> pd.DataFrame:
    columns = [
        "MonthStart",
        "YearMonth",
        "ТТ",
        "ID супервайзера",
        "Супервайзер",
        "ID территориального менеджера",
        "Территориальный менеджер",
        "Регион BI СВ",
        "Визиты с активной привязкой",
        "Визиты основного СВ",
        "Доля визитов основного СВ %",
        "Супервайзеров на ТТ",
        "ТМ на ТТ",
        "Супервайзеры ТТ",
        "Территориальные менеджеры ТТ",
    ]
    if teams is None or teams.empty:
        return pd.DataFrame(columns=columns)

    team_columns = [
        "ID мерчендайзера",
        "ID супервайзера",
        "Супервайзер",
        "ID территориального менеджера",
        "Территориальный менеджер",
        "Регион BI",
    ]
    if not set(team_columns).issubset(teams.columns):
        return pd.DataFrame(columns=columns)

    team_dir = teams[team_columns].replace("", pd.NA).copy()
    team_dir = team_dir.dropna(subset=["ID мерчендайзера"]).drop_duplicates("ID мерчендайзера", keep="last")
    team_dir = normalize_confirmed_tm(team_dir)

    visit_columns = ["MonthStart", "YearMonth", "Код ТТ", "ID мерчендайзера", "Дата визита"]
    if not set(visit_columns).issubset(okk.columns):
        return pd.DataFrame(columns=columns)

    visits = okk[visit_columns].replace("", pd.NA).dropna(
        subset=["MonthStart", "YearMonth", "Код ТТ", "ID мерчендайзера"]
    ).copy()
    if visits.empty:
        return pd.DataFrame(columns=columns)

    visits["ТТ"] = visits["Код ТТ"].astype(str)
    visits = visits.merge(team_dir, on="ID мерчендайзера", how="left")
    visits = visits[visits["ID супервайзера"].notna()].copy()
    if visits.empty:
        return pd.DataFrame(columns=columns)

    keys = ["MonthStart", "YearMonth", "ТТ"]
    summary = (
        visits.groupby(keys, dropna=False)
        .agg(
            **{
                "Визиты с активной привязкой": ("Дата визита", "count"),
                "Супервайзеров на ТТ": ("ID супервайзера", "nunique"),
                "ТМ на ТТ": ("ID территориального менеджера", "nunique"),
                "Супервайзеры ТТ": ("Супервайзер", _join_unique_text),
                "Территориальные менеджеры ТТ": ("Территориальный менеджер", _join_unique_text),
            }
        )
        .reset_index()
    )

    primary = (
        visits.groupby(
            keys
            + [
                "ID супервайзера",
                "Супервайзер",
                "ID территориального менеджера",
                "Территориальный менеджер",
                "Регион BI",
            ],
            dropna=False,
        )
        .agg(**{"Визиты основного СВ": ("Дата визита", "count")})
        .reset_index()
        .sort_values(
            keys + ["Визиты основного СВ", "Супервайзер"],
            ascending=[True, True, True, False, True],
            na_position="last",
        )
        .drop_duplicates(keys, keep="first")
    )

    result = summary.merge(
        primary[
            keys
            + [
                "ID супервайзера",
                "Супервайзер",
                "ID территориального менеджера",
                "Территориальный менеджер",
                "Регион BI",
                "Визиты основного СВ",
            ]
        ],
        on=keys,
        how="left",
    )
    result = result.rename(columns={"Регион BI": "Регион BI СВ"})
    result["Доля визитов основного СВ %"] = (
        result["Визиты основного СВ"] / result["Визиты с активной привязкой"].replace(0, np.nan)
    )
    return result[columns]


def _build_tm_territory_maps(teams: pd.DataFrame | None) -> tuple[pd.DataFrame, pd.DataFrame]:
    city_columns = [
        "Регион BI",
        "Город нормализованный",
        "ID ТМ города USERS",
        "ТМ города USERS",
        "ТМ города список",
        "ТМ города кол-во",
        "МЕ в городе USERS",
    ]
    region_columns = [
        "Регион BI",
        "ID ТМ региона USERS",
        "ТМ региона USERS",
        "ТМ региона список",
        "ТМ региона кол-во",
        "МЕ в регионе USERS",
    ]

    required_columns = [
        "ID мерчендайзера",
        "Регион BI",
        "Город мерчендайзера",
        "ID территориального менеджера",
        "Территориальный менеджер",
    ]
    if teams is None or teams.empty or not set(required_columns).issubset(teams.columns):
        return pd.DataFrame(columns=city_columns), pd.DataFrame(columns=region_columns)

    team_dir = (
        teams[required_columns]
        .replace("", pd.NA)
        .dropna(subset=["ID мерчендайзера", "Регион BI"])
        .drop_duplicates("ID мерчендайзера", keep="last")
        .copy()
    )
    if team_dir.empty:
        return pd.DataFrame(columns=city_columns), pd.DataFrame(columns=region_columns)

    team_dir = normalize_confirmed_tm(team_dir)
    team_dir["Город нормализованный"] = team_dir["Город мерчендайзера"].map(_normalize_city_for_tm)

    city_map = (
        team_dir.dropna(subset=["Город нормализованный"])
        .groupby(["Регион BI", "Город нормализованный"], dropna=False)
        .agg(
            **{
                "ID ТМ города USERS": ("ID территориального менеджера", mode_or_first),
                "ТМ города USERS": ("Территориальный менеджер", mode_or_first),
                "ТМ города список": ("Территориальный менеджер", _join_unique_text),
                "ТМ города кол-во": ("ID территориального менеджера", "nunique"),
                "МЕ в городе USERS": ("ID мерчендайзера", "nunique"),
            }
        )
        .reset_index()
    )

    region_map = (
        team_dir.groupby(["Регион BI"], dropna=False)
        .agg(
            **{
                "ID ТМ региона USERS": ("ID территориального менеджера", mode_or_first),
                "ТМ региона USERS": ("Территориальный менеджер", mode_or_first),
                "ТМ региона список": ("Территориальный менеджер", _join_unique_text),
                "ТМ региона кол-во": ("ID территориального менеджера", "nunique"),
                "МЕ в регионе USERS": ("ID мерчендайзера", "nunique"),
            }
        )
        .reset_index()
    )

    return city_map[city_columns], region_map[region_columns]


def _attach_tm_territory_assignment(snapshot: pd.DataFrame, teams: pd.DataFrame | None) -> pd.DataFrame:
    result = snapshot.copy()
    result["Город нормализованный"] = result["Город"].map(_normalize_city_for_tm)

    city_map, region_map = _build_tm_territory_maps(teams)
    if not city_map.empty:
        result = result.merge(city_map, on=["Регион BI", "Город нормализованный"], how="left")
    else:
        for column in city_map.columns:
            if column not in ["Регион BI", "Город нормализованный"]:
                result[column] = pd.NA

    if not region_map.empty:
        result = result.merge(region_map, on="Регион BI", how="left")
    else:
        for column in region_map.columns:
            if column != "Регион BI":
                result[column] = pd.NA

    result = normalize_confirmed_tm(result)
    confirmed_tm = result["Территориальный менеджер"].notna()
    result["ID ТМ территория"] = result["ID территориального менеджера"]
    result["ТМ территория"] = result["Территориальный менеджер"]
    result["Метод привязки ТМ"] = pd.Series(pd.NA, index=result.index, dtype="string")
    result.loc[confirmed_tm, "Метод привязки ТМ"] = "Актуальный USERS по МЕ визита"
    result["Доверие привязки ТМ"] = np.where(confirmed_tm, 1.0, 0.0)
    result["Статус привязки ТМ"] = np.where(confirmed_tm, "Подтверждена", "Нет привязки")
    result["Конфликт ТМ город/AGENCY"] = False

    return result


def _build_assignment_type(row: pd.Series) -> str:
    if row.get("ID супервайзера") == "NO_ACTIVE_SV":
        return "Нет активной привязки в USERS"

    primary_share = row.get("Доля визитов основного СВ %")
    supervisor_count = row.get("Супервайзеров на ТТ")
    if pd.notna(primary_share) and pd.notna(supervisor_count):
        if float(supervisor_count) > 1 and float(primary_share) < 0.60:
            return "Несколько СВ / подмена"

    store_region = row.get("Регион BI")
    supervisor_region = row.get("Регион BI СВ")
    if pd.notna(store_region) and pd.notna(supervisor_region) and store_region != supervisor_region:
        return "Вне региона СВ / подмена"

    return "Уверенная привязка"


def _responsible_supervisor(row: pd.Series) -> str:
    assignment_type = row.get("Тип привязки ТТ")
    if assignment_type == "Уверенная привязка":
        return row.get("Супервайзер")
    if assignment_type == "Несколько СВ / подмена":
        return "Несколько СВ / подмена"
    if assignment_type == "Вне региона СВ / подмена":
        return "Вне региона СВ / подмена"
    return "Нет активной привязки в USERS"


def _responsible_tm(row: pd.Series) -> str:
    assignment_type = row.get("Тип привязки ТТ")
    if assignment_type == "Уверенная привязка":
        return row.get("Территориальный менеджер")
    if assignment_type == "Несколько СВ / подмена":
        return "Несколько ТМ / подмена"
    if assignment_type == "Вне региона СВ / подмена":
        return "Вне региона СВ / подмена"
    return "Нет активной привязки в USERS"


def _build_tt_monthly_base(
    okk: pd.DataFrame,
    kpi: pd.DataFrame,
    kpi_tt_direct: pd.DataFrame | None = None,
) -> pd.DataFrame:
    okk_work = okk.copy()
    okk_work["_Нарушение ОКК"] = pd.to_numeric(
        okk_work["Качество визита"], errors="coerce"
    ).lt(TARGET_OKK)
    okk_work["_Фрод"] = okk_work["Флаг фальсификации"].fillna(False).eq(True)
    okk_base = (
        okk_work.groupby(["MonthStart", "YearMonth", "Код ТТ"], dropna=False)
        .agg(
            **{
                "Регион BI": ("Регион BI", "first"),
                "Сеть": ("Сеть", "first"),
                "Адрес": ("Адрес", "first"),
                "Проверки ОКК": ("Качество визита", "count"),
                "ОКК %": ("Качество визита", "mean"),
                "Нарушений ОКК": ("_Нарушение ОКК", "sum"),
                "Фрод %": ("Флаг фальсификации", "mean"),
                "Фрод кол-во": ("_Фрод", "sum"),
            }
        )
        .reset_index()
    )
    okk_base["MonthStart"] = pd.to_datetime(okk_base["MonthStart"], errors="coerce")
    okk_base["YearMonth"] = pd.to_numeric(okk_base["YearMonth"], errors="coerce").astype("Int64")
    okk_base["Код ТТ"] = okk_base["Код ТТ"].astype("string").str.strip()
    okk_base["Город"] = okk_base["Адрес"].map(_extract_city)

    if kpi_tt_direct is not None and not kpi_tt_direct.empty:
        tt_kpi = pivot_tt_kpi_metrics(kpi_tt_direct).rename(columns={"ТТ": "Код ТТ"})
        source = kpi_tt_direct.copy()
        source["MonthStart"] = pd.to_datetime(source["MonthStart"], errors="coerce")
        source["YearMonth"] = pd.to_numeric(source["YearMonth"], errors="coerce").astype("Int64")
        source["Код ТТ"] = source["ТТ"].astype("string").str.strip()
        metadata = (
            source.groupby(["MonthStart", "YearMonth", "Код ТТ"], dropna=False)
            .agg(
                **{
                    "Регион BI KPI": ("Регион BI", "first"),
                    "Сеть KPI": ("Сеть", "first"),
                    "Город KPI": ("Город", "first"),
                    "Адрес KPI": ("Адрес", "first"),
                }
            )
            .reset_index()
        )
        tt_kpi = tt_kpi.merge(metadata, on=["MonthStart", "YearMonth", "Код ТТ"], how="left")
    else:
        tt_kpi = pd.DataFrame(
            columns=[
                "MonthStart",
                "YearMonth",
                "Код ТТ",
                *KPI_PUBLIC_COLUMNS,
                "Регион BI KPI",
                "Сеть KPI",
                "Город KPI",
                "Адрес KPI",
            ]
        )
    base = tt_kpi.merge(
        okk_base,
        on=["MonthStart", "YearMonth", "Код ТТ"],
        how="outer",
    )
    for target, source_column in [
        ("Регион BI", "Регион BI KPI"),
        ("Сеть", "Сеть KPI"),
        ("Город", "Город KPI"),
        ("Адрес", "Адрес KPI"),
    ]:
        if source_column in base.columns:
            if target not in base.columns:
                base[target] = base[source_column]
            else:
                base[target] = base[source_column].combine_first(base[target])
    base = base.drop(
        columns=["Регион BI KPI", "Сеть KPI", "Город KPI", "Адрес KPI"],
        errors="ignore",
    )
    for column in KPI_PUBLIC_COLUMNS:
        if column not in base.columns:
            base[column] = np.nan
        base[column] = pd.to_numeric(base[column], errors="coerce")
    picos = pd.to_numeric(base["PICOS выполнение %"], errors="coerce")
    osa = pd.to_numeric(base["OSA выполнение %"], errors="coerce")
    top16 = pd.to_numeric(base["TOP16 выполнение %"], errors="coerce")
    picos_available = picos.notna()
    osa_top16_available = ~picos_available & osa.notna() & top16.notna()
    base["KPI схема"] = np.select(
        [picos_available, osa_top16_available],
        ["PICOS", "OSA+TOP16"],
        default=None,
    )
    picos_gap = ((TARGET_KPI - picos) / 0.20).clip(lower=0, upper=1)
    osa_gap = ((TARGET_KPI - osa) / 0.20).clip(lower=0, upper=1)
    top16_gap = ((TARGET_KPI - top16) / 0.20).clip(lower=0, upper=1)
    base["Разрыв KPI"] = picos_gap.where(picos_available)
    base.loc[osa_top16_available, "Разрыв KPI"] = (
        osa_gap.loc[osa_top16_available] + top16_gap.loc[osa_top16_available]
    ) / 2.0
    base["Просадка KPI проекта"] = base["KPI проекта %"] < TARGET_KPI
    base["Нарушение OKK"] = base["ОКК %"] < TARGET_OKK
    return base


def _build_rtm_complexity_history(rtm_visits: pd.DataFrame | None) -> pd.DataFrame:
    keys = ["MonthStart", "YearMonth", "Код ТТ"]
    if rtm_visits is None or rtm_visits.empty:
        return pd.DataFrame(columns=keys)
    required = {"MonthStart", "YearMonth", "ТТ", "Ключ визита RTM", "ID сотрудника"}
    if not required.issubset(rtm_visits.columns):
        return pd.DataFrame(columns=keys)

    visits = rtm_visits.copy()
    visits["MonthStart"] = pd.to_datetime(visits["MonthStart"], errors="coerce")
    visits["YearMonth"] = pd.to_numeric(visits["YearMonth"], errors="coerce").astype("Int64")
    visits["Код ТТ"] = visits["ТТ"].astype("string").str.strip()
    visits = visits.dropna(subset=[*keys, "Ключ визита RTM"])

    summary = (
        visits.groupby(keys, dropna=False)
        .agg(
            **{
                "Визиты RTM": ("Ключ визита RTM", "nunique"),
                "МЕ RTM на ТТ": ("ID сотрудника", "nunique"),
            }
        )
        .reset_index()
    )
    assigned = visits.dropna(subset=["ID сотрудника"]).copy()
    if assigned.empty:
        summary["Основной МЕ RTM"] = pd.NA
        return summary

    primary = (
        assigned.groupby([*keys, "ID сотрудника"], dropna=False)["Ключ визита RTM"]
        .nunique()
        .reset_index(name="Визиты МЕ RTM")
        .sort_values(
            [*keys, "Визиты МЕ RTM", "ID сотрудника"],
            ascending=[True, True, True, False, True],
            kind="mergesort",
        )
        .drop_duplicates(keys, keep="first")
        .rename(columns={"ID сотрудника": "Основной МЕ RTM"})
    )
    return summary.merge(primary[[*keys, "Основной МЕ RTM"]], on=keys, how="left")


def _build_tt_complexity_snapshot(
    okk: pd.DataFrame,
    kpi: pd.DataFrame,
    kpi_tt_direct: pd.DataFrame | None = None,
    teams: pd.DataFrame | None = None,
    rtm_visits: pd.DataFrame | None = None,
) -> pd.DataFrame:
    tt_base = _build_tt_monthly_base(okk, kpi, kpi_tt_direct=kpi_tt_direct)
    rtm_history = _build_rtm_complexity_history(rtm_visits)
    if not rtm_history.empty:
        tt_base = tt_base.merge(
            rtm_history,
            on=["MonthStart", "YearMonth", "Код ТТ"],
            how="left",
        )
    for column in ["Визиты RTM", "МЕ RTM на ТТ", "Основной МЕ RTM"]:
        if column not in tt_base.columns:
            tt_base[column] = np.nan
    peer_keys = ["MonthStart", "Сеть", "Город", "KPI схема"]
    tt_base["ТТ в группе KPI"] = tt_base.groupby(peer_keys, dropna=False)["Код ТТ"].transform("count")
    tt_base["Медиана KPI группы"] = tt_base.groupby(peer_keys, dropna=False)["KPI проекта %"].transform("median")
    history_columns = [
        "Разрыв KPI",
        "Визиты RTM",
        "PICOS выполнение %",
        "OSA выполнение %",
        "TOP16 выполнение %",
        "Проверки ОКК",
        "Нарушений ОКК",
        "Основной МЕ RTM",
    ]
    history_frame = tt_base.copy()
    for lag in [1, 2]:
        source = tt_base[["Код ТТ", "MonthStart", *history_columns]].copy()
        source["MonthStart"] = source["MonthStart"] + pd.DateOffset(months=lag)
        source = source.rename(
            columns={column: f"{column} lag {lag}" for column in history_columns}
        )
        history_frame = history_frame.merge(
            source,
            on=["Код ТТ", "MonthStart"],
            how="left",
            validate="one_to_one",
        )

    def numeric_history(column: str) -> pd.DataFrame:
        return pd.DataFrame(
            {
                2: pd.to_numeric(history_frame[f"{column} lag 2"], errors="coerce"),
                1: pd.to_numeric(history_frame[f"{column} lag 1"], errors="coerce"),
                0: pd.to_numeric(history_frame[column], errors="coerce"),
            }
        )

    def normalized_history_range(values: pd.DataFrame, valid: pd.DataFrame | None = None) -> pd.Series:
        selected = values.where(valid) if valid is not None else values
        count = selected.notna().sum(axis=1)
        score = ((selected.max(axis=1) - selected.min(axis=1)) / 0.20).clip(0.0, 1.0)
        return score.where(count >= MIN_HISTORY_MONTHS)

    gap_history = numeric_history("Разрыв KPI")
    complete_kpi_months = gap_history.notna().sum(axis=1)
    kpi_repeat_score = gap_history.mean(axis=1).where(
        complete_kpi_months >= MIN_HISTORY_MONTHS
    )

    picos_history = numeric_history("PICOS выполнение %")
    osa_history = numeric_history("OSA выполнение %")
    top16_history = numeric_history("TOP16 выполнение %")
    paired_osa_top16 = osa_history.notna() & top16_history.notna()
    picos_instability = normalized_history_range(picos_history)
    osa_instability = normalized_history_range(osa_history, paired_osa_top16)
    top16_instability = normalized_history_range(top16_history, paired_osa_top16)
    kpi_instability_score = pd.Series(np.nan, index=history_frame.index, dtype=float)
    picos_scheme = history_frame["KPI схема"].eq("PICOS")
    osa_top16_scheme = history_frame["KPI схема"].eq("OSA+TOP16")
    kpi_instability_score.loc[picos_scheme] = picos_instability.loc[picos_scheme]
    kpi_instability_score.loc[osa_top16_scheme] = (
        0.5 * osa_instability.loc[osa_top16_scheme]
        + 0.5 * top16_instability.loc[osa_top16_scheme]
    )

    visits_history = numeric_history("Визиты RTM")
    history_visits_count = visits_history.fillna(0).sum(axis=1)
    okk_checks = numeric_history("Проверки ОКК").fillna(0).sum(axis=1)
    okk_violations = numeric_history("Нарушений ОКК").fillna(0).sum(axis=1)
    okk_repeat_score = (okk_violations / okk_checks).where(okk_checks >= MIN_OKK_CHECKS)

    current_kpi = pd.to_numeric(history_frame["KPI проекта %"], errors="coerce")
    peer_count = pd.to_numeric(history_frame["ТТ в группе KPI"], errors="coerce")
    peer_kpi = pd.to_numeric(history_frame["Медиана KPI группы"], errors="coerce")
    peer_available = (
        peer_count.sub(1).ge(MIN_PEER_TT)
        & peer_kpi.notna()
        & current_kpi.notna()
    )
    peer_gap_score = ((peer_kpi - current_kpi) / 0.20).clip(0.0, 1.0).where(peer_available)

    primary_history = pd.DataFrame(
        {
            2: history_frame["Основной МЕ RTM lag 2"].astype("string"),
            1: history_frame["Основной МЕ RTM lag 1"].astype("string"),
            0: history_frame["Основной МЕ RTM"].astype("string"),
        }
    )
    primary_available = primary_history.notna()
    primary_count = primary_available.sum(axis=1)
    primary_changes = (
        (primary_available[2] & primary_available[1] & primary_history[2].ne(primary_history[1])).astype(int)
        + (primary_available[1] & primary_available[0] & primary_history[1].ne(primary_history[0])).astype(int)
        + (
            primary_available[2]
            & ~primary_available[1]
            & primary_available[0]
            & primary_history[2].ne(primary_history[0])
        ).astype(int)
    )
    me_change_score = (primary_changes / primary_count.sub(1)).where(
        history_visits_count.ge(MIN_HISTORY_VISITS_TOTAL)
        & primary_count.ge(MIN_HISTORY_MONTHS)
    )

    scores = pd.DataFrame(
        {
            "kpi": kpi_repeat_score,
            "instability": kpi_instability_score,
            "okk": okk_repeat_score,
            "peer": peer_gap_score,
            "me": me_change_score,
        }
    )
    weights = pd.Series(
        {
            "kpi": KPI_REPEAT_WEIGHT,
            "instability": KPI_INSTABILITY_WEIGHT,
            "okk": OKK_REPEAT_WEIGHT,
            "peer": PEER_GAP_WEIGHT,
            "me": ME_CHANGE_WEIGHT,
        }
    )
    available_weight = scores.notna().mul(weights, axis=1).sum(axis=1)
    complexity_share = scores.fillna(0).mul(weights, axis=1).sum(axis=1) / available_weight
    complexity_share = complexity_share.clip(0.0, 1.0).where(
        available_weight >= MIN_AVAILABLE_COMPLEXITY_WEIGHT
    )

    bad_primary = gap_history.gt(0.25) & primary_available
    multiple_bad_merch = (
        (bad_primary[2] & bad_primary[1] & primary_history[2].ne(primary_history[1]))
        | (bad_primary[2] & bad_primary[0] & primary_history[2].ne(primary_history[0]))
        | (bad_primary[1] & bad_primary[0] & primary_history[1].ne(primary_history[0]))
    )
    insufficient_data = (
        pd.to_numeric(history_frame["Разрыв KPI"], errors="coerce").isna()
        | complete_kpi_months.lt(MIN_HISTORY_MONTHS)
        | history_visits_count.lt(MIN_HISTORY_VISITS_TOTAL)
        | complexity_share.isna()
    )
    complexity_share_output = complexity_share.mask(insufficient_data)
    current_okk = pd.to_numeric(history_frame["ОКК %"], errors="coerce")
    etalon = (
        ~insufficient_data
        & complexity_share.le(ETALON_COMPLEXITY_MAX)
        & current_kpi.ge(ETALON_KPI_MIN)
        & (current_okk.isna() | current_okk.ge(TARGET_OKK))
    )
    complex_tt = ~insufficient_data & ~etalon & complexity_share.ge(COMPLEX_TT_MIN)
    status = np.select(
        [insufficient_data, etalon, complex_tt & multiple_bad_merch, complex_tt],
        ["Недостаточно данных", "Эталон", "Не вина МЕ", "Сложная ТТ"],
        default="Контроль",
    )
    score_tt = (
        current_kpi * 55 + (1 - complexity_share_output) * 45
    ).clip(0.0, 100.0).round(0)

    snapshot = pd.DataFrame(
        {
            "MonthStart": history_frame["MonthStart"],
            "YearMonth": history_frame["YearMonth"],
            "ТТ": history_frame["Код ТТ"].astype("string"),
            "Регион BI": history_frame["Регион BI"],
            "Город": history_frame["Город"],
            "Сеть": history_frame["Сеть"],
            "Визиты": pd.to_numeric(history_frame["Визиты RTM"], errors="coerce").fillna(0),
            "KPI проекта %": current_kpi,
            "ОКК %": current_okk,
            "Фрод %": history_frame["Фрод %"],
            "Фрод кол-во": history_frame["Фрод кол-во"],
            "Сложность %": complexity_share_output,
            "Score ТТ": score_tt,
            "Статус ТТ": status,
            "Сложность KPI повтор %": (KPI_REPEAT_WEIGHT * kpi_repeat_score).round(4),
            "Сложность OSA/PICOS %": (KPI_INSTABILITY_WEIGHT * kpi_instability_score).round(4),
            "Сложность OKK %": (OKK_REPEAT_WEIGHT * okk_repeat_score).round(4),
            "Сложность похожие ТТ %": (PEER_GAP_WEIGHT * peer_gap_score).round(4),
            "Сложность смена МЕ %": (ME_CHANGE_WEIGHT * me_change_score).round(4),
        }
    )
    if snapshot.empty:
        return snapshot

    numeric_columns = [
        "Визиты",
        "KPI проекта %",
        "ОКК %",
        "PICOS %",
        "OSA %",
        "Фрод %",
        "Фрод кол-во",
        "Сложность %",
        "Score ТТ",
        "Сложность KPI повтор %",
        "Сложность OSA/PICOS %",
        "Сложность OKK %",
        "Сложность похожие ТТ %",
        "Сложность смена МЕ %",
        "Визиты с активной привязкой",
        "Визиты основного СВ",
        "Доля визитов основного СВ %",
        "Супервайзеров на ТТ",
        "ТМ на ТТ",
        "AGENCY SV вариантов на ТТ",
        "ТМ AGENCY SV кол-во",
        "МЕ в AGENCY SV",
        "ТМ города кол-во",
        "МЕ в городе USERS",
        "ТМ региона кол-во",
        "МЕ в регионе USERS",
        "Доверие привязки ТМ",
    ]
    for column in numeric_columns:
        if column in snapshot.columns:
            snapshot[column] = pd.to_numeric(snapshot[column], errors="coerce")

    pieces = []
    for month_start, month_df in snapshot.groupby("MonthStart"):
        ranked = month_df.copy()
        ranked["Сортировка статуса"] = ranked["Статус ТТ"].map(
            {
                "Эталон": 1,
                "Контроль": 2,
                "Не вина МЕ": 3,
                "Сложная ТТ": 4,
                "Недостаточно данных": 5,
            }
        ).fillna(5)
        ranked = ranked.sort_values(
            ["Сортировка статуса", "Score ТТ", "KPI проекта %", "ОКК %", "Визиты", "ТТ"],
            ascending=[True, False, False, False, False, True],
            na_position="last",
            kind="mergesort",
        ).copy()
        actionable = (
            ranked[ranked["Статус ТТ"] != "Недостаточно данных"]
            .sort_values(
                ["Score ТТ", "KPI проекта %", "ОКК %", "Визиты", "ТТ"],
                ascending=[False, False, False, False, True],
                na_position="last",
                kind="mergesort",
            )
            .copy()
        )
        actionable["Ранг"] = pd.array(range(1, len(actionable) + 1), dtype="Int64")
        ranked = ranked.merge(
            actionable[["MonthStart", "ТТ", "Ранг"]],
            on=["MonthStart", "ТТ"],
            how="left",
        )
        ranked = ranked.drop(columns=["Сортировка статуса"], errors="ignore")
        pieces.append(ranked)

    result = pd.concat(pieces, ignore_index=True)
    output_columns = [column for column in PAGE4_OUTPUT_COLUMNS if column in result.columns]
    return result[output_columns]


def _build_formula_table() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"Порядок": 1, "Вес": KPI_REPEAT_WEIGHT, "Формула": "40%", "Описание": "повторяемый разрыв PICOS либо OSA + TOP16 относительно целевого выполнения 95%"},
            {"Порядок": 2, "Вес": KPI_INSTABILITY_WEIGHT, "Формула": "30%", "Описание": "нестабильность выполнения PICOS, OSA и TOP16 за последние три месяца"},
            {"Порядок": 3, "Вес": OKK_REPEAT_WEIGHT, "Формула": "10%", "Описание": "повторяемость ОКК ниже 60%; если проверок нет, блок исключается без штрафа"},
            {"Порядок": 4, "Вес": PEER_GAP_WEIGHT, "Формула": "10%", "Описание": "отклонение от медианы похожих ТТ той же сети, города и KPI-схемы"},
            {"Порядок": 5, "Вес": ME_CHANGE_WEIGHT, "Формула": "10%", "Описание": "смена основного МЕ по подтвержденным визитам RTM"},
        ]
    )


def _build_status_legend_table() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Порядок": 1,
                "Статус ТТ": "Недостаточно данных",
                "Описание": "меньше двух полных KPI-месяцев, трех RTM-визитов или доступно меньше 60% веса формулы",
            },
            {
                "Порядок": 2,
                "Статус ТТ": "Эталон",
                "Описание": "сложность до 25%, KPI не ниже 95%; отсутствие ОКК не считается ухудшением",
            },
            {
                "Порядок": 3,
                "Статус ТТ": "Контроль",
                "Описание": "сложность от 25% до 50% или текущие показатели ниже целевого уровня",
            },
            {
                "Порядок": 4,
                "Статус ТТ": "Не вина МЕ",
                "Описание": "сложность от 50%, а просадка KPI повторяется минимум при двух разных МЕ",
            },
            {
                "Порядок": 5,
                "Статус ТТ": "Сложная ТТ",
                "Описание": "сложность от 50%, но повторяемость проблемы при разных МЕ пока не подтверждена",
            },
        ]
    )


def _build_rtm_tt_assignment(
    rtm_visits: pd.DataFrame | None,
    teams: pd.DataFrame | None,
) -> pd.DataFrame:
    keys = ["MonthStart", "YearMonth", "ТТ"]
    if rtm_visits is None or rtm_visits.empty:
        return pd.DataFrame(columns=keys)

    visits = rtm_visits.copy()
    visits["MonthStart"] = pd.to_datetime(visits["MonthStart"], errors="coerce")
    visits["YearMonth"] = pd.to_numeric(visits["YearMonth"], errors="coerce").astype("Int64")
    visits["ТТ"] = visits["ТТ"].astype("string").str.strip()
    visits = visits.dropna(subset=[*keys, "Ключ визита RTM"])

    summary = (
        visits.groupby(keys, dropna=False)
        .agg(
            **{
                "Визиты RTM": ("Ключ визита RTM", "nunique"),
                "Сопоставлено визитов RTM": ("ID сотрудника", "count"),
                "Сотрудников RTM на ТТ": ("ID сотрудника", "nunique"),
            }
        )
        .reset_index()
    )
    source_columns = [
        "ID сотрудника",
        "ФИО из логинов",
        "ID супервайзера",
        "Супервайзер",
        "ID территориального менеджера",
        "Территориальный менеджер",
        "Регион BI",
    ]
    if not set(source_columns).issubset(visits.columns):
        return summary

    assigned = visits.copy().rename(
        columns={
            "ФИО из логинов": "Мерчендайзер",
            "Регион BI": "Регион BI команды",
        }
    )
    assigned = assigned.dropna(subset=["Территориальный менеджер"]).copy()
    if assigned.empty:
        return summary
    for column in ["Сеть текущая", "Город текущий"]:
        if column not in assigned.columns:
            assigned[column] = pd.NA

    org_summary = (
        assigned.groupby(keys, dropna=False)
        .agg(
            **{
                "Визиты с активной привязкой RTM": ("Ключ визита RTM", "nunique"),
                "Супервайзеров RTM на ТТ": ("ID супервайзера", "nunique"),
                "ТМ RTM на ТТ": ("Территориальный менеджер", "nunique"),
                "Регион BI текущий RTM": ("Регион BI команды", "first"),
                "Сеть текущая RTM": ("Сеть текущая", "first"),
                "Город текущий RTM": ("Город текущий", "first"),
            }
        )
        .reset_index()
    )
    primary = (
        assigned.groupby(
            keys
            + [
                "ID сотрудника",
                "Мерчендайзер",
                "ID супервайзера",
                "Супервайзер",
                "ID территориального менеджера",
                "Территориальный менеджер",
                "Регион BI команды",
            ],
            dropna=False,
        )["Ключ визита RTM"]
        .nunique()
        .reset_index(name="Визиты основного СВ RTM")
        .sort_values(
            keys + ["Визиты основного СВ RTM", "ID сотрудника"],
            ascending=[True, True, True, False, True],
            na_position="last",
            kind="mergesort",
        )
        .drop_duplicates(keys, keep="first")
        .rename(
            columns={
                "ID сотрудника": "ID мерчендайзера RTM",
                "Мерчендайзер": "Мерчендайзер RTM",
                "ID супервайзера": "ID супервайзера RTM",
                "Супервайзер": "Супервайзер RTM",
                "ID территориального менеджера": "ID территориального менеджера RTM",
                "Территориальный менеджер": "Территориальный менеджер RTM",
                "Регион BI команды": "Регион BI СВ RTM",
            }
        )
    )
    result = summary.merge(org_summary, on=keys, how="left").merge(primary, on=keys, how="left")
    result["Доля визитов основного СВ RTM %"] = (
        result["Визиты основного СВ RTM"]
        / result["Визиты с активной привязкой RTM"].replace(0, np.nan)
    )
    return result


def _attach_kpi_detail_metrics(
    snapshot: pd.DataFrame,
    kpi_metrics: pd.DataFrame,
    rtm_visits: pd.DataFrame | None = None,
    teams: pd.DataFrame | None = None,
) -> pd.DataFrame:
    keys = ["MonthStart", "YearMonth", "ТТ"]
    detail = pivot_tt_kpi_metrics(kpi_metrics)

    source = kpi_metrics.copy()
    source["MonthStart"] = pd.to_datetime(source["MonthStart"], errors="coerce")
    source["YearMonth"] = pd.to_numeric(source["YearMonth"], errors="coerce").astype("Int64")
    source["ТТ"] = source["ТТ"].astype("string").str.strip()
    metadata_columns = [
        column
        for column in ["Регион BI", "Город", "Сеть"]
        if column in source.columns
    ]
    metadata = (
        source.groupby(keys, dropna=False)[metadata_columns]
        .first()
        .reset_index()
        .rename(columns={column: f"{column} source" for column in metadata_columns})
        if metadata_columns
        else source[keys].drop_duplicates()
    )
    kpi_tt = detail.merge(metadata, on=keys, how="left")

    result = snapshot.copy()
    result["MonthStart"] = pd.to_datetime(result["MonthStart"], errors="coerce")
    result["YearMonth"] = pd.to_numeric(result["YearMonth"], errors="coerce").astype("Int64")
    result["ТТ"] = result["ТТ"].astype("string").str.strip()
    result = result.merge(kpi_tt, on=keys, how="left", suffixes=("", " source"))
    for column in KPI_PUBLIC_COLUMNS:
        source_column = f"{column} source"
        if source_column in result.columns:
            if column in result.columns:
                result[column] = result[column].combine_first(result[source_column])
            else:
                result[column] = result[source_column]
    for column in metadata_columns:
        source_column = f"{column} source"
        if source_column in result.columns:
            result[column] = result[column].combine_first(result[source_column])

    existing_keys = result[keys].drop_duplicates()
    kpi_only = kpi_tt.merge(existing_keys, on=keys, how="left", indicator=True)
    kpi_only = kpi_only[kpi_only["_merge"].eq("left_only")].drop(columns="_merge")
    if not kpi_only.empty:
        kpi_only["Ранг"] = pd.NA
        kpi_only["Score ТТ"] = pd.NA
        kpi_only["Визиты"] = 0
        kpi_only["Статус ТТ"] = "Недостаточно данных"
        kpi_only["Тип привязки ТТ"] = "KPI по ТТ; нет данных для расчёта сложности"
        result = pd.concat([result, kpi_only], ignore_index=True, sort=False)

    for column in metadata_columns:
        source_column = f"{column} source"
        if source_column not in result.columns:
            continue
        if column not in result.columns:
            result[column] = result[source_column]
        else:
            result[column] = result[column].replace(r"^\s*$", pd.NA, regex=True)
            result[column] = result[column].combine_first(result[source_column])

    rtm_assignment = _build_rtm_tt_assignment(rtm_visits, teams)
    rtm_columns = [column for column in rtm_assignment.columns if column not in keys]
    if not rtm_assignment.empty:
        result = result.merge(rtm_assignment, on=keys, how="left")
        has_rtm = result["Визиты RTM"].notna()
        restore = has_rtm
        result.loc[restore, "Визиты"] = result.loc[restore, "Визиты RTM"]

        assignment_pairs = [
            ("ID мерчендайзера", "ID мерчендайзера RTM"),
            ("Мерчендайзер", "Мерчендайзер RTM"),
            ("ID супервайзера", "ID супервайзера RTM"),
            ("Супервайзер", "Супервайзер RTM"),
            ("ID территориального менеджера", "ID территориального менеджера RTM"),
            ("Территориальный менеджер", "Территориальный менеджер RTM"),
            ("Регион BI СВ", "Регион BI СВ RTM"),
            ("Визиты с активной привязкой", "Визиты с активной привязкой RTM"),
            ("Визиты основного СВ", "Визиты основного СВ RTM"),
            ("Доля визитов основного СВ %", "Доля визитов основного СВ RTM %"),
            ("Супервайзеров на ТТ", "Супервайзеров RTM на ТТ"),
            ("ТМ на ТТ", "ТМ RTM на ТТ"),
        ]
        for target, source_column in assignment_pairs:
            if source_column not in result.columns:
                continue
            if target not in result.columns:
                result[target] = pd.NA
            result.loc[restore, target] = result.loc[restore, source_column]

        current_region = restore & result["Регион BI текущий RTM"].notna()
        result.loc[current_region, "Регион BI"] = result.loc[
            current_region, "Регион BI текущий RTM"
        ]
        current_chain = restore & result["Сеть текущая RTM"].notna()
        result.loc[current_chain, "Сеть"] = result.loc[current_chain, "Сеть текущая RTM"]
        current_city = restore & result["Город текущий RTM"].notna()
        result.loc[current_city, "Город"] = result.loc[current_city, "Город текущий RTM"]

        mapped_rtm = restore & result["Территориальный менеджер RTM"].notna()
        multi_rtm = mapped_rtm & result["Супервайзеров RTM на ТТ"].gt(1) & result[
            "Доля визитов основного СВ RTM %"
        ].lt(0.60)
        single_tm_rtm = (
            mapped_rtm
            & result["ТМ RTM на ТТ"].eq(1)
            & result["Территориальный менеджер RTM"].notna()
        )
        multiple_tm_rtm = mapped_rtm & result["ТМ RTM на ТТ"].gt(1)
        no_team_rtm = (
            restore
            & ~mapped_rtm
            & result["Сотрудников RTM на ТТ"].fillna(0).gt(0)
        )
        no_employee_rtm = restore & ~mapped_rtm & ~no_team_rtm
        result.loc[restore & ~mapped_rtm, "Тип привязки ТТ"] = (
            "RTM: визиты есть, сотрудник или команда не сопоставлены"
        )
        result.loc[mapped_rtm & ~multi_rtm, "Тип привязки ТТ"] = "Уверенная привязка RTM"
        result.loc[multi_rtm, "Тип привязки ТТ"] = "Несколько СВ / подмена RTM"
        result.loc[mapped_rtm & ~multi_rtm, "Ответственный СВ ТТ"] = result.loc[
            mapped_rtm & ~multi_rtm, "Супервайзер RTM"
        ]
        result.loc[multi_rtm, "Ответственный СВ ТТ"] = "Несколько СВ / подмена"
        result.loc[single_tm_rtm, "Ответственный ТМ ТТ"] = result.loc[
            single_tm_rtm, "Территориальный менеджер RTM"
        ]
        result.loc[multiple_tm_rtm, "Ответственный ТМ ТТ"] = pd.NA
        result.loc[single_tm_rtm, "ТМ территория"] = result.loc[
            single_tm_rtm, "Территориальный менеджер RTM"
        ]
        result.loc[multiple_tm_rtm, "ТМ территория"] = pd.NA
        result.loc[single_tm_rtm, "Метод привязки ТМ"] = (
            "RTM → логины → активный USERS → текущая привязка ТТ"
        )
        result.loc[multiple_tm_rtm, "Метод привязки ТМ"] = (
            "Конфликт в текущей привязке ТТ"
        )
        result.loc[single_tm_rtm, "Доверие привязки ТМ"] = 0.95
        result.loc[multiple_tm_rtm, "Доверие привязки ТМ"] = 0.60
        result.loc[single_tm_rtm, "Статус привязки ТМ"] = "Уверенная"
        result.loc[multiple_tm_rtm, "Статус привязки ТМ"] = "Нужно проверить"

        result.loc[no_team_rtm, "Метод привязки ТМ"] = "RTM: нет подтверждённой KPI-иерархии"
        result.loc[no_employee_rtm, "Метод привязки ТМ"] = "RTM: логин не сопоставлен"
        result.loc[no_team_rtm | no_employee_rtm, "Доверие привязки ТМ"] = 0.0
        result.loc[no_team_rtm | no_employee_rtm, "Статус привязки ТМ"] = "Нет привязки"

    tm_text_columns = [
        "ТМ территория",
        "Метод привязки ТМ",
        "Статус привязки ТМ",
    ]
    for column in tm_text_columns:
        if column not in result.columns:
            result[column] = pd.NA
        result[column] = result[column].replace(r"^\s*$", pd.NA, regex=True)

    has_rtm_visits = pd.to_numeric(result["Визиты"], errors="coerce").fillna(0).gt(0)
    has_okk_check = (
        pd.to_numeric(result["ОКК %"], errors="coerce").notna()
        if "ОКК %" in result.columns
        else pd.Series(False, index=result.index)
    )
    result = result[has_rtm_visits | has_okk_check].copy()

    result["Метод привязки ТМ"] = result["Метод привязки ТМ"].fillna(
        "Нет подтверждённой привязки"
    )
    result["Статус привязки ТМ"] = result["Статус привязки ТМ"].fillna("Нет привязки")
    if "Доверие привязки ТМ" not in result.columns:
        result["Доверие привязки ТМ"] = 0.0
    result["Доверие привязки ТМ"] = pd.to_numeric(
        result["Доверие привязки ТМ"], errors="coerce"
    ).fillna(0.0)
    for column in [
        "Ответственный ТМ ТТ",
        "Территориальный менеджер",
    ]:
        if column not in result.columns:
            result[column] = pd.NA
        result[column] = result[column].replace(r"^\s*$", pd.NA, regex=True).fillna(
            result["ТМ территория"]
        )

    if "Доля визитов основного СВ %" not in result.columns:
        result["Доля визитов основного СВ %"] = np.nan
    result["Доля визитов основного СВ %"] = pd.to_numeric(
        result["Доля визитов основного СВ %"],
        errors="coerce",
    )
    result = result.drop(
        columns=[
            *[f"{column} source" for column in KPI_PUBLIC_COLUMNS],
            *[f"{column} source" for column in metadata_columns],
            *rtm_columns,
        ],
        errors="ignore",
    )
    result = result[[column for column in PAGE4_POWERBI_COLUMNS if column in result.columns]].copy()
    return result.sort_values(["MonthStart", "Ранг", "ТТ"], na_position="last", kind="mergesort").reset_index(drop=True)


def build_page4_tt_data() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    settings = load_settings()
    out_dir = Path(settings["paths"]["out"])

    okk = pd.read_parquet(out_dir / "okk_fact.parquet")
    kpi = pd.read_parquet(out_dir / "kpi_fact.parquet")
    teams = pd.read_parquet(out_dir / "dim_teams.parquet")
    kpi_tt_path = out_dir / "kpi_client_tt_fact.parquet"
    kpi_tt_direct = pd.read_parquet(kpi_tt_path) if kpi_tt_path.exists() else pd.DataFrame()
    rtm_visits_path = out_dir / "rtm_employee_visits.parquet"
    rtm_visits = pd.read_parquet(rtm_visits_path) if rtm_visits_path.exists() else pd.DataFrame()

    snapshot = _build_tt_complexity_snapshot(
        okk,
        kpi,
        kpi_tt_direct=kpi_tt_direct,
        teams=teams,
        rtm_visits=rtm_visits,
    )
    if not kpi_tt_direct.empty:
        snapshot = _attach_kpi_detail_metrics(
            snapshot,
            kpi_tt_direct,
            rtm_visits=rtm_visits,
            teams=teams,
        )
    formula = _build_formula_table()
    legend = _build_status_legend_table()

    save_parquet(
        snapshot,
        str(out_dir / "page4_tt_monthly_snapshot.parquet"),
        exclude_columns={"YearMonth", "Группа региона"},
    )
    save_parquet(formula, str(out_dir / "page4_tt_formula.parquet"))
    save_parquet(legend, str(out_dir / "page4_tt_status_legend.parquet"))

    print(f"\n  Page4 TT snapshot: {len(snapshot)} строк")
    print(f"  Page4 TT formula: {len(formula)} строк")
    print(f"  Page4 TT status legend: {len(legend)} строк")
    return snapshot, formula, legend


if __name__ == "__main__":
    build_page4_tt_data()
