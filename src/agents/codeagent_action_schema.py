"""
Contrat minimal des actions CodeAgent.

Ce module garde le LLM dans un protocole stable sans alourdir `sub_agent.py`.
Il ne remplace pas les outils: il normalise et refuse les payloads impossibles
avant execution, avec un message court que le modele peut corriger au tour suivant.
"""
from __future__ import annotations

from dataclasses import dataclass
import difflib
from typing import Any


ACTION_ALIASES = {
    "edit": "edit_lines",
    "replace": "str_replace",
    "search": "grep",
    "bash": "run_command",
    "shell": "run_command",
    "test": "run_tests",
    "finish": "done",
    "final": "done",
}

# CA-1 (run démineur 2026-07-12) — le LLM émet parfois la forme ReAct
# `{"action": "str_replace", "args": {"path": …, "old_str": …, "new_str": …}}` :
# le JSON parse, l'action est connue, mais TOUS les champs sont « manquants » au
# niveau racine → boucle de refus infinie (le message de correction ne disait pas
# de désimbriquer). Le registre ReAct dépaquette EXACTEMENT ces wrappers
# (tool_registry._WRAPPER_KEYS) — même invariant ici. La racine gagne toujours.
WRAPPER_KEYS = ("args", "arguments", "params", "parameters", "input", "payload", "data")

FIELD_ALIASES = {
    "apply_patch": {"patchText": "patch", "patch_text": "patch"},
    "edit_file": {"old_str": "search", "new_str": "replace", "oldString": "search", "newString": "replace"},
    "str_replace": {"oldString": "old_str", "newString": "new_str", "search": "old_str", "replace": "new_str"},
    "read_files_batch": {"file_paths": "paths", "files": "paths"},
    "run_tests": {"path": "test_path", "command": "test_path"},
    "done": {"message": "summary", "result": "summary"},
}

REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "plan": (),
    "think": (),
    "read_file": ("path",),
    "read_files_batch": ("paths",),
    "list_files": (),
    "write_file": ("path", "content"),
    "edit_file": ("path", "search", "replace"),
    "edit_lines": ("path", "start_line", "end_line", "content"),
    "str_replace": ("path", "old_str", "new_str"),
    "insert_at_anchor": ("path", "anchor", "content"),
    "apply_patch": ("patch",),
    "apply_patches": ("patches",),
    "undo_edit": ("path",),
    "run_command": ("command",),
    "run_tests": (),
    "grep": ("pattern", "path"),
    "lint": ("path",),
    "done": ("summary",),
}

LIST_FIELDS = {
    "read_files_batch": ("paths",),
    "apply_patches": ("patches",),
}

INT_FIELDS = {
    "read_file": ("start_line", "end_line"),
    "edit_lines": ("start_line", "end_line"),
    "read_files_batch": ("start_line", "end_line", "max_chars_per_file"),
}


@dataclass(frozen=True)
class ActionValidation:
    valid: bool
    action: dict[str, Any] | None = None
    message: str = ""


def normalize_codeagent_action(action: dict[str, Any]) -> dict[str, Any]:
    """Retourne une copie normalisee sans modifier l'objet source."""
    normalized = dict(action)

    if "action" not in normalized:
        for alias in ("tool", "name", "function", "tool_name", "command_name"):
            value = normalized.get(alias)
            if isinstance(value, str) and value.strip():
                normalized["action"] = value.strip()
                break

    raw_action = str(normalized.get("action", "") or "").strip()
    if raw_action:
        normalized["action"] = ACTION_ALIASES.get(raw_action, raw_action)

    # CA-1 — dépaqueter les champs imbriqués sous un wrapper (forme ReAct).
    # Fusion NON destructive : un champ déjà présent à la racine n'est jamais
    # écrasé par le wrapper. Le wrapper reste en place (inoffensif).
    for _wk in WRAPPER_KEYS:
        _inner = normalized.get(_wk)
        if isinstance(_inner, dict) and _inner:
            for _k, _v in _inner.items():
                if _k not in normalized:
                    normalized[_k] = _v

    action_type = str(normalized.get("action", "") or "")
    for source, target in FIELD_ALIASES.get(action_type, {}).items():
        if source in normalized and target not in normalized:
            normalized[target] = normalized[source]

    return normalized


def validate_codeagent_action(action: Any) -> ActionValidation:
    if not isinstance(action, dict):
        return ActionValidation(False, None, "Action JSON invalide: l'objet racine doit etre un dictionnaire.")

    normalized = normalize_codeagent_action(action)
    action_type = str(normalized.get("action", "") or "").strip()
    if not action_type:
        return ActionValidation(
            False,
            normalized,
            "Action JSON invalide: champ `action` manquant. Exemple: {\"action\":\"read_file\",\"path\":\"src/app.py\"}",
        )

    if action_type not in REQUIRED_FIELDS:
        suggestion = difflib.get_close_matches(action_type, REQUIRED_FIELDS.keys(), n=1, cutoff=0.55)
        suffix = f" Voulais-tu dire `{suggestion[0]}` ?" if suggestion else ""
        return ActionValidation(False, normalized, f"Action inconnue: `{action_type}`.{suffix}")

    missing = [field for field in REQUIRED_FIELDS[action_type] if _is_empty(normalized.get(field))]
    if missing:
        expected = _schema_hint(action_type)
        return ActionValidation(
            False,
            normalized,
            f"Action `{action_type}` invalide: champ(s) requis manquant(s): {', '.join(missing)}. Schema attendu: {expected}",
        )

    for field in LIST_FIELDS.get(action_type, ()):
        if field in normalized and not isinstance(normalized[field], list):
            return ActionValidation(False, normalized, f"Action `{action_type}` invalide: `{field}` doit etre une liste.")

    for field in INT_FIELDS.get(action_type, ()):
        if field in normalized and normalized[field] is not None:
            try:
                normalized[field] = int(normalized[field])
            except (TypeError, ValueError):
                return ActionValidation(False, normalized, f"Action `{action_type}` invalide: `{field}` doit etre un entier.")

    if action_type == "edit_lines":
        if int(normalized["start_line"]) <= 0 or int(normalized["end_line"]) <= 0:
            return ActionValidation(False, normalized, "Action `edit_lines` invalide: start_line/end_line doivent etre >= 1.")
        if int(normalized["end_line"]) < int(normalized["start_line"]):
            return ActionValidation(False, normalized, "Action `edit_lines` invalide: end_line doit etre >= start_line.")

    if action_type == "apply_patches":
        patches = normalized.get("patches") or []
        if not all(isinstance(item, dict) for item in patches):
            return ActionValidation(False, normalized, "Action `apply_patches` invalide: chaque patch doit etre un objet.")
        bad = [
            i for i, item in enumerate(patches, start=1)
            if _is_empty(item.get("file") or item.get("path")) or _is_empty(item.get("old") or item.get("old_str"))
        ]
        if bad:
            return ActionValidation(False, normalized, f"Action `apply_patches` invalide: patch(s) sans file/old: {bad[:5]}.")

    return ActionValidation(True, normalized, "")


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value == ""
    if isinstance(value, (list, tuple, dict)):
        return len(value) == 0
    return False


def _schema_hint(action_type: str) -> str:
    fields = REQUIRED_FIELDS[action_type]
    payload = {"action": action_type}
    for field in fields:
        payload[field] = "..."
    return str(payload).replace("'", '"')


__all__ = ["ActionValidation", "normalize_codeagent_action", "validate_codeagent_action"]
