"""
classify_intent() — Classifieur déterministe d'intention (Phase 3).

4 modes :
- CHAT        : conversation, question simple, salutations, opinions
- TOOL_DIRECT : action 1 outil (mail, fichier, spotify, chrome...)
- PROJECT     : "crée un site / app / projet" → pipeline project.py direct
- REACT       : raisonnement multi-étapes, recherche web complexe, reste
"""
from __future__ import annotations

import re
from enum import Enum
from typing import Optional


class RequestMode(str, Enum):
    CHAT = "chat"
    TOOL_DIRECT = "tool_direct"
    PROJECT = "project"
    REACT = "react"


# ── Patterns CHAT ────────────────────────────────────────────────────────────
_CHAT_SHORT_MAX_WORDS = 12
_CHAT_GREET = re.compile(
    r"^(bonjour|salut|hello|coucou|hey|bonsoir|bonne\s+(nuit|journée|soirée)|"
    r"hi|good\s+(morning|evening|night)|merci|thanks?|parfait|ok|"
    r"d'accord|c'est\s+bon|cool|super|génial|bravo|gg|nickel)[\s!.?]*$",
    re.IGNORECASE,
)
_CHAT_OPINION = re.compile(
    r"^(tu\s+(penses?|crois?|trouves?|aimes?|préfères?)|à\s+ton\s+avis|"
    r"qu'est[- ]ce\s+que\s+tu|c'est\s+quoi\s+(ton|ta)|quel\s+est\s+ton|"
    r"tu\s+(es|sais|peux)\s+me\s+(dire|expliquer|résumer))",
    re.IGNORECASE,
)
_CHAT_META = re.compile(
    r"^(qui\s+es[- ]tu|comment\s+(tu\s+t'appelles?|ça\s+va)|quel\s+est\s+ton\s+nom|"
    r"présente[- ]toi|parle[- ]moi\s+de\s+toi|c'est\s+quoi\s+lumena|"
    r"tu\s+(fais|peux faire)\s+quoi|tes\s+(capacités?|fonctions?|skills?))",
    re.IGNORECASE,
)

# ── Patterns PROJECT ──────────────────────────────────────────────────────────
_PROJECT_KW = re.compile(
    r"\b(crée|créer|génère|générer|développe|développer|construire|construis|"
    r"fais[- ]?moi|build|make|generate|scaffold)\b.{0,60}"
    r"\b(site\s*(web)?|application|app|projet|portfolio|landing\s*(page)?|"
    r"dashboard|api|backend|frontend|webapp|website)\b",
    re.IGNORECASE,
)
_PROJECT_WEB_DIRECT = re.compile(
    r"\b(site\s*web|landing\s*page|portfolio|webapp|website)\b",
    re.IGNORECASE,
)

# ── Patterns TOOL_DIRECT ─────────────────────────────────────────────────────
_TOOL_MAIL = re.compile(
    r"\b(envoi[es]?|envoyer|rédige|écris|forward|transfère|transférer)\b.{0,50}\b(mail|email|e-mail|courriel|message)\b",
    re.IGNORECASE,
)
_TOOL_FILE = re.compile(
    r"\b(lis|lire|ouvre|ouvrir|affiche|afficher|montre|"
    r"édite|modifier|supprime|efface|déplace|copie)\b.{0,60}\b(fichier|dossier|file|folder|répertoire)\b",
    re.IGNORECASE,
)
_TOOL_BROWSER = re.compile(
    r"\b(ouvre|lance|démarre|navigue|va\s+sur)\b.{0,50}"
    r"\b(chrome|firefox|navigateur|browser|https?://|www\.)\b",
    re.IGNORECASE,
)
_TOOL_SPOTIFY = re.compile(
    r"\b(joue|lance|mets?|pause|stop|suivant|prochain|"
    r"play|skip|next|volume)\b.{0,50}"
    r"\b(spotify|musique|chanson|titre?|track|playlist|son|audio)\b",
    re.IGNORECASE,
)
_TOOL_SCREENSHOT = re.compile(
    r"\b(prends?|take|fais?\s+une?|capture)\b.{0,30}"
    r"\b(screenshot|capture\s*d'écran|photo\s*d'écran)\b",
    re.IGNORECASE,
)
_TOOL_CLOCK = re.compile(
    r"^(quelle\s+(heure|date)(\s+est[- ]il)?|il\s+est\s+quelle\s+heure|"
    r"c'est\s+quoi\s+(l'heure|la\s+date)|"
    r"what\s*time|what('s|\s+is)\s+the\s+(time|date))",
    re.IGNORECASE,
)
_TOOL_CALC = re.compile(
    r"^(calcule|combien\s+(fait|vaut|donne)|"
    r"c'est\s+quoi\s+\d|\d[\d\s+\-*/^%()]+[=?]?)$",
    re.IGNORECASE,
)

# ── Patterns REACT ────────────────────────────────────────────────────────────
_REACT_SEARCH = re.compile(
    r"\b(cherche|recherche|trouve|googl|ddg|duckduckgo|"
    r"navigue|scrape|extrait?|analyse|compare|résume|synthétise|"
    r"search|find|lookup|browse|crawl)\b",
    re.IGNORECASE,
)
_REACT_MULTI = re.compile(
    r"\b(puis|ensuite|après|et\s+puis|et\s+ensuite|"
    r"d'abord|premièrement|deuxièmement|step\s+\d|étape\s+\d|"
    r"also|then|additionally|step[\s-]by[\s-]step)\b",
    re.IGNORECASE,
)
_REACT_CODE = re.compile(
    r"\b(écris|génère|code|programme|implémente|debug|fixe|"
    r"corrige|refactor|ajoute\s+une?\s+function|write\s+code|"
    r"implement|optimize|refactor)\b",
    re.IGNORECASE,
)

# Création d'artefact livrable (rapport, document, pdf, note, script, etc.)
# Verbes tolérants aux fautes courantes (peut/peux, crée/creer, rédige/redige)
_CREATE_ARTIFACT = re.compile(
    r"\b(cr[ée]+[erz]?|fais[\s-]?moi|r[ée]dige[rz]?|écri[s|rez]?|ecri[s|rez]?|"
    r"g[ée]n[èe]re[rz]?|produis|prépare[rz]?|prepare[rz]?|"
    r"make|write|draft|create|build)\b.{0,40}"
    r"\b(rapp?ort|rapp?rot|document|doc|pdf|docx|xlsx|pptx|csv|"
    r"note|lettre|mail|email|courriel|r[ée]sum[ée]|synth[èe]se|"
    r"compte[\s-]?rendu|brief|m[ée]mo|memo|script|fichier|texte|"
    r"article|post|tweet|facture|invoice|template|mod[èe]le)\b",
    re.IGNORECASE,
)


def classify_intent(query: str, runtime_ctx: Optional[object] = None) -> RequestMode:
    """
    Classifie la requête sans appel LLM : regex + heuristiques.

    Returns:
        RequestMode enum value
    """
    if not query or not query.strip():
        return RequestMode.CHAT

    text = query.strip()
    lower = text.lower()
    words = lower.split()
    n_words = len(words)

    # ── CHAT : salutations, meta, opinions ────────────────────────────────────
    if _CHAT_GREET.match(text):
        return RequestMode.CHAT
    if _CHAT_META.match(text):
        return RequestMode.CHAT
    if _CHAT_OPINION.match(lower):
        return RequestMode.CHAT
    # Phrases courtes sans verbe d'action → CHAT
    # Mais si un pattern TOOL_DIRECT matche, on laisse passer
    _has_tool_signal = (
        _TOOL_MAIL.search(text)
        or _TOOL_FILE.search(text)
        or _TOOL_BROWSER.search(text)
        or _TOOL_SPOTIFY.search(text)
        or _TOOL_SCREENSHOT.search(text)
        or _TOOL_CLOCK.match(text)
        or _TOOL_CALC.match(text)
    )
    # Détection rapide : si la phrase contient un mot d'action mail/envoi, ne pas router en CHAT
    _has_send_keyword = bool(re.search(r"\b(envoi|envoie|envoyer|mail|email|e-mail|telegram|whatsapp)\b", lower))

    # Si le source_channel est "telegram" en mode "agent", les phrases courtes
    # qui font référence à un résultat précédent ne doivent pas être routées en CHAT
    _is_agent_channel = False
    if runtime_ctx is not None:
        _src = getattr(runtime_ctx, "source_channel", "") or ""
        _mode = getattr(runtime_ctx, "mode", "") or ""
        if _src in ("telegram", "whatsapp") and _mode == "agent":
            _is_agent_channel = True

    if n_words <= _CHAT_SHORT_MAX_WORDS and not _REACT_SEARCH.search(text) and not _REACT_CODE.search(text) and not _CREATE_ARTIFACT.search(text):
        if not _PROJECT_KW.search(text) and not _has_tool_signal and not _has_send_keyword:
            if not _REACT_MULTI.search(text):
                # En mode agent Telegram, les phrases référençant un résultat précédent
                # doivent passer en REACT (ex: "je n'ai rien reçu", "ça marche pas")
                if _is_agent_channel and re.search(
                    r"\b(reçu|recois|marche|fonctionne|bug|erreur|plante|envoy|recu)\b", lower
                ):
                    pass  # ne pas router en CHAT
                else:
                    return RequestMode.CHAT

    # ── PROJECT : création de sites/apps ─────────────────────────────────────
    if _PROJECT_KW.search(text):
        return RequestMode.PROJECT
    if _PROJECT_WEB_DIRECT.search(text) and n_words <= 30:
        return RequestMode.PROJECT

    # ── TOOL_DIRECT : actions 1-outil ────────────────────────────────────────
    if _TOOL_CLOCK.match(text):
        return RequestMode.TOOL_DIRECT
    if _TOOL_CALC.match(text):
        return RequestMode.TOOL_DIRECT
    if _TOOL_MAIL.search(text) and not _REACT_MULTI.search(text):
        return RequestMode.TOOL_DIRECT
    if _TOOL_FILE.search(text) and not _REACT_MULTI.search(text):
        return RequestMode.TOOL_DIRECT
    if _TOOL_BROWSER.search(text):
        return RequestMode.TOOL_DIRECT
    if _TOOL_SPOTIFY.search(text):
        return RequestMode.TOOL_DIRECT
    if _TOOL_SCREENSHOT.search(text):
        return RequestMode.TOOL_DIRECT

    # ── Création d'artefact (rapport, document, pdf…) → REACT ─────────────────
    # Laisse la boucle ReAct choisir l'outil (create_pdf, write_file, etc.)
    # et permet au classifieur LLM hybride d'escalader si besoin vers CodeAgent.
    if _CREATE_ARTIFACT.search(text):
        return RequestMode.REACT

    # ── REACT : tout le reste ─────────────────────────────────────────────────
    return RequestMode.REACT
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the Apache License, Version 2.0
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
