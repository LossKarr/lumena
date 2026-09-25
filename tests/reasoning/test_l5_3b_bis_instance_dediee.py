"""Lot L5-3b-bis - la mission LANCE son instance, elle ne ROUTE pas celle de Charles.

**Correction d'un lot que j'avais signe la veille.** L5-3b faisait demander une
instance au lanceur ; il ne verifiait jamais ce que le lanceur en FAIT.

--- Le defaut, lu dans `ide_launcher.py` ---

`ensure_ready` traite une IDE deja connectee ainsi :

- l.168 : si elle est deja sur le workspace demande -> `reused=True`, rien a faire ;
- **l.171-188 : sinon, elle est CONNECTEE -> `workspace_router(requested)`**, et
  `_route_bridge_workspace` (l.81-85) fait `get_ide_bridge().navigate(...)` ;
- l.193 : un processus n'est lance **que si `not initial.transport_connected`**.

Donc en mission reelle - l'IDE de l'utilisateur ouverte sur SON projet - le chemin
pris n'est pas le lancement d'une seconde instance : c'est un `navigate` sur la
connexion proprietaire. **C'est exactement le vol de fenetre que L5-2 interdit.**

--- Pourquoi mes preuves de L5-3b ne l'ont pas vu ---

1. Le test de L5-3b remplacait `get_ide_launcher` par un ESPION : il prouvait que le
   rail *demande* une instance, jamais que la demande en *produit* une.
2. Le canari rendait 27/27 parce que son `navigate` EXTERNE (l.207) placait l'IDE sur
   le `mission_root` : `_matches` etait vrai et `ensure_ready` sortait en `reused`
   des la l.168, **sans jamais executer le code du lot**.

Le vert etait sincere et ne couvrait pas le chemin que le lot creait. Ici le lanceur
est donc le VRAI `IDELauncherService`, avec ses doubles injectes par constructeur.

--- Ce que ces tests exigent ---

La preuve porte sur **ce qui est atteint**, jamais sur le texte d'un refus (lecon de
L5-2) : le routeur ne doit JAMAIS etre appele, le demarreur doit l'etre.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from src.reasoning.caller_context import REACT
from tests.reasoning.test_conn5a_ide_mission_gate import (
    _envoi,
    _ide_ouverte_sur,
    _mission,
)
from tests.reasoning.test_conn3c_live_catalogue import owner as owner, registry as registry  # noqa: F401
from tests.tools.test_conn3c_ide_capabilities import service as service  # noqa: F401
from tests.tools.test_conn1b_ide_launcher import (  # noqa: F401
    _Discovery,
    _Probe,
    _Starter,
    _installation,
    _readiness,
)


@pytest.fixture
def lanceur(monkeypatch, tmp_path):
    """Le VRAI `IDELauncherService`, observe par ses points d'injection."""
    from src.tools.ide_launcher import IDELauncherService

    projet_de_charles = tmp_path / "projet-de-charles"
    projet_de_charles.mkdir()
    routes: list[Path] = []

    async def route(chemin: Path) -> bool:
        routes.append(chemin)
        return True

    demarreur = _Starter()
    service_reel = IDELauncherService(
        discovery=_Discovery(_installation(tmp_path / "ide")),
        readiness_probe=_Probe(
            _readiness(connected=True, handshake=True, workspace=projet_de_charles),
        ),
        workspace_router=route,
        process_starter=demarreur,
        sleep=lambda _delai: __import__("asyncio").sleep(0),
        timeout=1,
    )
    monkeypatch.setattr("src.tools.ide_launcher.get_ide_launcher", lambda: service_reel)
    return SimpleNamespace(
        service=service_reel, routes=routes, demarreur=demarreur,
        projet_de_charles=projet_de_charles,
    )


# ── 1. Le defaut : la fenetre de l'utilisateur est deplacee ─────────────────

@pytest.mark.asyncio
async def test_la_fenetre_de_l_utilisateur_n_est_jamais_routee(registry, service, owner, lanceur):
    """Regle de L5-2. Aujourd'hui `ensure_ready` route la proprietaire : rouge."""
    _mission(registry)
    _ide_ouverte_sur(service, lanceur.projet_de_charles)

    await registry.execute("ide__read_file", {"path": "README.md"}, caller=REACT)

    assert lanceur.routes == [], (
        "la mission a fait NAVIGUER l'IDE de l'utilisateur au lieu d'ouvrir la "
        f"sienne : {lanceur.routes}"
    )


@pytest.mark.asyncio
async def test_une_instance_dediee_est_reellement_lancee(registry, service, owner, lanceur):
    """`ensure_ready` ne lance que si AUCUNE IDE n'est connectee (l.193) : rouge."""
    _, root = _mission(registry)
    _ide_ouverte_sur(service, lanceur.projet_de_charles)

    await registry.execute("ide__read_file", {"path": "README.md"}, caller=REACT)

    assert len(lanceur.demarreur.calls) == 1, (
        "aucun processus lance : la mission reste tributaire de l'IDE de l'utilisateur"
    )
    commande, _cwd, environnement = lanceur.demarreur.calls[0]
    assert environnement["LUMENA_IDE_WORKSPACE"] == str(Path(root).resolve())
    assert environnement.get("LUMENA_IDE_USER_DATA"), (
        "sans `user_data_dir`, Electron refuse la seconde instance (L5-3)"
    )


@pytest.mark.asyncio
async def test_rien_ne_part_vers_l_ide_de_l_utilisateur(registry, service, owner, lanceur):
    """Aucune commande ne doit emprunter la connexion proprietaire."""
    _mission(registry)
    _ide_ouverte_sur(service, lanceur.projet_de_charles)
    send = _envoi(service, content="# contenu")

    await registry.execute("ide__read_file", {"path": "README.md"}, caller=REACT)

    send.assert_not_awaited()


# ── 2. Ce que le lot ne doit PAS changer ────────────────────────────────────

@pytest.mark.asyncio
async def test_hors_mission_ensure_ready_route_toujours(tmp_path):
    """Gel de CONN-1B (l.212-218) : le comportement par defaut ne bouge pas."""
    from src.tools.ide_launcher import IDELauncherService

    ancien = tmp_path / "ancien"
    nouveau = tmp_path / "nouveau"
    ancien.mkdir()
    nouveau.mkdir()
    routes: list[Path] = []

    async def route(chemin: Path) -> bool:
        routes.append(chemin)
        return True

    demarreur = _Starter()
    service_reel = IDELauncherService(
        discovery=_Discovery(_installation(tmp_path / "ide")),
        readiness_probe=_Probe(
            _readiness(connected=True, handshake=True, workspace=ancien),
            _readiness(connected=True, handshake=True, workspace=nouveau),
        ),
        workspace_router=route,
        process_starter=demarreur,
        sleep=lambda _delai: __import__("asyncio").sleep(0),
        timeout=1,
    )

    resultat = await service_reel.ensure_ready(nouveau)

    assert resultat.reused is True
    assert routes == [nouveau.resolve()], "le chemin normal doit continuer a router"
    assert demarreur.calls == [], "aucune instance supplementaire hors mission"
