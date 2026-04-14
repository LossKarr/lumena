"""Guard CI — interdit les chemins hardcodés hors src/utils/paths.py.

Ce test scanne src/ et web/ pour détecter les patterns :
  / "data"   / 'data'
  / "workspace"  / 'workspace'
  / "logs"   / 'logs'

Fichiers autorisés (allowlist) : paths.py lui-même + templates/chaînes littérales.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent

# Patterns interdits : division Path par "data", "workspace" ou "logs"
_FORBIDDEN_RE = re.compile(
    r"""/ \s* ["'](?:data|workspace|logs)["']""",
    re.VERBOSE,
)

# Fichiers exclus de la vérification (source de vérité ou faux positifs connus)
_ALLOWLIST: set[str] = {
    # Source de vérité
    "src/utils/paths.py",
    # Chaînes de sécurité / patterns de filtrage (pas de construction de Path)
    "src/tools/file_guardrails.py",
    "src/tools/network_hub.py",
    # .lumena/logs (chemin caché distinct de data/logs)
    "src/reasoning/handlers/heartbeat_self.py",
    # Runtime path logic (conditionnel sur runtime_root dynamique)
    "src/reasoning/react.py",
    # Tests eux-mêmes
    "tests/test_paths_centralized.py",
}


def _collect_violations() -> list[tuple[str, int, str]]:
    """Retourne (fichier_relatif, numéro_ligne, contenu_ligne) pour chaque violation."""
    violations: list[tuple[str, int, str]] = []
    for glob_root in ("src", "web"):
        base = _ROOT / glob_root
        if not base.is_dir():
            continue
        for pyfile in sorted(base.rglob("*.py")):
            rel = pyfile.relative_to(_ROOT).as_posix()
            if rel in _ALLOWLIST:
                continue
            try:
                lines = pyfile.read_text(encoding="utf-8", errors="replace").splitlines()
            except Exception:
                continue
            for i, line in enumerate(lines, start=1):
                stripped = line.lstrip()
                # Ignorer commentaires et docstrings
                if stripped.startswith("#"):
                    continue
                # Ignorer les chaînes de template / prompts LLM / exemples
                if any(kw in line for kw in ("prompt", "PROMPT", "hint", "HINT", "example", "EXAMPLE", "description")):
                    continue
                # Ignorer les lignes de log/print (messages humains)
                if any(kw in stripped for kw in ("logger.", "log.", "print(", "logging.")):
                    continue
                if _FORBIDDEN_RE.search(line):
                    violations.append((rel, i, line.rstrip()))
    return violations


def test_no_hardcoded_data_paths():
    """Aucun fichier src/ ou web/ ne doit construire un chemin avec / 'data' etc."""
    violations = _collect_violations()
    if violations:
        report = "\n".join(
            f"  {f}:{n}  {content}" for f, n, content in violations[:30]
        )
        pytest.fail(
            f"{len(violations)} chemin(s) hardcodé(s) détecté(s) hors paths.py :\n{report}\n\n"
            "→ Importer depuis src.utils.paths au lieu de construire le chemin manuellement."
        )
