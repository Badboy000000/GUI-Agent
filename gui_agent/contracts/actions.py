"""The trust boundary between model proposals and platform commands."""

from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite
from typing import Any, Mapping
from uuid import uuid4

from .observation import Observation, ObservationId


class ActionValidationError(ValueError):
    """Raised when an untrusted model action cannot enter the execution path."""


@dataclass(frozen=True, slots=True)
class ProposedAction:
    """Untrusted, normalized MAI-UI action proposed for one observation."""

    name: str
    arguments: Mapping[str, Any]
    source_observation_id: ObservationId
    rationale: str | None = None


@dataclass(frozen=True, slots=True)
class ValidatedAction:
    """A proposal proven current and structurally safe for compilation."""

    name: str
    arguments: Mapping[str, Any]
    source_observation_id: ObservationId
    validation_id: str = field(default_factory=lambda: str(uuid4()))


@dataclass(frozen=True, slots=True)
class PlatformCommand:
    """A platform-neutral primitive emitted only after action validation."""

    name: str
    arguments: Mapping[str, Any]
    validation_id: str


_COORDINATE_ACTIONS = {"click", "double_click", "long_press"}
_KNOWN_ACTIONS = _COORDINATE_ACTIONS | {
    "type",
    "swipe",
    "drag",
    "open",
    "system_button",
    "wait",
    "terminate",
    "answer",
    "ask_user",
}
_SYSTEM_BUTTONS = {"back", "home", "menu", "enter"}
_DIRECTIONS = {"up", "down", "left", "right"}


def validate_action(proposed: ProposedAction, observation: Observation) -> ValidatedAction:
    """Validate one MAI-UI proposal against the current device snapshot.

    This intentionally does not authorize side effects or turn the action into
    ADB input. Policy evaluation and platform compilation remain separate.
    """

    if proposed.source_observation_id != observation.id:
        raise ActionValidationError(
            "stale action: proposal does not reference the current observation"
        )
    if proposed.name not in _KNOWN_ACTIONS:
        raise ActionValidationError(f"unsupported action: {proposed.name}")

    args = proposed.arguments
    if not isinstance(args, Mapping):
        raise ActionValidationError("action arguments must be a mapping")
    if proposed.name in _COORDINATE_ACTIONS:
        _validate_coordinate(args.get("coordinate"), "coordinate")
    elif proposed.name == "drag":
        _validate_coordinate(args.get("start_coordinate"), "start_coordinate")
        _validate_coordinate(args.get("end_coordinate"), "end_coordinate")
    elif proposed.name == "swipe":
        direction = args.get("direction")
        if direction not in _DIRECTIONS:
            raise ActionValidationError("swipe direction must be up, down, left, or right")
        if "coordinate" in args:
            _validate_coordinate(args["coordinate"], "coordinate")
    elif proposed.name in {"type", "open", "answer", "ask_user"}:
        if not isinstance(args.get("text"), str) or not args["text"]:
            raise ActionValidationError(f"{proposed.name} action requires non-empty text")
    elif proposed.name == "system_button":
        if args.get("button") not in _SYSTEM_BUTTONS:
            raise ActionValidationError("unsupported system button")
    elif proposed.name == "terminate":
        if args.get("status") not in {"success", "fail"}:
            raise ActionValidationError("terminate status must be success or fail")

    return ValidatedAction(
        name=proposed.name,
        arguments=dict(args),
        source_observation_id=proposed.source_observation_id,
    )


def _validate_coordinate(value: Any, field_name: str) -> None:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ActionValidationError(f"{field_name} must contain exactly two normalized values")
    if any(isinstance(point, bool) or not isinstance(point, (int, float)) for point in value):
        raise ActionValidationError(f"{field_name} values must be numbers")
    if any(not isfinite(point) or point < 0 or point > 1 for point in value):
        raise ActionValidationError(f"{field_name} values must be in [0, 1]")
