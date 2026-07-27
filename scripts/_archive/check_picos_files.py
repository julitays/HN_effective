import sys, pandas as pd
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")
from scripts.parsers.okk_parser import _clean_col_header, _find_detail_sheet

PICOS_KEYWORDS = ["picos", "% наличие picos", "наличие picos", "picos%"]

for xlsx in sorted(Path("data/raw/okk").rglob("*.xlsx")):
    xl    = pd.ExcelFile(xlsx)
    sheet = _find_detail_sheet(xl)
    raw   = xl.parse(sheet, nrows=3, dtype=str)

    picos_cols = [c for c in raw.columns
                  if any(k in _clean_col_header(c) for k in PICOS_KEYWORDS)]

    status = "✓ PICOS есть" if picos_cols else "❌ PICOS нет"
    print(f"{status}  |  {xlsx.parent.name}/{xlsx.name}")
    for c in picos_cols[:2]:
        print(f"          → {repr(c)[:65]}")
