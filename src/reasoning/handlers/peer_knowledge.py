"""Lot D Phase 10 — Tool query_peer_knowledge pour l'agent Lumena.

Permet à Lumena d'interroger la mémoire d'une autre instance Lumena trusted
via POST /api/peer/knowledge/query, et d'intégrer le résumé dans sa réponse
sans importer automatiquement la mémoire distante.

Activé via LUMENA_PEER_COLLABORATION=1 (même flag que delegate_to_peer).
Requiert scope knowledge.query dans allowed_scopes du pair.
Anti-SSRF, sanitization prompt, jamais de token dans la sortie.
"""
from __future__ import annotations

import json
import os
import uuid
from typing import Any, Dict, List, Optional

from src.utils.paths import DATA_DIR

_PEER_REGISTRY_FILE = DATA_DIR / "peer_registry.json"
_MIN_TIMEOUT = 10
_MAX_TIMEOUT = 300
_DEFAULT_MAX_RESULTS = 5
_DEFAULT_SUMMARY_CHARS = 800


def _is_collaboration_enabled() -> bool:
    return os.getenv("LUMENA_PEER_COLLABORATION", "0").strip() == "1"


def _load_peers() -> Dict[str, dict]:
    try:
        if _PEER_REGISTRY_FILE.exists():
            return json.loads(_PEER_REGISTRY_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _audit(event: str, instance_id: str, task_id: str, scope: str,
           status: str, detail: str = "") -> None:
    try:
        from src.runtime.peer_protocol import write_audit_log
        write_audit_log(
            event=event,
            from_instance_id=instance_id,
            task_id=task_id,
            scope=scope,
            status=status,
            detail=detail,
        )
    except Exception:
        pass


async def query_peer_knowledge_handler(
    ctx: Any,
    instance_id: str,
    query: str,
    max_results: int = _DEFAULT_MAX_RESULTS,
    max_summary_chars: int = _DEFAULT_SUMMARY_CHARS,
    timeout_sec: int = 60,
) -> Any:
    """Interroge la mémoire d'une instance Lumena trusted (read-only).

    Vérifie : feature flag, paramètres, pair existant, trust=trusted,
    peer_token_outbound, scope knowledge.query dans allowed_scopes,
    anti-SSRF, absence de secret dans la query.
    Appelle POST /api/peer/knowledge/query.
    Retourne un résumé contrôlé sans importer la mémoire distante.
    """
    from .contracts import HandlerResult
    from src.runtime.peer_scopes import VALID_SCOPES

    task_id = f"kq-{uuid.uuid4().hex[:12]}"

    # 1. Feature flag
    if not _is_collaboration_enabled():
        return HandlerResult.fail(
            "Knowledge query inter-instances désactivée. "
            "Activez LUMENA_PEER_COLLABORATION=1 pour utiliser ce tool.",
            handler_name="query_peer_knowledge",
        )

    # 2. Paramètres obligatoires
    if not instance_id or not instance_id.strip():
        return HandlerResult.fail(
            "instance_id est requis.",
            handler_name="query_peer_knowledge",
        )
    if not query or not query.strip():
        return HandlerResult.fail(
            "query est requise.",
            handler_name="query_peer_knowledge",
        )

    instance_id = instance_id.strip()

    # 3. Pas de secret dans la query
    from src.runtime.peer_messages import has_secret_pattern
    if has_secret_pattern(query):
        _audit("knowledge_query_refused", instance_id, task_id,
               "knowledge.query", "refused", "Secret détecté dans la query")
        return HandlerResult.fail(
            "Query refusée : elle contient un pattern identifié comme secret "
            "(token, clé, hash). Retirez-le avant d'interroger le pair.",
            handler_name="query_peer_knowledge",
        )

    # 4. Scope knowledge.query dans la whitelist globale
    if "knowledge.query" not in VALID_SCOPES:
        return HandlerResult.fail(
            "Scope knowledge.query absent de la whitelist globale (erreur interne).",
            handler_name="query_peer_knowledge",
        )

    # 5. Timeout borné
    safe_timeout = max(_MIN_TIMEOUT, min(_MAX_TIMEOUT, int(timeout_sec)))
    safe_max = max(1, min(20, int(max_results)))
    safe_chars = max(100, min(4000, int(max_summary_chars)))

    # 6. Peer dans le registre
    peers = _load_peers()
    peer = peers.get(instance_id)
    if peer is None:
        _audit("knowledge_query_refused", instance_id, task_id,
               "knowledge.query", "refused", "Pair absent du registre")
        return HandlerResult.fail(
            f"Pair {instance_id!r} inconnu. "
            "Vérifiez la section Réseau Lumena ou le panneau réseau.",
            handler_name="query_peer_knowledge",
        )

    peer_name = peer.get("instance_name") or instance_id[:12]

    # 7. Trust fail-closed
    trust = peer.get("trust", "unknown")
    if trust == "blocked":
        _audit("knowledge_query_refused", instance_id, task_id,
               "knowledge.query", "refused", "Pair bloqué")
        return HandlerResult.fail(
            f"{peer_name!r} est bloqué. Débloquez ce pair avant de l'interroger.",
            handler_name="query_peer_knowledge",
        )
    if trust != "trusted":
        _audit("knowledge_query_refused", instance_id, task_id,
               "knowledge.query", "refused", f"trust={trust!r}")
        return HandlerResult.fail(
            f"{peer_name!r} n'est pas trusted (trust={trust!r}). "
            "Jumelez ce pair via le panneau réseau.",
            handler_name="query_peer_knowledge",
        )

    # 8. Token sortant nécessaire
    outbound_token = peer.get("peer_token_outbound", "")
    if not outbound_token:
        _audit("knowledge_query_refused", instance_id, task_id,
               "knowledge.query", "refused", "Token sortant absent")
        return HandlerResult.fail(
            f"Token sortant manquant pour {peer_name!r}. "
            "Rejumelez ce pair via le code de jumelage.",
            handler_name="query_peer_knowledge",
        )

    # 9. Scope knowledge.query autorisé pour ce pair
    allowed = peer.get("allowed_scopes") or []
    if "knowledge.query" not in allowed:
        _audit("knowledge_query_refused", instance_id, task_id,
               "knowledge.query", "refused",
               "knowledge.query absent de allowed_scopes")
        return HandlerResult.fail(
            f"Scope knowledge.query non autorisé pour {peer_name!r}. "
            f"Scopes actifs : {sorted(allowed) if allowed else 'aucun'}. "
            f"Activez via PUT /api/peers/{instance_id}/scopes.",
            handler_name="query_peer_knowledge",
        )

    # 10. Anti-SSRF
    host = peer.get("host", "")
    port = peer.get("port", 8080)
    try:
        from src.runtime.peer_host_validation import validate_peer_host
        validate_peer_host(host)
    except ValueError as ssrf_err:
        _audit("knowledge_query_refused", instance_id, task_id,
               "knowledge.query", "refused", f"SSRF: {ssrf_err}")
        return HandlerResult.fail(
            f"Host {host!r} refusé (anti-SSRF) : {ssrf_err}",
            handler_name="query_peer_knowledge",
        )

    # 11. Préparer l'enveloppe + payload
    from src.utils.paths import INSTANCE_ID as _OWN_ID

    try:
        from src.runtime.peer_messages import create_sanitized_peer_message
        _env = create_sanitized_peer_message(
            type="knowledge_query",
            scope="knowledge.query",
            from_instance_id=_OWN_ID,
            to_instance_id=instance_id,
            payload={"query": query},
            ttl_seconds=safe_timeout,
        )
        _envelope_dict: dict = _env.to_dict()
    except ValueError as _san_err:
        _audit("knowledge_query_refused", instance_id, task_id,
               "knowledge.query", "refused", f"Sanitization: {_san_err}")
        return HandlerResult.fail(
            "Query refusée : le contenu a échoué la vérification de sécurité.",
            handler_name="query_peer_knowledge",
        )

    payload = {
        "query": query,
        "from_instance_id": _OWN_ID,
        "from_user_id": "local:owner",
        "actor_id": "lumena_agent",
        "max_results": safe_max,
        "max_summary_chars": safe_chars,
        "context": {"peer_message": _envelope_dict},
    }

    url = f"http://{host}:{port}/api/peer/knowledge/query"

    # 12. Audit démarrage
    _audit("knowledge_query_started", instance_id, task_id,
           "knowledge.query", "running")

    # 13. Appel HTTP
    try:
        import httpx as _httpx
        async with _httpx.AsyncClient(timeout=float(safe_timeout)) as client:
            r = await client.post(
                url,
                json=payload,
                headers={"Authorization": f"Bearer {outbound_token}"},
            )

        if r.status_code != 200:
            _audit("knowledge_query_failed", instance_id, task_id,
                   "knowledge.query", "error", f"HTTP {r.status_code}")
            return HandlerResult.fail(
                f"{peer_name!r} a retourné HTTP {r.status_code}. "
                "Vérifiez que le pair est démarré et joignable.",
                handler_name="query_peer_knowledge",
            )

        data = r.json()
        summary = data.get("answer_summary", "")
        confidence = data.get("confidence", 0.0)
        source_count = data.get("source_count", 0)
        tags = data.get("tags") or []

        if not summary:
            _audit("knowledge_query_failed", instance_id, task_id,
                   "knowledge.query", "error", "answer_summary vide")
            return HandlerResult.fail(
                f"Réponse de {peer_name!r} vide ou invalide.",
                handler_name="query_peer_knowledge",
            )

        _audit("knowledge_query_completed", instance_id, task_id,
               "knowledge.query", "completed",
               f"source_count={source_count} confidence={confidence}")

        tags_str = f" [tags: {', '.join(tags)}]" if tags else ""
        return HandlerResult.ok(
            f"Connaissances de {peer_name} (confidence={confidence:.2f}, "
            f"{source_count} source(s){tags_str}) :\n{summary}",
            handler_name="query_peer_knowledge",
        )

    except Exception as exc:
        err = str(exc)
        _audit("knowledge_query_failed", instance_id, task_id,
               "knowledge.query", "error", err)
        if "timeout" in err.lower() or "timed out" in err.lower():
            return HandlerResult.fail(
                f"{peer_name!r} n'a pas répondu dans {safe_timeout}s.",
                handler_name="query_peer_knowledge",
            )
        return HandlerResult.fail(
            f"{peer_name!r} est injoignable ({host}:{port}). "
            "Vérifiez que le pair est démarré.",
            handler_name="query_peer_knowledge",
        )

async def propose_peer_knowledge_handler(
    ctx: Any,
    instance_id: str,
    title: str,
    summary: str,
    tags: Optional[List[str]] = None,
    confidence: float = 0.8,
    source_refs: Optional[List[str]] = None,
    timeout_sec: int = 60,
) -> Any:
    """Propose a controlled summary to a trusted peer without forcing import."""
    from .contracts import HandlerResult
    from src.runtime.peer_messages import has_secret_pattern

    task_id = f"ks-{uuid.uuid4().hex[:12]}"
    if not _is_collaboration_enabled():
        return HandlerResult.fail(
            "Partage de connaissances inter-instances desactive. Activez LUMENA_PEER_COLLABORATION=1.",
            handler_name="propose_peer_knowledge",
        )
    if not instance_id or not instance_id.strip():
        return HandlerResult.fail("instance_id est requis.", handler_name="propose_peer_knowledge")
    if not title or not title.strip():
        return HandlerResult.fail("title est requis.", handler_name="propose_peer_knowledge")
    if not summary or not summary.strip():
        return HandlerResult.fail("summary est requis.", handler_name="propose_peer_knowledge")
    if has_secret_pattern(title) or has_secret_pattern(summary):
        _audit("knowledge_share_refused", instance_id, task_id, "knowledge.share", "refused", "secret")
        return HandlerResult.fail(
            "Partage refuse : le titre ou resume contient un pattern identifie comme secret.",
            handler_name="propose_peer_knowledge",
        )
    for raw in list(tags or []) + list(source_refs or []):
        if has_secret_pattern(str(raw)):
            _audit("knowledge_share_refused", instance_id, task_id, "knowledge.share", "refused", "secret")
            return HandlerResult.fail(
                "Partage refuse : tags/source_refs contiennent un pattern identifie comme secret.",
                handler_name="propose_peer_knowledge",
            )

    instance_id = instance_id.strip()
    safe_timeout = max(_MIN_TIMEOUT, min(_MAX_TIMEOUT, int(timeout_sec)))
    peers = _load_peers()
    peer = peers.get(instance_id)
    if not peer:
        return HandlerResult.fail(f"Pair {instance_id!r} inconnu.", handler_name="propose_peer_knowledge")
    peer_name = peer.get("instance_name") or instance_id[:12]
    if peer.get("trust", "unknown") != "trusted":
        return HandlerResult.fail(
            f"{peer_name!r} n'est pas trusted (trust={peer.get('trust', 'unknown')!r}).",
            handler_name="propose_peer_knowledge",
        )
    outbound_token = peer.get("peer_token_outbound", "")
    if not outbound_token:
        return HandlerResult.fail(f"Token sortant manquant pour {peer_name!r}.", handler_name="propose_peer_knowledge")
    allowed = peer.get("allowed_scopes") or []
    if "knowledge.share" not in allowed:
        return HandlerResult.fail(
            f"Scope knowledge.share non autorise pour {peer_name!r}. Scopes actifs : {sorted(allowed) if allowed else 'aucun'}.",
            handler_name="propose_peer_knowledge",
        )

    host = peer.get("host", "")
    port = peer.get("port", 8080)
    try:
        from src.runtime.peer_host_validation import validate_peer_host
        validate_peer_host(host)
    except ValueError as ssrf_err:
        return HandlerResult.fail(
            f"Host {host!r} refuse (anti-SSRF) : {ssrf_err}",
            handler_name="propose_peer_knowledge",
        )

    from src.utils.paths import INSTANCE_ID as _OWN_ID
    try:
        safe_confidence = max(0.0, min(1.0, float(confidence)))
    except (TypeError, ValueError):
        safe_confidence = 0.0

    payload = {
        "title": title.strip(),
        "summary": summary.strip(),
        "from_instance_id": _OWN_ID,
        "origin_user_id": "local:owner",
        "tags": list(tags or [])[:12],
        "confidence": safe_confidence,
        "source_refs": list(source_refs or [])[:20],
    }

    try:
        import httpx as _httpx
        _audit("knowledge_share_started", instance_id, task_id, "knowledge.share", "running")
        async with _httpx.AsyncClient(timeout=float(safe_timeout)) as client:
            r = await client.post(
                f"http://{host}:{port}/api/peer/knowledge/propose",
                json=payload,
                headers={"Authorization": f"Bearer {outbound_token}"},
            )
        if r.status_code != 200:
            _audit("knowledge_share_failed", instance_id, task_id, "knowledge.share", "error", f"HTTP {r.status_code}")
            return HandlerResult.fail(
                f"{peer_name!r} a refuse la proposition (HTTP {r.status_code}).",
                handler_name="propose_peer_knowledge",
            )
        data = r.json()
        _audit("knowledge_share_completed", instance_id, task_id, "knowledge.share", "completed")
        return HandlerResult.ok(
            f"Connaissance proposee a {peer_name}. knowledge_id distant: {data.get('knowledge_id', '?')}. Import distant non force.",
            handler_name="propose_peer_knowledge",
        )
    except Exception as exc:
        _audit("knowledge_share_failed", instance_id, task_id, "knowledge.share", "error", str(exc))
        return HandlerResult.fail(
            f"{peer_name!r} est injoignable pour le partage de connaissance.",
            handler_name="propose_peer_knowledge",
        )


def get_peer_knowledge_handler_defs() -> List:
    """Retourne les handlers knowledge inter-Lumena.

    Retourne [] si LUMENA_PEER_COLLABORATION != "1".
    """
    if not _is_collaboration_enabled():
        return []

    from .registry_v2 import HandlerDef

    return [
        HandlerDef(
            name="query_peer_knowledge",
            description=(
                "Interroge la mémoire d'une autre instance Lumena trusted. "
                "Retourne un résumé contrôlé des connaissances pertinentes "
                "sans importer la mémoire distante. "
                "Utilise l'instance_id visible dans la section Réseau Lumena du contexte."
            ),
            parameters={
                "properties": {
                    "instance_id": {
                        "type": "string",
                        "description": (
                            "instance_id de l'instance Lumena cible "
                            "(visible dans la section '## Réseau Lumena' du contexte)."
                        ),
                    },
                    "query": {
                        "type": "string",
                        "description": "Question ou sujet à rechercher dans la mémoire du pair.",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Nombre max de résultats (1-20, défaut: 5).",
                    },
                    "max_summary_chars": {
                        "type": "integer",
                        "description": "Taille max du résumé retourné (100-4000, défaut: 800).",
                    },
                    "timeout_sec": {
                        "type": "integer",
                        "description": "Timeout en secondes (10-300, défaut: 60).",
                    },
                },
                "required": ["instance_id", "query"],
            },
            handler=query_peer_knowledge_handler,
            category="peers",
            source_module="handlers.peer_knowledge",
        ),
        HandlerDef(
            name="propose_peer_knowledge",
            description=(
                "Propose un resume de connaissance controle a un pair Lumena trusted. "
                "Le pair distant stocke une proposition seulement; aucun import memoire automatique."
            ),
            parameters={
                "properties": {
                    "instance_id": {
                        "type": "string",
                        "description": "instance_id du pair Lumena cible.",
                    },
                    "title": {
                        "type": "string",
                        "description": "Titre court de la connaissance.",
                    },
                    "summary": {
                        "type": "string",
                        "description": "Resume controle, sans secret ni donnees brutes.",
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Tags optionnels.",
                    },
                    "confidence": {
                        "type": "number",
                        "description": "Confiance 0.0-1.0.",
                    },
                    "source_refs": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "References non secretes.",
                    },
                    "timeout_sec": {
                        "type": "integer",
                        "description": "Timeout en secondes (10-300, defaut: 60).",
                    },
                },
                "required": ["instance_id", "title", "summary"],
            },
            handler=propose_peer_knowledge_handler,
            category="peers",
            source_module="handlers.peer_knowledge",
        ),
    ]
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
