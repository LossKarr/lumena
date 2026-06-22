"""
🌟 LUMENA - Core (Cerveau Principal)

Le cœur de LUMENA : gère le LLM, la mémoire, et les interactions.
Version reconstruite avec toutes les fonctionnalités originales + TTS.
"""

from typing import Dict, Any, List, Optional, Callable
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
import asyncio
import json
import os
import threading
import httpx
from loguru import logger

# Configuration logging persistant
from .utils.paths import LOGS_DIR as _log_dir, DATA_DIR as _default_data_dir
_log_dir.mkdir(parents=True, exist_ok=True)

import sys as _sys
_IN_PYTEST = "pytest" in _sys.modules or "PYTEST_CURRENT_TEST" in __import__("os").environ
if not _IN_PYTEST:
    logger.add(
        str(_log_dir / "lumena.log"),
        rotation="1 day",
        retention="7 days",
        compression="gz",
        enqueue=True,
        encoding="utf-8",
    )

from .personality import LumenaPersonality, Mood, DEFAULT_PERSONALITY
from .structured_state import StructuredState

# Import des modules optionnels
try:
    from .voice import get_tts
    TTS_AVAILABLE = True
except ImportError:
    TTS_AVAILABLE = False

try:
    from .emotion import get_emotion_manager
    EMOTION_AVAILABLE = True
except ImportError:
    EMOTION_AVAILABLE = False

try:
    from .memory import LumenaMemory, CHROMADB_AVAILABLE
    MEMORY_AVAILABLE = CHROMADB_AVAILABLE
except ImportError:
    MEMORY_AVAILABLE = False

try:
    from .memory.migration import migrate_legacy_vector_to_canonical
    MEMORY_MIGRATION_AVAILABLE = True
except ImportError:
    MEMORY_MIGRATION_AVAILABLE = False

try:
    from .runtime.task_orchestrator import TaskOrchestrator
    TASK_ORCHESTRATOR_AVAILABLE = True
except ImportError:
    TASK_ORCHESTRATOR_AVAILABLE = False

# Import du système de tools automatique
try:
    from .tools.tool_system import get_tool_system
    TOOL_SYSTEM_AVAILABLE = True
except ImportError:
    TOOL_SYSTEM_AVAILABLE = False
    logger.warning("⚠️ Système de tools non disponible")

# Import du provider LLM multi-provider
try:
    from .llm.multi_provider import MultiProviderLLM
    MULTI_PROVIDER_AVAILABLE = True
except ImportError:
    MULTI_PROVIDER_AVAILABLE = False

# Import du module Context (conscience projet)
try:
    from .context import RepoMap, CodeIndex
    CONTEXT_AVAILABLE = True
except ImportError:
    CONTEXT_AVAILABLE = False
    logger.debug("Module context non disponible")

# Import du rules_loader
try:
    from .context.rules_loader import RulesLoader
    RULES_AVAILABLE = True
except ImportError:
    RULES_AVAILABLE = False
    logger.debug("Rules loader non disponible")

# Import du système de hooks
try:
    from .hooks import HookSystem, HookEvent, get_hook_system, register_default_hooks
    HOOKS_AVAILABLE = True
except ImportError:
    HOOKS_AVAILABLE = False
    logger.debug("Module hooks non disponible")

# Import du système d'instincts (apprentissage)
try:
    from .learning.instincts import InstinctSystem, get_instinct_system
    INSTINCTS_AVAILABLE = True
except ImportError:
    INSTINCTS_AVAILABLE = False
    logger.debug("Module instincts non disponible")

# Import compaction de contexte
try:
    from .tools.compaction import ContextCompactor
    COMPACTION_AVAILABLE = True
except ImportError:
    COMPACTION_AVAILABLE = False

# ── Core Services (fragmentation Phase 2-5) ──────────────────────────────────
from .core_services.contracts import ServiceContext
from .core_services.workspace_service import WorkspaceService
from .core_services.voice_service import VoiceService
from .core_services.code_service import CodeService
from .core_services.web_service import WebService
from .core_services.memory_service import MemoryService
from .core_services.identity_service import IdentityService
from .core_services.context_service import ContextService
from .core_services.agent_service import AgentService

# Import config validator (Phase 3.2)
try:
    from .utils.config_validator import validate_and_log as validate_config
    CONFIG_VALIDATOR_AVAILABLE = True
except ImportError:
    CONFIG_VALIDATOR_AVAILABLE = False
    validate_config = None

# Import graceful degradation (Phase 6.2)
try:
    from .utils.graceful_degradation import log_startup_dependencies
    GRACEFUL_DEGRADATION_AVAILABLE = True
except ImportError:
    GRACEFUL_DEGRADATION_AVAILABLE = False
    log_startup_dependencies = None


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Message:
    """Représente un message dans la conversation."""
    role: str  # "user", "assistant", "system"
    content: str
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: Dict[str, Any] = field(default_factory=dict)


class ConversationContext:
    """Gère le contexte de conversation."""
    
    def __init__(self, max_messages: int = 20):
        self.messages: List[Message] = []
        self.max_messages = max_messages
        # ── État structuré parallèle (V1) ──
        self.structured_state: StructuredState = StructuredState()
    
    def add_message(self, role: str, content: str, metadata: Dict[str, Any] = None):
        """Ajoute un message au contexte."""
        msg = Message(role=role, content=content, metadata=metadata or {})
        self.messages.append(msg)
        
        # Garder seulement les derniers messages
        if len(self.messages) > self.max_messages:
            self.messages = self.messages[-self.max_messages:]
        
        return msg
    
    def get_history_for_llm(self) -> List[Dict[str, str]]:
        """Retourne l'historique formaté pour le LLM."""
        return [{"role": m.role, "content": m.content} for m in self.messages]
    
    def get_recent(self, n: int = 5) -> List[Dict[str, str]]:
        """Retourne les n derniers messages."""
        return [{"role": m.role, "content": m.content} for m in self.messages[-n:]]
    
    def clear(self):
        """Vide le contexte."""
        self.messages = []
        self.structured_state = StructuredState()


class OllamaClient:
    """Client pour Ollama (fallback local)."""
    
    def __init__(self, base_url: str = "http://localhost:11434", model: str = "qwen3:8b"):
        self.base_url = base_url
        self.model = model
    
    async def is_available(self) -> bool:
        """Vérifie si Ollama est disponible."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self.base_url}/api/tags")
                return response.status_code == 200
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.TimeoutException, OSError) as e:
            logger.debug(f"Ollama non disponible: {e}")
            return False
    
    async def list_models(self) -> List[str]:
        """Liste les modèles disponibles."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self.base_url}/api/tags")
                if response.status_code == 200:
                    data = response.json()
                    return [m["name"] for m in data.get("models", [])]
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.TimeoutException, OSError) as e:
            logger.debug(f"Impossible de lister les modeles Ollama: {e}")
        return []
    
    async def chat(
        self, 
        messages: List[Dict[str, str]], 
        model: Optional[str] = None,
        temperature: float = 0.7,
        stream: bool = False
    ) -> str:
        """Envoie un message au LLM et retourne la réponse."""
        model = model or self.model
        
        payload = {
            "model": model,
            "messages": messages,
            "stream": stream,
            "options": {
                "temperature": temperature,
                "num_predict": 2000
            }
        }
        
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(
                    f"{self.base_url}/api/chat",
                    json=payload
                )
                
                if response.status_code == 200:
                    data = response.json()
                    return data.get("message", {}).get("content", "")
                else:
                    logger.error(f"Ollama error: {response.status_code}")
                    return ""
                    
        except httpx.TimeoutException:
            logger.error("Timeout en attendant Ollama")
            return ""
        except Exception as e:
            logger.error(f"Erreur communication Ollama: {e}")
            return ""
    
    async def chat_stream(
        self, 
        messages: List[Dict[str, str]], 
        model: Optional[str] = None,
        temperature: float = 0.7
    ):
        """Envoie un message et stream la réponse token par token."""
        model = model or self.model
        
        payload = {
            "model": model,
            "messages": messages,
            "stream": True,
            "options": {
                "temperature": temperature,
            }
        }
        
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=10.0)) as client:
                async with client.stream(
                    "POST",
                    f"{self.base_url}/api/chat",
                    json=payload,
                ) as response:
                    async for line in response.aiter_lines():
                        if line:
                            try:
                                data = json.loads(line)
                                content = data.get("message", {}).get("content", "")
                                if content:
                                    yield content
                                if data.get("done", False):
                                    break
                            except json.JSONDecodeError:
                                continue
                                
        except Exception as e:
            logger.error(f"Erreur streaming: {e}")


class LumenaCore:
    """
    🌟 Le Cerveau de LUMENA
    
    Gère toutes les interactions, la mémoire, et le raisonnement.
    C'est le point central qui connecte tous les modules.
    """
    
    def __init__(
        self, 
        personality: Optional[LumenaPersonality] = None,
        data_dir: Optional[Path] = None
    ):
        # Personnalité
        self.personality = personality or DEFAULT_PERSONALITY
        
        # Client LLM - utiliser MultiProviderLLM si disponible pour les tools
        if MULTI_PROVIDER_AVAILABLE:
            self.llm = MultiProviderLLM()
            logger.info("🧠 Client MultiProviderLLM initialisé")
        else:
            self.llm = OllamaClient()
            logger.info("🧠 Client Ollama initialisé (fallback)")
        
        # Contexte de conversation (CLI/web) + contextes Telegram par ami (tg_id → context)
        self.context = ConversationContext()
        _max_ctx = int(os.getenv("LUMENA_MAX_CONTEXTS_PER_PLATFORM", "500"))
        self._max_contexts = _max_ctx
        self._tg_contexts: OrderedDict[str, ConversationContext] = OrderedDict()
        # Contextes Discord par utilisateur (user_id → context) + profils utilisateurs
        self._discord_contexts: OrderedDict[str, ConversationContext] = OrderedDict()
        self._discord_users: Dict[str, dict] = {}  # user_id → profil (username, first_seen, message_count, ...)
        self._discord_global_channel_id: Optional[str] = os.getenv("DISCORD_GLOBAL_CHANNEL_ID")
        
        # Répertoire des données
        self.data_dir = data_dir or _default_data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        
        # État
        self.is_initialized = False
        self.is_listening = False
        self.agent_final_repair_enabled = _env_flag("LUMENA_AGENT_FINAL_REPAIR", True)
        self.memory_auto_migrate = _env_flag("LUMENA_MEMORY_AUTO_MIGRATE", True)
        self.skills_auto_activation = _env_flag("LUMENA_SKILLS_AUTO_ACTIVATION", True)
        
        # Système d'émotions autonomes
        _emotion_enabled = _env_flag("LUMENA_EMOTION_ENABLED", True)
        self.emotion_manager = get_emotion_manager() if (EMOTION_AVAILABLE and _emotion_enabled) else None
        # Sync initiale personality → emotion_manager
        if self.emotion_manager and self.personality:
            self.emotion_manager._personality_ref = self.personality
            self.emotion_manager.force_mood(self.personality.current_mood)
        
        # Système de mémoire persistante (owner local, singleton legacy)
        self.memory = LumenaMemory(self.data_dir / "memory") if MEMORY_AVAILABLE else None
        # Cache mémoire par user_id (remplit au fil des requêtes)
        self._user_memory_cache: dict = {}
        
        # 🛠️ Système de tools automatique (toujours actif)
        self.tool_system = get_tool_system() if TOOL_SYSTEM_AVAILABLE else None
        if self.tool_system:
            if self.memory and hasattr(self.tool_system, "bind_memory"):
                self.tool_system.bind_memory(self.memory)
                logger.info("🧠 Mémoire canonique liée au ToolSystem")
            logger.info("🛠️ Système de tools automatique activé")
        
        # Callbacks
        self._on_response_callbacks: List[Callable[[str], None]] = []
        self._on_thinking_callbacks: List[Callable[[], None]] = []
        self._on_mood_change_callbacks: List[Callable[[str], None]] = []
        
        # Dernier lien/page mentionné (pour pouvoir l'ouvrir sur demande)
        self._last_mentioned_url: Optional[str] = None
        self._last_search_query: Optional[str] = None
        self._last_fetched_content: Optional[str] = None
        self._last_page_title: Optional[str] = None
        
        # 🗺️ RepoMap - Conscience projet
        self.repo_map: Optional[RepoMap] = None
        self.code_index: Optional[CodeIndex] = None
        if CONTEXT_AVAILABLE:
            _lumena_root = Path(__file__).parent.parent
            _ext_ws_raw = os.getenv("LUMENA_DEFAULT_WORKSPACE", "").strip()
            _ext_ws = Path(_ext_ws_raw).expanduser().resolve() if _ext_ws_raw else None
            if _ext_ws and _ext_ws.exists() and _ext_ws.is_dir() and _ext_ws != _lumena_root.resolve():
                project_root = _ext_ws
                logger.info(f"🗺️ RepoMap + CodeIndex sur workspace externe : {project_root}")
            else:
                project_root = _lumena_root
            self.repo_map = RepoMap(project_root, max_files=25, max_tokens=1200)
            self.code_index = CodeIndex(project_root)
            logger.info("🗺️ RepoMap + CodeIndex initialisés pour conscience projet")
        
        # 📜 Rules - Règles projet
        self.rules_loader: Optional[RulesLoader] = None
        if RULES_AVAILABLE:
            project_root = Path(__file__).parent.parent
            self.rules_loader = RulesLoader(project_root)
            logger.debug("📜 Rules loader initialisé")
        
        # 🪝 Hooks - Système event-driven
        self.hook_system: Optional[HookSystem] = None
        if HOOKS_AVAILABLE:
            self.hook_system = get_hook_system()
            register_default_hooks(self.hook_system)
            logger.info("🪝 Système de hooks initialisé")
        
        # 🧠 Instincts - Apprentissage continu
        self.instinct_system: Optional[InstinctSystem] = None
        if INSTINCTS_AVAILABLE:
            self.instinct_system = get_instinct_system()
            logger.info(f"🧠 Système d'instincts initialisé ({self.instinct_system.get_stats()['total_instincts']} instincts)")
        
        # 🔊 TTS - Synthèse vocale
        self.tts = None
        self.auto_speak = _env_flag("LUMENA_TTS_AUTO", False)
        if TTS_AVAILABLE:
            try:
                self.tts = get_tts()
                logger.info(f"🔊 Système TTS disponible (auto_speak: {self.auto_speak})")
            except Exception as e:
                logger.warning(f"⚠️ Erreur init TTS: {e}")
        
        # Mémoire permanente
        self._permanent_memory = ""
        self._skills = {}
        self._last_active_skills: List[str] = []
        self._last_agent_meta = {
            "agent_output_incomplete": False,
            "agent_output_warning": None,
            "agent_repair_attempts": 0,
            "agent_final_finish_reason": None,
        }

        # 🔧 ToolRegistry singleton (agent ReAct) — créé 1× au boot, réutilisé
        self._tool_registry = None

        # 🎯 TaskOrchestrator — orchestration longue durée (P3.1)
        self.task_orchestrator = None
        if TASK_ORCHESTRATOR_AVAILABLE:
            self.task_orchestrator = TaskOrchestrator(
                persistence_path=self.data_dir / "task_orchestrator_state.json",
            )
            logger.info("🎯 TaskOrchestrator initialisé")

        # 🔄 Compaction de contexte (airbag automatique pour conversations longues)
        self._compactor: Optional["ContextCompactor"] = (
            ContextCompactor(
                max_context_tokens=128_000,
                compaction_threshold=0.75,
                keep_recent_turns=8,
                llm_summarizer=self._llm_summarize,
            )
            if COMPACTION_AVAILABLE else None
        )

        # ── Core Services (Phase 2-5 fragmentation) ─────────────────────────
        self._svc_ctx = ServiceContext(
            data_dir=self.data_dir,
            llm=self.llm,
            memory=self.memory,
            tts=self.tts,
            emotion_manager=self.emotion_manager,
            tool_system=self.tool_system,
            repo_map=self.repo_map,
            code_index=self.code_index,
            rules_loader=self.rules_loader,
            hook_system=self.hook_system,
            instinct_system=self.instinct_system,
            auto_speak=self.auto_speak,
        )
        # Service instances
        self._ws_svc = WorkspaceService(self._svc_ctx)
        self._voice_svc = VoiceService(self._svc_ctx)
        self._code_svc = CodeService(self._svc_ctx)
        self._web_svc = WebService(self._svc_ctx)
        self._memory_svc = MemoryService(self._svc_ctx)
        self._identity_svc = IdentityService(
            self._svc_ctx,
            tg_contexts=self._tg_contexts,
            discord_contexts=self._discord_contexts,
            discord_users=self._discord_users,
            max_contexts=self._max_contexts,
        )
        self._context_svc = ContextService(self._svc_ctx)
        self._agent_svc = AgentService(self)

        logger.info(f"🌟 {self.personality.name} Core initialisé")
        if EMOTION_AVAILABLE:
            logger.info("🎭 Système d'émotions autonomes activé")
        if MEMORY_AVAILABLE:
            logger.info("💾 Système de mémoire ChromaDB activé")
    
    async def initialize(self) -> bool:
        """Initialise LUMENA - vérifie les dépendances et charge la mémoire."""
        logger.info("Initialisation de LUMENA...")
        
        # Phase 3.2: Validation de la configuration
        if CONFIG_VALIDATOR_AVAILABLE and validate_config:
            if not validate_config(self.data_dir):
                logger.warning("⚠️ Configuration avec des problèmes détectés")
        
        # Phase 6.2: Log des dépendances optionnelles
        if GRACEFUL_DEGRADATION_AVAILABLE and log_startup_dependencies:
            log_startup_dependencies()
        
        logger.info(
            "Flags: LUMENA_AGENT_FINAL_REPAIR={}, LUMENA_MEMORY_AUTO_MIGRATE={}",
            int(self.agent_final_repair_enabled),
            int(self.memory_auto_migrate),
        )
        logger.info(f"Chemin mémoire canonique: {self.data_dir / 'memory' / 'vector'}")
        
        # Vérifier la disponibilité du LLM
        if not await self.llm.is_available():
            if hasattr(self.llm, 'provider'):
                from .llm.providers import ProviderType
                if self.llm.provider == ProviderType.OLLAMA:
                    logger.error("❌ Ollama n'est pas disponible ! Lance 'ollama serve'")
                else:
                    logger.error(f"❌ Provider {self.llm.provider.value} non disponible")
            else:
                logger.error("❌ LLM non disponible !")
            return False
        
        # Vérifier le modèle (seulement pour Ollama)
        if hasattr(self.llm, 'provider'):
            from .llm.providers import ProviderType
            if self.llm.provider == ProviderType.OLLAMA:
                models = await self.llm.list_models()
                if not any("qwen3" in m.lower() or "qwen2.5" in m.lower() for m in models):
                    logger.warning(f"⚠️ Modèle Qwen pas trouvé. Modèles disponibles: {models}")
                    if models:
                        self.llm.model = models[0]
                        logger.info(f"📌 Utilisation de {self.llm.model} comme fallback")
            else:
                logger.info(f"☁️ Provider cloud: {self.llm.provider.value}, modèle: {self.llm.model}")

        if self.memory and self.memory_auto_migrate and MEMORY_MIGRATION_AVAILABLE:
            try:
                migration_result = migrate_legacy_vector_to_canonical(self.data_dir)
                if migration_result.get("status") == "success":
                    logger.success(
                        "Migration mémoire legacy terminée: inserted={}, skipped={}",
                        migration_result.get("inserted", 0),
                        migration_result.get("skipped", 0),
                    )
                elif migration_result.get("status") == "skipped":
                    logger.info(
                        "Migration mémoire legacy skip: {}",
                        migration_result.get("reason", "no_reason"),
                    )
                else:
                    logger.warning(
                        "Migration mémoire legacy non aboutie: {}",
                        migration_result.get("reason", "unknown"),
                    )
            except Exception as e:
                logger.warning(f"Migration mémoire legacy échouée: {e}")
        
        # 📖 Charger MEMORY.md
        self._load_memory_file()
        
        # 🎯 Charger les skills
        self._load_skills()
        
        self.is_initialized = True
        logger.info(f"✅ {self.personality.name} est prête !")
        
        return True
    
    async def _llm_summarize(self, messages: List[Dict[str, Any]]) -> str:
        return await self._memory_svc._llm_summarize(messages)

    def _load_memory_file(self):
        self._memory_svc._load_memory_file()
        self._permanent_memory = self._memory_svc._permanent_memory
    
    def _load_skills(self):
        self._context_svc._load_skills()
        self._skills = self._context_svc._skills

    def _resolve_sender_identity(
        self,
        sender: Optional[Dict[str, Any]],
        source_channel: str,
    ) -> Optional[Dict[str, Any]]:
        return self._identity_svc._resolve_sender_identity(sender, source_channel)

    def _resolve_channel_and_ide_context(
        self,
        source_channel: str,
        ide_context: Optional[Dict[str, Any]],
    ):
        return self._identity_svc._resolve_channel_and_ide_context(source_channel, ide_context)

    def _detect_friend_rename(
        self,
        user_message: str,
        sender_info: Optional[Dict[str, Any]],
    ) -> Optional[str]:
        return self._identity_svc._detect_friend_rename(user_message, sender_info)

    def _detect_self_introduction(
        self,
        user_message: str,
        sender_info: Optional[Dict[str, Any]],
    ) -> Optional[str]:
        return self._identity_svc._detect_self_introduction(user_message, sender_info)

    def _apply_friend_rename(self, old_name_query: str, new_name: str) -> Optional[Dict[str, str]]:
        return self._identity_svc._apply_friend_rename(old_name_query, new_name)

    # ─── Persistance des contextes Telegram / Discord  (→ IdentityService) ───

    def _load_discord_user_context(self, user_id: str, channel_id: str, username: str = "") -> ConversationContext:
        return self._identity_svc._load_discord_user_context(user_id, channel_id, username)

    def _save_discord_user_context(self, user_id: str, channel_id: str):
        return self._identity_svc._save_discord_user_context(user_id, channel_id)

    def _get_discord_user_context_block(self, user_id: str) -> str:
        return self._identity_svc._get_discord_user_context_block(user_id)

    def _load_tg_context(self, tg_id: str) -> ConversationContext:
        return self._identity_svc._load_tg_context(tg_id)

    def _save_tg_context(self, tg_id: str, context: ConversationContext):
        return self._identity_svc._save_tg_context(tg_id, context)

    def clear_tg_context(self, tg_id: str):
        return self._identity_svc.clear_tg_context(tg_id)

    def _load_wa_context(self, phone: str):
        return self._identity_svc._load_wa_context(phone)

    def _save_wa_context(self, phone: str, context):
        return self._identity_svc._save_wa_context(phone, context)

    def clear_wa_context(self, phone: str):
        return self._identity_svc.clear_wa_context(phone)

    def _load_web_context(self) -> ConversationContext:
        return self._identity_svc._load_web_context()

    def _save_web_context(self, context: ConversationContext):
        return self._identity_svc._save_web_context(context)

    def clear_web_context(self):
        return self._identity_svc.clear_web_context()

    # ──────────────────────────────────────────────────────────────────────────

    def _build_active_skills_context_for_query(
        self,
        query: str,
        max_results: int = 3,
        max_chars: int = 12000,
    ) -> str:
        self._context_svc.skills_auto_activation = self.skills_auto_activation
        result = self._context_svc._build_active_skills_context_for_query(query, max_results, max_chars)
        self._last_active_skills = self._context_svc._last_active_skills
        return result

    def get_last_active_skills(self) -> List[str]:
        return self._context_svc.get_last_active_skills()
    
    def get_permanent_memory_context(self) -> str:
        return self._memory_svc.get_permanent_memory_context()

    def get_project_context(self) -> str:
        return self._context_svc.get_project_context()
    
    def search_code(self, query: str, n_results: int = 5) -> str:
        return self._code_svc.search_code(query, n_results)
    
    def get_rules_context(self) -> str:
        return self._context_svc.get_rules_context()
    
    async def trigger_hook(self, event: str, data: dict = None):
        """Déclenche un hook événement."""
        if not HOOKS_AVAILABLE or self.hook_system is None:
            return
        try:
            event_enum = getattr(HookEvent, event.upper(), HookEvent.CUSTOM)
            await self.hook_system.trigger(event_enum, data or {})
        except Exception as e:
            logger.debug(f"Erreur hook {event}: {e}")
    
    def learn_from_interaction(self, pattern: str, response: str, success: bool):
        return self._memory_svc.learn_from_interaction(pattern, response, success)
    
    def get_full_context(self) -> str:
        return self._context_svc.get_full_context(
            get_permanent_memory_context_fn=self.get_permanent_memory_context
        )
    
    # =====================
    # Agent / Chat / ReAct  (-> AgentService)
    # =====================

    async def chat(
        self,
        user_message: str,
        source_channel: str = "web",
        sender: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Point d'entree principal pour parler avec LUMENA."""
        return await self._agent_svc.chat(user_message, source_channel, sender)

    async def _set_web_only_mode(self, enabled: bool) -> None:
        return await self._agent_svc._set_web_only_mode(enabled)

    def _match_model_alias(self, message: str) -> Optional[str]:
        return self._agent_svc._match_model_alias(message)

    async def _switch_brain_model(self, model_name: str) -> str:
        return await self._agent_svc._switch_brain_model(model_name)

    def _resolve_agent_type_from_label(self, raw_label: str) -> str:
        return self._agent_svc._resolve_agent_type_from_label(raw_label)

    async def _try_natural_delegation(self, message: str) -> Optional[str]:
        return await self._agent_svc._try_natural_delegation(message)

    async def _handle_runtime_controls(self, user_message: str, source_channel: str = "web") -> Optional[str]:
        return await self._agent_svc._handle_runtime_controls(user_message, source_channel)

    def _detect_and_save_preferences(self, message: str):
        return self._agent_svc._detect_and_save_preferences(message)

    def _save_conversation_to_memory(self, user_message: str, response: str):
        return self._agent_svc._save_conversation_to_memory(user_message, response)

    def _save_to_journal_file(self, user_message: str, response: str, importance: float = 0.5):
        return self._agent_svc._save_to_journal_file(user_message, response, importance)

    def _convert_tu_to_vous(self, text: str) -> str:
        return self._agent_svc._convert_tu_to_vous(text)

    async def chat_stream(
        self, user_message: str, source_channel: str = "web",
        channel_id: Optional[str] = None, user_id: Optional[str] = None,
        username: Optional[str] = None, active_users: Optional[list] = None,
        image_paths: Optional[list] = None, is_admin: bool = False,
        channel_name: Optional[str] = None, channel_topic: Optional[str] = None,
        available_channels: Optional[list] = None,
    ):
        """Pipeline Discord complet avec streaming token par token."""
        async for chunk in self._agent_svc.chat_stream(
            user_message, source_channel, channel_id, user_id, username,
            active_users, image_paths, is_admin, channel_name, channel_topic,
            available_channels,
        ):
            yield chunk

    def _default_agent_meta(self) -> Dict[str, Any]:
        return self._agent_svc._default_agent_meta()

    def get_last_agent_meta(self) -> Dict[str, Any]:
        return self._agent_svc.get_last_agent_meta()

    async def think_and_act(
        self,
        query: str,
        source_channel: str = "web",
        sender: Optional[Dict[str, Any]] = None,
        step_callback=None,
        max_iterations: Optional[int] = None,
    ) -> str:
        """Utilise la boucle ReAct pour reflechir et agir."""
        return await self._agent_svc.think_and_act(query, source_channel, sender, step_callback=step_callback, max_iterations=max_iterations)

    async def think_and_act_silent(
        self,
        task: str,
        timeout: float = 120.0,
        allowed_tools: Optional[list] = None,
        allow_when_busy: bool = False,
        artifacts_out: Optional[list] = None,
        allowed_tools_hard: bool = False,
        refusals_out: Optional[list] = None,
    ) -> str:
        """Boucle ReAct silencieuse pour les taches autonomes internes."""
        return await self._agent_svc.think_and_act_silent(
            task, timeout, allowed_tools, allow_when_busy=allow_when_busy,
            artifacts_out=artifacts_out, allowed_tools_hard=allowed_tools_hard,
            refusals_out=refusals_out,
        )

    # =====================
    # Méthodes utilitaires
    # =====================
    
    def on_response(self, callback: Callable[[str], None]) -> None:
        """Enregistre un callback appelé quand LUMENA répond."""
        self._on_response_callbacks.append(callback)
    
    def on_thinking(self, callback: Callable[[], None]) -> None:
        """Enregistre un callback appelé quand LUMENA réfléchit."""
        self._on_thinking_callbacks.append(callback)
    
    def on_mood_change(self, callback: Callable[[str], None]) -> None:
        """Enregistre un callback appelé quand l'humeur change."""
        self._on_mood_change_callbacks.append(callback)

    def greet(self) -> str:
        """Retourne une salutation courte selon l'humeur actuelle."""
        try:
            return self.personality.get_greeting()
        except Exception as e:
            logger.debug(f"Impossible de générer la salutation: {e}")
            return "Salut !"

    def set_mood(self, new_mood: Mood) -> str:
        """
        Met à jour l'humeur active de Lumena.

        Retourne un court commentaire (utilisé par le CLI) et notifie les callbacks.
        """
        if not isinstance(new_mood, Mood):
            try:
                if isinstance(new_mood, str):
                    normalized = new_mood.strip()
                    if normalized.lower() in {m.value for m in Mood}:
                        new_mood = next(m for m in Mood if m.value == normalized.lower())
                    else:
                        new_mood = Mood[normalized.upper()]
                else:
                    raise ValueError("type d'humeur invalide")
            except Exception:
                return "Humeur invalide."

        comment = self.personality.update_mood(new_mood)

        # Sync best-effort avec le gestionnaire émotionnel autonome si présent.
        if self.emotion_manager is not None:
            try:
                state = getattr(self.emotion_manager, "state", None)
                if state is not None and hasattr(state, "mood"):
                    emotion_mood_enum = type(state.mood)
                    if hasattr(emotion_mood_enum, "__members__") and new_mood.name in emotion_mood_enum.__members__:
                        state.mood = emotion_mood_enum[new_mood.name]
                        if hasattr(state, "last_mood_change"):
                            state.last_mood_change = datetime.now()
            except Exception as e:
                logger.debug(f"Sync humeur emotion_manager ignorée: {e}")

        for callback in self._on_mood_change_callbacks:
            try:
                callback(new_mood.value)
            except Exception as e:
                logger.warning(f"Mood change callback error: {e}")

        return comment
    
    def get_emotion_stats(self) -> dict:
        """Retourne les statistiques émotionnelles."""
        if self.emotion_manager:
            return self.emotion_manager.get_stats()
        return {
            "mood": self.personality.current_mood.value,
            "energy": self.personality.energy_level.value,
        }
    
    def clear_context(self) -> None:
        """Efface le contexte de conversation."""
        self.context.clear()
        logger.info("Contexte de conversation effacé")
    
    async def shutdown(self) -> None:
        """Arrête LUMENA proprement."""
        logger.info(f"Arrêt de {self.personality.name}...")
        
        self.is_initialized = False
        # Fermer le client HTTP persistant du LLM
        _llm = getattr(self, "llm", None)
        if _llm and hasattr(_llm, "close"):
            try:
                await _llm.close()
            except Exception as e:
                logger.warning(f"LLM close error: {e}")
        # Libérer les contextes en mémoire
        self._tg_contexts.clear()
        self._discord_contexts.clear()
        self._discord_users.clear()
        logger.info("👋 À bientôt !")
    
    # =====================
    # Web Search & URL Fetching  (→ WebService)
    # =====================

    def _sync_web_state(self):
        """Synchronise le state du WebService vers core (backward compat)."""
        self._last_mentioned_url = self._web_svc._last_mentioned_url
        self._last_search_query = self._web_svc._last_search_query
        self._last_fetched_content = self._web_svc._last_fetched_content
        self._last_page_title = self._web_svc._last_page_title

    async def search_web(self, query: str, num_results: int = 5) -> List[Dict[str, str]]:
        result = await self._web_svc.search_web(query, num_results)
        self._sync_web_state()
        return result

    async def fetch_url(self, url: str, max_content: int = 5000) -> Dict[str, Any]:
        result = await self._web_svc.fetch_url(url, max_content)
        self._sync_web_state()
        return result

    def open_google_search(self, query: str) -> str:
        result = self._web_svc.open_google_search(query)
        self._sync_web_state()
        return result

    async def summarize_url(self, url: str) -> str:
        result = await self._web_svc.summarize_url(url)
        self._sync_web_state()
        return result
    
    # =====================
    # Auto-Tools Detection  (→ AgentService)
    # =====================

    async def _auto_use_tools(self, user_message: str) -> Optional[str]:
        return await self._agent_svc._auto_use_tools(user_message)
    
    # =====================
    # Voice / TTS  (→ VoiceService)
    # =====================

    async def _speak_response(self, response_text: str) -> None:
        await self._voice_svc._speak_response(response_text)

    async def speak(self, text: str, wait: bool = True) -> None:
        await self._voice_svc.speak(text, wait)

    async def chat_and_speak(self, user_message: str) -> str:
        return await self._voice_svc.chat_and_speak(user_message, chat_fn=self.chat)

    def set_auto_speak(self, enabled: bool):
        self._voice_svc.set_auto_speak(enabled)
        self.auto_speak = self._svc_ctx.auto_speak  # sync réel (peut être bloqué par global_mute)

    def set_global_mute(self, enabled: bool):
        """Mute/unmute global persistant — bloque set_auto_speak(True) jusqu'à unmute explicite."""
        self._voice_svc.set_global_mute(enabled)
        self.auto_speak = self._svc_ctx.auto_speak

    # =====================
    # Workspace Management  (→ WorkspaceService)
    # =====================

    def get_workspace_path(self) -> Path:
        return self._ws_svc.get_workspace_path()

    def create_project_folder(self, project_name: str) -> Path:
        return self._ws_svc.create_project_folder(project_name)

    async def create_file(self, filename: str, content: str, project_name: Optional[str] = None) -> Path:
        return await self._ws_svc.create_file(filename, content, project_name)

    async def read_file(self, filepath: str) -> str:
        return await self._ws_svc.read_file(filepath)

    def list_workspace_files(self, pattern: str = "*") -> List[Dict[str, Any]]:
        return self._ws_svc.list_workspace_files(pattern)
    
    # =====================
    # Advanced Memory Management  (→ MemoryService)
    # =====================

    async def remember(self, content: str, memory_type: str = "episodic", importance: float = 0.5) -> bool:
        return await self._memory_svc.remember(content, memory_type, importance)

    async def recall(self, query: str, limit: int = 5) -> List[Any]:
        return await self._memory_svc.recall(query, limit)

    def get_memory_stats(self) -> Dict[str, Any]:
        return self._memory_svc.get_memory_stats()

    def learn_fact(self, key: str, value: Any) -> bool:
        return self._memory_svc.learn_fact(key, value)

    def get_user_memory(self, user_id: str = "local:owner"):
        """Retourne une instance LumenaMemory isolée par user_id (cache interne).

        En mode LUMENA_MULTI_USER=1, le répertoire mémoire est data/users/<safe_id>/memory/.
        En mode single-user, retourne self.memory (local:owner partagé, comportement legacy).
        """
        if not MEMORY_AVAILABLE:
            return None
        uid = (user_id or "local:owner").strip() or "local:owner"
        if not hasattr(self, "_user_memory_cache"):
            self._user_memory_cache = {}
        if uid not in self._user_memory_cache:
            try:
                from src.runtime.user_profile import MULTI_USER_ENABLED, get_user_memory_dir
                if MULTI_USER_ENABLED:
                    mem_dir = get_user_memory_dir(uid, create=True)
                    self._user_memory_cache[uid] = LumenaMemory(mem_dir, user_id=uid)
                else:
                    self._user_memory_cache[uid] = self.memory
            except Exception:
                self._user_memory_cache[uid] = self.memory
        return self._user_memory_cache[uid]

    def get_fact(self, key: str) -> Optional[Any]:
        return self._memory_svc.get_fact(key)
    
    # =====================
    # Code Analysis  (→ CodeService)
    # =====================

    def analyze_code(self, code: str, language: str = "python") -> Dict[str, Any]:
        return self._code_svc.analyze_code(code, language)

    async def explain_code(self, code: str, language: str = "python") -> str:
        return await self._code_svc.explain_code(code, language)

    async def debug_code(self, code: str, error: str = "", language: str = "python") -> str:
        return await self._code_svc.debug_code(code, error, language)
    
    # =====================
    # Status & Diagnostics  (→ ContextService)
    # =====================

    def _build_channel_expectations(self) -> Dict[str, Dict[str, Any]]:
        return self._context_svc._build_channel_expectations()

    def _get_channels_status(self) -> Dict[str, Any]:
        return self._context_svc._get_channels_status()

    def get_status(self) -> Dict[str, Any]:
        return self._context_svc.get_status(self)

    def get_capabilities(self) -> List[str]:
        return self._context_svc.get_capabilities()

    def print_status(self) -> str:
        return self._context_svc.print_status(self)


# Instance singleton avec lock thread-safe (Phase 2.1)
_lumena_instance: Optional[LumenaCore] = None
_lumena_lock = threading.Lock()


def get_lumena() -> LumenaCore:
    """Obtient l'instance singleton de LUMENA (thread-safe)."""
    global _lumena_instance
    
    # Double-check locking pattern
    if _lumena_instance is None:
        with _lumena_lock:
            if _lumena_instance is None:
                _lumena_instance = LumenaCore()
    return _lumena_instance


async def initialize_lumena(
    personality: Optional[LumenaPersonality] = None,
    data_dir: Optional[Path] = None
) -> LumenaCore:
    """
    Initialise et retourne l'instance LUMENA (thread-safe).
    
    Args:
        personality: Personnalité personnalisée (optionnel)
        data_dir: Répertoire des données (optionnel)
        
    Returns:
        L'instance LumenaCore initialisée
    """
    global _lumena_instance
    
    with _lumena_lock:
        if _lumena_instance is not None:
            try:
                await _lumena_instance.shutdown()
            except Exception:
                logger.warning("shutdown ancien instance échoué", exc_info=True)
        _lumena_instance = LumenaCore(personality=personality, data_dir=data_dir)
        await _lumena_instance.initialize()
    
    return _lumena_instance


def reset_lumena() -> None:
    """Remet à zéro l'instance singleton (thread-safe)."""
    global _lumena_instance
    with _lumena_lock:
        _lumena_instance = None
    logger.info("🔄 Instance LUMENA réinitialisée")


# =====================
# Tests
# =====================

if __name__ == "__main__":
    async def test():
        print("🧪 Test de LumenaCore...")
        
        lumena = LumenaCore()
        await lumena.initialize()
        
        print("\n📊 Status:")
        print(lumena.print_status())
        
        print("\n💬 Test chat...")
        response = await lumena.chat("Salut LUMENA ! Comment ça va ?")
        print(f"LUMENA: {response}")
        
        print("\n✅ Tests terminés !")
    
    asyncio.run(test())
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
