import json
import signal
import socket
import struct
from unittest.mock import Mock

from paperspeak import lan


def test_network_not_ready_at_boot_keeps_last_lan_address(database, monkeypatch):
    detected = iter(["192.168.10.112", None, None])
    monkeypatch.setattr(lan, "detect_ipv4", lambda: next(detected))
    assert lan.host() == "192.168.10.112"
    assert lan.host() == "192.168.10.112"
    path = lan.write_caddyfile(lan.host())
    assert "https://192.168.10.112:8443, https://localhost:8443" in path.read_text()


def test_first_boot_network_recovers_by_reloading_only_caddy(database, monkeypatch):
    detected = iter([None, "192.168.10.112", "192.168.10.112", None])
    monkeypatch.setattr(lan, "detect_ipv4", lambda: next(detected))
    address = lan.host()
    assert address == "localhost"
    lan.write_caddyfile(address)
    caddy = Mock()
    address = lan.refresh_caddy(caddy, address)
    assert address == "192.168.10.112"
    assert "192.168.10.112" in (database / "Caddyfile").read_text()
    assert lan.refresh_caddy(caddy, address) == address
    assert lan.refresh_caddy(caddy, address) == address
    caddy.send_signal.assert_called_once_with(signal.SIGUSR1)
    caddy.terminate.assert_not_called()
    caddy.kill.assert_not_called()


def test_changed_lan_address_updates_tls_and_cache(database, monkeypatch):
    monkeypatch.setattr(lan, "detect_ipv4", lambda: "192.168.10.113")
    caddy = Mock()
    assert lan.refresh_caddy(caddy, "192.168.10.112") == "192.168.10.113"
    assert "192.168.10.113" in (database / "Caddyfile").read_text()
    assert (
        json.loads((database / "lan-host.json").read_text())["host"] == "192.168.10.113"
    )


def test_explicit_host_wins_over_detected_or_cached_address(database, monkeypatch):
    monkeypatch.setenv("PAPERSPEAK_LAN_HOST", "paperspeak.local")
    monkeypatch.setattr(lan, "detect_ipv4", lambda: "192.168.10.112")
    assert lan.host() == "paperspeak.local"
    assert not (database / "lan-host.json").exists()


def test_bad_cache_does_not_enter_caddy_config(database, monkeypatch):
    monkeypatch.setattr(lan, "detect_ipv4", lambda: None)
    (database / "lan-host.json").write_text('{"host":"bad\\nconfig"}')
    assert lan.host() == "localhost"
    (database / "lan-host.json").write_text("not json")
    assert lan.host() == "localhost"


def test_lan_detection_without_default_route_uses_active_physical_interface(
    database, monkeypatch
):
    sock = Mock()
    sock.connect.side_effect = OSError("Network is unreachable")
    sock.__enter__ = Mock(return_value=sock)
    sock.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(lan.socket, "socket", lambda *_: sock)
    monkeypatch.setattr(
        lan.socket, "if_nameindex", lambda: [(1, "lo"), (2, "docker0"), (3, "enp129s0")]
    )

    def ioctl(_, operation, request):
        assert request.startswith(b"enp129s0")
        if operation == 0x8913:
            return b"\0" * 16 + struct.pack("H", 1) + b"\0" * 238
        assert operation == 0x8915
        return b"\0" * 20 + socket.inet_aton("192.168.10.112") + b"\0" * 232

    monkeypatch.setattr(lan.fcntl, "ioctl", ioctl)
    assert lan.detect_ipv4() == "192.168.10.112"


def test_cache_failure_does_not_stop_the_application(database, monkeypatch):
    monkeypatch.setattr(lan, "detect_ipv4", lambda: "192.168.10.112")
    monkeypatch.setattr(lan, "atomic_write", Mock(side_effect=OSError("Read only")))
    assert lan.host() == "192.168.10.112"
