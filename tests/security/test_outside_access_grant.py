"""Tests — OutsideAccessGrant et nouveau modèle de permission hors workspace.

Vérifie:
- OutsideAccessGrant.permits_read() correct
- resolve_user_path() bloqué sans grant
- resolve_user_path() accordé avec grant borné (lecture seule)
- resolve_write_target() bloqué hors workspace même avec grant de lecture
- _detect_outside_access_grant() détecte les bons patterns
- Mode autonome : grant None = verrou total
"""

import pytest
from pathlib import Path

from src.tools.file_guardrails import (
    OutsideAccessGrant,
    PathSecurityError,
    WorkspaceFileGuardrails,
)
from src.core_services.agent_service import _detect_outside_access_grant


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def roots(tmp_path):
    lumena = tmp_path / "lumena"
    workspace = lumena / "workspace"
    external = tmp_path / "external"
    home_like = tmp_path / "home" / "user"
    lumena.mkdir(parents=True)
    workspace.mkdir(parents=True)
    external.mkdir(parents=True)
    home_like.mkdir(parents=True)
    (workspace / "projet").mkdir()
    (workspace / "projet" / "index.html").write_text("<html></html>")
    (external / "notes.txt").write_text("notes")
    (external / "subdir").mkdir()
    (external / "subdir" / "file.py").write_text("# code")
    return lumena, workspace, external, home_like


@pytest.fixture
def guardrails(roots):
    lumena, workspace, *_ = roots
    return WorkspaceFileGuardrails(lumena)


# ---------------------------------------------------------------------------
# OutsideAccessGrant unit tests
# ---------------------------------------------------------------------------

class TestOutsideAccessGrant:
    def test_none_permits_nothing(self, roots):
        _, _, external, _ = roots
        grant = OutsideAccessGrant.none()
        assert not grant.permits_read(external / "notes.txt")

    def test_for_paths_permits_exact(self, roots):
        _, _, external, _ = roots
        grant = OutsideAccessGrant.for_paths(external)
        assert grant.permits_read(external / "notes.txt")

    def test_for_paths_permits_subtree(self, roots):
        _, _, external, _ = roots
        grant = OutsideAccessGrant.for_paths(external)
        assert grant.permits_read(external / "subdir" / "file.py")

    def test_for_paths_denies_sibling(self, roots):
        _, _, external, home_like = roots
        grant = OutsideAccessGrant.for_paths(external)
        assert not grant.permits_read(home_like / "notes.txt")

    def test_allow_read_false_blocks_everything(self, roots):
        _, _, external, _ = roots
        grant = OutsideAccessGrant(allowed_roots=[external], allow_read=False)
        assert not grant.permits_read(external / "notes.txt")

    def test_allow_write_always_false(self):
        grant = OutsideAccessGrant.for_paths(Path("/tmp"))
        assert not grant.allow_write
        assert not grant.allow_delete


# ---------------------------------------------------------------------------
# resolve_user_path — boundary enforcement
# ---------------------------------------------------------------------------

class TestResolveUserPathGrant:
    def test_inside_workspace_no_grant_needed(self, guardrails, roots):
        lumena, workspace, *_ = roots
        result = guardrails.resolve_user_path(
            str(workspace / "projet" / "index.html"),
            outside_grant=None,
        )
        assert result.exists()

    def test_outside_without_grant_raises(self, guardrails, roots):
        _, _, external, _ = roots
        with pytest.raises(PathSecurityError, match="hors des limites"):
            guardrails.resolve_user_path(
                str(external / "notes.txt"),
                outside_grant=None,
            )

    def test_outside_with_matching_grant_allowed(self, guardrails, roots):
        _, _, external, _ = roots
        grant = OutsideAccessGrant.for_paths(external)
        result = guardrails.resolve_user_path(
            str(external / "notes.txt"),
            outside_grant=grant,
        )
        assert "notes.txt" in result.name

    def test_outside_with_non_matching_grant_raises(self, guardrails, roots):
        _, _, external, home_like = roots
        grant = OutsideAccessGrant.for_paths(home_like)  # grant pour home_like, pas external
        with pytest.raises(PathSecurityError, match="hors des limites"):
            guardrails.resolve_user_path(
                str(external / "notes.txt"),
                outside_grant=grant,
            )

    def test_grant_read_false_raises(self, guardrails, roots):
        _, _, external, _ = roots
        grant = OutsideAccessGrant(allowed_roots=[external], allow_read=False)
        with pytest.raises(PathSecurityError):
            guardrails.resolve_user_path(
                str(external / "notes.txt"),
                outside_grant=grant,
            )


# ---------------------------------------------------------------------------
# resolve_write_target — écriture toujours bloquée hors workspace
# ---------------------------------------------------------------------------

class TestResolveWriteTargetBoundary:
    def test_write_inside_workspace_ok(self, guardrails, roots):
        lumena, workspace, *_ = roots
        target, redirected, _ = guardrails.resolve_write_target(
            str(workspace / "projet" / "new.html")
        )
        # Pas d'erreur levée

    def test_write_outside_workspace_raises(self, guardrails, roots):
        _, _, external, _ = roots
        with pytest.raises(PathSecurityError, match="hors des limites"):
            guardrails.resolve_write_target(str(external / "evil.txt"))

    def test_write_outside_ignores_any_read_grant(self, guardrails, roots):
        """Un grant de lecture ne doit jamais autoriser une écriture hors workspace."""
        _, _, external, _ = roots
        # Le grant est de lecture, mais resolve_write_target n'en tient pas compte
        with pytest.raises(PathSecurityError, match="hors des limites"):
            guardrails.resolve_write_target(str(external / "evil.txt"))


# ---------------------------------------------------------------------------
# _detect_outside_access_grant — détection de patterns
# ---------------------------------------------------------------------------

class TestDetectOutsideAccessGrant:
    def test_no_explicit_path_returns_none_grant(self):
        grant = _detect_outside_access_grant("corrige le bug dans mon code")
        assert not grant.allow_read
        assert not grant.permits_read(Path("C:/Users/user/Documents/file.txt"))

    def test_no_explicit_path_chat_returns_none_grant(self):
        grant = _detect_outside_access_grant("comment ça va ?")
        assert not grant.allow_read

    def test_explicit_windows_path_grants_read(self, roots):
        _, _, external, _ = roots
        query = f"va lire {external / 'notes.txt'}"
        grant = _detect_outside_access_grant(query)
        assert grant.allow_read
        assert grant.permits_read(external / "notes.txt")

    def test_named_downloads_dir(self, tmp_path, monkeypatch):
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))
        grant = _detect_outside_access_grant("cherche dans Downloads mon fichier")
        assert grant.allow_read
        assert any("Downloads" in str(r) for r in grant.allowed_roots)

    def test_named_bureau(self, tmp_path, monkeypatch):
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))
        grant = _detect_outside_access_grant("ouvre le fichier sur mon Bureau")
        assert grant.allow_read

    def test_pc_scope_keyword_grants_home(self, tmp_path, monkeypatch):
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))
        grant = _detect_outside_access_grant("trouve-moi ce fichier sur mon PC")
        assert grant.allow_read
        assert fake_home in grant.allowed_roots

    def test_hors_workspace_keyword(self, tmp_path, monkeypatch):
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))
        grant = _detect_outside_access_grant("ouvre ce dossier hors workspace")
        assert grant.allow_read

    def test_standard_coding_request_no_grant(self):
        grant = _detect_outside_access_grant("ajoute une fonction dans agent_service.py")
        assert not grant.allow_read

    def test_grant_never_allows_write(self, roots):
        _, _, external, _ = roots
        query = f"va lire {external / 'notes.txt'}"
        grant = _detect_outside_access_grant(query)
        assert not grant.allow_write
        assert not grant.allow_delete


# ---------------------------------------------------------------------------
# Régression : pas de bypass global via requête utilisateur ordinaire
# ---------------------------------------------------------------------------

class TestNoGlobalBypass:
    """S'assure qu'une requête utilisateur standard ne bypasse plus rien."""

    def test_user_request_without_explicit_path_stays_locked(self, guardrails, roots):
        _, _, external, _ = roots
        grant = _detect_outside_access_grant("fais-moi un site web")
        # Même avec ce grant vide, on ne peut pas accéder à external
        with pytest.raises(PathSecurityError):
            guardrails.resolve_user_path(
                str(external / "notes.txt"),
                outside_grant=grant,
            )

    def test_autonomous_mode_grant_none_blocks_all(self, guardrails, roots):
        _, _, external, _ = roots
        with pytest.raises(PathSecurityError):
            guardrails.resolve_user_path(
                str(external / "notes.txt"),
                outside_grant=None,
            )
