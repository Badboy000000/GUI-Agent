from __future__ import annotations

import pytest

from gui_agent.contracts import (
    ActionValidationError,
    Observation,
    ProposedAction,
    validate_action,
)


def _observation(sequence: int = 1) -> Observation:
    return Observation(
        device_id="emulator-5554",
        sequence=sequence,
        screen_width=1080,
        screen_height=2400,
    )


def test_valid_action_remains_bound_to_the_observation_that_produced_it() -> None:
    observation = _observation()
    proposed = ProposedAction(
        name="click",
        arguments={"coordinate": [0.25, 0.75]},
        source_observation_id=observation.id,
    )

    validated = validate_action(proposed, observation)

    assert validated.name == "click"
    assert validated.source_observation_id == observation.id
    assert validated.validation_id


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("invented_action", {}),
        ("click", {}),
        ("click", {"coordinate": [1.1, 0.5]}),
        ("click", {"coordinate": [float("nan"), 0.5]}),
        ("click", {"coordinate": [float("inf"), 0.5]}),
        ("swipe", {"direction": "diagonal"}),
        ("system_button", {"button": "power"}),
        ("terminate", {"status": "maybe"}),
    ],
)
def test_malformed_or_unknown_actions_are_rejected(
    name: str, arguments: dict[str, object]
) -> None:
    observation = _observation()
    proposed = ProposedAction(name, arguments, observation.id)

    with pytest.raises(ActionValidationError):
        validate_action(proposed, observation)


def test_action_from_a_prior_observation_is_rejected() -> None:
    prior_observation = _observation(1)
    latest_observation = _observation(2)
    proposed = ProposedAction(
        name="wait", arguments={}, source_observation_id=prior_observation.id
    )

    with pytest.raises(ActionValidationError, match="stale action"):
        validate_action(proposed, latest_observation)
