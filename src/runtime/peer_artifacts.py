"""Brique 4 — Artefacts inter-Lumena : manifeste, empaquetage, réception sandboxée.

Flux : une mission produit des fichiers (dans le workspace de B) → on en construit
un **manifeste** (nom, taille, sha256, mime) → on **empaquette** (ZIP si ≥2 fichiers,
brut si 1) → B sert les bytes (endpoints signés A2) → A **télécharge, vérifie le
hash, valide (anti zip-slip), et place dans son workspace** :
`workspace/inbound/<peer>/<task>/…`. **Jamais auto-exécuté.**

Sécurité de réception (réutilise l'esprit de `skills.loader._validate_skill_archive`):
taille bornée, nombre de fichiers borné, pas de chemin absolu ni `..`, extensions
exécutables bloquées, anti zip-slip strict.
"""
from __future__ import annotations

import hashlib
import io
import mimetypes
import os
import zipfile
from pathlib import Path, PurePosixPath
from typing import List, Optional

# ── Limites ───────────────────────────────────────────────────────────────────
MAX_ARTIFACT_FILES = 500
MAX_ARTIFACT_FILE_SIZE = 50 * 1024 * 1024     # 50 Mo / fichier
MAX_ARTIFACT_TOTAL_SIZE = 200 * 1024 * 1024   # 200 Mo total

# N'importe quel TYPE de fichier peut transiter (décision utilisateur) : on
# n'exécute JAMAIS un fichier reçu, et c'est la flotte du propriétaire. Le
# blocage d'extensions « exécutables » est donc **désactivé par défaut**, et
# réactivable via `LUMENA_PEER_ARTIFACT_BLOCK_EXEC=1` (défense en profondeur
# optionnelle). Les protections d'intégrité (hash, anti zip-slip, taille) sont,
# elles, TOUJOURS actives.
BLOCKED_ARTIFACT_EXTENSIONS = frozenset({
    ".exe", ".bat", ".cmd", ".com", ".scr", ".msi", ".dll", ".sys",
    ".ps1", ".psm1", ".vbs", ".vbe", ".jar", ".sh", ".bash", ".app",
    ".deb", ".rpm", ".pkg", ".lnk", ".reg",
})


def _exec_blocking_enabled() -> bool:
    return os.getenv("LUMENA_PEER_ARTIFACT_BLOCK_EXEC", "0").strip() == "1"


def _is_blocked_extension(name: str) -> bool:
    """True seulement si l'option de blocage est active ET l'extension listée."""
    if not _exec_blocking_enabled():
        return False
    return Path(name).suffix.lower() in BLOCKED_ARTIFACT_EXTENSIONS


# ── Hash ──────────────────────────────────────────────────────────────────────

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ── Capture des fichiers produits (depuis l'historique ReAct, côté B) ─────────

# Outils qui CRÉENT/écrivent des fichiers (liste blanche → pas de faux positifs
# comme read_file/list_directory). C'est le « hook explicite » du manifeste.
_FILE_CREATING_TOOLS = frozenset({
    "write_file", "str_replace", "multi_edit_file", "insert_at_anchor",
    "create_pdf", "create_docx", "create_xlsx", "create_pptx", "create_zip",
    "create_vcard", "create_meeting_report", "create_batch_documents",
    "save_template", "export_website_zip", "fill_pdf_form", "watermark_pdf",
    "sign_pdf", "protect_pdf", "image_to_pdf", "merge_pdf",
})

_PATH_ARG_KEYS = ("path", "file_path", "output_path", "output_filename", "filename", "out_path")

import re as _re
_ABS_PATH_RE = _re.compile(r'(?:[A-Za-z]:\\[^\s"\'<>|]+|/[^\s"\'<>|]+\.[A-Za-z0-9]{1,8})')


def snapshot_workspace(base_dir: Path) -> dict:
    """Empreinte `{chemin_absolu: (taille, mtime_ns)}` de tous les fichiers du workspace.

    Sert au **diff avant/après mission** : on ne devine plus *quel outil* crée des
    fichiers (liste blanche fragile : ratait `parallel_tools`, `create_project`…),
    on regarde le **disque**. Marche quel que soit l'outil (write_file, CodeAgent,
    MCP, futur outil). Le dossier `inbound/` (artefacts REÇUS d'autres pairs) est
    exclu pour ne jamais re-transférer ce qu'on a reçu.
    """
    base = Path(base_dir).resolve()
    snap: dict = {}
    if not base.exists():
        return snap
    for root, dirs, files in os.walk(base):
        rp = Path(root)
        # Exclut le sous-arbre `inbound/` (artefacts REÇUS) — pruning par nom au
        # niveau racine, robuste (pas de comparaison de chemins résolus).
        if rp.resolve() == base and "inbound" in dirs:
            dirs.remove("inbound")
        for fn in files:
            p = (rp / fn).resolve()
            try:
                st = p.stat()
            except OSError:
                continue
            snap[str(p)] = (st.st_size, st.st_mtime_ns)
    return snap


def diff_workspace(before: dict, after: dict) -> List[str]:
    """Fichiers **nouveaux ou modifiés** (taille/mtime) entre deux snapshots.

    = exactement les livrables produits pendant la fenêtre d'exécution de la
    mission (worker concurrence=1 → pas d'interférence).
    """
    out: List[str] = []
    for path, sig in (after or {}).items():
        if (before or {}).get(path) != sig:
            out.append(path)
    return out


def extract_created_files(history, *, base_dir: Path) -> List[str]:
    """Liste des fichiers PRODUITS par une mission, depuis l'historique ReAct.

    Ne considère que les **outils créateurs** (liste blanche), collecte les
    chemins (args + texte d'observation), et **ne garde que les fichiers
    existants sous `base_dir`** (le workspace) → précis, sans parasites.
    """
    base = base_dir.resolve()
    candidates: set[str] = set()
    for step in history or []:
        action = getattr(step, "action", None)
        if action is None:
            continue
        name = str(getattr(action, "tool_name", "") or "")
        if name not in _FILE_CREATING_TOOLS:
            continue
        args = getattr(action, "tool_args", None) or {}
        if isinstance(args, dict):
            for k in _PATH_ARG_KEYS:
                v = args.get(k)
                if isinstance(v, str) and v.strip():
                    candidates.add(v.strip())
        obs = getattr(getattr(step, "observation", None), "content", "") or ""
        if obs:
            for m in _ABS_PATH_RE.findall(obs):
                candidates.add(m.strip().strip('".,;)'))

    out: List[str] = []
    seen: set[str] = set()
    for c in candidates:
        try:
            rp = Path(c)
            if not rp.is_absolute():
                rp = base / c
            rp = rp.resolve()
        except Exception:
            continue
        key = str(rp)
        if key in seen:
            continue
        # garder seulement les fichiers existants SOUS le workspace
        if rp.is_file() and (rp == base or base in rp.parents):
            seen.add(key)
            out.append(key)
    return out


# ── Manifeste (côté producteur B) ─────────────────────────────────────────────

def build_manifest(file_paths: List[str], *, base_dir: Path) -> List[dict]:
    """Construit le manifeste des fichiers produits (existants, dédupliqués).

    `rel_path` est relatif à `base_dir` (le workspace) quand possible, pour
    préserver l'arborescence (un site = dossiers). Le champ interne `_abs`
    (chemin absolu) sert localement et est retiré avant l'envoi réseau
    (`public_manifest`).
    """
    base = base_dir.resolve()
    manifest: List[dict] = []
    seen: set[str] = set()
    for raw in file_paths or []:
        if not raw:
            continue
        try:
            rp = Path(raw).resolve()
        except Exception:
            continue
        if not rp.is_file():
            continue
        key = str(rp)
        if key in seen:
            continue
        seen.add(key)
        try:
            rel = rp.relative_to(base)
        except Exception:
            rel = Path(rp.name)
        manifest.append({
            "artifact_id": hashlib.sha1(key.encode("utf-8")).hexdigest()[:12],
            "filename": rp.name,
            "rel_path": str(rel).replace("\\", "/"),
            "size": rp.stat().st_size,
            "sha256": sha256_file(rp),
            "mime": mimetypes.guess_type(rp.name)[0] or "application/octet-stream",
            "_abs": str(rp),
        })
    return manifest


def public_manifest(manifest: List[dict]) -> List[dict]:
    """Manifeste épuré pour le réseau (sans chemin absolu interne)."""
    return [{k: v for k, v in m.items() if k != "_abs"} for m in (manifest or [])]


# ── Empaquetage (côté B) ──────────────────────────────────────────────────────

def prepare_bundle(manifest: List[dict], *, task_id: str, out_dir: Path) -> Optional[dict]:
    """Prépare le bundle à servir : **ZIP si ≥2 fichiers, brut si 1**.

    Retourne `{kind, path, filename, sha256, count}` ou None si rien à envoyer.
    """
    files = [m for m in (manifest or []) if m.get("_abs") and Path(m["_abs"]).is_file()]
    if not files:
        return None
    if len(files) == 1:
        src = Path(files[0]["_abs"])
        return {"kind": "raw", "path": str(src), "filename": src.name,
                "sha256": files[0]["sha256"], "count": 1}
    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / f"{task_id}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for m in files:
            zf.write(m["_abs"], arcname=m.get("rel_path") or Path(m["_abs"]).name)
    return {"kind": "zip", "path": str(zip_path), "filename": zip_path.name,
            "sha256": sha256_file(zip_path), "count": len(files)}


# ── Réception sandboxée (côté A) → workspace ──────────────────────────────────

def _safe_member(name: str) -> Optional[str]:
    """Valide un nom de membre d'archive (anti zip-slip / extension). None si refusé."""
    member = (name or "").replace("\\", "/")
    if not member or member.startswith("/"):
        return None
    parts = PurePosixPath(member).parts
    if not parts or any(p in ("", ".", "..") for p in parts):
        return None
    if _is_blocked_extension(parts[-1]):
        return None
    return member


def inbound_dir_for(peer_id: str, task_id: str) -> Path:
    """`workspace/inbound/<peer>/<task>/` — back-compat (ancien schéma à codes)."""
    from src.utils.paths import WORKSPACE_DIR
    safe_peer = "".join(c for c in (peer_id or "unknown") if c.isalnum() or c in "-_")[:40] or "unknown"
    safe_task = "".join(c for c in (task_id or "task") if c.isalnum() or c in "-_")[:40] or "task"
    return WORKSPACE_DIR / "inbound" / safe_peer / safe_task


def reception_dir_for(peer_name: str) -> Path:
    """`workspace/recu-de-<pair>/` — réception LISIBLE (nom du pair, pas d'UUID/code).

    Les fichiers reçus se rangent comme un projet normal du workspace, retrouvable
    par `find_project` (cf. `_register_received_projects`). Fini les chemins à codes.
    """
    from src.utils.paths import WORKSPACE_DIR
    slug = "".join(c if (c.isalnum() or c in "-_") else "-" for c in (peer_name or "pair").strip().lower())
    slug = slug.strip("-")[:40] or "pair"
    return WORKSPACE_DIR / f"recu-de-{slug}"


_DATE_SEG_RE = _re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _strip_routing_prefix(member: str) -> str:
    """Retire le routage INTERNE du pair (`<date>/projet-…/`) du chemin reçu.

    Côté producteur, les fichiers sont sous `workspace/<date>/projet-mission-…/`.
    À la réception on ne garde que l'arborescence utile (ex. `wok-nomade/index.html`)
    pour que ça reste lisible et trouvable.
    """
    parts = [p for p in (member or "").replace("\\", "/").split("/") if p not in ("", ".")]
    while parts and (_DATE_SEG_RE.match(parts[0]) or parts[0].lower().startswith("projet-")):
        parts.pop(0)
    if not parts:
        tail = (member or "").replace("\\", "/").rstrip("/").split("/")
        return tail[-1] if tail else member
    return "/".join(parts)


def receive_artifact(
    content: bytes,
    *,
    kind: str,
    filename: str,
    expected_sha256: str,
    dest_dir: Path,
) -> dict:
    """Valide puis **place** les fichiers dans `dest_dir` (sous le workspace).

    Vérifie le hash de l'archive/du fichier, refuse les membres dangereux
    (anti zip-slip, extensions bloquées), borne taille et nombre. Ne lance RIEN.
    Retourne `{ok, files, count, dest}` ou `{ok: False, error}`.
    """
    if expected_sha256 and sha256_bytes(content) != expected_sha256:
        return {"ok": False, "error": "hash_mismatch"}
    if len(content) > MAX_ARTIFACT_TOTAL_SIZE:
        return {"ok": False, "error": "too_large"}

    base = dest_dir.resolve()
    base.mkdir(parents=True, exist_ok=True)
    written: List[str] = []

    if kind == "zip":
        try:
            zf = zipfile.ZipFile(io.BytesIO(content))
        except zipfile.BadZipFile:
            return {"ok": False, "error": "bad_zip"}
        with zf:
            infos = [i for i in zf.infolist() if not i.is_dir()]
            if len(infos) > MAX_ARTIFACT_FILES:
                return {"ok": False, "error": "too_many_files"}
            total = 0
            for info in infos:
                if _safe_member(_strip_routing_prefix(info.filename)) is None:
                    return {"ok": False, "error": f"unsafe_member:{info.filename[:60]}"}
                fsize = int(info.file_size or 0)
                total += fsize
                if fsize > MAX_ARTIFACT_FILE_SIZE or total > MAX_ARTIFACT_TOTAL_SIZE:
                    return {"ok": False, "error": "too_large"}
            for info in infos:
                safe = _safe_member(_strip_routing_prefix(info.filename))
                target = (base / safe).resolve()
                if base != target and base not in target.parents:
                    return {"ok": False, "error": "zip_slip"}
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as srcf, open(target, "wb") as out:
                    out.write(srcf.read())
                written.append(str(target))
    else:  # brut (1 fichier)
        safe_name = Path(filename or "fichier.bin").name
        if not safe_name or _is_blocked_extension(safe_name):
            return {"ok": False, "error": "blocked_extension"}
        if len(content) > MAX_ARTIFACT_FILE_SIZE:
            return {"ok": False, "error": "too_large"}
        target = (base / safe_name).resolve()
        if base != target.parent and base not in target.parents:
            return {"ok": False, "error": "path_escape"}
        with open(target, "wb") as out:
            out.write(content)
        written.append(str(target))

    return {"ok": True, "files": written, "count": len(written), "dest": str(base)}
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
