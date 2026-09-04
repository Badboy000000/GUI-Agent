# Copyright (c) 2026, 东篱馆主

"""Focused compatibility coverage for the P3 MAI-UI source bridge."""

from gui_agent.evaluation.android import _load_existing_mai_ui


def test_compatibility_loader_imports_the_existing_navigation_agent() -> None:
    navigation_agent, _ = _load_existing_mai_ui()

    assert navigation_agent.__name__ == "MAIUINaivigationAgent"
