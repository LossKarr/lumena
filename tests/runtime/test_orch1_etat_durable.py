"""Lot ORCH-1 - l'etat des taches survit a une coupure, et le dit quand il echoue.

**Defaut trouve sur la machine de Charles le 23 septembre 2026.**
`data/task_orchestrator_state.json` : **12 270 771 octets, uniquement des zeros**.
Horodate 21:39:20, soit la seconde ou sa session s'est arretee. L'historique de
199 racines de missions est **perdu**, sans aucune sauvegarde exploitable.

--- Ce que la mesure a corrige dans mon premier diagnostic ---

J'avais annonce que Lumena ecrivait « directement par-dessus l'ancien ». **C'etait
faux** : `_persist_locked` fait bien `tmp.write_text()` puis `tmp.replace()`, le
motif atomique correct.

Le defaut est plus fin. `write_text()` **n'appelle pas `fsync`** : le contenu reste
dans le cache disque. Le `replace` est atomique au niveau du NOM - NTFS le
journalise - mais les DONNEES du fichier temporaire n'ont jamais atteint le disque.
Apres coupure, le nom pointe vers un fichier de la bonne taille dont le contenu n'a
jamais ete ecrit.

**L'atomicite etait la, la durabilite non.** C'est exactement la signature observee.

--- Et un second defaut, qui a rendu le premier invisible ---

`replace` ECRASE la version precedente : aucun repli. Et `_load_from_disk` avale
l'exception (`except Exception: ... return`), si bien qu'un etat illisible fait
repartir Lumena **vide et en silence**. Charles ne l'a su que parce que des tests
lisaient ce corpus.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.runtime.task_orchestrator import TaskOrchestrator


def _orchestrateur(chemin: Path) -> TaskOrchestrator:
    orch = TaskOrchestrator()
    orch._persistence_path = chemin
    return orch


def _avec_une_tache(chemin: Path) -> TaskOrchestrator:
    orch = _orchestrateur(chemin)
    orch.start_task(conversation_id="conv", channel="web", message_preview="travail",
                    metadata={"kind": "mission"}, task_id="task_1")
    return orch


# ── 1. Durabilite : les donnees atteignent le disque AVANT le rename ────────

def test_l_ecriture_force_les_donnees_sur_le_disque(tmp_path, monkeypatch):
    """Sans `fsync`, le rename publie un fichier dont le contenu est en cache.

    C'est ce qui a produit 12,3 Mo de zeros apres une coupure.
    """
    synchronises = []
    vrai_fsync = __import__("os").fsync

    def fsync_espion(fd):
        synchronises.append(fd)
        return vrai_fsync(fd)

    monkeypatch.setattr("os.fsync", fsync_espion)
    _avec_une_tache(tmp_path / "etat.json")

    assert synchronises, "aucun fsync : les donnees peuvent rester en cache disque"


def test_le_contenu_ecrit_reste_relisible(tmp_path):
    """Non-regression du chemin nominal : ce lot ne change pas le format."""
    _avec_une_tache(tmp_path / "etat.json")
    charge = json.loads((tmp_path / "etat.json").read_text(encoding="utf-8"))
    assert charge["schema_version"] == 1
    assert [t["task_id"] for t in charge["tasks"]] == ["task_1"]


# ── 2. Un repli, pour qu'une corruption ne coute pas tout ───────────────────

def test_la_version_precedente_est_conservee(tmp_path):
    chemin = tmp_path / "etat.json"
    orch = _avec_une_tache(chemin)
    orch.start_task(conversation_id="conv", channel="web", message_preview="second",
                    metadata={"kind": "mission"}, task_id="task_2")

    repli = chemin.with_suffix(chemin.suffix + ".bak")
    assert repli.exists(), "aucun repli : une corruption efface tout"
    precedent = json.loads(repli.read_text(encoding="utf-8"))
    assert [t["task_id"] for t in precedent["tasks"]] == ["task_1"]


def test_le_premier_enregistrement_ne_fabrique_pas_de_repli_vide(tmp_path):
    """Un `.bak` vide serait pire que pas de `.bak` : il ecraserait un espoir."""
    chemin = tmp_path / "etat.json"
    _avec_une_tache(chemin)
    repli = chemin.with_suffix(chemin.suffix + ".bak")
    if repli.exists():
        assert json.loads(repli.read_text(encoding="utf-8"))["tasks"] == []


# ── 3. Un etat illisible ne fait plus repartir de zero EN SILENCE ───────────

def test_un_etat_corrompu_est_recupere_depuis_le_repli(tmp_path):
    """Le cas reel : 12,3 Mo de zeros. Le repli doit reprendre la main."""
    chemin = tmp_path / "etat.json"
    orch = _avec_une_tache(chemin)
    orch.start_task(conversation_id="conv", channel="web", message_preview="second",
                    metadata={"kind": "mission"}, task_id="task_2")

    chemin.write_bytes(b"\x00" * 4096)  # exactement la corruption observee

    repris = _orchestrateur(chemin)
    repris._load_from_disk()

    assert "task_1" in repris._tasks, (
        "l'etat corrompu a fait repartir de zero alors qu'un repli existait"
    )


def test_un_etat_corrompu_SANS_repli_le_dit_clairement(tmp_path, caplog):
    """Sans repli il n'y a rien a sauver - mais le silence est interdit."""
    chemin = tmp_path / "etat.json"
    chemin.write_bytes(b"\x00" * 4096)

    orch = _orchestrateur(chemin)
    with caplog.at_level("WARNING"):
        orch._load_from_disk()

    assert orch._persistence_last_error, "l'erreur n'est meme pas retenue"
    trace = " ".join(r.getMessage() for r in caplog.records) + str(orch._persistence_last_error)
    assert "task_orchestrator" in trace.lower() or "etat" in trace.lower(), (
        "un etat illisible doit laisser une trace lisible, pas un redemarrage muet"
    )


def test_un_etat_valide_ne_touche_jamais_au_repli(tmp_path):
    """Non-regression : le repli ne sert QUE quand le fichier principal est illisible."""
    chemin = tmp_path / "etat.json"
    orch = _avec_une_tache(chemin)
    orch.start_task(conversation_id="conv", channel="web", message_preview="second",
                    metadata={"kind": "mission"}, task_id="task_2")

    repris = _orchestrateur(chemin)
    repris._load_from_disk()
    assert {"task_1", "task_2"} <= set(repris._tasks)


@pytest.mark.parametrize("contenu", [b"", b"{", b"\x00" * 16, b"pas du json"])
def test_aucune_forme_de_corruption_ne_leve(tmp_path, contenu):
    chemin = tmp_path / "etat.json"
    chemin.write_bytes(contenu)
    _orchestrateur(chemin)._load_from_disk()  # ne doit jamais lever
