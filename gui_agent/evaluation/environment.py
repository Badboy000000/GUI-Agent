# Copyright (c) 2026, 东篱馆主

"""Local MAI-UI connection configuration loaded from a dotenv file.

This is the single seam between the operator's local, git-ignored ``.env``
(real BigModel API key) and the real inference client.  It reads exactly three
values and never prints, logs, or writes the key back.  The process environment
takes precedence over the dotenv file so CI and shells can override it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

BASE_URL_VARIABLE = "MAI_UI_BASE_URL"
MODEL_NAME_VARIABLE = "MAI_UI_MODEL_NAME"
API_KEY_VARIABLE = "BIGMODEL_API_KEY"

_REQUIRED_VARIABLES = (BASE_URL_VARIABLE, MODEL_NAME_VARIABLE, API_KEY_VARIABLE)


class EvaluationEnvironmentError(RuntimeError):
    """Required MAI-UI connection configuration is missing.

    The message names only the missing variable keys; it never embeds values,
    so a secret cannot leak through a raised diagnostic.
    """


@dataclass(frozen=True, slots=True)
class MaiUiEnvironment:
    """The OpenAI-compatible endpoint coordinates for one MAI-UI inference run."""

    base_url: str
    model_name: str
    api_key: str

    def __repr__(self) -> str:
        # Never expose the key in logs, tracebacks, or audit artifacts.
        return (
            "MaiUiEnvironment("
            f"base_url={self.base_url!r}, model_name={self.model_name!r}, "
            f"api_key=<redacted:{len(self.api_key)} chars>)"
        )


def project_environment_path() -> Path:
    """Return the default repository-root ``.env`` path."""

    # gui_agent/evaluation/environment.py -> repository root is three parents up.
    return Path(__file__).resolve().parents[2] / ".env"


def load_mai_ui_environment(
    env_file: Path | str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> MaiUiEnvironment:
    """Load the MAI-UI connection from a dotenv file and the process environment.

    Process variables win over file values.  Missing or blank required values
    raise :class:`EvaluationEnvironmentError` naming only the variable keys.
    """

    path = Path(env_file) if env_file is not None else project_environment_path()
    values: dict[str, str] = {}
    if path.is_file():
        values.update(_parse_dotenv(path))

    process_env = dict(os.environ if environ is None else environ)
    for key in _REQUIRED_VARIABLES:
        if process_env.get(key):
            values[key] = process_env[key]

    missing = [key for key in _REQUIRED_VARIABLES if not values.get(key, "").strip()]
    if missing:
        raise EvaluationEnvironmentError(
            "missing required MAI-UI environment value(s): "
            + ", ".join(missing)
            + f" (set them in {path} or the process environment)"
        )

    return MaiUiEnvironment(
        base_url=values[BASE_URL_VARIABLE].strip(),
        model_name=values[MODEL_NAME_VARIABLE].strip(),
        api_key=values[API_KEY_VARIABLE].strip(),
    )


def _parse_dotenv(path: Path) -> dict[str, str]:
    """Parse a minimal ``KEY=VALUE`` dotenv file without third-party dependencies.

    Comments (``#``) and blank lines are ignored.  Surrounding quotes on a value
    are stripped.  Lines without ``=`` are skipped rather than raising, matching
    dotenv tolerance.
    """

    parsed: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            continue
        value = value.strip().strip('"').strip("'")
        parsed[key] = value
    return parsed
