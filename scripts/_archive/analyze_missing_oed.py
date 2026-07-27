import pandas as pd

dim  = pd.read_parquet("data/out/dim_employees.parquet")
fact = pd.read_parquet("data/out/fact_oed.parquet")

fact_ids = set(fact["employee_id"].dropna())
missing  = dim[~dim["employee_id"].isin(fact_ids)].copy()

print(f"Всего сотрудников в USERS: {len(dim)}")
print(f"Без записей в ОЭД: {len(missing)}")
print()

# ── Должности ────────────────────────────────────────────
print("=" * 50)
print("ДОЛЖНОСТИ (все варианты)")
print("=" * 50)
print(missing["position"].value_counts().head(25).to_string())
print()

# ── Категории ────────────────────────────────────────────
field_kw = ["мерч", "супервайз"]
is_field  = missing["position"].str.lower().str.contains("|".join(field_kw), na=False)

backoffice  = missing[~is_field]
field_staff = missing[is_field]

print("=" * 50)
print("ИТОГ ПО КАТЕГОРИЯМ")
print("=" * 50)
print(f"Полевой персонал (мерч/СВ) без ОЭД: {len(field_staff)}")
print(f"Бэк-офис и другие без ОЭД:           {len(backoffice)}")
print()

# ── Стаж — полевые без ОЭД ───────────────────────────────
print("=" * 50)
print("СТАЖ ПОЛЕВЫХ БЕЗ ОЭД")
print("=" * 50)
bins   = [-1, 30, 90, 180, 365, 99999]
labels = ["до 1 мес", "1-3 мес", "3-6 мес", "6-12 мес", "более года"]
field_staff = field_staff.copy()
field_staff["tenure_group"] = pd.cut(field_staff["tenure_days"], bins=bins, labels=labels)
print(field_staff["tenure_group"].value_counts().sort_index().to_string())
print()
recently = (field_staff["tenure_days"] <= 90).sum()
old_field = (field_staff["tenure_days"] > 90).sum()
print(f"Новички до 3 мес (не попали в оценку - норма): {recently}")
print(f"Стаж > 3 мес (должны быть в ОЭД - странно):   {old_field}")

# ── Детали полевых с большим стажем ──────────────────────
if old_field > 0:
    print()
    print("Полевые с большим стажем без ОЭД:")
    show = field_staff[field_staff["tenure_days"] > 90][
        ["employee_id", "full_name", "position", "tenure_days", "city", "hire_date"]
    ].sort_values("tenure_days", ascending=False)
    print(show.to_string())

# ── Должности бэк-офиса ───────────────────────────────────
print()
print("=" * 50)
print("ДОЛЖНОСТИ БЭК-ОФИСА (без ОЭД — норма)")
print("=" * 50)
print(backoffice["position"].value_counts().to_string())
