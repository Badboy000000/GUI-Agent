# Copyright (c) 2026, 东篱馆主

"""Public, offline acceptance coverage for the autonomous Android task runner.

These tests deliberately use the same ``run_android_task`` entry point as the
production CLI, but inject a PNG-backed Android backend and a scripted MAI
predictor.  They do not start ADB, contact a model service, or assert the
private implementation order of the task runner.
"""

from __future__ import annotations

from collections.abc import Iterable
from io import BytesIO
import json
from pathlib import Path
from typing import Any

from PIL import Image

from gui_agent.audit import load_replay
from gui_agent.autonomous import AndroidTaskConfig, run_android_task
from gui_agent.contracts import Observation, PlatformCommand
from gui_agent.platforms.base import DeviceBackend


_HOME_PACKAGE = "com.example.launcher"
_SETTINGS_PACKAGE = "com.android.settings"
_SCREEN_WIDTH = 1080
_SCREEN_HEIGHT = 2400

_HAPPY_PATH_ACTIONS: list[dict[str, object]] = [
    {"action": "open", "text": "Settings"},
    {"action": "click", "coordinate": [0.5, 0.5]},
    {"action": "type", "text": "hello"},
    {"action": "terminate", "status": "success"},
]


class PngScriptedBackend(DeviceBackend):
    """A connected-looking device whose observations are persisted PNG files."""

    def __init__(self, device_id: str, observations: Iterable[Observation]) -> None:
        self._device_id = device_id
        self._observations = iter(observations)
        self.executed: list[PlatformCommand] = []
        self.closed = False

    @property
    def device_id(self) -> str:
        return self._device_id

    def health(self) -> bool:
        return not self.closed

    def observe(self) -> Observation:
        return next(self._observations)

    def execute(self, command: PlatformCommand) -> None:
        self.executed.append(command)

    def close(self) -> None:
        self.closed = True


class ScriptedMAIPredictor:
    """The upstream MAI predictor shape consumed by ``MAIUIBrainAdapter``."""

    def __init__(self, actions: Iterable[dict[str, object]]) -> None:
        self._actions = iter(actions)
        self.received_screenshot_sizes: list[tuple[int, int]] = []

    def predict(self, instruction: str, obs: dict[str, Any]) -> tuple[str, dict[str, object]]:
        screenshot = obs["screenshot"]
        assert isinstance(screenshot, Image.Image)
        self.received_screenshot_sizes.append(screenshot.size)
        return f"scripted MAI response for {instruction}", next(self._actions)


class TaskFakes:
    """Fresh factories for the autonomous task dependency seams."""

    def __init__(
        self,
        tmp_path: Path,
        *,
        actions: list[dict[str, object]],
        foregrounds: list[str],
    ) -> None:
        self._tmp_path = tmp_path
        self._actions = actions
        self._foregrounds = foregrounds
        self.backend: PngScriptedBackend | None = None
        self.predictor: ScriptedMAIPredictor | None = None

    def backend_factory(self, config: AndroidTaskConfig) -> PngScriptedBackend:
        observations = [
            self._observation(sequence, foreground)
            for sequence, foreground in enumerate(self._foregrounds)
        ]
        self.backend = PngScriptedBackend(config.serial, observations)
        return self.backend

    def predictor_factory(self, config: AndroidTaskConfig) -> ScriptedMAIPredictor:
        self.predictor = ScriptedMAIPredictor(self._actions)
        return self.predictor

    def _observation(self, sequence: int, foreground_app: str) -> Observation:
        path = self._tmp_path / "fake-screens" / f"{sequence:06d}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        # Persist real PNG bytes; the brain adapter opens these through Pillow.
        path.write_bytes(_png_bytes(sequence))
        return Observation(
            device_id="offline-android",
            sequence=sequence,
            screen_width=_SCREEN_WIDTH,
            screen_height=_SCREEN_HEIGHT,
            screenshot_path=str(path),
            foreground_app=foreground_app,
        )


def _png_bytes(seed: int) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (12, 24), (seed % 256, 63, 127)).save(buffer, format="PNG")
    return buffer.getvalue()


def _config(tmp_path: Path, **overrides: object) -> AndroidTaskConfig:
    values: dict[str, object] = {
        "serial": "offline-android",
        "instruction": "Open Settings, tap the center of the screen, type hello, then finish.",
        "artifact_directory": tmp_path / "task-artifacts",
        "llm_base_url": "http://offline.invalid/v1",
        "model_name": "scripted-mai",
        "app_packages": {"Settings": _SETTINGS_PACKAGE},
    }
    values.update(overrides)
    return AndroidTaskConfig(**values)


def _run(
    tmp_path: Path,
    *,
    actions: list[dict[str, object]],
    foregrounds: list[str] | None = None,
    config_overrides: dict[str, object] | None = None,
) -> tuple[dict[str, object], TaskFakes]:
    # One observation per loop iteration, plus slack so a runaway loop fails on
    # the step budget instead of exhausting the scripted observation iterator.
    resolved_foregrounds = foregrounds or [_HOME_PACKAGE] * (len(actions) + 1)
    fakes = TaskFakes(tmp_path, actions=actions, foregrounds=resolved_foregrounds)
    report = run_android_task(
        _config(tmp_path, **(config_overrides or {})),
        backend_factory=fakes.backend_factory,
        predictor_factory=fakes.predictor_factory,
    )
    return report, fakes


def _executed_backend(fakes: TaskFakes) -> PngScriptedBackend:
    backend = fakes.backend
    assert backend is not None
    return backend


def test_autonomous_task_executes_the_scripted_multistep_task(tmp_path: Path) -> None:
    foregrounds = [_HOME_PACKAGE, _SETTINGS_PACKAGE, _SETTINGS_PACKAGE, _SETTINGS_PACKAGE]
    report, fakes = _run(tmp_path, actions=_HAPPY_PATH_ACTIONS, foregrounds=foregrounds)

    assert report["state"] == "succeeded"
    assert report["success"] is True
    assert report["audit_jsonl_valid"] is True
    assert report["last_foreground_package"] == _SETTINGS_PACKAGE

    executed = _executed_backend(fakes).executed
    assert [command.name for command in executed] == ["launch", "tap", "text"]
    assert executed[0].arguments == {"package_name": _SETTINGS_PACKAGE}
    # The compiler converts normalized [0,1] coordinates with round(v*(dim-1)).
    expected_x = round(0.5 * (_SCREEN_WIDTH - 1))
    expected_y = round(0.5 * (_SCREEN_HEIGHT - 1))
    assert (expected_x, expected_y) == (540, 1200)
    assert executed[1].arguments == {"x": expected_x, "y": expected_y}
    assert executed[2].arguments == {"text": "hello"}
    assert _executed_backend(fakes).closed is True


def test_policy_rejects_double_click_before_any_device_input(tmp_path: Path) -> None:
    report, fakes = _run(
        tmp_path,
        actions=[{"action": "double_click", "coordinate": [0.5, 0.5]}],
    )

    assert report["state"] == "failed"
    assert report["success"] is False
    detail = report["detail"]
    assert isinstance(detail, str)
    assert "double_click" in detail
    # The policy error names only the action name, never argument values.
    assert "0.5" not in detail
    assert _executed_backend(fakes).executed == []


def test_policy_rejects_answer_before_any_device_input(tmp_path: Path) -> None:
    report, fakes = _run(
        tmp_path,
        actions=[{"action": "answer", "text": "sensitive-typed-text"}],
    )

    assert report["state"] == "failed"
    assert report["success"] is False
    detail = report["detail"]
    assert isinstance(detail, str)
    assert "answer" in detail
    # Typed text must not leak into the failure detail or the report.
    assert "sensitive-typed-text" not in detail
    assert _executed_backend(fakes).executed == []


def test_policy_rejects_an_unknown_action_before_any_device_input(tmp_path: Path) -> None:
    report, fakes = _run(tmp_path, actions=[{"action": "screenshot"}])

    assert report["state"] == "failed"
    assert report["success"] is False
    detail = report["detail"]
    assert isinstance(detail, str)
    assert "screenshot" in detail
    assert _executed_backend(fakes).executed == []


def test_out_of_range_coordinate_fails_closed_before_any_device_input(tmp_path: Path) -> None:
    report, fakes = _run(
        tmp_path,
        actions=[{"action": "click", "coordinate": [1.5, 0.5]}],
    )

    assert report["state"] == "failed"
    assert report["success"] is False
    detail = report["detail"]
    assert isinstance(detail, str)
    assert "coordinate" in detail
    assert _executed_backend(fakes).executed == []


def test_open_of_a_non_allowlisted_app_fails_before_any_device_input(tmp_path: Path) -> None:
    report, fakes = _run(tmp_path, actions=[{"action": "open", "text": "WeChat"}])

    assert report["state"] == "failed"
    assert report["success"] is False
    detail = report["detail"]
    assert isinstance(detail, str)
    assert "WeChat" in detail
    assert _executed_backend(fakes).executed == []


def test_report_structure_and_persisted_artifacts(tmp_path: Path) -> None:
    foregrounds = [_HOME_PACKAGE, _SETTINGS_PACKAGE, _SETTINGS_PACKAGE, _SETTINGS_PACKAGE]
    report, fakes = _run(
        tmp_path,
        actions=_HAPPY_PATH_ACTIONS,
        foregrounds=foregrounds,
        config_overrides={"run_id": "structure-check"},
    )

    assert json.loads(json.dumps(report)) == report
    assert report["run_id"] == "structure-check"
    run_directory = Path(str(report["run_directory"]))
    assert run_directory.name == "structure-check"
    report_path = Path(str(report["report_path"]))
    assert report_path.is_file()
    assert json.loads(report_path.read_text(encoding="utf-8")) == report
    audit_path = Path(str(report["audit_path"]))
    assert audit_path.is_file()
    assert audit_path.name == "audit.jsonl"
    assert report["audit_jsonl_valid"] is True
    assert load_replay(audit_path).events
    # TaskRunner records one step per executed device command; the terminal
    # terminate decision itself is not a recorded step.
    assert report["steps"] == len(_HAPPY_PATH_ACTIONS) - 1
    assert _executed_backend(fakes).closed is True


def test_expected_foreground_package_blocks_an_unverified_success(tmp_path: Path) -> None:
    report, fakes = _run(
        tmp_path,
        actions=[{"action": "terminate", "status": "success"}],
        foregrounds=[_HOME_PACKAGE],
        config_overrides={"expected_foreground_package": _SETTINGS_PACKAGE},
    )

    assert report["state"] == "failed"
    assert report["success"] is False
    detail = report["detail"]
    assert isinstance(detail, str)
    assert "not verified" in detail
    assert _executed_backend(fakes).executed == []


def test_expected_foreground_package_accepts_a_matching_success(tmp_path: Path) -> None:
    report, _ = _run(
        tmp_path,
        actions=[{"action": "terminate", "status": "success"}],
        foregrounds=[_SETTINGS_PACKAGE],
        config_overrides={"expected_foreground_package": _SETTINGS_PACKAGE},
    )

    assert report["state"] == "succeeded"
    assert report["success"] is True
    assert report["last_foreground_package"] == _SETTINGS_PACKAGE


def test_ask_user_pauses_the_run_and_reports_the_prompt_without_device_input(
    tmp_path: Path,
) -> None:
    prompt = "Please unlock the device so I can continue."
    report, fakes = _run(tmp_path, actions=[{"action": "ask_user", "text": prompt}])

    # The pause is reported as-is; the run is never auto-resolved.
    assert report["state"] == "waiting_for_confirmation"
    assert report["success"] is False
    assert report["pending_confirmation"] == prompt
    assert report["audit_jsonl_valid"] is True
    assert _executed_backend(fakes).executed == []
