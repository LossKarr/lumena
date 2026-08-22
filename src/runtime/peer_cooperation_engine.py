"""Bloc C3 — Moteur d'initiative de coopération inter-Lumena.

Décide **quelle** tâche candidate gagnerait à être confiée à **quel** pair, à partir
de la carte des capacités vivante (C2). Fonction PURE et transparente : pas d'I/O,
pas d'action — elle ne fait que *proposer*. L'exécution (ou non) est décidée ailleurs
(mode shadow = log seulement ; mode live = exécution gardée, C3-live).

Heuristique volontairement simple et lisible (affinable ensuite) :
- éligible = pair `delegable` (joignable + non-quarantaine + token + scopes) ET
  `task.delegate` accordé ET niveau `mission` (peut exécuter une mission complète) ;
- score = base + bonus capacité qui colle + bonus fraîcheur ;
- au mieux 1 proposition par tâche candidate, les meilleures d'abord.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

# Mots-clés → capacité de pair (soft : un bonus, jamais un filtre dur).
_CAP_KEYWORDS = {
    "browser":   ("site", "web", "scrap", "internet", "url", "navigue", "en ligne", "crawl"),
    "documents": ("document", "rapport", "pdf", "docx", "xlsx", "excel", "présentation", "pptx", "tableur"),
    "voice":     ("voix", "audio", "parle", "tts", "dictée", "vocal"),
}


def _task_capabilities(text: str) -> Set[str]:
    t = (text or "").lower()
    return {cap for cap, kws in _CAP_KEYWORDS.items() if any(k in t for k in kws)}


def _reason(peer: Dict[str, Any], matched: Set[str]) -> str:
    bits = [f"{peer.get('name', '?')} est joignable et en mode mission"]
    if matched:
        bits.append("capacité adaptée : " + ", ".join(sorted(matched)))
    return " ; ".join(bits) + "."


def propose_delegations(
    candidate_tasks: Optional[List[Any]],
    capability_map: Dict[str, Any],
    *,
    max_proposals: int = 5,
) -> List[Dict[str, Any]]:
    """Retourne une liste de propositions `{objective, peer_id, peer_name, reason, score}`.

    `candidate_tasks` : liste de str OU de dict `{objective: ...}`.
    `capability_map`  : sortie de `build_capability_map()` (C2).
    Aucune proposition si aucun pair éligible ou aucune tâche.
    """
    peers = capability_map.get("peers", []) if isinstance(capability_map, dict) else []
    eligible = [
        p for p in peers
        if p.get("delegable")
        and "task.delegate" in (p.get("allowed_scopes") or [])
        and p.get("capability_level") == "mission"
    ]
    if not eligible or not candidate_tasks:
        return []

    proposals: List[Dict[str, Any]] = []
    for task in candidate_tasks:
        obj = task.get("objective") if isinstance(task, dict) else task
        obj = str(obj or "").strip()
        if not obj:
            continue
        wanted = _task_capabilities(obj)
        best, best_score, best_matched = None, -1.0, set()
        for p in eligible:
            score = 1.0
            matched = wanted & set(p.get("capabilities") or [])
            if matched:
                score += 2.0
            ago = p.get("seen_seconds_ago")
            if isinstance(ago, (int, float)):
                score += max(0.0, 1.0 - ago / 600.0)  # plus c'est frais, mieux c'est
            if score > best_score:
                best, best_score, best_matched = p, score, matched
        if best is not None:
            proposals.append({
                "objective": obj[:200],
                "peer_id": best.get("instance_id", ""),
                "peer_name": best.get("name", ""),
                "reason": _reason(best, best_matched),
                "score": round(best_score, 2),
            })
        if len(proposals) >= max_proposals:
            break
    # meilleures d'abord
    proposals.sort(key=lambda x: x["score"], reverse=True)
    return proposals
