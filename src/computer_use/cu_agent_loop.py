"""
cu_agent_loop.py — Boucle Agent Computer Use autonome pour LUMENA.

Boucle spécialisée pour les tâches multi-étapes sur le bureau Windows.
Le LLM voit un screenshot à chaque itération, raisonne, choisit une action,
l'exécute, et vérifie le résultat — de manière entièrement autonome.

Architecture :
    1. Perception  — screenshot + curseur + métadonnées écran
    2. Raisonnement — envoi au LLM vision (Gemini / Claude / Ollama)
    3. Action      — exécution via les contrôleurs existants (click, type, scroll...)
    4. Vérification — re-screenshot, détection stuck (pHash)
    5. Boucle      — jusqu'à but atteint, timeout, ou max itérations

Modèle-agnostique : fonctionne avec DeepSeek, Claude, Gemini, Ollama, ou
tout provider vision qui accepte (image_base64 + prompt) → texte.

Auteur : LUMENA / Phase 1.2
Date   : 2026-03-06
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from loguru import logger
from src.prompts.computer_use.cu_prompts import (
    CU_SYSTEM_PROMPT,
    CU_STEP_PROMPT,
)


# ─── Configuration ─────────────────────────────────────────────────────────

import os as _os  # import local pour les constantes module-level (os déjà importé plus haut)

MAX_ITERATIONS      = int(_os.getenv("LUMENA_CU_MAX_ITERATIONS", "30"))
TIMEOUT_SECONDS     = int(_os.getenv("LUMENA_CU_TIMEOUT_SEC", "600"))   # 10 minutes
STUCK_THRESHOLD     = 3     # screenshots identiques consécutifs = stuck
STUCK_HASH_TOLERANCE = 5    # tolérance du hash perceptuel (bits différents)
STEP_ACTION_TIMEOUT  = 10.0 # timeout par action individuelle
MAX_CONSECUTIVE_ERRORS = 5  # max erreurs de parsing consécutives avant abandon


# ─── Data classes ──────────────────────────────────────────────────────────

@dataclass
class CUAction:
    """Action décidée par le LLM à chaque itération."""
    action: str                    # "click", "type_text", "scroll", "hotkey", etc.
    params: Dict[str, Any] = field(default_factory=dict)
    thought: str = ""             # raisonnement du LLM
    raw_response: str = ""        # réponse brute du LLM

    def __str__(self) -> str:
        p = ", ".join(f"{k}={v!r}" for k, v in self.params.items())
        return f"{self.action}({p})"


@dataclass
class CUStepResult:
    """Résultat d'une itération de la boucle CU."""
    iteration: int
    action: CUAction
    success: bool
    output: str = ""
    screenshot_hash: str = ""
    duration_ms: float = 0.0
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class CUTaskResult:
    """Résultat final d'une tâche CU complète."""
    goal: str
    success: bool
    summary: str
    steps: List[CUStepResult] = field(default_factory=list)
    total_iterations: int = 0
    total_duration_ms: float = 0.0
    exit_reason: str = ""         # "done", "max_iterations", "timeout", "error"


# ─── Prompts ───────────────────────────────────────────────────────────────


# ─── Stuck Detection ──────────────────────────────────────────────────────

def _image_hash(image_path: str) -> str:
    """Hash perceptuel simple : average hash 8x8 = 64 bits.
    
    Downscale l'image à 8x8 en niveaux de gris, puis compare
    chaque pixel à la moyenne pour produire un hash binaire.
    Rapide (~2ms) et robuste aux petits changements de rendu.
    """
    try:
        from PIL import Image
        img = Image.open(image_path).convert("L").resize((8, 8), Image.Resampling.LANCZOS)
        pixels = list(img.getdata())
        avg = sum(pixels) / len(pixels)
        bits = "".join("1" if p > avg else "0" for p in pixels)
        return bits
    except Exception:  # phash échoué, fallback MD5
        # Fallback : hash MD5 du fichier (pas perceptuel mais mieux que rien)
        try:
            with open(image_path, "rb") as f:
                return hashlib.md5(f.read()).hexdigest()
        except Exception:
            return ""  # hash impossible


def _hamming_distance(h1: str, h2: str) -> int:
    """Distance de Hamming entre deux hashes binaires."""
    if len(h1) != len(h2) or not h1 or not h2:
        return 999  # Incomparable
    return sum(c1 != c2 for c1, c2 in zip(h1, h2))


# ─── Action Parsing ───────────────────────────────────────────────────────

def _sanitize_llm_json(text: str) -> str:
    """Nettoie le texte JSON produit par n'importe quel LLM vision.
    
    Model-agnostic : gère les problèmes courants de minicpm-v, llava,
    moondream, gemma3, llama3.2-vision, et tout autre modèle.
    
    Corrige :
    - Caractères CJK (chinois/japonais/coréen) mélangés au texte
    - Guillemets simples au lieu de doubles dans les valeurs JSON
    - Tokens modèle spéciaux (<box>, </box>, <ref>, <|im_start|>, etc.)
    - Caractères de contrôle
    - Texte avant/après le JSON (preamble, postamble)
    - Commentaires dans le JSON
    - Trailing commas
    """
    import re
    
    # 1. Supprimer les tokens modèle spéciaux (minicpm-v, qwen-vl, llava, etc.)
    text = re.sub(r'</?(?:box|ref|seg|grounding|think|output|image|s|unk)>', '', text)
    text = re.sub(r'<\|(?:im_start|im_end|endoftext|pad|system|user|assistant)\|>', '', text)
    
    # 1b. Supprimer les role labels isolés (assistant, user, system) en début de texte
    text = re.sub(r'^(?:assistant|user|system)\s*\n?', '', text.strip(), flags=re.IGNORECASE)
    
    # 2. Supprimer les caractères CJK (U+2E80-U+9FFF, U+F900-U+FAFF)
    text = re.sub(r'[\u2E80-\u9FFF\uF900-\uFAFF]+', '', text)
    
    # 3. Supprimer les caractères coréens (hangul)
    text = re.sub(r'[\uAC00-\uD7AF\u1100-\u11FF]+', '', text)
    
    # 4. Supprimer les caractères arabes/hébreux/thaï parasites
    text = re.sub(r'[\u0600-\u06FF\u0590-\u05FF\u0E00-\u0E7F]+', '', text)
    
    # 5. Nettoyer les double-espaces résultants
    text = re.sub(r'  +', ' ', text)
    
    # 6. Fixer les single quotes pour les valeurs JSON  
    #    Pattern: "key": 'value' → "key": "value"
    text = re.sub(r":\s*'([^']*?)'", r': "\1"', text)
    
    # 7. Supprimer les trailing commas avant } ou ]
    text = re.sub(r',\s*([}\]])', r'\1', text)
    
    # 8. Supprimer les commentaires // et /* */
    text = re.sub(r'//[^\n]*', '', text)
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.DOTALL)
    
    # 9. Supprimer les caractères de contrôle (sauf newline/tab)
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', text)
    
    return text.strip()


def _parse_cu_action(raw: str) -> CUAction:
    """Parse la réponse JSON du LLM en CUAction.
    
    Stratégie multi-niveaux (robuste aux LLMs qui ajoutent du texte) :
    1. JSON direct
    2. JSON dans bloc ```json ... ```
    3. Premier { ... } trouvé
    4. Regex pour trouver un objet avec "action"
    5. Pré-nettoyage + re-tentative (CJK, single quotes, tokens modèle)
    """
    import re

    if not raw or not raw.strip():
        return CUAction(action="error", params={"reason": "Réponse LLM vide"}, raw_response=raw)

    text = raw.strip()

    # Stratégie 1 : JSON pur
    try:
        data = json.loads(text)
        return _dict_to_action(data, raw)
    except json.JSONDecodeError:
        pass  # essayer méthode suivante

    # Stratégie 2 : JSON dans markdown
    if "```json" in text:
        try:
            json_str = text.split("```json")[1].split("```")[0].strip()
            data = json.loads(json_str)
            return _dict_to_action(data, raw)
        except (json.JSONDecodeError, IndexError):
            pass  # essayer méthode suivante

    if "```" in text:
        try:
            json_str = text.split("```")[1].split("```")[0].strip()
            data = json.loads(json_str)
            return _dict_to_action(data, raw)
        except (json.JSONDecodeError, IndexError):
            pass  # essayer méthode suivante

    # Stratégie 3 : trouver le JSON le plus large
    brace_start = text.find("{")
    brace_end = text.rfind("}")
    if brace_start != -1 and brace_end > brace_start:
        try:
            data = json.loads(text[brace_start:brace_end + 1])
            return _dict_to_action(data, raw)
        except json.JSONDecodeError:
            pass  # essayer méthode suivante

    # Stratégie 4 : regex pour un objet JSON avec "action"
    pattern = r'\{[^{}]*"action"\s*:\s*"[^"]+?"[^{}]*\}'
    matches = re.findall(pattern, text, re.DOTALL)
    for match in matches:
        try:
            data = json.loads(match)
            return _dict_to_action(data, raw)
        except json.JSONDecodeError:
            pass  # essayer méthode suivante

    # Stratégie 5 : Nettoyage agressif (CJK, single quotes, tokens modèle)
    #               puis re-tentative des stratégies 1-4
    cleaned = _sanitize_llm_json(text)
    if cleaned != text:
        # Re-tenter avec le texte nettoyé
        brace_start = cleaned.find("{")
        brace_end = cleaned.rfind("}")
        if brace_start != -1 and brace_end > brace_start:
            try:
                data = json.loads(cleaned[brace_start:brace_end + 1])
                return _dict_to_action(data, raw)
            except json.JSONDecodeError:
                pass  # essayer méthode suivante

        # Regex sur le texte nettoyé
        matches = re.findall(pattern, cleaned, re.DOTALL)
        for match in matches:
            try:
                data = json.loads(match)
                return _dict_to_action(data, raw)
            except json.JSONDecodeError:
                pass  # essayer méthode suivante

    return CUAction(
        action="error",
        params={"reason": f"Impossible de parser le JSON: {text[:200]}"},
        raw_response=raw,
    )


def _dict_to_action(data: Dict[str, Any], raw: str) -> CUAction:
    """Convertit un dict parsé en CUAction."""
    action = data.get("action", "error")
    params = data.get("params", {})
    thought = data.get("thought", "")

    # Certains LLMs mettent les params au même niveau
    if not params and action != "error":
        known_non_params = {"action", "thought", "params"}
        params = {k: v for k, v in data.items() if k not in known_non_params}

    # Normaliser les actions avec alias (model-agnostic)
    action_aliases = {
        "click_left": "click",
        "left_click": "click",
        "mouse_click": "click",
        "tap": "click",
        "finish": "done",
        "complete": "done",
        "completed": "done",
        "task_done": "done",
        "task_complete": "done",
        "success": "done",
        "type": "type_text",
        "write": "type_text",
        "input": "type_text",
        "enter_text": "type_text",
        "key": "press_key",
        "keyboard": "press_key",
        "keypress": "press_key",
        "shortcut": "hotkey",
        "key_combo": "hotkey",
        "open": "open_app",
        "launch": "open_app",
        "start_app": "open_app",
        "scroll_down": "scroll",
        "scroll_up": "scroll",
        "swipe": "scroll",
    }
    action = action_aliases.get(action, action)

    # Normaliser scroll_down/scroll_up → scroll avec direction
    if data.get("action") in ("scroll_down", "scroll_up"):
        params.setdefault("direction", "down" if data["action"] == "scroll_down" else "up")
        params.setdefault("amount", 3)

    return CUAction(action=action, params=params, thought=thought, raw_response=raw)


# ─── Unstuck Strategies ───────────────────────────────────────────────────

UNSTUCK_ACTIONS = [
    CUAction(action="press_key", params={"key": "escape"}, thought="Tentative de déblocage: Escape"),
    CUAction(action="scroll", params={"direction": "down", "amount": 5}, thought="Tentative de déblocage: scroll down"),
    CUAction(action="hotkey", params={"keys": "alt+tab"}, thought="Tentative de déblocage: Alt+Tab"),
    CUAction(action="click", params={"x": 960, "y": 540}, thought="Tentative de déblocage: clic au centre de l'écran"),
]


# ─── Agent CU Loop ─────────────────────────────────────────────────────────

class CUAgentLoop:
    """Boucle Agent Computer Use autonome.
    
    Orchestre la perception (screenshot), le raisonnement (LLM vision),
    et l'action (contrôleurs CU) pour accomplir une tâche multi-étapes
    sur le bureau Windows.
    
    Modèle-agnostique : accepte tout LLM vision via la fonction
    `vision_llm_func(image_path, prompt) → {"success": bool, "answer": str}`.
    
    Usage:
        loop = CUAgentLoop()
        result = await loop.run("Ouvre Chrome et va sur google.com")
    """

    def __init__(
        self,
        vision_llm_func: Optional[Callable] = None,
        max_iterations: int = MAX_ITERATIONS,
        timeout_seconds: int = TIMEOUT_SECONDS,
    ):
        """
        Args:
            vision_llm_func: async (image_path, prompt) → {"success": bool, "answer": str}
                             Si None, utilise le provider par défaut de VisionModule.
            max_iterations: Max d'itérations avant arrêt forcé.
            timeout_seconds: Timeout global de la tâche.
        """
        self._vision_llm_func = vision_llm_func
        self.max_iterations = max_iterations
        self.timeout_seconds = timeout_seconds

        # Lazy-loaded
        self._cu = None
        self._vision = None
        # _provider_failures supprimé — géré par VisionModule._provider_health (P3.6)

        # P4.2 — DOM snapshot courant (web context, reset chaque itération)
        self._current_dom_snapshot = None

    def _get_cu(self):
        """Obtient l'instance ComputerUse (lazy)."""
        if self._cu is None:
            from . import get_computer_use
            self._cu = get_computer_use()
        return self._cu

    def _get_vision(self):
        """Obtient l'instance VisionModule (lazy)."""
        if self._vision is None:
            from .vision import get_vision
            self._vision = get_vision()
        return self._vision

    # ─── P4.1 — Context Detection ──────────────────────────────────────

    _BROWSER_HINTS = frozenset({
        "chrome", "firefox", "edge", "brave", "arc", "opera",
        "safari", "chromium", "vivaldi",
    })

    async def _detect_context(self) -> str:
        """Détecte si le contexte courant est 'web' ou 'desktop'.

        1. Playwright page active contrôlée par Lumena → 'web'
        2. Titre fenêtre active contient un navigateur → 'web'
        3. Sinon → 'desktop'
        """
        # 1. Playwright page active
        try:
            from ..tools.playwright_browser import get_playwright_browser
            browser = get_playwright_browser()
            if (browser.is_running
                    and getattr(browser, '_page', None) is not None
                    and not browser._page.is_closed()):
                return "web"
        except Exception:
            pass

        # 2. Titre fenêtre active
        try:
            cu = self._get_cu()
            title = await asyncio.to_thread(cu.window.get_active_window)
            if title and any(h in title.lower() for h in self._BROWSER_HINTS):
                return "web"
        except Exception:
            pass

        return "desktop"

    async def _call_vision_llm(self, image_path: str, prompt: str) -> Dict[str, Any]:
        """Appelle le LLM vision via route_cu_vision (P3.6 — router unifié).
        
        Bypass custom func si défini. Sinon route_cu_vision orchestre la cascade
        avec health tracking sur VisionModule._provider_health.
        """
        if self._vision_llm_func:
            return await self._vision_llm_func(image_path, prompt)

        vision = self._get_vision()
        from .cu_router import route_cu_vision
        result = await route_cu_vision(vision, image_path, prompt, capability="vision_describe")
        if result.get("success"):
            # Normaliser la clé pour la compat avec le code appelant (qui cherche "answer")
            return {"success": True, "answer": result.get("text", result.get("answer", ""))}
        return result

    async def _take_screenshot(self) -> Tuple[str, float, int, int]:
        """Prend un screenshot et le prépare pour le LLM.
        
        Returns:
            (prepared_path, scale_factor, pad_offset_x, pad_offset_y)
        """
        cu = self._get_cu()
        vision = self._get_vision()
        screenshot_dir = tempfile.gettempdir()
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        raw_path = os.path.join(screenshot_dir, f"lumena_cu_agent_{ts}.png")

        await cu.take_screenshot(raw_path)
        prepared_path, scale_factor, _w, _h, pad_offset_x, pad_offset_y = await vision.prepare_screenshot_for_llm(raw_path)
        return prepared_path, scale_factor, pad_offset_x, pad_offset_y

    async def _execute_action(
        self,
        action: CUAction,
        scale_factor: float,
        pad_offset_x: int = 0,
        pad_offset_y: int = 0,
    ) -> str:
        """Exécute une CUAction via les contrôleurs bas niveau.
        
        Les coordonnées sont automatiquement converties de l'espace LLM
        vers l'espace écran réel (upscale + monitor offset).
        
        Returns:
            Message de résultat.
        """
        cu = self._get_cu()
        vision = self._get_vision()
        name = action.action
        params = dict(action.params)

        # ── Résolution target_index → coordonnées (P4.2.5 — DOM-aware) ──
        if "target_index" in params and name in ("click", "double_click", "right_click"):
            if self._current_dom_snapshot:
                idx = int(params.pop("target_index"))
                elem = next(
                    (e for e in self._current_dom_snapshot.elements if e.index == idx), None
                )
                if elem and elem.center:
                    params["x"], params["y"] = elem.center
                else:
                    return f"Élément index {idx} introuvable dans le DOM snapshot actuel"
            else:
                params.pop("target_index")
                return "DOM snapshot indisponible — utilise x/y pour cette action"

        # ── Scaling des coordonnées (critique !) ──
        if name in ("click", "double_click", "right_click", "move_mouse"):
            if "x" in params and "y" in params:
                llm_x, llm_y = int(params["x"]), int(params["y"])
                screen_x, screen_y = vision.scale_coordinates_to_screen(
                    llm_x,
                    llm_y,
                    scale_factor,
                    pad_offset_x=pad_offset_x,
                    pad_offset_y=pad_offset_y,
                )
                offset_x, offset_y = cu.screen.get_monitor_offset()
                params["x"] = screen_x + offset_x
                params["y"] = screen_y + offset_y
                logger.debug(
                    f"🎯 CU Agent coords: LLM({llm_x},{llm_y}) → "
                    f"Screen({screen_x},{screen_y}) + offset({offset_x},{offset_y}) "
                    f"= ({params['x']},{params['y']})"
                )

        # ── Dispatch vers les contrôleurs ──
        try:
            if name == "click":
                cu.mouse.click(params.get("x", 0), params.get("y", 0), params.get("button", "left"))
                return f"Clic à ({params.get('x')}, {params.get('y')})"

            elif name == "double_click":
                cu.mouse.click(params.get("x", 0), params.get("y", 0), clicks=2)
                return f"Double-clic à ({params.get('x')}, {params.get('y')})"

            elif name == "right_click":
                cu.mouse.click(params.get("x", 0), params.get("y", 0), button="right")
                return f"Clic droit à ({params.get('x')}, {params.get('y')})"

            elif name == "type_text":
                text = str(params.get("text", ""))
                cu.keyboard.type_text(text)
                return f"Texte tapé: {text[:50]}{'...' if len(text) > 50 else ''}"

            elif name == "press_key":
                key = str(params.get("key", params.get("input", "")))
                cu.keyboard.press_key(key)
                return f"Touche: {key}"

            elif name == "hotkey":
                keys_str = str(params.get("keys", params.get("input", "")))
                keys = [k.strip() for k in keys_str.replace("+", ",").split(",") if k.strip()]
                if keys:
                    cu.keyboard.hotkey(*keys)
                    return f"Raccourci: {'+'.join(keys)}"
                return "Erreur: pas de touches spécifiées"

            elif name == "scroll":
                direction = str(params.get("direction", "down"))
                amount = int(params.get("amount", 3))
                scroll_val = amount if direction == "up" else -amount
                cu.mouse.scroll(scroll_val)
                return f"Scroll {direction} ({amount})"

            elif name == "open_app":
                app_name = str(params.get("name", ""))
                await cu.open_application(app_name)
                return f"Application ouverte: {app_name}"

            elif name == "open_url":
                url = str(params.get("url", ""))
                # Utiliser Ctrl+L pour naviguer dans la fenêtre Chrome active
                # (plus fiable que webbrowser.open qui peut ouvrir une nouvelle fenêtre)
                try:
                    import pyperclip
                    pyperclip.copy(url)
                    await asyncio.to_thread(cu.keyboard.hotkey, "ctrl", "l")
                    await asyncio.sleep(0.4)
                    await asyncio.to_thread(cu.keyboard.hotkey, "ctrl", "a")
                    await asyncio.sleep(0.1)
                    await asyncio.to_thread(cu.keyboard.hotkey, "ctrl", "v")
                    await asyncio.sleep(0.15)
                    await asyncio.to_thread(cu.keyboard.press_key, "enter")
                    await asyncio.sleep(2.0)
                except Exception:
                    # Fallback: ouvrir via webbrowser système
                    import webbrowser
                    webbrowser.open(url)
                    await asyncio.sleep(2.0)
                return f"URL ouverte: {url}"

            elif name == "move_mouse":
                x, y = params.get("x", 0), params.get("y", 0)
                await asyncio.to_thread(cu.mouse.move_to, x, y)
                return f"Souris déplacée vers ({x}, {y})"

            elif name == "drag":
                sx = int(params.get("start_x", params.get("x", 0)))
                sy = int(params.get("start_y", params.get("y", 0)))
                ex = int(params.get("end_x", sx))
                ey = int(params.get("end_y", sy))
                # Scaling des 4 coordonnées
                sx, sy = vision.scale_coordinates_to_screen(
                    sx, sy, scale_factor, pad_offset_x=pad_offset_x, pad_offset_y=pad_offset_y
                )
                ex, ey = vision.scale_coordinates_to_screen(
                    ex, ey, scale_factor, pad_offset_x=pad_offset_x, pad_offset_y=pad_offset_y
                )
                offset_x, offset_y = cu.screen.get_monitor_offset()
                sx += offset_x; sy += offset_y
                ex += offset_x; ey += offset_y
                await asyncio.to_thread(cu.mouse.move_to, sx, sy)
                await asyncio.to_thread(cu.mouse.drag_to, ex, ey)
                return f"Drag de ({sx},{sy}) vers ({ex},{ey})"

            elif name == "paste":
                text = str(params.get("text", ""))
                if text:
                    try:
                        import pyperclip
                        pyperclip.copy(text)
                    except Exception:
                        cu.keyboard.type_text(text)
                        return f"Texte collé (type_text fallback): {text[:50]}"
                await asyncio.to_thread(cu.keyboard.hotkey, "ctrl", "v")
                return "Collé"

            elif name == "clear_field":
                await asyncio.to_thread(cu.keyboard.hotkey, "ctrl", "a")
                await asyncio.sleep(0.05)
                await asyncio.to_thread(cu.keyboard.press_key, "delete")
                return "Champ vidé"

            elif name == "focus_window":
                title = str(params.get("title", ""))
                success = await asyncio.to_thread(cu.window.focus_window, title)
                if success:
                    return f"Fenêtre '{title}' au premier plan"
                return f"Fenêtre '{title}' introuvable"

            elif name == "wait":
                seconds = min(int(params.get("seconds", 2)), 5)
                await asyncio.sleep(seconds)
                return f"Attente de {seconds}s"

            elif name == "done":
                summary = str(params.get("summary", params.get("message", "Tâche terminée")))
                return f"DONE: {summary}"

            elif name == "error":
                return f"Erreur LLM: {params.get('reason', 'Inconnue')}"

            else:
                return f"Action inconnue: {name}"

        except Exception as e:
            logger.error(f"❌ CU Agent action error [{name}]: {e}")
            return f"Erreur d'exécution [{name}]: {e}"

    def _build_step_prompt(
        self, goal: str, steps: List[CUStepResult], screen_metadata: str, extra: str = ""
    ) -> str:
        """Construit le prompt pour une étape de la boucle."""
        # Historique résumé (5 dernières actions max)
        recent = steps[-5:] if steps else []
        history_lines = []
        for s in recent:
            status = "✅" if s.success else "❌"
            history_lines.append(f"  {status} {s.action} → {s.output[:100]}")
        history_str = "\n".join(history_lines) if history_lines else "(aucune action précédente)"

        extra_ctx = extra
        if not extra_ctx:
            if len(steps) >= 3:
                extra_ctx = "NOTE: Plusieurs étapes déjà effectuées. Vérifie si le but est atteint."

        return CU_STEP_PROMPT.format(
            screen_metadata=screen_metadata,
            n_steps=len(recent),
            action_history=history_str,
            extra_context=extra_ctx,
        )

    # ─── Main Loop ─────────────────────────────────────────────────────

    async def run(self, goal: str) -> CUTaskResult:
        """Exécute une tâche CU autonome.
        
        Args:
            goal: Description en langage naturel de la tâche à accomplir.
                  Ex: "Ouvre Chrome et va sur google.com"
                  Ex: "Remplis le formulaire avec nom=Lumena et email=lumena@ai.com"
        
        Returns:
            CUTaskResult avec le résumé, les étapes, et le statut.
        """
        logger.info(f"🖥️ CU Agent: Démarrage — \"{goal}\"")
        start_time = time.time()

        steps: List[CUStepResult] = []
        screenshot_hashes: List[str] = []
        unstuck_idx = 0
        consecutive_errors = 0  # Compteur d'erreurs de parsing consécutives
        system_prompt = CU_SYSTEM_PROMPT.format(goal=goal)

        for iteration in range(1, self.max_iterations + 1):
            iter_start = time.time()

            # ── Timeout global ──
            elapsed = time.time() - start_time
            if elapsed > self.timeout_seconds:
                logger.warning(f"⏱️ CU Agent: Timeout ({self.timeout_seconds}s)")
                return CUTaskResult(
                    goal=goal,
                    success=False,
                    summary=f"Timeout après {iteration - 1} itérations ({elapsed:.0f}s)",
                    steps=steps,
                    total_iterations=iteration - 1,
                    total_duration_ms=(time.time() - start_time) * 1000,
                    exit_reason="timeout",
                )

            logger.info(f"🔄 CU Agent: Itération {iteration}/{self.max_iterations}")

            # ── 1. Perception + Context Detection (P4) ──
            context = await self._detect_context()
            try:
                screenshot_path, scale_factor, pad_offset_x, pad_offset_y = await self._take_screenshot()
                screen_metadata = self._get_vision().get_screen_metadata()
                screenshot_hash = _image_hash(screenshot_path)
            except Exception as e:
                logger.error(f"❌ CU Agent: Erreur screenshot: {e}")
                steps.append(CUStepResult(
                    iteration=iteration,
                    action=CUAction(action="screenshot_error", thought=str(e)),
                    success=False,
                    output=f"Erreur screenshot: {e}",
                    duration_ms=(time.time() - iter_start) * 1000,
                ))
                continue

            # ── 2. State Acquisition (P4.2/4.3) ──
            self._current_dom_snapshot = None
            state_ctx = ""
            _dom_ok = False
            if context == "web":
                try:
                    from ..tools.playwright_browser import get_playwright_browser
                    from .dom_indexer import get_dom_indexer
                    browser = get_playwright_browser()
                    if (browser.is_running
                            and getattr(browser, '_page', None) is not None
                            and not browser._page.is_closed()):
                        indexer = get_dom_indexer()
                        snap = await indexer.snapshot(browser._page)
                        snap = await indexer.enrich_with_bboxes(browser._page, snap)
                        self._current_dom_snapshot = snap
                        state_ctx = "DOM INTERACTIF:\n" + snap.to_text()
                        _dom_ok = True
                except Exception as _dom_exc:
                    logger.debug(f"DOM snapshot échoué: {_dom_exc}")
            # Fallback UIA : contexte desktop OU web avec DOM échoué
            if context == "desktop" or (context == "web" and not _dom_ok):
                try:
                    from .cu_router import build_state_policy
                    cu = self._get_cu()
                    for source in build_state_policy("desktop"):
                        if source == "uia":
                            active_title = await asyncio.to_thread(cu.window.get_active_window)
                            controls = await asyncio.to_thread(cu.ui.list_controls, active_title or "")
                            if controls:
                                lines = [f"  [{i+1}] {c.get('name','?')} ({c.get('control_type','')})"
                                         for i, c in enumerate(controls[:30])]
                                state_ctx = "CONTRÔLES UI ACCESSIBLES:\n" + "\n".join(lines)
                                break
                except Exception as _uia_exc:
                    logger.debug(f"UIA state acquisition échoué: {_uia_exc}")

            # ── 3. Stuck Detection ──
            screenshot_hashes.append(screenshot_hash)
            if len(screenshot_hashes) >= STUCK_THRESHOLD:
                recent_hashes = screenshot_hashes[-STUCK_THRESHOLD:]
                all_similar = all(
                    _hamming_distance(recent_hashes[0], h) <= STUCK_HASH_TOLERANCE
                    for h in recent_hashes[1:]
                )
                if all_similar:
                    logger.warning(f"🔒 CU Agent: Stuck détecté ({STUCK_THRESHOLD} screenshots similaires)")
                    if unstuck_idx < len(UNSTUCK_ACTIONS):
                        unstuck_action = UNSTUCK_ACTIONS[unstuck_idx]
                        unstuck_idx += 1
                        logger.info(f"🔓 CU Agent: Déblocage → {unstuck_action}")
                        output = await self._execute_action(
                            unstuck_action,
                            scale_factor,
                            pad_offset_x=pad_offset_x,
                            pad_offset_y=pad_offset_y,
                        )
                        steps.append(CUStepResult(
                            iteration=iteration,
                            action=unstuck_action,
                            success=True,
                            output=f"[UNSTUCK] {output}",
                            screenshot_hash=screenshot_hash,
                            duration_ms=(time.time() - iter_start) * 1000,
                        ))
                        # Reset hashes après déblocage
                        screenshot_hashes.clear()
                        continue
                    else:
                        logger.error("🔒 CU Agent: Toutes les stratégies de déblocage épuisées")
                        return CUTaskResult(
                            goal=goal,
                            success=False,
                            summary=f"Bloqué après {iteration} itérations — toutes les stratégies de déblocage ont échoué",
                            steps=steps,
                            total_iterations=iteration,
                            total_duration_ms=(time.time() - start_time) * 1000,
                            exit_reason="stuck",
                        )

            # ── 4. Raisonnement (LLM Vision) ──
            # Combiner state_ctx (DOM/UIA) avec la note "plusieurs étapes"
            extra_parts = []
            if state_ctx:
                extra_parts.append(state_ctx)
            if len(steps) >= 3:
                extra_parts.append("NOTE: Plusieurs étapes déjà effectuées. Vérifie si le but est atteint.")
            combined_extra = "\n\n".join(extra_parts)
            step_prompt = self._build_step_prompt(goal, steps, screen_metadata, extra=combined_extra)
            full_prompt = system_prompt + "\n\n" + step_prompt

            try:
                llm_result = await self._call_vision_llm(screenshot_path, full_prompt)
            except Exception as e:
                logger.error(f"❌ CU Agent: Erreur LLM: {e}")
                steps.append(CUStepResult(
                    iteration=iteration,
                    action=CUAction(action="llm_error", thought=str(e)),
                    success=False,
                    output=f"Erreur LLM: {e}",
                    screenshot_hash=screenshot_hash,
                    duration_ms=(time.time() - iter_start) * 1000,
                ))
                continue

            if not llm_result.get("success"):
                error = llm_result.get("error", "Inconnue")
                logger.warning(f"⚠️ CU Agent: LLM a échoué: {error}")
                steps.append(CUStepResult(
                    iteration=iteration,
                    action=CUAction(action="llm_error", thought=error),
                    success=False,
                    output=f"LLM indisponible: {error}",
                    screenshot_hash=screenshot_hash,
                    duration_ms=(time.time() - iter_start) * 1000,
                ))
                continue

            # ── 4. Parse la décision du LLM ──
            raw_answer = llm_result.get("answer", "")
            action = _parse_cu_action(raw_answer)
            logger.info(f"🧠 CU Agent: {action.thought[:100] if action.thought else '(pas de thought)'}")
            logger.info(f"▶️  CU Agent: {action}")
            # Compteur d'erreurs de parsing consécutives
            if action.action == "error":
                consecutive_errors += 1
                logger.warning(f"\u26a0\ufe0f CU Agent: Erreur parsing {consecutive_errors}/{MAX_CONSECUTIVE_ERRORS}")
                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    logger.error(f"\u274c CU Agent: {MAX_CONSECUTIVE_ERRORS} erreurs de parsing consécutives \u2014 LLM vision incompatible")
                    steps.append(CUStepResult(
                        iteration=iteration,
                        action=action,
                        success=False,
                        output=f"Abandon: {MAX_CONSECUTIVE_ERRORS} erreurs de parsing consécutives (LLM vision inadapté)",
                        screenshot_hash=screenshot_hash,
                        duration_ms=(time.time() - iter_start) * 1000,
                    ))
                    return CUTaskResult(
                        goal=goal,
                        success=False,
                        summary=f"LLM vision ne produit pas de JSON valide après {consecutive_errors} tentatives",
                        steps=steps,
                        total_iterations=iteration,
                        total_duration_ms=(time.time() - start_time) * 1000,
                        exit_reason="llm_parse_failure",
                    )
            else:
                consecutive_errors = 0  # Reset si action valide
            # ── 5. Vérifier si c'est "done" ──
            if action.action == "done":
                summary = action.params.get("summary", action.params.get("message", "Tâche terminée"))
                logger.info(f"✅ CU Agent: But accompli — {summary}")
                steps.append(CUStepResult(
                    iteration=iteration,
                    action=action,
                    success=True,
                    output=f"DONE: {summary}",
                    screenshot_hash=screenshot_hash,
                    duration_ms=(time.time() - iter_start) * 1000,
                ))
                return CUTaskResult(
                    goal=goal,
                    success=True,
                    summary=summary,
                    steps=steps,
                    total_iterations=iteration,
                    total_duration_ms=(time.time() - start_time) * 1000,
                    exit_reason="done",
                )

            # ── 6. Exécuter l'action ──
            try:
                output = await asyncio.wait_for(
                    self._execute_action(
                        action,
                        scale_factor,
                        pad_offset_x=pad_offset_x,
                        pad_offset_y=pad_offset_y,
                    ),
                    timeout=STEP_ACTION_TIMEOUT,
                )
                action_success = "Erreur" not in output
            except asyncio.TimeoutError:
                output = f"Timeout ({STEP_ACTION_TIMEOUT}s) pour {action}"
                action_success = False
            except Exception as e:
                output = f"Exception: {e}"
                action_success = False

            logger.info(f"{'✅' if action_success else '❌'} CU Agent: {output[:100]}")

            steps.append(CUStepResult(
                iteration=iteration,
                action=action,
                success=action_success,
                output=output,
                screenshot_hash=screenshot_hash,
                duration_ms=(time.time() - iter_start) * 1000,
            ))

            # Reset unstuck si action réussie ET si la screen a changé
            # (ne pas reset si même action répétée qui causait le stuck)
            _last_stuck_action = steps[-unstuck_idx].action.action if unstuck_idx > 0 and len(steps) >= unstuck_idx else None
            if action_success and action.action != "error" and action.action != _last_stuck_action:
                unstuck_idx = 0

            # Petit délai pour laisser l'UI réagir
            await asyncio.sleep(0.5)

        # ── Max itérations atteintes ──
        logger.warning(f"⚠️ CU Agent: Max itérations ({self.max_iterations}) atteintes")
        return CUTaskResult(
            goal=goal,
            success=False,
            summary=f"Max itérations ({self.max_iterations}) atteintes sans compléter le but",
            steps=steps,
            total_iterations=self.max_iterations,
            total_duration_ms=(time.time() - start_time) * 1000,
            exit_reason="max_iterations",
        )


# ─── Singleton ─────────────────────────────────────────────────────────────

import threading

_agent_loop: Optional[CUAgentLoop] = None
_agent_lock = threading.Lock()


def get_cu_agent_loop() -> CUAgentLoop:
    """Obtient l'instance singleton de CUAgentLoop (thread-safe)."""
    global _agent_loop
    if _agent_loop is None:
        with _agent_lock:
            if _agent_loop is None:
                _agent_loop = CUAgentLoop()
    return _agent_loop
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU General Public License v3.0 (GPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
