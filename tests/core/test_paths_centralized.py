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

# RACINE DU DEPOT, pas `tests/`.
#
# `parent.parent` depuis `tests/core/` donnait `tests/` : le garde scannait
# `tests/src` (inexistant) et `tests/web`, donc il n'a JAMAIS regarde les
# vrais `src/` et `web/`. Son allowlist le prouve — elle liste
# `src/utils/paths.py` et `src/reasoning/react.py`, des chemins relatifs a
# la racine du depot que son propre calcul ne pouvait pas produire. Le garde
# etait inerte depuis sa creation, et ne rougissait que sur des fichiers de
# test qui se trouvaient sous `tests/web/`.
_ROOT = Path(__file__).resolve().parents[2]

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

    # ── Racines DERIVEES, pas reconstruites ────────────────────────────────
    # Ces sept sites divisent une racine deja resolue (`ROOT_DIR`, un
    # `project_root` recu en argument, un repertoire temporaire) par un
    # sous-dossier. Le motif du garde est purement textuel et ne sait pas
    # distinguer les deux ; ce sont des faux positifs, verifies un par un
    # quand le garde a ete rebranche sur le vrai arbre.
    #
    #   codex_codeagent.py:390     Path(temp_dir.name) / "workspace"
    #   execution_router.py:848    ROOT_DIR / "workspace"  (deja paths.py)
    #   delegate_strategy.py:258   base / "workspace"      (base recu)
    #   final_guards.py:973,1006   project_root / "workspace"
    #   system.py:1290             root / "data" / "ops"   (root recu)
    "src/llm/codex_codeagent.py",
    "src/llm/execution_router.py",
    "src/reasoning/delegate_strategy.py",
    "src/reasoning/final_guards.py",
    "src/reasoning/handlers/system.py",
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


def test_le_garde_regarde_VRAIMENT_le_depot():
    """Le defaut qui a rendu ce fichier inutile pendant toute sa vie.

    `_ROOT` pointait sur `tests/`, donc `src/` n'existait pas pour lui et il ne
    scannait que `tests/web/`. Un garde qui ne regarde rien passe au vert pour
    toujours — c'est la pire forme d'echec, parce qu'elle ressemble a un succes.

    Ce test verifie la seule chose qu'aucune assertion de contenu ne peut
    verifier : que le garde a bien du MATERIEL a inspecter."""
    assert (_ROOT / "src" / "utils" / "paths.py").is_file(), (
        "la racine du garde ne contient pas src/utils/paths.py : il scanne le "
        "mauvais arbre et repassera au vert sans rien avoir verifie"
    )
    fichiers = list((_ROOT / "src").rglob("*.py"))
    assert len(fichiers) > 200, (
        f"seulement {len(fichiers)} fichiers dans src/ : perimetre suspect"
    )


def test_l_allowlist_ne_contient_que_des_fichiers_EXISTANTS():
    """Une entree d'allowlist qui ne correspond a aucun fichier est soit une
    faute de frappe, soit un reste de refactor — dans les deux cas elle
    n'excuse rien et laisse croire qu'elle excuse quelque chose."""
    fantomes = sorted(e for e in _ALLOWLIST
                      if not (_ROOT / e).is_file() and not e.startswith("tests/"))
    assert not fantomes, f"entrees d'allowlist sans fichier : {fantomes}"
