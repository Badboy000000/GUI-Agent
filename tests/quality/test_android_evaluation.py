# Copyright (c) 2026, 东篱馆主

"""Public, offline acceptance coverage for the Android P3 evaluator.

These tests deliberately use the same ``run_android_evaluation`` entry point
as the production CLI, but inject a PNG-backed Android backend and a scripted
MAI predictor.  They do not start ADB, contact a model service, or assert the
private implementation order of the task runner.
"""

from __future__ import annotations

from collections.abc import Iterable
from io import BytesIO
import json
from pathlib import Path
from typing import Any

from PIL import Image

from gui_agent.audit import AuditEventKind, load_replay
from gui_agent.contracts import Observation, PlatformCommand
from gui_agent.evaluation import AndroidEvaluationConfig, run_android_evaluation
from gui_agent.platforms.base import DeviceBackend


_HOME_PACKAGE = "com.example.launcher"
_SETTINGS_PACKAGE = "com.android.settings"


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


class EvaluationFakes:
    """Fresh per-task factories for the public evaluator dependency seams."""

    def __init__(
        self,
        tmp_path: Path,
        *,
        actions_by_task: dict[str, list[dict[str, object]]] | None = None,
        wait_foregrounds: tuple[str, ...] = (_HOME_PACKAGE,) * 4,
    ) -> None:
        self._tmp_path = tmp_path
        self._actions_by_task = actions_by_task or _default_actions()
        self._wait_foregrounds = wait_foregrounds
        self.backends: dict[str, PngScriptedBackend] = {}
        self.predictors: dict[str, ScriptedMAIPredictor] = {}

    def backend_factory(self, config: AndroidEvaluationConfig, task: object) -> PngScriptedBackend:
        name = _task_name(task)
        backend = PngScriptedBackend(config.serial, self._observations_for(name))
        self.backends[name] = backend
        return backend

    def predictor_factory(self, config: AndroidEvaluationConfig, task: object) -> ScriptedMAIPredictor:
        name = _task_name(task)
        predictor = ScriptedMAIPredictor(self._actions_by_task[name])
        self.predictors[name] = predictor
        return predictor

    def _observations_for(self, task_name: str) -> list[Observation]:
        if task_name == "settings":
            return [
                self._observation(task_name, 0, _HOME_PACKAGE, b"home"),
                self._observation(task_name, 1, _SETTINGS_PACKAGE, b"settings"),
            ]
        if task_name == "home":
            return [
                self._observation(task_name, 0, _SETTINGS_PACKAGE, b"settings"),
                self._observation(task_name, 1, _HOME_PACKAGE, b"home"),
            ]
        if task_name == "wait":
            # The middle pair is fingerprint-identical, satisfying the real
            # screenshot-based stability waiter before the terminal decision.
            return [
                self._observation(task_name, 0, _HOME_PACKAGE, b"before-wait"),
                self._observation(task_name, 1, self._wait_foregrounds[0], b"stable"),
                self._observation(task_name, 2, self._wait_foregrounds[1], b"stable"),
                self._observation(task_name, 3, self._wait_foregrounds[2], b"after-wait"),
                self._observation(task_name, 4, self._wait_foregrounds[3], b"after-wait"),
            ]
        if task_name == "takeover":
            return [self._observation(task_name, 0, _HOME_PACKAGE, b"takeover")]
        raise AssertionError(f"unexpected P3 task: {task_name}")

    def _observation(
        self, task_name: str, sequence: int, foreground_app: str, png_payload: bytes
    ) -> Observation:
        path = self._tmp_path / "fake-screens" / task_name / f"{sequence:06d}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        # Persist real PNG bytes; the adapter opens these through Pillow and the
        # stability waiter hashes the exact same files.
        path.write_bytes(_png_bytes(png_payload))
        return Observation(
            device_id="offline-android",
            sequence=sequence,
            screen_width=1080,
            screen_height=2400,
            screenshot_path=str(path),
            foreground_app=foreground_app,
        )


def _task_name(task: object) -> str:
    name = getattr(task, "name", None)
    assert isinstance(name, str)
    return name


def _png_bytes(payload: bytes) -> bytes:
    buffer = BytesIO()
    # Different payloads create visibly distinct images, while equal payloads
    # yield byte-identical artifacts for the stability fingerprint.
    color = (payload[0] if payload else 0, len(payload) % 256, 127)
    Image.new("RGB", (12, 24), color).save(buffer, format="PNG")
    return buffer.getvalue()


def _default_actions() -> dict[str, list[dict[str, object]]]:
    return {
        "settings": [
            {"action": "open", "text": "Settings"},
            {"action": "terminate", "status": "success"},
        ],
        "home": [
            {"action": "system_button", "button": "home"},
            {"action": "terminate", "status": "success"},
        ],
        "wait": [
            {"action": "wait"},
            {"action": "terminate", "status": "success"},
        ],
        "takeover": [{"action": "ask_user", "text": "Please take over this device."}],
    }


def _config(tmp_path: Path, **overrides: object) -> AndroidEvaluationConfig:
    values: dict[str, object] = {
        "serial": "offline-android",
        "llm_base_url": "http://offline.invalid/v1",
        "model_name": "scripted-mai",
        "artifact_directory": tmp_path / "evaluation-artifacts",
        "home_package": _HOME_PACKAGE,
    }
    values.update(overrides)
    return AndroidEvaluationConfig(**values)


def _run(
    tmp_path: Path,
    *,
    fakes: EvaluationFakes | None = None,
    config_overrides: dict[str, object] | None = None,
) -> tuple[dict[str, object], EvaluationFakes]:
    active_fakes = fakes or EvaluationFakes(tmp_path)
    report = run_android_evaluation(
        _config(tmp_path, **(config_overrides or {})),
        backend_factory=active_fakes.backend_factory,
        predictor_factory=active_fakes.predictor_factory,
    )
    return report, active_fakes


def _reports_by_name(report: dict[str, object]) -> dict[str, dict[str, object]]:
    tasks = report["tasks"]
    assert isinstance(tasks, list)
    result: dict[str, dict[str, object]] = {}
    for task in tasks:
        assert isinstance(task, dict)
        name = task.get("name")
        assert isinstance(name, str)
        result[name] = task
    return result


def _aggregate(report: dict[str, object]) -> dict[str, object]:
    aggregate = report["aggregate"]
    assert isinstance(aggregate, dict)
    return aggregate


def _replay_kinds(task_report: dict[str, object]) -> set[AuditEventKind]:
    path = task_report["audit_path"]
    assert isinstance(path, str)
    return {event.kind for event in load_replay(path).events}


def test_evaluation_reports_all_safe_scenarios_and_expected_outcome_rate(tmp_path: Path) -> None:
    report, fakes = _run(tmp_path)
    task_reports = _reports_by_name(report)
    aggregate = _aggregate(report)

    assert json.loads(json.dumps(report)) == report
    assert set(task_reports) == {"settings", "home", "wait", "takeover"}
    assert all(item["scenario_passed"] is True for item in task_reports.values())
    assert {name: item["state"] for name, item in task_reports.items()} == {
        "settings": "succeeded",
        "home": "succeeded",
        "wait": "succeeded",
        "takeover": "cancelled",
    }
    assert aggregate["total"] == 4
    assert aggregate["scenario_passed"] == 4
    assert aggregate["success_rate"] == 1.0
    assert aggregate["state_counts"] == {"succeeded": 3, "cancelled": 1}
    assert aggregate["stability_timeouts"] == 0
    assert aggregate["human_takeovers"] == 1
    assert aggregate["audit_jsonl_valid"] == 4
    assert all(fakes.predictors[name].received_screenshot_sizes for name in task_reports)


def test_stable_wait_reports_replay_evidence_without_device_input(tmp_path: Path) -> None:
    report, fakes = _run(tmp_path)
    wait_report = _reports_by_name(report)["wait"]

    assert wait_report["scenario_passed"] is True
    assert wait_report["timed_out"] is False
    assert wait_report["audit_jsonl_valid"] is True
    assert AuditEventKind.STABILITY_WAIT_COMPLETED in _replay_kinds(wait_report)
    assert AuditEventKind.STABILITY_WAIT_TIMEOUT not in _replay_kinds(wait_report)
    assert fakes.backends["wait"].executed == []


def test_takeover_cancels_after_pause_without_device_input_and_replays_it(tmp_path: Path) -> None:
    report, fakes = _run(tmp_path)
    takeover_report = _reports_by_name(report)["takeover"]

    assert takeover_report["state"] == "cancelled"
    assert takeover_report["scenario_passed"] is True
    assert takeover_report["human_takeover"] is True
    assert takeover_report["audit_jsonl_valid"] is True
    assert {
        AuditEventKind.CONFIRMATION_REQUESTED,
        AuditEventKind.HUMAN_TAKEOVER,
        AuditEventKind.TASK_FINISHED,
    } <= _replay_kinds(takeover_report)
    assert fakes.backends["takeover"].executed == []


def test_policy_violation_fails_before_input_and_other_scenarios_are_reported(tmp_path: Path) -> None:
    actions = _default_actions()
    actions["wait"] = [{"action": "click", "coordinate": [0.5, 0.5]}]
    report, fakes = _run(tmp_path, fakes=EvaluationFakes(tmp_path, actions_by_task=actions))
    task_reports = _reports_by_name(report)
    wait_report = task_reports["wait"]

    assert wait_report["state"] == "failed"
    assert wait_report["scenario_passed"] is False
    assert wait_report["failure_classification"] == "policy_violation"
    assert isinstance(wait_report["failure_reason"], str)
    assert wait_report["audit_jsonl_valid"] is True
    assert fakes.backends["wait"].executed == []
    assert task_reports["settings"]["scenario_passed"] is True
    assert task_reports["home"]["scenario_passed"] is True
    assert task_reports["takeover"]["scenario_passed"] is True
    assert _aggregate(report)["total"] == 4


def test_stability_timeout_is_reported_and_its_audit_remains_replayable(tmp_path: Path) -> None:
    # Distinct foreground packages prevent the stability waiter from observing
    # consecutive matching snapshots.  The configured tiny bounded window
    # exercises the evaluator's timeout classification without an ADB device.
    fakes = EvaluationFakes(
        tmp_path,
        wait_foregrounds=("com.example.one", "com.example.two", "com.example.three", "com.example.four"),
    )
    report, _ = _run(
        tmp_path,
        fakes=fakes,
        config_overrides={
            "stability_poll_interval_seconds": 0.000001,
            "stability_timeout_seconds": 0.000001,
        },
    )
    wait_report = _reports_by_name(report)["wait"]

    assert wait_report["state"] == "failed"
    assert wait_report["scenario_passed"] is False
    assert wait_report["timed_out"] is True
    assert wait_report["failure_classification"] == "stability_timeout"
    assert wait_report["audit_jsonl_valid"] is True
    assert AuditEventKind.STABILITY_WAIT_TIMEOUT in _replay_kinds(wait_report)
    assert _aggregate(report)["stability_timeouts"] == 1
    assert _aggregate(report)["audit_jsonl_valid"] == 4


def test_evaluation_compares_the_second_device_report_with_a_baseline(tmp_path: Path) -> None:
    emulator_report, _ = _run(tmp_path, config_overrides={"run_id": "emulator"})
    baseline_path = emulator_report["report_path"]
    assert isinstance(baseline_path, str)
    baseline = json.loads(Path(baseline_path).read_text(encoding="utf-8"))
    baseline["device"]["home_package"] = "com.example.emulator.launcher"
    Path(baseline_path).write_text(json.dumps(baseline), encoding="utf-8")

    redmi_report, _ = _run(
        tmp_path,
        config_overrides={
            "run_id": "redmi",
            "baseline_report_path": baseline_path,
        },
    )
    comparison = redmi_report["comparison"]
    assert isinstance(comparison, dict)
    assert comparison["available"] is True
    assert {
        "field": "home_package",
        "baseline": "com.example.emulator.launcher",
        "current": _HOME_PACKAGE,
    } in comparison["device_differences"]
