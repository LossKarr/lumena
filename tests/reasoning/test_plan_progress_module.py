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
    "correction_task_blocks_readonly",
    "tool_explicit_task_blocks",  # LOT 2.7
    "sourced_web_research_task_proven",
    "browser_interaction_task_blocks", "artifact_target_task_blocks",
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


def test_recherche_sourcee_exige_des_urls_reelles():
    f = pp.sourced_web_research_task_proven
    task = "Étape 3: Rechercher 2 recommandations énergétiques sourcées"
    assert f("delegate_and_wait", task, "Deux recommandations trouvées.") is False
    assert f("web_search_brave", task, "https://ademe.fr/guide") is False
    assert f(
        "parallel_tools",
        task,
        "https://ademe.fr/guide puis https://ecologie.gouv.fr/aides",
    ) is True
    # A delegated report remains valid when it carries the actual evidence.
    assert f(
        "delegate_and_wait",
        task,
        "Sources: https://ademe.fr/guide https://ecologie.gouv.fr/aides",
    ) is True
    assert f("delegate_and_wait", "Rédiger les recommandations", "sans URL") is True


def test_pages_reellement_consultees_exigent_une_lecture_de_page():
    f = pp.sourced_web_research_task_proven
    task = "Rechercher 2 sources et fournir les URL reellement consultees"
    search_obs = "https://ademe.fr/guide https://ecologie.gouv.fr/aides"
    assert f("web_search_brave", task, search_obs) is False
    assert f("web_fetch", task, "Page lue: https://ademe.fr/guide") is False
    assert f(
        "parallel_tools",
        task,
        "web_fetch: https://ademe.fr/guide\nweb_fetch: https://ecologie.gouv.fr/aides",
    ) is True


def test_interaction_browser_task_requires_strong_tool():
    f = pp.browser_interaction_task_blocks
    task = "Saisir 175, cliquer puis verifier que le total change dans le DOM"
    assert f("browser_navigate", task) is True
    assert f("browser_type_index", task) is True
    assert f("browser_click_index", task) is True
    assert f("browser_screenshot", task) is True
    assert f("browser_evaluate", task) is False
    assert f("browser_verify_local_project", task) is False
    assert f("browser_navigate", "Ouvrir la page dans le navigateur") is False


def test_artifact_target_extension_mismatch_blocks_plan_credit():
    f = pp.artifact_target_task_blocks
    assert f("write_file", "Ecrire consommation.csv", {"path": "SOURCES.md"}) is True
    assert f("write_file", "Ecrire consommation.csv", {"path": "consommation.csv"}) is False
    assert f("write_file", "Creer HTML CSS JS", {"path": "style.css"}) is False
    assert f("web_fetch", "Rechercher des sources", {"url": "https://example.test"}) is False


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


def test_final_fulfills_closure_tasks_missions():
    # Lot 5 — les libellés de clôture (lead/workers) sont réalisés par le FINAL
    f = pp.final_fulfills_task
    assert f("Finaliser") is True
    assert f("Rendre le résultat final") is True
    assert f("Livrer le texte final") is True
    assert f("Finaliser et rendre le résultat") is True
    # mais une clôture À EFFET DE BORD reste une vraie tâche (pas FINAL seul)
    assert f("Finaliser et envoyer le rapport par email") is False
    assert f("Livrer le résultat final par mail") is False


def test_final_fulfills_passthrough_via_final():
    # Lot 5 (B′) — « Retourner le rapport via FINAL » = réalisé par le FINAL lui-même
    f = pp.final_fulfills_task
    assert f("Retourner le rapport via FINAL") is True
    assert f("Retourner le résultat à l'utilisateur") is True
    # mais une passerelle À EFFET DE BORD reste une vraie tâche
    assert f("Retourner le rapport par email") is False


def test_final_fulfills_worker_redaction_tasks():
    # #2 (2026-06-30) — les tâches de RÉDACTION des plans auto-générés des workers
    # sont réalisées par le texte FINAL (le livrable d'un worker EST sa réponse).
    f = pp.final_fulfills_task
    assert f("Rédiger le paragraphe structuré") is True
    assert f("Rédiger un paragraphe final") is True
    assert f("Synthétiser en un paragraphe structuré") is True
    assert f("Rédiger une section complète et propre") is True
    # ⚠️ CAS CRITIQUE : une tâche qui NOMME un fichier de sortie exige write_file,
    # PAS un simple FINAL — même si elle contient « rédiger ».
    assert f("Écrire le fichier workspace/jeux_cultes.md") is False
    assert f("Rédiger un guide dans workspace/jeux_cultes.md") is False
    assert f("Sauvegarder le paragraphe dans data/notes.txt") is False
    # effet de bord classique reste bloqué
    assert f("Envoyer le rapport par email") is False


def test_verify_satisfied_by_artifact_read():
    # #3 (2026-06-30) — helper pur : une LECTURE crédite une tâche de vérification
    # UNIQUEMENT si elle relit un artefact réellement écrit avant (artifact_reread).
    from src.reasoning.plan_evidence import verify_satisfied_by_artifact_read as f
    # cas nominal : read_file + tâche verify + artefact relu → True
    assert f("read_file", "Vérifier le fichier final", artifact_reread=True) is True
    assert f("read_document", "Vérifier le rapport produit", artifact_reread=True) is True
    # pas d'artefact relu (lecture arbitraire) → False (garde-fou « lecture ≠ preuve »)
    assert f("read_file", "Vérifier le fichier final", artifact_reread=False) is False
    # outil non-lecteur → False (ce n'est pas le rôle de ce helper)
    assert f("write_file", "Vérifier le fichier final", artifact_reread=True) is False
    # tâche NON-verify → False (on ne crédite que les étapes de vérification)
    assert f("read_file", "Rédiger le paragraphe", artifact_reread=True) is False
    assert f("read_file", "", artifact_reread=True) is False


def test_mission_progress_proven():
    # Lot 5 (B′) — relaxation PLAN GUARD : travail réel prouvé (livrable OU recherche)
    f = pp.mission_progress_proven
    assert f({"write_file"}) is True
    assert f({"create_pdf"}) is True
    assert f({"delegate_and_wait"}) is True
    assert f({"deep_research"}) is True
    assert f({"web_search", "web_fetch"}) is True
    # rien fait / readonly trivial → False (le worker reste nudgé)
    assert f({"get_time"}) is False
    assert f(set()) is False
    assert f(None) is False


def test_delegation_task_fulfilled():
    f = pp.delegation_task_fulfilled
    # vraies tâches de délégation → True (accomplies par delegate_and_wait réussi)
    assert f("Lancer 3 sous-agents en parallèle pour les 3 styles de café") is True
    assert f("Déléguer à des workers la recherche") is True
    assert f("Étape 1: Lancer 3 sous-missions via delegate_and_wait") is True
    assert f("Lancer des sous-agents") is True
    # NON-délégation → False (pas de sur-match)
    assert f("Lancer le serveur web") is False
    assert f("Fusionner les résultats dans le fichier") is False
    assert f("Vérifier le fichier final") is False
    # « worker » seul (sans verbe d'action) → False
    assert f("Comparer les modèles worker pool de Go") is False
    assert f("") is False


def test_mission_deliverable_finalizable():
    # Finalisation déterministe du lead : artefact écrit + plan 100% complété.
    from src.reasoning.plan_progress import mission_deliverable_finalizable as f
    from types import SimpleNamespace as _T

    def _plan(*flags):
        return [_T(completed=c, description=d) for d, c in flags]

    # Cas 1 : artefact + toutes tâches complétées (relecture faite) → True
    p1 = _plan(("Lancer 6 sous-agents", True), ("Fusionner", True), ("Vérifier le fichier", True))
    assert f(p1, artifact_written=True) is True
    # Cas 2 : artefact mais tâche de vérif encore ouverte → False (on laisse read_file)
    p2 = _plan(("Fusionner", True), ("Vérifier le fichier final", False))
    assert f(p2, artifact_written=True) is False
    # Cas 3 : pas de tâche de vérif, tout complété → True dès l'écriture
    p3 = _plan(("Rechercher", True), ("Écrire le fichier", True))
    assert f(p3, artifact_written=True) is True
    # Footgun protégé : une tâche à EFFET DE BORD encore ouverte → False (pas de saut d'étape)
    p4 = _plan(("Fusionner", True), ("Envoyer le rapport par mail", False))
    assert f(p4, artifact_written=True) is False
    # Pas d'artefact sur disque → jamais déterministe
    assert f(p1, artifact_written=False) is False
    # Plan vide SANS relecture → False (on garde le FINAL LLM normal)
    assert f([], artifact_written=True) is False

    # ── Cas 2 (2026-07-01) : relecture de l'artefact = preuve indépendante du plan ──
    # Worker SANS plan (run thés) mais fichier écrit ET relu → True.
    assert f([], artifact_written=True, target_reread=True) is True
    # Plan émis mais verify NON crédité + relecture constatée → True (débloque le SKIP).
    assert f(p2, artifact_written=True, target_reread=True) is True
    # target_reread ne sauve JAMAIS un effet de bord externe ouvert (garde-fou absolu).
    assert f(p4, artifact_written=True, target_reread=True) is False
    # Relecture mais artefact pas confirmé écrit → False.
    assert f([], artifact_written=False, target_reread=True) is False
    # Non-régression : plan complet reste True même sans relecture.
    assert f(p1, artifact_written=True, target_reread=False) is True


def test_correction_task_blocks_readonly():
    # P0 (2026-07-02) — « lire = diagnostiquer, pas corriger ». Cf. run taskflow :
    # read_files_batch a marqué « Corriger les erreurs » completed → FINALIZE
    # prématuré avec 7 tests rouges.
    f = pp.correction_task_blocks_readonly
    # Tâche de correction + outil de lecture → bloqué (non créditable).
    assert f("read_files_batch", "Corriger les erreurs") is True
    assert f("read_file", "Étape 4: Corriger les erreurs de test") is True
    assert f("grep_search", "réparer les tests qui échouent") is True
    assert f("list_directory", "déboguer l'import cassé") is True
    # Même tâche mais MUTATION → autorisé (une mutation crédite bien la correction).
    assert f("edit_file", "Corriger les erreurs") is False
    assert f("write_file", "Corriger les erreurs") is False
    assert f("apply_patch", "réparer les tests") is False
    # Tâche NON-correction + lecture → non concerné (pas de sur-blocage).
    assert f("read_file", "Lire les fichiers clés") is False
    assert f("read_files_batch", "Explorer la structure du projet") is False
    assert f("read_file", "") is False


def test_tool_explicit_task_blocks():
    # LOT 2.7 (run NoteFlash 2026-07-02) — le plan disait « Poser le contrat via
    # write_mission_contract » et la tâche a été créditée par… create_mission.
    # Règle : une tâche qui NOMME un outil précis n'est créditée QUE par cet outil.
    f = pp.tool_explicit_task_blocks
    # Le cas réel du run : create_mission NE crédite PAS la tâche contrat.
    assert f("create_mission", "Poser le contrat via write_mission_contract") is True
    # Le bon outil crédite.
    assert f("write_mission_contract", "Poser le contrat via write_mission_contract") is False
    # delegate_and_wait nommé → seul lui.
    assert f("create_mission", "Lancer les 4 sous-tâches via delegate_and_wait") is True
    assert f("delegate_and_wait", "Lancer les 4 sous-tâches via delegate_and_wait") is False
    # pytest nommé → seuls les outils d'EXÉCUTION réelle (jamais une lecture).
    assert f("read_file", "Lancer pytest et corriger jusqu'au vert") is True
    assert f("write_file", "Lancer pytest et corriger jusqu'au vert") is True
    assert f("run_command", "Lancer pytest et corriger jusqu'au vert") is False
    assert f("run_tests", "Étape 5: pytest vert") is False
    # browser_navigate nommé → seul lui.
    assert f("web_fetch", "Vérifier au navigateur via browser_navigate") is True
    assert f("browser_navigate", "Vérifier au navigateur via browser_navigate") is False
    # Les rails documentaires nommés ne se créditent jamais entre eux.
    assert f("generate_studio_document", "Générer les restants via create_pdf") is True
    assert f("create_pdf", "Générer les restants via create_pdf") is False
    assert f("generate_studio_document", "Générer le lot via generate_studio_documents") is True
    assert f("generate_studio_documents", "Générer le lot via generate_studio_documents") is False
    # Tâche sans nom d'outil → non concerné (pas de sur-blocage).
    assert f("create_mission", "Créer la mission") is False
    assert f("read_file", "Lire les fichiers clés") is False
    assert f("read_file", "") is False


def test_browser_verify_local_project_is_strictly_tool_explicit():
    f = pp.tool_explicit_task_blocks
    task = "Etape 6: browser_verify_local_project"

    assert f("browser_type_index", task) is True
    assert f("browser_click_index", task) is True
    assert f("browser_get_content", task) is True
    assert f("browser_verify_local_project", task) is False


def test_is_mission_tracking_task():
    f = pp.is_mission_tracking_task
    # vraies tâches de SUIVI de mission (les plans baby-sitting du log) → True
    assert f("Suivre avec `mission_status` si besoin") is True
    assert f("Récupérer le résultat avec `mission_result` puis FINAL") is True
    assert f("Suivre l'avancement de la mission") is True
    # tâches RÉELLES (effet de bord) → JAMAIS auto-complétées (pas de trou)
    assert f("Envoyer un mail à l'utilisateur") is False
    assert f("Créer le fichier veille.md") is False
    assert f("") is False
