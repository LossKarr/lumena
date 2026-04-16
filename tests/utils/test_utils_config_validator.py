"""Tests unitaires pour src/utils/config_validator.py"""
import os
import sys
import pytest
from pathlib import Path
from unittest.mock import patch

from src.utils.config_validator import (
    ValidationResult,
    ConfigValidationReport,
    ConfigValidator,
    validate_config,
    validate_and_log,
)


# ─── ValidationResult ──────────────────────────────────────────────────────

class TestValidationResult:
    def test_create_ok(self):
        r = ValidationResult(valid=True, category="python", message="OK", severity="info")
        assert r.valid is True
        assert r.category == "python"
        assert r.severity == "info"

    def test_create_error(self):
        r = ValidationResult(valid=False, category="paths", message="missing", severity="error")
        assert r.valid is False
        assert r.severity == "error"


# ─── ConfigValidationReport ────────────────────────────────────────────────

class TestConfigValidationReport:
    def _make_report(self, items):
        report = ConfigValidationReport()
        for v, sev in items:
            report.add(ValidationResult(valid=v, category="test", message="m", severity=sev))
        return report

    def test_empty_report_is_valid(self):
        report = ConfigValidationReport()
        assert report.is_valid is True
        assert report.total_checks == 0
        assert report.error_count == 0
        assert report.warning_count == 0

    def test_error_count(self):
        report = self._make_report([(True, "info"), (False, "error"), (False, "error")])
        assert report.error_count == 2

    def test_warning_count(self):
        report = self._make_report([(True, "warning"), (True, "info"), (True, "warning")])
        assert report.warning_count == 2

    def test_is_valid_false_when_errors(self):
        report = self._make_report([(False, "error")])
        assert report.is_valid is False

    def test_is_valid_true_with_warnings(self):
        report = self._make_report([(True, "warning"), (True, "warning")])
        assert report.is_valid is True

    def test_total_checks(self):
        report = self._make_report([(True, "info")] * 5)
        assert report.total_checks == 5


# ─── ConfigValidator._validate_python ──────────────────────────────────────

class TestConfigValidatorPython:
    def test_current_python_passes(self):
        report = ConfigValidationReport()
        validator = ConfigValidator()
        validator._validate_python(report)
        # Python 3.10+ → should pass (we're running on 3.10+)
        assert report.total_checks == 1
        assert report.results[0].category == "python"

    def test_old_python_fails(self):
        from collections import namedtuple
        report = ConfigValidationReport()
        validator = ConfigValidator()
        VersionInfo = namedtuple('version_info', ['major', 'minor', 'micro', 'releaselevel', 'serial'])
        old_version = VersionInfo(3, 8, 0, 'final', 0)
        with patch.object(sys, "version_info", old_version):
            validator._validate_python(report)
        # severity = error
        assert report.results[0].severity == "error"


# ─── ConfigValidator._validate_env_vars ────────────────────────────────────

class TestConfigValidatorEnvVars:
    def test_default_model_resolved(self):
        report = ConfigValidationReport()
        validator = ConfigValidator()
        with patch.dict(os.environ, {}, clear=False):
            validator._validate_env_vars(report)
        model_result = next(r for r in report.results if "modele" in r.message.lower() or "Modele" in r.message)
        assert model_result.valid is True

    def test_custom_model_env(self):
        with patch.dict(os.environ, {"LUMENA_DEFAULT_MODEL": "my-custom-model"}):
            result = ConfigValidator._resolve_default_model_env()
        assert result == "my-custom-model"

    def test_fallback_model_chain(self):
        with patch.dict(os.environ, {}, clear=True):
            # Patch all lookups to return empty
            with patch.dict(os.environ, {
                "LUMENA_DEFAULT_MODEL": "",
                "DEFAULT_MODEL": "",
                "LUMENA_MODEL": "",
            }):
                result = ConfigValidator._resolve_default_model_env()
        assert result == "qwen3-8b"

    def test_invalid_react_timeout(self):
        report = ConfigValidationReport()
        validator = ConfigValidator()
        with patch.dict(os.environ, {"LUMENA_REACT_TIMEOUT": "not_a_number"}):
            validator._validate_env_vars(report)
        timeout_errors = [r for r in report.results if "REACT_TIMEOUT" in r.message]
        assert any(r.severity == "warning" for r in timeout_errors)

    def test_low_react_timeout_is_warning(self):
        report = ConfigValidationReport()
        validator = ConfigValidator()
        with patch.dict(os.environ, {"LUMENA_REACT_TIMEOUT": "10"}):
            validator._validate_env_vars(report)
        low_warnings = [r for r in report.results if "trop bas" in r.message]
        assert len(low_warnings) == 1

    def test_valid_react_timeout(self):
        report = ConfigValidationReport()
        validator = ConfigValidator()
        with patch.dict(os.environ, {"LUMENA_REACT_TIMEOUT": "600"}):
            validator._validate_env_vars(report)
        timeout_ok = [r for r in report.results if "timeout" in r.message.lower()]
        assert all(r.severity == "info" for r in timeout_ok)

    def test_invalid_openai_key_format_warned(self):
        report = ConfigValidationReport()
        validator = ConfigValidator()
        with patch.dict(os.environ, {"OPENAI_API_KEY": "invalid-key"}):
            validator._validate_env_vars(report)
        warnings = [r for r in report.results if "OPENAI" in r.message]
        assert len(warnings) == 1 and warnings[0].severity == "warning"

    def test_valid_openai_key_no_warning(self):
        report = ConfigValidationReport()
        validator = ConfigValidator()
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-abcdefghij1234567890"}):
            validator._validate_env_vars(report)
        warnings = [r for r in report.results if "OPENAI" in r.message]
        assert len(warnings) == 0


# ─── validate_config factory ───────────────────────────────────────────────

class TestValidateConfig:
    def test_returns_report(self, tmp_path):
        report = validate_config(data_dir=tmp_path)
        assert isinstance(report, ConfigValidationReport)
        assert report.total_checks > 0

    def test_valid_on_typical_setup(self, tmp_path):
        report = validate_config(data_dir=tmp_path)
        # No errors expected on a valid Python 3.10+ setup with writable tmp_path
        assert report.error_count == 0


# ─── validate_and_log ──────────────────────────────────────────────────────

class TestValidateAndLog:
    def test_returns_bool(self, tmp_path):
        result = validate_and_log(data_dir=tmp_path)
        assert isinstance(result, bool)
        assert result is True  # should pass on valid setup
