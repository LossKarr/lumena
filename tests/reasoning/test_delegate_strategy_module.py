"""Garde-fou structurel — extraction delegate_strategy.py (déménagement pur).

Vérifie que :
- le module est importable seul et auto-contenu (pas d'import de react → pas de
  cycle) ;
- les 18 symboles publics existent ;
- react ré-exporte EXACTEMENT les mêmes objets (identité, pas une copie) → les
  imports historiques `from src.reasoning.react import _looks_like_...` tiennent.

Si quelqu'un re-noie ces helpers dans react ou casse le re-export, ce test pète.
"""
from pathlib import Path

import src.reasoning.delegate_strategy as d
import src.reasoning.react as r

_PUBLIC = [
    "_DELEGATE_NOOP_MARKERS", "_WEB_DELIVERY_MARKERS", "_CANVAS_DELIVERY_MARKERS",
    "_CANVAS_NON_TECHNICAL_MARKERS",
    "_fold_react_status_text", "_delegate_report_has_real_work",
    "_post_delegate_web_verify_enabled", "_looks_like_web_delegate_delivery",
    "_delegate_delivery_expects_canvas", "_is_post_codeagent_synthesis_task",
    "_is_post_codeagent_conditional_correction_task", "_is_post_codeagent_closure_task",
    "_candidate_is_web_project", "_extract_existing_web_project_path",
    "_build_post_delegate_web_verify_success_query", "_build_post_delegate_continue_query",
    "_verify_report_has_preview_server_mime_error",
    "_build_post_delegate_web_verify_failure_query",
]


def test_module_importable_et_symboles_presents():
    for s in _PUBLIC:
        assert hasattr(d, s), f"manquant dans delegate_strategy: {s}"


def test_module_auto_contenu_pas_de_dependance_react():
    """Aucun IMPORT réel vers react/react_config (anti-cycle). On inspecte les
    statements d'import via AST — pas le texte (la docstring les mentionne)."""
    import ast
    tree = ast.parse(Path(d.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    forbidden = [m for m in imported if "react" in m or "tool_registry" in m
                 or "execution_ledger" in m]
    assert not forbidden, f"import interdit (cycle): {forbidden}"
    # stdlib uniquement
    assert imported <= {"json", "os", "re", "unicodedata", "pathlib", "typing",
                        "__future__"}, f"imports inattendus: {imported}"


def test_react_reexporte_les_memes_objets():
    for s in _PUBLIC:
        assert hasattr(r, s), f"react ne ré-exporte plus: {s}"
        assert getattr(r, s) is getattr(d, s), f"re-export divergent (copie?) pour {s}"


def test_comportement_inchange_spotcheck():
    # quelques cas concrets pour ancrer le comportement après déménagement
    assert d._fold_react_status_text("Créé É") == "cree e"
    assert d._is_post_codeagent_synthesis_task("Résumer à l'utilisateur") is True
    assert d._is_post_codeagent_synthesis_task("Envoyer le PDF par email") is False
    assert d._delegate_report_has_real_work("delegate_task", "livraison refusée") is False
