import sys, pandas as pd
sys.stdout.reconfigure(encoding="utf-8")

dim  = pd.read_parquet("data/out/dim_employees.parquet")
oed  = pd.read_parquet("data/out/fact_oed.parquet")
okk  = pd.read_parquet("data/out/okk_fact.parquet")

print("=== dim_employees ===")
active   = dim["is_active"].eq(True).sum()
inactive = dim["is_active"].eq(False).sum()
print(f"Всего: {len(dim)} | Активных: {active} | Архивных: {inactive}")
print()

print("=== Матчинг ОЭД ===")
total = len(oed)
matched = oed["employee_id"].notna().sum()
unmatched = oed["employee_id"].isna().sum()
print(f"Всего строк: {total}")
print(f"Совпало: {matched} ({matched/total*100:.1f}%)")
print(f"Не совпало: {unmatched} ({unmatched/total*100:.1f}%)")
print()
print("По периодам:")
for p, grp in oed.groupby("period"):
    m = grp["employee_id"].notna().mean()*100
    print(f"  {p}: {m:.0f}%")

print()
print("=== Матчинг ОКК ===")
total_okk = len(okk)
sv_matched  = okk["ID супервайзера"].notna().sum()
me_matched  = okk["ID мерчендайзера"].notna().sum()
print(f"Всего строк: {total_okk}")
print(f"ID СВ:    {sv_matched} ({sv_matched/total_okk*100:.1f}%)")
print(f"ID Мерча: {me_matched} ({me_matched/total_okk*100:.1f}%)")
print()
print("ID СВ по периодам:")
for p, grp in okk.groupby("Период"):
    m = grp["ID супервайзера"].notna().mean()*100
    print(f"  {p}: {m:.0f}%")
