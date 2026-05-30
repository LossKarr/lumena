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
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

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
    # Reset des globals pymysql (BDD optionnelle) pour isoler chaque test
    mod._pymysql = None
    mod._pymysql_tried = False

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
        result = asyncio.run(
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
        result = asyncio.run(
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
        result = asyncio.run(
            deployer.deploy("test.fr", project)
        )
        assert result.success
        assert result.uploaded == 1
        assert result.total_bytes > 0
        assert result.duration_sec >= 0

    def test_deploy_nonexistent_dir(self, deployer):
        deployer.add_site("test.fr", "h", "u", "p")

        import asyncio
        result = asyncio.run(
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
        result = asyncio.run(
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
        asyncio.run(
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
        assert len(defs) == 39

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
            "ionos_test_site_database",
            "ionos_set_site_database",
            "ionos_clear_site_database",
            "ionos_db_list_tables",
            "ionos_db_describe_table",
            "ionos_db_select",
            "ionos_db_propose_write",
            "ionos_db_propose_delete",
            "ionos_db_get_config",
            "ionos_db_bridge_status",
            "ionos_db_get_write_config",
            "ionos_db_get_delete_config",
            "ionos_db_get_sandbox_config",
            "ionos_db_get_restore_config",
            "ionos_db_get_react_write_config",
            "ionos_db_get_react_delete_config",
            "ionos_db_list_snapshots",
            "ionos_db_list_pending_actions",
            "ionos_db_install_bridge",
            "ionos_db_set_sandbox_config",
            "ionos_db_set_write_config",
            "ionos_db_set_delete_config",
            "ionos_db_set_restore_config",
            "ionos_db_set_react_write_config",
            "ionos_db_set_react_delete_config",
            "ionos_db_create_sandbox_table",
            "ionos_db_get_sandbox_drop_config",
            "ionos_db_set_sandbox_drop_config",
            "ionos_db_propose_drop_sandbox_table",
            "ionos_db_get_sandbox_clear_config",
            "ionos_db_set_sandbox_clear_config",
            "ionos_db_propose_clear_sandbox_table",
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


# ═════════════════════════════════════════════════════════════════════════
# Étape 1 — Socle de configuration BDD IONOS (PAS de connexion réelle)
# ═════════════════════════════════════════════════════════════════════════


class TestIonosDatabaseConfigStep1:
    """Configuration BDD par site : stockage chiffré + statut non sensible.

    Aucune connexion DB réelle : on teste uniquement le modèle/config et
    l'absence de fuite de secret.
    """

    def test_set_site_database_encrypts_password(self, deployer, _isolate_ionos):
        deployer.add_site("test.fr", "h", "u", "sftp_pw")
        result = deployer.set_site_database(
            "test.fr", host="db.host.io", name="dbs1", user="dbu1",
            password="db_secret_pw", port=3306,
        )
        assert result["status"] == "ok"
        assert result["database_configured"] is True
        raw = json.loads(
            _isolate_ionos["tmp_path"].joinpath("ionos_sites.json").read_text()
        )
        db = raw["sites"]["test.fr"]["database"]
        assert "db_secret_pw" not in json.dumps(db)
        assert db["password_encrypted"].startswith("gAAAAA")
        # encryption_check présent au niveau fichier
        assert raw["encryption_check"] == "lumena_ionos_v1"

    def test_set_site_database_requires_existing_site(self, deployer):
        with pytest.raises(KeyError, match="non trouvé"):
            deployer.set_site_database("nope.fr", host="h", name="n", user="u", password="p")

    def test_set_site_database_missing_fields_raises(self, deployer):
        deployer.add_site("test.fr", "h", "u", "p")
        with pytest.raises(ValueError, match="obligatoires"):
            deployer.set_site_database("test.fr", host="", name="n", user="u", password="p")

    def test_set_site_database_empty_password_requires_existing(self, deployer):
        deployer.add_site("test.fr", "h", "u", "p")
        with pytest.raises(ValueError, match="password est obligatoire"):
            deployer.set_site_database("test.fr", host="h", name="n", user="u", password="")

    def test_set_site_database_empty_password_keeps_existing(self, deployer):
        deployer.add_site("test.fr", "h", "u", "p")
        deployer.set_site_database("test.fr", host="h", name="n", user="u", password="first_pw")
        first = deployer.get_site_database("test.fr", include_secret=True)["password"]
        # Re-set sans password → conserve le secret existant
        deployer.set_site_database("test.fr", host="h2", name="n2", user="u2", password="")
        kept = deployer.get_site_database("test.fr", include_secret=True)["password"]
        assert kept == first == "first_pw"

    def test_get_site_database_hides_secret_by_default(self, deployer):
        deployer.add_site("test.fr", "h", "u", "p")
        deployer.set_site_database("test.fr", host="db.h", name="dbs1", user="dbu1", password="zzz")
        db = deployer.get_site_database("test.fr")
        assert db is not None
        assert "password" not in db
        assert "password_encrypted" not in db
        assert db["host"] == "db.h"
        assert db["name"] == "dbs1"

    def test_get_site_database_include_secret_decrypts(self, deployer):
        deployer.add_site("test.fr", "h", "u", "p")
        deployer.set_site_database("test.fr", host="db.h", name="n", user="u", password="zzz")
        db = deployer.get_site_database("test.fr", include_secret=True)
        assert db["password"] == "zzz"

    def test_get_site_database_none_if_not_configured(self, deployer):
        deployer.add_site("test.fr", "h", "u", "p")
        assert deployer.get_site_database("test.fr") is None

    def test_clear_site_database(self, deployer):
        deployer.add_site("test.fr", "h", "u", "p")
        deployer.set_site_database("test.fr", host="db.h", name="n", user="u", password="zzz")
        result = deployer.clear_site_database("test.fr")
        assert result["database_configured"] is False
        assert deployer.get_site_database("test.fr") is None
        # le site SFTP reste intact
        assert deployer.get_site("test.fr") is not None

    def test_get_site_exposes_db_config_non_sensitive(self, deployer):
        deployer.add_site("test.fr", "h", "u", "p")
        deployer.set_site_database("test.fr", host="db.h", name="dbs1", user="dbu1", password="zzz")
        site = deployer.get_site("test.fr")
        assert site["database_configured"] is True
        assert site["database_host"] == "db.h"
        assert site["database_name"] == "dbs1"
        assert site["database_user"] == "dbu1"
        assert "password" not in json.dumps(site)
        assert "password_encrypted" not in json.dumps(site)

    def test_list_sites_public_hides_db_host_user_name(self, deployer):
        """Route PUBLIQUE : statut booléen seulement, jamais host/user/name."""
        deployer.add_site("test.fr", "h", "u", "p")
        deployer.set_site_database("test.fr", host="db.h", name="dbs1", user="dbu1", password="zzz")
        sites = deployer.list_sites()
        s = sites[0]
        assert s["database_configured"] is True
        assert "database_host" not in s
        assert "database_name" not in s
        assert "database_user" not in s
        assert "password" not in json.dumps(s)
        assert "password_encrypted" not in json.dumps(s)

    def test_list_sites_db_not_configured_flag_false(self, deployer):
        deployer.add_site("test.fr", "h", "u", "p")
        s = deployer.list_sites()[0]
        assert s["database_configured"] is False
        assert "database_host" not in s

    def test_existing_site_without_database_still_valid(self, deployer):
        """Compat : un site sans `database` reste listable/déployable."""
        deployer.add_site("legacy.fr", "h", "u", "p")
        sites = deployer.list_sites()
        assert sites[0]["domain"] == "legacy.fr"
        assert sites[0]["database_configured"] is False


# ═════════════════════════════════════════════════════════════════════════
# Étape 2 — Test de connexion BDD IONOS (PING uniquement, aucune lecture)
# ═════════════════════════════════════════════════════════════════════════


def _configure_db(deployer):
    deployer.add_site("test.fr", "h", "u", "sftp_pw")
    deployer.set_site_database(
        "test.fr", host="db.host.io", name="dbs1", user="dbu1", password="db_pw",
    )


class TestIonosDatabaseTestConnectionStep2:
    """Connexion réelle via PyMySQL (mockée) : PING seulement, pas de SELECT."""

    def test_connection_success_updates_last_check(self, deployer, _isolate_ionos):
        import src.services.ionos_deployer as mod
        _configure_db(deployer)
        mock_pymysql = MagicMock()
        mock_conn = MagicMock()
        mock_pymysql.connect.return_value = mock_conn
        mod._pymysql = mock_pymysql
        mod._pymysql_tried = True

        result = deployer.test_database_connection("test.fr")
        assert result["ok"] is True
        assert result["configured"] is True
        assert "latency_ms" in result
        # PING appelé, AUCUN SELECT/cursor exécuté
        mock_conn.ping.assert_called_once()
        assert not mock_conn.cursor.called
        mock_conn.close.assert_called_once()
        # last_check persisté et non sensible
        db = deployer.get_site_database("test.fr")
        assert db["last_check"]["ok"] is True
        assert "password" not in json.dumps(db["last_check"])

    def test_connection_uses_decrypted_password_only_for_connect(self, deployer):
        import src.services.ionos_deployer as mod
        _configure_db(deployer)
        mock_pymysql = MagicMock()
        mock_pymysql.connect.return_value = MagicMock()
        mod._pymysql = mock_pymysql
        mod._pymysql_tried = True

        deployer.test_database_connection("test.fr")
        # Le password déchiffré est passé à connect(), jamais stocké/retourné
        _, kwargs = mock_pymysql.connect.call_args
        assert kwargs["password"] == "db_pw"
        assert kwargs["host"] == "db.host.io"
        assert kwargs["connect_timeout"] >= 1

    def test_connection_failure_is_clean(self, deployer):
        import src.services.ionos_deployer as mod
        _configure_db(deployer)
        mock_pymysql = MagicMock()
        mock_pymysql.connect.side_effect = Exception("Can't connect (timed out)")
        mod._pymysql = mock_pymysql
        mod._pymysql_tried = True

        result = deployer.test_database_connection("test.fr")
        assert result["ok"] is False
        assert "timed out" in result["error"]
        db = deployer.get_site_database("test.fr")
        assert db["last_check"]["ok"] is False

    def test_connection_dns_error_gives_clear_message(self, deployer):
        """getaddrinfo failed → message DNS clair, erreur brute hors du message."""
        import src.services.ionos_deployer as mod
        _configure_db(deployer)
        mock_pymysql = MagicMock()
        mock_pymysql.connect.side_effect = Exception(
            "(2003, \"Can't connect to MySQL server on 'db5020513717.hosting-data.io' "
            "([Errno 11001] getaddrinfo failed)\")"
        )
        mod._pymysql = mock_pymysql
        mod._pymysql_tried = True

        result = deployer.test_database_connection("test.fr")
        assert result["ok"] is False
        assert "DNS" in result["message"]
        assert "nom d'hôte" in result["message"].lower()
        # l'erreur technique reste disponible côté last_check (serveur), pas dans le message
        assert "getaddrinfo" not in result["message"]
        db = deployer.get_site_database("test.fr")
        assert "getaddrinfo" in db["last_check"]["error"]

    def test_internal_ionos_host_dns_fail_gives_ionos_message(self, deployer, monkeypatch):
        """Hôte *.hosting-data.io injoignable → message IONOS interne (pas tunnel)."""
        import src.services.ionos_deployer as mod
        monkeypatch.setenv("LUMENA_IONOS_DB_TUNNEL", "off")
        deployer.add_site("test.fr", "h", "u", "sftp_pw")
        deployer.set_site_database(
            "test.fr", host="db5020513717.hosting-data.io", name="dbs1", user="dbu1", password="db_pw",
        )
        mock_pymysql = MagicMock()
        mock_pymysql.connect.side_effect = Exception("[Errno 11001] getaddrinfo failed")
        mod._pymysql = mock_pymysql
        mod._pymysql_tried = True

        result = deployer.test_database_connection("test.fr")
        assert result["ok"] is False
        assert "IONOS" in result["message"]
        assert "getaddrinfo" not in result["message"]
        assert "getaddrinfo" in deployer.get_site_database("test.fr")["last_check"]["error"]

    def test_dns_fail_falls_back_to_ssh_tunnel_success(self, deployer):
        """Connexion directe échoue (DNS) → repli tunnel SSH réussit."""
        import src.services.ionos_deployer as mod
        deployer.add_site("test.fr", "sftp.host", "sftpuser", "sftp_pw")
        deployer.set_site_database(
            "test.fr", host="db5020513717.hosting-data.io", name="dbs1", user="dbu1", password="db_pw",
        )

        def _connect(**kw):
            if kw.get("host") == "127.0.0.1":
                return MagicMock()  # via tunnel → OK
            raise Exception("[Errno 11001] getaddrinfo failed")  # directe → DNS fail

        mock_pymysql = MagicMock()
        mock_pymysql.connect.side_effect = _connect
        mod._pymysql = mock_pymysql
        mod._pymysql_tried = True
        # paramiko est déjà mocké par la fixture _isolate_ionos

        result = deployer.test_database_connection("test.fr")
        assert result["ok"] is True
        assert result["via"] == "ssh_tunnel"

    def test_tunnel_mode_off_no_fallback(self, deployer, monkeypatch):
        import src.services.ionos_deployer as mod
        monkeypatch.setenv("LUMENA_IONOS_DB_TUNNEL", "off")
        deployer.add_site("test.fr", "sftp.host", "u", "p")
        deployer.set_site_database("test.fr", host="db.h", name="n", user="u", password="zzz")
        mock_pymysql = MagicMock()
        mock_pymysql.connect.side_effect = Exception("getaddrinfo failed")
        mod._pymysql = mock_pymysql
        mod._pymysql_tried = True
        result = deployer.test_database_connection("test.fr")
        assert result["ok"] is False
        assert result["via"] == "direct"

    def test_connection_error_redacts_password(self, deployer):
        """Si une erreur contient le mot de passe, il est rédigé avant stockage/retour."""
        import src.services.ionos_deployer as mod
        _configure_db(deployer)
        mock_pymysql = MagicMock()
        # Erreur hostile qui recrache le mot de passe en clair.
        mock_pymysql.connect.side_effect = Exception(
            "Access denied using password 'db_pw' for user 'dbu1'"
        )
        mod._pymysql = mock_pymysql
        mod._pymysql_tried = True

        result = deployer.test_database_connection("test.fr")
        assert result["ok"] is False
        assert "db_pw" not in result["error"]
        assert "***" in result["error"]
        # Et persisté rédigé dans last_check.
        db = deployer.get_site_database("test.fr")
        assert "db_pw" not in json.dumps(db["last_check"])

    def test_connection_no_database_configured(self, deployer):
        deployer.add_site("test.fr", "h", "u", "p")
        result = deployer.test_database_connection("test.fr")
        assert result["configured"] is False
        assert result["ok"] is False

    def test_connection_pymysql_absent_degrades(self, deployer):
        import src.services.ionos_deployer as mod
        _configure_db(deployer)
        # pymysql absent : import déjà tenté et échoué
        mod._pymysql = None
        mod._pymysql_tried = True

        result = deployer.test_database_connection("test.fr")
        assert result["ok"] is False
        assert result["degraded"] is True
        assert "pymysql" in result["error"]

    def test_connection_unknown_site_raises(self, deployer):
        with pytest.raises(KeyError, match="non trouvé"):
            deployer.test_database_connection("nope.fr")

    def test_test_result_never_exposes_password(self, deployer):
        import src.services.ionos_deployer as mod
        _configure_db(deployer)
        mock_pymysql = MagicMock()
        mock_pymysql.connect.return_value = MagicMock()
        mod._pymysql = mock_pymysql
        mod._pymysql_tried = True

        result = deployer.test_database_connection("test.fr")
        blob = json.dumps(result)
        assert "db_pw" not in blob
        assert "password" not in blob
        assert "password_encrypted" not in blob


class TestIonosTestDatabaseHandlerStep2:
    """Handler ReAct ionos_test_site_database."""

    @pytest.fixture
    def ctx(self):
        return MagicMock()

    def test_handler_registered_with_category(self):
        from src.reasoning.handlers.ionos import get_ionos_handler_defs
        d = next(d for d in get_ionos_handler_defs() if d.name == "ionos_test_site_database")
        assert d.category == "ionos"
        assert d.source_module == "ionos"

    @pytest.mark.asyncio
    async def test_handler_ok(self, ctx, deployer):
        import src.services.ionos_deployer as mod
        import src.reasoning.handlers.ionos as hmod
        _configure_db(deployer)
        hmod._deployer = deployer
        mock_pymysql = MagicMock()
        mock_pymysql.connect.return_value = MagicMock()
        mod._pymysql = mock_pymysql
        mod._pymysql_tried = True

        from src.reasoning.handlers.ionos import ionos_test_site_database_handler
        result = await ionos_test_site_database_handler(ctx, site="test.fr")
        assert result.success
        assert "ok" in result.output.lower()

    @pytest.mark.asyncio
    async def test_handler_no_db_configured(self, ctx, deployer):
        import src.reasoning.handlers.ionos as hmod
        deployer.add_site("test.fr", "h", "u", "p")
        hmod._deployer = deployer
        from src.reasoning.handlers.ionos import ionos_test_site_database_handler
        result = await ionos_test_site_database_handler(ctx, site="test.fr")
        assert not result.success

    @pytest.mark.asyncio
    async def test_handler_no_site(self, ctx, deployer, monkeypatch):
        import src.reasoning.handlers.ionos as hmod
        hmod._deployer = deployer
        monkeypatch.delenv("LUMENA_IONOS_DEFAULT_SITE", raising=False)
        from src.reasoning.handlers.ionos import ionos_test_site_database_handler
        result = await ionos_test_site_database_handler(ctx, site="")
        assert not result.success


class TestIonosSetClearHandlersStep25:
    """Handlers ReAct ionos_set_site_database / ionos_clear_site_database."""

    @pytest.fixture
    def ctx(self):
        return MagicMock()

    def test_handlers_registered(self):
        from src.reasoning.handlers.ionos import get_ionos_handler_defs
        names = {d.name for d in get_ionos_handler_defs()}
        assert "ionos_set_site_database" in names
        assert "ionos_clear_site_database" in names
        for d in get_ionos_handler_defs():
            if d.name in ("ionos_set_site_database", "ionos_clear_site_database"):
                assert d.category == "ionos"
                assert d.source_module == "ionos"

    @pytest.mark.asyncio
    async def test_set_handler_ok_no_secret(self, ctx, deployer):
        import src.reasoning.handlers.ionos as hmod
        deployer.add_site("test.fr", "h", "u", "p")
        hmod._deployer = deployer
        from src.reasoning.handlers.ionos import ionos_set_site_database_handler
        result = await ionos_set_site_database_handler(
            ctx, site="test.fr", host="db.h", name="dbs1", user="dbu1", password="zzz",
        )
        assert result.success
        assert "zzz" not in result.output
        assert "password" not in result.output.lower() or "jamais affiché" in result.output.lower()
        # bien persisté
        assert deployer.get_site_database("test.fr")["host"] == "db.h"

    @pytest.mark.asyncio
    async def test_set_handler_keeps_password_on_empty(self, ctx, deployer):
        import src.reasoning.handlers.ionos as hmod
        deployer.add_site("test.fr", "h", "u", "p")
        hmod._deployer = deployer
        from src.reasoning.handlers.ionos import ionos_set_site_database_handler
        await ionos_set_site_database_handler(
            ctx, site="test.fr", host="db.h", name="n", user="u", password="first",
        )
        await ionos_set_site_database_handler(
            ctx, site="test.fr", host="db.h2", name="n2", user="u2", password="",
        )
        kept = deployer.get_site_database("test.fr", include_secret=True)["password"]
        assert kept == "first"

    @pytest.mark.asyncio
    async def test_set_handler_unknown_site(self, ctx, deployer):
        import src.reasoning.handlers.ionos as hmod
        hmod._deployer = deployer
        from src.reasoning.handlers.ionos import ionos_set_site_database_handler
        result = await ionos_set_site_database_handler(
            ctx, site="nope.fr", host="h", name="n", user="u", password="p",
        )
        assert not result.success

    @pytest.mark.asyncio
    async def test_clear_handler_ok(self, ctx, deployer):
        import src.reasoning.handlers.ionos as hmod
        deployer.add_site("test.fr", "h", "u", "p")
        deployer.set_site_database("test.fr", host="db.h", name="n", user="u", password="zzz")
        hmod._deployer = deployer
        from src.reasoning.handlers.ionos import ionos_clear_site_database_handler
        result = await ionos_clear_site_database_handler(ctx, site="test.fr")
        assert result.success
        assert deployer.get_site_database("test.fr") is None
        # site SFTP intact
        assert deployer.get_site("test.fr") is not None


class TestIonosDatabaseConfigRoutesStep25:
    """Routes admin set/get/delete database : auth + zéro secret."""

    @pytest.mark.asyncio
    async def test_set_route_ok_no_secret(self, deployer):
        import httpx
        deployer.add_site("test.fr", "h", "u", "p")
        app, _ = _make_ionos_app(deployer)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/ionos/sites/test.fr/database",
                json={"host": "db.h", "name": "dbs1", "user": "dbu1", "password": "zzz"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["configured"] is True
        assert body["host"] == "db.h"
        blob = json.dumps(body)
        assert "zzz" not in blob
        assert "password" not in blob
        assert "password_encrypted" not in blob

    @pytest.mark.asyncio
    async def test_set_route_requires_admin(self, deployer):
        import httpx
        from fastapi import FastAPI
        from web.routes import ionos as ionos_routes
        deployer.add_site("test.fr", "h", "u", "p")
        app = FastAPI()
        app.include_router(ionos_routes.router)
        ionos_routes._deployer = deployer
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/ionos/sites/test.fr/database",
                json={"host": "h", "name": "n", "user": "u", "password": "p"},
            )
        assert resp.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_get_route_non_sensitive(self, deployer):
        import httpx
        deployer.add_site("test.fr", "h", "u", "p")
        deployer.set_site_database("test.fr", host="db.h", name="dbs1", user="dbu1", password="zzz")
        app, _ = _make_ionos_app(deployer)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/ionos/sites/test.fr/database")
        assert resp.status_code == 200
        body = resp.json()
        assert body["configured"] is True
        assert body["host"] == "db.h"
        assert body["user"] == "dbu1"
        assert "zzz" not in json.dumps(body)
        assert "password" not in json.dumps(body)

    @pytest.mark.asyncio
    async def test_get_route_not_configured(self, deployer):
        import httpx
        deployer.add_site("test.fr", "h", "u", "p")
        app, _ = _make_ionos_app(deployer)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/ionos/sites/test.fr/database")
        assert resp.status_code == 200
        assert resp.json()["configured"] is False

    @pytest.mark.asyncio
    async def test_set_route_missing_fields_400(self, deployer):
        import httpx
        deployer.add_site("test.fr", "h", "u", "p")
        app, _ = _make_ionos_app(deployer)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            # password vide + pas de BDD existante → ValueError → 400
            resp = await client.post(
                "/api/ionos/sites/test.fr/database",
                json={"host": "h", "name": "n", "user": "u", "password": ""},
            )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_delete_route_ok(self, deployer):
        import httpx
        deployer.add_site("test.fr", "h", "u", "p")
        deployer.set_site_database("test.fr", host="db.h", name="n", user="u", password="zzz")
        app, _ = _make_ionos_app(deployer)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.delete("/api/ionos/sites/test.fr/database")
        assert resp.status_code == 200
        assert resp.json()["configured"] is False
        assert deployer.get_site_database("test.fr") is None
        # site SFTP toujours présent
        assert deployer.get_site("test.fr") is not None

    @pytest.mark.asyncio
    async def test_set_route_unknown_site_404(self, deployer):
        import httpx
        app, _ = _make_ionos_app(deployer)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/ionos/sites/nope.fr/database",
                json={"host": "h", "name": "n", "user": "u", "password": "p"},
            )
        assert resp.status_code == 404


def test_redact_db_error_unit():
    """Le helper de rédaction retire le secret, neutralise password= et borne."""
    from src.services.ionos_deployer import _redact_db_error
    assert "secretpw" not in _redact_db_error("denied for 'secretpw'", "secretpw")
    assert "***" in _redact_db_error("denied for 'secretpw'", "secretpw")
    # token password= neutralisé même sans secret fourni
    assert "hunter2" not in _redact_db_error("conn password=hunter2 host=x")
    # bornage longueur
    assert len(_redact_db_error("x" * 1000)) <= 300


def test_classify_db_error_unit():
    """Le classifieur traduit les erreurs techniques en messages clairs."""
    from src.services.ionos_deployer import _classify_db_error
    assert "DNS" in _classify_db_error("[Errno 11001] getaddrinfo failed")
    assert "DNS" in _classify_db_error("Name or service not known")
    assert "Délai" in _classify_db_error("connection timed out")
    assert "refusé" in _classify_db_error("(1045, Access denied for user)").lower()
    assert "introuvable" in _classify_db_error("(1049, Unknown database 'x')").lower()
    # fallback générique, jamais l'erreur brute
    out = _classify_db_error("some weird internal error xyz")
    assert "xyz" not in out


# ═════════════════════════════════════════════════════════════════════════
# Étape 2 — Route admin POST /api/ionos/sites/{domain}/database/test
# ═════════════════════════════════════════════════════════════════════════


def _make_ionos_app(deployer):
    """App FastAPI isolée avec auth admin neutralisée et deployer injecté."""
    import httpx  # noqa: F401  (présence vérifiée)
    from fastapi import FastAPI
    from web.routes import ionos as ionos_routes
    from web.routes import deps
    app = FastAPI()
    app.include_router(ionos_routes.router)
    app.dependency_overrides[deps.verify_admin_token] = lambda: None
    ionos_routes._deployer = deployer  # injecte le deployer isolé du test
    return app, ionos_routes


class TestIonosDatabaseTestRouteStep2:
    """Route admin de test de connexion BDD : auth + statut + zéro secret."""

    @pytest.mark.asyncio
    async def test_route_requires_admin(self, deployer):
        """Sans override d'auth, la route doit exiger le token admin (401/403)."""
        import httpx
        from fastapi import FastAPI
        from web.routes import ionos as ionos_routes
        app = FastAPI()
        app.include_router(ionos_routes.router)
        ionos_routes._deployer = deployer
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post("/api/ionos/sites/test.fr/database/test")
        assert resp.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_route_ok_no_secret_in_response(self, deployer):
        import httpx
        import src.services.ionos_deployer as mod
        _configure_db(deployer)
        mock_pymysql = MagicMock()
        mock_pymysql.connect.return_value = MagicMock()
        mod._pymysql = mock_pymysql
        mod._pymysql_tried = True

        app, _ = _make_ionos_app(deployer)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post("/api/ionos/sites/test.fr/database/test")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["configured"] is True
        blob = json.dumps(body)
        assert "db_pw" not in blob
        assert "password" not in blob
        assert "password_encrypted" not in blob

    @pytest.mark.asyncio
    async def test_route_failure_redacts_secret(self, deployer):
        import httpx
        import src.services.ionos_deployer as mod
        _configure_db(deployer)
        mock_pymysql = MagicMock()
        mock_pymysql.connect.side_effect = Exception(
            "Access denied using password 'db_pw'"
        )
        mod._pymysql = mock_pymysql
        mod._pymysql_tried = True

        app, _ = _make_ionos_app(deployer)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post("/api/ionos/sites/test.fr/database/test")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is False
        assert "db_pw" not in json.dumps(body)

    @pytest.mark.asyncio
    async def test_route_no_database_configured(self, deployer):
        import httpx
        deployer.add_site("test.fr", "h", "u", "p")
        app, _ = _make_ionos_app(deployer)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post("/api/ionos/sites/test.fr/database/test")
        assert resp.status_code == 200
        body = resp.json()
        assert body["configured"] is False
        assert body["ok"] is False

    @pytest.mark.asyncio
    async def test_route_unknown_site_404(self, deployer):
        import httpx
        app, _ = _make_ionos_app(deployer)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post("/api/ionos/sites/nope.fr/database/test")
        assert resp.status_code == 404


# ═════════════════════════════════════════════════════════════════════════
# Étape 3B — Bridge PHP : install / remove / status via SFTP (service only)
# ═════════════════════════════════════════════════════════════════════════


class TestIonosDatabaseBridgeStep3B:
    """Déploiement/retrait/statut du squelette bridge. SFTP mocké, aucune BDD réelle."""

    @pytest.mark.asyncio
    async def test_install_uploads_bridge_and_index(self, deployer, _isolate_ionos):
        _configure_db(deployer)
        result = await deployer.install_database_bridge("test.fr")
        assert result["ok"] is True
        assert result["installed"] is True
        # Les 2 fichiers .lumena ont bien été poussés via SFTP (mock_sftp.put).
        put_targets = [c.args[1] for c in _isolate_ionos["mock_sftp"].put.call_args_list]
        assert any(t.startswith("/.lumena/db-") and t.endswith(".php") for t in put_targets)
        assert any(t == "/.lumena/index.php" for t in put_targets)
        # JAMAIS de .htaccess.
        assert not any(t.endswith(".htaccess") for t in put_targets)

    @pytest.mark.asyncio
    async def test_install_stores_metadata_without_plaintext_secret(self, deployer):
        _configure_db(deployer)
        result = await deployer.install_database_bridge("test.fr")
        bridge = deployer._sites["test.fr"]["database"]["bridge"]
        assert bridge["installed"] is True
        assert bridge["path"].startswith("/.lumena/db-")
        assert bridge["version"] == "9"
        assert bridge["checksum"].startswith("sha256:")
        # secret chiffré présent, jamais en clair
        assert bridge["secret_encrypted"].startswith("gAAAAA")
        assert "secret" not in result  # le retour n'expose aucun secret
        assert "secret_encrypted" not in json.dumps(result)

    @pytest.mark.asyncio
    async def test_install_lands_under_site_root_not_sftp_root(self, deployer, _isolate_ionos):
        """Régression : le bridge doit atterrir SOUS le root du site (docroot),
        pas en absolu à la racine SFTP. Root arbitraire → <root>/.lumena/...
        (valeur d'exemple neutre : youyout.fr / /youyout)."""
        deployer.add_site("youyout.fr", "h", "u", "sftp_pw", root="/youyout")
        deployer.set_site_database("youyout.fr", host="db.h", name="n", user="u", password="z")
        await deployer.install_database_bridge("youyout.fr")
        put_targets = [c.args[1] for c in _isolate_ionos["mock_sftp"].put.call_args_list]
        # doit être SOUS /youyout, jamais /.lumena absolu à la racine
        assert any(t == "/youyout/.lumena/index.php" for t in put_targets), put_targets
        assert any(t.startswith("/youyout/.lumena/db-") for t in put_targets), put_targets
        assert not any(t.startswith("/.lumena/") for t in put_targets), put_targets

    @pytest.mark.asyncio
    async def test_install_requires_configured_database(self, deployer):
        deployer.add_site("test.fr", "h", "u", "p")  # site sans BDD
        result = await deployer.install_database_bridge("test.fr")
        assert result["ok"] is False
        assert result["installed"] is False

    @pytest.mark.asyncio
    async def test_checksum_is_deterministic_and_recomputable(self, deployer):
        from src.services.ionos_deployer import _render_bridge_php, _bridge_checksum, _decrypt
        _configure_db(deployer)
        await deployer.install_database_bridge("test.fr")
        bridge = deployer._sites["test.fr"]["database"]["bridge"]
        secret = _decrypt(bridge["secret_encrypted"])
        recomputed = _bridge_checksum(_render_bridge_php(secret, bridge["version"]))
        assert recomputed == bridge["checksum"]

    @pytest.mark.asyncio
    async def test_remove_deletes_files_and_purges_config(self, deployer, _isolate_ionos):
        _configure_db(deployer)
        await deployer.install_database_bridge("test.fr")
        bridge_path = deployer._sites["test.fr"]["database"]["bridge"]["path"]
        result = await deployer.remove_database_bridge("test.fr")
        assert result["removed"] is True
        # SFTP remove appelé sur les chemins du bridge
        removed = [c.args[0] for c in _isolate_ionos["mock_sftp"].remove.call_args_list]
        assert any(p == bridge_path for p in removed)
        # config bridge purgée, mais BDD + site SFTP intacts
        assert "bridge" not in deployer._sites["test.fr"]["database"]
        assert deployer.get_site_database("test.fr") is not None
        assert deployer.get_site("test.fr") is not None

    @pytest.mark.asyncio
    async def test_remove_when_no_bridge(self, deployer):
        _configure_db(deployer)
        result = await deployer.remove_database_bridge("test.fr")
        assert result["removed"] is False

    @pytest.mark.asyncio
    async def test_status_installed_non_sensitive(self, deployer):
        from src.services.ionos_deployer import RemoteFile
        _configure_db(deployer)
        await deployer.install_database_bridge("test.fr")
        path = deployer._sites["test.fr"]["database"]["bridge"]["path"]
        fname = path.rsplit("/", 1)[-1]
        deployer.list_remote = AsyncMock(return_value=[RemoteFile(path=fname, size=10, is_dir=False)])
        st = await deployer.get_database_bridge_status("test.fr")
        assert st["installed"] is True
        assert st["file_present"] is True
        assert st["orphan"] is None
        assert "secret" not in json.dumps(st)
        assert "secret_encrypted" not in json.dumps(st)

    @pytest.mark.asyncio
    async def test_status_orphan_config_without_file(self, deployer):
        _configure_db(deployer)
        await deployer.install_database_bridge("test.fr")
        deployer.list_remote = AsyncMock(return_value=[])  # aucun fichier distant
        st = await deployer.get_database_bridge_status("test.fr")
        assert st["installed"] is True
        assert st["file_present"] is False
        assert st["orphan"] == "config_without_file"

    @pytest.mark.asyncio
    async def test_status_orphan_untracked_file(self, deployer):
        from src.services.ionos_deployer import RemoteFile
        _configure_db(deployer)  # BDD configurée mais bridge NON installé
        deployer.list_remote = AsyncMock(
            return_value=[RemoteFile(path="db-deadbeef.php", size=10, is_dir=False)]
        )
        st = await deployer.get_database_bridge_status("test.fr")
        assert st["installed"] is False
        assert st["orphan"] == "untracked_bridge_file"

    @pytest.mark.asyncio
    async def test_install_unknown_site_raises(self, deployer):
        with pytest.raises(KeyError, match="non trouvé"):
            await deployer.install_database_bridge("nope.fr")


class TestIonosDbReadStep3D:
    """Lecture read-only via bridge : validation Lumena + routage (bridge mocké)."""

    def _install(self, deployer):
        import asyncio as _aio
        _configure_db(deployer)
        _aio.run(deployer.install_database_bridge("test.fr"))

    def _ok(self, payload):
        def fake(*a, **k):
            d = {"_http_status": 200, "ok": True}
            d.update(payload)
            return d
        return fake

    # --- validation AVANT réseau ---
    def test_describe_rejects_bad_table(self, deployer):
        r = deployer.db_describe_table("youyout.fr", "users; DROP")
        assert r["ok"] is False and r["error"] == "bad_table"

    def test_select_rejects_bad_table(self, deployer):
        r = deployer.db_select("youyout.fr", "a b")
        assert r["ok"] is False and r["error"] == "bad_table"

    def test_select_rejects_bad_column(self, deployer):
        r = deployer.db_select("youyout.fr", "users", columns=["ok", "bad col"])
        assert r["ok"] is False and r["error"] == "bad_column"

    def test_select_rejects_bad_where_key(self, deployer):
        r = deployer.db_select("youyout.fr", "users", where={"id; DROP": 1})
        assert r["ok"] is False and r["error"] == "bad_column"

    def test_select_clamps_limit(self, deployer, monkeypatch):
        self._install(deployer)
        captured = {}
        def fake(domain, secret, path, op, body, timeout, ts=None, nonce=None):
            captured["body"] = body
            return {"_http_status": 200, "ok": True, "columns": [], "rows": [], "count": 0, "truncated": False}
        monkeypatch.setattr(deployer, "_bridge_request", fake)
        deployer.db_select("test.fr", "users", limit=99999)
        assert captured["body"]["limit"] == 1000
        deployer.db_select("test.fr", "users", limit=0)
        assert captured["body"]["limit"] == 1

    # --- routage via bridge (mock) ---
    def test_list_tables_via_bridge(self, deployer, monkeypatch):
        self._install(deployer)
        monkeypatch.setattr(deployer, "_bridge_request",
                            self._ok({"tables": ["users", "orders"], "truncated": False}))
        r = deployer.db_list_tables("test.fr")
        assert r["ok"] is True and r["tables"] == ["users", "orders"]
        assert "_http_status" not in r

    def test_describe_via_bridge(self, deployer, monkeypatch):
        self._install(deployer)
        monkeypatch.setattr(deployer, "_bridge_request",
                            self._ok({"columns": [{"field": "id", "type": "int"}]}))
        r = deployer.db_describe_table("test.fr", "users")
        assert r["ok"] is True and r["columns"][0]["field"] == "id"

    def test_select_via_bridge(self, deployer, monkeypatch):
        self._install(deployer)
        monkeypatch.setattr(deployer, "_bridge_request",
                            self._ok({"columns": ["id"], "rows": [["1"]], "count": 1, "truncated": False}))
        r = deployer.db_select("test.fr", "users", columns=["id"], where={"id": 1}, limit=10)
        assert r["ok"] is True and r["rows"] == [["1"]]

    def test_select_op_sealed_with_creds(self, deployer, monkeypatch):
        """Le body envoyé contient des creds scellés (iv/ct/tag), jamais en clair."""
        self._install(deployer)
        captured = {}
        def fake(domain, secret, path, op, body, timeout, ts=None, nonce=None):
            captured["body"] = body; captured["op"] = op
            return {"_http_status": 200, "ok": True, "columns": [], "rows": [], "count": 0, "truncated": False}
        monkeypatch.setattr(deployer, "_bridge_request", fake)
        deployer.db_select("test.fr", "users")
        assert captured["op"] == "db_select"
        assert set(captured["body"]["creds"]) == {"iv", "ct", "tag"}
        assert "password" not in json.dumps(captured["body"])
        assert "db_pw" not in json.dumps(captured["body"])

    # --- états dégradés ---
    def test_no_database(self, deployer):
        deployer.add_site("test.fr", "h", "u", "p")
        r = deployer.db_list_tables("test.fr")
        assert r["ok"] is False and r["error"] == "no_database"

    def test_bridge_not_installed(self, deployer):
        _configure_db(deployer)
        r = deployer.db_list_tables("test.fr")
        assert r["ok"] is False and r["error"] == "bridge_not_installed"

    def test_upgrade_required_on_old_bridge(self, deployer):
        self._install(deployer)
        deployer._sites["test.fr"]["database"]["bridge"]["version"] = "2"
        r = deployer.db_list_tables("test.fr")
        assert r.get("upgrade_required") is True
        assert r["ok"] is False


class TestIonosDbReadExposure3E:
    """3E : handlers ReAct + routes admin read-only (service mocké)."""

    @pytest.fixture
    def ctx(self):
        return MagicMock()

    def test_handlers_registered(self):
        from src.reasoning.handlers.ionos import get_ionos_handler_defs
        names = {d.name for d in get_ionos_handler_defs()}
        for n in ("ionos_db_list_tables", "ionos_db_describe_table", "ionos_db_select"):
            assert n in names
        for d in get_ionos_handler_defs():
            if d.name.startswith("ionos_db_"):
                assert d.category == "ionos" and d.source_module == "ionos"

    @pytest.mark.asyncio
    async def test_handler_list_tables_ok(self, ctx, deployer, monkeypatch):
        import src.reasoning.handlers.ionos as hmod
        hmod._deployer = deployer
        monkeypatch.setattr(deployer, "db_list_tables",
                            lambda dom: {"ok": True, "tables": ["announcements", "users"]})
        from src.reasoning.handlers.ionos import ionos_db_list_tables_handler
        r = await ionos_db_list_tables_handler(ctx, site="test.fr")
        assert r.success
        assert "announcements" in r.output
        assert "sensible" in r.output.lower()  # users marqué sensible

    @pytest.mark.asyncio
    async def test_handler_select_caps_limit_100(self, ctx, deployer, monkeypatch):
        import src.reasoning.handlers.ionos as hmod
        hmod._deployer = deployer
        captured = {}
        def fake_select(dom, table, columns=None, where=None, limit=20):
            captured["limit"] = limit
            return {"ok": True, "columns": ["id"], "rows": [["1"]], "count": 1, "truncated": False}
        monkeypatch.setattr(deployer, "db_select", fake_select)
        from src.reasoning.handlers.ionos import ionos_db_select_handler
        await ionos_db_select_handler(ctx, site="test.fr", table="t", limit="9999")
        assert captured["limit"] == 100  # borné à 100 côté handler

    @pytest.mark.asyncio
    async def test_handler_select_parses_where(self, ctx, deployer, monkeypatch):
        import src.reasoning.handlers.ionos as hmod
        hmod._deployer = deployer
        captured = {}
        def fake_select(dom, table, columns=None, where=None, limit=20):
            captured["where"] = where; captured["columns"] = columns
            return {"ok": True, "columns": [], "rows": [], "count": 0, "truncated": False}
        monkeypatch.setattr(deployer, "db_select", fake_select)
        from src.reasoning.handlers.ionos import ionos_db_select_handler
        await ionos_db_select_handler(ctx, site="test.fr", table="t", columns="a,b", where="id=5")
        assert captured["where"] == {"id": "5"}
        assert captured["columns"] == ["a", "b"]

    @pytest.mark.asyncio
    async def test_route_tables_requires_admin(self, deployer):
        import httpx
        from fastapi import FastAPI
        from web.routes import ionos as ionos_routes
        _configure_db(deployer)
        app = FastAPI(); app.include_router(ionos_routes.router); ionos_routes._deployer = deployer
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/ionos/sites/test.fr/database/tables")
        assert resp.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_route_tables_no_secret(self, deployer, monkeypatch):
        import httpx
        _configure_db(deployer)
        monkeypatch.setattr(deployer, "db_list_tables",
                            lambda dom: {"ok": True, "tables": ["announcements"]})
        app, _ = _make_ionos_app(deployer)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/ionos/sites/test.fr/database/tables")
        assert resp.status_code == 200
        body = resp.json()
        assert body["tables"] == ["announcements"]
        assert "password" not in json.dumps(body) and "secret" not in json.dumps(body)

    @pytest.mark.asyncio
    async def test_route_preview_caps_limit(self, deployer, monkeypatch):
        import httpx
        _configure_db(deployer)
        captured = {}
        def fake_select(dom, table, columns=None, where=None, limit=20):
            captured["limit"] = limit
            return {"ok": True, "columns": [], "rows": [], "count": 0, "truncated": False}
        monkeypatch.setattr(deployer, "db_select", fake_select)
        app, _ = _make_ionos_app(deployer)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/api/ionos/sites/test.fr/database/tables/t/preview", json={"limit": 9999})
        assert resp.status_code == 200
        assert captured["limit"] == 100

    @pytest.mark.asyncio
    async def test_route_schema_ok(self, deployer, monkeypatch):
        import httpx
        _configure_db(deployer)
        monkeypatch.setattr(deployer, "db_describe_table",
                            lambda dom, table: {"ok": True, "columns": [{"field": "id", "type": "int"}]})
        app, _ = _make_ionos_app(deployer)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/ionos/sites/test.fr/database/tables/announcements/schema")
        assert resp.status_code == 200
        assert resp.json()["columns"][0]["field"] == "id"

    # --- routes bridge install/remove/status (Part 2) ---
    @pytest.mark.asyncio
    async def test_bridge_routes_require_admin(self, deployer):
        import httpx
        from fastapi import FastAPI
        from web.routes import ionos as ionos_routes
        _configure_db(deployer)
        app = FastAPI(); app.include_router(ionos_routes.router); ionos_routes._deployer = deployer
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            for method, url in (
                ("get", "/api/ionos/sites/test.fr/database/bridge"),
                ("post", "/api/ionos/sites/test.fr/database/bridge"),
                ("delete", "/api/ionos/sites/test.fr/database/bridge"),
            ):
                resp = await getattr(c, method)(url)
                assert resp.status_code in (401, 403), (method, url)

    @pytest.mark.asyncio
    async def test_bridge_install_route_no_secret(self, deployer):
        import httpx
        _configure_db(deployer)
        app, _ = _make_ionos_app(deployer)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/api/ionos/sites/test.fr/database/bridge")
        assert resp.status_code == 200
        body = resp.json()
        assert body.get("ok") is True and body.get("version") == "9"
        assert "secret" not in json.dumps(body) and "secret_encrypted" not in json.dumps(body)

    @pytest.mark.asyncio
    async def test_bridge_status_route_non_sensitive(self, deployer):
        import httpx
        _configure_db(deployer)
        await deployer.install_database_bridge("test.fr")
        app, _ = _make_ionos_app(deployer)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/ionos/sites/test.fr/database/bridge")
        assert resp.status_code == 200
        body = resp.json()
        assert body.get("installed") is True and body.get("version") == "9"
        assert "secret" not in json.dumps(body)

    @pytest.mark.asyncio
    async def test_bridge_remove_route(self, deployer):
        import httpx
        _configure_db(deployer)
        await deployer.install_database_bridge("test.fr")
        app, _ = _make_ionos_app(deployer)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.delete("/api/ionos/sites/test.fr/database/bridge")
        assert resp.status_code == 200
        assert resp.json().get("removed") is True
        assert "bridge" not in deployer._sites["test.fr"]["database"]


def test_db_validators_unit():
    from src.services.ionos_deployer import _valid_db_identifier, _clamp_db_limit
    assert _valid_db_identifier("users") and _valid_db_identifier("col_1")
    assert not _valid_db_identifier("a b") and not _valid_db_identifier("x; DROP")
    assert not _valid_db_identifier("") and not _valid_db_identifier("*")
    assert _clamp_db_limit(99999) == 1000
    assert _clamp_db_limit(0) == 1
    assert _clamp_db_limit("abc") == 100  # valeur invalide → défaut 100 (durcissement #3)


class TestIonosSandboxCreateStep42:
    """Étape 4.2 : CREATE TABLE sandbox contrôlé (préfixe fixe, types whitelistés)."""

    def _ready(self, deployer, enable=True):
        import asyncio as _aio
        _configure_db(deployer)
        _aio.run(deployer.install_database_bridge("test.fr"))
        deployer.set_site_sandbox_config("test.fr", enable)

    _OKCOLS = [{"name": "label", "type": "VARCHAR", "length": 120, "nullable": True}]

    def test_sandbox_config_roundtrip(self, deployer):
        _configure_db(deployer)
        assert deployer.get_site_sandbox_config("test.fr") == {"enabled": False}
        deployer.set_site_sandbox_config("test.fr", True)
        assert deployer.get_site_sandbox_config("test.fr") == {"enabled": True}

    def test_reject_bad_prefix(self, deployer):
        self._ready(deployer)
        r = deployer.db_create_sandbox_table("test.fr", "announcements_x", self._OKCOLS, confirm=True)
        assert r["ok"] is False and r["error"] == "bad_prefix"

    def test_reject_bad_type(self, deployer):
        self._ready(deployer)
        r = deployer.db_create_sandbox_table("test.fr", "lumena_sandbox_t",
                                             [{"name": "amount", "type": "DECIMAL"}], confirm=True)
        assert r["ok"] is False and r["error"] == "bad_type"

    def test_reject_varchar_bad_length(self, deployer):
        self._ready(deployer)
        r = deployer.db_create_sandbox_table("test.fr", "lumena_sandbox_t",
                                             [{"name": "x", "type": "VARCHAR", "length": 999}], confirm=True)
        assert r["ok"] is False and r["error"] == "bad_length"

    def test_reject_bad_column_id(self, deployer):
        self._ready(deployer)
        r = deployer.db_create_sandbox_table("test.fr", "lumena_sandbox_t",
                                             [{"name": "id", "type": "INT"}], confirm=True)
        assert r["ok"] is False and r["error"] == "bad_column"

    def test_reject_too_many_columns(self, deployer):
        self._ready(deployer)
        cols = [{"name": f"c{i}", "type": "INT"} for i in range(31)]
        r = deployer.db_create_sandbox_table("test.fr", "lumena_sandbox_t", cols, confirm=True)
        assert r["ok"] is False and r["error"] == "bad_columns"

    def test_reject_without_confirm(self, deployer):
        self._ready(deployer)
        r = deployer.db_create_sandbox_table("test.fr", "lumena_sandbox_t", self._OKCOLS, confirm=False)
        assert r["ok"] is False and r["error"] == "not_confirmed"

    def test_reject_when_disabled(self, deployer):
        self._ready(deployer, enable=False)
        r = deployer.db_create_sandbox_table("test.fr", "lumena_sandbox_t", self._OKCOLS, confirm=True)
        assert r["ok"] is False and r["error"] == "sandbox_disabled"

    def test_reject_bad_default(self, deployer):
        self._ready(deployer)
        r = deployer.db_create_sandbox_table("test.fr", "lumena_sandbox_t",
                                             [{"name": "x", "type": "INT", "default": "1); DROP"}], confirm=True)
        assert r["ok"] is False and r["error"] == "bad_default"

    def test_create_routes_via_bridge(self, deployer, monkeypatch):
        self._ready(deployer)
        captured = {}
        def fake(domain, secret, path, op, body, timeout, ts=None, nonce=None):
            captured["op"] = op; captured["body"] = body
            return {"_http_status": 200, "ok": True, "table": body["name"], "created": True}
        monkeypatch.setattr(deployer, "_bridge_request", fake)
        r = deployer.db_create_sandbox_table("test.fr", "lumena_sandbox_demo", self._OKCOLS, confirm=True)
        assert r["ok"] is True and r["table"] == "lumena_sandbox_demo" and r["created"] is True
        assert captured["op"] == "db_create_table"
        assert set(captured["body"]["creds"]) == {"iv", "ct", "tag"}

    def test_create_upgrade_required_on_old_bridge(self, deployer):
        self._ready(deployer)
        deployer._sites["test.fr"]["database"]["bridge"]["version"] = "4"
        r = deployer.db_create_sandbox_table("test.fr", "lumena_sandbox_x", self._OKCOLS, confirm=True)
        assert r.get("upgrade_required") is True and r["ok"] is False

    def test_create_audit_no_sql_no_secret(self, deployer, monkeypatch, tmp_path):
        import src.services.ionos_deployer as mod
        monkeypatch.setattr(mod, "_DB_AUDIT_PATH", tmp_path / "audit.jsonl")
        self._ready(deployer)
        monkeypatch.setattr(deployer, "_bridge_request",
                            lambda *a, **k: {"_http_status": 200, "ok": True, "table": "lumena_sandbox_demo", "created": True})
        deployer.db_create_sandbox_table("test.fr", "lumena_sandbox_demo", self._OKCOLS, confirm=True)
        audit = (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
        assert '"op": "create_table"' in audit and '"value_keys": ["label"]' in audit
        assert "create table" not in audit.lower()    # jamais le SQL
        assert "password" not in audit and "secret" not in audit

    @pytest.mark.asyncio
    async def test_sandbox_routes_require_admin(self, deployer):
        import httpx
        from fastapi import FastAPI
        from web.routes import ionos as ionos_routes
        _configure_db(deployer)
        app = FastAPI(); app.include_router(ionos_routes.router); ionos_routes._deployer = deployer
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            for method, url in (
                ("get", "/api/ionos/sites/test.fr/database/sandbox-config"),
                ("post", "/api/ionos/sites/test.fr/database/sandbox-config"),
                ("post", "/api/ionos/sites/test.fr/database/sandbox-tables"),
            ):
                kw = {} if method == "get" else {"json": {}}
                resp = await getattr(c, method)(url, **kw)
                assert resp.status_code in (401, 403), (method, url)

    @pytest.mark.asyncio
    async def test_sandbox_create_route_no_secret(self, deployer, monkeypatch):
        import httpx
        _configure_db(deployer)
        await deployer.install_database_bridge("test.fr")
        deployer.set_site_sandbox_config("test.fr", True)
        monkeypatch.setattr(deployer, "_bridge_request",
                            lambda *a, **k: {"_http_status": 200, "ok": True, "table": "lumena_sandbox_demo", "created": True})
        app, _ = _make_ionos_app(deployer)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/api/ionos/sites/test.fr/database/sandbox-tables",
                                json={"name": "lumena_sandbox_demo", "columns": [{"name": "label", "type": "VARCHAR", "length": 50}], "confirm": True})
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True and body["table"] == "lumena_sandbox_demo"
        assert "secret" not in json.dumps(body) and "password" not in json.dumps(body)


class TestIonosDbWriteStep41:
    """Étape 4.1 : write contrôlé INSERT/UPDATE (UI-only, DELETE interdit)."""

    def _ready(self, deployer, enable=True, tables=("announcements",)):
        import asyncio as _aio
        _configure_db(deployer)  # site test.fr + BDD
        _aio.run(deployer.install_database_bridge("test.fr"))
        deployer.set_site_write_config("test.fr", enable, list(tables))

    # --- validation / refus AVANT réseau ---
    def test_write_rejects_delete(self, deployer):
        self._ready(deployer)
        r = deployer.db_write("test.fr", "delete", "announcements", {"x": 1}, confirm=True)
        assert r["ok"] is False and r["error"] == "bad_op"

    def test_write_requires_confirm(self, deployer):
        self._ready(deployer)
        r = deployer.db_write("test.fr", "insert", "announcements", {"title": "x"}, confirm=False)
        assert r["ok"] is False and r["error"] == "not_confirmed"

    def test_write_update_requires_where(self, deployer):
        self._ready(deployer)
        r = deployer.db_write("test.fr", "update", "announcements", {"title": "x"}, where=None, confirm=True)
        assert r["ok"] is False and r["error"] == "missing_where"

    def test_write_bad_table_identifier(self, deployer):
        self._ready(deployer)
        r = deployer.db_write("test.fr", "insert", "a b", {"title": "x"}, confirm=True)
        assert r["ok"] is False and r["error"] == "bad_table"

    def test_write_bad_column(self, deployer):
        self._ready(deployer)
        r = deployer.db_write("test.fr", "insert", "announcements", {"bad col": "x"}, confirm=True)
        assert r["ok"] is False and r["error"] == "bad_values"

    def test_write_disabled(self, deployer):
        self._ready(deployer, enable=False)
        r = deployer.db_write("test.fr", "insert", "announcements", {"title": "x"}, confirm=True)
        assert r["ok"] is False and r["error"] == "write_disabled"

    def test_write_table_not_in_allowlist(self, deployer):
        self._ready(deployer, enable=True, tables=("announcements",))
        r = deployer.db_write("test.fr", "insert", "contacts", {"name": "x"}, confirm=True)
        assert r["ok"] is False and r["error"] == "table_not_allowed"

    # --- routage bridge (mock) ---
    def test_write_insert_routes_via_bridge(self, deployer, monkeypatch):
        self._ready(deployer)
        captured = {}
        def fake(domain, secret, path, op, body, timeout, ts=None, nonce=None):
            captured["op"] = op; captured["body"] = body
            return {"_http_status": 200, "ok": True, "op": "insert", "affected": 1, "snapshot_count": 0, "warning": ""}
        monkeypatch.setattr(deployer, "_bridge_request", fake)
        r = deployer.db_write("test.fr", "insert", "announcements", {"title": "Hello"}, confirm=True)
        assert r["ok"] is True and r["affected"] == 1
        assert captured["op"] == "db_write" and captured["body"]["wop"] == "insert"
        # creds scellés, aucune valeur/secret en clair dans le body envoyé hors values voulues
        assert set(captured["body"]["creds"]) == {"iv", "ct", "tag"}
        assert "password" not in json.dumps({k: v for k, v in captured["body"].items() if k != "values"})

    def test_write_update_warning_no_rows(self, deployer, monkeypatch):
        self._ready(deployer)
        monkeypatch.setattr(deployer, "_bridge_request",
                            lambda *a, **k: {"_http_status": 200, "ok": True, "op": "update", "affected": 0, "snapshot_count": 1, "warning": "no_rows_modified"})
        r = deployer.db_write("test.fr", "update", "announcements", {"title": "x"}, where={"id": 2}, confirm=True)
        assert r["ok"] is True and r["affected"] == 0 and r["warning"] == "no_rows_modified"

    def test_write_too_many_rows(self, deployer, monkeypatch):
        self._ready(deployer)
        monkeypatch.setattr(deployer, "_bridge_request",
                            lambda *a, **k: {"_http_status": 200, "ok": False, "error": "too_many_rows", "snapshot_count": 999})
        r = deployer.db_write("test.fr", "update", "announcements", {"title": "x"}, where={"is_active": 1}, confirm=True)
        assert r["ok"] is False and r["error"] == "too_many_rows"

    def test_write_audit_no_values_no_secret(self, deployer, monkeypatch, tmp_path):
        import src.services.ionos_deployer as mod
        monkeypatch.setattr(mod, "_DB_AUDIT_PATH", tmp_path / "audit.jsonl")
        self._ready(deployer)
        monkeypatch.setattr(deployer, "_bridge_request",
                            lambda *a, **k: {"_http_status": 200, "ok": True, "op": "insert", "affected": 1, "snapshot_count": 0, "warning": ""})
        deployer.db_write("test.fr", "insert", "announcements", {"title": "SECRET_VALUE_42"}, confirm=True)
        audit = (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
        assert '"op": "insert"' in audit and '"value_keys": ["title"]' in audit
        assert "SECRET_VALUE_42" not in audit          # jamais les valeurs
        assert "password" not in audit and "secret" not in audit

    def test_write_config_roundtrip(self, deployer):
        _configure_db(deployer)
        assert deployer.get_site_write_config("test.fr") == {"enabled": False, "tables": []}
        deployer.set_site_write_config("test.fr", True, ["announcements", "bad col", "contacts"])
        wc = deployer.get_site_write_config("test.fr")
        assert wc["enabled"] is True
        assert wc["tables"] == ["announcements", "contacts"]  # "bad col" filtré

    @pytest.mark.asyncio
    async def test_write_route_requires_admin(self, deployer):
        import httpx
        from fastapi import FastAPI
        from web.routes import ionos as ionos_routes
        _configure_db(deployer)
        app = FastAPI(); app.include_router(ionos_routes.router); ionos_routes._deployer = deployer
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/api/ionos/sites/test.fr/database/tables/announcements/write",
                                json={"op": "insert", "values": {"a": "b"}, "confirm": True})
        assert resp.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_write_route_no_secret(self, deployer, monkeypatch):
        import httpx
        _configure_db(deployer)
        await deployer.install_database_bridge("test.fr")
        deployer.set_site_write_config("test.fr", True, ["announcements"])
        monkeypatch.setattr(deployer, "_bridge_request",
                            lambda *a, **k: {"_http_status": 200, "ok": True, "op": "insert", "affected": 1, "snapshot_count": 0, "warning": ""})
        app, _ = _make_ionos_app(deployer)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/api/ionos/sites/test.fr/database/tables/announcements/write",
                                json={"op": "insert", "values": {"title": "x"}, "confirm": True})
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert "secret" not in json.dumps(body) and "password" not in json.dumps(body)

    @pytest.mark.asyncio
    async def test_write_config_route(self, deployer):
        import httpx
        _configure_db(deployer)
        app, _ = _make_ionos_app(deployer)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/api/ionos/sites/test.fr/database/write-config",
                                json={"enabled": True, "tables": ["announcements"]})
            assert resp.status_code == 200 and resp.json()["enabled"] is True
            resp2 = await c.get("/api/ionos/sites/test.fr/database/write-config")
            assert resp2.json()["tables"] == ["announcements"]


class TestIonosSnapshotStep43:
    """Étape 4.3 : snapshot chiffré (UPDATE) + restore gaté (restore_enabled OFF par défaut)."""

    @pytest.fixture(autouse=True)
    def _isolate_snapshot_store(self, monkeypatch, tmp_path):
        import src.services.ionos_deployer as mod
        monkeypatch.setattr(mod, "_SNAPSHOT_DIR", tmp_path / "snaps")
        monkeypatch.setattr(mod, "_SNAPSHOT_INDEX", tmp_path / "snaps" / "index.jsonl")

    def _ready(self, deployer, write=True, restore=False, tables=("announcements",)):
        import asyncio as _aio
        _configure_db(deployer)
        _aio.run(deployer.install_database_bridge("test.fr"))
        deployer.set_site_write_config("test.fr", write, list(tables))
        if restore:
            deployer.set_site_restore_config("test.fr", True)

    # --- config restore : OFF par défaut + roundtrip ---
    def test_restore_config_off_by_default(self, deployer):
        _configure_db(deployer)
        assert deployer.get_site_restore_config("test.fr") == {"enabled": False}
        deployer.set_site_restore_config("test.fr", True)
        assert deployer.get_site_restore_config("test.fr") == {"enabled": True}

    # --- stockage chiffré : aucune valeur en clair, métadonnées non sensibles ---
    def test_store_snapshot_encrypted_no_plaintext(self, deployer):
        import src.services.ionos_deployer as mod
        _configure_db(deployer)
        snap = {"table": "announcements", "pk_col": "id", "op": "update",
                "rows": [{"id": 7, "title": "SECRET_TITLE_42"}]}
        sid = deployer._store_snapshot("test.fr", snap)
        assert sid
        blob = (mod._SNAPSHOT_DIR / "test.fr" / f"{sid}.enc").read_text(encoding="utf-8")
        assert "SECRET_TITLE_42" not in blob and blob.startswith("gAAAAA")  # Fernet, jamais en clair
        lst = deployer.list_snapshots("test.fr")["snapshots"]
        assert len(lst) == 1 and lst[0]["table"] == "announcements"
        assert lst[0]["columns"] == ["id", "title"] and lst[0]["row_count"] == 1
        assert "SECRET_TITLE_42" not in json.dumps(lst)   # métadonnées seulement

    # --- intégration db_write UPDATE : snapshot capturé puis stocké ---
    def test_write_update_captures_and_stores_snapshot(self, deployer, monkeypatch):
        from src.services.ionos_deployer import _seal_creds
        self._ready(deployer)
        def fake(domain, secret, path, op, body, timeout, ts=None, nonce=None):
            snap = {"table": "announcements", "pk_col": "id", "op": "update",
                    "rows": [{"id": 3, "title": "ANCIEN_TITRE"}]}
            enc = _seal_creds(secret, snap, "db_write", ts, nonce)  # AES-GCM en transit
            return {"_http_status": 200, "ok": True, "op": "update", "affected": 1,
                    "snapshot_count": 1, "snapshot_enc": enc, "warning": ""}
        monkeypatch.setattr(deployer, "_bridge_request", fake)
        r = deployer.db_write("test.fr", "update", "announcements", {"title": "x"},
                              where={"id": 3}, confirm=True)
        assert r["ok"] is True and r["snapshot_id"]
        lst = deployer.list_snapshots("test.fr")["snapshots"]
        assert len(lst) == 1 and lst[0]["id"] == r["snapshot_id"]
        assert "ANCIEN_TITRE" not in json.dumps(lst)   # valeur jamais exposée

    # --- snapshot impossible côté bridge → write refusé ---
    def test_write_refused_when_snapshot_impossible(self, deployer, monkeypatch):
        self._ready(deployer)
        monkeypatch.setattr(deployer, "_bridge_request",
                            lambda *a, **k: {"_http_status": 200, "ok": False, "error": "snapshot_no_pk"})
        r = deployer.db_write("test.fr", "update", "announcements", {"title": "x"},
                              where={"id": 1}, confirm=True)
        assert r["ok"] is False and r["error"] == "snapshot_no_pk"
        assert "primaire" in r["message"].lower()
        assert deployer.list_snapshots("test.fr")["snapshots"] == []  # rien stocké

    # --- purge TTL : un snapshot expiré disparaît ---
    def test_purge_drops_expired(self, deployer):
        import src.services.ionos_deployer as mod
        import datetime as _dt
        _configure_db(deployer)
        sid = deployer._store_snapshot("test.fr", {"table": "t", "pk_col": "id", "rows": [{"id": 1}]})
        idx = deployer._snapshot_index_read()
        idx[0]["expires_at"] = (_dt.datetime.now() - _dt.timedelta(days=1)).isoformat(timespec="seconds")
        deployer._snapshot_index_write(idx)
        assert deployer.list_snapshots("test.fr")["snapshots"] == []
        assert not (mod._SNAPSHOT_DIR / "test.fr" / f"{sid}.enc").exists()

    # --- purge excédent : > max/site, les plus anciens sont supprimés ---
    def test_purge_caps_per_site(self, deployer, monkeypatch):
        import src.services.ionos_deployer as mod
        monkeypatch.setattr(mod, "_SNAPSHOT_MAX_PER_SITE", 2)
        _configure_db(deployer)
        for _ in range(3):
            deployer._store_snapshot("test.fr", {"table": "t", "pk_col": "id", "rows": [{"id": 1}]})
        assert len(deployer.list_snapshots("test.fr")["snapshots"]) == 2

    # --- restore : refus si restore_enabled OFF ---
    def test_restore_refused_when_disabled(self, deployer):
        self._ready(deployer, restore=False)
        sid = deployer._store_snapshot("test.fr", {"table": "announcements", "pk_col": "id",
                                                   "rows": [{"id": 1, "title": "a"}]})
        r = deployer.restore_snapshot("test.fr", sid, confirm=True)
        assert r["ok"] is False and r["error"] == "restore_disabled"

    # --- restore : refus sans confirm ---
    def test_restore_requires_confirm(self, deployer):
        self._ready(deployer, restore=True)
        sid = deployer._store_snapshot("test.fr", {"table": "announcements", "pk_col": "id",
                                                   "rows": [{"id": 1, "title": "a"}]})
        r = deployer.restore_snapshot("test.fr", sid, confirm=False)
        assert r["ok"] is False and r["error"] == "not_confirmed"

    # --- restore : ré-applique l'image-avant via les guards write 4.1 ---
    def test_restore_reapplies_via_write_guards(self, deployer, monkeypatch):
        self._ready(deployer, restore=True)
        sid = deployer._store_snapshot("test.fr", {"table": "announcements", "pk_col": "id",
                                                   "rows": [{"id": 5, "title": "AVANT"}]})
        calls = []
        def fake_write(domain, op, table, values, where=None, confirm=False, source="ui"):
            calls.append({"op": op, "table": table, "values": values, "where": where,
                          "confirm": confirm, "source": source})
            return {"ok": True, "affected": 1}
        monkeypatch.setattr(deployer, "db_write", fake_write)
        r = deployer.restore_snapshot("test.fr", sid, confirm=True)
        assert r["ok"] is True and r["restored"] == 1
        assert len(calls) == 1
        c = calls[0]
        assert c["op"] == "update" and c["table"] == "announcements"
        assert c["where"] == {"id": 5} and c["values"] == {"title": "AVANT"}
        assert c["confirm"] is True and c["source"] == "restore"

    def test_delete_snapshot(self, deployer):
        import src.services.ionos_deployer as mod
        _configure_db(deployer)
        sid = deployer._store_snapshot("test.fr", {"table": "t", "pk_col": "id", "rows": [{"id": 1}]})
        r = deployer.delete_snapshot("test.fr", sid)
        assert r["ok"] is True and r["deleted"] is True
        assert not (mod._SNAPSHOT_DIR / "test.fr" / f"{sid}.enc").exists()
        assert deployer.list_snapshots("test.fr")["snapshots"] == []

    # --- audit restore : aucune valeur, op="restore" ---
    def test_restore_audit_no_values(self, deployer, monkeypatch, tmp_path):
        import src.services.ionos_deployer as mod
        monkeypatch.setattr(mod, "_DB_AUDIT_PATH", tmp_path / "audit.jsonl")
        self._ready(deployer, restore=True)
        sid = deployer._store_snapshot("test.fr", {"table": "announcements", "pk_col": "id",
                                                   "rows": [{"id": 5, "title": "VALEUR_SECRETE_X"}]})
        monkeypatch.setattr(deployer, "db_write", lambda *a, **k: {"ok": True, "affected": 1})
        deployer.restore_snapshot("test.fr", sid, confirm=True)
        audit = (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
        assert '"op": "restore"' in audit
        assert "VALEUR_SECRETE_X" not in audit
        assert "password" not in audit and "secret" not in audit

    # --- routes admin : protégées + sans secret ---
    @pytest.mark.asyncio
    async def test_snapshot_routes_require_admin(self, deployer):
        import httpx
        from fastapi import FastAPI
        from web.routes import ionos as ionos_routes
        _configure_db(deployer)
        app = FastAPI(); app.include_router(ionos_routes.router); ionos_routes._deployer = deployer
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            for method, url in (
                ("get", "/api/ionos/sites/test.fr/database/restore-config"),
                ("post", "/api/ionos/sites/test.fr/database/restore-config"),
                ("get", "/api/ionos/sites/test.fr/database/snapshots"),
                ("post", "/api/ionos/sites/test.fr/database/snapshots/abc/restore"),
                ("delete", "/api/ionos/sites/test.fr/database/snapshots/abc"),
            ):
                kw = {} if method in ("get", "delete") else {"json": {}}
                resp = await getattr(c, method)(url, **kw)
                assert resp.status_code in (401, 403), (method, url)

    @pytest.mark.asyncio
    async def test_snapshots_list_route_metadata_only(self, deployer):
        import httpx
        _configure_db(deployer)
        deployer._store_snapshot("test.fr", {"table": "announcements", "pk_col": "id",
                                             "rows": [{"id": 1, "title": "SECRET_LIST_VAL"}]})
        app, _ = _make_ionos_app(deployer)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/ionos/sites/test.fr/database/snapshots")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True and len(body["snapshots"]) == 1
        assert body["snapshots"][0]["table"] == "announcements"
        assert "SECRET_LIST_VAL" not in json.dumps(body)
        assert "secret" not in json.dumps(body) and "password" not in json.dumps(body)


class TestIonosDbDeleteStep44:
    """Étape 4.4 : DELETE contrôlé (op distincte, WHERE + double confirm, snapshot obligatoire)."""

    @pytest.fixture(autouse=True)
    def _isolate_snapshot_store(self, monkeypatch, tmp_path):
        import src.services.ionos_deployer as mod
        monkeypatch.setattr(mod, "_SNAPSHOT_DIR", tmp_path / "snaps")
        monkeypatch.setattr(mod, "_SNAPSHOT_INDEX", tmp_path / "snaps" / "index.jsonl")

    def _ready(self, deployer, delete=True, tables=("lumena_write_test",),
               write=False, write_tables=("lumena_write_test",), restore=False):
        import asyncio as _aio
        _configure_db(deployer)
        _aio.run(deployer.install_database_bridge("test.fr"))
        deployer.set_site_delete_config("test.fr", delete, list(tables))
        if write or restore:
            deployer.set_site_write_config("test.fr", True, list(write_tables))
        if restore:
            deployer.set_site_restore_config("test.fr", True)

    # --- config delete : OFF par défaut, allowlist séparée, indépendance write ---
    def test_delete_config_off_by_default(self, deployer):
        _configure_db(deployer)
        assert deployer.get_site_delete_config("test.fr") == {"enabled": False, "tables": []}
        deployer.set_site_delete_config("test.fr", True, ["lumena_write_test", "bad col"])
        dc = deployer.get_site_delete_config("test.fr")
        assert dc["enabled"] is True and dc["tables"] == ["lumena_write_test"]  # "bad col" filtré

    def test_delete_independent_of_write(self, deployer):
        _configure_db(deployer)
        deployer.set_site_write_config("test.fr", True, ["lumena_write_test"])
        # write activé ne doit PAS activer le delete
        assert deployer.get_site_delete_config("test.fr") == {"enabled": False, "tables": []}

    # --- refus AVANT réseau ---
    def test_delete_requires_where(self, deployer):
        self._ready(deployer)
        r = deployer.db_delete("test.fr", "lumena_write_test", where=None,
                               confirm=True, confirm_table="lumena_write_test")
        assert r["ok"] is False and r["error"] == "missing_where"

    def test_delete_requires_confirm(self, deployer):
        self._ready(deployer)
        r = deployer.db_delete("test.fr", "lumena_write_test", where={"id": "1"},
                               confirm=False, confirm_table="lumena_write_test")
        assert r["ok"] is False and r["error"] == "not_confirmed"

    def test_delete_confirm_table_mismatch(self, deployer):
        self._ready(deployer)
        r = deployer.db_delete("test.fr", "lumena_write_test", where={"id": "1"},
                               confirm=True, confirm_table="autre_table")
        assert r["ok"] is False and r["error"] == "confirm_mismatch"

    def test_delete_bad_table(self, deployer):
        self._ready(deployer)
        r = deployer.db_delete("test.fr", "a b", where={"id": "1"},
                               confirm=True, confirm_table="a b")
        assert r["ok"] is False and r["error"] == "bad_table"

    def test_delete_disabled(self, deployer):
        self._ready(deployer, delete=False)
        r = deployer.db_delete("test.fr", "lumena_write_test", where={"id": "1"},
                               confirm=True, confirm_table="lumena_write_test")
        assert r["ok"] is False and r["error"] == "delete_disabled"

    def test_delete_table_not_allowed(self, deployer):
        self._ready(deployer, delete=True, tables=("lumena_write_test",))
        r = deployer.db_delete("test.fr", "contacts", where={"id": "1"},
                               confirm=True, confirm_table="contacts")
        assert r["ok"] is False and r["error"] == "table_not_allowed"

    # --- routage bridge mocké : op db_delete, creds scellés ---
    def test_delete_routes_via_bridge(self, deployer, monkeypatch):
        self._ready(deployer)
        captured = {}
        def fake(domain, secret, path, op, body, timeout, ts=None, nonce=None):
            captured["op"] = op; captured["body"] = body
            return {"_http_status": 200, "ok": True, "op": "delete", "affected": 1,
                    "snapshot_count": 1, "warning": ""}
        monkeypatch.setattr(deployer, "_bridge_request", fake)
        r = deployer.db_delete("test.fr", "lumena_write_test", where={"id": "1"},
                               confirm=True, confirm_table="lumena_write_test")
        assert r["ok"] is True and r["affected"] == 1
        assert captured["op"] == "db_delete"
        assert captured["body"]["affected_max"] == 25  # plafond DELETE
        assert set(captured["body"]["creds"]) == {"iv", "ct", "tag"}
        assert "password" not in json.dumps({k: v for k, v in captured["body"].items() if k != "where"})

    # --- snapshot capturé + stocké ---
    def test_delete_captures_and_stores_snapshot(self, deployer, monkeypatch):
        from src.services.ionos_deployer import _seal_creds
        self._ready(deployer)
        def fake(domain, secret, path, op, body, timeout, ts=None, nonce=None):
            snap = {"table": "lumena_write_test", "pk_col": "id", "op": "delete",
                    "rows": [{"id": 9, "title": "LIGNE_SUPPRIMEE"}]}
            enc = _seal_creds(secret, snap, "db_delete", ts, nonce)
            return {"_http_status": 200, "ok": True, "op": "delete", "affected": 1,
                    "snapshot_count": 1, "snapshot_enc": enc, "warning": ""}
        monkeypatch.setattr(deployer, "_bridge_request", fake)
        r = deployer.db_delete("test.fr", "lumena_write_test", where={"id": "9"},
                               confirm=True, confirm_table="lumena_write_test")
        assert r["ok"] is True and r["snapshot_id"]
        lst = deployer.list_snapshots("test.fr")["snapshots"]
        assert len(lst) == 1 and lst[0]["op"] == "delete" and lst[0]["id"] == r["snapshot_id"]
        assert "LIGNE_SUPPRIMEE" not in json.dumps(lst)

    # --- snapshot impossible → DELETE refusé ---
    def test_delete_refused_when_snapshot_impossible(self, deployer, monkeypatch):
        self._ready(deployer)
        monkeypatch.setattr(deployer, "_bridge_request",
                            lambda *a, **k: {"_http_status": 200, "ok": False, "error": "snapshot_no_pk"})
        r = deployer.db_delete("test.fr", "lumena_write_test", where={"id": "1"},
                               confirm=True, confirm_table="lumena_write_test")
        assert r["ok"] is False and r["error"] == "snapshot_no_pk"
        assert "primaire" in r["message"].lower()
        assert deployer.list_snapshots("test.fr")["snapshots"] == []

    def test_delete_too_many_rows(self, deployer, monkeypatch):
        self._ready(deployer)
        monkeypatch.setattr(deployer, "_bridge_request",
                            lambda *a, **k: {"_http_status": 200, "ok": False, "error": "too_many_rows", "snapshot_count": 99})
        r = deployer.db_delete("test.fr", "lumena_write_test", where={"is_active": "1"},
                               confirm=True, confirm_table="lumena_write_test")
        assert r["ok"] is False and r["error"] == "too_many_rows"

    # --- audit op=delete sans valeurs/secret ---
    def test_delete_audit_no_values_no_secret(self, deployer, monkeypatch, tmp_path):
        import src.services.ionos_deployer as mod
        monkeypatch.setattr(mod, "_DB_AUDIT_PATH", tmp_path / "audit.jsonl")
        self._ready(deployer)
        monkeypatch.setattr(deployer, "_bridge_request",
                            lambda *a, **k: {"_http_status": 200, "ok": True, "op": "delete", "affected": 1, "snapshot_count": 1, "warning": ""})
        deployer.db_delete("test.fr", "lumena_write_test", where={"id": "SECRET_WHERE_7"},
                           confirm=True, confirm_table="lumena_write_test")
        audit = (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
        assert '"op": "delete"' in audit and '"where_keys": ["id"]' in audit
        assert "SECRET_WHERE_7" not in audit          # jamais les valeurs
        assert "password" not in audit and "secret" not in audit

    def test_delete_upgrade_required_on_old_bridge(self, deployer):
        self._ready(deployer)
        deployer._sites["test.fr"]["database"]["bridge"]["version"] = "6"
        r = deployer.db_delete("test.fr", "lumena_write_test", where={"id": "1"},
                               confirm=True, confirm_table="lumena_write_test")
        assert r.get("upgrade_required") is True and r["ok"] is False

    # --- restore d'un snapshot delete = ré-INSERT via guards write 4.1 ---
    def test_restore_delete_reinserts_via_write(self, deployer, monkeypatch):
        self._ready(deployer, restore=True)
        sid = deployer._store_snapshot("test.fr", {"table": "lumena_write_test", "pk_col": "id",
                                                   "op": "delete",
                                                   "rows": [{"id": 9, "title": "REVENU", "note": "x"}]})
        calls = []
        def fake_write(domain, op, table, values, where=None, confirm=False, source="ui"):
            calls.append({"op": op, "table": table, "values": values, "where": where,
                          "confirm": confirm, "source": source})
            return {"ok": True, "affected": 1}
        monkeypatch.setattr(deployer, "db_write", fake_write)
        r = deployer.restore_snapshot("test.fr", sid, confirm=True)
        assert r["ok"] is True and r["restored"] == 1
        assert len(calls) == 1
        c = calls[0]
        assert c["op"] == "insert" and c["table"] == "lumena_write_test"
        assert c["values"] == {"id": 9, "title": "REVENU", "note": "x"}  # ligne complète, PK comprise
        assert c["where"] is None and c["confirm"] is True and c["source"] == "restore"

    # --- routes admin protégées + sans secret ---
    @pytest.mark.asyncio
    async def test_delete_routes_require_admin(self, deployer):
        import httpx
        from fastapi import FastAPI
        from web.routes import ionos as ionos_routes
        _configure_db(deployer)
        app = FastAPI(); app.include_router(ionos_routes.router); ionos_routes._deployer = deployer
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            for method, url in (
                ("get", "/api/ionos/sites/test.fr/database/delete-config"),
                ("post", "/api/ionos/sites/test.fr/database/delete-config"),
                ("post", "/api/ionos/sites/test.fr/database/tables/lumena_write_test/delete"),
            ):
                kw = {} if method == "get" else {"json": {}}
                resp = await getattr(c, method)(url, **kw)
                assert resp.status_code in (401, 403), (method, url)

    @pytest.mark.asyncio
    async def test_delete_route_no_secret(self, deployer, monkeypatch):
        import httpx
        _configure_db(deployer)
        await deployer.install_database_bridge("test.fr")
        deployer.set_site_delete_config("test.fr", True, ["lumena_write_test"])
        monkeypatch.setattr(deployer, "_bridge_request",
                            lambda *a, **k: {"_http_status": 200, "ok": True, "op": "delete", "affected": 1, "snapshot_count": 0, "warning": ""})
        app, _ = _make_ionos_app(deployer)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/api/ionos/sites/test.fr/database/tables/lumena_write_test/delete",
                                json={"where": {"id": "1"}, "confirm": True, "confirm_table": "lumena_write_test"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert "secret" not in json.dumps(body) and "password" not in json.dumps(body)

    @pytest.mark.asyncio
    async def test_delete_config_route(self, deployer):
        import httpx
        _configure_db(deployer)
        app, _ = _make_ionos_app(deployer)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/api/ionos/sites/test.fr/database/delete-config",
                                json={"enabled": True, "tables": ["lumena_write_test"]})
            assert resp.status_code == 200 and resp.json()["enabled"] is True
            resp2 = await c.get("/api/ionos/sites/test.fr/database/delete-config")
            assert resp2.json()["tables"] == ["lumena_write_test"]


class TestIonosReactProposeStep45A:
    """Étape 4.5A : ReAct propose-only INSERT/UPDATE (l'agent PROPOSE, l'humain EXÉCUTE)."""

    @pytest.fixture(autouse=True)
    def _isolate_stores(self, monkeypatch, tmp_path):
        import src.services.ionos_deployer as mod
        monkeypatch.setattr(mod, "_SNAPSHOT_DIR", tmp_path / "snaps")
        monkeypatch.setattr(mod, "_SNAPSHOT_INDEX", tmp_path / "snaps" / "index.jsonl")
        monkeypatch.setattr(mod, "_PROPOSAL_DIR", tmp_path / "props")
        monkeypatch.setattr(mod, "_PROPOSAL_INDEX", tmp_path / "props" / "index.jsonl")

    def _ready(self, deployer, react=True, write=True, tables=("lumena_write_test",)):
        import asyncio as _aio
        _configure_db(deployer)
        _aio.run(deployer.install_database_bridge("test.fr"))
        deployer.set_site_write_config("test.fr", write, list(tables))
        deployer.set_site_react_write_config("test.fr", react)

    # --- config react : OFF par défaut ---
    def test_react_config_off_by_default(self, deployer):
        _configure_db(deployer)
        assert deployer.get_site_react_write_config("test.fr") == {"enabled": False}
        deployer.set_site_react_write_config("test.fr", True)
        assert deployer.get_site_react_write_config("test.fr") == {"enabled": True}

    # --- propose enfile sans exécuter (aucun _bridge_request d'écriture) ---
    def test_propose_enqueues_without_executing(self, deployer, monkeypatch):
        self._ready(deployer)
        called = {"n": 0}
        def fake(domain, secret, path, op, body, timeout, ts=None, nonce=None):
            called["n"] += 1
            return {"_http_status": 200, "ok": True, "count": 0, "columns": [], "rows": [], "truncated": False}
        monkeypatch.setattr(deployer, "_bridge_request", fake)  # autorise le COUNT read-only mais pas d'écriture
        r = deployer.propose_write("test.fr", "insert", "lumena_write_test", {"title": "X"})
        assert r["ok"] is True and r["status"] == "pending" and r["proposal_id"]
        assert r["value_keys"] == ["title"]
        pend = deployer.list_pending_actions("test.fr")["actions"]
        assert len(pend) == 1 and pend[0]["op"] == "insert"
        # insert ne déclenche aucun COUNT ni écriture
        assert called["n"] == 0

    # --- refus si react_write_enabled=false (pas de mise en file) ---
    def test_propose_refused_react_disabled(self, deployer):
        self._ready(deployer, react=False)
        r = deployer.propose_write("test.fr", "insert", "lumena_write_test", {"title": "X"})
        assert r["ok"] is False and r["error"] == "react_write_disabled"
        assert deployer.list_pending_actions("test.fr")["actions"] == []

    # --- refus si write_enabled=false ou table hors allowlist ---
    def test_propose_refused_write_disabled(self, deployer):
        self._ready(deployer, react=True, write=False)
        r = deployer.propose_write("test.fr", "insert", "lumena_write_test", {"title": "X"})
        assert r["ok"] is False and r["error"] == "write_disabled"
        assert deployer.list_pending_actions("test.fr")["actions"] == []

    def test_propose_refused_table_not_allowed(self, deployer):
        self._ready(deployer, react=True, write=True, tables=("lumena_write_test",))
        r = deployer.propose_write("test.fr", "insert", "contacts", {"name": "X"})
        assert r["ok"] is False and r["error"] == "table_not_allowed"
        assert deployer.list_pending_actions("test.fr")["actions"] == []

    def test_propose_update_requires_where(self, deployer):
        self._ready(deployer)
        r = deployer.propose_write("test.fr", "update", "lumena_write_test", {"title": "X"}, where=None)
        assert r["ok"] is False and r["error"] == "missing_where"

    # --- valeurs chiffrées au repos, métadonnées non sensibles ---
    def test_proposal_values_encrypted_metadata_only(self, deployer):
        import src.services.ionos_deployer as mod
        self._ready(deployer)
        r = deployer.propose_write("test.fr", "insert", "lumena_write_test", {"title": "SECRET_PROP_42"})
        sid = r["proposal_id"]
        blob = (mod._PROPOSAL_DIR / "test.fr" / f"{sid}.enc").read_text(encoding="utf-8")
        assert blob.startswith("gAAAAA") and "SECRET_PROP_42" not in blob   # Fernet, pas de clair
        pend = deployer.list_pending_actions("test.fr")["actions"]
        assert "SECRET_PROP_42" not in json.dumps(pend)                     # métadonnées seules
        assert pend[0]["value_keys"] == ["title"]

    # --- la proposition seule n'écrit rien dans l'audit comme exécution ---
    def test_propose_audit_no_values(self, deployer, monkeypatch, tmp_path):
        import src.services.ionos_deployer as mod
        monkeypatch.setattr(mod, "_DB_AUDIT_PATH", tmp_path / "audit.jsonl")
        self._ready(deployer)
        deployer.propose_write("test.fr", "insert", "lumena_write_test", {"title": "SECRET_AUDIT_X"})
        audit = (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
        assert '"op": "propose_insert"' in audit and '"value_keys": ["title"]' in audit
        assert "SECRET_AUDIT_X" not in audit and "password" not in audit

    # --- approbation exécute via guards 4.1 (source=react_approved) ---
    def test_approve_executes_via_write_guards(self, deployer, monkeypatch):
        self._ready(deployer)
        r = deployer.propose_write("test.fr", "insert", "lumena_write_test", {"title": "X"})
        sid = r["proposal_id"]
        calls = []
        def fake_write(domain, op, table, values, where=None, confirm=False, source="ui"):
            calls.append({"op": op, "table": table, "values": values, "where": where,
                          "confirm": confirm, "source": source})
            return {"ok": True, "op": op, "affected": 1, "warning": ""}
        monkeypatch.setattr(deployer, "db_write", fake_write)
        out = deployer.approve_pending_action("test.fr", sid, confirm=True)
        assert out["ok"] is True
        assert len(calls) == 1
        c = calls[0]
        assert c["op"] == "insert" and c["confirm"] is True and c["source"] == "react_approved"
        assert c["values"] == {"title": "X"}
        # proposition marquée exécutée + retirée des pending
        assert deployer.list_pending_actions("test.fr")["actions"] == []

    # --- approbation sans confirm refusée (le confirm vient de l'humain, jamais du modèle) ---
    def test_approve_requires_confirm(self, deployer):
        self._ready(deployer)
        sid = deployer.propose_write("test.fr", "insert", "lumena_write_test", {"title": "X"})["proposal_id"]
        out = deployer.approve_pending_action("test.fr", sid, confirm=False)
        assert out["ok"] is False and out["error"] == "not_confirmed"

    # --- approbation bloquée si guards UI désactivés entre-temps (double verrou) ---
    def test_approve_blocked_if_write_disabled_after_propose(self, deployer):
        self._ready(deployer)
        sid = deployer.propose_write("test.fr", "insert", "lumena_write_test", {"title": "X"})["proposal_id"]
        deployer.set_site_write_config("test.fr", False, [])   # guards UI coupés après la proposition
        out = deployer.approve_pending_action("test.fr", sid, confirm=True)
        assert out["ok"] is False and out["error"] == "write_disabled"

    # --- rejet : aucune exécution ---
    def test_reject_no_execution(self, deployer, monkeypatch):
        self._ready(deployer)
        sid = deployer.propose_write("test.fr", "insert", "lumena_write_test", {"title": "X"})["proposal_id"]
        monkeypatch.setattr(deployer, "db_write", lambda *a, **k: (_ for _ in ()).throw(AssertionError("ne doit pas exécuter")))
        out = deployer.reject_pending_action("test.fr", sid)
        assert out["ok"] is True and out["status"] == "rejected"
        assert deployer.list_pending_actions("test.fr")["actions"] == []

    # --- handler ReAct : propose-only, pas de paramètre confirm ---
    def test_handler_propose_only_no_confirm_param(self, deployer):
        import inspect
        from src.reasoning.handlers.ionos import ionos_db_propose_write_handler
        params = set(inspect.signature(ionos_db_propose_write_handler).parameters)
        assert "confirm" not in params and "confirm_table" not in params
        assert {"site", "table", "op", "values", "where"} <= params

    def _ready_no_bridge(self, deployer, react=True, write=True, tables=("lumena_write_test",)):
        # propose_write n'exige pas le bridge installé → évite asyncio.run en test async.
        _configure_db(deployer)
        deployer.set_site_write_config("test.fr", write, list(tables))
        deployer.set_site_react_write_config("test.fr", react)

    @pytest.mark.asyncio
    async def test_handler_proposes_without_executing(self, deployer, monkeypatch):
        import src.reasoning.handlers.ionos as hmod
        self._ready_no_bridge(deployer)
        hmod._deployer = deployer
        monkeypatch.setattr(deployer, "db_write",
                            lambda *a, **k: (_ for _ in ()).throw(AssertionError("handler ne doit jamais exécuter")))
        from src.reasoning.handlers.ionos import ionos_db_propose_write_handler
        from unittest.mock import MagicMock
        r = await ionos_db_propose_write_handler(MagicMock(), site="test.fr", table="lumena_write_test",
                                                 op="insert", values="title=Bonjour")
        assert r.success
        assert "Proposition" in r.output and "confirmée par un humain" in r.output
        assert "Bonjour" not in r.output   # la valeur n'est jamais renvoyée au modèle
        assert len(deployer.list_pending_actions("test.fr")["actions"]) == 1

    @pytest.mark.asyncio
    async def test_handler_refused_when_react_disabled(self, deployer):
        import src.reasoning.handlers.ionos as hmod
        self._ready_no_bridge(deployer, react=False)
        hmod._deployer = deployer
        from src.reasoning.handlers.ionos import ionos_db_propose_write_handler
        from unittest.mock import MagicMock
        r = await ionos_db_propose_write_handler(MagicMock(), site="test.fr", table="lumena_write_test",
                                                 op="insert", values="title=X")
        assert not r.success

    # --- routes admin protégées + sans secret ---
    @pytest.mark.asyncio
    async def test_react_routes_require_admin(self, deployer):
        import httpx
        from fastapi import FastAPI
        from web.routes import ionos as ionos_routes
        _configure_db(deployer)
        app = FastAPI(); app.include_router(ionos_routes.router); ionos_routes._deployer = deployer
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            for method, url in (
                ("get", "/api/ionos/sites/test.fr/database/react-config"),
                ("post", "/api/ionos/sites/test.fr/database/react-config"),
                ("get", "/api/ionos/sites/test.fr/database/pending-actions"),
                ("post", "/api/ionos/sites/test.fr/database/pending-actions/abc/approve"),
                ("post", "/api/ionos/sites/test.fr/database/pending-actions/abc/reject"),
            ):
                kw = {} if method == "get" else {"json": {}}
                resp = await getattr(c, method)(url, **kw)
                assert resp.status_code in (401, 403), (method, url)

    @pytest.mark.asyncio
    async def test_pending_actions_route_metadata_only(self, deployer):
        import httpx
        self._ready_no_bridge(deployer)
        deployer.propose_write("test.fr", "insert", "lumena_write_test", {"title": "SECRET_ROUTE_V"})
        app, _ = _make_ionos_app(deployer)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/ionos/sites/test.fr/database/pending-actions")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True and len(body["actions"]) == 1
        assert "SECRET_ROUTE_V" not in json.dumps(body)
        assert "secret" not in json.dumps(body) and "password" not in json.dumps(body)


class TestIonosReactProposeDeleteStep45B:
    """Étape 4.5B : ReAct propose-only DELETE, désactivé par défaut (kill-switch + flag site)."""

    @pytest.fixture(autouse=True)
    def _isolate_stores(self, monkeypatch, tmp_path):
        import src.services.ionos_deployer as mod
        monkeypatch.setattr(mod, "_SNAPSHOT_DIR", tmp_path / "snaps")
        monkeypatch.setattr(mod, "_SNAPSHOT_INDEX", tmp_path / "snaps" / "index.jsonl")
        monkeypatch.setattr(mod, "_PROPOSAL_DIR", tmp_path / "props")
        monkeypatch.setattr(mod, "_PROPOSAL_INDEX", tmp_path / "props" / "index.jsonl")

    def _ready(self, deployer, killswitch=True, react_delete=True, delete=True,
               tables=("lumena_write_test",), monkeypatch=None):
        _configure_db(deployer)
        deployer.set_site_delete_config("test.fr", delete, list(tables))
        deployer.set_site_react_delete_config("test.fr", react_delete)
        if killswitch and monkeypatch is not None:
            monkeypatch.setenv("LUMENA_IONOS_REACT_DELETE_ENABLED", "1")
        elif monkeypatch is not None:
            monkeypatch.setenv("LUMENA_IONOS_REACT_DELETE_ENABLED", "0")
        # estimate_count : COUNT via db_select → on mocke pour éviter le réseau
        if monkeypatch is not None:
            monkeypatch.setattr(deployer, "db_select",
                                lambda dom, table, columns=None, where=None, limit=20: {"ok": True, "count": 1})

    # --- kill-switch OFF par défaut ---
    def test_killswitch_off_by_default(self, deployer, monkeypatch):
        monkeypatch.delenv("LUMENA_IONOS_REACT_DELETE_ENABLED", raising=False)
        assert deployer._react_delete_killswitch_on() is False
        monkeypatch.setenv("LUMENA_IONOS_REACT_DELETE_ENABLED", "1")
        assert deployer._react_delete_killswitch_on() is True

    # --- flag site OFF par défaut + roundtrip ---
    def test_react_delete_config_off_by_default(self, deployer):
        _configure_db(deployer)
        assert deployer.get_site_react_delete_config("test.fr") == {"enabled": False}
        deployer.set_site_react_delete_config("test.fr", True)
        assert deployer.get_site_react_delete_config("test.fr") == {"enabled": True}

    # --- refus si kill-switch global OFF ---
    def test_propose_refused_killswitch_off(self, deployer, monkeypatch):
        self._ready(deployer, killswitch=False, monkeypatch=monkeypatch)
        r = deployer.propose_delete("test.fr", "lumena_write_test", where={"id": "1"})
        assert r["ok"] is False and r["error"] == "react_delete_killswitch_off"
        assert deployer.list_pending_actions("test.fr")["actions"] == []

    # --- refus si flag site OFF ---
    def test_propose_refused_react_delete_disabled(self, deployer, monkeypatch):
        self._ready(deployer, killswitch=True, react_delete=False, monkeypatch=monkeypatch)
        r = deployer.propose_delete("test.fr", "lumena_write_test", where={"id": "1"})
        assert r["ok"] is False and r["error"] == "react_delete_disabled"

    # --- refus si delete_enabled OFF ---
    def test_propose_refused_delete_disabled(self, deployer, monkeypatch):
        self._ready(deployer, killswitch=True, react_delete=True, delete=False, monkeypatch=monkeypatch)
        r = deployer.propose_delete("test.fr", "lumena_write_test", where={"id": "1"})
        assert r["ok"] is False and r["error"] == "delete_disabled"

    # --- refus si table hors delete_tables ---
    def test_propose_refused_table_not_allowed(self, deployer, monkeypatch):
        self._ready(deployer, tables=("lumena_write_test",), monkeypatch=monkeypatch)
        r = deployer.propose_delete("test.fr", "contacts", where={"id": "1"})
        assert r["ok"] is False and r["error"] == "table_not_allowed"

    # --- refus si WHERE vide/absent ---
    def test_propose_refused_missing_where(self, deployer, monkeypatch):
        self._ready(deployer, monkeypatch=monkeypatch)
        r = deployer.propose_delete("test.fr", "lumena_write_test", where=None)
        assert r["ok"] is False and r["error"] == "missing_where"

    # --- refus si estimated_count dépasse le plafond DELETE (25) ---
    def test_propose_refused_too_many_rows(self, deployer, monkeypatch):
        self._ready(deployer, monkeypatch=monkeypatch)
        monkeypatch.setattr(deployer, "db_select",
                            lambda *a, **k: {"ok": True, "count": 99})
        r = deployer.propose_delete("test.fr", "lumena_write_test", where={"is_active": "1"})
        assert r["ok"] is False and r["error"] == "too_many_rows"
        assert deployer.list_pending_actions("test.fr")["actions"] == []

    # --- succès : enfile op=delete sans exécuter, chiffré, métadonnées seules ---
    def test_propose_delete_enqueues_without_executing(self, deployer, monkeypatch):
        import src.services.ionos_deployer as mod
        self._ready(deployer, monkeypatch=monkeypatch)
        r = deployer.propose_delete("test.fr", "lumena_write_test", where={"id": "7"})
        assert r["ok"] is True and r["op"] == "delete" and r["status"] == "pending"
        assert r["where_keys"] == ["id"] and r["value_keys"] == []
        pend = deployer.list_pending_actions("test.fr")["actions"]
        assert len(pend) == 1 and pend[0]["op"] == "delete"
        # payload chiffré, aucune valeur en clair
        blob = (mod._PROPOSAL_DIR / "test.fr" / f"{r['proposal_id']}.enc").read_text(encoding="utf-8")
        assert blob.startswith("gAAAAA") and '"7"' not in blob

    # --- audit propose_delete sans valeurs ---
    def test_propose_delete_audit_no_values(self, deployer, monkeypatch, tmp_path):
        import src.services.ionos_deployer as mod
        monkeypatch.setattr(mod, "_DB_AUDIT_PATH", tmp_path / "audit.jsonl")
        self._ready(deployer, monkeypatch=monkeypatch)
        deployer.propose_delete("test.fr", "lumena_write_test", where={"id": "SECRET_WHERE_X"})
        audit = (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
        assert '"op": "propose_delete"' in audit and '"where_keys": ["id"]' in audit
        assert "SECRET_WHERE_X" not in audit and "password" not in audit

    # --- approbation exécute via db_delete (confirm_table + source=react_delete_approved) ---
    def test_approve_executes_via_db_delete(self, deployer, monkeypatch):
        self._ready(deployer, monkeypatch=monkeypatch)
        sid = deployer.propose_delete("test.fr", "lumena_write_test", where={"id": "7"})["proposal_id"]
        calls = []
        def fake_delete(domain, table, where=None, confirm=False, confirm_table="", source="ui"):
            calls.append({"table": table, "where": where, "confirm": confirm,
                          "confirm_table": confirm_table, "source": source})
            return {"ok": True, "op": "delete", "affected": 1, "snapshot_id": "snap1"}
        monkeypatch.setattr(deployer, "db_delete", fake_delete)
        # db_write ne doit JAMAIS être appelé pour un delete
        monkeypatch.setattr(deployer, "db_write",
                            lambda *a, **k: (_ for _ in ()).throw(AssertionError("delete ne doit pas passer par db_write")))
        out = deployer.approve_pending_action("test.fr", sid, confirm=True)
        assert out["ok"] is True
        assert len(calls) == 1
        c = calls[0]
        assert c["table"] == "lumena_write_test" and c["where"] == {"id": "7"}
        assert c["confirm"] is True and c["confirm_table"] == "lumena_write_test"
        assert c["source"] == "react_delete_approved"
        assert deployer.list_pending_actions("test.fr")["actions"] == []

    def test_approve_delete_requires_confirm(self, deployer, monkeypatch):
        self._ready(deployer, monkeypatch=monkeypatch)
        sid = deployer.propose_delete("test.fr", "lumena_write_test", where={"id": "7"})["proposal_id"]
        out = deployer.approve_pending_action("test.fr", sid, confirm=False)
        assert out["ok"] is False and out["error"] == "not_confirmed"

    def test_reject_delete_no_execution(self, deployer, monkeypatch):
        self._ready(deployer, monkeypatch=monkeypatch)
        sid = deployer.propose_delete("test.fr", "lumena_write_test", where={"id": "7"})["proposal_id"]
        monkeypatch.setattr(deployer, "db_delete",
                            lambda *a, **k: (_ for _ in ()).throw(AssertionError("ne doit pas exécuter")))
        out = deployer.reject_pending_action("test.fr", sid)
        assert out["ok"] is True and out["status"] == "rejected"
        assert deployer.list_pending_actions("test.fr")["actions"] == []

    # --- handler ReAct : propose-only, pas de confirm/confirm_table ---
    def test_handler_propose_delete_no_confirm_param(self):
        import inspect
        from src.reasoning.handlers.ionos import ionos_db_propose_delete_handler
        params = set(inspect.signature(ionos_db_propose_delete_handler).parameters)
        assert "confirm" not in params and "confirm_table" not in params
        assert {"site", "table", "where"} <= params

    @pytest.mark.asyncio
    async def test_handler_proposes_without_executing(self, deployer, monkeypatch):
        import src.reasoning.handlers.ionos as hmod
        from unittest.mock import MagicMock
        self._ready(deployer, monkeypatch=monkeypatch)
        hmod._deployer = deployer
        monkeypatch.setattr(deployer, "db_delete",
                            lambda *a, **k: (_ for _ in ()).throw(AssertionError("handler ne doit jamais exécuter")))
        from src.reasoning.handlers.ionos import ionos_db_propose_delete_handler
        r = await ionos_db_propose_delete_handler(MagicMock(), site="test.fr",
                                                  table="lumena_write_test", where="id=7")
        assert r.success and "Proposition DELETE" in r.output and "confirmée par un" in r.output
        assert len(deployer.list_pending_actions("test.fr")["actions"]) == 1

    @pytest.mark.asyncio
    async def test_handler_refused_when_killswitch_off(self, deployer, monkeypatch):
        import src.reasoning.handlers.ionos as hmod
        from unittest.mock import MagicMock
        self._ready(deployer, killswitch=False, monkeypatch=monkeypatch)
        hmod._deployer = deployer
        from src.reasoning.handlers.ionos import ionos_db_propose_delete_handler
        r = await ionos_db_propose_delete_handler(MagicMock(), site="test.fr",
                                                  table="lumena_write_test", where="id=7")
        assert not r.success

    @pytest.mark.asyncio
    async def test_handler_refused_without_where(self, deployer, monkeypatch):
        import src.reasoning.handlers.ionos as hmod
        from unittest.mock import MagicMock
        self._ready(deployer, monkeypatch=monkeypatch)
        hmod._deployer = deployer
        from src.reasoning.handlers.ionos import ionos_db_propose_delete_handler
        r = await ionos_db_propose_delete_handler(MagicMock(), site="test.fr",
                                                  table="lumena_write_test", where="")
        assert not r.success

    # --- routes admin protégées + sans secret ---
    @pytest.mark.asyncio
    async def test_react_delete_routes_require_admin(self, deployer):
        import httpx
        from fastapi import FastAPI
        from web.routes import ionos as ionos_routes
        _configure_db(deployer)
        app = FastAPI(); app.include_router(ionos_routes.router); ionos_routes._deployer = deployer
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            for method, url in (
                ("get", "/api/ionos/sites/test.fr/database/react-delete-config"),
                ("post", "/api/ionos/sites/test.fr/database/react-delete-config"),
            ):
                kw = {} if method == "get" else {"json": {}}
                resp = await getattr(c, method)(url, **kw)
                assert resp.status_code in (401, 403), (method, url)

    @pytest.mark.asyncio
    async def test_react_delete_config_route_roundtrip(self, deployer):
        import httpx
        _configure_db(deployer)
        app, _ = _make_ionos_app(deployer)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/api/ionos/sites/test.fr/database/react-delete-config", json={"enabled": True})
            assert resp.status_code == 200 and resp.json()["enabled"] is True
            resp2 = await c.get("/api/ionos/sites/test.fr/database/react-delete-config")
            assert resp2.json()["enabled"] is True


class TestIonosReactExposureHandlers:
    """Exposition ReAct complète encadrée : get/set configs, list, install, create sandbox."""

    @pytest.fixture(autouse=True)
    def _isolate_stores(self, monkeypatch, tmp_path):
        import src.services.ionos_deployer as mod
        monkeypatch.setattr(mod, "_SNAPSHOT_DIR", tmp_path / "snaps")
        monkeypatch.setattr(mod, "_SNAPSHOT_INDEX", tmp_path / "snaps" / "index.jsonl")
        monkeypatch.setattr(mod, "_PROPOSAL_DIR", tmp_path / "props")
        monkeypatch.setattr(mod, "_PROPOSAL_INDEX", tmp_path / "props" / "index.jsonl")

    def _wire(self, deployer):
        import src.reasoning.handlers.ionos as hmod
        _configure_db(deployer)
        hmod._deployer = deployer

    # --- read-only : aucun secret/valeur ---
    @pytest.mark.asyncio
    async def test_get_config_no_secret(self, deployer):
        from unittest.mock import MagicMock
        from src.reasoning.handlers.ionos import ionos_db_get_config_handler
        self._wire(deployer)
        r = await ionos_db_get_config_handler(MagicMock(), site="test.fr")
        assert r.success
        # base (name) en clair ; user masqué partiellement ; mot de passe jamais affiché
        assert "dbs1" in r.output
        assert "dbu1" not in r.output and "****" in r.output
        assert "db_pw" not in r.output and "password" not in r.output.lower()

    @pytest.mark.asyncio
    async def test_get_config_handlers_no_sensitive(self, deployer, monkeypatch):
        from unittest.mock import MagicMock
        import src.reasoning.handlers.ionos as hmod
        self._wire(deployer)
        deployer.set_site_write_config("test.fr", True, ["lumena_write_test"])
        deployer.set_site_delete_config("test.fr", True, ["lumena_write_test"])
        from src.reasoning.handlers.ionos import (
            ionos_db_get_write_config_handler, ionos_db_get_delete_config_handler,
            ionos_db_get_sandbox_config_handler, ionos_db_get_restore_config_handler,
            ionos_db_get_react_write_config_handler, ionos_db_get_react_delete_config_handler,
        )
        for h in (ionos_db_get_write_config_handler, ionos_db_get_delete_config_handler,
                  ionos_db_get_sandbox_config_handler, ionos_db_get_restore_config_handler,
                  ionos_db_get_react_write_config_handler, ionos_db_get_react_delete_config_handler):
            r = await h(MagicMock(), site="test.fr")
            assert r.success
            assert "db_pw" not in r.output and "password" not in r.output.lower() and "secret" not in r.output.lower()

    @pytest.mark.asyncio
    async def test_list_snapshots_pending_no_values(self, deployer, monkeypatch):
        from unittest.mock import MagicMock
        self._wire(deployer)
        # 1 snapshot + 1 proposition avec valeur sensible
        deployer._store_snapshot("test.fr", {"table": "lumena_write_test", "pk_col": "id",
                                             "op": "update", "rows": [{"id": 1, "title": "SECRET_SNAP"}]})
        deployer.set_site_write_config("test.fr", True, ["lumena_write_test"])
        deployer.set_site_react_write_config("test.fr", True)
        deployer.propose_write("test.fr", "insert", "lumena_write_test", {"title": "SECRET_PROP"})
        from src.reasoning.handlers.ionos import (
            ionos_db_list_snapshots_handler, ionos_db_list_pending_actions_handler)
        r1 = await ionos_db_list_snapshots_handler(MagicMock(), site="test.fr")
        r2 = await ionos_db_list_pending_actions_handler(MagicMock(), site="test.fr")
        assert r1.success and "SECRET_SNAP" not in r1.output
        assert r2.success and "SECRET_PROP" not in r2.output

    # --- set_* : modifie uniquement les flags ---
    @pytest.mark.asyncio
    async def test_set_handlers_modify_flags_only(self, deployer):
        from unittest.mock import MagicMock
        self._wire(deployer)
        from src.reasoning.handlers.ionos import (
            ionos_db_set_sandbox_config_handler, ionos_db_set_write_config_handler,
            ionos_db_set_delete_config_handler, ionos_db_set_restore_config_handler,
            ionos_db_set_react_write_config_handler, ionos_db_set_react_delete_config_handler,
        )
        await ionos_db_set_sandbox_config_handler(MagicMock(), site="test.fr", enabled="true")
        assert deployer.get_site_sandbox_config("test.fr") == {"enabled": True}
        await ionos_db_set_write_config_handler(MagicMock(), site="test.fr", enabled="true", tables="lumena_write_test, bad col")
        wc = deployer.get_site_write_config("test.fr")
        assert wc["enabled"] is True and wc["tables"] == ["lumena_write_test"]
        await ionos_db_set_delete_config_handler(MagicMock(), site="test.fr", enabled="1", tables="lumena_write_test")
        assert deployer.get_site_delete_config("test.fr")["enabled"] is True
        await ionos_db_set_restore_config_handler(MagicMock(), site="test.fr", enabled="true")
        assert deployer.get_site_restore_config("test.fr") == {"enabled": True}
        await ionos_db_set_react_write_config_handler(MagicMock(), site="test.fr", enabled="true")
        assert deployer.get_site_react_write_config("test.fr") == {"enabled": True}
        await ionos_db_set_react_delete_config_handler(MagicMock(), site="test.fr", enabled="false")
        assert deployer.get_site_react_delete_config("test.fr") == {"enabled": False}

    # --- create sandbox : refus si flag OFF, ne tente jamais mysql/php/node ---
    @pytest.mark.asyncio
    async def test_create_sandbox_refused_when_disabled(self, deployer, monkeypatch):
        from unittest.mock import MagicMock
        self._wire(deployer)
        # sandbox OFF par défaut + auto-sandbox global neutralisé (déterminisme)
        monkeypatch.delenv("LUMENA_IONOS_AUTO_SANDBOX_CREATE_ENABLED", raising=False)
        from src.reasoning.handlers.ionos import ionos_db_create_sandbox_table_handler
        r = await ionos_db_create_sandbox_table_handler(MagicMock(), site="test.fr",
                                                        name="demo", columns="label:VARCHAR:50")
        assert not r.success
        assert "désactivée" in r.output and "ionos_db_set_sandbox_config" in r.output
        assert "mysql" in r.output.lower()  # message anti-mysql/php/node

    @pytest.mark.asyncio
    async def test_create_sandbox_auto_enable_env_then_restore_off(self, deployer, monkeypatch):
        from unittest.mock import MagicMock
        self._wire(deployer)
        monkeypatch.setenv("LUMENA_IONOS_AUTO_SANDBOX_CREATE_ENABLED", "1")
        captured = {}

        def fake_create(domain, name, columns, confirm=False, source="ui"):
            captured.update({
                "name": name,
                "confirm": confirm,
                "source": source,
                "enabled_during_create": deployer.get_site_sandbox_config(domain)["enabled"],
            })
            return {"ok": True, "table": name, "created": True}

        monkeypatch.setattr(deployer, "db_create_sandbox_table", fake_create)
        from src.reasoning.handlers.ionos import ionos_db_create_sandbox_table_handler

        assert deployer.get_site_sandbox_config("test.fr") == {"enabled": False}
        r = await ionos_db_create_sandbox_table_handler(
            MagicMock(),
            site="test.fr",
            name="test",
            columns="label:VARCHAR:80, created_at:DATETIME",
        )

        assert r.success
        assert captured == {
            "name": "lumena_sandbox_test",
            "confirm": True,
            "source": "react",
            "enabled_during_create": True,
        }
        assert deployer.get_site_sandbox_config("test.fr") == {"enabled": False}
        assert "remise OFF" in r.output

    @pytest.mark.asyncio
    async def test_create_sandbox_imposes_prefix_and_confirm(self, deployer, monkeypatch):
        from unittest.mock import MagicMock
        self._wire(deployer)
        deployer.set_site_sandbox_config("test.fr", True)
        captured = {}
        def fake_create(domain, name, columns, confirm=False, source="ui"):
            captured.update({"name": name, "confirm": confirm, "source": source, "columns": columns})
            return {"ok": True, "table": name, "created": True}
        monkeypatch.setattr(deployer, "db_create_sandbox_table", fake_create)
        from src.reasoning.handlers.ionos import ionos_db_create_sandbox_table_handler
        r = await ionos_db_create_sandbox_table_handler(MagicMock(), site="test.fr",
                                                        name="demo", columns="label:VARCHAR:120, qty:INT")
        assert r.success
        assert captured["name"] == "lumena_sandbox_demo"   # préfixe imposé
        assert captured["confirm"] is True and captured["source"] == "react"
        assert captured["columns"] == [{"name": "label", "type": "VARCHAR", "length": 120},
                                       {"name": "qty", "type": "INT"}]

    @pytest.mark.asyncio
    async def test_install_bridge_handler(self, deployer):
        from unittest.mock import MagicMock
        self._wire(deployer)
        from src.reasoning.handlers.ionos import ionos_db_bridge_status_handler, ionos_db_install_bridge_handler
        r = await ionos_db_install_bridge_handler(MagicMock(), site="test.fr")
        assert r.success and "installé" in r.output
        st = await ionos_db_bridge_status_handler(MagicMock(), site="test.fr")
        assert st.success and "version=9" in st.output


def test_ionos_get_config_partial_masking():
    """Masquage partiel : host/user partiellement masqués, base en clair."""
    from src.reasoning.handlers.ionos import _mask_mid, _mask_host
    assert _mask_mid("dbu4924776") == "dbu****776"
    assert _mask_mid("dbu1") == "d****"            # trop court → réduit
    assert _mask_mid("") == ""
    assert _mask_host("db5020513717.hosting-data.io") == "db50****.hosting-data.io"
    assert _mask_host("db.host.io") == "d****.host.io"
    assert _mask_host("") == ""


def test_handler_and_route_share_same_deployer(monkeypatch):
    """Bug fix : handlers ReAct et routes web partagent UNE instance IonosDeployer."""
    import src.services.ionos_deployer as svc
    import src.reasoning.handlers.ionos as hmod
    import web.routes.ionos as rmod
    monkeypatch.setattr(svc, "_shared_deployer", None)
    monkeypatch.setattr(hmod, "_deployer", None)
    monkeypatch.setattr(rmod, "_deployer", None)
    d1 = hmod._get_deployer()
    d2 = rmod._get_deployer()
    assert d1 is d2 is svc.get_shared_deployer()


class TestIonosApprovePathIntegration:
    """Étape 4.5A bug : approve_pending_action doit respecter la write_config limitée."""

    @pytest.fixture(autouse=True)
    def _isolate_stores(self, monkeypatch, tmp_path):
        import src.services.ionos_deployer as mod
        monkeypatch.setattr(mod, "_SNAPSHOT_DIR", tmp_path / "snaps")
        monkeypatch.setattr(mod, "_SNAPSHOT_INDEX", tmp_path / "snaps" / "index.jsonl")
        monkeypatch.setattr(mod, "_PROPOSAL_DIR", tmp_path / "props")
        monkeypatch.setattr(mod, "_PROPOSAL_INDEX", tmp_path / "props" / "index.jsonl")

    def _ready(self, deployer, tables=("lumena_sandbox_test_v2",)):
        import asyncio as _aio
        _configure_db(deployer)
        _aio.run(deployer.install_database_bridge("test.fr"))
        deployer.set_site_write_config("test.fr", True, list(tables))
        deployer.set_site_react_write_config("test.fr", True)

    def test_propose_then_approve_executes_insert(self, deployer, monkeypatch):
        self._ready(deployer)
        prop = deployer.propose_write("test.fr", "insert", "lumena_sandbox_test_v2", {"label": "x"})
        assert prop["ok"] is True
        captured = {}
        def fake(domain, secret, path, op, body, timeout, ts=None, nonce=None):
            captured["op"] = op; captured["table"] = body.get("table"); captured["wop"] = body.get("wop")
            return {"_http_status": 200, "ok": True, "op": "insert", "affected": 1,
                    "snapshot_count": 0, "warning": ""}
        monkeypatch.setattr(deployer, "_bridge_request", fake)
        out = deployer.approve_pending_action("test.fr", prop["proposal_id"], confirm=True)
        assert out["ok"] is True and out["affected"] == 1
        # l'INSERT est bien parti vers le bridge sur la table sandbox allowlistée
        assert captured["op"] == "db_write" and captured["wop"] == "insert"
        assert captured["table"] == "lumena_sandbox_test_v2"
        assert deployer.list_pending_actions("test.fr")["actions"] == []

    def test_approve_refused_if_write_disabled_after_propose(self, deployer):
        self._ready(deployer)
        prop = deployer.propose_write("test.fr", "insert", "lumena_sandbox_test_v2", {"label": "x"})
        deployer.set_site_write_config("test.fr", False, [])   # écriture coupée après propose
        out = deployer.approve_pending_action("test.fr", prop["proposal_id"], confirm=True)
        assert out["ok"] is False and out["error"] == "write_disabled"

    def test_approve_refused_if_table_removed_from_allowlist(self, deployer):
        self._ready(deployer)
        prop = deployer.propose_write("test.fr", "insert", "lumena_sandbox_test_v2", {"label": "x"})
        deployer.set_site_write_config("test.fr", True, ["autre_table"])  # table retirée de l'allowlist
        out = deployer.approve_pending_action("test.fr", prop["proposal_id"], confirm=True)
        assert out["ok"] is False and out["error"] == "table_not_allowed"


def test_remask_observed_masked_values_in_final():
    """Le finalizer ré-impose les valeurs masquées vues en observation (anti-reconstitution)."""
    from unittest.mock import MagicMock

    # ReActAgent sans __init__ : on teste la méthode pure sur un historique simulé.
    from src.reasoning.react import ReActLoop
    agent = object.__new__(ReActLoop)

    obs = MagicMock()
    obs.content = ("**BDD openlumena.com** — host=`db50****.hosting-data.io` port=3306 "
                   "base=`dbs15704993` user=`dbu****776` moteur=mariadb")
    step = MagicMock(); step.observation = obs
    agent.history = [step]

    # Le modèle a halluciné des valeurs concrètes dans le FINAL.
    final = ("Infos BDD openlumena.com :\n"
             "- host : db506225391.hosting-data.io\n"
             "- base : dbs15704993\n"
             "- user : dbu281776\n")
    out = agent._remask_observed_masked_values(final)
    # valeurs reconstituées remplacées par la forme masquée
    assert "db506225391" not in out and "dbu281776" not in out
    assert "db50****.hosting-data.io" in out and "dbu****776" in out
    # base (non masquée) conservée
    assert "dbs15704993" in out


def test_remask_noop_without_masked_observation():
    """Sans valeur masquée en observation, le finalizer ne touche à rien."""
    from unittest.mock import MagicMock
    from src.reasoning.react import ReActLoop
    agent = object.__new__(ReActLoop)
    obs = MagicMock(); obs.content = "Aucune BDD configurée."
    step = MagicMock(); step.observation = obs
    agent.history = [step]
    txt = "Réponse normale avec db506225391.hosting-data.io mentionné."
    assert agent._remask_observed_masked_values(txt) == txt


@pytest.mark.asyncio
async def test_describe_table_warns_structure_only(deployer, monkeypatch):
    """describe_table indique explicitement structure-only (anti-déduction de contenu)."""
    from unittest.mock import MagicMock
    import src.reasoning.handlers.ionos as hmod
    _configure_db(deployer)
    hmod._deployer = deployer
    monkeypatch.setattr(deployer, "db_describe_table",
                        lambda dom, table: {"ok": True, "columns": [{"field": "id", "type": "int", "key": "PRI"}]})
    from src.reasoning.handlers.ionos import ionos_db_describe_table_handler
    r = await ionos_db_describe_table_handler(MagicMock(), site="test.fr", table="lumena_sandbox_x")
    assert r.success
    low = r.output.lower()
    assert "structure" in low and "ionos_db_select" in r.output
    assert "pas le contenu" in low or "jamais le nombre" in low


def test_read_file_redacts_config_php_secrets():
    """read_file masque les valeurs de secrets (config.php / .env), garde les clés."""
    from src.reasoning.handlers.files import _redact_secrets
    php = ("<?php\ndefine('DB_PASSWORD', 'SuperSecret123');\n"
           "$db_pass = \"hunter2\";\n$db_user = 'dbu123';\n")
    out = _redact_secrets(php)
    assert "SuperSecret123" not in out and "hunter2" not in out
    assert "DB_PASSWORD" in out and "***REDACTED***" in out
    assert "dbu123" in out  # user non masqué
    env = "ADMIN_SETUP_TOKEN=abc123def\nCLIENT_SECRET=xyz789\nLUMENA_PORT=8080\n"
    oenv = _redact_secrets(env)
    assert "abc123def" not in oenv and "xyz789" not in oenv
    assert "8080" in oenv  # valeur non sensible préservée


def test_ionos_auto_sandbox_config_is_exposed_and_synced():
    from pathlib import Path
    from scripts.sync_env_example import render_env_example
    from web.routes.config import _CONFIG_SCHEMA

    entry = next((e for e in _CONFIG_SCHEMA if e["key"] == "LUMENA_IONOS_AUTO_SANDBOX_CREATE_ENABLED"), None)
    assert entry is not None
    assert entry["group"] == "IONOS (Hébergement)"
    assert entry["type"] == "bool"
    assert entry["default"] == "0"
    rendered = render_env_example()
    assert "LUMENA_IONOS_AUTO_SANDBOX_CREATE_ENABLED=0" in rendered
    assert Path(".env.example").read_text(encoding="utf-8") == rendered


@pytest.mark.asyncio
async def test_run_command_blocks_ionos_db_direct_access():
    """run_command bloque mysql/php/node visant une BDD IONOS → message bridge."""
    from unittest.mock import MagicMock
    from src.reasoning.handlers.system import run_command_handler
    ctx = MagicMock()
    ctx.is_ide_runtime.return_value = False
    ctx._discovered_executables = None
    ctx.lumena_root = "."
    r = await run_command_handler(ctx, command="mysql -h db5020513717.hosting-data.io -u dbu1 -p")
    assert "bridge IONOS" in r.output.lower() or "ionos_db_" in r.output
    assert "ionos_db_" in r.output


class TestIonosSandboxDropStep46:
    """Étape 4.6 : DROP sandbox encadré (propose-only, kill-switch + flag, table VIDE)."""

    @pytest.fixture(autouse=True)
    def _isolate_stores(self, monkeypatch, tmp_path):
        import src.services.ionos_deployer as mod
        monkeypatch.setattr(mod, "_SNAPSHOT_DIR", tmp_path / "snaps")
        monkeypatch.setattr(mod, "_SNAPSHOT_INDEX", tmp_path / "snaps" / "index.jsonl")
        monkeypatch.setattr(mod, "_PROPOSAL_DIR", tmp_path / "props")
        monkeypatch.setattr(mod, "_PROPOSAL_INDEX", tmp_path / "props" / "index.jsonl")

    def _ready(self, deployer, killswitch=True, drop=True, monkeypatch=None, install=False):
        import asyncio as _aio
        _configure_db(deployer)
        if install:
            _aio.run(deployer.install_database_bridge("test.fr"))
        deployer.set_site_sandbox_drop_config("test.fr", drop)
        if monkeypatch is not None:
            monkeypatch.setenv("LUMENA_IONOS_SANDBOX_DROP_ENABLED", "1" if killswitch else "0")

    def test_drop_config_off_by_default(self, deployer):
        _configure_db(deployer)
        assert deployer.get_site_sandbox_drop_config("test.fr") == {"enabled": False}
        deployer.set_site_sandbox_drop_config("test.fr", True)
        assert deployer.get_site_sandbox_drop_config("test.fr") == {"enabled": True}

    def test_killswitch_off_by_default(self, deployer, monkeypatch):
        monkeypatch.delenv("LUMENA_IONOS_SANDBOX_DROP_ENABLED", raising=False)
        assert deployer._sandbox_drop_killswitch_on() is False

    def test_propose_drop_refused_by_default(self, deployer, monkeypatch):
        monkeypatch.delenv("LUMENA_IONOS_SANDBOX_DROP_ENABLED", raising=False)
        _configure_db(deployer)
        r = deployer.propose_drop_sandbox("test.fr", "lumena_sandbox_demo")
        assert r["ok"] is False and r["error"] == "sandbox_drop_killswitch_off"
        assert deployer.list_pending_actions("test.fr")["actions"] == []

    def test_propose_drop_refused_without_killswitch(self, deployer, monkeypatch):
        self._ready(deployer, killswitch=False, drop=True, monkeypatch=monkeypatch)
        r = deployer.propose_drop_sandbox("test.fr", "lumena_sandbox_demo")
        assert r["ok"] is False and r["error"] == "sandbox_drop_killswitch_off"

    def test_propose_drop_refused_flag_off(self, deployer, monkeypatch):
        self._ready(deployer, killswitch=True, drop=False, monkeypatch=monkeypatch)
        r = deployer.propose_drop_sandbox("test.fr", "lumena_sandbox_demo")
        assert r["ok"] is False and r["error"] == "sandbox_drop_disabled"

    def test_propose_drop_refused_bad_prefix(self, deployer, monkeypatch):
        self._ready(deployer, monkeypatch=monkeypatch)
        for bad in ("users", "sessions", "verification_codes", "announcements", "config"):
            r = deployer.propose_drop_sandbox("test.fr", bad)
            assert r["ok"] is False and r["error"] == "bad_prefix", bad
        assert deployer.list_pending_actions("test.fr")["actions"] == []

    def test_propose_drop_refused_if_table_not_empty(self, deployer, monkeypatch):
        # Cas 1 : la proposition NE doit PAS être créée si la table n'est pas vide.
        self._ready(deployer, monkeypatch=monkeypatch)
        monkeypatch.setattr(deployer, "db_select", lambda *a, **k: {"ok": True, "count": 1})
        r = deployer.propose_drop_sandbox("test.fr", "lumena_sandbox_test_v2")
        assert r["ok"] is False and r["error"] == "table_not_empty"
        assert deployer.list_pending_actions("test.fr")["actions"] == []

    def test_propose_drop_enqueues_without_executing(self, deployer, monkeypatch):
        self._ready(deployer, monkeypatch=monkeypatch)
        monkeypatch.setattr(deployer, "db_select", lambda *a, **k: {"ok": True, "count": 0})  # table vide
        r = deployer.propose_drop_sandbox("test.fr", "lumena_sandbox_demo")
        assert r["ok"] is True and r["op"] == "drop_sandbox" and r["status"] == "pending"
        pend = deployer.list_pending_actions("test.fr")["actions"]
        assert len(pend) == 1 and pend[0]["op"] == "drop_sandbox" and pend[0]["table"] == "lumena_sandbox_demo"

    def test_approve_executes_drop_on_empty_sandbox(self, deployer, monkeypatch):
        self._ready(deployer, monkeypatch=monkeypatch, install=True)
        prop = deployer.propose_drop_sandbox("test.fr", "lumena_sandbox_demo")
        captured = {}
        def fake(domain, secret, path, op, body, timeout, ts=None, nonce=None):
            captured["op"] = op; captured["name"] = body.get("name")
            return {"_http_status": 200, "ok": True, "op": "drop_sandbox",
                    "table": body.get("name"), "dropped": True}
        monkeypatch.setattr(deployer, "_bridge_request", fake)
        out = deployer.approve_pending_action("test.fr", prop["proposal_id"], confirm=True)
        assert out["ok"] is True and out["dropped"] is True
        assert captured["op"] == "db_drop_sandbox_table" and captured["name"] == "lumena_sandbox_demo"
        assert deployer.list_pending_actions("test.fr")["actions"] == []

    def test_approve_refused_if_table_not_empty(self, deployer, monkeypatch):
        self._ready(deployer, monkeypatch=monkeypatch, install=True)
        prop = deployer.propose_drop_sandbox("test.fr", "lumena_sandbox_demo")
        monkeypatch.setattr(deployer, "_bridge_request",
                            lambda *a, **k: {"_http_status": 200, "ok": False, "error": "table_not_empty", "rows": 3})
        out = deployer.approve_pending_action("test.fr", prop["proposal_id"], confirm=True)
        assert out["ok"] is False and out["error"] == "table_not_empty"
        assert "vide" in out["message"].lower()

    def test_drop_executor_guards(self, deployer, monkeypatch):
        self._ready(deployer, monkeypatch=monkeypatch, install=True)
        assert deployer.drop_sandbox_table("test.fr", "users", confirm=True, confirm_table="users")["error"] == "bad_prefix"
        assert deployer.drop_sandbox_table("test.fr", "lumena_sandbox_demo", confirm=False, confirm_table="lumena_sandbox_demo")["error"] == "not_confirmed"
        assert deployer.drop_sandbox_table("test.fr", "lumena_sandbox_demo", confirm=True, confirm_table="autre")["error"] == "confirm_mismatch"
        deployer.set_site_sandbox_drop_config("test.fr", False)
        assert deployer.drop_sandbox_table("test.fr", "lumena_sandbox_demo", confirm=True, confirm_table="lumena_sandbox_demo")["error"] == "sandbox_drop_disabled"

    def test_handler_propose_drop_no_confirm_param(self):
        import inspect
        from src.reasoning.handlers.ionos import ionos_db_propose_drop_sandbox_table_handler
        params = set(inspect.signature(ionos_db_propose_drop_sandbox_table_handler).parameters)
        assert "confirm" not in params and "confirm_table" not in params
        assert {"site", "table"} <= params

    @pytest.mark.asyncio
    async def test_handler_proposes_without_executing(self, deployer, monkeypatch):
        import src.reasoning.handlers.ionos as hmod
        from unittest.mock import MagicMock
        _configure_db(deployer)
        deployer.set_site_sandbox_drop_config("test.fr", True)
        monkeypatch.setenv("LUMENA_IONOS_SANDBOX_DROP_ENABLED", "1")
        hmod._deployer = deployer
        monkeypatch.setattr(deployer, "drop_sandbox_table",
                            lambda *a, **k: (_ for _ in ()).throw(AssertionError("handler ne doit jamais exécuter")))
        from src.reasoning.handlers.ionos import ionos_db_propose_drop_sandbox_table_handler
        r = await ionos_db_propose_drop_sandbox_table_handler(MagicMock(), site="test.fr", table="lumena_sandbox_demo")
        assert r.success and "Proposition DROP" in r.output
        assert len(deployer.list_pending_actions("test.fr")["actions"]) == 1

    @pytest.mark.asyncio
    async def test_handler_refused_when_killswitch_off(self, deployer, monkeypatch):
        import src.reasoning.handlers.ionos as hmod
        from unittest.mock import MagicMock
        _configure_db(deployer)
        deployer.set_site_sandbox_drop_config("test.fr", True)
        monkeypatch.setenv("LUMENA_IONOS_SANDBOX_DROP_ENABLED", "0")
        hmod._deployer = deployer
        from src.reasoning.handlers.ionos import ionos_db_propose_drop_sandbox_table_handler
        r = await ionos_db_propose_drop_sandbox_table_handler(MagicMock(), site="test.fr", table="lumena_sandbox_demo")
        assert not r.success

    @pytest.mark.asyncio
    async def test_sandbox_drop_routes_require_admin(self, deployer):
        import httpx
        from fastapi import FastAPI
        from web.routes import ionos as ionos_routes
        _configure_db(deployer)
        app = FastAPI(); app.include_router(ionos_routes.router); ionos_routes._deployer = deployer
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            for method, url in (
                ("get", "/api/ionos/sites/test.fr/database/sandbox-drop-config"),
                ("post", "/api/ionos/sites/test.fr/database/sandbox-drop-config"),
            ):
                kw = {} if method == "get" else {"json": {}}
                resp = await getattr(c, method)(url, **kw)
                assert resp.status_code in (401, 403), (method, url)

    @pytest.mark.asyncio
    async def test_sandbox_drop_config_route_roundtrip(self, deployer):
        import httpx
        _configure_db(deployer)
        app, _ = _make_ionos_app(deployer)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/api/ionos/sites/test.fr/database/sandbox-drop-config", json={"enabled": True})
            assert resp.status_code == 200 and resp.json()["enabled"] is True
            resp2 = await c.get("/api/ionos/sites/test.fr/database/sandbox-drop-config")
            assert resp2.json()["enabled"] is True


class TestIonosSandboxClearStep47:
    """Étape 4.7 : CLEAR (vidage) sandbox encadré (propose-only, flag, DELETE total contrôlé + snapshot)."""

    @pytest.fixture(autouse=True)
    def _isolate_stores(self, monkeypatch, tmp_path):
        import src.services.ionos_deployer as mod
        monkeypatch.setattr(mod, "_SNAPSHOT_DIR", tmp_path / "snaps")
        monkeypatch.setattr(mod, "_SNAPSHOT_INDEX", tmp_path / "snaps" / "index.jsonl")
        monkeypatch.setattr(mod, "_PROPOSAL_DIR", tmp_path / "props")
        monkeypatch.setattr(mod, "_PROPOSAL_INDEX", tmp_path / "props" / "index.jsonl")

    def _ready(self, deployer, clear=True, install=False):
        import asyncio as _aio
        _configure_db(deployer)
        if install:
            _aio.run(deployer.install_database_bridge("test.fr"))
        deployer.set_site_sandbox_clear_config("test.fr", clear)

    def test_clear_config_off_by_default(self, deployer):
        _configure_db(deployer)
        assert deployer.get_site_sandbox_clear_config("test.fr") == {"enabled": False}
        deployer.set_site_sandbox_clear_config("test.fr", True)
        assert deployer.get_site_sandbox_clear_config("test.fr") == {"enabled": True}

    def test_propose_clear_refused_flag_off(self, deployer, monkeypatch):
        self._ready(deployer, clear=False)
        monkeypatch.setattr(deployer, "db_select", lambda *a, **k: {"ok": True, "count": 5})
        r = deployer.propose_clear_sandbox("test.fr", "lumena_sandbox_test_v2")
        assert r["ok"] is False and r["error"] == "sandbox_clear_disabled"

    def test_propose_clear_refused_bad_prefix(self, deployer):
        self._ready(deployer)
        for bad in ("users", "sessions", "verification_codes", "announcements"):
            r = deployer.propose_clear_sandbox("test.fr", bad)
            assert r["ok"] is False and r["error"] == "bad_prefix", bad

    def test_propose_clear_already_empty(self, deployer, monkeypatch):
        self._ready(deployer)
        monkeypatch.setattr(deployer, "db_select", lambda *a, **k: {"ok": True, "count": 0})
        r = deployer.propose_clear_sandbox("test.fr", "lumena_sandbox_test_v2")
        assert r["ok"] is True and r["status"] == "already_empty" and r["proposal_id"] is None
        assert deployer.list_pending_actions("test.fr")["actions"] == []

    def test_propose_clear_refused_too_many(self, deployer, monkeypatch):
        self._ready(deployer)
        monkeypatch.setattr(deployer, "db_select", lambda *a, **k: {"ok": True, "count": 999})
        r = deployer.propose_clear_sandbox("test.fr", "lumena_sandbox_test_v2")
        assert r["ok"] is False and r["error"] == "too_many_rows"
        assert deployer.list_pending_actions("test.fr")["actions"] == []

    def test_propose_clear_enqueues_without_executing(self, deployer, monkeypatch):
        self._ready(deployer)
        monkeypatch.setattr(deployer, "db_select", lambda *a, **k: {"ok": True, "count": 4})
        r = deployer.propose_clear_sandbox("test.fr", "lumena_sandbox_test_v2")
        assert r["ok"] is True and r["op"] == "clear_sandbox" and r["estimated_count"] == 4
        pend = deployer.list_pending_actions("test.fr")["actions"]
        assert len(pend) == 1 and pend[0]["op"] == "clear_sandbox"

    def test_approve_executes_clear_with_snapshot(self, deployer, monkeypatch):
        from src.services.ionos_deployer import _seal_creds
        self._ready(deployer, install=True)
        monkeypatch.setattr(deployer, "db_select", lambda *a, **k: {"ok": True, "count": 2})
        prop = deployer.propose_clear_sandbox("test.fr", "lumena_sandbox_test_v2")
        captured = {}
        def fake(domain, secret, path, op, body, timeout, ts=None, nonce=None):
            captured["op"] = op; captured["name"] = body.get("name")
            snap = {"table": "lumena_sandbox_test_v2", "pk_col": "id", "op": "delete",
                    "rows": [{"id": 1, "label": "a"}, {"id": 2, "label": "b"}]}
            enc = _seal_creds(secret, snap, "db_clear_sandbox_table", ts, nonce)
            return {"_http_status": 200, "ok": True, "op": "clear_sandbox",
                    "table": body.get("name"), "affected": 2, "snapshot_count": 2, "snapshot_enc": enc}
        monkeypatch.setattr(deployer, "_bridge_request", fake)
        out = deployer.approve_pending_action("test.fr", prop["proposal_id"], confirm=True)
        assert out["ok"] is True and out["affected"] == 2 and out["snapshot_id"]
        assert captured["op"] == "db_clear_sandbox_table" and captured["name"] == "lumena_sandbox_test_v2"
        # snapshot restaurable enregistré (métadonnées, aucune valeur)
        snaps = deployer.list_snapshots("test.fr")["snapshots"]
        assert len(snaps) == 1 and snaps[0]["op"] == "delete"

    def test_clear_executor_guards(self, deployer, monkeypatch):
        self._ready(deployer, install=True)
        assert deployer.clear_sandbox_table("test.fr", "users", confirm=True, confirm_table="users")["error"] == "bad_prefix"
        assert deployer.clear_sandbox_table("test.fr", "lumena_sandbox_test_v2", confirm=False, confirm_table="lumena_sandbox_test_v2")["error"] == "not_confirmed"
        assert deployer.clear_sandbox_table("test.fr", "lumena_sandbox_test_v2", confirm=True, confirm_table="x")["error"] == "confirm_mismatch"
        deployer.set_site_sandbox_clear_config("test.fr", False)
        assert deployer.clear_sandbox_table("test.fr", "lumena_sandbox_test_v2", confirm=True, confirm_table="lumena_sandbox_test_v2")["error"] == "sandbox_clear_disabled"

    def test_handler_propose_clear_no_confirm_param(self):
        import inspect
        from src.reasoning.handlers.ionos import ionos_db_propose_clear_sandbox_table_handler
        params = set(inspect.signature(ionos_db_propose_clear_sandbox_table_handler).parameters)
        assert "confirm" not in params and "confirm_table" not in params
        assert {"site", "table"} <= params

    @pytest.mark.asyncio
    async def test_handler_proposes_without_executing(self, deployer, monkeypatch):
        import src.reasoning.handlers.ionos as hmod
        from unittest.mock import MagicMock
        self._ready(deployer)
        monkeypatch.setattr(deployer, "db_select", lambda *a, **k: {"ok": True, "count": 3})
        hmod._deployer = deployer
        monkeypatch.setattr(deployer, "clear_sandbox_table",
                            lambda *a, **k: (_ for _ in ()).throw(AssertionError("handler ne doit jamais exécuter")))
        from src.reasoning.handlers.ionos import ionos_db_propose_clear_sandbox_table_handler
        r = await ionos_db_propose_clear_sandbox_table_handler(MagicMock(), site="test.fr", table="lumena_sandbox_test_v2")
        assert r.success and "VIDAGE" in r.output
        assert len(deployer.list_pending_actions("test.fr")["actions"]) == 1

    @pytest.mark.asyncio
    async def test_clear_routes_require_admin(self, deployer):
        import httpx
        from fastapi import FastAPI
        from web.routes import ionos as ionos_routes
        _configure_db(deployer)
        app = FastAPI(); app.include_router(ionos_routes.router); ionos_routes._deployer = deployer
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            for method, url in (
                ("get", "/api/ionos/sites/test.fr/database/sandbox-clear-config"),
                ("post", "/api/ionos/sites/test.fr/database/sandbox-clear-config"),
            ):
                kw = {} if method == "get" else {"json": {}}
                resp = await getattr(c, method)(url, **kw)
                assert resp.status_code in (401, 403), (method, url)


def test_bridge_php_v9_security_controls_present():
    """Bridge v9 : read-only + write + sandbox create/DROP/CLEAR + snapshot + DELETE ; DROP/CLEAR via ops dédiées ; AUCUN ALTER/TRUNCATE/RENAME/SQL libre."""
    from src.services.ionos_deployer import _render_bridge_php
    php = _render_bridge_php("deadbeef", "9")
    low = php.lower()
    # ops présentes (lecture + write + create/drop/clear sandbox + delete)
    for op in ("handshake", "db_ping", "db_tables", "db_describe", "db_select",
               "db_write", "db_create_table", "db_delete", "db_drop_sandbox_table",
               "db_clear_sandbox_table"):
        assert op in php, op
    # read-only + prepared + fallback mysqlnd
    assert "new mysqli" in low and "->ping()" in low and "show tables" in low and "describe" in low
    assert "->prepare(" in low and "bind_params_ref" in low and "call_user_func_array" in low
    assert "mysqli_stmt_get_result" in low and "bind_result" in low and "result_metadata" in low
    assert "clamp_limit" in low
    # WRITE : INSERT/UPDATE + transaction + rollback
    assert "insert into" in low and "update `" in low
    assert "begin_transaction" in low and "->commit(" in low and "->rollback(" in low
    assert "too_many_rows" in low and "no_rows_modified" in low
    # SNAPSHOT : capture image-avant dans la transaction, AES-GCM en transit
    assert "aes_seal" in low                               # helper de chiffrement AES-GCM
    assert "aes-256-gcm" in low                            # algo en transit
    assert "show keys from" in low and "key_name" in low   # détection de la clé primaire
    assert "snapshot_no_pk" in low                         # refus si pas de PK
    assert "snapshot_too_large" in low                     # refus si dépasse les bornes
    assert "snapshot_failed" in low                        # refus si chiffrement échoue
    assert "snapshot_enc" in low                           # blob chiffré renvoyé
    # DELETE contrôlé : op db_delete, DELETE préparé, WHERE obligatoire, snapshot op:'delete'
    assert "delete from `" in low                          # DELETE construit (préparé)
    assert "'delete'" in low                               # op snapshot 'delete'
    assert "missing_where" in low                          # jamais de DELETE total
    assert "no_rows_deleted" in low                        # warning suppression vide
    # CREATE sandbox : préfixe imposé + IF NOT EXISTS + engine/charset + types whitelistés
    assert "create table if not exists" in low
    assert "lumena_sandbox_" in low
    assert "engine=innodb default charset=utf8mb4" in low
    assert "auto_increment primary key" in low
    assert "bad_prefix" in low and "bad_type" in low
    # DROP sandbox (v8) : op db_drop_sandbox_table, DROP préparé, table VIDE exigée
    assert "drop table `" in low                           # DROP construit (op dédiée)
    assert "table_not_empty" in low                        # refus si non vide
    # le DROP n'apparaît QUE dans l'op db_drop_sandbox_table (aucun DROP générique)
    _drop_section = low.split("db_drop_sandbox_table", 1)[1] if "db_drop_sandbox_table" in low else ""
    assert "drop table" in _drop_section                   # le seul DROP est dans cette op
    assert low.count("drop table `") == 1                  # un unique DROP construit
    # CLEAR sandbox (v9) : op db_clear_sandbox_table, DELETE total contrôlé + snapshot, plafond
    _clear_section = low.split("db_clear_sandbox_table", 1)[1] if "db_clear_sandbox_table" in low else ""
    assert "delete from `" in _clear_section               # vidage via op dédiée
    assert "aes_seal" in _clear_section                     # snapshot avant vidage
    assert "already_empty" in low                           # 0 ligne → déjà vide
    # AUCUN ALTER/TRUNCATE/RENAME/REPLACE/DROP générique (DROP DATABASE, etc.)
    for forbidden in ("alter ", "truncate ", "rename ", "replace into", "drop database", "drop schema"):
        assert forbidden not in low, forbidden
    # contrôles sécurité (3C) toujours présents
    assert "hash_hmac" in php and "x_forwarded_proto" in low and "flock" in low
    assert "nonce_unavailable" in low or "replay" in low
    assert "openssl_decrypt" in low and "hash_hkdf" in low
    # erreurs neutres (jamais le SQL)
    assert "'db_error'" in php or '"db_error"' in php


def test_forbidden_files_still_block_htaccess():
    """Sentinelle non-régression : le filtre interdits reste actif (.htaccess, .env)."""
    from src.services.ionos_deployer import IonosDeployer
    assert IonosDeployer._is_forbidden(Path(".htaccess")) is True
    assert IonosDeployer._is_forbidden(Path(".env")) is True
    assert IonosDeployer._is_forbidden(Path("index.php")) is False


# ═════════════════════════════════════════════════════════════════════════
# Étape 3C — Handshake HTTPS signé + test BDD via bridge (connect+ping only)
# ═════════════════════════════════════════════════════════════════════════


def test_crypto_seal_open_roundtrip_python():
    """AES-256-GCM + HKDF : round-trip Python (interop PHP validée en prod)."""
    from src.services.ionos_deployer import _seal_creds, _open_creds
    secret = "s3cr3t-bridge"
    creds = {"host": "db.h", "port": 3306, "user": "u", "password": "p@ss/+x", "name": "n"}
    sealed = _seal_creds(secret, creds, "db_ping", 1700000000, "abc123")
    assert set(sealed) == {"iv", "ct", "tag"}
    out = _open_creds(secret, sealed, "db_ping", 1700000000, "abc123")
    assert out == creds


def test_crypto_aad_binding():
    """Un changement d'AAD (ts/nonce) invalide le déchiffrement (anti-rejeu lié)."""
    from src.services.ionos_deployer import _seal_creds, _open_creds
    secret = "s"
    sealed = _seal_creds(secret, {"x": 1}, "db_ping", 111, "n1")
    with pytest.raises(Exception):
        _open_creds(secret, sealed, "db_ping", 222, "n1")  # ts différent → échec


def test_bridge_sign_deterministic_no_spaces():
    """La signature HMAC est déterministe et sans espaces (interop json_encode)."""
    from src.services.ionos_deployer import _bridge_sign
    s1 = _bridge_sign("sec", "handshake", "", 100, "n")
    s2 = _bridge_sign("sec", "handshake", "", 100, "n")
    assert s1 == s2 and len(s1) == 64


class TestBridgeTestConnectionRouting3C:
    """test_database_connection route via bridge quand installé."""

    def test_routes_via_bridge_when_installed(self, deployer, monkeypatch):
        import asyncio as _aio
        _configure_db(deployer)
        _aio.run(deployer.install_database_bridge("test.fr"))
        # mock le ping bridge (pas de vrai HTTP)
        monkeypatch.setattr(deployer, "_bridge_db_ping",
                            lambda *a, **k: (True, 42, ""))
        result = deployer.test_database_connection("test.fr")
        assert result["via"] == "bridge"
        assert result["ok"] is True
        assert result["latency_ms"] == 42

    def test_bridge_failure_clean_message(self, deployer, monkeypatch):
        import asyncio as _aio
        _configure_db(deployer)
        _aio.run(deployer.install_database_bridge("test.fr"))
        monkeypatch.setattr(deployer, "_bridge_db_ping",
                            lambda *a, **k: (False, 10, "[Errno 11001] getaddrinfo failed"))
        result = deployer.test_database_connection("test.fr")
        assert result["via"] == "bridge"
        assert result["ok"] is False
        assert "getaddrinfo" not in result["message"]   # message clair, pas brut

    def test_bridge_v1_requires_upgrade(self, deployer):
        import asyncio as _aio
        _configure_db(deployer)
        _aio.run(deployer.install_database_bridge("test.fr"))
        # simule un vieux bridge v1
        deployer._sites["test.fr"]["database"]["bridge"]["version"] = "1"
        result = deployer.test_database_connection("test.fr")
        assert result["via"] == "bridge"
        assert result["upgrade_required"] is True
        assert result["ok"] is False
        assert "à mettre à jour" in result["message"].lower() or "réinstalle" in result["message"].lower()


import shutil as _shutil


@pytest.mark.skipif(_shutil.which("php") is None, reason="PHP CLI absent : interop PHP non testée localement")
def test_bridge_php_lints_when_php_available():
    """php -l sur le squelette rendu v8 (skippé si PHP CLI absent)."""
    import subprocess, tempfile, os as _os
    from src.services.ionos_deployer import _render_bridge_php
    php = _render_bridge_php("deadbeef", "9")
    fd, path = tempfile.mkstemp(suffix=".php")
    try:
        with _os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(php)
        r = subprocess.run(["php", "-l", path], capture_output=True, text=True)
        assert r.returncode == 0, r.stdout + r.stderr
    finally:
        _os.remove(path)
