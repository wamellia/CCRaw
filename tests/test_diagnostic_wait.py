"""Diagnostic waits must pump Qt and terminate when work stalls."""

import pytest
from PySide6.QtCore import QTimer


def test_diagnostic_wait_processes_completion_events(app):
    from ccraw.diagnostics.wait import wait_for_idle

    pending = [True]
    QTimer.singleShot(0, lambda: pending.clear())
    wait_for_idle(app, lambda: bool(pending), timeout=1, message='unfinished')
    assert not pending


def test_diagnostic_wait_reports_timeout(app):
    from ccraw.diagnostics.wait import wait_for_idle

    with pytest.raises(TimeoutError, match='unfinished merge'):
        wait_for_idle(app, lambda: True, timeout=0, message='unfinished merge')


def test_diagnostic_wait_flushes_events_even_if_initially_idle(app):
    from ccraw.diagnostics.wait import wait_for_idle

    completed = []
    QTimer.singleShot(0, lambda: completed.append(True))
    wait_for_idle(app, lambda: False, timeout=1, message='unfinished')
    assert completed == [True]
