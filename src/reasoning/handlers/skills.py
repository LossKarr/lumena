"""
skills.py - Handlers skills fragmentés depuis react.py.

Handlers: read_own_code, create_skill, list_skills, pip_check,
          search_in_code, get_my_capabilities, rollback, list_backups.

Chaque handler est une fonction async standalone:
    async def handler_name(ctx: HandlerContext, **kwargs) -> HandlerResult
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

from .context import HandlerContext
from .contracts import HandlerResult
from .registry_v2 import HandlerDef
from ...utils.paths import DATA_DIR as _DATA_DIR


# ─── Handlers ──────────────────────────────────────────────────────────────

async def read_own_code_handler(
    ctx: HandlerContext,
    file_path: str,
    start_line: int = None,
    end_line: int = None,
) -> HandlerResult:
    """Lit un fichier de code source de LUMENA."""
    try:
        # Nettoyer le chemin
        file_path = file_path.strip().replace("\\", "/").lstrip("/")

        # Construire le chemin de base
        base_path = ctx.lumena_root
        src_path = base_path / "src"

        # Essayer plusieurs chemins possibles
        paths_to_try = [
            src_path / file_path,
            base_path / file_path,
            base_path / "src" / file_path,
        ]

        if file_path.startswith("src/"):
            clean_path = file_path[4:]
            paths_to_try.insert(0, base_path / file_path)
            paths_to_try.append(src_path / clean_path)

        if file_path.startswith("skills/") or file_path.startswith("data/"):
            paths_to_try.insert(0, base_path / file_path)

        # Fichiers mémoire (MEMORY.md, etc.) → chercher dans data/
        if not file_path.startswith("data/") and not any(p.exists() for p in paths_to_try):
            paths_to_try.append(_DATA_DIR / file_path)

        full_path = None
        for path in paths_to_try:
            if path.exists():
                full_path = path
                break

        if full_path is None:
            tried = ", ".join([str(p.name) for p in paths_to_try[:3]])
            return HandlerResult.fail(
                f"❌ Fichier non trouvé: {file_path}\nChemins essayés dans: {tried}",
                handler_name="read_own_code",
            )

        if full_path.is_dir():
            entries = []
            for item in sorted(
                full_path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())
            ):
                prefix = "DIR" if item.is_dir() else "FILE"
                entries.append(f"- [{prefix}] {item.name}")
            preview = "\n".join(entries[:80])
            if len(entries) > 80:
                preview += "\n... [tronque]"
            return HandlerResult.ok(
                f"Dossier {file_path} ({len(entries)} elements):\n{preview}",
                handler_name="read_own_code",
            )

        content = full_path.read_text(encoding="utf-8")
        lines = content.split("\n")
        total_lines = len(lines)

        if start_line is not None or end_line is not None:
            start = max(1, start_line or 1) - 1
            end = min(total_lines, end_line or total_lines)
            lines = lines[start:end]
            content = "\n".join(lines)
            line_info = f" (lignes {start+1}-{end} sur {total_lines})"
        else:
            line_info = f" ({total_lines} lignes)"
            if len(content) > 5000:
                content = content[:5000] + "\n\n... [tronqué, fichier trop long]"

        return HandlerResult.ok(
            f"📄 Contenu de {file_path}{line_info}:\n```\n{content}\n```",
            handler_name="read_own_code",
        )
    except Exception as e:
        return HandlerResult.fail(
            f"Erreur lecture: {e}", handler_name="read_own_code"
        )


async def create_skill_handler(
    ctx: HandlerContext, name: str, content: str
) -> HandlerResult:
    """Crée un nouveau skill."""
    try:
        skills_path = ctx.lumena_root / "skills"
        normalized = re.sub(
            r"[^a-z0-9\-]+", "-", name.lower().replace("_", "-")
        ).strip("-")
        if not normalized:
            return HandlerResult.fail(
                f"Nom de skill invalide: {name}", handler_name="create_skill"
            )

        skill_dir = skills_path / normalized
        if skill_dir.exists():
            return HandlerResult.fail(
                f"Le skill '{normalized}' existe deja.",
                handler_name="create_skill",
            )

        skill_dir.mkdir(parents=True, exist_ok=False)
        skill_file = skill_dir / "SKILL.md"
        payload = (content or "").strip()
        if not payload.startswith("---"):
            payload = (
                "---\n"
                f"name: {normalized}\n"
                f"description: Skill {normalized}\n"
                "---\n\n"
                f"{payload}\n"
            )
        skill_file.write_text(payload, encoding="utf-8")

        from ...skills import reload_skills

        reload_skills()
        return HandlerResult.ok(
            f"Skill '{normalized}' cree avec succes.\n"
            f"Fichier: skills/{normalized}/SKILL.md",
            handler_name="create_skill",
        )
    except Exception as e:
        return HandlerResult.fail(
            f"Erreur création skill: {e}", handler_name="create_skill"
        )


async def list_skills_handler(ctx: HandlerContext) -> HandlerResult:
    """Liste les skills disponibles."""
    try:
        from ...skills import list_skills

        output = list_skills()
        return HandlerResult.ok(output, handler_name="list_skills")
    except Exception as e:
        return HandlerResult.fail(
            f"Erreur listing skills: {e}", handler_name="list_skills"
        )


# Mapping pip name → import name pour les packages courants dont le nom diffère
_PIP_TO_IMPORT = {
    "pillow": "PIL", "scikit-learn": "sklearn", "python-dateutil": "dateutil",
    "pyyaml": "yaml", "beautifulsoup4": "bs4", "opencv-python": "cv2",
    "opencv-python-headless": "cv2", "python-dotenv": "dotenv",
}


async def pip_check_handler(ctx: HandlerContext, package: str) -> HandlerResult:
    """Vérifie si un package est installé."""
    try:
        import importlib.util

        pkg_lower = package.lower()
        # Résoudre le vrai nom d'import (Pillow→PIL, etc.)
        module_name = _PIP_TO_IMPORT.get(pkg_lower, pkg_lower.replace("-", "_"))
        spec = importlib.util.find_spec(module_name)

        if spec is not None:
            return HandlerResult.ok(
                f"✅ Le package '{package}' est installé.",
                handler_name="pip_check",
            )

        # Fallback: vérifier via pip list (couvre les cas hors sys.path)
        import subprocess, sys
        result = subprocess.run(
            [sys.executable, "-m", "pip", "show", package],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            return HandlerResult.ok(
                f"✅ Le package '{package}' est installé.",
                handler_name="pip_check",
            )

        return HandlerResult.ok(
            f"❌ Le package '{package}' n'est PAS installé.\n"
            f"💡 Pour l'installer: pip install {package}",
            handler_name="pip_check",
        )
    except Exception as e:
        return HandlerResult.fail(
            f"Erreur vérification: {e}", handler_name="pip_check"
        )


async def search_in_code_handler(
    ctx: HandlerContext, query: str, file_extension: str = ".py"
) -> HandlerResult:
    """Recherche dans le code source de LUMENA."""
    try:
        from ...autonomy.self_improve import get_self_improver

        lumena_root = ctx.lumena_root
        improver = get_self_improver(lumena_root)
        output = improver.search_in_code(query, file_extension)
        return HandlerResult.ok(output, handler_name="search_in_code")
    except ImportError:
        return HandlerResult.fail(
            "❌ Module self_improve non disponible",
            handler_name="search_in_code",
        )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur: {e}", handler_name="search_in_code"
        )


async def get_my_capabilities_handler(ctx: HandlerContext) -> HandlerResult:
    """Retourne les capacités de LUMENA."""
    try:
        from ...autonomy.self_improve import get_self_improver

        lumena_root = ctx.lumena_root
        improver = get_self_improver(lumena_root)
        output = improver.get_my_capabilities()
        return HandlerResult.ok(output, handler_name="get_my_capabilities")
    except ImportError:
        return HandlerResult.fail(
            "❌ Module self_improve non disponible",
            handler_name="get_my_capabilities",
        )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur: {e}", handler_name="get_my_capabilities"
        )


async def rollback_handler(
    ctx: HandlerContext, file_path: str = ""
) -> HandlerResult:
    """Restaure les fichiers depuis le backup."""
    try:
        from ...autonomy.self_improve import get_self_improver

        improver = get_self_improver()
        target = Path(file_path) if file_path else None
        success, message = improver.rollback(target)
        return HandlerResult.ok(message, handler_name="rollback")
    except ImportError:
        return HandlerResult.fail(
            "❌ Module self_improve non disponible",
            handler_name="rollback",
        )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur rollback: {e}", handler_name="rollback"
        )


async def list_backups_handler(ctx: HandlerContext) -> HandlerResult:
    """Liste les backups disponibles."""
    try:
        from ...autonomy.self_improve import get_self_improver

        improver = get_self_improver()
        backups = improver.list_backups()

        if not backups:
            return HandlerResult.ok(
                "📂 Aucun backup disponible", handler_name="list_backups"
            )

        result = "📂 **Backups disponibles**\n\n"
        for b in backups[:10]:
            result += f"• `{b['session']}` - {b['file_count']} fichiers\n"

        return HandlerResult.ok(result, handler_name="list_backups")
    except ImportError:
        return HandlerResult.fail(
            "❌ Module self_improve non disponible",
            handler_name="list_backups",
        )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur: {e}", handler_name="list_backups"
        )


async def execute_skill_handler(
    ctx: HandlerContext,
    skill_name: str = "",
    script_name: str = "",
    args: str = "",
) -> HandlerResult:
    """Exécute un skill installé par son nom."""
    if not skill_name:
        return HandlerResult.fail(
            "❌ execute_skill: nom du skill requis.", handler_name="execute_skill"
        )
    try:
        from ...skills import execute_skill_script

        result = execute_skill_script(
            skill_name, args=args, script_name=script_name or None
        )
        return HandlerResult.ok(result, handler_name="execute_skill")
    except ImportError:
        return HandlerResult.fail(
            "❌ Module skills non disponible.", handler_name="execute_skill"
        )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur execution skill: {e}", handler_name="execute_skill"
        )


async def reload_skills_handler(ctx: HandlerContext) -> HandlerResult:
    """Recharge tous les skills depuis le disque sans redémarrer Lumena."""
    try:
        from ...skills import reload_skills
        loaded = reload_skills()
        lines = ["Hot-reload skills terminé", f"- total: {len(loaded)}"]
        for name in sorted(loaded.keys()):
            lines.append(f"  - {name}")
        return HandlerResult.ok("\n".join(lines), handler_name="reload_skills")
    except ImportError:
        return HandlerResult.fail("❌ reload_skills: module skills non disponible.", handler_name="reload_skills")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur reload_skills: {e}", handler_name="reload_skills")


async def sync_skills_main_handler(ctx: HandlerContext) -> HandlerResult:
    """Synchronise les skills locaux avec le dépôt principal."""
    try:
        from ...skills import reload_skills, sync_skills_main
        result = sync_skills_main()
        reload_skills()
        return HandlerResult.ok(
            f"Sync skills-main terminée\n"
            f"- updated: {result.get('updated_count', 0)}\n"
            f"- skipped: {result.get('skipped_count', 0)}\n"
            f"- conflicts: {result.get('conflicts_count', 0)}\n"
            f"- errors: {result.get('errors_count', 0)}",
            handler_name="sync_skills_main",
        )
    except ImportError:
        return HandlerResult.fail("❌ sync_skills_main: module skills non disponible.", handler_name="sync_skills_main")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur sync_skills_main: {e}", handler_name="sync_skills_main")


async def read_skill_reference_handler(
    ctx: HandlerContext,
    skill_name: str,
) -> HandlerResult:
    """Lit la documentation de référence (SKILL.md) d'un skill installé."""
    try:
        skill_path = ctx.lumena_root / "skills" / skill_name / "SKILL.md"
        if not skill_path.exists():
            skill_path = ctx.lumena_root / "skills" / skill_name / "README.md"
        if not skill_path.exists():
            return HandlerResult.fail(f"❌ Skill '{skill_name}' introuvable.", handler_name="read_skill_reference")
        content = skill_path.read_text(encoding="utf-8")
        return HandlerResult.ok(content, handler_name="read_skill_reference")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur read_skill_reference: {e}", handler_name="read_skill_reference")


async def edit_own_code_handler(
    ctx: HandlerContext,
    file_path: str,
    old_content: str,
    new_content: str,
    reason: str = "",
) -> HandlerResult:
    """Modifie un fichier de code source de Lumena (auto-amélioration)."""
    try:
        target = ctx.lumena_root / file_path
        if not target.exists():
            target = ctx.lumena_root / "src" / file_path
        if not target.exists():
            return HandlerResult.fail(f"❌ Fichier introuvable: {file_path}", handler_name="edit_own_code")
        text = target.read_text(encoding="utf-8")
        if old_content not in text:
            return HandlerResult.fail(f"❌ old_content introuvable dans {file_path}", handler_name="edit_own_code")
        text = text.replace(old_content, new_content, 1)
        target.write_text(text, encoding="utf-8")
        return HandlerResult.ok(f"✅ {file_path} modifié.", handler_name="edit_own_code")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur edit_own_code: {e}", handler_name="edit_own_code")


async def run_tests_handler(
    ctx: HandlerContext,
    test_path: str = "",
    timeout: int = 120,
) -> HandlerResult:
    """Lance la suite de tests Lumena et retourne le rapport de résultats."""
    if not test_path:
        return HandlerResult.ok(
            "⏭️ run_tests : test_path requis. Spécifie un chemin ou utilise run_tests depuis le CodeAgent.",
            handler_name="run_tests",
        )
    try:
        import asyncio
        cmd = ["py", "-m", "pytest", test_path, "-q", "--tb=short", "--no-header", f"--timeout={timeout}"]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(ctx.lumena_root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout + 10)
        output = stdout.decode("utf-8", errors="replace")
        return HandlerResult.ok(output, handler_name="run_tests")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur run_tests: {e}", handler_name="run_tests")


# ─── HandlerDefs ───────────────────────────────────────────────────────────────

def get_skills_handler_defs() -> List[HandlerDef]:
    """Retourne les définitions des 14 handlers skills."""
    return [
        HandlerDef(
            name="read_own_code",
            description="Lit un fichier de code source de LUMENA (pour s'améliorer)",
            parameters={
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Chemin relatif (ex: src/core.py, skills/pdf/SKILL.md)",
                    },
                    "start_line": {
                        "type": "integer",
                        "description": "Ligne de début (défaut: 1)",
                    },
                    "end_line": {
                        "type": "integer",
                        "description": "Ligne de fin (défaut: fin du fichier)",
                    },
                },
                "required": ["file_path"],
            },
            handler=read_own_code_handler,
        ),
        HandlerDef(
            name="create_skill",
            description="Crée un nouveau skill dans le dossier skills/",
            parameters={
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Nom du skill (snake_case)",
                    },
                    "content": {
                        "type": "string",
                        "description": "Contenu complet du fichier skill (format markdown avec frontmatter YAML)",
                    },
                },
                "required": ["name", "content"],
            },
            handler=create_skill_handler,
        ),
        HandlerDef(
            name="list_skills",
            description="Liste tous les skills disponibles",
            parameters={"properties": {}, "required": []},
            handler=list_skills_handler,
        ),
        HandlerDef(
            name="pip_check",
            description="Vérifie si un package Python est installé",
            parameters={
                "properties": {
                    "package": {
                        "type": "string",
                        "description": "Nom du package à vérifier",
                    },
                },
                "required": ["package"],
            },
            handler=pip_check_handler,
        ),
        HandlerDef(
            name="search_in_code",
            description="Recherche un terme dans tout le code source de LUMENA. Utile pour comprendre son fonctionnement.",
            parameters={
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Terme à rechercher",
                    },
                    "file_extension": {
                        "type": "string",
                        "description": "Extension (.py, .md)",
                        "default": ".py",
                    },
                },
                "required": ["query"],
            },
            handler=search_in_code_handler,
        ),
        HandlerDef(
            name="get_my_capabilities",
            description="Retourne un résumé des capacités de LUMENA (modules, skills, outils). Pour l'auto-connaissance.",
            parameters={"properties": {}, "required": []},
            handler=get_my_capabilities_handler,
        ),
        HandlerDef(
            name="rollback",
            description="SÉCURITÉ: Restaure les fichiers depuis le dernier backup. Utiliser si une modification a cassé quelque chose.",
            parameters={
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Fichier spécifique à restaurer (vide = tout restaurer)",
                    },
                },
                "required": [],
            },
            handler=rollback_handler,
        ),
        HandlerDef(
            name="list_backups",
            description="Liste tous les points de sauvegarde disponibles pour rollback.",
            parameters={"properties": {}, "required": []},
            handler=list_backups_handler,
        ),
        HandlerDef(
            name="execute_skill",
            description="Exécute un skill installé par son nom.",
            parameters={
                "properties": {
                    "skill_name": {
                        "type": "string",
                        "description": "Nom du skill à exécuter (snake_case)",
                    },
                    "script_name": {
                        "type": "string",
                        "description": "Nom du script spécifique",
                    },
                    "args": {
                        "type": "string",
                        "description": "Arguments passés au skill",
                    },
                },
                "required": ["skill_name"],
            },
            handler=execute_skill_handler,
        ),
        HandlerDef(
            name="reload_skills",
            description="Recharge tous les skills depuis le disque sans redémarrer Lumena. Utile après ajout ou modification d'un skill.",
            parameters={"properties": {}, "required": []},
            handler=reload_skills_handler,
            category="skills",
            source_module="handlers.skills",
        ),
        HandlerDef(
            name="sync_skills_main",
            description="Synchronise les skills locaux avec le dépôt principal Lumena (récupère les dernières mises à jour).",
            parameters={"properties": {}, "required": []},
            handler=sync_skills_main_handler,
            category="skills",
            source_module="handlers.skills",
        ),
        HandlerDef(
            name="read_skill_reference",
            description="Lit la documentation de référence (SKILL.md / README.md) d'un skill installé.",
            parameters={
                "properties": {
                    "skill_name": {"type": "string", "description": "Nom du skill (ex: pdf, trading, mail)"},
                },
                "required": ["skill_name"],
            },
            handler=read_skill_reference_handler,
            category="skills",
            source_module="handlers.skills",
        ),
        HandlerDef(
            name="edit_own_code",
            description=(
                "⚠️ AUTO-AMÉLIORATION: Modifie un fichier de code source de Lumena elle-même. "
                "Remplace old_content par new_content dans le fichier spécifié. "
                "Toujours vérifier avec read_own_code avant d'utiliser cet outil."
            ),
            parameters={
                "properties": {
                    "file_path": {"type": "string", "description": "Chemin relatif du fichier (ex: src/core.py)"},
                    "old_content": {"type": "string", "description": "Contenu exact à remplacer"},
                    "new_content": {"type": "string", "description": "Nouveau contenu à insérer"},
                    "reason": {"type": "string", "description": "Justification de la modification", "default": ""},
                },
                "required": ["file_path", "old_content", "new_content"],
            },
            handler=edit_own_code_handler,
            category="skills",
            source_module="handlers.skills",
        ),
        HandlerDef(
            name="run_tests",
            description="Lance la suite de tests Lumena (pytest) et retourne le rapport complet. Utiliser après une auto-modification du code.",
            parameters={
                "properties": {
                    "test_path": {"type": "string", "description": "Chemin optionnel (ex: tests/test_react.py). Vide = tous les tests.", "default": ""},
                    "timeout": {"type": "integer", "description": "Timeout en secondes", "default": 120},
                },
                "required": [],
            },
            handler=run_tests_handler,
            category="skills",
            source_module="handlers.skills",
        ),
    ]
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
