"""
Tests de parité pour Phase 5:
  - skills.py (8 handlers)
  - agents.py (12 handlers)
  - mail.py (15 handlers)
  - documents.py (5 handlers)
  - spotify.py (8 handlers)
  - notion.py (7 handlers)

Total attendu : 55 handlers, aucune collision de noms entre modules.
"""

import pytest

from src.reasoning.handlers.skills import get_skills_handler_defs
from src.reasoning.handlers.agents import get_agents_handler_defs
from src.reasoning.handlers.mail import get_mail_handler_defs
from src.reasoning.handlers.documents import get_documents_handler_defs
from src.reasoning.handlers.spotify import get_spotify_handler_defs
from src.reasoning.handlers.notion import get_notion_handler_defs


# ─── Noms attendus (extraits de react.py) ──────────────────────────────────

EXPECTED_SKILLS_NAMES = [
    "read_own_code",
    "create_skill",
    "update_skill",
    "delete_skill",
    "list_skills",
    "pip_check",
    "search_in_code",
    "get_my_capabilities",
    "rollback",
    "list_backups",
    "execute_skill",
    "reload_skills",
    "sync_skills_main",
    "read_skill_reference",
    "edit_own_code",
    "run_tests",
]

EXPECTED_AGENTS_NAMES = [
    "delegate_task",
    "delegate_task_bg",
    "get_agents_status",
    "fork_analyze",
    "bg_start",
    "bg_status",
    "bg_list",
    "bg_cancel",
    "process_run",
    "process_status",
    "process_input",
    "process_kill",
    "process_list",
]

EXPECTED_MAIL_NAMES = [
    "mail_account_upsert",
    "mail_list_accounts",
    "mail_quick_test",
    "mail_list_messages",
    "mail_read_message",
    "mail_download_attachments",
    "mail_send",
    "mail_reply_message",
    "mail_delete_message",
    "mail_move_message",
    "mail_remove_account",
    "telegram_send_document",
    "send_whatsapp_message",
    "send_whatsapp_photo",
    "send_whatsapp_document",
    "send_whatsapp_audio",
    "send_critical_sms",
    "place_critical_call",
    "notify_critical",
    "mail_list_folders",
]

EXPECTED_DOCUMENTS_NAMES = [
    "create_pdf",
    "create_invoice_pdf",
    "create_docx",
    "create_xlsx",
    "create_pptx",
    "read_document",
    "generate_chart",
    "create_meeting_report",
    "html_to_pdf",
    "merge_pdfs",
    "split_pdf",
    "create_csv",
    "convert_document",
    "edit_docx",
    "edit_xlsx",
    "edit_pptx",
    "annotate_pdf",
    "create_from_template",
    "list_templates",
    "save_template",
    "add_watermark",
    "sign_document",
    "fill_pdf_form",
    "list_pdf_fields",
    "analyze_document",
    "compare_documents",
    "protect_pdf",
    "image_to_document",
    "create_markdown",
    "create_html",
    "create_email_html",
    "create_ics",
    "create_vcard",
    "batch_documents",
    "zip_documents",
    "assemble_document",
]

EXPECTED_SPOTIFY_NAMES = [
    "spotify_api_play",
    "spotify_pause",
    "spotify_resume",
    "spotify_next",
    "spotify_prev",
    "spotify_volume",
    "spotify_current",
    "spotify_queue",
]

EXPECTED_NOTION_NAMES = [
    "notion_search",
    "notion_read_page",
    "notion_create_page",
    "notion_update_page",
    "notion_list_databases",
    "notion_query_database",
    "notion_add_to_database",
]


# ─── Skills parity ─────────────────────────────────────────────────────────

class TestSkillsParity:
    def test_count(self):
        assert len(get_skills_handler_defs()) == 16

    def test_names_match(self):
        actual = [d.name for d in get_skills_handler_defs()]
        assert actual == EXPECTED_SKILLS_NAMES

    def test_all_callable(self):
        for d in get_skills_handler_defs():
            assert callable(d.handler)

    def test_all_have_description(self):
        for d in get_skills_handler_defs():
            assert d.description, f"{d.name} manque une description"

    @pytest.mark.parametrize("name", EXPECTED_SKILLS_NAMES)
    def test_handler_exists(self, name):
        names = {d.name for d in get_skills_handler_defs()}
        assert name in names, f"Handler manquant: {name}"


# ─── Agents parity ─────────────────────────────────────────────────────────

class TestAgentsParity:
    def test_count(self):
        assert len(get_agents_handler_defs()) == 13  # +1 delegate_task_bg

    def test_names_match(self):
        actual = [d.name for d in get_agents_handler_defs()]
        assert actual == EXPECTED_AGENTS_NAMES

    def test_all_callable(self):
        for d in get_agents_handler_defs():
            assert callable(d.handler)

    def test_all_have_description(self):
        for d in get_agents_handler_defs():
            assert d.description, f"{d.name} manque une description"

    @pytest.mark.parametrize("name", EXPECTED_AGENTS_NAMES)
    def test_handler_exists(self, name):
        names = {d.name for d in get_agents_handler_defs()}
        assert name in names, f"Handler manquant: {name}"


# ─── Mail parity ───────────────────────────────────────────────────────────

class TestMailParity:
    def test_count(self):
        assert len(get_mail_handler_defs()) == 20

    def test_names_match(self):
        actual = [d.name for d in get_mail_handler_defs()]
        assert actual == EXPECTED_MAIL_NAMES

    def test_all_callable(self):
        for d in get_mail_handler_defs():
            assert callable(d.handler)

    def test_all_have_description(self):
        for d in get_mail_handler_defs():
            assert d.description, f"{d.name} manque une description"

    @pytest.mark.parametrize("name", EXPECTED_MAIL_NAMES)
    def test_handler_exists(self, name):
        names = {d.name for d in get_mail_handler_defs()}
        assert name in names, f"Handler manquant: {name}"


# ─── Documents parity ─────────────────────────────────────────────────────

class TestDocumentsParity:
    def test_count(self):
        assert len(get_documents_handler_defs()) >= 8

    def test_names_match(self):
        actual = [d.name for d in get_documents_handler_defs()]
        assert set(EXPECTED_DOCUMENTS_NAMES) <= set(actual)

    def test_all_callable(self):
        for d in get_documents_handler_defs():
            assert callable(d.handler)

    def test_all_have_description(self):
        for d in get_documents_handler_defs():
            assert d.description, f"{d.name} manque une description"

    @pytest.mark.parametrize("name", EXPECTED_DOCUMENTS_NAMES)
    def test_handler_exists(self, name):
        names = {d.name for d in get_documents_handler_defs()}
        assert name in names, f"Handler manquant: {name}"


# ─── Spotify parity ───────────────────────────────────────────────────────

class TestSpotifyParity:
    def test_count(self):
        assert len(get_spotify_handler_defs()) == 8

    def test_names_match(self):
        actual = [d.name for d in get_spotify_handler_defs()]
        assert actual == EXPECTED_SPOTIFY_NAMES

    def test_all_callable(self):
        for d in get_spotify_handler_defs():
            assert callable(d.handler)

    def test_all_have_description(self):
        for d in get_spotify_handler_defs():
            assert d.description, f"{d.name} manque une description"

    @pytest.mark.parametrize("name", EXPECTED_SPOTIFY_NAMES)
    def test_handler_exists(self, name):
        names = {d.name for d in get_spotify_handler_defs()}
        assert name in names, f"Handler manquant: {name}"


# ─── Notion parity ─────────────────────────────────────────────────────────

class TestNotionParity:
    def test_count(self):
        assert len(get_notion_handler_defs()) == 7

    def test_names_match(self):
        actual = [d.name for d in get_notion_handler_defs()]
        assert actual == EXPECTED_NOTION_NAMES

    def test_all_callable(self):
        for d in get_notion_handler_defs():
            assert callable(d.handler)

    def test_all_have_description(self):
        for d in get_notion_handler_defs():
            assert d.description, f"{d.name} manque une description"

    @pytest.mark.parametrize("name", EXPECTED_NOTION_NAMES)
    def test_handler_exists(self, name):
        names = {d.name for d in get_notion_handler_defs()}
        assert name in names, f"Handler manquant: {name}"


# ─── Cross-module Phase 5 parity ──────────────────────────────────────────

class TestCrossModulePhase5Parity:
    def _all_defs(self):
        return (
            get_skills_handler_defs()
            + get_agents_handler_defs()
            + get_mail_handler_defs()
            + get_documents_handler_defs()
            + get_spotify_handler_defs()
            + get_notion_handler_defs()
        )

    def test_total_count(self):
        """14 + 12 + 16 + 8+ + 8 + 7 = 65+ handlers."""
        assert len(self._all_defs()) >= 65

    def test_no_name_collision(self):
        """Aucun nom ne doit être dupliqué entre les 6 modules Phase 5."""
        all_names = [d.name for d in self._all_defs()]
        assert len(all_names) == len(set(all_names)), (
            f"Noms en collision: {[n for n in all_names if all_names.count(n) > 1]}"
        )

    def test_no_collision_with_phases_1_to_4(self):
        """Les noms Phase 5 ne doivent pas entrer en collision avec Phases 1-4."""
        from src.reasoning.handlers.files import get_file_handler_defs
        from src.reasoning.handlers.system import get_system_handler_defs
        from src.reasoning.handlers.web import get_web_handler_defs
        from src.reasoning.handlers.memory import get_memory_handler_defs
        from src.reasoning.handlers.browser import get_browser_handler_defs
        from src.reasoning.handlers.computer_use import get_computer_use_handler_defs

        prior_names = set()
        for getter in [
            get_file_handler_defs, get_system_handler_defs,
            get_web_handler_defs, get_memory_handler_defs,
            get_browser_handler_defs, get_computer_use_handler_defs,
        ]:
            for d in getter():
                prior_names.add(d.name)

        phase5_names = {d.name for d in self._all_defs()}
        collision = prior_names & phase5_names
        assert not collision, f"Collision avec Phases 1-4: {collision}"
