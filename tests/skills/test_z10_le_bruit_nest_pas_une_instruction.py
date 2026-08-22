"""LOT Z10 — du bruit présenté comme des ordres n'est pas neutre.

`delegate_task` injecte les skills actifs dans le prompt du CodeAgent sous le
titre : « **Instructions spécifiques à appliquer dans ton code** ». Un skill
hors sujet n'y est donc pas ignoré — il y est *appliqué*.

Un seuil existait (`>= 5.0`) avec le bon commentaire (« éviter d'injecter des
skills sur des matchs faibles »). Il était trop bas **de très peu** : le matcher
a un plancher structurel à **5.5**, si bien qu'un skill sans aucun rapport le
franchissait. Mesuré sur les descriptions réelles des workers :

    donnees.js  (persistance localStorage) → algorithmic-art 5.5 · datagouv 5.5
    chrono.js   (minuteur)                 → algorithmic-art 5.5
    « corrige ce bug python »              → datagouv 5.5 · docx 5.5 · pdf 5.5

Corpus de contrôle, 13 cas réels (workers + chat), passage 5.0 → 6.0 :
**3 cas changent, les 3 sont du bruit à 5.5 ; aucun cas pertinent n'est perdu**
(css 17.5, html 17.5, design 26.0, pdf 21.5, xlsx 15.5 — tous conservés).

Ce lot ne change donc pas une politique : il corrige un réglage que la mesure
montrait faux d'un demi-point.
"""

import pytest

from src.skills.loader import _MIN_SKILL_SCORE, build_active_skills_context
from src.skills import match_skills


# ── Le plancher du matcher, qui est la raison d'être du seuil ────────────────


@pytest.mark.parametrize(
    "description",
    [
        "Code donnees.js : persistance localStorage animaux gardiens reservations",
        "Code chrono.js : demarrer arreter duree cumulee reprise apres rechargement",
        "corrige ce bug dans mon script python",
    ],
)
def test_le_bruit_natteint_jamais_le_seuil(description):
    """Ces trois cas injectaient du hors-sujet avant Z10."""
    retenus = [m for m in match_skills(query=description, max_results=3)
               if m.score >= _MIN_SKILL_SCORE]
    assert retenus == []


def test_le_seuil_est_au_dessus_du_plancher_structurel():
    """5.5 est le score d'un skill SANS rapport : le seuil doit le dépasser,
    sinon il ne filtre rien du tout. C'est exactement le défaut corrigé."""
    assert _MIN_SKILL_SCORE > 5.5


def test_le_seuil_reste_bas_pour_ne_pas_devenir_un_baillon():
    """Un seuil trop haut priverait les missions de leurs guides de design —
    le défaut inverse, tout aussi réel."""
    assert _MIN_SKILL_SCORE <= 10.0


# ── Les cas pertinents ne doivent RIEN perdre ────────────────────────────────


@pytest.mark.parametrize(
    "description,attendu",
    [
        ("Code styles.css : toute la presentation des deux pages, design clair lisible",
         "frontend-design"),
        ("Code index.html : page publique accroche 3 arguments 3 formules tarifs",
         "frontend-design"),
        ("fais moi un site web moderne pour mon restaurant", "frontend-design"),
        ("ameliore le design de ma landing page elle est moche", "frontend-design"),
        ("fais moi un pdf de synthese avec les chiffres", "pdf"),
        ("construis un tableur de suivi de budget avec un graphique", "xlsx"),
    ],
)
def test_les_skills_pertinents_passent_toujours(description, attendu):
    noms = [m.name for m in match_skills(query=description, max_results=3)
            if m.score >= _MIN_SKILL_SCORE]
    assert attendu in noms


def test_le_worker_css_garde_son_guide_de_design():
    """Le cœur du sujet : c'est CE worker qui doit avoir `frontend-design`,
    et Z10 ne doit surtout pas le lui retirer."""
    ctx = build_active_skills_context(
        "Code styles.css : toute la presentation des deux pages, design clair lisible",
        max_results=2,
    )
    assert "frontend-design" in ctx


def test_le_worker_js_ne_recoit_plus_de_consignes_hors_sujet():
    ctx = build_active_skills_context(
        "Code donnees.js : persistance localStorage animaux gardiens reservations",
        max_results=2,
    )
    assert ctx == ""


# ── Le contrat de la fonction ────────────────────────────────────────────────


def test_un_contexte_vide_est_rendu_quand_rien_ne_correspond():
    """Vide, jamais une section d'instructions creuse : le CodeAgent ajoute un
    en-tête « Instructions à appliquer » dès que la chaîne est non vide."""
    assert build_active_skills_context("bonjour comment vas-tu", max_results=3) == ""


def test_le_seuil_est_une_constante_nommee():
    """Un seuil écrit en dur dans la condition ne se mesure pas et ne se teste
    pas — c'est ce qui l'avait laissé faux si longtemps."""
    from pathlib import Path

    src = Path("src/skills/loader.py").read_text(encoding="utf-8")
    assert "m.score >= _MIN_SKILL_SCORE" in src
    assert "m.score >= 5.0" not in src


def test_la_raison_du_seuil_est_ecrite_dans_le_code():
    """Le plancher 5.5 n'est pas devinable : sans lui, le prochain qui touche
    à cette valeur refera l'erreur."""
    from pathlib import Path

    src = Path("src/skills/loader.py").read_text(encoding="utf-8")
    i = src.index("_MIN_SKILL_SCORE: float")
    assert "5.5" in src[i - 400 : i]
