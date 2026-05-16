"""
VisualVerifier — Vérification visuelle post-création/modification de projets web.

Lance un serveur HTTP temporaire (port 8000, whitelisted SSRF),
screenshot via PlaywrightBrowser, analyse via llm.describe_image(path, prompt).

APIs réelles vérifiées :
- PlaywrightBrowser(headless=True) → .start() → .navigate(url) → .screenshot(full_page=True)
- llm.describe_image(image_path: str, prompt: str) → str
- Port 8000 : whitelisted dans _SSRF_ALLOWED_LOCAL_PORTS
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from loguru import logger


class VisualVerifier:
    """
    Vérification visuelle après création/modification d'un projet web.
    Ne bloque JAMAIS — toutes erreurs ignorées silencieusement.
    """

    _HTTP_PORT = 8000
    _VISION_PROMPT = (
        "Analyse ce screenshot d'une page web. "
        "Liste UNIQUEMENT les vrais problèmes visuels : "
        "page blanche, layout cassé, texte illisible, boutons hors écran, CSS non chargé. "
        "Si tout semble correct, réponds exactement : OK"
    )

    async def verify(self, workspace_path: Path, llm) -> str | None:
        """
        Retourne None si visuellement correct, sinon description des problèmes.
        Ne bloque JAMAIS — timeout 15s, toutes erreurs ignorées.

        Phase 0.2 — Chaque path de sortie loggue explicitement sa raison
        (auparavant beaucoup de `return None` silencieux rendaient le skip
        impossible à diagnostiquer en prod).
        """
        index = workspace_path / "index.html"
        if not index.exists():
            logger.debug(
                "[VisualVerifier] Skip: no_index_html (workspace={})",
                workspace_path,
            )
            return None
        if llm is None:
            logger.debug("[VisualVerifier] Skip: llm_is_none")
            return None
        if not hasattr(llm, "describe_image"):
            logger.debug(
                "[VisualVerifier] Skip: llm_no_describe_image (type={})",
                type(llm).__name__,
            )
            return None

        proc = None
        browser = None
        try:
            proc = subprocess.Popen(
                ["python", "-m", "http.server", str(self._HTTP_PORT)],
                cwd=str(workspace_path),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            await asyncio.sleep(1.5)

            from src.tools.playwright_browser import PlaywrightBrowser
            browser = PlaywrightBrowser(headless=True, profile_name=None)
            started = await browser.start()
            if not started:
                logger.debug("[VisualVerifier] Skip: playwright_browser_start_failed")
                return None

            nav = await browser.navigate(
                f"http://localhost:{self._HTTP_PORT}",
                wait_until="domcontentloaded",
            )
            if not nav.get("success"):
                logger.debug(
                    "[VisualVerifier] Skip: navigate_failed (error={!r})",
                    str(nav.get("error", "unknown"))[:200],
                )
                return None

            await asyncio.sleep(1.0)

            shot = await browser.screenshot(full_page=True)
            if not shot.get("success"):
                logger.debug(
                    "[VisualVerifier] Skip: screenshot_failed (error={!r})",
                    str(shot.get("error", "unknown"))[:200],
                )
                return None
            screenshot_path = shot["path"]

            try:
                result = await asyncio.wait_for(
                    llm.describe_image(screenshot_path, self._VISION_PROMPT),
                    timeout=12.0,
                )
            except asyncio.TimeoutError:
                logger.debug(
                    "[VisualVerifier] Skip: vision_timeout_12s (screenshot={})",
                    screenshot_path,
                )
                return None
            response = str(result).strip()
            if response.upper().startswith("OK"):
                logger.debug("[VisualVerifier] OK: pas de problème visuel détecté")
                return None
            logger.info(
                "[VisualVerifier] Problème détecté : {}",
                response[:160],
            )
            return response

        except Exception as e:
            logger.debug(
                "[VisualVerifier] Skip: exception ({}): {}",
                type(e).__name__, str(e)[:200] or "<empty message>",
            )
            return None
        finally:
            if proc:
                proc.terminate()
            if browser:
                try:
                    await browser.stop()
                except Exception:
                    pass
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
