# Copyright (c) 2026, 东篱馆主

"""Task identifiers and lifecycle states owned by the orchestration layer."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import NewType
from uuid import uuid4

from .observation import ObservationId

TaskId = NewType("TaskId", str)


def new_task_id() -> TaskId:
    return TaskId(str(uuid4()))


class TaskState(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    WAITING_FOR_CONFIRMATION = "waiting_for_confirmation"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in {self.SUCCEEDED, self.FAILED, self.CANCELLED}


@dataclass(frozen=True, slots=True)
class PendingConfirmation:
    """The exact user-facing question that paused a task."""

    text: str
    source_observation_id: ObservationId


@dataclass(frozen=True, slots=True)
class TaskStep:
    """A small, ordered session event suitable for later audit rendering."""

    sequence: int
    detail: str
    observation_id: ObservationId | None
