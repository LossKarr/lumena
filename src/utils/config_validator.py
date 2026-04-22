"""
Configuration validation for Lumena startup.

This module is intentionally defensive: validation reports warnings for
non-critical issues and returns False only on critical errors.
"""

from __future__ import annotations

import importlib
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from loguru import logger


@dataclass
class ValidationResult:
    valid: bool
    category: str
    message: str
    severity: str = "info"  # info | warning | error


@dataclass
class ConfigValidationReport:
    results: List[ValidationResult] = field(default_factory=list)

    def add(self, result: ValidationResult) -> None:
        self.results.append(result)

    @property
    def total_checks(self) -> int:
        return len(self.results)

    @property
    def warning_count(self) -> int:
        return sum(1 for r in self.results if r.severity == "warning")

    @property
    def error_count(self) -> int:
        return sum(1 for r in self.results if r.severity == "error")

    @property
    def is_valid(self) -> bool:
        return self.error_count == 0


class ConfigValidator:
    def __init__(self, data_dir: Optional[Path] = None) -> None:
        root = Path(__file__).resolve().parents[2]
        self.root = root
        from .paths import DATA_DIR
        self.data_dir = (data_dir or DATA_DIR).resolve()

    def validate_all(self) -> ConfigValidationReport:
        report = ConfigValidationReport()
        self._validate_python(report)
        self._validate_paths(report)
        self._validate_env_vars(report)
        self._validate_optional_dependencies(report)
        return report

    def _validate_python(self, report: ConfigValidationReport) -> None:
        version = sys.version_info
        if (version.major, version.minor) < (3, 10):
            report.add(
                ValidationResult(
                    valid=False,
                    category="python",
                    message=f"Python {version.major}.{version.minor} detecte, minimum requis: 3.10",
                    severity="error",
                )
            )
            return

        report.add(
            ValidationResult(
                valid=True,
                category="python",
                message=f"Python {version.major}.{version.minor} OK",
                severity="info",
            )
        )

    def _validate_paths(self, report: ConfigValidationReport) -> None:
        if not self.root.exists():
            report.add(
                ValidationResult(
                    valid=False,
                    category="paths",
                    message=f"Racine projet introuvable: {self.root}",
                    severity="error",
                )
            )
            return

        if not self.data_dir.exists():
            report.add(
                ValidationResult(
                    valid=True,
                    category="paths",
                    message=f"Data dir absent, sera cree: {self.data_dir}",
                    severity="warning",
                )
            )
        else:
            report.add(
                ValidationResult(
                    valid=True,
                    category="paths",
                    message=f"Data dir OK: {self.data_dir}",
                    severity="info",
                )
            )

    @staticmethod
    def _resolve_default_model_env() -> str:
        return (
            os.getenv("LUMENA_DEFAULT_MODEL")
            or os.getenv("DEFAULT_MODEL")
            or os.getenv("LUMENA_MODEL")
            or "qwen3-8b"
        )

    def _validate_env_vars(self, report: ConfigValidationReport) -> None:
        model = self._resolve_default_model_env()
        report.add(
            ValidationResult(
                valid=True,
                category="env",
                message=f"Modele configure: {model}",
                severity="info",
            )
        )

        react_timeout = os.getenv("LUMENA_REACT_TIMEOUT", "900")
        try:
            timeout_val = int(react_timeout)
            if timeout_val < 30:
                report.add(
                    ValidationResult(
                        valid=False,
                        category="env",
                        message=f"LUMENA_REACT_TIMEOUT trop bas ({timeout_val}s), minimum 30s",
                        severity="warning",
                    )
                )
            else:
                report.add(
                    ValidationResult(
                        valid=True,
                        category="env",
                        message=f"React timeout: {timeout_val}s",
                        severity="info",
                    )
                )
        except ValueError:
            report.add(
                ValidationResult(
                    valid=False,
                    category="env",
                    message=f"LUMENA_REACT_TIMEOUT invalide: {react_timeout}",
                    severity="warning",
                )
            )

        openai_key = os.getenv("OPENAI_API_KEY", "").strip()
        if openai_key and not re.match(r"^sk-[A-Za-z0-9_\-]{16,}$", openai_key):
            report.add(
                ValidationResult(
                    valid=True,
                    category="api_keys",
                    message="OPENAI_API_KEY format potentiellement invalide",
                    severity="warning",
                )
            )

        anthropic_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
        if anthropic_key and not re.match(r"^sk-ant-[A-Za-z0-9_\-]{16,}$", anthropic_key):
            report.add(
                ValidationResult(
                    valid=True,
                    category="api_keys",
                    message="ANTHROPIC_API_KEY format potentiellement invalide",
                    severity="warning",
                )
            )

    def _validate_optional_dependencies(self, report: ConfigValidationReport) -> None:
        optional = {
            "discord": ["discord"],
        }
        for feature, modules in optional.items():
            missing = []
            for module in modules:
                try:
                    importlib.import_module(module)
                except Exception:
                    missing.append(module)  # module non installé

            if missing:
                report.add(
                    ValidationResult(
                        valid=True,
                        category="dependencies",
                        message=f"Feature '{feature}' desactivee (manque: {', '.join(missing)})",
                        severity="info",
                    )
                )
            else:
                report.add(
                    ValidationResult(
                        valid=True,
                        category="dependencies",
                        message=f"Feature '{feature}' disponible",
                        severity="info",
                    )
                )


def validate_config(data_dir: Optional[Path] = None) -> ConfigValidationReport:
    validator = ConfigValidator(data_dir)
    return validator.validate_all()


def validate_and_log(data_dir: Optional[Path] = None) -> bool:
    report = validate_config(data_dir)

    if report.error_count == 0:
        logger.info(
            "Configuration OK avec {} warning(s) ({} verifications)",
            report.warning_count,
            report.total_checks,
        )
    else:
        logger.error(
            "Configuration invalide: {} erreur(s), {} warning(s), {} verifications",
            report.error_count,
            report.warning_count,
            report.total_checks,
        )

    for item in report.results:
        if item.severity == "error":
            logger.error("[{}] {}", item.category, item.message)
        elif item.severity == "warning":
            logger.warning("[{}] {}", item.category, item.message)
        else:
            logger.debug("[{}] {}", item.category, item.message)

    return report.is_valid
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
