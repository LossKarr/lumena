"""Phase 4 — API locale d'instance Lumena.

Expose les routes permettant à une instance Lumena de :
- se présenter aux autres instances (hello, capabilities, health)
- gérer une liste de pairs (list, pair, block)

Routes publiques (pas d'auth) : /api/instance/hello, /api/instance/capabilities,
                                 /api/instance/health
Routes protégées (admin token) : /api/peers, /api/peers/pair, /api/peers/block
"""
from __future__ import annotations

import asyncio
import json
import os
import platform
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from web.routes import deps

router = APIRouter()

# ── Peer registry ─────────────────────────────────────────────────────────────

from src.utils.paths import DATA_DIR
from src.runtime.peer_rate_limit import check_max_parallel_tasks, check_rate_limit

_PEER_REGISTRY_FILE = DATA_DIR / "peer_registry.json"
_PEER_LOCK = threading.Lock()

TRUST_LEVELS = {"unknown", "trusted", "blocked"}


def _load_peers() -> Dict[str, dict]:
    try:
        if _PEER_REGISTRY_FILE.exists():
            return json.loads(_PEER_REGISTRY_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_peers(data: Dict[str, dict]) -> None:
    tmp = _PEER_REGISTRY_FILE.with_suffix(".tmp")
    try:
        _PEER_REGISTRY_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(_PEER_REGISTRY_FILE)
    except Exception:
        pass


def _raise_peer_rate_limited(
    *,
    peer_id: str,
    scope: str,
    task_id: str,
    retry_after: int,
    detail: str,
) -> None:
    """Audit and reject a peer request with HTTP 429."""
    from src.runtime.peer_protocol import write_audit_log

    write_audit_log(
        event="peer_rate_limited",
        from_instance_id=peer_id,
        task_id=task_id,
        scope=scope,
        status="rate_limited",
        detail=detail,
    )
    raise HTTPException(
        status_code=429,
        detail=detail,
        headers={"Retry-After": str(max(1, int(retry_after)))},
    )


def _enforce_peer_rate_limit(peer_id: str, scope: str, task_id: str) -> None:
    allowed, retry_after = check_rate_limit(peer_id, scope)
    if not allowed:
        _raise_peer_rate_limited(
            peer_id=peer_id,
            scope=scope,
            task_id=task_id,
            retry_after=retry_after,
            detail=f"Rate limit exceeded for scope {scope}.",
        )


# ── Capabilities helper ───────────────────────────────────────────────────────

def _compute_capabilities() -> List[str]:
    """Retourne la liste des capacités déclarées de cette instance."""
    caps = ["chat"]
    try:
        import src.tools.playwright_browser  # noqa: F401
        caps.append("browser")
    except Exception:
        pass
    try:
        import src.tools.code_runner  # noqa: F401
        caps.append("code")
    except Exception:
        pass
    try:
        import src.tools.vision  # noqa: F401
        caps.append("vision")
    except Exception:
        pass
    try:
        from src.utils.paths import RECEIVED_DOCS_DIR
        if RECEIVED_DOCS_DIR.exists():
            caps.append("documents")
    except Exception:
        pass
    try:
        from src.voice.manager import VoiceManager  # noqa: F401
        caps.append("voice")
    except Exception:
        pass
    extra = os.getenv("LUMENA_EXTRA_CAPABILITIES", "").strip()
    if extra:
        for cap in extra.split(","):
            cap = cap.strip()
            if cap and cap not in caps:
                caps.append(cap)
    return caps


# ── Schemas ───────────────────────────────────────────────────────────────────

class PairRequest(BaseModel):
    instance_id: str
    instance_name: str
    host: str
    port: int
    version: str = ""
    role: str = "standalone"
    capabilities: List[str] = []


class BlockRequest(BaseModel):
    instance_id: str


class LocalInstanceDeleteRequest(BaseModel):
    force: bool = False


# ── Routes publiques ──────────────────────────────────────────────────────────

@router.get("/api/instance/hello")
async def instance_hello() -> Dict[str, Any]:
    """Présentation publique de cette instance.

    Ne retourne aucun secret (token admin, clés API, chemins internes).
    """
    from src.utils.paths import INSTANCE_ID, INSTANCE_NAME, INSTANCE_ROLE
    from src import __version__

    admin_token_set = bool(os.getenv("LUMENA_ADMIN_TOKEN", "").strip())
    return {
        "instance_id": INSTANCE_ID,
        "instance_name": INSTANCE_NAME,
        "version": __version__,
        "role": INSTANCE_ROLE,
        "capabilities": _compute_capabilities(),
        "requires_pairing": admin_token_set,
    }


@router.get("/api/instance/capabilities")
async def instance_capabilities() -> Dict[str, Any]:
    """Liste des capacités déclarées de cette instance."""
    return {"capabilities": _compute_capabilities()}


@router.get("/api/instance/health")
async def instance_health() -> Dict[str, Any]:
    """Santé minimale de l'instance — utilisé par les pairs pour vérifier la disponibilité."""
    from src.utils.paths import INSTANCE_ID
    initialized = deps.lumena is not None and getattr(deps.lumena, "is_initialized", False)
    return {
        "ok": True,
        "instance_id": INSTANCE_ID,
        "initialized": initialized,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ── Routes protégées ──────────────────────────────────────────────────────────

def _sanitize_peer(entry: dict) -> dict:
    """Retire les tokens bruts et ajoute has_peer_token pour l'UI."""
    safe = {k: v for k, v in entry.items() if k not in ("peer_token_outbound", "peer_token_hash")}
    safe["has_peer_token"] = bool(entry.get("peer_token_hash"))
    safe.setdefault("capability_level", "chat")  # A3 brique 2 : défaut explicite pour l'UI
    safe.setdefault("alias", "")  # Bloc A : nom personnalisé du pair (vide = nom d'origine)
    return safe


def _parse_duration_ms(detail: str) -> Optional[float]:
    """Extract duration_ms=123.4 from audit/event details."""
    if not detail:
        return None
    marker = "duration_ms="
    if marker not in detail:
        return None
    raw = detail.split(marker, 1)[1].split()[0].strip(" ,;")
    try:
        return float(raw)
    except ValueError:
        return None


def _classify_peer_issue(event: dict) -> Optional[str]:
    """Convert low-level audit entries to user-readable issue labels."""
    detail = str(event.get("detail") or "").lower()
    status = str(event.get("status") or "").lower()
    name = str(event.get("event") or "").lower()
    if "rate_limited" in status or "rate_limited" in name:
        return "Limite de requêtes atteinte"
    if "scope" in detail or "not allowed" in detail or "non autorisé" in detail:
        return "Scope non autorisé"
    if "token" in detail:
        return "Token révoqué ou invalide"
    if "blocked" in detail or "bloquée" in detail:
        return "Pair bloqué"
    if "timeout" in detail or status == "timeout":
        return "Timeout"
    if "ssrf" in detail or "host" in detail and "refus" in detail:
        return "Adresse réseau refusée"
    if status in {"error", "failed", "refused"}:
        return "Instance distante indisponible ou refusée"
    return None


def _trim_jsonl_file(path: Path, keep_last: int, dry_run: bool = True) -> Dict[str, Any]:
    """Keep only the last N JSONL lines. Returns counts for maintenance UI."""
    keep = max(0, int(keep_last))
    try:
        if not path.exists():
            return {"path": str(path), "existing": 0, "kept": 0, "removed": 0, "dry_run": dry_run}
        lines = path.read_text(encoding="utf-8").splitlines()
        kept = lines[-keep:] if keep else []
        removed = max(0, len(lines) - len(kept))
        if not dry_run and removed:
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(("\n".join(kept) + ("\n" if kept else "")), encoding="utf-8")
            tmp.replace(path)
        return {"path": str(path), "existing": len(lines), "kept": len(kept), "removed": removed, "dry_run": dry_run}
    except Exception as exc:
        return {"path": str(path), "error": type(exc).__name__, "dry_run": dry_run}


def _trim_task_events_file(
    path: Path,
    keep_last: int,
    *,
    dry_run: bool = True,
    drop_terminal: bool = False,
) -> Dict[str, Any]:
    """Trim task events and optionally remove finished task histories."""
    terminal = {"completed", "failed", "timeout", "cancelled", "interrupted"}
    keep = max(0, int(keep_last))
    try:
        if not path.exists():
            return {
                "path": str(path),
                "existing": 0,
                "kept": 0,
                "removed": 0,
                "dry_run": dry_run,
                "drop_terminal": drop_terminal,
            }
        lines = path.read_text(encoding="utf-8").splitlines()
        filtered = list(lines)
        if drop_terminal:
            latest: Dict[str, str] = {}
            parsed: List[tuple[str, Optional[dict]]] = []
            for line in lines:
                event = None
                try:
                    event = json.loads(line)
                    tid = str(event.get("task_id") or "")
                    if tid:
                        latest[tid] = str(event.get("status") or "")
                except Exception:
                    event = None
                parsed.append((line, event))
            filtered = [
                line for line, event in parsed
                if not event
                or not event.get("task_id")
                or latest.get(str(event.get("task_id"))) not in terminal
            ]
        kept = filtered[-keep:] if keep else []
        removed = max(0, len(lines) - len(kept))
        if not dry_run and removed:
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(("\n".join(kept) + ("\n" if kept else "")), encoding="utf-8")
            tmp.replace(path)
        return {
            "path": str(path),
            "existing": len(lines),
            "kept": len(kept),
            "removed": removed,
            "dry_run": dry_run,
            "drop_terminal": drop_terminal,
        }
    except Exception as exc:
        return {
            "path": str(path),
            "error": type(exc).__name__,
            "dry_run": dry_run,
            "drop_terminal": drop_terminal,
        }


@router.get("/api/peers", dependencies=[Depends(deps.verify_admin_token)])
async def list_peers() -> Dict[str, Any]:
    """Retourne la liste de tous les pairs connus (trusted, unknown, blocked).

    Les tokens bruts (peer_token_outbound) ne sont pas exposés.
    has_peer_token indique si un peer token est configuré pour ce pair.
    """
    with _PEER_LOCK:
        data = _load_peers()
    return {"peers": [_sanitize_peer(p) for p in data.values()], "count": len(data)}


@router.post("/api/peers/pair", dependencies=[Depends(deps.verify_admin_token)])
async def pair_peer(req: PairRequest) -> Dict[str, Any]:
    """Enregistre ou met à jour un pair avec le statut 'trusted'."""
    if not req.instance_id or not req.host:
        raise HTTPException(status_code=422, detail="instance_id et host sont requis")
    with _PEER_LOCK:
        data = _load_peers()
        existing = data.get(req.instance_id, {})
        # Ne pas dé-bloquer un pair bloqué via pair — il faut unblock explicite
        trust = existing.get("trust", "unknown")
        if trust != "blocked":
            trust = "trusted"
        data[req.instance_id] = {
            "instance_id": req.instance_id,
            "instance_name": req.instance_name,
            "host": req.host,
            "port": req.port,
            "version": req.version,
            "role": req.role,
            "capabilities": req.capabilities,
            "trust": trust,
            "paired_at": existing.get("paired_at") or datetime.now(timezone.utc).isoformat(),
            "last_seen": datetime.now(timezone.utc).isoformat(),
            "allowed_scopes": existing.get("allowed_scopes") or ["chat"],
        }
        _save_peers(data)
    return {"ok": True, "instance_id": req.instance_id, "trust": trust}


@router.post("/api/peers/block", dependencies=[Depends(deps.verify_admin_token)])
async def block_peer(req: BlockRequest) -> Dict[str, Any]:
    """Bloque un pair — toutes les délégations entrantes de ce pair seront refusées."""
    if not req.instance_id:
        raise HTTPException(status_code=422, detail="instance_id est requis")
    with _PEER_LOCK:
        data = _load_peers()
        if req.instance_id not in data:
            raise HTTPException(status_code=404, detail="Pair inconnu")
        data[req.instance_id]["trust"] = "blocked"
        data[req.instance_id]["last_seen"] = datetime.now(timezone.utc).isoformat()
        _save_peers(data)
    return {"ok": True, "instance_id": req.instance_id, "trust": "blocked"}


@router.delete("/api/peers/{instance_id}", dependencies=[Depends(deps.verify_admin_token)])
async def delete_peer(instance_id: str) -> Dict[str, Any]:
    """Supprime completement un pair du registre local.

    Contrairement a block/revoke, cette route efface l'entree UI et les tokens
    associes. Elle ne contacte pas l'instance distante.
    """
    if not instance_id:
        raise HTTPException(status_code=422, detail="instance_id est requis")
    with _PEER_LOCK:
        data = _load_peers()
        if instance_id not in data:
            raise HTTPException(status_code=404, detail=f"Pair {instance_id!r} inconnu")
        data.pop(instance_id, None)
        _save_peers(data)
    try:
        from src.runtime.peer_protocol import write_audit_log
        write_audit_log(
            event="peer_deleted",
            from_instance_id=instance_id,
            task_id="",
            scope="admin",
            status="deleted",
            detail="Peer removed from local registry by admin UI.",
        )
    except Exception:
        pass
    return {"ok": True, "instance_id": instance_id, "action": "deleted"}


# ── Phase 5 — Découverte LAN ──────────────────────────────────────────────────

_PRIVATE_PREFIXES = ("10.", "172.16.", "172.17.", "172.18.", "172.19.",
                     "172.20.", "172.21.", "172.22.", "172.23.", "172.24.",
                     "172.25.", "172.26.", "172.27.", "172.28.", "172.29.",
                     "172.30.", "172.31.", "192.168.", "127.")
_MAX_SCAN_PORTS = 20
_TIMEOUT_MIN, _TIMEOUT_MAX = 0.1, 5.0


class ProbeRequest(BaseModel):
    host: str
    port: int
    timeout: float = 3.0


class DiscoverRequest(BaseModel):
    network: Optional[str] = None   # ex: "192.168.1.0/24", None = auto-detect
    ports: Optional[List[int]] = None
    timeout: float = 1.5


@router.post("/api/peer/probe", dependencies=[Depends(deps.verify_admin_token)])
async def probe_peer(req: ProbeRequest) -> Dict[str, Any]:
    """Sonde une adresse précise (host:port) pour vérifier la présence d'une instance Lumena.

    Contrairement à /api/peer/discover (scan LAN), cette route ne scanne qu'une seule adresse.
    Utilisée pour jumeler un pair connu par son adresse.
    """
    if not req.host or not req.host.strip():
        raise HTTPException(status_code=422, detail="host est requis")
    if not (1 <= req.port <= 65535):
        raise HTTPException(status_code=422, detail=f"Port hors plage [1-65535] : {req.port}")
    _validate_peer_host(req.host.strip())
    safe_timeout = max(_TIMEOUT_MIN, min(_TIMEOUT_MAX, req.timeout))

    from src.runtime.peer_discovery import probe_single_peer
    result = await probe_single_peer(req.host.strip(), req.port, timeout=safe_timeout)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail=f"Aucune instance Lumena trouvée sur {req.host}:{req.port}",
        )
    return result


@router.post("/api/peer/discover", dependencies=[Depends(deps.verify_admin_token)])
async def discover_peers(req: DiscoverRequest) -> Dict[str, Any]:
    """Scanne le LAN pour trouver des instances Lumena (LUMENA_PEER_DISCOVERY=1 requis).

    Les pairs découverts sont automatiquement ajoutés au registre avec trust='unknown'.
    Aucun pair unknown ne peut recevoir de délégation.
    """
    from src.runtime.peer_discovery import is_peer_discovery_enabled, scan_lan_for_peers
    if not is_peer_discovery_enabled():
        raise HTTPException(
            status_code=403,
            detail="Découverte LAN désactivée. Activez LUMENA_PEER_DISCOVERY=1 (ou l'interrupteur réseau).",
        )

    # Validation réseau — uniquement plages privées RFC1918 + loopback
    if req.network is not None:
        net_str = req.network.strip()
        if not any(net_str.startswith(pfx) for pfx in _PRIVATE_PREFIXES):
            raise HTTPException(
                status_code=422,
                detail=f"Réseau {net_str!r} non autorisé. Seuls les réseaux privés (RFC1918) sont acceptés.",
            )

    # Validation ports — max _MAX_SCAN_PORTS, plage [1024, 65535]
    if req.ports is not None:
        if len(req.ports) > _MAX_SCAN_PORTS:
            raise HTTPException(
                status_code=422,
                detail=f"Trop de ports demandés ({len(req.ports)} > {_MAX_SCAN_PORTS}).",
            )
        invalid = [p for p in req.ports if not (1024 <= p <= 65535)]
        if invalid:
            raise HTTPException(
                status_code=422,
                detail=f"Ports hors plage [1024-65535] : {invalid}.",
            )

    # Timeout borné
    safe_timeout = max(_TIMEOUT_MIN, min(_TIMEOUT_MAX, req.timeout))

    # Timeout global élargi : couvrir un /24 complet (jusqu'à 2540 sondes) sans
    # tronquer (l'ancien 12s ratait les hôtes "lointains", ex: .57).
    _GLOBAL_SCAN_TIMEOUT = 30.0
    # "Auto" (network=None) → scanner TOUS les sous-réseaux privés, pas seulement
    # la route par défaut (qui peut tomber sur un adaptateur VIRTUEL ex 192.168.80.x).
    _networks = [req.network]
    if req.network is None:
        try:
            from src.runtime.network_diagnostics import get_network_interfaces
            _nets = [i.get("network") for i in get_network_interfaces() if i.get("network")]
            _networks = _nets[:4] if _nets else [None]
        except Exception:
            _networks = [None]
    found = []
    _seen_ids: set = set()
    for _net in _networks:
        try:
            _part = await asyncio.wait_for(
                scan_lan_for_peers(network=_net, ports=req.ports, timeout=safe_timeout),
                timeout=_GLOBAL_SCAN_TIMEOUT,
            )
        except asyncio.TimeoutError:
            _part = []
        for _p in _part:
            _pid = _p.get("instance_id")
            if _pid and _pid not in _seen_ids:
                _seen_ids.add(_pid)
                found.append(_p)
    # Persist les pairs découverts (trust=unknown — ne peuvent pas recevoir de délégation)
    with _PEER_LOCK:
        data = _load_peers()
        for peer in found:
            iid = peer["instance_id"]
            if iid and iid not in data:
                data[iid] = {**peer, "discovered_at": datetime.now(timezone.utc).isoformat()}
        _save_peers(data)
    return {"discovered": len(found), "peers": found}


# ── Phase 8.5 — Auth peer token (défini ici car utilisé par receive_delegation) ──

async def _enforce_peer_signature(request: Request, peer: dict) -> None:
    """A2 — Vérifie la signature HMAC d'un message reçu d'un pair de **flotte**.

    Ne s'applique QUE si : clé de flotte présente, pair `pairing_method=="fleet"`,
    et signing actif. Sinon (pairs jumelés par code) → Bearer seul, inchangé.

    Vérifie dans l'ordre : présence en-têtes → fraîcheur horodatage → validité
    crypto (sur le **body brut**, bytes exacts émis) → non-rejeu du nonce. Le
    nonce n'est consommé qu'APRÈS validation crypto (un message invalide ne peut
    pas « brûler » le nonce d'un message légitime). Tout rejet est audité.
    """
    from src.runtime.peer_fleet import get_fleet_key
    from src.runtime.peer_signing import (
        is_signing_enabled, is_timestamp_fresh, seen_nonce, verify_envelope_signature,
        SIG_HEADER, TS_HEADER, NONCE_HEADER,
    )
    from src.runtime.peer_protocol import write_audit_log
    from src.utils.paths import INSTANCE_ID as OWN_ID

    fleet_key = get_fleet_key()
    if not (fleet_key and peer.get("pairing_method") == "fleet" and is_signing_enabled()):
        return  # pair non-flotte / signing désactivé → Bearer seul (compat ascendante)

    peer_id = peer.get("instance_id", "")
    sig = request.headers.get(SIG_HEADER, "").strip()
    ts = request.headers.get(TS_HEADER, "").strip()
    nonce = request.headers.get(NONCE_HEADER, "").strip()

    def _reject(reason: str, http_detail: str) -> None:
        try:
            write_audit_log(
                event=reason, from_instance_id=peer_id, task_id="",
                scope="", status="refused", detail=http_detail,
            )
        except Exception:
            pass
        raise HTTPException(status_code=401, detail=http_detail)

    if not sig or not ts or not nonce:
        _reject("signature_missing", "Signature de flotte requise (X-Lumena-Sig/Ts/Nonce manquant).")
    if not is_timestamp_fresh(ts):
        _reject("signature_stale", "Horodatage hors fenêtre — message rejeté (anti-rejeu).")

    body = await request.body()
    canonical = body.decode("utf-8", "replace")
    if not verify_envelope_signature(
        sig, canonical, from_id=peer_id, to_id=OWN_ID, ts=ts, nonce=nonce, fleet_key=fleet_key
    ):
        _reject("signature_invalid", "Signature de flotte invalide — intégrité non vérifiée.")
    # Crypto valide → on peut consommer le nonce sans risque de DoS sur les légitimes.
    if seen_nonce(nonce):
        _reject("signature_replay", "Nonce déjà utilisé — rejeu détecté.")


async def verify_peer_token(
    request: Request,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Vérifie le peer token (+ signature de flotte A2) sur les routes pair.

    Le token présenté est haché (SHA-256) et comparé aux peer_token_hash stockés
    dans le registre de pairs. Seul un pair trusted avec un hash valide est accepté.
    Le token admin ne traverse jamais le réseau entre pairs.

    A2 : pour un pair de flotte, la signature HMAC du message est en plus
    exigée et vérifiée (intégrité + authenticité + anti-rejeu).
    """
    from src.runtime.peer_tokens import verify_peer_token as _verify

    candidate = (authorization or "").replace("Bearer ", "").strip()
    if not candidate:
        raise HTTPException(status_code=401, detail="Peer token requis (Authorization: Bearer <token>).")

    with _PEER_LOCK:
        data = _load_peers()

    for peer in data.values():
        token_hash = peer.get("peer_token_hash")
        if token_hash and peer.get("trust") == "trusted":
            if _verify(candidate, token_hash):
                await _enforce_peer_signature(request, peer)
                with _PEER_LOCK:
                    fresh = _load_peers()
                    if peer["instance_id"] in fresh:
                        fresh[peer["instance_id"]]["last_seen"] = datetime.now(timezone.utc).isoformat()
                        _save_peers(fresh)
                return peer

    raise HTTPException(status_code=401, detail="Token pair invalide ou inconnu.")


# ── Phase 6 — Délégation inter-instances ─────────────────────────────────────

class DelegatePayload(BaseModel):
    task_id: str
    from_instance_id: str
    from_user_id: str
    actor_id: str
    scope: str                       # "chat" uniquement en phase initiale
    prompt: str
    context: Dict[str, Any] = {}


class TaskStatusResponse(BaseModel):
    task_id: str
    status: str
    response: Optional[str] = None
    evidence: List[str] = []
    logs: List[str] = []


# Stockage en mémoire des tâches délégées (task_id → résultat)
_delegated_tasks: Dict[str, dict] = {}
_tasks_lock = threading.Lock()


def _peer_execution_allowed_tools(peer: dict):
    """(allowed_tools, level) pour exécuter une requête de ce pair (A3 — brique 2).

    Le **niveau de capacité accordé** au pair (défaut `chat`) borne le jeu
    d'outils de l'agent qui traite la demande. `chat` = liste blanche
    lecture/savoir ; `mission` = `None` (**tous les outils**, CodeAgent inclus).
    """
    from src.runtime.peer_capability import normalize_level, resolve_allowed_tools

    level = normalize_level((peer or {}).get("capability_level"))
    return resolve_allowed_tools(level), level


def _capability_prompt_note(level: str) -> str:
    """Note de capacité à injecter dans le prompt de délégation.

    Aligne ce que l'agent CROIT pouvoir faire sur ce qui est réellement enforced
    (filtre A4 Couche 0). Sans ça, le prompt affichait « Scope : task.delegate »
    alors que le niveau était `chat` → l'agent retentait write_file en boucle.
    """
    from src.runtime.peer_capability import normalize_level as _nl
    if _nl(level) == "mission":
        return "Capacité accordée : MISSION (tous tes outils sont disponibles)."
    return (
        "⚠️ CAPACITÉ ACCORDÉE : CHAT — LECTURE SEULE. Aucune écriture ni action "
        "(write_file, create_project, mail, etc.) n'est possible ; seuls les outils "
        "de lecture/recherche fonctionnent. Si la demande exige une action, réponds "
        "honnêtement que ton niveau ne te permet que la lecture."
    )


@router.post("/api/peer/delegate")
async def receive_delegation(
    req: DelegatePayload,
    _auth_peer: dict = Depends(verify_peer_token),
) -> Dict[str, Any]:
    """Reçoit une tâche déléguée d'un pair Lumena.

    Règles :
    - Le peer token doit appartenir exactement à from_instance_id (pas d'usurpation inter-pairs).
    - Le pair doit être 'trusted' (pas unknown, pas blocked).
    - Le scope demandé doit être dans ALLOWED_DELEGATION_SCOPES (chat uniquement).
    - Toute tentative est auditée (acceptée ou refusée).
    """
    from src.runtime.peer_protocol import write_audit_log

    # 0. Liaison token ↔ from_instance_id — empêche l'usurpation inter-pairs.
    # Le token identifie un pair précis ; le payload ne peut pas prétendre être quelqu'un d'autre.
    if _auth_peer["instance_id"] != req.from_instance_id:
        from src.runtime.peer_protocol import write_audit_log
        write_audit_log(
            event="delegate_refused",
            from_instance_id=_auth_peer["instance_id"],
            task_id=req.task_id,
            scope=req.scope,
            status="refused",
            detail=(
                f"Usurpation : token appartient à {_auth_peer['instance_id']!r}, "
                f"from_instance_id déclaré = {req.from_instance_id!r}."
            ),
        )
        raise HTTPException(
            status_code=403,
            detail=(
                f"Token présenté appartient à {_auth_peer['instance_id']!r}, "
                f"pas à {req.from_instance_id!r}. Usurpation d'identité refusée."
            ),
        )

    # 1. Vérification trust — lit depuis le registre local de la route (même fichier que pair/block)
    with _PEER_LOCK:
        _peers_data = _load_peers()
    _peer = _peers_data.get(req.from_instance_id)
    _trust = _peer.get("trust", "unknown") if _peer else "unknown"

    # Logique fail-closed : toute valeur trust qui n'est PAS exactement "trusted" est refusée.
    # Couvre unknown, blocked, et tout token corrompu/inattendu ("admin", "trusted ", etc.).
    if _trust != "trusted":
        _detail = (
            f"Instance {req.from_instance_id!r} est bloquée."
            if _trust == "blocked"
            else f"Instance {req.from_instance_id!r} n'est pas jumelée (trust={_trust!r}). "
                 "Utilisez POST /api/peers/pair pour établir la confiance."
        )
        write_audit_log(
            event="delegate_refused",
            from_instance_id=req.from_instance_id,
            task_id=req.task_id,
            scope=req.scope,
            status="refused",
            detail=_detail,
        )
        raise HTTPException(status_code=403, detail=_detail)

    # 2. Vérification scope — whitelist globale ET allowed_scopes du pair spécifique
    from src.runtime.peer_scopes import validate_peer_scope
    try:
        validate_peer_scope(_peer, req.scope)
    except PermissionError as exc:
        write_audit_log(
            event="delegate_refused",
            from_instance_id=req.from_instance_id,
            task_id=req.task_id,
            scope=req.scope,
            status="refused",
            detail=str(exc),
        )
        raise HTTPException(status_code=403, detail=str(exc))

    # 3. Exécution (scope=chat → appel deps.lumena.chat)
    # A4 Couche 2 — refus fail-closed si tentative d'injection dans le prompt du pair.
    from src.runtime.peer_messages import detect_prompt_injection as _detect_inj
    _inj = _detect_inj(req.prompt)
    if _inj:
        write_audit_log(
            event="delegate_refused", from_instance_id=req.from_instance_id,
            task_id=req.task_id, scope=req.scope, status="refused",
            detail=f"Injection détectée ({_inj}).",
        )
        raise HTTPException(
            status_code=422,
            detail=f"Demande refusée : tentative d'injection détectée ({_inj}).",
        )

    write_audit_log(
        event="delegate_accepted",
        from_instance_id=req.from_instance_id,
        task_id=req.task_id,
        scope=req.scope,
        status="running",
    )
    # Phase 8.7 — Préfixe de contexte inter-instance pour éviter que le LLM
    # nie la délégation faute de savoir qu'il est appelé via le protocole pair.
    # A4 Couche 1 — on retire l'ancien préambule « délégation autorisée, réponds
    # normalement » (amplificateur d'injection, G3) et on encadre le prompt du pair
    # comme DONNÉE.
    from src.runtime.peer_messages import frame_external_request as _frame
    _, _lvl = _peer_execution_allowed_tools(_peer)
    _delegation_ctx = (
        f"[CONTEXTE SYSTÈME — DÉLÉGATION INTER-LUMENA]\n"
        f"Tu es appelée par une autre instance Lumena via le protocole inter-instance "
        f"(authentifié).\n"
        f"Instance appelante : {req.from_instance_id}\n"
        f"{_capability_prompt_note(_lvl)}\n\n"
    )
    _augmented_prompt = _delegation_ctx + _frame(req.prompt)
    try:
        if deps.lumena is None:
            raise RuntimeError("Lumena non initialisée sur cette instance")
        # A3 — brique 2 : exécution BORNÉE par le niveau de capacité accordé au
        # pair (défaut `chat`). Toolset restreint → l'agent n'a entre les mains
        # que les outils du niveau. `allow_when_busy` : honorer la requête pair
        # même si l'utilisateur local discute.
        _allowed, _level = _peer_execution_allowed_tools(_peer)
        response = await deps.lumena.think_and_act_silent(
            _augmented_prompt,
            timeout=120.0,
            allowed_tools=(list(_allowed) if _allowed is not None else None),
            allow_when_busy=True,
            allowed_tools_hard=(_allowed is not None),  # A4 — chat = filtre DUR
        )
        result = {
            "task_id": req.task_id,
            "status": "completed",
            "response": response or "",
            "evidence": [f"scope={req.scope}", f"from={req.from_instance_id}", f"level={_level}"],
            "logs": [f"Délégation acceptée — task_id={req.task_id} (niveau {_level})"],
        }
    except Exception as exc:
        result = {
            "task_id": req.task_id,
            "status": "error",
            "response": "",
            "evidence": [],
            "logs": [str(exc)],
        }

    with _tasks_lock:
        _delegated_tasks[req.task_id] = result

    write_audit_log(
        event="delegate_completed",
        from_instance_id=req.from_instance_id,
        task_id=req.task_id,
        scope=req.scope,
        status=result["status"],
    )
    return result


@router.get("/api/peer/tasks/{task_id}", dependencies=[Depends(deps.verify_admin_token)])
async def get_delegated_task(task_id: str) -> Dict[str, Any]:
    """Retourne le statut d'une tâche déléguée par un pair."""
    with _tasks_lock:
        result = _delegated_tasks.get(task_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Tâche {task_id!r} inconnue")
    return result


@router.get("/api/peer/audit", dependencies=[Depends(deps.verify_admin_token)])
async def get_peer_audit_log(limit: int = 100) -> Dict[str, Any]:
    """Retourne les dernières entrées de l'audit log inter-instances."""
    from src.runtime.peer_protocol import read_audit_log
    entries = read_audit_log(limit=limit)
    return {"entries": entries, "count": len(entries)}


@router.get("/api/peer/history", dependencies=[Depends(deps.verify_admin_token)])
async def get_peer_history(limit: int = 200) -> Dict[str, Any]:
    """Vue produit **READ-ONLY** : historique des échanges inter-Lumena.

    Agrège les sources persistées (audit inter-instances + événements de tâches
    + connaissances partagées) en **fils lisibles** groupés par tâche/savoir.
    Ne mute RIEN (pas d'import, pas de nettoyage, pas d'action). Sanitizé :
    aucun token/secret/payload brut ne ressort (cf. `peer_history`).
    """
    from src.runtime.peer_protocol import read_audit_log
    from src.runtime.peer_history import build_peer_history, history_stats
    from src.utils.paths import INSTANCE_ID as _own_id

    safe_limit = max(10, min(2000, int(limit)))

    audit = read_audit_log(limit=safe_limit * 4)
    task_events = _read_task_events(task_id=None, limit=safe_limit * 4)

    knowledge: List[dict] = []
    try:
        from src.runtime.shared_knowledge import load_shared_knowledge, public_knowledge_view
        raw = load_shared_knowledge()
        if isinstance(raw, dict):
            for kid, rec in raw.items():
                view = public_knowledge_view(rec)
                view["id"] = kid
                knowledge.append(view)
    except Exception:
        knowledge = []

    with _PEER_LOCK:
        peers = _load_peers()
    peer_names = {
        pid: (p.get("instance_name") or pid[:12])
        for pid, p in peers.items()
    }

    # Missions SORTANTES suivies par cette instance (tracker côté A).
    missions: List[dict] = []
    try:
        from src.runtime.peer_mission_tracker import list_all_missions
        missions = list_all_missions()
    except Exception:
        missions = []

    exchanges = build_peer_history(
        audit=audit,
        task_events=task_events,
        knowledge=knowledge,
        own_id=_own_id,
        peer_names=peer_names,
        missions=missions,
        limit=safe_limit,
    )
    return {"exchanges": exchanges, "count": len(exchanges), "stats": history_stats(exchanges)}


@router.get("/api/peer/missions/active", dependencies=[Depends(deps.verify_admin_token)])
async def get_active_peer_missions() -> Dict[str, Any]:
    """Vue **READ-ONLY** des missions sortantes encore en cours (Cran 1 — UI live).

    Wrap de `list_pending()` (statuts non terminaux). Sanitizé : aucun token ni
    payload brut ne ressort, uniquement de quoi afficher un suivi visuel.
    """
    try:
        from src.runtime.peer_mission_tracker import list_pending
        pending = list_pending()
    except Exception:
        pending = []

    missions = [
        {
            "task_id": m.get("task_id", ""),
            "peer_id": m.get("peer_id", ""),
            "peer_name": m.get("peer_name") or (str(m.get("peer_id") or "")[:12]),
            "objective": str(m.get("objective") or "")[:200],
            "status": m.get("status") or "queued",
            "submitted_at": m.get("submitted_at", ""),
            "last_poll": m.get("last_poll", ""),
        }
        for m in pending
    ]
    missions.sort(key=lambda x: x.get("submitted_at", ""), reverse=True)
    return {"missions": missions, "count": len(missions)}


@router.get("/api/peer/events")
async def peer_events_stream(token: Optional[str] = None, _auth=Depends(deps.verify_admin_token)):
    """Cran 2 — Flux SSE temps réel des événements pairs (missions, refus).

    Pousse instantanément l'état des missions inter-Lumena (queued → running →
    completed/failed) au lieu d'attendre le poll. Lecture seule : aucun secret ne
    transite (le bus ne publie que des métadonnées d'affichage).
    """
    from fastapi.responses import StreamingResponse
    from src.runtime.peer_event_bus import get_peer_event_bus

    bus = get_peer_event_bus()
    heartbeat_sec = max(3, int(bus.get_stats().get("heartbeat_sec", 15)))

    async def generate():
        subscriber_id = None
        queue = None
        try:
            subscriber_id, queue = bus.subscribe(max_queue=200)
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=heartbeat_sec)
                    yield f"event: peer\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    hb = {"type": "heartbeat", "ts": datetime.now(timezone.utc).isoformat()}
                    yield f"event: heartbeat\ndata: {json.dumps(hb, ensure_ascii=False)}\n\n"
        finally:
            if subscriber_id:
                bus.unsubscribe(subscriber_id)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


class HaltRequest(BaseModel):
    halt: bool


@router.get("/api/peer/halt", dependencies=[Depends(deps.verify_admin_token)])
async def get_peer_halt() -> Dict[str, Any]:
    """État du kill-switch réseau (urgence)."""
    from src.runtime.peer_network_autonomy import is_peer_halt_enabled
    return {"halt": is_peer_halt_enabled()}


@router.post("/api/peer/halt", dependencies=[Depends(deps.verify_admin_token)])
async def set_peer_halt(req: HaltRequest) -> Dict[str, Any]:
    """Kill-switch SOFT du réseau (urgence, effet immédiat + persistant).

    halt=True → veto toute NOUVELLE activité réseau (délégations in/out, découverte,
    conscience). NE coupe PAS les missions EN COURS (Lumena 24/7) : drain gracieux.
    halt=False → reprise.
    """
    value = "1" if req.halt else "0"
    os.environ["LUMENA_PEER_HALT"] = value  # live immédiat
    try:
        from web.routes.config import _write_env_values
        _write_env_values({"LUMENA_PEER_HALT": value})  # persistant
    except Exception as exc:
        from loguru import logger as _logger
        _logger.warning("[peer.halt] écriture .env échouée (live appliqué quand même): {}", exc)

    from src.runtime.peer_protocol import write_audit_log
    write_audit_log(
        event="peer_halt_set", from_instance_id="local", task_id="kill-switch",
        scope="", status="halted" if req.halt else "resumed",
        detail="Kill-switch SOFT activé (missions en cours préservées)" if req.halt else "Réseau repris",
    )
    try:
        from src.runtime.peer_event_bus import publish_peer_event
        publish_peer_event("halt", halt=req.halt)
    except Exception:
        pass
    return {"ok": True, "halt": req.halt}


class RelaunchRequest(BaseModel):
    task_id: str
    escalate_capability: bool = False


@router.post("/api/peer/missions/relaunch", dependencies=[Depends(deps.verify_admin_token)])
async def relaunch_mission(req: RelaunchRequest) -> Dict[str, Any]:
    """C2.2c — Relance une mission passée (échouée/refusée/annulée) à l'identique.

    Réutilise l'objectif + le pair de la mission. `escalate_capability=True` passe
    d'abord le pair en niveau « mission » (refus→approuver). La relance repasse par
    TOUT le filet (collaboration, halt, quarantaine, anti-SSRF) → un réseau coupé
    ou un pair isolé ne sera PAS relancé.
    """
    from src.runtime.peer_mission_tracker import get_mission

    mission = get_mission(req.task_id)
    if not mission:
        raise HTTPException(status_code=404, detail=f"Mission {req.task_id!r} inconnue.")
    objective = str(mission.get("objective") or "").strip()
    peer_id = str(mission.get("peer_id") or "").strip()
    if not objective or not peer_id:
        raise HTTPException(status_code=400, detail="Mission sans objectif ou pair exploitable.")

    if req.escalate_capability:
        # Refus→approuver : passe le pair en « mission » (auto-accorde les scopes).
        await update_peer_capability(peer_id, CapabilityUpdateRequest(level="mission"))

    from src.reasoning.handlers.peer_tasks import submit_peer_task_handler
    res = await submit_peer_task_handler(None, peer_id, objective)
    if not getattr(res, "success", False):
        return {"ok": False, "error": getattr(res, "error", None) or getattr(res, "output", "Échec de la relance")}

    from src.runtime.peer_protocol import write_audit_log
    write_audit_log(event="mission_relaunched", from_instance_id=peer_id, task_id=req.task_id,
                    scope="task.delegate", status="running",
                    detail=f"Relance (escalate={req.escalate_capability})")
    return {"ok": True, "message": getattr(res, "output", "Mission relancée."), "source_task_id": req.task_id}


@router.get("/api/peer/deliverables", dependencies=[Depends(deps.verify_admin_token)])
async def list_peer_deliverables(task_id: str) -> Dict[str, Any]:
    """C2.2a — Liste (lecture seule) les fichiers reçus d'une mission inter-Lumena.

    Path-safe : on ne sert QUE des chemins résolus sous `WORKSPACE_DIR`. Aucun
    contenu de fichier ici (juste nom + taille) — pure lecture de métadonnées.
    """
    from src.runtime.peer_mission_tracker import get_mission
    from src.utils.paths import WORKSPACE_DIR

    mission = get_mission(task_id)
    if not mission:
        raise HTTPException(status_code=404, detail=f"Mission {task_id!r} inconnue.")
    raw_dir = str(mission.get("artifacts_dir") or "").strip()
    if not raw_dir:
        return {"task_id": task_id, "dir": "", "files": [], "count": 0}

    try:
        base = Path(WORKSPACE_DIR).resolve()
        target = Path(raw_dir).resolve()
        # Anti-traversal : le dossier DOIT être sous le workspace.
        if base != target and base not in target.parents:
            raise HTTPException(status_code=403, detail="Chemin hors workspace refusé.")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=400, detail="Chemin de livrables invalide.")

    files: List[Dict[str, Any]] = []
    if target.exists() and target.is_dir():
        for p in sorted(target.rglob("*")):
            if p.is_file():
                try:
                    files.append({"name": str(p.relative_to(target)), "size": p.stat().st_size})
                except Exception:
                    continue
            if len(files) >= 200:
                break
    return {"task_id": task_id, "dir": str(target), "files": files, "count": len(files)}


async def _outbound_cancel_mission(peer: dict, task_id: str) -> Dict[str, Any]:
    """C2.2b — Appel SORTANT signé pour annuler une mission chez le pair.

    Anti-SSRF + Bearer/signature flotte réutilisés. Best-effort : si le pair est
    injoignable, l'appelant marque quand même la mission annulée localement.
    """
    import httpx as _httpx
    from src.runtime.peer_host_validation import validate_peer_host
    from src.runtime.peer_signing import build_signed_request
    from src.utils.paths import INSTANCE_ID as _OWN_ID

    host = str(peer.get("host") or "")
    port = int(peer.get("port") or 8080)
    token = peer.get("peer_token_outbound", "")
    if not token:
        return {"ok": False, "error": "token_sortant_absent"}
    validate_peer_host(host)  # anti-SSRF (lève si refusé)
    content, headers = build_signed_request(
        None, from_id=_OWN_ID, to_id=peer.get("instance_id", ""),
        peer_token=token, pairing_method=peer.get("pairing_method", ""),
    )
    url = f"http://{host}:{port}/api/peer/tasks/{task_id}"
    async with _httpx.AsyncClient(timeout=8.0) as client:
        r = await client.request("DELETE", url, content=content, headers=headers)
    return {"ok": r.status_code == 200, "status_code": r.status_code}


@router.delete("/api/peer/missions/{task_id}", dependencies=[Depends(deps.verify_admin_token)])
async def cancel_outbound_mission(task_id: str) -> Dict[str, Any]:
    """C2.2b — Annule (acte humain explicite) une mission SORTANTE en cours.

    Relais sortant signé vers le pair + marque la mission `cancelled` localement.
    C'est la SEULE action qui stoppe une mission (la sécurité auto ne coupe que le futur).
    """
    from src.runtime.peer_mission_tracker import get_mission, update_status, _TERMINAL

    mission = get_mission(task_id)
    if not mission:
        raise HTTPException(status_code=404, detail=f"Mission {task_id!r} inconnue.")
    if mission.get("status") in _TERMINAL:
        return {"ok": True, "task_id": task_id, "status": mission.get("status"), "note": "Déjà terminée."}

    peer_id = mission.get("peer_id", "")
    with _PEER_LOCK:
        peer = _load_peers().get(peer_id)

    outbound: Dict[str, Any] = {"ok": False, "error": "pair_inconnu"}
    if peer:
        try:
            outbound = await _outbound_cancel_mission(peer, task_id)
        except Exception as exc:
            outbound = {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:120]}"}

    update_status(task_id, "cancelled")  # retire des « en cours » + publie SSE

    from src.runtime.peer_protocol import write_audit_log
    write_audit_log(event="mission_cancelled", from_instance_id=peer_id, task_id=task_id,
                    scope="task.delegate", status="cancelled",
                    detail=f"Annulée par l'humain ; relais pair ok={outbound.get('ok')}")
    return {"ok": True, "task_id": task_id, "status": "cancelled", "outbound": outbound}


@router.get("/api/peer/quarantine", dependencies=[Depends(deps.verify_admin_token)])
async def list_peer_quarantine() -> Dict[str, Any]:
    """Liste des pairs en quarantaine automatique (C-1.b), enrichie du nom."""
    from src.runtime.peer_quarantine import list_quarantined
    items = list_quarantined()
    with _PEER_LOCK:
        peers = _load_peers()
    for it in items:
        p = peers.get(it.get("peer_id", "")) or {}
        it["peer_name"] = p.get("alias") or p.get("instance_name") or str(it.get("peer_id", ""))[:12]
    return {"quarantined": items, "count": len(items)}


@router.post("/api/peer/quarantine/release/{instance_id}", dependencies=[Depends(deps.verify_admin_token)])
async def release_peer_quarantine(instance_id: str) -> Dict[str, Any]:
    """Lève la quarantaine d'un pair (acte humain) + reset du compteur."""
    from src.runtime.peer_quarantine import release
    release(instance_id)
    return {"ok": True, "instance_id": instance_id}


@router.get("/api/peer/metrics", dependencies=[Depends(deps.verify_admin_token)])
async def get_peer_metrics(limit: int = 5000) -> Dict[str, Any]:
    """Production metrics derived from existing peer audit/task logs.

    No new database and no secrets: this endpoint only aggregates persisted
    audit entries, task events and current in-memory task state.
    """
    from src.runtime.peer_protocol import read_audit_log

    safe_limit = max(100, min(20000, int(limit)))
    with _PEER_LOCK:
        peers = list(_load_peers().values())

    audit = read_audit_log(limit=safe_limit)
    task_events = _read_task_events(task_id=None, limit=safe_limit)

    trust_counts = {"trusted": 0, "unknown": 0, "blocked": 0}
    token_count = 0
    for peer in peers:
        trust = peer.get("trust", "unknown")
        trust_counts[trust] = trust_counts.get(trust, 0) + 1
        if peer.get("peer_token_hash"):
            token_count += 1

    delegation_events = [e for e in audit if str(e.get("event", "")).startswith("delegate_")]
    knowledge_events = [e for e in audit if str(e.get("event", "")).startswith("knowledge_query")]
    rate_limited = [e for e in audit if e.get("event") == "peer_rate_limited" or e.get("status") == "rate_limited"]
    refused = [e for e in audit if e.get("status") == "refused" or str(e.get("event", "")).endswith("_refused")]
    completed = [e for e in audit if e.get("status") == "completed" or str(e.get("event", "")).endswith("_completed")]

    durations = [
        d for d in (_parse_duration_ms(str(e.get("detail", ""))) for e in audit + task_events)
        if d is not None
    ]

    scope_refusals = 0
    auth_errors = 0
    ssrf_refusals = 0
    user_issues: List[str] = []
    for event in refused + rate_limited:
        detail = str(event.get("detail") or "").lower()
        if "scope" in detail or "not allowed" in detail or "non autorisé" in detail:
            scope_refusals += 1
        if "token" in detail or "usurpation" in detail:
            auth_errors += 1
        if "ssrf" in detail or ("host" in detail and "refus" in detail):
            ssrf_refusals += 1
        issue = _classify_peer_issue(event)
        if issue and issue not in user_issues:
            user_issues.append(issue)

    with _async_tasks_lock:
        active_tasks = _count_active_async_tasks_for_all_locked()
        queued_or_running = [
            v for v in _async_task_store.values()
            if v.get("status") in {"queued", "running"}
        ]

    success_rate = (
        round(len(completed) / max(1, len(completed) + len(refused)) * 100, 1)
        if (completed or refused) else 100.0
    )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "peers": {
            "total": len(peers),
            "trusted": trust_counts.get("trusted", 0),
            "unknown": trust_counts.get("unknown", 0),
            "blocked": trust_counts.get("blocked", 0),
            "with_peer_token": token_count,
        },
        "delegations": {
            "total_events": len(delegation_events),
            "completed": len(completed),
            "refused": len(refused),
            "success_rate_percent": success_rate,
        },
        "knowledge_queries": {
            "events": len(knowledge_events),
        },
        "tasks": {
            "active": active_tasks,
            "queued_or_running": len(queued_or_running),
            "events": len(task_events),
        },
        "errors": {
            "auth": auth_errors,
            "scope": scope_refusals,
            "ssrf": ssrf_refusals,
            "rate_limited": len(rate_limited),
        },
        "latency": {
            "avg_ms": round(sum(durations) / len(durations), 1) if durations else None,
            "samples": len(durations),
        },
        "user_issues": user_issues[:8],
    }


# ── Phase 7 — Instances locales ───────────────────────────────────────────────

@router.get("/api/instances/local", dependencies=[Depends(deps.verify_admin_token)])
async def list_local_instances() -> Dict[str, Any]:
    """Retourne les instances Lumena vivantes sur ce PC (depuis le registre local).

    Phase 8.9 — Fallback : si l'instance courante n'est pas dans le registre
    (LUMENA_MULTI_INSTANCE=0, registre absent, heartbeat pas encore écrit),
    une entrée synthétique est injectée pour que la carte UI ne soit jamais vide.
    """
    import os
    from src import __version__
    from src.runtime.instance_registry import get_registry
    from src.utils.paths import INSTANCE_ID, INSTANCE_NAME, INSTANCE_ROLE

    instances = get_registry().get_live()

    result = [
        {
            "instance_id": r.instance_id,
            "instance_name": r.instance_name,
            "role": r.role,
            "port": r.port,
            "pid": r.pid,
            "version": r.version,
            "capabilities": r.capabilities,
            "started_at": r.started_at,
            "last_seen": r.last_seen,
            "is_self": r.instance_id == INSTANCE_ID,
            "synthetic": False,
        }
        for r in instances
    ]

    # Si l'instance courante est absente du registre, on l'injecte depuis le runtime
    own_in_registry = any(r.instance_id == INSTANCE_ID for r in instances)
    if not own_in_registry:
        result.insert(0, {
            "instance_id": INSTANCE_ID,
            "instance_name": INSTANCE_NAME,
            "role": INSTANCE_ROLE,
            "port": int(os.getenv("LUMENA_PORT", "8080")),
            "pid": os.getpid(),
            "version": __version__,
            "capabilities": _compute_capabilities(),
            "started_at": "",
            "last_seen": datetime.now(timezone.utc).isoformat(),
            "is_self": True,
            "synthetic": True,
        })

    return {
        "own_instance_id": INSTANCE_ID,
        "instances": result,
        "count": len(result),
    }


@router.post("/api/instances/local/cleanup", dependencies=[Depends(deps.verify_admin_token)])
async def cleanup_local_instances() -> Dict[str, Any]:
    """Supprime les entrees locales stale du registre multi-instance."""
    from src.runtime.instance_registry import get_registry

    removed = get_registry().cleanup_stale()
    return {"ok": True, "removed": removed}


@router.delete("/api/instances/local/{instance_id}", dependencies=[Depends(deps.verify_admin_token)])
async def delete_local_instance(
    instance_id: str,
    req: Optional[LocalInstanceDeleteRequest] = None,
) -> Dict[str, Any]:
    """Supprime une entree du registre local.

    L'instance courante est protegee par defaut car son heartbeat la recreerait.
    """
    from src.runtime.instance_registry import get_registry
    from src.utils.paths import INSTANCE_ID

    force = bool(req.force) if req else False
    if instance_id == INSTANCE_ID and not force:
        raise HTTPException(
            status_code=409,
            detail="Impossible de supprimer l'instance courante sans force=true.",
        )
    registry = get_registry()
    known = {r.instance_id for r in registry.get_all()}
    if instance_id not in known:
        raise HTTPException(status_code=404, detail=f"Instance {instance_id!r} inconnue")
    registry.unregister(instance_id)
    return {"ok": True, "instance_id": instance_id, "action": "deleted"}


# ── Phase 8.10 — Discovery multi-réseaux ─────────────────────────────────────

@router.get("/api/instance/network-interfaces", dependencies=[Depends(deps.verify_admin_token)])
async def list_network_interfaces() -> Dict[str, Any]:
    """Liste les sous-réseaux LAN disponibles pour le scan multi-réseau.

    Retourne une entrée par sous-réseau /24 détecté sur les interfaces locales.
    Utilisé par l'UI pour laisser l'utilisateur choisir quel réseau scanner.
    """
    from src.runtime.network_diagnostics import get_network_interfaces
    interfaces = get_network_interfaces()
    return {"interfaces": interfaces, "count": len(interfaces)}


# ── Phase 8.1 — Diagnostic réseau ────────────────────────────────────────────

@router.get("/api/instance/network-diagnostic", dependencies=[Depends(deps.verify_admin_token)])
async def network_diagnostic() -> Dict[str, Any]:
    """Diagnostic réseau de cette instance : écoute, bind host, LAN IPs, pare-feu.

    Ne retourne aucun secret. Protégé admin pour ne pas exposer la topologie réseau.
    """
    from src.runtime.network_diagnostics import build_network_diagnostic
    return build_network_diagnostic()


@router.get("/api/peers/health", dependencies=[Depends(deps.verify_admin_token)])
async def get_peers_health(timeout: float = 1.5) -> Dict[str, Any]:
    """Probe known peers and return a compact production health summary."""
    from src.runtime.peer_discovery import probe_single_peer

    safe_timeout = max(0.2, min(5.0, float(timeout)))
    with _PEER_LOCK:
        peers = [_sanitize_peer(p) for p in _load_peers().values()]

    async def _probe(peer: dict) -> dict:
        iid = peer.get("instance_id", "")
        trust = peer.get("trust", "unknown")
        host = str(peer.get("host") or "")
        port = int(peer.get("port") or 8080)
        base = {
            "instance_id": iid,
            "instance_name": peer.get("instance_name", ""),
            "host": host,
            "port": port,
            "trust": trust,
            "scopes": peer.get("allowed_scopes") or ["chat"],
            "last_seen": peer.get("last_seen", ""),
            "reachable": False,
            "latency_ms": None,
            "status": "blocked" if trust == "blocked" else "unknown",
            "last_error": "",
        }
        if trust == "blocked":
            base["last_error"] = "Pair bloqué"
            return base
        try:
            _validate_peer_host(host)
        except HTTPException as exc:
            base["status"] = "invalid_host"
            base["last_error"] = str(exc.detail)
            return base

        t0 = time.monotonic()
        try:
            result = await probe_single_peer(host, port, timeout=safe_timeout)
            base["latency_ms"] = int((time.monotonic() - t0) * 1000)
            if result and result.get("instance_id") == iid:
                base["reachable"] = True
                base["status"] = "healthy"
                with _PEER_LOCK:
                    data = _load_peers()
                    if iid in data:
                        data[iid]["last_seen"] = datetime.now(timezone.utc).isoformat()
                        data[iid].pop("last_error", None)
                        _save_peers(data)
                return base
            base["status"] = "mismatch_or_down"
            base["last_error"] = "Instance distante indisponible ou identité différente"
            return base
        except Exception as exc:
            base["latency_ms"] = int((time.monotonic() - t0) * 1000)
            base["status"] = "down"
            base["last_error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
            return base

    items = await asyncio.gather(*[_probe(p) for p in peers], return_exceptions=False)
    trusted = [p for p in items if p.get("trust") == "trusted"]
    reachable_trusted = [p for p in trusted if p.get("reachable")]
    if not trusted:
        overall = "empty"
    elif len(reachable_trusted) == len(trusted):
        overall = "healthy"
    elif reachable_trusted:
        overall = "degraded"
    else:
        overall = "down"
    avg_latency = [
        p["latency_ms"] for p in items
        if p.get("reachable") and isinstance(p.get("latency_ms"), int)
    ]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "overall": overall,
        "count": len(items),
        "trusted_count": len(trusted),
        "reachable_trusted": len(reachable_trusted),
        "avg_latency_ms": round(sum(avg_latency) / len(avg_latency), 1) if avg_latency else None,
        "peers": items,
    }


# ── Phase 8.6 — Test délégation en un clic ───────────────────────────────────

@router.get("/api/peer/autonomy/status", dependencies=[Depends(deps.verify_admin_token)])
async def peer_autonomy_status() -> Dict[str, Any]:
    """Return the background peer-network autonomy state."""
    from src.runtime.peer_network_autonomy import get_peer_network_autonomy_status
    return get_peer_network_autonomy_status()


class PeerAutonomyRunRequest(BaseModel):
    scan: bool = True
    health: bool = True


@router.post("/api/peer/autonomy/run-once", dependencies=[Depends(deps.verify_admin_token)])
async def peer_autonomy_run_once(req: PeerAutonomyRunRequest) -> Dict[str, Any]:
    """Run one bounded autonomy pass on demand."""
    from src.runtime.peer_network_autonomy import run_peer_network_autonomy_once
    return await run_peer_network_autonomy_once(scan=req.scan, health=req.health)


class TestDelegationRequest(BaseModel):
    instance_id: str


@router.post("/api/peer/test-delegation", dependencies=[Depends(deps.verify_admin_token)])
async def test_delegation(req: TestDelegationRequest) -> Dict[str, Any]:
    """Teste la délégation vers un pair trusted en un clic.

    Utilise le peer_token_outbound (Phase 8.5) pour s'authentifier auprès du pair.
    Le token admin ne quitte jamais cette instance.
    """
    import uuid
    import httpx as _httpx

    with _PEER_LOCK:
        data = _load_peers()
    peer = data.get(req.instance_id)
    if not peer:
        raise HTTPException(status_code=404, detail=f"Pair {req.instance_id!r} inconnu")
    trust = peer.get("trust", "unknown")
    if trust != "trusted":
        raise HTTPException(
            status_code=403,
            detail=f"Pair {req.instance_id!r} non trusted (trust={trust!r}). "
                   "Jumelez d'abord via POST /api/peer/accept-pairing.",
        )

    peer_token = peer.get("peer_token_outbound", "")
    if not peer_token:
        return {
            "ok": False,
            "latency_ms": 0,
            "status": "no_peer_token",
            "response": "",
            "error": "Ce pair n'a pas de peer token. Rejumelez via le code de jumelage (POST /api/peer/pairing-code).",
        }

    from src.utils.paths import INSTANCE_ID as OWN_ID

    host = peer["host"]
    port = peer["port"]
    _validate_peer_host(host)

    task_id = f"test-{uuid.uuid4().hex[:8]}"
    payload = {
        "task_id": task_id,
        "from_instance_id": OWN_ID,
        "from_user_id": "local_admin",
        "actor_id": "test_delegation",
        "scope": "chat",
        "prompt": "Test de délégation inter-Lumena. Réponds simplement : 'Délégation OK.'",
    }

    t0 = time.monotonic()
    try:
        from src.runtime.peer_signing import build_signed_request
        _content, _headers = build_signed_request(
            payload, from_id=OWN_ID, to_id=peer.get("instance_id", req.instance_id),
            peer_token=peer_token, pairing_method=peer.get("pairing_method", ""),
        )
        async with _httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(
                f"http://{host}:{port}/api/peer/delegate",
                content=_content, headers=_headers,
            )
        latency_ms = int((time.monotonic() - t0) * 1000)

        if r.status_code != 200:
            return {
                "ok": False,
                "latency_ms": latency_ms,
                "status": "error",
                "response": "",
                "error": f"HTTP {r.status_code} — {r.text[:300]}",
            }
        d = r.json()
        return {
            "ok": d.get("status") == "completed",
            "latency_ms": latency_ms,
            "status": d.get("status", "unknown"),
            "response": d.get("response", ""),
        }
    except Exception as exc:
        latency_ms = int((time.monotonic() - t0) * 1000)
        return {
            "ok": False,
            "latency_ms": latency_ms,
            "status": "error",
            "response": "",
            "error": str(exc),
        }


# ── Phase 8.3 — Anti-SSRF ────────────────────────────────────────────────────

from src.runtime.peer_host_validation import validate_peer_host as _shared_validate_host


def _validate_peer_host(host: str) -> None:
    """Vérifie que le host est une IP privée strictement RFC1918.

    Délègue à src.runtime.peer_host_validation.validate_peer_host
    et convertit ValueError en HTTPException 422.
    """
    try:
        _shared_validate_host(host)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


# ── Phase 8.4 — Codes de jumelage ────────────────────────────────────────────

_PAIRING_CODE_TTL = 300  # 5 minutes
_pairing_codes: Dict[str, dict] = {}
_codes_lock = threading.Lock()


def _cleanup_expired_codes() -> None:
    now = time.monotonic()
    expired = [c for c, v in _pairing_codes.items() if v["expires_at"] < now]
    for c in expired:
        del _pairing_codes[c]


@router.post("/api/peer/pairing-code", dependencies=[Depends(deps.verify_admin_token)])
async def generate_pairing_code() -> Dict[str, Any]:
    """Génère un code de jumelage à 6 caractères valide 5 minutes.

    L'hôte affiche ce code à l'autre instance qui l'entre dans son interface.
    Un code est à usage unique et expire automatiquement.
    """
    from src.runtime.peer_tokens import generate_pairing_code as _gen_code

    code = _gen_code()
    expires_at = time.monotonic() + _PAIRING_CODE_TTL
    now_iso = datetime.now(timezone.utc).isoformat()
    with _codes_lock:
        _cleanup_expired_codes()
        _pairing_codes[code] = {"expires_at": expires_at, "issued_at": now_iso}

    return {
        "code": code,
        "expires_in": _PAIRING_CODE_TTL,
        "issued_at": now_iso,
    }


# ── Phase 8.5 — Échange de peer tokens ───────────────────────────────────────

class ValidatePairingRequest(BaseModel):
    code: str
    from_instance_id: str
    from_instance_name: str
    from_host: str
    from_port: int
    from_capabilities: List[str] = []
    peer_token_for_host: str  # token généré par le demandeur pour l'hôte (outbound de l'hôte)


@router.post("/api/peer/validate-pairing-code")
async def validate_pairing_code(req: ValidatePairingRequest) -> Dict[str, Any]:
    """Valide un code de jumelage et échange les peer tokens.

    Appelé par l'instance distante (pas par l'admin local) — le code fait office d'auth.
    Une seule validation par code (usage unique). Anti-SSRF : from_host doit être RFC1918.
    """
    from src.runtime.peer_tokens import generate_peer_token, hash_peer_token
    from src.utils.paths import INSTANCE_ID, INSTANCE_NAME

    _validate_peer_host(req.from_host)

    with _codes_lock:
        _cleanup_expired_codes()
        entry = _pairing_codes.get(req.code)
        if not entry or entry["expires_at"] < time.monotonic():
            _pairing_codes.pop(req.code, None)
            raise HTTPException(status_code=403, detail="Code de jumelage invalide ou expiré.")
        del _pairing_codes[req.code]

    # Token que l'hôte donnera au demandeur (demandeur l'utilise en outbound vers l'hôte)
    token_for_requester = generate_peer_token()
    token_for_requester_hash = hash_peer_token(token_for_requester)

    now = datetime.now(timezone.utc).isoformat()
    with _PEER_LOCK:
        data = _load_peers()
        data[req.from_instance_id] = {
            "instance_id": req.from_instance_id,
            "instance_name": req.from_instance_name,
            "host": req.from_host,
            "port": req.from_port,
            "capabilities": req.from_capabilities,
            "trust": "trusted",
            "pairing_method": "code",
            "paired_at": now,
            "last_seen": now,
            # hash du token qu'on a généré → valide les appels entrants du demandeur
            "peer_token_hash": token_for_requester_hash,
            # token brut reçu du demandeur → on l'utilise quand on appelle le demandeur
            "peer_token_outbound": req.peer_token_for_host,
            "allowed_scopes": ["chat"],
        }
        _save_peers(data)

    port = int(os.getenv("LUMENA_PORT", "8080"))
    try:
        from src.runtime.network_diagnostics import get_local_lan_ips
        lan_ips = get_local_lan_ips()
        own_host = lan_ips[0] if lan_ips else "0.0.0.0"
    except Exception:
        own_host = "0.0.0.0"

    return {
        "ok": True,
        "peer_token_for_requester": token_for_requester,
        "instance_id": INSTANCE_ID,
        "instance_name": INSTANCE_NAME,
        "host": own_host,
        "port": port,
        "capabilities": _compute_capabilities(),
    }


# ── A1 — Identité de flotte : auto-jumelage par preuve HMAC (sans code) ───────
# Deux instances avec le même LUMENA_FLEET_KEY s'auto-jumellent. La clé ne
# traverse jamais le réseau (preuves HMAC + nonces). Tokens dérivés, jamais
# transmis. Si la clé est absente → endpoints désactivés (jumelage manuel seul).

_FLEET_PENDING_TTL = 30  # secondes — fenêtre entre init et confirm
_fleet_pending: Dict[str, dict] = {}
_fleet_lock = threading.Lock()


def _cleanup_fleet_pending() -> None:
    now = time.monotonic()
    for k in [k for k, v in _fleet_pending.items() if v["expires_at"] < now]:
        _fleet_pending.pop(k, None)


def _own_lan_host_port() -> tuple:
    port = int(os.getenv("LUMENA_PORT", "8080"))
    try:
        from src.runtime.network_diagnostics import get_local_lan_ips
        ips = get_local_lan_ips()
        return (ips[0] if ips else "0.0.0.0"), port
    except Exception:
        return "0.0.0.0", port


class FleetPairInitRequest(BaseModel):
    from_instance_id: str
    from_instance_name: str = ""
    from_host: str
    from_port: int = 8080
    from_capabilities: List[str] = []
    nonce_init: str


class FleetPairConfirmRequest(BaseModel):
    from_instance_id: str
    nonce_init: str
    nonce_resp: str
    proof_a: str


@router.post("/api/peer/fleet-pair-init")
async def fleet_pair_init(req: FleetPairInitRequest) -> Dict[str, Any]:
    """A1 — Répondeur, étape 1 : reçoit le challenge d'un initiateur de la flotte.

    Prouve qu'on connaît la clé de flotte (proof_b), mémorise un pending TTL,
    attend le confirm. Auth = preuve HMAC (pas de token admin)."""
    from src.runtime.peer_fleet import is_fleet_pairing_enabled, generate_nonce, compute_proof
    from src.utils.paths import INSTANCE_ID, INSTANCE_NAME

    if not is_fleet_pairing_enabled():
        raise HTTPException(status_code=403, detail="Auto-jumelage de flotte désactivé (LUMENA_FLEET_KEY absent).")
    _validate_peer_host(req.from_host)
    if req.from_instance_id == INSTANCE_ID:
        raise HTTPException(status_code=400, detail="Auto-jumelage avec soi-même refusé.")

    nonce_resp = generate_nonce()
    proof_b = compute_proof(INSTANCE_ID, req.from_instance_id, req.nonce_init, nonce_resp)

    with _fleet_lock:
        _cleanup_fleet_pending()
        _fleet_pending[req.from_instance_id] = {
            "nonce_init": req.nonce_init,
            "nonce_resp": nonce_resp,
            "meta": {
                "instance_name": req.from_instance_name,
                "host": req.from_host,
                "port": req.from_port,
                "capabilities": req.from_capabilities,
            },
            "expires_at": time.monotonic() + _FLEET_PENDING_TTL,
        }

    host, port = _own_lan_host_port()
    return {
        "instance_id": INSTANCE_ID,
        "instance_name": INSTANCE_NAME,
        "host": host,
        "port": port,
        "capabilities": _compute_capabilities(),
        "nonce_resp": nonce_resp,
        "proof_b": proof_b,
    }


@router.post("/api/peer/fleet-pair-confirm")
async def fleet_pair_confirm(req: FleetPairConfirmRequest) -> Dict[str, Any]:
    """A1 — Répondeur, étape 2 : valide la preuve de l'initiateur et l'enregistre trusted."""
    from src.runtime.peer_fleet import is_fleet_pairing_enabled, verify_proof, get_fleet_key, derive_peer_token
    from src.runtime.peer_tokens import hash_peer_token
    from src.runtime.peer_protocol import write_audit_log
    from src.utils.paths import INSTANCE_ID, INSTANCE_NAME

    if not is_fleet_pairing_enabled():
        raise HTTPException(status_code=403, detail="Auto-jumelage de flotte désactivé.")

    with _fleet_lock:
        _cleanup_fleet_pending()
        pending = _fleet_pending.pop(req.from_instance_id, None)

    if (not pending
            or pending["nonce_init"] != req.nonce_init
            or pending["nonce_resp"] != req.nonce_resp):
        write_audit_log(event="fleet_pair_refused", from_instance_id=req.from_instance_id,
                        task_id="fleet", scope="pairing", status="refused", detail="pending absent/expiré ou nonces incohérents")
        raise HTTPException(status_code=403, detail="Aucun jumelage de flotte en attente ou nonces invalides.")

    # Vérifier la preuve de l'initiateur (prouveur = lui, autre = nous).
    if not verify_proof(req.proof_a, req.from_instance_id, INSTANCE_ID, req.nonce_init, req.nonce_resp):
        write_audit_log(event="fleet_pair_refused", from_instance_id=req.from_instance_id,
                        task_id="fleet", scope="pairing", status="refused", detail="preuve invalide (clé de flotte différente)")
        raise HTTPException(status_code=403, detail="Preuve de flotte invalide.")

    fk = get_fleet_key()
    meta = pending["meta"]
    now = datetime.now(timezone.utc).isoformat()
    with _PEER_LOCK:
        data = _load_peers()
        data[req.from_instance_id] = {
            **data.get(req.from_instance_id, {}),
            "instance_id": req.from_instance_id,
            "instance_name": meta.get("instance_name") or req.from_instance_id[:12],
            "host": meta.get("host", ""),
            "port": meta.get("port", 8080),
            "capabilities": meta.get("capabilities", []),
            "trust": "trusted",
            "pairing_method": "fleet",
            "paired_at": now,
            "last_seen": now,
            # hash du token que l'initiateur présentera (A→B) → valide ses appels entrants
            "peer_token_hash": hash_peer_token(derive_peer_token(req.from_instance_id, INSTANCE_ID, fleet_key=fk)),
            # token que nous présentons à l'initiateur (B→A)
            "peer_token_outbound": derive_peer_token(INSTANCE_ID, req.from_instance_id, fleet_key=fk),
            "allowed_scopes": ["chat"],
        }
        _save_peers(data)

    write_audit_log(event="fleet_pair_completed", from_instance_id=req.from_instance_id,
                    task_id="fleet", scope="pairing", status="completed")
    host, port = _own_lan_host_port()
    return {"ok": True, "instance_id": INSTANCE_ID, "instance_name": INSTANCE_NAME,
            "host": host, "port": port, "capabilities": _compute_capabilities()}


class FleetPairTrigger(BaseModel):
    host: str
    port: int = 8080


@router.post("/api/peer/fleet-pair", dependencies=[Depends(deps.verify_admin_token)])
async def fleet_pair_trigger(req: FleetPairTrigger) -> Dict[str, Any]:
    """A1 — Déclencheur explicite (admin) : auto-jumelle ce pair par preuve de flotte.

    L'auto-jumelage CONTINU/autonome (toute la flotte, en boucle) sera C1.
    """
    from src.runtime.peer_fleet import is_fleet_pairing_enabled
    from src.runtime.peer_discovery import attempt_fleet_pair

    if not is_fleet_pairing_enabled():
        return {"ok": False, "error": "LUMENA_FLEET_KEY absent — pose la clé de flotte (panneau config) sur les deux instances."}
    _validate_peer_host(req.host)
    return await attempt_fleet_pair(req.host, int(req.port))


class AcceptPairingRequest(BaseModel):
    host: str
    port: int
    code: str


@router.post("/api/peer/accept-pairing", dependencies=[Depends(deps.verify_admin_token)])
async def accept_pairing(req: AcceptPairingRequest) -> Dict[str, Any]:
    """Initie le jumelage par code depuis le côté demandeur.

    Anti-SSRF : seules les IPs RFC1918 sont acceptées.
    Génère un peer token pour l'hôte, envoie validate-pairing-code, stocke le retour.
    """
    import httpx as _httpx
    from src.runtime.peer_tokens import generate_peer_token, hash_peer_token
    from src.utils.paths import INSTANCE_ID, INSTANCE_NAME

    _validate_peer_host(req.host)

    # Token que le demandeur génère pour l'hôte (l'hôte l'utilisera en outbound vers nous)
    token_for_host = generate_peer_token()
    token_for_host_hash = hash_peer_token(token_for_host)

    our_port = int(os.getenv("LUMENA_PORT", "8080"))
    try:
        from src.runtime.network_diagnostics import get_local_lan_ips
        ips = get_local_lan_ips()
        our_host = ips[0] if ips else req.host
    except Exception:
        our_host = req.host

    payload = {
        "code": req.code,
        "from_instance_id": INSTANCE_ID,
        "from_instance_name": INSTANCE_NAME,
        "from_host": our_host,
        "from_port": our_port,
        "from_capabilities": _compute_capabilities(),
        "peer_token_for_host": token_for_host,
    }

    try:
        async with _httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(
                f"http://{req.host}:{req.port}/api/peer/validate-pairing-code",
                json=payload,
            )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Impossible de joindre {req.host}:{req.port} — {exc}")

    if r.status_code != 200:
        raise HTTPException(
            status_code=r.status_code,
            detail=f"Code refusé ou erreur hôte : {r.text[:300]}",
        )

    d = r.json()
    if not d.get("ok"):
        raise HTTPException(status_code=403, detail="Code refusé par l'hôte.")

    remote_instance_id = d["instance_id"]
    remote_token_for_us = d["peer_token_for_requester"]

    now = datetime.now(timezone.utc).isoformat()
    with _PEER_LOCK:
        data = _load_peers()
        data[remote_instance_id] = {
            "instance_id": remote_instance_id,
            "instance_name": d.get("instance_name", ""),
            "host": req.host,
            "port": req.port,
            "capabilities": d.get("capabilities", []),
            "trust": "trusted",
            "pairing_method": "code",
            "paired_at": now,
            "last_seen": now,
            # hash du token qu'on a généré pour l'hôte → valide ses appels entrants
            "peer_token_hash": token_for_host_hash,
            # token brut reçu de l'hôte → on l'utilise quand on appelle l'hôte
            "peer_token_outbound": remote_token_for_us,
            "allowed_scopes": ["chat"],
        }
        _save_peers(data)

    return {
        "ok": True,
        "instance_id": remote_instance_id,
        "instance_name": d.get("instance_name", ""),
        "host": req.host,
        "port": req.port,
        "trust": "trusted",
    }


@router.post("/api/peer/revoke-token/{instance_id}", dependencies=[Depends(deps.verify_admin_token)])
async def revoke_peer_token(instance_id: str) -> Dict[str, Any]:
    """Révoque le peer token d'un pair — il ne peut plus déléguer jusqu'au rejumelage."""
    with _PEER_LOCK:
        data = _load_peers()
        if instance_id not in data:
            raise HTTPException(status_code=404, detail=f"Pair {instance_id!r} inconnu")
        data[instance_id].pop("peer_token_hash", None)
        data[instance_id].pop("peer_token_outbound", None)
        data[instance_id]["trust"] = "unknown"
        data[instance_id]["revoked_at"] = datetime.now(timezone.utc).isoformat()
        _save_peers(data)
    return {"ok": True, "instance_id": instance_id, "trust": "unknown", "action": "token_revoked"}


# ── Lot 0 Phase 10 — Gestion des scopes inter-instances ──────────────────────

class ScopesUpdateRequest(BaseModel):
    allowed_scopes: List[str]


@router.get("/api/peers/{instance_id}/scopes", dependencies=[Depends(deps.verify_admin_token)])
async def get_peer_scopes(instance_id: str) -> Dict[str, Any]:
    """Retourne les scopes autorisés pour un pair, et la liste des scopes valides connus."""
    from src.runtime.peer_scopes import VALID_SCOPES
    with _PEER_LOCK:
        data = _load_peers()
    peer = data.get(instance_id)
    if not peer:
        raise HTTPException(status_code=404, detail=f"Pair {instance_id!r} inconnu")
    return {
        "instance_id": instance_id,
        "allowed_scopes": peer.get("allowed_scopes") or ["chat"],
        "valid_scopes": sorted(VALID_SCOPES),
    }


@router.put("/api/peers/{instance_id}/scopes", dependencies=[Depends(deps.verify_admin_token)])
async def update_peer_scopes(instance_id: str, req: ScopesUpdateRequest) -> Dict[str, Any]:
    """Met à jour les scopes autorisés pour un pair.

    Seuls les scopes présents dans VALID_SCOPES sont acceptés.
    La modification est auditée dans peer_audit.jsonl.
    """
    from src.runtime.peer_scopes import VALID_SCOPES
    from src.runtime.peer_protocol import write_audit_log

    invalid = [s for s in req.allowed_scopes if s not in VALID_SCOPES]
    if invalid:
        raise HTTPException(
            status_code=422,
            detail=f"Scopes inconnus : {invalid}. Valides : {sorted(VALID_SCOPES)}",
        )

    new_scopes = sorted(set(req.allowed_scopes))

    with _PEER_LOCK:
        data = _load_peers()
        if instance_id not in data:
            raise HTTPException(status_code=404, detail=f"Pair {instance_id!r} inconnu")
        old_scopes = data[instance_id].get("allowed_scopes") or ["chat"]
        data[instance_id]["allowed_scopes"] = new_scopes
        data[instance_id]["last_seen"] = datetime.now(timezone.utc).isoformat()
        _save_peers(data)

    write_audit_log(
        event="scope_updated",
        from_instance_id=instance_id,
        task_id="",
        scope=",".join(new_scopes),
        status="ok",
        detail=f"Scopes : {old_scopes} → {new_scopes}",
    )

    return {"ok": True, "instance_id": instance_id, "allowed_scopes": new_scopes}


# ── A3 brique 2 — Niveau de capacité accordé par pair ────────────────────────

class CapabilityUpdateRequest(BaseModel):
    level: str   # "chat" | "mission"


@router.get("/api/peers/{instance_id}/capability", dependencies=[Depends(deps.verify_admin_token)])
async def get_peer_capability(instance_id: str) -> Dict[str, Any]:
    """Retourne le niveau de capacité accordé à un pair (défaut `chat`)."""
    from src.runtime.peer_capability import LEVELS, normalize_level, describe_level
    with _PEER_LOCK:
        peer = _load_peers().get(instance_id)
    if peer is None:
        raise HTTPException(status_code=404, detail=f"Pair {instance_id!r} inconnu")
    level = normalize_level(peer.get("capability_level"))
    return {"instance_id": instance_id, "level": level,
            "label": describe_level(level), "valid_levels": sorted(LEVELS)}


@router.put("/api/peers/{instance_id}/capability", dependencies=[Depends(deps.verify_admin_token)])
async def update_peer_capability(instance_id: str, req: CapabilityUpdateRequest) -> Dict[str, Any]:
    """Accorde un niveau de capacité à un pair (`chat` sûr / `mission` complet).

    **Accordé explicitement par l'humain**, par pair. `mission` donne à ce pair
    le droit de faire exécuter une mission à pleine puissance (agent complet).
    Auditée.
    """
    from src.runtime.peer_capability import LEVELS, normalize_level
    from src.runtime.peer_protocol import write_audit_log

    level = (req.level or "").strip().lower()
    if level not in LEVELS:
        raise HTTPException(status_code=422, detail=f"Niveau inconnu : {req.level!r}. Valides : {sorted(LEVELS)}")

    # Passer en « mission » auto-accorde les scopes nécessaires pour confier ET
    # recevoir une mission async + ses livrables (sinon l'utilisateur devait
    # cocher « + tâches » à part — piège du double réglage).
    _MISSION_SCOPES = ["task.delegate", "artifact.share", "knowledge.query", "knowledge.share"]
    granted: List[str] = []
    with _PEER_LOCK:
        data = _load_peers()
        if instance_id not in data:
            raise HTTPException(status_code=404, detail=f"Pair {instance_id!r} inconnu")
        old = normalize_level(data[instance_id].get("capability_level"))
        data[instance_id]["capability_level"] = level
        if level == "mission":
            scopes = set(data[instance_id].get("allowed_scopes") or ["chat"])
            scopes.add("chat")
            for s in _MISSION_SCOPES:
                if s not in scopes:
                    scopes.add(s)
                    granted.append(s)
            data[instance_id]["allowed_scopes"] = sorted(scopes)
        data[instance_id]["last_seen"] = datetime.now(timezone.utc).isoformat()
        _save_peers(data)

    write_audit_log(
        event="capability_updated", from_instance_id=instance_id, task_id="",
        scope=level, status="ok",
        detail=f"Niveau : {old} → {level}" + (f" ; scopes auto-accordés : {granted}" if granted else ""),
    )
    return {"ok": True, "instance_id": instance_id, "level": level, "auto_granted_scopes": granted}


# ── Bloc A — Alias (nom personnalisé) accordé par pair ───────────────────────

class AliasUpdateRequest(BaseModel):
    alias: str


@router.put("/api/peers/{instance_id}/alias", dependencies=[Depends(deps.verify_admin_token)])
async def update_peer_alias(instance_id: str, req: AliasUpdateRequest) -> Dict[str, Any]:
    """Renomme un pair pour l'affichage (alias). Vide = retour au nom d'origine.

    N'altère aucun droit ni token — purement cosmétique. Borné à 60 caractères.
    Auditée.
    """
    from src.runtime.peer_protocol import write_audit_log

    alias = (req.alias or "").strip()[:60]
    with _PEER_LOCK:
        data = _load_peers()
        if instance_id not in data:
            raise HTTPException(status_code=404, detail=f"Pair {instance_id!r} inconnu")
        old = data[instance_id].get("alias") or ""
        if alias:
            data[instance_id]["alias"] = alias
        else:
            data[instance_id].pop("alias", None)
        _save_peers(data)

    write_audit_log(
        event="alias_updated", from_instance_id=instance_id, task_id="",
        scope="", status="ok",
        detail=f"Alias : {old!r} → {alias!r}",
    )
    return {"ok": True, "instance_id": instance_id, "alias": alias}


# ── Phase 8.2 — Réparation pare-feu assistée ─────────────────────────────────

@router.get("/api/instance/firewall-command", dependencies=[Depends(deps.verify_admin_token)])
async def get_firewall_command() -> Dict[str, Any]:
    """Retourne la commande netsh à exécuter pour ouvrir le port dans le pare-feu Windows.

    Read-only — ne modifie rien. L'utilisateur doit cliquer explicitement sur
    POST /api/instance/firewall-apply avec confirmed=true pour appliquer.
    """
    port = int(os.getenv("LUMENA_PORT", "8080"))
    rule_name = f"Lumena HTTP {port}"
    cmd = (
        f'netsh advfirewall firewall add rule name="{rule_name}" '
        f"protocol=TCP dir=in localport={port} action=allow"
    )
    return {
        "platform": platform.system(),
        "command": cmd,
        "port": port,
        "rule_name": rule_name,
        "description": f"Ouvre le port TCP {port} en entrée dans le pare-feu Windows.",
    }


class FirewallApplyRequest(BaseModel):
    confirmed: bool = False


@router.post("/api/instance/firewall-apply", dependencies=[Depends(deps.verify_admin_token)])
async def apply_firewall_rule(req: FirewallApplyRequest) -> Dict[str, Any]:
    """Applique la règle pare-feu Windows pour le port Lumena.

    EXIGE confirmed=true dans le corps — jamais automatique silencieux.
    Windows uniquement. Lance netsh via subprocess avec timeout 10 s.
    """
    if not req.confirmed:
        raise HTTPException(
            status_code=422,
            detail='Confirmation explicite requise : envoyez {"confirmed": true}.',
        )
    if platform.system() != "Windows":
        raise HTTPException(
            status_code=422,
            detail=f"Route Windows uniquement (plateforme actuelle : {platform.system()}).",
        )

    port = int(os.getenv("LUMENA_PORT", "8080"))
    rule_name = f"Lumena HTTP {port}"
    try:
        result = subprocess.run(
            [
                "netsh", "advfirewall", "firewall", "add", "rule",
                f"name={rule_name}", "protocol=TCP", "dir=in",
                f"localport={port}", "action=allow",
            ],
            capture_output=True, text=True, timeout=10,
        )
        return {
            "ok": result.returncode == 0,
            "command_output": result.stdout.strip(),
            "port": port,
            "rule_name": rule_name,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Erreur netsh : {exc}")

# ── Phase 8.11 — mDNS/Zeroconf ───────────────────────────────────────────────

@router.get("/api/mdns/status", dependencies=[Depends(deps.verify_admin_token)])
async def mdns_status() -> Dict[str, Any]:
    """Retourne l'état du feature mDNS : flag activé, lib disponible, service type."""
    from src.runtime.mdns_discovery import SERVICE_TYPE, is_mdns_available, is_mdns_enabled
    return {
        "enabled": is_mdns_enabled(),
        "available": is_mdns_available(),
        "service_type": SERVICE_TYPE,
        "note": (
            "Installez python-zeroconf et définissez LUMENA_MDNS_DISCOVERY=1 pour activer."
            if not is_mdns_available() else "mDNS opérationnel."
        ),
    }


class MdnsBrowseRequest(BaseModel):
    timeout: float = 5.0


@router.post("/api/mdns/browse", dependencies=[Depends(deps.verify_admin_token)])
async def mdns_browse(req: MdnsBrowseRequest) -> Dict[str, Any]:
    """Découvre les instances Lumena sur le LAN via mDNS (_lumena._tcp.local).

    Les instances trouvées sont retournées avec trust="unknown" et source="mdns".
    Elles sont également ajoutées au registre local comme candidats non-trusted.
    Le pairing par code reste obligatoire pour accorder la confiance.
    """
    import asyncio

    from src.runtime.mdns_discovery import browse_services, is_mdns_available
    from src.utils.paths import INSTANCE_ID

    if not is_mdns_available():
        return {
            "ok": False,
            "discovered": 0,
            "peers": [],
            "error": "mDNS non disponible. Installez python-zeroconf et activez LUMENA_MDNS_DISCOVERY=1.",
        }

    timeout = max(1.0, min(req.timeout, 30.0))

    # browse_services est bloquant (time.sleep) — on l'exécute dans un thread
    loop = asyncio.get_event_loop()
    peers = await loop.run_in_executor(
        None,
        lambda: browse_services(timeout=timeout, self_instance_id=INSTANCE_ID),
    )

    # Intègre les candidats dans le registre local (trust=unknown, non écrasé si déjà connu)
    # Anti-SSRF : on valide le host RFC1918 avant toute intégration.
    accepted: List[Dict[str, Any]] = []
    for p in peers:
        try:
            _validate_peer_host(p.get("host", ""))
            accepted.append(p)
        except HTTPException:
            pass  # host hors RFC1918 — ignoré silencieusement

    if accepted:
        with _PEER_LOCK:
            data = _load_peers()
            changed = False
            for p in accepted:
                iid = p["instance_id"]
                if iid and iid not in data:
                    data[iid] = {
                        "instance_id": iid,
                        "instance_name": p.get("instance_name", ""),
                        "host": p.get("host", ""),
                        "port": p.get("port", 8080),
                        "trust": "unknown",
                        "source": "mdns",
                        "role": p.get("role", "unknown"),
                        "version": p.get("version", ""),
                    }
                    changed = True
            if changed:
                _save_peers(data)

    return {
        "ok": True,
        "discovered": len(accepted),
        "peers": accepted,
    }


class MdnsAdvertiseRequest(BaseModel):
    stop: bool = False


@router.post("/api/mdns/advertise", dependencies=[Depends(deps.verify_admin_token)])
async def mdns_advertise(req: MdnsAdvertiseRequest) -> Dict[str, Any]:
    """Démarre ou arrête l'annonce mDNS de cette instance.

    L'annonce est non-persistante (redémarrage = réannonce nécessaire).
    Aucun secret n'est diffusé : seuls instance_id, instance_name, role, version,
    caps_hash et port sont dans les TXT records.
    """
    from src.runtime.mdns_discovery import (
        advertise_service,
        is_mdns_available,
        stop_service,
    )
    from src.utils.paths import INSTANCE_ID, INSTANCE_NAME, INSTANCE_ROLE

    if not is_mdns_available():
        return {
            "ok": False,
            "advertising": False,
            "error": "mDNS non disponible. Installez python-zeroconf et activez LUMENA_MDNS_DISCOVERY=1.",
        }

    # Registre d'état global (en mémoire, par processus)
    if req.stop:
        handle = getattr(mdns_advertise, "_handle", None)
        stop_service(handle)
        mdns_advertise._handle = None  # type: ignore[attr-defined]
        return {"ok": True, "advertising": False}

    # Stoppe une éventuelle annonce précédente avant d'en démarrer une nouvelle
    old_handle = getattr(mdns_advertise, "_handle", None)
    if old_handle is not None:
        stop_service(old_handle)

    from src import __version__
    port = int(os.getenv("LUMENA_PORT", "8080"))
    caps = _compute_capabilities()

    import asyncio
    loop = asyncio.get_event_loop()
    handle = await loop.run_in_executor(
        None,
        lambda: advertise_service(
            instance_id=INSTANCE_ID,
            instance_name=INSTANCE_NAME,
            role=INSTANCE_ROLE,
            version=__version__,
            capabilities=caps,
            port=port,
        ),
    )
    mdns_advertise._handle = handle  # type: ignore[attr-defined]

    return {
        "ok": handle is not None,
        "advertising": handle is not None,
        "instance_id": INSTANCE_ID,
        "port": port,
    }
# ── Lot D Phase 10 — Knowledge Query read-only ───────────────────────────────

_MAX_KNOWLEDGE_RESULTS = 20
_DEFAULT_KNOWLEDGE_RESULTS = 5
_MAX_SUMMARY_CHARS = 4000
_DEFAULT_SUMMARY_CHARS = 800


class KnowledgeQueryRequest(BaseModel):
    query: str
    from_instance_id: str
    from_user_id: str = "local:owner"
    actor_id: str = "lumena_agent"
    max_results: int = _DEFAULT_KNOWLEDGE_RESULTS
    max_summary_chars: int = _DEFAULT_SUMMARY_CHARS
    context: Dict[str, Any] = {}
    peer_message: Optional[Dict[str, Any]] = None


class KnowledgeQueryResponse(BaseModel):
    query: str
    answer_summary: str
    confidence: float          # 0.0 – 1.0 (moyenne des scores des résultats)
    tags: List[str]            # types de mémoire rencontrés (episodic, semantic…)
    source_count: int          # nombre de résultats trouvés
    redactions: List[str]      # clés retirées si besoin (audit trail)
    origin_instance_id: str
    created_at: str


class SharedKnowledgeCreateRequest(BaseModel):
    title: str
    summary: str
    origin_user_id: str = "local:owner"
    tags: List[str] = Field(default_factory=list)
    confidence: float = 0.8
    expires_at: Optional[str] = None
    source_refs: List[str] = Field(default_factory=list)


class SharedKnowledgeShareRequest(BaseModel):
    peer_id: str


class SharedKnowledgeImportRequest(BaseModel):
    force: bool = False


class SharedKnowledgeDismissRequest(BaseModel):
    reason: str = ""


class SharedKnowledgeProposalRequest(BaseModel):
    title: str
    summary: str
    from_instance_id: str
    origin_user_id: str = "local:owner"
    tags: List[str] = Field(default_factory=list)
    confidence: float = 0.8
    expires_at: Optional[str] = None
    source_refs: List[str] = Field(default_factory=list)


def _require_trusted_peer_scope(peer_id: str, scope: str) -> dict:
    with _PEER_LOCK:
        peers = _load_peers()
    peer = peers.get(peer_id)
    if not peer:
        raise HTTPException(status_code=404, detail=f"Pair {peer_id!r} inconnu.")
    trust = peer.get("trust", "unknown")
    if trust != "trusted":
        raise HTTPException(status_code=403, detail=f"Pair {peer_id!r} non trusted (trust={trust!r}).")
    from src.runtime.peer_scopes import validate_peer_scope
    try:
        validate_peer_scope(peer, scope)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    return peer


@router.get("/api/shared-knowledge", dependencies=[Depends(deps.verify_admin_token)])
async def list_shared_knowledge() -> Dict[str, Any]:
    """List local controlled shared-knowledge records for the admin UI."""
    from src.runtime.shared_knowledge import load_shared_knowledge, public_knowledge_view

    data = load_shared_knowledge()
    items = [public_knowledge_view(v) for v in data.values()]
    items.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return {"count": len(items), "items": items}


@router.get("/api/shared-knowledge/import-candidates", dependencies=[Depends(deps.verify_admin_token)])
async def list_shared_knowledge_import_candidates() -> Dict[str, Any]:
    """List peer-origin records with conservative import recommendations."""
    from src.runtime.shared_knowledge import list_import_candidates

    items = list_import_candidates()
    return {
        "count": len(items),
        "recommended": sum(1 for i in items if (i.get("assessment") or {}).get("import_recommended")),
        "items": items,
    }


@router.post("/api/shared-knowledge", dependencies=[Depends(deps.verify_admin_token)])
async def create_shared_knowledge(req: SharedKnowledgeCreateRequest) -> Dict[str, Any]:
    """Create a private knowledge proposal. It is not shared until /share is called."""
    from src.runtime.shared_knowledge import add_knowledge, create_knowledge_record, public_knowledge_view
    from src.utils.paths import INSTANCE_ID as _OWN_INSTANCE_ID

    try:
        record = create_knowledge_record(
            title=req.title,
            summary=req.summary,
            owner_instance_id=_OWN_INSTANCE_ID,
            origin_instance_id=_OWN_INSTANCE_ID,
            origin_user_id=req.origin_user_id,
            tags=req.tags,
            confidence=req.confidence,
            expires_at=req.expires_at,
            source_refs=req.source_refs,
            visibility="private",
        )
        add_knowledge(record)
        return public_knowledge_view(record)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/api/shared-knowledge/{knowledge_id}/share", dependencies=[Depends(deps.verify_admin_token)])
async def share_local_knowledge(
    knowledge_id: str,
    req: SharedKnowledgeShareRequest,
) -> Dict[str, Any]:
    """Share one local knowledge record with exactly one trusted peer."""
    from src.runtime.peer_protocol import write_audit_log
    from src.runtime.shared_knowledge import public_knowledge_view, share_knowledge

    _require_trusted_peer_scope(req.peer_id, "knowledge.share")
    try:
        record = share_knowledge(knowledge_id, req.peer_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Knowledge {knowledge_id!r} inconnue.")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    write_audit_log(
        event="knowledge_shared",
        from_instance_id=req.peer_id,
        task_id=knowledge_id,
        scope="knowledge.share",
        status="shared",
    )
    return public_knowledge_view(record)


@router.post("/api/shared-knowledge/{knowledge_id}/revoke", dependencies=[Depends(deps.verify_admin_token)])
async def revoke_local_knowledge(knowledge_id: str) -> Dict[str, Any]:
    """Revoke sharing for one knowledge record."""
    from src.runtime.shared_knowledge import public_knowledge_view, revoke_knowledge

    try:
        record = revoke_knowledge(knowledge_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Knowledge {knowledge_id!r} inconnue.")
    return public_knowledge_view(record)


@router.post("/api/shared-knowledge/{knowledge_id}/import", dependencies=[Depends(deps.verify_admin_token)])
async def import_shared_knowledge(
    knowledge_id: str,
    req: Optional[SharedKnowledgeImportRequest] = None,
) -> Dict[str, Any]:
    """Explicitly import one shared/proposed knowledge record into local owner memory."""
    from src.runtime.shared_knowledge import (
        assess_import_candidate,
        import_knowledge_to_memory,
        load_shared_knowledge,
        mark_imported,
        public_knowledge_view,
    )

    data = load_shared_knowledge()
    record = data.get(knowledge_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"Knowledge {knowledge_id!r} inconnue.")
    assessment = assess_import_candidate(record, existing_records=data)
    if not assessment["import_recommended"] and not (req and req.force):
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Import non recommande. Utilisez force=true pour confirmer explicitement.",
                "assessment": assessment,
            },
        )
    if deps.lumena is None:
        raise HTTPException(status_code=500, detail="Lumena non initialisée.")
    try:
        memory = deps.lumena.get_user_memory(user_id="local:owner")
        memory_id = import_knowledge_to_memory(memory, record)
        updated = mark_imported(knowledge_id, memory_id)
        return public_knowledge_view(updated)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/api/shared-knowledge/{knowledge_id}/dismiss", dependencies=[Depends(deps.verify_admin_token)])
async def dismiss_shared_knowledge(
    knowledge_id: str,
    req: SharedKnowledgeDismissRequest,
) -> Dict[str, Any]:
    """Dismiss a peer-origin knowledge proposal without importing it."""
    from src.runtime.shared_knowledge import dismiss_knowledge, public_knowledge_view

    try:
        record = dismiss_knowledge(knowledge_id, reason=req.reason)
        return public_knowledge_view(record)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Knowledge {knowledge_id!r} inconnue.")


@router.get("/api/peer/knowledge/shared")
async def peer_list_shared_knowledge(
    _auth_peer: dict = Depends(verify_peer_token),
) -> Dict[str, Any]:
    """Peer-token route: list records explicitly shared with this peer."""
    from src.runtime.shared_knowledge import list_knowledge_for_peer

    peer_id = _auth_peer["instance_id"]
    _require_trusted_peer_scope(peer_id, "knowledge.share")
    items = list_knowledge_for_peer(peer_id)
    return {"count": len(items), "items": items}


@router.post("/api/peer/knowledge/propose")
async def peer_propose_shared_knowledge(
    req: SharedKnowledgeProposalRequest,
    _auth_peer: dict = Depends(verify_peer_token),
) -> Dict[str, Any]:
    """Peer-token route: receive a knowledge proposal without importing it."""
    from src.runtime.peer_protocol import write_audit_log
    from src.runtime.shared_knowledge import add_knowledge, create_knowledge_record, public_knowledge_view
    from src.utils.paths import INSTANCE_ID as _OWN_INSTANCE_ID

    if _auth_peer["instance_id"] != req.from_instance_id:
        raise HTTPException(status_code=403, detail="Token pair ne correspond pas à from_instance_id.")
    _require_trusted_peer_scope(req.from_instance_id, "knowledge.share")

    try:
        record = create_knowledge_record(
            title=req.title,
            summary=req.summary,
            owner_instance_id=_OWN_INSTANCE_ID,
            origin_instance_id=req.from_instance_id,
            origin_user_id=req.origin_user_id,
            tags=req.tags,
            confidence=req.confidence,
            expires_at=req.expires_at,
            source_refs=req.source_refs,
            visibility="private",
        )
        add_knowledge(record)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    write_audit_log(
        event="knowledge_share_proposed",
        from_instance_id=req.from_instance_id,
        task_id=record["knowledge_id"],
        scope="knowledge.share",
        status="proposed",
    )
    return public_knowledge_view(record)


@router.post("/api/peer/knowledge/query", response_model=KnowledgeQueryResponse)
async def peer_knowledge_query(
    req: KnowledgeQueryRequest,
    _auth_peer: dict = Depends(verify_peer_token),
) -> KnowledgeQueryResponse:
    """Reçoit une requête knowledge query d'un pair Lumena trusted.

    Règles de sécurité :
    - Token lié à from_instance_id (anti-usurpation).
    - Trust exact = "trusted".
    - Scope knowledge.query dans allowed_scopes.
    - Isolation user_id stricte.
    - Jamais de documents bruts complets dans la réponse.
    - Jamais d'import mémoire automatique.
    """
    from src.runtime.peer_protocol import write_audit_log
    from src.utils.paths import INSTANCE_ID as _OWN_INSTANCE_ID

    _qid = f"kq-{uuid.uuid4().hex[:12]}"

    # 0. Liaison token ↔ from_instance_id
    if _auth_peer["instance_id"] != req.from_instance_id:
        write_audit_log(
            event="knowledge_query_refused",
            from_instance_id=_auth_peer["instance_id"],
            task_id=_qid,
            scope="knowledge.query",
            status="refused",
            detail=(
                f"Usurpation : token={_auth_peer['instance_id']!r}, "
                f"from={req.from_instance_id!r}"
            ),
        )
        raise HTTPException(
            status_code=403,
            detail=(
                f"Token présenté appartient à {_auth_peer['instance_id']!r}, "
                f"pas à {req.from_instance_id!r}."
            ),
        )

    # 1. Trust fail-closed
    with _PEER_LOCK:
        _peers_data = _load_peers()
    _peer = _peers_data.get(req.from_instance_id)
    _trust = _peer.get("trust", "unknown") if _peer else "unknown"

    if _trust != "trusted":
        _detail = (
            f"Instance {req.from_instance_id!r} est bloquée."
            if _trust == "blocked"
            else f"Instance {req.from_instance_id!r} n'est pas trusted (trust={_trust!r})."
        )
        write_audit_log(
            event="knowledge_query_refused",
            from_instance_id=req.from_instance_id,
            task_id=_qid, scope="knowledge.query",
            status="refused", detail=_detail,
        )
        raise HTTPException(status_code=403, detail=_detail)

    # 2. Scope knowledge.query obligatoire
    from src.runtime.peer_scopes import validate_peer_scope
    try:
        validate_peer_scope(_peer, "knowledge.query")
    except PermissionError as exc:
        write_audit_log(
            event="knowledge_query_refused",
            from_instance_id=req.from_instance_id,
            task_id=_qid, scope="knowledge.query",
            status="refused", detail=str(exc),
        )
        raise HTTPException(status_code=403, detail=str(exc))

    # 3. Rate limiting per peer/scope
    _enforce_peer_rate_limit(req.from_instance_id, "knowledge.query", _qid)

    # 4. Pas de secret dans la query
    from src.runtime.peer_messages import has_secret_pattern
    if has_secret_pattern(req.query):
        write_audit_log(
            event="knowledge_query_refused",
            from_instance_id=req.from_instance_id,
            task_id=_qid, scope="knowledge.query",
            status="refused", detail="Secret détecté dans la query",
        )
        raise HTTPException(
            status_code=422,
            detail="La query contient un pattern identifié comme secret. Retirez-le.",
        )

    # 5. Audit démarrage
    write_audit_log(
        event="knowledge_query_started",
        from_instance_id=req.from_instance_id,
        task_id=_qid, scope="knowledge.query", status="running",
    )

    # 6. Isolation user_id + recherche mémoire locale
    safe_max = max(1, min(_MAX_KNOWLEDGE_RESULTS, req.max_results))
    safe_chars = max(100, min(_MAX_SUMMARY_CHARS, req.max_summary_chars))

    answer_summary = ""
    confidence = 0.0
    tags: List[str] = []
    source_count = 0
    redactions: List[str] = []

    try:
        if deps.lumena is None:
            raise RuntimeError("Lumena non initialisée sur cette instance.")

        # Isolation user_id — toujours local:owner en V1.
        # Un pair distant ne peut pas choisir librement quel user_id local interroger,
        # même s'il est trusted. req.from_user_id est reçu pour logging uniquement.
        #
        # TODO V2 : ajouter un mapping explicite dans le peer registry :
        #   peer["allowed_user_id"] ou peer["allowed_profile_ids"]
        #   → seul un pair avec ce mapping peut accéder à une mémoire spécifique.
        #   Tant que ce mapping n'existe pas, on reste sur local:owner.
        _uid = "local:owner"

        _mem = deps.lumena.get_user_memory(user_id=_uid)
        memories = _mem.recall(req.query, limit=safe_max)
        source_count = len(memories)

        if memories:
            # Résumé contrôlé — jamais de contenu brut complet
            fragments: List[str] = []
            scores: List[float] = []
            seen_tags: set = set()

            for m in memories:
                # Tronquer chaque fragment individuellement
                snippet = m.content[:300].replace("\n", " ").strip()
                if len(m.content) > 300:
                    snippet += "…"
                fragments.append(snippet)
                scores.append(float(m.score) if m.score else 0.0)
                seen_tags.add(str(m.memory_type))

            confidence = round(sum(scores) / len(scores), 3) if scores else 0.0
            tags = sorted(seen_tags)

            # Assemblage résumé global tronqué à max_summary_chars
            raw_summary = " | ".join(fragments)
            if len(raw_summary) > safe_chars:
                raw_summary = raw_summary[:safe_chars - 1] + "…"
            answer_summary = raw_summary
        else:
            answer_summary = "Aucune connaissance pertinente trouvée sur ce sujet."
            confidence = 0.0
            tags = []

    except Exception as exc:
        write_audit_log(
            event="knowledge_query_failed",
            from_instance_id=req.from_instance_id,
            task_id=_qid, scope="knowledge.query",
            status="error", detail=str(exc),
        )
        raise HTTPException(status_code=500, detail=f"Erreur lors de la recherche : {exc}")

    write_audit_log(
        event="knowledge_query_completed",
        from_instance_id=req.from_instance_id,
        task_id=_qid, scope="knowledge.query",
        status="completed",
        detail=f"source_count={source_count} confidence={confidence}",
    )

    return KnowledgeQueryResponse(
        query=req.query,
        answer_summary=answer_summary,
        confidence=confidence,
        tags=tags,
        source_count=source_count,
        redactions=redactions,
        origin_instance_id=_OWN_INSTANCE_ID,
        created_at=datetime.now(timezone.utc).isoformat(),
    )


# ── Lot E1 Phase 10 — Tasks sync bounded ─────────────────────────────────────

_TASK_SYNC_MIN_TIMEOUT = 10
_TASK_SYNC_MAX_TIMEOUT = 300
_TASK_SYNC_DEFAULT_TIMEOUT = 120
_TASK_SYNC_MAX_RESULT_CHARS = 8000

# M2 — les MISSIONS async peuvent durer bien plus qu'une tâche sync (gros projet,
# longue analyse). Plafond séparé, réglable dans le panneau Configuration.
_MISSION_DEFAULT_MAX_TIMEOUT = 1800  # 30 min


def _get_mission_max_timeout() -> int:
    """Plafond de durée d'une mission async (s). Réglable via le panel config."""
    try:
        return max(_TASK_SYNC_MIN_TIMEOUT,
                   int(os.getenv("LUMENA_PEER_MISSION_MAX_TIMEOUT", str(_MISSION_DEFAULT_MAX_TIMEOUT))))
    except (ValueError, TypeError):
        return _MISSION_DEFAULT_MAX_TIMEOUT


class TaskSyncRequest(BaseModel):
    task_id: str
    from_instance_id: str
    from_user_id: str = "local:owner"
    actor_id: str = "lumena_agent"
    objective: str
    context: Dict[str, Any] = Field(default_factory=dict)
    allowed_tools: list = Field(default_factory=list)
    timeout_sec: int = _TASK_SYNC_DEFAULT_TIMEOUT
    expected_output: str = "summary"
    peer_message: Optional[Dict[str, Any]] = None


class TaskSyncResponse(BaseModel):
    task_id: str
    status: str          # completed | failed | timeout | refused
    result: str
    duration_ms: float
    origin_instance_id: str
    created_at: str


@router.post("/api/peer/tasks/run-sync", response_model=TaskSyncResponse)
async def peer_task_run_sync(
    req: TaskSyncRequest,
    _auth_peer: dict = Depends(verify_peer_token),
) -> TaskSyncResponse:
    """Reçoit une tâche bornée d'un pair Lumena trusted et l'exécute en synchrone.

    Règles :
    - Token lié à from_instance_id (anti-usurpation).
    - Trust exact = "trusted".
    - Scope task.delegate dans allowed_scopes.
    - Pas d'objective contenant un secret.
    - Timeout obligatoire [10, 300] secondes.
    - Résultat tronqué à _TASK_SYNC_MAX_RESULT_CHARS.
    - Pas de boucle inter-peer (la tâche est exécutée localement, pas redéleguée).
    """
    import time as _time
    from src.runtime.peer_protocol import write_audit_log
    from src.utils.paths import INSTANCE_ID as _OWN_INSTANCE_ID

    _created_at = datetime.now(timezone.utc).isoformat()
    _t0 = _time.monotonic()

    def _ms() -> float:
        return round((_time.monotonic() - _t0) * 1000, 1)

    def _refused(detail: str, status_code: int = 403) -> TaskSyncResponse:
        write_audit_log(
            event="task_sync_refused",
            from_instance_id=req.from_instance_id,
            task_id=req.task_id,
            scope="task.delegate",
            status="refused",
            detail=detail,
        )
        raise HTTPException(status_code=status_code, detail=detail)

    # 0. Liaison token ↔ from_instance_id
    if _auth_peer["instance_id"] != req.from_instance_id:
        _refused(
            f"Usurpation : token={_auth_peer['instance_id']!r}, "
            f"from={req.from_instance_id!r}."
        )

    # 1. Trust fail-closed
    with _PEER_LOCK:
        _peers_data = _load_peers()
    _peer = _peers_data.get(req.from_instance_id)
    _trust = _peer.get("trust", "unknown") if _peer else "unknown"

    if _trust != "trusted":
        _refused(
            f"Instance {req.from_instance_id!r} est bloquée."
            if _trust == "blocked"
            else f"Instance {req.from_instance_id!r} n'est pas trusted (trust={_trust!r})."
        )

    # 2. Scope task.delegate
    from src.runtime.peer_scopes import validate_peer_scope
    try:
        validate_peer_scope(_peer, "task.delegate")
    except PermissionError as exc:
        _refused(str(exc))

    # 3. Secrets dans tous les champs qui partent dans le prompt local
    # context["peer_message"] est exclu du contrôle secrets (contient des UUIDs hex32).
    from src.runtime.peer_messages import has_secret_pattern, detect_prompt_injection
    if has_secret_pattern(req.objective):
        _refused(
            "L'objective contient un pattern identifié comme secret. Retirez-le.",
            status_code=422,
        )
    if has_secret_pattern(req.expected_output):
        _refused(
            "expected_output contient un pattern identifié comme secret. Retirez-le.",
            status_code=422,
        )
    # A4 Couche 2 — refus fail-closed si tentative d'injection détectée.
    _inj = detect_prompt_injection(req.objective) or detect_prompt_injection(req.expected_output)
    if _inj:
        _refused(f"Demande refusée : tentative d'injection détectée ({_inj}).", status_code=422)
    _ctx_no_envelope = (
        {k: v for k, v in req.context.items() if k != "peer_message"}
        if isinstance(req.context, dict) else req.context
    )
    _ctx_serialized = json.dumps(_ctx_no_envelope, ensure_ascii=False)
    if has_secret_pattern(_ctx_serialized):
        _refused(
            "context contient un pattern identifié comme secret. Retirez-le.",
            status_code=422,
        )

    # 3b. Validation enveloppe peer_message (req.peer_message ET context["peer_message"])
    from src.runtime.peer_messages import (
        PeerMessage, validate_peer_message, sanitize_peer_message,
    )
    _envelopes_sync = []
    if req.peer_message:
        _envelopes_sync.append(req.peer_message)
    _ctx_pm_sync = req.context.get("peer_message") if isinstance(req.context, dict) else None
    if isinstance(_ctx_pm_sync, dict):
        _envelopes_sync.append(_ctx_pm_sync)
    for _pm_dict in _envelopes_sync:
        try:
            _incoming = PeerMessage.from_dict(_pm_dict)
            validate_peer_message(_incoming)
            sanitize_peer_message(_incoming)
        except ValueError as _env_err:
            _refused(
                f"Enveloppe peer_message invalide ou expirée : {_env_err}",
                status_code=422,
            )

    # 4. Rate limiting per peer/scope. Invalid payloads above do not consume quota.
    _enforce_peer_rate_limit(req.from_instance_id, "task.delegate", req.task_id)

    # 5. Timeout borné
    safe_timeout = max(
        _TASK_SYNC_MIN_TIMEOUT,
        min(_TASK_SYNC_MAX_TIMEOUT, int(req.timeout_sec)),
    )

    # 5b. P4 — Pair OCCUPÉ : si une mission async tourne déjà, répondre « busy »
    # vite et proprement au lieu de bloquer jusqu'au timeout (l'appelant croyait
    # à un échec/ReadTimeout). Mieux vaut un « réessaie » net qu'une attente morte.
    try:
        from src.runtime.peer_mission_worker import mission_load as _mission_load
        if int(_mission_load().get("running", 0)) > 0:
            return TaskSyncResponse(
                task_id=req.task_id,
                status="busy",
                result="Le pair est occupé par une autre mission en cours — réessaie dans un instant.",
                duration_ms=_ms(),
                origin_instance_id=_OWN_INSTANCE_ID,
                created_at=_created_at,
            )
    except Exception:
        pass

    # 6. Audit démarrage
    write_audit_log(
        event="task_sync_started",
        from_instance_id=req.from_instance_id,
        task_id=req.task_id,
        scope="task.delegate",
        status="running",
    )

    # 7. Exécution locale bornée — A4 Couche 1 : contenu pair encadré (donnée, pas ordre)
    from src.runtime.peer_messages import frame_external_request as _frame
    _allowed, _level = _peer_execution_allowed_tools(_peer)  # niveau pour le prompt + l'exécution
    _task_ctx = (
        f"[TÂCHE BORNÉE — DÉLÉGATION INTER-LUMENA]\n"
        f"Instance appelante : {req.from_instance_id}\n"
        f"{_capability_prompt_note(_level)}\n\n"
        + _frame(
            req.objective,
            expected_output=req.expected_output,
            context_json=json.dumps(req.context, ensure_ascii=False)[:500],
        )
        + "\n\nRéponds directement. Ne délègue pas à d'autres instances."
    )

    status = "failed"
    result = ""
    try:
        if deps.lumena is None:
            raise RuntimeError("Lumena non initialisée sur cette instance.")

        import asyncio as _asyncio
        # A3 — brique 2 : exécution bornée par le niveau (déjà résolu plus haut).
        response = await _asyncio.wait_for(
            deps.lumena.think_and_act_silent(
                _task_ctx,
                timeout=float(safe_timeout),
                allowed_tools=(list(_allowed) if _allowed is not None else None),
                allow_when_busy=True,
                allowed_tools_hard=(_allowed is not None),  # A4 — chat = filtre DUR
            ),
            timeout=float(safe_timeout) + 5.0,
        )
        result = (response or "").strip()
        if len(result) > _TASK_SYNC_MAX_RESULT_CHARS:
            result = result[:_TASK_SYNC_MAX_RESULT_CHARS - 1] + "…"
        status = "completed"

    except _asyncio.TimeoutError:
        status = "timeout"
        result = f"Tâche interrompue : timeout de {safe_timeout}s atteint."
        write_audit_log(
            event="task_sync_timeout",
            from_instance_id=req.from_instance_id,
            task_id=req.task_id,
            scope="task.delegate",
            status="timeout",
            detail=f"timeout={safe_timeout}s",
        )
    except Exception as exc:
        status = "failed"
        result = f"Erreur lors de l'exécution : {type(exc).__name__}"
        write_audit_log(
            event="task_sync_failed",
            from_instance_id=req.from_instance_id,
            task_id=req.task_id,
            scope="task.delegate",
            status="error",
            detail=str(exc),
        )

    if status == "completed":
        write_audit_log(
            event="task_sync_completed",
            from_instance_id=req.from_instance_id,
            task_id=req.task_id,
            scope="task.delegate",
            status="completed",
            detail=f"duration_ms={_ms()}",
        )

    return TaskSyncResponse(
        task_id=req.task_id,
        status=status,
        result=result,
        duration_ms=_ms(),
        origin_instance_id=_OWN_INSTANCE_ID,
        created_at=_created_at,
    )
# ── Lot E2 Phase 10 — Tasks async queue ──────────────────────────────────────

_ASYNC_TASK_MAX_AGE = 3600  # TTL 1h

_async_task_store: Dict[str, dict] = {}
_async_tasks_lock = threading.Lock()
_PEER_TASK_EVENTS_FILE = DATA_DIR / "peer_tasks" / "task_events.jsonl"
_peer_task_events_lock = threading.Lock()
_task_recovery_done = False
_task_recovery_lock = threading.Lock()


def _count_active_async_tasks(peer_id: str) -> int:
    """Count queued/running async tasks owned by a peer. Lock must be held."""
    return sum(
        1
        for entry in _async_task_store.values()
        if entry.get("from_instance_id") == peer_id
        and entry.get("status") in {"queued", "running"}
    )


def _count_active_async_tasks_for_all_locked() -> int:
    """Count all queued/running async tasks. _async_tasks_lock must be held."""
    return sum(
        1
        for entry in _async_task_store.values()
        if entry.get("status") in {"queued", "running"}
    )


def _enforce_peer_parallel_tasks(peer_id: str, task_id: str, active_count: int) -> None:
    allowed, retry_after = check_max_parallel_tasks(peer_id, active_count)
    if not allowed:
        _raise_peer_rate_limited(
            peer_id=peer_id,
            scope="task.delegate",
            task_id=task_id,
            retry_after=retry_after,
            detail="Too many active async tasks for this peer.",
        )


def _write_task_event(
    *,
    task_id: str,
    from_instance_id: str,
    event: str,
    status: str,
    detail: str = "",
    result: Optional[str] = None,
    origin_instance_id: Optional[str] = None,
) -> None:
    """Append one task event to data/peer_tasks/task_events.jsonl."""
    entry: Dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "task_id": task_id,
        "from_instance_id": from_instance_id,
        "scope": "task.delegate",
        "event": event,
        "status": status,
        "detail": detail,
    }
    if origin_instance_id is not None:
        entry["origin_instance_id"] = origin_instance_id
    if result is not None:
        entry["result"] = result[:_TASK_SYNC_MAX_RESULT_CHARS]
    line = json.dumps(entry, ensure_ascii=False)
    with _peer_task_events_lock:
        try:
            _PEER_TASK_EVENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(_PEER_TASK_EVENTS_FILE, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass


def _read_task_events(task_id: Optional[str] = None, limit: int = 500) -> List[dict]:
    try:
        if not _PEER_TASK_EVENTS_FILE.exists():
            return []
        lines = _PEER_TASK_EVENTS_FILE.read_text(encoding="utf-8").splitlines()
        events = []
        for line in lines[-max(1, limit):]:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except Exception:
                continue
            if task_id is None or event.get("task_id") == task_id:
                events.append(event)
        return events
    except Exception:
        return []


def _latest_task_events(limit: int = 10000) -> Dict[str, dict]:
    """Return the latest persisted event per task_id for admin UI summaries."""
    latest: Dict[str, dict] = {}
    for event in _read_task_events(task_id=None, limit=limit):
        tid = event.get("task_id")
        if tid:
            latest[tid] = event
    return latest


def _public_task_entry(task_id: str, entry: dict, latest_event: Optional[dict] = None) -> dict:
    """Sanitize one async task for the local admin UI.

    Internal asyncio.Task handles, raw context and full prompts are never exposed.
    """
    result = entry.get("result")
    if isinstance(result, str) and len(result) > 1200:
        result = result[:1199] + "…"
    latest_event = latest_event or {}
    return {
        "task_id": task_id,
        "from_instance_id": entry.get("from_instance_id", ""),
        "origin_instance_id": entry.get("origin_instance_id", ""),
        "status": entry.get("status", "unknown"),
        "result": result,
        "duration_ms": entry.get("duration_ms"),
        "created_at": entry.get("created_at") or latest_event.get("ts", ""),
        "latest_event": {
            "event": latest_event.get("event", ""),
            "status": latest_event.get("status", ""),
            "ts": latest_event.get("ts", ""),
            "detail": latest_event.get("detail", ""),
        },
    }


def _recover_interrupted_async_tasks_once() -> None:
    """Mark persisted queued/running tasks as interrupted after restart.

    Current in-memory tasks are ignored, so lazy recovery is safe while the
    process is running.
    """
    global _task_recovery_done
    with _task_recovery_lock:
        if _task_recovery_done:
            return
        _task_recovery_done = True

        events = _read_task_events(task_id=None, limit=10000)
        latest: Dict[str, dict] = {}
        for event in events:
            tid = event.get("task_id")
            if tid:
                latest[tid] = event

        with _async_tasks_lock:
            live_task_ids = set(_async_task_store)

        for tid, event in latest.items():
            if tid in live_task_ids:
                continue
            if event.get("status") in {"queued", "running"}:
                _write_task_event(
                    task_id=tid,
                    from_instance_id=event.get("from_instance_id", "unknown"),
                    event="task_async_interrupted",
                    status="interrupted",
                    detail="Task was queued/running before process restart.",
                    origin_instance_id=event.get("origin_instance_id"),
                )


def _cleanup_old_async_tasks() -> None:
    """Retire les entrées de plus d'une heure. Doit être appelé sous _async_tasks_lock.

    Annule aussi l'asyncio.Task interne si elle n'est pas encore terminée,
    pour éviter les tâches orphelines en background.
    """
    now = time.monotonic()
    expired = [tid for tid, v in _async_task_store.items()
               if now - v.get("_created_mono", 0) > _ASYNC_TASK_MAX_AGE]
    for tid in expired:
        _at = _async_task_store[tid].get("_asyncio_task")
        if _at and not _at.done():
            _at.cancel()
        del _async_task_store[tid]


class TaskSubmitRequest(BaseModel):
    task_id: str
    from_instance_id: str
    from_user_id: str = "local:owner"
    actor_id: str = "lumena_agent"
    objective: str
    context: Dict[str, Any] = Field(default_factory=dict)
    allowed_tools: list = Field(default_factory=list)
    timeout_sec: int = _TASK_SYNC_DEFAULT_TIMEOUT
    expected_output: str = "summary"
    peer_message: Optional[Dict[str, Any]] = None


class TaskSubmitResponse(BaseModel):
    task_id: str
    status: str          # "queued"
    origin_instance_id: str
    created_at: str


class AsyncTaskStatusResponse(BaseModel):
    task_id: str
    status: str          # queued | running | completed | failed | timeout | cancelled | refused
    result: Optional[str] = None
    duration_ms: Optional[float] = None
    origin_instance_id: str
    created_at: str


class PeerMaintenanceCleanupRequest(BaseModel):
    dry_run: bool = True
    keep_audit_lines: int = 5000
    keep_task_event_lines: int = 5000
    cleanup_memory_tasks: bool = True
    clear_terminal_task_events: bool = False
    cleanup_terminal_memory_tasks: bool = False


# ── Brique 4 — bundles d'artefacts produits par les missions (côté B) ─────────
_artifact_bundles: Dict[str, dict] = {}   # task_id -> {manifest, bundle, from_instance_id, created_mono}
_artifact_lock = threading.Lock()
_ARTIFACT_TTL = 3600  # 1h


def _store_mission_bundle(task_id: str, from_instance_id: str, artifact_paths: list) -> int:
    """Construit le manifeste + le bundle (ZIP/brut) des fichiers produits et l'indexe.

    Retourne le nombre de fichiers empaquetés (0 si rien). Jamais fatal.
    """
    try:
        if not artifact_paths:
            return 0
        from src.runtime.peer_artifacts import build_manifest, prepare_bundle, public_manifest
        from src.utils.paths import WORKSPACE_DIR, DATA_DIR
        manifest = build_manifest(artifact_paths, base_dir=WORKSPACE_DIR)
        if not manifest:
            return 0
        bundle = prepare_bundle(manifest, task_id=task_id, out_dir=DATA_DIR / "peer_artifacts" / task_id)
        if not bundle:
            return 0
        with _artifact_lock:
            # purge TTL paresseuse
            _now = time.monotonic()
            for _tid in [t for t, e in _artifact_bundles.items()
                         if _now - e.get("created_mono", 0) > _ARTIFACT_TTL]:
                _artifact_bundles.pop(_tid, None)
            _artifact_bundles[task_id] = {
                "manifest": public_manifest(manifest),
                "bundle": bundle,
                "from_instance_id": from_instance_id,
                "created_mono": _now,
            }
        return int(bundle.get("count", 0))
    except Exception:
        return 0


def _get_artifact_bundle(task_id: str) -> Optional[dict]:
    with _artifact_lock:
        entry = _artifact_bundles.get(task_id)
        return dict(entry) if entry else None


async def _execute_async_task(
    task_id: str,
    task_ctx: str,
    from_instance_id: str,
    actor_id: str,
    safe_timeout: int,
) -> None:
    """Exécute la tâche en background et met à jour _async_task_store."""
    import time as _time
    import asyncio as _asyncio
    from src.runtime.peer_protocol import write_audit_log

    from src.runtime.peer_mission_worker import mission_slot

    _origin_instance_id = None
    status = "failed"
    result = ""

    # M1 — file d'attente : la mission patiente (statut 'queued') jusqu'à obtenir
    # un créneau, puis passe 'running'. Évite de saturer le LLM/l'agent local.
    async with mission_slot():
        _t0 = _time.monotonic()
        with _async_tasks_lock:
            if task_id not in _async_task_store:
                return
            _async_task_store[task_id]["status"] = "running"
            _origin_instance_id = _async_task_store[task_id].get("origin_instance_id")

        _write_task_event(
            task_id=task_id,
            from_instance_id=from_instance_id,
            event="task_async_running",
            status="running",
            origin_instance_id=_origin_instance_id,
        )

        try:
            if deps.lumena is None:
                raise RuntimeError("Lumena non initialisée.")
            # A3 — brique 2 : exécution async bornée par le niveau de capacité du pair.
            with _PEER_LOCK:
                _peer_async = _load_peers().get(from_instance_id) or {}
            _allowed, _level = _peer_execution_allowed_tools(_peer_async)
            # A4 — préfixe la capacité RÉELLE (résolue à l'exécution) pour éviter la
            # contradiction « task.delegate » vs niveau `chat` → plus de boucle.
            task_ctx = f"{_capability_prompt_note(_level)}\n\n{task_ctx}"
            # Brique 4 — capture par **snapshot disque avant/après** (indépendant des
            # outils : marche pour write_file, parallel_tools, CodeAgent, MCP…).
            from src.runtime.peer_artifacts import snapshot_workspace, diff_workspace
            from src.utils.paths import WORKSPACE_DIR as _WS_DIR
            from src.tools.file_guardrails import WorkspaceFileGuardrails as _WFG
            _ws_before = snapshot_workspace(_WS_DIR)
            # Projet ÉPINGLÉ : tous les fichiers de la mission dans UN seul
            # `projet-…` (anti-scatter + anti-boucle de réécriture). Convention
            # date/projet conservée ; relâché automatiquement à la sortie.
            _refusals: list = []  # A4 — rempli si le pair (chat) a refusé une action
            with _WFG.pinned_project(f"mission-{task_id}"):
                response = await _asyncio.wait_for(
                    deps.lumena.think_and_act_silent(
                        task_ctx,
                        timeout=float(safe_timeout),
                        allowed_tools=(list(_allowed) if _allowed is not None else None),
                        allow_when_busy=True,
                        allowed_tools_hard=(_allowed is not None),  # A4 — chat = filtre DUR
                        refusals_out=_refusals,
                    ),
                    timeout=float(safe_timeout) + 5.0,
                )
            result = (response or "").strip()
            if len(result) > _TASK_SYNC_MAX_RESULT_CHARS:
                result = result[:_TASK_SYNC_MAX_RESULT_CHARS - 1] + "…"
            status = "completed"
            # Diff = fichiers produits par la mission → empaquetés pour le pair
            # demandeur (endpoints /artifact). Jamais fatal.
            _mission_artifacts = diff_workspace(_ws_before, snapshot_workspace(_WS_DIR))
            _nb_art = _store_mission_bundle(task_id, from_instance_id, _mission_artifacts)
            if _nb_art:
                result = f"{result}\n\n📦 {_nb_art} fichier(s) produit(s) — disponibles au transfert."
            elif _allowed is not None:
                # Pair niveau `chat` (lecture seule) SANS aucun livrable → mission NON
                # aboutie. Déterministe : on marque « refused » (jamais un faux succès),
                # que le pair ait tenté un outil (filtre A4 → `_refusals`) ou décliné
                # poliment dès le prompt. On conserve sa réponse (lecture utile éventuelle).
                status = "refused"
                if _refusals:
                    _why = (
                        "⛔ Ce pair (niveau « chat », lecture seule) a tenté d'agir mais a "
                        "été bloqué : aucune écriture/action n'est permise à ce niveau."
                    )
                else:
                    _why = (
                        "⛔ Ce pair est en niveau « chat » (lecture seule) : il ne peut rien "
                        "écrire, créer ni envoyer — aucun livrable produit."
                    )
                _why += (
                    " Pour l'autoriser, l'humain doit le passer en niveau « mission » "
                    "(panneau Pairs) ; une IA ne peut pas changer ce réglage."
                )
                result = f"{_why}\n\n{result}".strip() if result else _why
        except _asyncio.TimeoutError:
            status = "timeout"
            result = f"Tâche interrompue : timeout de {safe_timeout}s atteint."
        except _asyncio.CancelledError:
            status = "cancelled"
            result = "Tâche annulée."
        except Exception as exc:
            status = "failed"
            result = f"Erreur : {type(exc).__name__}"

    # ── Créneau libéré : journalisation + store (hors file d'attente) ──
    duration_ms = round((_time.monotonic() - _t0) * 1000, 1)

    write_audit_log(
        event=f"task_async_{status}",
        from_instance_id=from_instance_id,
        task_id=task_id,
        scope="task.delegate",
        status=status,
        detail=f"duration_ms={duration_ms}",
    )
    _write_task_event(
        task_id=task_id,
        from_instance_id=from_instance_id,
        event=f"task_async_{status}",
        status=status,
        detail=f"duration_ms={duration_ms}",
        result=result,
        origin_instance_id=_origin_instance_id,
    )

    with _async_tasks_lock:
        entry = _async_task_store.get(task_id)
        if entry and entry.get("status") != "cancelled":
            entry["status"] = status
            entry["result"] = result
            entry["duration_ms"] = duration_ms


@router.post("/api/peer/tasks/submit", response_model=TaskSubmitResponse)
async def peer_task_submit(
    req: TaskSubmitRequest,
    _auth_peer: dict = Depends(verify_peer_token),
) -> TaskSubmitResponse:
    """Accepte une tâche async d'un pair trusted, la démarre en background, retourne task_id."""
    from src.runtime.peer_protocol import write_audit_log
    from src.utils.paths import INSTANCE_ID as _OWN_INSTANCE_ID

    _created_at = datetime.now(timezone.utc).isoformat()

    def _refused(detail: str, status_code: int = 403) -> None:
        write_audit_log(
            event="task_async_refused",
            from_instance_id=req.from_instance_id,
            task_id=req.task_id,
            scope="task.delegate",
            status="refused",
            detail=detail,
        )
        raise HTTPException(status_code=status_code, detail=detail)

    # 0. Liaison token ↔ from_instance_id
    if _auth_peer["instance_id"] != req.from_instance_id:
        _refused(
            f"Usurpation : token={_auth_peer['instance_id']!r}, "
            f"from={req.from_instance_id!r}."
        )

    # 1. Trust fail-closed
    with _PEER_LOCK:
        _peers_data = _load_peers()
    _peer = _peers_data.get(req.from_instance_id)
    _trust = _peer.get("trust", "unknown") if _peer else "unknown"

    if _trust != "trusted":
        _refused(
            f"Instance {req.from_instance_id!r} est bloquée."
            if _trust == "blocked"
            else f"Instance {req.from_instance_id!r} n'est pas trusted (trust={_trust!r})."
        )

    # 2. Scope task.delegate
    from src.runtime.peer_scopes import validate_peer_scope
    try:
        validate_peer_scope(_peer, "task.delegate")
    except PermissionError as exc:
        _refused(str(exc))

    # 3. Secrets dans les champs qui partent dans le prompt local
    # context["peer_message"] est exclu du contrôle secrets : il contient des UUIDs
    # hex32 qui déclencheraient des faux positifs. Il est validé séparément au 3b.
    from src.runtime.peer_messages import has_secret_pattern, detect_prompt_injection
    if has_secret_pattern(req.objective):
        _refused("L'objective contient un pattern identifié comme secret. Retirez-le.", status_code=422)
    if has_secret_pattern(req.expected_output):
        _refused("expected_output contient un pattern identifié comme secret. Retirez-le.", status_code=422)
    # A4 Couche 2 — refus fail-closed si tentative d'injection détectée.
    _inj = detect_prompt_injection(req.objective) or detect_prompt_injection(req.expected_output)
    if _inj:
        _refused(f"Demande refusée : tentative d'injection détectée ({_inj}).", status_code=422)
    _ctx_no_envelope = (
        {k: v for k, v in req.context.items() if k != "peer_message"}
        if isinstance(req.context, dict) else req.context
    )
    _ctx_ser = json.dumps(_ctx_no_envelope, ensure_ascii=False)
    if has_secret_pattern(_ctx_ser):
        _refused("context contient un pattern identifié comme secret. Retirez-le.", status_code=422)

    # 3b. Validation enveloppe peer_message (req.peer_message ET context["peer_message"])
    # Le tool submit_peer_task envoie l'enveloppe dans context["peer_message"] ;
    # les deux chemins doivent donc être vérifiés pour garantir TTL/hop_count.
    from src.runtime.peer_messages import (
        PeerMessage as _PeerMessage,
        validate_peer_message as _validate_pm,
        sanitize_peer_message as _sanitize_pm,
    )
    _envelopes_to_check = []
    if req.peer_message:
        _envelopes_to_check.append(req.peer_message)
    _ctx_pm = req.context.get("peer_message") if isinstance(req.context, dict) else None
    if isinstance(_ctx_pm, dict):
        _envelopes_to_check.append(_ctx_pm)
    for _pm_dict in _envelopes_to_check:
        try:
            _incoming = _PeerMessage.from_dict(_pm_dict)
            _validate_pm(_incoming)
            _sanitize_pm(_incoming)
        except ValueError as _env_err:
            _refused(f"Enveloppe peer_message invalide ou expirée : {_env_err}", status_code=422)

    # 4. Rate limiting per peer/scope. Invalid payloads above do not consume quota.
    _enforce_peer_rate_limit(req.from_instance_id, "task.delegate", req.task_id)

    # 5. Timeout borné — M2 : les missions async ont un plafond élargi (réglable).
    safe_timeout = max(
        _TASK_SYNC_MIN_TIMEOUT,
        min(_get_mission_max_timeout(), int(req.timeout_sec)),
    )

    # 6. Contexte prompt local — A4 Couche 1 : contenu pair encadré (donnée, pas ordre)
    from src.runtime.peer_messages import frame_external_request as _frame
    # NB : la CAPACITÉ réelle (chat/mission) est préfixée à l'exécution dans
    # _execute_async_task (résolue au bon moment) — pas de « Scope » trompeur ici.
    _task_ctx = (
        f"[TÂCHE ASYNC — DÉLÉGATION INTER-LUMENA]\n"
        f"Instance appelante : {req.from_instance_id}\n\n"
        + _frame(
            req.objective,
            expected_output=req.expected_output,
            context_json=json.dumps(req.context, ensure_ascii=False)[:500],
        )
        + "\n\nRéponds directement. Ne délègue pas à d'autres instances."
    )

    # 7. Enregistrement queued + collision check + nettoyage TTL
    with _async_tasks_lock:
        if req.task_id in _async_task_store:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Tâche {req.task_id!r} existe déjà dans la queue. "
                    "Utilisez un task_id unique pour chaque soumission."
                ),
            )
        _cleanup_old_async_tasks()
        _enforce_peer_parallel_tasks(
            req.from_instance_id,
            req.task_id,
            _count_active_async_tasks(req.from_instance_id),
        )
        _async_task_store[req.task_id] = {
            "status": "queued",
            "result": None,
            "duration_ms": None,
            "from_instance_id": req.from_instance_id,
            "origin_instance_id": _OWN_INSTANCE_ID,
            "created_at": _created_at,
            "_created_mono": time.monotonic(),
            "_asyncio_task": None,
        }

    write_audit_log(
        event="task_async_queued",
        from_instance_id=req.from_instance_id,
        task_id=req.task_id,
        scope="task.delegate",
        status="queued",
    )
    _write_task_event(
        task_id=req.task_id,
        from_instance_id=req.from_instance_id,
        event="task_async_queued",
        status="queued",
        origin_instance_id=_OWN_INSTANCE_ID,
    )

    # 8. Lancement background
    _bg_task = asyncio.create_task(
        _execute_async_task(
            req.task_id, _task_ctx,
            req.from_instance_id, req.actor_id,
            safe_timeout,
        )
    )
    with _async_tasks_lock:
        if req.task_id in _async_task_store:
            _async_task_store[req.task_id]["_asyncio_task"] = _bg_task

    return TaskSubmitResponse(
        task_id=req.task_id,
        status="queued",
        origin_instance_id=_OWN_INSTANCE_ID,
        created_at=_created_at,
    )


@router.get("/api/peer/local-tasks", dependencies=[Depends(deps.verify_admin_token)])
async def list_local_peer_tasks(limit: int = 50) -> Dict[str, Any]:
    """List local async peer tasks for the admin UI.

    This is intentionally admin-only and read-only. It exposes operational state,
    not peer tokens, raw prompts or asyncio internals.
    """
    _recover_interrupted_async_tasks_once()
    safe_limit = max(1, min(200, int(limit)))
    latest_by_task = _latest_task_events()

    with _async_tasks_lock:
        items = [
            _public_task_entry(task_id, entry, latest_by_task.get(task_id))
            for task_id, entry in _async_task_store.items()
        ]

    live_ids = {item["task_id"] for item in items}
    for task_id, latest in latest_by_task.items():
        if task_id in live_ids:
            continue
        items.append(
            _public_task_entry(
                task_id,
                {
                    "from_instance_id": latest.get("from_instance_id", ""),
                    "origin_instance_id": latest.get("origin_instance_id", ""),
                    "status": latest.get("status", "unknown"),
                    "result": latest.get("result"),
                    "duration_ms": None,
                    "created_at": latest.get("ts", ""),
                },
                latest,
            )
        )

    items.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    return {"count": len(items), "items": items[:safe_limit]}


@router.post("/api/peer/maintenance/cleanup", dependencies=[Depends(deps.verify_admin_token)])
async def cleanup_peer_runtime(req: PeerMaintenanceCleanupRequest) -> Dict[str, Any]:
    """Controlled retention cleanup for peer audit/task event files.

    Defaults to dry-run. This endpoint never deletes peer registry, shared
    knowledge or tokens.
    """
    from src.runtime.peer_protocol import PEER_AUDIT_LOG

    audit = _trim_jsonl_file(PEER_AUDIT_LOG, req.keep_audit_lines, dry_run=req.dry_run)
    events = _trim_task_events_file(
        _PEER_TASK_EVENTS_FILE,
        req.keep_task_event_lines,
        dry_run=req.dry_run,
        drop_terminal=req.clear_terminal_task_events,
    )

    memory_removed = 0
    if req.cleanup_memory_tasks and not req.dry_run:
        with _async_tasks_lock:
            before = len(_async_task_store)
            if req.cleanup_terminal_memory_tasks:
                terminal = {"completed", "failed", "timeout", "cancelled", "interrupted"}
                for task_id in [
                    tid for tid, entry in _async_task_store.items()
                    if entry.get("status") in terminal
                ]:
                    _async_task_store.pop(task_id, None)
            _cleanup_old_async_tasks()
            memory_removed = max(0, before - len(_async_task_store))

    return {
        "ok": True,
        "dry_run": req.dry_run,
        "audit": audit,
        "task_events": events,
        "memory_tasks_removed": memory_removed,
        "note": "Peer registry, shared knowledge and tokens are not touched.",
    }


@router.get("/api/peer/tasks/{task_id}/events")
async def peer_task_events(
    task_id: str,
    _auth_peer: dict = Depends(verify_peer_token),
) -> Dict[str, Any]:
    """Return the persisted event stream for one async peer task."""
    _recover_interrupted_async_tasks_once()
    events = _read_task_events(task_id=task_id, limit=10000)
    if not events:
        raise HTTPException(status_code=404, detail=f"Tâche {task_id!r} inconnue.")

    owner = events[0].get("from_instance_id")
    if owner != _auth_peer["instance_id"]:
        raise HTTPException(status_code=403, detail="Accès refusé : cette tâche ne vous appartient pas.")

    return {"task_id": task_id, "count": len(events), "events": events}


@router.get("/api/peer/tasks/{task_id}/status")
async def peer_task_status(
    task_id: str,
    _auth_peer: dict = Depends(verify_peer_token),
) -> AsyncTaskStatusResponse:
    """Retourne le statut d'une tâche async soumise par ce pair."""
    _recover_interrupted_async_tasks_once()
    with _async_tasks_lock:
        entry = _async_task_store.get(task_id)

    if entry is None:
        events = _read_task_events(task_id=task_id, limit=10000)
        if events:
            latest = events[-1]
            if latest.get("from_instance_id") != _auth_peer["instance_id"]:
                raise HTTPException(status_code=403, detail="Accès refusé : cette tâche ne vous appartient pas.")
            if latest.get("status") == "interrupted":
                return AsyncTaskStatusResponse(
                    task_id=task_id,
                    status="interrupted",
                    result=latest.get("result") or "Tâche interrompue par redémarrage.",
                    duration_ms=None,
                    origin_instance_id=latest.get("origin_instance_id", ""),
                    created_at=latest.get("ts", datetime.now(timezone.utc).isoformat()),
                )
        raise HTTPException(status_code=404, detail=f"Tâche {task_id!r} inconnue.")

    if entry.get("from_instance_id") != _auth_peer["instance_id"]:
        raise HTTPException(status_code=403, detail="Accès refusé : cette tâche ne vous appartient pas.")

    return AsyncTaskStatusResponse(
        task_id=task_id,
        status=entry["status"],
        result=entry.get("result"),
        duration_ms=entry.get("duration_ms"),
        origin_instance_id=entry["origin_instance_id"],
        created_at=entry["created_at"],
    )


# ── Brique 4 — Service des artefacts produits par une mission (côté B) ─────────

def _check_artifact_owner(entry: dict, peer_id: str) -> None:
    """Seul le pair qui a soumis la tâche peut récupérer ses artefacts."""
    if entry.get("from_instance_id") != peer_id:
        raise HTTPException(status_code=403, detail="Accès refusé : ces artefacts ne vous appartiennent pas.")


@router.get("/api/peer/artifact/{task_id}/manifest")
async def get_peer_artifact_manifest(
    task_id: str,
    _auth_peer: dict = Depends(verify_peer_token),
) -> Dict[str, Any]:
    """Manifeste des fichiers produits par une mission (signé A2, propriété vérifiée)."""
    entry = _get_artifact_bundle(task_id)
    if not entry:
        return {"task_id": task_id, "available": False, "manifest": [], "count": 0}
    _check_artifact_owner(entry, _auth_peer["instance_id"])
    b = entry["bundle"]
    return {
        "task_id": task_id, "available": True,
        "manifest": entry["manifest"],
        "kind": b["kind"], "filename": b["filename"],
        "sha256": b["sha256"], "count": b["count"],
    }


@router.get("/api/peer/artifact/{task_id}/file")
async def get_peer_artifact_file(
    task_id: str,
    _auth_peer: dict = Depends(verify_peer_token),
):
    """Bytes du bundle (ZIP ou fichier brut) — signé A2, propriété vérifiée."""
    from fastapi.responses import FileResponse
    entry = _get_artifact_bundle(task_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Aucun artefact pour cette tâche.")
    _check_artifact_owner(entry, _auth_peer["instance_id"])
    b = entry["bundle"]
    path = b.get("path", "")
    if not path or not os.path.isfile(path):
        raise HTTPException(status_code=410, detail="Artefact expiré ou indisponible.")
    return FileResponse(path, filename=b["filename"], media_type="application/octet-stream")


@router.delete("/api/peer/tasks/{task_id}")
async def peer_task_cancel(
    task_id: str,
    _auth_peer: dict = Depends(verify_peer_token),
) -> Dict[str, Any]:
    """Annule une tâche async en cours soumise par ce pair."""
    with _async_tasks_lock:
        entry = _async_task_store.get(task_id)

    if entry is None:
        raise HTTPException(status_code=404, detail=f"Tâche {task_id!r} inconnue.")

    if entry.get("from_instance_id") != _auth_peer["instance_id"]:
        raise HTTPException(status_code=403, detail="Accès refusé : cette tâche ne vous appartient pas.")

    if entry["status"] in ("completed", "failed", "timeout", "cancelled"):
        return {"ok": True, "task_id": task_id, "status": entry["status"], "note": "Déjà terminée."}

    _at = entry.get("_asyncio_task")
    if _at and not _at.done():
        _at.cancel()

    with _async_tasks_lock:
        if task_id in _async_task_store:
            _async_task_store[task_id]["status"] = "cancelled"
            _async_task_store[task_id]["result"] = "Tâche annulée par le pair demandeur."

    from src.runtime.peer_protocol import write_audit_log
    write_audit_log(
        event="task_async_cancelled",
        from_instance_id=entry["from_instance_id"],
        task_id=task_id,
        scope="task.delegate",
        status="cancelled",
    )
    _write_task_event(
        task_id=task_id,
        from_instance_id=entry["from_instance_id"],
        event="task_async_cancelled",
        status="cancelled",
        detail="Cancelled by requester.",
        result="Tâche annulée par le pair demandeur.",
        origin_instance_id=entry.get("origin_instance_id"),
    )

    return {"ok": True, "task_id": task_id, "status": "cancelled"}
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
