"""LOT Z18 — le contrat s'interdisait ce qu'il interdit aux workers.

Run « Pelage » (2026-08-16). Le lead a posé son contrat avec :

    path : "workspace/pelage/donnees.js"     au lieu de     "donnees.js"

Chemin relatif, sans `..`, sans lettre de lecteur : **accepté**. Les stubs sont
donc partis dans `missions/<id>/workspace/pelage/`. Et les workers — à qui le
prompt dit pourtant, mot pour mot, « ne recopie JAMAIS `missions/<id>/` ni
`workspace/` » — ont été forcés d'écrire `workspace/…`, que leur résolveur rend
vers le workspace **global**. Introuvable, à chaque fois.

Ce que ça a coûté, mesuré au log :

    w_rdv     17 itérations de recherche → PLAN GUARD force FINAL → ÉCHEC
    w_css     25 itérations
    w_data    14 itérations
    w_rdvjs   11 itérations
    w_index    8 itérations

Environ **75 itérations passées à chercher des fichiers**. Le CodeAgent a fini
par contourner à la main (`cmd /c mkdir & copy`, puis `shutil.copy`), créant
quatre fichiers hors de son périmètre. La mission a conclu en 33 minutes **sans
jamais publier**.

**Rare mais total.** Sur les 447 chemins des 100 contrats du disque, 5 seulement
sont fautifs — les 5 de Pelage. Les 174 sous-dossiers légitimes (`tests/`,
`static/`, `src/core/`) doivent rester valides : on ne regarde donc que le
PREMIER segment.

C'est le motif du chantier appliqué au contrat lui-même : la règle existe, elle
est écrite noir sur blanc dans le prompt des workers, et elle ne s'applique pas
là où elle aurait tout changé.
"""

import pytest

from src.subagents.mission_contract import (
    _mission_reserved_prefix,
    _mission_strip_reserved,
    validate_contract,
)


def _contrat(*paths):
    return {"project": "x", "files": [
        {"path": p, "owner": f"w{i}", "desc": "d"} for i, p in enumerate(paths)]}


def _refus(*paths):
    return [e for e in validate_contract(_contrat(*paths)) if "commencer par" in e]


# ── Le cas mesuré ────────────────────────────────────────────────────────────


def test_le_contrat_reel_de_pelage_est_refuse():
    """Les cinq chemins, tels que le lead les a écrits ce soir-là."""
    erreurs = _refus(
        "workspace/pelage/donnees.js", "workspace/pelage/index.html",
        "workspace/pelage/rdv.html", "workspace/pelage/styles.css",
        "workspace/pelage/rdv.js",
    )
    assert len(erreurs) == 5


@pytest.mark.parametrize(
    "chemin",
    ["workspace/app.py", "workspace/sous/dossier/x.js", "WORKSPACE/index.html",
     "workspace\\pelage\\rdv.js", "missions/task_abc/app.py", "Missions/x/y.py"],
)
def test_les_prefixes_reserves_sont_refuses(chemin):
    """Casse et antislashs compris — un modèle écrit les deux."""
    assert _refus(chemin)


# ── L'inertie, qui est le vrai risque de ce lot ──────────────────────────────


@pytest.mark.parametrize(
    "chemin",
    ["index.html", "tests/test_api.py", "src/core/db.py", "static/css/app.css",
     "static/workspace.css", "app/missions.py", "workspace.py", "missions.md"],
)
def test_les_chemins_legitimes_restent_acceptes(chemin):
    """174 chemins du disque sont des sous-dossiers valides. Tester autre chose
    que le PREMIER segment les casserait tous — `static/workspace.css` et
    `app/missions.py` sont parfaitement sains."""
    assert not _refus(chemin)


def test_un_fichier_nomme_workspace_reste_valide():
    """`workspace.py` n'est pas un préfixe : il n'y a pas de séparateur."""
    assert not _mission_reserved_prefix("workspace.py")
    assert not _mission_reserved_prefix("missions.md")


@pytest.mark.parametrize("chemin", ["", "   ", None])
def test_une_entree_vide_ne_declenche_rien(chemin):
    assert _mission_reserved_prefix(chemin) is False


# ── La correction proposée — un refus muet coûte une itération pour rien ─────


@pytest.mark.parametrize(
    "avant,apres",
    [
        ("workspace/pelage/donnees.js", "pelage/donnees.js"),
        ("workspace/index.html", "index.html"),
        ("missions/task_abc123/app.py", "app.py"),
        ("missions/task_x/tests/test_api.py", "tests/test_api.py"),
        ("workspace\\pelage\\rdv.js", "pelage/rdv.js"),
    ],
)
def test_le_chemin_corrige_est_donne_au_lead(avant, apres):
    assert _mission_strip_reserved(avant) == apres


def test_le_message_contient_le_chemin_corrige():
    """Le lead n'a qu'à recopier — c'est le patron des exports en noms nus (F.1)."""
    msg = _refus("workspace/pelage/donnees.js")[0]
    assert "'pelage/donnees.js'" in msg


def test_le_message_explique_la_cause():
    """Sans le pourquoi, le lead retire le préfixe ici et le remet ailleurs."""
    msg = _refus("workspace/app.py")[0]
    assert "DOSSIER DE MISSION" in msg
    assert "sous-dossier fantôme" in msg


def test_un_chemin_deja_propre_nest_pas_altere():
    assert _mission_strip_reserved("index.html") == "index.html"
    assert _mission_strip_reserved("tests/test_api.py") == "tests/test_api.py"


# ── Le branchement ───────────────────────────────────────────────────────────


def test_la_regle_passe_apres_les_validations_de_securite():
    """Un chemin absolu ou avec `..` doit rendre SON erreur, pas un reproche de
    préfixe — l'ordre des `elif` porte cette priorité."""
    erreurs = validate_contract(_contrat("../evasion.py"))
    assert any("invalide" in e for e in erreurs)
    assert not any("commencer par" in e for e in erreurs)


def test_les_contrats_du_disque_restent_valides():
    """Garde-fou de non-régression : 447 chemins réels, seuls les 5 de Pelage
    tombent. Si ce compte change, c'est que la règle a débordé."""
    import glob
    import json
    import os

    base = "workspace/missions"
    if not os.path.isdir(base):
        pytest.skip("workspace/missions absent")
    fautifs = 0
    for c in glob.glob(os.path.join(base, "*", "contract.json")):
        try:
            data = json.load(open(c, encoding="utf-8"))
        except Exception:
            continue
        for f in (data.get("files") or []):
            if isinstance(f, dict) and _mission_reserved_prefix(f.get("path")):
                fautifs += 1
    assert fautifs <= 5


def test_la_raison_du_lot_est_datee_dans_le_code():
    from pathlib import Path

    src = Path("src/subagents/mission_contract.py").read_text(encoding="utf-8")
    # Ancre EXACTE : « LOT Z18 » seul tombe sur le commentaire de
    # `_MISSION_RESERVED_HEADS`, pas sur le bloc de justification. Un test qui
    # vise la première occurrence d'un marqueur vérifie son propre découpage.
    i = src.index("LOT Z18 — le contrat s'interdisait")
    entete = src[i : i + 1600]
    assert "Pelage" in entete
    assert "75 itérations" in entete
    assert "PREMIER segment" in entete
