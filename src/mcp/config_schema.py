"""
config_schema.py — Schéma universel de configuration d'un MCP (Phase I-1).

Dataclasses immuables décrivant la configuration requise par n'importe quel
MCP : secrets API, OAuth flows, paramètres non-sensibles, validations.

Doctrine :
  - Un schéma = une description STATIQUE de ce qu'un MCP attend en env.
  - 3 niveaux de sensibilité : SECRET (chiffré Fernet) | SENSITIVE (JSON masqué
    UI) | NORMAL (JSON clair).
  - Sérialisation JSON stable pour persistance dans ServerEntry.config_schema.
  - Aucun stockage de VALEURS ici : juste la description des champs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


# ──────────────────────────────────────────────────────────────────────────────
# Enums
# ──────────────────────────────────────────────────────────────────────────────


class ConfigKind(str, Enum):
    """Type technique du champ — détermine le rendu UI et la validation."""
    # Secrets (chiffrement obligatoire via SecretsService)
    SECRET_API_KEY = "secret_api_key"
    SECRET_TOKEN = "secret_token"
    SECRET_PASSWORD = "secret_password"
    OAUTH_CLIENT_ID = "oauth_client_id"
    OAUTH_CLIENT_SECRET = "oauth_client_secret"
    OAUTH_REFRESH_TOKEN = "oauth_refresh_token"
    OAUTH_ACCESS_TOKEN = "oauth_access_token"
    # Sensibles (JSON sur disque, masqué par défaut UI)
    WEBHOOK_URL = "webhook_url"
    CONNECTION_STRING = "connection_string"
    SSH_KEY_PATH = "ssh_key_path"
    # Normaux (JSON sur disque, affiché en clair)
    PATH_FILE = "path_file"
    PATH_DIR = "path_dir"
    PATH_LIST = "path_list"
    URL = "url"
    EMAIL = "email"
    STRING = "string"
    INTEGER = "integer"
    BOOLEAN = "boolean"
    ENUM = "enum"
    JSON = "json"


class Sensitivity(str, Enum):
    """Niveau de sensibilité — pilote stockage et affichage."""
    SECRET = "secret"        # SecretsService chiffré, masqué dans UI
    SENSITIVE = "sensitive"  # JSON sur disque, masqué par défaut UI
    NORMAL = "normal"        # JSON sur disque, affiché en clair


_KIND_TO_DEFAULT_SENSITIVITY: Dict[ConfigKind, Sensitivity] = {
    ConfigKind.SECRET_API_KEY: Sensitivity.SECRET,
    ConfigKind.SECRET_TOKEN: Sensitivity.SECRET,
    ConfigKind.SECRET_PASSWORD: Sensitivity.SECRET,
    ConfigKind.OAUTH_CLIENT_ID: Sensitivity.SECRET,
    ConfigKind.OAUTH_CLIENT_SECRET: Sensitivity.SECRET,
    ConfigKind.OAUTH_REFRESH_TOKEN: Sensitivity.SECRET,
    ConfigKind.OAUTH_ACCESS_TOKEN: Sensitivity.SECRET,
    ConfigKind.WEBHOOK_URL: Sensitivity.SENSITIVE,
    ConfigKind.CONNECTION_STRING: Sensitivity.SENSITIVE,
    ConfigKind.SSH_KEY_PATH: Sensitivity.SENSITIVE,
    ConfigKind.PATH_FILE: Sensitivity.NORMAL,
    ConfigKind.PATH_DIR: Sensitivity.NORMAL,
    ConfigKind.PATH_LIST: Sensitivity.NORMAL,
    ConfigKind.URL: Sensitivity.NORMAL,
    ConfigKind.EMAIL: Sensitivity.NORMAL,
    ConfigKind.STRING: Sensitivity.NORMAL,
    ConfigKind.INTEGER: Sensitivity.NORMAL,
    ConfigKind.BOOLEAN: Sensitivity.NORMAL,
    ConfigKind.ENUM: Sensitivity.NORMAL,
    ConfigKind.JSON: Sensitivity.NORMAL,
}


def default_sensitivity_for(kind: ConfigKind) -> Sensitivity:
    """Sensibilité par défaut d'un kind donné."""
    return _KIND_TO_DEFAULT_SENSITIVITY.get(kind, Sensitivity.NORMAL)


# ──────────────────────────────────────────────────────────────────────────────
# Validation
# ──────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ValidationRule:
    """Règle de validation optionnelle d'un champ."""
    regex: Optional[str] = None       # pattern à matcher
    min_length: Optional[int] = None
    max_length: Optional[int] = None
    min_value: Optional[int] = None   # pour INTEGER
    max_value: Optional[int] = None
    choices: Tuple[str, ...] = ()     # pour ENUM


# ──────────────────────────────────────────────────────────────────────────────
# Auth flows (OAuth, device flow, etc.)
# ──────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AuthFlow:
    """Description d'un flux d'authentification (OAuth2, device flow, etc.)."""
    kind: str                                # "oauth2_authorization_code" | "device_flow" | "api_key"
    provider: str                            # "google", "github", "microsoft", "slack"
    authorize_url: Optional[str] = None
    token_url: Optional[str] = None
    redirect_uri: Optional[str] = None
    scopes: Tuple[str, ...] = ()
    docs_url: Optional[str] = None


# ──────────────────────────────────────────────────────────────────────────────
# ConfigField
# ──────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ConfigField:
    """Description d'un champ de configuration d'un MCP."""
    name: str                                  # Ex: "SLACK_BOT_TOKEN"
    label: str                                 # Ex: "Token Bot Slack"
    description: str                           # Explication user-friendly
    kind: ConfigKind                           # Type technique
    sensitivity: Sensitivity                   # SECRET / SENSITIVE / NORMAL
    required: bool = True
    default: Optional[str] = None
    placeholder: Optional[str] = None          # Ex: "xoxb-..."
    obtained_from: Optional[str] = None        # Ex: "api.slack.com/apps → OAuth"
    docs_url: Optional[str] = None
    validation: Optional[ValidationRule] = None
    group: Optional[str] = None                # Ex: "Authentification"
    depends_on: Optional[str] = None           # Affichage conditionnel
    autonomy_resolvable: bool = False          # Lumena peut deviner seule ?


# ──────────────────────────────────────────────────────────────────────────────
# MCPConfigSchema
# ──────────────────────────────────────────────────────────────────────────────


_VALID_DETECTION_SOURCES = frozenset({
    "curated",   # KNOWN_MCPS hand-crafted
    "package",   # extraction README/package.json
    "probe",     # probe runtime
    "user",      # chat fallback
    "edited",    # user a édité manuellement
})


@dataclass(frozen=True)
class MCPConfigSchema:
    """Schéma complet de configuration d'un MCP."""
    server_id: str
    fields: Tuple[ConfigField, ...]
    auth_flows: Tuple[AuthFlow, ...] = ()
    detected_from: str = "curated"
    detected_at: str = ""        # ISO timestamp

    def field_names(self) -> List[str]:
        return [f.name for f in self.fields]

    def required_field_names(self) -> List[str]:
        return [f.name for f in self.fields if f.required]

    def secret_field_names(self) -> List[str]:
        return [
            f.name for f in self.fields
            if f.sensitivity == Sensitivity.SECRET
        ]

    def non_secret_field_names(self) -> List[str]:
        return [
            f.name for f in self.fields
            if f.sensitivity != Sensitivity.SECRET
        ]

    def get_field(self, name: str) -> Optional[ConfigField]:
        for f in self.fields:
            if f.name == name:
                return f
        return None


# ──────────────────────────────────────────────────────────────────────────────
# Sérialisation JSON (pour persistance dans ServerEntry.config_schema)
# ──────────────────────────────────────────────────────────────────────────────


def validation_to_dict(v: ValidationRule) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if v.regex is not None:
        out["regex"] = v.regex
    if v.min_length is not None:
        out["min_length"] = v.min_length
    if v.max_length is not None:
        out["max_length"] = v.max_length
    if v.min_value is not None:
        out["min_value"] = v.min_value
    if v.max_value is not None:
        out["max_value"] = v.max_value
    if v.choices:
        out["choices"] = list(v.choices)
    return out


def validation_from_dict(d: Dict[str, Any]) -> ValidationRule:
    return ValidationRule(
        regex=d.get("regex"),
        min_length=d.get("min_length"),
        max_length=d.get("max_length"),
        min_value=d.get("min_value"),
        max_value=d.get("max_value"),
        choices=tuple(d.get("choices", []) or []),
    )


def auth_flow_to_dict(a: AuthFlow) -> Dict[str, Any]:
    return {
        "kind": a.kind,
        "provider": a.provider,
        "authorize_url": a.authorize_url,
        "token_url": a.token_url,
        "redirect_uri": a.redirect_uri,
        "scopes": list(a.scopes),
        "docs_url": a.docs_url,
    }


def auth_flow_from_dict(d: Dict[str, Any]) -> AuthFlow:
    return AuthFlow(
        kind=str(d.get("kind", "")),
        provider=str(d.get("provider", "")),
        authorize_url=d.get("authorize_url"),
        token_url=d.get("token_url"),
        redirect_uri=d.get("redirect_uri"),
        scopes=tuple(d.get("scopes", []) or []),
        docs_url=d.get("docs_url"),
    )


def config_field_to_dict(f: ConfigField) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "name": f.name,
        "label": f.label,
        "description": f.description,
        "kind": f.kind.value,
        "sensitivity": f.sensitivity.value,
        "required": f.required,
    }
    if f.default is not None:
        out["default"] = f.default
    if f.placeholder is not None:
        out["placeholder"] = f.placeholder
    if f.obtained_from is not None:
        out["obtained_from"] = f.obtained_from
    if f.docs_url is not None:
        out["docs_url"] = f.docs_url
    if f.validation is not None:
        out["validation"] = validation_to_dict(f.validation)
    if f.group is not None:
        out["group"] = f.group
    if f.depends_on is not None:
        out["depends_on"] = f.depends_on
    if f.autonomy_resolvable:
        out["autonomy_resolvable"] = True
    return out


def config_field_from_dict(d: Dict[str, Any]) -> Optional[ConfigField]:
    try:
        kind = ConfigKind(d["kind"])
        sensitivity = Sensitivity(d["sensitivity"])
        validation = None
        if isinstance(d.get("validation"), dict):
            validation = validation_from_dict(d["validation"])
        return ConfigField(
            name=str(d["name"]),
            label=str(d["label"]),
            description=str(d["description"]),
            kind=kind,
            sensitivity=sensitivity,
            required=bool(d.get("required", True)),
            default=d.get("default"),
            placeholder=d.get("placeholder"),
            obtained_from=d.get("obtained_from"),
            docs_url=d.get("docs_url"),
            validation=validation,
            group=d.get("group"),
            depends_on=d.get("depends_on"),
            autonomy_resolvable=bool(d.get("autonomy_resolvable", False)),
        )
    except (KeyError, ValueError, TypeError):
        return None


def schema_to_dict(s: MCPConfigSchema) -> Dict[str, Any]:
    return {
        "server_id": s.server_id,
        "fields": [config_field_to_dict(f) for f in s.fields],
        "auth_flows": [auth_flow_to_dict(a) for a in s.auth_flows],
        "detected_from": s.detected_from,
        "detected_at": s.detected_at,
    }


def schema_from_dict(d: Dict[str, Any]) -> Optional[MCPConfigSchema]:
    """Reconstruit un schéma depuis sa forme JSON. Tolérant : champs invalides
    sont skippés. Retourne None si structure complètement invalide."""
    if not isinstance(d, dict):
        return None
    server_id = d.get("server_id")
    if not isinstance(server_id, str) or not server_id:
        return None
    raw_fields = d.get("fields", [])
    if not isinstance(raw_fields, list):
        raw_fields = []
    fields: List[ConfigField] = []
    for entry in raw_fields:
        if not isinstance(entry, dict):
            continue
        f = config_field_from_dict(entry)
        if f is not None:
            fields.append(f)
    raw_flows = d.get("auth_flows", [])
    if not isinstance(raw_flows, list):
        raw_flows = []
    auth_flows: List[AuthFlow] = []
    for entry in raw_flows:
        if isinstance(entry, dict):
            try:
                auth_flows.append(auth_flow_from_dict(entry))
            except Exception:  # noqa: BLE001
                continue
    detected_from = d.get("detected_from", "curated")
    if detected_from not in _VALID_DETECTION_SOURCES:
        detected_from = "curated"
    return MCPConfigSchema(
        server_id=server_id,
        fields=tuple(fields),
        auth_flows=tuple(auth_flows),
        detected_from=str(detected_from),
        detected_at=str(d.get("detected_at", "")),
    )


__all__ = [
    "ConfigKind",
    "Sensitivity",
    "ValidationRule",
    "AuthFlow",
    "ConfigField",
    "MCPConfigSchema",
    "default_sensitivity_for",
    "validation_to_dict",
    "validation_from_dict",
    "auth_flow_to_dict",
    "auth_flow_from_dict",
    "config_field_to_dict",
    "config_field_from_dict",
    "schema_to_dict",
    "schema_from_dict",
]
