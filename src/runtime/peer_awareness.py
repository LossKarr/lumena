"""Lot A Phase 10 — Peer Awareness pour le contexte agent Lumena.

Lit le registre de pairs, filtre les pairs trusted utilisables, et produit
un bloc de contexte court à injecter dans le prompt du chat.

Activé via LUMENA_PEER_AWARENESS=1 (défaut : 0 — désactivé).

Deux tokens distincts par pair :
  peer_token_hash    → prouve qu'on peut VÉRIFIER les appels entrants de ce pair.
  peer_token_outbound → token brut qu'on utilise pour APPELER ce pair.

Le snapshot expose deux booléens sans jamais exposer de valeur brute :
  has_inbound_token_hash : bool — le pair peut nous appeler et on peut le vérifier.
  can_call_peer          : bool — on possède le token pour appeler ce pair.

Le contexte agent ne mentionne la délégation possible que si can_call_peer=True
ET que le scope demandé figure dans allowed_scopes.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Optional

from src.utils.paths import DATA_DIR

_PEER_REGISTRY_FILE = DATA_DIR / "peer_registry.json"
_MAX_CONTEXT_CHARS = 1000


def resolve_peer_identifier(peers: dict, identifier: str) -> Optional[str]:
    """Résout un identifiant de pair **flexible** vers son instance_id (UUID).

    L'agent vise souvent un pair par `host:port` (ce qu'affiche le contexte),
    alors que le registre est indexé par UUID. On accepte donc :
      1. UUID exact (clé du registre),
      2. `host:port`,
      3. `host` seul,
      4. `instance_name` (insensible à la casse).
    Retourne l'UUID, ou None si introuvable.
    """
    if not identifier or not isinstance(peers, dict):
        return None
    ident = str(identifier).strip()
    if not ident:
        return None
    # 1. UUID exact
    if ident in peers:
        return ident
    # 2/3. host[:port]
    host, port = ident, None
    if ":" in ident:
        h, _, p = ident.rpartition(":")
        host = h.strip()
        try:
            port = int(p)
        except (ValueError, TypeError):
            port = None
    for pid, peer in peers.items():
        ph = str((peer or {}).get("host", "")).strip()
        if ph and ph == host:
            if port is None:
                return pid
            try:
                if int((peer or {}).get("port") or 0) == port:
                    return pid
            except (ValueError, TypeError):
                continue
    # 4. instance_name
    low = ident.lower()
    for pid, peer in peers.items():
        if str((peer or {}).get("instance_name", "")).strip().lower() == low:
            return pid
    # 5. Identifiant « brouillon » (le LLM colle parfois nom + adresse, ex.
    #    « Lumena-192.168.1.57:8081 »). On accepte UNIQUEMENT si le host:port
    #    EXACT (ou l'UUID) d'un pair est CONTENU dans l'identifiant. On NE matche
    #    PAS le host seul → un port erroné explicite (« 192.168.1.57:9999 ») reste
    #    « inconnu » (pas de faux positif).
    for pid, peer in peers.items():
        ph = str((peer or {}).get("host", "")).strip()
        pp = str((peer or {}).get("port") or "").strip()
        if ph and pp and f"{ph}:{pp}" in ident:
            return pid
    for pid in peers:
        if pid and pid in ident:
            return pid
    return None


def _is_peer_awareness_enabled() -> bool:
    # Kill-switch SOFT : le halt veto la conscience réseau (plus de nouvelles actions).
    # OR-fallback : le MAÎTRE (LUMENA_PEER_ENABLED) allume aussi la conscience.
    try:
        from src.runtime.peer_network_autonomy import is_peer_halt_enabled, is_peer_master_enabled
        if is_peer_halt_enabled():
            return False
        if is_peer_master_enabled():
            return True
    except Exception:
        pass
    return os.getenv("LUMENA_PEER_AWARENESS", "0").strip() == "1"


def _load_peers() -> dict:
    try:
        if _PEER_REGISTRY_FILE.exists():
            return json.loads(_PEER_REGISTRY_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def get_peer_awareness_snapshot(user_id: Optional[str] = None) -> dict:
    """Retourne un snapshot des pairs trusted connus.

    Filtre : trust=trusted ET (peer_token_hash OU peer_token_outbound) présent.
    Les pairs unknown, blocked, ou sans aucun token sont ignorés.

    Champs retournés par pair (aucun secret) :
      instance_id, instance_name, host, port, capabilities, allowed_scopes,
      last_seen, has_inbound_token_hash, can_call_peer.

    has_inbound_token_hash : on peut vérifier leurs appels entrants.
    can_call_peer          : on a un token sortant pour les appeler.
    """
    if not _is_peer_awareness_enabled():
        return {"enabled": False, "peers": []}

    data = _load_peers()
    result = []

    for peer in data.values():
        if peer.get("trust") != "trusted":
            continue

        has_inbound = bool(peer.get("peer_token_hash"))
        can_call = bool(peer.get("peer_token_outbound"))

        # Ignorer les pairs trusted sans aucun token (jumelage vide)
        if not has_inbound and not can_call:
            continue

        result.append({
            "instance_id": peer.get("instance_id", ""),
            "instance_name": peer.get("instance_name", ""),
            "host": peer.get("host", ""),
            "port": peer.get("port", 0),
            "capabilities": list(peer.get("capabilities") or []),
            "allowed_scopes": list(peer.get("allowed_scopes") or []),
            "last_seen": peer.get("last_seen", ""),
            "has_inbound_token_hash": has_inbound,
            "can_call_peer": can_call,
        })

    return {"enabled": True, "peers": result}


def _fmt_last_seen(iso: str) -> str:
    """Convertit une date ISO en durée relative lisible. Silencieux si invalide."""
    try:
        if not iso:
            return ""
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - dt
        mins = int(delta.total_seconds() / 60)
        if mins < 1:
            return ", vue à l'instant"
        if mins < 60:
            return f", vue il y a {mins} min"
        if mins < 1440:
            return f", vue il y a {mins // 60} h"
        return f", vue il y a {mins // 1440} j"
    except Exception:
        return ""


def build_peer_awareness_context(user_id: Optional[str] = None) -> str:
    """Construit le bloc de contexte peer à injecter dans le prompt agent.

    Retourne une chaîne vide si :
    - LUMENA_PEER_AWARENESS != "1"
    - aucun pair trusted avec au moins un token

    Logique par pair :
    - can_call_peer=False → "token sortant manquant, rejumelage requis"
    - can_call_peer=True + allowed_scopes=[] → "connecté mais aucun scope utilisable"
    - can_call_peer=True + scopes → liste les scopes disponibles

    Le bloc est tronqué à 1000 caractères.
    """
    snapshot = get_peer_awareness_snapshot(user_id)
    if not snapshot["enabled"] or not snapshot["peers"]:
        return ""

    peers = snapshot["peers"]
    n = len(peers)
    lines = [
        f"\n## Réseau Lumena :",
        f"- {n} instance{'s' if n > 1 else ''} Lumena trusted détectée{'s' if n > 1 else ''} sur ce réseau.",
    ]

    callable_count = 0
    for p in peers:
        name = p["instance_name"] or p["instance_id"][:8]
        addr = f"{p['host']}:{p['port']}"
        caps = ", ".join(p["capabilities"]) if p["capabilities"] else "non déclarées"
        last = _fmt_last_seen(p["last_seen"])

        if not p["can_call_peer"]:
            # On peut recevoir de ce pair mais pas l'appeler
            lines.append(
                f"- {name} — {addr} — capacités : {caps} — "
                f"token sortant manquant (rejumelage requis){last}."
            )
        elif not p["allowed_scopes"]:
            # Joignable mais aucun scope activé — pas délégable
            lines.append(
                f"- {name} — {addr} — capacités : {caps} — "
                f"joignable mais aucun scope utilisable{last}."
            )
        else:
            # Joignable ET scopes actifs — délégation possible
            callable_count += 1
            scopes = ", ".join(sorted(p["allowed_scopes"]))
            lines.append(
                f"- {name} — {addr} — capacités : {caps} — "
                f"scopes disponibles : {scopes}{last}."
            )

    if callable_count > 0:
        lines += [
            "- Délégation inter-instance disponible vers les pairs joignables.",
            "- Si l'utilisateur demande de parler à l'autre Lumena, de lui demander, de faire vérifier, de répartir ou de déléguer : utilise d'abord `peer_team_request`, puis seulement si nécessaire `orchestrate_peer_request`, `delegate_to_peer`, `run_peer_task_sync` ou `query_peer_knowledge`.",
            "- Mission produisant un FICHIER/livrable (doc, script, rapport) confiée à un pair → `submit_peer_task` (async, livrables dans `recu-de-<pair>/`), PAS `peer_team_request` ; ne reconstruis pas le fichier en local.",
            "- N'utilise jamais `http_request`, `web_fetch`, `browser_navigate` ou `run_command/curl` vers une Lumena connue : passe par le protocole peer.",
            "- Réponds comme un chef d'équipe : indique quel pair tu sollicites, puis synthétise son retour.",
            "- Utilise la délégation seulement si elle apporte une valeur réelle.",
            "- Ne prétends pas que la délégation est impossible si un pair joignable est disponible.",
        ]

    try:
        from src.runtime.peer_network_autonomy import build_peer_network_context
        autonomy_ctx = build_peer_network_context()
        if autonomy_ctx:
            lines += ["", autonomy_ctx]
    except Exception:
        pass

    ctx = "\n".join(lines)
    if len(ctx) > _MAX_CONTEXT_CHARS:
        ctx = ctx[:_MAX_CONTEXT_CHARS - 3] + "..."

    return ctx
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
