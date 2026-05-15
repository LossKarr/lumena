"""
stripe_cli.py — Service intégré Stripe CLI pour Lumena.

Gère automatiquement le forwarding des webhooks Stripe en local via `stripe listen`.
Démarre/arrête le processus CLI, récupère le webhook secret, et monitore la santé.

Usage:
    from src.services.stripe_cli import StripeCLIService
    svc = StripeCLIService(forward_url="http://localhost:8080/api/stripe/webhook")
    await svc.start()   # Lance stripe listen en background
    await svc.stop()    # Arrête proprement
    svc.webhook_secret  # Le whsec_... généré par la CLI
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
import threading
import time
import webbrowser
from pathlib import Path
from typing import Optional

from loguru import logger

_WHSEC_RE = re.compile(r"(whsec_[A-Za-z0-9]+)")


class StripeCLIService:
    """Gère le processus `stripe listen` en arrière-plan.

    Utilise subprocess.Popen + threading (pas asyncio.subprocess) pour
    éviter de bloquer l'event loop uvicorn sur Windows ProactorEventLoop.
    """

    def __init__(
        self,
        forward_url: str = None,
        api_key: Optional[str] = None,
    ):
        _port = os.getenv("LUMENA_PORT", "8080")
        self.forward_url = forward_url or os.getenv(
            "LUMENA_STRIPE_FORWARD_URL",
            f"http://localhost:{_port}/api/stripe/webhook"
        )
        self.api_key = api_key or os.getenv("STRIPE_API_KEY", "").strip()
        self._process: Optional[subprocess.Popen] = None
        self._webhook_secret: Optional[str] = None
        self._running = False
        self._monitor_thread: Optional[threading.Thread] = None
        self._secret_event = threading.Event()
        self._login_needed = False   # True si 403 détecté → auto-login requis
        self._cli_path: Optional[str] = None

    @property
    def webhook_secret(self) -> Optional[str]:
        """Le signing secret webhook généré par la CLI."""
        return self._webhook_secret

    @property
    def is_running(self) -> bool:
        return self._running and self._process is not None and self._process.poll() is None

    @staticmethod
    def find_cli() -> Optional[str]:
        """Cherche l'exécutable stripe dans le PATH."""
        path = shutil.which("stripe")
        if path:
            return path
        # Winget installe souvent ici
        local_app = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links" / "stripe.exe"
        if local_app.exists():
            return str(local_app)
        # Auto-install via zip (fallback Windows Server)
        local_stripe = Path(os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))) / "Stripe" / "stripe.exe"
        if local_stripe.exists():
            return str(local_stripe)
        return None

    @staticmethod
    def is_installed() -> bool:
        """Vérifie si Stripe CLI est disponible."""
        return StripeCLIService.find_cli() is not None

    @staticmethod
    def check_version() -> Optional[str]:
        """Retourne la version de la CLI, ou None si non installée."""
        cli = StripeCLIService.find_cli()
        if not cli:
            return None
        try:
            result = subprocess.run(
                [cli, "version"],
                capture_output=True, text=True, timeout=10,
            )
            return result.stdout.strip() if result.returncode == 0 else None
        except Exception:
            return None

    async def start(self) -> bool:
        """Démarre `stripe listen --forward-to <url>` en arrière-plan.

        Le subprocess tourne dans un thread séparé pour ne pas bloquer
        l'event loop asyncio (Windows ProactorEventLoop bug).

        Returns:
            True si démarré avec succès, False sinon.
        """
        if self.is_running:
            logger.info("[StripeCLI] Déjà en cours d'exécution")
            return True

        cli = self.find_cli()
        if not cli:
            logger.error("[StripeCLI] CLI non trouvée. Installe avec: winget install Stripe.StripeCLI")
            return False

        self._cli_path = cli
        cmd = [cli, "listen", "--forward-to", self.forward_url]
        # Ne pas passer --api-key : en mode live, Stripe CLI exige les credentials
        # de `stripe login` (restricted keys). Les credentials sont stockés localement.

        logger.info(f"[StripeCLI] Démarrage: {' '.join(cmd)}")

        try:
            # Environnement propre : retirer STRIPE_API_KEY pour que la CLI utilise
            # les credentials de `stripe login` (rk_live_...) au lieu de la clé secrète
            # brute (sk_live_...) qui est refusée en live mode.
            clean_env = {k: v for k, v in os.environ.items()
                         if k not in ("STRIPE_API_KEY", "STRIPE_SECRET_KEY")}

            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,  # Merge stderr→stdout
                env=clean_env,
            )
            self._running = True
            self._secret_event.clear()

            # Thread de monitoring qui lit stdout ligne par ligne
            self._monitor_thread = threading.Thread(
                target=self._reader_thread, daemon=True, name="stripe-cli-reader"
            )
            self._monitor_thread.start()

            # Attendre le webhook secret (max 30s) sans bloquer l'event loop
            got_secret = await _to_thread_event_wait(self._secret_event, timeout=30)
            if got_secret and self._webhook_secret:
                logger.info("[StripeCLI] Webhook secret capturé: whsec_***")
                os.environ["STRIPE_WEBHOOK_SECRET"] = self._webhook_secret
                logger.info("[StripeCLI] STRIPE_WEBHOOK_SECRET injecté dans l'environnement")
                return True

            if self._login_needed:
                # 403 détecté → lancer stripe login automatiquement
                logger.info("[StripeCLI] Authentification requise — ouverture du navigateur Stripe...")
                await self.stop()  # Arrêter le process échoué
                self._login_needed = False
                login_ok = await asyncio.to_thread(self._do_sync_login)
                if login_ok:
                    logger.info("[StripeCLI] Authentification réussie — relance de stripe listen")
                    return await self.start()  # Retry avec les nouveaux credentials
                else:
                    logger.error("[StripeCLI] Échec authentification — fermer Lumena et relancer 'stripe login' manuellement")
                    return False

            logger.warning("[StripeCLI] Pas de webhook secret détecté après 30s")
            return True  # La CLI tourne, juste pas de secret encore

        except Exception as exc:
            logger.error(f"[StripeCLI] Échec démarrage: {exc}")
            self._running = False
            return False

    def _reader_thread(self):
        """Thread daemon qui lit la sortie de stripe listen (bloquant, hors event loop)."""
        try:
            assert self._process is not None and self._process.stdout is not None
            for raw_line in self._process.stdout:
                if not self._running:
                    break
                text = raw_line.decode("utf-8", errors="replace").strip()
                if not text:
                    continue

                # Capturer le webhook secret
                match = _WHSEC_RE.search(text)
                if match and not self._webhook_secret:
                    self._webhook_secret = match.group(1)
                    self._secret_event.set()

                # Détecter erreur 403 (login requis)
                if "status=403" in text or "Authorization failed" in text:
                    logger.warning("[StripeCLI] 403 détecté — auto-login nécessaire")
                    self._login_needed = True
                    self._secret_event.set()  # Débloquer start() pour gérer le login

                # Log
                if "-->" in text or "200" in text or "event" in text.lower():
                    logger.info(f"[StripeCLI] {text}")
                elif "status=403" not in text and "Authorization" not in text:
                    logger.debug(f"[StripeCLI] {text}")
        except Exception as exc:
            if self._running:
                logger.error(f"[StripeCLI] Reader error: {exc}")
        finally:
            self._running = False
            logger.info("[StripeCLI] Reader thread arrêté")

    def _do_sync_login(self) -> bool:
        """Lance `stripe login` en ouvrant le navigateur automatiquement.

        Bloquant — à appeler via asyncio.to_thread().
        Timeout : 3 minutes pour que l'utilisateur confirme dans le navigateur.
        """
        cli = self._cli_path or self.find_cli()
        if not cli:
            return False

        clean_env = {k: v for k, v in os.environ.items()
                     if k not in ("STRIPE_API_KEY", "STRIPE_SECRET_KEY")}

        logger.info("[StripeCLI/login] Démarrage stripe login...")
        try:
            proc = subprocess.Popen(
                [cli, "login"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=clean_env,
            )

            # Appuyer sur Entrée automatiquement pour ouvrir le navigateur
            # (stripe login affiche l'URL puis attend Entrée OU visite de l'URL)
            deadline = time.time() + 10
            url_opened = False
            while time.time() < deadline:
                raw = proc.stdout.readline()
                if not raw:
                    break
                text = raw.decode("utf-8", errors="replace").strip()
                logger.info(f"[StripeCLI/login] {text}")
                if "dashboard.stripe.com" in text:
                    # Extraire l'URL et l'ouvrir
                    import re as _re
                    url_match = _re.search(r"https://\S+", text)
                    if url_match:
                        url = url_match.group(0)
                        logger.info(f"[StripeCLI/login] Ouverture navigateur: {url}")
                        webbrowser.open(url)
                        url_opened = True
                if url_opened:
                    break

            # Envoyer Entrée pour que la CLI continue à attendre le callback
            try:
                proc.stdin.write(b"\n")
                proc.stdin.flush()
            except Exception:
                pass

            # Attendre confirmation (max 3 minutes)
            logger.info("[StripeCLI/login] En attente de confirmation dans le navigateur (3 min max)...")
            deadline = time.time() + 180
            while time.time() < deadline:
                raw = proc.stdout.readline()
                if not raw:
                    if proc.poll() is not None:
                        break
                    continue
                text = raw.decode("utf-8", errors="replace").strip()
                if text:
                    logger.info(f"[StripeCLI/login] {text}")
                if "Done!" in text or "configured" in text.lower():
                    proc.wait(timeout=5)
                    logger.info("[StripeCLI/login] Authentification réussie !")
                    return True

            proc.terminate()
            logger.warning("[StripeCLI/login] Timeout — l'utilisateur n'a pas confirmé dans le navigateur")
            return False

        except Exception as exc:
            logger.error(f"[StripeCLI/login] Erreur: {exc}")
            return False

    async def stop(self):
        """Arrête proprement le processus stripe listen."""
        self._running = False

        if self._process and self._process.poll() is None:
            logger.info("[StripeCLI] Arrêt du processus...")
            try:
                self._process.terminate()
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                logger.warning("[StripeCLI] Kill forcé")
                self._process.kill()
            except Exception as exc:
                logger.error(f"[StripeCLI] Erreur arrêt: {exc}")

        if self._monitor_thread and self._monitor_thread.is_alive():
            self._monitor_thread.join(timeout=3)

        self._process = None
        logger.info("[StripeCLI] Arrêté")

    def status(self) -> dict:
        """Retourne l'état du service."""
        return {
            "installed": self.is_installed(),
            "running": self.is_running,
            "forward_url": self.forward_url,
            "webhook_secret_set": self._webhook_secret is not None,
            "cli_path": self._cli_path or self.find_cli(),
            "pid": self._process.pid if self._process and self._process.poll() is None else None,
        }


# ── Helpers ───────────────────────────────────────────────────────────────


async def _to_thread_event_wait(event: threading.Event, timeout: float) -> bool:
    """Attend un threading.Event sans bloquer l'event loop asyncio."""
    return await asyncio.to_thread(event.wait, timeout)


# ── Singleton ─────────────────────────────────────────────────────────────

_instance: Optional[StripeCLIService] = None
_instance_lock = threading.Lock()


def get_stripe_cli_service(
    forward_url: str = "http://localhost:8080/api/stripe/webhook",
) -> StripeCLIService:
    """Retourne le singleton du service Stripe CLI."""
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = StripeCLIService(forward_url=forward_url)
    return _instance
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
