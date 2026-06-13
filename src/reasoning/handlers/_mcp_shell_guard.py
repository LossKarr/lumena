"""
_mcp_shell_guard.py — Phase I-2 : détecte les installs MCP via shell.

But : bloquer les contournements du flux MCP officiel. Quand Lumena tente
d'installer un MCP avec `run_command("npm install -g @modelcontextprotocol/...")`,
elle court-circuite :
  - le sandbox `data/mcp/<server_id>/`
  - le Catalog (statut DECLARED → INSTALLED)
  - la persistance du schéma de config
  - les credentials par MCP

Doctrine :
  - Fonction PURE, sans I/O.
  - Détection conservative : on bloque uniquement les cas FORTEMENT suspects
    (commande d'install ET package MCP-like).
  - Une commande de dev générique (`npm install` dans un projet user) n'est
    PAS bloquée.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple


# ──────────────────────────────────────────────────────────────────────────────
# Patterns
# ──────────────────────────────────────────────────────────────────────────────

# Commandes d'install supportées (cherche dans la ligne).
_INSTALL_VERBS = (
    re.compile(r"\bnpm\s+(?:install|i|add)\b"),
    re.compile(r"\bpnpm\s+(?:install|add|i)\b"),
    re.compile(r"\byarn\s+(?:add|install)\b"),
    re.compile(r"\bpip3?\s+install\b"),
    re.compile(r"\bpipx\s+(?:install|run)\b"),
    re.compile(r"\buv\s+pip\s+install\b"),
    # Phase I-8 (Fix AK.3) : `uv tool install` — observé runtime
    # 2026-06-12 10:37 (`uv tool install mcp-duckduckgo`), passé au
    # travers et bloqué seulement par la whitelist générique dont le
    # message (souris/clavier) égare le LLM au lieu de le rediriger
    # vers add_mcp.
    re.compile(r"\buv\s+tool\s+(?:install|run)\b"),
    re.compile(r"\buvx\b"),
    re.compile(r"\bnpx\b"),
)

# Packages MCP-like : on extrait UN nom de package suspect dans la commande.
# Ces patterns sont conservateurs : on cherche des marqueurs clairs.
_MCP_PACKAGE_PATTERNS = (
    # @modelcontextprotocol/server-*  (officiels Anthropic)
    re.compile(
        r"(@modelcontextprotocol/[A-Za-z0-9_.\-]+)",
    ),
    # @<scope>/mcp-* ou @<scope>/*-mcp  (communautaires)
    re.compile(
        r"(@[A-Za-z0-9][A-Za-z0-9_.\-]*/(?:mcp-[A-Za-z0-9_.\-]+|"
        r"[A-Za-z0-9_.\-]+-mcp[A-Za-z0-9_.\-]*))",
    ),
    # Préfixes simples : mcp-server-*, mcp_server_*, mcp-*
    re.compile(
        r"(?<![A-Za-z0-9_./@-])"
        r"(mcp[-_](?:server[-_])?[A-Za-z0-9][A-Za-z0-9_.\-]+)",
    ),
    # Phase I-8 (Fix AK) : suffixe NON scopé `<nom>-mcp` / `<nom>_mcp`
    # (ex: bitcoin-mcp, weather_mcp). Trou observé runtime 2026-06-11
    # 21:57 : `pip install bitcoin-mcp` est passé au travers et a
    # installé le package DANS LE VENV DE LUMENA (mcp 1.27.2, pyjwt,
    # sse-starlette ajoutés à l'app) — exactement le contournement de
    # sandbox que ce guard doit empêcher.
    # Fix AK.3 : suffixe optionnel `-server` après `-mcp`
    # (duckduckgo-mcp-server) — la forme la plus courante sur PyPI.
    re.compile(
        r"(?<![A-Za-z0-9_./@-])"
        r"([A-Za-z0-9][A-Za-z0-9_.\-]*[-_]mcp(?:[-_]server)?)"
        r"(?![A-Za-z0-9_-])",
    ),
)


@dataclass(frozen=True)
class MCPShellInstallDetection:
    """Résultat d'une détection."""
    detected_tool: str        # "npm install", "pip install", "npx", ...
    detected_package: str     # nom du package suspect
    suggested_target: str     # à passer à add_mcp(target=...)


# ──────────────────────────────────────────────────────────────────────────────
# API publique
# ──────────────────────────────────────────────────────────────────────────────


def _find_install_verb(command_lower: str) -> Optional[str]:
    """Retourne la commande d'install détectée (forme texte), ou None."""
    for pat in _INSTALL_VERBS:
        m = pat.search(command_lower)
        if m is not None:
            return m.group(0)
    return None


def _find_mcp_package(command: str) -> Optional[str]:
    """Retourne le 1er package MCP-like détecté dans la commande, ou None.

    Note : on parcourt sur la commande ORIGINALE (case-sensitive) pour
    préserver le nom exact du package.
    """
    for pat in _MCP_PACKAGE_PATTERNS:
        m = pat.search(command)
        if m is not None:
            return m.group(1)
    return None


def _derive_suggested_target(package: str, verb: str) -> str:
    """Construit un argument propre pour `add_mcp(target=...)`.

    Stratégie :
      - npm/npx/pnpm/yarn  → "npm:<package>"
      - pip/uv/uvx/pipx    → "pypi:<package>"
      - sinon              → le slug nu (Lumena fera le matching curated)
    """
    pkg_clean = package.strip().strip("\"'")
    verb_low = verb.lower()
    if "pip" in verb_low or "uv" in verb_low:
        return f"pypi:{pkg_clean}"
    if any(t in verb_low for t in ("npm", "npx", "yarn", "pnpm")):
        return f"npm:{pkg_clean}"
    return pkg_clean


def detect_mcp_shell_install(command: str) -> Optional[MCPShellInstallDetection]:
    """Détecte une tentative d'install MCP via shell.

    Args:
        command: chaîne brute fournie à `run_command`.

    Returns:
        MCPShellInstallDetection si la commande contient À LA FOIS un
        install verb (npm install, pip install, uvx, ...) ET un package
        MCP-like (`@modelcontextprotocol/...`, `mcp-*`, etc.).
        None sinon.

    Garde-fous :
      - Fonction PURE — aucun side-effect.
      - Si command n'est pas une str non-vide → None (rien à bloquer).
      - Conservative : ne bloque que les cas FORTEMENT suspects, jamais une
        commande npm/pip générique qui ne touche pas à un MCP.
    """
    if not isinstance(command, str) or not command.strip():
        return None
    command_lower = command.lower()
    verb = _find_install_verb(command_lower)
    if verb is None:
        return None
    package = _find_mcp_package(command)
    if package is None:
        return None
    return MCPShellInstallDetection(
        detected_tool=verb.strip(),
        detected_package=package,
        suggested_target=_derive_suggested_target(package, verb),
    )


# Phase I-8 (Fix AP) : outils ReAct MCP écrits comme commande shell.
# Observé runtime 2026-06-12 03:39 : DeepSeek a émis
# `run_command("run_mcp_autonomy(intent=..., live=true, ...)")` — la
# guidance citait l'outil, le LLM l'a pris pour une commande. Le refus
# de la whitelist (qui parle de souris/clavier) l'a égaré : il a conclu
# « cet outil n'existe pas » et abandonné le flux MCP.
_REACT_MCP_TOOLS = (
    "run_mcp_autonomy",
    "add_mcp",
    "request_mcp_capability",
    "resume_mcp_task",
    "request_mcp_ticket",
)

_REACT_TOOL_AS_SHELL = re.compile(
    r"^\s*(" + "|".join(_REACT_MCP_TOOLS) + r")\s*\(",
)


def detect_react_tool_as_shell(command: str) -> Optional[str]:
    """Détecte un outil ReAct MCP émis comme commande shell.

    Returns le nom de l'outil si la commande COMMENCE par
    `<tool_mcp>(...)` (syntaxe d'appel, jamais une vraie commande
    shell), None sinon. Fonction PURE, conservative (ancrée en début
    de commande).
    """
    if not isinstance(command, str) or not command.strip():
        return None
    m = _REACT_TOOL_AS_SHELL.match(command)
    if m is None:
        return None
    return m.group(1)


def list_install_verbs() -> List[str]:
    """Utilitaire de test : liste les regex sources."""
    return [p.pattern for p in _INSTALL_VERBS]


def list_mcp_package_patterns() -> List[str]:
    """Utilitaire de test : liste les regex sources."""
    return [p.pattern for p in _MCP_PACKAGE_PATTERNS]


__all__ = [
    "MCPShellInstallDetection",
    "detect_mcp_shell_install",
    "detect_react_tool_as_shell",
    "list_install_verbs",
    "list_mcp_package_patterns",
]
