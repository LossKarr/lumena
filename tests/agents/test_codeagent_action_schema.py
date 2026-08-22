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


# ── CA-1 (run démineur 2026-07-12) — forme ReAct wrappée sous args ────────────
# Le LLM émettait {"action": "str_replace", "args": {...}} → JSON valide, action
# connue, mais champs « manquants » → boucle de refus infinie, script.js jamais
# écrit. Le dépaquetage suit l'invariant du registre ReAct (_WRAPPER_KEYS).

def test_CA1_wrapped_args_is_unwrapped_and_valid():
    result = validate_codeagent_action({
        "action": "str_replace",
        "args": {"path": "script.js", "old_str": "// TODO", "new_str": "initGame();"},
    })
    assert result.valid
    assert result.action["path"] == "script.js"
    assert result.action["old_str"] == "// TODO"
    assert result.action["new_str"] == "initGame();"


def test_CA1_wrapped_parameters_and_input_also_unwrapped():
    for wrapper in ("parameters", "input", "arguments"):
        result = validate_codeagent_action({
            "action": "edit_lines",
            wrapper: {"path": "a.py", "start_line": "1", "end_line": "2", "content": "x = 1"},
        })
        assert result.valid, wrapper
        assert result.action["start_line"] == 1


def test_CA1_root_field_wins_over_wrapper():
    result = validate_codeagent_action({
        "action": "str_replace",
        "path": "racine.js",
        "args": {"path": "wrapper.js", "old_str": "a", "new_str": "b"},
    })
    assert result.valid
    assert result.action["path"] == "racine.js"  # la racine n'est JAMAIS écrasée


def test_CA1_flat_action_strictly_unchanged():
    result = validate_codeagent_action({
        "action": "write_file", "path": "a.py", "content": "print(1)",
    })
    assert result.valid
    assert result.action["path"] == "a.py"


def test_CA1_truly_missing_fields_still_rejected():
    # Wrapper présent mais champ réellement absent → refus conservé
    result = validate_codeagent_action({
        "action": "str_replace",
        "args": {"path": "script.js", "old_str": "// TODO"},  # new_str manquant
    })
    assert not result.valid
    assert "new_str" in result.message


def test_CA1_wrapper_non_dict_ignored():
    # "data" chaîne (contenu de fichier mal placé) → PAS un wrapper, refus normal
    result = validate_codeagent_action({
        "action": "write_file", "path": "a.py", "data": "print(1)",
    })
    assert not result.valid
    assert "content" in result.message

