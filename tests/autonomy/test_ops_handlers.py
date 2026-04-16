"""Tests ciblés pour handler_daily_github_project et handler_workspace_archive."""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ────────────────────────────────────────────────────────
#  daily_github_project
# ────────────────────────────────────────────────────────

class TestDailyGithubProject:
    """Test du handler daily_github_project — flux déterministe 4 étapes."""

    @pytest.fixture(autouse=True)
    def _isolate(self, tmp_path, monkeypatch):
        """Isole l'état (state, dirs, metrics) dans un dossier temporaire."""
        self.ops_dir = tmp_path / "data" / "ops"
        self.ops_dir.mkdir(parents=True)
        self.workspace = tmp_path / "workspace"
        self.workspace.mkdir()
        import src.autonomy.ops_handlers as mod

        monkeypatch.setattr(mod, "_ROOT", tmp_path)
        monkeypatch.setattr(mod, "_DATA", tmp_path / "data")
        monkeypatch.setattr(mod, "_OPS_DIR", self.ops_dir)
        monkeypatch.setattr(mod, "_METRICS_FILE", self.ops_dir / "metrics.jsonl")
        monkeypatch.setattr(mod, "_STATE_FILE", self.ops_dir / "ops_state.json")
        monkeypatch.setattr(mod, "_REPORTS_DIR", tmp_path / "data" / "reports")
        monkeypatch.setattr(mod, "_WORKSPACE", self.workspace)
        # Empêcher la notification Telegram
        monkeypatch.setattr(mod, "_notify_telegram_proactive", AsyncMock(return_value=True))

        today = __import__("datetime").datetime.now().strftime("%Y-%m-%d")
        self._project_dir = self.workspace / today / f"lumena-daily-{today}"

    def _create_real_project_dir(self):
        """Crée le dossier projet dans le workspace isolé pour que le handler le trouve."""
        self._project_dir.mkdir(parents=True, exist_ok=True)
        (self._project_dir / "main.py").write_text("print('hello')")

    def _make_core_mock(self, llm_idea: str = "test-tool — a CLI tool"):
        core = MagicMock()
        core.llm = AsyncMock()
        core.llm.chat = AsyncMock(return_value=llm_idea)
        return core

    def _fake_project_result(self, success: bool = True):
        r = MagicMock()
        r.success = success
        r.output = "project created" if success else ""
        r.error = None if success else "fail"
        return r

    def _fake_push_result(self, success: bool = True):
        r = MagicMock()
        r.success = success
        r.output = "pushed 12 files" if success else ""
        r.error = None if success else "push fail"
        return r

    # ── Tests ──

    @pytest.mark.asyncio
    async def test_full_success_flow(self, tmp_path):
        """Le flux complet 4 étapes doit retourner executed=True."""
        from src.autonomy.ops_handlers import handler_daily_github_project

        core = self._make_core_mock()
        self._create_real_project_dir()

        with patch("src.core.get_lumena", return_value=core), \
             patch("src.reasoning.handlers.project.create_project_handler",
                   new_callable=AsyncMock, return_value=self._fake_project_result(True)), \
             patch("src.reasoning.handlers.github._get_token", return_value="ghp_fake"), \
             patch("src.reasoning.handlers.github._gh_request",
                   new_callable=AsyncMock,
                   return_value=(201, {"html_url": "https://github.com/u/r", "owner": {"login": "u"}})), \
             patch("src.reasoning.handlers.github.github_push_directory_handler",
                   new_callable=AsyncMock, return_value=self._fake_push_result(True)), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            result = await handler_daily_github_project()

        assert result["executed"] is True
        assert result["repo_url"] == "https://github.com/u/r"
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_skip_if_already_executed_today(self, tmp_path):
        """Si déjà exécuté aujourd'hui, doit retourner skipped=True."""
        import json as _json
        from src.autonomy.ops_handlers import handler_daily_github_project

        today = __import__("datetime").datetime.now().strftime("%Y-%m-%d")
        state = {"last_daily_github_project_date": today, "last_reset_date": today}
        (self.ops_dir / "ops_state.json").write_text(_json.dumps(state))

        result = await handler_daily_github_project()

        assert result["skipped"] is True
        assert result["executed"] is False
        assert "already_executed_today" in result.get("reason", "")

    @pytest.mark.asyncio
    async def test_no_core_returns_error(self):
        """Si LumenaCore indisponible, doit retourner une erreur."""
        from src.autonomy.ops_handlers import handler_daily_github_project

        with patch("src.core.get_lumena", return_value=None):
            result = await handler_daily_github_project()

        assert result["executed"] is False
        assert "error" in result

    @pytest.mark.asyncio
    async def test_create_project_failure_stops_flow(self, tmp_path):
        """Si create_project échoue, le push ne doit pas être tenté."""
        from src.autonomy.ops_handlers import handler_daily_github_project

        core = self._make_core_mock()
        push_mock = AsyncMock()

        with patch("src.core.get_lumena", return_value=core), \
             patch("src.reasoning.handlers.project.create_project_handler",
                   new_callable=AsyncMock, return_value=self._fake_project_result(False)), \
             patch("src.reasoning.handlers.github.github_push_directory_handler", push_mock):
            result = await handler_daily_github_project()

        assert result["executed"] is False
        assert "error" in result
        push_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_push_failure_keeps_executed_false(self, tmp_path):
        """Si le push échoue, executed doit rester False (pas de faux-positif)."""
        from src.autonomy.ops_handlers import handler_daily_github_project

        core = self._make_core_mock()
        self._create_real_project_dir()

        with patch("src.core.get_lumena", return_value=core), \
             patch("src.reasoning.handlers.project.create_project_handler",
                   new_callable=AsyncMock, return_value=self._fake_project_result(True)), \
             patch("src.reasoning.handlers.github._get_token", return_value="ghp_fake"), \
             patch("src.reasoning.handlers.github._gh_request",
                   new_callable=AsyncMock,
                   return_value=(201, {"html_url": "https://github.com/u/r", "owner": {"login": "u"}})), \
             patch("src.reasoning.handlers.github.github_push_directory_handler",
                   new_callable=AsyncMock, return_value=self._fake_push_result(False)), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            result = await handler_daily_github_project()

        assert result["executed"] is False
        assert "error" in result

    @pytest.mark.asyncio
    async def test_github_repo_creation_failure(self, tmp_path):
        """Si l'API GitHub refuse la création du repo, executed=False."""
        from src.autonomy.ops_handlers import handler_daily_github_project

        core = self._make_core_mock()
        self._create_real_project_dir()

        with patch("src.core.get_lumena", return_value=core), \
             patch("src.reasoning.handlers.project.create_project_handler",
                   new_callable=AsyncMock, return_value=self._fake_project_result(True)), \
             patch("src.reasoning.handlers.github._get_token", return_value="ghp_fake"), \
             patch("src.reasoning.handlers.github._gh_request",
                   new_callable=AsyncMock,
                   return_value=(422, {"message": "name already exists"})), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            result = await handler_daily_github_project()

        assert result["executed"] is False
        assert "error" in result

    @pytest.mark.asyncio
    async def test_missing_github_token(self, tmp_path):
        """Sans token GitHub, le handler doit s'arrêter proprement."""
        from src.autonomy.ops_handlers import handler_daily_github_project

        core = self._make_core_mock()
        self._create_real_project_dir()

        with patch("src.core.get_lumena", return_value=core), \
             patch("src.reasoning.handlers.project.create_project_handler",
                   new_callable=AsyncMock, return_value=self._fake_project_result(True)), \
             patch("src.reasoning.handlers.github._get_token", return_value=None):
            result = await handler_daily_github_project()

        assert result["executed"] is False
        assert "Token" in result.get("error", "") or "token" in result.get("error", "").lower()


# ────────────────────────────────────────────────────────
#  workspace_archive
# ────────────────────────────────────────────────────────

class TestWorkspaceArchive:
    """Test du handler workspace_archive — archivage des vieux projets."""

    @pytest.fixture(autouse=True)
    def _isolate(self, tmp_path, monkeypatch):
        """Isole le workspace dans un dossier temporaire."""
        self.root = tmp_path
        self.workspace = tmp_path / "workspace"
        self.workspace.mkdir()
        import src.autonomy.ops_handlers as mod

        monkeypatch.setattr(mod, "_ROOT", tmp_path)
        monkeypatch.setattr(mod, "_WORKSPACE", tmp_path / "workspace")
        monkeypatch.setattr(mod, "_DATA", tmp_path / "data")
        monkeypatch.setattr(mod, "_OPS_DIR", tmp_path / "data" / "ops")
        monkeypatch.setattr(mod, "_METRICS_FILE", tmp_path / "data" / "ops" / "metrics.jsonl")
        monkeypatch.setattr(mod, "_STATE_FILE", tmp_path / "data" / "ops" / "ops_state.json")
        (tmp_path / "data" / "ops").mkdir(parents=True)
        # Neutralise toute pollution de LUMENA_ARCHIVE_MAX_AGE_DAYS par des tests précédents
        monkeypatch.delenv("LUMENA_ARCHIVE_MAX_AGE_DAYS", raising=False)

    def _make_old_dir(self, name: str, age_days: int = 60):
        """Crée un dossier dans workspace/ avec un mtime ancien."""
        d = self.workspace / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "file.txt").write_text("content")
        old_time = time.time() - (age_days * 86400)
        os.utime(str(d), (old_time, old_time))
        return d

    def _make_recent_dir(self, name: str):
        d = self.workspace / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "file.txt").write_text("content")
        return d

    # ── Tests ──

    @pytest.mark.asyncio
    async def test_archives_old_directory(self):
        """Un dossier > 30 jours doit être archivé."""
        from src.autonomy.ops_handlers import handler_workspace_archive

        self._make_old_dir("old-project", age_days=60)

        result = await handler_workspace_archive()

        assert result["success"] is True
        assert result["archived"] == 1
        assert not (self.workspace / "old-project").exists()
        assert (self.workspace / "_archives").exists()

    @pytest.mark.asyncio
    async def test_skips_recent_directory(self):
        """Un dossier récent ne doit PAS être archivé."""
        from src.autonomy.ops_handlers import handler_workspace_archive

        self._make_recent_dir("fresh-project")

        result = await handler_workspace_archive()

        assert result["success"] is True
        assert result["archived"] == 0
        assert result["skipped"] == 1
        assert (self.workspace / "fresh-project").exists()

    @pytest.mark.asyncio
    async def test_skips_locked_directory(self):
        """Un vieux dossier avec .lock ne doit PAS être archivé."""
        from src.autonomy.ops_handlers import handler_workspace_archive

        d = self._make_old_dir("locked-project", age_days=60)
        (d / "build.lock").write_text("")

        result = await handler_workspace_archive()

        assert result["archived"] == 0
        assert result["skipped"] >= 1
        assert (self.workspace / "locked-project").exists()

    @pytest.mark.asyncio
    async def test_skips_wip_directory(self):
        """Un vieux dossier avec .wip ne doit PAS être archivé."""
        from src.autonomy.ops_handlers import handler_workspace_archive

        d = self._make_old_dir("wip-project", age_days=60)
        (d / "edit.wip").write_text("")

        result = await handler_workspace_archive()

        assert result["archived"] == 0
        assert result["skipped"] >= 1

    @pytest.mark.asyncio
    async def test_skips_archives_itself(self):
        """Le dossier _archives/ ne doit jamais tenter de s'auto-archiver."""
        from src.autonomy.ops_handlers import handler_workspace_archive

        archives = self.workspace / "_archives"
        archives.mkdir()
        old_time = time.time() - (90 * 86400)
        os.utime(str(archives), (old_time, old_time))

        result = await handler_workspace_archive()

        assert result["archived"] == 0
        assert (self.workspace / "_archives").exists()

    @pytest.mark.asyncio
    async def test_empty_workspace_succeeds(self):
        """Un workspace vide doit retourner success sans crash."""
        from src.autonomy.ops_handlers import handler_workspace_archive

        result = await handler_workspace_archive()

        assert result["success"] is True
        assert result["archived"] == 0

    @pytest.mark.asyncio
    async def test_mixed_old_and_recent(self):
        """Mélange de dossiers anciens et récents — seuls les anciens sont archivés."""
        from src.autonomy.ops_handlers import handler_workspace_archive

        self._make_old_dir("ancient-1", age_days=45)
        self._make_old_dir("ancient-2", age_days=90)
        self._make_recent_dir("new-1")
        self._make_recent_dir("new-2")

        result = await handler_workspace_archive()

        assert result["success"] is True
        assert result["archived"] == 2
        assert result["skipped"] == 2
        assert not (self.workspace / "ancient-1").exists()
        assert not (self.workspace / "ancient-2").exists()
        assert (self.workspace / "new-1").exists()
        assert (self.workspace / "new-2").exists()

    @pytest.mark.asyncio
    async def test_moved_list_contains_names(self):
        """La liste 'moved' doit contenir les noms et tailles."""
        from src.autonomy.ops_handlers import handler_workspace_archive

        self._make_old_dir("to-archive", age_days=60)

        result = await handler_workspace_archive()

        assert len(result["moved"]) == 1
        assert result["moved"][0]["name"] == "to-archive"
        assert "size_mb" in result["moved"][0]

    @pytest.mark.asyncio
    async def test_nonexistent_workspace_succeeds(self, tmp_path, monkeypatch):
        """Si le dossier workspace/ n'existe pas, doit réussir sans crash."""
        import shutil
        import src.autonomy.ops_handlers as mod

        shutil.rmtree(self.workspace)
        result = await mod.handler_workspace_archive()

        assert result["success"] is True
