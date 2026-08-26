"""LOT Z38 — le CodeAgent corrigeait du PHP sans interpreteur PHP.

Mesure sur le run « SaaS complet » (2026-08-25, logslumena A.txt) :

  * ~20 fichiers PHP ecrits (config, database, auth, ai, project, task,
    api/index, login, register, dashboard, projects, kanban, ...) ;
  * ZERO valide : `php` n'etait ni dans l'image sandbox ni dans l'allowlist ;

        WARNING Executable non autorise: 'php' (commande: php -l login.php)

  * consequence directe : le CodeAgent a repare des redeclarations
    `e()` et `csrf_verify()` **de tete**, six fois de suite (iters 8-10 de la
    tentative 1, puis 9-11 de la tentative 2), sans jamais pouvoir savoir
    s'il reparait ou s'il cassait.

Il a ensuite contourne le blocage via PowerShell (`powershell` EST autorise) :

    Get-ChildItem *.php | ForEach-Object { php -l $_.FullName }
    -> 26x « php n'est pas reconnu »  puis  [cmd_done] exit:0

Ce lot rend `php -l` disponible et autorise. Il ne rend pas le CodeAgent plus
intelligent : il lui rend la seule chose qui transforme une intuition en fait.

Le pendant « exit:0 malgre 26 erreurs » est un defaut DISTINCT (le code de
sortie de PowerShell ne remonte pas celui des commandes de la boucle) et il a
son propre lot.
"""

from pathlib import Path

import pytest

from src.utils.command_sanitizer import DEFAULT_ALLOWED_EXECUTABLES, sanitize_command

_DOCKERFILE = Path(__file__).resolve().parents[2] / "Dockerfile.sandbox"


# ══════════════════════════════════════════════════════════════════════════
#  L'allowlist : php n'etait pas dedans
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("commande", [
    "php -l login.php",
    "php -l config/config.php",
    "php --version",
])
def test_php_lint_est_autorise(commande):
    """LE lot. `php -l` est la seule validation possible d'un fichier PHP."""
    autorise, motif = sanitize_command(commande)
    assert autorise is True, f"{commande!r} refuse : {motif}"


def test_php_est_declare_dans_l_allowlist():
    assert "php" in DEFAULT_ALLOWED_EXECUTABLES


@pytest.mark.parametrize("commande", [
    "binairequinexistepas --tout-casser",
    "perl -e 'unlink glob \"*\"'",
])
def test_le_sanitizer_bloque_toujours_un_executable_inconnu(commande):
    """Garde-fou : ce lot ELARGIT l'allowlist, il ne la desarme pas.

    Attention au nom choisi : un nom en `Verbe-Nom` (« truc-machin ») est lu
    comme une cmdlet PowerShell et passe par une autre branche. Le premier
    essai de ce test a echoue pour cette raison — le test avait tort, pas le
    sanitizer."""
    autorise, _ = sanitize_command(commande)
    assert autorise is False


# ══════════════════════════════════════════════════════════════════════════
#  L'image sandbox : php n'y etait pas non plus
# ══════════════════════════════════════════════════════════════════════════


def test_l_image_sandbox_installe_php():
    """Autoriser `php` sans l'installer aurait juste deplace l'echec du
    sanitizer vers « command not found »."""
    contenu = _DOCKERFILE.read_text(encoding="utf-8")
    assert "php-cli" in contenu


def test_l_image_sandbox_verifie_php_au_build():
    """Misconfiguration fails loud : si php disparait de l'image, le build
    casse au lieu de produire une image silencieusement inutile."""
    contenu = _DOCKERFILE.read_text(encoding="utf-8")
    assert "php --version" in contenu


def test_les_autres_runtimes_restent_verifies_au_build():
    contenu = _DOCKERFILE.read_text(encoding="utf-8")
    for verification in ("python3 --version", "node --version", "npm --version"):
        assert verification in contenu


# ══════════════════════════════════════════════════════════════════════════
#  Le contournement PowerShell reste possible — et c'est voulu
# ══════════════════════════════════════════════════════════════════════════


def test_powershell_reste_autorise():
    """`powershell` est volontairement dans l'allowlist. Le defaut du run
    n'etait pas qu'il passe, mais qu'il rende exit:0 sur 26 erreurs — ce que
    ce lot ne pretend pas corriger."""
    autorise, _ = sanitize_command("powershell -NoProfile -Command \"Get-ChildItem\"")
    assert autorise is True
