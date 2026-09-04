# Copyright (c) 2026, 东篱馆主

"""Black-box acceptance tests for the Android MVP task boundary.

These tests intentionally use no ADB process or model service.  They exercise
the public orchestration, contract, compiler, and device-port APIs together so
that the safety boundary remains testable without a connected device.
"""

from __future__ import annotations

from collections.abc import Iterable

from gui_agent.actions import AndroidActionCompiler
from gui_agent.contracts import Observation, PlatformCommand, ProposedAction, TaskState
from gui_agent.orchestration import TaskRunner
from gui_agent.platforms.base import DeviceBackend


class FakeDevice(DeviceBackend):
    """A deterministic device port which records observable side effects."""

    def __init__(
        self,
        observations: Iterable[Observation],
        events: list[str],
        *,
        healthy: bool = True,
        fail_execute: bool = False,
    ) -> None:
        self._observations = iter(observations)
        self._events = events
        self._healthy = healthy
        self._fail_execute = fail_execute
        self.executed: list[PlatformCommand] = []

    @property
    def device_id(self) -> str:
        return "fake-android"

    def health(self) -> bool:
        self._events.append("health")
        return self._healthy

    def observe(self) -> Observation:
        self._events.append("observe")
        return next(self._observations)

    def execute(self, command: PlatformCommand) -> None:
        self._events.append(f"execute:{command.name}")
        self.executed.append(command)
        if self._fail_execute:
            raise RuntimeError("injected device failure")

    def close(self) -> None:
        pass


class ScriptedBrain:
    def __init__(self, actions: Iterable[ProposedAction], events: list[str]) -> None:
        self._actions = iter(actions)
        self._events = events

    def decide(self, instruction: str, observation: Observation) -> ProposedAction:
        self._events.append("decide")
        return next(self._actions)


class RecordingVerifier:
    def __init__(self, events: list[str], *, accepted: bool = True) -> None:
        self._events = events
        self._accepted = accepted

    def verify(self, instruction: str, observation: Observation) -> bool:
        self._events.append("verify")
        return self._accepted


def observation(sequence: int) -> Observation:
    return Observation(
        device_id="fake-android",
        sequence=sequence,
        screen_width=100,
        screen_height=200,
    )


def test_runner_completes_the_observe_decide_validate_execute_loop() -> None:
    events: list[str] = []
    first, second = observation(0), observation(1)
    device = FakeDevice([first, second], events)
    brain = ScriptedBrain(
        [
            ProposedAction("click", {"coordinate": [0.25, 0.75]}, first.id),
            ProposedAction("terminate", {"status": "success"}, second.id),
        ],
        events,
    )

    result = TaskRunner(
        device, brain, AndroidActionCompiler(), RecordingVerifier(events), max_steps=3
    ).run("open the target")

    assert result.state is TaskState.SUCCEEDED
    assert result.steps == 1
    assert [(command.name, dict(command.arguments)) for command in device.executed] == [
        ("tap", {"x": 25, "y": 149})
    ]
    assert events == ["health", "observe", "decide", "execute:tap", "observe", "decide", "verify"]


def test_runner_rejects_a_stale_action_before_compiling_or_executing() -> None:
    events: list[str] = []
    current, stale = observation(0), observation(99)
    device = FakeDevice([current], events)
    brain = ScriptedBrain(
        [ProposedAction("click", {"coordinate": [0.5, 0.5]}, stale.id)], events
    )

    result = TaskRunner(
        device, brain, AndroidActionCompiler(), RecordingVerifier(events), max_steps=1
    ).run("do not act on stale data")

    assert result.state is TaskState.FAILED
    assert "stale action" in result.detail
    assert device.executed == []
    assert events == ["health", "observe", "decide"]


def test_runner_marks_a_device_execution_error_as_failed_without_retrying() -> None:
    events: list[str] = []
    current = observation(0)
    device = FakeDevice([current], events, fail_execute=True)
    brain = ScriptedBrain(
        [ProposedAction("click", {"coordinate": [0.5, 0.5]}, current.id)], events
    )

    result = TaskRunner(
        device, brain, AndroidActionCompiler(), RecordingVerifier(events), max_steps=3
    ).run("perform one action")

    assert result.state is TaskState.FAILED
    assert "injected device failure" in result.detail
    assert len(device.executed) == 1
    assert events == ["health", "observe", "decide", "execute:tap"]


def test_runner_rejects_an_unhealthy_device_before_any_agent_or_device_action() -> None:
    events: list[str] = []
    current = observation(0)
    device = FakeDevice([current], events, healthy=False)
    brain = ScriptedBrain(
        [ProposedAction("click", {"coordinate": [0.5, 0.5]}, current.id)], events
    )

    result = TaskRunner(
        device, brain, AndroidActionCompiler(), RecordingVerifier(events), max_steps=1
    ).run("do not use an offline device")

    assert result.state is TaskState.FAILED
    assert result.detail == "device health check failed"
    assert result.last_observation is None
    assert device.executed == []
    assert events == ["health"]
