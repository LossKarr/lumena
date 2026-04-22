"""
ContextService — Construction du contexte pour le LLM.

Migré depuis LumenaCore (11 méthodes, dépendances skills, memory, mood, repo_map, rules).
"""

import os
from typing import Any, Dict, List, Optional

from loguru import logger

from .base_service import BaseService


def _env_flag(name: str, default: bool = False) -> bool:
    val = os.environ.get(name, "")
    if not val:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


class ContextService(BaseService):
    """Construction de contexte (skills, rules, project, status, capabilities)."""

    def __init__(self, ctx):
        super().__init__(ctx)
        self._skills: dict = {}
        self._last_active_skills: List[str] = []
        self.skills_auto_activation: bool = _env_flag("LUMENA_SKILLS_AUTO_ACTIVATION", True)

    def _load_skills(self):
        """Load runtime skills from src.skills."""
        self._skills = {}
        try:
            from src.skills import get_skill_loader as get_runtime_skill_loader
            SKILLS_RUNTIME_AVAILABLE = True
        except ImportError:
            SKILLS_RUNTIME_AVAILABLE = False

        if not SKILLS_RUNTIME_AVAILABLE:
            logger.debug("Skills runtime non disponible")
            return

        try:
            loader = get_runtime_skill_loader()
            loaded = dict(getattr(loader, "skills", {}) or {})
            self._skills = {name: skill.instructions for name, skill in loaded.items()}
            logger.info(f"{len(self._skills)} skills charges runtime: {list(self._skills.keys())}")
        except Exception as e:
            logger.warning(f"Erreur chargement skills runtime: {e}")
            self._skills = {}

    def _build_active_skills_context_for_query(
        self,
        query: str,
        max_results: int = 3,
        max_chars: int = 12000,
    ) -> str:
        """Build context for auto-activated skills and store runtime list."""
        self._last_active_skills = []
        if not self.skills_auto_activation:
            return ""
        try:
            from src.skills import (
                match_skills as match_runtime_skills,
                build_active_skills_context as build_runtime_skills_context,
            )
        except ImportError:
            return ""

        try:
            matches = match_runtime_skills(query=query, max_results=max_results)
            self._last_active_skills = [match.name for match in matches]
            if not matches:
                return ""
            return build_runtime_skills_context(
                query=query,
                max_results=max_results,
                max_chars=max_chars,
            )
        except Exception as e:
            logger.debug(f"Skills activation error: {e}")
            self._last_active_skills = []
            return ""

    def get_last_active_skills(self) -> List[str]:
        return list(self._last_active_skills)

    def get_project_context(self) -> str:
        """Retourne le contexte du projet pour le prompt."""
        repo_map = self.ctx.repo_map
        if repo_map is None:
            return ""
        try:
            if not repo_map._file_signatures:
                repo_map.build()
            project_map = repo_map.get_compact_map()
            return f"\n\n{project_map}"
        except Exception as e:
            logger.warning(f"Erreur génération contexte projet: {e}")
            return ""

    def get_rules_context(self) -> str:
        """Retourne les règles projet formatées pour le prompt."""
        rules_loader = self.ctx.rules_loader
        if rules_loader is None:
            return ""
        try:
            return rules_loader.get_rules_for_prompt()
        except Exception as e:
            logger.warning(f"Erreur règles: {e}")
            return ""

    def get_full_context(self, get_permanent_memory_context_fn=None) -> str:
        """Retourne le contexte complet pour le LLM."""
        parts = []
        rules = self.get_rules_context()
        if rules:
            parts.append(rules)
        project = self.get_project_context()
        if project:
            parts.append(project)
        if get_permanent_memory_context_fn:
            memory = get_permanent_memory_context_fn()
            if memory:
                parts.append(memory)
        return "\n".join(parts)

    def _build_channel_expectations(self) -> Dict[str, Dict[str, Any]]:
        """Construit les attentes de canaux depuis la configuration runtime."""
        web_only = _env_flag("LUMENA_WEB_ONLY", False)
        telegram_disabled = _env_flag("LUMENA_DISABLE_TELEGRAM", False) or web_only
        discord_disabled = _env_flag("LUMENA_DISABLE_DISCORD", False)
        whatsapp_disabled = _env_flag("LUMENA_DISABLE_WHATSAPP", False) or web_only

        return {
            "telegram": {
                "enabled": not telegram_disabled,
                "disabled_reason": "LUMENA_WEB_ONLY=1" if web_only else ("LUMENA_DISABLE_TELEGRAM=1" if telegram_disabled else None),
            },
            "discord": {
                "enabled": not discord_disabled,
                "disabled_reason": "LUMENA_DISABLE_DISCORD=1" if discord_disabled else None,
            },
            "whatsapp": {
                "enabled": not whatsapp_disabled,
                "disabled_reason": "LUMENA_WEB_ONLY=1" if web_only else ("LUMENA_DISABLE_WHATSAPP=1" if whatsapp_disabled else None),
            },
        }

    def _get_channels_status(self) -> Dict[str, Any]:
        """Retourne un état omnicanal consolidé."""
        expectations = self._build_channel_expectations()
        channels: Dict[str, Dict[str, Any]] = {}

        for name, expected in expectations.items():
            enabled = bool(expected.get("enabled", False))
            channels[name] = {
                "registered": False,
                "enabled": enabled,
                "running": False,
                "state": "not_registered" if enabled else "disabled_by_config",
                "error": expected.get("disabled_reason"),
            }

        manager_running = False
        manager_registered = 0
        try:
            from src.channels.manager import get_channel_manager
            manager = get_channel_manager()
            snapshot = manager.get_runtime_snapshot()
            manager_running = bool(snapshot.get("running", False))
            manager_registered = int(snapshot.get("registered_count", 0))

            for channel_name, raw_status in (snapshot.get("channels") or {}).items():
                if channel_name not in channels:
                    channels[channel_name] = {
                        "registered": False,
                        "enabled": True,
                        "running": False,
                        "state": "not_registered",
                        "error": None,
                    }
                merged = channels[channel_name]
                if isinstance(raw_status, dict):
                    merged.update(raw_status)
                if not merged.get("enabled", True) and merged.get("state") in {"stopped", "not_registered"}:
                    merged["state"] = "disabled_by_config"
        except Exception as e:
            for entry in channels.values():
                if entry.get("state") == "not_registered":
                    entry["error"] = f"channel_manager_unavailable: {e}"

        running_count = sum(1 for c in channels.values() if c.get("running"))
        enabled_count = sum(1 for c in channels.values() if c.get("enabled"))
        return {
            "manager_running": manager_running,
            "manager_registered": manager_registered,
            "enabled_count": enabled_count,
            "running_count": running_count,
            "legacy_channel_path_enabled": _env_flag("LUMENA_CHANNELS_LEGACY_IMPORTS", False),
            "channels": channels,
        }

    def get_status(self, core) -> Dict[str, Any]:
        """Retourne le status complet de LUMENA.

        *core* est passé pour accéder aux attributs non-migrés (is_initialized, etc.).
        """
        channels_status = self._get_channels_status()
        status = {
            "initialized": core.is_initialized,
            "personality": core.personality.name,
            "modules": {
                "llm": True,
                "memory": self.memory is not None,
                "emotions": self.ctx.emotion_manager is not None,
                "tools": self.ctx.tool_system is not None,
                "repo_map": self.ctx.repo_map is not None,
                "code_index": self.ctx.code_index is not None,
                "rules_loader": self.ctx.rules_loader is not None,
                "hooks": self.ctx.hook_system is not None,
                "instincts": self.ctx.instinct_system is not None,
                "tts": self.ctx.tts is not None,
            },
            "conversation": {
                "messages": len(core.context.messages),
                "max_messages": core.context.max_messages,
            },
            "last_activity": {
                "url": getattr(core, "_last_mentioned_url", None),
                "search": getattr(core, "_last_search_query", None),
            },
            "channels": channels_status,
        }

        if self.ctx.emotion_manager:
            status["mood"] = self.ctx.emotion_manager.get_mood().value
            status["emotions"] = self.ctx.emotion_manager.get_stats()

        if self.memory:
            from .memory_service import MemoryService
            # Compat: get_memory_stats peut être appelé via le proxy
            mem_svc = getattr(core, "_memory_svc", None)
            if mem_svc and isinstance(mem_svc, MemoryService):
                status["memory_stats"] = mem_svc.get_memory_stats()
            else:
                status["memory_stats"] = core.get_memory_stats()

        if self.ctx.instinct_system:
            status["instincts"] = self.ctx.instinct_system.get_stats()

        return status

    def get_capabilities(self) -> List[str]:
        """Retourne la liste des capacités disponibles."""
        capabilities = [
            "💬 Chat et conversation",
            "🧠 Raisonnement contextuel",
        ]
        try:
            from src.reasoning import ReActLoop
            capabilities.append("🔄 Raisonnement ReAct (think-and-act)")
        except ImportError:
            pass  # ReAct non disponible, capability non listée

        if self.memory:
            capabilities.append("💾 Mémoire persistante ChromaDB")
        if self.ctx.emotion_manager:
            capabilities.append("🎭 Émotions autonomes")
        if self.ctx.tool_system:
            capabilities.append("🛠️ Système de tools automatique")
        if self.ctx.repo_map:
            capabilities.append("🗺️ Conscience du projet (RepoMap)")
        if self.ctx.code_index:
            capabilities.append("🔍 Recherche sémantique de code")
        if self.ctx.rules_loader:
            capabilities.append("📜 Règles projet personnalisées")
        if self.ctx.hook_system:
            capabilities.append("🪝 Système d'événements (Hooks)")
        if self.ctx.instinct_system:
            capabilities.append("🧬 Apprentissage par instincts")
        if self.ctx.tts:
            capabilities.append("🔊 Synthèse vocale (TTS)")

        capabilities.extend([
            "🌐 Recherche web",
            "📥 Récupération d'URL",
            "📁 Gestion de workspace",
            "📝 Création de fichiers",
            "🔧 Analyse de code",
            "🐛 Aide au debugging",
        ])
        return capabilities

    def print_status(self, core) -> str:
        """Affiche le status de façon formatée."""
        status = self.get_status(core)
        capabilities = self.get_capabilities()

        output = f"""
╔══════════════════════════════════════════════════╗
║  🌟 LUMENA - Status                               ║
╠══════════════════════════════════════════════════╣
║  Personnalité: {status['personality']:<34} ║
║  Initialisée: {'✅ Oui' if status['initialized'] else '❌ Non':<35} ║
║  Messages: {status['conversation']['messages']:<38} ║
╠══════════════════════════════════════════════════╣
║  📦 Modules:                                      ║
"""
        for module, enabled in status['modules'].items():
            icon = "✅" if enabled else "❌"
            output += f"║    {icon} {module:<43} ║\n"

        output += f"""╠══════════════════════════════════════════════════╣
║  🎯 Capacités ({len(capabilities)}):                              ║
"""
        for cap in capabilities[:8]:
            output += f"║    {cap:<46} ║\n"
        if len(capabilities) > 8:
            output += f"║    ... et {len(capabilities) - 8} autres                           ║\n"

        output += "╚══════════════════════════════════════════════════╝"
        return output
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
