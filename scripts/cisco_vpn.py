from __future__ import annotations

import re
import socket
import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


_USERNAME_ENTRY = "__vpn_username__"


@dataclass(frozen=True)
class VpnCredentials:
    username: str
    password: str


def _vpn_config(settings: dict) -> dict:
    config = settings.get("vpn", {})
    required = [
        "host",
        "credential_service",
        "cli_path",
        "ui_path",
        "database_host",
        "database_port",
    ]
    missing = [key for key in required if not str(config.get(key, "")).strip()]
    if missing:
        raise ValueError("Не заполнены настройки VPN: " + ", ".join(missing))
    return config


def _keyring_module():
    try:
        import keyring
    except ImportError as exc:
        raise RuntimeError(
            "Не установлен пакет keyring. Выполните в PowerShell: "
            ".\\.venv\\Scripts\\python.exe -m pip install -r requirements.txt"
        ) from exc
    return keyring


def get_stored_credentials(config: dict) -> VpnCredentials | None:
    keyring = _keyring_module()
    service = str(config["credential_service"]).strip()
    username = keyring.get_password(service, _USERNAME_ENTRY) or ""
    if not username:
        return None
    password = keyring.get_password(service, username) or ""
    if not password:
        return None
    return VpnCredentials(username=username, password=password)


def store_credentials(config: dict, username: str, password: str) -> None:
    username = username.strip()
    if not username:
        raise ValueError("Логин VPN не заполнен")
    if not password:
        raise ValueError("Пустой пароль VPN сохранять нельзя")

    keyring = _keyring_module()
    service = str(config["credential_service"]).strip()
    previous_username = keyring.get_password(service, _USERNAME_ENTRY) or ""
    if previous_username and previous_username != username:
        try:
            keyring.delete_password(service, previous_username)
        except keyring.errors.PasswordDeleteError:
            pass
    keyring.set_password(service, _USERNAME_ENTRY, username)
    keyring.set_password(service, username, password)


def delete_stored_credentials(config: dict) -> bool:
    keyring = _keyring_module()
    service = str(config["credential_service"]).strip()
    username = keyring.get_password(service, _USERNAME_ENTRY) or ""
    removed = False
    if username:
        try:
            keyring.delete_password(service, username)
            removed = True
        except keyring.errors.PasswordDeleteError:
            pass
    try:
        keyring.delete_password(service, _USERNAME_ENTRY)
        removed = True
    except keyring.errors.PasswordDeleteError:
        pass
    return removed


def _run_process(
    arguments: list[str],
    *,
    input_text: str | None = None,
    timeout: int = 30,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        arguments,
        input=input_text,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )


def endpoint_available(config: dict) -> bool:
    host = str(config["database_host"]).strip()
    port = int(config["database_port"])
    timeout = float(config.get("endpoint_timeout_seconds", 3))
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def vpn_is_connected(config: dict) -> bool:
    cli_path = Path(str(config["cli_path"]))
    if not cli_path.exists():
        raise FileNotFoundError(f"Не найден Cisco VPN CLI: {cli_path}")
    result = _run_process([str(cli_path), "state"])
    return bool(
        re.search(
            r"(?:state|Connection State):\s*Connected\b",
            result.stdout,
            flags=re.IGNORECASE,
        )
    )


def _process_is_running(image_name: str) -> bool:
    result = _run_process(
        ["tasklist", "/FI", f"IMAGENAME eq {image_name}", "/NH"],
        timeout=10,
    )
    return image_name.casefold() in result.stdout.casefold()


def vpn_ui_is_running() -> bool:
    return _process_is_running("vpnui.exe")


def close_vpn_ui() -> None:
    if not vpn_ui_is_running():
        return
    _run_process(["taskkill", "/IM", "vpnui.exe"], timeout=10)
    deadline = time.monotonic() + 5
    while vpn_ui_is_running() and time.monotonic() < deadline:
        time.sleep(0.25)
    if vpn_ui_is_running():
        _run_process(["taskkill", "/F", "/IM", "vpnui.exe"], timeout=10)
    if vpn_ui_is_running():
        raise RuntimeError("Не удалось временно закрыть окно Cisco AnyConnect")


def start_vpn_ui(config: dict) -> None:
    if vpn_ui_is_running():
        return
    ui_path = Path(str(config["ui_path"]))
    if not ui_path.exists():
        raise FileNotFoundError(f"Не найден Cisco VPN UI: {ui_path}")
    creation_flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(
        [str(ui_path)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creation_flags,
        close_fds=True,
    )


def _safe_cli_details(output: str, credentials: VpnCredentials) -> str:
    sanitized = output.replace(credentials.password, "***")
    sanitized = sanitized.replace(credentials.username, "<логин VPN>")
    lines = [line.strip() for line in sanitized.splitlines() if line.strip()]
    return " | ".join(lines[-6:])


def connect_vpn(config: dict, credentials: VpnCredentials) -> None:
    cli_path = Path(str(config["cli_path"]))
    if not cli_path.exists():
        raise FileNotFoundError(f"Не найден Cisco VPN CLI: {cli_path}")

    host = str(config["host"]).strip()
    commands = f"connect {host}\n{credentials.username}\n{credentials.password}\n"
    timeout = int(config.get("connection_timeout_seconds", 90))
    result = _run_process(
        [str(cli_path), "-s"],
        input_text=commands,
        timeout=timeout,
    )

    normalized_output = result.stdout.casefold()
    authentication_errors = (
        "login failed",
        "authentication failed",
        "password expired",
        "connect not available",
    )
    if any(message in normalized_output for message in authentication_errors):
        details = _safe_cli_details(result.stdout, credentials)
        raise RuntimeError(
            "Cisco VPN отклонил подключение. Обновите сохранённый пароль. "
            f"Ответ Cisco: {details}"
        )

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if vpn_is_connected(config) and endpoint_available(config):
            return
        time.sleep(1)

    if vpn_is_connected(config):
        disconnect_vpn(config)
    details = _safe_cli_details(result.stdout, credentials)
    raise RuntimeError(
        "Cisco VPN не подключился. Обновите сохранённый пароль или проверьте доступ. "
        f"Ответ Cisco: {details}"
    )


def disconnect_vpn(config: dict) -> None:
    cli_path = Path(str(config["cli_path"]))
    if not cli_path.exists():
        return
    _run_process([str(cli_path), "disconnect"], timeout=20)
    deadline = time.monotonic() + int(config.get("disconnect_timeout_seconds", 20))
    while time.monotonic() < deadline:
        if not vpn_is_connected(config):
            return
        time.sleep(0.5)
    raise RuntimeError("Cisco VPN не отключился за отведённое время")


@contextmanager
def managed_database_vpn(settings: dict) -> Iterator[bool]:
    config = _vpn_config(settings)
    if endpoint_available(config):
        print("MariaDB уже доступна. Текущее VPN-подключение оставляем без изменений.")
        yield False
        return

    if vpn_is_connected(config):
        raise RuntimeError(
            "Cisco VPN уже подключён, но MariaDB недоступна. "
            "Сценарий не будет переподключать существующую VPN-сессию."
        )

    credentials = get_stored_credentials(config)
    if credentials is None:
        raise RuntimeError(
            "Учётные данные Cisco VPN не найдены. Сохраните их командой: "
            ".\\.venv\\Scripts\\python.exe -m scripts.manage_vpn_credential set"
        )

    ui_was_running = vpn_ui_is_running()
    connected_by_script = False
    try:
        if ui_was_running:
            print("Временно закрываем окно Cisco, чтобы подключиться через официальный CLI.")
            close_vpn_ui()
        print("Подключаем корпоративный VPN...")
        connect_vpn(config, credentials)
        connected_by_script = True
        print("VPN подключён, MariaDB доступна.")
        yield True
    finally:
        try:
            if connected_by_script:
                print("Отключаем VPN, который был запущен этим сценарием...")
                disconnect_vpn(config)
        finally:
            if ui_was_running:
                start_vpn_ui(config)
