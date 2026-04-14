"""
computer_use.py - Handlers computer-use fragmentés depuis react.py.

Handlers (29):
  screenshot, click, type_text, open_app, close_app,
  cursor_ide_local, hotkey, get_active_window, double_click,
  scroll, move_mouse, press_key, close_window, wait,
  spotify_play, open_url, list_windows, drag,
  screenshot_analyze, click_element, find_element, zoom,
  computer_task, list_screens, set_screen, ui_click, ui_type,
  ui_list_controls, mouse_pattern.

Chaque handler est une fonction async standalone:
    async def handler_name(ctx: HandlerContext, **kwargs) -> HandlerResult
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

from .context import HandlerContext
from .contracts import HandlerResult
from .registry_v2 import HandlerDef


# ─── Platform constant ────────────────────────────────────────────────────

IS_WINDOWS = sys.platform.startswith("win")


# ─── Helpers (cursor IDE) ─────────────────────────────────────────────────

def _resolve_cursor_ide_root(ctx: HandlerContext) -> Path:
    """Résout le dossier racine de cursor-ide-local."""
    env_override = os.getenv("LUMENA_CURSOR_IDE_PATH", "").strip()
    candidates: List[Path] = []
    if env_override:
        candidates.append(Path(env_override))
    candidates.append(ctx.lumena_root.parent / "cursor-ide-local")
    candidates.append(ctx.lumena_root / "cursor-ide-local")

    for candidate in candidates:
        if candidate.exists() and (candidate / "package.json").exists():
            return candidate.resolve()

    if candidates:
        return candidates[0].resolve()
    return (ctx.lumena_root / "cursor-ide-local").resolve()


def _list_cursor_ide_processes() -> List[Dict[str, Any]]:
    """Détecte les processus liés à cursor-ide-local."""
    if IS_WINDOWS:
        ps_script = (
            "$procs = Get-CimInstance Win32_Process | Where-Object { "
            "(($_.CommandLine -ne $null) -and ($_.CommandLine -like '*cursor-ide-local*')) "
            "-or ($_.Name -like 'cursor-ide-local*') "
            "-or ($_.Name -like 'Lumena IDE*') "
            "} | Select-Object -First 12 ProcessId, Name, CommandLine; "
            "$procs | ConvertTo-Json -Compress"
        )
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_script],
                capture_output=True,
                text=True,
                timeout=6,
                encoding="utf-8",
                errors="replace",
            )
            payload = (result.stdout or "").strip()
            if not payload:
                return []
            parsed = json.loads(payload)
            if isinstance(parsed, dict):
                parsed = [parsed]
            if not isinstance(parsed, list):
                return []
            return [
                {
                    "pid": item.get("ProcessId"),
                    "name": item.get("Name"),
                    "command": (item.get("CommandLine") or "")[:180],
                }
                for item in parsed
                if isinstance(item, dict)
            ]
        except Exception:
            return []

    # Linux / macOS
    try:
        result = subprocess.run(
            ["ps", "-eo", "pid=,command="],
            capture_output=True,
            text=True,
            timeout=6,
            encoding="utf-8",
            errors="replace",
        )
        entries: List[Dict[str, Any]] = []
        for raw_line in (result.stdout or "").splitlines():
            line = raw_line.strip()
            if "cursor-ide-local" not in line.lower():
                continue
            parts = line.split(maxsplit=1)
            if not parts:
                continue
            pid = parts[0]
            cmd = parts[1] if len(parts) > 1 else ""
            entries.append({"pid": pid, "name": "process", "command": cmd[:180]})
        return entries
    except Exception:
        return []


def _cursor_ide_status() -> Dict[str, Any]:
    """Retourne le statut de cursor-ide-local."""
    processes = _list_cursor_ide_processes()
    return {
        "running": len(processes) > 0,
        "process_count": len(processes),
        "processes": processes,
    }


def _prepare_cursor_workspace_path(
    ctx: HandlerContext,
    workspace_path: str = "",
    create_if_missing: bool = True,
) -> Optional[Path]:
    """Prépare un chemin de workspace pour l'IDE local."""
    cleaned = (workspace_path or "").strip()
    if not cleaned:
        return ctx.runtime_root

    requested = Path(cleaned).expanduser()
    if not requested.is_absolute():
        requested = (ctx.lumena_root / requested).resolve()
    else:
        requested = requested.resolve()

    if create_if_missing:
        requested.mkdir(parents=True, exist_ok=True)
    if not requested.exists() or not requested.is_dir():
        raise ValueError(f"Workspace invalide: {requested}")
    return requested


def _launch_cursor_ide_process(
    ide_root: Path,
    workspace_path: Optional[Path],
) -> tuple:
    """Lance cursor-ide-local en arrière-plan. Retourne (ok: bool, message: str)."""
    launcher_candidates = [
        ide_root / "LANCER_IDE.bat",
        ide_root / "LANCER_IDE_COMPLET.bat",
    ]
    launcher = next((p for p in launcher_candidates if p.exists()), None)

    env = os.environ.copy()
    if workspace_path:
        env["CURSOR_IDE_WORKSPACE"] = str(workspace_path)

    popen_kwargs: Dict[str, Any] = {
        "cwd": str(ide_root),
        "env": env,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if IS_WINDOWS:
        creationflags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0
        )
        if creationflags:
            popen_kwargs["creationflags"] = creationflags

    try:
        if IS_WINDOWS and launcher is not None:
            subprocess.Popen(
                ["cmd", "/c", "start", "", "cmd", "/c", str(launcher)],
                **popen_kwargs,
            )
            return True, f"Lancement de Cursor IDE Local via {launcher.name}"

        npm_cmd = ["npm", "run", "electron:dev"]
        if workspace_path:
            npm_cmd.extend(["--", f"--workspace={workspace_path}"])
        subprocess.Popen(npm_cmd, **popen_kwargs)
        return True, "Lancement de Cursor IDE Local via npm run electron:dev"
    except Exception as exc:
        return False, f"Echec lancement IDE: {exc}"


async def _focus_cursor_ide_window() -> bool:
    """Tente de donner le focus à la fenêtre de l'IDE."""
    try:
        from ...computer_use import get_computer_use
        cu = get_computer_use()
        titles = ["Lumena IDE", "Cursor IDE Local", "cursor-ide-local"]
        for title in titles:
            if cu.window.focus_window(title):
                return True
    except Exception:
        return False
    return False


# ─── Helpers (close_app) ──────────────────────────────────────────────────

def _resolve_close_targets(name: str, close_terminals: bool) -> List[str]:
    """Résout les cibles de fermeture à partir du nom donné."""
    raw = (name or "").strip().lower()
    aliases = {
        "cmd": ["cmd.exe"],
        "commande": ["cmd.exe"],
        "powershell": ["powershell.exe", "pwsh.exe"],
        "terminal": ["cmd.exe", "powershell.exe", "pwsh.exe", "wt.exe", "windowsterminal.exe"],
        "terminals": ["cmd.exe", "powershell.exe", "pwsh.exe", "wt.exe", "windowsterminal.exe"],
        "notepad": ["notepad.exe"],
        "bloc-notes": ["notepad.exe"],
        "calculator": ["calc.exe"],
        "calculatrice": ["calc.exe"],
        "chrome": ["chrome.exe"],
        "edge": ["msedge.exe"],
        "discord": ["discord.exe"],
        "explorer": ["explorer.exe"],
    }

    targets: List[str] = []
    if raw in aliases:
        targets.extend(aliases[raw])
    elif raw:
        if raw.endswith(".exe"):
            targets.append(raw)
        else:
            targets.append(f"{raw}.exe")

    if close_terminals:
        targets.extend(aliases["terminal"])

    deny = {"system", "wininit.exe", "services.exe", "lsass.exe", "csrss.exe", "smss.exe"}
    unique_targets: List[str] = []
    for item in targets:
        val = (item or "").strip().lower()
        if not val or val in deny:
            continue
        if val not in unique_targets:
            unique_targets.append(val)
    return unique_targets


# ─── Handlers ──────────────────────────────────────────────────────────────

async def screenshot(ctx: HandlerContext) -> HandlerResult:
    """Prend une capture d'écran."""
    try:
        from ...computer_use import get_computer_use
        cu = get_computer_use()
        path = await cu.take_screenshot()
        if path:
            return HandlerResult.ok(f"Screenshot sauvegardé: {path}")
        return HandlerResult.fail("Impossible de capturer l'écran")
    except Exception as e:
        return HandlerResult.fail(f"Erreur screenshot: {e}")


async def click(ctx: HandlerContext, *, x: int, y: int, button: str = "left") -> HandlerResult:
    """Clique à une position."""
    try:
        from ...computer_use import get_computer_use
        cu = get_computer_use()
        cu.mouse.click(int(x), int(y), button=button)
        return HandlerResult.ok(f"Clic {button} effectué à ({x}, {y})")
    except Exception as e:
        return HandlerResult.fail(f"Erreur clic: {e}")


async def type_text(ctx: HandlerContext, *, text: str) -> HandlerResult:
    """Tape du texte au clavier."""
    try:
        from ...computer_use import get_computer_use
        cu = get_computer_use()
        cu.keyboard.type_text(text)
        return HandlerResult.ok(f"Texte tapé: {text[:50]}...")
    except Exception as e:
        return HandlerResult.fail(f"Erreur frappe: {e}")


async def open_app(ctx: HandlerContext, *, name: str) -> HandlerResult:
    """Ouvre une application."""
    try:
        from ...computer_use import get_computer_use
        cu = get_computer_use()
        await cu.open_application(name)
        normalized = (name or "").strip().lower()
        if normalized:
            ctx._opened_apps_history.append(normalized)
            if len(ctx._opened_apps_history) > 30:
                ctx._opened_apps_history = ctx._opened_apps_history[-30:]
        return HandlerResult.ok(f"Application ouverte: {name}")
    except Exception as e:
        return HandlerResult.fail(f"Erreur ouverture app: {e}")


async def close_app(
    ctx: HandlerContext,
    *,
    name: str = "",
    close_terminals: bool = False,
    force: bool = True,
) -> HandlerResult:
    """Ferme une application (ou les terminaux) pour éviter la saturation."""
    try:
        targets = _resolve_close_targets(name=name, close_terminals=close_terminals)
        if not targets and ctx._opened_apps_history:
            last_name = ctx._opened_apps_history[-1]
            targets = _resolve_close_targets(name=last_name, close_terminals=False)

        if not targets:
            return HandlerResult.fail("close_app: cible vide (ex: name='cmd' ou close_terminals=true)")

        closed: List[str] = []
        failed: List[str] = []

        if IS_WINDOWS:
            for target in targets:
                cmd = ["taskkill", "/IM", target, "/T"]
                if force:
                    cmd.append("/F")
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=8,
                    encoding="utf-8",
                    errors="replace",
                )
                if result.returncode == 0:
                    closed.append(target)
                else:
                    failed.append(target)
        else:
            for target in targets:
                proc_name = target.replace(".exe", "")
                cmd = ["pkill", "-f", proc_name]
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=8,
                    encoding="utf-8",
                    errors="replace",
                )
                if result.returncode == 0:
                    closed.append(target)
                else:
                    failed.append(target)

        if closed:
            ctx._opened_apps_history = [
                x for x in ctx._opened_apps_history
                if f"{x}.exe" not in closed and x not in closed
            ]

        lines = [
            "🧹 Nettoyage applications terminé",
            f"- fermées: {', '.join(closed) if closed else '-'}",
            f"- introuvables/échouées: {', '.join(failed) if failed else '-'}",
        ]
        return HandlerResult.ok("\n".join(lines))
    except Exception as e:
        return HandlerResult.fail(f"Erreur close_app: {e}")


async def cursor_ide_local(
    ctx: HandlerContext,
    *,
    action: str = "status",
    workspace_path: str = "",
    create_if_missing: bool = True,
) -> HandlerResult:
    """
    Gère cursor-ide-local.

    Actions:
    - status: vérifier si l'IDE tourne
    - ensure_open/open: ouvrir l'IDE s'il est fermé
    - focus: focus sur la fenêtre IDE
    - ensure_workspace: démarrer une instance avec workspace cible
    """
    requested_action = (action or "status").strip().lower()
    normalized = {
        "open": "ensure_open",
        "launch": "ensure_open",
    }.get(requested_action, requested_action)
    allowed_actions = {"status", "ensure_open", "focus", "ensure_workspace"}
    if normalized not in allowed_actions:
        return HandlerResult.fail(
            "Erreur action cursor_ide_local: utilise status, ensure_open, "
            "focus ou ensure_workspace."
        )

    ide_root = _resolve_cursor_ide_root(ctx)
    if not ide_root.exists():
        return HandlerResult.fail(
            f"Erreur: dossier cursor-ide-local introuvable ({ide_root}). "
            "Définis LUMENA_CURSOR_IDE_PATH ou vérifie le repo."
        )

    status = _cursor_ide_status()
    if normalized == "status":
        return HandlerResult.ok(
            f"Cursor IDE Local status: running={status['running']}, "
            f"process_count={status['process_count']}, root={ide_root}"
        )

    if normalized == "focus":
        if not status["running"]:
            return HandlerResult.fail("Cursor IDE Local n'est pas ouvert. Utilise action=ensure_open.")
        focused = await _focus_cursor_ide_window()
        return HandlerResult.ok(
            "Cursor IDE Local focus OK." if focused else "IDE ouvert mais focus non confirmé."
        )

    try:
        workspace = _prepare_cursor_workspace_path(
            ctx,
            workspace_path=workspace_path,
            create_if_missing=bool(create_if_missing),
        )
    except Exception as exc:
        return HandlerResult.fail(f"Erreur workspace cursor_ide_local: {exc}")

    if normalized == "ensure_open" and status["running"]:
        focused = await _focus_cursor_ide_window()
        return HandlerResult.ok(
            f"Cursor IDE Local déjà ouvert (workspace cible: {workspace}). "
            + ("Focus appliqué." if focused else "Focus non confirmé.")
        )

    ok, message = _launch_cursor_ide_process(
        ide_root=ide_root,
        workspace_path=workspace,
    )
    if not ok:
        return HandlerResult.fail(f"Erreur cursor_ide_local: {message}")

    await asyncio.sleep(1.5)
    after = _cursor_ide_status()
    return HandlerResult.ok(
        f"{message}. workspace={workspace}. "
        f"running={after['running']} process_count={after['process_count']}"
    )


async def hotkey(ctx: HandlerContext, *, keys: str = None, input: str = None) -> HandlerResult:
    """Exécute un raccourci clavier (ex: 'ctrl+c')."""
    try:
        # Support 'input' comme alias de 'keys' (compatibilité DeepSeek)
        hotkey_val = keys or input
        if not hotkey_val:
            return HandlerResult.fail("Paramètre 'keys' ou 'input' requis")

        # Parser JSON si DeepSeek envoie un objet stringifié
        if isinstance(hotkey_val, str) and hotkey_val.strip().startswith('{'):
            try:
                data = json.loads(hotkey_val.strip())
                hotkey_val = data.get('keys') or data.get('input') or data.get('key') or hotkey_val
            except json.JSONDecodeError:
                pass  # not JSON, use raw string

        from ...computer_use import get_computer_use
        cu = get_computer_use()
        key_list = str(hotkey_val).lower().split("+")
        cu.keyboard.hotkey(*key_list)
        return HandlerResult.ok(f"Raccourci exécuté: {hotkey_val}")
    except Exception as e:
        return HandlerResult.fail(f"Erreur raccourci: {e}")


async def get_active_window(ctx: HandlerContext) -> HandlerResult:
    """Retourne le titre de la fenêtre active."""
    try:
        from ...computer_use import get_computer_use
        cu = get_computer_use()
        title = cu.window.get_active_window()
        return HandlerResult.ok(f"Fenêtre active: {title}")
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def double_click(ctx: HandlerContext, *, x: int, y: int) -> HandlerResult:
    """Double-clic à une position."""
    try:
        from ...computer_use import get_computer_use
        cu = get_computer_use()
        cu.mouse.double_click(int(x), int(y))
        return HandlerResult.ok(f"Double-clic effectué à ({x}, {y})")
    except Exception as e:
        return HandlerResult.fail(f"Erreur double-clic: {e}")


async def scroll(ctx: HandlerContext, *, direction: str, amount: int = 3) -> HandlerResult:
    """Scroll vers le haut ou le bas."""
    try:
        from ...computer_use import get_computer_use
        cu = get_computer_use()
        scroll_amount = amount if direction.lower() == "up" else -amount
        cu.mouse.scroll(scroll_amount)
        return HandlerResult.ok(f"Scroll {direction} de {amount}")
    except Exception as e:
        return HandlerResult.fail(f"Erreur scroll: {e}")


async def move_mouse(ctx: HandlerContext, *, x: int, y: int) -> HandlerResult:
    """Déplace la souris vers une position."""
    try:
        from ...computer_use import get_computer_use
        cu = get_computer_use()
        cu.mouse.move_to(int(x), int(y))
        return HandlerResult.ok(f"Souris déplacée vers ({x}, {y})")
    except Exception as e:
        return HandlerResult.fail(f"Erreur déplacement: {e}")


async def press_key(ctx: HandlerContext, *, key: str = None, input: str = None) -> HandlerResult:
    """Appuie sur une touche (enter, tab, escape, etc.)."""
    try:
        # Support 'input' comme alias de 'key' (compatibilité DeepSeek)
        pressed_key = key or input
        if not pressed_key:
            return HandlerResult.fail("Paramètre 'key' ou 'input' requis")

        # Parser JSON si DeepSeek envoie un objet stringifié
        if pressed_key.startswith('{'):
            try:
                data = json.loads(pressed_key)
                pressed_key = data.get('key') or data.get('input') or pressed_key
            except (json.JSONDecodeError, ValueError):
                pass  # not JSON, use raw string

        from ...computer_use import get_computer_use
        cu = get_computer_use()
        cu.keyboard.press_key(pressed_key)
        return HandlerResult.ok(f"Touche pressée: {pressed_key}")
    except Exception as e:
        return HandlerResult.fail(f"Erreur touche: {e}")


async def close_window(ctx: HandlerContext) -> HandlerResult:
    """Ferme la fenêtre active (Alt+F4)."""
    try:
        from ...computer_use import get_computer_use
        cu = get_computer_use()
        cu.keyboard.hotkey("alt", "f4")
        return HandlerResult.ok("Fenêtre fermée")
    except Exception as e:
        return HandlerResult.fail(f"Erreur fermeture: {e}")


async def wait(ctx: HandlerContext, *, seconds: int = 2) -> HandlerResult:
    """Attend un nombre de secondes (compatible Windows)."""
    try:
        wait_time = min(int(seconds), 10)  # Max 10 secondes
        await asyncio.sleep(wait_time)
        return HandlerResult.ok(f"Attendu {wait_time} secondes")
    except Exception as e:
        return HandlerResult.fail(f"Erreur attente: {e}")


async def spotify_play(ctx: HandlerContext, *, query: str) -> HandlerResult:
    """Recherche et joue sur Spotify avec navigation clavier (plus fiable que vision)."""
    try:
        from ...computer_use import get_computer_use
        cu = get_computer_use()

        # 1. Ouvrir/focus Spotify via Windows Search
        cu.keyboard.hotkey("win", "s")
        await asyncio.sleep(0.8)
        cu.keyboard.type_text("Spotify")
        await asyncio.sleep(0.5)
        cu.keyboard.press_key("enter")
        await asyncio.sleep(3)

        # 2. Ouvrir la recherche Spotify avec Ctrl+K
        cu.keyboard.hotkey("ctrl", "k")
        await asyncio.sleep(0.5)

        # 3. Effacer et taper la recherche
        cu.keyboard.hotkey("ctrl", "a")
        await asyncio.sleep(0.2)
        cu.keyboard.type_text(query)
        await asyncio.sleep(2.5)

        # 4. Sélectionner le premier résultat
        cu.keyboard.press_key("enter")
        await asyncio.sleep(2)

        # 5. Lancer la lecture
        cu.keyboard.press_key("enter")
        await asyncio.sleep(1)

        # 6. Fallback: Espace pour play
        cu.keyboard.press_key("space")
        await asyncio.sleep(0.5)

        return HandlerResult.ok(f"✅ Spotify: '{query}' recherché et lecture lancée (clavier) !")
    except Exception as e:
        return HandlerResult.fail(f"Erreur Spotify: {e}")


async def open_url(ctx: HandlerContext, *, url: str) -> HandlerResult:
    """Ouvre une URL dans le navigateur par défaut."""
    from ...utils.url_safety import assert_url_safe
    try:
        assert_url_safe(url)
    except ValueError as e:
        return HandlerResult.fail(f"URL bloquée: {e}")
    try:
        import webbrowser
        webbrowser.open(url)
        return HandlerResult.ok(f"URL ouverte: {url}")
    except Exception as e:
        return HandlerResult.fail(f"Erreur ouverture URL: {e}")


async def list_windows(ctx: HandlerContext) -> HandlerResult:
    """Liste les fenêtres ouvertes."""
    try:
        from ...computer_use import get_computer_use
        cu = get_computer_use()
        windows = cu.window.list_windows()
        if windows:
            return HandlerResult.ok("Fenêtres ouvertes:\n" + "\n".join([f"- {w}" for w in windows[:10]]))
        return HandlerResult.ok("Aucune fenêtre trouvée")
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def drag(ctx: HandlerContext, *, start_x: int, start_y: int, end_x: int, end_y: int) -> HandlerResult:
    """Glisser-déposer d'une position à une autre."""
    try:
        from ...computer_use import get_computer_use
        cu = get_computer_use()
        cu.mouse.move_to(int(start_x), int(start_y))
        cu.mouse.drag_to(int(end_x), int(end_y))
        return HandlerResult.ok(f"Glissé de ({start_x}, {start_y}) à ({end_x}, {end_y})")
    except Exception as e:
        return HandlerResult.fail(f"Erreur drag: {e}")


async def screenshot_analyze(ctx: HandlerContext, *, question: str = None, file_path: str = None) -> HandlerResult:
    """Analyse un screenshot avec vision LLM. Si file_path est fourni, analyse ce fichier PNG existant
    sans prendre de nouveau screenshot. Sinon, capture l'écran live."""
    try:
        from ...computer_use.vision import get_vision

        vision = get_vision()
        screen_meta = vision.get_screen_metadata()

        if file_path:
            # Analyser un fichier PNG existant sans capturer l'écran
            if not os.path.isfile(file_path):
                return HandlerResult.fail(f"Fichier introuvable: {file_path}")
            screenshot_path = file_path
            header = f"🖼️ Analyse du fichier: {os.path.basename(file_path)}"
        else:
            # Prendre un nouveau screenshot de l'écran live
            from ...computer_use import get_computer_use
            import tempfile
            from datetime import datetime

            cu = get_computer_use()
            screenshot_dir = tempfile.gettempdir()
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            screenshot_path = os.path.join(screenshot_dir, f"lumena_vision_{timestamp}.png")
            await cu.take_screenshot(screenshot_path)
            header = f"📸 {screen_meta}"

        base_question = question or "Décris ce que tu vois sur cet écran. Quelle application est ouverte? Quels éléments sont visibles?"
        prompt = f"{screen_meta}\n{base_question}"

        # P3.6 — router unifié (remplace cascade hardcodée Gemini→Claude→Ollama→OCR)
        from ...computer_use.cu_router import route_cu_vision
        result = await route_cu_vision(vision, screenshot_path, prompt, capability="vision_describe")

        # Dernier recours : OCR local si tous les LLM ont échoué
        if not result.get("success"):
            logger.debug(f"Tous LLM échoués, fallback OCR local…")
            try:
                from PIL import Image as _PILImage
                img = _PILImage.open(screenshot_path)
                ocr_text = vision.analyzer.extract_text(img)
                if ocr_text and ocr_text.strip():
                    result = {"success": True, "text": f"[OCR local — pas d'analyse IA]\nTexte détecté:\n{ocr_text.strip()}"}
                else:
                    result = {"success": False, "error": "OCR n'a détecté aucun texte"}
            except Exception as ocr_err:
                result = {"success": False, "error": f"OCR échoué: {ocr_err}"}

        if result.get("success"):
            answer = result.get("text") or result.get("answer", "Pas de réponse")
            return HandlerResult.ok(f"{header}\nAnalyse:\n{answer}")
        return HandlerResult.fail(f"Erreur analyse: {result.get('error', 'Inconnue')}")
    except Exception as e:
        return HandlerResult.fail(f"Erreur screenshot_analyze: {e}")


async def click_element(ctx: HandlerContext, *, element: str) -> HandlerResult:
    """Trouve un élément par description et clique dessus (state-first + self-healing 5 étapes).
    
    Pipeline state-first :
      0a. DOM index (si contexte web + Playwright actif) — sans coordonnées visuelles
      0b. UIA direct (si contexte desktop) — sans coordonnées visuelles
      1. Vision grounding direct (find_element_coordinates via router)
      2. Retry direct
      3. Scroll down 300px → vision
      4. Scroll up 600px → vision
      5. OCR local (pytesseract)
      6. UI Automation fallback
    """
    import tempfile
    from datetime import datetime

    STEP_TIMEOUT = 3.0

    try:
        from ...computer_use import get_computer_use
        from ...computer_use.vision import get_vision

        cu = get_computer_use()
        vision = get_vision()
        screenshot_dir = tempfile.gettempdir()

        # ── Détection contexte (web / desktop) ──────────────────────
        async def _detect_ctx() -> str:
            try:
                from ...tools.playwright_browser import get_playwright_browser
                b = get_playwright_browser()
                if (b.is_running
                        and getattr(b, '_page', None) is not None
                        and not b._page.is_closed()):
                    return "web"
            except Exception:
                pass
            _HINTS = {"chrome", "firefox", "edge", "brave", "arc", "opera", "safari", "chromium", "vivaldi"}
            try:
                t = await asyncio.to_thread(cu.window.get_active_window)
                if t and any(h in t.lower() for h in _HINTS):
                    return "web"
            except Exception:
                pass
            return "desktop"

        context = await _detect_ctx()

        async def _try_vision_find(step_label: str) -> Optional[Dict[str, Any]]:
            try:
                ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                spath = os.path.join(screenshot_dir, f"lumena_click_{ts}.png")
                await cu.take_screenshot(spath)
                result = await vision.find_element_coordinates(spath, element)
                if result.get("success") and result.get("found"):
                    return result
                logger.debug(f"🔍 [{step_label}] élément '{element}' non trouvé")
            except Exception as exc:
                logger.debug(f"🔍 [{step_label}] erreur: {exc}")
            return None

        # ── Étape 0a : DOM index (web) ───────────────────────────────
        if context == "web":
            try:
                from ...tools.playwright_browser import get_playwright_browser
                from ...computer_use.dom_indexer import get_dom_indexer
                import difflib
                browser = get_playwright_browser()
                if (browser.is_running
                        and getattr(browser, '_page', None) is not None
                        and not browser._page.is_closed()):
                    indexer = get_dom_indexer()
                    snap = await indexer.snapshot(browser._page)
                    snap = await indexer.enrich_with_bboxes(browser._page, snap)
                    # Chercher l'élément par correspondance de nom
                    elem_low = element.lower()
                    best = None
                    best_ratio = 0.0
                    for e in snap.elements:
                        candidate = (e.name or e.description or "").lower()
                        ratio = difflib.SequenceMatcher(None, elem_low, candidate).ratio()
                        if ratio > best_ratio:
                            best_ratio = ratio
                            best = e
                    if best and best.center and best_ratio >= 0.5:
                        x, y = best.center
                        offset_x, offset_y = cu.screen.get_monitor_offset()
                        cu.mouse.click(x + offset_x, y + offset_y)
                        return HandlerResult.ok(
                            f"✅ Clic sur '{element}' via DOM [{best.index}] '{best.name}' "
                            f"à ({x + offset_x}, {y + offset_y}) (ratio={best_ratio:.2f})"
                        )
            except Exception as exc:
                logger.debug(f"🔍 [dom] erreur: {exc}")

        # ── Étape 0b : UIA direct (desktop) ─────────────────────────
        elif context == "desktop":
            try:
                ui_success = cu.ui.click_element_by_name(element)
                if ui_success:
                    return HandlerResult.ok(
                        f"✅ Clic sur '{element}' via UI Automation (state-first)"
                    )
            except Exception as exc:
                logger.debug(f"🔍 [uia-direct] erreur: {exc}")

        # ── Étape 1 : Vision grounding direct ───────────────────────
        try:
            result = await asyncio.wait_for(_try_vision_find("direct"), timeout=STEP_TIMEOUT)
        except asyncio.TimeoutError:
            logger.debug(f"🔍 [direct] timeout ({STEP_TIMEOUT}s)")
            result = None

        if result:
            x, y = result.get("x", 0), result.get("y", 0)
            confidence = result.get("confidence", "low")
            offset_x, offset_y = cu.screen.get_monitor_offset()
            cu.mouse.click(x + offset_x, y + offset_y)
            return HandlerResult.ok(
                f"✅ Clic sur '{element}' à ({x + offset_x}, {y + offset_y}) - confiance: {confidence}"
            )

        # ── Étape 2 : Retry direct ───────────────────────────────────
        logger.info(f"🔄 Self-healing [{element}] step 2/6: retry direct")
        try:
            result = await asyncio.wait_for(_try_vision_find("retry"), timeout=STEP_TIMEOUT)
        except asyncio.TimeoutError:
            result = None

        if result:
            x, y = result.get("x", 0), result.get("y", 0)
            confidence = result.get("confidence", "low")
            offset_x, offset_y = cu.screen.get_monitor_offset()
            cu.mouse.click(x + offset_x, y + offset_y)
            return HandlerResult.ok(
                f"✅ Clic sur '{element}' à ({x + offset_x}, {y + offset_y}) - confiance: {confidence} (retry)"
            )

        # ── Étape 3 : Scroll down 300px → vision ────────────────────
        logger.info(f"🔄 Self-healing [{element}] step 3/6: scroll down + vision")
        try:
            async def _scroll_down_and_find():
                cu.mouse.scroll(-3)
                await asyncio.sleep(0.3)
                return await _try_vision_find("scroll_down")
            result = await asyncio.wait_for(_scroll_down_and_find(), timeout=STEP_TIMEOUT)
        except asyncio.TimeoutError:
            result = None

        if result:
            x, y = result.get("x", 0), result.get("y", 0)
            confidence = result.get("confidence", "low")
            offset_x, offset_y = cu.screen.get_monitor_offset()
            cu.mouse.click(x + offset_x, y + offset_y)
            return HandlerResult.ok(
                f"✅ Clic sur '{element}' à ({x + offset_x}, {y + offset_y}) - confiance: {confidence} (après scroll ↓)"
            )

        # ── Étape 4 : Scroll up 600px → vision ──────────────────────
        logger.info(f"🔄 Self-healing [{element}] step 4/6: scroll up + vision")
        try:
            async def _scroll_up_and_find():
                cu.mouse.scroll(6)
                await asyncio.sleep(0.3)
                return await _try_vision_find("scroll_up")
            result = await asyncio.wait_for(_scroll_up_and_find(), timeout=STEP_TIMEOUT)
        except asyncio.TimeoutError:
            result = None

        if result:
            x, y = result.get("x", 0), result.get("y", 0)
            confidence = result.get("confidence", "low")
            offset_x, offset_y = cu.screen.get_monitor_offset()
            cu.mouse.click(x + offset_x, y + offset_y)
            return HandlerResult.ok(
                f"✅ Clic sur '{element}' à ({x + offset_x}, {y + offset_y}) - confiance: {confidence} (après scroll ↑)"
            )

        # ── Étape 5 : OCR local ──────────────────────────────────────
        logger.info(f"🔄 Self-healing [{element}] step 5/6: OCR local")
        try:
            async def _ocr_find():
                ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                spath = os.path.join(screenshot_dir, f"lumena_click_ocr_{ts}.png")
                await cu.take_screenshot(spath)
                return await vision._find_element_with_ocr(spath, element)
            ocr_result = await asyncio.wait_for(_ocr_find(), timeout=STEP_TIMEOUT)
        except asyncio.TimeoutError:
            ocr_result = None

        if ocr_result and ocr_result.get("success") and ocr_result.get("found"):
            x, y = ocr_result.get("x", 0), ocr_result.get("y", 0)
            offset_x, offset_y = cu.screen.get_monitor_offset()
            cu.mouse.click(x + offset_x, y + offset_y)
            return HandlerResult.ok(
                f"✅ Clic sur '{element}' à ({x + offset_x}, {y + offset_y}) - via OCR (self-healing)"
            )

        # ── Étape 6 : UI Automation fallback ────────────────────────
        logger.info(f"🔄 Self-healing [{element}] step 6/6: UI Automation fallback")
        try:
            ui_success = cu.ui.click_element_by_name(element)
            if ui_success:
                return HandlerResult.ok(
                    f"✅ Clic sur '{element}' via UI Automation (self-healing, sans coordonnées)"
                )
        except Exception as exc:
            logger.debug(f"🔍 [ui_automation] erreur: {exc}")

        return HandlerResult.fail(
            f"❌ Élément '{element}' introuvable après 6 tentatives "
            f"(DOM/UIA state-first, vision×3, OCR, UI Automation). "
            f"L'élément n'est probablement pas visible à l'écran."
        )
    except Exception as e:
        return HandlerResult.fail(f"Erreur click_element: {e}")


async def find_element(ctx: HandlerContext, *, element: str) -> HandlerResult:
    """Trouve les coordonnées d'un élément décrit."""
    try:
        from ...computer_use import get_computer_use
        from ...computer_use.vision import get_vision

        cu = get_computer_use()

        import tempfile
        from datetime import datetime

        screenshot_dir = tempfile.gettempdir()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        screenshot_path = os.path.join(screenshot_dir, f"lumena_find_{timestamp}.png")

        await cu.take_screenshot(screenshot_path)

        vision = get_vision()
        result = await vision.find_element_coordinates(screenshot_path, element)

        if not result.get("success"):
            return HandlerResult.fail(f"Erreur: {result.get('error', 'Inconnue')}")

        if not result.get("found"):
            return HandlerResult.fail(f"Élément '{element}' non trouvé")

        x, y = result.get("x", 0), result.get("y", 0)
        confidence = result.get("confidence", "low")
        description = result.get("description", "")

        return HandlerResult.ok(f"📍 '{element}' trouvé à ({x}, {y}) - confiance: {confidence}\n   {description}")
    except Exception as e:
        return HandlerResult.fail(f"Erreur find_element: {e}")


async def zoom(ctx: HandlerContext, *, x1: int, y1: int, x2: int, y2: int, question: str = None) -> HandlerResult:
    """Capture une sous-région de l'écran à pleine résolution et l'analyse optionnellement."""
    try:
        from ...computer_use import get_computer_use
        from ...computer_use.vision import get_vision

        cu = get_computer_use()

        import tempfile
        from datetime import datetime

        screenshot_dir = tempfile.gettempdir()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        zoom_path = os.path.join(screenshot_dir, f"lumena_zoom_{timestamp}.png")

        img = cu.screen.zoom_region(int(x1), int(y1), int(x2), int(y2), save_path=zoom_path)

        if img is None:
            return HandlerResult.fail("Impossible de capturer la zone demandée")

        width, height = img.size
        result_text = f"🔍 Zone capturée: {width}x{height}px @ ({x1},{y1})→({x2},{y2}) — pleine résolution"

        # Si une question est posée, analyser avec vision router unifié (P3.6)
        if question:
            vision = get_vision()
            from ...computer_use.cu_router import route_cu_vision
            vision_result = await route_cu_vision(vision, zoom_path, question, capability="vision_describe")
            if vision_result.get("success"):
                answer = vision_result.get("text") or vision_result.get("answer", "")
                result_text += f"\n📸 Analyse: {answer}"
            else:
                result_text += f"\n⚠️ Analyse échouée: {vision_result.get('error', 'inconnue')}"

        return HandlerResult.ok(result_text)
    except Exception as e:
        return HandlerResult.fail(f"Erreur zoom: {e}")


async def computer_task(ctx: HandlerContext, *, goal: str, max_steps: int = 30) -> HandlerResult:
    """Lance une boucle Agent CU autonome pour accomplir une tâche multi-étapes.
    
    Cascade CU natif : Anthropic → OpenAI → Google → fallback système maison.
    Peu importe le modèle actif (DeepSeek, etc.), le CU natif des providers est
    tenté en priorité. Si aucun n'est disponible, le système maison prend le relais.
    
    Exemples de tâches :
    - "Ouvre Chrome et va sur google.com"
    - "Remplis le formulaire avec nom=Lumena"
    - "Prends une capture d'écran de la page et sauvegarde-la"
    """
    try:
        max_steps = min(int(max_steps), 30)  # Plafonner à 30

        # ── Cascade CU natif : Anthropic → OpenAI → Google ──
        from ...computer_use.native_cu import try_native_cu_cascade
        result = await try_native_cu_cascade(goal, max_steps=max_steps)

        # ── Fallback : système CU maison ──
        if result is None:
            from ...computer_use.cu_agent_loop import CUAgentLoop
            loop = CUAgentLoop(max_iterations=max_steps)
            result = await loop.run(goal)

        # Formater le résumé
        steps_summary = []
        for s in result.steps[-10:]:  # 10 dernières étapes max
            status = "✅" if s.success else "❌"
            steps_summary.append(f"  {status} [{s.iteration}] {s.action} → {s.output[:80]}")
        steps_text = "\n".join(steps_summary) if steps_summary else "(aucune étape)"

        header = "✅" if result.success else "❌"
        output = (
            f"{header} Computer Task: {result.summary}\n"
            f"📊 {result.total_iterations} itérations en {result.total_duration_ms / 1000:.1f}s "
            f"(sortie: {result.exit_reason})\n"
            f"Étapes:\n{steps_text}"
        )

        if result.success:
            return HandlerResult.ok(output)
        else:
            return HandlerResult.fail(output)
    except Exception as e:
        return HandlerResult.fail(f"Erreur computer_task: {e}")


async def list_screens(ctx: HandlerContext) -> HandlerResult:
    """Liste tous les écrans disponibles et montre lequel est actif."""
    try:
        from ...computer_use import get_computer_use
        cu = get_computer_use()

        info = cu.screen._monitor_info
        current = cu.screen._primary_monitor_index

        result_text = "📺 **Écrans disponibles:**\n"
        for i, m in info.items():
            if m['is_combined']:
                result_text += f"  #{i}: {m['width']}x{m['height']} (tous combinés)\n"
            else:
                active = " ← ACTIF" if i == current else ""
                result_text += f"  #{i}: {m['width']}x{m['height']} à ({m['left']}, {m['top']}){active}\n"

        result_text += f"\n💡 Utilise `set_screen(index)` pour changer d'écran cible."
        return HandlerResult.ok(result_text)
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def set_screen(ctx: HandlerContext, *, index: int = None) -> HandlerResult:
    """Change l'écran cible pour les captures et les clics."""
    try:
        from ...computer_use import get_computer_use
        cu = get_computer_use()

        if index is None:
            return HandlerResult.fail("Paramètre 'index' requis. Utilise list_screens pour voir les écrans disponibles.")

        index = int(index)
        old_index = cu.screen._primary_monitor_index
        cu.screen.set_target_monitor(index)
        new_index = cu.screen._primary_monitor_index

        if new_index == index:
            offset = cu.screen.get_monitor_offset()
            info = cu.screen._monitor_info.get(index, {})
            return HandlerResult.ok(
                f"✅ Écran cible changé: #{old_index} → #{index}\n"
                f"   Résolution: {info.get('width', 0)}x{info.get('height', 0)}\n"
                f"   Offset: ({offset[0]}, {offset[1]})"
            )
        return HandlerResult.fail(f"Écran #{index} non trouvé. Utilise list_screens pour voir les écrans disponibles.")
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}")


async def ui_click(ctx: HandlerContext, *, element: str, window: str = None) -> HandlerResult:
    """Clique sur un élément par son nom d'accessibilité Windows (pywinauto)."""
    try:
        from ...computer_use import get_computer_use
        cu = get_computer_use()
        if not cu.ui.is_available():
            return HandlerResult.fail("pywinauto non installé. Faites : pip install pywinauto")
        success = cu.ui.click_element_by_name(element, window_title=window or None)
        if success:
            msg = f"✅ Clic UI Automation sur '{element}'"
            if window:
                msg += f" dans '{window}'"
            return HandlerResult.ok(msg)
        return HandlerResult.fail(
            f"Élément '{element}' non trouvé via UI Automation — utilisez click_element (vision IA) en fallback"
        )
    except Exception as e:
        return HandlerResult.fail(f"Erreur ui_click: {e}")


async def ui_type(ctx: HandlerContext, *, field: str, text: str, window: str = None) -> HandlerResult:
    """Tape du texte dans un champ identifié par son nom d'accessibilité (pywinauto)."""
    try:
        from ...computer_use import get_computer_use
        cu = get_computer_use()
        if not cu.ui.is_available():
            return HandlerResult.fail("pywinauto non installé. Faites : pip install pywinauto")
        success = cu.ui.type_in_field(field, text, window_title=window or None)
        if success:
            msg = f"✅ Texte tapé dans '{field}'"
            if window:
                msg += f" dans '{window}'"
            return HandlerResult.ok(msg)
        return HandlerResult.fail(f"Champ '{field}' non trouvé via UI Automation")
    except Exception as e:
        return HandlerResult.fail(f"Erreur ui_type: {e}")


async def ui_list_controls(ctx: HandlerContext, *, window: str) -> HandlerResult:
    """Liste tous les contrôles cliquables d'une fenêtre via l'arbre d'accessibilité."""
    try:
        from ...computer_use import get_computer_use
        cu = get_computer_use()
        if not cu.ui.is_available():
            return HandlerResult.fail("pywinauto non installé. Faites : pip install pywinauto")
        controls = cu.ui.list_controls(window)
        if not controls:
            return HandlerResult.fail(f"Fenêtre '{window}' non trouvée ou aucun contrôle détecté")
        lines = [f"🪟 Contrôles dans '{window}' ({len(controls)} trouvés):"]
        for ctrl in controls[:30]:
            lines.append(f"  [{ctrl['type']}] {ctrl['name'] or '(sans nom)'}")
        return HandlerResult.ok("\n".join(lines))
    except Exception as e:
        return HandlerResult.fail(f"Erreur ui_list_controls: {e}")


async def mouse_pattern(
    ctx: HandlerContext,
    *,
    shape: str = "circle",
    repetitions: int = 1,
    radius: int = 200,
    speed: str = "normal",
    center_x: int = None,
    center_y: int = None,
) -> HandlerResult:
    """
    Effectue un pattern de déplacement souris répété nativement.

    Permet de tracer des cercles, carrés ou figure en 8 sans passer par run_command.
    """
    import math as _math

    try:
        from ...computer_use import get_computer_use
        cu = get_computer_use()

        try:
            import pyautogui as _pag
        except ImportError:
            return HandlerResult.fail("pyautogui non disponible")

        sw, sh = _pag.size()
        cx = int(center_x) if center_x is not None else sw // 2
        cy = int(center_y) if center_y is not None else sh // 2
        r = max(10, int(radius))
        reps = max(1, min(int(repetitions), 500))

        speed_map = {"slow": 0.06, "normal": 0.03, "fast": 0.01}
        step_dur = speed_map.get(speed.lower(), 0.03)

        shape = shape.lower()
        points: list = []

        if shape == "circle":
            steps = max(36, r // 3)
            for _ in range(reps):
                for i in range(steps + 1):
                    angle = 2 * _math.pi * i / steps
                    points.append((
                        int(cx + r * _math.cos(angle)),
                        int(cy + r * _math.sin(angle)),
                    ))
        elif shape == "square":
            sides = [
                [(cx - r + int(2 * r * t / 20), cy - r) for t in range(21)],
                [(cx + r, cy - r + int(2 * r * t / 20)) for t in range(21)],
                [(cx + r - int(2 * r * t / 20), cy + r) for t in range(21)],
                [(cx - r, cy + r - int(2 * r * t / 20)) for t in range(21)],
            ]
            for _ in range(reps):
                for side in sides:
                    points.extend(side)
        elif shape == "figure8":
            steps = max(72, r // 2)
            for _ in range(reps):
                for i in range(steps + 1):
                    t = 2 * _math.pi * i / steps
                    points.append((
                        int(cx + r * _math.sin(t)),
                        int(cy + r * _math.sin(t) * _math.cos(t)),
                    ))
        else:
            return HandlerResult.fail(f"Forme '{shape}' inconnue. Utilisez : circle, square, figure8")

        _pag.moveTo(points[0][0], points[0][1], duration=0.3, tween=_pag.easeInOutQuad)

        old_pause = _pag.PAUSE
        _pag.PAUSE = 0.0
        try:
            for px, py in points[1:]:
                _pag.moveTo(px, py, duration=step_dur)
        finally:
            _pag.PAUSE = old_pause

        total_pts = len(points)
        return HandlerResult.ok(
            f"✅ Pattern '{shape}' exécuté : {reps} répétition(s), "
            f"rayon={r}px, centre=({cx},{cy}), {total_pts} points parcourus."
        )
    except Exception as e:
        return HandlerResult.fail(f"Erreur mouse_pattern: {e}")


# ─── Handler Definitions ──────────────────────────────────────────────────

def get_computer_use_handler_defs() -> List[HandlerDef]:
    """Retourne les définitions des 27 handlers computer_use.
    
    Note: screenshot est défini dans system.py (pas ici) pour éviter le doublon.
    """
    return [
        HandlerDef(
            name="click",
            description="Clique à une position x,y sur l'écran",
            parameters={
                "properties": {
                    "x": {"type": "integer", "description": "Position X"},
                    "y": {"type": "integer", "description": "Position Y"},
                    "button": {"type": "string", "description": "left, right ou middle", "default": "left"},
                },
                "required": ["x", "y"],
            },
            handler=click,
            category="computer_use",
            source_module="handlers.computer_use",
        ),
        HandlerDef(
            name="type_text",
            description="Tape du texte au clavier",
            parameters={
                "properties": {
                    "text": {"type": "string", "description": "Texte à taper"},
                },
                "required": ["text"],
            },
            handler=type_text,
            category="computer_use",
            source_module="handlers.computer_use",
        ),
        HandlerDef(
            name="open_app",
            description="Ouvre une application par son nom",
            parameters={
                "properties": {
                    "name": {"type": "string", "description": "Nom de l'application"},
                },
                "required": ["name"],
            },
            handler=open_app,
            category="computer_use",
            source_module="handlers.computer_use",
        ),
        HandlerDef(
            name="close_app",
            description="Ferme une application (ou les terminaux CMD/PowerShell) pour éviter la saturation machine.",
            parameters={
                "properties": {
                    "name": {"type": "string", "description": "Nom app/process (ex: cmd, powershell, notepad, chrome)", "default": ""},
                    "close_terminals": {"type": "boolean", "description": "Fermer les terminaux CMD/PowerShell/Windows Terminal", "default": False},
                    "force": {"type": "boolean", "description": "Forcer la fermeture", "default": True},
                },
                "required": [],
            },
            handler=close_app,
            category="computer_use",
            source_module="handlers.computer_use",
        ),
        HandlerDef(
            name="cursor_ide_local",
            description=(
                "Controle l'IDE local cursor-ide-local de Lumena: statut, ouverture, "
                "focus et workspace projet."
            ),
            parameters={
                "properties": {
                    "action": {"type": "string", "description": "status | ensure_open | focus | ensure_workspace"},
                    "workspace_path": {"type": "string", "description": "Chemin workspace cible"},
                    "create_if_missing": {"type": "boolean", "description": "Crée le workspace cible s'il n'existe pas", "default": False},
                },
                "required": ["action"],
            },
            handler=cursor_ide_local,
            category="computer_use",
            source_module="handlers.computer_use",
        ),
        HandlerDef(
            name="hotkey",
            description="Exécute un raccourci clavier (ex: ctrl+c, alt+tab)",
            parameters={
                "properties": {
                    "keys": {"type": "string", "description": "Combinaison de touches (ex: ctrl+c)"},
                },
                "required": ["keys"],
            },
            handler=hotkey,
            category="computer_use",
            source_module="handlers.computer_use",
        ),
        HandlerDef(
            name="get_active_window",
            description="Retourne le titre de la fenêtre active",
            parameters={"properties": {}, "required": []},
            handler=get_active_window,
            category="computer_use",
            source_module="handlers.computer_use",
        ),
        HandlerDef(
            name="double_click",
            description="Double-clic à une position x,y",
            parameters={
                "properties": {
                    "x": {"type": "integer", "description": "Position X"},
                    "y": {"type": "integer", "description": "Position Y"},
                },
                "required": ["x", "y"],
            },
            handler=double_click,
            category="computer_use",
            source_module="handlers.computer_use",
        ),
        HandlerDef(
            name="scroll",
            description="Scroll vers le haut ou le bas",
            parameters={
                "properties": {
                    "direction": {"type": "string", "description": "up ou down"},
                    "amount": {"type": "integer", "description": "Quantité de scroll", "default": 3},
                },
                "required": ["direction"],
            },
            handler=scroll,
            category="computer_use",
            source_module="handlers.computer_use",
        ),
        HandlerDef(
            name="move_mouse",
            description="Déplace la souris vers une position",
            parameters={
                "properties": {
                    "x": {"type": "integer", "description": "Position X"},
                    "y": {"type": "integer", "description": "Position Y"},
                },
                "required": ["x", "y"],
            },
            handler=move_mouse,
            category="computer_use",
            source_module="handlers.computer_use",
        ),
        HandlerDef(
            name="press_key",
            description="Appuie sur une touche (enter, tab, escape, f1, etc.)",
            parameters={
                "properties": {
                    "key": {"type": "string", "description": "Nom de la touche"},
                },
                "required": ["key"],
            },
            handler=press_key,
            category="computer_use",
            source_module="handlers.computer_use",
        ),
        HandlerDef(
            name="close_window",
            description="Ferme la fenêtre active (Alt+F4)",
            parameters={"properties": {}, "required": []},
            handler=close_window,
            category="computer_use",
            source_module="handlers.computer_use",
        ),
        HandlerDef(
            name="wait",
            description="Attend un nombre de secondes (utile entre les actions UI)",
            parameters={
                "properties": {
                    "seconds": {"type": "integer", "description": "Nombre de secondes (max 10)"},
                },
                "required": ["seconds"],
            },
            handler=wait,
            category="computer_use",
            source_module="handlers.computer_use",
        ),
        HandlerDef(
            name="spotify_play",
            description="Ouvre Spotify et joue une recherche via navigation clavier (plus fiable que vision)",
            parameters={
                "properties": {
                    "query": {"type": "string", "description": "Artiste, titre ou album à jouer"},
                },
                "required": ["query"],
            },
            handler=spotify_play,
            category="computer_use",
            source_module="handlers.computer_use",
        ),
        HandlerDef(
            name="open_url",
            description="Ouvre une URL dans le navigateur par défaut",
            parameters={
                "properties": {
                    "url": {"type": "string", "description": "URL à ouvrir"},
                },
                "required": ["url"],
            },
            handler=open_url,
            category="computer_use",
            source_module="handlers.computer_use",
        ),
        HandlerDef(
            name="list_windows",
            description="Liste les fenêtres ouvertes",
            parameters={"properties": {}, "required": []},
            handler=list_windows,
            category="computer_use",
            source_module="handlers.computer_use",
        ),
        HandlerDef(
            name="drag",
            description="Glisser-déposer d'une position à une autre",
            parameters={
                "properties": {
                    "start_x": {"type": "integer", "description": "Position X de départ"},
                    "start_y": {"type": "integer", "description": "Position Y de départ"},
                    "end_x": {"type": "integer", "description": "Position X d'arrivée"},
                    "end_y": {"type": "integer", "description": "Position Y d'arrivée"},
                },
                "required": ["start_x", "start_y", "end_x", "end_y"],
            },
            handler=drag,
            category="computer_use",
            source_module="handlers.computer_use",
        ),
        HandlerDef(
            name="screenshot_analyze",
            description="Prend un screenshot et l'analyse avec l'IA pour décrire ce qui est visible. Si file_path est fourni, analyse ce fichier PNG existant au lieu de capturer l'écran live.",
            parameters={
                "properties": {
                    "question": {"type": "string", "description": "Question sur le screenshot"},
                    "file_path": {"type": "string", "description": "Chemin absolu vers un fichier PNG existant à analyser (optionnel). Si absent, prend un screenshot de l'écran live."},
                },
                "required": [],
            },
            handler=screenshot_analyze,
            category="computer_use",
            source_module="handlers.computer_use",
        ),
        HandlerDef(
            name="click_element",
            description="Trouve un élément par sa description et clique dessus (utilise la vision IA)",
            parameters={
                "properties": {
                    "element": {"type": "string", "description": "Description de l'élément à cliquer (ex: 'bouton lecture', 'barre de recherche')"},
                },
                "required": ["element"],
            },
            handler=click_element,
            category="computer_use",
            source_module="handlers.computer_use",
        ),
        HandlerDef(
            name="find_element",
            description="Trouve les coordonnées d'un élément décrit (utilise la vision IA)",
            parameters={
                "properties": {
                    "element": {"type": "string", "description": "Description de l'élément à trouver"},
                },
                "required": ["element"],
            },
            handler=find_element,
            category="computer_use",
            source_module="handlers.computer_use",
        ),
        HandlerDef(
            name="zoom",
            description=(
                "Capture une sous-région de l'écran à PLEINE RÉSOLUTION (pas de downscale). "
                "Utile pour inspecter une zone précise, lire du petit texte, ou vérifier un détail. "
                "Peut optionnellement analyser la zone avec l'IA vision."
            ),
            parameters={
                "properties": {
                    "x1": {"type": "integer", "description": "X coin supérieur gauche"},
                    "y1": {"type": "integer", "description": "Y coin supérieur gauche"},
                    "x2": {"type": "integer", "description": "X coin inférieur droit"},
                    "y2": {"type": "integer", "description": "Y coin inférieur droit"},
                    "question": {"type": "string", "description": "Question optionnelle pour analyser la zone capturée"},
                },
                "required": ["x1", "y1", "x2", "y2"],
            },
            handler=zoom,
            category="computer_use",
            source_module="handlers.computer_use",
        ),
        HandlerDef(
            name="computer_task",
            description=(
                "Lance une tâche Computer Use AUTONOME multi-étapes. "
                "LUMENA prend le contrôle de l'écran : elle voit, raisonne, agit, et vérifie. "
                "Utiliser pour les tâches complexes qui nécessitent plusieurs clics/saisies. "
                "Exemples: 'Ouvre Chrome et va sur google.com', 'Remplis le formulaire de contact'."
            ),
            parameters={
                "properties": {
                    "goal": {"type": "string", "description": "Description en langage naturel de la tâche à accomplir"},
                    "max_steps": {"type": "integer", "description": "Nombre max d'étapes (défaut: 30, max: 30)", "default": 30},
                },
                "required": ["goal"],
            },
            handler=computer_task,
            category="computer_use",
            source_module="handlers.computer_use",
        ),
        HandlerDef(
            name="list_screens",
            description="Liste tous les écrans disponibles et montre lequel est actif pour les captures",
            parameters={"properties": {}, "required": []},
            handler=list_screens,
            category="computer_use",
            source_module="handlers.computer_use",
        ),
        HandlerDef(
            name="set_screen",
            description="Change l'écran cible pour les captures et les clics (utile en multi-écran)",
            parameters={
                "properties": {
                    "index": {"type": "integer", "description": "Index de l'écran (1-N)"},
                },
                "required": ["index"],
            },
            handler=set_screen,
            category="computer_use",
            source_module="handlers.computer_use",
        ),
        HandlerDef(
            name="ui_click",
            description=(
                "Clique sur un bouton/élément par son NOM (pas par coordonnées). "
                "Utilise Windows UI Automation via pywinauto."
            ),
            parameters={
                "properties": {
                    "element": {"type": "string", "description": "Nom/texte exact ou partiel du contrôle"},
                    "window": {"type": "string", "description": "Titre partiel de la fenêtre"},
                },
                "required": ["element"],
            },
            handler=ui_click,
            category="computer_use",
            source_module="handlers.computer_use",
        ),
        HandlerDef(
            name="ui_type",
            description=(
                "Tape du texte dans un champ identifié par son nom d'accessibilité. "
                "Plus fiable que type_text car cible le bon champ sans dépendre du focus."
            ),
            parameters={
                "properties": {
                    "field": {"type": "string", "description": "Nom/label du champ de saisie"},
                    "text": {"type": "string", "description": "Texte à taper"},
                    "window": {"type": "string", "description": "Titre partiel de la fenêtre"},
                },
                "required": ["field", "text"],
            },
            handler=ui_type,
            category="computer_use",
            source_module="handlers.computer_use",
        ),
        HandlerDef(
            name="ui_list_controls",
            description=(
                "Liste tous les contrôles cliquables d'une fenêtre (boutons, champs, menus). "
                "Utile pour découvrir ce qui est disponible avant d'utiliser ui_click ou ui_type."
            ),
            parameters={
                "properties": {
                    "window": {"type": "string", "description": "Titre partiel de la fenêtre"},
                },
                "required": ["window"],
            },
            handler=ui_list_controls,
            category="computer_use",
            source_module="handlers.computer_use",
        ),
        HandlerDef(
            name="mouse_pattern",
            description=(
                "Trace un pattern de déplacement souris répété nativement (sans script externe). "
                "Utiliser pour des cercles, carrés, ou figure en 8 avec la souris."
            ),
            parameters={
                "properties": {
                    "shape": {"type": "string", "description": "Forme : 'circle', 'square', 'figure8'", "default": "circle"},
                    "repetitions": {"type": "integer", "description": "Nombre de répétitions", "default": 1},
                    "radius": {"type": "integer", "description": "Rayon ou demi-côté en pixels", "default": 200},
                    "speed": {"type": "string", "description": "Vitesse : 'slow', 'normal', 'fast'", "default": "normal"},
                    "center_x": {"type": "integer", "description": "Centre X (défaut = centre écran)"},
                    "center_y": {"type": "integer", "description": "Centre Y (défaut = centre écran)"},
                },
                "required": [],
            },
            handler=mouse_pattern,
            category="computer_use",
            source_module="handlers.computer_use",
        ),
    ]
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the Apache License, Version 2.0
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
