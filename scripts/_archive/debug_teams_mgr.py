import sys, pandas as pd
sys.stdout.reconfigure(encoding="utf-8")

dim = pd.read_parquet("data/out/dim_employees.parquet")
t   = pd.read_parquet("data/out/dim_teams.parquet")

# Проверяем наличие вакантных менеджеров (id — см. config/settings.yml) в dim
VACANT_TM = "109-352-710-45"
VACANT_RM = "172-922-951-88"

for pid, name in [(VACANT_TM, "vacant_tm_manager_id"), (VACANT_RM, "vacant_rm_manager_id")]:
    row = dim[dim["employee_id"] == pid]
    if not row.empty:
        print(f"{name} ({pid}): ЕСТЬ в dim | full_name={row.iloc[0]['full_name']}")
    else:
        print(f"{name} ({pid}): НЕТ в dim!")

print()

# Проверяем какие manager_id есть в dim_teams (и есть ли они в dim)
print("Уникальные manager_id в dim_teams:")
for mid in t["manager_id"].dropna().unique():
    in_dim = mid in dim["employee_id"].values
    name   = dim[dim["employee_id"] == mid]["full_name"].values
    nm     = name[0] if len(name) else "???"
    print(f"  {mid}: {'✓' if in_dim else '❌'} {nm}")

print()
# Смотрим типичный путь мерч -> sv -> tm -> manager
sample = t[t["employee_id"].notna() & t["manager_id"].notna()].head(3)
print("Примеры с заполненным manager_id:")
print(sample[["manager_id","manager_name","tm_id","tm_name","sv_name","employee_name"]].to_string())

print()
sample2 = t[t["employee_id"].notna() & t["manager_id"].isna() & t["tm_name"].isna()].head(3)
print("Примеры с пустым manager_id И пустым tm_name:")
print(sample2[["manager_id","tm_id","tm_name","sv_id","sv_name","employee_name"]].to_string())
