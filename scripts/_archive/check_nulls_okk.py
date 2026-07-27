import sys, pandas as pd
sys.stdout.reconfigure(encoding="utf-8")

df = pd.read_parquet("data/out/okk_fact.parquet")

# Переименуем обратно для анализа (используем русские имена)
print(f"Строк: {len(df)} | Периодов: {df['Период'].nunique()}")
print()

# Колонки с >20% пустых
high_null = [(c, df[c].isna().mean()*100)
             for c in df.columns
             if df[c].isna().mean() > 0.20]
high_null.sort(key=lambda x: x[1], reverse=True)

print(f"Колонок с >20% пустых: {len(high_null)}")
print()

# Для каждой такой колонки — % пустых по периодам
print("=" * 70)
print("NULL по периодам для колонок с >20% пустых")
print("=" * 70)

periods = sorted(df["Период"].unique())

for col, total_pct in high_null:
    print(f"\n{col}  (всего пустых: {total_pct:.0f}%)")
    for p in periods:
        sub  = df[df["Период"] == p]
        pct  = sub[col].isna().mean() * 100
        n    = len(sub)
        flag = " ← 100% пустых (колонка отсутствует в файле)" if pct == 100 else (
               " ← частично пустых" if pct > 5 else "")
        if pct > 5:
            print(f"  {p}: {pct:5.0f}% пустых  (строк: {n}){flag}")
