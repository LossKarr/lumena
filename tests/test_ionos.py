"""
Tests unitaires pour le service IONOS deployer et les handlers ReAct.

Tous les tests mockent paramiko — aucune connexion réseau réelle.
"""
from __future__ import annotations

import json
import os
import stat as stat_mod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List
from unittest.mock import MagicMock, patch, PropertyMock

import pytest


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolate_ionos(tmp_path, monkeypatch):
    """Isolate every test: fresh deployer, temp data dir, mocked paramiko."""
    # Reset singletons
    import src.services.ionos_deployer as mod
    mod._fernet_cipher = None
    mod._SITES_PATH = tmp_path / "ionos_sites.json"
    mod._BACKUPS_DIR = tmp_path / "ionos_backups"

    # Reset handler singleton
    import src.reasoning.handlers.ionos as hmod
    hmod._deployer = None

    # Mock paramiko globally so no real SSH happens
    mock_paramiko = MagicMock()
    mock_transport = MagicMock()
    mock_sftp = MagicMock()
    mock_ssh_client = MagicMock()

    mock_paramiko.Transport.return_value = mock_transport
    mock_paramiko.SFTPClient.from_transport.return_value = mock_sftp
    mock_paramiko.SSHClient.return_value = mock_ssh_client
    mock_paramiko.AutoAddPolicy.return_value = MagicMock()
    mock_ssh_client.open_sftp.return_value = mock_sftp
    mock_ssh_client.get_transport.return_value = mock_transport
    mock_sftp.listdir.return_value = []
    mock_sftp.listdir_attr.return_value = []
    mock_sftp.stat.side_effect = FileNotFoundError  # for mkdir_p

    monkeypatch.setattr(mod, "_paramiko", mock_paramiko)

    yield {
        "tmp_path": tmp_path,
        "mock_paramiko": mock_paramiko,
        "mock_transport": mock_transport,
        "mock_sftp": mock_sftp,
        "mock_ssh_client": mock_ssh_client,
    }


@pytest.fixture
def deployer():
    from src.services.ionos_deployer import IonosDeployer
    return IonosDeployer()


# ═════════════════════════════════════════════════════════════════════════
# 1. Service IonosDeployer
# ═════════════════════════════════════════════════════════════════════════


class TestIonosDeployerSiteManagement:
    """Tests de gestion des sites (CRUD)."""

    def test_add_site(self, deployer):
        result = deployer.add_site(
            domain="test.fr",
            host="sftp.test.fr",
            user="admin",
            password="secret123",
        )
        assert result["status"] == "ok"
        assert result["domain"] == "test.fr"
        sites = deployer.list_sites()
        assert len(sites) == 1
        assert sites[0]["domain"] == "test.fr"

    def test_add_site_duplicate_replaces(self, deployer):
        deployer.add_site("test.fr", "h1", "u1", "p1")
        deployer.add_site("test.fr", "h2", "u2", "p2")
        sites = deployer.list_sites()
        assert len(sites) == 1
        assert sites[0]["host"] == "h2"

    def test_remove_site(self, deployer):
        deployer.add_site("test.fr", "h", "u", "p")
        deployer.remove_site("test.fr")
        assert deployer.list_sites() == []

    def test_remove_site_not_found(self, deployer):
        with pytest.raises(KeyError, match="non trouvé"):
            deployer.remove_site("nope.fr")

    def test_list_sites_empty(self, deployer):
        assert deployer.list_sites() == []

    def test_list_sites_multiple(self, deployer):
        deployer.add_site("a.fr", "h", "u", "p")
        deployer.add_site("b.fr", "h", "u", "p")
        assert len(deployer.list_sites()) == 2

    def test_list_sites_no_password_exposed(self, deployer):
        deployer.add_site("test.fr", "h", "u", "secret")
        for site in deployer.list_sites():
            assert "password" not in site
            assert "password_encrypted" not in site

    def test_get_site(self, deployer):
        deployer.add_site("test.fr", "h", "u", "p")
        site = deployer.get_site("test.fr")
        assert site is not None
        assert site["domain"] == "test.fr"
        assert "password" not in site

    def test_get_site_not_found(self, deployer):
        assert deployer.get_site("nope.fr") is None

    def test_add_site_empty_domain_raises(self, deployer):
        with pytest.raises(ValueError, match="vide"):
            deployer.add_site("", "h", "u", "p")

    def test_add_site_missing_fields_raises(self, deployer):
        with pytest.raises(ValueError, match="obligatoires"):
            deployer.add_site("test.fr", "", "u", "p")


class TestIonosDeployerSecurity:
    """Tests de sécurité."""

    def test_password_encryption(self, deployer, _isolate_ionos):
        deployer.add_site("test.fr", "h", "u", "my_secret_password")
        raw = json.loads(
            _isolate_ionos["tmp_path"].joinpath("ionos_sites.json").read_text()
        )
        encrypted = raw["sites"]["test.fr"]["password_encrypted"]
        assert "my_secret_password" not in encrypted
        assert encrypted.startswith("gAAAAA")

    def test_password_decryption(self, deployer):
        deployer.add_site("test.fr", "h", "u", "my_secret")
        creds = deployer._get_credentials("test.fr")
        assert creds["password"] == "my_secret"

    def test_path_traversal_blocked(self):
        from src.services.ionos_deployer import IonosDeployer
        with pytest.raises(ValueError, match="traversal"):
            IonosDeployer._validate_remote_path("../../etc/passwd", "/")

    def test_forbidden_files_filtered(self):
        from src.services.ionos_deployer import IonosDeployer
        assert IonosDeployer._is_forbidden(Path(".env")) is True
        assert IonosDeployer._is_forbidden(Path("dump.sql")) is True
        assert IonosDeployer._is_forbidden(Path("key.pem")) is True
        assert IonosDeployer._is_forbidden(Path("index.html")) is False

    def test_max_upload_size(self, deployer, tmp_path, monkeypatch):
        monkeypatch.setenv("LUMENA_IONOS_MAX_UPLOAD_MB", "0")
        deployer.add_site("test.fr", "h", "u", "p")

        project = tmp_path / "project"
        project.mkdir()
        (project / "index.html").write_text("<h1>Hi</h1>")

        import asyncio
        result = asyncio.get_event_loop().run_until_complete(
            deployer.deploy("test.fr", project)
        )
        assert not result.success
        assert "limite" in result.errors[0].lower() or "dépasse" in result.errors[0].lower()


class TestIonosDeployerDeploy:
    """Tests de déploiement."""

    def test_deploy_dry_run(self, deployer, tmp_path):
        deployer.add_site("test.fr", "h", "u", "p")

        project = tmp_path / "project"
        project.mkdir()
        (project / "index.html").write_text("<h1>Hello</h1>")
        (project / "style.css").write_text("body{}")

        import asyncio
        result = asyncio.get_event_loop().run_until_complete(
            deployer.deploy("test.fr", project, dry_run=True)
        )
        assert result.success
        assert result.dry_run is True
        assert result.uploaded == 2

    def test_deploy_result_structure(self, deployer, tmp_path, monkeypatch):
        monkeypatch.setenv("LUMENA_IONOS_BACKUP_BEFORE_DEPLOY", "0")
        deployer.add_site("test.fr", "h", "u", "p")

        project = tmp_path / "project"
        project.mkdir()
        (project / "index.html").write_text("<h1>Hey</h1>")

        import asyncio
        result = asyncio.get_event_loop().run_until_complete(
            deployer.deploy("test.fr", project)
        )
        assert result.success
        assert result.uploaded == 1
        assert result.total_bytes > 0
        assert result.duration_sec >= 0

    def test_deploy_nonexistent_dir(self, deployer):
        deployer.add_site("test.fr", "h", "u", "p")

        import asyncio
        result = asyncio.get_event_loop().run_until_complete(
            deployer.deploy("test.fr", Path("/nonexistent/dir"))
        )
        assert not result.success
        assert "introuvable" in result.errors[0].lower()

    def test_deploy_filters_forbidden(self, deployer, tmp_path, monkeypatch):
        monkeypatch.setenv("LUMENA_IONOS_BACKUP_BEFORE_DEPLOY", "0")
        deployer.add_site("test.fr", "h", "u", "p")

        project = tmp_path / "project"
        project.mkdir()
        (project / "index.html").write_text("<h1>OK</h1>")
        (project / ".env").write_text("SECRET=bad")

        import asyncio
        result = asyncio.get_event_loop().run_until_complete(
            deployer.deploy("test.fr", project)
        )
        assert result.success
        assert result.uploaded == 1
        assert result.skipped == 1

    def test_sftp_mock_upload(self, deployer, tmp_path, _isolate_ionos, monkeypatch):
        monkeypatch.setenv("LUMENA_IONOS_BACKUP_BEFORE_DEPLOY", "0")
        deployer.add_site("test.fr", "h", "u", "p")

        project = tmp_path / "project"
        project.mkdir()
        (project / "index.html").write_text("<h1>Test</h1>")

        import asyncio
        asyncio.get_event_loop().run_until_complete(
            deployer.deploy("test.fr", project)
        )

        sftp = _isolate_ionos["mock_sftp"]
        sftp.put.assert_called_once()
        args = sftp.put.call_args[0]
        assert args[0].endswith("index.html")

    def test_connection_failure(self, _isolate_ionos):
        _isolate_ionos["mock_ssh_client"].connect.side_effect = Exception("Connection refused")

        from src.services.ionos_deployer import IonosDeployer
        d = IonosDeployer()
        with pytest.raises(Exception, match="Connection refused"):
            d.add_site("test.fr", "h", "u", "p")


# ═════════════════════════════════════════════════════════════════════════
# 2. Handler definitions
# ═════════════════════════════════════════════════════════════════════════


class TestIonosHandlerDefs:
    """Tests sur les définitions de handlers."""

    def test_handler_defs_count(self):
        from src.reasoning.handlers.ionos import get_ionos_handler_defs
        defs = get_ionos_handler_defs()
        assert len(defs) == 7

    def test_handler_defs_names(self):
        from src.reasoning.handlers.ionos import get_ionos_handler_defs
        names = {d.name for d in get_ionos_handler_defs()}
        expected = {
            "deploy_to_ionos",
            "update_ionos_files",
            "ionos_add_site",
            "ionos_remove_site",
            "ionos_list_sites",
            "ionos_list_files",
            "ionos_delete_files",
        }
        assert names == expected

    def test_handler_defs_have_descriptions(self):
        from src.reasoning.handlers.ionos import get_ionos_handler_defs
        for d in get_ionos_handler_defs():
            assert d.description, f"{d.name} has empty description"
            assert len(d.description) > 10

    def test_handler_defs_parameters_valid(self):
        from src.reasoning.handlers.ionos import get_ionos_handler_defs
        for d in get_ionos_handler_defs():
            assert "properties" in d.parameters, f"{d.name} missing properties"
            assert "required" in d.parameters, f"{d.name} missing required"

    def test_handler_defs_category(self):
        from src.reasoning.handlers.ionos import get_ionos_handler_defs
        for d in get_ionos_handler_defs():
            assert d.category == "ionos"
            assert d.source_module == "ionos"


# ═════════════════════════════════════════════════════════════════════════
# 3. Handler execution (mocked)
# ═════════════════════════════════════════════════════════════════════════


class TestIonosHandlerExecution:
    """Tests d'exécution des handlers avec mock."""

    @pytest.fixture
    def ctx(self):
        from src.reasoning.handlers.context import HandlerContext
        return HandlerContext()

    @pytest.mark.asyncio
    async def test_list_sites_empty(self, ctx):
        from src.reasoning.handlers.ionos import ionos_list_sites_handler
        result = await ionos_list_sites_handler(ctx)
        assert result.success
        assert "Aucun site" in result.output

    @pytest.mark.asyncio
    async def test_add_site_missing_params(self, ctx):
        from src.reasoning.handlers.ionos import ionos_add_site_handler
        result = await ionos_add_site_handler(ctx, domain="", host="", user="", password="")
        assert not result.success
        assert "requis" in result.output.lower() or "requis" in (result.error or "").lower()

    @pytest.mark.asyncio
    async def test_add_and_list_site(self, ctx):
        from src.reasoning.handlers.ionos import (
            ionos_add_site_handler,
            ionos_list_sites_handler,
        )
        r1 = await ionos_add_site_handler(
            ctx, domain="test.fr", host="sftp.test.fr", user="u", password="p"
        )
        assert r1.success

        r2 = await ionos_list_sites_handler(ctx)
        assert r2.success
        assert "test.fr" in r2.output

    @pytest.mark.asyncio
    async def test_remove_site_not_found(self, ctx):
        from src.reasoning.handlers.ionos import ionos_remove_site_handler
        result = await ionos_remove_site_handler(ctx, domain="nope.fr")
        assert not result.success

    @pytest.mark.asyncio
    async def test_deploy_no_site(self, ctx):
        from src.reasoning.handlers.ionos import deploy_to_ionos_handler
        result = await deploy_to_ionos_handler(ctx, site="", project_dir="")
        assert not result.success
        assert "site" in result.output.lower() or "site" in (result.error or "").lower()

    @pytest.mark.asyncio
    async def test_delete_files_no_paths(self, ctx):
        from src.reasoning.handlers.ionos import ionos_delete_files_handler
        result = await ionos_delete_files_handler(ctx, site="test.fr", paths="")
        assert not result.success
