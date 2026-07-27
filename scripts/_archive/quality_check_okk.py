import sys, pandas as pd
sys.stdout.reconfigure(encoding="utf-8")

df = pd.read_parquet("data/out/okk_fact.parquet")

# 1. Визиты без даты аудита
no_date = df[df["audit_date"].isna()]
print(f"Без audit_date: {len(no_date)} строк")
if len(no_date):
    print("Источники (file_source):")
    print(no_date["file_source"].value_counts().to_string())
    print("Пример:")
    cols = ["file_source","period","region","store_sap_id","sv_name_raw","audit_date"]
    print(no_date[cols].head(5).to_string())

print()

# 2. Визиты без store_sap_id
no_sap = df[df["store_sap_id"].isna() | (df["store_sap_id"] == "")]
print(f"Без store_sap_id: {len(no_sap)} строк")
if len(no_sap):
    print("Источники:")
    print(no_sap["file_source"].value_counts().to_string())

print()

# 3. has_falsification: пустые и противоречия
null_fals = df["has_falsification"].isna().sum()
print(f"has_falsification = null: {null_fals}")
conflict  = df[df["has_falsification"].eq(False) & (df["falsification_count"] > 0)]
print(f"Противоречие (FALSE но count > 0): {len(conflict)}")
if len(conflict):
    print(conflict[["file_source","period","has_falsification","falsification_count","falsification_notes"]].head(5).to_string())

print()

# 4. Технические колонки
tech = [c for c in df.columns if c.endswith("_match_method") or c in ("file_source",)]
print(f"Технические колонки: {tech}")
