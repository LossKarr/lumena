"""
🌟 LUMENA - Client LLM Multi-Provider

Client unifié pour tous les providers LLM.
"""

import asyncio
import os
import json
import re
import threading
import httpx
from typing import List, Dict, Any, Optional, AsyncIterator
from loguru import logger

# Semaphores par provider : max 2 appels LLM concurrents par provider
# Évite la saturation quand heartbeat + daemon + user appellent en simultané
_provider_semaphores: Dict[str, asyncio.Semaphore] = {}
_provider_semaphores_loop_id: int = 0
_provider_semaphores_lock = threading.Lock()

def _get_provider_semaphore(provider_name: str) -> asyncio.Semaphore:
    """Retourne (ou crée) le semaphore de concurrence pour un provider.

    Recréé automatiquement si l'event loop a changé (évite
    'is bound to a different event loop').
    """
    global _provider_semaphores, _provider_semaphores_loop_id
    _current_loop_id = id(asyncio.get_running_loop())
    with _provider_semaphores_lock:
        if _current_loop_id != _provider_semaphores_loop_id:
            _provider_semaphores.clear()
            _provider_semaphores_loop_id = _current_loop_id
        if provider_name not in _provider_semaphores:
            _provider_semaphores[provider_name] = asyncio.Semaphore(
                int(os.getenv("LUMENA_PROVIDER_CONCURRENCY", "2"))
            )
    return _provider_semaphores[provider_name]

from .providers import (
    ProviderType, ModelConfig, get_model_config, 
    get_api_key, check_api_key, AVAILABLE_MODELS,
    get_default_model_for_provider,
)

try:
    from ..telemetry import publish_trace
    TELEMETRY_AVAILABLE = True
except Exception:
    TELEMETRY_AVAILABLE = False  # telemetry non disponible


class MultiProviderLLM:
    """
    Client LLM unifié supportant plusieurs providers.
    
    Providers supportés:
    - Ollama (local)
    - OpenAI (GPT-4, GPT-4o)
    - Anthropic (Claude)
    - Google (Gemini)
    - Moonshot (Kimi)
    """
    
    def __init__(
        self,
        model_name: Optional[str] = None,
        ollama_host: Optional[str] = None,
    ):
        self.model_name = self._resolve_initial_model_name(model_name)
        self.ollama_host = ollama_host or self._resolve_ollama_host()
        self._config: Optional[ModelConfig] = None
        
        # Health check et cooldown
        self.provider_health: Dict[str, Dict[str, Any]] = {
            "ollama": {"healthy": True, "failures": 0, "cooldown_until": None},
            "openai": {"healthy": True, "failures": 0, "cooldown_until": None},
            "anthropic": {"healthy": True, "failures": 0, "cooldown_until": None},
            "google": {"healthy": True, "failures": 0, "cooldown_until": None},
            "moonshot": {"healthy": True, "failures": 0, "cooldown_until": None},
            "deepseek": {"healthy": True, "failures": 0, "cooldown_until": None},
            "xai": {"healthy": True, "failures": 0, "cooldown_until": None},
            "nvidia": {"healthy": True, "failures": 0, "cooldown_until": None},
            "minimax": {"healthy": True, "failures": 0, "cooldown_until": None},
            "zai":     {"healthy": True, "failures": 0, "cooldown_until": None},
            "mistral": {"healthy": True, "failures": 0, "cooldown_until": None},
        }
        self._health_lock = threading.Lock()  # P0: protège provider_health
        self._meta_lock = threading.Lock()  # protège _last_response_meta
        self.max_failures = int(os.getenv("LUMENA_PROVIDER_MAX_FAILURES", "3"))
        self.cooldown_minutes = int(os.getenv("LUMENA_PROVIDER_COOLDOWN_MIN", "5"))
        
        # Ordre de fallback : cloud providers d'abord, ollama en dernier recours
        _default_fallback = "deepseek,mistral,zai,anthropic,openai,google,moonshot,xai,nvidia,minimax,ollama"
        self.fallback_order = os.getenv("LUMENA_FALLBACK_ORDER", _default_fallback).split(",")
        self.max_continuation_steps = int(os.getenv("LUMENA_MAX_CONTINUATION_STEPS", "3"))
        self._last_response_meta: Dict[str, Any] = self._default_response_meta()
        
        # Client HTTP persistant avec connection pooling
        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=300.0, write=10.0, pool=10.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            follow_redirects=True,
        )
        self._http_loop_id: int = 0  # id() de l'event loop associée au client
        
        # Charger la config du modèle
        self._load_model_config()

    async def close(self) -> None:
        """Ferme le client HTTP persistant."""
        if hasattr(self, "_http") and self._http:
            await self._http.aclose()
    
    @staticmethod
    def _resolve_ollama_host() -> str:
        """Résout l'URL Ollama : LUMENA_OLLAMA_HOST prioritaire, OLLAMA_HOST comme fallback.

        Log DEPRECATED une seule fois si seul l'ancien nom est présent.
        """
        new_val = (os.getenv("LUMENA_OLLAMA_HOST", "") or "").strip()
        if new_val:
            return new_val
        old_val = (os.getenv("OLLAMA_HOST", "") or "").strip()
        if old_val:
            logger.warning(
                "DEPRECATED: Utilisez LUMENA_OLLAMA_HOST au lieu de OLLAMA_HOST "
                "(rétro-compatibilité temporaire)"
            )
            return old_val
        return "http://localhost:11434"

    @staticmethod
    def _resolve_initial_model_name(explicit_model_name: Optional[str]) -> str:
        """
        Resolve le modele initial avec priorite:
        1) argument explicite
        2) LUMENA_DEFAULT_MODEL
        3) DEFAULT_MODEL (compat)
        4) LUMENA_MODEL (compat legacy)
        5) qwen3-8b
        """
        source = "default"
        candidate = (explicit_model_name or "").strip()

        if candidate:
            source = "argument"
        else:
            for env_name in ("LUMENA_DEFAULT_MODEL", "DEFAULT_MODEL", "LUMENA_MODEL"):
                raw = (os.getenv(env_name, "") or "").strip()
                if raw:
                    candidate = raw
                    source = f"env:{env_name}"
                    break

        if not candidate:
            return "qwen3-8b"

        if get_model_config(candidate):
            if source != "argument":
                logger.info(f"Modele par defaut charge depuis {source}: {candidate}")
            return candidate

        logger.warning(
            f"Modele '{candidate}' ({source}) introuvable, fallback vers qwen3-8b"
        )
        return "qwen3-8b"

    def _load_model_config(self):
        """Charge la configuration du modèle."""
        self._config = get_model_config(self.model_name)
        if not self._config:
            logger.warning(f"Modèle {self.model_name} non trouvé, fallback vers qwen3-8b")
            self._config = get_model_config("qwen3-8b")
        
        logger.info(f"🧠 Modèle chargé: {self._config.display_name}")
    
    @property
    def model(self) -> str:
        """Retourne l'ID du modèle actuel."""
        return self._config.model_id if self._config else "qwen3:8b"
    
    @property
    def provider(self) -> ProviderType:
        """Retourne le provider actuel."""
        return self._config.provider if self._config else ProviderType.OLLAMA
    
    @property
    def context_window(self) -> int:
        """Retourne la taille de la fenêtre de contexte."""
        return self._config.context_window if self._config else 32000

    @property
    def max_output_tokens(self) -> int:
        """Retourne le nombre maximum de tokens en sortie pour le modèle actuel."""
        return self._config.max_output_tokens if self._config else 4096

    def build_runtime_snapshot(
        self,
        source_channel: str = "web",
        mode: str = "agent",
        budget_seconds: float = 900.0,
        intent: str = "react",
    ) -> "RuntimeContext":  # noqa: F821
        """
        Construit un snapshot immutable du contexte runtime.
        Appelé une fois par requête avant le traitement.
        """
        from src.core_services.runtime_context import RuntimeContext

        health = {}
        healthy = []
        for pname, pdata in self.provider_health.items():
            is_ok = self._is_healthy(pname)
            health[pname] = is_ok
            if is_ok:
                healthy.append(pname)

        return RuntimeContext(
            active_model=self.model_name,
            active_provider=self.provider.value,
            max_context_window=self.context_window,
            max_output_tokens=self.max_output_tokens,
            providers_health=health,
            healthy_providers=healthy,
            budget_seconds=budget_seconds,
            source_channel=source_channel,
            mode=mode,
            intent=intent,
            fallback_order=[p for p in self.fallback_order if health.get(p, True)],
        )

    # ========== Health Check & Failover (Phase 17) ==========
    
    def _mark_failure(self, provider_name: str):
        """Marque un échec pour un provider et active le cooldown si nécessaire."""
        from datetime import datetime, timedelta
        
        with self._health_lock:
            if provider_name not in self.provider_health:
                return
            
            health = self.provider_health[provider_name]

            # HALF_OPEN → rechute : cooldown exponentiel (x2 à chaque fois, max x8)
            if health.get("half_open"):
                health["half_open"] = False
                health["healthy"] = False
                mult = min(health.get("cooldown_mult", 1) * 2, 8)
                health["cooldown_mult"] = mult
                health["cooldown_until"] = datetime.now() + timedelta(minutes=self.cooldown_minutes * mult)
                logger.warning(
                    f"⚠️ Provider {provider_name} HALF_OPEN → rechute, cooldown x{mult} "
                    f"({self.cooldown_minutes * mult} min)"
                )
                return

            health["failures"] = health.get("failures", 0) + 1
            if health["failures"] >= self.max_failures:
                health["healthy"] = False
                health["cooldown_until"] = datetime.now() + timedelta(minutes=self.cooldown_minutes)
                logger.warning(f"⚠️ Provider {provider_name} en cooldown pour {self.cooldown_minutes} min")
    
    def _mark_success(self, provider_name: str):
        """Réinitialise le compteur d'échecs après succès (CLOSED)."""
        with self._health_lock:
            if provider_name in self.provider_health:
                was_half_open = self.provider_health[provider_name].get("half_open", False)
                if was_half_open:
                    logger.info(f"✅ Provider {provider_name} HALF_OPEN → CLOSED (guérison confirmée)")
                self.provider_health[provider_name] = {
                    "healthy": True,
                    "failures": 0,
                    "cooldown_until": None,
                    "half_open": False,
                    "cooldown_mult": 1,
                }
    
    def _is_healthy(self, provider_name: str) -> bool:
        """Vérifie si un provider est healthy (CLOSED) ou en test (HALF_OPEN)."""
        from datetime import datetime
        
        with self._health_lock:
            if provider_name not in self.provider_health:
                return True
            
            health = self.provider_health[provider_name]
            
            # Cooldown terminé → passe en HALF_OPEN (1 requête test avant de confirmer CLOSED)
            if health.get("cooldown_until"):
                if datetime.now() >= health["cooldown_until"]:
                    health["cooldown_until"] = None
                    health["half_open"] = True
                    health["failures"] = 0
                    logger.info(f"🔶 Provider {provider_name} → HALF_OPEN (1 requête test autorisée)")
                    return True  # Laisse passer la requête test
                return False
            
            return health.get("healthy", True)
    
    def _get_next_provider(self, current: str) -> Optional[str]:
        """Retourne le prochain provider healthy dans l'ordre de fallback."""
        current_idx = -1
        if current in self.fallback_order:
            current_idx = self.fallback_order.index(current)
        
        for i in range(current_idx + 1, len(self.fallback_order)):
            provider = self.fallback_order[i]
            if self._is_healthy(provider):
                return provider
        
        return None
    
    def get_health_status(self) -> Dict[str, Any]:
        """Retourne le statut de santé de tous les providers."""
        return {
            name: {
                "healthy": health["healthy"],
                "failures": health["failures"],
                "in_cooldown": health["cooldown_until"] is not None
            }
            for name, health in self.provider_health.items()
        }
    
    def _handle_httpx_error(self, error: Exception, provider_name: str) -> str:
        """
        Gère les erreurs httpx de manière centralisée.
        
        Catégorise les erreurs et retourne un message descriptif pour le logging.
        Marque le provider en échec si nécessaire.
        
        Phase 1.3: Gestion complète de ConnectError, ConnectTimeout, 
        ReadTimeout, PoolTimeout, RemoteProtocolError
        """
        error_type = type(error).__name__
        error_msg = str(error)
        
        # Erreurs de connexion
        if isinstance(error, httpx.ConnectError):
            self._mark_failure(provider_name)
            return f"🔌 Connexion impossible à {provider_name}: {error_msg}"
        
        # Timeout de connexion
        if isinstance(error, httpx.ConnectTimeout):
            self._mark_failure(provider_name)
            return f"⏱️ Timeout connexion {provider_name}: serveur injoignable"
        
        # Timeout de lecture
        if isinstance(error, httpx.ReadTimeout):
            self._mark_failure(provider_name)
            return f"⏱️ Timeout lecture {provider_name}: réponse trop lente"
        
        # Timeout pool (trop de connexions simultanées)
        if isinstance(error, httpx.PoolTimeout):
            return f"🔄 Pool saturé {provider_name}: trop de requêtes concurrentes"
        
        # Erreur de protocole HTTP
        if isinstance(error, httpx.RemoteProtocolError):
            self._mark_failure(provider_name)
            return f"📡 Erreur protocole {provider_name}: {error_msg}"
        
        # Erreur HTTP avec code de statut
        if isinstance(error, httpx.HTTPStatusError):
            status_code = error.response.status_code
            error_detail = ""
            try:
                error_detail = error.response.text[:500]
            except Exception:
                pass  # response body non lisible
            
            # Erreurs d'authentification
            if status_code == 401:
                return f"🔑 Clé API invalide {provider_name}: vérifiez votre .env"
            elif status_code == 403:
                return f"🚫 Accès refusé {provider_name}: quota dépassé ou IP bloquée"
            elif status_code == 402:
                self._mark_failure(provider_name)
                return f"💳 Crédits épuisés {provider_name}: rechargez votre compte"
            elif status_code == 429:
                self._mark_failure(provider_name)
                return f"⚡ Rate limit {provider_name}: trop de requêtes, réessayez plus tard"
            elif status_code >= 500:
                self._mark_failure(provider_name)
                return f"💥 Erreur serveur {provider_name} ({status_code}): {error_detail}"
            else:
                return f"❌ Erreur HTTP {provider_name} ({status_code}): {error_detail}"
        
        # Autres erreurs httpx (RequestError base class)
        if isinstance(error, httpx.RequestError):
            return f"🌐 Erreur réseau {provider_name}: {error_type} - {error_msg}"
        
        # Erreur générique
        return f"❓ Erreur {provider_name}: {error_type} - {error_msg}"


    def _default_response_meta(self) -> Dict[str, Any]:
        return {
            "provider_requested": self.provider.value if self._config else "unknown",
            "provider_used": self.provider.value if self._config else "unknown",
            "model_requested": self.model if self._config else "unknown",
            "model_used": self.model if self._config else "unknown",
            "auto_switch_used": False,
            "auto_switch_reason": None,
            "fallback_used": False,
            "fallback_reason": None,
            "continuation_used": False,
            "continuation_steps": 0,
            "finish_reason": None,
            "continuation_warning": None,
            "prompt_tokens": None,
            "completion_tokens": None,
        }

    def _set_last_response_meta(self, **kwargs) -> None:
        meta = self._default_response_meta()
        meta.update(kwargs)
        with self._meta_lock:
            self._last_response_meta = meta

    def _update_last_response_meta(self, **kwargs) -> None:
        with self._meta_lock:
            self._last_response_meta.update(kwargs)

    def get_last_response_meta(self) -> Dict[str, Any]:
        """Expose unified metadata for API/UI."""
        with self._meta_lock:
            return dict(self._last_response_meta)

    def _is_length_finish_reason(self, finish_reason: Optional[str]) -> bool:
        if not finish_reason:
            return False
        value = str(finish_reason).strip().lower()
        return value in {"length", "max_tokens", "max_output_tokens", "maxtokens"} or "max_tokens" in value

    def _last_user_message(self, messages: List[Dict[str, str]]) -> str:
        for msg in reversed(messages or []):
            if str(msg.get("role", "")).lower() == "user":
                return str(msg.get("content", "") or "")
        return ""

    def _extract_effective_user_intent(self, user_text: str) -> str:
        text = (user_text or "").strip()
        if not text:
            return ""

        markers = [
            "## Requête actuelle:",
            "## Requête actuelle:",
            "Requête originale:",
            "Requête originale:",
        ]
        stop_markers = [
            "\n\nMaintenant",
            "\nMaintenant",
            "\nTHOUGHT:",
            "\nACTION:",
            "\n## ",
        ]

        for marker in markers:
            idx = text.rfind(marker)
            if idx == -1:
                continue
            extracted = text[idx + len(marker):].strip()
            for stop in stop_markers:
                stop_idx = extracted.find(stop)
                if stop_idx != -1:
                    extracted = extracted[:stop_idx].strip()
                    break
            if extracted:
                return extracted

        return text

    def _maybe_expand_max_tokens_for_model_switch(
        self,
        *,
        requested_model: str,
        target_model: str,
        requested_max_tokens: int,
    ) -> int:
        """Évite de conserver le cap de sortie du modèle source après un auto-switch.

        Cas réel observé :
        - CodeAgent boucle sur `deepseek-chat`
        - passe `llm.max_output_tokens` (= 8192)
        - `chat()` auto-switch vers `deepseek-reasoner`
        - mais garde 8192 comme plafond effectif

        On n'élargit automatiquement le budget QUE si le caller a simplement
        hérité du cap du modèle source (ou moins). Si le caller a déjà demandé
        explicitement plus, on respecte sa valeur.
        """
        try:
            target_cfg = get_model_config(target_model)
        except Exception:
            return requested_max_tokens

        if not target_cfg:
            return requested_max_tokens

        source_cap = 0
        if str(requested_model).strip().lower() == str(getattr(self, "model", "")).strip().lower():
            try:
                source_cap = int(getattr(self, "max_output_tokens", 0) or 0)
            except Exception:
                source_cap = 0
        if source_cap <= 0:
            try:
                source_cfg = get_model_config(requested_model)
                source_cap = int(getattr(source_cfg, "max_output_tokens", 0) or 0) if source_cfg else 0
            except Exception:
                source_cap = 0

        target_cap = int(getattr(target_cfg, "max_output_tokens", 0) or 0)
        if source_cap <= 0 or target_cap <= source_cap:
            return requested_max_tokens

        if requested_max_tokens <= source_cap:
            logger.info(
                "🔓 Auto-switch budget uplift: {} {} -> {} {}",
                requested_model,
                requested_max_tokens,
                target_model,
                target_cap,
            )
            return target_cap

        return requested_max_tokens

    def _is_code_heavy_request(self, messages: List[Dict[str, str]], max_tokens: int) -> tuple[bool, Optional[str]]:
        auto_switch_raw = str(os.getenv("LUMENA_CODE_AUTOSWITCH_REASONER", "1")).strip().lower()
        if auto_switch_raw in {"0", "false", "off", "no"}:
            return False, None
        if self.provider != ProviderType.DEEPSEEK:
            return False, None
        # Auto-switch uniquement depuis deepseek-chat (V3.2 non-thinking) → deepseek-reasoner
        # Les modèles V4 (deepseek-v4-flash, deepseek-v4-pro) ne doivent pas être redirigés vers V3.2
        if self.model != "deepseek-chat":
            return False, None

        raw_user_text = self._last_user_message(messages)
        user_text = self._extract_effective_user_intent(raw_user_text).lower()
        if not user_text:
            return False, None

        # ── Ne pas auto-switch en mode ReAct (le reasoner est mauvais au format THOUGHT/ACTION) ──
        # Le CodeAgent a son propre swap dans sub_agent.py._iterative_code_loop
        _all_text = " ".join(str(m.get("content", "") or "") for m in (messages or []))
        if "THOUGHT:" in _all_text and "ACTION:" in _all_text and "ACTION_INPUT:" in _all_text:
            return False, None

        greeting = user_text.strip(" \t\r\n!?.")
        if greeting in {"salut", "bonjour", "hello", "hey", "yo", "hoi", "hoii"}:
            return False, None

        code_extensions = [
            ".py", ".js", ".ts", ".tsx", ".jsx", ".html", ".css",
            ".json", ".yaml", ".yml", ".toml", ".ini", ".sql",
            ".sh", ".ps1", ".bat", ".go", ".rs", ".java", ".c", ".cpp", ".cs",
        ]
        code_terms = [
            "code", "python", "javascript", "typescript", "html", "css", "react",
            "fonction", "classe", "module", "import", "api", "endpoint",
            "patch", "diff", "refactor", "stack trace", "traceback", "syntaxerror",
            "exception", "bug", "test", "pytest", "compiler", "compile",
        ]
        code_actions = [
            "corrige", "corriger", "fix", "debug", "modifie", "modifier", "edite",
            "édite", "met a jour", "mettre a jour", "patch", "refactor",
            "cree", "crée", "creer", "créer", "generate", "génère",
        ]
        doc_only_markers = [
            ".md", "markdown", "rapport", "résumé", "resume",
            "présentation", "presentation", "article", "email", "mail",
        ]

        has_code_extension = any(ext in user_text for ext in code_extensions)
        has_code_block = "```" in user_text
        has_code_term = has_code_block or any(term in user_text for term in code_terms)
        has_code_action = any(action in user_text for action in code_actions)
        looks_doc_only = (
            any(marker in user_text for marker in doc_only_markers)
            and not has_code_extension
            and not has_code_block
            and not any(term in user_text for term in ("python", "javascript", "typescript", "react", "api", "bug", "patch", "refactor"))
        )

        if looks_doc_only:
            return False, None
        if not (has_code_extension or has_code_term):
            return False, None

        token_pressure = max_tokens >= 12000
        text_size = sum(len(str(m.get("content", "") or "")) for m in (messages or []))
        context_pressure = text_size > 18000
        strong_debug_intent = any(term in user_text for term in ("stack trace", "traceback", "syntaxerror", "exception"))

        # ── Auto-switch agressif comme le CodeAgent ──
        # Si action de code + terme/extension code → switch direct (pas besoin de pression tokens)
        if has_code_action and (has_code_term or has_code_extension):
            return True, "code_task"

        # Debug long → switch si pression
        if strong_debug_intent and (token_pressure or context_pressure):
            reason = "code_debug_long_context" if context_pressure else "code_debug_high_tokens"
            return True, reason

        # Extension/terme code seul → switch uniquement sous pression tokens (évite surcoût)
        if (has_code_extension or has_code_term) and (token_pressure or context_pressure):
            reason = "code_context_pressure"
            return True, reason

        return False, None

    def _merge_text_segments(self, base: str, continuation: str, max_overlap: int = 1200) -> str:
        """Merge continuation while removing duplicated overlap."""
        if not base:
            return continuation
        if not continuation:
            return base

        tail = base[-max_overlap:]
        if continuation in tail:
            return base

        max_len = min(len(base), len(continuation), max_overlap)
        for size in range(max_len, 2, -1):
            if base[-size:] == continuation[:size]:
                return base + continuation[size:]
        return base + continuation

    async def _chat_provider_result(
        self,
        provider: ProviderType,
        messages: List[Dict[str, str]],
        temperature: float,
        max_tokens: int,
        model: Optional[str] = None,
        stop: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        async with _get_provider_semaphore(provider.value):
            result = await self._chat_provider_result_with_retry(provider, messages, temperature, max_tokens, model, stop=stop)
            # Defensive: garantir que le résultat est toujours un dict
            if isinstance(result, str):
                logger.warning("⚠️ _chat_provider_result: provider {} returned str — wrapping", provider.value)
                result = {"text": result, "finish_reason": "stop", "provider_used": provider.value}
            return result

    # ─── Retry intra-provider (429 / 5xx / ReadTimeout) ──────────────────
    _TRANSIENT_RETRIES = int(os.environ.get("LUMENA_LLM_TRANSIENT_RETRIES", "2"))  # 2 retries = 3 tentatives max
    _RETRY_DELAYS = tuple(float(x) for x in os.environ.get("LUMENA_LLM_RETRY_DELAYS", "1.0,3.0").split(","))  # backoff

    def _recreate_http_client(self) -> None:
        """Recrée le client HTTP persistant (après Event loop is closed, etc.).

        Tracks the event loop id to avoid redundant recreations when called
        multiple times within the same (new) loop.
        """
        import asyncio as _aio
        try:
            _current_loop_id = id(_aio.get_running_loop())
        except RuntimeError:
            _current_loop_id = 0
        # Skip si le client a déjà été recréé pour cette loop
        if _current_loop_id and _current_loop_id == self._http_loop_id:
            return
        logger.debug("🔄 Recréation du client HTTP (event loop changé)")
        # Ne pas tenter de fermer l'ancien client : ses connexions sont liées à
        # l'ancienne event loop (potentiellement fermée sur Windows ProactorEventLoop).
        # Appeler aclose() sur cet event loop cause "RuntimeError: Event loop is closed".
        # On remplace silencieusement — le GC + timeout serveur nettoient les connexions.
        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=300.0, write=10.0, pool=10.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            follow_redirects=True,
        )
        self._http_loop_id = _current_loop_id

    async def _chat_provider_result_with_retry(
        self,
        provider: ProviderType,
        messages: List[Dict[str, str]],
        temperature: float,
        max_tokens: int,
        model: Optional[str] = None,
        stop: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Appelle le provider avec retry pour les erreurs transitoires (429/5xx/timeout)."""
        last_exc: Optional[Exception] = None
        # Vérification proactive de l'event loop : si elle a changé depuis la création
        # du client HTTP, on le recrée maintenant (avant la 1re tentative) pour éviter
        # 1 appel perdu + ~300ms de latence supplémentaire par requête.
        try:
            import asyncio as _asyncio_chk
            _cur_loop_id = id(_asyncio_chk.get_running_loop())
            if _cur_loop_id and _cur_loop_id != self._http_loop_id:
                self._recreate_http_client()
                logger.debug("🔄 Client HTTP recréé proactivement (event loop changé)")
        except RuntimeError:
            pass
        for attempt in range(self._TRANSIENT_RETRIES + 1):
            try:
                return await self._chat_provider_result_inner(
                    provider, messages, temperature, max_tokens, model, stop=stop,
                )
            except RuntimeError as exc:
                _exc_str = str(exc)
                if "Event loop is closed" in _exc_str or "bound to a different event loop" in _exc_str:
                    # Connexions keepalive ou asyncio primitives liées à un event loop fermé/différent
                    # (Windows ProactorEventLoop ou changement de loop entre deux requêtes)
                    # → recréer le client HTTP et retry immédiatement
                    self._recreate_http_client()
                    logger.debug(f"🔄 {provider.value} event loop changed — client HTTP recréé, retry")
                    last_exc = exc
                    continue
                raise
            except httpx.HTTPStatusError as exc:
                code = exc.response.status_code
                if code in (429, 500, 502, 503) and attempt < self._TRANSIENT_RETRIES:
                    delay = self._RETRY_DELAYS[attempt]
                    logger.info(
                        f"🔄 {provider.value} HTTP {code} — retry {attempt+1}/{self._TRANSIENT_RETRIES} dans {delay}s"
                    )
                    await asyncio.sleep(delay)
                    last_exc = exc
                    continue
                raise
            except (httpx.ReadTimeout, httpx.ReadError, httpx.RemoteProtocolError) as exc:
                if attempt < self._TRANSIENT_RETRIES:
                    delay = self._RETRY_DELAYS[attempt]
                    exc_type = type(exc).__name__
                    logger.info(
                        f"🔄 {provider.value} {exc_type} — retry {attempt+1}/{self._TRANSIENT_RETRIES} dans {delay}s"
                    )
                    await asyncio.sleep(delay)
                    last_exc = exc
                    continue
                raise
        # Ne devrait jamais arriver, mais au cas où
        raise last_exc  # type: ignore[misc]

    async def _chat_provider_result_inner(
        self,
        provider: ProviderType,
        messages: List[Dict[str, str]],
        temperature: float,
        max_tokens: int,
        model: Optional[str] = None,
        stop: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        # Résoudre la cap output via ModelConfig (source unique de vérité)
        target_model = model or self.model
        cfg = get_model_config(target_model) if target_model else None
        model_cap = cfg.max_output_tokens if cfg else 4096

        if provider == ProviderType.OLLAMA:
            return await self._chat_ollama_result(messages, model=model, temperature=temperature, stop=stop)
        if provider == ProviderType.OPENAI:
            return await self._chat_openai_result(messages, temperature=temperature, max_tokens=min(max_tokens, model_cap), stop=stop, model=model)
        if provider == ProviderType.ANTHROPIC:
            return await self._chat_anthropic_result(messages, temperature=temperature, max_tokens=min(max_tokens, model_cap), stop=stop, model=model)
        if provider == ProviderType.GOOGLE:
            # Google API plafonne à 65535 même si le modèle dit 65536
            return await self._chat_google_result(messages, temperature=temperature, max_tokens=min(max_tokens, model_cap, 65535), stop=stop, model=model)
        if provider == ProviderType.MOONSHOT:
            return await self._chat_moonshot_result(messages, temperature=temperature, max_tokens=min(max_tokens, model_cap), stop=stop, model=model)
        if provider == ProviderType.DEEPSEEK:
            return await self._chat_deepseek_result(
                messages,
                temperature=temperature,
                max_tokens=min(max_tokens, model_cap),
                model=target_model,
                stop=stop,
            )
        if provider == ProviderType.XAI:
            # Les modèles grok reasoning rejettent le paramètre stop → on ne le passe pas
            xai_stop = None if any(x in str(target_model) for x in ("reasoning", "grok-4")) else stop
            return await self._chat_xai_result(messages, temperature=temperature, max_tokens=min(max_tokens, model_cap), model=model, stop=xai_stop)
        if provider == ProviderType.NVIDIA:
            return await self._chat_nvidia_result(messages, temperature=temperature, max_tokens=min(max_tokens, model_cap), model=model, stop=stop)
        if provider == ProviderType.MINIMAX:
            return await self._chat_minimax_result(messages, temperature=temperature, max_tokens=min(max_tokens, model_cap), model=model, stop=stop)
        if provider == ProviderType.ZAI:
            return await self._chat_zai_result(messages, temperature=temperature, max_tokens=min(max_tokens, model_cap), model=model, stop=stop)
        if provider == ProviderType.MISTRAL:
            return await self._chat_mistral_result(messages, temperature=temperature, max_tokens=min(max_tokens, model_cap), model=model, stop=stop)
        raise ValueError(f"Provider non supporté: {provider}")

    async def _continue_if_needed(
        self,
        provider: ProviderType,
        base_messages: List[Dict[str, str]],
        temperature: float,
        max_tokens: int,
        initial_result: Dict[str, Any],
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        # Defensive: si un provider retourne str au lieu de dict
        if isinstance(initial_result, str):
            logger.warning("⚠️ _continue_if_needed: initial_result est str au lieu de dict — wrapping")
            initial_result = {"text": initial_result, "finish_reason": "stop"}
        text = initial_result.get("text", "") or ""
        finish_reason = initial_result.get("finish_reason")
        continuation_steps = 0
        continuation_warning: Optional[str] = None
        
        # Phase 3.1: Détection de répétition
        previous_segments: List[str] = []
        text_may_be_incomplete = False

        while continuation_steps < self.max_continuation_steps and self._is_length_finish_reason(finish_reason):
            continuation_steps += 1
            
            # Fix: Moonshot (et certains providers) rejettent les messages assistant vides
            if not text or not text.strip():
                continuation_warning = "⚠️ Texte vide avant continuation. Arrêt."
                logger.warning(continuation_warning)
                text_may_be_incomplete = True
                break
            
            continuation_prompt = (
                "Continue depuis le dernier caractere, sans repeter. "
                "Retourne uniquement la suite manquante."
            )
            continuation_messages = list(base_messages) + [
                {"role": "assistant", "content": text},
                {"role": "user", "content": continuation_prompt},
            ]

            try:
                part_result = await self._chat_provider_result(
                    provider=provider,
                    messages=continuation_messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    model=model,
                )
            except Exception as exc:
                continuation_warning = f"⚠️ Continuation échouée ({exc}). Réponse potentiellement incomplète."
                logger.warning(continuation_warning)
                break

            # Defensive: même garde que pour initial_result
            if isinstance(part_result, str):
                part_result = {"text": part_result, "finish_reason": "stop"}
            part_text = part_result.get("text", "") or ""
            if not part_text.strip():
                continuation_warning = "⚠️ Continuation vide. Réponse potentiellement incomplète."
                logger.warning(continuation_warning)
                text_may_be_incomplete = True
                break
            
            # Phase 3.1: Détection de répétition
            part_text_normalized = part_text.strip()[:200]  # Comparer les 200 premiers chars
            if part_text_normalized in previous_segments:
                continuation_warning = "⚠️ Répétition détectée dans la continuation. Arrêt."
                logger.warning(continuation_warning)
                text_may_be_incomplete = True
                break
            previous_segments.append(part_text_normalized)

            text = self._merge_text_segments(text, part_text)
            finish_reason = part_result.get("finish_reason")

        if self._is_length_finish_reason(finish_reason):
            continuation_warning = (
                continuation_warning
                or "⚠️ Réponse potentiellement incomplète après continuation automatique."
            )

        # NOTE: On ne mélange PLUS le warning dans le texte.
        # Avant, un "⚠️ Réponse potentiellement incomplète..." était ajouté
        # en fin de texte, ce qui corrompait le code généré (HTML/CSS/JS).
        # Le flag text_may_be_incomplete est suffisant pour les appelants.

        result = dict(initial_result)
        result["text"] = text
        result["finish_reason"] = finish_reason
        result["continuation_used"] = continuation_steps > 0
        result["continuation_steps"] = continuation_steps
        result["continuation_warning"] = continuation_warning
        result["text_may_be_incomplete"] = text_may_be_incomplete or self._is_length_finish_reason(finish_reason)
        return result

    def switch_model(self, model_name: str) -> bool:
        """
        Change le modèle LLM.
        
        Returns:
            True si le changement a réussi
        """
        config = get_model_config(model_name)
        if not config:
            logger.error(f"Modèle inconnu: {model_name}")
            return False
        
        # Vérifier la clé API si nécessaire
        if not config.is_local() and not check_api_key(config.provider):
            logger.error(f"Clé API manquante pour {config.provider.value}")
            return False
        
        self.model_name = model_name
        self._config = config
        logger.info(f"🔄 Modèle changé: {config.display_name}")
        return True
    
    async def is_available(self) -> bool:
        """
        Vérifie si le provider LLM est disponible.
        
        Pour Ollama: vérifie si le serveur répond
        Pour les providers cloud: vérifie si la clé API est présente
        """
        if self.provider == ProviderType.OLLAMA:
            try:
                response = await self._http.get(
                    f"{self.ollama_host}/api/tags",
                    timeout=5.0
                )
                return response.status_code == 200
            except Exception:
                return False  # Ollama non joignable
        else:
            # Pour les providers cloud, vérifier la clé API
            return check_api_key(self.provider)
    
    async def list_models(self) -> list:
        """
        Liste les modèles disponibles.
        
        Pour Ollama: retourne les modèles installés localement
        Pour les providers cloud: retourne le modèle configuré
        """
        if self.provider == ProviderType.OLLAMA:
            try:
                response = await self._http.get(
                    f"{self.ollama_host}/api/tags",
                    timeout=10.0
                )
                if response.status_code == 200:
                    data = response.json()
                    return [m["name"] for m in data.get("models", [])]
            except Exception:
                pass  # listing modèles échoué
            return []
        else:
            # Pour les providers cloud, retourner le modèle actuel
            return [self.model] if self.model else []
    
    async def chat(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 65536,  # Valeur haute : chaque provider plafonne à son propre max
        stop: Optional[List[str]] = None,
        no_upgrade: bool = False,  # True → désactive l'auto-switch vers le modèle raisonneur
    ) -> str:
        """
        Envoie un message au LLM et retourne la réponse.
        
        Routing automatique selon le provider.
        Fallback vers Ollama si erreur avec provider cloud.
        """
        provider = self.provider
        requested_provider = provider.value
        requested_model = self.model
        self._set_last_response_meta(
            provider_requested=requested_provider,
            provider_used=requested_provider,
            model_requested=requested_model,
            model_used=requested_model,
            auto_switch_used=False,
            auto_switch_reason=None,
            fallback_used=False,
            fallback_reason=None,
            continuation_used=False,
            continuation_steps=0,
            finish_reason=None,
            continuation_warning=None,
        )

        provider_for_call = provider
        model_for_call = model or self.model
        max_tokens_for_call = max_tokens
        auto_switch_used = False
        auto_switch_reason: Optional[str] = None

        should_switch, switch_reason = self._is_code_heavy_request(messages, max_tokens=max_tokens)
        if should_switch and not no_upgrade:
            reasoner_cfg = get_model_config("deepseek-reasoner")
            reasoner_model = reasoner_cfg.model_id if reasoner_cfg else "deepseek-reasoner"
            if str(model_for_call).lower() != str(reasoner_model).lower():
                auto_switch_used = True
                auto_switch_reason = switch_reason or "code_task"
                model_for_call = reasoner_model
                max_tokens_for_call = self._maybe_expand_max_tokens_for_model_switch(
                    requested_model=requested_model,
                    target_model=model_for_call,
                    requested_max_tokens=max_tokens,
                )
                logger.info(
                    "🔁 Auto-switch model for this turn: {} -> {} ({})",
                    requested_model,
                    model_for_call,
                    auto_switch_reason,
                )
                if TELEMETRY_AVAILABLE:
                    try:
                        publish_trace(
                            stage="model_auto_switch",
                            status="ok",
                            mode="chat",
                            provider=requested_provider,
                            model=model_for_call,
                            summary=auto_switch_reason,
                        )
                    except Exception:
                        pass  # auto-switch trace best-effort
        
        try:
            result = await self._chat_provider_result(
                provider=provider_for_call,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens_for_call,
                model=model_for_call,
                stop=stop,
            )
            result = await self._continue_if_needed(
                provider=provider_for_call,
                base_messages=messages,
                temperature=temperature,
                max_tokens=max_tokens_for_call,
                initial_result=result,
                model=model_for_call,
            )
            # Defensive: garantir que result est un dict
            if isinstance(result, str):
                logger.warning("⚠️ chat(): result est str au lieu de dict — wrapping")
                result = {"text": result, "finish_reason": "stop"}
            # deepseek-chat tronqué (4096 tokens) → retry automatique avec deepseek-reasoner
            # Uniquement pour deepseek-chat (V3.2 non-thinking) — pas les modèles V4
            if (
                not auto_switch_used
                and not no_upgrade
                and result.get("truncated")
                and str(model_for_call).lower() == "deepseek-chat"
            ):
                reasoner_cfg = get_model_config("deepseek-reasoner")
                _reasoner_mdl = reasoner_cfg.model_id if reasoner_cfg else "deepseek-reasoner"
                logger.warning(
                    "⚠️ deepseek-chat tronqué ({} tokens) → retry avec deepseek-reasoner",
                    result.get("completion_tokens", "?"),
                )
                try:
                    _retry_max_tokens = self._maybe_expand_max_tokens_for_model_switch(
                        requested_model=requested_model,
                        target_model=_reasoner_mdl,
                        requested_max_tokens=max_tokens,
                    )
                    _retry_result = await self._chat_provider_result(
                        provider=provider_for_call,
                        messages=messages,
                        temperature=temperature,
                        max_tokens=_retry_max_tokens,
                        model=_reasoner_mdl,
                        stop=stop,
                    )
                    _retry_result = await self._continue_if_needed(
                        provider=provider_for_call,
                        base_messages=messages,
                        temperature=temperature,
                        max_tokens=_retry_max_tokens,
                        initial_result=_retry_result,
                        model=_reasoner_mdl,
                    )
                    if isinstance(_retry_result, str):
                        _retry_result = {"text": _retry_result, "finish_reason": "stop"}
                    result = _retry_result
                    auto_switch_used = True
                    auto_switch_reason = "truncation_upgrade"
                    model_for_call = _reasoner_mdl
                except Exception as _trunc_retry_err:
                    logger.warning("⚠️ Retry deepseek-reasoner après troncature échoué: {}", _trunc_retry_err)
            self._set_last_response_meta(
                provider_requested=requested_provider,
                provider_used=result.get("provider_used", requested_provider),
                model_requested=requested_model,
                model_used=result.get("model_used", requested_model),
                auto_switch_used=auto_switch_used,
                auto_switch_reason=auto_switch_reason,
                fallback_used=False,
                fallback_reason=None,
                continuation_used=result.get("continuation_used", False),
                continuation_steps=result.get("continuation_steps", 0),
                finish_reason=result.get("finish_reason"),
                continuation_warning=result.get("continuation_warning"),
                text_may_be_incomplete=result.get("text_may_be_incomplete", False),
                prompt_tokens=result.get("prompt_tokens"),
                completion_tokens=result.get("completion_tokens"),
            )
            return result.get("text", "")
        except Exception as e:
            error_msg = str(e) or f"{type(e).__name__}"
            logger.error(f"❌ Erreur {provider.value} ({type(e).__name__}): {error_msg}")
            
            # Si c'est une erreur d'authentification, donner un conseil
            if "401" in error_msg or "Unauthorized" in error_msg:
                logger.warning(f"🔑 Clé API invalide pour {provider.value}. Vérifiez votre .env")
            elif "403" in error_msg or "Forbidden" in error_msg:
                logger.warning(f"🚫 Accès refusé pour {provider.value}. Quota dépassé ?")

            # Déterminer la variable pour _mark_failure
            provider_name = provider.value if hasattr(provider, 'value') else str(provider)

            # Marquer le provider défaillant AVANT le fallback (health tracking)
            self._mark_failure(provider_name)

            # Fallback intelligent : parcourt TOUTE la chaîne de providers sains
            tried_providers = [provider_name]
            fallback_errors = [f"{requested_provider}: {error_msg}"]
            current_fb = provider_name
            while True:
                fallback_provider_name = self._get_next_provider(current_fb)
                if fallback_provider_name is None and current_fb != "ollama" and "ollama" not in tried_providers:
                    fallback_provider_name = "ollama"  # dernier recours
                if fallback_provider_name is None or fallback_provider_name in tried_providers:
                    break  # plus de providers disponibles
                tried_providers.append(fallback_provider_name)
                fb_config = get_default_model_for_provider(fallback_provider_name)
                fb_model = fb_config.model_id if fb_config else "qwen3:8b"
                fb_provider = ProviderType(fallback_provider_name)
                logger.info(f"🔄 Fallback vers {fallback_provider_name}/{fb_model}...")
                try:
                    fallback_result = await self._chat_provider_result(
                        provider=fb_provider,
                        messages=messages,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        model=fb_model,
                        stop=stop,
                    )
                    fallback_result = await self._continue_if_needed(
                        provider=fb_provider,
                        base_messages=messages,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        initial_result=fallback_result,
                        model=fb_model,
                    )
                    if isinstance(fallback_result, str):
                        fallback_result = {"text": fallback_result, "finish_reason": "stop"}
                    self._mark_success(fallback_provider_name)
                    self._set_last_response_meta(
                        provider_requested=requested_provider,
                        provider_used=fallback_result.get("provider_used", fallback_provider_name),
                        model_requested=requested_model,
                        model_used=fallback_result.get("model_used", fb_model),
                        auto_switch_used=auto_switch_used,
                        auto_switch_reason=auto_switch_reason,
                        fallback_used=True,
                        fallback_reason=f"{requested_provider}: {error_msg}",
                        continuation_used=fallback_result.get("continuation_used", False),
                        continuation_steps=fallback_result.get("continuation_steps", 0),
                        finish_reason=fallback_result.get("finish_reason"),
                        continuation_warning=fallback_result.get("continuation_warning"),
                        prompt_tokens=fallback_result.get("prompt_tokens"),
                        completion_tokens=fallback_result.get("completion_tokens"),
                    )
                    return fallback_result.get("text", "")
                except Exception as fallback_error:
                    logger.error(f"❌ Fallback {fallback_provider_name} échoué: {fallback_error}")
                    self._mark_failure(fallback_provider_name)
                    fallback_errors.append(f"{fallback_provider_name}: {fallback_error}")
                    current_fb = fallback_provider_name
                    continue  # essayer le suivant

            # Tous les providers ont échoué
            if len(tried_providers) > 1:
                self._set_last_response_meta(
                    provider_requested=requested_provider,
                    provider_used=requested_provider,
                    model_requested=requested_model,
                    model_used=requested_model,
                    auto_switch_used=auto_switch_used,
                    auto_switch_reason=auto_switch_reason,
                    fallback_used=True,
                    fallback_reason="; ".join(fallback_errors),
                    continuation_used=False,
                    continuation_steps=0,
                    finish_reason="error",
                    continuation_warning=None,
                )
                return f"[Erreur] Tous les providers ont échoué: {'; '.join(fallback_errors)}"
            
            self._set_last_response_meta(
                provider_requested=requested_provider,
                provider_used=requested_provider,
                model_requested=requested_model,
                model_used=requested_model,
                auto_switch_used=auto_switch_used,
                auto_switch_reason=auto_switch_reason,
                fallback_used=False,
                fallback_reason=None,
                continuation_used=False,
                continuation_steps=0,
                finish_reason="error",
                continuation_warning=None,
            )
            return f"[Erreur] {error_msg}"
    
    async def _chat_ollama(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.7
    ) -> str:
        """Chat via Ollama (local)."""
        result = await self._chat_ollama_result(messages, model=model, temperature=temperature)
        return result.get("text", "")

    async def _chat_ollama_result(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.7,
        stop: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Chat via Ollama (local) with unified metadata payload."""
        url = f"{self.ollama_host}/api/chat"
        
        _options: Dict[str, Any] = {
            "temperature": temperature,
            "num_ctx": 32768
        }
        if stop:
            _options["stop"] = stop

        # Convertir messages multipart (OpenAI vision format) → format Ollama
        ollama_messages = []
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, list):
                # Format multipart: [{"type":"text","text":"..."}, {"type":"image_url","image_url":{"url":"data:...;base64,XXX"}}]
                text_parts = []
                images = []
                for part in content:
                    if part.get("type") == "text":
                        text_parts.append(part.get("text", ""))
                    elif part.get("type") == "image_url":
                        data_url = part.get("image_url", {}).get("url", "")
                        if ";base64," in data_url:
                            images.append(data_url.split(";base64,", 1)[1])
                ollama_msg: Dict[str, Any] = {"role": msg.get("role", "user"), "content": "\n".join(text_parts)}
                if images:
                    ollama_msg["images"] = images
                ollama_messages.append(ollama_msg)
            else:
                ollama_messages.append({"role": msg.get("role", "user"), "content": content})

        payload = {
            "model": model or self.model,
            "messages": ollama_messages,
            "stream": False,
            "options": _options
        }
        
        response = await self._http.post(url, json=payload)
        response.raise_for_status()
        data = response.json()
        _usage = data.get("usage") or {}
        return {
            "text": data.get("message", {}).get("content", "") or "",
            "finish_reason": data.get("done_reason"),
            "provider_used": ProviderType.OLLAMA.value,
            "model_used": payload["model"],
            "prompt_tokens": _usage.get("prompt_tokens") or data.get("prompt_eval_count"),
            "completion_tokens": _usage.get("completion_tokens") or data.get("eval_count"),
        }
    
    async def _chat_openai(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 16384  # Augmenté pour code long
    ) -> str:
        """Chat via OpenAI API."""
        result = await self._chat_openai_result(messages, temperature=temperature, max_tokens=max_tokens)
        return result.get("text", "")

    @staticmethod
    def _is_gpt5_model(model_id: str) -> bool:
        """Détecte si le modèle est un modèle OpenAI moderne (GPT-5.x ou reasoning o3/o4)."""
        m = (model_id or "").lower()
        return m.startswith("gpt-5") or m.startswith("o3") or m.startswith("o4")

    @staticmethod
    def _is_reasoning_model(model_id: str) -> bool:
        """Détecte si le modèle est un reasoning model pur (o3, o4-mini)."""
        m = (model_id or "").lower()
        return m.startswith("o3") or m.startswith("o4")

    @staticmethod
    def _prepare_openai_messages(messages: List[Dict[str, str]], model_id: str) -> List[Dict[str, str]]:
        """Convertit role:'system' → role:'developer' pour les modèles GPT-5.x."""
        if not MultiProviderLLM._is_gpt5_model(model_id):
            return messages
        out = []
        for msg in messages:
            if msg.get("role") == "system":
                out.append({**msg, "role": "developer"})
            else:
                out.append(msg)
        return out

    @staticmethod
    def _build_openai_payload(
        model: str,
        messages: List[Dict[str, Any]],
        *,
        temperature: float = 0.7,
        max_tokens: Optional[int] = 16384,
        stop: Optional[List[str]] = None,
        stream: bool = False,
        tools: Optional[List[Dict]] = None,
    ) -> Dict[str, Any]:
        """Construit un payload OpenAI Chat Completions adapté à la famille du modèle.

        Trois profils :
        - GPT-5.x : developer role, max_completion_tokens, pas de temperature/stop
        - Reasoning (o3, o4-mini) : idem + reasoning_effort optionnel
        - Legacy (gpt-4.1, gpt-4o, gpt-4o-mini) : system role, max_tokens, temperature, stop

        Si max_tokens est None, aucun plafond n'est envoyé (l'API décide).
        """
        _is_modern = MultiProviderLLM._is_gpt5_model(model)
        prepared = MultiProviderLLM._prepare_openai_messages(messages, model)

        payload: Dict[str, Any] = {
            "model": model,
            "messages": prepared,
        }

        if _is_modern:
            # GPT-5.x et reasoning : max_completion_tokens, pas de temperature/stop
            if max_tokens is not None:
                payload["max_completion_tokens"] = max_tokens
            reasoning_effort = os.getenv("LUMENA_OPENAI_REASONING_EFFORT", "").strip().lower()
            if reasoning_effort in {"none", "low", "medium", "high", "xhigh"}:
                payload["reasoning_effort"] = reasoning_effort
        else:
            # Legacy : paramètres classiques
            if max_tokens is not None:
                payload["max_tokens"] = max_tokens
            payload["temperature"] = temperature
            if stop:
                payload["stop"] = stop

        if stream:
            payload["stream"] = True

        if tools:
            payload["tools"] = tools

        return payload

    async def _chat_openai_result(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 16384,
        stop: Optional[List[str]] = None,
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Chat via OpenAI API with unified metadata payload."""
        api_key = get_api_key(ProviderType.OPENAI)
        if not api_key:
            raise ValueError("OPENAI_API_KEY non configurée")
        
        url = "https://api.openai.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        
        effective_model = model or self.model
        
        payload = self._build_openai_payload(
            effective_model, messages,
            temperature=temperature, max_tokens=max_tokens, stop=stop,
        )
        
        response = await self._http.post(url, headers=headers, json=payload)
        if response.status_code != 200:
            body = ""
            try:
                body = response.text[:1000]
            except Exception:
                pass
            logger.error(f"OpenAI {response.status_code}: {body}")
            response.raise_for_status()
        data = response.json()
        choice = data["choices"][0]
        _usage = data.get("usage") or {}
        return {
            "text": choice.get("message", {}).get("content", "") or "",
            "finish_reason": choice.get("finish_reason"),
            "provider_used": ProviderType.OPENAI.value,
            "model_used": effective_model,
            "prompt_tokens": _usage.get("prompt_tokens"),
            "completion_tokens": _usage.get("completion_tokens"),
        }
    
    async def _chat_anthropic(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 65536  # Max Claude Sonnet 4/4.5
    ) -> str:
        """Chat via Anthropic API (Claude)."""
        result = await self._chat_anthropic_result(messages, temperature=temperature, max_tokens=max_tokens)
        return result.get("text", "")

    async def _chat_anthropic_result(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 65536,
        stop: Optional[List[str]] = None,
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Chat via Anthropic API (Claude) with unified metadata payload."""
        api_key = get_api_key(ProviderType.ANTHROPIC)
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY non configurée")
        
        url = "https://api.anthropic.com/v1/messages"
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "anthropic-beta": "context-1m-2025-08-07",  # Active le context window 1M tokens
            "Content-Type": "application/json"
        }
        
        # Extraire le system prompt
        system = ""
        chat_messages = []
        for msg in messages:
            if msg["role"] == "system":
                system = msg["content"]
            else:
                chat_messages.append(msg)
        
        payload = {
            "model": model or self.model,
            "max_tokens": max_tokens,
            "messages": chat_messages,
            "temperature": temperature
        }
        # Opus 4.7+ (adaptive thinking models) refuse le paramètre temperature
        _model_id = payload["model"] or ""
        if "opus-4-7" in _model_id:
            payload.pop("temperature", None)
        if stop:
            payload["stop_sequences"] = stop
        if system:
            payload["system"] = system
        
        response = await self._http.post(url, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
        blocks = data.get("content", [])
        text_parts = [
            block.get("text", "")
            for block in blocks
            if isinstance(block, dict) and block.get("type") == "text"
        ]

        if not text_parts:
            raise ValueError("Reponse Anthropic sans contenu texte exploitable")

        _usage = data.get("usage") or {}
        return {
            "text": "".join(text_parts),
            "finish_reason": data.get("stop_reason"),
            "provider_used": ProviderType.ANTHROPIC.value,
            "model_used": payload["model"],
            "prompt_tokens": _usage.get("input_tokens"),
            "completion_tokens": _usage.get("output_tokens"),
        }
    
    async def _chat_google(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 65535  # Max Gemini 2.5
    ) -> str:
        """Chat via Google Gemini API."""
        result = await self._chat_google_result(messages, temperature=temperature, max_tokens=max_tokens)
        return result.get("text", "")

    async def _chat_google_result(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 65535,
        stop: Optional[List[str]] = None,
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Chat via Google Gemini API with unified metadata payload."""
        api_key = get_api_key(ProviderType.GOOGLE)
        if not api_key:
            raise ValueError("GOOGLE_API_KEY non configurée")
        
        effective_model = model or self.model
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{effective_model}:generateContent?key={api_key}"
        
        # Convertir le format des messages pour Gemini
        system_instruction = ""
        contents = []
        
        for msg in messages:
            if msg["role"] == "system":
                system_instruction = msg["content"]
            elif msg["role"] == "user":
                contents.append({"role": "user", "parts": [{"text": msg["content"]}]})
            elif msg["role"] == "assistant":
                contents.append({"role": "model", "parts": [{"text": msg["content"]}]})
        
        _gen_config: Dict[str, Any] = {
                "temperature": temperature,
                "maxOutputTokens": max_tokens
            }
        if stop:
            _gen_config["stopSequences"] = stop
        payload = {
            "contents": contents,
            "generationConfig": _gen_config
        }
        if system_instruction:
            payload["systemInstruction"] = {"parts": [{"text": system_instruction}]}
        
        response = await self._http.post(url, json=payload)
        response.raise_for_status()
        data = response.json()

        # Parser la réponse de manière robuste
        try:
            candidates = data.get("candidates", [])
            if not candidates:
                # Vérifier s'il y a une erreur
                if "error" in data:
                    raise ValueError(f"Gemini error: {data['error'].get('message', str(data['error']))}")
                raise ValueError("Pas de réponse de Gemini")

            content = candidates[0].get("content", {})
            parts = content.get("parts", [])

            finish_reason = candidates[0].get("finishReason")
            if not parts:
                # Parfois la réponse est bloquée
                if finish_reason == "SAFETY":
                    return {
                        "text": "[Réponse bloquée par les filtres de sécurité Gemini]",
                        "finish_reason": finish_reason,
                        "provider_used": ProviderType.GOOGLE.value,
                        "model_used": effective_model,
                        "prompt_tokens": None,
                        "completion_tokens": None,
                    }
                raise ValueError(f"Réponse vide (finishReason: {finish_reason})")

            text_parts = [
                part.get("text", "")
                for part in parts
                if isinstance(part, dict) and part.get("text")
            ]
            if not text_parts:
                raise ValueError("Aucune partie texte exploitable dans la reponse Gemini")

            _usage_meta = data.get("usageMetadata") or {}
            return {
                "text": "".join(text_parts),
                "finish_reason": finish_reason,
                "provider_used": ProviderType.GOOGLE.value,
                "model_used": effective_model,
                "prompt_tokens": _usage_meta.get("promptTokenCount"),
                "completion_tokens": _usage_meta.get("candidatesTokenCount"),
            }
        except KeyError as e:
            logger.error(f"Structure réponse Gemini inattendue: {data}")
            raise ValueError(f"Erreur parsing Gemini: {e}")
    
    async def _chat_moonshot(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 32768  # Kimi K2.5 max output
    ) -> str:
        """Chat via Moonshot API (Kimi)."""
        result = await self._chat_moonshot_result(messages, temperature=temperature, max_tokens=max_tokens)
        return result.get("text", "")

    async def _chat_moonshot_result(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 32768,
        stop: Optional[List[str]] = None,
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Chat via Moonshot API (Kimi) with unified metadata payload.
        
        NOTE: For kimi-k2.5, temperature/top_p cannot be modified (API restriction).
        Temperature is fixed at 1.0 for Thinking mode, 0.6 for Instant mode.
        """
        api_key = get_api_key(ProviderType.MOONSHOT)
        if not api_key:
            raise ValueError("MOONSHOT_API_KEY non configurée")
        
        # En fallback, model est passé explicitement (ex: kimi-k2.5)
        # self.model vaut deepseek-chat en fallback → toujours utiliser le param model si fourni
        target_model = model or self.model
        
        # Moonshot utilise un format compatible OpenAI
        # URL officielle: https://api.moonshot.ai/v1
        url = "https://api.moonshot.ai/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": target_model,
            "messages": messages,
            "max_tokens": max_tokens
        }
        if stop:
            payload["stop"] = stop
        
        # Pour kimi-k2.5, temperature ne peut pas être modifiée
        # On ne l'envoie que pour les anciens modèles moonshot-v1
        if not target_model.startswith("kimi-k2"):
            payload["temperature"] = temperature
        
        try:
            response = await self._http.post(url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
            choice = data["choices"][0]
            _usage = data.get("usage") or {}
            return {
                "text": choice.get("message", {}).get("content", "") or "",
                "finish_reason": choice.get("finish_reason"),
                "provider_used": ProviderType.MOONSHOT.value,
                "model_used": payload["model"],
                "prompt_tokens": _usage.get("prompt_tokens"),
                "completion_tokens": _usage.get("completion_tokens"),
            }
        except httpx.HTTPStatusError as e:
            # Capturer le détail de l'erreur API
            error_detail = ""
            try:
                error_detail = e.response.text
            except Exception:
                pass  # Response body not readable
            logger.error(f"❌ Erreur Moonshot HTTP {e.response.status_code}: {error_detail[:1000]}")
            raise
    
    async def _chat_deepseek(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 32768,  # DeepSeek Reasoner supporte 32K+, V3.2 supporte 8K (limite API)
        model: Optional[str] = None,
    ) -> str:
        """Chat via DeepSeek API (compatible OpenAI).
        
        FIX: DeepSeek Reasoner renvoie la réponse dans reasoning_content,
        pas dans content. On doit lire les deux champs.
        """
        result = await self._chat_deepseek_result(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            model=model,
        )
        return result.get("text", "")

    async def _chat_xai(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 16384,
        model: Optional[str] = None,
    ) -> str:
        """Chat via xAI API (Grok)."""
        result = await self._chat_xai_result(messages, temperature=temperature, max_tokens=max_tokens, model=model)
        return result.get("text", "")

    async def _chat_xai_result(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 16384,
        model: Optional[str] = None,
        stop: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Chat via xAI API (Grok) with unified metadata payload.
        
        xAI utilise un format compatible OpenAI.
        URL : https://api.x.ai/v1/chat/completions
        """
        api_key = get_api_key(ProviderType.XAI)
        if not api_key:
            raise ValueError("XAI_API_KEY non configurée")

        target_model = model or self.model
        url = "https://api.x.ai/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": target_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if stop:
            payload["stop"] = stop
        # grok-4-1-fast-reasoning : le thinking interne est géré côté API,
        # pas besoin de paramètre spécial (pas de temperature fixée comme Kimi)

        try:
            response = await self._http.post(url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
            choice = data["choices"][0]
            content = choice.get("message", {}).get("content", "") or ""
            _usage = data.get("usage") or {}
            return {
                "text": content,
                "finish_reason": choice.get("finish_reason"),
                "provider_used": ProviderType.XAI.value,
                "model_used": target_model,
                "prompt_tokens": _usage.get("prompt_tokens"),
                "completion_tokens": _usage.get("completion_tokens"),
            }
        except httpx.HTTPStatusError as e:
            error_detail = ""
            try:
                error_detail = e.response.text
            except Exception:
                pass  # response body non lisible
            logger.error(f"❌ Erreur xAI HTTP {e.response.status_code}: {error_detail[:1000]}")
            raise

    async def _chat_mistral(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 16384,
        model: Optional[str] = None,
    ) -> str:
        """Chat via Mistral API."""
        result = await self._chat_mistral_result(messages, temperature=temperature, max_tokens=max_tokens, model=model)
        return result.get("text", "")

    async def _chat_mistral_result(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 16384,
        model: Optional[str] = None,
        stop: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Chat via Mistral API (compatible OpenAI).

        URL : https://api.mistral.ai/v1/chat/completions
        """
        api_key = get_api_key(ProviderType.MISTRAL)
        if not api_key:
            raise ValueError("MISTRAL_API_KEY non configurée")

        target_model = model or self.model
        url = "https://api.mistral.ai/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload: Dict[str, Any] = {
            "model": target_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if stop:
            payload["stop"] = stop

        try:
            response = await self._http.post(url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
            choice = data["choices"][0]
            content = choice.get("message", {}).get("content", "") or ""
            _usage = data.get("usage") or {}
            return {
                "text": content,
                "finish_reason": choice.get("finish_reason"),
                "provider_used": ProviderType.MISTRAL.value,
                "model_used": target_model,
                "prompt_tokens": _usage.get("prompt_tokens"),
                "completion_tokens": _usage.get("completion_tokens"),
            }
        except httpx.HTTPStatusError as e:
            error_detail = ""
            try:
                error_detail = e.response.text
            except Exception:
                pass
            logger.error(f"❌ Erreur Mistral HTTP {e.response.status_code}: {error_detail[:1000]}")
            raise

    async def _chat_nvidia(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 32768,
        model: Optional[str] = None,
    ) -> str:
        """Chat via NVIDIA NIM API (Kimi models gratuits)."""
        result = await self._chat_nvidia_result(messages, temperature=temperature, max_tokens=max_tokens, model=model)
        return result.get("text", "")

    async def _chat_nvidia_result(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 32768,
        model: Optional[str] = None,
        stop: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Chat via NVIDIA NIM API with unified metadata payload.
        
        NVIDIA NIM utilise un format compatible OpenAI.
        URL : https://integrate.api.nvidia.com/v1/chat/completions
        7 modèles gratuits: kimi-k2-instruct, kimi-k2-instruct-0905, kimi-k2-thinking, deepseek-v3.2, deepseek-v3.1, glm-4.7, minimax-m2.5
        """
        api_key = get_api_key(ProviderType.NVIDIA)
        if not api_key:
            raise ValueError("NVIDIA_API_KEY non configurée")

        # Résoudre le model_id NVIDIA depuis le nom interne ou model_id
        raw = model or self.model  # peut être model_id OU model_name
        from src.llm.providers import get_model_config
        # Chercher d'abord par name, puis par model_id
        cfg = get_model_config(raw)
        if not cfg:
            for m in AVAILABLE_MODELS.values():
                if m.model_id == raw and m.provider == ProviderType.NVIDIA:
                    cfg = m
                    break
        target_model = cfg.model_id if cfg else raw
        url = "https://integrate.api.nvidia.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": target_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if stop:
            payload["stop"] = stop

        try:
            response = await self._http.post(url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
            choices = data.get("choices") or []
            if not choices:
                raise ValueError(f"NVIDIA NIM: réponse vide (pas de choices) pour {target_model}")
            choice = choices[0]
            content = choice.get("message", {}).get("content", "") or ""
            _usage = data.get("usage") or {}
            return {
                "text": content,
                "finish_reason": choice.get("finish_reason"),
                "provider_used": ProviderType.NVIDIA.value,
                "model_used": target_model,
                "prompt_tokens": _usage.get("prompt_tokens"),
                "completion_tokens": _usage.get("completion_tokens"),
            }
        except httpx.HTTPStatusError as e:
            error_detail = ""
            try:
                error_detail = e.response.text
            except Exception:
                pass
            logger.error(f"❌ Erreur NVIDIA NIM HTTP {e.response.status_code}: {error_detail[:1000]}")
            raise
        except httpx.TimeoutException:
            logger.error(f"❌ Erreur NVIDIA NIM: timeout 300s pour {target_model}")
            raise
        except httpx.ConnectError as e:
            logger.error(f"❌ Erreur NVIDIA NIM connexion: {e}")
            raise

    async def _chat_deepseek_result(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 32768,
        model: Optional[str] = None,
        stop: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Chat via DeepSeek API with unified metadata payload."""
        api_key = get_api_key(ProviderType.DEEPSEEK)
        if not api_key:
            raise ValueError("DEEPSEEK_API_KEY non configurée")
        
        # DeepSeek utilise un format compatible OpenAI
        url = "https://api.deepseek.com/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": model or self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens
        }
        if stop:
            payload["stop"] = stop
        
        response = await self._http.post(url, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()

        choice = data["choices"][0]
        message = choice.get("message") or {}
        # Defensive: certaines réponses DeepSeek (rate limit, erreur partielle)
        # retournent message comme str au lieu de dict
        if isinstance(message, str):
            message = {"content": message, "role": "assistant"}
        content = message.get("content", "") or ""

        # FIX: DeepSeek Reasoner met la réponse dans reasoning_content quand content est vide
        reasoning_content = message.get("reasoning_content", "")

        if not content.strip() and reasoning_content:
            # Essayer d'extraire un bloc THOUGHT/ACTION valide depuis reasoning_content
            import re as _re
            _thought_match = _re.search(
                r'(THOUGHT:\s*.+?\nACTION:\s*.+?\nACTION_INPUT:\s*.+)',
                reasoning_content,
                _re.DOTALL
            )
            if _thought_match:
                logger.warning("⚠️ DeepSeek: content vide, extraction THOUGHT/ACTION depuis reasoning_content")
                content = _thought_match.group(1)
            else:
                # Chercher le DERNIER bloc JSON CodeAgent dans le raisonnement
                # (le dernier = la décision finale, pas les étapes de réflexion intermédiaires)
                # Utilise brace-depth pour gérer les JSON imbriqués (write_file avec code HTML etc.)
                try:
                    from src.llm.output_normalizer import extract_json_object as _ejson
                    # Chercher toutes les occurrences de { en ordre inverse pour prendre la dernière
                    _last_json = None
                    _search_text = reasoning_content
                    _pos = 0
                    _candidates = []
                    while True:
                        _brace = _search_text.find("{", _pos)
                        if _brace < 0:
                            break
                        _sub = _search_text[_brace:]
                        _parsed = _ejson(_sub)
                        if _parsed and "action" in _parsed:
                            _candidates.append(_parsed)
                        _pos = _brace + 1
                    if _candidates:
                        _last_json = _candidates[-1]  # dernière décision du LLM
                        import json as _json_mod
                        content = _json_mod.dumps(_last_json)
                        logger.warning("⚠️ DeepSeek: content vide, extraction JSON action depuis reasoning_content ({} candidats, dernier retenu)", len(_candidates))
                    else:
                        # FIX: Ne pas utiliser du texte descriptif comme code
                        _fence = _re.search(r'```(?:\w*)\n(.+?)```', reasoning_content, _re.DOTALL)
                        if _fence:
                            content = _fence.group(1).strip()
                            logger.warning("⚠️ DeepSeek: content vide, code fenced extrait de reasoning_content")
                        elif any(m in reasoning_content for m in ('import ', 'export ', 'function ', 'const ', 'def ', 'THOUGHT:', 'ACTION:')):
                            content = reasoning_content
                            logger.warning("⚠️ DeepSeek: content vide, reasoning_content utilisé (contient du code)")
                        elif len(reasoning_content.strip()) >= 200:
                            content = reasoning_content.strip()
                            logger.warning("⚠️ DeepSeek: content vide, reasoning_content descriptif accepté comme fallback ({} chars)", len(content))
                        else:
                            logger.error("🚨 DeepSeek: content vide, reasoning_content trop court ({} chars) → rejeté", len(reasoning_content.strip()))
                            content = ""
                except Exception:
                    _fence = _re.search(r'```(?:\w*)\n(.+?)```', reasoning_content, _re.DOTALL)
                    if _fence:
                        content = _fence.group(1).strip()
                        logger.warning("⚠️ DeepSeek: content vide, code fenced extrait (exception)")
                    elif any(m in reasoning_content for m in ('import ', 'export ', 'function ', 'const ', 'def ', 'THOUGHT:', 'ACTION:')):
                        content = reasoning_content
                        logger.warning("⚠️ DeepSeek: content vide, reasoning_content utilisé (exception)")
                    elif len(reasoning_content.strip()) >= 200:
                        content = reasoning_content.strip()
                        logger.warning("⚠️ DeepSeek: content vide, reasoning_content descriptif accepté (exception, {} chars)", len(content))
                    else:
                        content = ""
                        logger.error("🚨 DeepSeek: content vide, reasoning_content trop court ({} chars) → rejeté (exception)", len(reasoning_content.strip()))

        # FIX: Détecter les réponses tronquées par limite de tokens
        finish_reason = choice.get("finish_reason", "")
        _truncated = False
        if finish_reason == "length":
            # Vérifier si le contenu semble incomplet (HTML/CSS/JS non fermé)
            content_lower = content.lower()
            incomplete_signs = [
                ("</html>" not in content_lower and "<html" in content_lower),
                ("</body>" not in content_lower and "<body" in content_lower),
                (content.count("{") > content.count("}")),  # JS/CSS non fermé
                (content.count("(") > content.count(")")),  # Fonctions non fermées
            ]
            if any(incomplete_signs):
                _truncated = True
                _used_model = payload.get("model", "deepseek-chat")
                _used_max = max_tokens
                logger.error("🚨 TRONCATURE DÉTECTÉE: {} a atteint sa limite de {} tokens (finish_reason=length)!", _used_model, _used_max)
                if "reasoner" not in str(_used_model):
                    logger.error("💡 SOLUTION: Utilisez 'deepseek-reasoner' pour la génération de code (64K tokens)")

        _usage = data.get("usage") or {}
        return {
            "text": content,
            "finish_reason": finish_reason,
            "provider_used": ProviderType.DEEPSEEK.value,
            "model_used": payload["model"],
            "truncated": _truncated,
            "prompt_tokens": _usage.get("prompt_tokens"),
            "completion_tokens": _usage.get("completion_tokens"),
        }

    async def _chat_minimax_result(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 32768,
        model: Optional[str] = None,
        stop: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Chat via MiniMax API (OpenAI-compatible)."""
        api_key = get_api_key(ProviderType.MINIMAX)
        if not api_key:
            raise ValueError("MINIMAX_API_KEY non configurée")

        target_model = model or self.model
        cfg = get_model_config(target_model)
        model_id = cfg.model_id if cfg else "MiniMax-M2.5"

        url = "https://api.minimax.io/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        # MiniMax API refuse temperature=0.0 → clamp min 0.01
        safe_temp = max(temperature, 0.01)

        payload: Dict[str, Any] = {
            "model": model_id,
            "messages": messages,
            "temperature": safe_temp,
            "max_tokens": max_tokens,
        }
        if stop:
            payload["stop"] = stop

        timeout = httpx.Timeout(connect=10.0, read=300.0, write=10.0, pool=10.0)
        response = await self._http.post(url, headers=headers, json=payload, timeout=timeout)
        response.raise_for_status()
        data = response.json()
        choice = data["choices"][0]
        content = choice.get("message", {}).get("content", "") or ""
        _usage = data.get("usage") or {}
        return {
            "text": content,
            "finish_reason": choice.get("finish_reason"),
            "provider_used": ProviderType.MINIMAX.value,
            "model_used": model_id,
            "prompt_tokens": _usage.get("prompt_tokens"),
            "completion_tokens": _usage.get("completion_tokens"),
        }

    async def _chat_zai(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 32768,
        model: Optional[str] = None,
    ) -> str:
        """Chat via Z.AI API (GLM models)."""
        result = await self._chat_zai_result(messages, temperature=temperature, max_tokens=max_tokens, model=model)
        return result.get("text", "")

    # Flag session : True si Z.AI a retourné une erreur de solde insuffisant (1113)
    # → évite les retries inutiles jusqu'au prochain redémarrage
    _zai_balance_exhausted: bool = False

    async def _chat_zai_result(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 32768,
        model: Optional[str] = None,
        stop: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Chat via Z.AI API (GLM-5.1, GLM-4.7-Flash...) — format compatible OpenAI.

        URL : https://api.z.ai/api/paas/v4/chat/completions
        Auth : Bearer {ZAI_API_KEY}
        """
        if self.__class__._zai_balance_exhausted:
            raise ValueError("Z.AI désactivé (solde épuisé) — rechargez votre compte Z.AI")

        api_key = get_api_key(ProviderType.ZAI)
        if not api_key:
            raise ValueError("ZAI_API_KEY non configurée")

        target_model = model or self.model
        cfg = get_model_config(target_model)
        model_id = cfg.model_id if cfg else target_model

        base_url = os.getenv("ZAI_BASE_URL", "https://api.z.ai/api/paas/v4")
        url = f"{base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload: Dict[str, Any] = {
            "model": model_id,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if stop:
            payload["stop"] = stop

        try:
            response = await self._http.post(url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
            choice = data["choices"][0]
            content = choice.get("message", {}).get("content", "") or ""
            _usage = data.get("usage") or {}
            return {
                "text": content,
                "finish_reason": choice.get("finish_reason"),
                "provider_used": ProviderType.ZAI.value,
                "model_used": model_id,
                "prompt_tokens": _usage.get("prompt_tokens"),
                "completion_tokens": _usage.get("completion_tokens"),
            }
        except httpx.HTTPStatusError as e:
            error_detail = ""
            try:
                error_detail = e.response.text
            except Exception:
                pass
            # Code 1113 = solde insuffisant (erreur permanente — désactiver pour la session)
            if e.response.status_code == 429 and '"code":"1113"' in error_detail:
                self.__class__._zai_balance_exhausted = True
                logger.warning("⚠️ Z.AI solde épuisé (code 1113) — provider désactivé pour cette session")
                raise ValueError("Z.AI désactivé (solde épuisé) — rechargez votre compte Z.AI") from e
            logger.error(f"❌ Erreur Z.AI HTTP {e.response.status_code}: {error_detail[:1000]}")
            raise

    async def describe_image(self, image_path: str, prompt: str = "") -> str:
        """Décrit une image via vision API.

        Utilise automatiquement le meilleur modèle vision disponible
        (LUMENA_BRAIN_VISION ou sélection automatique via best_model_for("vision")).
        Supporte : OpenAI, Anthropic, Google Gemini, xAI Grok.

        Retourne la description textuelle, ou une chaîne vide si la vision
        n'est pas disponible.
        """
        import base64
        import mimetypes
        from pathlib import Path as _P

        p = _P(image_path)
        if not p.exists():
            return ""

        # Déterminer le meilleur modèle vision disponible
        try:
            from src.llm.providers import get_brain_model, get_model_config, ProviderType as _PT
            brain_name = get_brain_model("vision")
            if brain_name:
                brain_cfg = get_model_config(brain_name)
                if brain_cfg and brain_cfg.supports_vision:
                    vision_provider = brain_cfg.provider
                    vision_model_id = brain_cfg.model_id
                else:
                    brain_name = None
        except Exception:
            brain_name = None

        # Fallback : provider actuel si vision supportée
        if not brain_name:
            provider = self.provider
            if provider not in (ProviderType.OPENAI, ProviderType.ANTHROPIC,
                                ProviderType.GOOGLE, ProviderType.XAI, ProviderType.OLLAMA):
                return ""
            vision_provider = provider
            vision_model_id = self.model_name

        user_prompt = prompt or (
            "Décris cette image en détail : contenu, couleurs, personnes, texte visible, "
            "ambiance générale. Réponds en français, de façon claire et concise."
        )
        
        try:
            raw_bytes = p.read_bytes()
            b64 = base64.b64encode(raw_bytes).decode()
            mime, _ = mimetypes.guess_type(str(p))
            mime = mime or "image/jpeg"
        except Exception as e:
            logger.warning(f"Lecture image vision: {e}")
            return ""

        try:
            if vision_provider == ProviderType.OPENAI:
                api_key = get_api_key(ProviderType.OPENAI)
                if not api_key:
                    return ""
                payload = {
                    "model": vision_model_id,
                    "max_completion_tokens": 1024,
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                                {"type": "text", "text": user_prompt},
                            ],
                        }
                    ],
                }
                resp = await self._http.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json=payload,
                    timeout=60.0,
                )
                resp.raise_for_status()
                return resp.json()["choices"][0]["message"]["content"] or ""

            elif vision_provider == ProviderType.ANTHROPIC:
                api_key = get_api_key(ProviderType.ANTHROPIC)
                if not api_key:
                    return ""
                payload = {
                    "model": vision_model_id,
                    "max_tokens": 1024,
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "image",
                                    "source": {"type": "base64", "media_type": mime, "data": b64},
                                },
                                {"type": "text", "text": user_prompt},
                            ],
                        }
                    ],
                }
                resp = await self._http.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": api_key,
                        "anthropic-version": "2023-06-01",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=60.0,
                )
                resp.raise_for_status()
                content = resp.json().get("content", [])
                return next((c["text"] for c in content if c.get("type") == "text"), "")

            elif vision_provider == ProviderType.GOOGLE:
                api_key = get_api_key(ProviderType.GOOGLE)
                if not api_key:
                    return ""
                payload = {
                    "contents": [
                        {
                            "parts": [
                                {"inline_data": {"mime_type": mime, "data": b64}},
                                {"text": user_prompt},
                            ]
                        }
                    ]
                }
                model_id = vision_model_id or "gemini-2.5-flash"
                resp = await self._http.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{model_id}:generateContent",
                    headers={"Content-Type": "application/json"},
                    params={"key": api_key},
                    json=payload,
                    timeout=60.0,
                )
                resp.raise_for_status()
                candidates = resp.json().get("candidates", [])
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts", [])
                    return "".join(pt.get("text", "") for pt in parts)
                return ""

            elif vision_provider == ProviderType.XAI:
                api_key = get_api_key(ProviderType.XAI)
                if not api_key:
                    return ""
                payload = {
                    "model": vision_model_id,
                    "max_tokens": 1024,
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                                {"type": "text", "text": user_prompt},
                            ],
                        }
                    ],
                }
                resp = await self._http.post(
                    "https://api.x.ai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json=payload,
                    timeout=60.0,
                )
                resp.raise_for_status()
                return resp.json()["choices"][0]["message"]["content"] or ""

            elif vision_provider == ProviderType.OLLAMA:
                # Ollama vision: champ "images" avec base64 (sans préfixe data:)
                ollama_host = self.ollama_host
                model_id = vision_model_id or "minicpm-v"
                # Résoudre model_id → le vrai id Ollama (ex: "gemma4-26b" → "gemma4:26b")
                try:
                    cfg = get_model_config(model_id)
                    if cfg:
                        model_id = cfg.model_id
                except Exception:
                    pass
                payload = {
                    "model": model_id,
                    "messages": [
                        {
                            "role": "user",
                            "content": user_prompt,
                            "images": [b64],
                        }
                    ],
                    "stream": False,
                    "options": {"num_ctx": 8192},
                }
                resp = await self._http.post(
                    f"{ollama_host}/api/chat",
                    json=payload,
                    timeout=120.0,
                )
                resp.raise_for_status()
                return resp.json().get("message", {}).get("content", "") or ""

        except httpx.HTTPStatusError as e:
            # Log le body complet pour diagnostic (modèle invalide, format, quota…)
            body = ""
            try:
                body = e.response.text[:500]
            except Exception:
                pass
            logger.warning(
                f"Vision API ({vision_provider} / {vision_model_id}): "
                f"HTTP {e.response.status_code} — {body or e}"
            )
            # Fallback auto sur Gemini si l'erreur vient d'ailleurs (bad model, auth…)
            if vision_provider != ProviderType.GOOGLE:
                try:
                    gemini_key = get_api_key(ProviderType.GOOGLE)
                    if gemini_key:
                        logger.info(f"Vision fallback → Gemini (après {vision_provider.value} échec)")
                        payload = {
                            "contents": [{
                                "parts": [
                                    {"inline_data": {"mime_type": mime, "data": b64}},
                                    {"text": user_prompt},
                                ]
                            }]
                        }
                        resp = await self._http.post(
                            "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent",
                            headers={"Content-Type": "application/json"},
                            params={"key": gemini_key},
                            json=payload,
                            timeout=60.0,
                        )
                        resp.raise_for_status()
                        candidates = resp.json().get("candidates", [])
                        if candidates:
                            parts = candidates[0].get("content", {}).get("parts", [])
                            return "".join(pt.get("text", "") for pt in parts)
                except Exception as fallback_err:
                    logger.warning(f"Vision fallback Gemini: {fallback_err}")
            return ""
        except Exception as e:
            logger.warning(f"Vision API ({vision_provider} / {vision_model_id}): {e}")
            return ""
        return ""

    async def _detect_ollama_vision_models(self) -> List[str]:
        """Détecte les modèles vision locaux installés sur Ollama.

        Retourne la liste des noms de modèles dont le nom contient un
        pattern vision connu (llava, vision, vl, moondream, minicpm-v, bakllava…).
        Préserve l'ordre d'installation Ollama (plus récent en premier côté API).
        """
        import os
        host = os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
        try:
            resp = await self._http.get(f"{host}/api/tags", timeout=5.0)
            resp.raise_for_status()
            models = resp.json().get("models", [])
        except Exception:
            return []
        vision_patterns = ("llava", "vision", "-vl", ":vl", "moondream",
                           "minicpm-v", "bakllava", "cogvlm", "qwen2-vl",
                           "llama3.2-vision", "llama-3.2-vision", "pixtral", "gemma3")
        result = []
        for m in models:
            name = (m.get("name") or "").lower()
            if any(p in name for p in vision_patterns):
                result.append(m.get("name"))
        return result

    async def describe_image_cascade(
        self,
        image_path: str,
        prompt: str = "",
        max_chars: int = 600,
    ) -> str:
        """Décrit une image via cascade GRATUIT → payant.

        Ordre de fallback :
          1. 🥇 Ollama local (auto-détection llava/moondream/qwen2-vl/minicpm-v…)
          2. 🥈 Google Gemini Flash (free tier)
          3. 🥉 describe_image() classique (tout autre provider configuré)

        Retourne une description non-vide, ou "" si aucun provider n'est disponible.
        Pilotable via LUMENA_VISION_CASCADE=0 pour désactiver (force describe_image).
        """
        import os, base64, mimetypes
        from pathlib import Path as _P

        if os.getenv("LUMENA_VISION_CASCADE", "1") not in ("1", "true", "True"):
            return await self.describe_image(image_path, prompt)

        p = _P(image_path)
        if not p.exists():
            return ""

        user_prompt = prompt or (
            "Décris cette capture d'écran en 3-5 lignes : que voit-on à l'écran ? "
            "Éléments interactifs visibles, texte important, état de la page. "
            "Réponds en français, concis."
        )

        try:
            raw_bytes = p.read_bytes()
            b64 = base64.b64encode(raw_bytes).decode()
            mime, _ = mimetypes.guess_type(str(p))
            mime = mime or "image/png"
        except Exception as e:
            logger.warning(f"[vision-cascade] lecture image: {e}")
            return ""

        # ── Tier 1 : Ollama local ──
        try:
            ollama_models = await self._detect_ollama_vision_models()
        except Exception:
            ollama_models = []
        for model_name in ollama_models[:3]:  # max 3 tentatives locales
            try:
                host = os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
                payload = {
                    "model": model_name,
                    "messages": [{
                        "role": "user",
                        "content": user_prompt,
                        "images": [b64],
                    }],
                    "stream": False,
                    "options": {"num_ctx": 4096, "num_predict": 400},
                }
                resp = await self._http.post(f"{host}/api/chat", json=payload, timeout=45.0)
                resp.raise_for_status()
                text = (resp.json().get("message") or {}).get("content", "").strip()
                if text:
                    logger.info(f"[vision-cascade] ✅ Ollama {model_name} ({len(text)} chars)")
                    return text[:max_chars]
            except Exception as e:
                logger.debug(f"[vision-cascade] Ollama {model_name} échec: {e}")
                continue

        # ── Tier 2 : Gemini Flash (free tier) ──
        try:
            from .providers import get_api_key as _gk, ProviderType as _PT
            gkey = _gk(_PT.GOOGLE)
            if gkey:
                payload = {
                    "contents": [{
                        "parts": [
                            {"inline_data": {"mime_type": mime, "data": b64}},
                            {"text": user_prompt},
                        ]
                    }]
                }
                resp = await self._http.post(
                    "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent",
                    headers={"Content-Type": "application/json"},
                    params={"key": gkey},
                    json=payload,
                    timeout=45.0,
                )
                resp.raise_for_status()
                candidates = resp.json().get("candidates", [])
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts", [])
                    text = "".join(pt.get("text", "") for pt in parts).strip()
                    if text:
                        logger.info(f"[vision-cascade] ✅ Gemini Flash ({len(text)} chars)")
                        return text[:max_chars]
        except Exception as e:
            logger.debug(f"[vision-cascade] Gemini échec: {e}")

        # ── Tier 3 : fallback sur describe_image classique (provider payant éventuel) ──
        try:
            text = await self.describe_image(image_path, prompt=user_prompt)
            if text:
                return text[:max_chars]
        except Exception as e:
            logger.debug(f"[vision-cascade] describe_image échec: {e}")

        return ""

    async def stream_chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 16384,
    ) -> AsyncIterator[str]:
        """
        Chat en streaming réel (SSE) — yield des chunks de texte au fur et à mesure.

        Supporte tous les providers : Ollama, OpenAI, DeepSeek, Anthropic, Google, xAI, Moonshot.
        Fallback : si le streaming échoue, fait un chat normal et yield le résultat.
        """
        provider = self.provider
        model = self.model

        try:
            if provider == ProviderType.OLLAMA:
                async for chunk in self._stream_ollama(messages, model, temperature):
                    yield chunk
            elif provider == ProviderType.OPENAI:
                async for chunk in self._stream_openai_compat(
                    messages, temperature, min(max_tokens, 16384),
                    url="https://api.openai.com/v1/chat/completions",
                    api_key=get_api_key(ProviderType.OPENAI),
                    model=model,
                    max_tokens_key="max_completion_tokens",
                    is_openai=True,
                ):
                    yield chunk
            elif provider == ProviderType.DEEPSEEK:
                async for chunk in self._stream_openai_compat(
                    messages, temperature, min(max_tokens, 8192),
                    url="https://api.deepseek.com/chat/completions",
                    api_key=get_api_key(ProviderType.DEEPSEEK),
                    model=model,
                ):
                    yield chunk
            elif provider == ProviderType.XAI:
                async for chunk in self._stream_openai_compat(
                    messages, temperature, min(max_tokens, 131072),
                    url="https://api.x.ai/v1/chat/completions",
                    api_key=get_api_key(ProviderType.XAI),
                    model=model,
                ):
                    yield chunk
            elif provider == ProviderType.MOONSHOT:
                api_key = get_api_key(ProviderType.MOONSHOT)
                base_url = os.getenv("MOONSHOT_BASE_URL", "https://api.moonshot.cn/v1")
                async for chunk in self._stream_openai_compat(
                    messages, temperature, min(max_tokens, 262144),
                    url=f"{base_url}/chat/completions",
                    api_key=api_key,
                    model=model,
                ):
                    yield chunk
            elif provider == ProviderType.ANTHROPIC:
                async for chunk in self._stream_anthropic(messages, temperature, min(max_tokens, 65536)):
                    yield chunk
            elif provider == ProviderType.GOOGLE:
                async for chunk in self._stream_google(messages, temperature, min(max_tokens, 65535)):
                    yield chunk
            else:
                # Fallback: chat normal
                response = await self.chat(messages, temperature=temperature)
                yield response
        except Exception as e:
            logger.warning(f"Streaming {provider.value} échoué ({e}), fallback chat normal")
            response = await self.chat(messages, temperature=temperature)
            yield response

    # ─── Streaming helpers ─────────────────────────────────────────

    async def _stream_ollama(
        self,
        messages: List[Dict[str, str]],
        model: str,
        temperature: float,
    ) -> AsyncIterator[str]:
        """Stream depuis Ollama (format JSON ligne par ligne)."""
        url = f"{self.ollama_host}/api/chat"
        payload = {
            "model": model,
            "messages": messages,
            "stream": True,
            "options": {"temperature": temperature, "num_ctx": 32768},
        }
        async with self._http.stream("POST", url, json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                    content = data.get("message", {}).get("content", "")
                    if content:
                        yield content
                    if data.get("done"):
                        break
                except json.JSONDecodeError:
                    continue  # ligne SSE invalide

    async def _stream_openai_compat(
        self,
        messages: List[Dict[str, str]],
        temperature: float,
        max_tokens: int,
        *,
        url: str,
        api_key: str,
        model: str,
        max_tokens_key: str = "max_tokens",
        is_openai: bool = False,
    ) -> AsyncIterator[str]:
        """Stream depuis un API compatible OpenAI (OpenAI, DeepSeek, xAI, Moonshot)."""
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        if is_openai:
            # Utilise le builder centralisé pour OpenAI natif
            payload = self._build_openai_payload(
                model, messages,
                temperature=temperature, max_tokens=max_tokens, stream=True,
            )
        else:
            # Providers compatibles OpenAI (DeepSeek, xAI, Moonshot...)
            payload: Dict[str, Any] = {
                "model": model,
                "messages": messages,
                "temperature": temperature,
                max_tokens_key: max_tokens,
                "stream": True,
            }
        async with self._http.stream("POST", url, headers=headers, json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                line = line.strip()
                if not line or not line.startswith("data:"):
                    continue
                data_str = line[5:].strip()
                if data_str == "[DONE]":
                    break
                try:
                    data = json.loads(data_str)
                    delta = data.get("choices", [{}])[0].get("delta", {})
                    content = delta.get("content", "")
                    if content:
                        yield content
                except (json.JSONDecodeError, IndexError):
                    continue

    async def _stream_anthropic(
        self,
        messages: List[Dict[str, str]],
        temperature: float,
        max_tokens: int,
    ) -> AsyncIterator[str]:
        """Stream depuis Anthropic (SSE events)."""
        api_key = get_api_key(ProviderType.ANTHROPIC)
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY non configurée")

        system = ""
        chat_messages = []
        for msg in messages:
            if msg["role"] == "system":
                system = msg["content"]
            else:
                chat_messages.append(msg)

        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": chat_messages,
            "temperature": temperature,
            "stream": True,
        }
        # Opus 4.7+ (adaptive thinking models) refuse le paramètre temperature
        if "opus-4-7" in (self.model or ""):
            payload.pop("temperature", None)
        if system:
            payload["system"] = system

        async with self._http.stream("POST", "https://api.anthropic.com/v1/messages", headers=headers, json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                line = line.strip()
                if not line or not line.startswith("data:"):
                    continue
                data_str = line[5:].strip()
                if not data_str:
                    continue
                try:
                    data = json.loads(data_str)
                    evt_type = data.get("type", "")
                    if evt_type == "content_block_delta":
                        delta = data.get("delta", {})
                        text = delta.get("text", "")
                        if text:
                            yield text
                    elif evt_type == "message_stop":
                        break
                except json.JSONDecodeError:
                    continue  # ligne SSE invalide

    async def _stream_google(
        self,
        messages: List[Dict[str, str]],
        temperature: float,
        max_tokens: int,
    ) -> AsyncIterator[str]:
        """Stream depuis Google Gemini (SSE)."""
        api_key = get_api_key(ProviderType.GOOGLE)
        if not api_key:
            raise ValueError("GOOGLE_API_KEY non configurée")

        system_instruction = ""
        contents = []
        for msg in messages:
            if msg["role"] == "system":
                system_instruction = msg["content"]
            elif msg["role"] == "user":
                contents.append({"role": "user", "parts": [{"text": msg["content"]}]})
            elif msg["role"] == "assistant":
                contents.append({"role": "model", "parts": [{"text": msg["content"]}]})

        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model}:streamGenerateContent?alt=sse&key={api_key}"
        )
        payload = {
            "contents": contents,
            "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens},
        }
        if system_instruction:
            payload["systemInstruction"] = {"parts": [{"text": system_instruction}]}

        async with self._http.stream("POST", url, json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                line = line.strip()
                if not line or not line.startswith("data:"):
                    continue
                data_str = line[5:].strip()
                if not data_str:
                    continue
                try:
                    data = json.loads(data_str)
                    candidates = data.get("candidates", [])
                    if candidates:
                        parts = candidates[0].get("content", {}).get("parts", [])
                        for part in parts:
                            text = part.get("text", "")
                            if text:
                                yield text
                except json.JSONDecodeError:
                    continue  # ligne SSE invalide
    
    async def chat_with_tools(
        self,
        messages: List[Dict[str, str]],
        tool_system: Any = None,
        temperature: float = 0.7,
        max_tokens: int = 16384,  # Augmenté pour fichiers longs
        max_tool_iterations: int = 5
    ) -> str:
        """
        Chat avec support automatique des outils.
        
        Le LLM peut décider d'utiliser des outils et cette méthode
        gère automatiquement la boucle d'exécution.
        
        Args:
            messages: Historique de conversation
            tool_system: Instance de LumenaToolSystem
            temperature: Température de génération
            max_tokens: Tokens max par réponse
            max_tool_iterations: Nombre max d'appels d'outils par tour
            
        Returns:
            Réponse finale du LLM (après exécution des outils si nécessaire)
        """
        requested_provider = self.provider.value
        requested_model = self.model
        self._set_last_response_meta(
            provider_requested=requested_provider,
            provider_used=requested_provider,
            model_requested=requested_model,
            model_used=requested_model,
            fallback_used=False,
            fallback_reason=None,
            continuation_used=False,
            continuation_steps=0,
            finish_reason=None,
            continuation_warning=None,
        )

        if tool_system is None:
            # Pas d'outils, chat normal
            return await self.chat(messages, temperature=temperature, max_tokens=max_tokens)
        
        provider = self.provider
        
        # Pour les providers avec function calling natif
        if provider == ProviderType.GOOGLE:
            return await self._chat_google_with_tools(
                messages, tool_system, temperature, max_tokens, max_tool_iterations
            )
        elif provider == ProviderType.ANTHROPIC:
            return await self._chat_anthropic_with_tools(
                messages, tool_system, temperature, max_tokens, max_tool_iterations
            )
        elif provider == ProviderType.OPENAI:
            return await self._chat_openai_with_tools(
                messages, tool_system, temperature, max_tokens, max_tool_iterations
            )
        elif provider == ProviderType.MOONSHOT:
            # Moonshot (Kimi) utilise le format OpenAI - réutiliser le même handler
            return await self._chat_moonshot_with_tools(
                messages, tool_system, temperature, max_tokens, max_tool_iterations
            )
        else:
            # Pour Ollama, xAI et autres modèles sans function calling natif: parser le texte
            return await self._chat_with_text_tools(
                messages, tool_system, temperature, max_tokens, max_tool_iterations
            )
    
    async def _chat_google_with_tools(
        self,
        messages: List[Dict[str, str]],
        tool_system: Any,
        temperature: float,
        max_tokens: int,
        max_iterations: int
    ) -> str:
        """Chat Gemini avec function calling natif."""
        from .providers import get_api_key, ProviderType
        
        api_key = get_api_key(ProviderType.GOOGLE)
        if not api_key:
            raise ValueError("GOOGLE_API_KEY non configurée")
        
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent?key={api_key}"
        
        # Préparer les outils au format Gemini
        tools_def = tool_system.get_tools_for_provider("google")
        
        # Convertir les messages
        system_instruction = ""
        contents = []
        
        for msg in messages:
            if msg["role"] == "system":
                system_instruction = msg["content"]
            elif msg["role"] == "user":
                contents.append({"role": "user", "parts": [{"text": msg["content"]}]})
            elif msg["role"] == "assistant":
                contents.append({"role": "model", "parts": [{"text": msg["content"]}]})
        
        # Ajouter le guide des outils au system prompt
        if tool_system:
            tools_guide = tool_system.get_tools_prompt_section()
            system_instruction = system_instruction + "\n\n" + tools_guide
        
        iteration = 0
        final_response = ""
        last_finish_reason: Optional[str] = None
        
        while iteration < max_iterations:
            iteration += 1
            
            payload = {
                "contents": contents,
                "generationConfig": {
                    "temperature": temperature,
                    "maxOutputTokens": max_tokens
                }
            }
            
            if system_instruction:
                payload["systemInstruction"] = {"parts": [{"text": system_instruction}]}
            
            # Ajouter les outils
            if tools_def:
                payload["tools"] = [{"functionDeclarations": tools_def}]
            
            try:
                response = await self._http.post(url, json=payload)
                response.raise_for_status()
                data = response.json()

                candidates = data.get("candidates", [])
                if not candidates:
                    if "error" in data:
                        raise ValueError(f"Gemini error: {data['error']}")
                    break
                last_finish_reason = candidates[0].get("finishReason")

                content = candidates[0].get("content", {})
                parts = content.get("parts", [])

                if not parts:
                    finish_reason = candidates[0].get("finishReason", "UNKNOWN")
                    if finish_reason == "SAFETY":
                        self._update_last_response_meta(finish_reason=finish_reason)
                        return "[Réponse bloquée par les filtres de sécurité]"
                    break

                # Vérifier si c'est un function call
                has_function_call = False
                text_response = ""

                for part in parts:
                    if "functionCall" in part:
                        has_function_call = True
                        fc = part["functionCall"]
                        tool_name = fc.get("name", "")
                        tool_args = fc.get("args", {})

                        # ⚠️ GEMINI 3: Capturer le thoughtSignature (obligatoire!)
                        thought_signature = part.get("thoughtSignature")

                        logger.debug(f"🔧 Gemini appelle: {tool_name}({tool_args})")
                        if thought_signature:
                            logger.debug(f"🧠 ThoughtSignature capturé: {str(thought_signature)[:50]}...")

                        # Exécuter l'outil
                        from ..tools.tool_system import ToolCall
                        tool_call = ToolCall(name=tool_name, arguments=tool_args)
                        result = await tool_system.execute_tool(tool_call)

                        # Ajouter le function call du modèle AVEC le thoughtSignature
                        # C'est crucial pour Gemini 3!
                        model_part = {"functionCall": {"name": tool_name, "args": tool_args}}
                        if thought_signature:
                            model_part["thoughtSignature"] = thought_signature

                        contents.append({
                            "role": "model",
                            "parts": [model_part]
                        })

                        # Ajouter la réponse de la fonction
                        result_text = result.output if result.success else (result.output or f"Erreur: {result.error or 'inconnue'}")
                        contents.append({
                            "role": "user",
                            "parts": [{
                                "functionResponse": {
                                    "name": tool_name,
                                    "response": {"result": result_text}
                                }
                            }]
                        })

                        logger.debug(f"✅ Résultat outil ajouté: {result_text[:100] if result_text else 'vide'}...")

                    elif "text" in part:
                        text_response += part.get("text", "")

                if not has_function_call:
                    # Pas d'appel d'outil, c'est la réponse finale
                    final_response = text_response
                    break
            except httpx.HTTPStatusError as e:
                # Logger le contenu de la réponse pour debug
                error_detail = ""
                try:
                    error_detail = e.response.text
                except Exception:
                    pass  # Response body not readable
                logger.error(f"❌ Erreur Gemini HTTP {e.response.status_code}: {error_detail[:500]}")
                # Fallback: chat normal sans outils
                return await self.chat(messages, temperature=temperature, max_tokens=max_tokens)
            except Exception as e:
                logger.error(f"❌ Erreur inattendue: {e}")
                return await self.chat(messages, temperature=temperature, max_tokens=max_tokens)
        if final_response:
            self._update_last_response_meta(finish_reason=last_finish_reason)
            return final_response
        return await self.chat(messages, temperature=temperature, max_tokens=max_tokens)
    
    async def _chat_anthropic_with_tools(
        self,
        messages: List[Dict[str, str]],
        tool_system: Any,
        temperature: float,
        max_tokens: int,
        max_iterations: int
    ) -> str:
        """Chat Claude avec tools natif."""
        from .providers import get_api_key, ProviderType
        
        api_key = get_api_key(ProviderType.ANTHROPIC)
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY non configurée")
        
        url = "https://api.anthropic.com/v1/messages"
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "anthropic-beta": "context-1m-2025-08-07",  # Active le context window 1M tokens
            "Content-Type": "application/json"
        }
        
        # Préparer les outils au format Anthropic
        tools_def = tool_system.get_tools_for_provider("anthropic")
        
        # Séparer system des messages
        system_content = ""
        claude_messages = []
        
        for msg in messages:
            if msg["role"] == "system":
                system_content = msg["content"]
            else:
                claude_messages.append(msg)
        
        # Ajouter le guide des outils
        if tool_system:
            tools_guide = tool_system.get_tools_prompt_section()
            system_content = system_content + "\n\n" + tools_guide
        
        iteration = 0
        last_stop_reason: Optional[str] = None
        
        while iteration < max_iterations:
            iteration += 1
            
            payload = {
                "model": self.model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "messages": claude_messages
            }
            # Opus 4.7+ (adaptive thinking models) refuse le paramètre temperature
            if "opus-4-7" in (self.model or ""):
                payload.pop("temperature", None)
            
            if system_content:
                payload["system"] = system_content
            
            if tools_def:
                payload["tools"] = tools_def
            
            try:
                response = await self._http.post(url, headers=headers, json=payload)
                response.raise_for_status()
                data = response.json()

                stop_reason = data.get("stop_reason", "")
                last_stop_reason = stop_reason
                content_blocks = data.get("content", [])

                text_response = ""
                has_tool_use = False

                for block in content_blocks:
                    if block.get("type") == "text":
                        text_response += block.get("text", "")
                    elif block.get("type") == "tool_use":
                        has_tool_use = True
                        tool_name = block.get("name", "")
                        tool_args = block.get("input", {})
                        tool_id = block.get("id", "")

                        logger.debug(f"🔧 Claude appelle: {tool_name}({tool_args})")

                        # Exécuter l'outil
                        from ..tools.tool_system import ToolCall
                        tool_call = ToolCall(name=tool_name, arguments=tool_args, call_id=tool_id)
                        result = await tool_system.execute_tool(tool_call)

                        # Ajouter le résultat
                        claude_messages.append({
                            "role": "assistant",
                            "content": content_blocks
                        })
                        claude_messages.append({
                            "role": "user",
                            "content": [{
                                "type": "tool_result",
                                "tool_use_id": tool_id,
                                "content": result.output if result.success else (result.output or f"Erreur: {result.error or 'inconnue'}")
                            }]
                        })

                if not has_tool_use:
                    self._update_last_response_meta(finish_reason=last_stop_reason)
                    return text_response
                        
            except Exception as e:
                logger.error(f"❌ Erreur Claude: {e}")
                return await self.chat(messages, temperature=temperature, max_tokens=max_tokens)
        
        return await self.chat(messages, temperature=temperature, max_tokens=max_tokens)
    
    async def _chat_openai_with_tools(
        self,
        messages: List[Dict[str, str]],
        tool_system: Any,
        temperature: float,
        max_tokens: int,
        max_iterations: int
    ) -> str:
        """Chat OpenAI avec function calling."""
        from .providers import get_api_key, ProviderType
        
        api_key = get_api_key(ProviderType.OPENAI)
        if not api_key:
            raise ValueError("OPENAI_API_KEY non configurée")
        
        url = "https://api.openai.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        
        tools_def = tool_system.get_tools_for_provider("openai")
        
        # Ajouter le guide des outils au system
        augmented_messages = messages.copy()
        for i, msg in enumerate(augmented_messages):
            if msg["role"] == "system":
                tools_guide = tool_system.get_tools_prompt_section()
                augmented_messages[i] = {
                    "role": "system",
                    "content": msg["content"] + "\n\n" + tools_guide
                }
                break
        
        # Convertir system → developer pour GPT-5.x
        augmented_messages = self._prepare_openai_messages(augmented_messages, self.model)
        
        iteration = 0
        
        while iteration < max_iterations:
            iteration += 1
            
            payload = self._build_openai_payload(
                self.model, augmented_messages,
                temperature=temperature, max_tokens=max_tokens,
                tools=tools_def if tools_def else None,
            )
            
            try:
                response = await self._http.post(url, headers=headers, json=payload)
                response.raise_for_status()
                data = response.json()

                choice = data["choices"][0]
                message = choice["message"]
                finish_reason = choice.get("finish_reason")

                tool_calls = message.get("tool_calls", [])

                if not tool_calls:
                    self._update_last_response_meta(finish_reason=finish_reason)
                    return message.get("content", "")

                # Traiter les tool calls
                augmented_messages.append(message)

                for tc in tool_calls:
                    func = tc.get("function", {})
                    tool_name = func.get("name", "")
                    tool_args = json.loads(func.get("arguments", "{}"))
                    tool_id = tc.get("id", "")

                    logger.debug(f"🔧 OpenAI appelle: {tool_name}({tool_args})")

                    from ..tools.tool_system import ToolCall
                    tool_call = ToolCall(name=tool_name, arguments=tool_args, call_id=tool_id)
                    result = await tool_system.execute_tool(tool_call)

                    augmented_messages.append({
                        "role": "tool",
                        "tool_call_id": tool_id,
                        "content": result.output if result.success else (result.output or f"Erreur: {result.error or 'inconnue'}")
                    })
                        
            except Exception as e:
                logger.error(f"❌ Erreur OpenAI: {e}")
                return await self.chat(messages, temperature=temperature, max_tokens=max_tokens)
        
        return await self.chat(messages, temperature=temperature, max_tokens=max_tokens)
    
    async def _chat_moonshot_with_tools(
        self,
        messages: List[Dict[str, str]],
        tool_system: Any,
        temperature: float,
        max_tokens: int,
        max_iterations: int
    ) -> str:
        """
        Chat Moonshot (Kimi) avec function calling.
        
        Moonshot utilise un format compatible OpenAI, donc très similaire.
        URL: https://api.moonshot.ai/v1/chat/completions
        """
        from .providers import get_api_key, ProviderType
        
        api_key = get_api_key(ProviderType.MOONSHOT)
        if not api_key:
            raise ValueError("MOONSHOT_API_KEY non configurée")
        
        url = "https://api.moonshot.ai/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        
        # Moonshot utilise le format OpenAI pour les tools
        tools_def = tool_system.get_tools_for_provider("openai")
        
        # Ajouter le guide des outils au system prompt
        augmented_messages = messages.copy()
        tools_guide = tool_system.get_tools_prompt_section()
        for i, msg in enumerate(augmented_messages):
            if msg["role"] == "system":
                augmented_messages[i] = {
                    "role": "system",
                    "content": msg["content"] + "\n\n" + tools_guide
                }
                break
        
        iteration = 0
        
        while iteration < max_iterations:
            iteration += 1
            
            payload = {
                "model": self.model,
                "messages": augmented_messages,
                "max_tokens": max_tokens
            }
            
            # Pour kimi-k2.5, temperature ne peut pas être modifiée
            # On ne l'envoie que pour les anciens modèles moonshot-v1
            if not self.model.startswith("kimi-k2"):
                payload["temperature"] = temperature
            
            # Ajouter les tools si disponibles
            # NOTE: kimi-k2.5 supporte les tools mais avec certaines restrictions
            if tools_def:
                payload["tools"] = tools_def
                payload["tool_choice"] = "auto"
            
            try:
                response = await self._http.post(url, headers=headers, json=payload)
                response.raise_for_status()
                data = response.json()

                choice = data["choices"][0]
                message = choice["message"]
                finish_reason = choice.get("finish_reason")
                tool_calls = message.get("tool_calls", [])

                if not tool_calls:
                    # Pas d'appel d'outil, retourner la réponse
                    self._update_last_response_meta(finish_reason=finish_reason)
                    return message.get("content", "")

                # Exécuter les outils
                augmented_messages.append(message)

                for tc in tool_calls:
                    func = tc.get("function", {})
                    tool_name = func.get("name", "")
                    tool_args_str = func.get("arguments", "{}")
                    tool_id = tc.get("id", "")

                    try:
                        tool_args = json.loads(tool_args_str) if isinstance(tool_args_str, str) else tool_args_str
                    except json.JSONDecodeError:
                        tool_args = {}  # JSON args invalide

                    logger.debug(f"🔧 Moonshot appelle: {tool_name}({tool_args})")

                    from ..tools.tool_system import ToolCall
                    tool_call = ToolCall(name=tool_name, arguments=tool_args, call_id=tool_id)
                    result = await tool_system.execute_tool(tool_call)

                    augmented_messages.append({
                        "role": "tool",
                        "tool_call_id": tool_id,
                        "content": result.output if result.success else (result.output or f"Erreur: {result.error or 'inconnue'}")
                    })
                        
            except httpx.HTTPStatusError as e:
                # Log the actual error response from Moonshot API
                error_detail = ""
                try:
                    error_detail = e.response.text
                except Exception:
                    pass  # Response body not readable
                logger.error(f"❌ Erreur Moonshot HTTP {e.response.status_code}: {error_detail[:500]}")
                # Fallback to chat without tools
                return await self.chat(messages, temperature=temperature, max_tokens=max_tokens)
            except Exception as e:
                logger.error(f"❌ Erreur Moonshot: {e}")
                return await self.chat(messages, temperature=temperature, max_tokens=max_tokens)
        
        return await self.chat(messages, temperature=temperature, max_tokens=max_tokens)
    
    async def _chat_with_text_tools(
        self,
        messages: List[Dict[str, str]],
        tool_system: Any,
        temperature: float,
        max_tokens: int,
        max_iterations: int
    ) -> str:
        """
        Chat avec parsing textuel des tool calls.
        
        Pour les modèles sans function calling natif (Ollama, etc.),
        on ajoute les instructions dans le prompt et on parse la réponse.
        """
        # Augmenter le system prompt avec les instructions d'outils
        tools_guide = tool_system.get_tools_prompt_section()
        tools_guide += """

### Format pour appeler un outil:
Si tu dois utiliser un outil, utilise ce format:
[TOOL:nom_outil] {"argument1": "valeur1", "argument2": "valeur2"}

Exemple:
[TOOL:web_search] {"query": "météo Paris"}

⚠️ IMPORTANT: Après avoir reçu le résultat de l'outil, tu DOIS l'utiliser pour répondre à l'utilisateur.
Ne dis JAMAIS "vous avez ouvert le site" - DONNE les informations du résultat directement !
"""
        
        augmented_messages = messages.copy()
        for i, msg in enumerate(augmented_messages):
            if msg["role"] == "system":
                augmented_messages[i] = {
                    "role": "system",
                    "content": msg["content"] + "\n\n" + tools_guide
                }
                break
        else:
            # Pas de system message, en ajouter un
            augmented_messages.insert(0, {"role": "system", "content": tools_guide})
        
        iteration = 0
        all_tool_results = []  # Collecter tous les résultats d'outils
        
        while iteration < max_iterations:
            iteration += 1
            
            response = await self.chat(augmented_messages, temperature=temperature, max_tokens=max_tokens)
            
            # Parser les tool calls
            tool_calls = tool_system.parse_tool_calls_from_text(response)
            
            if not tool_calls:
                # Pas d'appel d'outil — réponse finale.
                # Les résultats des outils ont déjà été injectés dans la conversation
                # (augmented_messages) et le LLM les a utilisés pour formuler sa réponse.
                return response
            
            # Exécuter les outils et ajouter les résultats
            for tc in tool_calls:
                logger.debug(f"🔧 Parsed tool call: {tc.name}({tc.arguments})")
                result = await tool_system.execute_tool(tc)
                
                result_text = result.output if result.success else (result.output or f"Erreur: {result.error or 'inconnue'}")
                all_tool_results.append(f"**{tc.name}**: {result_text[:500]}")  # Limiter la taille
                
                # Ajouter le résultat à la conversation pour que le LLM puisse l'utiliser
                augmented_messages.append({"role": "assistant", "content": response})
                augmented_messages.append({
                    "role": "user",
                    "content": f"""[Résultat de l'outil {tc.name}]:
{result_text}

Maintenant, utilise ces informations pour répondre à ma question initiale de manière complète et utile."""
                })
        
        # Si on atteint max_iterations, retourner la dernière réponse avec les résultats
        if all_tool_results:
            return f"Voici ce que j'ai trouvé:\n\n" + "\n\n".join(all_tool_results)
        
        return await self.chat(messages, temperature=temperature, max_tokens=max_tokens)
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
