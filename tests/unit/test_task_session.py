# Copyright (c) 2026, 东篱馆主

from __future__ import annotations

import pytest

from gui_agent.contracts import Observation, TaskId, TaskState
from gui_agent.orchestration import InvalidTaskTransition, TaskSession


def _observation(sequence: int = 1) -> Observation:
    return Observation(
        device_id="emulator-5554",
        sequence=sequence,
        screen_width=1080,
        screen_height=2400,
    )


def test_session_preserves_confirmation_context_and_resumes_only_after_approval() -> None:
    session = TaskSession(task_id=TaskId("task-42"))
    observation = _observation()

    assert session.task_id == TaskId("task-42")
    assert session.state is TaskState.CREATED
    assert session.start() is TaskState.RUNNING
    assert (
        session.wait_for_confirmation("Continue with payment?", observation)
        is TaskState.WAITING_FOR_CONFIRMATION
    )
    assert session.last_observation is observation
    assert session.pending_confirmation is not None
    assert session.pending_confirmation.text == "Continue with payment?"
    assert session.pending_confirmation.source_observation_id == observation.id

    assert session.approve() is TaskState.RUNNING
    assert session.pending_confirmation is None
    assert session.transition_to(TaskState.SUCCEEDED) is TaskState.SUCCEEDED


def test_human_takeover_cancels_the_task_and_records_the_reason() -> None:
    session = TaskSession()
    observation = _observation()
    session.start()
    session.wait_for_confirmation("Ready to submit?", observation)

    assert session.take_over("I will review the form myself") is TaskState.CANCELLED
    assert session.takeover_reason == "I will review the form myself"
    assert session.pending_confirmation is None
    assert len(session.steps) == 1
    assert session.steps[0].sequence == 1
    assert session.steps[0].detail == "human takeover: I will review the form myself"
    assert session.steps[0].observation_id == observation.id


def test_session_rejects_bypassing_confirmation_and_terminal_transitions() -> None:
    session = TaskSession()

    with pytest.raises(InvalidTaskTransition, match="wait_for_confirmation"):
        session.transition_to(TaskState.WAITING_FOR_CONFIRMATION)
    with pytest.raises(InvalidTaskTransition, match="no confirmation"):
        session.approve()
    with pytest.raises(InvalidTaskTransition, match="requires a pending confirmation"):
        session.take_over("I need to take over")

    session.start()
    session.transition_to(TaskState.SUCCEEDED)

    with pytest.raises(InvalidTaskTransition):
        session.transition_to(TaskState.RUNNING)
    with pytest.raises(InvalidTaskTransition):
        session.wait_for_confirmation("Still running?", _observation())


def test_session_steps_expose_an_immutable_view_and_track_the_latest_observation() -> None:
    session = TaskSession()
    first_observation = _observation(1)
    second_observation = _observation(2)

    session.start()
    first_step = session.record_step("observed launch screen", first_observation)
    session.record_observation(second_observation)
    second_step = session.record_step("executed tap")

    assert isinstance(session.steps, tuple)
    assert first_step.sequence == 1
    assert first_step.observation_id == first_observation.id
    assert second_step.sequence == 2
    assert second_step.observation_id == second_observation.id
    assert session.last_observation is second_observation

    with pytest.raises(AttributeError):
        session.state = TaskState.FAILED  # type: ignore[misc]
