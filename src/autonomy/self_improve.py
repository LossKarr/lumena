"""
🧠 LUMENA - Système d'Auto-Amélioration

Inspiré de Factory.ai Signals et du pattern Ralph Wiggum (2025-2026).
Permet à Lumena de lire, analyser et améliorer son propre code.
"""

from typing import Dict, Any, List, Optional, Tuple
from pathlib import Path
from datetime import datetime
import subprocess
import difflib
import shutil
import ast
import re
import json
from loguru import logger
from ..utils.persistence import atomic_write_json


class CodeAnalyzer:
    """Analyse le code source de Lumena."""
    
    @staticmethod
    def get_file_info(file_path: Path) -> Dict[str, Any]:
        """Retourne des informations sur un fichier."""
        if not file_path.exists():
            return {"error": f"Fichier non trouvé: {file_path}"}
        
        content = file_path.read_text(encoding='utf-8')
        lines = content.split('\n')
        
        return {
            "path": str(file_path),
            "lines": len(lines),
            "size_bytes": len(content),
            "extension": file_path.suffix,
            "last_modified": datetime.fromtimestamp(file_path.stat().st_mtime).isoformat()
        }
    
    @staticmethod
    def analyze_python_file(file_path: Path) -> Dict[str, Any]:
        """Analyse un fichier Python pour extraire sa structure."""
        if not file_path.exists() or file_path.suffix != '.py':
            return {"error": "Fichier Python invalide"}
        
        content = file_path.read_text(encoding='utf-8')
        
        try:
            tree = ast.parse(content)
        except SyntaxError as e:
            return {"error": f"Erreur de syntaxe: {e}"}
        
        classes = []
        functions = []
        imports = []
        
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                methods = [m.name for m in node.body if isinstance(m, ast.FunctionDef)]
                classes.append({
                    "name": node.name,
                    "line": node.lineno,
                    "methods": methods,
                    "method_count": len(methods)
                })
            elif isinstance(node, ast.FunctionDef) and node.col_offset == 0:
                functions.append({
                    "name": node.name,
                    "line": node.lineno,
                    "args": [arg.arg for arg in node.args.args]
                })
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        imports.append(alias.name)
                else:
                    module = node.module or ""
                    for alias in node.names:
                        imports.append(f"{module}.{alias.name}")
        
        return {
            "path": str(file_path),
            "classes": classes,
            "functions": functions,
            "imports": imports,
            "total_classes": len(classes),
            "total_functions": len(functions)
        }


class SelfImprover:
    """
    Système d'auto-amélioration de Lumena.
    
    Permet à Lumena de:
    - Lire et comprendre son propre code
    - Proposer des améliorations
    - Appliquer des patches de manière sécurisée
    - Valider les changements via tests
    """
    
    def __init__(self, lumena_root: Optional[Path] = None):
        self.root = lumena_root or Path(__file__).parent.parent.parent
        self.src_dir = self.root / "src"
        from src.utils.paths import BACKUPS_DIR, AUTONOMY_DIR
        self.backup_dir = BACKUPS_DIR / datetime.now().strftime("%Y%m%d_%H%M%S")
        self.analyzer = CodeAnalyzer()
        self._autonomy_dir = AUTONOMY_DIR
        self._autonomy_dir.mkdir(parents=True, exist_ok=True)
        self._daily_skill_state_file = self._autonomy_dir / "daily_skill_state.json"
        
        logger.info(f"🧠 SelfImprover initialisé - root: {self.root}")
    
    def get_source_tree(self) -> Dict[str, Any]:
        """Retourne l'arborescence du code source."""
        tree = {"directories": {}, "files": []}
        
        for path in self.src_dir.rglob("*.py"):
            if "__pycache__" in str(path):
                continue
            
            relative = path.relative_to(self.src_dir)
            parts = relative.parts
            
            if len(parts) == 1:
                tree["files"].append(str(relative))
            else:
                dir_name = parts[0]
                if dir_name not in tree["directories"]:
                    tree["directories"][dir_name] = []
                tree["directories"][dir_name].append(str(relative))
        
        return tree
    
    def read_own_code(self, file_path: str, start_line: int = 1, end_line: int = 0) -> str:
        """
        Lit un fichier ou liste un dossier du code source de Lumena.
        
        Args:
            file_path: Chemin relatif (ex: "core.py", "tools/", "skills/")
            start_line: Ligne de début (1-indexed, pour fichiers uniquement)
            end_line: Ligne de fin (0 = jusqu'à la fin)
        
        Returns:
            Contenu du fichier avec numéros de ligne OU liste du dossier
        """
        # Nettoyer le chemin
        file_path = file_path.strip().replace("\\", "/").rstrip("/")
        
        # Essayer plusieurs chemins possibles
        paths_to_try = [
            self.src_dir / file_path,                    # tools/tool_system.py
            self.root / file_path,                       # src/tools/tool_system.py
            self.root / "src" / file_path,               # si root mal défini
            self.root / "skills" / file_path,            # skills/
        ]
        
        # Si le chemin commence par src/, l'ajouter en premier
        if file_path.startswith("src/"):
            paths_to_try.insert(0, self.root / file_path)
            clean_path = file_path[4:]
            paths_to_try.append(self.src_dir / clean_path)
        
        # Si le chemin commence par skills/, l'ajouter en premier  
        if file_path.startswith("skills/"):
            paths_to_try.insert(0, self.root / file_path)
        
        full_path = None
        for path in paths_to_try:
            if path.exists():
                full_path = path
                break
        
        if full_path is None:
            tried = ", ".join([str(p) for p in paths_to_try[:3]])
            return f"❌ Fichier/dossier non trouvé: {file_path}\nChemins essayés: {tried}"
        
        if not str(full_path.resolve()).startswith(str(self.root.resolve())):
            return "❌ Accès refusé: chemin hors du projet Lumena"
        
        # SI C'EST UN DOSSIER -> lister son contenu
        if full_path.is_dir():
            return self._list_directory(full_path, file_path)
        
        # SI C'EST UN FICHIER -> lire le contenu
        try:
            content = full_path.read_text(encoding='utf-8')
            lines = content.split('\n')
            
            if end_line == 0:
                end_line = len(lines)
            
            selected_lines = lines[start_line-1:end_line]
            
            numbered = []
            for i, line in enumerate(selected_lines, start=start_line):
                numbered.append(f"{i:4d}: {line}")
            
            header = f"📄 Contenu de {file_path}:\n```\n"
            footer = "\n```"
            return header + "\n".join(numbered) + footer
            
        except Exception as e:
            return f"❌ Erreur lecture: {e}"
    
    def _list_directory(self, dir_path: Path, relative_name: str) -> str:
        """Liste le contenu d'un dossier avec structure arborescente."""
        try:
            items = []
            files = []
            dirs = []
            
            for item in sorted(dir_path.iterdir()):
                if "__pycache__" in str(item) or item.name.startswith("."):
                    continue
                
                if item.is_dir():
                    # Compter les fichiers dans le sous-dossier
                    file_count = len(list(item.rglob("*")))
                    dirs.append(f"📁 {item.name}/ ({file_count} fichiers)")
                else:
                    # Taille du fichier
                    size = item.stat().st_size
                    if size > 1024:
                        size_str = f"{size // 1024}KB"
                    else:
                        size_str = f"{size}B"
                    
                    ext = item.suffix
                    if ext == ".py":
                        icon = "🐍"
                    elif ext == ".md":
                        icon = "📝"
                    elif ext == ".json":
                        icon = "📋"
                    else:
                        icon = "📄"
                    
                    files.append(f"{icon} {item.name} ({size_str})")
            
            result = f"📂 Contenu de {relative_name}/:\n\n"
            
            if dirs:
                result += "**Dossiers:**\n"
                for d in dirs:
                    result += f"  {d}\n"
                result += "\n"
            
            if files:
                result += "**Fichiers:**\n"
                for f in files:
                    result += f"  {f}\n"
            
            if not dirs and not files:
                result += "(dossier vide)"
            
            result += f"\n💡 Pour lire un fichier: read_own_code('{relative_name}/FILENAME')"
            return result
            
        except Exception as e:
            return f"❌ Erreur listing dossier: {e}"
    
    def search_in_code(self, query: str, file_extension: str = ".py") -> str:
        """
        Recherche un terme dans tout le code source de Lumena.
        
        Args:
            query: Terme à rechercher (texte ou regex simple)
            file_extension: Extension des fichiers à chercher (.py, .md, etc.)
        
        Returns:
            Liste des fichiers et lignes contenant le terme
        """
        results = []
        search_dirs = [self.src_dir, self.root / "skills"]
        
        for search_dir in search_dirs:
            if not search_dir.exists():
                continue
                
            for file_path in search_dir.rglob(f"*{file_extension}"):
                if "__pycache__" in str(file_path):
                    continue
                
                try:
                    content = file_path.read_text(encoding='utf-8')
                    lines = content.split('\n')
                    
                    for i, line in enumerate(lines, 1):
                        if query.lower() in line.lower():
                            relative = file_path.relative_to(self.root)
                            results.append({
                                "file": str(relative),
                                "line": i,
                                "content": line.strip()[:100]  # Limiter à 100 caractères
                            })
                except (IOError, OSError, UnicodeDecodeError):
                    continue
        
        if not results:
            return f"🔍 Aucun résultat pour '{query}' dans les fichiers {file_extension}"
        
        # Limiter à 20 résultats
        if len(results) > 20:
            results = results[:20]
            suffix = f"\n\n... et plus de résultats (limité à 20)"
        else:
            suffix = ""
        
        output = f"🔍 Résultats pour '{query}':\n\n"
        for r in results:
            output += f"📄 {r['file']}:{r['line']}\n   {r['content']}\n\n"
        
        return output + suffix
    
    def get_my_capabilities(self) -> str:
        """
        Retourne un résumé des capacités de Lumena basé sur l'analyse du code.
        Utile pour l'auto-connaissance.
        """
        try:
            capabilities = {
                "modules": [],
                "tools": [],
                "skills": [],
                "features": []
            }
            
            # Compter les modules dans src/
            for module in self.src_dir.iterdir():
                if module.is_dir() and not module.name.startswith("_"):
                    py_files = list(module.glob("*.py"))
                    if py_files:
                        capabilities["modules"].append(f"{module.name} ({len(py_files)} fichiers)")
            
            # Lister les skills
            skills_dir = self.root / "skills"
            if skills_dir.exists():
                for skill in skills_dir.iterdir():
                    if skill.suffix in [".md", ".py"]:
                        capabilities["skills"].append(skill.stem)
            
            # Chercher les tools enregistrés
            tool_system = self.src_dir / "tools" / "tool_system.py"
            if tool_system.exists():
                content = tool_system.read_text(encoding='utf-8')
                # Compter les register_tool
                import re
                tools = re.findall(r'register_tool\(["\'](\w+)["\']', content)
                capabilities["tools"] = tools[:10] if len(tools) > 10 else tools
            
            # Générer le résumé
            result = "🧠 **MES CAPACITÉS (Auto-analyse)**\n\n"
            
            result += f"**📦 Modules ({len(capabilities['modules'])}):**\n"
            for m in capabilities["modules"]:
                result += f"  - {m}\n"
            
            result += f"\n**🎯 Skills ({len(capabilities['skills'])}):**\n"
            for s in capabilities["skills"]:
                result += f"  - {s}\n"
            
            result += f"\n**🔧 Outils ({len(capabilities['tools'])}):**\n"
            for t in capabilities["tools"]:
                result += f"  - {t}\n"
            if len(capabilities["tools"]) == 10:
                result += "  - ... et plus\n"
            
            result += "\n💡 Utilise `read_own_code('module_name/')` pour explorer un module en détail."
            
            return result
            
        except Exception as e:
            return f"❌ Erreur analyse capacités: {e}"
    
    def analyze_file(self, file_path: str) -> Dict[str, Any]:
        """Analyse la structure d'un fichier Python."""
        if file_path.startswith("src/"):
            full_path = self.root / file_path
        else:
            full_path = self.src_dir / file_path
        
        return self.analyzer.analyze_python_file(full_path)
    
    def create_backup(self, file_path: Path) -> Path:
        """Crée un backup d'un fichier avant modification."""
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        
        relative = file_path.relative_to(self.root)
        backup_path = self.backup_dir / relative
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        
        shutil.copy2(file_path, backup_path)
        logger.info(f"💾 Backup créé: {backup_path}")
        
        return backup_path
    
    def rollback(self, file_path: Path = None) -> Tuple[bool, str]:
        """
        Restaure un fichier depuis le dernier backup.
        
        Args:
            file_path: Fichier spécifique à restaurer (ou None pour tout)
            
        Returns:
            (success, message)
        """
        if not self.backup_dir.exists():
            return False, "❌ Aucun backup trouvé"
        
        restored = []
        errors = []
        
        if file_path:
            # Restaurer un fichier spécifique
            relative = file_path.relative_to(self.root)
            backup_path = self.backup_dir / relative
            
            if backup_path.exists():
                shutil.copy2(backup_path, file_path)
                restored.append(str(file_path))
                logger.info(f"✅ Rollback: {file_path}")
            else:
                return False, f"❌ Backup non trouvé pour {file_path}"
        else:
            # Restaurer tous les fichiers du backup
            for backup_file in self.backup_dir.rglob("*"):
                if backup_file.is_file():
                    relative = backup_file.relative_to(self.backup_dir)
                    original = self.root / relative
                    
                    try:
                        original.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(backup_file, original)
                        restored.append(str(relative))
                        logger.info(f"✅ Restauré: {relative}")
                    except Exception as e:
                        errors.append(f"{relative}: {e}")
        
        if errors:
            return False, f"⚠️ Restauré {len(restored)} fichiers, erreurs: {errors}"
        elif restored:
            return True, f"✅ Rollback réussi: {len(restored)} fichiers restaurés"
        else:
            return False, "❌ Aucun fichier à restaurer"
    
    def list_backups(self) -> List[Dict[str, Any]]:
        """Liste tous les backups disponibles."""
        backups = []
        
        backup_root = self.root / "backups"
        if not backup_root.exists():
            return backups
        
        for backup_session in sorted(backup_root.iterdir(), reverse=True):
            if backup_session.is_dir():
                files = list(backup_session.rglob("*"))
                file_count = len([f for f in files if f.is_file()])
                
                backups.append({
                    "session": backup_session.name,
                    "path": str(backup_session),
                    "file_count": file_count,
                    "created": backup_session.stat().st_mtime
                })
        
        return backups
    
    def apply_patch(
        self, 
        file_path: str, 
        old_content: str, 
        new_content: str,
        description: str = ""
    ) -> Tuple[bool, str]:
        """
        Applique un patch à un fichier.
        
        Args:
            file_path: Chemin du fichier à modifier
            old_content: Contenu à remplacer
            new_content: Nouveau contenu
            description: Description du changement
        
        Returns:
            (success, message)
        """
        if file_path.startswith("src/"):
            full_path = self.root / file_path
        else:
            full_path = self.src_dir / file_path
        
        if not full_path.exists():
            return False, f"❌ Fichier non trouvé: {file_path}"
        
        # Vérification sécurité
        if not str(full_path.resolve()).startswith(str(self.root.resolve())):
            return False, "❌ Accès refusé: chemin hors du projet"
        
        try:
            # Lire le contenu actuel
            current = full_path.read_text(encoding='utf-8')
            
            # Vérifier que old_content existe
            if old_content not in current:
                return False, "❌ Contenu à remplacer non trouvé dans le fichier"
            
            # Créer backup
            self.create_backup(full_path)
            
            # Appliquer le patch
            new_file_content = current.replace(old_content, new_content, 1)
            
            # Vérifier la syntaxe Python si .py
            if full_path.suffix == '.py':
                try:
                    ast.parse(new_file_content)
                except SyntaxError as e:
                    return False, f"❌ Erreur de syntaxe dans le patch: {e}"
            
            # Écrire le nouveau contenu
            full_path.write_text(new_file_content, encoding='utf-8')
            
            # Générer diff pour log
            diff = difflib.unified_diff(
                current.split('\n'),
                new_file_content.split('\n'),
                fromfile=f"a/{file_path}",
                tofile=f"b/{file_path}",
                lineterm=''
            )
            diff_text = '\n'.join(list(diff)[:50])  # Limiter à 50 lignes
            
            logger.info(f"✅ Patch appliqué: {file_path}\n{description}")
            
            return True, f"✅ Patch appliqué avec succès!\n\nDiff:\n```diff\n{diff_text}\n```"
            
        except Exception as e:
            return False, f"❌ Erreur application patch: {e}"
    
    def run_tests(self, test_path: str = "") -> Tuple[bool, str]:
        """
        Exécute les tests pour valider les changements.
        
        Args:
            test_path: Chemin spécifique de test (optionnel)
        
        Returns:
            (success, output)
        """
        try:
            cmd = ["python", "-m", "pytest", "-v", "--tb=short"]
            
            if test_path:
                cmd.append(test_path)
            else:
                # Test d'import basique si pas de pytest
                cmd = ["python", "-c", "from src.core import LumenaCore; print('Import OK')"]
            
            result = subprocess.run(
                cmd,
                cwd=str(self.root),
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=60
            )
            
            success = result.returncode == 0
            output = result.stdout + result.stderr
            
            if success:
                logger.info("✅ Tests passés")
                return True, f"✅ Tests passés!\n\n{output[:1000]}"
            else:
                logger.warning(f"❌ Tests échoués:\n{output}")
                return False, f"❌ Tests échoués:\n\n{output[:1000]}"
                
        except subprocess.TimeoutExpired:
            return False, "❌ Timeout: tests trop longs"
        except Exception as e:
            return False, f"❌ Erreur exécution tests: {e}"
    
    def git_status(self) -> str:
        """Retourne le statut git du projet."""
        try:
            result = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=str(self.root),
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace'
            )
            
            if result.returncode != 0:
                return "❌ Erreur git status"
            
            changes = result.stdout.strip()
            if not changes:
                return "✅ Aucun changement"
            
            return f"📝 Changements:\n{changes}"
            
        except Exception as e:
            return f"❌ Erreur: {e}"
    
    def git_commit(self, message: str) -> Tuple[bool, str]:
        """
        Commit les changements.
        
        Args:
            message: Message de commit
        
        Returns:
            (success, output)
        """
        try:
            # Add all changes
            add_result = subprocess.run(
                ["git", "add", "-A"],
                cwd=str(self.root),
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace'
            )
            
            if add_result.returncode != 0:
                return False, f"❌ Erreur git add: {add_result.stderr}"
            
            # Commit
            commit_result = subprocess.run(
                ["git", "commit", "-m", f"🤖 {message}"],
                cwd=str(self.root),
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace'
            )
            
            if commit_result.returncode != 0:
                if "nothing to commit" in commit_result.stdout + commit_result.stderr:
                    return True, "ℹ️ Rien à commiter"
                return False, f"❌ Erreur git commit: {commit_result.stderr}"
            
            logger.info(f"✅ Commit créé: {message}")
            return True, f"✅ Commit créé: {message}\n\n{commit_result.stdout}"
            
        except Exception as e:
            return False, f"❌ Erreur: {e}"
    
    def propose_improvement(self, file_path: str) -> str:
        """
        Analyse un fichier et propose des améliorations.
        
        Cette méthode sera utilisée par le LLM pour suggérer
        des améliorations basées sur l'analyse du code.
        """
        analysis = self.analyze_file(file_path)
        
        if "error" in analysis:
            return f"❌ {analysis['error']}"
        
        suggestions = []
        
        # Vérifier les fichiers trop longs
        info = self.analyzer.get_file_info(
            self.src_dir / file_path if not file_path.startswith("src/") 
            else self.root / file_path
        )
        
        if info.get("lines", 0) > 500:
            suggestions.append(f"⚠️ Fichier long ({info['lines']} lignes) - considérer un refactoring")
        
        # Vérifier les classes avec beaucoup de méthodes
        for cls in analysis.get("classes", []):
            if cls["method_count"] > 15:
                suggestions.append(f"⚠️ Classe {cls['name']} a {cls['method_count']} méthodes - considérer une extraction")
        
        if not suggestions:
            suggestions.append("✅ Aucune amélioration évidente détectée")
        
        return f"📊 Analyse de {file_path}:\n\n" + "\n".join(suggestions)

    def _load_daily_skill_state(self) -> Dict[str, Any]:
        if not self._daily_skill_state_file.exists():
            return {
                "last_run_date": "",
                "run_count": 0,
                "last_skill_name": "",
                "last_skill_reason": "",
                "last_status": "never",
                "fail_count": 0,
            }
        try:
            payload = json.loads(self._daily_skill_state_file.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                return payload
        except Exception as e:
            logger.debug(f"Lecture daily skill state: {e}")
        return {
            "last_run_date": "",
            "run_count": 0,
            "last_skill_name": "",
            "last_skill_reason": "",
            "last_status": "invalid_state",
            "fail_count": 0,
        }

    def _save_daily_skill_state(self, state: Dict[str, Any]) -> None:
        self._daily_skill_state_file.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self._daily_skill_state_file, state)

    def _daily_skill_candidates(self) -> List[Dict[str, str]]:
        return [
            {
                "name": "input-quality-guard",
                "description": "Valider et normaliser les entrées utilisateur avant traitement.",
                "reason": "Renforcer la robustesse des entrées runtime.",
            },
            {
                "name": "incident-response-playbook",
                "description": "Structurer les réponses en cas d'erreur critique et escalade.",
                "reason": "Améliorer la gestion d'incidents.",
            },
            {
                "name": "daily-ops-checklist",
                "description": "Checklist quotidienne d'exploitation et de disponibilité.",
                "reason": "Fiabiliser les opérations quotidiennes.",
            },
            {
                "name": "knowledge-gap-review",
                "description": "Analyser les lacunes de compétences observées et prioriser les besoins.",
                "reason": "Formaliser l'auto-évaluation des besoins skills.",
            },
            {
                "name": "safe-automation-patterns",
                "description": "Patrons d'automatisation sécurisée avec fallback et validation.",
                "reason": "Réduire les risques d'autonomie non contrôlée.",
            },
        ]

    def _sanitize_skill_name(self, raw_name: str) -> str:
        value = (raw_name or "").strip().lower()
        value = re.sub(r"[^a-z0-9_-]+", "-", value)
        value = value.replace("_", "-")
        value = re.sub(r"-{2,}", "-", value).strip("-")
        return value[:64]

    def _select_daily_skill_candidate(
        self,
        existing_skills: List[str],
        force_create: bool,
    ) -> Dict[str, str]:
        normalized_existing = {self._sanitize_skill_name(item) for item in existing_skills}

        for candidate in self._daily_skill_candidates():
            candidate_name = self._sanitize_skill_name(candidate.get("name", ""))
            if candidate_name and candidate_name not in normalized_existing:
                return {
                    "name": candidate_name,
                    "description": candidate.get("description", ""),
                    "reason": candidate.get("reason", ""),
                    "mode": "gap_fill",
                }

        if force_create:
            day_key = datetime.now().strftime("%Y-%m-%d")
            fallback_name = self._sanitize_skill_name(f"autonomy-daily-{day_key}")
            return {
                "name": fallback_name,
                "description": "Skill journalier auto-généré pour améliorer les opérations de Lumena.",
                "reason": "Cycle quotidien: création proactive même sans gap explicite.",
                "mode": "forced_daily",
            }

        return {
            "name": "",
            "description": "",
            "reason": "Aucun gap détecté et création forcée désactivée.",
            "mode": "no_op",
        }

    def run_daily_skill_cycle(
        self,
        force_create: bool = True,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """Routine quotidienne sûre: analyse skills existants et crée un skill si nécessaire."""
        state = self._load_daily_skill_state()
        today = datetime.now().strftime("%Y-%m-%d")

        if state.get("last_run_date") == today:
            return {
                "success": True,
                "status": "already_done_today",
                "date": today,
                "created": False,
                "skill_name": state.get("last_skill_name", ""),
                "reason": "Cycle déjà exécuté aujourd'hui.",
            }

        try:
            from ..skills import create_skill, get_skill_loader
        except Exception as exc:
            state["last_run_date"] = today
            state["last_status"] = "import_error"
            state["fail_count"] = int(state.get("fail_count", 0)) + 1
            state["last_skill_reason"] = f"import_skills_failed: {exc}"
            self._save_daily_skill_state(state)
            return {
                "success": False,
                "status": "import_error",
                "date": today,
                "created": False,
                "error": str(exc),
            }

        loader = get_skill_loader()
        existing = loader.list_skills()

        candidate = self._select_daily_skill_candidate(existing, force_create=force_create)
        skill_name = candidate.get("name", "")
        if not skill_name:
            state["last_run_date"] = today
            state["run_count"] = int(state.get("run_count", 0)) + 1
            state["last_status"] = "no_op"
            state["last_skill_name"] = ""
            state["last_skill_reason"] = candidate.get("reason", "")
            self._save_daily_skill_state(state)
            return {
                "success": True,
                "status": "no_op",
                "date": today,
                "created": False,
                "existing_skill_count": len(existing),
                "reason": candidate.get("reason", ""),
            }

        if self._sanitize_skill_name(skill_name) in {self._sanitize_skill_name(item) for item in existing}:
            state["last_run_date"] = today
            state["run_count"] = int(state.get("run_count", 0)) + 1
            state["last_status"] = "already_exists"
            state["last_skill_name"] = skill_name
            state["last_skill_reason"] = candidate.get("reason", "")
            self._save_daily_skill_state(state)
            return {
                "success": True,
                "status": "already_exists",
                "date": today,
                "created": False,
                "skill_name": skill_name,
                "reason": "Skill déjà présent.",
            }

        if dry_run:
            return {
                "success": True,
                "status": "dry_run",
                "date": today,
                "created": False,
                "skill_name": skill_name,
                "reason": candidate.get("reason", ""),
                "description": candidate.get("description", ""),
            }

        create_result = create_skill(
            name=skill_name,
            description=candidate.get("description", ""),
            with_script=True,
        )
        created_ok = str(create_result).strip().startswith("✅")

        if created_ok:
            loader = get_skill_loader()
            verified = self._sanitize_skill_name(skill_name) in {
                self._sanitize_skill_name(k) for k in loader.skills
            }
            created_ok = bool(verified)

        state["last_run_date"] = today
        state["run_count"] = int(state.get("run_count", 0)) + 1
        state["last_skill_name"] = skill_name
        state["last_skill_reason"] = candidate.get("reason", "")
        if created_ok:
            state["last_status"] = "created"
        else:
            state["last_status"] = "create_failed"
            state["fail_count"] = int(state.get("fail_count", 0)) + 1
        self._save_daily_skill_state(state)

        return {
            "success": created_ok,
            "status": "created" if created_ok else "create_failed",
            "date": today,
            "created": created_ok,
            "skill_name": skill_name,
            "reason": candidate.get("reason", ""),
            "create_result": create_result,
            "existing_skill_count": len(existing),
            "mode": candidate.get("mode", ""),
        }


# Instance globale avec lock thread-safe (Phase 2.1)
import threading
_self_improver: Optional[SelfImprover] = None
_self_improver_lock = threading.Lock()

def get_self_improver(lumena_root: Optional[Path] = None) -> SelfImprover:
    """Retourne l'instance du SelfImprover (thread-safe)."""
    global _self_improver
    
    # Double-check locking pattern
    if _self_improver is None:
        with _self_improver_lock:
            if _self_improver is None:
                _self_improver = SelfImprover(lumena_root)
    return _self_improver
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the Apache License, Version 2.0
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
