"""test_proof.py — Preuve d'exécution de tests pour le VERROU DE VÉRITÉ mission.

Pur, stdlib uniquement. Aucun import de react/handlers → aucun cycle.

Raison d'être (cf. run bibliotech 2026-07-01) : une mission a annoncé
« terminée et certifiée ✅ — 10 tests verts » alors que le dernier pytest réel
donnait « 5 passed, 8 errors », et un `pytest.ini` annoncé n'existait pas. Le
ledger ne savait pas répondre à « un pytest VERT a-t-il réellement tourné ? ».

Ce module fournit le parseur d'issue de tests utilisé au moment de l'append
ledger (méta `test_outcome`), pour que `ExecutionLedger.has_green_test_run()`
puisse trancher. Règle d'or : un run vert obtenu via `--ignore` inventé (le
contournement du run bibliotech sur `tests/.backups`) n'est PAS une preuve verte.
"""

from __future__ import annotations

import re
from typing import Dict, Optional

# Commandes reconnues comme lançant une suite de tests.
_TEST_CMD_RE = re.compile(
    r"\b("
    r"pytest|py\.test|python(?:3)?\s+-m\s+pytest|"
    r"npm\s+(?:run\s+)?test|yarn\s+test|jest|vitest|"
    r"cargo\s+test|go\s+test|mocha|phpunit|rspec"
    r")\b",
    re.IGNORECASE,
)

# Contournement de portée : --ignore / --ignore-glob (le run bibliotech a
# inventé `--ignore=tests/.backups` pour « faire passer » la collecte).
_INVENTED_IGNORE_RE = re.compile(r"--ignore(?:-glob)?[=\s]", re.IGNORECASE)


def is_test_command(command: str) -> bool:
    """True si la commande shell lance une suite de tests connue."""
    return bool(command and _TEST_CMD_RE.search(command))


# ── Détection de FICHIERS de test (P0.2, cf. run PollApp multi-worker) ─────────
# Quand une suite de tests EXISTE (écrite par un worker → hors ledger du lead),
# le lead ne doit pas conclure « vérifié structurellement » sans l'avoir passée
# verte lui-même. On détecte la présence via le nom de fichier (test_*.py /
# *_test.py), pas via le ledger (aveugle aux tests des workers).
_TEST_FILENAME_RE = re.compile(r"(?:^test_.+\.py$|.+_test\.py$)", re.IGNORECASE)


def is_test_filename(name: str) -> bool:
    """True si `name` est un fichier de test pytest (test_*.py / *_test.py).

    Accepte un basename ou un chemin (on ne garde que le dernier segment).
    """
    if not name:
        return False
    base = str(name).replace("\\", "/").rsplit("/", 1)[-1].strip()
    return bool(_TEST_FILENAME_RE.match(base))


def any_test_file(names) -> bool:
    """True si au moins un nom de la collection est un fichier de test."""
    try:
        return any(is_test_filename(n) for n in (names or ()))
    except TypeError:
        return False


def tests_present_in_dir(path) -> bool:
    """LOT 2.5 — scan BORNÉ d'un dossier de mission : fichiers de test à la racine
    ou dans son sous-dossier `tests/`. JAMAIS récursif large (garde-fou P0.2 : ne
    jamais risquer de voir les tests de Lumena et rétrograder à tort)."""
    import os
    try:
        p = str(path or "").strip()
        if not p or not os.path.isdir(p):
            return False
        if any_test_file(os.listdir(p)):
            return True
        sub = os.path.join(p, "tests")
        return os.path.isdir(sub) and any_test_file(os.listdir(sub))
    except OSError:
        return False


def tests_present_in_contract(data) -> bool:
    """LOT 2.5 — True si le contrat de mission (dict parsé de contract.json, cf.
    mission_contract) DÉCLARE au moins un fichier de test dans files[].path.

    Source encore plus fiable que le disque : couvre `tests/test_api.py` même si
    le worker ne l'a pas encore écrit au moment du scan."""
    try:
        files = (data or {}).get("files") or []
        return any_test_file([f.get("path") for f in files if isinstance(f, dict)])
    except (AttributeError, TypeError):
        return False


def _uses_invented_ignore(command: str) -> bool:
    return bool(command and _INVENTED_IGNORE_RE.search(command))


def parse_test_outcome(
    command: str,
    output: str,
    exit_code: Optional[int] = None,
) -> Dict[str, object]:
    """Parse l'issue d'un run de tests depuis la commande + sa sortie.

    Retourne un dict sérialisable (stocké tel quel dans la méta ledger) :
      is_test_cmd, passed, failed, errors, collection_error, ran_something,
      used_invented_ignore, exit_code, green.

    `green` (preuve VERTE probante) exige TOUT :
      - au moins 1 test passé, 0 échec, 0 erreur ;
      - pas d'erreur de collecte (import mismatch, interrupted) ;
      - exit_code 0 (ou inconnu → None toléré, on s'appuie sur la sortie) ;
      - AUCUN `--ignore` inventé (sinon la portée réelle n'est pas prouvée).
    """
    cmd = command or ""
    out = (output or "").lower()

    def _count(pat: str) -> int:
        # C0.5 (run FrigoZen) : DERNIÈRE occurrence — le résumé pytest est en FIN
        # de sortie. La première occurrence pouvait tomber dans le corps d'un échec
        # (« assert 0 == 1\nFAILED tests\… » → failed=1 au lieu des 5 réels).
        matches = re.findall(pat, out)
        return int(matches[-1]) if matches else 0

    passed = _count(r"(\d+)\s+passed")
    failed = _count(r"(\d+)\s+failed")
    errors = _count(r"(\d+)\s+error")

    # Échec dur de collecte pytest : le projet ne tourne même pas.
    collection_error = (
        "error during collection" in out
        or "import file mismatch" in out
        or ("interrupted" in out and "error" in out)
    )
    ran_something = (passed + failed + errors) > 0
    used_invented_ignore = _uses_invented_ignore(cmd)

    green = (
        passed > 0
        and failed == 0
        and errors == 0
        and not collection_error
        and (exit_code in (None, 0))
        and not used_invented_ignore
    )

    return {
        "is_test_cmd": is_test_command(cmd),
        "passed": passed,
        "failed": failed,
        "errors": errors,
        "collection_error": bool(collection_error),
        "ran_something": bool(ran_something),
        "used_invented_ignore": bool(used_invented_ignore),
        "exit_code": exit_code,
        "green": bool(green),
    }


# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
