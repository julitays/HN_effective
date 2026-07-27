import sys, pandas as pd
sys.stdout.reconfigure(encoding="utf-8")

# id — см. config/settings.yml (vacant_tm/rm_manager_id, rm_branch_manager_id)
MANAGERS = {
    "109-352-710-45": "vacant_tm_manager_id",
    "172-922-951-88": "vacant_rm_manager_id",
    "143-211-040-91": "rm_branch_manager_id",
    "100-904-295-07": "ТМ 1",
    "132-562-013-19": "ТМ 2",
}

# 1. Проверяем block/ файл
print("=== В block/ файле ===")
block = pd.read_csv("data/raw/users/block/Пользователи_2026-06-01.csv", sep=";", encoding="utf-8", dtype=str)
for mid, name in MANAGERS.items():
    row = block[block["Внешний идентификатор"].str.strip().str.upper() == mid]
    if not row.empty:
        proj = row.iloc[0].get("Проект", "?")
        print(f"  {name} ({mid}): ЕСТЬ | Проект={proj}")
    else:
        print(f"  {name} ({mid}): нет в block/")

print()
# 2. Пробуем rebuild и смотрим промежуточные шаги
print("=== Проверка parse_users() ===")
from scripts.parsers.users_parser import parse_users
dim = parse_users()
for mid, name in MANAGERS.items():
    row = dim[dim["employee_id"] == mid]
    if not row.empty:
        r = row.iloc[0]
        print(f"  {name} ({mid}): ✓ is_active={r.get('is_active')}")
    else:
        print(f"  {name} ({mid}): ❌ НЕТ в dim!")
