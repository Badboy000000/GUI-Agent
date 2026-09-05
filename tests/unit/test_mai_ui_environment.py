# Copyright (c) 2026, 东篱馆主

from pathlib import Path

import pytest

from gui_agent.evaluation.environment import EvaluationEnvironmentError, load_mai_ui_environment


def test_loads_the_real_mai_ui_connection_from_dotenv(tmp_path: Path) -> None:
    environment_file = tmp_path / ".env"
    environment_file.write_text(
        "MAI_UI_BASE_URL=https://open.bigmodel.cn/api/paas/v4\n"
        "MAI_UI_MODEL_NAME=glm-5.3-flash\n"
        "BIGMODEL_API_KEY=test-secret\n",
        encoding="utf-8",
    )

    connection = load_mai_ui_environment(environment_file, environ={})

    assert connection.base_url == "https://open.bigmodel.cn/api/paas/v4"
    assert connection.model_name == "glm-5.3-flash"
    assert connection.api_key == "test-secret"


def test_rejects_a_missing_required_mai_ui_value_without_exposing_secrets(tmp_path: Path) -> None:
    environment_file = tmp_path / ".env"
    environment_file.write_text("MAI_UI_BASE_URL=https://example.invalid/v1\n", encoding="utf-8")

    with pytest.raises(EvaluationEnvironmentError, match="MAI_UI_MODEL_NAME"):
        load_mai_ui_environment(environment_file, environ={})
