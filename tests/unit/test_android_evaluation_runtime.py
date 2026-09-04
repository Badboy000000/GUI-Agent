# Copyright (c) 2026, 东篱馆主

"""Focused compatibility coverage for the P3 MAI-UI source bridge."""

from gui_agent.evaluation.android import _build_parser, _load_existing_mai_ui


def test_compatibility_loader_imports_the_existing_navigation_agent() -> None:
    navigation_agent, _ = _load_existing_mai_ui()

    assert navigation_agent.__name__ == "MAIUINaivigationAgent"


def test_cli_accepts_an_explicit_adb_path() -> None:
    args = _build_parser().parse_args(
        [
            "--serial",
            "emulator-5554",
            "--llm-base-url",
            "http://model.example/v1",
            "--model-name",
            "mai-ui",
            "--adb-path",
            "C:/Android/platform-tools/adb.exe",
        ]
    )

    assert args.adb_path == "C:/Android/platform-tools/adb.exe"
