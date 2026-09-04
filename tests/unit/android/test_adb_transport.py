# Copyright (c) 2026, 东篱馆主

from subprocess import CalledProcessError, CompletedProcess

import pytest

from gui_agent.platforms.android import AdbError, AdbTransport


PNG = b"\x89PNG\r\n\x1a\nmock"


class RecordingRunner:
    def __init__(self, responses: list[CompletedProcess[bytes]] | None = None) -> None:
        self.calls: list[list[str]] = []
        self.responses = responses or []

    def __call__(self, args, *, capture_output, timeout, check):
        self.calls.append(list(args))
        if self.responses:
            return self.responses.pop(0)
        return CompletedProcess(args, 0, b"", b"")


def result(stdout: bytes) -> CompletedProcess[bytes]:
    return CompletedProcess([], 0, stdout, b"")


def test_discover_parses_adb_devices_output() -> None:
    runner = RecordingRunner([result(b"List of devices attached\nemulator-5554\tdevice\nxyz\toffline\n")])

    devices = AdbTransport.discover(runner=runner)

    assert [(device.serial, device.state) for device in devices] == [
        ("emulator-5554", "device"),
        ("xyz", "offline"),
    ]
    assert runner.calls == [["adb", "devices"]]


def test_actions_are_argument_lists_not_a_host_shell_string() -> None:
    runner = RecordingRunner([result(PNG)] * 6)
    device = AdbTransport("emulator-5554", runner=runner)

    assert device.screenshot_png() == PNG
    device.tap(12, 34)
    device.swipe(1, 2, 3, 4, 500)
    device.type_text("hello world")
    device.launch("com.example.app")
    device.system_key("BACK")

    assert runner.calls == [
        ["adb", "-s", "emulator-5554", "exec-out", "screencap", "-p"],
        ["adb", "-s", "emulator-5554", "shell", "input", "tap", "12", "34"],
        ["adb", "-s", "emulator-5554", "shell", "input", "swipe", "1", "2", "3", "4", "500"],
        ["adb", "-s", "emulator-5554", "shell", "input", "text", "hello%sworld"],
        ["adb", "-s", "emulator-5554", "shell", "monkey", "-p", "com.example.app", "1"],
        ["adb", "-s", "emulator-5554", "shell", "input", "keyevent", "KEYCODE_BACK"],
    ]


def test_health_returns_false_when_adb_fails() -> None:
    def failing_runner(args, *, capture_output, timeout, check):
        raise CalledProcessError(1, args)

    assert not AdbTransport("device", runner=failing_runner).is_healthy()


@pytest.mark.parametrize("text", ["", "a\nb", "a\x00b"])
def test_text_rejects_unsupported_control_characters(text: str) -> None:
    with pytest.raises(ValueError):
        AdbTransport("device", runner=RecordingRunner()).type_text(text)


def test_rejects_invalid_screenshot_payload() -> None:
    with pytest.raises(AdbError, match="invalid screenshot"):
        AdbTransport("device", runner=RecordingRunner([result(b"not a png")])).screenshot_png()


def test_screen_size_and_foreground_app_are_parsed() -> None:
    runner = RecordingRunner(
        [
            result(b"Physical size: 1080x2400\n"),
            result(b"  mCurrentFocus=Window{abc u0 com.example.app/.MainActivity}\n"),
        ]
    )
    device = AdbTransport("device", runner=runner)

    assert device.screen_size() == (1080, 2400)
    assert device.foreground_app() == "com.example.app"


def test_foreground_app_falls_back_to_android_14_resumed_activity() -> None:
    runner = RecordingRunner(
        [
            result(b"WINDOW MANAGER WINDOWS\n"),
            result(
                b"  topResumedActivity=ActivityRecord{abc u0 "
                b"com.google.android.apps.nexuslauncher/.NexusLauncherActivity t7}\n"
            ),
        ]
    )

    assert AdbTransport("device", runner=runner).foreground_app() == "com.google.android.apps.nexuslauncher"
    assert runner.calls == [
        ["adb", "-s", "device", "shell", "dumpsys", "window", "windows"],
        ["adb", "-s", "device", "shell", "dumpsys", "activity", "activities"],
    ]


def test_foreground_app_falls_back_to_oem_resumed_activity() -> None:
    runner = RecordingRunner(
        [
            result(b"WINDOW MANAGER WINDOWS\n"),
            result(b"mResumedActivity: ActivityRecord{abc u0 com.miui.home/.launcher.Launcher t7}\n"),
        ]
    )

    assert AdbTransport("device", runner=runner).foreground_app() == "com.miui.home"


def test_foreground_app_returns_none_when_all_supported_formats_are_unavailable() -> None:
    runner = RecordingRunner([result(b"WINDOW MANAGER WINDOWS\n"), result(b"no resumed activity\n")])

    assert AdbTransport("device", runner=runner).foreground_app() is None


def test_resolved_settings_and_home_packages_are_parsed_deterministically() -> None:
    runner = RecordingRunner(
        [
            result(b"priority=1\ncom.android.settings/.homepage.SettingsHomepageActivity\n"),
            result(b"com.miui.home/.launcher.Launcher\n"),
        ]
    )
    device = AdbTransport("device", runner=runner)

    assert device.settings_package() == "com.android.settings"
    assert device.home_package() == "com.miui.home"
    assert runner.calls == [
        [
            "adb",
            "-s",
            "device",
            "shell",
            "cmd",
            "package",
            "resolve-activity",
            "--brief",
            "-a",
            "android.settings.SETTINGS",
        ],
        [
            "adb",
            "-s",
            "device",
            "shell",
            "cmd",
            "package",
            "resolve-activity",
            "--brief",
            "-a",
            "android.intent.action.MAIN",
            "-c",
            "android.intent.category.HOME",
        ],
    ]


def test_activity_resolution_rejects_ambiguous_or_unavailable_output() -> None:
    runner = RecordingRunner(
        [
            result(b"com.example.first/.One\ncom.example.second/.Two\n"),
            result(b"No activity found\n"),
        ]
    )
    device = AdbTransport("device", runner=runner)

    assert device.settings_package() is None
    assert device.home_package() is None
