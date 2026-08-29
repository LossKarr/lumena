"""Run du 2026-08-29 — le workspace nomme dans le message etait ignore.

L'utilisateur a ecrit « dans workspace/relevebank ». Le chat a resolu sur
`workspace/2026-08-29` (reason=default_fallback_default_workspace).

`resolve_workspace_for_request` ne lit JAMAIS le message : elle regarde
`requested_workspace`, le fichier actif et les onglets ouverts. Or le message
etait disponible au point de decision — le bloc d'observabilite juste en
dessous le LISAIT deja, pour emettre un warning.

C'est la classe de defaut fermee par Z41 dans `project_registry`, sur un
SECOND chemin que Z41 ne couvrait pas.
"""

from __future__ import annotations

import dataclasses

import pytest

from src.runtime.workspace_policy import WorkspaceResolution
from web.routes import chat as chat_mod
from web.routes import deps as deps_mod


class _Req:
    def __init__(self, message):
        self.message = message
        self.workspace_policy = "default"
        self.workspace_path = None
        self.active_file_path = None
        self.open_files = []


@pytest.fixture()
def racine(tmp_path, monkeypatch):
    """Un faux depot : <tmp>/workspace/<date> par defaut, <tmp>/workspace/relevebank existant."""
    defaut = tmp_path / "workspace" / "2026-08-29"
    defaut.mkdir(parents=True)
    (tmp_path / "workspace" / "relevebank").mkdir()

    def _resolveur(*_a, **_k):
        return WorkspaceResolution(
            workspace_policy="default",
            resolved_workspace=str(defaut),
            resolved_date="2026-08-29",
            resolution_reason="default_fallback_default_workspace",
            used_fallback=True,
        )

    monkeypatch.setattr(chat_mod, "WORKSPACE_POLICY_V2_ENABLED", True, raising=False)
    monkeypatch.setattr(deps_mod, "RUNTIME_AVAILABLE", True, raising=False)
    monkeypatch.setattr(deps_mod, "resolve_workspace_for_user", _resolveur, raising=False)
    return tmp_path


def _resoudre(message):
    return chat_mod._apply_workspace_policy(_Req(message), "web", {})


def test_LE_cas_du_run_le_workspace_nomme_gagne_sur_le_fallback(racine):
    r = _resoudre("continue le travail dans workspace/relevebank stp")
    assert r["workspace_path"].replace(chr(92), "/").endswith("workspace/relevebank")
    assert r["resolution_reason"] == "named_in_message"
    assert r["workspace_used_fallback"] is False


def test_un_dossier_qui_N_EXISTE_PAS_ne_detourne_rien(racine):
    """On ne cree jamais un workspace par inference : le fallback tient."""
    r = _resoudre("travaille dans workspace/jamais-cree")
    assert r["resolution_reason"] == "default_fallback_default_workspace"
    assert r["workspace_path"].replace(chr(92), "/").endswith("workspace/2026-08-29")


def test_un_message_sans_chemin_ne_change_rien(racine):
    r = _resoudre("bonjour, tu peux resumer la mission ?")
    assert r["resolution_reason"] == "default_fallback_default_workspace"


def test_une_resolution_DEJA_bonne_est_laissee_intacte(tmp_path, monkeypatch):
    """Le detournement ne vise QUE le fallback par defaut."""
    autre = tmp_path / "workspace" / "autre"
    autre.mkdir(parents=True)
    (tmp_path / "workspace" / "relevebank").mkdir()

    def _resolveur(*_a, **_k):
        return WorkspaceResolution(
            workspace_policy="explicit",
            resolved_workspace=str(autre),
            resolved_date="2026-08-29",
            resolution_reason="explicit_valid",
            used_fallback=False,
        )

    monkeypatch.setattr(chat_mod, "WORKSPACE_POLICY_V2_ENABLED", True, raising=False)
    monkeypatch.setattr(deps_mod, "RUNTIME_AVAILABLE", True, raising=False)
    monkeypatch.setattr(deps_mod, "resolve_workspace_for_user", _resolveur, raising=False)

    r = _resoudre("dans workspace/relevebank")
    assert r["resolution_reason"] == "explicit_valid"
    assert r["workspace_path"].replace(chr(92), "/").endswith("workspace/autre")


def test_un_resolveur_exotique_ne_fait_pas_tomber_le_chat(tmp_path, monkeypatch):
    """Defensif : si la resolution n'est pas un dataclass, on ne casse rien."""
    (tmp_path / "workspace" / "relevebank").mkdir(parents=True)
    defaut = tmp_path / "workspace" / "2026-08-29"
    defaut.mkdir(parents=True)

    class _Exotique:
        resolution_reason = "default_fallback_default_workspace"
        resolved_workspace = str(defaut)
        resolved_date = "2026-08-29"
        workspace_policy = "default"
        used_fallback = True

    monkeypatch.setattr(chat_mod, "WORKSPACE_POLICY_V2_ENABLED", True, raising=False)
    monkeypatch.setattr(deps_mod, "RUNTIME_AVAILABLE", True, raising=False)
    monkeypatch.setattr(deps_mod, "resolve_workspace_for_user",
                        lambda *a, **k: _Exotique(), raising=False)
    r = _resoudre("dans workspace/relevebank")
    assert r["workspace_path"] == str(defaut)
