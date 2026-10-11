"""Atomic and auditable application of Voice recommendations."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import threading
import time
from typing import Any, Awaitable, Callable, Mapping, Optional

from src.utils.paths import VOICE_MANAGEMENT_AUDIT_JSONL, VOICE_MANAGEMENT_STATE_JSON
from src.utils.persistence import atomic_write_json, safe_read_json

from .management import VOICE_MANAGED_KEYS, VOICE_PROTECTED_KEYS


ReadValues = Callable[[], Mapping[str, str]]
WriteValues = Callable[[dict[str, str]], None]
ValidateValues = Callable[[dict[str, str]], tuple[dict[str, str], list[str]]]
StatusProvider = Callable[[], Mapping[str, Any]]
RestartVoice = Callable[[], Awaitable[bool]]

_MUTATION_LOCK = threading.RLock()
_DEFERRED_TASKS: set[asyncio.Task] = set()
_ACTIVE_TRANSACTION_ID: Optional[str] = None
_SAFE_RUNTIME_STATES = {"idle", "wake_listening", "interrupted", "degraded", "unknown"}
_ACTIVE_RUNTIME_STATES = {"thinking", "speaking"}


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _fingerprint(values: Mapping[str, str]) -> str:
    payload = json.dumps(dict(sorted(values.items())), ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _bounded_code(value: Any, default: str = "unknown") -> str:
    clean = str(value or default).replace("\r", " ").replace("\n", " ")[:64]
    return clean or default


def validate_voice_management_updates(updates: Mapping[str, Any]) -> dict[str, str]:
    """Reject arbitrary configuration and privacy escalation."""
    result: dict[str, str] = {}
    for raw_key, raw_value in updates.items():
        key = str(raw_key)
        value = str(raw_value)
        if key not in VOICE_MANAGED_KEYS and key not in VOICE_PROTECTED_KEYS:
            raise ValueError("voice_setting_out_of_scope")
        if key in VOICE_PROTECTED_KEYS:
            # Recommendations may enforce local strict, never relax privacy.
            if key != "LUMENA_VOICE_CLOUD_ALLOWED" or value != "0":
                raise ValueError("protected_voice_setting")
        result[key] = value
    if not result:
        raise ValueError("empty_voice_recommendation")
    return result


def _append_audit(event: Mapping[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(dict(event), ensure_ascii=False, separators=(",", ":"), default=str)
    with _MUTATION_LOCK:
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line + "\n")


def _reserve_transaction(transaction_id: str) -> None:
    global _ACTIVE_TRANSACTION_ID
    with _MUTATION_LOCK:
        if _ACTIVE_TRANSACTION_ID is not None:
            raise RuntimeError("voice_configuration_busy")
        _ACTIVE_TRANSACTION_ID = transaction_id


def _release_transaction(transaction_id: str) -> None:
    global _ACTIVE_TRANSACTION_ID
    with _MUTATION_LOCK:
        if _ACTIVE_TRANSACTION_ID == transaction_id:
            _ACTIVE_TRANSACTION_ID = None


async def wait_until_voice_safe(
    status_provider: StatusProvider,
    *,
    timeout_s: float = 180.0,
    poll_s: float = 0.1,
) -> bool:
    """Wait until a current Voice turn has finished before a restart.

    If no active state is observed, a short grace period permits chat/Telegram
    calls to continue without waiting for an utterance that cannot exist.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(1.0, float(timeout_s))
    grace_deadline = loop.time() + 0.75
    observed_active = False
    while loop.time() < deadline:
        status = dict(status_provider() or {})
        state = str(status.get("runtime_state") or "unknown").strip().lower()
        if state in _ACTIVE_RUNTIME_STATES:
            observed_active = True
        elif observed_active and state in _SAFE_RUNTIME_STATES:
            await asyncio.sleep(0.15)
            return True
        elif not observed_active and loop.time() >= grace_deadline and state in _SAFE_RUNTIME_STATES:
            return True
        await asyncio.sleep(max(0.02, min(1.0, float(poll_s))))
    return False


async def apply_voice_recommendation_transaction(
    recommendation: Mapping[str, Any],
    *,
    read_values: ReadValues,
    write_values: WriteValues,
    validate_values: ValidateValues,
    status_provider: StatusProvider,
    restart_voice: Optional[RestartVoice] = None,
    defer_until_safe: bool = False,
    explicitly_authorized: bool = False,
    management_mode: str = "assisted",
    safe_auto_cooldown_s: float = 300.0,
    state_path: Path = VOICE_MANAGEMENT_STATE_JSON,
    audit_path: Path = VOICE_MANAGEMENT_AUDIT_JSONL,
) -> dict[str, Any]:
    mode = str(management_mode or "assisted").strip().lower()
    if mode not in {"observe", "assisted", "safe_auto"}:
        mode = "assisted"
    if mode == "observe":
        raise PermissionError("voice_management_observe_only")
    if not explicitly_authorized and mode != "safe_auto":
        raise PermissionError("explicit_voice_change_request_required")
    if recommendation.get("compatible") is False:
        raise ValueError("voice_recommendation_incompatible")
    updates = validate_voice_management_updates(recommendation.get("updates", {}))
    normalized, errors = validate_values(updates)
    if errors:
        raise ValueError("voice_settings_invalid:" + "|".join(str(item)[:80] for item in errors))
    if set(normalized) != set(updates):
        raise ValueError("voice_settings_validation_mismatch")

    before_all = dict(read_values())
    before = {key: str(before_all.get(key, "")) for key in normalized}
    transaction_id = secrets.token_hex(16)
    changed = {key: value for key, value in normalized.items() if before.get(key) != value}
    if not changed:
        return {
            "ok": True,
            "transaction_id": transaction_id,
            "state": "already_applied",
            "updated": [],
            "restarted": False,
            "verified": True,
        }
    if not explicitly_authorized and mode == "safe_auto":
        previous_state = safe_read_json(state_path, default={})
        previous = previous_state.get("last_transaction", {}) if isinstance(previous_state, Mapping) else {}
        last_epoch = float(previous.get("at_epoch", 0.0) or 0.0) if isinstance(previous, Mapping) else 0.0
        if last_epoch and time.time() - last_epoch < max(30.0, float(safe_auto_cooldown_s)):
            raise PermissionError("voice_safe_auto_cooldown")

    event = {
        "schema_version": 1,
        "at": _now(),
        "event": "voice_config_apply",
        "transaction_id": transaction_id,
        "goal": _bounded_code(recommendation.get("goal")),
        "profile_id": _bounded_code(recommendation.get("profile_id")),
        "keys": sorted(changed),
        "before_fingerprint": _fingerprint(before),
        "after_fingerprint": _fingerprint(changed),
    }
    _reserve_transaction(transaction_id)
    try:
        with _MUTATION_LOCK:
            write_values(changed)
            for key, value in changed.items():
                os.environ[key] = value
    except Exception:
        _release_transaction(transaction_id)
        raise

    async def finalize(*, wait_for_safe: bool) -> dict[str, Any]:
        restarted = False
        verified = False
        rolled_back = False
        restart_error: Optional[str] = None
        try:
            if restart_voice is not None and bool(status_provider().get("running")):
                if wait_for_safe:
                    safe = await wait_until_voice_safe(status_provider)
                    if not safe:
                        raise RuntimeError("voice_safe_restart_timeout")
                restarted = bool(await restart_voice())
                if not restarted:
                    raise RuntimeError("voice_restart_failed")
            after_all = dict(read_values())
            persisted = all(str(after_all.get(key, "")) == value for key, value in changed.items())
            effective = dict(status_provider() or {})
            pending = bool(effective.get("settings_pending"))
            verified = persisted and (not restarted or not pending)
            if not verified:
                raise RuntimeError("voice_change_not_effective")
        except Exception as exc:
            restart_error = _bounded_code(exc, "voice_change_failed")
            with _MUTATION_LOCK:
                write_values(before)
                for key, value in before.items():
                    os.environ[key] = value
            rolled_back = True
            if restart_voice is not None and bool(status_provider().get("running")):
                try:
                    await restart_voice()
                except Exception:
                    pass
        try:
            result = {
                "ok": bool(verified and not rolled_back),
                "transaction_id": transaction_id,
                "state": "verified" if verified and not rolled_back else "rolled_back",
                "updated": sorted(changed),
                "restarted": restarted,
                "verified": verified,
                "rolled_back": rolled_back,
                "error_code": restart_error,
            }
            state = {
                "schema_version": 1,
                "last_transaction": {
                    **result,
                    "at": _now(),
                    "at_epoch": time.time(),
                    "before": before,
                    "after": changed,
                },
            }
            state_path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_json(state_path, state)
            _append_audit(
                {
                    **event,
                    **{key: result[key] for key in ("state", "restarted", "verified", "rolled_back", "error_code")},
                },
                audit_path,
            )
            return result
        finally:
            _release_transaction(transaction_id)

    if defer_until_safe and restart_voice is not None and bool(status_provider().get("running")):
        scheduled = {
            "ok": True,
            "transaction_id": transaction_id,
            "state": "scheduled",
            "updated": sorted(changed),
            "restarted": False,
            "verified": False,
            "rolled_back": False,
            "error_code": None,
        }
        try:
            atomic_write_json(
                state_path,
                {
                    "schema_version": 1,
                    "last_transaction": {
                        **scheduled,
                        "at": _now(),
                        "at_epoch": time.time(),
                        "before": before,
                        "after": changed,
                    },
                },
            )
            _append_audit({**event, "state": "scheduled"}, audit_path)
            task = asyncio.create_task(finalize(wait_for_safe=True), name=f"voice-config-{transaction_id}")
        except Exception:
            _release_transaction(transaction_id)
            raise
        _DEFERRED_TASKS.add(task)
        task.add_done_callback(_DEFERRED_TASKS.discard)
        return scheduled
    return await finalize(wait_for_safe=False)


async def rollback_last_voice_transaction(
    *,
    read_values: ReadValues,
    write_values: WriteValues,
    status_provider: StatusProvider,
    restart_voice: Optional[RestartVoice] = None,
    explicitly_authorized: bool = False,
    state_path: Path = VOICE_MANAGEMENT_STATE_JSON,
    audit_path: Path = VOICE_MANAGEMENT_AUDIT_JSONL,
) -> dict[str, Any]:
    if not explicitly_authorized:
        raise PermissionError("explicit_voice_rollback_request_required")
    state = safe_read_json(state_path, default={})
    transaction = state.get("last_transaction", {}) if isinstance(state, Mapping) else {}
    before = transaction.get("before", {}) if isinstance(transaction, Mapping) else {}
    if not isinstance(before, Mapping) or not before:
        raise LookupError("voice_rollback_unavailable")
    updates = validate_voice_management_updates(before)
    with _MUTATION_LOCK:
        write_values(updates)
        for key, value in updates.items():
            os.environ[key] = value
    restarted = False
    if restart_voice is not None and bool(status_provider().get("running")):
        restarted = bool(await restart_voice())
        if not restarted:
            raise RuntimeError("voice_rollback_restart_failed")
    persisted = dict(read_values())
    verified = all(str(persisted.get(key, "")) == value for key, value in updates.items())
    result = {
        "ok": verified,
        "state": "restored" if verified else "restore_failed",
        "restored_transaction_id": str(transaction.get("transaction_id") or "")[:64],
        "updated": sorted(updates),
        "restarted": restarted,
        "verified": verified,
    }
    _append_audit(
        {
            "schema_version": 1,
            "at": _now(),
            "event": "voice_config_rollback",
            "transaction_id": result["restored_transaction_id"],
            "keys": result["updated"],
            "state": result["state"],
            "restarted": restarted,
            "verified": verified,
        },
        audit_path,
    )
    if verified:
        atomic_write_json(state_path, {"schema_version": 1, "last_transaction": None})
    return result
