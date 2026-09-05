# Copyright (c) 2026, 东篱馆主

"""Focused validation coverage for the autonomous Android task runtime."""

from pathlib import Path

import pytest

from gui_agent.autonomous.android import AndroidTaskConfig, _build_parser, _create_predictor


def _config_kwargs(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "serial": "emulator-5554",
        "instruction": "Open Settings.",
        "artifact_directory": Path("artifacts/android-tasks"),
    }
    values.update(overrides)
    return values


def test_config_rejects_a_blank_serial() -> None:
    with pytest.raises(ValueError):
        AndroidTaskConfig(**_config_kwargs(serial="  "))


def test_config_rejects_a_blank_instruction() -> None:
    with pytest.raises(ValueError):
        AndroidTaskConfig(**_config_kwargs(instruction=""))


def test_config_rejects_a_non_positive_max_steps() -> None:
    with pytest.raises(ValueError):
        AndroidTaskConfig(**_config_kwargs(max_steps=0))


def test_config_rejects_a_run_id_with_path_separators() -> None:
    with pytest.raises(ValueError):
        AndroidTaskConfig(**_config_kwargs(run_id="nested/run"))


def test_config_defaults_to_the_pixel_coordinate_scale() -> None:
    config = AndroidTaskConfig(**_config_kwargs())

    assert config.runtime_conf == {"coordinate_scale": "pixels"}
    assert config.app_packages == {}
    assert config.expected_foreground_package is None
    assert config.run_id is None


def test_parser_parses_the_positional_instruction_and_serial() -> None:
    args = _build_parser().parse_args(["Open Settings", "--serial", "emulator-5554"])

    assert args.instruction == "Open Settings"
    assert args.serial == "emulator-5554"


def test_parser_defaults() -> None:
    args = _build_parser().parse_args(["Open Settings", "--serial", "emulator-5554"])

    assert args.max_steps == 15
    assert args.artifact_directory == Path("artifacts/android-tasks")
    assert args.adb_path == "adb"
    assert args.expect_package is None


def test_create_predictor_forwards_the_default_pixel_coordinate_scale() -> None:
    # Construction is offline-safe: the OpenAI client connects lazily.
    agent = _create_predictor(
        AndroidTaskConfig(
            **_config_kwargs(llm_base_url="http://offline.invalid/v1", model_name="scripted-mai")
        )
    )

    assert agent.coordinate_scale == "pixels"


def test_create_predictor_respects_a_caller_supplied_coordinate_scale() -> None:
    agent = _create_predictor(
        AndroidTaskConfig(
            **_config_kwargs(
                llm_base_url="http://offline.invalid/v1",
                model_name="scripted-mai",
                runtime_conf={"coordinate_scale": "thousand"},
            )
        )
    )

    assert agent.coordinate_scale == "thousand"
