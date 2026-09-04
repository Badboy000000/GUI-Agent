# Copyright (c) 2026, 东篱馆主

from __future__ import annotations

from pathlib import Path

import pytest

from gui_agent.contracts import Observation
from gui_agent.platforms.android import UiStabilityWaiter


class ScriptedBackend:
    def __init__(self, observations: list[Observation]) -> None:
        self._observations = iter(observations)
        self.observe_calls = 0

    def observe(self) -> Observation:
        self.observe_calls += 1
        return next(self._observations)


class FakeClock:
    def __init__(self, *, advance_on_sleep: bool = True) -> None:
        self.current = 0.0
        self.advance_on_sleep = advance_on_sleep
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.current

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        if self.advance_on_sleep:
            self.current += seconds


def observation(
    tmp_path: Path,
    sequence: int,
    screenshot_content: bytes,
    foreground_app: str | None = "com.example.app",
) -> Observation:
    screenshot_path = tmp_path / f"{sequence}.png"
    screenshot_path.write_bytes(screenshot_content)
    return Observation(
        device_id="android-test",
        sequence=sequence,
        screen_width=100,
        screen_height=200,
        screenshot_path=str(screenshot_path),
        foreground_app=foreground_app,
    )


def test_waiter_returns_stable_after_consecutive_matching_observations(tmp_path: Path) -> None:
    clock = FakeClock()
    first = observation(tmp_path, 0, b"same")
    second = observation(tmp_path, 1, b"same")

    result = UiStabilityWaiter(
        ScriptedBackend([first, second]),
        poll_interval_seconds=0.5,
        timeout_seconds=2.0,
        sleeper=clock.sleep,
        monotonic_clock=clock.monotonic,
    ).wait()

    assert result.stable is True
    assert result.last_observation == second
    assert result.samples == 2
    assert result.consecutive_samples == 2
    assert result.reason == "stable"
    assert result.elapsed_seconds == 0.5
    assert clock.sleeps == [0.5]


def test_waiter_resets_stability_when_foreground_app_changes(tmp_path: Path) -> None:
    clock = FakeClock()
    first = observation(tmp_path, 0, b"same", "com.example.one")
    second = observation(tmp_path, 1, b"same", "com.example.two")
    third = observation(tmp_path, 2, b"same", "com.example.two")

    result = UiStabilityWaiter(
        ScriptedBackend([first, second, third]),
        poll_interval_seconds=0.25,
        timeout_seconds=2.0,
        sleeper=clock.sleep,
        monotonic_clock=clock.monotonic,
    ).wait()

    assert result.stable is True
    assert result.last_observation == third
    assert result.samples == 3
    assert result.consecutive_samples == 2


def test_waiter_resets_stability_when_screenshot_content_changes(tmp_path: Path) -> None:
    clock = FakeClock()
    first = observation(tmp_path, 0, b"before")
    second = observation(tmp_path, 1, b"after")
    third = observation(tmp_path, 2, b"after")

    result = UiStabilityWaiter(
        ScriptedBackend([first, second, third]),
        poll_interval_seconds=0.25,
        timeout_seconds=2.0,
        sleeper=clock.sleep,
        monotonic_clock=clock.monotonic,
    ).wait()

    assert result.stable is True
    assert result.samples == 3
    assert result.last_observation == third


def test_waiter_returns_timeout_with_last_observation(tmp_path: Path) -> None:
    clock = FakeClock()
    first = observation(tmp_path, 0, b"one")
    second = observation(tmp_path, 1, b"two")
    third = observation(tmp_path, 2, b"three")

    result = UiStabilityWaiter(
        ScriptedBackend([first, second, third]),
        poll_interval_seconds=0.5,
        timeout_seconds=1.0,
        sleeper=clock.sleep,
        monotonic_clock=clock.monotonic,
    ).wait()

    assert result.stable is False
    assert result.last_observation == third
    assert result.samples == 3
    assert result.reason == "timed out before the UI became stable"
    assert result.elapsed_seconds == 1.0


def test_waiter_never_treats_an_observation_without_screenshot_as_stable() -> None:
    clock = FakeClock()
    no_screenshot = Observation(
        device_id="android-test",
        sequence=0,
        screen_width=100,
        screen_height=200,
        foreground_app="com.example.app",
    )

    result = UiStabilityWaiter(
        ScriptedBackend([no_screenshot]),
        sleeper=clock.sleep,
        monotonic_clock=clock.monotonic,
    ).wait()

    assert result.stable is False
    assert result.last_observation == no_screenshot
    assert result.samples == 1
    assert result.reason.startswith("screenshot unavailable:")
    assert clock.sleeps == []


def test_waiter_has_a_bounded_poll_budget_if_injected_clock_does_not_advance(tmp_path: Path) -> None:
    clock = FakeClock(advance_on_sleep=False)
    observations = [
        observation(tmp_path, 0, b"one"),
        observation(tmp_path, 1, b"two"),
        observation(tmp_path, 2, b"three"),
    ]

    result = UiStabilityWaiter(
        ScriptedBackend(observations),
        poll_interval_seconds=0.5,
        timeout_seconds=1.0,
        sleeper=clock.sleep,
        monotonic_clock=clock.monotonic,
    ).wait()

    assert result.stable is False
    assert result.samples == 3
    assert result.reason == "bounded poll budget exhausted before the UI became stable"
    assert clock.sleeps == [0.5, 0.5]


@pytest.mark.parametrize(
    ("required_consecutive", "poll_interval_seconds", "timeout_seconds"),
    [
        (1, 0.25, 1.0),
        ("two", 0.25, 1.0),
        (2, 0.0, 1.0),
        (2, 0.25, 0.0),
        (2, float("inf"), 1.0),
    ],
)
def test_waiter_rejects_parameters_that_could_make_waiting_invalid_or_unbounded(
    required_consecutive: object, poll_interval_seconds: float, timeout_seconds: float
) -> None:
    with pytest.raises(ValueError):
        UiStabilityWaiter(
            ScriptedBackend([]),
            required_consecutive=required_consecutive,
            poll_interval_seconds=poll_interval_seconds,
            timeout_seconds=timeout_seconds,
        )
