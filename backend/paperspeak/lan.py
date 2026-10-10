"""Keep the LAN HTTPS address through early boot and delayed network startup."""

from __future__ import annotations

import fcntl
import ipaddress
import json
import os
import signal
import socket
import struct
import tempfile
import time

from . import config


def usable_ipv4(value):
    try:
        address = ipaddress.IPv4Address(value)
        return not (
            address.is_loopback
            or address.is_link_local
            or address.is_unspecified
            or address.is_multicast
        )
    except (ipaddress.AddressValueError, TypeError):
        return False


def detect_ipv4():
    # UDP connect only asks the kernel for a route; it sends no packet.
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        try:
            sock.connect(("192.0.2.1", 80))
            address = sock.getsockname()[0]
            if usable_ipv4(address):
                return address
        except OSError:
            pass
        # A LAN can be available without an Internet/default route.
        for _, name in socket.if_nameindex():
            if name.startswith(("lo", "docker", "veth", "br-", "virbr", "tun", "tap")):
                continue
            request = struct.pack("256s", name.encode()[:15])
            try:
                flags = fcntl.ioctl(sock.fileno(), 0x8913, request)  # SIOCGIFFLAGS
                if not struct.unpack_from("H", flags, 16)[0] & 1:  # IFF_UP
                    continue
                result = fcntl.ioctl(sock.fileno(), 0x8915, request)  # SIOCGIFADDR
                address = socket.inet_ntoa(result[20:24])
                if usable_ipv4(address):
                    return address
            except OSError:
                continue
    return None


def atomic_write(path, text):
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as stream:
        temporary = stream.name
        try:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def host():
    configured = os.environ.get("PAPERSPEAK_LAN_HOST")
    if configured:
        return configured
    cache = config.DATA / "lan-host.json"
    try:
        previous = json.loads(cache.read_text()).get("host")
    except (OSError, ValueError, AttributeError):
        previous = None
    try:
        detected = detect_ipv4()
    except OSError:
        detected = None
    if detected and usable_ipv4(detected):
        if detected != previous:
            try:
                atomic_write(
                    cache, json.dumps({"host": detected, "detected_at": time.time()})
                )
            except OSError:
                pass  # A cache write must not prevent serving or generating videos.
        return detected
    return previous if usable_ipv4(previous) else "localhost"


def write_caddyfile(address):
    hosts = f"https://{address}:8443"
    if address != "localhost":
        hosts += ", https://localhost:8443"
    path = config.DATA / "Caddyfile"
    atomic_write(
        path,
        "{\n admin off\n auto_https disable_redirects\n skip_install_trust\n storage file_system {\n root "
        + str(config.DATA / "tls")
        + "\n }\n}\n"
        + hosts
        + " {\n tls internal\n encode zstd gzip\n reverse_proxy 127.0.0.1:"
        + str(config.API_PORT)
        + " {\n flush_interval -1\n }\n}\n",
    )
    return path


def refresh_caddy(process, active_host):
    address = host()
    if address == active_host or address == "localhost":
        return active_host
    write_caddyfile(address)
    # Caddy >= 2.11 reloads its original file on SIGUSR1, with admin still off.
    process.send_signal(signal.SIGUSR1)
    return address
