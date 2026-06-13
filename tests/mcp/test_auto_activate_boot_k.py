"""Phase I-7 Fix K — Verrouille l'auto-activate au boot.

Bug d'origine : ActivationService.activate() existait mais n'était JAMAIS
appelé au boot. Conséquence : un MCP en statut INSTALLED restait en
statut INSTALLED indéfiniment, son process Node ne démarrait jamais, ses
tools (slack__list_channels, etc.) n'apparaissaient jamais dans le
ToolRegistry.

Fix K : au boot, scan du catalog pour les MCPs INSTALLED → activate()
pour chacun → tools dispos dès le démarrage.

Ces tests valident la logique du scan + appel activate, indépendamment
de l'init full lifespan.
"""
from __future__ import annotations

from typing import Any, List
from unittest.mock import MagicMock

import pytest


def _scan_and_activate(catalog, activation_service, status_installed_value) -> tuple[int, int]:
    """Reproduit la logique du Fix K en standalone pour tester."""
    from src.mcp.server_catalog import ServerStatus
    try:
        installed = catalog.list_servers(status_filter=ServerStatus.INSTALLED)
    except Exception:
        installed = []
    ok, ko = 0, 0
    for entry in installed:
        sid = getattr(entry, "server_id", None)
        if not isinstance(sid, str):
            continue
        try:
            res = activation_service.activate(sid)
            if bool(getattr(res, "success", False)):
                ok += 1
            else:
                ko += 1
        except Exception:
            ko += 1
    return ok, ko


class TestAutoActivateBootScan:

    def test_activates_all_installed_mcps(self):
        """Tous les MCPs INSTALLED sont activés en un scan."""
        slack_entry = MagicMock(server_id="slack")
        github_entry = MagicMock(server_id="github")

        catalog = MagicMock()
        catalog.list_servers = MagicMock(return_value=[slack_entry, github_entry])

        activation = MagicMock()
        activation.activate = MagicMock(
            return_value=MagicMock(success=True)
        )

        ok, ko = _scan_and_activate(catalog, activation, "installed")

        assert ok == 2
        assert ko == 0
        assert activation.activate.call_count == 2
        # L'ordre n'a pas d'importance, on vérifie les sid passés
        called_sids = {c.args[0] for c in activation.activate.call_args_list}
        assert called_sids == {"slack", "github"}

    def test_no_installed_no_call(self):
        """Catalog vide → zéro activate, zéro crash."""
        catalog = MagicMock()
        catalog.list_servers = MagicMock(return_value=[])
        activation = MagicMock()

        ok, ko = _scan_and_activate(catalog, activation, "installed")

        assert ok == 0
        assert ko == 0
        activation.activate.assert_not_called()

    def test_exception_per_server_does_not_stop_scan(self):
        """Si Slack plante, GitHub doit quand même être tenté."""
        slack_entry = MagicMock(server_id="slack")
        github_entry = MagicMock(server_id="github")

        catalog = MagicMock()
        catalog.list_servers = MagicMock(return_value=[slack_entry, github_entry])

        activation = MagicMock()
        # Slack explose, GitHub réussit
        activation.activate = MagicMock(side_effect=[
            RuntimeError("slack crashed"),
            MagicMock(success=True),
        ])

        ok, ko = _scan_and_activate(catalog, activation, "installed")

        assert ok == 1
        assert ko == 1
        assert activation.activate.call_count == 2  # GitHub tenté malgré crash Slack

    def test_failed_activation_counted_as_ko(self):
        """activate retourne success=False → compté comme KO sans planter."""
        slack_entry = MagicMock(server_id="slack")
        catalog = MagicMock()
        catalog.list_servers = MagicMock(return_value=[slack_entry])
        activation = MagicMock()
        activation.activate = MagicMock(
            return_value=MagicMock(success=False, reason="port_busy")
        )

        ok, ko = _scan_and_activate(catalog, activation, "installed")

        assert ok == 0
        assert ko == 1

    def test_list_servers_crash_returns_empty(self):
        """Si catalog.list_servers explose, fallback liste vide (pas de boot bloqué)."""
        catalog = MagicMock()
        catalog.list_servers = MagicMock(side_effect=RuntimeError("disk failure"))
        activation = MagicMock()

        ok, ko = _scan_and_activate(catalog, activation, "installed")

        assert ok == 0
        assert ko == 0

    def test_invalid_server_id_skipped(self):
        """Entry sans server_id valide est ignorée."""
        bad_entry = MagicMock(server_id=None)
        good_entry = MagicMock(server_id="github")

        catalog = MagicMock()
        catalog.list_servers = MagicMock(return_value=[bad_entry, good_entry])

        activation = MagicMock()
        activation.activate = MagicMock(return_value=MagicMock(success=True))

        ok, ko = _scan_and_activate(catalog, activation, "installed")

        assert ok == 1
        assert ko == 0
        activation.activate.assert_called_once_with("github")


class TestEnvFlagGate:
    """Vérifie que l'env var LUMENA_MCP_AUTOACTIVATE_AT_BOOT pilote."""

    def test_flag_default_on_in_lifespan(self):
        """Fix K activé par défaut (sans env var explicite)."""
        # Lecture directe du source pour s'assurer que la default est True.
        from pathlib import Path
        content = Path("web/routes/lifespan.py").read_text(encoding="utf-8")
        assert 'LUMENA_MCP_AUTOACTIVATE_AT_BOOT", True' in content, (
            "Fix K doit avoir default=True (activé) — sinon les MCPs installed "
            "restent invisibles au boot"
        )

    def test_fix_k_marker_present(self):
        """Le commentaire 'Fix K' doit être trouvable pour audit."""
        from pathlib import Path
        content = Path("web/routes/lifespan.py").read_text(encoding="utf-8")
        # Fix Q : Fix K déplacé après Phase I-6 et renommé Fix K+Q
        assert (
            "Fix K (Phase I-7)" in content or "Fix K+Q (Phase I-7)" in content
        )
        assert "auto-activate au boot" in content
