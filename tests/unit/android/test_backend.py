# Copyright (c) 2026, 东篱馆主

from pathlib import Path
from subprocess import CompletedProcess

import pytest

from gui_agent.contracts import PlatformCommand
from gui_agent.platforms.android import AdbTransport, AndroidBackendError, AndroidDeviceBackend


PNG = b"\x89PNG\r\n\x1a\nmock"


class Runner:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

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
