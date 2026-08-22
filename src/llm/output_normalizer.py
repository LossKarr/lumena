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


# ── DS-1 : format DSML natif de deepseek ────────────────────────────
# (run SkiLoc 2026-07-12) deepseek émet parfois ses tool-calls au format DSML
# en TEXTE brut : `<｜｜DSML｜｜tool_calls>` + `<｜｜DSML｜｜invoke name="X">` +
# `<｜｜DSML｜｜parameter name="p" string="true">v</…>`. Le parser les traitait
# en « THOUGHT halluciné » / « ACTION inline » → nom d'outil récupéré mais
# PARAMÈTRES PERDUS → « Paramètre(s) requis manquant(s) » en boucle (×8 dans le
# run), et du DSML brut streamé dans un FINAL chat. On CONVERTIT au lieu de
# jeter — même doctrine que CA-1 (wrapper args du CodeAgent).
# `｜` = U+FF5C ; tolérance 1-2 barres et barre ASCII.

_DSML_MARK = r"<[｜|]{1,2}DSML[｜|]{1,2}"
_DSML_ANY_RE = re.compile(_DSML_MARK)
_DSML_INVOKE_RE = re.compile(
    _DSML_MARK + r'invoke\s+name="([^"]+)"[^>]*>(.*?)</[｜|]{1,2}DSML[｜|]{1,2}invoke>',
    re.DOTALL,
)
_DSML_PARAM_RE = re.compile(
    _DSML_MARK + r'parameter\s+name="([^"]+)"([^>]*)>(.*?)</[｜|]{1,2}DSML[｜|]{1,2}parameter>',
    re.DOTALL,
)
_DSML_RESIDUAL_RE = re.compile(r"</?[｜|]{1,2}DSML[｜|]{1,2}[^>]*>")


def _dsml_invoke_args(body: str) -> dict:
    """Paramètres d'un invoke DSML. `string="true"` = valeur brute ; sinon JSON
    d'abord (listes/objets/nombres), repli valeur brute."""
    args: dict = {}
    for pm in _DSML_PARAM_RE.finditer(body or ""):
        pname, attrs, pval = pm.group(1).strip(), pm.group(2) or "", pm.group(3).strip()
        if 'string="true"' in attrs:
            args[pname] = pval
        else:
            try:
                args[pname] = json.loads(pval)
            except (json.JSONDecodeError, ValueError):
                args[pname] = pval
    return args


def convert_dsml_tool_calls(text: str) -> str:
    """Convertit les invokes DSML en blocs canoniques `ACTION:`/`ACTION_INPUT:`
    (le multi-invoke retombe sur la mécanique multi-action existante du parser),
    puis retire tout marqueur DSML résiduel. Texte sans DSML → inchangé."""
    if not text or not _DSML_ANY_RE.search(text):
        return text

    def _conv(m: "re.Match") -> str:
        name = m.group(1).strip()
        args = _dsml_invoke_args(m.group(2))
        return (
            "\nACTION: " + name
            + "\nACTION_INPUT: " + json.dumps(args, ensure_ascii=False) + "\n"
        )

    out = _DSML_INVOKE_RE.sub(_conv, text)
    return _DSML_RESIDUAL_RE.sub("", out)


def strip_dsml_markup(text: str) -> str:
    """Retire les blocs/marqueurs DSML d'un texte destiné à l'AFFICHAGE (FINAL) :
    l'utilisateur ne doit jamais voir de DSML brut. Sans DSML → inchangé."""
    if not text or not _DSML_ANY_RE.search(text):
        return text
    out = _DSML_INVOKE_RE.sub("", text)
    return _DSML_RESIDUAL_RE.sub("", out).strip()


def dsml_first_action(text: str) -> Optional[dict]:
    """DS-1.3 (CodeAgent) — premier invoke DSML sous forme d'action dict
    `{"action": name, **params}`, ou None si aucun DSML. Pur."""
    if not text or not _DSML_ANY_RE.search(text):
        return None
    m = _DSML_INVOKE_RE.search(text)
    if not m:
        return None
    action = {"action": m.group(1).strip()}
    action.update(_dsml_invoke_args(m.group(2)))
    return action


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

    # Fix AZ (Phase I-8) : un nom MCP namespacé est un CONTRAT du serveur,
    # pas une typo LLM — windows-mcp expose WaitFor/PowerShell/MultiSelect
    # (PascalCase) et la conversion camelCase→snake_case les rendait
    # introuvables au registry (mcp__windows-mcp__WaitFor → __wait_for).
    # On ne normalise JAMAIS un mcp__*. Les typos restent couvertes par le
    # fuzzy d'auto_fix_action_name (garde same-provider, Fix H).
    if name.strip().startswith("mcp__"):
        return name.strip()

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

def _mcp_provider_prefix(name: str) -> str:
    """Phase I-7 fix H : extrait le préfixe MCP provider (`__` strict).

    Convention MCP : `<server_id>__<tool_name>` avec double underscore.
      - `slack__list_channels` → "slack"
      - `github__create_issue` → "github"
      - `discord_list_channels` → ""  (handler natif, pas MCP)
      - `read_file` → ""

    Le garde-fou cross-provider s'applique UNIQUEMENT entre tools MCP (`__`).
    Les noms avec simple `_` restent libres au fuzzy (typos légitimes type
    `liste_files` → `list_files`).
    """
    if not name or "__" not in name:
        return ""
    return name.split("__", 1)[0]


def _provider_prefix(name: str) -> str:
    """Compat : alias historique conservé pour les tests."""
    return _mcp_provider_prefix(name)


def auto_fix_action_name(name: str, known_tools: set[str]) -> str:
    """Correction automatique d'un nom d'outil si proche d'un outil connu.

    1. Si name est dans known_tools → retour direct
    2. normalize_action_name() → si dans known_tools → retour
    3. difflib fuzzy (cutoff=0.75) → retour si match (avec garde provider)
    4. Retour name original

    Phase I-7 fix H : refuse le fuzzy cross-provider.
    `slack__list_channels` ne doit JAMAIS être auto-corrigé vers
    `discord_list_channels` même si le score difflib > 0.75.
    Sinon risque sécurité : message posté sur le mauvais provider.
    """
    if not name:
        return name
    if name in known_tools:
        return name

    # Essayer la normalisation
    normalized = normalize_action_name(name)
    if normalized in known_tools:
        return normalized

    requested_mcp_prefix = _mcp_provider_prefix(name)

    def _same_provider(candidate: str) -> bool:
        # Si name n'est PAS un format MCP (`__`), pas de contrainte
        # → typos type `liste_files` → `list_files` autorisés.
        if not requested_mcp_prefix:
            return True
        # name est format MCP : le candidate doit être MCP avec le même server_id.
        # Refuse explicitement le glissement MCP → handler natif (slack__* → discord_*).
        return _mcp_provider_prefix(candidate) == requested_mcp_prefix

    # Fuzzy match strict (0.75 pour auto-correction, plus strict que 0.5 pour suggestion)
    matches = difflib.get_close_matches(name, list(known_tools), n=5, cutoff=0.75)
    for cand in matches:
        if _same_provider(cand):
            return cand

    # Essayer fuzzy sur le nom normalisé aussi
    if normalized != name:
        matches = difflib.get_close_matches(normalized, list(known_tools), n=5, cutoff=0.75)
        for cand in matches:
            if _same_provider(cand):
                return cand

    return name
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
