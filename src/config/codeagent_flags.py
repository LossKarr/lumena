"""
Feature flags pour les améliorations CodeAgent (PLAN_SUPREME_CODEAGENT).

Tous les flags sont opt-OUT (activés par défaut). Mettre la variable
d'environnement à "0" / "false" / "no" pour désactiver.

Usage:
    from src.config.codeagent_flags import PROVIDER_PROMPTS

    if PROVIDER_PROMPTS:
        prompt = _load_provider_prompt(model_name)
    else:
        prompt = _CODE_AGENT_SYSTEM  # fallback original

Convention: chaque feature de chaque phase a son flag dédié, ce qui permet
de rollback une feature individuelle si elle dégrade un modèle spécifique.
"""

from __future__ import annotations

import os


def _flag(name: str, default: bool = True) -> bool:
    """Lit un flag d'environnement avec valeur par défaut."""
    raw = os.environ.get(f"LUMENA_{name}")
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


# ── P0: Prompts par provider ─────────────────────────────────
PROVIDER_PROMPTS: bool = _flag("PROVIDER_PROMPTS")

# ── P0b: Descriptions outils .txt séparés ────────────────────
TOOL_HINTS: bool = _flag("TOOL_HINTS")

# ── P1: Fuzzy replace (4 nouvelles stratégies) ───────────────
FUZZY_REPLACE: bool = _flag("FUZZY_REPLACE")

# ── P2: Compaction prune progressif + tiktoken ───────────────
COMPACTION_PRUNE: bool = _flag("COMPACTION_PRUNE")

# ── P3: Plan mode read-only ──────────────────────────────────
PLAN_MODE: bool = _flag("PLAN_MODE")

# ── P4: Output truncation avec sauvegarde disque ─────────────
TRUNCATION_SAVE: bool = _flag("TRUNCATION_SAVE")

# ── P5: Max-steps graceful (résumé structuré final) ──────────
MAX_STEPS_GRACEFUL: bool = _flag("MAX_STEPS_GRACEFUL")

# ── P6: Auto-format post-edit (ruff / prettier) ──────────────
AUTO_FORMAT: bool = _flag("AUTO_FORMAT")

# ── P7: Quality gates partagés ReAct ↔ CodeAgent ─────────────
REACT_QUALITY_GATES: bool = _flag("REACT_QUALITY_GATES")

# ── P8: Features OpenCode manquantes (sub-flags) ─────────────
DID_YOU_MEAN: bool = _flag("DID_YOU_MEAN")
MODEL_TEMPERATURES: bool = _flag("MODEL_TEMPERATURES")
COMPACTION_REPLAY: bool = _flag("COMPACTION_REPLAY")
INVALID_TOOL_CATCH: bool = _flag("INVALID_TOOL_CATCH")
CRLF_NORMALIZE: bool = _flag("CRLF_NORMALIZE")
ENV_CONTEXT: bool = _flag("ENV_CONTEXT")
SSE_TIMEOUT: bool = _flag("SSE_TIMEOUT")
PROMPT_CACHE: bool = _flag("PROMPT_CACHE")

# ── P10: Observabilité ───────────────────────────────────────
CODING_METRICS: bool = _flag("CODING_METRICS")

# ── P11: Polish UX ───────────────────────────────────────────
DESTRUCTIVE_CONFIRM: bool = _flag("DESTRUCTIVE_CONFIRM", default=False)  # opt-IN (peut bloquer batch)
FRENCH_ERRORS: bool = _flag("FRENCH_ERRORS")

# ── Upgrade Final ─────────────────────────────────────────────
# P2 — Verification Gate (validate avant de déclarer "done")
VERIFICATION_GATE: bool = _flag("VERIFICATION_GATE", default=False)  # opt-IN progressif

# P3 — Fail-to-Pass flow (test d'abord, puis patch)
FAIL_TO_PASS: bool = _flag("FAIL_TO_PASS", default=False)  # opt-IN

# P5 — LSP pre-edit (inject dépendances avant modification)
LSP_PRE_EDIT: bool = _flag("LSP_PRE_EDIT", default=False)  # opt-IN

# P6 — Convention scanning (tsconfig/eslint/pyproject injectés)
CONVENTION_SCAN: bool = _flag("CONVENTION_SCAN", default=True)

# P8 — FileWatcher bridge IDE
FILE_WATCHER_BRIDGE: bool = _flag("FILE_WATCHER_BRIDGE", default=False)  # opt-IN

# P9 — SWE pipeline (Reproducer→Patcher→Reviewer)
SWE_PIPELINE: bool = _flag("SWE_PIPELINE", default=False)  # opt-IN, désactivé par défaut


__all__ = [
    "PROVIDER_PROMPTS",
    "TOOL_HINTS",
    "FUZZY_REPLACE",
    "COMPACTION_PRUNE",
    "PLAN_MODE",
    "TRUNCATION_SAVE",
    "MAX_STEPS_GRACEFUL",
    "AUTO_FORMAT",
    "REACT_QUALITY_GATES",
    "DID_YOU_MEAN",
    "MODEL_TEMPERATURES",
    "COMPACTION_REPLAY",
    "INVALID_TOOL_CATCH",
    "CRLF_NORMALIZE",
    "ENV_CONTEXT",
    "SSE_TIMEOUT",
    "PROMPT_CACHE",
    "CODING_METRICS",
    "DESTRUCTIVE_CONFIRM",
    "FRENCH_ERRORS",
    "VERIFICATION_GATE",
    "FAIL_TO_PASS",
    "LSP_PRE_EDIT",
    "CONVENTION_SCAN",
    "FILE_WATCHER_BRIDGE",
    "SWE_PIPELINE",
]
