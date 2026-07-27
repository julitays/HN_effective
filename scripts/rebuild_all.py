import sys, pandas as pd
sys.stdout.reconfigure(encoding="utf-8")
from scripts.parsers.users_parser import parse_users
from scripts.parsers.oed_parser import parse_oed
from scripts.builders.teams_builder import build_teams
from scripts.parsers.okk_parser import parse_okk
from scripts.parsers.learning_parser import parse_learning

dim = parse_users()
print()

print("=== ОЭД ===")
parse_oed(dim=dim)

print()
print("=== Справочник команд ===")
build_teams(dim=dim)

print()
print("=== ОКК ===")
parse_okk(dim=dim)

print()
print("=== Обучение ===")
parse_learning(dim=dim)

print()
print("=== ИТОГОВАЯ СТАТИСТИКА МАТЧИНГА ===")

fact_oed = pd.read_parquet("data/out/fact_oed.parquet")
sv_oed   = "ID сотрудника"
matched_oed = fact_oed[sv_oed].notna().sum() if sv_oed in fact_oed.columns else 0
total_oed   = len(fact_oed)
print(f"ОЭД:  {matched_oed}/{total_oed} ({matched_oed/total_oed*100:.1f}%)")

fact_okk = pd.read_parquet("data/out/okk_fact.parquet")
sv_col = "ID супервайзера"
me_col = "ID мерчендайзера"
if sv_col in fact_okk.columns:
    matched_sv = fact_okk[sv_col].notna().sum()
    total_okk  = len(fact_okk)
    print(f"ОКК СВ: {matched_sv}/{total_okk} ({matched_sv/total_okk*100:.1f}%)")
if me_col in fact_okk.columns:
    matched_me = fact_okk[me_col].notna().sum()
    print(f"ОКК Мерч: {matched_me}/{total_okk} ({matched_me/total_okk*100:.1f}%)")
