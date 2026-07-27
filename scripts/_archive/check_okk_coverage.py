import sys, pandas as pd
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")
from scripts.parsers.okk_parser import _clean_col_header, _find_detail_sheet, _map_columns, NOT_CHECK

# Собираем ВСЕ уникальные колонки из всех файлов (после дедупликации и маппинга)
all_raw_cols: dict[str, list[str]] = {}  # clean_name → [файлы где встречается]

for xlsx in sorted(Path("data/raw/okk").rglob("*.xlsx")):
    xl    = pd.ExcelFile(xlsx)
    sheet = _find_detail_sheet(xl)
    raw   = xl.parse(sheet, nrows=3, dtype=str)

    # Дедупликация как в парсере
    seen: dict = {}
    dedup = []
    for col in raw.columns:
        key = _clean_col_header(col)
        if key in seen:
            seen[key] += 1
            dedup.append(f"{col}.{seen[key]}")
        else:
            seen[key] = 0
            dedup.append(col)
    raw.columns = dedup

    col_map = _map_columns(raw)

    for col in raw.columns:
        mapped = col_map.get(col, col)  # имя после маппинга
        clean  = _clean_col_header(col)
        if clean not in all_raw_cols:
            all_raw_cols[clean] = []
        all_raw_cols[clean].append(f"{xlsx.parent.name}/{xlsx.name}")

# Что есть в маппинге
mapping = pd.read_excel("config/okk_columns_map.xlsx", dtype=str)
mapping = mapping[mapping["Сокращение в файле"].notna()].copy()
mapped_shorts = set(mapping["Сокращение в файле"].str.strip())

# Что знает парсер (NOT_CHECK — именованные колонки)
known = NOT_CHECK | mapped_shorts

# Ищем колонки из файлов, которые НЕ попали в маппинг
# Используем col_map чтобы узнать итоговое имя
print("=" * 65)
print("КОЛОНКИ ИЗ ФАЙЛОВ, КОТОРЫХ НЕТ В МАППИНГЕ config/okk_columns_map.xlsx")
print("=" * 65)

missing = []
for xlsx in sorted(Path("data/raw/okk").rglob("*.xlsx")):
    xl    = pd.ExcelFile(xlsx)
    sheet = _find_detail_sheet(xl)
    raw   = xl.parse(sheet, nrows=3, dtype=str)

    seen: dict = {}
    dedup = []
    for col in raw.columns:
        key = _clean_col_header(col)
        if key in seen:
            seen[key] += 1
            dedup.append(f"{col}.{seen[key]}")
        else:
            seen[key] = 0
            dedup.append(col)
    raw.columns = dedup

    col_map = _map_columns(raw)
    raw2    = raw.rename(columns=col_map)

    for orig_col, mapped_col in col_map.items():
        if mapped_col not in mapped_shorts and mapped_col not in NOT_CHECK:
            key = (mapped_col, orig_col[:60])
            if key not in missing:
                missing.append(key)

if missing:
    for mapped, orig in missing:
        print(f"  сокращение: {mapped!r}")
        print(f"  оригинал:   {orig!r}")
        print()
else:
    print("Все именованные колонки присутствуют в маппинге!")

print()
print("=" * 65)
print("ИТОГО: именованных колонок в файлах:", len(NOT_CHECK))
print("Колонок в маппинге:", len(mapped_shorts))
