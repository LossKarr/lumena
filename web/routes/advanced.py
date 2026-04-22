"""Advanced feature routes: tools, emotions, hooks, training, journal, logs, voice."""
from __future__ import annotations
import asyncio
import json as _json
import socket
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException

from web.routes import deps

from src.utils.paths import ROOT_DIR, DATA_DIR, LOGS_DIR, JOURNAL_JSON, WORKSPACE_DIR, TRAINING_POOL_DIR, TRAINING_VALIDATED_DIR

# ─── Workspace live-serve registry ───────────────────────────────────────────
# slug -> {process: asyncio.Process, port: int, url: str, path: str}
_SERVING_WORKSPACES: Dict[str, Any] = {}


def _find_free_port() -> int:
    """Retourne un port TCP libre sur 127.0.0.1."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _serve_workspace_dir(ws_path: str, slug: str) -> Dict[str, Any]:
    """Lance python -m http.server sur ws_path et retourne {url, port, slug, path}."""
    if slug in _SERVING_WORKSPACES:
        return _SERVING_WORKSPACES[slug]
    port = _find_free_port()
    proc = await asyncio.create_subprocess_exec(
        "python", "-m", "http.server", str(port), "--directory", ws_path,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    info: Dict[str, Any] = {
        "process": proc, "port": port,
        "url": f"http://localhost:{port}",
        "slug": slug, "path": ws_path,
    }
    _SERVING_WORKSPACES[slug] = info
    return info

_PROJECT_ROOT = ROOT_DIR

router = APIRouter()


@router.get("/api/tools", dependencies=[Depends(deps.verify_admin_token)])
async def get_tools():
    """Retourne la liste des outils disponibles."""
    if not deps.lumena or not deps.lumena.tool_system:
        raise HTTPException(status_code=503, detail="ToolSystem not available")

    try:
        tools = list(deps.lumena.tool_system._iter_all_tools())
        return {
            "count": len(tools),
            "tools": [
                {
                    "name": name,
                    "description": desc[:100] + "..." if len(desc) > 100 else desc
                }
                for name, desc, _params in tools
            ]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/emotions", dependencies=[Depends(deps.verify_admin_token)])
async def get_emotions():
    """Retourne l'etat emotionnel de Lumena."""
    if not deps.lumena or not deps.lumena.emotion_manager:
        raise HTTPException(status_code=503, detail="EmotionManager not available")

    try:
        stats = deps.lumena.emotion_manager.get_stats()
        return {
            "current_mood": stats.get("mood", "neutral"),
            "energy": stats.get("energy", "medium"),
            "emotions": {
                "happiness": stats.get("happiness", 50),
                "curiosity": stats.get("curiosity", 50),
                "excitement": stats.get("excitement", 40),
                "boredom": stats.get("boredom", 0),
                "tiredness": stats.get("tiredness", 0),
                "pride": stats.get("pride", 30),
            },
            "stats": {
                "compliments_received": stats.get("compliments_received", 0),
                "tasks_completed": stats.get("tasks_completed", 0),
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/hooks", dependencies=[Depends(deps.verify_admin_token)])
async def get_hooks():
    """Retourne les hooks enregistres."""
    if not deps.lumena or not deps.lumena.hook_system:
        raise HTTPException(status_code=503, detail="HookSystem not available")

    try:
        all_hooks = deps.lumena.hook_system.get_hooks()
        return {
            "count": len(all_hooks),
            "hooks": [
                {
                    "name": h.name,
                    "event": h.event.value,
                    "enabled": h.enabled,
                    "priority": h.priority,
                    "description": h.description
                }
                for h in all_hooks
            ]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/training")
async def get_training(_auth=Depends(deps.verify_admin_token)):
    """Stats sur les datasets d'entrainement (pool + validés)."""
    datasets = []
    for folder_name, folder in [("training_pool", TRAINING_POOL_DIR), ("training_validated", TRAINING_VALIDATED_DIR)]:
        if not folder.exists():
            continue
        for f in sorted(folder.iterdir()):
            if f.is_file() and f.suffix in (".json", ".jsonl"):
                size = f.stat().st_size
                count = 0
                try:
                    if f.suffix == ".jsonl":
                        lines = [l for l in f.read_text(encoding="utf-8", errors="replace").splitlines() if l.strip()]
                        count = len(lines)
                    else:
                        raw = _json.loads(f.read_text(encoding="utf-8", errors="replace"))
                        count = len(raw) if isinstance(raw, list) else 1
                except Exception:
                    pass
                datasets.append({
                    "folder": folder_name,
                    "name": f.name,
                    "size_bytes": size,
                    "entries": count,
                    "modified": f.stat().st_mtime,
                })
    datasets.sort(key=lambda x: x["modified"], reverse=True)
    total_conversations = sum(d["entries"] for d in datasets)
    pool_size_bytes = sum(d["size_bytes"] for d in datasets if d["folder"] == "training_pool")
    validated_size_bytes = sum(d["size_bytes"] for d in datasets if d["folder"] == "training_validated")
    # Pool stats from conversation logger
    pool_stats = {}
    try:
        from src.learning.conversation_logger import get_pool_stats
        pool_stats = get_pool_stats()
    except Exception:
        pass

    return {
        "success": True,
        "datasets": datasets,
        "total": len(datasets),
        "total_conversations": total_conversations,
        "pool_size_bytes": pool_size_bytes,
        "validated_size_bytes": validated_size_bytes,
        "pool_stats": pool_stats,
    }


@router.get("/api/logs/recent")
async def get_recent_logs(lines: int = 150, _auth=Depends(deps.verify_admin_token)):
    """Derniere N lignes du log Lumena le plus recent (sans pollution tests)."""
    logs_dir = LOGS_DIR
    if not logs_dir.exists():
        return {"success": True, "file": None, "lines": []}
    log_files = sorted(logs_dir.glob("*.log"), key=lambda f: f.stat().st_mtime)
    if not log_files:
        return {"success": True, "file": None, "lines": []}
    latest = log_files[-1]
    try:
        all_lines = latest.read_text(encoding="utf-8", errors="replace").splitlines()
        # Filter out test-related log lines (pytest writes to same log)
        _TEST_MARKERS = ("pytest-of", "pytest-", "\\Temp\\2\\pytest", "/tmp/pytest",
                         "test_build_runs", "test_get_stats", "test_get_too",
                         "test_search_symbol", "test_get_file_context", "test_refresh",
                         "test_empty_dir", "test_get_compact_map")
        filtered = [l for l in all_lines if not any(m in l for m in _TEST_MARKERS)]
        tail = filtered[-lines:]
        return {"success": True, "file": latest.name, "total_lines": len(filtered), "lines": tail}
    except Exception as e:
        return {"success": True, "file": latest.name, "lines": [], "error": str(e)}


@router.get("/api/journal")
async def get_journal(limit: int = 100, type: str = "", _auth=Depends(deps.verify_admin_token)):
    """Journal d'activite de Lumena (actions, pensees, apprentissages)."""
    journal_path = JOURNAL_JSON
    if not journal_path.exists():
        return {"success": True, "entries": [], "total": 0}
    try:
        entries = _json.loads(journal_path.read_text(encoding="utf-8", errors="replace"))
        if type:
            entries = [e for e in entries if e.get("type", "") == type]
        entries = entries[-limit:][::-1]
        return {"success": True, "entries": entries, "total": len(entries)}
    except Exception as e:
        return {"success": True, "entries": [], "total": 0, "error": str(e)}


# ─── Workspace live preview ───────────────────────────────────────────────────

def _find_workspace_dir(slug: str) -> Path | None:
    """Cherche le répertoire d'un workspace par son slug dans WORKSPACE_DIR."""
    if not WORKSPACE_DIR.exists():
        return None
    for date_dir in sorted(WORKSPACE_DIR.iterdir(), reverse=True):
        candidate = date_dir / slug
        if candidate.is_dir():
            return candidate
    return None


@router.post("/api/workspaces/{slug}/serve")
async def serve_workspace(slug: str, _auth=Depends(deps.verify_admin_token)):
    """Lance un serveur HTTP local pour le workspace donné."""
    if slug in _SERVING_WORKSPACES:
        info = _SERVING_WORKSPACES[slug]
        return {"url": info["url"], "port": info["port"], "slug": slug}
    ws_dir = _find_workspace_dir(slug)
    if ws_dir is None:
        raise HTTPException(status_code=404, detail=f"Workspace '{slug}' introuvable")
    info = await _serve_workspace_dir(str(ws_dir), slug)
    return {"url": info["url"], "port": info["port"], "slug": slug}


@router.delete("/api/workspaces/{slug}/serve")
async def stop_serve_workspace(slug: str, _auth=Depends(deps.verify_admin_token)):
    """Arrête le serveur HTTP local du workspace donné."""
    if slug not in _SERVING_WORKSPACES:
        raise HTTPException(status_code=404, detail=f"Aucun serveur actif pour '{slug}'")
    info = _SERVING_WORKSPACES.pop(slug)
    try:
        info["process"].terminate()
        await info["process"].wait()
    except Exception:
        pass
    return {"success": True, "slug": slug}


@router.get("/api/workspaces/serving")
async def list_serving_workspaces(_auth=Depends(deps.verify_admin_token)):
    """Liste tous les workspaces actuellement servis."""
    return {
        "serving": [
            {"slug": slug, "url": info["url"], "port": info["port"], "path": info["path"]}
            for slug, info in _SERVING_WORKSPACES.items()
        ]
    }


@router.get("/api/voice/status", dependencies=[Depends(deps.verify_admin_token)])
async def get_voice_status():
    """Retourne le statut de l'assistant vocal."""
    if not deps.VoiceManager:
        return {"running": False, "available": False}
    return deps.VoiceManager.get_instance().get_status()


@router.post("/api/voice/toggle", dependencies=[Depends(deps.verify_admin_token)])
async def toggle_voice():
    """Active ou desactive l'assistant vocal."""
    if not deps.VoiceManager or not deps.lumena:
        raise HTTPException(status_code=503, detail="VoiceManager not available")

    mgr = deps.VoiceManager.get_instance()
    if mgr.running:
        await mgr.stop()
        return {"running": False, "message": "Assistant vocal arrêté"}
    else:
        success = await mgr.start(deps.lumena)
        if success:
            return {"running": True, "message": "Assistant vocal démarré"}
        else:
            raise HTTPException(status_code=500, detail="Impossible de démarrer l'assistant vocal")
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU General Public License v3.0 (GPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
