# Copyright (c) 2026, 东篱馆主

from pathlib import Path

from PIL import Image

from gui_agent.brains import MAIUIBrainAdapter
from gui_agent.contracts import Observation


class Predictor:
    def predict(self, instruction, obs):
        assert instruction == "open settings"
        assert obs["screenshot"].size == (10, 20)
        return "raw model output", {"action": "click", "coordinate": [0.5, 0.25]}


def test_adapter_turns_an_upstream_prediction_into_a_bound_proposal(tmp_path: Path) -> None:
    screenshot_path = tmp_path / "screen.png"
    Image.new("RGB", (10, 20)).save(screenshot_path)
    observation = Observation(
        device_id="emulator", sequence=0, screen_width=10, screen_height=20, screenshot_path=str(screenshot_path)
    )

    proposal = MAIUIBrainAdapter(Predictor()).decide("open settings", observation)

    assert proposal.name == "click"
    assert proposal.arguments == {"coordinate": [0.5, 0.25]}
    assert proposal.source_observation_id == observation.id
