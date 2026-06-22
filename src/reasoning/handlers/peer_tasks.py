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
    # Kill-switch SOFT : le halt veto toute NOUVELLE collaboration (in/out).
    # OR-fallback : le MAÎTRE (LUMENA_PEER_ENABLED) allume aussi la collaboration.
    try:
        from src.runtime.peer_network_autonomy import is_peer_halt_enabled, is_peer_master_enabled
        if is_peer_halt_enabled():
            return False
        if is_peer_master_enabled():
            return True
    except Exception:
        pass
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


def _q_anomaly(peer_id: str, kind: str) -> None:
    """C-1.b — compte un échec de SANTÉ du pair (injoignable/HTTP). Best-effort."""
    try:
        from src.runtime.peer_quarantine import record_anomaly
        record_anomaly(peer_id, kind)
    except Exception:
        pass


def _q_success(peer_id: str) -> None:
    """C-1.b — le pair a répondu (joignable) → reset le compteur d'échecs."""
    try:
        from src.runtime.peer_quarantine import record_success
        record_success(peer_id)
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
    from src.runtime.peer_awareness import resolve_peer_identifier
    _resolved_id = resolve_peer_identifier(peers, instance_id)
    if _resolved_id:
        instance_id = _resolved_id
    peer = peers.get(_resolved_id) if _resolved_id else None
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

    # 6b. Quarantaine (C-1.b) : un pair qui enchaîne les échecs est isolé. On REFUSE
    # une NOUVELLE délégation, sans jamais toucher aux missions DÉJÀ en cours.
    try:
        from src.runtime.peer_quarantine import is_quarantined
        if is_quarantined(instance_id):
            _audit("task_sync_refused", instance_id, task_id,
                   "task.delegate", "refused", "Pair en quarantaine")
            return HandlerResult.fail(
                f"{peer_name!r} est en quarantaine (trop d'échecs récents). "
                "Lève la quarantaine dans le panneau réseau pour réessayer.",
                handler_name="run_peer_task_sync",
            )
    except Exception:
        pass

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

    # 12. Appel HTTP (Bearer + signature de flotte A2 si pair fleet)
    try:
        import httpx as _httpx
        from src.runtime.peer_signing import build_signed_request
        _content, _headers = build_signed_request(
            payload, from_id=_OWN_ID, to_id=peer.get("instance_id", instance_id),
            peer_token=outbound_token, pairing_method=peer.get("pairing_method", ""),
        )
        async with _httpx.AsyncClient(timeout=float(safe_timeout)) as client:
            r = await client.post(url, content=_content, headers=_headers)

        if r.status_code != 200:
            _q_anomaly(instance_id, f"http_{r.status_code}")
            _audit("task_sync_failed", instance_id, task_id,
                   "task.delegate", "error", f"HTTP {r.status_code}")
            return HandlerResult.fail(
                f"{peer_name!r} a retourné HTTP {r.status_code}. "
                "Vérifiez que le pair est démarré et joignable.",
                handler_name="run_peer_task_sync",
            )

        data = r.json()
        _q_success(instance_id)  # pair joignable (200) → reset compteur d'échecs
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
        _q_anomaly(instance_id, "unreachable")  # injoignable/timeout réseau → santé KO
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


def _detect_origin_channel(ctx: Any) -> str:
    """Canal d'où vient la demande (telegram/web/…), pour router la notification.

    Lu depuis le runtime_ctx ; défaut `web` si indéterminable.
    """
    try:
        lum = getattr(ctx, "lumena", None)
        rt = getattr(ctx, "runtime_ctx", None) or (getattr(lum, "runtime_ctx", None) if lum else None)
        ch = (getattr(rt, "channel", None) or getattr(rt, "source_channel", None)) if rt else None
        return str(ch).strip().lower() if ch else "web"
    except Exception:
        return "web"


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
    from src.runtime.peer_awareness import resolve_peer_identifier
    _resolved_id = resolve_peer_identifier(peers, instance_id)
    if _resolved_id:
        instance_id = _resolved_id
    peer = peers.get(_resolved_id) if _resolved_id else None
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

    # 6b. Quarantaine (C-1.b) : un pair isolé ne reçoit pas de NOUVELLE mission async
    # (les missions en cours ne sont pas touchées). Couvre aussi « Relancer » (C2.2c).
    try:
        from src.runtime.peer_quarantine import is_quarantined
        if is_quarantined(instance_id):
            _audit("task_async_refused", instance_id, task_id, "task.delegate", "refused",
                   "Pair en quarantaine")
            return HandlerResult.fail(
                f"{peer_name!r} est en quarantaine (trop d'échecs récents). "
                "Lève la quarantaine dans le panneau réseau pour réessayer.",
                handler_name="submit_peer_task",
            )
    except Exception:
        pass

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
        from src.runtime.peer_signing import build_signed_request
        _content, _headers = build_signed_request(
            payload, from_id=_OWN_ID, to_id=peer.get("instance_id", instance_id),
            peer_token=outbound_token, pairing_method=peer.get("pairing_method", ""),
        )
        async with _httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(url, content=_content, headers=_headers)

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
        # M4 — enregistre la mission au tracker (suivi de fond + notification auto)
        # avec le canal d'origine, pour pouvoir prévenir l'utilisateur à la fin.
        try:
            from src.runtime.peer_mission_tracker import register_outbound_mission
            register_outbound_mission(
                task_id=queued_task_id,
                peer_id=peer.get("instance_id", instance_id),
                peer_name=peer_name, host=host, port=port,
                objective=objective, channel=_detect_origin_channel(ctx),
            )
        except Exception:
            pass
        return HandlerResult.ok(
            f"✅ Mission bien lancée chez {peer_name} (réf. {queued_task_id}). "
            "Ça va prendre un peu de temps — je te préviens dès que c'est terminé. "
            "Tu peux continuer à me parler en attendant.",
            handler_name="submit_peer_task",
        )

    except Exception as exc:
        err = str(exc)
        _audit("task_async_failed", instance_id, task_id, "task.delegate", "error", err)
        return HandlerResult.fail(
            f"{peer_name!r} est injoignable ({host}:{port}).",
            handler_name="submit_peer_task",
        )


def _local_reception_suffix(task_id: str) -> str:
    """Info de RÉCEPTION locale (tracker) : les fichiers sont-ils déjà rapatriés ?

    Rend le « alors ? » fiable : on ne se fie pas qu'au statut distant, on dit
    aussi où les livrables ont atterri dans le workspace (ou qu'ils arrivent).
    """
    try:
        from src.runtime import peer_mission_tracker as _tr
        m = _tr.get_mission(task_id)
        if not m:
            return ""
        n = int(m.get("artifacts_count") or 0)
        dest = m.get("artifacts_dir") or ""
        if n and dest:
            return f"\n📦 {n} fichier(s) déjà reçu(s) dans ton workspace : {dest}"
        if m.get("status") == "completed":
            return ("\n⏳ Terminée chez le pair ; les fichiers sont en cours de "
                    "rapatriement automatique (vérifie dans workspace/inbound/).")
    except Exception:
        pass
    return ""


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
    from src.runtime.peer_awareness import resolve_peer_identifier
    _resolved_id = resolve_peer_identifier(peers, instance_id)
    if _resolved_id:
        instance_id = _resolved_id
    peer = peers.get(_resolved_id) if _resolved_id else None
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

    # 7. GET status (Bearer + signature de flotte A2 sur body vide si pair fleet)
    url = f"http://{host}:{port}/api/peer/tasks/{task_id}/status"
    try:
        import httpx as _httpx
        from src.runtime.peer_signing import build_signed_request
        from src.utils.paths import INSTANCE_ID as _OWN_ID
        _content, _headers = build_signed_request(
            None, from_id=_OWN_ID, to_id=peer.get("instance_id", instance_id),
            peer_token=outbound_token, pairing_method=peer.get("pairing_method", ""),
        )
        async with _httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(url, headers=_headers)

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
            f"Tâche {task_id} chez {peer_name} — statut : {status_val}{_duration_str}."
            f"{_result_str}{_local_reception_suffix(task_id)}",
            handler_name="get_peer_task_status",
        )

    except Exception as exc:
        err = str(exc)
        # Pair injoignable : si on a DÉJÀ rapatrié les fichiers localement, c'est
        # une réussite, pas un échec — on répond avec l'info locale.
        _local = _local_reception_suffix(task_id)
        if "📦" in _local:
            return HandlerResult.ok(
                f"Tâche {task_id} — pair {peer_name!r} momentanément injoignable, "
                f"mais les livrables sont déjà là.{_local}",
                handler_name="get_peer_task_status",
            )
        return HandlerResult.fail(
            f"{peer_name!r} est injoignable ({host}:{port}) : {err}{_local}",
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
                "Confie une mission à une instance Lumena trusted en mode asynchrone et retourne "
                "IMMÉDIATEMENT (le pair exécute en arrière-plan, les fichiers produits reviennent "
                "tout seuls dans ton workspace). "
                "IMPORTANT : après l'appel, donne directement ta réponse FINAL "
                "(« mission lancée, je te préviens quand c'est fini ») — NE re-vérifie PAS le statut, "
                "n'appelle PAS get_peer_task_status en boucle, ne refais PAS le travail toi-même : "
                "tu seras notifié AUTOMATIQUEMENT à la fin."
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
                "(Rarement utile — la fin de mission est notifiée AUTOMATIQUEMENT, n'utilise PAS "
                "cet outil pour poller en boucle.) Interroge ponctuellement le statut d'une tâche "
                "async soumise via submit_peer_task. Retourne le statut "
                "(queued/running/completed/failed/timeout/cancelled) et le résultat."
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
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
