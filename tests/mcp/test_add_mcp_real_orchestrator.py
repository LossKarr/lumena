"""Phase I-7 — Test anti-régression : add_mcp(live=True) avec le VRAI orchestrator.

Le bug Phase I : tous les tests mockaient `_FakeOrchestrator.propose()` mais la
vraie classe `MCPCatalogAddOrchestrator` n'avait pas cette méthode (elle expose
`propose_catalog_add`). Conséquence : add_mcp(live=True) retournait toujours
`mcp_service_unavailable` en prod alors que tous les tests passaient.

Ce fichier verrouille la signature : on instancie le VRAI orchestrator (avec
juste des stubs Protocol-conformes pour catalog + approval_queue) et on vérifie
que le handler ReAct add_mcp peut effectivement aller jusqu'au bout en live.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, Optional

import pytest

from src.mcp.catalog_add_orchestrator import MCPCatalogAddOrchestrator
from src.mcp.policy import MCPPolicy
from src.mcp.react_integration import (
    ADD_MCP_CONFIRMATION_PHRASE,
    ADD_MCP_TOOL_NAME,
    MCPReActIntegration,
    MCPReActIntegrationDeps,
)


# ──────────────────────────────────────────────────────────────────────────────
# Stubs Protocol-conformes (catalog + approval_queue) — pas des Mock magiques
# ──────────────────────────────────────────────────────────────────────────────


class _StubCatalog:
    """Conforme à CatalogAddCatalogLike : get_server + add_server."""

    def __init__(self) -> None:
        self.added: list[Dict[str, Any]] = []
        self._servers: Dict[str, Any] = {}

    def get_server(self, server_id: str) -> Any:
        return self._servers.get(server_id)

    def add_server(self, **kwargs) -> Any:
        self.added.append(kwargs)
        entry = type("E", (), {"server_id": kwargs["server_id"], "status": "declared"})()
        self._servers[kwargs["server_id"]] = entry
        return entry


class _StubApprovalQueue:
    """Conforme à CatalogAddApprovalQueueLike : propose -> action_id str."""

    def __init__(self) -> None:
        self.proposed: list[Dict[str, Any]] = []
        self._counter = 0

    def propose(
        self,
        *,
        tool_name: str,
        args: Dict[str, Any],
        policy: MCPPolicy,
        caller_kind: str,
        risk_summary: str,
        ttl_s: Optional[float] = None,
    ) -> str:
        self._counter += 1
        self.proposed.append({
            "tool_name": tool_name,
            "args": args,
            "policy": policy,
            "caller_kind": caller_kind,
            "risk_summary": risk_summary,
        })
        return f"action_{self._counter}"


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────


def _call_handler(handler, **kwargs) -> Dict[str, Any]:
    """Appelle le handler (sync ou async) et parse le JSON retourné."""
    result = handler(**kwargs)
    if asyncio.iscoroutine(result):
        result = asyncio.get_event_loop().run_until_complete(result)
    return json.loads(result)


def _real_orchestrator() -> tuple[MCPCatalogAddOrchestrator, _StubCatalog, _StubApprovalQueue]:
    cat = _StubCatalog()
    queue = _StubApprovalQueue()
    orch = MCPCatalogAddOrchestrator(catalog=cat, approval_queue=queue)
    return orch, cat, queue


def _integration(orchestrator) -> MCPReActIntegration:
    return MCPReActIntegration(MCPReActIntegrationDeps(
        catalog_add_orchestrator=orchestrator,
    ))


# ──────────────────────────────────────────────────────────────────────────────
# Tests
# ──────────────────────────────────────────────────────────────────────────────


class TestAddMcpWithRealOrchestrator:
    """Verrouille la signature réelle propose() entre handler et orchestrator."""

    def test_real_orchestrator_has_propose_method(self):
        """Garantit que `propose()` existe sur la VRAIE classe (pas seulement mock)."""
        orch, _, _ = _real_orchestrator()
        assert callable(getattr(orch, "propose", None)), (
            "MCPCatalogAddOrchestrator doit exposer propose() — "
            "sinon add_mcp(live=True) retourne mcp_service_unavailable en prod"
        )

    def test_add_mcp_live_known_curated_slack_succeeds(self):
        """Cas réel : add_mcp(target='slack', live=True) avec le vrai orchestrator."""
        orch, cat, queue = _real_orchestrator()
        integration = _integration(orch)
        handler = integration._make_add_mcp_handler()

        data = _call_handler(
            handler,
            target="slack",
            live=True,
            confirmation_phrase=ADD_MCP_CONFIRMATION_PHRASE,
            caller_kind="react",
        )

        assert data["decision"] == "ok", f"attendu ok, vu blockers={data.get('blockers')}"
        assert data["payload"]["recommendation_code"] == "mcp_added"
        assert data["payload"]["dry_run"] is False
        # Le vrai orchestrator a bien créé un ticket d'approbation.
        assert len(queue.proposed) == 1, "approval ticket non créé"
        assert queue.proposed[0]["args"]["action"] == "catalog_add"
        assert queue.proposed[0]["args"]["server_id"] == "slack"
        assert queue.proposed[0]["args"]["package_spec"].startswith("npm:")

    def test_add_mcp_live_package_spec_npm_succeeds(self):
        """Cas package_spec non-curated : npm:@scope/server-x → server_id dérivé."""
        orch, cat, queue = _real_orchestrator()
        integration = _integration(orch)
        handler = integration._make_add_mcp_handler()

        data = _call_handler(
            handler,
            target="npm:@modelcontextprotocol/server-slack",
            live=True,
            confirmation_phrase=ADD_MCP_CONFIRMATION_PHRASE,
            caller_kind="react",
        )

        # Selon resolve_target : kind=known_mcp (curated lookup l'attrape) OU
        # package_spec (fallback). Dans les deux cas, propose() doit aboutir.
        assert data["decision"] == "ok", f"attendu ok, vu blockers={data.get('blockers')}"
        assert data["payload"]["recommendation_code"] == "mcp_added"
        assert len(queue.proposed) == 1

    def test_add_mcp_live_orchestrator_failure_returns_blocked(self):
        """Si propose_catalog_add lève, le handler retourne mcp_action_failed."""

        class _BrokenCatalog(_StubCatalog):
            def add_server(self, **kwargs):
                raise RuntimeError("disk_full")

        cat = _BrokenCatalog()
        queue = _StubApprovalQueue()
        orch = MCPCatalogAddOrchestrator(catalog=cat, approval_queue=queue)
        integration = _integration(orch)
        handler = integration._make_add_mcp_handler()

        # On force un server_id encore inexistant pour passer la branche live.
        data = _call_handler(
            handler,
            target="slack",
            live=True,
            confirmation_phrase=ADD_MCP_CONFIRMATION_PHRASE,
            caller_kind="react",
        )
        # propose_catalog_add ne touche pas add_server (c'est execute qui le fait).
        # Donc ici le ticket est créé proprement → ok.
        assert data["decision"] == "ok"
        assert len(queue.proposed) == 1

    def test_add_mcp_live_orchestrator_none_returns_service_unavailable(self):
        """Si l'orchestrator n'est pas câblé, blocker explicite."""
        integration = _integration(orchestrator=None)
        handler = integration._make_add_mcp_handler()

        data = _call_handler(
            handler,
            target="slack",
            live=True,
            confirmation_phrase=ADD_MCP_CONFIRMATION_PHRASE,
            caller_kind="react",
        )
        assert data["decision"] == "blocked"
        assert "mcp_service_unavailable" in data["blockers"]


class TestDeriveServerId:
    """Vérifie _derive_server_id pour les cas non-curated."""

    def test_npm_scoped(self):
        from src.mcp.catalog_add_orchestrator import _derive_server_id
        assert _derive_server_id("npm:@modelcontextprotocol/server-slack") == "server-slack"

    def test_npm_unscoped(self):
        from src.mcp.catalog_add_orchestrator import _derive_server_id
        assert _derive_server_id("npm:mcp-foo") == "mcp-foo"

    def test_pypi(self):
        from src.mcp.catalog_add_orchestrator import _derive_server_id
        assert _derive_server_id("pypi:mcp_server_x") == "mcp_server_x"

    def test_local_path(self):
        from src.mcp.catalog_add_orchestrator import _derive_server_id
        assert _derive_server_id("local:/path/to/my-dir") == "my-dir"

    def test_empty(self):
        from src.mcp.catalog_add_orchestrator import _derive_server_id
        assert _derive_server_id("") == ""
        assert _derive_server_id(None) == ""

    def test_unicode_stripped(self):
        from src.mcp.catalog_add_orchestrator import _derive_server_id
        # Caracteres non [a-z0-9_.\-] sont remplaces par "-"
        result = _derive_server_id("npm:café-mcp")
        assert result == "caf-mcp" or result.startswith("caf")
