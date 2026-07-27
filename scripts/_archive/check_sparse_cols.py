import sys, pandas as pd
sys.stdout.reconfigure(encoding="utf-8")

df = pd.read_parquet("data/out/okk_fact.parquet")

print("Колонки по % заполненности (от худших к лучшим):")
print("=" * 70)
print(f"{'Колонка':<55} {'Заполнено':>8}  {'Строк':>7}")
print("-" * 70)

stats = []
for c in df.columns:
    filled  = df[c].notna().sum()
    pct     = filled / len(df) * 100
    stats.append((c, filled, pct))

stats.sort(key=lambda x: x[2])

for col, filled, pct in stats:
    bar = "█" * int(pct / 5)
    print(f"{col:<55} {pct:6.0f}%   {filled:>7}")
