"""
Phase 0 — Diagnostic + tests post-patch.

AVANT le patch (commits antérieurs au 2026-05-16) :
    `_maybe_generate_reflexion` et amies bail out silencieusement à cause
    de `self.client` inexistant.

APRÈS le patch :
    Elles utilisent `self._get_llm(getattr(self, "current_task", None))`
    qui retombe sur `core.llm` si current_task est None (fire-and-forget).

Référence : DIAGNOSTIC_PROD.md section 3.
Lignes patched dans sub_agent.py : ~1985, ~2055, ~2122.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── Test 1 : invariants structurels (ne dépendent pas du patch) ──────────────


def test_no_legacy_client_pattern_left():
    """Vérifie qu'aucune référence au `getattr(self, "client", ...)` bogué
    ne subsiste dans sub_agent.py.

    Si ce test FAIL : un nouveau code a réintroduit le pattern → potentielle
    régression vers le silent bail-out.
    """
    src = Path("src/agents/sub_agent.py").read_text(encoding="utf-8")
    assert 'getattr(self, "client"' not in src, (
        "Le pattern bogué 'getattr(self, \"client\", ...)' est revenu — "
        "régression du fix Phase 0. Cf. DIAGNOSTIC_PROD.md §3."
    )
    assert 'getattr(self, "_client"' not in src, (
        "Idem pour `_client`."
    )


def test_get_llm_handles_none_task():
    """`_get_llm(None)` doit retomber sur core.llm sans crash.

    C'est ce qui rend sûr l'appel fire-and-forget depuis les fonctions
    d'apprentissage où `current_task` peut déjà être None.
    """
    from src.agents.sub_agent import CodeAgent

    agent = CodeAgent()
    llm = agent._get_llm(None)
    assert llm is not None, "_get_llm(None) ne doit jamais retourner None"


# ── Test 2 : mocked LLM — _maybe_generate_reflexion appelle bien llm.chat() ──


@pytest.mark.asyncio
async def test_maybe_generate_reflexion_calls_llm_and_writes_store(tmp_path, monkeypatch):
    """APRÈS PATCH : la méthode doit
    1. appeler `llm.chat(...)`
    2. parser la réponse
    3. écrire dans le ReflexionStore.
    """
    from src.agents.sub_agent import CodeAgent

    # Mock LLM qui retourne une réponse parseable
    fake_llm_response = json.dumps({
        "triggered_by": "test_signal",
        "root_cause": "test cause",
        "lesson": "Toujours vérifier le contexte avant str_replace",
        "apply_when": "édition fichier",
        "confidence": 0.8,
        "tags": ["test"],
    })

    mock_llm = MagicMock()
    mock_llm.chat = AsyncMock(return_value=fake_llm_response)

    # Mock le store pour un chemin isolé
    from src.learning import reflexion_store as rs_mod

    isolated_store = rs_mod.ReflexionStore(path=tmp_path / "reflexions.jsonl")
    monkeypatch.setattr(rs_mod, "get_reflexion_store", lambda: isolated_store)

    agent = CodeAgent()
    agent._last_reflexion_ts = 0.0  # bypass anti-spam

    # Patch _get_llm pour retourner notre mock
    with patch.object(agent, "_get_llm", return_value=mock_llm):
        await agent._maybe_generate_reflexion(
            signal="test_signal",
            context_tail="trace de test",
            task_hint="Tâche de test",
        )

    # 1. llm.chat a été appelé exactement une fois avec les bons params
    mock_llm.chat.assert_called_once()
    call_kwargs = mock_llm.chat.call_args.kwargs
    assert "messages" in call_kwargs
    assert call_kwargs["temperature"] == 0.2
    assert call_kwargs["max_tokens"] == 350

    # 2. Le store contient maintenant une reflexion
    assert len(isolated_store._items) == 1, (
        "Le store doit contenir la reflexion ajoutée après le patch"
    )

    # 3. Le compteur a été incrémenté
    assert agent._reflexion_generated_count == 1


@pytest.mark.asyncio
async def test_maybe_generate_reflexion_handles_llm_error_silently(tmp_path, monkeypatch):
    """Si llm.chat() throw, la fonction doit avaler l'exception (fire-and-forget).

    C'est important : si l'apprentissage casse, il ne doit pas faire échouer
    la tâche principale.
    """
    from src.agents.sub_agent import CodeAgent

    mock_llm = MagicMock()
    mock_llm.chat = AsyncMock(side_effect=RuntimeError("LLM API down"))

    from src.learning import reflexion_store as rs_mod
    isolated_store = rs_mod.ReflexionStore(path=tmp_path / "reflexions.jsonl")
    monkeypatch.setattr(rs_mod, "get_reflexion_store", lambda: isolated_store)

    agent = CodeAgent()
    agent._last_reflexion_ts = 0.0

    with patch.object(agent, "_get_llm", return_value=mock_llm):
        # Ne doit PAS lever
        await agent._maybe_generate_reflexion(
            signal="test",
            context_tail="trace",
            task_hint="task",
        )

    # Store reste vide (LLM a échoué)
    assert len(isolated_store._items) == 0


@pytest.mark.asyncio
async def test_maybe_generate_reflexion_works_with_current_task_none(tmp_path, monkeypatch):
    """Cas réaliste fire-and-forget : `self.current_task` est None
    (la tâche s'est terminée avant que la coroutine planifiée s'exécute).

    Doit utiliser core.llm gracieusement.
    """
    from src.agents.sub_agent import CodeAgent

    fake_response = json.dumps({
        "triggered_by": "post_task_signal",
        "root_cause": "tâche terminée mais coroutine ré-exécute",
        "lesson": "Toujours passer current_task explicitement",
        "apply_when": "fire-and-forget",
        "confidence": 0.7,
        "tags": [],
    })

    mock_llm = MagicMock()
    mock_llm.chat = AsyncMock(return_value=fake_response)

    from src.learning import reflexion_store as rs_mod
    isolated_store = rs_mod.ReflexionStore(path=tmp_path / "reflexions.jsonl")
    monkeypatch.setattr(rs_mod, "get_reflexion_store", lambda: isolated_store)

    agent = CodeAgent()
    agent._last_reflexion_ts = 0.0
    agent.current_task = None  # ← simule fin de tâche

    with patch.object(agent, "_get_llm", return_value=mock_llm) as mock_get_llm:
        await agent._maybe_generate_reflexion(
            signal="test",
            context_tail="trace",
            task_hint="task",
        )

    # _get_llm doit avoir été appelé avec None (current_task)
    mock_get_llm.assert_called_once_with(None)
    assert len(isolated_store._items) == 1


# ── Test 3 : baseline historique (peut devenir obsolète après nouvelle prod) ──


def test_metrics_jsonl_baseline_reflexions_zero():
    """Documente le constat AVANT patch : 0 reflexion sur 197 vraies tâches.

    Ce test reste valide tant qu'aucune nouvelle vraie tâche n'est exécutée
    après le patch. Quand de nouvelles tâches viendront, le total montera
    progressivement → adapter ou supprimer ce baseline.
    """
    metrics_path = Path("data/logs/codeagent/metrics.jsonl")
    if not metrics_path.exists():
        pytest.skip("metrics.jsonl non présent dans cet environnement")

    real_tasks_reflexions = 0
    real_tasks_count = 0

    with metrics_path.open(encoding="utf-8") as f:
        for line in f:
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get("task_id") == "task-x":
                continue
            real_tasks_count += 1
            real_tasks_reflexions += d.get("reflexions_generated", 0)

    assert real_tasks_count > 0
    # Baseline pré-patch — sera dépassé dès que la prod tournera après le fix
    print(f"\nBaseline historique : {real_tasks_reflexions} reflexions / {real_tasks_count} tâches")
