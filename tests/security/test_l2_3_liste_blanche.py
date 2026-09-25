"""Lot L2-3 - la liste blanche juge la CIBLE, plus seulement le nom (constat N2).

Mesure du 16 septembre 2026, par appel REEL au juge (pas une lecture de code) :

| Commande                                  | Avant ce lot | Jugement |
|-------------------------------------------|--------------|----------|
| `New-Item -ItemType Directory -Path js`   | REFUS        | faux refus (creation dans le workspace) |
| `Copy-Item -Recurse -Force src dst`       | REFUS        | faux refus |
| `Remove-Item fichier.txt`                 | REFUS        | faux refus (supprimer SON fichier) |
| `echo "config ssh ok"`                    | REFUS        | **faux positif sur du TEXTE** |
| `ssh user@host ls`                        | REFUS        | correct, a conserver |
| `vite build` / `npx vite build`           | REFUS / OK   | incoherence N2 |

Sur 1504 commandes reelles, **100 refusees (6,6 %)**, dont `Remove-Item` 18x,
`New-Item` 5x, `Copy-Item` 3x - presque toutes dans le workspace.

Ces refus existaient parce que le DOSSIER n'etait pas borne. Depuis L1c, L2-1 et L2-2
il l'est : une commande qui travaille dans un dossier autorise est deja contenue. On
assouplit donc **la ou le dossier est autorise**, et on garde tout ce qui protege
d'autre chose que l'ecriture (exfiltration, processus detaches, substitutions,
credentials).
"""
from __future__ import annotations

import pytest

from src.utils.command_sanitizer import sanitize_chained_command

ECRITURES_PS = [
    "New-Item -ItemType Directory -Path js",
    "Copy-Item -Recurse -Force src dst",
    "Remove-Item fichier.txt",
    "Move-Item a.txt b.txt",
    "Rename-Item vieux.txt neuf.txt",
]


def test_set_content_reste_refuse_hors_perimetre_de_ce_lot():
    """`Set-Content`/`Out-File`/`Add-Content` sont attrapes AVANT le verdict de verbe,
    par un garde dedie (`command_sanitizer.py:348`). Mesure du 16/09 : il ne protege pas
    « le dossier » mais le PERIMETRE DE FICHIERS d'un worker (`allowed_files`), que le
    dossier borne ne remplace pas. Hors perimetre de L2-3 : refus conserve, dossier
    autorise ou non."""
    for autorise in (False, True):
        permis, raison = sanitize_chained_command(
            "Set-Content -Path app.js -Value 'x'", workdir_allowed=autorise)
        assert permis is False and "shell" in raison.lower()


# ── 1. Caracterisation : sans dossier autorise, rien ne change ───────────────

@pytest.mark.parametrize("commande", ECRITURES_PS)
def test_ecriture_powershell_refusee_par_defaut(commande):
    """Le comportement historique reste le defaut : aucun droit nouveau sans dossier."""
    permis, raison = sanitize_chained_command(commande)
    assert permis is False and "verbe" in raison.lower()


# ── 2. Assouplissement : dossier autorise -> l'ecriture PowerShell passe ─────

@pytest.mark.parametrize("commande", ECRITURES_PS)
def test_ecriture_powershell_admise_quand_le_dossier_est_autorise(commande):
    permis, raison = sanitize_chained_command(commande, workdir_allowed=True)
    assert permis is True, raison


def test_suppression_recursive_reste_refusee_meme_dans_un_dossier_autorise():
    """`Remove-Item -Recurse` efface un arbre entier : le dossier borne n'y suffit pas."""
    permis, raison = sanitize_chained_command(
        "Remove-Item -Recurse -Force .", workdir_allowed=True)
    assert permis is False and "dangereux" in raison.lower()


# ── 3. Le mot dans une CHAINE n'est pas une commande ─────────────────────────

@pytest.mark.parametrize("commande", [
    'echo "config ssh ok"',
    "echo 'transfert ftp termine'",
    'node -e "console.log(\'scp done\')"',
])
def test_mot_sensible_dans_une_chaine_litterale_est_admis(commande):
    permis, raison = sanitize_chained_command(commande)
    assert permis is True, raison


@pytest.mark.parametrize("commande", [
    "ssh user@host ls",
    "scp fichier user@host:/tmp",
    "sftp user@host",
    "rsync -a . user@host:/var/www",
])
def test_vrai_transfert_distant_reste_refuse(commande):
    """Exfiltration et deploiement : refus conserve, dossier autorise ou non."""
    for autorise in (False, True):
        permis, raison = sanitize_chained_command(commande, workdir_allowed=autorise)
        assert permis is False, (commande, autorise, raison)


# ── 4. N2 : `vite` et `npx vite` jugés pareil ───────────────────────────────

def test_npx_vite_reste_admis():
    """Caracterisation : ce cas passait deja."""
    assert sanitize_chained_command("npx vite build")[0] is True


@pytest.mark.parametrize("commande", ["vite build", "tsx script.ts", "vitest run"])
def test_outil_local_admis_comme_via_npx(commande):
    """N2 : refuser `vite` mais admettre `npx vite` n'a aucun sens - meme binaire."""
    permis, raison = sanitize_chained_command(commande)
    assert permis is True, raison


# ── 5. Ce qui doit rester refusé, dossier autorisé ou non ───────────────────

@pytest.mark.parametrize("commande", [
    "Start-Job -ScriptBlock { python app.py }",
    "Start-Process python -ArgumentList '-m','http.server'",
    "Invoke-Expression $payload",
    "rm -rf /",
    "format C:",
    'sftp://jean:motdepasse@serveur',
])
def test_refus_de_securite_conserves(commande):
    for autorise in (False, True):
        permis, _ = sanitize_chained_command(commande, workdir_allowed=autorise)
        assert permis is False, (commande, autorise)


# ── 6. Le parametre est optionnel et ne change rien s'il est absent ─────────

def test_parametre_optionnel_ne_casse_aucun_appelant():
    """Regle apprise n°4 : nouveau parametre NOMME, transmis seulement si present."""
    assert sanitize_chained_command("python -m pytest")[0] is True
    assert sanitize_chained_command("python -m pytest", None)[0] is True
