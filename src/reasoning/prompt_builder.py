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
    return bool(re.search(r"[.!?\"')\]\}]$", text.strip()))


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

    video_keywords = [
        "vidéo", "video", "clip", "reel", "tiktok", "short",
        "animation", "motion", "trailer", "intro vidéo", "outro",
        "pub vidéo", "explainer", "motion design",
    ]
    creation_verbs = [
        "cree", "crée", "creer", "créer", "genere", "génère",
        "build", "make", "fais", "produi", "réalis", "realis",
    ]
    return any(k in q for k in video_keywords) and any(v in q for v in creation_verbs)


def looks_code_like_or_structured(text: str) -> bool:
    lowered = text.lower()
    if "```" in text:
        return True
    if any(token in lowered for token in ["<html", "</", "function ", "class ", "import ", "const ", "let ", "def "]):
        return True
    if any(ch in text for ch in ["{", "}", "[", "]"]):
        return True
    return False


def looks_incomplete_final_answer(answer: str, llm_meta: Dict[str, Any]) -> bool:
    trimmed = (answer or "").strip()
    if not trimmed:
        return True

    finish_reason = str(llm_meta.get("finish_reason") or "").strip().lower()
    if is_length_finish_reason(finish_reason):
        return True

    # Long answers are often intentionally detailed in coding tasks.
    if finish_reason in {"stop", "end_turn", "eos", "completed", "complete"} and len(trimmed) >= 2000:
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

    # For explicit non-truncated finish reasons, require very strong evidence
    if finish_reason in {"stop", "end_turn", "eos", "completed", "complete"}:
        if _has_unbalanced or _has_unclosed or trailing_connector:
            return True
        return suspicion_score >= 5

    return suspicion_score >= 2
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the Apache License, Version 2.0
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
