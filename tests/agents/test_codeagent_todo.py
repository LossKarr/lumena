from src.agents.codeagent_todo import CodeAgentTodoState


def test_todo_starts_from_plan_and_advances():
    todo = CodeAgentTodoState()
    rendered = todo.set_plan(["Creer app.py", "Verifier les tests"])
    assert "[in_progress] Creer app.py" in rendered
    assert "[pending] Verifier les tests" in rendered

    updated = todo.observe_action("write_file", path="app.py", success=True)
    assert "[completed] Creer app.py" in updated
    assert "[in_progress] Verifier les tests" in updated


def test_todo_marks_blocked_on_failed_action():
    todo = CodeAgentTodoState()
    todo.set_plan(["Modifier app.py"])
    updated = todo.observe_action("edit_lines", path="app.py", success=False)
    assert "[blocked] Modifier app.py" in updated


def test_todo_done_completes_remaining_items():
    todo = CodeAgentTodoState()
    todo.set_plan(["Creer app.py", "Verifier"])
    todo.observe_action("done", success=True)
    rendered = todo.render(changed_only=False)
    assert "[completed] Creer app.py" in rendered
    assert "[completed] Verifier" in rendered

