import sys, pandas as pd
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")

block_df  = pd.read_csv("data/raw/users/block/Пользователи_2026-06-01.csv", sep=";", encoding="utf-8", dtype=str)
active_df = pd.read_csv(sorted(Path("data/raw/users/active").glob("*.csv"))[-1], sep=";", encoding="utf-8", dtype=str)
block_df["_id"]  = block_df["Внешний идентификатор"].str.strip().str.upper()
active_df["_id"] = active_df["Внешний идентификатор"].str.strip().str.upper()

dim_ids = set(pd.read_parquet("data/out/dim_employees.parquet")["employee_id"])

# Собираем несовпавшие ID из ОЭД-файлов
nowhere_ids = set()
for xlsx in sorted(Path("data/raw/oed").rglob("*.xlsx")):
    raw = pd.ExcelFile(xlsx).parse(pd.ExcelFile(xlsx).sheet_names[0], dtype=str)
    if "ID" not in raw.columns:
        continue
    raw_ids = set(raw["ID"].str.strip().str.upper().dropna())
    nowhere_ids |= (raw_ids - dim_ids)

print(f"Несовпавших ID из ОЭД: {len(nowhere_ids)}")
print()

# Ищем в block/ БЕЗ фильтра
found_in_block  = block_df[block_df["_id"].isin(nowhere_ids)]
found_in_active = active_df[active_df["_id"].isin(nowhere_ids)]

print(f"Найдено в block/ (все проекты): {len(found_in_block)}")
if not found_in_block.empty:
    print("Проекты:")
    print(found_in_block["Проект"].value_counts().to_string())
    print()
    print("Примеры:")
    print(found_in_block[["Внешний идентификатор","Фамилия","Имя","Должность","Проект"]].head(10).to_string())

print()
print(f"Найдено в active/ (все проекты): {len(found_in_active)}")
if not found_in_active.empty:
    print("Проекты:")
    print(found_in_active["Проект"].value_counts().to_string())
