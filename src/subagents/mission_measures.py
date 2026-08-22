"""LOT N1 — le CONSTAT mesuré d'une mission doit survivre jusqu'à l'utilisateur.

Run HuffPack (2026-08-14). La consigne était explicite :

    À MESURER ET À RAPPORTER HONNÊTEMENT : le taux de compression obtenu sur
    trois entrées […]. Donne les trois chiffres réels, même s'ils sont mauvais.

Le lead a mesuré, pour de vrai :

    Texte français répétitif :  7 500 →  3 549 octets    47,32 %
    Fichier core.py          :  4 896 →  3 363 octets    68,69 %
    50 Ko aléatoires         : 51 200 → 52 489 octets   102,52 %   ← le fichier GROSSIT

Ces chiffres sont apparus dans le log à 03:11:27… et nulle part ailleurs. Quand
l'utilisateur a demandé « alors la dernière mission », le récapitulatif — pourtant
libre et bien formulé — n'a pu dire que « 12 passed », parce que les faits
autoritatifs s'arrêtent à quatre compteurs :

    cause=completed | publie=workspace/huffpack | tests=verts (12 passed) | workers=3/3

Le modèle s'était pourtant promis « je livre le récapitulatif avec les chiffres
concrets ». Il ne les a pas inventés — le truth-lock a tenu — il ne les avait
simplement plus. C'est le motif racine du chantier, au dernier mètre : le fait
existait, était calculé, était affiché, puis JETÉ avant d'atteindre l'utilisateur.

Le correctif ne touche pas à la façon dont Lumena s'exprime : il remet le constat
dans les faits, et le récapitulatif libre le dira de lui-même.

Calibrage sur les **113 commandes uniques réellement exécutées** (212 au total,
6 fichiers de logs) : 49 tests/compilations, 21 `python -c` inline, 11 inspections,
et **3 exécutions de script** — dont `python bench/benchmark.py`, le constat de
HuffPack. Le critère est donc très sélectif par construction.

Module auto-contenu (stdlib) → testable sans runtime.
"""
from __future__ import annotations

import re

__all__ = [
    "command_is_measurement",
    "summarize_measurement_output",
    "merge_measurement",
    "format_measurements",
]

_MAX_MEASUREMENTS = 5
_MAX_OUTPUT_CHARS = 700

# Un test, un linter ou une compilation ne sont PAS des constats : leur verdict a
# déjà sa place dans les faits (`tests=verts (12 passed)`).
_NOT_A_MEASUREMENT = (
    "pytest", "py_compile", "unittest", "ruff", "flake8", "mypy", "black",
    "pylint", "isort", "coverage", "pip ", "pip3 ", "npm ", "npx ", "yarn ",
    "git ", "curl ", "wget ",
)
# Inspecter n'est pas mesurer.
_INSPECTION_PREFIXES = (
    "ls", "dir", "cat", "type", "where", "which", "echo", "cd", "pwd",
    "head", "tail", "find", "tree", "mkdir", "del", "rm", "copy", "cp", "mv",
)
# « python chemin/script.py » — un script DU PROJET qu'on exécute pour voir ce
# qu'il dit. C'est la forme qu'a prise le benchmark.
_SCRIPT_RUN_RE = re.compile(
    r"^(?:python|python3|py)(?:\s+-[A-Za-z]+)*\s+(?!-)(\S+\.py)\b", re.IGNORECASE
)


def command_is_measurement(command: str) -> bool:
    """True si la commande produit un CONSTAT à rapporter (pur/testable)."""
    cmd = " ".join(str(command or "").split())
    if not cmd:
        return False
    low = cmd.lower()
    if any(token in low for token in _NOT_A_MEASUREMENT):
        return False
    first = low.split(" ", 1)[0].strip()
    if first in _INSPECTION_PREFIXES:
        return False
    return bool(_SCRIPT_RUN_RE.match(cmd))


def summarize_measurement_output(output: str, *, limit: int = _MAX_OUTPUT_CHARS) -> str:
    """Sortie normalisée d'un constat — "" si rien d'exploitable.

    On ne résume PAS : on garde les lignes telles quelles. Reformuler un chiffre
    mesuré serait exactement la fabrication que ce lot combat. On coupe seulement
    si c'est trop long, et on le DIT.
    """
    text = str(output or "").replace("\r\n", "\n").strip()
    if not text:
        return ""
    lines = [ln.rstrip() for ln in text.split("\n") if ln.strip()]
    if not lines:
        return ""
    kept, total = [], 0
    for line in lines:
        if total + len(line) + 1 > limit:
            kept.append("… (sortie tronquée)")
            break
        kept.append(line)
        total += len(line) + 1
    return "\n".join(kept)


def merge_measurement(existing: object, command: str, output: str) -> list:
    """Ajoute un constat à la liste persistée — dernière valeur par commande.

    Une même commande relancée (le lead rejoue souvent le benchmark après une
    correction) doit garder sa DERNIÈRE sortie, pas la première : c'est celle qui
    reflète le code livré. Liste bornée à `_MAX_MEASUREMENTS`, les plus récentes.
    """
    cmd = " ".join(str(command or "").split())
    body = summarize_measurement_output(output)
    if not cmd or not body:
        return [dict(m) for m in (existing or []) if isinstance(m, dict)]
    kept = [
        dict(m) for m in (existing or [])
        if isinstance(m, dict) and m.get("command") != cmd
    ]
    kept.append({"command": cmd, "output": body})
    return kept[-_MAX_MEASUREMENTS:]


def format_measurements(measurements: object) -> str:
    """Rendu lisible des constats pour le récapitulatif — "" si aucun."""
    rows = [m for m in (measurements or []) if isinstance(m, dict) and m.get("output")]
    if not rows:
        return ""
    blocks = []
    for row in rows:
        cmd = str(row.get("command") or "").strip()
        out = str(row.get("output") or "").strip()
        blocks.append(f"$ {cmd}\n{out}" if cmd else out)
    return "\n\n".join(blocks)
