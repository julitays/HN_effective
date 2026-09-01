from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

if __package__ is None or __package__ == "":
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from scripts.staffing_utils import is_tm_role
from scripts.utils import load_settings, map_region_series, normalize_dim, save_parquet


def build_security_access(dim: pd.DataFrame | None = None) -> pd.DataFrame:
    settings = load_settings()
    out_dir = Path(settings["paths"]["out"])
    if dim is None:
        dim = pd.read_parquet(settings["sources"]["users"]["output"])
    users = normalize_dim(dim).copy()
    if "Регион BI" not in users.columns:
        users["Регион BI"] = map_region_series(users["region"])
    active = users[
        users["is_active"].fillna(False)
        & users["project"].astype("string").str.strip().str.upper().eq(settings.get("project", "H&N").upper())
    ].copy()
    active["UPN"] = active["email"].astype("string").str.strip().str.lower()
    active["Регион BI"] = active["Регион BI"].astype("string").str.strip()
    has_upn = active["UPN"].str.contains("@", na=False).fillna(False)
    has_region = active["Регион BI"].notna() & active["Регион BI"].fillna("").ne("")
    active = active[has_upn & has_region]

    base = active[["UPN", "Регион BI", "employee_id", "position"]].rename(
        columns={"employee_id": "ID сотрудника", "position": "Должность"}
    )
    base["Источник доступа"] = "USERS: собственный регион"

    tm_users = active[active["position"].apply(is_tm_role)][["employee_id", "UPN", "position"]]
    dsupervisor_path = out_dir / "dSupervisor.parquet"
    tm_regions = pd.DataFrame(columns=base.columns)
    if dsupervisor_path.exists() and not tm_users.empty:
        dsupervisor = pd.read_parquet(dsupervisor_path)
        tm_regions = dsupervisor[
            ["ID территориального менеджера", "Регион BI"]
        ].dropna().drop_duplicates()
        tm_regions = tm_regions.merge(
            tm_users,
            left_on="ID территориального менеджера",
            right_on="employee_id",
            how="inner",
        )
        tm_regions = tm_regions.rename(
            columns={"employee_id": "ID сотрудника", "position": "Должность"}
        )[["UPN", "Регион BI", "ID сотрудника", "Должность"]]
        tm_regions["Источник доступа"] = "USERS: территория ТМ"

    result = pd.concat([base, tm_regions], ignore_index=True)
    result = result.drop_duplicates(["UPN", "Регион BI"]).sort_values(["UPN", "Регион BI"])
    save_parquet(result, str(out_dir / "security_region_access.parquet"))
    print(f"  RLS: {result['UPN'].nunique()} пользователей, {len(result)} доступов к регионам")
    return result


if __name__ == "__main__":
    build_security_access()
