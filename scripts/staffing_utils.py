import re
from typing import Any

import pandas as pd

from scripts.utils import (
    canonical_region_from_text,
    get_active_users_scope,
    map_region_series,
    normalize_dim,
    normalize_text_value,
)


NO_TM_ID = "NO_TM"
NO_TM_NAME = "Вакансия / нет ТМ"
NO_SV_ID = "NO_SV"


def normalize_confirmed_tm(
    frame: pd.DataFrame,
    id_column: str = "ID территориального менеджера",
    name_column: str = "Территориальный менеджер",
) -> pd.DataFrame:
    result = frame.copy()
    if id_column not in result.columns:
        result[id_column] = pd.NA
    if name_column not in result.columns:
        result[name_column] = pd.NA

    tm_ids = result[id_column].astype("string").str.strip().replace("", pd.NA)
    tm_names = result[name_column].astype("string").str.strip().replace("", pd.NA)
    confirmed_vacancy = tm_ids.eq(NO_TM_ID).fillna(False) | tm_names.str.contains(
        "вакан", case=False, na=False
    )
    tm_ids.loc[confirmed_vacancy] = NO_TM_ID
    tm_names.loc[confirmed_vacancy] = NO_TM_NAME
    tm_ids.loc[tm_names.isna()] = pd.NA

    result[id_column] = tm_ids
    result[name_column] = tm_names
    return result


def normalize_name(value) -> str | None:
    text = normalize_text_value(value)
    if not text:
        return None
    text = text.lower()
    return text


def short_name(value) -> str | None:
    text = normalize_name(value)
    if not text:
        return None
    parts = text.split()
    if len(parts) >= 2:
        return f"{parts[0]} {parts[1]}"
    return parts[0]


def role_bucket(value) -> str:
    text = normalize_text_value(value, upper=True) or ""
    if "СУПЕРВАЙЗЕР" in text:
        return "СВ"
    if "МЕРЧ" in text:
        return "МЕ"
    return "Прочее"


def parse_bs_owner_name(value) -> str | None:
    text = normalize_text_value(value)
    if not text:
        return None
    match = re.search(r"\(([^()]+)\)", text)
    if not match:
        return None
    owner = normalize_text_value(match.group(1))
    return owner


def mode_or_first(series: pd.Series):
    clean = series.dropna()
    if clean.empty:
        return pd.NA
    mode = clean.mode()
    if not mode.empty:
        return mode.iloc[0]
    return clean.iloc[0]


def mode_or_first_text(series: pd.Series):
    clean = series.dropna().astype("string").str.strip()
    clean = clean[clean.ne("")]
    return mode_or_first(clean)


def is_tm_role(value) -> bool:
    text = str(value or "").strip().casefold()
    return text in {"tm", "тм", "территориальный менеджер"}


def missing_supervisor_key(region, tm_id) -> str:
    region_text = str(region).strip() if pd.notna(region) and str(region).strip() else "Без региона"
    tm_text = str(tm_id).strip() if pd.notna(tm_id) and str(tm_id).strip() else NO_TM_ID
    return f"{NO_SV_ID}|{region_text}|{tm_text}"


def missing_supervisor_keys(frame: pd.DataFrame) -> pd.Series:
    return frame.apply(
        lambda row: missing_supervisor_key(
            row.get("Регион BI"), row.get("ID территориального менеджера")
        ),
        axis=1,
    )


def _unique_lookup(df: pd.DataFrame, key_col: str, payload_cols: list[str]) -> dict[str, dict[str, Any]]:
    if key_col not in df.columns:
        return {}
    work = df.dropna(subset=[key_col]).copy()
    if work.empty:
        return {}
    work[key_col] = work[key_col].astype(str)
    counts = work[key_col].value_counts()
    unique_keys = set(counts[counts == 1].index.tolist())
    work = work[work[key_col].isin(unique_keys)].copy()
    if work.empty:
        return {}
    keep = [key_col] + [c for c in payload_cols if c in work.columns]
    return work[keep].set_index(key_col).to_dict("index")


def _clean_identifier(value) -> str | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "null"}:
        return None
    return text


def _is_missing(value) -> bool:
    return value is None or pd.isna(value) or str(value).strip() == ""


def _merge_match(primary: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
    if not primary:
        return fallback.copy()
    if not fallback:
        return primary.copy()

    result = primary.copy()
    for key, value in fallback.items():
        if key == "match_type":
            continue
        if _is_missing(result.get(key, pd.NA)) and not _is_missing(value):
            result[key] = value
    result["fallback_match_type"] = fallback.get("match_type", pd.NA)
    return result


def _is_supervisor_position(value) -> bool:
    text = normalize_text_value(value, upper=True) or ""
    return "СУПЕРВАЙЗЕР" in text


def _is_merch_position(value) -> bool:
    text = normalize_text_value(value, upper=True) or ""
    return "МЕРЧ" in text


def _is_tm_position(value) -> bool:
    text = normalize_text_value(value, upper=True) or ""
    return text in {"TM", "ТМ", "ТЕРРИТОРИАЛЬНЫЙ МЕНЕДЖЕР"}


def _is_hn_project(df: pd.DataFrame) -> pd.Series:
    mask = pd.Series(True, index=df.index)
    filters: list[pd.Series] = []
    if "project" in df.columns:
        filters.append(df["project"].astype(str).str.strip().str.upper().eq("H&N"))
    if "groups" in df.columns:
        filters.append(df["groups"].astype(str).str.contains("H&N", case=False, na=False))
    if filters:
        mask = filters[0]
        for item in filters[1:]:
            mask = mask | item
    return mask


def _current_team_payload(
    tm_id,
    current_tm_lookup: dict[str, dict[str, Any]],
    fallback_region=None,
) -> dict[str, Any]:
    clean_tm_id = _clean_identifier(tm_id)
    if not clean_tm_id or clean_tm_id not in current_tm_lookup:
        return {
            "ID территориального менеджера": pd.NA,
            "Территориальный менеджер": pd.NA,
            "Регион BI": fallback_region,
        }

    tm_row = current_tm_lookup[clean_tm_id]
    return {
        "ID территориального менеджера": clean_tm_id,
        "Территориальный менеджер": tm_row.get("Территориальный менеджер", pd.NA),
        "Регион BI": tm_row.get("Регион BI", fallback_region),
    }


def _build_all_users_field_lookup(
    dim: pd.DataFrame,
    sv_team: pd.DataFrame,
    tm_team: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    all_people = dim.copy()
    for column in [
        "employee_id",
        "full_name",
        "position",
        "region",
        "manager_id",
        "is_active",
        "Регион BI",
    ]:
        if column not in all_people.columns:
            all_people[column] = pd.NA

    all_people = all_people[_is_hn_project(all_people)].copy()
    if all_people.empty:
        empty = pd.DataFrame()
        return empty, empty

    all_people["employee_id"] = all_people["employee_id"].map(_clean_identifier)
    all_people["manager_id"] = all_people["manager_id"].map(_clean_identifier)
    all_people["name_norm"] = all_people["full_name"].map(normalize_name)
    all_people["short_name"] = all_people["full_name"].map(short_name)
    all_people["Регион BI"] = all_people["Регион BI"].combine_first(map_region_series(all_people["region"]))

    current_sv_lookup = {
        str(row["employee_id"]).strip(): row.to_dict()
        for _, row in sv_team.dropna(subset=["employee_id"]).iterrows()
        if _clean_identifier(row.get("employee_id"))
    }
    current_tm_lookup = {
        str(row["employee_id"]).strip(): row.to_dict()
        for _, row in tm_team.dropna(subset=["employee_id"]).iterrows()
        if _clean_identifier(row.get("employee_id"))
    }
    people_by_id = {
        employee_id: row.to_dict()
        for employee_id, row in all_people.dropna(subset=["employee_id"]).set_index("employee_id").iterrows()
    }

    rows: list[dict[str, Any]] = []
    for _, person in all_people.iterrows():
        employee_id = _clean_identifier(person.get("employee_id"))
        if not employee_id:
            continue

        is_sv = _is_supervisor_position(person.get("position"))
        is_me = _is_merch_position(person.get("position"))
        if not is_sv and not is_me:
            continue

        payload: dict[str, Any] = {
            "employee_id": employee_id,
            "full_name": person.get("full_name"),
            "position": person.get("position"),
            "name_norm": person.get("name_norm"),
            "short_name": person.get("short_name"),
            "Регион BI": person.get("Регион BI"),
            "ID супервайзера": pd.NA,
            "Супервайзер": pd.NA,
            "ID территориального менеджера": pd.NA,
            "Территориальный менеджер": pd.NA,
            "Активен USERS": person.get("is_active", pd.NA),
        }

        if is_sv:
            payload["ID супервайзера"] = employee_id
            payload["Супервайзер"] = person.get("full_name")
            current_sv = current_sv_lookup.get(employee_id)
            if current_sv:
                payload["ID территориального менеджера"] = current_sv.get("ID территориального менеджера", pd.NA)
                payload["Территориальный менеджер"] = current_sv.get("Территориальный менеджер", pd.NA)
                payload["Регион BI"] = current_sv.get("Регион BI", payload["Регион BI"])
            else:
                manager_id = _clean_identifier(person.get("manager_id"))
                if manager_id and manager_id in current_sv_lookup:
                    manager_sv = current_sv_lookup[manager_id]
                    payload["ID территориального менеджера"] = manager_sv.get("ID территориального менеджера", pd.NA)
                    payload["Территориальный менеджер"] = manager_sv.get("Территориальный менеджер", pd.NA)
                    payload["Регион BI"] = manager_sv.get("Регион BI", payload["Регион BI"])
                else:
                    payload.update(_current_team_payload(manager_id, current_tm_lookup, payload["Регион BI"]))

        if is_me:
            sv_id = _clean_identifier(person.get("manager_id"))
            current_sv = current_sv_lookup.get(sv_id) if sv_id else None
            if current_sv:
                payload["ID супервайзера"] = sv_id
                payload["Супервайзер"] = current_sv.get("Супервайзер", pd.NA)
                payload["ID территориального менеджера"] = current_sv.get("ID территориального менеджера", pd.NA)
                payload["Территориальный менеджер"] = current_sv.get("Территориальный менеджер", pd.NA)
                payload["Регион BI"] = current_sv.get("Регион BI", payload["Регион BI"])
            elif sv_id and sv_id in people_by_id:
                sv_person = people_by_id[sv_id]
                if _is_supervisor_position(sv_person.get("position")):
                    payload["ID супервайзера"] = sv_id
                    payload["Супервайзер"] = sv_person.get("full_name")
                    payload.update(
                        _current_team_payload(
                            sv_person.get("manager_id"),
                            current_tm_lookup,
                            payload["Регион BI"],
                        )
                    )

        rows.append(payload)

    resolved = pd.DataFrame(rows)
    if resolved.empty:
        empty = pd.DataFrame()
        return empty, empty

    resolved["ID территориального менеджера"] = resolved["ID территориального менеджера"].map(_clean_identifier)
    resolved["ID супервайзера"] = resolved["ID супервайзера"].map(_clean_identifier)
    resolved["Регион BI"] = resolved["Регион BI"].replace("", pd.NA)

    all_me = resolved[resolved["position"].map(_is_merch_position)].copy()
    all_sv = resolved[resolved["position"].map(_is_supervisor_position)].copy()
    return all_me, all_sv


def build_staffing_reference(dim_employees: pd.DataFrame, teams: pd.DataFrame) -> dict[str, Any]:
    dim = normalize_dim(dim_employees.copy()) if "employee_id" not in dim_employees.columns else dim_employees.copy()
    scope = get_active_users_scope(dim)
    active = scope["frame"].copy()
    active["name_norm"] = active["full_name"].map(normalize_name)
    active["short_name"] = active["full_name"].map(short_name)

    teams_work = teams.replace("", pd.NA).copy()

    merch_team = (
        teams_work.dropna(subset=["ID мерчендайзера"])
        .groupby("ID мерчендайзера", dropna=False)
        .agg(
            **{
                "ID супервайзера": ("ID супервайзера", "first"),
                "Супервайзер": ("Супервайзер", "first"),
                "ID территориального менеджера": ("ID территориального менеджера", "first"),
                "Территориальный менеджер": ("Территориальный менеджер", "first"),
                "Регион BI": ("Регион BI", mode_or_first),
            }
        )
        .reset_index()
        .rename(columns={"ID мерчендайзера": "employee_id"})
    )

    sv_team = (
        teams_work.dropna(subset=["ID супервайзера"])
        .groupby("ID супервайзера", dropna=False)
        .agg(
            **{
                "Супервайзер": ("Супервайзер", "first"),
                "ID территориального менеджера": ("ID территориального менеджера", "first"),
                "Территориальный менеджер": ("Территориальный менеджер", "first"),
                "Регион BI": ("Регион BI", mode_or_first),
            }
        )
        .reset_index()
        .rename(columns={"ID супервайзера": "employee_id"})
    )

    tm_team = (
        teams_work.dropna(subset=["ID территориального менеджера"])
        .groupby("ID территориального менеджера", dropna=False)
        .agg(
            **{
                "Территориальный менеджер": ("Территориальный менеджер", "first"),
                "Регион BI": ("Регион BI", mode_or_first),
            }
        )
        .reset_index()
        .rename(columns={"ID территориального менеджера": "employee_id"})
    )

    people = active[
        [
            "employee_id",
            "full_name",
            "position",
            "city",
            "region",
            "manager_id",
            "manager_full_name",
        ]
    ].copy()
    if "Регион BI" in active.columns:
        people["Регион BI"] = active["Регион BI"]
    else:
        people["Регион BI"] = map_region_series(active["region"])

    people["name_norm"] = people["full_name"].map(normalize_name)
    people["short_name"] = people["full_name"].map(short_name)

    merch = people[people["position"].astype(str).str.lower().str.contains("мерч", na=False)].copy()
    merch = merch.merge(merch_team, on="employee_id", how="left", suffixes=("", "_team"))
    merch["Регион BI"] = merch["Регион BI_team"].combine_first(merch["Регион BI"])
    merch = merch.drop(columns=[c for c in ["Регион BI_team"] if c in merch.columns])

    sv = people[people["position"].astype(str).str.lower().str.contains("супервайзер", na=False)].copy()
    sv = sv.merge(sv_team, on="employee_id", how="left", suffixes=("", "_team"))
    sv["Супервайзер"] = sv["Супервайзер"].combine_first(sv["full_name"])
    sv["Регион BI"] = sv["Регион BI_team"].combine_first(sv["Регион BI"])
    sv = sv.rename(columns={"employee_id": "ID супервайзера"})
    sv = sv.drop(columns=[c for c in ["Регион BI_team"] if c in sv.columns])

    tm_mask = people["position"].map(_is_tm_position)
    tm = people[tm_mask].copy()
    tm = tm.merge(tm_team, on="employee_id", how="outer", suffixes=("", "_team"))
    tm["Территориальный менеджер"] = tm["full_name"].combine_first(tm["Территориальный менеджер"])
    tm["Регион BI"] = tm["Регион BI_team"].combine_first(tm["Регион BI"])
    tm = tm.rename(columns={"employee_id": "ID территориального менеджера"})
    tm["name_norm"] = tm["Территориальный менеджер"].map(normalize_name)
    tm = tm.drop(columns=[c for c in ["Регион BI_team"] if c in tm.columns])

    all_me, all_sv = _build_all_users_field_lookup(dim, sv_team, tm_team)

    city_region = (
        active.dropna(subset=["city"])
        .assign(city_norm=active["city"].map(normalize_text_value))
        .dropna(subset=["city_norm", "Регион BI"])
        .groupby("city_norm", dropna=False)["Регион BI"]
        .agg(mode_or_first)
        .to_dict()
    )

    return {
        "active": active,
        "merch": merch,
        "sv": sv,
        "tm": tm,
        "city_region_lookup": city_region,
        "sv_full_lookup": _unique_lookup(
            sv.assign(name_key=sv["Супервайзер"].map(normalize_name)),
            "name_key",
            ["ID супервайзера", "Супервайзер", "ID территориального менеджера", "Территориальный менеджер", "Регион BI"],
        ),
        "tm_full_lookup": _unique_lookup(
            tm.assign(name_key=tm["Территориальный менеджер"].map(normalize_name)),
            "name_key",
            ["ID территориального менеджера", "Территориальный менеджер", "Регион BI"],
        ),
        "me_full_lookup": _unique_lookup(
            merch.assign(name_key=merch["full_name"].map(normalize_name)),
            "name_key",
            ["employee_id", "full_name", "ID супервайзера", "Супервайзер", "ID территориального менеджера", "Территориальный менеджер", "Регион BI"],
        ),
        "all_me_full_lookup": _unique_lookup(
            all_me.assign(name_key=all_me["full_name"].map(normalize_name)) if not all_me.empty else all_me,
            "name_key",
            ["employee_id", "full_name", "ID супервайзера", "Супервайзер", "ID территориального менеджера", "Территориальный менеджер", "Регион BI", "Активен USERS"],
        ),
        "all_sv_full_lookup": _unique_lookup(
            all_sv.assign(name_key=all_sv["full_name"].map(normalize_name)) if not all_sv.empty else all_sv,
            "name_key",
            ["employee_id", "full_name", "ID супервайзера", "Супервайзер", "ID территориального менеджера", "Территориальный менеджер", "Регион BI", "Активен USERS"],
        ),
    }


def resolve_region(*values, reference: dict[str, Any] | None = None) -> str | None:
    for value in values:
        if value is None or pd.isna(value):
            continue
        exact = map_region_series(pd.Series([value])).iloc[0]
        if pd.notna(exact):
            return str(exact)

    city_lookup = (reference or {}).get("city_region_lookup", {})
    for value in values:
        city_norm = normalize_text_value(value)
        if city_norm and city_norm in city_lookup:
            return city_lookup[city_norm]

    for value in values:
        region = canonical_region_from_text(value)
        if region:
            return region
    return None


def match_leader_name(raw_name, reference: dict[str, Any], allow_tm: bool = True) -> dict[str, Any]:
    name_norm = normalize_name(raw_name)
    if name_norm and name_norm in reference["sv_full_lookup"]:
        row = reference["sv_full_lookup"][name_norm].copy()
        row["match_type"] = "sv_full"
        return row
    if allow_tm and name_norm and name_norm in reference["tm_full_lookup"]:
        row = reference["tm_full_lookup"][name_norm].copy()
        row["match_type"] = "tm_full"
        return row
    return {}


def match_employee_name(raw_name, role: str, reference: dict[str, Any]) -> dict[str, Any]:
    name_norm = normalize_name(raw_name)

    if role == "СВ":
        row = match_leader_name(raw_name, reference, allow_tm=True)
        fallback = {}
        if name_norm and name_norm in reference["all_sv_full_lookup"]:
            fallback = reference["all_sv_full_lookup"][name_norm].copy()
            fallback["match_type"] = "all_users_sv_full"
        return _merge_match(row, fallback)

    if role == "МЕ":
        row = {}
        if name_norm and name_norm in reference["me_full_lookup"]:
            row = reference["me_full_lookup"][name_norm].copy()
            row["match_type"] = "me_full"

        fallback = {}
        if name_norm and name_norm in reference["all_me_full_lookup"]:
            fallback = reference["all_me_full_lookup"][name_norm].copy()
            fallback["match_type"] = "all_users_me_full"
        if row or fallback:
            return _merge_match(row, fallback)

    return match_leader_name(raw_name, reference, allow_tm=True)


# ── Скоринг СВ/ТМ-эффективности (вынесено сюда — было продублировано
#    один в один между build_page5_sv_oed_data.py и build_page7_tm_data.py) ───

def score_higher_is_better(value, green_min: float, red_min: float, weight: float) -> float:
    if pd.isna(value):
        return 0.0
    value = float(value)
    if value < red_min:
        return 0.0
    return max(0.0, min(1.0, value / green_min)) * weight


def score_lower_is_better(value, green_max: float, red_max: float, weight: float) -> float:
    if pd.isna(value):
        return 0.0
    value = max(0.0, float(value))
    if value > red_max:
        return 0.0
    if value <= green_max:
        return weight
    span = red_max - green_max
    if span <= 0:
        return 0.0
    return max(0.0, min(1.0, (red_max - value) / span)) * weight


def attach_last_quarter_metric(
    monthly_base: pd.DataFrame,
    quarterly_df: pd.DataFrame,
    value_col: str,
    period: str = "quarter",
) -> pd.DataFrame:
    """К каждому месяцу региона подтягивает значение только внутри согласованного периода."""
    if quarterly_df.empty:
        monthly_base[value_col] = pd.NA
        return monthly_base

    pieces = []
    for region, region_base in monthly_base.groupby("Регион BI", dropna=False):
        base_sorted = region_base.sort_values("MonthStart").reset_index(drop=True).copy()
        quarter_sorted = quarterly_df[quarterly_df["Регион BI"] == region].sort_values("QuarterStart").copy()
        if quarter_sorted.empty:
            base_sorted[value_col] = pd.NA
        else:
            merged = pd.merge_asof(
                base_sorted,
                quarter_sorted[["QuarterStart", value_col]],
                left_on="MonthStart",
                right_on="QuarterStart",
                direction="backward",
            )
            month_start = pd.to_datetime(base_sorted["MonthStart"], errors="coerce")
            matched_quarter = pd.to_datetime(merged["QuarterStart"], errors="coerce")
            if period == "year":
                valid_period = matched_quarter.dt.year.eq(month_start.dt.year)
            else:
                valid_period = matched_quarter.dt.to_period("Q").eq(month_start.dt.to_period("Q"))
            base_sorted[value_col] = merged[value_col].where(valid_period, pd.NA).values
        pieces.append(base_sorted)

    return pd.concat(pieces, ignore_index=True)


def build_enps_quarterly(enps: pd.DataFrame) -> pd.DataFrame:
    return (
        enps.groupby(["QuarterStart", "YearQuarter", "Регион BI"], dropna=False)
        .agg(
            **{
                "Риск ухода региона %": (
                    "Уровень риска ухода",
                    lambda s: s.eq("Высокий").mean(),
                ),
            }
        )
        .reset_index()
    )
