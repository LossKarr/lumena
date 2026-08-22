"""Codex subscription contract and local CLI preflight.

This module is intentionally dormant: importing it never starts Codex, reads
credentials, opens a browser, or changes Lumena's active LLM.  Later rollout
lots build on this fail-closed contract.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlparse

from src.llm.codex_app_server import (
    CodexAppServerSupervisor,
    CodexNotification,
)


CODEX_PROTOCOL_FAMILY = "app-server-jsonl-v1"
CODEX_REQUIRED_CLI_CAPABILITIES = (
    "codex --version",
    "codex app-server --help",
    "codex app-server generate-json-schema",
)
CODEX_LOCAL_ROLLOUT_STAGE = "stable_local"


def codex_cli_compatibility(
    *, observed_version: str = "", ready: bool | None = None
) -> dict[str, Any]:
    """Describe the capability-based CLI contract without inventing a version floor.

    OpenAI documents App Server and its schema generator but does not publish a
    durable numeric minimum version for third-party local integrations. Lumena
    therefore certifies the executable it actually probes instead of trusting a
    stale version string.
    """

    return {
        "policy": "capability_probe",
        "numeric_minimum": None,
        "protocol_family": CODEX_PROTOCOL_FAMILY,
        "required_capabilities": list(CODEX_REQUIRED_CLI_CAPABILITIES),
        "observed_version": str(observed_version or ""),
        "compatible": ready,
        "rollout": CODEX_LOCAL_ROLLOUT_STAGE,
    }


class OpenAIAccessMode(str, Enum):
    """How Lumena is allowed to access OpenAI-backed execution."""

    API = "api"
    CHATGPT_CODEX = "chatgpt_codex"


class CodexAPIFallback(str, Enum):
    """Paid API fallback policy for subscription execution."""

    NEVER = "never"
    ASK = "ask"


class CodexSurface(str, Enum):
    """Lumena surfaces that may eventually route through Codex."""

    CODEAGENT = "codeagent"
    CHAT = "chat"
    COLLABORATION = "collaboration"
    AGENT = "agent"
    MISSIONS = "missions"


class CodexSurfaceStage(str, Enum):
    """Rollout gate for each surface in the implementation plan."""

    PILOT = "pilot"
    AFTER_CODEAGENT = "after_codeagent"
    AFTER_CHAT = "after_chat"
    REQUIRES_TOOL_BRIDGE = "requires_tool_bridge"


CODEX_SURFACE_STAGES: Mapping[CodexSurface, CodexSurfaceStage] = {
    CodexSurface.CODEAGENT: CodexSurfaceStage.PILOT,
    CodexSurface.CHAT: CodexSurfaceStage.AFTER_CODEAGENT,
    CodexSurface.COLLABORATION: CodexSurfaceStage.AFTER_CHAT,
    CodexSurface.AGENT: CodexSurfaceStage.REQUIRES_TOOL_BRIDGE,
    CodexSurface.MISSIONS: CodexSurfaceStage.REQUIRES_TOOL_BRIDGE,
}


@dataclass(frozen=True)
class CodexSubscriptionSettings:
    """Non-secret subscription preferences loaded from the environment."""

    access_mode: OpenAIAccessMode = OpenAIAccessMode.API
    cli_path: str = ""
    default_model: str = ""
    surfaces: frozenset[CodexSurface] = frozenset({CodexSurface.CODEAGENT})
    api_fallback: CodexAPIFallback = CodexAPIFallback.NEVER

    @property
    def enabled(self) -> bool:
        return self.access_mode is OpenAIAccessMode.CHATGPT_CODEX

    def surface_requested(self, surface: CodexSurface) -> bool:
        return self.enabled and surface in self.surfaces


def load_codex_subscription_settings(
    environ: Mapping[str, str] | None = None,
) -> CodexSubscriptionSettings:
    """Load settings with safe defaults for malformed or absent values."""

    env = os.environ if environ is None else environ

    try:
        access_mode = OpenAIAccessMode(
            env.get("LUMENA_OPENAI_ACCESS_MODE", OpenAIAccessMode.API.value)
            .strip()
            .lower()
        )
    except ValueError:
        access_mode = OpenAIAccessMode.API

    raw_surfaces = env.get("LUMENA_CODEX_SURFACES", CodexSurface.CODEAGENT.value)
    surfaces: set[CodexSurface] = set()
    for value in re.split(r"[,;\s]+", raw_surfaces.strip().lower()):
        if not value:
            continue
        try:
            surfaces.add(CodexSurface(value))
        except ValueError:
            continue

    try:
        api_fallback = CodexAPIFallback(
            env.get("LUMENA_CODEX_API_FALLBACK", CodexAPIFallback.NEVER.value)
            .strip()
            .lower()
        )
    except ValueError:
        api_fallback = CodexAPIFallback.NEVER

    return CodexSubscriptionSettings(
        access_mode=access_mode,
        cli_path=env.get("LUMENA_CODEX_CLI_PATH", "").strip(),
        default_model=env.get("LUMENA_CODEX_DEFAULT_MODEL", "").strip(),
        surfaces=frozenset(surfaces),
        api_fallback=api_fallback,
    )


class CodexCLIState(str, Enum):
    """Actionable result of the local Codex CLI preflight."""

    READY = "ready"
    NOT_FOUND = "not_found"
    INACCESSIBLE = "inaccessible"
    TIMED_OUT = "timed_out"
    BROKEN = "broken"
    APP_SERVER_UNSUPPORTED = "app_server_unsupported"
    PROTOCOL_INCOMPATIBLE = "protocol_incompatible"


@dataclass(frozen=True)
class CodexCLICandidate:
    path: str
    source: str


@dataclass(frozen=True)
class CodexProbeAttempt:
    path: str
    source: str
    state: CodexCLIState
    detail: str = ""
    version: str = ""
    schema_files: int = 0


@dataclass(frozen=True)
class CodexPreflightResult:
    state: CodexCLIState
    executable: str = ""
    source: str = ""
    version: str = ""
    protocol_family: str = CODEX_PROTOCOL_FAMILY
    schema_files: int = 0
    detail: str = ""
    attempts: tuple[CodexProbeAttempt, ...] = ()

    @property
    def ready(self) -> bool:
        return self.state is CodexCLIState.READY

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["state"] = self.state.value
        payload["attempts"] = [
            {**asdict(attempt), "state": attempt.state.value}
            for attempt in self.attempts
        ]
        payload["ready"] = self.ready
        return payload


def _normalise_candidate_path(value: str) -> str:
    expanded = os.path.expandvars(os.path.expanduser(value.strip().strip('"')))
    path = Path(expanded)
    if not path.is_absolute():
        path = Path.cwd() / path
    return str(path.resolve(strict=False))


def discover_codex_cli_candidates(
    configured_path: str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] = shutil.which,
    platform: str | None = None,
) -> tuple[CodexCLICandidate, ...]:
    """Discover a small, deterministic set of local CLI candidates."""

    env = os.environ if environ is None else environ
    system = sys.platform if platform is None else platform
    configured = (
        configured_path
        if configured_path is not None
        else env.get("LUMENA_CODEX_CLI_PATH", "")
    ).strip()
    candidates: list[CodexCLICandidate] = []
    seen: set[str] = set()

    def add(value: str | None, source: str, *, require_exists: bool = False) -> None:
        if not value:
            return
        path = _normalise_candidate_path(value)
        if require_exists and not Path(path).is_file():
            return
        key = os.path.normcase(path) if system == "win32" else path
        if key in seen:
            return
        seen.add(key)
        candidates.append(CodexCLICandidate(path=path, source=source))

    if configured:
        configured_resolved = None
        if not any(sep in configured for sep in ("/", "\\")):
            configured_resolved = which(configured)
        add(configured_resolved or configured, "configured")

    names: Sequence[str]
    if system == "win32":
        names = ("codex.exe", "codex.cmd", "codex.bat", "codex")
    else:
        names = ("codex",)
    for name in names:
        add(which(name), "path")

    if system == "win32":
        local_app = env.get("LOCALAPPDATA", "")
        roaming = env.get("APPDATA", "")
        if local_app:
            add(
                str(Path(local_app) / "Microsoft" / "WinGet" / "Links" / "codex.exe"),
                "winget",
                require_exists=True,
            )
            desktop_bin = Path(local_app) / "OpenAI" / "Codex" / "bin"
            try:
                desktop_candidates = sorted(
                    desktop_bin.glob("*/codex.exe"),
                    key=lambda item: item.stat().st_mtime,
                    reverse=True,
                )[:4]
            except OSError:
                desktop_candidates = []
            for desktop_executable in desktop_candidates:
                add(
                    str(desktop_executable),
                    "codex_desktop",
                    require_exists=True,
                )
        if roaming:
            add(
                str(Path(roaming) / "npm" / "codex.cmd"),
                "npm",
                require_exists=True,
            )
    else:
        add(
            str(Path.home() / ".local" / "bin" / "codex"),
            "user_local",
            require_exists=True,
        )

    return tuple(candidates)


_SECRET_RE = re.compile(
    r"(?i)(bearer\s+\S+|(?:sk|sess|eyJ)[-_A-Za-z0-9.]{12,})"
)


def _safe_detail(*values: str, limit: int = 280) -> str:
    text = " ".join(value.strip() for value in values if value and value.strip())
    text = " ".join(text.split())
    text = _SECRET_RE.sub("[REDACTED]", text)
    home = str(Path.home())
    if home:
        text = text.replace(home, "~")
    return text[:limit]


def _version_from_output(output: str) -> str:
    first_line = output.strip().splitlines()[0] if output.strip() else ""
    match = re.search(r"(?i)\bcodex(?:-cli)?\s+([^\s]+)", first_line)
    return match.group(1) if match else first_line[:80]


def _attempt(
    candidate: CodexCLICandidate,
    *,
    timeout_s: float,
    runner: Callable[..., subprocess.CompletedProcess[str]],
) -> CodexProbeAttempt:
    path = candidate.path
    if not Path(path).is_file():
        return CodexProbeAttempt(
            path=path,
            source=candidate.source,
            state=CodexCLIState.NOT_FOUND,
            detail="Configured executable does not exist.",
        )

    common_kwargs = {
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "timeout": timeout_s,
        "shell": False,
    }
    try:
        version_result = runner([path, "--version"], **common_kwargs)
    except PermissionError as exc:
        return CodexProbeAttempt(
            path=path,
            source=candidate.source,
            state=CodexCLIState.INACCESSIBLE,
            detail=_safe_detail(str(exc)) or "Permission denied while starting Codex CLI.",
        )
    except FileNotFoundError as exc:
        return CodexProbeAttempt(
            path=path,
            source=candidate.source,
            state=CodexCLIState.NOT_FOUND,
            detail=_safe_detail(str(exc)),
        )
    except subprocess.TimeoutExpired:
        return CodexProbeAttempt(
            path=path,
            source=candidate.source,
            state=CodexCLIState.TIMED_OUT,
            detail="Codex CLI version probe timed out.",
        )
    except OSError as exc:
        state = (
            CodexCLIState.INACCESSIBLE
            if getattr(exc, "winerror", None) == 5
            else CodexCLIState.BROKEN
        )
        return CodexProbeAttempt(
            path=path,
            source=candidate.source,
            state=state,
            detail=_safe_detail(str(exc)),
        )

    version_output = version_result.stdout or version_result.stderr or ""
    version = _version_from_output(version_output)
    if version_result.returncode != 0:
        return CodexProbeAttempt(
            path=path,
            source=candidate.source,
            state=CodexCLIState.BROKEN,
            detail=_safe_detail(version_result.stderr, version_result.stdout),
            version=version,
        )

    try:
        help_result = runner([path, "app-server", "--help"], **common_kwargs)
    except subprocess.TimeoutExpired:
        return CodexProbeAttempt(
            path=path,
            source=candidate.source,
            state=CodexCLIState.TIMED_OUT,
            detail="Codex app-server capability probe timed out.",
            version=version,
        )
    except (PermissionError, OSError) as exc:
        state = (
            CodexCLIState.INACCESSIBLE
            if isinstance(exc, PermissionError) or getattr(exc, "winerror", None) == 5
            else CodexCLIState.BROKEN
        )
        return CodexProbeAttempt(
            path=path,
            source=candidate.source,
            state=state,
            detail=_safe_detail(str(exc)),
            version=version,
        )
    if help_result.returncode != 0:
        return CodexProbeAttempt(
            path=path,
            source=candidate.source,
            state=CodexCLIState.APP_SERVER_UNSUPPORTED,
            detail=_safe_detail(help_result.stderr, help_result.stdout),
            version=version,
        )

    try:
        with tempfile.TemporaryDirectory(prefix="lumena-codex-schema-") as temp_dir:
            schema_result = runner(
                [
                    path,
                    "app-server",
                    "generate-json-schema",
                    "--out",
                    temp_dir,
                ],
                **common_kwargs,
            )
            if schema_result.returncode != 0:
                return CodexProbeAttempt(
                    path=path,
                    source=candidate.source,
                    state=CodexCLIState.PROTOCOL_INCOMPATIBLE,
                    detail=_safe_detail(schema_result.stderr, schema_result.stdout),
                    version=version,
                )
            schema_paths = tuple(Path(temp_dir).rglob("*.json"))
            if not schema_paths:
                return CodexProbeAttempt(
                    path=path,
                    source=candidate.source,
                    state=CodexCLIState.PROTOCOL_INCOMPATIBLE,
                    detail="Codex generated no JSON protocol schema.",
                    version=version,
                )
            for schema_path in schema_paths:
                with schema_path.open("r", encoding="utf-8") as handle:
                    json.load(handle)
    except subprocess.TimeoutExpired:
        return CodexProbeAttempt(
            path=path,
            source=candidate.source,
            state=CodexCLIState.TIMED_OUT,
            detail="Codex protocol schema probe timed out.",
            version=version,
        )
    except (PermissionError, OSError) as exc:
        state = (
            CodexCLIState.INACCESSIBLE
            if isinstance(exc, PermissionError) or getattr(exc, "winerror", None) == 5
            else CodexCLIState.BROKEN
        )
        return CodexProbeAttempt(
            path=path,
            source=candidate.source,
            state=state,
            detail=_safe_detail(str(exc)),
            version=version,
        )
    except (json.JSONDecodeError, UnicodeError) as exc:
        return CodexProbeAttempt(
            path=path,
            source=candidate.source,
            state=CodexCLIState.PROTOCOL_INCOMPATIBLE,
            detail=_safe_detail(str(exc)),
            version=version,
        )

    return CodexProbeAttempt(
        path=path,
        source=candidate.source,
        state=CodexCLIState.READY,
        detail="Codex CLI, app-server, and JSON schema are available.",
        version=version,
        schema_files=len(schema_paths),
    )


_FAILURE_PRIORITY: Mapping[CodexCLIState, int] = {
    CodexCLIState.INACCESSIBLE: 0,
    CodexCLIState.PROTOCOL_INCOMPATIBLE: 1,
    CodexCLIState.APP_SERVER_UNSUPPORTED: 2,
    CodexCLIState.TIMED_OUT: 3,
    CodexCLIState.BROKEN: 4,
    CodexCLIState.NOT_FOUND: 5,
    CodexCLIState.READY: 99,
}


def probe_codex_cli(
    configured_path: str | None = None,
    *,
    timeout_s: float = 8.0,
    environ: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] = shutil.which,
    platform: str | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> CodexPreflightResult:
    """Probe Codex without authentication, network calls, or token usage."""

    candidates = discover_codex_cli_candidates(
        configured_path,
        environ=environ,
        which=which,
        platform=platform,
    )
    if not candidates:
        return CodexPreflightResult(
            state=CodexCLIState.NOT_FOUND,
            detail="No executable Codex CLI candidate was found.",
        )

    attempts: list[CodexProbeAttempt] = []
    for candidate in candidates:
        attempt = _attempt(candidate, timeout_s=timeout_s, runner=runner)
        attempts.append(attempt)
        if attempt.state is CodexCLIState.READY:
            return CodexPreflightResult(
                state=attempt.state,
                executable=attempt.path,
                source=attempt.source,
                version=attempt.version,
                schema_files=attempt.schema_files,
                detail=attempt.detail,
                attempts=tuple(attempts),
            )

    best = min(attempts, key=lambda item: _FAILURE_PRIORITY[item.state])
    return CodexPreflightResult(
        state=best.state,
        executable=best.path,
        source=best.source,
        version=best.version,
        schema_files=best.schema_files,
        detail=best.detail,
        attempts=tuple(attempts),
    )


async def probe_codex_cli_async(
    configured_path: str | None = None,
    **kwargs,
) -> CodexPreflightResult:
    """Run the blocking local preflight outside Lumena's event loop."""

    return await asyncio.to_thread(probe_codex_cli, configured_path, **kwargs)


# ---------------------------------------------------------------------------
# S2 account, authentication, and quota facade
# ---------------------------------------------------------------------------

ACCOUNT_READ_METHOD = "account/read"
ACCOUNT_LOGIN_START_METHOD = "account/login/start"
ACCOUNT_LOGIN_CANCEL_METHOD = "account/login/cancel"
ACCOUNT_LOGOUT_METHOD = "account/logout"
ACCOUNT_RATE_LIMITS_READ_METHOD = "account/rateLimits/read"
MODEL_LIST_METHOD = "model/list"
ACCOUNT_LOGIN_COMPLETED_NOTIFICATION = "account/login/completed"
ACCOUNT_UPDATED_NOTIFICATION = "account/updated"
ACCOUNT_RATE_LIMITS_UPDATED_NOTIFICATION = "account/rateLimits/updated"

_CREDENTIAL_KEYS = frozenset(
    {
        "accesstoken",
        "refreshtoken",
        "idtoken",
        "apikey",
        "authorization",
        "cookie",
        "sessioncookie",
        "credential",
        "credentials",
        "password",
        "secret",
    }
)


class CodexAccountState(str, Enum):
    DISCONNECTED = "disconnected"
    CONNECTED = "connected"
    LOGIN_PENDING = "login_pending"
    SESSION_EXPIRED = "session_expired"
    QUOTA_EXHAUSTED = "quota_exhausted"
    ERROR = "error"


@dataclass(frozen=True)
class CodexAccountSummary:
    state: CodexAccountState
    account_type: str = ""
    plan_type: str = ""
    email_masked: str = ""
    workspace_name: str = ""
    message: str = ""

    @property
    def subscription_usable(self) -> bool:
        return self.state is CodexAccountState.CONNECTED and self.account_type in {
            "chatgpt",
            "chatgpt_team",
            "chatgpt_enterprise",
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "state": self.state.value,
            "subscription_usable": self.subscription_usable,
        }


@dataclass(frozen=True)
class CodexLoginChallenge:
    login_id: str
    auth_url: str = ""
    user_code: str = ""
    verification_url: str = ""
    expires_in_s: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CodexQuotaSummary:
    exhausted: bool
    primary_used_percent: float | None = None
    primary_resets_at: int | float | str | None = None
    secondary_used_percent: float | None = None
    secondary_resets_at: int | float | str | None = None
    raw: Mapping[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CodexModelSummary:
    """One model exposed by the connected Codex account."""

    model_id: str
    display_name: str
    description: str = ""
    is_default: bool = False
    reasoning_efforts: tuple[str, ...] = ()
    default_reasoning_effort: str = ""
    input_modalities: tuple[str, ...] = ()
    output_modalities: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CodexSubscriptionAccountError(RuntimeError):
    """An account payload is safe to expose but unusable for subscription mode."""


def _normalised_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def strip_codex_credentials(value: Any) -> Any:
    """Recursively remove credential fields from App Server payloads."""

    if isinstance(value, Mapping):
        clean: dict[str, Any] = {}
        for key, nested in value.items():
            if _normalised_key(key) in _CREDENTIAL_KEYS:
                continue
            clean[str(key)] = strip_codex_credentials(nested)
        return clean
    if isinstance(value, (list, tuple)):
        return [strip_codex_credentials(item) for item in value]
    return value


def mask_codex_email(value: Any) -> str:
    text = str(value or "").strip()
    if "@" not in text:
        return ""
    local, domain = text.rsplit("@", 1)
    if not local or not domain:
        return ""
    domain_name, dot, suffix = domain.partition(".")
    local_masked = local[:1] + "***"
    domain_masked = domain_name[:1] + "***"
    return f"{local_masked}@{domain_masked}{dot}{suffix}" if dot else f"{local_masked}@{domain_masked}"


def _first(mapping: Mapping[str, Any], *names: str, default: Any = "") -> Any:
    by_key = {_normalised_key(key): value for key, value in mapping.items()}
    for name in names:
        key = _normalised_key(name)
        if key in by_key and by_key[key] is not None:
            return by_key[key]
    return default


def normalise_codex_account(payload: Any) -> CodexAccountSummary:
    """Normalize account/read without ever retaining its credential fields."""

    clean = strip_codex_credentials(payload)
    if not isinstance(clean, Mapping):
        return CodexAccountSummary(state=CodexAccountState.DISCONNECTED)
    account = _first(clean, "account", default=clean)
    if account is None:
        return CodexAccountSummary(state=CodexAccountState.DISCONNECTED)
    if not isinstance(account, Mapping):
        return CodexAccountSummary(state=CodexAccountState.ERROR, message="Malformed account payload")

    raw_type = str(
        _first(account, "type", "accountType", "authMode", default="")
    ).strip().lower()
    aliases = {
        "chatgpt": "chatgpt",
        "chatgptaccount": "chatgpt",
        "chatgptteam": "chatgpt_team",
        "team": "chatgpt_team",
        "business": "chatgpt_team",
        "chatgptenterprise": "chatgpt_enterprise",
        "enterprise": "chatgpt_enterprise",
        "apikey": "api_key",
        "api": "api_key",
    }
    account_type = aliases.get(_normalised_key(raw_type), raw_type)
    email = _first(account, "email", "emailAddress", default="")
    plan = str(
        _first(account, "planType", "plan", "subscription", "tier", default="")
    ).strip()
    workspace = str(
        _first(account, "workspaceName", "workspace", "organizationName", default="")
    ).strip()
    if not account_type and not email and not plan:
        return CodexAccountSummary(state=CodexAccountState.DISCONNECTED)
    return CodexAccountSummary(
        state=CodexAccountState.CONNECTED,
        account_type=account_type,
        plan_type=plan,
        email_masked=mask_codex_email(email),
        workspace_name=workspace,
    )


def _safe_https_url(value: Any) -> str:
    text = str(value or "").strip()
    try:
        parsed = urlparse(text)
    except ValueError:
        return ""
    host = (parsed.hostname or "").lower()
    official_host = host in {"openai.com", "chatgpt.com"} or host.endswith(
        (".openai.com", ".chatgpt.com")
    )
    return text if parsed.scheme == "https" and official_host else ""


def normalise_codex_login_challenge(payload: Any) -> CodexLoginChallenge:
    clean = strip_codex_credentials(payload)
    if not isinstance(clean, Mapping):
        raise CodexSubscriptionAccountError("Codex returned an invalid login challenge")
    challenge = _first(clean, "login", "challenge", default=clean)
    if not isinstance(challenge, Mapping):
        raise CodexSubscriptionAccountError("Codex returned an invalid login challenge")
    login_id = str(_first(challenge, "loginId", "id", default="")).strip()
    if not login_id:
        raise CodexSubscriptionAccountError("Codex did not return a login id")
    auth_url = _safe_https_url(
        _first(challenge, "authUrl", "authorizationUrl", "url", default="")
    )
    verification_url = _safe_https_url(
        _first(challenge, "verificationUrl", "verificationUri", default="")
    )
    expires = _first(challenge, "expiresIn", "expiresInSeconds", default=None)
    try:
        expires_int = int(expires) if expires is not None else None
    except (TypeError, ValueError):
        expires_int = None
    return CodexLoginChallenge(
        login_id=login_id,
        auth_url=auth_url,
        user_code=str(_first(challenge, "userCode", "code", default="")).strip(),
        verification_url=verification_url,
        expires_in_s=expires_int,
    )


def _window_value(payload: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    value = _first(payload, name, default={})
    return value if isinstance(value, Mapping) else {}


def _percent(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return min(100.0, max(0.0, number))


def normalise_codex_rate_limits(payload: Any) -> CodexQuotaSummary:
    clean = strip_codex_credentials(payload)
    if not isinstance(clean, Mapping):
        clean = {}
    root = _first(clean, "rateLimits", "limits", default=clean)
    if not isinstance(root, Mapping):
        root = {}
    primary = _window_value(root, "primary")
    secondary = _window_value(root, "secondary")
    primary_used = _percent(_first(primary, "usedPercent", "percentUsed", default=None))
    secondary_used = _percent(
        _first(secondary, "usedPercent", "percentUsed", default=None)
    )
    exhausted_flag = bool(_first(root, "exhausted", "limitReached", default=False))
    exhausted = exhausted_flag or primary_used == 100.0 or secondary_used == 100.0
    return CodexQuotaSummary(
        exhausted=exhausted,
        primary_used_percent=primary_used,
        primary_resets_at=_first(primary, "resetsAt", "resetAt", default=None),
        secondary_used_percent=secondary_used,
        secondary_resets_at=_first(secondary, "resetsAt", "resetAt", default=None),
        raw=root,
    )


def _string_tuple(value: Any, *item_names: str) -> tuple[str, ...]:
    if isinstance(value, str):
        candidates: Sequence[Any] = (value,)
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        candidates = value
    else:
        return ()
    result: list[str] = []
    for item in candidates:
        if isinstance(item, Mapping):
            item = _first(item, *item_names, default="")
        text = str(item or "").strip()
        if text and text not in result:
            result.append(text)
    return tuple(result)


def normalise_codex_models(payload: Any) -> tuple[CodexModelSummary, ...]:
    """Normalize model/list while preserving the server's recommended order."""

    clean = strip_codex_credentials(payload)
    default_model = ""
    raw_models: Any = clean
    if isinstance(clean, Mapping):
        default_model = str(
            _first(clean, "defaultModel", "defaultModelId", "recommendedModel", default="")
        ).strip()
        raw_models = _first(clean, "models", "data", "items", default=[])
        if isinstance(raw_models, Mapping):
            raw_models = _first(raw_models, "models", "data", "items", default=[])
    if not isinstance(raw_models, Sequence) or isinstance(
        raw_models, (str, bytes, bytearray)
    ):
        return ()

    models: list[CodexModelSummary] = []
    seen: set[str] = set()
    for raw in raw_models:
        if not isinstance(raw, Mapping):
            continue
        model_id = str(_first(raw, "id", "model", "slug", default="")).strip()
        if not model_id or model_id in seen:
            continue
        hidden = bool(_first(raw, "hidden", "isHidden", default=False))
        available = _first(raw, "available", "enabled", default=True)
        if hidden or available is False:
            continue
        seen.add(model_id)
        display_name = str(
            _first(raw, "displayName", "name", "title", default=model_id)
        ).strip() or model_id
        description = " ".join(
            str(_first(raw, "description", "summary", default="")).split()
        )[:400]
        reasoning_efforts = _string_tuple(
            _first(
                raw,
                "supportedReasoningEfforts",
                "reasoningEfforts",
                default=[],
            ),
            "reasoningEffort",
            "effort",
            "value",
            "id",
        )
        input_modalities = _string_tuple(
            _first(raw, "inputModalities", "input", default=[]),
            "type",
            "modality",
            "value",
        )
        output_modalities = _string_tuple(
            _first(raw, "outputModalities", "output", default=[]),
            "type",
            "modality",
            "value",
        )
        is_default = bool(
            _first(raw, "isDefault", "default", "recommended", default=False)
        ) or model_id == default_model
        models.append(
            CodexModelSummary(
                model_id=model_id,
                display_name=display_name,
                description=description,
                is_default=is_default,
                reasoning_efforts=reasoning_efforts,
                default_reasoning_effort=str(
                    _first(raw, "defaultReasoningEffort", "reasoningEffort", default="")
                ).strip(),
                input_modalities=input_modalities,
                output_modalities=output_modalities,
            )
        )
    return tuple(models)


class CodexSubscriptionGateway:
    """Secret-free account operations over an initialized App Server."""

    def __init__(self, supervisor: CodexAppServerSupervisor):
        self.supervisor = supervisor

    async def read_account(self, *, refresh: bool = False) -> CodexAccountSummary:
        payload = await self.supervisor.request(
            ACCOUNT_READ_METHOD, {"refreshToken": bool(refresh)}
        )
        return normalise_codex_account(payload)

    async def require_chatgpt_account(self, *, refresh: bool = False) -> CodexAccountSummary:
        account = await self.read_account(refresh=refresh)
        if not account.subscription_usable:
            if account.account_type == "api_key":
                message = "Codex is authenticated with an API key, not a ChatGPT subscription"
            else:
                message = "No usable ChatGPT subscription session is connected"
            raise CodexSubscriptionAccountError(message)
        return account

    async def start_login(self) -> CodexLoginChallenge:
        payload = await self.supervisor.request(
            ACCOUNT_LOGIN_START_METHOD, {"type": "chatgpt"}
        )
        return normalise_codex_login_challenge(payload)

    async def cancel_login(self, login_id: str) -> None:
        if not str(login_id).strip():
            raise CodexSubscriptionAccountError("A login id is required")
        await self.supervisor.request(
            ACCOUNT_LOGIN_CANCEL_METHOD, {"loginId": str(login_id).strip()}
        )

    async def logout(self) -> None:
        await self.supervisor.request(ACCOUNT_LOGOUT_METHOD, {})

    async def read_rate_limits(self) -> CodexQuotaSummary:
        payload = await self.supervisor.request(ACCOUNT_RATE_LIMITS_READ_METHOD, {})
        return normalise_codex_rate_limits(payload)

    async def list_models(self) -> tuple[CodexModelSummary, ...]:
        payload = await self.supervisor.request(MODEL_LIST_METHOD, {})
        return normalise_codex_models(payload)

    async def wait_for_account_update(
        self, *, timeout: float = 30.0
    ) -> CodexAccountSummary:
        notification = await self.supervisor.wait_for_notification(
            ACCOUNT_UPDATED_NOTIFICATION, timeout=timeout
        )
        account = normalise_codex_account(notification.params)
        if account.state is CodexAccountState.DISCONNECTED:
            return await self.read_account()
        return account

    async def wait_for_rate_limits_update(
        self, *, timeout: float = 30.0
    ) -> CodexQuotaSummary:
        notification = await self.supervisor.wait_for_notification(
            ACCOUNT_RATE_LIMITS_UPDATED_NOTIFICATION, timeout=timeout
        )
        return normalise_codex_rate_limits(notification.params)

    async def wait_for_login(
        self, login_id: str, *, timeout: float = 120.0
    ) -> CodexAccountSummary:
        login_id = str(login_id).strip()

        def matches(notification: CodexNotification) -> bool:
            params = notification.params
            if not isinstance(params, Mapping):
                return not login_id
            observed = str(_first(params, "loginId", "id", default="")).strip()
            return not observed or observed == login_id

        await self.supervisor.wait_for_notification(
            ACCOUNT_LOGIN_COMPLETED_NOTIFICATION,
            predicate=matches,
            timeout=timeout,
        )
        return await self.require_chatgpt_account(refresh=True)
