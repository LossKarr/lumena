from src.agents.codeagent_observation import classify_observation, compact_observation


def test_blocked_command_is_not_success():
    text = "Sous-commande bloquee: Executable 'if($?)' non autorise par la whitelist de securite."
    assert classify_observation(text) == "blocked"


def test_error_output_is_error():
    assert classify_observation("Traceback (most recent call last):\nNameError: x") == "error"


def test_compact_observation_prefixes_failed_status(monkeypatch):
    monkeypatch.setenv("LUMENA_CODEAGENT_OBSERVATION_COMPACT", "false")
    view = compact_observation("SyntaxError: unexpected token", action_type="run_command")
    assert view.status == "error"
    assert view.text.startswith("STATUT_OUTIL=ERROR")


def test_large_success_gets_head_tail(monkeypatch):
    monkeypatch.setenv("LUMENA_CODEAGENT_OBSERVATION_COMPACT", "false")
    text = "A" * 9000 + "TAIL"
    view = compact_observation(text, action_type="run_command", threshold=1000)
    assert view.status == "success"
    assert "chars omis" in view.text
    assert "TAIL" in view.text

