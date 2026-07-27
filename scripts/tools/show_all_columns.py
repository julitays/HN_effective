import sys, pandas as pd
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")

FILES = {
    "dim_employees":  "data/out/dim_employees.parquet",
    "dim_teams":      "data/out/dim_teams.parquet",
    "fact_oed":       "data/out/fact_oed.parquet",
    "fact_okk":       "data/out/okk_fact.parquet",
    "fact_learning":  "data/out/learning_fact.parquet",
}

for name, path in FILES.items():
    if not Path(path).exists():
        print(f"{name}: ФАЙЛ НЕ НАЙДЕН\n")
        continue
    df = pd.read_parquet(path)
    print(f"{'='*60}")
    print(f"{name}  ({len(df)} строк, {len(df.columns)} колонок)")
    print(f"{'='*60}")
    for col in df.columns:
        dtype = str(df[col].dtype)
        sample = df[col].dropna().iloc[0] if df[col].notna().any() else "null"
        print(f"  {col:<40} {dtype:<15} | {str(sample)[:30]}")
    print()
