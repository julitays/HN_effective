import sys, pandas as pd
sys.stdout.reconfigure(encoding="utf-8")

df = pd.read_excel("config/okk_columns_map.xlsx", dtype=str)

REMOVE = {
    "хо_фирменные_качество",
    "pct_фото_доп",
    "picos_планто",
    "анкета_теплая_полка",
    "анкета_холодная_полка",
}

changed = 0
for i, row in df.iterrows():
    short = str(row.get("Сокращение в файле", "") or "").strip()
    if short in REMOVE:
        df.at[i, "Берем в витрину или нет"] = "Нет"
        name = str(row.get("Необходимое название в файле", "") or "")
        print(f"  Нет: {short}  ({name})")
        changed += 1

df.to_excel("config/okk_columns_map.xlsx", index=False)
print(f"\nОбновлено {changed} строк -> config/okk_columns_map.xlsx")
