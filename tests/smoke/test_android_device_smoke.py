# Copyright (c) 2026, 东篱馆主

"""Opt-in, read-only smoke coverage for one explicitly selected Android device."""

from __future__ import annotations

import os

import pytest

from gui_agent.platforms.android import AdbTransport


pytestmark = pytest.mark.android_smoke

_ENABLED = os.environ.get("GUI_AGENT_ANDROID_SMOKE") == "1"
_SERIAL = os.environ.get("GUI_AGENT_ANDROID_SERIAL")

if not _ENABLED:
    pytest.skip(
        "Android device smoke is opt-in; set GUI_AGENT_ANDROID_SMOKE=1 and GUI_AGENT_ANDROID_SERIAL",
        allow_module_level=True,
    )
if not _SERIAL:
    pytest.skip("GUI_AGENT_ANDROID_SERIAL must explicitly select one Android device", allow_module_level=True)


def test_selected_android_device_supports_read_only_observation_queries() -> None:
    """Health, screen state, screenshot, and foreground package are readable."""

    device = AdbTransport(_SERIAL)

    assert device.is_healthy()
    width, height = device.screen_size()
    screenshot = device.screenshot_png()
    foreground_app = device.foreground_app()

    assert width > 0
    assert height > 0
    assert screenshot.startswith(b"\x89PNG\r\n\x1a\n")
    assert foreground_app is None or isinstance(foreground_app, str)
