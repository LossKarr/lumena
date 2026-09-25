"""Lot L5-3c-2 - en mode dedie, le lanceur observe SON instance, pas la proprietaire.

L5-3b-bis fait LANCER un processus dedie. Mais la boucle d'attente qui suit
interroge `readiness_probe()`, c'est-a-dire `_probe_bridge` (`ide_launcher.py`
l.64), qui lit `get_ide_bridge()` - la connexion PROPRIETAIRE, donc la fenetre de
l'utilisateur, ouverte sur SON projet.

Consequence mesuree : `_matches(last, requested)` compare le workspace de
l'utilisateur au dossier de mission. Il ne peut jamais correspondre.
**`ensure_ready(dedicated=True)` ne peut donc finir qu'en `timeout`**, meme quand
l'instance de mission est parfaitement vivante et appairee.

--- Ce que la mesure du 22 septembre etablit (verifie, pas suppose) ---

1. **La sonde par defaut ne peut pas changer de signature.** `_Probe`
   (`test_conn1b_ide_launcher.py` l.49) declare `async def __call__(self)`, sans
   parametre, et sert de doublure a 18 appels d'`ensure_ready`. Le lot ajoute donc
   une sonde SEPAREE, nommee et injectable, employee uniquement en mode dedie.
2. **La forme canonique sert a CHERCHER, jamais a repondre.** `_matches` (l.294)
   compare `readiness.workspace == requested`, et `_validate_workspace` produit un
   `Path.resolve()` **sans** `normcase`. Une sonde qui renverrait la forme
   normalisee ne correspondrait jamais sur Windows.
3. **Ce que `snapshot_pour_workspace` designe est deja authentifie** : il ne rend
   qu'une connexion validee par `_snapshot_de`. `transport_connected` et
   `authenticated` sont donc des faits observes, pas des suppositions.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from src.tools.ide_launcher import IDELauncherService, IDEReadiness
from tests.tools.test_conn1b_ide_launcher import (
    _Discovery,
    _Probe,
    _Starter,
    _installation,
    _readiness,
)

pytestmark = pytest.mark.asyncio


def _lanceur(tmp_path, *, projet, sonde_workspace=None, routes=None):
    async def route(chemin: Path) -> bool:
        if routes is not None:
            routes.append(chemin)
        return True

    return IDELauncherService(
        discovery=_Discovery(_installation(tmp_path / "ide")),
        readiness_probe=_Probe(_readiness(connected=True, handshake=True, workspace=projet)),
        workspace_router=route,
        process_starter=_Starter(),
        workspace_probe=sonde_workspace,
        sleep=lambda _delai: __import__("asyncio").sleep(0),
        timeout=1,
    )


# ── 1. Le fait qui fonde le lot ─────────────────────────────────────────────

async def test_en_mode_dedie_l_instance_de_mission_est_observee(tmp_path):
    """Aujourd'hui la boucle regarde la proprietaire : `timeout` garanti."""
    projet = tmp_path / "projet-de-charles"
    mission = tmp_path / "dossier-de-mission"
    projet.mkdir()
    mission.mkdir()
    demandes: list[Path] = []

    async def sonde_workspace(chemin: Path) -> IDEReadiness:
        demandes.append(chemin)
        return _readiness(connected=True, handshake=True, workspace=mission)

    resultat = await _lanceur(tmp_path, projet=projet, sonde_workspace=sonde_workspace) \
        .ensure_ready(mission, dedicated=True)

    assert demandes == [mission], "la boucle interroge encore la proprietaire"
    assert resultat.available and resultat.workspace == mission
    assert resultat.process_started


async def test_la_sonde_dediee_recoit_le_dossier_demande(tmp_path):
    """Sans le dossier, elle ne peut pas designer la bonne instance."""
    projet = tmp_path / "projet"
    mission = tmp_path / "mission"
    projet.mkdir()
    mission.mkdir()
    vus: list[Path] = []

    async def sonde_workspace(chemin: Path) -> IDEReadiness:
        vus.append(chemin)
        return _readiness(connected=True, handshake=True, workspace=mission)

    await _lanceur(tmp_path, projet=projet, sonde_workspace=sonde_workspace) \
        .ensure_ready(mission, dedicated=True)
    assert vus and all(chemin == mission for chemin in vus)


# ── 2. La sonde par defaut, branchee sur le pont ────────────────────────────

async def test_la_sonde_par_defaut_designe_l_instance_du_dossier(tmp_path, monkeypatch):
    mission = tmp_path / "mission"
    mission.mkdir()
    from src.tools import ide_launcher as module

    demande = {}

    def pont_doublure():
        def designer(chemin):
            demande["chemin"] = chemin
            return SimpleNamespace(workspace_path=str(mission), state="ready")
        return SimpleNamespace(snapshot_pour_workspace=designer)

    monkeypatch.setattr("src.tools.ide_bridge.get_ide_bridge", pont_doublure)
    etat = await module._probe_bridge_workspace(mission)

    assert Path(demande["chemin"]) == mission
    assert etat.transport_connected and etat.handshake_received and etat.authenticated
    assert etat.workspace == mission


async def test_la_sonde_par_defaut_rend_un_chemin_comparable_a_matches(tmp_path, monkeypatch):
    """Piege mesure : `_matches` compare a un `resolve()` SANS `normcase`."""
    mission = tmp_path / "Mission-Avec-Majuscules"
    mission.mkdir()
    from src.tools import ide_launcher as module

    monkeypatch.setattr(
        "src.tools.ide_bridge.get_ide_bridge",
        lambda: SimpleNamespace(
            snapshot_pour_workspace=lambda _c: SimpleNamespace(
                workspace_path=str(mission), state="ready")),
    )
    etat = await module._probe_bridge_workspace(mission)
    assert module.IDELauncherService._matches(etat, module.Path(mission).resolve())


async def test_sans_instance_sur_ce_dossier_la_sonde_ne_ment_pas(tmp_path, monkeypatch):
    mission = tmp_path / "mission"
    mission.mkdir()
    from src.tools import ide_launcher as module

    monkeypatch.setattr(
        "src.tools.ide_bridge.get_ide_bridge",
        lambda: SimpleNamespace(snapshot_pour_workspace=lambda _c: None),
    )
    etat = await module._probe_bridge_workspace(mission)
    assert not etat.transport_connected and not etat.handshake_received
    assert not etat.authenticated and etat.workspace is None


# ── 3. Ce que le lot ne doit PAS changer ────────────────────────────────────

async def test_hors_mode_dedie_la_sonde_dediee_n_est_jamais_appelee(tmp_path):
    """Non-regression : le chemin normal continue d'observer la proprietaire."""
    projet = tmp_path / "projet"
    projet.mkdir()
    appels: list[Path] = []

    async def sonde_workspace(chemin: Path) -> IDEReadiness:
        appels.append(chemin)
        return _readiness(connected=True, handshake=True, workspace=chemin)

    routes: list[Path] = []
    resultat = await _lanceur(tmp_path, projet=projet, sonde_workspace=sonde_workspace,
                              routes=routes).ensure_ready(projet)

    assert appels == []
    assert resultat.reused and resultat.available


# ── 4. LOT L5-4b : la confiance du dossier de mission ───────────────────────

async def test_le_mode_dedie_pre_approuve_le_dossier_de_la_mission(tmp_path):
    """Sans cela, toute ecriture est refusee : « explicit trust required ».

    Mesure du canari reel du 23 septembre : l'instance de mission s'ouvre bien
    (`authenticated_ready` en 2,2 s) mais son magasin de confiance est vierge, son
    `userData` etant dedie. Personne n'est devant sa fenetre pour l'approuver.
    """
    mission = tmp_path / "mission"
    mission.mkdir()
    demarreur = _Starter()

    async def sonde_workspace(chemin: Path) -> IDEReadiness:
        return _readiness(connected=True, handshake=True, workspace=mission)

    service = IDELauncherService(
        discovery=_Discovery(_installation(tmp_path / "ide")),
        readiness_probe=_Probe(_readiness(connected=True, handshake=True, workspace=tmp_path)),
        workspace_router=lambda _c: __import__("asyncio").sleep(0, result=True),
        process_starter=demarreur,
        workspace_probe=sonde_workspace,
        sleep=lambda _d: __import__("asyncio").sleep(0),
        timeout=1,
    )
    await service.ensure_ready(mission, user_data_dir=tmp_path / "profil", dedicated=True)

    environnement = demarreur.calls[-1][2]
    assert environnement.get("LUMENA_IDE_TRUST_WORKSPACE") == "1"
    assert environnement.get("LUMENA_IDE_WORKSPACE") == str(mission)
    assert environnement.get("LUMENA_IDE_USER_DATA")


async def test_hors_mode_dedie_aucune_confiance_n_est_pre_approuvee(tmp_path):
    """La fenetre de l'utilisateur ne doit jamais recevoir cette variable."""
    projet = tmp_path / "projet"
    projet.mkdir()
    demarreur = _Starter()
    service = IDELauncherService(
        discovery=_Discovery(_installation(tmp_path / "ide")),
        readiness_probe=_Probe(_readiness(connected=False, handshake=False, workspace=None),
                               _readiness(connected=True, handshake=True, workspace=projet)),
        workspace_router=lambda _c: __import__("asyncio").sleep(0, result=True),
        process_starter=demarreur,
        sleep=lambda _d: __import__("asyncio").sleep(0),
        timeout=1,
    )
    await service.ensure_ready(projet)

    assert "LUMENA_IDE_TRUST_WORKSPACE" not in demarreur.calls[-1][2]
