# Copyright (c) 2026, 东篱馆主

"""Minimal, auditable observe-decide-validate-execute task loop."""

from __future__ import annotations

from dataclasses import dataclass
from time import sleep
from typing import Callable, Protocol

from gui_agent.contracts import Observation, ProposedAction, TaskState, validate_action
from gui_agent.orchestration.task_state_machine import TaskStateMachine
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


@dataclass(frozen=True, slots=True)
class TaskResult:
    state: TaskState
    steps: int
    detail: str
    last_observation: Observation | None


class TaskRunner:
    """Own one bounded, side-effect-aware task run.

    This MVP intentionally has no automatic retry: GUI effects such as tapping
    or typing are not safely repeatable after a transport failure.
    """

    def __init__(self, backend: DeviceBackend, brain: Brain, compiler: ActionCompiler, verifier: SuccessVerifier, *, max_steps: int = 20, wait_seconds: float = 1.0, sleeper: Callable[[float], None] = sleep) -> None:
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

    def run(self, instruction: str) -> TaskResult:
        machine = TaskStateMachine()
        machine.transition_to(TaskState.RUNNING)
        last_observation: Observation | None = None
        completed_steps = 0
        try:
            if not self._backend.health():
                machine.transition_to(TaskState.FAILED)
                return TaskResult(machine.state, 0, "device health check failed", None)
            for step in range(self._max_steps):
                completed_steps = step + 1
                last_observation = self._backend.observe()
                proposed = self._brain.decide(instruction, last_observation)
                validated = validate_action(proposed, last_observation)
                if validated.name == "terminate":
                    if validated.arguments["status"] == "success" and self._verifier.verify(instruction, last_observation):
                        machine.transition_to(TaskState.SUCCEEDED)
                        return TaskResult(machine.state, step, "success verified", last_observation)
                    machine.transition_to(TaskState.FAILED)
                    return TaskResult(machine.state, step, "model termination was not verified", last_observation)
                if validated.name == "ask_user":
                    machine.transition_to(TaskState.WAITING_FOR_CONFIRMATION)
                    return TaskResult(machine.state, step, validated.arguments["text"], last_observation)
                if validated.name == "wait":
                    self._sleeper(self._wait_seconds)
                    continue
                command = self._compiler.compile(validated, last_observation)
                self._backend.execute(command)
            machine.transition_to(TaskState.FAILED)
            return TaskResult(machine.state, completed_steps, "step budget exhausted", last_observation)
        except Exception as error:
            machine.transition_to(TaskState.FAILED)
            return TaskResult(machine.state, completed_steps, str(error), last_observation)
