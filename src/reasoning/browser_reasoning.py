"""Helpers de raisonnement NAVIGATEUR — decisions pures, sans etat.

EXTRAIT DE `react.py` le 2026-08-27 par le lot RF-1 du plan
`plans/REFACTOR_REACT_DEDICATED_PLAN_2026-08-27.md`.

Deplacement quasi verbatim : noms, signatures, corps et ordre sont identiques a
ce qu'ils etaient dans `react.py`. Aucune correction, aucun renommage, aucun
changement de valeur par defaut n'a ete introduit par ce lot.

Ce module ne connait PAS `ReActLoop` :

  * il ne prend jamais `self` ;
  * il n'importe jamais `react.py` (invariant 2 du plan) — un cycle rendrait
    l'import fragile et forcerait des imports locaux dans tout le paquet ;
  * l'etat navigateur de `ReActLoop` (27 paires property/setter, l. 2913-3157
    de `react.py`) reste chez lui : ce lot ne deplace que les decisions pures.

`react.py` reexporte les 69 symboles ci-dessous : 27 d'entre eux sont lus
ailleurs dans le fichier, et 28 sont importes par d'autres modules ou tests
(`_browser_progress_delta` 21 fois, `_detect_browser_impasse` 11 fois,
`_classify_browser_surface` 5 fois). Les 8 sans appelant externe sont
reexportes aussi : un lot d'extraction ne decide pas d'un nettoyage de surface.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
import unicodedata
from typing import Any, Dict, Iterable, Optional

# Import RELATIF, identique a celui de `react.py` (l. 64) : `browser_reasoning`
# vit dans le meme paquet. Un import absolu `documents.document_intent` ne
# resout que si `src/` est sur `sys.path` — vrai sous pytest, FAUX pour un
# processus fils lance par `python -m src.llm.codex_mcp_bridge` depuis la
# racine du depot. C'est ce qui a casse ce fils lors du premier jet du lot.
from ..documents.document_intent import normalize_document_query


_READ_SIG_BUCKET = 50  # granularité en lignes pour la détection de zone redondante
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


_BROWSER_SOURCE_PIVOT_MARKERS = (
    "cloudflare", "anti-bot", "anti bot", "challenge", "captcha", "recaptcha",
    "acces refuse", "access denied", "403", "rate limit", "429",
)


def _legal_browser_source_pivot(
    current_url: str,
    reason: str,
    original_query: str,
    tried_origins: Iterable[str],
    *,
    max_origins: int = 3,
) -> tuple[str, str] | None:
    """Return one bounded legal source-pivot instruction for a blocked origin."""
    folded_reason = normalize_document_query(reason)
    if not any(marker in folded_reason for marker in _BROWSER_SOURCE_PIVOT_MARKERS):
        return None
    try:
        from urllib.parse import urlparse

        parsed = urlparse(str(current_url or ""))
        origin = f"{parsed.scheme}://{parsed.netloc}".casefold() if parsed.netloc else ""
    except (TypeError, ValueError):
        origin = ""
    if not origin:
        origin = "<origine-inconnue>"
    tried = {str(value or "").casefold() for value in tried_origins if str(value or "")}
    if origin in tried or len(tried) >= max_origins:
        return None
    guidance = (
        f"⚠️ SOURCE BLOQUEE: `{origin}` refuse l'acces ({reason}). "
        "Ne retente pas cette origine et ne contourne aucun CAPTCHA/WAF.\n"
        "Pivote maintenant vers UNE source publique distincte et legale : "
        "`web_fetch` sur une URL publique candidate, `web_search` pour un autre domaine, "
        "une API officielle, ou un MCP deja actif. Recoupe le resultat avec la demande "
        f"originale (`{str(original_query or '')[:240]}`)."
    )
    return origin, guidance


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
    if tool_name not in {"browser_type", "browser_click", "browser_select"}:
        return None
    selector = str((tool_args or {}).get("selector", "")).strip()
    if not selector:
        return None
    match = re.fullmatch(r"\[(\d+)\]", selector)
    if match is None:
        return None
    idx = match.group(1)
    if tool_name == "browser_select":
        # LOT Z19 — run « Pelage » (2026-08-17) : browser_select(selector='[9]',
        # label='Marie Curie', by='index') → Playwright lève « '[9]' is not a
        # valid selector ». Le mécanisme de conversion existait déjà pour
        # browser_type et browser_click ; il manquait la troisième branche.
        # Attention au collision de noms : le `index` de browser_select désigne
        # le RANG DE L'OPTION, pas l'index DOM — il devient `option_index`.
        args: Dict[str, Any] = {"index": idx}
        for _src, _dst in (("label", "label"), ("value", "value"), ("index", "option_index")):
            if _src in (tool_args or {}):
                args[_dst] = (tool_args or {})[_src]
        return (
            "browser_select_index",
            args,
            f"sélecteur '{selector}' reconnu comme index DOM [{idx}] — conversion vers browser_select_index",
        )
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


_BROWSER_EVALUATE_MUTATION_RE = re.compile(
    r"\.click\s*\(|\.dispatchEvent\s*\(|\bdispatchEvent\s*\(|"
    r"\brequestSubmit\s*\(|\.value\s*=|\bKeyboardEvent\s*\(|\bMouseEvent\s*\(",
    re.IGNORECASE,
)
_BROWSER_INTERACTION_STATE_KEYS = frozenset({
    "activecount", "after", "before", "changed", "checked", "classname",
    "classes", "counter", "countertext", "grid", "movecount", "moves",
    "position", "positions", "score", "selected", "state", "states", "value",
})
_BROWSER_EVALUATE_ERROR_MARKERS = (
    "typeerror", "syntaxerror", "referenceerror", "cannot read", "is not defined",
    "is not a function", "evaluation failed", "js error",
)


def _browser_evaluate_payload(observation_text: str) -> Any:
    """Extract the structured value returned by a successful browser_evaluate."""
    text = str(observation_text or "").strip()
    folded = "".join(
        char for char in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(char)
    ).lower()
    if not text or any(marker in folded for marker in _BROWSER_EVALUATE_ERROR_MARKERS):
        return None
    if "\u2705" not in text and "js execute" not in folded:
        return None

    payload_text = text.split("\u2192", 1)[-1].strip() if "\u2192" in text else text
    starts = [index for index in (payload_text.find("{"), payload_text.find("[")) if index >= 0]
    if not starts:
        return None
    start = min(starts)
    opener = payload_text[start]
    end = payload_text.rfind("}" if opener == "{" else "]")
    if end < start:
        return None
    candidate = payload_text[start:end + 1]
    for loader in (json.loads, ast.literal_eval):
        try:
            return loader(candidate)
        except (TypeError, ValueError, SyntaxError):
            continue
    return None


def _browser_payload_has_dynamic_state(payload: Any) -> bool:
    if isinstance(payload, dict):
        for raw_key, value in payload.items():
            key = re.sub(r"[^a-z0-9]", "", str(raw_key).lower())
            is_dynamic_key = key in _BROWSER_INTERACTION_STATE_KEYS or any(
                key.startswith(root) and len(key) > len(root)
                for root in _BROWSER_INTERACTION_STATE_KEYS
            )
            if is_dynamic_key and value not in (None, "", [], {}):
                return True
            if _browser_payload_has_dynamic_state(value):
                return True
    elif isinstance(payload, (list, tuple)):
        return any(_browser_payload_has_dynamic_state(item) for item in payload)
    return False


def _browser_evaluate_proves_interaction(script: str, observation_text: str) -> bool:
    """Require both a browser mutation and a concrete dynamic-state observation."""
    if not _BROWSER_EVALUATE_MUTATION_RE.search(str(script or "")):
        return False
    payload = _browser_evaluate_payload(observation_text)
    return payload is not None and _browser_payload_has_dynamic_state(payload)


_BROWSER_USER_MUTATION_TOOLS = frozenset({
    "browser_click", "browser_click_index", "browser_click_smart",
    "browser_type", "browser_type_index", "browser_select",
    "browser_press_key", "browser_check", "browser_uncheck",
})
_BROWSER_STATE_READ_TOOLS = frozenset({
    "browser_get_content", "browser_dom_state", "browser_read", "browser_extract",
})


_BROWSER_CLICK_TOOLS = frozenset({
    "browser_click", "browser_click_index", "browser_click_smart",
})
# LOT M1 (run CaveÀVin 2026-08-14) — la trace d'un clic nomme le TYPE d'élément
# atteint : `✅ Clic sur [1] link "S'inscrire" … → navigation vers /register`.
_LINK_CLICK_RE = re.compile(r"clic\s+sur\s+\[?\d*\]?\s*link\b", re.IGNORECASE)


def _browser_click_is_link_navigation(tool_name: str, observation: str) -> bool:
    """LOT M1 (run CaveÀVin 2026-08-14) — un clic sur un LIEN change de page ; ce
    n'est PAS une interaction produit.

    CaveÀVin a été clôturée en pleine vérification : le lead a cliqué « S'inscrire »
    (un `<a>`), lu le DOM du formulaire — DOM différent, donc `dom_delta` a posé
    `local_preview_interaction_proven` — et le FINALIZE est tombé 2 s plus tard.
    Aucun champ rempli, aucun compte créé, aucune bouteille ajoutée, alors que
    l'objectif exigeait « inscription → connexion → ajout → liste ».

    `browser_proven` dépend entièrement de ce flag quand l'objectif réclame une
    interaction (cf. `_mission_completion_evidence`), d'où la clôture prématurée.

    Un clic sur un `button` (soumettre un formulaire) reste une vraie mutation,
    même s'il provoque une redirection : c'est le TYPE d'élément qui tranche, pas
    le fait de naviguer.
    """
    if str(tool_name or "") not in _BROWSER_CLICK_TOOLS:
        return False
    return bool(_LINK_CLICK_RE.search(str(observation or "")))


def _browser_state_fingerprint(text: str) -> str:
    """Stable compact fingerprint for a browser state observation."""
    # A form submitted without ``preventDefault`` can merely append its values
    # to the URL and reload the exact same empty DOM.  That is navigation, not
    # proof that the requested UI result/state changed.  Keep URL tracking in
    # the browser progress guard, but exclude it from interaction authority.
    stable_text = re.sub(
        r"^URL:\s*.*$", "", str(text or ""), flags=re.IGNORECASE | re.MULTILINE
    )
    normalized = re.sub(r"\s+", " ", stable_text).strip().lower()
    if not normalized:
        return ""
    return hashlib.sha256(normalized.encode("utf-8", errors="replace")).hexdigest()


def _manual_browser_flow_proves_interaction(
    previous_fingerprint: str,
    *,
    mutation_seen: bool,
    current_observation: str,
) -> bool:
    """Proof = prior DOM read + real user action + a different subsequent DOM read."""
    current = _browser_state_fingerprint(current_observation)
    return bool(
        previous_fingerprint
        and mutation_seen
        and current
        and current != previous_fingerprint
    )


def _advance_manual_browser_flow(
    previous_fingerprint: str,
    *,
    mutation_pending: bool,
    tool_name: str,
    observation: str,
) -> tuple[bool, str, bool]:
    """Advance strict proof without discarding an unobserved user action.

    A click/type acknowledgement does not include the resulting static text.
    The first follow-up DOM read may expose only interactive controls, so keep
    the action pending until a later state read actually differs.
    """
    tool = str(tool_name or "")
    # LOT M1 — un clic sur un LIEN n'arme PAS la preuve d'interaction : changer de
    # page n'est pas agir sur le produit (run CaveÀVin).
    _real_user_mutation = (
        tool in _BROWSER_USER_MUTATION_TOOLS
        and not _browser_click_is_link_navigation(tool, observation)
    )
    pending = bool(mutation_pending or _real_user_mutation)
    fingerprint = str(previous_fingerprint or "")
    proven = False
    if tool in _BROWSER_STATE_READ_TOOLS:
        proven = _manual_browser_flow_proves_interaction(
            fingerprint,
            mutation_seen=pending,
            current_observation=observation,
        )
        current = _browser_state_fingerprint(observation)
        if current:
            fingerprint = current
        if proven:
            pending = False
    return proven, fingerprint, pending


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


BROWSER_ACTION_TOOLS: frozenset = frozenset({
    "browser_click", "browser_click_index", "browser_click_smart",
    "browser_click_at", "browser_type", "browser_type_index",
    "browser_navigate", "browser_hover", "browser_select",
    "browser_keyboard_press", "browser_drag", "browser_drag_at",
})


def _local_preview_loop_decision(
    is_local_preview: bool,
    tool_name: str,
    progressed: bool,
    current_streak: int,
    evaluate_asked: bool,
    *,
    warn_at: int = 3,
    stop_at: int = 5,
    interaction_proven: bool = False,
    tool_succeeded: bool = True,
) -> tuple:
    """LOT 2.11.C/D — décision PURE sur une preview LOCALE servie par Lumena.

    Cas racine (run memo) : sur un jeu/preview local, Lumena inspecte en boucle
    (screenshot/dom_state) sans que rien ne progresse ; ces outils VISUELS ne
    comptent pas dans `browser_no_progress_streak` (réservé aux vraies actions)
    → aucun stop ne se déclenchait, boucle infinie sur un livrable pourtant servi.

    Politique BORNÉE (pas d'arrêt bête, une méthode plus intelligente d'abord) :
      - inspection visuelle répétée sans progrès → au bout de `warn_at`, on ESCALADE
        UNE fois vers `browser_evaluate` (lire l'état JS concret : compteur de coups,
        score, assertion DOM) ;
      - si l'évaluation ne prouve toujours rien (ou `stop_at` atteint après escalade)
        → STOP et conclusion HONNÊTE (« page servie, navigation OK, mais validation
        interactive complète NON prouvée ») — jamais « jeu validé » sans preuve.

    LOT R′ (run Cadran, 2026-08-14) — CE GARDE A COUPÉ UNE MISSION QUI AVAIT SA
    PREUVE. Séquence exacte :

        23:55:47  clic « Auteur » → L'Étranger/Camus devient Fahrenheit/Bradbury
        23:55:56  les 8 lignes relues dans le nouvel ordre
        23:56:03  browser_evaluate SANS paramètre `script` (appel mal formé)
        23:56:10  browser_evaluate (test du thème) → STOP

    La mission a conclu à 7 min 19 sur 60, sans avoir vérifié le thème persistant,
    le responsive ni le clavier. Deux défauts, tous deux ici :

    1. **La preuve n'entrait pas dans la décision.** `local_preview_interaction_proven`
       est calculé, journalisé et persisté en métadonnée — mais APRÈS cet appel, et
       sans jamais lui être transmis. Cause racine commune aux lots F→I : le fait
       existait, était calculé… puis jeté avant la décision.
    2. **Réclamer une preuve puis punir celui qui la fournit.** L'escalade demande
       « fais un `browser_evaluate` » et le PREMIER qui suit coupait, quel que soit
       son contenu — y compris celui qui apportait la preuve demandée.

    ⚠️ Nuance qui sauve le cas d'origine : un `browser_evaluate` techniquement
    réussi mais VIDE ne prouve rien. Sans le distinguer, le jeu memo reboucle à
    l'infini. D'où deux faits distincts — `interaction_proven` (la preuve) et
    `tool_succeeded` (l'appel a abouti) — et jamais l'un pour l'autre.

    Retour : (action, new_streak, new_evaluate_asked) où
      action ∈ {"none", "escalate", "stop"}.
    Ne touche à AUCUN état ; le caller applique le résultat.
    """
    if not is_local_preview:
        return ("none", 0, False)
    if interaction_proven:
        # L'interactif est DÉMONTRÉ : cette boucle n'a plus lieu d'être.
        return ("none", 0, False)
    if progressed:
        # La boucle est cassée : un vrai progrès a eu lieu → on repart à zéro.
        return ("none", 0, False)
    is_evaluate = (tool_name or "") == "browser_evaluate"
    is_visual = (tool_name or "") in BROWSER_VISUAL_TOOLS
    # Seules l'inspection visuelle et l'évaluation alimentent ce compteur.
    if not (is_visual or is_evaluate):
        return ("none", current_streak, evaluate_asked)
    if is_evaluate and not tool_succeeded:
        # Appel mal formé (`script` manquant, JS invalide) : la tentative demandée
        # par l'escalade n'a pas eu lieu. On ne la consomme pas — sinon un typo
        # coûte la mission, ce qui est arrivé à 23:56:03.
        return ("none", current_streak, evaluate_asked)
    new_streak = current_streak + 1
    # L'escalade a DÉJÀ été demandée et une évaluation ABOUTIE revient sans preuve
    # → stop. C'est le cas memo : on a demandé l'état JS, il ne démontre rien.
    if is_evaluate and evaluate_asked:
        return ("stop", new_streak, True)
    if new_streak >= stop_at and evaluate_asked:
        return ("stop", new_streak, True)
    if new_streak >= warn_at and not evaluate_asked:
        return ("escalate", new_streak, True)
    return ("none", new_streak, evaluate_asked)


def _url_is_local_preview(url) -> bool:
    """True si l'URL pointe une preview loopback ENREGISTRÉE par Lumena.

    S'appuie sur le registre `utils.local_preview` (host loopback + port
    enregistré, jamais l'IP LAN ni l'externe). Tolérant : toute erreur = False.
    """
    if not url:
        return False
    try:
        from urllib.parse import urlparse
        from ..utils.local_preview import is_preview_allowed
        p = urlparse(str(url))
        host = p.hostname
        port = p.port
        if port is None:
            port = 443 if (p.scheme or "").lower() == "https" else 80
        return bool(is_preview_allowed(host, port))
    except Exception:
        return False
