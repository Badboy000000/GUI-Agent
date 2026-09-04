# Copyright (c) 2026, 东篱馆主

"""Stable contracts shared by the brain, runtime, and platform adapters."""

from .actions import (
    ActionValidationError,
    PlatformCommand,
    ProposedAction,
    ValidatedAction,
    validate_action,
)
from .observation import Observation, ObservationId
from .task import TaskId, TaskState

__all__ = [
    "ActionValidationError",
    "Observation",
    "ObservationId",
    "PlatformCommand",
    "ProposedAction",
    "TaskId",
    "TaskState",
    "ValidatedAction",
    "validate_action",
]
