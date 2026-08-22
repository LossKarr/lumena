from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from src.documents.document_intent import (
    DOCUMENT_KINDS,
    normalize_document_kind,
    resolve_document_route,
    document_kinds_mentioned,
)
from src.reasoning.handlers.contracts import SubToolResult
from src.reasoning.react import ReActLoop, _document_requested_kinds_guidance
from src.reasoning.react_config import Observation
from src.reasoning.react_config import TaskItem
from src.reasoning.plan_progress import (
    _read_only_discovery_tool_can_complete_task,
    document_plan_tool_can_complete_task,
)


THIRTEEN_DOCUMENTS = """
Fais-moi une attestation de travail detaillee.
Cree-moi un bon de commande professionnel.
Fais-moi un bulletin de paye complet.
Redige un contrat de prestation professionnelle.
Prepare-moi un devis detaille.
Cree une facture professionnelle complete.
Redige une fiche de poste pour un developpeur Python.
Prepare une lettre officielle de demande de partenariat.
Cree un accord de confidentialite entre deux entreprises.
Redige une note interne annoncant une nouvelle procedure.
Prepare le proces-verbal d'une reunion d'equipe.
Cree un rapport d'activite mensuel detaille.
Redige une relance professionnelle pour une facture impayee.
""".strip()

LEGACY_THIRTEEN_KINDS = (
    "attestation", "bon_commande", "bulletin_paie", "contrat_prestation",
    "devis", "facture", "fiche_poste", "lettre_officielle", "nda",
    "note_interne", "proces_verbal", "rapport_activite", "relance_impaye",
)


AGENCY_OPENING_KIT = (
    "Prepare un kit complet d'ouverture : contrat de travail, fiche de poste, "
    "procedure d'accueil, ordre de mission, note de frais, feuille de temps, "
    "demande de conge, rapport d'incident, plan d'action, compte rendu de "
    "reunion et facture proforma. Genere ces 11 documents avec les modeles "
    "Document Studio appropries, ouvre-les tous, puis modifie le 6e document "
    "pour ajouter AGENCE-LYON-2042. Relis ce document et fournis le bilan."
)

AGENCY_OPENING_KINDS = (
    "contrat_travail", "fiche_poste", "procedure_operationnelle",
    "ordre_mission", "note_frais", "feuille_temps", "demande_conge",
    "rapport_incident", "plan_action", "proces_verbal", "facture_proforma",
)

FESTIVAL_NANTES_WORKFLOW = (
    "Prépare le dossier administratif complet d’un festival culturel à Nantes "
    "avec exactement 11 documents, dans cet ordre : une facture, un devis, "
    "un bon de commande, une note de frais, un rapport d’activité, une feuille "
    "de temps, une procédure d’accueil, un compte rendu de réunion, un plan "
    "d’action, une attestation et une lettre officielle.\n\n"
    "La procédure d’accueil doit contenir 6 étapes détaillées et le compte "
    "rendu doit inclure 4 participants avec leurs rôles. Utilise uniquement "
    "les modèles Document Studio correspondants, sans PDF générique.\n\n"
    "Ensuite, ouvre les 11 documents, modifie le 6e document en ajoutant la "
    "référence FESTIVAL-NANTES-730, relis le document modifié et donne-moi "
    "un bilan exact avec le reçu, les fichiers produits et la preuve que cette "
    "référence est bien présente."
)

FESTIVAL_NANTES_KINDS = (
    "facture", "devis", "bon_commande", "note_frais", "rapport_activite",
    "feuille_temps", "procedure_operationnelle", "proces_verbal",
    "plan_action", "attestation", "lettre_officielle",
)

ATELIER_HORIZON_WORKFLOW = """
Crée exactement 6 PDF professionnels pour l’entreprise Atelier Horizon : un devis, une facture, un bon de commande, un procès-verbal de réunion, un rapport d’activité et une lettre officielle.

Utilise exclusivement les modèles intégrés de Document Studio, jamais create_pdf. Renseigne des données cohérentes et professionnelles, puis ouvre réellement les 6 documents générés.

Ensuite, modifie uniquement le numéro du devis pour le remplacer par DEV-CLOTURE-7391, sans remplacer les autres données. Ouvre le document révisé et relis-le.

Termine avec un bilan indiquant les 6 reçus, l’identifiant du devis parent et celui de sa version révisée.
""".strip()

ATELIER_HORIZON_KINDS = (
    "devis", "facture", "bon_commande", "proces_verbal",
    "rapport_activite", "lettre_officielle",
)

NOVA_SANTE_WORKFLOW = (
    "Cr\u00e9e exactement 6 PDF RH professionnels pour l\u2019entreprise Nova Sant\u00e9 : "
    "un contrat de travail, une demande de cong\u00e9, un compte rendu "
    "d\u2019entretien annuel, une note de frais, un ordre de mission et une fiche "
    "de poste. Utilise exclusivement les mod\u00e8les int\u00e9gr\u00e9s de Document "
    "Studio, jamais create_pdf, puis ouvre r\u00e9ellement les 6 documents. "
    "Ensuite, r\u00e9vise uniquement le compte rendu d\u2019entretien annuel pour "
    "ajouter CAP-LEADERSHIP-2042 dans son bilan. Ouvre la version r\u00e9vis\u00e9e "
    "et relis-la. Termine avec un bilan exact."
)

NOVA_SANTE_KINDS = (
    "contrat_travail", "demande_conge", "entretien_annuel",
    "note_frais", "ordre_mission", "fiche_poste",
)


def _step(tool_name: str, *, args=None, success=True, sub_results=()):
    return SimpleNamespace(
        action=SimpleNamespace(tool_name=tool_name, tool_args=args or {}),
        observation=Observation("ok" if success else "failed", success=success, sub_results=sub_results),
    )


def test_combined_request_keeps_all_13_catalog_documents_in_order():
    route = resolve_document_route(THIRTEEN_DOCUMENTS, mode="agent")

    assert route.requires_studio is True
    assert route.operation == "create"
    assert route.requested_kinds == LEGACY_THIRTEEN_KINDS
    assert len(route.items) == 13
    assert [item.index for item in route.items] == list(range(1, 14))
    assert len(set(route.requested_kinds)) == 13
    assert route.requested_kinds[-1] == "relance_impaye"


def test_single_sentence_business_kit_keeps_all_11_models_and_sixth_target():
    route = resolve_document_route(AGENCY_OPENING_KIT, mode="agent")

    assert route.requires_studio is True
    assert route.operation == "create"
    assert route.requested_kinds == AGENCY_OPENING_KINDS
    assert [item.index for item in route.items] == list(range(1, 12))
    revision = next(
        action for action in route.workflow_actions
        if action.operation == "revise"
    )
    assert revision.target_ordinal == 6


def test_detail_sentence_cannot_shrink_the_explicit_festival_batch():
    route = resolve_document_route(FESTIVAL_NANTES_WORKFLOW, mode="agent")

    assert route.operation == "create"
    assert route.requested_count == 11
    assert route.requested_kinds == FESTIVAL_NANTES_KINDS
    assert [item.index for item in route.items] == list(range(1, 12))
    revision = next(
        action for action in route.workflow_actions
        if action.operation == "revise"
    )
    assert revision.target_ordinal == 6


def test_compound_batch_then_revision_keeps_exact_initial_generation_set():
    route = resolve_document_route(ATELIER_HORIZON_WORKFLOW, mode="agent")

    assert route.requires_studio is True
    assert route.operation == "create"
    assert route.requested_kinds == ATELIER_HORIZON_KINDS
    assert route.requested_count == 6
    assert [item.operation for item in route.items] == ["create"] * 6
    assert [action.operation for action in route.workflow_actions] == [
        "generate", "open", "revise", "verify", "deliver",
    ]
    revision = next(
        action for action in route.workflow_actions
        if action.operation == "revise"
    )
    assert revision.target_ordinal == 1
    guidance = _document_requested_kinds_guidance(route)
    assert "exactement 6 types" in guidance
    assert ", ".join(ATELIER_HORIZON_KINDS) in guidance
    assert "rapport_activite, devis, devis" not in guidance


def test_named_hr_revision_resolves_one_specific_model_and_third_target():
    route = resolve_document_route(NOVA_SANTE_WORKFLOW, mode="agent")

    assert route.requested_kinds == NOVA_SANTE_KINDS
    assert route.requested_count == 6
    assert document_kinds_mentioned(
        "compte rendu d\u2019entretien annuel"
    ) == ("entretien_annuel",)
    assert [action.operation for action in route.workflow_actions] == [
        "generate", "open", "revise", "verify", "deliver",
    ]
    revision = next(
        action for action in route.workflow_actions
        if action.operation == "revise"
    )
    assert revision.target_ordinal == 3


def test_external_revision_is_preserved_but_excluded_from_generation_guidance():
    route = resolve_document_route(
        "Crée un devis et une facture.\n"
        "Ensuite, modifie le contrat de prestation existant.",
        mode="agent",
    )

    assert route.requested_kinds == (
        "devis", "facture", "contrat_prestation",
    )
    assert [item.operation for item in route.items] == [
        "create", "create", "revise",
    ]
    guidance = _document_requested_kinds_guidance(route)
    assert "exactement 2 types" in guidance
    assert "devis, facture" in guidance
    assert "contrat_prestation" not in guidance
    revision = next(
        action for action in route.workflow_actions
        if action.operation == "revise"
    )
    assert revision.target_ordinal == 0


def test_distinct_mixed_item_operations_are_not_flattened():
    route = resolve_document_route(
        "Crée un devis.\nModifie la facture existante.",
        mode="agent",
    )

    assert route.requested_kinds == ("devis", "facture")
    assert [item.operation for item in route.items] == ["create", "revise"]


def test_business_wording_resolves_to_existing_studio_models():
    assert document_kinds_mentioned("procedure d'accueil") == (
        "procedure_operationnelle",
    )
    assert document_kinds_mentioned("compte rendu de reunion") == (
        "proces_verbal",
    )


def test_multi_model_guidance_names_the_exact_batch_without_catalog_dump():
    route = resolve_document_route(AGENCY_OPENING_KIT, mode="agent")

    guidance = _document_requested_kinds_guidance(route)

    assert "11 types structures" in guidance
    assert "generate_studio_documents" in guidance
    assert ", ".join(AGENCY_OPENING_KINDS) in guidance
    assert "create_pdf" in guidance
    assert _document_requested_kinds_guidance(
        resolve_document_route("fais moi un devis", mode="agent")
    ) == ""


def test_single_document_route_remains_backward_compatible():
    route = resolve_document_route("fais moi un devis detaille", mode="agent")

    assert route.kind == "devis"
    assert route.requested_kinds == ("devis",)
    assert len(route.items) == 1


def test_single_structured_document_requires_exact_delivery_truth():
    structured = resolve_document_route("fais moi un devis detaille", mode="agent")
    informative = resolve_document_route("c est quoi un devis", mode="agent")
    assert ReActLoop._document_delivery_truth_required(structured, 1) is True
    assert ReActLoop._document_delivery_truth_required(informative, 1) is False


def test_revision_proof_is_accepted_for_single_document_manifest():
    route = resolve_document_route("modifie ce devis", mode="agent")
    state = SimpleNamespace(
        _document_route=route,
        history=[_step(
            "revise_studio_document",
            args={"document_id": "doc-old"},
            success=True,
        )],
    )
    state.history[0].observation.content = _proof_content("devis")
    manifest, missing, unverified = ReActLoop._structured_document_delivery_manifest(state)
    assert [proof.kind for proof in manifest] == ["devis"]
    assert missing == ()
    assert unverified == ()


@pytest.mark.parametrize(
    ("provided", "canonical"),
    [
        ("bon-de-commande", "bon_commande"),
        ("bulletin-de-paye", "bulletin_paie"),
        ("contrat-prestation", "contrat_prestation"),
        ("accord-de-confidentialite", "nda"),
    ],
)
def test_runtime_aliases_are_canonicalized(provided, canonical):
    assert normalize_document_kind(provided) == canonical


def test_multi_document_gate_stays_closed_until_every_kind_was_attempted():
    route = resolve_document_route(THIRTEEN_DOCUMENTS, mode="agent")
    state = SimpleNamespace(_document_route=route, history=[])

    state.history.append(_step("generate_studio_document", args={"kind": "attestation"}, success=False))
    blocked = ReActLoop._structured_document_tool_gate(state, "create_pdf", {})

    assert blocked is not None
    assert "12" in blocked.content
    assert "bon_commande" in blocked.content

    state.history = [
        _step("generate_studio_document", args={"kind": kind}, success=False)
        for kind in route.requested_kinds
    ]
    assert ReActLoop._structured_document_tool_gate(state, "create_pdf", {}) is None


def test_single_sentence_business_kit_blocks_pdf_fallback_for_all_11_models():
    route = resolve_document_route(AGENCY_OPENING_KIT, mode="agent")
    state = SimpleNamespace(_document_route=route, history=[])

    blocked = ReActLoop._structured_document_tool_gate(state, "create_pdf", {})

    assert blocked is not None
    assert "11" in blocked.content
    assert "contrat_travail" in blocked.content
    assert "facture_proforma" in blocked.content


def test_nested_parallel_fallback_is_blocked_before_studio_attempts():
    route = resolve_document_route(THIRTEEN_DOCUMENTS, mode="agent")
    state = SimpleNamespace(_document_route=route, history=[])

    blocked = ReActLoop._structured_document_tool_gate(
        state,
        "parallel_tools",
        {"tool_calls": [{"name": "create_pdf", "args": {"filename": "one.pdf"}}]},
    )

    assert blocked is not None
    assert "create_pdf" in blocked.content


def test_delivery_progress_counts_real_successes_and_parallel_subresults():
    route = resolve_document_route(THIRTEEN_DOCUMENTS, mode="agent")
    sub_results = tuple(
        SubToolResult(
            tool_name="generate_studio_document",
            success=True,
            content="ok",
            args={"kind": kind},
        )
        for kind in route.requested_kinds[:3]
    )
    state = SimpleNamespace(
        _document_route=route,
        history=[
            _step("parallel_tools", sub_results=sub_results),
            *[_step("create_pdf", success=True) for _ in range(7)],
            _step("create_pdf", success=False),
        ],
    )

    requested, delivered, missing = ReActLoop._structured_document_delivery_progress(state)

    assert requested == 13
    assert delivered == 10
    assert missing == route.requested_kinds[10:]


def _proof_content(kind: str, *, verified: bool = True, page_count: int = 1) -> str:
    return json.dumps({
        "kind": kind,
        "document_id": f"doc_{kind}",
        "filename": f"{kind}-perso.pdf" if kind == "bon_commande" else f"{kind}.pdf",
        "path": f"C:/workspace/documents/{kind}-perso.pdf" if kind == "bon_commande" else f"C:/workspace/documents/{kind}.pdf",
        "sha256": f"sha-{kind}",
        "template_id": kind,
        "format": "pdf",
        "size": 1000,
        "logo_id": "logo_active",
        "render_status": "render_verified" if verified else "render_failed",
        "render_verified": verified,
        "thumbnail_path": f"C:/cache/{kind}.webp",
        "page_count": page_count,
    })


def test_exact_manifest_preserves_requested_order_and_real_filename():
    route = resolve_document_route(THIRTEEN_DOCUMENTS, mode="agent")
    subs = tuple(
        SubToolResult(
            tool_name="generate_studio_document",
            success=True,
            content=_proof_content(kind),
            args={"kind": kind},
        )
        for kind in reversed(route.requested_kinds)
    )
    state = SimpleNamespace(
        _document_route=route,
        history=[_step("parallel_tools", sub_results=subs)],
    )

    manifest, missing, unverified = ReActLoop._structured_document_delivery_manifest(state)

    assert [proof.kind for proof in manifest] == list(route.requested_kinds)
    assert manifest[1].filename == "bon_commande-perso.pdf"
    assert missing == ()
    assert unverified == ()


def test_explicit_minimum_pages_keeps_short_render_uncertified():
    route = resolve_document_route(
        "je veux un contrat de travail d'au moins 6 pages", mode="agent",
    )
    state = SimpleNamespace(
        _document_route=route,
        history=[
            SimpleNamespace(
                action=SimpleNamespace(
                    tool_name="generate_studio_document",
                    tool_args={"kind": "contrat_travail"},
                ),
                observation=Observation(
                    _proof_content("contrat_travail", page_count=2),
                    success=True,
                ),
            )
        ],
    )

    manifest, missing, unverified = ReActLoop._structured_document_delivery_manifest(state)

    assert manifest[0].page_count == 2
    assert missing == ()
    assert unverified == ("contrat_travail",)


def test_business_kit_manifest_and_revision_target_keep_the_sixth_document():
    route = resolve_document_route(AGENCY_OPENING_KIT, mode="agent")
    subs = tuple(
        SubToolResult(
            tool_name="generate_studio_document",
            success=True,
            content=_proof_content(kind),
            args={"kind": kind},
        )
        for kind in reversed(route.requested_kinds)
    )
    state = SimpleNamespace(
        _document_route=route,
        history=[_step("generate_studio_documents", sub_results=subs)],
    )

    manifest, missing, unverified = ReActLoop._structured_document_delivery_manifest(state)
    target = ReActLoop._document_workflow_target(state)

    assert [proof.kind for proof in manifest] == list(AGENCY_OPENING_KINDS)
    assert target.kind == "feuille_temps"
    assert target.document_id == "doc_feuille_temps"
    assert missing == ()
    assert unverified == ()


def test_festival_manifest_keeps_all_11_documents_and_timesheet_target():
    route = resolve_document_route(FESTIVAL_NANTES_WORKFLOW, mode="agent")
    subs = tuple(
        SubToolResult(
            tool_name="generate_studio_document",
            success=True,
            content=_proof_content(kind),
            args={"kind": kind},
        )
        for kind in reversed(route.requested_kinds)
    )
    state = SimpleNamespace(
        _document_route=route,
        history=[_step("generate_studio_documents", sub_results=subs)],
    )

    manifest, missing, unverified = ReActLoop._structured_document_delivery_manifest(state)
    target = ReActLoop._document_workflow_target(state)

    assert [proof.kind for proof in manifest] == list(FESTIVAL_NANTES_KINDS)
    assert len(manifest) == route.requested_count == 11
    assert target.kind == "feuille_temps"
    assert target.document_id == "doc_feuille_temps"
    assert missing == ()
    assert unverified == ()


def test_document_batch_and_verify_tasks_wait_for_manifest():
    assert document_plan_tool_can_complete_task(
        "generate_studio_documents", "Generer le devis", required_kinds=("devis",),
    ) is False
    assert document_plan_tool_can_complete_task(
        "generate_studio_document",
        "Generer les documents attestation, devis et facture",
        tool_kind="attestation",
        required_kinds=("attestation", "devis", "facture"),
    ) is False
    assert document_plan_tool_can_complete_task(
        "generate_studio_document",
        "Verifier que tous les documents sont bien crees",
        tool_kind="attestation",
    ) is False
    assert document_plan_tool_can_complete_task(
        "generate_studio_document", "Generer le devis", tool_kind="devis",
    ) is True


def test_plan_reconciliation_waits_for_each_batch_then_render_verification():
    route = resolve_document_route(THIRTEEN_DOCUMENTS, mode="agent")
    first_task = "Generer attestation, bon de commande, bulletin de paie, contrat, devis, facture, fiche de poste et NDA"
    second_task = "Creer lettre officielle, note interne, proces-verbal, rapport d activite et relance impayee"
    first_group = document_kinds_mentioned(first_task)
    second_group = document_kinds_mentioned(second_task)
    state = SimpleNamespace(
        _document_route=route,
        history=[],
        _task_plan=[
            TaskItem(first_task),
            TaskItem(second_task),
            TaskItem("Verifier que tout est bien cree"),
        ],
        _emit_plan_state=lambda **_kwargs: None,
    )
    state.history = [
        _step(
            "parallel_tools",
            sub_results=tuple(
                SubToolResult(
                    tool_name="generate_studio_document", success=True,
                    content=_proof_content(kind), args={"kind": kind},
                )
                for kind in first_group
            ),
        )
    ]

    assert ReActLoop._reconcile_document_plan_from_manifest(state, 1) == 1
    assert [task.completed for task in state._task_plan] == [True, False, False]

    state.history.append(_step(
        "parallel_tools",
        sub_results=tuple(
            SubToolResult(
                tool_name="generate_studio_document", success=True,
                content=_proof_content(kind), args={"kind": kind},
            )
            for kind in second_group
        ),
    ))
    assert ReActLoop._reconcile_document_plan_from_manifest(state, 2) == 2
    assert all(task.completed for task in state._task_plan)


def test_plan_reconciliation_marks_explicit_batch_and_unused_fallback_honestly():
    route = resolve_document_route(THIRTEEN_DOCUMENTS, mode="agent")
    state = SimpleNamespace(
        _document_route=route,
        history=[_step(
            "generate_studio_documents",
            sub_results=tuple(
                SubToolResult(
                    tool_name="generate_studio_document", success=True,
                    content=_proof_content(kind), args={"kind": kind},
                )
                for kind in route.requested_kinds
            ),
        )],
        _task_plan=[
            TaskItem("Générer le lot via generate_studio_documents"),
            TaskItem("Générer les restants via create_pdf"),
        ],
        _emit_plan_state=lambda **_kwargs: None,
    )

    assert ReActLoop._reconcile_document_plan_from_manifest(state, 3) == 2
    assert state._task_plan[0].completion_status == "created"
    assert state._task_plan[1].completion_status == "not_required"
    assert "fallback non requis" in state._task_plan[1].completion_evidence


def test_latest_retry_replaces_an_unverified_manifest_proof():
    route = resolve_document_route("fais moi un devis detaille", mode="agent")
    state = SimpleNamespace(
        _document_route=route,
        history=[
            _step("generate_studio_document", args={"kind": "devis"}),
            _step("generate_studio_document", args={"kind": "devis"}),
        ],
    )
    state.history[0].observation.content = _proof_content("devis", verified=False)
    state.history[1].observation.content = _proof_content("devis", verified=True)

    manifest, missing, unverified = ReActLoop._structured_document_delivery_manifest(state)

    assert len(manifest) == 1
    assert manifest[0].render_verified is True
    assert missing == ()
    assert unverified == ()


def test_plan_abbreviations_bc_and_pv_resolve_only_in_document_plan_context():
    kinds = ReActLoop._document_plan_required_kinds(
        "Generer le BC puis verifier le PV de reunion"
    )

    assert kinds == ("proces_verbal", "bon_commande")
    assert document_kinds_mentioned("une chaine bc ordinaire") == ()


def test_final_cannot_complete_batch_verification_before_exact_manifest():
    route = resolve_document_route(THIRTEEN_DOCUMENTS, mode="agent")
    state = SimpleNamespace(_document_route=route, history=[])

    assert ReActLoop._document_final_fulfills_plan_task(
        state, "Verifier que tout est bien cree"
    ) is False

    state.history = [
        _step(
            "generate_studio_documents",
            sub_results=tuple(
                SubToolResult(
                    tool_name="generate_studio_document",
                    success=True,
                    content=_proof_content(kind),
                    args={"kind": kind},
                )
                for kind in route.requested_kinds
            ),
        )
    ]
    assert ReActLoop._document_final_fulfills_plan_task(
        state, "Verifier que tout est bien cree"
    ) is True


@pytest.mark.asyncio
async def test_model_list_is_compact_until_a_kind_is_targeted(monkeypatch):
    from src.documents import studio as studio_module
    from src.reasoning.handlers.documents import list_document_models_handler

    manifest = SimpleNamespace(
        id="devis-pro", name="Devis Pro", kind="devis", format="pdf",
        origin="builtin", version=1, description="Modele de devis",
    )
    record = SimpleNamespace(valid=True, manifest=manifest)
    catalog = SimpleNamespace(
        list_templates=lambda: [record],
        read_sample_data=lambda _record: {"numero": "DEV-001", "client": {"name": "Atlas"}},
        get_default=lambda kind, fmt: record,
    )
    monkeypatch.setattr(studio_module, "get_document_studio", lambda: SimpleNamespace(catalog=catalog))

    compact = await list_document_models_handler(None)
    targeted = await list_document_models_handler(None, kind="devis")
    compact_payload = json.loads(compact.output)
    targeted_payload = json.loads(targeted.output)

    assert "sample_data" not in compact_payload["models"][0]
    assert compact_payload["hint"]
    assert targeted_payload["models"][0]["sample_data"]["numero"] == "DEV-001"


@pytest.mark.asyncio
async def test_targeted_unknown_kind_is_a_guided_failure(monkeypatch):
    from src.documents import studio as studio_module
    from src.reasoning.handlers.documents import list_document_models_handler

    manifest = SimpleNamespace(
        id="devis", name="Devis", kind="devis", format="pdf",
        origin="builtin", version=1, description="Modele de devis",
    )
    record = SimpleNamespace(valid=True, manifest=manifest)
    catalog = SimpleNamespace(list_templates=lambda: [record])
    monkeypatch.setattr(
        studio_module, "get_document_studio",
        lambda: SimpleNamespace(catalog=catalog),
    )

    result = await list_document_models_handler(None, kind="kind_inconnu")

    assert result.success is False
    assert "aucun modele pour kind='kind_inconnu'" in result.output
    assert "Types canoniques disponibles: devis" in result.output


@pytest.mark.asyncio
async def test_multi_kind_model_list_returns_exact_ordered_samples(monkeypatch):
    from src.documents import studio as studio_module
    from src.reasoning.handlers.documents import list_document_models_handler

    def record(kind):
        manifest = SimpleNamespace(
            id=kind, name=kind, kind=kind, format="pdf",
            origin="builtin", version=1, description=f"Modele {kind}",
        )
        return SimpleNamespace(valid=True, manifest=manifest)

    records = [record("facture"), record("devis"), record("bon_commande")]
    by_kind = {item.manifest.kind: item for item in records}
    catalog = SimpleNamespace(
        list_templates=lambda: records,
        read_sample_data=lambda item: {
            "kind": item.manifest.kind,
            "party": {"name": f"Exemple {item.manifest.kind}"},
        },
        get_default=lambda kind, _fmt: by_kind.get(kind),
    )
    monkeypatch.setattr(
        studio_module, "get_document_studio",
        lambda: SimpleNamespace(catalog=catalog),
    )

    result = await list_document_models_handler(
        None, kind="devis,facture,bon_commande",
    )
    payload = json.loads(result.output)

    assert result.success is True
    assert [row["kind"] for row in payload["models"]] == [
        "devis", "facture", "bon_commande",
    ]
    assert [row["sample_data"]["kind"] for row in payload["models"]] == [
        "devis", "facture", "bon_commande",
    ]


@pytest.mark.asyncio
async def test_multi_kind_model_list_fails_atomically_on_unknown_kind(monkeypatch):
    from src.documents import studio as studio_module
    from src.reasoning.handlers.documents import list_document_models_handler

    manifest = SimpleNamespace(
        id="devis", name="Devis", kind="devis", format="pdf",
        origin="builtin", version=1, description="Modele devis",
    )
    record = SimpleNamespace(valid=True, manifest=manifest)
    catalog = SimpleNamespace(
        list_templates=lambda: [record],
        get_default=lambda kind, _fmt: record if kind == "devis" else None,
    )
    monkeypatch.setattr(
        studio_module, "get_document_studio",
        lambda: SimpleNamespace(catalog=catalog),
    )

    result = await list_document_models_handler(
        None, kind="devis,kind_inconnu",
    )

    assert result.success is False
    assert "kind_inconnu" in result.output
    assert "Types canoniques disponibles: devis" in result.output


def test_compound_guidance_requests_all_model_contracts_in_one_call():
    route = resolve_document_route(ATELIER_HORIZON_WORKFLOW, mode="agent")

    guidance = _document_requested_kinds_guidance(route)

    assert (
        "list_document_models("
        "kind='devis,facture,bon_commande,proces_verbal,"
        "rapport_activite,lettre_officielle')"
    ) in guidance
    assert "un seul appel" in guidance


def test_batch_tool_contract_describes_atomic_preflight():
    from src.reasoning.handlers.documents import get_documents_handler_defs

    definition = next(
        item for item in get_documents_handler_defs()
        if item.name == "generate_studio_documents"
    )

    assert "preflight valide tout le lot" in definition.description
    assert "aucun document n'est genere" in definition.description
    assert "une erreur n'annule pas les autres" not in definition.description


def test_model_listing_filters_understand_last_custom_models():
    from src.reasoning.handlers.context import HandlerContext
    from src.reasoning.handlers.documents import _model_listing_filters

    ctx = HandlerContext(
        original_user_query=(
            "Génère un document avec chacun de mes quatre derniers modèles personnalisés"
        )
    )

    assert _model_listing_filters(ctx) == ("custom", 4, "recent")
    assert _model_listing_filters(ctx, origin="builtin", limit=2, sort="name") == (
        "builtin", 2, "name",
    )


@pytest.mark.asyncio
async def test_model_list_returns_only_the_latest_custom_models(monkeypatch, tmp_path):
    from src.documents import studio as studio_module
    from src.reasoning.handlers.documents import list_document_models_handler

    def record(model_id, origin, timestamp):
        directory = tmp_path / model_id
        directory.mkdir()
        manifest_path = directory / "manifest.json"
        manifest_path.write_text("{}", encoding="utf-8")
        manifest_path.touch()
        import os
        os.utime(manifest_path, (timestamp, timestamp))
        manifest = SimpleNamespace(
            id=model_id, name=model_id, kind="devis", format="pdf",
            origin=origin, version=1, description=model_id,
        )
        return SimpleNamespace(valid=True, manifest=manifest, directory=directory)

    old = record("custom-old", "custom", 100)
    recent = record("custom-recent", "custom", 300)
    middle = record("custom-middle", "custom", 200)
    builtin = record("builtin", "builtin", 400)
    catalog = SimpleNamespace(
        list_templates=lambda: [old, builtin, recent, middle],
        read_sample_data=lambda _record: {},
        get_default=lambda _kind, _fmt: None,
    )
    monkeypatch.setattr(
        studio_module, "get_document_studio",
        lambda: SimpleNamespace(catalog=catalog),
    )

    result = await list_document_models_handler(
        None, origin="custom", limit=2, sort="recent",
    )
    payload = json.loads(result.output)

    assert [row["id"] for row in payload["models"]] == [
        "custom-recent", "custom-middle",
    ]


@pytest.mark.asyncio
async def test_generation_failure_returns_only_the_targeted_sample(monkeypatch):
    from src.documents import studio as studio_module
    from src.reasoning.handlers.documents import generate_studio_document_handler

    manifest = SimpleNamespace(id="attestation", kind="attestation", format="pdf")
    record = SimpleNamespace(valid=True, manifest=manifest)

    class FakeStudio:
        catalog = SimpleNamespace(
            list_templates=lambda: [record],
            read_sample_data=lambda _record: {"beneficiaire": {"name": "Camille"}, "titre": "Consultant"},
            get_default=lambda kind, fmt: record,
        )

        @staticmethod
        def parse_json_object(value, *, field):
            return json.loads(value)

        async def generate(self, **kwargs):
            raise ValueError("beneficiaire requis")

    monkeypatch.setattr(studio_module, "get_document_studio", lambda: FakeStudio())
    result = await generate_studio_document_handler(None, kind="attestation", data="{}")

    assert result.success is False
    assert "beneficiaire" in result.output
    assert '"name": "Camille"' in result.output
    assert "bulletin_paie" not in result.output


@pytest.mark.asyncio
async def test_single_generation_merges_exact_template_sample(monkeypatch):
    from src.documents import studio as studio_module
    from src.reasoning.handlers.documents import generate_studio_document_handler

    manifest = SimpleNamespace(id="devis-pro", kind="devis", format="pdf")
    record = SimpleNamespace(valid=True, manifest=manifest)

    class FakeStudio:
        def __init__(self):
            self.received = None
            self.catalog = SimpleNamespace(
                read_sample_data=lambda _record: {
                    "client": {"name": "Exemple", "city": "Paris"},
                    "currency": "EUR",
                },
            )

        def resolve_template(self, **kwargs):
            assert kwargs["template_id"] == "devis-pro"
            return record

        @staticmethod
        def parse_json_object(value, *, field):
            return json.loads(value)

        async def generate(self, **kwargs):
            self.received = kwargs
            return {
                "path": "devis.pdf",
                "record": {"id": "doc-1", "filename": "devis.pdf", "size": 10},
                "recipe": {"kind": "devis", "template_id": "devis-pro"},
                "render_proof": {"verified": True, "status": "verified"},
            }

    studio = FakeStudio()
    monkeypatch.setattr(studio_module, "get_document_studio", lambda: studio)
    result = await generate_studio_document_handler(
        SimpleNamespace(is_mission_run=True, runtime_task_id="task_single"),
        kind="devis",
        template_id="devis-pro",
        data=json.dumps({"client": {"name": "Orion"}}),
    )

    assert result.success is True
    payload = json.loads(result.output)
    assert "publish_mission_workspace" in payload["mission_publish_hint"]
    assert "Copy-Item" in payload["mission_publish_hint"]
    assert studio.received["data"] == {
        "client": {"name": "Orion", "city": "Paris"},
        "currency": "EUR",
    }


def test_model_listing_cannot_complete_a_generation_task():
    assert _read_only_discovery_tool_can_complete_task(
        "list_document_models", "Lister les modeles documentaires",
    ) is True
    assert _read_only_discovery_tool_can_complete_task(
        "list_document_models", "Generer la facture finale",
    ) is False
