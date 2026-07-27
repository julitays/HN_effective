import sys, pandas as pd
sys.stdout.reconfigure(encoding="utf-8")

t = pd.read_parquet("data/out/dim_teams.parquet")
print("Колонки dim_teams:")
for c in t.columns:
    null_pct = t[c].isna().mean() * 100
    dtype = str(t[c].dtype)
    print(f"  {c:<35} {null_pct:5.0f}% null | dtype={dtype}")

print()
# Смотрим строки с null manager_id
no_mgr = t[t["manager_id"].isna()]
print(f"Строк с manager_id=null: {len(no_mgr)}")
if len(no_mgr):
    print("tm_name у них:")
    print(no_mgr["tm_name"].value_counts(dropna=False).head(5).to_string())
    print("sv_name примеры:")
    print(no_mgr["sv_name"].dropna().head(5).tolist())

print()
# Вакансии ТМ
vac = t[t["tm_name"] == "Вакансия"]
print(f"Вакансий ТМ: {len(vac)} строк")
print(f"  manager_name у них: {vac['manager_name'].value_counts(dropna=False).head(3).to_string()}")
print(f"  manager_id у них:   {vac['manager_id'].value_counts(dropna=False).head(3).to_string()}")
