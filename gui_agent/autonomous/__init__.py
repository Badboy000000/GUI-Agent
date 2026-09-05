# Copyright (c) 2026, 东篱馆主

"""Autonomous general-purpose Android task entry points."""

from typing import Any

__all__ = ["AndroidTaskConfig", "run_android_task"]


def __getattr__(name: str) -> Any:
    """Load the runtime lazily so ``python -m gui_agent.autonomous.android`` is clean."""

    if name in __all__:
        from .android import AndroidTaskConfig, run_android_task

        return {
            "AndroidTaskConfig": AndroidTaskConfig,
            "run_android_task": run_android_task,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
