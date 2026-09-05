# Copyright (c) 2026, 东篱馆主

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from gui_agent.contracts import Observation
from gui_agent.platforms.android import UiStabilityWaiter


CANVAS_WIDTH = 1080
CANVAS_HEIGHT = 2400
CANVAS_PIXELS = CANVAS_WIDTH * CANVAS_HEIGHT  # 2_592_000 px

BASE_GRAY = 128
PATCH_GRAY = 255  # |255 - 128| = 127, well above _PIXEL_DELTA_THRESHOLD (8)

# A 150x30 px patch: 4_500 changed px of 2_592_000 ≈ 0.00174 (0.174%).
PATCH = (100, 200, 250, 230)
PATCH_PIXELS = (PATCH[2] - PATCH[0]) * (PATCH[3] - PATCH[1])  # 4_500
PATCH_RATIO = PATCH_PIXELS / CANVAS_PIXELS  # ≈ 0.00174 <= default max_diff_ratio 0.01


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


def png_bytes(gray: int, *, patch: tuple[int, int, int, int] | None = None) -> bytes:
    """Render a real PNG: a solid gray canvas with an optional white rectangle.

    ``patch`` is (left, top, right, bottom) in pixels; the painted area covers
    exactly (right - left) x (bottom - top) pixels.
    """
    image = Image.new("L", (CANVAS_WIDTH, CANVAS_HEIGHT), gray)
    if patch is not None:
        left, top, right, bottom = patch
        ImageDraw.Draw(image).rectangle([left, top, right - 1, bottom - 1], fill=PATCH_GRAY)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


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
    first = observation(tmp_path, 0, png_bytes(BASE_GRAY))
    second = observation(tmp_path, 1, png_bytes(BASE_GRAY))

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
    first = observation(tmp_path, 0, png_bytes(BASE_GRAY), "com.example.one")
    second = observation(tmp_path, 1, png_bytes(BASE_GRAY), "com.example.two")
    third = observation(tmp_path, 2, png_bytes(BASE_GRAY), "com.example.two")

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
    # Full-canvas repaint (every pixel differs), because tolerant comparison
    # intentionally ignores small visual changes.
    clock = FakeClock()
    first = observation(tmp_path, 0, png_bytes(0))
    second = observation(tmp_path, 1, png_bytes(255))
    third = observation(tmp_path, 2, png_bytes(255))

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
    first = observation(tmp_path, 0, png_bytes(0))
    second = observation(tmp_path, 1, png_bytes(128))
    third = observation(tmp_path, 2, png_bytes(255))

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


def test_waiter_treats_an_undecodable_screenshot_as_unavailable(tmp_path: Path) -> None:
    clock = FakeClock()
    corrupt = observation(tmp_path, 0, b"this is not a png")

    result = UiStabilityWaiter(
        ScriptedBackend([corrupt]),
        sleeper=clock.sleep,
        monotonic_clock=clock.monotonic,
    ).wait()

    assert result.stable is False
    assert result.last_observation == corrupt
    assert result.samples == 1
    assert result.reason.startswith("screenshot unavailable:")
    assert clock.sleeps == []


def test_waiter_has_a_bounded_poll_budget_if_injected_clock_does_not_advance(tmp_path: Path) -> None:
    clock = FakeClock(advance_on_sleep=False)
    observations = [
        observation(tmp_path, 0, png_bytes(0)),
        observation(tmp_path, 1, png_bytes(128)),
        observation(tmp_path, 2, png_bytes(255)),
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


@pytest.mark.parametrize(
    "max_diff_ratio",
    [-0.1, 1.5, float("nan"), float("inf"), True, False, "0.5"],
)
def test_waiter_rejects_invalid_max_diff_ratio(max_diff_ratio: object) -> None:
    with pytest.raises(ValueError, match="max_diff_ratio"):
        UiStabilityWaiter(ScriptedBackend([]), max_diff_ratio=max_diff_ratio)


@pytest.mark.parametrize("max_diff_ratio", [0.0, 0, 0.5, 1, 1.0])
def test_waiter_accepts_valid_max_diff_ratio(max_diff_ratio: float) -> None:
    UiStabilityWaiter(ScriptedBackend([]), max_diff_ratio=max_diff_ratio)


def test_waiter_treats_a_small_repainted_patch_as_stable(tmp_path: Path) -> None:
    # 4_500 changed px / 2_592_000 px ≈ 0.00174 <= default max_diff_ratio 0.01.
    clock = FakeClock()
    first = observation(tmp_path, 0, png_bytes(BASE_GRAY))
    second = observation(tmp_path, 1, png_bytes(BASE_GRAY, patch=PATCH))

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


def test_waiter_never_stabilizes_when_half_the_canvas_repaints(tmp_path: Path) -> None:
    # 50% changed px, far above the default 1% tolerance.
    clock = FakeClock()
    plain = png_bytes(BASE_GRAY)
    half_repainted = png_bytes(
        BASE_GRAY, patch=(0, CANVAS_HEIGHT // 2, CANVAS_WIDTH, CANVAS_HEIGHT)
    )
    observations = [
        observation(tmp_path, sequence, plain if sequence % 2 == 0 else half_repainted)
        for sequence in range(3)
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
    assert result.reason == "timed out before the UI became stable"


def test_waiter_resets_when_foreground_changes_despite_identical_pixels(tmp_path: Path) -> None:
    clock = FakeClock()
    frame = png_bytes(BASE_GRAY)
    first = observation(tmp_path, 0, frame, "com.example.one")
    second = observation(tmp_path, 1, frame, "com.example.two")
    third = observation(tmp_path, 2, frame, "com.example.two")

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


def test_waiter_discriminates_ratios_just_above_and_below_max_diff_ratio(tmp_path: Path) -> None:
    # Same frame pair (PATCH_RATIO ≈ 0.00174): stable with a 0.002 allowance,
    # unstable with a 0.001 allowance, proving the threshold discriminates.
    plain = png_bytes(BASE_GRAY)
    patched = png_bytes(BASE_GRAY, patch=PATCH)

    below_clock = FakeClock()
    below = UiStabilityWaiter(
        ScriptedBackend(
            [
                observation(tmp_path, 0, plain),
                observation(tmp_path, 1, patched),
            ]
        ),
        max_diff_ratio=0.002,
        poll_interval_seconds=0.5,
        timeout_seconds=1.0,
        sleeper=below_clock.sleep,
        monotonic_clock=below_clock.monotonic,
    ).wait()

    assert below.stable is True
    assert below.samples == 2

    above_clock = FakeClock()
    above = UiStabilityWaiter(
        ScriptedBackend(
            [
                observation(tmp_path, 2, plain),
                observation(tmp_path, 3, patched),
                observation(tmp_path, 4, plain),
            ]
        ),
        max_diff_ratio=0.001,
        poll_interval_seconds=0.5,
        timeout_seconds=1.0,
        sleeper=above_clock.sleep,
        monotonic_clock=above_clock.monotonic,
    ).wait()

    assert above.stable is False
    assert above.samples == 3
    assert above.reason == "timed out before the UI became stable"
