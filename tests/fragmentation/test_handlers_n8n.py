"""Tests unitaires pour handlers/n8n.py — 17 handlers n8n."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.reasoning.handlers.context import HandlerContext
from src.reasoning.handlers.contracts import HandlerResult
from src.reasoning.handlers.n8n import (
    n8n_status_handler,
    n8n_list_workflows_handler,
    n8n_trigger_workflow_handler,
    n8n_trigger_webhook_handler,
    n8n_activate_workflow_handler,
    n8n_deactivate_workflow_handler,
    n8n_list_executions_handler,
    n8n_get_execution_handler,
    n8n_create_workflow_handler,
    n8n_delete_workflow_handler,
    n8n_update_workflow_handler,
    n8n_get_workflow_handler,
    n8n_list_node_types_handler,
    n8n_create_from_template_handler,
    n8n_list_templates_handler,
    n8n_search_online_templates_handler,
    n8n_import_online_template_handler,
    get_n8n_handler_defs,
)


@pytest.fixture
def ctx(tmp_path):
    return HandlerContext.for_testing(lumena_root=tmp_path, runtime_root=tmp_path / "workspace")


def _mock_bridge(**overrides):
    bridge = MagicMock()
    bridge.is_configured = True
    bridge.base_url = "http://localhost:5678"
    bridge.health = AsyncMock(return_value={"status": "ok"})
    bridge.list_workflows = AsyncMock(return_value=[
        {"id": "1", "name": "Email Quotidien", "active": True},
        {"id": "2", "name": "Backup DB", "active": False},
    ])
    bridge.get_workflow = AsyncMock(return_value={"id": "1", "name": "Email Quotidien", "active": True})
    bridge.activate_workflow = AsyncMock(return_value={"id": "1", "name": "Email Quotidien", "active": True})
    bridge.deactivate_workflow = AsyncMock(return_value={"id": "1", "name": "Email Quotidien", "active": False})
    bridge.trigger_workflow = AsyncMock(return_value={"id": "exec_42", "executionId": "exec_42"})
    bridge.trigger_webhook = AsyncMock(return_value={"status": "ok", "message": "received"})
    bridge.list_executions = AsyncMock(return_value=[
        {"id": "100", "status": "success", "workflowData": {"name": "Email"}, "stoppedAt": "2026-03-29T10:00:00Z"},
        {"id": "101", "status": "error", "workflowData": {"name": "Backup"}, "stoppedAt": "2026-03-29T09:00:00Z"},
    ])
    bridge.get_execution = AsyncMock(return_value={
        "id": "100", "status": "success", "workflowData": {"name": "Email"},
        "startedAt": "2026-03-29T09:59:00Z", "stoppedAt": "2026-03-29T10:00:00Z", "mode": "trigger",
    })
    bridge.create_workflow = AsyncMock(return_value={"id": "3", "name": "Nouveau Workflow"})
    bridge.delete_workflow = AsyncMock(return_value={})
    bridge.update_workflow = AsyncMock(return_value={"id": "3", "name": "Workflow Mis à Jour"})
    bridge.get_node_types = AsyncMock(return_value=[{"name": "manualTrigger", "displayName": "Manual Trigger"}])
    bridge.create_from_template = AsyncMock(return_value={"id": "tpl_1", "name": "Template Workflow", "active": False})
    bridge.search_online_templates = AsyncMock(return_value=[
        {"id": 1954, "name": "Telegram Bot AI", "nodes": [1, 2, 3], "totalViews": 5200},
        {"id": 2001, "name": "Telegram Notification", "nodes": [1, 2], "totalViews": 3100},
    ])
    bridge.get_online_template = AsyncMock(return_value={
        "id": 1954, "name": "Telegram Bot AI",
        "workflow": {
            "nodes": [
                {"name": "Trigger", "type": "n8n-nodes-base.manualTrigger", "typeVersion": 1, "position": [250, 300], "parameters": {}},
                {"name": "HTTP", "type": "n8n-nodes-base.httpRequest", "typeVersion": 4, "position": [480, 300], "parameters": {}},
            ],
            "connections": {"Trigger": {"main": [[{"node": "HTTP", "type": "main", "index": 0}]]}},
        },
    })
    bridge.import_online_template = AsyncMock(return_value={
        "id": "imported_1", "name": "Telegram Bot AI", "active": False,
        "template_id": 1954, "template_name": "Telegram Bot AI",
    })
    for key, val in overrides.items():
        setattr(bridge, key, val)
    return bridge


# ─── n8n_status ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_n8n_status_ok(ctx):
    with patch("src.reasoning.handlers.n8n._get_bridge", return_value=_mock_bridge()):
        r = await n8n_status_handler(ctx)
    assert r.success
    assert "connecté" in r.output
    assert "2 total" in r.output
    assert "1 actifs" in r.output


@pytest.mark.asyncio
async def test_n8n_status_not_configured(ctx):
    bridge = _mock_bridge()
    bridge.is_configured = False
    with patch("src.reasoning.handlers.n8n._get_bridge", return_value=bridge):
        r = await n8n_status_handler(ctx)
    assert not r.success
    assert "non configuré" in r.output


@pytest.mark.asyncio
async def test_n8n_status_error(ctx):
    bridge = _mock_bridge()
    bridge.health = AsyncMock(side_effect=Exception("Connection refused"))
    with patch("src.reasoning.handlers.n8n._get_bridge", return_value=bridge):
        r = await n8n_status_handler(ctx)
    assert not r.success
    assert "Connection refused" in r.output


# ─── n8n_list_workflows ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_workflows(ctx):
    with patch("src.reasoning.handlers.n8n._get_bridge", return_value=_mock_bridge()):
        r = await n8n_list_workflows_handler(ctx)
    assert r.success
    assert "Email Quotidien" in r.output
    assert "Backup DB" in r.output


@pytest.mark.asyncio
async def test_list_workflows_empty(ctx):
    bridge = _mock_bridge(list_workflows=AsyncMock(return_value=[]))
    with patch("src.reasoning.handlers.n8n._get_bridge", return_value=bridge):
        r = await n8n_list_workflows_handler(ctx)
    assert r.success
    assert "Aucun" in r.output


# ─── n8n_trigger_workflow ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_trigger_workflow(ctx):
    with patch("src.reasoning.handlers.n8n._get_bridge", return_value=_mock_bridge()):
        r = await n8n_trigger_workflow_handler(ctx, workflow_id="1")
    assert r.success
    assert "déclenché" in r.output
    assert "exec_42" in r.output


@pytest.mark.asyncio
async def test_trigger_workflow_error(ctx):
    bridge = _mock_bridge(trigger_workflow=AsyncMock(side_effect=Exception("404 Not Found")))
    with patch("src.reasoning.handlers.n8n._get_bridge", return_value=bridge):
        r = await n8n_trigger_workflow_handler(ctx, workflow_id="999")
    assert not r.success
    assert "404" in r.output


# ─── n8n_trigger_webhook ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_trigger_webhook(ctx):
    with patch("src.reasoning.handlers.n8n._get_bridge", return_value=_mock_bridge()):
        r = await n8n_trigger_webhook_handler(ctx, webhook_path="alerte-stock", data={"item": "A"})
    assert r.success
    assert "alerte-stock" in r.output


# ─── n8n_activate/deactivate ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_activate_workflow(ctx):
    with patch("src.reasoning.handlers.n8n._get_bridge", return_value=_mock_bridge()):
        r = await n8n_activate_workflow_handler(ctx, workflow_id="1")
    assert r.success
    assert "activé" in r.output


@pytest.mark.asyncio
async def test_deactivate_workflow(ctx):
    with patch("src.reasoning.handlers.n8n._get_bridge", return_value=_mock_bridge()):
        r = await n8n_deactivate_workflow_handler(ctx, workflow_id="1")
    assert r.success
    assert "désactivé" in r.output


# ─── n8n_list_executions ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_executions(ctx):
    with patch("src.reasoning.handlers.n8n._get_bridge", return_value=_mock_bridge()):
        r = await n8n_list_executions_handler(ctx)
    assert r.success
    assert "2 exécution" in r.output
    assert "Email" in r.output


@pytest.mark.asyncio
async def test_list_executions_empty(ctx):
    bridge = _mock_bridge(list_executions=AsyncMock(return_value=[]))
    with patch("src.reasoning.handlers.n8n._get_bridge", return_value=bridge):
        r = await n8n_list_executions_handler(ctx)
    assert r.success
    assert "Aucune" in r.output


# ─── n8n_get_execution ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_execution(ctx):
    with patch("src.reasoning.handlers.n8n._get_bridge", return_value=_mock_bridge()):
        r = await n8n_get_execution_handler(ctx, execution_id="100")
    assert r.success
    assert "100" in r.output
    assert "Email" in r.output
    assert "success" in r.output


# ─── n8n_create_workflow ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_workflow(ctx):
    with patch("src.reasoning.handlers.n8n._get_bridge", return_value=_mock_bridge()):
        r = await n8n_create_workflow_handler(ctx, name="Mon Workflow")
    assert r.success
    assert "créé" in r.output


# ─── n8n_delete_workflow ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_delete_workflow(ctx):
    with patch("src.reasoning.handlers.n8n._get_bridge", return_value=_mock_bridge()):
        r = await n8n_delete_workflow_handler(ctx, workflow_id="2")
    assert r.success
    assert "supprimé" in r.output

# ─── n8n_update_workflow ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_update_workflow(ctx):
    with patch("src.reasoning.handlers.n8n._get_bridge", return_value=_mock_bridge()):
        r = await n8n_update_workflow_handler(ctx, workflow_id="3", name="Workflow Mis à Jour")
    assert r.success
    assert "mis à jour" in r.output.lower()


# ─── n8n_get_workflow ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_workflow(ctx):
    with patch("src.reasoning.handlers.n8n._get_bridge", return_value=_mock_bridge()):
        r = await n8n_get_workflow_handler(ctx, workflow_id="1")
    assert r.success
    assert "Email Quotidien" in r.output


# ─── n8n_list_node_types ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_node_types(ctx):
    with patch("src.reasoning.handlers.n8n._get_bridge", return_value=_mock_bridge()):
        r = await n8n_list_node_types_handler(ctx)
    assert r.success
    assert "manualTrigger" in r.output
    assert "httpRequest" in r.output
    assert "telegram" in r.output

# ─── HandlerDefs quality ────────────────────────────────────────────────

def test_handler_defs_count():
    defs = get_n8n_handler_defs()
    assert len(defs) == 17


def test_handler_defs_no_duplicates():
    defs = get_n8n_handler_defs()
    names = [d.name for d in defs]
    assert len(names) == len(set(names))


def test_handler_defs_all_n8n_prefixed():
    defs = get_n8n_handler_defs()
    for d in defs:
        assert d.name.startswith("n8n_"), f"{d.name} should start with n8n_"


def test_handler_defs_have_descriptions():
    defs = get_n8n_handler_defs()
    for d in defs:
        assert d.description, f"{d.name} manque une description"


def test_handler_defs_have_handlers():
    for d in get_n8n_handler_defs():
        assert callable(d.handler), f"{d.name} handler not callable"


def test_handler_defs_category():
    for d in get_n8n_handler_defs():
        assert d.category == "automation", f"{d.name} should have category 'automation'"


def test_handler_defs_parameters_schema():
    for d in get_n8n_handler_defs():
        assert "properties" in d.parameters, f"{d.name} missing 'properties' in parameters"
        assert "required" in d.parameters, f"{d.name} missing 'required' in parameters"


# ─── n8n_create_from_template ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_from_template_ok(ctx):
    bridge = _mock_bridge()
    bridge.create_from_template = AsyncMock(
        return_value={"id": "tpl_1", "name": "🚀 Pro — Crypto", "active": False}
    )
    with patch("src.reasoning.handlers.n8n._get_bridge", return_value=bridge):
        r = await n8n_create_from_template_handler(ctx, template_name="crypto_alert")
    assert r.success
    assert "tpl_1" in r.output
    assert "crypto_alert" in r.output


@pytest.mark.asyncio
async def test_create_from_template_unknown(ctx):
    with patch("src.reasoning.handlers.n8n._get_bridge", return_value=_mock_bridge()):
        r = await n8n_create_from_template_handler(ctx, template_name="does_not_exist")
    assert not r.success
    assert "inconnu" in r.output.lower()
    # Doit lister les templates disponibles
    assert "crypto_alert" in r.output
    assert "webhook_relay" in r.output


@pytest.mark.asyncio
async def test_create_from_template_with_activate(ctx):
    bridge = _mock_bridge()
    bridge.create_from_template = AsyncMock(
        return_value={"id": "tpl_2", "name": "🔗 Webhook Relay", "active": True}
    )
    with patch("src.reasoning.handlers.n8n._get_bridge", return_value=bridge):
        r = await n8n_create_from_template_handler(ctx, template_name="webhook_relay", activate=True)
    assert r.success
    assert "activé" in r.output


@pytest.mark.asyncio
async def test_create_from_template_error(ctx):
    bridge = _mock_bridge()
    bridge.create_from_template = AsyncMock(side_effect=RuntimeError("n8n down"))
    with patch("src.reasoning.handlers.n8n._get_bridge", return_value=bridge):
        r = await n8n_create_from_template_handler(ctx, template_name="crypto_alert")
    assert not r.success
    assert "n8n down" in r.output


# ─── n8n_list_templates ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_templates(ctx):
    r = await n8n_list_templates_handler(ctx)
    assert r.success
    assert "crypto_alert" in r.output
    assert "webhook_relay" in r.output
    assert "api_health_check" in r.output
    assert "daily_report" in r.output
    assert "email_to_telegram" in r.output
    assert "file_monitor" in r.output


# ─── Template data integrity ──────────────────────────────────────────


def test_all_templates_have_required_fields():
    """Vérifie que chaque template a name, description, nodes, connections."""
    from src.services.n8n_bridge import _WORKFLOW_TEMPLATES

    for tpl_name, tpl in _WORKFLOW_TEMPLATES.items():
        assert "name" in tpl, f"Template {tpl_name} missing 'name'"
        assert "description" in tpl, f"Template {tpl_name} missing 'description'"
        assert "nodes" in tpl, f"Template {tpl_name} missing 'nodes'"
        assert len(tpl["nodes"]) >= 2, f"Template {tpl_name} should have at least 2 nodes"
        assert "connections" in tpl, f"Template {tpl_name} missing 'connections'"


def test_all_template_nodes_have_required_keys():
    """Vérifie que chaque nœud a name, type, typeVersion, position, parameters."""
    from src.services.n8n_bridge import _WORKFLOW_TEMPLATES

    required_keys = {"name", "type", "typeVersion", "position", "parameters"}
    for tpl_name, tpl in _WORKFLOW_TEMPLATES.items():
        for i, node in enumerate(tpl["nodes"]):
            for key in required_keys:
                assert key in node, f"Template {tpl_name}, node #{i} ({node.get('name', '?')}) missing '{key}'"


def test_all_template_node_types_are_valid():
    """Vérifie que chaque type de nœud est dans la liste des types valides."""
    from src.services.n8n_bridge import _WORKFLOW_TEMPLATES
    from src.reasoning.handlers.n8n import _VALID_NODE_TYPES

    for tpl_name, tpl in _WORKFLOW_TEMPLATES.items():
        for node in tpl["nodes"]:
            ntype = node["type"]
            assert ntype in _VALID_NODE_TYPES, (
                f"Template {tpl_name}, node '{node['name']}': type '{ntype}' not in _VALID_NODE_TYPES"
            )


def test_all_template_connections_reference_existing_nodes():
    """Vérifie que les connexions référencent des nœuds existants dans le template."""
    from src.services.n8n_bridge import _WORKFLOW_TEMPLATES

    for tpl_name, tpl in _WORKFLOW_TEMPLATES.items():
        node_names = {n["name"] for n in tpl["nodes"]}
        connections = tpl.get("connections", {})
        for src_name, conn_data in connections.items():
            assert src_name in node_names, (
                f"Template {tpl_name}: connexion source '{src_name}' n'existe pas dans les nœuds"
            )
            for output_idx, targets in enumerate(conn_data.get("main", [])):
                for target in targets:
                    tgt_name = target["node"]
                    assert tgt_name in node_names, (
                        f"Template {tpl_name}: connexion cible '{tgt_name}' (depuis '{src_name}') n'existe pas"
                    )


# ─── n8n_search_online_templates ──────────────────────────────────────


@pytest.mark.asyncio
async def test_search_online_templates_ok(ctx):
    with patch("src.reasoning.handlers.n8n._get_bridge", return_value=_mock_bridge()):
        r = await n8n_search_online_templates_handler(ctx, query="telegram")
    assert r.success
    assert "2 template(s)" in r.output
    assert "Telegram Bot AI" in r.output
    assert "1954" in r.output
    assert "n8n_import_online_template" in r.output


@pytest.mark.asyncio
async def test_search_online_templates_empty(ctx):
    bridge = _mock_bridge(search_online_templates=AsyncMock(return_value=[]))
    with patch("src.reasoning.handlers.n8n._get_bridge", return_value=bridge):
        r = await n8n_search_online_templates_handler(ctx, query="zzz_nothing")
    assert r.success
    assert "Aucun" in r.output


@pytest.mark.asyncio
async def test_search_online_templates_error(ctx):
    bridge = _mock_bridge(search_online_templates=AsyncMock(side_effect=Exception("Network error")))
    with patch("src.reasoning.handlers.n8n._get_bridge", return_value=bridge):
        r = await n8n_search_online_templates_handler(ctx, query="telegram")
    assert not r.success
    assert "Network error" in r.output


# ─── n8n_import_online_template ──────────────────────────────────────


@pytest.mark.asyncio
async def test_import_online_template_ok(ctx):
    with patch("src.reasoning.handlers.n8n._get_bridge", return_value=_mock_bridge()):
        r = await n8n_import_online_template_handler(ctx, template_id=1954)
    assert r.success
    assert "imported_1" in r.output
    assert "Telegram Bot AI" in r.output
    assert "n8n.io/workflows/1954" in r.output


@pytest.mark.asyncio
async def test_import_online_template_with_name(ctx):
    bridge = _mock_bridge()
    bridge.import_online_template = AsyncMock(return_value={
        "id": "imp_2", "name": "Mon Bot TG", "active": False,
        "template_id": 1954, "template_name": "Telegram Bot AI",
    })
    with patch("src.reasoning.handlers.n8n._get_bridge", return_value=bridge):
        r = await n8n_import_online_template_handler(ctx, template_id=1954, name="Mon Bot TG")
    assert r.success
    assert "Mon Bot TG" in r.output or "Telegram Bot AI" in r.output


@pytest.mark.asyncio
async def test_import_online_template_not_found(ctx):
    bridge = _mock_bridge(import_online_template=AsyncMock(
        side_effect=ValueError("Template 99999 introuvable sur n8n.io")
    ))
    with patch("src.reasoning.handlers.n8n._get_bridge", return_value=bridge):
        r = await n8n_import_online_template_handler(ctx, template_id=99999)
    assert not r.success
    assert "introuvable" in r.output


@pytest.mark.asyncio
async def test_import_online_template_error(ctx):
    bridge = _mock_bridge(import_online_template=AsyncMock(side_effect=RuntimeError("n8n down")))
    with patch("src.reasoning.handlers.n8n._get_bridge", return_value=bridge):
        r = await n8n_import_online_template_handler(ctx, template_id=1954)
    assert not r.success
    assert "n8n down" in r.output


@pytest.mark.asyncio
async def test_import_online_template_activate(ctx):
    bridge = _mock_bridge()
    bridge.import_online_template = AsyncMock(return_value={
        "id": "imp_3", "name": "Auto Workflow", "active": True,
        "template_id": 2001, "template_name": "Telegram Notification",
    })
    with patch("src.reasoning.handlers.n8n._get_bridge", return_value=bridge):
        r = await n8n_import_online_template_handler(ctx, template_id=2001, activate=True)
    assert r.success
    assert "activé" in r.output
