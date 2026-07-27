import sys
from pathlib import Path

import pandas as pd

if __package__ is None or __package__ == "":
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from scripts.staffing_utils import (
    build_staffing_reference,
    match_employee_name,
    match_leader_name,
    parse_bs_owner_name,
    resolve_region,
    role_bucket,
)
from scripts.utils import load_settings, save_parquet


def _first_present(primary: dict, secondary: dict, key: str):
    value = primary.get(key, pd.NA)
    if value is None or pd.isna(value) or str(value).strip() == "":
        return secondary.get(key, pd.NA)
    return value


def parse_hr_registry() -> pd.DataFrame:
    settings = load_settings()
    users_folder = Path(settings["sources"]["users"]["folder"])
    out_dir = Path(settings["paths"]["out"])
    folder = users_folder / "kardovyi_db"
    output = out_dir / "fact_hr_registry.parquet"

    files = sorted([p for p in folder.glob("*.xlsx") if p.is_file()])
    if not files:
        print("  HR REGISTRY: файлы не найдены, пропускаем")
        return pd.DataFrame()

    source = files[-1]
    dim = pd.read_parquet(settings["sources"]["users"]["output"])
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

        region_bi = resolve_region(
            row.get("Макрорегион клиента"),
            row.get("Регион"),
            row.get("Город"),
            reference=reference,
        )
        if not region_bi:
            region_bi = employee_match.get("Регион BI") or bs_match.get("Регион BI")

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
                "ID территориального менеджера": _first_present(employee_match, bs_match, "ID территориального менеджера"),
                "Территориальный менеджер": _first_present(employee_match, bs_match, "Территориальный менеджер"),
                "Тип матчинга сотрудника": employee_match.get("match_type", pd.NA),
                "Тип fallback матчинга": employee_match.get("fallback_match_type", pd.NA),
                "Тип матчинга руководителя": bs_match.get("match_type", pd.NA),
                "Активен в USERS": employee_match.get("Активен USERS", pd.NA),
            }
        )

    result = pd.DataFrame(rows)
    save_parquet(result, str(output))
    print(f"\n  HR registry: {len(result)} строк")
    return result


if __name__ == "__main__":
    parse_hr_registry()
