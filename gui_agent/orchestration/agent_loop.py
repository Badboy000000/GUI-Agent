# Copyright (c) 2026, 东篱馆主

"""Auditable, recoverable observe-decide-validate-execute task loop."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from time import sleep
from typing import Protocol

from gui_agent.audit import AuditEventKind, AuditRecorder
from gui_agent.contracts import Observation, ProposedAction, TaskState, validate_action
from gui_agent.contracts.task import PendingConfirmation, TaskId
from gui_agent.orchestration.task_session import TaskSession
from gui_agent.platforms.base import DeviceBackend


class Brain(Protocol):
    """The small seam between task orchestration and a decision model."""

    def decide(self, instruction: str, observation: Observation) -> ProposedAction: ...


class ActionCompiler(Protocol):
    """The small seam between a validated action and a platform primitive."""

    def compile(self, action, observation: Observation): ...


class SuccessVerifier(Protocol):
    """Determines whether the current observed state satisfies a task."""

    def verify(self, instruction: str, observation: Observation) -> bool: ...


class UiStabilityResult(Protocol):
    """Read-only result returned by an optional platform-specific waiter."""

    stable: bool
    last_observation: Observation | None
    samples: int
    consecutive_samples: int
    reason: str
    elapsed_seconds: float


StabilityWaiter = Callable[[], UiStabilityResult]


@dataclass(frozen=True, slots=True)
class TaskResult:
    """The externally consumable snapshot of a recoverable task session."""

    task_id: TaskId
    state: TaskState
    steps: int
    detail: str
    last_observation: Observation | None
    pending_confirmation: PendingConfirmation | None


class TaskRunner:
    """Own one bounded, side-effect-aware task run.

    Passing ``UiStabilityWaiter(backend).wait`` as ``stability_waiter`` enables
    Android's screenshot-based stable wait without coupling the orchestration
    layer to an Android import. A caller must resume an ``ask_user`` pause by
    calling :meth:`approve_confirmation` before calling :meth:`run` again, or
    call :meth:`take_over` to cancel the session and hand the device to a user.
    """

    def __init__(
        self,
        backend: DeviceBackend,
        brain: Brain,
        compiler: ActionCompiler,
        verifier: SuccessVerifier,
        *,
        max_steps: int = 20,
        wait_seconds: float = 1.0,
        sleeper: Callable[[float], None] = sleep,
        session: TaskSession | None = None,
        audit_recorder: AuditRecorder | None = None,
        stability_waiter: StabilityWaiter | None = None,
    ) -> None:
        if max_steps <= 0:
            raise ValueError("max_steps must be positive")
        if wait_seconds < 0:
            raise ValueError("wait_seconds must not be negative")
        self._backend = backend
        self._brain = brain
        self._compiler = compiler
        self._verifier = verifier
        self._max_steps = max_steps
        self._wait_seconds = wait_seconds
        self._sleeper = sleeper
        self._session = session or TaskSession()
        self._audit_recorder = audit_recorder
        self._stability_waiter = stability_waiter

    @property
    def task_id(self) -> TaskId:
        """Return the stable ID required to correlate one task's audit trail."""

        return self._session.task_id

    @property
    def state(self) -> TaskState:
        """Return the current lifecycle state without exposing mutation."""

        return self._session.state

    @property
    def pending_confirmation(self) -> PendingConfirmation | None:
        """Return the current user prompt, if the run is paused."""

        return self._session.pending_confirmation

    def approve_confirmation(self) -> TaskState:
        """Record a human approval and make a paused task runnable again."""

        pending = self._session.pending_confirmation
        if pending is None:
            return self._session.approve()
        try:
            self._record(
                AuditEventKind.CONFIRMATION_APPROVED,
                {"source_observation_id": str(pending.source_observation_id)},
            )
            return self._session.approve()
        except Exception as error:
            return self._fail_after_error(error).state

    def take_over(self, reason: str) -> TaskResult:
        """Cancel a paused task after a person assumes device control.

        The reason remains in the in-memory session for the operator. The
        persistent audit trail records only that a non-empty reason was given,
        never its potentially sensitive text.
        """

        pending = self._session.pending_confirmation
        if pending is None or not isinstance(reason, str) or not reason.strip():
            # Preserve the session's domain-level diagnostic for invalid calls.
            self._session.take_over(reason)
        try:
            self._record(AuditEventKind.HUMAN_TAKEOVER, {"reason_provided": True})
            self._record(AuditEventKind.TASK_FINISHED, {"state": TaskState.CANCELLED.value})
        except Exception as error:
            return self._fail_after_error(error)
        self._session.take_over(reason)
        return self._result("human took over device")

    def run(self, instruction: str) -> TaskResult:
        """Advance the current session until it pauses or reaches a terminal state."""

        if self._session.state is TaskState.CREATED:
            self._session.start()
            try:
                device_id = getattr(self._backend, "device_id", None)
                self._record(
                    AuditEventKind.TASK_STARTED,
                    {"device_id": device_id if isinstance(device_id, str) else None},
                )
            except Exception as error:
                return self._fail_after_error(error)
        elif self._session.state is TaskState.WAITING_FOR_CONFIRMATION:
            return self._result("human confirmation is required before resuming")
        elif self._session.state.is_terminal:
            return self._result("task session is already terminal")

        try:
            healthy = self._backend.health()
            self._record(AuditEventKind.DEVICE_HEALTH_CHECKED, {"healthy": healthy})
            if not healthy:
                return self._finish(TaskState.FAILED, "device health check failed")

            for _ in range(self._max_steps):
                observation = self._backend.observe()
                self._record_observation(observation)
                proposed = self._brain.decide(instruction, observation)
                self._record(AuditEventKind.ACTION_PROPOSED, self._proposed_action_payload(proposed))
                validated = validate_action(proposed, observation)
                self._record(
                    AuditEventKind.ACTION_VALIDATED,
                    self._validated_action_payload(
                        validated.name, validated.arguments, validated.validation_id
                    ),
                )

                if validated.name == "terminate":
                    if (
                        validated.arguments["status"] == "success"
                        and self._verifier.verify(instruction, observation)
                    ):
                        return self._finish(TaskState.SUCCEEDED, "success verified")
                    return self._finish(TaskState.FAILED, "model termination was not verified")

                if validated.name == "ask_user":
                    self._session.wait_for_confirmation(validated.arguments["text"], observation)
                    self._session.record_step("confirmation requested", observation)
                    self._record(
                        AuditEventKind.CONFIRMATION_REQUESTED,
                        {"source_observation_id": str(observation.id)},
                    )
                    return self._result(validated.arguments["text"])

                if validated.name == "wait":
                    self._session.record_step("wait requested", observation)
                    stability_result = self._wait_for_stable_ui()
                    if stability_result is None:
                        continue
                    if stability_result.last_observation is not None:
                        self._record_observation(stability_result.last_observation)
                    self._record_stability_result(stability_result)
                    if not stability_result.stable:
                        return self._finish(TaskState.FAILED, stability_result.reason)
                    continue

                command = self._compiler.compile(validated, observation)
                self._session.record_step(f"command attempted: {command.name}", observation)
                self._backend.execute(command)
                self._record(
                    AuditEventKind.COMMAND_EXECUTED,
                    {"name": command.name, "validation_id": command.validation_id},
                )

            return self._finish(TaskState.FAILED, "step budget exhausted")
        except Exception as error:
            return self._fail_after_error(error)

    def _wait_for_stable_ui(self) -> UiStabilityResult | None:
        """Use Android stability polling when supplied; retain MVP compatibility otherwise."""

        if self._stability_waiter is None:
            self._sleeper(self._wait_seconds)
            return None
        return self._stability_waiter()

    def _record_observation(self, observation: Observation) -> None:
        self._session.record_observation(observation)
        self._record(
            AuditEventKind.OBSERVATION_RECEIVED,
            {
                "observation_id": str(observation.id),
                "sequence": observation.sequence,
                "screen_width": observation.screen_width,
                "screen_height": observation.screen_height,
                "foreground_app": observation.foreground_app,
                "screenshot_path": observation.screenshot_path,
                "ui_tree_available": observation.ui_tree is not None,
            },
        )

    def _record_stability_result(self, result: UiStabilityResult) -> None:
        kind = (
            AuditEventKind.STABILITY_WAIT_COMPLETED
            if result.stable
            else AuditEventKind.STABILITY_WAIT_TIMEOUT
        )
        self._record(
            kind,
            {
                "samples": result.samples,
                "consecutive_samples": result.consecutive_samples,
                "elapsed_seconds": result.elapsed_seconds,
                "reason": result.reason,
            },
        )

    def _finish(self, target: TaskState, detail: str) -> TaskResult:
        self._record(AuditEventKind.TASK_FINISHED, {"state": target.value})
        self._session.transition_to(target)
        return self._result(detail)

    def _fail_after_error(self, error: Exception) -> TaskResult:
        detail = str(error)
        if not self._session.state.is_terminal:
            try:
                self._record(AuditEventKind.TASK_ERROR, {"error_type": type(error).__name__})
                self._record(AuditEventKind.TASK_FINISHED, {"state": TaskState.FAILED.value})
            except Exception as audit_error:
                detail = f"{detail}; audit recording failed: {type(audit_error).__name__}"
            self._session.fail()
        return self._result(detail)

    def _record(self, kind: AuditEventKind, payload: Mapping[str, object]) -> None:
        if self._audit_recorder is not None:
            self._audit_recorder.record(str(self._session.task_id), kind, payload)

    @staticmethod
    def _proposed_action_payload(proposed: ProposedAction) -> dict[str, object]:
        argument_keys = (
            sorted(str(key) for key in proposed.arguments)
            if isinstance(proposed.arguments, Mapping)
            else []
        )
        return {
            "name": proposed.name,
            "source_observation_id": str(proposed.source_observation_id),
            "argument_keys": argument_keys,
        }

    @staticmethod
    def _validated_action_payload(
        name: str, arguments: Mapping[str, object], validation_id: str
    ) -> dict[str, object]:
        return {
            "name": name,
            "validation_id": validation_id,
            "argument_keys": sorted(str(key) for key in arguments),
        }

    def _result(self, detail: str) -> TaskResult:
        return TaskResult(
            task_id=self._session.task_id,
            state=self._session.state,
            steps=len(self._session.steps),
            detail=detail,
            last_observation=self._session.last_observation,
            pending_confirmation=self._session.pending_confirmation,
        )
