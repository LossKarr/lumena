"""
native_cu.py — Cascade Computer Use natif : Anthropic → OpenAI → Google → fallback maison.

Chaque provider CU natif implémente sa propre boucle agent :
  - screenshot → envoi au LLM avec tool CU natif → parse action → exécute → boucle

La cascade essaie chaque provider dans l'ordre. Si un provider échoue
(clé absente, erreur API, modèle indisponible), on passe au suivant.
Le fallback final est CUAgentLoop (système maison).

Architecture :
    try_native_cu_cascade(goal, max_steps)
      ├─ AnthropicNativeCU.run()    — computer_20251124
      ├─ OpenAINativeCU.run()       — computer tool (Responses API)
      ├─ GoogleNativeCU.run()       — ComputerUse (normalized 0-999)
      └─ None → fallback CUAgentLoop maison

Auteur : LUMENA
Date   : 2026-04-03
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
from loguru import logger

from .cu_agent_loop import CUAction, CUStepResult, CUTaskResult

# ─── Configuration ─────────────────────────────────────────────────────────

_MAX_ITERATIONS = int(os.getenv("LUMENA_CU_MAX_ITERATIONS", "30"))
_TIMEOUT_SEC = int(os.getenv("LUMENA_CU_TIMEOUT_SEC", "600"))
_HTTP_TIMEOUT = 180.0  # timeout par appel HTTP


# ─── Helpers ───────────────────────────────────────────────────────────────

def _has_key(provider: str) -> bool:
    """Vérifie qu'une clé API est disponible pour un provider."""
    env_map = {
        "anthropic": "ANTHROPIC_API_KEY",
        "openai": "OPENAI_API_KEY",
        "google": "GOOGLE_API_KEY",
    }
    var = env_map.get(provider)
    return bool(os.getenv(var)) if var else False


def _get_key(provider: str) -> str:
    """Retourne la clé API d'un provider."""
    env_map = {
        "anthropic": "ANTHROPIC_API_KEY",
        "openai": "OPENAI_API_KEY",
        "google": "GOOGLE_API_KEY",
    }
    return os.getenv(env_map.get(provider, ""), "")


async def _take_screenshot() -> Tuple[str, int, int]:
    """Prend un screenshot et retourne (path, width, height).

    Utilise le module controller existant.
    """
    from .controller import get_computer_use

    cu = get_computer_use()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    path = os.path.join(tempfile.gettempdir(), f"lumena_ncu_{ts}.png")
    await cu.take_screenshot(path)

    try:
        from PIL import Image
        with Image.open(path) as img:
            w, h = img.size
    except Exception:
        w, h = 1920, 1080  # fallback raisonnable

    return path, w, h


def _encode_b64(path: str) -> str:
    """Encode un fichier image en base64."""
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def _execute_action_sync(action_name: str, params: Dict[str, Any]) -> str:
    """Exécute une action CU bas niveau via le controller existant (sync).

    On réutilise pyautogui/pywinauto directement pour rester simple.
    """
    from .controller import get_computer_use

    cu = get_computer_use()

    try:
        if action_name in ("left_click", "click"):
            x, y = int(params.get("x", params.get("coordinate", [0, 0])[0] if isinstance(params.get("coordinate"), list) else 0)),\
                   int(params.get("y", params.get("coordinate", [0, 0])[1] if isinstance(params.get("coordinate"), list) else 0))
            cu.mouse.click(x, y)
            return f"Clic à ({x}, {y})"

        elif action_name == "double_click":
            x, y = int(params.get("x", 0)), int(params.get("y", 0))
            cu.mouse.click(x, y, clicks=2)
            return f"Double-clic à ({x}, {y})"

        elif action_name == "right_click":
            x, y = int(params.get("x", 0)), int(params.get("y", 0))
            cu.mouse.click(x, y, button="right")
            return f"Clic droit à ({x}, {y})"

        elif action_name in ("type", "type_text"):
            text = str(params.get("text", ""))
            cu.keyboard.type_text(text)
            return f"Texte tapé: {text[:50]}"

        elif action_name in ("key", "press_key", "keypress"):
            key = str(params.get("key", params.get("keys", "")))
            # Anthropic format: "Return", "space", etc.
            key_lower = key.lower().replace("return", "enter")
            cu.keyboard.press_key(key_lower)
            return f"Touche: {key}"

        elif action_name in ("hotkey", "keyboard_shortcut"):
            keys_str = str(params.get("keys", params.get("key", "")))
            keys = [k.strip() for k in keys_str.replace("+", ",").split(",") if k.strip()]
            if keys:
                cu.keyboard.hotkey(*keys)
                return f"Raccourci: {'+'.join(keys)}"
            return "Erreur: pas de touches spécifiées"

        elif action_name in ("scroll", "mouse_scroll"):
            direction = str(params.get("direction", "down"))
            amount = int(params.get("amount", params.get("coordinate", [0, 3])[-1] if isinstance(params.get("coordinate"), list) else 3))
            scroll_val = amount if direction == "up" else -amount
            cu.mouse.scroll(scroll_val)
            return f"Scroll {direction} ({amount})"

        elif action_name in ("move", "mouse_move", "move_mouse"):
            x, y = int(params.get("x", 0)), int(params.get("y", 0))
            cu.mouse.move_to(x, y)
            return f"Souris déplacée vers ({x}, {y})"

        elif action_name in ("drag", "left_click_drag"):
            sx, sy = int(params.get("start_x", params.get("startCoordinate", [0, 0])[0] if isinstance(params.get("startCoordinate"), list) else 0)),\
                     int(params.get("start_y", params.get("startCoordinate", [0, 0])[1] if isinstance(params.get("startCoordinate"), list) else 0))
            ex, ey = int(params.get("end_x", params.get("endCoordinate", [0, 0])[0] if isinstance(params.get("endCoordinate"), list) else 0)),\
                     int(params.get("end_y", params.get("endCoordinate", [0, 0])[1] if isinstance(params.get("endCoordinate"), list) else 0))
            cu.mouse.move_to(sx, sy)
            cu.mouse.drag_to(ex, ey)
            return f"Drag de ({sx},{sy}) vers ({ex},{ey})"

        elif action_name == "screenshot":
            return "Screenshot pris (prochaine itération)"

        elif action_name == "wait":
            return "Attente"

        else:
            return f"Action inconnue: {action_name}"

    except Exception as e:
        return f"Erreur exécution [{action_name}]: {e}"


async def _exec_action(action_name: str, params: Dict[str, Any]) -> str:
    """Wrapper async pour _execute_action_sync."""
    return await asyncio.to_thread(_execute_action_sync, action_name, params)


# ═══════════════════════════════════════════════════════════════════════════
# ANTHROPIC NATIVE CU — computer_20251124
# ═══════════════════════════════════════════════════════════════════════════

_ANTHROPIC_CU_MODEL = os.getenv("LUMENA_ANTHROPIC_CU_MODEL", "claude-sonnet-4-20250514")

_ANTHROPIC_CU_SYSTEM = """Tu es un assistant qui contrôle un ordinateur Windows pour accomplir des tâches.
Tu as accès à un outil 'computer' qui te permet d'interagir avec l'écran.
Sois précis dans tes clics et tes actions. Quand le but est atteint, dis-le clairement."""


async def _anthropic_cu_loop(
    goal: str,
    max_steps: int = _MAX_ITERATIONS,
    timeout: float = _TIMEOUT_SEC,
) -> CUTaskResult:
    """Boucle agent CU native Anthropic (computer_20251124).

    Flow :
      1. Envoie un message avec le goal + screenshot initial
      2. Claude répond avec tool_use blocks (computer actions)
      3. On exécute chaque action, on prend un nouveau screenshot
      4. On renvoie tool_result avec le screenshot
      5. Boucle jusqu'à "end_turn" ou max_steps
    """
    api_key = _get_key("anthropic")
    start = time.time()
    steps: List[CUStepResult] = []

    # Screenshot initial
    ss_path, scr_w, scr_h = await _take_screenshot()
    ss_b64 = _encode_b64(ss_path)

    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "anthropic-beta": "computer-use-2025-01-24",
        "content-type": "application/json",
    }

    # Messages initiaux
    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/png", "data": ss_b64},
                },
                {"type": "text", "text": f"BUT: {goal}\n\nVoici l'écran actuel. Commence à accomplir le but."},
            ],
        }
    ]

    tools = [
        {
            "type": "computer_20251124",
            "name": "computer",
            "display_width_px": scr_w,
            "display_height_px": scr_h,
            "display_number": 1,
        }
    ]

    for iteration in range(1, max_steps + 1):
        elapsed = time.time() - start
        if elapsed > timeout:
            return CUTaskResult(
                goal=goal, success=False,
                summary=f"Timeout ({timeout}s)", steps=steps,
                total_iterations=iteration - 1,
                total_duration_ms=(time.time() - start) * 1000,
                exit_reason="timeout",
            )

        iter_start = time.time()
        logger.info(f"🔄 Anthropic CU natif: iteration {iteration}/{max_steps}")

        payload = {
            "model": _ANTHROPIC_CU_MODEL,
            "max_tokens": 4096,
            "system": _ANTHROPIC_CU_SYSTEM,
            "tools": tools,
            "messages": messages,
        }

        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers=headers, json=payload,
            )
            if resp.status_code != 200:
                error = resp.text[:500]
                logger.error(f"Anthropic CU HTTP {resp.status_code}: {error}")
                raise RuntimeError(f"Anthropic CU HTTP {resp.status_code}: {error}")
            data = resp.json()

        stop_reason = data.get("stop_reason", "")
        content_blocks = data.get("content", [])

        # Ajouter la réponse de l'assistant aux messages
        messages.append({"role": "assistant", "content": content_blocks})

        # Extraire les tool_use blocks
        tool_uses = [b for b in content_blocks if b.get("type") == "tool_use"]
        text_blocks = [b.get("text", "") for b in content_blocks if b.get("type") == "text"]
        thought = " ".join(text_blocks).strip()

        if not tool_uses:
            # Pas de tool_use = le modèle a terminé (end_turn)
            summary = thought or "Tâche terminée"
            logger.info(f"✅ Anthropic CU natif: terminé — {summary[:100]}")
            steps.append(CUStepResult(
                iteration=iteration,
                action=CUAction(action="done", thought=thought),
                success=True, output=f"DONE: {summary}",
                duration_ms=(time.time() - iter_start) * 1000,
            ))
            return CUTaskResult(
                goal=goal, success=True, summary=summary, steps=steps,
                total_iterations=iteration,
                total_duration_ms=(time.time() - start) * 1000,
                exit_reason="done",
            )

        # Traiter chaque tool_use
        tool_results = []
        for tu in tool_uses:
            tool_id = tu["id"]
            action_input = tu.get("input", {})
            action_name = action_input.get("action", "screenshot")

            # Extraire les coordonnées Anthropic (format: coordinate: [x, y])
            coord = action_input.get("coordinate", [])
            params: Dict[str, Any] = dict(action_input)
            if coord and isinstance(coord, list) and len(coord) == 2:
                params["x"] = coord[0]
                params["y"] = coord[1]

            if action_name == "screenshot":
                # Juste prendre un screenshot, pas d'action physique
                output = "Screenshot pris"
            else:
                logger.info(f"▶️  Anthropic CU: {action_name}({params})")
                output = await _exec_action(action_name, params)
                logger.info(f"{'✅' if 'Erreur' not in output else '❌'} {output[:100]}")

            steps.append(CUStepResult(
                iteration=iteration,
                action=CUAction(action=action_name, params=params, thought=thought),
                success="Erreur" not in output,
                output=output,
                duration_ms=(time.time() - iter_start) * 1000,
            ))

            # Prendre un nouveau screenshot après l'action
            await asyncio.sleep(0.5)
            ss_path, _, _ = await _take_screenshot()
            ss_b64 = _encode_b64(ss_path)

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tool_id,
                "content": [
                    {"type": "text", "text": output},
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": "image/png", "data": ss_b64},
                    },
                ],
            })

        messages.append({"role": "user", "content": tool_results})

        # Si stop_reason indique la fin
        if stop_reason == "end_turn":
            summary = thought or "Tâche terminée (end_turn)"
            return CUTaskResult(
                goal=goal, success=True, summary=summary, steps=steps,
                total_iterations=iteration,
                total_duration_ms=(time.time() - start) * 1000,
                exit_reason="done",
            )

    return CUTaskResult(
        goal=goal, success=False,
        summary=f"Max itérations ({max_steps}) atteintes",
        steps=steps, total_iterations=max_steps,
        total_duration_ms=(time.time() - start) * 1000,
        exit_reason="max_iterations",
    )


# ═══════════════════════════════════════════════════════════════════════════
# OPENAI NATIVE CU — Responses API computer tool
# ═══════════════════════════════════════════════════════════════════════════

_OPENAI_CU_MODEL = os.getenv("LUMENA_OPENAI_CU_MODEL", "computer-use-preview")

_OPENAI_CU_INSTRUCTIONS = """Tu contrôles un ordinateur Windows. Utilise l'outil computer pour accomplir le but demandé.
Sois méthodique : clique aux bons endroits, tape le texte requis, et vérifie le résultat.
Quand le but est atteint, dis-le."""


async def _openai_cu_loop(
    goal: str,
    max_steps: int = _MAX_ITERATIONS,
    timeout: float = _TIMEOUT_SEC,
) -> CUTaskResult:
    """Boucle agent CU native OpenAI (Responses API + computer tool).

    Flow :
      1. Envoie un message avec le goal
      2. Le modèle répond avec computer_call (actions batch)
      3. On exécute les actions, on prend un screenshot
      4. On renvoie computer_call_output avec le screenshot
      5. Boucle jusqu'à plus de computer_call ou max_steps
    """
    api_key = _get_key("openai")
    start = time.time()
    steps: List[CUStepResult] = []

    # Screenshot initial
    ss_path, scr_w, scr_h = await _take_screenshot()
    ss_b64 = _encode_b64(ss_path)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    tools = [
        {
            "type": "computer_use_preview",
            "display_width": scr_w,
            "display_height": scr_h,
            "environment": "windows",
        }
    ]

    # Premier appel — Responses API
    payload: Dict[str, Any] = {
        "model": _OPENAI_CU_MODEL,
        "instructions": _OPENAI_CU_INSTRUCTIONS,
        "tools": tools,
        "input": [
            {
                "type": "message",
                "role": "user",
                "content": [
                    {"type": "input_text", "text": f"BUT: {goal}"},
                    {
                        "type": "input_image",
                        "image_url": f"data:image/png;base64,{ss_b64}",
                    },
                ],
            }
        ],
        "truncation": "auto",
    }

    for iteration in range(1, max_steps + 1):
        elapsed = time.time() - start
        if elapsed > timeout:
            return CUTaskResult(
                goal=goal, success=False,
                summary=f"Timeout ({timeout}s)", steps=steps,
                total_iterations=iteration - 1,
                total_duration_ms=(time.time() - start) * 1000,
                exit_reason="timeout",
            )

        iter_start = time.time()
        logger.info(f"🔄 OpenAI CU natif: iteration {iteration}/{max_steps}")

        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.post(
                "https://api.openai.com/v1/responses",
                headers=headers, json=payload,
            )
            if resp.status_code != 200:
                error = resp.text[:500]
                logger.error(f"OpenAI CU HTTP {resp.status_code}: {error}")
                raise RuntimeError(f"OpenAI CU HTTP {resp.status_code}: {error}")
            data = resp.json()

        response_id = data.get("id", "")
        output_items = data.get("output", [])

        # Chercher des computer_call dans la réponse
        computer_calls = [o for o in output_items if o.get("type") == "computer_call"]
        text_items = [o for o in output_items if o.get("type") == "message"]

        if not computer_calls:
            # Pas de computer_call = le modèle a terminé
            summary_parts = []
            for ti in text_items:
                for c in ti.get("content", []):
                    if c.get("type") == "output_text":
                        summary_parts.append(c.get("text", ""))
            summary = " ".join(summary_parts).strip() or "Tâche terminée"
            logger.info(f"✅ OpenAI CU natif: terminé — {summary[:100]}")
            steps.append(CUStepResult(
                iteration=iteration,
                action=CUAction(action="done", thought=summary),
                success=True, output=f"DONE: {summary}",
                duration_ms=(time.time() - iter_start) * 1000,
            ))
            return CUTaskResult(
                goal=goal, success=True, summary=summary, steps=steps,
                total_iterations=iteration,
                total_duration_ms=(time.time() - start) * 1000,
                exit_reason="done",
            )

        # Traiter chaque computer_call
        input_items: List[Dict[str, Any]] = []
        for cc in computer_calls:
            call_id = cc.get("call_id", "")
            action_data = cc.get("action", {})
            action_type = action_data.get("type", "")

            # Mapper les actions OpenAI vers nos actions locales
            params: Dict[str, Any] = {}
            local_action = action_type

            if action_type in ("click", "double_click", "right_click"):
                coord = action_data.get("coordinate", [0, 0])
                if isinstance(coord, list) and len(coord) == 2:
                    params["x"] = coord[0]
                    params["y"] = coord[1]
                local_action = action_type

            elif action_type == "type":
                params["text"] = action_data.get("text", "")
                local_action = "type_text"

            elif action_type == "keypress":
                keys = action_data.get("keys", [])
                if len(keys) == 1:
                    params["key"] = keys[0]
                    local_action = "press_key"
                else:
                    params["keys"] = "+".join(keys)
                    local_action = "hotkey"

            elif action_type == "scroll":
                coord = action_data.get("coordinate", [0, 0])
                delta_x = action_data.get("delta_x", 0)
                delta_y = action_data.get("delta_y", 0)
                if isinstance(coord, list) and len(coord) == 2:
                    params["x"] = coord[0]
                    params["y"] = coord[1]
                if delta_y < 0:
                    params["direction"] = "down"
                    params["amount"] = abs(delta_y) // 100 or 3
                else:
                    params["direction"] = "up"
                    params["amount"] = delta_y // 100 or 3
                local_action = "scroll"

            elif action_type == "drag":
                start_coord = action_data.get("startCoordinate", [0, 0])
                end_coord = action_data.get("endCoordinate", [0, 0])
                if isinstance(start_coord, list) and len(start_coord) == 2:
                    params["start_x"] = start_coord[0]
                    params["start_y"] = start_coord[1]
                if isinstance(end_coord, list) and len(end_coord) == 2:
                    params["end_x"] = end_coord[0]
                    params["end_y"] = end_coord[1]
                local_action = "drag"

            elif action_type == "screenshot":
                local_action = "screenshot"

            elif action_type == "wait":
                local_action = "wait"

            if local_action == "screenshot":
                output = "Screenshot pris"
            else:
                logger.info(f"▶️  OpenAI CU: {local_action}({params})")
                output = await _exec_action(local_action, params)
                logger.info(f"{'✅' if 'Erreur' not in output else '❌'} {output[:100]}")

            steps.append(CUStepResult(
                iteration=iteration,
                action=CUAction(action=local_action, params=params),
                success="Erreur" not in output,
                output=output,
                duration_ms=(time.time() - iter_start) * 1000,
            ))

            # Screenshot après l'action
            await asyncio.sleep(0.5)
            ss_path, _, _ = await _take_screenshot()
            ss_b64 = _encode_b64(ss_path)

            input_items.append({
                "type": "computer_call_output",
                "call_id": call_id,
                "output": {
                    "type": "computer_screenshot",
                    "image_url": f"data:image/png;base64,{ss_b64}",
                },
            })

        # Préparer le prochain appel avec previous_response_id
        payload = {
            "model": _OPENAI_CU_MODEL,
            "instructions": _OPENAI_CU_INSTRUCTIONS,
            "tools": tools,
            "input": input_items,
            "previous_response_id": response_id,
            "truncation": "auto",
        }

    return CUTaskResult(
        goal=goal, success=False,
        summary=f"Max itérations ({max_steps}) atteintes",
        steps=steps, total_iterations=max_steps,
        total_duration_ms=(time.time() - start) * 1000,
        exit_reason="max_iterations",
    )


# ═══════════════════════════════════════════════════════════════════════════
# GOOGLE NATIVE CU — Gemini ComputerUse tool
# ═══════════════════════════════════════════════════════════════════════════

_GOOGLE_CU_MODEL = os.getenv(
    "LUMENA_GOOGLE_CU_MODEL", "gemini-2.5-flash-preview-native-audio-dialog"
)

_GOOGLE_CU_SYSTEM = """Tu contrôles un ordinateur Windows. Utilise les outils disponibles pour accomplir le but.
Les coordonnées sont normalisées entre 0 et 9999 (0,0 = coin supérieur gauche, 9999,9999 = coin inférieur droit).
Quand le but est atteint, réponds avec un message texte (sans appel d'outil)."""


async def _google_cu_loop(
    goal: str,
    max_steps: int = _MAX_ITERATIONS,
    timeout: float = _TIMEOUT_SEC,
) -> CUTaskResult:
    """Boucle agent CU native Google (Gemini + ComputerUse tool).

    Google utilise des coordonnées normalisées (0-9999 au lieu de pixels).
    Les function_call dans la réponse contiennent des actions comme
    click_at, type_text_at, scroll_at, etc.
    """
    api_key = _get_key("google")
    start = time.time()
    steps: List[CUStepResult] = []

    # Screenshot initial
    ss_path, scr_w, scr_h = await _take_screenshot()
    ss_b64 = _encode_b64(ss_path)

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{_GOOGLE_CU_MODEL}:generateContent?key={api_key}"
    )

    # Tool declaration pour Computer Use Gemini
    tools_decl = [
        {
            "computerUse": {
                "environment": "ENVIRONMENT_DESKTOP",
                "displayWidth": scr_w,
                "displayHeight": scr_h,
            }
        }
    ]

    contents: List[Dict[str, Any]] = [
        {
            "role": "user",
            "parts": [
                {"text": f"BUT: {goal}\n\nVoici l'écran actuel."},
                {
                    "inlineData": {
                        "mimeType": "image/png",
                        "data": ss_b64,
                    }
                },
            ],
        }
    ]

    for iteration in range(1, max_steps + 1):
        elapsed = time.time() - start
        if elapsed > timeout:
            return CUTaskResult(
                goal=goal, success=False,
                summary=f"Timeout ({timeout}s)", steps=steps,
                total_iterations=iteration - 1,
                total_duration_ms=(time.time() - start) * 1000,
                exit_reason="timeout",
            )

        iter_start = time.time()
        logger.info(f"🔄 Google CU natif: iteration {iteration}/{max_steps}")

        payload = {
            "contents": contents,
            "tools": tools_decl,
            "systemInstruction": {"parts": [{"text": _GOOGLE_CU_SYSTEM}]},
            "generationConfig": {"temperature": 0.3, "maxOutputTokens": 4096},
        }

        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code != 200:
                error = resp.text[:500]
                logger.error(f"Google CU HTTP {resp.status_code}: {error}")
                raise RuntimeError(f"Google CU HTTP {resp.status_code}: {error}")
            data = resp.json()

        candidates = data.get("candidates", [])
        if not candidates:
            raise RuntimeError("Google CU: pas de candidats dans la réponse")

        cand = candidates[0]
        parts = cand.get("content", {}).get("parts", [])

        # Ajouter la réponse du modèle aux contents
        contents.append({"role": "model", "parts": parts})

        # Chercher des functionCall dans parts
        func_calls = [p for p in parts if "functionCall" in p]
        text_parts = [p.get("text", "") for p in parts if "text" in p]

        if not func_calls:
            # Pas de function call = terminé
            summary = " ".join(text_parts).strip() or "Tâche terminée"
            logger.info(f"✅ Google CU natif: terminé — {summary[:100]}")
            steps.append(CUStepResult(
                iteration=iteration,
                action=CUAction(action="done", thought=summary),
                success=True, output=f"DONE: {summary}",
                duration_ms=(time.time() - iter_start) * 1000,
            ))
            return CUTaskResult(
                goal=goal, success=True, summary=summary, steps=steps,
                total_iterations=iteration,
                total_duration_ms=(time.time() - start) * 1000,
                exit_reason="done",
            )

        # Traiter les function calls
        func_response_parts: List[Dict[str, Any]] = []
        for fc_part in func_calls:
            fc = fc_part["functionCall"]
            fn_name = fc.get("name", "")
            fn_args = fc.get("args", {})

            # Convertir les coordonnées normalisées (0-9999) → pixels
            params: Dict[str, Any] = dict(fn_args)
            local_action = fn_name

            # Gemini CU actions: click, type, scroll, keypress, wait, screenshot, etc.
            if fn_name in ("click", "click_at", "left_click"):
                nx = int(params.pop("x", params.pop("normalizedX", 0)))
                ny = int(params.pop("y", params.pop("normalizedY", 0)))
                params["x"] = round(nx * scr_w / 10000)
                params["y"] = round(ny * scr_h / 10000)
                local_action = "click"

            elif fn_name in ("double_click", "double_click_at"):
                nx = int(params.pop("x", params.pop("normalizedX", 0)))
                ny = int(params.pop("y", params.pop("normalizedY", 0)))
                params["x"] = round(nx * scr_w / 10000)
                params["y"] = round(ny * scr_h / 10000)
                local_action = "double_click"

            elif fn_name in ("right_click", "right_click_at"):
                nx = int(params.pop("x", params.pop("normalizedX", 0)))
                ny = int(params.pop("y", params.pop("normalizedY", 0)))
                params["x"] = round(nx * scr_w / 10000)
                params["y"] = round(ny * scr_h / 10000)
                local_action = "right_click"

            elif fn_name in ("type", "type_text", "type_text_at"):
                local_action = "type_text"

            elif fn_name in ("scroll", "scroll_at"):
                nx = int(params.pop("x", params.pop("normalizedX", 0)))
                ny = int(params.pop("y", params.pop("normalizedY", 0)))
                params["x"] = round(nx * scr_w / 10000)
                params["y"] = round(ny * scr_h / 10000)
                delta = int(params.pop("direction", params.pop("scrollDirection", -3)))
                if isinstance(delta, str):
                    params["direction"] = delta
                else:
                    params["direction"] = "down" if delta < 0 else "up"
                    params["amount"] = abs(delta)
                local_action = "scroll"

            elif fn_name in ("key", "keypress", "press_key"):
                local_action = "press_key"

            elif fn_name in ("drag", "drag_at"):
                sx = int(params.pop("startX", params.pop("normalizedStartX", 0)))
                sy = int(params.pop("startY", params.pop("normalizedStartY", 0)))
                ex = int(params.pop("endX", params.pop("normalizedEndX", 0)))
                ey = int(params.pop("endY", params.pop("normalizedEndY", 0)))
                params["start_x"] = round(sx * scr_w / 10000)
                params["start_y"] = round(sy * scr_h / 10000)
                params["end_x"] = round(ex * scr_w / 10000)
                params["end_y"] = round(ey * scr_h / 10000)
                local_action = "drag"

            elif fn_name == "screenshot":
                local_action = "screenshot"

            elif fn_name == "wait":
                local_action = "wait"

            if local_action == "screenshot":
                output = "Screenshot pris"
            elif local_action == "wait":
                await asyncio.sleep(float(params.get("seconds", params.get("duration", 2))))
                output = "Attente"
            else:
                logger.info(f"▶️  Google CU: {local_action}({params})")
                output = await _exec_action(local_action, params)
                logger.info(f"{'✅' if 'Erreur' not in output else '❌'} {output[:100]}")

            steps.append(CUStepResult(
                iteration=iteration,
                action=CUAction(action=local_action, params=params),
                success="Erreur" not in output,
                output=output,
                duration_ms=(time.time() - iter_start) * 1000,
            ))

            # Screenshot après l'action
            await asyncio.sleep(0.5)
            ss_path, _, _ = await _take_screenshot()
            ss_b64 = _encode_b64(ss_path)

            func_response_parts.append({
                "functionResponse": {
                    "name": fn_name,
                    "response": {
                        "result": output,
                        "screenshot": {
                            "inlineData": {
                                "mimeType": "image/png",
                                "data": ss_b64,
                            }
                        },
                    },
                }
            })

        # Ajouter les réponses de fonction aux contents
        contents.append({"role": "user", "parts": func_response_parts})

    return CUTaskResult(
        goal=goal, success=False,
        summary=f"Max itérations ({max_steps}) atteintes",
        steps=steps, total_iterations=max_steps,
        total_duration_ms=(time.time() - start) * 1000,
        exit_reason="max_iterations",
    )


# ═══════════════════════════════════════════════════════════════════════════
# CASCADE — Anthropic → OpenAI → Google → None (fallback maison)
# ═══════════════════════════════════════════════════════════════════════════

_CASCADE_ORDER = ["anthropic", "openai", "google"]


def _get_provider_loop(provider: str):
    """Retourne la fonction de boucle CU pour un provider (lookup runtime)."""
    # Lookup dynamique pour permettre le mocking en tests
    _map = {
        "anthropic": "_anthropic_cu_loop",
        "openai": "_openai_cu_loop",
        "google": "_google_cu_loop",
    }
    fname = _map.get(provider)
    if not fname:
        return None
    import sys
    mod = sys.modules[__name__]
    return getattr(mod, fname, None)


async def try_native_cu_cascade(
    goal: str,
    max_steps: int = _MAX_ITERATIONS,
) -> Optional[CUTaskResult]:
    """Essaie les CU natifs dans l'ordre Anthropic → OpenAI → Google.

    Retourne le CUTaskResult du premier provider qui réussit à s'exécuter,
    ou None si aucun n'est disponible / tous échouent.

    Attention : un provider qui s'exécute et échoue (max_iterations, stuck)
    retourne quand même son CUTaskResult — on ne cascade PAS après une exécution
    complète. La cascade ne s'applique que si le provider est *indisponible*
    (pas de clé API, erreur HTTP au 1er appel, modèle inexistant).
    """
    override = os.getenv("LUMENA_CU_NATIVE_ORDER", "").strip()
    if override:
        order = [p.strip().lower() for p in override.split(",") if p.strip()]
    else:
        order = list(_CASCADE_ORDER)

    # Désactiver complètement le CU natif si demandé
    if os.getenv("LUMENA_CU_NATIVE_DISABLED", "0") == "1":
        logger.info("🏠 CU natif désactivé (LUMENA_CU_NATIVE_DISABLED=1), fallback maison")
        return None

    for provider in order:
        loop_func = _get_provider_loop(provider)
        if loop_func is None:
            logger.warning(f"CU natif: provider inconnu '{provider}', skip")
            continue

        if not _has_key(provider):
            logger.debug(f"CU natif: {provider} sans clé API, skip")
            continue

        logger.info(f"🌐 CU natif: tentative {provider}...")

        try:
            result = await loop_func(goal, max_steps=max_steps)
            # Enrichir le summary avec le provider utilisé
            result.summary = f"[{provider.upper()} CU natif] {result.summary}"
            result.exit_reason = f"native_{provider}:{result.exit_reason}"
            logger.info(
                f"{'✅' if result.success else '❌'} CU natif {provider}: "
                f"{result.summary[:100]} ({result.total_iterations} iter, "
                f"{result.total_duration_ms / 1000:.1f}s)"
            )
            return result
        except Exception as e:
            logger.warning(f"⚠️ CU natif {provider} échoué: {e}")
            continue

    logger.info("🏠 Aucun CU natif disponible, fallback vers système maison")
    return None
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the Apache License, Version 2.0
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
