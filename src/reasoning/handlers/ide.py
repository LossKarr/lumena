"""
ide.py — Handlers IDE pour la boucle ReAct.

Expose le controle de l'IDE Lumena (Electron) comme outils que
Lumena peut appeler pendant son raisonnement : ouvrir un fichier,
editer, naviguer, executer une commande terminal, voir un diff.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from .context import HandlerContext
from .contracts import HandlerResult
from .registry_v2 import HandlerDef

logger = logging.getLogger("lumena.handlers.ide")


def _get_bridge():
    from ...tools.ide_bridge import get_ide_bridge
    return get_ide_bridge()


def _resolve_path(path: str) -> str:
    """Resout un chemin relatif en absolu base sur la racine Lumena."""
    import os
    p = Path(path)
    if p.is_absolute():
        return str(p)
    # Racine Lumena = 3 niveaux au-dessus de ce fichier (src/reasoning/handlers/ide.py)
    lumena_root = Path(__file__).parent.parent.parent.parent
    resolved = (lumena_root / p).resolve()
    return str(resolved)


# ── ide_status ───────────────────────────────────────────────────

async def _handle_ide_status(ctx: HandlerContext, **kwargs) -> HandlerResult:
    """Verifie si l'IDE Lumena est connecte et retourne son etat."""
    bridge = _get_bridge()
    result = await bridge.get_status()

    if not result.get("connected"):
        return HandlerResult(
            success=True,
            output="IDE Lumena: DECONNECTE. Lancez l'IDE pour activer le controle distant.",
            handler_name="ide_status",
        )

    workspace = result.get("workspace", "aucun")
    has_terminal = result.get("hasTerminal", False)
    return HandlerResult(
        success=True,
        output=(
            f"IDE Lumena: CONNECTE\n"
            f"Workspace: {workspace}\n"
            f"Terminal actif: {'oui' if has_terminal else 'non'}"
        ),
        handler_name="ide_status",
    )


# ── ide_open_file ────────────────────────────────────────────────

async def _handle_ide_open_file(ctx: HandlerContext, **kwargs) -> HandlerResult:
    """Ouvre un fichier dans l'editeur de l'IDE."""
    path = kwargs.get("path", "").strip()
    if not path:
        return HandlerResult.fail("Parametre 'path' requis.", handler_name="ide_open_file")

    abs_path = _resolve_path(path)
    bridge = _get_bridge()
    result = await bridge.open_file(abs_path)

    if result.get("success"):
        return HandlerResult(success=True, output=f"Fichier ouvert dans l'IDE: {abs_path}", handler_name="ide_open_file")
    err = result.get("error", "Echec ouverture")
    return HandlerResult.fail(f"Echec ouverture '{abs_path}': {err}", handler_name="ide_open_file")


# ── ide_read_file ────────────────────────────────────────────────

async def _handle_ide_read_file(ctx: HandlerContext, **kwargs) -> HandlerResult:
    """Lit le contenu d'un fichier via l'IDE (sans ouvrir d'onglet)."""
    path = kwargs.get("path", "").strip()
    if not path:
        return HandlerResult(success=False, output="", error="Parametre 'path' requis.", handler_name="ide_read_file")

    bridge = _get_bridge()
    result = await bridge.read_file(path)

    if result.get("success"):
        content = result.get("content", "")
        lines = content.split("\n")
        preview = "\n".join(lines[:200])
        suffix = f"\n... ({len(lines)} lignes total)" if len(lines) > 200 else ""
        return HandlerResult(success=True, output=f"```\n{preview}{suffix}\n```", handler_name="ide_read_file")
    return HandlerResult(success=False, output="", error=result.get("error", "Echec lecture"), handler_name="ide_read_file")


# ── ide_write_file ───────────────────────────────────────────────

async def _handle_ide_write_file(ctx: HandlerContext, **kwargs) -> HandlerResult:
    """Ecrit du contenu dans un fichier et l'ouvre dans l'IDE."""
    path = kwargs.get("path", "").strip()
    content = kwargs.get("content", "")
    if not path:
        return HandlerResult(success=False, output="", error="Parametre 'path' requis.", handler_name="ide_write_file")

    bridge = _get_bridge()
    result = await bridge.write_file(path, content)

    if result.get("success"):
        return HandlerResult(success=True, output=f"Fichier ecrit et ouvert: {path} ({len(content)} chars)", handler_name="ide_write_file")
    return HandlerResult(success=False, output="", error=result.get("error", "Echec ecriture"), handler_name="ide_write_file")


# ── ide_terminal ─────────────────────────────────────────────────

async def _handle_ide_terminal(ctx: HandlerContext, **kwargs) -> HandlerResult:
    """Execute une commande dans le terminal integre de l'IDE."""
    command = kwargs.get("command", "").strip()
    if not command:
        return HandlerResult(success=False, output="", error="Parametre 'command' requis.", handler_name="ide_terminal")

    bridge = _get_bridge()
    result = await bridge.terminal_run(command)

    if result.get("success"):
        return HandlerResult(success=True, output=f"Commande envoyee au terminal IDE: {command}", handler_name="ide_terminal")
    return HandlerResult(success=False, output="", error=result.get("error", "Echec terminal"), handler_name="ide_terminal")


# ── ide_navigate ─────────────────────────────────────────────────

async def _handle_ide_navigate(ctx: HandlerContext, **kwargs) -> HandlerResult:
    """[DOSSIER] Ouvre un DOSSIER dans la sidebar de l'IDE (explorateur de fichiers/workspace). NE PAS utiliser pour des fichiers."""
    path = kwargs.get("path", "").strip()
    if not path:
        return HandlerResult.fail("Parametre 'path' requis.", handler_name="ide_navigate")

    abs_path = _resolve_path(path)
    bridge = _get_bridge()
    result = await bridge.navigate(abs_path)

    if result.get("success"):
        return HandlerResult(success=True, output=f"IDE navigue vers: {result.get('workspace', abs_path)}", handler_name="ide_navigate")
    err = result.get("error", "Echec navigation")
    return HandlerResult.fail(f"Echec navigation vers '{abs_path}': {err}", handler_name="ide_navigate")


# ── ide_open_workspace (DOSSIER + fichiers optionnels) ───────────

async def _handle_ide_open_workspace(ctx: HandlerContext, **kwargs) -> HandlerResult:
    """[DOSSIER] Ouvre un DOSSIER comme workspace dans la sidebar ET ouvre optionnellement des fichiers dans Monaco.
    C'est le handler principal a utiliser quand l'utilisateur dit 'ouvre le projet/dossier X dans l'IDE'."""
    folder = kwargs.get("path", kwargs.get("folder", "")).strip()
    files = kwargs.get("files", [])  # liste de chemins de fichiers a ouvrir en onglets
    if not folder:
        return HandlerResult.fail("Parametre 'path' (dossier) requis.", handler_name="ide_open_workspace")

    abs_folder = _resolve_path(folder)
    bridge = _get_bridge()

    # 1. Ouvrir le dossier dans la sidebar
    nav_result = await bridge.navigate(abs_folder)
    if not nav_result.get("success"):
        err = nav_result.get("error", "Echec navigation dossier")
        return HandlerResult.fail(f"Echec ouverture dossier '{abs_folder}': {err}", handler_name="ide_open_workspace")

    lines = [f"Dossier ouvert dans la sidebar: {abs_folder}"]

    # 2. Ouvrir des fichiers optionnels dans Monaco
    if isinstance(files, str):
        files = [f.strip() for f in files.split(",") if f.strip()]
    opened = []
    for file_path in (files or []):
        abs_file = _resolve_path(file_path)
        r = await bridge.open_file(abs_file)
        if r.get("success"):
            opened.append(abs_file)
    if opened:
        lines.append(f"Fichiers ouverts dans l'editeur: {', '.join(opened)}")

    return HandlerResult(success=True, output="\n".join(lines), handler_name="ide_open_workspace")


# ── ide_list_files ───────────────────────────────────────────────

async def _handle_ide_list_files(ctx: HandlerContext, **kwargs) -> HandlerResult:
    """Liste les fichiers dans un dossier via l'IDE."""
    path = kwargs.get("path", "").strip()

    abs_path = _resolve_path(path) if path else None
    bridge = _get_bridge()
    result = await bridge.list_files(abs_path)

    if result.get("success"):
        files = result.get("files", [])
        if not files:
            return HandlerResult(success=True, output="Dossier vide.", handler_name="ide_list_files")
        lines = []
        for f in files[:100]:
            icon = "📁" if f.get("type") == "directory" else "📄"
            lines.append(f"  {icon} {f['name']}")
        suffix = f"\n  ... ({len(files)} total)" if len(files) > 100 else ""
        return HandlerResult(success=True, output="\n".join(lines) + suffix, handler_name="ide_list_files")
    err = result.get("error", "Echec listing")
    return HandlerResult.fail(f"Echec listing '{abs_path}': {err}", handler_name="ide_list_files")


# ── ide_diff ─────────────────────────────────────────────────────

async def _handle_ide_diff(ctx: HandlerContext, **kwargs) -> HandlerResult:
    """Affiche un diff dans l'IDE entre contenu original et modifie."""
    original = kwargs.get("original", "")
    modified = kwargs.get("modified", "")
    filename = kwargs.get("filename", "diff")
    file_path = kwargs.get("file_path", "")

    bridge = _get_bridge()
    result = await bridge.show_diff(original, modified, filename, file_path or None)

    if result.get("success"):
        return HandlerResult(success=True, output=f"Diff affiche dans l'IDE: {filename}", handler_name="ide_diff")
    return HandlerResult(success=False, output="", error=result.get("error", "Echec diff"), handler_name="ide_diff")


# ── ide_launch ───────────────────────────────────────────────────

async def _handle_ide_launch(ctx: HandlerContext, **kwargs) -> HandlerResult:
    """Lance l'IDE Lumena (Electron) en arriere-plan."""
    workspace = kwargs.get("workspace", "").strip()

    ide_dir = Path(__file__).parent.parent.parent.parent / ".." / "cursor-ide-local"
    if not ide_dir.exists():
        # Chercher dans le dossier parent du workspace Lumena
        lumena_root = Path(__file__).parent.parent.parent.parent
        candidates = list(lumena_root.parent.glob("cursor-ide-local"))
        if candidates:
            ide_dir = candidates[0]
        else:
            return HandlerResult(
                success=False, output="",
                error="Dossier cursor-ide-local introuvable. L'IDE n'est pas installe cote a cote.",
                handler_name="ide_launch",
            )

    ide_dir = ide_dir.resolve()
    cmd = ["npm.cmd" if sys.platform == "win32" else "npm", "run", "start"]
    if workspace:
        # Pass workspace via env
        env = {**__import__("os").environ, "CURSOR_IDE_WORKSPACE": workspace}
    else:
        env = None

    try:
        subprocess.Popen(
            cmd,
            cwd=str(ide_dir),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        return HandlerResult(
            success=True,
            output=f"IDE Lumena lance depuis {ide_dir}" + (f" (workspace: {workspace})" if workspace else ""),
            handler_name="ide_launch",
        )
    except Exception as e:
        return HandlerResult(success=False, output="", error=f"Echec lancement IDE: {e}", handler_name="ide_launch")


# ── OS Control : état global ─────────────────────────────────────

async def _handle_ide_get_state(ctx: HandlerContext, **kwargs) -> HandlerResult:
    """Retourne l'etat complet de l'IDE (onglets, workspace, panels visibles)."""
    bridge = _get_bridge()
    result = await bridge.get_state()
    if not result.get("connected"):
        return HandlerResult(success=False, output="", error="IDE non connecte", handler_name="ide_get_state")
    tabs = result.get("tabs", [])
    lines = [
        f"Workspace: {result.get('workspace', 'aucun')}",
        f"Onglets ouverts: {len(tabs)}",
    ]
    for t in tabs:
        active = " [ACTIF]" if t.get("id") == result.get("activeTabId") else ""
        lines.append(f"  - {t.get('name', '?')} ({t.get('language', '?')}){active}")
    lines.append(f"Terminal: {'visible' if result.get('showTerminal') else 'cache'}")
    lines.append(f"Sidebar: {'visible' if result.get('showSidebar') else 'cache'}")
    lines.append(f"Chat: {'visible' if result.get('showChat') else 'cache'}")
    return HandlerResult(success=True, output="\n".join(lines), handler_name="ide_get_state")


# ── OS Control : éditeur ─────────────────────────────────────────

async def _handle_ide_editor_get_content(ctx: HandlerContext, **kwargs) -> HandlerResult:
    """Retourne le contenu de l'onglet actif dans Monaco."""
    bridge = _get_bridge()
    result = await bridge.editor_get_content()
    if result.get("success") is False:
        return HandlerResult(success=False, output="", error=result.get("error", "Echec"), handler_name="ide_editor_get_content")
    content = result.get("content", "")
    path = result.get("path", "")
    lines = content.split("\n")
    preview = "\n".join(lines[:300])
    suffix = f"\n... ({len(lines)} lignes total)" if len(lines) > 300 else ""
    return HandlerResult(success=True, output=f"Fichier: {path}\n```\n{preview}{suffix}\n```", handler_name="ide_editor_get_content")


async def _handle_ide_editor_switch_tab(ctx: HandlerContext, **kwargs) -> HandlerResult:
    """Change l'onglet actif (par chemin ou index)."""
    path = kwargs.get("path", "").strip() or None
    index = kwargs.get("index")
    if index is not None:
        index = int(index)
    bridge = _get_bridge()
    result = await bridge.editor_switch_tab(path=path, index=index)
    if result.get("success") is False:
        return HandlerResult(success=False, output="", error=result.get("error", "Echec"), handler_name="ide_editor_switch_tab")
    return HandlerResult(success=True, output=f"Onglet change: {path or f'index {index}'}", handler_name="ide_editor_switch_tab")


async def _handle_ide_editor_close_tab(ctx: HandlerContext, **kwargs) -> HandlerResult:
    """Ferme un onglet (par chemin ou index)."""
    path = kwargs.get("path", "").strip() or None
    index = kwargs.get("index")
    if index is not None:
        index = int(index)
    bridge = _get_bridge()
    result = await bridge.editor_close_tab(path=path, index=index)
    if result.get("success") is False:
        return HandlerResult(success=False, output="", error=result.get("error", "Echec"), handler_name="ide_editor_close_tab")
    return HandlerResult(success=True, output=f"Onglet ferme: {path or f'index {index}'}", handler_name="ide_editor_close_tab")


async def _handle_ide_editor_cursor_goto(ctx: HandlerContext, **kwargs) -> HandlerResult:
    """Positionne le curseur Monaco a la ligne et colonne donnees."""
    line = int(kwargs.get("line", 1))
    col = int(kwargs.get("col", kwargs.get("column", 1)))
    bridge = _get_bridge()
    result = await bridge.editor_cursor_goto(line, col)
    if result.get("success") is False:
        return HandlerResult(success=False, output="", error=result.get("error", "Echec"), handler_name="ide_editor_cursor_goto")
    return HandlerResult(success=True, output=f"Curseur positionne: ligne {line}, col {col}", handler_name="ide_editor_cursor_goto")


async def _handle_ide_editor_select(ctx: HandlerContext, **kwargs) -> HandlerResult:
    """Selectionne une plage de texte dans Monaco."""
    start_line = int(kwargs.get("startLine", kwargs.get("start_line", 1)))
    end_line = int(kwargs.get("endLine", kwargs.get("end_line", start_line)))
    start_col = int(kwargs.get("startCol", kwargs.get("start_col", 1)))
    end_col = kwargs.get("endCol", kwargs.get("end_col"))
    if end_col is not None:
        end_col = int(end_col)
    bridge = _get_bridge()
    result = await bridge.editor_select(start_line, end_line, start_col, end_col)
    if result.get("success") is False:
        return HandlerResult(success=False, output="", error=result.get("error", "Echec"), handler_name="ide_editor_select")
    return HandlerResult(success=True, output=f"Texte selectionne: L{start_line}:{start_col} → L{end_line}:{end_col or '?'}", handler_name="ide_editor_select")


async def _handle_ide_editor_insert(ctx: HandlerContext, **kwargs) -> HandlerResult:
    """Insere du texte a la position donnee ou au curseur actuel."""
    text = kwargs.get("text", "")
    line = kwargs.get("line")
    col = kwargs.get("col", kwargs.get("column"))
    if line is not None:
        line = int(line)
    if col is not None:
        col = int(col)
    bridge = _get_bridge()
    result = await bridge.editor_insert(text, line=line, col=col)
    if result.get("success") is False:
        return HandlerResult(success=False, output="", error=result.get("error", "Echec"), handler_name="ide_editor_insert")
    return HandlerResult(success=True, output=f"Texte insere ({len(text)} chars)", handler_name="ide_editor_insert")


async def _handle_ide_find_replace(ctx: HandlerContext, **kwargs) -> HandlerResult:
    """Cherche et remplace du texte dans l'editeur actif."""
    find = kwargs.get("find", "").strip()
    if not find:
        return HandlerResult(success=False, output="", error="Parametre 'find' requis.", handler_name="ide_find_replace")
    replace = kwargs.get("replace")
    all_occurrences = bool(kwargs.get("all", True))
    bridge = _get_bridge()
    result = await bridge.editor_find_replace(find, replace=replace, all=all_occurrences)
    if result.get("success") is False:
        return HandlerResult(success=False, output="", error=result.get("error", "Echec"), handler_name="ide_find_replace")
    count = result.get("count", 0)
    if replace is not None:
        replaced = result.get("replaceCount", count)
        return HandlerResult(success=True, output=f"'{find}' → '{replace}': {replaced} occurrence(s) remplacee(s)", handler_name="ide_find_replace")
    return HandlerResult(success=True, output=f"'{find}': {count} occurrence(s) trouvee(s)", handler_name="ide_find_replace")


async def _handle_ide_editor_save(ctx: HandlerContext, **kwargs) -> HandlerResult:
    """Sauvegarde le fichier de l'onglet actif."""
    bridge = _get_bridge()
    result = await bridge.editor_save()
    if result.get("success") is False:
        return HandlerResult(success=False, output="", error=result.get("error", "Echec"), handler_name="ide_editor_save")
    return HandlerResult(success=True, output=f"Fichier sauvegarde: {result.get('path', '?')}", handler_name="ide_editor_save")


# ── OS Control : terminal ─────────────────────────────────────────

async def _handle_ide_terminal_clear(ctx: HandlerContext, **kwargs) -> HandlerResult:
    """Efface l'output du terminal integre de l'IDE."""
    bridge = _get_bridge()
    result = await bridge.terminal_clear()
    if result.get("success") is False:
        return HandlerResult(success=False, output="", error=result.get("error", "Echec"), handler_name="ide_terminal_clear")
    return HandlerResult(success=True, output="Terminal efface.", handler_name="ide_terminal_clear")


async def _handle_ide_terminal_get_output(ctx: HandlerContext, **kwargs) -> HandlerResult:
    """Retourne l'output actuel du terminal integre."""
    bridge = _get_bridge()
    result = await bridge.terminal_get_output()
    if result.get("success") is False:
        return HandlerResult(success=False, output="", error=result.get("error", "Echec"), handler_name="ide_terminal_get_output")
    output = result.get("output", "")
    length = result.get("length", len(output))
    preview = output[-3000:] if len(output) > 3000 else output
    prefix = f"... (tronque, {length} chars total)\n" if len(output) > 3000 else ""
    return HandlerResult(success=True, output=f"{prefix}```\n{preview}\n```", handler_name="ide_terminal_get_output")


# ── OS Control : panels ───────────────────────────────────────────

async def _handle_ide_toggle_terminal(ctx: HandlerContext, **kwargs) -> HandlerResult:
    """Affiche ou cache le panneau terminal."""
    visible = kwargs.get("visible")
    if visible is not None:
        visible = str(visible).lower() not in ("false", "0", "non", "no")
    bridge = _get_bridge()
    result = await bridge.toggle_terminal(visible)
    if result.get("success") is False:
        return HandlerResult(success=False, output="", error=result.get("error", "Echec"), handler_name="ide_toggle_terminal")
    return HandlerResult(success=True, output=f"Terminal: {'affiche' if visible else 'cache' if visible is not None else 'bascule'}", handler_name="ide_toggle_terminal")


async def _handle_ide_toggle_search(ctx: HandlerContext, **kwargs) -> HandlerResult:
    """Affiche ou cache le panneau de recherche."""
    visible = kwargs.get("visible")
    if visible is not None:
        visible = str(visible).lower() not in ("false", "0", "non", "no")
    bridge = _get_bridge()
    result = await bridge.toggle_search(visible)
    if result.get("success") is False:
        return HandlerResult(success=False, output="", error=result.get("error", "Echec"), handler_name="ide_toggle_search")
    return HandlerResult(success=True, output="Recherche basculee.", handler_name="ide_toggle_search")


async def _handle_ide_toggle_sidebar(ctx: HandlerContext, **kwargs) -> HandlerResult:
    """Affiche ou cache la sidebar (explorateur de fichiers)."""
    visible = kwargs.get("visible")
    if visible is not None:
        visible = str(visible).lower() not in ("false", "0", "non", "no")
    bridge = _get_bridge()
    result = await bridge.toggle_sidebar(visible)
    if result.get("success") is False:
        return HandlerResult(success=False, output="", error=result.get("error", "Echec"), handler_name="ide_toggle_sidebar")
    return HandlerResult(success=True, output="Sidebar basculee.", handler_name="ide_toggle_sidebar")


async def _handle_ide_toggle_chat(ctx: HandlerContext, **kwargs) -> HandlerResult:
    """Affiche ou cache le panneau chat."""
    visible = kwargs.get("visible")
    if visible is not None:
        visible = str(visible).lower() not in ("false", "0", "non", "no")
    bridge = _get_bridge()
    result = await bridge.toggle_chat(visible)
    if result.get("success") is False:
        return HandlerResult(success=False, output="", error=result.get("error", "Echec"), handler_name="ide_toggle_chat")
    return HandlerResult(success=True, output="Chat bascule.", handler_name="ide_toggle_chat")


# ── OS Control : sidebar / fichiers ───────────────────────────────

async def _handle_ide_sidebar_create_file(ctx: HandlerContext, **kwargs) -> HandlerResult:
    """Cree un fichier vide et rafraichit la sidebar de l'IDE."""
    path = kwargs.get("path", "").strip()
    if not path:
        return HandlerResult(success=False, output="", error="Parametre 'path' requis.", handler_name="ide_sidebar_create_file")
    bridge = _get_bridge()
    result = await bridge.sidebar_create_file(path)
    if result.get("success") is False:
        return HandlerResult(success=False, output="", error=result.get("error", "Echec"), handler_name="ide_sidebar_create_file")
    return HandlerResult(success=True, output=f"Fichier cree: {path}", handler_name="ide_sidebar_create_file")


async def _handle_ide_sidebar_create_folder(ctx: HandlerContext, **kwargs) -> HandlerResult:
    """Cree un dossier et rafraichit la sidebar de l'IDE."""
    path = kwargs.get("path", "").strip()
    if not path:
        return HandlerResult(success=False, output="", error="Parametre 'path' requis.", handler_name="ide_sidebar_create_folder")
    bridge = _get_bridge()
    result = await bridge.sidebar_create_folder(path)
    if result.get("success") is False:
        return HandlerResult(success=False, output="", error=result.get("error", "Echec"), handler_name="ide_sidebar_create_folder")
    return HandlerResult(success=True, output=f"Dossier cree: {path}", handler_name="ide_sidebar_create_folder")


async def _handle_ide_sidebar_delete(ctx: HandlerContext, **kwargs) -> HandlerResult:
    """Supprime un fichier/dossier et rafraichit la sidebar."""
    path = kwargs.get("path", "").strip()
    if not path:
        return HandlerResult(success=False, output="", error="Parametre 'path' requis.", handler_name="ide_sidebar_delete")
    bridge = _get_bridge()
    result = await bridge.sidebar_delete(path)
    if result.get("success") is False:
        return HandlerResult(success=False, output="", error=result.get("error", "Echec"), handler_name="ide_sidebar_delete")
    return HandlerResult(success=True, output=f"Supprime: {path}", handler_name="ide_sidebar_delete")


async def _handle_ide_sidebar_rename(ctx: HandlerContext, **kwargs) -> HandlerResult:
    """Renomme ou deplace un fichier/dossier."""
    old_path = kwargs.get("old_path", kwargs.get("oldPath", "")).strip()
    new_path = kwargs.get("new_path", kwargs.get("newPath", "")).strip()
    if not old_path or not new_path:
        return HandlerResult(success=False, output="", error="Parametres 'old_path' et 'new_path' requis.", handler_name="ide_sidebar_rename")
    bridge = _get_bridge()
    result = await bridge.sidebar_rename(old_path, new_path)
    if result.get("success") is False:
        return HandlerResult(success=False, output="", error=result.get("error", "Echec"), handler_name="ide_sidebar_rename")
    return HandlerResult(success=True, output=f"Renomme: {old_path} → {new_path}", handler_name="ide_sidebar_rename")


# ── OS Control : recherche globale ────────────────────────────────

async def _handle_ide_search_in_files(ctx: HandlerContext, **kwargs) -> HandlerResult:
    """Recherche du texte dans tous les fichiers du workspace."""
    query = kwargs.get("query", "").strip()
    if not query:
        return HandlerResult(success=False, output="", error="Parametre 'query' requis.", handler_name="ide_search_in_files")
    workspace = kwargs.get("workspace", "").strip() or None
    bridge = _get_bridge()
    result = await bridge.search_in_files(query, workspace=workspace)
    if result.get("success") is False:
        return HandlerResult(success=False, output="", error=result.get("error", "Echec"), handler_name="ide_search_in_files")
    results = result.get("results", [])
    if not results:
        return HandlerResult(success=True, output=f"Aucun resultat pour '{query}'.", handler_name="ide_search_in_files")
    lines = [f"Resultats pour '{query}' ({len(results)} occurrences):"]
    for r in results[:50]:
        lines.append(f"  {r.get('fileName', '?')}:{r.get('line', '?')} — {r.get('preview', '').strip()}")
    if len(results) > 50:
        lines.append(f"  ... ({len(results) - 50} de plus)")
    return HandlerResult(success=True, output="\n".join(lines), handler_name="ide_search_in_files")


# ── OS Control : fenêtre ──────────────────────────────────────────

async def _handle_ide_window_minimize(ctx: HandlerContext, **kwargs) -> HandlerResult:
    """Minimise la fenetre de l'IDE."""
    bridge = _get_bridge()
    result = await bridge.window_minimize()
    if result.get("success") is False:
        return HandlerResult(success=False, output="", error=result.get("error", "Echec"), handler_name="ide_window_minimize")
    return HandlerResult(success=True, output="Fenetre minimisee.", handler_name="ide_window_minimize")


async def _handle_ide_window_maximize(ctx: HandlerContext, **kwargs) -> HandlerResult:
    """Maximise ou restaure la fenetre de l'IDE."""
    bridge = _get_bridge()
    result = await bridge.window_maximize()
    if result.get("success") is False:
        return HandlerResult(success=False, output="", error=result.get("error", "Echec"), handler_name="ide_window_maximize")
    return HandlerResult(success=True, output="Fenetre maximisee/restauree.", handler_name="ide_window_maximize")


async def _handle_ide_window_close(ctx: HandlerContext, **kwargs) -> HandlerResult:
    """Ferme la fenetre de l'IDE."""
    bridge = _get_bridge()
    result = await bridge.window_close()
    if result.get("success") is False:
        return HandlerResult(success=False, output="", error=result.get("error", "Echec"), handler_name="ide_window_close")
    return HandlerResult(success=True, output="Fenetre fermee.", handler_name="ide_window_close")


# ── Registration ─────────────────────────────────────────────────

HANDLERS: List[HandlerDef] = [
    HandlerDef(
        name="ide_status",
        description="Verifie si l'IDE Lumena est connecte et retourne son etat (workspace, terminal).",
        handler=_handle_ide_status,
        parameters={"properties": {}, "required": []},
        category="ide",
    ),
    HandlerDef(
        name="ide_open_file",
        description="[FICHIER] Ouvre UN FICHIER (pas un dossier) dans un onglet de l'editeur Monaco de l'IDE. Utiliser quand on veut lire/editer un fichier specifique. NE PAS utiliser pour ouvrir un dossier ou projet.",
        handler=_handle_ide_open_file,
        parameters={
            "properties": {
                "path": {"type": "string", "description": "Chemin absolu du fichier a ouvrir."},
            },
            "required": ["path"],
        },
        category="ide",
    ),
    HandlerDef(
        name="ide_read_file",
        description="[FICHIER] Lit et retourne le contenu brut d'un fichier via l'IDE (sans l'ouvrir dans un onglet). Utiliser pour inspecter un fichier sans changer l'editeur.",
        handler=_handle_ide_read_file,
        parameters={
            "properties": {
                "path": {"type": "string", "description": "Chemin absolu du fichier a lire."},
            },
            "required": ["path"],
        },
        category="ide",
    ),
    HandlerDef(
        name="ide_write_file",
        description="Ecrit du contenu dans un fichier et l'ouvre dans l'IDE Lumena.",
        handler=_handle_ide_write_file,
        parameters={
            "properties": {
                "path": {"type": "string", "description": "Chemin absolu du fichier."},
                "content": {"type": "string", "description": "Contenu complet a ecrire."},
            },
            "required": ["path", "content"],
        },
        category="ide",
    ),
    HandlerDef(
        name="ide_terminal",
        description="Execute une commande dans le terminal integre de l'IDE Lumena.",
        handler=_handle_ide_terminal,
        parameters={
            "properties": {
                "command": {"type": "string", "description": "Commande a executer."},
            },
            "required": ["command"],
        },
        category="ide",
    ),
    HandlerDef(
        name="ide_navigate",
        description="[DOSSIER] Ouvre UN DOSSIER/PROJET comme workspace dans la sidebar (explorateur de fichiers) de l'IDE. Utiliser quand l'utilisateur dit 'ouvre le dossier X', 'ouvre le projet X', 'navigue vers X'. Le dossier apparait dans la barre de gauche. NE PAS utiliser pour ouvrir un fichier.",
        handler=_handle_ide_navigate,
        parameters={
            "properties": {
                "path": {"type": "string", "description": "Chemin absolu du dossier."},
            },
            "required": ["path"],
        },
        category="ide",
    ),
    HandlerDef(
        name="ide_open_workspace",
        description="[DOSSIER + FICHIERS] Ouvre un DOSSIER comme workspace dans la sidebar ET ouvre optionnellement des fichiers dans l'editeur. LE MEILLEUR CHOIX quand l'utilisateur dit 'ouvre le projet/dossier X dans l'IDE' ou 'va sur le projet X'.",
        handler=_handle_ide_open_workspace,
        parameters={
            "properties": {
                "path": {"type": "string", "description": "Chemin absolu du DOSSIER a ouvrir dans la sidebar."},
                "files": {"type": "array", "items": {"type": "string"}, "description": "Chemins absolus de fichiers a ouvrir dans Monaco (optionnel)."},
            },
            "required": ["path"],
        },
        category="ide",
    ),
    HandlerDef(
        name="ide_list_files",
        description="Liste les fichiers d'un dossier via l'IDE Lumena.",
        handler=_handle_ide_list_files,
        parameters={
            "properties": {
                "path": {"type": "string", "description": "Chemin du dossier (defaut: workspace actuel)."},
            },
            "required": [],
        },
        category="ide",
    ),
    HandlerDef(
        name="ide_diff",
        description="Affiche un diff visuel dans l'IDE entre contenu original et modifie.",
        handler=_handle_ide_diff,
        parameters={
            "properties": {
                "original": {"type": "string", "description": "Contenu original."},
                "modified": {"type": "string", "description": "Contenu modifie."},
                "filename": {"type": "string", "description": "Nom du fichier pour le titre."},
                "file_path": {"type": "string", "description": "Chemin absolu du fichier."},
            },
            "required": ["original", "modified"],
        },
        category="ide",
    ),
    HandlerDef(
        name="ide_launch",
        description="Lance l'IDE Lumena (Electron) en arriere-plan. Utiliser 'workspace' pour ouvrir directement un dossier au demarrage.",
        handler=_handle_ide_launch,
        parameters={
            "properties": {
                "workspace": {"type": "string", "description": "Chemin du dossier workspace a ouvrir."},
            },
            "required": [],
        },
        category="ide",
    ),

    # ── OS Control ───────────────────────────────────────────────
    HandlerDef(
        name="ide_get_state",
        description="Retourne l'etat complet de l'IDE: onglets ouverts, workspace, panels visibles (terminal, sidebar, chat).",
        handler=_handle_ide_get_state,
        parameters={"properties": {}, "required": []},
        category="ide",
    ),
    HandlerDef(
        name="ide_editor_get_content",
        description="Retourne le contenu complet de l'onglet actif dans l'editeur Monaco (avec chemin du fichier).",
        handler=_handle_ide_editor_get_content,
        parameters={"properties": {}, "required": []},
        category="ide",
    ),
    HandlerDef(
        name="ide_editor_switch_tab",
        description="Change l'onglet actif dans l'editeur (par chemin de fichier ou par index numerique).",
        handler=_handle_ide_editor_switch_tab,
        parameters={
            "properties": {
                "path": {"type": "string", "description": "Chemin absolu du fichier a afficher."},
                "index": {"type": "integer", "description": "Index de l'onglet (0 = premier)."},
            },
            "required": [],
        },
        category="ide",
    ),
    HandlerDef(
        name="ide_editor_close_tab",
        description="Ferme un onglet dans l'editeur (par chemin ou index).",
        handler=_handle_ide_editor_close_tab,
        parameters={
            "properties": {
                "path": {"type": "string", "description": "Chemin absolu du fichier."},
                "index": {"type": "integer", "description": "Index de l'onglet."},
            },
            "required": [],
        },
        category="ide",
    ),
    HandlerDef(
        name="ide_editor_cursor_goto",
        description="Positionne le curseur Monaco a une ligne et colonne precises et centre la vue.",
        handler=_handle_ide_editor_cursor_goto,
        parameters={
            "properties": {
                "line": {"type": "integer", "description": "Numero de ligne (1-base)."},
                "col": {"type": "integer", "description": "Numero de colonne (1-base, defaut: 1)."},
            },
            "required": ["line"],
        },
        category="ide",
    ),
    HandlerDef(
        name="ide_editor_select",
        description="Selectionne une plage de texte dans Monaco (de startLine:startCol a endLine:endCol).",
        handler=_handle_ide_editor_select,
        parameters={
            "properties": {
                "startLine": {"type": "integer", "description": "Ligne de debut (1-base)."},
                "endLine": {"type": "integer", "description": "Ligne de fin (1-base)."},
                "startCol": {"type": "integer", "description": "Colonne de debut (defaut: 1)."},
                "endCol": {"type": "integer", "description": "Colonne de fin (defaut: fin de ligne)."},
            },
            "required": ["startLine", "endLine"],
        },
        category="ide",
    ),
    HandlerDef(
        name="ide_editor_insert",
        description="Insere du texte a une position donnee ou au curseur actuel dans Monaco.",
        handler=_handle_ide_editor_insert,
        parameters={
            "properties": {
                "text": {"type": "string", "description": "Texte a inserer."},
                "line": {"type": "integer", "description": "Ligne d'insertion (optionnel, sinon curseur actuel)."},
                "col": {"type": "integer", "description": "Colonne d'insertion (optionnel)."},
            },
            "required": ["text"],
        },
        category="ide",
    ),
    HandlerDef(
        name="ide_find_replace",
        description="Cherche du texte dans l'editeur actif et le remplace optionnellement. Retourne le nombre d'occurrences.",
        handler=_handle_ide_find_replace,
        parameters={
            "properties": {
                "find": {"type": "string", "description": "Texte a chercher."},
                "replace": {"type": "string", "description": "Texte de remplacement (optionnel, si absent: cherche seulement)."},
                "all": {"type": "boolean", "description": "Remplacer toutes les occurrences (defaut: true)."},
            },
            "required": ["find"],
        },
        category="ide",
    ),
    HandlerDef(
        name="ide_editor_save",
        description="Sauvegarde le fichier de l'onglet actif sur disque.",
        handler=_handle_ide_editor_save,
        parameters={"properties": {}, "required": []},
        category="ide",
    ),
    HandlerDef(
        name="ide_terminal_clear",
        description="Efface l'output du terminal integre de l'IDE.",
        handler=_handle_ide_terminal_clear,
        parameters={"properties": {}, "required": []},
        category="ide",
    ),
    HandlerDef(
        name="ide_terminal_get_output",
        description="Retourne l'output actuel du terminal integre de l'IDE (les 3000 derniers caracteres).",
        handler=_handle_ide_terminal_get_output,
        parameters={"properties": {}, "required": []},
        category="ide",
    ),
    HandlerDef(
        name="ide_toggle_terminal",
        description="Affiche ou cache le panneau terminal de l'IDE.",
        handler=_handle_ide_toggle_terminal,
        parameters={
            "properties": {
                "visible": {"type": "boolean", "description": "True=afficher, False=cacher, absent=basculer."},
            },
            "required": [],
        },
        category="ide",
    ),
    HandlerDef(
        name="ide_toggle_search",
        description="Affiche ou cache le panneau de recherche globale de l'IDE.",
        handler=_handle_ide_toggle_search,
        parameters={
            "properties": {
                "visible": {"type": "boolean", "description": "True=afficher, False=cacher, absent=basculer."},
            },
            "required": [],
        },
        category="ide",
    ),
    HandlerDef(
        name="ide_toggle_sidebar",
        description="Affiche ou cache la sidebar (explorateur de fichiers) de l'IDE.",
        handler=_handle_ide_toggle_sidebar,
        parameters={
            "properties": {
                "visible": {"type": "boolean", "description": "True=afficher, False=cacher, absent=basculer."},
            },
            "required": [],
        },
        category="ide",
    ),
    HandlerDef(
        name="ide_toggle_chat",
        description="Affiche ou cache le panneau chat Lumena dans l'IDE.",
        handler=_handle_ide_toggle_chat,
        parameters={
            "properties": {
                "visible": {"type": "boolean", "description": "True=afficher, False=cacher, absent=basculer."},
            },
            "required": [],
        },
        category="ide",
    ),
    HandlerDef(
        name="ide_sidebar_create_file",
        description="Cree un nouveau fichier vide et rafraichit l'explorateur de la sidebar.",
        handler=_handle_ide_sidebar_create_file,
        parameters={
            "properties": {
                "path": {"type": "string", "description": "Chemin absolu du fichier a creer."},
            },
            "required": ["path"],
        },
        category="ide",
    ),
    HandlerDef(
        name="ide_sidebar_create_folder",
        description="Cree un nouveau dossier et rafraichit l'explorateur de la sidebar.",
        handler=_handle_ide_sidebar_create_folder,
        parameters={
            "properties": {
                "path": {"type": "string", "description": "Chemin absolu du dossier a creer."},
            },
            "required": ["path"],
        },
        category="ide",
    ),
    HandlerDef(
        name="ide_sidebar_delete",
        description="Supprime un fichier ou dossier et rafraichit la sidebar.",
        handler=_handle_ide_sidebar_delete,
        parameters={
            "properties": {
                "path": {"type": "string", "description": "Chemin absolu a supprimer."},
            },
            "required": ["path"],
        },
        category="ide",
    ),
    HandlerDef(
        name="ide_sidebar_rename",
        description="Renomme ou deplace un fichier/dossier et rafraichit la sidebar.",
        handler=_handle_ide_sidebar_rename,
        parameters={
            "properties": {
                "old_path": {"type": "string", "description": "Chemin actuel."},
                "new_path": {"type": "string", "description": "Nouveau chemin."},
            },
            "required": ["old_path", "new_path"],
        },
        category="ide",
    ),
    HandlerDef(
        name="ide_search_in_files",
        description="Recherche du texte dans tous les fichiers du workspace et retourne les occurrences avec contexte.",
        handler=_handle_ide_search_in_files,
        parameters={
            "properties": {
                "query": {"type": "string", "description": "Texte a rechercher."},
                "workspace": {"type": "string", "description": "Dossier workspace (defaut: workspace actuel de l'IDE)."},
            },
            "required": ["query"],
        },
        category="ide",
    ),
    HandlerDef(
        name="ide_window_minimize",
        description="Minimise la fenetre de l'IDE Lumena.",
        handler=_handle_ide_window_minimize,
        parameters={"properties": {}, "required": []},
        category="ide",
    ),
    HandlerDef(
        name="ide_window_maximize",
        description="Maximise ou restaure la fenetre de l'IDE Lumena.",
        handler=_handle_ide_window_maximize,
        parameters={"properties": {}, "required": []},
        category="ide",
    ),
    HandlerDef(
        name="ide_window_close",
        description="Ferme la fenetre de l'IDE Lumena.",
        handler=_handle_ide_window_close,
        parameters={"properties": {}, "required": []},
        category="ide",
    ),
]


def get_ide_handler_defs() -> List[HandlerDef]:
    """Retourne toutes les definitions de handlers IDE pour le registre V2."""
    return HANDLERS
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU General Public License v3.0 (GPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
