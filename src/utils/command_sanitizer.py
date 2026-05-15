"""
Sanitisation des commandes shell pour Lumena.

Valide les commandes avant execution via une approche whitelist
au lieu d'une blacklist facilement contournable.
"""

from __future__ import annotations

import shlex
import re
from typing import Tuple, Optional, Set
from loguru import logger


# Executables autorises par defaut
DEFAULT_ALLOWED_EXECUTABLES: Set[str] = {
    # Python
    "python", "python3", "python.exe", "pip", "pip3", "pip.exe",
    "pytest", "pytest.exe", "mypy", "ruff", "black", "isort",
    # Node/JS
    "node", "node.exe", "npm", "npm.cmd", "npx", "npx.cmd",
    "yarn", "yarn.cmd", "pnpm", "pnpm.cmd", "bun", "bun.exe",
    "tsc", "tsc.cmd", "eslint", "prettier",
    # Git
    "git", "git.exe", "gh", "gh.exe",
    # Systeme (lecture seule / navigation)
    "ls", "dir", "cat", "type", "head", "tail", "more",
    "find", "where", "which", "whoami", "hostname",
    "echo", "printf", "date", "time",
    "pwd", "cd", "tree", "wc", "sort", "uniq",
    "grep", "findstr", "findstr.exe", "rg", "ag", "awk", "sed",
    "curl", "wget",
    "ping", "ipconfig", "ifconfig", "nslookup",
    "ver",              # Version Windows (lecture seule)
    "uname",            # Version Unix/WSL (lecture seule)
    # Reseau / diagnostic
    "arp", "arp.exe",
    "netstat", "netstat.exe",
    "nbtstat", "nbtstat.exe",
    "tracert", "tracert.exe",
    "traceroute",
    "route",
    "net",           # net view, net share, net use (lecture)
    "nmap",
    # Fichiers (non destructifs)
    "mkdir", "cp", "copy", "xcopy", "robocopy",
    "touch", "mv", "move", "rename", "ren",
    "tar", "zip", "unzip", "7z",
    # Suppression fichiers individuels (del /s /f /q reste bloqué par BLOCKED_PATTERNS)
    "del", "del.exe",
    "Remove-Item",      # PowerShell — Remove-Item -Recurse reste bloqué par BLOCKED_PATTERNS
    # Dev tools
    "cargo", "rustc", "go", "javac", "java", "gcc", "g++", "make", "cmake",
    "docker", "docker-compose",
    # Editeurs CLI
    "code", "code.cmd",
    # Playwright / browsers
    "playwright", "npx.cmd",
    # === WINDOWS: Commandes systeme pour ouvrir des applications ===
    "start", "start.exe",           # Ouvrir apps/fichiers/URLs
    "explorer", "explorer.exe",     # Explorateur Windows
    "notepad", "notepad.exe",       # Bloc-notes
    "calc", "calc.exe",             # Calculatrice
    "mspaint", "mspaint.exe",       # Paint
    "cmd", "cmd.exe",               # Invite de commandes
    "powershell", "powershell.exe", # PowerShell
    "pwsh", "pwsh.exe",             # PowerShell 7+
    "tasklist", "tasklist.exe",     # Liste des processus (lecture seule)
    "systeminfo",                   # Info systeme (lecture seule)
    "wmic",                         # WMI queries
    "netsh", "netsh.exe",            # Configuration réseau
    "sc", "sc.exe",                  # Service control
    "reg", "reg.exe",               # Registre (reg delete bloqué par pattern)
    "taskkill", "taskkill.exe",      # Tuer des processus
    "schtasks", "schtasks.exe",      # Tâches planifiées
    "certutil", "certutil.exe",      # Utilitaire certificats
    "winget",                        # Package manager Windows
    "choco", "choco.exe",            # Chocolatey
    "icacls", "icacls.exe",          # Permissions fichiers
    "pathping", "pathping.exe",      # Traceroute alternatif
    "netsh", "net.exe",
    "ipconfig", "ipconfig.exe",      # Config réseau
    "msiexec", "msiexec.exe",        # Installateur MSI
    "dxdiag", "dxdiag.exe",          # Diagnostic DirectX
    "msinfo32", "msinfo32.exe",      # Info système
    # === Applications courantes ===
    "spotify", "spotify.exe",       # Spotify
    "chrome", "chrome.exe",         # Chrome
    "firefox", "firefox.exe",       # Firefox
    "msedge", "msedge.exe",         # Edge
    "slack", "slack.exe",           # Slack
    "discord", "discord.exe",       # Discord
    "teams", "teams.exe",           # Teams
    "winword", "excel", "powerpnt", # Office
    "vlc", "vlc.exe",               # VLC
}

# Commandes toujours bloquees, meme si l'executable est autorise
BLOCKED_PATTERNS = [
    r"rm\s+(-\w*[rf])",       # rm -rf, rm -f, rm -r
    r"del\s+/[sfq]",          # del /s /f /q
    r"rmdir\s+/s",            # rmdir /s
    r"format\s+[a-zA-Z]:",    # format C:
    r"mkfs",                  # mkfs
    r":\(\)\s*\{",            # fork bomb
    r"shutdown",              # shutdown
    r"reboot",                # reboot
    r"taskkill\s+/f",         # taskkill /f (force kill)
    r"reg\s+delete",          # reg delete
    r"net\s+user",            # net user (modification utilisateurs)
    r"attrib\s+[+-]",         # attrib (modification attributs systeme)
    r"cipher\s+/[we]",        # cipher encrypt/wipe
    r"diskpart",              # diskpart
    r"bcdedit",               # boot config
    r"sfc\s+/",               # system file checker
    r"chkdsk\s+/[rf]",        # chkdsk repair
    # PowerShell dangerous patterns (belt-and-suspenders with _PS_BLOCKED_VERBS)
    r"Remove-Item\s+.*-Recurse",   # Remove-Item -Recurse
    r"Stop-Process\s+.*-Force",     # Stop-Process -Force
    r"Invoke-Expression",           # Invoke-Expression (arbitrary code execution)
    r"IEX\s",                       # IEX alias for Invoke-Expression
    r"Set-ExecutionPolicy",         # Changing execution policy
    # Protocoles réseau de déploiement — bloquer techniquement.
    # Le déploiement passe OBLIGATOIREMENT par deploy_to_ionos / ionos.py.
    r"\bssh\b",                        # ssh (accès shell distant)
    r"\bscp\b",                        # scp (copie SSH)
    r"\brsync\b",                      # rsync (synchronisation SSH)
    r"\bsftp\b",                       # sftp (FTP over SSH)
    r"\bftp\b",                        # ftp (transfert non sécurisé)
    r"\bpsftp\b",                      # PuTTY SFTP
    r"\bpscp\b",                       # PuTTY SCP
    r"\bwinSCP\b",                     # WinSCP CLI
]

# Message spécifique pour les tentatives de déploiement réseau
_DEPLOY_NETWORK_MSG = (
    "⛔ Déploiement réseau bloqué ({exe}). "
    "Le déploiement doit passer par l'outil natif `deploy_to_ionos` "
    "(ou `ionos_add_site`, `update_ionos_files`) qui gère les credentials SFTP "
    "de façon sécurisée. N'utilise JAMAIS ssh/scp/sftp/rsync/ftp directement."
)
_DEPLOY_NETWORK_EXES = frozenset({"ssh", "scp", "sftp", "rsync", "ftp", "psftp", "pscp", "winscp"})

# Verbes PowerShell autorisés (lecture / diagnostic / formatage)
_PS_SAFE_VERBS: Set[str] = {
    "get", "test", "format", "select", "where", "sort", "measure",
    "write", "out", "foreach", "read", "find", "group", "join",
    "show", "trace", "compare", "convert", "push", "pop",
    "invoke",  # Invoke-WebRequest, Invoke-RestMethod
    "resolve", "split", "join",
}

# Verbes PowerShell toujours bloqués (modification système)
_PS_BLOCKED_VERBS: Set[str] = {
    "remove", "delete", "stop", "kill", "reset", "clear", "set",
    "new", "add", "copy", "move", "rename", "start",
    "register", "unregister", "enable", "disable",
    "mount", "dismount", "suspend", "resume",
}

# Regex pour détecter les cmdlets PowerShell (Verb-Noun)
_PS_CMDLET_RE = re.compile(r'^([A-Za-z]+)-[A-Za-z]', re.IGNORECASE)

# Regex pour détecter les expressions PowerShell pures (range, variable, tableau)
_PS_EXPR_RE = re.compile(r'^(\d+\.\.|\$[A-Za-z]|@{|@\(|\[)', re.IGNORECASE)

# Mots-clés de contrôle PowerShell (flow control, fonctions, blocs)
# Autorisés comme "pseudo-exécutables" — ils ne sont pas des programmes mais du langage PS natif
_PS_KEYWORDS: Set[str] = {
    "if", "else", "elseif", "foreach", "while", "do", "until",
    "switch", "try", "catch", "finally", "for", "return",
    "break", "continue", "function", "filter", "param",
    "begin", "process", "end", "class", "enum", "using",
    "throw", "trap", "exit",
}


# Operateurs shell dangereux quand combines avec du code injecte
DANGEROUS_OPERATORS = [
    r"\$\(",          # $(command) substitution
    r"`[^`]+`",       # `command` backtick substitution
    r">\s*/dev/null\s*2>&1\s*&",  # background silencieux
]


def sanitize_command(command: str, extra_allowed: Optional[Set[str]] = None) -> Tuple[bool, str]:
    """
    Valide une commande shell avant execution.

    Returns:
        Tuple (allowed, reason):
        - (True, "") si la commande est autorisee
        - (False, reason) si la commande est bloquee
    """
    if not command or not command.strip():
        return False, "Commande vide"

    command_stripped = command.strip()

    # 0. Détection de credentials dans la commande (fuite de secrets)
    _CREDENTIAL_PATTERNS = [
        r"(?:sftp|ssh|ftp)://[^@\s]+:[^@\s]+@",       # sftp://user:pass@host
        r"\bsshpass\b",                                  # sshpass utility
        r"(?:-[oO]\s*|StrictHostKeyChecking|PasswordAuth).*(?:pass|pwd)",
        r"ConvertTo-SecureString\s+['\"][^'\"]{4,}['\"]",  # PS plaintext→SecureString
        r"Net\.NetworkCredential\s*\(['\"][^'\"]+['\"],\s*['\"][^'\"]+['\"]\)",
        r"(?:password|passwd|pwd|pass)\s*[=:]\s*['\"][^'\"]{4,}['\"]",  # password='...'
    ]
    for _cp in _CREDENTIAL_PATTERNS:
        if re.search(_cp, command_stripped, re.IGNORECASE):
            logger.warning("Commande bloquee (credential leak): {}", command_stripped[:40] + "...")
            return False, (
                "⛔ Commande bloquée: credentials détectés en clair dans la commande. "
                "Utilise les outils natifs (ionos_deploy, ionos_add_site, etc.) "
                "au lieu de passer les mots de passe dans des commandes shell."
            )

    # 1. Verifier les patterns toujours bloques
    for pattern in BLOCKED_PATTERNS:
        if re.search(pattern, command_stripped, re.IGNORECASE):
            logger.warning("Commande bloquee (pattern dangereux): {}", command_stripped[:80])
            return False, f"Commande bloquee: pattern dangereux detecte"

    # 2. Verifier les operateurs d'injection
    # Regex pour détecter un cmdlet PS (Verb-Noun) n'importe où dans la commande
    _PS_CMDLET_ANYWHERE_RE = re.compile(r'(?:^|[|;&\s])[A-Z][a-z]+-[A-Z]', re.IGNORECASE)

    for pattern in DANGEROUS_OPERATORS:
        if re.search(pattern, command_stripped):
            # Exception PS : $() est une subexpression légitime en PowerShell
            # (Write-Host "$($x - $y)" → calcul inline, PAS une injection bash)
            # Les commandes PS ne vont JAMAIS en sandbox Docker (should_use_sandbox=False
            # pour tout ce qui contient un cmdlet) → $() évalué en PS, pas en bash → safe
            if pattern == r"\$\(" and _PS_CMDLET_ANYWHERE_RE.search(command_stripped):
                continue
            logger.warning("Commande bloquee (injection potentielle): {}", command_stripped[:80])
            return False, "Commande bloquee: substitution de commande non autorisee"

    # 3. Extraire l'executable principal
    executable = _extract_executable(command_stripped)
    if not executable:
        return False, "Impossible d'identifier l'executable"

    # 4a. Cmdlets PowerShell (Verb-Noun) : vérifier le verbe contre la blacklist
    ps_match = _PS_CMDLET_RE.match(executable)
    if ps_match:
        verb = ps_match.group(1).lower()
        if verb in _PS_BLOCKED_VERBS:
            logger.warning(
                "Cmdlet PowerShell bloquee (verbe '{}'): {}",
                verb, command_stripped[:80],
            )
            return False, f"Cmdlet PowerShell bloquee: le verbe '{verb}' est interdit"
        return True, ""

    # 4b. Autoriser les expressions PowerShell pures (range 1..254, variables $x, tableaux @{})
    if _PS_EXPR_RE.match(executable):
        return True, ""

    # 4b2. Autoriser les mots-clés de contrôle PowerShell (if, foreach, while, try, switch…)
    if executable.lower() in _PS_KEYWORDS:
        return True, ""

    # 4c. Verifier contre la whitelist standard
    allowed = DEFAULT_ALLOWED_EXECUTABLES.copy()
    if extra_allowed:
        allowed |= extra_allowed

    exe_lower = executable.lower()
    # Accepter le nom nu ou avec extension
    exe_base = exe_lower.rsplit(".", 1)[0] if "." in exe_lower else exe_lower

    if exe_lower not in allowed and exe_base not in allowed:
        logger.warning("Executable non autorise: '{}' (commande: {})", executable, command_stripped[:80])
        # Message spécialisé pour les protocoles de déploiement réseau
        if exe_base in _DEPLOY_NETWORK_EXES:
            return False, _DEPLOY_NETWORK_MSG.format(exe=executable)
        reason = (
            f"Executable '{executable}' non autorise par la whitelist de securite. "
            "Pour controler la souris ou le clavier, utilise directement les outils natifs : "
            "move_mouse, click, double_click, right_click, type_text, press_key, hotkey, scroll, "
            "drag, ui_click, ui_type, mouse_pattern. "
            "Ne pas ecrire de script Python externe pour ces actions."
        )
        cmd_lower = command_stripped.lower()
        if (
            exe_lower in {"powershell", "powershell.exe", "pwsh", "pwsh.exe"}
            and ("compress-archive" in cmd_lower or ".zip" in cmd_lower)
        ):
            reason += (
                " Pour compresser des fichiers, utilise create_zip, puis "
                "telegram_send_document, send_whatsapp_document ou mail_send pour l'envoi."
            )
        return False, reason
    return True, ""


def _extract_executable(command: str) -> Optional[str]:
    """Extrait le nom de l'executable principal d'une commande."""
    # Gerer les commandes chainees (&&, ||, ;)
    # On verifie seulement la premiere commande
    # Les operateurs de chaine sont autorises si chaque commande individuelle est OK

    # Prendre la premiere commande avant &&, ||, ;, |
    # Utiliser un split conscient des guillemets ET des accolades PowerShell {}
    # pour ne pas couper sur un ; ou | à l'intérieur de @{...} ou { scriptblock }
    parts_list = _split_shell_operators_respecting_quotes(command)
    first_cmd = parts_list[0].strip() if parts_list else ""

    if not first_cmd:
        return None

    # Si la commande commence par ( → expression PowerShell (grouping)
    # Ex: (Get-Content file.css -Raw | Select-String '{').Count
    if first_cmd.startswith('('):
        first_cmd = first_cmd.lstrip('(')

    try:
        parts = shlex.split(first_cmd)
        if not parts:
            return None

        exe = parts[0]
        # Extraire juste le nom de fichier si c'est un chemin
        if "/" in exe or "\\" in exe:
            exe = exe.replace("\\", "/").rsplit("/", 1)[-1]
        # shlex posix mode mange le \\ de .\\exe.exe → .exe.exe
        if exe.startswith(".") and not exe.startswith(".."):
            exe = exe.lstrip(".")
        return exe
    except ValueError:
        # shlex.split peut echouer sur des commandes mal formees
        # Fallback: prendre le premier mot
        first_word = first_cmd.split()[0] if first_cmd.split() else None
        if first_word:
            if "/" in first_word or "\\" in first_word:
                first_word = first_word.replace("\\", "/").rsplit("/", 1)[-1]
            if first_word.startswith(".") and not first_word.startswith(".."):
                first_word = first_word.lstrip(".")
        return first_word


def _split_shell_operators_respecting_quotes(command: str) -> list:
    """
    Sépare une commande shell sur les opérateurs (&&, ||, ;, |)
    en ignorant ceux situés à l'intérieur de guillemets simples ou doubles.

    Exemple :
        'python -c "import os; os.getcwd()" && echo ok'
        → ['python -c "import os; os.getcwd()"', 'echo ok']

    Sans cette fonction, re.split couperait naïvement sur le ';' interne,
    créant une fausse sous-commande 'os.getcwd()' et bloquant la commande.
    """
    parts: list = []
    current: list = []
    in_single = False
    in_double = False
    brace_depth = 0  # profondeur des accolades PowerShell { } (scriptblocks, hashtables)
    i = 0
    n = len(command)

    while i < n:
        c = command[i]

        if c == "'" and not in_double:
            in_single = not in_single
            current.append(c)
        elif c == '"' and not in_single:
            in_double = not in_double
            current.append(c)
        elif c == '{' and not in_single and not in_double:
            brace_depth += 1
            current.append(c)
        elif c == '}' and not in_single and not in_double:
            brace_depth = max(0, brace_depth - 1)
            current.append(c)
        elif not in_single and not in_double and brace_depth == 0:
            # Opérateurs à deux caractères : && et ||
            if i + 1 < n and command[i:i + 2] in ("&&", "||"):
                seg = "".join(current).strip()
                if seg:
                    parts.append(seg)
                current = []
                i += 2
                continue
            # Opérateurs à un caractère : ; et |
            elif c in (";", "|"):
                seg = "".join(current).strip()
                if seg:
                    parts.append(seg)
                current = []
            else:
                current.append(c)
        else:
            current.append(c)

        i += 1

    seg = "".join(current).strip()
    if seg:
        parts.append(seg)

    return parts


def sanitize_chained_command(command: str, extra_allowed: Optional[Set[str]] = None) -> Tuple[bool, str]:
    """
    Valide une commande avec operateurs de chaine (&&, ||, ;, |).
    Chaque sous-commande est validee individuellement.

    Utilise un split respectueux des guillemets pour ne pas couper les
    arguments de commandes comme `python -c "..."` sur les ; internes.
    """
    sub_commands = _split_shell_operators_respecting_quotes(command)

    for sub_cmd in sub_commands:
        sub_cmd = sub_cmd.strip()
        if not sub_cmd:
            continue
        allowed, reason = sanitize_command(sub_cmd, extra_allowed)
        if not allowed:
            return False, f"Sous-commande bloquee: {reason}"

    return True, ""
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
