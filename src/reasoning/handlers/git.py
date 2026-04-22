"""
git.py - Handlers Git pour LUMENA.

Handlers (9):
  git_status, git_init, git_add, git_commit, git_log,
  git_diff, git_branch, git_push_pull, git_remote.

Permet à LUMENA d'utiliser git comme un dev senior :
  - Initialiser un dépôt, versionner le travail après chaque tâche
  - Voir l'état du dépôt, l'historique, les diffs
  - Créer/switcher des branches, puller/pusher

Chaque handler est une fonction async standalone :
    async def handler_name(ctx: HandlerContext, **kwargs) -> HandlerResult
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

from .context import HandlerContext
from .contracts import HandlerResult
from .registry_v2 import HandlerDef


# ─── Utilitaires internes ─────────────────────────────────────────────────


def _git_available() -> bool:
    return shutil.which("git") is not None


async def _git(
    args: List[str],
    cwd: Path,
    timeout: int = 30,
    stdin_input: str = "",
) -> tuple[int, str]:
    """
    Lance `git <args>` dans cwd.
    Retourne (returncode, output_combiné).
    """
    cmd = ["git"] + args
    stdin_pipe = asyncio.subprocess.PIPE if stdin_input else None
    stdin_bytes = stdin_input.encode("utf-8") if stdin_input else None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=stdin_pipe,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(cwd),
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},  # pas de prompt interactif
        )
        try:
            out_b, err_b = await asyncio.wait_for(
                proc.communicate(input=stdin_bytes), timeout=timeout
            )
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass  # process déjà mort
            return -1, f"[timeout >{timeout}s]"
        out = (out_b or b"").decode("utf-8", errors="replace").strip()
        err = (err_b or b"").decode("utf-8", errors="replace").strip()
        combined = out + ("\n" + err if err else "")
        rc = proc.returncode if proc.returncode is not None else 0
        return rc, combined.strip()
    except FileNotFoundError:
        return -1, "git introuvable — installez git et relancez."
    except Exception as exc:
        return -1, f"Erreur interne git : {exc}"


def _resolve_dir(ctx: HandlerContext, path: str) -> Path:
    if path:
        return ctx.resolve_path(path, want_dir=True)
    return ctx.runtime_root


def _walk_repo_files(base: Path, max_files: int = 40) -> List[str]:
    """Liste les fichiers trackés/non-ignorés (heuristique rapide)."""
    files = []
    for p in base.rglob("*"):
        if p.is_file() and not any(
            part.startswith(".") or part in ("node_modules", "__pycache__", "dist", "build")
            for part in p.relative_to(base).parts
        ):
            files.append(str(p.relative_to(base)).replace("\\", "/"))
            if len(files) >= max_files:
                break
    return files


def _has_own_git(cwd: Path) -> bool:
    """Vérifie que cwd a un .git PROPRE (pas hérité d'un parent)."""
    return (cwd / ".git").exists()


def _parent_repo_warning(cwd: Path, handler_name: str) -> HandlerResult:
    """Message d'erreur quand on opère dans un dossier sans .git propre."""
    return HandlerResult.ok(
        f"⚠️ Pas de dépôt git dans {cwd.name}/. "
        f"Utilise d'abord `git_init` pour initialiser le dépôt, "
        f"puis `git_add` + `git_commit` pour versionner.",
        handler_name=handler_name,
    )


# ─── Handlers ────────────────────────────────────────────────────────────


async def git_status_handler(
    ctx: HandlerContext,
    path: str = "",
) -> HandlerResult:
    """Affiche l'état du dépôt git (fichiers modifiés, staged, non trackés)."""
    if not _git_available():
        return HandlerResult.fail("git introuvable sur ce système.", handler_name="git_status")
    cwd = _resolve_dir(ctx, path)
    # Guard: si le dossier n'a pas de .git, vérifier qu'on ne remonte pas à un repo parent
    # pour éviter de lister des milliers de fichiers hors-contexte.
    if not _has_own_git(cwd):
        return _parent_repo_warning(cwd, "git_status")
    rc, out = await _git(["status", "--short", "--branch"], cwd)
    if rc != 0:
        return HandlerResult.fail(f"❌ git status : {out}", handler_name="git_status")
    return HandlerResult.ok(out or "(rien à signaler — dépôt propre)", handler_name="git_status")


async def git_init_handler(
    ctx: HandlerContext,
    path: str = "",
    initial_branch: str = "main",
) -> HandlerResult:
    """Initialise un dépôt git dans le répertoire indiqué."""
    if not _git_available():
        return HandlerResult.fail("git introuvable.", handler_name="git_init")
    cwd = _resolve_dir(ctx, path)
    cwd.mkdir(parents=True, exist_ok=True)

    # Vérifier si déjà un dépôt PROPRE (.git dans ce dossier, pas hérité)
    if _has_own_git(cwd):
        return HandlerResult.ok(f"✅ Dépôt git déjà initialisé dans `{cwd}`", handler_name="git_init")

    # Guard : si le dossier contient déjà des fichiers et qu'un chemin explicite a été fourni,
    # avertir l'agent qu'il doit s'assurer d'avoir le bon projet avant de continuer.
    if path and cwd.exists() and any(cwd.iterdir()):
        basename = cwd.name
        warning_suffix = (
            f"\n\n⚠️ ATTENTION : le dossier `{basename}` contenait déjà des fichiers. "
            "Si ce projet n'est PAS celui demandé par l'utilisateur, "
            "stoppe immédiatement et utilise FINAL pour expliquer la situation "
            "(ex : \"Ce projet n'est pas en local, veux-tu que je le clone depuis GitHub ?\")."
        )
    else:
        warning_suffix = ""

    rc, out = await _git(["init", f"--initial-branch={initial_branch}"], cwd)
    if rc != 0:
        # Fallback sans --initial-branch (vieilles versions git)
        rc, out = await _git(["init"], cwd)
    if rc != 0:
        return HandlerResult.fail(f"❌ git init : {out}", handler_name="git_init")

    # Créer .gitignore de base si absent
    gitignore = cwd / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text(
            "__pycache__/\n*.pyc\n*.pyo\n.env\n.venv/\nvenv/\nnode_modules/\ndist/\nbuild/\n*.log\n.DS_Store\n",
            encoding="utf-8",
        )
        out += "\n  → .gitignore créé"

    return HandlerResult.ok(f"✅ {out}\nDossier : `{cwd}`{warning_suffix}", handler_name="git_init")


async def git_add_handler(
    ctx: HandlerContext,
    files: str = ".",
    path: str = "",
) -> HandlerResult:
    """Stage des fichiers pour le prochain commit (git add)."""
    if not _git_available():
        return HandlerResult.fail("git introuvable.", handler_name="git_add")
    cwd = _resolve_dir(ctx, path)
    if not _has_own_git(cwd):
        return _parent_repo_warning(cwd, "git_add")
    targets = [f.strip() for f in files.split(",") if f.strip()] or ["."]
    rc, out = await _git(["add"] + targets, cwd)
    if rc != 0:
        return HandlerResult.fail(f"❌ git add : {out}", handler_name="git_add")
    rc2, out2 = await _git(["status", "--short"], cwd)
    staged = "\n".join(l for l in (out2 or "").splitlines() if l.startswith(("A", "M", "D", "R", "C")))
    return HandlerResult.ok(
        f"✅ Staged :\n{staged or '(aucun changement détecté après add)'}",
        handler_name="git_add",
    )


async def git_commit_handler(
    ctx: HandlerContext,
    message: str,
    add_all: bool = True,
    path: str = "",
) -> HandlerResult:
    """
    Crée un commit git.
    Si add_all=True (défaut), fait `git add .` avant de committer.
    """
    if not _git_available():
        return HandlerResult.fail("git introuvable.", handler_name="git_commit")
    cwd = _resolve_dir(ctx, path)
    if not _has_own_git(cwd):
        return _parent_repo_warning(cwd, "git_commit")

    # Configurer user si absent (nécessaire pour le premier commit)
    await _git(["config", "user.email", "lumena@localhost"], cwd)
    await _git(["config", "user.name", "Lumena"], cwd)

    if add_all:
        await _git(["add", "."], cwd)

    rc, out = await _git(["commit", "-m", message], cwd)
    if rc != 0:
        if "nothing to commit" in out.lower():
            return HandlerResult.ok("ℹ️ Rien à committer — dépôt déjà propre.", handler_name="git_commit")
        return HandlerResult.fail(f"❌ git commit : {out}", handler_name="git_commit")
    return HandlerResult.ok(f"✅ {out}", handler_name="git_commit")


async def git_log_handler(
    ctx: HandlerContext,
    n: int = 10,
    path: str = "",
    oneline: bool = True,
) -> HandlerResult:
    """Affiche l'historique des commits."""
    if not _git_available():
        return HandlerResult.fail("git introuvable.", handler_name="git_log")
    cwd = _resolve_dir(ctx, path)
    if not _has_own_git(cwd):
        return _parent_repo_warning(cwd, "git_log")
    args = ["log", f"-{max(1, min(int(n), 50))}"]
    if oneline:
        args.append("--oneline")
    else:
        args += ["--pretty=format:%h %ad %an : %s", "--date=short"]
    rc, out = await _git(args, cwd)
    if rc != 0:
        if "does not have any commits" in out or "fatal" in out.lower():
            return HandlerResult.ok("ℹ️ Aucun commit encore dans ce dépôt.", handler_name="git_log")
        return HandlerResult.fail(f"❌ git log : {out}", handler_name="git_log")
    return HandlerResult.ok(out or "(historique vide)", handler_name="git_log")


async def git_diff_handler(
    ctx: HandlerContext,
    file: str = "",
    staged: bool = False,
    path: str = "",
) -> HandlerResult:
    """Affiche les différences (git diff). staged=True pour voir ce qui est staged."""
    if not _git_available():
        return HandlerResult.fail("git introuvable.", handler_name="git_diff")
    cwd = _resolve_dir(ctx, path)
    if not _has_own_git(cwd):
        return _parent_repo_warning(cwd, "git_diff")
    args = ["diff"]
    if staged:
        args.append("--staged")
    if file:
        args += ["--", file]
    rc, out = await _git(args, cwd)
    if rc != 0:
        return HandlerResult.fail(f"❌ git diff : {out}", handler_name="git_diff")
    if len(out) > 6000:
        out = out[:6000] + "\n[... diff tronqué ...]"
    return HandlerResult.ok(out or "(aucune différence)", handler_name="git_diff")


async def git_branch_handler(
    ctx: HandlerContext,
    action: str = "list",
    name: str = "",
    path: str = "",
) -> HandlerResult:
    """
    Gère les branches git.
    action: 'list' (défaut), 'create', 'switch', 'delete'.
    """
    if not _git_available():
        return HandlerResult.fail("git introuvable.", handler_name="git_branch")
    cwd = _resolve_dir(ctx, path)
    if not _has_own_git(cwd):
        return _parent_repo_warning(cwd, "git_branch")
    action = action.lower().strip()

    if action == "list":
        rc, out = await _git(["branch", "-a"], cwd)
    elif action == "create":
        if not name:
            return HandlerResult.fail("Paramètre 'name' requis pour créer une branche.", handler_name="git_branch")
        rc, out = await _git(["checkout", "-b", name], cwd)
    elif action in ("switch", "checkout"):
        if not name:
            return HandlerResult.fail("Paramètre 'name' requis pour switcher de branche.", handler_name="git_branch")
        rc, out = await _git(["checkout", name], cwd)
    elif action == "delete":
        if not name:
            return HandlerResult.fail("Paramètre 'name' requis pour supprimer une branche.", handler_name="git_branch")
        rc, out = await _git(["branch", "-d", name], cwd)
    else:
        return HandlerResult.fail(
            f"Action inconnue : '{action}'. Valeurs valides : list, create, switch, delete.",
            handler_name="git_branch",
        )

    if rc != 0:
        return HandlerResult.fail(f"❌ git branch ({action}) : {out}", handler_name="git_branch")
    return HandlerResult.ok(out or f"✅ branch {action} OK", handler_name="git_branch")


async def git_push_pull_handler(
    ctx: HandlerContext,
    action: str = "pull",
    remote: str = "origin",
    branch: str = "",
    path: str = "",
) -> HandlerResult:
    """
    Push ou pull vers/depuis un remote.
    action: 'push' ou 'pull'.
    """
    if not _git_available():
        return HandlerResult.fail("git introuvable.", handler_name="git_push_pull")
    cwd = _resolve_dir(ctx, path)
    if not _has_own_git(cwd):
        return _parent_repo_warning(cwd, "git_push_pull")
    action = action.lower().strip()
    if action not in ("push", "pull"):
        return HandlerResult.fail(
            f"Action invalide '{action}'. Utiliser 'push' ou 'pull'.",
            handler_name="git_push_pull",
        )

    args = [action, remote]
    if branch:
        args.append(branch)

    rc, out = await _git(args, cwd, timeout=60)
    if rc != 0:
        return HandlerResult.fail(f"❌ git {action} : {out}", handler_name="git_push_pull")
    return HandlerResult.ok(f"✅ git {action} réussi :\n{out}", handler_name="git_push_pull")


async def git_remote_handler(
    ctx: HandlerContext,
    action: str = "list",
    name: str = "origin",
    url: str = "",
    path: str = "",
) -> HandlerResult:
    """
    Gère les remotes git.
    action: 'list' (afficher), 'add' (ajouter), 'set-url' (modifier), 'remove' (supprimer).
    """
    if not _git_available():
        return HandlerResult.fail("git introuvable.", handler_name="git_remote")
    cwd = _resolve_dir(ctx, path)
    if not _has_own_git(cwd):
        return _parent_repo_warning(cwd, "git_remote")
    action = action.lower().strip()

    if action == "list":
        rc, out = await _git(["remote", "-v"], cwd)
        if rc != 0:
            return HandlerResult.fail(f"❌ git remote : {out}", handler_name="git_remote")
        return HandlerResult.ok(out or "(aucun remote configuré)", handler_name="git_remote")

    if action == "add":
        if not url:
            return HandlerResult.fail(
                "Paramètre 'url' requis pour ajouter un remote.", handler_name="git_remote",
            )
        rc, out = await _git(["remote", "add", name, url], cwd)
        if rc != 0:
            if "already exists" in out:
                # Auto-corriger en set-url
                rc, out = await _git(["remote", "set-url", name, url], cwd)
                if rc != 0:
                    return HandlerResult.fail(f"❌ git remote set-url : {out}", handler_name="git_remote")
                return HandlerResult.ok(f"✅ Remote '{name}' mis à jour → {url}", handler_name="git_remote")
            return HandlerResult.fail(f"❌ git remote add : {out}", handler_name="git_remote")
        return HandlerResult.ok(f"✅ Remote '{name}' ajouté → {url}", handler_name="git_remote")

    if action == "set-url":
        if not url:
            return HandlerResult.fail(
                "Paramètre 'url' requis pour modifier un remote.", handler_name="git_remote",
            )
        rc, out = await _git(["remote", "set-url", name, url], cwd)
        if rc != 0:
            return HandlerResult.fail(f"❌ git remote set-url : {out}", handler_name="git_remote")
        return HandlerResult.ok(f"✅ Remote '{name}' → {url}", handler_name="git_remote")

    if action == "remove":
        rc, out = await _git(["remote", "remove", name], cwd)
        if rc != 0:
            return HandlerResult.fail(f"❌ git remote remove : {out}", handler_name="git_remote")
        return HandlerResult.ok(f"✅ Remote '{name}' supprimé", handler_name="git_remote")

    return HandlerResult.fail(
        f"Action inconnue : '{action}'. Valeurs valides : list, add, set-url, remove.",
        handler_name="git_remote",
    )


# ─── Registration ────────────────────────────────────────────────────────


def get_git_handler_defs() -> List[HandlerDef]:
    """Retourne les définitions des handlers git pour le registre V2."""
    return [
        HandlerDef(
            name="git_status",
            description=(
                "Affiche l'état du dépôt git (fichiers modifiés, staged, non trackés). "
                "Utiliser avant tout commit ou pour savoir ce qui a changé."
            ),
            parameters={
                "properties": {
                    "path": {"type": "string", "description": "Dossier du dépôt (optionnel, défaut: workspace)"},
                },
                "required": [],
            },
            handler=git_status_handler,
            category="git",
            source_module="handlers.git",
        ),
        HandlerDef(
            name="git_init",
            description=(
                "Initialise un dépôt git dans un dossier. "
                "Crée aussi un .gitignore standard. À appeler après create_project pour versionner."
            ),
            parameters={
                "properties": {
                    "path": {"type": "string", "description": "Dossier à initialiser (optionnel)"},
                    "initial_branch": {"type": "string", "description": "Nom de la branche initiale (défaut: main)"},
                },
                "required": [],
            },
            handler=git_init_handler,
            category="git",
            source_module="handlers.git",
        ),
        HandlerDef(
            name="git_add",
            description=(
                "Stage des fichiers pour le prochain commit (git add). "
                "Par défaut stage tout (.). Accepte une liste séparée par virgules."
            ),
            parameters={
                "properties": {
                    "files": {"type": "string", "description": "Fichiers à stager, séparés par virgules (défaut: '.' = tout)"},
                    "path": {"type": "string", "description": "Dossier du dépôt (optionnel)"},
                },
                "required": [],
            },
            handler=git_add_handler,
            category="git",
            source_module="handlers.git",
        ),
        HandlerDef(
            name="git_commit",
            description=(
                "Crée un commit git avec un message. "
                "Par défaut fait git add . avant (add_all=true). "
                "À appeler après avoir créé ou modifié des fichiers importants."
            ),
            parameters={
                "properties": {
                    "message": {"type": "string", "description": "Message du commit (clair et descriptif)"},
                    "add_all": {"type": "boolean", "description": "Faire git add . avant le commit (défaut: true)"},
                    "path": {"type": "string", "description": "Dossier du dépôt (optionnel)"},
                },
                "required": ["message"],
            },
            handler=git_commit_handler,
            category="git",
            source_module="handlers.git",
        ),
        HandlerDef(
            name="git_log",
            description="Affiche l'historique des commits du dépôt.",
            parameters={
                "properties": {
                    "n": {"type": "integer", "description": "Nombre de commits à afficher (défaut: 10)"},
                    "path": {"type": "string", "description": "Dossier du dépôt (optionnel)"},
                    "oneline": {"type": "boolean", "description": "Format compact une ligne par commit (défaut: true)"},
                },
                "required": [],
            },
            handler=git_log_handler,
            category="git",
            source_module="handlers.git",
        ),
        HandlerDef(
            name="git_diff",
            description=(
                "Affiche les différences dans les fichiers (git diff). "
                "staged=true pour voir ce qui est prêt à committer."
            ),
            parameters={
                "properties": {
                    "file": {"type": "string", "description": "Fichier spécifique à diff-er (optionnel, défaut: tout)"},
                    "staged": {"type": "boolean", "description": "Voir les changements staged (défaut: false)"},
                    "path": {"type": "string", "description": "Dossier du dépôt (optionnel)"},
                },
                "required": [],
            },
            handler=git_diff_handler,
            category="git",
            source_module="handlers.git",
        ),
        HandlerDef(
            name="git_branch",
            description=(
                "Gère les branches git. "
                "action: 'list' (voir les branches), 'create' (créer), 'switch' (changer), 'delete' (supprimer)."
            ),
            parameters={
                "properties": {
                    "action": {
                        "type": "string",
                        "description": "Action : 'list', 'create', 'switch', 'delete' (défaut: 'list')",
                    },
                    "name": {"type": "string", "description": "Nom de la branche (requis pour create/switch/delete)"},
                    "path": {"type": "string", "description": "Dossier du dépôt (optionnel)"},
                },
                "required": [],
            },
            handler=git_branch_handler,
            category="git",
            source_module="handlers.git",
        ),
        HandlerDef(
            name="git_push_pull",
            description=(
                "Push ou pull vers/depuis un remote git (GitHub, GitLab, etc.). "
                "action: 'push' ou 'pull'. remote défaut: 'origin'."
            ),
            parameters={
                "properties": {
                    "action": {"type": "string", "description": "'push' ou 'pull' (défaut: 'pull')"},
                    "remote": {"type": "string", "description": "Nom du remote (défaut: 'origin')"},
                    "branch": {"type": "string", "description": "Branche cible (optionnel)"},
                    "path": {"type": "string", "description": "Dossier du dépôt (optionnel)"},
                },
                "required": [],
            },
            handler=git_push_pull_handler,
            category="git",
            source_module="handlers.git",
        ),
        HandlerDef(
            name="git_remote",
            description=(
                "Gère les remotes git (origin, etc.). "
                "action: 'list' (voir), 'add' (ajouter), 'set-url' (modifier URL), 'remove' (supprimer). "
                "TOUJOURS utiliser cet outil au lieu de `run_command` pour les opérations remote."
            ),
            parameters={
                "properties": {
                    "action": {
                        "type": "string",
                        "description": "Action : 'list', 'add', 'set-url', 'remove' (défaut: 'list')",
                    },
                    "name": {"type": "string", "description": "Nom du remote (défaut: 'origin')"},
                    "url": {"type": "string", "description": "URL du remote (requis pour add/set-url)"},
                    "path": {"type": "string", "description": "Dossier du dépôt (optionnel)"},
                },
                "required": [],
            },
            handler=git_remote_handler,
            category="git",
            source_module="handlers.git",
        ),
    ]
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
