"""
P3 — Détecteur de test runner pour le flux Fail-to-Pass.

Inspecte le workspace pour déterminer quel test runner utiliser et
quels fichiers de test sont pertinents pour les fichiers modifiés.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence

from loguru import logger


@dataclass
class TestRunnerInfo:
    runner: str              # "pytest" | "npm_test" | "go_test" | "unknown"
    command: str             # Commande complète à exécuter
    relevant_tests: list[str] = field(default_factory=list)  # Chemins relatifs des tests pertinents
    config_file: Optional[str] = None   # Fichier de config détecté


def detect_test_runner(
    workspace: Path,
    modified_files: Sequence[str] = (),
) -> TestRunnerInfo:
    """
    Détecte le test runner pour un workspace et les tests pertinents.

    Ordre de priorité :
      1. pytest (pyproject.toml / setup.cfg / pytest.ini / conftest.py)
      2. npm test (package.json avec scripts.test)
      3. go test (go.mod)
      4. unknown (fallback)
    """
    ws = Path(workspace).resolve()

    # ── pytest ────────────────────────────────────────────────────────────
    if _has_pytest(ws):
        tests = _find_python_tests(ws, modified_files)
        return TestRunnerInfo(
            runner="pytest",
            command="python -m pytest -x -q",
            relevant_tests=tests,
            config_file=_pytest_config_file(ws),
        )

    # ── npm test ──────────────────────────────────────────────────────────
    npm_info = _detect_npm(ws)
    if npm_info:
        tests = _find_js_tests(ws, modified_files)
        return TestRunnerInfo(
            runner="npm_test",
            command=npm_info,
            relevant_tests=tests,
            config_file="package.json",
        )

    # ── go test ───────────────────────────────────────────────────────────
    if (ws / "go.mod").exists():
        return TestRunnerInfo(
            runner="go_test",
            command="go test ./...",
            relevant_tests=[],
            config_file="go.mod",
        )

    return TestRunnerInfo(runner="unknown", command="", relevant_tests=[])


def _has_pytest(ws: Path) -> bool:
    markers = [
        "pyproject.toml", "setup.cfg", "pytest.ini", "tox.ini",
        "conftest.py", "setup.py",
    ]
    if any((ws / m).exists() for m in markers):
        return True
    # Chercher un dossier tests/ ou test/ avec des fichiers .py
    for d in ("tests", "test"):
        td = ws / d
        if td.is_dir() and any(td.rglob("test_*.py")):
            return True
    return bool(list(ws.rglob("test_*.py"))[:1])


def _pytest_config_file(ws: Path) -> Optional[str]:
    for name in ("pyproject.toml", "pytest.ini", "setup.cfg", "conftest.py"):
        if (ws / name).exists():
            return name
    return None


def _detect_npm(ws: Path) -> Optional[str]:
    pkg = ws / "package.json"
    if not pkg.exists():
        return None
    try:
        data = json.loads(pkg.read_text(encoding="utf-8", errors="ignore"))
        scripts = data.get("scripts", {})
        if "test" in scripts:
            return "npm test"
        if "jest" in data.get("devDependencies", {}):
            return "npx jest"
        if "vitest" in data.get("devDependencies", {}):
            return "npx vitest run"
    except Exception:
        pass
    return None


def _find_python_tests(ws: Path, modified_files: Sequence[str]) -> list[str]:
    """Trouve les tests pytest pertinents pour les fichiers modifiés."""
    if not modified_files:
        return []

    relevant: list[str] = []
    modified_modules = {Path(f).stem.lstrip("test_") for f in modified_files if f.endswith(".py")}

    for test_file in ws.rglob("test_*.py"):
        stem = test_file.stem  # e.g. "test_calculator"
        module = stem.removeprefix("test_")
        if module in modified_modules:
            try:
                relevant.append(str(test_file.relative_to(ws)))
            except ValueError:
                pass

    # Dédupliquer et limiter
    return relevant[:10]


def _find_js_tests(ws: Path, modified_files: Sequence[str]) -> list[str]:
    """Trouve les tests JS/TS pertinents pour les fichiers modifiés."""
    if not modified_files:
        return []

    modified_stems = {Path(f).stem for f in modified_files}
    relevant: list[str] = []

    for pattern in ("**/*.test.js", "**/*.spec.js", "**/*.test.ts", "**/*.spec.ts"):
        for tf in ws.glob(pattern):
            if "node_modules" in tf.parts:
                continue
            stem = re.sub(r"\.(test|spec)$", "", tf.stem)
            if stem in modified_stems:
                try:
                    relevant.append(str(tf.relative_to(ws)))
                except ValueError:
                    pass

    return relevant[:10]


def format_for_architect(info: TestRunnerInfo) -> str:
    """Formate les infos du test runner pour injection dans le prompt Architect."""
    if info.runner == "unknown":
        return ""

    lines = [f"## Test runner détecté : {info.runner}"]
    lines.append(f"Commande : `{info.command}`")
    if info.relevant_tests:
        lines.append("Tests pertinents :")
        for t in info.relevant_tests:
            lines.append(f"  - {t}")
    lines.append(
        "\n**Flux Fail-to-Pass** : si une régression est détectée, "
        "écris d'abord le test qui reproduit le bug, vérifie qu'il échoue, "
        "puis applique le patch et vérifie qu'il passe."
    )
    return "\n".join(lines)


__all__ = ["TestRunnerInfo", "detect_test_runner", "format_for_architect"]
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# ──────────────────────────────────────────────────────────────────────────────
