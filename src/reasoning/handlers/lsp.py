"""
lsp.py — Handlers LSP pour la boucle ReAct.

Expose les capacités LSP comme outils que Lumena peut appeler
pendant son raisonnement : diagnostics, go-to-definition,
find-references, hover/type-info, check_project.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from .context import HandlerContext
from .contracts import HandlerResult
from .registry_v2 import HandlerDef

logger = logging.getLogger("lumena.handlers.lsp")

try:
    from ...tools.lsp_client import (
        LSPClient,
        LSPDiagnostic,
        DiagnosticSeverity,
        detect_available_servers,
        lsp_check_project,
        get_install_instructions,
        KNOWN_SERVERS,
    )
    _LSP_AVAILABLE = True
except Exception:
    _LSP_AVAILABLE = False

# ── Helpers ──────────────────────────────────────────────────────

_SEV_LABELS = {1: "ERROR", 2: "WARNING", 3: "INFO", 4: "HINT"}


def _format_diagnostics(diags: List[Any], limit: int = 80) -> str:
    """Formatte une liste de LSPDiagnostic en texte lisible."""
    if not diags:
        return "Aucun diagnostic trouvé."

    lines = [f"**{len(diags)} diagnostic(s):**\n"]
    for d in diags[:limit]:
        sev = _SEV_LABELS.get(int(d.severity), "?")
        src = f"[{d.source}]" if d.source else ""
        code = f" ({d.code})" if d.code else ""
        lines.append(f"  {sev} {d.file_path}:{d.line + 1}:{d.col + 1} — {d.message}{code} {src}")

    if len(diags) > limit:
        lines.append(f"\n  ... et {len(diags) - limit} diagnostic(s) supplémentaires.")

    errors = sum(1 for d in diags if int(d.severity) == 1)
    warnings = sum(1 for d in diags if int(d.severity) == 2)
    lines.append(f"\nRésumé: {errors} erreur(s), {warnings} warning(s), {len(diags) - errors - warnings} info/hint")
    return "\n".join(lines)


def _resolve_project_dir(ctx: HandlerContext, path: Optional[str]) -> Path:
    """Résout le répertoire projet depuis path ou le contexte IDE."""
    if path:
        return ctx.resolve_path(path, want_dir=True)
    # Utiliser le workspace IDE si disponible
    ws = ctx.ide_context.get("workspace_path")
    if ws:
        return Path(ws)
    return ctx.runtime_root


def _pick_server(file_path: str) -> Optional[str]:
    """Détermine le meilleur serveur LSP pour un fichier donné."""
    if not _LSP_AVAILABLE:
        return None
    ext = Path(file_path).suffix.lower()
    for name, cfg in KNOWN_SERVERS.items():
        if ext in cfg.extensions:
            return name
    return None


# ── Handlers ─────────────────────────────────────────────────────

async def lsp_check_handler(ctx: HandlerContext, **kwargs: Any) -> HandlerResult:
    """Lance les diagnostics LSP sur un projet ou des fichiers spécifiques."""
    if not _LSP_AVAILABLE:
        return HandlerResult.fail("LSP client non disponible (import manquant).", handler_name="lsp_check")

    project_dir = _resolve_project_dir(ctx, kwargs.get("project_dir"))
    files = kwargs.get("files")
    timeout = min(float(kwargs.get("timeout", 20)), 60)

    try:
        diags = await lsp_check_project(project_dir, files=files, timeout=timeout)
        return HandlerResult.ok(_format_diagnostics(diags), handler_name="lsp_check")
    except Exception as e:
        return HandlerResult.fail(f"Erreur lors du check LSP: {e}", handler_name="lsp_check")


async def lsp_diagnostics_handler(ctx: HandlerContext, **kwargs: Any) -> HandlerResult:
    """Récupère les diagnostics LSP pour un fichier spécifique."""
    if not _LSP_AVAILABLE:
        return HandlerResult.fail("LSP client non disponible.", handler_name="lsp_diagnostics")

    file_path: str = kwargs.get("path", "")
    if not file_path:
        return HandlerResult.fail("Paramètre 'path' requis.", handler_name="lsp_diagnostics")

    resolved = str(ctx.resolve_path(file_path))
    server = _pick_server(resolved)
    if not server:
        return HandlerResult.fail(
            f"Aucun serveur LSP pour l'extension '{Path(resolved).suffix}'.",
            handler_name="lsp_diagnostics",
        )

    available = detect_available_servers()
    if server not in available:
        return HandlerResult.fail(
            f"Serveur '{server}' non installé. {KNOWN_SERVERS[server].install_hint}",
            handler_name="lsp_diagnostics",
        )

    project_dir = _resolve_project_dir(ctx, kwargs.get("project_dir"))
    try:
        async with LSPClient(server, project_dir) as lsp:
            diags = await lsp.get_diagnostics([resolved], timeout=15.0)
        return HandlerResult.ok(_format_diagnostics(diags), handler_name="lsp_diagnostics")
    except Exception as e:
        return HandlerResult.fail(f"Erreur LSP diagnostics: {e}", handler_name="lsp_diagnostics")


async def lsp_goto_definition_handler(ctx: HandlerContext, **kwargs: Any) -> HandlerResult:
    """Go-to-definition via LSP pour un symbole à une position donnée."""
    if not _LSP_AVAILABLE:
        return HandlerResult.fail("LSP client non disponible.", handler_name="lsp_goto_definition")

    file_path: str = kwargs.get("path", "")
    line: int = int(kwargs.get("line", 0))
    col: int = int(kwargs.get("col", 0))

    if not file_path:
        return HandlerResult.fail("Paramètre 'path' requis.", handler_name="lsp_goto_definition")

    resolved = str(ctx.resolve_path(file_path))
    server = _pick_server(resolved)
    if not server:
        return HandlerResult.fail(
            f"Aucun serveur LSP pour '{Path(resolved).suffix}'.",
            handler_name="lsp_goto_definition",
        )

    available = detect_available_servers()
    if server not in available:
        return HandlerResult.fail(
            f"Serveur '{server}' non installé.",
            handler_name="lsp_goto_definition",
        )

    project_dir = _resolve_project_dir(ctx, kwargs.get("project_dir"))
    try:
        async with LSPClient(server, project_dir) as lsp:
            symbols = await lsp.get_definitions(resolved, line, col)

        if not symbols:
            return HandlerResult.ok(
                f"Aucune définition trouvée pour la position {file_path}:{line + 1}:{col + 1}.",
                handler_name="lsp_goto_definition",
            )

        lines = [f"**{len(symbols)} définition(s) trouvée(s):**\n"]
        for s in symbols:
            name_part = f" ({s.name})" if s.name else ""
            lines.append(f"  → {s.file_path}:{s.line + 1}:{s.col + 1}{name_part}")
        return HandlerResult.ok("\n".join(lines), handler_name="lsp_goto_definition")
    except Exception as e:
        return HandlerResult.fail(f"Erreur go-to-definition: {e}", handler_name="lsp_goto_definition")


async def lsp_find_references_handler(ctx: HandlerContext, **kwargs: Any) -> HandlerResult:
    """Find-references via LSP pour un symbole."""
    if not _LSP_AVAILABLE:
        return HandlerResult.fail("LSP client non disponible.", handler_name="lsp_find_references")

    file_path: str = kwargs.get("path", "")
    line: int = int(kwargs.get("line", 0))
    col: int = int(kwargs.get("col", 0))

    if not file_path:
        return HandlerResult.fail("Paramètre 'path' requis.", handler_name="lsp_find_references")

    resolved = str(ctx.resolve_path(file_path))
    server = _pick_server(resolved)
    if not server:
        return HandlerResult.fail(
            f"Aucun serveur LSP pour '{Path(resolved).suffix}'.",
            handler_name="lsp_find_references",
        )

    available = detect_available_servers()
    if server not in available:
        return HandlerResult.fail(
            f"Serveur '{server}' non installé.",
            handler_name="lsp_find_references",
        )

    project_dir = _resolve_project_dir(ctx, kwargs.get("project_dir"))
    try:
        async with LSPClient(server, project_dir) as lsp:
            refs = await lsp.get_references(resolved, line, col)

        if not refs:
            return HandlerResult.ok(
                f"Aucune référence trouvée pour {file_path}:{line + 1}:{col + 1}.",
                handler_name="lsp_find_references",
            )

        lines = [f"**{len(refs)} référence(s):**\n"]
        for r in refs:
            lines.append(f"  • {r.file_path}:{r.line + 1}:{r.col + 1}")
        if len(refs) > 50:
            lines.append(f"\n  (affichage limité aux 50 premières)")
        return HandlerResult.ok("\n".join(lines[:52]), handler_name="lsp_find_references")
    except Exception as e:
        return HandlerResult.fail(f"Erreur find-references: {e}", handler_name="lsp_find_references")


async def lsp_hover_handler(ctx: HandlerContext, **kwargs: Any) -> HandlerResult:
    """Récupère le type / docstring d'un symbole via LSP hover."""
    if not _LSP_AVAILABLE:
        return HandlerResult.fail("LSP client non disponible.", handler_name="lsp_hover")

    file_path: str = kwargs.get("path", "")
    line: int = int(kwargs.get("line", 0))
    col: int = int(kwargs.get("col", 0))

    if not file_path:
        return HandlerResult.fail("Paramètre 'path' requis.", handler_name="lsp_hover")

    resolved = str(ctx.resolve_path(file_path))
    server = _pick_server(resolved)
    if not server:
        return HandlerResult.fail(
            f"Aucun serveur LSP pour '{Path(resolved).suffix}'.",
            handler_name="lsp_hover",
        )

    available = detect_available_servers()
    if server not in available:
        return HandlerResult.fail(
            f"Serveur '{server}' non installé.",
            handler_name="lsp_hover",
        )

    project_dir = _resolve_project_dir(ctx, kwargs.get("project_dir"))
    try:
        async with LSPClient(server, project_dir) as lsp:
            info = await lsp.get_hover(resolved, line, col)

        if not info:
            return HandlerResult.ok(
                f"Pas d'info hover pour {file_path}:{line + 1}:{col + 1}.",
                handler_name="lsp_hover",
            )

        parts = []
        if info.language:
            parts.append(f"**Langage:** {info.language}")
        parts.append(info.contents)
        return HandlerResult.ok("\n".join(parts), handler_name="lsp_hover")
    except Exception as e:
        return HandlerResult.fail(f"Erreur hover: {e}", handler_name="lsp_hover")


async def lsp_servers_handler(ctx: HandlerContext, **kwargs: Any) -> HandlerResult:
    """Liste les serveurs LSP disponibles et leur statut."""
    if not _LSP_AVAILABLE:
        return HandlerResult.fail("LSP client non disponible.", handler_name="lsp_servers")

    try:
        report = get_install_instructions()
        return HandlerResult.ok(report, handler_name="lsp_servers")
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}", handler_name="lsp_servers")


# ── Registration ─────────────────────────────────────────────────

def get_lsp_handler_defs() -> List[HandlerDef]:
    """Retourne toutes les définitions de handlers LSP pour le registre V2."""
    return [
        HandlerDef(
            name="lsp_check",
            description=(
                "Analyse un projet avec les language servers LSP installes (pyright, typescript, css, html, json). "
                "Retourne les erreurs de compilation, erreurs de type, imports manquants, variables non definies, etc. "
                "Utilise cet outil apres avoir ecrit du code pour verifier qu'il compile et n'a pas d'erreurs."
            ),
            parameters={
                "properties": {
                    "project_dir": {
                        "type": "string",
                        "description": "Repertoire du projet a analyser (defaut: workspace courant).",
                    },
                    "files": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Liste de chemins relatifs a verifier. Ex: ['src/main.py']. Defaut: auto-decouverte.",
                    },
                    "timeout": {
                        "type": "number",
                        "description": "Timeout en secondes (defaut 20, max 60).",
                        "default": 20,
                    },
                },
                "required": [],
            },
            handler=lsp_check_handler,
            category="lsp",
            source_module="handlers.lsp",
        ),
        HandlerDef(
            name="lsp_diagnostics",
            description=(
                "Recupere les diagnostics LSP (erreurs, warnings, types) pour un fichier specifique. "
                "Plus rapide que lsp_check car ne lance qu'un seul serveur."
            ),
            parameters={
                "properties": {
                    "path": {"type": "string", "description": "Chemin du fichier a analyser."},
                    "project_dir": {
                        "type": "string",
                        "description": "Repertoire du projet (defaut: workspace courant).",
                    },
                },
                "required": ["path"],
            },
            handler=lsp_diagnostics_handler,
            category="lsp",
            source_module="handlers.lsp",
        ),
        HandlerDef(
            name="lsp_goto_definition",
            description=(
                "Go-to-definition: trouve ou un symbole (fonction, classe, variable) est defini. "
                "Utilise les positions 0-based (premiere ligne = 0, premiere colonne = 0)."
            ),
            parameters={
                "properties": {
                    "path": {"type": "string", "description": "Chemin du fichier."},
                    "line": {"type": "integer", "description": "Ligne (0-based)."},
                    "col": {"type": "integer", "description": "Colonne (0-based)."},
                    "project_dir": {"type": "string", "description": "Repertoire projet (optionnel)."},
                },
                "required": ["path", "line", "col"],
            },
            handler=lsp_goto_definition_handler,
            category="lsp",
            source_module="handlers.lsp",
        ),
        HandlerDef(
            name="lsp_find_references",
            description=(
                "Find-references: trouve toutes les utilisations d'un symbole dans le projet. "
                "Positions 0-based."
            ),
            parameters={
                "properties": {
                    "path": {"type": "string", "description": "Chemin du fichier."},
                    "line": {"type": "integer", "description": "Ligne (0-based)."},
                    "col": {"type": "integer", "description": "Colonne (0-based)."},
                    "project_dir": {"type": "string", "description": "Repertoire projet (optionnel)."},
                },
                "required": ["path", "line", "col"],
            },
            handler=lsp_find_references_handler,
            category="lsp",
            source_module="handlers.lsp",
        ),
        HandlerDef(
            name="lsp_hover",
            description=(
                "Recupere le type et la documentation d'un symbole a une position donnee "
                "(equivalent du hover dans un IDE). Positions 0-based."
            ),
            parameters={
                "properties": {
                    "path": {"type": "string", "description": "Chemin du fichier."},
                    "line": {"type": "integer", "description": "Ligne (0-based)."},
                    "col": {"type": "integer", "description": "Colonne (0-based)."},
                    "project_dir": {"type": "string", "description": "Repertoire projet (optionnel)."},
                },
                "required": ["path", "line", "col"],
            },
            handler=lsp_hover_handler,
            category="lsp",
            source_module="handlers.lsp",
        ),
        HandlerDef(
            name="lsp_servers",
            description="Liste les language servers LSP disponibles (installes et manquants) avec instructions d'installation.",
            parameters={"properties": {}, "required": []},
            handler=lsp_servers_handler,
            category="lsp",
            source_module="handlers.lsp",
        ),
    ]
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
