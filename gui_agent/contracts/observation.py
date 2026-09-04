"""Immutable snapshot contract produced by a device backend."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, NewType
from uuid import uuid4

ObservationId = NewType("ObservationId", str)


@dataclass(frozen=True, slots=True)
class Observation:
    """One coherent device snapshot.

    ``sequence`` is monotonic per device connection. An action is valid only
    for the exact observation id it was decided from; the sequence is retained
    for audit and useful ordering diagnostics.
    """

    device_id: str
    sequence: int
    screen_width: int
    screen_height: int
    screenshot_path: str | None = None
    foreground_app: str | None = None
    ui_tree: Mapping[str, Any] | None = None
    captured_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    id: ObservationId = field(default_factory=lambda: ObservationId(str(uuid4())))

    def __post_init__(self) -> None:
        if not self.device_id:
            raise ValueError("device_id must not be empty")
        if self.sequence < 0:
            raise ValueError("sequence must be non-negative")
        if self.screen_width <= 0 or self.screen_height <= 0:
            raise ValueError("screen dimensions must be positive")
        if self.captured_at.tzinfo is None:
            raise ValueError("captured_at must be timezone-aware")
