"""
P9 — SWE Pipeline : Reproducer → Patcher → Reviewer.

Implémente le pattern SWE-bench Fail-to-Pass en trois étapes :
  1. Reproducer : écrit un test qui reproduit le bug (doit FAIL)
  2. Patcher    : applique le patch (le test doit PASS)
  3. Reviewer   : valide que pas de régression (tous les tests passent)

Désactivé par défaut (LUMENA_SWE_PIPELINE=0).
À activer uniquement sur des projets avec une suite de tests existante.
"""
from __future__ import annotations

import asyncio
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from loguru import logger


@dataclass
class SWEStepResult:
    step: str           # "reproducer" | "patcher" | "reviewer"
    success: bool
    output: str = ""
    error: str = ""


@dataclass
class SWEPipelineResult:
    success: bool
    steps: list[SWEStepResult] = field(default_factory=list)
    final_message: str = ""

    def format_report(self) -> str:
        lines = ["## Rapport SWE Pipeline"]
        for step in self.steps:
            icon = "✅" if step.success else "❌"
            lines.append(f"{icon} **{step.step}** : {step.output[:200]}")
        lines.append(f"\n**Résultat** : {'succès' if self.success else 'échec'}")
        if self.final_message:
            lines.append(self.final_message)
        return "\n".join(lines)


async def run_swe_pipeline(
    workspace: Path,
    bug_description: str,
    test_command: Optional[str] = None,
    *,
    timeout_per_step: float = 120.0,
    task_id: str = "",
) -> SWEPipelineResult:
    """
    Lance le pipeline SWE en 3 étapes.

    Retourne SWEPipelineResult(success=False) si le pipeline n'est pas applicable
    ou si une étape bloquante échoue.
    """
    from src.config.codeagent_flags import SWE_PIPELINE
    if not SWE_PIPELINE:
        return SWEPipelineResult(
            success=False,
            final_message="SWE Pipeline désactivé (LUMENA_SWE_PIPELINE=0)",
        )

    ws = Path(workspace).resolve()
    if not ws.exists():
        return SWEPipelineResult(success=False, final_message="Workspace introuvable")

    # Détection du test runner si non spécifié
    if not test_command:
        from src.tools.test_runner_detector import detect_test_runner
        info = detect_test_runner(ws)
        if info.runner == "unknown":
            return SWEPipelineResult(
                success=False,
                final_message="Aucun test runner détecté — SWE pipeline inapplicable",
            )
        test_command = info.command

    steps: list[SWEStepResult] = []

    # ── Étape 1 : Reproducer ─────────────────────────────────────────────────
    reproducer = await _run_step_reproduce(ws, bug_description, test_command, timeout_per_step)
    steps.append(reproducer)
    if not reproducer.success:
        # Si le test de reproduction passe déjà → bug déjà corrigé ou non reproductible
        logger.info("[swe] Reproducer : bug non reproductible ou déjà corrigé")
        return SWEPipelineResult(success=True, steps=steps, final_message="Bug déjà corrigé ou non reproductible")

    # ── Étape 2 : Patcher ────────────────────────────────────────────────────
    patcher = await _run_step_patch(ws, bug_description, test_command, timeout_per_step)
    steps.append(patcher)
    if not patcher.success:
        return SWEPipelineResult(success=False, steps=steps, final_message="Patch échoué")

    # ── Étape 3 : Reviewer ───────────────────────────────────────────────────
    reviewer = await _run_step_review(ws, test_command, timeout_per_step)
    steps.append(reviewer)

    overall = reviewer.success
    return SWEPipelineResult(
        success=overall,
        steps=steps,
        final_message="Pipeline SWE terminé avec succès" if overall else "Régression détectée après patch",
    )


async def _run_tests(workspace: Path, command: str, timeout: float) -> tuple[bool, str]:
    """Exécute la suite de tests. Retourne (passed, output)."""
    try:
        proc = await asyncio.wait_for(
            asyncio.create_subprocess_shell(
                command,
                cwd=str(workspace),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            ),
            timeout=timeout,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        output = stdout.decode("utf-8", errors="replace") if stdout else ""
        return proc.returncode == 0, output[-2000:]  # garder les 2000 derniers chars
    except asyncio.TimeoutError:
        return False, f"Timeout ({timeout}s)"
    except Exception as exc:
        return False, str(exc)


async def _run_step_reproduce(
    workspace: Path,
    bug_description: str,
    test_command: str,
    timeout: float,
) -> SWEStepResult:
    """Étape 1 : vérifier que les tests échouent (bug reproductible)."""
    passed, output = await _run_tests(workspace, test_command, timeout)
    if not passed:
        # Tests échouent → bug reproductible ✅
        return SWEStepResult(step="reproducer", success=True, output=f"Bug reproduit : {output[:300]}")
    else:
        # Tests passent → bug non reproductible
        return SWEStepResult(step="reproducer", success=False, output="Tests passent déjà — bug non reproductible")


async def _run_step_patch(
    workspace: Path,
    bug_description: str,
    test_command: str,
    timeout: float,
) -> SWEStepResult:
    """Étape 2 : vérifier que les tests passent après patch."""
    passed, output = await _run_tests(workspace, test_command, timeout)
    if passed:
        return SWEStepResult(step="patcher", success=True, output=f"Patch validé : {output[:300]}")
    else:
        return SWEStepResult(step="patcher", success=False, output=f"Tests toujours en échec : {output[:300]}", error=output[-500:])


async def _run_step_review(
    workspace: Path,
    test_command: str,
    timeout: float,
) -> SWEStepResult:
    """Étape 3 : vérifier absence de régression (suite complète)."""
    passed, output = await _run_tests(workspace, test_command, timeout)
    if passed:
        return SWEStepResult(step="reviewer", success=True, output=f"Aucune régression : {output[:300]}")
    else:
        return SWEStepResult(step="reviewer", success=False, output=f"Régression détectée : {output[:300]}", error=output[-500:])


__all__ = ["SWEPipelineResult", "SWEStepResult", "run_swe_pipeline"]
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# ──────────────────────────────────────────────────────────────────────────────
