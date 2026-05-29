from scripts.oed_parser import parse_oed
from scripts.okk_parser import parse_okk
from scripts.learning_parser import parse_learning
from scripts.kpi_parser import parse_kpi
from scripts.users_parser import parse_users
from scripts.enps_parser import parse_enps
from scripts.attestations_parser import parse_attestations


def run_all() -> None:
    print("=== Запуск всех парсеров ===")

    print("[1/7] ОЭД...")
    parse_oed()

    print("[2/7] ОКК...")
    parse_okk()

    print("[3/7] Обучение...")
    parse_learning()

    print("[4/7] KPI...")
    parse_kpi()

    print("[5/7] USERS...")
    parse_users()

    print("[6/7] ENPS...")
    parse_enps()

    print("[7/7] АТТЕСТАЦИИ...")
    parse_attestations()

    print("=== Готово ===")


if __name__ == "__main__":
    run_all()
