r"""Lot PS-1 - le garde PowerShell ne s'enveloppe plus lui-meme.

--- Ce que le run du 24/09 a 20 h 07 a mesure ---

Lumena lance :

    powershell -NoProfile -Command "Start-Sleep -Seconds 12; Write-Output 'boot-wait'"

Observation :

    ⚠️ ECHEC de la commande (exit code 1)
    [STDERR] -NoProfile : Le terme «-NoProfile» n'est pas reconnu comme nom d'applet de
    commande, fonction, fichier de script ou programme executable.

Son raisonnement au tour suivant : « Ma commande d'attente a echoue sur un detail
PowerShell (`-NoProfile` non reconnu par le wrapper) ». Une iteration perdue, et elle a
change de strategie en croyant s'etre trompee.

--- La cause, au code (`handlers/system.py` l.792-804) ---

Le garde enveloppe les cmdlets `Verb-Noun` dans `powershell -Command`. Quand la commande
commence DEJA par `powershell`, il verifie si elle porte `-Command` :

    if not _re.match(r'(?i)\\s*-(?:Command|c|File)', _rest):

`_re.match` est ANCRE AU DEBUT. Or `_rest` vaut ici `-NoProfile -Command "..."` : le test
echoue, et le garde enveloppe une seconde fois :

    powershell -NoProfile -NonInteractive -Command "-NoProfile -Command \\"Start-Sleep...\\""

PowerShell recoit alors `-NoProfile` comme une COMMANDE. D'ou le message.

**Mesure : 5 formes sur 7 cassees** — toute commande portant un flag AVANT `-Command` :

    powershell -NoProfile -Command "..."             CASSE
    powershell -Command "..."                          ok
    powershell -NoProfile -NonInteractive -Command ..  CASSE
    powershell -ExecutionPolicy Bypass -File x.ps1     CASSE
    powershell -WindowStyle Hidden -Command "..."      CASSE
    powershell Get-Process; Write-Output 'z'           ok (enveloppe a raison)
    powershell.exe -NoLogo -Command "..."              CASSE

Ironie mesuree : le code lui-meme genere `powershell -NoProfile -Command "..."` pour
traduire `tail`/`head` (l.664, l.668).

--- Le choix de conception ---

La logique est extraite en fonction pure `reecrire_powershell_si_besoin` : enfouie dans le
handler, elle n'etait testable qu'en EXECUTANT de vraies commandes. Meme geste qu'au lot
GATE-2 avec `verifier_js_par_node`.

Le test cherche `-Command`/`-File`/`-c` **n'importe ou dans les flags**, plus seulement
collee au debut.
"""
from __future__ import annotations

import pytest

from src.reasoning.handlers.system import reecrire_powershell_si_besoin as reecrire


def _double_enveloppe(resultat: str) -> bool:
    """Le symptome exact : le corps du `-Command` commence par un flag."""
    marque = "-Command "
    if resultat.count(marque) < 1:
        return False
    corps = resultat.split(marque, 1)[1].lstrip().lstrip('"').lstrip()
    return corps.startswith("-")


# -- 1. Les cinq formes cassees ---------------------------------------------

CASSEES = [
    'powershell -NoProfile -Command "Start-Sleep -Seconds 12; Write-Output \'ok\'"',
    'powershell -NoProfile -NonInteractive -Command "Get-Process; Write-Output \'x\'"',
    "powershell -ExecutionPolicy Bypass -File script.ps1; Write-Output 'y'",
    'powershell -WindowStyle Hidden -Command "Get-Date; Write-Output \'w\'"',
    'powershell.exe -NoLogo -Command "Get-Date; Write-Output \'v\'"',
]


@pytest.mark.parametrize("commande", CASSEES)
def test_un_flag_avant_Command_ne_declenche_plus_la_double_enveloppe(commande):
    resultat = reecrire(commande)
    assert not _double_enveloppe(resultat), resultat


def test_LA_commande_du_run_est_rendue_telle_quelle():
    """Verbatim de 20 h 07 : elle etait deja correcte, il ne faut RIEN lui faire."""
    commande = ('powershell -NoProfile -Command "Start-Sleep -Seconds 12; '
                "Write-Output 'boot-wait'\"")
    assert reecrire(commande) == commande


@pytest.mark.parametrize("flag", ["-NoProfile", "-NonInteractive", "-NoLogo",
                                  "-WindowStyle Hidden", "-ExecutionPolicy Bypass"])
def test_les_flags_courants_sont_tous_reconnus(flag):
    commande = f'powershell {flag} -Command "Get-Date; Write-Output \'a\'"'
    assert reecrire(commande) == commande, reecrire(commande)


# -- 2. Ce que le garde doit CONTINUER de faire -----------------------------

def test_une_cmdlet_nue_est_toujours_enveloppee():
    """La raison d'etre du garde : sans `-Command`, cmd.exe interpreterait le pipe."""
    resultat = reecrire("powershell Get-Process; Write-Output 'z'")
    assert "-Command" in resultat
    assert "Get-Process" in resultat


def test_une_commande_sans_powershell_est_enveloppee():
    """Cas historique : `Select-String ...` passe tel quel echouerait dans cmd.exe."""
    resultat = reecrire("Get-Content fichier.txt; Write-Output 'fin'")
    assert resultat.lower().startswith("powershell")
    assert "-Command" in resultat


def test_Command_deja_collee_reste_intacte():
    """Le seul cas que l'ancien test reconnaissait — il doit continuer de marcher."""
    commande = 'powershell -Command "Start-Sleep -Seconds 1; Write-Output \'ok\'"'
    assert reecrire(commande) == commande


@pytest.mark.parametrize("commande", [
    "cmd /c dir",
    "python -c \"print(1)\"",
    "py -3 script.py",
])
def test_les_prefixes_exclus_le_restent(commande):
    """`cmd`, `python`, `py ` etaient deliberement hors du garde."""
    assert reecrire(commande) == commande


def test_une_commande_sans_cmdlet_n_est_pas_touchee():
    """Le declencheur est un `Verb-Noun` : `dir` ou `echo` ne doivent rien subir."""
    assert reecrire("echo bonjour") == "echo bonjour"


# -- 3. Le branchement : le handler doit EMPRUNTER cette fonction ----------

def test_le_handler_appelle_bien_la_fonction_extraite():
    """Une fonction pure que le handler n'utilise pas ne corrige rien — c'est le motif
    que ce chantier rencontre en boucle."""
    import inspect

    from src.reasoning.handlers import system

    source = inspect.getsource(system.run_command_handler)
    assert "reecrire_powershell_si_besoin" in source, (
        "le handler n'emprunte pas la fonction : PS-1 serait du code mort"
    )
