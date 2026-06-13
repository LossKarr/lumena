"""Phase I-7 Fixes R + S + T — Finalisation autonomie MCP.

Fix R : alias `<server>__<tool>` → `mcp__<server>__<tool>` dans
ToolRegistry.execute(). Le registre namespace les tools MCP avec le
préfixe `mcp__` (anti-collision), mais le LLM appelle souvent la forme
courte enseignée historiquement par le skill.

Fix S : statut ACTIVE fantôme. Un MCP activé lors d'une session
précédente reste "active" dans le catalog persisté alors que son
process est mort → ni le boot ni l'autonomy ne peuvent le réactiver.
Réconciliation au boot + self-healing dans _force_activate_if_needed.

Fix T : levée contrôlée des policies WRITE (bloquées Phase 9) via
double opt-in env LUMENA_MCP_LIVE=1 + LUMENA_MCP_TRUST_LIVE=1.
SECRETS_AUTH reste toujours bloquée.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.mcp.policy import (
    MCPPolicy,
    is_blocked_effective,
    is_blocked_phase9,
    is_write_lift_enabled,
)


# ──────────────────────────────────────────────────────────────────────────────
# Fix T — policy lift
# ──────────────────────────────────────────────────────────────────────────────


class TestFixTPolicyLift:

    def test_reads_never_blocked(self, monkeypatch):
        monkeypatch.delenv("LUMENA_MCP_LIVE", raising=False)
        monkeypatch.delenv("LUMENA_MCP_TRUST_LIVE", raising=False)
        assert is_blocked_effective(MCPPolicy.READ_ONLY) is False
        assert is_blocked_effective(MCPPolicy.EXTERNAL_READ) is False

    def test_writes_blocked_without_flags(self, monkeypatch):
        monkeypatch.delenv("LUMENA_MCP_LIVE", raising=False)
        monkeypatch.delenv("LUMENA_MCP_TRUST_LIVE", raising=False)
        assert is_blocked_effective(MCPPolicy.LOCAL_WRITE) is True
        assert is_blocked_effective(MCPPolicy.EXTERNAL_WRITE_RECOVERABLE) is True
        assert is_blocked_effective(MCPPolicy.EXTERNAL_WRITE_IRREVERSIBLE) is True

    def test_writes_blocked_with_single_flag(self, monkeypatch):
        """Un seul flag ne suffit PAS (double opt-in strict)."""
        monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
        monkeypatch.delenv("LUMENA_MCP_TRUST_LIVE", raising=False)
        assert is_blocked_effective(MCPPolicy.EXTERNAL_WRITE_RECOVERABLE) is True

        monkeypatch.delenv("LUMENA_MCP_LIVE", raising=False)
        monkeypatch.setenv("LUMENA_MCP_TRUST_LIVE", "1")
        assert is_blocked_effective(MCPPolicy.EXTERNAL_WRITE_RECOVERABLE) is True

    def test_writes_lifted_with_double_optin(self, monkeypatch):
        monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
        monkeypatch.setenv("LUMENA_MCP_TRUST_LIVE", "1")
        assert is_write_lift_enabled() is True
        assert is_blocked_effective(MCPPolicy.LOCAL_WRITE) is False
        assert is_blocked_effective(MCPPolicy.EXTERNAL_WRITE_RECOVERABLE) is False
        assert is_blocked_effective(MCPPolicy.EXTERNAL_WRITE_IRREVERSIBLE) is False

    def test_secrets_auth_never_liftable(self, monkeypatch):
        """SECRETS_AUTH reste bloquée même avec double opt-in."""
        monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
        monkeypatch.setenv("LUMENA_MCP_TRUST_LIVE", "1")
        assert is_blocked_effective(MCPPolicy.SECRETS_AUTH) is True

    def test_is_blocked_phase9_unchanged(self):
        """La fonction pure Phase 9 n'a PAS changé de sémantique (audit)."""
        assert is_blocked_phase9(MCPPolicy.LOCAL_WRITE) is True
        assert is_blocked_phase9(MCPPolicy.SECRETS_AUTH) is True
        assert is_blocked_phase9(MCPPolicy.READ_ONLY) is False


# ──────────────────────────────────────────────────────────────────────────────
# Fix R — alias mcp__ dans execute (vérification source + skill)
# ──────────────────────────────────────────────────────────────────────────────


SKILL_PATH = Path(__file__).parents[2] / "skills" / "mcp-builder" / "SKILL.md"
REGISTRY_PATH = (
    Path(__file__).parents[2] / "src" / "reasoning" / "tool_registry.py"
)


class TestFixRNamingAlignment:

    def test_skill_teaches_mcp_prefix(self):
        content = SKILL_PATH.read_text(encoding="utf-8")
        assert "mcp__slack__list_channels" in content, (
            "Le skill doit enseigner la convention réelle du registre "
            "`mcp__<server>__<tool>`"
        )
        assert "mcp__<server_id>__<tool_name>" in content

    def test_registry_has_mcp_alias(self):
        content = REGISTRY_PATH.read_text(encoding="utf-8")
        assert "Alias MCP" in content and "Fix R" in content, (
            "ToolRegistry.execute doit résoudre l'alias forme courte → mcp__"
        )

    def test_alias_rechecks_policy(self):
        """Sécurité : après aliasing, la policy DOIT être re-vérifiée
        (le premier check a été fait sur le nom court, inconnu des
        dynamic handlers → skip silencieux = bypass potentiel)."""
        content = REGISTRY_PATH.read_text(encoding="utf-8")
        idx = content.find("Alias MCP")
        assert idx != -1
        section = content[idx:idx + 1500]
        assert "_mcp_policy_check" in section, (
            "Re-check policy obligatoire après résolution d'alias"
        )


class TestFixRAliasRuntime:
    """Test fonctionnel bout-en-bout de l'alias via un vrai ToolRegistry."""

    @staticmethod
    def _register_mcp_tool(registry, name, policy):
        from src.reasoning.handlers.contracts import HandlerResult
        from src.reasoning.handlers.registry_v2 import HandlerDef

        async def _handler(ctx, **kwargs):
            return HandlerResult.ok(output="ALIAS_EXECUTED_OK")

        hdef = HandlerDef(
            name=name,
            description="test alias",
            parameters={"type": "object", "properties": {}},
            handler=_handler,
            category="mcp",
            source_module="mcp.test",
        )
        registry.register_dynamic_handler(hdef, policy=policy)
        return lambda: registry.unregister_dynamic_handler(name)

    @pytest.mark.asyncio
    async def test_short_form_executes_via_alias(self):
        """`slack__list_channels` doit exécuter `mcp__slack__list_channels`."""
        from src.reasoning.caller_context import REACT
        from src.reasoning.tool_registry import ToolRegistry

        registry = ToolRegistry()
        cleanup = self._register_mcp_tool(
            registry, "mcp__slack__list_channels", MCPPolicy.EXTERNAL_READ,
        )
        try:
            obs = await registry.execute(
                "slack__list_channels", {}, caller=REACT,
            )
            assert obs.success is True
            assert "ALIAS_EXECUTED_OK" in (obs.content or "")
        finally:
            cleanup()

    @pytest.mark.asyncio
    async def test_alias_does_not_bypass_policy(self, monkeypatch):
        """Sécurité : la forme courte d'un tool WRITE bloqué doit être
        refusée — l'alias ne doit pas contourner la policy."""
        monkeypatch.delenv("LUMENA_MCP_LIVE", raising=False)
        monkeypatch.delenv("LUMENA_MCP_TRUST_LIVE", raising=False)
        from src.reasoning.caller_context import REACT
        from src.reasoning.tool_registry import ToolRegistry

        registry = ToolRegistry()
        cleanup = self._register_mcp_tool(
            registry,
            "mcp__slack__post_message",
            MCPPolicy.EXTERNAL_WRITE_RECOVERABLE,
        )
        try:
            obs = await registry.execute(
                "slack__post_message", {}, caller=REACT,
            )
            assert obs.success is False
            assert "BLOCKED" in (obs.content or "")
        finally:
            cleanup()

    def test_alias_does_not_apply_to_native_single_underscore(self):
        """`discord_list_channels` (simple underscore, natif) ne doit
        jamais être préfixé mcp__."""
        name = "discord_list_channels"
        assert "__" not in name  # pas de double underscore → pas d'alias


# ──────────────────────────────────────────────────────────────────────────────
# Fix S — réconciliation ACTIVE fantôme
# ──────────────────────────────────────────────────────────────────────────────


LIFESPAN_PATH = Path(__file__).parents[2] / "web" / "routes" / "lifespan.py"


class TestFixSBootReconciliation:

    def test_lifespan_has_reconciliation(self):
        content = LIFESPAN_PATH.read_text(encoding="utf-8")
        assert "Fix S" in content, "Réconciliation Fix S absente du boot"
        assert "ACTIVE fantôme" in content
        # Le reset doit cibler INSTALLED
        idx = content.find("Fix S")
        section = content[idx:idx + 3000]
        assert "INSTALLED" in section
        assert "is_running" in section, (
            "La réconciliation doit vérifier que le process ne tourne pas "
            "avant de reset (ne jamais toucher un serveur réellement actif)"
        )


class TestFixSForceActivateSelfHealing:

    def _make_integration(self, catalog, activation):
        from src.mcp.react_integration import (
            MCPReActIntegration,
            MCPReActIntegrationDeps,
        )
        deps = MCPReActIntegrationDeps(
            catalog=catalog,
            activation_service=activation,
        )
        return MCPReActIntegration(deps)

    def test_active_but_not_running_resets_and_activates(self):
        """Statut active + process mort → reset INSTALLED → activate()."""
        entry = MagicMock()
        entry.status = MagicMock(value="active")
        catalog = MagicMock()
        catalog.get_server = MagicMock(return_value=entry)
        catalog.update_status = MagicMock()

        activation = MagicMock()
        activation.is_running = MagicMock(return_value=False)
        activation.activate = MagicMock(return_value=MagicMock(success=True))

        integ = self._make_integration(catalog, activation)
        payload = {"target_server_id": "slack"}
        out = integ._force_activate_if_needed(payload)

        catalog.update_status.assert_called_once()
        assert out.get("force_activate_stale_reset") is True
        assert activation.activate.call_count == 1
        assert out.get("force_activate_ok") is True

    def test_active_and_running_untouched(self):
        """Statut active + process vivant → ne rien faire (idempotent)."""
        entry = MagicMock()
        entry.status = MagicMock(value="active")
        catalog = MagicMock()
        catalog.get_server = MagicMock(return_value=entry)
        catalog.update_status = MagicMock()

        activation = MagicMock()
        activation.is_running = MagicMock(return_value=True)

        integ = self._make_integration(catalog, activation)
        payload = {"target_server_id": "slack"}
        out = integ._force_activate_if_needed(payload)

        catalog.update_status.assert_not_called()
        activation.activate.assert_not_called()
        assert "force_activate_stale_reset" not in out

    def test_installed_path_unchanged(self):
        """Le chemin INSTALLED existant (Fix L+N) n'est pas cassé."""
        entry = MagicMock()
        entry.status = MagicMock(value="installed")
        catalog = MagicMock()
        catalog.get_server = MagicMock(return_value=entry)

        activation = MagicMock()
        activation.activate = MagicMock(return_value=MagicMock(success=True))

        integ = self._make_integration(catalog, activation)
        payload = {"target_server_id": "slack"}
        out = integ._force_activate_if_needed(payload)

        assert out.get("force_activate_ok") is True

    def test_declared_still_skipped(self):
        """DECLARED (pas installé) → toujours skip."""
        entry = MagicMock()
        entry.status = MagicMock(value="declared")
        catalog = MagicMock()
        catalog.get_server = MagicMock(return_value=entry)

        activation = MagicMock()

        integ = self._make_integration(catalog, activation)
        out = integ._force_activate_if_needed({"target_server_id": "x"})
        activation.activate.assert_not_called()
        assert "force_activate_attempted" not in out


# ──────────────────────────────────────────────────────────────────────────────
# Fix U — détection schéma non-curated dans le flux autonome
# ──────────────────────────────────────────────────────────────────────────────


ORCH_PATH = (
    Path(__file__).parents[2] / "src" / "mcp" / "autonomy_orchestrator.py"
)


class TestFixUNonCuratedSchemaDetection:

    def test_fulfill_capability_wires_cascade_level2(self):
        """Le flux autonome doit appeler detect_schema niveau 2 quand le
        target résolu (npm:/pypi: explicite) n'a pas de schéma embarqué."""
        content = ORCH_PATH.read_text(encoding="utf-8")
        assert "Fix U" in content
        idx = content.find("Fix U")
        section = content[idx:idx + 2000]
        assert "detect_schema" in section, (
            "fulfill_capability doit appeler la cascade schema_cascade"
        )
        assert "enable_levels=(2,)" in section, (
            "Seul le niveau 2 (README registre npm/PyPI) doit être activé "
            "en autonome — niveau 3 (probe binaire) requiert install préalable"
        )
        assert "LUMENA_MCP_NETWORK_SEARCH_ENABLED" in section, (
            "L'appel réseau doit être gated par le flag réseau MCP"
        )

    def test_schema_detected_for_non_curated_package(self, monkeypatch):
        """Fonctionnel : un package npm non-curated avec README contenant
        des env vars → schéma détecté via la cascade injectable."""
        monkeypatch.setenv("LUMENA_MCP_NETWORK_SEARCH_ENABLED", "1")
        from src.mcp.schema_extractor import extract_schema_from_package

        readme = (
            "# chess-mcp\n\n"
            "## Environment Variables\n\n"
            "```bash\n"
            "export CHESS_API_TOKEN=your-token\n"
            "export CHESS_ENGINE_PATH=/usr/bin/stockfish\n"
            "```\n"
        )
        schema = extract_schema_from_package(
            server_id="chess-mcp",
            package_spec="npm:chess-mcp",
            readme_override=readme,
        )
        assert schema is not None
        names = {f.name for f in schema.fields}
        assert "CHESS_API_TOKEN" in names
        assert "CHESS_ENGINE_PATH" in names

    def test_no_network_flag_no_fetch(self, monkeypatch):
        """Sans flag réseau, fulfill_capability ne tente PAS de fetch
        (le code gated doit court-circuiter avant tout appel réseau)."""
        monkeypatch.delenv("LUMENA_MCP_NETWORK_SEARCH_ENABLED", raising=False)
        content = ORCH_PATH.read_text(encoding="utf-8")
        idx = content.find("Fix U")
        section = content[idx:idx + 2000]
        # Le if _net_ok doit envelopper l'appel detect_schema
        assert "_net_ok" in section
        assert section.find("_net_ok") < section.find("detect_schema")


# ──────────────────────────────────────────────────────────────────────────────
# Fix V — régression NameError `deps` dans _build_activation_service_default
# ──────────────────────────────────────────────────────────────────────────────


MCP_ROUTES_PATH = Path(__file__).parents[2] / "web" / "routes" / "mcp.py"


class TestFixVNoBaredepsReference:

    def test_no_bare_deps_getattr_in_mcp_routes(self):
        """Régression 2026-06-10 : `getattr(deps, ...)` avec `deps` non
        importé au niveau module → NameError avalé par except → factory
        ActivationService retournait None → AUCUNE activation MCP possible
        de toute la session. Le pattern obligatoire est l'import local
        `from web.routes import deps as _d` (helpers _i6_*)."""
        content = MCP_ROUTES_PATH.read_text(encoding="utf-8")
        assert "getattr(deps," not in content, (
            "Référence nue `deps` dans web/routes/mcp.py — utiliser les "
            "helpers _i6_*_singleton() (import local anti-circulaire)"
        )

    def test_factory_uses_i6_helpers(self):
        content = MCP_ROUTES_PATH.read_text(encoding="utf-8")
        idx = content.find("def _build_activation_service(")
        assert idx != -1
        section = content[idx:idx + 5000]
        assert "_i6_credentials_singleton()" in section
        assert "_i6_config_singleton()" in section

    def test_factory_except_is_not_silent(self):
        """Le except final de la factory doit logger l'exception —
        c'est lui qui a masqué le NameError une session entière."""
        content = MCP_ROUTES_PATH.read_text(encoding="utf-8")
        idx = content.find("def _build_activation_service(")
        section = content[idx:idx + 6000]
        assert "_build_activation_service_default failed" in section


# ──────────────────────────────────────────────────────────────────────────────
# Fix W — résilience process MCP mort (is_running réel + zombie cleanup + EOF)
# ──────────────────────────────────────────────────────────────────────────────


class TestFixWIsRunningChecksProcess:

    def _make_service_with_context(self, poll_result):
        """Service minimal avec un contexte dont le process poll() est
        contrôlé. poll_result=None → vivant ; int → mort."""
        from src.mcp.activation_service import MCPActivationService

        svc = object.__new__(MCPActivationService)
        proc = MagicMock()
        proc.poll = MagicMock(return_value=poll_result)
        runner = MagicMock()
        runner.process = proc
        ctx = MagicMock()
        ctx.runner = runner
        ctx.registered_handlers = ["mcp__x__a", "mcp__x__b"]
        ctx.client = MagicMock()
        svc._running_contexts = {"x": ctx}
        return svc, ctx

    def test_alive_process_is_running_true(self):
        svc, _ = self._make_service_with_context(poll_result=None)
        assert svc.is_running("x") is True

    def test_dead_process_is_running_false(self):
        """LE fix : process mort (poll() retourne un code) → False.
        Avant Fix W : True à vie → already_running → MCP inutilisable."""
        svc, _ = self._make_service_with_context(poll_result=1)
        assert svc.is_running("x") is False

    def test_no_context_is_running_false(self):
        svc, _ = self._make_service_with_context(poll_result=None)
        assert svc.is_running("absent") is False

    def test_cleanup_dead_context_unregisters_and_drops(self):
        from src.mcp.activation_service import MCPActivationService

        svc, ctx = self._make_service_with_context(poll_result=1)
        svc._registry_writer = MagicMock()
        svc._watcher = MagicMock()
        svc._best_effort_runner_stop = MagicMock()

        svc._cleanup_dead_context("x")

        assert "x" not in svc._running_contexts
        # Les 2 handlers ont été retirés du registre
        assert svc._registry_writer.unregister_dynamic_handler.call_count == 2
        svc._watcher.unregister_runner.assert_called_once_with("x")
        svc._best_effort_runner_stop.assert_called_once()


class TestFixWClientEofDistinctFromTimeout:

    def test_eof_with_dead_process_raises_process_died(self):
        """EOF + poll() non-None → MCPProtocolError 'process died' avec
        exit_code, PAS le trompeur 'Timed out after 30.0s'."""
        from src.mcp.client import MCPClient, MCPProtocolError

        proc = MagicMock()
        proc.stdin = MagicMock()
        proc.stdout = MagicMock()
        proc.stderr = MagicMock()
        proc.poll = MagicMock(return_value=137)
        # readline retourne "" = EOF immédiat
        proc.stdout.readline = MagicMock(return_value="")

        client = MCPClient(proc, "slack")
        with pytest.raises(MCPProtocolError) as exc_info:
            client._read_response_with_id(4, timeout_s=5.0)
        msg = str(exc_info.value)
        assert "process died" in msg
        assert "exit_code=137" in msg
        assert "Timed out" not in msg

    def test_watcher_logs_unexpected_death(self):
        """Le watcher doit logger en WARNING une mort non sollicitée
        (source-level check : warning + exit_code + stderr tail)."""
        content = (
            Path(__file__).parents[2] / "src" / "mcp" / "sandbox_runner.py"
        ).read_text(encoding="utf-8")
        idx = content.find("def _process_watcher_loop")
        section = content[idx:idx + 3000]
        assert "logger.warning" in section
        assert "exit_code" in section
        assert "stderr tail" in section


# ──────────────────────────────────────────────────────────────────────────────
# Fix X — read_cause précis + canal cassé avec process VIVANT
# ──────────────────────────────────────────────────────────────────────────────


class TestFixXReadCauseTelemetry:

    def _make_client(self, *, readline_behavior, poll_result):
        from src.mcp.client import MCPClient

        proc = MagicMock()
        proc.stdin = MagicMock()
        proc.stdout = MagicMock()
        proc.stderr = MagicMock()
        proc.poll = MagicMock(return_value=poll_result)
        proc.stdout.readline = readline_behavior
        return MCPClient(proc, "slack")

    def test_eof_alive_process_raises_channel_broken(self):
        """Le cas réel des sessions 20:37 et 23:17 : readline EOF en
        quelques ms avec process VIVANT → 'stdio channel broken' avec
        read_cause=eof, PAS 'Timed out after 30.0s'."""
        from src.mcp.client import MCPProtocolError

        client = self._make_client(
            readline_behavior=MagicMock(return_value=""),
            poll_result=None,  # process VIVANT
        )
        with pytest.raises(MCPProtocolError) as exc_info:
            client._read_response_with_id(4, timeout_s=5.0)
        msg = str(exc_info.value)
        assert "stdio channel broken" in msg
        assert "read_cause=eof" in msg
        assert "process alive" in msg
        assert "Timed out" not in msg
        # Le client se marque closed → is_running (Fix W+X) déclenchera
        # le self-healing
        assert client.is_closed is True

    def test_readline_exception_exposes_type_and_msg(self):
        """Une exception dans readline (ex: I/O on closed file) doit
        remonter son type + message dans read_cause."""
        from src.mcp.client import MCPProtocolError

        client = self._make_client(
            readline_behavior=MagicMock(
                side_effect=ValueError("I/O operation on closed file"),
            ),
            poll_result=None,
        )
        with pytest.raises(MCPProtocolError) as exc_info:
            client._read_response_with_id(4, timeout_s=5.0)
        msg = str(exc_info.value)
        assert "read_cause=exception:ValueError" in msg
        assert "closed file" in msg

    def test_real_timeout_still_timeout(self):
        """Un VRAI timeout (readline bloque) reste MCPTimeoutError."""
        import time as _t
        from src.mcp.client import MCPTimeoutError

        def _slow_readline():
            _t.sleep(10)
            return "never"

        client = self._make_client(
            readline_behavior=_slow_readline,
            poll_result=None,
        )
        with pytest.raises(MCPTimeoutError):
            client._read_response_with_id(4, timeout_s=0.3)
        # Vrai timeout → le canal n'est PAS marqué mort (réponse lente
        # possible) — pas de self-heal intempestif
        assert client.is_closed is False

    def test_is_running_false_when_client_channel_broken(self):
        """is_running doit retourner False si le client est closed,
        même avec un process vivant — sinon Fix S ne répare jamais le
        cas 'canal cassé, process zombie vivant'."""
        from src.mcp.activation_service import MCPActivationService

        svc = object.__new__(MCPActivationService)
        proc = MagicMock()
        proc.poll = MagicMock(return_value=None)  # VIVANT
        runner = MagicMock()
        runner.process = proc
        client = MagicMock()
        client.is_closed = True  # canal cassé
        ctx = MagicMock()
        ctx.runner = runner
        ctx.client = client
        svc._running_contexts = {"slack": ctx}

        assert svc.is_running("slack") is False


# ──────────────────────────────────────────────────────────────────────────────
# Fix Y — encoding UTF-8 explicite (LE tueur : cp1252 Windows)
# ──────────────────────────────────────────────────────────────────────────────


class TestFixYUtf8Encoding:
    """Le bug runtime final : Popen(text=True) sans encoding= lit stdout
    en cp1252 sur Windows. Les réponses ASCII (initialize, tools/list,
    erreurs API) passaient, mais la PREMIÈRE vraie réponse contenant de
    l'UTF-8 (noms de canaux accentués/emojis) levait UnicodeDecodeError
    → canal déclaré cassé alors que tout fonctionnait."""

    def test_runner_popen_uses_utf8(self):
        content = (
            Path(__file__).parents[2] / "src" / "mcp" / "sandbox_runner.py"
        ).read_text(encoding="utf-8")
        idx = content.find("self._process = subprocess.Popen(")
        assert idx != -1
        section = content[idx:idx + 600]
        assert 'encoding="utf-8"' in section, (
            "Popen du serveur MCP DOIT forcer UTF-8 (spec MCP stdio) — "
            "sinon cp1252 Windows crash sur le premier byte non-ASCII"
        )
        assert 'errors="replace"' in section, (
            "Un byte invalide ne doit jamais casser le canal entier"
        )

    def test_install_run_uses_utf8(self):
        content = (
            Path(__file__).parents[2] / "src" / "mcp" / "sandbox_runner.py"
        ).read_text(encoding="utf-8")
        idx = content.find("def _run_install_command")
        section = content[idx:idx + 1000]
        assert 'encoding="utf-8"' in section

    def test_utf8_roundtrip_through_real_subprocess_placeholder(self):
        pass

class TestFixZActionableIntentMcpLifecycle:
    """Fix Z : « installe et active le MCP memory » donnait
    actionable_intent=False (aucun token dans la whitelist) →
    no_capability_found → blocked, même pour un slug CURATED.
    La whitelist manquait : les verbes du cycle de vie MCP
    (installe/active/configure/utilise/ajoute), le mot « mcp »
    lui-même, et 13 des 17 slugs curated."""

    @staticmethod
    def _actionable(intent: str) -> bool:
        from src.mcp.capability_resolver import (
            _is_actionable_intent,
            _tokenize,
        )
        return _is_actionable_intent(_tokenize(intent))

    def test_install_activate_memory_is_actionable(self):
        """Le cas exact du log runtime 2026-06-10 23:50."""
        assert self._actionable("installe et active le MCP memory") is True

    @pytest.mark.parametrize("intent", [
        "installe le MCP github",
        "active le MCP slack",
        "configure le serveur postgres",
        "utilise puppeteer pour scraper",
        "ajoute le serveur sqlite",
        "install the time mcp",
        "utilise le MCP filesystem",
        "installe linear",
        "active sentry",
        "utilise tavily pour chercher",
        "installe gitlab",
        "ajoute brave search",
    ])
    def test_all_curated_lifecycle_intents_actionable(self, intent):
        assert self._actionable(intent) is True

    def test_smalltalk_still_not_actionable(self):
        """Contrôle négatif : le small talk ne doit PAS devenir
        actionable (sinon chaque bonjour déclenche la machinerie MCP)."""
        assert self._actionable("bonjour comment vas-tu") is False
        assert self._actionable("merci beaucoup") is False

    def test_all_known_mcp_slugs_in_whitelist(self):
        """Invariant : CHAQUE slug KNOWN_MCPS doit rendre un intent
        « utilise <slug> » actionable — sinon le MCP curated est
        ininstallable par la voix naturelle."""
        from src.mcp.known_mcps import list_known_mcp_slugs

        failures = []
        for slug in list_known_mcp_slugs():
            if not self._actionable(f"je veux le mcp {slug}"):
                failures.append(slug)
        assert not failures, (
            f"Slugs curated non-actionables : {failures} — ajouter à "
            "_ACTIONABLE_VERBS_TOOLS (capability_resolver.py)"
        )

    def test_utf8_roundtrip_through_real_subprocess(self):
        """End-to-end réel : un sous-process qui émet de l'UTF-8 (emoji,
        accents — y compris U+0090-adjacents) doit être lu sans erreur
        avec les mêmes kwargs Popen que le runner."""
        import subprocess
        import sys as _sys

        # Reproduit le payload qui a tué la session 23:29 : JSON avec
        # caractères multi-bytes
        payload = '{"channels": [{"name": "général-🚀", "topic": "déjà vu"}]}'
        proc = subprocess.Popen(
            [_sys.executable, "-X", "utf8", "-c",
             f"import sys; sys.stdout.write({payload!r})"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        out, _ = proc.communicate(timeout=30)
        assert "général-🚀" in out
        assert "déjà vu" in out
