"""Content, search, and knowledge routes."""
from __future__ import annotations
import json as _json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request

from web.routes import deps
from web.routes.schemas import SearchRequest, MemoryRequest

from src.utils.paths import ROOT_DIR, DATA_DIR, FACTS_JSON, HEARTBEAT_STATE_JSON, REFLECTION_DIR

_PROJECT_ROOT = ROOT_DIR

router = APIRouter()


@router.post("/api/search/code")
async def search_code(request: SearchRequest, _auth=Depends(deps.verify_admin_token)):
    """Recherche semantique dans le code."""
    if not deps.lumena or not deps.lumena.code_index:
        raise HTTPException(status_code=503, detail="CodeIndex not available")

    try:
        results = deps.lumena.code_index.search(request.query, n_results=request.n_results)
        return {
            "query": request.query,
            "results": [
                {
                    "file": r.chunk.file_path,
                    "symbol": r.chunk.symbol_name,
                    "score": round(r.score, 3),
                    "content": r.chunk.content[:200] + "..." if len(r.chunk.content) > 200 else r.chunk.content
                }
                for r in results
            ]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/search/memory")
async def search_memory(request: MemoryRequest, _auth=Depends(deps.verify_admin_token)):
    """Recherche dans la memoire."""
    if not deps.lumena or not deps.lumena.memory:
        raise HTTPException(status_code=503, detail="Memory not available")

    try:
        memories = deps.lumena.memory.recall(request.query, limit=request.limit)
        results = []
        for m in memories:
            mem_type = "unknown"
            if hasattr(m, 'memory_type'):
                if hasattr(m.memory_type, 'value'):
                    mem_type = m.memory_type.value
                else:
                    mem_type = str(m.memory_type)

            timestamp = None
            if hasattr(m, 'timestamp') and m.timestamp:
                if hasattr(m.timestamp, 'isoformat'):
                    timestamp = m.timestamp.isoformat()
                else:
                    timestamp = str(m.timestamp)

            results.append({
                "content": m.content[:300] + "..." if len(m.content) > 300 else m.content,
                "type": mem_type,
                "timestamp": timestamp
            })

        return {
            "query": request.query,
            "results": results
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/repo-map")
async def get_repo_map(_auth=Depends(deps.verify_admin_token)):
    """Retourne la carte du projet."""
    if not deps.lumena or not deps.lumena.repo_map:
        raise HTTPException(status_code=503, detail="RepoMap not available")

    try:
        if not deps.lumena.repo_map._file_signatures:
            deps.lumena.repo_map.build()

        stats = deps.lumena.repo_map.get_stats()
        compact = deps.lumena.repo_map.get_compact_map()

        return {
            "stats": {
                "total_files": stats.total_files,
                "total_symbols": stats.total_symbols,
                "languages": stats.languages
            },
            "map": compact[:5000]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/rules")
async def get_rules(_auth=Depends(deps.verify_admin_token)):
    """Retourne les regles du projet."""
    if not deps.lumena or not deps.lumena.rules_loader:
        raise HTTPException(status_code=503, detail="RulesLoader not available")

    try:
        rules = deps.lumena.rules_loader.get_rules()
        return {
            "project_name": rules.project_name,
            "language": rules.language,
            "style_guide": rules.style_guide,
            "conventions": rules.conventions,
            "always": rules.always,
            "do_not": rules.do_not,
            "context": rules.context[:500] if rules.context else None
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/instincts")
async def get_instincts(_auth=Depends(deps.verify_admin_token)):
    """Retourne les instincts appris."""
    if not deps.lumena or not deps.lumena.instinct_system:
        raise HTTPException(status_code=503, detail="InstinctSystem not available")

    try:
        instincts = list(deps.lumena.instinct_system.instincts.values())
        return {
            "count": len(instincts),
            "instincts": [
                {
                    "id": i.id,
                    "pattern": i.pattern,
                    "response": i.response,
                    "confidence": round(i.confidence, 2),
                    "times_used": i.times_used
                }
                for i in instincts[:20]
            ]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/facts")
async def get_facts(_auth=Depends(deps.verify_admin_token)):
    """Faits persistants sur l'utilisateur et Lumena (memoire semantique)."""
    facts = {}
    facts_path = FACTS_JSON
    if facts_path.exists():
        try:
            facts = _json.loads(facts_path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            pass
    hb = {}
    hb_path = HEARTBEAT_STATE_JSON
    if hb_path.exists():
        try:
            hb = _json.loads(hb_path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            pass
    insights_path = REFLECTION_DIR / "insights.json"
    insights = []
    if insights_path.exists():
        try:
            raw = _json.loads(insights_path.read_text(encoding="utf-8", errors="replace"))
            insights = raw[-10:] if isinstance(raw, list) else []
        except Exception:
            pass
    return {"success": True, "facts": facts, "heartbeat": hb, "insights": insights}


@router.put("/api/facts")
async def update_fact(request: Request, _auth=Depends(deps.verify_admin_token)):
    """Ajoute ou modifie un fait persistant."""
    body = await request.json()
    key = body.get("key", "").strip()
    value = body.get("value", "").strip()
    if not key or not value:
        raise HTTPException(status_code=400, detail="key et value requis")
    if len(key) > 80 or len(value) > 200:
        raise HTTPException(status_code=400, detail="key (80 max) ou value (200 max) trop long")

    facts_path = FACTS_JSON
    facts = {}
    if facts_path.exists():
        try:
            facts = _json.loads(facts_path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            pass
    facts[key] = value
    facts_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = facts_path.with_suffix(".tmp")
    tmp.write_text(_json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(facts_path)
    return {"success": True, "key": key, "value": value}


@router.delete("/api/facts/{key}")
async def delete_fact(key: str, _auth=Depends(deps.verify_admin_token)):
    """Supprime un fait persistant."""
    facts_path = FACTS_JSON
    if not facts_path.exists():
        raise HTTPException(status_code=404, detail="Aucun fichier de faits")
    try:
        facts = _json.loads(facts_path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        raise HTTPException(status_code=500, detail="Impossible de lire facts.json")
    if key not in facts:
        raise HTTPException(status_code=404, detail=f"Fait '{key}' non trouvé")
    del facts[key]
    tmp = facts_path.with_suffix(".tmp")
    tmp.write_text(_json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(facts_path)
    return {"success": True, "deleted": key}
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU General Public License v3.0 (GPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
