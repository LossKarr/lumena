"""Lot L5-3b - une mission obtient SA propre instance IDE.

**Lot MANQUANT de mon decoupage de la voie A, decouvert a la mesure le 16/09.**

--- Le fait fondateur, prouve ligne a l'appui ---

`authorize_mission_call` (`ide_mission_scope.py` l.200-201) refuse tout appel des
que le workspace de l'IDE differe du `mission_root` :
`ide_mission_workspace_mismatch`. Or **rien en production n'ouvre l'IDE sur un
dossier de mission** :

- `navigate` est `mission_policy: forbidden` au catalogue ;
- `ide_launch` l'est aussi (`_launch_spec`, `ide_capabilities.py` l.38-46), et tout
  nom `ide_*` passe par le provider qui applique `authorize_mission_call` ;
- `lumena_ide` (alors nomme `cursor_ide_local`) a ete ferme en mission par **L5-2**.

Donc en production, tout appel `ide__*` d'une mission echouait - sauf si
l'utilisateur avait par hasard ouvert son IDE sur ce dossier precis.

**C'est le canari qui produisait lui-meme l'etat que la production ne sait pas
produire** : `verify-conn2a-bridge.mjs` l.207 execute `navigate` vers
`missionPrepared.missionRoot` DEPUIS L'EXTERIEUR, avant de lancer `mission_rail`
(l.211) ; son commentaire l.202-203 l'assume. **Motif identique a CONN-5D-1**, ou
les tests poussaient eux-memes le contexte proprietaire absent en production. Un
canari vert ne prouvait donc pas qu'une mission fonctionne : il prouvait que le
RAIL fonctionne quand l'IDE est deja au bon endroit.

--- Ce que le lot fait, et ce qu'il ne fait pas ---

Ouverture **PARESSEUSE** : a la premiere commande `ide__*` d'une mission, si l'IDE
disponible n'est pas sur le `mission_root`, une instance **dediee** est demandee au
lanceur, avec le `user_data_dir` livre par **L5-3** (sans lui, Electron refuse la
seconde instance : le verrou d'instance unique est lie au `userData`). Une mission
qui n'utilise aucun outil `ide__*` ne paie rien.

**L'instance de l'utilisateur n'est ni reutilisee, ni routee, ni focalisee** : la
regle posee par L5-2 n'est pas affaiblie. Sans instance possible, la mission echoue
proprement.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from src.reasoning.caller_context import REACT
from src.runtime.context import pop_runtime_context
from tests.reasoning.test_conn5a_ide_mission_gate import (
    _contexte,
    _envoi,
    _ide_ouverte_sur,
    _mission,
)
from tests.reasoning.test_conn3c_live_catalogue import owner as owner, registry as registry  # noqa: F401
from tests.tools.test_conn3c_ide_capabilities import service as service  # noqa: F401


class _LanceurEspion:
    """Observe ce que le rail DEMANDE, sans jamais lancer d'Electron."""

    def __init__(self, *, disponible: bool = False) -> None:
        self.demandes: list[tuple] = []
        self._disponible = disponible

    async def ensure_ready(self, workspace=None, *, user_data_dir=None, dedicated=False):
        # LOT L5-3b-bis : `dedicated` ajoute le 17/09 apres mesure. Sans lui,
        # `ensure_ready` se contentait de ROUTER l'IDE deja connectee (`navigate` sur
        # la proprietaire) au lieu d'en lancer une : la mission deplacait la fenetre
        # de l'utilisateur. L'espion l'enregistre pour que le test l'EXIGE.
        self.demandes.append((workspace, user_data_dir, dedicated))
        return SimpleNamespace(
            available=self._disponible, state="unavailable", error="aucune instance",
            workspace=workspace, reused=False, authenticated=False,
        )


@pytest.fixture
def espion(monkeypatch):
    valeur = _LanceurEspion()
    monkeypatch.setattr("src.tools.ide_launcher.get_ide_launcher", lambda: valeur)
    return valeur


# ── 1. Le fait qui fonde le lot ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_une_mission_demande_sa_propre_instance(registry, service, owner, espion, tmp_path):
    """L'IDE de l'utilisateur est ailleurs : la mission doit demander la SIENNE."""
    _, root = _mission(registry)
    projet_de_charles = tmp_path / "projet-de-charles"
    projet_de_charles.mkdir()
    _ide_ouverte_sur(service, projet_de_charles)

    await registry.execute("ide__read_file", {"path": "README.md"}, caller=REACT)

    assert espion.demandes, (
        "aucune instance dediee demandee : la mission reste tributaire de l'IDE "
        "de l'utilisateur, ouverte sur un autre dossier"
    )
    workspace, user_data_dir, dedicated = espion.demandes[0]
    assert Path(str(workspace)).resolve() == root.resolve()
    assert user_data_dir is not None, (
        "sans `user_data_dir`, Electron refuse la seconde instance (L5-3)"
    )
    assert dedicated is True, (
        "sans `dedicated`, `ensure_ready` ROUTE l'IDE de l'utilisateur au lieu d'en "
        "lancer une : la mission lui prendrait sa fenetre (L5-3b-bis)"
    )


@pytest.mark.asyncio
async def test_la_fenetre_de_l_utilisateur_n_est_jamais_sollicitee(registry, service, owner,
                                                                  espion, tmp_path):
    """Regle de L5-2, non affaiblie : rien ne part vers l'IDE de l'utilisateur."""
    _, _root = _mission(registry)
    projet_de_charles = tmp_path / "projet-de-charles"
    projet_de_charles.mkdir()
    _ide_ouverte_sur(service, projet_de_charles)
    send = _envoi(service, content="# contenu")

    await registry.execute("ide__read_file", {"path": "README.md"}, caller=REACT)

    send.assert_not_awaited()


# ── 2. Ce que le lot ne doit PAS changer ────────────────────────────────────

@pytest.mark.asyncio
async def test_ide_deja_sur_le_dossier_de_mission_rien_n_est_lance(registry, service, owner,
                                                                   espion):
    """Non-regression du cas CONN-5A : la lecture part, sans ouvrir d'instance."""
    _, root = _mission(registry)
    _ide_ouverte_sur(service, root)
    send = _envoi(service, content="# Herbier")

    observation = await registry.execute("ide__read_file", {"path": "README.md"}, caller=REACT)

    assert observation.success is True
    send.assert_awaited_once()
    assert espion.demandes == [], "aucune instance ne doit etre demandee inutilement"


@pytest.mark.asyncio
async def test_hors_mission_aucune_instance_n_est_demandee(registry, service, espion):
    """En conversation, le comportement ne bouge pas."""
    token = _contexte(role="owner")
    try:
        send = _envoi(service, connected=True)
        observation = await registry.execute("ide__get_status", {})
    finally:
        pop_runtime_context(token)

    assert observation.success is True
    send.assert_awaited_once()
    assert espion.demandes == []
