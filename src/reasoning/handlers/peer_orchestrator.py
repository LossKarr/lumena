"""Phase 10 Lot F - orchestrate_peer_request tool.

Chooses trusted peer(s) for a requested scope/capability, falls back when one
fails, and can produce a deterministic multi-peer synthesis. It intentionally
avoids LLM-on-LLM contradiction scoring for now.
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

_KNOWLEDGE_HINTS = (
    "mémoire", "memoire", "souvenir", "connaissance", "connaissances",
    "ce qu'il sait", "ce quelle sait", "ce qu'elle sait", "appris",
    "historique", "base de savoir", "savoir local",
)

_TASK_HINTS = (
    "recherche", "chercher", "cherche", "web", "internet", "google",
    "wikipedia", "wikipédia", "navigateur", "browser", "analyse",
    "analyser", "fichier", "document", "pdf", "code", "tâche", "tache",
    "travaille", "exécute", "execute", "lance",
)

_BROWSER_HINTS = (
    "web", "internet", "google", "wikipedia", "wikipédia", "navigateur",
    "browser", "site", "page",
)

_DOCUMENT_HINTS = (
    "document", "documents", "pdf", "docx", "xlsx", "fichier", "fichiers",
)

_TEAM_HINTS = (
    "ensemble", "equipe", "équipe", "toutes les lumena", "tous les pairs",
    "plusieurs lumena", "plusieurs instances", "repartis", "répartis",
    "split", "compare", "croise", "croisé", "synthese", "synthèse",
    "double avis", "avis des deux",
)


def infer_peer_team_routes(
    user_request: str,
    *,
    preferred_scope: str = "",
    capability: str = "",
) -> List[Dict[str, str]]:
    """Convertit une demande naturelle en routes peer ordonnées.

    V1 reste volontairement simple et déterministe :
    - mémoire/connaissances -> knowledge.query puis chat fallback ;
    - recherche/exécution/analyse -> task.delegate puis chat fallback ;
    - avis/conversation -> chat ;
    - preferred_scope force le premier choix mais garde un fallback chat sûr.
    """
    text = (user_request or "").lower()
    cap = (capability or "").strip()
    preferred = (preferred_scope or "").strip()

    if not cap:
        if any(h in text for h in _BROWSER_HINTS):
            cap = "browser"
        elif any(h in text for h in _DOCUMENT_HINTS):
            cap = "documents"

    routes: List[Dict[str, str]] = []

    def _add(scope: str, route_capability: str = "") -> None:
        item = {"scope": scope, "capability": route_capability}
        if item not in routes:
            routes.append(item)

    if preferred:
        _add(preferred, cap)
    elif any(h in text for h in _KNOWLEDGE_HINTS):
        _add("knowledge.query", cap)
    elif any(h in text for h in _TASK_HINTS):
        _add("task.delegate", cap)
    else:
        _add("chat", cap)

    # Fallback conversationnel : si le scope spécialisé n'est pas activé, le
    # pair peut quand même donner un avis via chat. On garde la capability pour
    # éviter d'envoyer une demande browser à un pair sans capacité browser.
    if routes[0]["scope"] != "chat":
        _add("chat", cap)
    return routes


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


def _message_type_for_scope(scope: str) -> str:
    if scope == "knowledge.query":
        return "knowledge_query"
    if scope == "task.delegate":
        return "task_request"
    return "chat_delegate"


def _endpoint_for_scope(scope: str, host: str, port: int) -> str:
    if scope == "knowledge.query":
        return f"http://{host}:{port}/api/peer/knowledge/query"
    if scope == "task.delegate":
        return f"http://{host}:{port}/api/peer/tasks/run-sync"
    return f"http://{host}:{port}/api/peer/delegate"


def _payload_for_scope(
    *,
    scope: str,
    task_id: str,
    own_id: str,
    prompt: str,
    timeout_sec: int,
    envelope: dict,
) -> dict:
    if scope == "knowledge.query":
        return {
            "query": prompt,
            "from_instance_id": own_id,
            "from_user_id": "local:owner",
            "actor_id": "lumena_agent",
            "max_results": 5,
            "max_summary_chars": 800,
            "context": {"peer_message": envelope},
        }
    if scope == "task.delegate":
        return {
            "task_id": task_id,
            "from_instance_id": own_id,
            "from_user_id": "local:owner",
            "actor_id": "lumena_agent",
            "objective": prompt,
            "context": {"peer_message": envelope},
            "allowed_tools": [],
            "timeout_sec": timeout_sec,
            "expected_output": "summary",
        }
    return {
        "task_id": task_id,
        "from_instance_id": own_id,
        "from_user_id": "local_agent",
        "actor_id": "lumena_agent",
        "scope": scope,
        "prompt": prompt,
        "context": {"peer_message": envelope},
    }


def _envelope_payload_for_scope(scope: str, prompt: str) -> dict:
    if scope == "knowledge.query":
        return {"query": prompt}
    if scope == "task.delegate":
        return {"objective": prompt}
    return {"prompt": prompt}


def _extract_response(scope: str, data: dict) -> str:
    if scope == "knowledge.query":
        return str(data.get("answer_summary") or "")
    if scope == "task.delegate":
        return str(data.get("result") or "")
    return str(data.get("response") or "")


def _wants_team_synthesis(user_request: str) -> bool:
    text = (user_request or "").lower()
    return any(h in text for h in _TEAM_HINTS)


def _clamp_max_peers(max_peers: Any) -> int:
    try:
        return max(1, min(5, int(max_peers or 3)))
    except (TypeError, ValueError):
        return 3


def _synthesize_peer_outputs(*, scope: str, capability: str, results: List[Dict[str, str]]) -> str:
    """Deterministic synthesis: no local LLM call, no hidden extra delegation."""
    lines = [
        "Synthese equipe Lumena",
        f"scope={scope}, capability={capability or '*'}, reponses={len(results)}",
        "",
    ]
    for idx, item in enumerate(results, start=1):
        response = item["response"].strip()
        if len(response) > 4000:
            response = response[:4000].rstrip() + "\n...[tronque]"
        lines.extend([
            f"[{idx}] {item['peer_name']} ({item['instance_id']})",
            response or "(reponse vide)",
            "",
        ])
    lines.append("Conclusion: les reponses ci-dessus sont separees par pair pour garder la tracabilite.")
    return "\n".join(lines).strip()


async def orchestrate_peer_request_handler(
    ctx: Any,
    prompt: str,
    scope: str = "chat",
    capability: str = "",
    strategy: str = "fallback_on_failure",
    timeout_sec: int = 120,
    max_peers: int = 3,
    trace_id: str = "",
    hop_count: int = 0,
) -> Any:
    """Choose peer(s) automatically, call them, and fallback or synthesize."""
    from .contracts import HandlerResult
    from src.runtime.peer_scopes import VALID_SCOPES
    from src.runtime.peer_orchestrator import (
        build_peer_candidates,
        candidate_summary,
        register_trace_or_raise,
    )

    task_id = f"orch-{uuid.uuid4().hex[:12]}"

    if not _is_collaboration_enabled():
        return HandlerResult.fail(
            "Orchestration inter-instances désactivée. Activez LUMENA_PEER_COLLABORATION=1.",
            handler_name="orchestrate_peer_request",
        )
    if not prompt or not prompt.strip():
        return HandlerResult.fail("prompt est requis.", handler_name="orchestrate_peer_request")
    if scope not in VALID_SCOPES:
        return HandlerResult.fail(
            f"Scope {scope!r} inconnu. Scopes valides : {sorted(VALID_SCOPES)}",
            handler_name="orchestrate_peer_request",
        )
    if scope not in {"chat", "knowledge.query", "task.delegate"}:
        return HandlerResult.fail(
            "Lot F V1 supporte uniquement les scopes chat, knowledge.query et task.delegate.",
            handler_name="orchestrate_peer_request",
        )
    if strategy not in {"single_best", "fallback_on_failure", "multi_best"}:
        return HandlerResult.fail(
            "strategy doit etre 'single_best', 'fallback_on_failure' ou 'multi_best'.",
            handler_name="orchestrate_peer_request",
        )

    safe_timeout = max(_MIN_TIMEOUT, min(_MAX_TIMEOUT, int(timeout_sec)))
    safe_max_peers = _clamp_max_peers(max_peers)
    trace = trace_id.strip() or uuid.uuid4().hex
    try:
        register_trace_or_raise(trace, int(hop_count))
    except ValueError as exc:
        _audit("peer_orchestration_refused", "orchestrator", task_id, scope, "refused", str(exc))
        return HandlerResult.fail(str(exc), handler_name="orchestrate_peer_request")

    from src.runtime.peer_messages import create_sanitized_peer_message, has_secret_pattern, redact_string
    if has_secret_pattern(prompt):
        _audit("peer_orchestration_refused", "orchestrator", task_id, scope, "refused", "secret")
        return HandlerResult.fail(
            "Orchestration refusée : le prompt contient un pattern identifié comme secret.",
            handler_name="orchestrate_peer_request",
        )

    peers = _load_peers()
    candidates = build_peer_candidates(
        peers,
        scope=scope,
        capability=capability.strip() or None,
    )
    if not candidates:
        return HandlerResult.fail(
            f"Aucun pair utilisable pour scope={scope!r}, capability={capability or '*'}."
            " Vérifiez trust, token, allowed_scopes et capabilities.",
            handler_name="orchestrate_peer_request",
        )

    if strategy == "single_best":
        candidates = candidates[:1]
    elif strategy == "multi_best":
        candidates = candidates[:safe_max_peers]

    from src.utils.paths import INSTANCE_ID as _OWN_ID
    from src.runtime.peer_host_validation import validate_peer_host

    failures: List[str] = []
    attempts = candidate_summary(candidates)
    successes: List[Dict[str, str]] = []

    for candidate in candidates:
        peer = candidate.peer
        peer_name = candidate.instance_name
        outbound_token = peer.get("peer_token_outbound", "")
        try:
            validate_peer_host(candidate.host)
            msg = create_sanitized_peer_message(
                type=_message_type_for_scope(scope),
                scope=scope,
                from_instance_id=_OWN_ID,
                to_instance_id=candidate.instance_id,
                payload=_envelope_payload_for_scope(scope, prompt),
                trace_id=trace,
                ttl_seconds=safe_timeout,
            )
            envelope = msg.to_dict()
            envelope["hop_count"] = int(hop_count) + 1
            payload = _payload_for_scope(
                scope=scope,
                task_id=task_id,
                own_id=_OWN_ID,
                prompt=prompt,
                timeout_sec=safe_timeout,
                envelope=envelope,
            )
            url = _endpoint_for_scope(scope, candidate.host, candidate.port)

            _audit("peer_orchestration_started", candidate.instance_id, task_id, scope, "running")

            import httpx as _httpx
            async with _httpx.AsyncClient(timeout=float(safe_timeout)) as client:
                r = await client.post(
                    url,
                    json=payload,
                    headers={"Authorization": f"Bearer {outbound_token}"},
                )
            if r.status_code != 200:
                failures.append(f"{peer_name}: HTTP {r.status_code}")
                _audit("peer_orchestration_failed", candidate.instance_id, task_id, scope, "error",
                       f"HTTP {r.status_code}")
                continue
            data = r.json()
            response = redact_string(_extract_response(scope, data))
            if not response.strip():
                failures.append(f"{peer_name}: empty response")
                _audit("peer_orchestration_failed", candidate.instance_id, task_id, scope, "error",
                       "empty response")
                continue
            _audit("peer_orchestration_completed", candidate.instance_id, task_id, scope, "completed")
            if strategy == "multi_best":
                successes.append({
                    "peer_name": peer_name,
                    "instance_id": candidate.instance_id,
                    "response": response,
                })
                continue
            return HandlerResult.ok(
                (
                    f"Pair choisi: {peer_name} ({candidate.instance_id}) "
                    f"scope={scope}, capability={capability or '*'}, strategy={strategy}.\n"
                    f"Réponse:\n{response}"
                ),
                handler_name="orchestrate_peer_request",
            )
        except Exception as exc:
            failures.append(f"{peer_name}: {type(exc).__name__}")
            _audit("peer_orchestration_failed", candidate.instance_id, task_id, scope, "error", str(exc))
            continue

    if strategy == "multi_best" and successes:
        return HandlerResult.ok(
            (
                f"Pairs consultes: {len(successes)}/{len(candidates)} "
                f"scope={scope}, capability={capability or '*'}.\n"
                f"{_synthesize_peer_outputs(scope=scope, capability=capability, results=successes)}"
                + (f"\n\nEchecs: {failures}" if failures else "")
            ),
            handler_name="orchestrate_peer_request",
        )

    return HandlerResult.fail(
        (
            "Aucun pair n'a répondu correctement. "
            f"Tentatives: {attempts}. Echecs: {failures}"
        ),
        handler_name="orchestrate_peer_request",
    )


async def peer_team_request_handler(
    ctx: Any,
    user_request: str,
    preferred_scope: str = "",
    capability: str = "",
    timeout_sec: int = 120,
    max_peers: int = 3,
) -> Any:
    """Entrée conversationnelle naturelle pour collaborer avec une autre Lumena."""
    from .contracts import HandlerResult
    from src.runtime.peer_scopes import VALID_SCOPES

    if not _is_collaboration_enabled():
        return HandlerResult.fail(
            "Collaboration inter-instances désactivée. Activez LUMENA_PEER_COLLABORATION=1.",
            handler_name="peer_team_request",
        )
    if not user_request or not user_request.strip():
        return HandlerResult.fail("user_request est requis.", handler_name="peer_team_request")
    if preferred_scope and preferred_scope not in VALID_SCOPES:
        return HandlerResult.fail(
            f"preferred_scope {preferred_scope!r} inconnu. Scopes valides : {sorted(VALID_SCOPES)}",
            handler_name="peer_team_request",
        )

    routes = infer_peer_team_routes(
        user_request,
        preferred_scope=preferred_scope,
        capability=capability,
    )
    failures: List[str] = []
    team_strategy = "multi_best" if _wants_team_synthesis(user_request) else "fallback_on_failure"

    for route in routes:
        scope = route["scope"]
        cap = route["capability"]
        result = await orchestrate_peer_request_handler(
            ctx,
            prompt=user_request,
            scope=scope,
            capability=cap,
            strategy=team_strategy,
            timeout_sec=timeout_sec,
            max_peers=max_peers,
        )
        if getattr(result, "success", False):
            return HandlerResult.ok(
                (
                    f"Mode équipe Lumena: route={scope}"
                    f"{f', capability={cap}' if cap else ''}, strategy={team_strategy}.\n"
                    f"{result.output}"
                ),
                handler_name="peer_team_request",
            )
        failures.append(f"{scope}{f'/{cap}' if cap else ''}: {getattr(result, 'output', result)}")

    return HandlerResult.fail(
        (
            "Aucune instance Lumena utilisable n'a pu traiter cette demande. "
            f"Routes tentées: {', '.join(r['scope'] + (('/' + r['capability']) if r['capability'] else '') for r in routes)}. "
            f"Détails: {failures}"
        ),
        handler_name="peer_team_request",
    )


def get_peer_orchestrator_handler_defs() -> List:
    if not _is_collaboration_enabled():
        return []

    from .registry_v2 import HandlerDef

    return [
        HandlerDef(
            name="peer_team_request",
            description=(
                "Entrée principale pour les demandes naturelles de collaboration entre Lumena. "
                "À utiliser quand l'utilisateur dit 'demande à l'autre Lumena', 'demande lui', "
                "'fais vérifier par le salon', 'répartis', ou demande une tâche à une autre "
                "instance. Le tool choisit automatiquement chat, knowledge.query ou "
                "task.delegate, la capacité utile (browser/documents), le meilleur pair, et "
                "fallback proprement."
            ),
            parameters={
                "properties": {
                    "user_request": {
                        "type": "string",
                        "description": "Demande utilisateur complète à transmettre au réseau Lumena.",
                    },
                    "preferred_scope": {
                        "type": "string",
                        "description": "Optionnel: chat | knowledge.query | task.delegate.",
                    },
                    "capability": {
                        "type": "string",
                        "description": "Optionnel: browser | documents | voice | autre capacité peer.",
                    },
                    "timeout_sec": {
                        "type": "integer",
                        "description": "Timeout en secondes (10-300, défaut: 120).",
                    },
                    "max_peers": {
                        "type": "integer",
                        "description": "Nombre max de pairs a consulter en mode equipe (1-5, defaut: 3).",
                    },
                },
                "required": ["user_request"],
            },
            handler=peer_team_request_handler,
            category="peers",
            source_module="handlers.peer_orchestrator",
        ),
        HandlerDef(
            name="orchestrate_peer_request",
            description=(
                "Choisit automatiquement le meilleur pair Lumena trusted pour un scope/capability, "
                "puis délègue avec fallback si le premier pair échoue. V1 ne fait pas de synthèse "
                "multi-réponses. À utiliser en priorité quand l'utilisateur dit: demande à l'autre "
                "Lumena, fais vérifier par le salon, répartis la tâche, interroge une autre instance."
            ),
            parameters={
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "Question, recherche ou tâche à envoyer au pair choisi.",
                    },
                    "scope": {
                        "type": "string",
                        "description": "chat | knowledge.query | task.delegate (défaut: chat).",
                    },
                    "capability": {
                        "type": "string",
                        "description": "Capacité requise optionnelle, ex: browser, documents, voice.",
                    },
                    "strategy": {
                        "type": "string",
                        "description": "single_best, fallback_on_failure (defaut) ou multi_best.",
                    },
                    "timeout_sec": {
                        "type": "integer",
                        "description": "Timeout en secondes (10-300, défaut: 120).",
                    },
                    "max_peers": {
                        "type": "integer",
                        "description": "Nombre max de pairs a consulter avec strategy=multi_best (1-5, defaut: 3).",
                    },
                    "trace_id": {
                        "type": "string",
                        "description": "Trace réseau optionnelle pour anti-boucle.",
                    },
                    "hop_count": {
                        "type": "integer",
                        "description": "Hop count reçu si orchestration en chaîne.",
                    },
                },
                "required": ["prompt"],
            },
            handler=orchestrate_peer_request_handler,
            category="peers",
            source_module="handlers.peer_orchestrator",
        ),
    ]
