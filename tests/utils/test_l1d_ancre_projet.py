"""Lot L1d-1 - Lumena ne se perd plus : une ancre de projet par conversation.

Exigence de Charles du 15 septembre 2026 : « le PC devient ses mains, son corps, sans
se perdre : etre sur un projet, aller lire, auditer, analyser un projet a cote, et
retourner sur le bon projet et continuer, que ce soit le CodeAgent ou elle », de facon
AUTONOME (sans que l'utilisateur dise « passe sur X »).

Mesure du 15/09 (sonde jetable) : A travaille, audit de B, puis « continue »,
« reprends ou on en etait », « corrige le bug restant », « ajoute les tests »,
« continue sur la facturation » -> **B 5 fois sur 5** (biais « recemment actif »,
`register_project` en fin de toute tache du CodeAgent).

Regles :
- l'ancre (projet de travail de la conversation) prime sur toute devinette ;
- seul un projet DESIGNE explicitement (chemin ecrit, dossier nomme, nom exact d'un
  projet connu) l'emporte sur l'ancre, sans la deplacer ;
- une lecture/un audit n'inscrit pas le projet comme recent ; seul un travail qui
  ECRIT le fait ;
- la racine d'un projet se retrouve depuis n'importe quel fichier, dans ou hors du
  workspace.

Workspace et registre JETABLES (attributs du module rediriges).
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

import src.utils.project_registry as pr


@pytest.fixture
def monde(tmp_path, monkeypatch):
    ws = tmp_path / "workspace"
    a = ws / "2026-09-15" / "projet-atelier-facture"
    b = ws / "2026-09-15" / "projet-voisin-audit"
    for p in (a, b):
        p.mkdir(parents=True)
    (a / "app.py").write_text("print('A')\n", encoding="utf-8")
    (b / "lib.py").write_text("print('B')\n", encoding="utf-8")
    monkeypatch.setattr(pr, "WORKSPACE_DIR", ws)
    monkeypatch.setattr(pr, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(pr, "_REGISTRY_PATH", tmp_path / "project_registry.json")
    pr.register_project(a, description="application de facturation", slug=a.name)
    time.sleep(0.02)
    pr.register_project(b, description="audit du projet voisin", slug=b.name)  # B = plus recent
    return {"ws": ws, "a": a, "b": b, "tmp": tmp_path}


# ── 1. Racine d'un projet ────────────────────────────────────────────────────

def test_racine_projet_date_du_workspace(monde):
    m = monde
    (m["a"] / "src").mkdir()
    assert pr.project_root_for(m["a"] / "src" / "module.py") == m["a"]


def test_racine_projet_direct_du_workspace(monde):
    m = monde
    projet = m["ws"] / "mon-site"
    (projet / "css").mkdir(parents=True)
    assert pr.project_root_for(projet / "css" / "style.css") == projet


def test_dossier_de_date_n_est_pas_un_projet(monde):
    assert pr.project_root_for(monde["ws"] / "2026-09-15") is None


def test_racine_projet_hors_workspace_par_marqueur(monde):
    m = monde
    projet = m["tmp"] / "perso" / "mon-outil"
    (projet / "src").mkdir(parents=True)
    (projet / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    assert pr.project_root_for(projet / "src" / "cli.py") == projet


def test_racine_projet_enregistre_prioritaire(monde):
    m = monde
    projet = m["tmp"] / "ailleurs" / "client-x"
    (projet / "docs").mkdir(parents=True)
    pr.register_project(projet, description="client", slug="client-x")
    assert pr.project_root_for(projet / "docs" / "notes.md") == projet


def test_racine_hors_workspace_sans_marqueur_dossier_parent(monde):
    m = monde
    dossier = m["tmp"] / "divers"
    dossier.mkdir()
    assert pr.project_root_for(dossier / "note.txt") == dossier


@pytest.mark.parametrize("cible", [None, "", 12, "\x00chemin invalide"])
def test_racine_pour_l_ancre_ne_leve_jamais(monde, cible):
    assert pr.anchor_root_for_mutation(cible) is None


def test_racine_pour_l_ancre_d_un_projet_date(monde):
    m = monde
    assert pr.anchor_root_for_mutation(str(m["a"] / "app.py")) == m["a"]


def test_simple_dossier_ne_deplace_pas_l_ancre(monde):
    """ReAct memorise l'ancre avec allow_plain_dir=False : une note ecrite dans un
    dossier ordinaire n'est pas un changement de projet."""
    m = monde
    dossier = m["tmp"] / "divers"
    dossier.mkdir()
    assert pr.project_root_for(dossier / "note.txt", allow_plain_dir=False) is None


# ── 2. Designation explicite d'un projet ─────────────────────────────────────

@pytest.mark.parametrize("demande", [
    "continue", "reprends où on en était", "corrige le bug restant", "ajoute les tests",
    "continue sur la facturation",
])
def test_rien_n_est_designe_dans_une_suite_de_conversation(monde, demande):
    assert pr.designated_project(demande) is None


def test_nom_exact_d_un_projet_connu_est_designe(monde):
    assert pr.designated_project("audite projet-voisin-audit stp") == monde["b"]


def test_chemin_absolu_ecrit_est_designe(monde):
    m = monde
    assert pr.designated_project(f"analyse {m['b'] / 'lib.py'}") == m["b"]


def test_chemin_workspace_nomme_est_designe(monde):
    assert pr.designated_project("regarde workspace/2026-09-15/projet-voisin-audit") == monde["b"]


# ── 3. Scenario mesure : A -> audit de B -> retour, avec ancre ───────────────

@pytest.mark.parametrize("demande", [
    "continue", "reprends où on en était", "corrige le bug restant", "ajoute les tests",
    "continue sur la facturation",
])
def test_scenario_retour_sur_le_projet_ancre(monde, demande):
    m = monde
    r = pr.resolve_workspace(demande, context={"anchor_path": str(m["a"])}, allow_create=False)
    assert r.path == m["a"], (demande, r)
    assert r.source == "anchor"


def test_scenario_aller_lire_le_projet_voisin_sans_perdre_l_ancre(monde):
    m = monde
    r = pr.resolve_workspace("audite projet-voisin-audit", context={"anchor_path": str(m["a"])},
                             allow_create=False)
    assert r.path == m["b"] and r.source == "designated"


def test_ancre_disparue_retombe_sur_la_resolution_habituelle(monde):
    m = monde
    r = pr.resolve_workspace("audite projet-voisin-audit",
                             context={"anchor_path": str(m["tmp"] / "supprime")}, allow_create=False)
    assert r.path == m["b"] and r.source != "anchor"


def test_project_dir_du_contexte_reste_prioritaire(monde):
    """Caracterisation : un dossier deja resolu par l'appelant gagne toujours."""
    m = monde
    r = pr.resolve_workspace("continue", context={"project_dir": str(m["b"]),
                                                   "anchor_path": str(m["a"])}, allow_create=False)
    assert r.path == m["b"] and r.source == "context"


# ── 4. delegate_task : quel projet donner au CodeAgent ───────────────────────

def test_delegation_suite_de_travail_prend_l_ancre(monde):
    m = monde
    assert pr.choose_delegate_project(str(m["a"]), "corrige le bug restant") == str(m["a"])


def test_delegation_projet_designe_l_emporte_sur_l_ancre(monde):
    m = monde
    assert pr.choose_delegate_project(str(m["a"]), "audite projet-voisin-audit") == str(m["b"])


def test_delegation_sans_ancre_ni_designation(monde):
    assert pr.choose_delegate_project("", "corrige le bug restant") == ""


# ── 4bis. Lot L1d-2 : chemins contenant des espaces ─────────────────────────

@pytest.fixture
def projet_avec_espaces(monde, tmp_path):
    projet = tmp_path / "Mes Documents" / "site vitrine"
    (projet / "src").mkdir(parents=True)
    (projet / "index.html").write_text("<html></html>", encoding="utf-8")
    (projet / ".git").mkdir()
    return projet


def test_chemin_avec_espaces_entre_guillemets_est_designe(monde, projet_avec_espaces):
    assert pr.designated_project(f'audite "{projet_avec_espaces}" stp') == projet_avec_espaces


def test_chemin_avec_espaces_sans_guillemets_est_designe(monde, projet_avec_espaces):
    assert pr.designated_project(f"regarde {projet_avec_espaces} et dis-moi") == projet_avec_espaces


def test_fichier_avec_espaces_donne_la_racine_du_projet(monde, projet_avec_espaces):
    cible = projet_avec_espaces / "index.html"
    assert pr.designated_project(f"lis {cible}") == projet_avec_espaces


def test_phrase_sans_chemin_existant_ne_designe_rien(monde):
    assert pr.designated_project("regarde C:/Users/inexistant/le projet perdu") is None


# ── 4ter. Lot L1d-2 : l'ancre suit une ECRITURE du CodeAgent ────────────────

def test_ancre_apres_delegation_qui_a_ecrit(monde):
    m = monde
    assert pr.anchor_after_delegation(
        True, [str(m["a"] / "app.py")], str(m["a"])) == m["a"]


def test_pas_d_ancre_si_la_delegation_n_a_rien_ecrit(monde):
    """Un audit delegue (aucun fichier produit) ne deplace pas le projet principal."""
    m = monde
    assert pr.anchor_after_delegation(True, [], str(m["b"])) is None


def test_pas_d_ancre_si_la_delegation_a_echoue(monde):
    m = monde
    assert pr.anchor_after_delegation(False, [str(m["b"] / "lib.py")], str(m["b"])) is None


def test_ancre_de_delegation_depuis_le_fichier_si_le_dossier_manque(monde):
    m = monde
    assert pr.anchor_after_delegation(True, [str(m["a"] / "app.py")], "") == m["a"]


def test_ancre_de_delegation_ignore_un_dossier_ordinaire(monde):
    m = monde
    dossier = m["tmp"] / "notes libres"
    dossier.mkdir()
    fichier = dossier / "memo.txt"
    fichier.write_text("x", encoding="utf-8")
    assert pr.anchor_after_delegation(True, [str(fichier)], str(dossier)) is None


# ── 4quater. Lot L1d-2 : le branchement dans delegate_task ──────────────────

class _IdentiteFausse:
    def __init__(self):
        self.appels = []

    def remember_code_context(self, channel_key, workspace_path, project_slug=None):
        self.appels.append((channel_key, workspace_path, project_slug))


def _contexte_delegation(identite):
    from types import SimpleNamespace
    from src.runtime.context import RuntimeContext
    requete = RuntimeContext.build(
        channel="web", client="test", request_id="req-l1d2", conversation_id="conv-l1d2",
        message_id="msg", workspace_policy=None, task_id=None, client_caps=None,
        workspace_path=None, active_file_path=None, open_files=None,
        resolved_workspace=None, resolved_date=None, resolution_reason=None)
    return SimpleNamespace(lumena=SimpleNamespace(_identity_svc=identite, runtime_ctx=requete),
                           runtime_ctx=requete)


def test_delegation_qui_ecrit_pose_l_ancre_du_canal(monde):
    from src.reasoning.handlers.agents import _pose_ancre_apres_delegation
    m = monde
    identite = _IdentiteFausse()
    _pose_ancre_apres_delegation(_contexte_delegation(identite), True,
                                 [str(m["a"] / "app.py")], [], str(m["a"]))
    assert len(identite.appels) == 1
    assert Path(identite.appels[0][1]) == m["a"] and identite.appels[0][2] == m["a"].name


def test_delegation_sans_ecriture_ne_pose_pas_d_ancre(monde):
    from src.reasoning.handlers.agents import _pose_ancre_apres_delegation
    m = monde
    identite = _IdentiteFausse()
    _pose_ancre_apres_delegation(_contexte_delegation(identite), True, [], [], str(m["b"]))
    assert identite.appels == []


def test_fichiers_annonces_mais_absents_ne_posent_pas_d_ancre(monde):
    """Le CodeAgent annonce des fichiers qui n'existent pas : rien n'a ete ecrit."""
    from src.reasoning.handlers.agents import _pose_ancre_apres_delegation
    m = monde
    identite = _IdentiteFausse()
    fantome = str(m["b"] / "jamais_ecrit.py")
    _pose_ancre_apres_delegation(_contexte_delegation(identite), True, [fantome], [fantome],
                                 str(m["b"]))
    assert identite.appels == []


def test_pose_ancre_ne_leve_jamais(monde):
    from types import SimpleNamespace
    from src.reasoning.handlers.agents import _pose_ancre_apres_delegation
    _pose_ancre_apres_delegation(SimpleNamespace(), True, ["x"], [], "")  # aucun service


# ── 5. CodeAgent : seul un travail qui ECRIT inscrit le projet ───────────────

def _agent(root: Path, edits: list[str]):
    from src.agents.sub_agent import CodeAgent
    agent = CodeAgent.__new__(CodeAgent)
    agent._task_workspace_root = root
    agent._session_memory = {"files_read": {}, "errors_seen": [], "edits_done": edits,
                             "grep_zero_results": {}}
    return agent


def test_codeagent_audit_sans_ecriture_n_inscrit_pas_le_projet(monde, monkeypatch):
    m = monde
    appels = []
    monkeypatch.setattr(pr, "register_project", lambda *a, **k: appels.append((a, k)))
    _agent(m["b"], [])._register_task_project("audite le projet voisin")
    assert appels == []


def test_codeagent_travail_ecrit_inscrit_le_projet(monde, monkeypatch):
    m = monde
    appels = []
    monkeypatch.setattr(pr, "register_project", lambda *a, **k: appels.append((a, k)))
    _agent(m["a"], ["app.py: edit"])._register_task_project("corrige le bug")
    assert len(appels) == 1 and Path(appels[0][0][0]) == m["a"]
