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
import threading
import unicodedata
import subprocess
import difflib
from time import perf_counter
from loguru import logger

# Cancel token registry: thread_id → threading.Event
# Enregistré depuis chat.py avant le démarrage du thread agent.
# Vérifié entre chaque itération pour stopper la boucle sans ctypes.
_REACT_CANCEL_EVENTS: Dict[int, Any] = {}

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
from ..runtime.execution_ledger import (
    ExecutionLedger, MUTATION_TOOLS as _LEDGER_MUTATION_TOOLS,
    INTENT_TO_MUTATION_FAMILY as _LEDGER_INTENT_FAMILIES,
    _extract_target as _ledger_extract_target,
    _extract_proof as _ledger_extract_proof,
)
# ── Semantic tool families for anti-hallucination guard ──────────────────────
_HC_TOOLS_FILE = frozenset({
    "write_file", "edit_file", "apply_patch", "insert_at_anchor", "edit_by_lines",
    "str_replace", "multi_edit_file", "create_file", "create_html", "create_markdown",
    "create_from_template", "create_email_html", "create_ics", "create_vcard",
    "create_meeting_report", "create_zip",
})
_HC_TOOLS_DOC = frozenset({
    "create_pdf", "create_docx", "create_pptx", "create_xlsx", "create_csv",
    "create_invoice_pdf", "create_batch_documents", "edit_docx", "edit_pptx",
    "edit_xlsx", "annotate_pdf", "add_watermark", "assemble_document", "convert_document",
})
_HC_TOOLS_SITE = frozenset({
    "generate_website", "serve_website", "edit_website", "write_website_files",
    "create_project", "delegate_task", "delegate_task_bg",
})
_HC_TOOLS_TASK = frozenset({
    "create_task", "schedule_task", "memory_save", "memory_store", "memory_add", "create_skill",
})
_HC_TOOLS_MAIL = frozenset({"mail_send", "send_email", "mail_reply_message"})
_HC_TOOLS_DISCORD = frozenset({
    "discord_send", "discord_send_message", "discord_send_embed",
    "discord_create_channel", "discord_create_category", "discord_create_invite",
    "discord_create_role", "discord_delete_channel", "discord_delete_message",
    "discord_delete_role", "discord_modify_channel", "discord_pin", "discord_unpin",
    "discord_assign_role", "discord_remove_role", "discord_ban", "discord_unban",
    "discord_kick", "discord_set_channel_permissions", "discord_server_configure",
})
_HC_TOOLS_MESSAGING = frozenset({
    "telegram_send_message", "telegram_send_document",
    "send_whatsapp_message", "send_whatsapp_document", "send_whatsapp_photo",
    "send_whatsapp_audio", "send_message", "send_critical_sms",
})
_HC_TOOLS_SOCIAL = frozenset({
    "twitter_post_tweet", "twitter_reply", "twitter_like", "twitter_compose_thread",
})
_HC_TOOLS_STRIPE = frozenset({
    "stripe_create_product", "stripe_update_product", "stripe_delete_product",
    "stripe_create_price", "stripe_create_payment_link", "stripe_update_payment_link",
    "stripe_create_customer", "stripe_update_customer", "stripe_create_subscription",
    "stripe_cancel_subscription", "stripe_create_invoice", "stripe_send_invoice",
    "stripe_void_invoice", "stripe_add_invoice_item", "stripe_create_checkout_session",
    "stripe_create_coupon", "stripe_delete_coupon", "stripe_create_refund",
})
_HC_TOOLS_GITHUB = frozenset({
    "github_repo_create", "github_file_write", "github_push_directory",
    "git_add", "git_commit", "git_push_pull", "git_init",
})
_HC_TOOLS_IMAGE = frozenset({
    "generate_image", "edit_image", "generate_thumbnail", "generate_thumbnail_pro",
    "generate_logo", "generate_svg", "upscale_image", "remove_background",
    "replace_background", "sketch_to_image", "compose_image", "generate_video", "edit_video",
})
_HC_TOOLS_NOTION = frozenset({"notion_create_page", "notion_update_page", "notion_add_to_database"})
_HC_TOOLS_RUNTIME = frozenset({
    "process_status", "health_check", "web_fetch",
    "browser_navigate", "browser_get_content", "browser_dom_state",
})
_HC_TOOLS_ANY_CREATE = (
    _HC_TOOLS_FILE | _HC_TOOLS_DOC | _HC_TOOLS_SITE | _HC_TOOLS_TASK
    | _HC_TOOLS_GITHUB | _HC_TOOLS_STRIPE | _HC_TOOLS_IMAGE | _HC_TOOLS_NOTION
    | _HC_TOOLS_DISCORD
)
_HC_TOOLS_ANY_SEND = _HC_TOOLS_MAIL | _HC_TOOLS_MESSAGING | _HC_TOOLS_DISCORD | _HC_TOOLS_SOCIAL | _HC_TOOLS_GITHUB

_HINT_ONLY_PROOF_REQUIRED_TOOLS = frozenset({"run_command", "run_shell", "exec_command"})
_SERVER_RUNTIME_CLAIM_RE = re.compile(
    r"\b(serveur|server|processus|localhost|127\.0\.0\.1|::1|port\s*\d+).{0,40}"
    r"(lanc[ée]|demarr|démarr|running|tourne|actif|accessible|en ligne)\b",
    re.IGNORECASE,
)


def _has_runtime_server_claim_proof(text: str, successful_tools: set[str]) -> bool:
    if not text or not _SERVER_RUNTIME_CLAIM_RE.search(text):
        return False
    return any(tool in successful_tools for tool in _HC_TOOLS_RUNTIME)
# ─────────────────────────────────────────────────────────────────────────────

from .agent_execution_state import AgentExecutionState, RunMetaProxy
from .response_parser import (
    parse_response as _parse_response_fn,
    parse_plan as _parse_plan_fn,
    extract_balanced_json,
    parse_action_args as _parse_action_args_fn,
    _action_inline_total as _ait_global,
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


_READ_SIG_BUCKET = 50  # granularité en lignes pour la détection de zone redondante


def _extract_anchor_facts(text: str) -> str:
    """
    Extrait les faits structurés clés d'une observation avant compaction.
    Retourne une ligne "📌 Ancres: ..." ou "" si rien de notable.
    Couverture : snowflakes Discord (17-20 chiffres), patterns guild_id=/channel_id=/server_id=,
    chemins Windows (C:\\...).
    """
    import re
    facts: list[str] = []

    # Snowflake IDs Discord (17-20 chiffres, pas dans un chemin)
    for m in re.finditer(r'(?<![/\\.\d])\b(\d{17,20})\b(?![/\\.\d])', text):
        facts.append(m.group(1))

    # guild_id=... / channel_id=... (valeur alphanumérique ou entre backticks/guillemets)
    for m in re.finditer(r'\b(?:guild_id|channel_id|server_id)\s*[=:]\s*[`"\']?(\w{6,})[`"\']?', text, re.IGNORECASE):
        facts.append(f"{m.group(0).split('=')[0].split(':')[0].strip()}={m.group(1)}")

    # Chemins Windows (C:\...) — juste le segment racine pour ne pas gonfler
    for m in re.finditer(r'[A-Za-z]:\\(?:[^\s\n"\']{3,60})', text):
        facts.append(m.group(0)[:80])

    # Dédupliquer en conservant l'ordre
    seen: set[str] = set()
    unique: list[str] = []
    for f in facts:
        if f not in seen:
            seen.add(f)
            unique.append(f)

    if not unique:
        return ""
    # Limiter à 10 ancres max pour ne pas gonfler le résumé
    return "📌 Ancres: " + " | ".join(unique[:10]) + "\n"


# ── Politique de preuve et complétion du plan ─────────────────────────────────
# Extraite dans plan_evidence.py pour isolation et testabilité.
_BROWSER_SPA_NOISE_MARKERS: tuple[str, ...] = (
    "document.documentelement",
    "localstorage.getitem",
    "colorscheme",
    "prefers-color-scheme",
    "function k(",
    "theme\",\"system",
    "webpack",
    "__next",
)


def _looks_like_browser_spa_noise(text: str) -> bool:
    if not text:
        return False
    lower = text.lower()
    marker_hits = sum(1 for marker in _BROWSER_SPA_NOISE_MARKERS if marker in lower)
    if marker_hits >= 2:
        return True
    return lower.count("=>") >= 2 and lower.count("{") >= 10 and lower.count("}") >= 10


def _extract_human_browser_lines(text: str, *, max_lines: int = 12) -> list[str]:
    lines: list[str] = []
    seen: set[str] = set()
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        lower = line.lower()
        if lower.startswith(("📄 page:", "url:", "interactive elements:", "form state:")):
            continue
        if any(marker in lower for marker in _BROWSER_SPA_NOISE_MARKERS):
            continue
        if len(line) < 8:
            continue
        alpha_count = sum(ch.isalpha() for ch in line)
        if alpha_count < 4:
            continue
        if line in seen:
            continue
        seen.add(line)
        lines.append(line)
        if len(lines) >= max_lines:
            break
    return lines


def _looks_like_chat_transcript(text: str) -> bool:
    if not text or "---" not in text:
        return False
    lower = text.lower()
    if "js exécuté" not in lower and "js execute" not in lower and "conversation" not in lower:
        return False
    if not re.search(r"\b\d{1,2}:\d{2}\s*(?:am|pm)?\b", text, re.IGNORECASE):
        return False
    human_lines = _extract_human_browser_lines(text, max_lines=20)
    return len(human_lines) >= 2


def _compact_browser_observation_payload(
    tool_name: str,
    observation_text: str,
    is_chat_surface: bool = False,
) -> Optional[str]:
    """Compacte intelligemment les observations browser bruitées ou transcriptées.

    Fix B: Pour les surfaces chat (chat_composer, chat_transcript), la limite est augmentée
    à 3500 chars pour ne pas tronquer les conversations longues.
    """
    if not observation_text:
        return None
    # Fix B: Limite adaptée à la surface — plus haute pour les chats
    _compact_limit = 3500 if is_chat_surface else 1400
    if tool_name == "browser_get_content" and _looks_like_browser_spa_noise(observation_text):
        title_match = re.search(r"^📄 Page:\s*(.+)$", observation_text, re.MULTILINE)
        title = title_match.group(1).strip() if title_match else "Page browser"
        # Fix B: Plus de lignes extraites pour les surfaces chat
        max_lines = 30 if is_chat_surface else 10
        human_lines = _extract_human_browser_lines(observation_text, max_lines=max_lines)
        if human_lines:
            return (
                f"📄 Page: {title}\n\n"
                "⚠️ SPA shell détectée — observation browser compactée sur le texte humain visible.\n\n"
                + "\n".join(human_lines)
            )[:_compact_limit]
    if tool_name == "browser_evaluate" and _looks_like_chat_transcript(observation_text):
        max_lines = 30 if is_chat_surface else 12
        human_lines = _extract_human_browser_lines(observation_text, max_lines=max_lines)
        if human_lines:
            return ("✅ JS exécuté\n→ " + "\n---\n".join(human_lines))[:_compact_limit]
    return None


from .plan_evidence import (
    _SEQ_FALLBACK_BLOCKLIST,
    _EXPLORATION_TOOLS_STRICT,
    _BUSINESS_ACTION_STARTERS,
    _BUSINESS_ACTION_STARTERS_NORMALIZED,
    _normalize_guard_token,
    _VERIFY_TASK_KEYWORDS,
    _VERIFY_PROOF_TOOLS,
    _VERIFY_OBS_PROOF_MARKERS,
    classify_observation,
    is_verify_task,
    has_sufficient_proof,
    evaluate_task_proof,
    reconcile_delegate_report,
    task_completion_status,
)
# ─────────────────────────────────────────────────────────────────────────────


def _compute_read_sig(tool_name: str, tool_args: dict) -> tuple:
    """Empreinte (fichier, zone_bucket, intention) d'une action de lecture.

    Deux lectures sont considérées redondantes si elles retournent la même
    empreinte : même fichier, même bucket de zone (paliers de 50 lignes),
    même intention/pattern.  Une progression (autre fichier, autre zone ou
    autre cible) produit une empreinte différente.
    """
    B = _READ_SIG_BUCKET
    if tool_name == "read_file":
        p = str(tool_args.get("path", tool_args.get("file_path", "")))
        s = int(tool_args.get("start_line") or 0)
        e = int(tool_args.get("end_line") or s + 100)
        return (p, (s // B, e // B), "read")
    elif tool_name in ("grep_search", "search_in_code"):
        p = str(tool_args.get("path", tool_args.get("directory", "")))
        pat = str(tool_args.get("pattern", tool_args.get("query", "")))
        return (p, None, pat)
    elif tool_name == "find_files":
        return (str(tool_args.get("path", "")), None, str(tool_args.get("pattern", "")))
    elif tool_name == "list_directory":
        return (str(tool_args.get("path", "")), None, "list")
    else:
        return (str(sorted(tool_args.keys())), None, tool_name)


# ── Browser impasse detection ─────────────────────────────────────────────────
# Signaux textuels indiquant qu'une page est bloquée / non exploitable.
# Chaque entrée : (token, raison lisible, try_dismiss)
#   try_dismiss=True  → overlay/popup potentiellement résolvable
#   try_dismiss=False → blocage structurel (anti-bot, auth, erreur serveur)
_BROWSER_IMPASSE_SIGNALS: list = [
    # Cloudflare / anti-bot
    ("cloudflare", "protection Cloudflare détectée", False),
    ("checking your browser", "vérification anti-bot Cloudflare", False),
    ("just a moment", "vérification Cloudflare (Just a moment)", False),
    ("challenge_running", "challenge Cloudflare actif", False),
    # Captcha
    ("captcha", "CAPTCHA requis — vérification humaine", False),
    ("recaptcha", "reCAPTCHA détecté", False),
    ("i'm not a robot", "reCAPTCHA checkbox détecté", False),
    # Erreurs serveur connues
    ("dyno hours exhausted", "service Heroku suspendu (dyno hours épuisées)", False),
    ("no web processes running", "service Heroku sans processus web", False),
    ("application error", "erreur applicative — site en panne", False),
    # Contrôle d'accès
    ("access denied", "accès refusé par le serveur", False),
    ("403 forbidden", "accès interdit (403)", False),
    ("401 unauthorized", "authentification requise (401)", False),
    ("rate limit exceeded", "rate limit atteint — trop de requêtes", False),
    ("too many requests", "trop de requêtes (429)", False),
    # Login wall / mur d'authentification
    # Signaux qualifiés (login+qualificateur) pour éviter les faux positifs sur formulaires normaux
    ("you must be logged in", "mur d'authentification — connexion requise", False),
    ("you must sign in", "mur d'authentification — connexion requise", False),
    ("please log in to continue", "mur d'authentification — connexion requise", False),
    ("please sign in to continue", "mur d'authentification — connexion requise", False),
    ("login required", "mur d'authentification — login requis", False),
    ("sign in required", "mur d'authentification — sign in requis", False),
    ("members only", "contenu réservé aux membres", False),
    ("subscribers only", "contenu réservé aux abonnés", False),
    ("authentication required", "authentification requise (page)", False),
    ("your session has expired", "session expirée — reconnexion requise", False),
    # Overlays bloquants (dismiss peut aider)
    ("cookie consent", "bannière cookies bloquante", True),
    ("accept cookies", "popup cookies bloquant", True),
    # Page vide / non interactive
    ("no interactive elements found", "aucun élément interactif sur la page", False),
    ("0 elements found", "aucun élément exploitable (DOM vide)", False),
    ("aucun élément interactif", "aucun élément interactif sur la page", False),
]

# Token → frozenset pour lookup rapide sans parcourir la liste à chaque appel
_BROWSER_IMPASSE_TOKEN_SET: frozenset = frozenset(
    token for token, _reason, _dismiss in _BROWSER_IMPASSE_SIGNALS
)


def _detect_browser_impasse(obs_text: str) -> "tuple[bool, str, bool]":
    """Détecte si une observation browser indique une page bloquée / non exploitable.

    Retourne (is_blocked, reason, try_dismiss):
    - is_blocked  : True si un signal d'impasse est détecté
    - reason      : description lisible du blocage
    - try_dismiss : True si browser_dismiss_popups vaut la peine d'être tenté
    """
    if not obs_text:
        return False, "", False
    lower = obs_text.lower()
    for token, reason, try_dismiss in _BROWSER_IMPASSE_SIGNALS:
        if token in lower:
            return True, reason, try_dismiss
    return False, "", False


BROWSER_SURFACE_TYPES: frozenset[str] = frozenset({
    "search_results",
    "listing_results",
    "chat_composer",
    "chat_transcript",
    "public_form",
    "auth_form",       # formulaire de connexion/login (mot de passe présent)
    "contact_form",    # formulaire de contact/newsletter (pas de mot de passe)
    "detail_page",     # fiche produit, événement, concert — contenu riche
    "spa_shell",       # SPA sans contenu utile chargé (JS requis / loading)
    "builder_editor",
    "login_wall",
    "anti_bot_or_challenge",
    "error_page",
    "popup_blocked",
    "iframe_heavy",
    "non_interactive",
    "normal_content",
    "unknown",
})

_BROWSER_SURFACE_FILL_FORM_HINTS: frozenset[str] = frozenset({
    "formulaire", "remplir", "rempli", "submit", "soumettre",
    "fill form", "fill out", "contact form", "demo form",
})
_BROWSER_SURFACE_AUTH_HINTS: frozenset[str] = frozenset({
    "login", "log in", "sign in", "connexion", "connecter", "se connecter",
    "authentif", "compte",
})
_BROWSER_SURFACE_SEARCH_HINTS: frozenset[str] = frozenset({
    "recherche google", "résultats google", "resultats google",
    "google search", "search results", "recherche duckduckgo", "bing search",
})
_BROWSER_SURFACE_BUILDER_HINTS: frozenset[str] = frozenset({
    "form builder", "revision history", "add collaborators",
    "add element", "available fields", "customize thank you page",
    "product selector, currently selected form builder",
    "preview form", "jotform form builder", "dismiss suggestions",
})
_BROWSER_SURFACE_IFRAME_HINTS: frozenset[str] = frozenset({
    "frame(s)", "iframe", "iframeresult", "__tcfapilocator",
})
_BROWSER_SURFACE_LISTING_HINTS: frozenset[str] = frozenset({
    "voir l’annonce", "voir lannonce", "ajouter l’annonce aux favoris",
    "ajouter lannonce aux favoris", "site de petites annonces gratuites",
    "choisir une localisation", "mes recherches", "favoris",
    "valider votre recherche", "déposer une annonce", "deposer une annonce",
    "voitures d’occasion", "voitures d’occasion", "mileage_max",
    "petites annonces", "voir le détail", "voir le detail",
})

# Domaines de sites d’annonces connus — détection par URL même sans hints dans le contenu
_BROWSER_LISTING_URL_DOMAINS: frozenset[str] = frozenset({
    "leboncoin.fr", "autoscout24.fr", "autoscout24.com",
    "lacentrale.fr", "leparking.fr", "paruvendu.fr",
    "argusdeloccasion.com", "occasion.caradisiac.com",
    "facebook.com/marketplace",
})
# Segments de chemin indiquant une page de résultats / recherche sur ces sites
_BROWSER_LISTING_URL_PATH_SEGMENTS: frozenset[str] = frozenset({
    "/lst/", "/recherche", "/listing", "/search",
    "/voitures/", "/ck/", "/marketplace",
})
_BROWSER_SURFACE_PUBLIC_FORM_HINTS: frozenset[str] = frozenset({
    "textbox", "searchbox", "combobox", "spinbutton", "textarea",
    "radio", "checkbox", "submit button", "name input", "email input",
    "password input", "phone input",
})
_BROWSER_SURFACE_CHAT_HINTS: frozenset[str] = frozenset({
    "prosemirror",
    "message input",
    "ask anything",
    "send question",
    "send message",
    "start chatting",
    "new discussion",
    "new chat",
    "chat vocal",
    "select agent",
    "voice mode",
    "edit question",
    "rewrite",
    "copy to clipboard",
    "contenteditable trouve",
})
_BROWSER_SURFACE_ERROR_HINTS: frozenset[str] = frozenset({
    "404 not found", "application error", "dyno hours exhausted",
    "access denied", "403 forbidden", "401 unauthorized",
    "too many requests", "rate limit exceeded", "no web processes running",
})

# Signaux forts pour auth_form — champ mot de passe présent dans le DOM
_BROWSER_SURFACE_AUTH_FORM_HINTS: frozenset[str] = frozenset({
    "password input", "mot de passe", "password field", "confirm password",
    "confirmation du mot de passe",
    "forgot password", "mot de passe oublié",
    "remember me", "rester connecté", "keep me signed in",
    "envoyer le code", "send code", "reset code",
    "retour à la connexion", "return to login",
})
# Segments d'URL d'authentification
_BROWSER_SURFACE_AUTH_FORM_URL_SEGMENTS: frozenset[str] = frozenset({
    "/login", "/signin", "/connexion", "/sign-in", "/log-in",
    "/auth/", "/auth.", "/public/auth", "/compte/connexion", "/account/login",
})
# Signaux pour formulaire de contact (pas de mot de passe)
_BROWSER_SURFACE_CONTACT_FORM_HINTS: frozenset[str] = frozenset({
    "formulaire de contact", "contact form", "nous contacter", "contact us",
    "votre message", "your message", "objet du message", "message subject",
    "demande de contact", "contact request",
    "newsletter", "s'abonner à", "subscribe to",
})
_BROWSER_SURFACE_CONTACT_ACTION_HINTS: frozenset[str] = frozenset({
    'button "envoyer votre message"', 'button "nous contacter"',
    'button "contact us"', 'button "subscribe"',
})
# Signaux pour page de détail produit/événement
_BROWSER_SURFACE_DETAIL_PAGE_HINTS: frozenset[str] = frozenset({
    "ajouter au panier", "add to cart", "add to bag",
    "acheter maintenant", "buy now", "commander",
    "billetterie", "réserver", "book now", "réservation en ligne",
    "fiche produit", "product details",
    "en stock", "in stock", "rupture de stock", "out of stock",
    "quantité :", "quantity:", "taille :", "couleur :",
    "prix :", "price:", "tarif :", "à partir de",
    "date :", "lieu :", "horaires :", "programme :",
    "durée :", "catégorie :", "mise en scène",
})
# Signaux pour SPA shell — contenu non chargé / JS requis
_BROWSER_SURFACE_SPA_SHELL_HINTS: frozenset[str] = frozenset({
    "javascript is required", "javascript requis", "please enable javascript",
    "activez javascript pour", "javascript must be enabled",
    "application loading", "app is loading",
    "chargement de l'application",
    "interactive elements: 0\n", "interactive elements: 1\n",
})
_BROWSER_PLAN_PASSIVE_TOOLS: frozenset[str] = frozenset({
    "browser_navigate", "browser_dom_state", "browser_screenshot",
    "browser_screenshot_labels", "browser_page_info", "browser_get_content",
    "browser_get_text", "browser_frames", "browser_frame_content",
    "browser_scroll", "browser_wait_for",
})

_READ_ONLY_DISCOVERY_PLAN_TOOLS: frozenset[str] = frozenset({
    "web_fetch",
    "web_search",
    "web_search_brave",
    "browser_search_google",
    "get_time",
    "health_check",
    "process_status",
})

_BROWSER_AUXILIARY_ACTION_MARKERS: frozenset[str] = frozenset({
    'copy to clipboard',
    'button "copy to clipboard"',
    'button "like"',
    'button "dislike"',
    'button "rewrite"',
    'button "toggle theme"',
    'button "settings"',
})


def _browser_observation_is_auxiliary_action(tool_name: str, observation_text: str) -> bool:
    """Détecte un clic browser neutre qui ne doit pas compter comme progression métier."""
    if not tool_name.startswith("browser_"):
        return False
    lower = (observation_text or "").lower()
    if not lower:
        return False
    if "clic sur" not in lower and "clique sur" not in lower:
        return False
    return any(marker in lower for marker in _BROWSER_AUXILIARY_ACTION_MARKERS)


def _browser_observation_looks_like_popup_or_modal(observation_text: str) -> bool:
    lower = (observation_text or "").lower()
    if not lower:
        return False
    popup_markers = (
        "popup",
        "pop-up",
        "modal",
        "dialog",
        "annuler",
        "ouvrir",
        "fermer",
        "close",
        "google_vignette",
        "vignette",
        "publicité",
        "advertisement",
        "adsense",
    )
    return any(marker in lower for marker in popup_markers)


def _classify_browser_surface(
    obs_text: str,
    *,
    current_url: str = "",
    page_title: str = "",
    previous_surface: str = "",
    allow_impasse: bool = True,
) -> "tuple[str, str]":
    """Classe la surface browser courante pour guider la stratégie ReAct."""
    blob = "\n".join(x for x in (current_url, page_title, obs_text) if x).strip()
    if not blob:
        return "unknown", "aucun signal exploitable"

    lower = blob.lower()
    _looks_like_terse_action_confirmation = (
        "✅" in obs_text
        and any(tok in lower for tok in (
            "clic sur", "clique sur", "tape ", "texte saisi", "textbox", "button",
        ))
    )

    if allow_impasse:
        blocked, reason, try_dismiss = _detect_browser_impasse(lower)
        if blocked:
            if try_dismiss:
                return "popup_blocked", reason
            if any(tok in lower for tok in (
                "cloudflare", "checking your browser", "just a moment",
                "challenge_running", "captcha", "recaptcha", "i'm not a robot",
            )):
                return "anti_bot_or_challenge", reason
            if any(tok in lower for tok in (
                "you must be logged in", "you must sign in", "please log in to continue",
                "please sign in to continue", "login required", "sign in required",
                "members only", "subscribers only", "authentication required",
                "your session has expired",
            )):
                return "login_wall", reason
            if any(tok in lower for tok in (
                "no interactive elements found", "0 elements found", "aucun élément interactif",
            )):
                return "non_interactive", reason
            return "error_page", reason

    # Détection listing par URL — sites d'annonces connus + segment de chemin
    _url_lower = current_url.lower()
    if _url_lower and (
        any(domain in _url_lower for domain in _BROWSER_LISTING_URL_DOMAINS)
        and any(seg in _url_lower for seg in _BROWSER_LISTING_URL_PATH_SEGMENTS)
    ):
        return "listing_results", f"URL de site d'annonces reconnue ({current_url})"

    if any(tok in lower for tok in _BROWSER_SURFACE_BUILDER_HINTS):
        return "builder_editor", "surface éditeur/builder détectée"
    if any(tok in lower for tok in _BROWSER_SURFACE_SEARCH_HINTS):
        return "search_results", "surface de résultats de recherche détectée"
    if any(tok in lower for tok in _BROWSER_SURFACE_LISTING_HINTS):
        return "listing_results", "surface de petites annonces détectée"
    if any(tok in lower for tok in _BROWSER_SURFACE_IFRAME_HINTS):
        return "iframe_heavy", "surface pilotée par des iframes détectée"

    # SPA shell : aucun contenu utile chargé (JS requis / application en cours de chargement)
    if any(tok in lower for tok in _BROWSER_SURFACE_SPA_SHELL_HINTS):
        return "spa_shell", "SPA shell sans contenu utile détecté"

    # Détection de formulaire typé : auth_form > contact_form > detail_page > public_form.
    # Sur les SPA, /connexion peut afficher encore le formulaire de contact; il faut
    # le reconnaître explicitement au lieu de le laisser tomber dans public_form.
    if _looks_like_chat_transcript(obs_text):
        return "chat_transcript", "transcription de conversation détectée"

    _chat_signal_hits = sum(1 for tok in _BROWSER_SURFACE_CHAT_HINTS if tok in lower)
    _has_chat_signal = _chat_signal_hits > 0
    _has_chat_controls = any(tok in lower for tok in (
        "think",
        "tools",
        "send question",
        "send message",
        "voice mode",
        "start chatting",
        "new discussion",
        "new chat",
        "nouvelle discussion",
        "nouveau chat",
        "chat vocal",
        "ask anything",
        "prosemirror",
    ))

    _has_password = any(tok in lower for tok in _BROWSER_SURFACE_AUTH_FORM_HINTS)
    _has_auth_url = bool(_url_lower) and any(
        seg in _url_lower for seg in _BROWSER_SURFACE_AUTH_FORM_URL_SEGMENTS
    )
    _has_form_ctrl = any(tok in lower for tok in _BROWSER_SURFACE_PUBLIC_FORM_HINTS)
    _contact_hits = sum(1 for tok in _BROWSER_SURFACE_CONTACT_FORM_HINTS if tok in lower)
    _has_contact_action = any(tok in lower for tok in _BROWSER_SURFACE_CONTACT_ACTION_HINTS)
    _has_contact = (
        _contact_hits >= 1
        or "nous contacter" in lower
        or "contact us" in lower
        or _has_contact_action
    )

    if (
        previous_surface in {"chat_composer", "chat_transcript"}
        and _looks_like_terse_action_confirmation
        and (_has_chat_signal or _has_chat_controls or ("chat" in lower and _has_form_ctrl))
    ):
        return previous_surface, "confirmation d'action sur une vue de chat deja etablie"

    if (
        (_has_chat_signal and _has_chat_controls)
        or ("chat" in lower and _has_form_ctrl and _has_chat_controls)
    ):
        return "chat_composer", "surface de chat conversationnel detectee"

    if (_has_password or _has_auth_url) and _has_form_ctrl:
        if _has_password:
            return "auth_form", "formulaire d'authentification détecté (champ mot de passe présent)"
        if _has_contact:
            return "contact_form", "URL d'auth détectée mais le formulaire visible est un contact/home form (SPA probablement sur la mauvaise vue)"

    # Page de détail riche (produit, événement, concert)
    if any(tok in lower for tok in _BROWSER_SURFACE_DETAIL_PAGE_HINTS):
        return "detail_page", "page de détail produit/événement détectée"

    if _has_contact and not _has_password:
        return "contact_form", "formulaire de contact ou newsletter détecté"

    if (
        previous_surface == "auth_form"
        and _has_form_ctrl
        and _looks_like_terse_action_confirmation
        and not _has_contact
    ):
        return "auth_form", "confirmation d'action sur une vue d'auth déjà établie"

    if _has_form_ctrl:
        return "public_form", "surface de formulaire remplissable détectée"

    if any(tok in lower for tok in _BROWSER_SURFACE_ERROR_HINTS):
        return "error_page", "surface d'erreur détectée"

    if previous_surface and previous_surface in BROWSER_SURFACE_TYPES:
        return previous_surface, f"surface héritée depuis l'état précédent ({previous_surface})"

    return "normal_content", "contenu standard sans signal fort"


def _browser_surface_mismatch(surface: str, query: str) -> "tuple[bool, str]":
    """Détecte les mésalignements surface ↔ objectif utilisateur les plus utiles."""
    q = (query or "").lower()
    wants_form_fill = any(tok in q for tok in _BROWSER_SURFACE_FILL_FORM_HINTS)
    wants_auth = any(tok in q for tok in _BROWSER_SURFACE_AUTH_HINTS)

    if surface == "builder_editor" and wants_form_fill:
        return True, "tu es dans un éditeur/builder, pas dans un formulaire public remplissable"
    if surface == "login_wall" and not wants_auth:
        return True, "la page exige une connexion alors que la tâche ne demande pas une authentification"
    # Mismatch auth_form ↔ contact_form
    if surface == "contact_form" and wants_auth:
        return True, "tu es sur un formulaire de contact, pas un formulaire de connexion — cherche la page /login ou /connexion"
    if surface == "auth_form" and wants_form_fill and not wants_auth:
        return True, "tu es sur un formulaire de connexion, pas un formulaire de contact public remplissable"
    # NOTE: le cas public_form+wants_auth est intentionnellement supprimé : trop de faux positifs
    # (Perplexity, formulaires génériques) car wants_auth match sur "connexion"/"compte" très courants.
    # Les vrais mismatches auth sont couverts par auth_form/contact_form/login_wall.
    return False, ""


def _browser_is_auth_intent(query: str) -> bool:
    q = (query or "").lower()
    return any(tok in q for tok in _BROWSER_SURFACE_AUTH_HINTS.union({
        "mot de passe", "password", "oublié", "oublie", "forgot password",
    }))


def _extract_browser_auth_target(obs_text: str) -> Optional[tuple[str, str]]:
    """Extrait un lien/bouton de connexion visible depuis browser_dom_state."""
    if not obs_text:
        return None
    auth_tokens = (
        "connexion", "connecter", "se connecter", "login",
        "log in", "sign in", "authentification",
    )
    for line in obs_text.splitlines():
        m = re.match(r'^\[(\d+)\]\s+(link|button)\s+"([^"]+)"', line.strip(), re.IGNORECASE)
        if not m:
            continue
        label = m.group(3).strip()
        lower = label.lower()
        if any(tok in lower for tok in auth_tokens):
            return m.group(1), label
    return None


def _extract_browser_textbox_target(
    obs_text: str,
    *,
    index: Optional[str] = None,
) -> Optional[tuple[str, str, str]]:
    """Extrait un champ texte visible depuis browser_dom_state.

    Retourne (index, role, label) si l'élément ciblé est un champ texte.
    """
    if not obs_text:
        return None
    wanted = str(index).strip() if index is not None else ""
    text_roles = {"textbox", "searchbox", "combobox", "spinbutton", "textarea"}
    fallback = None
    for line in obs_text.splitlines():
        m = re.match(r'^\[(\d+)\]\s+([a-z_]+)\s+"([^"]*)"', line.strip(), re.IGNORECASE)
        if not m:
            m = re.search(r'\[(\d+)\]\s+([a-z_]+)\s+"([^"]*)"', line.strip(), re.IGNORECASE)
            if not m:
                continue
        idx, role, label = m.group(1), m.group(2).lower(), m.group(3).strip()
        if role not in text_roles:
            continue
        if wanted and idx == wanted:
            return idx, role, label
        if fallback is None:
            fallback = (idx, role, label)
    return fallback


def _extract_browser_textbox_targets(obs_text: str) -> list[tuple[str, str, str]]:
    """Retourne tous les champs texte visibles depuis browser_dom_state."""
    if not obs_text:
        return []
    text_roles = {"textbox", "searchbox", "combobox", "spinbutton", "textarea"}
    matches: list[tuple[str, str, str]] = []
    for line in obs_text.splitlines():
        m = re.match(r'^\[(\d+)\]\s+([a-z_]+)\s+"([^"]*)"', line.strip(), re.IGNORECASE)
        if not m:
            m = re.search(r'\[(\d+)\]\s+([a-z_]+)\s+"([^"]*)"', line.strip(), re.IGNORECASE)
            if not m:
                continue
        idx, role, label = m.group(1), m.group(2).lower(), m.group(3).strip()
        if role in text_roles:
            matches.append((idx, role, label))
    return matches


def _browser_passive_tool_can_complete_task(tool_name: str, task_desc: str) -> bool:
    """Autorise seulement certaines tâches de plan pour les outils browser passifs."""
    desc = (task_desc or "").lower()
    if tool_name == "browser_navigate":
        return any(tok in desc for tok in (
            "naviguer", "aller", "ouvrir", "accéder", "acceder", "visiter",
            "vérifier", "verifier", "accessible", "opérationnel", "operationnel",
        ))
    if tool_name in {
        "browser_dom_state", "browser_screenshot", "browser_screenshot_labels",
        "browser_page_info", "browser_get_content", "browser_get_text",
        "browser_frames", "browser_frame_content",
    }:
        # Exclure les tâches qui mentionnent des contextes non-browser
        if any(excl in desc for excl in ("email", "mail", "spam", "sms", "téléphone", "telephone", "appel")):
            return False
        return any(tok in desc for tok in (
            "trouver", "identifier", "repérer", "reperer", "inspecter",
            "voir", "lire", "analyser", "localiser", "détecter", "detecter",
            "vérifier", "verifier", "confirmer",
        ))
    if tool_name == "browser_scroll":
        return any(tok in desc for tok in ("scroller", "scroll", "charger plus"))
    return False


def _read_only_discovery_tool_can_complete_task(tool_name: str, task_desc: str) -> bool:
    desc = (task_desc or "").lower()
    if tool_name == "get_time":
        return any(tok in desc for tok in ("heure", "date", "horaire", "time"))
    if tool_name in {"health_check", "process_status"}:
        return any(tok in desc for tok in (
            "statut", "status", "santé", "sante", "health",
            "vérifier", "verifier", "accessible", "opérationnel", "operationnel",
            "disponible", "fonctionne", "running", "alive", "check",
            "lancer", "démarrer", "demarrer", "serveur", "server", "port",
        ))
    if tool_name in {"web_fetch", "web_search", "web_search_brave", "browser_search_google"}:
        if any(tok in desc for tok in ("échanger", "echanger", "discussion", "conversation", "discuter", "parler", "envoyer")):
            return False
        return any(tok in desc for tok in (
            "vérifier", "verifier", "chercher", "rechercher", "trouver",
            "identifier", "inspecter", "lire", "consulter", "analyser",
            "comparer", "regarder",
        ))
    return True


def _browser_rewrite_human_navigation_action(
    tool_name: str,
    tool_args: Dict[str, Any],
    *,
    query: str,
    last_surface: str,
    last_observation: str,
) -> Optional[tuple[str, Dict[str, Any], str]]:
    """Préférence au clic réel sur le web avant une renavigation auth redondante."""
    if tool_name != "browser_navigate":
        return None
    if last_surface != "contact_form":
        return None
    if not _browser_is_auth_intent(query):
        return None
    target_url = str((tool_args or {}).get("url", "")).lower()
    if not any(seg in target_url for seg in _BROWSER_SURFACE_AUTH_FORM_URL_SEGMENTS):
        return None
    auth_target = _extract_browser_auth_target(last_observation)
    if not auth_target:
        return None
    idx, label = auth_target
    return (
        "browser_click_index",
        {"index": idx},
        f"préférence au clic réel sur [{idx}] {label!r} avant une renavigation auth redondante",
    )


def _browser_rewrite_text_entry_action(
    tool_name: str,
    tool_args: Dict[str, Any],
    *,
    last_observation: str,
) -> Optional[tuple[str, Dict[str, Any], str]]:
    """Réécrit les faux clics de saisie en vrai type_index.

    Cas réel vu dans les logs:
    - le modèle appelle browser_click_index sur un textbox
    - avec `text` ou `value` en argument parasite
    - le registry supprime l'arg, donc rien n'est écrit
    """
    if tool_name != "browser_click_index":
        return None
    if not tool_args:
        return None
    idx = str(tool_args.get("index", "")).strip()
    if not idx:
        return None
    text_value = tool_args.get("text")
    if text_value is None:
        text_value = tool_args.get("value")
    if text_value is None:
        return None
    text_value = str(text_value).strip()
    if not text_value:
        return None
    textbox = _extract_browser_textbox_target(last_observation, index=idx)
    if textbox is None:
        return None
    _tb_idx, role, label = textbox
    return (
        "browser_type_index",
        {"index": _tb_idx, "text": text_value},
        f"saisie détectée sur [{_tb_idx}] {role} {label!r} — conversion du faux clic en browser_type_index",
    )


_BROWSER_CLICK_ONLY_ROLES: frozenset[str] = frozenset({
    "radio", "checkbox", "button", "submit button",
    "menuitem", "option", "tab", "switch",
})


def _browser_rewrite_type_to_click_for_ctrl(
    tool_name: str,
    tool_args: Dict[str, Any],
    *,
    last_observation: str,
) -> Optional[tuple[str, Dict[str, Any], str]]:
    """Réécrit browser_type_index en browser_click_index quand la cible est un contrôle non-texte.

    Cas réel vu dans les logs :
      browser_type_index(index=3, text="oui") sur un radio → playwright rejette la saisie
      car les radios/checkboxes/boutons ne sont pas des champs texte.

    Ne s'applique que si l'index ciblé est explicitement reconnu comme un contrôle
    non-texte dans la dernière observation.
    """
    if tool_name != "browser_type_index":
        return None
    if not tool_args:
        return None
    idx = str(tool_args.get("index", "")).strip()
    if not idx:
        return None
    if not last_observation:
        return None
    # Chercher l'élément ciblé dans l'observation
    for line in last_observation.splitlines():
        m = re.match(r'^\[(\d+)\]\s+([a-z_\s]+)\s+"([^"]*)"', line.strip(), re.IGNORECASE)
        if not m:
            m = re.search(r'\[(\d+)\]\s+([a-z_\s]+?)\s+"([^"]*)"', line.strip(), re.IGNORECASE)
            if not m:
                continue
        line_idx, role, label = m.group(1), m.group(2).strip().lower(), m.group(3).strip()
        if line_idx != idx:
            continue
        if role in _BROWSER_CLICK_ONLY_ROLES or any(
            ctrl in role for ctrl in ("radio", "checkbox", "button", "submit")
        ):
            return (
                "browser_click_index",
                {"index": idx},
                f"réécriture type→click sur [{idx}] {role} {label!r} — les contrôles {role} s'activent par clic, pas par saisie",
            )
        break  # index trouvé mais c'est un champ texte → pas de réécriture
    return None


def _browser_rewrite_index_like_selector_action(
    tool_name: str,
    tool_args: Dict[str, Any],
) -> Optional[tuple[str, Dict[str, Any], str]]:
    """Réécrit un faux sélecteur CSS `[12]` vers les outils DOM indexés.

    Cas réel vu dans les logs :
      browser_type(selector='[16]', text='LumenaAI')
    alors que `[16]` représente l'index DOM exposé par browser_dom_state,
    pas un sélecteur CSS valide.
    """
    if tool_name not in {"browser_type", "browser_click"}:
        return None
    selector = str((tool_args or {}).get("selector", "")).strip()
    if not selector:
        return None
    match = re.fullmatch(r"\[(\d+)\]", selector)
    if match is None:
        return None
    idx = match.group(1)
    if tool_name == "browser_type":
        if "text" not in (tool_args or {}):
            return None
        return (
            "browser_type_index",
            {"index": idx, "text": tool_args.get("text", "")},
            f"sélecteur '{selector}' reconnu comme index DOM [{idx}] — conversion vers browser_type_index",
        )
    return (
        "browser_click_index",
        {"index": idx},
        f"sélecteur '{selector}' reconnu comme index DOM [{idx}] — conversion vers browser_click_index",
    )


def _browser_rewrite_selector_guess_to_index_action(
    tool_name: str,
    tool_args: Dict[str, Any],
    *,
    last_surface: str,
    last_observation: str,
) -> Optional[tuple[str, Dict[str, Any], str]]:
    """Convertit un browser_type à sélecteur deviné vers browser_type_index.

    Cas réel vu dans les logs :
      - browser_dom_state expose un unique textbox [10] "Ask anything"
      - le modèle tente browser_type(selector='textarea[aria-label="Ask anything"]', ...)
      - ou browser_type(selector='text=Ask anything', ...)
      - alors que le chemin robuste attendu est browser_type_index(index=10, ...)
    """
    if tool_name != "browser_type":
        return None
    if last_surface not in {"chat_composer", "public_form", "auth_form", "contact_form"}:
        return None
    text_value = str((tool_args or {}).get("text", "") or "").strip()
    if not text_value:
        return None
    selector = str((tool_args or {}).get("selector", "") or "").strip()
    if not selector:
        return None
    if re.fullmatch(r"\[(\d+)\]", selector):
        return None

    selector_lower = selector.lower()
    heuristic_tokens = (
        "textarea",
        "textbox",
        "contenteditable",
        "prosemirror",
        "ask anything",
        "text=",
        '[role="textbox"]',
        "[role='textbox']",
    )
    if not any(token in selector_lower for token in heuristic_tokens):
        return None

    textboxes = _extract_browser_textbox_targets(last_observation)
    if len(textboxes) != 1:
        return None
    idx, role, label = textboxes[0]
    return (
        "browser_type_index",
        {"index": idx, "text": text_value},
        f"sélecteur browser guessed '{selector}' — conversion vers browser_type_index sur [{idx}] {role} {label!r}",
    )


def _extract_sendkeys_payload(command: str) -> Optional[str]:
    """Extrait le texte envoyé par un script Windows SendKeys/SendWait."""
    if not command:
        return None
    patterns = (
        r"SendKeys\(\s*'([^']+)'\s*\)",
        r'SendKeys\(\s*"([^"]+)"\s*\)',
        r"SendWait\(\s*'([^']+)'\s*\)",
        r'SendWait\(\s*"([^"]+)"\s*\)',
    )
    for pattern in patterns:
        m = re.search(pattern, command, re.IGNORECASE)
        if m:
            payload = m.group(1).strip()
            if payload:
                return payload
    return None


def _browser_rewrite_system_typing_action(
    tool_name: str,
    tool_args: Dict[str, Any],
    *,
    last_observation: str,
    last_textbox_index: str = "",
) -> Optional[tuple[str, Dict[str, Any], str]]:
    """Remplace les SendKeys système par une vraie saisie Playwright.

    Les logs ont montré que cette voie dépend du clavier Windows
    (Caps Lock/layout/focus) et contourne inutilement Playwright.
    """
    if tool_name != "run_command":
        return None
    command = str((tool_args or {}).get("command", "")).strip()
    if not command:
        return None
    payload = _extract_sendkeys_payload(command)
    if payload is None:
        return None
    textbox = None
    if last_textbox_index:
        textbox = _extract_browser_textbox_target(last_observation, index=last_textbox_index)
        if textbox is None:
            return (
                "browser_type_index",
                {"index": str(last_textbox_index), "text": payload},
                f"commande système SendKeys détectée — conversion vers browser_type_index sur le dernier champ texte ciblé [{last_textbox_index}]",
            )
    if textbox is None:
        textbox = _extract_browser_textbox_target(last_observation)
    if textbox is None:
        return None
    idx, role, label = textbox
    return (
        "browser_type_index",
        {"index": idx, "text": payload},
        f"commande système SendKeys détectée — conversion vers browser_type_index sur [{idx}] {role} {label!r}",
    )


def _extract_browser_interactive_count(obs_text: str) -> Optional[int]:
    """Extrait le nombre d'éléments interactifs depuis une observation browser."""
    if not obs_text:
        return None
    m = re.search(r"Interactive elements:\s*(\d+)", obs_text, re.IGNORECASE)
    if not m:
        return None
    try:
        return int(m.group(1))
    except Exception:
        return None


def _extract_browser_form_state(obs_text: str) -> Optional[tuple]:
    """Extrait l'état de formulaire depuis une observation browser.

    Format attendu : "Form state: filled=X, checked=Y, disabled_buttons=Z,
                      enabled_submit_buttons=W, controls=V"

    Retourne (filled, checked, disabled_buttons, enabled_submit_buttons, controls) ou None.
    """
    if not obs_text:
        return None
    m = re.search(
        r"Form state:\s*filled=(\d+),\s*checked=(\d+),\s*disabled_buttons=(\d+)"
        r",\s*enabled_submit_buttons=(\d+),\s*controls=(\d+)",
        obs_text,
        re.IGNORECASE,
    )
    if not m:
        return None
    try:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)),
                int(m.group(4)), int(m.group(5)))
    except Exception:
        return None


def _make_browser_progress_signature(
    surface: str,
    obs_text: str,
    *,
    current_url: str = "",
    page_title: str = "",
    previous: Optional[tuple] = None,
) -> tuple:
    """Construit une signature stable pour mesurer la progression browser.

    Structure 6-tuple :
      (surface, url, title, interactive_bucket, form_state, extra_signal)

    Les champs manquants réutilisent le précédent état pour éviter les faux
    no-progress sur `browser_screenshot` pur.
    Rétrocompatible avec les anciens 4-tuples en entrée (previous).
    """
    if previous is None:
        prev_surface, prev_url, prev_title = "", "", ""
        prev_bucket, prev_form, prev_extra = None, None, None
    else:
        prev_surface = previous[0] if len(previous) > 0 else ""
        prev_url     = previous[1] if len(previous) > 1 else ""
        prev_title   = previous[2] if len(previous) > 2 else ""
        prev_bucket  = previous[3] if len(previous) > 3 else None
        prev_form    = previous[4] if len(previous) > 4 else None
        prev_extra   = previous[5] if len(previous) > 5 else None

    _count = _extract_browser_interactive_count(obs_text)
    _bucket = None if _count is None else _count // 10
    _form = _extract_browser_form_state(obs_text)

    return (
        surface or prev_surface or "unknown",
        current_url or prev_url or "",
        page_title or prev_title or "",
        _bucket if _bucket is not None else prev_bucket,
        _form if _form is not None else prev_form,
        prev_extra,
    )


def _browser_progress_delta(
    previous_sig: Optional[tuple],
    current_sig: tuple,
    *,
    action_tool: str = "",
    observation_text: str = "",
) -> "tuple[bool, str]":
    """Détermine si le browser progresse réellement entre deux états.

    Accepte des 4-tuples ou 6-tuples (rétrocompatible).
    """
    if previous_sig is None:
        return True, "premier état browser"

    prev_surface = previous_sig[0] if len(previous_sig) > 0 else ""
    prev_url     = previous_sig[1] if len(previous_sig) > 1 else ""
    prev_title   = previous_sig[2] if len(previous_sig) > 2 else ""
    prev_bucket  = previous_sig[3] if len(previous_sig) > 3 else None
    prev_form    = previous_sig[4] if len(previous_sig) > 4 else None
    prev_extra   = previous_sig[5] if len(previous_sig) > 5 else None

    cur_surface = current_sig[0] if len(current_sig) > 0 else ""
    cur_url     = current_sig[1] if len(current_sig) > 1 else ""
    cur_title   = current_sig[2] if len(current_sig) > 2 else ""
    cur_bucket  = current_sig[3] if len(current_sig) > 3 else None
    cur_form    = current_sig[4] if len(current_sig) > 4 else None
    cur_extra   = current_sig[5] if len(current_sig) > 5 else None

    if cur_surface != prev_surface:
        return True, f"surface changée ({prev_surface} → {cur_surface})"
    if cur_url and prev_url and cur_url != prev_url:
        return True, "url changée"
    if cur_title and prev_title and cur_title != prev_title:
        return True, "titre changé"
    if action_tool == "browser_navigate" and cur_surface == prev_surface and cur_url and prev_url and cur_url == prev_url:
        return False, "renavigation vers la même URL sans changement visible (SPA probable)"
    if (
        cur_bucket is not None and prev_bucket is not None
        and cur_bucket != prev_bucket
        and action_tool in BROWSER_ACTION_TOOLS.union({"browser_frames", "browser_frame_content"})
    ):
        return True, "densité interactive changée"

    if observation_text:
        obs_lower = observation_text.lower()
        if action_tool in ("browser_click", "browser_click_index", "browser_click_smart") and _browser_observation_is_auxiliary_action(
            action_tool, observation_text
        ):
            return False, "clic auxiliaire sans progression métier"
        if action_tool == "browser_keyboard_press" and (
            "soumission n'est probablement pas partie" in obs_lower
            or "enter n'a pas finalise l'envoi" in obs_lower
        ):
            return False, "enter n'a pas provoque de soumission utile"

    # Progression d'état de formulaire
    if cur_form is not None and prev_form is not None and len(cur_form) >= 4:
        cur_filled, cur_checked, _cur_dis, cur_submit = cur_form[:4]
        prev_filled, prev_checked, _prev_dis, prev_submit = prev_form[:4]
        if cur_filled > prev_filled:
            return True, "champs remplis en progression"
        if cur_checked > prev_checked:
            return True, "cases cochées en progression"
        if cur_submit > prev_submit:
            return True, "bouton de soumission activé"

    # Progression détectée via le texte d'observation (typage / case à cocher / navigation)
    if observation_text:
        if action_tool in ("browser_type", "browser_type_index"):
            if (
                "echec de saisie" in obs_lower
                or "valeur persistante:" in obs_lower
                or "valeur actuelle:" in obs_lower
            ):
                return False, "saisie non persistante ou explicitement en echec"
            if "soumission non prete" in obs_lower:
                return False, "saisie non confirmee par l'interface"
            if "✅" in observation_text or "typed" in obs_lower or "saisi" in obs_lower:
                return True, "saisie dans un champ réussie"
        if action_tool in ("browser_click", "browser_click_index", "browser_click_smart"):
            if "checkbox" in obs_lower or "case" in obs_lower:
                if "✅" in observation_text or "checked" in obs_lower or "coché" in obs_lower:
                    return True, "case à cocher activée"
            # Un clic sur un lien est une navigation — toujours progrès
            if " link " in obs_lower and ("✅ clic" in obs_lower or "✅ clic" in observation_text):
                return True, "clic sur un lien (navigation probable)"
            if ("envoyer le code" in obs_lower or "send code" in obs_lower) and "✅" in observation_text:
                return True, "soumission de code de vérification tentée"

        # browser_evaluate : contenu réel = progrès, bruit JS = pas de progrès
        if action_tool == "browser_evaluate":
            _eval_real = {
                "date", "lieu", "prix", "tarif", "billetterie", "concert",
                "événement", "evenement", "spectacle", "artiste", "salle",
                "disponible", "réservation", "reservation", "€", "$",
                "titre", "description", "horaire", "programme",
            }
            _eval_noise = {
                "undefined", "null", "typeerror", "syntaxerror", "referenceerror",
                "cannot read", "is not a function", "is not defined",
                "[object object]", "nan", "infinity",
            }
            _has_real = any(tok in obs_lower for tok in _eval_real)
            _has_noise = any(tok in obs_lower for tok in _eval_noise)
            if "✅" in observation_text and _has_real and not _has_noise:
                return True, "browser_evaluate retourne du contenu réel (date/lieu/prix/…)"
            if _has_noise and not _has_real:
                return False, "browser_evaluate retourne du bruit JS (erreur ou undefined)"

        # browser_dismiss_popups / browser_accept_cookies : dismiss réussi = progrès
        if action_tool in ("browser_dismiss_popups", "browser_accept_cookies"):
            if ("✅" in observation_text or "dismissed" in obs_lower
                    or "fermé" in obs_lower or "accepté" in obs_lower
                    or "closed" in obs_lower):
                return True, "overlay/cookie éliminé — page devenue plus accessible"

    # Progression listing : annonce cliquée ou nombre de labels changé
    if cur_extra is not None and prev_extra is not None:
        if len(cur_extra) >= 2 and len(prev_extra) >= 2 and cur_extra[1] != prev_extra[1]:
            return True, "annonce cliquée (listing)"
        if len(cur_extra) >= 3 and len(prev_extra) >= 3 and cur_extra[2] != prev_extra[2]:
            return True, "nombre de labels changé (listing)"

    return False, "même surface sans changement utile"


def _browser_observation_has_failure(tool_name: str, observation_content: str) -> bool:
    """Détecte les échecs browser usuels que classify_observation peut laisser passer."""
    if not tool_name.startswith("browser_"):
        return False
    lower = (observation_content or "").lower()
    if not lower.strip():
        return False
    failure_markers = (
        "aucun élément trouvé",
        "no element found",
        "no element matched",
        "paramètre(s) requis manquant",
        "required parameter",
        "invalid selector",
        "element not found",
        "élément introuvable",
        "timed out",
        "timeout",
        "erreur:",
        "failed to",
    )
    success_markers = ("✅", "succès", "success", "navigué vers", "clic sur", "texte tapé dans")
    return any(tok in lower for tok in failure_markers) and not any(tok in lower for tok in success_markers)


# ── Ensembles browser vision / action (module-level pour testabilité directe) ─
# Outils qui redonnent un état visuel/structurel → reset du blind streak
BROWSER_VISUAL_TOOLS: frozenset = frozenset({
    "browser_screenshot",        # vue pixel complète
    "browser_dom_state",         # liste indexée des éléments cliquables
    "browser_get_content",       # HTML/texte brut de la page
    "browser_frames",            # liste des iframes → état structurel
    "browser_frame_content",     # contenu d'un frame → relecture visuelle
    "browser_screenshot_labels", # screenshot + labels visuels enrichis
    "browser_page_info",         # URL, titre, dimensions — état minimal
    "browser_get_text",          # texte extrait — relecture structurelle
})

# Outils d'action (interactions) → incrémentent le blind streak
BROWSER_SELF_VISUAL_ACTION_TOOLS: frozenset = frozenset({
    "browser_navigate",
    "browser_click",
    "browser_click_index",
    "browser_click_smart",
    "browser_type_index",
})

BROWSER_ACTION_TOOLS: frozenset = frozenset({
    "browser_click", "browser_click_index", "browser_click_smart",
    "browser_click_at", "browser_type", "browser_type_index",
    "browser_navigate", "browser_hover", "browser_select",
    "browser_keyboard_press", "browser_drag", "browser_drag_at",
})

# Outils système vers lesquels le LLM peut dériver après un blocage browser
_BROWSER_DRIFT_TOOLS: frozenset = frozenset({
    "run_command", "run_shell", "exec_command", "web_fetch", "curl",
})


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
        self.llm_meta_getter = llm_meta_getter
        self.max_final_repair_attempts = max(0, int(max_final_repair_attempts))
        self.task_orchestrator = task_orchestrator
        self.task_id = (task_id or "").strip() or None
        self.is_weak_model = bool(is_weak_model)
        self.step_callback: Optional[Callable[[str, dict], None]] = step_callback
        self.runtime_ctx = runtime_ctx  # RuntimeContext snapshot (Phase 2)
        # ── Plan TODO ──
        self._task_plan: List[TaskItem] = []
        self._plan_emitted: bool = False
        self._plan_last_emit_state: str = ""  # dédup: n'émet TODO_STATE que si changé
        self._iterations_without_progress: int = 0
        self._last_completed_task_count: int = 0
        # P5 — profil comportemental par modèle (chargé dynamiquement à la première itération)
        self._model_profile = None
        self._model_profile_applied_for: str = ""
        # ── ExecutionLedger — source de vérité d'exécution (V1) ──
        self.execution_ledger = ExecutionLedger()
        # ── AgentExecutionState — état structuré (V1) ──
        self.exec_state = AgentExecutionState()

    # ── Propriétés-alias : compatibilité transitoire vers exec_state ─────
    # Permettent à tout le code existant de continuer à écrire
    # self._consecutive_same_action = X  et  if self._run_meta[...]:
    # sans changement, tout en centralisant l'état dans exec_state.
    # À retirer progressivement quand les consommateurs seront migrés.

    # --- guards ---
    @property
    def _consecutive_same_action(self):
        self._ensure_exec_state()
        return self.exec_state.guards.consecutive_same_action
    @_consecutive_same_action.setter
    def _consecutive_same_action(self, v):
        self._ensure_exec_state()
        self.exec_state.guards.consecutive_same_action = v

    @property
    def _last_action_signature(self):
        self._ensure_exec_state()
        return self.exec_state.guards.last_action_signature
    @_last_action_signature.setter
    def _last_action_signature(self, v):
        self._ensure_exec_state()
        self.exec_state.guards.last_action_signature = v

    @property
    def _pending_loop_guidance(self):
        self._ensure_exec_state()
        return self.exec_state.guards.pending_loop_guidance
    @_pending_loop_guidance.setter
    def _pending_loop_guidance(self, v):
        self._ensure_exec_state()
        self.exec_state.guards.pending_loop_guidance = v

    @property
    def _last_auto_advance_iter(self):
        self._ensure_exec_state()
        return self.exec_state.guards.last_auto_advance_iter
    @_last_auto_advance_iter.setter
    def _last_auto_advance_iter(self, v):
        self._ensure_exec_state()
        self.exec_state.guards.last_auto_advance_iter = v

    @property
    def _last_browser_visual_iter(self):
        self._ensure_exec_state()
        return self.exec_state.guards.last_browser_visual_iter
    @_last_browser_visual_iter.setter
    def _last_browser_visual_iter(self, v):
        self._ensure_exec_state()
        self.exec_state.guards.last_browser_visual_iter = v

    @property
    def _browser_blind_streak(self):
        self._ensure_exec_state()
        return self.exec_state.guards.browser_blind_streak
    @_browser_blind_streak.setter
    def _browser_blind_streak(self, v):
        self._ensure_exec_state()
        self.exec_state.guards.browser_blind_streak = v

    @property
    def _last_browser_surface(self):
        self._ensure_exec_state()
        return self.exec_state.guards.last_browser_surface
    @_last_browser_surface.setter
    def _last_browser_surface(self, v):
        self._ensure_exec_state()
        self.exec_state.guards.last_browser_surface = v

    @property
    def _last_browser_surface_reason(self):
        self._ensure_exec_state()
        return self.exec_state.guards.last_browser_surface_reason
    @_last_browser_surface_reason.setter
    def _last_browser_surface_reason(self, v):
        self._ensure_exec_state()
        self.exec_state.guards.last_browser_surface_reason = v

    @property
    def _browser_surface_streak(self):
        self._ensure_exec_state()
        return self.exec_state.guards.browser_surface_streak
    @_browser_surface_streak.setter
    def _browser_surface_streak(self, v):
        self._ensure_exec_state()
        self.exec_state.guards.browser_surface_streak = v

    @property
    def _last_browser_progress_sig(self):
        self._ensure_exec_state()
        return self.exec_state.guards.last_browser_progress_sig
    @_last_browser_progress_sig.setter
    def _last_browser_progress_sig(self, v):
        self._ensure_exec_state()
        self.exec_state.guards.last_browser_progress_sig = v

    @property
    def _browser_no_progress_streak(self):
        self._ensure_exec_state()
        return self.exec_state.guards.browser_no_progress_streak
    @_browser_no_progress_streak.setter
    def _browser_no_progress_streak(self, v):
        self._ensure_exec_state()
        self.exec_state.guards.browser_no_progress_streak = v

    # --- repairs ---
    @property
    def _final_repair_attempts(self):
        self._ensure_exec_state()
        return self.exec_state.repairs.final_repair_attempts
    @_final_repair_attempts.setter
    def _final_repair_attempts(self, v):
        self._ensure_exec_state()
        self.exec_state.repairs.final_repair_attempts = v

    @property
    def _hallucination_repair_attempts(self):
        self._ensure_exec_state()
        return self.exec_state.repairs.hallucination_repair_attempts
    @_hallucination_repair_attempts.setter
    def _hallucination_repair_attempts(self, v):
        self._ensure_exec_state()
        self.exec_state.repairs.hallucination_repair_attempts = v

    @property
    def _thought_leak_repairs(self):
        self._ensure_exec_state()
        return self.exec_state.repairs.thought_leak_repairs
    @_thought_leak_repairs.setter
    def _thought_leak_repairs(self, v):
        self._ensure_exec_state()
        self.exec_state.repairs.thought_leak_repairs = v

    @property
    def _premature_final_retries(self):
        self._ensure_exec_state()
        return self.exec_state.repairs.premature_final_retries
    @_premature_final_retries.setter
    def _premature_final_retries(self, v):
        self._ensure_exec_state()
        self.exec_state.repairs.premature_final_retries = v

    @property
    def _plan_guard_retries(self):
        self._ensure_exec_state()
        return self.exec_state.repairs.plan_guard_retries
    @_plan_guard_retries.setter
    def _plan_guard_retries(self, v):
        self._ensure_exec_state()
        self.exec_state.repairs.plan_guard_retries = v

    @property
    def _verbalization_redirects(self):
        self._ensure_exec_state()
        return self.exec_state.repairs.verbalization_redirects
    @_verbalization_redirects.setter
    def _verbalization_redirects(self, v):
        self._ensure_exec_state()
        self.exec_state.repairs.verbalization_redirects = v

    @property
    def _action_inline_count(self):
        self._ensure_exec_state()
        return self.exec_state.repairs.action_inline_count
    @_action_inline_count.setter
    def _action_inline_count(self, v):
        self._ensure_exec_state()
        self.exec_state.repairs.action_inline_count = v

    @property
    def _ledger_final_guard_used(self):
        self._ensure_exec_state()
        return self.exec_state.repairs.ledger_final_guard_used
    @_ledger_final_guard_used.setter
    def _ledger_final_guard_used(self, v):
        self._ensure_exec_state()
        self.exec_state.repairs.ledger_final_guard_used = v

    @property
    def _pre_repair_answer(self):
        self._ensure_exec_state()
        return self.exec_state.repairs.pre_repair_answer
    @_pre_repair_answer.setter
    def _pre_repair_answer(self, v):
        self._ensure_exec_state()
        self.exec_state.repairs.pre_repair_answer = v

    @property
    def _after_delegate_success(self):
        self._ensure_exec_state()
        return self.exec_state.repairs.after_delegate_success
    @_after_delegate_success.setter
    def _after_delegate_success(self, v):
        self._ensure_exec_state()
        self.exec_state.repairs.after_delegate_success = v

    # --- budget ---
    @property
    def _category_iter_counts(self):
        self._ensure_exec_state()
        return self.exec_state.budget.iter_counts
    @_category_iter_counts.setter
    def _category_iter_counts(self, v):
        self._ensure_exec_state()
        self.exec_state.budget.iter_counts = v

    # --- run_meta (proxy dict → RunMeta dataclass) ---
    def _ensure_exec_state(self):
        """Lazy-init exec_state si absent (ex: object.__new__ dans les tests)."""
        if not hasattr(self, 'exec_state'):
            self.exec_state = AgentExecutionState()

    @property
    def _run_meta(self):
        self._ensure_exec_state()
        return RunMetaProxy(self.exec_state.run_meta)
    @_run_meta.setter
    def _run_meta(self, v):
        self._ensure_exec_state()
        if isinstance(v, dict):
            self.exec_state.run_meta.agent_output_incomplete = v.get("agent_output_incomplete", False)
            self.exec_state.run_meta.agent_output_warning = v.get("agent_output_warning")
            self.exec_state.run_meta.agent_repair_attempts = v.get("agent_repair_attempts", 0)
            self.exec_state.run_meta.agent_final_finish_reason = v.get("agent_final_finish_reason")

    # --- session tools ---
    @property
    def _all_session_tools(self):
        self._ensure_exec_state()
        return self.exec_state.all_session_tools
    @_all_session_tools.setter
    def _all_session_tools(self, v):
        self._ensure_exec_state()
        self.exec_state.all_session_tools = v

    @property
    def _successful_session_tools(self):
        """Outils dont l'observation.success était True — seule preuve fiable."""
        self._ensure_exec_state()
        return self.exec_state.successful_session_tools
    @_successful_session_tools.setter
    def _successful_session_tools(self, v):
        self._ensure_exec_state()
        self.exec_state.successful_session_tools = v

    # --- last_llm_meta ---
    @property
    def _last_llm_meta(self):
        self._ensure_exec_state()
        return self.exec_state.last_llm_meta
    @_last_llm_meta.setter
    def _last_llm_meta(self, v):
        self._ensure_exec_state()
        self.exec_state.last_llm_meta = v

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
            # Enrichir le checkpoint avec la projection du ledger (si disponible)
            enriched = payload
            if hasattr(self, 'execution_ledger') and self.execution_ledger.size > 0:
                from ..runtime.task_orchestrator import TaskOrchestrator as _TO
                enriched = _TO.enrich_checkpoint(
                    payload, self.execution_ledger.checkpoint_projection(),
                )
            mark_checkpoint = getattr(self.task_orchestrator, "mark_checkpoint", None)
            if callable(mark_checkpoint):
                mark_checkpoint(self.task_id, enriched)
        except Exception as exc:
            logger.debug("task orchestrator mark_checkpoint skipped: {}", exc)

    def _mark_task_done(self, summary: str) -> None:
        if not self._orchestrator_enabled():
            return
        try:
            # Enrichir le checkpoint final avec la projection du ledger
            if hasattr(self, 'execution_ledger') and self.execution_ledger.size > 0:
                from ..runtime.task_orchestrator import TaskOrchestrator as _TO
                final_cp = _TO.enrich_checkpoint(
                    {"phase": "done"},
                    self.execution_ledger.checkpoint_projection(),
                )
                mark_checkpoint = getattr(self.task_orchestrator, "mark_checkpoint", None)
                if callable(mark_checkpoint):
                    mark_checkpoint(self.task_id, final_cp)
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

    # ── StructuredState: accès sûr au structured_state du ConversationContext ──

    @property
    def _structured_state(self):
        """Retourne le StructuredState du ConversationContext, ou None si indisponible."""
        ctx = getattr(self, 'conversation_context', None)
        if ctx is not None and hasattr(ctx, 'structured_state'):
            return ctx.structured_state
        return None

    def _feed_structured_tool(self, tool_name: str) -> None:
        """Enregistre un outil dans le structured_state (recent_tools)."""
        ss = self._structured_state
        if ss is not None:
            ss.record_tool(tool_name)

    def _feed_structured_intent(self, intent: Optional[str]) -> None:
        """Alimente last_intent avec la valeur classifiée."""
        if intent is None:
            return
        ss = self._structured_state
        if ss is not None:
            ss.last_intent = str(intent).strip() or None

    @staticmethod
    def _infer_intent_from_query(query: str) -> Optional[str]:
        """Inférence légère de l'intent depuis la requête (fallback sans classifier).

        Retourne une valeur grossière parmi :
        code_edit | discord | web_search | file_ops | create_project | conversation | question
        Retourne None si aucun signal clair.
        """
        q = query.lower()
        if any(k in q for k in ("discord", "salon", "channel", "serveur discord", "guild")):
            return "discord"
        if any(k in q for k in ("modifie", "edit", "corrige", "bug", "refactor", "implémente", "implement", "ajoute", "add", "crée", "create")):
            if any(k in q for k in ("fichier", "file", "code", "fonction", "class", "méthode", "method", "module")):
                return "code_edit"
        if any(k in q for k in ("recherche", "search", "trouve", "find", "google", "web")):
            return "web_search"
        if any(k in q for k in ("projet", "project", "app", "application", "crée un", "create a", "génère", "generate")):
            return "create_project"
        if any(k in q for k in ("lis", "read", "ouvre", "open", "affiche", "show", "liste")):
            return "file_ops"
        if q.endswith("?") or any(k in q for k in ("comment", "pourquoi", "qu'est", "what is", "how", "why", "explique", "explain")):
            return "question"
        return None

    def _feed_structured_clarification(self, question: str) -> None:
        """Ajoute une question en attente au structured_state."""
        ss = self._structured_state
        if ss is not None:
            ss.add_pending_question(question)

    def _reset_structured_pending(self) -> None:
        """Efface les questions en attente au début d'un nouveau run.

        Le nouveau message de l'utilisateur résout implicitement les clarifications
        précédemment émises — on repart d'un état propre.
        """
        ss = self._structured_state
        if ss is not None:
            ss.clear_pending_questions()

    def _feed_structured_facts_from_runtime(self) -> None:
        """Pose des established_facts fiables depuis le runtime_ctx."""
        ss = self._structured_state
        if ss is None:
            return
        rt = getattr(self, 'runtime_ctx', None)
        if rt is None:
            return
        # channel — attribut commun aux deux variantes de RuntimeContext
        channel = getattr(rt, 'channel', None) or getattr(rt, 'source_channel', None)
        if channel:
            ss.set_fact("channel", str(channel))
        # workspace — préférer resolved_workspace (plus fiable) à workspace_path
        workspace = getattr(rt, 'resolved_workspace', None) or getattr(rt, 'workspace_path', None)
        if workspace:
            ss.set_fact("workspace", str(workspace))
        # fichier actif dans l'IDE (signal stable, fourni par le plugin)
        active_file = getattr(rt, 'active_file_path', None)
        if active_file:
            ss.set_fact("active_file", str(active_file))
        # active_project_path — projet actif récent (sans keyword gate : c'est un fait structurel)
        # Uniquement si pas encore posé dans ce run et pas de workspace IDE explicite
        if not ss.established_facts.get("active_project_path"):
            _ide_ws = getattr(rt, 'resolved_workspace', None) or getattr(rt, 'workspace_path', None)
            if not _ide_ws:
                try:
                    _lum_sf = getattr(self, 'tools', None)
                    _lum_sf = getattr(_lum_sf, 'lumena', None) if _lum_sf else None
                    _id_svc_sf = getattr(_lum_sf, '_identity_svc', None) if _lum_sf else None
                    if _id_svc_sf is not None:
                        from ..core_services.identity_service import IdentityService as _IDS_SF
                        _ck_sf = _IDS_SF.resolve_channel_key(rt)
                        _rpc_sf = _id_svc_sf.get_recent_code_context(_ck_sf) if _ck_sf else None
                        if _rpc_sf:
                            _path_sf = _rpc_sf.get("workspace_path", "")
                            _slug_sf = _rpc_sf.get("project_slug", "")
                            if _path_sf:
                                ss.set_fact("active_project_path", _path_sf)
                                if _slug_sf:
                                    ss.set_fact("active_project_slug", _slug_sf)
                except Exception:
                    pass

    @staticmethod
    def _looks_like_local_code_fix(
        query: str,
        *,
        has_project_anchor: bool,
        inferred_intent: Optional[str] = None,
    ) -> bool:
        """Heuristique conservative pour les correctifs/code local bornés.

        But: desserrer les garde-fous de boucle sur les tâches de dev simples
        sans relâcher les cas ambigus ou de refonte large.
        """
        q = (query or "").lower()
        if not q:
            return False
        broad_scope_markers = (
            "refonte", "rewrite", "réécris", "reécris", "from scratch",
            "architecture", "restructure", "fusionne", "merge tout",
            "tout le projet", "whole project", "réorganise", "migre",
            "migration", "clean architecture", "full rewrite",
        )
        if any(k in q for k in broad_scope_markers):
            return False
        local_fix_markers = (
            "corrige", "correct", "fix", "bug", "erreur", "crash", "plante",
            "marche pas", "ne marche pas", "cassé", "casse", "bloque",
            "touche", "entrée", "enter", "bouton", "click", "clic",
            "fonctionne pas", "répare", "repare",
        )
        file_hint = bool(re.search(r"\b[\w.\-]+\.(?:py|js|ts|tsx|jsx|html|css|json|md|ya?ml|toml)\b", q))
        intent_hint = inferred_intent in {"code_edit", "file_ops"}
        local_signal = any(k in q for k in local_fix_markers) or file_hint or intent_hint
        return local_signal and (has_project_anchor or file_hint)

    def _is_direct_coding_request(self, query: str) -> bool:
        """Détecte les tâches de dev simples qui supportent mal les guards lourds."""
        has_anchor = False
        ide_ctx = getattr(self.tools, "ide_context", {}) or {}
        if ide_ctx.get("workspace_path") or ide_ctx.get("active_file_path"):
            has_anchor = True
        ss = self._structured_state
        inferred_intent = ss.last_intent if ss is not None else None
        if ss is not None:
            facts = getattr(ss, "established_facts", {}) or {}
            if facts.get("workspace") or facts.get("active_file"):
                has_anchor = True
        if not has_anchor:
            _lum = getattr(self.tools, "lumena", None)
            _id_svc = getattr(_lum, "_identity_svc", None) if _lum else None
            if _id_svc is not None and self.runtime_ctx is not None:
                try:
                    from ..core_services.identity_service import IdentityService as _IDS
                    _chan_key = _IDS.resolve_channel_key(self.runtime_ctx)
                    _recent_ctx = _id_svc.get_recent_code_context(_chan_key) if _chan_key else None
                    if _recent_ctx and _recent_ctx.get("workspace_path"):
                        has_anchor = True
                except Exception:
                    pass
        return self._looks_like_local_code_fix(
            query,
            has_project_anchor=has_anchor,
            inferred_intent=inferred_intent,
        )

    # ── THOUGHT leak auto-clean ────────────────────────────────────────────

    @staticmethod
    def _strip_thought_leak_prefix(text: str) -> Optional[str]:
        """Supprime les phrases de réflexion interne du début d'une réponse.

        Retourne le texte nettoyé si du contenu utile reste (≥ 50 chars),
        sinon None (la reformulation classique prendra le relais).
        """
        import re as _re

        # Patterns de phrases internes à retirer du début.
        # On retire phrase par phrase jusqu'à trouver du contenu utilisateur.
        _STRIP_PATTERNS = [
            # FR
            _re.compile(
                r"^(?:l['\u2018\u2019]utilisateur\s+(?:demande|veut|souhaite|a\s+demandé)[^.!?\n]{0,200}[.!?\n]\s*)",
                _re.IGNORECASE,
            ),
            _re.compile(
                r"^(?:je\s+(?:dois|vais|peux)\s+[^.!?\n]{0,200}[.!?\n]\s*)",
                _re.IGNORECASE,
            ),
            _re.compile(
                r"^(?:il\s+faut\s+que\s+je\s+[^.!?\n]{0,200}[.!?\n]\s*)",
                _re.IGNORECASE,
            ),
            _re.compile(
                r"^(?:(?:maintenant\s+que\s+j['\u2018\u2019]ai|après\s+avoir|sur\s+la\s+base\s+de|d['\u2018\u2019]après\s+les)\s+[^.!?\n]{0,200}[.!?\n]\s*)",
                _re.IGNORECASE,
            ),
            _re.compile(
                r"^(?:(?:j['\u2018\u2019]ai\s+(?:déjà|maintenant|exécuté|effectué|analysé))[^.!?\n]{0,200}[.!?\n]\s*)",
                _re.IGNORECASE,
            ),
            _re.compile(
                r"^(?:(?:rien\s+à\s+faire)[^.!?\n]{0,80}[.!?\n]\s*)",
                _re.IGNORECASE,
            ),
            # EN
            _re.compile(
                r"^(?:the\s+user\s+(?:is\s+asking|wants|asked|requested)\s+[^.!?\n]{0,200}[.!?\n]\s*)",
                _re.IGNORECASE,
            ),
            _re.compile(
                r"^(?:(?:i\s+(?:need\s+to|should|will)|i['\u2018\u2019](?:ll|ve)|let\s+me)\s+[^.!?\n]{0,200}[.!?\n]\s*)",
                _re.IGNORECASE,
            ),
            _re.compile(
                r"^(?:(?:based\s+on|now\s+that\s+i\s+have|i\s+have\s+(?:already|now)|having\s+gathered)\s+[^.!?\n]{0,200}[.!?\n]\s*)",
                _re.IGNORECASE,
            ),
        ]

        cleaned = text.strip()
        # On fait plusieurs passes (un prefix peut en cacher un autre)
        for _ in range(5):
            changed = False
            for pat in _STRIP_PATTERNS:
                m = pat.match(cleaned)
                if m:
                    cleaned = cleaned[m.end():].strip()
                    changed = True
                    break
            if not changed:
                break

        # Sécurité : si on a trop nettoyé, retourner None
        if not cleaned or len(cleaned) < 50:
            return None
        # Sécurité : si le résultat commence toujours par un prefix interne, abandonner
        _cl = cleaned.lower()
        _STILL_INTERNAL = (
            "l'utilisateur", "je dois ", "je vais ", "il faut que je",
            "the user ", "i need to", "i should ", "i will now",
        )
        if any(_cl.startswith(p) for p in _STILL_INTERNAL):
            return None
        return cleaned

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
                _max_mem = int(os.environ.get("LUMENA_MEMORY_MAX_INJECT", "20"))
                mem_ctx = memory.get_context_for_prompt(query, max_memories=_max_mem)
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
                "  2b. CLASSER → identifie la surface réelle : résultats, formulaire public, builder, login wall, anti-bot, iframe, erreur\n"
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

        # --- Peer Awareness (Lot A Phase 10) ---
        peer_awareness_section = ""
        try:
            from src.runtime.peer_awareness import build_peer_awareness_context
            _user_id = getattr(self.runtime_ctx, "user_id", None) if self.runtime_ctx else None
            peer_awareness_section = build_peer_awareness_context(user_id=_user_id)
        except Exception as _pa_exc:
            logger.debug(f"Peer awareness injection: {_pa_exc}")

        # --- Contexte IDE (source de verite pour workspace) ---
        ide_workspace = str((getattr(self.tools, "ide_context", {}) or {}).get("workspace_path") or "").strip()
        ide_active_file = str((getattr(self.tools, "ide_context", {}) or {}).get("active_file_path") or "").strip()
        ide_open_files = (getattr(self.tools, "ide_context", {}) or {}).get("open_files") or []
        ide_runtime_context = ""
        _rt_channel = ""
        if self.runtime_ctx is not None:
            _rt_channel = getattr(self.runtime_ctx, 'channel', '') or ''
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
        if _rt_channel == "ide":
            ide_runtime_context += """
## CANAL IDE — MODE DEVELOPPEMENT:
- Tu es connectee a l'IDE Lumena. L'utilisateur code activement.
- Concentre-toi UNIQUEMENT sur le developpement, le code, le debug, l'architecture.
- Reponds de maniere technique et directe. Pas de bavardage.
- Utilise les outils IDE en priorite: ide_open_file, ide_write_file, ide_terminal, ide_diff.
- Si un fichier est ouvert dans l'IDE (fichier actif/fichiers ouverts), travaille dessus directement.
- Pour les modifications de code, prefere edit_file/str_replace pour les petits changements, delegate_task pour les gros.
"""

        # --- Projet actif récent (continuité multi-tour) ---
        # Injecté uniquement si la requête ressemble à une continuation et qu'un
        # projet a été créé/modifié lors d'un tour précédent sur ce canal.
        recent_project_context = ""
        if not ide_workspace:  # Ne pas surcharger si l'IDE donne déjà le workspace
            _rpc_path = ""
            _rpc_slug = ""
            # 1.3: Lire established_facts en priorité (zéro lock, déjà posé par _feed_structured_facts)
            _ss_rpc = self._structured_state
            if _ss_rpc is not None:
                _rpc_path = _ss_rpc.established_facts.get("active_project_path", "")
                _rpc_slug = _ss_rpc.established_facts.get("active_project_slug", "")
            # Fallback: IdentityService si le fait n'est pas encore posé dans ce run
            if not _rpc_path:
                _lum_rpc = getattr(self.tools, "lumena", None)
                _id_svc = getattr(_lum_rpc, "_identity_svc", None) if _lum_rpc else None
                if _id_svc is not None and self.runtime_ctx is not None:
                    try:
                        from ..core_services.identity_service import IdentityService as _IDS
                        _chan_key = _IDS.resolve_channel_key(self.runtime_ctx)
                        _recent_ctx = _id_svc.get_recent_code_context(_chan_key) if _chan_key else None
                        if _recent_ctx:
                            _rpc_path = _recent_ctx.get("workspace_path", "")
                            _rpc_slug = _recent_ctx.get("project_slug", "")
                    except Exception as _rpc_exc:
                        logger.debug("[RecentProject] Échec récupération contexte: {}", _rpc_exc)
            if _rpc_path:
                # 2.3: Liste élargie pour couvrir le français familier
                _CONT_KW = (
                    "corrige", "correct", "fix", "fixe", "bug",
                    "continue", "suite", "fais la suite",
                    "améliore", "ameliore", "complète", "complete",
                    "marche pas", "ça bug", "ça crash", "ça plante",
                    "refais", "re-fais", "le jeu", "le projet",
                    "l'appli", "le site", "le code",
                    "toujours pas", "ça marche toujours pas", "le dernier truc",
                    "change-le", "relance-le", "encore une fois", "pas encore",
                    "retente", "le même", "le truc", "c'est encore",
                    "reessaie", "réessaie", "reprends", "retravaille",
                )
                _is_continuation = any(k in query_lower for k in _CONT_KW)
                if _is_continuation:
                    _label = _rpc_slug or _rpc_path.replace("\\", "/").rsplit("/", 1)[-1]
                    recent_project_context = (
                        f"\n## PROJET ACTIF RÉCENT (priorité continuité) :\n"
                        f"- Chemin : `{_rpc_path}`\n"
                        f"- Nom : {_label}\n"
                        f"- Ce projet a été créé/modifié lors d'un tour récent.\n"
                        f"- Réutilise ce chemin **en priorité** pour `delegate_task` "
                        f"ou toute opération sur le projet, sans relancer find_files.\n"
                    )

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
{peer_awareness_section}
{ide_runtime_context}
{recent_project_context}
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
2. Nouveau fichier SIMPLE (1 seul, non-code) -> `write_file`. Fichier existant -> `edit_file`/`apply_patch`.
3. Projet code (jeu, site, app, script >50 lignes, multi-fichiers) -> TOUJOURS `delegate_task(agent_type="code")`. JAMAIS write_file un par un pour du code.
4. PLAN = ENGAGEMENT : complete toutes les taches avant FINAL. Si impossible : explique-le dans THOUGHT et passe a la suivante.
5. Apres delegate_task ✅ → FINAL_ANSWER IMMEDIATEMENT. Ne relance JAMAIS delegate_task pour "verifier". Le CodeAgent a deja tout fait, son rapport est ta verification.
6. Tache de code (creation jeu/site/app/script, modification, debug) -> OBLIGATOIREMENT `delegate_task` ou `delegate_task_bg`. N'utilise JAMAIS write_file/create_project pour ecrire du code toi-meme. Le CodeAgent est specialise et produit un meilleur resultat.
7. OTP/CAPTCHA -> `telegram_send_message` ou `send_whatsapp_message`, puis `wait(seconds=30)`.
8. UNE seule ACTION par reponse. Attends l'OBSERVATION avant d'agir ensuite.
9. Serveur de preview/test (http.server, serve, vite, etc.) -> JAMAIS sur le port 8080 (reserve a Lumena). Utilise 8081 ou superieur (ex: `python -m http.server 8081`).

## Delegation CodeAgent — OBLIGATOIRE pour le code :
⚠️ REGLE ABSOLUE : Tu ne codes JAMAIS toi-meme. Tu DELEGUES au CodeAgent.
- "code moi un jeu" / "cree un site" / "fais un script" / "programme une app" → `delegate_task(agent_type="code", description="...", context="...")`
- Le CodeAgent ecrit le code, cree les fichiers, execute, teste, et corrige. Toi tu ne fais que deleguer.
- `delegate_task` : SYNCHRONE — attend le resultat, tu enchaines (deploy, mail, etc.).
- `delegate_task_bg` : ARRIERE-PLAN — retourne un task_id, la progression s'affiche automatiquement dans le chat.
- Exception micro-fix borné (typo, import manquant, 1-2 lignes cassées, petit fix CSS/HTML/JS/Python, max 30 lignes, 1 seul fichier) → `str_replace` ou `edit_by_lines` en priorité, `edit_file` si fichier court. Exclus : Dockerfile, package.json, pyproject.toml, requirements.txt, tout fichier de config/build. Incertitude ou chantier plus large → `delegate_task` obligatoire.
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
        _prev_inline = _ait_global[0]
        thought, action, halluc_flag, pending = _parse_response_fn(response)
        self._last_thought_was_hallucinated = halluc_flag
        self._pending_multi_actions = pending
        # P5 — action_inline_risk : tracker les inline détectés par le parser global
        if _ait_global[0] > _prev_inline:
            self._action_inline_count += _ait_global[0] - _prev_inline
        return thought, action

    def _parse_plan(self, raw_response: str) -> List[TaskItem]:
        return _parse_plan_fn(raw_response)


    def _update_plan_progress(self, tool_name: str, tool_args: Dict[str, Any],
                               observation_content: str, iteration: int) -> None:
        """Met a jour le plan en cochant les taches completees par l'outil execute."""
        if not self._task_plan:
            return

        # Signaux d'échec : si l'observation contient un marqueur d'erreur, ne rien cocher
        obs_lower = (observation_content or "").lower()
        _fail, _overridden = classify_observation(observation_content)
        # ❌ seul n'est PAS un marqueur d'échec — voir plan_evidence._FAIL_MARKERS.
        observation_has_failure = _fail and not _overridden
        observation_has_failure = observation_has_failure or _browser_observation_has_failure(
            tool_name,
            observation_content,
        )

        hints = _TOOL_COMPLETION_HINTS.get(tool_name, [])
        tool_lower = tool_name.lower()
        tool_module_category = ""
        tool_semantic_category = ""
        try:
            tool_module_category = self.tools.get_tool_module_category(tool_name)
            tool_semantic_category = self.tools.get_tool_semantic_category(tool_name)
        except Exception:
            pass
        # Guard 5 pré-calculé : si l'outil est exploratoire, aucune tâche métier
        # ne peut être marquée par aucune voie (sem, seq, auto).
        _is_exploration_for_guard5 = tool_name in _EXPLORATION_TOOLS_STRICT

        _any_matched = False
        _has_specific_match = False  # True si au moins un arg/tool/obs match (pas juste hint)
        _completed_this_call = 0  # Limite le nombre de complétion par appel
        _MAX_COMPLETIONS_PER_CALL = 2  # garde-fou: un outil complète au max 2 tâches
        _SUBMIT_VERBS = ("soumett", "soumettre", "submit", "envoyer le formulaire",
                         "envoyer le form", "valider le formulaire", "valider le form",
                         "cliquer sur soumettre", "cliquer sur envoyer")
        _FINAL_ONLY_STARTS = (
            "confirmer à", "confirmer que", "confirmer le", "confirmer les",
            "rapporter", "informer l'", "informer le", "signaler le", "signaler les",
            "résumer les résultats", "résumer le résultat",
            "afficher le résultat", "afficher les résultats",
            "répondre à l'utilisateur",
        )
        _RESULT_CAPTURE_MARKERS = (
            "screenshot du résultat", "screenshot du resultat",
            "capture du résultat", "capture du resultat",
            "screenshot final", "capture finale",
            "screenshot du résultat final", "capture du résultat final",
        )
        _STRICT_SUBMIT_SUCCESS_MARKERS = (
            "soumis", "soumise", "soumission", "submitted",
            "envoyé", "envoyee", "envoyée", "envoye",
            "confirmation", "confirmé", "confirmee", "confirmée", "confirmed",
            "httpbin.org/post", "merci", "success", "réussi", "reussi",
            "formulaire envoy", "form submitted", "inscription réussie",
            "compte créé", "account created",
        )
        _STRICT_CAPTURE_SUCCESS_MARKERS = (
            "screenshot", "capture", "📸", ".png", ".jpg", ".jpeg", ".webp",
        )
        _CHAT_INTERACTION_MARKERS = (
            "interagir avec l'ia", "interagir avec une ia",
            "échanger avec l'ia", "echanger avec l'ia",
            "échanger avec une ia", "echanger avec une ia",
            "parler avec l'ia", "parler avec une ia",
            "discuter avec l'ia", "discuter avec une ia",
            "envoyer un message", "obtenir une réponse", "obtenir une reponse",
        )
        _CHAT_CONFIRM_MARKERS = (
            "confirmer l'échange", "confirmer l'echange",
            "échange réussi", "echange réussi", "echange reussi",
            "échange avec l'ia réussi", "echange avec l'ia reussi",
            "confirmer la réponse", "confirmer la reponse",
        )
        _STRICT_CHAT_SUCCESS_MARKERS = (
            "réponse", "reponse", "assistant", "a répondu", "a repondu",
            "message envoyé", "message envoye", "envoyé", "envoye",
            "reply", "responded", "conversation", "new message",
        )

        def _strip_plan_prefix(_desc_lower: str) -> str:
            return re.sub(
                r"^\s*(?:étape|etape|step)\s*\d+\s*[:\-]\s*",
                "",
                (_desc_lower or "").strip(),
                flags=re.IGNORECASE,
            ).strip()

        def _is_chat_interaction_task(_desc_lower: str) -> bool:
            _desc_guard = _strip_plan_prefix(_desc_lower)
            return any(marker in _desc_guard for marker in _CHAT_INTERACTION_MARKERS)

        def _is_final_only_task(_desc_lower: str) -> bool:
            _desc_guard = _strip_plan_prefix(_desc_lower)
            if any(_desc_guard.startswith(fos) for fos in _FINAL_ONLY_STARTS):
                return True
            return any(marker in _desc_guard for marker in _CHAT_CONFIRM_MARKERS)

        def _requires_strict_proof(_desc_lower: str) -> bool:
            _desc_guard = _strip_plan_prefix(_desc_lower)
            if any(sv in _desc_guard for sv in _SUBMIT_VERBS):
                return True
            if _is_final_only_task(_desc_guard):
                return True
            if any(marker in _desc_guard for marker in _RESULT_CAPTURE_MARKERS):
                return True
            return _is_chat_interaction_task(_desc_guard)

        def _has_strict_plan_proof(_desc_lower: str, _obs_lower: str) -> bool:
            _desc_guard = _strip_plan_prefix(_desc_lower)
            if _browser_observation_is_auxiliary_action(tool_name, observation_content or ""):
                return False
            if _is_final_only_task(_desc_guard):
                return False
            if any(sv in _desc_guard for sv in _SUBMIT_VERBS):
                return any(marker in _obs_lower for marker in _STRICT_SUBMIT_SUCCESS_MARKERS)
            if any(marker in _desc_guard for marker in _RESULT_CAPTURE_MARKERS):
                return any(marker in _obs_lower for marker in _STRICT_CAPTURE_SUCCESS_MARKERS)
            if _is_chat_interaction_task(_desc_guard):
                if any(marker in _obs_lower for marker in _STRICT_CHAT_SUCCESS_MARKERS):
                    return True
                return _looks_like_chat_transcript(observation_content or "")
            return True

        for task in self._task_plan:
            if task.completed:
                continue
            desc_lower = task.description.lower()
            desc_guard = _strip_plan_prefix(desc_lower)

            # Guard 5 : outil exploratoire ne peut jamais cocher une tâche métier
            if _is_exploration_for_guard5:
                _task_desc_norm = _normalize_guard_token(desc_lower)
                if any(
                    _task_desc_norm == starter or _task_desc_norm.startswith(starter + " ")
                    for starter in _BUSINESS_ACTION_STARTERS_NORMALIZED
                ):
                    logger.debug(
                        "[PLAN] Guard 5 (sémantique): tâche métier '{}' non marquable par {} (iter {})",
                        task.description, tool_name, iteration,
                    )
                    continue  # ignore ce task, passe au suivant

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

            # Guard SUBMIT-ONLY : les tâches de soumission ne peuvent être marquées
            # que par un clic (browser_click_index), pas par une saisie (browser_type_index).
            # Cause réelle : "formulaire" dans les hints de browser_type_index faisait matcher
            # "Soumettre le formulaire" alors qu'on était encore en train de remplir des champs.
            if tool_name == "browser_type_index" and any(sv in desc_guard for sv in _SUBMIT_VERBS):
                logger.debug(
                    "[PLAN] Guard SUBMIT-ONLY: '{}' non marquable par browser_type_index (iter {})",
                    task.description, iteration,
                )
                continue

            # Guard FINAL-ONLY : les tâches de rapport/confirmation ne doivent être
            # marquées qu'au moment du FINAL, pas par un outil browser.
            # Elles commencent par confirmer/rapporter/informer/signaler.
            if tool_name.startswith("browser_") and _is_final_only_task(desc_guard):
                logger.debug(
                    "[PLAN] Guard FINAL-ONLY: '{}' non marquable par {} — réservé à FINAL (iter {})",
                    task.description, tool_name, iteration,
                )
                continue

            # Fallback: observation de succes + mot du nom d'outil dans la description
            obs_match = False
            if not (hint_match or tool_match or arg_match) and observation_content:
                if "\u2705" in observation_content or "succes" in obs_lower or "créé" in obs_lower or "envoyé" in obs_lower:
                    tool_words = tool_lower.replace("_", " ").split()
                    if any(tw in desc_lower for tw in tool_words if len(tw) > 2):
                        obs_match = True

            is_specific = arg_match or tool_match or obs_match
            _proof_for_task = has_sufficient_proof(
                tool_name,
                observation_content,
                task.description,
                tool_module_category,
                tool_semantic_category,
            )
            if hint_match and not is_specific and tool_name in _HINT_ONLY_PROOF_REQUIRED_TOOLS and not _proof_for_task:
                logger.debug(
                    "[PLAN] Hint-only bloqué: '{}' non marquable par {} sans preuve spécifique (iter {})",
                    task.description, tool_name, iteration,
                )
                continue
            if hint_match or is_specific:
                if (
                    tool_name in _READ_ONLY_DISCOVERY_PLAN_TOOLS
                    and not _read_only_discovery_tool_can_complete_task(tool_name, task.description)
                ):
                    logger.debug(
                        "[PLAN] Outil découverte hors périmètre: '{}' non marquable par {} (iter {})",
                        task.description, tool_name, iteration,
                    )
                    continue
                if (
                    tool_name in _BROWSER_PLAN_PASSIVE_TOOLS
                    and not _browser_passive_tool_can_complete_task(tool_name, task.description)
                ):
                    logger.debug(
                        "[PLAN] Browser passif hors périmètre: '{}' non marquable par {} (iter {})",
                        task.description, tool_name, iteration,
                    )
                    continue
                if (
                    tool_name in _BROWSER_PLAN_PASSIVE_TOOLS
                    and not _proof_for_task
                ):
                    logger.debug(
                        "[PLAN] Browser passif sans preuve: '{}' non marquable par {} (iter {})",
                        task.description, tool_name, iteration,
                    )
                    continue
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
                # Verify-gate : une tâche de vérification exige une preuve réelle.
                # Présence de fichiers, hint de tool, ou ✅ générique ne suffisent pas.
                if is_verify_task(desc_lower) and not _proof_for_task:
                    logger.debug(
                        "[PLAN] Verify-gate (sem): '{}' non marquable par {} — preuve insuffisante (iter {})",
                        task.description, tool_name, iteration,
                    )
                    continue
                if _requires_strict_proof(desc_lower) and not _proof_for_task:
                    logger.debug(
                        "[PLAN] Strict-proof (sem): '{}' non marquable par {} — preuve insuffisante (iter {})",
                        task.description, tool_name, iteration,
                    )
                    continue
                if _requires_strict_proof(desc_lower) and not _has_strict_plan_proof(desc_lower, obs_lower):
                    logger.debug(
                        "[PLAN] Strict-proof content (sem): '{}' non marquable par {} — observation insuffisante (iter {})",
                        task.description, tool_name, iteration,
                    )
                    continue
                _proof = evaluate_task_proof(task.description, tool_name, observation_content)
                task.completed = True
                task.completed_at_iteration = iteration
                task.completed_by_tool = tool_name
                task.completion_status = task_completion_status(
                    tool_name, desc_lower, tool_semantic_category, tool_module_category,
                )
                task.completion_evidence = _proof.evidence_summary
                task.completion_confidence = _proof.confidence
                logger.info("[PROOF] '{}' — {} ({})", task.description[:50], _proof.evidence_kind, _proof.confidence)
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
        # Les outils purement info/inspection ne peuvent pas cocher une tâche métier
        # via le fallback séquentiel : "config" dans get_lumena_config matcherait
        # faussement "Configurer les rôles" sans que rien n'ait été fait.
        _seq_fallback_blocked = tool_name in _SEQ_FALLBACK_BLOCKLIST
        if (
            not _any_matched
            and not observation_has_failure
            and not _seq_fallback_blocked
            and not _browser_observation_is_auxiliary_action(tool_name, observation_content or "")
        ):
            tool_words = {w for w in tool_lower.replace("_", " ").split() if len(w) > 2}
            for task in self._task_plan:
                if not task.completed:
                    desc_lower = task.description.lower()
                    if tool_words and any(tw in desc_lower for tw in tool_words):
                        if (
                            tool_name in _READ_ONLY_DISCOVERY_PLAN_TOOLS
                            and not _read_only_discovery_tool_can_complete_task(tool_name, task.description)
                        ):
                            logger.debug(
                                "[PLAN] Outil découverte seq hors périmètre: '{}' non marquable par {} (iter {})",
                                task.description, tool_name, iteration,
                            )
                            break
                        if tool_name.startswith("browser_") and _is_final_only_task(desc_lower):
                            logger.debug(
                                "[PLAN] Browser seq FINAL-ONLY: '{}' non marquable par {} (iter {})",
                                task.description, tool_name, iteration,
                            )
                            break
                        if (
                            tool_name in _BROWSER_PLAN_PASSIVE_TOOLS
                            and not _browser_passive_tool_can_complete_task(tool_name, task.description)
                        ):
                            logger.debug(
                                "[PLAN] Browser seq hors périmètre: '{}' non marquable par {} (iter {})",
                                task.description, tool_name, iteration,
                            )
                            break
                        if tool_name.startswith("browser_") and not has_sufficient_proof(
                            tool_name,
                            observation_content,
                            task.description,
                            tool_module_category,
                            tool_semantic_category,
                        ):
                            logger.debug(
                                "[PLAN] Browser seq sans preuve: '{}' non marquable par {} (iter {})",
                                task.description, tool_name, iteration,
                            )
                            break
                        # Verify-gate : le fallback séquentiel n'est pas une preuve réelle.
                        if is_verify_task(desc_lower) and not has_sufficient_proof(
                            tool_name,
                            observation_content,
                            task.description,
                            tool_module_category,
                            tool_semantic_category,
                        ):
                            logger.debug(
                                "[PLAN] Verify-gate (seq): '{}' non marquable par {} — preuve insuffisante (iter {})",
                                task.description, tool_name, iteration,
                            )
                            break
                        if _requires_strict_proof(desc_lower) and not has_sufficient_proof(
                            tool_name,
                            observation_content,
                            task.description,
                            tool_module_category,
                            tool_semantic_category,
                        ):
                            logger.debug(
                                "[PLAN] Strict-proof (seq): '{}' non marquable par {} — preuve insuffisante (iter {})",
                                task.description, tool_name, iteration,
                            )
                            break
                        if _requires_strict_proof(desc_lower) and not _has_strict_plan_proof(desc_lower, obs_lower):
                            logger.debug(
                                "[PLAN] Strict-proof content (seq): '{}' non marquable par {} — observation insuffisante (iter {})",
                                task.description, tool_name, iteration,
                            )
                            break
                        _proof = evaluate_task_proof(task.description, tool_name, observation_content)
                        task.completed = True
                        task.completed_at_iteration = iteration
                        task.completed_by_tool = f"{tool_name}:seq"
                        task.completion_status = task_completion_status(
                            tool_name, desc_lower, tool_semantic_category, tool_module_category,
                        )
                        task.completion_evidence = _proof.evidence_summary
                        task.completion_confidence = _proof.confidence
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
            # Lecture / navigation fichiers
            "wait", "memory_add", "read_file", "list_files", "list_dir",
            "search_files", "search_code", "list_directory", "find_files",
            "grep_search", "search_in_code", "view_file_outline",
            # Mail info-only
            "mail_list_accounts", "mail_inbox", "mail_check", "memory_search",
            "mail_account_upsert",
            # Config / inspection système — ne représentent aucune action métier
            "get_lumena_config", "get_system_info", "health_check",
            "get_weather", "get_time", "provider_info",
            # Listing modèles / ressources
            "list_image_models", "ionos_list_sites", "ionos_list_files",
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

        if (
            not _any_matched
            and not _seq_matched
            and not observation_has_failure
            and not _is_read_only_mode
            and not _browser_observation_is_auxiliary_action(tool_name, observation_content or "")
        ):
            # Garde: max 1 auto-avancement par itération (parallel_tools peut appeler
            # _update_plan_progress N fois dans la même itération → sans garde, N tâches
            # sont marquées completed d'un coup sans rapport avec le contenu réel)
            if self._last_auto_advance_iter == iteration:
                pass  # déjà auto-avancé cette itération
            # Garde 1b: parallel_tools agrège plusieurs sous-outils et ne doit jamais
            # auto-avancer à lui seul une tâche métier via ce fallback générique.
            elif tool_name == "parallel_tools":
                pass
            # Garde 2: pas d'auto-avancement trop tôt (itération 0) sauf si
            # l'observation contient un marqueur de succès explicite (✅)
            elif iteration < 1 and "\u2705" not in (observation_content or ""):
                pass
            # Garde 3: l'observation doit être substantielle (pas juste "OK" ou vide)
            elif (
                observation_content
                and len(observation_content.strip()) >= 10
                and (
                    tool_name not in _TRIVIAL_TOOLS
                    or _trivial_tool_matches_next_task()
                    # Un outil de vérification (health_check, run_command…) avec preuve
                    # réelle n'est pas trivial même s'il figure dans _TRIVIAL_TOOLS.
                    or has_sufficient_proof(
                        tool_name,
                        observation_content,
                        "",
                        tool_module_category,
                        tool_semantic_category,
                    )
                )
            ):
                # Garde 4: si la tâche mentionne explicitement un nom d'outil différent
                # du tool actuel, ne PAS auto-avancer (ex: tâche dit "check_web_project"
                # mais le tool est "run_command" → pas de lien causal)
                import re as _re_plan
                for task in self._task_plan:
                    if not task.completed:
                        desc_lower = task.description.lower()
                        if (
                            tool_name in _READ_ONLY_DISCOVERY_PLAN_TOOLS
                            and not _read_only_discovery_tool_can_complete_task(tool_name, task.description)
                        ):
                            logger.debug(
                                "[PLAN] Outil découverte auto hors périmètre: '{}' non marquable par {} (iter {})",
                                task.description, tool_name, iteration,
                            )
                            break
                        if (
                            tool_name in _BROWSER_PLAN_PASSIVE_TOOLS
                            and not _browser_passive_tool_can_complete_task(tool_name, task.description)
                        ):
                            logger.debug(
                                "[PLAN] Browser auto hors périmètre: '{}' non marquable par {} (iter {})",
                                task.description, tool_name, iteration,
                            )
                            break
                        if tool_name.startswith("browser_") and not has_sufficient_proof(
                            tool_name,
                            observation_content,
                            task.description,
                            tool_module_category,
                            tool_semantic_category,
                        ):
                            logger.debug(
                                "[PLAN] Browser auto sans preuve: '{}' non marquable par {} (iter {})",
                                task.description, tool_name, iteration,
                            )
                            break

                        # Guard 5 : un outil d'exploration ne peut pas auto-avancer
                        # une tâche métier (premier mot = verbe d'action).
                        # Ex : run_command("cd") ne peut pas cocher "Déléguer …".
                        if (
                            tool_name in _EXPLORATION_TOOLS_STRICT
                            and any(
                                _normalize_guard_token(desc_lower) == starter
                                or _normalize_guard_token(desc_lower).startswith(starter + " ")
                                for starter in _BUSINESS_ACTION_STARTERS_NORMALIZED
                            )
                        ):
                            logger.debug(
                                "[PLAN] Guard 5: tâche métier '{}' non marquable par outil exploratoire {} (iter {})",
                                task.description, tool_name, iteration,
                            )
                            break

                        # Guard 4 : la description référence un outil spécifique qui
                        # n'est pas le tool courant → l'auto-avancement est illégitime
                        _tool_refs = _re_plan.findall(r'\b([a-z][a-z0-9]*(?:_[a-z0-9]+)+)\b', desc_lower)
                        if _tool_refs and tool_name.lower() not in _tool_refs:
                            logger.debug(
                                "[PLAN] Auto-avancement bloqué: '{}' référence {} mais tool={} (iter {})",
                                task.description, _tool_refs, tool_name, iteration,
                            )
                            break
                        # Verify-gate : l'auto-avancement générique ne constitue pas
                        # une preuve pour les étapes de vérification fonctionnelle.
                        if is_verify_task(desc_lower) and not has_sufficient_proof(
                            tool_name,
                            observation_content,
                            task.description,
                            tool_module_category,
                            tool_semantic_category,
                        ):
                            logger.debug(
                                "[PLAN] Verify-gate (auto): '{}' non marquable par {} — preuve insuffisante (iter {})",
                                task.description, tool_name, iteration,
                            )
                            break
                        if _requires_strict_proof(desc_lower) and not has_sufficient_proof(
                            tool_name,
                            observation_content,
                            task.description,
                            tool_module_category,
                            tool_semantic_category,
                        ):
                            logger.debug(
                                "[PLAN] Strict-proof (auto): '{}' non marquable par {} — preuve insuffisante (iter {})",
                                task.description, tool_name, iteration,
                            )
                            break
                        if _requires_strict_proof(desc_lower) and not _has_strict_plan_proof(desc_lower, obs_lower):
                            logger.debug(
                                "[PLAN] Strict-proof content (auto): '{}' non marquable par {} — observation insuffisante (iter {})",
                                task.description, tool_name, iteration,
                            )
                            break
                        # Guard SUBMIT-ONLY (fallback) : même restriction que le
                        # chemin principal — browser_type_index ne peut jamais marquer
                        # une tâche de soumission.
                        if tool_name == "browser_type_index" and any(
                            sv in desc_lower for sv in _SUBMIT_VERBS
                        ):
                            logger.debug(
                                "[PLAN] Guard SUBMIT-ONLY (auto): '{}' non marquable par browser_type_index (iter {})",
                                task.description, iteration,
                            )
                            break
                        # Guard FINAL-ONLY (fallback) : les tâches de rapport/confirmation
                        # ne sont pas marquables par des outils browser.
                        if tool_name.startswith("browser_") and _is_final_only_task(desc_lower):
                            logger.debug(
                                "[PLAN] Guard FINAL-ONLY (auto): '{}' non marquable par {} — réservé à FINAL (iter {})",
                                task.description, tool_name, iteration,
                            )
                            break
                        _proof = evaluate_task_proof(task.description, tool_name, observation_content)
                        task.completed = True
                        task.completed_at_iteration = iteration
                        task.completed_by_tool = f"{tool_name}:auto"
                        task.completion_status = task_completion_status(
                            tool_name, desc_lower, tool_semantic_category, tool_module_category,
                        )
                        task.completion_evidence = _proof.evidence_summary
                        task.completion_confidence = _proof.confidence
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

    def _reconcile_plan_from_delegate_success(self, obs_text: str, iteration: int) -> int:
        """Réconcilie le plan après un succès delegate_task.

        Délègue la logique de décision à reconcile_delegate_report() (plan_evidence.py)
        et gère l'émission de l'état du plan côté React loop.
        """
        marked = reconcile_delegate_report(self._task_plan, obs_text, iteration)
        if marked:
            self._emit_plan_state(context_tool="delegate_task")
        return marked

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
                # ── StructuredState V1 : alimenter last_intent (classifier) ──
                self._feed_structured_intent(_intent_for_filter)
                # Fallback léger si le classifier n'a rien donné
                if _intent_for_filter is None:
                    self._feed_structured_intent(self._infer_intent_from_query(query))
        else:
            # Classifier non invoqué (_caller_set_allowed=True) : fallback keyword
            self._feed_structured_intent(self._infer_intent_from_query(query))

        single_file_creation_intent = self._is_single_file_creation_request(original_query)
        # ── Reset état structuré pour ce run ──
        self.exec_state.reset()
        self.execution_ledger.clear()
        # ── StructuredState V1 : nouveau run = questions en attente résolues ──
        self._reset_structured_pending()
        # Effacer le projet actif pour le re-évaluer depuis get_recent_code_context.
        _ss_reset = self._structured_state
        if _ss_reset is not None:
            _ss_reset.remove_fact("active_project_path")
            _ss_reset.remove_fact("active_project_slug")
        # ── StructuredState V1 : faits fiables depuis runtime_ctx ──
        self._feed_structured_facts_from_runtime()
        _direct_coding_mode = self._is_direct_coding_request(query)
        if _direct_coding_mode:
            self._run_meta["agent_output_warning"] = "direct_coding_mode"
        # Alias locaux vers guards (les locals existants pointent dans exec_state)
        _g = self.exec_state.guards
        last_read_signature = _g.last_read_signature
        repeated_read_count = _g.repeated_read_count
        _listed_dirs = _g.listed_dirs
        browser_fail_streak = _g.browser_fail_streak
        web_fetch_fail_streak = _g.web_fetch_fail_streak
        _read_file_path_counter = _g.read_file_path_counter
        _read_file_ranges_seen = _g.read_file_ranges_seen
        _read_file_reread_counter = _g.read_file_reread_counter
        _previous_thoughts = _g.previous_thoughts
        _stagnation_streak = _g.stagnation_streak
        _exploratory_since_productive = _g.exploratory_since_productive
        _write_tools = frozenset({"write_file", "edit_file", "apply_patch", "create_directory",
                                   "run_command", "check_web_project"})
        _read_only_tools = frozenset({"read_file", "list_directory", "find_files",
                                       "grep_search", "search_in_code", "view_file_outline"})
        _post_edit_read_streak = _g.post_edit_read_streak
        _redundant_read_streak = _g.redundant_read_streak
        _last_read_sig = _g.last_read_sig
        _has_done_edits = _g.has_done_edits
        _web_writes_count = _g.web_writes_count
        _pre_edit_redundant_streak = _g.pre_edit_redundant_streak
        _pre_edit_last_sig = _g.pre_edit_last_sig


        # ── Pipeline Direct : workflows connus exécutés sans boucle ReAct ──
        _pipeline_result = await self._try_direct_pipeline(query)
        if _pipeline_result is not None:
            return _pipeline_result

        # v2: Auto-route supprimé — le LLM utilise delegate_task / delegate_task_bg via le prompt

        for i in range(self.max_iterations):
            self._current_iteration = i  # Exposé pour réduction mémoire dynamique
            logger.debug(f"Iteration {i+1}")
            # ── Cancel user check ENTRE itérations ──────────────────────────────
            _rl_tid = threading.current_thread().ident
            if _rl_tid:
                _ce = _REACT_CANCEL_EVENTS.get(_rl_tid)
                if _ce is not None and _ce.is_set():
                    raise SystemExit("user_cancelled_react")
            # ── Cancel via TaskOrchestrator (stream parent annulé) ──────────────
            if self._orchestrator_enabled():
                try:
                    if self.task_orchestrator.is_cancel_requested(self.task_id):
                        logger.info("[ReAct] cancel_requested task={}", self.task_id)
                        raise SystemExit("task_orchestrator_cancel")
                except SystemExit:
                    raise
                except Exception:
                    pass
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
            # Si le prompt dépasse 75% de la fenêtre de contexte du modèle,
            # compacter l'historique pour éviter troncature silencieuse.
            _ctx_max = 0
            if self.runtime_ctx is not None:
                _ctx_max = getattr(self.runtime_ctx, "max_context_window", 0) or 0
            # Fallback modèle-agnostic si runtime_ctx absent ou context_window non configuré
            if _ctx_max == 0:
                _CTX_FALLBACKS = {
                    "deepseek-chat": 32_000, "deepseek-reasoner": 64_000,
                    "deepseek-r1": 64_000, "deepseek-v3": 64_000,
                    "gpt-4o": 128_000, "gpt-4": 64_000, "gpt-3.5": 16_000,
                    "gemini-2": 200_000, "gemini-1.5": 128_000, "gemini-1.0": 32_000,
                    "claude-3": 200_000, "claude-sonnet": 200_000, "claude-haiku": 200_000,
                    "kimi": 128_000, "llama-3": 128_000, "llama-2": 32_000,
                    "mistral": 32_000, "mixtral": 32_000, "qwen": 32_000,
                    "gemma": 8_000, "phi": 8_000,
                }
                _meta_now = self._get_llm_meta()
                _guard_model = (
                    _meta_now.get("model_used") or _meta_now.get("model_name")
                    or self._last_llm_meta.get("model_used") or ""
                ).lower()
                for _key, _limit in _CTX_FALLBACKS.items():
                    if _key in _guard_model:
                        _ctx_max = _limit
                        break
                if _ctx_max == 0:
                    _ctx_max = 32_000  # seuil conservateur universel
                if _guard_model:
                    logger.debug(f"🔍 Context guard fallback: modèle='{_guard_model}' → ctx_max={_ctx_max}")
            if _ctx_max > 0:
                from ..tools.compaction import estimate_tokens
                _prompt_tokens = estimate_tokens(prompt)
                # P5 — seuil de compaction adapté au profil du modèle (défaut 0.75)
                _compact_threshold = getattr(self._model_profile, "compact_ctx_threshold", 0.75) if self._model_profile else 0.75
                _threshold = int(_ctx_max * _compact_threshold)
                if _prompt_tokens > _threshold:
                    _overflow = _prompt_tokens - _threshold
                    logger.warning(
                        f"⚠️ Context overflow guard: {_prompt_tokens} tokens > {_compact_threshold:.0%} de {_ctx_max} "
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
            # P5 — profil comportemental : charge une fois par modèle actif
            if _active_model and _active_model != self._model_profile_applied_for:
                try:
                    from ..llm.model_profile import get_model_profile, describe_profile
                    self._model_profile = get_model_profile(_active_model)
                    self._model_profile_applied_for = _active_model
                    logger.debug("[P5] profil chargé pour '{}': {}", _active_model, describe_profile(self._model_profile))
                    # parser_severity="forgiving" → augmenter le budget de repair FINAL
                    # (les modèles forgiving tronquent souvent leurs réponses)
                    if self._model_profile.parser_severity == "forgiving" and self.max_final_repair_attempts < 2:
                        self.max_final_repair_attempts = 2
                        logger.debug("[P5] parser_severity=forgiving → max_final_repair_attempts élevé à 2")
                except Exception:
                    pass
            _timeout_mult = getattr(self._model_profile, "timeout_multiplier", 1.0) if self._model_profile else 1.0
            _base_timeout = int(240 * _timeout_mult)
            _llm_call_timeout = (_base_timeout + 60) if i >= 9 else ((_base_timeout + 30) if i >= 5 else _base_timeout)
            # P5 — signaux comportementaux dérivés du profil (calculés une fois par itération)
            _parser_sev = getattr(self._model_profile, "parser_severity", "lenient") if self._model_profile else "lenient"
            _loop_risk = getattr(self._model_profile, "loop_risk", "low") if self._model_profile else "low"
            # P5 — react_stability : seuil de stagnation adapté (unstable déclenche plus tôt)
            _stagnation_limit = (
                2 if getattr(self._model_profile, "react_stability", "stable") == "unstable" else 3
            ) if self._model_profile else 3
            if _direct_coding_mode:
                _stagnation_limit = max(_stagnation_limit, 4)
            # P5 — action_inline_risk : nb inline avant injection rappel format
            _inline_risk = getattr(self._model_profile, "action_inline_risk", "low") if self._model_profile else "low"
            _inline_reminder_thresh = 1 if _inline_risk == "high" else (2 if _inline_risk == "medium" else 0)
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
            
            # FIX: Gérer les réponses vides - comportement adapté au profil (P5)
            if not response or len(response.strip()) == 0:
                _empty_risk = getattr(self._model_profile, "empty_response_risk", "rare") if self._model_profile else "rare"
                _retry_on_empty = getattr(self._model_profile, "retry_on_empty", True) if self._model_profile else True
                if _empty_risk == "frequent":
                    logger.debug("⚠️ Réponse LLM vide (attendu pour ce modèle) — retry format")
                else:
                    logger.warning("⚠️ Réponse LLM vide détectée - retry avec rappel de format")
                if _retry_on_empty:
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
                # Streak ≥ 2 : compaction d'urgence — le contexte accumulé est probablement
                # la cause principale des hallucinations. Garder seulement les 3 dernières étapes.
                if _halluc_streak >= 2 and len(self.history) > 3:
                    _kept = self.history[-3:]
                    _dropped = len(self.history) - 3
                    self.history = _kept
                    logger.warning(
                        "🚨 Hallucination streak {} — compaction d'urgence: {} étapes supprimées, "
                        "historique réduit à 3 pour nettoyer le contexte.",
                        _halluc_streak, _dropped,
                    )
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
                        # P5 — modèles à loop_risk élevé : seuil abaissé pour détection plus tôt
                        _q_words = len(original_query.split())
                        _base_thresh = 0.65 if _q_words <= 5 else 0.80
                        _thresh = (
                            _base_thresh - 0.10 if _loop_risk == "high" else
                            _base_thresh - 0.05 if _loop_risk == "medium" else
                            _base_thresh
                        )
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
                    # Après N stagnations consécutives : forcer la complétion du plan
                    # (N=2 pour react_stability=unstable, N=3 sinon)
                    if _stagnation_streak >= _stagnation_limit and self._task_plan:
                        logger.warning("⚠️ Stagnation critique ({}) — bypass PLAN GUARD pour débloquer FINAL", _stagnation_streak)
                        # NE PAS mentir sur l'état des tâches — juste bypasser le guard
                        self._plan_guard_retries = 3  # Empêche PLAN GUARD de bloquer
                    # P3 HARD: Après N stagnations consécutives ET actions identiques → FORCER FINAL synthétique
                    # Une progression légitime (lectures séquentielles avec args différents) est tolérée.
                    _actions_are_redundant = False
                    if _stagnation_streak >= _stagnation_limit and len(self.history) >= 3:
                        _recent_actions = self.history[-3:]
                        _sig = (action.tool_name, str(action.tool_args))
                        _recent_sigs = [(h.action.tool_name, str(h.action.tool_args)) for h in _recent_actions]
                        # Si les 3 dernières actions + l'actuelle sont toutes identiques → vrai blocage
                        _actions_are_redundant = all(s == _sig for s in _recent_sigs)
                    if _stagnation_streak >= _stagnation_limit and _actions_are_redundant:
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
                    # path vide = appel sans argument → échoue au registry, on ne tracke pas
                    if target_path:
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
                            # 4.1: Priorité 0 — established_facts (zéro lock, déjà résolu)
                            try:
                                _ss_esc = self._structured_state
                                if _ss_esc is not None:
                                    _ef_esc_path = _ss_esc.established_facts.get("active_project_path", "")
                                    if _ef_esc_path and os.path.isdir(_ef_esc_path):
                                        _esc_project_path = _ef_esc_path
                                        logger.info("[ReAct] Escalade: project_path depuis established_facts: {}", _ef_esc_path[:80])
                            except Exception:
                                pass
                            # Priorité 1 — IdentityService si le fait n'est pas posé
                            if not _esc_project_path:
                                try:
                                    _id_svc_esc = getattr(_lum, "_identity_svc", None)
                                    if _id_svc_esc is not None and self.runtime_ctx is not None:
                                        from ..core_services.identity_service import IdentityService as _IDS_E
                                        _ck_esc = _IDS_E.resolve_channel_key(self.runtime_ctx)
                                        _rpc_esc = _id_svc_esc.get_recent_code_context(_ck_esc) if _ck_esc else None
                                        if _rpc_esc:
                                            _rpc_path_esc = _rpc_esc.get("workspace_path", "")
                                            if _rpc_path_esc and os.path.isdir(_rpc_path_esc):
                                                _esc_project_path = _rpc_path_esc
                                                logger.info("[ReAct] Escalade: project_path depuis contexte récent: {}", _rpc_path_esc[:80])
                                except Exception:
                                    pass
                            # 1. Chemin explicite dans la query
                            if not _esc_project_path:
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

                # ── Détecteur de stagnation post-édition (contextuel) ─────────
                # Distingue les lectures progressives (nouveau fichier/zone/cible)
                # des lectures vraiment redondantes (même fichier+zone+intention N fois).
                # ── Guard pré-édition : boucle de lecture en phase d'exploration ──
                # Détecte les relectures redondantes avant tout edit (ex: script.js lu 6x
                # pendant une investigation, compaction → perte contexte → re-lecture).
                if not _has_done_edits and action.tool_name in _read_only_tools:
                    _pre_edit_guidance_at = 5 if _direct_coding_mode else 3
                    _pre_edit_guidance_hard_at = 8 if _direct_coding_mode else 5
                    _curr_pre_sig = _compute_read_sig(action.tool_name, action.tool_args)
                    _pre_progressive = (
                        _pre_edit_last_sig is None
                        or _curr_pre_sig[0] != _pre_edit_last_sig[0]
                        or _curr_pre_sig[2] != _pre_edit_last_sig[2]
                        or (
                            _curr_pre_sig[1] is not None
                            and _pre_edit_last_sig[1] is not None
                            and _curr_pre_sig[1] != _pre_edit_last_sig[1]
                        )
                    )
                    if _pre_progressive:
                        _pre_edit_redundant_streak = 0
                    else:
                        _pre_edit_redundant_streak += 1
                    _pre_edit_last_sig = _curr_pre_sig

                    if _pre_edit_redundant_streak == _pre_edit_guidance_at:
                        logger.warning(
                            "⚠️ Boucle exploration: {} lectures redondantes sur même cible (pré-édition) — guidance injectée",
                            _pre_edit_redundant_streak,
                        )
                        self._pending_loop_guidance = (
                            "⚠️ Tu relis le même fichier/zone plusieurs fois sans avancer. "
                            "Si le fichier est trop long, utilise `grep_search` pour cibler "
                            "directement le symbole ou la ligne cherchée. "
                            "Si la tâche est complexe, utilise `delegate_task` pour confier "
                            "l'exploration à un agent spécialisé."
                        )
                    elif _pre_edit_redundant_streak >= _pre_edit_guidance_hard_at:
                        # Pas de FINAL forcé : rien n'a encore été édité, forcer FINAL
                        # abandonnerait la tâche. On injecte une guidance maximale qui
                        # sera la première chose que voit le modèle à l'itération suivante.
                        logger.warning(
                            "⚠️ Boucle exploration renforcée: {} lectures redondantes (pré-édition) — guidance obligatoire",
                            _pre_edit_redundant_streak,
                        )
                        _finish_iteration(status="ok", summary=f"pre_edit_loop_guidance_reinforced_{_pre_edit_redundant_streak}")
                        self._pending_loop_guidance = (
                            "⚠️ STOP — tu relis la même cible depuis "
                            f"{_pre_edit_redundant_streak} itérations sans progresser. "
                            "Utilise OBLIGATOIREMENT `grep_search` avec le pattern exact "
                            "ou `delegate_task` pour sortir de cette boucle. "
                            "Ne relis pas le même fichier."
                        )

                if action.tool_name in _write_tools:
                    _has_done_edits = True
                    _post_edit_read_streak = 0
                    _redundant_read_streak = 0
                    _last_read_sig = None
                    _pre_edit_redundant_streak = 0
                    _pre_edit_last_sig = None
                elif _has_done_edits and action.tool_name in _read_only_tools:
                    _post_edit_guidance_total = 6 if _direct_coding_mode else 4
                    _post_edit_guidance_redundant = 3 if _direct_coding_mode else 2
                    _post_edit_force_redundant = 6 if _direct_coding_mode else 4
                    _post_edit_force_total = 14 if _direct_coding_mode else 10
                    _post_edit_read_streak += 1
                    _curr_sig = _compute_read_sig(action.tool_name, action.tool_args)
                    _is_progressive = (
                        _last_read_sig is None
                        or _curr_sig[0] != _last_read_sig[0]          # fichier différent
                        or _curr_sig[2] != _last_read_sig[2]          # intention/pattern différent
                        or (
                            _curr_sig[1] is not None
                            and _last_read_sig[1] is not None
                            and _curr_sig[1] != _last_read_sig[1]     # zone différente
                        )
                    )
                    if _is_progressive:
                        _redundant_read_streak = 0
                    else:
                        _redundant_read_streak += 1
                    _last_read_sig = _curr_sig

                    # Guidance seulement si les lectures deviennent redondantes (≥2 redondantes)
                    if _post_edit_read_streak >= _post_edit_guidance_total and _redundant_read_streak >= _post_edit_guidance_redundant:
                        if _redundant_read_streak == _post_edit_guidance_redundant:
                            logger.warning(
                                "⚠️ Stagnation post-édition: {} lectures redondantes (même fichier/zone) — guidance injectée",
                                _redundant_read_streak,
                            )
                            self._pending_loop_guidance = (
                                "⚠️ STOP — tu as fait des modifications et tu relis le même fichier/zone "
                                f"depuis {_redundant_read_streak} itérations sans rien changer. "
                                "Options : 1) Utilise `check_web_project` pour valider, "
                                "2) Corrige un problème trouvé avec write_file/edit_file, "
                                "3) Conclus avec FINAL_ANSWER si les corrections sont terminées."
                            )
                        elif _redundant_read_streak >= _post_edit_force_redundant or _post_edit_read_streak >= _post_edit_force_total:
                            # Forçage uniquement sur vraie boucle redondante (4+ identiques)
                            # ou si streak total dépasse 10 (garder un filet de sécurité)
                            logger.warning(
                                "⚠️ Stagnation post-édition forcée FINAL: {} lectures redondantes / {} total read-only",
                                _redundant_read_streak, _post_edit_read_streak,
                            )
                            _finish_iteration(status="ok", summary=f"forced_final_post_edit_stagnation_redundant_{_redundant_read_streak}")
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
                        _redundant_read_streak = 0
                        _last_read_sig = None

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
                    # Si 3+ actions browser_* consécutives SANS revalidation visuelle → forcer à "voir"
                    _tool = action.tool_name or ""
                    _iter_now = len(self.history)
                    if _tool in BROWSER_VISUAL_TOOLS or _tool in BROWSER_SELF_VISUAL_ACTION_TOOLS:
                        self._last_browser_visual_iter = _iter_now
                        self._browser_blind_streak = 0
                    elif _tool in BROWSER_ACTION_TOOLS:
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

                    # ── Guard anti-dérive post-blocage browser ─────────────────────────
                    # Après une impasse browser avec suggestion de dismiss, empêche la
                    # dérive vers run_command/curl/exec sans justification explicite.
                    if getattr(self, "_browser_post_block_guard", False):
                        if _tool in _BROWSER_DRIFT_TOOLS:
                            self._browser_post_block_guard = False
                            if not self._pending_loop_guidance:
                                self._pending_loop_guidance = (
                                    f"⚠️ GUIDANCE ANTI-DÉRIVE BROWSER: tu tentes d'utiliser `{_tool}` "
                                    "alors qu'un blocage browser est actif. Avant de quitter le navigateur, "
                                    "essaie d'abord : `browser_dismiss_popups`, `browser_scroll`, "
                                    "ou `browser_evaluate`. N'utilise des outils système que si "
                                    "le navigateur est définitivement infranchissable."
                                )
                        elif _tool.startswith("browser_"):
                            # Une action browser légitime → annule le guard
                            self._browser_post_block_guard = False

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

            # ── Budget par outil ──────────────────────────────────────────────
            # Plafonds adaptatifs : au-delà, guidance injectée (pas de hard-stop
            # pour ne pas bloquer des tâches légitimement longues).
            _TOOL_SOFT_BUDGET: dict[str, int] = {
                "read_file": 12,
                "list_directory": 6,
                "grep_search": 10,
                "find_files": 6,
                "run_command": 20,
                "http_request": 8,
                "browser_get_content": 6,
            }
            if action.action_type == ActionType.TOOL_CALL and action.tool_name:
                _tname = action.tool_name
                _tbudget = _TOOL_SOFT_BUDGET.get(_tname, 0)
                if _tbudget:
                    _tcalls = sum(
                        1 for _ak in self.action_history
                        if isinstance(_ak, tuple) and _ak[0] == _tname
                    )
                    if _tcalls >= _tbudget and not getattr(self, "_pending_loop_guidance", None):
                        self._pending_loop_guidance = (
                            f"⚠️ Budget outil dépassé : `{_tname}` appelé {_tcalls}× "
                            f"(budget conseillé : {_tbudget}×). "
                            "Continue uniquement si l'outil est strictement nécessaire, "
                            "sinon passe à l'étape productive suivante ou conclus avec FINAL."
                        )
                        logger.warning(
                            "⚠️ Budget outil: {} appelé {}x (budget={})",
                            _tname, _tcalls, _tbudget,
                        )
            # ─────────────────────────────────────────────────────────────────

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
                # ── StructuredState V1 : enregistrer la question en attente ──
                self._feed_structured_clarification(question)
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
                        # Générique création/planification/envoi → famille cohérente attendue
                        (r"\bj[''`]ai (créé|crée|planifié|planifie|enregistré|enregistre|configuré|configure|programmé|programme|ajouté|ajoute|sauvegardé|sauvegarde)\b", _HC_TOOLS_ANY_CREATE),
                        (r"\bj[''`]ai (envoyé|envoye|expedié|expedie)\b", _HC_TOOLS_ANY_SEND),
                        (r"\bla tâche a été (créée|planifiée|enregistrée|programmée)\b", _HC_TOOLS_TASK),
                        (r"\bj[''`]ai bien (enregistré|planifié|créé|configuré)\b", _HC_TOOLS_ANY_CREATE),
                        (r"\bj[''`]ai bien (envoyé|envoye)\b", _HC_TOOLS_ANY_SEND),
                        (r"\bc[''`]est (fait|configuré|planifié|enregistré|créé)\b", _HC_TOOLS_ANY_CREATE),
                        # Discord
                        (r"\bdiscord.{0,30}(animé|anime|géré|gere|organisé|organise|avec succès|avec succes)\b", _HC_TOOLS_DISCORD),
                        (r"\b(animé|anime).{0,20}discord\b", _HC_TOOLS_DISCORD),
                        (r"\b(salon|channel|canal).{0,20}(créé|crée|supprimé|supprime)\b", _HC_TOOLS_DISCORD),
                        (r"\b(message|messages|fichier|document|zip).{0,20}(envoyé|envoye|posté|poste|publié|publie)\b", _HC_TOOLS_MESSAGING | _HC_TOOLS_MAIL | _HC_TOOLS_DISCORD | _HC_TOOLS_SOCIAL),
                        # Apprentissage — pas une mutation, pas de faux positif
                        (r"\bj[''`]ai (appris|découvert|exploré|explore|recherché|recherche|étudié|etudie)\b", frozenset({"web_search", "web_search_brave", "ddg_search", "web_fetch", "memory_search", "browser_navigate", "browser_get_content"})),
                        # GitHub / Git
                        (r"\b(push réussi|push reussi|premier push|repository créé|repo créé|poussé sur github|pushé sur github|commit réussi|commit reussi|fichier poussé)\b", _HC_TOOLS_GITHUB),
                        # Mail
                        (r"\b(mail|email|courriel).{0,20}(envoyé|envoye|envoi effectué)\b", _HC_TOOLS_MAIL),
                        # Images / vidéos / logos
                        (r"\b(image|logo|thumbnail|vignette|svg|vidéo|video).{0,30}(généré|genere|créé|crée|produit|rendu)\b", _HC_TOOLS_IMAGE),
                        # Stripe (forme masc./fém. : créé/créée, annulé/annulée, envoyé/envoyée)
                        (r"\b(produit|abonnement|facture|paiement|remboursement).{0,20}(créé[e]?|crée[e]?|envoyé[e]?|annulé[e]?)\b", _HC_TOOLS_STRIPE),
                        # Notion
                        (r"\b(page|base de données|database).{0,20}(créée|ajoutée|mise à jour)\b", _HC_TOOLS_NOTION),
                    ]
                    # Seuls les outils dont l'observation.success=True comptent comme preuve
                    _tools_used_this_session = self._successful_session_tools
                    _all_known_tools = _tools_used_this_session

                    # Fix F: Exclusion browser — si des outils browser ont été utilisés dans la session,
                    # les patterns "message/messages envoyé" et "j'ai envoyé" sont des résumés légitimes
                    # d'actions browser réelles (ex: "j'ai eu une conversation de 10 messages avec Mistral").
                    # Ces patterns ne doivent pas déclencher le guard anti-hallucination.
                    _browser_tools_used_session = any(
                        t.startswith("browser_") for t in _tools_used_this_session
                    )

                    # Exclusion : références temporelles au passé ("j'ai créé plus tôt", "que j'avais envoyé hier")
                    # → le LLM parle d'une action passée, pas d'une action de cette session.
                    _TEMPORAL_BYPASS_RE = re.compile(
                        r"\bj[''`']ai\s+\w+(\s+\w+){0,5}\s+(plus\s+t[oô]t|pr[eé]c[eé]demment|avant|hier|la\s+derni[eè]re\s+fois|tout\s+[àa]\s+l[''']heure|tantôt|tantoˆt)|"
                        r"\b(que\s+tu\s+m[''']a(vai[st]|s)\s+demand\w*|comme\s+(demand\w*|convenu)|"
                        r"tout\s+[àa]\s+l[''']instant|juste\s+avant)\b",
                        re.IGNORECASE,
                    )
                    _has_temporal_ref = bool(_TEMPORAL_BYPASS_RE.search(_combined_text))
                    _has_runtime_claim_proof = _has_runtime_server_claim_proof(_combined_text, _tools_used_this_session)
                    _hallucination_blocked = False
                    if self._premature_final_retries < 2 and not _has_temporal_ref:
                        for _pattern, _expected_tools in _HALLUCINATION_PATTERNS:
                            if re.search(_pattern, _combined_text, re.IGNORECASE):
                                if _expected_tools == _HC_TOOLS_ANY_CREATE and _has_runtime_claim_proof:
                                    continue
                                # Fix F: Si des outils browser ont été utilisés dans la session,
                                # les patterns "message/messages envoyé" et "j'ai envoyé" sont des
                                # résumés légitimes d'actions browser (ex: conversation de 10 messages).
                                # On exclut les patterns d'envoi de messages pour éviter les faux positifs.
                                if _browser_tools_used_session and any(
                                    kw in _pattern for kw in (
                                        "message|messages", "envoyé|envoye|expedié|expedie",
                                        r"\bj[''`]ai (envoyé|envoye",
                                    )
                                ):
                                    continue
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
                    # Seuls les outils dont l'observation.success=True comptent comme preuve
                    _tu = self._successful_session_tools
                    _HP_NOPLAN = [
                        (r"\bj[''`]ai (créé|crée|planifié|planifie|enregistré|enregistre|configuré|configure|programmé|programme|ajouté|ajoute|sauvegardé|sauvegarde)\b", _HC_TOOLS_ANY_CREATE),
                        (r"\bj[''`]ai (envoyé|envoye|expedié|expedie)\b", _HC_TOOLS_ANY_SEND),
                        (r"\bj[''`]ai bien (enregistré|planifié|créé|configuré)\b", _HC_TOOLS_ANY_CREATE),
                        (r"\bj[''`]ai bien (envoyé|envoye)\b", _HC_TOOLS_ANY_SEND),
                        (r"\bc[''`]est (fait|configuré|planifié|enregistré|créé)\b", _HC_TOOLS_ANY_CREATE),
                        (r"\b(push réussi|push reussi|premier push|repository créé|repo créé|poussé sur github|commit réussi|commit reussi)\b", _HC_TOOLS_GITHUB),
                        (r"\b(mail|email|courriel).{0,20}(envoyé|envoye|envoi effectué)\b", _HC_TOOLS_MAIL),
                    ]
                    _all_known_np = _tu
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
                    _has_runtime_claim_proof_np = _has_runtime_server_claim_proof(_ct, _all_known_np)
                    if self._premature_final_retries < 2 and not _has_temporal_ref_np:
                        for _p, _et in _HP_NOPLAN:
                            if re.search(_p, _ct, re.IGNORECASE):
                                if _et == _HC_TOOLS_ANY_CREATE and _has_runtime_claim_proof_np:
                                    continue
                                if any(t in _all_known_np for t in _et):
                                    continue
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

                # ── ExecutionLedger FINAL guard ──────────────────────────────
                # Si le FINAL prétend avoir fait des mutations mais que le ledger
                # ne contient aucune mutation réussie, bloquer une fois et forcer
                # l'agent à exécuter réellement les outils.
                # Garde conservateur : n'intervient que si :
                #   1) la réponse FINAL affirme avoir agi (regex léger)
                #   2) le ledger ne contient AUCUNE mutation réussie
                #   3) on n'a pas déjà retry via ce guard
                _ledger_guard_triggered = False
                if not getattr(self, '_ledger_final_guard_used', False):
                    _final_text_lower = ((action.answer or "") + " " + (thought.content or "")).lower()
                    _runtime_claim_for_final = _has_runtime_server_claim_proof(_final_text_lower, self._successful_session_tools)
                    _CLAIM_PATTERNS = (
                        "j'ai créé", "j'ai crée", "j'ai envoyé", "j'ai envoye",
                        "j'ai écrit", "j'ai modifié", "j'ai configuré", "j'ai planifié",
                        "j'ai enregistré", "j'ai sauvegardé", "j'ai généré",
                        "c'est fait", "c'est envoyé", "c'est créé",
                        "i created", "i wrote", "i sent", "i saved", "i configured",
                        "fichier créé", "fichier écrit", "message envoyé",
                    )
                    _claims_action = any(p in _final_text_lower for p in _CLAIM_PATTERNS)
                    if _claims_action and not _runtime_claim_for_final and not self.execution_ledger.has_any_mutation():
                        self._ledger_final_guard_used = True
                        _ledger_guard_triggered = True
                        _led_tools = self.execution_ledger.successful_actions() or ["AUCUN"]
                        logger.warning(
                            "[LEDGER GUARD] FINAL prétend avoir agi mais aucune mutation dans le ledger "
                            "(outils réussis: {}) — retry",
                            _led_tools,
                        )
                        self.history.pop()
                        query = (
                            f"Requête originale: {original_query}\n\n"
                            "⛔ Tu as déclaré avoir accompli une action (création, envoi, écriture…) "
                            "mais le journal d'exécution ne contient AUCUNE mutation réussie.\n\n"
                            f"Outils exécutés avec succès: {', '.join(_led_tools)}\n\n"
                            "Tu DOIS appeler l'outil approprié et ATTENDRE le résultat "
                            "avant de conclure avec FINAL."
                        )
                        _finish_iteration(status="ok", summary="ledger_final_guard_blocked")
                if _ledger_guard_triggered:
                    continue

                # ── Heuristique H2 : mutations présentes mais hors famille attendue ──
                # Exemple : intent="discord" mais seules des mutations "write_file" existent.
                if (not getattr(self, '_ledger_final_guard_used', False)
                        and not _ledger_guard_triggered
                        and _claims_action
                        and not _runtime_claim_for_final
                        and self.execution_ledger.has_any_mutation()):
                    _ss_guard = self._structured_state
                    _guard_intent = _ss_guard.last_intent if _ss_guard else None
                    _expected_family = _LEDGER_INTENT_FAMILIES.get(_guard_intent, frozenset())
                    if _expected_family and not self.execution_ledger.has_mutation_in_family(_expected_family):
                        self._ledger_final_guard_used = True
                        _led_tools = self.execution_ledger.successful_actions() or ["AUCUN"]
                        logger.warning(
                            "[LEDGER GUARD H2] Mutations existent mais hors famille '{}' — retry",
                            _guard_intent,
                        )
                        self.history.pop()
                        query = (
                            f"Requête originale: {original_query}\n\n"
                            f"⛔ Tu as déclaré avoir agi pour une tâche '{_guard_intent}' "
                            f"mais aucun outil de la catégorie attendue n'a été exécuté.\n\n"
                            f"Outils exécutés: {', '.join(_led_tools)}\n\n"
                            "Appelle l'outil approprié avant de conclure."
                        )
                        _finish_iteration(status="ok", summary=f"ledger_guard_h2_wrong_family_{_guard_intent}")
                        continue

                # ── Heuristique H3 : cible explicite mentionnée mais aucune mutation pour elle ──
                # Repair léger fire-once. Plus conservateur que H2 :
                #   - flag propre (_ledger_h3_guard_used), pas _ledger_final_guard_used
                #   - message ⚠️ (vérification), pas ⛔ (blocage dur)
                #   - ne tire pas si H2 a déjà escaladé (_ledger_final_guard_used)
                if (not getattr(self, '_ledger_h3_guard_used', False)
                        and not getattr(self, '_ledger_final_guard_used', False)
                        and _claims_action
                        and not _runtime_claim_for_final
                        and self.execution_ledger.has_any_mutation()):
                    import re as _re_h3
                    _target_hint_h3: Optional[str] = None
                    _channel_match_h3 = _re_h3.search(r'#([\w\-]{2,32})', original_query)
                    if _channel_match_h3:
                        _target_hint_h3 = _channel_match_h3.group(1)
                    else:
                        _file_match_h3 = _re_h3.search(
                            r'[\w\-]+\.(py|js|ts|html|css|json|md|txt|yaml|toml)', original_query
                        )
                        if _file_match_h3:
                            _target_hint_h3 = _file_match_h3.group(0)
                    if _target_hint_h3 and not self.execution_ledger.has_mutation_for_target_hint(_target_hint_h3):
                        self._ledger_h3_guard_used = True
                        _led_tools_h3 = self.execution_ledger.successful_actions() or ["AUCUN"]
                        logger.warning(
                            "[LEDGER GUARD H3] Cible '{}' mentionnée mais aucune mutation pour cette cible"
                            " — repair léger (outils: {})",
                            _target_hint_h3,
                            _led_tools_h3,
                        )
                        self.history.pop()
                        query = (
                            f"Requête originale: {original_query}\n\n"
                            f"⚠️ Tu affirmes avoir agi, et une mutation a bien eu lieu, "
                            f"mais aucune action ne semble concerner la cible « {_target_hint_h3} ».\n\n"
                            f"Outils exécutés: {', '.join(_led_tools_h3)}\n\n"
                            "Vérifie que tu as bien traité la bonne cible, "
                            "puis agis dessus si ce n'est pas encore fait avant de conclure."
                        )
                        _finish_iteration(status="ok", summary=f"ledger_guard_h3_target_{_target_hint_h3}")
                        continue

                answer = action.answer or ""
                finish_reason = self._last_llm_meta.get("finish_reason")
                self._run_meta["agent_final_finish_reason"] = finish_reason

                # ── Chemin direct post-delegate_task ✅ : on saute tous les repairs ──
                # L'instruction injectée était explicite → la réponse est fiable, on ne re-sonde pas.
                # Guard : si des tâches de vérification restent non résolues dans le plan,
                # ne pas bypasser — le chemin FINAL normal les reflétera comme ⏭️.
                if self._after_delegate_success:
                    self._after_delegate_success = False  # consommé dans tous les cas
                    _pending_verify = [
                        t for t in self._task_plan
                        if not t.completed and is_verify_task(t.description.lower())
                    ]
                    if not _pending_verify:
                        # Cas nominal : aucune verify-task pendante → bypass autorisé
                        _finish_iteration(status="ok", summary="delegate_task_final_direct")
                        message = answer if answer.strip() else (
                            "Le CodeAgent a terminé avec succès. Consulte le workspace pour les fichiers créés."
                        )
                        self._mark_task_done("delegate_task_final_direct")
                        return message
                    # Verify-tasks non prouvées : traitement FINAL normal ci-dessous
                    logger.info(
                        "[delegate] Bypass annulé: {} verify-task(s) non résolue(s) "
                        "→ traitement FINAL normal (plan reflétera l'état réel)",
                        len(_pending_verify),
                    )

                # ── Guard anti-thought-leak : le LLM a mis sa réflexion dans ACTION_INPUT au lieu de la réponse ──
                # Cela arrive quand ACTION_INPUT est vide → fallback sur thought_content (ligne 1881)
                # NOTE: Grok met souvent la vraie réponse dans THOUGHT avec ACTION_INPUT vide.
                # On ne doit PAS considérer ça comme un leak si le contenu ne ressemble pas à
                # de la réflexion interne (sinon on gaspille des itérations en re-prompting).
                _answer_lower = (answer or "").lower().lstrip()
                _INTERNAL_PREFIXES = (
                    # FR — réflexion interne
                    "l'utilisateur me demande",
                    "l'utilisateur demande",
                    "l'utilisateur souhaite",
                    "l'utilisateur veut",
                    "l'utilisateur a demandé",
                    "l'utilisateur a sollicité",
                    "l'utilisateur me pose",
                    "me demande comment",
                    "me demande de",
                    "me demande si",
                    "je dois maintenant",
                    "je vais maintenant synthétiser",
                    "je vais maintenant formuler",
                    "je vais maintenant fournir",
                    "je vais maintenant résumer",
                    "je vais maintenant répondre",
                    "je vais maintenant donner",
                    "je vais répondre directement",
                    "je réponds directement",
                    "je dois analyser",
                    "je dois vérifier",
                    "je dois d'abord",
                    "je dois ensuite",
                    "je lance ",
                    "je lance`",
                    "je vais lire ",
                    "je vais vérifier ",
                    "je vais chercher ",
                    "je vais appeler ",
                    "je vais utiliser ",
                    "je vais grep",
                    "j'ai exécuté les",
                    "j'ai déjà exécuté",
                    "j'ai déjà effectué une recherche",
                    "j'ai maintenant toutes les",
                    "maintenant que j'ai",
                    "sur la base de",
                    "après avoir analysé",
                    "d'après les résultats",
                    "rien à faire ici",
                    "rien à faire,",
                    # EN — internal reasoning prefixes
                    "the user is asking",
                    "the user wants",
                    "the user asked",
                    "the user requested",
                    "i need to now",
                    "i should now",
                    "i will now",
                    "i'll now",
                    "let me analyze",
                    "let me now",
                    "let me provide",
                    "let me summarize",
                    "let me now provide",
                    "based on the",
                    "based on my",
                    "now that i have",
                    "i have already",
                    "i've already",
                    "i have now",
                    "i've now",
                    "having gathered",
                )
                _is_reasoning_prefix = any(_answer_lower.startswith(p) for p in _INTERNAL_PREFIXES)
                _thought_leaked = (
                    # Cas 1 : réponse non vide mais commence par un préfixe de réflexion interne
                    (bool(answer) and _is_reasoning_prefix)
                    or (
                        # Cas 2 : answer == thought ET contient des marqueurs de réflexion interne
                        bool(answer)
                        and bool(thought.content)
                        and answer.strip() == thought.content.strip()
                        and any(k in _answer_lower for k in (
                            "l'utilisateur", "je dois ", "je vais ", "il faut que je",
                            "the user ", "i need to", "i should ",
                        ))
                    )
                    or (
                        # Cas 3 : ACTION: FINAL sans ACTION_INPUT (answer vide/whitespace) + thought présent
                        # → le modèle a déclaré FINAL mais n'a rien écrit pour l'utilisateur
                        not (answer or "").strip()
                        and bool(thought.content)
                        and action.action_type == ActionType.FINAL_ANSWER
                    )
                )
                # P5 — modèles à thought_leak_risk élevé ont droit à plus de repairs
                _max_tleak = (
                    4 if getattr(self._model_profile, "thought_leak_risk", "low") == "high" else
                    3 if getattr(self._model_profile, "thought_leak_risk", "low") == "medium" else
                    2
                ) if self._model_profile else 2
                # ── AUTO-CLEAN: Cas 1 (préfixe de réflexion interne) ──
                # Au lieu de forcer une reformulation coûteuse (1-3 iter perdues),
                # on tente de nettoyer le texte en supprimant les phrases internes
                # du début pour extraire la réponse utile directement.
                if _thought_leaked and _is_reasoning_prefix and answer and len(answer) > 100:
                    _cleaned_answer = self._strip_thought_leak_prefix(answer)
                    if _cleaned_answer and len(_cleaned_answer) >= 50:
                        logger.info(
                            "🔧 THOUGHT leak auto-nettoyé: {} chars → {} chars (économise une reformulation)",
                            len(answer), len(_cleaned_answer),
                        )
                        action = Action(
                            action_type=ActionType.FINAL_ANSWER,
                            answer=_cleaned_answer,
                            tool_name=action.tool_name,
                            tool_args=action.tool_args,
                        )
                        answer = _cleaned_answer
                        _thought_leaked = False  # cleaned, no need to repair

                if _thought_leaked and self._thought_leak_repairs < _max_tleak:
                    self._thought_leak_repairs += 1
                    logger.warning(
                        f"⚠️ THOUGHT leaké comme réponse finale (tentative {self._thought_leak_repairs}/{_max_tleak}) - reformulation demandée"
                    )
                    # Conserver l'analyse faite dans ce thought pour ne pas la perdre.
                    _leaked_analysis = ""
                    if thought.content and len(thought.content.strip()) > 80:
                        _thought_excerpt = thought.content.strip()[:600]
                        _leaked_analysis = (
                            f"\nAnalyse déjà effectuée (réutilise-la, ne refais pas les mêmes lectures) :\n"
                            f"{_thought_excerpt}{'...' if len(thought.content.strip()) > 600 else ''}\n"
                        )
                    self.history.pop()
                    query = (
                        f"Requête originale: {original_query}\n"
                        f"{_leaked_analysis}\n"
                        "⚠️ Tu as mis ta réflexion interne dans ACTION_INPUT au lieu d'une vraie réponse.\n"
                        "Maintenant écris ta RÉPONSE DIRECTE à l'utilisateur dans ACTION_INPUT:\n\n"
                        "THOUGHT: (bref)\n"
                        "ACTION: FINAL\n"
                        "ACTION_INPUT: [ta réponse complète ici, en tutoyant/vouvoyant selon le contexte]"
                    )
                    _finish_iteration(status="ok", summary="thought_leaked_repair")
                    continue

                elif _thought_leaked:
                    # Repairs épuisés — tenter de nettoyer le THOUGHT prefix au lieu
                    # de retourner le raisonnement interne brut à l'utilisateur.
                    _stripped = self._strip_thought_leak_prefix(answer) if answer else None
                    if _stripped and len(_stripped) >= 20:
                        logger.warning(
                            "⚠️ THOUGHT leak non résolu après {}/{} tentatives — strip forcé ({} chars)",
                            _max_tleak, _max_tleak, len(answer) - len(_stripped),
                        )
                        action = Action(
                            action_type=ActionType.FINAL_ANSWER,
                            answer=_stripped,
                            tool_name=action.tool_name,
                            tool_args=action.tool_args,
                        )
                        answer = _stripped

                # ── VERBALIZATION REDIRECT ──────────────────────────────────
                # Détecte quand le LLM verbalise un plan/raisonnement dans sa réponse
                # finale au lieu d'exécuter un tool call. Marqueurs : **THOUGHT:**,
                # **PLAN:**, "je délègue", "je vais déléguer" sans tool call effectif.
                # Au lieu de tronquer ou reformuler, on redirige : le texte est conservé
                # comme message assistant et on relance un tour avec un nudge pour que
                # le LLM appelle le tool approprié.
                _MAX_VERB_REDIRECTS = 2
                if answer and self._verbalization_redirects < _MAX_VERB_REDIRECTS:
                    _answer_for_check = (answer or "").strip()
                    _al = _answer_for_check.lower()
                    _has_internal_markers = (
                        "**thought:**" in _al
                        or "**plan:**" in _al
                        or "**thought :**" in _al
                        or "**plan :**" in _al
                    )
                    _has_verbalized_delegation = bool(
                        any(p in _al for p in (
                            "je délègue", "je vais déléguer", "je délègue au",
                            "i will delegate", "i'll delegate", "delegating to",
                        ))
                        and not any(
                            h.action and h.action.action_type not in (ActionType.FINAL_ANSWER,)
                            and h.action.tool_name and "delegate" in (h.action.tool_name or "").lower()
                            for h in self.history[-3:]
                        )
                    )
                    if _has_internal_markers or _has_verbalized_delegation:
                        self._verbalization_redirects += 1
                        logger.warning(
                            "🔄 VERBALIZATION REDIRECT {}/{}: réponse finale contient un plan/raisonnement "
                            "sans tool call — redirection vers un nouveau tour",
                            self._verbalization_redirects, _MAX_VERB_REDIRECTS,
                        )
                        # Conserver le raisonnement du LLM dans l'historique
                        self.history.pop()
                        query = (
                            f"Requête originale: {original_query}\n\n"
                            f"Ton analyse (réutilise-la) :\n{_answer_for_check[:800]}\n\n"
                            "⚠️ Tu as verbalisé ton plan au lieu de l'exécuter.\n"
                            "N'ÉCRIS PAS ce que tu vas faire — FAIS-LE.\n"
                            "Appelle le tool approprié (delegate_task, web_search, etc.) "
                            "via ACTION/ACTION_INPUT MAINTENANT."
                        )
                        _finish_iteration(status="ok", summary="verbalization_redirect")
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
                _last_obs_for_browser = ""
                if self.history and self.history[-1].observation:
                    _last_obs_for_browser = self.history[-1].observation.content or ""
                _browser_rewrite = _browser_rewrite_human_navigation_action(
                    action.tool_name,
                    action.tool_args or {},
                    query=query,
                    last_surface=self._last_browser_surface or "",
                    last_observation=_last_obs_for_browser,
                )
                if _browser_rewrite is not None:
                    _new_tool, _new_args, _rewrite_reason = _browser_rewrite
                    logger.info("[BROWSER HUMAN] {} → {}", action.tool_name, _new_tool)
                    logger.debug("[BROWSER HUMAN] {}", _rewrite_reason)
                    action.tool_name = _new_tool
                    action.tool_args = _new_args

                _text_entry_rewrite = _browser_rewrite_text_entry_action(
                    action.tool_name,
                    action.tool_args or {},
                    last_observation=_last_obs_for_browser,
                )
                if _text_entry_rewrite is not None:
                    _new_tool, _new_args, _rewrite_reason = _text_entry_rewrite
                    logger.info("[BROWSER WRITE] {} → {}", action.tool_name, _new_tool)
                    logger.debug("[BROWSER WRITE] {}", _rewrite_reason)
                    action.tool_name = _new_tool
                    action.tool_args = _new_args

                _system_typing_rewrite = _browser_rewrite_system_typing_action(
                    action.tool_name,
                    action.tool_args or {},
                    last_observation=_last_obs_for_browser,
                    last_textbox_index=str(getattr(self, "_browser_last_textbox_index", "") or ""),
                )
                if _system_typing_rewrite is not None:
                    _new_tool, _new_args, _rewrite_reason = _system_typing_rewrite
                    logger.info("[BROWSER WRITE] {} → {}", action.tool_name, _new_tool)
                    logger.debug("[BROWSER WRITE] {}", _rewrite_reason)
                    action.tool_name = _new_tool
                    action.tool_args = _new_args

                _index_selector_rewrite = _browser_rewrite_index_like_selector_action(
                    action.tool_name,
                    action.tool_args or {},
                )
                if _index_selector_rewrite is not None:
                    _new_tool, _new_args, _rewrite_reason = _index_selector_rewrite
                    logger.info("[BROWSER INDEX] {} → {}", action.tool_name, _new_tool)
                    logger.debug("[BROWSER INDEX] {}", _rewrite_reason)
                    action.tool_name = _new_tool
                    action.tool_args = _new_args

                _selector_guess_rewrite = _browser_rewrite_selector_guess_to_index_action(
                    action.tool_name,
                    action.tool_args or {},
                    last_surface=self._last_browser_surface or "",
                    last_observation=_last_obs_for_browser,
                )
                if _selector_guess_rewrite is not None:
                    _new_tool, _new_args, _rewrite_reason = _selector_guess_rewrite
                    logger.info("[BROWSER INDEX] {} → {}", action.tool_name, _new_tool)
                    logger.debug("[BROWSER INDEX] {}", _rewrite_reason)
                    action.tool_name = _new_tool
                    action.tool_args = _new_args

                # P4 — Réécriture browser_type_index → browser_click_index
                # pour les contrôles non-texte (radio, checkbox, button, switch…)
                _ctrl_rewrite = _browser_rewrite_type_to_click_for_ctrl(
                    action.tool_name,
                    action.tool_args or {},
                    last_observation=_last_obs_for_browser,
                )
                if _ctrl_rewrite is not None:
                    _new_tool, _new_args, _rewrite_reason = _ctrl_rewrite
                    logger.info("[BROWSER CTRL] {} → {}", action.tool_name, _new_tool)
                    logger.debug("[BROWSER CTRL] {}", _rewrite_reason)
                    action.tool_name = _new_tool
                    action.tool_args = _new_args

                # Notifier le step_callback (ex: voix) avant l'exécution de l'outil
                if self.step_callback:
                    try:
                        self.step_callback(action.tool_name, action.tool_args or {})
                    except Exception as e:
                        logger.debug(f"Step callback: {e}")
                # Propager le budget temps restant et le task_id au HandlerContext
                if hasattr(self, '_loop_start_time') and hasattr(self.tools, '_v2_context'):
                    from time import perf_counter as _pc
                    _elapsed = _pc() - self._loop_start_time
                    _total = float(self.timeout_seconds or 600) + getattr(self, '_tool_time_total', 0.0)
                    self.tools._v2_context.budget_seconds = max(0.0, _total - _elapsed)
                    # Cancel canal : propager le parent task_id pour delegate_task
                    if self.task_id:
                        self.tools._v2_context.runtime_task_id = self.task_id
                # Mesurer le temps outil pour exclure du timeout de raisonnement
                from .caller_context import REACT as _CALLER_REACT
                _tool_exec_start = perf_counter()
                observation = await self.tools.execute(
                    action.tool_name, 
                    action.tool_args,
                    caller=_CALLER_REACT,
                )
                _tool_exec_duration = perf_counter() - _tool_exec_start
                # ── Cancel post-outil : stopper avant de réinjecter l'observation ──
                # Si le parent a été annulé PENDANT l'outil (ex: delegate_task long),
                # on coupe ici pour ne pas injecter un résultat orphelin dans la boucle.
                if self._orchestrator_enabled():
                    try:
                        if self.task_orchestrator.is_cancel_requested(self.task_id):
                            logger.info("[ReAct] cancel détecté post-outil task={}", self.task_id)
                            raise SystemExit("task_orchestrator_cancel")
                    except SystemExit:
                        raise
                    except Exception:
                        pass
                # Repousser la deadline du temps passé dans l'outil
                # → seul le temps de raisonnement (LLM) compte pour le timeout
                if hasattr(self, '_timeout_deadline'):
                    self._timeout_deadline += _tool_exec_duration
                    self._tool_time_total = getattr(self, '_tool_time_total', 0.0) + _tool_exec_duration
                # ── P4: Budget par catégorie ──
                _tool_cat = getattr(self.tools, "_tool_modules", {}).get(action.tool_name, "unknown")
                self._category_iter_counts[_tool_cat] = self._category_iter_counts.get(_tool_cat, 0) + 1
                _CAT_ITER_LIMITS = {
                    "web": 8, "browser": 32, "memory": 5,
                    "security": 10, "network": 8,
                }
                _cat_limit = _CAT_ITER_LIMITS.get(_tool_cat, 0)
                if _cat_limit and self._category_iter_counts[_tool_cat] >= _cat_limit:
                    logger.warning(
                        "[P4] Budget catégorie '{}' atteint ({}/{}) — outil={} — passage à FINAL suggéré",
                        _tool_cat, self._category_iter_counts[_tool_cat], _cat_limit, action.tool_name,
                    )
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

                # Fix 3.2: Vérifier que write_file/apply_patch produit un fichier non-vide
                # (uniquement sur chemins absolus pour éviter les faux positifs sur chemins relatifs)
                if observation.success and action.tool_name in ("write_file", "apply_patch"):
                    _wf_path = (action.tool_args or {}).get("path") or (action.tool_args or {}).get("file_path", "")
                    if _wf_path and os.path.isabs(_wf_path):
                        try:
                            if os.path.isfile(_wf_path) and os.path.getsize(_wf_path) == 0:
                                observation = Observation(
                                    content=f"❌ ERREUR : le fichier `{_wf_path}` a été écrit mais est VIDE (0 octet). L'écriture a échoué silencieusement. Recommence avec le contenu complet.",
                                    success=False,
                                )
                                step.observation = observation
                                logger.warning("[Fix3.2] {} → fichier vide: {}", action.tool_name, _wf_path)
                        except Exception:
                            pass

                # ── ExecutionLedger V1 : enregistrer chaque action exécutée ──
                try:
                    _led_target = _ledger_extract_target(
                        action.tool_name, action.tool_args or {},
                    )
                    _led_proof = _ledger_extract_proof(
                        action.tool_name, observation.content or "", observation.success,
                    )
                    _led_intent = None
                    _ss_for_led = self._structured_state
                    if _ss_for_led is not None:
                        _led_intent = _ss_for_led.last_intent
                    self.execution_ledger.append(
                        iteration=i,
                        action=action.tool_name,
                        target=_led_target,
                        success=observation.success,
                        proof=_led_proof,
                        meta={
                            "duration_ms": round(_tool_exec_duration * 1000, 1),
                            "intent": _led_intent,
                        },
                    )
                except Exception as _led_exc:
                    logger.debug("[ExecutionLedger] Échec enregistrement: {}", _led_exc)

                # ── ExecutionLedger : expansion des sous-outils parallel_tools ──
                if action.tool_name == "parallel_tools":
                    _sub_results_pt = getattr(observation, "sub_results", ())
                    for _sub in _sub_results_pt:
                        try:
                            _sub_target = _ledger_extract_target(_sub.tool_name, _sub.args)
                            _sub_proof = _ledger_extract_proof(
                                _sub.tool_name, _sub.content, _sub.success
                            )
                            self.execution_ledger.append(
                                iteration=i,
                                action=_sub.tool_name,
                                target=_sub_target,
                                success=_sub.success,
                                proof=_sub_proof,
                                meta={
                                    "duration_ms": 0.0,
                                    "intent": _led_intent,
                                    "via": "parallel_tools",
                                },
                            )
                        except Exception as _sub_led_exc:
                            logger.debug("[ExecutionLedger] parallel_tools sub: {}", _sub_led_exc)

                # ── Mission A : mémoriser le projet actif après mutation sur workspace ──
                # Permet au tour suivant de réutiliser ce projet sans find_files.
                if observation.success and _led_target and action.tool_name in _LEDGER_MUTATION_TOOLS:
                    _ws_match = re.search(r'(.+?[/\\]workspace[/\\][\w\-]+)', _led_target.replace("\\", "/"))
                    if _ws_match:
                        try:
                            _lum_mem = getattr(self.tools, "lumena", None)
                            _id_svc_mem = getattr(_lum_mem, "_identity_svc", None) if _lum_mem else None
                            if _id_svc_mem is not None and self.runtime_ctx is not None:
                                from ..core_services.identity_service import IdentityService as _IDS_M
                                _ck_mem = _IDS_M.resolve_channel_key(self.runtime_ctx)
                                if _ck_mem:
                                    _ws_path = _ws_match.group(1)
                                    _slug = _ws_path.replace("\\", "/").rsplit("/", 1)[-1]
                                    _id_svc_mem.remember_code_context(_ck_mem, _ws_path, project_slug=_slug)
                                    logger.debug("[RecentProject] Mémorisé: {} → {}", _ck_mem, _ws_path)
                                    # Poser immédiatement dans established_facts pour ce run
                                    _ss_proj = self._structured_state
                                    if _ss_proj is not None:
                                        _ss_proj.set_fact("active_project_path", _ws_path)
                                        _ss_proj.set_fact("active_project_slug", _slug)
                        except Exception as _mem_exc:
                            logger.debug("[RecentProject] Mémorisation échouée: {}", _mem_exc)

                # ── StructuredState V1 : alimenter recent_tools ──
                self._feed_structured_tool(action.tool_name)

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

                # ── Guard browser : impasse, échecs en série, répétition de cible ──
                # Couvre tous les outils browser_* (préfixe), pas seulement les 6 initiaux.
                _is_browser_tool = (action.tool_name or "").startswith("browser_")
                if _is_browser_tool:
                    obs_lower = (observation.content or "").lower()
                    _page_title = ""
                    _page_url = ""
                    _obs_raw = observation.content or ""
                    # Format dom_state / page_info : "Page: ...\nURL: ..."
                    _m_title = re.search(r"^Page:\s*(.+)$", _obs_raw, re.MULTILINE)
                    _m_url   = re.search(r"^URL:\s*(.+)$",  _obs_raw, re.MULTILINE)
                    if _m_title:
                        _page_title = _m_title.group(1).strip()
                    if _m_url:
                        _page_url = _m_url.group(1).strip()
                    # Fallback — format browser_navigate : "✅ Navigué vers: Title (URL)"
                    if not _page_url:
                        _m_nav = re.search(
                            r"(?:Navigu[eé] vers|Navigated to)[^\n]*\((https?://[^)\n]+)\)",
                            _obs_raw,
                        )
                        if _m_nav:
                            _page_url = _m_nav.group(1).strip()
                    if not _page_title and _page_url:
                        _m_nav_t = re.search(
                            r"(?:Navigu[eé] vers|Navigated to):\s*(.+?)\s*\(https?://",
                            _obs_raw,
                        )
                        if _m_nav_t:
                            _page_title = _m_nav_t.group(1).strip()

                    # Phase 1 browser: reconnaître la surface réelle avant d'insister.
                    # previous_surface : héritage de surface sur les observations sans signal fort
                    # (ex : browser_screenshot renvoie juste le chemin du fichier → pas de hints listing)
                    _obs_text = observation.content or ""
                    _surface, _surface_reason = _classify_browser_surface(
                        _obs_text,
                        current_url=_page_url,
                        page_title=_page_title,
                        previous_surface=self._last_browser_surface,
                    )
                    if _surface == self._last_browser_surface:
                        self._browser_surface_streak += 1
                    else:
                        self._browser_surface_streak = 1
                        self._last_browser_surface = _surface
                    self._last_browser_surface_reason = _surface_reason
                    logger.debug(
                        "[BROWSER SURFACE] {} (streak={}) — {}",
                        _surface,
                        self._browser_surface_streak,
                        _surface_reason,
                    )

                    _prev_progress_sig = self._last_browser_progress_sig
                    _progress_sig = _make_browser_progress_signature(
                        _surface,
                        _obs_text,
                        current_url=_page_url,
                        page_title=_page_title,
                        previous=_prev_progress_sig,
                    )
                    _progressed, _progress_reason = _browser_progress_delta(
                        _prev_progress_sig,
                        _progress_sig,
                        action_tool=action.tool_name or "",
                        observation_text=_obs_text,
                    )
                    self._last_browser_progress_sig = _progress_sig
                    _is_real_action = (action.tool_name or "") in BROWSER_ACTION_TOOLS
                    if _progressed:
                        self._browser_no_progress_streak = 0
                    elif _is_real_action:
                        # Seules les vraies actions (clics, saisie, navigation) comptent.
                        # Outils visuels ET utilitaires (scroll, dismiss_popups…) sont neutres.
                        self._browser_no_progress_streak += 1
                    logger.debug(
                        "[BROWSER PROGRESS] progressed={} streak={} — {}",
                        _progressed,
                        self._browser_no_progress_streak,
                        _progress_reason,
                    )

                    _intent_query = getattr(self, "_original_query", query) or query
                    _surface_mismatch, _surface_mismatch_reason = _browser_surface_mismatch(
                        _surface,
                        _intent_query,
                    )
                    _auth_recovery_target = None
                    if _surface == "contact_form" and _browser_is_auth_intent(_intent_query):
                        _auth_recovery_target = _extract_browser_auth_target(_obs_text)
                    if _surface == "iframe_heavy" and action.tool_name not in ("browser_frames", "browser_frame_content"):
                        self._pending_loop_guidance = (
                            "⚠️ GUIDANCE SURFACE: Cette page semble pilotée par des iframes. "
                            "Appelle `browser_frames` puis `browser_frame_content` pour lire le bon frame "
                            "avant de continuer à cliquer ou taper."
                        )
                    elif _surface_mismatch:
                        _soft_auth_recovery = _surface == "contact_form" and _browser_is_auth_intent(_intent_query)
                        if _soft_auth_recovery and _auth_recovery_target:
                            _idx, _label = _auth_recovery_target
                            self._pending_loop_guidance = (
                                "⚠️ GUIDANCE AUTH SPA: l'URL ressemble à un login, mais la vue affichée reste un formulaire de contact. "
                                f"Avant d'abandonner, clique explicitement sur le lien visible [{_idx}] \"{_label}\" "
                                "avec `browser_click_index`, puis relis le DOM. N'insiste pas avec `browser_navigate` sur la même route."
                            )
                        elif _soft_auth_recovery:
                            self._pending_loop_guidance = (
                                "⚠️ GUIDANCE AUTH SPA: tu es sur un formulaire de contact alors que la tâche demande une connexion. "
                                "Explore encore un peu comme un humain: lis le DOM, cherche un lien/bouton de connexion visible, "
                                "essaie un clic réel avant de conclure."
                            )

                        if self._browser_surface_streak >= (4 if _soft_auth_recovery else 2):
                            logger.warning(
                                "⛔ Mismatch browser surface/objectif: {} — arrêt propre",
                                _surface_mismatch_reason,
                            )
                            _finish_iteration(status="error", error="browser_surface_mismatch")
                            message = (
                                f"⛔ Navigation interrompue : **{_surface_mismatch_reason}**.\n\n"
                                f"Surface détectée : `{_surface}` ({_surface_reason}).\n\n"
                                "Je peux reprendre si tu me donnes une URL publique directe ou une surface plus adaptée."
                            )
                            self._mark_task_failed("browser_surface_mismatch")
                            return message
                        self._pending_loop_guidance = (
                            f"⚠️ GUIDANCE SURFACE: {_surface_mismatch_reason}. "
                            "Ne continue pas à agir comme si la page était directement exploitable. "
                            "Cherche une preview/public link/share link, ou change de surface."
                        )

                    if not _progressed:
                        _soft_auth_recovery = _surface == "contact_form" and _browser_is_auth_intent(_intent_query)
                        _no_progress_stop = 8 if _soft_auth_recovery else 6
                        _no_progress_warn = 4 if _soft_auth_recovery else 3
                        if self._browser_no_progress_streak >= _no_progress_stop:
                            logger.warning(
                                "⛔ Browser sans progression utile (surface={}, streak={}) — arrêt propre",
                                _surface,
                                self._browser_no_progress_streak,
                            )
                            _finish_iteration(status="error", error="browser_no_progress")
                            message = (
                                f"⛔ Navigation interrompue : aucune progression utile détectée sur la surface "
                                f"`{_surface}` après {self._browser_no_progress_streak} tours.\n\n"
                                f"Raison: {_progress_reason}. Surface: {_surface_reason}.\n\n"
                                "Je peux reprendre avec une URL plus directe, une stratégie différente, "
                                "ou un objectif browser plus simple."
                            )
                            self._mark_task_failed("browser_no_progress")
                            return message
                        if self._browser_no_progress_streak >= _no_progress_warn:
                            if _surface == "search_results":
                                self._pending_loop_guidance = (
                                    "⚠️ GUIDANCE PROGRESSION: tu restes sur une page de résultats sans progression utile. "
                                    "Ouvre un résultat concret ou navigue directement vers une URL plus ciblée."
                                )
                            elif _surface == "listing_results":
                                self._pending_loop_guidance = (
                                    "⚠️ GUIDANCE PROGRESSION: tu es sur une page d'annonces sans avancement. "
                                    "Clique sur une annonce spécifique ('Voir l'annonce'), utilise les filtres "
                                    "pour affiner (kilométrage, prix, année), ou scrolle pour charger plus de résultats. "
                                    "Évite de répéter browser_dom_state sans agir."
                                )
                            elif _surface == "public_form":
                                self._pending_loop_guidance = (
                                    "⚠️ GUIDANCE PROGRESSION: tu restes sur le même formulaire sans progrès visible. "
                                    "Relis `browser_dom_state`, identifie un autre champ ou change de stratégie."
                                )
                            elif _surface == "auth_form":
                                self._pending_loop_guidance = (
                                    "⚠️ GUIDANCE PROGRESSION: tu es sur un formulaire de connexion sans avancement. "
                                    "Vérifie que tu remplis les bons champs (email + mot de passe). "
                                    "Utilise `browser_dom_state` pour lister les indices exact, "
                                    "puis `browser_type_index` pour saisir chaque champ."
                                )
                            elif _surface == "contact_form":
                                self._pending_loop_guidance = (
                                    "⚠️ GUIDANCE PROGRESSION: tu es sur un formulaire de contact sans avancement. "
                                    "Assure-toi de remplir tous les champs obligatoires (nom, email, message). "
                                    "Utilise `browser_dom_state` pour voir les champs disponibles."
                                )
                            elif _surface == "spa_shell":
                                self._pending_loop_guidance = (
                                    "⚠️ GUIDANCE SPA: La page est un shell SPA sans contenu chargé. "
                                    "Essaie : 1) `browser_wait_for` pour attendre le chargement, "
                                    "2) `browser_evaluate` pour forcer l'état JavaScript, "
                                    "3) `browser_dom_state` puis cliquer sur un lien/onglet pour charger la vue."
                                )
                            elif _surface in ("normal_content", "detail_page"):
                                # SPA stagnation : si navigate sans changement d'URL → orienter vers DOM/JS
                                if action.tool_name == "browser_navigate" and _page_url == (
                                    _prev_progress_sig[1]
                                    if _prev_progress_sig and len(_prev_progress_sig) > 1
                                    else ""
                                ):
                                    self._pending_loop_guidance = (
                                        "⚠️ GUIDANCE SPA: La navigation vers cette URL ne change pas le contenu visible. "
                                        "La page semble être une SPA dont la vue ne se met pas à jour via browser_navigate. "
                                        "Stratégie : 1) `browser_dom_state` pour lister les liens/onglets cliquables, "
                                        "2) cliquer sur le lien/onglet cible pour changer la vue, "
                                        "3) `browser_evaluate` pour forcer un changement d'état JavaScript."
                                    )
                                else:
                                    self._pending_loop_guidance = (
                                        "⚠️ GUIDANCE PROGRESSION: tu restes sur la même surface sans changement utile. "
                                        "Revalide l'état réel puis choisis une action différente."
                                    )

                    # ── 2a. Détection d'impasse centralisée ──────────────────────────────
                    _imp_blocked, _imp_reason, _imp_try_dismiss = _detect_browser_impasse(
                        observation.content or ""
                    )
                    if _surface != "popup_blocked":
                        self._browser_dismiss_attempted = False
                    if _imp_blocked:
                        _dismiss_tried = getattr(self, "_browser_dismiss_attempted", False)
                        if _imp_try_dismiss and not _dismiss_tried and action.tool_name != "browser_dismiss_popups":
                            # Tentative automatique : un seul essai de dismiss avant de conclure
                            self._browser_dismiss_attempted = True
                            self._browser_post_block_guard = True  # anti-dérive activé
                            logger.info(
                                "[BROWSER IMPASSE] {} — tentative browser_dismiss_popups",
                                _imp_reason,
                            )
                            self._pending_loop_guidance = (
                                f"⚠️ GUIDANCE BROWSER: {_imp_reason}.\n"
                                "Appelle `browser_dismiss_popups` pour tenter de fermer l'overlay, "
                                "puis reprends depuis `browser_screenshot`.\n"
                                "⛔ Reste dans le navigateur — n'utilise pas run_command, curl "
                                "ou d'outils système avant de confirmer que le blocage est infranchissable."
                            )
                        else:
                            # ── P1 — Fallback CAPTCHA / anti-bot Google ───────────────────
                            # Si le dernier outil était browser_search_google et que le
                            # blocage est un CAPTCHA/anti-bot, tenter DuckDuckGo ou une URL
                            # directe plutôt que de stopper immédiatement. Une seule tentative.
                            _is_search_captcha = (
                                action.tool_name == "browser_search_google"
                                and any(tok in (_imp_reason or "").lower() for tok in (
                                    "captcha", "recaptcha", "bot", "cloudflare",
                                    "checking your browser", "challenge",
                                ))
                            )
                            _captcha_fallback_tried = getattr(
                                self, "_google_search_captcha_fallback_attempted", False
                            )
                            if _is_search_captcha and not _captcha_fallback_tried:
                                self._google_search_captcha_fallback_attempted = True
                                _search_query = str((action.tool_args or {}).get("query", ""))
                                _ddg_url = (
                                    "https://duckduckgo.com/?q="
                                    + _search_query.replace(" ", "+")
                                )
                                self._pending_loop_guidance = (
                                    f"⚠️ GUIDANCE CAPTCHA: Google a bloqué la recherche ({_imp_reason}).\n"
                                    "Stratégie de repli (essaie dans l'ordre) :\n"
                                    f"1. `browser_navigate` vers DuckDuckGo : `{_ddg_url}`\n"
                                    "2. Si aussi bloqué : navigue directement vers une URL candidate pertinente.\n"
                                    "3. Sinon : essaie Bing (`https://www.bing.com/search?q=...`).\n"
                                    "⛔ Ne passe pas en FINAL — un seul fallback suffit pour continuer."
                                )
                                logger.info(
                                    "[BROWSER CAPTCHA FALLBACK] {} → DuckDuckGo fallback, query={}",
                                    _imp_reason,
                                    _search_query,
                                )
                            else:
                                logger.warning(
                                    "⛔ Impasse browser détectée: {} — arrêt propre",
                                    _imp_reason,
                                )
                                _finish_iteration(status="error", error="browser_impasse")
                                message = (
                                    f"⛔ Navigation interrompue : **{_imp_reason}**.\n\n"
                                    f"Le site semble protégé ou non exploitable "
                                    f"({_imp_reason.lower()}).\n\n"
                                    f"Observation : {(observation.content or '')[:400]}"
                                )
                                self._mark_task_failed("browser_impasse")
                                return message

                    # ── 2b. Suivi des échecs techniques en série ─────────────────────────
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
                            f"Dernière observation: {(observation.content or '')[:500]}\n\n"
                            "Conseil: relancer avec une instruction plus simple (ex: 'ouvre google.com puis cherche ...') "
                            "ou vérifier que Playwright est bien installé (playwright install chromium)."
                        )
                        self._mark_task_failed("browser_fail_streak")
                        return message

                    # ── 2b-bis. Élément sans position connue → scrollIntoView + retry ────
                    # Cas réel : "Element [13] n'a pas de position connue (bbox=None)"
                    # L'élément existe mais est hors viewport ou masqué.
                    _NO_POS_PATTERNS = (
                        "n'a pas de position connue",
                        "no position",
                        "bbox=none",
                        "bounding_box indisponible",
                        "element is outside the viewport",
                        "element not visible",
                    )
                    if (
                        action.tool_name in ("browser_click_index", "browser_type_index")
                        and any(p in obs_lower for p in _NO_POS_PATTERNS)
                        and not self._pending_loop_guidance
                    ):
                        _no_pos_idx = str((action.tool_args or {}).get("index", "?"))
                        logger.info(
                            "[BROWSER NO-POS] index {} hors viewport — guidance scrollIntoView",
                            _no_pos_idx,
                        )
                        self._pending_loop_guidance = (
                            f"⚠️ GUIDANCE BROWSER: L'élément [{_no_pos_idx}] existe mais n'a pas de "
                            "position connue (hors viewport ou masqué).\n"
                            "Stratégie :\n"
                            f"1. `browser_evaluate(\"document.querySelectorAll('[data-lumena-idx]')[{_no_pos_idx}]?.scrollIntoView({{block:'center'}})\")`\n"
                            f"   OU `browser_scroll` pour amener l'élément en vue.\n"
                            f"2. Puis réessaie `{action.tool_name}` sur l'index [{_no_pos_idx}].\n"
                            "Si l'élément reste inaccessible, utilise `browser_dom_state` "
                            "pour trouver un index alternatif."
                        )

                    # ── 2b-ter. Signaux de succès précoces → guidance FINAL ──────────────
                    # Détecte les patterns prouvant que la tâche est déjà accomplie et guide
                    # vers FINAL immédiatement, évitant les tours superflus.
                    if observation.success and not self._pending_loop_guidance:
                        _early_success_signal: Optional[str] = None

                        # Signal 1 : connexion réussie (présence d'un lien de déconnexion)
                        if any(tok in obs_lower for tok in (
                            "déconnexion", "se déconnecter", "déconnectez-vous",
                            "logout", "log out", "sign out", "signout",
                        )) and _browser_is_auth_intent(query):
                            _early_success_signal = "connexion réussie (lien de déconnexion visible)"

                        # Signal 2 : formulaire soumis → httpbin.org/post (résultat de test)
                        elif "httpbin.org/post" in (_page_url or "").lower() or (
                            "httpbin" in obs_lower and "form" in obs_lower
                        ):
                            _early_success_signal = "formulaire soumis (réponse httpbin.org/post reçue)"

                        # Signal 3 : formulaire disparu + message de confirmation/succès
                        elif _surface in {"public_form", "contact_form", "auth_form"} and action.tool_name in (
                            "browser_click_index", "browser_submit_form"
                        ) and not _browser_observation_looks_like_popup_or_modal(observation.content or "") and any(tok in obs_lower for tok in (
                            "merci", "thank you", "thanks", "confirmation", "confirmé",
                            "bien reçu", "message envoyé", "votre message",
                            "votre demande", "success", "successfully sent",
                            "submitted", "soumis avec succès", "formulaire envoyé",
                        )):
                            _early_success_signal = "formulaire soumis avec succès (message de confirmation)"

                        # Signal 4 : chat ou messagerie — réponse reçue
                        elif action.tool_name in (
                            "discord_send", "discord_send_message",
                            "telegram_send_message", "send_whatsapp_message",
                        ) and observation.success:
                            _early_success_signal = "message envoyé avec succès"

                        if _early_success_signal:
                            logger.info(
                                "[BROWSER EARLY SUCCESS] {} — guidage vers FINAL",
                                _early_success_signal,
                            )
                            self._pending_loop_guidance = (
                                f"✅ SIGNAL DE SUCCÈS DÉTECTÉ : {_early_success_signal}.\n"
                                "La tâche principale est accomplie. "
                                "PASSE DIRECTEMENT À `ACTION: FINAL` avec un résumé clair de ce qui a été fait.\n"
                                "Ne relance pas d'autres outils browser inutiles."
                            )

                    # ── 2c. Détection répétition sur même cible browser ──────────────────
                    # Si le LLM clique/type sur le même index 3× sans progression → guidance
                    if action.tool_name in ("browser_click_index", "browser_type_index"):
                        _bct_idx = str((action.tool_args or {}).get("index", "?"))
                        _bct_key = f"{action.tool_name}:{_bct_idx}"
                        if not hasattr(self, "_browser_target_counts"):
                            self._browser_target_counts: dict = {}
                        self._browser_target_counts[_bct_key] = (
                            self._browser_target_counts.get(_bct_key, 0) + 1
                        )
                        if self._browser_target_counts[_bct_key] == 3:
                            logger.warning(
                                "[BROWSER REPEAT] {} sur index {} — 3e fois, guidance injectée",
                                action.tool_name, _bct_idx,
                            )
                            self._pending_loop_guidance = (
                                f"⚠️ GUIDANCE BROWSER: Tu viens d'appeler `{action.tool_name}` "
                                f"sur l'index {_bct_idx} pour la 3e fois sans progression visible.\n"
                                "L'index ne répond probablement pas comme attendu. "
                                "APPELLE `browser_screenshot` puis `browser_dom_state` "
                                "pour réévaluer l'état réel avant d'agir."
                            )
                        _obs_lower = (observation.content or "").lower()
                        if "textbox" in _obs_lower or "searchbox" in _obs_lower or "combobox" in _obs_lower:
                            self._browser_last_textbox_index = _bct_idx
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
            
            # 6. Compacter les observations volumineuses avant stockage (anti-context-poisoning)
            # Le modèle a déjà vu l'observation complète — on stocke une version compacte
            # pour que les futures itérations ne soient pas noyées dans du contenu stale.
            # RÈGLE : read_file/grep ont un seuil élevé (8000) — le contenu fichier est précieux.
            #         delegate_task/run_command ont un seuil bas (3000) — ce sont des résumés.
            if step.observation and step.observation.content:
                _raw_obs_len = len(step.observation.content)
                _tool_name_compact = action.tool_name or ""
                # Seuils adaptatifs par type d'outil
                if _tool_name_compact in ("read_file", "search_in_code", "grep_search", "find_files"):
                    _OBS_COMPACT_LIMIT = 8000   # seuil élevé : ne pas tronquer le contenu fichier
                elif _tool_name_compact in ("browser_get_content", "browser_evaluate"):
                    # Fix A: Pour les surfaces chat, augmenter la limite pour ne pas tronquer la conversation
                    _is_chat_surface = getattr(self, "_last_browser_surface", "") in ("chat_composer", "chat_transcript", "chat_response")
                    _OBS_COMPACT_LIMIT = 4000 if _is_chat_surface else 1800
                else:
                    _OBS_COMPACT_LIMIT = 3000   # seuil bas pour les outils qui retournent des rapports
                if _raw_obs_len > _OBS_COMPACT_LIMIT:
                    _anchor = _extract_anchor_facts(step.observation.content)
                    _is_chat_surface_compact = getattr(self, "_last_browser_surface", "") in (
                        "chat_composer", "chat_transcript", "chat_response"
                    )
                    _browser_compacted = _compact_browser_observation_payload(
                        _tool_name_compact,
                        step.observation.content,
                        is_chat_surface=_is_chat_surface_compact,
                    )
                    if _browser_compacted is not None:
                        _c_body = _browser_compacted
                    elif _tool_name_compact in (
                        "delegate_task", "create_project", "generate_website",
                        "write_website_files", "website_build",
                    ):
                        # Résultats de délégation : garder début (statut) + fin (conclusion)
                        _c_head = step.observation.content[:600]
                        _c_tail = step.observation.content[-200:]
                        _c_body = (
                            f"{_anchor}{_c_head}\n[...{_raw_obs_len - 800} chars compactés — "
                            f"contenu disponible sur demande...]\n{_c_tail}"
                        )
                    elif _tool_name_compact in ("run_command", "execute_code", "dev_run_fix"):
                        # Sorties de commandes : garder début (env) + fin (résultat/erreur)
                        _c_head = step.observation.content[:400]
                        _c_tail = step.observation.content[-400:]
                        _c_body = (
                            f"{_anchor}{_c_head}\n[...sortie tronquée ({_raw_obs_len} chars)...]\n{_c_tail}"
                        )
                    elif _tool_name_compact in (
                        "read_file", "search_in_code", "grep_search", "find_files",
                    ):
                        # Lectures fichiers : seuil élevé atteint → garder 3000 chars (début)
                        # Pas d'ancre ici : le contenu brut est déjà préservé intégralement
                        _c_body = (
                            step.observation.content[:3000]
                            + f"\n[...{_raw_obs_len - 3000} chars omis — relire avec plage de lignes si nécessaire...]"
                        )
                    else:
                        _c_head = step.observation.content[:500]
                        _c_tail = step.observation.content[-300:]
                        _c_body = (
                            f"{_anchor}{_c_head}\n[...{_raw_obs_len - 800} chars compactés...]\n{_c_tail}"
                        )
                    step = ReActStep(
                        thought=step.thought,
                        action=step.action,
                        observation=Observation(
                            content=_c_body,
                            success=step.observation.success,
                        ),
                    )
                    logger.debug(
                        f"🗜️ Observation compactée: {_raw_obs_len} → {len(_c_body)} chars "
                        f"({_tool_name_compact})"
                    )
            # 6. Ajouter à l'historique
            # Accumuler le nom de l'outil dans le set session (survit aux compactions)
            if action.tool_name:
                self._all_session_tools.add(action.tool_name)
                # N'ajouter aux outils réussis que si l'observation indique un succès réel
                if observation.success:
                    self._successful_session_tools.add(action.tool_name)
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
                # Fix E: Augmenter le seuil browser à 20 pour laisser le temps de changer de stratégie
                # (ex: Mistral bloque après 2 échanges → le LLM doit naviguer vers HuggingFace Chat)
                _guard_limit = 20 if _has_browser else (16 if _needs_more_space else 10)
                _warn_limit = 15 if _has_browser else (12 if _needs_more_space else 7)

                # Fix E: Réinitialiser le compteur si une navigation réussie vers une nouvelle URL
                # (changement de domaine = nouvelle stratégie = progression réelle)
                if (
                    _has_browser
                    and step.action
                    and step.action.tool_name == "browser_navigate"
                    and step.observation
                    and "✅" in (step.observation.content or "")
                    and self._iterations_without_progress > 0
                ):
                    # Navigation réussie vers un nouveau site = reset du compteur de stagnation
                    _nav_url = str((step.action.tool_args or {}).get("url", "")).lower()
                    _prev_urls = [
                        str((h.action.tool_args or {}).get("url", "")).lower()
                        for h in self.history[-8:]
                        if h.action and h.action.tool_name == "browser_navigate"
                    ]
                    # Si l'URL est différente des 8 dernières navigations → nouvelle stratégie
                    if _nav_url and _nav_url not in _prev_urls[:-1]:
                        logger.debug(
                            "[PLAN GUARD] Navigation vers nouveau site '{}' — reset compteur stagnation",
                            _nav_url[:60],
                        )
                        self._iterations_without_progress = 0

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
            
            # ── Post-succès delegate_task : chemin FINAL direct ──
            # Après un delegate_task ✅, le rapport du CodeAgent EST la vérification.
            # On force le chemin FINAL sans repasser par "continue" pour éviter
            # les tours perdus sur thought_leak / reformulation inutile.
            _is_delegate_success = (
                action.tool_name in ("delegate_task", "delegate_task_bg")
                and observation.success           # preuve structurelle, pas juste le badge ✅
                and obs_text
                and (obs_text.lstrip().startswith("✅") or "✅" in obs_text[:60])
            )
            if _is_delegate_success:
                # Réconcilier le plan avant le FINAL — contourne _MAX_COMPLETIONS_PER_CALL=2
                # pour les rapports CodeAgent qui couvrent plusieurs étapes d'un coup
                self._reconcile_plan_from_delegate_success(obs_text, i)
                query = (
                    f"Requête originale: {original_query}\n\n"
                    f"Le CodeAgent a terminé avec succès :\n{obs_text[:3000]}\n\n"
                    "INSTRUCTION : Rédige maintenant ta réponse finale à l'utilisateur en résumant "
                    "ce qui a été accompli. Utilise OBLIGATOIREMENT :\n"
                    "THOUGHT: (1 ligne)\n"
                    "ACTION: FINAL\n"
                    "ACTION_INPUT: [résumé clair de ce qui a été fait]"
                )
                self._after_delegate_success = True
                _finish_iteration(status="ok", summary="delegate_task_success_direct_final")
            else:
                # P5 — action_inline_risk : injecter rappel format si inline détecté au-delà du seuil
                _inline_hint = ""
                if _inline_reminder_thresh > 0 and 0 < self._action_inline_count <= _inline_reminder_thresh + 2:
                    _inline_hint = "\n\n⚠️ FORMAT: Écris ACTION: en début de ligne séparée (pas sur la même ligne que THOUGHT:)."
                # Adapter le hint de conclusion selon l'avancement réel de la tâche
                if is_web_request and (has_html or has_css or has_js):
                    # Des fichiers web ont été créés : rappeler quand conclure
                    _conclusion_hint = " Si tu as créé les 3 fichiers (HTML, CSS, JS), utilise ACTION: FINAL."
                elif is_web_request and action.tool_name in _read_only_tools:
                    # Tâche web mais en phase d'investigation (pas encore de fichiers) : orienter vers grep/ciblage
                    _conclusion_hint = " Utilise grep_search ou read_file ciblé pour trouver l'information, puis agis."
                else:
                    _conclusion_hint = ""
                query = f"""Requête originale: {original_query}
{files_reminder}
Observation de l'action précédente ({action.tool_name}): {obs_text}{_inline_hint}

Continue à répondre à la question initiale.{_conclusion_hint}"""
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
