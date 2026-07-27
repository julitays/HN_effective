import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

if __package__ is None or __package__ == "":
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from scripts.utils import load_settings, save_parquet
from scripts.staffing_utils import _mode_or_first


TARGET_KPI = 0.75
TARGET_OKK = 0.60
TARGET_OSA = 0.85
TARGET_PICOS = 0.85
TARGET_FRAUD = 0.10

MIN_HISTORY_MONTHS = 2
MIN_HISTORY_VISITS_TOTAL = 3

ETALON_COMPLEXITY_MAX = 0.35
COMPLEX_TT_MIN = 0.65
ETALON_KPI_MIN = 0.80
NON_ME_COMPLEXITY_MIN = 18

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
    "Метод привязки ТМ",
    "Доверие привязки ТМ",
    "Статус привязки ТМ",
    "Ответственный СВ ТТ",
    "Тип привязки ТТ",
    "Визиты",
    "KPI проекта %",
    "ОКК %",
    "PICOS %",
    "OSA %",
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


def _range_score(series: pd.Series, scale: float) -> float:
    clean = series.dropna()
    if len(clean) <= 1:
        return 0.0
    value_range = float(clean.max() - clean.min())
    return max(0.0, min(1.0, value_range / scale))


def _build_tt_kpi_from_merch(okk: pd.DataFrame, kpi: pd.DataFrame) -> pd.DataFrame:
    tt_merch = (
        okk.dropna(subset=["MonthStart", "Код ТТ", "ID мерчендайзера"])
        .groupby(["MonthStart", "YearMonth", "Код ТТ", "ID мерчендайзера"], dropna=False)
        .agg(**{"Визиты МЕ на ТТ": ("Дата визита", "count")})
        .reset_index()
    )

    kpi_merch = (
        kpi.dropna(subset=["MonthStart", "ID мерчендайзера"])
        .groupby(["MonthStart", "ID мерчендайзера"], dropna=False)
        .agg(**{"KPI проекта %": ("KPI 1", "mean")})
        .reset_index()
    )

    tt_merch = tt_merch.merge(
        kpi_merch,
        on=["MonthStart", "ID мерчендайзера"],
        how="left",
    )

    tt_merch["Взвешенный KPI вклад"] = tt_merch["Визиты МЕ на ТТ"] * tt_merch["KPI проекта %"]
    tt_merch["Визиты с KPI"] = tt_merch["Визиты МЕ на ТТ"].where(tt_merch["KPI проекта %"].notna(), 0)

    tt_kpi = (
        tt_merch.groupby(["MonthStart", "YearMonth", "Код ТТ"], dropna=False)
        .agg(
            **{
                "Взвешенный KPI вклад": ("Взвешенный KPI вклад", "sum"),
                "Визиты с KPI": ("Визиты с KPI", "sum"),
            }
        )
        .reset_index()
    )
    tt_kpi["KPI проекта %"] = tt_kpi["Взвешенный KPI вклад"] / tt_kpi["Визиты с KPI"]
    tt_kpi.loc[tt_kpi["Визиты с KPI"] == 0, "KPI проекта %"] = pd.NA

    return tt_kpi[["MonthStart", "YearMonth", "Код ТТ", "KPI проекта %"]]


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
                "AGENCY SV": (route_col, _mode_or_first),
                "Группа продаж KPI": ("Группа продаж", _mode_or_first) if "Группа продаж" in tt_agency.columns else (route_col, lambda s: pd.NA),
                "Регион BI KPI": ("Регион BI", _mode_or_first),
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
    team_dir["ID территориального менеджера"] = team_dir["ID территориального менеджера"].fillna("NO_TM")
    team_dir["Территориальный менеджер"] = team_dir["Территориальный менеджер"].fillna("Вакансия / нет ТМ")

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
                "ID ТМ raw": ("ID территориального менеджера", _mode_or_first),
                "ТМ raw": ("Территориальный менеджер", _mode_or_first),
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
    team_dir["ID территориального менеджера"] = team_dir["ID территориального менеджера"].fillna("NO_TM")
    team_dir["Территориальный менеджер"] = team_dir["Территориальный менеджер"].fillna("Вакансия / нет ТМ")

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

    team_dir["ID территориального менеджера"] = team_dir["ID территориального менеджера"].fillna("NO_TM")
    team_dir["Территориальный менеджер"] = team_dir["Территориальный менеджер"].fillna("Вакансия / нет ТМ")
    team_dir["Город нормализованный"] = team_dir["Город мерчендайзера"].map(_normalize_city_for_tm)

    city_map = (
        team_dir.dropna(subset=["Город нормализованный"])
        .groupby(["Регион BI", "Город нормализованный"], dropna=False)
        .agg(
            **{
                "ID ТМ города USERS": ("ID территориального менеджера", _mode_or_first),
                "ТМ города USERS": ("Территориальный менеджер", _mode_or_first),
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
                "ID ТМ региона USERS": ("ID территориального менеджера", _mode_or_first),
                "ТМ региона USERS": ("Территориальный менеджер", _mode_or_first),
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

    city_unique = result["ТМ города кол-во"].fillna(0).eq(1)
    agency_unique = (
        result["Тип привязки ТМ AGENCY SV"].eq("Уверенная TM-привязка")
        & result["Регион BI KPI"].notna()
        & result["Регион BI"].eq(result["Регион BI KPI"])
    )
    region_unique = result["ТМ региона кол-во"].fillna(0).eq(1)
    agency_multi = result["ТМ AGENCY SV кол-во"].fillna(0).gt(1) | result["AGENCY SV вариантов на ТТ"].fillna(0).gt(1)
    city_multi = result["ТМ города кол-во"].fillna(0).gt(1)
    region_multi = result["ТМ региона кол-во"].fillna(0).gt(1)
    city_agency_conflict = (
        city_unique
        & agency_unique
        & result["ID ТМ города USERS"].notna()
        & result["ID ТМ по AGENCY SV"].notna()
        & result["ID ТМ города USERS"].ne(result["ID ТМ по AGENCY SV"])
    )

    result["ID ТМ территория"] = np.select(
        [
            city_unique,
            ~city_unique & agency_unique,
            ~city_unique & ~agency_unique & region_unique,
        ],
        [
            result["ID ТМ города USERS"],
            result["ID ТМ по AGENCY SV"],
            result["ID ТМ региона USERS"],
        ],
        default="NO_RELIABLE_TM",
    )
    result["ТМ территория"] = np.select(
        [
            city_unique,
            ~city_unique & agency_unique,
            ~city_unique & ~agency_unique & region_unique,
        ],
        [
            result["ТМ города USERS"],
            result["ТМ по AGENCY SV"],
            result["ТМ региона USERS"],
        ],
        default="Нет надежной привязки ТМ",
    )
    result["Метод привязки ТМ"] = np.select(
        [
            city_agency_conflict,
            city_unique,
            ~city_unique & agency_unique,
            ~city_unique & ~agency_unique & region_unique,
            city_multi,
            ~city_unique & agency_multi,
            ~city_unique & ~agency_unique & region_multi,
        ],
        [
            "Конфликт: город USERS / AGENCY SV",
            "Город ТТ из USERS",
            "AGENCY SV из KPI + USERS",
            "Регион из USERS",
            "Неоднозначно: город",
            "Неоднозначно: AGENCY SV",
            "Неоднозначно: регион",
        ],
        default="Нет надежной привязки",
    )
    result["Доверие привязки ТМ"] = np.select(
        [
            city_agency_conflict,
            city_unique,
            ~city_unique & agency_unique,
            ~city_unique & ~agency_unique & region_unique,
            city_multi,
            ~city_unique & agency_multi,
            ~city_unique & ~agency_unique & region_multi,
        ],
        [0.60, 0.95, 0.85, 0.70, 0.40, 0.35, 0.20],
        default=0.0,
    )
    result["Статус привязки ТМ"] = np.select(
        [
            result["Доверие привязки ТМ"].ge(0.80),
            result["Доверие привязки ТМ"].ge(0.70),
            result["Доверие привязки ТМ"].gt(0),
        ],
        ["Уверенная", "Допустимая", "Нужно проверить"],
        default="Нет привязки",
    )
    result["Конфликт ТМ город/AGENCY"] = city_agency_conflict

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
    base = (
        okk.groupby(["MonthStart", "YearMonth", "Код ТТ"], dropna=False)
        .agg(
            **{
                "Регион BI": ("Регион BI", "first"),
                "Сеть": ("Сеть", "first"),
                "Адрес": ("Адрес", "first"),
                "Визиты": ("Дата визита", "count"),
                "ОКК %": ("Качество визита", "mean"),
                "OSA %": ("% наличия товара на полке", "mean"),
                "PICOS %": ("% наличия PICoS", "mean"),
                "Фрод %": ("Флаг фальсификации", "mean"),
                "Фрод кол-во": ("Флаг фальсификации", lambda s: s.fillna(False).eq(True).sum()),
                "МЕ на ТТ": ("ID мерчендайзера", lambda s: s.dropna().nunique()),
            }
        )
        .reset_index()
    )
    base["Город"] = base["Адрес"].map(_extract_city)
    if kpi_tt_direct is not None and not kpi_tt_direct.empty:
        tt_kpi = kpi_tt_direct.rename(columns={"ТТ": "Код ТТ", "KPI 1": "KPI проекта %"})[
            ["MonthStart", "YearMonth", "Код ТТ", "KPI проекта %"]
        ].copy()
    else:
        tt_kpi = _build_tt_kpi_from_merch(okk, kpi)
    base = base.merge(tt_kpi, on=["MonthStart", "YearMonth", "Код ТТ"], how="left")
    base["Просадка KPI проекта"] = base["KPI проекта %"] < TARGET_KPI
    base["Нарушение OKK"] = base["ОКК %"] < TARGET_OKK
    return base


def _build_tt_complexity_snapshot(
    okk: pd.DataFrame,
    kpi: pd.DataFrame,
    kpi_tt_direct: pd.DataFrame | None = None,
    teams: pd.DataFrame | None = None,
) -> pd.DataFrame:
    tt_base = _build_tt_monthly_base(okk, kpi, kpi_tt_direct=kpi_tt_direct)
    rows: list[dict] = []

    for tt_code, tt_df in tt_base.groupby("Код ТТ", dropna=False):
        tt_history = tt_df.sort_values("MonthStart").reset_index(drop=True)
        tt_visits = okk[okk["Код ТТ"] == tt_code].copy()

        for idx, row in tt_history.iterrows():
            history = tt_history.iloc[max(0, idx - 2): idx + 1].copy()
            history_months = history["MonthStart"].dropna().unique().tolist()
            history_visits = tt_visits[tt_visits["MonthStart"].isin(history_months)].copy()
            history_months_count = len(history["MonthStart"].dropna().unique())
            history_visits_count = len(history_visits)

            unique_merch = history_visits["ID мерчендайзера"].dropna().nunique()
            merch_factor = 1.0 if unique_merch > 1 else 0.5
            kpi_drop_mean = history["Просадка KPI проекта"].mean()
            c_kpi_repeat = float(kpi_drop_mean) * merch_factor * 35 if pd.notna(kpi_drop_mean) else 0.0

            osa_instability = _range_score(history["OSA %"], 0.25)
            picos_instability = _range_score(history["PICOS %"], 0.25)
            instability_parts = [value for value in [osa_instability, picos_instability] if pd.notna(value)]
            c_instability = (sum(instability_parts) / len(instability_parts)) * 25 if instability_parts else 0.0

            c_okk_repeat = float(history["Нарушение OKK"].mean()) * 20

            peer_mask = (
                (tt_base["MonthStart"] == row["MonthStart"])
                & (tt_base["Сеть"] == row["Сеть"])
                & (tt_base["Город"] == row["Город"])
                & (tt_base["Код ТТ"] != tt_code)
            )
            peers = tt_base[peer_mask]
            if not peers.empty and pd.notna(row["KPI проекта %"]):
                peer_kpi = peers["KPI проекта %"].dropna().mean()
                c_peer = max(0.0, min(1.0, (peer_kpi - row["KPI проекта %"]) / 0.20)) * 10 if pd.notna(peer_kpi) else 0.0
            else:
                c_peer = 0.0

            c_me_change = min(1.0, max(0.0, (unique_merch - 1) / 2)) * 10

            complexity_score = c_kpi_repeat + c_instability + c_okk_repeat + c_peer + c_me_change
            complexity_share = max(0.0, min(1.0, complexity_score / 100))

            score_tt = max(
                0.0,
                min(
                    100.0,
                    (
                        (row["KPI проекта %"] if pd.notna(row["KPI проекта %"]) else 0.0) * 55
                        + (1 - complexity_share) * 45
                    ),
                ),
            )

            dominant_non_me = c_peer + c_me_change
            dominant_me = c_kpi_repeat + c_instability + c_okk_repeat

            insufficient_data = (
                history_months_count < MIN_HISTORY_MONTHS
                or history_visits_count < MIN_HISTORY_VISITS_TOTAL
            )

            if insufficient_data:
                status = "Недостаточно данных"
                complexity_share_output = np.nan
            elif (
                complexity_share <= ETALON_COMPLEXITY_MAX
                and pd.notna(row["KPI проекта %"])
                and row["KPI проекта %"] >= ETALON_KPI_MIN
                and pd.notna(row["ОКК %"])
                and row["ОКК %"] >= TARGET_OKK
            ):
                status = "Эталон"
                complexity_share_output = complexity_share
            elif complexity_share >= COMPLEX_TT_MIN:
                if dominant_non_me >= NON_ME_COMPLEXITY_MIN:
                    status = "Не вина МЕ"
                else:
                    status = "Сложная ТТ"
                complexity_share_output = complexity_share
            else:
                status = "Контроль"
                complexity_share_output = complexity_share

            rows.append(
                {
                    "MonthStart": row["MonthStart"],
                    "YearMonth": row["YearMonth"],
                    "ТТ": str(tt_code) if pd.notna(tt_code) else None,
                    "Регион BI": row["Регион BI"],
                    "Город": row["Город"],
                    "Сеть": row["Сеть"],
                    "Визиты": int(row["Визиты"]) if pd.notna(row["Визиты"]) else 0,
                    "KPI проекта %": row["KPI проекта %"],
                    "ОКК %": row["ОКК %"],
                    "PICOS %": row["PICOS %"],
                    "OSA %": row["OSA %"],
                    "Фрод %": row["Фрод %"],
                    "Фрод кол-во": row["Фрод кол-во"],
                    "Сложность %": complexity_share_output,
                    "Score ТТ": round(score_tt, 0),
                    "Статус ТТ": status,
                    "Сложность KPI повтор %": round(c_kpi_repeat / 100, 4),
                    "Сложность OSA/PICOS %": round(c_instability / 100, 4),
                    "Сложность OKK %": round(c_okk_repeat / 100, 4),
                    "Сложность похожие ТТ %": round(c_peer / 100, 4),
                    "Сложность смена МЕ %": round(c_me_change / 100, 4),
                }
            )

    snapshot = pd.DataFrame(rows)
    if snapshot.empty:
        return snapshot

    tt_assignment = _build_tt_org_assignment(okk, teams)
    if not tt_assignment.empty:
        snapshot = snapshot.merge(
            tt_assignment,
            on=["MonthStart", "YearMonth", "ТТ"],
            how="left",
        )
    else:
        for column in [
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
        ]:
            snapshot[column] = pd.NA
    snapshot["ID супервайзера"] = snapshot["ID супервайзера"].fillna("NO_ACTIVE_SV")
    snapshot["Супервайзер"] = snapshot["Супервайзер"].fillna("Нет активной привязки в USERS")
    snapshot["ID территориального менеджера"] = snapshot["ID территориального менеджера"].fillna("NO_ACTIVE_TM")
    snapshot["Территориальный менеджер"] = snapshot["Территориальный менеджер"].fillna("Нет активной привязки в USERS")
    snapshot["Регион BI СВ"] = snapshot["Регион BI СВ"].fillna("Нет активной привязки в USERS")
    snapshot["СВ визита"] = snapshot["Супервайзер"]
    snapshot["ТМ визита"] = snapshot["Территориальный менеджер"]
    snapshot["Тип привязки ТТ"] = snapshot.apply(_build_assignment_type, axis=1)
    snapshot["Ответственный СВ ТТ"] = snapshot.apply(_responsible_supervisor, axis=1)
    snapshot["Ответственный ТМ ТТ"] = snapshot.apply(_responsible_tm, axis=1)

    agency_assignment = _build_tt_agency_tm_assignment(kpi_tt_direct, kpi, teams)
    if not agency_assignment.empty:
        snapshot = snapshot.merge(
            agency_assignment,
            on=["MonthStart", "YearMonth", "ТТ"],
            how="left",
        )
    else:
        for column in [
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
        ]:
            snapshot[column] = pd.NA
    snapshot["ID ТМ по AGENCY SV"] = snapshot["ID ТМ по AGENCY SV"].fillna("NO_AGENCY_TM")
    snapshot["ТМ по AGENCY SV"] = snapshot["ТМ по AGENCY SV"].fillna("Нет привязки по AGENCY SV")
    snapshot["Тип привязки ТМ AGENCY SV"] = snapshot["Тип привязки ТМ AGENCY SV"].fillna("Нет совпадения AGENCY SV")

    snapshot = _attach_tm_territory_assignment(snapshot, teams)

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
            ["Сортировка статуса", "Score ТТ", "KPI проекта %", "ОКК %", "Визиты"],
            ascending=[True, False, False, False, False],
            na_position="last",
        ).copy()
        actionable = ranked[ranked["Статус ТТ"] != "Недостаточно данных"].copy()
        actionable["Ранг"] = actionable["Score ТТ"].rank(method="dense", ascending=False).astype("Int64")
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
            {"Порядок": 1, "Вес": 0.35, "Формула": "35%", "Описание": "повторяемая просадка KPI проекта на ТТ при разных МЕ"},
            {"Порядок": 2, "Вес": 0.25, "Формула": "25%", "Описание": "нестабильность OSA/PICOS по истории"},
            {"Порядок": 3, "Вес": 0.20, "Формула": "20%", "Описание": "повторяемость OKK-нарушений на ТТ"},
            {"Порядок": 4, "Вес": 0.10, "Формула": "10%", "Описание": "отклонение от похожих ТТ сети/города"},
            {"Порядок": 5, "Вес": 0.10, "Формула": "10%", "Описание": "частая смена МЕ / нестабильность маршрута"},
        ]
    )


def _build_status_legend_table() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Порядок": 1,
                "Статус ТТ": "Недостаточно данных",
                "Описание": "по ТТ пока мало истории или слишком мало визитов, чтобы корректно считать сложность",
            },
            {
                "Порядок": 2,
                "Статус ТТ": "Эталон",
                "Описание": "точка стабильная, показатели сильные, история не показывает системной сложности",
            },
            {
                "Порядок": 3,
                "Статус ТТ": "Контроль",
                "Описание": "точка требует наблюдения, но подтвержденной высокой сложности по истории нет",
            },
            {
                "Порядок": 4,
                "Статус ТТ": "Не вина МЕ",
                "Описание": "сложность высокая, но главный вклад дают условия точки, похожие ТТ или частая смена МЕ",
            },
            {
                "Порядок": 5,
                "Статус ТТ": "Сложная ТТ",
                "Описание": "по истории подтверждается высокая сложность точки и повторяемость проблем",
            },
        ]
    )


def build_page4_tt_data() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    settings = load_settings()
    out_dir = Path(settings["paths"]["out"])

    okk = pd.read_parquet(out_dir / "okk_fact.parquet")
    kpi = pd.read_parquet(out_dir / "kpi_fact.parquet")
    teams = pd.read_parquet(out_dir / "dim_teams.parquet")
    kpi_tt_path = out_dir / "kpi_client_tt_fact.parquet"
    kpi_tt_direct = pd.read_parquet(kpi_tt_path) if kpi_tt_path.exists() else pd.DataFrame()

    snapshot = _build_tt_complexity_snapshot(okk, kpi, kpi_tt_direct=kpi_tt_direct, teams=teams)
    formula = _build_formula_table()
    legend = _build_status_legend_table()

    save_parquet(snapshot, str(out_dir / "page4_tt_monthly_snapshot.parquet"))
    save_parquet(formula, str(out_dir / "page4_tt_formula.parquet"))
    save_parquet(legend, str(out_dir / "page4_tt_status_legend.parquet"))

    print(f"\n  Page4 TT snapshot: {len(snapshot)} строк")
    print(f"  Page4 TT formula: {len(formula)} строк")
    print(f"  Page4 TT status legend: {len(legend)} строк")
    return snapshot, formula, legend


if __name__ == "__main__":
    build_page4_tt_data()
