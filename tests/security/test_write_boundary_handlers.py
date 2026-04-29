"""Tests — Write boundary enforcement in mutating handlers.

Vérifie qu'un OutsideAccessGrant de lecture ne peut jamais être détourné
en vecteur d'écriture, de modification ou de création hors workspace.

Couvre :
- _assert_write_boundary() directement
- insert_at_anchor_handler
- create_zip_handler
- edit_file_handler
- apply_patch_handler
- undo_edit_handler
- create_directory_handler
- write_file_handler (IDE path)
"""

import asyncio
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from src.tools.file_guardrails import (
    OutsideAccessGrant,
    PathSecurityError,
    WorkspaceFileGuardrails,
)
from src.reasoning.handlers.files import _assert_write_boundary
from src.reasoning.handlers.context import HandlerContext


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def roots(tmp_path):
    lumena = tmp_path / "lumena"
    workspace = lumena / "workspace"
    external = tmp_path / "external"
    lumena.mkdir(parents=True)
    workspace.mkdir(parents=True)
    external.mkdir(parents=True)
    (workspace / "projet").mkdir()
    (workspace / "projet" / "index.html").write_text("<html></html>")
    (external / "notes.txt").write_text("original content")
    (external / "subdir").mkdir()
    return lumena, workspace, external


@pytest.fixture
def guardrails(roots):
    lumena, *_ = roots
    return WorkspaceFileGuardrails(lumena)


@pytest.fixture
def ctx_inside(roots, guardrails):
    """HandlerContext pointant sur workspace normal, sans grant."""
    lumena, workspace, _ = roots
    ctx = HandlerContext.for_testing(lumena_root=lumena, runtime_root=workspace)
    ctx.file_guardrails = guardrails
    ctx.outside_access_grant = None
    return ctx


@pytest.fixture
def ctx_with_read_grant(roots, guardrails):
    """HandlerContext avec un grant de LECTURE sur external uniquement."""
    lumena, workspace, external = roots
    ctx = HandlerContext.for_testing(lumena_root=lumena, runtime_root=workspace)
    ctx.file_guardrails = guardrails
    ctx.outside_access_grant = OutsideAccessGrant.for_paths(external)
    return ctx


# ---------------------------------------------------------------------------
# _assert_write_boundary — tests unitaires directs
# ---------------------------------------------------------------------------

class TestAssertWriteBoundary:
    def test_allows_inside_workspace(self, roots, guardrails):
        lumena, workspace, _ = roots
        ctx = HandlerContext.for_testing(lumena_root=lumena, runtime_root=workspace)
        ctx.file_guardrails = guardrails
        # Ne doit pas lever
        _assert_write_boundary(workspace / "projet" / "index.html", ctx)

    def test_allows_inside_lumena(self, roots, guardrails):
        lumena, workspace, _ = roots
        ctx = HandlerContext.for_testing(lumena_root=lumena, runtime_root=workspace)
        ctx.file_guardrails = guardrails
        (lumena / "src").mkdir(exist_ok=True)
        (lumena / "src" / "core.py").touch()
        _assert_write_boundary(lumena / "src" / "core.py", ctx)

    def test_blocks_external_no_grant(self, roots, guardrails):
        lumena, workspace, external = roots
        ctx = HandlerContext.for_testing(lumena_root=lumena, runtime_root=workspace)
        ctx.file_guardrails = guardrails
        ctx.outside_access_grant = None
        with pytest.raises(PathSecurityError, match="hors des limites"):
            _assert_write_boundary(external / "notes.txt", ctx)

    def test_blocks_external_even_with_read_grant(self, roots, ctx_with_read_grant):
        """Read grant covers external for reads — but writes must still be blocked."""
        _, _, external = roots
        # Le grant de lecture couvre external, mais _assert_write_boundary doit bloquer
        with pytest.raises(PathSecurityError, match="hors des limites"):
            _assert_write_boundary(external / "notes.txt", ctx_with_read_grant)

    def test_no_guardrails_no_crash(self, roots):
        lumena, workspace, external = roots
        ctx = HandlerContext.for_testing(lumena_root=lumena, runtime_root=workspace)
        ctx.file_guardrails = None  # Pas de guardrails (mode test léger)
        # Ne doit pas lever
        _assert_write_boundary(external / "notes.txt", ctx)


# ---------------------------------------------------------------------------
# insert_at_anchor_handler — ne doit pas écrire hors workspace
# ---------------------------------------------------------------------------

class TestInsertAtAnchorBoundary:
    def test_blocks_write_to_external_via_read_grant(self, roots, ctx_with_read_grant):
        """insert_at_anchor doit refuser même si le chemin est résolu via read grant."""
        from src.reasoning.handlers.files import insert_at_anchor_handler
        _, _, external = roots
        ext_file = external / "notes.txt"
        ext_file.write_text("line1\n<!-- ANCHOR -->\nline3\n")

        # Injecter resolve_path pour simuler la résolution du fichier externe
        ctx_with_read_grant.resolve_path = lambda p, **kw: ext_file

        result = asyncio.get_event_loop().run_until_complete(
            insert_at_anchor_handler(
                ctx_with_read_grant,
                path=str(ext_file),
                anchor="<!-- ANCHOR -->",
                content="injected",
                position="after",
            )
        )
        assert not result.success or "refusée" in result.output or "refus" in result.output.lower()
        # Vérifier que le fichier n'a PAS été modifié
        assert ext_file.read_text() == "line1\n<!-- ANCHOR -->\nline3\n"

    def test_allows_write_inside_workspace(self, roots, ctx_inside):
        from src.reasoning.handlers.files import insert_at_anchor_handler
        lumena, workspace, _ = roots
        target = workspace / "projet" / "index.html"
        target.write_text("<html>\n<!-- ANCHOR -->\n</html>")

        ctx_inside.resolve_path = lambda p, **kw: target

        result = asyncio.get_event_loop().run_until_complete(
            insert_at_anchor_handler(
                ctx_inside,
                path=str(target),
                anchor="<!-- ANCHOR -->",
                content="<p>inserted</p>",
                position="after",
            )
        )
        assert "insert_at_anchor" in str(result) or result.success


# ---------------------------------------------------------------------------
# create_zip_handler — out_zip hors workspace refusé
# ---------------------------------------------------------------------------

class TestCreateZipBoundary:
    def test_blocks_zip_output_outside_workspace(self, roots, ctx_with_read_grant):
        from src.reasoning.handlers.files import create_zip_handler
        _, workspace, external = roots
        source = workspace / "projet" / "index.html"
        out_zip = external / "output.zip"

        # Simuler resolve_path : source → workspace, out_zip → external
        def _mock_resolve(p, **kw):
            if "index.html" in p:
                return source
            return out_zip

        ctx_with_read_grant.resolve_path = _mock_resolve

        result = asyncio.get_event_loop().run_until_complete(
            create_zip_handler(
                ctx_with_read_grant,
                source_paths=str(source),
                zip_path=str(out_zip),
            )
        )
        assert not result.success or "refusée" in result.output or "refus" in result.output.lower()
        assert not out_zip.exists()

    def test_allows_zip_inside_workspace(self, roots, ctx_inside):
        from src.reasoning.handlers.files import create_zip_handler
        _, workspace, _ = roots
        source = workspace / "projet" / "index.html"
        out_zip = workspace / "output.zip"

        def _mock_resolve(p, **kw):
            if "index.html" in p:
                return source
            return out_zip

        ctx_inside.resolve_path = _mock_resolve

        result = asyncio.get_event_loop().run_until_complete(
            create_zip_handler(
                ctx_inside,
                source_paths=str(source),
                zip_path=str(out_zip),
            )
        )
        # Succès attendu (ou échec sur autre raison, pas boundary)
        if not result.success:
            assert "hors des limites" not in result.output


# ---------------------------------------------------------------------------
# edit_file_handler — ne doit pas éditer hors workspace
# ---------------------------------------------------------------------------

class TestEditFileBoundary:
    def test_blocks_edit_outside_workspace(self, roots, ctx_with_read_grant):
        from src.reasoning.handlers.files import edit_file_handler
        _, _, external = roots
        ext_file = external / "notes.txt"
        ext_file.write_text("original")

        ctx_with_read_grant.resolve_path = lambda p, **kw: ext_file

        result = asyncio.get_event_loop().run_until_complete(
            edit_file_handler(
                ctx_with_read_grant,
                file_path=str(ext_file),
                old_content="original",
                new_content="hacked",
            )
        )
        assert not result.success or "refusée" in result.output or "refus" in result.output.lower()
        assert ext_file.read_text() == "original"


# ---------------------------------------------------------------------------
# apply_patch_handler — ne doit pas patcher hors workspace
# ---------------------------------------------------------------------------

class TestApplyPatchBoundary:
    def test_blocks_patch_outside_workspace(self, roots, ctx_with_read_grant):
        from src.reasoning.handlers.files import apply_patch_handler
        _, _, external = roots
        ext_file = external / "notes.txt"
        ext_file.write_text("original")

        ctx_with_read_grant.resolve_path = lambda p, **kw: ext_file

        result = asyncio.get_event_loop().run_until_complete(
            apply_patch_handler(
                ctx_with_read_grant,
                file_path=str(ext_file),
                old_content="original",
                new_content="patched",
                description="test",
            )
        )
        assert not result.success or "refusée" in result.output or "refus" in result.output.lower()
        assert ext_file.read_text() == "original"


# ---------------------------------------------------------------------------
# undo_edit_handler — ne doit pas restaurer hors workspace
# ---------------------------------------------------------------------------

class TestUndoEditBoundary:
    def test_blocks_undo_restore_outside_workspace(self, roots, ctx_with_read_grant, tmp_path):
        from src.reasoning.handlers.files import undo_edit_handler
        lumena, workspace, external = roots

        # Créer une structure backup factice dans lumena
        backup_root = lumena / ".backups"
        session = backup_root / "session_001"
        session.mkdir(parents=True)
        (session / "dummy.py").write_text("backup content")

        ext_target = external / "notes.txt"
        ctx_with_read_grant.resolve_path = lambda p, **kw: ext_target

        result = asyncio.get_event_loop().run_until_complete(
            undo_edit_handler(
                ctx_with_read_grant,
                file_path=str(ext_target),
            )
        )
        assert not result.success or "refusée" in result.output or "refus" in result.output.lower()


# ---------------------------------------------------------------------------
# create_directory_handler — ne doit pas créer de dossier hors workspace
# ---------------------------------------------------------------------------

class TestCreateDirectoryBoundary:
    def test_blocks_mkdir_outside_workspace(self, roots, ctx_with_read_grant):
        from src.reasoning.handlers.files import create_directory_handler
        _, _, external = roots
        new_dir = external / "evil_dir"

        result = asyncio.get_event_loop().run_until_complete(
            create_directory_handler(
                ctx_with_read_grant,
                path=str(new_dir),
            )
        )
        assert not result.success or "refusée" in result.output or "refus" in result.output.lower()
        assert not new_dir.exists()

    def test_allows_mkdir_inside_workspace(self, roots, ctx_inside):
        from src.reasoning.handlers.files import create_directory_handler
        _, workspace, _ = roots
        new_dir = workspace / "nouveau_dossier"

        result = asyncio.get_event_loop().run_until_complete(
            create_directory_handler(
                ctx_inside,
                path=str(new_dir),
            )
        )
        assert new_dir.exists()


# ---------------------------------------------------------------------------
# write_file_handler — chemin absolu IDE hors workspace bloqué
# ---------------------------------------------------------------------------

class TestWriteFileBoundary:
    def test_blocks_write_outside_workspace_absolute(self, roots, ctx_with_read_grant):
        from src.reasoning.handlers.files import write_file_handler
        _, _, external = roots
        ext_file = external / "injected.txt"

        # Simuler contexte IDE (is_ide_runtime = True) avec chemin absolu
        ctx_with_read_grant.ide_context = {"workspace_path": str(external)}

        result = asyncio.get_event_loop().run_until_complete(
            write_file_handler(
                ctx_with_read_grant,
                path=str(ext_file),
                content="malicious content",
            )
        )
        assert not result.success or "refusée" in result.output or "refus" in result.output.lower()
        assert not ext_file.exists()

    def test_allows_write_inside_workspace(self, roots, ctx_inside):
        from src.reasoning.handlers.files import write_file_handler
        _, workspace, _ = roots
        target = workspace / "nouveau.txt"

        result = asyncio.get_event_loop().run_until_complete(
            write_file_handler(
                ctx_inside,
                path=str(target),
                content="contenu légitime",
            )
        )
        # Pas de PathSecurityError levée (peut échouer pour d'autres raisons)
        if not result.success:
            assert "hors des limites" not in result.output
