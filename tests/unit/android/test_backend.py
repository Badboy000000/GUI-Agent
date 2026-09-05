# Copyright (c) 2026, 东篱馆主

from pathlib import Path
from subprocess import CompletedProcess

import pytest

from gui_agent.contracts import PlatformCommand
from gui_agent.platforms.android import AdbTransport, AndroidBackendError, AndroidDeviceBackend


PNG = b"\x89PNG\r\n\x1a\nmock"

UI_XML = (
    b"<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>"
    b'<hierarchy rotation="0"><node index="0" text="Settings" resource-id="" '
    b'class="android.widget.FrameLayout" package="com.demo" content-desc="" '
    b'clickable="false" bounds="[0,0][1080,2400]"/></hierarchy>'
)


class Runner:
    def __init__(self, ui_xml: bytes | None = None) -> None:
        self.calls: list[list[str]] = []
        self._ui_xml = ui_xml

    def __call__(self, args, *, capture_output, timeout, check):
        self.calls.append(list(args))
        command = args[-3:]
        if command == ["shell", "wm", "size"]:
            return CompletedProcess(args, 0, b"Physical size: 1080x2400\n", b"")
        if command == ["exec-out", "screencap", "-p"]:
            return CompletedProcess(args, 0, PNG, b"")
        if command == ["dumpsys", "window", "windows"]:
            return CompletedProcess(args, 0, b"mCurrentFocus=Window{a u0 com.demo/.Main}\n", b"")
        if args[-1] == "get-state":
            return CompletedProcess(args, 0, b"device\n", b"")
        if "uiautomator" in args:
            return CompletedProcess(args, 0, b"", b"")
        if "cat" in args and self._ui_xml is not None:
            return CompletedProcess(args, 0, self._ui_xml, b"")
        return CompletedProcess(args, 0, b"", b"")


def test_observe_persists_a_consistent_snapshot(tmp_path: Path) -> None:
    backend = AndroidDeviceBackend(AdbTransport("emulator", runner=Runner()), screenshot_directory=tmp_path)

    observation = backend.observe()

    assert (observation.device_id, observation.sequence) == ("emulator", 0)
    assert (observation.screen_width, observation.screen_height) == (1080, 2400)
    assert observation.foreground_app == "com.demo"
    assert Path(observation.screenshot_path).read_bytes() == PNG


def test_execute_uses_only_compiled_platform_commands(tmp_path: Path) -> None:
    runner = Runner()
    backend = AndroidDeviceBackend(AdbTransport("emulator", runner=runner), screenshot_directory=tmp_path)

    backend.execute(PlatformCommand("tap", {"x": 10, "y": 20}, "validated-1"))

    assert runner.calls[-1] == ["adb", "-s", "emulator", "shell", "input", "tap", "10", "20"]
    with pytest.raises(AndroidBackendError, match="unsupported"):
        backend.execute(PlatformCommand("terminate", {}, "validated-2"))


def test_closed_backend_refuses_side_effects(tmp_path: Path) -> None:
    backend = AndroidDeviceBackend(AdbTransport("emulator", runner=Runner()), screenshot_directory=tmp_path)
    backend.close()

    with pytest.raises(AndroidBackendError, match="closed"):
        backend.execute(PlatformCommand("tap", {"x": 1, "y": 2}, "validated"))


def test_observe_captures_the_ui_tree_when_enabled(tmp_path: Path) -> None:
    backend = AndroidDeviceBackend(
        AdbTransport("emulator", runner=Runner(ui_xml=UI_XML)),
        screenshot_directory=tmp_path,
        capture_ui_tree=True,
    )

    observation = backend.observe()

    assert observation.ui_tree is not None
    assert observation.ui_tree["package"] == "com.demo"
    assert observation.ui_tree["node_count"] == 1
    assert observation.ui_tree["nodes"][0]["text"] == "Settings"
    assert Path(observation.screenshot_path).read_bytes() == PNG


def test_observe_degrades_to_no_ui_tree_when_the_dump_fails(tmp_path: Path) -> None:
    backend = AndroidDeviceBackend(
        AdbTransport("emulator", runner=Runner()),
        screenshot_directory=tmp_path,
        capture_ui_tree=True,
    )

    observation = backend.observe()

    assert observation.ui_tree is None
    assert Path(observation.screenshot_path).read_bytes() == PNG


def test_observe_does_not_dump_the_ui_tree_by_default(tmp_path: Path) -> None:
    runner = Runner(ui_xml=UI_XML)
    backend = AndroidDeviceBackend(AdbTransport("emulator", runner=runner), screenshot_directory=tmp_path)

    observation = backend.observe()

    assert observation.ui_tree is None
    assert not any("uiautomator" in call for call in runner.calls)
