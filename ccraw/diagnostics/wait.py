"""Bounded Qt event pumping shared by desktop diagnostic workflows."""

import time


def wait_for_idle(app, busy, *, timeout, message):
    """Keep completion signals flowing while a diagnostic's work is pending."""
    deadline = time.monotonic() + timeout
    while busy():
        app.processEvents()
        time.sleep(0.01)
        if time.monotonic() > deadline:
            raise TimeoutError(message)
    app.processEvents()
