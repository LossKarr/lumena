"""Renommage `cursor_ide_local` -> `lumena_ide` (24/09/2026).

--- Pourquoi le nom comptait ---

Run reel du 24/09 a 18 h 24. Charles : « ouvre une autre instance ide avec ton
workspace dedans ». Premier raisonnement de Lumena, verbatim : « Losskarr veut une
nouvelle instance de l'IDE (VS Code) ». Elle lance `code --new-window`. A l'iteration 7
elle voit trois fenetres « Lumena IDE », en deduit qu'un IDE integre existe, interroge
`discover_tools`... et ne le trouve pas. Elle retourne a VS Code.

L'outil qu'elle cherchait etait la, visible, dans son catalogue. Il s'appelait
`cursor_ide_local` — un nom herite du fork Cursor dont l'IDE est issue. IDE-3 avait
corrige la description ; le NOM, lui, continuait de designer autre chose que ce que
l'outil pilote.

--- Le contrat que ce fichier fige ---

**Un seul nom est declare.** Deux entrees identiques au catalogue forceraient le modele
a arbitrer entre elles — exactement le doute qu'on vient de supprimer.

**L'ancien nom reste executable.** Il vit dans les conversations passees, la memoire de
Lumena et les logs. Le registre le traduit au point d'entree de `execute`, donc avant
le lease, les gardes de perimetre et le controle de politique de mission : rien ne se
contourne en appelant l'ancien nom. Le fuzzy du registre (cutoff 0,75) ne l'aurait pas
rattrape — les deux noms sont trop eloignes l'un de l'autre.
"""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.reasoning.react import ToolRegistry, ReActLoop


class _DummyTools:
    def get_tools_description(self) -> str:
        return "- lumena_ide(action, workspace_path, create_if_missing)"

    async def execute(self, _name: str, _args):
        return "ok"


async def _dummy_llm(_messages, **kwargs):
    return "ACTION: FINAL\nACTION_INPUT: done"


# ── 1. Un seul nom au catalogue ─────────────────────────────────────────────

def test_le_catalogue_ne_declare_que_le_nouveau_nom():
    registry = ToolRegistry()
    assert "lumena_ide" in registry.tools
    assert "cursor_ide_local" not in registry.tools, (
        "l'ancien nom reste executable, mais ne doit plus etre PROPOSE : "
        "deux entrees identiques feraient arbitrer le modele entre elles"
    )


def test_le_fuzzy_seul_n_aurait_pas_sauve_l_ancien_nom():
    """C'est ce fait qui rend l'alias explicite necessaire, et non superflu."""
    from src.llm.output_normalizer import auto_fix_action_name

    registry = ToolRegistry()
    corrige = auto_fix_action_name("cursor_ide_local", set(registry.tools.keys()))
    assert corrige != "lumena_ide", (
        "le fuzzy rattrape maintenant l'ancien nom : verifier si l'alias explicite "
        "est devenu du code mort avant de s'y fier"
    )


# ── 2. Les deux noms atteignent le meme handler ─────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("appele", ["lumena_ide", "cursor_ide_local"])
async def test_status_repond_par_les_deux_noms(monkeypatch, tmp_path: Path, appele):
    registry = ToolRegistry(lumena_root=tmp_path)

    import src.reasoning.handlers.computer_use as _cu_mod

    class _Readiness:
        transport_connected = True
        handshake_received = True
        authenticated = False
        workspace = tmp_path

    class _Launcher:
        async def observe(self):
            return _Readiness()

    monkeypatch.setattr(_cu_mod, "_get_cursor_ide_launcher", lambda: _Launcher())

    obs = await registry.execute(appele, {"action": "status"})
    assert obs.success, obs.content
    assert "connected=True" in obs.content
    assert "handshake=True" in obs.content


@pytest.mark.asyncio
async def test_l_ancien_nom_n_echappe_pas_au_garde_de_mission(monkeypatch, tmp_path: Path):
    """L'ancien nom mene au MEME handler, donc aux memes gardes : il n'existe pas de
    seconde implementation a garder en phase. Sans cette preuve, le renommage pourrait
    laisser une porte derobee sur le verrou L5-2 (une mission ne deplace jamais la
    fenetre de l'utilisateur)."""
    import src.reasoning.handlers.computer_use as _cu_mod

    class _LanceurInterdit:
        async def ensure_ready(self, workspace=None, *, dedicated: bool = False):
            raise AssertionError("le lanceur a ete atteint depuis une mission")

        async def observe(self):
            return None

    monkeypatch.setattr(_cu_mod, "_get_cursor_ide_launcher", lambda: _LanceurInterdit())

    from src.reasoning.handlers.context import HandlerContext
    ctx = HandlerContext.for_testing(lumena_root=tmp_path, runtime_root=tmp_path)
    ctx.is_mission_run = True
    r = await _cu_mod.cursor_ide_local(ctx, action="new_instance")
    assert r.success is False
    assert "mission" in (str(r.output) + str(getattr(r, "error", "") or "")).lower()


# ── 3. Ce que le renommage ne doit pas changer ──────────────────────────────

def test_lumena_ide_reste_un_natif_ordinaire():
    """`is_ide_tool_name` mord sur le prefixe `ide_`. Un nom qui aurait commence par
    `ide_` aurait basculé l'outil vers le routage IDE authentifie et l'aurait rendu
    inutilisable au chat — c'etait le seul vrai danger du renommage."""
    from src.utils.external_tool_names import is_ide_tool_name
    assert is_ide_tool_name("lumena_ide") is False
    assert is_ide_tool_name("ide_launch") is True


def test_le_garde_anti_hallucination_compte_l_ouverture_comme_une_action():
    """L'ancien nom figurait dans `_HC_TOOLS_ANY_ACTION`. Renommer sans l'y ajouter
    aurait fait cesser d'ouvrir l'IDE de compter comme une action reelle."""
    from src.reasoning.hallucination_guard import _HC_TOOLS_ANY_ACTION
    assert "lumena_ide" in _HC_TOOLS_ANY_ACTION


def test_react_prompt_n_injecte_plus_de_section_ide_statique():
    """P2: la section statique cursor_ide_context a ete retiree ; elle est remplacee
    par `ide_runtime_context`, qui n'apparait que si `tools.ide_context` est pose."""
    loop = ReActLoop(llm_chat_func=_dummy_llm, tools=_DummyTools())

    prompt_no_ide = loop._build_react_prompt("cree un projet web fullstack")
    assert "PRIORITE IDE LOCAL (cursor-ide-local)" not in prompt_no_ide
    assert "ACTION: lumena_ide" not in prompt_no_ide
    assert "ACTION: cursor_ide_local" not in prompt_no_ide

    # Lot RF-3 (2026-08-27) : le corps de `_build_react_prompt` a quitte `react.py`
    # pour `src/prompts/react_prompt.py`. Preuve comportementale equivalente dans
    # tests/reasoning/test_rf3_react_prompt_extraction.py.
    import inspect
    from src.prompts import react_prompt

    src = inspect.getsource(react_prompt.construire_prompt_react)
    assert "ide_runtime_context" in src, "ide_runtime_context must be in the prompt builder"
