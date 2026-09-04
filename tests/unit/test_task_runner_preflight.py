# Copyright (c) 2026, 东篱馆主

from gui_agent.actions import AndroidActionCompiler
from gui_agent.contracts import Observation, ProposedAction
from gui_agent.orchestration.agent_loop import TaskRunner
from gui_agent.contracts.task import TaskState


class OfflineDevice:
    def health(self) -> bool:
        return False

    def observe(self) -> Observation:
        raise AssertionError("an offline device must not be observed")

    def execute(self, command) -> None:
        raise AssertionError("an offline device must not execute commands")


class NeverCalledBrain:
    def decide(self, instruction: str, observation: Observation) -> ProposedAction:
        raise AssertionError("an offline device must not invoke the brain")


class NeverCalledVerifier:
    def verify(self, instruction: str, observation: Observation) -> bool:
        raise AssertionError("an offline device must not verify success")


def test_task_runner_rejects_an_unhealthy_device_before_observing() -> None:
    result = TaskRunner(
        OfflineDevice(), NeverCalledBrain(), AndroidActionCompiler(), NeverCalledVerifier()
    ).run("open settings")

    assert result.state is TaskState.FAILED
    assert result.steps == 0
    assert result.last_observation is None
