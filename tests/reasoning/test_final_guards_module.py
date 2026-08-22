"""Garde-fou structurel + unitaires — extraction final_guards.py (Phase 3).

Helpers PURS des guards de réponse finale (déménagement pur). On vérifie :
- module autonome (aucun import react → pas de cycle) ;
- re-export identité via react ;
- comportement ancré (intention / strip thought-leak / remask).
"""
import ast
from pathlib import Path

import src.reasoning.final_guards as fg
import src.reasoning.react as r

_PUBLIC = [
    "_INTENTION_MARKERS", "_DELIVERABLE_MARKERS", "_INTERNAL_PREFIXES",
    "_looks_like_intention", "strip_thought_leak_prefix", "remask_secrets",
    "extract_mission_deliverable",
]


def test_extract_mission_deliverable():
    f = fg.extract_mission_deliverable
    # vrai résultat livrable → renvoie le corps SANS le préfixe "Résultat de <id> :"
    body = "✅ **Mission accomplie !** Le dossier est prêt.\n📦 1 livrable(s) : a.md"
    assert f(f"Résultat de task_abc123 :\n{body}") == body
    # multi-lignes conservées
    assert f("Résultat de task_x :\nLigne 1\nLigne 2").splitlines() == ["Ligne 1", "Ligne 2"]
    # EN COURS (ne commence pas par "Résultat de") → None
    assert f("Mission task_x pas encore terminée — EN COURS (état technique=checkpointed).") is None
    # terminée sans résultat → None
    assert f("Mission task_x terminée sans résultat (état=failed).") is None
    # résumé vide → None
    assert f("Résultat de task_x :\n(pas de résumé)") is None
    # corps vide / pas de saut de ligne / vide → None
    assert f("Résultat de task_x :\n   ") is None
    assert f("Résultat de task_x :") is None
    assert f("") is None
    assert f(None) is None  # type: ignore


def test_module_auto_contenu_pas_de_cycle():
    tree = ast.parse(Path(fg.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    assert not [m for m in imported if "react" in m], f"cycle: {imported}"
    # LOT 2.11.E : `pathlib` (stdlib, auto-contenu, aucun cycle) rejoint le set
    # autorisé — published_target_missing_on_disk vérifie l'existence disque.
    assert imported <= {"re", "typing", "__future__", "pathlib"}, f"imports inattendus: {imported}"


def test_react_reexporte_les_memes_objets():
    for s in _PUBLIC:
        assert hasattr(r, s) and getattr(r, s) is getattr(fg, s), s


def test_looks_like_intention():
    assert fg._looks_like_intention("je vais maintenant synthétiser la réponse") is True
    assert fg._looks_like_intention("") is True
    assert fg._looks_like_intention(None) is True  # type: ignore
    # livrable concret (chiffres/%) → pas une intention
    assert fg._looks_like_intention("Résultat : 1234 lignes, 56 colonnes, 78 % traités") is False


def test_strip_thought_leak_prefix():
    leaked = ("Je dois analyser la demande. "
              "Voici la réponse complète et utile pour l'utilisateur avec assez de contenu.")
    cleaned = fg.strip_thought_leak_prefix(leaked)
    assert cleaned and cleaned.startswith("Voici la réponse")
    # trop court après nettoyage → None
    assert fg.strip_thought_leak_prefix("Je dois faire un truc.") is None


def test_remask_secrets():
    # une valeur concrète du même préfixe/suffixe qu'un token masqué observé → re-masquée
    out = fg.remask_secrets("host = db5012345.hosting-data.io",
                            ["config observée: db50****.hosting-data.io"])
    assert "db50****.hosting-data.io" in out
    assert "db5012345" not in out
    # pas de token masqué observé → answer inchangée
    assert fg.remask_secrets("rien de masqué", ["tout en clair"]) == "rien de masqué"


def test_wrappers_react_delegent():
    from src.reasoning.react import ReActLoop
    # staticmethod délègue au helper pur
    txt = "Je dois analyser. " + "Contenu livrable réel et suffisamment long pour passer le seuil."
    assert ReActLoop._strip_thought_leak_prefix(txt) == fg.strip_thought_leak_prefix(txt)
