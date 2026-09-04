# Copyright (c) 2026, 东篱馆主

from __future__ import annotations

import pytest

from gui_agent.contracts import TaskState
from gui_agent.orchestration import InvalidTaskTransition, TaskStateMachine


def test_task_lifecycle_allows_confirmation_and_terminal_completion() -> None:
    state_machine = TaskStateMachine()

    assert state_machine.transition_to(TaskState.RUNNING) is TaskState.RUNNING
    assert (
        state_machine.transition_to(TaskState.WAITING_FOR_CONFIRMATION)
        is TaskState.WAITING_FOR_CONFIRMATION
    )
    assert state_machine.transition_to(TaskState.RUNNING) is TaskState.RUNNING
    assert state_machine.transition_to(TaskState.SUCCEEDED) is TaskState.SUCCEEDED
    assert state_machine.state.is_terminal


@pytest.mark.parametrize(
    ("initial", "target"),
    [
        (TaskState.CREATED, TaskState.SUCCEEDED),
        (TaskState.RUNNING, TaskState.CREATED),
        (TaskState.SUCCEEDED, TaskState.RUNNING),
        (TaskState.CANCELLED, TaskState.FAILED),
    ],
)
def test_illegal_or_terminal_transitions_are_rejected(
    initial: TaskState, target: TaskState
) -> None:
    state_machine = TaskStateMachine(state=initial)

    with pytest.raises(InvalidTaskTransition):
        state_machine.transition_to(target)
