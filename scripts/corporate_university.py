import getpass
import os
import sys

import pandas as pd


_PASSWORD_CACHE: str | None = None


def _database_config(settings: dict) -> dict:
    config = settings.get("corporate_university", {})
    required = ["dsn", "user", "password_env"]
    missing = [key for key in required if not str(config.get(key, "")).strip()]
    if missing:
        raise ValueError(
            "Не заполнены настройки корпоративного университета: "
            + ", ".join(missing)
        )
    return config


def _credential_key(config: dict) -> tuple[str, str]:
    service = str(config.get("credential_service", config["dsn"])).strip()
    user = str(config["user"]).strip()
    return service, user


def _keyring_module():
    try:
        import keyring
    except ImportError as exc:
        raise RuntimeError(
            "Не установлен пакет keyring. Выполните в PowerShell: "
            ".\\.venv\\Scripts\\python.exe -m pip install -r requirements.txt"
        ) from exc
    return keyring


def get_stored_password(config: dict) -> str:
    service, user = _credential_key(config)
    password = _keyring_module().get_password(service, user)
    return password or ""


def store_password(config: dict, password: str) -> None:
    if not password:
        raise ValueError("Пустой пароль сохранять нельзя")
    service, user = _credential_key(config)
    _keyring_module().set_password(service, user, password)


def delete_stored_password(config: dict) -> bool:
    keyring = _keyring_module()
    service, user = _credential_key(config)
    if not keyring.get_password(service, user):
        return False
    keyring.delete_password(service, user)
    return True


def clear_password_cache() -> None:
    global _PASSWORD_CACHE
    _PASSWORD_CACHE = None


def _get_password(config: dict) -> str:
    global _PASSWORD_CACHE
    if _PASSWORD_CACHE:
        return _PASSWORD_CACHE

    password = get_stored_password(config)
    password_env = str(config["password_env"]).strip()
    if not password:
        password = os.environ.get(password_env, "")
    if not password:
        if not sys.stdin.isatty():
            raise RuntimeError(
                "Пароль MariaDB не найден в Windows Credential Manager. "
                "Сохраните его командой: .\\.venv\\Scripts\\python.exe "
                "-m scripts.manage_corporate_university_credential set"
            )
        password = getpass.getpass("Пароль корпоративного университета: ")
    if not password:
        raise RuntimeError("Пароль MariaDB не введён")

    _PASSWORD_CACHE = password
    return password


def connect(settings: dict):
    try:
        import pyodbc
    except ImportError as exc:
        raise RuntimeError(
            "Не установлен пакет pyodbc. Выполните в PowerShell: "
            ".\\.venv\\Scripts\\python.exe -m pip install -r requirements.txt"
        ) from exc

    config = _database_config(settings)
    password = _get_password(config)
    connection_string = (
        f"DSN={config['dsn']};"
        f"UID={config['user']};"
        f"PWD={password};"
        "READONLY=1;"
    )
    connection = pyodbc.connect(
        connection_string,
        autocommit=True,
        timeout=int(config.get("connect_timeout_seconds", 15)),
    )
    connection.timeout = int(config.get("query_timeout_seconds", 180))
    return connection


def read_sql(settings: dict, query: str, params: tuple | list = ()) -> pd.DataFrame:
    with connect(settings) as connection:
        cursor = connection.cursor()
        cursor.execute(query, tuple(params))
        columns = [description[0] for description in cursor.description]
        rows = cursor.fetchall()
    return pd.DataFrame.from_records(rows, columns=columns)
