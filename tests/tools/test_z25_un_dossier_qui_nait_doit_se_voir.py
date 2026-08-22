"""LOT Z25 — un dossier qui naît doit se voir.

Cause racine du README égaré du run « jeu 3D monde ouvert » (2026-08-19).

La mission écrit `jeu-3d-monde-ouvert/README.md`. En mission, un chemin relatif
résout sous `missions/<id>/` : un arbre NEUF est né, portant le nom du livrable,
et le README n'a jamais rejoint `workspace/jeu-3d-monde-ouvert/`.

Diagnostic corrigé en cours d'audit — il ne s'agissait PAS d'un manque
d'information : `write_file_strict` affichait déjà « 📍 Chemin complet ». Le
défaut est plus fin : **rien ne signalait que ce dossier venait d'être inventé**.
Un chemin plausible, aucun signal de nouveauté, et le modèle croit avoir écrit
dans le livrable.

Deux notes factuelles, jamais bloquantes :
  1. le dossier parent n'existait pas → il vient d'être créé ;
  2. un dossier du MÊME NOM existe déjà à la racine du workspace, là où vivent
     les livrables — c'est très probablement l'endroit voulu.

La note 2 est celle qui aurait sauvé le README : `workspace/jeu-3d-monde-ouvert/`
existait déjà quand le fantôme est né.

Z24 signale le symptôme au FINAL (« écrit après la publication »). Z25 s'attaque
à la cause, à l'instant où elle se produit.
"""

import tempfile
from pathlib import Path

import pytest

from src.tools.file_guardrails import WorkspaceFileGuardrails


# ── Banc : la disposition exacte du run 3D ───────────────────────────────────


@pytest.fixture()
def garde(tmp_path):
    """Un workspace où le livrable `jeu-3d-monde-ouvert` existe DÉJÀ."""
    ws = tmp_path / "workspace"
    (ws / "jeu-3d-monde-ouvert").mkdir(parents=True)
    g = WorkspaceFileGuardrails(tmp_path)
    # En production `_looks_like_project_root()` est vrai et renvoie WORKSPACE_DIR.
    g._workspace_root = lambda: ws
    g._ws = ws
    return g


def _fantome(g):
    return g._ws / "missions" / "task_281f" / "jeu-3d-monde-ouvert" / "README.md"


# ── Le cas mesuré ────────────────────────────────────────────────────────────


def test_le_readme_du_run_3d_declenche_l_alerte(garde):
    """LE lot."""
    note = garde._new_directory_notice(_fantome(garde), False)
    assert "existe DEJA ici" in note
    assert "jeu-3d-monde-ouvert" in note


def test_l_alerte_nomme_l_endroit_probablement_voulu(garde):
    """Signaler sans dire OÙ est le vrai dossier ne sert à rien."""
    note = garde._new_directory_notice(_fantome(garde), False)
    assert str(garde._ws / "jeu-3d-monde-ouvert") in note


def test_l_alerte_dit_la_consequence_concrete(garde):
    """« ce que tu ecris n'ira pas dans le premier » — c'est le fait qui compte,
    pas la remarque sur l'arborescence."""
    note = garde._new_directory_notice(_fantome(garde), False)
    assert "n'ira pas dans le premier" in note


# ── La note de naissance, seule ──────────────────────────────────────────────


def test_un_dossier_neuf_se_signale_meme_sans_homonyme(garde):
    note = garde._new_directory_notice(garde._ws / "projet-neuf" / "a.md", False)
    assert "Dossier CREE" in note
    assert "existe DEJA" not in note


def test_un_dossier_deja_la_ne_dit_rien(garde):
    """Le cas ordinaire — 99 % des écritures. Aucun bruit."""
    assert garde._new_directory_notice(_fantome(garde), True) == ""


def test_le_meme_dossier_ne_se_signale_pas_lui_meme(garde):
    """Écrire dans `workspace/jeu-3d-monde-ouvert/` lui-même n'est pas un
    doublon : sans cette garde, chaque livrable s'auto-dénoncerait."""
    cible = garde._ws / "jeu-3d-monde-ouvert" / "index.html"
    note = garde._new_directory_notice(cible, False)
    assert "existe DEJA" not in note


def test_la_note_ne_leve_jamais(garde):
    """Un garde-fou d'écriture ne doit jamais faire échouer l'écriture."""
    garde._workspace_root = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    assert garde._new_directory_notice(_fantome(garde), False) != ""  # note 1 seule
    assert garde._new_directory_notice(None, False) == ""


# ── Bout en bout, par l'écriture réelle ──────────────────────────────────────


def test_l_ecriture_reelle_porte_la_note(tmp_path):
    """Le chemin complet ET le signal de naissance dans la même observation."""
    g = WorkspaceFileGuardrails(tmp_path)
    r = g.write_file_strict(
        path="jeu-3d-monde-ouvert/README.md",
        content="# Instructions",
        mission_workspace_subdir="missions/task_281f",
    )
    assert r.success is True
    assert "Chemin complet" in r.message
    assert "Dossier CREE" in r.message


def test_la_seconde_ecriture_est_silencieuse(tmp_path):
    """Le dossier existe désormais : plus aucune note. Une note répétée à chaque
    écriture perdrait toute valeur de signal."""
    g = WorkspaceFileGuardrails(tmp_path)
    kw = dict(path="jeu-3d-monde-ouvert/README.md",
              mission_workspace_subdir="missions/task_281f")
    g.write_file_strict(content="# v1", **kw)
    r2 = g.write_file_strict(content="# v2", **kw)
    assert "Dossier CREE" not in r2.message


def test_l_ecriture_reussit_toujours(tmp_path):
    """Z25 informe, il ne bloque rien — le fichier est bien sur le disque."""
    g = WorkspaceFileGuardrails(tmp_path)
    r = g.write_file_strict(
        path="neuf/sous/dossier/a.txt", content="contenu",
        mission_workspace_subdir="missions/t1",
    )
    assert r.success is True
    assert Path(r.file_path).read_text(encoding="utf-8") == "contenu"


# ── Le code porte sa raison ──────────────────────────────────────────────────


def test_la_raison_du_lot_est_datee_dans_le_code():
    src = Path("src/tools/file_guardrails.py").read_text(encoding="utf-8")
    entete = src[src.index("LOT Z25 — un dossier qui NAIT"):][:900]
    assert "jeu 3D" in entete
    assert "missions/<id>/" in entete


def test_la_capture_precede_le_mkdir():
    """Capturée après, l'existence du parent serait toujours vraie — le lot
    entier deviendrait inerte sans qu'aucun test ne le voie."""
    src = Path("src/tools/file_guardrails.py").read_text(encoding="utf-8")
    i_cap = src.index("_parent_existed = target.parent.exists()")
    i_mk = src.index("target.parent.mkdir(parents=True, exist_ok=True)\n        target.write_text")
    assert i_cap < i_mk
