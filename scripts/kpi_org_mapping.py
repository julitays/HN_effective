from __future__ import annotations

from pathlib import Path

import pandas as pd

from scripts.cache_utils import load_or_build_parquet_cache, source_set_digest
from scripts.staffing_utils import normalize_name
from scripts.utils import canonical_region_from_text, load_region_map


CURRENT_TT_SOURCE = "Текущая привязка ТТ → ТМ"
CURRENT_SV_SOURCE = "Текущая привязка СВ → ТМ"
CURRENT_USERS_SOURCE = "Активный USERS по сотруднику"
UNRESOLVED_SOURCE = "Нет ТМ в текущей привязке"


def _clean_text(series: pd.Series) -> pd.Series:
    return (
        series.astype("string")
        .str.replace("\xa0", " ", regex=False)
        .str.replace(r"\s+", " ", regex=True)
        .str.strip()
        .replace({"": pd.NA, "nan": pd.NA, "None": pd.NA, "<NA>": pd.NA})
    )


def _code_key(series: pd.Series) -> pd.Series:
    return _clean_text(series).str.upper().str.replace(r"\.0+$", "", regex=True)


def _grouped_mode(
    frame: pd.DataFrame,
    keys: list[str],
    value_column: str,
) -> pd.DataFrame:
    values = frame[keys + [value_column]].dropna(subset=[value_column]).copy()
    if values.empty:
        return pd.DataFrame(columns=keys + [value_column])
    counts = (
        values.groupby(keys + [value_column], dropna=False, sort=False)
        .size()
        .rename("_count")
        .reset_index()
    )
    counts["_value_sort"] = counts[value_column].astype("string")
    return (
        counts.sort_values(
            ["_count", "_value_sort"],
            ascending=[False, True],
            kind="stable",
        )
        .drop_duplicates(keys, keep="first")
        [keys + [value_column]]
    )


def _column(frame: pd.DataFrame, *aliases: str) -> str | None:
    normalized = {str(column).strip().casefold(): column for column in frame.columns}
    for alias in aliases:
        found = normalized.get(alias.strip().casefold())
        if found is not None:
            return found
    return None


def _active_employee_dimension(dim_employees: pd.DataFrame) -> pd.DataFrame:
    if dim_employees is None or dim_employees.empty:
        return pd.DataFrame()
    work = dim_employees.copy()
    active_column = _column(work, "Активен", "is_active")
    if active_column is not None:
        work = work[work[active_column].fillna(False).eq(True)].copy()
    return work


def _employee_columns(dim_employees: pd.DataFrame) -> tuple[str | None, str | None, str | None]:
    return (
        _column(dim_employees, "ID сотрудника", "employee_id"),
        _column(dim_employees, "ФИО", "full_name"),
        _column(dim_employees, "Атрибут", "attribute"),
    )


def _unique_supervisor_route_lookup(
    dim_employees: pd.DataFrame,
) -> pd.DataFrame:
    output_columns = [
        "Код СВ текущий",
        "ID супервайзера текущий",
        "Супервайзер текущий",
        "USERS ID ТМ текущего СВ",
        "USERS ТМ текущего СВ",
    ]
    active = _active_employee_dimension(dim_employees)
    if active.empty:
        return pd.DataFrame(columns=output_columns)
    id_column, name_column, route_column = _employee_columns(active)
    manager_column = _column(active, "ID руководителя", "manager_id")
    if id_column is None or name_column is None or route_column is None or manager_column is None:
        return pd.DataFrame(columns=output_columns)

    work = active[[id_column, name_column, route_column, manager_column]].dropna(
        subset=[id_column, route_column]
    ).copy()
    work["ID"] = _clean_text(work[id_column])
    work["Маршрут"] = _code_key(work[route_column])
    work["ID руководителя"] = _clean_text(work[manager_column])
    work = work[work["ID"].notna() & work["Маршрут"].notna()]
    counts = work.groupby("Маршрут")["ID"].nunique()
    unique_routes = counts[counts.eq(1)].index
    work = work[work["Маршрут"].isin(unique_routes)].drop_duplicates("Маршрут", keep="last")
    id_to_name = (
        active[[id_column, name_column]]
        .dropna(subset=[id_column])
        .drop_duplicates(id_column, keep="last")
        .set_index(id_column)[name_column]
        .to_dict()
    )
    work["ФИО руководителя"] = work["ID руководителя"].map(id_to_name)
    return work[
        ["Маршрут", "ID", name_column, "ID руководителя", "ФИО руководителя"]
    ].rename(
        columns={
            "Маршрут": "Код СВ текущий",
            "ID": "ID супервайзера текущий",
            name_column: "Супервайзер текущий",
            "ID руководителя": "USERS ID ТМ текущего СВ",
            "ФИО руководителя": "USERS ТМ текущего СВ",
        }
    )


def load_current_tm_assignments(
    assignment_root: Path,
    dim_employees: pd.DataFrame,
    cache_root: Path | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    sources = sorted(assignment_root.glob("*.xlsx"))
    if not sources:
        raise FileNotFoundError(
            f"В {assignment_root} нет файла текущей привязки с полями Ship To и TM Name"
        )

    def build_raw_map() -> pd.DataFrame:
        frames: list[pd.DataFrame] = []
        for source in sources:
            with pd.ExcelFile(source) as workbook:
                for sheet in workbook.sheet_names:
                    raw = workbook.parse(sheet, dtype="string")
                    tt_column = _column(raw, "Ship To")
                    tm_column = _column(raw, "TM Name")
                    if tt_column is None or tm_column is None:
                        continue

                    sv_column = _column(raw, "SV Name")
                    region_column = _column(raw, "Cluster BU")
                    city_column = _column(raw, "Cluster SG")
                    chain_column = _column(raw, "Chain")
                    agency_column = _column(raw, "Агентство")
                    order_type_column = _column(raw, "Order Type")

                    part = pd.DataFrame(index=raw.index)
                    part["ТТ"] = _code_key(raw[tt_column])
                    part["ТМ источник"] = _clean_text(raw[tm_column])
                    part["Код СВ текущий"] = _code_key(raw[sv_column]) if sv_column else pd.NA
                    part["Регион источник"] = _clean_text(raw[region_column]) if region_column else pd.NA
                    part["Город текущий"] = _clean_text(raw[city_column]) if city_column else pd.NA
                    part["Сеть текущая"] = _clean_text(raw[chain_column]) if chain_column else pd.NA
                    part["Агентство"] = _clean_text(raw[agency_column]) if agency_column else pd.NA
                    part["Order Type"] = _clean_text(raw[order_type_column]) if order_type_column else pd.NA
                    part["Файл текущей привязки"] = source.name
                    part["Лист текущей привязки"] = sheet
                    frames.append(part[part["ТТ"].notna()].copy())
        if not frames:
            raise FileNotFoundError(
                f"В {assignment_root} нет файла текущей привязки с полями Ship To и TM Name"
            )
        return pd.concat(frames, ignore_index=True).drop_duplicates()

    cache_key = source_set_digest(sources, assignment_root, "current-tm-v1")
    raw_map, cache_hit = load_or_build_parquet_cache(cache_root, cache_key, build_raw_map)
    print(f"  Привязки ТМ: {'кеш parquet' if cache_hit else 'прочитаны Excel'}")
    agency = _clean_text(raw_map["Агентство"])
    raw_map = raw_map[agency.isna() | agency.str.upper().eq("OPEN")].copy()

    tm_conflicts = raw_map.groupby("ТТ")["ТМ источник"].nunique(dropna=True)
    conflicting_tt = set(tm_conflicts[tm_conflicts.gt(1)].index)
    raw_map = raw_map[~raw_map["ТТ"].isin(conflicting_tt)].copy()
    raw_map = raw_map.drop_duplicates("ТТ", keep="last")

    known_sv_tm = raw_map.dropna(subset=["Код СВ текущий", "ТМ источник"]).copy()
    sv_tm_counts = known_sv_tm.groupby("Код СВ текущий")["ТМ источник"].nunique()
    unique_sv = set(sv_tm_counts[sv_tm_counts.eq(1)].index)
    sv_tm_lookup = (
        known_sv_tm[known_sv_tm["Код СВ текущий"].isin(unique_sv)]
        .drop_duplicates("Код СВ текущий", keep="last")
        .set_index("Код СВ текущий")["ТМ источник"]
        .to_dict()
    )

    raw_map["Источник привязки ТМ"] = CURRENT_TT_SOURCE
    restore_by_sv = raw_map["ТМ источник"].isna() & raw_map["Код СВ текущий"].isin(unique_sv)
    raw_map.loc[restore_by_sv, "ТМ источник"] = raw_map.loc[
        restore_by_sv, "Код СВ текущий"
    ].map(sv_tm_lookup)
    raw_map.loc[restore_by_sv, "Источник привязки ТМ"] = CURRENT_SV_SOURCE
    raw_map.loc[raw_map["ТМ источник"].isna(), "Источник привязки ТМ"] = UNRESOLVED_SOURCE

    route_lookup = _unique_supervisor_route_lookup(dim_employees)
    raw_map = raw_map.merge(route_lookup, on="Код СВ текущий", how="left")
    source_tm_key = raw_map["ТМ источник"].map(normalize_name)
    users_tm_key = raw_map["USERS ТМ текущего СВ"].map(normalize_name)
    source_tm_short = source_tm_key.map(
        lambda value: " ".join(str(value).split()[:2]) if value else ""
    )
    users_tm_short = users_tm_key.map(
        lambda value: " ".join(str(value).split()[:2]) if value else ""
    )
    confirmed_tm_identity = (
        raw_map["ТМ источник"].notna()
        & raw_map["USERS ID ТМ текущего СВ"].notna()
        & source_tm_short.ne("")
        & source_tm_short.eq(users_tm_short)
    )
    raw_map["ТМ источник key"] = source_tm_key
    raw_map["ТМ источник short"] = source_tm_short

    active_people = _active_employee_dimension(dim_employees)
    active_id_column, active_name_column, _ = _employee_columns(active_people)
    active_position_column = _column(active_people, "Должность", "position")
    active_tm_lookup = pd.DataFrame()
    if (
        active_id_column is not None
        and active_name_column is not None
        and active_position_column is not None
    ):
        active_tm_lookup = active_people[
            [active_id_column, active_name_column, active_position_column]
        ].copy()
        active_positions = (
            _clean_text(active_tm_lookup[active_position_column])
            .str.casefold()
            .isin({"tm", "тм", "rm", "территориальный менеджер"})
        )
        active_tm_lookup = active_tm_lookup[active_positions].copy()
        active_tm_lookup["ТМ active short"] = active_tm_lookup[active_name_column].map(
            normalize_name
        ).map(lambda value: " ".join(str(value).split()[:2]) if value else "")
        active_counts = active_tm_lookup.groupby("ТМ active short")[
            active_id_column
        ].nunique()
        active_unique_keys = set(active_counts[active_counts.eq(1)].index)
        active_tm_lookup = (
            active_tm_lookup[
                active_tm_lookup["ТМ active short"].isin(active_unique_keys)
            ]
            .drop_duplicates("ТМ active short", keep="last")
            .set_index("ТМ active short")
        )

    confirmed_rows = raw_map.loc[
        confirmed_tm_identity,
        ["ТМ источник key", "USERS ID ТМ текущего СВ", "USERS ТМ текущего СВ"],
    ].copy()
    confirmed_counts = confirmed_rows.groupby("ТМ источник key")[
        "USERS ID ТМ текущего СВ"
    ].nunique()
    confirmed_keys = set(confirmed_counts[confirmed_counts.eq(1)].index)
    confirmed_lookup = (
        confirmed_rows[confirmed_rows["ТМ источник key"].isin(confirmed_keys)]
        .drop_duplicates("ТМ источник key", keep="last")
        .set_index("ТМ источник key")
    )
    direct_active_identity = (
        raw_map["ТМ источник short"].isin(active_tm_lookup.index)
        if not active_tm_lookup.empty
        else pd.Series(False, index=raw_map.index)
    )
    verified_tm_identity = direct_active_identity | raw_map["ТМ источник key"].isin(
        confirmed_keys
    )
    raw_map["ID территориального менеджера"] = pd.NA
    raw_map["Территориальный менеджер"] = pd.NA
    if direct_active_identity.any():
        raw_map.loc[
            direct_active_identity, "ID территориального менеджера"
        ] = raw_map.loc[direct_active_identity, "ТМ источник short"].map(
            active_tm_lookup[active_id_column]
        )
        raw_map.loc[direct_active_identity, "Территориальный менеджер"] = raw_map.loc[
            direct_active_identity, "ТМ источник short"
        ].map(active_tm_lookup[active_name_column])
    route_only_identity = verified_tm_identity & ~direct_active_identity
    raw_map.loc[route_only_identity, "ID территориального менеджера"] = raw_map.loc[
        route_only_identity, "ТМ источник key"
    ].map(confirmed_lookup["USERS ID ТМ текущего СВ"])
    raw_map.loc[route_only_identity, "Территориальный менеджер"] = raw_map.loc[
        route_only_identity, "ТМ источник key"
    ].map(confirmed_lookup["USERS ТМ текущего СВ"])
    unresolved_person = raw_map["ТМ источник"].notna() & ~verified_tm_identity
    raw_map.loc[unresolved_person, "Источник привязки ТМ"] = UNRESOLVED_SOURCE
    raw_map["Регион BI текущий"] = raw_map["Регион источник"].map(canonical_region_from_text)

    audit = pd.DataFrame(
        [
            {"Проверка": "Строк в текущем файле", "Количество": len(raw_map)},
            {"Проверка": "Конфликтующих ТТ исключено", "Количество": len(conflicting_tt)},
            {
                "Проверка": "ТМ указан напрямую для ТТ",
                "Количество": int(raw_map["Источник привязки ТМ"].eq(CURRENT_TT_SOURCE).sum()),
            },
            {
                "Проверка": "ТМ восстановлен по однозначному коду СВ",
                "Количество": int(raw_map["Источник привязки ТМ"].eq(CURRENT_SV_SOURCE).sum()),
            },
            {
                "Проверка": "Активный ТМ не определён в текущей привязке",
                "Количество": int(raw_map["Территориальный менеджер"].isna().sum()),
            },
            {
                "Проверка": "ТМ не подтверждён активным USERS: имя и ID не используются",
                "Количество": int(unresolved_person.sum()),
            },
        ]
    )

    public_columns = [
        "ТТ",
        "ID территориального менеджера",
        "Территориальный менеджер",
        "ID супервайзера текущий",
        "Супервайзер текущий",
        "Код СВ текущий",
        "Регион BI текущий",
        "Город текущий",
        "Сеть текущая",
        "Источник привязки ТМ",
        "Файл текущей привязки",
    ]
    return raw_map[public_columns].copy(), audit


def _current_team_employee_lookup(current_teams: pd.DataFrame | None) -> pd.DataFrame:
    if current_teams is None or current_teams.empty:
        return pd.DataFrame(columns=["ID сотрудника"])

    pieces: list[pd.DataFrame] = []
    merch_columns = {
        "ID мерчендайзера",
        "ID супервайзера",
        "Супервайзер",
        "ID территориального менеджера",
        "Территориальный менеджер",
        "Регион BI",
    }
    if merch_columns.issubset(current_teams.columns):
        merch = current_teams[
            [
                "ID мерчендайзера",
                "ID супервайзера",
                "Супервайзер",
                "ID территориального менеджера",
                "Территориальный менеджер",
                "Регион BI",
            ]
        ].rename(columns={"ID мерчендайзера": "ID сотрудника"})
        pieces.append(merch)

    sv_columns = {
        "ID супервайзера",
        "Супервайзер",
        "ID территориального менеджера",
        "Территориальный менеджер",
        "Регион BI",
    }
    if sv_columns.issubset(current_teams.columns):
        sv = current_teams[
            [
                "ID супервайзера",
                "Супервайзер",
                "ID территориального менеджера",
                "Территориальный менеджер",
                "Регион BI",
            ]
        ].copy()
        sv["ID сотрудника"] = sv["ID супервайзера"]
        pieces.append(
            sv[
                [
                    "ID сотрудника",
                    "ID супервайзера",
                    "Супервайзер",
                    "ID территориального менеджера",
                    "Территориальный менеджер",
                    "Регион BI",
                ]
            ]
        )

    if not pieces:
        return pd.DataFrame(columns=["ID сотрудника"])

    work = pd.concat(pieces, ignore_index=True)
    work["ID сотрудника"] = _clean_text(work["ID сотрудника"])
    work = work.dropna(subset=["ID сотрудника"])
    counts = work.groupby("ID сотрудника")["ID супервайзера"].nunique(dropna=True)
    valid_ids = set(counts[counts.le(1)].index)
    return work[work["ID сотрудника"].isin(valid_ids)].drop_duplicates(
        "ID сотрудника", keep="last"
    )


def attach_kpi_rtm_org(
    visits: pd.DataFrame,
    current_tm_assignments: pd.DataFrame,
    current_teams: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if visits is None or visits.empty:
        return pd.DataFrame() if visits is None else visits.copy(), pd.DataFrame()

    result = visits.copy().reset_index(drop=True)
    stale_columns = [
        "ID территориального менеджера",
        "Территориальный менеджер",
        "ID супервайзера",
        "Супервайзер",
        "Источник привязки ТМ",
        "Регион BI",
        "Группа региона",
        "Конфликт маршрута и KPI",
        "Конфликт логинов и KPI",
        "ID ТМ текущего СВ",
        "ТМ текущего СВ",
        "Конфликт прошлой и будущей иерархии ТМ",
        "Код СВ текущий",
        "Город текущий",
        "Сеть текущая",
        "Файл текущей привязки",
        "Статус цепочки привязки",
        "Сотрудник подтверждён активным USERS",
    ]
    result = result.drop(columns=stale_columns, errors="ignore")
    result["YearMonth"] = pd.to_numeric(result["YearMonth"], errors="coerce").astype("Int64")
    result["ТТ"] = _code_key(result["ТТ"])
    result["ID сотрудника"] = _clean_text(
        result.get("ID сотрудника", pd.Series(pd.NA, index=result.index))
    )

    assignments = current_tm_assignments.copy()
    assignments["ТТ"] = _code_key(assignments["ТТ"])
    assignments = assignments.drop_duplicates("ТТ", keep="last")
    result = result.merge(assignments, on="ТТ", how="left")

    result["ID территориального менеджера"] = result[
        "ID территориального менеджера"
    ].astype("string")
    result["Территориальный менеджер"] = _clean_text(result["Территориальный менеджер"])
    result["ID супервайзера"] = result["ID супервайзера текущий"].astype("string")
    result["Супервайзер"] = _clean_text(result["Супервайзер текущий"])

    team_lookup = _current_team_employee_lookup(current_teams)
    if not team_lookup.empty:
        team_lookup = team_lookup.rename(
            columns={
                "ID супервайзера": "USERS ID супервайзера",
                "Супервайзер": "USERS Супервайзер",
                "ID территориального менеджера": "USERS ID ТМ",
                "Территориальный менеджер": "USERS ТМ",
                "Регион BI": "USERS Регион BI",
            }
        )
        result = result.merge(team_lookup, on="ID сотрудника", how="left")
        missing_current_sv = (
            result["ID супервайзера"].isna()
            & result["Код СВ текущий"].isna()
            & result["USERS ID супервайзера"].notna()
        )
        result.loc[missing_current_sv, "ID супервайзера"] = result.loc[
            missing_current_sv, "USERS ID супервайзера"
        ]
        result.loc[missing_current_sv, "Супервайзер"] = result.loc[
            missing_current_sv, "USERS Супервайзер"
        ]
        missing_current_tm = (
            result["Территориальный менеджер"].isna()
            & result["USERS ТМ"].notna()
        )
        result.loc[
            missing_current_tm, "ID территориального менеджера"
        ] = result.loc[missing_current_tm, "USERS ID ТМ"]
        result.loc[missing_current_tm, "Территориальный менеджер"] = result.loc[
            missing_current_tm, "USERS ТМ"
        ]
        result.loc[missing_current_tm, "Источник привязки ТМ"] = CURRENT_USERS_SOURCE
    else:
        result["USERS Регион BI"] = pd.NA

    result["Регион BI"] = _clean_text(result["Регион BI текущий"])
    result["Регион BI"] = result["Регион BI"].combine_first(
        _clean_text(result.get("USERS Регион BI", pd.Series(pd.NA, index=result.index)))
    )

    region_map = load_region_map()
    region_group = (
        region_map.drop_duplicates("canonical_region", keep="last")
        .set_index("canonical_region")["region_group"]
        .to_dict()
    )
    result["Группа региона"] = result["Регион BI"].map(region_group).astype("string")
    result["Сотрудник подтверждён активным USERS"] = result["ID сотрудника"].notna()
    result["Статус цепочки привязки"] = "RTM → текущая привязка ТТ → активный USERS"
    result.loc[
        result["ID сотрудника"].notna(), "Статус цепочки привязки"
    ] = "RTM → логины → активный USERS → текущая привязка ТТ"
    result.loc[
        result["Источник привязки ТМ"].eq(CURRENT_USERS_SOURCE),
        "Статус цепочки привязки",
    ] = "RTM → логины → активный USERS"
    result.loc[
        result["Территориальный менеджер"].isna(), "Статус цепочки привязки"
    ] = "Исключён: нет активного ТМ"
    sql_visits = result.get(
        "Источник визитов",
        pd.Series(pd.NA, index=result.index, dtype="string"),
    ).eq("SQL клиента")
    result.loc[
        sql_visits & result["ID сотрудника"].isna(),
        "Статус цепочки привязки",
    ] = "SQL визитов → текущая привязка ТТ"
    result.loc[
        sql_visits & result["ID сотрудника"].notna(),
        "Статус цепочки привязки",
    ] = "SQL визитов → USERS → текущая привязка ТТ"
    result.loc[
        sql_visits & result["Источник привязки ТМ"].eq(CURRENT_USERS_SOURCE),
        "Статус цепочки привязки",
    ] = "SQL визитов → USERS"
    result.loc[
        sql_visits & result["Территориальный менеджер"].isna(),
        "Статус цепочки привязки",
    ] = "Исключён: нет активного ТМ"

    excluded = result[result["Территориальный менеджер"].isna()].copy()
    result = result[result["Территориальный менеджер"].notna()].copy()

    audit = (
        result.groupby(
            ["YearMonth", "Источник привязки ТМ", "Статус цепочки привязки"],
            dropna=False,
        )
        .agg(
            **{
                "Визитов": ("Ключ визита RTM", "nunique"),
                "Уникальных ТТ": ("ТТ", "nunique"),
                "Уникальных ТМ": ("Территориальный менеджер", "nunique"),
                "Сотрудников подтверждено активным USERS": (
                    "Сотрудник подтверждён активным USERS",
                    "sum",
                ),
            }
        )
        .reset_index()
    )
    audit["Источник привязки ТМ"] = audit["Источник привязки ТМ"].fillna(
        UNRESOLVED_SOURCE
    )
    if not excluded.empty:
        excluded_audit = (
            excluded.groupby("YearMonth", dropna=False)
            .agg(
                **{
                    "Визитов": ("Ключ визита RTM", "nunique"),
                    "Уникальных ТТ": ("ТТ", "nunique"),
                    "Сотрудников подтверждено активным USERS": (
                        "Сотрудник подтверждён активным USERS",
                        "sum",
                    ),
                }
            )
            .reset_index()
        )
        excluded_audit["Источник привязки ТМ"] = UNRESOLVED_SOURCE
        excluded_audit["Статус цепочки привязки"] = "Исключён: нет активного ТМ"
        excluded_audit["Уникальных ТМ"] = 0
        audit = pd.concat([audit, excluded_audit[audit.columns]], ignore_index=True)

    result = result.drop(
        columns=[
            "ID супервайзера текущий",
            "Супервайзер текущий",
            "Регион BI текущий",
            "USERS ID супервайзера",
            "USERS Супервайзер",
            "USERS ID ТМ",
            "USERS ТМ",
            "USERS Регион BI",
        ],
        errors="ignore",
    )
    return result, audit


def build_rtm_month_org(visits: pd.DataFrame, entity_column: str) -> pd.DataFrame:
    required = {
        "MonthStart",
        "YearMonth",
        entity_column,
        "ID территориального менеджера",
        "Территориальный менеджер",
    }
    if visits is None or visits.empty or not required.issubset(visits.columns):
        return pd.DataFrame(columns=["MonthStart", "YearMonth", entity_column])
    work = visits.copy().replace("", pd.NA)
    work = work.dropna(
        subset=["MonthStart", "YearMonth", entity_column, "ID территориального менеджера"]
    )
    if work.empty:
        return pd.DataFrame(columns=["MonthStart", "YearMonth", entity_column])
    keys = ["MonthStart", "YearMonth", entity_column]
    tm_count = work.groupby(keys)["ID территориального менеджера"].transform("nunique")
    work = work[tm_count.eq(1)].copy()
    result = (
        work.groupby(keys, dropna=False, sort=False)
        .agg(
            **{
                "ID территориального менеджера": (
                    "ID территориального менеджера",
                    "first",
                ),
            }
        )
        .reset_index()
    )
    for column in ["Территориальный менеджер", "Регион BI"]:
        result = result.merge(_grouped_mode(work, keys, column), on=keys, how="left")

    if entity_column == "ID сотрудника" and {"ID супервайзера", "Супервайзер"}.issubset(
        work.columns
    ):
        sv_count = work.groupby(keys)["ID супервайзера"].transform("nunique")
        work.loc[sv_count.ne(1), ["ID супервайзера", "Супервайзер"]] = pd.NA
        for column in ["ID супервайзера", "Супервайзер"]:
            result = result.merge(_grouped_mode(work, keys, column), on=keys, how="left")
    return result
