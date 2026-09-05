# Copyright (c) 2026, 东篱馆主

"""Fail-closed drift detection between the MAI-UI brain and the policy layer.

Two symptoms reveal that the model's coordinate convention drifted away from
the configured one.  Signal A: a thousand-quoting model under ``thousand``
parsing emits out-of-range coordinates that generic validation would reject
without diagnosis.  Signal B: a thousand-quoting model under ``pixels``
parsing silently maps every point into the top ``999 / screen_height`` band.
The monitor raises on either symptom; it never repairs or recalibrates by
itself.
"""

from __future__ import annotations

from collections import deque
from typing import Protocol

from gui_agent.contracts import Observation, ProposedAction


class CoordinateDriftError(RuntimeError):
    """Raised when proposals suggest the coordinate convention drifted."""


class _InnerBrain(Protocol):
    def decide(self, instruction: str, observation: Observation) -> ProposedAction: ...


_COORDINATE_FIELDS = ("coordinate", "start_coordinate", "end_coordinate")
_THOUSAND = 999


class CoordinateDriftMonitor:
    """Inspect coordinate proposals for convention drift before the policy."""

    def __init__(self, inner_brain: _InnerBrain, *, window: int = 5) -> None:
        if window < 1:
            raise ValueError("drift window must be positive")
        self._inner = inner_brain
        self._recent_y: deque[float] = deque(maxlen=window)

    def decide(self, instruction: str, observation: Observation) -> ProposedAction:
        proposal = self._inner.decide(instruction, observation)
        points = self._coordinate_points(proposal)
        if not points:
            return proposal
        if any(x < 0 or x > 1 or y < 0 or y > 1 for x, y in points):
            raise CoordinateDriftError(
                "model coordinates escaped [0, 1]; the coordinate convention "
                "may no longer match the configuration, recalibrate with "
                "coordinate_scale='auto'"
            )
        height = observation.screen_height
        if height > _THOUSAND:
            self._recent_y.extend(y for _, y in points)
            if len(self._recent_y) == self._recent_y.maxlen and all(
                y < _THOUSAND / height for y in self._recent_y
            ):
                raise CoordinateDriftError(
                    "every recent coordinate landed in the thousand-convention "
                    "band; under pixels configuration the model looks like a "
                    "thousand-quoting model, recalibrate with coordinate_scale='auto'"
                )
        return proposal

    @staticmethod
    def _coordinate_points(proposal: ProposedAction) -> list[tuple[float, float]]:
        points = []
        for field in _COORDINATE_FIELDS:
            value = proposal.arguments.get(field)
            if (
                isinstance(value, (list, tuple))
                and len(value) == 2
                and all(
                    not isinstance(point, bool) and isinstance(point, (int, float))
                    for point in value
                )
            ):
                points.append((float(value[0]), float(value[1])))
        return points
