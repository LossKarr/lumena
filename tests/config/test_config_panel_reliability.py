from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from web.routes import config as config_mod


class _Request:
    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body


def _schema() -> dict[str, dict]:
    return {entry["key"]: entry for entry in config_mod._CONFIG_SCHEMA}


def test_decimal_fields_accept_documented_values():
    schema = _schema()
    normalized, errors = config_mod._validate_config_updates(
        {"LUMENA_DESKTOP_ZOOM": "0.90", "LUMENA_JUDGE_THRESHOLD": "6.5"},
        schema,
    )
    assert errors == []
    assert normalized == {
        "LUMENA_DESKTOP_ZOOM": "0.90",
        "LUMENA_JUDGE_THRESHOLD": "6.5",
    }


def test_integer_decimal_non_finite_and_out_of_range_are_rejected():
    schema = _schema()
    _, fractional_errors = config_mod._validate_config_updates(
        {"LUMENA_BROWSER_MAX_TABS": "2.5"}, schema,
    )
    _, infinite_errors = config_mod._validate_config_updates(
        {"LUMENA_DESKTOP_ZOOM": "NaN"}, schema,
    )
    _, range_errors = config_mod._validate_config_updates(
        {"LUMENA_BROWSER_MAX_TABS": "21"}, schema,
    )
    assert "entière" in fractional_errors[0]
    assert "non numérique" in infinite_errors[0]
    assert "hors limites" in range_errors[0]


def test_react_and_codeagent_iteration_budgets_have_no_ui_or_api_ceiling():
    schema = _schema()
    react = schema["LUMENA_MAX_REACT_ITERATIONS"]
    codeagent = schema["LUMENA_CODE_AGENT_MAX_ITER"]
    assert "max" not in react
    assert "max" not in codeagent

    normalized, errors = config_mod._validate_config_updates(
        {
            "LUMENA_MAX_REACT_ITERATIONS": "10000",
            "LUMENA_CODE_AGENT_MAX_ITER": "10000",
        },
        schema,
    )
    assert errors == []
    assert normalized == {
        "LUMENA_MAX_REACT_ITERATIONS": "10000",
        "LUMENA_CODE_AGENT_MAX_ITER": "10000",
    }


def test_one_sided_iteration_minimum_returns_a_validation_error_not_keyerror():
    schema = _schema()
    normalized, errors = config_mod._validate_config_updates(
        {"LUMENA_MAX_REACT_ITERATIONS": "0"}, schema,
    )
    assert normalized == {}
    assert errors == [
        "LUMENA_MAX_REACT_ITERATIONS: valeur hors limites (minimum 1)"
    ]


def test_select_and_boolean_values_are_validated_and_normalized():
    schema = _schema()
    normalized, errors = config_mod._validate_config_updates(
        {"LUMENA_BROWSER_HEADLESS": "false", "LUMENA_UPDATE_CHANNEL": "nightly"},
        schema,
    )
    assert normalized["LUMENA_BROWSER_HEADLESS"] == "0"
    assert any("option invalide" in error for error in errors)


@pytest.mark.asyncio
async def test_update_validates_only_explicit_changes_not_legacy_values():
    written = {}
    current = {
        "LUMENA_BROWSER_MAX_TABS": "100",
        "LUMENA_BROWSER_HEADLESS": "0",
    }
    with patch.object(config_mod, "_read_env_file", return_value=current), \
         patch.object(config_mod, "_write_env_values", side_effect=lambda values: written.update(values)), \
         patch.dict(os.environ, {}, clear=False):
        result = await config_mod.update_config(
            _Request({"updates": {"LUMENA_BROWSER_HEADLESS": "1"}}),
        )

    assert result["success"] is True
    assert written == {"LUMENA_BROWSER_HEADLESS": "1"}
    assert "LUMENA_BROWSER_MAX_TABS" not in written


@pytest.mark.asyncio
async def test_unknown_key_is_rejected_without_writing():
    with patch.object(config_mod, "_write_env_values") as writer:
        result = await config_mod.update_config(
            _Request({"updates": {"LUMENA_NOT_A_REAL_SETTING": "1"}}),
        )
    assert result["success"] is False
    assert "inconnue" in result["error"]
    writer.assert_not_called()


@pytest.mark.asyncio
async def test_default_model_change_truthfully_requires_restart():
    written = {}
    with patch.object(config_mod, "_read_env_file", return_value={}), \
         patch.object(config_mod, "_write_env_values", side_effect=lambda values: written.update(values)), \
         patch.dict(os.environ, {}, clear=False):
        result = await config_mod.update_config(
            _Request({"updates": {"LUMENA_DEFAULT_MODEL": "deepseek-flash"}}),
        )

    assert result["success"] is True
    assert result["needs_restart"] is True
    assert written == {"LUMENA_DEFAULT_MODEL": "deepseek-flash"}
