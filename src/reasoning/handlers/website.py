"""
website.py — Handlers V2 pour les outils Website Builder.

7 outils: generate_website, write_website_files, serve_website,
          stop_website_server, edit_website, export_website_zip,
          list_website_projects

Wrappers V2 autour des handlers de src.tools.website_builder.
Pattern: async (ctx: HandlerContext, **kwargs) -> HandlerResult
"""

from __future__ import annotations

from typing import List, Optional

from loguru import logger
from .context import HandlerContext
from .contracts import HandlerResult
from .registry_v2 import HandlerDef

try:
    from ...tools.website_builder import (
        generate_website_handler as _generate_website,
        write_website_files_handler as _write_website_files,
        serve_website_handler as _serve_website,
        stop_server_handler as _stop_server,
        edit_website_handler as _edit_website,
        export_website_zip_handler as _export_website_zip,
        list_website_projects_handler as _list_website_projects,
        parse_website_response as _parse_website_response,
        write_project_to_disk as _write_project_to_disk,
        build_design_directives as _build_design_directives,
        WEBSITE_GENERATE_PROMPT as _WEBSITE_GENERATE_PROMPT,
    )
    WEBSITE_BUILDER_AVAILABLE = True
except ImportError:
    WEBSITE_BUILDER_AVAILABLE = False
    def _build_design_directives(d: str) -> str: return ""  # type: ignore[misc]
    _WEBSITE_GENERATE_PROMPT = ""

try:
    from .project import create_project_handler as _create_project_handler
    _PROJECT_HANDLER_AVAILABLE = True
except ImportError:
    _PROJECT_HANDLER_AVAILABLE = False

try:
    from ...tools.apply_patch import apply_patch as _apply_patch
    _PATCH_AVAILABLE = True
except ImportError:
    _PATCH_AVAILABLE = False


async def generate_website_handler(
    ctx: HandlerContext,
    description: str = "",
    project_name: str = "",
    project_type: str = "frontend",
    output_dir: str = "",
) -> HandlerResult:
    """
    Phase 5 : génère un site web complet en déléguant à create_project_handler.
    Enrichit la description avec les directives de design avant de déléguer.
    """
    if not description:
        return HandlerResult.fail("❌ Paramètre 'description' requis", handler_name="generate_website")

    if _PROJECT_HANDLER_AVAILABLE and WEBSITE_BUILDER_AVAILABLE:
        try:
            design = _build_design_directives(description)
            # Option (a) : enrichir description avec contexte web + directives design
            enriched = (
                f"Site web {project_type} : {description}\n\n"
                f"Directives de design :\n{design}\n\n"
                f"Instructions de génération :\n{_WEBSITE_GENERATE_PROMPT[:3000]}"
            )
            result = await _create_project_handler(
                ctx=ctx,
                description=enriched,
                project_name=project_name or "",
                output_dir=output_dir or "",
                auto_run=False,
            )
            return HandlerResult.ok(result.output if hasattr(result, 'output') else str(result), handler_name="generate_website")
        except Exception as e:
            # Fallback vers l'ancien comportement si create_project_handler échoue
            pass

    # Fallback legacy
    if not WEBSITE_BUILDER_AVAILABLE:
        return HandlerResult.fail("❌ Module website_builder non disponible", handler_name="generate_website")
    try:
        result = await _generate_website(
            description=description,
            project_name=project_name,
            project_type=project_type,
            output_dir=output_dir,
        )
        return HandlerResult.ok(result, handler_name="generate_website")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur generate_website: {e}", handler_name="generate_website")


async def write_website_files_handler(
    ctx: HandlerContext,
    json_data: str = "",
    output_dir: str = "",
    project_name: str = "",
) -> HandlerResult:
    if not WEBSITE_BUILDER_AVAILABLE:
        return HandlerResult.fail("❌ Module website_builder non disponible", handler_name="write_website_files")
    try:
        result = await _write_website_files(
            json_data=json_data,
            output_dir=output_dir,
            project_name=project_name,
        )
        return HandlerResult.ok(result, handler_name="write_website_files")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur write_website_files: {e}", handler_name="write_website_files")


async def serve_website_handler(
    ctx: HandlerContext,
    project_name: str = "",
    port: int = 8080,
    directory: str = "",
) -> HandlerResult:
    if not WEBSITE_BUILDER_AVAILABLE:
        return HandlerResult.fail("❌ Module website_builder non disponible", handler_name="serve_website")
    try:
        result = await _serve_website(
            project_name=project_name,
            port=port,
            directory=directory,
        )
        return HandlerResult.ok(result, handler_name="serve_website")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur serve_website: {e}", handler_name="serve_website")


async def stop_website_server_handler(ctx: HandlerContext) -> HandlerResult:
    if not WEBSITE_BUILDER_AVAILABLE:
        return HandlerResult.fail("❌ Module website_builder non disponible", handler_name="stop_website_server")
    try:
        result = await _stop_server()
        return HandlerResult.ok(result, handler_name="stop_website_server")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur stop_website_server: {e}", handler_name="stop_website_server")


async def edit_website_handler(
    ctx: HandlerContext,
    modifications: str = "",
    project_name: str = "",
    directory: str = "",
) -> HandlerResult:
    if not WEBSITE_BUILDER_AVAILABLE:
        return HandlerResult.fail("❌ Module website_builder non disponible", handler_name="edit_website")
    try:
        # Phase 1 : Construire le prompt (via l'ancien handler qui prépare le contexte)
        edit_prompt = await _edit_website(
            modifications=modifications,
            project_name=project_name,
            directory=directory,
        )
        if edit_prompt.startswith("❌"):
            return HandlerResult.fail(edit_prompt, handler_name="edit_website")

        # Phase 2 : Appeler le LLM pour générer les modifications
        lumena = ctx.lumena
        if not lumena or not hasattr(lumena, "llm"):
            return HandlerResult.ok(edit_prompt, handler_name="edit_website")

        llm = lumena.llm
        from loguru import logger

        # Résoudre le répertoire cible
        from pathlib import Path
        if directory:
            target = Path(directory)
        elif project_name:
            from ...utils.paths import WORKSPACE_DIR
            target = WORKSPACE_DIR / project_name
        else:
            from ...utils.paths import WORKSPACE_DIR
            workspace = WORKSPACE_DIR
            if workspace.exists():
                dirs = sorted(
                    [p for p in workspace.iterdir() if p.is_dir()],
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                )
                if dirs:
                    target = dirs[0]
                    project_name = target.name
                else:
                    return HandlerResult.fail("❌ Aucun projet trouvé", handler_name="edit_website")
            else:
                return HandlerResult.fail("❌ Workspace introuvable", handler_name="edit_website")

        # ── Diff-based edit mode : patch ciblé au lieu de fichiers complets ──
        if _PATCH_AVAILABLE:
            logger.info("[edit_website] 🔧 Mode diff-based: génération de patch ciblé")

            raw_response = await llm.chat(
                messages=[
                    {"role": "system", "content": edit_prompt},
                    {"role": "user", "content": (
                        "Applique les modifications demandées en utilisant le format PATCH suivant.\n"
                        "Retourne UNIQUEMENT un patch, pas de fichiers complets.\n\n"
                        "Format:\n"
                        "*** Begin Patch\n"
                        "*** Update File: chemin/fichier.ext\n"
                        "@@\n"
                        "- ancienne ligne exacte\n"
                        "+ nouvelle ligne\n"
                        "*** End File\n"
                        "*** End Patch\n\n"
                        "Pour ajouter un nouveau fichier:\n"
                        "*** Add File: chemin/nouveau.ext\n"
                        "[contenu complet]\n"
                        "*** End File\n\n"
                        "Sois PRÉCIS sur les lignes à remplacer (copie exacte)."
                    )},
                ],
                temperature=0.3,
            )

            # Tenter d'appliquer comme patch
            if "*** Begin Patch" in raw_response or "*** Update File" in raw_response or "*** Add File" in raw_response:
                patch_result = await _apply_patch(raw_response, workspace_root=target)
                if patch_result.success and (patch_result.modified or patch_result.added):
                    all_changed = patch_result.modified + patch_result.added
                    files_list = "\n".join(f"  📄 {f}" for f in all_changed)
                    return HandlerResult.ok(
                        f"✅ Modifications appliquées (mode diff) !\n\n"
                        f"📂 Projet: {project_name or target.name}\n"
                        f"📁 Dossier: {target}\n"
                        f"📊 {len(all_changed)} fichier(s) modifié(s)\n\n"
                        f"Fichiers modifiés:\n{files_list}\n\n"
                        f"📝 {patch_result.summary()}",
                        handler_name="edit_website",
                    )
                else:
                    logger.warning("[edit_website] Patch échoué: {} — fallback JSON complet", patch_result.errors)
            else:
                logger.info("[edit_website] Réponse non-patch, tentative fallback JSON")

        # ── Fallback: mode fichiers complets (ancien comportement) ──
        logger.info("[edit_website] Mode complet: régénération des fichiers modifiés")

        raw_response_full = await llm.chat(
            messages=[
                {"role": "system", "content": edit_prompt},
                {"role": "user", "content": (
                    "Applique les modifications demandées. "
                    "Retourne UNIQUEMENT le JSON avec les fichiers COMPLETS modifiés, "
                    'au format {"files": {"chemin": "contenu complet"}, "summary": "..."}'
                )},
            ],
            temperature=0.3,
        )

        parsed = _parse_website_response(raw_response_full)
        if not parsed or not parsed.get("files"):
            logger.warning("[edit_website] ⚠️ LLM n'a pas retourné de JSON valide — retour du prompt brut")
            return HandlerResult.ok(edit_prompt, handler_name="edit_website")

        write_result = _write_project_to_disk(parsed, target)

        if write_result["success"]:
            files_list = "\n".join(f"  📄 {f}" for f in parsed["files"].keys())
            summary = parsed.get("summary", "Modifications appliquées")
            return HandlerResult.ok(
                f"✅ Modifications appliquées avec succès !\n\n"
                f"📂 Projet: {project_name or target.name}\n"
                f"📁 Dossier: {target}\n"
                f"📊 {write_result['files_written']} fichier(s) modifié(s)\n\n"
                f"Fichiers modifiés:\n{files_list}\n\n"
                f"📝 {summary}",
                handler_name="edit_website",
            )
        else:
            errors_text = "\n".join(f"  ❌ {e}" for e in write_result.get("errors", []))
            return HandlerResult.fail(f"❌ Erreur écriture:\n{errors_text}", handler_name="edit_website")

    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur edit_website: {e}", handler_name="edit_website")


async def export_website_zip_handler(
    ctx: HandlerContext,
    project_name: str = "",
    directory: str = "",
    output_path: str = "",
) -> HandlerResult:
    if not WEBSITE_BUILDER_AVAILABLE:
        return HandlerResult.fail("❌ Module website_builder non disponible", handler_name="export_website_zip")
    try:
        result = await _export_website_zip(
            project_name=project_name,
            directory=directory,
            output_path=output_path,
        )
        return HandlerResult.ok(result, handler_name="export_website_zip")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur export_website_zip: {e}", handler_name="export_website_zip")


async def list_website_projects_handler(ctx: HandlerContext) -> HandlerResult:
    if not WEBSITE_BUILDER_AVAILABLE:
        return HandlerResult.fail("❌ Module website_builder non disponible", handler_name="list_website_projects")
    try:
        result = await _list_website_projects()
        return HandlerResult.ok(result, handler_name="list_website_projects")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur list_website_projects: {e}", handler_name="list_website_projects")


async def check_web_project_handler(
    ctx: HandlerContext,
    project_dir: str = "",
) -> HandlerResult:
    """
    Analyse un projet web et rapporte les problèmes réels :
    - Validation statique cross-files (type=module, sélecteurs, imports CSS…)
    - Syntaxe JS via `node --check` si Node.js disponible
    - Fichiers HTML/JS/CSS manquants référencés
    """
    import asyncio
    import sys
    from pathlib import Path

    # Résoudre le répertoire
    if project_dir:
        base = Path(ctx.lumena_root) / project_dir if not Path(project_dir).is_absolute() else Path(project_dir)
    else:
        # Chercher le dernier projet web généré dans workspace/
        from ...utils.paths import WORKSPACE_DIR
        ws = WORKSPACE_DIR
        candidates = sorted(
            [d for d in ws.rglob("index.html") if d.is_file()],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not candidates:
            return HandlerResult.fail(
                "❌ Aucun projet web trouvé. Précise project_dir.",
                handler_name="check_web_project",
            )
        base = candidates[0].parent

    if not base.exists():
        return HandlerResult.fail(
            f"❌ Répertoire non trouvé: {base}",
            handler_name="check_web_project",
        )

    # ── Chargement des fichiers ──
    all_files: dict = {}
    for fp in base.rglob("*"):
        if fp.is_file() and not any(p.startswith(".") for p in fp.relative_to(base).parts):
            ext = fp.suffix.lower()
            if ext in (".html", ".htm", ".css", ".js", ".mjs", ".ts", ".tsx", ".json"):
                rel = str(fp.relative_to(base)).replace("\\", "/")
                try:
                    all_files[rel] = fp.read_text(encoding="utf-8", errors="replace")
                except Exception as e:
                    logger.debug("[website] read file %s: %s", rel, e)

    if not all_files:
        return HandlerResult.fail(
            f"❌ Aucun fichier web dans {base}",
            handler_name="check_web_project",
        )

    lines: list[str] = [f"## check_web_project — {base.name}\n"]
    lines.append(f"**Répertoire** : `{base}`")
    lines.append(f"**Fichiers analysés** : {len(all_files)}\n")

    # ── Validation statique Lumena ──
    _validator_ok = False
    try:
        from ...tools.code_validator import validate_project as _validate_project
        report = _validate_project(all_files, project_dir=base)
        _validator_ok = True
        if report.is_clean:
            lines.append("✅ **Validation statique** : aucun problème détecté")
        else:
            lines.append(f"⚠️ **Validation statique** : {report.summary()}")
            for issue in report.issues[:30]:
                sev = "❌" if issue.severity.value == "error" else "⚠️"
                lines.append(f"  {sev} `{issue.file_path}:{issue.line}` [{issue.code}] {issue.message}")
                if issue.suggestion:
                    lines.append(f"     → {issue.suggestion}")
            if len(report.issues) > 30:
                lines.append(f"  … et {len(report.issues) - 30} autres issues")
    except Exception as e:
        lines.append(f"⚠️ Validation statique indisponible: {e}")

    # ── Vérification Node.js `node --check` sur chaque JS ──
    _node_available = False
    try:
        _check = await asyncio.create_subprocess_exec(
            "node", "--version",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        _stdout, _ = await asyncio.wait_for(_check.communicate(), timeout=5)
        _node_ver = _stdout.decode().strip()
        _node_available = True
    except Exception:
        _node_ver = None

    if _node_available:
        lines.append(f"\n### Syntaxe JS (node {_node_ver})")
        _js_errors: list[str] = []
        for rel, content in all_files.items():
            if not rel.endswith(".js"):
                continue
            fp = base / rel
            try:
                proc = await asyncio.create_subprocess_exec(
                    "node", "--check", str(fp),
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                )
                _, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
                if proc.returncode != 0:
                    err = stderr.decode(errors="replace").strip()
                    # Simplifier le chemin dans le message
                    err_short = err.replace(str(fp), rel).replace(str(base), "")
                    _js_errors.append(f"  ❌ `{rel}` : {err_short[:300]}")
            except asyncio.TimeoutError:
                _js_errors.append(f"  ⏱ `{rel}` : timeout node --check")
            except Exception as e2:
                _js_errors.append(f"  ⚠️ `{rel}` : {e2}")

        if _js_errors:
            lines.extend(_js_errors)
        else:
            lines.append("  ✅ Tous les fichiers JS sont syntaxiquement valides")
    else:
        lines.append("\n### Syntaxe JS\n  ℹ️ Node.js non disponible — vérification syntaxique sautée")

    # ── Vérification fichiers référencés manquants ──
    lines.append("\n### Références manquantes")
    import re as _re
    _missing: list[str] = []
    for rel, content in all_files.items():
        if not rel.endswith((".html", ".htm")):
            continue
        # <script src="...">
        for m in _re.finditer(r'<script\b[^>]*\bsrc=["\']([^"\']+)["\']', content, _re.IGNORECASE):
            src = m.group(1).split("?")[0].split("#")[0].lstrip("./")
            # Chercher dans les fichiers connus
            if not any(fp.endswith(src) or fp.endswith(src.lstrip("/")) for fp in all_files):
                # Vérifier sur disque
                if not (base / src).exists():
                    line_no = content[:m.start()].count("\n") + 1
                    _missing.append(f"  ❌ `{rel}:{line_no}` → JS manquant: `{src}`")
        # <link rel="stylesheet" href="...">
        for m in _re.finditer(r'<link\b[^>]*\bhref=["\']([^"\']+)["\']', content, _re.IGNORECASE):
            href = m.group(1).split("?")[0].split("#")[0].lstrip("./")
            if href.endswith(".css") and not any(fp.endswith(href) for fp in all_files):
                if not (base / href).exists():
                    line_no = content[:m.start()].count("\n") + 1
                    _missing.append(f"  ❌ `{rel}:{line_no}` → CSS manquant: `{href}`")

    if _missing:
        lines.extend(_missing[:20])
        if len(_missing) > 20:
            lines.append(f"  … et {len(_missing) - 20} autres fichiers manquants")
    else:
        lines.append("  ✅ Tous les fichiers référencés sont présents")

    lines.append(f"\n---\n📁 Projet : `{base}`")
    return HandlerResult.ok("\n".join(lines), handler_name="check_web_project")


def get_website_handler_defs() -> List[HandlerDef]:
    """Retourne les 7 définitions de handlers website."""
    return [
        HandlerDef(
            name="generate_website",
            description=(
                "Génère un site web complet (frontend + backend + API) à partir d'une description. "
                "Crée la structure du projet, les fichiers HTML/CSS/JS, et optionnellement le backend PHP/Node + SQL. "
                "Utilise pour: créer un site, faire une landing page, construire une app web."
            ),
            parameters={
                "properties": {
                    "description": {"type": "string", "description": "Description du site à générer"},
                    "project_name": {"type": "string", "description": "Nom du projet (ex: 'restaurant-bella')", "default": ""},
                    "project_type": {"type": "string", "description": "Type: 'frontend' ou 'fullstack'", "default": "frontend"},
                    "output_dir": {"type": "string", "description": "Dossier de sortie (optionnel)", "default": ""},
                },
                "required": ["description"],
            },
            handler=generate_website_handler,
            category="website",
            source_module="handlers.website",
        ),
        HandlerDef(
            name="write_website_files",
            description=(
                "Écrit les fichiers d'un site web sur disque à partir du JSON généré par le LLM. "
                "Appeler après generate_website quand le code JSON est prêt."
            ),
            parameters={
                "properties": {
                    "json_data": {"type": "string", "description": "Le JSON contenant les fichiers du site ({files: {path: content}})"},
                    "output_dir": {"type": "string", "description": "Dossier de sortie (optionnel)", "default": ""},
                    "project_name": {"type": "string", "description": "Nom du projet (optionnel)", "default": ""},
                },
                "required": ["json_data"],
            },
            handler=write_website_files_handler,
            category="website",
            source_module="handlers.website",
        ),
        HandlerDef(
            name="serve_website",
            description="Lance un serveur HTTP local pour prévisualiser un site web généré. Ouvre le site dans le navigateur.",
            parameters={
                "properties": {
                    "project_name": {"type": "string", "description": "Nom du projet à servir", "default": ""},
                    "port": {"type": "integer", "description": "Port HTTP (défaut: 8080)", "default": 8080},
                    "directory": {"type": "string", "description": "Dossier à servir (alternatif)", "default": ""},
                },
                "required": [],
            },
            handler=serve_website_handler,
            category="website",
            source_module="handlers.website",
        ),
        HandlerDef(
            name="stop_website_server",
            description="Arrête le serveur de preview du site web.",
            parameters={"properties": {}, "required": []},
            handler=stop_website_server_handler,
            category="website",
            source_module="handlers.website",
        ),
        HandlerDef(
            name="edit_website",
            description=(
                "Modifie chirurgicalement un site web existant. "
                "Charge les fichiers du projet et prépare les instructions de modification. "
                "Utilise pour: ajouter une page, modifier le style, corriger un bug."
            ),
            parameters={
                "properties": {
                    "modifications": {"type": "string", "description": "Description des modifications (ex: 'ajoute une section témoignages')"},
                    "project_name": {"type": "string", "description": "Nom du projet à modifier", "default": ""},
                    "directory": {"type": "string", "description": "Dossier du projet (alternatif)", "default": ""},
                },
                "required": ["modifications"],
            },
            handler=edit_website_handler,
            category="website",
            source_module="handlers.website",
        ),
        HandlerDef(
            name="export_website_zip",
            description=(
                "Exporte un projet web en fichier ZIP avec progression en temps réel. "
                "Affiche la barre de progression et les statistiques du ZIP."
            ),
            parameters={
                "properties": {
                    "project_name": {"type": "string", "description": "Nom du projet à exporter", "default": ""},
                    "directory": {"type": "string", "description": "Dossier du projet (alternatif)", "default": ""},
                    "output_path": {"type": "string", "description": "Chemin de sortie du ZIP (optionnel)", "default": ""},
                },
                "required": [],
            },
            handler=export_website_zip_handler,
            category="website",
            source_module="handlers.website",
        ),
        HandlerDef(
            name="list_website_projects",
            description="Liste tous les projets web dans le workspace Lumena avec leurs stats.",
            parameters={"properties": {}, "required": []},
            handler=list_website_projects_handler,
            category="website",
            source_module="handlers.website",
        ),
        HandlerDef(
            name="check_web_project",
            description=(
                "Analyse et débugge un projet web existant : "
                "validation statique croisée (type=module manquant, sélecteurs JS orphelins, CSS dupliqués…), "
                "vérification syntaxique JS via node --check, détection de fichiers référencés manquants. "
                "Utiliser après avoir généré ou modifié un site pour s'assurer qu'il est correct."
            ),
            parameters={
                "properties": {
                    "project_dir": {
                        "type": "string",
                        "description": "Chemin vers le répertoire du projet (ex: 'workspace/2026-03-26/mon-site/mon-site'). Optionnel : analyse le dernier projet si omis.",
                        "default": "",
                    },
                },
                "required": [],
            },
            handler=check_web_project_handler,
            category="website",
            source_module="handlers.website",
        ),
    ]
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the Apache License, Version 2.0
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
