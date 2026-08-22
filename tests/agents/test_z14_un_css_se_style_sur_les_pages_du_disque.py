"""LOT Z14 — le fichier prime sur la consigne quand les deux se contredisent.

Run « Verdure » (2026-08-16), chronologie à la milliseconde :

    17:48:47.363   le CodeAgent FINIT index.html   → `.prestation-card`, `.tarif`
                                                      sont sur le DISQUE
    17:48:47.560   il COMMENCE styles.css            200 ms plus tard
    17:48:47.949   « 4 fichier(s) cible(s) injecté(s) »

Le code lit les fichiers cibles au démarrage de la tâche (`_cand_path.read_text()`),
donc il avait le bon HTML sous les yeux. Il a écrit `.prestations-list` et `.card`.

**Pourquoi ?** Le worker CSS lui avait dicté, 76 secondes plus tôt, une liste de
classes « possibles ». Les cinq workers partent en parallèle : quand celui du CSS
a lu ses voisins, ils ne contenaient que `TODO (worker)`. Il l'écrit lui-même au
log :

    « Les HTML sont encore des stubs vides. Je ne peux pas relever les classes
      réelles. Je vais donc créer un CSS complet qui couvre TOUTES les classes
      possibles. »

Il n'a pas désobéi à la consigne Z1 (« LIS le HTML et relève ses class réelles ») :
il l'a honorée sur un fichier vide. Le CodeAgent, lui, a préféré l'instruction du
worker au fichier qu'il avait en main.

Cela explique l'aléa mesuré sur 29 missions — de 0 % à 100 % de couverture sans
logique apparente (palier 4 %, rustine 0 %, marée 31 %, fournil 100 %) : tout
dépendait de l'ordre de passage dans le CodeAgent, qui est un singleton sérialisé.

Z14 ferme les deux failles : les pages entrent d'office dans le contexte d'une
tâche de style, et une règle dit laquelle des deux sources fait foi.
"""

from pathlib import Path

import pytest

_SRC = Path("src/agents/sub_agent.py").read_text(encoding="utf-8")


def _bloc_z14() -> str:
    """Le lot ENTIER — commentaire de justification compris, pas seulement le
    code. Partir de `_styling_task = ` laissait la chronologie hors du bloc et
    faisait échouer le test sur son propre découpage."""
    debut = _SRC.index("# ── LOT Z14")
    fin = _SRC.index("_target_content_blocks: list[str] = []", debut)
    return _SRC[debut:fin]


# ── La règle de sélection, reproduite ────────────────────────────────────────
#
# La logique vit au cœur de `_single_code_attempt`, une méthode trop enchâssée
# pour être appelée seule. On rejoue ici la règle EXACTE, et un test structurel
# plus bas vérifie que le source la contient bien telle quelle.


def _prioriser(candidats, tous_les_fichiers, description):
    styling = ".css" in description.lower()
    if not styling:
        return candidats
    pages = [f for f in tous_les_fichiers if f.lower().endswith((".html", ".htm"))]
    if not pages:
        return candidats
    return pages + [c for c in candidats if c not in pages]


_PROJET = ["index.html", "commande.html", "styles.css", "donnees.js", "commandes.js"]


def test_le_cas_verdure_la_page_ne_peut_plus_etre_evincee():
    """`_arch_max_files` vaut 4 : avec 5 fichiers cités, une page pouvait tomber
    hors du contexte par simple troncature."""
    candidats = ["styles.css", "donnees.js", "commandes.js", "index.html", "commande.html"]
    ordonne = _prioriser(candidats, _PROJET, "Remplis le fichier styles.css")
    assert ordonne[:2] == ["index.html", "commande.html"]
    assert "index.html" in ordonne[:4] and "commande.html" in ordonne[:4]


def test_une_page_jamais_citee_entre_quand_meme():
    """`_candidates` ne retenait un fichier que si son nom figurait dans la
    description. Une page que le worker ne nomme pas doit entrer malgré tout —
    c'est d'elle que viennent les sélecteurs."""
    assert "commande.html" in _prioriser(["styles.css"], _PROJET, "remplis styles.css")


def test_le_css_cible_reste_present():
    """Prioriser les pages ne doit pas évincer le fichier qu'on vient écrire."""
    assert "styles.css" in _prioriser(["styles.css"], _PROJET, "remplis styles.css")


def test_aucun_doublon_si_la_page_etait_deja_candidate():
    ordonne = _prioriser(["index.html", "styles.css"], _PROJET, "styles.css et index.html")
    assert ordonne.count("index.html") == 1


@pytest.mark.parametrize("desc", ["remplis styles.css", "corrige le STYLES.CSS", "ajoute a.css"])
def test_une_tache_de_style_est_reconnue(desc):
    assert _prioriser(["a"], ["p.html"], desc)[0] == "p.html"


# ── Inertie : une tâche qui ne style rien n'est pas touchée ──────────────────


@pytest.mark.parametrize(
    "desc",
    ["remplis donnees.js selon CONTRAT.md", "implémente app.py", "corrige le test"],
)
def test_une_tache_sans_css_garde_ses_candidats(desc):
    """Injecter les pages partout noierait le contexte d'un worker backend."""
    candidats = ["donnees.js", "app.py"]
    assert _prioriser(candidats, _PROJET, desc) == candidats


def test_un_projet_sans_page_reste_inchange():
    candidats = ["theme.css", "main.py"]
    assert _prioriser(candidats, ["theme.css", "main.py"], "remplis theme.css") == candidats


def test_aucun_candidat_et_aucune_page_ne_leve_pas():
    assert _prioriser([], [], "remplis styles.css") == []


# ── La règle de priorité : sans elle, le fichier est là et ignoré ────────────


def test_la_regle_dit_laquelle_des_deux_sources_fait_foi():
    """Sur Verdure, la page ÉTAIT en contexte et le modèle a suivi la
    description. Injecter ne suffit pas : il faut trancher le conflit."""
    i = _SRC.index("_styling_rule = ")
    bloc = _SRC[i : i + 900]
    assert "JAMAIS dans la description" in bloc
    assert "le fichier a" in bloc and "raison" in bloc


def test_la_regle_interdit_le_css_defensif():
    """« couvre TOUTES les classes possibles » est la phrase exacte qui a produit
    les 366 lignes de CSS mort de Verdure."""
    i = _SRC.index("_styling_rule = ")
    assert "CSS mort" in _SRC[i : i + 900]


def test_la_regle_explique_pourquoi_la_description_peut_avoir_tort():
    """Un modèle qui ne comprend pas la raison arbitre au hasard."""
    i = _SRC.index("_styling_rule = ")
    assert "avant que les pages soient écrites" in _SRC[i : i + 900]


def test_la_regle_ne_sort_que_pour_une_tache_de_style():
    """Un worker backend ne doit pas lire une consigne sur les sélecteurs CSS."""
    i = _SRC.index("_styling_rule = ")
    assert "if _styling_task else" in _SRC[i : i + 1100]


# ── Le branchement ───────────────────────────────────────────────────────────


def test_la_priorisation_precede_la_troncature():
    """Placée après `_candidates[:_arch_max_files]`, elle n'aurait rien sauvé."""
    bloc = _bloc_z14()
    assert "_pages + [c for c in _candidates if c not in _pages]" in bloc
    assert _SRC.index("_styling_task = ") < _SRC.index("for _cand in _candidates[:_arch_max_files]")


def test_les_pages_viennent_de_la_liste_deja_filtree():
    """`_project_files_clean` exclut .backups, .git, node_modules — repartir de
    la liste brute réinjecterait des sauvegardes comme sources de vérité."""
    assert "_pf for _pf in _project_files_clean" in _bloc_z14()


def test_seuls_les_fichiers_de_page_sont_promus():
    bloc = _bloc_z14()
    assert '(".html", ".htm")' in bloc


def test_la_raison_du_lot_est_datee_dans_le_code():
    """Sans la chronologie, le prochain lecteur croira à une optimisation
    gratuite et la supprimera."""
    bloc = _bloc_z14()
    assert "Verdure" in bloc and "17:48:47" in bloc
    assert "stubs" in bloc
