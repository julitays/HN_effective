import sys, pandas as pd
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")
from scripts.parsers.okk_parser import _map_columns, _find_detail_sheet, _clean_col_header, NOT_CHECK

df = pd.read_parquet("data/out/okk_fact.parquet")

print("=== check_01 в витрине ===")
col = "check_01"
vals = df[col].dropna()
print(f"Строк с данными: {len(vals)} из {len(df)}")
print(f"Значения: {sorted(vals.unique().tolist())}")
print(f"Распределение: {vals.value_counts().to_dict()}")
print()
print("По периодам:")
for p, grp in df.groupby("Период"):
    has = grp[col].notna().sum()
    if has > 0:
        vals_p = grp[col].dropna()
        print(f"  {p}: {has} строк | значения: {vals_p.value_counts().to_dict()}")

print()
print("=== Источник: ищем в сырых файлах ===")
for xlsx in sorted(Path("data/raw/okk").rglob("*.xlsx")):
    xl    = pd.ExcelFile(xlsx)
    sheet = _find_detail_sheet(xl)
    raw   = xl.parse(sheet, nrows=5, dtype=str)

    # Дедупликация
    seen: dict = {}
    dedup = []
    for c in raw.columns:
        key = _clean_col_header(c)
        if key in seen:
            seen[key] += 1
            dedup.append(f"{c}.{seen[key]}")
        else:
            seen[key] = 0
            dedup.append(c)
    raw.columns = dedup

    col_map = _map_columns(raw)
    raw2    = raw.rename(columns=col_map)

    unnamed = [c for c in raw2.columns
               if c not in NOT_CHECK
               and c not in ("sv_name_raw","tm_name_raw","me_name_raw","audit_date")
               and pd.to_numeric(raw2[c], errors="coerce").notna().any()]
    if unnamed:
        import os
        print(f"{os.path.basename(xlsx)}:")
        for c in unnamed:
            orig = next((k for k,v in col_map.items() if v == c), c)
            vals_raw = pd.to_numeric(raw2[c], errors="coerce").dropna()
            print(f"  '{orig}'  ->  значения: {sorted(vals_raw.unique().tolist())[:5]}")
