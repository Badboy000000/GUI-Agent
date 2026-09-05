# Copyright (c) 2026, 东篱馆主

"""Autonomous general-purpose Android task runner using the MAI-UI brain.

The module deliberately owns only task composition.  It does not create
another inference service or widen the Android command surface: a task-local
policy sits in front of ``TaskRunner`` and admits only the declared set of
actions the compiler can truly execute on the device.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from gui_agent.actions import AndroidActionCompiler
from gui_agent.audit import JsonlAuditRecorder, load_replay
from gui_agent.brains import MAIUIBrainAdapter
from gui_agent.contracts import Observation, ProposedAction, TaskState
from gui_agent.evaluation.environment import load_mai_ui_environment
from gui_agent.orchestration import TaskRunner
from gui_agent.platforms.android import (
    AdbTransport,
    AndroidDeviceBackend,
    AndroidDeviceProfile,
    discover_android_device_profile,
)
from gui_agent.platforms.base import DeviceBackend


class AutonomousTaskPolicyError(RuntimeError):
    """Raised when the model proposes an action outside the general policy."""


class _Predictor(Protocol):
    def predict(self, instruction: str, obs: dict[str, Any], **kwargs: Any) -> tuple[str, dict[str, Any]]: ...


class GeneralActionPolicy:
    """Apply the general task policy to every model proposal before validation.

    The allowlist contains exactly the actions ``AndroidActionCompiler`` can
    execute plus the runner's meta-actions.  ``double_click`` and ``answer``
    pass structural validation but have no compile branch, so they are
    rejected here, fail-closed.  The error names only the action name: typed
    text or coordinates must never leak into errors or reports.
    """

    ALLOWED_ACTIONS = frozenset(
        {
            "click",
            "long_press",
            "drag",
            "swipe",
            "type",
            "open",
            "system_button",
            "wait",
            "terminate",
            "ask_user",
        }
    )

    def __init__(self, brain: MAIUIBrainAdapter) -> None:
        self._brain = brain

    def decide(self, instruction: str, observation: Observation) -> ProposedAction:
        proposal = self._brain.decide(instruction, observation)
        if proposal.name not in self.ALLOWED_ACTIONS:
            raise AutonomousTaskPolicyError(
                f"general task policy rejected action {proposal.name!r}"
            )
        return proposal


class AcceptingVerifier:
    """Trust the model's terminate claim; the audit trail carries the evidence."""

    def verify(self, instruction: str, observation: Observation) -> bool:
        return True


class ForegroundPackageVerifier:
    """Verify a terminal model claim against the observed foreground package."""

    def __init__(self, expected_package: str) -> None:
        self._expected_package = expected_package

    def verify(self, instruction: str, observation: Observation) -> bool:
        return observation.foreground_app == self._expected_package


@dataclass(frozen=True, slots=True)
class AndroidTaskConfig:
    """Inputs for one autonomous, single-instruction Android task run.

    ``llm_base_url`` and ``model_name`` may stay empty at construction because
    the CLI fills them from the dotenv environment; ``run_android_task`` fails
    fast when they are still blank.  ``runtime_conf`` defaults to the pixel
    coordinate convention used by the production Zhipu BigModel endpoint.
    """

    serial: str
    instruction: str
    artifact_directory: Path | str
    llm_base_url: str = ""
    model_name: str = ""
    api_key: str = "empty"
    adb_path: str = "adb"
    adb_timeout_seconds: float = 15.0
    max_steps: int = 15
    wait_seconds: float = 1.0
    runtime_conf: Mapping[str, Any] = field(default_factory=lambda: {"coordinate_scale": "pixels"})
    app_packages: Mapping[str, str] = field(default_factory=dict)
    expected_foreground_package: str | None = None
    run_id: str | None = None

    def __post_init__(self) -> None:
        if not self.serial.strip():
            raise ValueError("serial must not be empty")
        if not self.instruction.strip():
            raise ValueError("instruction must not be empty")
        if self.max_steps <= 0:
            raise ValueError("max_steps must be positive")
        if self.adb_timeout_seconds <= 0:
            raise ValueError("adb_timeout_seconds must be positive")
        if self.expected_foreground_package is not None and not self.expected_foreground_package.strip():
            raise ValueError("expected_foreground_package must be non-empty when provided")
        if self.run_id is not None and not self.run_id.strip():
            raise ValueError("run_id must be non-empty when provided")
        if self.run_id is not None and Path(self.run_id).name != self.run_id:
            raise ValueError("run_id must be a single directory name")
        object.__setattr__(self, "artifact_directory", Path(self.artifact_directory))
        object.__setattr__(self, "runtime_conf", dict(self.runtime_conf))
        object.__setattr__(self, "app_packages", dict(self.app_packages))


BackendFactory = Callable[[AndroidTaskConfig], DeviceBackend]
PredictorFactory = Callable[[AndroidTaskConfig], _Predictor]


def run_android_task(
    config: AndroidTaskConfig,
    *,
    backend_factory: BackendFactory | None = None,
    predictor_factory: PredictorFactory | None = None,
) -> dict[str, object]:
    """Run one autonomous Android task and return a JSON-serializable report.

    The two factory seams are intended for CI.  Production performs a
    read-only ADB preflight, constructs the existing MAI-UI navigation
    predictor, and connects the existing ADB backend.  One run owns one
    backend, one predictor, and one JSONL audit trail.  A paused task
    (``ask_user``) is reported as-is; it is never auto-resolved.
    """

    if not config.llm_base_url.strip():
        raise ValueError("llm_base_url must not be empty when running a task")
    if not config.model_name.strip():
        raise ValueError("model_name must not be empty when running a task")
    run_id = config.run_id or str(uuid4())
    run_directory = config.artifact_directory / run_id
    run_directory.mkdir(parents=True, exist_ok=False)

    device_profile: AndroidDeviceProfile | None = None
    if backend_factory is None:
        # Read-only preflight for the production path only; the injected
        # factory seam must never start an adb process.
        device_profile = discover_android_device_profile(
            AdbTransport(
                config.serial,
                adb_path=config.adb_path,
                timeout_seconds=config.adb_timeout_seconds,
            )
        )
    app_packages = (
        {"Settings": device_profile.settings_package} | dict(config.app_packages)
        if device_profile is not None
        else dict(config.app_packages)
    )

    backend = (
        backend_factory(config) if backend_factory is not None else _create_backend(config, run_directory)
    )
    try:
        predictor = (
            predictor_factory(config) if predictor_factory is not None else _create_predictor(config)
        )
        verifier = (
            ForegroundPackageVerifier(config.expected_foreground_package)
            if config.expected_foreground_package is not None
            else AcceptingVerifier()
        )
        recorder = JsonlAuditRecorder(run_directory / "audit.jsonl")
        runner = TaskRunner(
            backend,
            GeneralActionPolicy(MAIUIBrainAdapter(predictor)),
            AndroidActionCompiler(app_packages=app_packages),
            verifier,
            max_steps=config.max_steps,
            wait_seconds=config.wait_seconds,
            audit_recorder=recorder,
        )
        result = runner.run(config.instruction)
    finally:
        try:
            backend.close()
        except Exception:
            # Backend shutdown cannot revise the recorded result, so it is
            # deliberately not turned into an unrecorded task failure.
            pass

    audit_path = run_directory / "audit.jsonl"
    try:
        load_replay(audit_path)
        audit_jsonl_valid = True
    except Exception:
        audit_jsonl_valid = False

    report: dict[str, object] = {
        "run_id": run_id,
        "run_directory": str(run_directory),
        "instruction": config.instruction,
        "model_name": config.model_name,
        "device": {"serial": config.serial, **_profile_report(device_profile)},
        "state": result.state.value,
        "steps": result.steps,
        "detail": result.detail,
        "pending_confirmation": (
            result.pending_confirmation.text if result.pending_confirmation is not None else None
        ),
        "last_foreground_package": (
            result.last_observation.foreground_app if result.last_observation is not None else None
        ),
        "audit_jsonl_valid": audit_jsonl_valid,
        "audit_path": str(audit_path),
        "success": result.state is TaskState.SUCCEEDED,
    }
    report_path = run_directory / "report.json"
    report["report_path"] = str(report_path)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


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


def _create_backend(config: AndroidTaskConfig, run_directory: Path) -> AndroidDeviceBackend:
    transport = AdbTransport(
        config.serial,
        adb_path=config.adb_path,
        timeout_seconds=config.adb_timeout_seconds,
    )
    return AndroidDeviceBackend(transport, screenshot_directory=run_directory / "screenshots")


def _create_predictor(config: AndroidTaskConfig) -> _Predictor:
    """Load the project's upstream-style MAI-UI source without a package refactor."""

    source_directory = Path(__file__).resolve().parents[2] / "src"
    source_text = str(source_directory)
    if source_text not in sys.path:
        sys.path.insert(0, source_text)
    from mai_naivigation_agent import MAIUINaivigationAgent

    return MAIUINaivigationAgent(
        config.llm_base_url,
        config.model_name,
        runtime_conf=dict(config.runtime_conf),
        api_key=config.api_key,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run one autonomous Android task with the MAI-UI brain on an explicit device"
    )
    parser.add_argument("instruction", help="natural-language task the agent must complete")
    parser.add_argument("--serial", required=True, help="explicit adb serial for the emulator or device")
    parser.add_argument(
        "--env-file",
        type=Path,
        help="dotenv file with MAI_UI_BASE_URL/MAI_UI_MODEL_NAME/BIGMODEL_API_KEY (default: repo-root .env)",
    )
    parser.add_argument(
        "--llm-base-url",
        help="OpenAI-compatible MAI-UI endpoint; defaults to MAI_UI_BASE_URL from .env",
    )
    parser.add_argument(
        "--model-name",
        help="model name accepted by the MAI-UI endpoint; defaults to MAI_UI_MODEL_NAME from .env",
    )
    parser.add_argument(
        "--adb-path",
        default="adb",
        help="ADB executable path; use this when platform-tools is not on PATH",
    )
    parser.add_argument("--max-steps", type=int, default=15)
    parser.add_argument("--artifact-directory", type=Path, default=Path("artifacts/android-tasks"))
    parser.add_argument(
        "--expect-package",
        help="optional package that must be foreground when the model terminates with success",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    environment = load_mai_ui_environment(args.env_file)
    config = AndroidTaskConfig(
        serial=args.serial,
        instruction=args.instruction,
        artifact_directory=args.artifact_directory,
        llm_base_url=args.llm_base_url or environment.base_url,
        model_name=args.model_name or environment.model_name,
        api_key=environment.api_key,
        adb_path=args.adb_path,
        max_steps=args.max_steps,
        expected_foreground_package=args.expect_package,
    )
    # Report endpoint/model only; the API key stays in the process and is never printed.
    print(
        f"MAI-UI endpoint: {config.llm_base_url} model={config.model_name} device={config.serial}",
        file=sys.stderr,
    )
    report = run_android_task(config)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
