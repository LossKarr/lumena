"""
Normalisation des sorties LLM multi-modèle.

Fonctions pures (pas de classe, pas de state) pour nettoyer et normaliser
les réponses des LLM avant parsing. Utilisé par :
- response_parser.py (pipeline ReAct)
- sub_agent.py (pipeline CodeAgent)
- tool_registry.py (normalisation outil à l'exécution)
"""

import difflib
import json
import re
from typing import Optional


# ── fix_json_text ───────────────────────────────────────────────────

_CODE_FENCE_RE = re.compile(r"^```(?:json|JSON)?\s*\n?(.*?)```\s*$", re.DOTALL)
_TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")


def fix_json_text(text: str) -> str:
    """Nettoie du texte JSON avant parsing.

    - Strip markdown code fences (```json ... ```)
    - Remove trailing commas ({...,})
    """
    if not text:
        return text
    text = text.strip()
    # Strip markdown code fences
    m = _CODE_FENCE_RE.match(text)
    if m:
        text = m.group(1).strip()
    # Remove trailing commas (safe : ne touche pas les virgules dans les strings
    # car le " entre , et } empêche le match)
    text = _TRAILING_COMMA_RE.sub(r"\1", text)
    return text


# ── extract_json_object ────────────────────────────────────────────

def extract_json_object(text: str) -> Optional[dict]:
    """Extraction robuste du premier objet JSON valide depuis du texte LLM.

    Stratégies (dans l'ordre) :
    1. fix_json_text + json.loads direct
    2. Extraction depuis bloc markdown ```json ... ```
    3. Brace-depth avec gestion strings/escapes
    """
    if not text:
        return None
    text = text.strip()

    # 1. Fix + parse direct
    cleaned = fix_json_text(text)
    if cleaned.startswith("{"):
        try:
            result = json.loads(cleaned)
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass

    # 2. Extraction depuis bloc markdown
    fence_match = re.search(r"```(?:json|JSON)?\s*\n?(.*?)```", text, re.DOTALL)
    if fence_match:
        inner = fix_json_text(fence_match.group(1).strip())
        try:
            result = json.loads(inner)
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass

    # 3. Brace-depth avec gestion strings/escapes
    brace = text.find("{")
    if brace >= 0:
        depth = 0
        in_string = False
        escaped = False
        for i in range(brace, len(text)):
            ch = text[i]
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[brace : i + 1]
                    # Appliquer fix trailing comma avant parse
                    candidate = _TRAILING_COMMA_RE.sub(r"\1", candidate)
                    try:
                        result = json.loads(candidate)
                        if isinstance(result, dict):
                            return result
                    except json.JSONDecodeError:
                        break
    return None


# ── normalize_action_name ──────────────────────────────────────────

_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")

_ACTION_ALIAS_MAP: dict[str, str] = {
    # Alias sémantiques observés en production
    "cat": "read_file",
    "show": "read_file",
    "view": "read_file",
    "view_file": "read_file",
    # NB: "open_file" est un handler V2 réel (ouvre un fichier dans l'app) — PAS un alias
    "create_file": "write_file",
    "save_file": "write_file",
    "search": "grep_search",
    "find": "grep_search",
    "grep": "grep_search",
    "exec": "run_command",
    "execute": "run_command",
    "shell": "run_command",
    "bash": "run_command",
    "cmd": "run_command",
    "test": "run_tests",
    "tests": "run_tests",
    "ls": "list_files",
    "dir": "list_files",
    "tree": "list_files",
    "patch": "apply_patch",
    "diff": "apply_patch",
    "modify": "edit_file",
    "update": "edit_file",
    "replace": "edit_file",
    "finish": "done",
    "complete": "done",
    "end": "done",
    "final": "done",
    "plan_steps": "plan",
    "think": "plan",
}


def normalize_action_name(name: str) -> str:
    """Normalise un nom d'action LLM vers snake_case.

    1. Lookup direct dans _ACTION_ALIAS_MAP
    2. Strip préfixes/suffixes connus (tool_, _tool, _action, _handler)
    3. Conversion camelCase → snake_case
    4. Re-lookup après conversion
    5. Retourne le nom original si aucun match
    """
    if not name:
        return name
    lower = name.strip().lower()

    # Lookup direct
    if lower in _ACTION_ALIAS_MAP:
        return _ACTION_ALIAS_MAP[lower]

    # Strip préfixes/suffixes parasites
    stripped = lower
    for prefix in ("tool_",):
        if stripped.startswith(prefix):
            stripped = stripped[len(prefix):]
    for suffix in ("_tool", "_action", "_handler"):
        if stripped.endswith(suffix):
            stripped = stripped[: -len(suffix)]
    if stripped != lower and stripped in _ACTION_ALIAS_MAP:
        return _ACTION_ALIAS_MAP[stripped]

    # camelCase → snake_case
    snake = _CAMEL_RE.sub("_", name.strip()).lower()
    if snake != lower:
        # Vérifier après conversion
        if snake in _ACTION_ALIAS_MAP:
            return _ACTION_ALIAS_MAP[snake]
        return snake

    return name.strip()


# ── normalize_file_path ────────────────────────────────────────────

_ABS_PREFIXES = ("/home/", "/root/", "/tmp/", "c:/users/", "c:\\users\\")


def normalize_file_path(path: str, workspace_root: str = "") -> str:
    """Normalise un chemin fichier LLM.

    - Backslash → forward slash
    - Strip préfixe ./
    - Strip workspace_root dupliqué
    Ne touche PAS aux chemins absolus valides (gérés par _resolve_path en aval).
    """
    if not path:
        return path

    # Backslash → forward slash
    path = path.replace("\\", "/")

    # Strip ./
    if path.startswith("./"):
        path = path[2:]

    # Strip workspace_root dupliqué
    if workspace_root:
        ws = workspace_root.replace("\\", "/").rstrip("/") + "/"
        # Si le path commence par le workspace root en double
        if path.startswith(ws) and path[len(ws):].startswith(ws):
            path = path[len(ws):]

    # Strip leading / seulement si ce n'est PAS un chemin absolu valide
    # (les chemins absolus légitimes sont gérés par _resolve_path en aval)

    return path


# ── auto_fix_action_name ──────────────────────────────────────────

def auto_fix_action_name(name: str, known_tools: set[str]) -> str:
    """Correction automatique d'un nom d'outil si proche d'un outil connu.

    1. Si name est dans known_tools → retour direct
    2. normalize_action_name() → si dans known_tools → retour
    3. difflib fuzzy (cutoff=0.75) → retour si match
    4. Retour name original
    """
    if not name:
        return name
    if name in known_tools:
        return name

    # Essayer la normalisation
    normalized = normalize_action_name(name)
    if normalized in known_tools:
        return normalized

    # Fuzzy match strict (0.75 pour auto-correction, plus strict que 0.5 pour suggestion)
    matches = difflib.get_close_matches(name, list(known_tools), n=1, cutoff=0.75)
    if matches:
        return matches[0]

    # Essayer fuzzy sur le nom normalisé aussi
    if normalized != name:
        matches = difflib.get_close_matches(normalized, list(known_tools), n=1, cutoff=0.75)
        if matches:
            return matches[0]

    return name
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the Apache License, Version 2.0
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
