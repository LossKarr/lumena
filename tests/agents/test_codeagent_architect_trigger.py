from src.agents.sub_agent import _is_local_bugfix_task


def test_multi_file_runtime_error_is_not_local_bugfix(tmp_path):
    description = """
    Corrige les erreurs dans le projet minecraft3d:
    player.js:5 Uncaught ReferenceError: THREE is not defined
    engine.js:1 Uncaught SyntaxError: Identifier 'camera' has already been declared
    index.html charge les scripts du jeu.
    """

    assert not _is_local_bugfix_task(
        description,
        workspace_path=tmp_path,
        resolved_intent="modify",
    )


def test_single_file_error_remains_local_bugfix(tmp_path):
    assert _is_local_bugfix_task(
        "Corrige l'erreur de click dans button.js",
        workspace_path=tmp_path,
        resolved_intent="modify",
    )


def test_project_level_bug_without_file_uses_architect(tmp_path):
    assert not _is_local_bugfix_task(
        "Corrige le projet minecraft3d, le jeu ne marche pas",
        workspace_path=tmp_path,
        resolved_intent="modify",
    )
