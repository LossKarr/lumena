"""Lot L5-3c-1 - le pont sait DESIGNER l'instance ouverte sur un dossier donne.

L5-1b a fait coexister plusieurs IDE ; L5-1c a prouve qu'une commande part sur SA
connexion **quand on lui fournit le snapshot**. Le transport est donc pret. Ce qui
manque est la DECOUVERTE : personne ne sait dire « quelle instance est ouverte sur
ce dossier de mission ».

--- Ce que la mesure du 22 septembre etablit (verifie, pas suppose) ---

1. **Les trois maillons de L5-4 lisent la PROPRIETAIRE.** `_probe_bridge`
   (`ide_launcher.py` l.64) lit `get_ide_bridge()` ; `capture` et `is_current`
   (`ide_capabilities.py` l.62, 79, 91) s'ancrent sur `catalogue_snapshot()`, dont
   la docstring dit « decrit la PROPRIETAIRE ». Une mission ne peut donc jamais
   voir son instance, meme lancee : `ensure_ready(dedicated=True)` ne peut finir
   qu'en `timeout`.
2. **Le fait est deja porte, sans aucune E/S reseau** : `NegotiatedSession`
   (`ide_protocol.py` l.108) porte `workspace_path`, renseigne a la negociation et
   mis a jour par `update_workspace`. Designer une instance ne coute donc **aucune
   commande** et ne peut pas echouer sur un IDE occupe.
3. **La resolution doit regarder la proprietaire EN PLUS du registre**, exactement
   comme `_connexion_pour_snapshot` (l.316) : le gel
   `test_conn3c_ide_snapshot_binding` construit ses ponts en ecrivant directement
   l'etat et laisse `_connexions` VIDE.
4. **Chaque candidate passe par `_snapshot_de`** : une connexion tombee, non
   appairee ou dont la generation a change ne doit jamais etre designee. La
   validation est celle qui existe deja, pas une seconde regle.
5. **Gel CONN-0A : 41 methodes `async` sur `IDEBridge`.** L'aide est SYNCHRONE -
   elle ne lit que de l'etat en memoire.
6. **La comparaison est canonique, jamais textuelle.** `canonical_workspace` est la
   seule forme comparable (liens resolus, casse normalisee) ; le chemin annonce par
   l'IDE n'est valide cote Lumena que comme texte.
"""
from __future__ import annotations

import ast
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.tools.ide_bridge import IDEBridge
from src.tools.ide_protocol import negotiate
from tests.tools.test_conn2b_ide_protocol import hello

RACINE = Path(__file__).resolve().parents[2]
PONT = RACINE / "src" / "tools" / "ide_bridge.py"


def _session(chemin, *, instance="02" * 16):
    """Une negociation complete annoncant `chemin` comme workspace."""
    message = hello()
    message["ide"]["instance_id"] = instance
    message["workspace"] = {"id": "04" * 32, "path": str(chemin)}
    session, _ = negotiate(message, message["session_id"])
    return session


def _pont_avec(proprietaire=None, autres=()):
    """Pont unitaire : etat ecrit directement, comme le gel conn3c."""
    pont = IDEBridge()
    pont._pairing = SimpleNamespace(is_current=lambda _: True)
    if proprietaire is not None:
        pont._negotiated = proprietaire
        pont._session = SimpleNamespace(session_id=proprietaire.session_id)
        pont._ws = object()
        pont._connected = True
    for session in autres:
        from src.tools.ide_bridge import IDEConnection

        connexion = IDEConnection()
        connexion._negotiated = session
        connexion._session = SimpleNamespace(session_id=session.session_id)
        connexion._ws = object()
        connexion._connected = True
        pont._connexions[session.instance_id] = connexion
    return pont


# ── 1. Le fait qui fonde le lot ─────────────────────────────────────────────

def test_l_instance_de_la_mission_est_designee_et_pas_celle_de_l_utilisateur(tmp_path):
    """Aujourd'hui rien ne sait repondre a cette question."""
    projet = tmp_path / "projet-utilisateur"
    mission = tmp_path / "dossier-de-mission"
    projet.mkdir()
    mission.mkdir()
    vue_utilisateur = _session(projet)
    vue_mission = _session(mission, instance="0a" * 16)
    pont = _pont_avec(vue_utilisateur, [vue_mission])

    assert pont.snapshot_pour_workspace(str(mission)) is vue_mission
    assert pont.snapshot_pour_workspace(str(projet)) is vue_utilisateur


def test_la_proprietaire_est_trouvable_meme_sans_registre(tmp_path):
    """Le gel conn3c ecrit l'etat directement et laisse `_connexions` vide."""
    projet = tmp_path / "projet"
    projet.mkdir()
    vue = _session(projet)
    pont = _pont_avec(vue)
    assert not pont._connexions
    assert pont.snapshot_pour_workspace(str(projet)) is vue


# ── 2. Ce qui ne doit JAMAIS etre designe ───────────────────────────────────

def test_un_dossier_sans_instance_ne_designe_rien(tmp_path):
    projet = tmp_path / "projet"
    ailleurs = tmp_path / "ailleurs"
    projet.mkdir()
    ailleurs.mkdir()
    pont = _pont_avec(_session(projet))
    assert pont.snapshot_pour_workspace(str(ailleurs)) is None


@pytest.mark.parametrize("valeur", ["", "   ", "relatif/dossier", None, 42, "abs\x00olu"])
def test_un_chemin_sans_forme_canonique_ne_designe_rien(tmp_path, valeur):
    projet = tmp_path / "projet"
    projet.mkdir()
    pont = _pont_avec(_session(projet))
    assert pont.snapshot_pour_workspace(valeur) is None


@pytest.mark.parametrize("champ,valeur", [
    ("_connected", False), ("_ws", None), ("_session", None), ("_pairing", None),
])
def test_une_connexion_non_authentifiee_n_est_jamais_designee(tmp_path, champ, valeur):
    """La validation est `_snapshot_de`, pas une seconde regle plus laxiste."""
    projet = tmp_path / "projet"
    projet.mkdir()
    pont = _pont_avec(_session(projet))
    setattr(pont, champ, valeur)
    assert pont.snapshot_pour_workspace(str(projet)) is None


def test_la_comparaison_est_canonique_et_non_textuelle(tmp_path):
    """Un separateur final ou une casse differente designent la MEME instance."""
    projet = tmp_path / "projet"
    projet.mkdir()
    pont = _pont_avec(_session(projet))
    vue = pont.snapshot_pour_workspace(str(projet))
    assert vue is not None
    assert pont.snapshot_pour_workspace(str(projet) + os.sep) is vue
    if sys.platform == "win32":
        assert pont.snapshot_pour_workspace(str(projet).upper()) is vue


# ── 3. Ce que le lot ne doit PAS changer ────────────────────────────────────

def test_le_defaut_reste_la_proprietaire(tmp_path):
    """`catalogue_snapshot` continue de decrire la fenetre de l'utilisateur."""
    projet = tmp_path / "projet"
    mission = tmp_path / "mission"
    projet.mkdir()
    mission.mkdir()
    vue_utilisateur = _session(projet)
    pont = _pont_avec(vue_utilisateur, [_session(mission, instance="0a" * 16)])
    assert pont.catalogue_snapshot() is vue_utilisateur


def test_le_gel_conn0a_reste_a_41_methodes_async():
    """L'aide de designation est SYNCHRONE : elle ne lit que de la memoire."""
    arbre = ast.parse(PONT.read_text(encoding="utf-8"))
    classe = next(n for n in arbre.body
                  if isinstance(n, ast.ClassDef) and n.name == "IDEBridge")
    asynchrones = [n.name for n in classe.body if isinstance(n, ast.AsyncFunctionDef)]
    assert len(asynchrones) == 41, asynchrones
