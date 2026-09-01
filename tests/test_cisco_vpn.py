import pytest

import scripts.cisco_vpn as cisco_vpn
from scripts.utils import load_settings


def test_vpn_settings_are_explicit() -> None:
    config = cisco_vpn._vpn_config(load_settings())

    assert config["host"] == "vpn.open-com.ru"
    assert config["database_host"] == "prod-cu03.open-com.ru"
    assert int(config["database_port"]) == 3306


def test_existing_database_connection_is_not_disconnected(monkeypatch) -> None:
    settings = load_settings()
    events: list[str] = []
    monkeypatch.setattr(cisco_vpn, "endpoint_available", lambda config: True)
    monkeypatch.setattr(
        cisco_vpn,
        "disconnect_vpn",
        lambda config: events.append("disconnect"),
    )

    with cisco_vpn.managed_database_vpn(settings) as connected_by_script:
        events.append("etl")

    assert connected_by_script is False
    assert events == ["etl"]


def test_script_disconnects_only_its_own_connection(monkeypatch) -> None:
    settings = load_settings()
    events: list[str] = []
    credentials = cisco_vpn.VpnCredentials("user", "secret")
    monkeypatch.setattr(cisco_vpn, "endpoint_available", lambda config: False)
    monkeypatch.setattr(cisco_vpn, "vpn_is_connected", lambda config: False)
    monkeypatch.setattr(
        cisco_vpn,
        "get_stored_credentials",
        lambda config: credentials,
    )
    monkeypatch.setattr(cisco_vpn, "vpn_ui_is_running", lambda: True)
    monkeypatch.setattr(cisco_vpn, "close_vpn_ui", lambda: events.append("close_ui"))
    monkeypatch.setattr(
        cisco_vpn,
        "connect_vpn",
        lambda config, current: events.append("connect"),
    )
    monkeypatch.setattr(
        cisco_vpn,
        "disconnect_vpn",
        lambda config: events.append("disconnect"),
    )
    monkeypatch.setattr(
        cisco_vpn,
        "start_vpn_ui",
        lambda config: events.append("start_ui"),
    )

    with cisco_vpn.managed_database_vpn(settings) as connected_by_script:
        events.append("etl")

    assert connected_by_script is True
    assert events == ["close_ui", "connect", "etl", "disconnect", "start_ui"]


def test_connected_vpn_with_unavailable_database_is_not_restarted(monkeypatch) -> None:
    settings = load_settings()
    monkeypatch.setattr(cisco_vpn, "endpoint_available", lambda config: False)
    monkeypatch.setattr(cisco_vpn, "vpn_is_connected", lambda config: True)

    with pytest.raises(RuntimeError, match="MariaDB недоступна"):
        with cisco_vpn.managed_database_vpn(settings):
            pass

