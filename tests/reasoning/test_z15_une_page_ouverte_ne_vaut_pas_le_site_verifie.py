"""LOT Z15 — une page ouverte ne vaut pas le site vérifié.

Run « Verdure 2 » (2026-08-16). Le style était à 96 % (Z14 prouvé), et pourtant :

    URL ouvertes par la mission : http://localhost:8081     (= index.html)
    pages produites             : index.html, devis.html
    [Z11] déclenchements        : 0
    sortie                      : MISSION FINALIZE déterministe, clôture propre

`devis.html` — l'espace client où vivent l'enregistrement d'un client, la
création d'un devis et le changement d'état — n'a **jamais** été ouverte. Aucune
interaction n'a été exercée. La mission a conclu « vérifié structurellement ».

**Pourquoi Z11 s'est tu.** Il avait été placé avant les sorties anticipées de
`_mission_browser_verify_pending`. Mais son APPELANT en a une, un cran plus haut :

    def _finalize_browser_gate_pending(...):
        if self._current_browser_proof():
            return ""                              ← sort ICI
        return self._mission_browser_verify_pending(...)   ← Z11 vit là-dedans

Une page ouverte suffit à rendre `_current_browser_proof()` vrai. Le garde se
taisait donc sans jamais demander à Z11 s'il restait des pages. La voie du FINAL
LLM portait exactement la même condition (`not self._current_browser_proof()`).

Même motif, même erreur, deux étages plus haut — et mon test structurel Z11 ne
regardait que l'étage du dessous.

**DÉCISION UTILISATEUR (2026-08-16)** : « la vérification web du projet, peu
importe le domaine, doit se faire par le parent une fois les workers finis ; il
doit vraiment naviguer, scanner, vérifier — et s'il manque quelque chose, il est
là pour rattraper. C'est une sécurité. » Les workers ne voient chacun que leur
fichier ; le lead est le seul à pouvoir regarder le résultat en entier.
"""

from pathlib import Path

import pytest

from src.reasoning.plan_progress import pages_never_opened

_REACT = Path("src/reasoning/react.py").read_text(encoding="utf-8")

# Lot RF-7a du refactor ReAct (2026-08-28) : `_pages_never_opened_reason` et
# `_finalize_browser_gate_pending` ont quitte `react.py` pour
# `src/reasoning/browser_runtime.py`. Les assertions de ce fichier qui
# DECOUPENT ces corps pointent donc leur nouveau proprietaire ; celles qui
# visent le SITE D'APPEL (voie FINAL LLM, dans `_run_internal`) restent sur
# `react.py` — elles n'ont pas bouge.
#
# Le rebindage a renomme les acces a `self` en appelables de l'entree. Les
# assertions gardent leur INTENTION mot pour mot ; seuls les noms suivent :
#
#     self._is_worker_run()                    -> e.est_run_worker()
#     self._current_browser_proof()            -> e.preuve_navigateur_courante()
#     self._truth_lock_interaction_proven()    -> e.interaction_prouvee()
#     self._pages_never_opened_reason()        -> e.pages_jamais_ouvertes()
#     getattr(self, "_browser_gate_shots", 0)  -> e.tirs_gate_navigateur()
#
# Preuve COMPORTEMENTALE equivalente exigee par le plan avant ce repointage :
#   tests/reasoning/test_rf7a_browser_runtime_extraction.py
#     - test_les_gardes_refusent_autant_qu_ils_laissent_passer
#     - test_comportement_le_constat_de_preview_ferme_la_relecture_de_CETTE_page
# Elles verifient que les gardes REFUSENT vraiment (10 refus mesures), au lieu
# de chercher des chaines dans un fichier.
_GATE = Path("src/reasoning/browser_runtime.py").read_text(encoding="utf-8")



def _bloc_appelant() -> str:
    """`_finalize_browser_gate_pending` en ENTIER — c'est l'étage qui manquait."""
    debut = _GATE.index("def _finalize_browser_gate_pending")
    fin = _GATE.index("def _finalize_interaction_gate_pending", debut)
    return _GATE[debut:fin]


# ── Le cas mesuré ────────────────────────────────────────────────────────────


def test_le_cas_verdure2_une_seule_page_ouverte_sur_deux():
    """La racine sert `index.html` ; `devis.html` reste invisible."""
    produced = ["index.html", "devis.html", "styles.css", "donnees.js", "devis.js"]
    assert pages_never_opened(produced, ["http://localhost:8081"]) == ["devis.html"]


def test_ouvrir_laccueil_ne_dispense_pas_de_lespace_client():
    """Le cœur du lot : la preuve navigateur existe, et pourtant il reste à voir."""
    assert pages_never_opened(["index.html", "espace.html"], ["http://x:8081/"]) == [
        "espace.html"
    ]


# ── L'étage qui manquait : l'appelant ────────────────────────────────────────


def test_lappelant_consulte_z11_avant_sa_sortie_anticipee():
    """LE test que Z11 n'avait pas. `_current_browser_proof()` renvoyait ""
    avant que Z11 ait pu dire qu'il restait des pages."""
    bloc = _bloc_appelant()
    i_z11 = bloc.index("e.pages_jamais_ouvertes()")
    i_proof = bloc.index("if e.preuve_navigateur_courante():")
    assert i_z11 < i_proof


def test_lappelant_rend_la_raison_de_z11_telle_quelle():
    """Le message doit nommer les pages : un blocage muet se contourne."""
    bloc = _bloc_appelant()
    i = bloc.index("e.pages_jamais_ouvertes()")
    assert "return _unseen" in bloc[i : i + 260]


def test_z11_ne_peut_pas_casser_le_gate_chez_lappelant():
    """Un garde d'appoint ne doit jamais faire échouer une clôture légitime."""
    bloc = _bloc_appelant()
    i = bloc.index("e.pages_jamais_ouvertes()")
    assert "except Exception" in bloc[i : i + 320]


def test_le_plafond_dun_tir_reste_en_tete():
    """Sans lui, une mission dont une page reste fermée bouclerait sans fin."""
    bloc = _bloc_appelant()
    assert bloc.index("e.tirs_gate_navigateur() >= 1") < bloc.index(
        "e.pages_jamais_ouvertes()"
    )


# ── L'autre voie de clôture : le FINAL LLM ───────────────────────────────────


def _condition_gate_final() -> str:
    """La condition du gate navigateur sur la voie FINAL LLM.

    Bornée par un ancrage SÉMANTIQUE (`_bg_shots < 1` → fin de la condition) et
    non par une indentation littérale : un test qui dépend de la mise en page
    casse au premier reformatage et ne dit rien de l'intention."""
    debut = _REACT.index("_bg_shots = getattr(self, \"_browser_gate_shots\", 0)")
    fin = _REACT.index("_web_where = self._mission_browser_verify_pending", debut)
    return _REACT[debut:fin]


def test_la_voie_du_final_llm_porte_la_meme_correction():
    """Les deux chemins de clôture avaient la MÊME sortie anticipée. En corriger
    un seul aurait déplacé le trou au lieu de le fermer."""
    cond = _condition_gate_final()
    assert "_current_browser_proof()" in cond
    assert "_pages_never_opened_reason()" in cond
    # en ALTERNATIVE, pas en exigence supplémentaire
    assert "or self._pages_never_opened_reason()" in cond


def test_les_deux_voies_sont_tracees_comme_z15():
    # Les deux traces vivent desormais dans DEUX fichiers : la voie FINAL LLM
    # est restee dans `_run_internal`, la voie FINALIZE est partie avec le
    # gate. L'affirmation — « les deux voies sont tracees » — est inchangee.
    assert _REACT.count("LOT Z15") + _GATE.count("LOT Z15") >= 2


def test_la_voie_du_final_reste_bornee_a_un_tir():
    """`_bg_shots < 1` doit rester dans la même condition."""
    assert "_bg_shots < 1" in _condition_gate_final()


def test_la_voie_du_final_reste_hors_de_la_fin_du_budget():
    assert "i < self.max_iterations - 2" in _condition_gate_final()


# ── Inertie : Z15 ne parle que du multi-pages, chez le lead ──────────────────


def test_une_page_unique_ne_declenche_rien():
    """Le mono-page est déjà couvert par la jambe navigateur du LOT D."""
    assert pages_never_opened(["index.html"], []) == []


def test_toutes_les_pages_vues_ne_reproche_rien():
    vues = ["http://x/index.html", "http://x/devis.html"]
    assert pages_never_opened(["index.html", "devis.html"], vues) == []


def test_une_mission_sans_page_ne_declenche_rien():
    assert pages_never_opened(["app.py", "styles.css"], []) == []


def test_z15_reste_inerte_chez_un_worker():
    """LOT D-fix : l'app n'est pas servie pendant le run isolé d'un worker —
    l'inertie vient de `_pages_never_opened_reason`, qui sort sur
    `_is_worker_run()`."""
    debut = _GATE.index("def _pages_never_opened_reason")
    fin = _GATE.index("def _finalize_browser_gate_pending", debut)
    assert "e.est_run_worker()" in _GATE[debut:fin]


# ── La raison, pour que le lot ne soit pas défait par mégarde ────────────────


def test_la_decision_utilisateur_est_ecrite_dans_le_code():
    """Sans elle, le prochain lecteur verra une sortie anticipée « optimisante »
    et la rétablira."""
    bloc = _bloc_appelant()
    assert "Verdure 2" in bloc
    assert "devis.html" in bloc
    assert "filet de sécurité" in bloc


def test_le_code_dit_pourquoi_le_test_precedent_navait_rien_vu():
    """La leçon de méthode : vérifier l'ordre dans une fonction ne dit rien de
    l'ordre chez celui qui l'appelle."""
    bloc = _bloc_appelant()
    assert "APPELANT" in bloc


@pytest.mark.parametrize(
    "produced,visited,attendu",
    [
        (["a.html", "b.html"], ["http://x/a.html"], ["b.html"]),
        (["a.html", "b.html"], ["http://x/"], ["a.html", "b.html"]),
        (["index.html", "b.html"], ["http://x/"], ["b.html"]),
        (["index.html", "b.html"], [], ["b.html", "index.html"]),
    ],
)
def test_le_calcul_reste_juste_quelle_que_soit_la_forme(produced, visited, attendu):
    assert pages_never_opened(produced, visited) == attendu
