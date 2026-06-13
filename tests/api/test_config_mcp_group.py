"""
Tests Phase 21.0 + Phase G — MCP Configuration knobs.

Vérifie l'extension du groupe "MCP" dans web/routes/config.py :
  - 14 clés MCP présentes (9 Phase 21 + 5 Phase G)
  - types corrects
  - defaults sécurisés (live=0, autoapprove=0, trust=0, kill switches=0)
  - UTF-8 préservé (pas de mojibake)
  - web/routes/config.py n'importe PAS src.mcp
  - Aucune route MCP ajoutée pour cette config (toujours via GET/PUT /api/config)
"""
from __future__ import annotations

from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_CONFIG_PY_PATH = _REPO_ROOT / "web" / "routes" / "config.py"
_MCP_PY_PATH = _REPO_ROOT / "web" / "routes" / "mcp.py"


_EXPECTED_MCP_KEYS = {
    # Phase 21
    "LUMENA_MCP_LIVE":               "bool",
    "LUMENA_MCP_AUTOAPPROVE_LIVE":   "bool",
    "LUMENA_MCP_AGENT_AUTOAPPROVE_LIVE": "bool",
    "LUMENA_MCP_TRUST_LIVE":         "bool",
    "LUMENA_MCP_REACT_INTEGRATION_ENABLED": "bool",
    "LUMENA_MCP_NETWORK_SEARCH_ENABLED": "bool",
    "LUMENA_MCP_INSTALL_DISABLED":   "bool",
    "LUMENA_MCP_ACTIVATION_DISABLED":"bool",
    "LUMENA_MCP_ROOT":               "text",
    # Phase G
    "LUMENA_MCP_CURATED_CACHE_DIR":         "text",
    "LUMENA_MCP_PREFER_NATIVE_DEFAULT":     "bool",
    "LUMENA_MCP_AUTO_TRUST_THRESHOLD":      "number",
    "LUMENA_MCP_OVERLAP_DETECTION_ENABLED": "bool",
    "LUMENA_MCP_REMOTE_TRANSPORT_DISABLED": "bool",
}


def _schema():
    from web.routes import config as config_mod
    return config_mod._CONFIG_SCHEMA


def _mcp_entries():
    return [e for e in _schema() if e.get("group") == "MCP"]


# ──────────────────────────────────────────────────────────────────────────────
# Section 1 — Schéma : groupe MCP présent, 9 clés
# ──────────────────────────────────────────────────────────────────────────────


def test_schema_contains_group_mcp():
    groups = {e.get("group") for e in _schema()}
    assert "MCP" in groups


def test_schema_mcp_has_exactly_fourteen_entries():
    entries = _mcp_entries()
    assert len(entries) == 14, f"Expected 14 MCP entries, got {len(entries)}"


@pytest.mark.parametrize("key", list(_EXPECTED_MCP_KEYS.keys()))
def test_schema_mcp_key_present(key):
    keys = {e["key"] for e in _mcp_entries()}
    assert key in keys, f"Missing MCP key {key}"


@pytest.mark.parametrize("key,expected_type", list(_EXPECTED_MCP_KEYS.items()))
def test_schema_mcp_key_type(key, expected_type):
    entry = next(e for e in _mcp_entries() if e["key"] == key)
    assert entry["type"] == expected_type


# ──────────────────────────────────────────────────────────────────────────────
# Section 2 — Defaults sécurisés
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("key", [
    "LUMENA_MCP_LIVE",
    "LUMENA_MCP_AUTOAPPROVE_LIVE",
    "LUMENA_MCP_AGENT_AUTOAPPROVE_LIVE",
    "LUMENA_MCP_TRUST_LIVE",
    "LUMENA_MCP_REACT_INTEGRATION_ENABLED",
    "LUMENA_MCP_INSTALL_DISABLED",
    "LUMENA_MCP_ACTIVATION_DISABLED",
])
def test_bool_defaults_are_zero_secure(key):
    entry = next(e for e in _mcp_entries() if e["key"] == key)
    assert entry["default"] == "0", (
        f"{key} default must be '0' (sécurité par défaut)"
    )


def test_network_search_default_is_one_readonly():
    entry = next(
        e for e in _mcp_entries()
        if e["key"] == "LUMENA_MCP_NETWORK_SEARCH_ENABLED"
    )
    assert entry["default"] == "1"
    assert "read-only" in entry["hint"]


def test_mcp_root_default_is_empty():
    entry = next(e for e in _mcp_entries() if e["key"] == "LUMENA_MCP_ROOT")
    assert entry["default"] == ""


# ──────────────────────────────────────────────────────────────────────────────
# Section 3 — UTF-8 préservé / anti-mojibake
# ──────────────────────────────────────────────────────────────────────────────


def test_config_py_has_no_mojibake():
    text = _CONFIG_PY_PATH.read_text(encoding="utf-8")
    for moji in ("Ã©", "Ã¨", "Ã ", "Ã§", "Ã®", "Ã´", "Ãª",
                 "â€™", "â€œ", "â€"):
        assert moji not in text, f"mojibake détecté : {moji}"


def test_mcp_entries_labels_preserve_accents():
    entries = {e["key"]: e for e in _mcp_entries()}
    # On vérifie que les labels avec accents sont bien tels que prévus.
    assert "Désactiver" in entries["LUMENA_MCP_INSTALL_DISABLED"]["label"]
    assert "Désactiver" in entries["LUMENA_MCP_ACTIVATION_DISABLED"]["label"]
    assert "ReAct" in entries["LUMENA_MCP_REACT_INTEGRATION_ENABLED"]["label"]
    assert "Recherche" in entries["LUMENA_MCP_NETWORK_SEARCH_ENABLED"]["label"]
    assert "Dossier" in entries["LUMENA_MCP_ROOT"]["label"]


def test_mcp_entries_hints_preserve_accents():
    entries = {e["key"]: e for e in _mcp_entries()}
    # quelques accents au hasard
    hint_root = entries["LUMENA_MCP_ROOT"]["hint"]
    assert "Redémarrage" in hint_root or "défaut" in hint_root or "où" in hint_root


# ──────────────────────────────────────────────────────────────────────────────
# Section 4 — Pas d'import src.mcp ni de route MCP ajoutée pour cette config
# ──────────────────────────────────────────────────────────────────────────────


def test_config_py_does_not_import_src_mcp():
    text = _CONFIG_PY_PATH.read_text(encoding="utf-8")
    assert "src.mcp" not in text, "config.py NE DOIT PAS importer src.mcp"
    assert "from src.mcp" not in text
    assert "import src.mcp" not in text


def test_no_new_mcp_route_added_for_config_21_0():
    """Aucune route /api/mcp/config ni /api/mcp/knobs ne doit exister."""
    text = _MCP_PY_PATH.read_text(encoding="utf-8")
    assert "/api/mcp/config" not in text
    assert "/api/mcp/knobs" not in text


def test_config_py_phase21_0_only_extends_schema():
    """Phase 21.0 doit se contenter d'étendre _CONFIG_SCHEMA — pas de nouveaux
    routers/endpoints/singletons MCP côté config.py."""
    text = _CONFIG_PY_PATH.read_text(encoding="utf-8")
    # aucune nouvelle déclaration de router @ ni endpoint /api/mcp/*
    assert "/api/mcp/" not in text
    # le module ne doit pas instancier de service MCP
    for forb in ("MCPServerCatalog(", "MCPApprovalQueue(",
                 "MCPRuntimeWatcher(", "AutoApproveEngine("):
        assert forb not in text


# ──────────────────────────────────────────────────────────────────────────────
# Section 5 — Phase G : 5 nouveaux knobs
# ──────────────────────────────────────────────────────────────────────────────


_PHASE_G_KEYS = {
    "LUMENA_MCP_CURATED_CACHE_DIR":         ("text",   ""),
    "LUMENA_MCP_PREFER_NATIVE_DEFAULT":     ("bool",   "1"),
    "LUMENA_MCP_AUTO_TRUST_THRESHOLD":      ("number", "80"),
    "LUMENA_MCP_OVERLAP_DETECTION_ENABLED": ("bool",   "1"),
    "LUMENA_MCP_REMOTE_TRANSPORT_DISABLED": ("bool",   "1"),
}


@pytest.mark.parametrize("key,expected", list(_PHASE_G_KEYS.items()))
def test_phase_g_key_present_with_default(key, expected):
    expected_type, expected_default = expected
    entry = next(
        (e for e in _mcp_entries() if e["key"] == key), None,
    )
    assert entry is not None, f"Missing Phase G key {key}"
    assert entry["type"] == expected_type
    assert entry["default"] == expected_default


def test_phase_g_auto_trust_threshold_has_bounds():
    entry = next(
        e for e in _mcp_entries()
        if e["key"] == "LUMENA_MCP_AUTO_TRUST_THRESHOLD"
    )
    assert entry.get("min") == 0
    assert entry.get("max") == 100


def test_phase_g_curated_cache_dir_hint_mentions_data_dir():
    entry = next(
        e for e in _mcp_entries()
        if e["key"] == "LUMENA_MCP_CURATED_CACHE_DIR"
    )
    assert "DATA_DIR" in entry["hint"] or "mcp_curated" in entry["hint"]


def test_phase_g_prefer_native_default_is_one():
    """Doctrine cohabitation : par défaut, on garde le natif quand overlap."""
    entry = next(
        e for e in _mcp_entries()
        if e["key"] == "LUMENA_MCP_PREFER_NATIVE_DEFAULT"
    )
    assert entry["default"] == "1"


def test_phase_g_remote_transport_disabled_by_default():
    """Kill switch sécurité : remote transport interdit par défaut."""
    entry = next(
        e for e in _mcp_entries()
        if e["key"] == "LUMENA_MCP_REMOTE_TRANSPORT_DISABLED"
    )
    assert entry["default"] == "1"
    assert "switch" in entry["hint"].lower() or "kill" in entry["hint"].lower()
