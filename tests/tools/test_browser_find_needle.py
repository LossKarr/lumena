"""W4 — Recherche « aiguille » : le cœur testable est _best_passage, qui isole
le passage le plus pertinent d'un texte pour une requête (sans navigateur).
"""
def bp(*a, **k):
    from src.tools.playwright_browser import PlaywrightBrowser
    return PlaywrightBrowser._best_passage(*a, **k)

TEXT = (
    "La Tour Eiffel est un monument parisien.\n\n"
    "Construite par Gustave Eiffel, elle a été inaugurée en 1889.\n\n"
    "La hauteur de la Tour Eiffel est de 330 mètres avec ses antennes.\n\n"
    "Elle reçoit des millions de visiteurs chaque année."
)


def test_isole_le_passage_qui_contient_la_reponse():
    res = bp(TEXT, "hauteur de la Tour Eiffel mètres")
    assert "330 mètres" in res["passage"]
    assert res["score"] > 0


def test_choisit_le_meilleur_passage_parmi_plusieurs():
    # Le passage "hauteur ... 330 mètres" doit battre "inaugurée en 1889"
    res = bp(TEXT, "hauteur mètres")
    assert "330" in res["passage"]


def test_must_include_ecarte_les_passages_sans_le_terme():
    # On EXIGE "1889" → seul le passage de l'inauguration peut gagner
    res = bp(TEXT, "Tour Eiffel", must_include=["1889"])
    assert "1889" in res["passage"]


def test_must_include_absent_du_texte_donne_score_nul():
    res = bp(TEXT, "Tour Eiffel", must_include=["sous-marin"])
    assert res["score"] == 0.0
    assert res["passage"] == ""


def test_texte_vide():
    res = bp("", "quoi que ce soit")
    assert res["score"] == 0.0


def test_tokenisation_ignore_les_mots_vides():
    from src.tools.playwright_browser import PlaywrightBrowser
    toks = PlaywrightBrowser._needle_tokens("Quelle est la hauteur de la Tour Eiffel")
    assert "hauteur" in toks and "tour" in toks and "eiffel" in toks
    assert "la" not in toks and "est" not in toks and "de" not in toks
