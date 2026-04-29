"""
Sandbox Docker pour l'exécution de commandes Lumena.

Exécute les commandes dans un container Docker isolé au lieu de directement
sur la machine hôte. Fallback sur exécution locale si Docker n'est pas disponible.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path
from typing import Optional, Tuple

from loguru import logger

# ─── Configuration ──────────────────────────────────────────────────────────

_DOCKER_IMAGE = os.getenv("LUMENA_SANDBOX_IMAGE", "lumena-sandbox")
_DOCKER_MEMORY = os.getenv("LUMENA_SANDBOX_MEMORY", "512m")
_DOCKER_CPUS = os.getenv("LUMENA_SANDBOX_CPUS", "1")

# ─── Mode sandbox : auto / always / never ──────────────────────────────────
# "auto"   = commandes système Windows en local, code/scripts en Docker
# "always" = tout passe par Docker (sécurité max, mais pas de commandes Windows)
# "never"  = pas de Docker, tout en local (comportement pré-Docker)
SANDBOX_MODES = ("auto", "always", "never")


def get_sandbox_mode() -> str:
    """Retourne le mode sandbox actuel depuis l'env."""
    mode = os.environ.get("LUMENA_SANDBOX_MODE", "auto").lower().strip()
    return mode if mode in SANDBOX_MODES else "auto"


def set_sandbox_mode(mode: str) -> None:
    """Change le mode sandbox en live (appelé par l'API config)."""
    mode = mode.lower().strip()
    if mode not in SANDBOX_MODES:
        logger.warning("[sandbox] Mode invalide '{}', ignoré", mode)
        return
    os.environ["LUMENA_SANDBOX_MODE"] = mode
    logger.info("[sandbox] Mode changé → {}", mode)


# ─── Commandes qui DOIVENT tourner sur l'hôte Windows (pas dans le container Linux) ──
import re as _re

_LOCAL_COMMANDS = frozenset({
    # Commandes Windows natives (inexistantes dans le container Linux)
    "powershell", "powershell.exe", "pwsh", "pwsh.exe",
    "cmd", "cmd.exe",
    "tasklist", "tasklist.exe", "taskkill", "taskkill.exe",
    "netstat", "netstat.exe", "ipconfig", "ipconfig.exe",
    "systeminfo", "systeminfo.exe",
    "wmic", "wmic.exe",
    "sc", "sc.exe",
    "schtasks", "schtasks.exe",
    "reg", "reg.exe",
    "netsh", "netsh.exe",
    "nbtstat", "nbtstat.exe",
    "arp", "arp.exe",
    "tracert", "tracert.exe",
    "pathping", "pathping.exe",
    "route", "route.exe",
    "net", "net.exe",
    "ver",
    "dxdiag", "dxdiag.exe",
    "msinfo32", "msinfo32.exe",
    "certutil", "certutil.exe",
    "icacls", "icacls.exe",
    "winget",
    "choco", "choco.exe",
    "msiexec", "msiexec.exe",
    # Explorateur / applications Windows
    "start", "start.exe",
    "explorer", "explorer.exe",
    "notepad", "notepad.exe",
    "calc", "calc.exe",
    "mspaint", "mspaint.exe",
    "code", "code.cmd",
    # Apps desktop
    "spotify", "spotify.exe",
    "chrome", "chrome.exe",
    "firefox", "firefox.exe",
    "msedge", "msedge.exe",
    "slack", "slack.exe",
    "discord", "discord.exe",
    "teams", "teams.exe",
    "vlc", "vlc.exe",
    "winword", "excel", "powerpnt",
    # Docker lui-même (ne pas mettre Docker dans Docker)
    "docker", "docker.exe", "docker-compose",
    # Commandes shell basiques (dir, cd sont des builtins cmd.exe)
    "cd", "dir", "echo", "type", "copy", "move", "del", "mkdir", "rmdir",
    "set", "cls", "ver", "vol", "date", "time", "findstr",
})

# Pattern PowerShell cmdlet (Get-Process, Select-Object, etc.)
_PS_CMDLET_PATTERN = _re.compile(r'(?:^|[|;&]\s*)[A-Z][a-z]+-[A-Z]', _re.IGNORECASE)
_LOCALHOST_PATTERN = _re.compile(
    r"(?i)\b(?:https?://)?(?:localhost|127\.0\.0\.1|\[::1\]|::1)(?::\d+)?(?:/|\b)"
)


def should_use_sandbox(command: str) -> bool:
    """
    Détermine si une commande doit passer par le sandbox Docker.

    Modes:
      - never  → toujours False
      - always → toujours True
      - auto   → False si commande système Windows, True sinon
    """
    mode = get_sandbox_mode()
    if mode == "never":
        return False
    if mode == "always":
        return True

    # Mode auto : détecter si la commande doit rester locale
    cmd_stripped = command.strip()
    if not cmd_stripped:
        return False

    # Extraire le premier exécutable (avant les pipes, &&, etc.)
    # Gérer "cd [/d] <dir> && <cmd>" → prendre <cmd>
    _cd_m = _re.match(
        r"cd(?:\s+/d)?\s+(?:\"[^\"]+\"|'[^']+'|\S+)\s*&&\s*(.+)",
        cmd_stripped, _re.IGNORECASE,
    )
    effective_cmd = _cd_m.group(1).strip() if _cd_m else cmd_stripped

    # Premier mot = exécutable
    first_word = effective_cmd.split()[0].lower().strip('"\'') if effective_cmd.split() else ""

    # Si le premier exécutable est une commande locale → pas de sandbox
    if first_word in _LOCAL_COMMANDS:
        return False

    # Si la commande contient des cmdlets PowerShell (Verb-Noun) → local
    if _PS_CMDLET_PATTERN.search(cmd_stripped):
        return False

    # Les probes localhost/127.0.0.1/::1 doivent rester sur l'hôte.
    if _LOCALHOST_PATTERN.search(cmd_stripped):
        return False

    # Sinon → sandbox Docker
    return True


# Commandes qui nécessitent un accès réseau (pip install, npm install, etc.)
_NETWORK_COMMANDS = frozenset({
    "pip", "pip3", "npm", "npx", "yarn", "pnpm", "bun",
    "curl", "wget", "git", "cargo", "go",
})

# Cache: Docker est-il disponible ?
_docker_available: Optional[bool] = None
_docker_checked_at: float = 0.0
_DOCKER_NEGATIVE_TTL = float(os.getenv("LUMENA_DOCKER_RETRY_SEC", "300"))  # re-check après 5 min si négatif


async def is_docker_available() -> bool:
    """Vérifie si Docker est installé et le daemon tourne."""
    global _docker_available, _docker_checked_at
    import time
    now = time.monotonic()
    # Cache positif = permanent, cache négatif = TTL 5 min
    if _docker_available is True:
        return True
    if _docker_available is False and (now - _docker_checked_at) < _DOCKER_NEGATIVE_TTL:
        return False
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "info",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=10)
        _docker_available = proc.returncode == 0
    except Exception:
        _docker_available = False
    _docker_checked_at = now
    if _docker_available:
        await _ensure_sandbox_image()
    logger.info("[sandbox] Docker disponible: {}", _docker_available)
    return _docker_available


_sandbox_image_checked = False


async def _ensure_sandbox_image() -> None:
    """Build l'image sandbox si elle n'existe pas encore."""
    global _sandbox_image_checked
    if _sandbox_image_checked:
        return
    _sandbox_image_checked = True
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "image", "inspect", _DOCKER_IMAGE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=10)
        if proc.returncode == 0:
            return  # image existe déjà
    except Exception:
        pass
    # Chercher Dockerfile.sandbox à la racine du projet
    dockerfile = Path(__file__).resolve().parents[2] / "Dockerfile.sandbox"
    if not dockerfile.exists():
        logger.warning("[sandbox] Dockerfile.sandbox introuvable, image '{}' absente — fallback python:3.12-slim", _DOCKER_IMAGE)
        return
    logger.info("[sandbox] Image '{}' absente — build automatique depuis {}...", _DOCKER_IMAGE, dockerfile.name)
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "build", "-f", str(dockerfile), "-t", _DOCKER_IMAGE, str(dockerfile.parent),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
        if proc.returncode == 0:
            logger.info("[sandbox] ✅ Image '{}' construite avec succès", _DOCKER_IMAGE)
        else:
            logger.error("[sandbox] ❌ Build échoué (exit {}): {}", proc.returncode, stderr.decode()[:500])
    except asyncio.TimeoutError:
        logger.error("[sandbox] ❌ Build timeout (300s)")
    except Exception as e:
        logger.error("[sandbox] ❌ Build erreur: {}", e)


def _needs_network(command: str) -> bool:
    """Détecte si une commande nécessite un accès réseau."""
    cmd_lower = command.strip().lower()
    first_word = cmd_lower.split()[0] if cmd_lower else ""
    # Vérifier le premier mot ou après cd && ...
    for net_cmd in _NETWORK_COMMANDS:
        if first_word == net_cmd or f"&& {net_cmd} " in cmd_lower or f"&& {net_cmd}" == cmd_lower:
            return True
    # pip install, npm install patterns
    if "install" in cmd_lower and any(p in cmd_lower for p in ("pip", "npm", "yarn", "pnpm", "cargo")):
        return True
    return False


def _build_docker_args(
    command: str,
    workdir: str,
    timeout_sec: int = 120,
    network: bool = False,
) -> list[str]:
    """Construit la ligne de commande docker run."""
    args = [
        "docker", "run", "--rm",
        "--memory", _DOCKER_MEMORY,
        "--cpus", _DOCKER_CPUS,
        "--pids-limit", os.environ.get("LUMENA_SANDBOX_PIDS_LIMIT", "256"),
    ]

    # Réseau : bridge si nécessaire, sinon none
    if network:
        args += ["--network", "bridge"]
    else:
        args += ["--network", "none"]

    # Monter le workspace en RW
    workdir_path = Path(workdir).resolve()
    if workdir_path.exists():
        # Docker Desktop WSL2 : convertir le chemin Windows en format Docker
        mount_src = str(workdir_path).replace("\\", "/")
        args += ["-v", f"{mount_src}:/work:rw"]
        args += ["-w", "/work"]

    args += [_DOCKER_IMAGE, "bash", "-c", command]
    return args


async def run_in_sandbox(
    command: str,
    workdir: str,
    timeout_sec: int = 120,
    stdin_input: str = "",
) -> Tuple[str, str, int]:
    """
    Exécute une commande dans un container Docker sandbox.

    Returns:
        Tuple (stdout, stderr, exit_code)
    """
    network = _needs_network(command)
    docker_args = _build_docker_args(command, workdir, timeout_sec, network)

    logger.info(
        "[sandbox] docker run (network={}, timeout={}s): {}",
        "bridge" if network else "none", timeout_sec, command[:200],
    )

    stdin_pipe = asyncio.subprocess.PIPE if stdin_input else None
    proc = await asyncio.create_subprocess_exec(
        *docker_args,
        stdin=stdin_pipe,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    stdin_bytes = stdin_input.encode("utf-8") if stdin_input else None

    try:
        stdout_raw, stderr_raw = await asyncio.wait_for(
            proc.communicate(input=stdin_bytes),
            timeout=timeout_sec + int(os.environ.get("LUMENA_SANDBOX_STARTUP_MARGIN", "30")),  # marge startup
        )
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        return "", f"Timeout sandbox ({timeout_sec}s)", -1

    stdout = stdout_raw.decode("utf-8", errors="replace")
    stderr = stderr_raw.decode("utf-8", errors="replace")
    exit_code = proc.returncode or 0

    return stdout, stderr, exit_code


async def run_lint_in_sandbox(
    file_path: str,
    workdir: str,
) -> str:
    """
    Lint un fichier Python via ruff dans le sandbox.
    Retourne "" si OK, message d'erreur sinon.
    """
    if not file_path.endswith(".py"):
        return ""

    # Construire le chemin relatif au workdir
    abs_path = Path(file_path) if Path(file_path).is_absolute() else Path(workdir) / file_path
    if not abs_path.exists():
        return ""

    rel_path = abs_path.relative_to(workdir) if abs_path.is_relative_to(workdir) else abs_path.name

    # Installer ruff + lint en une commande
    cmd = (
        "pip install -q ruff 2>/dev/null; "
        f'ruff check --select E,F,W --no-fix --output-format concise "/work/{rel_path}"'
    )

    stdout, stderr, exit_code = await run_in_sandbox(
        cmd, workdir, timeout_sec=30,
    )

    if exit_code != 0 and stdout.strip():
        return stdout.strip()[:600]
    return ""


def reset_cache() -> None:
    """Reset le cache de disponibilité Docker (pour les tests)."""
    global _docker_available
    _docker_available = None
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
