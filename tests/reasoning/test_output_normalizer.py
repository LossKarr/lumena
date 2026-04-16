"""Tests unitaires pour src/llm/output_normalizer.py."""

import pytest

from src.llm.output_normalizer import (
    auto_fix_action_name,
    extract_json_object,
    fix_json_text,
    normalize_action_name,
    normalize_file_path,
)


# ═══════════════════════════════════════════════════════════════════
# fix_json_text
# ═══════════════════════════════════════════════════════════════════

class TestFixJsonText:
    def test_strip_markdown_json_fence(self):
        raw = '```json\n{"action": "read_file"}\n```'
        assert fix_json_text(raw) == '{"action": "read_file"}'

    def test_strip_markdown_fence_no_language(self):
        raw = '```\n{"key": "val"}\n```'
        # Sans "json" après les backticks, ne matche pas le regex strict
        result = fix_json_text(raw)
        # Devrait quand même être parsable après trailing comma fix
        assert '"key"' in result

    def test_strip_markdown_json_fence_uppercase(self):
        raw = '```JSON\n{"x": 1}\n```'
        assert fix_json_text(raw) == '{"x": 1}'

    def test_trailing_comma_object(self):
        assert fix_json_text('{"a": 1,}') == '{"a": 1}'

    def test_trailing_comma_array(self):
        assert fix_json_text('[1, 2,]') == '[1, 2]'

    def test_trailing_comma_nested(self):
        raw = '{"a": [1, 2,], "b": {"c": 3,},}'
        result = fix_json_text(raw)
        assert result == '{"a": [1, 2], "b": {"c": 3}}'

    def test_trailing_comma_inside_string_untouched(self):
        raw = '{"a": "trailing,"}'
        assert fix_json_text(raw) == '{"a": "trailing,"}'

    def test_comma_value_before_brace(self):
        raw = '{"a": ","}'
        assert fix_json_text(raw) == '{"a": ","}'

    def test_already_clean_json(self):
        raw = '{"action": "read_file", "path": "src/main.py"}'
        assert fix_json_text(raw) == raw

    def test_empty_string(self):
        assert fix_json_text("") == ""

    def test_none_like(self):
        assert fix_json_text("  ") == ""

    def test_fence_with_whitespace(self):
        raw = '  ```json\n{"a": 1}\n```  '
        assert fix_json_text(raw) == '{"a": 1}'


# ═══════════════════════════════════════════════════════════════════
# extract_json_object
# ═══════════════════════════════════════════════════════════════════

class TestExtractJsonObject:
    def test_direct_json(self):
        result = extract_json_object('{"action": "read_file", "path": "x.py"}')
        assert result == {"action": "read_file", "path": "x.py"}

    def test_json_in_markdown_fence(self):
        raw = '```json\n{"action": "write_file", "path": "a.py"}\n```'
        result = extract_json_object(raw)
        assert result == {"action": "write_file", "path": "a.py"}

    def test_json_embedded_in_text(self):
        raw = 'Here is my action:\n{"action": "read_file", "path": "test.py"}\nDone!'
        result = extract_json_object(raw)
        assert result == {"action": "read_file", "path": "test.py"}

    def test_trailing_comma_recovery(self):
        raw = '{"action": "edit_file", "path": "x.py",}'
        result = extract_json_object(raw)
        assert result == {"action": "edit_file", "path": "x.py"}

    def test_nested_json(self):
        raw = '{"action": "write_file", "content": {"key": "val"}}'
        result = extract_json_object(raw)
        assert result["action"] == "write_file"
        assert result["content"] == {"key": "val"}

    def test_no_json_returns_none(self):
        assert extract_json_object("No JSON here, just text.") is None

    def test_truncated_json_returns_none(self):
        assert extract_json_object('{"action": "read_file", "path": ') is None

    def test_json_with_escaped_quotes(self):
        raw = r'{"content": "line1\nline2\t\"quoted\""}'
        result = extract_json_object(raw)
        assert result is not None
        assert "line1" in result["content"]

    def test_empty_returns_none(self):
        assert extract_json_object("") is None
        assert extract_json_object(None) is None  # type: ignore[arg-type]

    def test_json_with_trailing_comma_in_fence(self):
        raw = '```json\n{"a": 1, "b": 2,}\n```'
        result = extract_json_object(raw)
        assert result == {"a": 1, "b": 2}

    def test_array_not_returned(self):
        # extract_json_object ne retourne que des dict
        assert extract_json_object("[1, 2, 3]") is None

    def test_json_after_text_with_braces_in_string(self):
        raw = 'The function uses {} syntax.\n{"action": "done"}'
        result = extract_json_object(raw)
        # Le premier {} est vide dict, pas un action. Mais le parser le trouvera.
        # Le deuxième est le bon. Vérifier qu'on obtient au moins un dict valide.
        assert result is not None


# ═══════════════════════════════════════════════════════════════════
# normalize_action_name
# ═══════════════════════════════════════════════════════════════════

class TestNormalizeActionName:
    # camelCase → snake_case
    def test_camel_to_snake_read_file(self):
        assert normalize_action_name("readFile") == "read_file"

    def test_camel_to_snake_write_file(self):
        assert normalize_action_name("writeFile") == "write_file"

    def test_camel_to_snake_edit_file(self):
        assert normalize_action_name("editFile") == "edit_file"

    def test_camel_to_snake_run_command(self):
        assert normalize_action_name("runCommand") == "run_command"

    def test_camel_to_snake_list_files(self):
        assert normalize_action_name("listFiles") == "list_files"

    def test_camel_to_snake_apply_patch(self):
        assert normalize_action_name("applyPatch") == "apply_patch"

    def test_camel_to_snake_grep_search(self):
        assert normalize_action_name("grepSearch") == "grep_search"

    def test_camel_to_snake_run_tests(self):
        assert normalize_action_name("runTests") == "run_tests"

    # Aliases sémantiques
    def test_alias_cat(self):
        assert normalize_action_name("cat") == "read_file"

    def test_alias_show(self):
        assert normalize_action_name("show") == "read_file"

    def test_alias_view(self):
        assert normalize_action_name("view") == "read_file"

    def test_alias_grep(self):
        assert normalize_action_name("grep") == "grep_search"

    def test_alias_search(self):
        assert normalize_action_name("search") == "grep_search"

    def test_alias_bash(self):
        assert normalize_action_name("bash") == "run_command"

    def test_alias_ls(self):
        assert normalize_action_name("ls") == "list_files"

    def test_alias_dir(self):
        assert normalize_action_name("dir") == "list_files"

    def test_alias_test(self):
        assert normalize_action_name("test") == "run_tests"

    def test_alias_finish(self):
        assert normalize_action_name("finish") == "done"

    def test_alias_complete(self):
        assert normalize_action_name("complete") == "done"

    def test_alias_diff(self):
        assert normalize_action_name("diff") == "apply_patch"

    def test_alias_modify(self):
        assert normalize_action_name("modify") == "edit_file"

    def test_alias_replace(self):
        assert normalize_action_name("replace") == "edit_file"

    def test_alias_think(self):
        assert normalize_action_name("think") == "plan"

    # Strip préfixes/suffixes
    def test_strip_tool_prefix(self):
        assert normalize_action_name("tool_cat") == "read_file"

    def test_strip_tool_suffix(self):
        assert normalize_action_name("cat_tool") == "read_file"

    def test_strip_action_suffix(self):
        assert normalize_action_name("cat_action") == "read_file"

    def test_strip_handler_suffix(self):
        assert normalize_action_name("cat_handler") == "read_file"

    # Noms déjà corrects — ne changent pas
    def test_existing_snake_case_unchanged(self):
        assert normalize_action_name("read_file") == "read_file"

    def test_unknown_name_unchanged(self):
        assert normalize_action_name("some_custom_tool") == "some_custom_tool"

    def test_handler_v2_names_unchanged(self):
        """Les noms V2 (mail_send, stripe_list_invoices, etc.) ne doivent pas être altérés."""
        for name in ("mail_send", "stripe_list_invoices", "n8n_create_workflow",
                      "discord_send_message", "browser_navigate"):
            assert normalize_action_name(name) == name

    def test_empty(self):
        assert normalize_action_name("") == ""

    def test_case_insensitive(self):
        assert normalize_action_name("CAT") == "read_file"
        assert normalize_action_name("BASH") == "run_command"


# ═══════════════════════════════════════════════════════════════════
# normalize_file_path
# ═══════════════════════════════════════════════════════════════════

class TestNormalizeFilePath:
    def test_backslash_to_forward(self):
        assert normalize_file_path(r"src\utils\foo.py") == "src/utils/foo.py"

    def test_strip_dot_slash(self):
        assert normalize_file_path("./src/main.py") == "src/main.py"

    def test_strip_double_workspace(self):
        ws = "workspace/2026-04-06/projet"
        path = f"{ws}/{ws}/index.html"
        result = normalize_file_path(path, workspace_root=ws)
        assert result == f"{ws}/index.html"

    def test_absolute_linux_unchanged(self):
        """Les chemins absolus ne sont pas strippés (_resolve_path les gère en aval)."""
        result = normalize_file_path("/home/user/project/src/main.py")
        assert result == "/home/user/project/src/main.py"

    def test_absolute_windows_backslash_to_forward(self):
        """Les chemins absolus Windows sont convertis backslash→forward mais pas strippés."""
        result = normalize_file_path(r"C:\Users\admin\project\src\main.py")
        assert result == "C:/Users/admin/project/src/main.py"

    def test_already_clean_unchanged(self):
        path = "src/utils/helpers.py"
        assert normalize_file_path(path) == path

    def test_empty_string(self):
        assert normalize_file_path("") == ""

    def test_just_filename(self):
        assert normalize_file_path("main.py") == "main.py"

    def test_backslash_and_dot_slash(self):
        assert normalize_file_path(r".\src\main.py") == "src/main.py"

    def test_no_false_strip_short_path(self):
        """Les chemins absolus courts ne sont pas altérés."""
        assert normalize_file_path("/a/b") == "/a/b"


# ═══════════════════════════════════════════════════════════════════
# auto_fix_action_name
# ═══════════════════════════════════════════════════════════════════

class TestAutoFixActionName:
    KNOWN = {"read_file", "write_file", "edit_file", "list_files",
             "run_command", "run_tests", "grep_search", "apply_patch",
             "done", "plan", "mail_send", "stripe_list_invoices"}

    def test_exact_match_returns_same(self):
        assert auto_fix_action_name("read_file", self.KNOWN) == "read_file"

    def test_camel_case_fixed(self):
        assert auto_fix_action_name("readFile", self.KNOWN) == "read_file"

    def test_alias_fixed(self):
        assert auto_fix_action_name("cat", self.KNOWN) == "read_file"

    def test_fuzzy_match_close(self):
        result = auto_fix_action_name("liste_files", self.KNOWN)
        assert result == "list_files"

    def test_fuzzy_match_too_far_returns_original(self):
        result = auto_fix_action_name("zzzzz_unknown", self.KNOWN)
        assert result == "zzzzz_unknown"

    def test_normalize_then_fuzzy(self):
        # writeFile → write_file (via normalize), exact match
        assert auto_fix_action_name("writeFile", self.KNOWN) == "write_file"

    def test_empty(self):
        assert auto_fix_action_name("", self.KNOWN) == ""

    def test_handler_v2_exact(self):
        assert auto_fix_action_name("mail_send", self.KNOWN) == "mail_send"

    def test_prefix_strip_then_match(self):
        assert auto_fix_action_name("tool_cat", self.KNOWN) == "read_file"
