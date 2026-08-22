"""Routage des tests pytest : venv Lumena local, jamais le Docker jetable.

cf. run budgeto 2026-07-01 — un worker de mission est resté bloqué ~15 itérations
sur `No module named pytest` parce que `python -m pytest` partait dans un Docker
éphémère sans pytest, et le fallback local ne se déclenchait pas.
"""

import src.utils.docker_sandbox as ds


# ── is_python_test_command ────────────────────────────────────────────────────

def test_is_python_test_command_matches():
    assert ds.is_python_test_command("pytest test_x.py -v")
    assert ds.is_python_test_command("python -m pytest tests/")
    assert ds.is_python_test_command("python3 -m pytest tests/ -q")
    assert ds.is_python_test_command("py -m pytest test_x.py")
    assert ds.is_python_test_command("python.exe -m pytest test_x.py")
    # après un cd && (cas fréquent des workers)
    assert ds.is_python_test_command("cd workspace/budgeto && pytest test_budgeto.py -v")
    assert ds.is_python_test_command("cd X && python -m pytest tests/")


def test_is_python_test_command_rejects_non_tests():
    # étroit : pas de faux positifs
    assert not ds.is_python_test_command("python app.py")
    assert not ds.is_python_test_command('python -c "import budgeto"')
    assert not ds.is_python_test_command("pip install pytest")
    assert not ds.is_python_test_command("python -m pip install pytest")
    assert not ds.is_python_test_command("echo pytest")
    assert not ds.is_python_test_command("mypytest run")
    assert not ds.is_python_test_command("")


# ── should_use_sandbox : pytest reste local ───────────────────────────────────

def test_should_use_sandbox_pytest_always_local(monkeypatch):
    # Même en mode always, pytest ne doit PAS partir en Docker (image sans pytest).
    for mode in ("auto", "always"):
        monkeypatch.setenv("LUMENA_SANDBOX_MODE", mode)
        assert ds.should_use_sandbox("python -m pytest tests/") is False
        assert ds.should_use_sandbox("cd budgeto && pytest test_x.py -v") is False


def test_should_use_sandbox_normal_python_still_sandboxed(monkeypatch):
    # Un script Python normal continue de passer par le sandbox (isolation préservée).
    monkeypatch.setenv("LUMENA_SANDBOX_MODE", "auto")
    assert ds.should_use_sandbox("python app.py") is True
    # Une commande Windows reste locale (inchangé).
    assert ds.should_use_sandbox("dir") is False


# ── sandbox_error_needs_local_fallback : ciblé pytest, pas tous les modules ────

def test_fallback_triggers_on_pytest_missing():
    assert ds.sandbox_error_needs_local_fallback("/usr/local/bin/python: No module named pytest")
    assert ds.sandbox_error_needs_local_fallback("No module named 'pytest'")
    assert ds.sandbox_error_needs_local_fallback("bash: foo: command not found")
    assert ds.sandbox_error_needs_local_fallback("no such file or directory")


def test_fallback_ignores_real_missing_project_deps():
    # Une vraie dépendance projet manquante NE doit PAS déclencher le fallback local
    # (sinon Lumena validerait un projet incomplet grâce aux libs de l'hôte).
    assert not ds.sandbox_error_needs_local_fallback("ModuleNotFoundError: No module named 'flask'")
    assert not ds.sandbox_error_needs_local_fallback("No module named numpy")
    assert not ds.sandbox_error_needs_local_fallback("")
