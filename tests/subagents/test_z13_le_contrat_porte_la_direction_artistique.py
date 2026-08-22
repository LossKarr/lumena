"""LOT Z13 — le contrat garantissait la cohérence, il excluait la direction.

Lumena possède `generate_website` : sites « niveau agence » bâtis sur
`build_design_directives()` — palette validée WCAG 2.1 AA, variables CSS
complètes (ombres, rayons, transitions), typographie choisie. Le skill
`frontend-design` route explicitement vers cet outil, et le skill
`website-generator` dit même : « tu DOIS utiliser l'outil `generate_website` ».

**Une mission ne peut pas s'en servir.** `write_mission_contract` fige
`styles.css` comme un fichier assigné à un propriétaire ; `generate_website`
produirait un site entier, incompatible avec un périmètre d'un seul fichier.

Mesuré sur le run Fournil (2026-08-16) :

    frontend-design    chargé (41.5)   ← le guide dit « route vers generate_website »
    theme-factory      chargé (47.5)
    generate_website   0 appel         ← l'outil n'est jamais utilisé

Le worker CSS improvise. Sur Fournil il a deviné juste (du brun pour une
boulangerie) mais sans variables, sans échelle d'ombres, sans typographie. Sur
Palier, Tanière, Marée et Rustine, il a inventé des noms de classes qui ne
rencontraient jamais le HTML — 4 %, 50 %, 31 %, 0 % de couverture.

Ce n'est pas un bug : c'est une conséquence non voulue du contrat, qui a résolu
la cohérence entre workers en excluant tout outil produisant un ensemble d'un
seul coup. Z13 ne casse pas le contrat — il lui fait PORTER la direction, via la
même fonction pure que le générateur.
"""

import re

import pytest

from src.subagents.mission_contract import (
    _owns_stylesheet,
    design_brief_for_contract,
    worker_discipline_block,
    worker_objectives,
)

_FOURNIL = {
    "project": "fournil",
    "files": [
        {"path": "index.html", "owner": "w_index",
         "desc": "Page publique de la boulangerie : accroche, 3 specialites (pain, "
                 "viennoiseries), horaires, bouton Ma commande."},
        {"path": "commande.html", "owner": "w_commande",
         "desc": "Espace client : barre laterale, en-tete, zone principale."},
        {"path": "styles.css", "owner": "w_styles",
         "desc": "TOUTE la presentation des deux pages. Chaleureux, artisanal, lisible."},
        {"path": "donnees.js", "owner": "w_donnees",
         "desc": "Persistance localStorage : produits, commandes, clients."},
        {"path": "commandes.js", "owner": "w_commandes",
         "desc": "Creer une commande, changer son etat, lister celles en attente."},
    ],
}


def _palette(brief: str) -> str:
    m = re.search(r'PALETTE: "([^"]+)"', brief or "")
    return m.group(1) if m else ""


# ── Le brief produit la bonne direction ──────────────────────────────────────


def test_le_contrat_fournil_donne_la_palette_boulangerie():
    """Le run réel : la mission a produit du brun par chance. Ici c'est par choix."""
    assert _palette(design_brief_for_contract(_FOURNIL)) == "Bakery Warm Brown"


def test_le_brief_porte_ce_qui_manquait_au_css_artisanal():
    brief = design_brief_for_contract(_FOURNIL)
    for attendu in ("--primary", "--radius", "--shadow", "--font-heading", "WCAG"):
        assert attendu in brief


def test_toutes_les_descriptions_sont_agregees():
    """MESURÉ sur Fournil — la détection de domaine a besoin des descriptions des
    PAGES, où vivent « boulangerie » et « viennoiseries » :

        project seul .................. E-commerce Green   ❌
        project + desc du CSS ......... AI/Chatbot Purple  ❌
        agrégat complet ............... Bakery Warm Brown  ✅
    """
    seul = {"project": "fournil", "files": [
        {"path": "styles.css", "owner": "w", "desc": "TOUTE la presentation."}]}
    assert _palette(design_brief_for_contract(seul)) != "Bakery Warm Brown"
    assert _palette(design_brief_for_contract(_FOURNIL)) == "Bakery Warm Brown"


def test_le_brief_est_deterministe():
    """Même contrat → même palette : deux relances ne doivent pas repeindre le site."""
    assert design_brief_for_contract(_FOURNIL) == design_brief_for_contract(_FOURNIL)


@pytest.mark.parametrize(
    "sujet,attendu_absent",
    [("boulangerie artisanale pain viennoiseries", "SaaS Trust Blue"),
     ("SaaS suivi de temps freelances dashboard", "Bakery Warm Brown")],
)
def test_la_direction_depend_du_domaine(sujet, attendu_absent):
    d = {"project": "x", "files": [{"path": "styles.css", "owner": "w", "desc": sujet}]}
    assert _palette(design_brief_for_contract(d)) != attendu_absent


# ── Seul le propriétaire du style le reçoit ──────────────────────────────────


def test_seul_le_worker_css_recoit_le_brief():
    """~3400 caractères : les donner à tous les workers frontend noierait le
    prompt de celui qui écrit le HTML ou le JS."""
    porteurs = [
        o for o in worker_objectives(_FOURNIL) if "PALETTE" in o["objective"]
    ]
    assert len(porteurs) == 1
    assert porteurs[0]["allowed_files"] == ["styles.css"]


def test_le_worker_html_ne_recoit_pas_le_brief():
    for o in worker_objectives(_FOURNIL):
        if o["allowed_files"] == ["index.html"]:
            assert "PALETTE" not in o["objective"]
            break
    else:
        pytest.fail("worker index.html introuvable")


def test_le_worker_css_garde_sa_consigne_de_style_z1():
    """Z13 s'ajoute à Z1 (« lis le HTML et relève ses class réelles »), il ne le
    remplace pas : les deux répondent à des défauts différents."""
    for o in worker_objectives(_FOURNIL):
        if o["allowed_files"] == ["styles.css"]:
            assert "PALETTE" in o["objective"]
            assert "LIS le fichier HTML" in o["objective"]
            break
    else:
        pytest.fail("worker styles.css introuvable")


@pytest.mark.parametrize(
    "fichiers,attendu",
    [(["styles.css"], True), (["a/styles.css"], True), (["theme.CSS"], True),
     (["index.html"], False), (["app.js"], False), (["main.py"], False), ([], False)],
)
def test_la_detection_du_porteur_de_style(fichiers, attendu):
    assert _owns_stylesheet(fichiers) is attendu


def test_un_worker_mixte_html_et_css_recoit_le_brief():
    """Un seul worker pour les deux : c'est bien lui qui décide du style."""
    bloc = worker_discipline_block(["index.html", "styles.css"], "═══ PALETTE test ═══")
    assert "PALETTE test" in bloc


# ── Inertie : rien ne change là où il n'y a rien à styler ────────────────────


def test_un_contrat_sans_css_ne_produit_aucune_injection():
    d = {"project": "outil", "files": [
        {"path": "moteur.py", "owner": "w1", "desc": "Calcul."},
        {"path": "test_moteur.py", "owner": "w2", "desc": "Tests."}]}
    for o in worker_objectives(d):
        assert "PALETTE" not in o["objective"]


def test_sans_brief_le_bloc_est_identique_a_avant():
    """Le défaut de `design_brief` doit rendre le comportement historique EXACT."""
    assert worker_discipline_block(["styles.css"]) == worker_discipline_block(
        ["styles.css"], ""
    )


@pytest.mark.parametrize("data", [None, {}, [], "contrat", {"files": []}])
def test_une_entree_inexploitable_ne_leve_jamais(data):
    assert design_brief_for_contract(data) == ""


def test_un_contrat_sans_texte_ne_produit_rien():
    assert design_brief_for_contract({"project": "", "files": []}) == ""


def test_la_generation_indisponible_laisse_le_contrat_intact(monkeypatch):
    """`build_design_directives` peut être absente (WEBSITE_BUILDER_AVAILABLE) :
    l'injection est additive, jamais bloquante."""
    import src.tools.website_builder as wb

    def _boum(*a, **k):
        raise RuntimeError("indisponible")

    monkeypatch.setattr(wb, "build_design_directives", _boum)
    assert design_brief_for_contract(_FOURNIL) == ""
    for o in worker_objectives(_FOURNIL):
        assert "PALETTE" not in o["objective"]


def test_un_worker_deffets_purs_nest_pas_touche():
    """H4 : un porteur d'effets sans fichier reçoit la discipline d'EFFETS, pas
    celle de codage — et surtout pas 3400 chars de CSS."""
    d = {"project": "veille", "files": [], "effects": [
        {"owner": "w_mail", "action": "envoyer", "desc": "Envoyer la synthèse par mail."}]}
    for o in worker_objectives(d):
        assert "PALETTE" not in o["objective"]


# ── Le branchement ───────────────────────────────────────────────────────────


def test_le_brief_est_calcule_une_seule_fois_par_contrat():
    from pathlib import Path

    src = Path("src/subagents/mission_contract.py").read_text(encoding="utf-8")
    corps = src[src.index("def worker_objectives") :]
    corps = corps[: corps.index("\ndef ", 10)]
    assert corps.count("design_brief_for_contract(data)") == 1
    assert corps.index("design_brief_for_contract(data)") < corps.index("for owner in owners")


def test_la_raison_du_lot_est_ecrite_dans_le_code():
    """Sans la trace, le prochain qui lit ce code croira à un doublon de Z1."""
    from pathlib import Path

    src = Path("src/subagents/mission_contract.py").read_text(encoding="utf-8")
    i = src.index("def design_brief_for_contract")
    entete = src[i : i + 2200]
    assert "generate_website" in entete
    assert "Fournil" in entete
