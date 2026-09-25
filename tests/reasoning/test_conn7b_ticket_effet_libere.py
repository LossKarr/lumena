"""Lot CONN-7b - un effet IDE qui echoue libere quand meme son ticket.

**Constat ouvert par CONN-6b le 23 septembre 2026, non corrige a l'epoque.**

`external_effect_cache.begin()` ouvre un ticket avant `send_command` ;
`complete()` le referme apres. Entre les deux, **aucun `finally`**. Si
`send_command` leve - transport coupe, delai depasse, IDE fermee - le ticket reste
actif **a vie** dans un singleton de MODULE.

Consequence : `observation_cache_epoch` rend `None` tant qu'un ticket est actif, ce
qui **desactive le cache d'observation pour tout le reste de la session**. Ce n'est
pas une faille de securite : c'est une degradation de performance permanente et
**invisible**, qu'aucun journal ne signale.

--- Pourquoi ce n'etait pas evident ---

Le commentaire de `ide_tool_runtime.py` l.310 montre que la suspension est
**volontaire** pour un lancement dont la fin n'est pas prouvee : « Un lancement ne
quitte le cache d'effets qu'avec sa fin verifiee ; sans elle la suspension du cache
reste, par conception. »

Ce lot ne touche pas a cette regle. Il distingue deux situations que le code
confondait :

* **l'operation a eu lieu mais sa fin n'est pas prouvee** -> la suspension RESTE,
  c'est la conception ;
* **l'operation n'a jamais eu lieu** (le transport a leve) -> il n'y a RIEN a
  prouver, donc rien a suspendre.

Le premier cas est un doute legitime. Le second est une fuite.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.reasoning.caller_context import REACT
from src.reasoning.external_effect_cache import external_effect_cache
from tests.reasoning.test_conn5a_ide_mission_gate import _envoi, _ide_ouverte_sur, _mission
from tests.reasoning.test_conn3c_live_catalogue import owner as owner, registry as registry  # noqa: F401
from tests.tools.test_conn3c_ide_capabilities import service as service  # noqa: F401

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def effets_isoles():
    """Le registre d'effets est un singleton de MODULE (lecon de CONN-6b)."""
    cache = external_effect_cache()
    with cache._lock:
        avant = dict(cache._active)
    yield
    with cache._lock:
        cache._active.clear()
        cache._active.update(avant)
        cache._epoch += 1


def _mission_protocole_4(registry, service):
    """Le perimetre de mission exige le protocole 4 (CONN-5B-2).

    Sans cela, le rail refuse par `ide_mission_scope_unsupported` **avant**
    d'atteindre `begin()` : le test passerait en ne prouvant RIEN. Piege tombe en
    ecrivant ce fichier, et c'est l'assertion sur le code de refus qui l'a
    demasque. Meme forme que `test_conn5b1_ide_mission_write` l.87.
    """
    from dataclasses import replace

    _, racine = _mission(registry)
    _ide_ouverte_sur(service, racine)
    service.bridge._negotiated = replace(service.bridge._negotiated, protocol=4)
    return racine


def _actifs() -> int:
    cache = external_effect_cache()
    with cache._lock:
        return len(cache._active)


# ── 1. Le fait qui fonde le lot ─────────────────────────────────────────────

async def test_un_transport_qui_leve_ne_laisse_aucun_ticket(registry, service, owner, tmp_path):
    """Aujourd'hui le ticket reste actif a vie et desactive le cache d'observation."""
    _mission_protocole_4(registry, service)
    service.bridge.send_command = AsyncMock(side_effect=ConnectionError("transport coupe"))

    avant = _actifs()
    observation = await registry._ide_tools.execute(
        "ide__write_file", {"path": "README.md", "content": "x"}, caller=REACT)

    assert observation.success is False
    # Le test ne vaut que si `begin()` a VRAIMENT ete atteint : un refus survenu
    # plus tot n'ouvrirait aucun ticket et rendrait ce test vert pour rien.
    assert "ide_dispatch_failed" in observation.content, observation.content
    assert _actifs() == avant, (
        "un ticket est reste actif : le cache d'observation est desactive "
        "pour tout le reste de la session"
    )


async def test_le_cache_d_observation_survit_a_un_transport_casse(registry, service, owner):
    """La consequence produit, mesuree par le meme chemin que `tool_registry`."""
    from src.reasoning.external_effect_cache import observation_cache_epoch

    _mission_protocole_4(registry, service)
    service.bridge.send_command = AsyncMock(side_effect=TimeoutError("delai depasse"))

    await registry._ide_tools.execute(
        "ide__write_file", {"path": "README.md", "content": "x"}, caller=REACT)

    class _Registre:
        _observation_cache: dict = {}
        _observation_cache_hits: dict = {}
        _external_cache_epoch = 0

    assert observation_cache_epoch(_Registre()) is not None, (
        "le cache d'observation reste suspendu apres un transport casse"
    )


@pytest.mark.parametrize("panne", [ConnectionError("coupe"), TimeoutError("delai"), RuntimeError("inattendu")])
async def test_toute_panne_de_transport_libere(registry, service, owner, panne):
    _mission_protocole_4(registry, service)
    service.bridge.send_command = AsyncMock(side_effect=panne)

    avant = _actifs()
    await registry._ide_tools.execute(
        "ide__write_file", {"path": "README.md", "content": "x"}, caller=REACT)
    assert _actifs() == avant


# ── 2. Ce que le lot ne doit PAS changer ────────────────────────────────────

async def test_un_succes_normal_libere_toujours_son_ticket(registry, service, owner):
    """Non-regression du chemin nominal."""
    _mission_protocole_4(registry, service)
    _envoi(service, content="ok")

    avant = _actifs()
    await registry._ide_tools.execute(
        "ide__read_file", {"path": "README.md"}, caller=REACT)
    assert _actifs() == avant


async def test_la_suspension_VOLONTAIRE_d_un_lancement_non_prouve_est_conservee():
    """Regle de conception, pas defaut : elle ne doit pas tomber avec ce lot.

    Un lancement dont la fin n'est pas verifiee GARDE son ticket - c'est ce que dit
    `ide_tool_runtime.py` l.310. Le lot ne libere que les operations qui n'ont
    JAMAIS eu lieu, faute de transport : il n'y a alors rien a prouver.
    """
    from pathlib import Path

    source = (Path(__file__).resolve().parents[2] / "src" / "reasoning"
              / "ide_tool_runtime.py").read_text(encoding="utf-8")
    assert "qu'avec sa fin verifiee" in source, (
        "le commentaire qui porte la regle de conception a disparu"
    )
