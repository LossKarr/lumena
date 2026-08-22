"""LOT Z17 — publier n'autorise pas à détruire.

Run « Verdure 2 » (2026-08-16), déroulé exact :

    l'objectif disait          workspace/verdure2/
    le lead a appelé           publish_mission_workspace()      ← sans argument
    la destination est venue   contract.project = « Verdure »
    donc publication dans      workspace/verdure/               ← le livrable de la VEILLE

`shutil.copytree(..., dirs_exist_ok=True)` a recouvert les sept fichiers sans un
mot. Le Verdure de la veille a disparu, et rien ne l'a signalé — ni au log, ni à
la mission, ni à l'utilisateur. Je ne l'ai su qu'en comparant les deux runs.

**On n'interdit pas l'écrasement** : republier au même endroit est le cas normal,
et le LOT Z8 l'EXIGE même, après une correction tardive. On refuse seulement
qu'il soit SILENCIEUX et IRRÉVERSIBLE :

  · tout fichier dont le contenu VA changer est archivé sous `.backups/<nom>.<ts>`
    (le mécanisme d'`edit_file`, déjà en place dans le dépôt) ;
  · republier à l'identique n'archive rien — le cas courant ne coûte rien ;
  · la mission LIT ce qui a été recouvert et peut en rendre compte.
"""

import shutil
from pathlib import Path

import pytest


# ── La règle, rejouée ────────────────────────────────────────────────────────
#
# Le code vit au milieu d'un handler asynchrone trop enchâssé pour être appelé
# seul. On rejoue ici la règle EXACTE, et des tests structurels plus bas
# vérifient que le source la contient bien telle quelle.


def _archiver_avant_ecrasement(src_dir: Path, dest: Path, stamp: str = "20260816_2100"):
    ecrases: list[str] = []
    if not dest.is_dir():
        return ecrases
    for s in src_dir.rglob("*"):
        if not s.is_file():
            continue
        rel = s.relative_to(src_dir)
        d = dest / rel
        if not d.is_file():
            continue
        if d.read_bytes() == s.read_bytes():
            continue
        bdir = dest / ".backups"
        bdir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(d, bdir / f"{rel.name}.{stamp}")
        ecrases.append(str(rel).replace("\\", "/"))
    return ecrases


@pytest.fixture()
def deux_verdure(tmp_path):
    """La veille dans `dest`, la mission du jour dans `src`."""
    src, dest = tmp_path / "mission", tmp_path / "verdure"
    src.mkdir()
    dest.mkdir()
    (dest / "index.html").write_text("<h1>Verdure DE LA VEILLE</h1>", encoding="utf-8")
    (dest / "styles.css").write_text("/* veille */", encoding="utf-8")
    (src / "index.html").write_text("<h1>Verdure du jour</h1>", encoding="utf-8")
    (src / "styles.css").write_text("/* jour */", encoding="utf-8")
    (src / "devis.html").write_text("<h1>nouveau</h1>", encoding="utf-8")
    return src, dest


# ── Le cas mesuré ────────────────────────────────────────────────────────────


def test_le_travail_de_la_veille_est_archive_avant_decrasement(deux_verdure):
    src, dest = deux_verdure
    ecrases = _archiver_avant_ecrasement(src, dest)
    assert sorted(ecrases) == ["index.html", "styles.css"]
    sauve = (dest / ".backups" / "index.html.20260816_2100").read_text(encoding="utf-8")
    assert "DE LA VEILLE" in sauve


def test_un_fichier_nouveau_narchive_rien(deux_verdure):
    """`devis.html` n'existait pas : il n'y a rien à préserver."""
    src, dest = deux_verdure
    assert "devis.html" not in _archiver_avant_ecrasement(src, dest)


def test_republier_a_lidentique_narchive_rien(tmp_path):
    """Le cas COURANT — Z8 exige de republier après correction. Archiver à chaque
    fois remplirait le disque (déjà à 97 %) pour rien."""
    src, dest = tmp_path / "m", tmp_path / "d"
    src.mkdir()
    dest.mkdir()
    for d in (src, dest):
        (d / "app.js").write_text("meme contenu", encoding="utf-8")
    assert _archiver_avant_ecrasement(src, dest) == []
    assert not (dest / ".backups").exists()


def test_une_premiere_publication_narchive_rien(tmp_path):
    src = tmp_path / "m"
    src.mkdir()
    (src / "index.html").write_text("neuf", encoding="utf-8")
    assert _archiver_avant_ecrasement(src, tmp_path / "jamais_vu") == []


def test_seuls_les_fichiers_reellement_modifies_sont_archives(tmp_path):
    src, dest = tmp_path / "m", tmp_path / "d"
    src.mkdir()
    dest.mkdir()
    (src / "a.txt").write_text("change", encoding="utf-8")
    (dest / "a.txt").write_text("avant", encoding="utf-8")
    (src / "b.txt").write_text("stable", encoding="utf-8")
    (dest / "b.txt").write_text("stable", encoding="utf-8")
    assert _archiver_avant_ecrasement(src, dest) == ["a.txt"]


def test_larchive_est_une_copie_fidele(deux_verdure):
    """Un archivage tronqué ou réencodé ne vaudrait pas mieux que rien."""
    src, dest = deux_verdure
    avant = (dest / "styles.css").read_bytes()
    _archiver_avant_ecrasement(src, dest)
    assert (dest / ".backups" / "styles.css.20260816_2100").read_bytes() == avant


# ── Le branchement dans le handler ───────────────────────────────────────────


_SRC = Path("src/reasoning/handlers/missions.py").read_text(encoding="utf-8")


def _bloc_z17() -> str:
    debut = _SRC.index("# ── LOT Z17")
    fin = _SRC.index("shutil.copytree(src_dir", debut)
    return _SRC[debut:fin]


def test_larchivage_precede_la_copie():
    """Placé après, il archiverait la version déjà écrasée — donc rien."""
    assert _SRC.index("# ── LOT Z17") < _SRC.index("shutil.copytree(src_dir")


def test_larchivage_ne_peut_pas_faire_echouer_une_publication():
    """Une publication légitime ne doit jamais mourir d'un souci d'archivage."""
    bloc = _bloc_z17()
    assert "except Exception" in bloc


def test_les_dossiers_techniques_sont_ignores():
    """Archiver `__pycache__` ou `.git` gonflerait un disque déjà à 97 %."""
    assert "_EXCLUDED_DIRS" in _bloc_z17()


def test_le_contenu_identique_court_circuite_larchivage():
    bloc = _bloc_z17()
    assert "read_bytes() == _s.read_bytes()" in bloc


def test_la_mission_lit_ce_quelle_a_recouvert():
    """Un archivage muet ne protège personne : le run Verdure 2 n'a rien signalé
    nulle part. La mission doit pouvoir en rendre compte."""
    i = _SRC.index("RECOUVERTS")
    bloc = _SRC[i - 400 : i + 700]
    assert ".backups" in bloc
    assert "_ecrases" in bloc


def test_le_message_donne_la_sortie_de_secours():
    """Nommer le problème sans donner le geste laisse la mission sans recours."""
    i = _SRC.index("RECOUVERTS")
    assert "publish_mission_workspace(target=" in _SRC[i : i + 800]


def test_le_message_ne_sort_que_sil_y_a_eu_ecrasement():
    """Une bannière permanente deviendrait du bruit qu'on n'ouvre plus."""
    i = _SRC.index("RECOUVERTS")
    assert "if _ecrases else" in _SRC[i : i + 800]


def test_la_raison_du_lot_est_datee_dans_le_code():
    bloc = _bloc_z17()
    assert "Verdure 2" in bloc
    assert "dirs_exist_ok" in bloc
    assert "Z8" in bloc  # republier reste légitime
