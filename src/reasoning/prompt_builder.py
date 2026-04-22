"""
Prompt builder — Heuristiques statiques pour le prompt ReAct.

Fonctions pures de détection / classification de requêtes et de réponses.
Extraites de react.py pour améliorer la lisibilité.
"""

from typing import Dict, Any, Optional
import re


def is_length_finish_reason(finish_reason: Optional[str]) -> bool:
    if not finish_reason:
        return False
    value = str(finish_reason).strip().lower()
    return value in {"length", "max_tokens", "max_output_tokens", "max_tokens_exceeded"}


def has_unbalanced_delimiters(text: str) -> bool:
    checks = [("(", ")"), ("{", "}"), ("[", "]")]
    for opening, closing in checks:
        if text.count(opening) != text.count(closing):
            return True
    return False


def has_unclosed_quotes(text: str) -> bool:
    if text.count('"""') % 2 != 0 or text.count("'''") % 2 != 0:
        return True

    double_quotes = len(re.findall(r'(?<!\\)"', text))
    if double_quotes % 2 != 0:
        return True

    single_quotes = len(re.findall(r"(?<!\\)'", text))
    if single_quotes % 2 != 0 and ("```" in text or "\n" in text):
        return True

    return False


def ends_with_strong_punctuation(text: str) -> bool:
    if not text:
        return False
    stripped = text.strip()
    if not stripped:
        return False
    # Emojis et symboles Unicode (> U+2000) comptent comme ponctuation forte
    if ord(stripped[-1]) > 0x2000:
        return True
    return bool(re.search(r"[.!?\"')\]\}]$", stripped))


def is_exploratory_tool(tool_name: str) -> bool:
    return tool_name in {
        "list_directory", "get_time", "find_files",
    }


def is_single_file_creation_request(query: str) -> bool:
    q = (query or "").lower()
    creation_verbs = [
        "creer", "créer", "genere", "génère", "fais", "fabrique",
        "build", "make", "write", "code",
    ]
    file_markers = [".py", "python", "fichier", "file", "script", "workspace"]
    web_markers = [".html", ".css", ".js", "landing", "site web", "page web", "portfolio"]
    asks_creation = any(v in q for v in creation_verbs)
    mentions_file = any(m in q for m in file_markers)
    is_web_project = any(m in q for m in web_markers)
    return asks_creation and mentions_file and not is_web_project


def is_project_creation_request(query: str) -> bool:
    """Detecte une demande de creation/structuration de projet complet."""
    q = (query or "").lower()
    if not q:
        return False

    creation_verbs = [
        "cree", "cree", "creer", "creer", "genere", "genere",
        "build", "make", "developpe", "developpe", "construis",
        "lance", "demarre", "start",
    ]
    project_markers = [
        "projet", "project", "application", "app", "site web", "website",
        "api", "bot", "saas", "frontend", "backend", "fullstack",
        "codebase", "workspace",
    ]
    has_creation = any(v in q for v in creation_verbs)
    has_project_scope = any(m in q for m in project_markers)
    return has_creation and has_project_scope


def is_web_request(query: str) -> bool:
    """Detect explicit web project requests (not generic web search)."""
    q = (query or "").lower()
    if not q:
        return False

    explicit_web_files = [
        "index.html", "style.css", "script.js",
        ".html", ".css", ".js",
    ]
    if any(marker in q for marker in explicit_web_files):
        return True

    web_keywords = [
        "site web", "page web", "landing", "portfolio",
        "frontend", "front-end", "html", "css", "javascript", "js",
    ]
    web_creation_verbs = [
        "cree", "crée", "creer", "créer", "genere", "génère",
        "build", "make", "developpe", "développe", "refais", "ameliore", "améliore",
    ]
    return any(k in q for k in web_keywords) and any(v in q for v in web_creation_verbs)


def is_video_request(query: str) -> bool:
    """Detect explicit video generation requests."""
    q = (query or "").lower()
    if not q:
        return False

    # Mots-clés qui disqualifient "animation" comme vidéo (contexte CSS/web)
    web_context = ["css", "html", "site", "web", "page", "scroll", "hover", "transition"]
    has_web_context = any(w in q for w in web_context)

    video_keywords = [
        "vidéo", "video", "clip", "reel", "tiktok", "short",
        "motion design", "trailer", "intro vidéo", "outro",
        "pub vidéo", "explainer",
    ]
    # "animation" et "motion" ne comptent comme vidéo que hors contexte CSS/web
    if not has_web_context:
        video_keywords.extend(["animation", "motion"])
    creation_verbs = [
        "cree", "crée", "creer", "créer", "genere", "génère",
        "build", "make", "fais", "faire", "fait", "produi", "réalis", "realis",
        # Verbes d'édition vidéo → doivent aussi rester dans ReAct (handler edit_video)
        "modifie", "modifier", "edite", "édite", "editer", "éditer",
        "change", "changer", "retouche", "retoucher", "améliore", "ameliore",
        "mets à jour", "met à jour", "update",
    ]
    # Word-boundary matching pour éviter les faux positifs
    # ex: "reel" dans "reellement", "short" dans "shorts", "clip" dans "cliparts".
    import re as _re
    def _has_word(terms: list[str], text: str) -> bool:
        for t in terms:
            # Les expressions multi-mots (ex: "motion design") gardent le simple "in"
            if " " in t:
                if t in text:
                    return True
                continue
            if _re.search(r"(?<![a-zà-ÿ0-9])" + _re.escape(t) + r"(?![a-zà-ÿ0-9])", text):
                return True
        return False
    return _has_word(video_keywords, q) and _has_word(creation_verbs, q)


def looks_code_like_or_structured(text: str) -> bool:
    lowered = text.lower()
    if "```" in text:
        return True
    if any(token in lowered for token in ["<html", "</", "function ", "class ", "import ", "const ", "let ", "def "]):
        return True
    if any(ch in text for ch in ["{", "}", "[", "]"]):
        return True
    return False


# Détecte les "fausses promesses" : le LLM dit FINAL mais promet une action future
# qu'il n'a pas encore exécutée ("je vais faire", "donne-moi quelques secondes", etc.)
_RE_FALSE_PROMISE = re.compile(
    r"\b("
    r"donne[- ]moi\s+(quelques?\s+)?(secondes?|instants?|moments?)"
    r"|laisse[- ]moi\s+(faire|chercher|regarder|vérifier|trouver)"
    r"|je\s+vais\s+(faire|chercher|effectuer|lancer|démarrer|commencer|rechercher)"
    r"|je\s+vais\s+maintenant"
    r"|let\s+me\s+(search|check|look|find|do)"
    r"|give\s+me\s+(a\s+)?(moment|second|sec)"
    r")",
    re.IGNORECASE,
)


def looks_incomplete_final_answer(answer: str, llm_meta: Dict[str, Any]) -> bool:
    trimmed = (answer or "").strip()
    if not trimmed:
        return True

    # Fausse promesse : le LLM s'engage à faire quelque chose qu'il n'a pas fait
    # (ex: "Donne-moi quelques secondes..." alors qu'aucun outil n'a été appelé)
    if _RE_FALSE_PROMISE.search(trimmed):
        return True

    finish_reason = str(llm_meta.get("finish_reason") or "").strip().lower()
    if is_length_finish_reason(finish_reason):
        return True

    # Long answers are often intentionally detailed in coding tasks.
    # Mais on doit quand même vérifier les délimiteurs non-fermés (code tronqué).
    if finish_reason in {"stop", "end_turn", "eos", "completed", "complete"} and len(trimmed) >= 2000:
        _structured_long = looks_code_like_or_structured(trimmed)
        if _structured_long and (has_unbalanced_delimiters(trimmed) or has_unclosed_quotes(trimmed)):
            return True
        return False

    if len(trimmed) < 30:
        return False

    structured_output = looks_code_like_or_structured(trimmed)
    ends_strong = ends_with_strong_punctuation(trimmed)
    _has_unbalanced = has_unbalanced_delimiters(trimmed) if structured_output else False
    _has_unclosed = has_unclosed_quotes(trimmed) if structured_output else False
    trailing_connector = bool(
        re.search(
            r"\b(et|ou|mais|donc|car|avec|pour|puis|ensuite|and|or|but|so|because|with|for)$",
            trimmed,
            re.IGNORECASE,
        )
    )
    suspicion_score = 0

    if _has_unbalanced:
        suspicion_score += 3

    if _has_unclosed:
        suspicion_score += 3

    if re.search(r"[,;:\-/\\(]$", trimmed):
        suspicion_score += 1

    if trailing_connector and not ends_strong:
        suspicion_score += 2
    elif not ends_strong and len(trimmed) >= 220:
        suspicion_score += 1

    if structured_output and not ends_strong and len(trimmed) >= 160:
        suspicion_score += 1

    # Pour les finish_reason explicitement "terminé", n'accepter une troncature
    # que si des problèmes structurels réels sont détectés (délimiteurs non-fermés,
    # connecteur en fin, etc.). Ignorer la longueur : "stop" signifie que le LLM
    # a décidé de s'arrêter, quelle que soit la taille de la réponse.
    if finish_reason in {"stop", "end_turn", "eos", "completed", "complete"}:
        if not _has_unbalanced and not _has_unclosed and not trailing_connector:
            return False  # stop sans problème structurel → réponse complète
        if _has_unbalanced or _has_unclosed or trailing_connector:
            return True
        return suspicion_score >= 5

    return suspicion_score >= 2
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU General Public License v3.0 (GPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
