"""
test_handlers_config_manager.py — Tests des handlers get_lumena_config / update_lumena_config.

Teste la lecture de config, la modification, la validation de types,
et le blocage des secrets via un fichier .env temporaire.
"""
import os
import pytest
from pathlib import Path
from unittest.mock import patch

from src.reasoning.handlers.context import HandlerContext
from src.reasoning.handlers.contracts import HandlerResult
from src.reasoning.handlers.config_manager import (
    get_config_handler,
    update_config_handler,
    explain_config_handler,
    get_config_manager_handler_defs,
)


# ─── Helpers ───────────────────────────────────────────────────────────────

_MINI_SCHEMA = [
    {"key": "LUMENA_DEFAULT_MODEL", "label": "Modèle par défaut", "group": "LLM",
     "type": "select", "options": ["deepseek-v3", "gpt-5.4"], "default": "deepseek-v3",
     "level": "simple", "hint": "Modèle LLM utilisé par défaut."},
    {"key": "LUMENA_MAX_REACT_ITERATIONS", "label": "Max itérations ReAct", "group": "LLM",
     "type": "number", "default": "25", "level": "avancé",
     "hint": "Nombre max de cycles THOUGHT."},
    {"key": "LUMENA_TTS_AUTO", "label": "TTS automatique", "group": "Voix",
     "type": "bool", "default": "0", "level": "simple",
     "hint": "Lit automatiquement les réponses."},
    {"key": "LUMENA_AUTONOMY_ALLOWED_ACTIONS", "label": "Actions autorisées", "group": "Autonomie",
     "type": "text", "default": "EXPLORE_WEB,REFLECT", "level": "simple",
     "hint": "Types d'actions autorisées."},
    {"key": "OPENAI_API_KEY", "label": "OpenAI API Key", "group": "Clés API",
     "type": "secret", "default": "", "level": "simple",
     "hint": "Clé API OpenAI."},
    {"key": "LUMENA_PORT", "label": "Port du serveur web", "group": "Serveur",
     "type": "number", "default": "8080", "level": "avancé", "restart": True,
     "hint": "Port d'écoute FastAPI."},
    {"key": "LUMENA_ARCHIVE_MAX_AGE_DAYS", "label": "Age max archive", "group": "Ops",
     "type": "number", "default": "30", "level": "expert",
     "hint": "Archives de plus de X jours supprimées."},
]


@pytest.fixture
def env_file(tmp_path):
    """Crée un .env temporaire avec quelques valeurs."""
    env = tmp_path / ".env"
    env.write_text(
        "LUMENA_DEFAULT_MODEL=deepseek-v3\n"
        "LUMENA_MAX_REACT_ITERATIONS=25\n"
        "LUMENA_TTS_AUTO=0\n"
        "OPENAI_API_KEY=sk-test1234567890abcdef\n",
        encoding="utf-8",
    )
    return env


@pytest.fixture
def mock_config(env_file, monkeypatch):
    """Patche les internals config pour utiliser le .env temporaire et le mini schéma."""
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
        allowed = {s["key"] for s in _MINI_SCHEMA}
        remaining = {k: v for k, v in updates.items() if k in allowed}
        lines = env_file.read_text(encoding="utf-8").splitlines()
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

    # Isole os.environ pour que update_config_handler ne pollue pas les autres tests
    with patch.dict(os.environ, {}, clear=False), patch(
        "src.reasoning.handlers.config_manager._get_config_internals",
        return_value=(_MINI_SCHEMA, read_env, write_env),
    ):
        yield env_file


@pytest.fixture
def ctx(tmp_path):
    return HandlerContext.for_testing(lumena_root=tmp_path, runtime_root=tmp_path)


# ─── get_lumena_config ─────────────────────────────────────────────────────

class TestGetConfig:
    @pytest.mark.asyncio
    async def test_read_default_simple(self, ctx, mock_config):
        """P6.1: sans filtre → simple uniquement."""
        r = await get_config_handler(ctx)
        assert r.success
        assert "LUMENA_DEFAULT_MODEL" in r.output
        assert "LUMENA_TTS_AUTO" in r.output
        # avancé/expert exclus
        assert "LUMENA_PORT" not in r.output
        assert "LUMENA_ARCHIVE_MAX_AGE_DAYS" not in r.output
        assert "Affichage simple" in r.output

    @pytest.mark.asyncio
    async def test_read_avance(self, ctx, mock_config):
        """P6.1: group=avancé → simple+avancé."""
        r = await get_config_handler(ctx, group="avancé")
        assert r.success
        assert "LUMENA_DEFAULT_MODEL" in r.output
        assert "LUMENA_PORT" in r.output
        assert "LUMENA_ARCHIVE_MAX_AGE_DAYS" not in r.output

    @pytest.mark.asyncio
    async def test_read_tout(self, ctx, mock_config):
        """P6.1: group=tout → tout."""
        r = await get_config_handler(ctx, group="tout")
        assert r.success
        assert "LUMENA_DEFAULT_MODEL" in r.output
        assert "LUMENA_PORT" in r.output
        assert "LUMENA_ARCHIVE_MAX_AGE_DAYS" in r.output

    @pytest.mark.asyncio
    async def test_read_filtered_group(self, ctx, mock_config):
        r = await get_config_handler(ctx, group="Voix")
        assert r.success
        assert "TTS automatique" in r.output
        assert "LLM" not in r.output

    @pytest.mark.asyncio
    async def test_secrets_masked(self, ctx, mock_config):
        r = await get_config_handler(ctx)
        assert r.success
        assert "sk-test1234567890abcdef" not in r.output
        assert "***" in r.output

    @pytest.mark.asyncio
    async def test_unknown_group(self, ctx, mock_config):
        r = await get_config_handler(ctx, group="inexistant")
        assert r.success
        assert "Aucun" in r.output


# ─── update_lumena_config ──────────────────────────────────────────────────

class TestUpdateConfig:
    @pytest.mark.asyncio
    async def test_update_number(self, ctx, mock_config):
        r = await update_config_handler(ctx, key="LUMENA_MAX_REACT_ITERATIONS", value="50")
        assert r.success
        assert "50" in r.output
        assert os.environ.get("LUMENA_MAX_REACT_ITERATIONS") == "50"
        # Vérifier le fichier .env
        content = mock_config.read_text()
        assert "LUMENA_MAX_REACT_ITERATIONS=50" in content

    @pytest.mark.asyncio
    async def test_update_select(self, ctx, mock_config):
        r = await update_config_handler(ctx, key="LUMENA_DEFAULT_MODEL", value="gpt-5.4")
        assert r.success
        assert "gpt-5.4" in r.output

    @pytest.mark.asyncio
    async def test_update_bool_oui(self, ctx, mock_config):
        r = await update_config_handler(ctx, key="LUMENA_TTS_AUTO", value="oui")
        assert r.success
        assert os.environ.get("LUMENA_TTS_AUTO") == "1"

    @pytest.mark.asyncio
    async def test_update_bool_false(self, ctx, mock_config):
        r = await update_config_handler(ctx, key="LUMENA_TTS_AUTO", value="non")
        assert r.success
        assert os.environ.get("LUMENA_TTS_AUTO") == "0"

    @pytest.mark.asyncio
    async def test_reject_secret(self, ctx, mock_config):
        r = await update_config_handler(ctx, key="OPENAI_API_KEY", value="sk-newkey")
        assert not r.success
        assert "interdite" in r.output.lower() or "secret" in r.output.lower()

    @pytest.mark.asyncio
    async def test_reject_unknown_key(self, ctx, mock_config):
        r = await update_config_handler(ctx, key="TOTALLY_FAKE_KEY", value="123")
        assert not r.success
        assert "inconnue" in r.output.lower() or "valides" in r.output.lower()

    @pytest.mark.asyncio
    async def test_reject_invalid_number(self, ctx, mock_config):
        r = await update_config_handler(ctx, key="LUMENA_MAX_REACT_ITERATIONS", value="abc")
        assert not r.success
        assert "nombre" in r.output.lower()

    @pytest.mark.asyncio
    async def test_reject_invalid_select(self, ctx, mock_config):
        r = await update_config_handler(ctx, key="LUMENA_DEFAULT_MODEL", value="fake-model")
        assert not r.success
        assert "option" in r.output.lower()

    @pytest.mark.asyncio
    async def test_reject_invalid_bool(self, ctx, mock_config):
        r = await update_config_handler(ctx, key="LUMENA_TTS_AUTO", value="maybe")
        assert not r.success
        assert "booléenne" in r.output.lower() or "bool" in r.output.lower()

    @pytest.mark.asyncio
    async def test_missing_params(self, ctx, mock_config):
        r = await update_config_handler(ctx, key="", value="")
        assert not r.success

    @pytest.mark.asyncio
    async def test_fuzzy_key_by_label(self, ctx, mock_config):
        """L'utilisateur peut dire 'max itérations' au lieu du nom technique."""
        r = await update_config_handler(ctx, key="max itérations", value="40")
        assert r.success
        assert "LUMENA_MAX_REACT_ITERATIONS" in r.output
        assert "40" in r.output

    @pytest.mark.asyncio
    async def test_update_text_field(self, ctx, mock_config):
        r = await update_config_handler(ctx, key="LUMENA_AUTONOMY_ALLOWED_ACTIONS", value="REFLECT,WRITE_DIARY")
        assert r.success
        assert "REFLECT,WRITE_DIARY" in r.output

    @pytest.mark.asyncio
    async def test_restart_note_for_port(self, ctx, mock_config):
        """P6.3: un champ restart=True affiche un message de redémarrage."""
        r = await update_config_handler(ctx, key="LUMENA_PORT", value="9090")
        assert r.success
        assert "redémarrage" in r.output.lower()

    @pytest.mark.asyncio
    async def test_guard_avance_note(self, ctx, mock_config):
        """P6.3: un champ avancé affiche '(paramètre avancé)'."""
        r = await update_config_handler(ctx, key="LUMENA_MAX_REACT_ITERATIONS", value="50")
        assert r.success
        assert "avancé" in r.output.lower()

    @pytest.mark.asyncio
    async def test_guard_expert_note(self, ctx, mock_config):
        """P6.3: un champ expert affiche '(paramètre expert)'."""
        r = await update_config_handler(ctx, key="LUMENA_ARCHIVE_MAX_AGE_DAYS", value="60")
        assert r.success
        assert "expert" in r.output.lower()


# ─── Registre ──────────────────────────────────────────────────────────────

# ─── explain_lumena_config (P6.2) ───────────────────────────────────────────

class TestExplainConfig:
    @pytest.mark.asyncio
    async def test_fuzzy_match_label(self, ctx, mock_config):
        r = await explain_config_handler(ctx, query="TTS")
        assert r.success
        assert "TTS automatique" in r.output

    @pytest.mark.asyncio
    async def test_fuzzy_match_hint(self, ctx, mock_config):
        r = await explain_config_handler(ctx, query="FastAPI")
        assert r.success
        assert "LUMENA_PORT" in r.output

    @pytest.mark.asyncio
    async def test_secrets_masked_in_explain(self, ctx, mock_config):
        r = await explain_config_handler(ctx, query="OpenAI")
        assert r.success
        assert "***" in r.output
        assert "sk-test" not in r.output

    @pytest.mark.asyncio
    async def test_no_match(self, ctx, mock_config):
        r = await explain_config_handler(ctx, query="xyznonexistent")
        assert r.success
        assert "Aucun" in r.output

    @pytest.mark.asyncio
    async def test_empty_query_rejected(self, ctx, mock_config):
        r = await explain_config_handler(ctx, query="")
        assert not r.success

    @pytest.mark.asyncio
    async def test_suggestion_for_modifiable(self, ctx, mock_config):
        r = await explain_config_handler(ctx, query="itérations")
        assert r.success
        assert "update_lumena_config" in r.output

    @pytest.mark.asyncio
    async def test_suggestion_secret_redirect(self, ctx, mock_config):
        r = await explain_config_handler(ctx, query="OpenAI")
        assert r.success
        assert "page Configuration web" in r.output


# ─── Registre ──────────────────────────────────────────────────────────────

class TestRegistry:
    def test_handler_defs_count(self):
        defs = get_config_manager_handler_defs()
        assert len(defs) == 3

    def test_handler_names(self):
        defs = get_config_manager_handler_defs()
        names = {d.name for d in defs}
        assert "get_lumena_config" in names
        assert "update_lumena_config" in names
        assert "explain_lumena_config" in names

    def test_handler_category(self):
        defs = get_config_manager_handler_defs()
        for d in defs:
            assert d.category == "system"

    def test_update_has_required_params(self):
        defs = get_config_manager_handler_defs()
        update = next(d for d in defs if d.name == "update_lumena_config")
        assert "key" in update.parameters["properties"]
        assert "value" in update.parameters["properties"]
        assert "key" in update.parameters["required"]
        assert "value" in update.parameters["required"]
