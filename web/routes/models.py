"""LLM model management and provider health routes."""
from __future__ import annotations

import json as _json
import statistics as _stats
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from pydantic import BaseModel, ConfigDict

from web.routes import deps

from src.utils.paths import ROOT_DIR, OPS_DIR

_PROJECT_ROOT = ROOT_DIR

router = APIRouter()


@router.get("/api/providers", dependencies=[Depends(deps.verify_admin_token)])
async def get_providers():
    """Sante et performances des providers LLM (deepseek, ollama, etc.)."""
    _ops_dir = OPS_DIR
    ops_state_path = _ops_dir / "ops_state.json"

    # Providers connus — toujours affiches meme sans trafic
    _ALL_PROVIDERS = [
        "ollama", "openai", "anthropic", "google",
        "deepseek", "moonshot", "xai", "nvidia", "minimax",
    ]

    stats: dict = {}
    if ops_state_path.exists():
        try:
            ops_state = _json.loads(ops_state_path.read_text(encoding="utf-8", errors="replace"))
            stats = ops_state.get("provider_stats_daily", {})
        except Exception as e:
            logger.warning("providers: read error: {}", e)

    # Detecter la sante live depuis multi_provider si dispo
    live_health: dict = {}
    try:
        if deps.lumena and deps.lumena.llm:
            live_health = {
                n: h.get("healthy", True)
                for n, h in deps.lumena.llm.provider_health.items()
            }
    except Exception:
        pass

    # Detecter si la cle API est configuree
    api_configured: dict = {}
    try:
        from src.llm.providers import AVAILABLE_MODELS, check_api_key, ProviderType
        _provider_by_name = {
            "ollama": ProviderType.OLLAMA, "openai": ProviderType.OPENAI,
            "anthropic": ProviderType.ANTHROPIC, "google": ProviderType.GOOGLE,
            "deepseek": ProviderType.DEEPSEEK, "moonshot": ProviderType.MOONSHOT,
            "xai": ProviderType.XAI, "nvidia": ProviderType.NVIDIA,
            "minimax": ProviderType.MINIMAX,
        }
        for pname, ptype in _provider_by_name.items():
            if pname == "ollama":
                api_configured[pname] = True  # local, pas de cle
            else:
                api_configured[pname] = check_api_key(ptype)
    except Exception:
        pass

    result = []
    seen = set()
    for name in list(stats.keys()) + _ALL_PROVIDERS:
        if name in seen:
            continue
        seen.add(name)
        s = stats.get(name, {})
        probes = s.get("probes", 0)
        successes = s.get("successes", 0)
        lats = s.get("latencies") or []
        healthy = live_health.get(name, True)
        has_key = api_configured.get(name)

        # Status : Sain / Dégradé / Critique / Inactif / Non configuré
        rate = (successes / probes * 100) if probes else 0
        if has_key is False:
            status = "Non configuré"
        elif probes == 0:
            # Clé configurée mais pas de trafic récent → Sain si healthy
            status = "Sain" if healthy else "Erreur"
        elif not healthy:
            status = "Critique"
        elif rate >= 95:
            status = "Sain"
        elif rate >= 80:
            status = "Dégradé"
        else:
            status = "Critique"

        result.append({
            "name": name,
            "probes": probes,
            "successes": successes,
            "failures": probes - successes,
            "success_rate": round(successes / probes * 100, 1) if probes else 0,
            "avg_latency": round(_stats.mean(lats), 3) if lats else None,
            "min_latency": round(min(lats), 3) if lats else None,
            "max_latency": round(max(lats), 3) if lats else None,
            "latency_samples": len(lats),
            "healthy": healthy,
            "api_configured": has_key,
            "status": status,
        })
    return {"success": True, "providers": result}


def _find_best_available_fallback(requested_name: str):
    """Trouve le meilleur modele disponible si le modele demande n'a pas de cle API."""
    from src.llm.providers import AVAILABLE_MODELS, get_model_config, check_api_key

    config = get_model_config(requested_name)

    FALLBACK_PRIORITY = ["ollama", "google", "deepseek", "anthropic", "openai", "xai", "kimi"]

    def _is_available(name: str, m_cfg) -> bool:
        return m_cfg.is_local() or check_api_key(m_cfg.provider)

    if config:
        for name, m_cfg in AVAILABLE_MODELS.items():
            if name == requested_name:
                continue
            if m_cfg.provider == config.provider and _is_available(name, m_cfg):
                return name

    for provider_value in FALLBACK_PRIORITY:
        for name, m_cfg in AVAILABLE_MODELS.items():
            if m_cfg.provider.value == provider_value and _is_available(name, m_cfg):
                return name

    return None


@router.get("/api/models", dependencies=[Depends(deps.verify_admin_token)])
async def get_models():
    """Retourne la liste des modeles disponibles."""
    from src.llm.providers import AVAILABLE_MODELS, check_api_key

    current_model = None
    if deps.lumena and deps.lumena.llm:
        current_model = deps.lumena.llm.model_name

    models = []
    for name, config in AVAILABLE_MODELS.items():
        has_key = True
        if config.provider.value != "ollama":
            has_key = check_api_key(config.provider)

        models.append({
            "name": name,
            "display_name": config.display_name,
            "provider": config.provider.value,
            "description": config.description,
            "badge": getattr(config, "badge", ""),
            "is_local": config.is_local(),
            "is_free": config.is_free(),
            "supports_vision": config.supports_vision,
            "context_window": config.context_window,
            "available": has_key,
            "current": name == current_model
        })

    models.sort(key=lambda m: (not m["current"], m["provider"]))

    return {
        "current_model": current_model,
        "models": models
    }


class ModelSwitchRequest(BaseModel):
    model_config = ConfigDict(protected_namespaces=())
    model_name: str


@router.post("/api/model/switch", dependencies=[Depends(deps.verify_admin_token)])
async def switch_model(request: ModelSwitchRequest):
    """Change le modele LLM utilise."""
    from src.llm.providers import get_model_config, check_api_key
    from src.llm.multi_provider import MultiProviderLLM

    config = get_model_config(request.model_name)
    if not config:
        raise HTTPException(status_code=400, detail=f"Modele '{request.model_name}' non trouve")

    if deps.lumena and deps.lumena.llm and deps.lumena.llm.model_name == request.model_name:
        return {
            "success": True,
            "model": request.model_name,
            "display_name": config.display_name,
            "message": f"Modele {config.display_name} deja actif"
        }

    if not config.is_local() and not check_api_key(config.provider):
        fallback_name = _find_best_available_fallback(request.model_name)
        if fallback_name:
            fallback_config = get_model_config(fallback_name)
            logger.warning(
                f"Cle API manquante pour {config.display_name}, "
                f"bascule vers {fallback_config.display_name}"
            )
            deps.lumena.llm = MultiProviderLLM(model_name=fallback_name)
            return {
                "success": True,
                "model": fallback_name,
                "display_name": fallback_config.display_name,
                "message": (
                    f"Cle API manquante pour **{config.display_name}** — "
                    f"bascule automatique vers **{fallback_config.display_name}**"
                ),
                "fallback": True,
                "requested": request.model_name
            }
        raise HTTPException(
            status_code=400,
            detail=f"Cle API manquante pour {config.provider.value} et aucun modele de fallback disponible. Configure-la dans .env"
        )

    try:
        logger.info(f" Changement de modele vers {config.display_name}...")
        deps.lumena.llm = MultiProviderLLM(model_name=request.model_name)
        logger.info(f" Modele change vers {config.display_name}")

        return {
            "success": True,
            "model": request.model_name,
            "display_name": config.display_name,
            "message": f"Modele change vers {config.display_name}"
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/model/current", dependencies=[Depends(deps.verify_admin_token)])
async def get_current_model():
    """Retourne le modele actuellement utilise."""
    if not deps.lumena or not deps.lumena.llm:
        return {"model": None, "display_name": "Non initialise"}

    from src.llm.providers import get_model_config
    config = get_model_config(deps.lumena.llm.model_name)

    return {
        "model": deps.lumena.llm.model_name,
        "display_name": config.display_name if config else deps.lumena.llm.model_name,
        "provider": deps.lumena.llm.provider.value if deps.lumena.llm.provider else "unknown"
    }
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU General Public License v3.0 (GPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
