"""LLM model management and provider health routes."""
from __future__ import annotations

import json as _json
import statistics as _stats

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from pydantic import BaseModel, ConfigDict

from web.routes import deps

from src.utils.paths import ROOT_DIR, OPS_DIR

_PROJECT_ROOT = ROOT_DIR

router = APIRouter()


def _canonical_ollama_id(value: str) -> str:
    """Normalise les identifiants Ollama pour comparer ``name`` et ``name:latest``."""
    value = str(value or "").strip().lower()
    if not value:
        return ""
    return value if ":" in value.rsplit("/", 1)[-1] else f"{value}:latest"


def _local_model_enabled(config) -> bool:
    """Lit l'état réconcilié au boot, sans appel réseau dans le catalogue."""
    try:
        from src.local_models.identifiers import parse_model_reference
        from src.local_models.manager import get_local_model_manager

        reference = parse_model_reference(config.model_id)
        entry = get_local_model_manager().state_store.entry(reference)
        return bool(entry.get("installed") and entry.get("enabled", True))
    except Exception as exc:
        logger.debug("models: local availability unavailable for {}: {}", config.name, exc)
        return False


async def _ensure_local_model_live(_llm, config, *, ollama_client=None) -> None:
    """Prouve la présence du tag via Ollama, indépendamment du provider actif.

    ``MultiProviderLLM.is_available`` et ``list_models`` décrivent le provider
    actuellement sélectionné. Les utiliser avant un passage cloud -> Ollama
    interroge donc le cloud et fait passer un modèle local installé pour absent.
    """
    if ollama_client is None:
        from src.local_models.manager import get_local_model_manager

        ollama_client = get_local_model_manager().client
    try:
        health = await ollama_client.health()
        if not health.get("available"):
            raise HTTPException(
                status_code=503,
                detail="Ollama est indisponible. Démarrez Ollama puis réessayez.",
            )
        installed = await ollama_client.list_installed()
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("model switch: Ollama probe failed: {}", exc)
        raise HTTPException(
            status_code=503,
            detail="Impossible de joindre Ollama pour vérifier le modèle local.",
        ) from exc

    expected = _canonical_ollama_id(config.model_id)
    available = {
        _canonical_ollama_id(candidate)
        for model in installed
        for candidate in (model.reference.pull_reference, model.reference.canonical)
    }
    if expected not in available:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Le modèle local '{config.display_name}' n'est pas installé. "
                "Installez-le depuis Modèles locaux puis réessayez."
            ),
        )


async def _activate_runtime_model(model_name: str, config) -> None:
    """Active un modèle et reconstruit le cœur absent après un boot setup-only."""
    from src.llm.multi_provider import MultiProviderLLM

    core = deps.lumena
    if core is None:
        # ``initialize_lumena`` publie son singleton avant d'attendre
        # ``core.initialize()``. Une exception de bootstrap peut donc laisser un
        # cœur récupérable dans src.core alors que la dépendance web n'a jamais
        # reçu l'affectation. C'est le cas typique du premier démarrage.
        try:
            from src.core import get_lumena

            core = get_lumena()
            deps.lumena = core
        except Exception as exc:
            logger.exception("model switch: impossible de reconstruire le cœur Lumena")
            raise HTTPException(
                status_code=503,
                detail=(
                    "Le cœur Lumena n'a pas pu être reconstruit. "
                    "Vérifiez la configuration du modèle puis réessayez."
                ),
            ) from exc

    current_llm = getattr(core, "llm", None)
    if isinstance(current_llm, MultiProviderLLM):
        if config.is_local():
            await _ensure_local_model_live(current_llm, config)
        if not current_llm.switch_model(model_name):
            raise HTTPException(status_code=409, detail="Le modèle demandé ne peut pas être activé.")
    else:
        candidate = MultiProviderLLM(model_name=model_name)
        if config.is_local():
            await _ensure_local_model_live(candidate, config)
        core.llm = candidate
        service_context = getattr(core, "_svc_ctx", None)
        if service_context is not None:
            service_context.llm = candidate

    if not getattr(core, "is_initialized", False):
        initialized = await core.initialize()
        if not initialized:
            raise HTTPException(
                status_code=503,
                detail="Le modèle est configuré mais le cœur Lumena n'a pas pu démarrer.",
            )
        deps.setup_only_mode = False


@router.get("/api/providers", dependencies=[Depends(deps.verify_admin_token)])
async def get_providers():
    """Sante et performances des providers LLM (deepseek, ollama, etc.)."""
    _ops_dir = OPS_DIR
    ops_state_path = _ops_dir / "ops_state.json"

    # Providers connus — toujours affiches meme sans trafic
    _ALL_PROVIDERS = [
        "ollama", "openai", "anthropic", "google",
        "deepseek", "mistral", "moonshot", "xai", "nvidia", "minimax",
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
        from src.llm.providers import check_api_key, ProviderType
        _provider_by_name = {
            "ollama": ProviderType.OLLAMA, "openai": ProviderType.OPENAI,
            "anthropic": ProviderType.ANTHROPIC, "google": ProviderType.GOOGLE,
            "deepseek": ProviderType.DEEPSEEK, "mistral": ProviderType.MISTRAL,
            "moonshot": ProviderType.MOONSHOT, "xai": ProviderType.XAI,
            "nvidia": ProviderType.NVIDIA, "minimax": ProviderType.MINIMAX,
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
    from src.llm.providers import AVAILABLE_MODELS, get_model_config, check_api_key, get_model_fallbacks

    config = get_model_config(requested_name)

    FALLBACK_PRIORITY = [
        "deepseek", "mistral", "zai", "google", "moonshot", "minimax",
        "nvidia", "xai", "anthropic", "openai", "ollama",
    ]

    def _is_available(name: str, m_cfg) -> bool:
        return m_cfg.is_selectable() and (m_cfg.is_local() or check_api_key(m_cfg.provider))

    for name in get_model_fallbacks(requested_name):
        m_cfg = get_model_config(name)
        if m_cfg and _is_available(name, m_cfg):
            return name

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
        if not config.is_selectable():
            continue
        has_key = (
            _local_model_enabled(config)
            if config.provider.value == "ollama"
            else check_api_key(config.provider)
        )

        models.append({
            "name": name,
            "display_name": config.display_name,
            "provider": config.provider.value,
            "description": config.description,
            "badge": getattr(config, "badge", ""),
            "is_local": config.is_local(),
            "is_free": config.is_free(),
            "supports_vision": config.supports_vision,
            "supports_image_generation": config.supports_image_generation,
            "context_window": config.context_window,
            "max_output_tokens": config.max_output_tokens,
            "lifecycle": config.lifecycle.value,
            "fallback_eligible": config.is_fallback_eligible(),
            "pricing": config.pricing.as_dict() if config.pricing else None,
            "successor": config.successor,
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
    try:
        config = get_model_config(request.model_name)
        if not config:
            raise HTTPException(status_code=400, detail=f"Modele '{request.model_name}' non trouve")
        if not config.is_selectable():
            migration = f" Migrez vers '{config.successor}'." if config.successor else ""
            raise HTTPException(
                status_code=409,
                detail=f"Modele '{request.model_name}' retire et non selectionnable.{migration}",
            )

        if deps.lumena and deps.lumena.llm and deps.lumena.llm.model_name == request.model_name:
            # Le nom peut déjà être configuré alors que le cœur est resté en
            # mode setup (par exemple après un premier démarrage sans le tag
            # Ollama). Une activation idempotente doit dans ce cas achever le
            # démarrage au lieu de déclarer le modèle prêt à tort.
            if not getattr(deps.lumena, "is_initialized", False):
                await _activate_runtime_model(request.model_name, config)
            elif config.is_local():
                await _ensure_local_model_live(deps.lumena.llm, config)
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
                if fallback_config is None:
                    raise HTTPException(status_code=503, detail="Le fallback configuré est introuvable.")
                logger.warning(
                    "Cle API manquante pour {}, bascule vers {}",
                    config.display_name,
                    fallback_config.display_name,
                )
                await _activate_runtime_model(fallback_name, fallback_config)
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
                detail=(
                    f"Cle API manquante pour {config.provider.value} et aucun modele de fallback disponible. "
                    "Configure-la dans .env"
                ),
            )

        logger.info(f" Changement de modele vers {config.display_name}...")
        await _activate_runtime_model(request.model_name, config)
        logger.info(f" Modele change vers {config.display_name}")

        return {
            "success": True,
            "model": request.model_name,
            "display_name": config.display_name,
            "message": f"Modele change vers {config.display_name}"
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("model switch failed for {}", request.model_name)
        raise HTTPException(
            status_code=500,
            detail="Le changement de modèle a échoué. Consultez data/logs/lumena.log.",
        ) from exc


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
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
