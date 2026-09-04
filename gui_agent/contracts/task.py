"""Task identifiers and lifecycle states owned by the orchestration layer."""

from __future__ import annotations

from enum import Enum
from typing import NewType
from uuid import uuid4

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
