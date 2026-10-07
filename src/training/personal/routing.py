"""Deterministic routing between the principal and personal models."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    requested_mode: str
    selected_model: str
    fallback_model: str
    reason_code: str
    confidence: float


class PersonalModelRouter:
    def choose(
        self,
        *,
        mode: str,
        principal_model: str,
        personal_model: str = "",
        personal_available: bool = False,
        capability_scores: dict[str, float] | None = None,
        required_capability: str = "general",
        confidence_threshold: float = 0.7,
    ) -> RoutingDecision:
        if mode not in {"principal", "personal", "automatic"}:
            raise ValueError("personal_routing_mode_invalid")
        if not principal_model:
            raise ValueError("principal_model_required")
        if mode == "principal":
            return RoutingDecision(mode, principal_model, "", "principal_requested", 1.0)
        if mode == "personal":
            if personal_available and personal_model:
                return RoutingDecision(mode, personal_model, principal_model, "personal_requested", 1.0)
            return RoutingDecision(mode, principal_model, "", "personal_unavailable_fallback", 0.0)
        score = float((capability_scores or {}).get(required_capability, (capability_scores or {}).get("general", 0.0)))
        if personal_available and personal_model and score >= confidence_threshold:
            return RoutingDecision(mode, personal_model, principal_model, "personal_capability_sufficient", score)
        return RoutingDecision(mode, principal_model, "", "principal_safer_for_capability", score)

    @staticmethod
    def provenance(decision: RoutingDecision) -> dict[str, Any]:
        return {
            "requested_mode": decision.requested_mode,
            "model_used": decision.selected_model,
            "fallback_model": decision.fallback_model,
            "reason_code": decision.reason_code,
            "confidence": round(decision.confidence, 4),
        }
