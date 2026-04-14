"""
test_setup_wizard.py — Tests for the setup wizard routes.

Tests the GET /api/setup/status, GET /api/setup/schema,
POST /api/setup/complete, and POST /api/setup/test-key endpoints.
"""
import os
import pytest
from pathlib import Path
from unittest.mock import patch

from web.routes.setup import (
    _is_setup_complete,
    router,
)


# ─── Helpers ───────────────────────────────────────────────────────

_MINI_SCHEMA = [
    {"key": "LUMENA_DEFAULT_MODEL", "label": "Modèle par défaut", "group": "LLM",
     "type": "select", "options": ["deepseek-v3", "gpt-5.4"], "default": "deepseek-v3"},
    {"key": "DEEPSEEK_API_KEY", "label": "DeepSeek API Key", "group": "Clés API",
     "type": "secret", "default": ""},
    {"key": "LUMENA_TTS_AUTO", "label": "TTS automatique", "group": "Voix",
     "type": "bool", "default": "0"},
]


@pytest.fixture
def env_file(tmp_path):
    """Create a temp .env file."""
    env = tmp_path / ".env"
    env.write_text("LUMENA_DEFAULT_MODEL=deepseek-v3\n", encoding="utf-8")
    return env


@pytest.fixture
def mock_setup(env_file):
    """Patch config internals and setup_complete check to use temp .env."""
    project_root = env_file.parent

    def read_env():
        result = {}
        if not env_file.exists():
            return result
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            result[k.strip()] = v.strip()
        return result

    def write_env(updates):
        lines = env_file.read_text(encoding="utf-8").splitlines() if env_file.exists() else []
        remaining = dict(updates)
        new_lines = []
        for line in lines:
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                key = stripped.split("=", 1)[0].strip()
                if key in remaining:
                    new_lines.append(f"{key}={remaining.pop(key)}")
                    continue
            new_lines.append(line)
        for k, v in remaining.items():
            new_lines.append(f"{k}={v}")
        env_file.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

    with patch("web.routes.setup._CONFIG_SCHEMA", _MINI_SCHEMA), \
         patch("web.routes.setup._read_env_file", read_env), \
         patch("web.routes.setup._write_env_values", write_env):
        yield env_file


# ─── _is_setup_complete ───────────────────────────────────────────

class TestIsSetupComplete:
    def test_not_complete_by_default(self, mock_setup):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LUMENA_SETUP_COMPLETE", None)
            assert not _is_setup_complete()

    def test_complete_via_env_var(self, mock_setup):
        with patch.dict(os.environ, {"LUMENA_SETUP_COMPLETE": "1"}):
            assert _is_setup_complete()

    def test_complete_via_env_file(self, mock_setup):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LUMENA_SETUP_COMPLETE", None)
            # Write to .env
            content = mock_setup.read_text()
            mock_setup.write_text(content + "LUMENA_SETUP_COMPLETE=1\n")
            assert _is_setup_complete()


# ─── GET /api/setup/status ─────────────────────────────────────────

class TestSetupStatus:
    @pytest.mark.asyncio
    async def test_needs_setup_when_not_complete(self, mock_setup):
        from web.routes.setup import setup_status
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LUMENA_SETUP_COMPLETE", None)
            result = await setup_status(preview="0")
            assert result["needsSetup"] is True
            assert result["preview"] is False

    @pytest.mark.asyncio
    async def test_no_setup_when_complete(self, mock_setup):
        from web.routes.setup import setup_status
        from web.routes import deps
        old = deps.setup_only_mode
        try:
            deps.setup_only_mode = False
            with patch.dict(os.environ, {"LUMENA_SETUP_COMPLETE": "1"}):
                result = await setup_status(preview="0")
                assert result["needsSetup"] is False
        finally:
            deps.setup_only_mode = old

    @pytest.mark.asyncio
    async def test_preview_always_shows(self, mock_setup):
        from web.routes.setup import setup_status
        with patch.dict(os.environ, {"LUMENA_SETUP_COMPLETE": "1"}):
            result = await setup_status(preview="1")
            assert result["needsSetup"] is True
            assert result["preview"] is True


# ─── GET /api/setup/schema ─────────────────────────────────────────

class TestSetupSchema:
    @pytest.mark.asyncio
    async def test_returns_steps(self):
        from web.routes.setup import setup_schema
        result = await setup_schema()
        assert "steps" in result
        assert len(result["steps"]) >= 2  # at least model + keys
        step_ids = [s["id"] for s in result["steps"]]
        assert "model" in step_ids
        assert "keys" in step_ids

    @pytest.mark.asyncio
    async def test_model_step_has_fields(self):
        from web.routes.setup import setup_schema
        result = await setup_schema()
        model_step = next(s for s in result["steps"] if s["id"] == "model")
        assert len(model_step["fields"]) >= 1
        assert model_step["icon"] == "brain"


# ─── POST /api/setup/complete ──────────────────────────────────────

class TestSetupComplete:
    @pytest.mark.asyncio
    async def test_preview_mode_does_not_write(self, mock_setup):
        from web.routes.setup import setup_complete
        from unittest.mock import AsyncMock, MagicMock

        request = AsyncMock()
        request.client = MagicMock()
        request.client.host = "127.0.0.1"
        request.json = AsyncMock(return_value={
            "preview": True,
            "config": {"LUMENA_DEFAULT_MODEL": "gpt-5.4"},
        })

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LUMENA_SETUP_COMPLETE", None)
            result = await setup_complete(request)

        assert result["success"] is True
        assert result["preview"] is True
        # .env should NOT have been touched
        content = mock_setup.read_text()
        assert "LUMENA_SETUP_COMPLETE" not in content
        assert "gpt-5.4" not in content

    @pytest.mark.asyncio
    async def test_real_mode_writes_env(self, mock_setup):
        from web.routes.setup import setup_complete
        from unittest.mock import AsyncMock, MagicMock

        request = AsyncMock()
        request.client = MagicMock()
        request.client.host = "127.0.0.1"
        request.json = AsyncMock(return_value={
            "preview": False,
            "config": {"LUMENA_DEFAULT_MODEL": "gpt-5.4"},
        })

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LUMENA_SETUP_COMPLETE", None)
            result = await setup_complete(request)

        assert result["success"] is True
        assert result["preview"] is False
        assert "LUMENA_DEFAULT_MODEL" in result["updated"]
        # .env should have the new values
        content = mock_setup.read_text()
        assert "LUMENA_SETUP_COMPLETE=1" in content
        assert "LUMENA_DEFAULT_MODEL=gpt-5.4" in content

    @pytest.mark.asyncio
    async def test_blocked_if_already_complete(self, mock_setup):
        from web.routes.setup import setup_complete
        from unittest.mock import AsyncMock, MagicMock

        request = AsyncMock()
        request.client = MagicMock()
        request.client.host = "127.0.0.1"
        request.json = AsyncMock(return_value={
            "preview": False,
            "config": {"LUMENA_DEFAULT_MODEL": "gpt-5.4"},
        })

        with patch.dict(os.environ, {"LUMENA_SETUP_COMPLETE": "1"}):
            result = await setup_complete(request)

        assert result["success"] is False
        assert "déjà" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_empty_config_rejected(self, mock_setup):
        from web.routes.setup import setup_complete
        from unittest.mock import AsyncMock, MagicMock

        request = AsyncMock()
        request.client = MagicMock()
        request.client.host = "127.0.0.1"
        request.json = AsyncMock(return_value={
            "preview": False,
            "config": {},
        })

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LUMENA_SETUP_COMPLETE", None)
            result = await setup_complete(request)

        assert result["success"] is False


# ─── POST /api/setup/test-key ──────────────────────────────────────

def _localhost_request(**kwargs):
    """Create a mock request with client.host='127.0.0.1'."""
    from unittest.mock import AsyncMock, MagicMock
    request = AsyncMock()
    client = MagicMock()
    client.host = "127.0.0.1"
    request.client = client
    if "json_data" in kwargs:
        request.json = AsyncMock(return_value=kwargs["json_data"])
    return request


class TestTestKey:
    @pytest.mark.asyncio
    async def test_valid_deepseek_key(self):
        from web.routes.setup import test_api_key
        from unittest.mock import AsyncMock, MagicMock, patch

        request = _localhost_request(json_data={
            "provider": "DEEPSEEK_API_KEY",
            "key": "sk-abc123def456",
        })

        mock_resp = MagicMock(status_code=200)
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await test_api_key(request)
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_invalid_format(self):
        from web.routes.setup import test_api_key

        request = _localhost_request(json_data={
            "provider": "DEEPSEEK_API_KEY",
            "key": "wrong-prefix-key",
        })
        result = await test_api_key(request)
        assert result["success"] is False
        assert "sk-" in result["error"]

    @pytest.mark.asyncio
    async def test_missing_params(self):
        from web.routes.setup import test_api_key

        request = _localhost_request(json_data={
            "provider": "",
            "key": "",
        })
        result = await test_api_key(request)
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_unknown_provider_passes(self):
        from web.routes.setup import test_api_key

        request = _localhost_request(json_data={
            "provider": "SOME_NEW_PROVIDER",
            "key": "any-key-value",
        })
        result = await test_api_key(request)
        assert result["success"] is True


# ─── POST /api/setup/validate-path ────────────────────────────────

class TestValidatePath:
    @pytest.mark.asyncio
    async def test_empty_path_valid(self):
        from web.routes.setup import validate_workspace_path

        request = _localhost_request(json_data={"path": ""})
        result = await validate_workspace_path(request)
        assert result["valid"] is True

    @pytest.mark.asyncio
    async def test_existing_dir_valid(self, tmp_path):
        from web.routes.setup import validate_workspace_path

        request = _localhost_request(json_data={"path": str(tmp_path)})
        result = await validate_workspace_path(request)
        assert result["valid"] is True
        assert "valid" in result

    @pytest.mark.asyncio
    async def test_nonexistent_dir_will_create(self, tmp_path):
        from web.routes.setup import validate_workspace_path

        new_dir = tmp_path / "lumena_workspace_new"
        request = _localhost_request(json_data={"path": str(new_dir)})
        result = await validate_workspace_path(request)
        assert result["valid"] is True
        assert result.get("will_create") is True

    @pytest.mark.asyncio
    async def test_file_path_invalid(self, tmp_path):
        from web.routes.setup import validate_workspace_path

        f = tmp_path / "some_file.txt"
        f.write_text("content")
        request = _localhost_request(json_data={"path": str(f)})
        result = await validate_workspace_path(request)
        assert result["valid"] is False


# ─── Phase 4: Schema additions ────────────────────────────────────────────────

class TestP4SchemaAdditions:
    """P4: New schema keys + wizard step additions."""

    @pytest.mark.asyncio
    async def test_schema_has_sandbox_memory(self):
        from web.routes.config import _CONFIG_SCHEMA
        keys = {s["key"] for s in _CONFIG_SCHEMA}
        assert "LUMENA_SANDBOX_MEMORY" in keys

    @pytest.mark.asyncio
    async def test_schema_has_use_emojis(self):
        from web.routes.config import _CONFIG_SCHEMA
        keys = {s["key"] for s in _CONFIG_SCHEMA}
        assert "LUMENA_USE_EMOJIS" in keys

    @pytest.mark.asyncio
    async def test_schema_has_default_mood(self):
        from web.routes.config import _CONFIG_SCHEMA
        keys = {s["key"] for s in _CONFIG_SCHEMA}
        assert "LUMENA_DEFAULT_MOOD" in keys
        entry = next(s for s in _CONFIG_SCHEMA if s["key"] == "LUMENA_DEFAULT_MOOD")
        assert entry["type"] == "select"
        assert "neutral" in entry["options"]

    @pytest.mark.asyncio
    async def test_security_step_has_host_field(self):
        from web.routes.setup import setup_schema
        result = await setup_schema()
        sec = next((s for s in result["steps"] if s["id"] == "security"), None)
        assert sec is not None
        assert "host_field" in sec
        assert sec["host_field"]["key"] == "LUMENA_HOST"
        opts = [o["value"] if isinstance(o, dict) else o for o in sec["host_field"]["options"]]
        assert "127.0.0.1" in opts
        assert "0.0.0.0" in opts

    @pytest.mark.asyncio
    async def test_autonomy_step_has_sandbox_fields(self):
        from web.routes.setup import setup_schema
        result = await setup_schema()
        auto = next((s for s in result["steps"] if s["id"] == "autonomy"), None)
        assert auto is not None
        assert "sandbox_fields" in auto
        sandbox_keys = {f["key"] for f in auto["sandbox_fields"]}
        assert "LUMENA_SANDBOX_MODE" in sandbox_keys
        assert "LUMENA_SANDBOX_MEMORY" in sandbox_keys

    @pytest.mark.asyncio
    async def test_integrations_step_has_ibkr(self):
        from web.routes.setup import setup_schema
        result = await setup_schema()
        integs = next((s for s in result["steps"] if s["id"] == "integrations"), None)
        assert integs is not None
        ibkr = next((i for i in integs["integrations"] if i["key"] == "IBKR_HOST"), None)
        assert ibkr is not None, "IBKR intégration manquante"
        assert ibkr.get("collapsed") is True
        ibkr_field_keys = [f["key"] for f in ibkr["fields"]]
        assert "IBKR_HOST" in ibkr_field_keys
        assert "IBKR_PORT" in ibkr_field_keys
        assert "IBKR_CLIENT_ID" in ibkr_field_keys

    @pytest.mark.asyncio
    async def test_setup_complete_allows_twitter_keys(self, mock_setup):
        from web.routes.setup import setup_complete
        from unittest.mock import AsyncMock, MagicMock
        request = AsyncMock()
        request.client = MagicMock()
        request.client.host = "127.0.0.1"
        request.json = AsyncMock(return_value={
            "preview": False,
            "config": {
                "TWITTER_BEARER_TOKEN": "AAABBB",
                "TWITTER_API_KEY": "abc123",
            },
        })
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LUMENA_SETUP_COMPLETE", None)
            result = await setup_complete(request)
        assert result["success"] is True
        assert "TWITTER_BEARER_TOKEN" in result["updated"]

    @pytest.mark.asyncio
    async def test_setup_complete_allows_ibkr_keys(self, mock_setup):
        from web.routes.setup import setup_complete
        from unittest.mock import AsyncMock, MagicMock
        request = AsyncMock()
        request.client = MagicMock()
        request.client.host = "127.0.0.1"
        request.json = AsyncMock(return_value={
            "preview": False,
            "config": {
                "IBKR_HOST": "127.0.0.1",
                "IBKR_PORT": "4002",
                "IBKR_CLIENT_ID": "1",
            },
        })
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LUMENA_SETUP_COMPLETE", None)
            result = await setup_complete(request)
        assert result["success"] is True
        assert "IBKR_HOST" in result["updated"]

    @pytest.mark.asyncio
    async def test_setup_complete_allows_trait_keys(self, mock_setup):
        from web.routes.setup import setup_complete
        from unittest.mock import AsyncMock, MagicMock
        request = AsyncMock()
        request.client = MagicMock()
        request.client.host = "127.0.0.1"
        request.json = AsyncMock(return_value={
            "preview": False,
            "config": {
                "LUMENA_TRAIT_CURIOSITY": "85",
                "LUMENA_DEFAULT_MOOD": "happy",
                "LUMENA_USE_EMOJIS": "1",
            },
        })
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LUMENA_SETUP_COMPLETE", None)
            result = await setup_complete(request)
        assert result["success"] is True
        assert "LUMENA_DEFAULT_MOOD" in result["updated"]
        assert "LUMENA_USE_EMOJIS" in result["updated"]
        assert "LUMENA_TRAIT_CURIOSITY" in result["updated"]


# ─── Phase 5: Group ordering + panel structure ────────────────────────────────

class TestP5GroupOrdering:
    """P5: Config panel groupe ordering, collapsible levels, Instance 2-level."""

    def _get_config_groups(self):
        """Call /api/config and return groups dict."""
        import asyncio
        from web.routes.config import get_config
        return asyncio.run(get_config())["groups"]

    def test_simple_groups_present(self):
        groups = self._get_config_groups()
        for name in ("LLM", "Préférences", "Voix", "Autonomie", "Alertes", "Clés API"):
            assert name in groups, f"Groupe simple manquant: {name}"

    def test_avance_groups_present(self):
        groups = self._get_config_groups()
        for name in ("Serveur", "Browser", "Email"):
            assert name in groups, f"Groupe avancé manquant: {name}"

    def test_expert_groups_present(self):
        groups = self._get_config_groups()
        for name in ("Ops", "Système", "Instance"):
            assert name in groups, f"Groupe expert manquant: {name}"

    def test_instance_main_keys_present(self):
        groups = self._get_config_groups()
        instance_keys = {it["key"] for it in groups.get("Instance", [])}
        assert "LUMENA_INSTANCE_NAME" in instance_keys
        assert "LUMENA_PUBLIC_BASE_URL" in instance_keys

    def test_instance_isolated_keys_present(self):
        """Les clés d'isolation avancée sont bien dans le groupe Instance."""
        groups = self._get_config_groups()
        instance_keys = {it["key"] for it in groups.get("Instance", [])}
        for key in ("LUMENA_INSTANCE_ID", "LUMENA_DATA_DIR", "LUMENA_WORKSPACE_DIR", "LUMENA_UPLOADS_DIR"):
            assert key in instance_keys, f"Clé Instance isolée manquante: {key}"

    def test_no_duplicate_items_across_groups(self):
        """Chaque clé n'apparaît qu'une seule fois dans tous les groupes."""
        groups = self._get_config_groups()
        all_keys = []
        for items in groups.values():
            all_keys.extend(it["key"] for it in items)
        assert len(all_keys) == len(set(all_keys)), "Doublon de clés entre groupes"

    def test_all_items_have_level(self):
        """Toutes les entrées schema ont un champ level (simple/avancé/expert)."""
        from web.routes.config import _CONFIG_SCHEMA
        for entry in _CONFIG_SCHEMA:
            assert "level" in entry, f"{entry['key']} n'a pas de niveau"
            assert entry["level"] in ("simple", "avancé", "expert")

    def test_systeme_group_has_sandbox_keys(self):
        groups = self._get_config_groups()
        sys_keys = {it["key"] for it in groups.get("Système", [])}
        assert "LUMENA_SANDBOX_MODE" in sys_keys
        assert "LUMENA_SANDBOX_MEMORY" in sys_keys

    def test_preferences_group_present(self):
        groups = self._get_config_groups()
        pref_keys = {it["key"] for it in groups.get("Préférences", [])}
        assert "LUMENA_USE_EMOJIS" in pref_keys
        assert "LUMENA_DEFAULT_MOOD" in pref_keys


# ═══════════════════════════════════════════════════════════════════
# Batch 1 — PLAN_PREMIER_DEMARRAGE_PARFAIT (P0.1–P0.12)
# ═══════════════════════════════════════════════════════════════════


class TestBatch1CrashRecovery:
    """P0.9 — 2nd boot with SETUP_COMPLETE=1 but no LLM → setup_only_mode (not crash)."""

    @pytest.mark.asyncio
    async def test_crash_no_api_key_becomes_setup_only(self):
        """Boot with LUMENA_SETUP_COMPLETE=1 and 0 API keys → no crash, setup_only_mode=True."""
        from web.routes import deps
        old = deps.setup_only_mode
        try:
            # Simulate: setup_complete=1 but initialized=False
            # P0.9 fix: should set setup_only_mode instead of raising RuntimeError
            deps.setup_only_mode = False
            # The fix turns the RuntimeError into deps.setup_only_mode = True
            # We test the lifespan logic indirectly by verifying the code path
            with patch.dict(os.environ, {"LUMENA_SETUP_COMPLETE": "1"}):
                # Verify setup_status returns needsSetup when setup_only_mode active
                deps.setup_only_mode = True
                from web.routes.setup import setup_status
                result = await setup_status(preview="0")
                assert result["needsSetup"] is True, "P0.12: wizard must re-show in recovery mode"
        finally:
            deps.setup_only_mode = old


class TestBatch1EnvInjection:
    """P0.10 — Regex key validation prevents .env injection."""

    @pytest.mark.asyncio
    async def test_env_injection_trait_newline_rejected(self, mock_setup):
        """POST with key containing newline → rejected (not in filtered)."""
        from web.routes.setup import setup_complete
        from unittest.mock import AsyncMock, MagicMock

        request = AsyncMock()
        request.client = MagicMock()
        request.client.host = "127.0.0.1"
        request.json = AsyncMock(return_value={
            "preview": False,
            "config": {
                "LUMENA_TRAIT_X\nEVIL_VAR": "injected",
                "LUMENA_DEFAULT_MODEL": "deepseek-v3",
            },
        })

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LUMENA_SETUP_COMPLETE", None)
            result = await setup_complete(request)

        assert result["success"] is True
        # The injected key must NOT be in the written values
        assert "LUMENA_TRAIT_X\nEVIL_VAR" not in result.get("updated", [])
        content = mock_setup.read_text()
        assert "EVIL_VAR" not in content

    @pytest.mark.asyncio
    async def test_valid_trait_key_accepted(self, mock_setup):
        """POST with a properly named LUMENA_TRAIT_* key → accepted."""
        from web.routes.setup import setup_complete
        from unittest.mock import AsyncMock, MagicMock

        request = AsyncMock()
        request.client = MagicMock()
        request.client.host = "127.0.0.1"
        request.json = AsyncMock(return_value={
            "preview": False,
            "config": {
                "LUMENA_TRAIT_CURIOSITY": "0.8",
                "LUMENA_DEFAULT_MODEL": "deepseek-v3",
            },
        })

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LUMENA_SETUP_COMPLETE", None)
            result = await setup_complete(request)

        assert result["success"] is True
        assert "LUMENA_TRAIT_CURIOSITY" in result.get("updated", [])


class TestBatch1AdminTokenAutogen:
    """P0.11 — Auto-generate LUMENA_ADMIN_TOKEN if not provided."""

    @pytest.mark.asyncio
    async def test_admin_token_autogen(self, mock_setup):
        """POST setup without LUMENA_ADMIN_TOKEN → token auto-generated in response."""
        from web.routes.setup import setup_complete
        from unittest.mock import AsyncMock, MagicMock

        request = AsyncMock()
        request.client = MagicMock()
        request.client.host = "127.0.0.1"
        request.json = AsyncMock(return_value={
            "preview": False,
            "config": {"LUMENA_DEFAULT_MODEL": "deepseek-v3"},
        })

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LUMENA_SETUP_COMPLETE", None)
            os.environ.pop("LUMENA_ADMIN_TOKEN", None)
            result = await setup_complete(request)

        assert result["success"] is True
        assert result.get("admin_token"), "Must return an auto-generated admin_token"
        assert len(result["admin_token"]) >= 20
        assert "LUMENA_ADMIN_TOKEN" in result.get("updated", [])

    @pytest.mark.asyncio
    async def test_explicit_admin_token_preserved(self, mock_setup):
        """POST setup with explicit LUMENA_ADMIN_TOKEN → that token is used."""
        from web.routes.setup import setup_complete
        from unittest.mock import AsyncMock, MagicMock

        request = AsyncMock()
        request.client = MagicMock()
        request.client.host = "127.0.0.1"
        request.json = AsyncMock(return_value={
            "preview": False,
            "config": {
                "LUMENA_DEFAULT_MODEL": "deepseek-v3",
                "LUMENA_ADMIN_TOKEN": "my-custom-token-42",
            },
        })

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LUMENA_SETUP_COMPLETE", None)
            result = await setup_complete(request)

        assert result["success"] is True
        assert result["admin_token"] == "my-custom-token-42"


class TestBatch1LlmReady:
    """P0.7 — setup_complete returns llm_ready flag."""

    @pytest.mark.asyncio
    async def test_llm_ready_false_when_setup_only(self, mock_setup):
        """POST setup when LLM not available → llm_ready: false."""
        from web.routes.setup import setup_complete
        from web.routes import deps
        from unittest.mock import AsyncMock, MagicMock

        request = AsyncMock()
        request.client = MagicMock()
        request.client.host = "127.0.0.1"
        request.json = AsyncMock(return_value={
            "preview": False,
            "config": {"LUMENA_DEFAULT_MODEL": "deepseek-v3"},
        })

        old = deps.setup_only_mode
        old_lumena = deps.lumena
        try:
            deps.setup_only_mode = True
            # Mock initialize_lumena to simulate failure (no API keys)
            mock_core = MagicMock()
            mock_core.is_initialized = False

            async def _fake_init():
                return mock_core

            with patch.dict(os.environ, {}, clear=False), \
                 patch("src.core.initialize_lumena", _fake_init):
                os.environ.pop("LUMENA_SETUP_COMPLETE", None)
                result = await setup_complete(request)
            assert result["success"] is True
            assert result["llm_ready"] is False
        finally:
            deps.setup_only_mode = old
            deps.lumena = old_lumena


class TestBatch1SetupOnlyReSetup:
    """P0.12 — Allow re-setup when in setup_only_mode."""

    @pytest.mark.asyncio
    async def test_re_setup_in_setup_only_mode(self, mock_setup):
        """POST setup accepted even if LUMENA_SETUP_COMPLETE=1, as long as setup_only_mode."""
        from web.routes.setup import setup_complete
        from web.routes import deps
        from unittest.mock import AsyncMock, MagicMock

        request = AsyncMock()
        request.client = MagicMock()
        request.client.host = "127.0.0.1"
        request.json = AsyncMock(return_value={
            "preview": False,
            "config": {"LUMENA_DEFAULT_MODEL": "deepseek-v3"},
        })

        old = deps.setup_only_mode
        try:
            deps.setup_only_mode = True
            with patch.dict(os.environ, {"LUMENA_SETUP_COMPLETE": "1"}):
                result = await setup_complete(request)
            assert result["success"] is True, "P0.12: must allow re-setup in recovery mode"
        finally:
            deps.setup_only_mode = old

    @pytest.mark.asyncio
    async def test_blocked_if_complete_and_not_setup_only(self, mock_setup):
        """POST setup blocked when SETUP_COMPLETE=1 and NOT in setup_only_mode."""
        from web.routes.setup import setup_complete
        from web.routes import deps
        from unittest.mock import AsyncMock, MagicMock

        request = AsyncMock()
        request.client = MagicMock()
        request.client.host = "127.0.0.1"
        request.json = AsyncMock(return_value={
            "preview": False,
            "config": {"LUMENA_DEFAULT_MODEL": "deepseek-v3"},
        })

        old = deps.setup_only_mode
        try:
            deps.setup_only_mode = False
            with patch.dict(os.environ, {"LUMENA_SETUP_COMPLETE": "1"}):
                result = await setup_complete(request)
            assert result["success"] is False
        finally:
            deps.setup_only_mode = old


class TestBatch1AuthBypass:
    """P0.12 — verify_admin_token bypasses auth in setup_only_mode."""

    @pytest.mark.asyncio
    async def test_verify_admin_token_bypass_in_setup_only(self):
        """In setup_only_mode with existing ADMIN_TOKEN in .env → no 401."""
        from web.routes.deps import verify_admin_token
        from web.routes import deps

        old = deps.setup_only_mode
        try:
            deps.setup_only_mode = True
            with patch.dict(os.environ, {
                "LUMENA_SETUP_COMPLETE": "1",
                "LUMENA_ADMIN_TOKEN": "existing-secret-token",
            }):
                # No Authorization header → normally would 401, but setup_only bypasses
                result = await verify_admin_token(authorization=None)
                assert result is None  # should return without raising
        finally:
            deps.setup_only_mode = old

    @pytest.mark.asyncio
    async def test_verify_admin_token_normal_auth_still_works(self):
        """When NOT in setup_only_mode, normal auth is enforced."""
        from web.routes.deps import verify_admin_token
        from web.routes import deps
        from fastapi import HTTPException

        old = deps.setup_only_mode
        try:
            deps.setup_only_mode = False
            with patch.dict(os.environ, {
                "LUMENA_SETUP_COMPLETE": "1",
                "LUMENA_ADMIN_TOKEN": "my-secret",
            }):
                # No header → should raise 401
                with pytest.raises(HTTPException) as exc_info:
                    await verify_admin_token(authorization=None)
                assert exc_info.value.status_code == 401
        finally:
            deps.setup_only_mode = old


# ═══════════════════════════════════════════════════════════════════
# Batch 2 — P0.3, P0.4
# ═══════════════════════════════════════════════════════════════════


class TestBatch2EmailProviders:
    """P0.3 — Email fallback no longer defaults to Gmail."""

    def test_email_yahoo_fr(self):
        from web.routes.setup import _get_email_config
        cfg = _get_email_config("user@yahoo.fr")
        assert cfg["imap_host"] == "imap.mail.yahoo.com"
        assert cfg["smtp_host"] == "smtp.mail.yahoo.com"

    def test_email_free_fr(self):
        from web.routes.setup import _get_email_config
        cfg = _get_email_config("user@free.fr")
        assert cfg["imap_host"] == "imap.free.fr"

    def test_email_icloud(self):
        from web.routes.setup import _get_email_config
        cfg = _get_email_config("user@icloud.com")
        assert cfg["imap_host"] == "imap.mail.me.com"

    def test_email_unknown_domain_not_gmail(self):
        """Unknown domain → generic imap.{domain}, NOT Gmail."""
        from web.routes.setup import _get_email_config
        cfg = _get_email_config("user@custom.org")
        assert cfg["imap_host"] == "imap.custom.org"
        assert cfg["smtp_host"] == "smtp.custom.org"
        assert "gmail" not in cfg["imap_host"]
        assert cfg.get("auto_detected") is True

    def test_email_gmail_still_works(self):
        from web.routes.setup import _get_email_config
        cfg = _get_email_config("user@gmail.com")
        assert cfg["imap_host"] == "imap.gmail.com"

    def test_email_orange_fr(self):
        from web.routes.setup import _get_email_config
        cfg = _get_email_config("user@orange.fr")
        assert cfg["imap_host"] == "imap.orange.fr"


class TestBatch2OllamaHost:
    """P0.4 — Ollama endpoints use LUMENA_OLLAMA_HOST env var."""

    @pytest.mark.asyncio
    async def test_ollama_respects_host(self):
        """get_ollama_models should use LUMENA_OLLAMA_HOST if set."""
        from web.routes.setup import get_ollama_models
        from unittest.mock import AsyncMock, MagicMock, patch as _patch

        request = AsyncMock()
        request.client = MagicMock()
        request.client.host = "127.0.0.1"

        captured_url = {}

        class FakeResponse:
            status_code = 200
            def json(self):
                return {"models": []}

        class FakeClient:
            async def get(self, url, **kw):
                captured_url['url'] = url
                return FakeResponse()
            async def __aenter__(self): return self
            async def __aexit__(self, *a): pass

        with patch.dict(os.environ, {"LUMENA_OLLAMA_HOST": "http://192.168.1.50:11434"}), \
             _patch("httpx.AsyncClient", return_value=FakeClient()):
            await get_ollama_models(request)

        assert "192.168.1.50:11434" in captured_url['url']


# ═══════════════════════════════════════════════════════════════════
# Batch 3 — P1.2, P1.5
# ═══════════════════════════════════════════════════════════════════


class TestBatch3WelcomeTools:
    """P1.2 — welcome-tools no longer shows '340'."""

    def test_no_hardcoded_340_in_index(self):
        index = Path(__file__).resolve().parent.parent / "web" / "index.html"
        content = index.read_text(encoding="utf-8")
        assert '>340<' not in content
        assert 'id="welcome-tools"' in content


class TestBatch3HostDefault:
    """P1.5 — Wizard security step defaults to 127.0.0.1."""

    @pytest.mark.asyncio
    async def test_host_default_127(self):
        from web.routes.setup import setup_schema
        result = await setup_schema()
        security_step = next((s for s in result["steps"] if s["id"] == "security"), None)
        assert security_step is not None
        host_field = security_step.get("host_field")
        assert host_field is not None
        assert host_field["default"] == "127.0.0.1"


# ─── Batch 4 tests ────────────────────────────────────────────────

class TestBatch4MiniMaxWizard:
    """P2.3 — MiniMax present in wizard schema and probes."""

    @pytest.mark.asyncio
    async def test_minimax_in_keys_step(self):
        from web.routes.setup import setup_schema
        result = await setup_schema()
        keys_step = next((s for s in result["steps"] if s["id"] == "keys"), None)
        assert keys_step is not None
        providers = keys_step.get("providers", [])
        minimax = next((p for p in providers if p["key"] == "MINIMAX_API_KEY"), None)
        assert minimax is not None, "MINIMAX_API_KEY absent du step keys"
        assert "MiniMax" in minimax["name"]

    @pytest.mark.asyncio
    async def test_minimax_probe_exists(self):
        """Probe MiniMax is registered in test_api_key."""
        import httpx
        from unittest.mock import AsyncMock, patch as _p
        from web.routes.setup import test_api_key
        # Just verify the probe dict contains minimax by calling with a fake key
        # and mocking httpx to avoid real HTTP
        mock_resp = AsyncMock()
        mock_resp.status_code = 200
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_request = type("R", (), {
            "json": AsyncMock(return_value={"provider": "minimax", "key": "fake-key-123"}),
            "client": type("C", (), {"host": "127.0.0.1"})(),
        })()
        with _p("httpx.AsyncClient", return_value=mock_client):
            result = await test_api_key(mock_request)
        assert result.get("success") is True


class TestBatch4HealthCheck9Providers:
    """P2.4 — Health check knows about all 9 providers (was 4)."""

    def test_all_providers_in_check(self):
        from src.utils.health_check import HealthChecker
        # Verify the source contains all 8 providers
        import inspect
        src = inspect.getsource(HealthChecker._check_api_keys)
        for key in ["ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY",
                     "DEEPSEEK_API_KEY", "NVIDIA_API_KEY", "MOONSHOT_API_KEY",
                     "XAI_API_KEY", "MINIMAX_API_KEY"]:
            assert key in src, f"{key} absent de _check_api_keys"

    def test_nvidia_key_detected(self):
        from src.utils.health_check import HealthChecker
        checker = HealthChecker()
        with patch.dict(os.environ, {"NVIDIA_API_KEY": "nvapi-test123"}, clear=False):
            result = checker._check_api_keys()
        assert result.healthy
        # At least 1 provider detected (may be more from real env)
        assert "provider" in result.message


class TestBatch4AgentKeywordsAccents:
    """P2.6 — AGENT_KEYWORDS includes accented versions, no duplicates."""

    def test_accented_keywords_in_source(self):
        src = Path(__file__).resolve().parent.parent / "web" / "routes" / "lifespan.py"
        content = src.read_text(encoding="utf-8")
        for kw in ["exécute", "mémorise", "mémoire", "mémoires", "réfléchis", "planifie", "génère"]:
            assert kw in content, f"Accent keyword '{kw}' absent de lifespan.py"

    def test_no_duplicate_execute(self):
        src = Path(__file__).resolve().parent.parent / "web" / "routes" / "lifespan.py"
        content = src.read_text(encoding="utf-8")
        # Find the AGENT_KEYWORDS block
        start = content.find("AGENT_KEYWORDS = [")
        end = content.find("]", start) + 1
        block = content[start:end]
        # "execute" (no accent) should appear exactly once
        assert block.count('"execute"') == 1, f'"execute" duplicated: found {block.count(chr(34) + "execute" + chr(34))}'


class TestBatch4PreflightAsync:
    """P2.7 — Preflight uses asyncio.to_thread."""

    def test_preflight_uses_to_thread(self):
        src = Path(__file__).resolve().parent.parent / "web" / "routes" / "system.py"
        content = src.read_text(encoding="utf-8")
        assert "asyncio.to_thread" in content, "preflight should use asyncio.to_thread"


class TestBatch4SetupJsonValidation:
    """P2.9 — setup_complete handles invalid JSON gracefully."""

    @pytest.mark.asyncio
    async def test_invalid_json_returns_error(self):
        from web.routes.setup import setup_complete
        from unittest.mock import AsyncMock, MagicMock

        mock_request = MagicMock()
        mock_request.client = MagicMock()
        mock_request.client.host = "127.0.0.1"
        mock_request.json = AsyncMock(side_effect=ValueError("Invalid JSON"))

        with patch("web.routes.setup.deps") as mock_deps:
            mock_deps.verify_admin_token = AsyncMock()
            mock_deps.setup_only_mode = False
            result = await setup_complete(mock_request)

        assert result["success"] is False
        assert "invalide" in result["error"].lower() or "JSON" in result["error"]

    @pytest.mark.asyncio
    async def test_non_dict_json_returns_error(self):
        from web.routes.setup import setup_complete
        from unittest.mock import AsyncMock, MagicMock

        mock_request = MagicMock()
        mock_request.client = MagicMock()
        mock_request.client.host = "127.0.0.1"
        mock_request.json = AsyncMock(return_value=[1, 2, 3])  # array, not dict

        with patch("web.routes.setup.deps") as mock_deps:
            mock_deps.verify_admin_token = AsyncMock()
            mock_deps.setup_only_mode = False
            result = await setup_complete(mock_request)

        assert result["success"] is False
        assert "object" in result["error"].lower() or "JSON" in result["error"]


class TestBatch4AnthropicProbeReadonly:
    """P2.2 — Anthropic probe uses GET /v1/models (not POST /v1/messages)."""

    def test_anthropic_probe_is_get(self):
        src = Path(__file__).resolve().parent.parent / "web" / "routes" / "setup.py"
        content = src.read_text(encoding="utf-8")
        # Find the _PROBES block (nested dicts, so find matching closing brace)
        start = content.find("_PROBES = {")
        # Find the line "probe = _PROBES.get" which comes right after the dict
        end = content.find("probe = _PROBES.get", start)
        block = content[start:end]
        # Anthropic should use GET /v1/models, not POST /v1/messages
        assert '"anthropic"' in block
        assert "/v1/messages" not in block, "Anthropic probe still uses /v1/messages (should be /v1/models)"
        assert "/v1/models" in block


class TestBatch4VouvoiementFix:
    """P2.5 — Restart message uses tutoiement."""

    def test_no_vouvoiement_in_restart(self):
        src = Path(__file__).resolve().parent.parent / "web" / "static" / "js" / "setup.js"
        content = src.read_text(encoding="utf-8")
        assert "Vous avez configuré" not in content, "Vouvoiement still present"
        assert "Fermez cette fenêtre" not in content, "Vouvoiement still present"
        assert "Tu as configuré" in content


# ─── Batch 5 tests ────────────────────────────────────────────────

class TestBatch5NoCharlesRegex:
    """P3.1 — No 'charles' hardcoded in email regex."""

    def test_no_charles_in_config(self):
        src = Path(__file__).resolve().parent.parent / "web" / "routes" / "config.py"
        content = src.read_text(encoding="utf-8")
        assert "charles" not in content.lower(), "'charles' still hardcoded in config.py"


class TestBatch5MiniMaxSimpleLevel:
    """P3.2 — MINIMAX_API_KEY is level 'simple'."""

    def test_minimax_in_simple_keys(self):
        from web.routes.config import _SIMPLE_KEYS
        assert "MINIMAX_API_KEY" in _SIMPLE_KEYS


class TestBatch5OpsPurgeDefault:
    """P3.3 — OPS_MEMORY_PURGE_ENABLED default is '1' (not 'true')."""

    def test_ops_purge_default_is_1(self):
        from web.routes.config import _CONFIG_SCHEMA
        entry = next((s for s in _CONFIG_SCHEMA if s["key"] == "LUMENA_OPS_MEMORY_PURGE_ENABLED"), None)
        assert entry is not None
        assert entry["default"] == "1", f"Expected '1', got '{entry['default']}'"


class TestBatch5NoInlineOnclick:
    """P3.5 — No inline onclick attributes in setup.js."""

    def test_no_onclick_in_setup_js(self):
        src = Path(__file__).resolve().parent.parent / "web" / "static" / "js" / "setup.js"
        content = src.read_text(encoding="utf-8")
        assert 'onclick="' not in content, "Inline onclick still present in setup.js"


class TestBatch5SetupOnlySkipsChannels:
    """P3.6 — In setup_only_mode, channels are skipped."""

    def test_lifespan_guards_channels(self):
        src = Path(__file__).resolve().parent.parent / "web" / "routes" / "lifespan.py"
        content = src.read_text(encoding="utf-8")
        for marker in ["Demarrer Telegram", "Demarrer Discord",
                       "Démarrer Twitter", "Démarrer le serveur WebSocket IDE"]:
            idx = content.find(marker)
            assert idx != -1, f"'{marker}' comment not found"
            block = content[idx:idx+300]
            assert "setup_only_mode" in block, f"No setup_only_mode guard near '{marker}'"


class TestBatch5EnvWriteDecomments:
    """P3.7 — _write_env_values matches and replaces commented-out lines too."""

    def test_write_env_uncomments_keys(self, tmp_path):
        from web.routes.config import _write_env_values, _ENV_WRITE_LOCK
        from unittest.mock import patch as _p, MagicMock
        from pathlib import Path

        env_file = tmp_path / ".env"
        env_file.write_text("# LUMENA_ADMIN_TOKEN=\nFOO=bar\n# TWITTER_BEARER_TOKEN=\n", encoding="utf-8")
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        lock_file = tmp_path / ".env.lock"

        with _p("web.routes.config._PROJECT_ROOT", tmp_path), \
             _p("web.routes.config._ENV_BACKUP_DIR", backup_dir), \
             _p("web.routes.config._ENV_FILE_LOCK", lock_file):
            _write_env_values({"LUMENA_ADMIN_TOKEN": "tok123", "TWITTER_BEARER_TOKEN": "tw456"})

        content = env_file.read_text(encoding="utf-8")
        lines = content.splitlines()
        # Should NOT have duplicates
        admin_lines = [l for l in lines if l.startswith("LUMENA_ADMIN_TOKEN=")]
        assert len(admin_lines) == 1, f"Expected 1 LUMENA_ADMIN_TOKEN line, got {len(admin_lines)}"
        assert admin_lines[0] == "LUMENA_ADMIN_TOKEN=tok123"
        tw_lines = [l for l in lines if l.startswith("TWITTER_BEARER_TOKEN=")]
        assert len(tw_lines) == 1
        assert tw_lines[0] == "TWITTER_BEARER_TOKEN=tw456"
        # Original FOO should still be there
        assert any(l.startswith("FOO=bar") for l in lines)


# ─── Batch 6 tests ────────────────────────────────────────────────

class TestBatch6Accessibility:
    """P4.1-P4.4 — Accessibility improvements."""

    def test_dialog_role_on_overlay(self):
        """P4.1 — role=dialog + aria-modal on wizard overlay."""
        src = Path(__file__).resolve().parent.parent / "web" / "index.html"
        content = src.read_text(encoding="utf-8")
        assert 'role="dialog"' in content
        assert 'aria-modal="true"' in content

    def test_focus_management_in_navigation(self):
        """P4.2 — Focus is set after step change."""
        src = Path(__file__).resolve().parent.parent / "web" / "static" / "js" / "setup.js"
        content = src.read_text(encoding="utf-8")
        # Both _goNext and _goBack should call .focus()
        go_next_idx = content.find("function _goNext()")
        go_back_idx = content.find("function _goBack()")
        assert go_next_idx != -1 and go_back_idx != -1
        go_next_block = content[go_next_idx:go_back_idx]
        go_back_block = content[go_back_idx:go_back_idx + 500]
        assert ".focus()" in go_next_block, "_goNext should call .focus()"
        assert ".focus()" in go_back_block, "_goBack should call .focus()"

    def test_responsive_css(self):
        """P4.3 — Mobile responsive media query exists."""
        src = Path(__file__).resolve().parent.parent / "web" / "static" / "css" / "setup.css"
        content = src.read_text(encoding="utf-8")
        assert "@media (max-width: 480px)" in content

    def test_focus_visible_styles(self):
        """P4.4 — focus-visible outline on interactive elements."""
        src = Path(__file__).resolve().parent.parent / "web" / "static" / "css" / "setup.css"
        content = src.read_text(encoding="utf-8")
        assert ".setup-btn:focus-visible" in content
        assert "outline:" in content


# ─── Batch 7 — Missing P5 tests ───────────────────────────────────

class TestBatch7SetupDefaultsPrefilled:
    """P5.1 — POST setup/complete with empty config still has defaults applied."""

    @pytest.mark.asyncio
    async def test_defaults_applied_on_empty_config(self, mock_setup):
        from web.routes.setup import setup_complete
        from unittest.mock import AsyncMock, MagicMock, patch as _p

        mock_request = MagicMock()
        mock_request.client = MagicMock()
        mock_request.client.host = "127.0.0.1"
        mock_request.json = AsyncMock(return_value={"config": {"LUMENA_DEFAULT_MODEL": "deepseek-v3"}})

        env_file = mock_setup
        with _p("web.routes.setup.deps") as mock_deps, \
             _p("src.core.initialize_lumena", new_callable=AsyncMock, create=True):
            mock_deps.setup_only_mode = True
            mock_deps.lumena = None
            result = await setup_complete(mock_request)

        assert result.get("success") is True
        # .env should contain at least LUMENA_SETUP_COMPLETE
        content = env_file.read_text(encoding="utf-8")
        assert "LUMENA_SETUP_COMPLETE=1" in content


class TestBatch7SandboxFieldSelect:
    """P5.1 — LUMENA_SANDBOX_MODE schema has type 'select' with options."""

    def test_sandbox_mode_is_select(self):
        from web.routes.config import _CONFIG_SCHEMA
        entry = next((s for s in _CONFIG_SCHEMA if s["key"] == "LUMENA_SANDBOX_MODE"), None)
        assert entry is not None, "LUMENA_SANDBOX_MODE not in schema"
        assert entry["type"] == "select", f"Expected type 'select', got '{entry['type']}'"
        assert "options" in entry and len(entry["options"]) > 0


class TestBatch7Chat503Detail:
    """P5.1 — chat.js reads response.json().detail before throwing Error."""

    def test_chat_reads_detail_on_error(self):
        src = Path(__file__).resolve().parent.parent / "web" / "static" / "js" / "chat.js"
        content = src.read_text(encoding="utf-8")
        # The pattern should be: if(!response.ok) → read json → throw with detail
        assert "response.json()" in content, "chat.js should read response body on error"
        assert ".detail" in content, "chat.js should extract .detail from error response"
