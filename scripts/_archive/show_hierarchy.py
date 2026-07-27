import pandas as pd

dim    = pd.read_parquet("data/out/dim_employees.parquet")
lookup = dim.set_index("employee_id").to_dict("index")

chain = [
    "132-562-013-19",  # TM
    "109-352-710-45",  # директор над ТМ
    "178-016-434-71",  # выше
    "153-852-703-74",  # выше
    "127-768-566-09",  # выше
    "128-706-070-60",  # выше (Исполнительный директор)
]

print("=" * 60)
print("ЦЕПОЧКА УПРАВЛЕНИЯ: TM -> верх структуры")
print("=" * 60)
for i, eid in enumerate(chain):
    row  = lookup.get(eid, {})
    name = row.get("full_name", "??? не найден")
    pos  = row.get("position", "???")
    org  = row.get("org_unit", "???")
    mgr  = row.get("manager_full_name", "???")
    indent = "  " * i
    print(f"{indent}[Уровень {i+1}] {name}")
    print(f"{indent}            Должность: {pos}")
    print(f"{indent}            Отдел:     {org}")
    print(f"{indent}            Руководит: {mgr}")
    print()

print("=" * 60)
print("ПАРАЛЛЕЛЬНАЯ ВЕТКА: RM напрямую над СВ")
print("=" * 60)
rm     = lookup.get("143-211-040-91", {})
rm_mgr_id = rm.get("manager_id", "")
rm_mgr = lookup.get(rm_mgr_id, {})
print(f"  RM:              {rm.get('full_name')}")
print(f"  Должность:       {rm.get('position')}")
print(f"  Его руководитель:{rm.get('manager_full_name')} ({rm_mgr.get('position','?')})")
