from src.agents.codeagent_action_schema import validate_codeagent_action


def test_alias_tool_is_normalized():
    result = validate_codeagent_action({"tool": "read_file", "path": "src/app.py"})
    assert result.valid
    assert result.action["action"] == "read_file"


def test_missing_required_field_is_rejected():
    result = validate_codeagent_action({"action": "edit_lines", "path": "src/app.py", "start_line": 1})
    assert not result.valid
    assert "champ(s) requis" in result.message
    assert "end_line" in result.message


def test_edit_lines_numeric_fields_are_normalized():
    result = validate_codeagent_action({
        "action": "edit_lines",
        "path": "src/app.py",
        "start_line": "2",
        "end_line": "3",
        "content": "print('ok')",
    })
    assert result.valid
    assert result.action["start_line"] == 2
    assert result.action["end_line"] == 3


def test_unknown_action_suggests_close_name():
    result = validate_codeagent_action({"action": "read_fil", "path": "x.py"})
    assert not result.valid
    assert "Action inconnue" in result.message
    assert "read_file" in result.message


def test_apply_patches_requires_file_and_old():
    result = validate_codeagent_action({"action": "apply_patches", "patches": [{"file": "a.py"}]})
    assert not result.valid
    assert "file/old" in result.message

