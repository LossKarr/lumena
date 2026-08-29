"""Z40a — une redirection de chemin doit ATTEINDRE l'appelant.

Defaut mesure en production le 2026-08-28 (run « repartition », log complet).

Le CodeAgent, enferme dans `workspace/tests` par un routage fuzzy, a compris
qu'il lui fallait un dossier `repartition` et a demande :

    write_file ../repartition/repartition.py

`_resolve_path` a REFUSE le chemin — puis l'a REDIRIGE vers le workspace
courant, et a rendu ce chemin. L'ecriture a reussi. L'observation retournee
disait :

    ✅ Fichier écrit: ../repartition/repartition.py (2656 chars)

soit le chemin DEMANDE, jamais le chemin REEL. Le WARNING `BLOCKED path
traversal` est parti quinze fois dans les logs et n'a jamais atteint celui qui
decide.

Consequence mesuree au log : le CodeAgent a passe **5 iterations sur 9** a
chercher ses propres fichiers —

    iter 3  cd ..\\repartition && pytest        -> exit:1, syntaxe
    iter 4  pytest ..\\repartition\\test_...     -> exit:4, introuvable
    iter 5  pytest C:\\...\\repartition\\test_... -> exit:4, introuvable
    iter 6  list_files ../repartition          -> BLOCKED
    iter 7  list_files .                       -> les trouve enfin

C'est le motif de fond du chantier : **le fait existe, il est calcule, il est
meme journalise — et il n'atteint pas celui qui decide.**

--- Ce que ce lot NE change PAS ---

Le blocage lui-meme, et la destination du fichier. Le perimetre task-scoped
(lot 2.10) reste etanche, et le fichier atterrit exactement ou il atterrissait
avant. Z40a rend la redirection VISIBLE, il ne relocalise rien.
"""

from __future__ import annotations

import asyncio
import pathlib
import tempfile

import pytest

from src.agents.sub_agent import CodeAgent, SubAgent


CHEMIN_HORS_WORKSPACE = "../repartition/repartition.py"


def _agent(cls=CodeAgent):
    """Un agent avec un workspace de tache, sans passer par `__init__`.

    C'est la forme que prend le CodeAgent en mission : `_task_workspace_root`
    pose, le reste absent.
    """
    racine = pathlib.Path(tempfile.mkdtemp(prefix="z40a_"))
    (racine / "tests").mkdir()
    agent = object.__new__(cls)
    agent._task_workspace_root = racine / "tests"
    return agent, racine


# ══════════════════════════════════════════════════════════════════════════
#  1. Le fait : la redirection a bien lieu, et la destination NE CHANGE PAS
# ══════════════════════════════════════════════════════════════════════════


def test_le_chemin_hors_workspace_est_toujours_redirige_au_meme_endroit():
    """Z40a ne relocalise rien. Le fichier atterrit exactement ou il
    atterrissait avant : dans le workspace courant, sous son nom seul."""
    agent, racine = _agent(SubAgent)
    resolu = agent._resolve_path(CHEMIN_HORS_WORKSPACE)

    assert resolu == racine / "tests" / "repartition.py"
    assert resolu.name == "repartition.py"
    assert "repartition" not in str(resolu.parent), (
        "le dossier demande a ete cree — Z40a ne doit RIEN relocaliser"
    )


def test_le_perimetre_reste_etanche():
    """Le blocage lui-meme n'est pas touche : rien ne sort du workspace."""
    agent, racine = _agent(SubAgent)
    resolu = agent._resolve_path(CHEMIN_HORS_WORKSPACE)
    resolu.parent.mkdir(parents=True, exist_ok=True)
    resolu.resolve().relative_to((racine / "tests").resolve())  # ne leve pas


# ══════════════════════════════════════════════════════════════════════════
#  2. LE DEFAUT : la redirection doit atteindre l'appelant
# ══════════════════════════════════════════════════════════════════════════


def test_resolve_path_expose_la_redirection_qu_il_vient_de_faire():
    """Le WARNING part dans les logs ; il faut aussi que l'APPELANT puisse le
    lire. C'est la difference entre « c'est ecrit quelque part » et « celui qui
    decide le sait »."""
    agent, _ = _agent(SubAgent)
    agent._resolve_path(CHEMIN_HORS_WORKSPACE)

    redirection = getattr(agent, "_derniere_redirection_chemin", None)
    assert redirection is not None, (
        "la redirection n'est visible que dans les logs — l'appelant ne peut "
        "pas la lire"
    )
    demande, obtenu = redirection
    assert demande == CHEMIN_HORS_WORKSPACE
    assert obtenu.endswith("repartition.py")


def test_un_chemin_normal_ne_laisse_AUCUNE_redirection():
    """Le signal doit etre remis a zero a chaque resolution, sinon une
    redirection ancienne contaminerait une ecriture legitime."""
    agent, _ = _agent(SubAgent)
    agent._resolve_path(CHEMIN_HORS_WORKSPACE)          # pose le signal
    agent._resolve_path("module.py")                    # doit l'effacer

    assert getattr(agent, "_derniere_redirection_chemin", None) is None, (
        "une redirection ancienne survit a une resolution normale"
    )


def test_l_observation_d_ecriture_NOMME_le_chemin_reel():
    """LE test du lot.

    Avant Z40a, l'observation disait « Fichier écrit: ../repartition/... » —
    le chemin DEMANDE. Le CodeAgent a alors cherche ses fichiers la, pendant
    cinq iterations.
    """
    agent, _ = _agent()
    resolu = agent._resolve_path(CHEMIN_HORS_WORKSPACE)
    message = asyncio.run(
        agent._write_file_action(CHEMIN_HORS_WORKSPACE, "print('x')\n")
    )

    assert resolu.exists(), "le fichier n'a pas ete ecrit"
    assert str(resolu) in message, (
        "l'observation ne nomme pas le chemin REEL — le CodeAgent cherchera "
        f"ses fichiers au mauvais endroit.\nObservation : {message!r}"
    )
    assert CHEMIN_HORS_WORKSPACE in message, (
        "l'observation doit aussi rappeler ce qui avait ete DEMANDE, sinon le "
        "CodeAgent ne comprend pas ce qui s'est passe"
    )


def test_l_observation_d_ecriture_normale_ne_porte_AUCUNE_note():
    """Une ecriture legitime ne doit pas se retrouver decoree d'un
    avertissement : le bruit tue le signal."""
    agent, _ = _agent()
    message = asyncio.run(agent._write_file_action("module.py", "print('x')\n"))

    for marqueur in ("hors du workspace", "redirig"):
        assert marqueur not in message.lower(), (
            f"une ecriture normale porte la note « {marqueur} » : {message!r}"
        )


def test_l_observation_de_listage_NOMME_le_chemin_reel():
    """Meme defaut sur `list_files` : au run, `list_files ../repartition` a
    coute une iteration de plus."""
    agent, _ = _agent()
    message = asyncio.run(agent._list_files_action("../repartition"))

    assert "../repartition" in message
    assert any(m in message.lower() for m in ("hors du workspace", "redirig")), (
        f"le listage ne signale pas la redirection : {message!r}"
    )


# ══════════════════════════════════════════════════════════════════════════
#  3. Ce que le lot ne doit pas casser
# ══════════════════════════════════════════════════════════════════════════


def test_la_signature_de_resolve_path_est_inchangee():
    """`_resolve_path` a une vingtaine de sites d'appel. Changer son type de
    retour les toucherait tous — ce ne serait plus un patch minimal."""
    import inspect

    sig = inspect.signature(SubAgent._resolve_path)
    assert list(sig.parameters) == ["self", "file_path"]
    assert sig.return_annotation in (pathlib.Path, "Path")


def test_le_warning_de_blocage_est_toujours_emis():
    """La trace ne disparait pas : elle est doublee, pas remplacee."""
    from loguru import logger

    agent, _ = _agent(SubAgent)
    captures: list[str] = []
    poignee = logger.add(captures.append, level="WARNING", format="{message}")
    try:
        agent._resolve_path(CHEMIN_HORS_WORKSPACE)
    finally:
        logger.remove(poignee)

    assert any("BLOCKED path traversal" in c for c in captures), (
        f"le warning historique a disparu ; captures = {captures[-3:]}"
    )
