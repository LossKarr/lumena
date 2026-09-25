"""Detection des lancements INTERACTIFS (shell ou interpreteur qui attend l'entree).

Lot natif prealable a CONN-5C-2. Un shell interactif alimente ensuite par
`process_input` recoit du texte brut : ni le sanitizer ni G1 ne voient ces lignes.
En mission, un tel lancement est refuse ; une commande unique (`cmd /c ...`,
`python script.py`, `node -e ...`) reste jugee par les gardes habituelles.

Conservateur dans le sens du refus seulement pour les programmes listes ici : un
executable inconnu n'est jamais signale.
"""
from __future__ import annotations

import shlex

from .command_sanitizer import _split_shell_operators_respecting_quotes

_SHELLS_CMD = frozenset({"cmd"})
_SHELLS_PS = frozenset({"powershell", "pwsh"})
_SHELLS_POSIX = frozenset({"bash", "sh", "zsh", "fish"})
_PYTHONS = frozenset({"python", "python3", "py", "pythonw"})
_NODES = frozenset({"node", "nodejs"})

# Options Python qui consomment la valeur suivante.
_PYTHON_VALUE_OPTIONS = frozenset({"-W", "-X", "--check-hash-based-pycs"})
_PS_COMMAND_OPTIONS = ("-command", "-c", "-file", "-f", "-encodedcommand", "-ec", "-e")


def _program(token: str) -> str:
    name = token.strip("\"'").replace("\\", "/").rsplit("/", 1)[-1].lower()
    return name[:-4] if name.endswith(".exe") else name


def _tokens(sub: str) -> list:
    try:
        return shlex.split(sub, posix=False)
    except ValueError:
        return sub.split()


def _cmd_interactive(args: list) -> bool:
    lowered = [a.lower() for a in args]
    if "/k" in lowered:
        return True
    return "/c" not in lowered


def _powershell_interactive(args: list) -> bool:
    lowered = [a.lower() for a in args]
    if "-noexit" in lowered:
        return True
    for arg in lowered:
        if arg.startswith("-"):
            if arg in _PS_COMMAND_OPTIONS or (len(arg) > 3 and "-command".startswith(arg)):
                return False
            continue
        return False  # argument nu : script ou commande
    return True


def _posix_shell_interactive(args: list) -> bool:
    if "-i" in args:
        return True
    return not any(a == "-c" or not a.startswith("-") for a in args)


def _python_interactive(args: list) -> bool:
    if "-i" in args:
        return True
    skip = False
    for arg in args:
        if skip:
            skip = False
            continue
        if arg in ("-c", "-m"):
            return False
        if arg in _PYTHON_VALUE_OPTIONS:
            skip = True
            continue
        if arg.startswith("-"):
            continue
        return False  # script
    return True


def _node_interactive(args: list) -> bool:
    if "-i" in args or "--interactive" in args:
        return True
    for arg in args:
        if arg in ("-e", "--eval", "-p", "--print"):
            return False
        if not arg.startswith("-"):
            return False
    return True


def interactive_launch(command: str) -> str:
    """Rend la sous-commande qui lance un shell ou un interpreteur interactif, `""` sinon."""
    if not isinstance(command, str) or not command.strip():
        return ""
    for sub in _split_shell_operators_respecting_quotes(command):
        tokens = _tokens((sub or "").strip())
        if not tokens:
            continue
        program, args = _program(tokens[0]), tokens[1:]
        if program in _SHELLS_CMD:
            interactive = _cmd_interactive(args)
        elif program in _SHELLS_PS:
            interactive = _powershell_interactive(args)
        elif program in _SHELLS_POSIX:
            interactive = _posix_shell_interactive(args)
        elif program in _PYTHONS:
            interactive = _python_interactive(args)
        elif program in _NODES:
            interactive = _node_interactive(args)
        else:
            interactive = False
        if interactive:
            return sub.strip()
    return ""
