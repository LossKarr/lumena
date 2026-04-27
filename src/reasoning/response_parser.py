"""
Response parser — Fonctions de parsing des réponses LLM ReAct.

Extrait de react.py pour améliorer la lisibilité et la maintenabilité.
Les fonctions sont pures (pas de self) sauf parse_response qui retourne
un tuple au lieu d'écrire sur self.
"""

from typing import Dict, Any, List, Optional, Tuple
from loguru import logger
import json
import re

from .react_config import (
    ActionType, Thought, Action, Observation, TaskItem,
    _PLAN_RE, _TASK_LINE_RE,
)

# Compteur global ACTION inline — monitoring P4 (reset par processus, pas par requête)
_action_inline_total: list = [0]


def extract_balanced_json(text: str, start_index: int) -> Optional[tuple[str, int]]:
    """Extract a balanced JSON object/array starting at index."""
    i = start_index
    while i < len(text) and text[i].isspace():
        i += 1
    if i >= len(text) or text[i] not in "{[":
        return None

    opening = text[i]
    closing = "}" if opening == "{" else "]"
    depth = 0
    in_string = False
    escaped = False

    for j in range(i, len(text)):
        ch = text[j]

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
        elif ch == opening:
            depth += 1
        elif ch == closing:
            depth -= 1
            if depth == 0:
                return text[i : j + 1], j + 1

    return None


def extract_action_input(response: str, action_start: int, action_name: str) -> str:
    """Extract ACTION_INPUT linked to the selected ACTION block."""
    tail = response[action_start:]
    input_match = re.search(r"(?im)^\s*ACTION_INPUT:\s*", tail)
    # Fallback: ACTION_INPUT inline (Kimi écrit parfois tout sur la même ligne)
    if not input_match:
        input_match = re.search(r"(?i)ACTION_INPUT:\s*", tail)
    if not input_match:
        return ""

    start = action_start + input_match.end()
    while start < len(response) and response[start].isspace():
        start += 1

    if action_name.upper() == "FINAL":
        return response[start:].strip()

    extracted = extract_balanced_json(response, start)
    if extracted:
        return extracted[0].strip()

    next_label = re.search(r"(?im)^\s*(THOUGHT|ACTION|OBSERVATION):", response[start:])
    if next_label:
        end = start + next_label.start()
        return response[start:end].strip()
    return response[start:].strip()


def parse_action_args(action_input: str) -> Dict[str, Any]:
    """Parse tool arguments from ACTION_INPUT with robust fallbacks."""
    if not action_input:
        return {}

    from src.llm.output_normalizer import fix_json_text
    cleaned = fix_json_text(action_input.strip())
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass  # try code block extraction

    extracted = extract_balanced_json(cleaned, 0)
    if extracted:
        try:
            parsed = json.loads(extracted[0])
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass  # try brace extraction

    args: Dict[str, Any] = {}
    path_match = re.search(r'["\']?path["\']?\s*[=:]\s*["\']([^"\']+)["\']', cleaned, re.IGNORECASE)
    if path_match:
        args["path"] = path_match.group(1)

    content_match = re.search(r'["\']?content["\']?\s*[=:]\s*(.+)$', cleaned, re.DOTALL | re.IGNORECASE)
    if content_match:
        content = content_match.group(1).strip()
        if (content.startswith('"') and content.endswith('"')) or (
            content.startswith("'") and content.endswith("'")
        ):
            content = content[1:-1]
        content = content.replace("\\n", "\n").replace("\\t", "\t")
        content = content.replace('\\"', '"').replace("\\'", "'").replace("\\\\", "\\")
        args["content"] = content

    if args:
        return args

    file_match = re.search(r'([\w\-/\\.]+\.(html|css|js|py|md|txt|json))', cleaned, re.IGNORECASE)
    if file_match:
        inferred: Dict[str, Any] = {"path": file_match.group(1)}
        rest = cleaned.replace(file_match.group(1), "", 1).strip()
        if rest:
            inferred["content"] = rest
        return inferred

    return {"input": cleaned}


def parse_response(response: str) -> Tuple[Thought, Action, bool, list]:
    """Parse la réponse du LLM en Thought et Action.

    Retourne (thought, action, halluc_flag, pending_multi_actions).
    - halluc_flag: True si le THOUGHT contenait des blocs ACTION/OBSERVATION hallucinés
    - pending_multi_actions: liste de (name, args) supplémentaires si multi-action
    """
    # Retirer le bloc PLAN avant parsing (evite interference regex)
    cleaned_response = _PLAN_RE.sub("", response)
    # ── Strip fausses OBSERVATION: hallucilées par le LLM (seul le système en génère)
    _obs_pattern = re.compile(r"(?im)^\s*OBSERVATION:\s*.*?(?=^\s*(?:THOUGHT|ACTION):|\Z)", re.DOTALL | re.MULTILINE)
    _obs_count = len(_obs_pattern.findall(cleaned_response))
    if _obs_count:
        cleaned_response = _obs_pattern.sub("", cleaned_response)
        logger.warning("⚠️ {} fausse(s) OBSERVATION: hallucilée(s) supprimée(s) de la réponse LLM", _obs_count)
    thought_matches = list(
        re.finditer(
            r"(?is)^\s*THOUGHT:\s*(.+?)(?=^\s*(?:ACTION|THOUGHT):|\Z)",
            cleaned_response,
            re.MULTILINE,
        )
    )
    thought_content = thought_matches[-1].group(1).strip() if thought_matches else ""

    # ── Nettoyer le THOUGHT des blocs ACTION:/OBSERVATION: que Kimi y injecte parfois
    halluc_flag = False
    _first_halluc = re.search(
        r"\b(?:ACTION|OBSERVATION|ACTION_INPUT)\s*:",
        thought_content,
        re.IGNORECASE,
    )
    if _first_halluc and _first_halluc.start() > 20:
        _thought_before = thought_content[:_first_halluc.start()].strip()
        if _thought_before:
            _halluc_removed = len(thought_content) - len(_thought_before)
            if _halluc_removed > 100:
                logger.warning(
                    "⚠️ THOUGHT halluciné nettoyé: {} chars de contenu simulé supprimés",
                    _halluc_removed,
                )
                halluc_flag = True
            thought_content = _thought_before

    action_matches = list(re.finditer(r"(?im)^\s*ACTION:\s*([A-Za-z_][A-Za-z0-9_]*)", cleaned_response))
    # ── Fallback: détecter ACTION: inline (Kimi écrit parfois THOUGHT + ACTION sur la même ligne)
    if not action_matches:
        inline_m = re.search(r"(?i)\bACTION:\s*([A-Za-z_][A-Za-z0-9_]*)", cleaned_response)
        if inline_m:
            action_matches = [inline_m]
            # Compter les occurrences ACTION inline pour le monitoring P4
            _action_inline_total[0] += 1
            logger.warning(
                "⚠️ ACTION inline détecté (#{}, pas en début de ligne) — extraction forcée: {}",
                _action_inline_total[0], inline_m.group(1).strip(),
            )
    # ── Multi-action: exécuter toutes les actions séquentiellement ──
    _sel = None
    pending_multi_actions: list = []
    if action_matches:
        # Filtrer les actions réelles (ni FINAL ni CLARIFY)
        _real_actions = [m for m in action_matches if m.group(1).strip().upper() not in ("FINAL", "CLARIFY")]
        _has_final = any(m.group(1).strip().upper() == "FINAL" for m in action_matches)

        if len(_real_actions) > 1:
            # Multi-action détecté: prendre la première, queuer les suivantes
            _sel = _real_actions[0]
            for _extra in _real_actions[1:]:
                _extra_name = _extra.group(1).strip()
                _extra_input = extract_action_input(cleaned_response, _extra.start(), _extra_name)
                _extra_args = parse_action_args(_extra_input)
                pending_multi_actions.append((_extra_name, _extra_args))
            logger.warning(
                "⚠️ MULTI-ACTION: {} ACTION: détectés — exécution séquentielle ({} + {} en queue)",
                len(action_matches), _sel.group(1).strip(), len(pending_multi_actions),
            )
        elif _real_actions:
            _sel = _real_actions[0]
        elif action_matches:
            _sel = action_matches[-1]  # Seulement FINAL/CLARIFY
    action_name = _sel.group(1).strip() if _sel else ""
    # Normaliser le nom d'action (camelCase→snake_case, alias courants)
    # NE PAS normaliser FINAL et CLARIFY (actions système ReAct)
    if action_name and action_name.upper() not in ("FINAL", "CLARIFY"):
        from src.llm.output_normalizer import normalize_action_name
        action_name = normalize_action_name(action_name)
    action_input = extract_action_input(cleaned_response, _sel.start(), action_name) if _sel else ""

    # Si multi-action détecté, prendre la première pensée (associée au premier outil)
    if _sel and action_matches and _sel != action_matches[-1] and thought_matches:
        thought_content = thought_matches[0].group(1).strip()

    if not thought_content or thought_content in ["...", ".", ".."]:
        thought_content = cleaned_response.strip()[:500] if len(cleaned_response.strip()) > 5 else "Réflexion..."
    thought = Thought(content=thought_content)

    if action_name.upper() == "FINAL":
        # Ne jamais recycler thought_content comme réponse finale :
        # si action_input est vide, on retourne "" → react.py déclenchera le repair.
        final_answer = action_input if action_input else ""
        # Unwrap JSON {"response":"..."} généré par erreur par certains LLM
        if final_answer and final_answer.strip().startswith('{') and final_answer.strip().endswith('}'):
            try:
                import json as _json
                _parsed = _json.loads(final_answer.strip())
                if isinstance(_parsed, dict) and len(_parsed) == 1:
                    _val = next(iter(_parsed.values()))
                    if isinstance(_val, str):
                        final_answer = _val
            except Exception:
                pass
        return thought, Action(action_type=ActionType.FINAL_ANSWER, answer=final_answer), halluc_flag, pending_multi_actions

    if action_name.upper() == "CLARIFY":
        clarify_question = action_input if action_input else thought_content
        return thought, Action(action_type=ActionType.CLARIFY, answer=clarify_question), halluc_flag, pending_multi_actions

    if not action_name:
        # Fallback: si le LLM envoie du JSON brut avec path/content
        first_json = None
        for idx, char in enumerate(cleaned_response):
            if char in "{[":
                first_json = extract_balanced_json(cleaned_response, idx)
                if first_json:
                    break
        if first_json:
            try:
                parsed = json.loads(first_json[0])
                if isinstance(parsed, dict) and ("path" in parsed or "content" in parsed):
                    logger.warning("⚠️ Détection JSON brut - conversion en write_file")
                    return thought, Action(
                        action_type=ActionType.TOOL_CALL,
                        tool_name="write_file",
                        tool_args=parsed,
                    ), halluc_flag, pending_multi_actions
            except json.JSONDecodeError:
                pass

        return thought, Action(action_type=ActionType.FINAL_ANSWER, answer=thought_content), halluc_flag, pending_multi_actions

    args = parse_action_args(action_input)
    if "content" in args and isinstance(args["content"], str):
        logger.info(f"📏 CONTENT LENGTH: {len(args['content'])} chars, ~{len(args['content'])//4} tokens")

    return thought, Action(
        action_type=ActionType.TOOL_CALL,
        tool_name=action_name,
        tool_args=args,
    ), halluc_flag, pending_multi_actions


def parse_plan(raw_response: str) -> List[TaskItem]:
    """Parse un bloc PLAN depuis la reponse LLM. Retourne [] si absent.

    Les items [x] écrits par le modèle à l'itération 0 sont forcés à
    completed=False : aucun outil n'a encore été exécuté, un [x] dans le
    plan initial est toujours une hallucination d'avancement.
    """
    match = _PLAN_RE.search(raw_response)
    if not match:
        return []
    tasks: List[TaskItem] = []
    for m in _TASK_LINE_RE.finditer(match.group(1)):
        # Forcer completed=False quel que soit le [x] écrit par le modèle.
        # L'état réel sera mis à jour par _update_plan_progress() au fil des outils.
        tasks.append(TaskItem(description=m.group(2).strip(), completed=False))
    return tasks[:8]
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
