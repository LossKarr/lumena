"""Phase 10 Lot F - peer orchestrator V1.

Small, deterministic orchestration layer:
- filter usable peers by trust/token/scope/capability
- rank by health/latency
- guard traces against local loops

No LLM comparison, no multi-answer synthesis in V1.
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

_TRACE_TTL_SECONDS = 3600
_seen_traces: Dict[str, float] = {}
_trace_lock = threading.Lock()


@dataclass(frozen=True)
class PeerCandidate:
    instance_id: str
    instance_name: str
    host: str
    port: int
    scope: str
    capabilities: tuple
    latency_ms: float
    score: float
    peer: dict


def _latency_ms(peer: dict) -> float:
    for key in ("last_latency_ms", "latency_ms", "avg_latency_ms"):
        value = peer.get(key)
        if isinstance(value, (int, float)) and value >= 0:
            return float(value)
    health = peer.get("health") if isinstance(peer.get("health"), dict) else {}
    value = health.get("latency_ms")
    if isinstance(value, (int, float)) and value >= 0:
        return float(value)
    return 9999.0


def _last_seen_penalty(peer: dict) -> float:
    if peer.get("last_seen"):
        return 0.0
    return 500.0


def _capabilities(peer: dict) -> tuple:
    caps = peer.get("capabilities") or []
    if not isinstance(caps, list):
        return ()
    return tuple(str(c) for c in caps)


def peer_supports_capability(peer: dict, capability: Optional[str]) -> bool:
    if not capability:
        return True
    return capability in _capabilities(peer)


def is_usable_peer(peer: dict, scope: str, capability: Optional[str] = None) -> bool:
    if not isinstance(peer, dict):
        return False
    if peer.get("trust") != "trusted":
        return False
    if not peer.get("peer_token_outbound"):
        return False
    if scope not in (peer.get("allowed_scopes") or []):
        return False
    if not peer_supports_capability(peer, capability):
        return False
    if not peer.get("host") or not peer.get("port"):
        return False
    return True


def build_peer_candidates(
    peers: Dict[str, dict],
    *,
    scope: str,
    capability: Optional[str] = None,
) -> List[PeerCandidate]:
    candidates: List[PeerCandidate] = []
    for instance_id, peer in peers.items():
        if not is_usable_peer(peer, scope, capability):
            continue
        latency = _latency_ms(peer)
        score = latency + _last_seen_penalty(peer)
        candidates.append(
            PeerCandidate(
                instance_id=str(peer.get("instance_id") or instance_id),
                instance_name=str(peer.get("instance_name") or instance_id[:12]),
                host=str(peer.get("host")),
                port=int(peer.get("port") or 8080),
                scope=scope,
                capabilities=_capabilities(peer),
                latency_ms=latency,
                score=score,
                peer=peer,
            )
        )
    candidates.sort(key=lambda c: (c.score, c.instance_name, c.instance_id))
    return candidates


def choose_best_peer(
    peers: Dict[str, dict],
    *,
    scope: str,
    capability: Optional[str] = None,
) -> Optional[PeerCandidate]:
    candidates = build_peer_candidates(peers, scope=scope, capability=capability)
    return candidates[0] if candidates else None


def _max_hops() -> int:
    try:
        return max(1, int(os.getenv("LUMENA_PEER_MAX_HOPS", "5")))
    except (TypeError, ValueError):
        return 5


def register_trace_or_raise(trace_id: str, hop_count: int = 0) -> None:
    """Fail closed for repeated traces and max-hop overflow."""
    if not trace_id or not trace_id.strip():
        raise ValueError("trace_id is required.")
    if hop_count >= _max_hops():
        raise ValueError("Max peer hop_count reached.")

    now = time.monotonic()
    with _trace_lock:
        expired = [tid for tid, ts in _seen_traces.items() if now - ts > _TRACE_TTL_SECONDS]
        for tid in expired:
            del _seen_traces[tid]
        if trace_id in _seen_traces:
            raise ValueError("Trace already seen; refusing orchestration loop.")
        _seen_traces[trace_id] = now


def reset_seen_traces() -> None:
    """Test helper."""
    with _trace_lock:
        _seen_traces.clear()


def candidate_summary(candidates: Iterable[PeerCandidate]) -> List[Dict[str, Any]]:
    return [
        {
            "instance_id": c.instance_id,
            "instance_name": c.instance_name,
            "host": c.host,
            "port": c.port,
            "scope": c.scope,
            "capabilities": list(c.capabilities),
            "latency_ms": c.latency_ms,
            "score": c.score,
        }
        for c in candidates
    ]
