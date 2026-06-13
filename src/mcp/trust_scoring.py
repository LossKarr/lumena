"""
trust_scoring.py — Score de confiance pur pour packages MCP (Phase 6).

Fonction PURE : aucun appel réseau, aucun side effect.
La collecte des métadonnées (npm registry, pypi, github) est la responsabilité
du caller — cette fonction prend les métadonnées en entrée et retourne un
TrustReport déterministe.

7 facteurs pondérés (total = 100) :
  - Source officialité (30)
  - Licence OSI (15)
  - Maintenance < 90 jours (15)
  - Popularité (stars ≥ 100 OU downloads ≥ 1000) (10)
  - Signature package (10)
  - Code scan clean (10)
  - Permissions alignées avec catégorie déclarée (10)

Anti-typosquatting : levenshtein ≤ 2 vs allowlist → pénalité -40.
Comparaison sur nom complet ET basename (anti-scope-typosquat).

Decision hint (cf plan v4.1) :
  - score >= 85  → "allow"
  - 60 <= score < 85 → "sandbox_heavy"
  - score < 60   → "refuse"

IMPORTANT : decision_hint est un INDICATEUR consommable par l'orchestrateur
MCP futur. Il ne déclenche AUCUNE action automatique. Le score décide le
niveau de sandbox, pas un blanc-seing pour activer des outils write
(cf REPO/PLAN_MCP_LUMENA.md v4.1 + REPO/THREAT_MODEL_MCP.md §6 NC2).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import FrozenSet, Iterable, List, Literal, Optional


# ──────────────────────────────────────────────────────────────────────────────
# Constantes (testables)
# ──────────────────────────────────────────────────────────────────────────────

_OFFICIAL_SOURCE_PREFIXES = (
    "github.com/modelcontextprotocol/",
    "github.com/anthropic/",
    "github.com/claude/",
)

# Licences SPDX permissives couramment acceptables pour MCP tiers
_OSI_LICENSES = frozenset(
    {
        "MIT",
        "Apache-2.0",
        "BSD-3-Clause",
        "BSD-2-Clause",
        "ISC",
    }
)

_DEFAULT_POPULAR_PACKAGES: FrozenSet[str] = frozenset(
    {
        "@modelcontextprotocol/server-filesystem",
        "@modelcontextprotocol/server-postgres",
        "@modelcontextprotocol/server-brave-search",
        "@modelcontextprotocol/server-github",
        "@modelcontextprotocol/server-memory",
        "@modelcontextprotocol/server-fetch",
        "@modelcontextprotocol/server-everything",
    }
)

# Seuils décision (cf plan v4.1)
THRESHOLD_ALLOW = 85
THRESHOLD_REFUSE = 60

# Pondérations (total = 100)
_WEIGHT_SOURCE = 30
_WEIGHT_LICENSE = 15
_WEIGHT_MAINTENANCE = 15
_WEIGHT_POPULARITY = 10
_WEIGHT_SIGNATURE = 10
_WEIGHT_CODE_SCAN = 10
_WEIGHT_PERMISSIONS = 10
_MAX_TOTAL = 100

# Anti-typosquatting
_LEVENSHTEIN_THRESHOLD = 2
_TYPOSQUAT_PENALTY = 40

# Maintenance
_MAINTENANCE_MAX_AGE_DAYS = 90

# Popularité
_POPULARITY_MIN_STARS = 100
_POPULARITY_MIN_DOWNLOADS_30D = 1000

# Permissions : politique stricte par catégorie
# Whitelist des permissions reconnues (toute permission hors liste = penalty)
_KNOWN_PERMISSIONS = frozenset(
    {
        "fs:read",
        "fs:write",
        "fs:delete",
        "net:read",
        "net:write",
        "net:any",
        "exec",
        "secrets:read",
        "secrets:rotate",
    }
)

# Permissions explicitement INTERDITES par catégorie déclarée.
# Si declared_permissions contient l'une d'elles → score permissions = 0
_CATEGORY_FORBIDDEN_PERMS = {
    "read_only": frozenset(
        {"fs:write", "fs:delete", "net:write", "net:any", "exec", "secrets:rotate"}
    ),
    "local_write": frozenset(
        {"net:write", "net:any", "exec", "secrets:rotate"}
    ),
    "external_read": frozenset({"fs:write", "fs:delete", "exec", "secrets:rotate"}),
    "external_write_recoverable": frozenset({"exec", "secrets:rotate"}),
    "external_write_irreversible": frozenset({"exec"}),
    "secrets_auth": frozenset({"net:any"}),
}

# Catégories connues — toute valeur hors de cet ensemble → score permissions = 0
_KNOWN_CATEGORIES = frozenset(_CATEGORY_FORBIDDEN_PERMS.keys())


# ──────────────────────────────────────────────────────────────────────────────
# Dataclasses
# ──────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PackageMetadata:
    """Métadonnées d'un package à scorer (input pur).

    Tous les champs collectés en amont (fetcher npm/pypi/github = hors scope).
    Les champs Optional peuvent être None si non collectés.

    Normalisation :
      - package_name : strip + lowercase appliqués par score_package()
      - source_url   : strip + lowercase + retrait https://, http://, www.,
                       trailing slash appliqués par score_package()
      - license_id   : strip appliqué par score_package() (case-sensitive SPDX)
    """

    package_name: str
    transport: Literal["npm", "uv"]

    source_url: Optional[str] = None
    is_from_allowlist: bool = False

    license_id: Optional[str] = None

    last_commit_iso: Optional[str] = None

    stars: Optional[int] = None
    downloads_last_30d: Optional[int] = None

    has_npm_provenance: bool = False
    has_pypi_sigstore: bool = False

    code_scan_clean: Optional[bool] = None
    scan_findings: List[str] = field(default_factory=list)

    declared_permissions: List[str] = field(default_factory=list)
    claimed_category: Optional[str] = None


@dataclass(frozen=True)
class TrustFactor:
    name: str
    score: int
    max_score: int
    detail: str = ""


@dataclass(frozen=True)
class TrustReport:
    package_name: str
    total_score: int
    factors: List[TrustFactor]
    typosquatting_warning: Optional[str] = None
    decision_hint: Literal["allow", "sandbox_heavy", "refuse"] = "refuse"


# ──────────────────────────────────────────────────────────────────────────────
# Helpers internes (purs)
# ──────────────────────────────────────────────────────────────────────────────


def _normalize_package_name(name: str) -> str:
    if not isinstance(name, str):
        return ""
    return name.strip().lower()


def _package_basename(name: str) -> str:
    """`@scope/server-postgres` → `server-postgres`, sinon retourne `name`."""
    norm = _normalize_package_name(name)
    if "/" in norm:
        return norm.split("/")[-1]
    return norm


def _normalize_source_url(url: Optional[str]) -> str:
    """Normalise URL : strip, lowercase, retire scheme/www/trailing slash."""
    if not isinstance(url, str):
        return ""
    u = url.strip().lower()
    for prefix in ("https://", "http://", "git+https://", "git+http://"):
        if u.startswith(prefix):
            u = u[len(prefix) :]
            break
    if u.startswith("www."):
        u = u[len("www.") :]
    if u.endswith("/"):
        u = u[:-1]
    return u


def _normalize_license(license_id: Optional[str]) -> str:
    if not isinstance(license_id, str):
        return ""
    return license_id.strip()


def _levenshtein(a: str, b: str) -> int:
    """Distance Levenshtein (itératif, O(n*m))."""
    if a == b:
        return 0
    if len(a) < len(b):
        a, b = b, a
    if len(b) == 0:
        return len(a)
    previous_row = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        current_row = [i + 1]
        for j, cb in enumerate(b):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (0 if ca == cb else 1)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row
    return previous_row[-1]


def _check_typosquatting(
    package_name: str,
    popular: Iterable[str],
) -> Optional[str]:
    """Compare nom complet ET basename contre une allowlist de packages connus.

    Returns un message de warning si suspicion typosquat, None sinon.
    Match exact (nom complet OU basename) = aucun warning.
    """
    norm_name = _normalize_package_name(package_name)
    norm_basename = _package_basename(package_name)

    # Match exact : safe
    for popular_pkg in popular:
        pop_norm = _normalize_package_name(popular_pkg)
        if norm_name == pop_norm or norm_basename == _package_basename(popular_pkg):
            return None

    # Distance proche : suspect
    for popular_pkg in popular:
        pop_norm = _normalize_package_name(popular_pkg)
        pop_basename = _package_basename(popular_pkg)
        # Comparaison nom complet
        if _levenshtein(norm_name, pop_norm) <= _LEVENSHTEIN_THRESHOLD:
            return (
                f"package name {package_name!r} is suspiciously similar to "
                f"popular package {popular_pkg!r}"
            )
        # Comparaison basename (anti-scope-typosquat)
        if (
            norm_basename
            and pop_basename
            and norm_basename != pop_basename
            and _levenshtein(norm_basename, pop_basename) <= _LEVENSHTEIN_THRESHOLD
        ):
            return (
                f"package basename {norm_basename!r} (from {package_name!r}) is "
                f"suspiciously similar to popular package {popular_pkg!r}"
            )
    return None


def _is_official_source(normalized_url: str) -> bool:
    if not normalized_url:
        return False
    return any(
        normalized_url.startswith(prefix) for prefix in _OFFICIAL_SOURCE_PREFIXES
    )


def _maintenance_age_days(
    last_commit_iso: Optional[str],
    now: datetime,
) -> Optional[int]:
    """Retourne l'âge en jours, ou None si la date est invalide.

    Gère timezone-aware ET naive proprement :
      - tz-aware → utilisée telle quelle
      - naive → assume UTC
    Date future → renvoie 0 (pas négatif), considérée comme "fraîche"
    mais ne dépasse pas le score plein.
    """
    if not isinstance(last_commit_iso, str) or not last_commit_iso.strip():
        return None
    s = last_commit_iso.strip()
    # Gérer suffixe "Z" (ISO 8601 = UTC) pour compat Python < 3.11
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    delta_days = (now - dt).days
    if delta_days < 0:
        # Date future : on clamp à 0 (= aujourd'hui)
        return 0
    return delta_days


def _check_permissions_alignment(
    claimed_category: Optional[str],
    declared_permissions: List[str],
) -> bool:
    """True si permissions OK pour cette catégorie, False sinon.

    Politique STRICTE :
      - claimed_category None ou inconnue → False (pas permissif par défaut)
      - Toute permission hors _KNOWN_PERMISSIONS → False (politique stricte)
      - Toute permission interdite par catégorie → False
      - Sinon → True
    """
    if claimed_category is None:
        return False
    if claimed_category not in _KNOWN_CATEGORIES:
        return False
    forbidden = _CATEGORY_FORBIDDEN_PERMS.get(claimed_category, frozenset())
    for perm in declared_permissions:
        if not isinstance(perm, str):
            return False
        if perm not in _KNOWN_PERMISSIONS:
            return False
        if perm in forbidden:
            return False
    return True


# ──────────────────────────────────────────────────────────────────────────────
# Fonction principale (PURE)
# ──────────────────────────────────────────────────────────────────────────────


def score_package(
    metadata: PackageMetadata,
    *,
    now: Optional[datetime] = None,
    popular_packages: Optional[Iterable[str]] = None,
) -> TrustReport:
    """Fonction PURE : retourne un TrustReport déterministe.

    Args:
        metadata: métadonnées du package (collecte = ailleurs)
        now: instant de référence pour calcul d'âge (défaut: now UTC). Injectable
             pour tests reproductibles.
        popular_packages: itérable de packages connus pour anti-typosquatting.
                          Défaut: liste statique courte (enrichissable au catalogue).

    Returns:
        TrustReport avec breakdown des 7 facteurs, warning typosquat éventuel,
        et decision_hint (allow / sandbox_heavy / refuse).

    Garanties :
      - Aucun appel réseau
      - Aucun side effect (log, metrics, fichier)
      - Déterministe : même input → même output
    """
    if now is None:
        now = datetime.now(timezone.utc)
    popular = (
        frozenset(_normalize_package_name(p) for p in popular_packages)
        if popular_packages is not None
        else frozenset(
            _normalize_package_name(p) for p in _DEFAULT_POPULAR_PACKAGES
        )
    )
    # On recalcule un set "popular original" pour les comparaisons de typo
    # qui ont besoin du basename original (et non lowercase déjà appliqué)
    popular_originals = (
        list(popular_packages)
        if popular_packages is not None
        else list(_DEFAULT_POPULAR_PACKAGES)
    )

    norm_url = _normalize_source_url(metadata.source_url)
    norm_license = _normalize_license(metadata.license_id)

    factors: List[TrustFactor] = []

    # 1. Source officialité (30)
    if metadata.is_from_allowlist or _is_official_source(norm_url):
        factors.append(
            TrustFactor(
                "source_officiality",
                _WEIGHT_SOURCE,
                _WEIGHT_SOURCE,
                "official source"
                if not metadata.is_from_allowlist
                else "explicit allowlist",
            )
        )
    else:
        factors.append(
            TrustFactor(
                "source_officiality",
                0,
                _WEIGHT_SOURCE,
                f"unofficial source: {norm_url or 'unknown'}",
            )
        )

    # 2. Licence OSI (15)
    if norm_license in _OSI_LICENSES:
        factors.append(
            TrustFactor(
                "license",
                _WEIGHT_LICENSE,
                _WEIGHT_LICENSE,
                f"OSI permissive: {norm_license}",
            )
        )
    else:
        factors.append(
            TrustFactor(
                "license",
                0,
                _WEIGHT_LICENSE,
                f"non-OSI or unknown: {norm_license or '(none)'}",
            )
        )

    # 3. Maintenance (15)
    age_days = _maintenance_age_days(metadata.last_commit_iso, now)
    if age_days is None:
        factors.append(
            TrustFactor(
                "maintenance",
                0,
                _WEIGHT_MAINTENANCE,
                "no valid commit date",
            )
        )
    elif age_days <= _MAINTENANCE_MAX_AGE_DAYS:
        factors.append(
            TrustFactor(
                "maintenance",
                _WEIGHT_MAINTENANCE,
                _WEIGHT_MAINTENANCE,
                f"last commit {age_days}d ago",
            )
        )
    else:
        factors.append(
            TrustFactor(
                "maintenance",
                0,
                _WEIGHT_MAINTENANCE,
                f"last commit {age_days}d ago (> {_MAINTENANCE_MAX_AGE_DAYS}d)",
            )
        )

    # 4. Popularité (10)
    pop_score = 0
    pop_detail = "insufficient popularity data"
    if (
        isinstance(metadata.stars, int)
        and metadata.stars >= _POPULARITY_MIN_STARS
    ):
        pop_score = _WEIGHT_POPULARITY
        pop_detail = f"{metadata.stars} stars"
    elif (
        isinstance(metadata.downloads_last_30d, int)
        and metadata.downloads_last_30d >= _POPULARITY_MIN_DOWNLOADS_30D
    ):
        pop_score = _WEIGHT_POPULARITY
        pop_detail = f"{metadata.downloads_last_30d} downloads/30d"
    factors.append(
        TrustFactor("popularity", pop_score, _WEIGHT_POPULARITY, pop_detail)
    )

    # 5. Signature (10)
    if metadata.has_npm_provenance or metadata.has_pypi_sigstore:
        sig_detail = []
        if metadata.has_npm_provenance:
            sig_detail.append("npm-provenance")
        if metadata.has_pypi_sigstore:
            sig_detail.append("pypi-sigstore")
        factors.append(
            TrustFactor(
                "signature",
                _WEIGHT_SIGNATURE,
                _WEIGHT_SIGNATURE,
                "+".join(sig_detail),
            )
        )
    else:
        factors.append(
            TrustFactor(
                "signature",
                0,
                _WEIGHT_SIGNATURE,
                "no signed provenance",
            )
        )

    # 6. Code scan (10)
    if metadata.code_scan_clean is True:
        factors.append(
            TrustFactor(
                "code_scan",
                _WEIGHT_CODE_SCAN,
                _WEIGHT_CODE_SCAN,
                "scan clean",
            )
        )
    elif metadata.code_scan_clean is False:
        snippet = ", ".join(metadata.scan_findings[:3]) or "(no detail)"
        factors.append(
            TrustFactor(
                "code_scan",
                0,
                _WEIGHT_CODE_SCAN,
                f"findings: {snippet}",
            )
        )
    else:
        factors.append(
            TrustFactor(
                "code_scan",
                0,
                _WEIGHT_CODE_SCAN,
                "not scanned",
            )
        )

    # 7. Permissions alignées (10)
    aligned = _check_permissions_alignment(
        metadata.claimed_category, metadata.declared_permissions
    )
    if aligned:
        factors.append(
            TrustFactor(
                "permissions",
                _WEIGHT_PERMISSIONS,
                _WEIGHT_PERMISSIONS,
                f"permissions match category {metadata.claimed_category!r}",
            )
        )
    else:
        if metadata.claimed_category is None:
            detail = "no claimed_category (strict policy: 0)"
        elif metadata.claimed_category not in _KNOWN_CATEGORIES:
            detail = (
                f"unknown claimed_category {metadata.claimed_category!r}"
            )
        else:
            detail = (
                f"declared_permissions incompatible with "
                f"{metadata.claimed_category!r} or unknown permission"
            )
        factors.append(
            TrustFactor("permissions", 0, _WEIGHT_PERMISSIONS, detail)
        )

    # Total avant pénalité
    total = sum(f.score for f in factors)

    # Anti-typosquatting
    typo_warning = _check_typosquatting(metadata.package_name, popular_originals)
    if typo_warning:
        total = max(0, total - _TYPOSQUAT_PENALTY)

    # Clamp final
    total = max(0, min(total, _MAX_TOTAL))

    # Decision hint
    if total >= THRESHOLD_ALLOW:
        hint: Literal["allow", "sandbox_heavy", "refuse"] = "allow"
    elif total >= THRESHOLD_REFUSE:
        hint = "sandbox_heavy"
    else:
        hint = "refuse"

    return TrustReport(
        package_name=metadata.package_name,
        total_score=total,
        factors=factors,
        typosquatting_warning=typo_warning,
        decision_hint=hint,
    )
