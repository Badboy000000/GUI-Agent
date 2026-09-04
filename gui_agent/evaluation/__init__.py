# Copyright (c) 2026, 东篱馆主

"""Android end-to-end evaluation entry points."""

from typing import Any

__all__ = ["AndroidEvaluationConfig", "run_android_evaluation"]


def __getattr__(name: str) -> Any:
    """Load the runtime lazily so ``python -m gui_agent.evaluation.android`` is clean."""

    if name in __all__:
        from .android import AndroidEvaluationConfig, run_android_evaluation

        return {
            "AndroidEvaluationConfig": AndroidEvaluationConfig,
            "run_android_evaluation": run_android_evaluation,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
