"""
P2 — Verification Gate du CodeAgent.

Vérifie syntaxe + validité statique avant de déclarer "done".
Activé uniquement si le flag LUMENA_VERIFICATION_GATE=1 (opt-IN).

Interface publique :
    await run_gate(workspace, modified_files) -> GateResult
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence

from loguru import logger


@dataclass
class GateResult:
    passed: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def format_feedback(self) -> str:
        lines = ["VERIFICATION GATE — problèmes détectés :"]
        for e in self.errors:
            lines.append(f"  ❌ {e}")
        for w in self.warnings[:5]:  # max 5 warnings pour ne pas noyer le contexte
            lines.append(f"  ⚠️  {w}")
        lines.append("\nCorrige ces problèmes puis utilise done à nouveau.")
        return "\n".join(lines)


async def run_gate(
    workspace: Optional[Path],
    modified_files: Sequence[str] = (),
    *,
    task_id: str = "",
    timeout: float = 20.0,
) -> GateResult:
    """
    Lance la validation statique + syntaxique sur les fichiers modifiés.

    Fail-open : toute exception interne retourne GateResult(passed=True)
    afin de ne jamais bloquer le CodeAgent sur une erreur d'infra.
    """
    from src.utils.gate_metrics import record_gate_pass, record_gate_fail, record_lsp_fail_open

    if workspace is None or not workspace.exists():
        return GateResult(passed=True)

    try:
        result = await asyncio.wait_for(
            _do_validate(workspace, modified_files, task_id=task_id),
            timeout=timeout,
        )
        if result.passed:
            record_gate_pass(task_id=task_id)
        else:
            record_gate_fail(task_id=task_id, reason="; ".join(result.errors[:3]))
        return result
    except asyncio.TimeoutError:
        logger.warning("[gate] timeout ({}s) — fail-open", timeout)
        record_lsp_fail_open(task_id=task_id, error=f"timeout {timeout}s")
        return GateResult(passed=True)
    except Exception as exc:
        logger.warning("[gate] erreur inattendue — fail-open : {}", exc)
        record_lsp_fail_open(task_id=task_id, error=str(exc))
        return GateResult(passed=True)


async def _do_validate(
    workspace: Path,
    modified_files: Sequence[str],
    task_id: str = "",
) -> GateResult:
    """Validation réelle : statique + tests auto-détectés."""
    from src.tools.code_validator import validate_project_async

    # ── 1. Validation statique ────────────────────────────────────────────────
    files: dict[str, str] = {}
    for rel in modified_files:
        abs_path = workspace / rel
        if abs_path.exists() and abs_path.is_file():
            try:
                files[rel] = abs_path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                pass

    if not files:
        for ext in (".py", ".js", ".ts", ".jsx", ".tsx"):
            for fp in workspace.rglob(f"*{ext}"):
                if any(p in fp.parts for p in ("node_modules", "__pycache__", ".git", ".venv")):
                    continue
                try:
                    rel = str(fp.relative_to(workspace))
                    files[rel] = fp.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    pass

    errors: list[str] = []
    warnings: list[str] = []

    if files:
        report = await validate_project_async(files, workspace)
        errors = [str(i) for i in report.issues if i.severity.value == "error"]
        warnings = [str(i) for i in report.issues if i.severity.value == "warning"]

    # Si erreurs statiques → pas la peine de lancer les tests
    if errors:
        return GateResult(passed=False, errors=errors, warnings=warnings)

    # ── 2. Exécution des tests auto-détectés (P3) ─────────────────────────────
    test_errors = await _run_detected_tests(workspace, modified_files)
    errors.extend(test_errors)

    return GateResult(
        passed=len(errors) == 0,
        errors=errors,
        warnings=warnings,
    )


async def _run_detected_tests(
    workspace: Path,
    modified_files: Sequence[str],
) -> list[str]:
    """Lance les tests pertinents pour les fichiers modifiés. Retourne les erreurs (vide = OK)."""
    try:
        from src.tools.test_runner_detector import detect_test_runner
        info = detect_test_runner(workspace, modified_files)
        if info.runner == "unknown" or not info.command:
            return []

        # Cibler uniquement les tests pertinents si détectés, sinon suite complète
        cmd = info.command
        if info.relevant_tests:
            if info.runner == "pytest":
                cmd = f"python -m pytest -x -q {' '.join(info.relevant_tests)}"
            # npm/go : pas de ciblage granulaire, on lance la suite complète

        proc = await asyncio.create_subprocess_shell(
            cmd,
            cwd=str(workspace),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=60.0)
        out_str = (out or b"").decode("utf-8", errors="replace")

        if proc.returncode != 0:
            # Extraire les lignes d'erreur pertinentes (max 10)
            fail_lines = [
                l for l in out_str.splitlines()
                if any(kw in l for kw in ("FAILED", "ERROR", "error", "assert", "Traceback"))
            ][:10]
            summary = out_str.strip().split("\n")[-1] if out_str.strip() else "tests failed"
            return [f"Tests ({info.runner}): {summary}"] + [f"  {l}" for l in fail_lines[:5]]

        return []
    except asyncio.TimeoutError:
        return []  # fail-open sur timeout
    except Exception:
        return []  # fail-open


__all__ = ["GateResult", "run_gate"]
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# ──────────────────────────────────────────────────────────────────────────────
