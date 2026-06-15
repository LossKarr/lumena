"""B-2ter — Garde-fou cookie : les contrôles de consentement (CMP) ne doivent
jamais rafler les premiers index du DOM, sinon le LLM clique "Accepter" /
"Préférences de consentement" en croyant ouvrir le contenu (cas vécu runtime).

On teste la couche 2 (déprioritisation dans l'index), pure et sans navigateur.
"""
from src.computer_use.dom_indexer import (
    _is_consent_control,
    _deprioritize_consent,
)


def test_is_consent_control_detecte_les_boutons_cmp():
    for name in (
        "Accepter",
        "Tout accepter",
        "Refuser",
        "Voir les préférences",
        "Préférences de consentement",
        "Accept all",
        "Reject all",
        "Gérer les cookies",
        "Continuer sans accepter",
    ):
        assert _is_consent_control({"name": name}), name


def test_is_consent_control_ne_touche_pas_au_contenu_legitime():
    for name in (
        "Rechercher",
        "Marques",
        "Occasions",
        "Paris",
        "Ajouter au panier",
        "Voir le comparatif complet",
        "Connexion",
    ):
        assert not _is_consent_control({"name": name}), name


def test_deprioritize_pousse_le_consent_en_fin_sans_rien_perdre():
    raw = [
        {"name": "Préférences de consentement"},  # consent en tête (cas du log)
        {"name": "Accepter"},
        {"name": "Marques"},
        {"name": "Occasions"},
        {"name": "Paris"},
    ]
    out = _deprioritize_consent(raw)
    # Rien n'est perdu
    assert len(out) == len(raw)
    # Les 3 premiers index sont désormais du vrai contenu
    assert [e["name"] for e in out[:3]] == ["Marques", "Occasions", "Paris"]
    # Le consent est relégué en fin
    assert all(_is_consent_control(e) for e in out[-2:])


def test_deprioritize_est_stable_pour_le_contenu():
    raw = [{"name": n} for n in ("A", "B", "C", "D")]
    # Aucun consent -> ordre strictement préservé
    assert [e["name"] for e in _deprioritize_consent(raw)] == ["A", "B", "C", "D"]


def test_deprioritize_liste_vide():
    assert _deprioritize_consent([]) == []
