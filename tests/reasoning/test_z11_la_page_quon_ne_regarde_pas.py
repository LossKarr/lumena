"""LOT Z11 — la page qu'on ne regarde pas est la page qu'on bâcle.

Mesuré sur les TROIS runs web multi-pages, sans une seule exception :

    Palier   app.html    100 %  ·  index.html    4 %   (0 ouverture)
    Tanière  espace.html 100 %  ·  index.html   50 %   (0 ouverture)
    Marée    espace.html 100 %  ·  index.html   31 %   (0 ouverture, 9 sur espace)

La mission ouvre la page où vivent les fonctionnalités, la teste à fond — clics,
rechargement, lecture du DOM — et ne regarde jamais la page publique. Le LOT D
exige déjà une « jambe navigateur », mais il se satisfait d'UNE navigation :
ouvrir une page sur deux le contentait.

Z7 rend pourtant le fait, nommément (« `index.html` n'a que 4/13 de ses classes
stylées »). Sur Tanière la mission a corrigé ; sur Marée elle a ignoré le même
message. **Un constat seul ne suffit pas ici** — d'où la décision utilisateur du
2026-08-16 : forcer l'ouverture de CHAQUE page produite, comme Z1b avait forcé
le passage par le CodeAgent.
"""

from pathlib import Path

import pytest

from src.reasoning.plan_progress import pages_never_opened, unseen_pages_reason

_MAREE = ["maree/index.html", "maree/espace.html", "maree/styles.css", "maree/donnees.js"]


# ── Le cas mesuré ────────────────────────────────────────────────────────────


def test_le_cas_reel_de_maree():
    """9 navigations sur espace.html, aucune sur index.html."""
    vues = ["http://localhost:8081/espace.html"] * 9
    assert pages_never_opened(_MAREE, vues) == ["index.html"]


def test_le_cas_reel_de_palier():
    produced = ["palier/index.html", "palier/app.html"]
    assert pages_never_opened(produced, ["http://127.0.0.1:8081/app.html"]) == ["index.html"]


def test_toutes_les_pages_vues_ne_reproche_rien():
    vues = ["http://localhost:8081/index.html", "http://localhost:8081/espace.html"]
    assert pages_never_opened(_MAREE, vues) == []


def test_plusieurs_pages_manquantes_sont_toutes_listees():
    produced = ["a.html", "b.html", "c.html", "d.html"]
    assert pages_never_opened(produced, ["http://x/a.html"]) == ["b.html", "c.html", "d.html"]


# ── Rapprocher un fichier produit et une URL navigée ─────────────────────────


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:8081/index.html",
        "http://127.0.0.1:8085/index.html",
        "http://localhost:8081/index.html?v=2",
        "http://localhost:8081/index.html#section",
        "http://localhost:8081/sous/dossier/index.html",
        "file:///C:/Users/x/workspace/maree/index.html",
        "HTTP://LOCALHOST:8081/INDEX.HTML",
    ],
)
def test_une_page_est_reconnue_quelle_que_soit_la_forme_de_lurl(url):
    """Produite comme `maree/index.html`, ouverte comme une URL complète :
    seul le nom de fichier les relie."""
    assert "index.html" not in pages_never_opened(_MAREE, [url])


# ── La racine sert index.html (correctif du run Fournil) ────────────────────


@pytest.mark.parametrize(
    "racine",
    [
        "http://localhost:8081/",
        "http://localhost:8081",       # sans slash final — forme tout aussi courante
        "http://127.0.0.1:8085/",
        "file:///C:/Users/x/workspace/fournil/",
    ],
)
def test_ouvrir_la_racine_compte_comme_ouvrir_index(racine):
    """Run « Fournil » (2026-08-16) — MA régression, pas celle de Lumena.

    La mission avait ouvert `http://localhost:8081/` : c'est bien la page
    d'accueil, tout serveur statique y sert `index.html`. Mon helper en tirait un
    basename VIDE et répondait « index.html ET commande.html jamais ouvertes » —
    faux des deux côtés, donc muet là où il aurait dû bloquer, car
    `commande.html` n'a réellement jamais été ouverte.
    """
    produced = ["fournil/index.html", "fournil/commande.html"]
    assert pages_never_opened(produced, [racine]) == ["commande.html"]


def test_un_hote_nest_jamais_pris_pour_une_page():
    """`http://localhost:8081` sans chemin ne doit pas rendre « localhost:8081 »."""
    from src.reasoning.plan_progress import _page_key

    assert _page_key("http://localhost:8081") == "index.html"
    assert ":" not in _page_key("http://127.0.0.1:8085")


def test_une_chaine_vide_ne_designe_toujours_aucune_page():
    """La racine sert index.html ; le vide, lui, ne désigne rien."""
    from src.reasoning.plan_progress import _page_key

    assert _page_key("") == ""
    assert _page_key("   ") == ""
    assert _page_key(None) == ""


def test_la_racine_ne_dispense_pas_des_autres_pages():
    """Le cœur du garde : ouvrir l'accueil ne vaut pas ouvrir le reste."""
    produced = ["a/index.html", "a/tarifs.html", "a/contact.html"]
    assert pages_never_opened(produced, ["http://x/"]) == ["contact.html", "tarifs.html"]


def test_les_chemins_windows_sont_rapproches():
    assert pages_never_opened(["maree\\index.html", "maree\\espace.html"],
                              ["http://x/index.html", "http://x/espace.html"]) == []


# ── Inertie : ce garde ne parle QUE du multi-pages ───────────────────────────


def test_une_page_unique_ne_declenche_rien():
    """Le cas mono-page est déjà couvert par la jambe navigateur du LOT D.
    Élargir ici ferait double emploi et sur-déclencherait."""
    assert pages_never_opened(["app/index.html"], []) == []


def test_aucune_page_html_ne_declenche_rien():
    """Mission Python, document, effets : rien à ouvrir."""
    assert pages_never_opened(["a.py", "b.css", "c.js", "d.md"], []) == []


@pytest.mark.parametrize("produced", [None, [], ["", "   "]])
def test_entrees_vides_ne_levent_jamais(produced):
    assert pages_never_opened(produced, None) == []


def test_la_raison_est_vide_quand_rien_ne_manque():
    assert unseen_pages_reason([]) == ""
    assert unseen_pages_reason(None) == ""


# ── Le message rendu au gate ─────────────────────────────────────────────────


def test_la_raison_nomme_les_pages():
    r = unseen_pages_reason(["index.html", "tarifs.html"])
    assert "`index.html`" in r and "`tarifs.html`" in r


def test_la_raison_dit_pourquoi_ca_compte():
    """Un gate qui bloque sans expliquer se contourne au lieu de se respecter."""
    assert "personne ne regarde" in unseen_pages_reason(["index.html"])


def test_au_dela_de_six_pages_le_reste_est_compte():
    r = unseen_pages_reason([f"p{i}.html" for i in range(9)])
    assert "(+3)" in r


# ── Le branchement dans le gate navigateur ───────────────────────────────────


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



def _corps_z11() -> str:
    """La méthode ENTIÈRE, bornée à la suivante — une fenêtre en nombre de
    caractères tronquait la fin et faisait échouer le test sur son propre
    découpage plutôt que sur le code."""
    debut = _GATE.index("def _pages_never_opened_reason")
    fin = _GATE.index("def _finalize_browser_gate_pending", debut)
    return _GATE[debut:fin]


def test_le_gate_consulte_z11_avant_ses_sorties_anticipees():
    """Le gate rendait "" dès qu'UNE preuve navigateur existait : placé après,
    Z11 n'aurait jamais tiré sur Marée (9 navigations sur une seule page)."""
    i = _GATE.index("e.pages_jamais_ouvertes()")
    bloc = _GATE[i : i + 1200]
    assert "e.interaction_prouvee()" in bloc
    assert "e.preuve_navigateur_courante()" in bloc


def test_z11_est_inerte_chez_un_worker():
    """LOT D-fix : la vérif navigateur est le job du TOP-LEAD ; l'app n'est pas
    servie pendant le run isolé d'un worker."""
    corps = _corps_z11()
    assert "e.est_run_worker()" in corps


def test_z11_lit_le_ledger_et_le_contrat():
    """Mêmes sources bornées que `_mission_web_present_for_gate` (garde-fou P0.2) :
    pas de scan disque nouveau."""
    corps = _corps_z11()
    assert "written_basenames()" in corps
    assert "contract.json" in corps


def test_z11_collecte_les_urls_depuis_lhistorique():
    corps = _corps_z11()
    assert 'tool_name", "") != "browser_navigate"' in corps
    assert 'get("url")' in corps


def test_z11_ne_peut_pas_casser_le_gate():
    """Un garde d'appoint ne doit jamais faire échouer une clôture légitime."""
    corps = _corps_z11()
    assert "try:" in corps and "except Exception" in corps


def test_z11_trace_ce_quil_bloque():
    """Sans trace, un gate qui tire est indistinguable d'une mission lente —
    c'est ce qui a coûté le plus de temps d'analyse sur les runs précédents."""
    i = _GATE.index("def _pages_never_opened_reason")
    assert "[Z11]" in _GATE[i : i + 2600]
