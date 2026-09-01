import argparse
import getpass

from scripts.cisco_vpn import (
    _vpn_config,
    delete_stored_credentials,
    get_stored_credentials,
    store_credentials,
)
from scripts.utils import load_settings


def _set_credentials(config: dict) -> None:
    current = get_stored_credentials(config)
    current_hint = f" [{current.username}]" if current else ""
    username = input(f"Введите логин Cisco VPN{current_hint}: ").strip()
    if not username and current:
        username = current.username
    password = getpass.getpass("Введите пароль Cisco VPN: ")
    confirmation = getpass.getpass("Повторите пароль Cisco VPN: ")
    if password != confirmation:
        raise ValueError("Пароли не совпадают")
    store_credentials(config, username, password)
    print("Учётные данные Cisco VPN сохранены в Windows Credential Manager")


def _show_status(config: dict) -> None:
    if get_stored_credentials(config):
        print("Учётные данные Cisco VPN сохранены в Windows Credential Manager")
    else:
        print("Учётные данные Cisco VPN в Windows Credential Manager не найдены")


def main() -> None:
    parser = argparse.ArgumentParser(description="Управление учётными данными Cisco VPN")
    parser.add_argument("action", choices=["set", "status", "delete"])
    arguments = parser.parse_args()

    config = _vpn_config(load_settings())
    if arguments.action == "set":
        _set_credentials(config)
    elif arguments.action == "status":
        _show_status(config)
    elif delete_stored_credentials(config):
        print("Учётные данные Cisco VPN удалены из Windows Credential Manager")
    else:
        print("Учётные данные Cisco VPN в Windows Credential Manager не найдены")


if __name__ == "__main__":
    main()

