import sys, pandas as pd
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")

if __package__ is None or __package__ == "":
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from scripts.parsers.okk_parser import _map_columns, _find_detail_sheet, _normalize_percent

df = pd.read_parquet("data/out/okk_fact.parquet")

print("=" * 60)
print("1. ОБЩАЯ СТАТИСТИКА")
print("=" * 60)
print(f"Строк: {len(df)} | Периодов: {df['Период'].nunique()} | Колонок: {len(df.columns)}")
print()

print("=" * 60)
print("2. NULL по ключевым ID")
print("=" * 60)
for col in ("ID супервайзера", "ID мерчендайзера", "ID ТМ"):
    total  = len(df)
    nulls  = df[col].isna().sum()
    pct    = nulls / total * 100
    uniq   = df[col].nunique()
    print(f"  {col}: {nulls} null ({pct:.0f}%) | уникальных ID: {uniq}")

print()
print("  Разбивка ID супервайзера по периодам:")
for p, grp in df.groupby("Период"):
    pct = grp["ID супервайзера"].isna().mean() * 100
    if pct > 10:
        print(f"    {p}: {pct:.0f}% null")

print()
print("=" * 60)
print("3. ПРАВИЛА ФОТОГРАФИРОВАНИЯ: ОБЩЕЕ КАЧЕСТВО")
print("=" * 60)
col = "Правила фотографирования: общее качество"
vals = df[col].dropna()
print(f"Всего значений: {len(vals)} | null: {df[col].isna().sum()}")
print(f"  min: {vals.min():.4f}")
print(f"  max: {vals.max():.4f}")
print(f"  mean: {vals.mean():.4f}")
print(f"  Уникальных значений (топ-10): {sorted(vals.unique()[:10].tolist())[:10]}")
print()
# Проверяем — есть ли бинарные (только 0 и 1)?
binary_count = ((vals == 0) | (vals == 1)).sum()
decimal_count = ((vals > 0) & (vals < 1)).sum()
print(f"  Бинарных (0 или 1): {binary_count} ({binary_count/len(vals)*100:.0f}%)")
print(f"  Дробных (0<x<1):    {decimal_count} ({decimal_count/len(vals)*100:.0f}%)")

print()
# Смотрим источник в файлах
print("  Откуда берётся в каждом формате:")
for xlsx, label in [
    ("data/raw/okk/2025/Сводная H&N МАЙ 02.06 Закрыт.xlsx", "2025 W0"),
    ("data/raw/okk/2025/Сводная H&N АВГУСТ 08.09 Закрыт.xlsx", "2025 Анкеты"),
    ("data/raw/okk/2026/Сводная H&N МАРТ Закрыт.xlsx", "2026 Анкеты"),
]:
    xl    = pd.ExcelFile(xlsx)
    sheet = _find_detail_sheet(xl)
    raw   = xl.parse(sheet, nrows=20, dtype=str)
    col_map = _map_columns(raw)
    src_col = next((k for k,v in col_map.items() if v == "pct_фото_правила"), None)
    if src_col:
        vals_src = _normalize_percent(raw[src_col]).dropna()
        b = ((vals_src == 0) | (vals_src == 1)).sum()
        d = ((vals_src > 0) & (vals_src < 1)).sum()
        print(f"  [{label}] '{src_col[:45]}': бинарных={b}, дробных={d}, mean={vals_src.mean():.3f}")
    else:
        print(f"  [{label}]: колонка не найдена")

print()
print("=" * 60)
print("4. ФИНАЛЬНЫЕ NULL ПО ВСЕМ МЕТРИКАМ")
print("=" * 60)
metric_cols = [c for c in df.columns
               if c not in ("Период","Год","Месяц","Дата визита","Регион",
                             "Код ТТ","Сеть","Адрес","ID супервайзера",
                             "ID ТМ","ID мерчендайзера","Супервайзер",
                             "Территориальный менеджер","Мерчендайзер")]
for c in metric_cols:
    pct = df[c].isna().mean() * 100
    flag = " ← структурный пробел (нет в источнике)" if pct > 50 else ""
    print(f"  {c:<55} {pct:4.0f}%{flag}")
