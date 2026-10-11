"""Canonical conversational tools for Voice operational management.

Handlers expose bounded JSON facts.  Lumena composes the user-facing answer;
no canned assistant response lives here.
"""

from __future__ import annotations

import json
import unicodedata
from typing import Any, List

from .context import HandlerContext
from .contracts import HandlerResult
from .registry_v2 import HandlerDef
from ...voice.lifecycle import VoiceLifecycleManager
from ...voice.v2.management import (
    VOICE_GOALS,
    build_voice_operational_snapshot,
    diagnose_voice_snapshot,
    get_voice_recommendation_broker,
    recommend_voice_configuration,
)
from ...voice.v2.management_control import (
    apply_voice_recommendation_transaction,
    rollback_last_voice_transaction,
)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _normalized(value: str) -> str:
    return "".join(
        char for char in unicodedata.normalize("NFKD", str(value or "").casefold()) if not unicodedata.combining(char)
    )


def _runtime_channel_role() -> tuple[str, str]:
    try:
        from ...runtime.context import get_current_runtime_context

        runtime = get_current_runtime_context()
    except Exception:
        runtime = None
    if runtime is None:
        return "web", "owner"
    return (
        str(getattr(runtime, "channel", "web") or "web").strip().lower(),
        str(getattr(runtime, "user_role", "guest") or "guest").strip().lower(),
    )


def _explicit_voice_mutation(ctx: HandlerContext, *, rollback: bool = False) -> bool:
    if ctx.is_mission_run:
        return False
    query = _normalized(ctx.original_user_query)
    if not query:
        return False
    voice_terms = ("voix", "vocal", "parle", "tts", "micro", "ecoute", "whisper", "qwen", "piper")
    verbs = (
        ("restaur", "annul", "retour", "precedent")
        if rollback
        else ("appli", "regl", "change", "modifi", "optim", "acceler", "ralenti", "passe", "mets", "met ")
    )
    return any(term in query for term in voice_terms) and any(verb in query for verb in verbs)


def _config_dependencies():
    from web.routes.config import (
        _CONFIG_SCHEMA,
        _read_env_file,
        _validate_config_updates,
        _write_env_values,
    )

    schema_map = {item["key"]: item for item in _CONFIG_SCHEMA}
    return (
        _read_env_file,
        _write_env_values,
        lambda updates: _validate_config_updates(updates, schema_map),
    )


def _manager() -> VoiceLifecycleManager:
    return VoiceLifecycleManager.get_instance()


def _snapshot() -> dict[str, Any]:
    read_values, _, _ = _config_dependencies()
    from ...voice.v2.prewarm import detect_voice_hardware

    return build_voice_operational_snapshot(
        _manager().get_status(), env_values=read_values(), hardware=detect_voice_hardware()
    )


def _failure(name: str, exc: BaseException) -> HandlerResult:
    code = str(exc or "voice_management_failed").replace("\r", " ").replace("\n", " ")[:120]
    return HandlerResult.fail(
        code,
        output=_json({"ok": False, "error_code": code}),
        handler_name=name,
        status_code=code,
    )


async def inspect_voice_runtime_handler(ctx: HandlerContext) -> HandlerResult:
    name = "inspect_voice_runtime"
    try:
        return HandlerResult.ok(_json({"ok": True, "snapshot": _snapshot()}), handler_name=name)
    except Exception as exc:
        return _failure(name, exc)


async def diagnose_voice_handler(ctx: HandlerContext) -> HandlerResult:
    name = "diagnose_voice"
    try:
        snapshot = _snapshot()
        return HandlerResult.ok(
            _json({"ok": True, "diagnostic": diagnose_voice_snapshot(snapshot), "snapshot": snapshot}),
            handler_name=name,
        )
    except Exception as exc:
        return _failure(name, exc)


async def recommend_voice_configuration_handler(
    ctx: HandlerContext,
    goal: str = "balanced",
) -> HandlerResult:
    name = "recommend_voice_configuration"
    try:
        snapshot = _snapshot()
        recommendation = recommend_voice_configuration(snapshot, goal)
        ticket = get_voice_recommendation_broker().issue(recommendation)
        return HandlerResult.ok(
            _json({"ok": True, "recommendation": {**recommendation, **ticket}}),
            handler_name=name,
        )
    except Exception as exc:
        return _failure(name, exc)


async def _restart_voice(ctx: HandlerContext, manager: VoiceLifecycleManager) -> bool:
    if ctx.lumena is None:
        return False
    backend = str(getattr(manager, "active_backend", "v2") or "v2")
    if backend not in {"v2", "legacy"}:
        backend = "v2"
    await manager.stop()
    return bool(await manager.start(ctx.lumena, backend=backend))


async def apply_voice_recommendation_handler(
    ctx: HandlerContext,
    recommendation_id: str,
) -> HandlerResult:
    name = "apply_voice_recommendation"
    channel, role = _runtime_channel_role()
    if role not in {"owner", "admin"}:
        return _failure(name, PermissionError("voice_owner_or_admin_required"))
    if not _explicit_voice_mutation(ctx):
        return _failure(name, PermissionError("explicit_voice_change_request_required"))
    recommendation = get_voice_recommendation_broker().consume(recommendation_id)
    if recommendation is None:
        return _failure(name, LookupError("voice_recommendation_expired_or_used"))
    try:
        read_values, write_values, validate_values = _config_dependencies()
        manager = _manager()
        result = await apply_voice_recommendation_transaction(
            recommendation,
            read_values=read_values,
            write_values=write_values,
            validate_values=validate_values,
            status_provider=manager.get_status,
            restart_voice=lambda: _restart_voice(ctx, manager),
            defer_until_safe=channel == "voice",
            explicitly_authorized=True,
            management_mode=read_values().get("LUMENA_VOICE_MANAGEMENT_MODE", "assisted"),
        )
        return HandlerResult.ok(_json({"ok": True, "result": result}), handler_name=name)
    except Exception as exc:
        return _failure(name, exc)


async def verify_voice_configuration_handler(ctx: HandlerContext) -> HandlerResult:
    name = "verify_voice_configuration"
    try:
        snapshot = _snapshot()
        diagnostic = diagnose_voice_snapshot(snapshot)
        return HandlerResult.ok(
            _json(
                {
                    "ok": True,
                    "verified": not snapshot["configuration"]["settings_pending"],
                    "snapshot": snapshot,
                    "diagnostic": diagnostic,
                }
            ),
            handler_name=name,
        )
    except Exception as exc:
        return _failure(name, exc)


async def rollback_voice_configuration_handler(ctx: HandlerContext) -> HandlerResult:
    name = "rollback_voice_configuration"
    channel, role = _runtime_channel_role()
    if role not in {"owner", "admin"}:
        return _failure(name, PermissionError("voice_owner_or_admin_required"))
    if not _explicit_voice_mutation(ctx, rollback=True):
        return _failure(name, PermissionError("explicit_voice_rollback_request_required"))
    try:
        read_values, write_values, _ = _config_dependencies()
        manager = _manager()
        result = await rollback_last_voice_transaction(
            read_values=read_values,
            write_values=write_values,
            status_provider=manager.get_status,
            restart_voice=lambda: _restart_voice(ctx, manager),
            explicitly_authorized=True,
        )
        return HandlerResult.ok(_json({"ok": True, "result": result}), handler_name=name)
    except Exception as exc:
        return _failure(name, exc)


def get_voice_management_handler_defs() -> List[HandlerDef]:
    goal_schema = {"type": "string", "enum": sorted(VOICE_GOALS), "default": "balanced"}
    return [
        HandlerDef(
            "inspect_voice_runtime",
            "Lit l'état vocal réel de Lumena : moteur demandé/effectif, matériel, "
            "composants, mesures, confidentialité et réglages en attente. Ne modifie rien.",
            {"properties": {}, "required": []},
            inspect_voice_runtime_handler,
            "voice",
            "handlers.voice_management",
        ),
        HandlerDef(
            "diagnose_voice",
            "Diagnostique la voix à partir des preuves runtime et retourne causes, "
            "limites et actions possibles. Ne modifie rien.",
            {"properties": {}, "required": []},
            diagnose_voice_handler,
            "voice",
            "handlers.voice_management",
        ),
        HandlerDef(
            "recommend_voice_configuration",
            "Recommande un profil vocal compatible selon le besoin, sans l'appliquer. "
            "Retourne un identifiant court à usage unique.",
            {"properties": {"goal": goal_schema}, "required": []},
            recommend_voice_configuration_handler,
            "voice",
            "handlers.voice_management",
        ),
        HandlerDef(
            "apply_voice_recommendation",
            "Applique une recommandation Voice à usage unique seulement si la demande "
            "humaine exige le changement. Vérifie ou restaure automatiquement.",
            {
                "properties": {"recommendation_id": {"type": "string", "minLength": 24, "maxLength": 128}},
                "required": ["recommendation_id"],
            },
            apply_voice_recommendation_handler,
            "voice",
            "handlers.voice_management",
        ),
        HandlerDef(
            "verify_voice_configuration",
            "Relit l'état vocal effectif après un changement et indique si des réglages "
            "restent en attente. Ne modifie rien.",
            {"properties": {}, "required": []},
            verify_voice_configuration_handler,
            "voice",
            "handlers.voice_management",
        ),
        HandlerDef(
            "rollback_voice_configuration",
            "Restaure la dernière transaction Voice éligible seulement sur demande humaine explicite.",
            {"properties": {}, "required": []},
            rollback_voice_configuration_handler,
            "voice",
            "handlers.voice_management",
        ),
    ]
