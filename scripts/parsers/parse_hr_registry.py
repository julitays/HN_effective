import sys
from pathlib import Path

import pandas as pd

if __package__ is None or __package__ == "":
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from scripts.staffing_utils import (
    NO_TM_ID,
    NO_TM_NAME,
    build_staffing_reference,
    match_employee_name,
    match_leader_name,
    normalize_confirmed_tm,
    parse_bs_owner_name,
    resolve_region,
    role_bucket,
)
from scripts.utils import get_as_of_date, load_settings, normalize_dim, save_parquet


MIN_PRIOR_USERS_DAYS_FOR_REISSUE = 30
AMBIGUOUS_HR_MACROREGIONS = {"восток"}


def _first_present(primary: dict, secondary: dict, key: str):
    value = primary.get(key, pd.NA)
    if value is None or pd.isna(value) or str(value).strip() == "":
        return secondary.get(key, pd.NA)
    return value


def _confirmed_tm_pair(primary: dict, secondary: dict):
    for source in (primary, secondary):
        tm_id = source.get("ID территориального менеджера", pd.NA)
        tm_name = source.get("Территориальный менеджер", pd.NA)
        tm_id_text = "" if pd.isna(tm_id) else str(tm_id).strip()
        tm_name_text = "" if pd.isna(tm_name) else str(tm_name).strip()
        if tm_id_text == NO_TM_ID or "вакан" in tm_name_text.casefold():
            return NO_TM_ID, NO_TM_NAME
        if tm_id_text and tm_name_text:
            return tm_id, tm_name
    return pd.NA, pd.NA


def _resolve_hr_region(
    row: pd.Series,
    employee_match: dict,
    reference: dict,
) -> str | None:
    detailed_region = resolve_region(
        row.get("Регион"),
        row.get("Город"),
        reference=reference,
    )
    if detailed_region:
        return detailed_region

    employee_region = employee_match.get("Регион BI")
    if employee_region is not None and pd.notna(employee_region):
        employee_region = str(employee_region).strip()
        if employee_region:
            return employee_region

    macroregion = row.get("Макрорегион клиента")
    macroregion_text = "" if macroregion is None or pd.isna(macroregion) else str(macroregion).strip()
    if not macroregion_text or macroregion_text.casefold() in AMBIGUOUS_HR_MACROREGIONS:
        return None
    return resolve_region(macroregion_text, reference=reference)


def _users_snapshot_date() -> pd.Timestamp:
    return get_as_of_date()


def _stable_employee_key(frame: pd.DataFrame) -> pd.Series:
    snils = (
        frame.get("СНИЛС", pd.Series(pd.NA, index=frame.index, dtype="string"))
        .astype("string")
        .str.replace(r"\D", "", regex=True)
    )
    employee_id = (
        frame.get("ID сотрудника", pd.Series(pd.NA, index=frame.index, dtype="string"))
        .astype("string")
        .str.strip()
    )
    employee_name = (
        frame.get("Сотрудник", pd.Series(pd.NA, index=frame.index, dtype="string"))
        .astype("string")
        .str.strip()
        .str.casefold()
    )
    birth_date = pd.to_datetime(frame.get("Дата рождения"), errors="coerce").dt.strftime("%Y-%m-%d")
    fallback = "NAME|" + employee_name.fillna("") + "|" + birth_date.fillna("")
    return snils.where(snils.str.len().fillna(0).gt(0), employee_id).where(
        lambda values: values.str.len().fillna(0).gt(0),
        fallback,
    )


def classify_hr_events(
    frame: pd.DataFrame,
    active_users: pd.DataFrame,
    snapshot_date: pd.Timestamp,
) -> pd.DataFrame:
    result = frame.copy()
    result["Дата приема"] = pd.to_datetime(result["Дата приема"], errors="coerce")
    result["Дата увольнения"] = pd.to_datetime(result["Дата увольнения"], errors="coerce")
    result["Учитывать в найме"] = result["Дата приема"].notna()
    result["Учитывать в увольнении"] = (
        result["Состояние"].astype(str).str.contains("увольнение", case=False, na=False)
        & result["Дата увольнения"].notna()
        & result["Дата увольнения"].le(snapshot_date)
    )
    result["Тип кадрового движения"] = "Первичный найм"
    result["Причина исключения из найма"] = pd.NA
    result["Причина исключения из увольнений"] = pd.NA
    result["_employee_key"] = _stable_employee_key(result)

    result = result.sort_values(
        ["_employee_key", "Дата приема", "Дата увольнения"],
        kind="stable",
    )
    for _, indices in result.groupby("_employee_key", dropna=False, sort=False).groups.items():
        ordered = list(indices)
        if len(ordered) <= 1:
            continue
        for position, current_index in enumerate(ordered[1:], start=1):
            previous_index = ordered[position - 1]
            result.loc[current_index, "Учитывать в найме"] = False
            result.loc[current_index, "Тип кадрового движения"] = "Повторный выход на проект"
            result.loc[current_index, "Причина исключения из найма"] = (
                "Повторный выход того же сотрудника"
            )
            if pd.notna(result.loc[previous_index, "Дата увольнения"]):
                result.loc[previous_index, "Учитывать в увольнении"] = False
                result.loc[previous_index, "Причина исключения из увольнений"] = (
                    "Сотрудник повторно вышел на проект"
                )

    active = normalize_dim(active_users.copy())
    if "is_active" in active.columns:
        active = active[active["is_active"].fillna(False).eq(True)].copy()
    active["employee_id"] = active["employee_id"].astype("string").str.strip()
    active["hire_date"] = pd.to_datetime(active.get("hire_date"), errors="coerce")
    current_hire = (
        active.dropna(subset=["employee_id"])
        .drop_duplicates("employee_id", keep="last")
        .set_index("employee_id")["hire_date"]
    )
    result["Дата текущего выхода USERS"] = result["ID сотрудника"].astype("string").str.strip().map(current_hire)

    prior_users_days = (result["Дата приема"] - result["Дата текущего выхода USERS"]).dt.days
    users_confirmed_reissue = (
        result["Учитывать в найме"]
        & prior_users_days.gt(MIN_PRIOR_USERS_DAYS_FOR_REISSUE)
    )
    result.loc[users_confirmed_reissue, "Учитывать в найме"] = False
    result.loc[users_confirmed_reissue, "Тип кадрового движения"] = "Переоформление кадровой записи"
    result.loc[users_confirmed_reissue, "Причина исключения из найма"] = (
        f"До кадровой даты сотрудник уже работал в USERS более {MIN_PRIOR_USERS_DAYS_FOR_REISSUE} дней"
    )

    active_after_termination = (
        result["Учитывать в увольнении"]
        & result["Активен в USERS"].eq(True)
    )
    repeated_after_break = (
        active_after_termination
        & result["Дата текущего выхода USERS"].gt(result["Дата увольнения"])
    )
    contract_reissue = (
        active_after_termination
        & ~repeated_after_break
        & result["Вид договора"].astype(str).str.strip().eq("УД")
    )
    active_without_confirmed_exit = active_after_termination & ~repeated_after_break & ~contract_reissue

    result.loc[active_after_termination, "Учитывать в увольнении"] = False
    result.loc[repeated_after_break, "Тип кадрового движения"] = "Повторный выход на проект"
    result.loc[repeated_after_break, "Причина исключения из увольнений"] = (
        "После увольнения подтвержден повторный выход в USERS"
    )
    result.loc[contract_reissue, "Тип кадрового движения"] = "Переоформление договора"
    result.loc[contract_reissue, "Причина исключения из увольнений"] = (
        "УД завершен, сотрудник продолжает работать по USERS"
    )
    result.loc[active_without_confirmed_exit, "Тип кадрового движения"] = "Увольнение не подтверждено"
    result.loc[active_without_confirmed_exit, "Причина исключения из увольнений"] = (
        "Сотрудник остается активным в USERS"
    )

    result["Причина исключения из кадровых потоков"] = result.apply(
        lambda row: "; ".join(
            dict.fromkeys(
                str(value).strip()
                for value in [
                    row.get("Причина исключения из найма"),
                    row.get("Причина исключения из увольнений"),
                ]
                if pd.notna(value) and str(value).strip()
            )
        )
        or pd.NA,
        axis=1,
    )

    return result.sort_index().drop(columns="_employee_key")


def parse_hr_registry(
    dim: pd.DataFrame | None = None,
    teams: pd.DataFrame | None = None,
) -> pd.DataFrame:
    settings = load_settings()
    out_dir = Path(settings["paths"]["out"])
    folder = Path(settings["sources"]["hr_registry"]["folder"])
    output = out_dir / "fact_hr_registry.parquet"

    files = sorted([p for p in folder.glob("*.xlsx") if p.is_file()])
    if not files:
        print("  HR REGISTRY: файлы не найдены, пропускаем")
        return pd.DataFrame()

    source = files[-1]
    if dim is None or dim.empty:
        dim = pd.read_parquet(settings["sources"]["users"]["output"])
    if teams is None or teams.empty:
        teams = pd.read_parquet(settings["sources"]["teams"]["output"])
    reference = build_staffing_reference(dim, teams)

    raw = pd.read_excel(source, header=2)
    raw = raw[raw["Проект"].astype(str).str.strip().eq("1054")].copy()
    if raw.empty:
        print("  HR REGISTRY: строк по проекту 1054 нет")
        return pd.DataFrame()

    work = raw.copy()
    work["Дата приема"] = pd.to_datetime(work["Дата приема"], errors="coerce")
    work["Дата увольнения"] = pd.to_datetime(work["Дата увольнения"], errors="coerce")
    work["MonthStart найм"] = work["Дата приема"].dt.to_period("M").dt.to_timestamp()
    work["YearMonth найм"] = (work["MonthStart найм"].dt.year * 100 + work["MonthStart найм"].dt.month).astype("Int64")
    work["MonthStart увольнение"] = work["Дата увольнения"].dt.to_period("M").dt.to_timestamp()
    work["YearMonth увольнение"] = (work["MonthStart увольнение"].dt.year * 100 + work["MonthStart увольнение"].dt.month).astype("Int64")
    work["Роль"] = work["Должность"].map(role_bucket)
    work["BS руководитель"] = work["SV от BS"].map(parse_bs_owner_name)

    rows: list[dict] = []
    for _, row in work.iterrows():
        employee_match = match_employee_name(row.get("Сотрудник"), row.get("Роль"), reference)
        bs_match = match_leader_name(row.get("BS руководитель"), reference, allow_tm=True)
        tm_id, tm_name = _confirmed_tm_pair(employee_match, bs_match)

        region_bi = _resolve_hr_region(row, employee_match, reference)

        rows.append(
            {
                "Сотрудник": row.get("Сотрудник"),
                "СНИЛС": row.get("СНИЛС"),
                "Вид договора": row.get("Вид договора"),
                "Состояние": row.get("Состояние"),
                "Function": row.get("Function"),
                "Проект": row.get("Проект"),
                "Город": row.get("Город"),
                "Регион источник": row.get("Регион"),
                "Макрорегион клиента": row.get("Макрорегион клиента"),
                "Должность": row.get("Должность"),
                "Роль": row.get("Роль"),
                "Дата рождения": pd.to_datetime(row.get("Дата рождения"), errors="coerce"),
                "Дата приема": row.get("Дата приема"),
                "MonthStart найм": row.get("MonthStart найм"),
                "YearMonth найм": row.get("YearMonth найм"),
                "Дата увольнения": row.get("Дата увольнения"),
                "MonthStart увольнение": row.get("MonthStart увольнение"),
                "YearMonth увольнение": row.get("YearMonth увольнение"),
                "Телефон": row.get("Телефон"),
                "Email": row.get("Email"),
                "SV от BS": row.get("SV от BS"),
                "BS руководитель": row.get("BS руководитель"),
                "Регион BI": region_bi,
                "ID сотрудника": employee_match.get("employee_id", pd.NA),
                "ID супервайзера": _first_present(employee_match, bs_match, "ID супервайзера"),
                "Супервайзер": _first_present(employee_match, bs_match, "Супервайзер"),
                "ID территориального менеджера": tm_id,
                "Территориальный менеджер": tm_name,
                "Тип матчинга сотрудника": employee_match.get("match_type", pd.NA),
                "Тип fallback матчинга": employee_match.get("fallback_match_type", pd.NA),
                "Тип матчинга руководителя": bs_match.get("match_type", pd.NA),
                "Активен в USERS": employee_match.get("Активен USERS", pd.NA),
            }
        )

    result = normalize_confirmed_tm(pd.DataFrame(rows))
    result = classify_hr_events(result, dim, _users_snapshot_date())
    save_parquet(result, str(output))
    print(f"\n  HR registry: {len(result)} строк")
    return result


if __name__ == "__main__":
    parse_hr_registry()
