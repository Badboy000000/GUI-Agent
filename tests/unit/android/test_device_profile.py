# Copyright (c) 2026, 东篱馆主

from subprocess import CompletedProcess

import pytest

from gui_agent.platforms.android import (
    AdbTransport,
    AndroidDeviceProfileError,
    discover_android_device_profile,
)


def result(stdout: bytes) -> CompletedProcess[bytes]:
    return CompletedProcess([], 0, stdout, b"")


class RecordingRunner:
    def __init__(self, responses: list[CompletedProcess[bytes]]) -> None:
        self.responses = responses
        self.calls: list[list[str]] = []

    def __call__(self, args, *, capture_output, timeout, check):
        self.calls.append(list(args))
        return self.responses.pop(0)


def test_device_profile_collects_only_read_only_preflight_facts() -> None:
    runner = RecordingRunner(
        [
            result(b"device\n"),
            result(b"Physical size: 1080x2400\n"),
            result(b"com.android.settings/.homepage.SettingsHomepageActivity\n"),
            result(b"com.miui.home/.launcher.Launcher\n"),
            result(b"mCurrentFocus=Window{abc u0 com.miui.home/.launcher.Launcher}\n"),
            result(b"Xiaomi\n"),
            result(b"23127PN0CC\n"),
            result(b"15\n"),
        ]
    )

    profile = discover_android_device_profile(AdbTransport("redmi-serial", runner=runner))

    assert profile.serial == "redmi-serial"
    assert profile.manufacturer == "Xiaomi"
    assert profile.model == "23127PN0CC"
    assert profile.android_release == "15"
    assert (profile.screen_width, profile.screen_height) == (1080, 2400)
    assert profile.settings_package == "com.android.settings"
    assert profile.home_package == "com.miui.home"
    assert profile.foreground_package == "com.miui.home"
    assert all("input" not in call and "monkey" not in call for call in runner.calls)


@pytest.mark.parametrize(
    "settings_output,home_output,window_output,activity_output",
    [
        (b"No activity found\n", b"com.miui.home/.Launcher\n", b"focus\n", b"topResumedActivity=ActivityRecord{ com.miui.home/.Launcher}\n"),
        (b"com.android.settings/.Settings\n", b"No activity found\n", b"focus\n", b"topResumedActivity=ActivityRecord{ com.miui.home/.Launcher}\n"),
        (b"com.android.settings/.Settings\n", b"com.miui.home/.Launcher\n", b"focus\n", b"no activity\n"),
    ],
)
def test_device_profile_fails_closed_when_required_package_observation_is_unavailable(
    settings_output: bytes, home_output: bytes, window_output: bytes, activity_output: bytes
) -> None:
    runner = RecordingRunner(
        [
            result(b"device\n"),
            result(b"Physical size: 1080x2400\n"),
            result(settings_output),
            result(home_output),
            result(window_output),
            result(activity_output),
        ]
    )

    with pytest.raises(AndroidDeviceProfileError, match="requires an observable"):
        discover_android_device_profile(AdbTransport("device", runner=runner))


def test_device_profile_fails_before_collecting_facts_when_device_is_unhealthy() -> None:
    runner = RecordingRunner([result(b"offline\n")])

    with pytest.raises(AndroidDeviceProfileError, match="not healthy"):
        discover_android_device_profile(AdbTransport("device", runner=runner))

    assert runner.calls == [["adb", "-s", "device", "get-state"]]
