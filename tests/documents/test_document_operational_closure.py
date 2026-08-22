from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.documents.document_intent import resolve_document_route
from src.documents.document_library import DocumentLibrary
from src.documents.provenance import DocumentRecord
from src.reasoning.handlers.contracts import HandlerResult, SubToolResult
from src.reasoning.handlers.documents import (
    generate_studio_documents_handler,
    search_document_library_handler,
)
from src.reasoning.react import ReActLoop
from src.reasoning.react_config import Observation, TaskItem


def _step(tool_name: str, *, args=None, content="", success=True, sub_results=()):
    return SimpleNamespace(
        action=SimpleNamespace(tool_name=tool_name, tool_args=args or {}),
        observation=Observation(
            content,
            success=success,
            sub_results=sub_results,
        ),
    )


def _proof(template_id: str, kind: str, index: int) -> str:
    return json.dumps({
        "kind": kind,
        "document_id": f"doc-{index}",
        "filename": f"document-{index:02d}.pdf",
        "path": f"C:/workspace/documents/document-{index:02d}.pdf",
        "sha256": f"sha-{index}",
        "template_id": template_id,
        "format": "pdf",
        "size": 100 + index,
        "logo_id": "",
        "render_status": "render_verified",
        "render_verified": True,
        "thumbnail_path": "",
    })


def test_custom_latest_models_are_a_bounded_studio_selection():
    route = resolve_document_route(
        "Genere un document avec chacun de mes quatre derniers modeles personnalises",
        mode="agent",
    )

    assert route.requires_studio is True
    assert route.requested_count == 4
    assert route.selection_origin == "custom"
    assert route.selection_limit == 4
    assert route.selection_sort == "recent"
    assert route.requested_kinds == ()


def test_generic_30_structured_documents_select_builtin_catalog_deterministically():
    route = resolve_document_route(
        "Genere 30 documents structures differents avec Document Studio",
        mode="agent",
    )

    assert route.requires_studio is True
    assert route.requested_count == 30
    assert route.selection_origin == "builtin"
    assert route.selection_limit == 30
    assert route.selection_sort == "name"


def test_exactly_named_integrated_models_override_the_generic_catalog_count():
    route = resolve_document_route(
        "Genere exactement ces 6 modeles integres : devis, facture, bon de commande, "
        "proces-verbal de reunion, rapport d'activite et lettre officielle.",
        mode="agent",
    )

    assert route.requires_studio is True
    assert route.is_catalog_selection is False
    assert route.requested_count == 6
    assert route.requested_kinds == (
        "devis",
        "facture",
        "bon_commande",
        "proces_verbal",
        "rapport_activite",
        "lettre_officielle",
    )


def test_partial_example_does_not_weaken_a_real_catalog_count_selection():
    route = resolve_document_route(
        "Genere 6 modeles integres, dont un devis.",
        mode="agent",
    )

    assert route.is_catalog_selection is True
    assert route.requested_count == 6
    assert route.selection_origin == "builtin"


def test_selection_gate_requires_catalog_then_exact_template_ids():
    route = resolve_document_route(
        "Genere un document avec chacun de mes quatre derniers modeles personnalises",
        mode="agent",
    )
    state = SimpleNamespace(_document_route=route, history=[])
    requests = [
        {"kind": "devis", "template_id": f"custom-{index}", "data": {}}
        for index in range(1, 5)
    ]

    blocked = ReActLoop._structured_document_tool_gate(
        state,
        "generate_studio_documents",
        {"requests": requests},
    )
    assert blocked is not None
    assert "list_document_models" in blocked.content

    models = [{
        "id": f"custom-{index}",
        "kind": "devis",
        "name": f"Custom {index}",
        "format": "pdf",
        "origin": "custom",
    } for index in range(1, 5)]
    state.history.append(_step(
        "list_document_models",
        args={"origin": "custom", "limit": 4, "sort": "recent"},
        content=json.dumps({"models": models}),
    ))

    assert ReActLoop._structured_document_tool_gate(
        state,
        "generate_studio_documents",
        {"requests": requests},
    ) is None

    wrong = [*requests]
    wrong[-1] = {"kind": "devis", "template_id": "invented", "data": {}}
    refused = ReActLoop._structured_document_tool_gate(
        state,
        "generate_studio_documents",
        {"requests": wrong},
    )
    assert refused is not None
    assert "template_id" in refused.content


def test_selection_manifest_uses_latest_batch_and_catalog_order():
    route = resolve_document_route(
        "Genere un document avec chacun de mes quatre derniers modeles personnalises",
        mode="agent",
    )
    models = [{
        "id": f"custom-{index}", "kind": "devis", "name": str(index),
        "format": "pdf", "origin": "custom",
    } for index in range(1, 5)]
    partial = tuple(
        SubToolResult(
            tool_name="generate_studio_document",
            success=True,
            content=_proof(f"custom-{index}", "devis", index),
            args={"kind": "devis", "template_id": f"custom-{index}"},
        )
        for index in range(1, 3)
    )
    complete = tuple(
        SubToolResult(
            tool_name="generate_studio_document",
            success=True,
            content=_proof(f"custom-{index}", "devis", index),
            args={"kind": "devis", "template_id": f"custom-{index}"},
        )
        for index in range(1, 5)
    )
    state = SimpleNamespace(
        _document_route=route,
        history=[
            _step("list_document_models", content=json.dumps({"models": models})),
            _step("generate_studio_documents", sub_results=partial),
            _step("generate_studio_documents", sub_results=complete),
        ],
    )

    manifest, missing, unverified = ReActLoop._structured_document_delivery_manifest(state)

    assert [proof.template_id for proof in manifest] == [
        "custom-1", "custom-2", "custom-3", "custom-4",
    ]
    assert missing == ()
    assert unverified == ()


def test_selection_manifest_completes_plain_generation_and_verification_tasks():
    route = resolve_document_route(
        "Genere un document avec chacun de mes quatre derniers modeles personnalises",
        mode="agent",
    )
    models = [{
        "id": f"custom-{index}", "kind": "devis", "name": str(index),
        "format": "pdf", "origin": "custom",
    } for index in range(1, 5)]
    complete = tuple(
        SubToolResult(
            tool_name="generate_studio_document",
            success=True,
            content=_proof(f"custom-{index}", "devis", index),
            args={"kind": "devis", "template_id": f"custom-{index}"},
        )
        for index in range(1, 5)
    )
    state = SimpleNamespace(
        _document_route=route,
        history=[
            _step("list_document_models", content=json.dumps({"models": models})),
            _step("generate_studio_documents", sub_results=complete),
        ],
        _task_plan=[
            TaskItem("Generer les quatre documents selectionnes"),
            TaskItem("Verifier les rendus du lot"),
        ],
        _emit_plan_state=lambda **_kwargs: None,
    )

    assert ReActLoop._reconcile_document_plan_from_manifest(state, 2) == 2
    assert all(task.completed for task in state._task_plan)


class _BatchStudio:
    def __init__(self, root: Path, *, invalid_kind: str = ""):
        self.root = root
        self.output_root = root / "output"
        self.output_root.mkdir(parents=True)
        self.invalid_kind = invalid_kind
        self.calls = []
        self.catalog = SimpleNamespace(
            read_sample_data=lambda record: {"sample": record.manifest.id},
        )

    def resolve_template(self, *, template_id="", kind="", output_format="pdf"):
        selected = template_id or kind
        if selected == self.invalid_kind:
            raise KeyError(selected)
        return SimpleNamespace(
            directory=self.root,
            manifest=SimpleNamespace(
                id=selected,
                kind=kind or selected,
                format="pdf",
                renderer="html-jinja",
            ),
        )

    @staticmethod
    def parse_json_object(value, *, field):
        parsed = json.loads(value)
        if not isinstance(parsed, dict):
            raise ValueError(f"{field} doit etre un objet")
        return parsed

    @staticmethod
    def _safe_stem(value):
        return Path(str(value)).stem

    async def generate(self, **kwargs):
        self.calls.append(kwargs)
        target = self.output_root / f"{kwargs['filename']}.pdf"
        target.write_bytes(kwargs["kind"].encode())
        content = target.read_bytes()
        return {
            "path": str(target),
            "record": {
                "id": f"doc-{kwargs['filename']}",
                "sha256": hashlib.sha256(content).hexdigest(),
                "format": "pdf",
                "size": len(content),
                "template_id": kwargs["template_id"] or kwargs["kind"],
            },
            "recipe": {
                "kind": kwargs["kind"],
                "template_id": kwargs["template_id"] or kwargs["kind"],
                "output_format": "pdf",
            },
            "render_proof": {"status": "render_verified", "verified": True},
        }


class _StructuredBuiltinBatchStudio(_BatchStudio):
    def __init__(self, root: Path):
        super().__init__(root)
        self.catalog = SimpleNamespace(
            read_sample_data=lambda _record: {
                "salarie": {"name": "Morgan Leroy", "service": "Produit"},
                "clauses": [{"title": "Confidentialite", "content": "Exemple"}],
            },
        )

    def resolve_template(self, *, template_id="", kind="", output_format="pdf"):
        selected = template_id or kind
        return SimpleNamespace(
            directory=self.root,
            manifest=SimpleNamespace(
                id=selected,
                kind=kind or selected,
                format="pdf",
                renderer="native-pdf",
                origin="builtin",
            ),
        )


@pytest.mark.asyncio
async def test_batch_preflight_rejects_everything_before_first_write(monkeypatch, tmp_path):
    from src.documents import studio as studio_module

    studio = _BatchStudio(tmp_path / "studio", invalid_kind="unknown")
    monkeypatch.setattr(studio_module, "get_document_studio", lambda: studio)

    result = await generate_studio_documents_handler(None, [
        {"kind": "devis", "filename": "would-have-been-written"},
        {"kind": "unknown", "filename": "invalid"},
    ])

    assert result.success is False
    assert studio.calls == []
    assert list(studio.output_root.iterdir()) == []
    assert "preflight" in result.output.lower()


@pytest.mark.asyncio
async def test_batch_preflight_rejects_wrong_nested_shape_before_first_render(
    monkeypatch, tmp_path,
):
    from src.documents import studio as studio_module

    studio = _StructuredBuiltinBatchStudio(tmp_path / "studio")
    monkeypatch.setattr(studio_module, "get_document_studio", lambda: studio)

    result = await generate_studio_documents_handler(None, [{
        "kind": "contrat_travail",
        "filename": "contrat-sarah",
        "data": {
            "salarie": "Sarah Morel",
            "clauses": [{"title": "Confidentialite", "content": "Adaptee"}],
        },
    }])
    payload = json.loads(result.output)

    assert result.success is False
    assert studio.calls == []
    assert list(studio.output_root.iterdir()) == []
    assert payload["phase"] == "preflight"
    assert payload["errors"][0]["kind"] == "contrat_travail"
    assert payload["errors"][0]["template_id"] == "contrat_travail"
    assert "salarie doit etre un objet" in payload["errors"][0]["error"]
    assert '"name":"Morgan Leroy"' in payload["errors"][0]["error"]


@pytest.mark.asyncio
async def test_batch_preflight_accepts_valid_partial_nested_shapes(monkeypatch, tmp_path):
    from src.documents import studio as studio_module

    studio = _StructuredBuiltinBatchStudio(tmp_path / "studio")
    monkeypatch.setattr(studio_module, "get_document_studio", lambda: studio)

    result = await generate_studio_documents_handler(None, [{
        "kind": "contrat_travail",
        "filename": "contrat-sarah",
        "data": {
            "salarie": {"name": "Sarah Morel"},
            "clauses": [{"title": "Confidentialite"}],
        },
    }])

    assert result.success is True
    assert len(studio.calls) == 1
    generated = studio.calls[0]["data"]
    assert generated["salarie"] == {
        "name": "Sarah Morel",
        "service": "Produit",
    }
    assert generated["clauses"] == [{
        "title": "Confidentialite",
        "content": "Exemple",
    }]


@pytest.mark.asyncio
async def test_batch_preflight_rejects_scalar_in_structured_list(monkeypatch, tmp_path):
    from src.documents import studio as studio_module

    studio = _StructuredBuiltinBatchStudio(tmp_path / "studio")
    monkeypatch.setattr(studio_module, "get_document_studio", lambda: studio)

    result = await generate_studio_documents_handler(None, [{
        "kind": "contrat_travail",
        "data": {
            "salarie": {"name": "Sarah Morel"},
            "clauses": ["Confidentialite"],
        },
    }])

    assert result.success is False
    assert studio.calls == []
    error = json.loads(result.output)["errors"][0]["error"]
    assert "clauses[0] doit etre un objet JSON" in error


@pytest.mark.asyncio
async def test_batch_preflight_returns_exact_retry_data_contract(monkeypatch, tmp_path):
    from src.documents import studio as studio_module

    studio = _StructuredBuiltinBatchStudio(tmp_path / "studio")
    monkeypatch.setattr(studio_module, "get_document_studio", lambda: studio)

    result = await generate_studio_documents_handler(None, [{
        "kind": "contrat_travail",
        "data": {"champ_invente": "interdit"},
    }])
    payload = json.loads(result.output)

    assert result.success is False
    assert payload["phase"] == "preflight"
    assert payload["generated"] == 0
    error = payload["errors"][0]
    assert error["kind"] == "contrat_travail"
    assert error["retry_request"]["kind"] == "contrat_travail"
    assert error["retry_request"]["data"]["salarie"]["name"] == "Morgan Leroy"
    assert error["retry_request"]["data"]["clauses"] == [{
        "title": "Confidentialite",
        "content": "Exemple",
    }]


@pytest.mark.asyncio
async def test_batch_persists_exact_receipt_in_handler_observation(monkeypatch, tmp_path):
    from src.documents import studio as studio_module
    from src.documents.delivery_receipt import load_delivery_receipt

    studio = _BatchStudio(tmp_path / "studio")
    monkeypatch.setattr(studio_module, "get_document_studio", lambda: studio)

    result = await generate_studio_documents_handler(None, [
        {"kind": "devis", "filename": "devis-01"},
        {"kind": "facture", "filename": "facture-02"},
    ])
    payload = json.loads(result.output)

    assert result.success is True
    assert payload["receipt_id"].startswith("doclot_")
    receipt = load_delivery_receipt(
        studio.root / "delivery_receipts",
        payload["receipt_id"],
    )
    assert receipt["requested_count"] == 2
    assert [row["filename"] for row in receipt["documents"]] == [
        "devis-01.pdf", "facture-02.pdf",
    ]


def test_library_strict_reference_never_falls_back_to_unique_search(tmp_path):
    library = DocumentLibrary(tmp_path / "documents.sqlite3")
    record = DocumentRecord.create(
        sha256="abc",
        filename="devis-noir-atlas-sarl.pdf",
        path=str(tmp_path / "devis-noir-atlas-sarl.pdf"),
        format="pdf",
        mime_type="application/pdf",
        size=10,
        source_kind="generated",
        title="Devis noir Atlas SARL",
        content_text="Devis professionnel pour Atlas",
    )
    library.upsert(record)

    assert library.resolve_reference("Atlas").id == record.id
    assert library.resolve_reference("Atlas", allow_search=False) is None


def test_revision_route_never_allows_fresh_generation_as_a_substitute():
    route = resolve_document_route(
        "Modifie mon devis-atlas.pdf pour remplacer le client par Nova",
        mode="agent",
    )
    state = SimpleNamespace(_document_route=route, history=[])

    blocked = ReActLoop._structured_document_tool_gate(
        state,
        "generate_studio_document",
        {"kind": "devis", "data": {}},
    )

    assert blocked is not None
    assert "revise_studio_document" in blocked.content


@pytest.mark.asyncio
async def test_library_search_accepts_list_formats(monkeypatch):
    from src.documents import studio as studio_module

    captured = {}
    library = SimpleNamespace(
        search=lambda query, **kwargs: captured.update({"query": query, **kwargs}) or [],
    )
    monkeypatch.setattr(
        studio_module,
        "get_document_studio",
        lambda: SimpleNamespace(library=library),
    )

    result = await search_document_library_handler(
        None,
        "cert-doc-01",
        formats=["pdf", ".docx"],
    )

    assert result.success is True
    assert captured["formats"] == ["pdf", "docx"]
