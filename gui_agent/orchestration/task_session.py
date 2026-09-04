# Copyright (c) 2026, 东篱馆主

"""In-memory task context for confirmation and human takeover."""

from __future__ import annotations

from gui_agent.contracts import Observation
from gui_agent.contracts.task import (
    PendingConfirmation,
    TaskId,
    TaskState,
    TaskStep,
    new_task_id,
)
from gui_agent.orchestration.task_state_machine import (
    InvalidTaskTransition,
    TaskStateMachine,
)


class TaskSession:
    """Keep the recoverable context of one in-process task run.

    The session deliberately owns no execution policy or persistence.  Callers
    use it to retain enough context to render a confirmation prompt, resume
    after approval, or hand the device back to a person without reopening the
    task.
    """

    def __init__(self, task_id: TaskId | None = None) -> None:
        self._machine = TaskStateMachine(
            task_id=task_id if task_id is not None else new_task_id()
        )
        self._steps: list[TaskStep] = []
        self._last_observation: Observation | None = None
        self._pending_confirmation: PendingConfirmation | None = None
        self._takeover_reason: str | None = None

    @property
    def task_id(self) -> TaskId:
        """The stable identifier shared by all events in this session."""

        return self._machine.task_id

    @property
    def state(self) -> TaskState:
        """The current lifecycle state; it cannot be assigned directly."""

        return self._machine.state

    @property
    def steps(self) -> tuple[TaskStep, ...]:
        """Ordered, immutable-view session events."""

        return tuple(self._steps)

    @property
    def last_observation(self) -> Observation | None:
        """Most recent observation reported by the task runner."""

        return self._last_observation

    @property
    def pending_confirmation(self) -> PendingConfirmation | None:
        """The prompt awaiting a human decision, if the task is paused."""

        return self._pending_confirmation

    @property
    def takeover_reason(self) -> str | None:
        """The human-supplied reason for a cancelled, taken-over task."""

        return self._takeover_reason

    def start(self) -> TaskState:
        """Start a newly created task."""

        return self.transition_to(TaskState.RUNNING)

    def transition_to(self, target: TaskState) -> TaskState:
        """Apply an ordinary lifecycle transition.

        Entering or leaving a confirmation pause requires the dedicated methods
        below so the pending prompt cannot be silently discarded.
        """

        if target is TaskState.WAITING_FOR_CONFIRMATION:
            raise InvalidTaskTransition(
                "use wait_for_confirmation() to enter a confirmation pause"
            )
        if self.state is TaskState.WAITING_FOR_CONFIRMATION:
            raise InvalidTaskTransition(
                "use approve() or take_over() while waiting for confirmation"
            )
        return self._machine.transition_to(target)

    def wait_for_confirmation(self, text: str, observation: Observation) -> TaskState:
        """Pause a running task and retain the precise ask-user context."""

        if not isinstance(text, str) or not text.strip():
            raise ValueError("confirmation text must be a non-empty string")
        self._machine.transition_to(TaskState.WAITING_FOR_CONFIRMATION)
        self._last_observation = observation
        self._pending_confirmation = PendingConfirmation(text, observation.id)
        return self.state

    def approve(self) -> TaskState:
        """Resume a paused task after an explicit human approval."""

        if self._pending_confirmation is None:
            raise InvalidTaskTransition("no confirmation is pending")
        self._machine.transition_to(TaskState.RUNNING)
        self._pending_confirmation = None
        return self.state

    def take_over(self, reason: str) -> TaskState:
        """Cancel a paused task after a person assumes control of the device."""

        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("takeover reason must be a non-empty string")
        if self._pending_confirmation is None:
            raise InvalidTaskTransition("human takeover requires a pending confirmation")
        self._machine.transition_to(TaskState.CANCELLED)
        self._takeover_reason = reason
        self._pending_confirmation = None
        self.record_step(f"human takeover: {reason}")
        return self.state

    def fail(self) -> TaskState:
        """Fail a session when the runtime cannot preserve its safety boundary.

        A failed audit write is an execution-boundary failure, not a human
        decision. It must therefore be able to leave a confirmation pause
        without pretending that the prompt was approved or taken over.
        """

        if self.state.is_terminal:
            return self.state
        self._machine.transition_to(TaskState.FAILED)
        self._pending_confirmation = None
        return self.state

    def record_observation(self, observation: Observation) -> None:
        """Update the latest device snapshot without changing lifecycle state."""

        self._last_observation = observation

    def record_step(self, detail: str, observation: Observation | None = None) -> TaskStep:
        """Append a small audit event, optionally updating its observation context."""

        if not isinstance(detail, str) or not detail.strip():
            raise ValueError("step detail must be a non-empty string")
        if observation is not None:
            self.record_observation(observation)
        step = TaskStep(
            sequence=len(self._steps) + 1,
            detail=detail,
            observation_id=(
                self._last_observation.id if self._last_observation is not None else None
            ),
        )
        self._steps.append(step)
        return step
