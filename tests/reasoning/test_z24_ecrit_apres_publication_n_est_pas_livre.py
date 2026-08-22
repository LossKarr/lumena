"""LOT Z24 — ce qui s'écrit après la publication n'est pas livré.

Run « jeu 3D monde ouvert » (2026-08-19). Les deux faits sont persistés CÔTE À
CÔTE dans le même enregistrement de tâche, et personne ne les croise :

    published_files : ['CONTRAT.md','contract.json','index.html','script.js','style.css']
    ledger          : write_file → 'jeu-3d-monde-ouvert/README.md'
                      success=True, itération 26   ← APRÈS le publish
    terminal_reason : completed — « toutes les portes de clôture ont autorisé
                      le résultat »

L'objectif exigeait « index.html, styles, scripts, **instructions** ». Le README
a bien été écrit. Il n'a jamais rejoint le livrable. **Aucune porte n'a rien vu,
et le FINAL n'en a pas dit un mot** — ce n'était pas un mensonge (elle n'a jamais
affirmé l'avoir livré), c'était un silence.

Mesure préalable sur le corpus réel : 26 missions publiées, la règle lève 1 fois
— exactement ce README, zéro faux positif. Échantillon mince (les projections
persistées ne gardent que 5 entrées), d'où le choix de tourner dans le run sur le
ledger complet plutôt que sur les checkpoints tronqués.

Publier fige un instantané : tout ce qui s'écrit APRÈS est, par construction,
hors du livrable tant qu'on ne republie pas. Aucune lecture d'intention, aucun
regex sur l'objectif — 2.13.A dit que cette course est perdue.
"""

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.reasoning.final_guards import apply_mission_truth_lock
from src.reasoning.react import ReActLoop
from src.runtime.execution_ledger import ExecutionLedger

_WS = "workspace/jeu-3d-monde-ouvert"


# ── Bancs ────────────────────────────────────────────────────────────────────


def _ledger_du_run_3d() -> ExecutionLedger:
    """Le ledger tel qu'il était à l'itération 26."""
    led = ExecutionLedger()
    led.append(iteration=20, action="write_file",
               target="jeu-3d-monde-ouvert/index.html", success=True)
    led.append(iteration=25, action="publish_mission_workspace",
               target="jeu-3d-monde-ouvert", success=True, proof="5 fichier(s)")
    led.append(iteration=26, action="write_file",
               target="jeu-3d-monde-ouvert/README.md", success=True)
    return led


def _agent(ledger=None, *, mission=True, ws=_WS):
    orch = SimpleNamespace(
        get_task=lambda _tid: {"metadata": {"published_workspace": ws}}
    )
    return SimpleNamespace(
        _is_mission_run=mission,
        execution_ledger=ledger if ledger is not None else _ledger_du_run_3d(),
        task_orchestrator=orch,
        task_id="task_281f8cef",
    )


def _manquants(agent):
    return ReActLoop._mission_unpublished_writes(agent)


# ── Le ledger : le fait brut ─────────────────────────────────────────────────


def test_le_readme_du_run_3d_est_vu():
    """LE cas mesuré."""
    ecrits = _ledger_du_run_3d().writes_after_last_publish()
    assert [e.target for e in ecrits] == ["jeu-3d-monde-ouvert/README.md"]


def test_ce_qui_precede_la_publication_est_livre():
    """index.html a été écrit AVANT le publish : il est dans le livrable."""
    cibles = [e.target for e in _ledger_du_run_3d().writes_after_last_publish()]
    assert "jeu-3d-monde-ouvert/index.html" not in cibles


def test_sans_publication_le_garde_est_inerte():
    """Une mission qui n'a jamais publié n'a rien 'hors livrable'."""
    led = ExecutionLedger()
    led.append(iteration=1, action="write_file", target="a.md", success=True)
    assert led.writes_after_last_publish() == []


def test_republier_referme_l_ecart():
    """Le fait est recalculé sur la DERNIÈRE publication, pas la première —
    sinon republier ne servirait à rien et la redirection serait un piège."""
    led = _ledger_du_run_3d()
    led.append(iteration=27, action="publish_mission_workspace",
               target="jeu-3d-monde-ouvert", success=True)
    assert led.writes_after_last_publish() == []


def test_une_ecriture_ratee_ne_compte_pas():
    led = _ledger_du_run_3d()
    led.append(iteration=28, action="write_file", target="rate.md", success=False)
    assert "rate.md" not in [e.target for e in led.writes_after_last_publish()]


def test_une_publication_ratee_ne_fige_rien():
    led = ExecutionLedger()
    led.append(iteration=1, action="publish_mission_workspace",
               target="x", success=False)
    led.append(iteration=2, action="write_file", target="a.md", success=True)
    assert led.writes_after_last_publish() == []


@pytest.mark.parametrize("tool", ["read_file", "browser_click", "run_command",
                                  "list_directory", "serve_website"])
def test_seules_les_ecritures_comptent(tool):
    led = _ledger_du_run_3d()
    led.append(iteration=27, action=tool, target="bidule", success=True)
    assert [e.action for e in led.writes_after_last_publish()] == ["write_file"]


# ── Le helper : les bornes ───────────────────────────────────────────────────


def test_le_helper_signale_le_readme():
    assert _manquants(_agent()) == ["README.md"]


def test_ecrire_DANS_le_dossier_publie_ne_compte_pas():
    """Doctrine DISK-GROUNDED (2.12.C) : le fichier est sur le disque à
    l'endroit publié, donc consultable — le signaler serait un FAUX."""
    led = _ledger_du_run_3d()
    led.append(iteration=27, action="write_file",
               target="workspace/jeu-3d-monde-ouvert/style.css", success=True)
    assert _manquants(_agent(led)) == ["README.md"]


def test_hors_mission_le_helper_est_inerte():
    assert _manquants(_agent(mission=False)) == []


def test_les_doublons_sont_ecrases_et_la_liste_plafonnee():
    led = _ledger_du_run_3d()
    for n in range(15):
        led.append(iteration=27 + n, action="write_file",
                   target=f"notes/f{n}.md", success=True)
    led.append(iteration=99, action="write_file",
               target="autre/README.md", success=True)
    out = _manquants(_agent(led))
    assert len(out) <= 8
    assert len(out) == len(set(out))


def test_le_helper_ne_leve_jamais():
    """Un garde-fou ne doit pas tuer la boucle ReAct."""
    assert _manquants(SimpleNamespace(_is_mission_run=True)) == []
    casse = _agent()
    casse.task_orchestrator = SimpleNamespace(
        get_task=lambda _t: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    # L'orchestrateur casse ne doit pas empêcher de dire le fait.
    assert casse and _manquants(casse) == ["README.md"]


# ── La redirection : un tir, au bon moment ───────────────────────────────────


def _agent_nudge(manquants=("README.md",)):
    a = SimpleNamespace(
        _z24_nudged=False,
        _pending_loop_guidance=None,
        _mission_unpublished_writes=lambda: list(manquants),
    )
    return a


def test_la_redirection_nomme_le_fichier_et_la_sortie():
    a = _agent_nudge()
    ReActLoop._nudge_unpublished_writes(a)
    assert "README.md" in a._pending_loop_guidance
    assert "publish_mission_workspace" in a._pending_loop_guidance


def test_la_redirection_interdit_de_conclure_en_laissant_croire():
    """C'est le défaut EXACT du run 3D : un silence, pas un mensonge."""
    a = _agent_nudge()
    ReActLoop._nudge_unpublished_writes(a)
    assert "dis-le explicitement" in a._pending_loop_guidance
    assert "laissant croire" in a._pending_loop_guidance


def test_un_seul_tir():
    """Jamais de harcèlement en boucle — même discipline que P2b."""
    a = _agent_nudge()
    ReActLoop._nudge_unpublished_writes(a)
    a._pending_loop_guidance = None
    ReActLoop._nudge_unpublished_writes(a)
    assert a._pending_loop_guidance is None


def test_rien_a_signaler_rien_a_dire():
    a = _agent_nudge(manquants=())
    ReActLoop._nudge_unpublished_writes(a)
    assert a._pending_loop_guidance is None
    assert a._z24_nudged is False


def test_la_redirection_ne_leve_jamais():
    casse = SimpleNamespace(
        _z24_nudged=False,
        _mission_unpublished_writes=lambda: (_ for _ in ()).throw(ValueError("x")),
    )
    ReActLoop._nudge_unpublished_writes(casse)  # ne doit pas lever


# ── Le FINAL : le filet d'honnêteté ──────────────────────────────────────────


def test_le_final_porte_le_fait():
    texte, info = apply_mission_truth_lock(
        "Le livrable est publié dans workspace/jeu-3d-monde-ouvert/.",
        has_green_test=True, has_published=True,
        unpublished_writes=["README.md"],
    )
    assert "hors du livrable" in texte
    assert "README.md" in texte
    assert info["unpublished_writes_note"] is True


def test_sans_manquants_le_final_est_intact():
    """Inerte par défaut : les appelants existants ne changent pas."""
    src = "Le livrable est publié."
    texte, info = apply_mission_truth_lock(
        src, has_green_test=True, has_published=True,
    )
    assert texte == src
    assert info["changed"] is False


@pytest.mark.parametrize("vide", [None, [], [""], ["   "]])
def test_une_liste_vide_ne_bannerise_pas(vide):
    src = "Le livrable est publié."
    texte, _ = apply_mission_truth_lock(
        src, has_green_test=True, has_published=True, unpublished_writes=vide,
    )
    assert texte == src


def test_pas_de_double_banniere():
    """Le verrou s'applique aussi au point d'étranglement d'émission : repasser
    un texte déjà banni ne doit pas empiler."""
    texte, _ = apply_mission_truth_lock(
        "Le livrable est publié.", has_green_test=True, has_published=True,
        unpublished_writes=["README.md"],
    )
    encore, info = apply_mission_truth_lock(
        texte, has_green_test=True, has_published=True,
        unpublished_writes=["README.md"],
    )
    assert encore == texte
    assert info.get("already_locked") is True


def test_le_fait_cohabite_avec_les_autres_bannieres():
    """Il s'ajoute, il ne masque pas — un run peut cumuler les manques."""
    texte, _ = apply_mission_truth_lock(
        "Le jeu est publié et fonctionne.",
        has_green_test=True, has_published=True,
        web_deliverable=True, interaction_proven=False, objective_is_game=True,
        unpublished_writes=["README.md"],
    )
    assert "hors du livrable" in texte
    assert "NON prouvée" in texte


# ── Le branchement ───────────────────────────────────────────────────────────


_SRC = Path("src/reasoning/react.py").read_text(encoding="utf-8")


def test_les_trois_sites_du_truth_lock_sont_alimentes():
    """Un site oublié = un chemin de sortie qui ment par omission."""
    assert _SRC.count("unpublished_writes=self._mission_unpublished_writes()") == 3
    assert _SRC.count("apply_mission_truth_lock(") == 3


def test_la_redirection_est_branchee_hors_du_garde_browser():
    """Écrire un fichier n'est jamais un outil `browser_*` — même raison que Z20."""
    i = _SRC.index("self._nudge_unpublished_writes()")
    bloc = _SRC[i - 900:i]
    assert "_invalidate_interaction_pending" in bloc
    assert "_is_browser_tool" not in bloc


def test_la_raison_du_lot_est_datee_dans_le_code():
    entete = _SRC[_SRC.index("LOT Z24 — fichiers ecrits APRES"):][:1800]
    assert "jeu 3D" in entete
    assert "iteration 26" in entete
    assert "2.12.C" in entete
