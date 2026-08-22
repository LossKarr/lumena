"""Lot 5.6 — Fix A2 : verrouille la règle « MCP actif déjà présent → usage DIRECT ».

Bug d'origine (log 27/06) : sur « utilise le MCP météo actif », le chat partait en
`request_mcp_capability` → `no_safe_path` → `run_mcp_autonomy` → **ticket d'approbation
parasite**, alors que `mcp__…__weather_forecast` était déjà actif et appelable.

Fix A2 : section CAS 0 (prioritaire) — si un `mcp__<serveur>__<outil>` couvrant le besoin
existe déjà, l'appeler DIRECTEMENT ; `run_mcp_autonomy`/`request_mcp_capability` UNIQUEMENT
pour une capacité ABSENTE.
"""
from __future__ import annotations

from pathlib import Path

import pytest

SKILL_PATH = Path(__file__).parents[2] / "skills" / "mcp-builder" / "SKILL.md"


@pytest.fixture(scope="module")
def skill_content() -> str:
    return SKILL_PATH.read_text(encoding="utf-8")


def test_cas0_section_present(skill_content):
    assert "CAS 0" in skill_content, "Le skill doit avoir un CAS 0 prioritaire"


def test_cas0_says_call_directly(skill_content):
    idx = skill_content.find("CAS 0")
    section = skill_content[idx:idx + 1200]
    lc = section.lower()
    assert "directement" in lc, "CAS 0 doit dire d'appeler l'outil MCP directement"
    assert "mcp__" in section, "CAS 0 doit citer la convention mcp__<serveur>__<outil>"


def test_cas0_forbids_autonomy_when_active(skill_content):
    idx = skill_content.find("CAS 0")
    section = skill_content[idx:idx + 1200]
    # Doit dire de NE PAS utiliser request_mcp_capability / run_mcp_autonomy si déjà actif
    assert "run_mcp_autonomy" in section
    assert "request_mcp_capability" in section
    lc = section.lower()
    assert ("ni " in lc) or ("n'utilise" in lc) or ("absente" in lc), (
        "CAS 0 doit restreindre l'autonomie MCP aux capacités absentes"
    )


def test_cas1_scoped_to_missing(skill_content):
    # CAS 1 doit désormais viser une capacité ABSENTE (pas « utiliser » tout court).
    idx = skill_content.find("CAS 1")
    section = skill_content[idx:idx + 400]
    lc = section.lower()
    assert "absent" in lc or "manquante" in lc, (
        "CAS 1 doit être scopé à une capacité absente/manquante"
    )
