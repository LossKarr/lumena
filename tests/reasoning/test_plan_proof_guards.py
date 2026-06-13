"""Tests — Guards de preuve pour la réconciliation du plan et l'audit subagent.

Vérifie que :
A. delegate_task avec résultat non cohérent ne fait PAS avancer le plan
B. une étape "vérifier/fonctionnel" ne se coche pas sans preuve réelle
C. l'audit subagent logue success=False pour un résultat contenant "non trouvé"
D. une création valide peut cocher "créer" mais pas "vérifier"
E. une vraie vérification (run_command + 200 OK) peut cocher "vérifier"
"""

import pytest
from unittest.mock import MagicMock
from src.reasoning.react_config import TaskItem
from src.reasoning.plan_evidence import (
    _VERIFY_TASK_KEYWORDS,
    _VERIFY_PROOF_TOOLS,
    _VERIFY_OBS_PROOF_MARKERS,
    classify_observation,
    is_verify_task,
    has_verify_proof,
    reconcile_delegate_report,
)
from src.reasoning.react import _delegate_report_has_real_work


def test_delegate_report_has_real_work_accepts_real_codeagent_report():
    obs = "✅ **codeAgent terminé** (179.8s, 21 itérations)\n\nProjet créé et testé."
    assert _delegate_report_has_real_work("delegate_task", obs) is True


def test_delegate_report_has_real_work_rejects_noop_codeagent_report():
    obs = "✅ **codeAgent terminé** (0.0s, ? itérations)\n\n⏭️ run_tests : test_path requis."
    assert _delegate_report_has_real_work("delegate_task", obs) is False


# ─────────────────────────────────────────────────────────────────────────────
# Helpers : simuler ReActLoop._update_plan_progress et _reconcile_plan_from_delegate_success
# ─────────────────────────────────────────────────────────────────────────────

def _make_loop_with_plan(descriptions: list[str]):
    """Crée un objet ReActLoop minimal avec un plan de tâches."""
    from src.reasoning.react import ReActLoop
    loop = object.__new__(ReActLoop)
    loop._task_plan = [TaskItem(description=d) for d in descriptions]
    loop._last_auto_advance_iter = -1
    loop._plan_emitted = False
    loop._plan_last_emit_state = ""
    # Stubber l'émission d'état
    loop._emit_plan_state = lambda **kw: None
    return loop


def _attach_tool_categories(loop, module: str = "", semantic: str = ""):
    loop.tools = MagicMock()
    loop.tools.get_tool_module_category.return_value = module
    loop.tools.get_tool_semantic_category.return_value = semantic
    return loop


def _completed_tasks(loop) -> list[str]:
    return [t.description for t in loop._task_plan if t.completed]


def _pending_tasks(loop) -> list[str]:
    return [t.description for t in loop._task_plan if not t.completed]


# ─────────────────────────────────────────────────────────────────────────────
# A. delegate_task résultat non cohérent ne fait pas avancer le plan
# ─────────────────────────────────────────────────────────────────────────────

class TestDelegateIncoherentResult:
    def test_incoherent_report_does_not_mark_verify_task(self):
        """Un rapport delegate_task contenant du contenu de core.py ne doit pas
        marquer 'Vérifier que tout est fonctionnel' comme complétée."""
        loop = _make_loop_with_plan([
            "Créer le projet MoodForge",
            "Vérifier que tout est fonctionnel",
            "Présenter le résultat",
        ])
        # Simuler un rapport avec contenu de core.py — incoherent mais contient ✅
        incoherent_obs = "✅ CodeAgent terminé\n\n" + "content of core.py\n" * 20
        marked = loop._reconcile_plan_from_delegate_success(incoherent_obs, iteration=3)

        assert "Vérifier que tout est fonctionnel" in _pending_tasks(loop), (
            "La tâche 'vérifier' ne doit pas être marquée par un rapport incoherent"
        )
        assert "Présenter le résultat" in _pending_tasks(loop), (
            "La tâche 'présenter' ne doit pas être marquée par un rapport incoherent"
        )

    def test_incoherent_report_can_mark_matching_creation_task(self):
        """Un rapport qui contient les mots de 'Créer MoodForge' peut marquer CETTE tâche."""
        loop = _make_loop_with_plan([
            "Créer le projet MoodForge avec structure complète",
            "Vérifier que tout est fonctionnel",
        ])
        obs = "✅ CodeAgent terminé\n\nProjet MoodForge créé avec structure complète"
        loop._reconcile_plan_from_delegate_success(obs, iteration=3)

        assert "Créer le projet MoodForge avec structure complète" in _completed_tasks(loop)
        assert "Vérifier que tout est fonctionnel" in _pending_tasks(loop)

    def test_fallback_global_removed_does_not_mark_all(self):
        """La Passe 2 (fallback global pour plans courts) est supprimée :
        un plan ≤ 5 tâches + ✅ ne doit plus tout cocher."""
        loop = _make_loop_with_plan([
            "Créer le projet",
            "Vérifier que tout est fonctionnel",
            "Présenter le résultat",
        ])
        obs = "✅ Projet créé avec succès"
        loop._reconcile_plan_from_delegate_success(obs, iteration=1)

        # "Vérifier" et "Présenter" ne doivent PAS être marquées par le fallback global
        assert "Vérifier que tout est fonctionnel" in _pending_tasks(loop)
        assert "Présenter le résultat" in _pending_tasks(loop)

    def test_no_task_marked_when_obs_has_no_matching_words(self):
        """Aucune tâche marquée si l'observation ne contient pas 2+ mots des descriptions."""
        loop = _make_loop_with_plan([
            "Configurer les paramètres réseau",
            "Déployer l'application en production",
        ])
        obs = "✅ Terminé avec succès"  # aucun mot des descriptions
        marked = loop._reconcile_plan_from_delegate_success(obs, iteration=2)
        assert marked == 0
        assert len(_pending_tasks(loop)) == 2


# ─────────────────────────────────────────────────────────────────────────────
# B. Étape "vérifier/fonctionnel" ne se coche pas sans preuve réelle
# ─────────────────────────────────────────────────────────────────────────────

class TestVerifyGate:
    def _run_update(self, loop, tool_name: str, obs: str, iteration: int = 1, args: dict = None):
        loop._update_plan_progress(tool_name, args or {}, obs, iteration)

    def test_verify_task_blocked_by_read_file(self):
        """read_file ne peut pas cocher 'Vérifier que tout est fonctionnel'."""
        loop = _make_loop_with_plan(["Vérifier que tout est fonctionnel"])
        self._run_update(loop, "read_file", "contenu du fichier app.js\n" * 5)
        assert "Vérifier que tout est fonctionnel" in _pending_tasks(loop)

    def test_verify_task_blocked_by_node_check_syntax_only(self):
        """run_command avec juste un check syntaxique n'est pas une preuve fonctionnelle
        si l'observation ne contient pas de marqueur de preuve (port, running, 200...)."""
        loop = _make_loop_with_plan(["Vérifier que tout est fonctionnel"])
        self._run_update(
            loop, "run_command",
            "Vérification syntaxique OK — aucune erreur JS détectée",
        )
        # Pas de marqueur de preuve réelle (port, listening, 200, etc.)
        assert "Vérifier que tout est fonctionnel" in _pending_tasks(loop)

    def test_verify_task_blocked_by_failed_run_command(self):
        """run_command dont l'observation contient 'error' ne peut pas cocher 'vérifier'."""
        loop = _make_loop_with_plan(["Vérifier que tout est fonctionnel"])
        self._run_update(
            loop, "run_command",
            "Error: Cannot find module 'express'",
        )
        assert "Vérifier que tout est fonctionnel" in _pending_tasks(loop)

    def test_verify_task_blocked_by_refused_run_command(self):
        """run_command refusé par policy ne peut pas cocher 'vérifier'."""
        loop = _make_loop_with_plan(["Vérifier que tout est fonctionnel"])
        self._run_update(
            loop, "run_command",
            "⛔ Commande refusée par la politique de sécurité",
        )
        assert "Vérifier que tout est fonctionnel" in _pending_tasks(loop)

    def test_verify_task_blocked_by_create_project(self):
        """create_project ne peut pas cocher 'vérifier' même si ✅ présent."""
        loop = _make_loop_with_plan([
            "Créer le projet MoodForge",
            "Vérifier que tout est fonctionnel",
        ])
        self._run_update(loop, "create_project", "✅ Projet créé avec succès")
        assert "Vérifier que tout est fonctionnel" in _pending_tasks(loop)

    def test_verify_task_blocked_by_auto_advance(self):
        """L'auto-avancement générique ne doit pas cocher 'vérifier'."""
        loop = _make_loop_with_plan(["Vérifier que tout est fonctionnel"])
        # Simuler une observation substantielle d'un outil non trivial
        self._run_update(loop, "write_file", "Fichier écrit avec succès: app.js")
        assert "Vérifier que tout est fonctionnel" in _pending_tasks(loop)

    def test_functional_task_blocked_too(self):
        """'Vérifier que l'app est fonctionnelle' est aussi protégée."""
        loop = _make_loop_with_plan(["Vérifier que l'app est fonctionnelle"])
        self._run_update(loop, "write_file", "✅ Fichier créé")
        assert "Vérifier que l'app est fonctionnelle" in _pending_tasks(loop)

    def test_verify_project_blocked_by_create_directory_files_category(self):
        """Un dossier créé ne prouve pas que le projet web fonctionne."""
        loop = _attach_tool_categories(
            _make_loop_with_plan(["Vérifier le projet créé"]),
            module="files",
            semantic="files",
        )
        self._run_update(loop, "create_directory", "✅ Répertoire créé: workspace/site-test")
        assert "Vérifier le projet créé" in _pending_tasks(loop)

    def test_verify_task_allowed_by_run_command_with_proof(self):
        """run_command avec preuve réelle (port listening) peut cocher 'vérifier'."""
        loop = _make_loop_with_plan(["Vérifier que tout est fonctionnel"])
        self._run_update(
            loop, "run_command",
            "Server listening on port 3000 — HTTP 200 OK",
        )
        assert "Vérifier que tout est fonctionnel" in _completed_tasks(loop)

    def test_verify_task_allowed_by_web_fetch_200(self):
        """web_fetch avec 200 OK peut cocher 'vérifier'."""
        loop = _make_loop_with_plan(["Vérifier que tout est fonctionnel"])
        self._run_update(
            loop, "web_fetch",
            "HTTP 200 OK — localhost:3000 accessible, page title: MoodForge",
        )
        assert "Vérifier que tout est fonctionnel" in _completed_tasks(loop)

    def test_creation_task_not_affected_by_verify_gate(self):
        """'Créer le projet' n'est pas une tâche de vérification → gate ne s'applique pas."""
        loop = _make_loop_with_plan(["Créer le projet MoodForge"])
        self._run_update(loop, "create_project", "✅ Projet créé avec succès")
        assert "Créer le projet MoodForge" in _completed_tasks(loop)


# ─────────────────────────────────────────────────────────────────────────────
# C. subagent_audit : success=False pour résultat contenant "non trouvé"
# ─────────────────────────────────────────────────────────────────────────────

class TestSubAgentAuditErrorDetection:
    """Vérifie que _RESULT_ERROR_PATTERNS détecte correctement les erreurs textuelles."""

    # Réplique la logique de sub_agent.py._call_tool pour les tester isolément
    _RESULT_ERROR_PATTERNS = (
        "outil '", 'outil "', "tool '", 'tool "',
        "non trouvé", "not found", "introuvable",
        "nameerror", "attributeerror", "keyerror", "typeerror",
        "aucun outil", "unknown tool",
    )

    def _is_text_error(self, result_str: str) -> bool:
        lower = result_str.lower()
        return any(p in lower for p in self._RESULT_ERROR_PATTERNS)

    def test_outil_non_trouve_detected(self):
        result = "Erreur: Outil 'run_command' non trouvé dans le registre"
        assert self._is_text_error(result)

    def test_run_tests_non_trouve_detected(self):
        result = "Erreur: Outil 'run_tests' non trouvé"
        assert self._is_text_error(result)

    def test_read_files_batch_non_trouve_detected(self):
        result = "Erreur: Outil 'read_files_batch' non trouvé"
        assert self._is_text_error(result)

    def test_unknown_tool_detected(self):
        result = "Unknown tool: execute_command"
        assert self._is_text_error(result)

    def test_not_found_en_detected(self):
        result = "tool 'launch_server' not found in registry"
        assert self._is_text_error(result)

    def test_valid_result_not_flagged(self):
        """Un résultat valide ne doit pas être considéré comme une erreur."""
        result = "Fichier app.js écrit avec succès (842 octets)"
        assert not self._is_text_error(result)

    def test_command_output_not_flagged(self):
        result = "Server started on port 3000\nListening..."
        assert not self._is_text_error(result)

    def test_empty_result_not_flagged(self):
        assert not self._is_text_error("")

    def test_partial_word_not_flagged(self):
        """'found' dans un contexte positif ne doit pas être flaggué."""
        result = "Found 3 files matching pattern *.js"
        assert not self._is_text_error(result)


# ─────────────────────────────────────────────────────────────────────────────
# D. Création valide coche "créer" mais pas "vérifier"
# ─────────────────────────────────────────────────────────────────────────────

class TestCreationVsVerification:
    def test_create_project_marks_create_not_verify(self):
        """create_project coche la tâche de création, mais pas la tâche de vérification."""
        loop = _make_loop_with_plan([
            "Créer le projet MoodForge avec tous les fichiers",
            "Vérifier que tout est fonctionnel",
            "Présenter le résultat à l'utilisateur",
        ])
        loop._update_plan_progress(
            "create_project", {},
            "✅ Projet MoodForge créé : index.html, app.js, style.css générés",
            iteration=1,
        )
        completed = _completed_tasks(loop)
        pending = _pending_tasks(loop)

        assert any("Créer" in d for d in completed), "La tâche de création doit être cochée"
        assert any("Vérifier" in d for d in pending), "La tâche de vérification doit rester en attente"
        assert any("Présenter" in d for d in pending), "La tâche de présentation doit rester en attente"

    def test_write_file_does_not_mark_verify(self):
        loop = _make_loop_with_plan([
            "Écrire le fichier de configuration",
            "Vérifier que la configuration est fonctionnelle",
        ])
        loop._update_plan_progress(
            "write_file", {"path": "config.json"},
            "✅ Fichier config.json écrit avec succès",
            iteration=2,
        )
        pending = _pending_tasks(loop)
        assert any("Vérifier" in d for d in pending)

    def test_install_deps_does_not_mark_verify(self):
        loop = _make_loop_with_plan([
            "Installer les dépendances npm",
            "Vérifier que l'installation est fonctionnelle",
        ])
        loop._update_plan_progress(
            "run_command", {},
            "npm install completed: 142 packages installed",
            iteration=2,
        )
        pending = _pending_tasks(loop)
        # "npm install" n'est pas une preuve fonctionnelle (pas port/listening/200)
        assert any("Vérifier" in d for d in pending)

    def test_run_command_does_not_mark_browser_test_task(self):
        loop = _make_loop_with_plan(["Lancer un serveur local et tester avec le navigateur"])
        loop._update_plan_progress(
            "run_command",
            {},
            "⏳ Commande lancée en arrière-plan.\nCommande: python -m http.server 8081",
            iteration=3,
        )

        assert "Lancer un serveur local et tester avec le navigateur" in _pending_tasks(loop)


# ─────────────────────────────────────────────────────────────────────────────
# E. Vérification réelle peut cocher "vérifier"
# ─────────────────────────────────────────────────────────────────────────────

class TestRealVerificationAllowed:
    def test_run_command_with_port_listening_marks_verify(self):
        loop = _make_loop_with_plan(["Vérifier que le serveur est fonctionnel"])
        loop._update_plan_progress(
            "run_command", {},
            "Server listening on port 3000\nHTTP 200 OK",
            iteration=3,
        )
        assert "Vérifier que le serveur est fonctionnel" in _completed_tasks(loop)

    def test_health_check_marks_verify(self):
        loop = _make_loop_with_plan(["Vérifier que tout est accessible"])
        loop._update_plan_progress(
            "health_check", {},
            "✅ Service accessible — HTTP 200 OK, latency 12ms",
            iteration=2,
        )
        assert "Vérifier que tout est accessible" in _completed_tasks(loop)

    def test_browser_navigate_200_marks_verify(self):
        loop = _make_loop_with_plan(["Vérifier que le site est opérationnel"])
        loop._update_plan_progress(
            "browser_navigate", {"url": "http://localhost:3000"},
            "Page chargée avec succès — HTTP 200 — titre: MoodForge",
            iteration=4,
        )
        assert "Vérifier que le site est opérationnel" in _completed_tasks(loop)

    def test_browser_verify_local_project_marks_verify(self):
        loop = _attach_tool_categories(
            _make_loop_with_plan(["Vérifier que le site est opérationnel"]),
            module="website",
            semantic="website",
        )
        loop._update_plan_progress(
            "browser_verify_local_project",
            {"project_path": "workspace/site-test"},
            "## Runtime web verify: OK\nHTTP 200 OK\nPage loaded\nDOM ready_state: complete\nInteractions: scroll OK",
            iteration=4,
        )
        assert "Vérifier que le site est opérationnel" in _completed_tasks(loop)

    def test_browser_verify_local_project_marks_browser_test_task(self):
        loop = _attach_tool_categories(
            _make_loop_with_plan(["Lancer un serveur local et tester avec le navigateur"]),
            module="website",
            semantic="website",
        )
        loop._update_plan_progress(
            "browser_verify_local_project",
            {"project_path": "workspace/site-test"},
            "## Runtime web verify: OK\nHTTP 200 OK\nPage loaded\nDOM ready_state: complete\nInteractions: scroll OK",
            iteration=4,
        )

        assert "Lancer un serveur local et tester avec le navigateur" in _completed_tasks(loop)

    def test_run_tests_marks_verify(self):
        loop = _make_loop_with_plan(["Vérifier que les tests passent"])
        loop._update_plan_progress(
            "run_command", {},
            "Tests passed: 12/12 — success",
            iteration=3,
        )
        assert "Vérifier que les tests passent" in _completed_tasks(loop)

    def test_non_verify_task_still_completable_normally(self):
        """Les tâches non-verify ne sont pas impactées par le verify gate."""
        loop = _make_loop_with_plan(["Générer le logo du projet"])
        loop._update_plan_progress(
            "generate_image", {},
            "✅ Logo généré : logo.png (512x512)",
            iteration=1,
        )
        assert "Générer le logo du projet" in _completed_tasks(loop)


# ─────────────────────────────────────────────────────────────────────────────
# Constantes — vérification structurelle
# ─────────────────────────────────────────────────────────────────────────────

class TestVerifyGateConstants:
    def test_verify_keywords_nonempty(self):
        assert len(_VERIFY_TASK_KEYWORDS) > 0

    def test_proof_tools_are_action_tools(self):
        """Les outils de preuve doivent être des outils d'action, pas de lecture."""
        read_only = {"read_file", "list_files", "list_directory", "memory_search"}
        assert not (_VERIFY_PROOF_TOOLS & read_only), (
            f"Des outils read-only sont dans _VERIFY_PROOF_TOOLS: {_VERIFY_PROOF_TOOLS & read_only}"
        )

    def test_run_command_in_proof_tools(self):
        assert "run_command" in _VERIFY_PROOF_TOOLS

    def test_web_fetch_in_proof_tools(self):
        assert "web_fetch" in _VERIFY_PROOF_TOOLS

    def test_proof_markers_include_http_indicators(self):
        assert "200" in _VERIFY_OBS_PROOF_MARKERS
        assert "listening" in _VERIFY_OBS_PROOF_MARKERS
        assert "running" in _VERIFY_OBS_PROOF_MARKERS

    def test_verif_in_keywords(self):
        assert "vérif" in _VERIFY_TASK_KEYWORDS

    def test_fonctionnel_in_keywords(self):
        assert "fonctionnel" in _VERIFY_TASK_KEYWORDS


# ─────────────────────────────────────────────────────────────────────────────
# F. Tests directs des fonctions pures de plan_evidence — sans ReActLoop
# ─────────────────────────────────────────────────────────────────────────────

class TestPlanEvidencePureFunctions:
    """Vérifie les fonctions pures de plan_evidence.py de façon totalement isolée,
    sans instancier ReActLoop."""

    # ── classify_observation ──────────────────────────────────────────────────

    def test_classify_clean_success(self):
        has_failure, overridden = classify_observation("✅ Fichier créé avec succès")
        assert not has_failure

    def test_classify_pure_failure(self):
        has_failure, overridden = classify_observation("Erreur: module non trouvé")
        assert has_failure
        assert not overridden

    def test_classify_failure_overridden(self):
        """Ping timeout MAIS ports OPEN → succès global."""
        has_failure, overridden = classify_observation(
            "timeout: 2 hôtes — ports open: 80, 443"
        )
        assert has_failure
        assert overridden  # "open" annule l'échec

    def test_classify_empty_observation(self):
        has_failure, overridden = classify_observation("")
        assert not has_failure

    def test_classify_cross_symbol_not_failure(self):
        """❌ seul ne doit pas déclencher has_failure (pas dans _FAIL_MARKERS)."""
        has_failure, _ = classify_observation("❌ Aucun port ouvert détecté")
        assert not has_failure

    # ── is_verify_task ────────────────────────────────────────────────────────

    def test_is_verify_task_verif(self):
        assert is_verify_task("vérifier que le serveur est opérationnel")

    def test_is_verify_task_fonctionnel(self):
        assert is_verify_task("s'assurer que tout est fonctionnel")

    def test_is_verify_task_accessible(self):
        assert is_verify_task("confirmer que le site est accessible")

    def test_is_not_verify_task_create(self):
        assert not is_verify_task("créer le projet lumena")

    def test_is_not_verify_task_send(self):
        assert not is_verify_task("envoyer le rapport par email")

    # ── has_verify_proof ──────────────────────────────────────────────────────

    def test_has_proof_run_command_port(self):
        assert has_verify_proof("run_command", "Server listening on port 3000")

    def test_has_proof_web_fetch_200(self):
        assert has_verify_proof("web_fetch", "HTTP 200 OK — page accessible")

    def test_has_proof_health_check_success(self):
        assert has_verify_proof("health_check", "✅ Service accessible — latency 5ms")

    def test_no_proof_read_file(self):
        assert not has_verify_proof("read_file", "contenu du fichier : PORT=3000")

    def test_no_proof_run_command_no_markers(self):
        """run_command sans marqueur de preuve (juste syntaxe OK) ne suffit pas."""
        assert not has_verify_proof("run_command", "Vérification syntaxique OK — aucune erreur")

    def test_no_proof_create_project(self):
        assert not has_verify_proof("create_project", "✅ Projet créé avec succès")

    # ── reconcile_delegate_report ─────────────────────────────────────────────

    def test_reconcile_marks_matching_task(self):
        from src.reasoning.react_config import TaskItem
        tasks = [TaskItem(description="Créer le projet MoodForge avec tous les fichiers")]
        marked = reconcile_delegate_report(
            tasks,
            "✅ Projet MoodForge créé avec tous les fichiers — index.html, app.js, style.css",
            iteration=2,
        )
        assert marked == 1
        assert tasks[0].completed

    def test_reconcile_skips_verify_task(self):
        from src.reasoning.react_config import TaskItem
        tasks = [
            # Description avec 2+ mots longs pour assurer le match (>4 chars)
            TaskItem(description="Créer le projet MoodForge complet"),
            TaskItem(description="Vérifier que tout est fonctionnel"),
        ]
        obs = "✅ Projet MoodForge créé complet avec succès, fonctionnel et opérationnel"
        marked = reconcile_delegate_report(tasks, obs, iteration=1)
        assert tasks[0].completed  # création OK (moodforge + complet = 2+ mots matchés)
        assert not tasks[1].completed  # vérification bloquée par verify-gate

    def test_reconcile_returns_zero_no_match(self):
        from src.reasoning.react_config import TaskItem
        tasks = [TaskItem(description="Configurer les paramètres réseau avancés")]
        marked = reconcile_delegate_report(tasks, "✅ Terminé", iteration=1)
        assert marked == 0
        assert not tasks[0].completed

    def test_reconcile_already_completed_not_double_counted(self):
        from src.reasoning.react_config import TaskItem
        task = TaskItem(description="Créer le projet MoodForge avec structure")
        task.completed = True
        tasks = [task]
        marked = reconcile_delegate_report(
            tasks, "✅ MoodForge créé avec structure complète", iteration=2
        )
        assert marked == 0  # déjà complétée, non recomptée
