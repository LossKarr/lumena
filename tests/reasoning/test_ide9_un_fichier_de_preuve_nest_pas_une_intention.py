"""Lot IDE-9 - un fichier de preuve ne liste pas ce qu'on s'apprete a tenter.

--- Ce que le run du 24/09 a laisse sur le disque de Charles ---

A 20 h 05 44, Lumena ecrit `workspace/ide-demo/PREUVE_IDE.md` :

    # Demo pilotage Lumena IDE
    Fichier cree par Lumena pour prouver qu'elle pilote son IDE.
    - Ouverture de fichier via protocole IDE
    - Lecture de l'etat Git
    - Zero modification du code source de Lumena

**Les deux premieres lignes decrivent des actions qui ont ECHOUE.** Elle a ecrit le
fichier AVANT de les tenter - comme une liste d'intentions - puis `ide__open_file` et
`ide__git_status` ont rendu `ide_snapshot_stale`, et le fichier est reste.

Son FINAL, lui, etait HONNETE : elle a distingue ce qu'elle avait fait de ce qui
bloquait, cite le code d'erreur, dit « 6+ fois sans deblocage ». Les verrous de verite
ont fonctionne. **Le defaut n'est pas dans ce qu'elle a dit, il est dans ce qu'elle a
laisse sur le disque** - et dans un mois ce fichier se relira comme une preuve.

--- La portee de ce lot, dite franchement ---

Le contenu libre d'un fichier n'est pas mecanisable : aucun garde ne peut juger si une
phrase est vraie. Le seul levier honnete est la regle, posee la ou elle sera lue.

La regle 1 du prompt ReAct existait deja - « N'affirme JAMAIS avoir fait une action
sans OBSERVATION confirmee » - mais elle ne parlait que de ce que Lumena DIT. Un
fichier n'est pas une affirmation au sens du truth-lock, qui garde le FINAL. C'est
exactement par cette fente que l'artefact est passe.

Ce fichier fige donc l'extension de la regle, et rien de plus. C'est le lot le plus
faible des cinq, et il faut le savoir : il rend le geste explicite, il ne l'empeche pas.
"""
from __future__ import annotations

import inspect

from src.prompts import react_prompt


def _regles() -> str:
    return inspect.getsource(react_prompt)


def test_la_regle_anti_hallucination_couvre_les_fichiers():
    """Le fait mesure : la regle ne parlait que de ce qu'elle DIT."""
    source = _regles()
    debut = source.index("1. ANTI-HALLUCINATION")
    regle = source[debut:debut + 900]
    assert "FICHIER" in regle.upper(), (
        "la regle ne mentionne pas les fichiers : un artefact peut encore attester "
        "un travail non fait"
    )


def test_la_regle_dit_APRES_et_pas_avant():
    """C'est l'ordre qui a produit le defaut : le fichier a ete ecrit a 20 h 05 44,
    les actions qu'il decrit tentees ensuite - et echouees."""
    source = _regles()
    debut = source.index("1. ANTI-HALLUCINATION")
    regle = source[debut:debut + 900]
    assert "APRES" in regle, regle[:400]
    assert "avant" in regle.lower(), regle[:400]


def test_la_regle_nomme_les_formes_reelles_d_artefact():
    """« preuve » est le mot exact du fichier laisse sur le disque. Les rapports et
    comptes rendus posent le meme risque et passent par le meme geste."""
    source = _regles()
    debut = source.index("1. ANTI-HALLUCINATION")
    regle = source[debut:debut + 900].lower()
    for forme in ("preuve", "rapport"):
        assert forme in regle, f"forme absente de la regle : {forme}"


def test_la_regle_dit_POURQUOI():
    """Une consigne sans raison se fait contourner des qu'elle gene - mesure du jour :
    devant un refus de `run_command`, elle a cherche et trouve un contournement en une
    iteration. La raison ici est que le fichier SURVIT a l'echec."""
    source = _regles()
    debut = source.index("1. ANTI-HALLUCINATION")
    regle = source[debut:debut + 900].lower()
    assert "disque" in regle or "survi" in regle or "restera" in regle or "reste" in regle, regle[:400]


def test_l_ancienne_regle_est_conservee():
    """Caracterisation : ce lot ETEND, il ne remplace pas. Le coeur - une affirmation
    exige son OBSERVATION - doit rester intact."""
    source = _regles()
    debut = source.index("1. ANTI-HALLUCINATION")
    regle = source[debut:debut + 900]
    assert "OBSERVATION" in regle
    assert "JAMAIS" in regle
