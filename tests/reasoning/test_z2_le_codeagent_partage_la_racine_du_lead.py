"""LOT Z2 — le CodeAgent délégué par le lead partage la racine de la mission.

Run réel « Sillage » (2026-08-15, task_6f1adf49…) :

    04:20:40  delegate_task            ← le lead obéit au garde Z1b
    04:21:24  [CodeAgent] iter=1/50
              [CodeAgent] BLOCKED path traversal '../sillage/sillage.py'   ×15
    04:28:11  workspace/missions/task_6f1adf49…/  ← créé HUIT MINUTES plus tard

Z1b a ouvert un ordre d'événements qui n'existait pas : le lead délègue le code
AVANT tout `delegate_and_wait`, donc avant que rien n'ait créé `missions/<id>`.
`_mission_codeagent_scope` exigeait `is_dir()` et renvoyait None ; le CodeAgent
partait alors dans le workspace daté générique pendant que le lead travaillait
dans le dossier de mission. Deux racines, quinze refus, CodeAgent stérile.

Ces tests fixent les deux moitiés : la racine est désormais partagée pour une
délégation de CODE, et l'inertie voulue par P2a survit pour tout le reste.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from src.reasoning.handlers.agents import (
    _CODE_AGENT_KINDS,
    _mission_codeagent_scope,
)


class _Guardrails:
    def __init__(self, root: Path) -> None:
        self._root = root

    def _workspace_root(self) -> Path:
        return self._root


def _ctx(root: Path, subdir: str, *, is_mission: bool = True, owned=None):
    """Contexte minimal : `_mission_codeagent_scope` ne lit rien d'autre."""
    return SimpleNamespace(
        is_mission_run=is_mission,
        file_guardrails=_Guardrails(root),
        mission_workspace_subdir=lambda: subdir,
        mission_allowed_files_set=lambda: (frozenset(owned) if owned else None),
    )


# ── La moitié réparée : la racine est partagée ────────────────────────────────


def test_le_dossier_absent_ne_renvoie_plus_none_pour_du_code(tmp_path):
    """Le cas exact de Sillage : dossier pas encore là, délégation de code."""
    path, _ = _mission_codeagent_scope(
        _ctx(tmp_path, "missions/task_abc"), create_if_missing=True
    )
    assert path is not None
    assert Path(path) == (tmp_path / "missions" / "task_abc").resolve()


def test_le_dossier_est_reellement_cree_sur_le_disque(tmp_path):
    """Renvoyer un chemin ne suffit pas : le CodeAgent doit pouvoir y écrire."""
    cible = tmp_path / "missions" / "task_abc"
    assert not cible.exists()
    _mission_codeagent_scope(_ctx(tmp_path, "missions/task_abc"), create_if_missing=True)
    assert cible.is_dir()


def test_les_parents_manquants_sont_crees(tmp_path):
    """`missions/` lui-même n'existe pas au premier run d'une instance neuve."""
    assert not (tmp_path / "missions").exists()
    path, _ = _mission_codeagent_scope(
        _ctx(tmp_path, "missions/task_abc"), create_if_missing=True
    )
    assert path is not None and Path(path).is_dir()


def test_un_dossier_deja_present_est_reutilise_tel_quel(tmp_path):
    """Le chemin nominal (après delegate_and_wait) ne change pas."""
    cible = tmp_path / "missions" / "task_abc"
    cible.mkdir(parents=True)
    (cible / "temoin.txt").write_text("ne pas perdre", encoding="utf-8")
    path, _ = _mission_codeagent_scope(
        _ctx(tmp_path, "missions/task_abc"), create_if_missing=True
    )
    assert Path(path) == cible.resolve()
    assert (cible / "temoin.txt").read_text(encoding="utf-8") == "ne pas perdre"


def test_le_perimetre_du_worker_est_toujours_rendu(tmp_path):
    """Créer le dossier ne doit pas faire perdre les `allowed_files`."""
    _, owned = _mission_codeagent_scope(
        _ctx(tmp_path, "missions/task_abc", owned={"b.py", "a.py"}),
        create_if_missing=True,
    )
    assert owned == ["a.py", "b.py"]


def test_le_lead_na_pas_de_perimetre_mais_bien_une_racine(tmp_path):
    """Le lead possède l'intégration : pas d'`allowed_files`, mais une racine."""
    path, owned = _mission_codeagent_scope(
        _ctx(tmp_path, "missions/task_abc"), create_if_missing=True
    )
    assert path is not None
    assert owned is None


# ── La moitié préservée : l'inertie de P2a ────────────────────────────────────


def test_sans_creation_le_dossier_absent_renvoie_toujours_none(tmp_path):
    """Comportement historique inchangé quand on ne demande pas la création."""
    path, owned = _mission_codeagent_scope(
        _ctx(tmp_path, "missions/task_abc"), create_if_missing=False
    )
    assert (path, owned) == (None, None)


def test_sans_creation_rien_napparait_sur_le_disque(tmp_path):
    """Une mission d'effets ne doit laisser aucune trace de dossier."""
    _mission_codeagent_scope(_ctx(tmp_path, "missions/task_abc"), create_if_missing=False)
    assert not (tmp_path / "missions").exists()


def test_le_defaut_est_labstention(tmp_path):
    """Un appelant qui ne se prononce pas garde l'ancien comportement."""
    path, _ = _mission_codeagent_scope(_ctx(tmp_path, "missions/task_abc"))
    assert path is None
    assert not (tmp_path / "missions").exists()


def test_hors_mission_rien_ne_se_cree_meme_en_demandant(tmp_path):
    """Le CodeAgent du chat reste strictement intouché."""
    path, _ = _mission_codeagent_scope(
        _ctx(tmp_path, "missions/task_abc", is_mission=False), create_if_missing=True
    )
    assert path is None
    assert not (tmp_path / "missions").exists()


def test_un_subdir_vide_ne_cree_rien(tmp_path):
    """Pas de `mission_workspace` → pas de dossier fabriqué au hasard."""
    path, _ = _mission_codeagent_scope(_ctx(tmp_path, ""), create_if_missing=True)
    assert path is None
    assert list(tmp_path.iterdir()) == []


def test_sans_guardrails_aucune_creation(tmp_path):
    """Sans racine de workspace connue, on s'abstient plutôt que de deviner."""
    ctx = _ctx(tmp_path, "missions/task_abc")
    ctx.file_guardrails = None
    path, _ = _mission_codeagent_scope(ctx, create_if_missing=True)
    assert path is None


# ── Anti-traversal : la création ne doit pas devenir une brèche ───────────────


@pytest.mark.parametrize(
    "subdir",
    ["", "../evade", "/etc/lumena", "C:/Windows/Temp", "missions/../../evade"],
)
def test_un_subdir_hostile_ne_cree_jamais_rien(tmp_path, subdir):
    """`mission_workspace_subdir()` filtre déjà, mais la création ne relâche rien :
    si un chemin douteux arrivait ici, il ne doit pas produire de dossier hors
    du workspace."""
    _mission_codeagent_scope(_ctx(tmp_path, subdir), create_if_missing=True)
    for cree in tmp_path.rglob("*"):
        assert tmp_path.resolve() in cree.resolve().parents


def test_la_creation_reste_sous_la_racine_du_workspace(tmp_path):
    """Le dossier créé est toujours un descendant du workspace."""
    path, _ = _mission_codeagent_scope(
        _ctx(tmp_path, "missions/task_abc"), create_if_missing=True
    )
    assert tmp_path.resolve() in Path(path).parents


def test_un_echec_de_creation_retombe_sur_none(tmp_path, monkeypatch):
    """Disque plein, droits refusés : on s'abstient, on ne lève pas."""

    def _refuse(self, *a, **k):
        raise PermissionError("refusé")

    monkeypatch.setattr(Path, "mkdir", _refuse)
    path, owned = _mission_codeagent_scope(
        _ctx(tmp_path, "missions/task_abc"), create_if_missing=True
    )
    assert (path, owned) == (None, None)


# ── Le choix de l'appelant : qui a droit à la création ────────────────────────


@pytest.mark.parametrize("kind", ["code", "debug", "refactor"])
def test_les_types_qui_ecrivent_du_code_ont_droit_a_la_racine(kind):
    """Z2 réutilise la notion « types qui codent » déjà portée par le module —
    pas une seconde liste qui divergerait de la première."""
    assert kind in _CODE_AGENT_KINDS


@pytest.mark.parametrize("kind", ["research", "planner", "general", "file"])
def test_les_types_deffets_nont_pas_droit_a_la_creation(kind):
    """P2a : une mission d'effets (mail, PDF, recherche) ne crée pas de dossier."""
    assert kind not in _CODE_AGENT_KINDS
