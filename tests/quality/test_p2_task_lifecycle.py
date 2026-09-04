# Copyright (c) 2026, 东篱馆主

"""Black-box acceptance coverage for the Android-only P2 task lifecycle."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from gui_agent.actions import AndroidActionCompiler
from gui_agent.audit import AuditEventKind, JsonlAuditRecorder, load_replay
from gui_agent.contracts import Observation, PlatformCommand, ProposedAction, TaskId, TaskState
from gui_agent.orchestration import TaskRunner, TaskSession


class ScriptedDevice:
    """In-memory Android device port; no ADB process or real device is used."""

    def __init__(self, observations: Iterable[Observation]) -> None:
        self._observations = iter(observations)
        self.health_calls = 0
        self.observe_calls = 0
        self.executed: list[PlatformCommand] = []

    @property
    def device_id(self) -> str:
        return "android-acceptance-fake"

    def health(self) -> bool:
        self.health_calls += 1
        return True

    def observe(self) -> Observation:
        self.observe_calls += 1
        return next(self._observations)

    def execute(self, command: PlatformCommand) -> None:
        self.executed.append(command)


class ScriptedBrain:
    def __init__(self, actions: Iterable[ProposedAction]) -> None:
        self._actions = iter(actions)
        self.calls = 0

    def decide(self, instruction: str, observation: Observation) -> ProposedAction:
        self.calls += 1
        return next(self._actions)


class AcceptingVerifier:
    def verify(self, instruction: str, observation: Observation) -> bool:
        return True


class SelectivelyFailingRecorder:
    """Exercises the runner's audit-failure policy without a filesystem sink."""

    def __init__(self, failing_kind: AuditEventKind) -> None:
        self._failing_kind = failing_kind
        self.kinds: list[AuditEventKind] = []

    def record(self, task_id: str, kind: AuditEventKind, payload: object) -> None:
        self.kinds.append(kind)
        if kind is self._failing_kind:
            raise RuntimeError("injected audit failure")


@dataclass(frozen=True, slots=True)
class StabilityResult:
    stable: bool
    last_observation: Observation | None
    samples: int
    consecutive_samples: int
    reason: str
    elapsed_seconds: float


def observation(sequence: int) -> Observation:
    return Observation(
        device_id="android-acceptance-fake",
        sequence=sequence,
        screen_width=1080,
        screen_height=2400,
        screenshot_path=f"C:/safe-artifacts/{sequence}.png",
        foreground_app="com.example.android",
    )


def recorded_kinds(path: Path) -> list[AuditEventKind]:
    return [event.kind for event in load_replay(path).events]


def test_confirmation_approval_resumes_and_replays_a_safe_ordered_trail(tmp_path: Path) -> None:
    audit_path = tmp_path / "approval.jsonl"
    first, second = observation(0), observation(1)
    device = ScriptedDevice([first, second])
    question = "Approve the irreversible-looking operation?"
    runner = TaskRunner(
        device,
        ScriptedBrain(
            [
                ProposedAction("ask_user", {"text": question}, first.id),
                ProposedAction("terminate", {"status": "success"}, second.id),
            ]
        ),
        AndroidActionCompiler(),
        AcceptingVerifier(),
        session=TaskSession(TaskId("approval-task")),
        audit_recorder=JsonlAuditRecorder(audit_path),
    )

    paused = runner.run("perform a controlled operation")
    assert paused.state is TaskState.WAITING_FOR_CONFIRMATION
    assert paused.pending_confirmation is not None
    assert runner.approve_confirmation() is TaskState.RUNNING
    finished = runner.run("perform a controlled operation")

    assert finished.state is TaskState.SUCCEEDED
    assert device.executed == []
    assert recorded_kinds(audit_path) == [
        AuditEventKind.TASK_STARTED,
        AuditEventKind.DEVICE_HEALTH_CHECKED,
        AuditEventKind.OBSERVATION_RECEIVED,
        AuditEventKind.ACTION_PROPOSED,
        AuditEventKind.ACTION_VALIDATED,
        AuditEventKind.CONFIRMATION_REQUESTED,
        AuditEventKind.CONFIRMATION_APPROVED,
        AuditEventKind.DEVICE_HEALTH_CHECKED,
        AuditEventKind.OBSERVATION_RECEIVED,
        AuditEventKind.ACTION_PROPOSED,
        AuditEventKind.ACTION_VALIDATED,
        AuditEventKind.TASK_FINISHED,
    ]
    assert question not in audit_path.read_text(encoding="utf-8")


def test_human_takeover_cancels_without_resuming_or_executing_device_input(tmp_path: Path) -> None:
    audit_path = tmp_path / "takeover.jsonl"
    first = observation(0)
    device = ScriptedDevice([first])
    runner = TaskRunner(
        device,
        ScriptedBrain([ProposedAction("ask_user", {"text": "Continue?"}, first.id)]),
        AndroidActionCompiler(),
        AcceptingVerifier(),
        session=TaskSession(TaskId("takeover-task")),
        audit_recorder=JsonlAuditRecorder(audit_path),
    )

    assert runner.run("wait for a person").state is TaskState.WAITING_FOR_CONFIRMATION
    taken_over = runner.take_over("operator will enter private details")
    terminal_attempt = runner.run("wait for a person")

    assert taken_over.state is TaskState.CANCELLED
    assert terminal_attempt.state is TaskState.CANCELLED
    assert device.executed == []
    assert device.observe_calls == 1
    assert recorded_kinds(audit_path) == [
        AuditEventKind.TASK_STARTED,
        AuditEventKind.DEVICE_HEALTH_CHECKED,
        AuditEventKind.OBSERVATION_RECEIVED,
        AuditEventKind.ACTION_PROPOSED,
        AuditEventKind.ACTION_VALIDATED,
        AuditEventKind.CONFIRMATION_REQUESTED,
        AuditEventKind.HUMAN_TAKEOVER,
        AuditEventKind.TASK_FINISHED,
    ]
    audit_text = audit_path.read_text(encoding="utf-8")
    assert "operator will enter private details" not in audit_text
    assert '"reason_provided":true' in audit_text


def test_approval_audit_failure_fails_the_paused_task_without_executing_input() -> None:
    first = observation(0)
    device = ScriptedDevice([first])
    recorder = SelectivelyFailingRecorder(AuditEventKind.CONFIRMATION_APPROVED)
    runner = TaskRunner(
        device,
        ScriptedBrain([ProposedAction("ask_user", {"text": "Continue?"}, first.id)]),
        AndroidActionCompiler(),
        AcceptingVerifier(),
        session=TaskSession(TaskId("approval-audit-failure-task")),
        audit_recorder=recorder,
    )

    assert runner.run("wait for approval").state is TaskState.WAITING_FOR_CONFIRMATION
    assert runner.approve_confirmation() is TaskState.FAILED
    terminal_attempt = runner.run("wait for approval")

    assert terminal_attempt.state is TaskState.FAILED
    assert runner.pending_confirmation is None
    assert device.executed == []
    assert device.observe_calls == 1
    assert recorder.kinds[-3:] == [
        AuditEventKind.CONFIRMATION_APPROVED,
        AuditEventKind.TASK_ERROR,
        AuditEventKind.TASK_FINISHED,
    ]


def test_takeover_audit_failure_returns_failed_without_executing_input() -> None:
    first = observation(0)
    device = ScriptedDevice([first])
    recorder = SelectivelyFailingRecorder(AuditEventKind.HUMAN_TAKEOVER)
    runner = TaskRunner(
        device,
        ScriptedBrain([ProposedAction("ask_user", {"text": "Continue?"}, first.id)]),
        AndroidActionCompiler(),
        AcceptingVerifier(),
        session=TaskSession(TaskId("takeover-audit-failure-task")),
        audit_recorder=recorder,
    )

    assert runner.run("wait for takeover").state is TaskState.WAITING_FOR_CONFIRMATION
    result = runner.take_over("operator intervention")

    assert result.state is TaskState.FAILED
    assert "injected audit failure" in result.detail
    assert runner.pending_confirmation is None
    assert device.executed == []
    assert device.observe_calls == 1
    assert recorder.kinds[-3:] == [
        AuditEventKind.HUMAN_TAKEOVER,
        AuditEventKind.TASK_ERROR,
        AuditEventKind.TASK_FINISHED,
    ]


def test_stable_wait_records_completion_then_allows_the_task_to_succeed(tmp_path: Path) -> None:
    audit_path = tmp_path / "stable-wait.jsonl"
    before_wait, stable_observation, after_wait = observation(0), observation(1), observation(2)
    device = ScriptedDevice([before_wait, after_wait])
    runner = TaskRunner(
        device,
        ScriptedBrain(
            [
                ProposedAction("wait", {}, before_wait.id),
                ProposedAction("terminate", {"status": "success"}, after_wait.id),
            ]
        ),
        AndroidActionCompiler(),
        AcceptingVerifier(),
        session=TaskSession(TaskId("stable-wait-task")),
        audit_recorder=JsonlAuditRecorder(audit_path),
        stability_waiter=lambda: StabilityResult(
            stable=True,
            last_observation=stable_observation,
            samples=2,
            consecutive_samples=2,
            reason="stable",
            elapsed_seconds=0.5,
        ),
    )

    result = runner.run("wait for the interface to settle")

    assert result.state is TaskState.SUCCEEDED
    assert device.executed == []
    kinds = recorded_kinds(audit_path)
    assert kinds.count(AuditEventKind.OBSERVATION_RECEIVED) == 3
    assert AuditEventKind.STABILITY_WAIT_COMPLETED in kinds
    assert AuditEventKind.STABILITY_WAIT_TIMEOUT not in kinds
    assert kinds[-1] is AuditEventKind.TASK_FINISHED


def test_timed_out_wait_fails_without_another_decision_or_device_command(tmp_path: Path) -> None:
    audit_path = tmp_path / "timed-out-wait.jsonl"
    before_wait = observation(0)
    device = ScriptedDevice([before_wait])
    brain = ScriptedBrain([ProposedAction("wait", {}, before_wait.id)])
    runner = TaskRunner(
        device,
        brain,
        AndroidActionCompiler(),
        AcceptingVerifier(),
        session=TaskSession(TaskId("timeout-task")),
        audit_recorder=JsonlAuditRecorder(audit_path),
        stability_waiter=lambda: StabilityResult(
            stable=False,
            last_observation=None,
            samples=5,
            consecutive_samples=1,
            reason="timed out before the UI became stable",
            elapsed_seconds=1.0,
        ),
    )

    result = runner.run("wait for the interface to settle")

    assert result.state is TaskState.FAILED
    assert result.detail == "timed out before the UI became stable"
    assert brain.calls == 1
    assert device.observe_calls == 1
    assert device.executed == []
    assert recorded_kinds(audit_path) == [
        AuditEventKind.TASK_STARTED,
        AuditEventKind.DEVICE_HEALTH_CHECKED,
        AuditEventKind.OBSERVATION_RECEIVED,
        AuditEventKind.ACTION_PROPOSED,
        AuditEventKind.ACTION_VALIDATED,
        AuditEventKind.STABILITY_WAIT_TIMEOUT,
        AuditEventKind.TASK_FINISHED,
    ]
