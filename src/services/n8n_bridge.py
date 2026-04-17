"""
n8n_bridge.py — Service de connexion à n8n (self-hosted workflow automation).

Permet à Lumena de :
  - Lister, déclencher, activer/désactiver des workflows n8n
  - Récupérer les exécutions passées
  - Vérifier la santé de l'instance n8n

Configuration (.env) :
  N8N_BASE_URL=http://localhost:5678   (URL de l'instance n8n)
  N8N_API_KEY=n8n_api_...              (API key générée dans n8n > Settings > API)
"""

from __future__ import annotations

import os
import threading
import uuid
from typing import Any, Dict, List, Optional

from loguru import logger

try:
    import httpx
    _HTTPX = True
except ImportError:
    _HTTPX = False


class N8nBridge:
    """Client HTTP pour l'API REST n8n v1."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
    ):
        self.base_url = (base_url or os.getenv("N8N_BASE_URL", "")).rstrip("/")
        self.api_key = api_key or os.getenv("N8N_API_KEY", "").strip()
        self._timeout = 30.0

    # ── helpers ─────────────────────────────────────────────────────────

    @property
    def is_configured(self) -> bool:
        return bool(self.base_url and self.api_key)

    def _headers(self) -> Dict[str, str]:
        return {
            "X-N8N-API-KEY": self.api_key,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    async def _get(self, path: str, params: Optional[Dict] = None) -> Dict[str, Any]:
        if not _HTTPX:
            raise RuntimeError("httpx non installé (pip install httpx)")
        if not self.is_configured:
            raise RuntimeError("n8n non configuré. Renseigner N8N_BASE_URL et N8N_API_KEY dans .env")
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            r = await client.get(
                f"{self.base_url}/api/v1{path}",
                headers=self._headers(),
                params=params,
            )
            r.raise_for_status()
            return r.json()

    async def _post(self, path: str, json_body: Optional[Dict] = None) -> Dict[str, Any]:
        if not _HTTPX:
            raise RuntimeError("httpx non installé (pip install httpx)")
        if not self.is_configured:
            raise RuntimeError("n8n non configuré. Renseigner N8N_BASE_URL et N8N_API_KEY dans .env")
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            r = await client.post(
                f"{self.base_url}/api/v1{path}",
                headers=self._headers(),
                json=json_body or {},
            )
            if r.status_code >= 400:
                body = r.text[:500]
                raise httpx.HTTPStatusError(
                    f"{r.status_code} {r.reason_phrase} — {body}",
                    request=r.request, response=r,
                )
            return r.json()

    async def _patch(self, path: str, json_body: Optional[Dict] = None) -> Dict[str, Any]:
        if not _HTTPX:
            raise RuntimeError("httpx non installé (pip install httpx)")
        if not self.is_configured:
            raise RuntimeError("n8n non configuré. Renseigner N8N_BASE_URL et N8N_API_KEY dans .env")
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            r = await client.patch(
                f"{self.base_url}/api/v1{path}",
                headers=self._headers(),
                json=json_body or {},
            )
            if r.status_code >= 400:
                body = r.text[:500]
                raise httpx.HTTPStatusError(
                    f"{r.status_code} {r.reason_phrase} — {body}",
                    request=r.request, response=r,
                )
            return r.json()

    async def _put(self, path: str, json_body: Optional[Dict] = None) -> Dict[str, Any]:
        if not _HTTPX:
            raise RuntimeError("httpx non installé (pip install httpx)")
        if not self.is_configured:
            raise RuntimeError("n8n non configuré. Renseigner N8N_BASE_URL et N8N_API_KEY dans .env")
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            r = await client.put(
                f"{self.base_url}/api/v1{path}",
                headers=self._headers(),
                json=json_body or {},
            )
            if r.status_code >= 400:
                body = r.text[:500]
                raise httpx.HTTPStatusError(
                    f"{r.status_code} {r.reason_phrase} — {body}",
                    request=r.request, response=r,
                )
            return r.json()

    async def _delete(self, path: str) -> Dict[str, Any]:
        if not _HTTPX:
            raise RuntimeError("httpx non installé (pip install httpx)")
        if not self.is_configured:
            raise RuntimeError("n8n non configuré. Renseigner N8N_BASE_URL et N8N_API_KEY dans .env")
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            r = await client.delete(
                f"{self.base_url}/api/v1{path}",
                headers=self._headers(),
            )
            if r.status_code >= 400:
                body = r.text[:500]
                raise httpx.HTTPStatusError(
                    f"{r.status_code} {r.reason_phrase} — {body}",
                    request=r.request, response=r,
                )
            return r.json()

    # ── API publique ────────────────────────────────────────────────────

    async def health(self) -> Dict[str, Any]:
        """Vérifie la santé de l'instance n8n."""
        if not _HTTPX:
            raise RuntimeError("httpx non installé")
        if not self.is_configured:
            raise RuntimeError("n8n non configuré")
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            r = await client.get(
                f"{self.base_url}/healthz",
                headers=self._headers(),
            )
            return {"status": "ok" if r.status_code == 200 else "error", "code": r.status_code}

    async def list_workflows(self, limit: int = 50, active_only: bool = False) -> List[Dict[str, Any]]:
        """Liste tous les workflows n8n."""
        params: Dict[str, Any] = {"limit": limit}
        if active_only:
            params["active"] = "true"
        data = await self._get("/workflows", params=params)
        return data.get("data", [])

    async def get_workflow(self, workflow_id: str) -> Dict[str, Any]:
        """Récupère les détails d'un workflow."""
        return await self._get(f"/workflows/{workflow_id}")

    async def activate_workflow(self, workflow_id: str) -> Dict[str, Any]:
        """Active un workflow (POST /workflows/{id}/activate)."""
        return await self._post(f"/workflows/{workflow_id}/activate")

    async def deactivate_workflow(self, workflow_id: str) -> Dict[str, Any]:
        """Désactive un workflow (POST /workflows/{id}/deactivate)."""
        return await self._post(f"/workflows/{workflow_id}/deactivate")

    async def update_workflow(
        self, workflow_id: str, name: Optional[str] = None,
        nodes: Optional[List[Dict]] = None, connections: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        """Met à jour la structure d'un workflow (PUT /workflows/{id}).
        
        Récupère d'abord le workflow existant pour ne modifier que les champs fournis.
        Sanitise les nœuds pour éviter les erreurs n8n (IDs manquants, paramètres invalides).
        """
        current = await self.get_workflow(workflow_id)
        final_nodes = nodes if nodes is not None else current.get("nodes", [])
        # Sanitiser chaque nœud
        for node in final_nodes:
            if "id" not in node:
                node["id"] = str(uuid.uuid4())
            if "typeVersion" not in node:
                node["typeVersion"] = 1
            if "position" not in node:
                node["position"] = [250, 300]
            if "parameters" not in node:
                node["parameters"] = {}
            # Nettoyer les champs inconnus qui peuvent faire crasher n8n
            allowed = {"id", "name", "type", "typeVersion", "position", "parameters", "credentials", "disabled", "notes", "notesInFlow"}
            extra_keys = set(node.keys()) - allowed
            for k in extra_keys:
                del node[k]
        body: Dict[str, Any] = {
            "name": name if name is not None else current.get("name", ""),
            "nodes": final_nodes,
            "connections": connections if connections is not None else current.get("connections", {}),
            "settings": current.get("settings", {"executionOrder": "v1"}),
        }
        return await self._put(f"/workflows/{workflow_id}", json_body=body)

    async def trigger_workflow(self, workflow_id: str, data: Optional[Dict] = None) -> Dict[str, Any]:
        """Déclenche un workflow via webhook test (production trigger)."""
        return await self._post(f"/workflows/{workflow_id}/run", json_body=data or {})

    async def trigger_webhook(self, webhook_path: str, data: Optional[Dict] = None) -> Dict[str, Any]:
        """Déclenche un workflow via son webhook URL directe."""
        if not _HTTPX:
            raise RuntimeError("httpx non installé")
        if not self.is_configured:
            raise RuntimeError("n8n non configuré")
        url = f"{self.base_url}/webhook/{webhook_path}"
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            r = await client.post(url, json=data or {})
            r.raise_for_status()
            try:
                return r.json()
            except Exception:
                return {"status": "ok", "response": r.text[:500]}

    async def list_executions(
        self, workflow_id: Optional[str] = None, limit: int = 20, status: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Liste les exécutions passées."""
        params: Dict[str, Any] = {"limit": limit}
        if workflow_id:
            params["workflowId"] = workflow_id
        if status:
            params["status"] = status  # success, error, waiting
        data = await self._get("/executions", params=params)
        return data.get("data", [])

    async def get_execution(self, execution_id: str) -> Dict[str, Any]:
        """Récupère les détails d'une exécution."""
        return await self._get(f"/executions/{execution_id}")

    async def delete_workflow(self, workflow_id: str) -> Dict[str, Any]:
        """Supprime un workflow."""
        return await self._delete(f"/workflows/{workflow_id}")

    async def get_node_types(self) -> List[Dict[str, Any]]:
        """Récupère la liste des types de nœuds disponibles dans l'instance n8n."""
        data = await self._get("/node-types")
        return data if isinstance(data, list) else data.get("data", [])

    async def create_workflow(self, name: str, nodes: Optional[List[Dict]] = None, active: bool = False) -> Dict[str, Any]:
        """Crée un nouveau workflow. Inclut automatiquement un nœud Manual Trigger si aucun nœud fourni."""
        if not nodes:
            # n8n requiert au minimum un nœud trigger valide avec un id unique
            nodes = [
                {
                    "id": str(uuid.uuid4()),
                    "parameters": {},
                    "name": "When clicking 'Test workflow'",
                    "type": "n8n-nodes-base.manualTrigger",
                    "typeVersion": 1,
                    "position": [250, 300],
                }
            ]
        else:
            # S'assurer que chaque nœud a un id
            for node in nodes:
                if "id" not in node:
                    node["id"] = str(uuid.uuid4())
        # n8n refuse le champ "active" dans le body de création (read-only)
        # → créer sans ce champ, puis activer séparément si demandé
        body: Dict[str, Any] = {
            "name": name,
            "nodes": nodes,
            "connections": {},
            "settings": {"executionOrder": "v1"},
        }
        result = await self._post("/workflows", json_body=body)
        # Activer après création si demandé
        if active and result.get("id"):
            try:
                await self.activate_workflow(str(result["id"]))
                result["active"] = True
            except Exception as exc:
                logger.warning(f"[N8N] Workflow créé mais activation échouée: {exc}")
        return result

    # ── API publique n8n.io Templates (8968+ templates, sans auth) ─────

    _N8N_TPL_API = "https://api.n8n.io"

    async def search_online_templates(
        self, query: str, category: str = "", limit: int = 10
    ) -> List[Dict[str, Any]]:
        """Recherche des templates dans la bibliothèque publique n8n.io."""
        if not _HTTPX:
            raise RuntimeError("httpx non installé")
        params: Dict[str, Any] = {"search": query, "rows": limit, "page": 1}
        if category:
            params["category"] = category
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(f"{self._N8N_TPL_API}/templates/search", params=params)
            r.raise_for_status()
            data = r.json()
        return data.get("workflows", [])

    async def get_online_template(self, template_id: int) -> Dict[str, Any]:
        """Récupère un template depuis api.n8n.io au format prêt à importer.
        
        Utilise /workflows/templates/{id} (format flat), qui retourne:
        { "id": ..., "name": ..., "workflow": { "nodes": [...], "connections": {} } }
        """
        if not _HTTPX:
            raise RuntimeError("httpx non installé")
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(f"{self._N8N_TPL_API}/workflows/templates/{template_id}")
            if r.status_code == 404:
                raise ValueError(f"Template {template_id} introuvable sur n8n.io")
            r.raise_for_status()
            return r.json()

    async def import_online_template(
        self, template_id: int, name: Optional[str] = None, activate: bool = False
    ) -> Dict[str, Any]:
        """Importe un template n8n.io dans l'instance locale.
        
        1. Fetch depuis api.n8n.io/workflows/templates/{id}
        2. Sanitise les nœuds (IDs frais, champs obligatoires)
        3. Crée + connecte dans n8n local
        4. Active optionnellement
        """
        tpl = await self.get_online_template(template_id)
        wf_def = tpl.get("workflow", {})
        nodes = list(wf_def.get("nodes", []))
        connections = wf_def.get("connections", {})
        final_name = name or tpl.get("name", f"Template {template_id}")

        allowed_keys = {
            "id", "name", "type", "typeVersion", "position",
            "parameters", "credentials", "disabled", "notes", "notesInFlow",
        }
        for node in nodes:
            node["id"] = str(uuid.uuid4())
            node.setdefault("typeVersion", 1)
            node.setdefault("position", [250, 300])
            node.setdefault("parameters", {})
            for k in list(node.keys()):
                if k not in allowed_keys:
                    del node[k]

        result = await self.create_workflow(name=final_name, nodes=nodes)
        wf_id = result.get("id")
        if wf_id and connections:
            await self.update_workflow(wf_id, connections=connections)
        if activate and wf_id:
            try:
                await self.activate_workflow(str(wf_id))
                result["active"] = True
            except Exception as exc:
                logger.warning(f"[N8N] Template importé mais activation échouée: {exc}")
        result["template_id"] = template_id
        result["template_name"] = tpl.get("name", "")
        return result

    async def create_from_template(self, template_name: str, activate: bool = False) -> Dict[str, Any]:
        """Crée un workflow depuis un template pré-construit et testé.
        
        Templates disponibles: crypto_alert, webhook_relay, file_monitor,
        daily_report, api_health_check, email_to_telegram, email_to_whatsapp
        """
        tpl = _WORKFLOW_TEMPLATES.get(template_name)
        if not tpl:
            available = ", ".join(_WORKFLOW_TEMPLATES.keys())
            raise ValueError(f"Template '{template_name}' inconnu. Disponibles: {available}")

        wf_name = tpl["name"]
        nodes = []
        for n in tpl["nodes"]:
            node = dict(n)
            node["id"] = str(uuid.uuid4())
            nodes.append(node)
        connections = tpl.get("connections", {})

        result = await self.create_workflow(name=wf_name, nodes=nodes)
        wf_id = result.get("id")
        if not wf_id:
            return result

        # Appliquer les connexions
        if connections:
            await self.update_workflow(wf_id, connections=connections)

        # Activer si demandé
        if activate:
            try:
                await self.activate_workflow(wf_id)
                result["active"] = True
            except Exception as exc:
                logger.warning(f"[N8N] Template créé mais activation échouée: {exc}")

        return result


# ═══════════════════════════════════════════════════════════════════════════
#  Templates de workflows pré-construits et testés
# ═══════════════════════════════════════════════════════════════════════════

def _uid() -> str:
    return str(uuid.uuid4())

_WORKFLOW_TEMPLATES: Dict[str, Dict[str, Any]] = {
    "crypto_alert": {
        "name": "🚀 Pro — Crypto Prix + Alertes Telegram",
        "description": "Vérifie les prix crypto toutes les heures via CoinGecko, alerte si variation > 5%",
        "nodes": [
            {
                "name": "Toutes les heures",
                "type": "n8n-nodes-base.scheduleTrigger",
                "typeVersion": 1,
                "position": [250, 300],
                "parameters": {"rule": {"interval": [{"field": "hours", "hoursInterval": 1}]}},
            },
            {
                "name": "CoinGecko API",
                "type": "n8n-nodes-base.httpRequest",
                "typeVersion": 4,
                "position": [480, 300],
                "parameters": {
                    "url": "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum&vs_currencies=usd&include_24hr_change=true",
                    "method": "GET",
                    "options": {},
                },
            },
            {
                "name": "Extraire données",
                "type": "n8n-nodes-base.set",
                "typeVersion": 3,
                "position": [700, 300],
                "parameters": {
                    "mode": "manual",
                    "duplicateItem": False,
                    "assignments": {
                        "assignments": [
                            {"id": _uid(), "name": "btc_price", "value": "={{ $json.bitcoin.usd }}", "type": "number"},
                            {"id": _uid(), "name": "btc_change", "value": "={{ $json.bitcoin.usd_24h_change }}", "type": "number"},
                            {"id": _uid(), "name": "eth_price", "value": "={{ $json.ethereum.usd }}", "type": "number"},
                            {"id": _uid(), "name": "eth_change", "value": "={{ $json.ethereum.usd_24h_change }}", "type": "number"},
                        ],
                    },
                },
            },
            {
                "name": "Variation > 5% ?",
                "type": "n8n-nodes-base.if",
                "typeVersion": 2,
                "position": [920, 300],
                "parameters": {
                    "conditions": {
                        "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict"},
                        "conditions": [
                            {
                                "id": _uid(),
                                "leftValue": "={{ Math.abs($json.btc_change) }}",
                                "rightValue": "5",
                                "operator": {"type": "number", "operation": "gt"},
                            },
                        ],
                        "combinator": "or",
                    },
                },
            },
            {
                "name": "Note — Pas d'alerte",
                "type": "n8n-nodes-base.stickyNote",
                "typeVersion": 1,
                "position": [1100, 500],
                "parameters": {"content": "## Pas d'alerte\nVariation < 5% → rien à faire"},
            },
            {
                "name": "Log résultats",
                "type": "n8n-nodes-base.code",
                "typeVersion": 2,
                "position": [920, 520],
                "parameters": {
                    "jsCode": (
                        "const now = new Date().toISOString();\n"
                        "const btc = $input.all()[0].json;\n"
                        "console.log(`[${now}] BTC: $${btc.btc_price} (${btc.btc_change.toFixed(2)}%) | ETH: $${btc.eth_price} (${btc.eth_change.toFixed(2)}%)`);\n"
                        "return $input.all();"
                    ),
                },
            },
        ],
        "connections": {
            "Toutes les heures": {"main": [[{"node": "CoinGecko API", "type": "main", "index": 0}]]},
            "CoinGecko API": {"main": [[{"node": "Extraire données", "type": "main", "index": 0}]]},
            "Extraire données": {"main": [[{"node": "Variation > 5% ?", "type": "main", "index": 0}, {"node": "Log résultats", "type": "main", "index": 0}]]},
        },
    },

    "webhook_relay": {
        "name": "🔗 Pro — Webhook Relay + Notification",
        "description": "Reçoit un webhook, traite les données, envoie une notification",
        "nodes": [
            {
                "name": "Webhook entrant",
                "type": "n8n-nodes-base.webhook",
                "typeVersion": 2,
                "position": [250, 300],
                "parameters": {"path": "lumena-relay", "httpMethod": "POST", "responseMode": "responseNode"},
            },
            {
                "name": "Valider données",
                "type": "n8n-nodes-base.if",
                "typeVersion": 2,
                "position": [480, 300],
                "parameters": {
                    "conditions": {
                        "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict"},
                        "conditions": [
                            {
                                "id": _uid(),
                                "leftValue": "={{ $json.body }}",
                                "rightValue": "",
                                "operator": {"type": "string", "operation": "exists"},
                            },
                        ],
                        "combinator": "and",
                    },
                },
            },
            {
                "name": "Formater message",
                "type": "n8n-nodes-base.set",
                "typeVersion": 3,
                "position": [700, 200],
                "parameters": {
                    "mode": "manual",
                    "duplicateItem": False,
                    "assignments": {
                        "assignments": [
                            {"id": _uid(), "name": "message", "value": "=📨 Webhook reçu:\n{{ JSON.stringify($json.body, null, 2) }}", "type": "string"},
                            {"id": _uid(), "name": "timestamp", "value": "={{ new Date().toISOString() }}", "type": "string"},
                        ],
                    },
                },
            },
            {
                "name": "Répondre OK",
                "type": "n8n-nodes-base.respondToWebhook",
                "typeVersion": 1,
                "position": [920, 200],
                "parameters": {"respondWith": "json", "responseBody": "={\"status\":\"ok\",\"received_at\":\"{{ $json.timestamp }}\"}"},
            },
            {
                "name": "Répondre Erreur",
                "type": "n8n-nodes-base.respondToWebhook",
                "typeVersion": 1,
                "position": [700, 420],
                "parameters": {"respondWith": "json", "responseBody": "={\"status\":\"error\",\"message\":\"Invalid payload\"}", "options": {"responseCode": 400}},
            },
        ],
        "connections": {
            "Webhook entrant": {"main": [[{"node": "Valider données", "type": "main", "index": 0}]]},
            "Valider données": {"main": [[{"node": "Formater message", "type": "main", "index": 0}], [{"node": "Répondre Erreur", "type": "main", "index": 0}]]},
            "Formater message": {"main": [[{"node": "Répondre OK", "type": "main", "index": 0}]]},
        },
    },

    "api_health_check": {
        "name": "🏥 Pro — API Health Check Multi-Services",
        "description": "Vérifie la santé de plusieurs APIs/services toutes les 5 minutes et log les résultats",
        "nodes": [
            {
                "name": "Toutes les 5 min",
                "type": "n8n-nodes-base.scheduleTrigger",
                "typeVersion": 1,
                "position": [250, 300],
                "parameters": {"rule": {"interval": [{"field": "minutes", "minutesInterval": 5}]}},
            },
            {
                "name": "Check Lumena",
                "type": "n8n-nodes-base.httpRequest",
                "typeVersion": 4,
                "position": [480, 200],
                "parameters": {"url": "http://host.docker.internal:8080/api/health", "method": "GET", "options": {"timeout": 10000}},
            },
            {
                "name": "Check CoinGecko",
                "type": "n8n-nodes-base.httpRequest",
                "typeVersion": 4,
                "position": [480, 420],
                "parameters": {"url": "https://api.coingecko.com/api/v3/ping", "method": "GET", "options": {"timeout": 10000}},
            },
            {
                "name": "Merge résultats",
                "type": "n8n-nodes-base.merge",
                "typeVersion": 3,
                "position": [720, 300],
                "parameters": {"mode": "append"},
            },
            {
                "name": "Log santé",
                "type": "n8n-nodes-base.code",
                "typeVersion": 2,
                "position": [940, 300],
                "parameters": {
                    "jsCode": (
                        "const results = $input.all();\n"
                        "const now = new Date().toISOString();\n"
                        "for (const item of results) {\n"
                        "  const status = item.json.statusCode || item.json.gecko_says ? 'UP' : 'DOWN';\n"
                        "  console.log(`[${now}] Service check: ${status}`);\n"
                        "}\n"
                        "return results;"
                    ),
                },
            },
        ],
        "connections": {
            "Toutes les 5 min": {"main": [[{"node": "Check Lumena", "type": "main", "index": 0}, {"node": "Check CoinGecko", "type": "main", "index": 0}]]},
            "Check Lumena": {"main": [[{"node": "Merge résultats", "type": "main", "index": 0}]]},
            "Check CoinGecko": {"main": [[{"node": "Merge résultats", "type": "main", "index": 1}]]},
            "Merge résultats": {"main": [[{"node": "Log santé", "type": "main", "index": 0}]]},
        },
    },

    "daily_report": {
        "name": "📊 Pro — Rapport Quotidien Automatique",
        "description": "Collecte des données chaque jour à 8h, génère un rapport et l'envoie",
        "nodes": [
            {
                "name": "Chaque jour à 8h",
                "type": "n8n-nodes-base.scheduleTrigger",
                "typeVersion": 1,
                "position": [250, 300],
                "parameters": {"rule": {"interval": [{"field": "cronExpression", "expression": "0 8 * * *"}]}},
            },
            {
                "name": "Météo Paris",
                "type": "n8n-nodes-base.httpRequest",
                "typeVersion": 4,
                "position": [480, 200],
                "parameters": {"url": "https://wttr.in/Paris?format=j1", "method": "GET", "options": {}},
            },
            {
                "name": "News headlines",
                "type": "n8n-nodes-base.httpRequest",
                "typeVersion": 4,
                "position": [480, 420],
                "parameters": {"url": "https://newsdata.io/api/1/latest?language=fr&category=technology&apikey=pub_demo", "method": "GET", "options": {}},
            },
            {
                "name": "Générer rapport",
                "type": "n8n-nodes-base.code",
                "typeVersion": 2,
                "position": [720, 300],
                "parameters": {
                    "jsCode": (
                        "const weather = $('Météo Paris').first().json;\n"
                        "const news = $('News headlines').first().json;\n"
                        "const temp = weather?.current_condition?.[0]?.temp_C || '?';\n"
                        "const desc = weather?.current_condition?.[0]?.weatherDesc?.[0]?.value || '?';\n"
                        "const articles = (news?.results || []).slice(0, 3).map(a => `• ${a.title}`).join('\\n');\n"
                        "const report = `📊 Rapport du ${new Date().toLocaleDateString('fr-FR')}\\n\"\n"
                        "  + `\\n🌤️ Météo Paris: ${temp}°C — ${desc}\"\n"
                        "  + `\\n\\n📰 Actus tech:\\n${articles || 'Aucune actualité'}`;\n"
                        "return [{json: {report}}];"
                    ),
                },
            },
        ],
        "connections": {
            "Chaque jour à 8h": {"main": [[{"node": "Météo Paris", "type": "main", "index": 0}, {"node": "News headlines", "type": "main", "index": 0}]]},
            "Météo Paris": {"main": [[{"node": "Générer rapport", "type": "main", "index": 0}]]},
            "News headlines": {"main": [[{"node": "Générer rapport", "type": "main", "index": 0}]]},
        },
    },

    "email_to_telegram": {
        "name": "📧→📱 Pro — Email forwarding Telegram",
        "description": "Template de base : reçoit un webhook (email parser) et relaie sur Telegram",
        "nodes": [
            {
                "name": "Webhook email",
                "type": "n8n-nodes-base.webhook",
                "typeVersion": 2,
                "position": [250, 300],
                "parameters": {"path": "email-relay", "httpMethod": "POST"},
            },
            {
                "name": "Extraire sujet et corps",
                "type": "n8n-nodes-base.set",
                "typeVersion": 3,
                "position": [480, 300],
                "parameters": {
                    "mode": "manual",
                    "duplicateItem": False,
                    "assignments": {
                        "assignments": [
                            {"id": _uid(), "name": "subject", "value": "={{ $json.body.subject || 'Sans objet' }}", "type": "string"},
                            {"id": _uid(), "name": "from", "value": "={{ $json.body.from || 'Inconnu' }}", "type": "string"},
                            {"id": _uid(), "name": "preview", "value": "={{ ($json.body.text || '').substring(0, 200) }}", "type": "string"},
                        ],
                    },
                },
            },
            {
                "name": "Log",
                "type": "n8n-nodes-base.code",
                "typeVersion": 2,
                "position": [700, 300],
                "parameters": {
                    "jsCode": (
                        "const d = $input.first().json;\n"
                        "console.log(`📧 Email de ${d.from}: ${d.subject}`);\n"
                        "return $input.all();"
                    ),
                },
            },
        ],
        "connections": {
            "Webhook email": {"main": [[{"node": "Extraire sujet et corps", "type": "main", "index": 0}]]},
            "Extraire sujet et corps": {"main": [[{"node": "Log", "type": "main", "index": 0}]]},
        },
    },

    "email_to_whatsapp": {
        "name": "📧→📱 Pro — Email forwarding WhatsApp",
        "description": "Template de base : reçoit un webhook (email parser) et relaie sur WhatsApp via l'API Cloud",
        "nodes": [
            {
                "name": "Webhook email",
                "type": "n8n-nodes-base.webhook",
                "typeVersion": 2,
                "position": [250, 300],
                "parameters": {"path": "email-relay-wa", "httpMethod": "POST"},
            },
            {
                "name": "Extraire sujet et corps",
                "type": "n8n-nodes-base.set",
                "typeVersion": 3,
                "position": [480, 300],
                "parameters": {
                    "mode": "manual",
                    "duplicateItem": False,
                    "assignments": {
                        "assignments": [
                            {"id": _uid(), "name": "subject", "value": "={{ $json.body.subject || 'Sans objet' }}", "type": "string"},
                            {"id": _uid(), "name": "from", "value": "={{ $json.body.from || 'Inconnu' }}", "type": "string"},
                            {"id": _uid(), "name": "preview", "value": "={{ ($json.body.text || '').substring(0, 200) }}", "type": "string"},
                        ],
                    },
                },
            },
            {
                "name": "Envoyer WhatsApp",
                "type": "n8n-nodes-base.httpRequest",
                "typeVersion": 4,
                "position": [700, 300],
                "parameters": {
                    "method": "POST",
                    "url": "=https://graph.facebook.com/v21.0/{{ $env.WHATSAPP_PHONE_NUMBER_ID }}/messages",
                    "sendHeaders": True,
                    "headerParameters": {
                        "parameters": [
                            {"name": "Authorization", "value": "=Bearer {{ $env.WHATSAPP_ACCESS_TOKEN }}"},
                            {"name": "Content-Type", "value": "application/json"},
                        ],
                    },
                    "sendBody": True,
                    "contentType": "json",
                    "body": {
                        "messaging_product": "whatsapp",
                        "to": "={{ $env.WHATSAPP_OWNER_PHONE }}",
                        "type": "text",
                        "text": {"body": "=📧 De: {{ $json.from }}\nSujet: {{ $json.subject }}\n\n{{ $json.preview }}"},
                    },
                },
            },
        ],
        "connections": {
            "Webhook email": {"main": [[{"node": "Extraire sujet et corps", "type": "main", "index": 0}]]},
            "Extraire sujet et corps": {"main": [[{"node": "Envoyer WhatsApp", "type": "main", "index": 0}]]},
        },
    },

    "file_monitor": {
        "name": "📁 Pro — Surveillance Dossier + Traitement",
        "description": "Surveille un dossier local, traite les fichiers et log",
        "nodes": [
            {
                "name": "Surveiller dossier",
                "type": "n8n-nodes-base.localFileTrigger",
                "typeVersion": 1,
                "position": [250, 300],
                "parameters": {"path": "/data", "events": ["add", "change"]},
            },
            {
                "name": "Infos fichier",
                "type": "n8n-nodes-base.set",
                "typeVersion": 3,
                "position": [480, 300],
                "parameters": {
                    "mode": "manual",
                    "duplicateItem": False,
                    "assignments": {
                        "assignments": [
                            {"id": _uid(), "name": "filename", "value": "={{ $json.path }}", "type": "string"},
                            {"id": _uid(), "name": "event", "value": "={{ $json.event }}", "type": "string"},
                            {"id": _uid(), "name": "detected_at", "value": "={{ new Date().toISOString() }}", "type": "string"},
                        ],
                    },
                },
            },
            {
                "name": "Log",
                "type": "n8n-nodes-base.code",
                "typeVersion": 2,
                "position": [700, 300],
                "parameters": {
                    "jsCode": (
                        "const f = $input.first().json;\n"
                        "console.log(`📁 [${f.detected_at}] ${f.event}: ${f.filename}`);\n"
                        "return $input.all();"
                    ),
                },
            },
        ],
        "connections": {
            "Surveiller dossier": {"main": [[{"node": "Infos fichier", "type": "main", "index": 0}]]},
            "Infos fichier": {"main": [[{"node": "Log", "type": "main", "index": 0}]]},
        },
    },
}


# ═══════════════════════════════════════════════════════════════════════════
#  Auto-start / auto-stop n8n via Docker
# ═══════════════════════════════════════════════════════════════════════════

_CONTAINER_NAME = "lumena_n8n"
_N8N_IMAGE = "n8nio/n8n:latest"
_N8N_PORT = 5678


async def _run_cmd(*args: str, timeout: float = 60.0) -> tuple[int, str]:
    """Exécute une commande système et retourne (returncode, stdout)."""
    import asyncio
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return -1, f"timeout after {timeout}s"
    return proc.returncode, (stdout or b"").decode("utf-8", errors="replace").strip()


async def _docker_available() -> bool:
    """Vérifie si docker est disponible."""
    try:
        rc, _ = await _run_cmd("docker", "info")
        return rc == 0
    except FileNotFoundError:
        return False


async def _container_status() -> str:
    """Retourne 'running', 'exited', 'missing'."""
    rc, out = await _run_cmd(
        "docker", "inspect", "-f", "{{.State.Status}}", _CONTAINER_NAME,
    )
    if rc != 0:
        return "missing"
    return out.strip().lower()


async def ensure_n8n_running() -> str:
    """
    Démarre automatiquement n8n via Docker si nécessaire.
    Retourne un message de statut.
    """
    if not await _docker_available():
        return "Docker non disponible — n8n doit être lancé manuellement"

    status = await _container_status()

    if status == "running":
        logger.info("[N8N] Container déjà en cours d'exécution")
        return "n8n déjà en cours d'exécution"

    if status == "exited":
        logger.info("[N8N] Redémarrage du container existant...")
        rc, out = await _run_cmd("docker", "start", _CONTAINER_NAME)
        if rc == 0:
            logger.info("[N8N] Container redémarré")
            return "n8n redémarré"
        return f"Erreur redémarrage n8n: {out}"

    # Container inexistant → créer
    logger.info("[N8N] Création du container n8n...")
    rc, out = await _run_cmd(
        "docker", "run", "-d",
        "--name", _CONTAINER_NAME,
        "-p", f"{_N8N_PORT}:{_N8N_PORT}",
        "-v", "lumena_n8n_data:/home/node/.n8n",
        "--restart", "unless-stopped",
        _N8N_IMAGE,
    )
    if rc == 0:
        logger.info(f"[N8N] Container créé : {out[:12]}")
        return "n8n installé et démarré (premier lancement)"
    return f"Erreur création n8n: {out}"


async def stop_n8n() -> str:
    """Arrête le container n8n."""
    if not await _docker_available():
        return "Docker non disponible"
    status = await _container_status()
    if status != "running":
        return "n8n pas en cours d'exécution"
    rc, out = await _run_cmd("docker", "stop", _CONTAINER_NAME)
    if rc == 0:
        logger.info("[N8N] Container arrêté")
        return "n8n arrêté"
    return f"Erreur arrêt n8n: {out}"


# ── Singleton ───────────────────────────────────────────────────────────────

_instance: Optional[N8nBridge] = None
_instance_lock = threading.Lock()


def get_n8n_bridge() -> N8nBridge:
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = N8nBridge()
    return _instance
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the Apache License, Version 2.0
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
