"""Lot L5-2 - une mission ne deplace jamais la fenetre de l'utilisateur.

--- Ce que la mesure du 16 septembre etablit (et corrige) ---

Ma premiere version de ce lot visait `electron/singleInstanceLifecycle.ts` et
`applySecondInstanceRequest`. **C'etait le mauvais endroit ET le mauvais depot** :
`IDELauncherService.ensure_ready` (`ide_launcher.py` l.153-170) ne lance AUCUN
processus quand une IDE est deja connectee. Il appelle
`workspace_router(requested)` -> `get_ide_bridge().navigate(...)` (l.84) et rend
`reused=True` ; le test `test_connected_instance_reuses_transport_and_switches_
workspace` le fige (`starter.calls == []`). **Le vol de fenetre est un `navigate`
sur la connexion proprietaire, cote Lumena.**

Perimetre REEL, mesure : **une seule porte**.

- Le catalogue declare `navigate` en `mission_policy: forbidden`, mais
  `ensure_ready` appelle la METHODE PYTHON du pont : la politique ne protege que la
  voie `ide__*`.
- `ide_launch` est **deja protege** : `tool_registry.py` l.2772-2776 route tout nom
  `ide_*` vers `IDEExternalToolProvider.execute` (donc `derive_mission_scope` +
  `authorize_mission_call`), et `_launch_spec` declare
  `mission_policy=MissionPolicy.FORBIDDEN`.
- Les 32 autres facades `ide_*` sont masquees inconditionnellement par
  `ExternalToolView.native_items()` (tout `is_ide_tool_name`). Sur 33 facades
  declarees, **1 seule** est dans `self.tools`.
- **`lumena_ide` (nomme `cursor_ide_local` jusqu'au 24/09) echappe a tout cela** : `is_ide_tool_name` rend False, c'est
  un handler natif V2 ordinaire, **sans aucune politique de mission**, present dans
  `self.tools`, avec `allowed_tools=None` en mission normale et absent de
  `RECOVERY_ALLOWED_TOOLS`. Ses actions `ensure_open` et `ensure_workspace`
  atteignent `ensure_ready(workspace)` -> `navigate` sur le pont PARTAGE.

Decision de perimetre : `status` et `focus` restent autorises - ils ne deplacent pas
le workspace. La voie A exige que la fenetre ne BASCULE pas, pas qu'une mission soit
aveugle.

La preuve porte sur le fait que le LANCEUR N'EST JAMAIS ATTEINT, pas sur le seul
texte du refus : un refus rendu apres l'appel laisserait le mal se produire.
"""
from __future__ import annotations

import pytest

from src.reasoning.handlers import computer_use
from src.reasoning.handlers.context import HandlerContext
from src.utils.external_tool_names import is_ide_tool_name


class _LanceurEspion:
    """Compte les appels : le defaut, c'est d'ATTEINDRE `ensure_ready`."""

    def __init__(self) -> None:
        self.appels: list = []

    # IDE-3 : le vrai `ensure_ready` accepte `dedicated` depuis L5-3b-bis. Un double
    # dont la signature est en retard sur le contrat ne teste pas le contrat.
    async def ensure_ready(self, workspace=None, *, dedicated: bool = False):
        self.appels.append(workspace)
        raise AssertionError("ensure_ready ne doit jamais etre atteint en mission")

    async def observe(self):
        return None


@pytest.fixture
def monde(tmp_path, monkeypatch):
    racine = tmp_path / "lumena"
    ws = racine / "workspace"
    (ws / "missions" / "m1").mkdir(parents=True)
    espion = _LanceurEspion()
    monkeypatch.setattr(computer_use, "_get_cursor_ide_launcher", lambda: espion)
    return {"racine": racine, "ws": ws, "mission": ws / "missions" / "m1",
            "espion": espion}


def _ctx_mission(m) -> HandlerContext:
    ctx = HandlerContext.for_testing(lumena_root=m["racine"], runtime_root=m["ws"])
    ctx.is_mission_run = True
    ctx.runtime_task_id = "task_m1"
    ctx.mission_workspace = "missions/m1"
    return ctx


def _ctx_chat(m) -> HandlerContext:
    return HandlerContext.for_testing(lumena_root=m["racine"], runtime_root=m["ws"])


# ── 1. Le fait qui fonde le lot ─────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["ensure_workspace", "ensure_open"])
async def test_une_mission_ne_peut_pas_deplacer_la_fenetre(monde, action):
    """Le lanceur ne doit JAMAIS etre atteint : sinon `navigate` a deja eu lieu."""
    m = monde
    r = await computer_use.lumena_ide(
        _ctx_mission(m), action=action, workspace_path=str(m["mission"]),
    )
    assert r.success is False
    assert m["espion"].appels == [], (
        "le lanceur a ete atteint : `navigate` aurait deplace la fenetre"
    )


@pytest.mark.asyncio
async def test_le_motif_du_refus_reste_lisible(monde):
    """Un refus muet serait pire : le modele doit savoir POURQUOI."""
    m = monde
    r = await computer_use.lumena_ide(
        _ctx_mission(m), action="ensure_workspace", workspace_path=str(m["mission"]),
    )
    texte = (str(r.output) + str(getattr(r, "error", "") or "")).lower()
    assert "mission" in texte, texte[:200]


# ── 2. Le fait mesure qui rend le defaut possible ───────────────────────────

def test_lumena_ide_echappe_au_routage_des_outils_ide():
    """C'est CE fait qui laisse la porte ouverte : pas de politique de mission."""
    assert is_ide_tool_name("lumena_ide") is False
    assert is_ide_tool_name("ide_launch") is True


# ── 3. Ce que le lot ne doit PAS changer ────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["ensure_workspace", "ensure_open"])
async def test_hors_mission_le_comportement_ne_bouge_pas(monde, action):
    """En conversation, l'IDE reste pilotable : le lanceur est bien atteint."""
    m = monde
    with pytest.raises(AssertionError, match="ne doit jamais etre atteint"):
        await computer_use.lumena_ide(
            _ctx_chat(m), action=action, workspace_path=str(m["mission"]),
        )
    assert len(m["espion"].appels) == 1


@pytest.mark.asyncio
async def test_une_action_invalide_reste_refusee_comme_avant(monde):
    """Caracterisation : le garde ne remplace pas la validation d'action."""
    m = monde
    r = await computer_use.lumena_ide(_ctx_mission(m), action="n_importe_quoi")
    assert r.success is False
    assert m["espion"].appels == []
