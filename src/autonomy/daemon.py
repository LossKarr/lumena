"""
🌟 LUMENA - Daemon Principal 24/7

Le daemon qui fait tourner LUMENA en permanence.
Elle reste active, observe, réfléchit et agit de manière autonome.
"""

import asyncio
import signal
import sys
import os
import shutil
from typing import Optional, Dict, Any, Callable
from datetime import datetime, timedelta
from pathlib import Path
from loguru import logger

# Imports LUMENA
from ..core import LumenaCore, get_lumena
from ..emotion import get_emotion_manager
from .curiosity import get_curiosity_module, AutonomousAction, ActionType
from .goals import get_goal_manager
from .scheduler import get_scheduler
from .heartbeat import HeartbeatSystem, get_heartbeat
from .activity_ledger import append_autonomy_event
from ..learning.reflection import get_self_reflection
from ..utils.persistence import atomic_write_json

try:
    from ..telemetry import publish_trace
    _TELEMETRY_AVAILABLE = True
except Exception:
    _TELEMETRY_AVAILABLE = False


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class LumenaDaemon:
    """
    🌟 Le Daemon Principal de LUMENA
    
    Fait tourner LUMENA 24/7 de manière autonome.
    Gère tous les sous-systèmes et la boucle de vie principale.
    """
    
    def __init__(self, data_dir: Optional[Path] = None):
        # Répertoire des données
        from src.utils.paths import DATA_DIR
        self.data_dir = data_dir or DATA_DIR
        self.data_dir.mkdir(parents=True, exist_ok=True)
        
        # Core
        self.lumena: Optional[LumenaCore] = None
        
        # Sous-systèmes
        self.curiosity = get_curiosity_module()
        self.goals = get_goal_manager(self.data_dir / "goals")
        self.scheduler = get_scheduler(self.data_dir / "scheduler")
        self.emotions = get_emotion_manager()
        self.heartbeat: Optional[HeartbeatSystem] = None  # Initialisé au start
        
        # État
        self.running = False
        self.started_at: Optional[datetime] = None
        self.user_present = False
        self.last_user_activity: datetime = datetime.now()

        # Mode veille (hibernation totale)
        self.sleep_mode = False
        self._sleep_state_file = self.data_dir / "sleep_state.json"
        self._restore_sleep_state()
        
        # Tâches async
        self._main_task: Optional[asyncio.Task] = None
        self._autonomous_task: Optional[asyncio.Task] = None
        self._active_action_task: Optional[asyncio.Task] = None
        
        # Callbacks pour les événements
        self._on_autonomous_action: Optional[Callable[[AutonomousAction], None]] = None
        self._on_status_change: Optional[Callable[[str], None]] = None

        # Exécution autonome réelle (opt-in). OFF par défaut pour éviter toute casse.
        self.enable_action_execution = _env_flag("LUMENA_AUTONOMY_EXECUTE_ACTIONS", False)
        self.autonomy_action_timeout_seconds = int(os.getenv("LUMENA_AUTONOMY_ACTION_TIMEOUT_SEC", "120"))
        # Timeout étendu pour les actions de recherche longues (deep_research, web crawl, etc.)
        self.autonomy_research_timeout_seconds = int(os.getenv("LUMENA_AUTONOMY_RESEARCH_TIMEOUT_SEC", "900"))
        self.goal_execution_cooldown_seconds = int(os.getenv("LUMENA_AUTONOMY_GOAL_COOLDOWN_SEC", "300"))
        self.goal_max_consecutive_failures = int(os.getenv("LUMENA_AUTONOMY_GOAL_MAX_FAILURES", "3"))
        self.progressive_mode_enabled = _env_flag("LUMENA_AUTONOMY_PROGRESSIVE_MODE", True)
        self.max_actions_per_hour = int(os.getenv("LUMENA_AUTONOMY_MAX_ACTIONS_PER_HOUR", "6"))
        self.action_repeat_cooldown_seconds = int(os.getenv("LUMENA_AUTONOMY_ACTION_REPEAT_COOLDOWN_SEC", "900"))
        self.autonomy_min_free_gb = float(os.getenv("LUMENA_AUTONOMY_MIN_FREE_GB", "10"))
        self.allowed_action_types = self._parse_allowed_action_types(
            os.getenv("LUMENA_AUTONOMY_ALLOWED_ACTIONS", "EXPLORE_WEB,LEARN_SOMETHING,REFLECT,WRITE_DIARY,CHECK_NEWS")
        )
        self._executed_action_timestamps: list[datetime] = []
        self._recent_action_signatures: Dict[str, datetime] = {}
        
        logger.info("🌟 LUMENA Daemon créé")

    def _parse_allowed_action_types(self, raw: str) -> set[str]:
        allowed: set[str] = set()
        for item in (raw or "").split(","):
            value = item.strip().upper()
            if not value:
                continue
            if value in ActionType.__members__:
                allowed.add(value)
        return allowed

    def _prune_action_window(self) -> None:
        cutoff = datetime.now() - timedelta(hours=1)
        self._executed_action_timestamps = [t for t in self._executed_action_timestamps if t >= cutoff]

    def _prune_action_signatures(self) -> None:
        if self.action_repeat_cooldown_seconds <= 0:
            self._recent_action_signatures.clear()
            return
        cutoff = datetime.now() - timedelta(seconds=max(1, self.action_repeat_cooldown_seconds))
        self._recent_action_signatures = {
            key: ts for key, ts in self._recent_action_signatures.items() if ts >= cutoff
        }

    def _action_signature(self, action: AutonomousAction) -> str:
        """Build a deterministic signature to prevent repeated autonomous actions."""
        topic = ""
        if isinstance(action.metadata, dict):
            topic = str(action.metadata.get("topic", "")).strip().lower()
        desc = (action.description or "").strip().lower()
        return f"{action.action_type.value}|{topic}|{desc[:120]}"

    def _is_repeated_action(self, action: AutonomousAction) -> bool:
        if self.action_repeat_cooldown_seconds <= 0:
            return False
        self._prune_action_signatures()
        signature = self._action_signature(action)
        return signature in self._recent_action_signatures

    def _free_disk_gb(self) -> float | None:
        try:
            usage = shutil.disk_usage(self.data_dir)
            return usage.free / (1024 ** 3)
        except Exception as e:
            logger.debug(f"Impossible de lire l'espace disque libre: {e}")
            return None

    def _disk_guard_block_reason(self, action: AutonomousAction) -> str:
        if self.autonomy_min_free_gb <= 0:
            return ""
        if not self.running and os.getenv("LUMENA_AUTONOMY_LEDGER_IN_TESTS", "") != "1":
            return ""
        free_gb = self._free_disk_gb()
        if free_gb is None or free_gb >= self.autonomy_min_free_gb:
            return ""
        allowed_when_low_disk = {ActionType.REFLECT, ActionType.WRITE_DIARY}
        if action.action_type in allowed_when_low_disk:
            return ""
        return (
            f"disk_guard: free disk {free_gb:.1f} GB below "
            f"{self.autonomy_min_free_gb:.1f} GB; heavy autonomous action blocked"
        )

    def _autonomy_block_reason(self, action: AutonomousAction) -> str:
        if not self.enable_action_execution:
            return "execution_disabled"
        if self.progressive_mode_enabled and self.allowed_action_types:
            action_key = action.action_type.name.upper()
            if action_key not in self.allowed_action_types:
                return f"allowlist_blocked:{action_key}"
        disk_reason = self._disk_guard_block_reason(action)
        if disk_reason:
            return disk_reason
        if self._is_repeated_action(action):
            return "repeat_cooldown"
        self._prune_action_window()
        if self.max_actions_per_hour > 0 and len(self._executed_action_timestamps) >= self.max_actions_per_hour:
            return "hourly_budget_reached"
        return ""

    def _log_autonomy_event(self, event_type: str, action: AutonomousAction, **extra) -> None:
        if not self.running and os.getenv("LUMENA_AUTONOMY_LEDGER_IN_TESTS", "") != "1":
            return
        try:
            append_autonomy_event(
                event_type,
                data_dir=self.data_dir,
                action_type=action.action_type.value,
                description=action.description,
                metadata=action.metadata if isinstance(action.metadata, dict) else {},
                **extra,
            )
        except Exception as e:
            logger.debug(f"Ledger autonomie non ecrit: {e}")

    def _can_execute_autonomous_action(self, action: AutonomousAction) -> bool:
        reason = self._autonomy_block_reason(action)
        if reason:
            logger.debug(f"Action autonome bloquee: {reason}")
            return False

        if not self.enable_action_execution:
            return False

        if self.progressive_mode_enabled and self.allowed_action_types:
            action_key = action.action_type.name.upper()
            if action_key not in self.allowed_action_types:
                logger.debug(f"Action autonome bloquée par allowlist progressive: {action_key}")
                return False

        if self._is_repeated_action(action):
            logger.debug("Action autonome bloquée: répétition détectée")
            return False

        self._prune_action_window()
        if self.max_actions_per_hour > 0 and len(self._executed_action_timestamps) >= self.max_actions_per_hour:
            logger.debug("Budget horaire d'actions autonomes atteint")
            return False

        return True

    def _record_executed_action(self, action: AutonomousAction) -> None:
        self._executed_action_timestamps.append(datetime.now())
        self._prune_action_window()
        if self.action_repeat_cooldown_seconds > 0:
            self._recent_action_signatures[self._action_signature(action)] = datetime.now()
            self._prune_action_signatures()
    
    async def start(self):
        """Démarre le daemon."""
        if self.running:
            logger.warning("Daemon déjà en cours")
            return
        
        logger.info("🚀 Démarrage du daemon LUMENA...")
        
        # Initialiser le core
        self.lumena = get_lumena()
        if self.lumena.is_initialized:
            initialized = True
        else:
            initialized = await self.lumena.initialize()

        if not initialized:
            logger.error("❌ Impossible d'initialiser LUMENA")
            return
        
        # Configurer le scheduler
        self.scheduler.setup_default_tasks()
        await self.scheduler.start()
        
        # Configurer le heartbeat
        self.heartbeat = get_heartbeat(
            workspace_dir=self.data_dir.parent,
            on_task_callback=self._handle_heartbeat_task
        )
        await self.heartbeat.start()
        
        # Configurer les callbacks
        self.curiosity.on_action(self._handle_autonomous_action)
        
        # Démarrer les boucles
        self.running = True
        self.started_at = datetime.now()
        
        self._main_task = asyncio.create_task(self._main_loop())
        self._autonomous_task = asyncio.create_task(self._autonomous_loop())
        
        logger.info("✅ Daemon LUMENA démarré")
        logger.info(f"💓 Heartbeat: {'actif' if not self.heartbeat.is_effectively_empty() else 'en veille'}")
        
        # Salutation initiale (optionnelle selon implémentation core)
        try:
            greet_fn = getattr(self.lumena, "greet", None)
            if callable(greet_fn):
                greeting = greet_fn()
                if greeting:
                    logger.info(f"💬 {greeting}")
        except Exception as e:
            logger.debug(f"Salutation initiale ignorée: {e}")
    
    async def stop(self):
        """Arrête le daemon proprement."""
        if not self.running:
            return
        
        logger.info("🛑 Arrêt du daemon LUMENA...")
        
        self.running = False
        
        # Arrêter le heartbeat
        if self.heartbeat:
            await self.heartbeat.stop()
        
        # Arrêter le scheduler
        await self.scheduler.stop()
        
        # Annuler les tâches
        if self._main_task:
            self._main_task.cancel()
        if self._autonomous_task:
            self._autonomous_task.cancel()
        if self._active_action_task:
            self._active_action_task.cancel()
        
        # Arrêter le core
        if self.lumena:
            await self.lumena.shutdown()
        
        logger.info("👋 Daemon LUMENA arrêté")
    
    async def _main_loop(self):
        """
        Boucle principale du daemon.
        Gère les événements et le cycle de vie.
        """
        logger.debug("🔄 Boucle principale démarrée")
        
        while self.running:
            try:
                # Hibernation totale en mode veille
                if self.sleep_mode:
                    await asyncio.sleep(5)
                    continue

                # Vérifier la présence utilisateur
                self._check_user_presence()
                
                # Mettre à jour les émotions passivement
                if self.emotions:
                    mood_change = self.emotions.update_passive(user_present=self.user_present)
                    if mood_change:
                        logger.info(f"🎭 {mood_change}")
                
                # Attendre
                await asyncio.sleep(30)  # Check toutes les 30 secondes
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Erreur boucle principale: {e}")
                await asyncio.sleep(5)
    
    async def _autonomous_loop(self):
        """
        Boucle d'autonomie.
        Gère la curiosité et les actions spontanées.
        """
        logger.debug("🧠 Boucle autonome démarrée")
        
        while self.running:
            try:
                # Hibernation totale en mode veille
                if self.sleep_mode:
                    await asyncio.sleep(5)
                    continue

                # Mettre à jour la curiosité
                action = self.curiosity.update(self.user_present)
                
                if action:
                    action_task = asyncio.create_task(self._execute_autonomous_action(action))
                    self._active_action_task = action_task
                    try:
                        await action_task
                    except asyncio.CancelledError:
                        logger.debug("Action autonome annulée (interaction utilisateur)")
                    finally:
                        if self._active_action_task is action_task:
                            self._active_action_task = None
                
                # Vérifier les objectifs
                next_goal = self.goals.get_next_goal()
                if next_goal and not action:
                    # Travailler sur un objectif si pas d'action spontanée
                    await self._work_on_goal(next_goal)
                
                # Attendre entre les checks
                await asyncio.sleep(60)  # Check chaque minute
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Erreur boucle autonome: {e}")
                await asyncio.sleep(10)
    
    def _check_user_presence(self):
        """Vérifie si l'utilisateur est présent."""
        # Pour l'instant, basé sur le temps d'inactivité
        idle_minutes = (datetime.now() - self.last_user_activity).total_seconds() / 60
        
        was_present = self.user_present
        self.user_present = idle_minutes < 10  # Présent si actif dans les 10 dernières minutes
        
        if was_present and not self.user_present:
            logger.info("👤 L'utilisateur semble absent")
        elif not was_present and self.user_present:
            logger.info("👤 L'utilisateur est de retour!")
    
    async def _execute_autonomous_action(self, action: AutonomousAction):
        """Exécute une action autonome."""
        # Vérifier si l'action peut être exécutée AVANT de logger/notifier
        self._log_autonomy_event("action_candidate", action, decision="considered")
        block_reason = self._autonomy_block_reason(action)
        if not block_reason:
            logger.info(f"🎯 Exécution: {action.description}")
            # P7 — telemetry action autonome
            if _TELEMETRY_AVAILABLE:
                try:
                    publish_trace(
                        stage="daemon_action_start",
                        status="start",
                        mode="autonomy",
                        summary=f"{action.action_type.value}: {action.description[:80]}",
                    )
                except Exception:
                    pass
            if self._on_autonomous_action:
                self._on_autonomous_action(action)
            executed = await self._execute_action_with_core(action)
            if executed:
                self._record_executed_action(action)
                self._log_autonomy_event("action_completed", action, decision="completed")
                if _TELEMETRY_AVAILABLE:
                    try:
                        publish_trace(
                            stage="daemon_action_success",
                            status="ok",
                            mode="autonomy",
                            summary=f"{action.action_type.value}: {action.description[:80]}",
                        )
                    except Exception:
                        pass
            else:
                self._log_autonomy_event(
                    "action_failed",
                    action,
                    decision="failed",
                    reason="core_returned_false",
                )
                if _TELEMETRY_AVAILABLE:
                    try:
                        publish_trace(
                            stage="daemon_action_failure",
                            status="error",
                            mode="autonomy",
                            summary=f"{action.action_type.value}: {action.description[:80]}",
                        )
                    except Exception:
                        pass
        else:
            logger.debug("Autonomy action execution désactivée (LUMENA_AUTONOMY_EXECUTE_ACTIONS=0)")
        
        # Obtenir une pensée de LUMENA sur l'action
        if block_reason:
            self._log_autonomy_event(
                "action_blocked",
                action,
                decision="blocked",
                reason=block_reason,
                safe_to_execute=False,
            )

        thought = self.curiosity.get_thought()
        logger.info(f"💭 {thought}")

    async def _execute_action_with_core(self, action: AutonomousAction) -> bool:
        """Exécute une action autonome via LumenaCore de manière sécurisée (opt-in)."""
        if not self.lumena:
            logger.debug("Action autonome ignorée: LumenaCore indisponible")
            return False

        current_year = datetime.now().year
        prompt = None
        # Hard cap d'itérations par type d'action autonome (le LLM ignore les hints prompt)
        _ACTION_MAX_ITERATIONS = {
            ActionType.WRITE_DIARY: 3,
            ActionType.REFLECT: 3,
            ActionType.EXPLORE_WEB: 4,
            ActionType.CHECK_NEWS: 4,
            ActionType.LEARN_SOMETHING: 5,
        }
        action_max_iter = _ACTION_MAX_ITERATIONS.get(action.action_type)  # None = défaut 35
        if action.action_type == ActionType.EXPLORE_WEB:
            topic = action.metadata.get("topic", "un sujet utile")
            prompt = (
                f"Explore brièvement le web sur {topic} et résume les points clés. "
                f"Priorise les infos récentes de {current_year}. "
                f"IMPORTANT: Utilise web_search_brave ou web_search_ddg pour chercher. "
                f"N'utilise PAS browser_navigate ni browser_get_content (trop lent). "
                f"2-3 itérations maximum."
            )
        elif action.action_type == ActionType.LEARN_SOMETHING:
            topic = action.metadata.get("topic", "un sujet nouveau")
            prompt = (
                f"Apprends quelque chose sur {topic} puis note un résumé actionnable. "
                f"Priorise les informations et tendances de {current_year}. "
                f"IMPORTANT: Utilise web_search_brave ou web_search_ddg pour chercher. "
                f"N'utilise PAS browser_navigate ni browser_get_content (trop lent). "
                f"2-3 itérations maximum."
            )
        elif action.action_type == ActionType.REFLECT:
            # Utiliser le vrai système de réflexion au lieu d'un prompt vague
            try:
                reflection = get_self_reflection(self.data_dir).reflect()
                logger.info(f"🪞 Réflexion structurée: {reflection[:160]}")
                return True  # Réflexion faite sans LLM — pas besoin de prompt
            except Exception as e:
                logger.warning(f"Réflexion fallback prompt: {e}")
                prompt = "Fais une réflexion courte sur les dernières interactions et propose une amélioration concrète."
        elif action.action_type == ActionType.WRITE_DIARY:
            _today = datetime.now().strftime("%Y-%m-%d")
            prompt = (
                f"Écris une note de journal concise sur ce que tu as appris récemment. "
                f"Utilise UNIQUEMENT l'outil write_journal pour sauvegarder la note "
                f"(date={_today}). N'utilise PAS write_file, edit_file ou create_file. "
                f"Ne crée PAS de fichier dans workspace/."
            )
        elif action.action_type == ActionType.CHECK_NEWS:
            prompt = f"Fais un check d'actualités général de {current_year} et extrais 3 éléments pertinents."

        if not prompt:
            logger.debug(f"Action autonome non mappée, mode journal only: {action.action_type.value}")
            return False

        # Les actions de recherche (web + apprentissage) peuvent prendre beaucoup plus de temps
        _research_types = {ActionType.EXPLORE_WEB, ActionType.LEARN_SOMETHING, ActionType.CHECK_NEWS}
        effective_timeout = (
            self.autonomy_research_timeout_seconds
            if action.action_type in _research_types
            else self.autonomy_action_timeout_seconds
        )

        try:
            response = await asyncio.wait_for(
                self.lumena.think_and_act(prompt, max_iterations=action_max_iter),
                timeout=effective_timeout,
            )
            response_str = str(response)[:500]
            logger.info(f"✅ Action autonome exécutée ({action.action_type.value}): {response_str[:160]}")

            # 🧠 Stocker le résultat : instinct + journal de réflexion
            try:
                if hasattr(self.lumena, 'instinct_system') and self.lumena.instinct_system:
                    self.lumena.instinct_system.learn(
                        pattern=prompt[:200],
                        response=response_str[:300],
                        was_successful=True,
                        category="autonomous",
                    )
            except Exception as e_inst:
                logger.debug(f"Instinct auto non créé: {e_inst}")

            try:
                refl = get_self_reflection(self.data_dir)
                topic = action.metadata.get("topic", action.action_type.value)
                refl.log_action(
                    action=f"{action.action_type.value}: {topic}",
                    result=response_str[:300],
                    success=True,
                )
            except Exception as e_refl:
                logger.debug(f"Journal réflexion non écrit: {e_refl}")

            return True
        except asyncio.TimeoutError:
            logger.warning(f"⏱️ Timeout action autonome: {action.action_type.value} (timeout={effective_timeout}s)")
            return False
        except Exception as e:
            logger.error(f"Erreur exécution action autonome {action.action_type.value}: {e}")
            return False
    
    async def _work_on_goal(self, goal):
        """Travaille sur un objectif."""
        from .goals import GoalStatus
        
        if goal.status == GoalStatus.PENDING:
            goal.status = GoalStatus.IN_PROGRESS
            logger.info(f"🎯 Début objectif: {goal.title}")

        if not self.enable_action_execution or not self.lumena:
            goal.update_progress(1)
            return

        metadata = goal.metadata if isinstance(goal.metadata, dict) else {}
        now = datetime.now()

        last_attempt_raw = metadata.get("last_attempt_at")
        if isinstance(last_attempt_raw, str):
            try:
                last_attempt = datetime.fromisoformat(last_attempt_raw)
                elapsed = (now - last_attempt).total_seconds()
                if elapsed < max(0, self.goal_execution_cooldown_seconds):
                    return
            except Exception as e:
                logger.debug(f"Parse last_attempt_at: {e}")

        prompt = self._build_goal_prompt(goal)
        metadata["last_attempt_at"] = now.isoformat()
        goal.metadata = metadata

        # P3 — envelope de traçabilité pour cette exécution de goal
        try:
            from .task_envelope import TaskEnvelope
            _goal_envelope = TaskEnvelope.for_autonomous(
                origin="goals",
                intent=str(goal.title or goal.description or "goal autonome")[:200],
                tool_category="system",
                risk_level="medium",
                budget_seconds=max(10, self.autonomy_action_timeout_seconds),
            )
            logger.debug("[envelope] goal '{}' — {}", goal.title, _goal_envelope)
        except Exception:
            pass

        try:
            _goal_envelope = goal.build_task_envelope(
                budget_seconds=max(10, self.autonomy_action_timeout_seconds),
            )
            metadata["envelope_origin"] = _goal_envelope.origin
            metadata["envelope_intent"] = _goal_envelope.intent
            metadata["envelope_workspace"] = _goal_envelope.workspace
            metadata["envelope_tool_category"] = _goal_envelope.tool_category
            metadata["envelope_budget_seconds"] = _goal_envelope.budget_seconds
            metadata["envelope_risk_level"] = _goal_envelope.risk_level
            metadata["envelope_requires_verification"] = _goal_envelope.requires_verification
            goal.metadata = metadata
            logger.debug("[envelope] goal '{}' -> {}", goal.title, _goal_envelope)
        except Exception as e:
            logger.warning("[envelope] goal '{}' invalide: {}", goal.title, e)

        try:
            response = await asyncio.wait_for(
                self.lumena.think_and_act(prompt),
                timeout=max(10, self.autonomy_action_timeout_seconds),
            )
            goal.notes.append(f"{datetime.now().isoformat()} ✅ {str(response)[:240]}")
            metadata["consecutive_failures"] = 0
            goal.metadata = metadata
            goal.update_progress(1)
        except asyncio.TimeoutError:
            self._record_goal_failure(goal, "timeout")
        except Exception as e:
            self._record_goal_failure(goal, str(e))

    def _build_goal_prompt(self, goal) -> str:
        """Construit un prompt d'exécution orienté objectif."""
        goal_type = getattr(getattr(goal, "goal_type", None), "value", "general")
        return (
            f"[OBJECTIF AUTONOME]\n"
            f"Type: {goal_type}\n"
            f"Titre: {goal.title}\n"
            f"Description: {goal.description}\n"
            f"Progression: {goal.steps_completed}/{goal.steps_total}\n\n"
            "Instructions:\n"
            "1) Exécute une petite étape concrète et sûre liée à cet objectif.\n"
            "2) Si rien d'utile n'est faisable maintenant, réponds ACTION_SKIPPED avec la raison.\n"
            "3) Réponds en 1-3 phrases, sans blabla."
        )

    def _record_goal_failure(self, goal, reason: str) -> None:
        """Enregistre un échec de goal et protège contre les boucles d'échec."""
        from .goals import GoalStatus

        metadata = goal.metadata if isinstance(goal.metadata, dict) else {}
        failures = int(metadata.get("consecutive_failures", 0)) + 1
        metadata["consecutive_failures"] = failures
        goal.metadata = metadata
        goal.notes.append(f"{datetime.now().isoformat()} ❌ {reason[:240]}")

        if failures >= max(1, self.goal_max_consecutive_failures):
            goal.status = GoalStatus.FAILED
            goal.notes.append(
                f"{datetime.now().isoformat()} ⚠️ auto-failed après {failures} échecs consécutifs"
            )
            logger.warning(f"Goal auto-failed après échecs répétés: {goal.title}")
    
    def _handle_autonomous_action(self, action: AutonomousAction):
        """Handler pour les actions autonomes (Phase 4.13: error handling)."""
        if self._on_autonomous_action:
            try:
                self._on_autonomous_action(action)
            except Exception as e:
                logger.error(f"Erreur dans callback autonomous action: {e}")
    
    async def _handle_heartbeat_task(self, prompt: str) -> str:
        """Handler pour les tâches heartbeat."""
        if not self.lumena:
            return "HEARTBEAT_OK"
        
        try:
            # Utiliser think_and_act pour que Lumena utilise ses outils
            response = await self.lumena.think_and_act(prompt)
            return response
        except Exception as e:
            logger.error(f"Erreur heartbeat task: {e}")
            return "HEARTBEAT_OK"
    
    def user_interaction(self, message: str):
        """Signale une interaction utilisateur."""
        self.last_user_activity = datetime.now()
        self.curiosity.user_interacted()
        self.user_present = True
        task = self._active_action_task
        if task and not task.done():
            task.cancel()
            logger.info("⏸️ Action autonome interrompue: interaction utilisateur détectée")
    
    async def chat(self, message: str) -> str:
        """Interface de chat avec le daemon."""
        if not self.lumena:
            return "LUMENA n'est pas initialisée"
        
        self.user_interaction(message)
        response = await self.lumena.chat(message)
        return response
    
    def on_autonomous_action(self, callback: Callable[[AutonomousAction], None]):
        """Enregistre un callback pour les actions autonomes."""
        self._on_autonomous_action = callback

    # ------------------------------------------------------------------ #
    #  MODE VEILLE                                                         #
    # ------------------------------------------------------------------ #

    def _restore_sleep_state(self):
        """Restaure l'état de veille depuis le disque au démarrage."""
        try:
            if self._sleep_state_file.exists():
                import json as _json
                data = _json.loads(self._sleep_state_file.read_text(encoding="utf-8"))
                self.sleep_mode = bool(data.get("sleeping", False))
                if self.sleep_mode:
                    since = data.get("since", "?")
                    logger.info(f"😴 Mode veille restauré (en veille depuis {since})")
        except Exception as e:
            logger.warning(f"Impossible de restaurer l'état de veille: {e}")

    def _save_sleep_state(self):
        """Persiste l'état de veille sur disque."""
        state = {"sleeping": self.sleep_mode, "since": datetime.now().isoformat()}
        try:
            atomic_write_json(self._sleep_state_file, state)
        except Exception as e:
            logger.warning(f"Impossible de sauvegarder l'état de veille: {e}")

    async def enter_sleep(self) -> str:
        """Mode veille : suspend heartbeat, scheduler et boucles autonomes."""
        if self.sleep_mode:
            return "😴 Je suis déjà en mode veille."
        self.sleep_mode = True
        self._save_sleep_state()
        # Suspendre le heartbeat
        if self.heartbeat:
            await self.heartbeat.stop()
        # Suspendre le scheduler
        await self.scheduler.stop()
        # Annuler toute action autonome en cours
        if self._active_action_task and not self._active_action_task.done():
            self._active_action_task.cancel()
        logger.info("😴 Lumena est entrée en mode veille (hibernation totale)")
        return (
            "😴 Mode veille activé. Heartbeat, scheduler et boucles autonomes suspendus. "
            "Dis « Salut Lumena » pour me réveiller."
        )

    async def exit_sleep(self) -> str:
        """Réveille Lumena et relance tous les sous-systèmes."""
        if not self.sleep_mode:
            return "☀️ Je ne suis pas en veille."
        self.sleep_mode = False
        # Supprimer le fichier persistant
        try:
            if self._sleep_state_file.exists():
                self._sleep_state_file.unlink()
        except Exception as e:
            logger.warning(f"Erreur suppression sleep state file: {e}")
        # Relancer le heartbeat
        if self.heartbeat:
            await self.heartbeat.start()
        # Relancer le scheduler
        await self.scheduler.start()
        logger.info("☀️ Lumena est sortie du mode veille")
        return "☀️ Me revoilà ! Tous les systèmes relancés. Qu'est-ce que je peux faire pour toi ?"

    def get_status(self) -> Dict[str, Any]:
        """Retourne le statut complet du daemon."""
        uptime = None
        if self.started_at:
            uptime = str(datetime.now() - self.started_at).split('.')[0]
        
        return {
            "running": self.running,
            "uptime": uptime,
            "user_present": self.user_present,
            "autonomy_action_execution": self.enable_action_execution,
            "progressive_mode_enabled": self.progressive_mode_enabled,
            "max_actions_per_hour": self.max_actions_per_hour,
            "actions_last_hour": len(self._executed_action_timestamps),
            "action_repeat_cooldown_seconds": self.action_repeat_cooldown_seconds,
            "autonomy_min_free_gb": self.autonomy_min_free_gb,
            "disk_free_gb": self._free_disk_gb(),
            "recent_action_signatures": len(self._recent_action_signatures),
            "curiosity": self.curiosity.get_status(),
            "goals": self.goals.get_stats(),
            "scheduler": self.scheduler.get_stats(),
            "emotions": self.emotions.get_stats() if self.emotions else None,
            "heartbeat": self.heartbeat.get_status() if self.heartbeat else None,
        }

# Instance globale avec lock thread-safe (Phase 2.1)
import threading
_daemon: Optional[LumenaDaemon] = None
_daemon_lock = threading.Lock()


def get_daemon(data_dir: Optional[Path] = None) -> LumenaDaemon:
    """Obtient l'instance singleton du daemon (thread-safe)."""
    global _daemon
    
    # Double-check locking pattern
    if _daemon is None:
        with _daemon_lock:
            if _daemon is None:
                _daemon = LumenaDaemon(data_dir)
    return _daemon


def get_active_daemon() -> Optional[LumenaDaemon]:
    """Retourne le daemon seulement s'il est déjà instancié (pas de création)."""
    return _daemon


async def run_daemon():
    """Fonction principale pour lancer le daemon."""
    daemon = get_daemon()
    
    # Gérer les signaux d'arrêt
    def signal_handler(sig, frame):
        logger.info("Signal d'arrêt reçu")
        asyncio.create_task(daemon.stop())
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Démarrer
    await daemon.start()
    
    # Attendre
    try:
        while daemon.running:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        await daemon.stop()


if __name__ == "__main__":
    asyncio.run(run_daemon())
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
