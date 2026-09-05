# Copyright (c) 2026, 东篱馆主

"""Unit coverage for the coordinate-drift monitor's two fail-closed signals."""

from __future__ import annotations

from collections.abc import Iterable

import pytest

from gui_agent.brains import CoordinateDriftError, CoordinateDriftMonitor
from gui_agent.contracts import Observation, ProposedAction


_HEIGHT = 2400
_THOUSAND_BAND_TOP = 999 / _HEIGHT  # ~0.416


class _ScriptedBrain:
    def __init__(self, proposals: Iterable[ProposedAction]) -> None:
        self._proposals = iter(proposals)

    def decide(self, instruction: str, observation: Observation) -> ProposedAction:
        return next(self._proposals)


def _observation(sequence: int, height: int = _HEIGHT) -> Observation:
    return Observation(
        device_id="offline-android",
        sequence=sequence,
        screen_width=1080,
        screen_height=height,
    )


def _click(observation: Observation, x: float, y: float) -> ProposedAction:
    return ProposedAction(
        name="click",
        arguments={"coordinate": [x, y]},
        source_observation_id=observation.id,
    )


def _decide(monitor: CoordinateDriftMonitor, sequence: int, height: int = _HEIGHT) -> ProposedAction:
    observation = _observation(sequence, height)
    return monitor.decide("tap something", observation)


def test_out_of_range_coordinate_raises_the_drift_diagnosis() -> None:
    observation = _observation(0)
    brain = _ScriptedBrain([_click(observation, 1.2, 0.5)])
    monitor = CoordinateDriftMonitor(brain)

    with pytest.raises(CoordinateDriftError, match="auto"):
        monitor.decide("tap something", observation)


def test_out_of_range_drag_endpoints_raise_the_drift_diagnosis() -> None:
    observation = _observation(0)
    brain = _ScriptedBrain(
        [
            ProposedAction(
                name="drag",
                arguments={"start_coordinate": [0.5, 0.5], "end_coordinate": [0.5, -0.1]},
                source_observation_id=observation.id,
            )
        ]
    )
    monitor = CoordinateDriftMonitor(brain)

    with pytest.raises(CoordinateDriftError, match="auto"):
        monitor.decide("drag", observation)


def test_thousand_band_accumulation_raises_once_the_window_is_full() -> None:
    observations = [_observation(index) for index in range(5)]
    brain = _ScriptedBrain(
        [_click(observation, 0.5, _THOUSAND_BAND_TOP * 0.5) for observation in observations]
    )
    monitor = CoordinateDriftMonitor(brain)

    for observation in observations[:4]:
        monitor.decide("tap something", observation)
    with pytest.raises(CoordinateDriftError, match="thousand"):
        monitor.decide("tap something", observations[4])


def test_an_incomplete_window_does_not_raise() -> None:
    observations = [_observation(index) for index in range(4)]
    brain = _ScriptedBrain(
        [_click(observation, 0.5, _THOUSAND_BAND_TOP * 0.5) for observation in observations]
    )
    monitor = CoordinateDriftMonitor(brain)

    for observation in observations:
        monitor.decide("tap something", observation)


def test_normally_spread_coordinates_do_not_raise() -> None:
    observations = [_observation(index) for index in range(5)]
    heights = [0.1, 0.9, 0.2, 0.8, 0.3]
    brain = _ScriptedBrain(
        [_click(observation, 0.5, y) for observation, y in zip(observations, heights)]
    )
    monitor = CoordinateDriftMonitor(brain)

    for observation in observations:
        monitor.decide("tap something", observation)


def test_the_band_check_is_disabled_on_small_screens() -> None:
    # With height <= 999 every normalized y is below the band edge, so the
    # signal would be meaningless; the monitor must stay silent.
    observations = [_observation(index, height=800) for index in range(6)]
    brain = _ScriptedBrain([_click(observation, 0.5, 0.9) for observation in observations])
    monitor = CoordinateDriftMonitor(brain)

    for observation in observations:
        monitor.decide("tap something", observation)


def test_non_coordinate_actions_pass_through_without_filling_the_window() -> None:
    typed = _observation(10)
    proposals: list[ProposedAction] = []
    observations: list[Observation] = []
    for index in range(3):
        observation = _observation(index)
        observations.append(observation)
        proposals.append(_click(observation, 0.5, _THOUSAND_BAND_TOP * 0.5))
    observations.append(typed)
    proposals.append(
        ProposedAction(
            name="type",
            arguments={"text": "hello"},
            source_observation_id=typed.id,
        )
    )
    for index in range(3, 5):
        observation = _observation(index)
        observations.append(observation)
        proposals.append(_click(observation, 0.5, _THOUSAND_BAND_TOP * 0.5))
    brain = _ScriptedBrain(proposals)
    monitor = CoordinateDriftMonitor(brain)

    for observation in observations[:5]:
        monitor.decide("do things", observation)
    # The fifth coordinate action completes the window of five low-band y
    # values; the interleaved type action must not have diluted it.
    with pytest.raises(CoordinateDriftError, match="thousand"):
        monitor.decide("do things", observations[5])
