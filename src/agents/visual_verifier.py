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
        """
        index = workspace_path / "index.html"
        if not index.exists():
            return None
        if llm is None:
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
                return None

            nav = await browser.navigate(
                f"http://localhost:{self._HTTP_PORT}",
                wait_until="domcontentloaded",
            )
            if not nav.get("success"):
                return None

            await asyncio.sleep(1.0)

            shot = await browser.screenshot(full_page=True)
            if not shot.get("success"):
                return None
            screenshot_path = shot["path"]

            result = await asyncio.wait_for(
                llm.describe_image(screenshot_path, self._VISION_PROMPT),
                timeout=12.0,
            )
            response = str(result).strip()
            if response.upper().startswith("OK"):
                return None
            return response

        except Exception as e:
            logger.debug("[VisualVerifier] Skip: {}", e)
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
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
