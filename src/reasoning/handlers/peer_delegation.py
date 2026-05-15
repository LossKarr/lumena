"""Lot B Phase 10 — Tool delegate_to_peer pour l'agent Lumena.

Permet à Lumena d'appeler une autre instance Lumena trusted depuis le ReAct,
de récupérer sa réponse et de l'intégrer dans son raisonnement.

Activé via LUMENA_PEER_COLLABORATION=1 (défaut : 0).
Aucun token brut dans les sorties, logs utilisateur ou contexte LLM.
"""
from __future__ import annotations

import json
import os
import uuid
from typing import Any, Dict, List

from src.utils.paths import DATA_DIR

_PEER_REGISTRY_FILE = DATA_DIR / "peer_registry.json"
_MIN_TIMEOUT = 10
_MAX_TIMEOUT = 300


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


async def delegate_to_peer_handler(
    ctx: Any,
    instance_id: str,
    prompt: str,
    scope: str = "chat",
    timeout_sec: int = 120,
) -> Any:
    """Délègue une question ou une tâche à une autre instance Lumena trusted.

    Vérifie dans l'ordre : feature flag, paramètres, scope valide, peer existant,
    trust=trusted, token sortant, scope dans allowed_scopes.
    Appelle POST /api/peer/delegate avec Authorization: Bearer <peer_token_outbound>.
    Jamais de token dans la sortie ou l'audit.
    """
    from .contracts import HandlerResult
    from src.runtime.peer_scopes import VALID_SCOPES

    task_id = f"deleg-{uuid.uuid4().hex[:12]}"

    # 1. Feature flag
    if not _is_collaboration_enabled():
        return HandlerResult.fail(
            "Délégation inter-instances désactivée. "
            "Activez LUMENA_PEER_COLLABORATION=1 pour utiliser ce tool.",
            handler_name="delegate_to_peer",
        )

    # 2. Paramètres obligatoires
    if not instance_id or not instance_id.strip():
        return HandlerResult.fail(
            "instance_id est requis. Cherchez-le dans la section Réseau Lumena du contexte.",
            handler_name="delegate_to_peer",
        )
    if not prompt or not prompt.strip():
        return HandlerResult.fail("prompt est requis.", handler_name="delegate_to_peer")

    instance_id = instance_id.strip()

    # 3. Scope dans la whitelist globale
    if scope not in VALID_SCOPES:
        _audit("delegate_tool_refused", instance_id, task_id, scope, "refused",
               f"Scope inconnu : {scope!r}")
        return HandlerResult.fail(
            f"Scope {scope!r} inconnu. Scopes valides : {sorted(VALID_SCOPES)}",
            handler_name="delegate_to_peer",
        )

    # 4. Timeout borné — jamais de délégation infinie
    safe_timeout = max(_MIN_TIMEOUT, min(_MAX_TIMEOUT, int(timeout_sec)))

    # 5. Peer dans le registre
    peers = _load_peers()
    peer = peers.get(instance_id)
    if peer is None:
        _audit("delegate_tool_refused", instance_id, task_id, scope, "refused",
               "Pair absent du registre")
        return HandlerResult.fail(
            f"Pair {instance_id!r} inconnu. "
            "Vérifiez la section Réseau Lumena ou le panneau réseau.",
            handler_name="delegate_to_peer",
        )

    peer_name = peer.get("instance_name") or instance_id[:12]

    # 6. Trust check (fail-closed : uniquement "trusted" exact)
    trust = peer.get("trust", "unknown")
    if trust == "blocked":
        _audit("delegate_tool_refused", instance_id, task_id, scope, "refused",
               "Pair bloqué")
        return HandlerResult.fail(
            f"{peer_name!r} est bloqué. Débloquez ce pair avant de déléguer.",
            handler_name="delegate_to_peer",
        )
    if trust != "trusted":
        _audit("delegate_tool_refused", instance_id, task_id, scope, "refused",
               f"trust={trust!r}")
        return HandlerResult.fail(
            f"{peer_name!r} n'est pas trusted (trust={trust!r}). "
            "Jumelez ce pair via le panneau réseau.",
            handler_name="delegate_to_peer",
        )

    # 7. Token sortant nécessaire pour appeler le pair
    outbound_token = peer.get("peer_token_outbound", "")
    if not outbound_token:
        _audit("delegate_tool_refused", instance_id, task_id, scope, "refused",
               "Token sortant absent")
        return HandlerResult.fail(
            f"Token sortant manquant pour {peer_name!r}. "
            "Rejumelez ce pair via le code de jumelage.",
            handler_name="delegate_to_peer",
        )

    # 8. Scope autorisé pour ce pair spécifique
    allowed = peer.get("allowed_scopes") or []
    if scope not in allowed:
        _audit("delegate_tool_refused", instance_id, task_id, scope, "refused",
               f"scope {scope!r} absent de allowed_scopes")
        return HandlerResult.fail(
            f"Scope {scope!r} non autorisé pour {peer_name!r}. "
            f"Scopes actifs : {sorted(allowed) if allowed else 'aucun'}. "
            f"Activez ce scope via PUT /api/peers/{instance_id}/scopes.",
            handler_name="delegate_to_peer",
        )

    # 9. Anti-SSRF : le host doit être RFC1918 strictement
    host = peer.get("host", "")
    port = peer.get("port", 8080)
    try:
        from src.runtime.peer_host_validation import validate_peer_host
        validate_peer_host(host)
    except ValueError as ssrf_err:
        _audit("delegate_tool_refused", instance_id, task_id, scope, "refused",
               f"SSRF: {ssrf_err}")
        return HandlerResult.fail(
            f"Host {host!r} refusé (anti-SSRF) : {ssrf_err}",
            handler_name="delegate_to_peer",
        )

    # 10. Vérification du contenu avant envoi.
    # Le prompt est envoyé en clair dans le payload Phase 8 — refuser s'il contient
    # un pattern secret (Bearer token, JWT, hex ≥ 32 chars) pour éviter toute fuite.
    from src.utils.paths import INSTANCE_ID as _OWN_ID

    try:
        from src.runtime.peer_messages import create_sanitized_peer_message, has_secret_pattern
    except ImportError as _imp_err:
        _audit("delegate_tool_refused", instance_id, task_id, scope, "refused",
               f"Import peer_messages: {_imp_err}")
        return HandlerResult.fail(
            "Erreur interne : module peer_messages indisponible.",
            handler_name="delegate_to_peer",
        )

    if has_secret_pattern(prompt):
        _audit("delegate_tool_refused", instance_id, task_id, scope, "refused",
               "Secret détecté dans le prompt")
        return HandlerResult.fail(
            "Délégation refusée : le prompt contient un pattern identifié comme secret "
            "(token, clé, hash). Retirez-le avant de déléguer.",
            handler_name="delegate_to_peer",
        )

    try:
        _env = create_sanitized_peer_message(
            type="chat_delegate",
            scope=scope,
            from_instance_id=_OWN_ID,
            to_instance_id=instance_id,
            payload={"prompt": prompt},
            ttl_seconds=safe_timeout,
        )
        _envelope_dict: dict = _env.to_dict()
    except ValueError as _san_err:
        _audit("delegate_tool_refused", instance_id, task_id, scope, "refused",
               f"Sanitization: {_san_err}")
        return HandlerResult.fail(
            "Délégation refusée : le contenu du message a échoué la vérification de sécurité.",
            handler_name="delegate_to_peer",
        )

    # Payload Phase 8 (compatibilité /api/peer/delegate) + enveloppe dans context.
    payload = {
        "task_id": task_id,
        "from_instance_id": _OWN_ID,
        "from_user_id": "local_agent",
        "actor_id": "lumena_agent",
        "scope": scope,
        "prompt": prompt,
        "context": {"peer_message": _envelope_dict},
    }

    url = f"http://{host}:{port}/api/peer/delegate"

    # 11. Audit démarrage
    _audit("delegate_tool_started", instance_id, task_id, scope, "running")

    # 12. Appel HTTP — outbound_token transmis via Authorization: Bearer (standard Phase 8)
    # verify_peer_token() côté pair attend "Bearer <token>" — ne pas changer ce schéma.
    try:
        import httpx as _httpx
        async with _httpx.AsyncClient(timeout=float(safe_timeout)) as client:
            r = await client.post(
                url,
                json=payload,
                headers={"Authorization": f"Bearer {outbound_token}"},
            )

        if r.status_code != 200:
            _audit("delegate_tool_failed", instance_id, task_id, scope, "error",
                   f"HTTP {r.status_code}")
            return HandlerResult.fail(
                f"{peer_name!r} a retourné HTTP {r.status_code}. "
                "Vérifiez que le pair est démarré et joignable.",
                handler_name="delegate_to_peer",
            )

        data = r.json()
        status_val = data.get("status", "unknown")
        response_text = data.get("response", "")

        if status_val == "error" or not response_text:
            _audit("delegate_tool_failed", instance_id, task_id, scope, "error",
                   f"status={status_val}")
            return HandlerResult.fail(
                f"Délégation vers {peer_name!r} terminée avec erreur "
                f"(status={status_val!r}). Réponse : {response_text or 'vide'}",
                handler_name="delegate_to_peer",
            )

        _audit("delegate_tool_completed", instance_id, task_id, scope, "completed")
        return HandlerResult.ok(
            f"Réponse de {peer_name} (scope={scope}) :\n{response_text}",
            handler_name="delegate_to_peer",
        )

    except Exception as exc:
        err = str(exc)
        _audit("delegate_tool_failed", instance_id, task_id, scope, "error", err)
        if "timeout" in err.lower() or "timed out" in err.lower():
            return HandlerResult.fail(
                f"{peer_name!r} n'a pas répondu dans {safe_timeout}s. "
                "Le pair est peut-être surchargé.",
                handler_name="delegate_to_peer",
            )
        # Ne jamais exposer l'URL complète (contiendrait host/port mais pas de token)
        return HandlerResult.fail(
            f"{peer_name!r} est injoignable ({host}:{port}). "
            "Vérifiez que le pair est démarré.",
            handler_name="delegate_to_peer",
        )


def get_peer_delegation_handler_defs() -> List:
    """Retourne les handlers de délégation inter-instances.

    Retourne une liste vide si LUMENA_PEER_COLLABORATION != "1",
    ce qui masque le tool du registre et du prompt LLM.
    """
    if not _is_collaboration_enabled():
        return []

    from .registry_v2 import HandlerDef

    return [
        HandlerDef(
            name="delegate_to_peer",
            description=(
                "Délègue une question ou une tâche à une autre instance Lumena trusted. "
                "Utilise l'instance_id visible dans la section Réseau Lumena du contexte. "
                "Retourne la réponse du pair pour intégration dans la réponse finale."
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
                    "prompt": {
                        "type": "string",
                        "description": "Question ou instruction à envoyer au pair Lumena.",
                    },
                    "scope": {
                        "type": "string",
                        "description": (
                            "Scope de la délégation (défaut: 'chat'). "
                            "Doit figurer dans les scopes autorisés du pair."
                        ),
                    },
                    "timeout_sec": {
                        "type": "integer",
                        "description": "Timeout en secondes (10-300, défaut: 120).",
                    },
                },
                "required": ["instance_id", "prompt"],
            },
            handler=delegate_to_peer_handler,
            category="peers",
            source_module="handlers.peer_delegation",
        ),
    ]
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
