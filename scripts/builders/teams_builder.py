import pandas as pd
from pathlib import Path
from scripts.utils import load_settings, save_parquet, normalize_dim


NO_TM_ID = "NO_TM"
NO_TM_NAME = "Вакансия / нет ТМ"


def _detect_level(position: str, org_unit: str = "") -> str:
    pos = str(position).strip().lower()

    if pos == "rm":
        return "RM"
    if pos in {"tm", "тм", "территориальный менеджер"}:
        return "TM"
    if "супервайзер" in pos:
        return "SV"
    if "мерч" in pos:
        return "Merch"
    return "Other"


def _resolve(eid: str, lookup: dict) -> dict:
    if not eid or eid == "Вакансия" or eid not in lookup:
        return {}
    return lookup[eid]


def _tm_assignment_for_sv(sv_row: dict, lookup: dict) -> tuple[str | None, str | None, str | None, str]:
    tm_id = sv_row.get("manager_id", "")
    tm_row = _resolve(tm_id, lookup)
    if tm_row.get("level") == "TM":
        rm_id = tm_row.get("manager_id", "")
        rm_row = _resolve(rm_id, lookup)
        return rm_id or None, rm_row.get("full_name"), tm_id or None, tm_row.get("full_name")
    return None, None, NO_TM_ID, NO_TM_NAME


def build_teams(dim: pd.DataFrame = None) -> pd.DataFrame:
    settings     = load_settings()
    teams_cfg    = settings["sources"].get("teams", {})
    output       = teams_cfg.get("output", "data/out/dim_teams.parquet")

    if dim is None or dim.empty:
        dim_path = Path(settings["sources"]["users"]["output"])
        if not dim_path.exists():
            print("  КОМАНДЫ: dim_employees не найден, пропускаем")
            return pd.DataFrame()
        dim = normalize_dim(pd.read_parquet(dim_path))

    # Определяем уровень каждого сотрудника
    dim = dim.copy()
    dim["level"] = dim.apply(
        lambda r: _detect_level(r["position"], r["org_unit"]), axis=1
    )

    active_dim = dim[dim["is_active"].fillna(False).eq(True)].copy() if "is_active" in dim.columns else dim.copy()
    scope_dim = active_dim[active_dim["project"].astype(str).eq("H&N")].copy() if "project" in active_dim.columns else active_dim.copy()

    lookup = active_dim.set_index("employee_id").to_dict("index")

    merch_df = scope_dim[scope_dim["level"] == "Merch"].copy()
    sv_df    = scope_dim[scope_dim["level"] == "SV"].copy()

    svs_with_merch = set(merch_df["manager_id"].dropna())
    empty_svs      = sv_df[~sv_df["employee_id"].isin(svs_with_merch)]

    rows = []

    # ── Строки от мерчендайзеров ─────────────────────────────────────────────
    for _, m in merch_df.iterrows():
        sv_row  = _resolve(m["manager_id"], lookup)
        mgr_id, mgr_name, tm_id, tm_name = _tm_assignment_for_sv(sv_row, lookup)

        rows.append({
            "manager_id":    mgr_id,
            "manager_name":  mgr_name,
            "tm_id":         tm_id,
            "tm_name":       tm_name,
            "sv_id":         m["manager_id"] if m["manager_id"] in lookup else None,
            "sv_name":       sv_row.get("full_name"),
            "sv_city":       sv_row.get("city"),
            "sv_route":      sv_row.get("attribute"),
            "employee_id":   m["employee_id"],
            "employee_name": m["full_name"],
            "position":      m["position"],
            "city":          m["city"],
            "region":        m["region"],
            "hire_date":     m.get("hire_date"),
            "tenure_days":   m.get("tenure_days"),
            "tenure_months": m.get("tenure_months"),
        })

    # ── Строки для СВ без команды (пустые) ───────────────────────────────────
    for _, sv in empty_svs.iterrows():
        mgr_id, mgr_name, tm_id, tm_name = _tm_assignment_for_sv(sv, lookup)

        rows.append({
            "manager_id":    mgr_id,
            "manager_name":  mgr_name,
            "tm_id":         tm_id,
            "tm_name":       tm_name,
            "sv_id":         sv["employee_id"],
            "sv_name":       sv["full_name"],
            "sv_city":       sv["city"],
            "sv_route":      sv.get("attribute"),
            "employee_id":   None,
            "employee_name": None,
            "position":      None,
            "city":          None,
            "region":        sv.get("region"),
            "hire_date":     pd.NaT,
            "tenure_days":   None,
            "tenure_months": None,
        })

    teams = pd.DataFrame(rows)

    # Итоговая статистика
    total_merch = teams["employee_id"].notna().sum()
    total_svs   = teams["sv_id"].nunique()
    total_tms   = teams["tm_id"].nunique()
    mgr_cnt     = teams["manager_id"].nunique()

    print(f"  КОМАНДЫ: Менеджер={mgr_cnt} | ТМ/RM={total_tms} | СВ={total_svs} | Мерч={total_merch}")
    print(f"  КОМАНДЫ: {len(empty_svs)} СВ без мерчендайзеров (включены отдельными строками)")

    teams_out = teams.rename(columns={
        "manager_id":   "ID менеджера",
        "manager_name": "Менеджер",
        "tm_id":        "ID территориального менеджера",
        "tm_name":      "Территориальный менеджер",
        "sv_id":        "ID супервайзера",
        "sv_name":      "Супервайзер",
        "sv_city":      "Город супервайзера",
        "sv_route":     "Код маршрута СВ",
        "employee_id":  "ID мерчендайзера",
        "employee_name":"Мерчендайзер",
        "position":     "Должность",
        "city":         "Город мерчендайзера",
        "region":       "Регион",
        "hire_date":    "Дата приёма",
        "tenure_days":  "Стаж (дней)",
        "tenure_months":"Стаж (месяцев)",
    }).copy()

    string_cols = [
        "ID менеджера",
        "Менеджер",
        "ID территориального менеджера",
        "Территориальный менеджер",
        "ID супервайзера",
        "Супервайзер",
        "Город супервайзера",
        "Код маршрута СВ",
        "ID мерчендайзера",
        "Мерчендайзер",
        "Должность",
        "Город мерчендайзера",
        "Регион",
        "Регион BI",
        "Группа региона",
    ]
    for col in string_cols:
        if col in teams_out.columns:
            teams_out[col] = teams_out[col].astype("string").fillna("")

    if "Дата приёма" in teams_out.columns:
        teams_out["Дата приёма"] = pd.to_datetime(teams_out["Дата приёма"], errors="coerce")
    for col in ["Стаж (дней)", "Стаж (месяцев)"]:
        if col in teams_out.columns:
            teams_out[col] = pd.to_numeric(teams_out[col], errors="coerce").astype("float64")

    save_parquet(teams_out, output)
    return teams
