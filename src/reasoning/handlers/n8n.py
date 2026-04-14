"""
n8n.py — Handlers V2 n8n (workflow automation).

Permet à Lumena de piloter une instance n8n self-hosted :
  - Lister, déclencher, activer/désactiver des workflows
  - Consulter les exécutions passées
  - Vérifier l'état de santé de n8n
  - Déclencher des workflows via webhook
  - Créer/mettre à jour/supprimer des workflows
  - Lister les types de nœuds disponibles
  - Créer des workflows complets depuis des templates pré-construits
  - Rechercher et importer des templates depuis n8n.io (8968+ templates)

Handlers (17):
  n8n_status, n8n_list_workflows, n8n_get_workflow, n8n_trigger_workflow,
  n8n_trigger_webhook, n8n_activate_workflow, n8n_deactivate_workflow,
  n8n_list_executions, n8n_get_execution, n8n_create_workflow,
  n8n_delete_workflow, n8n_update_workflow, n8n_list_node_types,
  n8n_create_from_template, n8n_list_templates,
  n8n_search_online_templates, n8n_import_online_template
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from loguru import logger

from .context import HandlerContext
from .contracts import HandlerResult
from .registry_v2 import HandlerDef


# ═══════════════════════════════════════════════════════════════════════════
# Types de nœuds n8n GARANTIS disponibles en Community Edition
# Source: n8n-nodes-base (intégré dans toute installation n8n)
# ═══════════════════════════════════════════════════════════════════════════
_VALID_NODE_TYPES = {
    # Triggers
    "n8n-nodes-base.manualTrigger": "Déclenchement manuel (clic)",
    "n8n-nodes-base.scheduleTrigger": "Déclenchement planifié (cron)",
    "n8n-nodes-base.webhookTrigger": "Déclenchement par webhook HTTP entrant",
    "n8n-nodes-base.sSETrigger": "Déclenchement par Server-Sent Events",
    "n8n-nodes-base.emailReadImap": "Lecture emails IMAP (trigger sur nouveaux mails)",
    # Logique
    "n8n-nodes-base.if": "Condition Si/Sinon (branche Booléen)",
    "n8n-nodes-base.switch": "Aiguillage multi-cas (Switch)",
    "n8n-nodes-base.merge": "Fusion de branches",
    "n8n-nodes-base.splitInBatches": "Traitement en lots (loop)",
    "n8n-nodes-base.noOp": "Pas d’opération (No-op)",
    "n8n-nodes-base.wait": "Pause (attente temporelle ou signal)",
    "n8n-nodes-base.stopAndError": "Stoppe l’exécution avec une erreur",
    # Data
    "n8n-nodes-base.set": "Définir / transformer des champs JSON",
    "n8n-nodes-base.code": "Exécuter du code JavaScript/Python",
    "n8n-nodes-base.functionItem": "Transformation JavaScript sur chaque item",
    "n8n-nodes-base.dateTime": "Manipuler des dates",
    "n8n-nodes-base.crypto": "Hash / chiffrement (MD5, SHA256, AES)",
    "n8n-nodes-base.xml": "Parser / générer du XML",
    "n8n-nodes-base.html": "Parser du HTML / extraire du texte",
    "n8n-nodes-base.markdown": "Convertir Markdown → HTML",
    "n8n-nodes-base.extractFromFile": "Lire contenu d’un fichier binaire (CSV, JSON, TXT)",
    "n8n-nodes-base.convertToFile": "Convertir JSON en fichier (CSV, Excel, PDF…)",
    "n8n-nodes-base.compression": "Compresser / décompresser (ZIP, GZIP)",
    "n8n-nodes-base.spreadsheetFile": "Lire/écrire un fichier tableur (Excel/CSV)",
    # HTTP
    "n8n-nodes-base.httpRequest": "Requête HTTP (GET/POST/PUT/DELETE) vers n’importe quelle API",
    "n8n-nodes-base.webhook": "Recevoir des requêtes HTTP entrantes (Webhook passif)",
    "n8n-nodes-base.respondToWebhook": "Répondre à un Webhook entrant",
    # Fichiers
    "n8n-nodes-base.localFileTrigger": "Surveiller un dossier local (création/modification de fichiers)",
    "n8n-nodes-base.readWriteFile": "Lire ou écrire un fichier local",
    "n8n-nodes-base.moveFile": "Déplacer / renommer un fichier",
    # Emails
    "n8n-nodes-base.emailSend": "Envoyer un email (SMTP)",
    "n8n-nodes-base.gmail": "Gmail (lire / envoyer)",
    # Messageries
    "n8n-nodes-base.telegram": "Telegram (envoyer message, photo, fichier)",
    "n8n-nodes-base.slack": "Slack (message, fichier, canal)",
    "n8n-nodes-base.discord": "Discord (message via webhook)",
    "n8n-nodes-base.microsoftTeams": "Microsoft Teams (message)",
    # Bases de données
    "n8n-nodes-base.postgres": "PostgreSQL (SELECT/INSERT/UPDATE)",
    "n8n-nodes-base.mysql": "MySQL / MariaDB",
    "n8n-nodes-base.redis": "Redis (get/set/pub-sub)",
    "n8n-nodes-base.mongoDb": "MongoDB",
    "n8n-nodes-base.sqlite": "SQLite",
    # Stockage
    "n8n-nodes-base.googleDrive": "Google Drive (upload, download, liste)",
    "n8n-nodes-base.dropbox": "Dropbox",
    "n8n-nodes-base.ftp": "FTP / SFTP",
    "n8n-nodes-base.s3": "AWS S3",
    # Outils AI
    "n8n-nodes-base.openAi": "OpenAI (completion, embedding, image)",
    # Utilitaires
    "n8n-nodes-base.n8n": "Actions internes n8n (workflow, exécution…)",
    "n8n-nodes-base.executeCommand": "Exécuter une commande shell",
    "n8n-nodes-base.stickyNote": "Note (documentation dans l’éditeur)",
}

_VALID_TYPES_TEXT = "\n".join(f"  - {k}: {v}" for k, v in _VALID_NODE_TYPES.items())


def _get_bridge():
    from src.services.n8n_bridge import get_n8n_bridge
    return get_n8n_bridge()


# ═══════════════════════════════════════════════════════════════════════════
#  n8n_status — Vérifie la connexion à n8n
# ═══════════════════════════════════════════════════════════════════════════

async def n8n_status_handler(ctx: HandlerContext) -> HandlerResult:
    """Vérifie l'état de l'instance n8n."""
    try:
        bridge = _get_bridge()
        if not bridge.is_configured:
            return HandlerResult.fail(
                "❌ n8n non configuré. Renseigner N8N_BASE_URL et N8N_API_KEY dans .env",
                handler_name="n8n_status",
            )
        info = await bridge.health()
        workflows = await bridge.list_workflows()
        active = sum(1 for w in workflows if w.get("active"))
        return HandlerResult.ok(
            f"✅ n8n connecté ({bridge.base_url})\n"
            f"  Santé: {info.get('status', 'unknown')}\n"
            f"  Workflows: {len(workflows)} total, {active} actifs",
            handler_name="n8n_status",
        )
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur n8n: {e}", handler_name="n8n_status")


# ═══════════════════════════════════════════════════════════════════════════
#  n8n_list_workflows
# ═══════════════════════════════════════════════════════════════════════════

async def n8n_list_workflows_handler(
    ctx: HandlerContext, active_only: bool = False, limit: int = 50
) -> HandlerResult:
    """Liste les workflows n8n."""
    try:
        bridge = _get_bridge()
        workflows = await bridge.list_workflows(limit=limit, active_only=active_only)
        if not workflows:
            return HandlerResult.ok("Aucun workflow trouvé.", handler_name="n8n_list_workflows")
        lines = []
        for w in workflows:
            status = "🟢" if w.get("active") else "⚪"
            name = w.get("name", "Sans nom")
            wid = w.get("id", "?")
            lines.append(f"  {status} **{name}** (id: `{wid}`)")
        return HandlerResult.ok(
            f"📋 {len(workflows)} workflow(s) :\n" + "\n".join(lines),
            handler_name="n8n_list_workflows",
        )
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur: {e}", handler_name="n8n_list_workflows")


# ═══════════════════════════════════════════════════════════════════════════
#  n8n_trigger_workflow — Déclenche un workflow par ID
# ═══════════════════════════════════════════════════════════════════════════

async def n8n_trigger_workflow_handler(
    ctx: HandlerContext, workflow_id: str, data: Optional[Dict[str, Any]] = None
) -> HandlerResult:
    """Déclenche l'exécution d'un workflow n8n."""
    try:
        bridge = _get_bridge()
        result = await bridge.trigger_workflow(workflow_id, data)
        exec_id = result.get("id") or result.get("executionId", "?")
        return HandlerResult.ok(
            f"✅ Workflow `{workflow_id}` déclenché — Exécution: `{exec_id}`",
            handler_name="n8n_trigger_workflow",
        )
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur: {e}", handler_name="n8n_trigger_workflow")


# ═══════════════════════════════════════════════════════════════════════════
#  n8n_trigger_webhook — Déclenche via URL webhook
# ═══════════════════════════════════════════════════════════════════════════

async def n8n_trigger_webhook_handler(
    ctx: HandlerContext, webhook_path: str, data: Optional[Dict[str, Any]] = None
) -> HandlerResult:
    """Déclenche un workflow via son chemin webhook (ex: 'mon-webhook')."""
    try:
        bridge = _get_bridge()
        result = await bridge.trigger_webhook(webhook_path, data)
        return HandlerResult.ok(
            f"✅ Webhook `{webhook_path}` déclenché — Réponse: {str(result)[:300]}",
            handler_name="n8n_trigger_webhook",
        )
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur: {e}", handler_name="n8n_trigger_webhook")


# ═══════════════════════════════════════════════════════════════════════════
#  n8n_activate_workflow / n8n_deactivate_workflow
# ═══════════════════════════════════════════════════════════════════════════

async def n8n_activate_workflow_handler(
    ctx: HandlerContext, workflow_id: str
) -> HandlerResult:
    """Active un workflow n8n."""
    try:
        bridge = _get_bridge()
        result = await bridge.activate_workflow(workflow_id)
        name = result.get("name", workflow_id)
        return HandlerResult.ok(
            f"✅ Workflow **{name}** activé", handler_name="n8n_activate_workflow",
        )
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur: {e}", handler_name="n8n_activate_workflow")


async def n8n_deactivate_workflow_handler(
    ctx: HandlerContext, workflow_id: str
) -> HandlerResult:
    """Désactive un workflow n8n."""
    try:
        bridge = _get_bridge()
        result = await bridge.deactivate_workflow(workflow_id)
        name = result.get("name", workflow_id)
        return HandlerResult.ok(
            f"✅ Workflow **{name}** désactivé", handler_name="n8n_deactivate_workflow",
        )
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur: {e}", handler_name="n8n_deactivate_workflow")


# ═══════════════════════════════════════════════════════════════════════════
#  n8n_list_executions
# ═══════════════════════════════════════════════════════════════════════════

async def n8n_list_executions_handler(
    ctx: HandlerContext,
    workflow_id: Optional[str] = None,
    limit: int = 10,
    status: Optional[str] = None,
) -> HandlerResult:
    """Liste les exécutions passées de workflows n8n."""
    try:
        bridge = _get_bridge()
        execs = await bridge.list_executions(workflow_id=workflow_id, limit=limit, status=status)
        if not execs:
            return HandlerResult.ok("Aucune exécution trouvée.", handler_name="n8n_list_executions")
        lines = []
        for ex in execs:
            eid = ex.get("id", "?")
            st = ex.get("status", "unknown")
            icon = {"success": "✅", "error": "❌", "waiting": "⏳"}.get(st, "❓")
            wname = ex.get("workflowData", {}).get("name", ex.get("workflowId", "?"))
            finished = ex.get("stoppedAt", "en cours")
            lines.append(f"  {icon} `{eid}` — {wname} ({st}) — {finished}")
        return HandlerResult.ok(
            f"📋 {len(execs)} exécution(s) :\n" + "\n".join(lines),
            handler_name="n8n_list_executions",
        )
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur: {e}", handler_name="n8n_list_executions")


# ═══════════════════════════════════════════════════════════════════════════
#  n8n_get_execution
# ═══════════════════════════════════════════════════════════════════════════

async def n8n_get_execution_handler(
    ctx: HandlerContext, execution_id: str
) -> HandlerResult:
    """Récupère les détails d'une exécution n8n."""
    try:
        bridge = _get_bridge()
        ex = await bridge.get_execution(execution_id)
        st = ex.get("status", "unknown")
        wname = ex.get("workflowData", {}).get("name", "?")
        started = ex.get("startedAt", "?")
        stopped = ex.get("stoppedAt", "en cours")
        mode = ex.get("mode", "?")
        return HandlerResult.ok(
            f"📄 Exécution `{execution_id}`\n"
            f"  Workflow: {wname}\n"
            f"  Statut: {st}\n"
            f"  Mode: {mode}\n"
            f"  Début: {started}\n"
            f"  Fin: {stopped}",
            handler_name="n8n_get_execution",
        )
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur: {e}", handler_name="n8n_get_execution")


# ═══════════════════════════════════════════════════════════════════════════
#  n8n_get_workflow
# ═══════════════════════════════════════════════════════════════════════════

async def n8n_get_workflow_handler(
    ctx: HandlerContext, workflow_id: str
) -> HandlerResult:
    """Récupère la structure complète d'un workflow n8n (nœuds, connexions, settings)."""
    try:
        bridge = _get_bridge()
        wf = await bridge.get_workflow(workflow_id)
        name = wf.get("name", "?")
        nodes = wf.get("nodes", [])
        node_names = ", ".join(n.get("name", "?") + f" ({n.get('type','?').split('.')[-1]})" for n in nodes)
        return HandlerResult.ok(
            f"**{name}** (id: `{workflow_id}`, {len(nodes)} nœud(s)): {node_names or 'aucun'}",
            handler_name="n8n_get_workflow",
        )
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur: {e}", handler_name="n8n_get_workflow")


# ═══════════════════════════════════════════════════════════════════════════
#  n8n_list_node_types
# ═══════════════════════════════════════════════════════════════════════════

async def n8n_list_node_types_handler(ctx: HandlerContext) -> HandlerResult:
    """Liste les types de nœuds (nodes) disponibles dans l'instance n8n.
    
    Retourne d'abord la liste locale garantie, puis tente d'enrichir depuis l'API.
    À appeler AVANT n8n_update_workflow pour s'assurer d'utiliser des types valides.
    """
    # Liste locale garantie (toujours disponible)
    local_types = list(_VALID_NODE_TYPES.keys())
    lines = [f"**{len(local_types)} types de nœuds n8n disponibles** (n8n-nodes-base):\n"]
    for category, items in [
        ("Triggers", [k for k in local_types if any(x in k for x in ["Trigger", "Imap"])]),
        ("Logique", [k for k in local_types if any(x in k for x in ["if", "switch", "merge", "split", "noOp", "wait", "stop"])]),
        ("HTTP/Webhook", [k for k in local_types if any(x in k for x in ["http", "webhook", "respond"])]),
        ("Données", [k for k in local_types if any(x in k for x in ["set", "code", "function", "dateTime", "crypto", "xml", "html", "markdown", "extract", "convert", "compress", "spreadsheet"])]),
        ("Fichiers", [k for k in local_types if any(x in k for x in ["File", "Move"])]),
        ("Messagerie", [k for k in local_types if any(x in k for x in ["email", "gmail", "telegram", "slack", "discord", "teams"])]),
        ("BDD/Stockage", [k for k in local_types if any(x in k for x in ["postgres", "mysql", "redis", "mongo", "sqlite", "Drive", "dropbox", "ftp", "s3"])]),
        ("Autres", [k for k in local_types if any(x in k for x in ["openAi", "n8n", "execute", "sticky"])]),
    ]:
        if items:
            lines.append(f"\n*{category}:*")
            for t in items:
                lines.append(f"  `{t}` — {_VALID_NODE_TYPES.get(t, '')}")
    return HandlerResult.ok("\n".join(lines), handler_name="n8n_list_node_types")
# ═══════════════════════════════════════════════════════════════════════════

async def n8n_create_workflow_handler(
    ctx: HandlerContext, name: str, active: bool = False
) -> HandlerResult:
    """Crée un nouveau workflow n8n vide."""
    try:
        bridge = _get_bridge()
        result = await bridge.create_workflow(name=name, active=active)
        wid = result.get("id", "?")
        return HandlerResult.ok(
            f"✅ Workflow **{name}** créé (id: `{wid}`, actif: {active})",
            handler_name="n8n_create_workflow",
        )
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur: {e}", handler_name="n8n_create_workflow")


# ═══════════════════════════════════════════════════════════════════════════
#  n8n_update_workflow
# ═══════════════════════════════════════════════════════════════════════════

async def n8n_update_workflow_handler(
    ctx: HandlerContext,
    workflow_id: str,
    name: Optional[str] = None,
    nodes: Optional[List[Dict]] = None,
    connections: Optional[Dict] = None,
) -> HandlerResult:
    """Met à jour la structure (nom, nœuds, connexions) d'un workflow n8n existant."""
    try:
        bridge = _get_bridge()
        result = await bridge.update_workflow(
            workflow_id=workflow_id, name=name, nodes=nodes, connections=connections
        )
        wname = result.get("name", workflow_id)
        return HandlerResult.ok(
            f"✅ Workflow `{workflow_id}` mis à jour (**{wname}**)",
            handler_name="n8n_update_workflow",
        )
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur: {e}", handler_name="n8n_update_workflow")


# ═══════════════════════════════════════════════════════════════════════════
#  n8n_delete_workflow
# ═══════════════════════════════════════════════════════════════════════════

async def n8n_delete_workflow_handler(
    ctx: HandlerContext, workflow_id: str
) -> HandlerResult:
    """Supprime un workflow n8n."""
    try:
        bridge = _get_bridge()
        await bridge.delete_workflow(workflow_id)
        return HandlerResult.ok(
            f"✅ Workflow `{workflow_id}` supprimé",
            handler_name="n8n_delete_workflow",
        )
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur: {e}", handler_name="n8n_delete_workflow")


# ═══════════════════════════════════════════════════════════════════════════
#  n8n_create_from_template
# ═══════════════════════════════════════════════════════════════════════════

async def n8n_create_from_template_handler(
    ctx: HandlerContext, template_name: str, activate: bool = False
) -> HandlerResult:
    """Crée un workflow complet depuis un template pré-construit et testé."""
    try:
        bridge = _get_bridge()
        # Import pour lister les templates disponibles
        from src.services.n8n_bridge import _WORKFLOW_TEMPLATES

        if template_name not in _WORKFLOW_TEMPLATES:
            available = ", ".join(_WORKFLOW_TEMPLATES.keys())
            tpl_list = "\n".join(
                f"  • `{k}` — {v.get('description', '')}"
                for k, v in _WORKFLOW_TEMPLATES.items()
            )
            return HandlerResult.fail(
                f"❌ Template '{template_name}' inconnu.\n\nTemplates disponibles:\n{tpl_list}",
                handler_name="n8n_create_from_template",
            )

        result = await bridge.create_from_template(template_name, activate=activate)
        wid = result.get("id", "?")
        wname = result.get("name", template_name)
        active_str = "activé" if result.get("active") else "inactif"
        tpl = _WORKFLOW_TEMPLATES[template_name]
        node_count = len(tpl.get("nodes", []))
        return HandlerResult.ok(
            f"✅ Workflow **{wname}** créé depuis template `{template_name}`\n"
            f"   ID: `{wid}` | Nœuds: {node_count} | Statut: {active_str}\n"
            f"   Description: {tpl.get('description', '')}",
            handler_name="n8n_create_from_template",
        )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur création template: {e}",
            handler_name="n8n_create_from_template",
        )


async def n8n_list_templates_handler(
    ctx: HandlerContext,
) -> HandlerResult:
    """Liste les templates de workflows pré-construits disponibles."""
    from src.services.n8n_bridge import _WORKFLOW_TEMPLATES

    lines = ["📋 **Templates de workflows n8n disponibles:**\n"]
    for k, v in _WORKFLOW_TEMPLATES.items():
        node_count = len(v.get("nodes", []))
        lines.append(
            f"  • `{k}` ({node_count} nœuds) — {v.get('description', '')}"
        )
    lines.append(
        "\n💡 Utiliser `n8n_create_from_template` avec le nom du template pour créer un workflow complet."
    )
    return HandlerResult.ok("\n".join(lines), handler_name="n8n_list_templates")


# ═══════════════════════════════════════════════════════════════════════════
#  n8n_search_online_templates — Recherche dans la bibliothèque n8n.io
# ═══════════════════════════════════════════════════════════════════════════

async def n8n_search_online_templates_handler(
    ctx: HandlerContext, query: str, category: str = "", limit: int = 10
) -> HandlerResult:
    """Recherche de templates dans la bibliothèque publique n8n.io (8968+ templates)."""
    try:
        bridge = _get_bridge()
        results = await bridge.search_online_templates(query=query, category=category, limit=limit)
        if not results:
            return HandlerResult.ok(
                f"Aucun template trouvé pour '{query}'.",
                handler_name="n8n_search_online_templates",
            )
        lines = [f"🔍 **{len(results)} template(s) n8n.io pour '{query}':**\n"]
        for tpl in results:
            tid = tpl.get("id", "?")
            name = tpl.get("name", "Sans nom")
            nodes_count = len(tpl.get("nodes", tpl.get("workflowNodes", [])))
            views = tpl.get("totalViews", 0)
            lines.append(f"  • **{name}** (id: `{tid}`, {nodes_count} nœuds, {views} vues)")
        lines.append(
            "\n💡 Utiliser `n8n_import_online_template` avec l'ID du template pour l'importer dans n8n."
        )
        return HandlerResult.ok("\n".join(lines), handler_name="n8n_search_online_templates")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur recherche: {e}", handler_name="n8n_search_online_templates")


# ═══════════════════════════════════════════════════════════════════════════
#  n8n_import_online_template — Importe un template n8n.io dans l'instance locale
# ═══════════════════════════════════════════════════════════════════════════

async def n8n_import_online_template_handler(
    ctx: HandlerContext, template_id: int, name: Optional[str] = None, activate: bool = False
) -> HandlerResult:
    """Importe un template depuis n8n.io dans l'instance n8n locale."""
    try:
        bridge = _get_bridge()
        result = await bridge.import_online_template(
            template_id=template_id, name=name, activate=activate
        )
        wid = result.get("id", "?")
        wname = result.get("name", "?")
        tpl_name = result.get("template_name", "")
        active_str = "activé" if result.get("active") else "inactif"
        return HandlerResult.ok(
            f"✅ Template n8n.io **{tpl_name}** importé avec succès !\n"
            f"   Workflow: **{wname}** (id: `{wid}`, statut: {active_str})\n"
            f"   Source: https://n8n.io/workflows/{template_id}/",
            handler_name="n8n_import_online_template",
        )
    except ValueError as e:
        return HandlerResult.fail(f"❌ {e}", handler_name="n8n_import_online_template")
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur import template: {e}",
            handler_name="n8n_import_online_template",
        )


# ═══════════════════════════════════════════════════════════════════════════
#  Export HandlerDefs
# ═══════════════════════════════════════════════════════════════════════════

def get_n8n_handler_defs() -> list[HandlerDef]:
    return [
        HandlerDef(
            name="n8n_status",
            description="Vérifie la connexion et l'état de l'instance n8n (workflows actifs, santé)",
            parameters={"properties": {}, "required": []},
            handler=n8n_status_handler,
            category="automation",
            source_module="handlers.n8n",
        ),
        HandlerDef(
            name="n8n_list_workflows",
            description="Liste tous les workflows n8n avec leur statut (actif/inactif)",
            parameters={
                "properties": {
                    "active_only": {"type": "boolean", "description": "Ne lister que les workflows actifs"},
                    "limit": {"type": "integer", "description": "Nombre max de workflows (défaut 50)"},
                },
                "required": [],
            },
            handler=n8n_list_workflows_handler,
            category="automation",
            source_module="handlers.n8n",
        ),
        HandlerDef(
            name="n8n_get_workflow",
            description="Récupère la structure complète d'un workflow n8n (nœuds, connexions, settings)",
            parameters={
                "properties": {
                    "workflow_id": {"type": "string", "description": "ID du workflow"},
                },
                "required": ["workflow_id"],
            },
            handler=n8n_get_workflow_handler,
            category="automation",
            source_module="handlers.n8n",
        ),
        HandlerDef(
            name="n8n_trigger_workflow",
            description="Déclenche l'exécution d'un workflow n8n par son ID",
            parameters={
                "properties": {
                    "workflow_id": {"type": "string", "description": "ID du workflow à déclencher"},
                    "data": {"type": "object", "description": "Données à passer au workflow (optionnel)"},
                },
                "required": ["workflow_id"],
            },
            handler=n8n_trigger_workflow_handler,
            category="automation",
            source_module="handlers.n8n",
        ),
        HandlerDef(
            name="n8n_trigger_webhook",
            description="Déclenche un workflow n8n via son chemin webhook (ex: 'mon-webhook')",
            parameters={
                "properties": {
                    "webhook_path": {"type": "string", "description": "Chemin du webhook (sans /webhook/ prefix)"},
                    "data": {"type": "object", "description": "Données JSON à envoyer"},
                },
                "required": ["webhook_path"],
            },
            handler=n8n_trigger_webhook_handler,
            category="automation",
            source_module="handlers.n8n",
        ),
        HandlerDef(
            name="n8n_activate_workflow",
            description="Active un workflow n8n pour qu'il se déclenche automatiquement",
            parameters={
                "properties": {
                    "workflow_id": {"type": "string", "description": "ID du workflow à activer"},
                },
                "required": ["workflow_id"],
            },
            handler=n8n_activate_workflow_handler,
            category="automation",
            source_module="handlers.n8n",
        ),
        HandlerDef(
            name="n8n_deactivate_workflow",
            description="Désactive un workflow n8n (arrête les triggers automatiques)",
            parameters={
                "properties": {
                    "workflow_id": {"type": "string", "description": "ID du workflow à désactiver"},
                },
                "required": ["workflow_id"],
            },
            handler=n8n_deactivate_workflow_handler,
            category="automation",
            source_module="handlers.n8n",
        ),
        HandlerDef(
            name="n8n_list_executions",
            description="Liste les exécutions récentes de workflows n8n (succès, erreurs, en cours)",
            parameters={
                "properties": {
                    "workflow_id": {"type": "string", "description": "Filtrer par workflow ID (optionnel)"},
                    "limit": {"type": "integer", "description": "Nombre max de résultats (défaut 10)"},
                    "status": {"type": "string", "description": "Filtrer par statut: success, error, waiting"},
                },
                "required": [],
            },
            handler=n8n_list_executions_handler,
            category="automation",
            source_module="handlers.n8n",
        ),
        HandlerDef(
            name="n8n_get_execution",
            description="Récupère les détails complets d'une exécution n8n (logs, statut, timing)",
            parameters={
                "properties": {
                    "execution_id": {"type": "string", "description": "ID de l'exécution"},
                },
                "required": ["execution_id"],
            },
            handler=n8n_get_execution_handler,
            category="automation",
            source_module="handlers.n8n",
        ),
        HandlerDef(
            name="n8n_create_workflow",
            description="Crée un nouveau workflow n8n vide",
            parameters={
                "properties": {
                    "name": {"type": "string", "description": "Nom du workflow"},
                    "active": {"type": "boolean", "description": "Activer immédiatement (défaut false)"},
                },
                "required": ["name"],
            },
            handler=n8n_create_workflow_handler,
            category="automation",
            source_module="handlers.n8n",
        ),
        HandlerDef(
            name="n8n_delete_workflow",
            description="Supprime un workflow n8n",
            parameters={
                "properties": {
                    "workflow_id": {"type": "string", "description": "ID du workflow à supprimer"},
                },
                "required": ["workflow_id"],
            },
            handler=n8n_delete_workflow_handler,
            category="automation",
            source_module="handlers.n8n",
        ),
        HandlerDef(
            name="n8n_update_workflow",
            description=(
                "Met à jour la structure (nom, nœuds, connexions) d'un workflow n8n existant via PUT /workflows/{id}.\n"
                "IMPORTANT: utiliser UNIQUEMENT des types de nœuds valides (appeler n8n_list_node_types d'abord).\n"
                "Exemples de types VALIDES: n8n-nodes-base.manualTrigger, n8n-nodes-base.scheduleTrigger, "
                "n8n-nodes-base.httpRequest, n8n-nodes-base.if, n8n-nodes-base.set, n8n-nodes-base.code, "
                "n8n-nodes-base.telegram, n8n-nodes-base.emailSend, n8n-nodes-base.localFileTrigger, "
                "n8n-nodes-base.readWriteFile, n8n-nodes-base.webhook, n8n-nodes-base.slack.\n"
                "Types INVALIDES (ne pas utiliser): watchFolder, logger, fileWatcher, notification, archive.\n"
                "Chaque nœud DOIT avoir: id (UUID), name (str), type (str), typeVersion (int, généralement 1 ou 2), "
                "position ([x, y]), parameters (dict, peut être vide)."
            ),
            parameters={
                "properties": {
                    "workflow_id": {"type": "string", "description": "ID du workflow à modifier"},
                    "name": {"type": "string", "description": "Nouveau nom (optionnel)"},
                    "nodes": {"type": "array", "description": "Liste complète des nœuds avec id/name/type/typeVersion/position/parameters"},
                    "connections": {"type": "object", "description": "Connexions: {\"NomSource\": {\"main\": [[{\"node\": \"NomDest\", \"type\": \"main\", \"index\": 0}]]}}"},
                },
                "required": ["workflow_id"],
            },
            handler=n8n_update_workflow_handler,
            category="automation",
            source_module="handlers.n8n",
        ),
        HandlerDef(
            name="n8n_list_node_types",
            description=(
                "Liste tous les types de nœuds disponibles dans n8n (n8n-nodes-base). "
                "Appeler AVANT n8n_update_workflow pour choisir les bons types de nœuds. "
                "Retourne la liste groupée par catégorie (triggers, logique, HTTP, données, fichiers, messagerie, BDD)."
            ),
            parameters={"properties": {}, "required": []},
            handler=n8n_list_node_types_handler,
            category="automation",
            source_module="handlers.n8n",
        ),
        HandlerDef(
            name="n8n_create_from_template",
            description=(
                "Crée un workflow n8n COMPLET et fonctionnel depuis un template pré-construit et testé. "
                "RECOMMANDÉ : utiliser cette commande plutôt que n8n_create_workflow + n8n_update_workflow. "
                "Templates disponibles: crypto_alert (prix crypto + alerte), webhook_relay (reçoit webhook + répond), "
                "api_health_check (monitore APIs toutes les 5 min), daily_report (rapport quotidien 8h), "
                "email_to_telegram (relai email → TG), email_to_whatsapp (relai email → WhatsApp), file_monitor (surveillance dossier). "
                "Chaque template a des nœuds pré-configurés avec les bons paramètres n8n."
            ),
            parameters={
                "properties": {
                    "template_name": {
                        "type": "string",
                        "description": "Nom du template: crypto_alert, webhook_relay, api_health_check, daily_report, email_to_telegram, email_to_whatsapp, file_monitor",
                    },
                    "activate": {"type": "boolean", "description": "Activer immédiatement (défaut false)"},
                },
                "required": ["template_name"],
            },
            handler=n8n_create_from_template_handler,
            category="automation",
            source_module="handlers.n8n",
        ),
        HandlerDef(
            name="n8n_list_templates",
            description="Liste les templates de workflows n8n pré-construits disponibles avec leur description et nombre de nœuds",
            parameters={"properties": {}, "required": []},
            handler=n8n_list_templates_handler,
            category="automation",
            source_module="handlers.n8n",
        ),
        HandlerDef(
            name="n8n_search_online_templates",
            description=(
                "Recherche dans la bibliothèque publique n8n.io (8968+ templates). "
                "Chercher par mot-clé (ex: 'telegram', 'google sheets', 'ai agent') et/ou catégorie. "
                "Retourne les templates avec leur ID à utiliser avec n8n_import_online_template."
            ),
            parameters={
                "properties": {
                    "query": {"type": "string", "description": "Mot-clé de recherche (ex: 'telegram bot', 'email automation')"},
                    "category": {"type": "string", "description": "Catégorie optionnelle (ex: 'AI', 'Sales', 'Marketing')"},
                    "limit": {"type": "integer", "description": "Nombre max de résultats (défaut 10)"},
                },
                "required": ["query"],
            },
            handler=n8n_search_online_templates_handler,
            category="automation",
            source_module="handlers.n8n",
        ),
        HandlerDef(
            name="n8n_import_online_template",
            description=(
                "Importe un template depuis la bibliothèque publique n8n.io directement dans l'instance n8n locale. "
                "Fournir l'ID du template (obtenu via n8n_search_online_templates). "
                "Le workflow est créé avec tous ses nœuds et connexions, prêt à être configuré et activé."
            ),
            parameters={
                "properties": {
                    "template_id": {"type": "integer", "description": "ID du template n8n.io (ex: 1954)"},
                    "name": {"type": "string", "description": "Nom personnalisé pour le workflow (optionnel, sinon nom du template)"},
                    "activate": {"type": "boolean", "description": "Activer immédiatement (défaut false)"},
                },
                "required": ["template_id"],
            },
            handler=n8n_import_online_template_handler,
            category="automation",
            source_module="handlers.n8n",
        ),
    ]
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the Apache License, Version 2.0
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
