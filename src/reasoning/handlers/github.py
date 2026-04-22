"""
github.py - Handlers GitHub pour Lumena.

Donne à Lumena un accès complet à l'API GitHub REST v3 via son propre compte.
Token : variable d'environnement GITHUB_TOKEN ou registre des clés API.

Handlers disponibles :
  - github_repo_list       : lister ses repos
  - github_repo_create     : créer un repo
  - github_repo_delete     : supprimer un repo
  - github_file_read       : lire un fichier depuis un repo
  - github_file_write      : créer ou mettre à jour un fichier
  - github_file_delete     : supprimer un fichier d'un repo
  - github_search_code     : chercher du code sur GitHub
  - github_issues_list     : lister les issues d'un repo
  - github_issue_create    : créer une issue
  - github_push_directory  : uploader un dossier local vers un repo
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

from .context import HandlerContext
from .contracts import HandlerResult
from .registry_v2 import HandlerDef


# ─── Helpers GitHub ──────────────────────────────────────────────────────────

_GH_API = "https://api.github.com"

# Fichiers/dossiers à ignorer lors de l'upload d'un répertoire
_IGNORE_DIRS = {
    ".git", "__pycache__", "node_modules", ".venv", "venv", "env",
    ".tox", "dist", "build", ".eggs", "*.egg-info",
}
_IGNORE_EXTS = {".pyc", ".pyo", ".class", ".o", ".so", ".dll", ".exe"}
_MAX_FILE_SIZE = 1_000_000  # 1 MB : limite GitHub API


def _get_token(ctx: HandlerContext) -> Optional[str]:
    """Récupère le token GitHub depuis l'env ou le registre de clés API."""
    token = os.getenv("GITHUB_TOKEN", "").strip()
    if token:
        return token
    # Tenter le registre de clés Lumena
    try:
        from ...utils.paths import DATA_DIR
        registry_path = DATA_DIR / "api_keys.json"
        if registry_path.exists():
            keys = json.loads(registry_path.read_text(encoding="utf-8"))
            for key_entry in keys if isinstance(keys, list) else []:
                if key_entry.get("service", "").lower() in ("github", "github_token"):
                    return key_entry.get("key", "").strip() or None
    except Exception as e:
        logger.debug(f"GitHub token lookup: {e}")
    return None


def _headers(token: str) -> Dict[str, str]:
    return {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


async def _gh_request(
    method: str,
    endpoint: str,
    token: str,
    payload: Optional[Dict] = None,
    params: Optional[Dict] = None,
) -> tuple[int, Any]:
    """Effectue un appel REST GitHub. Retourne (status_code, body_dict | str)."""
    import httpx

    url = endpoint if endpoint.startswith("http") else f"{_GH_API}{endpoint}"
    headers = _headers(token)
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.request(
            method.upper(),
            url,
            headers=headers,
            json=payload,
            params=params,
        )
    try:
        body = r.json()
    except Exception:
        body = r.text
    return r.status_code, body


def _raw_err(status: int, body: Any) -> str:
    msg = body.get("message", str(body)) if isinstance(body, dict) else str(body)
    return f"GitHub API {status} : {msg}"


# ─── Handler : lister ses repos ──────────────────────────────────────────────

async def github_repo_list_handler(
    ctx: HandlerContext,
    visibility: str = "all",
    sort: str = "updated",
    per_page: int = 30,
) -> HandlerResult:
    """Liste les repos du compte GitHub authentifié."""
    handler_name = "github_repo_list"
    token = _get_token(ctx)
    if not token:
        return HandlerResult.fail(
            "Token GitHub manquant. Définis GITHUB_TOKEN dans l'environnement.",
            handler_name=handler_name,
        )
    status, body = await _gh_request(
        "GET", "/user/repos", token,
        params={"visibility": visibility, "sort": sort, "per_page": min(per_page, 100)},
    )
    if status != 200:
        return HandlerResult.fail(_raw_err(status, body), handler_name=handler_name)

    repos = body if isinstance(body, list) else []
    lines = [f"**{len(repos)} repos** (tri: {sort}, visibilité: {visibility})\n"]
    for r in repos:
        priv = "🔒" if r.get("private") else "🌐"
        desc = r.get("description") or ""
        pushed = (r.get("pushed_at") or "")[:10]
        lines.append(f"{priv} `{r['full_name']}` — {desc} (maj: {pushed})")
    return HandlerResult.ok("\n".join(lines), handler_name=handler_name)


# ─── Handler : créer un repo ─────────────────────────────────────────────────

async def github_repo_create_handler(
    ctx: HandlerContext,
    name: str = "",
    description: str = "",
    private: bool = True,
    auto_init: bool = True,
) -> HandlerResult:
    """Crée un nouveau repo GitHub."""
    handler_name = "github_repo_create"
    if not name:
        return HandlerResult.fail("Paramètre `name` requis.", handler_name=handler_name)
    token = _get_token(ctx)
    if not token:
        return HandlerResult.fail("Token GitHub manquant.", handler_name=handler_name)

    status, body = await _gh_request(
        "POST", "/user/repos", token,
        payload={
            "name": name,
            "description": description,
            "private": private,
            "auto_init": auto_init,
        },
    )
    if status not in (200, 201):
        return HandlerResult.fail(_raw_err(status, body), handler_name=handler_name)
    url = body.get("html_url", "?")
    vis = "privé" if body.get("private") else "public"
    return HandlerResult.ok(
        f"✅ Repo **{body.get('full_name')}** créé ({vis}) : {url}",
        handler_name=handler_name,
    )


# ─── Handler : supprimer un repo ─────────────────────────────────────────────

async def github_repo_delete_handler(
    ctx: HandlerContext,
    owner: str = "",
    repo: str = "",
) -> HandlerResult:
    """Supprime un repo GitHub (opération irréversible)."""
    handler_name = "github_repo_delete"
    if not owner or not repo:
        return HandlerResult.fail("`owner` et `repo` requis.", handler_name=handler_name)
    token = _get_token(ctx)
    if not token:
        return HandlerResult.fail("Token GitHub manquant.", handler_name=handler_name)

    status, body = await _gh_request("DELETE", f"/repos/{owner}/{repo}", token)
    if status == 204:
        return HandlerResult.ok(
            f"🗑️ Repo `{owner}/{repo}` supprimé.", handler_name=handler_name
        )
    return HandlerResult.fail(_raw_err(status, body), handler_name=handler_name)


# ─── Handler : lire un fichier du repo ───────────────────────────────────────

async def github_file_read_handler(
    ctx: HandlerContext,
    owner: str = "",
    repo: str = "",
    path: str = "",
    ref: str = "",
) -> HandlerResult:
    """Lit le contenu d'un fichier depuis un repo GitHub. path='' liste la racine."""
    handler_name = "github_file_read"
    if not (owner and repo):
        missing = [p for p, v in [("owner", owner), ("repo", repo)] if not v]
        return HandlerResult.fail(
            f"Paramètres manquants : {', '.join(missing)}. "
            f"Tu dois fournir owner et repo (path optionnel, vide = racine du repo).\n"
            f"Exemple : github_file_read(owner='mon-user', repo='mon-repo', path='README.md')",
            handler_name=handler_name,
        )
    token = _get_token(ctx)
    if not token:
        return HandlerResult.fail("Token GitHub manquant.", handler_name=handler_name)

    params = {"ref": ref} if ref else {}
    status, body = await _gh_request("GET", f"/repos/{owner}/{repo}/contents/{path}", token, params=params)
    if status != 200:
        return HandlerResult.fail(_raw_err(status, body), handler_name=handler_name)

    if isinstance(body, list):
        # Répertoire — lister le contenu
        entries = [f"{'📁' if e['type']=='dir' else '📄'} {e['name']}" for e in body]
        return HandlerResult.ok("\n".join(entries), handler_name=handler_name)

    encoding = body.get("encoding", "")
    content_b64 = body.get("content", "")
    if encoding == "base64":
        try:
            content = base64.b64decode(content_b64).decode("utf-8", errors="replace")
        except Exception as e:
            return HandlerResult.fail(f"Décodage base64 impossible : {e}", handler_name=handler_name)
    else:
        content = content_b64

    sha = body.get("sha", "")
    size = body.get("size", 0)
    return HandlerResult.ok(
        f"**{path}** (SHA: `{sha[:7]}`, {size} octets)\n```\n{content}\n```",
        handler_name=handler_name,
    )


# ─── Handler : créer ou mettre à jour un fichier ─────────────────────────────

async def github_file_write_handler(
    ctx: HandlerContext,
    owner: str = "",
    repo: str = "",
    path: str = "",
    content: str = "",
    message: str = "",
    branch: str = "",
    sha: str = "",
) -> HandlerResult:
    """Crée ou met à jour un fichier dans un repo GitHub.

    Si le fichier existe déjà, `sha` est requis (récupéré via github_file_read).
    """
    handler_name = "github_file_write"
    if not (owner and repo and path and content):
        return HandlerResult.fail(
            "`owner`, `repo`, `path` et `content` requis.", handler_name=handler_name
        )
    token = _get_token(ctx)
    if not token:
        return HandlerResult.fail("Token GitHub manquant.", handler_name=handler_name)

    commit_msg = message or f"Update {path} via Lumena"
    payload: Dict[str, Any] = {
        "message": commit_msg,
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
    }
    if sha:
        payload["sha"] = sha
    if branch:
        payload["branch"] = branch

    # Si sha non fourni, tenter de récupérer automatiquement le SHA existant
    if not sha:
        s2, b2 = await _gh_request("GET", f"/repos/{owner}/{repo}/contents/{path}", token)
        if s2 == 200 and isinstance(b2, dict) and b2.get("sha"):
            payload["sha"] = b2["sha"]

    status, body = await _gh_request("PUT", f"/repos/{owner}/{repo}/contents/{path}", token, payload=payload)
    if status in (200, 201):
        commit_url = body.get("commit", {}).get("html_url", "")
        action = "mis à jour" if status == 200 else "créé"
        return HandlerResult.ok(
            f"✅ Fichier `{path}` {action}.\n{commit_url}",
            handler_name=handler_name,
        )
    return HandlerResult.fail(_raw_err(status, body), handler_name=handler_name)


# ─── Handler : supprimer un fichier ──────────────────────────────────────────

async def github_file_delete_handler(
    ctx: HandlerContext,
    owner: str = "",
    repo: str = "",
    path: str = "",
    message: str = "",
    sha: str = "",
    branch: str = "",
) -> HandlerResult:
    """Supprime un fichier d'un repo GitHub."""
    handler_name = "github_file_delete"
    if not (owner and repo and path):
        return HandlerResult.fail("`owner`, `repo` et `path` requis.", handler_name=handler_name)
    token = _get_token(ctx)
    if not token:
        return HandlerResult.fail("Token GitHub manquant.", handler_name=handler_name)

    # Récupérer le SHA si non fourni
    file_sha = sha
    if not file_sha:
        s2, b2 = await _gh_request("GET", f"/repos/{owner}/{repo}/contents/{path}", token)
        if s2 == 200 and isinstance(b2, dict):
            file_sha = b2.get("sha", "")
    if not file_sha:
        return HandlerResult.fail(
            "SHA du fichier introuvable. Fournis `sha` manuellement.", handler_name=handler_name
        )

    payload: Dict[str, Any] = {
        "message": message or f"Delete {path} via Lumena",
        "sha": file_sha,
    }
    if branch:
        payload["branch"] = branch
    status, body = await _gh_request("DELETE", f"/repos/{owner}/{repo}/contents/{path}", token, payload=payload)
    if status == 200:
        return HandlerResult.ok(f"🗑️ Fichier `{path}` supprimé.", handler_name=handler_name)
    return HandlerResult.fail(_raw_err(status, body), handler_name=handler_name)


# ─── Handler : recherche de code ─────────────────────────────────────────────

async def github_search_code_handler(
    ctx: HandlerContext,
    query: str = "",
    per_page: int = 10,
) -> HandlerResult:
    """Recherche du code sur GitHub (limité aux repos accessibles avec le token)."""
    handler_name = "github_search_code"
    if not query:
        return HandlerResult.fail("`query` requis.", handler_name=handler_name)
    token = _get_token(ctx)
    if not token:
        return HandlerResult.fail("Token GitHub manquant.", handler_name=handler_name)

    status, body = await _gh_request(
        "GET", "/search/code", token,
        params={"q": query, "per_page": min(per_page, 30)},
    )
    if status != 200:
        return HandlerResult.fail(_raw_err(status, body), handler_name=handler_name)

    items = body.get("items", []) if isinstance(body, dict) else []
    total = body.get("total_count", "?") if isinstance(body, dict) else "?"
    lines = [f"**{total} résultats** pour `{query}`\n"]
    for item in items:
        repo_name = item.get("repository", {}).get("full_name", "?")
        file_path = item.get("path", "?")
        url = item.get("html_url", "")
        lines.append(f"- `{repo_name}` / `{file_path}`\n  {url}")
    return HandlerResult.ok("\n".join(lines), handler_name=handler_name)


# ─── Handler : lister les issues ─────────────────────────────────────────────

async def github_issues_list_handler(
    ctx: HandlerContext,
    owner: str = "",
    repo: str = "",
    state: str = "open",
    per_page: int = 20,
) -> HandlerResult:
    """Liste les issues d'un repo GitHub."""
    handler_name = "github_issues_list"
    if not (owner and repo):
        return HandlerResult.fail("`owner` et `repo` requis.", handler_name=handler_name)
    token = _get_token(ctx)
    if not token:
        return HandlerResult.fail("Token GitHub manquant.", handler_name=handler_name)

    status, body = await _gh_request(
        "GET", f"/repos/{owner}/{repo}/issues", token,
        params={"state": state, "per_page": min(per_page, 100)},
    )
    if status != 200:
        return HandlerResult.fail(_raw_err(status, body), handler_name=handler_name)

    issues = body if isinstance(body, list) else []
    lines = [f"**{len(issues)} issues** ({state}) dans `{owner}/{repo}`\n"]
    for iss in issues:
        num = iss.get("number", "?")
        title = iss.get("title", "")
        author = iss.get("user", {}).get("login", "?")
        labels = ", ".join(l["name"] for l in iss.get("labels", []))
        label_str = f" [{labels}]" if labels else ""
        lines.append(f"- #{num} **{title}**{label_str} — par {author}")
    return HandlerResult.ok("\n".join(lines), handler_name=handler_name)


# ─── Handler : créer une issue ───────────────────────────────────────────────

async def github_issue_create_handler(
    ctx: HandlerContext,
    owner: str = "",
    repo: str = "",
    title: str = "",
    body: str = "",
    labels: Optional[List[str]] = None,
) -> HandlerResult:
    """Crée une issue dans un repo GitHub."""
    handler_name = "github_issue_create"
    if not (owner and repo and title):
        return HandlerResult.fail("`owner`, `repo` et `title` requis.", handler_name=handler_name)
    token = _get_token(ctx)
    if not token:
        return HandlerResult.fail("Token GitHub manquant.", handler_name=handler_name)

    payload: Dict[str, Any] = {"title": title, "body": body or ""}
    if labels:
        payload["labels"] = labels

    status, resp = await _gh_request("POST", f"/repos/{owner}/{repo}/issues", token, payload=payload)
    if status == 201:
        url = resp.get("html_url", "?")
        num = resp.get("number", "?")
        return HandlerResult.ok(
            f"✅ Issue #{num} créée : {url}", handler_name=handler_name
        )
    return HandlerResult.fail(_raw_err(status, resp), handler_name=handler_name)


# ─── Handler : uploader un répertoire ────────────────────────────────────────

def _should_ignore(rel_path: Path) -> bool:
    """Retourne True si le fichier/dossier doit être ignoré lors de l'upload."""
    parts = rel_path.parts
    for part in parts:
        if part in _IGNORE_DIRS or part.endswith(".egg-info"):
            return True
    if rel_path.suffix in _IGNORE_EXTS:
        return True
    return False


async def _sync_local_git_after_api_push(base: Path, owner: str, repo: str, branch: str) -> str:
    """Configure le remote git local après un push via l'API GitHub.
    Ajoute l'origin, fetch la branche et synchronise le checkout local
    pour que git push/pull fonctionnent lors des opérations suivantes.
    """
    if not (base / ".git").exists():
        return ""

    async def _g(*args: str) -> tuple[int, str]:
        p = await asyncio.create_subprocess_exec(
            "git", *args, cwd=base,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        o, e = await p.communicate()
        return p.returncode, (o + e).decode("utf-8", errors="replace").strip()

    url = f"https://github.com/{owner}/{repo}.git"

    # 1. Configurer l'origin (ou corriger si déjà existant)
    rc, out = await _g("remote", "add", "origin", url)
    if rc != 0 and "already exists" in out:
        await _g("remote", "set-url", "origin", url)

    # 2. Fetch shallow de la branche
    rc_f, _ = await _g("fetch", "--depth=1", "origin", branch)
    if rc_f != 0:
        return "\u26a0\ufe0f Origin configuré mais fetch échoué"

    # 3. Créer la branche locale trackant le remote
    #    -f : force même si des fichiers non-trackés (identiques au remote) sont en place
    rc_co, _ = await _g("checkout", "-b", branch, "-f", f"origin/{branch}")
    if rc_co != 0:
        # Branche locale déjà existante — configurer uniquement le tracking
        await _g("branch", f"--set-upstream-to=origin/{branch}", branch)

    return f"\U0001f517 Origin configuré \u2192 `{url}` (branche `{branch}` synchronisée)"


async def _push_directory_per_file(
    base: Path,
    to_upload: list,
    owner: str,
    repo: str,
    commit_message: str,
    branch: str,
    token: str,
    handler_name: str,
) -> HandlerResult:
    """Fallback : upload fichier par fichier via l'API Contents (un commit par fichier)."""
    uploaded: list[str] = []
    failed: list[str] = []
    for file_path, remote_path in to_upload:
        try:
            raw = file_path.read_bytes()
            content_b64 = base64.b64encode(raw).decode("ascii")
        except Exception as e:
            failed.append(f"{remote_path} (lecture : {e})")
            continue
        payload: Dict[str, Any] = {
            "message": f"{commit_message} : {remote_path}",
            "content": content_b64,
        }
        if branch:
            payload["branch"] = branch
        s_chk, b_chk = await _gh_request("GET", f"/repos/{owner}/{repo}/contents/{remote_path}", token)
        if s_chk == 200 and isinstance(b_chk, dict) and b_chk.get("sha"):
            payload["sha"] = b_chk["sha"]
        status, resp = await _gh_request("PUT", f"/repos/{owner}/{repo}/contents/{remote_path}", token, payload=payload)
        if status in (200, 201):
            uploaded.append(remote_path)
        else:
            failed.append(f"{remote_path} ({_raw_err(status, resp)})")
        await asyncio.sleep(0.3)
    lines = [f"**Upload terminé** vers `{owner}/{repo}` (mode: commit-par-fichier)"]
    lines.append(f"✅ {len(uploaded)} fichiers uploadés")
    if failed:
        lines.append(f"❌ {len(failed)} échecs :")
        lines.extend(f"  - {f}" for f in failed)
    # Synchroniser le dépôt git local avec le remote si l'upload a réussi
    if uploaded:
        sync_msg = await _sync_local_git_after_api_push(base, owner, repo, branch)
        if sync_msg:
            lines.append(sync_msg)
    return HandlerResult.ok("\n".join(lines), handler_name=handler_name)


async def github_push_directory_handler(
    ctx: HandlerContext,
    local_dir: str = "",
    owner: str = "",
    repo: str = "",
    remote_prefix: str = "",
    branch: str = "",
    commit_message: str = "",
    max_files: int = 200,
) -> HandlerResult:
    """Upload un dossier local vers un repo GitHub en un seul commit groupé
    via l'API Git Tree (évite le rate-limit sur les gros projets).
    Fallback automatique sur commit-par-fichier si la branche n'existe pas encore.
    """
    handler_name = "github_push_directory"
    if not (local_dir and owner and repo):
        return HandlerResult.fail(
            "`local_dir`, `owner` et `repo` requis.", handler_name=handler_name
        )
    token = _get_token(ctx)
    if not token:
        return HandlerResult.fail("Token GitHub manquant.", handler_name=handler_name)

    base = ctx.resolve_path(local_dir, want_dir=True)
    if not base.exists() or not base.is_dir():
        return HandlerResult.fail(f"Dossier introuvable : {base}", handler_name=handler_name)

    # Collecter les fichiers
    to_upload: list[tuple[Path, str]] = []
    for file_path in sorted(base.rglob("*")):
        if not file_path.is_file():
            continue
        rel = file_path.relative_to(base)
        if _should_ignore(rel):
            continue
        if file_path.stat().st_size > _MAX_FILE_SIZE:
            logger.debug("[github_push_dir] Fichier trop grand ignoré : {}", file_path)
            continue
        remote_path = f"{remote_prefix}/{rel}".lstrip("/") if remote_prefix else str(rel).replace("\\", "/")
        to_upload.append((file_path, remote_path))
        if len(to_upload) >= max_files:
            break

    ignored_count = 0
    if len(to_upload) >= max_files:
        # Compter combien de fichiers restent non uploadés
        total_eligible = sum(
            1 for f in base.rglob("*")
            if f.is_file() and not _should_ignore(f.relative_to(base)) and f.stat().st_size <= _MAX_FILE_SIZE
        )
        ignored_count = max(0, total_eligible - max_files)
        if ignored_count > 0:
            logger.warning("[github_push_dir] Troncature: {} fichiers ignorés (limite max_files={})", ignored_count, max_files)

    if not to_upload:
        return HandlerResult.ok("Aucun fichier à uploader (répertoire vide ou tout ignoré).", handler_name=handler_name)

    commit_msg = commit_message or f"Upload via Lumena — {base.name}"

    # ── Étape 1 : résoudre la branche et obtenir le SHA du commit parent ────────
    used_branch = branch
    if not used_branch:
        s, b = await _gh_request("GET", f"/repos/{owner}/{repo}", token)
        if s == 200 and isinstance(b, dict):
            used_branch = b.get("default_branch", "main")
        else:
            used_branch = "main"

    s_ref, b_ref = await _gh_request("GET", f"/repos/{owner}/{repo}/git/ref/heads/{used_branch}", token)
    if s_ref != 200:
        logger.warning("[github_push_dir] Branche '{}' inconnue → fallback commit-par-fichier", used_branch)
        return await _push_directory_per_file(base, to_upload, owner, repo, commit_msg, used_branch, token, handler_name)

    parent_sha = b_ref["object"]["sha"]

    # Récupérer le SHA de l'arbre du commit parent
    s_cmt0, b_cmt0 = await _gh_request("GET", f"/repos/{owner}/{repo}/git/commits/{parent_sha}", token)
    if s_cmt0 != 200 or not isinstance(b_cmt0, dict) or "tree" not in b_cmt0:
        logger.warning("[github_push_dir] Commit parent inaccessible → fallback commit-par-fichier")
        return await _push_directory_per_file(base, to_upload, owner, repo, commit_msg, used_branch, token, handler_name)

    base_tree_sha = b_cmt0["tree"]["sha"]

    # ── Étape 2 : créer les blobs pour chaque fichier ───────────────────────────
    tree_entries: list[Dict[str, Any]] = []
    failed: list[str] = []
    for file_path, remote_path in to_upload:
        try:
            raw = file_path.read_bytes()
            try:
                blob_payload: Dict[str, Any] = {"content": raw.decode("utf-8"), "encoding": "utf-8"}
            except UnicodeDecodeError:
                blob_payload = {"content": base64.b64encode(raw).decode("ascii"), "encoding": "base64"}
            s_blob, b_blob = await _gh_request(
                "POST", f"/repos/{owner}/{repo}/git/blobs", token, payload=blob_payload
            )
            if s_blob not in (200, 201) or not isinstance(b_blob, dict) or "sha" not in b_blob:
                failed.append(f"{remote_path} (blob: {_raw_err(s_blob, b_blob)})")
                continue
            tree_entries.append({"path": remote_path, "mode": "100644", "type": "blob", "sha": b_blob["sha"]})
        except Exception as e:
            failed.append(f"{remote_path} (lecture: {e})")

    if not tree_entries:
        return HandlerResult.fail(
            f"Aucun blob créé. Erreurs: {'; '.join(failed[:5])}", handler_name=handler_name
        )

    # ── Étape 3 : créer l'arbre ──────────────────────────────────────────────────
    s_tree, b_tree = await _gh_request(
        "POST", f"/repos/{owner}/{repo}/git/trees", token,
        payload={"base_tree": base_tree_sha, "tree": tree_entries},
    )
    if s_tree not in (200, 201) or not isinstance(b_tree, dict) or "sha" not in b_tree:
        logger.warning("[github_push_dir] Création d'arbre échouée → fallback commit-par-fichier")
        return await _push_directory_per_file(base, to_upload, owner, repo, commit_msg, used_branch, token, handler_name)

    # ── Étape 4 : créer le commit ────────────────────────────────────────────────
    s_cmt, b_cmt = await _gh_request(
        "POST", f"/repos/{owner}/{repo}/git/commits", token,
        payload={"message": commit_msg, "tree": b_tree["sha"], "parents": [parent_sha]},
    )
    if s_cmt not in (200, 201) or not isinstance(b_cmt, dict) or "sha" not in b_cmt:
        return HandlerResult.fail(
            f"Création de commit échouée: {_raw_err(s_cmt, b_cmt)}", handler_name=handler_name
        )

    # ── Étape 5 : mettre à jour la référence de branche ─────────────────────────
    s_upd, b_upd = await _gh_request(
        "PATCH", f"/repos/{owner}/{repo}/git/refs/heads/{used_branch}", token,
        payload={"sha": b_cmt["sha"], "force": False},
    )
    if s_upd not in (200, 201):
        return HandlerResult.fail(
            f"Mise à jour branche échouée: {_raw_err(s_upd, b_upd)}", handler_name=handler_name
        )

    lines = [f"**Upload terminé** vers `{owner}/{repo}` (branche: `{used_branch}`)"]
    lines.append(f"✅ {len(tree_entries)} fichiers en **1 commit** : `{b_cmt['sha'][:7]}`")
    if failed:
        lines.append(f"❌ {len(failed)} fichiers ignorés :")
        lines.extend(f"  - {f}" for f in failed[:10])
    if ignored_count > 0:
        lines.append(f"⚠️ {ignored_count} fichiers supplémentaires ignorés (limite max_files={max_files})")

    # P5.4: auto-skill extraction après push réussi
    try:
        from ...skills import get_skill_loader
        loader = get_skill_loader()
        if loader and hasattr(loader, "record_project_push"):
            await loader.record_project_push(
                repo=f"{owner}/{repo}",
                branch=used_branch,
                file_count=len(tree_entries),
                commit_sha=b_cmt["sha"][:7],
            )
    except Exception:
        pass  # non-bloquant, skill extraction is best-effort

    return HandlerResult.ok("\n".join(lines), handler_name=handler_name)


# ─── Registration ─────────────────────────────────────────────────────────────

def get_github_handler_defs() -> List[HandlerDef]:
    """Retourne les définitions des handlers GitHub pour le registre V2."""
    return [
        HandlerDef(
            name="github_repo_list",
            description=(
                "Liste les repos GitHub du compte authentifié. "
                "Utiliser pour : 'montre mes repos GitHub', 'liste mes projets GitHub', "
                "'quels sont mes repos'."
            ),
            parameters={
                "properties": {
                    "visibility": {
                        "type": "string",
                        "description": "all | public | private (défaut: all)",
                    },
                    "sort": {
                        "type": "string",
                        "description": "Tri : updated | created | pushed | full_name (défaut: updated)",
                    },
                    "per_page": {
                        "type": "integer",
                        "description": "Nombre de repos à retourner (max 100, défaut: 30)",
                    },
                },
                "required": [],
            },
            handler=github_repo_list_handler,
            category="github",
            source_module="handlers.github",
        ),
        HandlerDef(
            name="github_repo_create",
            description=(
                "Crée un nouveau repo GitHub. "
                "Utiliser pour : 'crée un repo GitHub', 'crée un nouveau projet GitHub', "
                "'publie ce projet sur GitHub'."
            ),
            parameters={
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Nom du repo (sans espaces)",
                    },
                    "description": {
                        "type": "string",
                        "description": "Description du repo (optionnel)",
                    },
                    "private": {
                        "type": "boolean",
                        "description": "Repo privé (défaut: true)",
                    },
                    "auto_init": {
                        "type": "boolean",
                        "description": "Initialiser avec un README (défaut: true)",
                    },
                },
                "required": ["name"],
            },
            handler=github_repo_create_handler,
            category="github",
            source_module="handlers.github",
        ),
        HandlerDef(
            name="github_repo_delete",
            description=(
                "Supprime définitivement un repo GitHub (irréversible). "
                "Utiliser uniquement si l'utilisateur confirme explicitement la suppression."
            ),
            parameters={
                "properties": {
                    "owner": {"type": "string", "description": "Propriétaire du repo"},
                    "repo": {"type": "string", "description": "Nom du repo"},
                },
                "required": ["owner", "repo"],
            },
            handler=github_repo_delete_handler,
            category="github",
            source_module="handlers.github",
        ),
        HandlerDef(
            name="github_file_read",
            description=(
                "Lit le contenu d'un fichier (ou liste un répertoire) dans un repo GitHub. "
                "Utiliser pour : 'lis le fichier X dans le repo Y', 'montre-moi le README de ce repo', "
                "'accède au code de ce fichier GitHub'."
            ),
            parameters={
                "properties": {
                    "owner": {"type": "string", "description": "Propriétaire du repo"},
                    "repo": {"type": "string", "description": "Nom du repo"},
                    "path": {"type": "string", "description": "Chemin du fichier dans le repo"},
                    "ref": {
                        "type": "string",
                        "description": "Branche, tag ou commit SHA (optionnel, défaut: branche par défaut)",
                    },
                },
                "required": ["owner", "repo", "path"],
            },
            handler=github_file_read_handler,
            category="github",
            source_module="handlers.github",
        ),
        HandlerDef(
            name="github_file_write",
            description=(
                "Crée ou met à jour un fichier dans un repo GitHub. "
                "Si le fichier existe déjà, le SHA est récupéré automatiquement. "
                "Utiliser pour : 'publie ce fichier sur GitHub', 'met à jour src/main.py dans mon repo', "
                "'ajoute ce fichier au repo'."
            ),
            parameters={
                "properties": {
                    "owner": {"type": "string", "description": "Propriétaire du repo"},
                    "repo": {"type": "string", "description": "Nom du repo"},
                    "path": {"type": "string", "description": "Chemin cible dans le repo"},
                    "content": {"type": "string", "description": "Contenu du fichier (texte)"},
                    "message": {
                        "type": "string",
                        "description": "Message de commit (optionnel)",
                    },
                    "branch": {
                        "type": "string",
                        "description": "Branche cible (optionnel, défaut: branche par défaut du repo)",
                    },
                    "sha": {
                        "type": "string",
                        "description": "SHA actuel du fichier si connu (optionnel, récupéré automatiquement si absent)",
                    },
                },
                "required": ["owner", "repo", "path", "content"],
            },
            handler=github_file_write_handler,
            category="github",
            source_module="handlers.github",
        ),
        HandlerDef(
            name="github_file_delete",
            description=(
                "Supprime un fichier d'un repo GitHub. "
                "Utiliser pour : 'supprime ce fichier du repo', 'efface src/old.py de GitHub'."
            ),
            parameters={
                "properties": {
                    "owner": {"type": "string", "description": "Propriétaire du repo"},
                    "repo": {"type": "string", "description": "Nom du repo"},
                    "path": {"type": "string", "description": "Chemin du fichier à supprimer"},
                    "message": {"type": "string", "description": "Message de commit (optionnel)"},
                    "sha": {
                        "type": "string",
                        "description": "SHA du fichier (optionnel, récupéré automatiquement si absent)",
                    },
                    "branch": {"type": "string", "description": "Branche cible (optionnel)"},
                },
                "required": ["owner", "repo", "path"],
            },
            handler=github_file_delete_handler,
            category="github",
            source_module="handlers.github",
        ),
        HandlerDef(
            name="github_search_code",
            description=(
                "Cherche du code sur GitHub (dans les repos accessibles avec le token). "
                "Utiliser pour : 'cherche ce pattern dans GitHub', 'trouve des exemples de X sur GitHub', "
                "'recherche ce code dans mes repos'."
            ),
            parameters={
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "Requête de recherche GitHub (ex: 'repo:owner/name function_name', "
                            "'language:python asyncio.gather')"
                        ),
                    },
                    "per_page": {
                        "type": "integer",
                        "description": "Nombre de résultats (max 30, défaut: 10)",
                    },
                },
                "required": ["query"],
            },
            handler=github_search_code_handler,
            category="github",
            source_module="handlers.github",
        ),
        HandlerDef(
            name="github_issues_list",
            description=(
                "Liste les issues d'un repo GitHub. "
                "Utiliser pour : 'quelles sont les issues ouvertes ?', 'montre-moi les bugs GitHub', "
                "'liste les issues de ce projet'."
            ),
            parameters={
                "properties": {
                    "owner": {"type": "string", "description": "Propriétaire du repo"},
                    "repo": {"type": "string", "description": "Nom du repo"},
                    "state": {
                        "type": "string",
                        "description": "open | closed | all (défaut: open)",
                    },
                    "per_page": {
                        "type": "integer",
                        "description": "Nombre d'issues (max 100, défaut: 20)",
                    },
                },
                "required": ["owner", "repo"],
            },
            handler=github_issues_list_handler,
            category="github",
            source_module="handlers.github",
        ),
        HandlerDef(
            name="github_issue_create",
            description=(
                "Crée une issue dans un repo GitHub. "
                "Utiliser pour : 'crée une issue', 'ouvre un bug report sur GitHub', "
                "'ajoute une tâche dans le tracker GitHub'."
            ),
            parameters={
                "properties": {
                    "owner": {"type": "string", "description": "Propriétaire du repo"},
                    "repo": {"type": "string", "description": "Nom du repo"},
                    "title": {"type": "string", "description": "Titre de l'issue"},
                    "body": {"type": "string", "description": "Corps de l'issue (markdown, optionnel)"},
                    "labels": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Liste de labels à appliquer (optionnel)",
                    },
                },
                "required": ["owner", "repo", "title"],
            },
            handler=github_issue_create_handler,
            category="github",
            source_module="handlers.github",
        ),
        HandlerDef(
            name="github_push_directory",
            description=(
                "Upload un dossier local complet vers un repo GitHub (multi-fichiers). "
                "Ignore automatiquement .git, __pycache__, node_modules, .venv, etc. "
                "Utiliser pour : 'publie tout mon projet sur GitHub', "
                "'upload ce dossier dans mon repo', 'pousse ce code sur GitHub'."
            ),
            parameters={
                "properties": {
                    "local_dir": {
                        "type": "string",
                        "description": "Chemin du dossier local à uploader",
                    },
                    "owner": {"type": "string", "description": "Propriétaire du repo cible"},
                    "repo": {"type": "string", "description": "Nom du repo cible"},
                    "remote_prefix": {
                        "type": "string",
                        "description": "Préfixe du chemin dans le repo (optionnel, ex: 'src')",
                    },
                    "branch": {
                        "type": "string",
                        "description": "Branche cible (optionnel)",
                    },
                    "commit_message": {
                        "type": "string",
                        "description": "Message de commit de base (optionnel)",
                    },
                    "max_files": {
                        "type": "integer",
                        "description": "Nombre max de fichiers à uploader (défaut: 50)",
                    },
                },
                "required": ["local_dir", "owner", "repo"],
            },
            handler=github_push_directory_handler,
            category="github",
            source_module="handlers.github",
        ),
    ]
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU General Public License v3.0 (GPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
