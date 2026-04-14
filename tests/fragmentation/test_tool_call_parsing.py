"""
Phase 5.2 — Tests d'intégration du parsing des réponses LLM multi-provider.

Vérifie que _parse_response() et _parse_action_args() extraient correctement
THOUGHT, ACTION, ACTION_INPUT pour les réponses typiques de chaque provider :
- DeepSeek V3
- Kimi K2.5
- Claude
- Gemini
- Ollama local (petits modèles)
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ── Setup path ─────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.reasoning.react import (
    Action,
    ActionType,
    ReActLoop,
    Thought,
)


# ── Fixtures ───────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def loop():
    """Crée un ReActLoop minimal pour tester le parsing."""
    mock_tools = MagicMock()
    mock_tools.tools = {}  # dict vide pour pas de lookup
    mock_tools.lumena = None
    loop = object.__new__(ReActLoop)
    loop.tools = mock_tools
    loop.history = []
    loop.max_iterations = 5
    return loop


# ══════════════════════════════════════════════════════════════════════════
# Section 1: FORMAT STANDARD (THOUGHT/ACTION/ACTION_INPUT)
# ══════════════════════════════════════════════════════════════════════════

class TestParseStandardFormat:
    """Tests du format ReAct standard bien formé."""

    def test_simple_tool_call(self, loop):
        """Format classique avec une action simple."""
        response = (
            "THOUGHT: Je dois lire le fichier demandé.\n"
            "ACTION: read_file\n"
            'ACTION_INPUT: {"path": "src/main.py"}'
        )
        thought, action = loop._parse_response(response)
        assert thought.content == "Je dois lire le fichier demandé."
        assert action.action_type == ActionType.TOOL_CALL
        assert action.tool_name == "read_file"
        assert action.tool_args == {"path": "src/main.py"}

    def test_final_answer(self, loop):
        """ACTION: FINAL retourne une FINAL_ANSWER."""
        response = (
            "THOUGHT: Voici la réponse.\n"
            "ACTION: FINAL\n"
            "ACTION_INPUT: Le fichier contient 42 lignes."
        )
        thought, action = loop._parse_response(response)
        assert action.action_type == ActionType.FINAL_ANSWER
        assert "42 lignes" in action.answer

    def test_final_answer_unwraps_json_response(self, loop):
        """ACTION: FINAL avec JSON {"response":"..."} doit unwrapper le texte."""
        response = (
            'THOUGHT: Je réponds.\n'
            'ACTION: FINAL\n'
            'ACTION_INPUT: {"response":"Salut ! Je suis l\u00e0."}'
        )
        thought, action = loop._parse_response(response)
        assert action.action_type == ActionType.FINAL_ANSWER
        assert action.answer == "Salut ! Je suis l\u00e0."
        assert "{" not in action.answer

    def test_final_answer_keeps_multikey_json(self, loop):
        """ACTION: FINAL avec JSON multi-clés ne doit PAS unwrapper."""
        response = (
            'THOUGHT: Résultat.\n'
            'ACTION: FINAL\n'
            'ACTION_INPUT: {"name":"test","value":42}'
        )
        thought, action = loop._parse_response(response)
        assert action.action_type == ActionType.FINAL_ANSWER
        assert "{" in action.answer  # garde le JSON tel quel

    def test_nested_json_action_input(self, loop):
        """ACTION_INPUT avec JSON imbriqué."""
        inner = json.dumps({"selector": "#btn-submit", "by": "css"})
        response = (
            "THOUGHT: Click sur le bouton.\n"
            "ACTION: browser_click\n"
            f"ACTION_INPUT: {inner}"
        )
        thought, action = loop._parse_response(response)
        assert action.tool_name == "browser_click"
        assert action.tool_args["selector"] == "#btn-submit"
        assert action.tool_args["by"] == "css"

    def test_action_input_with_multiline_content(self, loop):
        """ACTION_INPUT avec du contenu multi-ligne (write_file)."""
        content = "line1\nline2\nline3"
        args = json.dumps({"path": "test.txt", "content": content})
        response = (
            "THOUGHT: Créer le fichier.\n"
            "ACTION: write_file\n"
            f"ACTION_INPUT: {args}"
        )
        thought, action = loop._parse_response(response)
        assert action.tool_name == "write_file"
        assert action.tool_args["path"] == "test.txt"
        assert "line2" in action.tool_args["content"]

    def test_empty_action_input(self, loop):
        """ACTION_INPUT vide pour des outils sans paramètres."""
        response = (
            "THOUGHT: Vérifier l'heure.\n"
            "ACTION: get_time\n"
            "ACTION_INPUT: {}"
        )
        thought, action = loop._parse_response(response)
        assert action.tool_name == "get_time"
        assert action.tool_args == {}


# ══════════════════════════════════════════════════════════════════════════
# Section 2: DEEPSEEK V3 — format propre, parfois multi-lignes
# ══════════════════════════════════════════════════════════════════════════

class TestParseDeepSeek:
    """Réponses typiques de DeepSeek V3."""

    def test_deepseek_clean_format(self, loop):
        """DeepSeek produit généralement un format très propre."""
        response = (
            "THOUGHT: L'utilisateur veut naviguer vers leboncoin.fr. "
            "Je vais ouvrir le navigateur avec browser_open.\n"
            "ACTION: browser_open\n"
            'ACTION_INPUT: {"url": "https://www.leboncoin.fr"}'
        )
        thought, action = loop._parse_response(response)
        assert action.tool_name == "browser_open"
        assert action.tool_args["url"] == "https://www.leboncoin.fr"

    def test_deepseek_multiline_thought(self, loop):
        """DeepSeek écrit parfois des THOUGHT sur plusieurs lignes."""
        response = (
            "THOUGHT: L'utilisateur demande de chercher des annonces.\n"
            "Je dois d'abord ouvrir le navigateur, puis naviguer vers le site.\n"
            "Commençons par ouvrir Chrome.\n"
            "ACTION: browser_open\n"
            'ACTION_INPUT: {"url": "https://www.google.com"}'
        )
        thought, action = loop._parse_response(response)
        # Le thought doit contenir le texte multi-ligne
        assert "annonces" in thought.content
        assert action.tool_name == "browser_open"

    def test_deepseek_complex_json(self, loop):
        """DeepSeek avec JSON complexe (array dans les args)."""
        response = (
            "THOUGHT: Exécuter les outils en parallèle.\n"
            "ACTION: parallel_tools\n"
            'ACTION_INPUT: {"tool_calls": [{"name": "get_time", "args": {}}, '
            '{"name": "read_file", "args": {"path": "README.md"}}]}'
        )
        thought, action = loop._parse_response(response)
        assert action.tool_name == "parallel_tools"
        assert isinstance(action.tool_args["tool_calls"], list)
        assert len(action.tool_args["tool_calls"]) == 2


# ══════════════════════════════════════════════════════════════════════════
# Section 3: CLAUDE — format propre mais THOUGHT parfois très long
# ══════════════════════════════════════════════════════════════════════════

class TestParseClaude:
    """Réponses typiques de Claude."""

    def test_claude_verbose_thought(self, loop):
        """Claude produit souvent des THOUGHT très détaillés."""
        response = (
            "THOUGHT: L'utilisateur me demande de lire un fichier Python. "
            "Je vais utiliser l'outil read_file pour accéder au contenu du fichier. "
            "Il est important de vérifier que le chemin existe avant de procéder, "
            "mais l'outil gèrera cette erreur naturellement. "
            "Procédons à la lecture.\n"
            "ACTION: read_file\n"
            'ACTION_INPUT: {"path": "src/reasoning/react.py", "start_line": 1, "end_line": 50}'
        )
        thought, action = loop._parse_response(response)
        assert action.tool_name == "read_file"
        assert action.tool_args["path"] == "src/reasoning/react.py"
        assert action.tool_args["start_line"] == 1
        assert action.tool_args["end_line"] == 50

    def test_claude_final_with_markdown(self, loop):
        """Claude met souvent du markdown dans sa réponse finale."""
        response = (
            "THOUGHT: J'ai trouvé l'information demandée.\n"
            "ACTION: FINAL\n"
            "ACTION_INPUT: Voici le résultat:\n\n"
            "```python\ndef hello():\n    print('Hello')\n```\n\n"
            "Ce code affiche 'Hello' à l'écran."
        )
        thought, action = loop._parse_response(response)
        assert action.action_type == ActionType.FINAL_ANSWER
        assert "```python" in action.answer
        assert "Hello" in action.answer


# ══════════════════════════════════════════════════════════════════════════
# Section 4: GEMINI — parfois du texte avant/après le format structuré
# ══════════════════════════════════════════════════════════════════════════

class TestParseGemini:
    """Réponses typiques de Gemini."""

    def test_gemini_text_before_thought(self, loop):
        """Gemini ajoute parfois du texte avant THOUGHT."""
        response = (
            "Bien sûr, laissez-moi vérifier cela.\n\n"
            "THOUGHT: Je vais lister les fichiers du répertoire.\n"
            "ACTION: list_directory\n"
            'ACTION_INPUT: {"path": "src/"}'
        )
        thought, action = loop._parse_response(response)
        assert action.tool_name == "list_directory"
        assert action.tool_args["path"] == "src/"

    def test_gemini_text_after_action(self, loop):
        """Gemini ajoute parfois du texte après l'action."""
        response = (
            "THOUGHT: Lecture du fichier.\n"
            "ACTION: read_file\n"
            'ACTION_INPUT: {"path": "config.json"}\n\n'
            "Je vais maintenant analyser le contenu."
        )
        thought, action = loop._parse_response(response)
        assert action.tool_name == "read_file"
        assert action.tool_args["path"] == "config.json"

    def test_gemini_extra_whitespace(self, loop):
        """Gemini ajoute des espaces excessifs."""
        response = (
            "  THOUGHT:   Je recherche les fichiers Python.  \n"
            "  ACTION:   find_files  \n"
            '  ACTION_INPUT:   {"pattern": "*.py"}  '
        )
        thought, action = loop._parse_response(response)
        assert action.tool_name == "find_files"
        assert action.tool_args["pattern"] == "*.py"


# ══════════════════════════════════════════════════════════════════════════
# Section 5: OLLAMA LOCAL — format souvent cassé (petits modèles)
# ══════════════════════════════════════════════════════════════════════════

class TestParseOllamaLocal:
    """Réponses typiques des modèles locaux Ollama (souvent malformées)."""

    def test_ollama_no_action_returns_final(self, loop):
        """Pas d'ACTION et pas de JSON → retourne FINAL_ANSWER."""
        response = "Voici la réponse à votre question: le fichier fait 42 lignes."
        thought, action = loop._parse_response(response)
        assert action.action_type == ActionType.FINAL_ANSWER

    def test_ollama_raw_json_with_path_triggers_write_file(self, loop):
        """JSON brut avec path/content → fallback write_file (modèles faibles)."""
        response = '{"path": "test.txt", "content": "hello world"}'
        thought, action = loop._parse_response(response)
        assert action.action_type == ActionType.TOOL_CALL
        assert action.tool_name == "write_file"
        assert action.tool_args["path"] == "test.txt"
        assert action.tool_args["content"] == "hello world"

    def test_ollama_raw_json_without_path_returns_final(self, loop):
        """JSON brut SANS path ni content → FINAL_ANSWER (pas de fallback)."""
        response = '{"status": "ok", "count": 42}'
        thought, action = loop._parse_response(response)
        assert action.action_type == ActionType.FINAL_ANSWER

    def test_ollama_raw_json_invalid_returns_final(self, loop):
        """JSON brut malformé → FINAL_ANSWER (pas de crash)."""
        response = '{"path": "test.txt", content: broken}'
        thought, action = loop._parse_response(response)
        assert action.action_type == ActionType.FINAL_ANSWER

    def test_ollama_partial_format(self, loop):
        """THOUGHT sans ACTION → réponse finale."""
        response = "THOUGHT: Je ne sais pas comment faire cela."
        thought, action = loop._parse_response(response)
        assert action.action_type == ActionType.FINAL_ANSWER
        assert "Je ne sais pas" in thought.content

    def test_ollama_thought_only_with_dots(self, loop):
        """THOUGHT: ... → doit gérer gracieusement."""
        response = "THOUGHT: ...\n"
        thought, action = loop._parse_response(response)
        # Doit tomber sur le fallback et pas crasher
        assert action.action_type == ActionType.FINAL_ANSWER

    def test_ollama_action_without_input(self, loop):
        """ACTION sans ACTION_INPUT → args vides."""
        response = (
            "THOUGHT: Vérifier l'heure.\n"
            "ACTION: get_time\n"
        )
        thought, action = loop._parse_response(response)
        assert action.tool_name == "get_time"
        assert action.tool_args == {}


# ══════════════════════════════════════════════════════════════════════════# Section 5b: run() — pas de message system d'identité (doublon)
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio(mode="auto")
class TestRunNoIdentitySystemMessage:
    """Vérifie que run() envoie seulement un message user (pas de system identity).
    
    Le backup fonctionnel n'avait pas de message system séparé.
    Le prompt ReAct contient déjà 'Tu es LUMENA...'.
    """

    @pytest.fixture
    def loop_with_captured_messages(self):
        """Crée un ReActLoop qui capture les messages envoyés au LLM."""
        captured = []
        async def mock_llm(messages, **kwargs):
            captured.append(messages)
            return "THOUGHT: Done.\nACTION: FINAL\nACTION_INPUT: Terminé."
        loop = ReActLoop(tools={}, llm_chat_func=mock_llm)
        loop._captured = captured
        return loop

    async def test_no_system_message_in_run(self, loop_with_captured_messages):
        """run() doit envoyer [{'role':'user', ...}] sans message system."""
        loop = loop_with_captured_messages
        await loop.run("Bonjour")
        assert len(loop._captured) >= 1
        first_call = loop._captured[0]
        assert len(first_call) == 1, f"Attendu 1 message, reçu {len(first_call)}"
        assert first_call[0]["role"] == "user"

    async def test_prompt_contains_lumena_identity(self, loop_with_captured_messages):
        """Le prompt user contient déjà l'identité LUMENA."""
        loop = loop_with_captured_messages
        await loop.run("Bonjour")
        prompt_content = loop._captured[0][0]["content"]
        assert "LUMENA" in prompt_content


# ══════════════════════════════════════════════════════════════════════# Section 6: _parse_action_args() — parsing robuste des arguments
# ══════════════════════════════════════════════════════════════════════════

class TestParseActionArgs:
    """Tests spécifiques pour _parse_action_args()."""

    def test_valid_json(self, loop):
        assert loop._parse_action_args('{"path": "test.py"}') == {"path": "test.py"}

    def test_empty_string(self, loop):
        assert loop._parse_action_args("") == {}

    def test_empty_json(self, loop):
        assert loop._parse_action_args("{}") == {}

    def test_json_with_whitespace(self, loop):
        result = loop._parse_action_args('  {"path":  "test.py" }  ')
        assert result == {"path": "test.py"}

    def test_json_with_integers(self, loop):
        result = loop._parse_action_args('{"start_line": 1, "end_line": 50}')
        assert result["start_line"] == 1
        assert result["end_line"] == 50

    def test_json_with_boolean(self, loop):
        result = loop._parse_action_args('{"recursive": true}')
        assert result["recursive"] is True

    def test_json_with_array(self, loop):
        result = loop._parse_action_args('{"tool_calls": [{"name": "get_time"}]}')
        assert isinstance(result["tool_calls"], list)

    def test_json_with_escaped_content(self, loop):
        """Contenu avec caractères échappés."""
        content = "line1\\nline2\\nline3"
        result = loop._parse_action_args(f'{{"content": "{content}"}}')
        assert "line1" in result["content"]

    def test_fallback_path_extraction(self, loop):
        """Extraction de path par regex quand le JSON est invalide."""
        result = loop._parse_action_args('path="src/main.py"')
        assert result.get("path") == "src/main.py"

    def test_fallback_file_extension_detection(self, loop):
        """Détection de fichier par extension."""
        result = loop._parse_action_args("README.md")
        assert result.get("path") == "README.md"

    def test_plain_text_becomes_input(self, loop):
        """Texte non-reconnu va dans 'input'."""
        result = loop._parse_action_args("something random")
        assert result.get("input") == "something random"


# ══════════════════════════════════════════════════════════════════════════
# Section 7: (supprimé — _detect_tool_context retiré, filtrage désactivé)
# ══════════════════════════════════════════════════════════════════════════


# ══════════════════════════════════════════════════════════════════════════
# Section 8: get_tools_description — format compact
# ══════════════════════════════════════════════════════════════════════════

class TestGetToolsDescriptionCompact:
    """Tests que get_tools_description produit un format compact 1-ligne."""

    @pytest.fixture
    def registry(self):
        from src.reasoning.react import ToolRegistry
        tr = object.__new__(ToolRegistry)
        tr._tools_desc_cache = None
        tr.tools = {
            "read_file": {
                "name": "read_file",
                "description": "Lire un fichier",
                "parameters": {"path": {"type": "string", "description": "Chemin"}},
                "required": ["path"],
            },
            "browser_open": {
                "name": "browser_open",
                "description": "Ouvrir le navigateur",
                "parameters": {"url": {"type": "string", "description": "URL"}},
                "required": ["url"],
            },
            "get_time": {
                "name": "get_time",
                "description": "Obtenir l'heure",
                "parameters": {},
                "required": [],
            },
        }
        return tr

    def test_shows_all_tools(self, registry):
        desc = registry.get_tools_description()
        assert "read_file" in desc
        assert "browser_open" in desc
        assert "get_time" in desc

    def test_compact_format_one_line_per_tool(self, registry):
        desc = registry.get_tools_description()
        lines = [l for l in desc.strip().split("\n") if l.strip()]
        assert len(lines) == 3  # 1 ligne par outil

    def test_params_in_signature(self, registry):
        desc = registry.get_tools_description()
        assert "read_file(path)" in desc
        assert "browser_open(url)" in desc
        assert "get_time()" in desc

    def test_optional_param_marker(self):
        from src.reasoning.react import ToolRegistry
        tr = object.__new__(ToolRegistry)
        tr._tools_desc_cache = None
        tr.tools = {
            "search": {
                "name": "search",
                "description": "Rechercher",
                "parameters": {"query": {"type": "string"}, "limit": {"type": "integer"}},
                "required": ["query"],
            },
        }
        desc = tr.get_tools_description()
        assert "search(query, limit?)" in desc


# ══════════════════════════════════════════════════════════════════════════
# Section 9: Tests de robustesse (edge cases)
# ══════════════════════════════════════════════════════════════════════════

class TestParseEdgeCases:
    """Edge cases du parsing."""

    def test_multiple_thoughts(self, loop):
        """Plusieurs THOUGHT — le dernier gagne."""
        response = (
            "THOUGHT: Première réflexion.\n"
            "THOUGHT: Deuxième réflexion, plus précise.\n"
            "ACTION: get_time\n"
            "ACTION_INPUT: {}"
        )
        thought, action = loop._parse_response(response)
        assert "Deuxième" in thought.content

    def test_multiple_actions(self, loop):
        """Plusieurs ACTION — la première est exécutée, les suivantes en queue."""
        response = (
            "THOUGHT: Réflexion.\n"
            "ACTION: read_file\n"
            'ACTION_INPUT: {"path": "old.py"}\n'
            "THOUGHT: Non, plutôt écrire.\n"
            "ACTION: write_file\n"
            'ACTION_INPUT: {"path": "new.py", "content": "hello"}'
        )
        thought, action = loop._parse_response(response)
        assert action.tool_name == "read_file"
        # Les actions supplémentaires sont mises en queue
        pending = getattr(loop, '_pending_multi_actions', [])
        assert len(pending) == 1
        assert pending[0][0] == "write_file"

    def test_action_name_with_underscore(self, loop):
        """Les noms d'outils contiennent des underscores."""
        response = (
            "THOUGHT: OK.\n"
            "ACTION: browser_switch_tab\n"
            'ACTION_INPUT: {"tab_id": 2}'
        )
        thought, action = loop._parse_response(response)
        assert action.tool_name == "browser_switch_tab"

    def test_very_long_response(self, loop):
        """Réponse très longue ne crashe pas."""
        long_text = "a" * 10000
        response = (
            f"THOUGHT: {long_text}\n"
            "ACTION: FINAL\n"
            "ACTION_INPUT: Done."
        )
        thought, action = loop._parse_response(response)
        assert action.action_type == ActionType.FINAL_ANSWER

    def test_unicode_content(self, loop):
        """Contenu avec des caractères Unicode."""
        response = (
            "THOUGHT: Créer un fichier avec des émojis.\n"
            "ACTION: write_file\n"
            'ACTION_INPUT: {"path": "test.txt", "content": "Bonjour 🌟 café ☕"}'
        )
        thought, action = loop._parse_response(response)
        assert "🌟" in action.tool_args["content"]
        assert "☕" in action.tool_args["content"]


# ══════════════════════════════════════════════════════════════════════════
# Section 10: Phase 4.2 — Fuzzy matching
# ══════════════════════════════════════════════════════════════════════════

class TestFuzzyToolSuggestion:
    """Vérifie que les suggestions fuzzy fonctionnent."""

    @pytest.fixture
    def registry(self):
        from src.reasoning.react import ToolRegistry
        tr = object.__new__(ToolRegistry)
        tr.tools = {
            "browser_click": {"handler": None},
            "browser_open": {"handler": None},
            "browser_close_tab": {"handler": None},
            "read_file": {"handler": None},
            "write_file": {"handler": None},
        }
        tr._tool_modules = {}
        return tr

    @pytest.mark.asyncio
    async def test_misspelled_tool_suggests(self, registry):
        """browser_clic → suggestion browser_click."""
        import difflib
        close = difflib.get_close_matches("browser_clic", list(registry.tools.keys()), n=3, cutoff=0.5)
        assert "browser_click" in close

    @pytest.mark.asyncio
    async def test_typo_in_tool_name(self, registry):
        import difflib
        close = difflib.get_close_matches("read_fille", list(registry.tools.keys()), n=3, cutoff=0.5)
        assert "read_file" in close

    @pytest.mark.asyncio
    async def test_no_match_returns_empty(self, registry):
        import difflib
        close = difflib.get_close_matches("zzzzzzzzz", list(registry.tools.keys()), n=3, cutoff=0.5)
        assert close == []
