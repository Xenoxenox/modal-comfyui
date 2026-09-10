from __future__ import annotations

from serve import _error_signal, _startup_stalled


def test_connection_refused_is_fatal_only_before_the_service_is_healthy() -> None:
    line = "modal client: Connection refused"

    assert _error_signal(line, service_healthy=False) == "Connection refused"
    assert _error_signal(line, service_healthy=True) is None


def test_remote_tracebacks_are_never_treated_as_local_startup_failures() -> None:
    assert _error_signal("Traceback (most recent call last):", service_healthy=False) is None
    assert _error_signal("Exception: boom", service_healthy=False) is None


def test_other_network_errors_stay_fatal_while_healthy() -> None:
    assert _error_signal("getaddrinfo failed", service_healthy=True) == "getaddrinfo failed"


def test_startup_stall_only_applies_before_the_service_is_healthy() -> None:
    assert _startup_stalled(0.0, 1000.0, timeout=120, healthy=False)
    assert not _startup_stalled(0.0, 1000.0, timeout=120, healthy=True)
