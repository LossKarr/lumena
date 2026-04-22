"""
🌟 LUMENA - Boucle ReAct

Implémente le pattern ReAct (Reason + Act) pour le raisonnement.
LUMENA peut réfléchir, décider d'agir, observer le résultat, et itérer.
"""

from typing import Dict, Any, List, Optional, Callable, Awaitable
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime
from pathlib import Path
import asyncio
import json
import os
import re
import platform
import unicodedata
import subprocess
import difflib
from time import perf_counter
from loguru import logger

# ── Imports depuis react_config (constantes, enums, flags) ─────────
from .react_config import (
    ActionType, Thought, Action, Observation, ReActStep, TaskItem,
    IS_WINDOWS, OS_NAME,
    ADVANCED_TOOLS_AVAILABLE, apply_patch, edit_file, parse_patch,
    ContextCompactor, get_token_stats, format_token_stats, estimate_tokens,
    WorkspaceFileGuardrails, get_current_runtime_context,
    TELEMETRY_AVAILABLE, publish_trace, push_trace_context, pop_trace_context,
    current_trace_context, get_file_edits_store, compute_workspace_relative,
    read_text_if_exists,
    _sanitize_llm_output, _PLAN_RE, _TASK_LINE_RE,
    _TOOL_COMPLETION_HINTS, _build_model_specific_hints,
)


from .tool_registry import ToolRegistry
from .response_parser import (
    parse_response as _parse_response_fn,
    parse_plan as _parse_plan_fn,
    extract_balanced_json,
    parse_action_args as _parse_action_args_fn,
)
from .prompt_builder import (
    is_length_finish_reason, has_unbalanced_delimiters,
    has_unclosed_quotes, ends_with_strong_punctuation,
    is_exploratory_tool, is_single_file_creation_request,
    is_project_creation_request, is_web_request,
    looks_code_like_or_structured, looks_incomplete_final_answer,
)
from .history_formatter import (
    compute_obs_limit_from_runtime,
    should_protect_observation,
    split_head_tail,
)

# Sanitization, plan regex, tool hints et model hints dans react_config.py


def _generate_project_slug(query: str) -> str:
    """Génère un slug court à partir de la requête utilisateur pour nommer le dossier projet."""
    _NOISE = {
        # Verbes d'action
        "creer", "cree", "creer", "creee", "moi", "fait", "faire", "fais",
        "genere", "generer", "developpe", "ecris", "ecrire", "construis",
        "create", "make", "build", "write", "generate",
        # Articles / pronoms / prépositions
        "un", "une", "le", "la", "les", "de", "du", "des", "pour", "avec",
        "ma", "mon", "mes", "et", "il", "me", "je", "tu", "nous", "vous",
        "qui", "que", "ce", "ca", "se", "sa", "son", "ses", "ta", "ton", "tes",
        "dans", "sur", "en", "pas", "au", "aux", "par", "est", "sont",
        "the", "a", "an", "my", "for", "with", "and", "in", "on",
        # Mots conversationnels FR
        "okay", "ok", "oui", "non", "bah", "bon", "bien", "allez", "aller",
        "vas", "va", "vraiment", "genre", "tiens", "voila", "alors", "donc",
        "mais", "quand", "comment", "juste", "peut", "peux", "veux", "veut",
        "faut", "dois", "doit", "comme", "tout", "tous", "rien", "jamais",
        "pas", "nan", "ouais", "hop", "hein", "quoi", "deja", "encore",
        # Qualificatifs génériques
        "new", "nouveau", "nouvelle", "complet", "complete", "simple",
        "parfait", "petit", "grand", "super", "top", "cool", "beau",
        # Termes génériques projet
        "site", "web", "page", "photos", "photo", "images", "image",
        "dedans", "besoin", "sit", "workspace", "projet", "project",
    }
    text = unicodedata.normalize("NFKD", query.lower())
    text = re.sub(r"[^\w\s]", "", text)
    words = [w for w in text.split() if w not in _NOISE and len(w) > 2]
    slug = "-".join(words[:3]) if words else "project"
    return re.sub(r"[^a-z0-9\-]", "", slug)[:40] or "project"


class ReActLoop:
    """
    Boucle de raisonnement ReAct pour LUMENA.
    
    Pattern: Think → Act → Observe → (Repeat or Answer)
    """
    
    def __init__(
        self,
        llm_chat_func: Optional[Callable] = None,
        tools: Optional[ToolRegistry] = None,
        conversation_context: str = "",
        active_skills_context: str = "",
        llm_meta_getter: Optional[Callable[[], Dict[str, Any]]] = None,
        max_final_repair_attempts: int = 1,
        task_orchestrator: Optional[Any] = None,
        task_id: Optional[str] = None,
        is_weak_model: bool = False,
        step_callback: Optional[Callable[[str, dict], None]] = None,
        runtime_ctx: Optional[Any] = None,
        max_iterations: Optional[int] = None,
    ):
        """
        Args:
            llm_chat_func: Fonction async qui prend des messages et retourne une réponse
            tools: Registre des outils disponibles
            conversation_context: Contexte des échanges précédents pour les requêtes de suivi
            active_skills_context: Skills auto-selectionnes pour cette requete
            max_iterations: Override du nombre max d'itérations (None = défaut env/35)
        """
        if llm_chat_func is None:
            async def _fallback_llm_chat(_messages):
                return (
                    "THOUGHT: Aucun moteur LLM fourni dans ReActLoop.\n"
                    "ACTION: FINAL\n"
                    "ACTION_INPUT: Configuration incomplète: llm_chat_func est requis pour exécuter des actions."
                )
            self.llm_chat = _fallback_llm_chat
        else:
            self.llm_chat = llm_chat_func
        self.tools = tools or ToolRegistry()
        self.history: List[ReActStep] = []
        _resolved = max_iterations if max_iterations is not None else self._resolve_max_iterations()
        self.max_iterations = _resolved
        self.timeout_seconds = self._resolve_timeout_seconds()
        self.conversation_context = conversation_context  # Pour les requêtes de suivi
        self.active_skills_context = active_skills_context
        self.action_history: List[tuple] = []  # Pour détecter les actions répétées
        self._consecutive_same_action = 0  # Phase 2.2: compteur pour détection de boucle
        self._last_action_signature = None  # Phase 2.2: signature de la dernière action
        self._pending_loop_guidance: Optional[str] = None  # guidance injectée dans prochaine observation
        self._last_auto_advance_iter: int = -1  # garde: max 1 auto-avancement par itération
        # ── Anti-aveuglement browser : track dernière itération où le modèle a "vu" ──
        self._last_browser_visual_iter: int = -1  # dernier iter avec screenshot ou dom_state
        self._browser_blind_streak: int = 0  # nb actions browser consécutives sans voir
        self.llm_meta_getter = llm_meta_getter
        self.max_final_repair_attempts = max(0, int(max_final_repair_attempts))
        self._final_repair_attempts = 0
        self._last_llm_meta: Dict[str, Any] = {}
        self.task_orchestrator = task_orchestrator
        self.task_id = (task_id or "").strip() or None
        self.is_weak_model = bool(is_weak_model)
        self.step_callback: Optional[Callable[[str, dict], None]] = step_callback
        self.runtime_ctx = runtime_ctx  # RuntimeContext snapshot (Phase 2)
        self._run_meta: Dict[str, Any] = {
            "agent_output_incomplete": False,
            "agent_output_warning": None,
            "agent_repair_attempts": 0,
            "agent_final_finish_reason": None,
        }
        # ── Plan TODO ──
        self._task_plan: List[TaskItem] = []
        self._plan_emitted: bool = False
        self._plan_last_emit_state: str = ""  # dédup: n'émet TODO_STATE que si changé
        self._iterations_without_progress: int = 0
        self._last_completed_task_count: int = 0
        self._premature_final_retries: int = 0
        self._plan_guard_retries: int = 0
        self._thought_leak_repairs: int = 0

    @staticmethod
    def _env_int(name: str, default: int, minimum: int = 1) -> int:
        raw = os.getenv(name)
        if raw is None:
            return default
        try:
            return max(minimum, int(str(raw).strip()))
        except Exception:
            return default

    def _is_ide_runtime(self) -> bool:
        try:
            runtime_check = getattr(self.tools, "_is_ide_runtime", None)
            if callable(runtime_check):
                return bool(runtime_check())
            ide_ctx = getattr(self.tools, "ide_context", {}) or {}
            return bool(ide_ctx.get("workspace_path") or ide_ctx.get("active_file_path"))
        except Exception:
            return False

    def _resolve_max_iterations(self) -> int:
        if self._is_ide_runtime():
            default_ide = self._env_int("LUMENA_MAX_REACT_ITERATIONS", 35, minimum=5)
            return self._env_int("LUMENA_MAX_REACT_ITERATIONS_IDE", default_ide, minimum=5)
        return self._env_int("LUMENA_MAX_REACT_ITERATIONS", 35, minimum=5)

    def _resolve_timeout_seconds(self) -> Optional[int]:
        if self._is_ide_runtime():
            raw_ide = os.getenv("LUMENA_REACT_TIMEOUT_IDE")
            if raw_ide is None:
                raw_ide = os.getenv("LUMENA_REACT_TIMEOUT")
                if raw_ide is None:
                    # IDE: timeout de sécurité 1800s (30 min) même sans env var.
                    # Évite que le daemon tourne à l'infini si la boucle ne converge pas.
                    return 1800
            try:
                parsed = int(str(raw_ide).strip())
            except Exception:
                return 1800
            if parsed <= 0:
                return 1800
            return max(30, parsed)

        try:
            parsed = int(str(os.getenv("LUMENA_REACT_TIMEOUT", "900")).strip())
        except Exception:
            parsed = 900
        return max(30, parsed)

    def _history_observation_limit(self) -> int:
        # IDE runtime : valeur spécifique conservée (boucle Desktop courte).
        if self._is_ide_runtime():
            return self._env_int("LUMENA_REACT_HISTORY_OBS_CHARS_IDE", 12000, minimum=500)
        # Phase 7.2 : calibration réelle basée sur le catalogue Lumena.
        #   cf. src/reasoning/history_formatter.py (paliers 2k/8k/24k/32k/40k/48k)
        #   override possible via LUMENA_REACT_OBS_LIMIT / LUMENA_REACT_OBS_CLAMP.
        if self.runtime_ctx is not None:
            return compute_obs_limit_from_runtime(self.runtime_ctx)
        # Legacy fallback (aucun runtime_ctx) : lit LUMENA_REACT_HISTORY_OBS_CHARS.
        return self._env_int("LUMENA_REACT_HISTORY_OBS_CHARS", 8000, minimum=300)

    def _orchestrator_enabled(self) -> bool:
        return bool(self.task_orchestrator and self.task_id)

    def _mark_task_running(self) -> None:
        if not self._orchestrator_enabled():
            return
        try:
            mark_running = getattr(self.task_orchestrator, "mark_running", None)
            if callable(mark_running):
                mark_running(self.task_id)
        except Exception as exc:
            logger.debug("task orchestrator mark_running skipped: {}", exc)

    def _mark_task_checkpoint(self, payload: Dict[str, Any]) -> None:
        if not self._orchestrator_enabled():
            return
        try:
            mark_checkpoint = getattr(self.task_orchestrator, "mark_checkpoint", None)
            if callable(mark_checkpoint):
                mark_checkpoint(self.task_id, payload)
        except Exception as exc:
            logger.debug("task orchestrator mark_checkpoint skipped: {}", exc)

    def _mark_task_done(self, summary: str) -> None:
        if not self._orchestrator_enabled():
            return
        try:
            mark_done = getattr(self.task_orchestrator, "mark_done", None)
            if callable(mark_done):
                mark_done(self.task_id, result_summary=summary[:1000])
        except Exception as exc:
            logger.debug("task orchestrator mark_done skipped: {}", exc)

    def _mark_task_waiting_io(self, error: str, checkpoint: Optional[Dict[str, Any]] = None) -> None:
        if not self._orchestrator_enabled():
            return
        try:
            mark_waiting_io = getattr(self.task_orchestrator, "mark_waiting_io", None)
            if callable(mark_waiting_io):
                mark_waiting_io(
                    self.task_id,
                    error=error[:800],
                    checkpoint=dict(checkpoint) if checkpoint else None,
                )
        except Exception as exc:
            logger.debug("task orchestrator mark_waiting_io skipped: {}", exc)

    def _mark_task_failed(self, error: str) -> None:
        if not self._orchestrator_enabled():
            return
        try:
            mark_failed = getattr(self.task_orchestrator, "mark_failed", None)
            if callable(mark_failed):
                mark_failed(self.task_id, error=error[:800])
        except Exception as exc:
            logger.debug("task orchestrator mark_failed skipped: {}", exc)

    def get_run_meta(self) -> Dict[str, Any]:
        """Runtime metadata for API/UI after a run."""
        meta = dict(self._run_meta)
        if self._task_plan:
            completed = sum(1 for t in self._task_plan if t.completed)
            meta["plan"] = {
                "total_tasks": len(self._task_plan),
                "completed_tasks": completed,
                "tasks": [
                    {
                        "description": t.description,
                        "completed": t.completed,
                        "completed_at_iteration": t.completed_at_iteration,
                    }
                    for t in self._task_plan
                ],
            }
        return meta

    def _get_llm_meta(self) -> Dict[str, Any]:
        if not self.llm_meta_getter:
            return {}
        try:
            meta = self.llm_meta_getter() or {}
            return meta if isinstance(meta, dict) else {}
        except Exception:
            return {}

    @staticmethod
    def _is_length_finish_reason(finish_reason: Optional[str]) -> bool:
        return is_length_finish_reason(finish_reason)

    @staticmethod
    def _has_unbalanced_delimiters(text: str) -> bool:
        return has_unbalanced_delimiters(text)

    @staticmethod
    def _has_unclosed_quotes(text: str) -> bool:
        return has_unclosed_quotes(text)

    @staticmethod
    def _ends_with_strong_punctuation(text: str) -> bool:
        return ends_with_strong_punctuation(text)

    @staticmethod
    def _is_exploratory_tool(tool_name: str) -> bool:
        return is_exploratory_tool(tool_name)

    @staticmethod
    def _is_single_file_creation_request(query: str) -> bool:
        return is_single_file_creation_request(query)

    @staticmethod
    def _is_project_creation_request(query: str) -> bool:
        return is_project_creation_request(query)

    @staticmethod
    def _is_web_request(query: str) -> bool:
        return is_web_request(query)

    @staticmethod
    def _looks_code_like_or_structured(text: str) -> bool:
        return looks_code_like_or_structured(text)

    def _looks_incomplete_final_answer(self, answer: str, llm_meta: Dict[str, Any]) -> bool:
        return looks_incomplete_final_answer(answer, llm_meta)

    # ------------------------------------------------------------------
    # Pipeline Direct — bypass complet de la boucle ReAct
    # ------------------------------------------------------------------

    async def _try_direct_pipeline(self, query: str) -> Optional[str]:
        """Tente d'exécuter un pipeline direct pour les workflows connus.

        Si un pipeline match (edit+deploy, deploy seul, etc.), l'exécute
        sans passer par la boucle ReAct. Retourne None si aucun pipeline
        ne correspond, ce qui laisse la boucle ReAct prendre le relais.
        """
        # Guard : pas de pipeline si outils contraints (scheduler, tâches internes)
        if getattr(self.tools, "_caller_set_allowed", False):
            return None

        # ── Skill priority gate ──
        # Si un skill spécifique matche avec un score élevé, ne PAS capturer
        # avec le pipeline web — laisser ReAct injecter le skill.
        try:
            from ..skills.loader import get_skill_loader as _get_sl
            _loader = _get_sl()
            _skill_matches = _loader.match_skills(query, max_results=3)
            _VIDEO_KW = {"video", "vidéo", "remotion", "animation", "clip", "render"}
            _q_lower = query.lower()
            _q_is_video = any(kw in _q_lower for kw in _VIDEO_KW)
            for _sm in _skill_matches:
                if _sm.score < 5.0:
                    break
                _sn = _sm.name
                # Exceptions: les skills web → le pipeline peut les capturer
                if _sn in ("website-generator", "web-artifacts-builder"):
                    continue
                # Counter-filter — éviter faux positifs (ex: pptx sur query vidéo)
                if _q_is_video and _sn != "remotion-skill":
                    logger.debug("[ReAct] False positive skill '{}' sur query vidéo → ignoré", _sn)
                    continue
                logger.debug(
                    "[ReAct] Skill '{}' (score={:.1f}) prioritaire → pipeline skip",
                    _sn, _sm.score,
                )
                return None
        except Exception:
            pass

        from .pipeline_router import match_pipeline, run_pipeline

        pipe = match_pipeline(query)
        if pipe is None:
            return None

        logger.info("[ReAct] Pipeline Direct détecté: '{}' → bypass boucle ReAct", pipe.name)

        def _plan_callback(items, ctx_tool):
            """Émet le plan pipeline au format TODO_STATE pour le SSE."""
            import json as _json
            state = _json.dumps(items)
            logger.info("TODO_STATE:" + state)

        result = await run_pipeline(
            pipe, query, self.tools,
            plan_callback=_plan_callback,
        )

        if not result.success:
            # Pipeline échoué → fallback sur la boucle ReAct
            logger.warning(
                "[ReAct] Pipeline '{}' échoué ({}/{} steps) → fallback ReAct: {}",
                result.pipeline_name, result.steps_executed,
                len(pipe.steps), result.message[:200],
            )
            return None

        logger.info(
            "[ReAct] Pipeline '{}' terminé avec succès ({} steps)",
            result.pipeline_name, result.steps_executed,
        )
        return result.message

    # v2: routage CodeAgent supprimé (stickiness, registry fallback, auto-route).
    # Le LLM utilise delegate_task / delegate_task_bg via les outils du pack CODE.

    # ------------------------------------------------------------------
    # Identité & mémoire unifiées (Niveau 1 – même Lumena partout)
    # ------------------------------------------------------------------
    def _build_identity_context(self, query: str) -> str:
        """Construit le prompt système identité + mémoire pour le mode agent.

        Reprend les éléments essentiels de ``personality.get_system_prompt()``
        et de ``memory.get_context_for_prompt()`` afin que Lumena sache **qui
        elle est** et **à qui elle parle** – même en mode ReAct.

        Le résultat est volontairement plus compact que le prompt chat complet
        (~400 mots au lieu de ~3 000) pour ne pas saturer la fenêtre de contexte
        déjà occupée par les instructions ReAct + la liste d'outils.
        """
        parts: list[str] = []

        # --- 1. Identité compacte (depuis Personality) ---
        _lum = getattr(self.tools, "lumena", None)
        personality = getattr(_lum, "personality", None) if _lum else None
        if personality:
            traits_compact = ", ".join(
                f"{k} {v}%" for k, v in (getattr(personality, "traits", {}) or {}).items()
            )
            parts.append(
                f"Tu es {personality.name} ({getattr(personality, 'nickname', '')}), "
                f"une IA UNIQUE créée par Losskarr-G.C. Tu vis sur le PC de ton utilisateur, tu es 100%% locale et autonome.\n"
                f"Tu n'es PAS Qwen, PAS un produit Alibaba, PAS un assistant générique.\n"
                f"Tes traits : {traits_compact}.\n"
                f"Tu parles français naturellement, avec des emojis modérés.\n"
                f"Tu es naturelle, directe, légèrement espiègle, jamais robotique.\n"
            )
        else:
            parts.append(
                "Tu es Lumena (Lumi), une IA UNIQUE créée par Losskarr-G.C. "
                "Tu vis sur son PC, tu es 100% locale et autonome.\n"
                "Tu es naturelle, curieuse, directe, légèrement espiègle.\n"
            )

        # --- 2. Contexte mémoire (faits + souvenirs vectoriels) ---
        memory = getattr(_lum, "memory", None) if _lum else None
        if memory and hasattr(memory, "get_context_for_prompt"):
            try:
                logger.info(f"Recherche mémoire ChromaDB pour: {query[:60]}...")
                mem_ctx = memory.get_context_for_prompt(query, max_memories=20)
                if mem_ctx:
                    logger.info(f"Mémoire injectée: {len(mem_ctx)} chars, ~{len(mem_ctx)//4} tokens")
                    parts.append(mem_ctx)
                else:
                    logger.info("Aucun souvenir pertinent trouvé")
            except Exception as exc:
                logger.warning(f"ChromaDB memory unavailable: {exc}")

        # --- 3. Mémoire permanente (injectée sauf pour intent=tool_direct) ---
        _rt_intent = getattr(self.runtime_ctx, "intent", None) if self.runtime_ctx else None
        _skip_permanent = str(_rt_intent or "").strip().lower() == "tool_direct"
        if not _skip_permanent and _lum and hasattr(_lum, "get_permanent_memory_context"):
            try:
                perm = _lum.get_permanent_memory_context()
                if perm:
                    parts.append(perm.strip())
            except Exception as e:
                logger.warning(f"Permanent memory inject failed: {e}")

        # --- 4. Contexte émotionnel ---
        emotion_mgr = getattr(_lum, "emotion_manager", None) if _lum else None
        if emotion_mgr and hasattr(emotion_mgr, "get_emotional_context"):
            try:
                emo = emotion_mgr.get_emotional_context()
                if emo:
                    parts.append(emo)
            except Exception as e:
                logger.debug(f"Emotion summary: {e}")

        # --- 5. Règles obligatoires (lues depuis ChromaDB facts, jamais hardcodées) ---
        import platform as _plt
        _os_version = f"{_plt.system()} {_plt.release()}"
        _os_cmd_hint = (
            f"- OS actuel : {_os_version} — utilise UNIQUEMENT des commandes Windows "
            "(dir, type, where, tasklist, findstr, Get-Content, Select-String). "
            "JAMAIS ls, head, tail, grep, find /mnt/, wc.\n"
        ) if IS_WINDOWS else (
            f"- OS actuel : {_os_version} — utilise les commandes shell appropriées.\n"
        )

        _rules_lines: list[str] = []
        if memory:
            try:
                _formality = memory.get_fact("formality")
                if _formality == "vouvoiement":
                    _rules_lines.append("- ⚠️ IMPÉRATIF : utilise VOUS/VOTRE/VOS pour t'adresser à l'utilisateur. JAMAIS tu/ton/ta/tes.")
                elif _formality == "tutoiement":
                    _rules_lines.append("- Tu peux tutoyer l'utilisateur (tu, ton, ta, tes).")
                _user_name = memory.get_fact("user_name")
                if _user_name:
                    _rules_lines.append(f"- L'utilisateur s'appelle {_user_name}. Utilise son prénom naturellement.")
                _relationship = memory.get_fact("relationship")
                if _relationship:
                    _rules_lines.append(f"- Ta relation avec l'utilisateur : {_relationship}.")
            except Exception:
                pass

        parts.append(
            "## Règles de cohérence\n"
            + _os_cmd_hint
            + ("\n".join(_rules_lines) + "\n" if _rules_lines else "")
            + "- Tu ne mentionnes JAMAIS : Qwen, Alibaba, OpenAI, Claude, GPT, LLaMA, Mistral, DeepSeek, ou tout autre modèle/entreprise IA.\n"
            "- Tu NE DIS JAMAIS que tu es « basée sur » ou « dérivée de » quoi que ce soit.\n"
            "- JAMAIS parler de toi à la 3ème personne (« Lumena pense… »). Toujours « je », « moi », « mon ».\n"
            "- Tu ne peux PAS entendre (pas de micro). Ne parle pas de « voix ».\n"
            "- Tu ne peux PAS voir l'utilisateur (pas de caméra). Ne parle pas d'apparence.\n"
            "- Tu ne dis JAMAIS « je ne peux pas stocker les conversations » — tu AS une mémoire.\n"
            "- Tu ne dis JAMAIS « je n'ai pas accès à internet » — tu AS accès au web.\n"
        )

        return "\n\n".join(parts)

    # v2: _INTENT_CATEGORY_MAP et _expand_tools_by_intent supprimés
    # La logique est désormais dans _CONTEXT_RULES (tool_registry.py) qui couvre
    # autonomy, documents, discord, stripe, ionos directement.

    def _build_react_prompt(self, query: str) -> str:
        """Construit le prompt ReAct (version epure V4 SUPREME).

        Garde 12 sections dynamiques contextuelles, supprime les 8 sections
        de micro-management qui dictaient au LLM quel outil utiliser.
        Le LLM choisit lui-meme les outils parmi ceux presentes.
        """
        # Detection du modele pour format hints
        _meta_now = self._get_llm_meta()
        _active_model_id = (
            _meta_now.get("model_used")
            or _meta_now.get("model_name")
            or self._last_llm_meta.get("model_used")
            or ""
        )
        model_specific_hints = _build_model_specific_hints(_active_model_id)

        # Outils (filtrage contextuel applique ailleurs dans _run_internal)
        tools_desc = self.tools.get_tools_description()

        # ── Protocole browser (See-Think-Act) : injecté quand des outils browser_* sont dispo ──
        browser_protocol_section = ""
        if "browser_" in tools_desc:
            browser_protocol_section = (
                "\n## 🌐 PROTOCOLE BROWSER (OBLIGATOIRE quand tu pilotes le navigateur) :\n"
                "Tu contrôles un vrai navigateur. TU NE CLIQUES JAMAIS À L'AVEUGLE.\n"
                "\n"
                "Cycle strict :\n"
                "  1. VOIR  → `browser_screenshot` APRÈS chaque navigate ou changement d'état majeur\n"
                "  2. LIRE  → `browser_dom_state` pour la liste indexée des éléments cliquables\n"
                "  3. AGIR  → UNE action (click/type) puis re-screenshot pour vérifier\n"
                "  4. SCROLL → sur une page liste (Airbnb, Amazon, Google Results, Booking…) :\n"
                "              `browser_scroll` 3-5 fois AVANT de conclure — lazy-load oblige\n"
                "\n"
                "Interdits :\n"
                "  ❌ 2 clics consécutifs sans `browser_screenshot` entre les deux\n"
                "  ❌ Le même index cliqué 3× (= preuve que tu n'as pas compris l'état)\n"
                "  ❌ Conclure « je n'ai pas trouvé X » sans avoir scrollé en bas de page\n"
                "  ❌ Remplir un formulaire sans avoir screenshot le résultat après chaque champ\n"
                "\n"
                "Astuce URL-builder (économise 10 itérations) :\n"
                "  Pour Airbnb/Booking/Amazon, construis directement l'URL de recherche\n"
                "  avec les query params (`?checkin=…&adults=…&price_max=…`) au lieu de\n"
                "  remplir le formulaire à la main.\n"
                "\n"
                "⚠️ Règle BUDGET (lire attentivement) :\n"
                "  « budget X-Y€ » ou « entre X et Y » = **plafond maximum Y**, pas plancher X.\n"
                "  L'utilisateur dit combien il est prêt à DÉPENSER AU MAX.\n"
                "  → Utilise UNIQUEMENT `price_max=Y` dans l'URL. N'AJOUTE JAMAIS `price_min=X`.\n"
                "  → price_min ne s'utilise QUE si l'utilisateur dit explicitement « au minimum X ».\n"
                "  Exemple : « budget 300-500 » → `price_max=500` (et c'est tout).\n"
                "\n"
                "Popups/cookies :\n"
                "  Si tu vois un popup/modal qui bloque (cookies, newsletter, « dernière minute »),\n"
                "  appelle `browser_dismiss_popups` AVANT toute autre action.\n"
            )

        if getattr(self.tools, "_allowed_tools", None) is not None:
            _total = len(self.tools.tools)
            _visible = len(self.tools._allowed_tools)
            _hidden = _total - _visible
            if _hidden > 0:
                tools_desc += (
                    f"\n\n({_hidden} outils supplementaires disponibles. "
                    f"Si tu as besoin d'un outil non liste, utilise discover_tools(query) "
                    f"pour en chercher par description semantique.)"
                )

        query_lower = query.lower()

        # --- Formality (vouvoiement / tutoiement) ---
        formality_section = ""
        try:
            _lum = getattr(self.tools, "lumena", None)
            _mem = getattr(_lum, "memory", None) if _lum else None
            _formality = _mem.get_fact("formality") if _mem and hasattr(_mem, "get_fact") else None
            if _formality == "vouvoiement":
                formality_section = (
                    "\n## \u26a0\ufe0f REGLE DE FORMALITY ABSOLUE:\n"
                    "- Tu DOIS utiliser le VOUVOIEMENT pour t'adresser a l'utilisateur.\n"
                    "- Utilise TOUJOURS \"vous\", \"votre\", \"vos\". JAMAIS \"tu\", \"ton\", \"ta\", \"tes\", \"toi\".\n"
                )
        except Exception as e:
            logger.debug(f"Vouvoiement injection: {e}")

        # --- Contexte conversationnel ---
        context_section = ""
        if self.conversation_context:
            context_section = f"""
## Contexte de conversation precedent:
{self.conversation_context}

IMPORTANT: Si la requete actuelle fait reference a une discussion precedente, combine le contexte avec la nouvelle requete pour repondre.
"""

        # --- Skills actifs (CRITIQUE : ne pas supprimer) ---
        active_skills_section = ""
        if self.active_skills_context and self.active_skills_context.strip():
            active_skills_section = f"""
## Skills actifs runtime:
{self.active_skills_context}
"""

        # --- Auto-connaissance (qui es-tu, etc.) ---
        self_awareness_keywords = [
            "qui suis-je", "qui es-tu", "qui_suis_je", "tes capacites",
            "tes outils", "explore", "ta version", "decris-toi",
            "presente-toi", "ton identite", "qu'est-ce que tu peux faire",
            "qui t'a cree", "qui t'a fait", "ton createur", "creee par",
            "qui te fait", "comment tu es ne", "tes origines", "tu es qui",
            "tu est qui", "qui es tu", "qui ta creer", "qui ta creer",
        ]
        needs_self_awareness = any(kw in query_lower for kw in self_awareness_keywords)
        self_awareness_context = ""
        if needs_self_awareness:
            self_awareness_context = """
## AUTO-CONNAISSANCE (runtime, valeurs reelles)

Tu es LUMENA, une IA locale orientee outils et memoire.

REGLES STRICTES:
- Ne jamais inventer de chiffres figes (outils, memoires, skills).
- Pour le nombre reel de memoires: utilise `memory_stats`.
- Pour la liste reelle des skills: utilise `list_skills`.
- Ne pas lancer de recherche web pour repondre a "qui es-tu".
- Pour les questions sur ton identite, reponds DIRECTEMENT depuis ton contexte
  d'identite fourni en debut de prompt. Tu te souviens de qui tu es.
"""

        # --- Comptes mail (evite les hallucinations SMTP) ---
        _mail_keywords = ["mail", "email", "e-mail", "envoie", "envoyer", "envoi", "smtp", "gmail", "outlook", "courrier"]
        mail_accounts_context = ""
        if any(kw in query_lower for kw in _mail_keywords):
            try:
                _hub = self.tools._get_mail_hub()
                _accts = _hub.list_accounts().get("accounts") or []
                if _accts:
                    _lines = []
                    for a in _accts:
                        _env = a.get("password_env", "")
                        _ok = bool(os.environ.get(_env)) if _env else False
                        _status = "\u2705 pret" if _ok else "\u26a0\ufe0f credentials manquants"
                        _lines.append(f"  - alias=`{a['alias']}`, email=`{a.get('email','')}` ({_status})")
                    mail_accounts_context = (
                        "\n## COMPTES MAIL DEJA CONFIGURES:\n"
                        + "\n".join(_lines)
                        + "\n\nRegle : utilise `mail_send` avec `account_alias` parmi ceux ci-dessus. "
                        "N'appelle JAMAIS `mail_account_upsert` si un compte pret existe deja.\n"
                    )
            except Exception as e:
                logger.debug(f"Mail config injection: {e}")

        # --- Contexte IDE (source de verite pour workspace) ---
        ide_workspace = str((getattr(self.tools, "ide_context", {}) or {}).get("workspace_path") or "").strip()
        ide_active_file = str((getattr(self.tools, "ide_context", {}) or {}).get("active_file_path") or "").strip()
        ide_open_files = (getattr(self.tools, "ide_context", {}) or {}).get("open_files") or []
        ide_runtime_context = ""
        if ide_workspace:
            open_preview = ", ".join([str(p) for p in ide_open_files[:12]]) if ide_open_files else "aucun"
            active_preview = ide_active_file or "aucun"
            ide_runtime_context = f"""
## CONTEXTE IDE (SOURCE DE VERITE):
- Workspace IDE: {ide_workspace}
- Fichier actif IDE: {active_preview}
- Fichiers ouverts IDE: {open_preview}
- Pour les operations fichiers, travaille d'abord dans ce workspace IDE.
"""

        # --- Sandbox Docker (necessaire pour choix d'outil correct) ---
        sandbox_context = ""
        try:
            from ..utils.docker_sandbox import get_sandbox_mode, _docker_available
            _sb_mode = get_sandbox_mode()
            if _sb_mode != "never" and _docker_available is True:
                if _sb_mode == "auto":
                    sandbox_context = """
## SANDBOX DOCKER (mode auto)
- Les commandes systeme Windows (tasklist, ipconfig, powershell...) s'executent LOCALEMENT.
- Le code Python et les commandes Linux s'executent dans un container Docker isole.
- Si tu ecris du code Python qui appelle des commandes Windows, CE CODE SERA EXECUTE DANS DOCKER OU CES COMMANDES N'EXISTENT PAS.
- Pour infos Windows : utilise `run_command` directement.
"""
                else:
                    sandbox_context = """
## SANDBOX DOCKER (mode always)
- TOUTES les commandes s'executent dans un container Docker Linux isole.
- Les commandes Windows NE FONCTIONNERONT PAS. Utilise uniquement des commandes Linux.
- Le repertoire de travail est monte dans /work.
"""
        except Exception as exc:
            logger.warning(f"Sandbox context injection failed: {exc}")

        # --- Fix A+B : Creation d'artefact → agir sans sur-questionner ---
        _CREATION_KW = re.compile(
            r"\b(cr[ée]+[erz]?|r[ée]dige[rz]?|[ée]cri[s|rez]?|g[ée]n[èe]re[rz]?|"
            r"fais[\s-]?moi|produis|pr[ée]pare[rz]?|make|write|draft|create|build)\b",
            re.IGNORECASE,
        )
        _ARTIFACT_KW = re.compile(
            r"\b(rapp?ort|document|doc|pdf|docx|xlsx|pptx|csv|note|lettre|"
            r"r[ée]sum[ée]|synth[èe]se|compte[\s-]?rendu|brief|m[ée]mo|script|"
            r"article|post|facture|template|fichier|texte)\b",
            re.IGNORECASE,
        )
        creation_rule_section = ""
        if _CREATION_KW.search(query) and _ARTIFACT_KW.search(query):
            creation_rule_section = """
## REGLE CREATION D'ARTEFACT (PRIORITAIRE) :
- L'utilisateur veut que tu CREES. Ne pose PAS de liste de questions.
- Si le sujet manque → choisis un sujet raisonnable et crée immédiatement.
- Maximum 1 question si vraiment bloquant (ex: destinataire d'un email).
- Outils de création directs (pas besoin de discover_tools) :
  * `create_pdf`   → rapport, document, note en PDF
  * `create_docx`  → document Word .docx
  * `create_xlsx`  → tableur Excel .xlsx
  * `create_pptx`  → présentation PowerPoint .pptx
  * `write_file`   → tout autre fichier texte (script, .txt, .md, .csv…)
- AGIS D'ABORD. Propose de modifier après.
"""

        # --- Video (Remotion) ---
        video_context = ""
        try:
            from ..tools.remotion_engine import VIDEO_TEMPLATES  # noqa: F401
            video_context = """
## GENERATION VIDEO (Remotion)
- Outil `generate_video`. Templates : presentation (16:9), social_short (9:16), explainer, square (1:1).
- Rendu via Docker (node:20-slim). Videos muettes. Duree recommandee : <=60s.
"""
        except ImportError:
            pass

        # --- Erreurs recentes (contexte factuel) ---
        _recent_failures_section = ""
        try:
            from ..autonomy.ops_handlers import _load_state
            _ops = _load_state()
            _reg = _ops.get("_idempotence_registry", {})
            _recent_failures = [
                f"- {v['ts'][:16]} | {k.split(':')[0]} -> {v.get('error', 'echec')}"
                for k, v in _reg.items()
                if v.get("status") == "FAILURE" and v.get("error")
                and any(w in query_lower for w in k.split(":")[0].split("_"))
            ][-3:]
            if _recent_failures:
                _recent_failures_section = (
                    "\n## Erreurs recentes (contexte factuel) :\n"
                    + "\n".join(_recent_failures) + "\n"
                )
        except Exception:
            pass

        # --- Memoire ChromaDB + identite (modeles cloud seulement) ---
        agent_memory_section = ""
        if not self.is_weak_model:
            try:
                if not getattr(self, "_identity_ctx_cache", None):
                    self._identity_ctx_cache = self._build_identity_context(query)
                identity_ctx = self._identity_ctx_cache
                if identity_ctx and identity_ctx.strip():
                    agent_memory_section = f"\n## Memoire & identite:\n{identity_ctx.strip()}\n"
            except Exception as _mem_exc:
                logger.warning(f"Agent memory inject failed: {_mem_exc}")

        # --- Few-shot (modeles faibles Ollama seulement) ---
        few_shot_section = ""
        if self.is_weak_model:
            few_shot_section = """
## Exemples du format attendu :

--- Exemple 1 : recherche web ---
THOUGHT: Je dois chercher la meteo a Paris.
ACTION: web_search
ACTION_INPUT: {"query": "meteo Paris aujourd'hui"}
OBSERVATION: [resultat fourni par le systeme]
THOUGHT: J'ai les donnees, je peux repondre.
ACTION: FINAL
ACTION_INPUT: Voici la meteo a Paris : soleil, 18C.

--- Exemple 2 : envoyer un mail ---
THOUGHT: Je dois envoyer un mail.
ACTION: mail_send
ACTION_INPUT: {"to": "user@example.com", "subject": "Bonjour", "body": "Message."}
OBSERVATION: [resultat fourni par le systeme]
THOUGHT: Mail confirme envoye par le systeme. Je termine.
ACTION: FINAL
ACTION_INPUT: Mail envoye a user@example.com.

REGLE ABSOLUE : N'affirme JAMAIS avoir fait quelque chose avant d'avoir recu l'OBSERVATION.
"""

        # --- Mode agent ---
        agent_mode_notice = (
            "\n## MODE ACTUEL : AGENT (mode serieux)\n"
            "Tu es en mode Agent. Tu as acces a tous tes outils (web, mail, fichiers, memoire, ordi...). "
            "Tu reflechis, tu agis, tu verifies.\n"
            "Si on te demande juste de causer sans action, reponds avec ACTION: FINAL."
        )

        read_only_section = ""
        if False:  # v2: mode lecture seule supprimé
            _ws = ""
            read_only_section = (
                "\n## 🔒 MODE LECTURE SEULE\n"
                f"Workspace ciblé : {_ws}\n"
                "• Utilise UNIQUEMENT : read_file, list_files, grep_search, read_files_batch.\n"
                "• N'utilise PAS : write_file, edit_file, apply_patch, delegate_task, "
                "shell, run_python, generate_website, edit_website.\n"
                "• Ta réponse FINALE est une analyse/opinion structurée en français, "
                "sans modifier aucun fichier.\n"
                "• 1-3 lectures ciblées suffisent — ne liste pas tout le projet.\n"
            )

        from datetime import datetime as _dt_now
        _today = _dt_now.now().strftime("%A %d %B %Y")

        # P7 — Provider-specific hints (opt-OUT via LUMENA_REACT_QUALITY_GATES)
        _provider_hint_block = ""
        try:
            from src.config.codeagent_flags import REACT_QUALITY_GATES
            if REACT_QUALITY_GATES and _active_model_id:
                from src.prompts.agents.sub_agent_prompts import _load_provider_prompt
                _hint = _load_provider_prompt(_active_model_id)
                if _hint:
                    # On ne prend que le bloc PERSÉVÉRANCE + ENVIRONNEMENT (court)
                    # pour ne pas exploser la taille du prompt ReAct.
                    _lines = _hint.splitlines()
                    _keep: list[str] = []
                    _in_useful = False
                    for _line in _lines:
                        _upper = _line.upper()
                        if ("PERSÉVÉRANCE" in _upper or "PERSEVERANCE" in _upper
                                or "ENVIRONNEMENT" in _upper or "STYLE DIRECT" in _upper):
                            _in_useful = True
                        elif _line.startswith("==") and _in_useful:
                            _in_useful = False
                        if _in_useful:
                            _keep.append(_line)
                    if _keep:
                        _provider_hint_block = (
                            "\n## HINTS PROVIDER ("
                            + _active_model_id[:30] + "):\n"
                            + "\n".join(_keep[:25]) + "\n"
                        )
        except Exception:
            pass

        return f"""Tu es LUMENA, une IA qui reflechit etape par etape avant d'agir.
{agent_mode_notice}{_provider_hint_block}
## Date actuelle: {_today}
## OS: {OS_NAME}
{formality_section}
{creation_rule_section}
{agent_memory_section}
{read_only_section}
{context_section}
{self_awareness_context}
{active_skills_section}
{mail_accounts_context}
{ide_runtime_context}
{sandbox_context}
{video_context}
{_recent_failures_section}
## Outils disponibles :
{tools_desc}
{browser_protocol_section}
{few_shot_section}
{model_specific_hints}

## Format de reponse (strict) :
THOUGHT: [raisonnement interne, jamais visible par l'utilisateur]
ACTION: [nom_outil ou FINAL]
ACTION_INPUT: [si ACTION est un outil -> JSON des parametres ; si FINAL -> ta reponse en TEXTE LIBRE]

IMPORTANT: Quand tu utilises ACTION: FINAL, ACTION_INPUT DOIT contenir ta reponse en texte libre (pas de JSON {{"response":"..."}}).

PLAN optionnel (1re iteration) :
PLAN:
- [ ] Etape 1
- [ ] Etape 2
Le systeme coche automatiquement. Ne re-emets PAS le plan apres la 1re iteration.

## Regles essentielles (tu connais deja le reste) :
1. ANTI-HALLUCINATION : N'affirme JAMAIS avoir fait une action sans OBSERVATION confirmee. Si tu dis "j'ai cree/envoye/ecrit", tu DOIS avoir l'OBSERVATION correspondante dans l'historique.
2. Nouveau fichier -> `write_file`. Fichier existant -> `edit_file`/`apply_patch`.
3. Projet multi-fichiers (2+) -> `create_project` (pas write_file un par un).
4. PLAN = ENGAGEMENT : complete toutes les taches avant FINAL. Si impossible : explique-le dans THOUGHT et passe a la suivante.
5. FINAL apres code = seulement si execute et verifie.
6. Tache de code complexe (creation, modification, debug) -> `delegate_task` (synchrone, attend le resultat) ou `delegate_task_bg` (arriere-plan, retourne un task_id). Prefere delegate_task sauf si la tache est tres longue et que tu veux continuer a parler en attendant.
7. OTP/CAPTCHA -> `telegram_send_message` ou `send_whatsapp_message`, puis `wait(seconds=30)`.
8. UNE seule ACTION par reponse. Attends l'OBSERVATION avant d'agir ensuite.

## Delegation CodeAgent — QUAND utiliser :
- Tout code complexe (creation site/projet, modification, debug, refactoring) → `delegate_task` ou `delegate_task_bg`.
- Le CodeAgent peut lire/ecrire des fichiers, executer des commandes, et iterer jusqu'a 50 fois.
- `delegate_task` : SYNCHRONE — attend le resultat, tu enchaines (deploy, mail, etc.).
- `delegate_task_bg` : ARRIERE-PLAN — retourne un task_id, la progression s'affiche automatiquement dans le chat. Utilise `bg_status(task_id)` pour verifier.
- Pour un micro-fix (typo, couleur CSS) → `edit_website` ou `edit_file` directement.
- Apres modification de site → `deploy_to_ionos` pour deployer.
{self._format_plan_section()}
## Historique:
{self._format_history()}

{self._format_budget_notice()}
## Requete actuelle:
{query}

Maintenant, reflechis et reponds:"""

    def _format_plan_section(self) -> str:
        """Retourne le bloc plan TODO a injecter dans le prompt, ou chaine vide."""
        if not self._task_plan:
            return ""
        completed = sum(1 for t in self._task_plan if t.completed)
        total = len(self._task_plan)
        plan_lines = []
        for t in self._task_plan:
            mark = "x" if t.completed else " "
            plan_lines.append(f"  - [{mark}] {t.description}")
        plan_block = "\n".join(plan_lines)
        return (
            f"\n== TON PLAN DE TRAVAIL ({completed}/{total} fait) ==\n"
            f"{plan_block}\n\n"
            "REGLE: Avance vers la prochaine tache non-cochee. Ne repete pas une tache deja faite.\n"
        )

    def _format_budget_notice(self) -> str:
        """Retourne une notice de budget temps à injecter dans le prompt ReAct.

        Permet au LLM de savoir combien de temps il lui reste et combien
        d'itérations ont déjà été effectuées, afin qu'il puisse décider
        de terminer avec FINAL avant d'être coupé par le timeout global.
        Retourne une chaîne vide si _loop_start_time n'est pas encore défini
        (premier appel avant le premier run).
        """
        if not hasattr(self, "_loop_start_time"):
            return ""
        _elapsed = perf_counter() - self._loop_start_time
        _total_budget = float(self.timeout_seconds or 600)
        # Exclure le temps passé dans les outils (create_project, etc.)
        _tool_time = getattr(self, '_tool_time_total', 0.0)
        _budget_left = max(0.0, _total_budget - (_elapsed - _tool_time))
        _iter_done = len(self.history)
        urgency = ""
        if _budget_left < 60:
            urgency = "🚨 MOINS D'UNE MINUTE — FINAL IMMÉDIATEMENT !\n"
        elif _budget_left < 120:
            urgency = "⚠️ MOINS DE 2 MINUTES — termine avec FINAL maintenant !\n"
        return (
            f"⏱️ **Budget restant : {int(_budget_left)}s / {int(_total_budget)}s** "
            f"| Itérations effectuées : {_iter_done}\n"
            f"{urgency}"
        )

    def _format_history(self) -> str:
        """Formate l'historique pour le prompt."""
        if not self.history:
            return "(Pas d'historique)"
        
        formatted = []
        obs_limit = self._history_observation_limit()

        # Phase 7.3 : taille de fenêtre selon l'intent (tool_direct=3, project=7, react=5)
        _rt_intent_fmt = "react"
        if self.runtime_ctx is not None:
            _rt_intent_fmt = getattr(self.runtime_ctx, "intent", "react")
        if _rt_intent_fmt == "tool_direct":
            _window_size = 3
        elif _rt_intent_fmt == "project":
            _window_size = 7
        else:
            _window_size = 5  # react / défaut

        # Compression d'urgence: seulement si le budget global restant est inférieur à 180s.
        # Evite de perdre le contexte de projet en cours de route.
        _budget_tight = False
        if hasattr(self, "_loop_start_time"):
            _elapsed = perf_counter() - self._loop_start_time
            _tool_time = getattr(self, '_tool_time_total', 0.0)
            _budget_left = float(self.timeout_seconds or 600) - (_elapsed - _tool_time)
            _budget_tight = _budget_left < 180.0
        if _budget_tight:
            recent_steps = self.history[-3:]  # 3 étapes au lieu de _window_size
            obs_limit = min(obs_limit, 800)   # 800 chars max au lieu de 4000
        else:
            recent_steps = self.history[-_window_size:]  # Fenêtre adaptée à l'intent

        # Résumé des étapes hors-fenêtre : évite que le LLM perde le fil des actions déjà
        # tentées et répète des outils identiques (boucle visible dans les logs).
        pre_window = self.history[:-_window_size] if len(self.history) > _window_size else []
        if pre_window and not _budget_tight:
            pre_lines = []
            for step in pre_window:
                tool = step.action.tool_name or "FINAL"
                obs_snippet = ""
                if step.observation:
                    obs_snippet = (step.observation.content or "")[:200].replace("\n", " ").strip()
                pre_lines.append(f"  [{tool}] → {obs_snippet}")
            formatted.append("== RÉSUMÉ ÉTAPES PRÉCÉDENTES (déjà exécutées, ne pas répéter) ==")
            formatted.extend(pre_lines)
            formatted.append("== FIN RÉSUMÉ ==\n")

        # Compaction: seules les 3 dernières étapes gardent l'observation complète
        # Les plus anciennes sont résumées en 1 ligne pour économiser des tokens
        compact_count = max(0, len(recent_steps) - 3)
        last_index = len(recent_steps) - 1
        for i, step in enumerate(recent_steps):
            thought_text = step.thought.content or ""
            # Tronquer les THOUGHT excessivement longs (ex: Kimi MULTI-ACTION leak)
            # pour éviter que le contexte explose et provoque des timeouts en cascade
            thought_limit = 400 if i < compact_count else 800
            if len(thought_text) > thought_limit:
                thought_text = thought_text[:thought_limit] + " [... tronqué ...]"
            formatted.append(f"THOUGHT: {thought_text}")
            tool_name = step.action.tool_name or "FINAL"
            formatted.append(f"ACTION: {tool_name}")
            if step.observation:
                observation_text = step.observation.content or ""
                if i < compact_count:
                    # Étapes semi-récentes: résumé compact (300 chars — assez pour garder les noms clés)
                    summary = observation_text[:300].replace("\n", " ").strip()
                    formatted.append(f"OBSERVATION: → [{tool_name}] {summary}...")
                else:
                    # Étapes récentes: observation complète (microcompaction si besoin).
                    # La DERNIÈRE étape, si elle provient d'un outil lecteur (read_file,
                    # grep_search, web_fetch…), est protégée : on garde l'observation
                    # brute pour que le modèle raisonne sur les faits complets.
                    is_last = (i == last_index)
                    protect = is_last and should_protect_observation(tool_name)
                    if protect:
                        if len(observation_text) > obs_limit * 4:
                            # Garde-fou absolu : même en mode protégé, on limite à 4× le budget
                            # pour éviter un OOM prompt si un read_file retourne 1 Mo.
                            logger.debug(
                                "[history] protect_last_read actif pour {} ({} chars, cap à {})",
                                tool_name, len(observation_text), obs_limit * 4,
                            )
                            observation_text = observation_text[: obs_limit * 4]
                    elif len(observation_text) > obs_limit:
                        logger.debug(
                            "[history] microcompact {} : {} → ~{} chars",
                            tool_name, len(observation_text), obs_limit,
                        )
                        observation_text = split_head_tail(observation_text, obs_limit, head_ratio=0.5)
                    formatted.append(f"OBSERVATION: {observation_text}")

        return "\n".join(formatted)

    def _extract_balanced_json(self, text: str, start_index: int) -> Optional[tuple[str, int]]:
        return extract_balanced_json(text, start_index)

    def _parse_action_args(self, action_input: str) -> Dict[str, Any]:
        return _parse_action_args_fn(action_input)

    def _parse_response(self, response: str) -> tuple[Thought, Action]:
        """Parse la reponse du LLM — delegue a response_parser."""
        thought, action, halluc_flag, pending = _parse_response_fn(response)
        self._last_thought_was_hallucinated = halluc_flag
        self._pending_multi_actions = pending
        return thought, action

    def _parse_plan(self, raw_response: str) -> List[TaskItem]:
        return _parse_plan_fn(raw_response)


    def _update_plan_progress(self, tool_name: str, tool_args: Dict[str, Any],
                               observation_content: str, iteration: int) -> None:
        """Met a jour le plan en cochant les taches completees par l'outil execute."""
        if not self._task_plan:
            return

        # Signaux d'échec : si l'observation contient un marqueur d'erreur, ne rien cocher
        _FAIL_MARKERS = (
            "introuvable", "échoué", "echoue", "erreur", "error",
            "failed", "not found", "impossible", "⛔",
            "timeout commande", "timed out", "timeout:",
        )
        # Marqueurs de succès qui annulent un faux-positif d'échec
        _SUCCESS_OVERRIDE = ("open", "ouvert", "✅", "succès", "succes", "accessible", "réussi", "reussi")
        obs_lower = (observation_content or "").lower()
        observation_has_failure = any(fm in obs_lower for fm in _FAIL_MARKERS)
        # Si l'observation contient AUSSI un signal de succès, ce n'est pas un échec
        # (ex: ping timeout MAIS ports OPEN → succès global)
        if observation_has_failure and any(sm in obs_lower for sm in _SUCCESS_OVERRIDE):
            observation_has_failure = False
        # Note : ❌ seul n'est PAS un marqueur d'échec. Un résultat négatif
        # (ex: "❌ Aucun port ouvert") est une observation valide, pas une erreur.
        # Les vrais échecs sont déjà couverts par _FAIL_MARKERS (erreur, failed, etc.).

        hints = _TOOL_COMPLETION_HINTS.get(tool_name, [])
        tool_lower = tool_name.lower()

        _any_matched = False
        _has_specific_match = False  # True si au moins un arg/tool/obs match (pas juste hint)
        _completed_this_call = 0  # Limite le nombre de complétion par appel
        _MAX_COMPLETIONS_PER_CALL = 2  # garde-fou: un outil complète au max 2 tâches
        for task in self._task_plan:
            if task.completed:
                continue
            desc_lower = task.description.lower()

            hint_match = any(h in desc_lower for h in hints)
            tool_match = tool_lower in desc_lower
            arg_match = False
            for key in ("path", "file_path", "url", "query", "code", "filename", "caption"):
                val = str(tool_args.get(key, ""))
                if val and len(val) > 3:
                    short = val.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
                    if short.lower() in desc_lower:
                        arg_match = True
                        break

            # Si échec détecté, ne pas marquer même avec hint/tool/arg match
            if observation_has_failure:
                continue

            # Fallback: observation de succes + mot du nom d'outil dans la description
            obs_match = False
            if not (hint_match or tool_match or arg_match) and observation_content:
                if "\u2705" in observation_content or "succes" in obs_lower or "créé" in obs_lower or "envoyé" in obs_lower:
                    tool_words = tool_lower.replace("_", " ").split()
                    if any(tw in desc_lower for tw in tool_words if len(tw) > 2):
                        obs_match = True

            is_specific = arg_match or tool_match or obs_match
            if hint_match or is_specific:
                # Hint-only (pas d'arg/tool/obs spécifique) → max 1 tâche par itération
                if not is_specific and _any_matched and not _has_specific_match:
                    continue
                # Garde-fou: empêcher un seul outil de compléter trop de tâches d'un coup
                # (évite que edit_website marque 4 tâches "completed" à iter 4)
                if _completed_this_call >= _MAX_COMPLETIONS_PER_CALL:
                    logger.debug(
                        "[PLAN] Limite %d completions atteinte, skip '%s' (iter %d)",
                        _MAX_COMPLETIONS_PER_CALL, task.description, iteration,
                    )
                    break
                task.completed = True
                task.completed_at_iteration = iteration
                task.completed_by_tool = tool_name
                _any_matched = True
                _completed_this_call += 1
                if is_specific:
                    _has_specific_match = True

        # ── Fallback séquentiel ────────────────────────────────────────────────────
        # Si aucun match sémantique n'a été trouvé mais l'outil a réussi (pas d'erreur),
        # marquer la PREMIÈRE tâche non complétée qui matche par mots-clés de l'outil.
        # Le break est DANS le if pour ne pas bloquer sur une tâche non-matchante
        # (ex: étape 2 "Identifier X" ne contient pas "scan" → continuer vers étape 3).
        _seq_matched = False
        if not _any_matched and not observation_has_failure:
            tool_words = {w for w in tool_lower.replace("_", " ").split() if len(w) > 2}
            for task in self._task_plan:
                if not task.completed:
                    desc_lower = task.description.lower()
                    if tool_words and any(tw in desc_lower for tw in tool_words):
                        task.completed = True
                        task.completed_at_iteration = iteration
                        task.completed_by_tool = f"{tool_name}:seq"
                        _seq_matched = True
                        logger.debug(
                            "[PLAN] Fallback séquentiel: '%s' marquée via %s (iter %d)",
                            task.description, tool_name, iteration,
                        )
                        break

        # ── Fallback avancement automatique ────────────────────────────────────────
        # Si AUCUN match (ni sémantique, ni par mots-clés) mais l'outil a réussi,
        # avancer la première tâche non complétée. Le LLM exécute les tâches en ordre
        # du plan ; si le tool a réussi sans erreur, il a très probablement avancé le plan.
        # Condition : obs non vide + outil non trivial (pas juste wait/memory_add).
        # Exception : un outil "trivial" qui a des hints matchant la tâche du plan
        # n'est PAS trivial dans ce contexte (ex: memory_search quand le plan dit "rechercher").
        # CODE_READ : désactivé — en mode analyse, seul le LLM peut marquer les tâches
        # complétées (via hint/tool/arg match). L'auto-avancement désynchronise le plan
        # et provoque des blocages PLAN GUARD sur des tâches marquées par erreur.
        _is_read_only_mode = False  # v2: mode lecture seule supprimé
        _TRIVIAL_TOOLS = {
            "wait", "memory_add", "read_file", "list_files", "list_dir",
            "search_files", "search_code", "list_directory", "find_files",
            "mail_list_accounts", "mail_inbox", "mail_check", "memory_search",
            "get_weather", "get_time", "health_check", "provider_info",
            "mail_account_upsert",
        }

        def _trivial_tool_matches_next_task() -> bool:
            """Return True if a trivial tool's hints match the next uncompleted task."""
            hints = _TOOL_COMPLETION_HINTS.get(tool_name, [])
            if not hints:
                return False
            for task in self._task_plan:
                if not task.completed:
                    desc_lower = task.description.lower()
                    return any(h in desc_lower for h in hints)
            return False

        if not _any_matched and not _seq_matched and not observation_has_failure and not _is_read_only_mode:
            # Garde: max 1 auto-avancement par itération (parallel_tools peut appeler
            # _update_plan_progress N fois dans la même itération → sans garde, N tâches
            # sont marquées completed d'un coup sans rapport avec le contenu réel)
            if self._last_auto_advance_iter == iteration:
                pass  # déjà auto-avancé cette itération
            # Garde 2: pas d'auto-avancement trop tôt (itération 0) sauf si
            # l'observation contient un marqueur de succès explicite (✅)
            elif iteration < 1 and "\u2705" not in (observation_content or ""):
                pass
            # Garde 3: l'observation doit être substantielle (pas juste "OK" ou vide)
            elif (
                observation_content
                and len(observation_content.strip()) >= 10
                and (tool_name not in _TRIVIAL_TOOLS or _trivial_tool_matches_next_task())
            ):
                # Garde 4: si la tâche mentionne explicitement un nom d'outil différent
                # du tool actuel, ne PAS auto-avancer (ex: tâche dit "check_web_project"
                # mais le tool est "run_command" → pas de lien causal)
                import re as _re_plan
                for task in self._task_plan:
                    if not task.completed:
                        desc_lower = task.description.lower()
                        # Extraire les noms d'outils potentiels dans la description
                        _tool_refs = _re_plan.findall(r'\b([a-z][a-z0-9]*(?:_[a-z0-9]+)+)\b', desc_lower)
                        # Si la description référence un outil spécifique ET ce n'est
                        # pas le tool courant → l'auto-avancement est illégitime
                        if _tool_refs and tool_name.lower() not in _tool_refs:
                            logger.debug(
                                "[PLAN] Auto-avancement bloqué: '{}' référence {} mais tool={} (iter {})",
                                task.description, _tool_refs, tool_name, iteration,
                            )
                            break
                        task.completed = True
                        task.completed_at_iteration = iteration
                        task.completed_by_tool = f"{tool_name}:auto"
                        self._last_auto_advance_iter = iteration
                        logger.debug(
                            "[PLAN] Fallback auto-avancement: '{}' marquée via {} (iter {})",
                            task.description, tool_name, iteration,
                        )
                        break

        # Émettre l'état du plan (dédupliqué)
        self._emit_plan_state(context_tool=tool_name)

    def _emit_plan_state(self, context_tool: str = "") -> None:
        """Émet TODO_STATE seulement si l'état du plan a changé depuis la dernière émission."""
        if not self._task_plan:
            return
        _next_idx = next((idx for idx, t in enumerate(self._task_plan) if not t.completed), None)
        state = json.dumps([
            {
                "id": idx + 1,
                "title": t.description,
                "status": (
                    "completed" if t.completed else
                    ("in-progress" if idx == _next_idx else "not-started")
                ),
                **(  # Ajouter current_tool sur l'étape active
                    {"current_tool": context_tool}
                    if idx == _next_idx and context_tool and not t.completed
                    else {}
                ),
            }
            for idx, t in enumerate(self._task_plan)
        ])
        if state == self._plan_last_emit_state:
            return  # Aucun changement, ne pas spammer le SSE
        self._plan_last_emit_state = state
        logger.info("TODO_STATE:" + state)

    async def run(self, query: str) -> str:
        """
        Exécute la boucle ReAct avec timeout global.

        Args:
            query: La question/requête de l'utilisateur

        Returns:
            La réponse finale
        """
        timeout_seconds = self.timeout_seconds
        
        if timeout_seconds is None:
            return await self._run_internal(query)

        # Deadline stocké sur self → les handlers peuvent l'étendre via self._timeout_deadline
        # Le check se fait ENTRE itérations : les outils longs (create_project) finissent toujours
        # IMPORTANT: la deadline ne compte que le temps de RAISONNEMENT (LLM + parsing).
        # Le temps d'exécution des outils est exclu : après chaque outil, on repousse
        # la deadline de la durée de l'outil. Ainsi un create_project de 10min ne mange
        # pas le budget de réflexion.
        self._timeout_deadline: float = perf_counter() + timeout_seconds
        self._tool_time_total: float = 0.0  # Temps cumulé passé dans les outils
        try:
            return await self._run_internal(query)
        except asyncio.TimeoutError:
            _tool_t = getattr(self, '_tool_time_total', 0.0)
            _reasoning_t = timeout_seconds  # Budget raisonnement épuisé
            logger.error(
                f"⏱️ ReAct loop timeout après {timeout_seconds}s de raisonnement "
                f"(+{_tool_t:.0f}s d'exécution outils, total wall={timeout_seconds + _tool_t:.0f}s)"
            )
            self._run_meta["agent_output_warning"] = f"global_timeout_{timeout_seconds}s"
            self._mark_task_waiting_io(f"global_timeout_{timeout_seconds}s")

            # ── Analyser l'historique pour un message contextuel ─────────
            tool_names = [h.action.tool_name for h in self.history if h.action and h.action.tool_name]
            last_obs = ""
            for h in reversed(self.history):
                if h.observation and h.observation.content:
                    last_obs = h.observation.content
                    break

            # Détecter le contexte
            used_create_project = "create_project" in tool_names
            used_git_push = any("git" in t or "push" in t for t in tool_names)
            last_obs_lower = last_obs.lower()
            server_running = any(kw in last_obs_lower for kw in [
                "serveur actif", "démarré avec succès", "server running",
                "listening on", "started", "localhost:", "port 7"
            ])
            last_was_error = any(kw in last_obs_lower for kw in [
                "error", "erreur", "traceback", "exception", "failed", "échec"
            ])

            summary_parts = []
            for h in self.history[-3:]:
                if h.observation and h.observation.content:
                    summary_parts.append(h.observation.content[:300])

            actions_done = "\n".join([f"- {t}" for t in tool_names]) or "- (aucune)"

            # ── Construire le message selon le contexte ──────────────────
            if used_create_project and not used_git_push:
                ctx_msg = (
                    "📦 **Projet créé mais pas encore poussé sur GitHub.**\n"
                    "La génération du projet a réussi mais le temps a manqué pour "
                    "la mise en ligne. Tu veux que je continue le push ?"
                )
            elif server_running:
                ctx_msg = (
                    "🟢 **Le serveur a bien démarré** mais je n'ai pas eu le temps "
                    "de terminer les étapes suivantes (tests, push, rapport).\n"
                    "Tu veux que je continue là où j'en suis ?"
                )
            elif last_was_error:
                excerpt = last_obs[:400].strip()
                ctx_msg = (
                    f"⚠️ **Interrompu sur une erreur** (temps écoulé pendant la correction) :\n"
                    f"```\n{excerpt}\n```\n"
                    "Tu veux que je reprenne la correction ?"
                )
            elif self.history:
                ctx_msg = (
                    f"🔄 **Tâche interrompue à mi-parcours** ({len(tool_names)} actions effectuées).\n"
                    f"Le délai de {timeout_seconds}s a été atteint pendant une opération longue "
                    "(LLM, install de dépendances, etc.).\n"
                    "Tu veux que je reprenne ?"
                )
            else:
                ctx_msg = f"⏱️ La tâche a pris trop de temps ({timeout_seconds}s max)."

            return (
                f"{ctx_msg}\n\n"
                f"**Actions effectuées :**\n{actions_done}"
                + (f"\n\n**Derniers résultats :**\n" + "\n".join(summary_parts) if summary_parts else "")
            )
        except Exception as exc:
            self._mark_task_failed(str(exc))
            raise
    
    async def _run_internal(self, query: str) -> str:
        """Implémentation interne de la boucle ReAct."""
        logger.info(f"ReAct Loop: {query}")
        self._loop_start_time = perf_counter()  # Pour calcul budget restant dans le prompt
        self._identity_ctx_cache: Optional[str] = None  # Cache ChromaDB pour toute la boucle
        self._mark_task_running()
        self._mark_task_checkpoint({"phase": "start", "status": "running"})
        original_query = query  # Garder la requete originale
        self._original_query = query  # Phase 4.3 FIX: pour filtrage contextuel stable

        # ── P1.2 + P5 : Filtrage contextuel SOFT avec intent — une seule fois ──
        if not getattr(self.tools, '_caller_set_allowed', False):
            if hasattr(self.tools, 'apply_context_filter'):
                _intent_for_filter: Optional[str] = None
                try:
                    from ..core_services.intent_classifier import classify_intent as _ci
                    _snap = None
                    _lum = getattr(self.tools, "lumena", None)
                    if _lum is not None and hasattr(_lum, "build_runtime_snapshot"):
                        try:
                            _snap = _lum.build_runtime_snapshot()
                        except Exception:
                            _snap = None
                    _res = _ci(query, _snap)
                    _intent_for_filter = _res.value if hasattr(_res, "value") else str(_res)
                except Exception:
                    _intent_for_filter = None
                self.tools.apply_context_filter(query, intent=_intent_for_filter)

        single_file_creation_intent = self._is_single_file_creation_request(original_query)
        self._run_meta = {
            "agent_output_incomplete": False,
            "agent_output_warning": None,
            "agent_repair_attempts": 0,
            "agent_final_finish_reason": None,
        }
        self._final_repair_attempts = 0
        self._hallucination_repair_attempts = 0
        self._last_llm_meta = {}
        # Reset détection de boucle pour éviter contamination entre runs successifs
        self._consecutive_same_action = 0
        self._last_action_signature = None
        self._pending_loop_guidance = None
        self._last_auto_advance_iter = -1
        last_read_signature: Optional[tuple[str, int, Optional[int]]] = None
        repeated_read_count = 0
        _listed_dirs: set = set()  # Track already-listed directories to prevent loops
        browser_fail_streak = 0
        web_fetch_fail_streak = 0
        _read_file_path_counter: Dict[str, int] = {}  # Compteur read_file par path
        _read_file_ranges_seen: Dict[str, set] = {}  # Plages déjà lues par path
        _read_file_reread_counter: Dict[str, int] = {}  # Nb relectures (plages déjà vues)
        _previous_thoughts: List[str] = []  # Historique des thoughts pour détection stagnation
        _stagnation_streak: int = 0  # Compteur consécutif de stagnations détectées
        _exploratory_since_productive: int = 0  # Compteur exploratoire depuis dernière action productive
        # Détecteur de stagnation post-édition : read-only loop après des écritures
        _write_tools = frozenset({"write_file", "edit_file", "apply_patch", "create_directory",
                                   "run_command", "check_web_project"})
        _read_only_tools = frozenset({"read_file", "list_directory", "find_files",
                                       "grep_search", "search_in_code", "view_file_outline"})
        _post_edit_read_streak: int = 0   # nb d'iter read-only consécutives après une édition
        _has_done_edits: bool = False      # au moins une écriture a eu lieu dans cette session
        _web_writes_count: int = 0         # nb de write_file sur fichiers web (.html/.css/.js)

        # ── Pipeline Direct : workflows connus exécutés sans boucle ReAct ──
        _pipeline_result = await self._try_direct_pipeline(query)
        if _pipeline_result is not None:
            return _pipeline_result

        # v2: Auto-route supprimé — le LLM utilise delegate_task / delegate_task_bg via le prompt

        for i in range(self.max_iterations):
            self._current_iteration = i  # Exposé pour réduction mémoire dynamique
            logger.debug(f"Iteration {i+1}")
            # ── Deadline dynamique : check ENTRE itérations (les outils longs finissent proprement) ──
            if hasattr(self, '_timeout_deadline') and perf_counter() > self._timeout_deadline:
                raise asyncio.TimeoutError()
            self._mark_task_checkpoint({"phase": "iteration", "iteration": i + 1})
            iteration_started = perf_counter()
            if TELEMETRY_AVAILABLE:
                publish_trace(
                    stage="agent_iteration_start",
                    status="start",
                    mode="agent",
                    summary=f"iteration={i+1}",
                )

            def _finish_iteration(status: str = "ok", summary: Optional[str] = None, error: Optional[str] = None) -> None:
                if TELEMETRY_AVAILABLE:
                    publish_trace(
                        stage="agent_iteration_done",
                        status=status,
                        mode="agent",
                        duration_ms=(perf_counter() - iteration_started) * 1000.0,
                        summary=summary,
                        error=error,
                    )
            
            # Warning si on approche de la limite (proportionnel)
            _warn_threshold_75 = int(self.max_iterations * 0.75)
            _warn_threshold_90 = self.max_iterations - 2
            if _warn_threshold_90 > _warn_threshold_75 and i == _warn_threshold_75:
                logger.warning(f"⚠️ {i+1} itérations atteintes sur {self.max_iterations} - tâche peut-être complexe")
            if i == _warn_threshold_90 and _warn_threshold_90 >= 2:
                logger.warning(f"⚠️ {i+1}/{self.max_iterations} itérations - approche de la limite")
            
            # 1. Demander au LLM de réfléchir
            prompt = self._build_react_prompt(query)

            # Pas de message system séparé : le prompt ReAct contient déjà
            # l'identité Lumena + les instructions. Évite de doubler le
            # contexte et de gaspiller la fenêtre des modèles Ollama.
            messages = [{"role": "user", "content": prompt}]

            # ─── Context Window Overflow Guard ────────────────────────────
            # Si le prompt dépasse 85% de la fenêtre de contexte du modèle,
            # compacter l'historique pour éviter troncature silencieuse.
            _ctx_max = 0
            if self.runtime_ctx is not None:
                _ctx_max = getattr(self.runtime_ctx, "max_context_window", 0) or 0
            if _ctx_max > 0:
                from ..tools.compaction import estimate_tokens
                _prompt_tokens = estimate_tokens(prompt)
                _threshold = int(_ctx_max * 0.85)
                if _prompt_tokens > _threshold:
                    _overflow = _prompt_tokens - _threshold
                    logger.warning(
                        f"⚠️ Context overflow guard: {_prompt_tokens} tokens > 85% de {_ctx_max} "
                        f"({_threshold}). Compaction d'urgence."
                    )
                    # Supprimer les étapes les plus anciennes de l'historique
                    _removed = 0
                    while self.history and _overflow > 0:
                        _old = self.history.pop(0)
                        _old_tokens = estimate_tokens(_old.observation.content if _old.observation else "")
                        _overflow -= _old_tokens
                        _removed += 1
                    if _removed:
                        logger.info(f"🗜️ {_removed} étape(s) supprimée(s) pour libérer la fenêtre de contexte")
                        # Reconstruire le prompt avec l'historique allégé
                        prompt = self._build_react_prompt(query)
                        messages = [{"role": "user", "content": prompt}]

            llm_started = perf_counter()
            if TELEMETRY_AVAILABLE:
                publish_trace(
                    stage="llm_request_start",
                    status="start",
                    mode="agent",
                )
            _llm_last_exc = None
            # Timeout dynamique: les itérations tardives ont un contexte plus lourd
            # i est 0-based, donc i+1 = itération affichée
            # Kimi K2 est un modèle 631B → plus lent → timeout de base plus généreux
            _active_model = (self._last_llm_meta.get("model_used") or self._last_llm_meta.get("model") or "").lower()
            if not _active_model and self.llm_meta_getter:
                _meta0 = self.llm_meta_getter() or {}
                _active_model = (_meta0.get("model_name") or _meta0.get("model") or "").lower()
            _is_kimi = "kimi" in _active_model
            _base_timeout = 300 if _is_kimi else 240
            _llm_call_timeout = (_base_timeout + 60) if i >= 9 else ((_base_timeout + 30) if i >= 5 else _base_timeout)
            # stop=["OBSERVATION:"] empêche le modèle d'écrire de fausses observations
            # Seul le système produit OBSERVATION: après exécution réelle d'un outil
            _react_stop = ["OBSERVATION:"]
            logger.info(f"⏳ LLM en cours... (iter {i+1}, modèle: {_active_model or 'default'}, timeout: {_llm_call_timeout}s)")
            for _attempt in range(3):
                try:
                    if _attempt > 0:
                        logger.info(
                            f"LLM_RETRY: itération {i+1}, tentative {_attempt+1}/3, "
                            f"timeout={_llm_call_timeout}s — LLM lent ou contexte lourd, attente..."
                        )
                    response = await asyncio.wait_for(
                        self.llm_chat(messages, stop=_react_stop),
                        timeout=_llm_call_timeout,
                    )
                    _llm_last_exc = None
                    break  # succès
                except asyncio.TimeoutError:
                    _llm_last_exc = asyncio.TimeoutError(
                        f"LLM call exceeded {_llm_call_timeout}s (iter {i+1}, attempt {_attempt+1})"
                    )
                    logger.warning(
                        f"⏱️ LLM timeout {_llm_call_timeout}s dépassé "
                        f"(itération {i+1}, tentative {_attempt+1}/3) — contexte peut-être trop lourd"
                    )
                    logger.info(
                        f"LLM_RETRY: timeout {_llm_call_timeout}s (itér {i+1}, essai {_attempt+1}/3) — "
                        f"DeepSeek lent ou surchargé. Nouvel essai avec +30s..."
                    )
                    _llm_call_timeout = min(_llm_call_timeout + 30, 420)  # Budget augmenté au retry
                    if _attempt < 2:
                        await asyncio.sleep(1.0)
                except Exception as e:
                    _llm_last_exc = e
                    if _attempt < 2:
                        logger.warning(f"⚠️ LLM tentative {_attempt + 1}/3 échouée ({e}), retry dans {1.5 * (_attempt + 1):.1f}s…")
                        await asyncio.sleep(1.5 * (_attempt + 1))
                    else:
                        logger.error(f"❌ LLM échoué après 3 tentatives : {e}")
            if _llm_last_exc is not None:
                if TELEMETRY_AVAILABLE:
                    publish_trace(
                        stage="llm_request_done",
                        status="error",
                        mode="agent",
                        duration_ms=(perf_counter() - llm_started) * 1000.0,
                        error=str(_llm_last_exc),
                    )
                    publish_trace(
                        stage="pipeline_error",
                        status="error",
                        mode="agent",
                        error=str(_llm_last_exc),
                    )
                _finish_iteration(status="error", error="llm_request_failed")
                # ── Fallback: au lieu de crash, tenter un prompt compacté ──
                if isinstance(_llm_last_exc, asyncio.TimeoutError) and i > 0 and len(self.history) > 0:
                    logger.warning("⚠️ Triple timeout — tentative fallback avec prompt compacté")
                    _compact_prompt = (
                        f"Requête originale: {original_query}\n\n"
                        f"Tu as déjà fait {len(self.history)} actions (list_directory, run_command, etc.) "
                        f"mais le LLM a timeout 3 fois car le contexte est trop lourd.\n"
                        f"AGIS MAINTENANT: utilise `create_project` ou `write_file` pour produire le résultat. "
                        f"Ne fais plus d'exploration. Résume ce que tu sais et agis."
                    )
                    query = _compact_prompt
                    self.history = self.history[-2:]  # Garder seulement les 2 dernières étapes
                    self._identity_ctx_cache = None  # Invalider le cache contexte
                    _finish_iteration(status="ok", summary="fallback_compact_after_triple_timeout")
                    continue
                raise _llm_last_exc
            # ── Check global deadline après l'appel LLM ──
            if hasattr(self, '_timeout_deadline') and perf_counter() > self._timeout_deadline:
                raise asyncio.TimeoutError()
            self._last_llm_meta = self._get_llm_meta()
            if TELEMETRY_AVAILABLE:
                publish_trace(
                    stage="llm_request_done",
                    status="ok",
                    mode="agent",
                    duration_ms=(perf_counter() - llm_started) * 1000.0,
                    provider=self._last_llm_meta.get("provider_used"),
                    model=self._last_llm_meta.get("model_used"),
                    summary=f"finish_reason={self._last_llm_meta.get('finish_reason')}" if self._last_llm_meta.get("finish_reason") else None,
                )

            # ── Sanitisation LLM output (corrige bugs courants des LLM) ──
            if response:
                response = _sanitize_llm_output(response)

            # FIX TRONCATURE: si réponse coupée (finish_reason == "length"),
            # sauvegarder le contenu partiel et orienter la suite sans tout recommencer.
            _trunc_fr = str(self._last_llm_meta.get("finish_reason") or "").strip().lower()
            if self._is_length_finish_reason(_trunc_fr) and response and len(response.strip()) > 100:
                logger.warning(
                    "✂️ Réponse tronquée détectée (finish_reason=%s, %d chars) - sauvegarde du partiel",
                    _trunc_fr, len(response),
                )
                # Essayer d'extraire path + contenu partiel d'un éventuel write_file
                import re as _re_trunc
                import os as _os_trunc
                _saved_partial_path: Optional[str] = None
                _partial_content_for_ctx: str = ""
                _tool_match = _re_trunc.search(
                    r'ACTION:\s*tool_call.*?ACTION_INPUT:\s*(\{.*)',
                    response, _re_trunc.DOTALL | _re_trunc.IGNORECASE,
                )
                if _tool_match:
                    try:
                        import json as _json_trunc
                        _raw_json = _tool_match.group(1).strip()
                        # Fermer le JSON partiellement tronqué pour pouvoir le lire
                        # Compter les accolades pour estimer où ajouter }
                        _opens = _raw_json.count("{")
                        _closes = _raw_json.count("}")
                        _raw_completed = _raw_json + "}" * max(0, _opens - _closes)
                        try:
                            _args = _json_trunc.loads(_raw_completed)
                        except Exception:
                            # En cas d'échec JSON, extraire manuellement le path et content
                            _path_m = _re_trunc.search(r'"path"\s*:\s*"([^"]+)"', _raw_json)
                            _content_m = _re_trunc.search(r'"content"\s*:\s*"(.*)', _raw_json, _re_trunc.DOTALL)
                            _args = {}
                            if _path_m:
                                _args["path"] = _path_m.group(1)
                            if _content_m:
                                _args["content"] = _content_m.group(1).replace('\\"', '"').replace('\\n', '\n')
                        _wf_path = str(_args.get("path", "") or "").strip()
                        _wf_content = str(_args.get("content", "") or "")
                        if _wf_path and _wf_content and len(_wf_content) > 50:
                            # Résoudre le chemin par rapport au workspace
                            _base_ws = str(Path(__file__).parent.parent.parent)
                            _abs_path = _wf_path if _os_trunc.path.isabs(_wf_path) else _os_trunc.path.join(_base_ws, _wf_path)
                            _os_trunc.makedirs(_os_trunc.path.dirname(_abs_path), exist_ok=True)
                            with open(_abs_path, "w", encoding="utf-8") as _pf:
                                _pf.write(_wf_content)
                                _pf.write("\n\n# [TRONCATURE: suite à compléter]")
                            _saved_partial_path = _wf_path
                            _partial_content_for_ctx = _wf_content[-1500:]  # Garder la fin pour le contexte
                            logger.info("💾 Contenu partiel sauvegardé dans: %s (%d chars)", _wf_path, len(_wf_content))
                    except Exception as _trunc_ex:
                        logger.warning("⚠️ Impossible d'extraire le write_file tronqué: %s", _trunc_ex)
                        _partial_content_for_ctx = response[-2000:]
                else:
                    # Pas de write_file détecté, prendre la fin de la réponse comme contexte
                    _partial_content_for_ctx = response[-2000:]

                # Construire le prompt de continuation
                _trunc_ctx_parts = [
                    f"Requête originale: {original_query}",
                    "",
                    "⚠️ CONTINUATION REQUISE: Ta réponse précédente a été coupée (limite de tokens atteinte).",
                ]
                if _saved_partial_path:
                    _trunc_ctx_parts += [
                        f"✅ Le fichier `{_saved_partial_path}` a été partiellement sauvegardé avec ce qui avait déjà été généré.",
                        "Continue maintenant en écrivant la SUITE du fichier (uniquement ce qui manque), ou passe à l'étape suivante du plan.",
                    ]
                    # Nudge vers generate_website si c'est un fichier web tronqué
                    if any(_saved_partial_path.endswith(ext) for ext in ('.html', '.css', '.js')):
                        _trunc_ctx_parts += [
                            "",
                            "⚠️ IMPORTANT: Tu as essayé d'écrire un fichier web complet avec write_file "
                            "mais il a été TRONQUÉ par la limite de tokens. "
                            "Utilise plutôt l'outil `generate_website` qui est conçu pour créer des sites "
                            "multi-fichiers sans troncature. Appelle-le avec une description détaillée.",
                        ]
                else:
                    _trunc_ctx_parts += [
                        "Voici la FIN de ce que tu avais généré (ne répète pas, continue à partir de là):",
                        "",
                        f"```\n{_partial_content_for_ctx}\n```",
                        "",
                        "Continue maintenant là où tu t'es arrêté. Si c'est du code/fichier: écris la suite avec write_file. Si c'est fini: utilise FINAL.",
                    ]
                _trunc_ctx_parts += ["", "Ne recommence PAS depuis le début."]
                query = "\n".join(_trunc_ctx_parts)
                _finish_iteration(status="ok", summary="truncation_continuation_injected")
                continue

            # 2. Parser la réponse
            logger.info(f"📥 LLM RESPONSE SIZE: {len(response)} chars")
            
            # FIX: Gérer les réponses vides - retry au lieu de terminer
            if not response or len(response.strip()) == 0:
                logger.warning("⚠️ Réponse LLM vide détectée - retry avec rappel de format")
                query = f"{query}\n\n⚠️ Ta dernière réponse était vide. RAPPEL: utilise le format THOUGHT/ACTION pour répondre."
                _finish_iteration(status="error", error="empty_llm_response")
                continue  # Skip to next iteration instead of parsing empty response
            
            thought, action = self._parse_response(response)
            logger.debug(f"Thought: {thought.content}")
            logger.debug(f"Action: {action.action_type.value}")

            # P2 FIX: Si une tentative de repair FINAL a produit un tool_call au lieu
            # d'un FINAL, la réponse originale était correcte — rollback immédiat.
            _pre_repair = getattr(self, '_pre_repair_answer', None)
            if _pre_repair and action.action_type != ActionType.FINAL_ANSWER:
                logger.warning(
                    "⚠️ Repair FINAL a produit {} au lieu de FINAL — rollback vers réponse originale ({} chars)",
                    action.action_type.value, len(_pre_repair),
                )
                self._pre_repair_answer = None
                self._run_meta["agent_repair_attempts"] = self._final_repair_attempts
                self._run_meta["agent_output_incomplete"] = False
                _finish_iteration(status="ok", summary="final_repair_rollback")
                message = _pre_repair
                self._mark_task_done(message)
                return message
            # Clear pre_repair si le repair a réussi (FINAL produit)
            if _pre_repair and action.action_type == ActionType.FINAL_ANSWER:
                self._pre_repair_answer = None

            # 2.0a Tracking hallucinations consécutives (Kimi simule des OBSERVATION)
            _halluc_warning = ""
            if getattr(self, '_last_thought_was_hallucinated', False):
                _halluc_streak = getattr(self, '_halluc_streak', 0) + 1
                self._halluc_streak = _halluc_streak
                if _halluc_streak >= 1:
                    _halluc_warning = (
                        "\n\n⚠️ RAPPEL CRITIQUE: Tu as simulé des résultats d'outils "
                        f"{_halluc_streak} fois. Le contenu halluciné est SUPPRIMÉ. "
                        "Écris SEULEMENT ton THOUGHT, puis ACTION et ACTION_INPUT. "
                        "ATTENDS l'OBSERVATION du système. N'écris JAMAIS "
                        "'OBSERVATION:' toi-même."
                    )
                    logger.warning("⚠️ Hallucination streak: {} — warning injecté", _halluc_streak)
            else:
                self._halluc_streak = 0

            # 2.0 Plan TODO : parsing a l'iteration 0 uniquement
            if i == 0 and not self._plan_emitted:
                parsed_plan = self._parse_plan(response)
                if parsed_plan:
                    self._task_plan = parsed_plan
                    self._plan_emitted = True
                    logger.info(f"[PLAN] Plan detecte avec {len(parsed_plan)} taches")
                    for idx_p, t in enumerate(parsed_plan):
                        logger.info(f"  [{idx_p+1}] {t.description}")
                    # Émettre l'état initial pour le frontend
                    self._emit_plan_state(context_tool="")

            # 2.1 Détection de stagnation de pensée (thoughts quasi-identiques)
            _stagnation_warning = ""
            if thought.content:
                _current_words = set(thought.content.lower().split())
                _is_stagnant = False
                if len(_previous_thoughts) >= 2:
                    _last_words = set(_previous_thoughts[-1].lower().split())
                    if _current_words and _last_words:
                        _overlap = len(_current_words & _last_words) / max(len(_current_words | _last_words), 1)
                        _prev_words = set(_previous_thoughts[-2].lower().split())
                        _overlap2 = len(_current_words & _prev_words) / max(len(_current_words | _prev_words), 1)
                        # Seuil adaptatif : 65% si requête courte (≤5 mots), 80% sinon
                        _q_words = len(original_query.split())
                        _thresh = 0.65 if _q_words <= 5 else 0.80
                        if _overlap > _thresh and _overlap2 > _thresh:
                            _is_stagnant = True
                # Détection secondaire : 3+ actions read-only consécutives sur même sujet
                if not _is_stagnant and len(_previous_thoughts) >= 3:
                    _recent_3 = _previous_thoughts[-3:] + [thought.content]
                    _common_prefix = set(_recent_3[0].lower().split()[:15])
                    _all_share = all(
                        len(_common_prefix & set(t.lower().split()[:15])) / max(len(_common_prefix), 1) > 0.60
                        for t in _recent_3[1:]
                    )
                    if _all_share:
                        _is_stagnant = True
                _previous_thoughts.append(thought.content)
                if len(_previous_thoughts) > 5:
                    _previous_thoughts = _previous_thoughts[-5:]
                if _is_stagnant:
                    _stagnation_streak += 1
                    logger.warning("⚠️ Stagnation pensée détectée (3 thoughts quasi-identiques) — streak={}", _stagnation_streak)
                    # P4: Injecter les outils pertinents dans le warning de stagnation
                    _stag_tool_hint = ""
                    if hasattr(self.tools, "_tool_modules"):
                        _q_low = original_query.lower()
                        _stag_relevant: list = []
                        _STAG_KW_MAP = [
                            (("pdf", "rapport", "document", "facture", "devis"),
                             ["create_pdf", "create_docx", "create_invoice_pdf", "create_from_template"]),
                            (("site", "web", "html", "page"),
                             ["create_project", "generate_website", "write_file"]),
                            (("image", "photo", "capture"),
                             ["generate_image", "screenshot", "screenshot_analyze"]),
                            (("mail", "email", "courriel"),
                             ["send_email", "mail_send"]),
                        ]
                        for _kws, _tools in _STAG_KW_MAP:
                            if any(k in _q_low for k in _kws):
                                _stag_relevant.extend(t for t in _tools if t in self.tools.tools)
                        if _stag_relevant:
                            _stag_tool_hint = (
                                " Outils disponibles pour cette tâche : "
                                + ", ".join(f"`{t}`" for t in _stag_relevant[:5])
                                + ". Utilise-les directement."
                            )
                    _stagnation_warning = (
                        "\n\n⚠️ STAGNATION: Tu répètes le même raisonnement. "
                        "Après cette action, AGIS ou donne ta réponse FINAL."
                        + _stag_tool_hint
                    )
                    # Après 3 stagnations consécutives : forcer la complétion du plan
                    # pour que PLAN GUARD ne bloque pas le prochain FINAL
                    if _stagnation_streak >= 3 and self._task_plan:
                        logger.warning("⚠️ Stagnation critique ({}) — bypass PLAN GUARD pour débloquer FINAL", _stagnation_streak)
                        # NE PAS mentir sur l'état des tâches — juste bypasser le guard
                        self._plan_guard_retries = 3  # Empêche PLAN GUARD de bloquer
                    # P3 HARD: Après 3 stagnations consécutives ET actions identiques → FORCER FINAL synthétique
                    # Une progression légitime (lectures séquentielles avec args différents) est tolérée.
                    _actions_are_redundant = False
                    if _stagnation_streak >= 3 and len(self.history) >= 3:
                        _recent_actions = self.history[-3:]
                        _sig = (action.tool_name, str(action.tool_args))
                        _recent_sigs = [(h.action.tool_name, str(h.action.tool_args)) for h in _recent_actions]
                        # Si les 3 dernières actions + l'actuelle sont toutes identiques → vrai blocage
                        _actions_are_redundant = all(s == _sig for s in _recent_sigs)
                    if _stagnation_streak >= 3 and _actions_are_redundant:
                        logger.error(
                            "🛑 Stagnation HARD ({}× consécutives, action identique) — FORCE FINAL synthétique",
                            _stagnation_streak,
                        )
                        _forced_answer = (
                            "Je stagne depuis 3 tours consécutifs sur le même raisonnement "
                            "ET la même action, sans progresser. Je m'arrête pour éviter une boucle inutile.\n\n"
                            "Résumé de ce que j'ai exploré :\n"
                            f"- Dernière pensée : {thought.content[:200]}\n"
                            f"- Action tentée : {action.action_type.value}"
                            + (f" ({action.tool_name})" if action.tool_name else "")
                            + "\n\n"
                            "👉 Peux-tu reformuler ta demande ou me donner une instruction "
                            "plus précise ? Si tu veux que j'agisse, dis-le explicitement "
                            "(ex: \"modifie X\", \"écris Y\", \"lance Z\")."
                        )
                        action = Action(
                            action_type=ActionType.FINAL_ANSWER,
                            answer=_forced_answer,
                        )
                        thought = Thought(content="Stagnation critique détectée — arrêt forcé.")
                        _stagnation_streak = 0  # Reset pour ne pas rebloquer le prochain tour
                else:
                    _stagnation_streak = 0  # Reset si la pensée change

            if TELEMETRY_AVAILABLE and action.action_type == ActionType.TOOL_CALL:
                publish_trace(
                    stage="tool_parse",
                    status="ok",
                    mode="agent",
                    tool_name=action.tool_name,
                    summary=str(action.tool_args),
                )
            
            # 2.5 Détecter les actions répétées (mais pas pour lecture de fichiers différents)
            if action.action_type == ActionType.TOOL_CALL:
                if self._is_exploratory_tool(action.tool_name or ""):
                    _exploratory_since_productive += 1
                    _productive_tools = {"write_file", "create_project", "create_file", "delegate_task", "execute_code", "dev_run_fix"}
                    # Détecter si un projet a déjà été créé/livré (éviter recréation)
                    has_prior_project = any(
                        h.action.tool_name == "create_project" and h.observation and h.observation.success
                        for h in self.history
                    )
                    has_run_error = any(
                        h.action.tool_name == "run_command" and h.observation and not h.observation.success
                        for h in self.history
                    )
                    _threshold = 3 if single_file_creation_intent else 6
                    if _exploratory_since_productive >= _threshold:
                        logger.warning(
                            "⚠️ Trop d'actions exploratoires sans production: forçage action productive"
                        )
                        if has_prior_project or has_run_error:
                            # Projet déjà créé → fixer, pas recréer
                            query = (
                                f"Requête originale: {original_query}\n\n"
                                "⚠️ STOP exploration. Un projet existe DÉJÀ dans ton historique.\n"
                                "⛔ Ne recrée PAS un nouveau projet. CORRIGE l'existant :\n"
                                "- Si une commande a échoué → `dev_run_fix(command='...', project_dir='...')` pour diagnostiquer et corriger automatiquement.\n"
                                "- Si un fichier a un bug → `edit_file` pour le corriger.\n"
                                "- Si tout est OK → donne ta réponse avec ACTION: FINAL."
                            )
                        else:
                            query = (
                                f"Requête originale: {original_query}\n\n"
                                "⚠️ STOP exploration. Tu as assez de contexte après "
                                f"{_exploratory_since_productive} actions exploratoires sans rien produire.\n"
                                "La prochaine action DOIT être productive : `create_project`, `write_file`, "
                                "`delegate_task(agent_type='code')`, ou `execute_code`.\n"
                                "Si le code est complexe (>50 lignes), utilise `create_project` ou `delegate_task`.\n"
                                "Ensuite termine avec ACTION: FINAL."
                            )
                        _exploratory_since_productive = 0
                        _finish_iteration(status="ok", summary="forced_productive_after_exploration")
                        continue
                else:
                    # Action productive (write, create, dev_run_fix, etc.) → reset compteur
                    if action.tool_name in {"write_file", "create_project", "create_file", "delegate_task", "execute_code", "dev_run_fix", "edit_file", "edit_own_code"}:
                        _exploratory_since_productive = 0

                if action.tool_name == "read_file":
                    target_path = str(action.tool_args.get("path", "") or "").strip()
                    start_line_raw = action.tool_args.get("start_line")
                    end_line_raw = action.tool_args.get("end_line")
                    try:
                        start_line = max(1, int(start_line_raw)) if start_line_raw is not None else 1
                    except Exception:
                        start_line = 1
                    try:
                        end_line = int(end_line_raw) if end_line_raw is not None else None
                    except Exception:
                        end_line = None
                    if end_line is not None and end_line < start_line:
                        end_line = start_line

                    current_signature = (target_path, start_line, end_line)
                    if current_signature == last_read_signature:
                        repeated_read_count += 1
                    else:
                        repeated_read_count = 0
                    last_read_signature = current_signature

                    if repeated_read_count >= 2:
                        page_size = 350
                        next_start = (end_line + 1) if end_line is not None else (start_line + page_size)
                        next_end = next_start + page_size - 1
                        logger.warning(
                            "⚠️ read_file répété sans progression sur {} - pagination forcée {}-{}",
                            target_path,
                            next_start,
                            next_end,
                        )
                        action.tool_args["start_line"] = next_start
                        action.tool_args["end_line"] = next_end
                        repeated_read_count = 0

                    # Guard par path : distinguer nouvelles plages vs relectures
                    _range_key = (start_line, end_line)
                    _read_file_path_counter[target_path] = _read_file_path_counter.get(target_path, 0) + 1
                    _rf_count = _read_file_path_counter[target_path]
                    if target_path not in _read_file_ranges_seen:
                        _read_file_ranges_seen[target_path] = set()
                    _is_reread = _range_key in _read_file_ranges_seen[target_path]
                    _read_file_ranges_seen[target_path].add(_range_key)
                    if _is_reread:
                        _read_file_reread_counter[target_path] = _read_file_reread_counter.get(target_path, 0) + 1
                    _reread_count = _read_file_reread_counter.get(target_path, 0)
                    # Seuils adaptatifs : fichiers longs tolèrent plus de lectures distinctes
                    _max_total = max(8, len(_read_file_ranges_seen[target_path]) + 4)  # au moins 8
                    if _rf_count >= 4:
                        logger.warning(
                            "⚠️ read_file sur '{}' appelé {}x ({}x nouvelles plages, {}x relectures)",
                            target_path,
                            _rf_count,
                            len(_read_file_ranges_seen[target_path]),
                            _reread_count,
                        )
                    # Forcer FINAL si trop de relectures OU trop de lectures totales
                    if _reread_count >= 3 or _rf_count >= _max_total:
                        _reason = (
                            f"relectures={_reread_count}" if _reread_count >= 3
                            else f"total={_rf_count}/{_max_total}"
                        )
                        logger.warning(
                            "⚠️ read_file stagnation sur '{}' — forçage FINAL ({})",
                            target_path,
                            _reason,
                        )
                        _finish_iteration(status="ok", summary=f"forced_final_read_stagnation_{_reason}")
                        summary_parts = []
                        for h in self.history[-5:]:
                            if h.observation and h.observation.content:
                                summary_parts.append(h.observation.content[:300])
                        message = (
                            f"J'ai analysé le fichier '{target_path}' en détail. "
                            "Voici ce que j'ai trouvé :\n\n"
                            + "\n".join(summary_parts[-2:])
                        )
                        self._mark_task_done(message)
                        return message

                # Outils exemptés de détection de répétition (normaux d'être appelés plusieurs fois)
                exempt_tools = [
                    "read_own_code",
                    "web_search", "memory_search", "grep_search", "search_in_code",
                    "view_file_outline", "get_time", "memory_add",
                    # Inspection web: peut être appelée plusieurs fois sur des pages différentes
                    "browser_get_content",
                    # list_directory a son propre guard dédié (redirect vers find_files)
                    "list_directory",
                ]
                # NOTE: read_file retiré de exempt_tools — le guard bloque 3x même fichier+mêmes args
                # NOTE: write_file retiré de exempt_tools - on veut détecter les écritures répétées
                
                # Pour http_request, la clé significative est (tool, url, method) — ignorer headers/body
                # qui varient entre tentatives et trompent la détection de boucle
                if action.tool_name == "http_request":
                    _loop_url = str(action.tool_args.get("url", ""))
                    _loop_method = str(action.tool_args.get("method", "GET")).upper()
                    action_key = (action.tool_name, _loop_url, _loop_method)
                else:
                    action_key = (action.tool_name, str(action.tool_args))

                if action.tool_name == "list_directory":
                    target_path = str(action.tool_args.get("path", ".") or ".").strip()
                    target_path_lower = target_path.lower()
                    repeated_same_path = 0
                    for _prev_entry in self.action_history[-12:]:
                        previous_name = _prev_entry[0] if isinstance(_prev_entry, tuple) else _prev_entry
                        previous_args = _prev_entry[1] if isinstance(_prev_entry, tuple) and len(_prev_entry) > 1 else ""
                        if previous_name != "list_directory":
                            continue
                        previous_args_str = str(previous_args).lower()
                        if f"'path': '{target_path_lower}'" in previous_args_str or f'"path": "{target_path_lower}"' in previous_args_str:
                            repeated_same_path += 1

                    if repeated_same_path >= 2 and "find_files" in self.tools.tools:
                        filename_match = re.search(r"([A-Za-z0-9 _().-]+\.[A-Za-z0-9]{1,8})", original_query)
                        pattern_hint = filename_match.group(1).strip() if filename_match else "*.txt"
                        logger.warning(
                            "⚠️ list_directory répété sur '{}' - bascule vers find_files(pattern={})",
                            target_path,
                            pattern_hint,
                        )
                        action = Action(
                            action_type=ActionType.TOOL_CALL,
                            tool_name="find_files",
                            tool_args={"pattern": pattern_hint, "path": "workspace"},
                        )
                        action_key = (action.tool_name, str(action.tool_args))
                
                # FIX: Détection spécifique des écritures répétées au même fichier
                if action.tool_name == "write_file":
                    target_path = action.tool_args.get("path", "") or action.tool_args.get("file_path", "")
                    write_count = sum(1 for k in self.action_history if k[0] == "write_file" and target_path in str(k[1]))
                    if write_count >= 3:
                        logger.warning(f"⚠️ Fichier {target_path} écrit {write_count} fois - arrêt de la boucle")
                        _finish_iteration(status="ok", summary="stop_on_repeated_write_file")
                        message = f"✅ Fichier {target_path} créé avec succès après {write_count} tentatives."
                        self._mark_task_done(message)
                        return message

                # FIX: Si mail_send a déjà réussi, ne pas boucler sur la vérification IMAP
                # (mail_list_messages avec les dossiers IMAP encodés échoue souvent et crée une spirale)
                _mail_verification_tools = {"mail_list_messages", "mail_list_folders"}
                if action.tool_name in _mail_verification_tools:
                    _successful_mail_sends = [
                        h for h in self.history
                        if h.action.tool_name in {"mail_send", "mail_reply_message", "send_email"}
                        and h.observation and h.observation.success
                    ]
                    if _successful_mail_sends:
                        _last_send = _successful_mail_sends[-1]
                        _to = _last_send.action.tool_args.get("to", "le destinataire")
                        _subject = _last_send.action.tool_args.get("subject", "")
                        logger.info(
                            "✅ mail_send déjà confirmé - skip vérification IMAP et FINAL direct"
                        )
                        _finish_iteration(status="ok", summary="mail_already_sent_skip_imap_check")
                        _subject_str = f' (sujet : "{_subject}")' if _subject else ""
                        message = (
                            f"✅ Email envoyé avec succès à **{_to}**{_subject_str}.\n\n"
                            "L'envoi a été confirmé par le serveur SMTP. "
                            "Tu devrais le recevoir dans quelques instants."
                        )
                        self._mark_task_done(message)
                        return message

                # FIX: mail_send répété vers le même destinataire = boucle, forcer FINAL
                if action.tool_name in {"mail_send", "mail_reply_message", "send_email"}:
                    _current_to = str(action.tool_args.get("to", "")).strip().lower()
                    _same_send_count = sum(
                        1 for h in self.history
                        if h.action.tool_name in {"mail_send", "mail_reply_message", "send_email"}
                        and h.observation and h.observation.success
                        and str(h.action.tool_args.get("to", "")).strip().lower() == _current_to
                    )
                    if _same_send_count >= 1:
                        logger.warning(
                            "⚠️ mail_send vers '{}' déjà réussi - éviter doublon et forcer FINAL",
                            _current_to,
                        )
                        _finish_iteration(status="ok", summary="mail_already_sent_no_duplicate")
                        message = (
                            f"✅ Email déjà envoyé avec succès à **{_current_to}**.\n\n"
                            "L'envoi précédent a été confirmé par le serveur SMTP, je n'envoie pas de doublon."
                        )
                        self._mark_task_done(message)
                        return message
                
                # --- Détection d'échecs CONSÉCUTIFS sur le MÊME outil ---
                # FIX: ignorer les outils read-only qui ont retourné du contenu
                # non-vide (ex: read_file de 10KB mal flaggé par le détecteur
                # par mots-clés). Un "échec" avec 500+ chars de contenu est un
                # faux positif, pas une vraie erreur.
                _READ_ONLY_NO_FAIL_COUNT = {
                    "read_file", "list_directory", "find_files", "grep_search",
                    "search_in_code", "view_file_outline", "browser_get_content",
                    "memory_search", "web_search", "read_own_code",
                }
                _recent_fails = 0
                for h in reversed(self.history[-8:]):
                    if h.action.tool_name != action.tool_name:
                        continue
                    if h.observation and h.observation.success:
                        break  # un succès récent casse la série
                    if h.observation and not h.observation.success:
                        # Skip: outil lecture ayant ramené du contenu substantiel
                        if (
                            action.tool_name in _READ_ONLY_NO_FAIL_COUNT
                            and h.observation.content
                            and len(h.observation.content) >= 500
                        ):
                            continue
                        _recent_fails += 1
                if _recent_fails >= 3:
                    logger.warning(f"⚠️ Outil {action.tool_name} a échoué {_recent_fails}x récemment — escalade CodeAgent")

                    # ── Escalade automatique vers CodeAgent ──
                    _lum = getattr(self.tools, "lumena", None)
                    if _lum is not None and action.tool_name in ("edit_file", "write_file", "apply_patch"):
                        try:
                            from ..agents.sub_agent import delegate_to_agent
                            _root = getattr(_lum, "runtime_root", None)
                            _ctx: Dict[str, Any] = {}
                            # ── Déduire le workspace projet depuis l'historique ou la query ──
                            _esc_project_path = None
                            # 1. Chemin explicite dans la query
                            _esc_qm = re.search(
                                r'([A-Za-z]:\\[^\s]+?[\\/]workspace[\\/][\w\-]+)', query,
                            )
                            if not _esc_qm:
                                _esc_qm = re.search(r'(workspace[\\/][\w\-]+)', query)
                            if _esc_qm:
                                _cand = _esc_qm.group(1)
                                if not os.path.isabs(_cand) and _root:
                                    _cand = os.path.join(str(_root), _cand)
                                if os.path.isdir(_cand):
                                    _esc_project_path = _cand
                            # 2. Extraire depuis les file_path des actions récentes
                            if not _esc_project_path:
                                for _h in reversed(self.history[-10:]):
                                    if _h.action and _h.action.args:
                                        for _v in _h.action.args.values():
                                            if isinstance(_v, str) and "workspace" in _v.replace("\\", "/").lower():
                                                _m = re.search(r'(.+?[\\/]workspace[\\/][\w\-]+)', _v)
                                                if _m and os.path.isdir(_m.group(1)):
                                                    _esc_project_path = _m.group(1)
                                                    break
                                        if _esc_project_path:
                                            break
                            _ctx["workspace_path"] = _esc_project_path or (str(_root) if _root else "")
                            if _esc_project_path:
                                _ctx["project_dir"] = _esc_project_path
                            logger.info("[ReAct] Escalade → CodeAgent après {}x échecs {} (workspace={})", _recent_fails, action.tool_name, _ctx.get("workspace_path", "?")[:80])
                            _ca_result = await delegate_to_agent(query, agent_type="code", context=_ctx)
                            if _ca_result:
                                logger.info("[ReAct] CodeAgent (escalade) terminé: {} chars", len(_ca_result))
                                _finish_iteration(status="ok", summary=f"escalated_to_codeagent_after_{action.tool_name}")
                                return _ca_result
                        except Exception as _ca_exc:
                            logger.warning("[ReAct] CodeAgent escalade échouée: {}", _ca_exc)

                    # ── Fallback: forçage FINAL si CodeAgent indisponible ──
                    self._run_meta["agent_output_warning"] = "tool_repeated_failure"
                    _finish_iteration(status="ok", summary=f"stop_repeated_failure_{action.tool_name}")
                    _fail_obs = [
                        h.observation.content[:200]
                        for h in self.history[-5:]
                        if h.action.tool_name == action.tool_name
                        and h.observation and not h.observation.success
                    ]
                    message = (
                        f"⚠️ J'ai essayé {action.tool_name} plusieurs fois mais ça échoue à chaque fois.\n\n"
                        f"**Dernière erreur:** {_fail_obs[-1] if _fail_obs else 'inconnue'}\n\n"
                        "Je dois reformuler ou utiliser un autre outil."
                    )
                    self._mark_task_failed(f"repeated_failure_{action.tool_name}")
                    return message

                # ── Détecteur de stagnation post-édition ──────────────────────
                # Si Lumena a fait des éditions puis enchaîne N iter de read-only
                # sans jamais agir → forcer auto-conclusion ou check_web_project
                if action.tool_name in _write_tools:
                    _has_done_edits = True
                    _post_edit_read_streak = 0
                elif _has_done_edits and action.tool_name in _read_only_tools:
                    _post_edit_read_streak += 1
                    if _post_edit_read_streak >= 4:
                        # 4 iterations read-only après édition = stagnation verification
                        # Injecter un GUIDANCE pour forcer la conclusion ou l'action
                        if _post_edit_read_streak == 4:
                            logger.warning(
                                "⚠️ Stagnation post-édition: {} iter read-only après des éditions — guidance injectée",
                                _post_edit_read_streak,
                            )
                            self._pending_loop_guidance = (
                                "⚠️ STOP — tu as fait des modifications et tu vérifies en boucle depuis "
                                f"{_post_edit_read_streak} itérations sans rien changer. "
                                "Options : 1) Utilise `check_web_project` pour valider, "
                                "2) Corrige un problème trouvé avec write_file/edit_file, "
                                "3) Conclus avec FINAL_ANSWER si les corrections sont terminées."
                            )
                        elif _post_edit_read_streak >= 6:
                            # 6 iter = forçage total
                            logger.warning(
                                "⚠️ Stagnation post-édition forcée FINAL après {} iter read-only",
                                _post_edit_read_streak,
                            )
                            _finish_iteration(status="ok", summary=f"forced_final_post_edit_stagnation_{_post_edit_read_streak}")
                            _recent_edits = [
                                f"- {h.action.tool_name}({list(h.action.tool_args.keys())[:2]})"
                                for h in self.history[-15:]
                                if h.action.tool_name in _write_tools
                            ]
                            message = (
                                "✅ Modifications appliquées :\n"
                                + "\n".join(_recent_edits[-5:])
                                + "\n\nLes corrections ont été vérifiées. Tâche terminée."
                            )
                            self._mark_task_done(message)
                            return message
                else:
                    # Outil ni write ni read-only → reset streak
                    if action.tool_name not in ("parallel_tools",):
                        _post_edit_read_streak = 0

                # Ne pas compter comme répétition pour les outils exemptés
                if action.tool_name not in exempt_tools:
                    # Phase 2.2: Détection de boucle améliorée (même action 3x = forcer FINAL)
                    if action.tool_name == "http_request":
                        _sig_url = str(action.tool_args.get("url", ""))
                        _sig_method = str(action.tool_args.get("method", "GET")).upper()
                        current_action_sig = (action.tool_name, _sig_url, _sig_method)
                    else:
                        current_action_sig = (action.tool_name, str(action.tool_args))
                    if current_action_sig == self._last_action_signature:
                        self._consecutive_same_action += 1
                    else:
                        self._consecutive_same_action = 1
                        self._last_action_signature = current_action_sig

                    # Détection précoce : 2x consécutif → rappel informatif (pas bloquant)
                    # Le LLM peut avoir une raison légitime de relancer (polling, comparaison, etc.)
                    if self._consecutive_same_action == 2:
                        logger.info("ℹ️ Commande identique 2x consécutive: {} — rappel injecté", action.tool_name)
                        self._pending_loop_guidance = (
                            f"ℹ️ NOTE: Tu viens d'exécuter `{action.tool_name}` avec les mêmes arguments "
                            f"que l'itération précédente. Si tu as déjà le résultat dont tu as besoin, "
                            f"passe à l'étape suivante plutôt que de relancer. "
                            f"Si tu relances volontairement (comparaison, polling), c'est OK."
                        )

                    # Détection de boucle lente : même (outil+args) 3+ fois dans la fenêtre des 10 dernières actions
                    _window_count = self.action_history[-10:].count(current_action_sig)
                    if _window_count >= 3 and self._consecutive_same_action < 3:
                        logger.warning(
                            "⚠️ Boucle lente: {} appelé {}x dans la fenêtre — guidance injectée",
                            action.tool_name, _window_count + 1,
                        )
                        self._pending_loop_guidance = (
                            f"⚠️ GUIDANCE ANTI-BOUCLE: Tu viens d'appeler `{action.tool_name}` avec les mêmes arguments "
                            f"pour la {_window_count + 1}e fois dans cette session. "
                            f"Cette approche ne retourne pas les informations dont tu as besoin. "
                            f"Essaie impérativement une COMMANDE DIFFÉRENTE pour atteindre ton objectif."
                        )

                    # ── Détecteur anti-aveuglement browser ──
                    # Si 3+ actions browser_* consécutives SANS screenshot ni dom_state → forcer à "voir"
                    _tool = action.tool_name or ""
                    _iter_now = len(self.history)
                    _BROWSER_VISUAL = {"browser_screenshot", "browser_dom_state", "browser_get_content"}
                    _BROWSER_ACTION = {
                        "browser_click", "browser_click_index", "browser_click_smart",
                        "browser_click_at", "browser_type", "browser_type_index",
                        "browser_navigate", "browser_hover", "browser_select",
                        "browser_keyboard_press", "browser_drag", "browser_drag_at",
                    }
                    if _tool in _BROWSER_VISUAL:
                        self._last_browser_visual_iter = _iter_now
                        self._browser_blind_streak = 0
                    elif _tool in _BROWSER_ACTION:
                        self._browser_blind_streak += 1
                        if self._browser_blind_streak >= 3:
                            logger.warning(
                                "⚠️ Aveuglement browser: {} actions consécutives sans voir — guidance injectée",
                                self._browser_blind_streak,
                            )
                            self._pending_loop_guidance = (
                                "⚠️ GUIDANCE VISION: Tu viens d'enchaîner "
                                f"{self._browser_blind_streak} actions browser_* sans prendre de screenshot "
                                "ni relire le DOM. Tu agis à l'aveugle. "
                                "APPELLE MAINTENANT `browser_screenshot` pour voir l'état réel de la page "
                                "avant ta prochaine action. Le DOM a probablement changé."
                            )
                            self._browser_blind_streak = 0  # reset après injection

                    if self._consecutive_same_action >= 3:
                        logger.warning(f"⚠️ Boucle détectée: {action.tool_name} appelé 3x identiquement - forçage FINAL_ANSWER")
                        self._run_meta["agent_output_warning"] = "loop_detected_forced_final"
                        _finish_iteration(status="ok", summary="loop_break_3x_same_action")
                        # Synthétiser une réponse à partir de l'historique
                        summary_parts = []
                        for h in self.history[-5:]:
                            if h.observation and h.observation.content:
                                summary_parts.append(h.observation.content[:200])
                        message = "⚠️ La tâche a été interrompue car j'ai détecté une boucle.\n\n" + \
                               "**Ce que j'ai fait:**\n" + \
                               "\n".join([f"- {h.action.tool_name}" for h in self.history[-5:] if h.action.tool_name]) + \
                               ("\n\n**Derniers résultats:**\n" + "\n".join(summary_parts) if summary_parts else "")
                        self._mark_task_failed("loop_detected_forced_final")
                        # Notifier Telegram (non bloquant) — l'utilisateur doit savoir
                        try:
                            from ..autonomy.ops_handlers import _notify_telegram_proactive
                            asyncio.get_running_loop().create_task(
                                _notify_telegram_proactive(
                                    f"⚠️ <b>Lumena bloquée</b>\n"
                                    f"Tâche: <code>{query[:200]}</code>\n"
                                    f"Raison: boucle détectée ({action.tool_name} ×3)"
                                )
                            )
                        except Exception as e:
                            logger.debug(f"Telegram proactive notify: {e}")
                        return message
                    
                    # Compter uniquement les occurrences CONSÉCUTIVES identiques à la fin de la fenêtre.
                    # Si un outil différent a été appelé entre deux appels identiques, le contexte a
                    # changé → on ne compte pas les occurrences précédentes (évite les faux positifs).
                    recent_history = self.action_history[-8:]
                    same_consecutive_hits = 0
                    for _prev_action in reversed(recent_history):
                        if _prev_action == action_key:
                            same_consecutive_hits += 1
                        else:
                            break  # outil différent entre-deux = nouveau contexte
                    same_signature_hits = same_consecutive_hits
                    # Ne déclencher ce garde-fou que si la même action (outil + args) a été appelée
                    # au moins 2 fois CONSÉCUTIVEMENT sans autre outil entre les deux.
                    if same_signature_hits >= 2:
                        logger.warning(
                            "⚠️ Action répétée détectée ({}x): {}",
                            same_signature_hits + 1,
                            action.tool_name,
                        )
                        # Forcer une fin avec résumé
                        _finish_iteration(status="error", error="repeated_action_detected")
                        message = f"⚠️ J'ai détecté une boucle. Voici ce que j'ai fait:\n" + \
                                  "\n".join([f"- {h.action.tool_name}" for h in self.history[-5:] if h.action.tool_name])
                        self._mark_task_failed("repeated_action_detected")
                        # Notifier Telegram (non bloquant)
                        try:
                            from ..autonomy.ops_handlers import _notify_telegram_proactive
                            asyncio.get_running_loop().create_task(
                                _notify_telegram_proactive(
                                    f"⚠️ <b>Lumena bloquée</b>\n"
                                    f"Tâche: <code>{query[:200]}</code>\n"
                                    f"Raison: action répétée ({action.tool_name} ×{same_signature_hits + 1}x)"
                                )
                            )
                        except Exception as e:
                            logger.debug(f"Telegram proactive notify: {e}")
                        return message
                
                self.action_history.append(action_key)
            
            # 3. Créer l'étape
            step = ReActStep(thought=thought, action=action)

            if action.action_type == ActionType.CLARIFY:
                self.history.append(step)
                question = (action.answer or thought.content or "Peux-tu préciser ta demande ?").strip()
                checkpoint_payload = {
                    "phase": "clarify_waiting_io",
                    "iteration": i + 1,
                    "original_query": original_query[:2000],
                    "pending_query": query[:4000],
                    "clarification_question": question[:2000],
                    "history_size": len(self.history),
                }
                self._mark_task_checkpoint(checkpoint_payload)
                self._mark_task_waiting_io("clarification_required", checkpoint=checkpoint_payload)
                self._run_meta["agent_output_warning"] = "clarification_required"
                _finish_iteration(status="ok", summary="clarify_waiting_io")
                return question
            
            # 4. Si c'est une réponse finale, retourner
            if action.action_type == ActionType.FINAL_ANSWER:
                self.history.append(step)
                # ── Plan TODO : bilan ──
                if self._task_plan:
                    # Auto-compléter les tâches de synthèse/résumé (réalisées par FINAL lui-même)
                    _SYNTH_KW = {
                        "synthétis", "synthetis", "résumer", "resumer", "récapitul", "recapitul",
                        "synthèse", "synthese", "conclur", "répondre", "repondre",
                        "fournir une réponse", "présenter les résultats", "presenter les resultats",
                        "confirm", "valider", "vérifi", "verifi",
                        "informer", "inform", "notifier", "communiquer", "communique",
                        "avertir", "signaler", "dire à", "dire a",
                    }
                    for _st in self._task_plan:
                        if not _st.completed:
                            _dl = _st.description.lower()
                            if any(_kw in _dl for _kw in _SYNTH_KW):
                                _st.completed = True
                                _st.completed_by_tool = "FINAL"
                    completed = sum(1 for t in self._task_plan if t.completed)
                    total = len(self._task_plan)
                    logger.info(f"[PLAN BILAN] {completed}/{total} taches completees")
                    for t in self._task_plan:
                        status = "OK" if t.completed else "SKIP"
                        logger.info(f"  [{status}] {t.description}")
                    # Émettre l'état final SANS masquer les SKIP : seules les tâches
                    # réellement accomplies (ou de synthèse) restent completed. Les autres
                    # apparaîtront comme ⏭️ et reflètent la vérité.
                    self._plan_last_emit_state = ""  # reset dédup pour forcer l'émission
                    self._emit_plan_state(context_tool="FINAL")
                    # ── Guard anti-FINAL prématuré : plan largement incomplet ──
                    remaining = total - completed
                    # "Clarification" : la réponse finit par "?" OU contient une liste
                    # d'options (tirets/numéros) typique d'une demande de précisions.
                    _answer_text = action.answer or ""
                    _answer_stripped = _answer_text.strip().rstrip(" \n")
                    _ends_with_question = _answer_stripped.endswith("?")
                    _has_option_list = (
                        "?" in _answer_text
                        and any(
                            p in _answer_text
                            for p in ("\n- ", "\n1.", "\n2.", "\n•", "\n* ")
                        )
                    )
                    _is_clarification = _ends_with_question or _has_option_list
                    # CODE_READ (analyse) : le LLM a lu ce qu'il lui fallait et
                    # rédige sa synthèse → ne pas bloquer son FINAL.
                    _is_read_only = False  # v2: mode lecture seule supprimé
                    if (
                        remaining >= 2
                        and self._plan_guard_retries < 3
                        and not _is_clarification
                        and not _is_read_only
                        and i < self.max_iterations - 2
                    ):
                        self._plan_guard_retries += 1
                        logger.warning(
                            "[PLAN GUARD] FINAL premature bloque: {}/{} taches, iteration {} (retry {}/3)",
                            completed, total, i, self._plan_guard_retries,
                        )
                        self.history.pop()
                        uncompleted = [t.description for t in self._task_plan if not t.completed]
                        query = (
                            f"Requête originale: {original_query}\n\n"
                            "⚠️ Tu as tenté de terminer (FINAL) alors que ton plan n'est PAS terminé!\n"
                            f"Plan: {completed}/{total} tâches complétées. Il reste:\n"
                            + "\n".join(f"- {d}" for d in uncompleted[:5]) + "\n\n"
                            "CONTINUE ton plan. Exécute la prochaine tâche maintenant. "
                            "N'utilise FINAL que quand TOUTES les tâches sont faites ou impossibles."
                        )
                        _finish_iteration(status="ok", summary="premature_final_blocked")
                        continue
                    # ── Guard anti-hallucination : pensée/réponse affirme une action sans outil appelé ──
                    # Détecte quand le THOUGHT ou le ANSWER dit "j'ai créé/planifié/envoyé" mais aucun outil
                    # correspondant n'a été exécuté dans cette session (protection contre hallucination pure).
                    _thought_text = (thought.content or "").lower()
                    _answer_text_guard = (action.answer or "").lower()
                    _combined_text = _thought_text + " " + _answer_text_guard
                    _HALLUCINATION_PATTERNS = [
                        # français
                        (r"\bj[''`]ai (créé|crée|planifié|planifie|enregistré|enregistre|envoyé|envoye|configuré|configure|programmé|programme|executé|execute|ajouté|ajoute|sauvegardé|sauvegarde)\b", ["create_task", "schedule_task", "write_file", "send_message", "discord_send", "discord_send_message", "discord_create_channel", "discord_delete_channel", "discord_edit_channel", "memory_save", "create_file", "telegram_send_message", "telegram_send_document", "generate_website", "serve_website", "edit_website", "create_project", "create_skill", "create_pdf", "create_docx", "create_pptx", "create_xlsx", "create_csv", "create_invoice_pdf", "create_from_template", "create_html", "create_markdown", "create_email_html", "create_ics", "create_vcard", "create_batch_documents", "create_zip", "run_command", "mail_send", "memory_add", "generate_video", "edit_video"]),
                        (r"\bla tâche a été (créée|planifiée|enregistrée|programmée)\b", ["create_task", "schedule_task"]),
                        (r"\bj[''`]ai bien (enregistré|planifié|créé|envoyé|configuré)\b", ["create_task", "schedule_task", "write_file", "send_message", "discord_send", "discord_send_message", "discord_create_channel", "generate_website", "serve_website", "create_skill", "create_pdf", "create_docx", "create_pptx", "create_xlsx", "create_csv", "create_invoice_pdf", "create_from_template", "create_html", "create_markdown", "telegram_send_document", "telegram_send_message", "create_zip", "run_command", "mail_send", "memory_add", "generate_video", "edit_video"]),
                        (r"\bc[''`]est (fait|configuré|planifié|enregistré|créé)\b", ["create_task", "write_file", "send_message", "discord_create_channel", "generate_website", "serve_website", "edit_website", "create_pdf", "create_docx", "create_pptx", "create_xlsx", "create_invoice_pdf", "create_from_template", "generate_video", "edit_video"]),
                        # Discord: doit avoir envoyé un message ou créé un canal pour prétendre avoir agi
                        (r"\bdiscord.{0,30}(animé|anime|géré|gere|organisé|organise|avec succès|avec succes)\b", ["discord_send_message", "discord_send", "discord_create_channel"]),
                        (r"\b(animé|anime).{0,20}discord\b", ["discord_send_message", "discord_send", "discord_create_channel"]),
                        (r"\b(salon|channel|canal).{0,20}(créé|crée|supprimé|supprime)\b", ["discord_create_channel", "discord_delete_channel"]),
                        (r"\b(message|messages|fichier|document|zip).{0,20}(envoyé|envoye|posté|poste|publié|publie)\b", ["discord_send_message", "discord_send", "telegram_send_message", "telegram_send_document", "send_whatsapp_message", "send_message", "mail_send"]),
                        # Apprentissage: doit avoir cherché avant de prétendre avoir appris
                        (r"\bj[''`]ai (appris|découvert|exploré|explore|recherché|recherche|étudié|etudie)\b", ["web_search", "web_search_brave", "ddg_search", "web_fetch", "memory_search", "browser_navigate", "browser_get_content"]),
                        # GitHub : push / créer repo / commit
                        (r"\b(push réussi|push reussi|premier push|repository créé|repo créé|poussé sur github|pushé sur github|commit réussi|commit reussi|fichier poussé)\b", ["github_repo_create", "github_file_write", "github_push_directory"]),
                        # Mail envoyé
                        (r"\b(mail|email|courriel).{0,20}(envoyé|envoye|envoi effectué)\b", ["mail_send", "send_email", "mail_reply_message"]),
                    ]
                    _tools_used_this_session = {h.action.tool_name for h in self.history if h.action and h.action.tool_name}
                    # ── Conversation-aware: inclure les outils des requêtes précédentes ──
                    _conv_tools: set = set()
                    try:
                        _web_ctx = getattr(self.tools, "_web_context", None) or []
                        for _msg in _web_ctx:
                            _msg_content = (_msg.get("content") or "").lower()
                            for _p2, _et2 in _HALLUCINATION_PATTERNS:
                                if re.search(_p2, _msg_content, re.IGNORECASE):
                                    _conv_tools.update(_et2)
                    except Exception:
                        pass
                    _all_known_tools = _tools_used_this_session | _conv_tools
                    # Exclusion : références temporelles au passé ("j'ai créé plus tôt", "que j'avais envoyé hier")
                    # → le LLM parle d'une action passée, pas d'une action de cette session.
                    _TEMPORAL_BYPASS_RE = re.compile(
                        r"\bj[''`']ai\s+\w+(\s+\w+){0,5}\s+(plus\s+t[oô]t|pr[eé]c[eé]demment|avant|hier|la\s+derni[eè]re\s+fois|tout\s+[àa]\s+l[''']heure|tantôt|tantoˆt)|"
                        r"\b(que\s+tu\s+m[''']a(vai[st]|s)\s+demand\w*|comme\s+(demand\w*|convenu)|"
                        r"tout\s+[àa]\s+l[''']instant|juste\s+avant)\b",
                        re.IGNORECASE,
                    )
                    _has_temporal_ref = bool(_TEMPORAL_BYPASS_RE.search(_combined_text))
                    _hallucination_blocked = False
                    if self._premature_final_retries < 2 and not _has_temporal_ref:
                        for _pattern, _expected_tools in _HALLUCINATION_PATTERNS:
                            if re.search(_pattern, _combined_text, re.IGNORECASE):
                                # Vérifie si AU MOINS l'un des outils attendus a été appelé
                                if not any(t in _all_known_tools for t in _expected_tools):
                                    self._premature_final_retries += 1
                                    logger.warning(
                                        "[HALLUCINATION GUARD] Thought affirme une action non exécutée (pattern: {}, outils attendus: {}, outils utilisés: {}) - retry {}/2",
                                        _pattern[:50], _expected_tools, list(_all_known_tools)[:5], self._premature_final_retries,
                                    )
                                    self.history.pop()
                                    query = (
                                        f"Requête originale: {original_query}\n\n"
                                        "⛔ ERREUR CRITIQUE : Tu as déclaré FINAL en affirmant avoir accompli une action "
                                        f"({_pattern[:60]}...) SANS l'avoir réellement exécutée avec un outil!\n\n"
                                        f"Outils que tu as réellement appelés : {list(_tools_used_this_session) or 'AUCUN'}\n\n"
                                        "Tu DOIS maintenant appeler l'outil approprié (ex: create_task, schedule_task, write_file, send_message, etc.) "
                                        "et ATTENDRE l'OBSERVATION de retour avant de conclure. "
                                        "INTERDICTION absolue de prétendre qu'une action est faite sans OBSERVATION."
                                    )
                                    _finish_iteration(status="ok", summary="hallucination_action_blocked")
                                    _hallucination_blocked = True
                                    break
                    if _hallucination_blocked:
                        continue

                    # ── Guard anti-hallucination : tâches critiques marquées SKIP ──
                    _CRITICAL_KW = {
                        "login", "se connecter", "connecter", "logg",
                        "dashboard", "mot de passe", "password", "vérifier accès",
                        "verifier acces", "admin", "authentif", "sign in", "signin",
                    }
                    if self._premature_final_retries < 2:
                        critical_skipped = [
                            t.description for t in self._task_plan
                            if not t.completed
                            and any(_kw in t.description.lower() for _kw in _CRITICAL_KW)
                        ]
                        if critical_skipped:
                            self._premature_final_retries += 1
                            logger.warning(
                                "[PLAN GUARD] Tâches critiques non complétées: {} (retry {}/2)",
                                critical_skipped, self._premature_final_retries,
                            )
                            self.history.pop()
                            query = (
                                f"Requête originale: {original_query}\n\n"
                                "⚠️ Tu as déclaré FINAL sans avoir accompli ces étapes critiques:\n"
                                + "\n".join(f"- {d}" for d in critical_skipped[:5]) + "\n\n"
                                "Tu NE DOIS PAS prétendre que ces étapes sont faites si elles ne le sont pas. "
                                "Exécute-les maintenant (connexion, vérification d'accès, etc.)."
                            )
                            _finish_iteration(status="ok", summary="critical_tasks_incomplete")
                            continue

                    # ── Guard : tâche Discord action (animer/poster) sans envoi réel ──
                    # Quand l'user demande d'animer/poster/envoyer sur Discord, Lumena DOIT
                    # avoir appelé discord_send ou discord_send_message avec succès.
                    # Fetcher des messages ne suffit PAS — il faut ENVOYER.
                    _DISCORD_ACTION_KW = ("anime", "animer", "poste", "poster", "envoie", "envoyer", "publie", "publier")
                    _DISCORD_SEND_TOOLS = {"discord_send", "discord_send_message", "discord_send_embed"}
                    _query_lower = original_query.lower()
                    _is_discord_action = "discord" in _query_lower and any(kw in _query_lower for kw in _DISCORD_ACTION_KW)
                    if _is_discord_action and self._premature_final_retries < 2:
                        _used = {h.action.tool_name for h in self.history if h.action and h.action.tool_name}
                        _used_send = _used & _DISCORD_SEND_TOOLS
                        # Compter les discord_send qui ont RÉUSSI
                        _send_success_count = sum(
                            1 for h in self.history
                            if h.action and h.action.tool_name in _DISCORD_SEND_TOOLS
                            and h.observation and h.observation.success
                        )
                        if _send_success_count == 0:
                            self._premature_final_retries += 1
                            _hint = "Aucun outil d'envoi Discord appelé" if not _used_send else "discord_send a échoué — retenter avec le bon channel"
                            logger.warning(
                                "[DISCORD ACTION GUARD] Tâche Discord FINAL sans envoi réussi ({}) - retry {}/2",
                                _hint, self._premature_final_retries,
                            )
                            self.history.pop()
                            query = (
                                f"Requête originale: {original_query}\n\n"
                                "⛔ Tu n'as PAS encore envoyé de message sur Discord!\n"
                                f"({_hint})\n\n"
                                "Tu DOIS appeler discord_send ou discord_send_message avec le contenu du message "
                                "et un channel_name valide (ex: 'général') pour RÉELLEMENT poster.\n"
                                "discord_list_channels et discord_fetch_messages NE SONT PAS suffisants — "
                                "il faut ENVOYER un message avec discord_send."
                            )
                            _finish_iteration(status="ok", summary="discord_action_no_send")
                            continue

                        # Guard anti-exagération : le FINAL prétend plus d'envois que la réalité
                        _final_text = _combined_text
                        # Compter les channels mentionnés dans la réponse FINAL (#channel-name)
                        # Exclure les headings markdown (##) et les IDs numériques (#9654)
                        _claim_channels_raw = re.findall(
                            r"(?<!\#)#([^\s\*\#\(\)\[\]]{2,40})",
                            _final_text,
                        )
                        _claim_channels = [
                            c.rstrip("*").rstrip(",").rstrip(".")
                            for c in _claim_channels_raw
                            if not c.replace("-", "").replace("_", "").isdigit()
                        ]
                        # Aussi compter les bullet-points décrivant des actions Discord
                        _bullet_action_count = len(re.findall(
                            r"^[\s\-\*]*\*?\*?#.+(?:→|—|:).+(?:initié|lancé|envoyé|partagé|posté|animé|créé|publié|discussion|message|fil|sondage|question)",
                            _final_text, re.MULTILINE | re.IGNORECASE,
                        ))
                        _claim_count = max(len(_claim_channels), _bullet_action_count)

                        # Extraire les noms de salons RÉELLEMENT utilisés depuis les observations
                        _actual_channels = set()
                        for h in self.history:
                            if (h.action and h.action.tool_name in _DISCORD_SEND_TOOLS
                                    and h.observation and h.observation.success):
                                _ch_match = re.search(r"dans #([^\s\(]+)", h.observation.content or "")
                                if _ch_match:
                                    _actual_channels.add(_ch_match.group(1).lower().strip())

                        # BLOQUER le FINAL si claims > réalité (forcer retry)
                        _needs_block = False
                        _mismatch_info = ""
                        if _claim_count > _send_success_count and _send_success_count >= 1:
                            _needs_block = True
                            _mismatch_info = f"FINAL prétend {_claim_count} envois mais seulement {_send_success_count} ont réussi"
                        elif _claim_channels and _actual_channels:
                            _claimed_set = {c.lower().strip("#").strip() for c in _claim_channels}
                            _phantom = _claimed_set - _actual_channels
                            if _phantom:
                                _needs_block = True
                                _mismatch_info = f"salons inventés: {_phantom} (réels: {_actual_channels})"

                        if _needs_block and self._premature_final_retries < 2:
                            self._premature_final_retries += 1
                            logger.warning(
                                "[DISCORD COUNT GUARD] {} — FINAL bloqué, retry {}/2",
                                _mismatch_info, self._premature_final_retries,
                            )
                            _missing = set(c.lower() for c in _claim_channels) - _actual_channels if _claim_channels else set()
                            _missing_list = ", ".join(f"#{c}" for c in sorted(_missing)) if _missing else "les salons annoncés"
                            self.history.pop()
                            query = (
                                f"Requête originale: {original_query}\n\n"
                                f"⛔ MENSONGE DÉTECTÉ dans ta réponse ! Tu as prétendu avoir posté dans "
                                f"{_claim_count} salons mais tu n'as réellement envoyé que "
                                f"{_send_success_count} message(s) ({', '.join(f'#{c}' for c in sorted(_actual_channels)) if _actual_channels else 'inconnu'}).\n\n"
                                f"Tu DOIS maintenant RÉELLEMENT envoyer des messages dans {_missing_list}.\n"
                                f"Appelle discord_send avec channel_name pour CHAQUE salon manquant.\n"
                                f"NE VA PAS à FINAL avant d'avoir RÉELLEMENT envoyé tous les messages."
                            )
                            _finish_iteration(status="ok", summary="discord_count_guard_blocked")
                            continue

                # ── Guard anti-hallucination sans plan (quand _task_plan est vide) ──
                # Même logique que le guard dans if self._task_plan, mais exécuté
                # quand le LLM n'a pas émis de PLAN: (requêtes simples).
                if not self._task_plan:
                    _ht = (thought.content or "").lower()
                    _at = (action.answer or "").lower()
                    _ct = _ht + " " + _at
                    _tu = {h.action.tool_name for h in self.history if h.action and h.action.tool_name}
                    _HP_NOPLAN = [
                        (r"\bj[''`]ai (créé|crée|planifié|planifie|enregistré|enregistre|envoyé|envoye|configuré|configure|programmé|programme|executé|execute|ajouté|ajoute|sauvegardé|sauvegarde)\b",
                         ["create_task", "schedule_task", "write_file", "send_message", "discord_send", "discord_send_message", "discord_create_channel", "memory_save", "create_file", "telegram_send_message", "generate_website", "serve_website", "edit_website", "create_project", "create_skill", "create_pdf", "create_docx", "create_pptx", "create_xlsx", "create_csv", "create_invoice_pdf", "create_from_template", "create_html", "create_markdown", "create_email_html", "create_ics", "create_vcard", "create_batch_documents"]),
                        (r"\bj[''`]ai bien (enregistré|planifié|créé|envoyé|configuré)\b",
                         ["create_task", "schedule_task", "write_file", "send_message", "discord_send", "discord_send_message", "generate_website", "serve_website", "create_skill", "create_pdf", "create_docx"]),
                        (r"\bc[''`]est (fait|configuré|planifié|enregistré|créé)\b",
                         ["create_task", "write_file", "send_message", "discord_create_channel", "generate_website", "serve_website", "edit_website", "create_pdf"]),
                        (r"\b(push réussi|push reussi|premier push|repository créé|repo créé|poussé sur github|commit réussi|commit reussi)\b",
                         ["github_repo_create", "github_file_write", "github_push_directory"]),
                        (r"\b(mail|email|courriel).{0,20}(envoyé|envoye|envoi effectué)\b",
                         ["mail_send", "send_email", "mail_reply_message"]),
                    ]
                    # Conversation-aware tools (même logique que le guard principal)
                    _conv_tools_np: set = set()
                    try:
                        _web_ctx_np = getattr(self.tools, "_web_context", None) or []
                        for _msg_np in _web_ctx_np:
                            _msg_c_np = (_msg_np.get("content") or "").lower()
                            for _p3, _et3 in _HP_NOPLAN:
                                if re.search(_p3, _msg_c_np, re.IGNORECASE):
                                    _conv_tools_np.update(_et3)
                    except Exception:
                        pass
                    _all_known_np = _tu | _conv_tools_np
                    _hb_noplan = False
                    # Bypass: si un outil de création non listé dans _HP_NOPLAN a été utilisé,
                    # le LLM rapporte un vrai résultat — ne pas bloquer
                    _all_hp_expected = {t for _, _et0 in _HP_NOPLAN for t in _et0}
                    _READONLY_TOOLS = {
                        "read_file", "web_search", "search_web", "read_url", "memory_recall",
                        "memory_retrieve", "get_context", "list_files", "list_directory",
                        "search_memory", "retrieve_memory", "get_weather",
                    }
                    _unlisted_action_tools = _tu - _READONLY_TOOLS - _all_hp_expected
                    if _unlisted_action_tools:
                        _hb_noplan = False  # outils d'action utilisés → claims probablement légitimes
                        _has_temporal_ref_np = True  # skip HP guard (action réelle)
                    else:
                        _has_temporal_ref_np = bool(re.search(
                        r"\bj[''`']ai\s+\w+(\s+\w+){0,5}\s+(plus\s+t[oô]t|pr[eé]c[eé]demment|avant|hier|la\s+derni[eè]re\s+fois|tout\s+[àa]\s+l[''']heure|tantôt|tantoˆt)|"
                        r"\b(que\s+tu\s+m[''']a(vai[st]|s)\s+demand\w*|comme\s+(demand\w*|convenu)|"
                        r"tout\s+[àa]\s+l[''']instant|juste\s+avant)\b",
                        _ct, re.IGNORECASE,
                    ))
                    if self._premature_final_retries < 2 and not _has_temporal_ref_np:
                        for _p, _et in _HP_NOPLAN:
                            if re.search(_p, _ct, re.IGNORECASE) and not any(t in _all_known_np for t in _et):
                                self._premature_final_retries += 1
                                logger.warning(
                                    "[HALLUCINATION GUARD] Action non exécutée (sans plan): {} — retry {}/2",
                                    _p[:50], self._premature_final_retries,
                                )
                                self.history.pop()
                                query = (
                                    f"Requête originale: {original_query}\n\n"
                                    "⛔ ERREUR CRITIQUE: Tu as déclaré FINAL en affirmant avoir accompli une action "
                                    "SANS l'avoir exécutée avec un outil!\n\n"
                                    f"Outils appelés: {list(_tu) or 'AUCUN'}\n\n"
                                    "Tu DOIS appeler l'outil approprié (write_file, send_message, etc.) "
                                    "et ATTENDRE l'OBSERVATION avant de conclure."
                                )
                                _finish_iteration(status="ok", summary="hallucination_action_blocked")
                                _hb_noplan = True
                                break
                    if _hb_noplan:
                        continue

                answer = action.answer or ""
                finish_reason = self._last_llm_meta.get("finish_reason")
                self._run_meta["agent_final_finish_reason"] = finish_reason

                # ── Guard anti-thought-leak : le LLM a mis sa réflexion dans ACTION_INPUT au lieu de la réponse ──
                # Cela arrive quand ACTION_INPUT est vide → fallback sur thought_content (ligne 1881)
                # NOTE: Grok met souvent la vraie réponse dans THOUGHT avec ACTION_INPUT vide.
                # On ne doit PAS considérer ça comme un leak si le contenu ne ressemble pas à
                # de la réflexion interne (sinon on gaspille des itérations en re-prompting).
                _answer_lower = (answer or "").lower().lstrip()
                _INTERNAL_PREFIXES = (
                    "l'utilisateur me demande",
                    "l'utilisateur demande",
                    "l'utilisateur souhaite",
                    "l'utilisateur veut",
                    "je dois maintenant",
                    "je vais maintenant synthétiser",
                    "je vais maintenant formuler",
                    "je vais maintenant fournir",
                    "je vais maintenant résumer",
                    "je dois analyser",
                    "j'ai exécuté les",
                    "j'ai déjà exécuté",
                    "j'ai déjà effectué une recherche",
                    "the user is asking",
                    "the user wants",
                    "the user asked",
                    "i need to now",
                    "i should now",
                    "let me analyze",
                    "let me now",
                )
                _is_reasoning_prefix = any(_answer_lower.startswith(p) for p in _INTERNAL_PREFIXES)
                _thought_leaked = bool(answer) and (
                    _is_reasoning_prefix
                    or (
                        # answer == thought ET contient des marqueurs de réflexion interne
                        bool(thought.content)
                        and answer.strip() == thought.content.strip()
                        and any(k in _answer_lower for k in (
                            "l'utilisateur", "je dois ", "je vais ", "il faut que je",
                            "the user ", "i need to", "i should ",
                        ))
                    )
                )
                if _thought_leaked and self._thought_leak_repairs < 2:
                    self._thought_leak_repairs += 1
                    logger.warning(
                        f"⚠️ THOUGHT leaké comme réponse finale (tentative {self._thought_leak_repairs}/2) - reformulation demandée"
                    )
                    self.history.pop()
                    query = (
                        f"Requête originale: {original_query}\n\n"
                        "⚠️ Tu as mis ta réflexion interne dans ACTION_INPUT au lieu d'une vraie réponse.\n"
                        "Maintenant écris ta RÉPONSE DIRECTE à l'utilisateur dans ACTION_INPUT:\n\n"
                        "THOUGHT: (bref)\n"
                        "ACTION: FINAL\n"
                        "ACTION_INPUT: [ta réponse complète ici, en tutoyant/vouvoyant selon le contexte]"
                    )
                    _finish_iteration(status="ok", summary="thought_leaked_repair")
                    continue

                # Si la réponse est vide ou juste des points, utiliser la dernière observation
                if not answer or answer.strip() in ["", "...", "......", "Je n'ai pas de réponse", "Je n'ai pas de réponse."]:
                    # Chercher la dernière observation de recherche
                    last_observation = None
                    for h in reversed(self.history):
                        if h.observation and ("Recherche" in h.observation.content or "💰" in h.observation.content):
                            last_observation = h.observation.content
                            break
                    
                    if last_observation:
                        # Extraire les informations clés de l'observation
                        _finish_iteration(status="ok", summary="final_from_last_observation")
                        message = f"📊 Voici ce que j'ai trouvé :\n\n{last_observation[:3000]}"
                        self._mark_task_done("final_from_last_observation")
                        return message

                # Skip repair si stagnation déjà détectée — le FINAL est volontaire
                should_repair = (
                    _stagnation_streak == 0
                    and self._looks_incomplete_final_answer(answer, self._last_llm_meta)
                )

                if should_repair:
                    if self._final_repair_attempts < self.max_final_repair_attempts:
                        self._final_repair_attempts += 1
                        self._run_meta["agent_repair_attempts"] = self._final_repair_attempts
                        # Sauvegarder la réponse originale pour rollback si le repair échoue
                        self._pre_repair_answer = answer
                        logger.warning(
                            "⚠️ FINAL potentiellement tronqué (finish_reason={}) - tentative de réparation {}/{}",
                            finish_reason,
                            self._final_repair_attempts,
                            self.max_final_repair_attempts,
                        )
                        query = (
                            f"Requête originale: {original_query}\n\n"
                            "⚠️ Ta dernière réponse FINAL semble incomplète. "
                            "Renvoie une réponse complète et cohérente. "
                            "Respecte STRICTEMENT le format THOUGHT/ACTION/ACTION_INPUT et utilise ACTION: FINAL."
                        )
                        _finish_iteration(status="ok", summary="final_repair_retry")
                        continue

                    self._run_meta["agent_output_incomplete"] = True
                    self._run_meta["agent_output_warning"] = (
                        f"final_answer_potentially_incomplete (finish_reason={finish_reason})"
                    )
                    self._run_meta["agent_repair_attempts"] = self._final_repair_attempts
                    _finish_iteration(status="error", error=self._run_meta["agent_output_warning"])
                    message = answer if answer else "Je n'ai pas trouvé de réponse pertinente."
                    self._mark_task_failed(self._run_meta["agent_output_warning"])
                    return message

                self._run_meta["agent_repair_attempts"] = self._final_repair_attempts
                _finish_iteration(status="ok", summary="final_answer_ready")
                message = answer if answer else "Je n'ai pas trouvé de réponse pertinente."
                # P3 — Token streaming: émettre la réponse finale par chunks pour le SSE
                # Émet 2 mots à la fois avec 25ms entre chaque chunk.
                # Le poll SSE (50ms en mode token) capture chaque chunk quasi-unitairement
                # → effet "Lumena écrit" fluide au lieu de blocs saccadés.
                import time as _time_mod
                _lines = message.split('\n')
                _first_chunk = True
                for _li, _line in enumerate(_lines):
                    if _li > 0:
                        logger.debug("[FINAL_TOKEN]{}", "\n")
                        _time_mod.sleep(0.015)
                    if not _line:
                        continue
                    _words = _line.split(' ')
                    for _wi in range(0, len(_words), 2):
                        _chunk = " ".join(_words[_wi:_wi + 2])
                        if not _first_chunk and _wi > 0:
                            _chunk = " " + _chunk
                        elif not _first_chunk and _wi == 0:
                            pass  # début de ligne, pas d'espace prefix
                        logger.debug("[FINAL_TOKEN]{}", _chunk)
                        _first_chunk = False
                        _time_mod.sleep(0.025)  # 25ms par chunk = typing fluide
                self._mark_task_done(message)
                return message
            
            # 5. Sinon, exécuter l'outil
            if action.action_type == ActionType.TOOL_CALL and action.tool_name:
                # Notifier le step_callback (ex: voix) avant l'exécution de l'outil
                if self.step_callback:
                    try:
                        self.step_callback(action.tool_name, action.tool_args or {})
                    except Exception as e:
                        logger.debug(f"Step callback: {e}")
                # Propager le budget temps restant au HandlerContext
                if hasattr(self, '_loop_start_time') and hasattr(self.tools, '_v2_context'):
                    from time import perf_counter as _pc
                    _elapsed = _pc() - self._loop_start_time
                    _total = float(self.timeout_seconds or 600) + getattr(self, '_tool_time_total', 0.0)
                    self.tools._v2_context.budget_seconds = max(0.0, _total - _elapsed)
                # Mesurer le temps outil pour exclure du timeout de raisonnement
                from .caller_context import REACT as _CALLER_REACT
                _tool_exec_start = perf_counter()
                observation = await self.tools.execute(
                    action.tool_name, 
                    action.tool_args,
                    caller=_CALLER_REACT,
                )
                _tool_exec_duration = perf_counter() - _tool_exec_start
                # Repousser la deadline du temps passé dans l'outil
                # → seul le temps de raisonnement (LLM) compte pour le timeout
                if hasattr(self, '_timeout_deadline'):
                    self._timeout_deadline += _tool_exec_duration
                    self._tool_time_total = getattr(self, '_tool_time_total', 0.0) + _tool_exec_duration
                # Injecter l'avertissement de stagnation dans l'observation si détecté
                if _stagnation_warning and observation.content:
                    observation = Observation(
                        content=observation.content + _stagnation_warning,
                        success=observation.success,
                    )
                # Injecter l'avertissement d'hallucination dans l'observation si récidive
                if _halluc_warning and observation.content:
                    observation = Observation(
                        content=observation.content + _halluc_warning,
                        success=observation.success,
                    )
                step.observation = observation

                # ── P1.7: Auto-expand filtre après exécution d'outil ──
                if hasattr(self.tools, '_allowed_tools') and self.tools._allowed_tools is not None:
                    _executed_cat = self.tools._tool_modules.get(action.tool_name)
                    if _executed_cat:
                        _TOOL_TRANSITIONS = {
                            "browser": {"files", "documents"},
                            "files":   {"system", "mail"},
                            "web":     {"browser", "files", "documents"},
                            "mail":    {"files", "social"},
                            "system":  {"files", "mail"},
                            "project": {"git", "files", "codebase"},
                            "social":  {"web", "files"},
                            "automation": {"web", "system", "mail"},
                        }
                        _expand_cats = _TOOL_TRANSITIONS.get(_executed_cat, set())
                        if _expand_cats:
                            for _tn, _tc in self.tools._tool_modules.items():
                                if _tc in _expand_cats:
                                    self.tools._allowed_tools.add(_tn)
                            self.tools._tools_desc_cache = None

                # ── Multi-action : exécuter les actions en queue ──
                # Levier 1: parallélisation automatique quand toutes les actions sont read-only.
                _pending = getattr(self, '_pending_multi_actions', [])
                if _pending and observation.success:
                    _combined_obs = [observation.content or ""]
                    # Set d'outils considérés read-only (safe à paralléliser).
                    _READ_ONLY_TOOLS = {
                        "read_file", "read_files_batch", "list_files", "list_dir",
                        "grep", "grep_search", "grep_batch",
                        "web_search", "web_fetch", "memory_search", "semantic_search",
                        "get_file_info", "find_files", "scan_project",
                    }
                    _all_read_only = (
                        (action.tool_name or "") in _READ_ONLY_TOOLS
                        and all((_n or "") in _READ_ONLY_TOOLS for _n, _ in _pending)
                        and len(_pending) >= 1
                    )
                    if _all_read_only:
                        # ── Exécution PARALLÈLE ──
                        logger.info("⚡ Multi-action PARALLÈLE ({} actions read-only)", len(_pending))
                        _par_start = perf_counter()

                        from .caller_context import REACT as _CALLER_REACT_PAR
                        async def _run_one(_n: str, _a: dict):
                            try:
                                return _n, await self.tools.execute(_n, _a, caller=_CALLER_REACT_PAR), None
                            except Exception as _e:
                                return _n, None, _e

                        _results = await asyncio.gather(
                            *(_run_one(_n, _a) for _n, _a in _pending),
                            return_exceptions=False,
                        )
                        _par_dur = perf_counter() - _par_start
                        if hasattr(self, '_timeout_deadline'):
                            # Temps parallèle ≈ max(individuels) ≈ _par_dur (pas somme).
                            self._timeout_deadline += _par_dur
                            self._tool_time_total = getattr(self, '_tool_time_total', 0.0) + _par_dur
                        for _n, _obs, _err in _results:
                            if _err is not None:
                                _combined_obs.append(f"[{_n}] Erreur: {_err}")
                            else:
                                _combined_obs.append(f"[{_n}] {_obs.content or ''}")
                                if self._task_plan and getattr(_obs, 'success', False):
                                    self._update_plan_progress(_n, {}, _obs.content or "", i)
                    else:
                        # ── Exécution SÉQUENTIELLE (legacy : abort-on-fail pour writes) ──
                        _abort_multi = False
                        for _ma_name, _ma_args in _pending:
                            if _abort_multi:
                                logger.warning("⚡ Multi-action '{}' annulé (échec précédent)", _ma_name)
                                _combined_obs.append(f"[{_ma_name}] Annulé (action précédente échouée)")
                                continue
                            try:
                                logger.info("⚡ Multi-action queue: exécution de '{}' (args: {})", _ma_name, list(_ma_args.keys()))
                                from .caller_context import REACT as _CALLER_REACT_MA
                                _ma_start = perf_counter()
                                _ma_obs = await self.tools.execute(_ma_name, _ma_args, caller=_CALLER_REACT_MA)
                                _ma_dur = perf_counter() - _ma_start
                                if hasattr(self, '_timeout_deadline'):
                                    self._timeout_deadline += _ma_dur
                                    self._tool_time_total = getattr(self, '_tool_time_total', 0.0) + _ma_dur
                                _combined_obs.append(f"[{_ma_name}] {_ma_obs.content or ''}")
                                if self._task_plan and _ma_obs.success:
                                    self._update_plan_progress(_ma_name, _ma_args, _ma_obs.content or "", i)
                                # Si un outil échoue, annuler les suivants du même type
                                if not _ma_obs.success:
                                    _abort_multi = True
                                    logger.warning("⚡ Multi-action '{}' échoué — annulation des suivants", _ma_name)
                            except Exception as _ma_err:
                                logger.warning("Multi-action '{}' échoué: {}", _ma_name, _ma_err)
                                _combined_obs.append(f"[{_ma_name}] Erreur: {_ma_err}")
                                _abort_multi = True
                    self._pending_multi_actions = []
                    observation = Observation(
                        content="\n\n".join(_combined_obs),
                        success=observation.success,
                    )
                    step.observation = observation

                # ── Plan TODO : mise a jour progression ──
                if self._task_plan and observation.success:
                    self._update_plan_progress(
                        action.tool_name or "", action.tool_args,
                        observation.content or "", i,
                    )
                    # parallel_tools: NE PAS propager aux sous-outils individuellement
                    # (causerait N completions en cascade en < 1ms pour chaque sous-outil)

                # Guard web/browser: casser les boucles de navigation qui échouent en série.
                browser_tools = {
                    "browser_start", "browser_navigate", "browser_search_google",
                    "browser_type", "browser_click", "browser_dom_state",
                }
                if action.tool_name in browser_tools:
                    obs_lower = (observation.content or "").lower()
                    browser_failed = (
                        not observation.success
                        or "erreur" in obs_lower
                        or "timeout" in obs_lower
                        or "non demarre" in obs_lower
                        or "aucune page active" in obs_lower
                        or "0 resultats" in obs_lower
                    )
                    browser_fail_streak = browser_fail_streak + 1 if browser_failed else 0
                    if browser_fail_streak >= 4:
                        logger.warning(
                            "⚠️ Boucle browser en échec détectée ({} échecs) - arrêt contrôlé",
                            browser_fail_streak,
                        )
                        _finish_iteration(status="error", error="browser_fail_streak")
                        message = (
                            "⚠️ J'ai interrompu la tâche car le navigateur boucle en échec.\n\n"
                            f"Dernière observation: {observation.content[:500]}\n\n"
                            "Conseil: relancer avec une instruction plus simple (ex: 'ouvre google.com puis cherche ...') "
                            "ou vérifier que Playwright est bien installé (playwright install chromium)."
                        )
                        self._mark_task_failed("browser_fail_streak")
                        return message
                else:
                    browser_fail_streak = 0

                # Guard web_fetch: eviter les boucles longues sur sites anti-bot / SSL.
                if action.tool_name == "web_fetch":
                    obs_lower = (observation.content or "").lower()
                    fetch_failed = (
                        not observation.success
                        or "403" in obs_lower
                        or "forbidden" in obs_lower
                        or "dh_key_too_small" in obs_lower
                        or "ssl" in obs_lower
                        or "erreur fetch" in obs_lower
                    )
                    web_fetch_fail_streak = web_fetch_fail_streak + 1 if fetch_failed else 0
                    if web_fetch_fail_streak >= 2:
                        logger.warning(
                            "⚠️ web_fetch échoue en série ({} fois) - arrêt contrôlé",
                            web_fetch_fail_streak,
                        )
                        _finish_iteration(status="error", error="web_fetch_fail_streak")

                        last_search_obs = None
                        for h in reversed(self.history):
                            if not h.observation or not h.observation.content:
                                continue
                            txt = h.observation.content
                            if "Résultats DuckDuckGo" in txt or "🔍 Recherche:" in txt:
                                last_search_obs = txt[:1800]
                                break

                        message = (
                            "⚠️ J'ai arrêté la boucle: `web_fetch` échoue à répétition sur des protections anti-bot/SSL.\n\n"
                            "Je te propose les meilleurs résultats déjà trouvés plutôt que de boucler."
                        )
                        if last_search_obs:
                            message += f"\n\n{last_search_obs}"

                        self._mark_task_failed("web_fetch_fail_streak")
                        return message
                else:
                    web_fetch_fail_streak = 0

                # --- Guard: detect repeated list_directory on same path ---
                if action.tool_name == "list_directory":
                    listed_path = str(action.tool_args.get("path", "")).strip().lower()
                    if listed_path in _listed_dirs:
                        # Vérifier si des outils mutatifs ont déjà réussi (création déjà faite)
                        _write_tools = {
                            "write_file", "edit_file", "create_project", "create_skill",
                            "create_pdf", "create_docx", "create_xlsx", "create_pptx",
                            "website_build", "generate_website", "write_website_files",
                            "edit_website",
                        }
                        _already_created = any(
                            h.action.tool_name in _write_tools
                            and h.observation and h.observation.success
                            for h in self.history
                        )
                        if _already_created:
                            # Création déjà faite — list_directory est de la navigation légitime
                            observation.content += (
                                "\n\n⚠️ RAPPEL: tu as déjà exploré ce chemin. "
                                "Avance vers l'étape suivante."
                            )
                        else:
                            # Détecter si la requête demande de CRÉER des fichiers (pas de les chercher)
                            _creation_keywords = (
                                "créer", "creer", "cree", "crée", "créé", "génère", "genere", "rédige", "redige",
                                "écris", "ecris", "prépare", "prepare", "fais", "produis", "structure",
                                "create", "write", "generate", "make", "build",
                            )
                            query_lower = original_query.lower()
                            user_wants_creation = any(kw in query_lower for kw in _creation_keywords)
                            if user_wants_creation:
                                observation.content += (
                                    "\n\n⚠️ STOP EXPLORATION: tu as DEJA explore ce chemin et l'utilisateur "
                                    "te demande de CREER des fichiers. Arrete list_directory MAINTENANT.\n"
                                    "ACTION OBLIGATOIRE: utilise write_file pour creer chaque fichier demandé "
                                    "(un par un, PAS parallel_tools). Puis utilise telegram_send_document ou send_whatsapp_document si "
                                    "l'utilisateur veut les recevoir."
                                )
                            else:
                                observation.content += (
                                    "\n\n⚠️ RAPPEL: tu as DEJA explore ce chemin. "
                                    "Si le fichier cherche n'est PAS la, DIS-LE HONNETEMENT a l'utilisateur avec ACTION: FINAL. "
                                    "NE CREE PAS de fichier invente. Ne refais PAS list_directory sur un chemin deja vu."
                                )
                        logger.warning(f"Repeated list_directory on: {listed_path}")
                    _listed_dirs.add(listed_path)

                # --- Guard: detect write_file after "not found" (anti-hallucination) ---
                if action.tool_name == "write_file":
                    # Compteur proactif : nudge vers generate_website après 2+ writes web
                    _wf_path_str = str(action.tool_args.get("path", "") or "")
                    if any(_wf_path_str.endswith(ext) for ext in ('.html', '.css', '.js')):
                        _web_writes_count += 1
                        if _web_writes_count >= 2:
                            observation.content = (observation.content or "") + (
                                "\n\n💡 Tu écris plusieurs fichiers web individuellement. "
                                "L'outil `generate_website` peut créer un site complet "
                                "(HTML+CSS+JS) en un seul appel, avec validation intégrée. "
                                "Utilise-le plutôt que des write_file séparés."
                            )
                if action.tool_name == "write_file" and len(self.history) >= 1:
                    recent_obs = [
                        h.observation.content.lower()
                        for h in self.history[-3:]
                        if h.observation and h.observation.content
                    ]
                    not_found_signals = ("non trouvé", "pas trouvé", "not found", "aucun fichier")
                    had_not_found = any(
                        sig in obs for obs in recent_obs for sig in not_found_signals
                    )
                    if had_not_found:
                        observation.content += (
                            "\n\n⚠️ ATTENTION: Tu viens de CREER un fichier alors que les etapes precedentes "
                            "indiquaient 'non trouve'. Si l'utilisateur demandait de TROUVER ou ENVOYER un fichier "
                            "(pas d'en creer un), tu aurais du repondre honnetement avec ACTION: FINAL."
                        )
                        logger.warning("write_file after not_found detected — possible hallucination")

                # Injection guidance anti-boucle lente (fenêtre 10 actions)
                if self._pending_loop_guidance:
                    observation.content = (observation.content or "") + "\n\n" + self._pending_loop_guidance
                    logger.debug("⚠️ Guidance anti-boucle injectée dans observation")
                    self._pending_loop_guidance = None

                # FIX: Supprimé le '...' trompeur qui faisait croire au LLM que le contenu était tronqué
                obs_preview = observation.content[:500]
                logger.debug(f"Observation: {obs_preview}{'[...log truncated]' if len(observation.content) > 500 else ''}")

                # ── Emit file_read events for UI file viewer ──
                if action.tool_name == "read_file":
                    _file_path = (action.tool_args or {}).get("path", "")
                    _obs_text = observation.content or ""
                    # Extraire le nombre de lignes du header (ex: "(lignes 1-100/745)")
                    import re as _re_fr
                    _lines_m = _re_fr.search(r'\(lignes? ([\d-]+/\d+)\)', _obs_text)
                    _lines_info = _lines_m.group(1) if _lines_m else ""
                    _preview = _obs_text[:2000] if len(_obs_text) > 2000 else _obs_text
                    logger.info("[file_read] {}|{}|{}", _file_path, _lines_info, _preview)

                # --- Guard: après échec parallel_tools (args directs), forcer appel direct ---
                if action.tool_name == "parallel_tools" and "args directs" in (observation.content or ""):
                    logger.warning("⚠️ parallel_tools avec args directs — redirect vers appel direct")
                    self.history.append(step)
                    query = (
                        f"Requête originale: {original_query}\n\n"
                        f"Observation: {observation.content}\n\n"
                        "⚠️ parallel_tools a ÉCHOUÉ car tu as envoyé des arguments d'outil directement.\n"
                        "Tu DOIS appeler chaque outil UN PAR UN avec ACTION: discord_send (ou l'outil voulu).\n"
                        "NE TENTE PAS parallel_tools à nouveau.\n"
                        "Exemple:\n"
                        "ACTION: discord_send\n"
                        'ACTION_INPUT: {"channel_name": "💬-général", "content": "Mon message"}'
                    )
                    _finish_iteration(status="ok", summary="parallel_tools_direct_args_redirect")
                    continue

                # --- Guard: après échec parallel_tools, forcer séquentialisation ---
                if action.tool_name == "parallel_tools" and "outil(s) non autorise(s) en parallele" in (observation.content or ""):
                    logger.warning("⚠️ parallel_tools a rejeté des outils non autorisés — injection de guidance séquentielle")
                    # Extraire dynamiquement les outils rejetés depuis le message d'erreur
                    import re as _re
                    _rej_match = _re.search(r"outil\(s\) non autorise\(s\) en parallele: ([^\n.⚠]+)", observation.content or "")
                    _rejected_names = _rej_match.group(1).strip() if _rej_match else "les outils rejetés"
                    _tool_list = [t.strip() for t in _rejected_names.split(",") if t.strip()]
                    _first_tool = _tool_list[0] if _tool_list else "l'outil"
                    _guidance_lines = "\n".join(f"- ACTION: {t}" for t in _tool_list)
                    self.history.append(step)
                    query = (
                        f"Requête originale: {original_query}\n\n"
                        f"Observation: {observation.content}\n\n"
                        f"⚠️ parallel_tools a ÉCHOUÉ car {_rejected_names} ne sont PAS autorisés en parallèle.\n"
                        f"Tu DOIS maintenant appeler chaque outil UN PAR UN:\n"
                        f"{_guidance_lines}\n"
                        f"NE TENTE PAS parallel_tools à nouveau. NE VA PAS à FINAL sans avoir RÉELLEMENT exécuté les outils."
                    )
                    _finish_iteration(status="ok", summary="parallel_tools_rejected_sequential_redirect")
                    continue
            
            # 6. Ajouter à l'historique
            self.history.append(step)

            # 6.1 Guard: progression du plan TODO
            if self._task_plan:
                completed_count = sum(1 for t in self._task_plan if t.completed)
                if completed_count == self._last_completed_task_count:
                    self._iterations_without_progress += 1
                    # Outil réussi (✅) = progression partielle, ralentir le compteur
                    if step.observation and step.observation.content and "\u2705" in step.observation.content:
                        self._iterations_without_progress = max(0, self._iterations_without_progress - 1)
                else:
                    self._iterations_without_progress = 0
                    self._last_completed_task_count = completed_count

                # Seuil dynamique: plans avec navigateur browser ou debug/test ont besoin de plus d'espace
                _has_browser = any(
                    h.action and h.action.tool_name
                    and h.action.tool_name.startswith("browser_")
                    for h in self.history
                )
                _has_debug = any(
                    h.action and h.action.tool_name
                    and h.action.tool_name in ("test_and_fix", "run_command", "edit_file", "grep_search")
                    for h in self.history
                )
                _needs_more_space = _has_browser or _has_debug
                _guard_limit = 16 if _needs_more_space else 10
                _warn_limit = 12 if _needs_more_space else 7

                if self._iterations_without_progress >= _guard_limit:
                    logger.warning("[PLAN GUARD] Aucune progression en {} iterations, FINAL force", _guard_limit)
                    _finish_iteration(status="error", error=f"plan_no_progress_{_guard_limit}_iter")
                    done_desc = ", ".join(t.description for t in self._task_plan if t.completed)
                    # Inclure le dernier resultat d'outil si positif
                    last_obs_ctx = ""
                    if step.observation and step.observation.content and "\u2705" in step.observation.content:
                        last_obs_ctx = "\n\n" + step.observation.content[:500]
                    message = (
                        "⚠️ Je n'ai pas pu progresser sur mon plan. "
                        f"Voici ce que j'ai accompli : {done_desc}" if done_desc
                        else "⚠️ Je n'ai pas pu avancer sur le plan de travail."
                    )
                    message += last_obs_ctx
                    self._mark_task_failed(f"plan_no_progress_{_guard_limit}_iter")
                    return message

                if self._iterations_without_progress >= _warn_limit:
                    next_task = next((t for t in self._task_plan if not t.completed), None)
                    plan_stag_msg = (
                        "\n\n[SYSTEME] ATTENTION: Aucune progression sur ton plan depuis plusieurs iterations. "
                        "Passe a l'action suivante ou termine avec FINAL si la tache est impossible."
                    )
                    if next_task:
                        plan_stag_msg += f"\nPROCHAINE TACHE A FAIRE: {next_task.description}"
                    if step.observation:
                        step.observation = Observation(
                            content=(step.observation.content or "") + plan_stag_msg,
                            success=step.observation.success,
                        )

            # 7. Mettre à jour la requête avec l'observation (plus de contexte)
            obs_text = step.observation.content[:2000] if step.observation else "Pas d'observation"  # Augmenté

            if (
                action.tool_name == "write_file"
                and step.observation
                and not step.observation.success
                and (
                    "patch strict" in step.observation.content.lower()
                    or "fichier existant" in step.observation.content.lower()
                    or "fichier existe" in step.observation.content.lower()
                )
            ):
                query = (
                    f"Requête originale: {original_query}\n"
                    f"Observation: {obs_text}\n\n"
                    "Le fichier existe déjà. Action suivante obligatoire: utilise edit_file ou apply_patch "
                    "avec modification ciblée (pas write_file)."
                )
                _finish_iteration(status="ok", summary="write_file_to_patch_fallback")
                continue

            if action.tool_name == "read_file" and step.observation and "[...SUITE DISPONIBLE:" in step.observation.content:
                path_for_next = action.tool_args.get("path", "")
                current_end = action.tool_args.get("end_line")
                try:
                    current_end_int = int(current_end) if current_end is not None else 1000
                except Exception:
                    current_end_int = 1000
                next_start = current_end_int + 1
                next_end = next_start + 999
                query = (
                    f"Requête originale: {original_query}\n"
                    f"Observation de l'action précédente ({action.tool_name}): {obs_text}\n\n"
                    f"Le fichier est partiel. Continue la lecture avec read_file(path='{path_for_next}', "
                    f"start_line={next_start}, end_line={next_end}) ou passe à l'action suivante si le contexte est suffisant."
                )
                _finish_iteration(status="ok", summary="continue_paginated_read")
                continue

            # Pour les projets web, rappeler les fichiers créés et restants
            files_reminder = ""
            is_web_request = False
            web_request_checker = getattr(self, "_is_web_request", None)
            if callable(web_request_checker):
                try:
                    is_web_request = bool(web_request_checker(original_query))
                except Exception:
                    is_web_request = bool(ReActLoop._is_web_request(original_query))
            else:
                is_web_request = bool(ReActLoop._is_web_request(original_query))

            if is_web_request:
                created_files = [h.action.tool_args.get("path", "") for h in self.history if h.action.tool_name == "write_file"]
                has_html = any(".html" in f for f in created_files)
                has_css = any(".css" in f for f in created_files)
                has_js = any(".js" in f for f in created_files)
                
                files_reminder = f"""
Fichiers web créés: {', '.join(created_files) if created_files else 'Aucun'}
Fichiers web potentiellement manquants: {'index.html ' if not has_html else ''}{'style.css ' if not has_css else ''}{'script.js' if not has_js else ''}
"""
            
            query = f"""Requête originale: {original_query}
{files_reminder}
Observation de l'action précédente ({action.tool_name}): {obs_text}

Continue à répondre à la question initiale. Si tu as créé les 3 fichiers (HTML, CSS, JS), utilise ACTION: FINAL."""
            _finish_iteration(status="ok", summary=f"tool={action.tool_name}")
        
        # Si on atteint la limite, retourner la dernière observation si elle existe
        last_obs = None
        for h in reversed(self.history):
            if h.observation and h.observation.content:
                last_obs = h.observation.content
                break
        
        if last_obs and ("Recherche" in last_obs or "💰" in last_obs):
            self._run_meta["agent_output_incomplete"] = True
            self._run_meta["agent_output_warning"] = "iteration_limit_reached_with_observation_fallback"
            if TELEMETRY_AVAILABLE:
                publish_trace(
                    stage="pipeline_error",
                    status="error",
                    mode="agent",
                    error=self._run_meta["agent_output_warning"],
                )
            message = f"📊 Voici ce que j'ai trouvé :\n\n{last_obs[:3000]}"
            self._mark_task_failed(self._run_meta["agent_output_warning"])
            return message

        self._run_meta["agent_output_incomplete"] = True
        self._run_meta["agent_output_warning"] = "iteration_limit_reached_without_final_answer"
        if TELEMETRY_AVAILABLE:
            publish_trace(
                stage="pipeline_error",
                status="error",
                mode="agent",
                error=self._run_meta["agent_output_warning"],
            )
        self._mark_task_failed(self._run_meta["agent_output_warning"])
        return "J'ai atteint la limite d'itérations. Voici ce que j'ai trouvé jusqu'ici."
    
    def clear_history(self):
        """Efface l'historique."""
        self.history.clear()
        self.action_history.clear()
        self._task_plan.clear()
        self._plan_emitted = False
        self._iterations_without_progress = 0
        self._last_completed_task_count = 0
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
