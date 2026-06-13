"""Phase I-7 Fix L — Verrouille _force_activate_if_needed.

Bug : `handle_run_mcp_autonomy` retournait `autonomy_ready_to_use` quand
le MCP était en statut INSTALLED sans jamais déclencher l'activation
réelle. Conséquence : process MCP jamais spawné, tools jamais registrés.

Fix L : helper `_force_activate_if_needed` qui s'intercale avant chaque
return final du autonomy. Si target_server_id est en `installed`,
appelle ActivationService.activate() inline.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.mcp.react_integration import (
    MCPReActIntegration,
    MCPReActIntegrationDeps,
)


def _make_integration(catalog=None, activation_service=None):
    deps = MCPReActIntegrationDeps(
        catalog=catalog,
        activation_service=activation_service,
    )
    return MCPReActIntegration(deps)


class TestForceActivateIfNeeded:

    def test_installed_triggers_activation(self):
        """Statut INSTALLED → activation_service.activate appelé."""
        entry = MagicMock()
        entry.status = MagicMock(value="installed")
        catalog = MagicMock()
        catalog.get_server = MagicMock(return_value=entry)

        activation = MagicMock()
        activation.activate = MagicMock(return_value=MagicMock(success=True))

        integ = _make_integration(catalog=catalog, activation_service=activation)
        payload = {"target_server_id": "slack", "recommendation_code": "autonomy_ready_to_use"}
        out = integ._force_activate_if_needed(payload)

        # Fix N : activate doit recevoir approval_result forgé APPROVED
        assert activation.activate.call_count == 1
        call_args, call_kwargs = activation.activate.call_args
        assert call_args == ("slack",)
        assert "approval_result" in call_kwargs
        _ar = call_kwargs["approval_result"]
        assert _ar.decision.value == "approved"
        assert _ar.args["server_id"] == "slack"
        assert _ar.args["action"] == "activate"
        assert out["force_activate_ok"] is True
        assert out["recommendation_code"] == "autonomy_activated"
        assert out["next_step"] == "call_target_tool"

    def test_active_skipped(self):
        """Déjà ACTIVE → pas d'appel activate (idempotent / efficace)."""
        entry = MagicMock()
        entry.status = MagicMock(value="active")
        catalog = MagicMock()
        catalog.get_server = MagicMock(return_value=entry)
        activation = MagicMock()

        integ = _make_integration(catalog=catalog, activation_service=activation)
        payload = {"target_server_id": "slack"}
        out = integ._force_activate_if_needed(payload)

        activation.activate.assert_not_called()
        assert "force_activate_attempted" not in out

    def test_declared_skipped(self):
        """DECLARED (pas encore installé) → pas d'activation forcée."""
        entry = MagicMock()
        entry.status = MagicMock(value="declared")
        catalog = MagicMock()
        catalog.get_server = MagicMock(return_value=entry)
        activation = MagicMock()

        integ = _make_integration(catalog=catalog, activation_service=activation)
        payload = {"target_server_id": "slack"}
        out = integ._force_activate_if_needed(payload)

        activation.activate.assert_not_called()

    def test_no_target_server_id_noop(self):
        """Sans target_server_id, helper retourne payload inchangé."""
        integ = _make_integration(catalog=MagicMock(), activation_service=MagicMock())
        payload = {"recommendation_code": "autonomy_would_run"}
        out = integ._force_activate_if_needed(payload)
        assert out == payload

    def test_no_catalog_noop(self):
        """Sans catalog, helper ne casse pas."""
        integ = _make_integration(catalog=None, activation_service=MagicMock())
        payload = {"target_server_id": "slack"}
        out = integ._force_activate_if_needed(payload)
        assert out == payload

    def test_no_activation_service_noop(self):
        """Sans activation_service, helper ne casse pas."""
        integ = _make_integration(catalog=MagicMock(), activation_service=None)
        payload = {"target_server_id": "slack"}
        out = integ._force_activate_if_needed(payload)
        assert out == payload

    def test_get_server_exception_noop(self):
        """Si catalog.get_server explose, helper ne casse pas le retour autonomy."""
        catalog = MagicMock()
        catalog.get_server = MagicMock(side_effect=RuntimeError("disk failure"))
        integ = _make_integration(catalog=catalog, activation_service=MagicMock())
        payload = {"target_server_id": "slack", "recommendation_code": "autonomy_ready_to_use"}
        out = integ._force_activate_if_needed(payload)
        assert out["recommendation_code"] == "autonomy_ready_to_use"

    def test_activate_exception_does_not_break_payload(self):
        """Si activate() explose, on log dans payload mais on retourne propre."""
        entry = MagicMock()
        entry.status = MagicMock(value="installed")
        catalog = MagicMock()
        catalog.get_server = MagicMock(return_value=entry)
        activation = MagicMock()
        activation.activate = MagicMock(side_effect=RuntimeError("port_busy"))

        integ = _make_integration(catalog=catalog, activation_service=activation)
        payload = {"target_server_id": "slack", "recommendation_code": "autonomy_ready_to_use"}
        out = integ._force_activate_if_needed(payload)

        assert out["force_activate_attempted"] is True
        # Fix N : on expose le type d'exception réel + message (plus "exception" générique)
        assert out["force_activate_error"] == "RuntimeError"
        assert "port_busy" in out["force_activate_error_msg"]
        # Le recommendation_code n'a pas été changé en "autonomy_activated"
        assert out["recommendation_code"] == "autonomy_ready_to_use"

    def test_activate_failure_logs_but_continues(self):
        """activate retourne success=False → log mais retour reste utilisable."""
        entry = MagicMock()
        entry.status = MagicMock(value="installed")
        catalog = MagicMock()
        catalog.get_server = MagicMock(return_value=entry)
        activation = MagicMock()
        activation.activate = MagicMock(return_value=MagicMock(success=False))

        integ = _make_integration(catalog=catalog, activation_service=activation)
        payload = {"target_server_id": "slack", "recommendation_code": "autonomy_ready_to_use"}
        out = integ._force_activate_if_needed(payload)

        assert out["force_activate_attempted"] is True
        assert out["force_activate_ok"] is False
        # Pas d'upgrade du recommendation_code car activation a échoué
        assert out["recommendation_code"] == "autonomy_ready_to_use"

    def test_fix_n_failure_reason_exposed(self):
        """Fix N : quand activate retourne success=False, force_activate_reason
        expose la raison (ex: runner_start_failed:FileNotFoundError:node.exe)."""
        entry = MagicMock()
        entry.status = MagicMock(value="installed")
        catalog = MagicMock()
        catalog.get_server = MagicMock(return_value=entry)
        activation = MagicMock()
        activation.activate = MagicMock(
            return_value=MagicMock(
                success=False,
                reason="runner_start_failed:FileNotFoundError:node.exe introuvable",
            )
        )

        integ = _make_integration(catalog=catalog, activation_service=activation)
        payload = {"target_server_id": "slack"}
        out = integ._force_activate_if_needed(payload)

        assert out["force_activate_ok"] is False
        assert "force_activate_reason" in out
        assert "runner_start_failed" in out["force_activate_reason"]
        assert "node.exe" in out["force_activate_reason"]

    def test_status_string_direct_works(self):
        """status peut être directement une string (pas un enum)."""
        entry = MagicMock()
        entry.status = "installed"  # Direct string, pas un enum avec .value
        catalog = MagicMock()
        catalog.get_server = MagicMock(return_value=entry)
        activation = MagicMock()
        activation.activate = MagicMock(return_value=MagicMock(success=True))

        integ = _make_integration(catalog=catalog, activation_service=activation)
        payload = {"target_server_id": "slack"}
        out = integ._force_activate_if_needed(payload)

        # Fix N : signature avec approval_result forgé
        assert activation.activate.call_count == 1
        call_args, call_kwargs = activation.activate.call_args
        assert call_args == ("slack",)
        assert call_kwargs["approval_result"].decision.value == "approved"
        assert out["force_activate_ok"] is True
