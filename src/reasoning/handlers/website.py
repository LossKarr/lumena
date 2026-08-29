"""
website.py — Handlers V2 pour les outils Website Builder.

7 outils: generate_website, write_website_files, serve_website,
          stop_website_server, edit_website, export_website_zip,
          list_website_projects

Wrappers V2 autour des handlers de src.tools.website_builder.
Pattern: async (ctx: HandlerContext, **kwargs) -> HandlerResult
"""

from __future__ import annotations

from pathlib import Path
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
    # LOT 2.0 (run MotDuJour 2026-07-06) : défaut 8080 = port RÉSERVÉ Lumena
    # (E.0/E.1b) → un appel sans port explicite était refusé d'office. 8081 =
    # premier port de la plage preview autorisée.
    port: int = 8081,
    directory: str = "",
) -> HandlerResult:
    if not WEBSITE_BUILDER_AVAILABLE:
        return HandlerResult.fail("❌ Module website_builder non disponible", handler_name="serve_website")
    try:
        # NB : l'enregistrement de la preview (SSRF) vit au boundary authoritatif
        # du serveur — website_builder.start_preview_server / stop_preview_server
        # (port RÉELLEMENT lié) — pas ici, pour couvrir tous les appelants et éviter
        # un registry stale. Cf. P1.
        result = await _serve_website(
            project_name=project_name,
            port=port,
            directory=directory,
        )
        return HandlerResult.ok(result, handler_name="serve_website")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur serve_website: {e}", handler_name="serve_website")


async def start_preview_server_handler(
    ctx: HandlerContext,
    directory: str = "",
    port: int = 8081,
    project_name: str = "",
) -> HandlerResult:
    """LOT 2.0 (run MotDuJour 2026-07-06) — ALIAS RÉEL de serve_website.

    Toute la guidance (sanitizer, steers, BROWSER GATE) nommait
    `start_preview_server`… qui n'était qu'une fonction interne de
    website_builder — PAS un outil. Le lead a cherché l'outil fantôme, dit
    2× « pas dans ma liste », écrit un run.py de contournement puis fabriqué
    le récapitulatif. Quel que soit le nom que le modèle cherche, il existe."""
    return await serve_website_handler(
        ctx, project_name=project_name, port=port, directory=directory,
    )


async def stop_website_server_handler(ctx: HandlerContext) -> HandlerResult:
    if not WEBSITE_BUILDER_AVAILABLE:
        return HandlerResult.fail("❌ Module website_builder non disponible", handler_name="stop_website_server")
    try:
        result = await _stop_server()
        return HandlerResult.ok(result, handler_name="stop_website_server")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur stop_website_server: {e}", handler_name="stop_website_server")


def _detect_target_html_files(target: "Path", modifications: str) -> list[str]:
    """Détecte quel(s) fichier(s) HTML contiennent les sélecteurs/IDs/classes mentionnés
    dans `modifications`. Retourne la liste des noms relatifs (ex: ["documentation.html"]).

    Heuristique :
    1. Extraire les IDs (#xxx) et classes (.xxx) des modifications.
    2. Extraire les noms de fichiers HTML littéralement mentionnés (foo.html).
    3. Pour chaque .html du projet, compter les matches de ces tokens.
    4. Retourner les fichiers triés par score décroissant (>=1 match).
    """
    import re
    from pathlib import Path

    # Fichiers littéralement mentionnés dans modifications (prioritaires)
    literal_files = set()
    for m in re.finditer(r"([\w./\\-]+\.html)\b", modifications, re.IGNORECASE):
        name = m.group(1).replace("\\", "/").split("/")[-1]
        literal_files.add(name.lower())

    # Tokens IDs/classes (minimum 3 chars pour éviter bruit)
    tokens = set()
    for m in re.finditer(r"[#.]([A-Za-z][\w-]{2,})", modifications):
        tokens.add(m.group(1))

    scores: dict[str, int] = {}
    for f in target.rglob("*.html"):
        if not f.is_file():
            continue
        rel = str(f.relative_to(target)).replace("\\", "/")
        name_lower = f.name.lower()
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        score = 0
        # Fichier littéralement mentionné → score très élevé
        if name_lower in literal_files:
            score += 1000
        # Compter tokens présents
        for tok in tokens:
            if f'id="{tok}"' in text or f"id='{tok}'" in text:
                score += 10
            if f'class="{tok}"' in text or f'class="' in text and tok in text:
                # Vérif plus stricte pour class
                if re.search(rf'class\s*=\s*["\'][^"\']*\b{re.escape(tok)}\b', text):
                    score += 5
        if score > 0:
            scores[rel] = score

    if not scores:
        return []
    # Tri décroissant
    return [name for name, _ in sorted(scores.items(), key=lambda x: x[1], reverse=True)]


def _read_project_files(target: "Path", max_total_chars: int = 16000) -> str:
    """Lit tous les fichiers HTML/CSS/JS du projet et retourne leur contenu formaté."""
    _WEB_SUFFIXES = {".html", ".css", ".js", ".json", ".svg"}
    files_content: list[str] = []
    total_chars = 0

    for f in sorted(target.rglob("*")):
        if not f.is_file():
            continue
        if f.suffix.lower() not in _WEB_SUFFIXES:
            continue
        # Skip backups et node_modules
        rel = f.relative_to(target)
        rel_str = str(rel).replace("\\", "/")
        if any(part.startswith(".") or part == "node_modules" for part in rel.parts):
            continue
        try:
            content = f.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if total_chars + len(content) > max_total_chars:
            files_content.append(
                f"\n--- {rel_str} (TRONQUÉ — {len(content)} chars) ---\n"
                f"{content[:2000]}\n[... tronqué ...]\n"
            )
            total_chars += 2000
            break
        files_content.append(f"\n--- {rel_str} ({len(content)} chars) ---\n{content}")
        total_chars += len(content)

    return "".join(files_content) if files_content else "(aucun fichier trouvé)"


# Contexte factuel sur Lumena — injecté pour que le CodeAgent
# génère du contenu cohérent quand il modifie le site de Lumena elle-même.
_LUMENA_SELF_CONTEXT = """
## Contexte factuel sur Lumena (à utiliser si le contenu du site parle de Lumena)
- Lumena est une IA autonome open-source écrite en **Python 3.12** avec **FastAPI**
- Stack : Python, FastAPI, ChromaDB, Playwright, Docker, SFTP/SSH
- LLM : DeepSeek V3 (principal), GPT-5.4, Claude Opus, Gemini, Ollama local
- 466 outils natifs (create_pdf, web_search, mail_send, execute_python, deploy_to_ionos, etc.)
- Canaux : Web UI, Telegram, Discord, Twitter/X, WhatsApp
- Mémoire : ChromaDB vectorielle + BM25, persistante entre sessions
- Architecture : src/ (agents, autonomy, computer_use, context, core_services, hooks, learning, llm, memory, perception, reasoning, runtime, services, skills, tools, voice, web)
- 29 skills programmables, système de hooks, scheduler CRON, daemon autonome
- Tourne 100% en local sur la machine de l'utilisateur (pas de cloud obligatoire)
- Déploiement sites web : SFTP vers IONOS (openlumena.com)
- NE PAS utiliser : npm, Node.js, config.json, localhost:3000 — ce sont des erreurs
"""


async def _edit_website_via_codeagent(
    ctx: HandlerContext,
    modifications: str,
    target: "Path",
    logger,
) -> HandlerResult:
    """Délègue la modification de site web au CodeAgent itératif.

    Pré-lit TOUS les fichiers du projet et les injecte dans la description,
    pour que le CodeAgent agisse comme un éditeur (pas un explorateur).
    """
    try:
        from ...agents.sub_agent import get_orchestrator, AgentType

        # Pré-lire tous les fichiers du projet
        files_dump = _read_project_files(target)
        logger.info(
            "[edit_website] Pré-lecture projet: {} chars injectés dans la description CodeAgent",
            len(files_dump),
        )

        # Détecter le(s) fichier(s) HTML cible(s) à partir des sélecteurs/mentions
        target_files = _detect_target_html_files(target, modifications)
        if target_files:
            primary = target_files[0]
            logger.info(
                "[edit_website] Fichier(s) HTML cible détecté(s): {} (primaire: {})",
                target_files, primary,
            )
            target_hint = (
                f"\n## 🎯 FICHIER CIBLE PRINCIPAL : `{primary}`\n"
                f"Les sélecteurs/IDs/classes présents dans les modifications "
                f"correspondent à ce fichier. **Édite `{primary}` en priorité**, "
                f"PAS les autres fichiers HTML.\n"
            )
            if len(target_files) > 1:
                target_hint += f"(Fichiers secondaires potentiellement impactés : {', '.join(target_files[1:])})\n"
        else:
            target_hint = ""

        description = (
            f"Modifie le site web dans le dossier '{target}'.\n"
            f"{target_hint}\n"
            f"## Modifications demandées\n{modifications}\n\n"
            f"## Fichiers ACTUELS du projet (contenu complet)\n"
            f"Utilise TOUJOURS les chemins ABSOLUS (ex: '{target}/index.html') "
            f"pour str_replace ou write_file.\n"
            f"{files_dump}\n\n"
            f"{_LUMENA_SELF_CONTEXT}\n"
            "## Instructions STRICTES\n"
            "1. Tu as DÉJÀ le contenu complet de tous les fichiers ci-dessus. "
            "**NE FAIS AUCUN `read_file` ni `list_files`** — c'est une perte de temps.\n"
            "2. Commence DIRECTEMENT par `str_replace` ou `edit_lines` sur le FICHIER CIBLE indiqué ci-dessus.\n"
            "3. Si un sélecteur CSS n'existe pas dans le fichier cible, crée-le avec la structure demandée.\n"
            "4. Vérifie la cohérence HTML↔CSS↔JS après modification.\n"
            "5. Quand tu as fini, utilise ACTION: done.\n"
            "⚠️ Toute action `read_file` sur un fichier déjà dumpé ci-dessus sera considérée "
            "comme une erreur et pénalisée."
        )
        context = {
            "project_dir": str(target),
            "workspace_path": str(target),
        }
        orchestrator = get_orchestrator()
        agent_result = await orchestrator.run_task_sync(description, AgentType.CODE, context)
        if not agent_result.success:
            logger.warning("[edit_website] CodeAgent échoué: {}", agent_result.output[:200])
            return HandlerResult.fail(
                f"❌ CodeAgent n'a pas pu modifier le site.\n\n{agent_result.output}",
                handler_name="edit_website",
            )
        return HandlerResult.ok(
            f"✅ Modifications appliquées via CodeAgent !\n\n"
            f"📂 Projet: {target.name}\n"
            f"📁 Dossier: {target}\n\n"
            f"{agent_result.output}",
            handler_name="edit_website",
        )
    except Exception as e:
        logger.error("[edit_website] CodeAgent fallback échoué: {}", e)
        return HandlerResult.fail(f"❌ Erreur edit_website (CodeAgent): {e}", handler_name="edit_website")


async def edit_website_handler(
    ctx: HandlerContext,
    modifications="",
    project_name: str = "",
    directory: str = "",
) -> HandlerResult:
    if not WEBSITE_BUILDER_AVAILABLE:
        return HandlerResult.fail("❌ Module website_builder non disponible", handler_name="edit_website")
    try:
        from loguru import logger
        from pathlib import Path
        import json as _json

        # Fix: le LLM envoie souvent `modifications` en liste/dict structuré
        # (format naturel pour des hunks multi-fichiers). On sérialise en texte
        # pour que le CodeAgent (qui attend une string) puisse l'exploiter.
        if isinstance(modifications, (list, dict)):
            try:
                modifications = _json.dumps(modifications, ensure_ascii=False, indent=2)
            except Exception:
                modifications = str(modifications)
        elif modifications is None:
            modifications = ""
        elif not isinstance(modifications, str):
            modifications = str(modifications)

        # Résoudre le répertoire cible
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

        # Déléguer directement au CodeAgent itératif (50 iter, read→edit→validate)
        logger.info("[edit_website] Délégation directe au CodeAgent pour: {}", modifications[:80])
        return await _edit_website_via_codeagent(ctx, modifications, target, logger)

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
    from ...utils.paths import WORKSPACE_DIR
    if project_dir:
        if Path(project_dir).is_absolute():
            base = Path(project_dir)
        else:
            # Essayer d'abord sous lumena_root, puis sous WORKSPACE_DIR
            base = Path(ctx.lumena_root) / project_dir
            if not base.exists():
                ws_candidate = WORKSPACE_DIR / project_dir
                if ws_candidate.exists():
                    base = ws_candidate
    else:
        # Chercher le dernier projet web généré dans workspace/
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
                    logger.debug("[website] read file {}: {}", rel, e)

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


def _prefer_web_project_root(candidate: Path) -> Path:
    """Promote templates/ or another child back to its Flask project root."""
    resolved = Path(candidate).resolve()
    for base in (resolved, *list(resolved.parents)[:3]):
        if not (base / "app.py").is_file():
            continue
        if (base / "templates" / "index.html").is_file() or (base / "index.html").is_file():
            return base
    return resolved


def _resolve_web_project_dir(ctx: HandlerContext, project_dir: str = "", project_path: str = "") -> Path:
    """Resolve a web project directory from an absolute path or workspace-relative name."""
    from ...utils.paths import WORKSPACE_DIR

    raw = (project_path or project_dir or "").strip()
    if raw:
        candidate = Path(raw)
        if candidate.is_absolute():
            return _prefer_web_project_root(candidate)
        base = Path(ctx.lumena_root) / raw
        if base.exists():
            return _prefer_web_project_root(base)
        ws_candidate = WORKSPACE_DIR / raw
        if ws_candidate.exists():
            return _prefer_web_project_root(ws_candidate)
        return base

    mission_subdir_fn = getattr(ctx, "mission_workspace_subdir", None)
    mission_subdir = str(mission_subdir_fn() or "") if callable(mission_subdir_fn) else ""
    if mission_subdir:
        mission_candidate = (WORKSPACE_DIR / mission_subdir).resolve()
        if mission_candidate.is_dir():
            return _prefer_web_project_root(mission_candidate)

    ws = WORKSPACE_DIR
    candidates = sorted(
        [d for d in ws.rglob("index.html") if d.is_file()],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if candidates:
        return _prefer_web_project_root(candidates[0].parent)
    return ws


async def browser_verify_local_project_handler(
    ctx: HandlerContext,
    project_dir: str = "",
    project_path: str = "",
    entry: str = "index.html",
    expect_canvas: bool = False,
    max_clicks: int = 3,
    timeout_ms: int = 30000,
) -> HandlerResult:
    """Verify a local web project in Playwright and return runtime proof."""
    try:
        base = _resolve_web_project_dir(ctx, project_dir=project_dir, project_path=project_path)
        if not base.exists():
            return HandlerResult.fail(
                f"❌ Répertoire non trouvé: {base}",
                handler_name="browser_verify_local_project",
                status_code="project_not_found",
            )

        from ...tools.web_project_runtime_verifier import verify_web_project_runtime

        result = await verify_web_project_runtime(
            base,
            entry=entry,
            expect_canvas=bool(expect_canvas),
            max_clicks=int(max_clicks or 0),
            timeout_ms=int(timeout_ms or 30000),
        )
        report = result.to_report()
        if result.passed:
            return HandlerResult.ok(
                "✅ Vérification navigateur autonome OK\n\n" + report,
                handler_name="browser_verify_local_project",
                status_code="runtime_ok",
            )
        return HandlerResult.fail(
            "❌ Vérification navigateur autonome échouée\n\n" + report,
            handler_name="browser_verify_local_project",
            status_code="runtime_failed",
        )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur browser_verify_local_project: {e}",
            handler_name="browser_verify_local_project",
            status_code="runtime_error",
        )


def get_website_handler_defs() -> List[HandlerDef]:
    """Retourne les définitions de handlers website."""
    return [
        HandlerDef(
            name="generate_website",
            description=(
                "Génère un NOUVEAU site web complet (frontend + backend + API) à partir d'une description. "
                "Crée la structure du projet, les fichiers HTML/CSS/JS, et optionnellement le backend PHP/Node + SQL. "
                "Utilise UNIQUEMENT pour créer un site from scratch. "
                "⚠️ Pour MODIFIER/AMÉLIORER un site existant, utiliser edit_website à la place."
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
            description=(
                "Lance un serveur local pour prévisualiser un site/app web et "
                "ENREGISTRE le port au registre de preview (browser_navigate autorisé "
                "ensuite). 2.5 : si le dossier contient app.py avec create_app/Flask, "
                "lance l'APP FLASK (les routes /api/* tournent) ; sinon sert en statique. "
                "Ex: serve_website(directory='workspace/monapp', port=8081). "
                "Ports autorisés: 8081-8099."
            ),
            parameters={
                "properties": {
                    "project_name": {"type": "string", "description": "Nom du projet à servir", "default": ""},
                    "port": {"type": "integer", "description": "Port HTTP 8081-8099 (défaut: 8081)", "default": 8081},
                    "directory": {"type": "string", "description": "Dossier à servir (alternatif)", "default": ""},
                },
                "required": [],
            },
            handler=serve_website_handler,
            category="website",
            source_module="handlers.website",
        ),
        HandlerDef(
            name="start_preview_server",
            description=(
                "Alias de serve_website : sert un dossier web local sur un port de "
                "preview enregistré (SSRF ok pour browser_navigate). "
                "Ex: start_preview_server(directory='workspace/monapp', port=8081)."
            ),
            parameters={
                "properties": {
                    "directory": {"type": "string", "description": "Dossier à servir", "default": ""},
                    "port": {"type": "integer", "description": "Port HTTP 8081-8099 (défaut: 8081)", "default": 8081},
                    "project_name": {"type": "string", "description": "Nom du projet (alternatif)", "default": ""},
                },
                "required": [],
            },
            handler=start_preview_server_handler,
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
                "Modifie chirurgicalement un site web existant (diff-based, pas de recréation). "
                "Charge les fichiers du projet et applique les modifications ciblées. "
                "Utilise pour: améliorer un site, ajouter une section, modifier le contenu/style, corriger un bug, mettre à jour un site déjà déployé."
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
        HandlerDef(
            name="browser_verify_local_project",
            description=(
                "Pour Flask, passe la RACINE contenant app.py, jamais templates/. En mission, "
                "le dossier courant est utilise si aucun chemin n'est fourni. Ne passe pas de "
                "parametres hors schema. "
                "Vérifie automatiquement un projet web local dans Playwright après génération/modification : "
                "serveur local, navigation localhost, erreurs console/page, DOM visible, screenshots, scroll, "
                "interactions basiques et canvas si attendu. À utiliser avant de dire qu'un site/app/jeu web est terminé."
            ),
            parameters={
                "properties": {
                    "project_dir": {
                        "type": "string",
                        "description": "Chemin ou nom du dossier projet. Optionnel si project_path est fourni.",
                        "default": "",
                    },
                    "project_path": {
                        "type": "string",
                        "description": "Chemin absolu du projet web à vérifier.",
                        "default": "",
                    },
                    "entry": {
                        "type": "string",
                        "description": "Fichier d'entrée à ouvrir (défaut index.html).",
                        "default": "index.html",
                    },
                    "expect_canvas": {
                        "type": "boolean",
                        "description": "True pour les jeux/canvas/Three.js où un canvas est attendu.",
                        "default": False,
                    },
                    "max_clicks": {
                        "type": "integer",
                        "description": "Nombre maximal de clics prudents pendant la vérification.",
                        "default": 3,
                    },
                    "timeout_ms": {
                        "type": "integer",
                        "description": "Timeout de chargement navigateur.",
                        "default": 30000,
                    },
                },
                "required": [],
            },
            handler=browser_verify_local_project_handler,
            category="website",
            source_module="handlers.website",
        ),
    ]
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
