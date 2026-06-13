"""
Tests pour trust_scoring (Phase 6).

Garanties à prouver :
  - Pureté (aucun side effect, aucun réseau, déterminisme)
  - Normalisation inputs (URL https://, trailing slash, lowercase, etc.)
  - 7 facteurs avec pondérations correctes (30+15+15+10+10+10+10 = 100)
  - Typosquatting : nom complet + basename (scope npm)
  - Maintenance date : tz-aware + naive + date future + date invalide
  - Permissions : catégorie inconnue + permission inconnue → 0
  - decision_hint selon seuils 85 / 60
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

import pytest

from src.mcp.trust_scoring import (
    PackageMetadata,
    THRESHOLD_ALLOW,
    THRESHOLD_REFUSE,
    TrustReport,
    _levenshtein,
    _normalize_source_url,
    _package_basename,
    score_package,
)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────


def _fixed_now() -> datetime:
    """Instant fixe pour tests déterministes (UTC)."""
    return datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


def _full_trust_metadata(**overrides) -> PackageMetadata:
    """Metadata configurée pour atteindre score plein (100)."""
    base: Dict[str, Any] = dict(
        package_name="@modelcontextprotocol/server-filesystem",
        transport="npm",
        source_url="https://github.com/modelcontextprotocol/servers",
        is_from_allowlist=False,
        license_id="MIT",
        last_commit_iso="2026-05-15T10:00:00+00:00",  # < 90j
        stars=2500,
        downloads_last_30d=50000,
        has_npm_provenance=True,
        has_pypi_sigstore=False,
        code_scan_clean=True,
        scan_findings=[],
        declared_permissions=["fs:read"],
        claimed_category="read_only",
    )
    base.update(overrides)
    return PackageMetadata(**base)


# ──────────────────────────────────────────────────────────────────────────────
# Pureté
# ──────────────────────────────────────────────────────────────────────────────


def test_score_is_pure_no_logs(caplog):
    caplog.clear()
    with caplog.at_level(logging.DEBUG):
        score_package(_full_trust_metadata(), now=_fixed_now())
    assert caplog.records == [], f"Logs émis: {caplog.records}"


def test_score_deterministic():
    md = _full_trust_metadata()
    now = _fixed_now()
    results = {score_package(md, now=now).total_score for _ in range(50)}
    assert len(results) == 1, f"Non déterministe: {results}"


def test_score_no_network_call(monkeypatch):
    """Aucun appel urllib.request / requests / httpx n'est fait."""
    forbidden = []

    def _trap(*args, **kwargs):
        forbidden.append((args, kwargs))
        raise RuntimeError("Network call detected in pure scoring!")

    # Patch les voies de comm les plus probables
    try:
        import urllib.request as ur
        monkeypatch.setattr(ur, "urlopen", _trap)
    except ImportError:
        pass

    score_package(_full_trust_metadata(), now=_fixed_now())
    assert forbidden == []


def test_score_returns_trust_report_type():
    report = score_package(_full_trust_metadata(), now=_fixed_now())
    assert isinstance(report, TrustReport)
    assert report.package_name == "@modelcontextprotocol/server-filesystem"
    assert isinstance(report.total_score, int)
    assert 0 <= report.total_score <= 100


# ──────────────────────────────────────────────────────────────────────────────
# Normalisation inputs
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("https://github.com/modelcontextprotocol/servers", "github.com/modelcontextprotocol/servers"),
        ("http://github.com/modelcontextprotocol/servers/", "github.com/modelcontextprotocol/servers"),
        ("https://www.github.com/modelcontextprotocol/servers/", "github.com/modelcontextprotocol/servers"),
        ("git+https://github.com/modelcontextprotocol/servers", "github.com/modelcontextprotocol/servers"),
        ("  HTTPS://GITHUB.COM/MODELCONTEXTPROTOCOL/SERVERS/  ", "github.com/modelcontextprotocol/servers"),
        ("", ""),
        (None, ""),
    ],
)
def test_normalize_source_url(raw, expected):
    assert _normalize_source_url(raw) == expected


def test_official_source_matches_with_https_and_trailing_slash():
    """L'URL avec https:// + trailing slash + caps doit matcher l'allowlist."""
    md = _full_trust_metadata(
        source_url="HTTPS://GitHub.com/ModelContextProtocol/Servers/",
        is_from_allowlist=False,
    )
    report = score_package(md, now=_fixed_now())
    source_factor = next(f for f in report.factors if f.name == "source_officiality")
    assert source_factor.score == 30, f"Expected 30, got {source_factor.score}"


def test_unofficial_source_returns_zero():
    md = _full_trust_metadata(
        source_url="https://github.com/random-user/something",
        is_from_allowlist=False,
    )
    report = score_package(md, now=_fixed_now())
    source_factor = next(f for f in report.factors if f.name == "source_officiality")
    assert source_factor.score == 0


def test_allowlist_shortcut_grants_source_score():
    md = _full_trust_metadata(
        source_url=None,  # pas d'URL
        is_from_allowlist=True,
    )
    report = score_package(md, now=_fixed_now())
    source_factor = next(f for f in report.factors if f.name == "source_officiality")
    assert source_factor.score == 30


def test_license_with_whitespace_is_normalized():
    md = _full_trust_metadata(license_id="  MIT  ")
    report = score_package(md, now=_fixed_now())
    license_factor = next(f for f in report.factors if f.name == "license")
    assert license_factor.score == 15


# ──────────────────────────────────────────────────────────────────────────────
# Facteurs individuels
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "license_id,expected_score",
    [
        ("MIT", 15),
        ("Apache-2.0", 15),
        ("BSD-3-Clause", 15),
        ("BSD-2-Clause", 15),
        ("ISC", 15),
        ("Proprietary", 0),
        ("Custom-License", 0),
        ("", 0),
        (None, 0),
        ("mit", 0),  # SPDX strict, lowercase ≠ MIT
    ],
)
def test_license_score_for_each_id(license_id, expected_score):
    md = _full_trust_metadata(license_id=license_id)
    report = score_package(md, now=_fixed_now())
    factor = next(f for f in report.factors if f.name == "license")
    assert factor.score == expected_score


@pytest.mark.parametrize(
    "stars,downloads,expected_score",
    [
        (100, None, 10),
        (99, None, 0),
        (None, 1000, 10),
        (None, 999, 0),
        (50, 500, 0),
        (200, 50, 10),
        (None, None, 0),
    ],
)
def test_popularity_thresholds(stars, downloads, expected_score):
    md = _full_trust_metadata(stars=stars, downloads_last_30d=downloads)
    report = score_package(md, now=_fixed_now())
    factor = next(f for f in report.factors if f.name == "popularity")
    assert factor.score == expected_score


@pytest.mark.parametrize(
    "npm_prov,pypi_sig,expected",
    [
        (True, False, 10),
        (False, True, 10),
        (True, True, 10),
        (False, False, 0),
    ],
)
def test_signature_score(npm_prov, pypi_sig, expected):
    md = _full_trust_metadata(
        has_npm_provenance=npm_prov, has_pypi_sigstore=pypi_sig,
    )
    report = score_package(md, now=_fixed_now())
    factor = next(f for f in report.factors if f.name == "signature")
    assert factor.score == expected


def test_code_scan_clean_full_score():
    md = _full_trust_metadata(code_scan_clean=True)
    report = score_package(md, now=_fixed_now())
    factor = next(f for f in report.factors if f.name == "code_scan")
    assert factor.score == 10


def test_code_scan_findings_zero():
    md = _full_trust_metadata(
        code_scan_clean=False,
        scan_findings=["eval() detected", "exec() detected"],
    )
    report = score_package(md, now=_fixed_now())
    factor = next(f for f in report.factors if f.name == "code_scan")
    assert factor.score == 0
    assert "eval" in factor.detail


def test_code_scan_not_scanned_zero():
    md = _full_trust_metadata(code_scan_clean=None)
    report = score_package(md, now=_fixed_now())
    factor = next(f for f in report.factors if f.name == "code_scan")
    assert factor.score == 0


# ──────────────────────────────────────────────────────────────────────────────
# Maintenance date : tz-aware + naive + future + invalide
# ──────────────────────────────────────────────────────────────────────────────


def test_maintenance_tz_aware_recent_full():
    md = _full_trust_metadata(last_commit_iso="2026-05-20T10:00:00+00:00")
    report = score_package(md, now=_fixed_now())
    factor = next(f for f in report.factors if f.name == "maintenance")
    assert factor.score == 15


def test_maintenance_naive_treated_as_utc():
    """Date naive (sans timezone) doit être traitée comme UTC, pas exception."""
    md = _full_trust_metadata(last_commit_iso="2026-05-20T10:00:00")
    report = score_package(md, now=_fixed_now())
    factor = next(f for f in report.factors if f.name == "maintenance")
    assert factor.score == 15


def test_maintenance_old_commit_zero():
    md = _full_trust_metadata(last_commit_iso="2025-01-01T00:00:00+00:00")
    report = score_package(md, now=_fixed_now())
    factor = next(f for f in report.factors if f.name == "maintenance")
    assert factor.score == 0


def test_maintenance_zulu_suffix_accepted():
    md = _full_trust_metadata(last_commit_iso="2026-05-20T10:00:00Z")
    report = score_package(md, now=_fixed_now())
    factor = next(f for f in report.factors if f.name == "maintenance")
    assert factor.score == 15


def test_maintenance_future_date_does_not_exceed_full_score():
    """Date future (clock skew possible) → clamp à 0 jours d'âge, jamais score > plein."""
    md = _full_trust_metadata(last_commit_iso="2027-01-01T00:00:00+00:00")
    report = score_package(md, now=_fixed_now())
    factor = next(f for f in report.factors if f.name == "maintenance")
    # Date future = traitée comme "aujourd'hui" → score plein, mais pas plus
    assert factor.score == 15


@pytest.mark.parametrize(
    "invalid",
    ["", "   ", "not-a-date", "2026-13-99", "garbage", None, "2026/05/20"],
)
def test_maintenance_invalid_date_zero_no_exception(invalid):
    """Date invalide → score 0, JAMAIS d'exception."""
    md = _full_trust_metadata(last_commit_iso=invalid)
    report = score_package(md, now=_fixed_now())  # ne doit pas crasher
    factor = next(f for f in report.factors if f.name == "maintenance")
    assert factor.score == 0


def test_maintenance_now_naive_works():
    """Si `now` est naive, doit être traité comme UTC."""
    naive_now = datetime(2026, 6, 1, 12, 0, 0)  # pas de tzinfo
    md = _full_trust_metadata(last_commit_iso="2026-05-15T10:00:00+00:00")
    report = score_package(md, now=naive_now)  # ne crash pas
    factor = next(f for f in report.factors if f.name == "maintenance")
    assert factor.score == 15


# ──────────────────────────────────────────────────────────────────────────────
# Permissions
# ──────────────────────────────────────────────────────────────────────────────


def test_permissions_aligned_read_only_with_fs_read():
    md = _full_trust_metadata(
        claimed_category="read_only", declared_permissions=["fs:read"],
    )
    report = score_package(md, now=_fixed_now())
    factor = next(f for f in report.factors if f.name == "permissions")
    assert factor.score == 10


def test_permissions_read_only_with_fs_write_mismatch():
    md = _full_trust_metadata(
        claimed_category="read_only",
        declared_permissions=["fs:read", "fs:write"],
    )
    report = score_package(md, now=_fixed_now())
    factor = next(f for f in report.factors if f.name == "permissions")
    assert factor.score == 0


def test_permissions_none_category_returns_zero():
    """claimed_category=None → 0 (politique stricte, pas permissif)."""
    md = _full_trust_metadata(
        claimed_category=None, declared_permissions=["fs:read"],
    )
    report = score_package(md, now=_fixed_now())
    factor = next(f for f in report.factors if f.name == "permissions")
    assert factor.score == 0
    assert "no claimed_category" in factor.detail.lower()


def test_permissions_unknown_category_returns_zero():
    md = _full_trust_metadata(
        claimed_category="totally_made_up_category",
        declared_permissions=["fs:read"],
    )
    report = score_package(md, now=_fixed_now())
    factor = next(f for f in report.factors if f.name == "permissions")
    assert factor.score == 0
    assert "unknown" in factor.detail.lower()


def test_permissions_unknown_permission_returns_zero():
    """Une permission hors whitelist → score 0."""
    md = _full_trust_metadata(
        claimed_category="read_only",
        declared_permissions=["fs:read", "magic:teleport"],  # magic:teleport inconnu
    )
    report = score_package(md, now=_fixed_now())
    factor = next(f for f in report.factors if f.name == "permissions")
    assert factor.score == 0


def test_permissions_local_write_allows_fs_write():
    md = _full_trust_metadata(
        claimed_category="local_write",
        declared_permissions=["fs:read", "fs:write", "fs:delete"],
    )
    report = score_package(md, now=_fixed_now())
    factor = next(f for f in report.factors if f.name == "permissions")
    assert factor.score == 10


def test_permissions_local_write_forbids_net_any():
    md = _full_trust_metadata(
        claimed_category="local_write",
        declared_permissions=["fs:write", "net:any"],
    )
    report = score_package(md, now=_fixed_now())
    factor = next(f for f in report.factors if f.name == "permissions")
    assert factor.score == 0


def test_permissions_non_string_in_list_returns_zero():
    md = _full_trust_metadata(
        claimed_category="read_only",
        declared_permissions=["fs:read", 12345],  # type: ignore
    )
    report = score_package(md, now=_fixed_now())
    factor = next(f for f in report.factors if f.name == "permissions")
    assert factor.score == 0


# ──────────────────────────────────────────────────────────────────────────────
# Anti-typosquatting : nom complet ET basename
# ──────────────────────────────────────────────────────────────────────────────


def test_typosquat_close_name_triggers_warning_and_penalty():
    md = _full_trust_metadata(
        package_name="@modelcontextprotocol/server-postgress",  # double s
    )
    report = score_package(md, now=_fixed_now())
    assert report.typosquatting_warning is not None
    # Score plein = 100, pénalité 40 → 60
    assert report.total_score <= 60


def test_typosquat_exact_match_no_warning():
    md = _full_trust_metadata(
        package_name="@modelcontextprotocol/server-postgres",
    )
    report = score_package(md, now=_fixed_now())
    assert report.typosquatting_warning is None


def test_typosquat_distance_above_threshold_no_warning():
    md = _full_trust_metadata(
        package_name="@somecorp/my-unique-tool-name",  # très différent
    )
    report = score_package(md, now=_fixed_now())
    assert report.typosquatting_warning is None


def test_typosquat_basename_match_scoped_attack():
    """Anti-scope-typosquat : @attacker/server-postgres a un basename
    proche du basename de @modelcontextprotocol/server-postgres → warning."""
    md = _full_trust_metadata(
        package_name="@evil/server-postgress",  # basename similaire
    )
    report = score_package(md, now=_fixed_now())
    assert report.typosquatting_warning is not None


def test_typosquat_basename_exact_match_no_warning():
    """Même basename exact = pas un typosquat (juste un autre scope légitime)."""
    md = _full_trust_metadata(
        package_name="@othercorp/server-postgres",  # basename exact
        # Met source non officiel pour ne pas avoir le bonus modelcontextprotocol
        source_url="https://github.com/othercorp/something",
    )
    report = score_package(md, now=_fixed_now())
    assert report.typosquatting_warning is None


def test_typosquat_custom_popular_set_used():
    """Custom popular set : `my-things` (distance 1 vs `my-thing`) → warning."""
    md = _full_trust_metadata(package_name="my-things")
    custom_popular = ["my-thing"]
    report = score_package(
        md, now=_fixed_now(), popular_packages=custom_popular,
    )
    assert report.typosquatting_warning is not None


def test_typosquat_custom_popular_set_far_no_warning():
    """Custom popular set : nom très différent → pas de warning."""
    md = _full_trust_metadata(package_name="something-totally-different-xyz")
    custom_popular = ["my-thing"]
    report = score_package(
        md, now=_fixed_now(), popular_packages=custom_popular,
    )
    assert report.typosquatting_warning is None


def test_typosquat_penalty_clamped_to_zero():
    """Si pénalité descend en-dessous de 0, total clampé à 0."""
    md = PackageMetadata(
        package_name="@modelcontextprotocol/server-postgress",  # typo
        transport="npm",
        source_url=None,
        license_id=None,
        last_commit_iso=None,
        stars=None,
        downloads_last_30d=None,
        has_npm_provenance=False,
        has_pypi_sigstore=False,
        code_scan_clean=None,
        declared_permissions=[],
        claimed_category=None,
    )
    report = score_package(md, now=_fixed_now())
    assert report.total_score == 0


# ──────────────────────────────────────────────────────────────────────────────
# Levenshtein interne
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "a,b,expected",
    [
        ("kitten", "sitting", 3),
        ("flaw", "lawn", 2),
        ("", "", 0),
        ("abc", "", 3),
        ("", "xyz", 3),
        ("abc", "abc", 0),
        ("a", "b", 1),
    ],
)
def test_levenshtein_cases(a, b, expected):
    assert _levenshtein(a, b) == expected


def test_levenshtein_symmetric():
    assert _levenshtein("abc", "abcd") == _levenshtein("abcd", "abc")


def test_package_basename():
    assert _package_basename("@scope/name") == "name"
    assert _package_basename("simple") == "simple"
    assert _package_basename("@scope/path/segments/end") == "end"
    assert _package_basename("") == ""


# ──────────────────────────────────────────────────────────────────────────────
# Total + decision_hint
# ──────────────────────────────────────────────────────────────────────────────


def test_total_score_max_100_when_all_factors_full():
    md = _full_trust_metadata()
    report = score_package(md, now=_fixed_now())
    assert report.total_score == 100


def test_total_score_zero_when_nothing_set():
    md = PackageMetadata(
        package_name="completely-unknown-rare-name-9876543",
        transport="npm",
    )
    report = score_package(md, now=_fixed_now())
    assert report.total_score == 0


def test_total_score_breakdown_sums_correctly():
    """Le total doit être ≤ somme des factors (peut être moins si pénalité typo)."""
    md = _full_trust_metadata()
    report = score_package(md, now=_fixed_now())
    sum_factors = sum(f.score for f in report.factors)
    # Pas de typosquat ici donc égalité
    assert report.total_score == sum_factors


def test_decision_hint_allow_at_85():
    md = _full_trust_metadata()
    # Force exactement 85 : enlever 15 pts (licence non OSI)
    md_85 = _full_trust_metadata(license_id="Proprietary")
    report = score_package(md_85, now=_fixed_now())
    assert report.total_score == 85
    assert report.decision_hint == "allow"


def test_decision_hint_sandbox_heavy_below_85_above_60():
    # 100 - 30 source = 70
    md_70 = _full_trust_metadata(
        source_url="https://github.com/random/repo",
        is_from_allowlist=False,
    )
    report = score_package(md_70, now=_fixed_now())
    assert report.total_score == 70
    assert report.decision_hint == "sandbox_heavy"


def test_decision_hint_refuse_below_60():
    # 100 - 30 source - 15 license - 10 popularity = 45
    md_45 = _full_trust_metadata(
        source_url="https://github.com/random/repo",
        license_id="Proprietary",
        stars=10,
        downloads_last_30d=100,
    )
    report = score_package(md_45, now=_fixed_now())
    assert report.total_score == 45
    assert report.decision_hint == "refuse"


def test_decision_hint_at_60_is_sandbox_heavy():
    # 100 - 30 source - 10 popularity = 60
    md_60 = _full_trust_metadata(
        source_url="https://github.com/random/repo",
        stars=10,
        downloads_last_30d=100,
    )
    report = score_package(md_60, now=_fixed_now())
    assert report.total_score == 60
    assert report.decision_hint == "sandbox_heavy"


def test_decision_hint_at_59_is_refuse():
    # 60 - 1 → impossible (granularité 5), construire 55 ou descendre encore
    # Construit 55 : 100 - 30 source - 15 license = 55
    md_55 = _full_trust_metadata(
        source_url="https://github.com/random/repo",
        license_id="Proprietary",
    )
    report = score_package(md_55, now=_fixed_now())
    assert report.total_score == 55
    assert report.decision_hint == "refuse"


# ──────────────────────────────────────────────────────────────────────────────
# Imports module __init__
# ──────────────────────────────────────────────────────────────────────────────


def test_module_exports_trust_scoring():
    from src.mcp import (
        PackageMetadata as PM,
        THRESHOLD_ALLOW as TA,
        THRESHOLD_REFUSE as TR,
        TrustFactor as TF,
        TrustReport as TRpt,
        score_package as sp,
    )
    assert PM is PackageMetadata
    assert TA == 85
    assert TR == 60
    assert callable(sp)


# ──────────────────────────────────────────────────────────────────────────────
# Combinatoire scores
# ──────────────────────────────────────────────────────────────────────────────


def test_seven_factors_present_in_report():
    md = _full_trust_metadata()
    report = score_package(md, now=_fixed_now())
    names = {f.name for f in report.factors}
    expected = {
        "source_officiality",
        "license",
        "maintenance",
        "popularity",
        "signature",
        "code_scan",
        "permissions",
    }
    assert names == expected


def test_each_factor_has_max_score_field():
    md = _full_trust_metadata()
    report = score_package(md, now=_fixed_now())
    expected_max = {
        "source_officiality": 30,
        "license": 15,
        "maintenance": 15,
        "popularity": 10,
        "signature": 10,
        "code_scan": 10,
        "permissions": 10,
    }
    for factor in report.factors:
        assert factor.max_score == expected_max[factor.name]


def test_factor_score_never_exceeds_max_score():
    md = _full_trust_metadata()
    report = score_package(md, now=_fixed_now())
    for factor in report.factors:
        assert 0 <= factor.score <= factor.max_score
