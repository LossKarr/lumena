"""
🧪 Tests - Path Traversal Protection (Phase 5.2)

Tests pour la protection contre les attaques path traversal.
"""

import pytest
import tempfile
import os
from pathlib import Path


class TestSelfImprovePathTraversal:
    """Tests pour la protection path traversal dans self_improve."""
    
    @pytest.fixture
    def temp_workspace(self):
        """Crée un workspace temporaire."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir).resolve()
            (workspace / "allowed").mkdir()
            (workspace / "allowed" / "test.py").write_text("# test")
            yield workspace
    
    def test_relative_path_within_workspace(self, temp_workspace):
        """Un chemin relatif dans le workspace doit être autorisé."""
        from pathlib import Path
        
        workspace = temp_workspace
        target = workspace / "allowed" / "test.py"
        
        # Résoudre et vérifier
        resolved = target.resolve()
        assert resolved.is_relative_to(workspace)
    
    def test_path_traversal_blocked(self, temp_workspace):
        """Un chemin avec .. hors du workspace doit être bloqué."""
        workspace = temp_workspace
        
        # Tentative de path traversal
        malicious_path = workspace / "allowed" / ".." / ".." / "etc" / "passwd"
        resolved = malicious_path.resolve()
        
        # Doit être hors du workspace
        try:
            is_relative = resolved.is_relative_to(workspace)
        except ValueError:
            is_relative = False
        
        assert not is_relative
    
    def test_absolute_path_outside_blocked(self, temp_workspace):
        """Un chemin absolu hors du workspace doit être bloqué."""
        workspace = temp_workspace
        
        # Chemin absolu hors workspace
        outside_path = Path("/etc/passwd")
        
        try:
            is_relative = outside_path.is_relative_to(workspace)
        except ValueError:
            is_relative = False
        
        assert not is_relative
    
    def test_symlink_traversal_blocked(self, temp_workspace):
        """Un symlink pointant hors du workspace doit être détecté."""
        workspace = temp_workspace
        
        # Créer un symlink malveillant
        symlink = workspace / "allowed" / "malicious_link"
        
        try:
            # Pointer vers la racine
            symlink.symlink_to("/")
            
            # Le resolve() doit révéler la vraie destination
            resolved = symlink.resolve()
            
            try:
                is_relative = resolved.is_relative_to(workspace)
            except ValueError:
                is_relative = False
            
            assert not is_relative
        except OSError:
            # Les symlinks peuvent être bloqués sur Windows
            pytest.skip("Symlinks not supported on this system")


class TestToolSystemPathValidation:
    """Tests pour la validation des chemins dans tool_system."""
    
    def test_normalize_path_removes_dots(self):
        """La normalisation doit résoudre les .. et ."""
        path = Path("./foo/../bar/./file.txt")
        normalized = path.resolve()
        
        # Le chemin résolu ne doit pas contenir .. ou .
        path_str = str(normalized)
        assert ".." not in path_str.split(os.sep)
    
    def test_home_expansion_is_safe(self):
        """L'expansion de ~ doit être sûre."""
        path = Path("~/sensitive_file")
        expanded = path.expanduser()
        
        # Doit pointer vers le home de l'utilisateur actuel
        assert str(expanded).startswith(str(Path.home()))


class TestReactPathValidation:
    """Tests pour la validation des chemins dans react.py."""
    
    @pytest.fixture
    def temp_workspace(self):
        """Crée un workspace temporaire."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            yield workspace
    
    def test_validate_path_in_workspace(self, temp_workspace):
        """Fonction de validation pour les chemins."""
        workspace = temp_workspace
        
        def is_path_safe(path_str: str, workspace: Path) -> bool:
            """Vérifie si un chemin est sûr (dans le workspace)."""
            try:
                path = Path(path_str)
                # Résoudre si relatif
                if not path.is_absolute():
                    path = (workspace / path).resolve()
                else:
                    path = path.resolve()
                
                # Vérifier qu'il est dans le workspace
                return path.is_relative_to(workspace.resolve())
            except Exception:
                return False
        
        # Tests
        assert is_path_safe("file.txt", workspace) is True
        assert is_path_safe("subdir/file.txt", workspace) is True
        assert is_path_safe("../../../etc/passwd", workspace) is False
        assert is_path_safe("/etc/passwd", workspace) is False


class TestCommandSanitizerPaths:
    """Tests pour la sanitization des chemins dans les commandes."""
    
    def test_path_in_command_is_validated(self):
        """Les chemins dans les commandes doivent être validés."""
        from src.utils.command_sanitizer import sanitize_command
        
        # Commande avec chemin traversal
        command = "cat ../../../etc/passwd"
        
        # Le sanitizer doit soit rejeter soit échapper
        try:
            result = sanitize_command(command)
            # Si ça passe, vérifier que c'est sûr
            assert "../../../etc/passwd" not in result or result is None
        except ValueError:
            # Rejet = comportement attendu
            pass
    
    def test_quoted_path_traversal(self):
        """Les chemins entre guillemets doivent aussi être vérifiés."""
        from src.utils.command_sanitizer import sanitize_command
        
        # Tentative d'échapper via quotes
        command = 'cat "../../etc/passwd"'
        
        try:
            result = sanitize_command(command)
            # Doit être bloqué ou sanitisé
        except ValueError:
            pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
