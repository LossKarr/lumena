"""
Tests Phase H — E2E chat flow MCP (mockés).

Couvre les 5 scénarios canoniques du plan :
  1. Découverte autonomy (intent libre → run_mcp_autonomy)
  2. Install direct (npm:... → add_mcp + categorie auto via cascade)
  3. URL GitHub → target_resolver → README → package_spec
  4. Overlap natif/MCP → set_mcp_preference → MCP visible
  5. Désactivation (disable_mcp → handler retiré)

Tests construits sur les outils Phase F + cascade Phase A/C + persistence Phase B.
Aucune dépendance réseau, tout est mocké via FakeCatalog / FakeActivationService.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from src.mcp.category_inference import infer_semantic_category, translate_human_to_category
from src.mcp.overlap_detector import detect_overlaps
from src.mcp.react_integration import (
    ADD_MCP_CONFIRMATION_PHRASE,
    DISABLE_MCP_CONFIRMATION_PHRASE,
    MCPReActIntegration,
    MCPReActIntegrationDeps,
    REMOVE_MCP_CONFIRMATION_PHRASE,
    SET_MCP_CATEGORY_CONFIRMATION_PHRASE,
    SET_MCP_PREFERENCE_CONFIRMATION_PHRASE,
)
from src.mcp.target_resolver import resolve_target


# ──────────────────────────────────────────────────────────────────────────────
# Fakes minimalistes
# ──────────────────────────────────────────────────────────────────────────────


@dataclass
class _FakeEntry:
    server_id: str
    package_spec: str
    semantic_category: Optional[str] = None
    prefer_over_native: bool = False
    status: str = "installed"


class _FakeCatalog:
    def __init__(self):
        self.entries: Dict[str, _FakeEntry] = {}
        self.events: List[tuple] = []

    def add(self, server_id, package_spec, **kwargs):
        e = _FakeEntry(server_id=server_id, package_spec=package_spec, **kwargs)
        self.entries[server_id] = e
        return e

    def get_server(self, sid):
        return self.entries.get(sid)

    def remove_server(self, sid):
        self.events.append(("remove", sid))
        return self.entries.pop(sid, None) is not None

    def update_prefer_over_native(self, sid, prefer):
        if sid not in self.entries:
            raise RuntimeError("server_not_found")
        e = self.entries[sid]
        new = _FakeEntry(
            server_id=e.server_id, package_spec=e.package_spec,
            semantic_category=e.semantic_category, prefer_over_native=prefer,
            status=e.status,
        )
        self.entries[sid] = new
        self.events.append(("prefer", sid, prefer))
        return new

    def update_semantic_category(self, sid, category, source):
        if sid not in self.entries:
            raise RuntimeError("server_not_found")
        e = self.entries[sid]
        new = _FakeEntry(
            server_id=e.server_id, package_spec=e.package_spec,
            semantic_category=category, prefer_over_native=e.prefer_over_native,
            status=e.status,
        )
        self.entries[sid] = new
        self.events.append(("category", sid, category, source))
        return new


class _FakeActivationService:
    def __init__(self, success=True):
        self.success = success
        self.events: List[tuple] = []

    def deactivate(self, sid):
        self.events.append(("deactivate", sid))
        return type("R", (), {"success": self.success, "reason": ""})()


class _FakeOrchestrator:
    def __init__(self):
        self.calls: List[Dict[str, Any]] = []

    def propose(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "server_id": "auto-generated",
            "package_spec": kwargs.get("package_spec"),
            "status": "declared",
        }


def _integration(*, catalog=None, activation=None, orch=None):
    return MCPReActIntegration(MCPReActIntegrationDeps(
        catalog=catalog,
        activation_service=activation,
        catalog_add_orchestrator=orch,
    ))


def _parse(json_str: str) -> Dict[str, Any]:
    return json.loads(json_str)


async def _call(handler, **kwargs):
    return await handler(**kwargs)


# ══════════════════════════════════════════════════════════════════════════════
# Scénario 1 — Découverte intent libre
# ══════════════════════════════════════════════════════════════════════════════


class TestE2EScenario1Discovery:
    """User : « trouve-moi un MCP pour scraper »
    → add_mcp dry-run → kind="intent" → recommendation_code=mcp_target_resolved.
    """

    @pytest.mark.asyncio
    async def test_step1_user_free_text_resolves_as_intent(self):
        integration = _integration()
        out = await _call(
            integration._make_add_mcp_handler(),
            target="trouve-moi un MCP pour scraper",
            caller_kind="react",
        )
        data = _parse(out)
        assert data["decision"] == "ok"
        assert data["payload"]["kind"] == "intent"
        assert data["payload"]["dry_run"] is True

    @pytest.mark.asyncio
    async def test_step2_research_agent_can_dry_run(self):
        """Doctrine : un agent recherche peut explorer en dry-run sans react."""
        out = await _call(
            _integration()._make_add_mcp_handler(),
            target="je voudrais lire mes mails gmail",
            caller_kind="research_agent",
        )
        assert _parse(out)["decision"] == "ok"

    @pytest.mark.asyncio
    async def test_step3_live_intent_requires_react(self):
        """Mutation live n'est jamais autorisée pour un agent recherche."""
        out = await _call(
            _integration()._make_add_mcp_handler(),
            target="trouve un MCP pour scraper",
            live=True,
            confirmation_phrase=ADD_MCP_CONFIRMATION_PHRASE,
            caller_kind="research_agent",
        )
        assert _parse(out)["blockers"] == ["caller_kind_not_allowed"]


# ══════════════════════════════════════════════════════════════════════════════
# Scénario 2 — Install direct (npm:...) + cascade catégorie auto
# ══════════════════════════════════════════════════════════════════════════════


class TestE2EScenario2DirectInstall:
    """User : « installe ce MCP : npm:@modelcontextprotocol/server-gmail »
    → add_mcp dry-run → package_spec direct → propose live → catégorie auto.
    """

    def test_step1_resolve_target_package_spec(self):
        r = resolve_target("npm:@modelcontextprotocol/server-gmail")
        assert r.kind == "package_spec"
        assert r.package_spec == "npm:@modelcontextprotocol/server-gmail"

    @pytest.mark.asyncio
    async def test_step2_add_mcp_dry_run_returns_payload(self):
        out = await _call(
            _integration()._make_add_mcp_handler(),
            target="npm:@modelcontextprotocol/server-gmail",
            caller_kind="react",
        )
        data = _parse(out)
        assert data["payload"]["kind"] == "package_spec"
        assert data["payload"]["recommendation_code"] == "mcp_target_resolved"

    @pytest.mark.asyncio
    async def test_step3_add_mcp_live_calls_orchestrator(self, monkeypatch):
        # Phase I-8 (Fix AB) : pas de sonde registry réelle en test —
        # None = réseau indisponible → le flux d'origine continue.
        import src.mcp.target_resolver as _tr
        monkeypatch.setattr(
            _tr, "probe_package_exists", lambda spec, **kw: None
        )
        orch = _FakeOrchestrator()
        out = await _call(
            _integration(orch=orch)._make_add_mcp_handler(),
            target="npm:@modelcontextprotocol/server-gmail",
            live=True,
            confirmation_phrase=ADD_MCP_CONFIRMATION_PHRASE,
            caller_kind="react",
        )
        data = _parse(out)
        assert data["decision"] == "ok"
        assert data["payload"]["recommendation_code"] == "mcp_added"
        assert orch.calls[0]["package_spec"] == "npm:@modelcontextprotocol/server-gmail"

    def test_step4_cascade_auto_classifies_gmail_as_mail(self):
        """Phase A : la cascade fait gmail → mail via _MCP_SERVER_NAME_TO_SEMANTIC."""
        category, source = infer_semantic_category(
            server_name="gmail-mcp",
            tool_descriptions=["Send an email"],
        )
        assert category == "mail"
        assert source == "static"


# ══════════════════════════════════════════════════════════════════════════════
# Scénario 3 — URL GitHub → README → package_spec dérivé
# ══════════════════════════════════════════════════════════════════════════════


class TestE2EScenario3GithubFlow:
    """User : « installe : https://github.com/foo/mcp-bar »
    → target_resolver fetch README → npm install → derive npm:...
    """

    @pytest.fixture(autouse=True)
    def _no_network_default_fetch(self, monkeypatch):
        """Fix AS : neutralise le fetcher README par défaut (réseau réel)."""
        monkeypatch.setattr(
            "src.mcp.target_resolver._default_github_readme_fetch",
            lambda url: "",
        )

    def test_step1_github_url_kind_detected(self):
        r = resolve_target("https://github.com/foo/mcp-bar")
        assert r.kind == "github_url"
        assert r.source_url == "https://github.com/foo/mcp-bar"

    def test_step2_readme_extracts_npm_install(self):
        readme = "# mcp-bar\n\nUsage:\n```\nnpm install -g @foo/mcp-bar\n```\n"
        r = resolve_target(
            "https://github.com/foo/mcp-bar",
            web_fetch_callable=lambda u: readme,
        )
        assert r.package_spec == "npm:@foo/mcp-bar"

    @pytest.mark.asyncio
    async def test_step3_add_mcp_dry_run_with_github_url(self):
        out = await _call(
            _integration()._make_add_mcp_handler(),
            target="https://github.com/foo/mcp-bar",
            caller_kind="react",
        )
        data = _parse(out)
        # En dry-run sans fetcher injecté → package_spec=None mais kind=github_url
        assert data["payload"]["kind"] == "github_url"
        assert data["payload"]["source_url"] == "https://github.com/foo/mcp-bar"


# ══════════════════════════════════════════════════════════════════════════════
# Scénario 4 — Overlap natif/MCP + set_mcp_preference
# ══════════════════════════════════════════════════════════════════════════════


class TestE2EScenario4OverlapPreference:
    """Activation détecte overlap → annonce dans chat → user dit "préfère MCP"
    → set_mcp_preference(True) → catalog persiste prefer_over_native=True.
    """

    def test_step1_overlap_detected_between_gmail_and_send_email(self):
        @dataclass
        class _T:
            name: str
            description: str = ""
        matches = detect_overlaps(
            server_name="gmail",
            mcp_tools=[_T("send_message", "Send an email message to a recipient")],
            native_handler_names=["send_email"],
            native_descriptions={
                "send_email": "Send an email via SMTP to a recipient",
            },
        )
        assert len(matches) == 1
        assert matches[0].native_tool_name == "send_email"

    @pytest.mark.asyncio
    async def test_step2_user_sets_preference_to_true(self):
        cat = _FakeCatalog()
        cat.add("gmail", "npm:server-gmail")
        out = await _call(
            _integration(catalog=cat)._make_set_mcp_preference_handler(),
            server_id="gmail",
            prefer_over_native=True,
            confirmation_phrase=SET_MCP_PREFERENCE_CONFIRMATION_PHRASE,
            caller_kind="react",
        )
        data = _parse(out)
        assert data["decision"] == "ok"
        assert data["payload"]["prefer_over_native"] is True
        assert cat.entries["gmail"].prefer_over_native is True

    @pytest.mark.asyncio
    async def test_step3_preference_without_confirmation_blocked(self):
        out = await _call(
            _integration(catalog=_FakeCatalog())._make_set_mcp_preference_handler(),
            server_id="gmail",
            prefer_over_native=True,
            confirmation_phrase="not-the-phrase",
            caller_kind="react",
        )
        assert _parse(out)["blockers"] == ["confirmation_phrase_invalid"]

    @pytest.mark.asyncio
    async def test_step4_set_category_via_human_phrase(self):
        """User dit "messagerie" → cascade HUMAN_TO_CATEGORY → "mail" persisté."""
        cat = _FakeCatalog()
        cat.add("gmail", "npm:server-gmail")
        out = await _call(
            _integration(catalog=cat)._make_set_mcp_category_handler(),
            server_id="gmail",
            human_phrase="messagerie",
            confirmation_phrase=SET_MCP_CATEGORY_CONFIRMATION_PHRASE,
            caller_kind="react",
        )
        data = _parse(out)
        assert data["payload"]["semantic_category"] == "mail"
        assert cat.entries["gmail"].semantic_category == "mail"
        assert ("category", "gmail", "mail", "user_override") in cat.events


# ══════════════════════════════════════════════════════════════════════════════
# Scénario 5 — Désactivation + suppression
# ══════════════════════════════════════════════════════════════════════════════


class TestE2EScenario5Disable:
    """User : « désactive le MCP gmail » → disable_mcp avec confirmation
    → activation_service.deactivate(gmail) → handler retiré du registry.
    """

    @pytest.mark.asyncio
    async def test_step1_disable_requires_confirmation(self):
        out = await _call(
            _integration(activation=_FakeActivationService())._make_disable_mcp_handler(),
            server_id="gmail",
            confirmation_phrase="non",
            caller_kind="react",
        )
        assert _parse(out)["blockers"] == ["confirmation_phrase_invalid"]

    @pytest.mark.asyncio
    async def test_step2_disable_succeeds(self):
        svc = _FakeActivationService(success=True)
        out = await _call(
            _integration(activation=svc)._make_disable_mcp_handler(),
            server_id="gmail",
            confirmation_phrase=DISABLE_MCP_CONFIRMATION_PHRASE,
            caller_kind="react",
        )
        data = _parse(out)
        assert data["payload"]["recommendation_code"] == "mcp_disabled"
        assert svc.events == [("deactivate", "gmail")]

    @pytest.mark.asyncio
    async def test_step3_remove_requires_confirmation(self):
        cat = _FakeCatalog()
        cat.add("gmail", "npm:server-gmail")
        out = await _call(
            _integration(catalog=cat)._make_remove_mcp_handler(),
            server_id="gmail",
            confirmation_phrase="non",
            caller_kind="react",
        )
        assert _parse(out)["blockers"] == ["confirmation_phrase_invalid"]

    @pytest.mark.asyncio
    async def test_step4_remove_succeeds_and_drops_entry(self):
        cat = _FakeCatalog()
        cat.add("gmail", "npm:server-gmail")
        out = await _call(
            _integration(catalog=cat)._make_remove_mcp_handler(),
            server_id="gmail",
            confirmation_phrase=REMOVE_MCP_CONFIRMATION_PHRASE,
            caller_kind="react",
        )
        data = _parse(out)
        assert data["payload"]["recommendation_code"] == "mcp_removed"
        assert "gmail" not in cat.entries

    @pytest.mark.asyncio
    async def test_step5_full_lifecycle_disable_then_remove(self):
        """Cycle complet : install → disable → remove."""
        cat = _FakeCatalog()
        cat.add("gmail", "npm:server-gmail")
        svc = _FakeActivationService(success=True)
        integration = _integration(catalog=cat, activation=svc)
        # 1. Désactiver
        out_d = await _call(
            integration._make_disable_mcp_handler(),
            server_id="gmail",
            confirmation_phrase=DISABLE_MCP_CONFIRMATION_PHRASE,
            caller_kind="react",
        )
        assert _parse(out_d)["payload"]["recommendation_code"] == "mcp_disabled"
        # 2. Supprimer
        out_r = await _call(
            integration._make_remove_mcp_handler(),
            server_id="gmail",
            confirmation_phrase=REMOVE_MCP_CONFIRMATION_PHRASE,
            caller_kind="react",
        )
        assert _parse(out_r)["payload"]["recommendation_code"] == "mcp_removed"
        assert "gmail" not in cat.entries


# ══════════════════════════════════════════════════════════════════════════════
# Scénario 6 — Garde-fous transverses
# ══════════════════════════════════════════════════════════════════════════════


class TestE2ECrossCuttingGuards:
    @pytest.mark.asyncio
    async def test_all_mutations_block_code_agent_caller(self):
        cat = _FakeCatalog()
        cat.add("alice", "npm:foo")
        integration = _integration(catalog=cat, activation=_FakeActivationService())
        # Toutes les mutations doivent bloquer code_agent
        results = []
        results.append(await _call(
            integration._make_add_mcp_handler(),
            target="npm:foo", live=True,
            confirmation_phrase=ADD_MCP_CONFIRMATION_PHRASE,
            caller_kind="code_agent",
        ))
        results.append(await _call(
            integration._make_disable_mcp_handler(),
            server_id="alice",
            confirmation_phrase=DISABLE_MCP_CONFIRMATION_PHRASE,
            caller_kind="code_agent",
        ))
        results.append(await _call(
            integration._make_remove_mcp_handler(),
            server_id="alice",
            confirmation_phrase=REMOVE_MCP_CONFIRMATION_PHRASE,
            caller_kind="code_agent",
        ))
        results.append(await _call(
            integration._make_set_mcp_preference_handler(),
            server_id="alice", prefer_over_native=True,
            confirmation_phrase=SET_MCP_PREFERENCE_CONFIRMATION_PHRASE,
            caller_kind="code_agent",
        ))
        results.append(await _call(
            integration._make_set_mcp_category_handler(),
            server_id="alice", human_phrase="messagerie",
            confirmation_phrase=SET_MCP_CATEGORY_CONFIRMATION_PHRASE,
            caller_kind="code_agent",
        ))
        for out in results:
            assert _parse(out)["blockers"] == ["code_agent_out_of_scope"]

    def test_human_to_category_covers_common_user_words(self):
        """L'utilisateur n'a jamais à apprendre les catégories techniques."""
        cases = {
            "messagerie": "mail",
            "boulot": "project",
            "fichiers": "files",
        }
        for human, technical in cases.items():
            assert translate_human_to_category(human) == technical, \
                f"{human!r} doit traduire vers {technical!r}"

    def test_system_prompt_contains_mcp_rules(self):
        """Phase H : les 5 règles MCP doivent être présentes dans le prompt."""
        from src.prompts.builder import PromptBuilder
        prompt = PromptBuilder().build()
        # 1) Section dédiée
        assert "Règles MCP Conversationnelles" in prompt
        # 2) Marqueur règle "consentement verbal"
        assert "consentement verbal" in prompt
        # 3) Règle "JAMAIS la confirmation_phrase"
        assert "JAMAIS" in prompt and "confirmation_phrase" in prompt
        # 4) Règle préférence natif par défaut
        assert "NATIF" in prompt and "prefer_over_native" in prompt
        # 5) Règle langage humain
        assert "langage humain" in prompt
        assert "messagerie" in prompt
