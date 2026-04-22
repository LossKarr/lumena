"""Tests pour le système Plan TODO intégré à la boucle ReAct."""
import pytest
from src.reasoning.react import (
    TaskItem,
    ReActLoop,
    _PLAN_RE,
    _TASK_LINE_RE,
    _TOOL_COMPLETION_HINTS,
)


# ── Tests _parse_plan ──────────────────────────────────────────────


class TestParsePlan:
    """Tests unitaires de _parse_plan()."""

    def _make_loop(self):
        loop = object.__new__(ReActLoop)
        loop._task_plan = []
        loop._plan_emitted = False
        loop._iterations_without_progress = 0
        loop._last_completed_task_count = 0
        return loop

    def test_parse_basic_plan(self):
        raw = (
            "THOUGHT: Je dois modifier le header.\n\n"
            "PLAN:\n"
            "- [ ] Lire le fichier style.css\n"
            "- [ ] Modifier la couleur du header\n"
            "- [ ] Vérifier le changement\n\n"
            "ACTION: read_file\n"
            "ACTION_INPUT: {\"path\": \"style.css\"}\n"
        )
        loop = self._make_loop()
        tasks = loop._parse_plan(raw)
        assert len(tasks) == 3
        assert tasks[0].description == "Lire le fichier style.css"
        assert tasks[1].description == "Modifier la couleur du header"
        assert tasks[2].description == "Vérifier le changement"
        assert all(not t.completed for t in tasks)

    def test_parse_plan_with_completed(self):
        raw = (
            "THOUGHT: OK\n\n"
            "PLAN:\n"
            "- [x] Lire le fichier\n"
            "- [ ] Modifier le fichier\n\n"
            "ACTION: edit_file\n"
            "ACTION_INPUT: {}\n"
        )
        loop = self._make_loop()
        tasks = loop._parse_plan(raw)
        assert len(tasks) == 2
        assert tasks[0].completed is True
        assert tasks[1].completed is False

    def test_parse_plan_absent(self):
        raw = (
            "THOUGHT: Simple réponse.\n"
            "ACTION: FINAL\n"
            "ACTION_INPUT: Bonjour\n"
        )
        loop = self._make_loop()
        tasks = loop._parse_plan(raw)
        assert tasks == []

    def test_parse_plan_max_8_tasks(self):
        lines = "\n".join(f"- [ ] Tache {i}" for i in range(12))
        raw = f"THOUGHT: Beaucoup.\n\nPLAN:\n{lines}\n\nACTION: FINAL\nACTION_INPUT: ok\n"
        loop = self._make_loop()
        tasks = loop._parse_plan(raw)
        assert len(tasks) == 8

    def test_parse_plan_case_insensitive(self):
        raw = "THOUGHT: ok\n\nplan:\n- [ ] Step one\n- [X] Step two\n\nACTION: FINAL\nACTION_INPUT: done\n"
        loop = self._make_loop()
        tasks = loop._parse_plan(raw)
        assert len(tasks) == 2
        assert tasks[1].completed is True


# ── Tests _update_plan_progress ────────────────────────────────────


class TestUpdatePlanProgress:
    """Tests unitaires de _update_plan_progress()."""

    def _make_loop(self, descriptions):
        loop = object.__new__(ReActLoop)
        loop._task_plan = [TaskItem(description=d) for d in descriptions]
        loop._plan_emitted = True
        loop._iterations_without_progress = 0
        loop._last_completed_task_count = 0
        loop._plan_last_emit_state = ""
        loop._last_auto_advance_iter = -1
        return loop

    def test_hint_match_read_file(self):
        loop = self._make_loop([
            "Lire le fichier core.py",
            "Modifier la fonction X",
        ])
        loop._update_plan_progress("read_file", {"path": "core.py"}, "OK", 1)
        assert loop._task_plan[0].completed is True
        assert loop._task_plan[0].completed_by_tool == "read_file"
        assert loop._task_plan[1].completed is False

    def test_arg_match_by_filename(self):
        loop = self._make_loop([
            "Examiner le fichier react.py",
            "Corriger le bug",
        ])
        loop._update_plan_progress("read_file", {"path": "src/reasoning/react.py"}, "OK", 1)
        assert loop._task_plan[0].completed is True

    def test_tool_name_in_description(self):
        loop = self._make_loop([
            "Faire un screenshot de la page",
            "Envoyer par email",
        ])
        loop._update_plan_progress("screenshot", {}, "OK", 1)
        assert loop._task_plan[0].completed is True

    def test_one_task_per_iteration(self):
        loop = self._make_loop([
            "Lire le fichier A",
            "Lire le fichier B",
        ])
        loop._update_plan_progress("read_file", {"path": "A"}, "OK", 1)
        assert loop._task_plan[0].completed is True
        assert loop._task_plan[1].completed is False

    def test_skip_already_completed(self):
        loop = self._make_loop([
            "Lire le fichier A",
            "Lire le fichier B",
        ])
        loop._task_plan[0].completed = True
        loop._update_plan_progress("read_file", {"path": "B"}, "OK", 2)
        assert loop._task_plan[1].completed is True
        assert loop._task_plan[1].completed_at_iteration == 2

    def test_no_match_no_change(self):
        loop = self._make_loop([
            "Envoyer un email",
        ])
        loop._update_plan_progress("read_file", {"path": "foo.py"}, "OK", 1)
        assert loop._task_plan[0].completed is False

    def test_empty_plan_no_error(self):
        loop = object.__new__(ReActLoop)
        loop._task_plan = []
        loop._update_plan_progress("read_file", {}, "OK", 1)  # Should not raise

    # ── Nouveaux tests: stems, fallback observation, outils manquants ──

    def test_stem_recherche_matches_deep_research(self):
        """'recherch' (stem) doit matcher 'recherche approfondie' dans la description."""
        loop = self._make_loop([
            "Effectuer une recherche approfondie sur la France",
            "Synthétiser les résultats",
        ])
        loop._update_plan_progress("deep_research", {"query": "France 2026"}, "OK sources", 1)
        assert loop._task_plan[0].completed is True
        assert loop._task_plan[0].completed_by_tool == "deep_research"

    def test_create_pdf_matches_creer_document(self):
        """create_pdf avec hint 'pdf' doit matcher 'Créer un document (PDF ou DOCX)'."""
        loop = self._make_loop([
            "Créer un document (PDF ou DOCX) avec ce rapport",
            "Envoyer le document via Telegram",
        ])
        loop._update_plan_progress("create_pdf", {"filename": "rapport.pdf"}, "✅ PDF créé", 1)
        assert loop._task_plan[0].completed is True

    def test_telegram_send_document_matches_envoyer(self):
        """telegram_send_document avec hint 'envoy' doit matcher 'Envoyer le document'."""
        loop = self._make_loop([
            "Envoyer le document via Telegram",
        ])
        loop._update_plan_progress("telegram_send_document", {"file_path": "r.pdf"}, "✅ envoyé", 1)
        assert loop._task_plan[0].completed is True

    def test_observation_fallback_success_with_tool_word(self):
        """Si aucun hint ne matche mais observation=✅ et mot du tool dans desc → match."""
        loop = self._make_loop([
            "Préparer un document PDF final",
        ])
        # 'create_pdf' → words ['create', 'pdf'] → 'pdf' in desc → match
        loop._update_plan_progress("create_pdf", {}, "✅ PDF créé avec succès", 1)
        assert loop._task_plan[0].completed is True

    def test_observation_fallback_no_success_no_match(self):
        """Sans indicateur de succès dans l'observation, le fallback ne matche pas."""
        loop = self._make_loop([
            "Préparer un PDF final",
        ])
        loop._update_plan_progress("unknown_tool", {}, "Erreur: timeout", 1)
        assert loop._task_plan[0].completed is False

    def test_list_directory_matches_lister(self):
        """list_directory doit matcher 'Lister les dossiers'."""
        loop = self._make_loop([
            "Lister les dossiers dans workspace",
        ])
        loop._update_plan_progress("list_directory", {"path": "workspace"}, "📂 contenu", 1)
        assert loop._task_plan[0].completed is True

    def test_filename_arg_match(self):
        """Le paramètre 'filename' doit aussi être vérifié pour l'arg_match."""
        loop = self._make_loop([
            "Générer rapport_france.pdf",
        ])
        loop._update_plan_progress("create_pdf", {"filename": "rapport_france.pdf"}, "OK", 1)
        assert loop._task_plan[0].completed is True

    # ── Tests browser interaction tools (P1/P3 fix) ──

    def test_browser_dom_state_matches_analyser_page(self):
        """browser_dom_state hint 'analys' doit matcher 'Analyser la page'."""
        loop = self._make_loop([
            "Analyser la page pour trouver le lien d'inscription",
            "Cliquer sur le bouton d'inscription",
        ])
        loop._update_plan_progress("browser_dom_state", {}, "Page: 37 elements", 1)
        assert loop._task_plan[0].completed is True
        assert loop._task_plan[0].completed_by_tool == "browser_dom_state"

    def test_browser_click_index_matches_cliquer(self):
        """browser_click_index hint 'cliqu' doit matcher 'Cliquer pour accéder'."""
        loop = self._make_loop([
            "Cliquer pour accéder au formulaire d'inscription",
            "Remplir le formulaire",
        ])
        loop._update_plan_progress("browser_click_index", {"index": 2}, "✅ Clic sur [2]", 1)
        assert loop._task_plan[0].completed is True

    def test_browser_type_index_matches_remplir(self):
        """browser_type_index hint 'rempli' doit matcher 'Remplir le formulaire'."""
        loop = self._make_loop([
            "Remplir le formulaire avec des informations",
            "Soumettre le formulaire",
        ])
        loop._update_plan_progress("browser_type_index", {"index": 7, "text": "Lumena"}, "✅ Tape", 1)
        assert loop._task_plan[0].completed is True

    def test_browser_start_matches_demarrer(self):
        """browser_start hint 'démarr' doit matcher 'Démarrer le navigateur'."""
        loop = self._make_loop([
            "Démarrer le navigateur contrôlé (Playwright)",
            "Naviguer vers le site",
        ])
        loop._update_plan_progress("browser_start", {}, "🌐 Navigateur démarré", 1)
        assert loop._task_plan[0].completed is True

    def test_browser_navigate_matches_naviguer(self):
        """browser_navigate hint 'navig' doit matcher 'Naviguer vers'."""
        loop = self._make_loop([
            "Naviguer vers https://example.com",
            "Analyser la page",
        ])
        loop._update_plan_progress("browser_navigate", {"url": "https://example.com"}, "✅ Navigué", 1)
        assert loop._task_plan[0].completed is True

    def test_browser_click_matches_soumettre(self):
        """browser_click_index hint 'soumett' doit matcher 'Soumettre le formulaire'."""
        loop = self._make_loop([
            "Soumettre le formulaire d'inscription",
        ])
        loop._update_plan_progress("browser_click_index", {"index": 3}, "✅ Clic sur [3] button", 1)
        assert loop._task_plan[0].completed is True

    def test_browser_dom_state_matches_verifier(self):
        """browser_dom_state hint 'vérifi' doit matcher 'Vérifier que le compte...'."""
        loop = self._make_loop([
            "Vérifier que le compte a bien été créé",
        ])
        loop._update_plan_progress("browser_dom_state", {}, "Page: Tableau de bord", 1)
        assert loop._task_plan[0].completed is True

    def test_wait_matches_attendre(self):
        """wait hint 'attend' doit matcher 'Attendre la confirmation'."""
        loop = self._make_loop([
            "Attendre le chargement de la page",
        ])
        loop._update_plan_progress("wait", {"seconds": 3}, "Attendu 3 secondes", 1)
        assert loop._task_plan[0].completed is True

    # ── Test browser auto-fallback (P9 fix) ──

    def test_browser_auto_fallback_with_checkmark(self):
        """Un outil browser_ avec ✅ doit matcher automatiquement même sans hint match."""
        loop = self._make_loop([
            "Effectuer une action complexe sur le site",
        ])
        # Aucun hint de browser_click_index ne matche "action complexe"
        # mais le fallback browser auto doit quand même matcher
        loop._update_plan_progress("browser_click_index", {"index": 5}, "✅ Clic sur [5] button", 1)
        assert loop._task_plan[0].completed is True

    def test_browser_auto_fallback_no_checkmark(self):
        """Sans ✅ dans l'observation, le fallback browser ne matche pas."""
        loop = self._make_loop([
            "Effectuer une action complexe sur le site",
        ])
        loop._update_plan_progress("browser_click_index", {"index": 5}, "Erreur: element non trouvé", 1)
        assert loop._task_plan[0].completed is False

    # ── Test plan guard hints structure ──

    def test_browser_tools_in_hints_dict(self):
        """Tous les outils browser doivent être présents dans _TOOL_COMPLETION_HINTS."""
        browser_tools = [
            "browser_navigate", "browser_dom_state", "browser_click_index",
            "browser_type_index", "browser_start", "browser_search_google",
            "browser_get_content",
        ]
        for tool in browser_tools:
            assert tool in _TOOL_COMPLETION_HINTS, f"{tool} manquant du dict"
        assert "wait" in _TOOL_COMPLETION_HINTS

    # ── Test losskarr.fr full scenario (P1 regression test) ──

    def test_losskarr_8task_plan_all_match(self):
        """Simule le plan 8 tâches losskarr.fr — toutes doivent matcher."""
        loop = self._make_loop([
            "Démarrer le navigateur contrôlé (Playwright)",
            "Naviguer vers https://example.com",
            "Analyser la page pour trouver le lien d'inscription",
            "Cliquer pour accéder au formulaire d'inscription",
            "Remplir le formulaire avec des informations",
            "Soumettre le formulaire",
            "Vérifier que le compte a bien été créé",
            "Prendre une capture d'écran pour preuve",
        ])
        actions = [
            ("browser_start", {}, "🌐 Navigateur démarré"),
            ("browser_navigate", {"url": "https://example.com"}, "✅ Navigué vers losskarr.fr"),
            ("browser_dom_state", {}, "Page: 37 elements"),
            ("browser_click_index", {"index": 2}, "✅ Clic sur [2] Inscription"),
            ("browser_type_index", {"index": 7, "text": "Lumena"}, "✅ Tape Lumena"),
            ("browser_click_index", {"index": 3}, "✅ Clic sur [3] Créer un compte"),
            ("browser_dom_state", {}, "Page: Tableau de bord - Bienvenue"),
            ("screenshot", {}, "✅ Screenshot capturé"),
        ]
        for i, (tool, args, obs) in enumerate(actions, 1):
            loop._update_plan_progress(tool, args, obs, i)
        completed = sum(1 for t in loop._task_plan if t.completed)
        assert completed == 8, f"Seulement {completed}/8 tâches complétées"


# ── Tests _parse_response avec PLAN ───────────────────────────────


class TestParseResponseWithPlan:
    """Vérifie que _parse_response() retire le PLAN avant parsing."""

    def test_plan_stripped_from_thought(self):
        raw = (
            "THOUGHT: Je dois lire le fichier.\n\n"
            "PLAN:\n"
            "- [ ] Lire style.css\n"
            "- [ ] Modifier le header\n\n"
            "ACTION: read_file\n"
            "ACTION_INPUT: {\"path\": \"style.css\"}\n"
        )
        # Just test the regex stripping
        cleaned = _PLAN_RE.sub("", raw)
        assert "PLAN:" not in cleaned
        assert "THOUGHT:" in cleaned
        assert "ACTION: read_file" in cleaned


# ── Tests _format_plan_section ─────────────────────────────────────


class TestFormatPlanSection:
    """Tests de _format_plan_section()."""

    def _make_loop(self, tasks):
        loop = object.__new__(ReActLoop)
        loop._task_plan = tasks
        return loop

    def test_empty_plan(self):
        loop = self._make_loop([])
        assert loop._format_plan_section() == ""

    def test_full_plan(self):
        tasks = [
            TaskItem(description="Lire le fichier", completed=True, completed_at_iteration=1),
            TaskItem(description="Modifier le code", completed=False),
        ]
        loop = self._make_loop(tasks)
        result = loop._format_plan_section()
        assert "1/2 fait" in result
        assert "[x] Lire le fichier" in result
        assert "[ ] Modifier le code" in result
        assert "REGLE:" in result


# ── Tests get_run_meta avec plan ──────────────────────────────────


class TestGetRunMetaPlan:
    """Vérifie que get_run_meta() expose le plan."""

    def _make_loop(self):
        loop = object.__new__(ReActLoop)
        loop._run_meta = {
            "agent_output_incomplete": False,
            "agent_output_warning": None,
            "agent_repair_attempts": 0,
            "agent_final_finish_reason": None,
        }
        loop._task_plan = []
        return loop

    def test_meta_without_plan(self):
        loop = self._make_loop()
        meta = loop.get_run_meta()
        assert "plan" not in meta

    def test_meta_with_plan(self):
        loop = self._make_loop()
        loop._task_plan = [
            TaskItem(description="Task A", completed=True, completed_at_iteration=1),
            TaskItem(description="Task B", completed=False),
        ]
        meta = loop.get_run_meta()
        assert "plan" in meta
        assert meta["plan"]["total_tasks"] == 2
        assert meta["plan"]["completed_tasks"] == 1
        assert len(meta["plan"]["tasks"]) == 2
        assert meta["plan"]["tasks"][0]["completed"] is True
        assert meta["plan"]["tasks"][1]["completed"] is False


# ── Tests regex ────────────────────────────────────────────────────


class TestPlanRegex:
    """Tests des regex _PLAN_RE et _TASK_LINE_RE."""

    def test_plan_re_matches(self):
        text = "PLAN:\n- [ ] Step 1\n- [x] Step 2\n"
        m = _PLAN_RE.search(text)
        assert m is not None
        assert "Step 1" in m.group(1)

    def test_task_line_re(self):
        text = "- [ ] Pending task\n- [x] Done task\n- [X] Also done\n"
        matches = list(_TASK_LINE_RE.finditer(text))
        assert len(matches) == 3
        assert matches[0].group(1) == " "
        assert matches[0].group(2) == "Pending task"
        assert matches[1].group(1) == "x"
        assert matches[2].group(1) == "X"

    def test_plan_re_no_match_without_tasks(self):
        text = "PLAN:\nJust some text"
        m = _PLAN_RE.search(text)
        assert m is None

    def test_tool_completion_hints_structure(self):
        assert isinstance(_TOOL_COMPLETION_HINTS, dict)
        assert "read_file" in _TOOL_COMPLETION_HINTS
        assert "edit_file" in _TOOL_COMPLETION_HINTS
        for key, val in _TOOL_COMPLETION_HINTS.items():
            assert isinstance(val, list)
            assert len(val) > 0


# ── Tests clear_history ────────────────────────────────────────────


class TestClearHistoryPlan:
    """Vérifie que clear_history() reset le plan."""

    def test_clear_resets_plan(self):
        loop = object.__new__(ReActLoop)
        loop.history = []
        loop.action_history = []
        loop._task_plan = [TaskItem(description="Test")]
        loop._plan_emitted = True
        loop._iterations_without_progress = 5
        loop._last_completed_task_count = 3
        loop.clear_history()
        assert loop._task_plan == []
        assert loop._plan_emitted is False
        assert loop._iterations_without_progress == 0
        assert loop._last_completed_task_count == 0
