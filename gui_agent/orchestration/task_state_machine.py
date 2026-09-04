# Copyright (c) 2026, 东篱馆主

"""Strict task lifecycle transition guard."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from gui_agent.contracts.task import TaskId, TaskState, new_task_id


class InvalidTaskTransition(ValueError):
    """Raised when an orchestration caller requests an illegal state change."""


_ALLOWED_TRANSITIONS: Mapping[TaskState, frozenset[TaskState]] = {
    TaskState.CREATED: frozenset({TaskState.RUNNING, TaskState.CANCELLED}),
    TaskState.RUNNING: frozenset(
        {
            TaskState.WAITING_FOR_CONFIRMATION,
            TaskState.SUCCEEDED,
            TaskState.FAILED,
            TaskState.CANCELLED,
        }
    ),
    TaskState.WAITING_FOR_CONFIRMATION: frozenset(
        {TaskState.RUNNING, TaskState.FAILED, TaskState.CANCELLED}
    ),
    TaskState.SUCCEEDED: frozenset(),
    TaskState.FAILED: frozenset(),
    TaskState.CANCELLED: frozenset(),
}


@dataclass(slots=True)
class TaskStateMachine:
    """Owns a single task lifecycle; terminal states can never be reopened."""

    task_id: TaskId = field(default_factory=new_task_id)
    state: TaskState = TaskState.CREATED

    def transition_to(self, target: TaskState) -> TaskState:
        if not isinstance(target, TaskState):
            raise TypeError("target must be a TaskState")
        if target not in _ALLOWED_TRANSITIONS[self.state]:
            raise InvalidTaskTransition(f"cannot transition {self.state.value} to {target.value}")
        self.state = target
        return self.state
