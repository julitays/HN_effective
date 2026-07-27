import sys, warnings, pandas as pd
warnings.filterwarnings("ignore")
sys.stdout.reconfigure(encoding="utf-8")
from scripts.parsers.users_parser import parse_users
from scripts.parsers.learning_parser import parse_learning

dim = parse_users()
parse_learning(dim=dim)

df = pd.read_parquet("data/out/learning_fact.parquet")
print(f"Строк: {len(df)} | Колонок: {len(df.columns)}")
print(f"Колонки: {list(df.columns)}")
print()
uch_col = "Ученический договор"
print(f"{uch_col}:")
print(df[uch_col].value_counts(dropna=False).to_string())
uch_people = df[df[uch_col] == True]["ID сотрудника"].nunique()
print(f"  Уникальных на учен. договоре: {uch_people}")
