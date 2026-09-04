# Copyright (c) 2026, 东篱馆主

"""Anti-corruption adapter from the upstream MAI-UI agent to ``Brain``."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from PIL import Image

from gui_agent.contracts import Observation, ProposedAction


class MAIUIPredictor(Protocol):
    """The small portion of MAIUINaivigationAgent used by this project."""

    def predict(self, instruction: str, obs: dict[str, Any], **kwargs: Any) -> tuple[str, dict[str, Any]]: ...


class MAIUIBrainError(RuntimeError):
    """Raised when an upstream MAI-UI result cannot become a proposal."""


class MAIUIBrainAdapter:
    """Adapt MAI-UI prediction output without exposing it to orchestration."""

    def __init__(self, predictor: MAIUIPredictor) -> None:
        self._predictor = predictor

    def decide(self, instruction: str, observation: Observation) -> ProposedAction:
        if observation.screenshot_path is None:
            raise MAIUIBrainError("MAI-UI requires an observation screenshot")
        screenshot_path = Path(observation.screenshot_path)
        try:
            with Image.open(screenshot_path) as image:
                screenshot = image.convert("RGB").copy()
        except (OSError, ValueError) as error:
            raise MAIUIBrainError("unable to load observation screenshot") from error

        _, action = self._predictor.predict(
            instruction,
            {"screenshot": screenshot, "accessibility_tree": observation.ui_tree},
        )
        if not isinstance(action, dict) or not isinstance(action.get("action"), str):
            raise MAIUIBrainError("MAI-UI returned an action without a valid name")
        arguments = {key: value for key, value in action.items() if key != "action"}
        return ProposedAction(
            name=action["action"],
            arguments=arguments,
            source_observation_id=observation.id,
        )
