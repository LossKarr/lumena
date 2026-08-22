"""
Sanitisation des commandes shell pour Lumena.

Valide les commandes avant execution via une approche whitelist
au lieu d'une blacklist facilement contournable.
"""

from __future__ import annotations

import os
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

# M1 (run RévizIA) — exécutables SERVEUR WEB hors whitelist : le refus doit guider
# vers start_preview_server (registre de preview → browser_navigate autorisé),
# pas vers la guidance souris/clavier générique.
_WEB_SERVER_EXES = frozenset({"flask", "uvicorn", "gunicorn", "waitress", "waitress-serve", "http-server"})

# Verbes PowerShell autorisés (lecture / diagnostic / formatage / lancement)
_PS_SAFE_VERBS: Set[str] = {
    "get", "test", "format", "select", "where", "sort", "measure",
    "write", "out", "foreach", "read", "find", "group", "join",
    "show", "trace", "compare", "convert", "push", "pop",
    "invoke",  # Invoke-WebRequest, Invoke-RestMethod
    "resolve", "split", "join",
    "start",   # Start-Process / Start-Service / Start-Job — lancer une app/un process
}

# Verbes PowerShell toujours bloqués (modification système)
_PS_BLOCKED_VERBS: Set[str] = {
    "remove", "delete", "stop", "kill", "reset", "clear", "set",
    "new", "add", "copy", "move", "rename",
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

    # 2b. M1bis-F2 (run MiniQuiz 2026-07-06) — serveurs lancés par des voies
    # WHITELISTÉES : `Start-Process python app.py` (verbe PS `start` autorisé,
    # python whitelisté) a contourné la guidance serveur M1.b et laissé un Flask
    # orphelin qui tenait les pipes → run_command pendu, worker gelé à jamais.
    # GUIDANCE seulement (doctrine revue M1), au niveau commande COMPLÈTE,
    # AVANT les branches whitelist/verbes PS.
    _cmd_low_full = command_stripped.lower()
    if re.search(
        r"start-process\b[^|;]*\b(?:python3?|py|node|npm|npx|flask|uvicorn|"
        r"gunicorn|waitress(?:-serve)?|http-server)\b",
        _cmd_low_full,
    ):
        logger.warning("Commande bloquee (Start-Process serveur/détaché): {}", command_stripped[:80])
        return False, (
            "Processus détaché refusé : Start-Process lance un process que le "
            "runner ne peut ni suivre ni arrêter (il garde les sorties ouvertes "
            "et gèle run_command). Pour un serveur web : APPELLE L'OUTIL "
            "serve_website(directory='<dossier de l'app>', port=8081) — il "
            "enregistre le port au registre de preview, ce qui autorise ensuite "
            "browser_navigate. Pour un simple script : lance-le DIRECTEMENT "
            "(python script.py), sans Start-Process."
        )
    if re.search(
        r"(?:\s-m\s+(?:flask|uvicorn|gunicorn|http\.server)\b|\bflask\s+(?:--app\s+\S+\s+)?run\b)",
        _cmd_low_full,
    ):
        logger.warning("Commande bloquee (serveur web via module whitelisté): {}", command_stripped[:80])
        return False, (
            "Serveur web : ne lance PAS de serveur a la main. APPELLE L'OUTIL "
            "serve_website(directory='<dossier de l'app>', port=8081) : il "
            "enregistre le port au registre de preview, ce qui autorise ensuite "
            "browser_navigate pour la verification. Un serveur lance a la main "
            "est bloque par la protection SSRF (navigateur inutilisable dessus)."
        )

    # 2c. 2.6.2 (run MiniQuiz §5) — Start-Job : job PowerShell détaché que le
    # runner ne voit pas. `powershell -Command "$j=Start-Job -ScriptBlock
    # {python app.py}; ..."` a lancé 3 serveurs Flask fantômes hors de tout
    # contrôle (le verbe `start` est whitelisté et le contenu de la chaîne
    # -Command n'était pas re-scanné). Blocage au niveau commande COMPLÈTE.
    if re.search(r"\bstart-job\b", _cmd_low_full):
        logger.warning("Commande bloquee (Start-Job détaché): {}", command_stripped[:80])
        return False, (
            "Processus détaché refusé : Start-Job lance un job invisible pour le "
            "runner (impossible à suivre ou arrêter). Pour un serveur web : "
            "APPELLE L'OUTIL serve_website(directory='<dossier de l'app>', "
            "port=8081). Pour un simple script : lance-le DIRECTEMENT "
            "(python script.py), en avant-plan."
        )

    # 2d. 2.6.2 — écriture de fichier via cmdlets PS, y compris IMBRIQUÉS dans
    # `powershell -Command "..."` : `(Get-Content app.py) -replace 'port=8081',
    # 'port=8085' | Set-Content app.py` a contourné le périmètre I.2 du CodeAgent
    # et VIOLÉ le contrat (port muté). Le check de verbe (4a) ne voit que le
    # cmdlet de TÊTE — ici on scanne toute la commande. Out-File/Add-Content ont
    # des verbes whitelistés (out/add… `out` est safe) → même trou, même fermeture.
    if re.search(r"\b(?:set-content|out-file|add-content)\b", _cmd_low_full):
        logger.warning("Commande bloquee (écriture fichier via cmdlet PS): {}",
                       command_stripped[:80])
        return False, (
            "Écriture de fichier via le shell refusée (Set-Content/Out-File/"
            "Add-Content, même dans powershell -Command). Utilise tes outils "
            "d'édition : write_file / edit_file / apply_patch — le périmètre de "
            "mission s'applique à eux, pas au shell."
        )

    # 2e. 2.6.2 — python -c qui ÉCRIT un fichier ou LANCE un serveur : deux
    # contournements du même run (création de test_run_desktop.py par
    # open(...,'w').write(...), et le lead servant Flask via
    # `python -c "from app import create_app; app.run(port=8085)"` → serveur
    # hors registre SSRF → navigateur bloqué → fabrication).
    if re.search(
        r"(?:python3?|py)(?:\.exe)?\b[^|;&]*\s-c\s.*"
        r"(?:open\s*\([^)]*['\"](?:w|a|r\+|w\+|a\+)['\"]|\.write\s*\()",
        _cmd_low_full,
    ):
        logger.warning("Commande bloquee (écriture fichier via python -c): {}",
                       command_stripped[:80])
        return False, (
            "Écriture de fichier via `python -c \"open(...).write(...)\"` refusée. "
            "Utilise tes outils d'édition : write_file / edit_file / apply_patch — "
            "le périmètre de mission s'applique à eux, pas au shell."
        )
    if re.search(
        r"(?:python3?|py)(?:\.exe)?\b[^|;&]*\s-c\s.*"
        r"(?:\bapp\s*\.\s*run\s*\(|\bcreate_app\s*\(|serve_forever|make_server\s*\()",
        _cmd_low_full,
    ):
        logger.warning("Commande bloquee (serveur lancé via python -c): {}",
                       command_stripped[:80])
        return False, (
            "Serveur web : ne lance PAS l'app à la main via python -c. APPELLE "
            "L'OUTIL serve_website(directory='<dossier de l'app>', port=8081) : il "
            "détecte app.py, lance la vraie app Flask et enregistre le port au "
            "registre de preview — sinon browser_navigate restera bloqué (SSRF) "
            "et ta vérification navigateur sera impossible."
        )

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
        # M1 (run RévizIA 2026-07-05) — message spécialisé SERVEUR WEB : le refus de
        # `flask run` renvoyait la guidance souris/clavier (aucun rapport) → le lead
        # abandonnait la vérif navigateur et fabriquait. GUIDANCE seulement, jamais
        # de redirection silencieuse (revue M1) : on indique la voie officielle.
        _cmd_low_srv = command_stripped.lower()
        if (
            exe_base in _WEB_SERVER_EXES
            or "http.server" in _cmd_low_srv
            or "runserver" in _cmd_low_srv
        ):
            return False, (
                f"Executable '{executable}' non autorise. Serveur web : ne lance PAS "
                "de serveur a la main. APPELLE L'OUTIL "
                "serve_website(directory='<dossier de l'app>', port=8081) : il "
                "enregistre le port au registre de preview, ce qui autorise ensuite "
                "browser_navigate pour la verification. Un serveur lance a la main "
                "est bloque par la protection SSRF (navigateur inutilisable dessus)."
            )
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
        # B0.4a (run PlantCare) — shlex en mode POSIX MANGE les backslashes d'un
        # chemin Windows quoté ("C:\...\python.exe" → C:Users...python.exe) : plus
        # aucun séparateur → l'extraction du basename ne s'applique pas → le chemin
        # mutilé entier est comparé à la whitelist → faux blocage du venv python.
        # On normalise les séparateurs AVANT le parsing (le basename est déjà
        # extrait en '/').
        parts = shlex.split(first_cmd.replace("\\", "/"))
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
# LOT G1 — garde de CIBLE pour les commandes destructives en mission
#
# Le reste de ce module juge la DANGEROSITE d'une commande : `rm -rf` et
# `del /s /f /q` sont bloques, tandis que `del <fichier>` est explicitement
# autorise (un worker doit pouvoir nettoyer SES fichiers).
#
# Il manquait la seconde dimension : la PROPRIETE de la cible. Le 2026-08-12, une
# mission a execute `del C:\...\lumena\pytest.ini` (exit 0) pour contourner un
# conflit de configuration pytest — supprimant un fichier du depot de
# l'utilisateur. `Rename-Item` sur le meme fichier avait ete bloque (verbe
# interdit) ; `pyproject.toml` n'a survecu que par hasard de sequence.
#
# `del rapport.md` dans le dossier de mission est legitime.
# `del <depot>/pytest.ini` ne l'est pas. Meme commande, meme verdict jusqu'ici.
#
# Ce garde est ADDITIF : il ne retire rien a l'allowlist, aux BLOCKED_PATTERNS ni
# aux verbes PowerShell. Il est CONSERVATEUR : tout doute (chemin ambigu, racine
# inconnue) laisse passer — sur-bloquer casserait les missions.
# ──────────────────────────────────────────────────────────────────────────────

# Verbes qui DETRUISENT ou DEPLACENT un fichier existant. `move`/`ren` en font
# partie : deplacer `pytest.ini` equivaut a le supprimer de sa place.
_DESTRUCTIVE_VERBS: Set[str] = {
    "del", "del.exe", "erase", "rm", "rmdir", "rd", "unlink",
    "move", "mv", "ren", "rename",
    "remove-item", "move-item", "rename-item", "clear-content", "set-content",
}

# G1.b — L'INTENTION destructive, ou qu'elle se trouve dans la ligne.
#
# La v1 de ce garde n'inspectait que le PREMIER mot de chaque sous-commande. Cinq
# contournements triviaux passaient : `python -c "os.remove(...)"`,
# `node -e "unlinkSync(...)"`, `powershell -Command "del ..."`, `cmd /c del ...`
# et l'ecrasement par redirection `echo x > fichier`.
#
# Enumerer les facons de detruire est une course perdue (le meme piege que la
# course aux regex sur le texte des finals). On inverse donc la logique :
#   (un chemin PROTEGE apparait) x (une intention destructive apparait) -> refus
# quel que soit l'enrobage : shell, interpreteur, redirection.
#
# Motifs volontairement SPECIFIQUES (`os.remove`, `unlinkSync`, `rmtree`...) pour
# ne pas se declencher sur de la prose : le mot nu « remove » ne compte pas.
_DESTRUCTIVE_INTENT_RE = re.compile(
    r"(?:"
    r"(?<![\w.-])(?:del|erase|rm|rmdir|rd|unlink|move|mv|ren|rename)(?![\w.-])"
    r"|remove-item|move-item|rename-item|clear-content|set-content"
    r"|os\.(?:remove|unlink|rmdir|rename|replace|truncate)"
    r"|shutil\.(?:rmtree|move)"
    r"|pathlib|\.unlink\s*\(|\.rmdir\s*\("
    r"|unlinksync|rmsync|rmdirsync|renamesync|truncatesync"
    r"|fs\.(?:unlink|rm|rmdir|rename|truncate|writefile)"
    r"|file_put_contents|>\s*&?\s*\S|>>"
    r")",
    re.IGNORECASE,
)

# Extraction des chemins PARTOUT dans la ligne, y compris a l'interieur d'une
# chaine passee a un interpreteur (`python -c "... 'C:/…/pytest.ini' ..."`).
_EMBEDDED_PATH_RE = re.compile(
    r"(?:[A-Za-z]:[\\/][^\"'\s,;)]*|(?:\.\.[\\/])+[^\"'\s,;)]*|/[^\"'\s,;)]{2,})"
)

# Tokens d'une ligne de commande, guillemets respectes (les chemins Windows
# contiennent des espaces : "C:\Program Files\...").
_CMD_TOKEN_RE = re.compile(r'"([^"]*)"|\'([^\']*)\'|(\S+)')


def _looks_like_path(token: str) -> bool:
    """Heuristique conservatrice : ce token designe-t-il un chemin ?"""
    if not token or token.startswith("-") or token.startswith("/"):
        return False  # option de ligne de commande, pas une cible
    return ("/" in token) or ("\\" in token) or ("." in token.strip("."))


def _normalized_parts(path_str: str) -> Optional[Tuple[str, ...]]:
    """Chemin -> tuple de segments normalises (minuscules), sans I/O disque.

    Purement lexical : `os.path.normpath` resout `..` et `.` sans toucher au
    systeme de fichiers, ce qui garde le helper testable hors runtime.
    """
    try:
        cleaned = str(path_str).strip().strip('"').strip("'").replace("\\", "/")
        if not cleaned:
            return None
        normalized = os.path.normpath(cleaned).replace("\\", "/")
        return tuple(p.lower() for p in normalized.split("/") if p not in ("", "."))
    except Exception:
        return None


def _is_within(parts: Tuple[str, ...], root: Tuple[str, ...]) -> bool:
    """`parts` est-il egal a `root` ou situe dessous ?"""
    return bool(root) and len(parts) >= len(root) and parts[: len(root)] == root


def destructive_command_target_violation(
    command: str,
    *,
    mission_root: Optional[str] = None,
    repo_root: Optional[str] = None,
) -> str:
    """Retourne le chemin fautif si une commande DESTRUCTIVE vise un fichier du
    depot situe HORS du workspace de la mission ; `""` sinon.

    Args:
        command: la ligne de commande complete (chainages compris).
        mission_root: dossier de mission du worker. `None`/vide -> helper INERTE
            (hors mission : chat, CodeAgent direct, autonomie — inchanges).
        repo_root: racine du depot Lumena a proteger.

    Conservateur par construction :
    - une commande sans verbe destructif n'est jamais signalee (`pytest`, `git
      status`, `node --check` restent libres, meme sur des chemins du depot) ;
    - un chemin relatif simple est resolu depuis le dossier de mission (cas
      normal) ; il ne devient fautif que s'il en sort via `..` ;
    - un chemin hors du depot n'est pas notre affaire (les autres gardes s'en
      chargent) ;
    - toute erreur d'analyse -> `""` (on ne bloque jamais sur un doute).
    """
    if not command or not mission_root or not repo_root:
        return ""
    try:
        repo_parts = _normalized_parts(repo_root)
        mission_parts = _normalized_parts(mission_root)
        if not repo_parts or not mission_parts:
            return ""

        for sub in _split_shell_operators_respecting_quotes(command):
            sub = (sub or "").strip()
            if not sub:
                continue
            # G1.b — l'intention destructive peut etre n'importe ou : premier mot
            # (`del x`), corps d'un interpreteur (`python -c "os.remove(...)"`),
            # ou simple redirection (`echo x > fichier`).
            if not _DESTRUCTIVE_INTENT_RE.search(sub):
                continue

            # Candidats : tokens de la ligne ET chemins noyes dans une chaine.
            candidates = [
                (m.group(1) or m.group(2) or m.group(3) or "")
                for m in _CMD_TOKEN_RE.finditer(sub)
            ]
            candidates += [m.group(0) for m in _EMBEDDED_PATH_RE.finditer(sub)]

            for raw in candidates:
                token = (raw or "").strip().strip("\"'()[],;")
                if not token or not _looks_like_path(token):
                    continue
                candidate = token.replace("\\", "/")
                is_absolute = candidate.startswith("/") or re.match(r"^[A-Za-z]:", candidate)
                parts = (
                    _normalized_parts(candidate)
                    if is_absolute
                    else _normalized_parts(f"{mission_root}/{candidate}")
                )
                if not parts:
                    continue
                # Dans le depot ET hors du workspace de la mission -> violation.
                if _is_within(parts, repo_parts) and not _is_within(parts, mission_parts):
                    return token
        return ""
    except Exception:
        return ""
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
