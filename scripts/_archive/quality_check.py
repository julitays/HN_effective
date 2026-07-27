import pandas as pd

dim  = pd.read_parquet("data/out/dim_employees.parquet")
fact = pd.read_parquet("data/out/fact_oed.parquet")

SEP = "=" * 55

# ── dim_employees ─────────────────────────────────────────
print(SEP)
print("ПРОВЕРКА dim_employees")
print(SEP)
print(f"Строк: {len(dim)} | Уникальных ID: {dim['employee_id'].nunique()}")
print(f"Дублей employee_id: {dim['employee_id'].duplicated().sum()}")

for col in ["employee_id", "full_name", "city", "region", "hire_date"]:
    n = dim[col].isna().sum()
    if n:
        print(f"  ПУСТО {col}: {n}")

known_ids   = set(dim["employee_id"])
orphan_mgr  = dim[~dim["manager_id"].isin(known_ids | {"Вакансия"})]["manager_id"].nunique()
vacancy_cnt = (dim["manager_id"] == "Вакансия").sum()
id_pattern  = dim["manager_full_name"].str.match(r"^\d{3}-\d{3}-\d{3}-\d{2}$", na=False).sum()
neg_tenure  = (dim["tenure_days"] < 0).sum()

print(f"Без руководителя (Вакансия): {vacancy_cnt}")
print(f"Менеджер не в USERS (внешний/уволенный): {orphan_mgr}")
print(f"manager_full_name остался ID: {id_pattern}")
print(f"Отрицательный стаж (будущая дата найма): {neg_tenure}")

print(f"Уникальных городов после нормализации: {dim['city'].dropna().nunique()}")
print("Топ-5 городов:")
print(dim["city"].value_counts().head().to_string())

# ── fact_oed ──────────────────────────────────────────────
print()
print(SEP)
print("ПРОВЕРКА fact_oed")
print(SEP)
matched_mask = fact["employee_id"].notna()
print(f"Строк всего: {len(fact)}")
print(f"Совпали с USERS: {matched_mask.sum()} ({matched_mask.mean()*100:.1f}%)")
print(f"Не совпали (исторические): {(~matched_mask).sum()}")

dups = fact.dropna(subset=["employee_id"]).duplicated(
    subset=["employee_id", "period", "role_type"], keep=False
)
print(f"\nДублей employee+period+role_type: {dups.sum()}")
if dups.sum():
    show = fact[fact["employee_id"].notna()][dups]
    print(show[["employee_id","period","role_type","rating","class"]].sort_values(
        ["employee_id","period"]
    ).head(8).to_string())

print(f"\nПустой rating: {fact['rating'].isna().sum()}")

print("\nЗначения class:")
print(fact["class"].value_counts(dropna=False).to_string())

matched = fact[matched_mask]
print(f"\nchurn_risk = True (из совпавших): {matched['churn_risk'].eq(True).sum()} из {len(matched)}")
print(f"is_first_period = True: {matched['is_first_period'].eq(True).sum()}")
print(f"rating_delta null (первый период): {matched['rating_delta'].isna().sum()}")

dim_ids  = set(dim["employee_id"])
fact_ids = set(fact["employee_id"].dropna())
not_in_oed = dim_ids - fact_ids
print(f"\nСотрудников из USERS без записи в ОЭД: {len(not_in_oed)} из {len(dim_ids)}")
print(f"% охвата ОЭД от активных сотрудников: {len(fact_ids)/len(dim_ids)*100:.1f}%")

print()
print(SEP)
print("ИТОГ")
print(SEP)
issues = []
if dim["employee_id"].duplicated().sum():
    issues.append("  [!] Дубли в dim_employees.employee_id")
if neg_tenure:
    issues.append(f"  [!] {neg_tenure} сотрудников с будущей датой найма")
if dups.sum():
    issues.append(f"  [!] {dups.sum()} дублей в fact_oed (один сотрудник в периоде дважды)")
if fact["rating"].isna().sum() > 100:
    issues.append(f"  [!] Много пустых рейтингов: {fact['rating'].isna().sum()}")
if issues:
    print("Проблемы:")
    for i in issues:
        print(i)
else:
    print("Критических проблем не обнаружено.")
