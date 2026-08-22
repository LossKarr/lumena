from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from src.documents.document_intent import resolve_document_route
from src.documents.document_settings import get_document_settings
from src.reasoning.handlers.contracts import SubToolResult
from src.reasoning.handlers.documents import generate_studio_documents_handler
from src.reasoning.plan_progress import document_workflow_task_blocks
from src.reasoning.react import (
    ReActLoop,
    _document_batch_failure_signature,
    _document_minimum_pages_guidance,
    _observation_counts_as_recent_failure,
    _recent_tool_failure_streak,
    _repeated_tool_failure_message,
)
from src.reasoning.react_config import Observation


def _action(name: str, args=None):
    return SimpleNamespace(tool_name=name, tool_args=args or {})


def _step(name: str, args=None, content="", success=True, sub_results=()):
    return SimpleNamespace(
        action=_action(name, args),
        observation=Observation(content, success=success, sub_results=sub_results),
    )


def _compound_state():
    route = resolve_document_route(
        "Genere mes 4 derniers modeles personnalises puis 30 documents structures.",
        mode="agent",
    )
    return SimpleNamespace(
        _document_route=route,
        _document_catalog_evidence={},
        history=[],
    )


def test_minimum_pages_guidance_keeps_the_registered_template_and_real_content():
    route = resolve_document_route(
        "Cree un contrat de travail d'au moins 6 pages.", mode="agent",
    )

    guidance = _document_minimum_pages_guidance(route)

    assert "au moins 6 pages REELLES" in guidance
    assert "18000 caracteres visibles" in guidance
    assert "LE MEME modele" in guidance
    assert "Ne cree, n'importe et ne modifie aucun modele" in guidance
    assert "page vide" in guidance


def test_minimum_pages_guidance_is_inert_without_explicit_minimum():
    route = resolve_document_route("Cree un contrat de travail.", mode="agent")

    assert _document_minimum_pages_guidance(route) == ""


def test_document_settings_defaults_and_hard_clamps(monkeypatch):
    monkeypatch.delenv("LUMENA_DOCUMENT_BATCH_SIZE", raising=False)
    monkeypatch.delenv("LUMENA_DOCUMENT_WORKFLOW_MAX_DOCUMENTS", raising=False)
    assert get_document_settings().batch_size == 30
    assert get_document_settings().workflow_max_documents == 100

    monkeypatch.setenv("LUMENA_DOCUMENT_BATCH_SIZE", "999")
    monkeypatch.setenv("LUMENA_DOCUMENT_WORKFLOW_MAX_DOCUMENTS", "999")
    assert get_document_settings().batch_size == 30
    assert get_document_settings().workflow_max_documents == 100

    monkeypatch.setenv("LUMENA_DOCUMENT_BATCH_SIZE", "invalid")
    monkeypatch.setenv("LUMENA_DOCUMENT_WORKFLOW_MAX_DOCUMENTS", "invalid")
    assert get_document_settings().batch_size == 30
    assert get_document_settings().workflow_max_documents == 100


@pytest.mark.asyncio
async def test_batch_handler_uses_configured_limit(monkeypatch):
    monkeypatch.setenv("LUMENA_DOCUMENT_BATCH_SIZE", "2")
    result = await generate_studio_documents_handler(None, [{}, {}, {}])
    assert result.success is False
    assert "2 documents maximum" in result.output


def test_catalog_proof_survives_compacted_history():
    state = _compound_state()
    custom = [{"id": f"custom-{index}"} for index in range(1, 5)]
    builtin = [
        {"id": f"builtin-{index}", "description": "x" * 180}
        for index in range(1, 31)
    ]
    custom_action = _action(
        "list_document_models", {"origin": "custom", "limit": 4, "sort": "recent"},
    )
    builtin_action = _action(
        "list_document_models", {"origin": "builtin", "limit": 30, "sort": "name"},
    )
    ReActLoop._record_document_catalog_evidence(
        state, custom_action, Observation(json.dumps({"models": custom})),
    )
    ReActLoop._record_document_catalog_evidence(
        state, builtin_action, Observation(json.dumps({"models": builtin})),
    )
    # The history contains the exact invalid shape produced by ReAct compaction.
    state.history = [
        _step(custom_action.tool_name, custom_action.tool_args, "{...compacte...}"),
        _step(builtin_action.tool_name, builtin_action.tool_args, "{...compacte...}"),
    ]

    expected = ReActLoop._document_expected_template_ids(state)
    assert expected == tuple(
        [f"custom-{index}" for index in range(1, 5)]
        + [f"builtin-{index}" for index in range(1, 31)]
    )


def test_parallel_catalog_proof_is_recorded():
    state = _compound_state()
    rows = [{"id": f"custom-{index}"} for index in range(1, 5)]
    sub = SubToolResult(
        tool_name="list_document_models",
        success=True,
        content=json.dumps({"models": rows}),
        args={"origin": "custom", "limit": 4, "sort": "recent"},
    )
    ReActLoop._record_document_catalog_evidence(
        state,
        _action("parallel_tools"),
        Observation("parallel preview", sub_results=(sub,)),
    )
    key = ReActLoop._document_catalog_evidence_key(sub.args)
    assert [row["id"] for row in state._document_catalog_evidence[key]] == [
        f"custom-{index}" for index in range(1, 5)
    ]


def test_configured_batch_and_workflow_limits_are_policy_refusals(monkeypatch):
    state = _compound_state()
    custom = [{"id": f"custom-{index}"} for index in range(1, 5)]
    builtin = [{"id": f"builtin-{index}"} for index in range(1, 31)]
    for args, rows in (
        ({"origin": "custom", "limit": 4, "sort": "recent"}, custom),
        ({"origin": "builtin", "limit": 30, "sort": "name"}, builtin),
    ):
        ReActLoop._record_document_catalog_evidence(
            state, _action("list_document_models", args),
            Observation(json.dumps({"models": rows})),
        )

    monkeypatch.setenv("LUMENA_DOCUMENT_BATCH_SIZE", "2")
    monkeypatch.setenv("LUMENA_DOCUMENT_WORKFLOW_MAX_DOCUMENTS", "100")
    requests = [
        {"template_id": f"custom-{index}", "kind": "devis", "data": {}}
        for index in range(1, 4)
    ]
    refused = ReActLoop._structured_document_tool_gate(
        state, "generate_studio_documents", {"requests": requests},
    )
    assert refused is not None
    assert refused.origin == "document_policy"
    assert "maximum 2" in refused.content

    monkeypatch.setenv("LUMENA_DOCUMENT_WORKFLOW_MAX_DOCUMENTS", "20")
    refused = ReActLoop._structured_document_tool_gate(
        state, "generate_studio_documents", {"requests": requests[:2]},
    )
    assert refused is not None
    assert refused.origin == "document_policy"
    assert "34 documents demandes" in refused.content


def test_policy_refusal_is_not_a_tool_failure_but_real_failure_is():
    policy = Observation("guided refusal", success=False, origin="document_policy")
    failure = Observation("network failure", success=False)
    assert _observation_counts_as_recent_failure("generate_studio_documents", policy) is False
    assert _observation_counts_as_recent_failure("generate_studio_documents", failure) is True


def _preflight_failure(failed: int, errors: list[dict]) -> Observation:
    return Observation(
        json.dumps({
            "phase": "preflight",
            "requested": 6,
            "generated": 0,
            "failed": failed,
            "errors": errors,
        }),
        success=False,
    )


def test_document_batch_failure_signature_tracks_actual_preflight_errors():
    first = _preflight_failure(
        3, [{"index": 4, "kind": "proces_verbal", "error": "participants"}],
    )
    second = _preflight_failure(
        2, [{"index": 4, "kind": "proces_verbal", "error": "ordre_du_jour"}],
    )

    assert _document_batch_failure_signature(first)
    assert _document_batch_failure_signature(first) != _document_batch_failure_signature(second)
    assert _document_batch_failure_signature(
        Observation("Parametre requests manquant", success=False),
    ) != _document_batch_failure_signature(first)


def test_document_batch_progress_does_not_trigger_repeated_failure_stop():
    history = [
        _step(
            "generate_studio_documents",
            content=_preflight_failure(
                3, [{"index": 4, "kind": "proces_verbal", "error": "participants"}],
            ).content,
            success=False,
        ),
        _step(
            "generate_studio_documents",
            content=_preflight_failure(
                3, [{"index": 4, "kind": "proces_verbal", "error": "resolutions"}],
            ).content,
            success=False,
        ),
        _step(
            "generate_studio_documents",
            content=_preflight_failure(
                2, [{"index": 4, "kind": "proces_verbal", "error": "ordre_du_jour"}],
            ).content,
            success=False,
        ),
    ]

    assert _recent_tool_failure_streak("generate_studio_documents", history) == 1


def test_document_batch_changed_arguments_are_progress_even_with_same_error():
    failure = _preflight_failure(
        1, [{"index": 1, "kind": "facture", "error": "client manquant"}],
    )
    history = [
        _step(
            "generate_studio_documents",
            args={"requests": [{"kind": "facture", "data": {"client": value}}]},
            content=failure.content,
            success=False,
        )
        for value in ("", "Atelier", "Atelier Lumena")
    ]

    assert _recent_tool_failure_streak("generate_studio_documents", history) == 1


def test_document_batch_identical_failure_still_stops_after_three_attempts():
    failure = _preflight_failure(
        2,
        [
            {"index": 4, "kind": "proces_verbal", "error": "ordre_du_jour"},
            {"index": 6, "kind": "lettre_officielle", "error": "expediteur"},
        ],
    )
    history = [
        _step(
            "generate_studio_documents",
            content=failure.content,
            success=False,
        )
        for _ in range(3)
    ]

    assert _recent_tool_failure_streak("generate_studio_documents", history) == 3
    message = _repeated_tool_failure_message(
        "generate_studio_documents", history,
    )
    assert "Aucun document Studio n'a ete genere" in message
    assert "autre outil" not in message
    assert "CodeAgent" not in message


def test_generic_tool_failure_count_keeps_historical_behavior():
    history = [
        _step("some_tool", content=f"failure-{index}", success=False)
        for index in range(3)
    ]
    assert _recent_tool_failure_streak("some_tool", history) == 3


def test_compound_create_open_revise_routes_to_creation_first():
    compound = resolve_document_route(
        "Genere un devis, ouvre-le, puis modifie le numero.", mode="agent",
    )
    revision = resolve_document_route(
        "Modifie mon devis existant pour changer le numero.", mode="agent",
    )
    assert compound.operation == "create"
    assert [item.operation for item in compound.workflow_actions] == [
        "generate", "open", "revise",
    ]
    assert revision.operation == "revise"


def test_generation_plan_requires_a_generation_proof():
    task = "Generer le devis"
    assert document_workflow_task_blocks("search_document_library", task) is True
    assert document_workflow_task_blocks("list_document_models", task) is True
    assert document_workflow_task_blocks("generate_studio_document", task) is False
    assert document_workflow_task_blocks("document_manifest", task) is False
