"""Lot E1 Phase 10 — Tool run_peer_task_sync pour l'agent Lumena.

Permet à Lumena de déléguer une tâche bornée à une instance Lumena trusted
via POST /api/peer/tasks/run-sync, d'attendre un résultat, et de l'intégrer
dans sa réponse.

Activé via LUMENA_PEER_COLLABORATION=1.
Requiert scope task.delegate dans allowed_scopes du pair.
Anti-SSRF, sanitization objective, timeout obligatoire, aucun token en sortie.
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
_DEFAULT_TIMEOUT = 120


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


async def run_peer_task_sync_handler(
    ctx: Any,
    instance_id: str,
    objective: str,
    timeout_sec: int = _DEFAULT_TIMEOUT,
    expected_output: str = "summary",
) -> Any:
    """Délègue une tâche bornée à une instance Lumena trusted.

    Vérifie : feature flag, paramètres, secret dans objective, pair existant,
    trust=trusted, peer_token_outbound, scope task.delegate, anti-SSRF,
    sanitization. Appelle POST /api/peer/tasks/run-sync, attend le résultat.
    """
    from .contracts import HandlerResult

    task_id = f"ts-{uuid.uuid4().hex[:12]}"

    # 1. Feature flag
    if not _is_collaboration_enabled():
        return HandlerResult.fail(
            "Délégation de tâches désactivée. "
            "Activez LUMENA_PEER_COLLABORATION=1 pour utiliser ce tool.",
            handler_name="run_peer_task_sync",
        )

    # 2. Paramètres obligatoires
    if not instance_id or not instance_id.strip():
        return HandlerResult.fail(
            "instance_id est requis.",
            handler_name="run_peer_task_sync",
        )
    if not objective or not objective.strip():
        return HandlerResult.fail(
            "objective est requis.",
            handler_name="run_peer_task_sync",
        )

    instance_id = instance_id.strip()

    # 3. Secret dans l'objective
    from src.runtime.peer_messages import has_secret_pattern
    if has_secret_pattern(objective):
        _audit("task_sync_refused", instance_id, task_id,
               "task.delegate", "refused", "Secret détecté dans l'objective")
        return HandlerResult.fail(
            "Tâche refusée : l'objective contient un pattern identifié comme secret. "
            "Retirez-le avant de déléguer.",
            handler_name="run_peer_task_sync",
        )

    # 4. Timeout borné
    safe_timeout = max(_MIN_TIMEOUT, min(_MAX_TIMEOUT, int(timeout_sec)))

    # 5. Peer dans le registre
    peers = _load_peers()
    peer = peers.get(instance_id)
    if peer is None:
        _audit("task_sync_refused", instance_id, task_id,
               "task.delegate", "refused", "Pair absent du registre")
        return HandlerResult.fail(
            f"Pair {instance_id!r} inconnu. "
            "Vérifiez la section Réseau Lumena ou le panneau réseau.",
            handler_name="run_peer_task_sync",
        )

    peer_name = peer.get("instance_name") or instance_id[:12]

    # 6. Trust fail-closed
    trust = peer.get("trust", "unknown")
    if trust == "blocked":
        _audit("task_sync_refused", instance_id, task_id,
               "task.delegate", "refused", "Pair bloqué")
        return HandlerResult.fail(
            f"{peer_name!r} est bloqué. Débloquez ce pair avant de lui déléguer.",
            handler_name="run_peer_task_sync",
        )
    if trust != "trusted":
        _audit("task_sync_refused", instance_id, task_id,
               "task.delegate", "refused", f"trust={trust!r}")
        return HandlerResult.fail(
            f"{peer_name!r} n'est pas trusted (trust={trust!r}). "
            "Jumelez ce pair via le panneau réseau.",
            handler_name="run_peer_task_sync",
        )

    # 7. Token sortant
    outbound_token = peer.get("peer_token_outbound", "")
    if not outbound_token:
        _audit("task_sync_refused", instance_id, task_id,
               "task.delegate", "refused", "Token sortant absent")
        return HandlerResult.fail(
            f"Token sortant manquant pour {peer_name!r}. "
            "Rejumelez ce pair via le code de jumelage.",
            handler_name="run_peer_task_sync",
        )

    # 8. Scope task.delegate autorisé pour ce pair
    allowed = peer.get("allowed_scopes") or []
    if "task.delegate" not in allowed:
        _audit("task_sync_refused", instance_id, task_id,
               "task.delegate", "refused", "task.delegate absent de allowed_scopes")
        return HandlerResult.fail(
            f"Scope task.delegate non autorisé pour {peer_name!r}. "
            f"Scopes actifs : {sorted(allowed) if allowed else 'aucun'}. "
            f"Activez via PUT /api/peers/{instance_id}/scopes.",
            handler_name="run_peer_task_sync",
        )

    # 9. Anti-SSRF
    host = peer.get("host", "")
    port = peer.get("port", 8080)
    try:
        from src.runtime.peer_host_validation import validate_peer_host
        validate_peer_host(host)
    except ValueError as ssrf_err:
        _audit("task_sync_refused", instance_id, task_id,
               "task.delegate", "refused", f"SSRF: {ssrf_err}")
        return HandlerResult.fail(
            f"Host {host!r} refusé (anti-SSRF) : {ssrf_err}",
            handler_name="run_peer_task_sync",
        )

    # 10. Enveloppe sanitized
    from src.utils.paths import INSTANCE_ID as _OWN_ID

    try:
        from src.runtime.peer_messages import create_sanitized_peer_message
        _env = create_sanitized_peer_message(
            type="task_request",
            scope="task.delegate",
            from_instance_id=_OWN_ID,
            to_instance_id=instance_id,
            payload={"objective": objective, "expected_output": expected_output},
            ttl_seconds=safe_timeout,
        )
        _envelope_dict: dict = _env.to_dict()
    except ValueError as _san_err:
        _audit("task_sync_refused", instance_id, task_id,
               "task.delegate", "refused", f"Sanitization: {_san_err}")
        return HandlerResult.fail(
            "Tâche refusée : le contenu a échoué la vérification de sécurité.",
            handler_name="run_peer_task_sync",
        )

    payload = {
        "task_id": task_id,
        "from_instance_id": _OWN_ID,
        "from_user_id": "local:owner",
        "actor_id": "lumena_agent",
        "objective": objective,
        "context": {"peer_message": _envelope_dict},
        "allowed_tools": [],
        "timeout_sec": safe_timeout,
        "expected_output": expected_output,
    }

    url = f"http://{host}:{port}/api/peer/tasks/run-sync"

    # 11. Audit démarrage
    _audit("task_sync_started", instance_id, task_id,
           "task.delegate", "running")

    # 12. Appel HTTP
    try:
        import httpx as _httpx
        async with _httpx.AsyncClient(timeout=float(safe_timeout)) as client:
            r = await client.post(
                url,
                json=payload,
                headers={"Authorization": f"Bearer {outbound_token}"},
            )

        if r.status_code != 200:
            _audit("task_sync_failed", instance_id, task_id,
                   "task.delegate", "error", f"HTTP {r.status_code}")
            return HandlerResult.fail(
                f"{peer_name!r} a retourné HTTP {r.status_code}. "
                "Vérifiez que le pair est démarré et joignable.",
                handler_name="run_peer_task_sync",
            )

        data = r.json()
        status_val = data.get("status", "unknown")
        result = str(data.get("result", "") or "")
        duration_ms = data.get("duration_ms", 0)

        if status_val == "timeout":
            _audit("task_sync_timeout", instance_id, task_id,
                   "task.delegate", "timeout")
            return HandlerResult.fail(
                f"{peer_name!r} : tâche interrompue par timeout côté pair.",
                handler_name="run_peer_task_sync",
            )

        if status_val in ("failed", "refused") or not result:
            _audit("task_sync_failed", instance_id, task_id,
                   "task.delegate", "error", f"status={status_val}")
            return HandlerResult.fail(
                f"Tâche {status_val} côté {peer_name!r}. "
                f"Résultat : {result or 'vide'}",
                handler_name="run_peer_task_sync",
            )

        # Re-vérification du result distant avant injection dans le LLM local.
        # Même un pair trusted peut bugger et renvoyer un contenu problématique.
        _MAX_CALLER_RESULT = 4000  # plus conservateur côté appelant que côté receveur
        if len(result) > _MAX_CALLER_RESULT:
            result = result[:_MAX_CALLER_RESULT - 1] + "…"

        from src.runtime.peer_messages import redact_string
        result = redact_string(result)

        _audit("task_sync_completed", instance_id, task_id,
               "task.delegate", "completed",
               f"duration_ms={duration_ms}")

        return HandlerResult.ok(
            f"Résultat de {peer_name} (task.delegate, {duration_ms:.0f}ms) :\n{result}",
            handler_name="run_peer_task_sync",
        )

    except Exception as exc:
        err = str(exc)
        _audit("task_sync_failed", instance_id, task_id,
               "task.delegate", "error", err)
        if "timeout" in err.lower() or "timed out" in err.lower():
            return HandlerResult.fail(
                f"{peer_name!r} n'a pas répondu dans {safe_timeout}s.",
                handler_name="run_peer_task_sync",
            )
        return HandlerResult.fail(
            f"{peer_name!r} est injoignable ({host}:{port}). "
            "Vérifiez que le pair est démarré.",
            handler_name="run_peer_task_sync",
        )


async def submit_peer_task_handler(
    ctx: Any,
    instance_id: str,
    objective: str,
    timeout_sec: int = _DEFAULT_TIMEOUT,
    expected_output: str = "summary",
) -> Any:
    """Soumet une tâche async à une instance Lumena trusted et retourne le task_id immédiatement.

    N'attend pas le résultat — utiliser get_peer_task_status pour vérifier la complétion.
    """
    from .contracts import HandlerResult

    task_id = f"ta-{uuid.uuid4().hex[:12]}"

    # 1. Feature flag
    if not _is_collaboration_enabled():
        return HandlerResult.fail(
            "Délégation de tâches désactivée. "
            "Activez LUMENA_PEER_COLLABORATION=1 pour utiliser ce tool.",
            handler_name="submit_peer_task",
        )

    # 2. Paramètres obligatoires
    if not instance_id or not instance_id.strip():
        return HandlerResult.fail("instance_id est requis.", handler_name="submit_peer_task")
    if not objective or not objective.strip():
        return HandlerResult.fail("objective est requis.", handler_name="submit_peer_task")

    instance_id = instance_id.strip()

    # 3. Secret dans l'objective
    from src.runtime.peer_messages import has_secret_pattern
    if has_secret_pattern(objective):
        _audit("task_async_refused", instance_id, task_id, "task.delegate", "refused",
               "Secret détecté dans l'objective")
        return HandlerResult.fail(
            "Tâche refusée : l'objective contient un pattern identifié comme secret.",
            handler_name="submit_peer_task",
        )

    # 4. Timeout borné
    safe_timeout = max(_MIN_TIMEOUT, min(_MAX_TIMEOUT, int(timeout_sec)))

    # 5. Peer dans le registre
    peers = _load_peers()
    peer = peers.get(instance_id)
    if peer is None:
        _audit("task_async_refused", instance_id, task_id, "task.delegate", "refused",
               "Pair absent du registre")
        return HandlerResult.fail(
            f"Pair {instance_id!r} inconnu.", handler_name="submit_peer_task",
        )

    peer_name = peer.get("instance_name") or instance_id[:12]

    # 6. Trust fail-closed
    trust = peer.get("trust", "unknown")
    if trust == "blocked":
        _audit("task_async_refused", instance_id, task_id, "task.delegate", "refused", "Pair bloqué")
        return HandlerResult.fail(
            f"{peer_name!r} est bloqué.", handler_name="submit_peer_task",
        )
    if trust != "trusted":
        _audit("task_async_refused", instance_id, task_id, "task.delegate", "refused",
               f"trust={trust!r}")
        return HandlerResult.fail(
            f"{peer_name!r} n'est pas trusted (trust={trust!r}).", handler_name="submit_peer_task",
        )

    # 7. Token sortant
    outbound_token = peer.get("peer_token_outbound", "")
    if not outbound_token:
        _audit("task_async_refused", instance_id, task_id, "task.delegate", "refused",
               "Token sortant absent")
        return HandlerResult.fail(
            f"Token sortant manquant pour {peer_name!r}.", handler_name="submit_peer_task",
        )

    # 8. Scope task.delegate
    allowed = peer.get("allowed_scopes") or []
    if "task.delegate" not in allowed:
        _audit("task_async_refused", instance_id, task_id, "task.delegate", "refused",
               "task.delegate absent de allowed_scopes")
        return HandlerResult.fail(
            f"Scope task.delegate non autorisé pour {peer_name!r}.",
            handler_name="submit_peer_task",
        )

    # 9. Anti-SSRF
    host = peer.get("host", "")
    port = peer.get("port", 8080)
    try:
        from src.runtime.peer_host_validation import validate_peer_host
        validate_peer_host(host)
    except ValueError as ssrf_err:
        _audit("task_async_refused", instance_id, task_id, "task.delegate", "refused",
               f"SSRF: {ssrf_err}")
        return HandlerResult.fail(
            f"Host {host!r} refusé (anti-SSRF) : {ssrf_err}", handler_name="submit_peer_task",
        )

    # 10. Enveloppe sanitized
    from src.utils.paths import INSTANCE_ID as _OWN_ID
    try:
        from src.runtime.peer_messages import create_sanitized_peer_message
        _env = create_sanitized_peer_message(
            type="task_request",
            scope="task.delegate",
            from_instance_id=_OWN_ID,
            to_instance_id=instance_id,
            payload={"objective": objective, "expected_output": expected_output},
            ttl_seconds=safe_timeout,
        )
        _envelope_dict: dict = _env.to_dict()
    except ValueError as _san_err:
        _audit("task_async_refused", instance_id, task_id, "task.delegate", "refused",
               f"Sanitization: {_san_err}")
        return HandlerResult.fail(
            "Tâche refusée : le contenu a échoué la vérification de sécurité.",
            handler_name="submit_peer_task",
        )

    payload = {
        "task_id": task_id,
        "from_instance_id": _OWN_ID,
        "from_user_id": "local:owner",
        "actor_id": "lumena_agent",
        "objective": objective,
        "context": {"peer_message": _envelope_dict},
        "allowed_tools": [],
        "timeout_sec": safe_timeout,
        "expected_output": expected_output,
    }

    url = f"http://{host}:{port}/api/peer/tasks/submit"
    _audit("task_async_started", instance_id, task_id, "task.delegate", "running")

    # 11. POST submit (timeout court — on attend juste l'accusé de réception)
    try:
        import httpx as _httpx
        async with _httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(
                url, json=payload,
                headers={"Authorization": f"Bearer {outbound_token}"},
            )

        if r.status_code != 200:
            _audit("task_async_failed", instance_id, task_id, "task.delegate", "error",
                   f"HTTP {r.status_code}")
            return HandlerResult.fail(
                f"{peer_name!r} a retourné HTTP {r.status_code}.",
                handler_name="submit_peer_task",
            )

        data = r.json()
        queued_task_id = data.get("task_id", task_id)
        _audit("task_async_queued", instance_id, queued_task_id, "task.delegate", "queued")
        return HandlerResult.ok(
            f"Tâche soumise à {peer_name} (task_id={queued_task_id}, status=queued). "
            "Utilisez get_peer_task_status pour vérifier le résultat.",
            handler_name="submit_peer_task",
        )

    except Exception as exc:
        err = str(exc)
        _audit("task_async_failed", instance_id, task_id, "task.delegate", "error", err)
        return HandlerResult.fail(
            f"{peer_name!r} est injoignable ({host}:{port}).",
            handler_name="submit_peer_task",
        )


async def get_peer_task_status_handler(
    ctx: Any,
    instance_id: str,
    task_id: str,
) -> Any:
    """Interroge le statut d'une tâche async précédemment soumise à un pair Lumena."""
    from .contracts import HandlerResult

    _qid = f"ts-status-{uuid.uuid4().hex[:8]}"

    # 1. Feature flag
    if not _is_collaboration_enabled():
        return HandlerResult.fail(
            "Délégation de tâches désactivée.",
            handler_name="get_peer_task_status",
        )

    # 2. Paramètres
    if not instance_id or not instance_id.strip():
        return HandlerResult.fail("instance_id est requis.", handler_name="get_peer_task_status")
    if not task_id or not task_id.strip():
        return HandlerResult.fail("task_id est requis.", handler_name="get_peer_task_status")

    instance_id = instance_id.strip()
    task_id = task_id.strip()

    # 3. Peer dans le registre
    peers = _load_peers()
    peer = peers.get(instance_id)
    if peer is None:
        return HandlerResult.fail(
            f"Pair {instance_id!r} inconnu.", handler_name="get_peer_task_status",
        )

    peer_name = peer.get("instance_name") or instance_id[:12]

    # 4. Trust fail-closed
    trust = peer.get("trust", "unknown")
    if trust != "trusted":
        return HandlerResult.fail(
            f"{peer_name!r} n'est pas trusted (trust={trust!r}).",
            handler_name="get_peer_task_status",
        )

    # 5. Token sortant
    outbound_token = peer.get("peer_token_outbound", "")
    if not outbound_token:
        return HandlerResult.fail(
            f"Token sortant manquant pour {peer_name!r}.", handler_name="get_peer_task_status",
        )

    # 6. Anti-SSRF
    host = peer.get("host", "")
    port = peer.get("port", 8080)
    try:
        from src.runtime.peer_host_validation import validate_peer_host
        validate_peer_host(host)
    except ValueError as ssrf_err:
        return HandlerResult.fail(
            f"Host {host!r} refusé (anti-SSRF) : {ssrf_err}",
            handler_name="get_peer_task_status",
        )

    # 7. GET status
    url = f"http://{host}:{port}/api/peer/tasks/{task_id}/status"
    try:
        import httpx as _httpx
        async with _httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(
                url, headers={"Authorization": f"Bearer {outbound_token}"},
            )

        if r.status_code == 404:
            return HandlerResult.fail(
                f"Tâche {task_id!r} inconnue côté {peer_name!r}.",
                handler_name="get_peer_task_status",
            )
        if r.status_code != 200:
            return HandlerResult.fail(
                f"{peer_name!r} a retourné HTTP {r.status_code}.",
                handler_name="get_peer_task_status",
            )

        data = r.json()
        status_val = data.get("status", "unknown")
        result = data.get("result") or ""
        duration_ms = data.get("duration_ms")

        if result:
            from src.runtime.peer_messages import redact_string
            result = redact_string(result)
            # Limite conservatrice côté appelant avant injection dans le LLM local
            _MAX_STATUS_RESULT = 4000
            if len(result) > _MAX_STATUS_RESULT:
                result = result[:_MAX_STATUS_RESULT - 1] + "…"

        _duration_str = f" ({duration_ms:.0f}ms)" if duration_ms is not None else ""
        _result_str = f"\nRésultat : {result}" if result else ""
        return HandlerResult.ok(
            f"Tâche {task_id} chez {peer_name} — statut : {status_val}{_duration_str}.{_result_str}",
            handler_name="get_peer_task_status",
        )

    except Exception as exc:
        err = str(exc)
        return HandlerResult.fail(
            f"{peer_name!r} est injoignable ({host}:{port}) : {err}",
            handler_name="get_peer_task_status",
        )


def get_peer_tasks_handler_defs() -> List:
    """Retourne les handlers run_peer_task_sync, submit_peer_task, get_peer_task_status.

    Retourne [] si LUMENA_PEER_COLLABORATION != "1".
    """
    if not _is_collaboration_enabled():
        return []

    from .registry_v2 import HandlerDef

    return [
        HandlerDef(
            name="run_peer_task_sync",
            description=(
                "Délègue une tâche bornée à une autre instance Lumena trusted et attend "
                "le résultat. La tâche est exécutée côté pair avec un timeout strict. "
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
                    "objective": {
                        "type": "string",
                        "description": "Tâche à exécuter côté pair.",
                    },
                    "timeout_sec": {
                        "type": "integer",
                        "description": "Timeout en secondes (10-300, défaut: 120).",
                    },
                    "expected_output": {
                        "type": "string",
                        "description": (
                            "Format attendu : summary | text | json | code "
                            "(défaut: summary)."
                        ),
                    },
                },
                "required": ["instance_id", "objective"],
            },
            handler=run_peer_task_sync_handler,
            category="peers",
            source_module="handlers.peer_tasks",
        ),
        HandlerDef(
            name="submit_peer_task",
            description=(
                "Soumet une tâche à une instance Lumena trusted en mode asynchrone et retourne "
                "immédiatement un task_id. Le pair exécute la tâche en background. "
                "Utiliser get_peer_task_status pour vérifier le résultat."
            ),
            parameters={
                "properties": {
                    "instance_id": {
                        "type": "string",
                        "description": "instance_id de l'instance Lumena cible.",
                    },
                    "objective": {
                        "type": "string",
                        "description": "Tâche à exécuter côté pair.",
                    },
                    "timeout_sec": {
                        "type": "integer",
                        "description": "Timeout côté pair en secondes (10-300, défaut: 120).",
                    },
                    "expected_output": {
                        "type": "string",
                        "description": "Format attendu : summary | text | json | code (défaut: summary).",
                    },
                },
                "required": ["instance_id", "objective"],
            },
            handler=submit_peer_task_handler,
            category="peers",
            source_module="handlers.peer_tasks",
        ),
        HandlerDef(
            name="get_peer_task_status",
            description=(
                "Interroge le statut d'une tâche async soumise à un pair Lumena via submit_peer_task. "
                "Retourne le statut (queued/running/completed/failed/timeout/cancelled) et le résultat."
            ),
            parameters={
                "properties": {
                    "instance_id": {
                        "type": "string",
                        "description": "instance_id de l'instance Lumena cible.",
                    },
                    "task_id": {
                        "type": "string",
                        "description": "task_id retourné par submit_peer_task.",
                    },
                },
                "required": ["instance_id", "task_id"],
            },
            handler=get_peer_task_status_handler,
            category="peers",
            source_module="handlers.peer_tasks",
        ),
    ]
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
