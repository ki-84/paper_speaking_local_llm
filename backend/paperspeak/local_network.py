"""Inference stages can contact the local model server, never an external host."""

import contextlib
import ipaddress
import socket


def _allowed(host):
    if host in {None, "localhost"}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@contextlib.contextmanager
def inference_only():
    """Worker-only guard; the API and source downloader run outside this context."""
    connect, connect_ex, getaddrinfo = (
        socket.socket.connect,
        socket.socket.connect_ex,
        socket.getaddrinfo,
    )

    def checked(self, address):
        if isinstance(address, tuple) and not _allowed(address[0]):
            raise OSError(
                "External communication is disabled during local story inference"
            )
        return connect(self, address)

    def checked_ex(self, address):
        if isinstance(address, tuple) and not _allowed(address[0]):
            raise OSError(
                "External communication is disabled during local story inference"
            )
        return connect_ex(self, address)

    def checked_lookup(host, *args, **kwargs):
        if not _allowed(host):
            raise OSError(
                "External name resolution is disabled during local story inference"
            )
        return getaddrinfo(host, *args, **kwargs)

    socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo = (
        checked,
        checked_ex,
        checked_lookup,
    )
    try:
        yield
    finally:
        socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo = (
            connect,
            connect_ex,
            getaddrinfo,
        )
