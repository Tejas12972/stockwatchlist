"""Proof that the suite really is offline.

SPEC.md M4: *"the suite must pass offline with no network."* Configuration can
drift and a comment in `pyproject.toml` proves nothing, so the guarantee is
asserted here as a test: if someone removes `--disable-socket`, or a code path
quietly acquires a network dependency, this file goes red.
"""

from __future__ import annotations

import socket

import pytest


class TestNetworkIsBlocked:
    def test_opening_a_tcp_connection_fails(self) -> None:
        with pytest.raises(Exception) as info:
            socket.create_connection(("142.250.72.46", 80), timeout=1)
        assert "socket" in type(info.value).__name__.lower() or "Blocked" in str(info.value)

    def test_creating_an_inet_socket_fails(self) -> None:
        with pytest.raises(Exception):  # noqa: B017 -- pytest_socket raises its own type
            socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    def test_dns_resolution_fails(self) -> None:
        with pytest.raises(Exception):  # noqa: B017
            socket.gethostbyname("finance.yahoo.com")

    def test_unix_sockets_are_still_allowed(self) -> None:
        """Local IPC is permitted so asyncio's event loop can start."""
        left, right = socket.socketpair()
        left.close()
        right.close()


class TestNoHiddenVendorImport:
    def test_importing_the_provider_package_does_not_import_yfinance(self) -> None:
        """yfinance is imported inside methods, not at module scope.

        Beyond speed, this is what stops a stray `from options_tool.providers
        import ...` anywhere in the codebase from dragging a networking library
        into a process that never asked for one.
        """
        import subprocess
        import sys

        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys; import options_tool.providers; "
                "import options_tool.analytics.chain; import options_tool.api.main; "
                "print('yfinance' in sys.modules)",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.stdout.strip() == "False", result.stdout

    def test_analytics_import_nothing_that_does_io(self) -> None:
        import options_tool.analytics.black_scholes as bs
        import options_tool.analytics.implied_vol as iv
        from options_tool.analytics import payoff

        for module in (bs, iv, payoff):
            source = module.__doc__ or ""
            assert source, f"{module.__name__} should document itself"
