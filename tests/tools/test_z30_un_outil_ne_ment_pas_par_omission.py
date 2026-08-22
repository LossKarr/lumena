"""LOT Z30 — un outil ne répond pas « rien » quand il n'a pas cherché ce qu'on lui demandait.

Deux défauts du run « Papier Cousu » (2026-08-19), tous deux mesurés au log,
tous deux de la même famille : l'outil fait quelque chose de défendable et se
tait sur ce qu'il a réellement fait.

**C — 18:12:29.** Lumena cherche l'animation demandée :

    grep_search(pattern='canvas|requestAnimationFrame|getContext',
                path='…/index.html', ignore_case=True)
    → aucun résultat

`is_regex` vaut False par défaut, donc `re.escape()` a cherché la chaîne
littérale `canvas|requestAnimationFrame|getContext`. Or `index.html` contient
bien `<canvas id="bookCanvas">`, appelé par `getElementById('bookCanvas')`.
Elle a failli conclure que l'animation exigée par le client n'existait pas.

**D — 18:07:46.** Elle veut servir le livrable qu'elle vient de lister :

    list_directory('2026-08-19/papier-cousu')        → OK, 6 fichiers
    serve_website(directory='2026-08-19/papier-cousu') → « Dossier introuvable »

`directory` était la seule entrée à ne pas chercher dans le workspace. **Quatre
itérations** perdues à retrouver un chemin qui était correct. Le même
`target = Path(directory)` était copié dans trois handlers.

⚠️ Honnêteté sur la mesure : le corpus `training_pool` ne stocke pas les
arguments d'outils. Je n'ai donc **qu'une occurrence observée de chacun**, pas
de fréquence. C'est pourquoi les deux correctifs n'altèrent aucun comportement :
C ajoute une phrase, D ne se déclenche que sur un chemin qui, sinon, échouerait.
"""

from pathlib import Path

import pytest


# ══════════════════════════════════════════════════════════════════════════════
#  C — grep_search dit ce qu'il a réellement cherché
# ══════════════════════════════════════════════════════════════════════════════

_FILES = Path("src/reasoning/handlers/files.py").read_text(encoding="utf-8")


def test_le_cas_mesure_est_bien_reproductible():
    """Le pattern du run cherché littéralement ne trouve rien dans un texte qui
    contient pourtant chacun de ses termes."""
    import re
    contenu = '<canvas id="bookCanvas"></canvas> requestAnimationFrame(boucle);'
    pattern = "canvas|requestAnimationFrame|getContext"
    assert re.compile(re.escape(pattern), re.IGNORECASE).search(contenu) is None
    assert re.compile(pattern, re.IGNORECASE).search(contenu) is not None


def test_l_avertissement_existe_et_nomme_la_cause():
    assert "cherché comme TEXTE LITTÉRAL" in _FILES or "TEXTE LITTÉRAL" in _FILES
    assert "is_regex=true" in _FILES


@pytest.mark.parametrize("meta", ["|", "*", "+", "?", "[", "]", "(", ")", "^", "$"])
def test_tous_les_metacaracteres_courants_declenchent_l_avertissement(meta):
    i = _FILES.index("_litteral_note = \"\"")
    # Le jeu de caractères contient lui-même des parenthèses : on l'extrait
    # entre ses guillemets, pas en découpant sur ')'.
    jeu = _FILES[i:i + 300].split('for c in "')[1].split('"')[0]
    assert meta in jeu


def test_l_avertissement_accompagne_aussi_un_resultat_non_vide():
    """Trouver 2 lignes sur une regex tronquée est plus trompeur que n'en
    trouver aucune : l'avertissement doit valoir dans les deux cas."""
    i_vide = _FILES.index("Aucun résultat pour")
    i_plein = _FILES.index("résultat(s) pour")
    for i in (i_vide, i_plein):
        assert "_litteral_note" in _FILES[i - 200:i + 400]


def test_le_comportement_de_recherche_est_inchange():
    """On informe, on ne bascule pas en regex dans le dos de l'appelant :
    un pattern littéral doit rester cherché littéralement."""
    i = _FILES.index("_litteral_note = \"\"")
    bloc = _FILES[i:i + 900]
    assert "is_regex = True" not in bloc
    assert "regex = " not in bloc


def test_rien_ne_se_dit_sur_un_pattern_ordinaire():
    i = _FILES.index("_litteral_note = \"\"")
    assert 'if not is_regex and any(' in _FILES[i:i + 300]


# ══════════════════════════════════════════════════════════════════════════════
#  D — serve_website résout comme les autres outils
# ══════════════════════════════════════════════════════════════════════════════

from src.tools.website_builder import _resolve_directory_arg  # noqa: E402

_WEB = Path("src/tools/website_builder.py").read_text(encoding="utf-8")


def test_un_chemin_absolu_valide_est_rendu_tel_quel(tmp_path):
    d = tmp_path / "papier-cousu"
    d.mkdir()
    assert _resolve_directory_arg(str(d)) == d


def test_un_chemin_relatif_est_cherche_dans_le_workspace(tmp_path, monkeypatch):
    """LE cas mesuré : `2026-08-19/papier-cousu` doit se résoudre."""
    ws = tmp_path / "workspace"
    cible = ws / "2026-08-19" / "papier-cousu"
    cible.mkdir(parents=True)
    import src.utils.paths as _paths
    monkeypatch.setattr(_paths, "WORKSPACE_DIR", ws, raising=False)
    assert _resolve_directory_arg("2026-08-19/papier-cousu") == cible


def test_un_nom_nu_est_cherche_dans_les_dossiers_dates(tmp_path, monkeypatch):
    """Comme le fait déjà la branche `project_name`."""
    ws = tmp_path / "workspace"
    cible = ws / "2026-08-19" / "papier-cousu"
    cible.mkdir(parents=True)
    import src.utils.paths as _paths
    monkeypatch.setattr(_paths, "WORKSPACE_DIR", ws, raising=False)
    assert _resolve_directory_arg("papier-cousu") == cible


def test_un_chemin_introuvable_est_rendu_inchange():
    """Le message d'erreur doit continuer de nommer ce que l'appelant a demandé,
    pas un chemin deviné."""
    assert _resolve_directory_arg("nulle-part-du-tout") == Path("nulle-part-du-tout")


def test_ne_leve_jamais():
    assert _resolve_directory_arg("") == Path("")
    assert isinstance(_resolve_directory_arg("a/b/c"), Path)


def test_les_trois_handlers_sont_alignes():
    """serve / edit / export partageaient la même ligne fautive : en corriger un
    seul aurait laissé le défaut vivant à deux endroits."""
    assert _WEB.count("target = _resolve_directory_arg(directory)") == 3
    assert "target = Path(directory)\n" not in _WEB


def test_la_raison_du_lot_est_datee_dans_le_code():
    entete = _WEB[_WEB.index("LOT Z30 — résoudre un `directory`"):][:1400]
    assert "18:07:46" in entete
    assert "list_directory" in entete
