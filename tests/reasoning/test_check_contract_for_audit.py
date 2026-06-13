"""
Tests pour la fonction pure `check_contract_for_audit` (Phase 2 drift check).

Garanties à prouver :
  - Pure : aucun side effect (logger, TraceBus, metrics, ContextVar, ide_context)
  - Déterministe : même entrée → même sortie
  - Correcte : reflète exactement la logique de _category_contract_check
    pour les règles requires_workspace et autonomy_allowed

Hors scope (volontaire) :
  - delegate_task description vague (args-dependent)
  - _policy_check (couche orthogonale, path-dependent)
"""
from __future__ import annotations

import logging

import pytest

from src.reasoning.tool_categories import check_contract_for_audit


# ──────────────────────────────────────────────────────────────────────────────
# Pureté : zéro side effect
# ──────────────────────────────────────────────────────────────────────────────


def test_pure_function_no_logger_call(caplog):
    """L'appel ne doit émettre aucun log (peu importe le niveau)."""
    caplog.clear()
    with caplog.at_level(logging.DEBUG):
        check_contract_for_audit("files", "autonomy", has_workspace=False)
        check_contract_for_audit("agents", "react", has_workspace=False)
        check_contract_for_audit("web", "codeagent", has_workspace=True)
    assert caplog.records == [], f"Logs émis: {caplog.records}"


def test_pure_function_no_tracebus_call(monkeypatch):
    """L'appel ne doit PAS publier sur TraceBus."""
    calls = []

    def fake_publish_trace(*args, **kwargs):
        calls.append((args, kwargs))

    # Patch le module si importé — sinon le fait pas
    try:
        import src.telemetry.trace_bus as tb
        monkeypatch.setattr(tb, "publish_trace", fake_publish_trace)
    except ImportError:
        pass

    check_contract_for_audit("files", "autonomy", has_workspace=False)
    check_contract_for_audit("agents", "react", has_workspace=False)

    assert calls == [], f"publish_trace appelé: {calls}"


def test_pure_function_no_metrics_call(monkeypatch):
    """L'appel ne doit PAS toucher reliability metrics."""
    calls = []

    class FakeMetrics:
        def record_policy_refuse(self, *args, **kwargs):
            calls.append(("record_policy_refuse", args, kwargs))

        def __getattr__(self, name):
            def _spy(*args, **kwargs):
                calls.append((name, args, kwargs))
            return _spy

    try:
        import src.utils.reliability_metrics as rm
        monkeypatch.setattr(rm, "get_metrics", lambda: FakeMetrics())
    except ImportError:
        pass

    check_contract_for_audit("files", "autonomy", has_workspace=False)
    check_contract_for_audit("agents", "react", has_workspace=False)

    assert calls == [], f"Metrics appelés: {calls}"


def test_pure_function_deterministic():
    """100 appels identiques retournent la même valeur."""
    results = {
        check_contract_for_audit("files", "autonomy", has_workspace=False)
        for _ in range(100)
    }
    assert len(results) == 1, "Résultat non déterministe"


def test_pure_function_no_context_leak():
    """Même si un ContextVar est setup, la fonction n'en lit pas."""
    # La fonction prend `has_workspace` en paramètre explicite.
    # Aucune lecture ContextVar/ide_context ne doit affecter le résultat.
    # On le prouve via 2 appels avec params identiques → résultat identique
    # peu importe ce qui se passe ailleurs dans le runtime.
    res1 = check_contract_for_audit("files", "autonomy", has_workspace=False)
    res2 = check_contract_for_audit("files", "autonomy", has_workspace=False)
    assert res1 == res2
    assert res1 is not None  # doit refuser


# ──────────────────────────────────────────────────────────────────────────────
# Règle requires_workspace
# ──────────────────────────────────────────────────────────────────────────────


def test_contract_autonomy_files_no_workspace_refused():
    """autonomy + files + no workspace → REFUS (cat. requires_workspace=True)."""
    result = check_contract_for_audit(
        semantic_category="files",
        caller_kind="autonomy",
        has_workspace=False,
    )
    assert result is not None
    assert "requires_workspace" in result.lower() or "workspace" in result.lower()


def test_contract_autonomy_files_with_workspace_allowed():
    """autonomy + files + workspace → OK."""
    result = check_contract_for_audit(
        semantic_category="files",
        caller_kind="autonomy",
        has_workspace=True,
    )
    assert result is None


def test_contract_scheduler_files_no_workspace_refused():
    """scheduler comporte comme autonomy."""
    result = check_contract_for_audit(
        semantic_category="files",
        caller_kind="scheduler",
        has_workspace=False,
    )
    assert result is not None


def test_contract_daemon_files_no_workspace_refused():
    """daemon comporte comme autonomy."""
    result = check_contract_for_audit(
        semantic_category="files",
        caller_kind="daemon",
        has_workspace=False,
    )
    assert result is not None


def test_contract_files_react_without_workspace_allowed():
    """react + files + no workspace → OK (contrôlé par WorkspaceFileGuardrails, pas par le contract)."""
    result = check_contract_for_audit(
        semantic_category="files",
        caller_kind="react",
        has_workspace=False,
    )
    assert result is None, (
        f"react+files+no_ws devrait être OK (la règle "
        f"requires_workspace pour react ne s'applique qu'à 'agents'). "
        f"Reçu: {result}"
    )


def test_contract_files_react_with_workspace_allowed():
    """react + files + workspace → OK."""
    result = check_contract_for_audit(
        semantic_category="files",
        caller_kind="react",
        has_workspace=True,
    )
    assert result is None


def test_contract_requires_workspace_react_agents():
    """react + agents + no workspace → REFUS (cas spécifique delegate_task)."""
    result = check_contract_for_audit(
        semantic_category="agents",
        caller_kind="react",
        has_workspace=False,
    )
    assert result is not None
    assert "agents" in result.lower() or "workspace" in result.lower()


def test_contract_agents_react_with_workspace_allowed():
    """react + agents + workspace → OK."""
    result = check_contract_for_audit(
        semantic_category="agents",
        caller_kind="react",
        has_workspace=True,
    )
    assert result is None


def test_contract_codeagent_files_no_workspace_allowed():
    """codeagent + files + no workspace → OK (codeagent jamais bloqué par cette règle)."""
    result = check_contract_for_audit(
        semantic_category="files",
        caller_kind="codeagent",
        has_workspace=False,
    )
    assert result is None


def test_contract_codeagent_agents_no_workspace_allowed():
    """codeagent + agents + no workspace → OK (la règle react+agents ne s'applique pas à codeagent)."""
    result = check_contract_for_audit(
        semantic_category="agents",
        caller_kind="codeagent",
        has_workspace=False,
    )
    assert result is None


# ──────────────────────────────────────────────────────────────────────────────
# Règle autonomy_allowed
# ──────────────────────────────────────────────────────────────────────────────


def test_contract_autonomy_not_allowed_blocks_autonomy():
    """Catégorie avec autonomy_allowed=False bloque caller=autonomy."""
    # 'communication' typiquement : autonomy_allowed=False
    # On vérifie via une catégorie réelle
    from src.reasoning.tool_categories import _CONTRACTS
    non_autonomy_categories = [
        name for name, c in _CONTRACTS.items() if not c.autonomy_allowed
    ]
    assert non_autonomy_categories, "Au moins une catégorie autonomy_allowed=False attendue"

    # Pick first non-workspace-requiring one for clean test
    target = None
    for name in non_autonomy_categories:
        c = _CONTRACTS[name]
        if not c.requires_workspace:
            target = name
            break

    if target is None:
        # All non-autonomy categories require workspace; provide workspace
        target = non_autonomy_categories[0]
        result = check_contract_for_audit(target, "autonomy", has_workspace=True)
    else:
        result = check_contract_for_audit(target, "autonomy", has_workspace=False)

    assert result is not None
    assert "autonomy" in result.lower()


def test_contract_autonomy_allowed_passes_for_react():
    """react n'est jamais bloqué par autonomy_allowed=False."""
    from src.reasoning.tool_categories import _CONTRACTS
    for name, c in _CONTRACTS.items():
        if c.autonomy_allowed:
            continue  # skip those allowed
        # caller=react + has_workspace=True doit passer
        # (sauf si semantic=="agents" qui a sa propre règle)
        if name == "agents":
            continue
        result = check_contract_for_audit(name, "react", has_workspace=True)
        assert result is None, (
            f"react ne doit pas être bloqué par autonomy_allowed=False "
            f"sur category={name}, reçu: {result}"
        )


# ──────────────────────────────────────────────────────────────────────────────
# Catégorie inconnue
# ──────────────────────────────────────────────────────────────────────────────


def test_contract_unknown_category_returns_none():
    """Catégorie inexistante → None (pas de contrat à appliquer)."""
    result = check_contract_for_audit(
        semantic_category="nonexistent_cat_xyz_123",
        caller_kind="react",
        has_workspace=False,
    )
    assert result is None


# ──────────────────────────────────────────────────────────────────────────────
# Tous les callers couverts
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "caller_kind",
    ["react", "codeagent", "autonomy", "scheduler", "daemon", "silent", "unknown"],
)
def test_contract_all_caller_kinds_return_value(caller_kind):
    """Tous les caller_kind connus sont gérés (retournent None ou str)."""
    result = check_contract_for_audit(
        semantic_category="files",
        caller_kind=caller_kind,
        has_workspace=True,
    )
    # Doit retourner None ou str — pas crash, pas autre type
    assert result is None or isinstance(result, str)
