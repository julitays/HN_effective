import sys, pandas as pd
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")

new_path = sorted(Path("data/raw/users").rglob("*2026-06-03*.csv"))[-1]
print(f"Файл: {new_path.name}")
new_df = pd.read_csv(new_path, sep=";", encoding="utf-8", dtype=str)
new_df["_id"] = new_df["Внешний идентификатор"].str.strip().str.upper()
print(f"Новый файл: {len(new_df)} строк, {new_df['_id'].nunique()} уникальных ID")
print(f"Авторизация: {new_df['Авторизация'].value_counts().to_dict()}")
print()

# Несовпавшие из learning
dim    = pd.read_parquet("data/out/dim_employees.parquet")
dim_ids = set(dim["ID сотрудника"].dropna())

learn_ids_missing = set()
for xlsx in Path("data/raw/learning").rglob("*.xlsx"):
    try:
        raw = pd.read_excel(xlsx, dtype=str, usecols=["extId"])
        learn_ids_missing |= set(raw["extId"].str.strip().str.upper().dropna())
    except Exception:
        pass
learn_ids_missing -= dim_ids

print(f"Несовпавших из learning: {len(learn_ids_missing)}")
found = new_df[new_df["_id"].isin(learn_ids_missing)]
print(f"Найдено в новом файле: {len(found)}")
if not found.empty:
    print(found[["Внешний идентификатор","Фамилия","Проект","Авторизация"]].head(10).to_string())
