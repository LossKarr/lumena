"""Bounded conversational tools for Lumena's personal-model control plane."""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Any, List

from src.training.personal.control_plane import PersonalModelControlPlane
from src.utils.paths import DATA_DIR

from .context import HandlerContext
from .contracts import HandlerResult
from .registry_v2 import HandlerDef


def _plane() -> PersonalModelControlPlane:
    return PersonalModelControlPlane(DATA_DIR / "personal_model")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _ok(name: str, value: Any) -> HandlerResult:
    return HandlerResult.ok(_json({"ok": True, **value}), handler_name=name)


def _fail(name: str, exc: Exception) -> HandlerResult:
    code = str(exc)[:160] or type(exc).__name__
    return HandlerResult.fail(code, output=_json({"ok": False, "error_code": code}), handler_name=name, status_code=code)


def _normalized(value: str) -> str:
    return "".join(char for char in unicodedata.normalize("NFKD", value.casefold()) if not unicodedata.combining(char))


def _explicit(ctx: HandlerContext, *verbs: str) -> bool:
    query = _normalized(ctx.original_user_query or "")
    return bool(query and any(re.search(rf"\b{re.escape(verb)}\w*\b", query) for verb in verbs))


async def personal_model_status_handler(ctx: HandlerContext) -> HandlerResult:
    return _ok("personal_model_status", {"status": _plane().status()})


async def personal_learning_health_handler(ctx: HandlerContext) -> HandlerResult:
    return _ok("personal_learning_health", {"health": _plane().health()})


async def personal_experience_stats_handler(ctx: HandlerContext) -> HandlerResult:
    return _ok("personal_experience_stats", {"experiences": _plane().experience_stats()})


async def personal_training_settings_handler(ctx: HandlerContext) -> HandlerResult:
    return _ok("personal_training_settings", {"settings": _plane().training_settings()})


async def personal_training_jobs_handler(ctx: HandlerContext) -> HandlerResult:
    return _ok("personal_training_jobs", {"jobs": _plane().status().get("jobs", [])})


async def personal_model_versions_handler(ctx: HandlerContext) -> HandlerResult:
    return _ok("personal_model_versions", {"lineages": _plane().status().get("lineages", {})})


async def personal_model_recommendations_handler(ctx: HandlerContext) -> HandlerResult:
    return _ok("personal_model_recommendations", {"recommendations": _plane().recommendations()})


async def personal_model_audit_trail_handler(ctx: HandlerContext, limit: int = 50) -> HandlerResult:
    return _ok("personal_model_audit_trail", {"events": _plane().audit_trail(max(1, min(limit, 100)))})


async def update_personal_training_settings_handler(ctx: HandlerContext, changes: dict[str, Any]) -> HandlerResult:
    name = "update_personal_training_settings"
    if not _explicit(ctx, "regl", "configur", "modifi", "active", "desactive", "planifi"):
        return _fail(name, PermissionError("explicit_user_settings_request_required"))
    try:
        return _ok(name, _plane().update_training_settings(changes, actor="conversation-owner"))
    except Exception as exc:
        return _fail(name, exc)


async def update_personal_learning_policy_handler(ctx: HandlerContext, changes: dict[str, Any], approval_token: str = "") -> HandlerResult:
    name = "update_personal_learning_policy"
    if not _explicit(ctx, "regl", "configur", "modifi", "active", "desactive", "juge", "collect"):
        return _fail(name, PermissionError("explicit_user_policy_request_required"))
    try:
        return _ok(name, _plane().update_policy(changes, actor="conversation-owner", approval_token=approval_token))
    except Exception as exc:
        return _fail(name, exc)


async def prepare_personal_dataset_handler(ctx: HandlerContext) -> HandlerResult:
    name = "prepare_personal_dataset"
    if not _explicit(ctx, "prepar", "construi", "cree", "dataset"):
        return _fail(name, PermissionError("explicit_user_dataset_request_required"))
    try:
        return _ok(name, _plane().prepare_dataset())
    except Exception as exc:
        return _fail(name, exc)


async def queue_personal_training_handler(
    ctx: HandlerContext,
    base_model_id: str,
    finetune: dict[str, Any],
    bump: str = "patch",
    display_prefix: str = "lumena",
) -> HandlerResult:
    name = "queue_personal_training"
    if not _explicit(ctx, "entrain", "lance", "cree", "version"):
        return _fail(name, PermissionError("explicit_user_training_request_required"))
    try:
        return _ok(
            name,
            _plane().create_training(
                base_model_id=base_model_id,
                finetune=finetune,
                bump=bump,
                display_prefix=display_prefix,
                actor="conversation-owner",
            ),
        )
    except Exception as exc:
        return _fail(name, exc)


async def control_personal_training_handler(ctx: HandlerContext, run_id: str, action: str) -> HandlerResult:
    name = "control_personal_training"
    if action not in {"launch", "pause", "resume"} or not _explicit(ctx, action, "lance", "pause", "reprend"):
        return _fail(name, PermissionError("explicit_user_training_control_required"))
    try:
        plane = _plane()
        result = {"launch": plane.launch_training, "pause": plane.pause_training, "resume": plane.resume_training}[action](run_id, actor="conversation-owner")
        return _ok(name, result)
    except Exception as exc:
        return _fail(name, exc)


async def evaluate_personal_model_handler(ctx: HandlerContext, lineage_id: str, version: str) -> HandlerResult:
    name = "evaluate_personal_model"
    if not _explicit(ctx, "evalu", "test", "compar", "verifi"):
        return _fail(name, PermissionError("explicit_user_evaluation_request_required"))
    try:
        return _ok(name, _plane().evaluate_version(lineage_id, version, actor="conversation-owner"))
    except Exception as exc:
        return _fail(name, exc)


async def request_personal_model_approval_handler(ctx: HandlerContext, action: str, resource: str) -> HandlerResult:
    name = "request_personal_model_approval"
    if not _explicit(ctx, "annul", "active", "restaur", "rollback", "supprim", "export", "import", "cloud"):
        return _fail(name, PermissionError("explicit_user_sensitive_action_required"))
    try:
        result = _plane().approval_preview(action=action, resource=resource, actor="conversation-owner")
        return HandlerResult.ok(_json({"ok": True, "confirmation_required": True, **result}), handler_name=name, status_code="confirmation_required")
    except Exception as exc:
        return _fail(name, exc)


async def confirm_personal_model_action_handler(
    ctx: HandlerContext, action: str, resource: str, approval_token: str,
    lineage_id: str = "", version: str = "", run_id: str = "", quant_type: str = "Q4_K_M",
) -> HandlerResult:
    name = "confirm_personal_model_action"
    if not _explicit(ctx, "confirm", "oui", "valide", "execute"):
        return _fail(name, PermissionError("human_confirmation_required"))
    try:
        plane = _plane()
        if action == "cancel_training":
            result = plane.cancel_training(run_id or resource, approval_token=approval_token, actor="conversation-owner")
        elif action == "activate_version":
            result = plane.activate_version(lineage_id, version, approval_token=approval_token, actor="conversation-owner")
        elif action == "rollback_version":
            result = plane.rollback_version(lineage_id, version, approval_token=approval_token, actor="conversation-owner")
        elif action == "export_version":
            result = plane.export_version(lineage_id, version, approval_token=approval_token, actor="conversation-owner", quant_type=quant_type)
        elif action == "export_data":
            result = plane.create_backup(approval_token=approval_token, actor="conversation-owner")
        elif action == "set_global_default":
            result = (
                plane.use_principal_default(approval_token=approval_token, actor="conversation-owner")
                if resource == "principal"
                else plane.set_global_default(lineage_id, version, approval_token=approval_token, actor="conversation-owner")
            )
        elif action == "delete_data":
            result = plane.delete_learning_data(approval_token=approval_token, actor="conversation-owner")
        else:
            raise ValueError("personal_model_action_not_supported")
        return _ok(name, result)
    except Exception as exc:
        return _fail(name, exc)


def get_personal_model_handler_defs() -> List[HandlerDef]:
    empty = {"properties": {}, "required": []}
    defs = [
        HandlerDef("personal_model_status", "Relit le modèle principal, la version personnelle active, le défaut effectif et le fallback.", empty, personal_model_status_handler, "personal_models", "handlers.personal_models"),
        HandlerDef("personal_learning_health", "Diagnostique la collecte, les dépendances, les jobs et les blocages à partir de l'état réel.", empty, personal_learning_health_handler, "personal_models", "handlers.personal_models"),
        HandlerDef("personal_experience_stats", "Compte les expériences sans exposer leur contenu privé.", empty, personal_experience_stats_handler, "personal_models", "handlers.personal_models"),
        HandlerDef("personal_training_settings", "Retourne les réglages demandés et effectifs avec leur provenance.", empty, personal_training_settings_handler, "personal_models", "handlers.personal_models"),
        HandlerDef("personal_training_jobs", "Relit les jobs, checkpoints et erreurs prouvés.", empty, personal_training_jobs_handler, "personal_models", "handlers.personal_models"),
        HandlerDef("personal_model_versions", "Relit la lignée, les versions et leur statut d'activation.", empty, personal_model_versions_handler, "personal_models", "handlers.personal_models"),
        HandlerDef("personal_model_recommendations", "Calcule des recommandations expirables depuis les faits actuels.", empty, personal_model_recommendations_handler, "personal_models", "handlers.personal_models"),
        HandlerDef("personal_model_audit_trail", "Relit les actions bornées et leurs identifiants de preuve.", {"properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 100}}, "required": []}, personal_model_audit_trail_handler, "personal_models", "handlers.personal_models"),
        HandlerDef("update_personal_training_settings", "Modifie les horaires et budgets uniquement sur demande explicite.", {"properties": {"changes": {"type": "object"}}, "required": ["changes"]}, update_personal_training_settings_handler, "personal_models", "handlers.personal_models"),
        HandlerDef("update_personal_learning_policy", "Modifie collecte et juge; un changement d'accès cloud exige un ticket dédié.", {"properties": {"changes": {"type": "object"}, "approval_token": {"type": "string"}}, "required": ["changes"]}, update_personal_learning_policy_handler, "personal_models", "handlers.personal_models"),
        HandlerDef("prepare_personal_dataset", "Prépare un dataset déterministe autorisé sans lancer d'entraînement.", empty, prepare_personal_dataset_handler, "personal_models", "handlers.personal_models"),
        HandlerDef("queue_personal_training", "Crée une version et un job à partir d'un dataset scellé; ne prouve pas encore la réussite.", {"properties": {"base_model_id": {"type": "string", "maxLength": 384}, "finetune": {"type": "object"}, "bump": {"type": "string", "enum": ["patch", "minor", "major"]}, "display_prefix": {"type": "string", "maxLength": 48}}, "required": ["base_model_id", "finetune"]}, queue_personal_training_handler, "personal_models", "handlers.personal_models"),
        HandlerDef("control_personal_training", "Lance, met en pause ou reprend un job existant sur demande explicite.", {"properties": {"run_id": {"type": "string", "maxLength": 96}, "action": {"type": "string", "enum": ["launch", "pause", "resume"]}}, "required": ["run_id", "action"]}, control_personal_training_handler, "personal_models", "handlers.personal_models"),
        HandlerDef("evaluate_personal_model", "Compare une version exportée à la baseline avec un juge isolé et persiste la preuve.", {"properties": {"lineage_id": {"type": "string"}, "version": {"type": "string"}}, "required": ["lineage_id", "version"]}, evaluate_personal_model_handler, "personal_models", "handlers.personal_models"),
        HandlerDef("request_personal_model_approval", "Prépare une action sensible et retourne un ticket court sans l'exécuter.", {"properties": {"action": {"type": "string", "enum": sorted(PersonalModelControlPlane.SENSITIVE_ACTIONS)}, "resource": {"type": "string", "maxLength": 160}}, "required": ["action", "resource"]}, request_personal_model_approval_handler, "personal_models", "handlers.personal_models"),
        HandlerDef("confirm_personal_model_action", "Consomme un ticket à usage unique après confirmation humaine explicite.", {"properties": {"action": {"type": "string", "enum": ["cancel_training", "activate_version", "rollback_version", "export_version", "export_data", "set_global_default", "delete_data"]}, "resource": {"type": "string"}, "approval_token": {"type": "string"}, "lineage_id": {"type": "string"}, "version": {"type": "string"}, "run_id": {"type": "string"}, "quant_type": {"type": "string", "enum": ["Q4_K_M", "Q5_K_M", "Q8_0"]}}, "required": ["action", "resource", "approval_token"]}, confirm_personal_model_action_handler, "personal_models", "handlers.personal_models"),
    ]
    return defs
