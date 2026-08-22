"""Bug content pollué (run scientifiques.md, 2026-07-01).

json.loads échoue sur un `content` Markdown multi-ligne (guillemets internes + retours
ligne) → l'ancien fallback glouton `(.+)$` happait la valeur + les args sœurs, produisant
un fichier commençant par `"` et finissant par `", "force_rewrite": true, …}`.
Ces tests verrouillent l'extracteur tolérant + la sanity-check + le message honnête.
"""
from src.reasoning.response_parser import (
    parse_action_args,
    _extract_content_and_siblings,
    deliverable_looks_malformed,
)


def test_extract_content_strips_leading_quote_and_sibling_tail():
    # Signature EXACTE du bug : content happé + queue force_rewrite/rewrite_reason.
    cleaned = ('{"path": "workspace/x.md", "content": "# Titre\n'
               'Il publia "Principia" en 1687\nFin", '
               '"force_rewrite": true, "rewrite_reason": "clean rewrite"}')
    out = _extract_content_and_siblings(cleaned)
    c = out["content"]
    assert not c.startswith('"'), "guillemet de tête non retiré"
    assert "force_rewrite" not in c and "rewrite_reason" not in c, "queue JSON happée dans le contenu"
    assert c.startswith("# Titre")
    assert c.rstrip().endswith("Fin")
    assert 'Il publia "Principia" en 1687' in c, "guillemets internes légitimes préservés"
    # les vraies valeurs des clés sœurs sont récupérées séparément
    assert out.get("force_rewrite") is True
    assert out.get("rewrite_reason") == "clean rewrite"


def test_parse_action_args_polluted_input_yields_clean_content():
    # Intégration : JSON invalide (guillemets internes non échappés) → fallback tolérant.
    inp = ('{"path": "workspace/x.md", "content": "# T\n'
           'Avec "guillemets" internes\nFin", "force_rewrite": true}')
    args = parse_action_args(inp)
    assert not args["content"].startswith('"')
    assert "force_rewrite" not in args["content"]
    assert args["content"].startswith("# T")


def test_valid_json_still_parses_cleanly():
    # Non-régression : un JSON bien formé passe par json.loads, inchangé.
    args = parse_action_args('{"path": "a.md", "content": "hello world"}')
    assert args == {"path": "a.md", "content": "hello world"}


def test_deliverable_looks_malformed():
    # Pollué : guillemet de tête isolé + queue force_rewrite → True
    assert deliverable_looks_malformed('"# 🔬 Titre\n...contenu...\n", "force_rewrite": true}') is True
    assert deliverable_looks_malformed('"{ "a": 1 }') is True  # quote isolé avant {
    # Propre → False
    assert deliverable_looks_malformed('# Titre propre\n\nContenu tout à fait normal.') is False
    assert deliverable_looks_malformed('') is False
    # Faux positif évité : « force_rewrite » au MILIEU (pas dans la queue) → False
    assert deliverable_looks_malformed(
        '# Doc\n\nOn évoque force_rewrite ici.\n\n' + 'texte propre. ' * 30) is False
