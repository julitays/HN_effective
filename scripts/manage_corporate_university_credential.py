import argparse
import getpass

from scripts.corporate_university import (
    _database_config,
    clear_password_cache,
    delete_stored_password,
    get_stored_password,
    read_sql,
    store_password,
)
from scripts.utils import load_settings


def _set_password(config: dict) -> None:
    password = getpass.getpass("Введите пароль корпоративного университета: ")
    confirmation = getpass.getpass("Повторите пароль: ")
    if password != confirmation:
        raise ValueError("Пароли не совпадают")
    store_password(config, password)
    clear_password_cache()
    print("Пароль сохранён в Windows Credential Manager")


def _show_status(config: dict) -> None:
    if get_stored_password(config):
        print("Пароль сохранён в Windows Credential Manager")
    else:
        print("Пароль в Windows Credential Manager не найден")


def _test_connection(settings: dict) -> None:
    clear_password_cache()
    result = read_sql(settings, "SELECT 1 AS connection_ok")
    if result.empty or int(result.iloc[0]["connection_ok"]) != 1:
        raise RuntimeError("База данных вернула неожиданный ответ")
    print("Подключение к корпоративному университету работает")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Управление паролем корпоративного университета"
    )
    parser.add_argument("action", choices=["set", "status", "test", "delete"])
    arguments = parser.parse_args()

    settings = load_settings()
    config = _database_config(settings)

    if arguments.action == "set":
        _set_password(config)
    elif arguments.action == "status":
        _show_status(config)
    elif arguments.action == "test":
        _test_connection(settings)
    elif delete_stored_password(config):
        clear_password_cache()
        print("Пароль удалён из Windows Credential Manager")
    else:
        print("Пароль в Windows Credential Manager не найден")


if __name__ == "__main__":
    main()
