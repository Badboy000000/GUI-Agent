# Copyright (c) 2026, 东篱馆主

"""Bounded, read-only UI stability observation for Android backends."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
from math import ceil, isfinite
from pathlib import Path
from time import monotonic, sleep

from gui_agent.contracts import Observation
from gui_agent.platforms.base import DeviceBackend


ScreenshotReader = Callable[[str], bytes]


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
    """Wait for consecutive identical screenshot and foreground-app snapshots.

    The waiter never sends input to a device.  It compares a SHA-256 digest of
    each persisted screenshot together with the foreground package name, so a
    foreground-app transition resets the consecutive-observation counter even
    when the pixels happen to match.  A bounded sample count complements the
    time deadline to avoid an unbounded loop if an injected clock never moves.
    """

    def __init__(
        self,
        backend: DeviceBackend,
        *,
        required_consecutive: int = 2,
        poll_interval_seconds: float = 0.25,
        timeout_seconds: float = 5.0,
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

        self._backend = backend
        self._required_consecutive = required_consecutive
        self._poll_interval_seconds = float(poll_interval_seconds)
        self._timeout_seconds = float(timeout_seconds)
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
        previous_fingerprint: tuple[str, str | None] | None = None
        last_observation: Observation | None = None
        consecutive_samples = 0

        for sample_index in range(self._max_samples):
            observation = self._backend.observe()
            last_observation = observation
            try:
                fingerprint = self._fingerprint(observation)
            except (OSError, ValueError) as error:
                return self._result(
                    stable=False,
                    last_observation=last_observation,
                    samples=sample_index + 1,
                    consecutive_samples=0,
                    reason=f"screenshot unavailable: {error}",
                    started_at=started_at,
                )

            if fingerprint == previous_fingerprint:
                consecutive_samples += 1
            else:
                previous_fingerprint = fingerprint
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

    def _fingerprint(self, observation: Observation) -> tuple[str, str | None]:
        if observation.screenshot_path is None:
            raise ValueError("observation has no persisted screenshot")
        return sha256(self._screenshot_reader(observation.screenshot_path)).hexdigest(), observation.foreground_app

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
