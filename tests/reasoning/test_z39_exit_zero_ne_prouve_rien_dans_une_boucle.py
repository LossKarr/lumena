"""LOT Z39 — `exit:0` rendu sur 26 erreurs consecutives.

Mesure sur le run « SaaS complet » (2026-08-25 04:28, logslumena A.txt) :

    [cmd_start] powershell -NoProfile -Command
        "Get-ChildItem *.php | ForEach-Object { $r = php -l $_.FullName; ... }"
    [cmd_output_err] php : Le terme «php» n'est pas reconnu ...    <- x26
    [cmd_done] exit:0

`ForEach-Object` ne propage pas le code de sortie des commandes natives qu'il
appelle : la boucle reussit meme quand tout ce qu'elle contient echoue.

Le marqueur d'echec pose par le lot A4/A5 (run FitLog) ne se declenchait QUE
sur `exit_code != 0`. Ici il valait 0 — donc rien en tete de l'observation.
Le stderr etait bien present dans le CORPS, mais le modele lit l'en-tete :
il a enchaine sur « validation PHP faite ».

Encore le motif des 53 lots : les 26 erreurs sont capturees, elles sont meme
loguees ligne par ligne, et le `0` gagne la decision.

PERIMETRE — ce lot ne marque PAS « stderr non vide » en general : git, curl et
npm ecrivent des avertissements sur stderr en cas de succes parfaitement
legitime, et un marqueur qui crie a chaque `git push` ne serait plus lu. Il
ferme le seul cas SANS ambiguite : un executable introuvable n'a rien execute,
quel que soit le code de sortie rendu par ce qui l'entourait.
"""

import pytest

from src.reasoning.handlers.system import (
    _COMMAND_NOT_FOUND_SIGNATURES,
    _command_not_found_in,
)


# ══════════════════════════════════════════════════════════════════════════
#  Le cas mesure, mot pour mot
# ══════════════════════════════════════════════════════════════════════════


_STDERR_DU_RUN = (
    "php : Le terme «php» n'est pas reconnu comme nom d'applet de commande, "
    "fonction, fichier de script ou programme\n"
    "exécutable. Vérifiez l'orthographe du nom, ou si un chemin d'accès existe...\n"
    "    + CategoryInfo          : ObjectNotFound: (php:String) [], "
    "CommandNotFoundException\n"
)


def test_le_stderr_exact_du_run_est_reconnu():
    """LE lot. C'est CE texte qui accompagnait `exit:0`."""
    assert _command_not_found_in(_STDERR_DU_RUN) is True


@pytest.mark.parametrize("stderr", [
    "php : Le terme «php» n'est pas reconnu comme nom d'applet de commande",
    "php : The term 'php' is not recognized as the name of a cmdlet",
    "+ FullyQualifiedErrorId : CommandNotFoundException",
    "'php' n'est pas reconnu en tant que commande interne ou externe",
    "'php' is not recognized as an internal or external command",
    "bash: php: command not found",
])
def test_toutes_les_formulations_de_shell_sont_reconnues(stderr):
    assert _command_not_found_in(stderr) is True


# ══════════════════════════════════════════════════════════════════════════
#  Ce que le marqueur ne doit PAS attraper
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("stderr", [
    "warning: LF will be replaced by CRLF in index.html",      # git, succes
    "npm WARN deprecated inflight@1.0.6: memory leak",         # npm, succes
    "  % Total    % Received  Xferd  Average Speed",           # curl, succes
    "Cloning into 'repo'...",                                  # git, succes
    "find: './broken-link': No such file or directory",        # find, exit 0
    "",
    "   \n  \n",
])
def test_un_stderr_legitime_ne_declenche_rien(stderr):
    """Un marqueur qui crie a chaque `git push` cesse d'etre lu. Le cas
    `No such file or directory` est volontairement exclu : il parle d'un
    FICHIER, pas d'un executable."""
    assert _command_not_found_in(stderr) is False


def test_aucune_signature_ne_parle_de_fichier_manquant():
    """Garde-fou de perimetre : elargir a « no such file » rendrait le
    marqueur faux-positif sur find, rsync et les liens casses."""
    assert not any("no such file" in s for s in _COMMAND_NOT_FOUND_SIGNATURES)


def test_les_signatures_sont_toutes_en_minuscules():
    """La comparaison se fait sur stderr.lower() : une signature en casse
    mixte ne matcherait jamais — defaut silencieux."""
    for signature in _COMMAND_NOT_FOUND_SIGNATURES:
        assert signature == signature.lower(), signature


# ══════════════════════════════════════════════════════════════════════════
#  Le marqueur dit ce que exit:0 ne dit pas
# ══════════════════════════════════════════════════════════════════════════


def test_le_module_expose_le_detecteur_au_handler():
    """Le handler `run_command` doit consommer CE detecteur — pas
    reimplementer sa propre liste dans son coin."""
    import inspect

    from src.reasoning.handlers import system

    source = inspect.getsource(system.run_command_handler)
    assert "_command_not_found_in(stderr)" in source
    assert "exit_code == 0" in source


def test_le_message_explique_pourquoi_exit_zero_ne_prouve_rien():
    import inspect

    from src.reasoning.handlers import system

    source = inspect.getsource(system.run_command_handler)
    assert "COMMANDE INTROUVABLE" in source
    assert "ForEach-Object" in source
