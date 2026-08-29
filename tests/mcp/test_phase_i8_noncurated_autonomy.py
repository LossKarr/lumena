"""Phase I-8 — Autonomie MCP non-curated de bout en bout.

Contexte runtime (2026-06-11 00:13→00:18) : « install un mcp meteo et test
le » a échoué — 3 tickets créés pour zéro install, payload « ready_to_use »
mensonger, package npm halluciné accepté, guidance demandant à l'utilisateur
de taper la confirmation phrase.

Frontière de confiance I-8 : l'approbation humaine du catalog_add est le
SEUL gate humain pour un MCP non-curated. Tout l'aval (install, activate,
usage) est autonome.

Fixes couverts :
  AA.1 — bypass auto-approve install/activate des entrées au catalogue
  AA.2 — force install des entrées DECLARED dans run_mcp_autonomy
  AA.3 — plus jamais de « ready_to_use » mensonger
  AA.4 — plancher trust 70 au catalog_add approuvé par l'humain
  AB   — sonde d'existence npm/PyPI (anti-package halluciné)
  AC   — capability_tags : reconnaissance des entrées par intent
  AD   — guidance : ne JAMAIS demander la phrase à l'utilisateur
  AF   — guards : formes passives + « avec succès » (anti-hallucination)
  AG   — fallback policy conservateur pour tools inclassables
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


REPO = Path(__file__).parents[2]


# ──────────────────────────────────────────────────────────────────────────────
# Helpers communs
# ──────────────────────────────────────────────────────────────────────────────


def _entry(
    sid="weather-mcp",
    status_value="declared",
    package_spec="npm:mcp-weather-server",
    trust_score=72,
    version=None,
    capability_tags=("meteo", "weather"),
    display_name="mcp-weather-server",
):
    e = MagicMock()
    e.server_id = sid
    e.status = MagicMock(value=status_value)
    e.package_spec = package_spec
    e.trust_score = trust_score
    e.version = version
    e.capability_tags = capability_tags
    e.display_name = display_name
    return e


def _make_integration(**deps_kwargs):
    from src.mcp.react_integration import (
        MCPReActIntegration,
        MCPReActIntegrationDeps,
    )
    return MCPReActIntegration(MCPReActIntegrationDeps(**deps_kwargs))


# ──────────────────────────────────────────────────────────────────────────────
# Fix AC — derive_capability_tags + matching
# ──────────────────────────────────────────────────────────────────────────────


class TestFixACDeriveCapabilityTags:

    def test_strips_lifecycle_tokens(self):
        """Les tokens du cycle de vie MCP ne doivent JAMAIS devenir des tags
        (ils matcheraient tout intent MCP futur → faux positifs)."""
        from src.mcp.capability_resolver import derive_capability_tags
        tags = derive_capability_tags("installe et active le MCP météo")
        assert "mcp" not in tags
        assert "installe" not in tags
        assert "active" not in tags
        assert "meteo" in tags  # désaccentué par _tokenize

    def test_accent_folding_fr(self):
        from src.mcp.capability_resolver import derive_capability_tags
        assert "meteo" in derive_capability_tags("la météo de Paris")

    def test_package_name_tokens_included(self):
        from src.mcp.capability_resolver import derive_capability_tags
        tags = derive_capability_tags("utiliser un MCP météo mcp-weather-server")
        assert "weather" in tags
        assert "meteo" in tags

    def test_cap_at_16(self):
        from src.mcp.capability_resolver import derive_capability_tags
        text = " ".join(f"motunique{i}" for i in range(40))
        assert len(derive_capability_tags(text)) <= 16

    def test_empty_intent_empty_tags(self):
        from src.mcp.capability_resolver import derive_capability_tags
        assert derive_capability_tags("") == ()


class TestFixACTagsMatchScore:

    def test_single_shared_token_reaches_declared_threshold(self):
        """1 seul token discriminant partagé doit suffire (indépendant de
        la taille de l'intent, contrairement à Jaccard)."""
        from src.mcp.capability_resolver import (
            _tags_match_score,
            _MATCH_DECLARED_MIN,
        )
        score = _tags_match_score(
            {"obtenir", "meteo", "actuelle", "ville"}, {"meteo", "weather"}
        )
        assert score >= _MATCH_DECLARED_MIN

    def test_no_overlap_zero(self):
        from src.mcp.capability_resolver import _tags_match_score
        assert _tags_match_score({"github", "repo"}, {"meteo", "weather"}) == 0.0

    def test_capped_at_07(self):
        from src.mcp.capability_resolver import _tags_match_score
        toks = {f"t{i}" for i in range(20)}
        assert _tags_match_score(toks, toks) == pytest.approx(0.7)

    def test_empty_sets_zero(self):
        from src.mcp.capability_resolver import _tags_match_score
        assert _tags_match_score(set(), {"a"}) == 0.0
        assert _tags_match_score({"a"}, set()) == 0.0


class TestFixACResolverRecognition:
    """Le resolver doit reconnaître une entrée DECLARED/INSTALLED via ses
    capability_tags — c'est ce qui casse le churn de tickets observé."""

    def _resolver(self, entries):
        from src.mcp.capability_resolver import (
            CapabilityResolver,
            CapabilityResolverDeps,
        )
        catalog = MagicMock()
        catalog.list_servers = MagicMock(return_value=entries)
        return CapabilityResolver(CapabilityResolverDeps(catalog=catalog))

    def test_declared_entry_matched_by_tags(self):
        from src.mcp.capability_resolver import _tokenize
        resolver = self._resolver([_entry(status_value="declared")])
        intent_tokens = _tokenize("obtenir la météo actuelle pour une ville")
        cands = resolver._build_mcp_declared_candidates(
            intent_tokens, [_entry(status_value="declared")]
        )
        assert cands, "Entrée DECLARED invisible malgré tags matchés"
        assert cands[0].server_id == "weather-mcp"
        assert cands[0].match_score >= 0.5

    def test_declared_entry_no_false_positive(self):
        from src.mcp.capability_resolver import _tokenize
        resolver = self._resolver([])
        intent_tokens = _tokenize("envoie un message sur le canal général")
        cands = resolver._build_mcp_declared_candidates(
            intent_tokens, [_entry(status_value="declared")]
        )
        assert cands == []

    def test_installed_entry_fallback_tags(self):
        """Entrée INSTALLED non-curated sans DiscoveryReport → le fallback
        tags doit produire un candidat (sinon jamais d'activation)."""
        from src.mcp.capability_resolver import _tokenize
        resolver = self._resolver([])
        intent_tokens = _tokenize("donne moi la météo de Lyon")
        cands = resolver._build_mcp_installed_candidates(
            intent_tokens,
            [_entry(status_value="installed")],
            {},  # aucun DiscoveryReport
        )
        assert cands, "Entrée INSTALLED non-curated invisible sans discovery"
        assert cands[0].kind == "mcp_installed"

    def test_entry_without_tags_backward_compat(self):
        """Entrée pré-I-8 (tags None) → comportement display_name inchangé."""
        from src.mcp.capability_resolver import _tokenize
        resolver = self._resolver([])
        e = _entry(capability_tags=None, display_name="weather server")
        intent_tokens = _tokenize("weather please")
        cands = resolver._build_mcp_declared_candidates(intent_tokens, [e])
        assert cands  # match via display_name Jaccard


# ──────────────────────────────────────────────────────────────────────────────
# Fix AC — persistence ServerCatalog
# ──────────────────────────────────────────────────────────────────────────────


class TestFixACCatalogPersistence:

    def _catalog(self, tmp_path):
        from src.mcp.server_catalog import MCPServerCatalog
        secrets = MagicMock()
        secrets.get = MagicMock(return_value="dGVzdC1obWFjLWtleS0zMi1ieXRlcy1sb25nLXg=")
        return MCPServerCatalog(
            catalog_dir=tmp_path / "catalog",
            secrets_service=secrets,
        )

    def test_roundtrip_tags(self, tmp_path):
        cat = self._catalog(tmp_path)
        cat.add_server(
            server_id="weather-mcp",
            display_name="Weather MCP",
            package_spec="npm:mcp-weather-server",
            owner_profile="lumena",
            trust_score=70,
            capability_tags=["meteo", "weather"],
        )
        loaded = cat.get_server("weather-mcp")
        assert loaded is not None, "HMAC/binding doit rester valide avec tags"
        assert loaded.capability_tags == ("meteo", "weather")

    def test_no_tags_backward_compat(self, tmp_path):
        cat = self._catalog(tmp_path)
        cat.add_server(
            server_id="plain",
            display_name="Plain",
            package_spec="npm:plain-mcp",
            owner_profile="lumena",
            trust_score=70,
        )
        loaded = cat.get_server("plain")
        assert loaded is not None
        assert loaded.capability_tags is None
        # Le champ ne doit PAS être émis dans le JSON (back-compat HMAC)
        raw = json.loads(
            (tmp_path / "catalog" / "servers" / "plain.json").read_text(
                encoding="utf-8"
            )
        )
        assert "capability_tags" not in raw["entry"]

    def test_invalid_tags_rejected(self, tmp_path):
        from src.mcp.server_catalog import CatalogError
        cat = self._catalog(tmp_path)
        with pytest.raises(CatalogError):
            cat.add_server(
                server_id="bad",
                display_name="Bad",
                package_spec="npm:bad-mcp",
                owner_profile="lumena",
                trust_score=70,
                capability_tags=["ok", "x" * 100],  # tag trop long
            )
        with pytest.raises(CatalogError):
            cat.add_server(
                server_id="bad2",
                display_name="Bad2",
                package_spec="npm:bad2-mcp",
                owner_profile="lumena",
                trust_score=70,
                capability_tags=[f"t{i}" for i in range(30)],  # trop de tags
            )


# ──────────────────────────────────────────────────────────────────────────────
# Fix AA.4 — plancher trust au catalog_add approuvé
# ──────────────────────────────────────────────────────────────────────────────


class TestFixAA4TrustFloor:

    def _orchestrator(self, catalog):
        from src.mcp.catalog_add_orchestrator import MCPCatalogAddOrchestrator
        queue = MagicMock()
        queue.propose = MagicMock(return_value="t" * 32)
        return MCPCatalogAddOrchestrator(catalog=catalog, approval_queue=queue)

    def _approval(self, sid, trust_score, tags=None):
        from src.mcp.approval_queue import ApprovalResult, ApprovalDecision
        args = {
            "action": "catalog_add",
            "server_id": sid,
            "display_name": "Weather",
            "package_spec": "npm:mcp-weather-server",
            "version": None,
            "trust_score": trust_score,
            "owner_profile": "lumena",
        }
        if tags is not None:
            args["capability_tags"] = tags
        return ApprovalResult(
            decision=ApprovalDecision.APPROVED, args=args, reason="test"
        )

    def _capture_catalog(self):
        catalog = MagicMock()
        catalog.get_server = MagicMock(return_value=None)
        added = {}

        def _add(**kwargs):
            added.update(kwargs)
            e = MagicMock()
            e.status = MagicMock(value="declared")
            return e

        catalog.add_server = MagicMock(side_effect=_add)
        return catalog, added

    def test_low_prescore_floored_to_70(self):
        """Pre-score réseau 40 + approbation humaine → trust 70 (sinon
        execute_approved_install bloquerait avec trust_too_low)."""
        catalog, added = self._capture_catalog()
        orch = self._orchestrator(catalog)
        result = orch.execute_approved_catalog_add(
            "weather-mcp", self._approval("weather-mcp", 40), dry_run=False
        )
        assert result.success is True
        assert added["trust_score"] == 70

    def test_high_prescore_preserved(self):
        catalog, added = self._capture_catalog()
        orch = self._orchestrator(catalog)
        orch.execute_approved_catalog_add(
            "weather-mcp", self._approval("weather-mcp", 85), dry_run=False
        )
        assert added["trust_score"] == 85

    def test_floor_never_reaches_secrets_threshold(self):
        """Le plancher est 70, JAMAIS 90 (seuil SECRETS inatteignable)."""
        from src.mcp.catalog_add_orchestrator import (
            _HUMAN_APPROVED_TRUST_FLOOR,
        )
        assert _HUMAN_APPROVED_TRUST_FLOOR == 70
        assert _HUMAN_APPROVED_TRUST_FLOOR < 90

    def test_capability_tags_flow_to_entry(self):
        catalog, added = self._capture_catalog()
        orch = self._orchestrator(catalog)
        orch.execute_approved_catalog_add(
            "weather-mcp",
            self._approval("weather-mcp", 70, tags=["meteo", "weather"]),
            dry_run=False,
        )
        assert added.get("capability_tags") == ["meteo", "weather"]

    def test_legacy_catalog_without_tags_kwarg(self):
        """Catalog mock legacy (add_server sans capability_tags) → retry
        sans le kwarg, jamais d'échec du chemin principal."""
        catalog = MagicMock()
        catalog.get_server = MagicMock(return_value=None)
        calls = []

        def _add_legacy(**kwargs):
            if "capability_tags" in kwargs:
                raise TypeError("unexpected keyword argument")
            calls.append(kwargs)
            e = MagicMock()
            e.status = MagicMock(value="declared")
            return e

        catalog.add_server = MagicMock(side_effect=_add_legacy)
        orch = self._orchestrator(catalog)
        result = orch.execute_approved_catalog_add(
            "weather-mcp",
            self._approval("weather-mcp", 70, tags=["meteo"]),
            dry_run=False,
        )
        assert result.success is True
        assert calls, "Le retry sans kwarg doit avoir abouti"


# ──────────────────────────────────────────────────────────────────────────────
# Fix AA.1 — bypass auto-approve des install/activate au catalogue
# ──────────────────────────────────────────────────────────────────────────────


class TestFixAA1CatalogedBypass:

    def _check(self, tool, sid, entry, payload=None):
        from src.mcp.react_integration import _is_cataloged_install_ticket
        catalog = MagicMock()
        catalog.get_server = MagicMock(return_value=entry)
        return _is_cataloged_install_ticket(
            tool, sid, payload or {}, catalog
        )

    def test_install_of_declared_entry_trusted(self):
        assert self._check(
            "mcp_install:weather-mcp", "weather-mcp", _entry()
        ) is True

    def test_activate_of_installed_entry_trusted(self):
        assert self._check(
            "mcp_activate:weather-mcp", "weather-mcp",
            _entry(status_value="installed"),
        ) is True

    def test_catalog_add_NEVER_bypassed(self):
        """Le catalog_add est LE gate humain — jamais de bypass non-curated."""
        assert self._check(
            "mcp_catalog_add:weather-mcp", "weather-mcp", _entry()
        ) is False

    def test_quarantined_refused(self):
        assert self._check(
            "mcp_install:weather-mcp", "weather-mcp",
            _entry(status_value="quarantined"),
        ) is False

    def test_entry_absent_refused(self):
        assert self._check(
            "mcp_install:weather-mcp", "weather-mcp", None
        ) is False

    def test_package_substitution_refused(self):
        """Anti-substitution : package_spec du ticket ≠ catalogue → refus."""
        assert self._check(
            "mcp_install:weather-mcp", "weather-mcp", _entry(),
            payload={"package_spec": "npm:evil-package"},
        ) is False

    def test_local_create_never_bypassed(self):
        assert self._check(
            "mcp_local_create:weather-mcp", "weather-mcp", _entry()
        ) is False


# ──────────────────────────────────────────────────────────────────────────────
# Fix AA.2 + AA.3 — force install des DECLARED + honnêteté des statuts
# ──────────────────────────────────────────────────────────────────────────────


class TestFixAA2ForceInstallDeclared:

    def _setup(self, monkeypatch, *, live=True, install_success=True):
        monkeypatch.setenv("LUMENA_MCP_LIVE", "1" if live else "0")
        declared = _entry(status_value="declared")
        installed = _entry(status_value="installed")
        catalog = MagicMock()
        # 1er get → declared, relecture post-install → installed
        catalog.get_server = MagicMock(side_effect=[declared, installed])
        activation = MagicMock()
        activation.activate = MagicMock(return_value=MagicMock(success=True))
        install_orch = MagicMock()
        install_orch.execute_approved_install = MagicMock(
            return_value=MagicMock(
                success=install_success,
                reason="ok" if install_success else "trust_too_low_for_install",
            )
        )
        integ = _make_integration(
            catalog=catalog,
            activation_service=activation,
            install_orchestrator=install_orch,
        )
        return integ, install_orch, activation

    def test_declared_chains_install_then_activate(self, monkeypatch):
        """Le cœur du Fix AA : DECLARED → install forgé → activate →
        autonomy_activated. Plus jamais de « rien à faire ici »."""
        integ, install_orch, activation = self._setup(monkeypatch)
        out = integ._force_activate_if_needed(
            {"target_server_id": "weather-mcp",
             "recommendation_code": "autonomy_ready_to_use"}
        )
        assert out.get("force_install_attempted") is True
        assert out.get("force_install_ok") is True
        install_orch.execute_approved_install.assert_called_once()
        activation.activate.assert_called_once()
        assert out.get("recommendation_code") == "autonomy_activated"

    def test_forged_approval_mirrors_catalog(self, monkeypatch):
        """Les args forgés doivent refléter EXACTEMENT le catalogue
        (anti-confused-deputy d'execute_approved_install)."""
        integ, install_orch, _ = self._setup(monkeypatch)
        integ._force_activate_if_needed({"target_server_id": "weather-mcp"})
        _, forged = install_orch.execute_approved_install.call_args[0]
        assert forged.args["server_id"] == "weather-mcp"
        assert forged.args["package_spec"] == "npm:mcp-weather-server"
        assert forged.args["package_name"] == "mcp-weather-server"
        assert forged.args["transport"] == "npm"
        assert forged.args["trust_score"] == 72

    def test_install_failure_is_honest(self, monkeypatch):
        """Échec install → autonomy_install_failed avec raison, JAMAIS
        un code « ready » mensonger (AA.3)."""
        integ, _, activation = self._setup(
            monkeypatch, install_success=False
        )
        out = integ._force_activate_if_needed(
            {"target_server_id": "weather-mcp",
             "recommendation_code": "autonomy_ready_to_use"}
        )
        assert out.get("recommendation_code") == "autonomy_install_failed"
        assert "trust_too_low" in out.get("force_install_reason", "")
        activation.activate.assert_not_called()

    def test_dry_run_payload_never_installs(self, monkeypatch):
        """Un payload dry_run ne doit JAMAIS déclencher un npm install,
        même avec LUMENA_MCP_LIVE=1."""
        integ, install_orch, _ = self._setup(monkeypatch, live=True)
        out = integ._force_activate_if_needed(
            {"target_server_id": "weather-mcp", "dry_run": True}
        )
        install_orch.execute_approved_install.assert_not_called()
        assert out.get("force_install_skipped") == "dry_run"

    def test_live_requested_false_never_installs(self, monkeypatch):
        integ, install_orch, _ = self._setup(monkeypatch, live=True)
        out = integ._force_activate_if_needed(
            {"target_server_id": "weather-mcp"}, live_requested=False
        )
        install_orch.execute_approved_install.assert_not_called()
        assert out.get("force_install_skipped") == "dry_run"

    def test_not_live_skips_install_honestly(self, monkeypatch):
        integ, install_orch, _ = self._setup(monkeypatch, live=False)
        out = integ._force_activate_if_needed(
            {"target_server_id": "weather-mcp",
             "recommendation_code": "autonomy_ready_to_use"}
        )
        install_orch.execute_approved_install.assert_not_called()
        assert out.get("recommendation_code") == "needs_install_approval"

    def test_no_install_orchestrator_honest_reason(self, monkeypatch):
        monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
        catalog = MagicMock()
        catalog.get_server = MagicMock(
            return_value=_entry(status_value="declared")
        )
        integ = _make_integration(
            catalog=catalog, activation_service=MagicMock()
        )
        out = integ._force_activate_if_needed(
            {"target_server_id": "weather-mcp"}
        )
        assert out.get("force_install_ok") is False
        assert out.get("force_install_reason") == "no_install_orchestrator"


class TestFixAA3HonestStatus:

    def test_quarantined_downgrades_ready_claim(self):
        catalog = MagicMock()
        catalog.get_server = MagicMock(
            return_value=_entry(status_value="quarantined")
        )
        integ = _make_integration(
            catalog=catalog, activation_service=MagicMock()
        )
        out = integ._force_activate_if_needed(
            {"target_server_id": "weather-mcp",
             "recommendation_code": "autonomy_ready_to_use"}
        )
        assert out["recommendation_code"] == "autonomy_blocked_status"
        assert out["catalog_status_observed"] == "quarantined"

    def test_active_and_running_keeps_ready(self):
        """Un serveur RÉELLEMENT actif garde son code ready (vérité)."""
        catalog = MagicMock()
        catalog.get_server = MagicMock(
            return_value=_entry(status_value="active")
        )
        activation = MagicMock()
        activation.is_running = MagicMock(return_value=True)
        integ = _make_integration(
            catalog=catalog, activation_service=activation
        )
        out = integ._force_activate_if_needed(
            {"target_server_id": "weather-mcp",
             "recommendation_code": "autonomy_ready_to_use"}
        )
        assert out["recommendation_code"] == "autonomy_ready_to_use"


# ──────────────────────────────────────────────────────────────────────────────
# Fix AB — sonde d'existence registry
# ──────────────────────────────────────────────────────────────────────────────


class TestFixABPackageExistenceProbe:

    def test_npm_200_exists(self):
        from src.mcp.target_resolver import probe_package_exists
        assert probe_package_exists(
            "npm:real-package", http_status_callable=lambda url: 200
        ) is True

    def test_npm_404_not_found(self):
        from src.mcp.target_resolver import probe_package_exists
        assert probe_package_exists(
            "npm:@nicholaschen/weather-mcp",
            http_status_callable=lambda url: 404,
        ) is False

    def test_scoped_name_url_encoded(self):
        from src.mcp.target_resolver import probe_package_exists
        seen = {}

        def _probe(url):
            seen["url"] = url
            return 200

        probe_package_exists(
            "npm:@scope/pkg", http_status_callable=_probe
        )
        assert "%2F" in seen["url"]
        assert "@scope" in seen["url"]

    def test_pypi_route(self):
        from src.mcp.target_resolver import probe_package_exists
        seen = {}

        def _probe(url):
            seen["url"] = url
            return 200

        assert probe_package_exists(
            "pypi:mcp-server-x", http_status_callable=_probe
        ) is True
        assert "pypi.org/pypi/mcp-server-x/json" in seen["url"]

    def test_network_unavailable_returns_none(self):
        from src.mcp.target_resolver import probe_package_exists
        assert probe_package_exists(
            "npm:whatever", http_status_callable=lambda url: None
        ) is None

    def test_local_transport_none(self):
        from src.mcp.target_resolver import probe_package_exists
        assert probe_package_exists("local:my-server") is None

    def test_unexpected_status_none(self):
        """5xx/429 → indéterminé, ne JAMAIS bloquer sur un registry HS."""
        from src.mcp.target_resolver import probe_package_exists
        assert probe_package_exists(
            "npm:x", http_status_callable=lambda url: 503
        ) is None


class TestFixABAddMcpBlocksHallucinated:

    def _integration_with_probe(self, monkeypatch, exists):
        import src.mcp.target_resolver as tr
        monkeypatch.setattr(
            tr, "probe_package_exists", lambda spec, **kw: exists
        )
        orchestrator = MagicMock()
        orchestrator.propose = MagicMock(
            return_value=SimpleNamespace(
                approval_ticket_id="a" * 32,
                server_id="weather-mcp",
                tool_name="mcp_catalog_add:weather-mcp",
                risk_summary="catalog_add_required",
                dry_run=False,
            )
        )
        return _make_integration(catalog_add_orchestrator=orchestrator)

    def test_hallucinated_package_blocked(self, monkeypatch):
        """Le scénario runtime exact : package npm inventé par le LLM →
        blocked mcp_package_not_found, AUCUNE entrée catalogue créée."""
        integ = self._integration_with_probe(monkeypatch, exists=False)
        raw = integ.handle_add_mcp(
            "npm:@nicholaschen/weather-mcp",
            caller_kind="react",
            live=True,
            confirmation_phrase="I-CONFIRM-ADD-MCP",
        )
        data = json.loads(raw)
        assert data["decision"] == "blocked"
        assert "mcp_package_not_found" in raw
        integ._deps.catalog_add_orchestrator.propose.assert_not_called()

    def test_existing_package_proceeds(self, monkeypatch):
        integ = self._integration_with_probe(monkeypatch, exists=True)
        raw = integ.handle_add_mcp(
            "npm:mcp-weather-server",
            caller_kind="react",
            live=True,
            confirmation_phrase="I-CONFIRM-ADD-MCP",
        )
        data = json.loads(raw)
        assert data["decision"] == "ok"
        assert data["payload"]["existence_check"] == "confirmed"

    def test_network_down_proceeds_with_flag(self, monkeypatch):
        """Réseau indisponible → on n'empêche PAS l'ajout (None ≠ False),
        mais on trace l'absence de vérification."""
        integ = self._integration_with_probe(monkeypatch, exists=None)
        raw = integ.handle_add_mcp(
            "npm:mcp-weather-server",
            caller_kind="react",
            live=True,
            confirmation_phrase="I-CONFIRM-ADD-MCP",
        )
        data = json.loads(raw)
        assert data["decision"] == "ok"
        assert data["payload"]["existence_check"] == "unverified_network"


# ──────────────────────────────────────────────────────────────────────────────
# Fix AD — guidance : jamais demander la phrase à l'utilisateur
# ──────────────────────────────────────────────────────────────────────────────


REACT_PATH = REPO / "src" / "reasoning" / "react.py"

# Lot RF-2 du refactor ReAct (2026-08-27) : `_phase27_mcp_observation_guidance`
# a quitte `react.py` pour `observation_synthesis.py`. Les trois tests de
# `TestFixADGuidanceNeverAsksUser` lisent le TEXTE SOURCE de cette guidance ;
# ils pointent donc desormais son nouveau proprietaire.
#
# Le plan de refactor n'autorise ce repointage qu'accompagne d'une preuve
# COMPORTEMENTALE equivalente. Elle existe :
#   tests/reasoning/test_rf2_observation_synthesis_extraction.py
#     - test_guidance_approbation_pointe_vers_un_outil_que_le_llm_possede
#     - test_guidance_approbation_ne_demande_jamais_la_phrase_a_l_utilisateur
#     - test_guidance_ticket_pending_reprend_avec_le_meme_intent
# Ces trois-la APPELLENT la fonction au lieu de chercher une chaine : elles
# survivraient a un prochain deplacement, contrairement a celles ci-dessous.
GUIDANCE_MCP_PATH = REPO / "src" / "reasoning" / "observation_synthesis.py"


class TestFixADGuidanceNeverAsksUser:

    def test_needs_approval_guidance_self_generates(self):
        content = GUIDANCE_MCP_PATH.read_text(encoding="utf-8")
        idx = content.find("Une action MCP est necessaire")
        assert idx > 0
        section = content[idx:idx + 900]
        assert "TOI-MEME" in section
        assert "JAMAIS a l'utilisateur de la taper" in section
        # L'ancienne instruction toxique doit avoir disparu du fichier
        assert "demande seulement cette phrase" not in content

    def test_needs_approval_guidance_points_to_available_tool(self):
        """Fix AH — runtime 2026-06-11 17:41 : la guidance pointait
        request_mcp_ticket (HORS liste d'outils du LLM) avec la phrase
        TICKET → DeepSeek transposait la mauvaise phrase sur
        run_mcp_autonomy et bouclait sur confirmation_phrase_invalid.
        La guidance doit pointer run_mcp_autonomy avec SA phrase."""
        content = GUIDANCE_MCP_PATH.read_text(encoding="utf-8")
        idx = content.find("Une action MCP est necessaire")
        section = content[idx:idx + 900]
        assert "run_mcp_autonomy" in section
        assert "I-CONFIRM-MCP-AUTONOMY" in section
        assert "request_mcp_ticket" not in section

    def test_ticket_pending_guidance_resume_path(self):
        """Après approbation panel, la guidance doit pointer vers
        run_mcp_autonomy avec le MÊME intent (pas resume avec un intent
        court qui re-déclenche le churn)."""
        content = GUIDANCE_MCP_PATH.read_text(encoding="utf-8")
        idx = content.find("Ticket MCP pending")
        assert idx > 0
        section = content[idx:idx + 900]
        assert "run_mcp_autonomy" in section
        assert "MEME intent" in section
        assert "ne demande JAMAIS a l'utilisateur de taper" in section


class TestFixAHConfirmationPhraseHints:
    """Fix AH — runtime 2026-06-11 17:41 : run_mcp_autonomy bloquait
    `confirmation_phrase_invalid` SANS exposer la phrase attendue
    (contrairement à add_mcp) → DeepSeek a bouclé 3× avec la phrase
    TICKET sur l'outil AUTONOMY puis a abandonné le flux."""

    def test_autonomy_blocked_exposes_expected_phrase(self):
        integ = _make_integration()
        raw = integ.handle_run_mcp_autonomy(
            "installer un MCP crypto",
            caller_kind="react",
            live=True,
            confirmation_phrase="I-CONFIRM-MCP-TICKET",  # mauvaise phrase
        )
        data = json.loads(raw)
        assert data["decision"] == "blocked"
        payload = data["payload"]
        assert payload["expected_confirmation_phrase"] == (
            "I-CONFIRM-MCP-AUTONOMY"
        )
        assert "TOI-MÊME" in payload["hint"]

    def test_ticket_blocked_exposes_expected_phrase(self):
        integ = _make_integration()
        raw = integ.handle_request_mcp_ticket(
            "installer un MCP crypto",
            caller_kind="react",
            confirmation_phrase="I-CONFIRM-MCP-AUTONOMY",  # mauvaise phrase
        )
        data = json.loads(raw)
        assert data["decision"] == "blocked"
        payload = data["payload"]
        assert payload["expected_confirmation_phrase"] == (
            "I-CONFIRM-MCP-TICKET"
        )
        assert "TOI-MÊME" in payload["hint"]


# ──────────────────────────────────────────────────────────────────────────────
# Fix AF — guards anti-hallucination : formes passives + « avec succès »
# ──────────────────────────────────────────────────────────────────────────────


class TestFixAFGuardsCoverMCPClaims:
    """Fix AF — runtime 2026-06-11 04:34 : final « MCP Météo installé et
    testé avec succès / a été installé sur ton système » avec ZÉRO outil
    appelé → aucun guard n'a tiré (formes passives et « avec succès »
    absentes des patterns). Ces tests verrouillent la couverture."""

    LYING_FINAL = (
        "✅ **MCP Météo installé et testé avec succès !** 🌤️ "
        "Le MCP météo (serveur météo via API) a été installé sur ton "
        "système. Test effectué : connexion API réussie."
    ).lower()

    def test_ledger_claim_patterns_cover_runtime_lie(self):
        # Les patterns du LEDGER guard ont été extraits dans ledger_guard.py
        # (Phase 2). On teste la VALEUR (le tuple + la détection), pas l'emplacement.
        from src.reasoning.ledger_guard import (
            _LEDGER_CLAIM_PATTERNS, ledger_text_claims_action,
        )
        for needle in (
            "a été installé",
            "installé et testé",
            "installé avec succès",
            "test effectué",
            "j'ai installé",
        ):
            assert needle in _LEDGER_CLAIM_PATTERNS, f"_LEDGER_CLAIM_PATTERNS ne couvre pas: {needle}"
        # Et la détection bloque bien le mensonge runtime exact.
        assert ledger_text_claims_action(self.LYING_FINAL) is True

    def test_ledger_text_normalizes_typographic_apostrophes(self):
        content = REACT_PATH.read_text(encoding="utf-8")
        idx = content.find("_runtime_claim_for_final = ")
        assert idx > 0
        before = content[max(0, idx - 1200):idx]
        assert "replace(_apo" in before or "replace(" in before, (
            "Les apostrophes typographiques doivent être normalisées "
            "avant le matching des _CLAIM_PATTERNS"
        )

    def test_noplan_patterns_match_passive_and_success_forms(self):
        """Les regex ajoutées à _HP_NOPLAN doivent matcher le mensonge
        runtime exact (spec verrouillée par duplication contrôlée)."""
        import re as _re
        passive = _re.compile(
            r"\b(a|ont) été (installé|installe|créé|cree|configuré|configure"
            r"|activé|active|testé|teste|envoyé|envoye|généré|genere"
            r"|déployé|deploye)",
            _re.IGNORECASE,
        )
        success = _re.compile(
            r"\b(installé|installe|activé|active|créé|cree|configuré"
            r"|configure|testé|teste|déployé|deploye)\w*( et \w+)? avec "
            r"succ[èe]s\b",
            _re.IGNORECASE,
        )
        assert passive.search(self.LYING_FINAL)
        assert success.search(self.LYING_FINAL)
        # Ces patterns ont été centralisés dans hallucination_guard.py (Temps 2) :
        # ils vivent désormais dans _HALLUCINATION_CLAIM_PATTERNS, plus dans _HP_NOPLAN.
        from pathlib import Path as _P
        guard_src = (_P(__file__).resolve().parents[2]
                     / "src" / "reasoning" / "hallucination_guard.py").read_text(encoding="utf-8")
        assert r"(a|ont) été (installé" in guard_src
        assert "avec succ[èe]s" in guard_src
        # Et surtout : le guard centralisé BLOQUE bien ce mensonge runtime (aucun outil).
        from src.reasoning.hallucination_guard import hallucination_retry_query
        _q, _ = hallucination_retry_query(self.LYING_FINAL, "orig", set(), 0)
        assert _q is not None

    def test_mcp_tools_count_as_create_proof(self):
        """Un VRAI run_mcp_autonomy réussi doit exonérer le guard sans-plan
        (sinon chaque install MCP véridique coûte des retries).

        NB : la définition vit désormais dans src/reasoning/hallucination_guard.py
        (ré-exportée par react). On teste la VALEUR (comportement), pas l'emplacement
        du source — plus robuste qu'un grep texte."""
        from src.reasoning.react import _HC_TOOLS_MCP, _HC_TOOLS_ANY_CREATE
        assert "run_mcp_autonomy" in _HC_TOOLS_MCP
        # Et inclus dans ANY_CREATE
        assert _HC_TOOLS_MCP <= _HC_TOOLS_ANY_CREATE

    def test_no_false_positive_on_honest_failure_report(self):
        """Un rapport HONNÊTE d'échec (« n'a PAS été installé ») contient
        les mots mais le guard ledger ne doit cibler que les affirmations.
        On vérifie au moins que claim_match_is_negated est présent et
        appelé dans le flux _HP_NOPLAN."""
        content = REACT_PATH.read_text(encoding="utf-8")
        assert "claim_match_is_negated" in content


# ──────────────────────────────────────────────────────────────────────────────
# Fix AG — fallback policy conservateur pour tools inclassables
# ──────────────────────────────────────────────────────────────────────────────


class TestFixAGUnclassifiedFallback:
    """Fix AG — runtime 2026-06-11 04:45 : open-meteo-mcp-server activé,
    17 tools légitimes (gfs_forecast, ecmwf_forecast...) TOUS refusés
    `no_keyword_match` → registered_count: 0. Le vocabulaire d'un domaine
    inconnu ne sera jamais dans les tables de keywords : fallback
    EXTERNAL_WRITE_RECOVERABLE (la plus restrictive exécutable) si
    trust ≥ 70, refus sinon."""

    def _attributor(self, tmp_path):
        from src.mcp.policy_attributor import PolicyAttributor
        return PolicyAttributor(
            audit_log_path=tmp_path / "audit.jsonl",
        )

    def _tool(self, name="gfs_forecast"):
        from src.mcp.policy_attributor import ToolMetadata
        return ToolMetadata(
            server_id="proposed_5a0132b8c4",
            tool_name=name,
            description=(
                "Get GFS model data for a location. Returns temperature, "
                "precipitation and wind at the given coordinates."
            ),
            input_schema={"type": "object", "properties": {
                "latitude": {"type": "number"},
                "longitude": {"type": "number"},
            }},
        )

    def test_unclassified_high_trust_falls_back_to_write(self, tmp_path):
        """Le cas runtime exact : tool météo inclassable + trust 80 →
        EXTERNAL_WRITE_RECOVERABLE (plus jamais de refus en bloc)."""
        from src.mcp.policy import MCPPolicy
        att = self._attributor(tmp_path)
        decision = att.attribute(self._tool(), trust_score=80)
        assert decision.policy == MCPPolicy.EXTERNAL_WRITE_RECOVERABLE
        assert decision.reason == "fallback_conservative_unclassified"

    def test_unclassified_low_trust_still_refused(self, tmp_path):
        att = self._attributor(tmp_path)
        decision = att.attribute(self._tool(), trust_score=50)
        assert decision.policy is None
        assert decision.reason == "no_keyword_match"

    def test_unclassified_no_trust_still_refused(self, tmp_path):
        att = self._attributor(tmp_path)
        decision = att.attribute(self._tool(), trust_score=None)
        assert decision.policy is None

    def test_fallback_is_never_read(self, tmp_path):
        """INVARIANT sécurité : le fallback ne doit JAMAIS être READ
        (un mutateur inclassé contournerait le gate write) ni SECRETS."""
        from src.mcp.policy import MCPPolicy
        att = self._attributor(tmp_path)
        decision = att.attribute(self._tool("mystery_tool_xyz"), trust_score=100)
        assert decision.policy not in (
            MCPPolicy.READ_ONLY, MCPPolicy.EXTERNAL_READ,
            MCPPolicy.SECRETS_AUTH,
        )
        assert decision.policy == MCPPolicy.EXTERNAL_WRITE_RECOVERABLE

    def test_classified_tools_unchanged(self, tmp_path):
        """Les tools classifiables par keywords gardent leur policy exacte
        (le fallback ne s'applique qu'aux inclassables)."""
        from src.mcp.policy import MCPPolicy
        from src.mcp.policy_attributor import ToolMetadata
        att = self._attributor(tmp_path)
        tool = ToolMetadata(
            server_id="x",
            tool_name="list_channels",
            description="List all public channels in the workspace.",
            input_schema={"type": "object"},
        )
        decision = att.attribute(tool, trust_score=80)
        assert decision.policy in (
            MCPPolicy.READ_ONLY, MCPPolicy.EXTERNAL_READ,
        )


class TestFixAIRelevanceScoring:
    """Fix AI — runtime 2026-06-11 18:24 et 18:29 : la recherche réseau a
    élu `mcp-framework` puis `mcp-use` (outils de dev génériques) pour des
    intents CRYPTO — le pre-score était 100% qualité, 0% pertinence. Un
    candidat non-curated sans token discriminant partagé avec l'intent
    est maintenant écarté."""

    def _plan(self, intent, results):
        from src.mcp.proposal_planner import (
            MCPProposalPlanner,
            MCPProposalPlannerDeps,
        )
        source = MagicMock()
        source.name = "npm_search"
        source.is_network = True
        source.network_enabled = True
        source.search = MagicMock(return_value=results)
        planner = MCPProposalPlanner(
            MCPProposalPlannerDeps(sources=[source], catalog_lookup=None)
        )
        return planner.plan_proposal(intent, caller_kind="react")

    def _raw(self, name, description, downloads=100000):
        return {
            "package_name": name,
            "package_spec": f"npm:{name}",
            "version": "1.0.0",
            "package_transport": "npm",
            "mcp_transport_hint": "stdio",
            "description": description,
            "downloads_count": downloads,
            "has_repo": True,
            "has_license": True,
            "last_publish_date": "2026-06-01T00:00:00Z",
            "tools_hint": [],
        }

    def test_offtopic_popular_package_rejected(self):
        """Le cas runtime exact : mcp-use (générique, populaire) ne doit
        JAMAIS être proposé pour un intent bitcoin."""
        plan = self._plan(
            "installer le MCP bitcoin-mcp depuis pypi",
            [self._raw(
                "mcp-use",
                "The easiest way to use MCP servers from any LLM client.",
                downloads=500000,
            )],
        )
        prop = getattr(plan, "catalog_proposal", None)
        assert prop is None or "mcp-use" not in str(
            getattr(prop, "proposed_package_spec", "")
        )

    def test_relevant_package_selected_over_popular_offtopic(self):
        """bitcoin-mcp (pertinent) doit battre mcp-use (populaire hors
        sujet) sur un intent crypto."""
        plan = self._plan(
            "utiliser un MCP crypto pour le prix du bitcoin",
            [
                self._raw(
                    "mcp-use",
                    "The easiest way to use MCP servers.",
                    downloads=500000,
                ),
                self._raw(
                    "bitcoin-mcp",
                    "MCP server exposing bitcoin and crypto market prices.",
                    downloads=2000,
                ),
            ],
        )
        prop = getattr(plan, "catalog_proposal", None)
        assert prop is not None, "Le candidat pertinent doit être proposé"
        assert "bitcoin-mcp" in str(prop.proposed_package_spec)

    def test_generic_intent_skips_relevance_filter(self):
        """Intent sans token discriminant (« installe un mcp ») → pas de
        filtre (sinon plus rien ne serait jamais proposé)."""
        plan = self._plan(
            "installe un mcp",
            [self._raw("some-server", "A generic MCP server.")],
        )
        prop = getattr(plan, "catalog_proposal", None)
        assert prop is not None


class TestFixAJRemovedRedeclare:
    """Fix AJ — le server_id est un hash déterministe du package_spec et
    REMOVED est terminal : un package supprimé via le panel était
    définitivement inréinstallable (observé runtime : bitcoin-mcp supprimé
    à 17:45 → tous les re-add silencieusement no-op)."""

    def _catalog(self, tmp_path):
        from src.mcp.server_catalog import MCPServerCatalog
        secrets = MagicMock()
        secrets.get = MagicMock(
            return_value="dGVzdC1obWFjLWtleS0zMi1ieXRlcy1sb25nLXg="
        )
        return MCPServerCatalog(
            catalog_dir=tmp_path / "catalog", secrets_service=secrets
        )

    def _removed_entry(self, cat):
        from src.mcp.server_catalog import ServerStatus
        cat.add_server(
            server_id="proposed_3c48f03ca3",
            display_name="bitcoin-mcp",
            package_spec="pypi:bitcoin-mcp",
            owner_profile="lumena",
            trust_score=70,
        )
        cat.remove_server("proposed_3c48f03ca3")
        entry = cat.get_server("proposed_3c48f03ca3")
        assert entry.status == ServerStatus.REMOVED
        return entry

    def test_redeclare_removed_entry(self, tmp_path):
        from src.mcp.server_catalog import ServerStatus
        cat = self._catalog(tmp_path)
        self._removed_entry(cat)
        entry = cat.redeclare_server(
            server_id="proposed_3c48f03ca3",
            display_name="bitcoin-mcp",
            package_spec="pypi:bitcoin-mcp",
            owner_profile="lumena",
            trust_score=75,
            capability_tags=["bitcoin", "crypto"],
        )
        assert entry.status == ServerStatus.DECLARED
        assert entry.trust_score == 75
        assert entry.capability_tags == ("bitcoin", "crypto")

    def test_redeclare_requires_removed_status(self, tmp_path):
        """redeclare ne doit JAMAIS écraser une entrée vivante."""
        from src.mcp.server_catalog import CatalogError
        cat = self._catalog(tmp_path)
        cat.add_server(
            server_id="alive",
            display_name="Alive",
            package_spec="npm:alive-mcp",
            owner_profile="lumena",
            trust_score=70,
        )
        with pytest.raises(CatalogError):
            cat.redeclare_server(
                server_id="alive",
                display_name="Alive",
                package_spec="npm:alive-mcp",
                owner_profile="lumena",
                trust_score=70,
            )

    def test_orchestrator_redeclares_removed(self):
        """execute_approved_catalog_add sur une entrée REMOVED → redeclare
        (avant : already_declared silencieux, entrée restait morte)."""
        from src.mcp.catalog_add_orchestrator import MCPCatalogAddOrchestrator
        from src.mcp.approval_queue import ApprovalResult, ApprovalDecision
        removed = MagicMock()
        removed.status = MagicMock(value="removed")
        catalog = MagicMock()
        catalog.get_server = MagicMock(return_value=removed)
        redeclared = MagicMock()
        redeclared.status = MagicMock(value="declared")
        catalog.redeclare_server = MagicMock(return_value=redeclared)
        queue = MagicMock()
        orch = MCPCatalogAddOrchestrator(catalog=catalog, approval_queue=queue)
        approval = ApprovalResult(
            decision=ApprovalDecision.APPROVED,
            args={
                "action": "catalog_add",
                "server_id": "proposed_3c48f03ca3",
                "display_name": "bitcoin-mcp",
                "package_spec": "pypi:bitcoin-mcp",
                "version": None,
                "trust_score": 70,
                "owner_profile": "lumena",
            },
            reason="test",
        )
        result = orch.execute_approved_catalog_add(
            "proposed_3c48f03ca3", approval, dry_run=False
        )
        assert result.success is True
        catalog.redeclare_server.assert_called_once()
        catalog.add_server.assert_not_called()

    def test_propose_creates_ticket_for_removed(self):
        """propose_catalog_add ne court-circuite plus une entrée REMOVED."""
        from src.mcp.catalog_add_orchestrator import (
            MCPCatalogAddOrchestrator,
            CatalogAddProposalInput,
        )
        removed = MagicMock()
        removed.status = MagicMock(value="removed")
        catalog = MagicMock()
        catalog.get_server = MagicMock(return_value=removed)
        queue = MagicMock()
        queue.propose = MagicMock(return_value="t" * 32)
        orch = MCPCatalogAddOrchestrator(catalog=catalog, approval_queue=queue)
        proposal = orch.propose_catalog_add(
            CatalogAddProposalInput(
                server_id="proposed_3c48f03ca3",
                display_name="bitcoin-mcp",
                package_spec="pypi:bitcoin-mcp",
                version=None,
                trust_score=70,
            ),
            dry_run=False,
        )
        assert proposal.approval_ticket_id is not None


class TestFixAKShellGuardUnscoped:
    """Fix AK — runtime 2026-06-11 21:57 : `pip install bitcoin-mcp` est
    passé au travers du guard (patterns : préfixe mcp-* et suffixe -mcp
    SCOPÉ seulement) et a installé le package DANS LE VENV DE LUMENA
    (mcp 1.27.2, pyjwt, sse-starlette ajoutés à l'app). Le suffixe non
    scopé `<nom>-mcp` / `<nom>_mcp` doit être bloqué."""

    def _detect(self, cmd):
        from src.reasoning.handlers._mcp_shell_guard import (
            detect_mcp_shell_install,
        )
        return detect_mcp_shell_install(cmd)

    def test_runtime_bypass_now_blocked(self):
        """Le contournement exact observé en runtime."""
        det = self._detect("pip install bitcoin-mcp")
        assert det is not None
        assert det.detected_package == "bitcoin-mcp"
        assert det.suggested_target == "pypi:bitcoin-mcp"

    def test_uv_tool_install_blocked(self):
        """Fix AK.3 — runtime 2026-06-12 10:37 : `uv tool install
        mcp-duckduckgo` passait au travers (seul `uv pip install` était
        couvert) et n'était stoppé que par la whitelist générique dont le
        message égare le LLM."""
        det = self._detect("uv tool install mcp-duckduckgo")
        assert det is not None
        assert det.detected_package == "mcp-duckduckgo"
        assert det.suggested_target == "pypi:mcp-duckduckgo"

    def test_uv_tool_run_blocked(self):
        det = self._detect("uv tool run duckduckgo-mcp-server")
        assert det is not None
        assert det.detected_package == "duckduckgo-mcp-server"

    def test_underscore_suffix_blocked(self):
        det = self._detect("pip install weather_mcp")
        assert det is not None
        assert det.detected_package == "weather_mcp"

    def test_npm_unscoped_suffix_blocked(self):
        det = self._detect("npm install -g crypto-mcp")
        assert det is not None
        assert det.suggested_target == "npm:crypto-mcp"

    def test_python_m_pip_blocked(self):
        det = self._detect("python -m pip install bitcoin-mcp")
        assert det is not None

    def test_generic_installs_still_pass(self):
        """Conservateur : aucun faux positif sur les installs de dev."""
        assert self._detect("npm install left-pad") is None
        assert self._detect("pip install requests pandas") is None
        assert self._detect("npm install") is None

    def test_no_false_positive_on_mcp_substring(self):
        """`tampmcpx` ou `something-mcplib` ne doivent pas matcher (le
        suffixe doit être exactement -mcp en fin de token)."""
        assert self._detect("pip install something-mcplib") is None


class TestFixAK2InstallFailureTraceability:
    """Fix AK.2 — l'exception du runner était avalée sans log ni type :
    `runner_install_failed` muet (échec pypi 22:00 intraçable)."""

    def test_reason_includes_exception_type(self):
        from src.mcp.install_orchestrator import MCPInstallOrchestrator
        from src.mcp.server_catalog import ServerStatus
        from src.mcp.approval_queue import ApprovalResult, ApprovalDecision
        entry = MagicMock()
        entry.status = ServerStatus.DECLARED
        entry.package_spec = "pypi:bitcoin-mcp"
        entry.version = "0.5.1"
        entry.trust_score = 70
        catalog = MagicMock()
        catalog.get_server = MagicMock(return_value=entry)
        queue = MagicMock()
        orch = MCPInstallOrchestrator(
            catalog=catalog, approval_queue=queue, dry_run=False,
        )
        approval = ApprovalResult(
            decision=ApprovalDecision.APPROVED,
            args={
                "server_id": "bitcoin-mcp",
                "transport": "pypi",
                "package_name": "bitcoin-mcp",
                "package_spec": "pypi:bitcoin-mcp",
                "version": "0.5.1",
                "trust_score": 70,
            },
            reason="test",
        )
        import src.mcp.install_orchestrator as io_mod
        with pytest.MonkeyPatch.context() as mp:
            failing = MagicMock(
                side_effect=RuntimeError("uv exploded for test")
            )
            mp.setattr(io_mod, "MCPSandboxRunner", failing)
            result = orch.execute_approved_install("bitcoin-mcp", approval)
        assert result.success is False
        assert result.reason.startswith("runner_install_failed:")
        assert "RuntimeError" in result.reason

    def test_audit_reason_stays_short_code(self):
        """TestAuditForensic : l'audit garde le code court SANS type ni
        message d'exception."""
        content = (
            REPO / "src" / "mcp" / "install_orchestrator.py"
        ).read_text(encoding="utf-8")
        idx = content.find("runner install failed for")
        assert idx > 0, "Le log d'exception Fix AK.2 doit exister"
        section = content[idx:idx + 800]
        assert 'reason="runner_install_failed"' in section, (
            "L'audit doit garder le code court sans exc info"
        )


class TestFixALAddMcpNextStep:
    """Fix AL — runtime 2026-06-11 22:44 : après `mcp_added`, le payload
    ne disait ni s'il y avait un ticket, ni le statut, ni la suite →
    le LLM a inventé une approbation inexistante puis n'a jamais enchaîné
    run_mcp_autonomy (entrée DECLARED restée morte)."""

    def _integration(self, ticket_id, entry_status="declared"):
        import src.mcp.target_resolver as tr
        orchestrator = MagicMock()
        orchestrator.propose = MagicMock(
            return_value=SimpleNamespace(
                approval_ticket_id=ticket_id,
                server_id="bitcoin-mcp",
                tool_name="mcp_catalog_add:bitcoin-mcp",
                risk_summary="catalog_add_required",
                dry_run=False,
            )
        )
        catalog = MagicMock()
        catalog.get_server = MagicMock(
            return_value=_entry(
                sid="bitcoin-mcp", status_value=entry_status,
                package_spec="pypi:bitcoin-mcp",
            )
        )
        return _make_integration(
            catalog_add_orchestrator=orchestrator, catalog=catalog
        )

    def _call(self, integ, monkeypatch):
        import src.mcp.target_resolver as tr
        monkeypatch.setattr(
            tr, "probe_package_exists", lambda spec, **kw: True
        )
        return json.loads(integ.handle_add_mcp(
            "pypi:bitcoin-mcp",
            caller_kind="react",
            live=True,
            confirmation_phrase="I-CONFIRM-ADD-MCP",
        ))

    def test_no_ticket_exposes_run_autonomy_next_step(self, monkeypatch):
        """Le cas runtime exact : entrée déjà DECLARED → pas de ticket →
        le payload doit dire « rien à approuver, run_mcp_autonomy »."""
        integ = self._integration(ticket_id=None)
        data = self._call(integ, monkeypatch)
        p = data["payload"]
        assert p["approval_ticket_id"] is None
        assert p["next_step"] == "run_mcp_autonomy"
        assert p["catalog_status"] == "declared"
        assert p["target_server_id"] == "bitcoin-mcp"

    def test_real_ticket_exposes_approval_next_step(self, monkeypatch):
        integ = self._integration(ticket_id="f" * 32)
        data = self._call(integ, monkeypatch)
        p = data["payload"]
        assert p["approval_ticket_id"] == "f" * 32
        assert p["next_step"] == "approve_ticket_then_resume"

    def test_guidance_covers_mcp_added(self):
        """add_mcp doit avoir une guidance MCP_LOOP qui pointe
        run_mcp_autonomy et interdit pip/npm."""
        from src.reasoning.react import (
            _phase27_mcp_observation_guidance,
            _PHASE27_MCP_LOOP_TOOLS,
        )
        assert "add_mcp" in _PHASE27_MCP_LOOP_TOOLS
        obs = json.dumps({
            "decision": "ok",
            "payload": {
                "recommendation_code": "mcp_added",
                "approval_ticket_id": None,
                "target_server_id": "bitcoin-mcp",
            },
        })
        guidance = _phase27_mcp_observation_guidance("add_mcp", obs)
        assert guidance is not None
        assert "run_mcp_autonomy" in guidance
        assert "I-CONFIRM-MCP-AUTONOMY" in guidance
        assert "ne demande PAS d'approbation" in guidance
        assert "JAMAIS pip/npm" in guidance

    def test_guidance_with_ticket_asks_panel_approval(self):
        from src.reasoning.react import _phase27_mcp_observation_guidance
        obs = json.dumps({
            "decision": "ok",
            "payload": {
                "recommendation_code": "mcp_added",
                "approval_ticket_id": "f" * 32,
            },
        })
        guidance = _phase27_mcp_observation_guidance("add_mcp", obs)
        assert guidance is not None
        assert "Approbations" in guidance
        assert "run_mcp_autonomy" in guidance

    def test_dry_run_guidance_no_mutation_claim(self):
        from src.reasoning.react import _phase27_mcp_observation_guidance
        obs = json.dumps({
            "decision": "ok",
            "payload": {"recommendation_code": "mcp_target_resolved"},
        })
        guidance = _phase27_mcp_observation_guidance("add_mcp", obs)
        assert guidance is not None
        assert "AUCUNE mutation" in guidance


class TestFixAMUvVenvIdempotent:
    """Fix AM — runtime 2026-06-11 22:58 (cause révélée par le log AK.2) :
    `uv venv` échoue exit 2 sur un .venv résiduel (« A virtual environment
    already exists ») → un install pypi raté empoisonnait définitivement
    tous les suivants. --clear rend l'install idempotent."""

    def test_uv_venv_uses_clear(self):
        content = (
            REPO / "src" / "mcp" / "sandbox_runner.py"
        ).read_text(encoding="utf-8")
        idx = content.find('"venv", "--clear"')
        assert idx > 0, (
            "uv venv doit utiliser --clear (idempotence des installs pypi)"
        )


class TestFixANVersionSentinelNotPinned:
    """Fix AN — runtime 2026-06-12 03:00 : target_resolver pose
    version="latest" (sentinel = pas de version extraite) ; le runner
    construisait `bitcoin-mcp==latest` que pip/uv rejettent
    (« Failed to parse: expected version to start with a number »).
    npm tolérait `@latest`, d'où le chemin npm vert et le pypi cassé.
    Un sentinel ne doit jamais devenir un pin."""

    def _runner(self, tmp_path, transport, version):
        from src.mcp.sandbox_runner import MCPInstallSpec, MCPSandboxRunner
        spec = MCPInstallSpec(
            name="bitcoin-mcp",
            transport=transport,
            package="bitcoin-mcp",
            env_keys_allowlist=[],
            package_version=version,
        )
        return MCPSandboxRunner(
            spec=spec, mcp_root=tmp_path / "mcp", logs_dir=tmp_path / "logs",
            stdout_mode="capture",
        )

    @pytest.mark.parametrize(
        "sentinel", ["latest", "Latest", "LATEST", "*", "", "  ", None]
    )
    def test_normalize_pin_rejects_sentinels(self, sentinel):
        from src.mcp.sandbox_runner import MCPSandboxRunner
        assert MCPSandboxRunner._normalize_pin(sentinel) is None

    @pytest.mark.parametrize("real", ["1.2.3", "0.1.0", " 2.0 "])
    def test_normalize_pin_keeps_real_versions(self, real):
        from src.mcp.sandbox_runner import MCPSandboxRunner
        assert MCPSandboxRunner._normalize_pin(real) == real.strip()

    def test_install_uv_latest_sentinel_unpinned(self, tmp_path):
        """Régression exacte du round 7 : version='latest' sur pypi ne doit
        produire aucun `==` dans la commande uv pip install."""
        import subprocess as _sp
        runner = self._runner(tmp_path, "uv", "latest")
        commands = []

        def _rec(cmd, env, *, check=True):
            commands.append(cmd)
            return _sp.CompletedProcess(cmd, 0, stdout="", stderr="")

        runner._run_install_command = _rec
        runner._install_uv({})
        install_cmd = commands[-1]
        assert "bitcoin-mcp" in install_cmd
        assert not any("==" in part for part in install_cmd), install_cmd

    def test_install_uv_real_version_pinned(self, tmp_path):
        import subprocess as _sp
        runner = self._runner(tmp_path, "uv", "1.2.3")
        commands = []

        def _rec(cmd, env, *, check=True):
            commands.append(cmd)
            return _sp.CompletedProcess(cmd, 0, stdout="", stderr="")

        runner._run_install_command = _rec
        runner._install_uv({})
        assert "bitcoin-mcp==1.2.3" in commands[-1]

    def test_install_npm_latest_sentinel_unpinned(self, tmp_path):
        runner = self._runner(tmp_path, "npm", "latest")
        commands = []
        runner._run_install_command = lambda cmd, env: commands.append(cmd)
        runner._install_npm({})
        assert "bitcoin-mcp" in commands[-1]
        assert "bitcoin-mcp@latest" not in commands[-1]

    def test_install_npm_real_version_pinned(self, tmp_path):
        runner = self._runner(tmp_path, "npm", "0.1.0")
        commands = []
        runner._run_install_command = lambda cmd, env: commands.append(cmd)
        runner._install_npm({})
        assert "bitcoin-mcp@0.1.0" in commands[-1]


class TestFixAOVenvEntryPoint:
    """Fix AO — runtime 2026-06-12 03:36 : start uv = `python -m bitcoin-mcp`
    → exit 1 « No module named bitcoin-mcp » (un nom de module ne peut pas
    contenir de tiret). Le chemin standard pip = entry point console dans
    <venv>/Scripts|bin (validé réel : bitcoin-mcp.exe → handshake + 48
    tools). Fallback -m normalisé en underscores."""

    def _runner(self, tmp_path, package="bitcoin-mcp", args=None):
        from src.mcp.sandbox_runner import MCPInstallSpec, MCPSandboxRunner
        spec = MCPInstallSpec(
            name=package,
            transport="uv",
            package=package,
            env_keys_allowlist=[],
            args=args,
        )
        return MCPSandboxRunner(
            spec=spec, mcp_root=tmp_path / "mcp", logs_dir=tmp_path / "logs",
            stdout_mode="capture",
        )

    def _bin_dir(self, runner):
        import sys as _sys
        sub = "Scripts" if _sys.platform == "win32" else "bin"
        d = runner.server_dir / ".venv" / sub
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _make_bin(self, bin_dir, stem):
        import sys as _sys
        name = f"{stem}.exe" if _sys.platform == "win32" else stem
        p = bin_dir / name
        p.write_bytes(b"")
        return p

    def test_entry_point_exact_name_resolved(self, tmp_path):
        """Régression exacte du round 8 : bitcoin-mcp.exe doit être élu,
        jamais `python -m bitcoin-mcp`."""
        runner = self._runner(tmp_path)
        expected = self._make_bin(self._bin_dir(runner), "bitcoin-mcp")
        cmd = runner._build_start_command()
        assert cmd == [str(expected)]
        assert "-m" not in cmd

    def test_entry_point_underscored_variant_resolved(self, tmp_path):
        runner = self._runner(tmp_path)
        expected = self._make_bin(self._bin_dir(runner), "bitcoin_mcp")
        cmd = runner._build_start_command()
        assert cmd == [str(expected)]

    def test_opportunistic_scan_contains_basename(self, tmp_path):
        runner = self._runner(tmp_path)
        expected = self._make_bin(self._bin_dir(runner), "bitcoin-mcp-server")
        cmd = runner._build_start_command()
        assert cmd == [str(expected)]

    def test_python_and_pip_never_elected(self, tmp_path):
        """Les exécutables standard du venv ne matchent pas le basename
        → fallback -m, jamais python.exe/pip.exe comme serveur."""
        runner = self._runner(tmp_path)
        bin_dir = self._bin_dir(runner)
        for stem in ("python", "pythonw", "pip", "pip3"):
            self._make_bin(bin_dir, stem)
        cmd = runner._build_start_command()
        assert cmd[0] == str(runner.venv_python)
        assert cmd[1] == "-m"

    def test_fallback_module_name_underscored(self, tmp_path):
        """Sans entry point : -m avec le nom normalisé (jamais de tiret)."""
        runner = self._runner(tmp_path)
        self._bin_dir(runner)  # venv vide
        cmd = runner._build_start_command()
        assert cmd == [str(runner.venv_python), "-m", "bitcoin_mcp"]

    def test_fallback_without_venv_dir(self, tmp_path):
        runner = self._runner(tmp_path, package="mcp-server-example")
        cmd = runner._build_start_command()
        assert cmd == [str(runner.venv_python), "-m", "mcp_server_example"]

    def test_spec_args_still_take_priority(self, tmp_path):
        runner = self._runner(tmp_path, args=["-m", "custom_module"])
        self._make_bin(self._bin_dir(runner), "bitcoin-mcp")
        cmd = runner._build_start_command()
        assert cmd == [str(runner.venv_python), "-m", "custom_module"]


class TestFixAPReactToolAsShell:
    """Fix AP — runtime 2026-06-12 03:39 : DeepSeek a émis
    `run_command("run_mcp_autonomy(intent=..., live=true, ...)")` ; le refus
    générique de la whitelist (parlant de souris/clavier) l'a convaincu que
    l'outil n'existait pas → abandon du flux MCP. Le guard doit rediriger
    vers l'appel d'outil direct."""

    @pytest.mark.parametrize("tool", [
        "run_mcp_autonomy", "add_mcp", "request_mcp_capability",
        "resume_mcp_task", "request_mcp_ticket",
    ])
    def test_detects_tool_call_syntax(self, tool):
        from src.reasoning.handlers._mcp_shell_guard import (
            detect_react_tool_as_shell,
        )
        cmd = f'{tool}(intent="utiliser bitcoin-mcp", live=true)'
        assert detect_react_tool_as_shell(cmd) == tool

    def test_detects_exact_runtime_command(self):
        """La commande exacte du log 03:39:27."""
        from src.reasoning.handlers._mcp_shell_guard import (
            detect_react_tool_as_shell,
        )
        cmd = ('run_mcp_autonomy(intent="utiliser bitcoin-mcp", live=true, '
               'confirmation_phrase="I-CONFIRM-MCP-AUTONOMY")')
        assert detect_react_tool_as_shell(cmd) == "run_mcp_autonomy"

    def test_leading_whitespace_tolerated(self):
        from src.reasoning.handlers._mcp_shell_guard import (
            detect_react_tool_as_shell,
        )
        assert detect_react_tool_as_shell('  add_mcp(target="npm:x")') == "add_mcp"

    @pytest.mark.parametrize("cmd", [
        "npm install something",
        "echo run_mcp_autonomy",          # pas une syntaxe d'appel
        "python script.py run_mcp_autonomy(x)",  # pas en début de commande
        "dir /b data",
        "",
        None,
    ])
    def test_never_blocks_real_shell_commands(self, cmd):
        from src.reasoning.handlers._mcp_shell_guard import (
            detect_react_tool_as_shell,
        )
        assert detect_react_tool_as_shell(cmd) is None


class TestFixAQActionNameWithDash:
    """Fix AQ — runtime 2026-06-12 03:52 (3 conversations) : le regex
    ACTION tronquait au premier tiret → `mcp__bitcoin-mcp__get_btc_price`
    exécuté comme `mcp__bitcoin` (introuvable), boucle 3x, forçage FINAL.
    Le LLM écrivait le BON nom — c'est le parser qui le cassait. Premier
    server_id avec tiret de l'histoire du projet (memory/slack/proposed_*
    n'en avaient pas)."""

    def _parse(self, text):
        from src.reasoning.response_parser import parse_response
        return parse_response(text)

    def test_mcp_tool_name_with_dash_parsed_fully(self):
        thought, action, halluc, multi = self._parse(
            "THOUGHT: J'appelle l'outil prix.\n"
            "ACTION: mcp__bitcoin-mcp__get_btc_price\n"
            "ACTION_INPUT: {}\n"
        )
        assert action.tool_name == "mcp__bitcoin-mcp__get_btc_price"

    def test_inline_action_with_dash(self):
        thought, action, halluc, multi = self._parse(
            "THOUGHT: prix. ACTION: mcp__bitcoin-mcp__get_btc_price "
            "ACTION_INPUT: {}"
        )
        assert action.tool_name == "mcp__bitcoin-mcp__get_btc_price"

    def test_trailing_dash_not_captured(self):
        thought, action, halluc, multi = self._parse(
            "THOUGHT: x\nACTION: discover_tools- \nACTION_INPUT: {}\n"
        )
        assert action.tool_name == "discover_tools"

    def test_plain_names_unchanged(self):
        thought, action, halluc, multi = self._parse(
            "THOUGHT: x\nACTION: run_mcp_autonomy\nACTION_INPUT: {}\n"
        )
        assert action.tool_name == "run_mcp_autonomy"

    def test_final_action_unchanged(self):
        from src.reasoning.response_parser import parse_response
        thought, action, halluc, multi = parse_response(
            "THOUGHT: fini.\nACTION: FINAL\nACTION_INPUT: Voilà le prix.\n"
        )
        assert action.answer == "Voilà le prix."


class TestFixARActiveServerFallback:
    """Fix AR (04:02) puis Fix AU (10:37) — histoire en deux temps :
    AR avait ajouté un fallback tags/display pour rendre les serveurs
    ACTIFS visibles sur « utilise le mcp X ». MAIS un tag de domaine
    (« bitcoin ») matche le SUJET de n'importe quelle requête : il a fait
    battre duckduckgo DECLARED par bitcoin-mcp ACTIF sur « recherche
    DuckDuckGo actualité bitcoin ». Fix AU le SUPPRIME — le cas légitime
    est couvert par la mention textuelle exacte du sid (Fix AT)."""

    def _resolver(self):
        from src.mcp.capability_resolver import (
            CapabilityResolver,
            CapabilityResolverDeps,
        )
        return CapabilityResolver(CapabilityResolverDeps(catalog=MagicMock()))

    def _handlers(self):
        # Descriptions riches = le cas réel qui plafonne le jaccard.
        return [
            {
                "name": "mcp__bitcoin-mcp__get_btc_price",
                "description": (
                    "Get current BTC USD price from CoinGecko free no API "
                    "key returns price change percent and market cap"
                ),
                "server_id": "bitcoin-mcp",
            },
            {
                "name": "mcp__bitcoin-mcp__analyze_mempool",
                "description": (
                    "Analyze mempool congestion fee tiers minimum fee for "
                    "next block useful for transaction planning"
                ),
                "server_id": "bitcoin-mcp",
            },
        ]

    def _active_entry(self):
        return _entry(
            sid="bitcoin-mcp", status_value="active",
            package_spec="pypi:bitcoin-mcp", trust_score=70,
            capability_tags=("bitcoin",), display_name="bitcoin-mcp",
        )

    def test_active_server_matched_via_sid_mention(self):
        """Round 10 préservé via AT : « utiliser bitcoin-mcp » mentionne
        le sid → candidat actif AU-DESSUS du seuil 0.3."""
        from src.mcp.capability_resolver import _tokenize, _MATCH_MCP_MIN
        resolver = self._resolver()
        intent = "utiliser bitcoin-mcp"
        cands = resolver._build_mcp_active_candidates(
            _tokenize(intent),
            self._handlers(),
            [self._active_entry()],
        )
        cands = resolver._apply_sid_mention_priority(
            intent, cands, [self._active_entry()], self._handlers(),
        )
        top = max(cands, key=lambda c: c.match_score)
        assert top.match_score >= _MATCH_MCP_MIN
        assert top.server_id == "bitcoin-mcp"

    def test_cascade_returns_use_active(self):
        from src.mcp.capability_resolver import (
            CapabilityDecision, _tokenize,
        )
        resolver = self._resolver()
        intent = "utiliser bitcoin-mcp"
        cands = resolver._build_mcp_active_candidates(
            _tokenize(intent),
            self._handlers(),
            [self._active_entry()],
        )
        cands = resolver._apply_sid_mention_priority(
            intent, cands, [self._active_entry()], self._handlers(),
        )
        cands.sort(key=lambda c: c.match_score, reverse=True)
        decision, selected = resolver._cascade_decision(
            cands, _tokenize(intent),
            [self._active_entry()], [],
        )
        assert decision == CapabilityDecision.USE_ACTIVE_MCP_TOOL
        assert selected is not None

    def test_au_subject_tag_never_elects_active_server(self):
        """Fix AU — régression exacte du round duckduckgo 10:37 : le tag
        « bitcoin » du serveur ACTIF ne doit JAMAIS produire un candidat
        au-dessus du seuil quand l'intent parle du SUJET bitcoin sans
        nommer le serveur."""
        from src.mcp.capability_resolver import _tokenize
        resolver = self._resolver()
        intent = "recherche web via DuckDuckGo pour actualité Bitcoin"
        cands = resolver._build_mcp_active_candidates(
            _tokenize(intent), self._handlers(), [self._active_entry()],
        )
        cands = resolver._apply_sid_mention_priority(
            intent, cands, [self._active_entry()], self._handlers(),
        )
        assert all(c.match_score < 0.3 for c in cands)

    def test_no_false_positive_unrelated_intent(self):
        """Un intent sans rapport ne doit produire aucun candidat serveur."""
        from src.mcp.capability_resolver import _tokenize
        resolver = self._resolver()
        cands = resolver._build_mcp_active_candidates(
            _tokenize("envoie un message sur slack"),
            self._handlers(),
            [self._active_entry()],
        )
        assert all(c.match_score < 0.3 for c in cands)

    def test_no_synthetic_candidate_without_handlers(self):
        """Entrée ACTIVE fantôme (0 handler enregistré) → pas de candidat
        synthétique : on ne promet jamais un tool inexistant."""
        from src.mcp.capability_resolver import _tokenize
        resolver = self._resolver()
        cands = resolver._build_mcp_active_candidates(
            _tokenize("utiliser bitcoin-mcp"),
            [],
            [self._active_entry()],
        )
        assert cands == []

    def test_direct_tool_match_still_wins_unchanged(self):
        """Quand un tool matche déjà mieux que l'identité serveur, pas de
        candidat synthétique en doublon au-dessus de lui."""
        from src.mcp.capability_resolver import _tokenize
        resolver = self._resolver()
        handlers = [{
            "name": "mcp__bitcoin-mcp__get_btc_price",
            "description": "btc price",
            "server_id": "bitcoin-mcp",
        }]
        cands = resolver._build_mcp_active_candidates(
            _tokenize("btc price"), handlers, [self._active_entry()],
        )
        names = [c.tool_name for c in cands]
        assert names.count("mcp__bitcoin-mcp__get_btc_price") == 1


class TestFixATSidMentionPriority:
    """Fix AT — runtime 2026-06-12 10:22 : « installer duckduckgo-mcp-server
    pour chercher actualité bitcoin » → le mot « bitcoin » (SUJET de la
    recherche) a fait élire le serveur ACTIF bitcoin-mcp via le fallback
    tags (Fix AR) contre le DECLARED duckduckgo-mcp-server nommé en toutes
    lettres → ready_to_use mensonger, install jamais lancée, fallback
    web_search. Un sid écrit tel quel dans l'intent doit primer sur toute
    heuristique."""

    _INTENT = ("installer et utiliser le serveur duckduckgo-mcp-server "
               "pour chercher 'actualité bitcoin'")

    def _resolver(self):
        from src.mcp.capability_resolver import (
            CapabilityResolver,
            CapabilityResolverDeps,
        )
        return CapabilityResolver(CapabilityResolverDeps(catalog=MagicMock()))

    def _bitcoin_active(self):
        return _entry(
            sid="bitcoin-mcp", status_value="active",
            package_spec="pypi:bitcoin-mcp", trust_score=70,
            capability_tags=("bitcoin",), display_name="bitcoin-mcp",
        )

    def _ddg_declared(self):
        return _entry(
            sid="duckduckgo-mcp-server", status_value="declared",
            package_spec="pypi:duckduckgo-mcp-server", trust_score=70,
            capability_tags=("duckduckgo",),
            display_name="duckduckgo-mcp-server",
        )

    def _handlers(self):
        return [{
            "name": "mcp__bitcoin-mcp__get_btc_price",
            "description": (
                "Get current BTC USD price from CoinGecko free no API key"
            ),
            "server_id": "bitcoin-mcp",
        }]

    def _pipeline(self, intent, entries, handlers):
        from src.mcp.capability_resolver import _tokenize
        resolver = self._resolver()
        tokens = _tokenize(intent)
        cands = []
        cands.extend(resolver._build_mcp_active_candidates(
            tokens, handlers, entries))
        cands.extend(resolver._build_mcp_installed_candidates(
            tokens, entries, {}))
        cands.extend(resolver._build_mcp_declared_candidates(
            tokens, entries))
        cands = resolver._apply_sid_mention_priority(
            intent, cands, entries, handlers)
        cands.sort(key=lambda c: c.match_score, reverse=True)
        decision, selected = resolver._cascade_decision(
            cands, tokens, entries, [])
        return decision, selected

    def test_regression_exacte_du_log(self):
        """L'intent du round duckduckgo doit élire le DECLARED nommé,
        pas le serveur actif dont le tag matche le sujet de la recherche."""
        from src.mcp.capability_resolver import CapabilityDecision
        decision, selected = self._pipeline(
            self._INTENT,
            [self._bitcoin_active(), self._ddg_declared()],
            self._handlers(),
        )
        assert decision == CapabilityDecision.INSTALL_DECLARED_MCP
        assert selected.server_id == "duckduckgo-mcp-server"

    def test_round10_use_active_preserved(self):
        """« utiliser bitcoin-mcp » mentionne le sid actif → ready_to_use
        (comportement Fix AR conservé, boosté par la mention)."""
        from src.mcp.capability_resolver import CapabilityDecision
        decision, selected = self._pipeline(
            "utiliser bitcoin-mcp",
            [self._bitcoin_active(), self._ddg_declared()],
            self._handlers(),
        )
        assert decision == CapabilityDecision.USE_ACTIVE_MCP_TOOL
        assert selected.server_id == "bitcoin-mcp"

    def test_no_mention_no_wrong_server_elected(self):
        """Sans mention de sid, aucun serveur n'est élu sur la base d'un
        tag de sujet (post-AU) : la cascade retombe sur la recherche ou
        no_capability — jamais un ready_to_use mensonger."""
        from src.mcp.capability_resolver import CapabilityDecision
        decision, selected = self._pipeline(
            "donne moi le prix du bitcoin",
            [self._bitcoin_active(), self._ddg_declared()],
            self._handlers(),
        )
        assert decision in (
            CapabilityDecision.SEARCH_MCP,
            CapabilityDecision.NO_CAPABILITY_FOUND,
        )
        assert selected is None

    def test_word_does_not_mention_hyphenated_sid(self):
        """« bitcoin » seul ne mentionne pas « bitcoin-mcp » : pas de
        boost 0.95 ni de candidat synthétique."""
        resolver = self._resolver()
        cands = resolver._apply_sid_mention_priority(
            "donne moi le prix du bitcoin",
            [], [self._bitcoin_active()], self._handlers(),
        )
        assert cands == []

    def test_active_mention_without_handlers_no_ghost(self):
        """Entrée ACTIVE fantôme mentionnée sans handler → aucun candidat
        synthétique (pas de promesse fantôme)."""
        resolver = self._resolver()
        cands = resolver._apply_sid_mention_priority(
            "utiliser bitcoin-mcp",
            [], [self._bitcoin_active()], [],
        )
        assert cands == []

    def test_mention_desaccentuee(self):
        """La mention survit aux accents de l'intent."""
        from src.mcp.capability_resolver import CapabilityDecision
        decision, selected = self._pipeline(
            "Installe duckduckgo-mcp-server et cherche les actualités",
            [self._ddg_declared()],
            [],
        )
        assert decision == CapabilityDecision.INSTALL_DECLARED_MCP
        assert selected.server_id == "duckduckgo-mcp-server"


class TestFixASGithubTarget:
    """Fix AS — le chemin « MCP depuis une URL GitHub » était câblé mais
    débranché : les appelants réels n'injectaient jamais de fetcher README
    → package_spec=None → crash `mcp_action_failed` générique. Désormais :
    fetcher par défaut (raw.githubusercontent), regex d'extraction
    modernisées, et blocage HONNÊTE quand le repo n'a pas de package
    publié. AUCUN test ne touche le réseau (leçon Fix AB)."""

    @pytest.fixture(autouse=True)
    def _no_network(self, monkeypatch):
        self._fetched_urls = []

        def _fake_fetch(url):
            self._fetched_urls.append(url)
            return getattr(self, "_fake_readme", "")

        monkeypatch.setattr(
            "src.mcp.target_resolver._default_github_readme_fetch",
            _fake_fetch,
        )

    # ── Regex modernisées ─────────────────────────────────────────────

    @pytest.mark.parametrize("readme,expected", [
        # npx -y = LA forme standard des READMEs MCP (ratée avant AS)
        ("Run with:\n```\nnpx -y @upstash/context7-mcp\n```", "npm:@upstash/context7-mcp"),
        ("npx -y duckduckgo-mcp-server", "npm:duckduckgo-mcp-server"),
        ("npm install -g @org/tool-mcp", "npm:@org/tool-mcp"),
        ("npm add my-mcp-server", "npm:my-mcp-server"),
        ("pip install -U bitcoin-mcp", "pypi:bitcoin-mcp"),
        ("uv tool install weather-mcp", "pypi:weather-mcp"),
        ("uvx --from duckduckgo-mcp-server ddg", "pypi:duckduckgo-mcp-server"),
        ("uvx mcp-fast", "pypi:mcp-fast"),
    ])
    def test_extract_modern_install_commands(self, readme, expected):
        from src.mcp.target_resolver import _extract_install_from_readme
        assert _extract_install_from_readme(readme) == expected

    def test_extract_none_when_no_command(self):
        from src.mcp.target_resolver import _extract_install_from_readme
        assert _extract_install_from_readme(
            "# Mon projet\nJuste de la doc, aucune commande."
        ) is None

    # ── Scoring calibré sur de VRAIS READMEs (2026-06-12) ─────────────

    def test_scoring_windows_mcp_real_shape(self):
        """Forme réelle du README CursorTouch/Windows-MCP : un outil de
        packaging npm apparaît AVANT les vraies commandes pip — l'ancien
        « npm en premier » élisait @anthropic-ai/mcpb."""
        from src.mcp.target_resolver import _extract_install_from_readme
        readme = (
            "## Install\npip install windows-mcp\n\n"
            "## Packaging\nnpx @anthropic-ai/mcpb pack\n\n"
            "## Update\npip install -U windows-mcp\n"
        )
        assert _extract_install_from_readme(
            readme, repo_hint="Windows-MCP"
        ) == "pypi:windows-mcp"

    def test_scoring_context7_real_shape(self):
        """Forme réelle du README upstash/context7 : le vrai package
        n'apparaît QUE dans les badges npm/smithery ; les commandes shell
        montrent leur CLI `ctx7`."""
        from src.mcp.target_resolver import _extract_install_from_readme
        readme = (
            "[![smithery](https://smithery.ai/badge/@upstash/context7-mcp)]"
            "(https://smithery.ai/server/@upstash/context7-mcp)\n"
            "## Quick install\nnpx ctx7 install\n"
            "Or: npm install -g ctx7\n"
        )
        assert _extract_install_from_readme(
            readme, repo_hint="context7"
        ) == "npm:@upstash/context7-mcp"

    def test_scoring_json_config_snippet(self):
        """Les snippets JSON claude_desktop comptent comme candidats."""
        from src.mcp.target_resolver import _extract_install_from_readme
        readme = (
            "Add to your config:\n```json\n"
            '{"mcpServers": {"foo": {"command": "uvx", '
            '"args": ["some-mcp-server"]}}}\n```\n'
        )
        assert _extract_install_from_readme(
            readme, repo_hint="some-mcp-server"
        ) == "pypi:some-mcp-server"

    def test_scoring_generic_tools_never_elected(self):
        from src.mcp.target_resolver import _extract_install_from_readme
        assert _extract_install_from_readme(
            "First: pip install uv\nThen nothing else."
        ) is None

    # ── Branchement du fetcher par défaut ─────────────────────────────

    def test_default_fetcher_used_without_injection(self):
        """Le cœur du fix : sans fetcher injecté, le défaut est appelé
        et le spec est extrait du README."""
        from src.mcp.target_resolver import resolve_target
        self._fake_readme = "Install:\n```\nnpx -y @upstash/context7-mcp\n```"
        r = resolve_target("https://github.com/upstash/context7")
        assert r.kind == "github_url"
        assert r.package_spec == "npm:@upstash/context7-mcp"
        assert self._fetched_urls == ["https://github.com/upstash/context7"]

    def test_injected_fetcher_still_wins(self):
        """Un fetcher explicite (tests existants) prime sur le défaut."""
        from src.mcp.target_resolver import resolve_target
        r = resolve_target(
            "https://github.com/x/y",
            web_fetch_callable=lambda u: "pip install custom-mcp",
        )
        assert r.package_spec == "pypi:custom-mcp"
        assert self._fetched_urls == []

    def test_default_fetch_rejects_non_github_url(self):
        from src.mcp.target_resolver import _default_github_readme_fetch
        assert _default_github_readme_fetch("https://evil.com/x/y") == ""
        assert _default_github_readme_fetch(None) == ""

    # ── Blocage honnête quand le repo n'a pas de package ──────────────

    def test_add_mcp_live_github_without_package_blocked_honestly(self):
        """Régression du trou n°4 : avant AS, ce cas finissait en
        `mcp_action_failed` générique via le crash de propose()."""
        self._fake_readme = "# Projet sans package publie"
        integration = _make_integration()
        out = integration.handle_add_mcp(
            "https://github.com/owner/unpublished-repo",
            live=True,
            caller_kind="react",
            confirmation_phrase="I-CONFIRM-ADD-MCP",
        )
        data = json.loads(out)
        assert data["decision"] == "blocked"
        assert data["payload"]["recommendation_code"] == "mcp_github_no_package"
        assert "nom EXACT" in data["payload"]["hint"]
        # Le code est whitelisté (piège Fix AB : code inconnu → fallback
        # silencieux caller_kind_not_allowed)
        assert data["blockers"] == ["mcp_github_no_package"]

    def test_guidance_github_no_package(self):
        from src.reasoning.react import _phase27_mcp_observation_guidance
        obs = json.dumps({
            "decision": "blocked",
            "payload": {"recommendation_code": "mcp_github_no_package"},
        })
        guidance = _phase27_mcp_observation_guidance("add_mcp", obs)
        assert guidance is not None
        assert "nom EXACT" in guidance
        assert "git clone" in guidance


class TestFixAWShutdownAll:
    """Fix AW — à la fermeture de Lumena, les subprocess MCP devenaient
    ORPHELINS (aucun arrêt au shutdown du lifespan) et le catalogue
    gardait des ACTIVE fantômes. shutdown_all() désactive tout,
    best-effort."""

    def _service(self, sids, deactivate_impl):
        from src.mcp.activation_service import MCPActivationService
        svc = object.__new__(MCPActivationService)
        svc._running_contexts = {sid: object() for sid in sids}
        svc.deactivate = deactivate_impl
        svc._audit = lambda *a, **k: None
        return svc

    def test_stops_all_running_servers(self):
        stopped = []

        def _ok(sid):
            stopped.append(sid)
            return SimpleNamespace(success=True)

        svc = self._service(["memory", "slack", "bitcoin-mcp"], _ok)
        results = svc.shutdown_all()
        assert sorted(stopped) == ["bitcoin-mcp", "memory", "slack"]
        assert all(results.values())

    def test_one_failure_does_not_stop_the_rest(self):
        stopped = []

        def _flaky(sid):
            stopped.append(sid)
            if sid == "slack":
                raise RuntimeError("boom")
            return SimpleNamespace(success=True)

        svc = self._service(["memory", "slack", "bitcoin-mcp"], _flaky)
        results = svc.shutdown_all()
        assert len(stopped) == 3
        assert results["slack"] is False
        assert results["memory"] is True

    def test_empty_map_is_noop(self):
        svc = self._service([], lambda sid: SimpleNamespace(success=True))
        assert svc.shutdown_all() == {}

    def test_lifespan_calls_shutdown_all(self):
        """Source-scan : le shutdown du lifespan doit invoquer
        shutdown_all AVANT de libérer les singletons MCP."""
        content = (
            REPO / "web" / "routes" / "lifespan.py"
        ).read_text(encoding="utf-8")
        idx_shutdown = content.find("=== SHUTDOWN ===")
        assert idx_shutdown > 0
        shutdown_zone = content[idx_shutdown:]
        idx_call = shutdown_zone.find("shutdown_all")
        idx_release = shutdown_zone.find(
            "deps._MCP_INSTALL_ORCHESTRATOR_SINGLETON = None"
        )
        assert idx_call > 0, "le shutdown du lifespan n'appelle pas shutdown_all"
        assert idx_release > idx_call, (
            "shutdown_all doit être appelé AVANT la libération des "
            "singletons MCP"
        )


class TestFixAXPythonVersion:
    """Fix AX — quand `uv pip install` refuse un package parce qu'il exige un
    Python plus récent que celui du venv isolé (ex. windows-mcp → Python>=3.13),
    on recrée le venv ISOLÉ avec `uv venv --python X.Y` (CPython managé par uv,
    confiné dans le cache du serveur) puis on relance l'install une fois.
    Le venv de Lumena n'est JAMAIS touché.
    """

    def _runner(self, tmp_path, monkeypatch):
        import subprocess as _sp
        from src.mcp.sandbox_runner import MCPSandboxRunner

        runner = object.__new__(MCPSandboxRunner)
        runner._server_dir = tmp_path
        runner.spec = SimpleNamespace(
            package="windows-mcp",
            package_version=None,
            require_wheels_only=False,
            lock_file=None,
        )

        calls = []

        def _fake_run(cmd, env, *, check=True):
            calls.append({"cmd": list(cmd), "check": check})
            joined = " ".join(cmd)
            if "venv" in cmd:
                return _sp.CompletedProcess(cmd, 0, stdout="", stderr="")
            # commande d'install
            install_calls = [c for c in calls if "pip" in c["cmd"]]
            if len(install_calls) == 1:
                # 1ʳᵉ install : échec exigence Python
                if check:  # pragma: no cover - le nominal passe check=False
                    from src.mcp.sandbox_runner import MCPSandboxError
                    raise MCPSandboxError("forced")
                return _sp.CompletedProcess(
                    cmd, 1, stdout="",
                    stderr=(
                        "Because windows-mcp requires Python>=3.13 and the "
                        "current Python version is 3.12.10, ..."
                    ),
                )
            return _sp.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(runner, "_run_install_command", _fake_run)
        return runner, calls

    def test_reactive_retry_recreates_venv_with_required_python(
        self, tmp_path, monkeypatch
    ):
        runner, calls = self._runner(tmp_path, monkeypatch)
        runner._install_uv({})

        venv_calls = [c for c in calls if "venv" in c["cmd"]]
        install_calls = [c for c in calls if "pip" in c["cmd"]]
        # 2 créations de venv : nominale puis recréation --python 3.13
        assert len(venv_calls) == 2
        assert "--python" not in venv_calls[0]["cmd"]
        assert venv_calls[1]["cmd"][venv_calls[1]["cmd"].index("--python") + 1] == "3.13"
        assert "--clear" in venv_calls[1]["cmd"]
        # 2 installs : nominale (check=False) puis retry (check=True)
        assert len(install_calls) == 2
        assert install_calls[0]["check"] is False
        assert install_calls[1]["check"] is True

    def test_no_retry_when_install_succeeds(self, tmp_path, monkeypatch):
        import subprocess as _sp
        from src.mcp.sandbox_runner import MCPSandboxRunner

        runner = object.__new__(MCPSandboxRunner)
        runner._server_dir = tmp_path
        runner.spec = SimpleNamespace(
            package="open-meteo-mcp",
            package_version=None,
            require_wheels_only=False,
            lock_file=None,
        )
        calls = []

        def _fake_run(cmd, env, *, check=True):
            calls.append(list(cmd))
            return _sp.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(runner, "_run_install_command", _fake_run)
        runner._install_uv({})

        venv_calls = [c for c in calls if "venv" in c]
        install_calls = [c for c in calls if "pip" in c]
        assert len(venv_calls) == 1  # pas de recréation
        assert "--python" not in venv_calls[0]
        assert len(install_calls) == 1  # pas de retry

    def test_unrelated_failure_raises_without_retry(
        self, tmp_path, monkeypatch
    ):
        import subprocess as _sp
        from src.mcp.sandbox_runner import (
            MCPSandboxRunner,
            MCPSandboxError,
        )

        runner = object.__new__(MCPSandboxRunner)
        runner._server_dir = tmp_path
        runner.spec = SimpleNamespace(
            package="broken-mcp",
            package_version=None,
            require_wheels_only=False,
            lock_file=None,
        )
        calls = []

        def _fake_run(cmd, env, *, check=True):
            calls.append(list(cmd))
            if "venv" in cmd:
                return _sp.CompletedProcess(cmd, 0, stdout="", stderr="")
            return _sp.CompletedProcess(
                cmd, 1, stdout="", stderr="ERROR: network unreachable"
            )

        monkeypatch.setattr(runner, "_run_install_command", _fake_run)
        with pytest.raises(MCPSandboxError):
            runner._install_uv({})
        # une seule création de venv, aucun retry
        assert len([c for c in calls if "venv" in c]) == 1
        assert len([c for c in calls if "pip" in c]) == 1

    def test_regex_extracts_version(self):
        from src.mcp.sandbox_runner import _PY_REQUIRES_RE

        m = _PY_REQUIRES_RE.search(
            "Because foo requires Python>=3.13 and the current ..."
        )
        assert m is not None and m.group(1) == "3.13"
        assert _PY_REQUIRES_RE.search("python >= 3.11 needed").group(1) == "3.11"
        assert _PY_REQUIRES_RE.search("no version requirement here") is None


class _FakeCLIRunner:
    """Runner factice : entry point = CLI à sous-commandes (signature
    windows-mcp réelle sur stderr). start() réussit toujours (le start ne
    crashe pas immédiatement — c'est le handshake initialize qui révèle
    l'échec), c'est le client factice qui décide selon entry_args."""

    _CLI_STDERR = [
        "Usage: windows-mcp [OPTIONS] COMMAND [ARGS]...",
        "Try 'windows-mcp --help' for help.",
        "",
        "Error: Missing command.",
    ]

    def __init__(self, stderr_lines=None, args=None, entry_args=None):
        self.spec = SimpleNamespace(
            args=list(args or []), entry_args=list(entry_args or [])
        )
        self._stderr = list(
            stderr_lines if stderr_lines is not None else self._CLI_STDERR
        )
        self.start_calls = []
        self.stop_calls = 0
        self.quarantine_clears = 0

    def get_logs(self, lines=50, stream="stderr"):
        return list(self._stderr)

    def set_entry_args(self, entry_args):
        self.spec = SimpleNamespace(
            args=list(self.spec.args), entry_args=list(entry_args or [])
        )

    def start(self, runtime_env_secrets=None):
        self.start_calls.append(list(self.spec.entry_args))

    def stop(self):
        self.stop_calls += 1

    def clear_quarantine(self):
        self.quarantine_clears += 1


class TestFixAYEntrySubcommand:
    """Fix AY — runtime 2026-06-13 02:16 : windows-mcp installé (3.13, Fix AX)
    et démarré, mais `client_initialize_failed` ×2 → rollback. Cause repro
    Temp : `windows-mcp.exe` nu → rc=2 « Missing command » (CLI Click à
    sous-commandes auth/install/serve/uninstall) ; `windows-mcp.exe serve`
    → handshake initialize parfait. Le retry réactif détecte la signature
    CLI sur stderr, bruteforce les sous-commandes serveur, persiste la
    gagnante au catalogue (`start_entry_args`)."""

    def _service(self, client_factory, catalog=None):
        from src.mcp.activation_service import MCPActivationService
        svc = object.__new__(MCPActivationService)
        svc._catalog = catalog if catalog is not None else MagicMock()
        svc._client_factory = client_factory
        svc.audits = []
        svc._audit = lambda event, **f: svc.audits.append((event, f))
        return svc

    def _client_factory_accepting(self, accepted_entry_args):
        """Client factice : initialize() ne réussit que si le runner a les
        entry_args attendus (simule le vrai handshake JSON-RPC)."""
        def _factory(runner):
            client = SimpleNamespace()
            ea = list(runner.spec.entry_args)

            def _init():
                if ea != accepted_entry_args:
                    raise RuntimeError("initialize failed")
            client.initialize = _init
            client.close = lambda: None
            return client
        return _factory

    def test_recovers_with_serve_and_persists(self):
        runner = _FakeCLIRunner()
        catalog = MagicMock()
        svc = self._service(
            self._client_factory_accepting(["serve"]), catalog=catalog
        )
        client = svc._try_entry_subcommand_recovery(
            "windows-mcp", runner, SimpleNamespace(close=lambda: None), {},
        )
        assert client is not None
        # serve est le 1er candidat → un seul start de bruteforce
        assert runner.start_calls == [["serve"]]
        catalog.update_start_entry_args.assert_called_once_with(
            "windows-mcp", ["serve"]
        )
        events = [e for e, _ in svc.audits]
        assert "entry_subcommand_recovered" in events

    def test_later_candidate_wins_after_failures(self):
        runner = _FakeCLIRunner()
        svc = self._service(self._client_factory_accepting(["stdio"]))
        client = svc._try_entry_subcommand_recovery(
            "x-mcp", runner, SimpleNamespace(close=lambda: None), {},
        )
        assert client is not None
        # serve et run échouent, stdio gagne
        assert runner.start_calls == [["serve"], ["run"], ["stdio"]]
        # quarantine clearée avant chaque tentative (sinon 3 crashes = mort)
        assert runner.quarantine_clears == 3

    def test_no_recovery_without_cli_signature(self):
        """Un crash générique (traceback, missing token...) ne déclenche
        JAMAIS le bruteforce — zéro coût au nominal."""
        runner = _FakeCLIRunner(
            stderr_lines=["Traceback (most recent call last):",
                          "KeyError: 'SLACK_BOT_TOKEN'"]
        )
        svc = self._service(self._client_factory_accepting(["serve"]))
        client = svc._try_entry_subcommand_recovery(
            "slack", runner, SimpleNamespace(close=lambda: None), {},
        )
        assert client is None
        assert runner.start_calls == []

    def test_no_recovery_when_explicit_args(self):
        """spec.args explicite (ex. local: -m module) : jamais de bruteforce."""
        runner = _FakeCLIRunner(args=["-m", "my_module"])
        svc = self._service(self._client_factory_accepting(["serve"]))
        client = svc._try_entry_subcommand_recovery(
            "local-mcp", runner, SimpleNamespace(close=lambda: None), {},
        )
        assert client is None
        assert runner.start_calls == []

    def test_all_candidates_fail_resets_entry_args(self):
        runner = _FakeCLIRunner()
        svc = self._service(self._client_factory_accepting(["never-matches"]))
        client = svc._try_entry_subcommand_recovery(
            "y-mcp", runner, SimpleNamespace(close=lambda: None), {},
        )
        assert client is None
        assert len(runner.start_calls) == 5  # tous les candidats tentés
        assert runner.spec.entry_args == []  # reset final

    def test_build_start_command_appends_entry_args(self, tmp_path):
        """uv : entry point console résolu + entry_args ajoutés après."""
        from src.mcp.sandbox_runner import MCPSandboxRunner

        scripts = tmp_path / ".venv" / (
            "Scripts" if __import__("sys").platform == "win32" else "bin"
        )
        scripts.mkdir(parents=True)
        exe_name = (
            "windows-mcp.exe"
            if __import__("sys").platform == "win32" else "windows-mcp"
        )
        (scripts / exe_name).write_bytes(b"")

        runner = object.__new__(MCPSandboxRunner)
        runner._server_dir = tmp_path
        runner.spec = SimpleNamespace(
            transport="uv", package="windows-mcp", args=[],
            entry_args=["serve"],
        )
        cmd = runner._build_start_command()
        assert cmd[0].endswith(exe_name)
        assert cmd[1:] == ["serve"]

    def test_set_entry_args_replaces_frozen_spec(self, tmp_path):
        from src.mcp.sandbox_runner import MCPInstallSpec, MCPSandboxRunner

        runner = object.__new__(MCPSandboxRunner)
        runner.spec = MCPInstallSpec(
            name="x-mcp", transport="uv", package="x-mcp",
        )
        runner.set_entry_args(["serve"])
        assert runner.spec.entry_args == ["serve"]
        runner.set_entry_args([])
        assert runner.spec.entry_args == []

    def test_catalog_roundtrip_start_entry_args(self, tmp_path):
        from src.mcp.server_catalog import MCPServerCatalog

        catalog = MCPServerCatalog(
            catalog_dir=tmp_path / "catalog",
            audit_log_path=tmp_path / "audit.jsonl",
        )
        catalog.add_server(
            server_id="windows-mcp",
            display_name="Windows MCP",
            package_spec="pypi:windows-mcp",
            owner_profile="owner",
            trust_score=70,
        )
        updated = catalog.update_start_entry_args("windows-mcp", ["serve"])
        assert updated.start_entry_args == ("serve",)
        # Relecture disque (nouvelle instance) : persistance + HMAC OK
        catalog2 = MCPServerCatalog(
            catalog_dir=tmp_path / "catalog",
            audit_log_path=tmp_path / "audit.jsonl",
        )
        entry = catalog2.get_server("windows-mcp")
        assert entry is not None
        assert entry.start_entry_args == ("serve",)
        # reset à None
        catalog2.update_start_entry_args("windows-mcp", None)
        assert catalog2.get_server("windows-mcp").start_entry_args is None

    def test_catalog_rejects_invalid_entry_args(self, tmp_path):
        from src.mcp.server_catalog import CatalogError, MCPServerCatalog

        catalog = MCPServerCatalog(
            catalog_dir=tmp_path / "catalog",
            audit_log_path=tmp_path / "audit.jsonl",
        )
        catalog.add_server(
            server_id="x-mcp",
            display_name="X",
            package_spec="pypi:x-mcp",
            owner_profile="owner",
            trust_score=70,
        )
        for bad in (["serve; rm -rf /"], ["a b"], [""], ["x" * 33],
                    ["a", "b", "c", "d", "e"], "serve", [42]):
            with pytest.raises(CatalogError):
                catalog.update_start_entry_args("x-mcp", bad)

    def test_install_spec_reads_start_entry_args(self):
        from web.routes.mcp import _build_install_spec_from_entry

        entry = SimpleNamespace(
            server_id="windows-mcp",
            package_spec="pypi:windows-mcp",
            version=None,
            trust_score=70,
            config_schema=None,
            start_entry_args=("serve",),
        )
        spec = _build_install_spec_from_entry(entry)
        assert spec.entry_args == ["serve"]
        # back-compat : None → []
        entry.start_entry_args = None
        spec = _build_install_spec_from_entry(entry)
        assert spec.entry_args == []


class TestFixAZPascalCaseToolNames:
    """Fix AZ — runtime 2026-06-13 02:43 : windows-mcp activé (Fix AY) mais
    `registered_count: 0` — ses 19 tools PascalCase (App, Click, PowerShell,
    WaitFor...) TOUS refusés `name_invalid` par le regex lowercase-only de
    discovery. La spec MCP n'impose pas la casse. Effet domino : serveur
    ACTIF sans handlers → AT refuse le candidat synthétique → ticket
    catalog_add parasite + doublon proposed_cf93131b1d. Casse élargie dans
    discovery + policy_attributor + policy_resolver (segment tool only) ;
    charset inchangé."""

    # Les 19 noms RÉELS de windows-mcp 3.4.2 (tools/list, repro Temp)
    _WINDOWS_MCP_TOOLS = [
        "App", "PowerShell", "FileSystem", "Snapshot", "Screenshot",
        "Click", "Type", "Scroll", "Move", "Shortcut", "Wait", "WaitFor",
        "Scrape", "MultiSelect", "MultiEdit", "Clipboard", "Process",
        "Notification", "Registry",
    ]

    def test_discovery_accepts_all_real_windows_mcp_names(self):
        from src.mcp.discovery import _validate_tool_name
        for name in self._WINDOWS_MCP_TOOLS:
            assert _validate_tool_name(name) is None, name

    def test_discovery_charset_still_strict(self):
        from src.mcp.discovery import _validate_tool_name
        for bad in ("App!", "Click Here", "Power$hell", "", None, "a" * 129):
            assert _validate_tool_name(bad) is not None, bad

    def test_discovery_spoofing_still_rejected(self):
        from src.mcp.discovery import _validate_tool_name
        assert _validate_tool_name("mcp__Alice__Tool") == "name_spoofing"

    def test_resolver_accepts_pascal_tool_segment_only(self):
        from src.mcp.policy_resolver import _TOOL_NAME_RE_P15
        assert _TOOL_NAME_RE_P15.match("mcp__windows-mcp__App")
        assert _TOOL_NAME_RE_P15.match("mcp__windows-mcp__WaitFor")
        # segment SERVER reste lowercase strict
        assert not _TOOL_NAME_RE_P15.match("mcp__Windows-MCP__App")

    def test_attributor_accepts_pascal_names(self):
        from src.mcp.policy_attributor import _TOOL_NAME_LOCAL_RE
        for name in self._WINDOWS_MCP_TOOLS:
            assert _TOOL_NAME_LOCAL_RE.match(name), name

    def test_normalizer_never_mangles_mcp_names(self):
        """Fix AZ.2 — normalize_action_name (appelé par response_parser sur
        CHAQUE ACTION) convertissait camelCase→snake_case :
        mcp__windows-mcp__WaitFor → mcp__windows-mcp__wait_for → introuvable
        au registry. Un nom mcp__* ne doit JAMAIS être normalisé."""
        from src.llm.output_normalizer import normalize_action_name
        for tool in self._WINDOWS_MCP_TOOLS:
            full = f"mcp__windows-mcp__{tool}"
            assert normalize_action_name(full) == full, full
        # Le comportement hors-MCP est préservé (alias + camelCase)
        assert normalize_action_name("readFile") == "read_file"
        assert normalize_action_name("cat") == "read_file"

    def test_parser_action_line_preserves_pascal_mcp_name(self):
        """Bout-en-bout parser : ACTION mcp__windows-mcp__WaitFor doit
        ressortir INTACT (regex Fix AQ + non-normalisation Fix AZ.2)."""
        from src.reasoning.response_parser import parse_response
        text = (
            "THOUGHT: je clique\n"
            "ACTION: mcp__windows-mcp__WaitFor\n"
            'ACTION_INPUT: {"selector": "button"}\n'
        )
        _thought, action, _halluc, _multi = parse_response(text)
        assert action.tool_name == "mcp__windows-mcp__WaitFor"


# ──────────────────────────────────────────────────────────────────────────────
# Invariant d'architecture : la frontière de confiance
# ──────────────────────────────────────────────────────────────────────────────


class TestTrustBoundaryInvariant:

    def test_catalog_add_noncurated_still_requires_human(self):
        """INVARIANT : aucun bypass ne doit couvrir mcp_catalog_add: pour
        un package non-curated. Si ce test casse, un package arbitraire
        peut entrer au catalogue sans humain — vulnérabilité majeure."""
        from src.mcp.react_integration import (
            _is_cataloged_install_ticket,
            _is_curated_install_ticket,
        )
        catalog = MagicMock()
        catalog.get_server = MagicMock(return_value=_entry())
        assert _is_cataloged_install_ticket(
            "mcp_catalog_add:weather-mcp", "weather-mcp", {}, catalog
        ) is False
        assert _is_curated_install_ticket(
            "mcp_catalog_add:weather-mcp", "weather-mcp", {}
        ) is False  # weather-mcp ∉ KNOWN_MCPS

    def test_secrets_trust_threshold_unreachable_by_floor(self):
        """Le plancher humain (70) ne doit jamais atteindre le seuil
        SECRETS (90) du PolicyAttributor."""
        from src.mcp.catalog_add_orchestrator import (
            _HUMAN_APPROVED_TRUST_FLOOR,
        )
        from src.mcp.policy_attributor import (
            _DEFAULT_MIN_TRUST_FOR_SECRETS,
        )
        assert _HUMAN_APPROVED_TRUST_FLOOR < _DEFAULT_MIN_TRUST_FOR_SECRETS
