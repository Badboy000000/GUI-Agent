import pytest

from gui_agent.actions import ActionCompilationError, AndroidActionCompiler
from gui_agent.contracts import Observation, ProposedAction, validate_action


def _observation() -> Observation:
    return Observation(device_id="emulator-5554", sequence=0, screen_width=100, screen_height=200)


def _validated(name: str, arguments: dict):
    observation = _observation()
    action = ProposedAction(name=name, arguments=arguments, source_observation_id=observation.id)
    return observation, validate_action(action, observation)


def test_compiler_converts_normalized_click_to_viewport_pixel_command() -> None:
    observation, action = _validated("click", {"coordinate": [0.5, 1.0]})

    command = AndroidActionCompiler().compile(action, observation)

    assert command.name == "tap"
    assert command.arguments == {"x": 50, "y": 199}


def test_compiler_only_launches_allowlisted_apps() -> None:
    observation, action = _validated("open", {"text": "Settings"})

    command = AndroidActionCompiler({"Settings": "com.android.settings"}).compile(action, observation)

    assert command.name == "launch"
    assert command.arguments == {"package_name": "com.android.settings"}

    with pytest.raises(ActionCompilationError, match="allowlisted"):
        AndroidActionCompiler().compile(action, observation)
