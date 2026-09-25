"""Lot L1d-3 - en conversation, Lumena lit partout sur le PC, sauf les zones secretes.

Decision de Charles du 15 septembre 2026 : « le PC devient ses mains, son corps » ->
lecture libre PARTOUT en conversation, SAUF les secrets (`.env` et `data/mail` de
Lumena, `~/.ssh`, profils de navigateurs, gestionnaires d'identifiants, fichiers de
cles). L'ECRITURE, elle, reste bornee a l'endroit designe + projet en cours (L1c-3).

Mesure du 15/09 (audit) : `read_file`, `list_directory`, `find_files`,
`read_files_batch` ne dependent que de la resolution de chemin, qui refuse hors depot
sans autorisation ; `grep_search` est plus strict encore : il resout tout depuis la
racine Lumena et applique `check_path_boundary`, donc il ne peut PAS chercher dans un
projet situe hors du depot, meme designe.

Le `.env` d'un projet de l'utilisateur reste lisible : sinon Lumena ne pourrait plus
l'aider dessus. Seul celui de Lumena est protege (liste noire existante).

Missions et autonomie restent confinees. Dossiers JETABLES.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.reasoning.handlers import files as files_mod
from src.reasoning.handlers.batch import read_files_batch_handler
from src.reasoning.handlers.context import HandlerContext
from src.tools.file_guardrails import (
    OutsideAccessGrant,
    PathSecurityError,
    WorkspaceFileGuardrails,
    check_secret_zone,
)

CONTENU = "SECRET_INTERIEUR = 42\n"


@pytest.fixture
def monde(tmp_path, monkeypatch):
    root = tmp_path / "lumena"
    ws = root / "workspace"
    ws.mkdir(parents=True)
    (root / ".env").write_text("CLE=1\n", encoding="utf-8")
    (root / "data" / "mail").mkdir(parents=True)
    (root / "data" / "mail" / "boite.json").write_text("{}", encoding="utf-8")
    maison = tmp_path / "maison"
    projet = maison / "Mes Documents" / "site vitrine"
    projet.mkdir(parents=True)
    (projet / "app.py").write_text(CONTENU, encoding="utf-8")
    (projet / ".env").write_text("API_DU_PROJET=abc\n", encoding="utf-8")
    ssh = maison / ".ssh"
    ssh.mkdir()
    (ssh / "id_rsa").write_text("-----BEGIN PRIVATE KEY-----\n", encoding="utf-8")
    navigateur = maison / "AppData" / "Local" / "Google" / "Chrome" / "User Data" / "Default"
    navigateur.mkdir(parents=True)
    (navigateur / "Login Data").write_text("mots de passe", encoding="utf-8")
    coffre = maison / "coffre.kdbx"
    coffre.write_text("keepass", encoding="utf-8")
    cle = maison / "serveur.pem"
    cle.write_text("-----BEGIN RSA PRIVATE KEY-----\n", encoding="utf-8")
    monkeypatch.setattr(WorkspaceFileGuardrails, "_workspace_root", lambda self: ws)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: maison))
    return {"root": root, "ws": ws, "maison": maison, "projet": projet, "ssh": ssh,
            "navigateur": navigateur, "coffre": coffre, "cle": cle, "tmp": tmp_path}


def _chat(m) -> HandlerContext:
    ctx = HandlerContext.for_testing(lumena_root=m["root"], runtime_root=m["ws"])
    ctx.outside_access_grant = OutsideAccessGrant.none()  # chat ordinaire : rien de nomme
    return ctx


def _mission(m) -> HandlerContext:
    ctx = _chat(m)
    ctx.is_mission_run = True
    ctx.runtime_task_id = "task-l1d3"
    return ctx


def _autonomie(m) -> HandlerContext:
    ctx = HandlerContext.for_testing(lumena_root=m["root"], runtime_root=m["ws"])
    ctx.outside_access_grant = None  # verrou total du mode autonome
    return ctx


# ── 1. Zones secretes : la regle, partout sur le PC ──────────────────────────

def test_cle_ssh_est_une_zone_secrete(monde):
    with pytest.raises(PathSecurityError):
        check_secret_zone(monde["ssh"] / "id_rsa")


def test_profil_de_navigateur_est_une_zone_secrete(monde):
    with pytest.raises(PathSecurityError):
        check_secret_zone(monde["navigateur"] / "Login Data")


@pytest.mark.parametrize("nom", ["coffre", "cle"])
def test_coffre_et_cle_privee_sont_secrets(monde, nom):
    with pytest.raises(PathSecurityError):
        check_secret_zone(monde[nom])


def test_fichier_de_projet_n_est_pas_secret(monde):
    check_secret_zone(monde["projet"] / "app.py")


def test_env_d_un_projet_utilisateur_reste_lisible(monde):
    """Seul le `.env` de Lumena est protege (liste noire) : sinon Lumena ne pourrait
    plus aider sur les projets de l'utilisateur."""
    check_secret_zone(monde["projet"] / ".env")


# ── 2. Chat : lecture partout, sans avoir a nommer l'endroit ─────────────────

@pytest.mark.asyncio
async def test_chat_lit_un_fichier_hors_depot_sans_le_nommer(monde):
    m = monde
    r = await files_mod.read_file_handler(_chat(m), path=str(m["projet"] / "app.py"))
    assert r.success and "SECRET_INTERIEUR" in str(r.output)


@pytest.mark.asyncio
async def test_chat_liste_un_dossier_hors_depot(monde):
    m = monde
    r = await files_mod.list_directory_handler(_chat(m), path=str(m["projet"]))
    assert r.success and "app.py" in str(r.output)


@pytest.mark.asyncio
async def test_chat_cherche_des_fichiers_hors_depot(monde):
    m = monde
    r = await files_mod.find_files_handler(_chat(m), pattern="*.py", path=str(m["projet"]))
    assert r.success and "app.py" in str(r.output)


@pytest.mark.asyncio
async def test_chat_lit_plusieurs_fichiers_hors_depot(monde):
    m = monde
    r = await read_files_batch_handler(_chat(m), paths=[str(m["projet"] / "app.py")])
    assert r.success and "SECRET_INTERIEUR" in str(r.output)


@pytest.mark.asyncio
async def test_chat_grep_cherche_dans_un_projet_hors_depot(monde):
    """Avant L1d-3 : `grep_search` resolvait depuis la racine Lumena et refusait."""
    m = monde
    r = await files_mod.grep_search_handler(_chat(m), pattern="SECRET_INTERIEUR",
                                            path=str(m["projet"]))
    assert r.success and "app.py" in str(r.output)


# ── 3. Ce qui reste refuse, meme en conversation ─────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("cle", ["ssh", "navigateur", "coffre", "cle"])
async def test_chat_ne_lit_pas_une_zone_secrete(monde, cle):
    m = monde
    cible = {"ssh": m["ssh"] / "id_rsa", "navigateur": m["navigateur"] / "Login Data",
             "coffre": m["coffre"], "cle": m["cle"]}[cle]
    r = await files_mod.read_file_handler(_chat(m), path=str(cible))
    assert not r.success and "refus" in str(r.output).lower()


@pytest.mark.asyncio
async def test_chat_ne_lit_pas_le_env_de_lumena(monde):
    """Caracterisation : la liste noire de lecture existante ne bouge pas."""
    m = monde
    r = await files_mod.read_file_handler(_chat(m), path=str(m["root"] / ".env"))
    assert not r.success and "refus" in str(r.output).lower()


@pytest.mark.asyncio
async def test_chat_ne_lit_pas_la_boite_mail_de_lumena(monde):
    m = monde
    r = await files_mod.read_file_handler(_chat(m), path=str(m["root"] / "data/mail/boite.json"))
    assert not r.success and "refus" in str(r.output).lower()


@pytest.mark.asyncio
async def test_chat_grep_ne_fouille_pas_une_zone_secrete(monde):
    m = monde
    r = await files_mod.grep_search_handler(_chat(m), pattern="PRIVATE", path=str(m["ssh"]))
    assert not r.success and "refus" in str(r.output).lower()


# ── 4. Missions et autonomie restent confinees ───────────────────────────────

@pytest.mark.asyncio
async def test_mission_ne_lit_pas_hors_de_son_perimetre(monde):
    m = monde
    r = await files_mod.read_file_handler(_mission(m), path=str(m["projet"] / "app.py"))
    assert not r.success and "SECRET_INTERIEUR" not in str(r.output)


@pytest.mark.asyncio
async def test_autonomie_ne_lit_pas_hors_depot(monde):
    m = monde
    r = await files_mod.read_file_handler(_autonomie(m), path=str(m["projet"] / "app.py"))
    assert not r.success and "SECRET_INTERIEUR" not in str(r.output)


# ── 5. L'ecriture ne s'ouvre pas avec la lecture ─────────────────────────────

@pytest.mark.asyncio
async def test_lire_partout_n_autorise_pas_a_ecrire_partout(monde):
    """L1c-3 inchange : ecrire hors depot demande l'endroit designe ou le projet en cours."""
    m = monde
    cible = m["projet"] / "app.py"
    r = await files_mod.edit_file_handler(_chat(m), file_path=str(cible),
                                          old_content="SECRET_INTERIEUR", new_content="ECRASE")
    assert not r.success
    assert cible.read_text(encoding="utf-8") == CONTENU
