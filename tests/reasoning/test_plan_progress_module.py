"""Garde-fou structurel + unitaires — extraction plan_progress.py (Phase 4A).

Helpers PURS de complétion de tâches plan (périmètre outil ↔ tâche).
Vérifie : module autonome (pas de cycle), re-export identité, comportement ancré.
"""
import ast
from pathlib import Path

import src.reasoning.plan_progress as pp
import src.reasoning.react as r

_PUBLIC = [
    "_BROWSER_PLAN_PASSIVE_TOOLS", "_READ_ONLY_DISCOVERY_PLAN_TOOLS",
    "_browser_passive_tool_can_complete_task",
    "_read_only_discovery_tool_can_complete_task",
    "_SYNTH_KW", "_SYNTH_SIDE_EFFECT_BLOCK_KW", "final_fulfills_task",
]


def test_module_auto_contenu_pas_de_cycle():
    tree = ast.parse(Path(pp.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    assert not [m for m in imported if "react" in m], f"cycle: {imported}"
    assert imported <= {"__future__"}, f"imports inattendus: {imported}"


def test_react_reexporte_les_memes_objets():
    for s in _PUBLIC:
        assert hasattr(r, s) and getattr(r, s) is getattr(pp, s), s


def test_browser_passif_perimetre():
    f = pp._browser_passive_tool_can_complete_task
    assert f("browser_navigate", "vérifier que le site est accessible") is True
    assert f("browser_get_content", "identifier le bouton de connexion") is True
    # contexte non-browser → refusé
    assert f("browser_get_content", "lire l'email reçu") is False
    # outil hors périmètre
    assert f("browser_click", "cliquer") is False


def test_readonly_discovery_perimetre():
    f = pp._read_only_discovery_tool_can_complete_task
    assert f("get_time", "quelle heure est-il") is True
    assert f("get_time", "écris un fichier") is False
    assert f("health_check", "vérifier que le serveur est opérationnel") is True
    # web_search ne complète PAS une tâche d'échange/conversation
    assert f("web_search", "échanger avec l'IA") is False
    # outil inconnu → True (laisse passer, ce n'est pas un outil de découverte bridé)
    assert f("write_file", "n'importe quoi") is True


def test_final_fulfills_task():
    f = pp.final_fulfills_task
    # tâches "réalisées par le FINAL" (synthèse/rapport)
    assert f("Présenter le rapport à l'utilisateur") is True
    assert f("Résumer les résultats") is True
    assert f("Confirmer l'échange") is True
    # tâches à effet de bord → exigent une vraie action, pas le FINAL seul
    assert f("Envoyer le rapport par email") is False
    assert f("Déployer le site web") is False
    # tâche métier sans mot de synthèse → False
    assert f("Installer le package fastmcp") is False
    assert f("") is False
