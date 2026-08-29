"""Run du 2026-08-29 — un log a placeholders morts.

    [PLAN] Fallback sequentiel: '%s' marquee via %s (iter %d)

Loguru formate avec `{}`, pas avec `%s` : il appelle `message.format(*args)`.
Une chaine sans `{}` revient donc INCHANGEE et les arguments sont jetes en
silence. Ce log-la etait le SEUL a expliquer pourquoi « Publier le workspace »
avait ete cochee — et il ne disait rien.

Mesure au moment du correctif : 31 appels dans 13 fichiers vivants de `src/`
et `web/`, tous muets de la meme facon.

Ce test est un garde STRUCTUREL : il relit l'arbre a chaque execution, donc
un 32e appel ecrit demain devient un test rouge.
"""

from __future__ import annotations

import ast
import pathlib
import re

RACINE = pathlib.Path(__file__).resolve().parents[2]
NIVEAUX = {"trace", "debug", "info", "success", "warning",
           "error", "exception", "critical"}


def _sources():
    for base in ("src", "web"):
        for f in (RACINE / base).rglob("*.py"):
            if "backups" in f.parts or "build" in f.parts:
                continue
            yield f


def _utilise_loguru(arbre) -> bool:
    """`logger` vient-il de loguru, ou du module `logging` standard ?

    Distinction INDISPENSABLE : `logging` formate bien avec `%s` — c'est loguru
    qui ne connait que `{}`. La premiere version de ce garde l'ignorait et a
    casse 18 tests en convertissant `src/utils/persistence.py`, qui fait
    `logger = logging.getLogger(__name__)`.
    """
    depuis_loguru = any(
        isinstance(n, ast.ImportFrom) and n.module == "loguru"
        and any(a.name == "logger" for a in n.names)
        for n in ast.walk(arbre)
    )
    if not depuis_loguru:
        return False
    # Un `logger = logging.getLogger(...)` plus bas REDEFINIT le nom.
    for n in ast.walk(arbre):
        if isinstance(n, ast.Assign) and any(
                isinstance(x, ast.Name) and x.id == "logger" for x in n.targets):
            if "getLogger" in ast.dump(n.value):
                return False
    return True


def _appels_muets():
    muets = []
    for f in _sources():
        try:
            arbre = ast.parse(f.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        if not _utilise_loguru(arbre):
            continue
        for n in ast.walk(arbre):
            if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)):
                continue
            if n.func.attr not in NIVEAUX:
                continue
            base = n.func.value
            if not (isinstance(base, ast.Name) and base.id == "logger"):
                continue
            if len(n.args) < 2 or not isinstance(n.args[0], ast.Constant):
                continue
            fmt = n.args[0].value
            if not isinstance(fmt, str) or "{}" in fmt:
                continue
            if re.search(r"%[sdr]", fmt):
                muets.append(f"{f.relative_to(RACINE).as_posix()}:{n.lineno}")
    return muets


def test_aucun_log_ne_jette_ses_arguments_en_silence():
    muets = _appels_muets()
    assert not muets, (
        "loguru formate avec {} : ces appels passent des arguments qui ne "
        "seront JAMAIS affiches — " + ", ".join(muets)
    )


def test_le_log_du_fallback_sequentiel_dit_enfin_quelque_chose():
    """Le log nomme du run : il doit porter des vrais placeholders."""
    src = (RACINE / "src" / "reasoning" / "react_plan_runtime.py").read_text(encoding="utf-8")
    arbre = ast.parse(src)
    trouve = [
        n for n in ast.walk(arbre)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
        and n.value.startswith("[PLAN] Fallback")
    ]
    assert trouve, "le log du fallback sequentiel a disparu"
    fmt = trouve[0].value
    assert fmt.count("{}") == 3, fmt
    assert "%" not in fmt, fmt


def test_le_garde_ne_confond_PAS_logging_standard_et_loguru():
    """`src/utils/persistence.py` fait `logger = logging.getLogger(__name__)` :
    ses `%s` sont CORRECTS et doivent rester intouches."""
    p = RACINE / "src" / "utils" / "persistence.py"
    arbre = ast.parse(p.read_text(encoding="utf-8"))
    assert _utilise_loguru(arbre) is False
    assert "%s" in p.read_text(encoding="utf-8"), (
        "les %s de persistence.py sont corrects — ils ne doivent pas etre convertis"
    )


def test_le_garde_reconnait_bien_loguru():
    arbre = ast.parse((RACINE / "src" / "reasoning" / "react.py").read_text(encoding="utf-8"))
    assert _utilise_loguru(arbre) is True
