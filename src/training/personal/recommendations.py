"""Deterministic, expiring recommendations based only on current facts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from .contracts import sha256_json


def build_recommendations(snapshot: dict[str, Any], *, now: datetime | None = None) -> list[dict[str, Any]]:
    moment = now or datetime.now(timezone.utc)
    expires = (moment + timedelta(minutes=10)).isoformat().replace("+00:00", "Z")
    recommendations: list[dict[str, Any]] = []

    def add(action: str, reasons: list[str], facts: dict[str, Any], benefit: str, risk: str, approval: bool) -> None:
        stable = {"action": action, "reasons": reasons, "facts": facts}
        recommendations.append({
            "recommendation_id": f"rec_{sha256_json(stable)[:20]}",
            "action": action,
            "reason_codes": reasons,
            "facts": facts,
            "expected_benefit": benefit,
            "estimated_time": "unknown" if action == "train" else "under_one_minute",
            "estimated_resources": snapshot.get("readiness", {}).get("resources", {}),
            "risk_level": risk,
            "confidence": 1.0,
            "required_approval": approval,
            "action_preview": action,
            "expires_at": expires,
        })

    policy = snapshot.get("policy", {})
    stats = snapshot.get("experiences", {})
    accepted = int(stats.get("counts", {}).get("accepted", 0))
    minimum = int(snapshot.get("settings", {}).get("min_new_experiences", 100))
    active_job = snapshot.get("active_job")
    active_model = snapshot.get("personal_model")
    if not policy.get("learning_enabled"):
        add("enable_learning", ["personal_learning_disabled"], {}, "Commencer la collecte locale autorisée.", "low", False)
    elif not policy.get("local_capture_enabled"):
        add("enable_capture", ["local_capture_disabled"], {}, "Constituer le patrimoine local.", "low", False)
    elif active_job:
        add("inspect_job", ["training_job_active"], {"state": active_job.get("state")}, "Voir la progression et les blocages réels.", "low", False)
    elif accepted < minimum:
        add("collect_more", ["accepted_experiences_below_threshold"], {"accepted": accepted, "required": minimum}, "Améliorer la fiabilité du prochain dataset.", "low", False)
    else:
        add("prepare_training", ["accepted_experiences_ready"], {"accepted": accepted, "required": minimum}, "Préparer une nouvelle version personnelle.", "medium", False)
    if active_model and not snapshot.get("backup_available"):
        add("create_backup", ["active_version_without_backup"], {"version": active_model.get("version")}, "Protéger la lignée avant une nouvelle version.", "low", False)
    return recommendations
