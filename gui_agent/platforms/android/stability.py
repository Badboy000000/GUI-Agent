# Copyright (c) 2026, 东篱馆主

"""Bounded, read-only UI stability observation for Android backends.

Screenshots are compared by a tolerant pixel-difference ratio instead of
exact byte equality, because live status-bar indicators (for example the
network-speed readout on HyperOS) repaint every second on real devices and
strictly identical frames would otherwise never be observed there.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from io import BytesIO
from math import ceil, isfinite
from pathlib import Path
from time import monotonic, sleep

import numpy as np
from PIL import Image

from gui_agent.contracts import Observation
from gui_agent.platforms.base import DeviceBackend


ScreenshotReader = Callable[[str], bytes]

# Minimum absolute per-pixel gray difference that counts as a changed pixel.
_PIXEL_DELTA_THRESHOLD = 8


@dataclass(frozen=True, slots=True)
class UiStabilityResult:
    """The outcome of a bounded sequence of read-only UI observations."""

    stable: bool
    last_observation: Observation | None
    samples: int
    consecutive_samples: int
    reason: str
    elapsed_seconds: float


class UiStabilityWaiter:
    """Wait for consecutive visually matching screenshot/foreground snapshots.

    The waiter never sends input to a device.  Each persisted screenshot is
    decoded once to a grayscale array and compared with the previous sample:
    two frames count as the same when the ratio of pixels whose absolute gray
    difference exceeds ``_PIXEL_DELTA_THRESHOLD`` is at most
    ``max_diff_ratio``.  This tolerant comparison exists because live
    status-bar indicators on real devices repaint continuously and would
    otherwise prevent the UI from ever counting as stable.  The foreground
    package name is still compared exactly, so a foreground-app transition
    resets the consecutive-observation counter even when the pixels match.
    A bounded sample count complements the time deadline to avoid an
    unbounded loop if an injected clock never moves.
    """

    def __init__(
        self,
        backend: DeviceBackend,
        *,
        required_consecutive: int = 2,
        poll_interval_seconds: float = 0.25,
        timeout_seconds: float = 5.0,
        max_diff_ratio: float = 0.01,
        sleeper: Callable[[float], None] = sleep,
        monotonic_clock: Callable[[], float] = monotonic,
        screenshot_reader: ScreenshotReader | None = None,
    ) -> None:
        if (
            isinstance(required_consecutive, bool)
            or not isinstance(required_consecutive, int)
            or required_consecutive < 2
        ):
            raise ValueError("required_consecutive must be an integer of at least 2")
        self._validate_positive_seconds(poll_interval_seconds, "poll_interval_seconds")
        self._validate_positive_seconds(timeout_seconds, "timeout_seconds")
        if (
            isinstance(max_diff_ratio, bool)
            or not isinstance(max_diff_ratio, (int, float))
            or not isfinite(max_diff_ratio)
            or not 0.0 <= max_diff_ratio <= 1.0
        ):
            raise ValueError(
                "max_diff_ratio must be a finite number between 0.0 and 1.0 inclusive "
                "(0.0 restores exact-match semantics)"
            )

        self._backend = backend
        self._required_consecutive = required_consecutive
        self._poll_interval_seconds = float(poll_interval_seconds)
        self._timeout_seconds = float(timeout_seconds)
        self._max_diff_ratio = float(max_diff_ratio)
        self._sleeper = sleeper
        self._monotonic_clock = monotonic_clock
        self._screenshot_reader = screenshot_reader or self._read_screenshot
        self._max_samples = ceil(self._timeout_seconds / self._poll_interval_seconds) + 1

    @staticmethod
    def _validate_positive_seconds(value: float, name: str) -> None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{name} must be a positive, finite number")
        if not isfinite(value) or value <= 0:
            raise ValueError(f"{name} must be a positive, finite number")

    @staticmethod
    def _read_screenshot(path: str) -> bytes:
        return Path(path).read_bytes()

    def wait(self) -> UiStabilityResult:
        """Return after stability, the deadline, or the bounded poll budget."""

        started_at = self._monotonic_clock()
        previous_frame: np.ndarray | None = None
        previous_foreground_app: str | None = None
        last_observation: Observation | None = None
        consecutive_samples = 0

        for sample_index in range(self._max_samples):
            observation = self._backend.observe()
            last_observation = observation
            try:
                frame = self._grayscale_frame(observation)
            except (OSError, ValueError) as error:
                return self._result(
                    stable=False,
                    last_observation=last_observation,
                    samples=sample_index + 1,
                    consecutive_samples=0,
                    reason=f"screenshot unavailable: {error}",
                    started_at=started_at,
                )

            if (
                previous_frame is not None
                and observation.foreground_app == previous_foreground_app
                and self._frames_match(previous_frame, frame)
            ):
                consecutive_samples += 1
            else:
                previous_frame = frame
                previous_foreground_app = observation.foreground_app
                consecutive_samples = 1

            if consecutive_samples >= self._required_consecutive:
                return self._result(
                    stable=True,
                    last_observation=last_observation,
                    samples=sample_index + 1,
                    consecutive_samples=consecutive_samples,
                    reason="stable",
                    started_at=started_at,
                )

            elapsed_seconds = self._elapsed_since(started_at)
            if elapsed_seconds >= self._timeout_seconds:
                return self._result(
                    stable=False,
                    last_observation=last_observation,
                    samples=sample_index + 1,
                    consecutive_samples=consecutive_samples,
                    reason="timed out before the UI became stable",
                    started_at=started_at,
                )

            if sample_index + 1 == self._max_samples:
                return self._result(
                    stable=False,
                    last_observation=last_observation,
                    samples=sample_index + 1,
                    consecutive_samples=consecutive_samples,
                    reason="bounded poll budget exhausted before the UI became stable",
                    started_at=started_at,
                )

            self._sleeper(min(self._poll_interval_seconds, self._timeout_seconds - elapsed_seconds))

        raise AssertionError("bounded UI stability loop unexpectedly exited")

    def _grayscale_frame(self, observation: Observation) -> np.ndarray:
        if observation.screenshot_path is None:
            raise ValueError("observation has no persisted screenshot")
        png_bytes = self._screenshot_reader(observation.screenshot_path)
        with Image.open(BytesIO(png_bytes)) as image:
            return np.asarray(image.convert("L"), dtype=np.uint8)

    def _frames_match(self, previous: np.ndarray, current: np.ndarray) -> bool:
        if previous.shape != current.shape:
            return False
        changed = (
            np.abs(previous.astype(np.int16) - current.astype(np.int16))
            > _PIXEL_DELTA_THRESHOLD
        )
        diff_ratio = float(np.count_nonzero(changed)) / changed.size
        return diff_ratio <= self._max_diff_ratio

    def _elapsed_since(self, started_at: float) -> float:
        return max(0.0, self._monotonic_clock() - started_at)

    def _result(
        self,
        *,
        stable: bool,
        last_observation: Observation | None,
        samples: int,
        consecutive_samples: int,
        reason: str,
        started_at: float,
    ) -> UiStabilityResult:
        return UiStabilityResult(
            stable=stable,
            last_observation=last_observation,
            samples=samples,
            consecutive_samples=consecutive_samples,
            reason=reason,
            elapsed_seconds=self._elapsed_since(started_at),
        )
