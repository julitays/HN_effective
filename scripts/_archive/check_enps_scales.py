import sys, pandas as pd
sys.stdout.reconfigure(encoding="utf-8")

df = pd.read_excel("data/raw/enps/2026-03-05 ДанонОпрос удовлетворенности ПромоМЕ.xlsx", dtype=str, nrows=3)
# Ищем колонки про рост/карьеру/развитие
for col in df.columns:
    cl = str(col).lower()
    if any(k in cl for k in ["развити","карьер","рост","реализ","крите","профессион"]):
        val = df[col].dropna().iloc[0] if df[col].notna().any() else "null"
        print(f"  {col[:70]} | {str(val)[:25]}")
