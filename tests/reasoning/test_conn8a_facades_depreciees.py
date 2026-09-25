"""Lot CONN-8a - les 33 facades statiques `ide_*` cessent d'etre proposees.

Exigence de CONN-8 : « **depreciation mesuree des 33 facades, sans rupture des
historiques** ».

--- La mesure, faite AVANT de toucher quoi que ce soit ---

Comptage sur **cinq fichiers de journaux**, du 15 au 24 septembre 2026 :

    APPELS REELS de facades ide_*  :  0
    appels du catalogue ide__*     : 10   (navigate, list_files, open_file,
                                           get_status, get_state, read_file)

**Zero appel en cinq mois.** Un premier comptage en annoncait 26 « vues » - avec
exactement **4 occurrences chacune**, ce que leur uniformite trahissait : c'etaient
des LISTAGES au chargement des handlers, pas des appels. Le motif de recherche etait
trop large.

Ces facades sont donc du code mort depuis que le catalogue dynamique existe
(CONN-3C). Elles doublonnent `ide__*` et ajoutent 33 noms au prompt du modele, avec
l'ambiguite qui va avec : `ide_editor_save` ou `ide__editor_save` ?

--- ET LA DEPRECIATION EST DEJA FAITE : ce lot ne fait que la FIGER ---

Mesure de suivi : les 33 facades ne sont ni dans `registre.tools`, ni proposees au
modele. `ExternalToolView` (`external_tool_view.py`) projette hors de `self.tools`
TOUT nom reconnu par `is_ide_tool_name` - ce qui les inclut. Elles sont declarees,
enregistrees au registre v2, et **debranchees de la surface du modele** depuis
CONN-3C.

Les zero appel des journaux ne traduisent donc pas une desuetude : c'etait deja
inatteignable. **Ce lot n'ecrit aucun code** - il empeche ce fait de redevenir un
secret, et il documente pourquoi on ne SUPPRIME pas :

1. `ide_launch` est reutilise par `_launch_spec` (`ide_capabilities.py` l.42) pour
   son schema : le supprimer casserait le catalogue dynamique lui-meme ;
2. deux gels comptent les 33 (`test_conn0a_ide_connection_baseline`,
   `test_l5_2_mission_ne_prend_pas_la_fenetre`) ;
3. la declaration reste la source de verite des schemas historiques.
"""
from __future__ import annotations

import pytest

from src.reasoning.handlers.ide import get_ide_handler_defs


def _facades() -> set:
    return {item.name for item in get_ide_handler_defs()}


# ── 1. Le fait qui fonde le lot ─────────────────────────────────────────────

def test_les_facades_ne_sont_plus_proposees_au_modele(tmp_path):
    """Zero appel en cinq mois : elles n'ont plus a occuper le prompt."""
    from src.reasoning.tool_registry import ToolRegistry

    registre = ToolRegistry(lumena=None, lumena_root=tmp_path)
    description = registre.get_tools_description()

    proposees = sorted(nom for nom in _facades() if f"- {nom}(" in description)
    assert proposees == [], f"{len(proposees)} facade(s) encore proposee(s) : {proposees[:6]}"


def test_elles_sont_DEBRANCHEES_de_la_surface_du_modele(tmp_path):
    """Constat, et non intention : `ExternalToolView` les projette deja hors de `tools`.

    Ecrit d'abord a l'envers (« elles restent appelables »), ce test a echoue - et
    c'est ce qui a revele que la depreciation etait DEJA faite par CONN-3C.
    """
    from src.reasoning.tool_registry import ToolRegistry

    registre = ToolRegistry(lumena=None, lumena_root=tmp_path)
    encore_la = sorted(nom for nom in _facades() if nom in registre.tools)
    assert encore_la == [], f"{len(encore_la)} facade(s) encore exposee(s) : {encore_la[:6]}"


def test_la_declaration_reste_INTACTE():
    """Debrancher n'est pas supprimer : les 33 definitions restent la source des schemas."""
    noms = _facades()
    assert "ide_launch" in noms, "le schema du catalogue dynamique en depend"
    assert len(noms) == 33


def test_le_compte_de_33_est_inchange():
    """Deux gels existants le figent - ce lot ne doit pas les faire tomber."""
    assert len(_facades()) == 33


# ── 2. Ce que le lot ne doit PAS cacher ─────────────────────────────────────

def test_c_est_ExternalToolView_qui_les_debranche():
    """Nommer le MECANISME, pour qu'un futur lot ne le reinvente pas.

    J'ai commence par ecrire un masquage dans `tool_registry` avant de mesurer qu'il
    existait deja. Ce test dit ou il vit.
    """
    from pathlib import Path

    from src.reasoning.external_tool_view import ExternalToolView

    assert hasattr(ExternalToolView, "native_items")
    source = (Path(__file__).resolve().parents[2] / "src" / "reasoning"
              / "external_tool_view.py").read_text(encoding="utf-8")
    assert "is_ide_tool_name" in source, (
        "la vue ne filtre plus sur le nom : les facades redeviendraient visibles"
    )


@pytest.mark.parametrize("outil", ["read_file", "run_command", "write_file", "grep_search"])
def test_les_outils_natifs_restent_proposes(tmp_path, outil):
    """Non-regression : le lot ne vise QUE les facades `ide_*`."""
    from src.reasoning.tool_registry import ToolRegistry

    registre = ToolRegistry(lumena=None, lumena_root=tmp_path)
    assert f"- {outil}(" in registre.get_tools_description()


def test_ide_launch_reste_le_schema_du_catalogue():
    """`_launch_spec` lit la facade `ide_launch` pour construire son schema.

    La cacher est sans effet ici - la supprimer casserait le catalogue dynamique
    lui-meme. C'est la premiere des trois raisons de ne pas supprimer.
    """
    from src.tools.ide_capabilities import _launch_spec
    from src.tools.ide_discovery import IDEInstallation
    from pathlib import Path

    fausse = IDEInstallation(
        mode="packaged", source="test", root=Path("."), manifest_path=Path("x.json"),
        version="1.0.0", platform="windows-x64", executable=Path("x.exe"),
        command=("x.exe",), manifest_sha256="0" * 64, artifact_sha256="0" * 64,
    )
    spec = _launch_spec(fausse)
    assert spec.name == "ide_launch"
