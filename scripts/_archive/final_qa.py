import sys, pandas as pd
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")

SEP = "=" * 60

def check_table(name, path, key_cols, expect_rows=None):
    if not Path(path).exists():
        print(f"  ОТСУТСТВУЕТ: {path}")
        return None
    df = pd.read_parquet(path)
    print(f"\n{SEP}")
    print(f"{name}  ({path})")
    print(f"{SEP}")
    print(f"Строк: {len(df)} | Колонок: {len(df.columns)}")
    if expect_rows:
        status = "✓" if len(df) >= expect_rows else "⚠"
        print(f"  {status} Ожидалось ≥{expect_rows} строк")

    # Дубли по ключу
    for k in key_cols:
        if k in df.columns:
            dups = df.duplicated(subset=k, keep=False).sum()
            status = "✓" if dups == 0 else f"⚠ {dups} дублей"
            print(f"  {k}: уникальных {df[k].nunique()} | дубли: {status}")

    # Пустые колонки
    empty = [c for c in df.columns if df[c].isna().all()]
    obj_empty = [c for c in df.columns if df[c].dtype == object and df[c].notna().sum() == 0]
    print(f"  Полностью пустых колонок: {len(empty)} {empty if empty else ''}")

    # check_* остатки
    checks = [c for c in df.columns if c.startswith("check_")]
    if checks:
        print(f"  ⚠ check_* колонки: {checks}")
    else:
        print(f"  ✓ Нет check_* колонок")

    # Типы с object (потенциальные проблемы PBI)
    obj_cols = [c for c in df.columns if df[c].dtype == object]
    if obj_cols:
        nonempty_obj = [c for c in obj_cols if df[c].notna().sum() > 0]
        print(f"  object dtype: {nonempty_obj[:5]} ({'...' if len(nonempty_obj)>5 else 'все'})")

    return df

# ── dim_employees ─────────────────────────────────────────
dim = check_table(
    "dim_employees",
    "data/out/dim_employees.parquet",
    key_cols=["ID сотрудника"],
)
if dim is not None:
    active_col = "Активен" if "Активен" in dim.columns else "is_active" if "is_active" in dim.columns else None
    active   = dim[active_col].eq(True).sum()  if active_col else "?"
    inactive = dim[active_col].eq(False).sum() if active_col else "?"
    print(f"  Активные: {active} | Неактивные: {inactive}")
    manager_col = "ФИО руководителя" if "ФИО руководителя" in dim.columns else "manager_full_name" if "manager_full_name" in dim.columns else None
    if manager_col:
        bad_mgr = dim[manager_col].astype("string").str.match(r"^\d{3}-\d{3}-\d{3}-\d{2}$", na=False).sum()
        print(f"  {manager_col} = ID (не resolved): {bad_mgr}")

# ── dim_teams ─────────────────────────────────────────────
teams = check_table(
    "dim_teams",
    "data/out/dim_teams.parquet",
    key_cols=[],
)
if teams is not None:
    print(f"  ID менеджера null: {teams['ID менеджера'].isna().sum() if 'ID менеджера' in teams.columns else '?'}")
    vac = teams["Территориальный менеджер"].astype("string").eq("Вакансия").sum() if "Территориальный менеджер" in teams.columns else 0
    print(f"  Вакансий ТМ: {vac}")

# ── fact_oed ──────────────────────────────────────────────
oed = check_table(
    "fact_oed",
    "data/out/fact_oed.parquet",
    key_cols=["ID сотрудника", "Период", "Роль"],
    expect_rows=2000,
)
if oed is not None:
    matched = oed["ID сотрудника"].notna().mean() * 100 if "ID сотрудника" in oed.columns else 0
    print(f"  Матчинг: {matched:.1f}%")
    risk_col = "Риск оттока" if "Риск оттока" in oed.columns else "churn_risk" if "churn_risk" in oed.columns else None
    if risk_col:
        churn = oed[risk_col].notna().sum()
        print(f"  {risk_col}: {churn} заполнено")

# ── fact_okk ──────────────────────────────────────────────
okk = check_table(
    "fact_okk",
    "data/out/okk_fact.parquet",
    key_cols=[],
    expect_rows=12000,
)
if okk is not None:
    fals_col = next((c for c in okk.columns if "фальс" in c.lower() and "флаг" in c.lower()), None)
    if fals_col:
        null_f = okk[fals_col].isna().sum()
        print(f"  {fals_col} null: {null_f}")
    audit_null = okk["Дата визита"].isna().sum() if "Дата визита" in okk.columns else "?"
    sap_null   = okk["Код ТТ"].isna().sum() if "Код ТТ" in okk.columns else "?"
    print(f"  Дата визита null: {audit_null} | Код ТТ null: {sap_null}")
    for col in ("% PICoS стандартов", "% качества PICoS", "% OSA (наличие на полке)"):
        if col in okk.columns:
            pct = okk[col].notna().mean() * 100
            print(f"  {col}: {pct:.0f}% заполнено")

print(f"\n{SEP}")
print("ИТОГ")
print(SEP)
files = {
    "dim_employees": "data/out/dim_employees.parquet",
    "dim_teams":     "data/out/dim_teams.parquet",
    "fact_oed":      "data/out/fact_oed.parquet",
    "fact_okk":      "data/out/okk_fact.parquet",
}
for name, path in files.items():
    exists = Path(path).exists()
    if exists:
        df = pd.read_parquet(path)
        checks = [c for c in df.columns if c.startswith("check_")]
        obj_empty = [c for c in df.columns
                     if df[c].dtype == object and df[c].notna().sum() == 0]
        issues = []
        if checks:       issues.append(f"{len(checks)} check_*")
        if obj_empty:    issues.append(f"{len(obj_empty)} empty object")
        status = "✓ ОК" if not issues else "⚠  " + ", ".join(issues)
        print(f"  {name:<20} {len(df):>6} строк  {status}")
    else:
        print(f"  {name:<20} ОТСУТСТВУЕТ")
