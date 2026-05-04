"""AgentService — chat(), chat_stream(), think_and_act() et helpers.

Reçoit une référence directe à ``LumenaCore`` via ``self.core`` pour accéder
aux attributs partagés (llm, memory, emotion_manager, personality…).
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import threading
import traceback
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Any, Dict, List, Optional, TYPE_CHECKING
from src.prompts.services.agent_service_prompts import (
    _LLM_FACT_EXTRACT_PROMPT,
)

try:
    from src.utils.persistence import atomic_write_text
except ImportError:
    atomic_write_text = None  # type: ignore[assignment]

if TYPE_CHECKING:
    from src.core import LumenaCore

logger = logging.getLogger("lumena.agent_service")

_JOURNAL_LOCK = threading.Lock()

# ── Imports conditionnels (même logique que core.py) ──────────────────────────
try:
    from src.reasoning.react import ReActLoop
    REASONING_AVAILABLE = True
except ImportError:
    REASONING_AVAILABLE = False

try:
    from src.reasoning.react import ToolRegistry
    TOOL_REGISTRY_AVAILABLE = True
except ImportError:
    TOOL_REGISTRY_AVAILABLE = False

try:
    from src.llm.multi_provider import MultiProviderLLM
    MULTI_PROVIDER_AVAILABLE = True
except ImportError:
    MULTI_PROVIDER_AVAILABLE = False

try:
    from src.telemetry import publish_trace, push_trace_context, pop_trace_context
    TELEMETRY_AVAILABLE = True
except ImportError:
    TELEMETRY_AVAILABLE = False

try:
    from src.learning.instincts import InstinctSystem
    INSTINCTS_AVAILABLE = True
except ImportError:
    INSTINCTS_AVAILABLE = False

try:
    from src.tools.compaction import estimate_messages_tokens
except ImportError:
    estimate_messages_tokens = None

try:
    from src.core_services.intent_classifier import classify_intent, RequestMode
    INTENT_CLASSIFIER_AVAILABLE = True
except ImportError:
    INTENT_CLASSIFIER_AVAILABLE = False
    RequestMode = None

try:
    from src.tools.file_guardrails import OutsideAccessGrant
    _OUTSIDE_GRANT_AVAILABLE = True
except ImportError:
    OutsideAccessGrant = None  # type: ignore[assignment,misc]
    _OUTSIDE_GRANT_AVAILABLE = False


# ---------------------------------------------------------------------------
# Détection d'accès hors workspace sur demande explicite utilisateur
# ---------------------------------------------------------------------------

_WIN_PATH_RE = re.compile(r'[A-Za-z]:\\(?:[^\s\'"<>|*?\r\n\\][^\s\'"<>|*?\r\n]*)')
_UNIX_PATH_RE = re.compile(r'/(?:home|Users|mnt|media|tmp)/[^\s\'"<>|*?\r\n]+')

_NAMED_USER_DIRS: List[tuple] = [
    ("downloads", "Downloads"),
    ("téléchargements", "Downloads"),
    ("telechargements", "Downloads"),
    ("documents", "Documents"),
    ("bureau", "Desktop"),
    ("desktop", "Desktop"),
    ("images", "Pictures"),
    ("pictures", "Pictures"),
    ("photos", "Pictures"),
    ("music", "Music"),
    ("musique", "Music"),
    ("videos", "Videos"),
    ("vidéos", "Videos"),
]

_PC_SCOPE_KEYWORDS = (
    "hors workspace",
    "hors du workspace",
    "sur mon pc",
    "sur l'ordinateur",
    "sur le disque",
    "sur mon disque",
    "sur mon ordi",
    "outside workspace",
    "outside the workspace",
    "my computer",
    "mon ordinateur",
)


def _detect_outside_access_grant(query: str) -> "OutsideAccessGrant":  # type: ignore[return]
    """Analyse la requête et retourne un grant d'accès hors workspace borné.

    Seule la lecture est accordée. L'écriture reste toujours interdite hors workspace.
    Si aucune intention explicite n'est détectée, retourne un grant vide (aucun accès).
    """
    if not _OUTSIDE_GRANT_AVAILABLE:
        return None  # type: ignore[return-value]

    q_lower = query.lower()
    allowed_roots: List[Path] = []

    # 1. Chemins absolus Windows explicites dans la requête
    for m in _WIN_PATH_RE.finditer(query):
        p = Path(m.group(0))
        root = p if p.is_dir() else p.parent
        if root not in allowed_roots:
            allowed_roots.append(root)

    # 2. Chemins absolus Unix explicites
    for m in _UNIX_PATH_RE.finditer(query):
        p = Path(m.group(0))
        root = p if p.is_dir() else p.parent
        if root not in allowed_roots:
            allowed_roots.append(root)

    # 3. Répertoires utilisateur nommés (Downloads, Documents, Bureau…)
    home = Path.home()
    for keyword, dir_name in _NAMED_USER_DIRS:
        if keyword in q_lower:
            candidate = home / dir_name
            if candidate not in allowed_roots:
                allowed_roots.append(candidate)

    # 4. Scope "PC/disque" générique → home uniquement (conservateur)
    if not allowed_roots:
        for kw in _PC_SCOPE_KEYWORDS:
            if kw in q_lower:
                allowed_roots.append(home)
                break

    if not allowed_roots:
        return OutsideAccessGrant.none()

    return OutsideAccessGrant.for_paths(*allowed_roots)


def _env_flag(name: str, default: bool = False) -> bool:
    val = os.environ.get(name, "").strip().lower()
    if val in ("1", "true", "yes", "on"):
        return True
    if val in ("0", "false", "no", "off"):
        return False
    return default


class AgentService:
    """Service agent : méthodes de conversation / raisonnement de LumenaCore."""

    def __init__(self, core: "LumenaCore"):
        self.core = core

    # ──────────────────────────────────────────────────────────────────────────
    # Petits helpers
    # ──────────────────────────────────────────────────────────────────────────

    def _default_agent_meta(self) -> Dict[str, Any]:
        return {
            "agent_output_incomplete": False,
            "agent_output_warning": None,
            "agent_repair_attempts": 0,
            "agent_final_finish_reason": None,
        }

    def get_last_agent_meta(self) -> Dict[str, Any]:
        return dict(self.core._last_agent_meta)

    # ──────────────────────────────────────────────────────────────────────────
    # Runtime controls
    # ──────────────────────────────────────────────────────────────────────────

    async def _set_web_only_mode(self, enabled: bool) -> None:
        """Force le runtime en mode web-only (désactive Telegram + WhatsApp)."""
        os.environ["LUMENA_WEB_ONLY"] = "1" if enabled else "0"
        if enabled:
            os.environ["LUMENA_DISABLE_TELEGRAM"] = "1"
            os.environ["LUMENA_DISABLE_WHATSAPP"] = "1"
        try:
            from src.channels.manager import get_channel_manager
            from src.channels.base import ChannelType
            manager = get_channel_manager()
            if enabled:
                await manager.stop_channel(ChannelType.TELEGRAM)
                await manager.stop_channel(ChannelType.WHATSAPP)
        except Exception as e:
            logger.debug(f"Toggle Telegram/WhatsApp: {e}")

    def _match_model_alias(self, message: str) -> Optional[str]:
        """Résout un alias naturel vers un nom de modèle interne."""
        text = (message or "").strip().lower()
        if not text:
            return None

        aliases = {
            "lumena": "lumena-v1",
            "lumena v1": "lumena-v1",
            "lumena v1.0.0": "lumena-v1",
            "lumenav1": "lumena-v1",
            "qwen": "qwen3-8b",
            "qwen3": "qwen3-8b",
            "qwen 3": "qwen3-8b",
            "coder": "qwen2.5-coder-14b",
            "deepseek": "deepseek-v3",
            "deepseek v3": "deepseek-v3",
            "deepseek chat": "deepseek-v3",
            "deepseek reasoner": "deepseek-reasoner",
            "reasoner": "deepseek-reasoner",
            "gpt": "gpt-4o",
            "gpt4": "gpt-4o",
            "gpt-4": "gpt-4o",
            "claude": "claude-sonnet-4.6",
            "opus": "claude-opus-4.7",
            "gemini": "gemini-2.5-flash",
            "kimi": "kimi-k2.5",
            "grok": "grok-code-fast-1",
            "grok code": "grok-code-fast-1",
            "grok fast": "grok-4-1-fast-non-reasoning",
            "grok reasoning": "grok-4-1-fast-reasoning",
            "grok 4": "grok-4-1-fast-reasoning",
            "nvidia": "nvidia-glm-4.7",
            "nvidia glm": "nvidia-glm-4.7",
            "glm 5": "nvidia-glm-4.7",
            "glm5": "nvidia-glm-4.7",
            "glm 4.7": "nvidia-glm-4.7",
            "glm4.7": "nvidia-glm-4.7",
            "glm 9b": "nvidia-glm-4.7",
            "glm4 9b": "nvidia-glm-4.7",
            "nvidia minimax": "nvidia-minimax-m2.7",
            "nvidia minimax m2.7": "nvidia-minimax-m2.7",
            "minimax m2": "nvidia-minimax-m2.7",
            "minimax": "nvidia-minimax-m2.7",
        }

        for alias, model_name in sorted(aliases.items(), key=lambda item: len(item[0]), reverse=True):
            if alias in text:
                return model_name

        try:
            from src.llm.providers import AVAILABLE_MODELS
            for model_name in AVAILABLE_MODELS.keys():
                if model_name.lower() in text:
                    return model_name
        except Exception as e:
            logger.debug(f"Match model alias: {e}")

        return None

    async def _switch_brain_model(self, model_name: str) -> str:
        """Change le modèle LLM actif de manière sûre."""
        if not MULTI_PROVIDER_AVAILABLE:
            return "❌ Switch modèle indisponible: MultiProviderLLM non chargé."
        try:
            from src.llm.providers import get_model_config, check_api_key
        except Exception:
            return "❌ Switch modèle indisponible: providers non chargés."

        config = get_model_config(model_name)
        if not config:
            return f"❌ Modèle inconnu: {model_name}"

        if self.core.llm and self.core.llm.model_name == model_name:
            return f"🧠 Cerveau déjà actif: {config.display_name}"

        if not config.is_local() and not check_api_key(config.provider):
            return f"❌ Clé API manquante pour {config.provider.value} ({config.display_name})."

        try:
            self.core.llm = MultiProviderLLM(model_name=model_name)
            return f"✅ Cerveau changé: {config.display_name} ({config.provider.value})"
        except Exception as e:
            return f"❌ Échec changement cerveau: {e}"

    def _resolve_agent_type_from_label(self, raw_label: str) -> str:
        """Mappe une étiquette naturelle vers un type de subagent."""
        label = (raw_label or "").strip().lower()
        mapping = {
            "code": "code", "dev": "code", "developpeur": "code",
            "développeur": "code", "programmer": "code",
            "research": "research", "recherche": "research", "chercheur": "research",
            "file": "file", "fichier": "file",
            "browser": "browser", "navigateur": "browser", "web": "browser",
            "debug": "debug", "debogage": "debug", "débogage": "debug",
            "refactor": "refactor", "refacto": "refactor",
            "agent": "general", "general": "general", "général": "general",
        }
        if label in mapping:
            return mapping[label]
        for key, value in mapping.items():
            if key in label:
                return value
        return "general"

    async def _try_natural_delegation(self, message: str) -> Optional[str]:
        """Détecte et exécute une délégation naturelle."""
        text = (message or "").strip()
        if not text:
            return None

        patterns = (
            r"(?:demande|dis|dit|confie|délègue|delegue)\s+(?:a|à)\s+(?:l['']?)?(?:agent\s+)?(?P<agent>[\w\-\séèêàùç]+?)\s+(?:de|d')\s+(?P<task>.+)",
            r"(?:fais|fait)\s+(?:faire\s+)?(?:a|à)\s+(?:l['']?)?(?:agent\s+)?(?P<agent>[\w\-\séèêàùç]+?)\s+(?:de|d')\s+(?P<task>.+)",
        )

        match = None
        lowered = text.lower()
        for pattern in patterns:
            match = re.search(pattern, lowered, re.IGNORECASE)
            if match:
                break

        if not match:
            return None

        raw_agent = (match.group("agent") or "general").strip()
        task = (match.group("task") or "").strip()
        if len(task) < 4:
            return "⚠️ Délégation détectée, mais la tâche est trop courte."

        agent_type = self._resolve_agent_type_from_label(raw_agent)

        try:
            from src.agents.sub_agent import delegate_to_agent
            result = await asyncio.wait_for(
                delegate_to_agent(task, agent_type, {
                    "source": "natural_delegate",
                    "requested_agent_label": raw_agent,
                }),
                timeout=600,
            )
            return f"🤖 Délégation vers {agent_type} ok:\n{result}"
        except asyncio.TimeoutError:
            return f"⏱️ Délégation vers {agent_type} en timeout."
        except Exception as e:
            return f"❌ Échec délégation {agent_type}: {e}"

    async def _handle_runtime_controls(self, user_message: str, source_channel: str = "web") -> Optional[str]:
        """Interprète des commandes naturelles de contrôle runtime."""
        message = (user_message or "").strip().lower()
        if not message:
            return None

        _wake_patterns = (
            r"\bsalut\s+lumena\b", r"\bbonjour\s+lumena\b", r"\bhey\s+lumena\b",
            r"\bcoucou\s+lumena\b", r"\br[ée]veille[-\s]?toi\b",
            r"\bsors?\s+(?:du\s+)?mode\s+veille\b", r"\bsors?\s+de\s+(?:ta\s+)?veille\b",
            r"\bmode\s+actif\b",
        )
        _sleep_patterns = (
            r"\blumena\s+mode\s+veille\b", r"\bpasse\s+en\s+(?:mode\s+)?veille\b",
            r"\bmets[-\s]?toi\s+en\s+(?:mode\s+)?veille\b", r"\bhibernation\s+totale\b",
        )

        _is_wake = any(re.search(p, message) for p in _wake_patterns)
        _is_sleep_cmd = any(re.search(p, message) for p in _sleep_patterns)

        try:
            from src.autonomy.daemon import get_active_daemon as _get_active_daemon
            _daemon = _get_active_daemon()
        except Exception:
            _daemon = None

        _currently_sleeping = _daemon is not None and _daemon.sleep_mode
        if _currently_sleeping and not _is_wake and not _is_sleep_cmd:
            return "😴 (en veille — dis « Salut Lumena » pour me réveiller)"

        if _is_wake:
            if _daemon is not None and _daemon.sleep_mode:
                return await _daemon.exit_sleep()
            return None

        if _is_sleep_cmd:
            if _daemon is not None:
                return await _daemon.enter_sleep()
            return "😴 Mode veille noté (daemon non actif — les sous-systèmes sont déjà inactifs)."

        model_status_patterns = (
            r"\bquel\s+(?:cerveau|modele|modèle)\b", r"\btu\s+(?:tournes|es)\s+sur\s+quel\b",
            r"\bmodele\s+actuel\b", r"\bmodel\s+current\b",
            # "tu utilise(s) quel modèle" / "tu utilise koi comme modèle" — question d'info
            r"\btu\s+utilise[sz]?\b.*\b(?:quel|quoi|koi|comment|comme)\b.*\b(?:modele|modèle|model|api|cerveau)\b",
            r"\btu\s+utilise[sz]?\b.*\b(?:modele|modèle|model|api|cerveau)\b.*\b(?:actuellement|maintenant|en\s+ce\s+moment)\b",
        )
        if any(re.search(pattern, message) for pattern in model_status_patterns):
            try:
                from src.llm.providers import get_model_config
                if not self.core.llm:
                    return "🧠 Aucun cerveau actif (LLM non initialisé)."
                config = get_model_config(self.core.llm.model_name)
                display_name = config.display_name if config else self.core.llm.model_name
                provider = self.core.llm.provider.value if self.core.llm and self.core.llm.provider else "unknown"
                return f"🧠 Cerveau actif: {display_name} ({provider}) sur {source_channel}."
            except Exception:
                return "🧠 Impossible de lire le cerveau actif pour l'instant."

        model_switch_patterns = (
            r"\b(?:passe|switch|change|met|mets|active|utilise)\b.*\b(?:cerveau|modele|modèle|model|llm)\b",
            r"\b(?:passe|switch|change|met|mets|active|utilise)\b.*\b(?:deepseek|qwen|lumena|gpt|claude|gemini|kimi|reasoner|grok|nvidia)\b",
        )
        # Garde-fous : un vrai ordre de switch est court et direct.
        # Sinon un long prompt contenant "passe à l'étape X" + "openlumena.com"
        # déclencherait un switch non voulu vers lumena-v1.
        _is_switch_command = (
            len(message) <= 160
            and any(re.search(pattern, message) for pattern in model_switch_patterns)
            # action verb doit apparaître dans les 40 premiers caractères
            and re.search(
                r"^\s*(?:\w+\s+){0,6}(?:passe|switch|change|met|mets|active|utilise)\b",
                message,
                re.IGNORECASE,
            )
            is not None
        )
        if _is_switch_command:
            target_model = self._match_model_alias(message)
            if target_model:
                return await self._switch_brain_model(target_model)
            return "⚠️ Je n'ai pas reconnu le cerveau demandé."

        delegated = await self._try_natural_delegation(message)
        if delegated is not None:
            return delegated

        if len(message) > 120:
            return None

        mute_patterns = (
            r"\bne\s+parle\s+pas\b", r"\btais[-\s]?toi\b", r"\bsilence\b", r"\bmute\b",
            r"\bcoupe\s+la\s+voix\b", r"\bd[ée]sactive\s+la\s+voix\b", r"\barr[êe]te\s+de\s+parler\b",
        )
        if any(re.search(pattern, message) for pattern in mute_patterns):
            self.core.set_global_mute(True)
            return "🔇 D'accord. Je ne parle plus à voix haute."

        unmute_patterns = (
            r"\bactive\s+la\s+voix\b", r"\br[ée]active\s+la\s+voix\b", r"\bunmute\b",
            r"\bparle\b", r"\btu\s+peux\s+parler\b",
        )
        if any(re.search(pattern, message) for pattern in unmute_patterns):
            self.core.set_global_mute(False)
            return "🔊 D'accord. Je reparle à voix haute."

        telegram_voice_enable_patterns = (
            r"\bactive\s+voix\s+telegram\b", r"\br[ée]active\s+voix\s+telegram\b",
            r"\bvoix\s+telegram\s+on\b", r"\btts\s+telegram\s+on\b",
        )
        if any(re.search(pattern, message) for pattern in telegram_voice_enable_patterns):
            os.environ["LUMENA_TTS_TELEGRAM"] = "1"
            return "🔊 Voix Telegram activée (la réponse texte reste prioritaire)."

        telegram_voice_disable_patterns = (
            r"\bd[ée]sactive\s+voix\s+telegram\b", r"\bvoix\s+telegram\s+off\b",
            r"\btts\s+telegram\s+off\b", r"\bpas\s+de\s+voix\s+telegram\b",
        )
        if any(re.search(pattern, message) for pattern in telegram_voice_disable_patterns):
            os.environ["LUMENA_TTS_TELEGRAM"] = "0"
            return "🔇 Voix Telegram désactivée."

        web_only_enable_patterns = (
            r"\bmode\s+web\s+only\b", r"\bweb\s+seulement\b", r"\bweb\s+uniquement\b",
            r"\bd[ée]sactive\s+telegram\b", r"\bpas\s+telegram\b", r"\bpas\s+de\s+telegram\b",
            r"\buniquement\s+sur\s+la\s+page\s+web\b",
        )
        if any(re.search(pattern, message) for pattern in web_only_enable_patterns):
            await self._set_web_only_mode(True)
            return "🌐 Mode Web-only activé. Telegram est désactivé pour cette session."

        web_only_disable_patterns = (
            r"\bd[ée]sactive\s+mode\s+web\s+only\b", r"\bquitte\s+mode\s+web\s+only\b",
            r"\br[ée]active\s+telegram\b", r"\bautorise\s+telegram\b", r"\bmode\s+omnicanal\b",
        )
        if any(re.search(pattern, message) for pattern in web_only_disable_patterns):
            await self._set_web_only_mode(False)
            if "LUMENA_DISABLE_TELEGRAM" in os.environ:
                os.environ["LUMENA_DISABLE_TELEGRAM"] = "0"
            return "🔁 Mode omnicanal réactivé. Telegram peut être relancé si configuré."

        return None

    # ──────────────────────────────────────────────────────────────────────────
    # Conversation helpers
    # ──────────────────────────────────────────────────────────────────────────

    # ── Faits essentiels d'identité à apprendre naturellement ────────────────
    _ESSENTIAL_FACTS: List[Dict[str, Any]] = [
        {"key": "prénom_utilisateur", "alt": ["user_name", "creator"],
         "hint": "Tu ne connais pas le prénom de ton interlocuteur. "
                 "Au début de la réponse, demande-lui directement et chaleureusement : "
                 "\"Au fait, comment tu t'appelles ?\" ou \"Comment je dois t'appeler ?\""},
        {"key": "formality", "alt": [],
         "hint": "Tu ne sais pas si tu dois tutoyer ou vouvoyer. "
                 "Demande simplement : \"Tu préfères qu'on se tutoie ou qu'on se vouvoie ?\""},
        {"key": "language", "alt": ["langue_preferee"],
         "hint": "Tu ne connais pas la langue préférée. "
                 "Si l'utilisateur écrit en français c'est évident, sinon demande : "
                 "\"Tu préfères qu'on échange en quelle langue ?\""},
        {"key": "profession", "alt": ["travail", "métier"],
         "hint": "Tu ne sais pas ce que fait ton interlocuteur. "
                 "Glisse naturellement : \"Et toi, tu fais quoi dans la vie ?\""},
        {"key": "ville", "alt": ["localisation", "pays"],
         "hint": "Tu ne sais pas où habite l'utilisateur. "
                 "Demande naturellement : \"Tu es basé où ?\" ou \"Tu viens d'où ?\""},
        {"key": "registre_langue", "alt": [],
         "hint": "Tu ne connais pas le registre de langue de ton interlocuteur "
                 "(familier, courant, soutenu). Adapte-toi au ton de ses messages."},
    ]

    _FACT_EXTRACT_PATTERNS: List[tuple] = [
        # Prénom
        (r"(?:je m['\u2019]?appelle|mon (?:nom|pr[eé]nom) (?:est|c['\u2019]est)|appelle[z]?[\s-]moi)\s+([A-ZÀ-ÖØ-Ý][a-zà-öø-ÿ]+)", "prénom_utilisateur"),
        (r"(?:moi c['\u2019]est)\s+([A-ZÀ-ÖØ-Ý][a-zà-öø-ÿ]+)", "prénom_utilisateur"),
        # Formality
        (r"\b(?:tutoie|tutoyer|tutoiement|on se tutoie|tu peux me tutoyer)\b", "formality:tutoiement"),
        (r"\b(?:vouvoie|vouvoyer|vouvoiement|on se vouvoie|vous pouvez me vouvoyer)\b", "formality:vouvoiement"),
        # Profession
        (r"(?:je (?:suis|travaille comme|bosse comme|fais du))\s+([\w\séèêàùôîâç/-]+?)(?:\s*[.,!?]|$)", "profession"),
        (r"(?:mon (?:métier|travail|job|boulot) (?:c['\u2019]est|est))\s+([\w\séèêàùôîâç/-]+?)(?:\s*[.,!?]|$)", "profession"),
        # Ville / Localisation
        (r"(?:j['\u2019]?habite|je vis|je suis de|je viens de|je suis basé)\s+(?:à|a|en|au|aux)?\s*([\w\séèêàùôîâç-]+?)(?:\s*[.,!?]|$)", "ville"),
        # Âge
        (r"(?:j['\u2019]?ai)\s+(\d{1,3})\s*ans\b", "age"),
        # Email
        (r"(?:mon (?:mail|email|e-mail|adresse mail) (?:c['\u2019]est|est))\s+([\w.+-]+@[\w.-]+\.\w+)", "email"),
        # Site web / Portfolio
        (r"(?:mon (?:site|portfolio|blog|url) (?:c['\u2019]est|est))\s+(https?://\S+|[\w.-]+\.\w{2,})", "portfolio_url"),
        (r"(?:mon (?:site|portfolio|blog) (?:c['\u2019]est|est))\s+([\w.-]+\.\w{2,})", "portfolio"),
        # Langue
        (r"(?:je (?:parle|préfère|pr[eé]f[eè]re))\s+(?:le |en )?(français|anglais|english|french|espagnol|spanish|arabe|arabic|allemand|german)", "language"),
        # Centres d'intérêt
        (r"(?:j['\u2019]?(?:aime|adore|suis passionné par|suis fan de|kiffe))\s+([\w\séèêàùôîâç]+?)(?:\s*[.,!?]|$)", "centre_interet"),
        # Relation / comment l'appeler
        (r"(?:tu es|considère[\s-]moi comme|on est|notre relation c['\u2019]est)\s+([\w\séèêàùôîâç]+?)(?:\s*[.,!?]|$)", "relationship"),
    ]

    # Mots à ignorer quand on détecte un prénom ou une profession
    _IGNORED_VALUES = frozenset([
        "ok", "bien", "super", "oui", "non", "lumena", "lumi", "moi",
        "la", "le", "les", "un", "une", "des", "je", "tu", "il", "elle",
        "ça", "ce", "que", "qui", "quoi", "bon", "bonne", "mauvais",
        "content", "contente", "triste", "fatigué", "fatiguée",
    ])

    def _detect_and_save_preferences(self, message: str):
        """Détecte et sauvegarde automatiquement les préférences et faits personnels."""
        if not self.core.memory:
            return
        # Désactivable via config
        if os.environ.get("LUMENA_IDENTITY_LEARNING", "1") == "0":
            return

        for pattern, fact_key in self._FACT_EXTRACT_PATTERNS:
            match = re.search(pattern, message, re.IGNORECASE)
            if not match:
                continue

            # Fait avec valeur fixe (formality:tutoiement)
            if ":" in fact_key:
                key, value = fact_key.split(":", 1)
                self.core.memory.learn_fact(key, value)
                logger.info(f"💾 Fait appris: {key} = {value}")
                continue

            # Fait avec valeur extraite
            value = match.group(1).strip() if match.lastindex else ""
            if not value or len(value) < 2 or len(value) > 80:
                continue
            if value.lower() in self._IGNORED_VALUES:
                continue

            # Pour le prénom, aussi mettre à jour user_name et creator si vides
            if fact_key == "prénom_utilisateur":
                value = value.capitalize()
                self.core.memory.learn_fact("prénom_utilisateur", value)
                if not self.core.memory.get_fact("creator"):
                    self.core.memory.learn_fact("creator", value)
                logger.info(f"💾 Prénom appris: {value}")
            elif fact_key == "centre_interet":
                # Accumuler au lieu d'écraser
                existing = self.core.memory.get_fact("centres_interet") or ""
                interests = [i.strip() for i in existing.split(",") if i.strip()]
                if value.lower() not in [i.lower() for i in interests]:
                    interests.append(value)
                    self.core.memory.learn_fact("centres_interet", ", ".join(interests))
                    logger.info(f"💾 Centre d'intérêt appris: {value}")
            else:
                self.core.memory.learn_fact(fact_key, value)
                logger.info(f"💾 Fait appris: {fact_key} = {value}")

    # ── Extraction LLM sémantique post-conversation ──────────────────────


    _FACT_EXTRACT_COOLDOWN = 30  # secondes entre deux extractions LLM

    async def _llm_extract_facts(self, user_message: str, response: str):
        """Extraction sémantique de faits via LLM, appelée en background après le chat."""
        if not self.core.memory or not self.core.llm:
            return
        if os.environ.get("LUMENA_IDENTITY_LEARNING", "1") == "0":
            return

        # Cooldown pour éviter de surcharger le LLM
        now = datetime.now()
        last = getattr(self, "_last_llm_fact_extract", None)
        if last and (now - last).total_seconds() < self._FACT_EXTRACT_COOLDOWN:
            return
        self._last_llm_fact_extract = now

        # Messages trop courts = pas d'info perso probable
        if len(user_message) < 15:
            return

        prompt = _LLM_FACT_EXTRACT_PROMPT.format(
            user_msg=user_message[:1000], assistant_msg=response[:500],
        )
        try:
            raw = await asyncio.wait_for(
                self.core.llm.chat(
                    [{"role": "user", "content": prompt}],
                    temperature=0.0, max_tokens=300,
                ),
                timeout=10.0,
            )
            if not raw or not isinstance(raw, str):
                return

            # Extraire le JSON — gérer les cas où le LLM emballe dans ```json
            text = raw.strip()
            if text.startswith("```"):
                text = re.sub(r"^```\w*\n?", "", text)
                text = re.sub(r"\n?```$", "", text)
            text = text.strip()
            if not text.startswith("{"):
                return

            import json
            facts = json.loads(text)
            if not isinstance(facts, dict) or not facts:
                return

            # Clés autorisées (sécurité)
            _ALLOWED = {
                "prénom_utilisateur", "profession", "ville", "language", "age",
                "email", "centres_interet", "formality", "registre_langue",
                "relationship", "portfolio",
            }
            for key, value in facts.items():
                if key not in _ALLOWED:
                    continue
                value = str(value).strip()
                if not value or len(value) < 2 or len(value) > 200:
                    continue
                if value.lower() in self._IGNORED_VALUES:
                    continue

                # Ne pas écraser un fait existant sauf si c'est un ajout (centres_interet)
                existing = self.core.memory.get_fact(key)
                if key == "centres_interet" and existing:
                    old_list = [i.strip().lower() for i in existing.split(",") if i.strip()]
                    new_items = [i.strip() for i in value.split(",") if i.strip()]
                    added = [i for i in new_items if i.lower() not in old_list]
                    if added:
                        merged = existing + ", " + ", ".join(added)
                        self.core.memory.learn_fact(key, merged)
                        logger.info(f"🧠 LLM fact: centres_interet += {added}")
                elif not existing:
                    if key == "prénom_utilisateur":
                        value = value.capitalize()
                        self.core.memory.learn_fact(key, value)
                        if not self.core.memory.get_fact("creator"):
                            self.core.memory.learn_fact("creator", value)
                    else:
                        self.core.memory.learn_fact(key, value)
                    logger.info(f"🧠 LLM fact: {key} = {value}")

        except asyncio.TimeoutError:
            logger.debug("LLM fact extraction: timeout 10s")
        except (json.JSONDecodeError, ValueError):
            logger.debug("LLM fact extraction: réponse LLM non-JSON")
        except Exception as e:
            logger.debug(f"LLM fact extraction: {e}")

    def _get_missing_identity_hint(self) -> str:
        """Génère un hint pour le system prompt si des faits essentiels manquent.

        Ne demande qu'UN fait à la fois, pas à chaque message (cooldown),
        et arrête de demander après 2 tentatives ignorées par l'utilisateur.
        """
        if not self.core.memory:
            return ""
        if os.environ.get("LUMENA_IDENTITY_LEARNING", "1") == "0":
            return ""

        # Cooldown configurable via .env (défaut 60s)
        cooldown = int(os.environ.get("LUMENA_IDENTITY_HINT_COOLDOWN", "60"))
        now = datetime.now()
        last_asked = getattr(self, "_last_fact_hint_time", None)
        if last_asked and (now - last_asked).total_seconds() < cooldown:
            return ""

        # Compteur de tentatives par clé (max 2, puis on abandonne)
        if not hasattr(self, "_fact_ask_counts"):
            self._fact_ask_counts: Dict[str, int] = {}

        facts = self.core.memory.get_all_facts()

        for fact_def in self._ESSENTIAL_FACTS:
            key = fact_def["key"]
            has_fact = bool(facts.get(key))
            if not has_fact:
                for alt_key in fact_def.get("alt", []):
                    if facts.get(alt_key):
                        has_fact = True
                        break
            if has_fact:
                # Fait rempli → reset compteur
                self._fact_ask_counts.pop(key, None)
                continue

            # Déjà demandé 2 fois sans réponse → on n'insiste plus
            asked = self._fact_ask_counts.get(key, 0)
            if asked >= 2:
                continue

            self._fact_ask_counts[key] = asked + 1
            self._last_fact_hint_time = now
            return (
                f"\n\n🧠 INSTRUCTION : {fact_def['hint']}\n"
                f"Intègre cette question naturellement dans ta réponse. "
                f"Si l'utilisateur ne répond pas ou change de sujet, n'insiste pas."
            )

        return ""

    # ── BLOCKER D: Auto-dispatch images for Telegram/WhatsApp channels ────

    _IMAGE_PATH_RE = None

    async def _dispatch_generated_images(
        self, response: str, source_channel: str, sender: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Détecte les chemins d'images générées dans la réponse ReAct et les envoie sur le canal."""
        if source_channel not in ("telegram", "whatsapp") or not response:
            return
        import re
        if AgentService._IMAGE_PATH_RE is None:
            AgentService._IMAGE_PATH_RE = re.compile(
                r'(?:Fichier|File|chemin|path)[:\s]+([^\n]+\.(?:png|jpg|jpeg|webp|gif|svg))',
                re.IGNORECASE,
            )
        matches = AgentService._IMAGE_PATH_RE.findall(response)
        if not matches:
            return
        chat_id = (sender or {}).get("chat_id", "")
        if not chat_id:
            return
        for raw_path in matches:
            p = Path(raw_path.strip())
            if not p.exists():
                continue
            try:
                if source_channel == "telegram":
                    from src.channels.telegram_channel import TelegramChannel
                    tg = TelegramChannel.get_instance() if hasattr(TelegramChannel, 'get_instance') else None
                    if tg:
                        await tg.send_photo(str(p), chat_id)
                elif source_channel == "whatsapp":
                    from src.channels.whatsapp_channel import WhatsAppChannel
                    wa = WhatsAppChannel.get_instance() if hasattr(WhatsAppChannel, 'get_instance') else None
                    if wa:
                        await wa.send_photo(str(p), chat_id)
            except Exception as e:
                logger.warning(f"Auto-dispatch image {source_channel} failed: {e}")

    async def _save_conversation_to_memory(self, user_message: str, response: str):
        """Sauvegarde la conversation en mémoire persistante ChromaDB."""
        if not self.core.memory:
            return

        message_lower = user_message.lower()

        memory_type = "episodic"
        importance = 0.5

        personal_keywords = ["je m'appelle", "mon nom", "j'ai", "je suis", "mon prénom", "mon prenom"]
        preference_keywords = ["j'aime", "j'adore", "je déteste", "je préfère"]
        memory_keywords = ["n'oublie pas", "souviens-toi", "retiens", "rappelle-toi"]

        if any(kw in message_lower for kw in memory_keywords):
            memory_type = "semantic"
            importance = 0.95
        elif any(kw in message_lower for kw in personal_keywords):
            memory_type = "semantic"
            importance = 0.9
        elif any(kw in message_lower for kw in preference_keywords):
            memory_type = "semantic"
            importance = 0.8

        # M-4: Modulation émotionnelle — si la conversation est intense, elle mérite plus d'importance
        try:
            _em = getattr(self.core, "emotion_manager", None)
            if _em:
                _arousal = getattr(getattr(_em, "state", None), "arousal", 0.0) or 0.0
                if _arousal > 0.8:
                    importance = max(importance, 0.85)
                elif _arousal > 0.5:
                    importance = max(importance, 0.7)
        except Exception:
            pass  # Fallback silencieux — le comportement reste identique

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")

        response_summary = response[:500] + "..." if len(response) > 500 else response
        # M-3: Résumé LLM pour toutes les conversations (abaissement seuil 0.8→0.5)
        # Toujours mieux qu'un texte brut tronqué, même pour les échanges ordinaires
        if self.core.llm and (importance >= 0.5 or len(response) > 100):
            try:
                summary_prompt = (
                    f"Résume en 1-3 phrases concises ce qui est important à retenir de cet échange:\n"
                    f"User: {user_message[:300]}\nLumena: {response[:600]}\n"
                    f"Réponds uniquement avec le résumé, sans introduction."
                )
                llm_summary = await self.core.llm.chat(
                    [{"role": "user", "content": summary_prompt}], temperature=0.3, no_upgrade=True
                )
                if llm_summary and llm_summary.strip():
                    response_summary = llm_summary.strip()
            except Exception as e:
                logger.debug(f"LLM summary fallback to truncation: {e}")

        memory_content = f"[{timestamp}] User: {user_message} | Lumena: {response_summary}"

        try:
            self.core.memory.remember(memory_content, memory_type=memory_type, importance=importance)
            logger.debug(f"💾 Conversation sauvegardée (importance: {importance})")
        except Exception as e:
            logger.error(f"Erreur sauvegarde mémoire: {e}")

        try:
            self._save_to_journal_file(user_message, response, importance)
        except Exception as e:
            logger.warning(f"Erreur sauvegarde journal: {e}")

    def _save_to_journal_file(self, user_message: str, response: str, importance: float = 0.5):
        """Sauvegarde dans le journal quotidien."""
        memory_dir = self.core.data_dir / "memory" / "journal"
        memory_dir.mkdir(parents=True, exist_ok=True)

        today = datetime.now().strftime("%Y-%m-%d")
        journal_file = memory_dir / f"{today}.md"
        time_str = datetime.now().strftime("%H:%M:%S")

        importance_emoji = "⭐" if importance >= 0.8 else "💬" if importance >= 0.5 else "📝"
        response_short = response[:500] + "..." if len(response) > 500 else response

        entry = f"""
## {time_str} {importance_emoji}

**User:** {user_message}

**Lumena:** {response_short}

---
"""

        with _JOURNAL_LOCK:
            if not journal_file.exists():
                header = f"""# 📔 Journal Lumena - {today}

Conversations et apprentissages de la journée.

---
"""
                if atomic_write_text is not None:
                    atomic_write_text(journal_file, header + entry)
                else:
                    journal_file.write_text(header + entry, encoding="utf-8")
            else:
                with open(journal_file, "a", encoding="utf-8") as f:
                    f.write(entry)

        logger.debug(f"📔 Journal mis à jour: {journal_file.name}")

    def _convert_tu_to_vous(self, text: str) -> str:
        """Convertit le tutoiement en vouvoiement."""
        conversions = [
            (r"\btu es\b", "vous êtes"), (r"\bTu es\b", "Vous êtes"),
            (r"\btu as\b", "vous avez"), (r"\bTu as\b", "Vous avez"),
            (r"\btu veux\b", "vous voulez"), (r"\bTu veux\b", "Vous voulez"),
            (r"\btu peux\b", "vous pouvez"), (r"\bTu peux\b", "Vous pouvez"),
            (r"\btu fais\b", "vous faites"), (r"\bTu fais\b", "Vous faites"),
            (r"\btu vas\b", "vous allez"), (r"\bTu vas\b", "Vous allez"),
            (r"\btu sais\b", "vous savez"), (r"\bTu sais\b", "Vous savez"),
            (r"\bton\b", "votre"), (r"\bTon\b", "Votre"),
            (r"\bta\b", "votre"), (r"\bTa\b", "Votre"),
            (r"\btes\b", "vos"), (r"\bTes\b", "Vos"),
            (r"\bpour toi\b", "pour vous"), (r"\bPour toi\b", "Pour vous"),
            (r"\btu\b", "vous"), (r"\bTu\b", "Vous"),
            (r"\btoi\b", "vous"), (r"\bToi\b", "Vous"),
        ]
        result = text
        for pattern, replacement in conversions:
            result = re.sub(pattern, replacement, result)
        return result

    # ──────────────────────────────────────────────────────────────────────────
    # _auto_use_tools
    # ──────────────────────────────────────────────────────────────────────────

    async def _auto_use_tools(self, user_message: str) -> Optional[str]:
        """Détecte automatiquement si des outils doivent être utilisés."""
        message_lower = user_message.lower()

        search_patterns = [
            (r"(?:cherche|recherche|trouve)\s+(?:sur\s+)?(?:le\s+)?(?:web|internet|google)?\s*(.+)", "web_search"),
            (r"(?:google|googler?)\s+(.+)", "google_search"),
            (r"qu(?:'est-ce que|oi)\s+(.+)", "web_search"),
            (r"(?:c'est quoi|donne-moi des infos sur)\s+(.+)", "web_search"),
        ]

        url_patterns = [
            (r"(?:ouvre|va sur|visite|accède à)\s+(https?://\S+)", "open_url"),
            (r"(?:résume|analyse)\s+(?:cette page|ce site)?\s*(https?://\S+)", "summarize_url"),
            (r"(https?://\S+)", "fetch_url"),
        ]

        for pattern, action in search_patterns:
            match = re.search(pattern, message_lower)
            if match:
                query = match.group(1).strip()
                if action == "web_search":
                    results = await self.core.search_web(query)
                    if results:
                        formatted = f"🔍 **Résultats pour '{query}':**\n\n"
                        for i, r in enumerate(results, 1):
                            formatted += f"{i}. **{r['title']}**\n"
                            formatted += f"   {r['snippet']}\n"
                            if r.get('link'):
                                formatted += f"   🔗 {r['link']}\n"
                            formatted += "\n"
                        return formatted
                elif action == "google_search":
                    url = self.core.open_google_search(query)
                    return f"🌐 J'ai ouvert une recherche Google pour: {query}\n{url}"

        for pattern, action in url_patterns:
            match = re.search(pattern, user_message)
            if match:
                url = match.group(1)
                if action == "summarize_url":
                    return await self.core.summarize_url(url)
                elif action in ("fetch_url", "open_url"):
                    result = await self.core.fetch_url(url)
                    if result.get("success"):
                        return f"📄 **{result['title']}**\n\n{result['content'][:1000]}..."

        return None

    # ──────────────────────────────────────────────────────────────────────────
    # chat()
    # ──────────────────────────────────────────────────────────────────────────

    async def chat(
        self,
        user_message: str,
        source_channel: str = "web",
        sender: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Point d'entrée principal pour parler avec LUMENA."""
        c = self.core  # raccourci

        if not c.is_initialized:
            await c.initialize()

        source_channel = (source_channel or "web").strip().lower()

        runtime_control = await self._handle_runtime_controls(user_message, source_channel=source_channel)
        if runtime_control is not None:
            return runtime_control

        sender_info = c._resolve_sender_identity(sender, source_channel)
        if not sender_info and source_channel == "whatsapp":
            sender_info = c._identity_svc._resolve_whatsapp_identity(sender, source_channel)

        active_context = (
            c._load_tg_context(sender_info["tg_id"])
            if sender_info and "tg_id" in sender_info
            else c._load_wa_context(sender_info["phone"])
            if sender_info and "phone" in sender_info
            else c._load_web_context()
        )

        _rename_confirmation: Optional[str] = c._detect_friend_rename(user_message, sender_info)

        if not _rename_confirmation:
            _intro_confirmation: Optional[str] = c._detect_self_introduction(user_message, sender_info)
        else:
            _intro_confirmation = None

        trace_tokens: Dict[str, Any] = {}
        llm_meta: Dict[str, Any] = {}
        if TELEMETRY_AVAILABLE:
            trace_tokens = push_trace_context(channel=source_channel or "web", mode="chat")
            publish_trace(stage="input_received", status="start", mode="chat", summary=user_message)
            publish_trace(stage="context_build_start", status="start", mode="chat")
        context_started = perf_counter()

        for callback in c._on_thinking_callbacks:
            callback()

        self._detect_and_save_preferences(user_message)

        mood_change_msg = None
        if c.emotion_manager:
            mood_change_msg = await c.emotion_manager.process_user_message(user_message)
            if mood_change_msg:
                for callback in c._on_mood_change_callbacks:
                    callback(mood_change_msg)

        await c.trigger_hook("MESSAGE_RECEIVED", {"message": user_message, "role": "user"})

        system_prompt = c.personality.get_system_prompt()
        if c.emotion_manager:
            emotional_context = c.emotion_manager.get_emotional_context()
            system_prompt = system_prompt + "\n\n" + emotional_context

        if c.memory:
            memory_started = perf_counter()
            if TELEMETRY_AVAILABLE:
                publish_trace(stage="memory_query_start", status="start", mode="chat")

            memory_context = c.memory.get_context_for_prompt(user_message, max_memories=20)
            if TELEMETRY_AVAILABLE:
                publish_trace(
                    stage="memory_query_done", status="ok", mode="chat",
                    duration_ms=(perf_counter() - memory_started) * 1000.0,
                    summary=f"context_len={len(memory_context or '')}",
                )
            if memory_context:
                system_prompt = system_prompt + "\n\n" + memory_context

        permanent_context = c.get_permanent_memory_context()
        if permanent_context:
            system_prompt = system_prompt + permanent_context

            if c.memory:
                formality = c.memory.get_fact("formality")
                user_name = c.memory.get_fact("user_name")

                critical_rules = []
                if formality == "vouvoiement":
                    critical_rules.append("⚠️ UTILISE VOUS/VOTRE/VOS OBLIGATOIREMENT. JAMAIS tu/ton/ta/tes.")
                if user_name:
                    critical_rules.append(f"⚠️ L'utilisateur est {user_name}, ton créateur.")

                critical_rules.append("⚠️ Tu ne peux PAS entendre (pas de micro actif). Ne parle pas de 'voix'.")
                critical_rules.append("⚠️ Tu ne peux PAS voir (pas de caméra). Ne parle pas d'apparence.")

                if critical_rules:
                    rules_text = " | ".join(critical_rules)
                    system_prompt = system_prompt + f"\n\n🚨 RAPPELS CRITIQUES: {rules_text}"

        if sender_info and not sender_info["is_owner"]:
            _owner = c.memory.get_fact("user_name") if c.memory else "l'utilisateur"
            system_prompt += (
                f"\n\n⚠️ INTERLOCUTEUR ACTUEL: {sender_info['name']} "
                f"(contact Telegram/WhatsApp de {_owner}, PAS {_owner} lui-même). "
                f"Réponds normalement à {sender_info['name']}. "
                f"Ne partage PAS d'informations privées sur {_owner}."
            )

        skills_context = c._build_active_skills_context_for_query(user_message)
        if skills_context:
            system_prompt = system_prompt + "\n\n" + skills_context

        system_prompt += (
            "\n\n## 💬 MODE ACTUEL : CONVERSATION (chat)\n"
            "Tu es en mode conversation — tu écoutes, tu réfléchis, tu réponds. "
            "Tu as accès aux outils si l'utilisateur te demande d'agir (mail, web, fichiers…).\n"
            "C'est le mode naturel pour échanger librement, mais tu peux aussi exécuter des actions."
        )

        # ── Hint naturel si un fait essentiel manque ─────────────────────────
        identity_hint = self._get_missing_identity_hint()
        if identity_hint:
            system_prompt += identity_hint

        history = active_context.get_history_for_llm()

        _is_ollama_provider = (
            hasattr(c.llm, 'provider') and
            str(getattr(c.llm.provider, 'value', c.llm.provider)) == "ollama"
        )

        if c._compactor and not _is_ollama_provider and estimate_messages_tokens:
            try:
                _model_ctx = getattr(getattr(c.llm, "_config", None), "context_window", 128_000)
                _trigger_at = int(_model_ctx * 0.75)
                _compact_to = int(_model_ctx * 0.90)
                c._compactor.max_context_tokens = _compact_to
                _probe = [{"role": "system", "content": system_prompt}] + history
                _tokens = estimate_messages_tokens(_probe)
                if _tokens > _trigger_at:
                    logger.info(
                        f"🔄 Compaction déclenchée: {_tokens} tokens > {_trigger_at} "
                        f"(fenêtre modèle: {_model_ctx})"
                    )
                    _result = await c._compactor.compact_if_needed(history, force=True)
                    if _result.was_compacted:
                        logger.info(
                            f"✅ Contexte compacté: {_result.dropped_messages} msgs supprimés, "
                            f"~{_result.dropped_tokens} tokens libérés"
                        )
                        history = _result.messages
            except Exception as _ce:
                logger.warning(f"⚠️ Compaction ignorée (erreur): {_ce}")

        history.append({"role": "user", "content": user_message})

        if c.memory:
            formality = c.memory.get_fact("formality")
            user_name = c.memory.get_fact("user_name")

            injection_parts = ["[RÈGLES OBLIGATOIRES POUR TA RÉPONSE]"]

            if sender_info:
                if sender_info["is_owner"]:
                    if formality == "vouvoiement":
                        injection_parts.append("• IMPÉRATIF: Utilise 'VOUS/VOTRE/VOS'. JAMAIS 'tu/ton/ta/tes'.")
                    if user_name:
                        injection_parts.append(f"• Tu parles à ton créateur {user_name}.")
                else:
                    injection_parts.append(
                        f"• Tu parles à {sender_info['name']}, un contact de {user_name or 'ton utilisateur'}."
                        f" Ne révèle PAS d'infos privées sur {user_name or 'ton utilisateur'}."
                    )
            else:
                if formality == "vouvoiement":
                    injection_parts.append("• IMPÉRATIF: Tu dois utiliser 'VOUS/VOTRE/VOS' pour t'adresser à l'utilisateur. JAMAIS 'tu/ton/ta/tes'.")
                if user_name:
                    injection_parts.append(f"• L'utilisateur se nomme {user_name}. C'est ton créateur.")

            injection_parts.append("• Tu es Lumena, créée par Losskarr-G.C. PAS Qwen, PAS Alibaba.")
            injection_parts.append("[FIN DES RÈGLES]\n")

            injection_text = "\n".join(injection_parts)

            if history and history[-1]["role"] == "user":
                original_message = history[-1]["content"]
                history[-1]["content"] = injection_text + "\nMessage de l'utilisateur: " + original_message

        _suggested_instinct_ids: List[str] = []
        _instinct_context = ""
        if INSTINCTS_AVAILABLE and c.instinct_system:
            try:
                _instinct_suggestions = c.instinct_system.suggest(user_message[:200])
                _suggested_instinct_ids = [i.id for i in _instinct_suggestions[:3]]
                if _instinct_suggestions:
                    _instinct_context = c.instinct_system.format_instincts_summary()
            except Exception as e:
                logger.debug(f"Instinct suggestions: {e}")

        enriched_system = system_prompt
        if _instinct_context:
            enriched_system = system_prompt + "\n\n" + _instinct_context

        messages = [{"role": "system", "content": enriched_system}] + history

        if TELEMETRY_AVAILABLE:
            publish_trace(
                stage="context_build_done", status="ok", mode="chat",
                duration_ms=(perf_counter() - context_started) * 1000.0,
            )

        if TELEMETRY_AVAILABLE:
            llm_provider = getattr(getattr(c.llm, "provider", None), "value", "unknown")
            llm_model = getattr(c.llm, "model_name", getattr(c.llm, "model", "unknown"))
            publish_trace(
                stage="llm_request_start", status="start", mode="chat",
                provider=str(llm_provider), model=str(llm_model),
            )
        llm_started = perf_counter()

        _is_ollama_provider = (
            hasattr(c.llm, 'provider') and
            str(getattr(c.llm.provider, 'value', c.llm.provider)) == "ollama"
        )
        # P1.2: activer le shim V1→V2 pour le mode chat (binding ToolRegistry)
        if c.tool_system and hasattr(c.tool_system, 'bind_tool_registry') and TOOL_REGISTRY_AVAILABLE:
            if not getattr(c, '_tool_registry', None):
                _chat_tr = ToolRegistry(lumena=c)
                c._tool_registry = _chat_tr
                c.tool_system.bind_tool_registry(_chat_tr)

        try:
            if c.tool_system and hasattr(c.llm, 'chat_with_tools'):
                response = await c.llm.chat_with_tools(
                    messages, tool_system=c.tool_system, temperature=0.7,
                    max_tokens=getattr(c.llm, "max_output_tokens", 65536),
                )
            else:
                response = await c.llm.chat(messages)
        except Exception as e:
            # Signal négatif avant de relancer l'exception
            try:
                c.learn_from_interaction(pattern=user_message[:100], response="ERROR", success=False)
                if INSTINCTS_AVAILABLE and c.instinct_system and _suggested_instinct_ids:
                    for _inst_id in _suggested_instinct_ids:
                        c.instinct_system.provide_feedback(_inst_id, False)
            except Exception:
                pass
            if TELEMETRY_AVAILABLE:
                publish_trace(stage="pipeline_error", status="error", mode="chat", error=str(e))
            raise

        if hasattr(c.llm, "get_last_response_meta"):
            try:
                _meta_raw = c.llm.get_last_response_meta()
                llm_meta = _meta_raw if isinstance(_meta_raw, dict) else {}
            except Exception:
                llm_meta = {}
        if TELEMETRY_AVAILABLE:
            publish_trace(
                stage="llm_request_done", status="ok", mode="chat",
                duration_ms=(perf_counter() - llm_started) * 1000.0,
                provider=llm_meta.get("provider_used"), model=llm_meta.get("model_used"),
                summary=f"finish_reason={llm_meta.get('finish_reason')}" if llm_meta.get("finish_reason") else None,
            )

        if not response:
            c.learn_from_interaction(pattern=user_message[:100], response="EMPTY", success=False)
            response = "Hmm, j'ai eu un petit souci pour réfléchir... Pouvez-vous répéter ? 😅"

        final_started = perf_counter()
        if TELEMETRY_AVAILABLE:
            publish_trace(stage="final_assembly_start", status="start", mode="chat")

        if c.memory:
            formality = c.memory.get_fact("formality")
            if formality == "vouvoiement":
                response = self._convert_tu_to_vous(response)

        await self._save_conversation_to_memory(user_message, response)

        # Extraction LLM sémantique en background (ne bloque pas le chat)
        asyncio.create_task(self._llm_extract_facts(user_message, response))

        try:
            from src.learning.conversation_logger import queue_conversation
            queue_conversation(
                user_message=user_message, response=response,
                model_used=llm_meta.get("model_used", "unknown"),
                provider=llm_meta.get("provider_used", "unknown"),
            )
        except Exception as e:
            logger.debug(f"Queue conversation: {e}")

        if c.emotion_manager:
            task_completed = any(word in response.lower() for word in ["fait", "terminé", "voilà", "c'est bon", "réussi"])
            post_response_mood = await c.emotion_manager.process_own_response(response, task_completed)
            if post_response_mood and not mood_change_msg:
                mood_change_msg = post_response_mood
                for callback in c._on_mood_change_callbacks:
                    callback(mood_change_msg)

        if mood_change_msg:
            response = f"{mood_change_msg}\n\n{response}"

        if _rename_confirmation:
            response = f"{_rename_confirmation}\n\n{response}"

        if _intro_confirmation:
            response = f"{_intro_confirmation}\n\n{response}"

        active_context.add_message("user", user_message)
        active_context.add_message("assistant", response)
        if sender_info:
            if "tg_id" in sender_info:
                c._save_tg_context(sender_info["tg_id"], active_context)
            elif "phone" in sender_info:
                c._save_wa_context(sender_info["phone"], active_context)
        else:
            c._save_web_context(active_context)

        for callback in c._on_response_callbacks:
            callback(response)

        await c.trigger_hook("MESSAGE_SENT", {"message": response, "role": "assistant"})

        c.learn_from_interaction(pattern=user_message[:100], response=response[:100], success=True)
        if INSTINCTS_AVAILABLE and c.instinct_system and _suggested_instinct_ids:
            for _inst_id in _suggested_instinct_ids:
                try:
                    c.instinct_system.provide_feedback(_inst_id, True)
                except Exception as e:
                    logger.debug(f"Instinct feedback: {e}")

        telegram_tts_enabled = _env_flag("LUMENA_TTS_TELEGRAM", False)
        whatsapp_tts_enabled = _env_flag("LUMENA_TTS_WHATSAPP", False)
        should_speak = c.auto_speak
        if source_channel == "telegram":
            should_speak = should_speak and telegram_tts_enabled
        elif source_channel == "whatsapp":
            should_speak = should_speak and whatsapp_tts_enabled

        if should_speak and c.tts:
            try:
                if source_channel in ("telegram", "whatsapp"):
                    asyncio.create_task(c._speak_response(response))
                else:
                    await c._speak_response(response)
            except Exception as e:
                logger.error(f"❌ Erreur TTS chat: {e}")

        if TELEMETRY_AVAILABLE:
            publish_trace(
                stage="final_assembly_done", status="ok", mode="chat",
                duration_ms=(perf_counter() - final_started) * 1000.0,
            )
            publish_trace(
                stage="output_sent", status="ok", mode="chat",
                provider=llm_meta.get("provider_used"), model=llm_meta.get("model_used"),
                summary=response,
            )
            if trace_tokens:
                pop_trace_context(trace_tokens)

        return response

    # ──────────────────────────────────────────────────────────────────────────
    # chat_stream()
    # ──────────────────────────────────────────────────────────────────────────

    async def chat_stream(
        self, user_message: str, source_channel: str = "web",
        channel_id: Optional[str] = None, user_id: Optional[str] = None,
        username: Optional[str] = None, active_users: Optional[list] = None,
        image_paths: Optional[list] = None, is_admin: bool = False,
        channel_name: Optional[str] = None, channel_topic: Optional[str] = None,
        available_channels: Optional[list] = None,
    ):
        """Pipeline Discord complet avec streaming token par token."""
        c = self.core

        if not c.is_initialized:
            await c.initialize()

        # Contrôles runtime (mute/unmute/reset) — identiques à chat() et think_and_act()
        runtime_control = await self._handle_runtime_controls(user_message, source_channel=source_channel)
        if runtime_control is not None:
            yield runtime_control
            return

        _discord_user_id: Optional[str] = None
        _discord_channel_id: Optional[str] = None
        active_context = c.context

        if source_channel == "discord" and user_id:
            _discord_user_id = user_id
            _discord_channel_id = channel_id or "global"
            active_context = c._load_discord_user_context(user_id, _discord_channel_id, username or "")

        # Fix F — extraction préférences + question identité (manquaient en Discord)
        try:
            c._detect_and_save_preferences(user_message)
        except Exception as _pref_exc:
            logger.debug(f"chat_stream preferences: {_pref_exc}")

        if image_paths and source_channel == "discord":
            vision_parts = []
            for img_path in image_paths:
                try:
                    description = await c.llm.describe_image(img_path)
                    if description:
                        from pathlib import Path as _P
                        vision_parts.append(
                            f"[📷 Description de l'image « {_P(img_path).name} »:\n{description}]"
                        )
                        logger.info(f"🖼️ Vision Discord: image décrite ({_P(img_path).name})")
                except Exception as e_v:
                    logger.warning(f"Vision describe: {e_v}")
            if vision_parts:
                user_message = "\n".join(vision_parts) + "\n" + user_message

        for callback in c._on_thinking_callbacks:
            callback()

        mood_change_msg = None
        if c.emotion_manager:
            mood_change_msg = await c.emotion_manager.process_user_message(user_message)
            if mood_change_msg:
                for callback in c._on_mood_change_callbacks:
                    callback(mood_change_msg)
                yield mood_change_msg + "\n\n"

        _AGENT_KEYWORDS = [
            "ouvre", "ferme", "lance", "demarre", "arrete", "stop", "kill",
            "execute", "run", "start",
            "fichier", "dossier", "cree", "supprime", "copie", "deplace",
            "ecris", "modifie", "sauvegarde", "enregistre", "lis", "affiche",
            "recherche", "cherche", "trouve", "google", "web", "fouille",
            "memorise", "souviens", "rappelle", "retiens", "apprends",
            "memoire", "journal",
            "code", "script", "programme", "compile", "workspace",
            "spotify", "chrome", "navigateur", "application",
            "!agent", "/agent", "mode agent",
        ]
        # Mots-clés qui déclenchent des actions réservées aux admins Discord
        _ADMIN_ONLY_KEYWORDS = [
            "ouvre", "ferme", "lance", "demarre", "arrete", "stop", "kill",
            "execute", "run", "start",
            "fichier", "dossier", "cree", "supprime", "copie", "deplace",
            "ecris", "modifie", "sauvegarde", "enregistre",
            "code", "script", "programme", "compile", "workspace",
            "spotify", "chrome", "navigateur", "application",
            "!agent", "/agent", "mode agent",
        ]
        _is_passive = "[Tu as entendu ton prénom" in user_message
        _is_ollama = (
            hasattr(c.llm, "provider")
            and str(getattr(c.llm.provider, "value", "")) == "ollama"
        )
        _msg_lower = user_message.lower()
        _wants_admin_action = (
            source_channel == "discord"
            and not is_admin
            and any(kw in _msg_lower for kw in _ADMIN_ONLY_KEYWORDS)
        )
        needs_agent = (
            not _is_passive
            and not _is_ollama
            and REASONING_AVAILABLE
            and any(kw in _msg_lower for kw in _AGENT_KEYWORDS)
            and (source_channel != "discord" or is_admin)
        )

        full_response = ""

        def _sanitize_discord_response(text: str) -> str:
            import re as _re_s
            text = _re_s.sub(
                r'[A-Za-z]:\\Users\\[^\\s"\'<>\]\)]+(?:\\[^\\s"\'<>\]\)]+)+',
                '[chemin local]', text,
            )
            text = _re_s.sub(
                r'[A-Za-z]:\\(?:[^\\s"\'<>\]\)]+\\){2,}[^\\s"\'<>\]\)]+\.\w{2,6}',
                '[chemin local]', text,
            )
            text = _re_s.sub(
                r'(?i)(telegram|whatsapp|chat_id|target_chat|phone_number)[^\n]{0,40}?(\d{7,15})',
                lambda m: m.group(0).replace(m.group(2), '[ID confidentiel]'), text,
            )
            text = _re_s.sub(
                r'(?i)(token|api[_\-]?key|secret)["\\s:=]+[A-Za-z0-9_\-\.]{20,}',
                r'\1=[CONFIDENTIEL]', text,
            )
            return text

        def _extract_file_marker(response: str) -> tuple:
            import re as _re_fp
            _win = _re_fp.findall(r'[A-Za-z]:\\(?:[^\\\n"\'<>|?*\s]+\\)*[^\\\n"\'<>|?*\s]+\.\w{2,5}', response)
            _unix = _re_fp.findall(r'/(?:[^\n"\'<>|?*\s]+/)*[^\n"\'<>|?*\s]+\.\w{2,5}', response)
            valid = []
            for p in _win + _unix:
                try:
                    from pathlib import Path as _Path
                    if _Path(p).exists() and _Path(p).is_file():
                        valid.append(p)
                except Exception:
                    pass  # chemin invalide, on skip
            if not valid:
                return response, ""
            marker = "\n[__DISCORD_FILES__:" + "|".join(valid) + "]"
            return response, marker

        if _wants_admin_action:
            # L'utilisateur demande une action système mais n'a pas le rôle admin
            logger.info(f"🔒 [Discord] Action admin refusée pour non-admin: {user_message[:60]}")
            _refus_prompt = (
                f"L'utilisateur Discord '{username or user_id}' vient de te demander : «{user_message}»\n\n"
                f"Il n'a pas le rôle administrateur sur ce serveur. Tu dois lui expliquer gentiment"
                f" mais clairement qu'il ne peut pas te demander ça — que ce type d'action est réservé"
                f" aux admins du serveur. Sois naturelle, sans être froide ni robotique."
                f" Une phrase ou deux suffisent. Pas de liste, pas de markdown excessif."
            )
            try:
                _refus_messages = [{"role": "system", "content": c.personality.get_system_prompt()},
                                   {"role": "user", "content": _refus_prompt}]
                _refus_resp = await c.llm.chat(_refus_messages)
                yield _refus_resp or "Désolée, cette action est réservée aux administrateurs du serveur."
            except Exception:
                yield "Désolée, je ne peux pas faire ça — c'est réservé aux administrateurs du serveur. 😊"
            return

        if needs_agent:
            logger.info(f"🎮 [Discord] Mode Agent auto-détecté pour: {user_message[:60]}")
            yield "🛠️ *Mode agent activé…*\n\n"
            try:
                full_response = await c.think_and_act(user_message, source_channel=source_channel)
                full_response = full_response or ""
            except Exception as e:
                logger.error(f"Erreur think_and_act Discord: {e}")
                full_response = f"Désolée, une erreur est survenue en mode agent : {e}"
            if full_response:
                _text, _fmarker = _extract_file_marker(full_response)
                if source_channel == "discord":
                    _text = _sanitize_discord_response(_text)
                yield _text
                if _fmarker:
                    yield _fmarker
                # BLOCKER D: Auto-dispatch images pour Telegram/WhatsApp
                await self._dispatch_generated_images(full_response, source_channel, None)
        else:
            active_context.add_message("user", user_message)

            system_prompt = c.personality.get_system_prompt()
            if c.emotion_manager:
                system_prompt += "\n\n" + c.emotion_manager.get_emotional_context()
            if c.memory:
                memory_context = c.memory.get_context_for_prompt(user_message, max_memories=15)
                if memory_context:
                    system_prompt += "\n\n" + memory_context
            permanent = c.get_permanent_memory_context()
            if permanent:
                system_prompt += permanent

            if _discord_user_id:
                user_block = c._get_discord_user_context_block(_discord_user_id)
                if user_block:
                    system_prompt += "\n\n" + user_block

            if source_channel == "discord":
                _admin_note = (
                    " Tu agis en mode administrateur : tu peux exécuter des actions système et envoyer des fichiers."
                    if is_admin else
                    " Seuls les administrateurs du serveur peuvent te demander des actions système importantes."
                )
                _topic_note = ""
                if channel_name or channel_topic:
                    _ch_display = f"#{channel_name}" if channel_name else "ce salon"
                    if channel_topic:
                        _topic_context = f"La description officielle de ce salon est : « {channel_topic} »."
                    elif channel_name:
                        _topic_context = (
                            f"Ce salon n'a pas de description officielle."
                            f" Son nom indique son sujet (jeux→jeux vidéo, musique→musique,"
                            f" dev/développement→code, général→tout sujet, etc.)."
                        )
                    else:
                        _topic_context = ""
                    if available_channels:
                        _other_channels = [
                            ch for ch in sorted(available_channels)
                            if ch.lower() != (channel_name or "").lower()
                        ][:39]
                        _ch_list = ", ".join(f"#{ch}" for ch in _other_channels)
                        _channels_note = (
                            f" Autres salons du serveur (pour redirection uniquement) : {_ch_list}."
                            f" N'utilise ces noms QUE dans le marqueur [REDIRECT:#nom-salon],"
                            f" JAMAIS pour prétendre que l'on s'y trouve déjà."
                        )
                    else:
                        _channels_note = ""
                    _topic_note = (
                        f"\n\n[LOCALISATION ACTUELLE] Tu te trouves ACTUELLEMENT dans le salon {_ch_display}."
                        f" C'est LE salon où se déroule cette conversation — ne prétends JAMAIS être"
                        f" dans un autre salon. {_topic_context}{_channels_note}"
                        f"\n[RÈGLE REDIRECTION] Si et seulement si le sujet du message correspond"
                        f" clairement à un AUTRE salon de la liste (ex: question de musique dans"
                        f" #documentation alors que #musique existe), alors : réponds normalement"
                        f" ET ajoute à la toute fin : [REDIRECT:#nom-exact-du-salon]."
                        f" Si le sujet peut raisonnablement être traité dans {_ch_display}, ne redirige pas."
                    )
                system_prompt += (
                    "\n\n[Contexte Discord communautaire] Tu interagis dans un serveur Discord public ou communautaire."
                    " RÈGLES ABSOLUES : ne révèle jamais de chemins de fichiers complets, d'IDs Telegram/WhatsApp ou internes,"
                    " de tokens, clés API, ou données personnelles sensibles. Si tu mentionnes un fichier, utilise"
                    " uniquement son nom court (ex: rapport.md, pas le chemin entier)."
                    "\n[ÉTAT BOT] Tu es un bot Discord ACTUELLEMENT CONNECTÉ et OPÉRATIONNEL sur ce serveur."
                    " Tu PEUX lire et envoyer des messages dans ce salon — la preuve : tu réponds en ce moment."
                    " Tu NE PEUX PAS créer/supprimer des salons, gérer les rôles ou administrer le serveur"
                    " (ces actions nécessitent des droits administrateur que tu n'as pas par défaut)."
                    " Ne prétends JAMAIS ne pas avoir de token Discord, ne pas être connectée, ou ne pas"
                    " pouvoir envoyer de messages — c'est factuellement faux puisque tu réponds ici."
                    f"{_topic_note}"
                    f"{_admin_note}"
                )

            if active_users:
                _others = []
                for u in active_users:
                    _uid = u.get("user_id", "")
                    _uname = u.get("username") or f"utilisateur {_uid}"
                    if _uid:
                        _others.append(f"{_uname} (<@{_uid}>)")
                if _others:
                    system_prompt += (
                        f"\n\n[Salon multi-utilisateurs actif] D'autres personnes participent "
                        f"actuellement à la conversation dans ce salon : {', '.join(_others)}. "
                        f"Tu peux les mentionner dans tes réponses avec leur @mention si tu "
                        f"t'adresses à eux ou si tu fais référence à ce qu'ils ont dit. "
                        f"Sois naturelle et humaine dans tes interactions multi-personnes."
                    )

            skills_ctx = c._build_active_skills_context_for_query(user_message)
            if skills_ctx:
                system_prompt += "\n\n" + skills_ctx

            # Fix F — question identité proactive (manquait en Discord)
            try:
                _identity_hint = self._get_missing_identity_hint()
                if _identity_hint:
                    system_prompt += _identity_hint
            except Exception as _hint_exc:
                logger.debug(f"chat_stream identity hint: {_hint_exc}")

            _history = active_context.get_history_for_llm()
            if channel_name and _history:
                _location_reminder = {
                    "role": "system",
                    "content": (
                        f"[RAPPEL ABSOLU — ignore l'historique sur ce point] "
                        f"Tu te trouves MAINTENANT dans le salon #{channel_name}. "
                        f"Peu importe ce que disent tes réponses précédentes dans l'historique, "
                        f"tu es dans #{channel_name} et nulle part ailleurs."
                    ),
                }
                _history = _history[:-1] + [_location_reminder] + _history[-1:]

            messages = [{"role": "system", "content": system_prompt}] + _history

            try:
                if c.tool_system and hasattr(c.llm, "chat_with_tools") and not _is_ollama:
                    full_response = await c.llm.chat_with_tools(
                        messages, tool_system=c.tool_system, temperature=0.7,
                        max_tokens=getattr(c.llm, "max_output_tokens", 65536),
                    )
                else:
                    full_response = await c.llm.chat(messages)
            except Exception as e:
                logger.error(f"Erreur chat Discord: {e}")
                full_response = f"Désolée, j'ai eu une erreur : {e}"

            full_response = full_response or ""

            if c.emotion_manager and full_response:
                task_completed = any(w in full_response.lower() for w in ["fait", "terminé", "voilà"])
                await c.emotion_manager.process_own_response(full_response, task_completed)

            active_context.add_message("assistant", full_response)
            await self._save_conversation_to_memory(user_message, full_response)
            asyncio.create_task(self._llm_extract_facts(user_message, full_response))

            _text, _fmarker = _extract_file_marker(full_response)
            if source_channel == "discord":
                _text = _sanitize_discord_response(_text)
            words = _text.split(" ")
            for i, word in enumerate(words):
                yield word + (" " if i < len(words) - 1 else "")
            if _fmarker:
                yield _fmarker
            # BLOCKER D: Auto-dispatch images pour Telegram/WhatsApp
            await self._dispatch_generated_images(full_response, source_channel, None)

        if _discord_user_id and _discord_channel_id:
            profile = c._discord_users.get(_discord_user_id, {})
            profile["message_count"] = profile.get("message_count", 0) + 1
            c._discord_users[_discord_user_id] = profile
            c._save_discord_user_context(_discord_user_id, _discord_channel_id)

        try:
            from src.learning.conversation_logger import queue_conversation
            _model = getattr(c.llm, "model_name", getattr(c.llm, "model", "unknown"))
            _provider = getattr(getattr(c.llm, "provider", None), "value", "ollama")
            queue_conversation(user_message=user_message, response=full_response, model_used=_model, provider=_provider)
        except Exception as e:
            logger.debug(f"Queue conversation: {e}")

        for callback in c._on_response_callbacks:
            callback(full_response)

        if c.auto_speak and c.tts:
            try:
                await c._speak_response(full_response)
            except Exception as e:
                logger.error(f"❌ Erreur TTS stream Discord: {e}")

    # ──────────────────────────────────────────────────────────────────────────
    # think_and_act()
    # ──────────────────────────────────────────────────────────────────────────

    async def think_and_act(
        self,
        query: str,
        source_channel: str = "web",
        sender: Optional[Dict[str, Any]] = None,
        step_callback=None,
        max_iterations: Optional[int] = None,
    ) -> str:
        """Utilise la boucle ReAct pour réfléchir et agir."""
        c = self.core
        c._last_agent_meta = self._default_agent_meta()

        source_channel = (source_channel or "web").strip().lower()
        runtime_control = await self._handle_runtime_controls(query, source_channel=source_channel)
        if runtime_control is not None:
            return runtime_control

        # ── Phase 3 : routage intelligent (déterministe, sans LLM) ──────────
        _detected_intent: str = "react"  # Phase 7.2 : capturé pour conditionner MEMORY.md
        if INTENT_CLASSIFIER_AVAILABLE and os.getenv("LUMENA_DISABLE_INTENT_ROUTING", "").lower() not in ("1", "true"):
            _runtime_ctx_pre = None
            if hasattr(c.llm, "build_runtime_snapshot"):
                _runtime_ctx_pre = c.llm.build_runtime_snapshot(source_channel=source_channel)
            intent = classify_intent(query, _runtime_ctx_pre)
            _detected_intent = intent.value if hasattr(intent, "value") else str(intent)
            if intent == RequestMode.CHAT:
                logger.debug(f"Intent router: CHAT → ReAct (outils toujours disponibles)")
                pass  # On ne court-circuite plus vers c.chat() — outils toujours accessibles
            elif intent == RequestMode.PROJECT:
                logger.debug(f"Intent router: PROJECT (delegating to c.chat + project tools)")
                # PROJECT : on passe en ReAct mais avec les outils projet seulement
                # (c.chat gère les tools nativement via chat_with_tools pour non-Ollama)
                # On ne court-circuite pas le ReAct car les projets nécessitent le suivi
                pass  # Continue vers ReAct
            # TOOL_DIRECT et REACT → continuer vers ReAct normalement

        trace_tokens: Dict[str, Any] = {}
        if TELEMETRY_AVAILABLE:
            trace_tokens = push_trace_context(channel=source_channel or "web", mode="agent")
            publish_trace(stage="input_received", status="start", mode="agent", summary=query)
            publish_trace(stage="context_build_start", status="start", mode="agent")
        context_started = perf_counter()

        if not REASONING_AVAILABLE:
            logger.warning("Module reasoning non disponible, fallback vers chat")
            if TELEMETRY_AVAILABLE and trace_tokens:
                pop_trace_context(trace_tokens)
            return await c.chat(query)

        sender_info = c._resolve_sender_identity(sender, source_channel)
        if not sender_info and source_channel == "whatsapp":
            sender_info = c._identity_svc._resolve_whatsapp_identity(sender, source_channel)
        active_context = (
            c._load_tg_context(sender_info["tg_id"])
            if sender_info and "tg_id" in sender_info
            else c._load_wa_context(sender_info["phone"])
            if sender_info and "phone" in sender_info
            else c._load_web_context()
        )

        if active_context:
            active_context.add_message("user", query)

        # Extraction regex des préférences utilisateur (Fix E — manquait en mode agent)
        try:
            c._detect_and_save_preferences(query)
        except Exception as _pref_exc:
            logger.debug(f"think_and_act preferences: {_pref_exc}")

        conversation_context = ""
        if active_context and active_context.messages:
            recent_messages = active_context.messages[-8:]
            for msg in recent_messages:
                role = msg.role if hasattr(msg, 'role') else ""
                content = (msg.content if hasattr(msg, 'content') else "")[:800]
                if role and content:
                    conversation_context += f"{role}: {content}\n"
        active_skills_context = c._build_active_skills_context_for_query(query)

        if TELEMETRY_AVAILABLE:
            publish_trace(
                stage="context_build_done", status="ok", mode="agent",
                duration_ms=(perf_counter() - context_started) * 1000.0,
            )

        async def llm_chat(messages, stop=None):
            return await c.llm.chat(messages, stop=stop)

        def llm_meta_getter() -> Dict[str, Any]:
            if hasattr(c.llm, "get_last_response_meta"):
                try:
                    _raw = c.llm.get_last_response_meta()
                    return _raw if isinstance(_raw, dict) else {}
                except Exception:
                    return {}
            return {}

        tools = c._tool_registry
        if tools is None:
            tools = ToolRegistry(lumena=c)
            c._tool_registry = tools
            if c.tool_system and hasattr(c.tool_system, "bind_tool_registry"):
                c.tool_system.bind_tool_registry(tools)
        tools._allowed_tools = None
        tools._tools_desc_cache = None
        tools._observation_cache.clear()
        tools._caller_set_allowed = False
        # Accès hors workspace : borné aux chemins/répertoires explicitement mentionnés.
        # Pas de bypass global — seule une mention explicite dans la requête accorde un grant.
        _outside_grant = _detect_outside_access_grant(query) if _OUTSIDE_GRANT_AVAILABLE else None
        tools._outside_access_grant = _outside_grant
        if hasattr(tools, '_v2_context') and tools._v2_context is not None:
            tools._v2_context.outside_access_grant = _outside_grant
        _is_ollama = (
            hasattr(c.llm, 'provider') and
            str(getattr(c.llm.provider, 'value', c.llm.provider)) == "ollama"
        )
        runtime_ctx = None
        if hasattr(c.llm, 'build_runtime_snapshot'):
            runtime_ctx = c.llm.build_runtime_snapshot(
                source_channel=source_channel,
                mode="agent",
                intent=_detected_intent,  # Phase 7.2 : propagé pour conditionner MEMORY.md
            )
        react = ReActLoop(
            llm_chat, tools,
            conversation_context=conversation_context,
            active_skills_context=active_skills_context,
            llm_meta_getter=llm_meta_getter,
            max_final_repair_attempts=(1 if c.agent_final_repair_enabled else 0),
            is_weak_model=_is_ollama,
            task_orchestrator=c.task_orchestrator,
            step_callback=step_callback,
            runtime_ctx=runtime_ctx,
            max_iterations=max_iterations,
        )

        try:
            from src.autonomy.scheduler import set_agent_busy as _sched_set_busy
            _sched_set_busy(True)
        except Exception as e:
            logger.debug(f"Set agent busy: {e}")

        try:
            result = await react.run(query)
            if hasattr(react, "get_run_meta"):
                # FT-1: Enrichir avec les champs utiles pour le fine-tuning
                # get_run_meta() contient plan + run_meta de base
                _run_meta = react.get_run_meta()
                _plan = _run_meta.get("plan", {})
                _plan_total = _plan.get("total_tasks", 0) if isinstance(_plan, dict) else 0
                _plan_done = _plan.get("completed_tasks", 0) if isinstance(_plan, dict) else 0
                c._last_agent_meta = {
                    # Champs de base (incomplete, warning, repair_attempts, finish_reason)
                    **self._default_agent_meta(),
                    # Meta complet du run ReAct (override les champs de base)
                    **_run_meta,
                    # Champs enrichis pour le judge/scoring fine-tuning
                    "tools_used": sorted(getattr(react, "_successful_session_tools", set()))[:30],
                    "iterations": getattr(react, "_current_iteration", 0),
                    "success": _plan_total > 0 and _plan_done == _plan_total,
                    "plan_completion_pct": round(100 * _plan_done / _plan_total) if _plan_total > 0 else 0,
                }

            if TELEMETRY_AVAILABLE:
                publish_trace(stage="final_assembly_start", status="start", mode="agent")

            if hasattr(react, 'history') and react.history:
                observations = []
                for step in react.history:
                    if step.observation and step.observation.content:
                        observations.append(step.observation.content)
                if observations:
                    c._last_fetched_content = "\n\n".join(observations)
                    url_match = re.search(r'https?://[^\s]+', query)
                    if url_match:
                        c._last_mentioned_url = url_match.group(0)
                        c._last_page_title = f"Analyse de {url_match.group(0)}"
                    else:
                        c._last_page_title = f"Recherche: {query[:50]}"
                    logger.debug(f"💾 Observations agent sauvegardées ({len(c._last_fetched_content)} chars)")

            if TELEMETRY_AVAILABLE:
                llm_meta = {}
                if hasattr(c.llm, "get_last_response_meta"):
                    try:
                        _raw_meta = c.llm.get_last_response_meta()
                        llm_meta = _raw_meta if isinstance(_raw_meta, dict) else {}
                    except Exception:
                        llm_meta = {}
                publish_trace(stage="final_assembly_done", status="ok", mode="agent")
                publish_trace(
                    stage="output_sent", status="ok", mode="agent",
                    provider=llm_meta.get("provider_used"), model=llm_meta.get("model_used"),
                    summary=result,
                )
                if trace_tokens:
                    pop_trace_context(trace_tokens)

            if active_context:
                active_context.add_message("assistant", result)
                if sender_info:
                    if "tg_id" in sender_info:
                        c._save_tg_context(sender_info["tg_id"], active_context)
                    elif "phone" in sender_info:
                        c._save_wa_context(sender_info["phone"], active_context)
                else:
                    c._save_web_context(active_context)

            try:
                formality = c.memory.get_fact("formality") if c.memory else None
                if formality == "vouvoiement" and result:
                    result = self._convert_tu_to_vous(result)
            except Exception as e:
                logger.debug(f"Vouvoiement: {e}")

            try:
                await self._save_conversation_to_memory(query, result)
                asyncio.create_task(self._llm_extract_facts(query, result))
                logger.debug("💾 Conversation agent sauvegardée en mémoire")
            except Exception as e:
                logger.warning(f"Erreur sauvegarde mémoire agent: {e}")

            try:
                from src.learning.conversation_logger import queue_conversation
                _model = getattr(c.llm, "model_name", getattr(c.llm, "model", "unknown"))
                _provider = getattr(getattr(c.llm, "provider", None), "value", "unknown")
                queue_conversation(
                    user_message=query, response=result,
                    model_used=_model, provider=_provider,
                    react_meta=dict(c._last_agent_meta) if c._last_agent_meta else None,
                )
            except Exception as e:
                logger.debug(f"Queue conversation agent: {e}")

            # M-2: Mémoire procédurale — capturer les stratégies qui ont marché
            # Si le plan était complet à 100%, créer un souvenir "procédural" pour la prochaine fois
            try:
                _meta = dict(c._last_agent_meta) if c._last_agent_meta else {}
                _plan_info = _meta.get("plan", {})
                _plan_total = _plan_info.get("total_tasks", 0) if isinstance(_plan_info, dict) else 0
                _plan_done = _plan_info.get("completed_tasks", 0) if isinstance(_plan_info, dict) else 0
                _success = _meta.get("success", False)
                _tools = _meta.get("tools_used", [])
                if _success and _plan_total > 0 and _plan_done == _plan_total and c.memory and _tools:
                    # Construire une description concise de la stratégie
                    _tools_short = ", ".join(list(_tools)[:5])
                    _task_summary = query[:80].strip().rstrip("?!.")
                    _procedural_content = (
                        f"Stratégie réussie pour '{_task_summary}': "
                        f"{_plan_done}/{_plan_total} tâches accomplies via {_tools_short}."
                    )
                    c.memory.remember(
                        _procedural_content,
                        memory_type="procedural",
                        importance=0.85,
                    )
                    logger.debug(f"📚 Mémoire procédurale créée: {_procedural_content[:80]}")
            except Exception as _me:
                logger.debug(f"M-2 mémoire procédurale: {_me}")

            if c.auto_speak and c.tts:
                try:
                    await c._speak_response(result)
                except Exception as e:
                    logger.error(f"❌ Erreur TTS agent: {e}")
            return result
        except Exception as e:
            err = str(e)
            if TELEMETRY_AVAILABLE:
                publish_trace(stage="pipeline_error", status="error", mode="agent", error=err)
                if trace_tokens:
                    pop_trace_context(trace_tokens)
            logger.error(f"Erreur ReAct: {e}\n{traceback.format_exc()}")

            try:
                fallback_answer = await c.chat(query, source_channel=source_channel, sender=sender)
                c._last_agent_meta = {
                    **self._default_agent_meta(),
                    "agent_output_incomplete": False,
                    "agent_output_warning": None,
                }
                if TELEMETRY_AVAILABLE:
                    publish_trace(stage="agent_fallback_chat", status="ok", mode="agent", error=err)
                return fallback_answer
            except Exception as fallback_error:
                c._last_agent_meta = {
                    **self._default_agent_meta(),
                    "agent_output_incomplete": True,
                    "agent_output_warning": f"agent_error: {e}",
                }
                logger.error(f"Fallback chat error apres echec ReAct: {fallback_error}\n{traceback.format_exc()}")
                return f"Erreur agent: {e}"
        finally:
            try:
                from src.autonomy.scheduler import set_agent_busy as _sched_set_busy
                _sched_set_busy(False)
            except Exception as e:
                logger.debug(f"Unset agent busy: {e}")

    # ──────────────────────────────────────────────────────────────────────────
    # think_and_act_silent()
    # ──────────────────────────────────────────────────────────────────────────

    async def think_and_act_silent(
        self,
        task: str,
        timeout: float = 120.0,
        allowed_tools: Optional[list] = None,
    ) -> str:
        """Boucle ReAct silencieuse pour les tâches autonomes internes."""
        c = self.core

        # ── Guard : ne pas lancer de boucle ReAct autonome si l'agent user est actif ──
        try:
            from src.autonomy.scheduler import is_agent_busy as _is_busy
            if _is_busy():
                logger.debug("think_and_act_silent: agent user actif, tâche différée")
                return ""
        except Exception:
            pass

        if not REASONING_AVAILABLE:
            logger.debug("think_and_act_silent: ReAct non disponible, fallback llm.chat")
            return await c.llm.chat([{"role": "user", "content": task}])

        async def _llm_chat(messages, stop=None):
            return await c.llm.chat(messages, stop=stop)

        def _llm_meta():
            if hasattr(c.llm, "get_last_response_meta"):
                try:
                    _raw = c.llm.get_last_response_meta()
                    return _raw if isinstance(_raw, dict) else {}
                except Exception:
                    return {}
            return {}

        _is_ollama = (
            hasattr(c.llm, "provider")
            and str(getattr(c.llm.provider, "value", c.llm.provider)) == "ollama"
        )
        tools = c._tool_registry
        if tools is None:
            tools = ToolRegistry(lumena=c)
            c._tool_registry = tools
        tools._allowed_tools = None
        tools._tools_desc_cache = None
        tools._observation_cache.clear()
        tools._caller_set_allowed = False
        tools._outside_access_grant = OutsideAccessGrant.none() if _OUTSIDE_GRANT_AVAILABLE else None
        if hasattr(tools, '_v2_context') and tools._v2_context is not None:
            tools._v2_context.outside_access_grant = None  # Mode autonome — verrou total
        if allowed_tools:
            tools._allowed_tools = set(allowed_tools)
            tools._tools_desc_cache = None
            tools._caller_set_allowed = True
        runtime_ctx = None
        if hasattr(c.llm, 'build_runtime_snapshot'):
            runtime_ctx = c.llm.build_runtime_snapshot(mode="agent")
        react = ReActLoop(
            _llm_chat, tools,
            conversation_context="",
            active_skills_context="",
            llm_meta_getter=_llm_meta,
            max_final_repair_attempts=1,
            is_weak_model=_is_ollama,
            runtime_ctx=runtime_ctx,
        )

        try:
            result = await asyncio.wait_for(react.run(task), timeout=timeout)
            logger.debug(
                "think_and_act_silent: tâche terminée ({} chars)",
                len(result) if result else 0,
            )
            return result or ""
        except asyncio.TimeoutError:
            logger.warning(
                "think_and_act_silent: timeout {}s dépassé — tâche: {}",
                timeout, task[:80],
            )
            return ""
        except Exception as e:
            logger.warning("think_and_act_silent: erreur ReAct ({}), fallback llm.chat", e)
            try:
                return await c.llm.chat([{"role": "user", "content": task}])
            except Exception:
                return ""
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
