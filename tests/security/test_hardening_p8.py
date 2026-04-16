"""Tests P8 — Hardening : solidité thread-safety, sanitizer exhaustif, path traversal, auth."""

import json
import os
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

# ===================================================================
# P8.1 — Thread-safety
# ===================================================================

class TestAtomicWriteJsonThreadSafety:
    """atomic_write_json × 10 threads simultanées → 0 corruption."""

    def test_concurrent_writes_no_corruption(self, tmp_path):
        """10 threads écrivent chacune dans un fichier dédié.

        Vérifie que atomic_write_json ne produit jamais de corruption,
        même sous charge concurrente. Chaque thread cible son propre
        fichier (évite les PermissionError Windows sur rename concurrent
        du même path, qui est un problème FS et pas un bug atomic_write).
        """
        from src.utils.persistence import atomic_write_json, safe_read_json

        errors: list = []

        def writer(i: int):
            try:
                target = tmp_path / f"concurrent_{i}.json"
                atomic_write_json(target, {"thread": i, "data": list(range(100))})
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors, f"Erreurs: {errors}"
        # Chaque fichier doit être un JSON valide et complet
        for i in range(10):
            result = safe_read_json(tmp_path / f"concurrent_{i}.json")
            assert result["thread"] == i
            assert len(result["data"]) == 100


class TestSafeReadJsonCorruption:
    """safe_read_json avec JSON tronqué → quarantaine correcte."""

    def test_truncated_json_returns_default(self, tmp_path):
        from src.utils.persistence import safe_read_json

        bad = tmp_path / "broken.json"
        bad.write_text('{"key": "value", "list": [1, 2,', encoding="utf-8")

        result = safe_read_json(bad, default={"fallback": True}, quarantine=True)
        assert result == {"fallback": True}

    def test_truncated_json_creates_quarantine(self, tmp_path):
        from src.utils.persistence import safe_read_json

        bad = tmp_path / "broken.json"
        bad.write_text('{"incomplete": tru', encoding="utf-8")

        safe_read_json(bad, quarantine=True)

        quarantine_dir = tmp_path / ".quarantine"
        assert quarantine_dir.exists()
        quarantine_files = list(quarantine_dir.glob("broken_*.json"))
        assert len(quarantine_files) >= 1
        content = quarantine_files[0].read_text(encoding="utf-8")
        assert '{"incomplete": tru' in content

    def test_missing_file_returns_default(self, tmp_path):
        from src.utils.persistence import safe_read_json

        result = safe_read_json(tmp_path / "nonexistent.json", default={"empty": True})
        assert result == {"empty": True}

    def test_valid_json_returns_parsed(self, tmp_path):
        from src.utils.persistence import safe_read_json

        good = tmp_path / "good.json"
        good.write_text('{"valid": true}', encoding="utf-8")

        result = safe_read_json(good)
        assert result == {"valid": True}


class TestEmbeddingCacheSingleton:
    """get_embedding_cache() × 10 threads → 1 seule instance."""

    def test_singleton_thread_safety(self, tmp_path):
        import src.memory.embedding_cache as mod

        # Reset singleton
        old_cache = mod._embedding_cache
        mod._embedding_cache = None
        try:
            instances: list = []
            errors: list = []
            cache_path = tmp_path / "test_embed.db"

            def getter():
                try:
                    inst = mod.get_embedding_cache(cache_path)
                    instances.append(id(inst))
                except Exception as exc:
                    errors.append(exc)

            threads = [threading.Thread(target=getter) for _ in range(10)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=10)

            assert not errors, f"Erreurs: {errors}"
            assert len(instances) == 10
            # Toutes les instances doivent être la même (même id)
            assert len(set(instances)) == 1, f"Instances différentes: {set(instances)}"
        finally:
            # Cleanup: fermer et restaurer
            if mod._embedding_cache is not None and mod._embedding_cache is not old_cache:
                try:
                    mod._embedding_cache.close()
                except Exception:
                    pass
            mod._embedding_cache = old_cache

    def test_singleton_no_cache_path_raises(self):
        import src.memory.embedding_cache as mod

        old_cache = mod._embedding_cache
        mod._embedding_cache = None
        try:
            with pytest.raises(RuntimeError, match="cache_path requis"):
                mod.get_embedding_cache(None)
        finally:
            mod._embedding_cache = old_cache


# ===================================================================
# P8.2 — Sanitizer exhaustif (complément des tests existants)
# ===================================================================

class TestPSBlockedVerbsExhaustive:
    """Teste TOUTES les entrées de _PS_BLOCKED_VERBS."""

    @pytest.fixture
    def blocked_verbs(self):
        from src.utils.command_sanitizer import _PS_BLOCKED_VERBS
        return _PS_BLOCKED_VERBS

    def test_all_blocked_verbs_rejected(self, blocked_verbs):
        from src.utils.command_sanitizer import sanitize_command

        for verb in blocked_verbs:
            # Chaque verbe + un nom quelconque doit être bloqué
            cmd = f"{verb.capitalize()}-Something"
            allowed, reason = sanitize_command(cmd)
            assert not allowed, f"{cmd} devrait être bloqué (verbe '{verb}')"

    def test_remove_item_recurse_force(self):
        from src.utils.command_sanitizer import sanitize_command
        allowed, _ = sanitize_command("Remove-Item -Recurse -Force C:\\Users")
        assert not allowed

    def test_invoke_expression_blocked(self):
        from src.utils.command_sanitizer import sanitize_command
        allowed, _ = sanitize_command('Invoke-Expression "malicious"')
        assert not allowed

    def test_safe_verbs_allowed(self):
        from src.utils.command_sanitizer import sanitize_command

        safe_cmds = [
            "Get-Content file.txt",
            "Get-Process",
            "Test-Path C:\\Windows",
            "Select-Object -First 5",
            "Format-List",
            "Where-Object {$_ -gt 0}",
            "Measure-Object -Line",
        ]
        for cmd in safe_cmds:
            allowed, reason = sanitize_command(cmd)
            assert allowed, f"{cmd} devrait être autorisé mais bloqué: {reason}"


# ===================================================================
# P8.3 — Path traversal (complément dans P8 scope)
# ===================================================================

class TestPathTraversalAdditional:
    """Tests supplémentaires P8.3 pour les scénarios du plan."""

    @pytest.fixture
    def roots(self, tmp_path):
        from src.tools.file_guardrails import (
            PathSecurityError, check_path_boundary,
            check_read_blacklist, check_write_blacklist,
            check_delete_allowed,
        )
        lumena = tmp_path / "lumena"
        workspace = lumena / "workspace"
        lumena.mkdir()
        workspace.mkdir()
        (lumena / ".env").touch()
        (lumena / ".env.local").touch()
        (lumena / "data").mkdir()
        (lumena / "data" / "mail").mkdir()
        (lumena / "data" / "browser_profiles").mkdir()
        (lumena / "data" / "journal.json").touch()
        (lumena / "src").mkdir()
        (lumena / "src" / "core.py").touch()
        (workspace / "projet").mkdir()
        (workspace / "projet" / "index.html").touch()
        return lumena, workspace

    def test_etc_passwd_traversal_blocked(self, roots):
        from src.tools.file_guardrails import PathSecurityError, check_path_boundary
        lumena, workspace = roots
        evil = Path("C:/Windows/System32/cmd.exe").resolve()
        with pytest.raises(PathSecurityError):
            check_path_boundary(evil, lumena, workspace)

    def test_env_read_blocked(self, roots):
        from src.tools.file_guardrails import PathSecurityError, check_read_blacklist
        lumena, _ = roots
        with pytest.raises(PathSecurityError):
            check_read_blacklist((lumena / ".env").resolve(), lumena)

    def test_env_write_blocked(self, roots):
        from src.tools.file_guardrails import PathSecurityError, check_write_blacklist
        lumena, _ = roots
        with pytest.raises(PathSecurityError):
            check_write_blacklist((lumena / ".env").resolve(), lumena)

    def test_env_local_write_blocked(self, roots):
        from src.tools.file_guardrails import PathSecurityError, check_write_blacklist
        lumena, _ = roots
        with pytest.raises(PathSecurityError):
            check_write_blacklist((lumena / ".env.local").resolve(), lumena)

    def test_data_journal_read_ok_write_blocked(self, roots):
        from src.tools.file_guardrails import (
            check_read_blacklist, check_write_blacklist, PathSecurityError,
        )
        lumena, _ = roots
        # Read OK
        check_read_blacklist((lumena / "data" / "journal.json").resolve(), lumena)
        # Write blocked
        with pytest.raises(PathSecurityError):
            check_write_blacklist((lumena / "data" / "journal.json").resolve(), lumena)

    def test_src_core_read_write_ok(self, roots):
        from src.tools.file_guardrails import (
            check_read_blacklist, check_write_blacklist,
        )
        lumena, _ = roots
        resolved = (lumena / "src" / "core.py").resolve()
        check_read_blacklist(resolved, lumena)
        check_write_blacklist(resolved, lumena)

    def test_workspace_file_full_access(self, roots):
        from src.tools.file_guardrails import (
            check_path_boundary, check_read_blacklist,
            check_write_blacklist, check_delete_allowed,
        )
        lumena, workspace = roots
        resolved = (workspace / "projet" / "index.html").resolve()
        check_path_boundary(resolved, lumena, workspace)
        check_read_blacklist(resolved, lumena)
        check_write_blacklist(resolved, lumena)
        check_delete_allowed(resolved, lumena, workspace)


# ===================================================================
# P8.4 — Auth (complément)
# ===================================================================

class TestAuthWarningStartup:
    """Warning startup si token vide ET setup complet."""

    @pytest.mark.asyncio
    async def test_empty_token_setup_done_401(self):
        from web.routes.deps import verify_admin_token
        from fastapi import HTTPException

        with patch.dict(os.environ, {"LUMENA_ADMIN_TOKEN": "", "LUMENA_SETUP_COMPLETE": "1"}):
            with pytest.raises(HTTPException) as exc_info:
                await verify_admin_token(authorization=None)
            assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_empty_token_setup_not_done_allowed(self):
        from web.routes.deps import verify_admin_token

        env = {"LUMENA_ADMIN_TOKEN": ""}
        with patch.dict(os.environ, env, clear=False):
            os.environ.pop("LUMENA_SETUP_COMPLETE", None)
            result = await verify_admin_token(authorization=None)
            assert result is None

    @pytest.mark.asyncio
    async def test_valid_token_required(self):
        from web.routes.deps import verify_admin_token
        from fastapi import HTTPException

        with patch.dict(os.environ, {"LUMENA_ADMIN_TOKEN": "mytoken", "LUMENA_SETUP_COMPLETE": "1"}):
            # Correct token passes
            result = await verify_admin_token(authorization="Bearer mytoken")
            assert result is None
            # Wrong token fails
            with pytest.raises(HTTPException) as exc:
                await verify_admin_token(authorization="Bearer wrong")
            assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_invalid_token_403(self):
        from web.routes.deps import verify_admin_token
        from fastapi import HTTPException

        with patch.dict(os.environ, {"LUMENA_ADMIN_TOKEN": "secret", "LUMENA_SETUP_COMPLETE": "1"}):
            with pytest.raises(HTTPException) as exc_info:
                await verify_admin_token(authorization="Bearer badtoken")
            assert exc_info.value.status_code == 403
