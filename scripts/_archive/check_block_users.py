import sys, pandas as pd
sys.stdout.reconfigure(encoding="utf-8")
from scripts.parsers.users_parser import parse_users

dim = parse_users()
print()

dim_df = pd.read_parquet("data/out/dim_employees.parquet")
print(f"dim_employees: {len(dim_df)} записей")
if "is_active" in dim_df.columns:
    active   = dim_df["is_active"].eq(True).sum()
    inactive = dim_df["is_active"].eq(False).sum()
    print(f"  is_active = True:  {active} (текущие)")
    print(f"  is_active = False: {inactive} (заблокированные/исторические)")
else:
    print("  Колонка is_active не найдена!")
