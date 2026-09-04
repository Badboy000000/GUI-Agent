# Copyright (c) 2026, 东篱馆主

"""Opt-in Android P3 evaluation using the existing MAI-UI predictor.

The module deliberately owns only evaluation composition.  It does not create
another inference service or widen the Android command surface: a task-local
policy sits in front of ``TaskRunner`` and rejects every model proposal other
than the small, declared action set for the selected scenario.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from time import monotonic
from typing import Any, Protocol
from uuid import uuid4

from gui_agent.actions import AndroidActionCompiler
from gui_agent.audit import AuditEventKind, JsonlAuditRecorder, load_replay
from gui_agent.brains import MAIUIBrainAdapter
from gui_agent.contracts import Observation, ProposedAction, TaskState
from gui_agent.orchestration import TaskRunner
from gui_agent.platforms.android import (
    AdbTransport,
    AndroidDeviceBackend,
    AndroidDeviceProfile,
    UiStabilityWaiter,
    discover_android_device_profile,
)
from gui_agent.platforms.base import DeviceBackend


class EvaluationPolicyError(RuntimeError):
    """Raised before ``TaskRunner`` can execute an out-of-profile model action."""


class _Predictor(Protocol):
    def predict(self, instruction: str, obs: dict[str, Any], **kwargs: Any) -> tuple[str, dict[str, Any]]: ...


@dataclass(frozen=True, slots=True)
class AndroidEvaluationConfig:
    """Inputs for one explicitly selected Android P3 evaluation suite."""

    serial: str
    llm_base_url: str
    model_name: str
    artifact_directory: Path | str
    home_package: str | None = None
    settings_package: str = "com.android.settings"
    max_steps: int = 4
    adb_path: str = "adb"
    adb_timeout_seconds: float = 15.0
    stability_required_consecutive: int = 2
    stability_poll_interval_seconds: float = 0.25
    stability_timeout_seconds: float = 5.0
    runtime_conf: Mapping[str, Any] = field(default_factory=dict)
    run_id: str | None = None
    baseline_report_path: Path | str | None = None

    def __post_init__(self) -> None:
        if not self.serial.strip():
            raise ValueError("serial must not be empty")
        if not self.llm_base_url.strip():
            raise ValueError("llm_base_url must not be empty")
        if not self.model_name.strip():
            raise ValueError("model_name must not be empty")
        if not self.settings_package.strip():
            raise ValueError("settings_package must not be empty")
        if self.home_package is not None and not self.home_package.strip():
            raise ValueError("home_package must be non-empty when provided")
        if self.max_steps <= 0:
            raise ValueError("max_steps must be positive")
        if self.run_id is not None and not self.run_id.strip():
            raise ValueError("run_id must be non-empty when provided")
        if self.run_id is not None and Path(self.run_id).name != self.run_id:
            raise ValueError("run_id must be a single directory name")
        object.__setattr__(self, "artifact_directory", Path(self.artifact_directory))
        object.__setattr__(self, "runtime_conf", dict(self.runtime_conf))
        if self.baseline_report_path is not None:
            object.__setattr__(self, "baseline_report_path", Path(self.baseline_report_path))


@dataclass(frozen=True, slots=True)
class _EvaluationTask:
    name: str
    instruction: str

    def allows(self, proposal: ProposedAction) -> bool:
        """Return whether one untrusted MAI-UI action is safe for this task."""

        if self.name == "settings":
            return (
                proposal.name == "open"
                and dict(proposal.arguments) == {"text": "Settings"}
            ) or _is_success_termination(proposal)
        if self.name == "home":
            return (
                proposal.name == "system_button"
                and dict(proposal.arguments) == {"button": "home"}
            ) or _is_success_termination(proposal)
        if self.name == "wait":
            return (proposal.name == "wait" and not proposal.arguments) or _is_success_termination(
                proposal
            )
        if self.name == "takeover":
            return (
                proposal.name == "ask_user"
                and set(proposal.arguments) == {"text"}
                and isinstance(proposal.arguments["text"], str)
                and bool(proposal.arguments["text"].strip())
            )
        return False


def _is_success_termination(proposal: ProposedAction) -> bool:
    return proposal.name == "terminate" and dict(proposal.arguments) == {"status": "success"}


class _RestrictedBrain:
    """Apply a task's policy to every action, including TaskRunner meta-actions."""

    def __init__(self, brain: MAIUIBrainAdapter, task: _EvaluationTask) -> None:
        self._brain = brain
        self._task = task

    def decide(self, instruction: str, observation: Observation) -> ProposedAction:
        proposal = self._brain.decide(instruction, observation)
        if not self._task.allows(proposal):
            raise EvaluationPolicyError(
                f"P3 policy rejected {self._task.name} action {proposal.name!r}"
            )
        return proposal


class _ForegroundVerifier:
    """Verify a terminal model claim against the foreground package in its observation."""

    def __init__(self, expected_package: str | None) -> None:
        self._expected_package = expected_package

    def verify(self, instruction: str, observation: Observation) -> bool:
        return self._expected_package is not None and observation.foreground_app == self._expected_package


class _AcceptingVerifier:
    """Accept termination; scenario-specific audit checks add the remaining evidence."""

    def verify(self, instruction: str, observation: Observation) -> bool:
        return True


BackendFactory = Callable[[AndroidEvaluationConfig, _EvaluationTask], DeviceBackend]
PredictorFactory = Callable[[AndroidEvaluationConfig, _EvaluationTask], _Predictor]


def run_android_evaluation(
    config: AndroidEvaluationConfig,
    *,
    backend_factory: BackendFactory | None = None,
    predictor_factory: PredictorFactory | None = None,
) -> dict[str, object]:
    """Run the four Android P3 scenarios and return only JSON-serializable results.

    The two factory seams are intended for CI.  Production constructs the
    existing MAI-UI navigation predictor and the existing ADB backend directly.
    Each task receives fresh collaborators and an independent JSONL trail.
    """

    backend_builder = backend_factory or _create_android_backend
    predictor_builder = predictor_factory or _create_mai_ui_predictor
    run_id = config.run_id or str(uuid4())
    run_directory = config.artifact_directory / run_id
    run_directory.mkdir(parents=True, exist_ok=False)
    run_config = replace(config, artifact_directory=run_directory, run_id=run_id)
    effective_config, device_profile = _resolve_device_profile(run_config, backend_factory)
    task_reports: list[dict[str, object]] = []

    for task in _evaluation_tasks():
        task_reports.append(
            _run_one_task(
                effective_config,
                task,
                backend_factory=backend_builder,
                predictor_factory=predictor_builder,
            )
        )

    state_counts = Counter(str(report["state"]) for report in task_reports)
    passed_count = sum(bool(report["scenario_passed"]) for report in task_reports)
    report = {
        "run_id": run_id,
        "run_directory": str(run_directory),
        "device": {
            "serial": effective_config.serial,
            "settings_package": effective_config.settings_package,
            "home_package": effective_config.home_package,
            **_profile_report(device_profile),
        },
        "model_name": config.model_name,
        "tasks": task_reports,
        "aggregate": {
            "total": len(task_reports),
            "scenario_passed": passed_count,
            "success_rate": passed_count / len(task_reports) if task_reports else 0.0,
            "state_counts": dict(sorted(state_counts.items())),
            "stability_timeouts": sum(bool(report["timed_out"]) for report in task_reports),
            "human_takeovers": sum(bool(report["human_takeover"]) for report in task_reports),
            "audit_jsonl_valid": sum(bool(report["audit_jsonl_valid"]) for report in task_reports),
        },
    }
    if config.baseline_report_path is not None:
        report["comparison"] = _compare_with_baseline(config.baseline_report_path, report)
    report_path = run_directory / "report.json"
    report["report_path"] = str(report_path)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def _resolve_device_profile(
    config: AndroidEvaluationConfig, backend_factory: BackendFactory | None
) -> tuple[AndroidEvaluationConfig, AndroidDeviceProfile | None]:
    """Collect read-only device facts only for the production ADB path.

    CI injects a backend factory, so its declared Settings/Home packages remain
    sufficient and no platform process is started.  Production resolves package
    handlers from the selected device before the first task input, which also
    records OEM-specific facts for the emulator-versus-Redmi comparison.
    """

    if backend_factory is not None:
        return config, None
    transport = AdbTransport(
        config.serial,
        adb_path=config.adb_path,
        timeout_seconds=config.adb_timeout_seconds,
    )
    profile = discover_android_device_profile(transport)
    if config.home_package is not None and config.home_package != profile.home_package:
        raise ValueError(
            "configured home_package does not match the Android preflight result: "
            f"expected {config.home_package!r}, observed {profile.home_package!r}"
        )
    return (
        replace(
            config,
            settings_package=profile.settings_package,
            home_package=profile.home_package,
        ),
        profile,
    )


def _profile_report(profile: AndroidDeviceProfile | None) -> dict[str, object]:
    if profile is None:
        return {}
    return {
        "manufacturer": profile.manufacturer,
        "model": profile.model,
        "android_release": profile.android_release,
        "screen_width": profile.screen_width,
        "screen_height": profile.screen_height,
        "foreground_package_before_run": profile.foreground_package,
    }


def _evaluation_tasks() -> tuple[_EvaluationTask, ...]:
    return (
        _EvaluationTask(
            "settings",
            "Open the Settings app using open Settings. Do not change any setting. "
            "When Settings is visible, terminate with success.",
        ),
        _EvaluationTask(
            "home",
            "Return to the Android Home screen using system_button home. Do not interact "
            "with the launcher. When Home is visible, terminate with success.",
        ),
        _EvaluationTask(
            "wait",
            "Wait for the current Android screen to become stable. Do not interact with it. "
            "After the stable wait completes, terminate with success.",
        ),
        _EvaluationTask(
            "takeover",
            "Do not operate the device. Ask the user to take over the device now.",
        ),
    )


def _run_one_task(
    config: AndroidEvaluationConfig,
    task: _EvaluationTask,
    *,
    backend_factory: BackendFactory,
    predictor_factory: PredictorFactory,
) -> dict[str, object]:
    task_directory = config.artifact_directory / task.name
    audit_path = task_directory / "audit.jsonl"
    backend = backend_factory(config, task)
    predictor = predictor_factory(config, task)
    recorder = JsonlAuditRecorder(audit_path)
    verifier = _verifier_for(config, task)
    runner = TaskRunner(
        backend,
        _RestrictedBrain(MAIUIBrainAdapter(predictor), task),
        AndroidActionCompiler(app_packages={"Settings": config.settings_package}),
        verifier,
        max_steps=config.max_steps,
        audit_recorder=recorder,
        stability_waiter=(
            UiStabilityWaiter(
                backend,
                required_consecutive=config.stability_required_consecutive,
                poll_interval_seconds=config.stability_poll_interval_seconds,
                timeout_seconds=config.stability_timeout_seconds,
            ).wait
            if task.name == "wait"
            else None
        ),
    )

    started_at = monotonic()
    initial_state: TaskState | None = None
    result = runner.run(task.instruction)
    if task.name == "takeover":
        initial_state = result.state
        if result.state is TaskState.WAITING_FOR_CONFIRMATION:
            result = runner.take_over("P3 evaluation operator takeover")
    duration_seconds = monotonic() - started_at

    replay, audit_error = _load_audit(audit_path)
    kinds = {event.kind for event in replay.events} if replay is not None else set()
    timed_out = AuditEventKind.STABILITY_WAIT_TIMEOUT in kinds
    human_takeover = AuditEventKind.HUMAN_TAKEOVER in kinds
    audit_valid = replay is not None
    scenario_passed = _scenario_passed(
        task,
        result.state,
        initial_state,
        kinds,
        audit_valid,
    )
    failure_reason = None if scenario_passed else _failure_reason(result.detail, audit_error)

    try:
        backend.close()
    except Exception:
        # Backend shutdown cannot revise the recorded result, so it is deliberately
        # not turned into an unrecorded task failure.
        pass

    return {
        "name": task.name,
        "state": result.state.value,
        "scenario_passed": scenario_passed,
        "audit_path": str(audit_path),
        "audit_jsonl_valid": audit_valid,
        "failure_reason": failure_reason,
        "failure_classification": None if scenario_passed else _failure_classification(
            result.detail, audit_error, timed_out
        ),
        "duration_seconds": duration_seconds,
        "steps": result.steps,
        "last_foreground_package": (
            result.last_observation.foreground_app if result.last_observation is not None else None
        ),
        "timed_out": timed_out,
        "human_takeover": human_takeover,
    }


def _verifier_for(config: AndroidEvaluationConfig, task: _EvaluationTask):
    if task.name == "settings":
        return _ForegroundVerifier(config.settings_package)
    if task.name == "home":
        return _ForegroundVerifier(config.home_package)
    return _AcceptingVerifier()


def _load_audit(path: Path):
    try:
        return load_replay(path), None
    except Exception as error:
        return None, f"{type(error).__name__}: {error}"


def _scenario_passed(
    task: _EvaluationTask,
    state: TaskState,
    initial_state: TaskState | None,
    kinds: set[AuditEventKind],
    audit_valid: bool,
) -> bool:
    if not audit_valid:
        return False
    if task.name == "takeover":
        return (
            initial_state is TaskState.WAITING_FOR_CONFIRMATION
            and state is TaskState.CANCELLED
            and {
                AuditEventKind.CONFIRMATION_REQUESTED,
                AuditEventKind.HUMAN_TAKEOVER,
                AuditEventKind.TASK_FINISHED,
            }.issubset(kinds)
        )
    if state is not TaskState.SUCCEEDED:
        return False
    if task.name == "wait":
        return (
            AuditEventKind.STABILITY_WAIT_COMPLETED in kinds
            and AuditEventKind.STABILITY_WAIT_TIMEOUT not in kinds
        )
    return AuditEventKind.TASK_FINISHED in kinds


def _failure_reason(detail: str, audit_error: str | None) -> str:
    return audit_error or detail


def _compare_with_baseline(path: Path, report: Mapping[str, object]) -> dict[str, object]:
    """Summarize observable Android differences from a previous suite report."""

    try:
        baseline = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(baseline, dict):
            raise ValueError("baseline report must contain a JSON object")
        baseline_device = _mapping_value(baseline, "device")
        current_device = _mapping_value(report, "device")
        baseline_aggregate = _mapping_value(baseline, "aggregate")
        current_aggregate = _mapping_value(report, "aggregate")
        return {
            "available": True,
            "baseline_report_path": str(path),
            "device_differences": _field_differences(
                baseline_device,
                current_device,
                (
                    "manufacturer",
                    "model",
                    "android_release",
                    "screen_width",
                    "screen_height",
                    "settings_package",
                    "home_package",
                    "foreground_package_before_run",
                ),
            ),
            "outcome_differences": _field_differences(
                baseline_aggregate,
                current_aggregate,
                ("success_rate", "stability_timeouts", "human_takeovers", "audit_jsonl_valid"),
            ),
            "task_differences": _task_differences(baseline, report),
        }
    except (OSError, ValueError, json.JSONDecodeError, TypeError) as error:
        return {
            "available": False,
            "baseline_report_path": str(path),
            "error": f"{type(error).__name__}: {error}",
        }


def _mapping_value(source: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = source.get(key)
    if not isinstance(value, Mapping):
        raise ValueError(f"report field {key!r} must be an object")
    return value


def _field_differences(
    baseline: Mapping[str, object], current: Mapping[str, object], fields: tuple[str, ...]
) -> list[dict[str, object]]:
    return [
        {"field": field, "baseline": baseline.get(field), "current": current.get(field)}
        for field in fields
        if baseline.get(field) != current.get(field)
    ]


def _task_differences(
    baseline: Mapping[str, object], current: Mapping[str, object]
) -> list[dict[str, object]]:
    baseline_tasks = _tasks_by_name(baseline)
    current_tasks = _tasks_by_name(current)
    differences: list[dict[str, object]] = []
    for name in sorted(set(baseline_tasks) | set(current_tasks)):
        baseline_task = baseline_tasks.get(name)
        current_task = current_tasks.get(name)
        if baseline_task is None or current_task is None:
            differences.append(
                {"task": name, "baseline": baseline_task, "current": current_task}
            )
            continue
        changes = _field_differences(
            baseline_task,
            current_task,
            (
                "state",
                "scenario_passed",
                "failure_classification",
                "timed_out",
                "human_takeover",
                "last_foreground_package",
            ),
        )
        if changes:
            differences.append({"task": name, "differences": changes})
    return differences


def _tasks_by_name(report: Mapping[str, object]) -> dict[str, Mapping[str, object]]:
    tasks = report.get("tasks")
    if not isinstance(tasks, list):
        raise ValueError("report field 'tasks' must be an array")
    result: dict[str, Mapping[str, object]] = {}
    for task in tasks:
        if not isinstance(task, Mapping) or not isinstance(task.get("name"), str):
            raise ValueError("each report task must contain a string name")
        result[task["name"]] = task
    return result


def _failure_classification(detail: str, audit_error: str | None, timed_out: bool) -> str:
    if audit_error is not None:
        return "audit_replay_error"
    if timed_out:
        return "stability_timeout"
    if "P3 policy rejected" in detail:
        return "policy_violation"
    if "step budget exhausted" in detail:
        return "step_budget_exhausted"
    if "MAI-UI" in detail or "llm client error" in detail:
        return "model_error"
    if "device" in detail.lower():
        return "device_error"
    return "scenario_not_satisfied"


def _create_android_backend(
    config: AndroidEvaluationConfig, task: _EvaluationTask
) -> AndroidDeviceBackend:
    transport = AdbTransport(
        config.serial,
        adb_path=config.adb_path,
        timeout_seconds=config.adb_timeout_seconds,
    )
    return AndroidDeviceBackend(
        transport,
        screenshot_directory=config.artifact_directory / task.name / "screenshots",
    )


def _create_mai_ui_predictor(
    config: AndroidEvaluationConfig, task: _EvaluationTask
) -> _Predictor:
    navigation_agent, ask_user_prompt = _load_existing_mai_ui()
    if task.name == "takeover":

        class AskUserEnabledNavigationAgent(navigation_agent):
            @property
            def system_prompt(self) -> str:
                return ask_user_prompt.render(tools="")

        return AskUserEnabledNavigationAgent(
            config.llm_base_url,
            config.model_name,
            runtime_conf=dict(config.runtime_conf),
        )
    return navigation_agent(
        config.llm_base_url,
        config.model_name,
        runtime_conf=dict(config.runtime_conf),
    )


def _load_existing_mai_ui():
    """Load the project's upstream-style MAI-UI source without a package refactor."""

    source_directory = Path(__file__).resolve().parents[2] / "src"
    source_text = str(source_directory)
    if source_text not in sys.path:
        sys.path.insert(0, source_text)
    from mai_naivigation_agent import MAIUINaivigationAgent
    from prompt import MAI_MOBILE_SYS_PROMPT_ASK_USER_MCP

    return MAIUINaivigationAgent, MAI_MOBILE_SYS_PROMPT_ASK_USER_MCP


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Android P3 MAI-UI evaluation on one explicit device")
    parser.add_argument("--serial", required=True, help="explicit adb serial for the emulator or device")
    parser.add_argument("--llm-base-url", required=True, help="existing OpenAI-compatible MAI-UI endpoint")
    parser.add_argument("--model-name", required=True, help="model name accepted by the MAI-UI endpoint")
    parser.add_argument("--artifact-directory", type=Path, default=Path("artifacts/android-p3"))
    parser.add_argument(
        "--home-package",
        help="optional launcher package override; the production preflight resolves it when omitted",
    )
    parser.add_argument("--settings-package", default="com.android.settings")
    parser.add_argument("--max-steps", type=int, default=4)
    parser.add_argument(
        "--baseline-report",
        type=Path,
        help="optional previous report.json to compare with this device run",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    config = AndroidEvaluationConfig(
        serial=args.serial,
        llm_base_url=args.llm_base_url,
        model_name=args.model_name,
        artifact_directory=args.artifact_directory,
        home_package=args.home_package,
        settings_package=args.settings_package,
        max_steps=args.max_steps,
        baseline_report_path=args.baseline_report,
    )
    print(json.dumps(run_android_evaluation(config), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
