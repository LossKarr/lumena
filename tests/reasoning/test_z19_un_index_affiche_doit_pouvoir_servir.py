"""LOT Z19 — l'index qu'on affiche doit pouvoir servir partout.

Run « Pelage » (2026-08-17). `browser_dom_state` venait d'afficher :

    [9]  combobox "-- Choisir --Marie Curie"
    [10] combobox "-- Choisir --"
    [11] combobox "Bain (25€) Tonte (40€) Soin complet (60€)"

La mission a fait le geste naturel :

    browser_select(selector='[9]', label='Marie Curie', by='index')
    → Page.select_option: SyntaxError: '[9]' is not a valid selector

Trois faits, trouvés dans le code :

  1. le convertisseur `[N]` EXISTAIT déjà — `_browser_rewrite_index_like_selector_action`,
     écrit pour ce cas exact — mais son garde listait `{browser_type, browser_click}` ;
  2. `browser_click_index` et `browser_type_index` étaient enregistrés ; le TROISIÈME
     outil de la famille, non ;
  3. `DOMElement.options` était déclaré (dom_indexer l. 137), lu depuis le JSON
     (l. 494) et rendu par `to_text()` (l. 162-165) — mais le chemin de repli JS
     écrivait `options: []` en dur.

Coût mesuré : la mission a piloté les trois `<select>` en JavaScript via
`browser_evaluate` — 10 appels, budget navigateur à **36/32**.

Ce que ça troue vraiment : **Z16** exige une interaction MÉTIER réelle, et un
`<select>` est dans presque tous les formulaires métier (client, forfait, état).
Sans cet outil, Z16 pousse la mission à écrire son propre JS pour simuler
l'interaction — exactement le trou que Z16 devait fermer.

Motif du chantier, à la lettre : l'index était calculé, il était affiché au
modèle, et il était jeté au seul outil qui en avait besoin.
"""

import asyncio
from pathlib import Path

import pytest

from src.reasoning.handlers.browser import (
    browser_select_index,
    get_browser_handler_defs,
)
from src.reasoning.handlers.context import HandlerContext
from src.reasoning.react import _browser_rewrite_index_like_selector_action


def _run(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("loop fermé")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


def _ctx() -> HandlerContext:
    return HandlerContext.for_testing()


# ── Le cas mesuré ────────────────────────────────────────────────────────────


def test_lappel_exact_de_pelage_est_reroute():
    """L'appel tel que la mission l'a écrit ce soir-là."""
    r = _browser_rewrite_index_like_selector_action(
        "browser_select", {"selector": "[9]", "label": "Marie Curie", "by": "index"}
    )
    assert r is not None
    outil, args, raison = r
    assert outil == "browser_select_index"
    assert args["index"] == "9"
    assert args["label"] == "Marie Curie"
    assert "index DOM [9]" in raison


def test_la_valeur_est_reportee_comme_le_label():
    _, args, _ = _browser_rewrite_index_like_selector_action(
        "browser_select", {"selector": "[11]", "value": "tonte"}
    )
    assert args["value"] == "tonte"


def test_le_rang_de_loption_ne_devient_pas_lindex_dom():
    """Collision de noms : le `index` de browser_select désigne le RANG DE
    L'OPTION, celui de browser_select_index l'élément DOM. Les confondre
    sélectionnerait la mauvaise option en silence — le pire des deux mondes."""
    _, args, _ = _browser_rewrite_index_like_selector_action(
        "browser_select", {"selector": "[11]", "index": 2}
    )
    assert args["index"] == "11"       # l'élément DOM
    assert args["option_index"] == 2   # le rang de l'option


def test_un_select_sans_critere_reste_converti():
    """Le rerouteur ne juge pas la complétude — le handler le fera, avec un
    message qui nomme les trois critères possibles."""
    assert _browser_rewrite_index_like_selector_action(
        "browser_select", {"selector": "[9]"}
    ) is not None


# ── L'inertie : les deux branches historiques ────────────────────────────────


def test_browser_type_est_intouche():
    outil, args, _ = _browser_rewrite_index_like_selector_action(
        "browser_type", {"selector": "[16]", "text": "LumenaAI"}
    )
    assert outil == "browser_type_index"
    assert args == {"index": "16", "text": "LumenaAI"}


def test_browser_click_est_intouche():
    outil, args, _ = _browser_rewrite_index_like_selector_action(
        "browser_click", {"selector": "[3]"}
    )
    assert outil == "browser_click_index"
    assert args == {"index": "3"}


@pytest.mark.parametrize(
    "args",
    [
        {"selector": "#select-client"},
        {"selector": "select[name='client']"},
        {"selector": "text=Marie Curie"},
        {"selector": "[data-id='9']"},
        {"selector": "[9"},
        {"selector": "9]"},
        {"selector": "[9][10]"},
        {"selector": ""},
        {},
    ],
)
def test_un_vrai_selecteur_css_nest_jamais_reroute(args):
    """`browser_select` en CSS reste la voie normale — c'est ce que font les 3
    tests existants de test_handlers_browser.py."""
    assert _browser_rewrite_index_like_selector_action("browser_select", args) is None


def test_un_outil_hors_famille_nest_jamais_reroute():
    assert _browser_rewrite_index_like_selector_action(
        "browser_hover", {"selector": "[9]"}
    ) is None


# ── Le handler : ce qu'il refuse, et comment il le dit ───────────────────────


@pytest.mark.parametrize("index", [0, -1])
def test_un_index_hors_bornes_est_refuse(index):
    r = _run(browser_select_index(_ctx(), index=index, label="x"))
    assert not r.success
    assert "indexes a partir de 1" in r.output


def test_sans_critere_le_message_nomme_les_trois_voies():
    """Un refus muet coûte une itération pour rien (leçon Z18)."""
    r = _run(browser_select_index(_ctx(), index=9))
    assert not r.success
    for mot in ("label", "value", "option_index"):
        assert mot in r.output


def test_le_critere_est_verifie_avant_de_toucher_au_navigateur():
    """Sans navigateur démarré, un appel sans critère doit rendre le reproche de
    critère — sinon le vrai défaut reste caché derrière « navigateur non
    demarre »."""
    r = _run(browser_select_index(_ctx(), index=9))
    assert "Rien a selectionner" in r.output


# ── Le raccordement, sans quoi l'outil ne sert à rien ────────────────────────


def test_loutil_est_enregistre_avec_le_bon_contrat():
    d = {h.name: h for h in get_browser_handler_defs()}["browser_select_index"]
    assert d.parameters["required"] == ["index"]
    assert set(d.parameters["properties"]) == {"index", "label", "value", "option_index"}
    assert d.category == "browser"


def test_choisir_dans_un_select_compte_comme_interaction_reelle():
    """LE point du lot : sans cette ligne, une mission qui remplit un formulaire
    à listes déroulantes ne prouverait rien au sens de Z16."""
    from src.reasoning.plan_evidence import (
        ProofCapability,
        _TOOL_CAPABILITY_OVERRIDES,
    )

    caps = _TOOL_CAPABILITY_OVERRIDES["browser_select_index"]
    assert ProofCapability.GENERIC_MUTATION in caps
    assert caps == _TOOL_CAPABILITY_OVERRIDES["browser_type_index"]


# ── Les options : déclarées, affichables… et vides ───────────────────────────


_INDEXER = Path("src/computer_use/dom_indexer.py").read_text(encoding="utf-8")


def test_le_repli_dom_lit_les_vraies_options():
    i = _INDEXER.index("LOT Z19")
    bloc = _INDEXER[i : i + 700]
    assert "el.options" in bloc
    assert "'select'" in bloc


def test_les_options_remontent_jusqua_lelement():
    """La plomberie était complète (l. 494 lisait déjà `raw['options']`) — seule
    la source était vide. Ce test tient les deux bouts."""
    assert 'options=raw.get("options", []) or []' in _INDEXER


def test_le_champ_textbox_ne_gagne_pas_de_fausses_options():
    """Le second chemin pousse `role: 'textbox'` en dur : un textbox n'a pas
    d'options, et lui en inventer serait un mensonge d'observation."""
    i = _INDEXER.index("role: 'textbox',")
    assert "options: []," in _INDEXER[i : i + 400]


def test_un_select_est_lisible_par_le_modele():
    """Bout de chaîne : ce que le LLM voit réellement dans l'observation."""
    from src.computer_use.dom_indexer import DOMElement

    txt = DOMElement(
        index=11, role="combobox", name="",
        options=["Bain (25€)", "Tonte (40€)", "Soin complet (60€)"],
    ).to_text()
    assert "[11]" in txt
    assert "Tonte (40€)" in txt


def test_la_raison_du_lot_est_datee_dans_le_code():
    src = Path("src/reasoning/handlers/browser.py").read_text(encoding="utf-8")
    i = src.index("LOT Z19 — l'index qu'on affiche")
    entete = src[i : i + 1400]
    assert "Pelage" in entete
    assert "not a valid selector" in entete
    assert "Z16" in entete
