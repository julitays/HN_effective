import argparse
from pathlib import Path

from scripts.cisco_vpn import managed_database_vpn
from scripts.production import validate_publish_target
from scripts.run_all_parsers import run_all
from scripts.utils import load_settings


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Полный ETL с автоматическим подключением корпоративного VPN"
    )
    parser.add_argument("--as-of-date", help="Фиксированная дата расчёта YYYY-MM-DD")
    parser.add_argument(
        "--direct",
        action="store_true",
        help="Запись напрямую без staging; только для локальной диагностики",
    )
    arguments = parser.parse_args()

    settings = load_settings()
    if not arguments.direct:
        validate_publish_target(Path(settings["paths"]["out"]))
    with managed_database_vpn(settings):
        run_all(
            transactional=not arguments.direct,
            as_of_date=arguments.as_of_date,
        )
    print("ETL завершён, временное VPN-подключение закрыто.")


if __name__ == "__main__":
    main()
