import sys, pandas as pd
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")
from scripts.parsers.okk_parser import _clean_col_header, _map_columns, _find_detail_sheet

TARGET_PATTERNS = {
    "ФОТО ОБЩЕЕ": ["% качества фото", "% качества выполнения", "средний % качества"],
    "PICOS %":    ["picos", "% наличие picos", "% качества picos"],
    "OSA":        ["osa"],
}

for xlsx in sorted(Path("data/raw/okk").rglob("*.xlsx")):
    xl    = pd.ExcelFile(xlsx)
    sheet = _find_detail_sheet(xl)
    raw   = xl.parse(sheet, nrows=3, dtype=str)

    period = xlsx.parent.name + "/" + xlsx.stem[:20]
    found = {}
    for group, keywords in TARGET_PATTERNS.items():
        cols = [c for c in raw.columns
                if any(k in _clean_col_header(c) for k in keywords)]
        found[group] = cols

    has_issues = any(len(v) == 0 for v in found.values())
    status = "ПРОПУСК" if has_issues else "ок"
    print(f"[{status}] {period}")
    for group, cols in found.items():
        if len(cols) == 0:
            print(f"         ❌ {group}: нет!")
        else:
            for c in cols[:2]:
                print(f"         ✓ {group}: {repr(c)[:60]}")
    print()
