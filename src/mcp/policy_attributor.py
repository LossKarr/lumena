"""
policy_attributor.py — PolicyAttributor (Phase 16 v2).

Décide une MCPPolicy à attribuer à un nouvel outil MCP découvert, sur la
base de son nom (et optionnellement de sa description) + d'un trust_score
du package source.

DOCTRINE Phase 16 :
  - Décision pure : entrée → AttributionDecision. Aucun side-effect autre
    qu'audit.
  - Aucun câblage runtime : pas d'écriture dans ToolRegistry, pas de
    découverte, pas d'install.
  - Aucune touche à : tool_registry.py, react.py, sub_agent.py,
    orchestrator.py, server_catalog.py, policy_resolver.py,
    MCPSandboxRunner, MCPClient, approval_queue.py, policy.py,
    auto_approve.py, runtime_watcher.py.
  - Conservateur par défaut : si plusieurs catégories matchent, on retient
    la PLUS RESTRICTIVE. Si rien ne matche dans le tool_name et que la
    description ne donne qu'un signal "read-level", on REFUSE.

Trust gating (après classification) :
  - READ_ONLY / EXTERNAL_READ        → pas de gate
  - LOCAL_WRITE / EXTERNAL_WRITE_*   → trust_score requis ≥ 70
  - SECRETS_AUTH                     → trust_score requis ≥ 90
  (Pas de paramètre require_trust_score : la nécessité d'un trust score
  est entièrement dérivée de la policy classifiée.)

Description = ESCALATION ONLY :
  La description peut ESCALADER vers une policy plus restrictive que celle
  inférée du tool_name. Elle ne peut JAMAIS attribuer seule READ_ONLY ou
  EXTERNAL_READ : si tool_name ne matche rien et que la description ne
  matche que des keywords read-level, on REFUSE avec reason="no_keyword_match".

LOCAL_WRITE = EXPLICIT ONLY :
  save/store/persist seuls sont classés EXTERNAL_WRITE_RECOVERABLE (la
  majorité des MCP servers utilisent ces verbes pour des persistances
  distantes). LOCAL_WRITE n'est attribué que sur signaux explicitement
  locaux : write_file, write_local, local_save, local_store, cache_local,
  cache_file, bigrammes (local, save), (local, store), (write, file), etc.

Audit forensique sans PII :
  - Whitelist : server_id, tool_name, policy, reason, matched_keywords,
    trust_score_used, classified_policy, ts.
  - JAMAIS : description (peut contenir docstring PII), input_schema (peut
    contenir noms de champs sensibles ou exemples), stringification de
    l'attributor.

Layout disque :
  DATA_DIR/mcp_policy_attributor/audit.jsonl
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, Optional, Tuple

from loguru import logger

from src.mcp.policy import MCPPolicy
from src.utils.paths import DATA_DIR


# ──────────────────────────────────────────────────────────────────────────────
# Constantes
# ──────────────────────────────────────────────────────────────────────────────

_DEFAULT_DIRNAME = "mcp_policy_attributor"
_AUDIT_FILENAME = "audit.jsonl"

_DEFAULT_MIN_TRUST_FOR_WRITE = 70
_DEFAULT_MIN_TRUST_FOR_SECRETS = 90

# Validations
_SERVER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.\-]{0,63}$")
# Fix AZ (Phase I-8) : casse libre — la spec MCP ne l'impose pas
# (windows-mcp expose App/Click/PowerShell). La classification par
# mots-clés est insensible à la casse (text.lower() en aval).
_TOOL_NAME_LOCAL_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,128}$")
_TOKEN_SPLIT_RE = re.compile(r"[_\-.\s\t]+")
_MAX_DESCRIPTION_LEN = 4096

_WINDOWS_RESERVED_NAMES: FrozenSet[str] = frozenset({
    "con", "prn", "aux", "nul",
    "com1", "com2", "com3", "com4", "com5",
    "com6", "com7", "com8", "com9",
    "lpt1", "lpt2", "lpt3", "lpt4", "lpt5",
    "lpt6", "lpt7", "lpt8", "lpt9",
})

# Priorité (du plus restrictif au moins) — pour multi-match.
#
# Note importante : LOCAL_WRITE est placé AU-DESSUS de
# EXTERNAL_WRITE_RECOVERABLE, pour que les signaux locaux explicites
# (bigrammes (local, save), (write, file), etc.) battent les verbes
# génériques save/store/persist qui matchent EXTERNAL_WRITE_RECOVERABLE.
# Sinon "local_save_doc" serait classé RECOVERABLE alors qu'il est
# explicitement local. EXTERNAL_WRITE_IRREVERSIBLE reste au-dessus pour
# que "delete_local_file" reste IRREVERSIBLE.
_POLICY_PRIORITY: Dict[MCPPolicy, int] = {
    MCPPolicy.SECRETS_AUTH:                6,
    MCPPolicy.EXTERNAL_WRITE_IRREVERSIBLE: 5,
    MCPPolicy.LOCAL_WRITE:                 4,
    MCPPolicy.EXTERNAL_WRITE_RECOVERABLE:  3,
    MCPPolicy.EXTERNAL_READ:               2,
    MCPPolicy.READ_ONLY:                   1,
}

# Le seuil au-delà duquel une policy nécessite un trust score "write"
_WRITE_POLICIES: FrozenSet[MCPPolicy] = frozenset({
    MCPPolicy.LOCAL_WRITE,
    MCPPolicy.EXTERNAL_WRITE_RECOVERABLE,
    MCPPolicy.EXTERNAL_WRITE_IRREVERSIBLE,
})

_READ_POLICIES: FrozenSet[MCPPolicy] = frozenset({
    MCPPolicy.READ_ONLY,
    MCPPolicy.EXTERNAL_READ,
})

# ── Keyword whitelists ────────────────────────────────────────────────────────

_SECRETS_AUTH_KEYWORDS: FrozenSet[str] = frozenset({
    "auth", "authorize", "login", "logout",
    "token", "tokens",
    "credential", "credentials",
    "secret", "secrets",
    "password", "passwords",
    "oauth",
    "apikey", "apikeys",
})

_WRITE_IRREVERSIBLE_KEYWORDS: FrozenSet[str] = frozenset({
    "delete", "destroy", "drop", "purge",
    "uninstall", "wipe", "truncate",
    "kill", "terminate", "shutdown",
    "exec", "execute", "run", "eval", "shell",
})

_WRITE_RECOVERABLE_KEYWORDS: FrozenSet[str] = frozenset({
    "send", "create", "post", "publish",
    "add", "insert", "update", "edit", "patch",
    "set", "schedule", "submit", "reply",
    # Voir docstring : save/store/persist sont classés ici par défaut.
    "save", "store", "persist",
})

_LOCAL_WRITE_KEYWORDS: FrozenSet[str] = frozenset({
    "write_file", "write_local",
    "local_save", "local_store",
    "cache_local", "cache_file",
})

_EXTERNAL_READ_KEYWORDS: FrozenSet[str] = frozenset({
    "fetch", "download", "scrape", "browse",
    "external", "remote",
})

_READ_ONLY_KEYWORDS: FrozenSet[str] = frozenset({
    "read", "get", "list", "search", "find",
    "view", "show", "describe", "query",
    "check", "status", "lookup", "inspect",
})

# ── Bigram whitelists (paires de tokens consécutifs) ──────────────────────────

_SECRETS_AUTH_BIGRAMS: FrozenSet[Tuple[str, str]] = frozenset({
    ("api", "key"), ("api", "keys"),
    ("refresh", "token"), ("refresh", "tokens"),
    ("access", "token"), ("access", "tokens"),
})

_LOCAL_WRITE_BIGRAMS: FrozenSet[Tuple[str, str]] = frozenset({
    ("local", "write"), ("local", "save"),
    ("local", "store"), ("local", "cache"),
    ("write", "local"),
    ("file", "write"), ("write", "file"),
    ("cache", "file"), ("cache", "local"),
})

# Tables consolidées : catégorie → (keywords, bigrams)
_KEYWORDS_BY_POLICY: Dict[MCPPolicy, FrozenSet[str]] = {
    MCPPolicy.SECRETS_AUTH:                _SECRETS_AUTH_KEYWORDS,
    MCPPolicy.EXTERNAL_WRITE_IRREVERSIBLE: _WRITE_IRREVERSIBLE_KEYWORDS,
    MCPPolicy.EXTERNAL_WRITE_RECOVERABLE:  _WRITE_RECOVERABLE_KEYWORDS,
    MCPPolicy.LOCAL_WRITE:                 _LOCAL_WRITE_KEYWORDS,
    MCPPolicy.EXTERNAL_READ:               _EXTERNAL_READ_KEYWORDS,
    MCPPolicy.READ_ONLY:                   _READ_ONLY_KEYWORDS,
}

_BIGRAMS_BY_POLICY: Dict[MCPPolicy, FrozenSet[Tuple[str, str]]] = {
    MCPPolicy.SECRETS_AUTH: _SECRETS_AUTH_BIGRAMS,
    MCPPolicy.LOCAL_WRITE:  _LOCAL_WRITE_BIGRAMS,
}


# ──────────────────────────────────────────────────────────────────────────────
# Dataclasses
# ──────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ToolMetadata:
    server_id: str
    tool_name: str
    description: Optional[str] = None
    input_schema: Optional[Dict[str, Any]] = None


@dataclass(frozen=True)
class AttributionDecision:
    policy: Optional[MCPPolicy]
    reason: str
    server_id: str
    tool_name: str
    matched_keywords: List[str] = field(default_factory=list)
    trust_score_used: Optional[int] = None
    classified_policy: Optional[MCPPolicy] = None


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _tokenize(text: Optional[str]) -> List[str]:
    if not text:
        return []
    lower = text.lower()
    return [t for t in _TOKEN_SPLIT_RE.split(lower) if t]


def _bigrams(tokens: List[str]) -> List[Tuple[str, str]]:
    return list(zip(tokens, tokens[1:]))


# ──────────────────────────────────────────────────────────────────────────────
# Validators (raise ValueError avec code court sans PII)
# ──────────────────────────────────────────────────────────────────────────────


class _MetadataInvalid(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _validate_server_id(server_id: Any) -> None:
    if not isinstance(server_id, str) or not _SERVER_ID_RE.match(server_id):
        raise _MetadataInvalid("metadata_invalid:server_id")
    if ".." in server_id or "/" in server_id or "\\" in server_id:
        raise _MetadataInvalid("metadata_invalid:server_id")
    stem = server_id.split(".", 1)[0]
    if stem in _WINDOWS_RESERVED_NAMES:
        raise _MetadataInvalid("metadata_invalid:server_id")


def _validate_tool_name_local(tool_name: Any) -> None:
    if not isinstance(tool_name, str) or not _TOOL_NAME_LOCAL_RE.match(tool_name):
        raise _MetadataInvalid("metadata_invalid:tool_name")


def _validate_description(description: Any) -> None:
    if description is None:
        return
    if not isinstance(description, str):
        raise _MetadataInvalid("metadata_invalid:description_type")
    if len(description) > _MAX_DESCRIPTION_LEN:
        raise _MetadataInvalid("metadata_invalid:description_too_long")
    for ch in description:
        # Control chars autorisés : tab, newline, carriage return.
        if ord(ch) < 0x20 and ch not in ("\t", "\n", "\r"):
            raise _MetadataInvalid("metadata_invalid:description_control_char")
        if ord(ch) == 0x7f:
            raise _MetadataInvalid("metadata_invalid:description_control_char")


def _validate_input_schema(input_schema: Any) -> None:
    if input_schema is None:
        return
    if not isinstance(input_schema, dict):
        raise _MetadataInvalid("metadata_invalid:input_schema_type")


def _validate_trust_score(trust_score: Any) -> None:
    if trust_score is None:
        return
    if isinstance(trust_score, bool):
        raise _MetadataInvalid("metadata_invalid:trust_score_bool")
    if not isinstance(trust_score, int):
        raise _MetadataInvalid("metadata_invalid:trust_score_type")
    if trust_score < 0 or trust_score > 100:
        raise _MetadataInvalid("metadata_invalid:trust_score_range")


# ──────────────────────────────────────────────────────────────────────────────
# Classification
# ──────────────────────────────────────────────────────────────────────────────


def _classify_tokens(
    tokens: List[str],
) -> Tuple[Optional[MCPPolicy], List[str]]:
    """Classifie une liste de tokens.

    Returns (policy, matched_keywords) où matched_keywords contient
    UNIQUEMENT des codes whitelist/bigrammes (jamais des tokens raw
    arbitraires non-whitelisted).
    """
    matched_per_cat: Dict[MCPPolicy, bool] = {}
    matched_kws: List[str] = []
    bigs = _bigrams(tokens)

    # Match keywords single tokens
    for policy, keywords in _KEYWORDS_BY_POLICY.items():
        for tok in tokens:
            if tok in keywords:
                matched_per_cat[policy] = True
                if tok not in matched_kws:
                    matched_kws.append(tok)

    # Match bigrams
    for policy, bigrams_set in _BIGRAMS_BY_POLICY.items():
        for a, b in bigs:
            if (a, b) in bigrams_set:
                matched_per_cat[policy] = True
                bigram_code = f"{a}_{b}"
                if bigram_code not in matched_kws:
                    matched_kws.append(bigram_code)

    if not matched_per_cat:
        return None, []

    winning = max(matched_per_cat.keys(), key=lambda p: _POLICY_PRIORITY[p])
    return winning, matched_kws


def _classify(
    tool_name: str, description: Optional[str]
) -> Tuple[Optional[MCPPolicy], List[str]]:
    """Classification finale (avec règle d'escalation pour description).

    La description peut ESCALADER vers une policy plus restrictive que
    celle inférée du tool_name. Elle ne peut JAMAIS attribuer seule
    READ_ONLY ou EXTERNAL_READ.
    """
    tokens_name = _tokenize(tool_name)
    pol_name, kws_name = _classify_tokens(tokens_name)

    tokens_desc = _tokenize(description) if description else []
    pol_desc, kws_desc = _classify_tokens(tokens_desc) if tokens_desc else (None, [])

    # Cas 1 : tool_name n'a rien matché.
    if pol_name is None:
        if pol_desc is None:
            return None, []
        # Description seule ne peut JAMAIS attribuer READ_ONLY ou EXTERNAL_READ
        if pol_desc in _READ_POLICIES:
            return None, []
        # Description a un signal restrictif (LOCAL_WRITE+) → on l'utilise
        return pol_desc, list(dict.fromkeys(kws_name + kws_desc))

    # Cas 2 : tool_name a matché. Description peut escalader, jamais dégrader.
    if pol_desc is None:
        return pol_name, kws_name

    # Les deux ont matché : on garde la plus restrictive
    if _POLICY_PRIORITY[pol_desc] > _POLICY_PRIORITY[pol_name]:
        final = pol_desc
    else:
        final = pol_name
    return final, list(dict.fromkeys(kws_name + kws_desc))


# ──────────────────────────────────────────────────────────────────────────────
# PolicyAttributor
# ──────────────────────────────────────────────────────────────────────────────


class PolicyAttributor:
    """Décide une MCPPolicy pour un outil MCP découvert."""

    def __init__(
        self,
        *,
        min_trust_score_for_write: int = _DEFAULT_MIN_TRUST_FOR_WRITE,
        min_trust_score_for_secrets: int = _DEFAULT_MIN_TRUST_FOR_SECRETS,
        audit_log_path: Optional[Path] = None,
    ):
        if not isinstance(min_trust_score_for_write, int) or isinstance(
            min_trust_score_for_write, bool
        ):
            raise ValueError("min_trust_score_for_write must be int")
        if not isinstance(min_trust_score_for_secrets, int) or isinstance(
            min_trust_score_for_secrets, bool
        ):
            raise ValueError("min_trust_score_for_secrets must be int")
        if not (0 <= min_trust_score_for_write <= 100):
            raise ValueError("min_trust_score_for_write must be in [0,100]")
        if not (0 <= min_trust_score_for_secrets <= 100):
            raise ValueError("min_trust_score_for_secrets must be in [0,100]")
        if min_trust_score_for_secrets < min_trust_score_for_write:
            raise ValueError(
                "min_trust_score_for_secrets must be >= min_trust_score_for_write"
            )

        self._min_trust_write = min_trust_score_for_write
        self._min_trust_secrets = min_trust_score_for_secrets
        self._audit_log_path = audit_log_path or (
            DATA_DIR / _DEFAULT_DIRNAME / _AUDIT_FILENAME
        )
        self._audit_log_path.parent.mkdir(parents=True, exist_ok=True)

    # ── Properties ────────────────────────────────────────────────────────

    @property
    def min_trust_score_for_write(self) -> int:
        return self._min_trust_write

    @property
    def min_trust_score_for_secrets(self) -> int:
        return self._min_trust_secrets

    @property
    def audit_log_path(self) -> Path:
        return self._audit_log_path

    # ── Audit (whitelist stricte) ─────────────────────────────────────────

    def _audit(self, event: str, **fields: Any) -> None:
        """Append-only audit.

        Whitelist : server_id, tool_name, policy, reason, matched_keywords,
        trust_score_used, classified_policy, ts.
        INTERDIT : description, input_schema, stringification.
        """
        record = {"ts": _now_iso(), "event": event, **fields}
        try:
            self._audit_log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._audit_log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as e:
            logger.warning(f"[mcp.policy_attributor] audit write failed: {e}")

    # ── attribute() ───────────────────────────────────────────────────────

    def attribute(
        self,
        tool: ToolMetadata,
        *,
        trust_score: Optional[int] = None,
    ) -> AttributionDecision:
        """Décide une policy pour `tool`.

        Algorithme :
          1. Validate ToolMetadata + trust_score.
          2. Classify : tokens(tool_name) + escalation description.
             - Description seule (read-level) → refus no_keyword_match.
          3. Si policy is None → return None + reason="no_keyword_match".
          4. Apply trust gate selon la policy CLASSIFIÉE :
               READ_ONLY / EXTERNAL_READ → pas de gate
               LOCAL_WRITE / EXTERNAL_WRITE_* → trust ≥ 70 requis
               SECRETS_AUTH → trust ≥ 90 requis
          5. Retourne AttributionDecision (policy None en cas de refus).
        """
        # ── Étape 1 : validation ──────────────────────────────────────────
        try:
            _validate_server_id(tool.server_id)
            _validate_tool_name_local(tool.tool_name)
            _validate_description(tool.description)
            _validate_input_schema(tool.input_schema)
            _validate_trust_score(trust_score)
        except _MetadataInvalid as e:
            # Anti-leak : on ne logue ni server_id ni tool_name si l'un
            # des deux est invalide. Pour les autres champs, on logue les
            # validés seulement.
            audit_fields: Dict[str, Any] = {"reason": e.code}
            # server_id et tool_name : logué seulement si valide
            try:
                _validate_server_id(tool.server_id)
                audit_fields["server_id"] = tool.server_id
                try:
                    _validate_tool_name_local(tool.tool_name)
                    audit_fields["tool_name"] = tool.tool_name
                except _MetadataInvalid:
                    pass
            except _MetadataInvalid:
                pass
            self._audit("attribution_refused", **audit_fields)
            return AttributionDecision(
                policy=None,
                reason=e.code,
                server_id=tool.server_id if isinstance(tool.server_id, str) else "",
                tool_name=tool.tool_name if isinstance(tool.tool_name, str) else "",
                matched_keywords=[],
                trust_score_used=trust_score
                    if isinstance(trust_score, int) and not isinstance(trust_score, bool)
                    and 0 <= trust_score <= 100 else None,
                classified_policy=None,
            )

        # ── Étape 2 : classification ──────────────────────────────────────
        classified_policy, matched_kws = _classify(tool.tool_name, tool.description)

        # ── Étape 3 : aucun match → fallback conservateur ou refus ────────
        if classified_policy is None:
            # Phase I-8 (Fix AG) : avant, no_keyword_match = refus pur.
            # Observé runtime 2026-06-11 04:45 : open-meteo-mcp-server,
            # 17 tools légitimes (gfs_forecast, ecmwf_forecast...) TOUS
            # refusés — le vocabulaire météo n'est dans aucune table de
            # keywords, et aucun domaine inconnu ne le sera jamais
            # (« n'importe quel MCP au monde » est l'objectif produit).
            # Fallback : EXTERNAL_WRITE_RECOVERABLE — la policy la plus
            # restrictive EXÉCUTABLE. Jamais READ par fallback (un tool
            # mutateur inclassé ne doit pas contourner le gate write) ;
            # jamais SECRETS_AUTH (réservé au matching explicite). À
            # l'exécution, Phase 9 exige le double opt-in LUMENA_MCP_LIVE
            # + LUMENA_MCP_TRUST_LIVE : posture par défaut inchangée.
            # Gate : trust ≥ seuil write (70), comme tout WRITE classifié.
            fallback_eligible = (
                isinstance(trust_score, int)
                and not isinstance(trust_score, bool)
                and trust_score >= self._min_trust_write
            )
            if fallback_eligible:
                reason = "fallback_conservative_unclassified"
                self._audit(
                    "attribution_ok",
                    server_id=tool.server_id,
                    tool_name=tool.tool_name,
                    policy=MCPPolicy.EXTERNAL_WRITE_RECOVERABLE.value,
                    reason=reason,
                    matched_keywords=[],
                    trust_score_used=trust_score,
                )
                return AttributionDecision(
                    policy=MCPPolicy.EXTERNAL_WRITE_RECOVERABLE,
                    reason=reason,
                    server_id=tool.server_id,
                    tool_name=tool.tool_name,
                    matched_keywords=[],
                    trust_score_used=trust_score,
                    classified_policy=None,
                )
            self._audit(
                "attribution_refused",
                server_id=tool.server_id,
                tool_name=tool.tool_name,
                reason="no_keyword_match",
                trust_score_used=trust_score,
            )
            return AttributionDecision(
                policy=None,
                reason="no_keyword_match",
                server_id=tool.server_id,
                tool_name=tool.tool_name,
                matched_keywords=[],
                trust_score_used=trust_score,
                classified_policy=None,
            )

        # ── Étape 4 : trust gating selon la policy classifiée ─────────────
        # READ_ONLY / EXTERNAL_READ → pas de gate
        if classified_policy in _READ_POLICIES:
            reason = f"match:{classified_policy.value}"
            self._audit(
                "attribution_ok",
                server_id=tool.server_id,
                tool_name=tool.tool_name,
                policy=classified_policy.value,
                reason=reason,
                matched_keywords=matched_kws,
                trust_score_used=trust_score,
            )
            return AttributionDecision(
                policy=classified_policy,
                reason=reason,
                server_id=tool.server_id,
                tool_name=tool.tool_name,
                matched_keywords=matched_kws,
                trust_score_used=trust_score,
                classified_policy=classified_policy,
            )

        # SECRETS_AUTH → trust ≥ min_trust_secrets requis
        if classified_policy == MCPPolicy.SECRETS_AUTH:
            if trust_score is None:
                reason = "trust_score_missing_for_secrets"
                self._audit(
                    "attribution_refused",
                    server_id=tool.server_id,
                    tool_name=tool.tool_name,
                    reason=reason,
                    matched_keywords=matched_kws,
                    classified_policy=classified_policy.value,
                    trust_score_used=None,
                )
                return AttributionDecision(
                    policy=None,
                    reason=reason,
                    server_id=tool.server_id,
                    tool_name=tool.tool_name,
                    matched_keywords=matched_kws,
                    trust_score_used=None,
                    classified_policy=classified_policy,
                )
            if trust_score < self._min_trust_secrets:
                reason = f"trust_too_low_for_secrets:{trust_score}"
                self._audit(
                    "attribution_refused",
                    server_id=tool.server_id,
                    tool_name=tool.tool_name,
                    reason=reason,
                    matched_keywords=matched_kws,
                    classified_policy=classified_policy.value,
                    trust_score_used=trust_score,
                )
                return AttributionDecision(
                    policy=None,
                    reason=reason,
                    server_id=tool.server_id,
                    tool_name=tool.tool_name,
                    matched_keywords=matched_kws,
                    trust_score_used=trust_score,
                    classified_policy=classified_policy,
                )
            reason = f"match:{classified_policy.value}"
            self._audit(
                "attribution_ok",
                server_id=tool.server_id,
                tool_name=tool.tool_name,
                policy=classified_policy.value,
                reason=reason,
                matched_keywords=matched_kws,
                trust_score_used=trust_score,
            )
            return AttributionDecision(
                policy=classified_policy,
                reason=reason,
                server_id=tool.server_id,
                tool_name=tool.tool_name,
                matched_keywords=matched_kws,
                trust_score_used=trust_score,
                classified_policy=classified_policy,
            )

        # WRITE policies → trust ≥ min_trust_write requis
        if classified_policy in _WRITE_POLICIES:
            if trust_score is None:
                reason = "trust_score_missing_for_write"
                self._audit(
                    "attribution_refused",
                    server_id=tool.server_id,
                    tool_name=tool.tool_name,
                    reason=reason,
                    matched_keywords=matched_kws,
                    classified_policy=classified_policy.value,
                    trust_score_used=None,
                )
                return AttributionDecision(
                    policy=None,
                    reason=reason,
                    server_id=tool.server_id,
                    tool_name=tool.tool_name,
                    matched_keywords=matched_kws,
                    trust_score_used=None,
                    classified_policy=classified_policy,
                )
            if trust_score < self._min_trust_write:
                reason = f"trust_too_low_for_write:{trust_score}"
                self._audit(
                    "attribution_refused",
                    server_id=tool.server_id,
                    tool_name=tool.tool_name,
                    reason=reason,
                    matched_keywords=matched_kws,
                    classified_policy=classified_policy.value,
                    trust_score_used=trust_score,
                )
                return AttributionDecision(
                    policy=None,
                    reason=reason,
                    server_id=tool.server_id,
                    tool_name=tool.tool_name,
                    matched_keywords=matched_kws,
                    trust_score_used=trust_score,
                    classified_policy=classified_policy,
                )
            reason = f"match:{classified_policy.value}"
            self._audit(
                "attribution_ok",
                server_id=tool.server_id,
                tool_name=tool.tool_name,
                policy=classified_policy.value,
                reason=reason,
                matched_keywords=matched_kws,
                trust_score_used=trust_score,
            )
            return AttributionDecision(
                policy=classified_policy,
                reason=reason,
                server_id=tool.server_id,
                tool_name=tool.tool_name,
                matched_keywords=matched_kws,
                trust_score_used=trust_score,
                classified_policy=classified_policy,
            )

        # Filet de sécurité : policy non gérée par la table de gating.
        # Ne devrait jamais arriver.
        self._audit(
            "attribution_refused",
            server_id=tool.server_id,
            tool_name=tool.tool_name,
            reason="policy_ungated",
            matched_keywords=matched_kws,
            classified_policy=classified_policy.value,
            trust_score_used=trust_score,
        )
        return AttributionDecision(
            policy=None,
            reason="policy_ungated",
            server_id=tool.server_id,
            tool_name=tool.tool_name,
            matched_keywords=matched_kws,
            trust_score_used=trust_score,
            classified_policy=classified_policy,
        )
