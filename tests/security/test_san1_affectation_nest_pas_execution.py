r"""Lot SAN-1 - une AFFECTATION de variable n'est pas une execution.

--- Ce que le run reel du 24 septembre 2026 a mesure ---

Charles demande une seconde instance de l'IDE. Lumena remonte toute la chaine seule :
elle lit `main.ts`, trouve `requestSingleInstanceLock()` ligne 707, comprend que le
verrou est lie au repertoire `userData`, identifie les deux variables d'environnement
prevues pour cela, localise l'exe package. Un raisonnement juste de bout en bout.

Puis trois strategies, trois refus, toujours le meme :

    $env:LUMENA_IDE_WORKSPACE='C:\...\lumena\workspace'
        -> « Executable 'workspace' non autorise par la whitelist de securite »
    $r='C:\Users\charl\Desktop\lumena'
        -> « Executable 'lumena' non autorise »

Le mecanisme : `shlex.split` fusionne l'affectation en UN token, puis le **basename**
en est extrait. Le dernier segment du CHEMIN devenait « l'executable ». Trente
iterations perdues sur un garde qui lisait un nom de dossier comme un programme.

--- Et le revers, trouve en ecrivant ce lot ---

`_PS_EXPR_RE` autorisait TOUTE commande commencant par `$x`, quelle que soit la suite.
Mesure sur la version d'origine : `$x = .\evil.exe` et `$x = evil.exe` **passaient**,
alors que les memes commandes NUES etaient bloquees.

Le garde refusait donc les chemins inoffensifs et laissait entrer les appels :
exactement a l'envers. Ces deux trous sont anterieurs a ce lot ; ils sont fermes ici
parce qu'on les a vus.

--- La regle, en une phrase ---

**Une affectation n'est ni plus laxiste ni plus stricte que la commande qu'elle
porte.** Seule une valeur INERTE — chaine entierement quotee, ou nombre — echappe au
jugement. Tout le reste est juge comme une commande.

La premiere version de cette regle disait l'inverse (« bloquer si la valeur contient
un operateur d'appel ») et la mesure l'a refutee : `evil.exe` ne contient aucun
operateur.
"""
from __future__ import annotations

import pytest

from src.utils.command_sanitizer import sanitize_command


# ── 1. Les cas exacts du run, qui doivent passer ────────────────────────────

@pytest.mark.parametrize("commande", [
    r"$env:LUMENA_IDE_WORKSPACE='C:\Users\charl\Desktop\lumena\workspace'",
    r"$env:LUMENA_IDE_USER_DATA='C:\Users\charl\AppData\Local\ide2ud'",
    r"$r='C:\Users\charl\Desktop\lumena'",
    r'$dossier="C:\chemin\avec\workspace\dedans"',
    r"$x=42",
    r"$seuil=-3.5",
])
def test_une_affectation_de_litteral_passe(commande):
    """Le defaut mesure : le dernier segment du chemin etait pris pour un programme."""
    autorise, message = sanitize_command(commande)
    assert autorise, f"affectation litterale refusee : {message}"


def test_le_nom_de_dossier_nest_plus_lu_comme_un_programme():
    """Le symptome exact, nomme : « Executable 'workspace' non autorise »."""
    _, message = sanitize_command(r"$env:X='C:\a\b\workspace'")
    assert "workspace" not in (message or ""), message


# ── 2. La regle : ni plus laxiste, ni plus stricte que la commande nue ──────

@pytest.mark.parametrize("nue", [
    r"Start-Process notepad",   # autorise nu -> doit rester autorise affecte
    r"notepad",
    r"python -c 'x'",
    r".\evil.exe",              # bloque nu -> DOIT rester bloque affecte
    r"evil.exe",
    r"& 'C:\evil.exe'",
    r"$(whoami)",
    r"rm -rf /",
])
def test_affectation_et_commande_nue_sont_jugees_pareil(nue):
    """Le coeur du lot. C'est cette symetrie qui ferme les deux trous d'origine.

    Sans elle, le garde peut etre a la fois trop strict (le run de Charles) et trop
    laxiste (`$x = evil.exe`) — ce qui etait exactement le cas avant ce lot.
    """
    nu_autorise, _ = sanitize_command(nue)
    affecte_autorise, _ = sanitize_command(f"$x = {nue}")
    assert nu_autorise == affecte_autorise, (
        f"{nue!r} : nue={'passe' if nu_autorise else 'bloque'} mais "
        f"affectee={'passe' if affecte_autorise else 'bloque'}"
    )


# ── 3. Les deux trous anterieurs, nommement fermes ─────────────────────────

@pytest.mark.parametrize("commande", [
    r"$x = evil.exe",
    r"$x = .\evil.exe",
    r"$outil = C:\Windows\System32\evil.exe",
])
def test_une_execution_deguisee_en_affectation_reste_bloquee(commande):
    """Ces trois-la PASSAIENT avant ce lot. Mesure faite sur la version d'origine."""
    autorise, _ = sanitize_command(commande)
    assert not autorise, f"execution deguisee autorisee : {commande}"


def test_l_operateur_d_appel_ne_sauve_pas_un_binaire_interdit():
    """`&` introduit le programme, il ne le remplace pas."""
    autorise, _ = sanitize_command(r"$x = & 'C:\evil.exe'")
    assert not autorise


def test_une_substitution_reste_bloquee():
    autorise, _ = sanitize_command(r"$x = $(whoami)")
    assert not autorise


# ── 4. Rien d'autre n'a bouge ───────────────────────────────────────────────

@pytest.mark.parametrize("commande,attendu", [
    (r"python -m pytest -q", True),
    (r"git status", True),
    (r"npm run build", True),
    (r"rm -rf /", False),
    (r"shutdown /s", False),
])
def test_les_commandes_ordinaires_sont_inchangees(commande, attendu):
    """Un lot qui repare un garde ne doit pas en desarmer un autre."""
    autorise, _ = sanitize_command(commande)
    assert autorise is attendu, commande
