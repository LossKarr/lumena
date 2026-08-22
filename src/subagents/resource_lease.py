"""Lot 0.c — Leases de ressources physiques exclusives (anti-collision).

Deux registres isolés (0.b) règlent l'état INTERNE, mais pas deux **actions
physiques** concurrentes : navigateur, Computer Use (souris/clavier/écran),
fichiers, MCP à état. Un sous-agent = une « Lumena complète » → il doit demander
un **lease** sur ces ressources. Le chat reste dispo ; une action sur la **même**
ressource **attend son tour** (gate le FUTUR, jamais le présent : lease PAR ACTION).

Clés de ressource **réelles** (pas une « classe » grossière) :
- navigateur      → clé fixe globale `browser` (session unique) ;
- Computer Use    → clé fixe globale `computer_use` (desktop unique) ;
- fichiers        → `files:<chemin résolu>` (pas un verrou global sur tous les fichiers) ;
- MCP             → `mcp:<server_id>` (fallback conservateur `mcp:?` si inconnu).

Le hook dans `ToolRegistry.execute` est posé au **Lot 1** (quand une mission réelle
permet de prouver la contention bout-en-bout). Ici : la primitive + le mapping, testés.
"""
from __future__ import annotations

import asyncio
import contextvars
import os
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional

from loguru import logger

# ── Mapping outil → clé de ressource exclusive ─────────────────────────────────

# Outils Computer Use (desktop partagé). Conservateur : on liste les actions physiques.
_COMPUTER_USE_TOOLS = frozenset({
    "computer_use", "computer_action", "click", "double_click", "right_click",
    "move_mouse", "drag", "scroll", "type_text", "key_press", "hotkey",
    "screenshot", "take_screenshot", "focus_window", "open_app",
})

# Outils fichiers à effet d'écriture (lecture seule = pas de lease).
_FILE_WRITE_TOOLS = frozenset({
    "write_file", "edit_file", "create_file", "append_file", "delete_file",
    "move_file", "copy_file", "edit_lines", "apply_patch",
})

# Outils qui pilotent le NAVIGATEUR Playwright PARTAGÉ (instance unique via
# get_playwright_browser()). Sans lease, deux boucles (chat ⇄ mission, mission ⇄
# mission) conduisent le MÊME navigateur en même temps → goto concurrents → hang.
# (web_search_brave = DuckDuckGo HTTP → PAS le navigateur → exclu.)
# Priorité 1 (2026-06-30) : `web_fetch` est URLLIB-FIRST (web.py:132) — il ne touche
# Playwright qu'en FALLBACK anti-bot. Le verrouiller ici sérialisait les workers même
# pour du HTTP pur (lenteur observée, mission fromages). On l'EXCLUT du lease registry ;
# il prend l'exclusivité navigateur LUI-MÊME, uniquement dans son fallback Playwright.
_BROWSER_TOOLS = frozenset({
    "web_search", "deep_research", "web_crawl", "web_crawl_campaign",
})


def lease_wait_timeout() -> float:
    """Attente max d'un lease avant abandon (anti-blocage). `LUMENA_TOOL_LEASE_TIMEOUT`,
    défaut 600s, borné 5..3600. Évite le freeze infini si une ressource reste occupée."""
    try:
        return float(max(5, min(3600, int(os.getenv("LUMENA_TOOL_LEASE_TIMEOUT", "600")))))
    except (ValueError, TypeError):
        return 600.0


def _resolve_path(args: Dict[str, Any]) -> str:
    raw = ""
    if isinstance(args, dict):
        raw = str(args.get("path") or args.get("file_path") or args.get("target") or "").strip()
    if not raw:
        return "?"
    try:
        return os.path.normcase(os.path.abspath(raw))
    except Exception:
        return raw


def resource_key_for(name: str, args: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """Retourne la clé de ressource exclusive d'un outil, ou None (pas de lease)."""
    if not name:
        return None
    args = args or {}
    if name.startswith("browser_") or name == "browser" or name in _BROWSER_TOOLS:
        return "browser"
    if name in _COMPUTER_USE_TOOLS:
        return "computer_use"
    if name in _FILE_WRITE_TOOLS:
        return f"files:{_resolve_path(args)}"
    if name.startswith("mcp__"):
        parts = name.split("__")
        server_id = parts[1] if len(parts) >= 3 and parts[1] else "?"
        return f"mcp:{server_id}"
    return None


# ── Lease ──────────────────────────────────────────────────────────────────────

class ResourceLease:
    """Verrous async par clé de ressource (créés à la demande)."""

    def __init__(self) -> None:
        self._locks: Dict[str, asyncio.Lock] = {}
        self._guard = asyncio.Lock()

    async def _lock_for(self, key: str) -> asyncio.Lock:
        async with self._guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[key] = lock
            return lock

    @asynccontextmanager
    async def hold(self, key: str, *, timeout: Optional[float] = None):
        """Tient le lease de `key` autour d'une action. Libère **toujours** (même sur
        exception). `timeout` (s) borne l'attente : dépassement → `asyncio.TimeoutError`.
        """
        lock = await self._lock_for(key)
        if timeout is not None:
            await asyncio.wait_for(lock.acquire(), timeout=timeout)
        else:
            await lock.acquire()
        try:
            yield
        finally:
            lock.release()

    def is_held(self, key: str) -> bool:
        lock = self._locks.get(key)
        return bool(lock and lock.locked())


# Singleton partagé (chat + missions utiliseront la MÊME instance au Lot 1).
_LEASE = ResourceLease()


def get_resource_lease() -> ResourceLease:
    return _LEASE


# ── #4 Exclusivité navigateur PAR MISSION (Option B, v1) ────────────────────────
# Le navigateur Playwright est un singleton à onglet actif partagé. Si plusieurs
# missions (lead + workers) naviguent, elles se disputent l'onglet → onglets mélangés.
# v1 : une MISSION tient le navigateur sur TOUTE sa session (sticky), les autres
# attendent. Le chat reste par-action (owner par appel, libéré aussitôt).
# Deux niveaux gérés ensemble :
#   - cross-owner  : un seul owner pilote à la fois ; une mission garde l'exclusivité
#                    entre ses actions → aucun autre ne s'intercale (zéro mélange).
#   - same-owner   : les actions d'un même owner s'exécutent une à la fois
#                    (parallel_tools lance plusieurs fetch en // dans une mission).
# `release_owner` est IDEMPOTENT et OWNER-CHECKÉ : ne libère que si CET owner tient,
# ne lève jamais, no-op si la mission n'a jamais touché le navigateur.
_browser_owner_var: "contextvars.ContextVar[Any]" = contextvars.ContextVar(
    "lumena_browser_owner", default=None
)


def set_browser_owner(owner: Any):
    """Marque l'owner navigateur courant (mission). Retourne un token pour reset."""
    return _browser_owner_var.set(owner)


def clear_browser_owner(token) -> None:
    try:
        _browser_owner_var.reset(token)
    except Exception:
        pass


def current_browser_owner() -> Any:
    return _browser_owner_var.get()


class BrowserExclusivity:
    """Exclusivité navigateur par owner (sticky pour une mission) + sérialisation
    des actions d'un même owner. Voir le commentaire ci-dessus."""

    def __init__(self) -> None:
        self._cond = asyncio.Condition()
        self._owner: Any = None   # owner qui tient l'exclusivité (None = libre)
        self._busy: bool = False  # une action est en cours (sérialise le même owner)

    async def _begin_action(self, owner: Any, timeout: Optional[float]) -> None:
        async def _wait() -> None:
            async with self._cond:
                while not ((self._owner is None or self._owner == owner) and not self._busy):
                    await self._cond.wait()
                self._owner = owner   # devient/reste owner (sticky)
                self._busy = True
        if timeout is not None:
            await asyncio.wait_for(_wait(), timeout=timeout)
        else:
            await _wait()

    async def _end_action(self) -> None:
        async with self._cond:
            self._busy = False
            self._cond.notify_all()

    @asynccontextmanager
    async def action(self, owner: Any, *, timeout: Optional[float] = None):
        """Tient le navigateur pour UNE action de `owner` (sérialisée même-owner ;
        l'owner reste « sticky » entre actions tant que `release_owner` n'est pas appelé)."""
        await self._begin_action(owner, timeout)
        try:
            yield
        finally:
            await self._end_action()

    async def release_owner(self, owner: Any) -> None:
        """Libère l'exclusivité — UNIQUEMENT si `owner` la tient. Idempotent, ne lève jamais."""
        try:
            async with self._cond:
                if self._owner == owner:
                    self._owner = None
                    self._cond.notify_all()
        except Exception:
            pass

    def current_owner(self) -> Any:
        return self._owner


_BROWSER_EXCL = BrowserExclusivity()


def get_browser_exclusivity() -> BrowserExclusivity:
    return _BROWSER_EXCL


def reset_browser_exclusivity_for_tests() -> None:
    global _BROWSER_EXCL
    _BROWSER_EXCL = BrowserExclusivity()
