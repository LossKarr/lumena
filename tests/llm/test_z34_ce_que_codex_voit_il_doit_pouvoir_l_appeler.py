"""LOT Z34 — ce que Codex voit, il doit pouvoir l'appeler.

Run du 2026-08-21, « genere moi un site web mais en motion design ». Au log :

    04:49:44  discover_tools  « générer un site web complet motion design »
    04:49:56  Tool index built: 732 tools indexed
    04:50:02  discover_tools  « generate_website outil natif paramètres »
    04:50:44  discover_tools  « outil générique pour APPELER un outil découvert
                                par son nom »           ← il demande la sortie
    04:51:03  create_directory  — hors filtre prompt, soft-filter
    04:52:00  write_file        — hors filtre prompt, soft-filter
    04:54:51  [Agent/Codex] tour termine  model=gpt-5.6-sol  tools=11
    04:54:51  [MISSION TRUTH-LOCK] → « ⚠️ Navigateur NON vérifié »

Il a TROUVÉ `generate_website`, cherché comment l'appeler, renoncé, et écrit le
site à la main. Le livrable était pourtant bon (32 ko, 3 fichiers liés) — mais
sans `serve_website` il n'a pas pu servir la preview, donc pas de
`browser_navigate`, donc **pas de preuve** : le truth-lock a bannérisé.

MESURÉ avant d'écrire une ligne :

    sélection contextuelle pour cette phrase : 84 outils (files, web, agents,
      system, project…) — la catégorie `website` n'est PAS retenue
    expansion Codex : 260 outils — `web` menait à {browser, files, documents},
      jamais à `website`
    → 351 outils sur 597 (59 % de Lumena) hors de portée, 22 catégories sur 33
      jamais atteignables

L'asymétrie de fond : côté API le filtre n'est qu'une INDICATION — le registre
exécute quand même en journalisant « hors filtre prompt — soft-filter » (deux
fois dans ce run). Côté Codex, MCP exige une déclaration : non déclaré = mur.

Pourquoi pas « tout déclarer » ? Mesuré : 597 outils = **78 k tokens de contexte
à chaque tour**. C'est la raison d'être du filtre.

D'où deux gestes : relier `website` là où il a un sens (phase 1), et donner
l'invocateur que Codex réclamait (phase 2) — qui supprime le mur sans gonfler
la déclaration.
"""

import asyncio
import json
from types import SimpleNamespace

import pytest

from src.llm.codex_mcp_bridge import (
    INVOKE_TOOL_NAME,
    CodexMCPBridgeError,
    LumenaCodexToolBridge,
)
from src.llm.execution_router import _TOOL_TRANSITIONS, _visible_tool_names
from src.reasoning.tool_registry import ToolRegistry


_REQUETE = "genere moi un site web mais en motion design"


# ══════════════════════════════════════════════════════════════════════════════
#  Phase 1 — `website` devient atteignable
# ══════════════════════════════════════════════════════════════════════════════


@pytest.fixture(scope="module")
def _visible():
    r = ToolRegistry()
    r.apply_context_filter(_REQUETE, intent="react")
    return _visible_tool_names(SimpleNamespace(tools=r)), r


@pytest.mark.parametrize("outil", ["generate_website", "serve_website"])
def test_le_cas_mesure_est_repare(_visible, outil):
    """LE lot. Ces deux-là étaient ABSENTS sur la requête exacte du run."""
    vis, _ = _visible
    assert outil in vis


def test_sans_serve_website_il_n_y_a_pas_de_preuve_possible(_visible):
    """`serve_website` est le maillon dur : sans serveur local, pas de
    `browser_navigate`, donc bannière « Navigateur NON vérifié »."""
    vis, _ = _visible
    assert "serve_website" in vis
    assert "browser_navigate" in vis


def test_website_est_relie_depuis_web_et_project():
    assert "website" in _TOOL_TRANSITIONS["web"]
    assert "website" in _TOOL_TRANSITIONS["project"]


def test_website_ramene_vers_le_navigateur():
    """Générer un site sans pouvoir le regarder n'aurait aucun sens."""
    assert "browser" in _TOOL_TRANSITIONS["website"]


def test_les_transitions_historiques_sont_intactes():
    """Garde anti-régression : on AJOUTE, on ne réécrit pas la table."""
    assert _TOOL_TRANSITIONS["browser"] == frozenset({"files", "documents"})
    assert _TOOL_TRANSITIONS["files"] == frozenset({"system", "mail"})
    assert {"browser", "files", "documents"} <= _TOOL_TRANSITIONS["web"]
    assert {"git", "files", "codebase"} <= _TOOL_TRANSITIONS["project"]


def test_l_ajout_reste_borne(_visible):
    """Déclarer les 597 outils coûterait 78 k tokens PAR TOUR. L'ajout doit
    rester ciblé — si ce test tombe, c'est qu'on a élargi à l'aveugle."""
    vis, _ = _visible
    assert len(vis) < 340, f"{len(vis)} outils déclarés : expansion trop large"


# ══════════════════════════════════════════════════════════════════════════════
#  Phase 2 — l'invocateur que Codex a cherché
# ══════════════════════════════════════════════════════════════════════════════


class _FauxObservation:
    def __init__(self, content="ok", success=True):
        self.content = content
        self.success = success


class _FauxRegistre:
    """Enregistre ce qui lui est demandé, sans rien exécuter."""

    def __init__(self):
        self.appels = []

    def get_tools_schema(self):
        return [
            {"function": {"name": "serve_website", "description": "sert un site",
                          "parameters": {"type": "object", "properties": {}}}},
            {"function": {"name": "generate_website", "description": "genere",
                          "parameters": {"type": "object", "properties": {}}}},
        ]

    async def execute(self, name, arguments, caller=None):
        self.appels.append((name, dict(arguments)))
        return _FauxObservation()


def _pont(allowed=("serve_website",), **kw):
    reg = _FauxRegistre()
    journal = []
    pont = LumenaCodexToolBridge(
        reg,
        allowed_tools=allowed,
        agent_id="test-z34",
        before_call=lambda: journal.append(("before", None)),
        after_call=lambda n, a, o, d: journal.append(("after", n)),
        **kw,
    )
    return pont, reg, journal


def _appel(pont, name, arguments):
    return asyncio.run(
        pont._dispatch({"op": "call", "name": name, "arguments": arguments})
    )


# ── La déclaration ───────────────────────────────────────────────────────────


def test_l_invocateur_est_declare_a_codex():
    """Non déclaré, il n'existe pas pour le modèle — le lot serait inerte."""
    pont, _, _ = _pont()
    noms = [t["name"] for t in pont.tools()]
    assert INVOKE_TOOL_NAME in noms


def test_l_invocateur_vient_en_dernier():
    """Le jeu contextuel reste la suggestion principale ; l'invocateur n'est que
    la porte de sortie."""
    pont, _, _ = _pont()
    assert [t["name"] for t in pont.tools()][-1] == INVOKE_TOOL_NAME


def test_la_declaration_dit_d_utiliser_discover_tools_d_abord():
    """Sans ça, le modèle inventera des noms d'outils."""
    pont, _, _ = _pont()
    schema = [t for t in pont.tools() if t["name"] == INVOKE_TOOL_NAME][0]
    assert "discover_tools" in schema["description"]
    assert schema["inputSchema"]["required"] == ["name"]


# ── Le comportement ──────────────────────────────────────────────────────────


def test_un_outil_HORS_perimetre_devient_appelable():
    """LE lot : `generate_website` n'est pas dans `allowed_tools`, et pourtant
    l'invocateur le fait exécuter."""
    pont, reg, _ = _pont(allowed=("serve_website",))
    _appel(pont, INVOKE_TOOL_NAME,
           {"name": "generate_website", "arguments": {"project_name": "x"}})
    assert reg.appels == [("generate_website", {"project_name": "x"})]


def test_l_appel_direct_hors_perimetre_reste_refuse():
    """On ouvre une porte, on n'abat pas le mur : le périmètre déclaré garde
    son sens pour les appels directs."""
    pont, reg, _ = _pont(allowed=("serve_website",))
    with pytest.raises(CodexMCPBridgeError, match="outside this run scope"):
        _appel(pont, "generate_website", {})
    assert reg.appels == []


def test_le_ledger_est_alimente_par_cette_voie():
    """LA condition non négociable. `after_call` projette dans history ET
    ledger — c'est ce qui a permis au truth-lock de bannériser ce run. Un
    invocateur qui contourne ça réparerait l'accès en cassant l'honnêteté."""
    pont, _, journal = _pont()
    _appel(pont, INVOKE_TOOL_NAME, {"name": "generate_website", "arguments": {}})
    assert ("after", "generate_website") in journal


def test_le_contexte_est_prepare_avant_l_appel():
    pont, _, journal = _pont()
    _appel(pont, INVOKE_TOOL_NAME, {"name": "generate_website", "arguments": {}})
    assert journal[0][0] == "before"
    assert journal.index(("before", None)) < journal.index(("after", "generate_website"))


def test_le_nom_reel_est_journalise_pas_l_enveloppe():
    """Si le ledger enregistrait « invoke_tool », toutes les preuves de clôture
    deviendraient illisibles."""
    pont, reg, journal = _pont()
    _appel(pont, INVOKE_TOOL_NAME, {"name": "serve_website", "arguments": {}})
    assert ("after", INVOKE_TOOL_NAME) not in journal
    assert ("after", "serve_website") in journal


# ── Les refus ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("interdit", ["final_answer", "ask_user", INVOKE_TOOL_NAME])
def test_les_outils_de_controle_sont_refuses(interdit):
    """`final_answer`/`ask_user` pilotent la boucle : les rendre appelables
    laisserait Codex court-circuiter le point d'étranglement du FINAL, donc le
    truth-lock. Et l'invocateur ne s'appelle pas lui-même."""
    pont, reg, _ = _pont()
    with pytest.raises(CodexMCPBridgeError):
        _appel(pont, INVOKE_TOOL_NAME, {"name": interdit, "arguments": {}})
    assert reg.appels == []


@pytest.mark.parametrize("mauvais", [{}, {"name": ""}, {"name": "   "}])
def test_un_nom_manquant_est_refuse(mauvais):
    pont, reg, _ = _pont()
    with pytest.raises(CodexMCPBridgeError, match="requires a tool name"):
        _appel(pont, INVOKE_TOOL_NAME, mauvais)
    assert reg.appels == []


def test_des_arguments_non_objet_sont_refuses():
    pont, reg, _ = _pont()
    with pytest.raises(CodexMCPBridgeError, match="must be an object"):
        _appel(pont, INVOKE_TOOL_NAME, {"name": "serve_website", "arguments": 42})
    assert reg.appels == []


def test_des_arguments_en_JSON_texte_sont_acceptes():
    """Les modèles sérialisent souvent les objets imbriqués en chaîne."""
    pont, reg, _ = _pont()
    _appel(pont, INVOKE_TOOL_NAME,
           {"name": "serve_website", "arguments": json.dumps({"port": 8081})})
    assert reg.appels == [("serve_website", {"port": 8081})]


def test_un_json_texte_invalide_est_refuse():
    pont, reg, _ = _pont()
    with pytest.raises(CodexMCPBridgeError):
        _appel(pont, INVOKE_TOOL_NAME,
               {"name": "serve_website", "arguments": "{pas du json"})
    assert reg.appels == []


def test_sans_arguments_l_appel_passe_quand_meme():
    """Beaucoup d'outils n'en prennent aucun."""
    pont, reg, _ = _pont()
    _appel(pont, INVOKE_TOOL_NAME, {"name": "serve_website"})
    assert reg.appels == [("serve_website", {})]


# ── La raison, dans le code ──────────────────────────────────────────────────


def test_la_raison_du_lot_est_datee_dans_le_code():
    from pathlib import Path
    src = Path("src/llm/execution_router.py").read_text(encoding="utf-8")
    entete = src[src.index("LOT Z34 phase 1"):][:1800]
    assert "78 k tokens" in entete
    assert "serve_website" in entete


def test_la_condition_non_negociable_est_ecrite():
    from pathlib import Path
    src = Path("src/llm/codex_mcp_bridge.py").read_text(encoding="utf-8")
    entete = src[src.index("LOT Z34 phase 2 — l'invocateur"):][:1600]
    assert "after_call" in entete
    assert "ledger" in entete
