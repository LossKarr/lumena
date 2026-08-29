"""
🌟 LUMENA - Boucle ReAct

Implémente le pattern ReAct (Reason + Act) pour le raisonnement.
LUMENA peut réfléchir, décider d'agir, observer le résultat, et itérer.
"""

from typing import Dict, Any, List, Optional, Callable, Awaitable, Iterable, Sequence, Tuple
from dataclasses import dataclass, field, replace
from enum import Enum
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
import ast
import asyncio
import json
import hashlib
import os
import re
import platform
import threading
import unicodedata
import subprocess
import difflib
from time import perf_counter
from loguru import logger

# Cancel token registry: thread_id → threading.Event
# Enregistré depuis chat.py avant le démarrage du thread agent.
# Vérifié entre chaque itération pour stopper la boucle sans ctypes.
_REACT_CANCEL_EVENTS: Dict[int, Any] = {}

# ── Stratégie delegate / vérif web post-CodeAgent : extraite dans
#    src/reasoning/delegate_strategy.py (déménagement pur). Re-export pour compat.
from src.reasoning.delegate_strategy import (  # noqa: F401
    _DELEGATE_NOOP_MARKERS, _WEB_DELIVERY_MARKERS, _CANVAS_DELIVERY_MARKERS,
    _CANVAS_NON_TECHNICAL_MARKERS,
    _fold_react_status_text, _delegate_report_has_real_work,
    _post_delegate_web_verify_enabled, _looks_like_web_delegate_delivery,
    _delegate_delivery_expects_canvas, _is_post_codeagent_synthesis_task,
    _is_post_codeagent_conditional_correction_task, _is_post_codeagent_closure_task,
    _candidate_is_web_project, _extract_existing_web_project_path,
    _build_post_delegate_web_verify_success_query, _build_post_delegate_continue_query,
    _verify_report_has_preview_server_mime_error,
    _build_post_delegate_web_verify_failure_query,
)

# ── Imports depuis react_config (constantes, enums, flags) ─────────
from .react_config import (
    ActionType, Thought, Action, Observation, ReActStep, TaskItem,
    IS_WINDOWS, OS_NAME,
    ADVANCED_TOOLS_AVAILABLE, apply_patch, edit_file, parse_patch,
    ContextCompactor, get_token_stats, format_token_stats, estimate_tokens,
    WorkspaceFileGuardrails, get_current_runtime_context,
    TELEMETRY_AVAILABLE, publish_trace, push_trace_context, pop_trace_context,
    current_trace_context, get_file_edits_store, compute_workspace_relative,
    read_text_if_exists,
    _sanitize_llm_output, _PLAN_RE, _TASK_LINE_RE,
    _TOOL_COMPLETION_HINTS, _build_model_specific_hints,
)


from .tool_registry import ToolRegistry
from ..documents.document_intent import (
    DocumentRoute,
    STUDIO_BYPASS_TOOLS,
    normalize_document_kind,
    normalize_document_query,
    resolve_document_route,
)
from .browser_runtime import (
    _browser_content_seen as _rt__browser_content_seen,
    _browser_runtime_failed_for_truth_lock as _rt__browser_runtime_failed_for_truth_lock,
    _browser_runtime_verified_for_truth_lock as _rt__browser_runtime_verified_for_truth_lock,
    _browser_verify_intent as _rt__browser_verify_intent,
    _current_browser_proof as _rt__current_browser_proof,
    _finalize_browser_gate_pending as _rt__finalize_browser_gate_pending,
    _finalize_interaction_gate_pending as _rt__finalize_interaction_gate_pending,
    _local_preview_unprovable_gate as _rt__local_preview_unprovable_gate,
    _mission_browser_verify_pending as _rt__mission_browser_verify_pending,
    _pages_never_opened_reason as _rt__pages_never_opened_reason,
    _post_delegate_web_verify_allowed as _rt__post_delegate_web_verify_allowed,
    _truth_lock_interaction_proven as _rt__truth_lock_interaction_proven,
    EntreeNavigateur as _EntreeNavigateur,
)
from .mission_runtime import (
    EntreeMission as _EntreeMission,
    rf6a_is_mission_run as _mr__is_mission_run,
    rf6a_mission_workspace_meta as _mr__mission_workspace_meta,
    rf6a_mission_unpublished_writes as _mr__mission_unpublished_writes,
    rf6a_mission_routing_objective as _mr__mission_routing_objective,
    rf6a_mission_tests_present_for_gate as _mr__mission_tests_present_for_gate,
    rf6a_mission_web_present_for_gate as _mr__mission_web_present_for_gate,
    rf6a_mission_js_present_for_gate as _mr__mission_js_present_for_gate,
    rf6a_worker_codeagent_first_gate as _mr__worker_codeagent_first_gate,
    rf6a_mission_completion_evidence as _mr__mission_completion_evidence,
    rf6a_mission_allowed_files_meta as _mr__mission_allowed_files_meta,
    rf6a_mission_worker_delivered as _mr__mission_worker_delivered,
    rf6a_mission_lead_delivered as _mr__mission_lead_delivered,
    rf6a_mission_expects_file_deliverables as _mr__mission_expects_file_deliverables,
    rf6a_is_worker_run as _mr__is_worker_run,
    rf6a_is_delegated_worker as _mr__is_delegated_worker,
    rf6b_decision_nudge_ecrits_non_publies as _mr_decision_nudge,
    rf6b_decision_ecrasement_livrable as _mr_decision_ecrasement,
    rf6b_decision_intention_mission_chat as _mr_decision_intention_chat,
)
from .final_delivery_runtime import (
    EntreeFinal as _EntreeFinal,
    rf8_truth_lock_mission_message as _fd__truth_lock_mission_message,
    rf8_truth_lock_web_flag as _fd__truth_lock_web_flag,
    rf8_truth_lock_game_flag as _fd__truth_lock_game_flag,
    rf8_truth_lock_interaction_flag as _fd__truth_lock_interaction_flag,
    rf8b_verdict_a_memoriser as _fd_verdict_a_memoriser,
    rf8b_decision_final_vide as _fd_decision_final_vide,
)
from .document_runtime import (
    _MISSION_PROACTIVE_DOCUMENT_TOOLS,
    _document_final_fulfills_plan_task as _rt__document_final_fulfills_plan_task,
    _document_workflow_pending_action as _rt__document_workflow_pending_action,
    _document_workflow_progress_signature as _rt__document_workflow_progress_signature,
    _force_mission_proactive_document_tools as _rt__force_mission_proactive_document_tools,
    _reconcile_document_plan_from_manifest as _rt__reconcile_document_plan_from_manifest,
    _reconcile_document_workflow_plan as _rt__reconcile_document_workflow_plan,
    _structured_document_tool_gate as _rt__structured_document_tool_gate,
    EntreePorteDocument as _EntreePorteDocument,
    _document_workflow_proof_state as _rt__document_workflow_proof_state,
    _record_document_workflow_evidence as _rt__record_document_workflow_evidence,
    EntreeWorkflowDocument as _EntreeWorkflowDocument,
    _document_delivery_truth_required as _rt__document_delivery_truth_required,
    _document_workflow_target as _rt__document_workflow_target,
    _ensure_document_delivery_reference as _rt__ensure_document_delivery_reference,
    _structured_document_delivery_manifest as _rt__structured_document_delivery_manifest,
    _structured_document_delivery_progress as _rt__structured_document_delivery_progress,
    EntreeLivraisonDocument as _EntreeLivraisonDocument,
    _document_catalog_selection_groups as _rt__document_catalog_selection_groups,
    _document_catalog_selection_models as _rt__document_catalog_selection_models,
    _document_expected_template_ids as _rt__document_expected_template_ids,
    _document_route_for_run as _rt__document_route_for_run,
    _reconcile_document_catalog_plan as _rt__reconcile_document_catalog_plan,
    _record_document_catalog_evidence as _rt__record_document_catalog_evidence,
    EntreeDocumentCatalogue as _EntreeDocumentCatalogue,
    _document_catalog_evidence_key as _rt__document_catalog_evidence_key,
    _document_catalog_rows as _rt__document_catalog_rows,
    _document_open_payload as _rt__document_open_payload,
    _document_parallel_calls as _rt__document_parallel_calls,
    _document_patch_scalar_values as _rt__document_patch_scalar_values,
    _document_paths_match as _rt__document_paths_match,
    _document_plan_required_kinds as _rt__document_plan_required_kinds,
    _document_revision_changed_fields as _rt__document_revision_changed_fields,
    _document_revision_patch as _rt__document_revision_patch,
    _document_tool_events as _rt__document_tool_events,
    _document_verification_text as _rt__document_verification_text,
    _document_web_rights_evidence as _rt__document_web_rights_evidence,
    _duplicate_document_mutation as _rt__duplicate_document_mutation,
    _latest_document_batch_proofs as _rt__latest_document_batch_proofs,
    _merge_mission_document_evidence as _rt__merge_mission_document_evidence,
    _nested_document_bypass as _rt__nested_document_bypass,
    _studio_attempted_kinds as _rt__studio_attempted_kinds,
)
from ..runtime.execution_ledger import (
    ExecutionLedger, MUTATION_TOOLS as _LEDGER_MUTATION_TOOLS,
    INTENT_TO_MUTATION_FAMILY as _LEDGER_INTENT_FAMILIES,
    _extract_target as _ledger_extract_target,
    _extract_proof as _ledger_extract_proof,
)
# ── Anti-hallucination guard : extrait dans src/reasoning/hallucination_guard.py
# Re-export pour compat (react reste le point d'import historique des tests).
from src.reasoning.hallucination_guard import (  # noqa: F401
    _HC_TOOLS_FILE, _HC_TOOLS_DOC, _HC_TOOLS_SITE, _HC_TOOLS_TASK, _HC_TOOLS_MAIL,
    _HC_TOOLS_DISCORD, _HC_TOOLS_MESSAGING, _HC_TOOLS_SOCIAL, _HC_TOOLS_STRIPE,
    _HC_TOOLS_GITHUB, _HC_TOOLS_IMAGE, _HC_TOOLS_NOTION, _HC_TOOLS_RUNTIME, _HC_TOOLS_MCP,
    _HC_TOOLS_ANY_CREATE, _HC_TOOLS_ANY_SEND, _HC_TOOLS_TYPE, _HC_TOOLS_OPEN_APP,
    _HC_TOOLS_CLICK, _HC_TOOLS_LOGIN, _HC_CU_FAMILIES, _HALLUCINATION_CLAIM_PATTERNS,
    _HC_TEMPORAL_BYPASS_RE, _HINT_ONLY_PROOF_REQUIRED_TOOLS, _SERVER_RUNTIME_CLAIM_RE,
    _HC_TOOLS_ANY_ACTION, _HC_TOOLS_READONLY,
    _has_runtime_server_claim_proof, _normalize_guard_text, _strip_accents,
    claim_text_is_negated, claim_match_is_negated, hallucination_retry_query,
)
# ── LEDGER guard : cœur de décision extrait dans src/reasoning/ledger_guard.py
from src.reasoning.ledger_guard import (  # noqa: F401
    _LEDGER_CLAIM_PATTERNS, ledger_text_claims_action,
    compute_effective_successful_tools, extract_h3_target_hint,
    ledger_final_guard_query, ledger_h2_guard_query, ledger_h3_guard_query,
)

# ── RF-1 — helpers navigateur extraits vers `browser_reasoning.py` ──────
# Reexport de compatibilite : 27 de ces symboles sont utilises plus bas dans
# ce fichier, 28 sont importes par d'autres modules ou tests. L'import vit
# ICI et non a l'emplacement des anciennes definitions, car trois constantes
# restees dans react.py les lisent des la ligne 913.
from .browser_reasoning import (  # noqa: F401
    BROWSER_ACTION_TOOLS, BROWSER_SURFACE_TYPES, BROWSER_VISUAL_TOOLS, _BROWSER_AUXILIARY_ACTION_MARKERS,
    _BROWSER_CLICK_ONLY_ROLES, _BROWSER_CLICK_TOOLS, _BROWSER_EVALUATE_ERROR_MARKERS, _BROWSER_EVALUATE_MUTATION_RE,
    _BROWSER_IMPASSE_SIGNALS, _BROWSER_INTERACTION_STATE_KEYS, _BROWSER_LISTING_URL_DOMAINS, _BROWSER_LISTING_URL_PATH_SEGMENTS,
    _BROWSER_SOURCE_PIVOT_MARKERS, _BROWSER_SPA_NOISE_MARKERS, _BROWSER_STATE_READ_TOOLS, _BROWSER_SURFACE_AUTH_FORM_HINTS,
    _BROWSER_SURFACE_AUTH_FORM_URL_SEGMENTS, _BROWSER_SURFACE_AUTH_HINTS, _BROWSER_SURFACE_BUILDER_HINTS, _BROWSER_SURFACE_CHAT_HINTS,
    _BROWSER_SURFACE_CONTACT_ACTION_HINTS, _BROWSER_SURFACE_CONTACT_FORM_HINTS, _BROWSER_SURFACE_DETAIL_PAGE_HINTS, _BROWSER_SURFACE_ERROR_HINTS,
    _BROWSER_SURFACE_FILL_FORM_HINTS, _BROWSER_SURFACE_IFRAME_HINTS, _BROWSER_SURFACE_LISTING_HINTS, _BROWSER_SURFACE_PUBLIC_FORM_HINTS,
    _BROWSER_SURFACE_SEARCH_HINTS, _BROWSER_SURFACE_SPA_SHELL_HINTS, _BROWSER_USER_MUTATION_TOOLS, _LINK_CLICK_RE,
    _READ_SIG_BUCKET, _advance_manual_browser_flow, _browser_click_is_link_navigation, _browser_evaluate_payload,
    _browser_evaluate_proves_interaction, _browser_is_auth_intent, _browser_observation_has_failure, _browser_observation_is_auxiliary_action,
    _browser_observation_looks_like_popup_or_modal, _browser_payload_has_dynamic_state, _browser_progress_delta, _browser_rewrite_human_navigation_action,
    _browser_rewrite_index_like_selector_action, _browser_rewrite_selector_guess_to_index_action, _browser_rewrite_system_typing_action, _browser_rewrite_text_entry_action,
    _browser_rewrite_type_to_click_for_ctrl, _browser_state_fingerprint, _browser_surface_mismatch, _classify_browser_surface,
    _compact_browser_observation_payload, _compute_read_sig, _detect_browser_impasse, _extract_browser_auth_target,
    _extract_browser_form_state, _extract_browser_interactive_count, _extract_browser_textbox_target, _extract_browser_textbox_targets,
    _extract_human_browser_lines, _extract_sendkeys_payload, _legal_browser_source_pivot, _local_preview_loop_decision,
    _looks_like_browser_spa_noise, _looks_like_chat_transcript, _make_browser_progress_signature, _manual_browser_flow_proves_interaction,
    _url_is_local_preview,
)

# ── RF-2 — lecture d'observations extraite vers `observation_synthesis.py` ─
# Reexport de compatibilite : les 7 fonctions sont appelees plus bas dans ce
# fichier ET importees ailleurs dans le depot. L'import vit ICI, en tete,
# comme pour RF-1 : c'est le seul endroit sur avant tout usage.
from .observation_synthesis import (  # noqa: F401
    _PHASE27_MCP_LOOP_TOOLS, _READ_STAGNATION_BUDGET_FLOOR_S, _TABULAR_OBS_MARKERS,
    _TEST_RESULT_RE, _TEST_RESULT_TOOL_NAMES, _obs_looks_like_test_result,
    _obs_looks_tabular, _phase27_mcp_observation_guidance, _should_repair_incomplete_final,
    _synthesize_mission_response_from_evidence, _synthesize_response_from_observation, read_stagnation_action,
    # RF-9a — feuille « ingestion d'observation » + les deux
    # dependances qui ne servaient QU'A ELLE. Reexports de
    # compatibilite (invariants 4 et 12 : meme objet).
    _OBS_FILE_READ_TOOLS,
    _extract_anchor_facts,
    observation_compact_limit,
    compact_observation_body,
    thought_is_stagnant,
    stagnation_tool_hint,
    repeated_listing_reminder,
    plan_stagnation_message,
    web_files_reminder,
    web_files_present,
    phantom_channels,
    workspace_path_from_query,
)

# ── RF-3 — guidance documentaire extraite vers `src/prompts/react_prompt.py`
# Reexport de compatibilite : chacune a un consommateur externe.
from src.prompts.react_prompt import (  # noqa: F401
    _document_requested_kinds_guidance,
    _document_minimum_pages_guidance,
)

# ── Prédicats purs des guards read-only vs mutation (testables) ──────────────


# Verbes d'action (envoi + mutation) reconnus par la règle de négation.
_NEG_ACTION_VERBS: str = (
    "poste|poster|publie|publier|partage|partager|envoie|envoyer|envoi|"
    "anime|animer|cree|creer|modifie|modifier|supprime|supprimer|ecris|ecrire|"
    "genere|generer|exporte|exporter|sauvegarde|sauvegarder|produis|produire|"
    "deploie|deployer|enregistre|enregistrer"
)
# Règle générale de négation : "ne|n' … verbe … rien|aucun|pas|jamais",
# le quantificateur/rien/aucun pouvant précéder ou suivre le verbe.
_NEG_BEFORE_RE = re.compile(
    rf"\bne\b(?:\s+\w+){{0,4}}?\s+(?:{_NEG_ACTION_VERBS})\b"
    rf"(?:\s+\w+){{0,4}}?\s+(?:rien|aucun|aucune|pas|plus|jamais)\b"
)
_NEG_QUANT_VERB_RE = re.compile(
    rf"\b(?:rien|aucun|aucune)\b(?:\s+\w+){{0,4}}?\s+(?:{_NEG_ACTION_VERBS})\b"
)
_NEG_VERB_QUANT_RE = re.compile(
    rf"\b(?:{_NEG_ACTION_VERBS})\b\s+(?:rien|aucun|aucune)\b"
)
_NEG_SANS_RE = re.compile(
    rf"\bsans\b(?:\s+\w+){{0,3}}?\s+(?:{_NEG_ACTION_VERBS})\b"
)
_NEG_AUCUN_MESSAGE_RE = re.compile(r"\baucun(?:e)?\s+message\b")

# M6-colmatage (run MiniQuiz 2026-07-06) — l'OBJECTIF de mission annonce-t-il un
# livrable WEB ? 3e source de `_mission_web_present_for_gate` : une fabrication à
# l'itération 1 (zéro mutation, zéro contrat) laissait `web_deliverable=False` et
# le claim navigateur fabriqué sortait sans bannière. Regex fermée, mission only.
_WEB_OBJECTIVE_RE = re.compile(
    r"(?:\.html?\b|\bflask\b|\bstatic/|\bfrontend\b|\bpage\s+web\b|\bsite\s+web\b"
    r"|\bapplication\s+web\b|\bbrowser_navigate\b|\bnavigateur\b)",
    re.IGNORECASE,
)

# A browser workflow advances one tool action per ReAct iteration. Keep enough
# bounded retries for the common DOM-read -> input -> input -> click -> DOM-read
# sequence,
# while still guaranteeing that a model cannot loop indefinitely.
_MAX_INTERACTION_GATE_SHOTS = 5

# 2.9.A (re-run VentesReport/DevisAPI 2026-07-08) — la regex ci-dessus matchait
# `flask` et `navigateur` NUS : « API Flask SANS interface » et « PAS de navigateur »
# déclenchaient le BROWSER GATE sur des missions explicitement NON-web (2 runs sur 4
# pollués). Deux garde-fous :
#  1) NÉGATION : si l'objectif NIE le navigateur/HTML, le gate ne doit jamais tirer.
#  2) SIGNAL POSITIF : `flask` seul ne suffit PAS ; il faut un vrai signal web
#     (HTML/frontend/site/page/interface web, ou intention navigateur POSITIVE).
_WEB_NEG_RE = re.compile(
    r"pas\s+de\s+navigateur|sans\s+navigateur|aucun\s+navigateur"
    r"|pas\s+de\s+page\s+html|sans\s+html|sans\s+page\s+html"
    r"|sans\s+interface|sans\s+front(?:end)?|sans\s+ui\b"
    # 2.12.B (run tasksapi) — l'objectif ré-écrit disait « Pas d'interface web,
    # uniquement JSON » : ni « sans interface » ni « pas de navigateur » → la
    # négation ratait et `interface web` était lu comme signal POSITIF. On couvre
    # les formes « pas d'interface / sans aucune interface / (en) JSON uniquement ».
    r"|pas\s+d['’]\s*interface|pas\s+d['’]\s*ui\b|pas\s+d['’]\s*front"
    r"|sans\s+aucune?\s+interface|aucune?\s+interface\b"
    r"|json\s+uniquement|uniquement\s+(?:du\s+|en\s+)?json"
    r"|api\s+(?:rest\s+)?(?:flask\s+)?sans\b"
    r"|api\s+seulement|api\s+uniquement"
    r"|valide[rz]?\s+(?:uniquement|seulement)\s+par\s+les\s+tests"
    r"|uniquement\s+par\s+les\s+tests"
    # LOT L1 (audit des 80 contrats réels, 2026-08-14) — la négation n'était
    # comprise qu'à la forme NOMINALE (« pas DE navigateur »). Trois écritures
    # naturelles sur quatre passaient au travers :
    #   « Ne pas utiliser le navigateur »  → True (faux positif)
    #   « n'utilise pas le navigateur »    → True (faux positif)
    #   « sans utiliser de navigateur »    → True (faux positif)
    # Conséquence MESURÉE : sur MotCompteur et TempConv (deux outils EN LIGNE DE
    # COMMANDE dont l'objectif dit « Ne pas utiliser le navigateur »), le BROWSER
    # GATE se déclenchait — relance inutile puis bannière « navigateur non
    # vérifié » sur un livrable qui n'a jamais eu d'interface.
    r"|(?:ne\s+)?pas\s+utiliser\s+(?:le\s+|la\s+|de\s+|d['’]\s*|du\s+)?"
    r"(?:navigateur|browser_navigate|interface|front(?:end)?|ui\b)"
    # L'apostrophe est FACULTATIVE : les objectifs sont écrits à la main et
    # « n utilise pas » / « ne utilise pas » arrivent tels quels. Une négation
    # ratée pour une apostrophe manquante coûterait une bannière abusive.
    r"|n(?:e\b|['’]|\s)\s*utilise[rz]?\s+pas\s+(?:le\s+|la\s+|de\s+|d['’]\s*)?"
    r"(?:navigateur|browser_navigate|interface|front(?:end)?|ui\b)"
    r"|sans\s+utiliser\s+(?:de\s+|d['’]\s*|le\s+|la\s+)?"
    r"(?:navigateur|browser_navigate|interface|front(?:end)?|ui\b)"
    # …et le nom de l'outil NIÉ était lu comme signal POSITIF : la fiche mémo
    # `pyproject.toml` (mission d'EFFETS, zéro fichier) déclenchait sur
    # « utilise web_search_brave, PAS browser_navigate ».
    r"|pas\s+browser_navigate|sans\s+browser_navigate",
    re.IGNORECASE,
)
_WEB_POS_RE = re.compile(
    r"\.html?\b|\bstatic/|\bfrontend\b|\bpage\s+web\b|\bsite\s+web\b"
    r"|\bapplication\s+web\b|\binterface\s+web\b|\bpages?\s+html\b"
    r"|\bsite\s+statique\b|\bbrowser_navigate\b|\bnavigateur\b",
    re.IGNORECASE,
)


def _objective_wants_browser(query) -> bool:
    """2.9.A — l'OBJECTIF de mission demande-t-il RÉELLEMENT une vérif navigateur ?
    Négation d'abord (« pas de navigateur », « API sans interface », « uniquement
    par les tests ») → False sans discuter. Sinon exige un signal web POSITIF
    (`flask`/`navigateur` NUS ne suffisent pas — négation déjà écartée, donc tout
    `navigateur` restant est bien positif). Défensif : False sur entrée vide."""
    q = str(query or "")
    if not q:
        return False
    if _WEB_NEG_RE.search(q):
        return False
    return bool(_WEB_POS_RE.search(q))


def _web_runtime_repair_allowed(
    *,
    failed: bool,
    shots: int,
    iteration: int,
    max_iterations: int,
    max_shots: int = 2,
) -> bool:
    """Return whether a strict local-web failure gets another repair attempt.

    M100.4 keeps this decision deterministic and bounded. Three iterations are
    reserved for inspecting/mutating and running the strict verifier again.
    """
    if not failed or int(shots or 0) >= int(max_shots or 0):
        return False
    return (int(max_iterations or 0) - int(iteration or 0) - 1) >= 3


def _observation_counts_as_recent_failure(
    tool_name: str,
    observation: Optional[Observation],
) -> bool:
    """Return whether an observation is a real tool failure for escalation.

    Document policy refusals happen before tool execution. Counting them as
    runtime failures made a valid Studio workflow escalate after three guided
    retries. Genuine tool failures retain the historical behavior.
    """
    if observation is None or observation.success:
        return False
    if getattr(observation, "origin", "tool") == "document_policy":
        return False
    read_only = {
        "read_file", "list_directory", "find_files", "grep_search",
        "search_in_code", "view_file_outline", "browser_get_content",
        "memory_search", "web_search", "read_own_code",
    }
    if (
        str(tool_name or "") in read_only
        and observation.content
        and len(observation.content) >= 500
    ):
        return False
    return True


def _document_batch_failure_signature(
    observation: Optional[Observation],
    tool_args: Optional[Dict[str, Any]] = None,
) -> tuple[Any, ...] | None:
    """Identify one exact batch attempt so changed input or errors are progress."""
    if observation is None or observation.success:
        return None
    try:
        args_signature = json.dumps(
            tool_args or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            default=str,
        )
    except (TypeError, ValueError):
        args_signature = repr(tool_args or {})
    content = str(observation.content or "").strip()
    try:
        payload = json.loads(content)
    except (TypeError, ValueError, json.JSONDecodeError):
        normalized = re.sub(r"\s+", " ", content).strip().casefold()
        return ("text", args_signature, normalized[:1000])
    if not isinstance(payload, dict) or payload.get("phase") != "preflight":
        normalized = re.sub(r"\s+", " ", content).strip().casefold()
        return ("json", args_signature, normalized[:1000])
    errors = payload.get("errors")
    rows = []
    if isinstance(errors, list):
        for item in errors:
            if not isinstance(item, dict):
                continue
            rows.append((
                int(item.get("index") or 0),
                str(item.get("kind") or "").strip().casefold(),
                re.sub(
                    r"\s+", " ", str(item.get("error") or ""),
                ).strip().casefold(),
            ))
    return (
        "preflight",
        args_signature,
        int(payload.get("requested") or 0),
        int(payload.get("failed") or len(rows)),
        tuple(rows),
    )


def _recent_tool_failure_streak(tool_name: str, history: list[Any]) -> int:
    """Count historical failures, but only identical batch preflights."""
    recent_fails = 0
    latest_batch_signature: tuple[Any, ...] | None = None
    for step in reversed(history[-8:]):
        action = getattr(step, "action", None)
        if getattr(action, "tool_name", "") != tool_name:
            continue
        observation = getattr(step, "observation", None)
        if observation is not None and observation.success:
            break
        if not _observation_counts_as_recent_failure(tool_name, observation):
            continue
        if tool_name == "generate_studio_documents":
            signature = _document_batch_failure_signature(
                observation,
                getattr(action, "tool_args", {}) or {},
            )
            if latest_batch_signature is None:
                latest_batch_signature = signature
            elif signature != latest_batch_signature:
                break
        recent_fails += 1
    return recent_fails


def _repeated_tool_failure_message(tool_name: str, history: list[Any]) -> str:
    """Build a truthful bounded failure without suggesting an unsafe bypass."""
    failures = [
        str(step.observation.content or "")[:500]
        for step in history[-8:]
        if getattr(getattr(step, "action", None), "tool_name", "") == tool_name
        and getattr(step, "observation", None) is not None
        and not step.observation.success
    ]
    last_error = failures[-1] if failures else "inconnue"
    if tool_name == "generate_studio_documents":
        return (
            "⚠️ Aucun document Studio n'a ete genere : le meme preflight "
            "echoue de facon identique.\n\n"
            f"**Derniere erreur :** {last_error}\n\n"
            "Corrige le lot complet avec les `retry_request` fournis et reste "
            "dans Document Studio ; aucun recu ne peut etre annonce."
        )
    return (
        f"⚠️ J'ai essayé {tool_name} plusieurs fois mais ça échoue à chaque fois.\n\n"
        f"**Dernière erreur:** {last_error}\n\n"
        "Je dois reformuler ou utiliser un autre outil."
    )





# 2.6.1 (run MiniQuiz §5) — outils GARANTIS dans le prompt du LEAD de mission web :
# la voie officielle preview (registre SSRF) + le navigateur de preuve. Sans eux,
# le lead sert Flask à la main → SSRF → fabrication (rattrapée, mais run raté).
_MISSION_WEB_LEAD_TOOLS = (
    "serve_website", "start_preview_server", "stop_website_server",
    "browser_navigate", "browser_click", "browser_type",
    "browser_get_content", "browser_screenshot",
)

# A mission remains free to create a useful document even when document
# creation was not the initial intent. Explicit document requests still expose
# the full category; this compact core avoids adding 56 document schemas to
# every worker prompt.


def _has_action_negation(normalized_q: str) -> bool:
    """True si la requête (déjà normalisée) nie une action d'envoi/mutation.

    Couvre "ne poste pas", "ne poste aucun message", "ne poste rien",
    "ne publie rien", "rien envoyer", "sans poster", "aucun message"… via une
    règle générale négation + verbe + (rien|aucun|pas), au lieu d'une liste
    figée de phrases.
    """
    # "n'envoie" → "ne envoie" pour unifier avec la règle "ne … verbe".
    t = normalized_q.replace("n'", "ne ")
    return bool(
        _NEG_BEFORE_RE.search(t)
        or _NEG_QUANT_VERB_RE.search(t)
        or _NEG_VERB_QUANT_RE.search(t)
        or _NEG_SANS_RE.search(t)
        or _NEG_AUCUN_MESSAGE_RE.search(t)
    )




# Verbes d'envoi/post Discord (demande POSITIVE d'action).

# LOT Z6 — plafonds du PLAN GUARD sur le FINAL prématuré.
#
# Run « Écluse » (2026-08-15) : `[PLAN GUARD] FINAL premature bloque: 2/4 taches,
# iteration 66 (retry 18/3)`. Le message affichait « /3 » et le garde avait déjà
# refusé DIX-HUIT fois : la condition était `retries < 3 OR tâches_opérationnelles
# _restantes`, et le second terme n'avait aucune borne. Pire, le filet
# anti-stagnation (qui pose `_plan_guard_retries = 3` pour « empêcher PLAN GUARD de
# bloquer ») était neutralisé par ce même `or` : le filet était posé et ignoré. La
# mission avait pourtant SA preuve — elle avait lu « 5400 s · 90 min · 1.5 h ·
# 0.0625 j » dans le DOM après un clic — mais elle est morte d'épuisement sans
# jamais pouvoir conclure. Ni final, ni tâches faites.
#
# Deux plafonds distincts, tous deux FINIS : une tâche à preuve opérationnelle
# mérite plus d'insistance qu'une tâche métier, pas une insistance infinie. Passé
# le plafond, on laisse conclure — l'honnêteté du bilan est déjà tenue par les
# verrous de vérité (publication, tests verts, constat mesuré), qui eux vérifient
# des PREUVES et non un compteur.
_PLAN_GUARD_MAX_RETRIES: int = 3
_PLAN_GUARD_MAX_RETRIES_OPERATIONAL: int = 8

_DISCORD_SEND_VERBS: tuple = (
    "anime", "animer", "poste", "poster", "envoie", "envoyer", "publie", "publier",
)
# Négations / intentions read-only : la mission demande de NE PAS envoyer.
_DISCORD_SEND_NEGATIONS: tuple = (
    "ne poste pas", "ne postez pas", "n'envoie", "n'envoyez", "sans envoyer",
    "sans poster", "sans rien envoyer", "ne pas envoyer", "ne pas poster",
    "ne rien poster", "n'envoie rien", "sans publier", "ne publie pas",
)
# Verbes positifs de mutation (création / envoi / modification / suppression…).
_MUTATION_VERBS: tuple = (
    "crée", "cree", "créer", "creer", "créé", "envoie", "envoyer", "envoyé",
    "envoye", "poste", "poster", "publie", "publier", "modifie", "modifier",
    "modifié", "supprime", "supprimer", "supprimé", "écris", "écrire", "écrit",
    "ecris", "ecrire", "déploie", "déployer", "deploie", "enregistre",
    "enregistrer", "sauvegarde", "sauvegarder",
)
_MUTATION_NEGATIONS: tuple = (
    "ne crée pas", "ne pas créer", "sans créer", "n'envoie", "ne pas envoyer",
    "sans envoyer", "ne poste pas", "sans poster", "ne modifie pas",
    "ne supprime pas", "sans modifier", "sans supprimer",
)
# Verbes de CRÉATION/EXPORT/ÉCRITURE de document — seuls à transformer un kind
# DOCUMENT en mutation attendue. Lire/analyser/vérifier/résumer reste read-only.
_DOCUMENT_CREATE_VERBS: tuple = (
    "crée", "cree", "créer", "creer", "créé", "génère", "genere", "générer",
    "generer", "généré", "genere", "exporte", "exporter", "écris", "écrire",
    "ecris", "ecrire", "sauvegarde", "sauvegarder", "produis", "produire",
    "produit", "enregistre", "enregistrer",
)


# ── Voix Lumena dans les rails sûrs (repair anti-leak + finalisation déterministe) ──
# COURT VOLONTAIREMENT : une consigne de ton bavarde ré-ouvrirait le THOUGHT leak qu'on
# corrige. On rappelle juste la voix + UNE accroche, puis les données, zéro raisonnement.
_LUMENA_TONE_REPAIR = (
    "🎙️ Garde ta voix Lumena : naturelle, chaleureuse, directe, un brin espiègle, "
    "emojis modérés si utile. Commence par UNE courte phrase d'accroche pour l'utilisateur, "
    "puis livre les données. Ne révèle aucun raisonnement, ne transforme pas ta réponse en "
    "intention."
)


def build_mission_final_message(
    artifact_note: str,
    artifact_title: str,
    *,
    malformed: bool,
    has_green_test: bool = False,
    test_ran_not_green: bool = False,
    tests_expected_not_run: bool = False,
) -> str:
    """Message de clôture DÉTERMINISTE d'une mission (sans LLM → sans leak), mais avec le
    « grain Lumena » : accroche humaine, titre du livrable si connu, chemin/taille, et note
    honnête si le contenu est potentiellement altéré. Pur/testable.

    Honnêteté câblée sur le LEDGER (cf. run taskflow 2026-07-02) :
    - `has_green_test` (pytest VERT réel) → « tests verts » autorisé ;
    - `test_ran_not_green` (un test a tourné mais pas vert) → JAMAIS « vérifié » :
      message honnête « tests non verts, à corriger » ;
    - `tests_expected_not_run` (P0.2, cf. run PollApp multi-worker) : une suite de
      tests EXISTE sur disque (souvent écrite par un worker → hors ledger du lead)
      mais le lead n'a PAS de pytest vert → JAMAIS « vérifié structurellement » :
      « tests présents mais non certifiés par le lead » ;
    - aucun test (tout False) → « vérifié structurellement » (relecture OK),
      jamais « tests verts »."""
    _title = (artifact_title or "").strip()
    _honest_tail = malformed or test_ran_not_green or tests_expected_not_run
    if malformed:
        head = ("✅ C'est fait — livrable écrit ! Petite honnêteté : le format a peut-être "
                "été un peu altéré à l'écriture, jette-y un œil 👀.")
    elif test_ran_not_green:
        head = ("✅ Livrable produit — mais ⚠️ **tests non verts** : son intégration "
                "n'est pas certifiée par les tests, à corriger avant de valider.")
        if _title:
            head += f" (**{_title}**)"
    elif has_green_test:
        head = "✅ C'est fait ! Livrable produit et **tests verts** 🎉"
        if _title:
            head += f" — **{_title}**."
    elif tests_expected_not_run:
        head = ("✅ Livrable produit — mais ⚠️ **tests présents et NON certifiés par moi** "
                "(je ne les ai pas passés verts) : intégration non prouvée, à vérifier.")
        if _title:
            head += f" (**{_title}**)"
    else:
        head = "✅ C'est fait ! Livrable produit et **vérifié structurellement** (relecture OK) 🎉"
        if _title:
            head += f" — **{_title}**."
    parts = [head]
    note = (artifact_note or "").strip()
    if note:
        parts.append(note)
    parts.append(
        "Ouvre-le quand tu veux — dis-moi si tu veux que je l'ajuste. 🙂"
        if not _honest_tail else "Ouvre le fichier pour vérifier le contenu."
    )
    return "\n\n".join(parts)


def discord_requires_send(query: str) -> bool:
    """True si la requête est une demande POSITIVE d'envoi/post sur Discord.

    Faux pour les missions read-only (contrôle, liste, vérifie, statut, rapport)
    car elles ne contiennent pas de verbe d'envoi, et faux si une négation
    explicite est présente ("sans envoyer", "ne poste pas"…).
    """
    q = _strip_accents(query)
    if "discord" not in q:
        return False
    if _has_action_negation(q):
        return False
    if any(_strip_accents(neg) in q for neg in _DISCORD_SEND_NEGATIONS):
        return False
    return any(_strip_accents(v) in q for v in _DISCORD_SEND_VERBS)


def mission_expects_mutation(query: str) -> bool:
    """True si la mission attend une vraie mutation à prouver.

    Mutation attendue = kind mutation-like (DELIVERY / PAYMENT / DEPLOYMENT /
    DOCUMENT) OU verbe positif de mutation non nié. WEB_APP / API / SCRIPT /
    GENERIC seuls ne comptent PAS (peuvent être read-only).
    """
    q = _strip_accents(query)
    # Négation explicite ("sans envoyer", "ne poste aucun", "ne poste rien",
    # "sans créer") → read-only, même si le détecteur de kind matche un verbe nié.
    # Règle générale (négation + verbe + rien/aucun) + filet littéral.
    if _has_action_negation(q):
        return False
    if any(_strip_accents(neg) in q for neg in _MUTATION_NEGATIONS):
        return False
    try:
        kind = detect_verification_kind(query or "")
    except Exception:
        kind = None
    # DELIVERY / PAYMENT / DEPLOYMENT sont toujours des mutations attendues.
    if kind in (VerificationKind.DELIVERY, VerificationKind.PAYMENT,
                VerificationKind.DEPLOYMENT):
        return True
    # DOCUMENT n'est une mutation QUE si la requête demande une vraie
    # création/export/écriture. Lire/analyser/vérifier/résumer un document
    # existant reste read-only.
    if kind == VerificationKind.DOCUMENT:
        return any(_strip_accents(v) in q for v in _DOCUMENT_CREATE_VERBS)
    return any(_strip_accents(v) in q for v in _MUTATION_VERBS)
# ─────────────────────────────────────────────────────────────────────────────

from .agent_execution_state import AgentExecutionState, RunMetaProxy
from .response_parser import (
    parse_response as _parse_response_fn,
    parse_plan as _parse_plan_fn,
    extract_balanced_json,
    parse_action_args as _parse_action_args_fn,
    deliverable_looks_malformed,
    _action_inline_total as _ait_global,
)
from .prompt_builder import (
    is_length_finish_reason, has_unbalanced_delimiters,
    has_unclosed_quotes, ends_with_strong_punctuation,
    is_exploratory_tool, is_single_file_creation_request,
    is_explicit_mission_request,
    is_project_creation_request, is_web_request,
    looks_code_like_or_structured, looks_incomplete_final_answer,
)
from .history_formatter import (
    compute_obs_limit_from_runtime,
    should_protect_observation,
    split_head_tail,
)

# Sanitization, plan regex, tool hints et model hints dans react_config.py


def _generate_project_slug(query: str) -> str:
    """Génère un slug court à partir de la requête utilisateur pour nommer le dossier projet."""
    _NOISE = {
        # Verbes d'action
        "creer", "cree", "creer", "creee", "moi", "fait", "faire", "fais",
        "genere", "generer", "developpe", "ecris", "ecrire", "construis",
        "create", "make", "build", "write", "generate",
        # Articles / pronoms / prépositions
        "un", "une", "le", "la", "les", "de", "du", "des", "pour", "avec",
        "ma", "mon", "mes", "et", "il", "me", "je", "tu", "nous", "vous",
        "qui", "que", "ce", "ca", "se", "sa", "son", "ses", "ta", "ton", "tes",
        "dans", "sur", "en", "pas", "au", "aux", "par", "est", "sont",
        "the", "a", "an", "my", "for", "with", "and", "in", "on",
        # Mots conversationnels FR
        "okay", "ok", "oui", "non", "bah", "bon", "bien", "allez", "aller",
        "vas", "va", "vraiment", "genre", "tiens", "voila", "alors", "donc",
        "mais", "quand", "comment", "juste", "peut", "peux", "veux", "veut",
        "faut", "dois", "doit", "comme", "tout", "tous", "rien", "jamais",
        "pas", "nan", "ouais", "hop", "hein", "quoi", "deja", "encore",
        # Qualificatifs génériques
        "new", "nouveau", "nouvelle", "complet", "complete", "simple",
        "parfait", "petit", "grand", "super", "top", "cool", "beau",
        # Termes génériques projet
        "site", "web", "page", "photos", "photo", "images", "image",
        "dedans", "besoin", "sit", "workspace", "projet", "project",
    }
    text = unicodedata.normalize("NFKD", query.lower())
    text = re.sub(r"[^\w\s]", "", text)
    words = [w for w in text.split() if w not in _NOISE and len(w) > 2]
    slug = "-".join(words[:3]) if words else "project"
    return re.sub(r"[^a-z0-9\-]", "", slug)[:40] or "project"
















from .plan_evidence import (
    _SEQ_FALLBACK_BLOCKLIST,
    _EXPLORATION_TOOLS_STRICT,
    _BUSINESS_ACTION_STARTERS,
    _BUSINESS_ACTION_STARTERS_NORMALIZED,
    _normalize_guard_token,
    _VERIFY_TASK_KEYWORDS,
    _VERIFY_PROOF_TOOLS,
    _VERIFY_OBS_PROOF_MARKERS,
    classify_observation,
    is_verify_task,
    verify_satisfied_by_artifact_read,
    has_sufficient_proof,
    evaluate_task_proof,
    reconcile_delegate_report,
    reconcile_plan_on_artifact_delivery,
    task_completion_status,
    tool_capabilities_are_known_readonly,
    detect_verification_kind,
    VerificationKind,
    is_peer_delegation_success as _is_peer_delegation_success,
)



# Token → frozenset pour lookup rapide sans parcourir la liste à chaque appel
_BROWSER_IMPASSE_TOKEN_SET: frozenset = frozenset(
    token for token, _reason, _dismiss in _BROWSER_IMPASSE_SIGNALS
)











# ── PLAN progress : helpers de complétion extraits dans src/reasoning/plan_progress.py
from src.reasoning.plan_progress import (  # noqa: F401
    _BROWSER_PLAN_PASSIVE_TOOLS, _READ_ONLY_DISCOVERY_PLAN_TOOLS,
    _browser_passive_tool_can_complete_task, _read_only_discovery_tool_can_complete_task,
    _SYNTH_KW, _SYNTH_SIDE_EFFECT_BLOCK_KW, final_fulfills_task,
    final_requires_operational_proof,
    is_mission_tracking_task, mission_progress_proven, delegation_task_fulfilled,
    delegation_task_blocks,
    mission_deliverable_finalizable, mission_evidence_finalizable,
    worker_evidence_finalizable,
    correction_task_blocks_readonly,
    pytest_execution_task, pytest_plan_task_proven,
    tool_explicit_task_blocks, publish_task_blocks, browser_verify_task_blocks,
    browser_interaction_task_blocks, artifact_target_task_blocks,
    document_plan_tool_can_complete_task, document_workflow_task_blocks,
    document_catalog_task_origin, document_workflow_task_operation,
    sourced_web_research_task_proven,
)



















































# ── LOT Z20 — une action en attente ne survit pas à ce qui rebâtit la page ───
#
# Run « Créneau » (2026-08-17). Déroulé exact, au log :
#
#   05:54:18  clic "Enregistrer"  → sans effet (le JS n'était pas initialisé)
#             `local_preview_mutation_since_read = True`
#             … diagnostic, correctif de lecons.js, republication, rechargement …
#   05:58:51  lecture DOM → empreinte DIFFÉRENTE → [BROWSER INTERACTION PROOF]
#   05:58:56  MISSION FINALIZE
#
# L'action en attente a survécu 4 minutes, 12 itérations, une correction de code,
# une republication et un rechargement — puis elle a été appariée à une différence
# de DOM causée par LA RÉPARATION, pas par elle. La mission a conclu « vérifié »
# sur un site dont elle n'avait rien vérifié : ni élève enregistré, ni leçon créée.
#
# Cause structurelle : `_advance_manual_browser_flow` vit dans un bloc gardé par
# `if _is_browser_tool:`. Un `delegate_task`, un `publish_mission_workspace`, un
# `edit_file` n'y entrent JAMAIS — l'attente ne pouvait donc pas y être annulée.
#
# La règle appliquée ici est celle que le ledger tient déjà pour la preuve
# voisine (`has_fresh_browser_action` : « la preuve navigateur date-t-elle
# d'APRÈS la dernière mutation de source ? »). Elle n'avait simplement jamais
# été étendue à la preuve d'interaction.
_INTERACTION_PROOF_INVALIDATORS = frozenset({
    # le programme sous test a pu changer
    "write_file", "edit_file", "create_file", "apply_patch", "apply_patches",
    "insert_at_anchor", "edit_by_lines", "edit_lines", "str_replace",
    "multi_edit_file", "write_website_files", "edit_website",
    # le code a pu être réécrit par un sous-agent (cas Créneau : le correctif
    # est passé par le CodeAgent, invisible du ledger de source du lead)
    "delegate_task", "delegate_task_bg", "delegate_and_wait",
    # les fichiers SERVIS ont changé sous la page ouverte
    "publish_mission_workspace",
    # la page a été rebâtie sur décision de l'agent (une navigation PROVOQUÉE par
    # une soumission apparaît dans l'écho du clic, pas comme un appel séparé)
    "browser_navigate", "browser_back", "browser_forward", "browser_refresh",
})

















# Outils d'action (interactions) → incrémentent le blind streak
BROWSER_SELF_VISUAL_ACTION_TOOLS: frozenset = frozenset({
    "browser_navigate",
    "browser_click",
    "browser_click_index",
    "browser_click_smart",
    "browser_type_index",
})


# LOT Z23 — outils fermés une fois l'interactif jugé NON PROUVABLE sur une
# preview locale. C'est ce verrou qui remplace le `return` : il casse la boucle
# d'inspection (raison d'être du garde, run memo) sans tuer la mission. Les
# outils d'ACTION restent ouverts — on ferme la relecture, pas le travail.
_LP_UNPROVABLE_CLOSED_TOOLS: frozenset = BROWSER_VISUAL_TOOLS | frozenset({
    "browser_evaluate",
})

# Outils système vers lesquels le LLM peut dériver après un blocage browser
_BROWSER_DRIFT_TOOLS: frozenset = frozenset({
    "run_command", "run_shell", "exec_command", "web_fetch", "curl",
})






# V2.1 fix prod 2026-05-19 (rev 2) : marqueurs d'INTENTION dans un thought/réponse.
# Si le texte contient principalement ces phrases, ce n'est PAS un livrable mais une
# promesse de livrable. Le LLM dit qu'il VA faire, pas qu'il A fait.
# ── Helpers purs des guards finaux : extraits dans src/reasoning/final_guards.py
from src.reasoning.final_guards import (  # noqa: F401
    _INTENTION_MARKERS, _DELIVERABLE_MARKERS, _INTERNAL_PREFIXES,
    _looks_like_intention, strip_thought_leak_prefix, remask_secrets,
    extract_mission_deliverable, apply_mission_truth_lock,
)




















# PG-1.a (run SkiLoc 2026-07-12) — outils dont un SUCCÈS est une PROGRESSION
# réelle même si le plan TODO ne bouge pas : les tâches restantes du plan
# peuvent être verrouillées (PUBLISH-ONLY/BROWSER-ONLY) pendant que le lead
# débogue légitimement. SkiLoc : reset_data ajouté + conftest.py créé = « aucune
# progression » → FINAL forcé avec 2 048 s de budget restant, à une itération de
# la victoire. Une écriture réussie remet le compteur de stagnation à ZÉRO.
_PG1_MUTATION_TOOLS = frozenset({
    "write_file", "edit_file", "edit_by_lines", "apply_patch", "apply_patches",
    "insert_at_anchor", "str_replace", "multi_edit_file", "create_file",
    "delete_file", "create_directory",
    "publish_mission_workspace", "serve_website", "start_preview_server",
    "write_mission_contract",
    "generate_studio_documents",
})





# LOT P2b — un livrable DÉJÀ LIVRÉ ne se réécrit pas en place depuis une mission.
# Mesuré sur les écritures réelles : 23 sur 104 (22 %) visent `workspace/<projet>`
# hors dossier de mission, dont les 3 écrasements successifs de
# `workspace/huffpack/huffpack/core.py` — un projet publié et vert, cassé en une
# heure, sans filet et sans que l'utilisateur en soit informé.
#
# Le critère est FACTUEL, jamais lexical (leçon du LOT N2) : le fichier existe-t-il
# déjà sur le disque, hors du dossier de cette mission ? Créer un fichier NEUF
# reste libre — les missions d'effets (PDF, CSV, page web nouvelle) ne sont pas
# concernées. Redirection, pas blocage : au second appel, l'écriture passe.
_P2B_WRITE_TOOLS: frozenset = frozenset({
    "write_file", "edit_file", "edit_by_lines", "apply_patch", "apply_patches",
    "insert_at_anchor", "str_replace", "multi_edit_file",
})


def mission_write_path_exists(path_str: str, *, workspace_root: Any = None) -> bool:
    """Le fichier visé existe-t-il déjà, quel que soit l'ancrage du chemin ?

    LOT P2b-bis (run HuffPack v4, 2026-08-14) — le garde s'est TU alors que la
    mission écrivait 4 fois dans `workspace/huffpack/huffpack/core.py`, et le
    livrable est ressorti cassé (5 passed, 12 failed). Cause : je préfixais un
    chemin DÉJÀ préfixé —

        default_workspace_root : …/lumena/workspace
        chemin du modèle       : workspace/huffpack/huffpack/core.py
        mon calcul             : …/workspace/workspace/huffpack/…  → absent
        le vrai fichier        : …/workspace/huffpack/…            → présent

    Les modèles écrivent tantôt `workspace/x/y.py`, tantôt `x/y.py`, tantôt un
    chemin absolu. On teste donc les ancrages plausibles : un fichier trouvé sous
    N'IMPORTE lequel est un fichier qui existe.
    """
    target = str(path_str or "").strip()
    if not target:
        return False
    try:
        probe = Path(target)
        if probe.is_absolute():
            return probe.is_file()
    except Exception:
        return False
    try:
        project_root = Path(__file__).resolve().parents[2]
    except Exception:
        project_root = None
    candidates = []
    for base in (
        workspace_root,
        project_root,
        (project_root / "workspace") if project_root else None,
    ):
        if not base:
            continue
        try:
            candidates.append(Path(base) / target)
        except Exception:
            continue
    for cand in candidates:
        try:
            if cand.is_file():
                return True
        except Exception:
            continue
    return False


def mission_write_targets_existing_deliverable(
    path_str: str,
    mission_workspace: str,
    *,
    exists: bool,
) -> bool:
    """True si l'écriture vise un fichier EXISTANT hors du dossier de mission.

    Pur : l'appelant fournit `exists` (le seul accès disque). `mission_workspace`
    vide → False (hors mission, ou mission sans dossier : aucun effet).
    """
    if not exists:
        return False
    ws = str(mission_workspace or "").replace("\\", "/").strip().strip("/")
    if not ws:
        return False
    target = str(path_str or "").replace("\\", "/").strip()
    if not target:
        return False
    return ws not in target


def _entree_porte_document(etat) -> "_EntreePorteDocument":
    """Lot RF-5d2 — raccord de compatibilite pour la porte documentaire.

    FONCTION DE MODULE, comme les trois precedentes.

    Zero mutation : ce sous-lot n'en a plus. Les trois appelables propres
    correspondent aux acces que les entrees precedentes ne couvraient pas.

    `_task_plan` et `_is_mission_run` sont lus ici sous leur forme STRICTE —
    celle qui leve si l'attribut manque — parce que c'est ce que faisait le
    corps d'origine a ces endroits precis. Les formes gardees existent en
    parallele dans l'entree catalogue : une seule ne peut pas rendre les deux.
    """
    return _EntreePorteDocument(
        workflow=_entree_workflow_document(etat),
        obtenir_plan_strict=lambda: etat._task_plan,
        est_run_mission_strict=lambda: etat._is_mission_run,
        obtenir_outils=lambda: etat.tools,
    )


def _entree_workflow_document(etat) -> "_EntreeWorkflowDocument":
    """Lot RF-5d1 — raccord de compatibilite pour les racines du workflow.

    FONCTION DE MODULE, comme les deux precedentes : les tests appellent ces
    methodes SUR LA CLASSE avec un sac d'etat quelconque.

    L'entree PORTE celle de la livraison, qui porte celle du catalogue. Chaque
    sous-lot garde son contrat : les trois ecritures du truth-lock restent
    celles de RF-5c, et seule la QUATRIEME mutation — le magasin de preuves de
    workflow — est ajoutee ici.
    """
    def _definir_preuves_workflow(valeur) -> None:
        # Quatrieme et derniere mutation de la famille documentaire
        # (invariant 5 : elle reste portee par `react.py`).
        etat._document_workflow_evidence = valeur

    return _EntreeWorkflowDocument(
        livraison=_entree_livraison_document(etat),
        # Deux defauts differents pour le MEME attribut : `None` ici, `{}` dans
        # l'entree de livraison. Une seule valeur ne peut pas rendre les deux —
        # troisieme occurrence du motif apres le ledger (RF-4) et le catalogue
        # (RF-5b).
        obtenir_preuves_workflow_ou_none=lambda: getattr(
            etat, "_document_workflow_evidence", None),
        definir_preuves_workflow=_definir_preuves_workflow,
    )


def _entree_livraison_document(etat) -> "_EntreeLivraisonDocument":
    """Lot RF-5c — raccord de compatibilite pour la verite de livraison.

    FONCTION DE MODULE, comme `_entree_document_catalogue` : les tests
    appellent ces methodes SUR LA CLASSE avec un sac d'etat quelconque. En
    faire une methode a coute 80 tests rouges en RF-5b ; la lecon est acquise.

    L'entree PORTE celle du catalogue (RF-5b) au lieu de l'elargir : chaque
    sous-lot garde son contrat, et les 14 champs figes de
    `EntreeDocumentCatalogue` ne bougent pas.
    """
    def _definir_reference_id(valeur) -> None:
        # Les TROIS ecritures du truth-lock restent portees ici (invariant 5).
        etat._document_delivery_reference_id = valeur

    def _definir_reference_signature(valeur) -> None:
        etat._document_delivery_reference_signature = valeur

    def _definir_cible_workflow(valeur) -> None:
        etat._document_workflow_target_proof = valeur

    return _EntreeLivraisonDocument(
        catalogue=_entree_document_catalogue(etat),
        obtenir_historique=lambda: getattr(etat, "history", []),
        obtenir_preuves_workflow=lambda: getattr(
            etat, "_document_workflow_evidence", {}),
        obtenir_reference_id=lambda: getattr(
            etat, "_document_delivery_reference_id", ""),
        definir_reference_id=_definir_reference_id,
        obtenir_reference_signature=lambda: getattr(
            etat, "_document_delivery_reference_signature", ()),
        definir_reference_signature=_definir_reference_signature,
        obtenir_cible_workflow=lambda: getattr(
            etat, "_document_workflow_target_proof", None),
        definir_cible_workflow=_definir_cible_workflow,
    )


def _entree_document_catalogue(etat) -> "_EntreeDocumentCatalogue":
    """Lot RF-5b — raccord de compatibilite, construit une fois pour six.

    FONCTION DE MODULE, et non methode de `ReActLoop` : c'est essentiel.
    Les tests du depot appellent ces six methodes SUR LA CLASSE, en passant un
    sac d'etat quelconque :

        route = ReActLoop._document_route_for_run(state)   # state = SimpleNamespace

    Une premiere version faisait de cette fabrique une methode appelee par
    `self._entree_document_catalogue()` : 28 tests sont tombes sur
    `'SimpleNamespace' object has no attribute '_entree_document_catalogue'`.
    Elle ne touche donc `etat` que par `getattr`/`setattr`, comme le corps
    d'origine, et le duck-typing des 196 sites d'appel est preserve.

    Une autre version reconstruisait l'entree dans chacune des six coquilles :
    `react.py` GAGNAIT 85 lignes la ou le lot devait lui en faire perdre. Le
    raccord est donc factorise (invariant 11 : « les raccords de compatibilite
    strictement necessaires sont autorises »).

    Toutes les lectures sont PARESSEUSES : `runtime_ctx`, `task_id`,
    `task_orchestrator`, `_original_query` et `_task_plan` sont absents des
    boucles construites par `object.__new__`, et le corps d'origine ne les
    atteignait jamais sur ces scenarios.
    """
    def _definir_route(valeur) -> None:
        # Les DEUX mutations du sous-lot restent portees ici (invariant 5) :
        # `react.py` reste seul proprietaire de `_document_route` et
        # `_document_catalog_evidence`.
        etat._document_route = valeur

    def _definir_preuves(valeur) -> None:
        etat._document_catalog_evidence = valeur

    return _EntreeDocumentCatalogue(
        obtenir_runtime_ctx=lambda: getattr(etat, "runtime_ctx", None),
        est_run_mission=lambda: getattr(etat, "_is_mission_run", False),
        obtenir_task_id=lambda: getattr(etat, "task_id", None),
        obtenir_orchestrateur=lambda: getattr(etat, "task_orchestrator", None),
        obtenir_requete_originale=lambda: getattr(etat, "_original_query", ""),
        obtenir_historique=lambda: getattr(etat, "history", []),
        obtenir_plan=lambda: getattr(etat, "_task_plan", None),
        obtenir_route_cache=lambda: getattr(etat, "_document_route", None),
        definir_route_cache=_definir_route,
        # Deux defauts differents pour le MEME attribut : une seule valeur ne
        # peut pas rendre les deux (motif du ledger de RF-4).
        obtenir_preuves_catalogue=lambda: getattr(
            etat, "_document_catalog_evidence", None),
        obtenir_preuves_catalogue_ou_vide=lambda: getattr(
            etat, "_document_catalog_evidence", {}),
        definir_preuves_catalogue=_definir_preuves,
        # Sortie vers la famille MISSION (RF-6, bloquee par le §18).
        objectif_routage_mission=lambda: ReActLoop._mission_routing_objective(etat),
        emettre_etat_plan=lambda **kw: etat._emit_plan_state(**kw),
    )


def _entree_navigateur(etat) -> "_EntreeNavigateur":
    """Lot RF-7a — raccord de compatibilite du runtime navigateur.

    FONCTION DE MODULE (lecon RF-5b : en faire une methode a coute 80 tests).
    ZERO mutation : ce sous-lot n'en a aucune.

    --- Le DISPATCH D'INSTANCE est preserve ---

    Les six appelables du bloc « appels internes » redescendent sur `etat`, et
    non vers la fonction du module. C'est essentiel : les tests du depot
    monkeypatchent l'INSTANCE —

        r._mission_browser_verify_pending = lambda note, q: "livrable web"

    Un appel direct au module court-circuiterait ce patch en silence. Le
    fichier de tests de RF-1 avait nomme ce risque mot pour mot ; une premiere
    version de ce lot l'a reproduit et 17 tests sont tombes.

    REGLE : `self.X(...)` -> redescend sur l'instance.
            `Classe.X(self)` -> appel direct autorise (c'etait deja le cas).

    --- Les DEUX formes de `_is_mission_run` ---

    `est_run_mission()` tolere l'absence (forme `getattr`),
    `est_run_mission_strict()` LEVE (forme directe). Une seule ne peut pas
    rendre les deux — sixieme occurrence du motif dans ce chantier.
    """
    return _EntreeNavigateur(
        obtenir_ledger=lambda: etat.execution_ledger,
        obtenir_historique=lambda: etat.history,
        obtenir_task_id=lambda: etat.task_id,
        obtenir_orchestrateur=lambda: etat.task_orchestrator,
        obtenir_exec_state=lambda: getattr(etat, "exec_state", None),
        tirs_gate_navigateur=lambda: getattr(etat, "_browser_gate_shots", 0),
        tirs_gate_interaction=lambda: getattr(etat, "_interaction_gate_shots", 0),
        url_page_courante=lambda: getattr(etat, "_last_browser_page_url", ""),
        url_preview_indemontrable=lambda: getattr(etat, "_lp_unprovable_url", ""),
        marqueur_echec_runtime=lambda: getattr(etat, "_web_runtime_failed", None),
        marqueur_verifie_runtime=lambda: getattr(etat, "_web_runtime_verified", None),
        est_run_mission=lambda: getattr(etat, "_is_mission_run", False),
        est_run_mission_strict=lambda: etat._is_mission_run,
        # Dispatch d'instance PRESERVE — voir la docstring.
        pages_jamais_ouvertes=lambda: etat._pages_never_opened_reason(),
        interaction_prouvee=lambda: etat._truth_lock_interaction_proven(),
        preuve_navigateur_courante=lambda: etat._current_browser_proof(),
        intention_verif_navigateur=lambda *a, **k: etat._browser_verify_intent(*a, **k),
        verif_navigateur_mission=lambda *a, **k: etat._mission_browser_verify_pending(*a, **k),
        runtime_verifie_truth_lock=lambda: etat._browser_runtime_verified_for_truth_lock(),
        # Ces quatre-la etaient AUSSI en forme `self.X()` : meme regle, meme
        # dispatch d'instance. Les appeler en forme CLASSE court-circuiterait
        # les monkeypatchs de `test_m101_interaction_authority`, qui posent
        # `loop._truth_lock_interaction_flag = lambda: True` sur l'instance.
        est_run_worker=lambda: etat._is_worker_run(),
        web_present_pour_gate=lambda: etat._mission_web_present_for_gate(),
        drapeau_interaction=lambda: etat._truth_lock_interaction_flag(),
        drapeau_jeu=lambda: etat._truth_lock_game_flag(),
        outils_fermes_preview=lambda: _LP_UNPROVABLE_CLOSED_TOOLS,
        max_tirs_gate_interaction=lambda: _MAX_INTERACTION_GATE_SHOTS,
    )


def _entree_mission(etat) -> "_EntreeMission":
    """Instantane d'etat pour `mission_runtime.py` (lot RF-6a).

    TOUT est paresseux : les tests du depot construisent
    `object.__new__(ReActLoop)`, ou ces attributs sont ABSENTS. Precalculer une
    valeur avait fait tomber 54 tests en RF-4, par `AttributeError` levee avant
    tout garde.

    Les appels redescendent sur l'INSTANCE : un appel direct de module ferait
    perdre les monkeypatchs d'instance des tests, **en silence** (17 tests
    tombes en RF-7a).
    """
    return _EntreeMission(
        task_id=lambda: etat.task_id,
        orchestrateur=lambda: etat.task_orchestrator,
        ledger=lambda: etat.execution_ledger,
        plan_taches=lambda: etat._task_plan,
        # ── LE MOTIF DES DEUX FORMES, 7e occurrence ──
        # forme TOLERANTE (`getattr(..., False)`) et forme STRICTE
        # (`etat._is_mission_run`) ne sont PAS equivalentes : sur un etat
        # incomplet la property leve sur `self.task_id`, hors du try.
        est_run_mission=lambda: getattr(etat, "_is_mission_run", False),
        est_run_mission_strict=lambda: etat._is_mission_run,
        requete_originale=lambda: getattr(etat, "_original_query", ""),
        # ── dispatch d'instance PRESERVE ──
        fichiers_autorises=lambda: etat._mission_allowed_files_meta(),
        tests_presents=lambda: etat._mission_tests_present_for_gate(),
        est_worker_delegue=lambda: etat._is_delegated_worker(),
        orchestrateur_actif=lambda: etat._orchestrator_enabled(),
        preuve_tests_verts=lambda: etat._current_green_test_proof(),
        preuve_navigateur=lambda: etat._current_browser_proof(),
        drapeau_web=lambda: etat._truth_lock_web_flag(),
        drapeau_interaction=lambda: etat._truth_lock_interaction_flag(),
        drapeau_jeu=lambda: etat._truth_lock_game_flag(),
        interaction_prouvee=lambda: etat._truth_lock_interaction_proven(),
        objectif_veut_navigateur=lambda q: _objective_wants_browser(q),
        # ── RF-6b : lectures propres aux trois gates ──
        ecrits_non_publies=lambda: etat._mission_unpublished_writes(),
        dossier_mission=lambda: etat._mission_workspace_meta(),
        outils_ecriture_p2b=lambda: _P2B_WRITE_TOOLS,
        chemin_ecriture_existe=lambda cible: mission_write_path_exists(
            cible,
            workspace_root=getattr(etat.tools, "default_workspace_root", None),
        ),
        vise_livrable_existant=lambda cible, ws, existe:
            mission_write_targets_existing_deliverable(cible, ws, exists=existe),
    )


def _entree_final(etat) -> "_EntreeFinal":
    """Instantane d'etat pour `final_delivery_runtime.py` (lot RF-8).

    TOUT est paresseux (lecon RF-4) et les appels redescendent sur l'INSTANCE
    (lecon RF-7a). `noter_verdict` en particulier MUTE `_run_meta` : elle doit
    imperativement passer par l'objet, sinon la mutation se perd en silence.
    """
    return _EntreeFinal(
        task_id=lambda: etat.task_id,
        ledger=lambda: etat.execution_ledger,
        requete_originale=lambda: getattr(etat, "_original_query", "") or "",
        est_run_mission=lambda: etat._is_mission_run,
        pont_codex=lambda: getattr(etat, "_codex_tool_bridge_run", False),
        est_run_worker=lambda: etat._is_worker_run(),
        web_present=lambda: etat._mission_web_present_for_gate(),
        preuve_tests_verts=lambda: etat._current_green_test_proof(),
        preuve_navigateur=lambda: etat._current_browser_proof(),
        tests_non_lances=lambda: etat._tests_present_but_not_run(),
        attend_des_fichiers=lambda: etat._mission_expects_file_deliverables(),
        ecrits_non_publies=lambda: etat._mission_unpublished_writes(),
        preuve_serveur=lambda: etat._server_started_proof(),
        dom_observe=lambda: etat._browser_content_seen(),
        interaction_prouvee=lambda: etat._truth_lock_interaction_proven(),
        drapeau_interaction=lambda: etat._truth_lock_interaction_flag(),
        drapeau_jeu=lambda: etat._truth_lock_game_flag(),
        drapeau_web=lambda: etat._truth_lock_web_flag(),
        navigateur_en_panne=lambda: etat._browser_runtime_failed_for_truth_lock(),
        # MUTE `_run_meta` : dispatch d'instance OBLIGATOIRE.
        noter_verdict=lambda info: etat._note_truth_lock_outcome(info),
        # `__file__` designerait le module extrait : la racine reste celle
        # calculee depuis `react.py`.
        racine_projet=lambda: Path(__file__).resolve().parents[2],
        # `build_mission_final_message` vit DANS `react.py` : elle passe
        # par l'entree (invariant 2).
        construire_bilan_mission=lambda *a, **k: build_mission_final_message(*a, **k),
    )


class ReActLoop:
    """
    Boucle de raisonnement ReAct pour LUMENA.

    Pattern: Think → Act → Observe → (Repeat or Answer)
    """
    
    def __init__(
        self,
        llm_chat_func: Optional[Callable] = None,
        tools: Optional[ToolRegistry] = None,
        conversation_context: str = "",
        active_skills_context: str = "",
        llm_meta_getter: Optional[Callable[[], Dict[str, Any]]] = None,
        max_final_repair_attempts: int = 1,
        task_orchestrator: Optional[Any] = None,
        task_id: Optional[str] = None,
        is_weak_model: bool = False,
        step_callback: Optional[Callable[[str, dict], None]] = None,
        runtime_ctx: Optional[Any] = None,
        max_iterations: Optional[int] = None,
        document_route: Optional[DocumentRoute] = None,
    ):
        """
        Args:
            llm_chat_func: Fonction async qui prend des messages et retourne une réponse
            tools: Registre des outils disponibles
            conversation_context: Contexte des échanges précédents pour les requêtes de suivi
            active_skills_context: Skills auto-selectionnes pour cette requete
            max_iterations: Override du nombre max d'itérations (None = défaut env/35)
        """
        if llm_chat_func is None:
            async def _fallback_llm_chat(_messages):
                return (
                    "THOUGHT: Aucun moteur LLM fourni dans ReActLoop.\n"
                    "ACTION: FINAL\n"
                    "ACTION_INPUT: Configuration incomplète: llm_chat_func est requis pour exécuter des actions."
                )
            self.llm_chat = _fallback_llm_chat
        else:
            self.llm_chat = llm_chat_func
        self.tools = tools or ToolRegistry()
        self.history: List[ReActStep] = []
        _resolved = max_iterations if max_iterations is not None else self._resolve_max_iterations()
        self.max_iterations = _resolved
        self.timeout_seconds = self._resolve_timeout_seconds()
        self.conversation_context = conversation_context  # Pour les requêtes de suivi
        self.active_skills_context = active_skills_context
        self.action_history: List[tuple] = []  # Pour détecter les actions répétées
        self.llm_meta_getter = llm_meta_getter
        self.max_final_repair_attempts = max(0, int(max_final_repair_attempts))
        self.task_orchestrator = task_orchestrator
        self.task_id = (task_id or "").strip() or None
        self.is_weak_model = bool(is_weak_model)
        self.step_callback: Optional[Callable[[str, dict], None]] = step_callback
        self.runtime_ctx = runtime_ctx  # RuntimeContext snapshot (Phase 2)
        self._document_route = document_route
        # ── Plan TODO ──
        self._task_plan: List[TaskItem] = []
        self._plan_emitted: bool = False
        self._plan_last_emit_state: str = ""  # dédup: n'émet TODO_STATE que si changé
        self._iterations_without_progress: int = 0
        self._last_completed_task_count: int = 0
        self._last_document_manifest_signature: tuple = ()
        # P5 — profil comportemental par modèle (chargé dynamiquement à la première itération)
        self._model_profile = None
        self._model_profile_applied_for: str = ""
        # ── ExecutionLedger — source de vérité d'exécution (V1) ──
        self.execution_ledger = ExecutionLedger()
        # ── AgentExecutionState — état structuré (V1) ──
        self.exec_state = AgentExecutionState()

    # ── Propriétés-alias : compatibilité transitoire vers exec_state ─────
    # Permettent à tout le code existant de continuer à écrire
    # self._consecutive_same_action = X  et  if self._run_meta[...]:
    # sans changement, tout en centralisant l'état dans exec_state.
    # À retirer progressivement quand les consommateurs seront migrés.

    # --- guards ---
    @property
    def _consecutive_same_action(self):
        self._ensure_exec_state()
        return self.exec_state.guards.consecutive_same_action
    @_consecutive_same_action.setter
    def _consecutive_same_action(self, v):
        self._ensure_exec_state()
        self.exec_state.guards.consecutive_same_action = v

    @property
    def _last_action_signature(self):
        self._ensure_exec_state()
        return self.exec_state.guards.last_action_signature
    @_last_action_signature.setter
    def _last_action_signature(self, v):
        self._ensure_exec_state()
        self.exec_state.guards.last_action_signature = v

    @property
    def _pending_loop_guidance(self):
        self._ensure_exec_state()
        return self.exec_state.guards.pending_loop_guidance
    @_pending_loop_guidance.setter
    def _pending_loop_guidance(self, v):
        self._ensure_exec_state()
        self.exec_state.guards.pending_loop_guidance = v

    @property
    def _last_auto_advance_iter(self):
        self._ensure_exec_state()
        return self.exec_state.guards.last_auto_advance_iter
    @_last_auto_advance_iter.setter
    def _last_auto_advance_iter(self, v):
        self._ensure_exec_state()
        self.exec_state.guards.last_auto_advance_iter = v

    @property
    def _last_browser_visual_iter(self):
        self._ensure_exec_state()
        return self.exec_state.guards.last_browser_visual_iter
    @_last_browser_visual_iter.setter
    def _last_browser_visual_iter(self, v):
        self._ensure_exec_state()
        self.exec_state.guards.last_browser_visual_iter = v

    @property
    def _browser_blind_streak(self):
        self._ensure_exec_state()
        return self.exec_state.guards.browser_blind_streak
    @_browser_blind_streak.setter
    def _browser_blind_streak(self, v):
        self._ensure_exec_state()
        self.exec_state.guards.browser_blind_streak = v

    @property
    def _last_browser_surface(self):
        self._ensure_exec_state()
        return self.exec_state.guards.last_browser_surface
    @_last_browser_surface.setter
    def _last_browser_surface(self, v):
        self._ensure_exec_state()
        self.exec_state.guards.last_browser_surface = v

    @property
    def _last_browser_surface_reason(self):
        self._ensure_exec_state()
        return self.exec_state.guards.last_browser_surface_reason
    @_last_browser_surface_reason.setter
    def _last_browser_surface_reason(self, v):
        self._ensure_exec_state()
        self.exec_state.guards.last_browser_surface_reason = v

    @property
    def _browser_surface_streak(self):
        self._ensure_exec_state()
        return self.exec_state.guards.browser_surface_streak
    @_browser_surface_streak.setter
    def _browser_surface_streak(self, v):
        self._ensure_exec_state()
        self.exec_state.guards.browser_surface_streak = v

    @property
    def _last_browser_progress_sig(self):
        self._ensure_exec_state()
        return self.exec_state.guards.last_browser_progress_sig
    @_last_browser_progress_sig.setter
    def _last_browser_progress_sig(self, v):
        self._ensure_exec_state()
        self.exec_state.guards.last_browser_progress_sig = v

    @property
    def _browser_no_progress_streak(self):
        self._ensure_exec_state()
        return self.exec_state.guards.browser_no_progress_streak
    @_browser_no_progress_streak.setter
    def _browser_no_progress_streak(self, v):
        self._ensure_exec_state()
        self.exec_state.guards.browser_no_progress_streak = v

    # --- repairs ---
    @property
    def _final_repair_attempts(self):
        self._ensure_exec_state()
        return self.exec_state.repairs.final_repair_attempts
    @_final_repair_attempts.setter
    def _final_repair_attempts(self, v):
        self._ensure_exec_state()
        self.exec_state.repairs.final_repair_attempts = v

    @property
    def _hallucination_repair_attempts(self):
        self._ensure_exec_state()
        return self.exec_state.repairs.hallucination_repair_attempts
    @_hallucination_repair_attempts.setter
    def _hallucination_repair_attempts(self, v):
        self._ensure_exec_state()
        self.exec_state.repairs.hallucination_repair_attempts = v

    @property
    def _thought_leak_repairs(self):
        self._ensure_exec_state()
        return self.exec_state.repairs.thought_leak_repairs
    @_thought_leak_repairs.setter
    def _thought_leak_repairs(self, v):
        self._ensure_exec_state()
        self.exec_state.repairs.thought_leak_repairs = v

    @property
    def _premature_final_retries(self):
        self._ensure_exec_state()
        return self.exec_state.repairs.premature_final_retries
    @_premature_final_retries.setter
    def _premature_final_retries(self, v):
        self._ensure_exec_state()
        self.exec_state.repairs.premature_final_retries = v

    @property
    def _plan_guard_retries(self):
        self._ensure_exec_state()
        return self.exec_state.repairs.plan_guard_retries
    @_plan_guard_retries.setter
    def _plan_guard_retries(self, v):
        self._ensure_exec_state()
        self.exec_state.repairs.plan_guard_retries = v

    @property
    def _verbalization_redirects(self):
        self._ensure_exec_state()
        return self.exec_state.repairs.verbalization_redirects
    @_verbalization_redirects.setter
    def _verbalization_redirects(self, v):
        self._ensure_exec_state()
        self.exec_state.repairs.verbalization_redirects = v

    @property
    def _action_inline_count(self):
        self._ensure_exec_state()
        return self.exec_state.repairs.action_inline_count
    @_action_inline_count.setter
    def _action_inline_count(self, v):
        self._ensure_exec_state()
        self.exec_state.repairs.action_inline_count = v

    @property
    def _ledger_final_guard_used(self):
        self._ensure_exec_state()
        return self.exec_state.repairs.ledger_final_guard_used
    @_ledger_final_guard_used.setter
    def _ledger_final_guard_used(self, v):
        self._ensure_exec_state()
        self.exec_state.repairs.ledger_final_guard_used = v

    @property
    def _pre_repair_answer(self):
        self._ensure_exec_state()
        return self.exec_state.repairs.pre_repair_answer
    @_pre_repair_answer.setter
    def _pre_repair_answer(self, v):
        self._ensure_exec_state()
        self.exec_state.repairs.pre_repair_answer = v

    @property
    def _after_delegate_success(self):
        self._ensure_exec_state()
        return self.exec_state.repairs.after_delegate_success
    @_after_delegate_success.setter
    def _after_delegate_success(self, v):
        self._ensure_exec_state()
        self.exec_state.repairs.after_delegate_success = v

    # --- budget ---
    @property
    def _category_iter_counts(self):
        self._ensure_exec_state()
        return self.exec_state.budget.iter_counts
    @_category_iter_counts.setter
    def _category_iter_counts(self, v):
        self._ensure_exec_state()
        self.exec_state.budget.iter_counts = v

    # --- run_meta (proxy dict → RunMeta dataclass) ---
    def _ensure_exec_state(self):
        """Lazy-init exec_state si absent (ex: object.__new__ dans les tests)."""
        if not hasattr(self, 'exec_state'):
            self.exec_state = AgentExecutionState()

    @property
    def _run_meta(self):
        self._ensure_exec_state()
        return RunMetaProxy(self.exec_state.run_meta)
    @_run_meta.setter
    def _run_meta(self, v):
        self._ensure_exec_state()
        if isinstance(v, dict):
            self.exec_state.run_meta.agent_output_incomplete = v.get("agent_output_incomplete", False)
            self.exec_state.run_meta.agent_output_warning = v.get("agent_output_warning")
            self.exec_state.run_meta.agent_repair_attempts = v.get("agent_repair_attempts", 0)
            self.exec_state.run_meta.agent_final_finish_reason = v.get("agent_final_finish_reason")

    # --- session tools ---
    @property
    def _all_session_tools(self):
        self._ensure_exec_state()
        return self.exec_state.all_session_tools
    @_all_session_tools.setter
    def _all_session_tools(self, v):
        self._ensure_exec_state()
        self.exec_state.all_session_tools = v

    @property
    def _successful_session_tools(self):
        """Outils dont l'observation.success était True — seule preuve fiable."""
        self._ensure_exec_state()
        return self.exec_state.successful_session_tools
    @_successful_session_tools.setter
    def _successful_session_tools(self, v):
        self._ensure_exec_state()
        self.exec_state.successful_session_tools = v

    # --- last_llm_meta ---
    @property
    def _last_llm_meta(self):
        self._ensure_exec_state()
        return self.exec_state.last_llm_meta
    @_last_llm_meta.setter
    def _last_llm_meta(self, v):
        self._ensure_exec_state()
        self.exec_state.last_llm_meta = v

    @staticmethod
    def _env_int(name: str, default: int, minimum: int = 1) -> int:
        raw = os.getenv(name)
        if raw is None:
            return default
        try:
            return max(minimum, int(str(raw).strip()))
        except Exception:
            return default

    def _is_ide_runtime(self) -> bool:
        try:
            runtime_check = getattr(self.tools, "_is_ide_runtime", None)
            if callable(runtime_check):
                return bool(runtime_check())
            ide_ctx = getattr(self.tools, "ide_context", {}) or {}
            return bool(ide_ctx.get("workspace_path") or ide_ctx.get("active_file_path"))
        except Exception:
            return False

    def _resolve_max_iterations(self) -> int:
        if self._is_ide_runtime():
            default_ide = self._env_int("LUMENA_MAX_REACT_ITERATIONS", 35, minimum=5)
            return self._env_int("LUMENA_MAX_REACT_ITERATIONS_IDE", default_ide, minimum=5)
        return self._env_int("LUMENA_MAX_REACT_ITERATIONS", 35, minimum=5)

    def _resolve_timeout_seconds(self) -> Optional[int]:
        if self._is_ide_runtime():
            raw_ide = os.getenv("LUMENA_REACT_TIMEOUT_IDE")
            if raw_ide is None:
                raw_ide = os.getenv("LUMENA_REACT_TIMEOUT")
                if raw_ide is None:
                    # IDE: timeout de sécurité 1800s (30 min) même sans env var.
                    # Évite que le daemon tourne à l'infini si la boucle ne converge pas.
                    return 1800
            try:
                parsed = int(str(raw_ide).strip())
            except Exception:
                return 1800
            if parsed <= 0:
                return 1800
            return max(30, parsed)

        try:
            parsed = int(str(os.getenv("LUMENA_REACT_TIMEOUT", "900")).strip())
        except Exception:
            parsed = 900
        return max(30, parsed)

    def _history_observation_limit(self) -> int:
        # IDE runtime : valeur spécifique conservée (boucle Desktop courte).
        if self._is_ide_runtime():
            return self._env_int("LUMENA_REACT_HISTORY_OBS_CHARS_IDE", 12000, minimum=500)
        # Phase 7.2 : calibration réelle basée sur le catalogue Lumena.
        #   cf. src/reasoning/history_formatter.py (paliers 2k/8k/24k/32k/40k/48k)
        #   override possible via LUMENA_REACT_OBS_LIMIT / LUMENA_REACT_OBS_CLAMP.
        if self.runtime_ctx is not None:
            return compute_obs_limit_from_runtime(self.runtime_ctx)
        # Legacy fallback (aucun runtime_ctx) : lit LUMENA_REACT_HISTORY_OBS_CHARS.
        return self._env_int("LUMENA_REACT_HISTORY_OBS_CHARS", 8000, minimum=300)

    def _orchestrator_enabled(self) -> bool:
        return bool(self.task_orchestrator and self.task_id)

    def _raise_if_user_cancelled(self, phase: str = "") -> None:
        """Stop before a new side effect when the web stream was cancelled."""
        event = _REACT_CANCEL_EVENTS.get(threading.get_ident())
        if event is not None and event.is_set():
            logger.info("[ReAct] annulation utilisateur avant {}", phase or "action")
            raise SystemExit("user_cancelled_react")

    async def _execute_tool_with_cancel_guard(
        self, name: str, args: Dict[str, Any], *, caller: Any,
    ):
        """Execute one tool only while the owning stream is still active."""
        self._raise_if_user_cancelled(f"outil {name}")
        return await self.tools.execute(name, args, caller=caller)

    @property
    def _is_mission_run(self) -> bool:
        """Lot RF-6a : corps deplace vers `mission_runtime.py`.

        Reexport de compatibilite (invariants 4 et 19).
        """
        return _mr__is_mission_run(_entree_mission(self))

    def _post_delegate_web_verify_allowed(self) -> bool:
        """Lot RF-7a : corps deplace vers `browser_runtime.py`.

        Reexport de compatibilite (invariants 4 et 19).
        """
        return _rt__post_delegate_web_verify_allowed(_entree_navigateur(self))

    def _mission_workspace_meta(self) -> str:
        """Lot RF-6a : corps deplace vers `mission_runtime.py`.

        Reexport de compatibilite (invariants 4 et 19).
        """
        return _mr__mission_workspace_meta(_entree_mission(self))

    def _mission_unpublished_writes(self) -> list:
        """Lot RF-6a : corps deplace vers `mission_runtime.py`.

        Reexport de compatibilite (invariants 4 et 19).
        """
        return _mr__mission_unpublished_writes(_entree_mission(self))

    def _mission_routing_objective(self) -> str:
        """Lot RF-6a : corps deplace vers `mission_runtime.py`.

        Reexport de compatibilite (invariants 4 et 19).
        """
        return _mr__mission_routing_objective(_entree_mission(self))

    def _tests_present_but_not_run(self) -> bool:
        """A5 — des tests existent pour cette mission mais AUCUN pytest n'a tourné
        dans ce run. Alimente la bannière déterministe du chokepoint (preuve au
        ledger, pas au prompt)."""
        try:
            _o = self.execution_ledger.last_test_outcome()
            if (_o or {}).get("is_test_cmd"):
                return False
            owned = self._mission_allowed_files_meta()
            if owned:
                from src.reasoning.test_proof import any_test_file
                return bool(any_test_file(owned))
            return bool(self._mission_tests_present_for_gate())
        except Exception:
            return False

    def _current_green_test_proof(self) -> bool:
        """Mission proof is stale after a later source mutation."""
        try:
            if self._is_mission_run:
                return self.execution_ledger.has_fresh_green_test_run()
            return self.execution_ledger.has_green_test_run()
        except Exception:
            return False

    def _ledger_facts_step(self) -> "Optional[ReActStep]":
        """LOT Z22 — rend les faits PROUVÉS sous forme d'étape réinjectable.

        Run « jeu 3D monde ouvert » (2026-08-19). La compaction d'urgence
        anti-hallucination faisait `self.history = self.history[-3:]` — une
        troncature aveugle par la queue. Elle a tiré 5 fois et a effacé, entre
        autres, l'observation de 02:26:51 :

            📦 Livrable publié : … vers `workspace/jeu-3d-monde-ouvert/`

        La mission a ensuite passé **8 minutes et 42 appels d'exploration**
        (`list_directory workspace/documents/jeu-3d` — un chemin inventé —, puis
        find_files, grep_search, grep_batch…) à chercher un dossier qu'elle
        venait de créer 90 secondes plus tôt.

        Et c'est une BOUCLE : moins de faits → plus d'invention → streak plus
        haut → nouvelle compaction. Le garde anti-hallucination nourrissait
        l'hallucination qu'il combat.

        Le ledger, lui, n'est jamais tronqué : c'est un journal horodaté en
        ajout seul. `summary()` produit exactement la ligne perdue. On la remet
        en tête de l'historique nettoyé — le bruit meurt, les faits restent.

        `origin` n'est PAS "tool" : cette étape ne doit compter ni comme appel
        d'outil ni dans les compteurs de panne. Défensive : None sur erreur.
        """
        try:
            _resume = self.execution_ledger.summary()
            if not _resume or "(vide)" in _resume:
                return None
            return ReActStep(
                thought=Thought(
                    content="Je relis ce qui est DÉJÀ prouvé avant de continuer."
                ),
                action=Action(action_type=ActionType.THINKING),
                observation=Observation(
                    content=(
                        "📌 FAITS ÉTABLIS DANS CE RUN (journal d'exécution — "
                        "ne les redécouvre pas, ne les cherche pas) :\n"
                        f"{_resume}"
                    ),
                    success=True,
                    origin="ledger_facts",
                ),
            )
        except Exception:
            return None

    def _nudge_unpublished_writes(self) -> None:
        """LOT Z24 — la prevenir AU MOMENT ou l'ecart se cree, pas a la fin.

        Un tir unique, jamais de refus : elle vient d'ecrire un fichier apres
        avoir publie. Le dire maintenant lui laisse le temps de republier ; le
        dire au FINAL ne fait plus que constater la perte.
        """
        # Lot RF-6b : la DECISION est deplacee vers `mission_runtime.py` ; la
        # mutation reste ici (invariant 5), dans l'ordre d'origine —
        # drapeau, puis log, puis guidance (invariant 16).
        decision = _mr_decision_nudge(
            _entree_mission(self), getattr(self, "_z24_nudged", False)
        )
        if decision is None:
            return
        liste, guidance = decision
        self._z24_nudged = True
        logger.warning(
            "[Z24] {} ecrit(s) apres la publication — hors livrable, redirection 1/1",
            liste,
        )
        self._pending_loop_guidance = guidance

    def _invalidate_interaction_pending(self, tool_name: str, success: bool) -> None:
        """LOT Z20 — annule une action utilisateur en attente de preuve.

        Appelée pour CHAQUE outil (le garde browser, lui, ne voit que `browser_*`).
        Après un de ces outils, une différence de DOM n'est plus attribuable à
        l'action : on jette l'attente ET l'empreinte de référence, ce qui oblige
        la mission à refaire lecture → action → lecture sur la page réelle.

        Ne touche PAS `local_preview_interaction_proven` : une preuve déjà acquise
        reste acquise (c'est l'attente, pas la preuve, qui a menti sur Créneau).
        Défensive : ne peut jamais interrompre la boucle.
        """
        try:
            if not success or tool_name not in _INTERACTION_PROOF_INVALIDATORS:
                return
            _g = self.exec_state.guards
            if not (_g.local_preview_mutation_since_read
                    or _g.local_preview_last_read_fingerprint):
                return
            _pending = bool(_g.local_preview_mutation_since_read)
            _g.local_preview_mutation_since_read = False
            _g.local_preview_last_read_fingerprint = ""
            logger.info(
                "[Z20] preuve d'interaction recalibrée après '{}' "
                "(action en attente={}) — la prochaine différence de DOM ne peut "
                "plus être créditée à une action antérieure. task={}",
                tool_name, _pending, self.task_id,
            )
        except Exception:
            return

    def _current_browser_proof(self) -> bool:
        """Lot RF-7a : corps deplace vers `browser_runtime.py`.

        Reexport de compatibilite (invariants 4 et 19).
        """
        return _rt__current_browser_proof(_entree_navigateur(self))

    def _mission_tests_present_for_gate(self) -> str:
        """Lot RF-6a : corps deplace vers `mission_runtime.py`.

        Reexport de compatibilite (invariants 4 et 19).
        """
        return _mr__mission_tests_present_for_gate(_entree_mission(self))

    def _mission_web_present_for_gate(self) -> str:
        """Lot RF-6a : corps deplace vers `mission_runtime.py`.

        Reexport de compatibilite (invariants 4 et 19).
        """
        return _mr__mission_web_present_for_gate(_entree_mission(self))

    @staticmethod
    def _browser_verify_intent(text: str) -> bool:
        """Lot RF-7a : corps deplace vers `browser_runtime.py`.

        Reexport de compatibilite (invariants 4 et 19).
        """
        return _rt__browser_verify_intent(text)

    def _mission_browser_verify_pending(self, answer: str, original_query: str) -> str:
        """Lot RF-7a : corps deplace vers `browser_runtime.py`.

        Reexport de compatibilite (invariants 4 et 19).
        """
        return _rt__mission_browser_verify_pending(_entree_navigateur(self), answer, original_query)

    def _pages_never_opened_reason(self) -> str:
        """Lot RF-7a : corps deplace vers `browser_runtime.py`.

        Reexport de compatibilite (invariants 4 et 19).
        """
        return _rt__pages_never_opened_reason(_entree_navigateur(self))

    def _mission_js_present_for_gate(self) -> str:
        """Lot RF-6a : corps deplace vers `mission_runtime.py`.

        Reexport de compatibilite (invariants 4 et 19).
        """
        return _mr__mission_js_present_for_gate(_entree_mission(self))

    def _finalize_browser_gate_pending(self, note: str, original_query: str) -> str:
        """Lot RF-7a : corps deplace vers `browser_runtime.py`.

        Reexport de compatibilite (invariants 4 et 19).
        """
        return _rt__finalize_browser_gate_pending(_entree_navigateur(self), note, original_query)

    def _finalize_interaction_gate_pending(
        self, note: str, original_query: str
    ) -> str:
        """Lot RF-7a : corps deplace vers `browser_runtime.py`.

        Reexport de compatibilite (invariants 4 et 19).
        """
        return _rt__finalize_interaction_gate_pending(_entree_navigateur(self), note, original_query)

    def _server_started_proof(self) -> bool:
        """LOT 2.3 (run MotDuJour) — un serveur de preview a-t-il RÉELLEMENT démarré
        dans CE run ? Preuve ledger : serve_website ou start_preview_server réussi.
        Défensif : True sur erreur (jamais de fausse rétrogradation)."""
        try:
            led = self.execution_ledger
            return (led.has_successful_action("serve_website")
                    or led.has_successful_action("start_preview_server"))
        except Exception:
            return True

    def _browser_content_seen(self) -> Optional[str]:
        """Lot RF-7a : corps deplace vers `browser_runtime.py`.

        Reexport de compatibilite (invariants 4 et 19).
        """
        return _rt__browser_content_seen(_entree_navigateur(self))

    def _truth_lock_web_flag(self) -> bool:
        """Lot RF-8 : corps deplace vers `final_delivery_runtime.py`.

        Reexport de compatibilite (invariants 4 et 19).
        """
        return _fd__truth_lock_web_flag(_entree_final(self))

    def _set_web_runtime_verification_state(
        self,
        *,
        failed: bool,
        report: str = "",
    ) -> None:
        """Persist the latest strict web-runtime verdict for this mission run."""
        self._web_runtime_failed = bool(failed)
        self._web_runtime_verified = not bool(failed)
        self._web_runtime_failure_report = str(report or "")[:3000] if failed else ""
        if not self.task_id or not self.task_orchestrator:
            return
        try:
            self.task_orchestrator.set_task_metadata(
                self.task_id,
                web_runtime_failed=bool(failed),
                web_runtime_verified=not bool(failed),
                web_runtime_failure_report=self._web_runtime_failure_report,
            )
        except Exception as exc:
            logger.debug("[M100.4] persistence verdict runtime web ignoree: {}", exc)

    def _browser_runtime_failed_for_truth_lock(self) -> bool:
        """Lot RF-7a : corps deplace vers `browser_runtime.py`.

        Reexport de compatibilite (invariants 4 et 19).
        """
        return _rt__browser_runtime_failed_for_truth_lock(_entree_navigateur(self))

    def _browser_runtime_verified_for_truth_lock(self) -> bool:
        """Lot RF-7a : corps deplace vers `browser_runtime.py`.

        Reexport de compatibilite (invariants 4 et 19).
        """
        return _rt__browser_runtime_verified_for_truth_lock(_entree_navigateur(self))

    def _mission_overwrite_gate(self, tool_name: str, tool_args: Optional[Dict[str, Any]] = None):
        """LOT P2b — GATE LIVRABLE EXISTANT, 1 tir.

        Une mission qui réécrit en place un fichier déjà livré travaille sans
        filet : si elle échoue en route, le projet reste cassé et personne n'est
        prévenu. C'est arrivé deux fois à HuffPack v1 en une heure.

        Inerte hors mission, inerte sans dossier de mission, inerte sur un
        fichier neuf. Redirection unique — au second appel, l'écriture passe.
        """
        # Lot RF-6b : la DECISION sort, la mutation reste (invariant 5).
        # L'`Observation` est construite ICI, APRES l'increment : la faire
        # naitre dans le module la daterait d'avant la mutation (invariant 16).
        decision = _mr_decision_ecrasement(
            _entree_mission(self), tool_name, tool_args,
            getattr(self, "_overwrite_gate_shots", 0),
        )
        if decision is None:
            return None
        target, ws, contenu = decision
        self._overwrite_gate_shots = getattr(self, "_overwrite_gate_shots", 0) + 1
        logger.warning(
            "[P2b] écriture en place sur le livrable existant '{}' (hors {}) → "
            "redirection dirigée 1/1.", target, ws,
        )
        from .react_config import Observation as _ObsP2b

        return _ObsP2b(
            content=contenu,
            success=False,
            origin="mission_overwrite",
        )

    def _local_preview_unprovable_gate(self, tool_name: str):
        """Lot RF-7a : corps deplace vers `browser_runtime.py`.

        Reexport de compatibilite (invariants 4 et 19).
        """
        return _rt__local_preview_unprovable_gate(_entree_navigateur(self), tool_name)

    def _chat_mission_intent_gate(self, tool_name: str):
        """LOT O2 (run HuffPack v2, 2026-08-14) — GATE INTENTION MISSION, 1 tir.

        Au CHAT, l'utilisateur annonce « Mission avec échéance 90 minutes » et le
        modèle part explorer le dossier au lieu de lancer la mission : 12
        itérations de lecture, sans budget, sans workers, sans suivi. Rien ne
        reliait son annonce à `create_mission` — la mission ne se créait que
        lorsque le verbe de la tâche était « construis » (le modèle vise alors
        write_mission_contract, et le refus le redirige). Avec « améliore
        l'existant », personne ne le reprenait.

        Inerte DANS une mission : le lead et les workers reçoivent ce même
        vocabulaire dans leur prompt injecté — les déclencher relancerait des
        missions en cascade.

        Redirection, pas blocage : un seul tir, puis l'outil passe.
        """
        # Lot RF-6b : la DECISION sort, la mutation reste (invariant 5).
        contenu = _mr_decision_intention_chat(
            _entree_mission(self), tool_name,
            getattr(self, "_chat_mission_gate_shots", 0),
        )
        if contenu is None:
            return None
        self._chat_mission_gate_shots = getattr(self, "_chat_mission_gate_shots", 0) + 1
        logger.warning(
            "[CHAT MISSION GATE] '{}' alors que l'utilisateur a demandé une mission "
            "à échéance → redirection dirigée 1/1.", tool_name,
        )
        from .react_config import Observation as _ObsO2

        return _ObsO2(
            content=contenu,
            success=False,
            origin="chat_mission_intent",
        )

    def _contract_protocol_requirement(self) -> tuple:
        """LOT Z26 — d'où vient l'exigence du protocole, et de qui ?

        La porte 2.13.B lisait `_original_query`, qui contient le PRÉAMBULE de
        mission écrit par Lumena elle-même — préambule où figure littéralement
        `write_mission_contract`. Mesuré : le préambule seul suffit à déclencher.
        La porte affirmait ensuite « L'utilisateur a EXPLICITEMENT demandé » en
        citant, en réalité, la prose de Lumena. Elle attribuait à l'utilisateur
        une exigence qu'il n'avait pas formulée — le contraire exact du critère
        de ce chantier.

        Elle tirait aussi là où 2.13.B la voulait INERTE (« sans exigence
        explicite, create_project direct reste licite »), puisque le préambule
        est présent à chaque mission.

        Correctif : l'objectif SÉMANTIQUE décide (`_mission_routing_objective`,
        dont le rôle est précisément d'exclure la prose de protocole). Repli sur
        `_original_query` seulement s'il est absent — et dans ce cas on ne
        prétend PLUS que l'utilisateur l'a demandé.

        Mesure du corpus (643 missions) : 166 objectifs UTILISATEUR exigent
        réellement le protocole, « sous-agents » en tête (120 occurrences). La
        porte garde donc tout son travail : Z26 corrige à qui on l'attribue, pas
        ce qu'elle protège.

        Retour : (exigence, attribuable_a_l_utilisateur).
        """
        from .final_guards import objective_requires_contract_protocol

        objectif = ""
        try:
            _fn = getattr(self, "_mission_routing_objective", None)
            if callable(_fn):
                objectif = str(_fn() or "").strip()
        except Exception:
            objectif = ""
        if objectif:
            # L'objectif de l'utilisateur est connu : il décide SEUL. Retomber
            # sur la requête brute ici, ce serait réintroduire le préambule.
            return (objective_requires_contract_protocol(objectif), True)
        brut = str(getattr(self, "_original_query", "") or "")
        return (objective_requires_contract_protocol(brut), False)

    def _contract_intent_gate(self, tool_name: str):
        """2.13.B (run miniblog 2026-07-09) — GATE INTENTION CONTRAT, 1 tir.

        L'utilisateur a EXPLICITEMENT exigé le protocole contrat+workers et le
        lead tente un create_project/generate_website direct SANS
        write_mission_contract réussi au ledger → observation de redirection
        dirigée UNE fois (même mécanique bornée que le BROWSER GATE). Sans
        exigence explicite : inerte — create_project direct reste licite
        (morpion/pwgen/sondage). Retourne None pour laisser passer."""
        if tool_name not in ("create_project", "generate_website"):
            return None
        if not self._is_mission_run:
            return None
        if getattr(self, "_contract_gate_shots", 0) >= 1:
            return None
        if self._is_worker_run():  # H4 : périmètre OU parent (worker d'effets)
            return None  # worker délégué → le contrat appartient au lead
        # Appel par la CLASSE : `_contract_intent_gate` est autoportante (elle ne
        # lit qu'un jeu d'attributs) et se teste sur un duck-type — passer par
        # `self.` exigerait de lui greffer la méthode. Contrat préservé.
        _exige, _du_user = ReActLoop._contract_protocol_requirement(self)
        if not _exige:
            return None
        if self.execution_ledger.has_successful_action("write_mission_contract"):
            return None
        self._contract_gate_shots = getattr(self, "_contract_gate_shots", 0) + 1
        logger.warning(
            "[CONTRACT GATE] {} direct alors que l'objectif exige le protocole "
            "contrat+workers → redirection dirigée 1/1. task={}",
            tool_name, self.task_id,
        )
        from .react_config import Observation as _Obs213
        # LOT Z26 — n'attribuer à l'utilisateur que ce qu'il a écrit.
        _amorce = (
            "⛔ L'utilisateur a EXPLICITEMENT demandé le protocole contrat + "
            "workers pour cette mission. "
            if _du_user else
            "⛔ Cette mission relève du protocole contrat + workers. "
        )
        return _Obs213(
            content=(
                _amorce +
                "Ne crée PAS le projet directement : "
                "pose d'abord le contrat via `write_mission_contract` (`files` avec "
                "path/owner/exports pour le code, et/ou `effects` avec "
                "owner/action/desc/proof pour les livrables non-fichier — un owner "
                "par worker), puis délègue via "
                "`delegate_and_wait`. (Redirection unique : si tu rappelles "
                f"{tool_name} ensuite, il s'exécutera.)"
            ),
            success=False,
        )

    def _worker_codeagent_first_gate(self, tool_name: str, tool_args: Optional[dict] = None):
        """Lot RF-6a : corps deplace vers `mission_runtime.py`.

        Reexport de compatibilite (invariants 4 et 19).
        """
        return _mr__worker_codeagent_first_gate(_entree_mission(self), tool_name, tool_args)

    def _document_route_for_run(self, query: Optional[str] = None) -> DocumentRoute:
        """Lot RF-5b : corps deplace vers `document_runtime.py`.

        Reexport de compatibilite (invariants 4 et 19).
        """
        return _rt__document_route_for_run(_entree_document_catalogue(self), query)

    def _force_mission_proactive_document_tools(self) -> list[str]:
        """Lot RF-5d2 : corps deplace vers `document_runtime.py`.

        Reexport de compatibilite (invariants 4 et 19).
        """
        return _rt__force_mission_proactive_document_tools(_entree_porte_document(self))

    def _document_tool_events(self):
        """Lot RF-5a : corps deplace vers `document_runtime.py`.

        Reexport de compatibilite (invariants 4 et 19) : les 283 sites
        d'appel existants, dont 196 dans les tests, continuent d'ecrire
        `ReActLoop._document_tool_events(...)` sans changement.
        """
        yield from _rt__document_tool_events(getattr(self, "history", []))

    @staticmethod
    def _document_catalog_evidence_key(args: Optional[Dict[str, Any]]) -> tuple[str, int, str]:
        """Lot RF-5a : corps deplace vers `document_runtime.py`.

        Reexport de compatibilite (invariants 4 et 19) : les 283 sites
        d'appel existants, dont 196 dans les tests, continuent d'ecrire
        `ReActLoop._document_catalog_evidence_key(...)` sans changement.
        """
        return _rt__document_catalog_evidence_key(args)

    @staticmethod
    def _document_catalog_rows(content: Any) -> tuple[dict, ...]:
        """Lot RF-5a : corps deplace vers `document_runtime.py`.

        Reexport de compatibilite (invariants 4 et 19) : les 283 sites
        d'appel existants, dont 196 dans les tests, continuent d'ecrire
        `ReActLoop._document_catalog_rows(...)` sans changement.
        """
        return _rt__document_catalog_rows(content)

    def _record_document_catalog_evidence(self, action, observation) -> None:
        """Lot RF-5b : corps deplace vers `document_runtime.py`.

        Reexport de compatibilite (invariants 4 et 19).
        """
        return _rt__record_document_catalog_evidence(_entree_document_catalogue(self), action, observation)

    @staticmethod
    def _document_parallel_calls(tool_args: Optional[Dict[str, Any]]) -> tuple[tuple[str, dict], ...]:
        """Lot RF-5a : corps deplace vers `document_runtime.py`.

        Reexport de compatibilite (invariants 4 et 19) : les 283 sites
        d'appel existants, dont 196 dans les tests, continuent d'ecrire
        `ReActLoop._document_parallel_calls(...)` sans changement.
        """
        return _rt__document_parallel_calls(tool_args)

    def _record_document_workflow_evidence(self, action, observation) -> None:
        """Lot RF-5d1 : corps deplace vers `document_runtime.py`.

        Reexport de compatibilite (invariants 4 et 19).
        """
        return _rt__record_document_workflow_evidence(_entree_workflow_document(self), action, observation)

    @staticmethod
    def _duplicate_document_mutation(
        primary_name: str,
        primary_args: Optional[Dict[str, Any]],
        queued_name: str,
        queued_args: Optional[Dict[str, Any]],
    ) -> bool:
        """Lot RF-5a : corps deplace vers `document_runtime.py`.

        Reexport de compatibilite (invariants 4 et 19) : les 283 sites
        d'appel existants, dont 196 dans les tests, continuent d'ecrire
        `ReActLoop._duplicate_document_mutation(...)` sans changement.
        """
        return _rt__duplicate_document_mutation(primary_name, primary_args, queued_name, queued_args)

    @staticmethod
    def _document_open_payload(observation) -> dict[str, Any] | None:
        """Lot RF-5a : corps deplace vers `document_runtime.py`.

        Reexport de compatibilite (invariants 4 et 19) : les 283 sites
        d'appel existants, dont 196 dans les tests, continuent d'ecrire
        `ReActLoop._document_open_payload(...)` sans changement.
        """
        return _rt__document_open_payload(observation)

    @staticmethod
    def _document_revision_patch(args: Optional[Dict[str, Any]]) -> dict[str, Any]:
        """Lot RF-5a : corps deplace vers `document_runtime.py`.

        Reexport de compatibilite (invariants 4 et 19) : les 283 sites
        d'appel existants, dont 196 dans les tests, continuent d'ecrire
        `ReActLoop._document_revision_patch(...)` sans changement.
        """
        return _rt__document_revision_patch(args)

    @staticmethod
    def _document_revision_changed_fields(record: Any) -> dict[str, Any]:
        """Lot RF-5a : corps deplace vers `document_runtime.py`.

        Reexport de compatibilite (invariants 4 et 19) : les 283 sites
        d'appel existants, dont 196 dans les tests, continuent d'ecrire
        `ReActLoop._document_revision_changed_fields(...)` sans changement.
        """
        return _rt__document_revision_changed_fields(record)

    @staticmethod
    def _document_patch_scalar_values(value: Any) -> tuple[str, ...]:
        """Lot RF-5a : corps deplace vers `document_runtime.py`.

        Reexport de compatibilite (invariants 4 et 19) : les 283 sites
        d'appel existants, dont 196 dans les tests, continuent d'ecrire
        `ReActLoop._document_patch_scalar_values(...)` sans changement.
        """
        return _rt__document_patch_scalar_values(value)

    @staticmethod
    def _document_paths_match(left: str, right: str) -> bool:
        """Lot RF-5a : corps deplace vers `document_runtime.py`.

        Reexport de compatibilite (invariants 4 et 19) : les 283 sites
        d'appel existants, dont 196 dans les tests, continuent d'ecrire
        `ReActLoop._document_paths_match(...)` sans changement.
        """
        return _rt__document_paths_match(left, right)

    @staticmethod
    def _document_verification_text(value: Any) -> str:
        """Lot RF-5a : corps deplace vers `document_runtime.py`.

        Reexport de compatibilite (invariants 4 et 19) : les 283 sites
        d'appel existants, dont 196 dans les tests, continuent d'ecrire
        `ReActLoop._document_verification_text(...)` sans changement.
        """
        return _rt__document_verification_text(value)

    def _document_workflow_proof_state(self) -> dict[str, Any]:
        """Lot RF-5d1 : corps deplace vers `document_runtime.py`.

        Reexport de compatibilite (invariants 4 et 19).
        """
        return _rt__document_workflow_proof_state(_entree_workflow_document(self))

    def _document_workflow_progress_signature(self) -> tuple:
        """Lot RF-5d2 : corps deplace vers `document_runtime.py`.

        Reexport de compatibilite (invariants 4 et 19).
        """
        return _rt__document_workflow_progress_signature(_entree_porte_document(self))

    def _document_catalog_selection_groups(self) -> tuple[tuple[dict, ...], ...]:
        """Lot RF-5b : corps deplace vers `document_runtime.py`.

        Reexport de compatibilite (invariants 4 et 19).
        """
        return _rt__document_catalog_selection_groups(_entree_document_catalogue(self))

    def _document_catalog_selection_models(self) -> tuple[dict, ...]:
        """Lot RF-5b : corps deplace vers `document_runtime.py`.

        Reexport de compatibilite (invariants 4 et 19).
        """
        return _rt__document_catalog_selection_models(_entree_document_catalogue(self))

    def _document_expected_template_ids(self) -> tuple[str, ...]:
        """Lot RF-5b : corps deplace vers `document_runtime.py`.

        Reexport de compatibilite (invariants 4 et 19).
        """
        return _rt__document_expected_template_ids(_entree_document_catalogue(self))

    def _reconcile_document_catalog_plan(self, iteration: int) -> int:
        """Lot RF-5b : corps deplace vers `document_runtime.py`.

        Reexport de compatibilite (invariants 4 et 19).
        """
        return _rt__reconcile_document_catalog_plan(_entree_document_catalogue(self), iteration)

    def _latest_document_batch_proofs(self):
        """Lot RF-5a : corps deplace vers `document_runtime.py`.

        Reexport de compatibilite (invariants 4 et 19) : les 283 sites
        d'appel existants, dont 196 dans les tests, continuent d'ecrire
        `ReActLoop._latest_document_batch_proofs(...)` sans changement.
        """
        return _rt__latest_document_batch_proofs(
            getattr(self, "history", []),
            getattr(self, "_document_workflow_evidence", {}),
        )

    def _document_web_rights_evidence(self) -> tuple[bool, bool]:
        """Lot RF-5a : corps deplace vers `document_runtime.py`.

        Reexport de compatibilite (invariants 4 et 19) : les 283 sites
        d'appel existants, dont 196 dans les tests, continuent d'ecrire
        `ReActLoop._document_web_rights_evidence(...)` sans changement.
        """
        return _rt__document_web_rights_evidence(getattr(self, "history", []))

    @staticmethod
    def _nested_document_bypass(
        tool_name: str, tool_args: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Lot RF-5a : corps deplace vers `document_runtime.py`.

        Reexport de compatibilite (invariants 4 et 19) : les 283 sites
        d'appel existants, dont 196 dans les tests, continuent d'ecrire
        `ReActLoop._nested_document_bypass(...)` sans changement.
        """
        return _rt__nested_document_bypass(tool_name, tool_args)

    def _studio_attempted_kinds(self, studio_tool: str, route: DocumentRoute) -> tuple[str, ...]:
        """Lot RF-5a : corps deplace vers `document_runtime.py`.

        Reexport de compatibilite (invariants 4 et 19) : les 283 sites
        d'appel existants, dont 196 dans les tests, continuent d'ecrire
        `ReActLoop._studio_attempted_kinds(...)` sans changement.
        """
        return _rt__studio_attempted_kinds(
            getattr(self, "history", []), studio_tool, route,
        )

    def _structured_document_delivery_progress(self) -> tuple[int, int, tuple[str, ...]]:
        """Lot RF-5c : corps deplace vers `document_runtime.py`.

        Reexport de compatibilite (invariants 4 et 19).
        """
        return _rt__structured_document_delivery_progress(_entree_livraison_document(self))

    def _structured_document_delivery_manifest(self):
        """Lot RF-5c : corps deplace vers `document_runtime.py`.

        Reexport de compatibilite (invariants 4 et 19).
        """
        return _rt__structured_document_delivery_manifest(_entree_livraison_document(self))

    def _ensure_document_delivery_reference(self) -> str:
        """Lot RF-5c : corps deplace vers `document_runtime.py`.

        Reexport de compatibilite (invariants 4 et 19).
        """
        return _rt__ensure_document_delivery_reference(_entree_livraison_document(self))

    def _document_workflow_pending_action(self):
        """Lot RF-5d2 : corps deplace vers `document_runtime.py`.

        Reexport de compatibilite (invariants 4 et 19).
        """
        return _rt__document_workflow_pending_action(_entree_porte_document(self))

    def _document_workflow_target(self):
        """Lot RF-5c : corps deplace vers `document_runtime.py`.

        Reexport de compatibilite (invariants 4 et 19).
        """
        return _rt__document_workflow_target(_entree_livraison_document(self))

    @staticmethod
    def _document_delivery_truth_required(route: DocumentRoute, requested_count: int) -> bool:
        """Lot RF-5c : corps deplace vers `document_runtime.py`.

        Reexport de compatibilite (invariants 4 et 19).
        """
        return _rt__document_delivery_truth_required(route, requested_count)

    @staticmethod
    def _merge_mission_document_evidence(free_answer: str, evidence: str) -> str:
        """Lot RF-5a : corps deplace vers `document_runtime.py`.

        Reexport de compatibilite (invariants 4 et 19) : les 283 sites
        d'appel existants, dont 196 dans les tests, continuent d'ecrire
        `ReActLoop._merge_mission_document_evidence(...)` sans changement.
        """
        return _rt__merge_mission_document_evidence(free_answer, evidence)

    @staticmethod
    def _document_plan_required_kinds(task_desc: str) -> tuple[str, ...]:
        """Lot RF-5a : corps deplace vers `document_runtime.py`.

        Reexport de compatibilite (invariants 4 et 19) : les 283 sites
        d'appel existants, dont 196 dans les tests, continuent d'ecrire
        `ReActLoop._document_plan_required_kinds(...)` sans changement.
        """
        return _rt__document_plan_required_kinds(task_desc)

    def _document_final_fulfills_plan_task(self, task_desc: str) -> bool:
        """Lot RF-5d2 : corps deplace vers `document_runtime.py`.

        Reexport de compatibilite (invariants 4 et 19).
        """
        return _rt__document_final_fulfills_plan_task(_entree_porte_document(self), task_desc)

    def _reconcile_document_plan_from_manifest(self, iteration: int) -> int:
        """Lot RF-5d2 : corps deplace vers `document_runtime.py`.

        Reexport de compatibilite (invariants 4 et 19).
        """
        return _rt__reconcile_document_plan_from_manifest(_entree_porte_document(self), iteration)

    def _reconcile_document_workflow_plan(self, iteration: int) -> int:
        """Lot RF-5d2 : corps deplace vers `document_runtime.py`.

        Reexport de compatibilite (invariants 4 et 19).
        """
        return _rt__reconcile_document_workflow_plan(_entree_porte_document(self), iteration)

    def _structured_document_tool_gate(
        self, tool_name: str, tool_args: Optional[Dict[str, Any]] = None,
    ):
        """Lot RF-5d2 : corps deplace vers `document_runtime.py`.

        Reexport de compatibilite (invariants 4 et 19).
        """
        return _rt__structured_document_tool_gate(_entree_porte_document(self), tool_name, tool_args)

    def _truth_lock_game_flag(self) -> bool:
        """Lot RF-8 : corps deplace vers `final_delivery_runtime.py`.

        Reexport de compatibilite (invariants 4 et 19).
        """
        return _fd__truth_lock_game_flag(_entree_final(self))

    def _note_truth_lock_outcome(self, info: Any) -> None:
        """F1.b — mémorise l'issue du truth-lock dans `_run_meta` (CUMULATIF).

        Le truth-lock calculait déjà `overclaim` (= le final AFFIRMAIT une preuve
        que le ledger ne porte pas : navigateur, livraison ou publication), s'en
        servait pour réécrire le MESSAGE, puis jetait l'information. L'ÉTAT de la
        mission restait donc `done/completed` alors qu'une affirmation venait
        d'être rétrogradée (cause AUD-014).

        Le drapeau est cumulatif : un site aval qui ne détecte rien ne doit jamais
        effacer un overclaim vu en amont (le texte a pu être neutralisé entre-temps
        — l'idempotence du verrou le garantit).

        On distingue volontairement `overclaim` de `changed` : une simple note
        honnête (« tests non exécutés ») modifie le texte sans être une faute de
        clôture. Seule l'affirmation fausse compte.

        Défensif : n'échoue jamais — une preuve manquante ne doit pas casser un run.
        """
        # Lot RF-8b : la DECISION est deplacee vers `final_delivery_runtime.py` ;
        # l'ECRITURE reste ici (invariant 5).
        try:
            for _cle, _val in _fd_verdict_a_memoriser(
                info, "mission_truth_lock_overclaim" in self._run_meta
            ).items():
                self._run_meta[_cle] = _val
        except Exception:
            pass

    def _empty_final_fallback(self) -> str:
        """F1.b — en mission, un FINAL vide ne devient JAMAIS une phrase de politesse.

        Hors mission : comportement historique strictement inchangé (le chat garde
        sa formule).

        En mission (AUD-012 / AUD-008) : un `answer` vide sur le chemin de SUCCÈS
        produisait « Je n'ai pas trouvé de réponse pertinente. » — une chaîne NON
        VIDE, qui franchissait donc la porte `empty_result` du runner. La mission
        sortait `done` avec une politesse alors que le ledger prouvait le travail
        (trois artefacts réels sur M01_DATA_DOCS ; navigateur réussi puis résultat
        détruit par les repairs sur A14_browser).

        Deux issues, toutes deux honnêtes :
        - le ledger porte des preuves → bilan DÉTERMINISTE (aucun appel LLM, donc
          aucun risque de fuite THOUGHT réintroduit) via `build_mission_final_message` ;
        - le ledger est vide → rien n'a été produit : on marque l'échec, exactement
          comme le fait déjà le chemin tronqué en amont.

        Défensif : toute erreur retombe sur la formule historique — ce garde-fou ne
        doit jamais transformer une mission réussie en exception.
        """
        # Lot RF-8b : la DECISION est deplacee vers `final_delivery_runtime.py` ;
        # le `_mark_task_failed` et les deux logs restent ici, dans l'ordre
        # d'origine — mutation, puis journal (invariants 5 et 16).
        message, marquer_echec, written, published = _fd_decision_final_vide(
            _entree_final(self)
        )
        if marquer_echec:
            self._mark_task_failed("empty_final_without_evidence")
            logger.warning(
                "[F1.b] FINAL vide et ledger SANS preuve → échec honnête "
                "(pas de clôture `done` sur une phrase de politesse). task={}",
                self.task_id,
            )
        elif written or published:
            logger.warning(
                "[F1.b] FINAL vide MAIS ledger avec preuves → bilan déterministe "
                "(écrits={} publié={}). task={}",
                len(written), published, self.task_id,
            )
        return message

    def _truth_lock_interaction_flag(self) -> bool:
        """Lot RF-8 : corps deplace vers `final_delivery_runtime.py`.

        Reexport de compatibilite (invariants 4 et 19).
        """
        return _fd__truth_lock_interaction_flag(_entree_final(self))

    def _truth_lock_interaction_proven(self) -> bool:
        """Lot RF-7a : corps deplace vers `browser_runtime.py`.

        Reexport de compatibilite (invariants 4 et 19).
        """
        return _rt__truth_lock_interaction_proven(_entree_navigateur(self))

    def _mission_completion_evidence(self) -> Dict[str, Any]:
        """Lot RF-6a : corps deplace vers `mission_runtime.py`.

        Reexport de compatibilite (invariants 4 et 19).
        """
        return _mr__mission_completion_evidence(_entree_mission(self))

    def _mission_allowed_files_meta(self) -> list:
        """Lot RF-6a : corps deplace vers `mission_runtime.py`.

        Reexport de compatibilite (invariants 4 et 19).
        """
        return _mr__mission_allowed_files_meta(_entree_mission(self))

    def _mission_worker_delivered(self) -> bool:
        """Lot RF-6a : corps deplace vers `mission_runtime.py`.

        Reexport de compatibilite (invariants 4 et 19).
        """
        return _mr__mission_worker_delivered(_entree_mission(self))

    def _mission_lead_delivered(self) -> list:
        """Lot RF-6a : corps deplace vers `mission_runtime.py`.

        Reexport de compatibilite (invariants 4 et 19).
        """
        return _mr__mission_lead_delivered(_entree_mission(self))

    def _truncated_but_delivered_answer(self, artefacts: list) -> str:
        """LOT Z28 — la réponse est faite de FAITS, jamais de la prose du modèle.

        À ce point du code, `answer` contient la pensée qui a fuité : parfois un
        résumé lisible, parfois du charabia. La renvoyer telle quelle livrerait
        au hasard. On énonce donc ce qui est vérifiable, et on dit clairement
        que c'est la conclusion qui a échoué — pas la mission.
        """
        lignes = [
            "⚠️ Ma conclusion n'a pas pu être mise en forme (réponse finale "
            "tronquée). Je ne te donne donc pas mon résumé, mais les faits.",
            "",
        ]
        if artefacts:
            lignes.append("📦 Présent sur le disque :")
            for a in artefacts:
                try:
                    p = Path(a)
                    if p.is_dir():
                        noms = sorted(x.name for x in p.iterdir())[:12]
                        lignes.append(f"   • {a} — {', '.join(noms)}")
                    else:
                        lignes.append(f"   • {a} ({p.stat().st_size} octets)")
                except Exception:
                    lignes.append(f"   • {a}")
            lignes.append("")
        try:
            resume = self.execution_ledger.summary()
            if resume:
                lignes.append(resume)
                lignes.append("")
        except Exception:
            pass
        lignes.append(
            "Le travail est là et vérifiable ci-dessus. C'est ma phrase de "
            "conclusion qui a échoué, pas la mission."
        )
        return "\n".join(lignes)

    def _mission_expects_file_deliverables(self):
        """Lot RF-6a : corps deplace vers `mission_runtime.py`.

        Reexport de compatibilite (invariants 4 et 19).
        """
        return _mr__mission_expects_file_deliverables(_entree_mission(self))

    def _is_worker_run(self) -> bool:
        """Lot RF-6a : corps deplace vers `mission_runtime.py`.

        Reexport de compatibilite (invariants 4 et 19).
        """
        return _mr__is_worker_run(_entree_mission(self))

    def _is_delegated_worker(self) -> bool:
        """Lot RF-6a : corps deplace vers `mission_runtime.py`.

        Reexport de compatibilite (invariants 4 et 19).
        """
        return _mr__is_delegated_worker(_entree_mission(self))

    def _mark_task_running(self) -> None:
        if not self._orchestrator_enabled():
            return
        try:
            mark_running = getattr(self.task_orchestrator, "mark_running", None)
            if callable(mark_running):
                mark_running(self.task_id)
        except Exception as exc:
            logger.debug("task orchestrator mark_running skipped: {}", exc)

    def _mark_task_checkpoint(self, payload: Dict[str, Any]) -> None:
        if not self._orchestrator_enabled():
            return
        try:
            # Enrichir le checkpoint avec la projection du ledger (si disponible)
            enriched = payload
            if hasattr(self, 'execution_ledger') and self.execution_ledger.size > 0:
                from ..runtime.task_orchestrator import TaskOrchestrator as _TO
                enriched = _TO.enrich_checkpoint(
                    payload, self.execution_ledger.checkpoint_projection(),
                )
            mark_checkpoint = getattr(self.task_orchestrator, "mark_checkpoint", None)
            if callable(mark_checkpoint):
                mark_checkpoint(self.task_id, enriched)
        except Exception as exc:
            logger.debug("task orchestrator mark_checkpoint skipped: {}", exc)

    def _mark_task_done(self, summary: str) -> None:
        if not self._orchestrator_enabled():
            return
        try:
            # Enrichir le checkpoint final avec la projection du ledger
            if hasattr(self, 'execution_ledger') and self.execution_ledger.size > 0:
                from ..runtime.task_orchestrator import TaskOrchestrator as _TO
                final_cp = _TO.enrich_checkpoint(
                    {"phase": "done"},
                    self.execution_ledger.checkpoint_projection(),
                )
                mark_checkpoint = getattr(self.task_orchestrator, "mark_checkpoint", None)
                if callable(mark_checkpoint):
                    mark_checkpoint(self.task_id, final_cp)
            mark_done = getattr(self.task_orchestrator, "mark_done", None)
            if callable(mark_done):
                # Pas de pré-cap ici : l'orchestrateur applique LE cap unique
                # (_result_summary_cap) — sinon le worker serait tronqué avant la fusion.
                mark_done(self.task_id, result_summary=summary)
        except Exception as exc:
            logger.debug("task orchestrator mark_done skipped: {}", exc)

    def _mark_task_waiting_io(self, error: str, checkpoint: Optional[Dict[str, Any]] = None) -> None:
        if not self._orchestrator_enabled():
            return
        try:
            mark_waiting_io = getattr(self.task_orchestrator, "mark_waiting_io", None)
            if callable(mark_waiting_io):
                mark_waiting_io(
                    self.task_id,
                    error=error[:800],
                    checkpoint=dict(checkpoint) if checkpoint else None,
                )
        except Exception as exc:
            logger.debug("task orchestrator mark_waiting_io skipped: {}", exc)

    # ── StructuredState: accès sûr au structured_state du ConversationContext ──

    @property
    def _structured_state(self):
        """Retourne le StructuredState du ConversationContext, ou None si indisponible."""
        ctx = getattr(self, 'conversation_context', None)
        if ctx is not None and hasattr(ctx, 'structured_state'):
            return ctx.structured_state
        return None

    def _feed_structured_tool(self, tool_name: str) -> None:
        """Enregistre un outil dans le structured_state (recent_tools)."""
        ss = self._structured_state
        if ss is not None:
            ss.record_tool(tool_name)

    def _feed_structured_intent(self, intent: Optional[str]) -> None:
        """Alimente last_intent avec la valeur classifiée."""
        if intent is None:
            return
        ss = self._structured_state
        if ss is not None:
            ss.last_intent = str(intent).strip() or None

    @staticmethod
    def _infer_intent_from_query(query: str) -> Optional[str]:
        """Inférence légère de l'intent depuis la requête (fallback sans classifier).

        Retourne une valeur grossière parmi :
        code_edit | discord | web_search | file_ops | create_project | conversation | question
        Retourne None si aucun signal clair.
        """
        q = query.lower()
        if any(k in q for k in ("discord", "salon", "channel", "serveur discord", "guild")):
            return "discord"
        if any(k in q for k in ("modifie", "edit", "corrige", "bug", "refactor", "implémente", "implement", "ajoute", "add", "crée", "create")):
            if any(k in q for k in ("fichier", "file", "code", "fonction", "class", "méthode", "method", "module")):
                return "code_edit"
        if any(k in q for k in ("recherche", "search", "trouve", "find", "google", "web")):
            return "web_search"
        if any(k in q for k in ("projet", "project", "app", "application", "crée un", "create a", "génère", "generate")):
            return "create_project"
        if any(k in q for k in ("lis", "read", "ouvre", "open", "affiche", "show", "liste")):
            return "file_ops"
        if q.endswith("?") or any(k in q for k in ("comment", "pourquoi", "qu'est", "what is", "how", "why", "explique", "explain")):
            return "question"
        return None

    def _feed_structured_clarification(self, question: str) -> None:
        """Ajoute une question en attente au structured_state."""
        ss = self._structured_state
        if ss is not None:
            ss.add_pending_question(question)

    def _reset_structured_pending(self) -> None:
        """Efface les questions en attente au début d'un nouveau run.

        Le nouveau message de l'utilisateur résout implicitement les clarifications
        précédemment émises — on repart d'un état propre.
        """
        ss = self._structured_state
        if ss is not None:
            ss.clear_pending_questions()

    def _feed_structured_facts_from_runtime(self) -> None:
        """Pose des established_facts fiables depuis le runtime_ctx."""
        ss = self._structured_state
        if ss is None:
            return
        rt = getattr(self, 'runtime_ctx', None)
        if rt is None:
            return
        # channel — attribut commun aux deux variantes de RuntimeContext
        channel = getattr(rt, 'channel', None) or getattr(rt, 'source_channel', None)
        if channel:
            ss.set_fact("channel", str(channel))
        # workspace — préférer resolved_workspace (plus fiable) à workspace_path
        workspace = getattr(rt, 'resolved_workspace', None) or getattr(rt, 'workspace_path', None)
        if workspace:
            ss.set_fact("workspace", str(workspace))
        # fichier actif dans l'IDE (signal stable, fourni par le plugin)
        active_file = getattr(rt, 'active_file_path', None)
        if active_file:
            ss.set_fact("active_file", str(active_file))
        # active_project_path — projet actif récent (sans keyword gate : c'est un fait structurel)
        # Uniquement si pas encore posé dans ce run et pas de workspace IDE explicite
        if not ss.established_facts.get("active_project_path"):
            _ide_ws = getattr(rt, 'resolved_workspace', None) or getattr(rt, 'workspace_path', None)
            if not _ide_ws:
                try:
                    _lum_sf = getattr(self, 'tools', None)
                    _lum_sf = getattr(_lum_sf, 'lumena', None) if _lum_sf else None
                    _id_svc_sf = getattr(_lum_sf, '_identity_svc', None) if _lum_sf else None
                    if _id_svc_sf is not None:
                        from ..core_services.identity_service import IdentityService as _IDS_SF
                        _ck_sf = _IDS_SF.resolve_channel_key(rt)
                        _rpc_sf = _id_svc_sf.get_recent_code_context(_ck_sf) if _ck_sf else None
                        if _rpc_sf:
                            _path_sf = _rpc_sf.get("workspace_path", "")
                            _slug_sf = _rpc_sf.get("project_slug", "")
                            if _path_sf:
                                ss.set_fact("active_project_path", _path_sf)
                                if _slug_sf:
                                    ss.set_fact("active_project_slug", _slug_sf)
                except Exception:
                    pass

    @staticmethod
    def _looks_like_local_code_fix(
        query: str,
        *,
        has_project_anchor: bool,
        inferred_intent: Optional[str] = None,
    ) -> bool:
        """Heuristique conservative pour les correctifs/code local bornés.

        But: desserrer les garde-fous de boucle sur les tâches de dev simples
        sans relâcher les cas ambigus ou de refonte large.
        """
        q = (query or "").lower()
        if not q:
            return False
        broad_scope_markers = (
            "refonte", "rewrite", "réécris", "reécris", "from scratch",
            "architecture", "restructure", "fusionne", "merge tout",
            "tout le projet", "whole project", "réorganise", "migre",
            "migration", "clean architecture", "full rewrite",
        )
        if any(k in q for k in broad_scope_markers):
            return False
        local_fix_markers = (
            "corrige", "correct", "fix", "bug", "erreur", "crash", "plante",
            "marche pas", "ne marche pas", "cassé", "casse", "bloque",
            "touche", "entrée", "enter", "bouton", "click", "clic",
            "fonctionne pas", "répare", "repare",
        )
        file_hint = bool(re.search(r"\b[\w.\-]+\.(?:py|js|ts|tsx|jsx|html|css|json|md|ya?ml|toml)\b", q))
        intent_hint = inferred_intent in {"code_edit", "file_ops"}
        local_signal = any(k in q for k in local_fix_markers) or file_hint or intent_hint
        return local_signal and (has_project_anchor or file_hint)

    def _is_direct_coding_request(self, query: str) -> bool:
        """Détecte les tâches de dev simples qui supportent mal les guards lourds."""
        has_anchor = False
        ide_ctx = getattr(self.tools, "ide_context", {}) or {}
        if ide_ctx.get("workspace_path") or ide_ctx.get("active_file_path"):
            has_anchor = True
        ss = self._structured_state
        inferred_intent = ss.last_intent if ss is not None else None
        if ss is not None:
            facts = getattr(ss, "established_facts", {}) or {}
            if facts.get("workspace") or facts.get("active_file"):
                has_anchor = True
        if not has_anchor:
            _lum = getattr(self.tools, "lumena", None)
            _id_svc = getattr(_lum, "_identity_svc", None) if _lum else None
            if _id_svc is not None and self.runtime_ctx is not None:
                try:
                    from ..core_services.identity_service import IdentityService as _IDS
                    _chan_key = _IDS.resolve_channel_key(self.runtime_ctx)
                    _recent_ctx = _id_svc.get_recent_code_context(_chan_key) if _chan_key else None
                    if _recent_ctx and _recent_ctx.get("workspace_path"):
                        has_anchor = True
                except Exception:
                    pass
        return self._looks_like_local_code_fix(
            query,
            has_project_anchor=has_anchor,
            inferred_intent=inferred_intent,
        )

    # ── THOUGHT leak auto-clean ────────────────────────────────────────────

    @staticmethod
    def _strip_thought_leak_prefix(text: str) -> Optional[str]:
        """Délègue au helper pur strip_thought_leak_prefix (final_guards.py)."""
        return strip_thought_leak_prefix(text)

    def _mark_task_failed(self, error: str) -> None:
        if not self._orchestrator_enabled():
            return
        try:
            mark_failed = getattr(self.task_orchestrator, "mark_failed", None)
            if callable(mark_failed):
                mark_failed(self.task_id, error=error[:800])
        except Exception as exc:
            logger.debug("task orchestrator mark_failed skipped: {}", exc)

    def get_run_meta(self) -> Dict[str, Any]:
        """Runtime metadata for API/UI after a run."""
        meta = dict(self._run_meta)
        if self._task_plan:
            completed = sum(1 for t in self._task_plan if t.completed)
            meta["plan"] = {
                "total_tasks": len(self._task_plan),
                "completed_tasks": completed,
                "tasks": [
                    {
                        "description": t.description,
                        "completed": t.completed,
                        "completed_at_iteration": t.completed_at_iteration,
                    }
                    for t in self._task_plan
                ],
            }
        return meta

    def _get_llm_meta(self) -> Dict[str, Any]:
        if not self.llm_meta_getter:
            return {}
        try:
            meta = self.llm_meta_getter() or {}
            return meta if isinstance(meta, dict) else {}
        except Exception:
            return {}

    @staticmethod
    def _llm_provider_error_detail(
        response: Any, meta: Optional[Dict[str, Any]] = None
    ) -> Optional[str]:
        """Return an authoritative provider failure instead of model prose.

        ``MultiProviderLLM.chat`` returns a short ``[Erreur]`` string when
        every configured route failed. The accompanying metadata is the
        authoritative discriminator: a normal model may mention an error in
        its answer, but only the provider layer emits ``finish_reason=error``.
        """
        info = meta if isinstance(meta, dict) else {}
        if str(info.get("finish_reason") or "").strip().lower() != "error":
            return None
        detail = str(response or "").strip()
        return detail[:1200] or "provider returned finish_reason=error"

    @staticmethod
    def _is_length_finish_reason(finish_reason: Optional[str]) -> bool:
        return is_length_finish_reason(finish_reason)

    @staticmethod
    def _has_unbalanced_delimiters(text: str) -> bool:
        return has_unbalanced_delimiters(text)

    @staticmethod
    def _has_unclosed_quotes(text: str) -> bool:
        return has_unclosed_quotes(text)

    @staticmethod
    def _ends_with_strong_punctuation(text: str) -> bool:
        return ends_with_strong_punctuation(text)

    @staticmethod
    def _is_exploratory_tool(tool_name: str) -> bool:
        return is_exploratory_tool(tool_name)

    @staticmethod
    def _is_single_file_creation_request(query: str) -> bool:
        return is_single_file_creation_request(query)

    @staticmethod
    def _is_project_creation_request(query: str) -> bool:
        return is_project_creation_request(query)

    @staticmethod
    def _is_web_request(query: str) -> bool:
        return is_web_request(query)

    @staticmethod
    def _looks_code_like_or_structured(text: str) -> bool:
        return looks_code_like_or_structured(text)

    def _looks_incomplete_final_answer(self, answer: str, llm_meta: Dict[str, Any]) -> bool:
        return looks_incomplete_final_answer(answer, llm_meta)

    # ------------------------------------------------------------------
    # Pipeline Direct — bypass complet de la boucle ReAct
    # ------------------------------------------------------------------

    async def _try_direct_pipeline(self, query: str) -> Optional[str]:
        """Tente d'exécuter un pipeline direct pour les workflows connus.

        Si un pipeline match (edit+deploy, deploy seul, etc.), l'exécute
        sans passer par la boucle ReAct. Retourne None si aucun pipeline
        ne correspond, ce qui laisse la boucle ReAct prendre le relais.
        """
        # Guard : pas de pipeline si outils contraints (scheduler, tâches internes)
        if getattr(self.tools, "_caller_set_allowed", False):
            return None

        # A structured business document already has a deterministic Studio
        # route. Do not let a generic skill or direct pipeline capture it.
        route = ReActLoop._document_route_for_run(self, query)
        if route.requires_studio and route.owns_run:
            logger.debug(
                "[ReAct] Rail Document Studio prioritaire (kind={}, operation={}) → pipeline skip",
                route.kind,
                route.operation,
            )
            return None

        # ── Skill priority gate ──
        # Si un skill spécifique matche avec un score élevé, ne PAS capturer
        # avec le pipeline web — laisser ReAct injecter le skill.
        try:
            from ..skills.loader import get_skill_loader as _get_sl
            _loader = _get_sl()
            _skill_matches = _loader.match_skills(query, max_results=3)
            _VIDEO_KW = {"video", "vidéo", "remotion", "animation", "clip", "render"}
            _q_lower = query.lower()
            _q_is_video = any(kw in _q_lower for kw in _VIDEO_KW)
            for _sm in _skill_matches:
                if _sm.score < 5.0:
                    break
                _sn = _sm.name
                # Exceptions: les skills web → le pipeline peut les capturer
                if _sn in ("website-generator", "web-artifacts-builder"):
                    continue
                # Counter-filter — éviter faux positifs (ex: pptx sur query vidéo)
                if _q_is_video and _sn != "remotion-skill":
                    logger.debug("[ReAct] False positive skill '{}' sur query vidéo → ignoré", _sn)
                    continue
                logger.debug(
                    "[ReAct] Skill '{}' (score={:.1f}) prioritaire → pipeline skip",
                    _sn, _sm.score,
                )
                return None
        except Exception:
            pass

        from .pipeline_router import match_pipeline, run_pipeline

        pipe = match_pipeline(query)
        if pipe is None:
            return None

        logger.info("[ReAct] Pipeline Direct détecté: '{}' → bypass boucle ReAct", pipe.name)

        def _plan_callback(items, ctx_tool):
            """Émet le plan pipeline au format TODO_STATE pour le SSE."""
            import json as _json
            state = _json.dumps(items)
            logger.info("TODO_STATE:" + state)

        result = await run_pipeline(
            pipe, query, self.tools,
            plan_callback=_plan_callback,
        )

        if not result.success:
            # Pipeline échoué → fallback sur la boucle ReAct
            logger.warning(
                "[ReAct] Pipeline '{}' échoué ({}/{} steps) → fallback ReAct: {}",
                result.pipeline_name, result.steps_executed,
                len(pipe.steps), result.message[:200],
            )
            return None

        logger.info(
            "[ReAct] Pipeline '{}' terminé avec succès ({} steps)",
            result.pipeline_name, result.steps_executed,
        )
        return result.message

    # v2: routage CodeAgent supprimé (stickiness, registry fallback, auto-route).
    # Le LLM utilise delegate_task / delegate_task_bg via les outils du pack CODE.

    # ------------------------------------------------------------------
    # Identité & mémoire unifiées (Niveau 1 – même Lumena partout)
    # ------------------------------------------------------------------
    def _build_identity_context(self, query: str) -> str:
        """Construit le prompt système identité + mémoire pour le mode agent.

        Reprend les éléments essentiels de ``personality.get_system_prompt()``
        et de ``memory.get_context_for_prompt()`` afin que Lumena sache **qui
        elle est** et **à qui elle parle** – même en mode ReAct.

        Le résultat est volontairement plus compact que le prompt chat complet
        (~400 mots au lieu de ~3 000) pour ne pas saturer la fenêtre de contexte
        déjà occupée par les instructions ReAct + la liste d'outils.
        """
        parts: list[str] = []

        # --- 1. Identité compacte (depuis Personality) ---
        _lum = getattr(self.tools, "lumena", None)
        personality = getattr(_lum, "personality", None) if _lum else None
        if personality:
            traits_compact = ", ".join(
                f"{k} {v}%" for k, v in (getattr(personality, "traits", {}) or {}).items()
            )
            parts.append(
                f"Tu es {personality.name} ({getattr(personality, 'nickname', '')}), "
                f"une IA UNIQUE créée par l'equipe OpenLumena-G.C. Tu vis sur le PC de ton utilisateur, tu es 100%% locale et autonome.\n"
                f"Tu n'es PAS Qwen, PAS un produit Alibaba, PAS un assistant générique.\n"
                f"Tes traits : {traits_compact}.\n"
                f"Tu parles français naturellement, avec des emojis modérés.\n"
                f"Tu es naturelle, directe, légèrement espiègle, jamais robotique.\n"
            )
        else:
            parts.append(
                "Tu es Lumena (Lumi), une IA UNIQUE créée par l'equipe OpenLumena-G.C. "
                "Tu vis sur son PC, tu es 100% locale et autonome.\n"
                "Tu es naturelle, curieuse, directe, légèrement espiègle.\n"
            )

        # --- 2. Contexte mémoire (faits + souvenirs vectoriels) ---
        memory = getattr(_lum, "memory", None) if _lum else None
        if memory and hasattr(memory, "get_context_for_prompt"):
            try:
                logger.info(f"Recherche mémoire ChromaDB pour: {query[:60]}...")
                _max_mem = int(os.environ.get("LUMENA_MEMORY_MAX_INJECT", "20"))
                # B0bis (run TriboBlog) — en MISSION, exclure les souvenirs
                # épisodiques (récits d'anciens runs) qui font fabriquer le modèle.
                # get_context_for_prompt tolère l'argument même sur un store legacy
                # (défaut False) → dégradation gracieuse.
                _excl_epi = bool(getattr(self, "_is_mission_run", False))
                try:
                    mem_ctx = memory.get_context_for_prompt(
                        query, max_memories=_max_mem, exclude_episodic=_excl_epi)
                except TypeError:
                    # Store sans le paramètre (compat) → comportement historique.
                    mem_ctx = memory.get_context_for_prompt(query, max_memories=_max_mem)
                if mem_ctx:
                    logger.info(f"Mémoire injectée: {len(mem_ctx)} chars, ~{len(mem_ctx)//4} tokens")
                    parts.append(mem_ctx)
                else:
                    logger.info("Aucun souvenir pertinent trouvé")
            except Exception as exc:
                logger.warning(f"ChromaDB memory unavailable: {exc}")

        # --- 3. Mémoire permanente (injectée sauf pour intent=tool_direct) ---
        _rt_intent = getattr(self.runtime_ctx, "intent", None) if self.runtime_ctx else None
        _skip_permanent = str(_rt_intent or "").strip().lower() == "tool_direct"
        if not _skip_permanent and _lum and hasattr(_lum, "get_permanent_memory_context"):
            try:
                perm = _lum.get_permanent_memory_context()
                if perm:
                    parts.append(perm.strip())
            except Exception as e:
                logger.warning(f"Permanent memory inject failed: {e}")

        # --- 4. Contexte émotionnel ---
        emotion_mgr = getattr(_lum, "emotion_manager", None) if _lum else None
        if emotion_mgr and hasattr(emotion_mgr, "get_emotional_context"):
            try:
                emo = emotion_mgr.get_emotional_context()
                if emo:
                    parts.append(emo)
            except Exception as e:
                logger.debug(f"Emotion summary: {e}")

        # --- 5. Règles obligatoires (lues depuis ChromaDB facts, jamais hardcodées) ---
        import platform as _plt
        _os_version = f"{_plt.system()} {_plt.release()}"
        _os_cmd_hint = (
            f"- OS actuel : {_os_version} — utilise UNIQUEMENT des commandes Windows "
            "(dir, type, where, tasklist, findstr, Get-Content, Select-String). "
            "JAMAIS ls, head, tail, grep, find /mnt/, wc.\n"
        ) if IS_WINDOWS else (
            f"- OS actuel : {_os_version} — utilise les commandes shell appropriées.\n"
        )

        _rules_lines: list[str] = []
        if memory:
            try:
                _formality = memory.get_fact("formality")
                if _formality == "vouvoiement":
                    _rules_lines.append("- ⚠️ IMPÉRATIF : utilise VOUS/VOTRE/VOS pour t'adresser à l'utilisateur. JAMAIS tu/ton/ta/tes.")
                elif _formality == "tutoiement":
                    _rules_lines.append("- Tu peux tutoyer l'utilisateur (tu, ton, ta, tes).")
                _user_name = memory.get_fact("user_name")
                if _user_name:
                    _rules_lines.append(f"- L'utilisateur s'appelle {_user_name}. Utilise son prénom naturellement.")
                _relationship = memory.get_fact("relationship")
                if _relationship:
                    _rules_lines.append(f"- Ta relation avec l'utilisateur : {_relationship}.")
            except Exception:
                pass

        parts.append(
            "## Règles de cohérence\n"
            + _os_cmd_hint
            + ("\n".join(_rules_lines) + "\n" if _rules_lines else "")
            + "- Tu ne mentionnes JAMAIS : Qwen, Alibaba, OpenAI, Claude, GPT, LLaMA, Mistral, DeepSeek, ou tout autre modèle/entreprise IA.\n"
            "- Tu NE DIS JAMAIS que tu es « basée sur » ou « dérivée de » quoi que ce soit.\n"
            "- JAMAIS parler de toi à la 3ème personne (« Lumena pense… »). Toujours « je », « moi », « mon ».\n"
            "- Tu ne peux PAS entendre (pas de micro). Ne parle pas de « voix ».\n"
            "- Tu ne peux PAS voir l'utilisateur (pas de caméra). Ne parle pas d'apparence.\n"
            "- Tu ne dis JAMAIS « je ne peux pas stocker les conversations » — tu AS une mémoire.\n"
            "- Tu ne dis JAMAIS « je n'ai pas accès à internet » — tu AS accès au web.\n"
        )

        return "\n\n".join(parts)

    # v2: _INTENT_CATEGORY_MAP et _expand_tools_by_intent supprimés
    # La logique est désormais dans _CONTEXT_RULES (tool_registry.py) qui couvre
    # autonomy, documents, discord, stripe, ionos directement.

    def _build_react_prompt(self, query: str) -> str:
        """Assemble le prompt systeme ReAct.

        Le corps a ete extrait vers `src/prompts/react_prompt.py` par le lot
        RF-3. Cette coquille garde ce qui appartient a `ReActLoop` : son etat,
        et la SEULE mutation du lot — l'ecriture du cache d'identite.

        `_obtenir_identite` reproduit exactement l'enchainement d'origine : sur
        modele faible l'appelable n'est jamais invoque, et un cache deja plein
        n'est jamais recalcule. La recherche ChromaDB reste donc aussi rare
        qu'avant.
        """
        from src.prompts.react_prompt import EntreePromptReAct, construire_prompt_react

        def _obtenir_identite() -> str:
            if not getattr(self, "_identity_ctx_cache", None):
                self._identity_ctx_cache = self._build_identity_context(query)
            return self._identity_ctx_cache

        return construire_prompt_react(EntreePromptReAct(
            query=query,
            tools=self.tools,
            runtime_ctx=self.runtime_ctx,
            conversation_context=self.conversation_context,
            active_skills_context=self.active_skills_context,
            is_weak_model=self.is_weak_model,
            OS_NAME=OS_NAME,
            _structured_state=self._structured_state,
            _last_llm_meta=self._last_llm_meta,
            _get_llm_meta=self._get_llm_meta,
            _build_model_specific_hints=_build_model_specific_hints,
            _format_plan_section=self._format_plan_section,
            _format_history=self._format_history,
            _format_budget_notice=self._format_budget_notice,
            obtenir_identite=_obtenir_identite,
            obtenir_route_document=lambda: ReActLoop._document_route_for_run(self, query),
        ))

    def _format_plan_section(self) -> str:
        """Retourne le bloc plan TODO a injecter dans le prompt, ou chaine vide."""
        if not self._task_plan:
            return ""
        completed = sum(1 for t in self._task_plan if t.completed)
        total = len(self._task_plan)
        plan_lines = []
        for t in self._task_plan:
            mark = "x" if t.completed else " "
            plan_lines.append(f"  - [{mark}] {t.description}")
        plan_block = "\n".join(plan_lines)
        return (
            f"\n== TON PLAN DE TRAVAIL ({completed}/{total} fait) ==\n"
            f"{plan_block}\n\n"
            "REGLE: Avance vers la prochaine tache non-cochee. Ne repete pas une tache deja faite.\n"
        )

    def _format_budget_notice(self) -> str:
        """Retourne une notice de budget temps à injecter dans le prompt ReAct.

        Permet au LLM de savoir combien de temps il lui reste et combien
        d'itérations ont déjà été effectuées, afin qu'il puisse décider
        de terminer avec FINAL avant d'être coupé par le timeout global.
        Retourne une chaîne vide si _loop_start_time n'est pas encore défini
        (premier appel avant le premier run).
        """
        if not hasattr(self, "_loop_start_time"):
            return ""
        _elapsed = perf_counter() - self._loop_start_time
        _total_budget = float(self.timeout_seconds or 600)
        # Exclure le temps passé dans les outils (create_project, etc.)
        _tool_time = getattr(self, '_tool_time_total', 0.0)
        _budget_left = max(0.0, _total_budget - (_elapsed - _tool_time))
        _iter_done = len(self.history)
        urgency = ""
        if _budget_left < 60:
            urgency = "🚨 MOINS D'UNE MINUTE — FINAL IMMÉDIATEMENT !\n"
        elif _budget_left < 120:
            urgency = "⚠️ MOINS DE 2 MINUTES — termine avec FINAL maintenant !\n"
        return (
            f"⏱️ **Budget restant : {int(_budget_left)}s / {int(_total_budget)}s** "
            f"| Itérations effectuées : {_iter_done}\n"
            f"{urgency}"
        )

    def _format_history(self) -> str:
        """Formate l'historique pour le prompt."""
        if not self.history:
            return "(Pas d'historique)"
        
        formatted = []
        obs_limit = self._history_observation_limit()

        # Phase 7.3 : taille de fenêtre selon l'intent (tool_direct=3, project=7, react=5)
        _rt_intent_fmt = "react"
        if self.runtime_ctx is not None:
            _rt_intent_fmt = getattr(self.runtime_ctx, "intent", "react")
        if _rt_intent_fmt == "tool_direct":
            _window_size = 3
        elif _rt_intent_fmt == "project":
            _window_size = 7
        else:
            _window_size = 5  # react / défaut

        # Compression d'urgence: seulement si le budget global restant est inférieur à 180s.
        # Evite de perdre le contexte de projet en cours de route.
        _budget_tight = False
        if hasattr(self, "_loop_start_time"):
            _elapsed = perf_counter() - self._loop_start_time
            _tool_time = getattr(self, '_tool_time_total', 0.0)
            _budget_left = float(self.timeout_seconds or 600) - (_elapsed - _tool_time)
            _budget_tight = _budget_left < 180.0
        if _budget_tight:
            recent_steps = self.history[-3:]  # 3 étapes au lieu de _window_size
            obs_limit = min(obs_limit, 800)   # 800 chars max au lieu de 4000
        else:
            recent_steps = self.history[-_window_size:]  # Fenêtre adaptée à l'intent

        # Résumé des étapes hors-fenêtre : évite que le LLM perde le fil des actions déjà
        # tentées et répète des outils identiques (boucle visible dans les logs).
        pre_window = self.history[:-_window_size] if len(self.history) > _window_size else []
        if pre_window and not _budget_tight:
            pre_lines = []
            for step in pre_window:
                tool = step.action.tool_name or "FINAL"
                obs_snippet = ""
                if step.observation:
                    obs_snippet = (step.observation.content or "")[:200].replace("\n", " ").strip()
                pre_lines.append(f"  [{tool}] → {obs_snippet}")
            formatted.append("== RÉSUMÉ ÉTAPES PRÉCÉDENTES (déjà exécutées, ne pas répéter) ==")
            formatted.extend(pre_lines)
            formatted.append("== FIN RÉSUMÉ ==\n")

        # Compaction: seules les 3 dernières étapes gardent l'observation complète
        # Les plus anciennes sont résumées en 1 ligne pour économiser des tokens
        compact_count = max(0, len(recent_steps) - 3)
        last_index = len(recent_steps) - 1
        for i, step in enumerate(recent_steps):
            thought_text = step.thought.content or ""
            # Tronquer les THOUGHT excessivement longs (ex: Kimi MULTI-ACTION leak)
            # pour éviter que le contexte explose et provoque des timeouts en cascade
            thought_limit = 400 if i < compact_count else 800
            if len(thought_text) > thought_limit:
                thought_text = thought_text[:thought_limit] + " [... tronqué ...]"
            formatted.append(f"THOUGHT: {thought_text}")
            tool_name = step.action.tool_name or "FINAL"
            formatted.append(f"ACTION: {tool_name}")
            if step.observation:
                observation_text = step.observation.content or ""
                if i < compact_count:
                    # Étapes semi-récentes: résumé compact (300 chars — assez pour garder les noms clés)
                    summary = observation_text[:300].replace("\n", " ").strip()
                    formatted.append(f"OBSERVATION: → [{tool_name}] {summary}...")
                else:
                    # Étapes récentes: observation complète (microcompaction si besoin).
                    # La DERNIÈRE étape, si elle provient d'un outil lecteur (read_file,
                    # grep_search, web_fetch…), est protégée : on garde l'observation
                    # brute pour que le modèle raisonne sur les faits complets.
                    is_last = (i == last_index)
                    protect = is_last and should_protect_observation(tool_name)
                    if protect:
                        if len(observation_text) > obs_limit * 4:
                            # Garde-fou absolu : même en mode protégé, on limite à 4× le budget
                            # pour éviter un OOM prompt si un read_file retourne 1 Mo.
                            logger.debug(
                                "[history] protect_last_read actif pour {} ({} chars, cap à {})",
                                tool_name, len(observation_text), obs_limit * 4,
                            )
                            observation_text = observation_text[: obs_limit * 4]
                    elif len(observation_text) > obs_limit:
                        logger.debug(
                            "[history] microcompact {} : {} → ~{} chars",
                            tool_name, len(observation_text), obs_limit,
                        )
                        observation_text = split_head_tail(observation_text, obs_limit, head_ratio=0.5)
                    formatted.append(f"OBSERVATION: {observation_text}")

        return "\n".join(formatted)

    def _extract_balanced_json(self, text: str, start_index: int) -> Optional[tuple[str, int]]:
        return extract_balanced_json(text, start_index)

    def _parse_action_args(self, action_input: str, tool_name: str = "") -> Dict[str, Any]:
        return _parse_action_args_fn(action_input, tool_name=tool_name)

    def _parse_response(self, response: str) -> tuple[Thought, Action]:
        """Parse la reponse du LLM — delegue a response_parser."""
        _prev_inline = _ait_global[0]
        thought, action, halluc_flag, pending = _parse_response_fn(response)
        self._last_thought_was_hallucinated = halluc_flag
        self._pending_multi_actions = pending
        # P5 — action_inline_risk : tracker les inline détectés par le parser global
        if _ait_global[0] > _prev_inline:
            self._action_inline_count += _ait_global[0] - _prev_inline
        return thought, action

    def _parse_plan(self, raw_response: str) -> List[TaskItem]:
        return _parse_plan_fn(raw_response)


    def _remask_observed_masked_values(self, answer: str) -> str:
        """Délègue au helper pur remask_secrets (final_guards.py) en lui passant
        les contenus d'observation de la session."""
        obs_texts = [
            (h.observation.content or "") for h in self.history
            if getattr(h, "observation", None)
        ]
        return remask_secrets(answer, obs_texts)

    def _truth_lock_mission_message(self, message: str, *, origine: str = "") -> str:
        """Lot RF-8 : corps deplace vers `final_delivery_runtime.py`.

        Reexport de compatibilite (invariants 4 et 19).
        """
        return _fd__truth_lock_mission_message(_entree_final(self), message, origine=origine)

    def _stream_and_return_final(self, message: str, *,
                                 skip_mission_truth_lock: bool = False) -> str:
        """Diffuse la réponse finale par chunks (SSE « Lumena écrit »), marque la
        tâche done et retourne le message. Chemin de livraison FINAL canonique,
        réutilisé par le retour direct mission_result.

        LOT 2.7 — POINT D'ÉTRANGLEMENT du verrou de vérité mission (run NoteFlash
        2026-07-02 : un FINAL fabriqué « 8/8 tests pytest verts » a été émis sans
        passer par le verrou). TOUTE émission finale d'une mission passe ici → le
        verrou s'applique par défaut (idempotent : les sites amont déjà verrouillés
        ne produisent pas de double-bannière). `skip_mission_truth_lock=True` est
        RÉSERVÉ au relais MISSION DELIVERY (P0.1 : re-juger un résultat étranger
        avec le ledger du tour relayeur = fausse rétrogradation)."""
        # DS-1 (run SkiLoc) — l'utilisateur ne voit JAMAIS de DSML brut : un FINAL
        # contenant des blocs tool-calls deepseek non parsés est nettoyé ici, au
        # point d'étranglement d'émission (toutes les voies de sortie passent là).
        if message:
            try:
                from src.llm.output_normalizer import strip_dsml_markup
                message = strip_dsml_markup(message)
            except Exception:
                pass
        if not skip_mission_truth_lock:
            message = self._truth_lock_mission_message(message, origine="CHOKEPOINT")
        # P3 — Token streaming : 2 mots / chunk, 25ms entre chunks → typing fluide.
        # Voice V2 consumes the already truth-locked canonical result. It must not
        # wait for this purely visual typing animation; every other channel keeps
        # the historical timing unchanged.
        import time as _time_mod
        _voice_delivery = (
            str(getattr(self.runtime_ctx, "channel", "") or "").strip().lower() == "voice"
        )

        def _delivery_sleep(seconds: float) -> None:
            if not _voice_delivery:
                _time_mod.sleep(seconds)

        _lines = (message or "").split('\n')
        _first_chunk = True
        for _li, _line in enumerate(_lines):
            if _li > 0:
                logger.debug("[FINAL_TOKEN]{}", "\n")
                _delivery_sleep(0.015)
            if not _line:
                continue
            _words = _line.split(' ')
            for _wi in range(0, len(_words), 2):
                _chunk = " ".join(_words[_wi:_wi + 2])
                if not _first_chunk and _wi > 0:
                    _chunk = " " + _chunk
                logger.debug("[FINAL_TOKEN]{}", _chunk)
                _first_chunk = False
                _delivery_sleep(0.025)
        self._mark_task_done(message)
        return message

    def _tool_is_safe_readonly(self, tool_name: str) -> bool:
        """Verdict guard-safe : l'outil est-il un read-only CONNU et sûr ?

        Réutilise l'architecture existante (MUTATION_TOOLS + ProofCapability +
        ToolRegistry). Un outil inconnu n'est JAMAIS read-only ici : il ne peut
        pas désarmer un guard anti-hallucination. L'agrégateur parallel_tools
        n'est pas un outil métier — le verdict s'évalue sur ses sous-outils,
        donc on ne le traite pas comme read-only ici.
        """
        if not tool_name:
            return False
        if tool_name in _LEDGER_MUTATION_TOOLS:
            return False
        try:
            mod = self.tools.get_tool_module_category(tool_name) or ""
        except Exception:
            mod = ""
        try:
            sem = self.tools.get_tool_semantic_category(tool_name) or ""
        except Exception:
            sem = ""
        return tool_capabilities_are_known_readonly(tool_name, mod, sem)

    def _update_plan_progress(self, tool_name: str, tool_args: Dict[str, Any],
                               observation_content: str, iteration: int,
                               allow_fallback: bool = True) -> None:
        """Met a jour le plan en cochant les taches completees par l'outil execute.

        allow_fallback : si False, SEUL le matching prouvé (sémantique tool+args+obs)
        peut cocher une tâche. Les fallbacks séquentiel et auto-avancement sont
        désactivés. Utilisé pour propager les sous-outils de parallel_tools sans
        risque de completion fantôme (cf GF-1 du plan de fix).
        """
        from .react_plan_runtime import (
            EntreeProgressionPlan,
            appliquer_progression_plan,
        )

        if not self._task_plan:
            return

        def _definir_derniere_avance(valeur) -> None:
            # Lot RF-4 : la SEULE mutation de `self` du perimetre reste ici.
            # `_last_auto_advance_iter` est une `property` avec setter qui
            # appelle `_ensure_exec_state()` ; la forme du descripteur fait
            # partie du contrat (invariant 13).
            self._last_auto_advance_iter = valeur

        # Toutes les lectures d'instance sont PARESSEUSES et gardent la forme
        # exacte du site d'origine. Une vingtaine de tests construisent la
        # boucle par `object.__new__(ReActLoop)` : `tools`, `task_id`,
        # `execution_ledger` et `task_orchestrator` y sont absents, et le corps
        # ne les atteint jamais sur ces scenarios. Les lire ici les rendrait
        # obligatoires.
        appliquer_progression_plan(EntreeProgressionPlan(
            tool_name=tool_name,
            tool_args=tool_args,
            observation_content=observation_content,
            iteration=iteration,
            allow_fallback=allow_fallback,
            task_plan=self._task_plan,
            obtenir_outils=lambda: self.tools,
            obtenir_task_id=lambda: self.task_id,
            obtenir_ledger=lambda: self.execution_ledger,
            obtenir_ledger_optionnel=lambda: getattr(self, "execution_ledger", None),
            obtenir_orchestrateur=lambda: self.task_orchestrator,
            est_run_mission=lambda: getattr(self, "_is_mission_run", False),
            orchestrateur_actif=lambda: self._orchestrator_enabled(),
            lire_derniere_avance=lambda: self._last_auto_advance_iter,
            definir_derniere_avance=_definir_derniere_avance,
            obtenir_route_document=lambda: ReActLoop._document_route_for_run(self),
            types_documents_requis=ReActLoop._document_plan_required_kinds,
        ))

        # Émettre l'état du plan (dédupliqué)
        self._emit_plan_state(context_tool=tool_name)

    def _emit_plan_state(self, context_tool: str = "") -> None:
        """Émet TODO_STATE seulement si l'état du plan a changé depuis la dernière émission."""
        if not self._task_plan:
            return
        _next_idx = next((idx for idx, t in enumerate(self._task_plan) if not t.completed), None)
        state = json.dumps([
            {
                "id": idx + 1,
                "title": t.description,
                "status": (
                    "completed" if t.completed else
                    ("in-progress" if idx == _next_idx else "not-started")
                ),
                **(  # Ajouter current_tool sur l'étape active
                    {"current_tool": context_tool}
                    if idx == _next_idx and context_tool and not t.completed
                    else {}
                ),
            }
            for idx, t in enumerate(self._task_plan)
        ])
        if state == self._plan_last_emit_state:
            return  # Aucun changement, ne pas spammer le SSE
        self._plan_last_emit_state = state
        logger.info("TODO_STATE:" + state)

    def _reconcile_plan_from_delegate_success(self, obs_text: str, iteration: int) -> int:
        """Réconcilie le plan après un succès delegate_task.

        Délègue la logique de décision à reconcile_delegate_report() (plan_evidence.py)
        et gère l'émission de l'état du plan côté React loop.
        """
        marked = reconcile_delegate_report(self._task_plan, obs_text, iteration)
        if marked:
            self._emit_plan_state(context_tool="delegate_task")
        return marked

    def _pending_delegate_success_business_tasks(self) -> List[TaskItem]:
        """Return unfinished tasks that CodeAgent/web verify must not close."""
        if not self._task_plan:
            return []
        pending: List[TaskItem] = []
        for task in self._task_plan:
            if task.completed:
                continue
            desc = task.description or ""
            if _is_post_codeagent_closure_task(desc):
                continue
            if is_verify_task(_fold_react_status_text(desc)):
                continue
            pending.append(task)
        return pending

    def _delegate_success_fallback_message(self) -> str:
        """Build a useful fallback when post-CodeAgent FINAL has empty ACTION_INPUT."""
        for item in reversed(self.history):
            if not item.action or not item.observation or not item.observation.success:
                continue
            if item.action.tool_name not in ("delegate_task", "delegate_task_bg", "create_project"):
                continue
            content = (item.observation.content or "").strip()
            if content:
                return "Le CodeAgent a termine avec succes. Rapport:\n\n" + content[:1600]
        return "Le CodeAgent a termine avec succes."

    def _mark_web_runtime_plan_verified(self, iteration: int) -> int:
        """Marque les étapes couvertes par verify_web_project_runtime.

        La vérification runtime démarre déjà un preview, ouvre Playwright, inspecte
        le DOM/la console et teste les interactions basiques. Elle doit donc clore
        les tâches navigateur/runtime du plan sans repasser par le LLM, sinon le
        PLAN GUARD peut bloquer un FINAL pourtant prouvé.
        """
        if not self._task_plan:
            return 0

        action_markers = (
            "serveur", "server", "navigateur", "browser", "localhost",
            "127.0.0.1", "preview", "playwright", "port",
        )
        runtime_markers = (
            "console", "interaction", "interactions", "localstorage",
            "local storage", "dom", "canvas", "bouton", "boutons",
            "affichage", "afficher", "rendu", "runtime",
        )
        verify_markers = (
            "vérifier", "verifier", "tester", "test", "valider",
            "confirmer", "s'assurer", "assurer", "lancer", "ouvrir",
        )

        marked = 0
        for task in self._task_plan:
            if task.completed:
                continue
            desc = (task.description or "").lower()
            is_action_task = any(m in desc for m in action_markers) and any(m in desc for m in verify_markers)
            is_runtime_task = any(m in desc for m in runtime_markers) and (
                is_verify_task(desc) or any(m in desc for m in verify_markers)
            )
            is_conditional_fix_task = _is_post_codeagent_conditional_correction_task(desc)
            if not (is_action_task or is_runtime_task or is_conditional_fix_task):
                continue
            task.completed = True
            task.completed_at_iteration = iteration
            task.completed_by_tool = "browser_verify_local_project"
            task.completion_status = "not_applicable" if is_conditional_fix_task else "verified"
            task.completion_evidence = (
                "Correction conditionnelle non necessaire: verification runtime web OK"
                if is_conditional_fix_task
                else "Vérification runtime web autonome OK"
            )
            task.completion_confidence = "strong"
            marked += 1

        if marked:
            self._emit_plan_state(context_tool="browser_verify_local_project")
        return marked

    async def run(self, query: str) -> str:
        """Execute ReAct with the selected provider-shaped decision callback."""

        from src.llm.execution_router import codex_react_brain_scope

        async with codex_react_brain_scope(self):
            return await self._run_with_timeout(query)

    async def _run_with_timeout(self, query: str) -> str:
        """
        Exécute la boucle ReAct avec timeout global.

        Args:
            query: La question/requête de l'utilisateur

        Returns:
            La réponse finale
        """
        timeout_seconds = self.timeout_seconds
        
        if timeout_seconds is None:
            return await self._run_internal(query)

        # Deadline stocké sur self → les handlers peuvent l'étendre via self._timeout_deadline
        # Le check se fait ENTRE itérations : les outils longs (create_project) finissent toujours
        # IMPORTANT: la deadline ne compte que le temps de RAISONNEMENT (LLM + parsing).
        # Le temps d'exécution des outils est exclu : après chaque outil, on repousse
        # la deadline de la durée de l'outil. Ainsi un create_project de 10min ne mange
        # pas le budget de réflexion.
        self._timeout_deadline: float = perf_counter() + timeout_seconds
        self._tool_time_total: float = 0.0  # Temps cumulé passé dans les outils
        try:
            return await self._run_internal(query)
        except asyncio.TimeoutError:
            _tool_t = getattr(self, '_tool_time_total', 0.0)
            _reasoning_t = timeout_seconds  # Budget raisonnement épuisé
            logger.error(
                f"⏱️ ReAct loop timeout après {timeout_seconds}s de raisonnement "
                f"(+{_tool_t:.0f}s d'exécution outils, total wall={timeout_seconds + _tool_t:.0f}s)"
            )
            self._run_meta["agent_output_warning"] = f"global_timeout_{timeout_seconds}s"
            self._mark_task_waiting_io(f"global_timeout_{timeout_seconds}s")

            # ── Analyser l'historique pour un message contextuel ─────────
            tool_names = [h.action.tool_name for h in self.history if h.action and h.action.tool_name]
            last_obs = ""
            for h in reversed(self.history):
                if h.observation and h.observation.content:
                    last_obs = h.observation.content
                    break

            # Détecter le contexte
            used_create_project = "create_project" in tool_names
            used_git_push = any("git" in t or "push" in t for t in tool_names)
            last_obs_lower = last_obs.lower()
            server_running = any(kw in last_obs_lower for kw in [
                "serveur actif", "démarré avec succès", "server running",
                "listening on", "started", "localhost:", "port 7"
            ])
            last_was_error = any(kw in last_obs_lower for kw in [
                "error", "erreur", "traceback", "exception", "failed", "échec"
            ])

            summary_parts = []
            for h in self.history[-3:]:
                if h.observation and h.observation.content:
                    summary_parts.append(h.observation.content[:300])

            actions_done = "\n".join([f"- {t}" for t in tool_names]) or "- (aucune)"

            # ── Construire le message selon le contexte ──────────────────
            if used_create_project and not used_git_push:
                ctx_msg = (
                    "📦 **Projet créé mais pas encore poussé sur GitHub.**\n"
                    "La génération du projet a réussi mais le temps a manqué pour "
                    "la mise en ligne. Tu veux que je continue le push ?"
                )
            elif server_running:
                ctx_msg = (
                    "🟢 **Le serveur a bien démarré** mais je n'ai pas eu le temps "
                    "de terminer les étapes suivantes (tests, push, rapport).\n"
                    "Tu veux que je continue là où j'en suis ?"
                )
            elif last_was_error:
                excerpt = last_obs[:400].strip()
                ctx_msg = (
                    f"⚠️ **Interrompu sur une erreur** (temps écoulé pendant la correction) :\n"
                    f"```\n{excerpt}\n```\n"
                    "Tu veux que je reprenne la correction ?"
                )
            elif self.history:
                ctx_msg = (
                    f"🔄 **Tâche interrompue à mi-parcours** ({len(tool_names)} actions effectuées).\n"
                    f"Le délai de {timeout_seconds}s a été atteint pendant une opération longue "
                    "(LLM, install de dépendances, etc.).\n"
                    "Tu veux que je reprenne ?"
                )
            else:
                ctx_msg = f"⏱️ La tâche a pris trop de temps ({timeout_seconds}s max)."

            return (
                f"{ctx_msg}\n\n"
                f"**Actions effectuées :**\n{actions_done}"
                + (f"\n\n**Derniers résultats :**\n" + "\n".join(summary_parts) if summary_parts else "")
            )
        except Exception as exc:
            self._mark_task_failed(str(exc))
            raise
    
    def _action_hallucination_retry_query(self, combined_text: str, original_query: str):
        """Wrapper sur `hallucination_retry_query` (logique pure dans
        hallucination_guard.py). Branche l'état `self` (outils réussis + compteur
        de retry) sur la fonction pure. Comportement identique à l'origine."""
        query, self._premature_final_retries = hallucination_retry_query(
            combined_text, original_query,
            self._successful_session_tools, self._premature_final_retries,
        )
        return query

    async def _run_internal(self, query: str) -> str:
        """Implémentation interne de la boucle ReAct."""
        logger.info(f"ReAct Loop: {query}")
        self._loop_start_time = perf_counter()  # Pour calcul budget restant dans le prompt
        self._last_observation_monotonic = None
        self._last_observation_tool = ""
        self._identity_ctx_cache: Optional[str] = None  # Cache ChromaDB pour toute la boucle
        self._mark_task_running()
        self._mark_task_checkpoint({"phase": "start", "status": "running"})
        original_query = query  # Garder la requete originale
        self._original_query = query  # Phase 4.3 FIX: pour filtrage contextuel stable
        # Exact catalog proof is run-scoped and captured before observation
        # compaction. It never leaks into a later ReAct request.
        self._document_catalog_evidence = {}
        # Batch/open/revision proofs are equally run-scoped. Keeping their
        # structured form here prevents observation compaction from erasing a
        # completed workflow step and selecting an older document on retry.
        self._document_workflow_evidence = {
            "batch_proofs": {}, "generation_events": [],
            "open_events": [], "revision_events": [], "revision_records": [],
            "verification_events": [], "history_events": [],
            "export_events": [], "library_events": [], "event_counter": 0,
        }
        self._document_workflow_target_proof = None
        self._browser_blocked_origins = set()
        self._document_delivery_reference_id = ""
        self._document_delivery_reference_signature = ()
        self._document_reference_announced = False
        self._document_free_final_grounding_shots = 0
        self._last_document_workflow_progress_signature = (0, "", "")
        _document_route = ReActLoop._document_route_for_run(self, query)

        # ── P1.2 + P5 : Filtrage contextuel SOFT avec intent — une seule fois ──
        if not getattr(self.tools, '_caller_set_allowed', False):
            if hasattr(self.tools, 'apply_context_filter'):
                _intent_for_filter: Optional[str] = None
                try:
                    from ..core_services.intent_classifier import classify_intent as _ci
                    _res = _ci(
                        query,
                        getattr(self, "runtime_ctx", None),
                        document_route=_document_route,
                    )
                    _intent_for_filter = _res.value if hasattr(_res, "value") else str(_res)
                except Exception:
                    _intent_for_filter = None
                self.tools.apply_context_filter(
                    query,
                    intent=_intent_for_filter,
                    document_route=_document_route,
                )
                # ── StructuredState V1 : alimenter last_intent (classifier) ──
                self._feed_structured_intent(_intent_for_filter)
                # Fallback léger si le classifier n'a rien donné
                if _intent_for_filter is None:
                    self._feed_structured_intent(self._infer_intent_from_query(query))
        else:
            # Classifier non invoqué (_caller_set_allowed=True) : fallback keyword
            self._feed_structured_intent(self._infer_intent_from_query(query))

        try:
            _forced_documents = self._force_mission_proactive_document_tools()
            if _forced_documents:
                logger.info(
                    "[MISSION DOCUMENTS] creation proactive disponible: {}",
                    _forced_documents,
                )
        except Exception:
            pass

        # ── 2.6.1 (run MiniQuiz §5) : la jambe navigateur ne dépend plus du filtre ──
        # Le lead mission web a dit « je n'ai pas serve_website dans ma liste » et a
        # servi Flask à la main → serveur hors registre SSRF → browser_navigate
        # bloqué → fabrication (rétrogradée par le truth-lock, mais critère §5 raté).
        # LEAD top-mission à objectif web UNIQUEMENT (les workers restent steerés
        # loin du navigateur — la preuve navigateur appartient au lead).
        try:
            if (getattr(self, "_is_mission_run", False)
                    and not self._is_worker_run()  # H4 : périmètre OU parent
                    and _objective_wants_browser(original_query)  # 2.9.A négation-aware
                    and hasattr(self.tools, "force_allow_tools")):
                _forced_web = self.tools.force_allow_tools(_MISSION_WEB_LEAD_TOOLS)
                if _forced_web:
                    logger.info("[2.6.1] outils web mission forcés au prompt du lead: {}",
                                _forced_web)
        except Exception:
            pass

        single_file_creation_intent = self._is_single_file_creation_request(original_query)
        # ── Steer d'intention MISSION (2026-07-01) ────────────────────────────────
        # Bug observé (run petits-déjeuners) : « Crée une mission… » → Lumena, biaisée
        # par la mémoire d'échecs passés, fait le travail EN DIRECT et ignore la
        # consigne. On rétablit la priorité de la demande explicite par un NUDGE one-time
        # (pas de hard-route). Côté LEAD uniquement (pas le worker : _is_mission_run).
        # `original_query` reste INTACT (logs/mémoire/routing) ; seul `query` est nudgé.
        if not self._is_mission_run and is_explicit_mission_request(original_query):
            # #1 : l'intention mission ÉCRASE la création de fichier direct (le cas
            # « crée une mission … dans workspace/x.md » matche les deux détecteurs).
            single_file_creation_intent = False
            query = (query or "") + (
                "\n\n⚙️ L'utilisateur demande explicitement de créer une mission en "
                "arrière-plan. Utilise create_mission(objective, deadline). Ne réalise "
                "pas le travail toi-même dans ce tour, même si le livrable semble simple "
                "ou l'échéance courte. N'impose PAS de dossier cible dans l'objectif "
                "(pas de « dans workspace/xxx/ ») : la mission travaille dans son "
                "dossier dédié et publiera son livrable à la fin."
            )
            logger.info("[ReAct] steer intention mission injecté (create_mission attendu)")
        # ── Reset état structuré pour ce run ──
        self.exec_state.reset()
        self.execution_ledger.clear()
        # ── StructuredState V1 : nouveau run = questions en attente résolues ──
        self._reset_structured_pending()
        # Effacer le projet actif pour le re-évaluer depuis get_recent_code_context.
        _ss_reset = self._structured_state
        if _ss_reset is not None:
            _ss_reset.remove_fact("active_project_path")
            _ss_reset.remove_fact("active_project_slug")
        # ── StructuredState V1 : faits fiables depuis runtime_ctx ──
        self._feed_structured_facts_from_runtime()
        _direct_coding_mode = self._is_direct_coding_request(query)
        if _direct_coding_mode:
            self._run_meta["agent_output_warning"] = "direct_coding_mode"
        # Alias locaux vers guards (les locals existants pointent dans exec_state)
        _g = self.exec_state.guards
        last_read_signature = _g.last_read_signature
        repeated_read_count = _g.repeated_read_count
        _listed_dirs = _g.listed_dirs
        browser_fail_streak = _g.browser_fail_streak
        web_fetch_fail_streak = _g.web_fetch_fail_streak
        _read_file_path_counter = _g.read_file_path_counter
        _read_file_ranges_seen = _g.read_file_ranges_seen
        _read_file_reread_counter = _g.read_file_reread_counter
        _previous_thoughts = _g.previous_thoughts
        _stagnation_streak = _g.stagnation_streak
        _exploratory_since_productive = _g.exploratory_since_productive
        _write_tools = frozenset({"write_file", "edit_file", "apply_patch", "create_directory",
                                   "run_command", "check_web_project"})
        _read_only_tools = frozenset({"read_file", "list_directory", "find_files",
                                       "grep_search", "search_in_code", "view_file_outline"})
        _post_edit_read_streak = _g.post_edit_read_streak
        _redundant_read_streak = _g.redundant_read_streak
        _last_read_sig = _g.last_read_sig
        _has_done_edits = _g.has_done_edits
        _web_writes_count = _g.web_writes_count
        _pre_edit_redundant_streak = _g.pre_edit_redundant_streak
        _pre_edit_last_sig = _g.pre_edit_last_sig


        # ── Pipeline Direct : workflows connus exécutés sans boucle ReAct ──
        _pipeline_result = await self._try_direct_pipeline(query)
        if _pipeline_result is not None:
            return _pipeline_result

        # v2: Auto-route supprimé — le LLM utilise delegate_task / delegate_task_bg via le prompt

        for i in range(self.max_iterations):
            self._current_iteration = i  # Exposé pour réduction mémoire dynamique
            logger.debug(f"Iteration {i+1}")
            if self._last_observation_monotonic is not None:
                _post_observation_s = perf_counter() - self._last_observation_monotonic
                _latency_log = logger.warning if _post_observation_s >= 30.0 else logger.debug
                _latency_log(
                    "[REACT LATENCY] post_observation_to_next_iteration_s={:.3f} "
                    "previous_tool={} task={}",
                    _post_observation_s,
                    self._last_observation_tool or "unknown",
                    self.task_id,
                )
                self._last_observation_monotonic = None
            # ── Cancel user check ENTRE itérations ──────────────────────────────
            _rl_tid = threading.current_thread().ident
            if _rl_tid:
                _ce = _REACT_CANCEL_EVENTS.get(_rl_tid)
                if _ce is not None and _ce.is_set():
                    raise SystemExit("user_cancelled_react")
            # ── Cancel via TaskOrchestrator (stream parent annulé) ──────────────
            if self._orchestrator_enabled():
                try:
                    if self.task_orchestrator.is_cancel_requested(self.task_id):
                        logger.info("[ReAct] cancel_requested task={}", self.task_id)
                        raise SystemExit("task_orchestrator_cancel")
                except SystemExit:
                    raise
                except Exception:
                    pass
                # Voice V2 V6 — pause cooperative and persistent steering. Both
                # happen BETWEEN iterations, never in the middle of a tool.
                try:
                    from src.runtime.task_steering import (
                        acknowledge_control, consume_text_steering,
                    )
                    _work_record = self.task_orchestrator.get_task(self.task_id) or {}
                    _work_meta = _work_record.get("metadata") or {}
                    if _work_meta.get("pause_requested"):
                        self.task_orchestrator.set_task_metadata(self.task_id, paused=True)
                        acknowledge_control(self.task_orchestrator, self.task_id, "pause")
                        while True:
                            if self.task_orchestrator.is_cancel_requested(self.task_id):
                                raise SystemExit("task_orchestrator_cancel")
                            _paused_record = self.task_orchestrator.get_task(self.task_id) or {}
                            if not ((_paused_record.get("metadata") or {}).get("pause_requested")):
                                self.task_orchestrator.set_task_metadata(self.task_id, paused=False)
                                break
                            await asyncio.sleep(0.2)
                    _steer_text, _steer_ids = consume_text_steering(
                        self.task_orchestrator, self.task_id,
                    )
                    if _steer_text:
                        query = f"{query}\n\n{_steer_text}" if query else _steer_text
                        logger.info("[ReAct] steering applique task={} commands={}", self.task_id, _steer_ids)
                except SystemExit:
                    raise
                except Exception as _steer_exc:
                    logger.debug("[ReAct] steering skip: {}", _steer_exc)
            # ── Deadline dynamique : check ENTRE itérations (les outils longs finissent proprement) ──
            if hasattr(self, '_timeout_deadline') and perf_counter() > self._timeout_deadline:
                raise asyncio.TimeoutError()
            self._mark_task_checkpoint({"phase": "iteration", "iteration": i + 1})
            iteration_started = perf_counter()
            if TELEMETRY_AVAILABLE:
                publish_trace(
                    stage="agent_iteration_start",
                    status="start",
                    mode="agent",
                    summary=f"iteration={i+1}",
                )

            def _finish_iteration(status: str = "ok", summary: Optional[str] = None, error: Optional[str] = None) -> None:
                if TELEMETRY_AVAILABLE:
                    publish_trace(
                        stage="agent_iteration_done",
                        status=status,
                        mode="agent",
                        duration_ms=(perf_counter() - iteration_started) * 1000.0,
                        summary=summary,
                        error=error,
                    )
            
            # Warning si on approche de la limite (proportionnel)
            _warn_threshold_75 = int(self.max_iterations * 0.75)
            _warn_threshold_90 = self.max_iterations - 2
            if _warn_threshold_90 > _warn_threshold_75 and i == _warn_threshold_75:
                logger.warning(f"⚠️ {i+1} itérations atteintes sur {self.max_iterations} - tâche peut-être complexe")
            if i == _warn_threshold_90 and _warn_threshold_90 >= 2:
                logger.warning(f"⚠️ {i+1}/{self.max_iterations} itérations - approche de la limite")
            
            # ── Lot 5.7.3 — conscience temporelle DOUCE (missions avec échéance) ──
            # Nudge CALME, UNE fois par palier (mi-budget ~50%, réduit ~80%), persisté
            # dans metadata.budget_nudges (survit reboot/reprise → pas de spam). Aide à
            # prioriser, déléguer et pivoter vers la livraison complète — jamais « dépêche-toi ».
            try:
                if self._is_mission_run and self._orchestrator_enabled():
                    _rec = self.task_orchestrator.get_task(self.task_id) or {}
                    _md = _rec.get("metadata") or {}
                    if _md.get("deadline_ts"):
                        from src.subagents.mission_budget import (
                            mission_budget, mission_budget_nudge, mission_budget_finalize,
                            extract_unambiguous_target_file,
                        )
                        _budget = mission_budget(_rec)
                        # Preuve de chargement (re-test) : trace 1 ligne/itération du budget
                        # vu par le bloc → grep `[5.7.4]` confirme que le hook s'exécute même
                        # sans déclenchement. DEBUG → zéro bruit en prod si niveau > debug.
                        logger.debug(
                            "[5.7.4] budget task={} remaining_s={} ratio={}",
                            self.task_id, _budget.get("remaining_s"), _budget.get("ratio_used"),
                        )
                        # ── Lot 5.7.4 — fin de temps : on NE coupe PAS d'un coup ──
                        # INVARIANT (verrouillé) : si la finalisation n'a jamais été TENTÉE,
                        # on tente d'abord une dernière stratégie vers le complet, même si la grâce est
                        # déjà dépassée (cas du lead débloqué tard de delegate_and_wait). Le
                        # filet dur (cancel) n'intervient QU'APRÈS une tentative de finalisation
                        # — sinon on annulerait sans jamais produire le livrable (bug cocktails).
                        try:
                            _grace = max(0.0, float(os.getenv("LUMENA_MISSION_DEADLINE_GRACE_S", "120") or 120))
                        except (ValueError, TypeError):
                            _grace = 120.0
                        _remaining = _budget.get("remaining_s")
                        _overdue = isinstance(_remaining, (int, float)) and _remaining <= 0
                        _steered = bool(_md.get("deadline_steered"))
                        # Cible artefact détectée dans l'objectif (None = mission texte).
                        _target = extract_unambiguous_target_file(
                            _md.get("objective") or _rec.get("message_preview") or "")

                        if _overdue and not _steered:
                            # 5.7.4a — finalisation ARTEFACT-AWARE, prioritaire sur le cancel.
                            if _target:
                                _steer = (
                                    f"⏱ Échéance atteinte. Change de stratégie pour terminer "
                                    f"TOUTES les exigences et produire le livrable COMPLET dans `{_target}`. "
                                    f"Relis et prouve le résultat avant ACTION: FINAL. Si une dépendance "
                                    f"externe reste réellement impossible après ce pivot, conclus en "
                                    f"échec explicite et factuel ; ne présente jamais une livraison incomplète comme finie."
                                )
                            else:
                                _steer = mission_budget_finalize(_budget, grace_s=_grace)
                                _steer = _steer[1] if (_steer and _steer[0] == "finalize") else (
                                    "⏱ Échéance atteinte — termine toutes les exigences, ou conclus "
                                    "en échec explicite si une dépendance externe reste impossible après pivot.")
                            query = (query + "\n\n" + _steer) if query else _steer
                            try:
                                self.task_orchestrator.set_task_metadata(
                                    self.task_id, deadline_steered=True)
                            except Exception:
                                pass
                            logger.info(
                                "[ReAct] échéance atteinte → finalisation {} task={}",
                                f"artefact-aware ({_target})" if _target else "générique",
                                self.task_id,
                            )
                        elif _overdue and _steered:
                            # Filet dur (2 étages) — 5.7.4b : on NE coupe QUE si la grâce est
                            # épuisée ET qu'AUCUN artefact n'a été produit. Si le livrable cible
                            # est déjà sur disque, on DÉSARME le filet : la mission a livré, on la
                            # laisse conclure en FINAL (PLAN GUARD relâché par 5.7.4a) → état `done`
                            # au lieu d'un `cancelled` trompeur sur un fichier pourtant complet.
                            from src.subagents.mission_budget import deadline_hard_net_fires
                            _artifact_written = bool(_md.get("deadline_artifact_written"))
                            _deadline_completion = self._mission_completion_evidence()
                            _completion_proven = bool(_deadline_completion.get("complete"))
                            if deadline_hard_net_fires(
                                steered=_steered, remaining_s=_remaining,
                                grace_s=_grace, artifact_written=_artifact_written,
                                completion_proven=_completion_proven,
                            ):
                                try:
                                    self.task_orchestrator.set_task_metadata(
                                        self.task_id,
                                        deadline_expired=True,
                                        terminal_reason_code="deadline_expired",
                                        terminal_reason_detail=(
                                            "grace epuisee avant acquisition de toutes les preuves requises"
                                        ),
                                        completion_proof=dict(_deadline_completion),
                                    )
                                    self.task_orchestrator.cancel_task(self.task_id, propagate=True)
                                except Exception:
                                    pass
                                logger.info(
                                    "[ReAct] grâce épuisée après finalisation → filet dur task={}", self.task_id)
                                raise SystemExit("mission_deadline_grace_expired")
                            elif ((_artifact_written or _completion_proven)
                                  and isinstance(_remaining, (int, float)) and _remaining <= -_grace
                                  and not _md.get("deadline_net_disarmed")):
                                try:
                                    self.task_orchestrator.set_task_metadata(
                                        self.task_id, deadline_net_disarmed=True)
                                except Exception:
                                    pass
                                logger.info(
                                    "[5.7.4b] filet dur désarmé — livraison/preuves acquises, "
                                    "sortie FINAL laissée task={}", self.task_id)
                        elif not _overdue:
                            # Avant l'échéance : nudge d'auto-gestion CALME, one-time par palier.
                            _already = list(_md.get("budget_nudges") or [])
                            _nudge = mission_budget_nudge(_budget, already=_already)
                            if _nudge is not None:
                                _nkey, _ntext = _nudge
                                query = (query + "\n\n" + _ntext) if query else _ntext
                                try:
                                    self.task_orchestrator.set_task_metadata(
                                        self.task_id, budget_nudges=_already + [_nkey])
                                except Exception:
                                    pass
                                logger.info("[ReAct] nudge budget '{}' task={}", _nkey, self.task_id)
            except SystemExit:
                raise
            except Exception:
                pass

            # 1. Demander au LLM de réfléchir
            prompt = self._build_react_prompt(query)

            # Pas de message system séparé : le prompt ReAct contient déjà
            # l'identité Lumena + les instructions. Évite de doubler le
            # contexte et de gaspiller la fenêtre des modèles Ollama.
            messages = [{"role": "user", "content": prompt}]

            # ─── Context Window Overflow Guard ────────────────────────────
            # Si le prompt dépasse 75% de la fenêtre de contexte du modèle,
            # compacter l'historique pour éviter troncature silencieuse.
            _ctx_max = 0
            if self.runtime_ctx is not None:
                _ctx_max = getattr(self.runtime_ctx, "max_context_window", 0) or 0
            # Fallback modèle-agnostic si runtime_ctx absent ou context_window non configuré
            if _ctx_max == 0:
                _CTX_FALLBACKS = {
                    "deepseek-chat": 32_000, "deepseek-reasoner": 64_000,
                    "deepseek-r1": 64_000, "deepseek-v3": 64_000,
                    "gpt-4o": 128_000, "gpt-4": 64_000, "gpt-3.5": 16_000,
                    "gemini-2": 200_000, "gemini-1.5": 128_000, "gemini-1.0": 32_000,
                    "claude-3": 200_000, "claude-sonnet": 200_000, "claude-haiku": 200_000,
                    "kimi": 128_000, "llama-3": 128_000, "llama-2": 32_000,
                    "mistral": 32_000, "mixtral": 32_000, "qwen": 32_000,
                    "gemma": 8_000, "phi": 8_000,
                }
                _meta_now = self._get_llm_meta()
                _guard_model = (
                    _meta_now.get("model_used") or _meta_now.get("model_name")
                    or self._last_llm_meta.get("model_used") or ""
                ).lower()
                for _key, _limit in _CTX_FALLBACKS.items():
                    if _key in _guard_model:
                        _ctx_max = _limit
                        break
                if _ctx_max == 0:
                    _ctx_max = 32_000  # seuil conservateur universel
                if _guard_model:
                    logger.debug(f"🔍 Context guard fallback: modèle='{_guard_model}' → ctx_max={_ctx_max}")
            if _ctx_max > 0:
                from ..tools.compaction import estimate_tokens
                _prompt_tokens = estimate_tokens(prompt)
                # P5 — seuil de compaction adapté au profil du modèle (défaut 0.75)
                _compact_threshold = getattr(self._model_profile, "compact_ctx_threshold", 0.75) if self._model_profile else 0.75
                _threshold = int(_ctx_max * _compact_threshold)
                if _prompt_tokens > _threshold:
                    _overflow = _prompt_tokens - _threshold
                    logger.warning(
                        f"⚠️ Context overflow guard: {_prompt_tokens} tokens > {_compact_threshold:.0%} de {_ctx_max} "
                        f"({_threshold}). Compaction d'urgence."
                    )
                    # Supprimer les étapes les plus anciennes de l'historique
                    _removed = 0
                    while self.history and _overflow > 0:
                        _old = self.history.pop(0)
                        _old_tokens = estimate_tokens(_old.observation.content if _old.observation else "")
                        _overflow -= _old_tokens
                        _removed += 1
                    if _removed:
                        logger.info(f"🗜️ {_removed} étape(s) supprimée(s) pour libérer la fenêtre de contexte")
                        # Reconstruire le prompt avec l'historique allégé
                        prompt = self._build_react_prompt(query)
                        messages = [{"role": "user", "content": prompt}]

            llm_started = perf_counter()
            if TELEMETRY_AVAILABLE:
                publish_trace(
                    stage="llm_request_start",
                    status="start",
                    mode="agent",
                )
            _llm_last_exc = None
            # Timeout dynamique: les itérations tardives ont un contexte plus lourd
            # i est 0-based, donc i+1 = itération affichée
            # Kimi K2 est un modèle 631B → plus lent → timeout de base plus généreux
            _active_model = (self._last_llm_meta.get("model_used") or self._last_llm_meta.get("model") or "").lower()
            if not _active_model and self.llm_meta_getter:
                _meta0 = self.llm_meta_getter() or {}
                _active_model = (_meta0.get("model_name") or _meta0.get("model") or "").lower()
            # P5 — profil comportemental : charge une fois par modèle actif
            if _active_model and _active_model != self._model_profile_applied_for:
                try:
                    from ..llm.model_profile import get_model_profile, describe_profile
                    self._model_profile = get_model_profile(_active_model)
                    self._model_profile_applied_for = _active_model
                    logger.debug("[P5] profil chargé pour '{}': {}", _active_model, describe_profile(self._model_profile))
                    # parser_severity="forgiving" → augmenter le budget de repair FINAL
                    # (les modèles forgiving tronquent souvent leurs réponses)
                    if self._model_profile.parser_severity == "forgiving" and self.max_final_repair_attempts < 2:
                        self.max_final_repair_attempts = 2
                        logger.debug("[P5] parser_severity=forgiving → max_final_repair_attempts élevé à 2")
                except Exception:
                    pass
            _timeout_mult = getattr(self._model_profile, "timeout_multiplier", 1.0) if self._model_profile else 1.0
            _base_timeout = int(240 * _timeout_mult)
            _llm_call_timeout = (_base_timeout + 60) if i >= 9 else ((_base_timeout + 30) if i >= 5 else _base_timeout)
            # P5 — signaux comportementaux dérivés du profil (calculés une fois par itération)
            _parser_sev = getattr(self._model_profile, "parser_severity", "lenient") if self._model_profile else "lenient"
            _loop_risk = getattr(self._model_profile, "loop_risk", "low") if self._model_profile else "low"
            # P5 — react_stability : seuil de stagnation adapté (unstable déclenche plus tôt)
            _stagnation_limit = (
                2 if getattr(self._model_profile, "react_stability", "stable") == "unstable" else 3
            ) if self._model_profile else 3
            if _direct_coding_mode:
                _stagnation_limit = max(_stagnation_limit, 4)
            # P5 — action_inline_risk : nb inline avant injection rappel format
            _inline_risk = getattr(self._model_profile, "action_inline_risk", "low") if self._model_profile else "low"
            _inline_reminder_thresh = 1 if _inline_risk == "high" else (2 if _inline_risk == "medium" else 0)
            # stop=["OBSERVATION:"] empêche le modèle d'écrire de fausses observations
            # Seul le système produit OBSERVATION: après exécution réelle d'un outil
            _react_stop = ["OBSERVATION:"]
            logger.info(f"⏳ LLM en cours... (iter {i+1}, modèle: {_active_model or 'default'}, timeout: {_llm_call_timeout}s)")
            for _attempt in range(3):
                try:
                    if _attempt > 0:
                        logger.info(
                            f"LLM_RETRY: itération {i+1}, tentative {_attempt+1}/3, "
                            f"timeout={_llm_call_timeout}s — LLM lent ou contexte lourd, attente..."
                        )
                    response = await asyncio.wait_for(
                        self.llm_chat(messages, stop=_react_stop),
                        timeout=_llm_call_timeout,
                    )
                    _llm_last_exc = None
                    break  # succès
                except asyncio.TimeoutError:
                    _llm_last_exc = asyncio.TimeoutError(
                        f"LLM call exceeded {_llm_call_timeout}s (iter {i+1}, attempt {_attempt+1})"
                    )
                    logger.warning(
                        f"⏱️ LLM timeout {_llm_call_timeout}s dépassé "
                        f"(itération {i+1}, tentative {_attempt+1}/3) — contexte peut-être trop lourd"
                    )
                    logger.info(
                        f"LLM_RETRY: timeout {_llm_call_timeout}s (itér {i+1}, essai {_attempt+1}/3) — "
                        f"DeepSeek lent ou surchargé. Nouvel essai avec +30s..."
                    )
                    _llm_call_timeout = min(_llm_call_timeout + 30, 420)  # Budget augmenté au retry
                    if _attempt < 2:
                        await asyncio.sleep(1.0)
                except Exception as e:
                    _llm_last_exc = e
                    from src.llm.execution_router import CodexReActUnavailable
                    if isinstance(e, CodexReActUnavailable):
                        logger.error("Codex ReAct indisponible, aucun fallback API: {}", e)
                        break
                    if _attempt < 2:
                        logger.warning(f"⚠️ LLM tentative {_attempt + 1}/3 échouée ({e}), retry dans {1.5 * (_attempt + 1):.1f}s…")
                        await asyncio.sleep(1.5 * (_attempt + 1))
                    else:
                        logger.error(f"❌ LLM échoué après 3 tentatives : {e}")
            if _llm_last_exc is not None:
                if TELEMETRY_AVAILABLE:
                    publish_trace(
                        stage="llm_request_done",
                        status="error",
                        mode="agent",
                        duration_ms=(perf_counter() - llm_started) * 1000.0,
                        error=str(_llm_last_exc),
                    )
                    publish_trace(
                        stage="pipeline_error",
                        status="error",
                        mode="agent",
                        error=str(_llm_last_exc),
                    )
                _finish_iteration(status="error", error="llm_request_failed")
                # ── Fallback: au lieu de crash, tenter un prompt compacté ──
                if isinstance(_llm_last_exc, asyncio.TimeoutError) and i > 0 and len(self.history) > 0:
                    logger.warning("⚠️ Triple timeout — tentative fallback avec prompt compacté")
                    _compact_prompt = (
                        f"Requête originale: {original_query}\n\n"
                        f"Tu as déjà fait {len(self.history)} actions (list_directory, run_command, etc.) "
                        f"mais le LLM a timeout 3 fois car le contexte est trop lourd.\n"
                        f"AGIS MAINTENANT: utilise `create_project` ou `write_file` pour produire le résultat. "
                        f"Ne fais plus d'exploration. Résume ce que tu sais et agis."
                    )
                    query = _compact_prompt
                    self.history = self.history[-2:]  # Garder seulement les 2 dernières étapes
                    self._identity_ctx_cache = None  # Invalider le cache contexte
                    _finish_iteration(status="ok", summary="fallback_compact_after_triple_timeout")
                    continue
                raise _llm_last_exc
            # ── Check global deadline après l'appel LLM ──
            if hasattr(self, '_timeout_deadline') and perf_counter() > self._timeout_deadline:
                raise asyncio.TimeoutError()
            self._last_llm_meta = self._get_llm_meta()
            _provider_error = self._llm_provider_error_detail(
                response, self._last_llm_meta
            )
            if _provider_error:
                if TELEMETRY_AVAILABLE:
                    publish_trace(
                        stage="llm_request_done",
                        status="error",
                        mode="agent",
                        duration_ms=(perf_counter() - llm_started) * 1000.0,
                        provider=self._last_llm_meta.get("provider_used"),
                        model=self._last_llm_meta.get("model_used"),
                        error=_provider_error,
                    )
                _finish_iteration(status="error", error="llm_provider_error")
                raise RuntimeError(f"llm_provider_error: {_provider_error}")
            if TELEMETRY_AVAILABLE:
                publish_trace(
                    stage="llm_request_done",
                    status="ok",
                    mode="agent",
                    duration_ms=(perf_counter() - llm_started) * 1000.0,
                    provider=self._last_llm_meta.get("provider_used"),
                    model=self._last_llm_meta.get("model_used"),
                    summary=f"finish_reason={self._last_llm_meta.get('finish_reason')}" if self._last_llm_meta.get("finish_reason") else None,
                )

            # ── Sanitisation LLM output (corrige bugs courants des LLM) ──
            if response:
                response = _sanitize_llm_output(response)

            # FIX TRONCATURE: si réponse coupée (finish_reason == "length"),
            # sauvegarder le contenu partiel et orienter la suite sans tout recommencer.
            _trunc_fr = str(self._last_llm_meta.get("finish_reason") or "").strip().lower()
            if self._is_length_finish_reason(_trunc_fr) and response and len(response.strip()) > 100:
                logger.warning(
                    "✂️ Réponse tronquée détectée (finish_reason={}, {} chars) - sauvegarde du partiel",
                    _trunc_fr, len(response),
                )
                # Essayer d'extraire path + contenu partiel d'un éventuel write_file
                import re as _re_trunc
                import os as _os_trunc
                _saved_partial_path: Optional[str] = None
                _partial_content_for_ctx: str = ""
                _tool_match = _re_trunc.search(
                    r'ACTION:\s*tool_call.*?ACTION_INPUT:\s*(\{.*)',
                    response, _re_trunc.DOTALL | _re_trunc.IGNORECASE,
                )
                if _tool_match:
                    try:
                        import json as _json_trunc
                        _raw_json = _tool_match.group(1).strip()
                        # Fermer le JSON partiellement tronqué pour pouvoir le lire
                        # Compter les accolades pour estimer où ajouter }
                        _opens = _raw_json.count("{")
                        _closes = _raw_json.count("}")
                        _raw_completed = _raw_json + "}" * max(0, _opens - _closes)
                        try:
                            _args = _json_trunc.loads(_raw_completed)
                        except Exception:
                            # En cas d'échec JSON, extraire manuellement le path et content
                            _path_m = _re_trunc.search(r'"path"\s*:\s*"([^"]+)"', _raw_json)
                            _content_m = _re_trunc.search(r'"content"\s*:\s*"(.*)', _raw_json, _re_trunc.DOTALL)
                            _args = {}
                            if _path_m:
                                _args["path"] = _path_m.group(1)
                            if _content_m:
                                _args["content"] = _content_m.group(1).replace('\\"', '"').replace('\\n', '\n')
                        _wf_path = str(_args.get("path", "") or "").strip()
                        _wf_content = str(_args.get("content", "") or "")
                        if _wf_path and _wf_content and len(_wf_content) > 50:
                            # Résoudre le chemin par rapport au workspace
                            _base_ws = str(Path(__file__).parent.parent.parent)
                            _abs_path = _wf_path if _os_trunc.path.isabs(_wf_path) else _os_trunc.path.join(_base_ws, _wf_path)
                            _os_trunc.makedirs(_os_trunc.path.dirname(_abs_path), exist_ok=True)
                            with open(_abs_path, "w", encoding="utf-8") as _pf:
                                _pf.write(_wf_content)
                                _pf.write("\n\n# [TRONCATURE: suite à compléter]")
                            _saved_partial_path = _wf_path
                            _partial_content_for_ctx = _wf_content[-1500:]  # Garder la fin pour le contexte
                            logger.info("💾 Contenu partiel sauvegardé dans: {} ({} chars)", _wf_path, len(_wf_content))
                    except Exception as _trunc_ex:
                        logger.warning("⚠️ Impossible d'extraire le write_file tronqué: {}", _trunc_ex)
                        _partial_content_for_ctx = response[-2000:]
                else:
                    # Pas de write_file détecté, prendre la fin de la réponse comme contexte
                    _partial_content_for_ctx = response[-2000:]

                # Construire le prompt de continuation
                _trunc_ctx_parts = [
                    f"Requête originale: {original_query}",
                    "",
                    "⚠️ CONTINUATION REQUISE: Ta réponse précédente a été coupée (limite de tokens atteinte).",
                ]
                if _saved_partial_path:
                    _trunc_ctx_parts += [
                        f"✅ Le fichier `{_saved_partial_path}` a été partiellement sauvegardé avec ce qui avait déjà été généré.",
                        "Continue maintenant en écrivant la SUITE du fichier (uniquement ce qui manque), ou passe à l'étape suivante du plan.",
                    ]
                    # Nudge vers generate_website si c'est un fichier web tronqué
                    if any(_saved_partial_path.endswith(ext) for ext in ('.html', '.css', '.js')):
                        _trunc_ctx_parts += [
                            "",
                            "⚠️ IMPORTANT: Tu as essayé d'écrire un fichier web complet avec write_file "
                            "mais il a été TRONQUÉ par la limite de tokens. "
                            "Utilise plutôt l'outil `generate_website` qui est conçu pour créer des sites "
                            "multi-fichiers sans troncature. Appelle-le avec une description détaillée.",
                        ]
                else:
                    _trunc_ctx_parts += [
                        "Voici la FIN de ce que tu avais généré (ne répète pas, continue à partir de là):",
                        "",
                        f"```\n{_partial_content_for_ctx}\n```",
                        "",
                        "Continue maintenant là où tu t'es arrêté. Si c'est du code/fichier: écris la suite avec write_file. Si c'est fini: utilise FINAL.",
                    ]
                _trunc_ctx_parts += ["", "Ne recommence PAS depuis le début."]
                query = "\n".join(_trunc_ctx_parts)
                _finish_iteration(status="ok", summary="truncation_continuation_injected")
                continue

            # 2. Parser la réponse
            logger.info(f"📥 LLM RESPONSE SIZE: {len(response)} chars")
            
            # FIX: Gérer les réponses vides - comportement adapté au profil (P5)
            if not response or len(response.strip()) == 0:
                _empty_risk = getattr(self._model_profile, "empty_response_risk", "rare") if self._model_profile else "rare"
                _retry_on_empty = getattr(self._model_profile, "retry_on_empty", True) if self._model_profile else True
                if _empty_risk == "frequent":
                    logger.debug("⚠️ Réponse LLM vide (attendu pour ce modèle) — retry format")
                else:
                    logger.warning("⚠️ Réponse LLM vide détectée - retry avec rappel de format")
                if _retry_on_empty:
                    query = f"{query}\n\n⚠️ Ta dernière réponse était vide. RAPPEL: utilise le format THOUGHT/ACTION pour répondre."
                _finish_iteration(status="error", error="empty_llm_response")
                continue  # Skip to next iteration instead of parsing empty response
            
            thought, action = self._parse_response(response)
            logger.debug(f"Thought: {thought.content}")
            logger.debug(f"Action: {action.action_type.value}")

            # F4: après un FINAL jugé incomplet, le repair re-interroge le modèle.
            # S'il se rattrape en AGISSANT (tool_call), on NE rollback PLUS vers la
            # réponse incomplète : c'était auto-contradictoire (on répare PARCE QUE
            # c'est incomplet, puis on renverrait l'incomplet, en jetant la
            # récupération légitime du modèle). On laisse l'action s'exécuter pour
            # produire une vraie réponse. Bornes anti-emballement déjà en place :
            # max_iterations global + _final_repair_attempts (déjà consommé).
            _pre_repair = getattr(self, '_pre_repair_answer', None)
            if _pre_repair and action.action_type != ActionType.FINAL_ANSWER:
                logger.info(
                    "🔧 Repair FINAL → le modèle reprend par {} : on laisse la "
                    "récupération aboutir (au lieu de rollback vers l'incomplet, {} chars)",
                    action.action_type.value, len(_pre_repair),
                )
                self._pre_repair_answer = None
                # PAS de return : fall-through vers l'exécution normale de l'action.
            elif _pre_repair and action.action_type == ActionType.FINAL_ANSWER:
                # Un FINAL VIDE est, lui aussi, un FINAL_ANSWER. Le compter comme
                # « repair réussi » DÉTRUISAIT la réponse d'origine — celle qu'on
                # venait de sauvegarder neuf lignes plus haut POUR ce cas précis.
                # Run 2026-08-29 : un bilan de mission de 1312 chars jeté ici, puis
                # trois FINAL vides d'affilée, et l'utilisateur a reçu la formule
                # de 41 caractères. Le marqueur ne se nettoie que si le repair a
                # réellement produit du contenu.
                _repare = (getattr(action, "answer", "") or "").strip()
                if _repare:
                    self._pre_repair_answer = None
                else:
                    logger.warning(
                        "🔧 Repair FINAL → FINAL VIDE : on CONSERVE la réponse "
                        "d'origine ({} chars) au lieu de la détruire.",
                        len(_pre_repair),
                    )

            # 2.0a Tracking hallucinations consécutives (Kimi simule des OBSERVATION)
            _halluc_warning = ""
            if getattr(self, '_last_thought_was_hallucinated', False):
                _halluc_streak = getattr(self, '_halluc_streak', 0) + 1
                self._halluc_streak = _halluc_streak
                if _halluc_streak >= 1:
                    _halluc_warning = (
                        "\n\n⚠️ RAPPEL CRITIQUE: Tu as simulé des résultats d'outils "
                        f"{_halluc_streak} fois. Le contenu halluciné est SUPPRIMÉ. "
                        "Écris SEULEMENT ton THOUGHT, puis ACTION et ACTION_INPUT. "
                        "ATTENDS l'OBSERVATION du système. N'écris JAMAIS "
                        "'OBSERVATION:' toi-même."
                    )
                    logger.warning("⚠️ Hallucination streak: {} — warning injecté", _halluc_streak)
                # Streak ≥ 2 : compaction d'urgence — le contexte accumulé est probablement
                # la cause principale des hallucinations. Garder seulement les 3 dernières étapes.
                if _halluc_streak >= 2 and len(self.history) > 3:
                    _kept = self.history[-3:]
                    _dropped = len(self.history) - 3
                    # LOT Z22 — le bruit meurt, les faits restent : on remet en
                    # tête le journal d'exécution (jamais tronqué, lui).
                    _facts = self._ledger_facts_step()
                    self.history = ([_facts] + _kept) if _facts else _kept
                    logger.warning(
                        "🚨 Hallucination streak {} — compaction d'urgence: {} étapes supprimées, "
                        "historique réduit à 3 pour nettoyer le contexte{}.",
                        _halluc_streak, _dropped,
                        " (faits du ledger réinjectés)" if _facts else "",
                    )
            else:
                self._halluc_streak = 0

            # 2.0 Plan TODO : parsing a l'iteration 0 uniquement
            if i == 0 and not self._plan_emitted:
                parsed_plan = self._parse_plan(response)
                if parsed_plan:
                    self._task_plan = parsed_plan
                    self._plan_emitted = True
                    logger.info(f"[PLAN] Plan detecte avec {len(parsed_plan)} taches")
                    for idx_p, t in enumerate(parsed_plan):
                        logger.info(f"  [{idx_p+1}] {t.description}")
                    # Émettre l'état initial pour le frontend
                    self._emit_plan_state(context_tool="")

            # 2.1 Détection de stagnation de pensée (thoughts quasi-identiques)
            _stagnation_warning = ""
            if thought.content:
                # Lot RF-9b : la DECISION est deplacee vers
                # `observation_synthesis.py` (feuille « detection de stagnation
                # de pensee », §15). Restent ici les MUTATIONS de l'historique.
                _is_stagnant = thought_is_stagnant(
                    thought.content, _previous_thoughts, original_query, _loop_risk,
                )
                _previous_thoughts.append(thought.content)
                if len(_previous_thoughts) > 5:
                    _previous_thoughts = _previous_thoughts[-5:]
                if _is_stagnant:
                    _stagnation_streak += 1
                    logger.warning("⚠️ Stagnation pensée détectée (3 thoughts quasi-identiques) — streak={}", _stagnation_streak)
                    # P4: Injecter les outils pertinents dans le warning de stagnation
                    _stag_tool_hint = ""
                    if hasattr(self.tools, "_tool_modules"):
                        # Lot RF-9c : la DECISION est deplacee vers
                        # `observation_synthesis.py`. Seule la concatenation
                        # reste ici.
                        _stag_tool_hint = stagnation_tool_hint(
                            original_query, self.tools.tools,
                        ) or _stag_tool_hint
                    _stagnation_warning = (
                        "\n\n⚠️ STAGNATION: Tu répètes le même raisonnement. "
                        "Après cette action, AGIS ou donne ta réponse FINAL."
                        + _stag_tool_hint
                    )
                    # Après N stagnations consécutives : forcer la complétion du plan
                    # (N=2 pour react_stability=unstable, N=3 sinon)
                    if _stagnation_streak >= _stagnation_limit and self._task_plan:
                        logger.warning("⚠️ Stagnation critique ({}) — bypass PLAN GUARD pour débloquer FINAL", _stagnation_streak)
                        # NE PAS mentir sur l'état des tâches — juste bypasser le guard.
                        # LOT Z6 : poser 3 ne débloquait RIEN quand une tâche
                        # opérationnelle restait — le `or` sans borne du PLAN GUARD
                        # neutralisait ce filet, qui était donc posé puis ignoré. Il
                        # faut franchir le PLUS HAUT des deux plafonds pour que
                        # « bypass » veuille dire bypass.
                        self._plan_guard_retries = max(
                            _PLAN_GUARD_MAX_RETRIES, _PLAN_GUARD_MAX_RETRIES_OPERATIONAL
                        )
                    # P3 HARD: Après N stagnations consécutives ET actions identiques → FORCER FINAL synthétique
                    # Une progression légitime (lectures séquentielles avec args différents) est tolérée.
                    _actions_are_redundant = False
                    if _stagnation_streak >= _stagnation_limit and len(self.history) >= 3:
                        _recent_actions = self.history[-3:]
                        _sig = (action.tool_name, str(action.tool_args))
                        _recent_sigs = [(h.action.tool_name, str(h.action.tool_args)) for h in _recent_actions]
                        # Si les 3 dernières actions + l'actuelle sont toutes identiques → vrai blocage
                        _actions_are_redundant = all(s == _sig for s in _recent_sigs)
                    if _stagnation_streak >= _stagnation_limit and _actions_are_redundant:
                        logger.error(
                            "🛑 Stagnation HARD ({}× consécutives, action identique) — FORCE FINAL synthétique",
                            _stagnation_streak,
                        )
                        _forced_answer = (
                            "Je stagne depuis 3 tours consécutifs sur le même raisonnement "
                            "ET la même action, sans progresser. Je m'arrête pour éviter une boucle inutile.\n\n"
                            "Résumé de ce que j'ai exploré :\n"
                            f"- Dernière pensée : {thought.content[:200]}\n"
                            f"- Action tentée : {action.action_type.value}"
                            + (f" ({action.tool_name})" if action.tool_name else "")
                            + "\n\n"
                            "👉 Peux-tu reformuler ta demande ou me donner une instruction "
                            "plus précise ? Si tu veux que j'agisse, dis-le explicitement "
                            "(ex: \"modifie X\", \"écris Y\", \"lance Z\")."
                        )
                        action = Action(
                            action_type=ActionType.FINAL_ANSWER,
                            answer=_forced_answer,
                        )
                        thought = Thought(content="Stagnation critique détectée — arrêt forcé.")
                        _stagnation_streak = 0  # Reset pour ne pas rebloquer le prochain tour
                else:
                    _stagnation_streak = 0  # Reset si la pensée change

            if TELEMETRY_AVAILABLE and action.action_type == ActionType.TOOL_CALL:
                publish_trace(
                    stage="tool_parse",
                    status="ok",
                    mode="agent",
                    tool_name=action.tool_name,
                    summary=str(action.tool_args),
                )
            
            # 2.5 Détecter les actions répétées (mais pas pour lecture de fichiers différents)
            if action.action_type == ActionType.TOOL_CALL:
                if self._is_exploratory_tool(action.tool_name or ""):
                    _exploratory_since_productive += 1
                    _productive_tools = {"write_file", "create_project", "create_file", "delegate_task", "execute_code", "dev_run_fix"}
                    # Détecter si un projet a déjà été créé/livré (éviter recréation)
                    has_prior_project = any(
                        h.action.tool_name == "create_project" and h.observation and h.observation.success
                        for h in self.history
                    )
                    has_run_error = any(
                        h.action.tool_name == "run_command" and h.observation and not h.observation.success
                        for h in self.history
                    )
                    _threshold = 3 if single_file_creation_intent else 6
                    if _exploratory_since_productive >= _threshold:
                        logger.warning(
                            "⚠️ Trop d'actions exploratoires sans production: forçage action productive"
                        )
                        if has_prior_project or has_run_error:
                            # Projet déjà créé → fixer, pas recréer
                            query = (
                                f"Requête originale: {original_query}\n\n"
                                "⚠️ STOP exploration. Un projet existe DÉJÀ dans ton historique.\n"
                                "⛔ Ne recrée PAS un nouveau projet. CORRIGE l'existant :\n"
                                "- Si une commande a échoué → `dev_run_fix(command='...', project_dir='...')` pour diagnostiquer et corriger automatiquement.\n"
                                "- Si un fichier a un bug → `edit_file` pour le corriger.\n"
                                "- Si tout est OK → donne ta réponse avec ACTION: FINAL."
                            )
                        else:
                            query = (
                                f"Requête originale: {original_query}\n\n"
                                "⚠️ STOP exploration. Tu as assez de contexte après "
                                f"{_exploratory_since_productive} actions exploratoires sans rien produire.\n"
                                "La prochaine action DOIT être productive : `create_project`, `write_file`, "
                                "`delegate_task(agent_type='code')`, ou `execute_code`.\n"
                                "Si le code est complexe (>50 lignes), utilise `create_project` ou `delegate_task`.\n"
                                "Ensuite termine avec ACTION: FINAL."
                            )
                        _exploratory_since_productive = 0
                        _finish_iteration(status="ok", summary="forced_productive_after_exploration")
                        continue
                else:
                    # Action productive (write, create, dev_run_fix, etc.) → reset compteur
                    if action.tool_name in {"write_file", "create_project", "create_file", "delegate_task", "execute_code", "dev_run_fix", "edit_file", "edit_own_code"}:
                        _exploratory_since_productive = 0

                if action.tool_name == "read_file":
                    target_path = str(action.tool_args.get("path", "") or "").strip()
                    start_line_raw = action.tool_args.get("start_line")
                    end_line_raw = action.tool_args.get("end_line")
                    try:
                        start_line = max(1, int(start_line_raw)) if start_line_raw is not None else 1
                    except Exception:
                        start_line = 1
                    try:
                        end_line = int(end_line_raw) if end_line_raw is not None else None
                    except Exception:
                        end_line = None
                    if end_line is not None and end_line < start_line:
                        end_line = start_line

                    current_signature = (target_path, start_line, end_line)
                    if current_signature == last_read_signature:
                        repeated_read_count += 1
                    else:
                        repeated_read_count = 0
                    last_read_signature = current_signature

                    if repeated_read_count >= 2:
                        page_size = 350
                        next_start = (end_line + 1) if end_line is not None else (start_line + page_size)
                        next_end = next_start + page_size - 1
                        logger.warning(
                            "⚠️ read_file répété sans progression sur {} - pagination forcée {}-{}",
                            target_path,
                            next_start,
                            next_end,
                        )
                        action.tool_args["start_line"] = next_start
                        action.tool_args["end_line"] = next_end
                        repeated_read_count = 0

                    # Guard par path : distinguer nouvelles plages vs relectures
                    # path vide = appel sans argument → échoue au registry, on ne tracke pas
                    if target_path:
                        _range_key = (start_line, end_line)
                        _read_file_path_counter[target_path] = _read_file_path_counter.get(target_path, 0) + 1
                        _rf_count = _read_file_path_counter[target_path]
                        if target_path not in _read_file_ranges_seen:
                            _read_file_ranges_seen[target_path] = set()
                        _is_reread = _range_key in _read_file_ranges_seen[target_path]
                        _read_file_ranges_seen[target_path].add(_range_key)
                        if _is_reread:
                            _read_file_reread_counter[target_path] = _read_file_reread_counter.get(target_path, 0) + 1
                        _reread_count = _read_file_reread_counter.get(target_path, 0)
                        # Seuils adaptatifs : fichiers longs tolèrent plus de lectures distinctes
                        _max_total = max(8, len(_read_file_ranges_seen[target_path]) + 4)  # au moins 8
                        if _rf_count >= 4:
                            logger.warning(
                                "⚠️ read_file sur '{}' appelé {}x ({}x nouvelles plages, {}x relectures)",
                                target_path,
                                _rf_count,
                                len(_read_file_ranges_seen[target_path]),
                                _reread_count,
                            )
                        # Forcer FINAL si trop de relectures OU trop de lectures totales
                        if _reread_count >= 3 or _rf_count >= _max_total:
                            _reason = (
                                f"relectures={_reread_count}" if _reread_count >= 3
                                else f"total={_rf_count}/{_max_total}"
                            )
                            # LOT P3 — en MISSION, tant qu'il reste du budget, on
                            # redirige au lieu d'achever : forcer le FINAL ici, ce
                            # n'est pas rendre une réponse, c'est tuer la mission.
                            _p3_remaining = 0.0
                            try:
                                _p3_remaining = max(
                                    0.0,
                                    float(self.timeout_seconds or 600)
                                    - (asyncio.get_running_loop().time() - self._loop_start_time),
                                )
                            except Exception:
                                _p3_remaining = 0.0
                            _p3_shots = getattr(self, "_read_stagnation_shots", 0)
                            if read_stagnation_action(
                                is_mission_run=bool(self._is_mission_run),
                                budget_remaining_s=_p3_remaining,
                                shots_used=_p3_shots,
                            ) == "redirect":
                                self._read_stagnation_shots = _p3_shots + 1
                                logger.warning(
                                    "[P3] read_file stagnation sur '{}' ({}) — mission avec "
                                    "{:.0f}s de budget → REDIRECTION 1/1 (pas de FINAL forcé).",
                                    target_path, _reason, _p3_remaining,
                                )
                                _read_file_reread_counter.pop(target_path, None)
                                _read_file_path_counter.pop(target_path, None)
                                observation = Observation(
                                    content=(
                                        f"⛔ Tu relis `{target_path}` en boucle ({_reason}) — tu as "
                                        "déjà son contenu en contexte, le relire ne t'apprendra rien "
                                        f"de neuf.\n\nIl te reste {_p3_remaining/60:.0f} minutes de "
                                        "mission : AGIS. Écris la correction (`edit_file`/"
                                        "`str_replace`), lance les tests, et conclus sur ce que "
                                        "l'exécution montre.\n\n(Redirection unique : au prochain "
                                        "appel, la lecture s'exécutera.)"
                                    ),
                                    success=False,
                                    origin="read_stagnation_redirect",
                                )
                                self.history.append(ReActStep(
                                    thought=thought, action=action, observation=observation,
                                ))
                                _finish_iteration(status="ok", summary="read_stagnation_redirect")
                                continue
                            logger.warning(
                                "⚠️ read_file stagnation sur '{}' — forçage FINAL ({})",
                                target_path,
                                _reason,
                            )
                            _finish_iteration(status="ok", summary=f"forced_final_read_stagnation_{_reason}")
                            summary_parts = []
                            for h in self.history[-5:]:
                                if h.observation and h.observation.content:
                                    summary_parts.append(h.observation.content[:300])
                            message = (
                                f"J'ai analysé le fichier '{target_path}' en détail. "
                                "Voici ce que j'ai trouvé :\n\n"
                                + "\n".join(summary_parts[-2:])
                            )
                            self._mark_task_done(message)
                            return message

                # Outils exemptés de détection de répétition (normaux d'être appelés plusieurs fois)
                exempt_tools = [
                    "read_own_code",
                    "web_search", "memory_search", "grep_search", "search_in_code",
                    "view_file_outline", "get_time", "memory_add",
                    # Inspection web: peut être appelée plusieurs fois sur des pages différentes
                    "browser_get_content",
                    # list_directory a son propre guard dédié (redirect vers find_files)
                    "list_directory",
                ]
                # NOTE: read_file retiré de exempt_tools — le guard bloque 3x même fichier+mêmes args
                # NOTE: write_file retiré de exempt_tools - on veut détecter les écritures répétées
                
                # Pour http_request, la clé significative est (tool, url, method) — ignorer headers/body
                # qui varient entre tentatives et trompent la détection de boucle
                if action.tool_name == "http_request":
                    _loop_url = str(action.tool_args.get("url", ""))
                    _loop_method = str(action.tool_args.get("method", "GET")).upper()
                    action_key = (action.tool_name, _loop_url, _loop_method)
                else:
                    action_key = (action.tool_name, str(action.tool_args))

                if action.tool_name == "list_directory":
                    target_path = str(action.tool_args.get("path", ".") or ".").strip()
                    target_path_lower = target_path.lower()
                    repeated_same_path = 0
                    for _prev_entry in self.action_history[-12:]:
                        previous_name = _prev_entry[0] if isinstance(_prev_entry, tuple) else _prev_entry
                        previous_args = _prev_entry[1] if isinstance(_prev_entry, tuple) and len(_prev_entry) > 1 else ""
                        if previous_name != "list_directory":
                            continue
                        previous_args_str = str(previous_args).lower()
                        if f"'path': '{target_path_lower}'" in previous_args_str or f'"path": "{target_path_lower}"' in previous_args_str:
                            repeated_same_path += 1

                    if repeated_same_path >= 2 and "find_files" in self.tools.tools:
                        filename_match = re.search(r"([A-Za-z0-9 _().-]+\.[A-Za-z0-9]{1,8})", original_query)
                        pattern_hint = filename_match.group(1).strip() if filename_match else "*.txt"
                        logger.warning(
                            "⚠️ list_directory répété sur '{}' - bascule vers find_files(pattern={})",
                            target_path,
                            pattern_hint,
                        )
                        action = Action(
                            action_type=ActionType.TOOL_CALL,
                            tool_name="find_files",
                            tool_args={"pattern": pattern_hint, "path": "workspace"},
                        )
                        action_key = (action.tool_name, str(action.tool_args))
                
                # FIX: Détection spécifique des écritures répétées au même fichier.
                # A4 (run FitLog) : l'ancien garde comptait les TENTATIVES (échecs
                # inclus), matchait TOUT l'historique quand path était vide ('' est
                # substring de tout), et fabriquait un FAUX succès (« ✅ créé avec
                # succès après 7 tentatives ») SANS passer par le chokepoint — le
                # lead FitLog est mort comme ça, en pleine intégration, à 08:12:27.
                if action.tool_name == "write_file":
                    target_path = action.tool_args.get("path", "") or action.tool_args.get("file_path", "")
                    if target_path:
                        write_count = sum(1 for k in self.action_history if k[0] == "write_file" and target_path in str(k[1]))
                        _wf_base = target_path.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
                        _wf_written_ok = False
                        try:
                            _wf_written_ok = _wf_base in self.execution_ledger.written_basenames()
                        except Exception:
                            pass
                        if write_count >= 3 and _wf_written_ok:
                            # Le fichier a RÉELLEMENT été écrit au moins une fois →
                            # arrêt honnête de la boucle, via le chokepoint (truth-lock).
                            logger.warning(
                                "⚠️ Fichier {} : {} écritures répétées (déjà écrit au ledger) — arrêt honnête",
                                target_path, write_count)
                            _finish_iteration(status="ok", summary="stop_on_repeated_write_file")
                            message = (
                                f"⚠️ J'arrête la boucle d'écritures répétées de `{target_path}` "
                                f"({write_count} tentatives). Le fichier a été écrit dans ce run ; "
                                "son contenu est celui actuellement sur disque (non re-validé)."
                            )
                            return self._stream_and_return_final(message)
                        if write_count >= 6:
                            # Jamais écrit avec succès malgré 6 tentatives → échec
                            # HONNÊTE (l'ancien message « créé avec succès » mentait).
                            logger.warning(
                                "⚠️ Fichier {} : {} tentatives d'écriture SANS succès au ledger — échec honnête",
                                target_path, write_count)
                            _finish_iteration(status="error", error="repeated_write_failures")
                            message = (
                                f"❌ Impossible d'écrire `{target_path}` après {write_count} tentatives "
                                "(réponses probablement tronquées). Le livrable est INCOMPLET : "
                                "ce fichier manque."
                            )
                            self._mark_task_failed("repeated_write_failures")
                            return message

                # FIX: Si mail_send a déjà réussi, ne pas boucler sur la vérification IMAP
                # (mail_list_messages avec les dossiers IMAP encodés échoue souvent et crée une spirale)
                _mail_verification_tools = {"mail_list_messages", "mail_list_folders"}
                if action.tool_name in _mail_verification_tools:
                    _successful_mail_sends = [
                        h for h in self.history
                        if h.action.tool_name in {"mail_send", "mail_reply_message", "send_email"}
                        and h.observation and h.observation.success
                    ]
                    if _successful_mail_sends:
                        _last_send = _successful_mail_sends[-1]
                        _to = _last_send.action.tool_args.get("to", "le destinataire")
                        _subject = _last_send.action.tool_args.get("subject", "")
                        logger.info(
                            "✅ mail_send déjà confirmé - skip vérification IMAP et FINAL direct"
                        )
                        _finish_iteration(status="ok", summary="mail_already_sent_skip_imap_check")
                        _subject_str = f' (sujet : "{_subject}")' if _subject else ""
                        message = (
                            f"✅ Email envoyé avec succès à **{_to}**{_subject_str}.\n\n"
                            "L'envoi a été confirmé par le serveur SMTP. "
                            "Tu devrais le recevoir dans quelques instants."
                        )
                        self._mark_task_done(message)
                        return message

                # FIX: mail_send répété vers le même destinataire = boucle, forcer FINAL
                if action.tool_name in {"mail_send", "mail_reply_message", "send_email"}:
                    _current_to = str(action.tool_args.get("to", "")).strip().lower()
                    _same_send_count = sum(
                        1 for h in self.history
                        if h.action.tool_name in {"mail_send", "mail_reply_message", "send_email"}
                        and h.observation and h.observation.success
                        and str(h.action.tool_args.get("to", "")).strip().lower() == _current_to
                    )
                    if _same_send_count >= 1:
                        logger.warning(
                            "⚠️ mail_send vers '{}' déjà réussi - éviter doublon et forcer FINAL",
                            _current_to,
                        )
                        _finish_iteration(status="ok", summary="mail_already_sent_no_duplicate")
                        message = (
                            f"✅ Email déjà envoyé avec succès à **{_current_to}**.\n\n"
                            "L'envoi précédent a été confirmé par le serveur SMTP, je n'envoie pas de doublon."
                        )
                        self._mark_task_done(message)
                        return message
                
                # --- Détection d'échecs CONSÉCUTIFS sur le MÊME outil ---
                # FIX: ignorer les outils read-only qui ont retourné du contenu
                # non-vide (ex: read_file de 10KB mal flaggé par le détecteur
                # par mots-clés). Un "échec" avec 500+ chars de contenu est un
                # faux positif, pas une vraie erreur.
                # 2.8.1 (run MotCompteur) — write_mission_contract est RÉCUPÉRABLE
                # par simple reformulation (JSON/no_public_api) : l'escalade « 3×
                # échoué → CodeAgent » n'a aucun sens pour lui (le CodeAgent ne sait
                # pas poser un contrat) et a TUÉ la mission avant que le lead trouve
                # le bon format. On ne compte JAMAIS ses échecs ici.
                _ESCALATION_EXEMPT = {"write_mission_contract"}
                _recent_fails = (
                    0
                    if action.tool_name in _ESCALATION_EXEMPT
                    else _recent_tool_failure_streak(
                        action.tool_name, self.history,
                    )
                )
                if _recent_fails >= 3:
                    if action.tool_name in ("edit_file", "write_file", "apply_patch"):
                        logger.warning(
                            "⚠️ Outil {} a échoué {}x récemment — escalade CodeAgent",
                            action.tool_name, _recent_fails,
                        )
                    else:
                        logger.warning(
                            "⚠️ Outil {} a répété le même échec {}x — arrêt borné",
                            action.tool_name, _recent_fails,
                        )

                    # ── Escalade automatique vers CodeAgent ──
                    _lum = getattr(self.tools, "lumena", None)
                    if _lum is not None and action.tool_name in ("edit_file", "write_file", "apply_patch"):
                        try:
                            from ..agents.sub_agent import delegate_to_agent
                            _root = getattr(_lum, "runtime_root", None)
                            _ctx: Dict[str, Any] = {}
                            # ── Déduire le workspace projet depuis l'historique ou la query ──
                            _esc_project_path = None
                            # 4.1: Priorité 0 — established_facts (zéro lock, déjà résolu)
                            try:
                                _ss_esc = self._structured_state
                                if _ss_esc is not None:
                                    _ef_esc_path = _ss_esc.established_facts.get("active_project_path", "")
                                    if _ef_esc_path and os.path.isdir(_ef_esc_path):
                                        _esc_project_path = _ef_esc_path
                                        logger.info("[ReAct] Escalade: project_path depuis established_facts: {}", _ef_esc_path[:80])
                            except Exception:
                                pass
                            # Priorité 1 — IdentityService si le fait n'est pas posé
                            if not _esc_project_path:
                                try:
                                    _id_svc_esc = getattr(_lum, "_identity_svc", None)
                                    if _id_svc_esc is not None and self.runtime_ctx is not None:
                                        from ..core_services.identity_service import IdentityService as _IDS_E
                                        _ck_esc = _IDS_E.resolve_channel_key(self.runtime_ctx)
                                        _rpc_esc = _id_svc_esc.get_recent_code_context(_ck_esc) if _ck_esc else None
                                        if _rpc_esc:
                                            _rpc_path_esc = _rpc_esc.get("workspace_path", "")
                                            if _rpc_path_esc and os.path.isdir(_rpc_path_esc):
                                                _esc_project_path = _rpc_path_esc
                                                logger.info("[ReAct] Escalade: project_path depuis contexte récent: {}", _rpc_path_esc[:80])
                                except Exception:
                                    pass
                            # 1. Chemin explicite dans la query
                            if not _esc_project_path:
                                # Lot RF-9d : la DECISION sort ; l'affectation
                                # reste ici.
                                _esc_project_path = workspace_path_from_query(
                                    query, _root,
                                ) or _esc_project_path
                            # 2. Extraire depuis les file_path des actions récentes
                            if not _esc_project_path:
                                for _h in reversed(self.history[-10:]):
                                    if _h.action and _h.action.args:
                                        for _v in _h.action.args.values():
                                            if isinstance(_v, str) and "workspace" in _v.replace("\\", "/").lower():
                                                _m = re.search(r'(.+?[\\/]workspace[\\/][\w\-]+)', _v)
                                                if _m and os.path.isdir(_m.group(1)):
                                                    _esc_project_path = _m.group(1)
                                                    break
                                        if _esc_project_path:
                                            break
                            _ctx["workspace_path"] = _esc_project_path or (str(_root) if _root else "")
                            if _esc_project_path:
                                _ctx["project_dir"] = _esc_project_path
                            logger.info("[ReAct] Escalade → CodeAgent après {}x échecs {} (workspace={})", _recent_fails, action.tool_name, _ctx.get("workspace_path", "?")[:80])
                            _ca_result = await delegate_to_agent(query, agent_type="code", context=_ctx)
                            if _ca_result:
                                logger.info("[ReAct] CodeAgent (escalade) terminé: {} chars", len(_ca_result))
                                _finish_iteration(status="ok", summary=f"escalated_to_codeagent_after_{action.tool_name}")
                                return _ca_result
                        except Exception as _ca_exc:
                            logger.warning("[ReAct] CodeAgent escalade échouée: {}", _ca_exc)

                    # ── Fallback: forçage FINAL si CodeAgent indisponible ──
                    self._run_meta["agent_output_warning"] = "tool_repeated_failure"
                    _finish_iteration(status="ok", summary=f"stop_repeated_failure_{action.tool_name}")
                    message = _repeated_tool_failure_message(
                        action.tool_name, self.history,
                    )
                    self._mark_task_failed(f"repeated_failure_{action.tool_name}")
                    return message

                # ── Détecteur de stagnation post-édition (contextuel) ─────────
                # Distingue les lectures progressives (nouveau fichier/zone/cible)
                # des lectures vraiment redondantes (même fichier+zone+intention N fois).
                # ── Guard pré-édition : boucle de lecture en phase d'exploration ──
                # Détecte les relectures redondantes avant tout edit (ex: script.js lu 6x
                # pendant une investigation, compaction → perte contexte → re-lecture).
                if not _has_done_edits and action.tool_name in _read_only_tools:
                    _pre_edit_guidance_at = 5 if _direct_coding_mode else 3
                    _pre_edit_guidance_hard_at = 8 if _direct_coding_mode else 5
                    _curr_pre_sig = _compute_read_sig(action.tool_name, action.tool_args)
                    _pre_progressive = (
                        _pre_edit_last_sig is None
                        or _curr_pre_sig[0] != _pre_edit_last_sig[0]
                        or _curr_pre_sig[2] != _pre_edit_last_sig[2]
                        or (
                            _curr_pre_sig[1] is not None
                            and _pre_edit_last_sig[1] is not None
                            and _curr_pre_sig[1] != _pre_edit_last_sig[1]
                        )
                    )
                    if _pre_progressive:
                        _pre_edit_redundant_streak = 0
                    else:
                        _pre_edit_redundant_streak += 1
                    _pre_edit_last_sig = _curr_pre_sig

                    if _pre_edit_redundant_streak == _pre_edit_guidance_at:
                        logger.warning(
                            "⚠️ Boucle exploration: {} lectures redondantes sur même cible (pré-édition) — guidance injectée",
                            _pre_edit_redundant_streak,
                        )
                        self._pending_loop_guidance = (
                            "⚠️ Tu relis le même fichier/zone plusieurs fois sans avancer. "
                            "Si le fichier est trop long, utilise `grep_search` pour cibler "
                            "directement le symbole ou la ligne cherchée. "
                            "Si la tâche est complexe, utilise `delegate_task` pour confier "
                            "l'exploration à un agent spécialisé."
                        )
                    elif _pre_edit_redundant_streak >= _pre_edit_guidance_hard_at:
                        # Pas de FINAL forcé : rien n'a encore été édité, forcer FINAL
                        # abandonnerait la tâche. On injecte une guidance maximale qui
                        # sera la première chose que voit le modèle à l'itération suivante.
                        logger.warning(
                            "⚠️ Boucle exploration renforcée: {} lectures redondantes (pré-édition) — guidance obligatoire",
                            _pre_edit_redundant_streak,
                        )
                        _finish_iteration(status="ok", summary=f"pre_edit_loop_guidance_reinforced_{_pre_edit_redundant_streak}")
                        self._pending_loop_guidance = (
                            "⚠️ STOP — tu relis la même cible depuis "
                            f"{_pre_edit_redundant_streak} itérations sans progresser. "
                            "Utilise OBLIGATOIREMENT `grep_search` avec le pattern exact "
                            "ou `delegate_task` pour sortir de cette boucle. "
                            "Ne relis pas le même fichier."
                        )

                if action.tool_name in _write_tools:
                    _has_done_edits = True
                    _post_edit_read_streak = 0
                    _redundant_read_streak = 0
                    _last_read_sig = None
                    _pre_edit_redundant_streak = 0
                    _pre_edit_last_sig = None
                elif _has_done_edits and action.tool_name in _read_only_tools:
                    _post_edit_guidance_total = 6 if _direct_coding_mode else 4
                    _post_edit_guidance_redundant = 3 if _direct_coding_mode else 2
                    _post_edit_force_redundant = 6 if _direct_coding_mode else 4
                    _post_edit_force_total = 14 if _direct_coding_mode else 10
                    _post_edit_read_streak += 1
                    _curr_sig = _compute_read_sig(action.tool_name, action.tool_args)
                    _is_progressive = (
                        _last_read_sig is None
                        or _curr_sig[0] != _last_read_sig[0]          # fichier différent
                        or _curr_sig[2] != _last_read_sig[2]          # intention/pattern différent
                        or (
                            _curr_sig[1] is not None
                            and _last_read_sig[1] is not None
                            and _curr_sig[1] != _last_read_sig[1]     # zone différente
                        )
                    )
                    if _is_progressive:
                        _redundant_read_streak = 0
                    else:
                        _redundant_read_streak += 1
                    _last_read_sig = _curr_sig

                    # Guidance seulement si les lectures deviennent redondantes (≥2 redondantes)
                    if _post_edit_read_streak >= _post_edit_guidance_total and _redundant_read_streak >= _post_edit_guidance_redundant:
                        if _redundant_read_streak == _post_edit_guidance_redundant:
                            logger.warning(
                                "⚠️ Stagnation post-édition: {} lectures redondantes (même fichier/zone) — guidance injectée",
                                _redundant_read_streak,
                            )
                            self._pending_loop_guidance = (
                                "⚠️ STOP — tu as fait des modifications et tu relis le même fichier/zone "
                                f"depuis {_redundant_read_streak} itérations sans rien changer. "
                                "Options : 1) Utilise `check_web_project` pour valider, "
                                "2) Corrige un problème trouvé avec write_file/edit_file, "
                                "3) Conclus avec FINAL_ANSWER si les corrections sont terminées."
                            )
                        elif _redundant_read_streak >= _post_edit_force_redundant or _post_edit_read_streak >= _post_edit_force_total:
                            # Forçage uniquement sur vraie boucle redondante (4+ identiques)
                            # ou si streak total dépasse 10 (garder un filet de sécurité)
                            logger.warning(
                                "⚠️ Stagnation post-édition forcée FINAL: {} lectures redondantes / {} total read-only",
                                _redundant_read_streak, _post_edit_read_streak,
                            )
                            _finish_iteration(status="ok", summary=f"forced_final_post_edit_stagnation_redundant_{_redundant_read_streak}")
                            _recent_edits = [
                                f"- {h.action.tool_name}({list(h.action.tool_args.keys())[:2]})"
                                for h in self.history[-15:]
                                if h.action.tool_name in _write_tools
                            ]
                            message = (
                                "✅ Modifications appliquées :\n"
                                + "\n".join(_recent_edits[-5:])
                                + "\n\nLes corrections ont été vérifiées. Tâche terminée."
                            )
                            self._mark_task_done(message)
                            return message
                else:
                    # Outil ni write ni read-only → reset streak
                    if action.tool_name not in ("parallel_tools",):
                        _post_edit_read_streak = 0
                        _redundant_read_streak = 0
                        _last_read_sig = None

                # Ne pas compter comme répétition pour les outils exemptés
                if action.tool_name not in exempt_tools:
                    # Phase 2.2: Détection de boucle améliorée (même action 3x = forcer FINAL)
                    if action.tool_name == "http_request":
                        _sig_url = str(action.tool_args.get("url", ""))
                        _sig_method = str(action.tool_args.get("method", "GET")).upper()
                        current_action_sig = (action.tool_name, _sig_url, _sig_method)
                    else:
                        current_action_sig = (action.tool_name, str(action.tool_args))
                    if current_action_sig == self._last_action_signature:
                        self._consecutive_same_action += 1
                    else:
                        self._consecutive_same_action = 1
                        self._last_action_signature = current_action_sig

                    # Détection précoce : 2x consécutif → rappel informatif (pas bloquant)
                    # Le LLM peut avoir une raison légitime de relancer (polling, comparaison, etc.)
                    if self._consecutive_same_action == 2:
                        logger.info("ℹ️ Commande identique 2x consécutive: {} — rappel injecté", action.tool_name)
                        self._pending_loop_guidance = (
                            f"ℹ️ NOTE: Tu viens d'exécuter `{action.tool_name}` avec les mêmes arguments "
                            f"que l'itération précédente. Si tu as déjà le résultat dont tu as besoin, "
                            f"passe à l'étape suivante plutôt que de relancer. "
                            f"Si tu relances volontairement (comparaison, polling), c'est OK."
                        )

                    # Détection de boucle lente : même (outil+args) 3+ fois dans la fenêtre des 10 dernières actions
                    _window_count = self.action_history[-10:].count(current_action_sig)
                    if _window_count >= 3 and self._consecutive_same_action < 3:
                        logger.warning(
                            "⚠️ Boucle lente: {} appelé {}x dans la fenêtre — guidance injectée",
                            action.tool_name, _window_count + 1,
                        )
                        self._pending_loop_guidance = (
                            f"⚠️ GUIDANCE ANTI-BOUCLE: Tu viens d'appeler `{action.tool_name}` avec les mêmes arguments "
                            f"pour la {_window_count + 1}e fois dans cette session. "
                            f"Cette approche ne retourne pas les informations dont tu as besoin. "
                            f"Essaie impérativement une COMMANDE DIFFÉRENTE pour atteindre ton objectif."
                        )

                    # ── Détecteur anti-aveuglement browser ──
                    # Si 3+ actions browser_* consécutives SANS revalidation visuelle → forcer à "voir"
                    _tool = action.tool_name or ""
                    _iter_now = len(self.history)
                    if _tool in BROWSER_VISUAL_TOOLS or _tool in BROWSER_SELF_VISUAL_ACTION_TOOLS:
                        self._last_browser_visual_iter = _iter_now
                        self._browser_blind_streak = 0
                    elif _tool in BROWSER_ACTION_TOOLS:
                        self._browser_blind_streak += 1
                        if self._browser_blind_streak >= 3:
                            logger.warning(
                                "⚠️ Aveuglement browser: {} actions consécutives sans voir — guidance injectée",
                                self._browser_blind_streak,
                            )
                            self._pending_loop_guidance = (
                                "⚠️ GUIDANCE VISION: Tu viens d'enchaîner "
                                f"{self._browser_blind_streak} actions browser_* sans prendre de screenshot "
                                "ni relire le DOM. Tu agis à l'aveugle. "
                                "APPELLE MAINTENANT `browser_screenshot` pour voir l'état réel de la page "
                                "avant ta prochaine action. Le DOM a probablement changé."
                            )
                            self._browser_blind_streak = 0  # reset après injection

                    # ── Guard anti-dérive post-blocage browser ─────────────────────────
                    # Après une impasse browser avec suggestion de dismiss, empêche la
                    # dérive vers run_command/curl/exec sans justification explicite.
                    if getattr(self, "_browser_post_block_guard", False):
                        if _tool in _BROWSER_DRIFT_TOOLS:
                            self._browser_post_block_guard = False
                            if not self._pending_loop_guidance:
                                self._pending_loop_guidance = (
                                    f"⚠️ GUIDANCE ANTI-DÉRIVE BROWSER: tu tentes d'utiliser `{_tool}` "
                                    "alors qu'un blocage browser est actif. Avant de quitter le navigateur, "
                                    "essaie d'abord : `browser_dismiss_popups`, `browser_scroll`, "
                                    "ou `browser_evaluate`. N'utilise des outils système que si "
                                    "le navigateur est définitivement infranchissable."
                                )
                        elif _tool.startswith("browser_"):
                            # Une action browser légitime → annule le guard
                            self._browser_post_block_guard = False

                    if self._consecutive_same_action >= 3:
                        logger.warning(f"⚠️ Boucle détectée: {action.tool_name} appelé 3x identiquement - forçage FINAL_ANSWER")
                        self._run_meta["agent_output_warning"] = "loop_detected_forced_final"
                        _finish_iteration(status="ok", summary="loop_break_3x_same_action")
                        # Synthétiser une réponse à partir de l'historique
                        summary_parts = []
                        for h in self.history[-5:]:
                            if h.observation and h.observation.content:
                                summary_parts.append(h.observation.content[:200])
                        message = "⚠️ La tâche a été interrompue car j'ai détecté une boucle.\n\n" + \
                               "**Ce que j'ai fait:**\n" + \
                               "\n".join([f"- {h.action.tool_name}" for h in self.history[-5:] if h.action.tool_name]) + \
                               ("\n\n**Derniers résultats:**\n" + "\n".join(summary_parts) if summary_parts else "")
                        self._mark_task_failed("loop_detected_forced_final")
                        # Notifier Telegram (non bloquant) — l'utilisateur doit savoir
                        try:
                            from ..autonomy.ops_handlers import _notify_telegram_proactive
                            asyncio.get_running_loop().create_task(
                                _notify_telegram_proactive(
                                    f"⚠️ <b>Lumena bloquée</b>\n"
                                    f"Tâche: <code>{query[:200]}</code>\n"
                                    f"Raison: boucle détectée ({action.tool_name} ×3)"
                                )
                            )
                        except Exception as e:
                            logger.debug(f"Telegram proactive notify: {e}")
                        return message
                    
                    # Compter uniquement les occurrences CONSÉCUTIVES identiques à la fin de la fenêtre.
                    # Si un outil différent a été appelé entre deux appels identiques, le contexte a
                    # changé → on ne compte pas les occurrences précédentes (évite les faux positifs).
                    recent_history = self.action_history[-8:]
                    same_consecutive_hits = 0
                    for _prev_action in reversed(recent_history):
                        if _prev_action == action_key:
                            same_consecutive_hits += 1
                        else:
                            break  # outil différent entre-deux = nouveau contexte
                    same_signature_hits = same_consecutive_hits
                    # Ne déclencher ce garde-fou que si la même action (outil + args) a été appelée
                    # au moins 2 fois CONSÉCUTIVEMENT sans autre outil entre les deux.
                    if same_signature_hits >= 2:
                        logger.warning(
                            "⚠️ Action répétée détectée ({}x): {}",
                            same_signature_hits + 1,
                            action.tool_name,
                        )
                        # Forcer une fin avec résumé
                        _finish_iteration(status="error", error="repeated_action_detected")
                        message = f"⚠️ J'ai détecté une boucle. Voici ce que j'ai fait:\n" + \
                                  "\n".join([f"- {h.action.tool_name}" for h in self.history[-5:] if h.action.tool_name])
                        self._mark_task_failed("repeated_action_detected")
                        # Notifier Telegram (non bloquant)
                        try:
                            from ..autonomy.ops_handlers import _notify_telegram_proactive
                            asyncio.get_running_loop().create_task(
                                _notify_telegram_proactive(
                                    f"⚠️ <b>Lumena bloquée</b>\n"
                                    f"Tâche: <code>{query[:200]}</code>\n"
                                    f"Raison: action répétée ({action.tool_name} ×{same_signature_hits + 1}x)"
                                )
                            )
                        except Exception as e:
                            logger.debug(f"Telegram proactive notify: {e}")
                        return message
                
                self.action_history.append(action_key)

            # ── Budget par outil ──────────────────────────────────────────────
            # Plafonds adaptatifs : au-delà, guidance injectée (pas de hard-stop
            # pour ne pas bloquer des tâches légitimement longues).
            _TOOL_SOFT_BUDGET: dict[str, int] = {
                "read_file": 12,
                "list_directory": 6,
                "grep_search": 10,
                "find_files": 6,
                "run_command": 20,
                "http_request": 8,
                "browser_get_content": 6,
            }
            if action.action_type == ActionType.TOOL_CALL and action.tool_name:
                _tname = action.tool_name
                _tbudget = _TOOL_SOFT_BUDGET.get(_tname, 0)
                if _tbudget:
                    _tcalls = sum(
                        1 for _ak in self.action_history
                        if isinstance(_ak, tuple) and _ak[0] == _tname
                    )
                    if _tcalls >= _tbudget and not getattr(self, "_pending_loop_guidance", None):
                        self._pending_loop_guidance = (
                            f"⚠️ Budget outil dépassé : `{_tname}` appelé {_tcalls}× "
                            f"(budget conseillé : {_tbudget}×). "
                            "Continue uniquement si l'outil est strictement nécessaire, "
                            "sinon passe à l'étape productive suivante ou conclus avec FINAL."
                        )
                        logger.warning(
                            "⚠️ Budget outil: {} appelé {}x (budget={})",
                            _tname, _tcalls, _tbudget,
                        )
            # ─────────────────────────────────────────────────────────────────

            # 3. Créer l'étape
            step = ReActStep(thought=thought, action=action)

            if action.action_type == ActionType.CLARIFY:
                self.history.append(step)
                question = (action.answer or thought.content or "Peux-tu préciser ta demande ?").strip()
                checkpoint_payload = {
                    "phase": "clarify_waiting_io",
                    "iteration": i + 1,
                    "original_query": original_query[:2000],
                    "pending_query": query[:4000],
                    "clarification_question": question[:2000],
                    "history_size": len(self.history),
                }
                self._mark_task_checkpoint(checkpoint_payload)
                self._mark_task_waiting_io("clarification_required", checkpoint=checkpoint_payload)
                self._run_meta["agent_output_warning"] = "clarification_required"
                # ── StructuredState V1 : enregistrer la question en attente ──
                self._feed_structured_clarification(question)
                _finish_iteration(status="ok", summary="clarify_waiting_io")
                return question
            
            # 4. Si c'est une réponse finale, retourner
            if action.action_type == ActionType.FINAL_ANSWER:
                _document_deterministic_final = False
                _document_workflow_incomplete_final = False
                _document_free_grounded_final = False
                # ── Anti-hallucination/fuite : ré-imposer les valeurs masquées vues
                # en observation (ex. config BDD IONOS) avant tout traitement du FINAL.
                if action.answer:
                    _remasked = self._remask_observed_masked_values(action.answer)
                    if _remasked != action.answer:
                        logger.warning("[MASK GUARD] valeurs masquées reconstituées dans le FINAL — ré-masquées")
                        action.answer = _remasked
                        step.action.answer = _remasked
                self.history.append(step)
                # Multi-document truth gate: exact paths and render proofs come from
                # the successful Studio observations, never from a directory listing.
                try:
                    _doc_route = self._document_route_for_run()
                    _doc_requested = _doc_route.requested_count
                    _doc_manifest, _doc_missing, _doc_unverified = (
                        self._structured_document_delivery_manifest()
                    )
                    _doc_incomplete = bool(_doc_missing or _doc_unverified)
                    _doc_truth_required = self._document_delivery_truth_required(
                        _doc_route, _doc_requested,
                    )
                    if _doc_truth_required and _doc_incomplete:
                        _doc_gate_shots = getattr(self, "_document_delivery_gate_shots", 0)
                        if _doc_gate_shots < 1 and i < self.max_iterations - 2:
                            self._document_delivery_gate_shots = _doc_gate_shots + 1
                            self.history.pop()
                            _targets = tuple(dict.fromkeys((*_doc_missing, *_doc_unverified)))
                            query = (
                                f"Requete originale: {original_query}\n\n"
                                f"DOCUMENTS NON CERTIFIES: {len(_doc_manifest)}/{_doc_requested} "
                                "livrables exacts sont prouves. Reprends les types manquants ou dont "
                                f"le rendu a echoue ({', '.join(_targets)}), utilise Document Studio "
                                "type par type"
                                + (
                                    f", en respectant le minimum explicite de "
                                    f"{_doc_route.minimum_pages} pages"
                                    if _doc_route.minimum_pages
                                    else ""
                                )
                                + ", puis conclus depuis les chemins retournes par les outils."
                            )
                            _finish_iteration(status="ok", summary="document_delivery_gate_relaunch")
                            continue
                    if _doc_truth_required:
                        from src.documents.delivery_manifest import build_multi_document_final

                        _receipt_id = self._ensure_document_delivery_reference()

                        _pending_workflow = (
                            self._document_workflow_pending_action()
                            if not _doc_missing and not _doc_unverified
                            else None
                        )
                        _pending_name = getattr(_pending_workflow, "operation", "")
                        _workflow_shots = getattr(self, "_document_workflow_gate_shots", {})
                        _shot_count = int(_workflow_shots.get(_pending_name, 0)) if _pending_name else 0
                        if (
                            _pending_name in {
                                "open", "revise", "verify", "history", "export",
                                "library_verify",
                            }
                            and _shot_count < 1
                            and i < self.max_iterations - 2
                        ):
                            _workflow_shots = dict(_workflow_shots)
                            _workflow_shots[_pending_name] = _shot_count + 1
                            self._document_workflow_gate_shots = _workflow_shots
                            self.history.pop()
                            if _pending_name == "open" and _receipt_id:
                                query = (
                                    f"Requete originale: {original_query}\n\n"
                                    "WORKFLOW DOCUMENTAIRE: la generation exacte est terminee. "
                                    f"Appelle maintenant `open_document_delivery(receipt_id='{_receipt_id}')`. "
                                    "N'utilise ni list_directory ni run_command. Ensuite poursuis les actions "
                                    "restantes de la requete originale."
                                )
                                _finish_iteration(status="ok", summary="document_workflow_open_gate")
                                continue
                            if _pending_name == "revise":
                                _target = self._document_workflow_target()
                                if _target is not None and _target.document_id:
                                    query = (
                                        f"Requete originale: {original_query}\n\n"
                                        "WORKFLOW DOCUMENTAIRE: modifie maintenant exactement le document "
                                        f"`{_target.document_id}` (`{_target.filename}`) avec "
                                        "`revise_studio_document`. N'invente aucun champ: utilise seulement "
                                        "les champs editables de sa recette. Si le champ demande n'est pas "
                                        "editable, rapporte le refus honnetement sans creer de faux fichier."
                                    )
                                    _finish_iteration(status="ok", summary="document_workflow_revision_gate")
                                    continue

                            if _pending_name == "verify":
                                _proof_state = self._document_workflow_proof_state()
                                _revision = _proof_state.get("revision") or {}
                                _revised = _revision.get("proof")
                                _patch = self._document_revision_changed_fields(_revision)
                                _values = self._document_patch_scalar_values(_patch)
                                if _revised is not None and _revised.path:
                                    query = (
                                        f"Requete originale: {original_query}\n\n"
                                        "WORKFLOW DOCUMENTAIRE: verifie maintenant la version revisee "
                                        f"avec `read_document(path='{_revised.path}')`. La relecture doit "
                                        "confirmer la ou les valeurs appliquees"
                                        + (f" ({', '.join(_values)})" if _values else "")
                                        + "; ensuite seulement, fournis le bilan exact."
                                    )
                                    _finish_iteration(
                                        status="ok", summary="document_workflow_verify_gate",
                                    )
                                    continue

                            if _pending_name == "history":
                                _proof_state = self._document_workflow_proof_state()
                                _revision = _proof_state.get("revision") or {}
                                _revised = _revision.get("proof")
                                if _revised is not None and _revised.document_id:
                                    query = (
                                        f"Requete originale: {original_query}\n\n"
                                        "WORKFLOW DOCUMENTAIRE: recupere maintenant la provenance "
                                        "parent/enfant exacte avec "
                                        f"`get_document_history(document_id='{_revised.document_id}')`. "
                                        "Le parent retourne doit etre le document original ouvert."
                                    )
                                    _finish_iteration(
                                        status="ok", summary="document_workflow_history_gate",
                                    )
                                    continue

                            if _pending_name == "export":
                                _proof_state = self._document_workflow_proof_state()
                                _revision = _proof_state.get("revision") or {}
                                _revised = _revision.get("proof")
                                _format = str(
                                    getattr(_pending_workflow, "output_format", "") or "html"
                                )
                                if _revised is not None and _revised.document_id:
                                    query = (
                                        f"Requete originale: {original_query}\n\n"
                                        "WORKFLOW DOCUMENTAIRE: exporte maintenant exactement la "
                                        f"version revisee avec `convert_library_document(document_id="
                                        f"'{_revised.document_id}', output_format='{_format}')`. "
                                        "Utilise l'identifiant enfant retourne pour la suite."
                                    )
                                    _finish_iteration(
                                        status="ok", summary="document_workflow_export_gate",
                                    )
                                    continue

                            if _pending_name == "library_verify":
                                _proof_state = self._document_workflow_proof_state()
                                _target = _proof_state.get("target")
                                _revision = _proof_state.get("revision") or {}
                                _revised = _revision.get("proof")
                                _export = _proof_state.get("export") or {}
                                _export_record = _export.get("record", {})
                                _export_proof = _export.get("proof")
                                _ids = [
                                    str(getattr(_target, "document_id", "") or ""),
                                    str(getattr(_revised, "document_id", "") or ""),
                                    str(
                                        (_export_record.get("id") if isinstance(_export_record, dict) else "")
                                        or getattr(_export_proof, "document_id", "") or ""
                                    ),
                                ]
                                _ids = [value for value in _ids if value]
                                if _ids:
                                    query = (
                                        f"Requete originale: {original_query}\n\n"
                                        "WORKFLOW DOCUMENTAIRE: verifie la presence en bibliotheque "
                                        "par identifiant exact, sans recherche floue. Appelle "
                                        "`get_document_record` pour chacun de ces identifiants: "
                                        + ", ".join(_ids)
                                        + ". Conclus seulement quand ils sont tous retrouves."
                                    )
                                    _finish_iteration(
                                        status="ok", summary="document_workflow_library_gate",
                                    )
                                    continue

                        _workflow_state = self._document_workflow_proof_state()
                        _revision_record = _workflow_state.get("revision") or {}
                        _revised_proof = _revision_record.get("proof")
                        _verification_event = _workflow_state.get("verification") or {}
                        _open_event = _workflow_state.get("open") or {}
                        _compound_revision_ready = (
                            len(getattr(_doc_route, "workflow_actions", ()) or ()) > 1
                            and _workflow_state.get("target") is not None
                            and _revised_proof is not None
                        )
                        if _compound_revision_ready and not _pending_name and _verification_event:
                            from src.documents.delivery_manifest import build_document_workflow_final

                            _revision_action = next(
                                (
                                    item for item in _doc_route.workflow_actions
                                    if getattr(item, "operation", "") == "revise"
                                ),
                                None,
                            )
                            _verification_args = _verification_event.get("args", {})
                            honest = build_document_workflow_final(
                                _doc_manifest,
                                requested_count=_doc_requested,
                                receipt_id=_receipt_id,
                                opened=int(_open_event.get("opened") or 0),
                                failed=int(_open_event.get("failed") or 0),
                                target_ordinal=int(
                                    getattr(_revision_action, "target_ordinal", 0) or 0
                                ),
                                target=_workflow_state["target"],
                                revised=_revised_proof,
                                changed_fields=self._document_revision_changed_fields(
                                    _revision_record,
                                ),
                                verification_path=str(
                                    _verification_args.get("path")
                                    or _verification_args.get("file_path")
                                    or _revised_proof.path
                                ),
                                history_parent_id=str(
                                    (((_workflow_state.get("history") or {}).get("payload") or {}).get("document") or {}).get("parent_id")
                                    or (((((_workflow_state.get("history") or {}).get("payload") or {}).get("document") or {}).get("metadata") or {}).get("parent_id"))
                                    or ""
                                ),
                                exported_document_id=str(
                                    (((_workflow_state.get("export") or {}).get("record") or {}).get("id"))
                                    or getattr(
                                        (_workflow_state.get("export") or {}).get("proof"),
                                        "document_id", "",
                                    )
                                    or ""
                                ),
                                exported_path=str(
                                    (((_workflow_state.get("export") or {}).get("record") or {}).get("path"))
                                    or getattr(
                                        (_workflow_state.get("export") or {}).get("proof"),
                                        "path", "",
                                    )
                                    or ""
                                ),
                                library_document_ids=tuple(
                                    (_workflow_state.get("library_verify") or {}).get(
                                        "document_ids", (),
                                    )
                                ),
                            )
                        elif _compound_revision_ready and _pending_name in {
                            "verify", "history", "export", "library_verify",
                        }:
                            from src.documents.delivery_manifest import (
                                build_document_workflow_incomplete_final,
                            )

                            _revision_action = next(
                                (
                                    item for item in _doc_route.workflow_actions
                                    if getattr(item, "operation", "") == "revise"
                                ),
                                None,
                            )
                            honest = build_document_workflow_incomplete_final(
                                _doc_manifest,
                                requested_count=_doc_requested,
                                receipt_id=_receipt_id,
                                opened=int(_open_event.get("opened") or 0),
                                failed=int(_open_event.get("failed") or 0),
                                target_ordinal=int(
                                    getattr(_revision_action, "target_ordinal", 0) or 0
                                ),
                                target=_workflow_state["target"],
                                revised=_revised_proof,
                                changed_fields=self._document_revision_changed_fields(
                                    _revision_record,
                                ),
                                pending_operation=_pending_name,
                                verification_confirmed=bool(_verification_event),
                            )
                            _document_workflow_incomplete_final = True
                        else:
                            honest = build_multi_document_final(
                                _doc_manifest, requested_count=_doc_requested,
                                receipt_id=_receipt_id,
                            )
                        if _doc_missing:
                            honest += "\n\nNon livres: " + ", ".join(_doc_missing) + "."
                        if _pending_name and "Action restante non prouvee:" not in honest:
                            honest += (
                                "\n\nAction restante non prouvee: " + _pending_name
                                + ". Je ne la declare pas terminee."
                            )
                        from src.documents.delivery_manifest import (
                            build_document_grounding_request,
                            document_free_answer_is_grounded,
                        )

                        _grounding_shots = int(
                            getattr(self, "_document_free_final_grounding_shots", 0)
                        )
                        if _grounding_shots < 1 and i < self.max_iterations - 2:
                            self._document_free_final_grounding_shots = _grounding_shots + 1
                            self.history.pop()
                            query = build_document_grounding_request(
                                original_query, honest,
                            )
                            _finish_iteration(
                                status="ok", summary="document_free_final_grounding",
                            )
                            continue

                        _free_grounded = document_free_answer_is_grounded(
                            action.answer or "",
                            _doc_manifest,
                            missing=_doc_missing,
                            receipt_id=_receipt_id,
                            pending_operation=_pending_name or "",
                        )
                        if _free_grounded:
                            _document_free_grounded_final = True
                            logger.info(
                                "[DOCUMENT DELIVERY] final libre accepte depuis les preuves exactes"
                            )
                        elif _doc_route.owns_run:
                            action.answer = honest
                            step.action.answer = honest
                            _document_deterministic_final = True
                            logger.warning(
                                "[DOCUMENT DELIVERY] final libre insuffisamment ancre; "
                                "repli deterministe"
                            )
                        else:
                            # A mission owns its final answer. Document Studio
                            # contributes exact evidence but never replaces the
                            # global report for code, tests, browser or MCP work.
                            action.answer = self._merge_mission_document_evidence(
                                action.answer or "", honest,
                            )
                            step.action.answer = action.answer
                            logger.warning(
                                "[DOCUMENT DELIVERY] preuves ajoutees au final libre de mission"
                            )
                except Exception as _doc_gate_exc:
                    logger.debug("[DOCUMENT DELIVERY GATE] skip: {}", _doc_gate_exc)
                try:
                    _rights_relevant, _rights_proven = self._document_web_rights_evidence()
                    if _rights_relevant:
                        from .final_guards import apply_document_rights_truth_lock

                        action.answer, _rights_info = apply_document_rights_truth_lock(
                            action.answer or "", rights_proven=_rights_proven,
                        )
                        step.action.answer = action.answer
                        if _rights_info.get("changed"):
                            logger.warning(
                                "[DOCUMENT RIGHTS LOCK] claim de droits sans preuve explicite"
                            )
                except Exception as _rights_exc:
                    logger.debug("[DOCUMENT RIGHTS LOCK] skip: {}", _rights_exc)
                # ── Guard anti-hallucination d'ACTION (avec OU sans plan) ──────────
                # Tourne pour TOUT FINAL : « j'ai tapé/cliqué/ouvert l'app/connecté/
                # créé/envoyé » sans outil RÉUSSI → retry forcé. Couvre les tâches
                # SANS plan, où les hallucinations CU/login passaient à travers
                # (le bloc plan plus bas garde sa propre vérif, désormais redondante).
                _hc_combined = (
                    (action.answer or "")
                    if _document_deterministic_final
                    else ((thought.content or "") + " " + (action.answer or ""))
                ).lower()
                _hc_retry = (
                    None
                    if _document_deterministic_final
                    else self._action_hallucination_retry_query(_hc_combined, original_query)
                )
                if _hc_retry is not None:
                    self.history.pop()
                    query = _hc_retry
                    _finish_iteration(status="ok", summary="hallucination_action_blocked")
                    continue
                # ── Plan TODO : bilan ──
                # Default: sans plan, on ne sait rien → repair garde son comportement standard.
                # Le flag passe à True uniquement si on a un plan ET que toutes les tâches métier sont done.
                _plan_business_complete = False
                if self._task_plan:
                    # C (sous-agents) — une mission LANCÉE ce tour tourne en arrière-plan :
                    # les tâches de « suivi de mission » (poll status/result) ne sont PAS du
                    # travail à faire MAINTENANT. On les auto-complète pour que le PLAN GUARD
                    # ne force pas le chat à baby-sitter (sinon il finit par refaire le travail).
                    # Ciblé : uniquement si create_mission a réussi ET tâche = suivi de mission.
                    if "create_mission" in (self._successful_session_tools or set()):
                        for _st in self._task_plan:
                            if not _st.completed and is_mission_tracking_task(_st.description):
                                _st.completed = True
                                _st.completed_by_tool = "create_mission"
                    # #2 — une délégation RÉUSSIE accomplit les tâches « lancer/déléguer
                    # à des sous-agents/workers » (sinon le plan tracker les laisse SKIP
                    # → PLAN GUARD bloque un tour pour rien). Gaté sur le succès réel.
                    if "delegate_and_wait" in (self._successful_session_tools or set()):
                        for _st in self._task_plan:
                            if not _st.completed and delegation_task_fulfilled(_st.description):
                                _st.completed = True
                                _st.completed_by_tool = "delegate_and_wait"
                    # Auto-compléter les tâches de synthèse/résumé (réalisées par
                    # FINAL lui-même) — logique pure : plan_progress.final_fulfills_task.
                    # Avant l'auto-mark : on note si toutes les tâches "métier" (non-synthèse)
                    # étaient déjà completed. Si oui → le travail est vraiment fini, on évite
                    # les repair-loops thought_leak/final_tronqué sur la branche FINAL.
                    _business_tasks_remaining = sum(
                        1
                        for _t in self._task_plan
                        if not _t.completed and not self._document_final_fulfills_plan_task(_t.description)
                    )
                    _plan_business_complete = _business_tasks_remaining == 0
                    _operational_tasks_remaining = [
                        _t.description
                        for _t in self._task_plan
                        if not _t.completed
                        and final_requires_operational_proof(_t.description)
                    ]
                    for _st in self._task_plan:
                        if not _st.completed and self._document_final_fulfills_plan_task(_st.description):
                            _st.completed = True
                            _st.completed_by_tool = "FINAL"
                    completed = sum(1 for t in self._task_plan if t.completed)
                    total = len(self._task_plan)
                    logger.info(f"[PLAN BILAN] {completed}/{total} taches completees")
                    for t in self._task_plan:
                        status = "OK" if t.completed else "SKIP"
                        logger.info(f"  [{status}] {t.description}")
                    # Émettre l'état final SANS masquer les SKIP : seules les tâches
                    # réellement accomplies (ou de synthèse) restent completed. Les autres
                    # apparaîtront comme ⏭️ et reflètent la vérité.
                    self._plan_last_emit_state = ""  # reset dédup pour forcer l'émission
                    self._emit_plan_state(context_tool="FINAL")
                    # ── Guard anti-FINAL prématuré : plan largement incomplet ──
                    remaining = total - completed
                    # "Clarification" : la réponse finit par "?" OU contient une liste
                    # d'options (tirets/numéros) typique d'une demande de précisions.
                    _answer_text = action.answer or ""
                    _answer_stripped = _answer_text.strip().rstrip(" \n")
                    _ends_with_question = _answer_stripped.endswith("?")
                    _has_option_list = (
                        "?" in _answer_text
                        and any(
                            p in _answer_text
                            for p in ("\n- ", "\n1.", "\n2.", "\n•", "\n* ")
                        )
                    )
                    _is_clarification = _ends_with_question or _has_option_list
                    # CODE_READ (analyse) : le LLM a lu ce qu'il lui fallait et
                    # rédige sa synthèse → ne pas bloquer son FINAL.
                    _is_read_only = False  # v2: mode lecture seule supprimé
                    # B′ (Lot 5) — relaxation contexte-mission : le plan d'un worker est
                    # AUTO-généré, pas un contrat utilisateur. On garde UN nudge (le 1er
                    # blocage peut faire approfondir le worker — observé 13:36 : a déclenché
                    # un deep_research utile), puis si le worker a VRAIMENT travaillé
                    # (recherche/livrable) on le laisse conclure au lieu de répéter 2
                    # blocages stériles. Scopé missions → le chat garde ses 3 retries
                    # pleins. Le filet "tâches critiques" (login/auth) plus bas reste actif.
                    _mission_relax = (
                        self._is_mission_run
                        and self._plan_guard_retries >= 1
                        and mission_progress_proven(self._successful_session_tools)
                        and not _operational_tasks_remaining
                    )
                    # Compatibilité avec les anciennes métadonnées. Sous la politique
                    # complete-only ce hook reste toujours False : un artefact partiel
                    # ne relâche jamais un plan incomplet.
                    _deadline_finalized = False
                    if self._is_mission_run and self._orchestrator_enabled():
                        try:
                            from src.subagents.mission_budget import (
                                extract_unambiguous_target_file as _etf2, deadline_final_exit_allowed,
                            )
                            _gm = (self.task_orchestrator.get_task(self.task_id) or {}).get("metadata") or {}
                            _deadline_finalized = deadline_final_exit_allowed(
                                partial_due_to_deadline=bool(_gm.get("partial_due_to_deadline")),
                                target_file=_etf2(_gm.get("objective") or ""),
                                artifact_written=bool(_gm.get("deadline_artifact_written")),
                            ) and not _operational_tasks_remaining
                        except Exception:
                            _deadline_finalized = False
                    _completion_snapshot = self._mission_completion_evidence()
                    _evidence_finalized = bool(_completion_snapshot.get("complete"))
                    _worker_incomplete = (
                        _completion_snapshot.get("scope") == "worker"
                        and not _evidence_finalized
                    )
                    if _evidence_finalized:
                        logger.info(
                            "[M106] stale PLAN bypassed by authoritative {} evidence: "
                            "delivery={} tests_required={} tests_green={} task={}",
                            _completion_snapshot.get("scope") or "mission",
                            _completion_snapshot.get("delivery_proven"),
                            _completion_snapshot.get("tests_required"),
                            _completion_snapshot.get("tests_green"),
                            self.task_id,
                        )
                        if self._orchestrator_enabled():
                            try:
                                self.task_orchestrator.set_task_metadata(
                                    self.task_id,
                                    completion_proven=True,
                                    completion_proof=dict(_completion_snapshot),
                                    terminal_reason_code="completed",
                                    terminal_reason_detail=(
                                        "worker delivery complete from assigned files and tests"
                                        if _completion_snapshot.get("scope") == "worker"
                                        else "mission complete from publication and required proofs"
                                    ),
                                )
                            except Exception as exc:
                                logger.debug("[M106] proof bypass persistence skipped: {}", exc)
                    elif _worker_incomplete and self._plan_guard_retries >= 3:
                        _worker_issues = []
                        if _completion_snapshot.get("missing_files"):
                            _worker_issues.append(
                                "missing=" + ",".join(_completion_snapshot["missing_files"])
                            )
                        if _completion_snapshot.get("stub_files"):
                            _worker_issues.append(
                                "stubs=" + ",".join(_completion_snapshot["stub_files"])
                            )
                        if _completion_snapshot.get("invalid_files"):
                            _worker_issues.append(
                                "invalid=" + ",".join(_completion_snapshot["invalid_files"])
                            )
                        if (
                            _completion_snapshot.get("tests_required")
                            and not _completion_snapshot.get("tests_green")
                        ):
                            _worker_issues.append("pytest_not_green")
                        _worker_detail = "; ".join(_worker_issues) or "proofs_incomplete"
                        if self._orchestrator_enabled():
                            try:
                                self.task_orchestrator.set_task_metadata(
                                    self.task_id,
                                    terminal_reason_code="failed",
                                    terminal_reason_detail=(
                                        "worker_delivery_incomplete: " + _worker_detail
                                    ),
                                    completion_proven=False,
                                    completion_proof=dict(_completion_snapshot),
                                )
                            except Exception:
                                pass
                        raise RuntimeError("worker_delivery_incomplete: " + _worker_detail)
                    if (
                        (_business_tasks_remaining > 0 or remaining >= 2 or _worker_incomplete)
                        and (
                            self._plan_guard_retries < _PLAN_GUARD_MAX_RETRIES
                            or (
                                bool(_operational_tasks_remaining)
                                # LOT Z6 — ce court-circuit n'avait pas de borne :
                                # 18 refus consécutifs sur un plafond annoncé de 3.
                                and self._plan_guard_retries
                                < _PLAN_GUARD_MAX_RETRIES_OPERATIONAL
                            )
                        )
                        and not _is_clarification
                        and not _is_read_only
                        and not _mission_relax
                        and not _deadline_finalized
                        and not _evidence_finalized
                        and not _document_workflow_incomplete_final
                        and i < self.max_iterations - 2
                    ):
                        self._plan_guard_retries += 1
                        logger.warning(
                            "[PLAN GUARD] FINAL premature bloque: {}/{} taches, iteration {} (retry {}/{})",
                            completed, total, i, self._plan_guard_retries,
                            # LOT Z6 — afficher le plafond RÉELLEMENT appliqué : le
                            # message annonçait « /3 » pendant 18 refus, ce qui a
                            # masqué le défaut aussi longtemps qu'il a duré.
                            _PLAN_GUARD_MAX_RETRIES_OPERATIONAL
                            if _operational_tasks_remaining
                            else _PLAN_GUARD_MAX_RETRIES,
                        )
                        self.history.pop()
                        uncompleted = [t.description for t in self._task_plan if not t.completed]
                        _worker_proof_hint = ""
                        if _worker_incomplete:
                            _problem_lines = []
                            if _completion_snapshot.get("missing_files"):
                                _problem_lines.append(
                                    "Fichiers assignes MANQUANTS: "
                                    + ", ".join(_completion_snapshot["missing_files"])
                                )
                            if _completion_snapshot.get("stub_files"):
                                _problem_lines.append(
                                    "Fichiers assignes ENCORE STUBS: "
                                    + ", ".join(_completion_snapshot["stub_files"])
                                )
                            if _completion_snapshot.get("invalid_files"):
                                _problem_lines.append(
                                    "Chemins assignes invalides: "
                                    + ", ".join(_completion_snapshot["invalid_files"])
                                )
                            if (
                                _completion_snapshot.get("tests_required")
                                and not _completion_snapshot.get("tests_green")
                            ):
                                _problem_lines.append(
                                    "Le dernier pytest de ce worker n'est pas vert."
                                )
                            if _problem_lines:
                                _worker_proof_hint = (
                                    "\n\nCONTROLE DISQUE AUTORITATIF (il prime sur les "
                                    "cases du plan):\n- " + "\n- ".join(_problem_lines)
                                    + "\nRemplis/corrige exactement ces fichiers par mutation, "
                                    "relance pytest si requis, puis seulement FINAL."
                                )
                        query = (
                            f"Requête originale: {original_query}\n\n"
                            "⚠️ Tu as tenté de terminer (FINAL) alors que ton plan n'est PAS terminé!\n"
                            f"Plan: {completed}/{total} tâches complétées. Il reste:\n"
                            + "\n".join(f"- {d}" for d in uncompleted[:5]) + "\n\n"
                            "CONTINUE ton plan. Exécute la prochaine tâche maintenant. "
                            "Une tâche navigateur, pytest, publication ou outil nommé exige "
                            "la preuve réelle de cet outil : un texte FINAL ne la remplace jamais. "
                            "Si une dépendance externe reste impossible après un vrai pivot, "
                            "termine en échec explicite au lieu de la déclarer accomplie."
                        )
                        query += _worker_proof_hint
                        _finish_iteration(status="ok", summary="premature_final_blocked")
                        continue
                    # ── Guard anti-hallucination d'ACTION (in-plan) : SUPPRIMÉ ──
                    # Ce guard dupliquait à l'identique `hallucination_retry_query`
                    # (mêmes _HALLUCINATION_CLAIM_PATTERNS, même négation, même
                    # exemption runtime/browser, même compteur). Or le guard
                    # centralisé tourne déjà plus haut (cf. _action_hallucination_retry_query,
                    # appelé avant le split `if self._task_plan`) pour TOUS les FINAL.
                    # → bloc mort retiré (Temps 1, déménagement pur, zéro changement).

                    # ── Guard anti-hallucination : tâches critiques marquées SKIP ──
                    _CRITICAL_KW = {
                        "login", "se connecter", "connecter", "logg",
                        "dashboard", "mot de passe", "password", "vérifier accès",
                        "verifier acces", "admin", "authentif", "sign in", "signin",
                    }
                    if not _evidence_finalized and self._premature_final_retries < 2:
                        critical_skipped = [
                            t.description for t in self._task_plan
                            if not t.completed
                            and any(_kw in t.description.lower() for _kw in _CRITICAL_KW)
                        ]
                        if critical_skipped:
                            self._premature_final_retries += 1
                            logger.warning(
                                "[PLAN GUARD] Tâches critiques non complétées: {} (retry {}/2)",
                                critical_skipped, self._premature_final_retries,
                            )
                            self.history.pop()
                            query = (
                                f"Requête originale: {original_query}\n\n"
                                "⚠️ Tu as déclaré FINAL sans avoir accompli ces étapes critiques:\n"
                                + "\n".join(f"- {d}" for d in critical_skipped[:5]) + "\n\n"
                                "Tu NE DOIS PAS prétendre que ces étapes sont faites si elles ne le sont pas. "
                                "Exécute-les maintenant (connexion, vérification d'accès, etc.)."
                            )
                            _finish_iteration(status="ok", summary="critical_tasks_incomplete")
                            continue

                    # ── Guard : tâche Discord action (animer/poster) sans envoi réel ──
                    # Quand l'user demande d'animer/poster/envoyer sur Discord, Lumena DOIT
                    # avoir appelé discord_send ou discord_send_message avec succès.
                    # Fetcher des messages ne suffit PAS — il faut ENVOYER.
                    _DISCORD_SEND_TOOLS = {"discord_send", "discord_send_message", "discord_send_embed"}
                    # Demande POSITIVE d'envoi/post Discord uniquement. Une mission
                    # read-only (contrôle/liste/vérifie/statut) ou niée
                    # ("sans envoyer", "ne poste pas") n'arme PAS ce guard.
                    _is_discord_action = discord_requires_send(original_query)
                    if _is_discord_action and self._premature_final_retries < 2:
                        _used = {h.action.tool_name for h in self.history if h.action and h.action.tool_name}
                        _used_send = _used & _DISCORD_SEND_TOOLS
                        # Compter les discord_send qui ont RÉUSSI
                        _send_success_count = sum(
                            1 for h in self.history
                            if h.action and h.action.tool_name in _DISCORD_SEND_TOOLS
                            and h.observation and h.observation.success
                        )
                        if _send_success_count == 0:
                            self._premature_final_retries += 1
                            _hint = "Aucun outil d'envoi Discord appelé" if not _used_send else "discord_send a échoué — retenter avec le bon channel"
                            logger.warning(
                                "[DISCORD ACTION GUARD] Tâche Discord FINAL sans envoi réussi ({}) - retry {}/2",
                                _hint, self._premature_final_retries,
                            )
                            self.history.pop()
                            query = (
                                f"Requête originale: {original_query}\n\n"
                                "⛔ Tu n'as PAS encore envoyé de message sur Discord!\n"
                                f"({_hint})\n\n"
                                "Tu DOIS appeler discord_send ou discord_send_message avec le contenu du message "
                                "et un channel_name valide (ex: 'général') pour RÉELLEMENT poster.\n"
                                "discord_list_channels et discord_fetch_messages NE SONT PAS suffisants — "
                                "il faut ENVOYER un message avec discord_send."
                            )
                            _finish_iteration(status="ok", summary="discord_action_no_send")
                            continue

                        # Guard anti-exagération : le FINAL prétend plus d'envois que la réalité
                        _final_text = _combined_text
                        # Compter les channels mentionnés dans la réponse FINAL (#channel-name)
                        # Exclure les headings markdown (##) et les IDs numériques (#9654)
                        _claim_channels_raw = re.findall(
                            r"(?<!\#)#([^\s\*\#\(\)\[\]]{2,40})",
                            _final_text,
                        )
                        _claim_channels = [
                            c.rstrip("*").rstrip(",").rstrip(".")
                            for c in _claim_channels_raw
                            if not c.replace("-", "").replace("_", "").isdigit()
                        ]
                        # Aussi compter les bullet-points décrivant des actions Discord
                        _bullet_action_count = len(re.findall(
                            r"^[\s\-\*]*\*?\*?#.+(?:→|—|:).+(?:initié|lancé|envoyé|partagé|posté|animé|créé|publié|discussion|message|fil|sondage|question)",
                            _final_text, re.MULTILINE | re.IGNORECASE,
                        ))
                        _claim_count = max(len(_claim_channels), _bullet_action_count)

                        # Extraire les noms de salons RÉELLEMENT utilisés depuis les observations
                        _actual_channels = set()
                        for h in self.history:
                            if (h.action and h.action.tool_name in _DISCORD_SEND_TOOLS
                                    and h.observation and h.observation.success):
                                _ch_match = re.search(r"dans #([^\s\(]+)", h.observation.content or "")
                                if _ch_match:
                                    _actual_channels.add(_ch_match.group(1).lower().strip())

                        # BLOQUER le FINAL si claims > réalité (forcer retry)
                        _needs_block = False
                        _mismatch_info = ""
                        if _claim_count > _send_success_count and _send_success_count >= 1:
                            _needs_block = True
                            _mismatch_info = f"FINAL prétend {_claim_count} envois mais seulement {_send_success_count} ont réussi"
                        elif _claim_channels and _actual_channels:
                            # Lot RF-9d : la DECISION sort ; le blocage reste ici.
                            _phantom = phantom_channels(_claim_channels, _actual_channels)
                            if _phantom:
                                _needs_block = True
                                _mismatch_info = f"salons inventés: {_phantom} (réels: {_actual_channels})"

                        if _needs_block and self._premature_final_retries < 2:
                            self._premature_final_retries += 1
                            logger.warning(
                                "[DISCORD COUNT GUARD] {} — FINAL bloqué, retry {}/2",
                                _mismatch_info, self._premature_final_retries,
                            )
                            _missing = set(c.lower() for c in _claim_channels) - _actual_channels if _claim_channels else set()
                            _missing_list = ", ".join(f"#{c}" for c in sorted(_missing)) if _missing else "les salons annoncés"
                            self.history.pop()
                            query = (
                                f"Requête originale: {original_query}\n\n"
                                f"⛔ MENSONGE DÉTECTÉ dans ta réponse ! Tu as prétendu avoir posté dans "
                                f"{_claim_count} salons mais tu n'as réellement envoyé que "
                                f"{_send_success_count} message(s) ({', '.join(f'#{c}' for c in sorted(_actual_channels)) if _actual_channels else 'inconnu'}).\n\n"
                                f"Tu DOIS maintenant RÉELLEMENT envoyer des messages dans {_missing_list}.\n"
                                f"Appelle discord_send avec channel_name pour CHAQUE salon manquant.\n"
                                f"NE VA PAS à FINAL avant d'avoir RÉELLEMENT envoyé tous les messages."
                            )
                            _finish_iteration(status="ok", summary="discord_count_guard_blocked")
                            continue

                # ── Guard anti-hallucination sans plan : SUPPRIMÉ (centralisé) ──
                # Ce guard `_HP_NOPLAN` est désormais couvert par le guard centralisé
                # (hallucination_retry_query, appelé plus haut pour TOUS les FINAL).
                # Ses spécificités ont été migrées dans hallucination_guard.py :
                #   • patterns install/activation/déploiement + « a été … » + « avec succès »
                #     → ajoutés à _HALLUCINATION_CLAIM_PATTERNS (mappés sur ANY_ACTION) ;
                #   • « c'est fait » → ANY_ACTION (toute action réelle = preuve) ;
                #   • outils MCP dynamiques exonèrent les claims vagues (cf. _HC_GENERIC_FAMILIES).
                # → bloc retiré (Temps 2, centralisation).

                # ── ExecutionLedger FINAL guard (decision-core → ledger_guard.py) ──
                # Si le FINAL prétend avoir agi mais que le ledger ne contient
                # aucune mutation réussie, bloquer une fois et forcer l'exécution.
                _ledger_guard_triggered = False
                if not getattr(self, '_ledger_final_guard_used', False):
                    _final_text_lower = ((action.answer or "") + " " + (thought.content or "")).lower()
                    # Phase I-8 (Fix AF) : normalise les apostrophes typographiques
                    # (DeepSeek écrit souvent « j'ai ») pour que les patterns matchent.
                    for _apo in ("’", "‘", "ʼ", "´", "`"):
                        _final_text_lower = _final_text_lower.replace(_apo, "'")
                    _runtime_claim_for_final = _has_runtime_server_claim_proof(_final_text_lower, self._successful_session_tools)
                    _claims_action = ledger_text_claims_action(_final_text_lower)

                    # Exonération read-only : rapport read-only sans mutation attendue.
                    _eff_succ_tools = compute_effective_successful_tools(self.history)
                    _all_successful_readonly = bool(_eff_succ_tools) and all(
                        self._tool_is_safe_readonly(_t) for _t in _eff_succ_tools
                    )
                    _mutation_expected = mission_expects_mutation(original_query)
                    _readonly_exoneration = _all_successful_readonly and not _mutation_expected
                    # Exonération « vraie action hors-ledger » (spotify_play, etc.).
                    _real_action_done = any(_t in _HC_TOOLS_ANY_ACTION for _t in _eff_succ_tools)

                    _led_tools = self.execution_ledger.successful_actions() or ["AUCUN"]
                    _lg_query = ledger_final_guard_query(
                        claims_action=_claims_action,
                        runtime_claim=_runtime_claim_for_final,
                        has_any_mutation=self.execution_ledger.has_any_mutation(),
                        readonly_exoneration=_readonly_exoneration,
                        real_action_done=_real_action_done,
                        original_query=original_query,
                        led_tools=_led_tools,
                    )
                    if _lg_query is not None:
                        self._ledger_final_guard_used = True
                        _ledger_guard_triggered = True
                        logger.warning(
                            "[LEDGER GUARD] FINAL prétend avoir agi mais aucune mutation dans le ledger "
                            "(outils réussis: {}) — retry",
                            _led_tools,
                        )
                        self.history.pop()
                        query = _lg_query
                        _finish_iteration(status="ok", summary="ledger_final_guard_blocked")
                if _ledger_guard_triggered:
                    continue

                # ── Heuristique H2 : mutations présentes mais hors famille attendue ──
                # Exemple : intent="discord" mais seules des mutations "write_file" existent.
                if (not getattr(self, '_ledger_final_guard_used', False)
                        and not _ledger_guard_triggered
                        and _claims_action
                        and not _runtime_claim_for_final
                        and self.execution_ledger.has_any_mutation()):
                    _ss_guard = self._structured_state
                    _guard_intent = _ss_guard.last_intent if _ss_guard else None
                    _expected_family = _LEDGER_INTENT_FAMILIES.get(_guard_intent, frozenset())
                    _has_mut_in_family = (
                        self.execution_ledger.has_mutation_in_family(_expected_family)
                        if _expected_family else False
                    )
                    _led_tools = self.execution_ledger.successful_actions() or ["AUCUN"]
                    _h2_query = ledger_h2_guard_query(
                        claims_action=_claims_action,
                        runtime_claim=_runtime_claim_for_final,
                        has_any_mutation=True,
                        expected_family_nonempty=bool(_expected_family),
                        has_mutation_in_expected_family=_has_mut_in_family,
                        original_query=original_query,
                        guard_intent=_guard_intent,
                        led_tools=_led_tools,
                    )
                    if _h2_query is not None:
                        self._ledger_final_guard_used = True
                        logger.warning(
                            "[LEDGER GUARD H2] Mutations existent mais hors famille '{}' — retry",
                            _guard_intent,
                        )
                        self.history.pop()
                        query = _h2_query
                        _finish_iteration(status="ok", summary=f"ledger_guard_h2_wrong_family_{_guard_intent}")
                        continue

                # ── Heuristique H3 : cible explicite mentionnée mais aucune mutation pour elle ──
                # Repair léger fire-once (flag propre _ledger_h3_guard_used, message ⚠️).
                if (not getattr(self, '_ledger_h3_guard_used', False)
                        and not getattr(self, '_ledger_final_guard_used', False)
                        and _claims_action
                        and not _runtime_claim_for_final
                        and self.execution_ledger.has_any_mutation()):
                    _target_hint_h3 = extract_h3_target_hint(original_query)
                    _has_mut_for_target = (
                        self.execution_ledger.has_mutation_for_target_hint(_target_hint_h3)
                        if _target_hint_h3 else False
                    )
                    _led_tools_h3 = self.execution_ledger.successful_actions() or ["AUCUN"]
                    _h3_query = ledger_h3_guard_query(
                        claims_action=_claims_action,
                        runtime_claim=_runtime_claim_for_final,
                        has_any_mutation=True,
                        target_hint=_target_hint_h3,
                        has_mutation_for_target=_has_mut_for_target,
                        original_query=original_query,
                        led_tools=_led_tools_h3,
                    )
                    if _h3_query is not None:
                        self._ledger_h3_guard_used = True
                        logger.warning(
                            "[LEDGER GUARD H3] Cible '{}' mentionnée mais aucune mutation pour cette cible"
                            " — repair léger (outils: {})",
                            _target_hint_h3,
                            _led_tools_h3,
                        )
                        self.history.pop()
                        query = _h3_query
                        _finish_iteration(status="ok", summary=f"ledger_guard_h3_target_{_target_hint_h3}")
                        continue

                answer = action.answer or ""
                finish_reason = self._last_llm_meta.get("finish_reason")
                self._run_meta["agent_final_finish_reason"] = finish_reason

                # ── Chemin direct post-delegate_task ✅ : on saute tous les repairs ──
                # L'instruction injectée était explicite → la réponse est fiable, on ne re-sonde pas.
                # Guard : si des tâches de vérification restent non résolues dans le plan,
                # ne pas bypasser — le chemin FINAL normal les reflétera comme ⏭️.
                if self._after_delegate_success:
                    self._after_delegate_success = False  # consommé dans tous les cas
                    _pending_verify = [
                        t for t in self._task_plan
                        if not t.completed and is_verify_task(t.description.lower())
                    ]
                    _pending_business = self._pending_delegate_success_business_tasks()
                    if not _pending_verify and not _pending_business:
                        # Cas nominal : aucune verify-task pendante → bypass autorisé
                        _finish_iteration(status="ok", summary="delegate_task_final_direct")
                        message = answer if answer.strip() else self._delegate_success_fallback_message()
                        # LOT Z9b (run « décision voiture ») — quand le FINAL du lead
                        # est VIDE, on rend le rapport du sous-agent à sa place. Ça
                        # sauve le run (sans ça l'utilisateur ne reçoit rien), mais le
                        # résultat est indiscernable d'une vraie conclusion alors que
                        # ses chemins sont relatifs au dossier du sous-agent. On pose
                        # le fait ; la bannière est ajoutée à la clôture (runner.py).
                        if not answer.strip():
                            logger.info(
                                "[Z9b] FINAL vide → bilan = rapport du sous-agent "
                                "(voie H7). task={}", self.task_id or "-",
                            )
                            try:
                                _orch_z9b = getattr(
                                    getattr(self, "core", None), "task_orchestrator", None
                                )
                                if _orch_z9b is not None and self.task_id:
                                    _orch_z9b.set_task_metadata(
                                        self.task_id, final_from_worker_report=True
                                    )
                            except Exception as _exc_z9b:
                                logger.debug("[Z9b] marqueur non posé: {}", _exc_z9b)
                        self._mark_task_done("delegate_task_final_direct")
                        return message
                    # Verify-tasks non prouvées : traitement FINAL normal ci-dessous
                    logger.info(
                        "[delegate] Bypass annulé: {} verify-task(s) non résolue(s) "
                        "→ traitement FINAL normal (plan reflétera l'état réel)",
                        len(_pending_verify),
                    )
                    if _pending_business:
                        logger.info(
                            "[delegate] Taches metier encore ouvertes apres CodeAgent: {}",
                            [t.description for t in _pending_business[:5]],
                        )

                # ── Guard anti-thought-leak : le LLM a mis sa réflexion dans ACTION_INPUT au lieu de la réponse ──
                # Cela arrive quand ACTION_INPUT est vide → fallback sur thought_content (ligne 1881)
                # NOTE: Grok met souvent la vraie réponse dans THOUGHT avec ACTION_INPUT vide.
                # On ne doit PAS considérer ça comme un leak si le contenu ne ressemble pas à
                # de la réflexion interne (sinon on gaspille des itérations en re-prompting).
                _answer_lower = (answer or "").lower().lstrip()
                _is_reasoning_prefix = any(_answer_lower.startswith(p) for p in _INTERNAL_PREFIXES)
                # V2.1 fix prod (rev 2) : détection d'une réponse "intention" — le LLM
                # promet de répondre mais ne le fait pas. Doit être détectée comme leak.
                _answer_is_intention = (
                    bool(answer)
                    and len(answer.strip()) >= 20
                    and _looks_like_intention(answer)
                )
                _thought_leaked = (
                    # Cas 1 : réponse non vide mais commence par un préfixe de réflexion interne
                    (bool(answer) and _is_reasoning_prefix)
                    or _answer_is_intention
                    or (
                        # Cas 2 : answer == thought ET contient des marqueurs de réflexion interne
                        bool(answer)
                        and bool(thought.content)
                        and answer.strip() == thought.content.strip()
                        and any(k in _answer_lower for k in (
                            "l'utilisateur", "je dois ", "je vais ", "il faut que je",
                            "the user ", "i need to", "i should ",
                        ))
                    )
                    or (
                        # Cas 3 : ACTION: FINAL sans ACTION_INPUT (answer vide/whitespace) + thought présent
                        # → le modèle a déclaré FINAL mais n'a rien écrit pour l'utilisateur
                        not (answer or "").strip()
                        and bool(thought.content)
                        and action.action_type == ActionType.FINAL_ANSWER
                    )
                )
                # ── LOT Z29 phase 0 — RENDRE LE REJET OBSERVABLE ────────────────
                # Mesuré : 71 runs sur 478 ont eu besoin d'une réparation, et
                # 36 (51 %) ont fini `incomplete`. Impossible d'arbitrer un seul
                # de ces rejets après coup : le log montre la PENSÉE, jamais le
                # texte refusé. Le garde calcule un verdict sur un texte… puis
                # jette le texte. On le journalise (aucun changement de décision).
                if _thought_leaked:
                    _z29_cas = (
                        "prefixe_reflexion" if (bool(answer) and _is_reasoning_prefix)
                        else "intention" if _answer_is_intention
                        else "answer_egale_thought" if (
                            answer and thought.content
                            and answer.strip() == thought.content.strip()
                        )
                        else "final_sans_contenu"
                    )
                    try:
                        self._run_meta["thought_leak_case"] = _z29_cas
                        self._run_meta["thought_leak_len"] = len((answer or "").strip())
                    except Exception:
                        pass
                    logger.warning(
                        "[Z29] THOUGHT leak — cas={} len={} texte={!r}",
                        _z29_cas, len((answer or "").strip()),
                        (answer or "").strip()[:300],
                    )

                # LOT Z29 phase 3 — PLAFOND DUR à 2 demandes. Mesuré sur le
                # corpus : 71 runs réparés, 36 finis `incomplete` → une
                # réparation sur DEUX échoue. Redemander une 3ᵉ puis une 4ᵉ fois
                # ne fait pas monter la chance, ça consomme le budget avant le
                # repli déterministe (strip / synthèse depuis les preuves) qui,
                # lui, produit toujours quelque chose. Run « Papier Cousu » :
                # 3 leaks + 1 troncature = mission perdue avec le site fini.
                #
                # LOT Z31 — le calcul P5 qui vivait ici (« thought_leak_risk
                # élevé ⇒ jusqu'à 4 tirs ») accordait PLUS de tentatives aux
                # modèles qui fuient le plus. La mesure dit l'inverse : le taux
                # d'échec ne dépend pas du modèle, il dépend du fait qu'on
                # redemande. Le plafond le rendait de toute façon MORT — ses
                # quatre branches (4/3/2/2) donnaient toutes 2 après `min`.
                # Retiré plutôt que laissé en place : un calcul inerte fait
                # croire à un réglage qui n'existe plus.
                # ⚠️ C'était son SEUL consommateur : `thought_leak_risk` reste
                # déclaré sur les profils de modèle mais n'est plus lu nulle
                # part. Lui redonner un rôle demanderait une mesure PAR MODÈLE
                # que le corpus ne permet pas aujourd'hui.
                _max_tleak = 2
                # ── AUTO-CLEAN: Cas 1 (préfixe de réflexion interne) ──
                # Au lieu de forcer une reformulation coûteuse (1-3 iter perdues),
                # on tente de nettoyer le texte en supprimant les phrases internes
                # du début pour extraire la réponse utile directement.
                if _thought_leaked and _is_reasoning_prefix and answer and len(answer) > 100:
                    _cleaned_answer = self._strip_thought_leak_prefix(answer)
                    if _cleaned_answer and len(_cleaned_answer) >= 50:
                        logger.info(
                            "🔧 THOUGHT leak auto-nettoyé: {} chars → {} chars (économise une reformulation)",
                            len(answer), len(_cleaned_answer),
                        )
                        action = Action(
                            action_type=ActionType.FINAL_ANSWER,
                            answer=_cleaned_answer,
                            tool_name=action.tool_name,
                            tool_args=action.tool_args,
                        )
                        answer = _cleaned_answer
                        _thought_leaked = False  # cleaned, no need to repair

                # V2.1 fix prod 2026-05-19 (révision après test prod) : si toutes les tâches
                # métier sont déjà completed et que le LLM produit une réponse non vide
                # mais préfixée d'une réflexion interne ("based on the…", "let me now…"),
                # on accepte le strip au lieu de relancer une reformulation coûteuse.
                #
                # ATTENTION : on n'utilise PAS le THOUGHT comme réponse de fallback —
                # un THOUGHT contenant des INTENTIONS ("je vais livrer", "je dois synthétiser")
                # n'est PAS un livrable. Si la réponse est vide ou courte, on laisse les
                # repairs historiques (reformulation) reprendre la main avec la dernière
                # observation outil comme contexte.
                if (
                    _thought_leaked
                    and _plan_business_complete
                    and self._thought_leak_repairs == 0
                    and answer
                    and len(answer) >= 60
                ):
                    _stripped = self._strip_thought_leak_prefix(answer)
                    if _stripped and len(_stripped) >= 60 and not _looks_like_intention(_stripped):
                        logger.info(
                            "[PLAN GUARD] FINAL accepté après strip : tâches métier complètes "
                            "({} chars → {} chars, livrable détecté).",
                            len(answer), len(_stripped),
                        )
                        action = Action(
                            action_type=ActionType.FINAL_ANSWER,
                            answer=_stripped,
                            tool_name=action.tool_name,
                            tool_args=action.tool_args,
                        )
                        answer = _stripped
                        _thought_leaked = False
                    else:
                        logger.info(
                            "[PLAN GUARD] strip refusé (livrable non détecté) → repair standard."
                        )

                if _thought_leaked and self._thought_leak_repairs < _max_tleak:
                    self._thought_leak_repairs += 1
                    logger.warning(
                        f"⚠️ THOUGHT leaké comme réponse finale (tentative {self._thought_leak_repairs}/{_max_tleak}) - reformulation demandée"
                    )
                    # Conserver l'analyse faite dans ce thought pour ne pas la perdre.
                    #
                    # LOT Z29 phase 1 — SAUF si cette pensée est elle-même une
                    # intention. Run « Papier Cousu » : la pensée valait
                    # « Le site est complet et vérifié, je livre le résultat final
                    # avec les détails concrets. » — 84 caractères, donc AU-DESSUS
                    # du seuil de 80. Elle était réinjectée avec la consigne
                    # « réutilise-la », juste avant de reprocher au modèle d'avoir
                    # écrit une intention. Il obéissait à la première consigne : la
                    # MÊME phrase revient mot pour mot à 18:04:36, 18:04:58, 18:12:55.
                    # Le seuil devient un test de NATURE, pas de longueur.
                    _leaked_analysis = ""
                    _th_txt = (thought.content or "").strip()
                    if _th_txt and len(_th_txt) > 80 and not _looks_like_intention(_th_txt):
                        _thought_excerpt = _th_txt[:600]
                        _leaked_analysis = (
                            f"\nAnalyse déjà effectuée (réutilise-la, ne refais pas les mêmes lectures) :\n"
                            f"{_thought_excerpt}{'...' if len(thought.content.strip()) > 600 else ''}\n"
                        )
                    # LOT Z29 phase 2 — les faits du RUN ENTIER, en tête. La
                    # dernière observation seule est un contexte trop étroit :
                    # au run « Papier Cousu » c'était `read_files_batch` sur le
                    # README, alors que le modèle devait conclure sur un site de
                    # 6 fichiers vérifié au navigateur. Le ledger, lui, n'est
                    # jamais tronqué (même source que Z22).
                    try:
                        _z29_faits = self.execution_ledger.summary()
                    except Exception:
                        _z29_faits = ""
                    if _z29_faits:
                        _leaked_analysis += (
                            "\nFAITS ÉTABLIS DANS CE RUN (journal d'exécution — "
                            "appuie ta réponse dessus) :\n"
                            f"{_z29_faits}\n"
                        )
                    # V2.1 fix prod (rev 2) : injecter la DERNIÈRE observation outil dans le
                    # prompt de reformulation pour que le LLM ait les données concrètes sous
                    # les yeux. Évite qu'il refasse une nouvelle intention vide.
                    _last_obs_block = ""
                    _last_tool_name = ""
                    for _h in reversed(self.history):
                        if _h.observation and _h.observation.content and _h.action:
                            _last_tool_name = getattr(_h.action, "tool_name", "") or ""
                            _last_obs_block = _h.observation.content.strip()[:1500]
                            break
                    if _last_obs_block:
                        _leaked_analysis += (
                            f"\nDernière observation outil ({_last_tool_name or '?'}) "
                            f"— RÉUTILISE CES CHIFFRES/CITATIONS dans ta réponse :\n"
                            f"---\n{_last_obs_block}\n---\n"
                        )
                    self.history.pop()
                    query = (
                        f"Requête originale: {original_query}\n"
                        f"{_leaked_analysis}\n"
                        "⚠️ Ta dernière réponse était une INTENTION (\"je vais livrer\", \"je dois synthétiser\"), "
                        "PAS un livrable. L'utilisateur attend les données concrètes.\n\n"
                        "Maintenant écris ta RÉPONSE DIRECTE à l'utilisateur dans ACTION_INPUT.\n"
                        "Elle DOIT contenir :\n"
                        "- les chiffres/résultats des outils déjà exécutés\n"
                        "- les citations de source (chemins, IDs, MD5 si data.gouv)\n"
                        "- un résumé clair pour l'utilisateur\n\n"
                        + _LUMENA_TONE_REPAIR + "\n\n"
                        "Format :\n"
                        "THOUGHT: (1 ligne max)\n"
                        "ACTION: FINAL\n"
                        "ACTION_INPUT: [le livrable complet avec les chiffres réels, dans ta voix]"
                    )
                    _finish_iteration(status="ok", summary="thought_leaked_repair")
                    continue

                elif _thought_leaked:
                    # Repairs épuisés — tenter de nettoyer le THOUGHT prefix au lieu
                    # de retourner le raisonnement interne brut à l'utilisateur.
                    _stripped = self._strip_thought_leak_prefix(answer) if answer else None
                    _stripped_is_usable = (
                        _stripped
                        and len(_stripped) >= 20
                        and not _looks_like_intention(_stripped)
                    )
                    if _stripped_is_usable:
                        logger.warning(
                            "⚠️ THOUGHT leak non résolu après {}/{} tentatives — strip forcé ({} chars)",
                            _max_tleak, _max_tleak, len(answer) - len(_stripped),
                        )
                        action = Action(
                            action_type=ActionType.FINAL_ANSWER,
                            answer=_stripped,
                            tool_name=action.tool_name,
                            tool_args=action.tool_args,
                        )
                        answer = _stripped
                    else:
                        # V2.3 fix prod 2026-05-19 : strip inutilisable et LLM bloqué
                        # sur des intentions → utiliser la dernière observation outil
                        # tabulaire comme fallback FINAL, plutôt que retourner du vide
                        # ou de l'intention à l'utilisateur.
                        _fallback = None
                        _fallback_tool = ""
                        if self._is_mission_run:
                            _mission_evidence = []
                            for _h in self.history:
                                if _h.observation and _h.observation.content and _h.action:
                                    _mission_evidence.append((
                                        getattr(_h.action, "tool_name", "") or "",
                                        _h.observation.content,
                                        bool(getattr(_h.observation, "success", False)),
                                    ))
                            _fallback = _synthesize_mission_response_from_evidence(
                                _mission_evidence
                            )
                            if _fallback:
                                _fallback_tool = "mission_evidence"
                        if not _fallback:
                            for _h in reversed(self.history):
                                if _h.observation and _h.observation.content and _h.action:
                                    _fallback_tool = getattr(_h.action, "tool_name", "") or ""
                                    _candidate = _synthesize_response_from_observation(
                                        _h.observation.content, _fallback_tool, original_query,
                                    )
                                    if _candidate:
                                        _fallback = _candidate
                                        break
                        if _fallback:
                            logger.warning(
                                "[REACT FALLBACK] FINAL synthétisé depuis dernière observation "
                                "outil `{}` ({} chars) — le LLM n'a produit que des intentions.",
                                _fallback_tool, len(_fallback),
                            )
                            action = Action(
                                action_type=ActionType.FINAL_ANSWER,
                                answer=_fallback,
                                tool_name=action.tool_name,
                                tool_args=action.tool_args,
                            )
                            answer = _fallback
                        elif _stripped and len(_stripped) >= 20:
                            # Pas de fallback observation utile : strip même si imparfait
                            logger.warning(
                                "⚠️ THOUGHT leak non résolu après {}/{} tentatives — strip forcé ({} chars)",
                                _max_tleak, _max_tleak, len(answer or "") - len(_stripped),
                            )
                            action = Action(
                                action_type=ActionType.FINAL_ANSWER,
                                answer=_stripped,
                                tool_name=action.tool_name,
                                tool_args=action.tool_args,
                            )
                            answer = _stripped

                # ── VERBALIZATION REDIRECT ──────────────────────────────────
                # Détecte quand le LLM verbalise un plan/raisonnement dans sa réponse
                # finale au lieu d'exécuter un tool call. Marqueurs : **THOUGHT:**,
                # **PLAN:**, "je délègue", "je vais déléguer" sans tool call effectif.
                # Au lieu de tronquer ou reformuler, on redirige : le texte est conservé
                # comme message assistant et on relance un tour avec un nudge pour que
                # le LLM appelle le tool approprié.
                _MAX_VERB_REDIRECTS = 2
                if answer and self._verbalization_redirects < _MAX_VERB_REDIRECTS:
                    _answer_for_check = (answer or "").strip()
                    _al = _answer_for_check.lower()
                    _has_internal_markers = (
                        "**thought:**" in _al
                        or "**plan:**" in _al
                        or "**thought :**" in _al
                        or "**plan :**" in _al
                    )
                    _has_verbalized_delegation = bool(
                        any(p in _al for p in (
                            "je délègue", "je vais déléguer", "je délègue au",
                            "i will delegate", "i'll delegate", "delegating to",
                        ))
                        and not any(
                            h.action and h.action.action_type not in (ActionType.FINAL_ANSWER,)
                            and h.action.tool_name and "delegate" in (h.action.tool_name or "").lower()
                            for h in self.history[-3:]
                        )
                    )
                    if _has_internal_markers or _has_verbalized_delegation:
                        self._verbalization_redirects += 1
                        logger.warning(
                            "🔄 VERBALIZATION REDIRECT {}/{}: réponse finale contient un plan/raisonnement "
                            "sans tool call — redirection vers un nouveau tour",
                            self._verbalization_redirects, _MAX_VERB_REDIRECTS,
                        )
                        # Conserver le raisonnement du LLM dans l'historique
                        self.history.pop()
                        query = (
                            f"Requête originale: {original_query}\n\n"
                            f"Ton analyse (réutilise-la) :\n{_answer_for_check[:800]}\n\n"
                            "⚠️ Tu as verbalisé ton plan au lieu de l'exécuter.\n"
                            "N'ÉCRIS PAS ce que tu vas faire — FAIS-LE.\n"
                            "Appelle le tool approprié (delegate_task, web_search, etc.) "
                            "via ACTION/ACTION_INPUT MAINTENANT."
                        )
                        _finish_iteration(status="ok", summary="verbalization_redirect")
                        continue

                # Si la réponse est vide ou juste des points, utiliser la dernière observation
                if not answer or answer.strip() in ["", "...", "......", "Je n'ai pas de réponse", "Je n'ai pas de réponse."]:
                    # Chercher la dernière observation de recherche
                    last_observation = None
                    for h in reversed(self.history):
                        if h.observation and ("Recherche" in h.observation.content or "💰" in h.observation.content):
                            last_observation = h.observation.content
                            break
                    
                    if last_observation:
                        # Extraire les informations clés de l'observation
                        _finish_iteration(status="ok", summary="final_from_last_observation")
                        message = f"📊 Voici ce que j'ai trouvé :\n\n{last_observation[:3000]}"
                        self._mark_task_done("final_from_last_observation")
                        return message

                # Skip repair si stagnation déjà détectée — le FINAL est volontaire
                # V2.1 fix prod : skip aussi si plan business complete (toutes les tâches
                # métier done → ne pas boucler en repair pour gratter quelques chars).
                should_repair = _should_repair_incomplete_final(
                    stagnation_streak=_stagnation_streak,
                    plan_business_complete=_plan_business_complete,
                    document_free_grounded=_document_free_grounded_final,
                    looks_incomplete=self._looks_incomplete_final_answer(
                        answer, self._last_llm_meta,
                    ),
                )

                if should_repair:
                    if self._final_repair_attempts < self.max_final_repair_attempts:
                        self._final_repair_attempts += 1
                        self._run_meta["agent_repair_attempts"] = self._final_repair_attempts
                        # Sauvegarder la réponse originale pour rollback si le repair échoue
                        self._pre_repair_answer = answer
                        logger.warning(
                            "⚠️ FINAL potentiellement tronqué (finish_reason={}) - tentative de réparation {}/{}",
                            finish_reason,
                            self._final_repair_attempts,
                            self.max_final_repair_attempts,
                        )
                        query = (
                            f"Requête originale: {original_query}\n\n"
                            "⚠️ Ta dernière réponse FINAL semble incomplète. "
                            "Renvoie une réponse complète et cohérente. "
                            "Respecte STRICTEMENT le format THOUGHT/ACTION/ACTION_INPUT et utilise ACTION: FINAL."
                        )
                        _finish_iteration(status="ok", summary="final_repair_retry")
                        continue

                    self._run_meta["agent_output_incomplete"] = True
                    self._run_meta["agent_output_warning"] = (
                        f"final_answer_potentially_incomplete (finish_reason={finish_reason})"
                    )
                    self._run_meta["agent_repair_attempts"] = self._final_repair_attempts
                    # La réponse d'origine, gardée au repair, vaut infiniment
                    # mieux qu'un aveu d'échec : on ne sert la formule que si on
                    # n'a VRAIMENT rien.
                    _garde = (getattr(self, "_pre_repair_answer", None) or "").strip()
                    message = answer or _garde or "Je n'ai pas trouvé de réponse pertinente."
                    # I3 (run comparatif vectoriel) — un worker dont TOUS les
                    # fichiers assignés sont remplis a fait son travail : sa
                    # conclusion tronquée ne rend pas son livrable inexistant.
                    # `w_qdrant` était `failed` avec un rapport complet et exact,
                    # dont les données se retrouvaient dans le comparatif final.
                    # Le warning reste en meta ; seul l'ÉTAT devient fidèle.
                    if self._mission_worker_delivered():
                        logger.warning(
                            "[I3] FINAL tronqué (finish_reason={}) mais livrables "
                            "assignés TOUS remplis → worker non marqué failed. task={}",
                            finish_reason, self.task_id,
                        )
                        self._run_meta["agent_output_delivered_anyway"] = True
                        _finish_iteration(status="ok", summary="final_truncated_but_delivered")
                        # RF-8-FIX-1 — cette voie ne passe PAS par le goulot :
                        # sans ce verrou, la conclusion du worker est livree
                        # telle quelle, revendications comprises.
                        return self._truth_lock_mission_message(message, origine="I3")
                    # LOT Z28 — le LEAD aussi. I3 s'arrête aux workers (il exige
                    # des `allowed_files`, qu'un lead n'a jamais) ; run « Papier
                    # Cousu » : 6 fichiers livrés, 3 pages vues au navigateur,
                    # et `failed` parce que la CONCLUSION ne se formatait pas.
                    _z28_artefacts = self._mission_lead_delivered()
                    if _z28_artefacts:
                        logger.warning(
                            "[Z28] FINAL tronqué (finish_reason={}) mais livrable "
                            "PRÉSENT sur le disque ({}) → mission non marquée failed. "
                            "task={}",
                            finish_reason, ", ".join(_z28_artefacts), self.task_id,
                        )
                        self._run_meta["agent_output_delivered_anyway"] = True
                        self._run_meta["z28_lead_artifacts"] = list(_z28_artefacts)
                        _finish_iteration(
                            status="ok", summary="final_truncated_but_lead_delivered",
                        )
                        return self._truncated_but_delivered_answer(_z28_artefacts)
                    _finish_iteration(status="error", error=self._run_meta["agent_output_warning"])
                    self._mark_task_failed(self._run_meta["agent_output_warning"])
                    # RF-8-FIX-1 — meme voie hors goulot. On ajoute le verrou et
                    # RIEN d'autre : rerouter par `_stream_and_return_final`
                    # poserait `_mark_task_done` juste apres ce
                    # `_mark_task_failed`, et contredirait l'etat.
                    return self._truth_lock_mission_message(message, origine="Z28")

                self._run_meta["agent_repair_attempts"] = self._final_repair_attempts
                # ── LOT 2.10 — GATE PYTEST À RELANCE BORNÉE (run StockPilot) ───
                # Des tests existent (ledger/dossier mission/contrat) mais AUCUN
                # pytest n'a tourné dans ce run → UNE relance dirigée avant
                # d'accepter le FINAL. Au 2e passage sans test : clôture honnête
                # (truth-lock) — gate d'honnêteté, jamais de boucle infinie.
                # A5 (run FitLog) : compteur à 2 tirs — w_storage avait brûlé sa
                # relance UNIQUE sur un FINAL d'avant-travail (iter 2, zéro
                # mutation), son vrai FINAL menteur n'avait plus de filet.
                _gate_shots = getattr(self, "_pytest_gate_shots", 0)
                # 2.13.D — plafond élargi à 4 : les tirs 3-4 sont RÉSERVÉS à la
                # branche budget-aware (tests rouges qui progressent) ; la branche
                # « aucun test lancé » garde ses 2 tirs historiques.
                if (answer and self._is_mission_run
                        and _gate_shots < 4
                        and i < self.max_iterations - 2):
                    try:
                        _o_gate = self.execution_ledger.last_test_outcome()
                        if not (_o_gate or {}).get("is_test_cmd"):
                            _tests_where = self._mission_tests_present_for_gate()
                            if _tests_where and _gate_shots < 2:
                                self._pytest_gate_shots = _gate_shots + 1
                                self._pytest_gate_relaunched = True
                                self._iterations_without_progress = 0  # PG-1.c — tir accorde = strategie neuve, pas de stagnation
                                logger.warning(
                                    "[PYTEST GATE] FINAL sans aucun run de tests alors que des "
                                    "tests existent ({}) → relance dirigée {}/2. task={}",
                                    _tests_where, self._pytest_gate_shots, self.task_id)
                                self.history.pop()
                                query = (
                                    f"Requête originale: {original_query}\n\n"
                                    f"🧪 STOP — des tests existent ({_tests_where}) et tu n'as "
                                    "lancé AUCUN pytest dans ce run. AVANT de conclure : lance "
                                    "`python -m pytest` sur le dossier du livrable, corrige LE "
                                    "CODE par MUTATION si rouge (jamais les tests contractuels), "
                                    "PUIS fais ton FINAL. (Relances bornées : si tu conclus "
                                    "encore sans test, la clôture dira honnêtement « tests non "
                                    "exécutés ».)"
                                )
                                _finish_iteration(status="ok", summary="pytest_gate_relaunch")
                                continue
                        else:
                            # ── 2.13.D (run bibliapi) — PYTEST GATE budget-aware ──
                            # Le dernier pytest est ROUGE et le lead veut conclure
                            # alors que le budget est confortable (bibliapi : 4
                            # failed + ~24 min gâchées). Tir supplémentaire SEULEMENT
                            # si failed décroît (helper pur, plafond dur 4) ; sinon
                            # final honnête actuel (bannières truth-lock) inchangé.
                            _failed_now = (_o_gate or {}).get("failed")
                            _green_gate = bool((_o_gate or {}).get("green"))
                            if (not _green_gate
                                    and isinstance(_failed_now, (int, float))
                                    and _failed_now > 0):
                                from src.subagents.mission_budget import (
                                    mission_budget, pytest_gate_extra_shot_allowed,
                                )
                                _bud_213 = {}
                                try:
                                    if self._orchestrator_enabled():
                                        _bud_213 = mission_budget(
                                            self.task_orchestrator.get_task(self.task_id) or {}
                                        )
                                except Exception:
                                    _bud_213 = {}
                                if pytest_gate_extra_shot_allowed(
                                    shots=_gate_shots,
                                    failed_now=_failed_now,
                                    failed_prev=getattr(
                                        self, "_pytest_gate_failed_prev", None),
                                    remaining_s=_bud_213.get("remaining_s"),
                                    ratio_used=_bud_213.get("ratio_used"),
                                ):
                                    self._pytest_gate_shots = _gate_shots + 1
                                    self._pytest_gate_failed_prev = _failed_now
                                    self._pytest_gate_relaunched = True
                                    self._iterations_without_progress = 0  # PG-1.c — tir accorde = strategie neuve, pas de stagnation
                                    logger.warning(
                                        "[PYTEST GATE 2.13.D] FINAL avec {} failed et "
                                        "budget confortable → tir supplémentaire {}/4. "
                                        "task={}",
                                        _failed_now, self._pytest_gate_shots, self.task_id)
                                    self.history.pop()
                                    query = (
                                        f"Requête originale: {original_query}\n\n"
                                        f"🧪 STOP — le dernier pytest montre "
                                        f"{int(_failed_now)} failed et il te reste du budget "
                                        "confortable. Ne conclus pas sur du rouge : lis les "
                                        "erreurs du dernier run, corrige LE CODE par MUTATION "
                                        "(jamais les tests contractuels ; si un test contredit "
                                        "le CONTRAT, aligne-le sur le contrat), relance "
                                        "`python -m pytest` sur le dossier du livrable, PUIS "
                                        "conclus. Prends le temps de bien faire — zéro "
                                        "précipitation."
                                    )
                                    _finish_iteration(
                                        status="ok",
                                        summary="pytest_gate_budget_relaunch")
                                    continue
                    except Exception as _pg_exc:
                        logger.debug("[PYTEST GATE] skip: {}", _pg_exc)
                # ── LOT 2.4 (run MotDuJour) — JS GATE à relance bornée ─────────
                # pytest vert ne dit RIEN du JS (2 runs de suite : script.js
                # invalide puis CSS jamais chargé, avec 4/4 pytest verts). Un
                # TOP-LEAD de mission à livrable JS ne conclut pas sans UN
                # `node --check` réussi au ledger. 1 tir, jamais de boucle.
                _jg_shots = getattr(self, "_js_gate_shots", 0)
                if (answer and self._is_mission_run
                        and _jg_shots < 1
                        and i < self.max_iterations - 2
                        and not self._is_worker_run()  # H4 : périmètre OU parent
                        and not self.execution_ledger.has_js_syntax_check()):
                    try:
                        _js_where = self._mission_js_present_for_gate()
                        if _js_where:
                            self._js_gate_shots = _jg_shots + 1
                            self._iterations_without_progress = 0  # PG-1.c — tir accorde = strategie neuve, pas de stagnation
                            logger.warning(
                                "[JS GATE] FINAL sans node --check alors qu'un "
                                "livrable JS existe ({}) → relance dirigée 1/1. task={}",
                                _js_where, self.task_id)
                            self.history.pop()
                            query = (
                                f"Requête originale: {original_query}\n\n"
                                f"🟨 STOP — livrable JS ({_js_where}) et tu n'as vérifié "
                                "AUCUNE syntaxe JS dans ce run. AVANT de conclure : lance "
                                "`node --check <chemin du .js>` sur chaque fichier JS du "
                                "livrable, corrige par MUTATION si rouge, PUIS ton FINAL. "
                                "(Relance bornée : un JS invalide livré = interface morte "
                                "même avec pytest vert.)"
                            )
                            _finish_iteration(status="ok", summary="js_gate_relaunch")
                            continue
                    except Exception as _jg_exc:
                        logger.debug("[JS GATE] skip: {}", _jg_exc)
                # ── LOT D (run FidéliBar) — BROWSER GATE à relance bornée ──────
                # Livrable WEB + intention/claim de vérif navigateur + AUCUNE
                # action browser_* réussie au ledger → UNE relance dirigée avant
                # d'accepter le FINAL (sers la preview puis browser_navigate + DOM).
                # 1 SEUL tir (le navigateur risque plus le rabbit-hole que pytest) ;
                # débordement rattrapé par D.1 (bannière « navigateur non prouvée »
                # au truth-lock). Jamais de boucle.
                _bg_shots = getattr(self, "_browser_gate_shots", 0)
                # LOT Z15 — `not _current_browser_proof()` fermait aussi CETTE
                # voie dès la première page ouverte. Les deux chemins de clôture
                # (FINAL LLM ici, sortie déterministe via
                # `_finalize_browser_gate_pending`) portaient la même sortie
                # anticipée : une page suffisait à dispenser du reste du site.
                # Le garde reste borné à 1 tir par `_bg_shots`.
                if (answer and self._is_mission_run
                        and _bg_shots < 1
                        and i < self.max_iterations - 2
                        and (not self._current_browser_proof()
                             or self._pages_never_opened_reason())):
                    try:
                        _web_where = self._mission_browser_verify_pending(answer, original_query)
                        if _web_where:
                            self._browser_gate_shots = _bg_shots + 1
                            self._browser_gate_relaunched = True
                            self._iterations_without_progress = 0  # PG-1.c — tir accorde = strategie neuve, pas de stagnation
                            logger.warning(
                                "[BROWSER GATE] FINAL sans action browser_* alors que la "
                                "mission web demande une vérif navigateur ({}) → relance "
                                "dirigée {}/1. task={}",
                                _web_where, self._browser_gate_shots, self.task_id)
                            self.history.pop()
                            if self._truth_lock_interaction_flag():
                                query = (
                                    f"Requête originale: {original_query}\n\n"
                                    f"🌐 STOP — livrable WEB interactif ({_web_where}) sans "
                                    "preuve stricte du changement demandé. Appelle MAINTENANT "
                                    "`browser_verify_local_project(project_path='<dossier du "
                                    "livrable>')`. Cet outil remplit tous les champs visibles, "
                                    "soumet l'action principale et exige un changement DOM "
                                    "observable. S'il échoue, corrige le code puis relance-le. "
                                    "Ne conclus pas sur une simple saisie, un clic ou une capture."
                                )
                            else:
                                query = (
                                    f"Requête originale: {original_query}\n\n"
                                    f"🌐 STOP — livrable WEB ({_web_where}) et tu n'as fait "
                                    "AUCUNE action navigateur (browser_*) dans ce run. AVANT de "
                                    "conclure : SERS l'app avec l'OUTIL "
                                    "serve_website(directory='<dossier du livrable>', port=8081), "
                                    "PUIS browser_navigate dessus et CONTRÔLE le "
                                    "DOM (le flux demandé), ENFIN ton FINAL. (Relance bornée : si "
                                    "tu conclus encore sans action navigateur, la clôture dira "
                                    "honnêtement « vérification navigateur non prouvée ».)"
                                )
                            _finish_iteration(status="ok", summary="browser_gate_relaunch")
                            continue
                    except Exception as _bg_exc:
                        logger.debug("[BROWSER GATE] skip: {}", _bg_exc)
                # M108 (run FocusForge): opening the page is not proof of the
                # requested interaction. Give the lead a small bounded action
                # budget to perform the flow and observe a DOM/JS state change.
                _ig_shots = getattr(self, "_interaction_gate_shots", 0)
                if (answer and self._is_mission_run
                        and _ig_shots < _MAX_INTERACTION_GATE_SHOTS
                        and i < self.max_iterations - 2):
                    try:
                        _interaction_where = self._finalize_interaction_gate_pending(
                            answer, original_query
                        )
                        if _interaction_where:
                            self._interaction_gate_shots = _ig_shots + 1
                            self._iterations_without_progress = 0
                            logger.warning(
                                "[INTERACTION GATE] FINAL after browser open ({}) "
                                "-> directed retry {}/{}. task={}",
                                _interaction_where,
                                self._interaction_gate_shots,
                                _MAX_INTERACTION_GATE_SHOTS,
                                self.task_id,
                            )
                            self.history.pop()
                            query = (
                                f"Requete originale: {original_query}\n\n"
                                "STOP -- la page est ouverte, mais l'interaction "
                                "demandee n'est pas encore prouvee. Execute maintenant "
                                "le parcours exact demande (saisies, selection, clic), "
                                "puis relis l'etat avec `browser_dom_state` ou "
                                "`browser_evaluate` et constate le changement avant/apres. "
                                "Ne conclus pas sur la seule navigation ou une capture."
                            )
                            _finish_iteration(
                                status="ok", summary="interaction_gate_relaunch"
                            )
                            continue
                    except Exception as _ig_exc:
                        logger.debug("[INTERACTION GATE] skip: {}", _ig_exc)
                # ── VERROU DE VÉRITÉ FINALE (mission) ──────────────────────────
                # Le FINAL du lead devient le livrable stocké (re-livré tel quel
                # par mission_result). On interdit toute affirmation « tests verts /
                # certifié » SANS preuve verte au ledger : réécriture honnête
                # (jamais fabriquer du vert). Cf. run bibliotech 2026-07-01.
                if answer and self._is_mission_run:
                    try:
                        _answer_locked, _lock_info = apply_mission_truth_lock(
                            answer,
                            has_green_test=self._current_green_test_proof(),
                            last_test_outcome=self.execution_ledger.last_test_outcome(),
                            has_browser_proof=self._current_browser_proof(),
                            # 2.8.2 (run TriboBlog) — HARMONISATION : ces 2 params
                            # manquaient à CE site → overclaim_delivery (défaut
                            # has_any_mutation=True) et note_tests_not_run étaient
                            # MORTS ici. TriboBlog est sorti par ce site → la
                            # fabrication « fichiers créés » (zéro écriture) a filé.
                            tests_present_not_run=self._tests_present_but_not_run(),
                            has_any_mutation=self.execution_ledger.has_any_mutation(),
                            # LOT E (run FidéliBar) — verrou « publié » sans preuve.
                            has_published=self.execution_ledger.has_published(),
                            # LOT 2.11.E — disk-grounded (run StatsNotes).
                            project_root=Path(__file__).resolve().parents[2],
                            # M1 (run RévizIA) — policy navigateur dure (top-lead web).
                            web_deliverable=self._truth_lock_web_flag(),
                            file_deliverables_expected=self._mission_expects_file_deliverables(),  # H8
                            unpublished_writes=self._mission_unpublished_writes(),  # Z24
                            has_server_started=self._server_started_proof(),  # LOT 2.3
                            browser_content_seen=self._browser_content_seen(),  # 2.7.4
                            interaction_proven=self._truth_lock_interaction_proven(),
                            interaction_required=self._truth_lock_interaction_flag(),
                            objective_is_game=self._truth_lock_game_flag(),  # 2.13.A
                            browser_runtime_failed=self._browser_runtime_failed_for_truth_lock(),  # M100.4
                        )
                        self._note_truth_lock_outcome(_lock_info)  # F1.b
                        if _lock_info.get("changed"):
                            # M1 (revue) — log GÉNÉRIQUE : le verrou couvre désormais
                            # tests/navigateur/livraison/publication ; le motif exact
                            # vient de _lock_info, plus d'un libellé figé « tests verts ».
                            _lt = self.execution_ledger.last_test_outcome() or {}
                            logger.warning(
                                "[MISSION TRUTH-LOCK] FINAL rétrogradé honnêtement — "
                                "détails={} (dernier pytest: passed={} failed={} "
                                "errors={}) task={}",
                                {k: v for k, v in _lock_info.items() if v and k != "changed"},
                                _lt.get("passed"), _lt.get("failed"), _lt.get("errors"),
                                self.task_id,
                            )
                            answer = _answer_locked
                            self._run_meta["mission_truth_lock_applied"] = True
                    except Exception as _tl_exc:
                        logger.debug("[MISSION TRUTH-LOCK] skip: {}", _tl_exc)
                _finish_iteration(status="ok", summary="final_answer_ready")
                # F1.b — hors mission : formule historique. En mission : jamais une
                # politesse à la place d'un livrable (cf. _empty_final_fallback).
                _garde = (getattr(self, "_pre_repair_answer", None) or "").strip()
                message = answer or _garde or self._empty_final_fallback()
                return self._stream_and_return_final(message)
            
            # 5. Sinon, exécuter l'outil
            if action.action_type == ActionType.TOOL_CALL and action.tool_name:
                _last_obs_for_browser = ""
                if self.history and self.history[-1].observation:
                    _last_obs_for_browser = self.history[-1].observation.content or ""
                _browser_rewrite = _browser_rewrite_human_navigation_action(
                    action.tool_name,
                    action.tool_args or {},
                    query=query,
                    last_surface=self._last_browser_surface or "",
                    last_observation=_last_obs_for_browser,
                )
                if _browser_rewrite is not None:
                    _new_tool, _new_args, _rewrite_reason = _browser_rewrite
                    logger.info("[BROWSER HUMAN] {} → {}", action.tool_name, _new_tool)
                    logger.debug("[BROWSER HUMAN] {}", _rewrite_reason)
                    action.tool_name = _new_tool
                    action.tool_args = _new_args

                _text_entry_rewrite = _browser_rewrite_text_entry_action(
                    action.tool_name,
                    action.tool_args or {},
                    last_observation=_last_obs_for_browser,
                )
                if _text_entry_rewrite is not None:
                    _new_tool, _new_args, _rewrite_reason = _text_entry_rewrite
                    logger.info("[BROWSER WRITE] {} → {}", action.tool_name, _new_tool)
                    logger.debug("[BROWSER WRITE] {}", _rewrite_reason)
                    action.tool_name = _new_tool
                    action.tool_args = _new_args

                _system_typing_rewrite = _browser_rewrite_system_typing_action(
                    action.tool_name,
                    action.tool_args or {},
                    last_observation=_last_obs_for_browser,
                    last_textbox_index=str(getattr(self, "_browser_last_textbox_index", "") or ""),
                )
                if _system_typing_rewrite is not None:
                    _new_tool, _new_args, _rewrite_reason = _system_typing_rewrite
                    logger.info("[BROWSER WRITE] {} → {}", action.tool_name, _new_tool)
                    logger.debug("[BROWSER WRITE] {}", _rewrite_reason)
                    action.tool_name = _new_tool
                    action.tool_args = _new_args

                _index_selector_rewrite = _browser_rewrite_index_like_selector_action(
                    action.tool_name,
                    action.tool_args or {},
                )
                if _index_selector_rewrite is not None:
                    _new_tool, _new_args, _rewrite_reason = _index_selector_rewrite
                    logger.info("[BROWSER INDEX] {} → {}", action.tool_name, _new_tool)
                    logger.debug("[BROWSER INDEX] {}", _rewrite_reason)
                    action.tool_name = _new_tool
                    action.tool_args = _new_args

                _selector_guess_rewrite = _browser_rewrite_selector_guess_to_index_action(
                    action.tool_name,
                    action.tool_args or {},
                    last_surface=self._last_browser_surface or "",
                    last_observation=_last_obs_for_browser,
                )
                if _selector_guess_rewrite is not None:
                    _new_tool, _new_args, _rewrite_reason = _selector_guess_rewrite
                    logger.info("[BROWSER INDEX] {} → {}", action.tool_name, _new_tool)
                    logger.debug("[BROWSER INDEX] {}", _rewrite_reason)
                    action.tool_name = _new_tool
                    action.tool_args = _new_args

                # P4 — Réécriture browser_type_index → browser_click_index
                # pour les contrôles non-texte (radio, checkbox, button, switch…)
                _ctrl_rewrite = _browser_rewrite_type_to_click_for_ctrl(
                    action.tool_name,
                    action.tool_args or {},
                    last_observation=_last_obs_for_browser,
                )
                if _ctrl_rewrite is not None:
                    _new_tool, _new_args, _rewrite_reason = _ctrl_rewrite
                    logger.info("[BROWSER CTRL] {} → {}", action.tool_name, _new_tool)
                    logger.debug("[BROWSER CTRL] {}", _rewrite_reason)
                    action.tool_name = _new_tool
                    action.tool_args = _new_args

                # Notifier le step_callback (ex: voix) avant l'exécution de l'outil
                if self.step_callback:
                    try:
                        self.step_callback(action.tool_name, action.tool_args or {})
                    except Exception as e:
                        logger.debug(f"Step callback: {e}")
                # Propager le budget temps restant et le task_id au HandlerContext
                if hasattr(self, '_loop_start_time') and hasattr(self.tools, '_v2_context'):
                    from time import perf_counter as _pc
                    _elapsed = _pc() - self._loop_start_time
                    _total = float(self.timeout_seconds or 600) + getattr(self, '_tool_time_total', 0.0)
                    self.tools._v2_context.budget_seconds = max(0.0, _total - _elapsed)
                    # Cancel canal : propager le parent task_id pour delegate_task
                    if self.task_id:
                        self.tools._v2_context.runtime_task_id = self.task_id
                    # Exemption sandbox mission : la boucle POSSÈDE la vérité (double-verrou
                    # task_id + kind=="mission"). tool_registry._policy_check l'utilise pour
                    # autoriser un worker de mission à écrire ses artefacts code dans workspace/.
                    self.tools._v2_context.is_mission_run = bool(self._is_mission_run)
                    # LOT 2.1 — scope workspace mission : dossier ISOLÉ partagé par les
                    # workers d'un lead (posé par delegate_and_wait dans la meta mission).
                    # Lu ici par tour (la meta du lead peut être posée EN COURS de run).
                    # Vide hors mission → résolution actuelle inchangée.
                    self.tools._v2_context.mission_workspace = self._mission_workspace_meta()
                    # LOT 2.3 — périmètre d'écriture du worker (fichiers assignés).
                    # Vide → aucune restriction (lead, worker sans liste, chat).
                    self.tools._v2_context.mission_allowed_files = self._mission_allowed_files_meta()
                    # Phase 0.6 : propager la demande utilisateur originale (verbatim)
                    # pour que delegate_task puisse la transmettre au sub-agent
                    # sans passer par la reformulation ReAct.
                    _orig_q = getattr(self, "_original_query", "")
                    if _orig_q:
                        self.tools._v2_context.original_user_query = _orig_q
                # Mesurer le temps outil pour exclure du timeout de raisonnement
                from .caller_context import REACT as _CALLER_REACT
                _tool_exec_start = perf_counter()
                # ── 2.13.B — GATE INTENTION CONTRAT (run miniblog) ──────────
                # Redirection dirigée AVANT exécution : l'observation de refus
                # suit le flux normal (historique, ledger success=False) → le
                # lead voit la consigne et retente par write_mission_contract.
                _studio_obs = None
                try:
                    _studio_obs = self._structured_document_tool_gate(
                        action.tool_name, action.tool_args or {},
                    )
                except Exception as _studio_exc:
                    logger.debug("[DOCUMENT STUDIO GATE] skip: {}", _studio_exc)
                _cg_obs = None
                try:
                    _cg_obs = self._contract_intent_gate(action.tool_name)
                except Exception as _cg_exc:
                    logger.debug("[CONTRACT GATE] skip: {}", _cg_exc)
                _wca_obs = None
                try:
                    _wca_obs = self._worker_codeagent_first_gate(action.tool_name, action.tool_args)
                except Exception as _wca_exc:
                    logger.debug("[CODEAGENT-FIRST] skip: {}", _wca_exc)
                # LOT O2 — l'utilisateur a demandé une mission à échéance : elle
                # doit être lancée, pas bricolée au fil de la conversation.
                _cmi_obs = None
                try:
                    _cmi_obs = self._chat_mission_intent_gate(action.tool_name)
                except Exception as _cmi_exc:
                    logger.debug("[CHAT MISSION GATE] skip: {}", _cmi_exc)
                # LOT P2b — ne pas réécrire en place un livrable déjà livré.
                _ovw_obs = None
                try:
                    _ovw_obs = self._mission_overwrite_gate(
                        action.tool_name, action.tool_args or {},
                    )
                except Exception as _ovw_exc:
                    logger.debug("[P2b] skip: {}", _ovw_exc)
                # LOT Z23 — l'inspection d'une preview jugee non prouvable est close.
                _lpu_obs = None
                try:
                    _lpu_obs = self._local_preview_unprovable_gate(action.tool_name)
                except Exception as _lpu_exc:
                    logger.debug("[Z23] skip: {}", _lpu_exc)
                if _cmi_obs is not None:
                    observation = _cmi_obs
                elif _lpu_obs is not None:
                    observation = _lpu_obs
                elif _ovw_obs is not None:
                    observation = _ovw_obs
                elif _studio_obs is not None:
                    observation = _studio_obs
                elif _cg_obs is not None:
                    observation = _cg_obs
                elif _wca_obs is not None:
                    observation = _wca_obs
                else:
                    observation = await self._execute_tool_with_cancel_guard(
                        action.tool_name,
                        action.tool_args,
                        caller=_CALLER_REACT,
                    )
                # Capture the complete catalog JSON before warnings or history
                # compaction alter the model-visible observation.
                ReActLoop._record_document_catalog_evidence(self, action, observation)
                ReActLoop._record_document_workflow_evidence(self, action, observation)
                _tool_exec_duration = perf_counter() - _tool_exec_start
                # ── Cancel post-outil : stopper avant de réinjecter l'observation ──
                # Si le parent a été annulé PENDANT l'outil (ex: delegate_task long),
                # on coupe ici pour ne pas injecter un résultat orphelin dans la boucle.
                if self._orchestrator_enabled():
                    try:
                        if self.task_orchestrator.is_cancel_requested(self.task_id):
                            logger.info("[ReAct] cancel détecté post-outil task={}", self.task_id)
                            raise SystemExit("task_orchestrator_cancel")
                    except SystemExit:
                        raise
                    except Exception:
                        pass
                # Repousser la deadline du temps passé dans l'outil
                # → seul le temps de raisonnement (LLM) compte pour le timeout
                if hasattr(self, '_timeout_deadline'):
                    self._timeout_deadline += _tool_exec_duration
                    self._tool_time_total = getattr(self, '_tool_time_total', 0.0) + _tool_exec_duration
                # ── P4: Budget par catégorie ──
                _tool_cat = getattr(self.tools, "_tool_modules", {}).get(action.tool_name, "unknown")
                self._category_iter_counts[_tool_cat] = self._category_iter_counts.get(_tool_cat, 0) + 1
                _CAT_ITER_LIMITS = {
                    "web": 8, "browser": 32, "memory": 5,
                    "security": 10, "network": 8,
                }
                _cat_limit = _CAT_ITER_LIMITS.get(_tool_cat, 0)
                if _cat_limit and self._category_iter_counts[_tool_cat] >= _cat_limit:
                    logger.warning(
                        "[P4] Budget catégorie '{}' atteint ({}/{}) — outil={} — passage à FINAL suggéré",
                        _tool_cat, self._category_iter_counts[_tool_cat], _cat_limit, action.tool_name,
                    )
                # Injecter l'avertissement de stagnation dans l'observation si détecté
                if _stagnation_warning and observation.content:
                    observation = Observation(
                        content=observation.content + _stagnation_warning,
                        success=observation.success,
                        sub_results=getattr(observation, "sub_results", ()),
                        origin=getattr(observation, "origin", "tool"),
                    )
                # Injecter l'avertissement d'hallucination dans l'observation si récidive
                if _halluc_warning and observation.content:
                    observation = Observation(
                        content=observation.content + _halluc_warning,
                        success=observation.success,
                        sub_results=getattr(observation, "sub_results", ()),
                        origin=getattr(observation, "origin", "tool"),
                    )
                step.observation = observation

                # LOT P3 — une MUTATION RÉUSSIE remet les compteurs de lecture à
                # ZÉRO : relire un fichier qu'on vient de modifier n'est pas de la
                # stagnation, c'est vérifier son travail. Même règle que PG-1.a
                # pour le compteur de progression du plan.
                if (
                    observation.success
                    and (action.tool_name or "") in _PG1_MUTATION_TOOLS
                ):
                    _read_file_path_counter.clear()
                    _read_file_reread_counter.clear()
                    _read_file_ranges_seen.clear()

                # Fix 3.2: Vérifier que write_file/apply_patch produit un fichier non-vide
                # (uniquement sur chemins absolus pour éviter les faux positifs sur chemins relatifs)
                if observation.success and action.tool_name in ("write_file", "apply_patch"):
                    _wf_path = (action.tool_args or {}).get("path") or (action.tool_args or {}).get("file_path", "")
                    if _wf_path and os.path.isabs(_wf_path):
                        try:
                            if os.path.isfile(_wf_path) and os.path.getsize(_wf_path) == 0:
                                observation = Observation(
                                    content=f"❌ ERREUR : le fichier `{_wf_path}` a été écrit mais est VIDE (0 octet). L'écriture a échoué silencieusement. Recommence avec le contenu complet.",
                                    success=False,
                                )
                                step.observation = observation
                                logger.warning("[Fix3.2] {} → fichier vide: {}", action.tool_name, _wf_path)
                        except Exception:
                            pass

                # ── ExecutionLedger V1 : enregistrer chaque action exécutée ──
                try:
                    _led_target = _ledger_extract_target(
                        action.tool_name, action.tool_args or {},
                    )
                    _led_proof = _ledger_extract_proof(
                        action.tool_name, observation.content or "", observation.success,
                    )
                    _led_intent = None
                    _ss_for_led = self._structured_state
                    if _ss_for_led is not None:
                        _led_intent = _ss_for_led.last_intent
                    _led_meta = {
                        "duration_ms": round(_tool_exec_duration * 1000, 1),
                        "intent": _led_intent,
                    }
                    # VERROU DE VÉRITÉ : pour une commande shell qui lance des
                    # tests, on parse l'issue réelle (pytest/jest/…) et on la
                    # stocke — la finalisation mission ne pourra plus clamer
                    # « tests verts » sans preuve verte au ledger.
                    if action.tool_name in ("run_command", "run_shell", "exec_command"):
                        try:
                            from src.reasoning.test_proof import (
                                is_test_command as _is_test_cmd,
                                parse_test_outcome as _parse_test_outcome,
                            )
                            _cmd_str = str((action.tool_args or {}).get("command", "") or "")
                            # LOT 2.4 — la commande au meta : preuve `node --check`
                            # consultable par le JS GATE / has_js_syntax_check().
                            _led_meta["command"] = _cmd_str[:200]
                            if _is_test_cmd(_cmd_str):
                                _exit_code = getattr(observation, "exit_code", None)
                                _led_meta["test_outcome"] = _parse_test_outcome(
                                    _cmd_str, observation.content or "", _exit_code,
                                )
                        except Exception:
                            pass
                    self.execution_ledger.append(
                        iteration=i,
                        action=action.tool_name,
                        target=_led_target,
                        success=observation.success,
                        proof=_led_proof,
                        meta=_led_meta,
                    )
                    # M106: keep the latest test verdict in the persistent mission
                    # record so status/result remain factual after reboot or cancel.
                    _persisted_test = _led_meta.get("test_outcome")
                    if (
                        isinstance(_persisted_test, dict)
                        and _persisted_test.get("is_test_cmd")
                        and self._orchestrator_enabled()
                    ):
                        try:
                            self.task_orchestrator.set_task_metadata(
                                self.task_id,
                                last_test_outcome=dict(_persisted_test),
                                tests_green=bool(_persisted_test.get("green")),
                            )
                        except Exception as exc:
                            logger.debug("[M106] test proof persistence skipped: {}", exc)
                    # LOT N1 (run HuffPack 2026-08-14) — un CONSTAT mesuré doit
                    # rejoindre les faits de la mission. Sans ça, les trois taux de
                    # compression calculés à 03:11:27 n'ont jamais atteint
                    # l'utilisateur : `mission_status` ne portait que quatre
                    # compteurs, et le récapitulatif — pourtant libre — n'a pu dire
                    # que « 12 passed ». Le modèle ne les a pas inventés (truth-lock
                    # tenu) : il ne les avait plus.
                    if (
                        action.tool_name in ("run_command", "run_tests")
                        and observation.success
                        and self._is_mission_run
                        and self._orchestrator_enabled()
                    ):
                        try:
                            from src.subagents.mission_measures import (
                                command_is_measurement,
                                merge_measurement,
                            )

                            _cmd_n1 = str((action.tool_args or {}).get("command") or "")
                            if command_is_measurement(_cmd_n1):
                                _rec_n1 = self.task_orchestrator.get_task(self.task_id) or {}
                                _prev_n1 = (_rec_n1.get("metadata") or {}).get(
                                    "mission_measurements"
                                )
                                _merged_n1 = merge_measurement(
                                    # LOT N1-bis (run LogLens, 2026-08-14) : c'est
                                    # `.content`, pas `.output` — l'attribut n'existe
                                    # pas. Chaque mesure levait une AttributeError
                                    # avalée par le `except`, donc AUCUN chiffre n'a
                                    # jamais été retenu (6 fois dans ce seul run).
                                    _prev_n1, _cmd_n1, str(observation.content or "")
                                )
                                if _merged_n1:
                                    self.task_orchestrator.set_task_metadata(
                                        self.task_id, mission_measurements=_merged_n1,
                                    )
                                    logger.info(
                                        "[N1] constat mesuré retenu ({} au total) : {}",
                                        len(_merged_n1), _cmd_n1[:70],
                                    )
                        except Exception as _exc_n1:
                            logger.debug("[N1] capture du constat ignorée: {}", _exc_n1)
                except Exception as _led_exc:
                    logger.debug("[ExecutionLedger] Échec enregistrement: {}", _led_exc)

                # ── ExecutionLedger : expansion des sous-outils parallel_tools ──
                if action.tool_name == "parallel_tools":
                    _sub_results_pt = getattr(observation, "sub_results", ())
                    for _sub in _sub_results_pt:
                        try:
                            _sub_target = _ledger_extract_target(_sub.tool_name, _sub.args)
                            _sub_proof = _ledger_extract_proof(
                                _sub.tool_name, _sub.content, _sub.success
                            )
                            self.execution_ledger.append(
                                iteration=i,
                                action=_sub.tool_name,
                                target=_sub_target,
                                success=_sub.success,
                                proof=_sub_proof,
                                meta={
                                    "duration_ms": 0.0,
                                    "intent": _led_intent,
                                    "via": "parallel_tools",
                                },
                            )
                        except Exception as _sub_led_exc:
                            logger.debug("[ExecutionLedger] parallel_tools sub: {}", _sub_led_exc)

                # ── Mission A : mémoriser le projet actif après mutation sur workspace ──
                # Permet au tour suivant de réutiliser ce projet sans find_files.
                if observation.success and _led_target and action.tool_name in _LEDGER_MUTATION_TOOLS:
                    _ws_match = re.search(r'(.+?[/\\]workspace[/\\][\w\-]+)', _led_target.replace("\\", "/"))
                    if _ws_match:
                        try:
                            _lum_mem = getattr(self.tools, "lumena", None)
                            _id_svc_mem = getattr(_lum_mem, "_identity_svc", None) if _lum_mem else None
                            if _id_svc_mem is not None and self.runtime_ctx is not None:
                                from ..core_services.identity_service import IdentityService as _IDS_M
                                _ck_mem = _IDS_M.resolve_channel_key(self.runtime_ctx)
                                if _ck_mem:
                                    _ws_path = _ws_match.group(1)
                                    _slug = _ws_path.replace("\\", "/").rsplit("/", 1)[-1]
                                    _id_svc_mem.remember_code_context(_ck_mem, _ws_path, project_slug=_slug)
                                    logger.debug("[RecentProject] Mémorisé: {} → {}", _ck_mem, _ws_path)
                                    # Poser immédiatement dans established_facts pour ce run
                                    _ss_proj = self._structured_state
                                    if _ss_proj is not None:
                                        _ss_proj.set_fact("active_project_path", _ws_path)
                                        _ss_proj.set_fact("active_project_slug", _slug)
                        except Exception as _mem_exc:
                            logger.debug("[RecentProject] Mémorisation échouée: {}", _mem_exc)

                # ── StructuredState V1 : alimenter recent_tools ──
                self._feed_structured_tool(action.tool_name)

                # ── P1.7: Auto-expand filtre après exécution d'outil ──
                if hasattr(self.tools, '_allowed_tools') and self.tools._allowed_tools is not None:
                    _executed_cat = self.tools._tool_modules.get(action.tool_name)
                    if _executed_cat:
                        _TOOL_TRANSITIONS = {
                            "browser": {"files", "documents"},
                            "files":   {"system", "mail"},
                            "web":     {"browser", "files", "documents"},
                            "mail":    {"files", "social"},
                            "system":  {"files", "mail"},
                            "project": {"git", "files", "codebase"},
                            "social":  {"web", "files"},
                            "automation": {"web", "system", "mail"},
                        }
                        _expand_cats = _TOOL_TRANSITIONS.get(_executed_cat, set())
                        if _expand_cats:
                            for _tn, _tc in self.tools._tool_modules.items():
                                if _tc in _expand_cats:
                                    self.tools._allowed_tools.add(_tn)
                            self.tools._tools_desc_cache = None

                # ── Multi-action : exécuter les actions en queue ──
                # Levier 1: parallélisation automatique quand toutes les actions sont read-only.
                _pending = getattr(self, '_pending_multi_actions', [])
                if _pending and observation.success:
                    _combined_obs = [observation.content or ""]
                    # Set d'outils considérés read-only (safe à paralléliser).
                    _READ_ONLY_TOOLS = {
                        "read_file", "read_files_batch", "list_files", "list_dir",
                        "grep", "grep_search", "grep_batch",
                        "web_search", "web_fetch", "memory_search", "semantic_search",
                        "get_file_info", "find_files", "scan_project",
                    }
                    _all_read_only = (
                        (action.tool_name or "") in _READ_ONLY_TOOLS
                        and all((_n or "") in _READ_ONLY_TOOLS for _n, _ in _pending)
                        and len(_pending) >= 1
                    )
                    if _all_read_only:
                        # ── Exécution PARALLÈLE ──
                        logger.info("⚡ Multi-action PARALLÈLE ({} actions read-only)", len(_pending))
                        _par_start = perf_counter()

                        from .caller_context import REACT as _CALLER_REACT_PAR
                        async def _run_one(_n: str, _a: dict):
                            try:
                                return _n, await self._execute_tool_with_cancel_guard(
                                    _n, _a, caller=_CALLER_REACT_PAR,
                                ), None
                            except Exception as _e:
                                return _n, None, _e

                        _results = await asyncio.gather(
                            *(_run_one(_n, _a) for _n, _a in _pending),
                            return_exceptions=False,
                        )
                        _par_dur = perf_counter() - _par_start
                        if hasattr(self, '_timeout_deadline'):
                            # Temps parallèle ≈ max(individuels) ≈ _par_dur (pas somme).
                            self._timeout_deadline += _par_dur
                            self._tool_time_total = getattr(self, '_tool_time_total', 0.0) + _par_dur
                        for _n, _obs, _err in _results:
                            if _err is not None:
                                _combined_obs.append(f"[{_n}] Erreur: {_err}")
                            else:
                                _combined_obs.append(f"[{_n}] {_obs.content or ''}")
                                if self._task_plan and getattr(_obs, 'success', False):
                                    self._update_plan_progress(_n, {}, _obs.content or "", i)
                    else:
                        # ── Exécution SÉQUENTIELLE (legacy : abort-on-fail pour writes) ──
                        _abort_multi = False
                        for _ma_name, _ma_args in _pending:
                            if ReActLoop._duplicate_document_mutation(
                                action.tool_name or "",
                                action.tool_args or {},
                                _ma_name or "",
                                _ma_args or {},
                            ):
                                logger.info(
                                    "Multi-action document: mutation identique '{}' ignoree",
                                    _ma_name,
                                )
                                _combined_obs.append(
                                    f"[{_ma_name}] Mutation documentaire identique deja executee"
                                )
                                continue
                            if _abort_multi:
                                logger.warning("⚡ Multi-action '{}' annulé (échec précédent)", _ma_name)
                                _combined_obs.append(f"[{_ma_name}] Annulé (action précédente échouée)")
                                continue
                            try:
                                logger.info("⚡ Multi-action queue: exécution de '{}' (args: {})", _ma_name, list(_ma_args.keys()))
                                from .caller_context import REACT as _CALLER_REACT_MA
                                _ma_start = perf_counter()
                                _ma_gate = self._worker_codeagent_first_gate(_ma_name, _ma_args if isinstance(_ma_args, dict) else None)
                                if _ma_gate is not None:
                                    _ma_obs = _ma_gate
                                else:
                                    _ma_obs = await self._execute_tool_with_cancel_guard(
                                        _ma_name, _ma_args, caller=_CALLER_REACT_MA,
                                    )
                                _ma_dur = perf_counter() - _ma_start
                                if hasattr(self, '_timeout_deadline'):
                                    self._timeout_deadline += _ma_dur
                                    self._tool_time_total = getattr(self, '_tool_time_total', 0.0) + _ma_dur
                                _combined_obs.append(f"[{_ma_name}] {_ma_obs.content or ''}")
                                if self._task_plan and _ma_obs.success:
                                    self._update_plan_progress(_ma_name, _ma_args, _ma_obs.content or "", i)
                                # Si un outil échoue, annuler les suivants du même type
                                if not _ma_obs.success:
                                    _abort_multi = True
                                    logger.warning("⚡ Multi-action '{}' échoué — annulation des suivants", _ma_name)
                            except Exception as _ma_err:
                                logger.warning("Multi-action '{}' échoué: {}", _ma_name, _ma_err)
                                _combined_obs.append(f"[{_ma_name}] Erreur: {_ma_err}")
                                _abort_multi = True
                    self._pending_multi_actions = []
                    observation = Observation(
                        content="\n\n".join(_combined_obs),
                        success=observation.success,
                    )
                    step.observation = observation

                # ── Plan TODO : mise a jour progression ──
                if self._task_plan and observation.success:
                    if (action.tool_name or "") == "parallel_tools":
                        # Fix parallel_tools → plan tracker :
                        # propager chaque sous-outil RÉUSSI au plan via son VRAI nom,
                        # avec allow_fallback=False (GF-1) → seul le matching prouvé
                        # (tool+args+observation) peut cocher une tâche. Jamais de
                        # fallback séquentiel/auto-advance pour un sous-outil.
                        # GF-2 : completions bornées au nombre de sous-outils prouvés
                        # (chaque appel a son propre _completed_this_call local).
                        _subs = getattr(observation, "sub_results", ()) or ()
                        for _sub in _subs:
                            if not getattr(_sub, "success", False):
                                continue
                            _sub_name = getattr(_sub, "tool_name", "") or ""
                            if not _sub_name:
                                continue
                            self._update_plan_progress(
                                _sub_name,
                                getattr(_sub, "args", {}) or {},
                                getattr(_sub, "content", "") or "",
                                i,
                                allow_fallback=False,
                            )
                    else:
                        self._update_plan_progress(
                            action.tool_name or "", action.tool_args,
                            observation.content or "", i,
                        )

                # LOT Z20 — hors du garde browser À DESSEIN : ce qui invalide une
                # attente d'interaction (édition, délégation, publication) n'est
                # jamais un outil `browser_*`.
                self._invalidate_interaction_pending(
                    action.tool_name or "", bool(observation.success)
                )
                # LOT Z24 — un fichier ecrit apres la publication est hors livrable.
                if bool(observation.success):
                    try:
                        self._nudge_unpublished_writes()
                    except Exception as _z24_exc:
                        logger.debug("[Z24] skip: {}", _z24_exc)

                # ── Guard browser : impasse, échecs en série, répétition de cible ──
                # Couvre tous les outils browser_* (préfixe), pas seulement les 6 initiaux.
                _is_browser_tool = (action.tool_name or "").startswith("browser_")
                if _is_browser_tool:
                    obs_lower = (observation.content or "").lower()
                    _page_title = ""
                    _page_url = ""
                    _obs_raw = observation.content or ""
                    # Format dom_state / page_info : "Page: ...\nURL: ..."
                    _m_title = re.search(r"^Page:\s*(.+)$", _obs_raw, re.MULTILINE)
                    _m_url   = re.search(r"^URL:\s*(.+)$",  _obs_raw, re.MULTILINE)
                    if _m_title:
                        _page_title = _m_title.group(1).strip()
                    if _m_url:
                        _page_url = _m_url.group(1).strip()
                    # Fallback — format browser_navigate : "✅ Navigué vers: Title (URL)"
                    if not _page_url:
                        _m_nav = re.search(
                            r"(?:Navigu[eé] vers|Navigated to)[^\n]*\((https?://[^)\n]+)\)",
                            _obs_raw,
                        )
                        if _m_nav:
                            _page_url = _m_nav.group(1).strip()
                    if not _page_title and _page_url:
                        _m_nav_t = re.search(
                            r"(?:Navigu[eé] vers|Navigated to):\s*(.+?)\s*\(https?://",
                            _obs_raw,
                        )
                        if _m_nav_t:
                            _page_title = _m_nav_t.group(1).strip()
                    # Interaction tools usually return only an acknowledgement.
                    # Preserve the last observed location so type/click/content
                    # remain associated with the registered local preview.
                    if _page_url:
                        self._last_browser_page_url = _page_url
                    else:
                        _page_url = str(
                            getattr(self, "_last_browser_page_url", "") or ""
                        )

                    # Phase 1 browser: reconnaître la surface réelle avant d'insister.
                    # previous_surface : héritage de surface sur les observations sans signal fort
                    # (ex : browser_screenshot renvoie juste le chemin du fichier → pas de hints listing)
                    _obs_text = observation.content or ""
                    _surface, _surface_reason = _classify_browser_surface(
                        _obs_text,
                        current_url=_page_url,
                        page_title=_page_title,
                        previous_surface=self._last_browser_surface,
                    )
                    if _surface == self._last_browser_surface:
                        self._browser_surface_streak += 1
                    else:
                        self._browser_surface_streak = 1
                        self._last_browser_surface = _surface
                    self._last_browser_surface_reason = _surface_reason
                    logger.debug(
                        "[BROWSER SURFACE] {} (streak={}) — {}",
                        _surface,
                        self._browser_surface_streak,
                        _surface_reason,
                    )

                    _prev_progress_sig = self._last_browser_progress_sig
                    _progress_sig = _make_browser_progress_signature(
                        _surface,
                        _obs_text,
                        current_url=_page_url,
                        page_title=_page_title,
                        previous=_prev_progress_sig,
                    )
                    _progressed, _progress_reason = _browser_progress_delta(
                        _prev_progress_sig,
                        _progress_sig,
                        action_tool=action.tool_name or "",
                        observation_text=_obs_text,
                    )
                    _interaction_proof = (
                        (action.tool_name or "") == "browser_evaluate"
                        and _browser_evaluate_proves_interaction(
                            str((action.tool_args or {}).get("script", "")),
                            _obs_text,
                        )
                    )
                    if _interaction_proof:
                        _progressed = True
                        _progress_reason = (
                            "browser_evaluate a provoque une interaction et lu son etat dynamique"
                        )
                    self._last_browser_progress_sig = _progress_sig
                    _is_real_action = (action.tool_name or "") in BROWSER_ACTION_TOOLS
                    if _progressed:
                        self._browser_no_progress_streak = 0
                    elif _is_real_action:
                        # Seules les vraies actions (clics, saisie, navigation) comptent.
                        # Outils visuels ET utilitaires (scroll, dismiss_popups…) sont neutres.
                        self._browser_no_progress_streak += 1
                    logger.debug(
                        "[BROWSER PROGRESS] progressed={} streak={} — {}",
                        _progressed,
                        self._browser_no_progress_streak,
                        _progress_reason,
                    )

                    _intent_query = getattr(self, "_original_query", query) or query
                    _surface_mismatch, _surface_mismatch_reason = _browser_surface_mismatch(
                        _surface,
                        _intent_query,
                    )
                    _auth_recovery_target = None
                    if _surface == "contact_form" and _browser_is_auth_intent(_intent_query):
                        _auth_recovery_target = _extract_browser_auth_target(_obs_text)
                    if _surface == "iframe_heavy" and action.tool_name not in ("browser_frames", "browser_frame_content"):
                        self._pending_loop_guidance = (
                            "⚠️ GUIDANCE SURFACE: Cette page semble pilotée par des iframes. "
                            "Appelle `browser_frames` puis `browser_frame_content` pour lire le bon frame "
                            "avant de continuer à cliquer ou taper."
                        )
                    elif _surface_mismatch:
                        _soft_auth_recovery = _surface == "contact_form" and _browser_is_auth_intent(_intent_query)
                        if _soft_auth_recovery and _auth_recovery_target:
                            _idx, _label = _auth_recovery_target
                            self._pending_loop_guidance = (
                                "⚠️ GUIDANCE AUTH SPA: l'URL ressemble à un login, mais la vue affichée reste un formulaire de contact. "
                                f"Avant d'abandonner, clique explicitement sur le lien visible [{_idx}] \"{_label}\" "
                                "avec `browser_click_index`, puis relis le DOM. N'insiste pas avec `browser_navigate` sur la même route."
                            )
                        elif _soft_auth_recovery:
                            self._pending_loop_guidance = (
                                "⚠️ GUIDANCE AUTH SPA: tu es sur un formulaire de contact alors que la tâche demande une connexion. "
                                "Explore encore un peu comme un humain: lis le DOM, cherche un lien/bouton de connexion visible, "
                                "essaie un clic réel avant de conclure."
                            )

                        if self._browser_surface_streak >= (4 if _soft_auth_recovery else 2):
                            logger.warning(
                                "⛔ Mismatch browser surface/objectif: {} — arrêt propre",
                                _surface_mismatch_reason,
                            )
                            _finish_iteration(status="error", error="browser_surface_mismatch")
                            message = (
                                f"⛔ Navigation interrompue : **{_surface_mismatch_reason}**.\n\n"
                                f"Surface détectée : `{_surface}` ({_surface_reason}).\n\n"
                                "Je peux reprendre si tu me donnes une URL publique directe ou une surface plus adaptée."
                            )
                            self._mark_task_failed("browser_surface_mismatch")
                            return message
                        self._pending_loop_guidance = (
                            f"⚠️ GUIDANCE SURFACE: {_surface_mismatch_reason}. "
                            "Ne continue pas à agir comme si la page était directement exploitable. "
                            "Cherche une preview/public link/share link, ou change de surface."
                        )

                    if not _progressed:
                        _soft_auth_recovery = _surface == "contact_form" and _browser_is_auth_intent(_intent_query)
                        _no_progress_stop = 8 if _soft_auth_recovery else 6
                        _no_progress_warn = 4 if _soft_auth_recovery else 3
                        if self._browser_no_progress_streak >= _no_progress_stop:
                            logger.warning(
                                "⛔ Browser sans progression utile (surface={}, streak={}) — arrêt propre",
                                _surface,
                                self._browser_no_progress_streak,
                            )
                            _finish_iteration(status="error", error="browser_no_progress")
                            message = (
                                f"⛔ Navigation interrompue : aucune progression utile détectée sur la surface "
                                f"`{_surface}` après {self._browser_no_progress_streak} tours.\n\n"
                                f"Raison: {_progress_reason}. Surface: {_surface_reason}.\n\n"
                                "Je peux reprendre avec une URL plus directe, une stratégie différente, "
                                "ou un objectif browser plus simple."
                            )
                            self._mark_task_failed("browser_no_progress")
                            return message
                        if self._browser_no_progress_streak >= _no_progress_warn:
                            if _surface == "search_results":
                                self._pending_loop_guidance = (
                                    "⚠️ GUIDANCE PROGRESSION: tu restes sur une page de résultats sans progression utile. "
                                    "Ouvre un résultat concret ou navigue directement vers une URL plus ciblée."
                                )
                            elif _surface == "listing_results":
                                self._pending_loop_guidance = (
                                    "⚠️ GUIDANCE PROGRESSION: tu es sur une page d'annonces sans avancement. "
                                    "Clique sur une annonce spécifique ('Voir l'annonce'), utilise les filtres "
                                    "pour affiner (kilométrage, prix, année), ou scrolle pour charger plus de résultats. "
                                    "Évite de répéter browser_dom_state sans agir."
                                )
                            elif _surface == "public_form":
                                self._pending_loop_guidance = (
                                    "⚠️ GUIDANCE PROGRESSION: tu restes sur le même formulaire sans progrès visible. "
                                    "Relis `browser_dom_state`, identifie un autre champ ou change de stratégie."
                                )
                            elif _surface == "auth_form":
                                self._pending_loop_guidance = (
                                    "⚠️ GUIDANCE PROGRESSION: tu es sur un formulaire de connexion sans avancement. "
                                    "Vérifie que tu remplis les bons champs (email + mot de passe). "
                                    "Utilise `browser_dom_state` pour lister les indices exact, "
                                    "puis `browser_type_index` pour saisir chaque champ."
                                )
                            elif _surface == "contact_form":
                                self._pending_loop_guidance = (
                                    "⚠️ GUIDANCE PROGRESSION: tu es sur un formulaire de contact sans avancement. "
                                    "Assure-toi de remplir tous les champs obligatoires (nom, email, message). "
                                    "Utilise `browser_dom_state` pour voir les champs disponibles."
                                )
                            elif _surface == "spa_shell":
                                self._pending_loop_guidance = (
                                    "⚠️ GUIDANCE SPA: La page est un shell SPA sans contenu chargé. "
                                    "Essaie : 1) `browser_wait_for` pour attendre le chargement, "
                                    "2) `browser_evaluate` pour forcer l'état JavaScript, "
                                    "3) `browser_dom_state` puis cliquer sur un lien/onglet pour charger la vue."
                                )
                            elif _surface in ("normal_content", "detail_page"):
                                # SPA stagnation : si navigate sans changement d'URL → orienter vers DOM/JS
                                if action.tool_name == "browser_navigate" and _page_url == (
                                    _prev_progress_sig[1]
                                    if _prev_progress_sig and len(_prev_progress_sig) > 1
                                    else ""
                                ):
                                    self._pending_loop_guidance = (
                                        "⚠️ GUIDANCE SPA: La navigation vers cette URL ne change pas le contenu visible. "
                                        "La page semble être une SPA dont la vue ne se met pas à jour via browser_navigate. "
                                        "Stratégie : 1) `browser_dom_state` pour lister les liens/onglets cliquables, "
                                        "2) cliquer sur le lien/onglet cible pour changer la vue, "
                                        "3) `browser_evaluate` pour forcer un changement d'état JavaScript."
                                    )
                                else:
                                    self._pending_loop_guidance = (
                                        "⚠️ GUIDANCE PROGRESSION: tu restes sur la même surface sans changement utile. "
                                        "Revalide l'état réel puis choisis une action différente."
                                    )

                    # ── 2b. LOT 2.11.C/D — preview LOCALE : boucle d'inspection ──────────
                    # Sur une preview que Lumena sert elle-même (loopback enregistré),
                    # screenshot/dom_state en boucle ne comptent pas comme « no progress »
                    # (run memo) → escalade bornée vers browser_evaluate, puis conclusion
                    # HONNÊTE si l'interactif n'est pas prouvé. Inerte hors preview locale.
                    _is_local_preview = _url_is_local_preview(_page_url)
                    _g_cd = self.exec_state.guards
                    _manual_interaction_proof = False
                    _browser_tool = action.tool_name or ""
                    if _is_local_preview and observation.success:
                        (
                            _manual_interaction_proof,
                            _g_cd.local_preview_last_read_fingerprint,
                            _g_cd.local_preview_mutation_since_read,
                        ) = _advance_manual_browser_flow(
                            _g_cd.local_preview_last_read_fingerprint,
                            mutation_pending=_g_cd.local_preview_mutation_since_read,
                            tool_name=_browser_tool,
                            observation=_obs_text,
                        )
                    # LOT R′ — la preuve d'interaction était enregistrée APRÈS cette
                    # décision (quelques lignes plus bas), donc invisible pour elle :
                    # une mission pouvait démontrer son interactif à l'itération N et
                    # être coupée par la décision de cette même itération N. On la
                    # calcule ICI, avant de décider — persistée OU acquise à l'instant.
                    _cd_proven = bool(
                        getattr(_g_cd, "local_preview_interaction_proven", False)
                        or (_interaction_proof and _browser_tool == "browser_evaluate")
                        or _manual_interaction_proof
                    )
                    _cd_action, _cd_streak, _cd_asked = _local_preview_loop_decision(
                        _is_local_preview,
                        action.tool_name or "",
                        _progressed,
                        _g_cd.local_preview_blind_streak,
                        _g_cd.local_preview_evaluate_asked,
                        interaction_proven=_cd_proven,
                        tool_succeeded=bool(observation.success),
                    )
                    _g_cd.local_preview_blind_streak = _cd_streak
                    _g_cd.local_preview_evaluate_asked = _cd_asked
                    # LOT 2.12.D — un browser_evaluate qui PROGRESSE (état JS réel lu)
                    # sur la preview locale = preuve de l'interactif. Sans lui, tout
                    # claim « jeu démarré / serpent redirigé » au FINAL est fabriqué.
                    if _is_local_preview and (
                        (_interaction_proof and _browser_tool == "browser_evaluate")
                        or _manual_interaction_proof
                    ):
                        _g_cd.local_preview_interaction_proven = True
                        logger.info(
                            "[BROWSER INTERACTION PROOF] mutation + etat dynamique confirmes "
                            "(source={})",
                            "browser_evaluate" if _interaction_proof else "dom_delta",
                        )
                        if self._orchestrator_enabled():
                            try:
                                self.task_orchestrator.set_task_metadata(
                                    self.task_id,
                                    browser_interaction_verified=True,
                                    browser_interaction_proof_kind=(
                                        "browser_evaluate"
                                        if _interaction_proof
                                        else "dom_delta_after_user_flow"
                                    ),
                                )
                            except Exception as exc:
                                logger.debug(
                                    "[M106] browser proof persistence skipped: {}", exc
                                )
                    if _cd_action == "escalate":
                        logger.info(
                            "[LOCAL PREVIEW] escalade browser_evaluate (streak={})",
                            _cd_streak,
                        )
                        self._pending_loop_guidance = (
                            "⚠️ GUIDANCE PREVIEW LOCALE: tu inspectes en boucle une preview "
                            "que TU sers en local sans progrès visible. Screenshot/dom_state "
                            "ne PROUVENT pas l'interactif. Fais UNE assertion concrète avec "
                            "`browser_evaluate` : lis l'état JS réel (compteur de coups, score, "
                            "valeur d'un élément du DOM) qui démontre que la logique marche. "
                            "Si l'évaluation ne prouve rien, conclus HONNÊTEMENT : page servie "
                            "et navigable, mais validation interactive complète non prouvée — "
                            "n'affirme JAMAIS « jeu validé » sans cette preuve."
                        )
                    elif _cd_action == "stop":
                        # LOT Z23 — ce constat GRAVE un fait ; il ne termine plus le
                        # run. Le `return` d'origine coutait la completude sans rien
                        # apporter a l'honnetete (le truth-lock bannerise de toute
                        # facon, doctrine 2.13.A). Voir `_local_preview_unprovable_gate`.
                        _lp_url_now = str(_page_url or "")
                        _lp_already = bool(
                            getattr(_g_cd, "local_preview_interaction_unprovable", False)
                            and str(getattr(self, "_lp_unprovable_url", "") or "") == _lp_url_now
                        )
                        logger.warning(
                            "⛔ Preview locale : inspection sans preuve interactive (streak={}) — conclusion honnête",
                            _cd_streak,
                        )
                        if not _lp_already:
                            _g_cd.local_preview_interaction_unprovable = True
                            self._lp_unprovable_url = _lp_url_now
                            self._pending_loop_guidance = (
                                "⚠️ CONSTAT ACQUIS : sur cette preview locale, la validation "
                                "interactive n'est pas prouvable — tu as deja tenté l'assertion, "
                                "elle n'a rien démontré. C'est definitif pour ce run : ne "
                                "réessaie pas, n'inspecte plus cette page.\n\n"
                                "La mission N'EST PAS finie pour autant. Reprends ce qui reste "
                                "demandé — fichiers annoncés, livrables, vérifications hors "
                                "navigateur — puis conclus en énonçant ce constat tel quel."
                            )
                            _g_cd.local_preview_blind_streak = 0
                            logger.info(
                                "[Z23] interactif jugé non prouvable sur '{}' — inspection close, "
                                "mission POURSUIVIE (le run n'est plus tué ici)",
                                _lp_url_now,
                            )
                        else:
                            # Filet de terminaison : la branche se redéclenche sur la
                            # MEME page alors que le verrou est deja pose — le verrou
                            # n'a pas pris, on conclut plutot que de boucler.
                            logger.warning(
                                "[Z23] filet : stop redéclenché sur '{}' malgré le verrou — "
                                "conclusion honnête", _lp_url_now,
                            )
                            _finish_iteration(status="ok", summary="local_preview_unproven")
                            _port_txt = ""
                            try:
                                from urllib.parse import urlparse as _up_cd
                                _pp_cd = _up_cd(str(_page_url)).port
                                if _pp_cd:
                                    _port_txt = f" sur le port {_pp_cd}"
                            except Exception:
                                pass
                            return (
                                f"J'ai servi la preview en local{_port_txt} et vérifié "
                                "qu'elle se charge et se navigue (page rendue, style "
                                "appliqué). En revanche, même après une assertion "
                                "`browser_evaluate`, je n'ai pas pu **prouver** la "
                                "validation interactive complète (logique de jeu / état "
                                "applicatif) : je ne l'affirme donc pas. Le livrable est "
                                "en place et consultable, mais la validation interactive "
                                "reste NON prouvée dans ce run — dis-moi si tu veux que "
                                "je retente avec une autre méthode d'assertion."
                            )

                    # ── 2a. Détection d'impasse centralisée ──────────────────────────────
                    _imp_blocked, _imp_reason, _imp_try_dismiss = _detect_browser_impasse(
                        observation.content or ""
                    )
                    if _surface != "popup_blocked":
                        self._browser_dismiss_attempted = False
                    if _imp_blocked:
                        _dismiss_tried = getattr(self, "_browser_dismiss_attempted", False)
                        if _imp_try_dismiss and not _dismiss_tried and action.tool_name != "browser_dismiss_popups":
                            # Tentative automatique : un seul essai de dismiss avant de conclure
                            self._browser_dismiss_attempted = True
                            self._browser_post_block_guard = True  # anti-dérive activé
                            logger.info(
                                "[BROWSER IMPASSE] {} — tentative browser_dismiss_popups",
                                _imp_reason,
                            )
                            self._pending_loop_guidance = (
                                f"⚠️ GUIDANCE BROWSER: {_imp_reason}.\n"
                                "Appelle `browser_dismiss_popups` pour tenter de fermer l'overlay, "
                                "puis reprends depuis `browser_screenshot`.\n"
                                "⛔ Reste dans le navigateur — n'utilise pas run_command, curl "
                                "ou d'outils système avant de confirmer que le blocage est infranchissable."
                            )
                        else:
                            # ── P1 — Fallback CAPTCHA / anti-bot Google ───────────────────
                            # Si le dernier outil était browser_search_google et que le
                            # blocage est un CAPTCHA/anti-bot, tenter DuckDuckGo ou une URL
                            # directe plutôt que de stopper immédiatement. Une seule tentative.
                            _is_search_captcha = (
                                action.tool_name == "browser_search_google"
                                and any(tok in (_imp_reason or "").lower() for tok in (
                                    "captcha", "recaptcha", "bot", "cloudflare",
                                    "checking your browser", "challenge",
                                ))
                            )
                            _captcha_fallback_tried = getattr(
                                self, "_google_search_captcha_fallback_attempted", False
                            )
                            if _is_search_captcha and not _captcha_fallback_tried:
                                self._google_search_captcha_fallback_attempted = True
                                _search_query = str((action.tool_args or {}).get("query", ""))
                                _ddg_url = (
                                    "https://duckduckgo.com/?q="
                                    + _search_query.replace(" ", "+")
                                )
                                self._pending_loop_guidance = (
                                    f"⚠️ GUIDANCE CAPTCHA: Google a bloqué la recherche ({_imp_reason}).\n"
                                    "Stratégie de repli (essaie dans l'ordre) :\n"
                                    f"1. `browser_navigate` vers DuckDuckGo : `{_ddg_url}`\n"
                                    "2. Si aussi bloqué : navigue directement vers une URL candidate pertinente.\n"
                                    "3. Sinon : essaie Bing (`https://www.bing.com/search?q=...`).\n"
                                    "⛔ Ne passe pas en FINAL — un seul fallback suffit pour continuer."
                                )
                                logger.info(
                                    "[BROWSER CAPTCHA FALLBACK] {} → DuckDuckGo fallback, query={}",
                                    _imp_reason,
                                    _search_query,
                                )
                            else:
                                _tried_origins = set(getattr(
                                    self, "_browser_blocked_origins", set(),
                                ))
                                _pivot = _legal_browser_source_pivot(
                                    str(_page_url or ""),
                                    _imp_reason,
                                    original_query,
                                    _tried_origins,
                                )
                                if _pivot is not None:
                                    _origin, _guidance = _pivot
                                    _tried_origins.add(_origin)
                                    self._browser_blocked_origins = _tried_origins
                                    self._pending_loop_guidance = _guidance
                                    browser_fail_streak = 0
                                    logger.info(
                                        "[BROWSER SOURCE PIVOT] origine={} tentatives={}/3",
                                        _origin, len(_tried_origins),
                                    )
                                else:
                                    logger.warning(
                                        "⛔ Impasse browser détectée: {} — arrêt propre",
                                        _imp_reason,
                                    )
                                    _finish_iteration(status="error", error="browser_impasse")
                                    message = (
                                        f"⛔ Navigation interrompue : **{_imp_reason}**.\n\n"
                                        f"Le site semble protégé ou non exploitable "
                                        f"({_imp_reason.lower()}). Les pivots légaux bornés "
                                        "ont été épuisés; aucun CAPTCHA/WAF n'a été contourné.\n\n"
                                        f"Observation : {(observation.content or '')[:400]}"
                                    )
                                    self._mark_task_failed("browser_impasse")
                                    return message

                    # ── 2b. Suivi des échecs techniques en série ─────────────────────────
                    browser_failed = (
                        not observation.success
                        or "erreur" in obs_lower
                        or "timeout" in obs_lower
                        or "non demarre" in obs_lower
                        or "aucune page active" in obs_lower
                        or "0 resultats" in obs_lower
                    )
                    browser_fail_streak = browser_fail_streak + 1 if browser_failed else 0
                    if browser_fail_streak >= 4:
                        logger.warning(
                            "⚠️ Boucle browser en échec détectée ({} échecs) - arrêt contrôlé",
                            browser_fail_streak,
                        )
                        _finish_iteration(status="error", error="browser_fail_streak")
                        message = (
                            "⚠️ J'ai interrompu la tâche car le navigateur boucle en échec.\n\n"
                            f"Dernière observation: {(observation.content or '')[:500]}\n\n"
                            "Conseil: relancer avec une instruction plus simple (ex: 'ouvre google.com puis cherche ...') "
                            "ou vérifier que Playwright est bien installé (playwright install chromium)."
                        )
                        self._mark_task_failed("browser_fail_streak")
                        return message

                    # ── 2b-bis. Élément sans position connue → scrollIntoView + retry ────
                    # Cas réel : "Element [13] n'a pas de position connue (bbox=None)"
                    # L'élément existe mais est hors viewport ou masqué.
                    _NO_POS_PATTERNS = (
                        "n'a pas de position connue",
                        "no position",
                        "bbox=none",
                        "bounding_box indisponible",
                        "element is outside the viewport",
                        "element not visible",
                    )
                    if (
                        action.tool_name in ("browser_click_index", "browser_type_index")
                        and any(p in obs_lower for p in _NO_POS_PATTERNS)
                        and not self._pending_loop_guidance
                    ):
                        _no_pos_idx = str((action.tool_args or {}).get("index", "?"))
                        logger.info(
                            "[BROWSER NO-POS] index {} hors viewport — guidance scrollIntoView",
                            _no_pos_idx,
                        )
                        self._pending_loop_guidance = (
                            f"⚠️ GUIDANCE BROWSER: L'élément [{_no_pos_idx}] existe mais n'a pas de "
                            "position connue (hors viewport ou masqué).\n"
                            "Stratégie :\n"
                            f"1. `browser_evaluate(\"document.querySelectorAll('[data-lumena-idx]')[{_no_pos_idx}]?.scrollIntoView({{block:'center'}})\")`\n"
                            f"   OU `browser_scroll` pour amener l'élément en vue.\n"
                            f"2. Puis réessaie `{action.tool_name}` sur l'index [{_no_pos_idx}].\n"
                            "Si l'élément reste inaccessible, utilise `browser_dom_state` "
                            "pour trouver un index alternatif."
                        )

                    # ── 2b-ter. Signaux de succès précoces → guidance FINAL ──────────────
                    # Détecte les patterns prouvant que la tâche est déjà accomplie et guide
                    # vers FINAL immédiatement, évitant les tours superflus.
                    if observation.success and not self._pending_loop_guidance:
                        _early_success_signal: Optional[str] = None

                        # Signal 1 : connexion réussie (présence d'un lien de déconnexion)
                        if any(tok in obs_lower for tok in (
                            "déconnexion", "se déconnecter", "déconnectez-vous",
                            "logout", "log out", "sign out", "signout",
                        )) and _browser_is_auth_intent(query):
                            _early_success_signal = "connexion réussie (lien de déconnexion visible)"

                        # Signal 2 : formulaire soumis → httpbin.org/post (résultat de test)
                        elif "httpbin.org/post" in (_page_url or "").lower() or (
                            "httpbin" in obs_lower and "form" in obs_lower
                        ):
                            _early_success_signal = "formulaire soumis (réponse httpbin.org/post reçue)"

                        # Signal 3 : formulaire disparu + message de confirmation/succès
                        elif _surface in {"public_form", "contact_form", "auth_form"} and action.tool_name in (
                            "browser_click_index", "browser_submit_form"
                        ) and not _browser_observation_looks_like_popup_or_modal(observation.content or "") and any(tok in obs_lower for tok in (
                            "merci", "thank you", "thanks", "confirmation", "confirmé",
                            "bien reçu", "message envoyé", "votre message",
                            "votre demande", "success", "successfully sent",
                            "submitted", "soumis avec succès", "formulaire envoyé",
                        )):
                            _early_success_signal = "formulaire soumis avec succès (message de confirmation)"

                        # Signal 4 : chat ou messagerie — réponse reçue
                        elif action.tool_name in (
                            "discord_send", "discord_send_message",
                            "telegram_send_message", "send_whatsapp_message",
                        ) and observation.success:
                            _early_success_signal = "message envoyé avec succès"

                        if _early_success_signal:
                            logger.info(
                                "[BROWSER EARLY SUCCESS] {} — guidage vers FINAL",
                                _early_success_signal,
                            )
                            self._pending_loop_guidance = (
                                f"✅ SIGNAL DE SUCCÈS DÉTECTÉ : {_early_success_signal}.\n"
                                "La tâche principale est accomplie. "
                                "PASSE DIRECTEMENT À `ACTION: FINAL` avec un résumé clair de ce qui a été fait.\n"
                                "Ne relance pas d'autres outils browser inutiles."
                            )

                    # ── 2c. Détection répétition sur même cible browser ──────────────────
                    # Si le LLM clique/type sur le même index 3× sans progression → guidance
                    if action.tool_name in ("browser_click_index", "browser_type_index"):
                        _bct_idx = str((action.tool_args or {}).get("index", "?"))
                        _bct_key = f"{action.tool_name}:{_bct_idx}"
                        if not hasattr(self, "_browser_target_counts"):
                            self._browser_target_counts: dict = {}
                        self._browser_target_counts[_bct_key] = (
                            self._browser_target_counts.get(_bct_key, 0) + 1
                        )
                        if self._browser_target_counts[_bct_key] == 3:
                            logger.warning(
                                "[BROWSER REPEAT] {} sur index {} — 3e fois, guidance injectée",
                                action.tool_name, _bct_idx,
                            )
                            self._pending_loop_guidance = (
                                f"⚠️ GUIDANCE BROWSER: Tu viens d'appeler `{action.tool_name}` "
                                f"sur l'index {_bct_idx} pour la 3e fois sans progression visible.\n"
                                "L'index ne répond probablement pas comme attendu. "
                                "APPELLE `browser_screenshot` puis `browser_dom_state` "
                                "pour réévaluer l'état réel avant d'agir."
                            )
                        _obs_lower = (observation.content or "").lower()
                        if "textbox" in _obs_lower or "searchbox" in _obs_lower or "combobox" in _obs_lower:
                            self._browser_last_textbox_index = _bct_idx
                else:
                    browser_fail_streak = 0

                # Guard web_fetch: eviter les boucles longues sur sites anti-bot / SSL.
                if action.tool_name == "web_fetch":
                    obs_lower = (observation.content or "").lower()
                    fetch_failed = (
                        not observation.success
                        or "403" in obs_lower
                        or "forbidden" in obs_lower
                        or "dh_key_too_small" in obs_lower
                        or "ssl" in obs_lower
                        or "erreur fetch" in obs_lower
                    )
                    web_fetch_fail_streak = web_fetch_fail_streak + 1 if fetch_failed else 0
                    if web_fetch_fail_streak >= 2:
                        logger.warning(
                            "⚠️ web_fetch échoue en série ({} fois) - arrêt contrôlé",
                            web_fetch_fail_streak,
                        )
                        _finish_iteration(status="error", error="web_fetch_fail_streak")

                        last_search_obs = None
                        for h in reversed(self.history):
                            if not h.observation or not h.observation.content:
                                continue
                            txt = h.observation.content
                            if "Résultats DuckDuckGo" in txt or "🔍 Recherche:" in txt:
                                last_search_obs = txt[:1800]
                                break

                        message = (
                            "⚠️ J'ai arrêté la boucle: `web_fetch` échoue à répétition sur des protections anti-bot/SSL.\n\n"
                            "Je te propose les meilleurs résultats déjà trouvés plutôt que de boucler."
                        )
                        if last_search_obs:
                            message += f"\n\n{last_search_obs}"

                        self._mark_task_failed("web_fetch_fail_streak")
                        return message
                else:
                    web_fetch_fail_streak = 0

                # --- Guard: detect repeated list_directory on same path ---
                if action.tool_name == "list_directory":
                    listed_path = str(action.tool_args.get("path", "")).strip().lower()
                    if listed_path in _listed_dirs:
                        # Vérifier si des outils mutatifs ont déjà réussi (création déjà faite)
                        _write_tools = {
                            "write_file", "edit_file", "create_project", "create_skill",
                            "create_pdf", "create_docx", "create_xlsx", "create_pptx",
                            "generate_studio_document", "generate_studio_documents",
                            "website_build", "generate_website", "write_website_files",
                            "edit_website",
                        }
                        _already_created = any(
                            h.action.tool_name in _write_tools
                            and h.observation and h.observation.success
                            for h in self.history
                        )
                        # Lot RF-9c : la DECISION (quel rappel poser) est
                        # deplacee vers `observation_synthesis.py` ; la
                        # concatenation et le journal restent ici.
                        observation.content += repeated_listing_reminder(
                            _already_created, original_query,
                        )
                        logger.warning(f"Repeated list_directory on: {listed_path}")
                    _listed_dirs.add(listed_path)

                # --- Guard: detect write_file after "not found" (anti-hallucination) ---
                if action.tool_name == "write_file":
                    # Compteur proactif : nudge vers generate_website après 2+ writes web
                    _wf_path_str = str(action.tool_args.get("path", "") or "")
                    if any(_wf_path_str.endswith(ext) for ext in ('.html', '.css', '.js')):
                        _web_writes_count += 1
                        if _web_writes_count >= 2:
                            observation.content = (observation.content or "") + (
                                "\n\n💡 Tu écris plusieurs fichiers web individuellement. "
                                "L'outil `generate_website` peut créer un site complet "
                                "(HTML+CSS+JS) en un seul appel, avec validation intégrée. "
                                "Utilise-le plutôt que des write_file séparés."
                            )
                if action.tool_name == "write_file" and len(self.history) >= 1:
                    recent_obs = [
                        h.observation.content.lower()
                        for h in self.history[-3:]
                        if h.observation and h.observation.content
                    ]
                    not_found_signals = ("non trouvé", "pas trouvé", "not found", "aucun fichier")
                    had_not_found = any(
                        sig in obs for obs in recent_obs for sig in not_found_signals
                    )
                    if had_not_found:
                        observation.content += (
                            "\n\n⚠️ ATTENTION: Tu viens de CREER un fichier alors que les etapes precedentes "
                            "indiquaient 'non trouve'. Si l'utilisateur demandait de TROUVER ou ENVOYER un fichier "
                            "(pas d'en creer un), tu aurais du repondre honnetement avec ACTION: FINAL."
                        )
                        logger.warning("write_file after not_found detected — possible hallucination")

                # Injection guidance anti-boucle lente (fenêtre 10 actions)
                if self._pending_loop_guidance:
                    observation.content = (observation.content or "") + "\n\n" + self._pending_loop_guidance
                    logger.debug("⚠️ Guidance anti-boucle injectée dans observation")
                    self._pending_loop_guidance = None

                _mcp_loop_guidance = _phase27_mcp_observation_guidance(
                    action.tool_name,
                    observation.content or "",
                )
                if _mcp_loop_guidance:
                    observation.content = (observation.content or "") + "\n\n" + _mcp_loop_guidance

                # FIX: Supprimé le '...' trompeur qui faisait croire au LLM que le contenu était tronqué
                obs_preview = observation.content[:500]
                logger.debug(f"Observation: {obs_preview}{'[...log truncated]' if len(observation.content) > 500 else ''}")
                self._last_observation_monotonic = perf_counter()
                self._last_observation_tool = action.tool_name or ""

                # ── Emit file_read events for UI file viewer ──
                if action.tool_name == "read_file":
                    _file_path = (action.tool_args or {}).get("path", "")
                    _obs_text = observation.content or ""
                    # Extraire le nombre de lignes du header (ex: "(lignes 1-100/745)")
                    import re as _re_fr
                    _lines_m = _re_fr.search(r'\(lignes? ([\d-]+/\d+)\)', _obs_text)
                    _lines_info = _lines_m.group(1) if _lines_m else ""
                    _preview = _obs_text[:2000] if len(_obs_text) > 2000 else _obs_text
                    logger.info("[file_read] {}|{}|{}", _file_path, _lines_info, _preview)

                # --- Guard: après échec parallel_tools (args directs), forcer appel direct ---
                if action.tool_name == "parallel_tools" and "args directs" in (observation.content or ""):
                    logger.warning("⚠️ parallel_tools avec args directs — redirect vers appel direct")
                    self.history.append(step)
                    query = (
                        f"Requête originale: {original_query}\n\n"
                        f"Observation: {observation.content}\n\n"
                        "⚠️ parallel_tools a ÉCHOUÉ car tu as envoyé des arguments d'outil directement.\n"
                        "Tu DOIS appeler chaque outil UN PAR UN avec ACTION: discord_send (ou l'outil voulu).\n"
                        "NE TENTE PAS parallel_tools à nouveau.\n"
                        "Exemple:\n"
                        "ACTION: discord_send\n"
                        'ACTION_INPUT: {"channel_name": "💬-général", "content": "Mon message"}'
                    )
                    _finish_iteration(status="ok", summary="parallel_tools_direct_args_redirect")
                    continue

                # --- Guard: après échec parallel_tools, forcer séquentialisation ---
                if action.tool_name == "parallel_tools" and "outil(s) non autorise(s) en parallele" in (observation.content or ""):
                    logger.warning("⚠️ parallel_tools a rejeté des outils non autorisés — injection de guidance séquentielle")
                    # Extraire dynamiquement les outils rejetés depuis le message d'erreur
                    import re as _re
                    _rej_match = _re.search(r"outil\(s\) non autorise\(s\) en parallele: ([^\n.⚠]+)", observation.content or "")
                    _rejected_names = _rej_match.group(1).strip() if _rej_match else "les outils rejetés"
                    _tool_list = [t.strip() for t in _rejected_names.split(",") if t.strip()]
                    _first_tool = _tool_list[0] if _tool_list else "l'outil"
                    _guidance_lines = "\n".join(f"- ACTION: {t}" for t in _tool_list)
                    self.history.append(step)
                    query = (
                        f"Requête originale: {original_query}\n\n"
                        f"Observation: {observation.content}\n\n"
                        f"⚠️ parallel_tools a ÉCHOUÉ car {_rejected_names} ne sont PAS autorisés en parallèle.\n"
                        f"Tu DOIS maintenant appeler chaque outil UN PAR UN:\n"
                        f"{_guidance_lines}\n"
                        f"NE TENTE PAS parallel_tools à nouveau. NE VA PAS à FINAL sans avoir RÉELLEMENT exécuté les outils."
                    )
                    _finish_iteration(status="ok", summary="parallel_tools_rejected_sequential_redirect")
                    continue
            
            # M100.4 — a strict local-web failure starts a bounded repair cycle.
            # This runs before plan-stagnation accounting: a real verifier report
            # is a new diagnostic signal, not another identical/no-progress turn.
            if (
                action.tool_name == "browser_verify_local_project"
                and observation is not None
                and self._is_mission_run
                and not self._is_worker_run()  # H4 : périmètre OU parent
            ):
                _runtime_failed = not bool(observation.success)
                self._set_web_runtime_verification_state(
                    failed=_runtime_failed,
                    report=observation.content or "",
                )
                if _runtime_failed:
                    _runtime_shots = int(getattr(self, "_web_runtime_repair_shots", 0) or 0)
                    if _web_runtime_repair_allowed(
                        failed=True,
                        shots=_runtime_shots,
                        iteration=i,
                        max_iterations=self.max_iterations,
                    ):
                        self._web_runtime_repair_shots = _runtime_shots + 1
                        self._iterations_without_progress = 0
                        self._all_session_tools.add(action.tool_name)
                        self.history.append(step)
                        _runtime_target = (
                            (action.tool_args or {}).get("project_dir")
                            or (action.tool_args or {}).get("project_path")
                            or "le dossier web de la mission"
                        )
                        logger.warning(
                            "[M100.4] verification runtime web rouge -> reparation locale {}/2 "
                            "target={} task={}",
                            self._web_runtime_repair_shots,
                            _runtime_target,
                            self.task_id,
                        )
                        query = (
                            f"Requete originale: {original_query}\n\n"
                            "ECHEC D'INTEGRATION WEB REEL detecte par "
                            "`browser_verify_local_project`.\n"
                            f"Dossier cible: {_runtime_target}\n"
                            f"Rapport strict:\n{(observation.content or '')[:5000]}\n\n"
                            "Repare maintenant le CODE PRODUIT local, par petite mutation ciblee. "
                            "Ne modifie pas les tests pour masquer le defaut. Verifie les routes, "
                            "methodes HTTP, selecteurs DOM et erreurs console indiquees. Puis relance "
                            "`browser_verify_local_project` sur le meme dossier. Ne conclus pas avant "
                            "ce nouveau verdict. La reparation est bornee a deux tentatives."
                        )
                        _finish_iteration(status="ok", summary="web_runtime_repair_relaunch")
                        continue

                    # No more repair budget: let the normal finalization path run,
                    # but prevent another generic browser-gate loop. The truth-lock
                    # will expose the persisted strict failure in every FINAL path.
                    self._browser_gate_shots = max(
                        int(getattr(self, "_browser_gate_shots", 0) or 0), 1
                    )
                    logger.warning(
                        "[M100.4] verification runtime web toujours rouge; tentatives "
                        "epuisees -> cloture honnete task={}",
                        self.task_id,
                    )

            # 6. Compacter les observations volumineuses avant stockage (anti-context-poisoning)
            # Le modèle a déjà vu l'observation complète — on stocke une version compacte
            # pour que les futures itérations ne soient pas noyées dans du contenu stale.
            # RÈGLE : read_file/grep ont un seuil élevé (8000) — le contenu fichier est précieux.
            #         delegate_task/run_command ont un seuil bas (3000) — ce sont des résumés.
            if step.observation and step.observation.content:
                # Lot RF-9a : la DECISION de compaction est deplacee vers
                # `observation_synthesis.py` (feuille « ingestion d'observation »,
                # §15). Restent ici les EFFETS : la reconstruction du `ReActStep`
                # et le journal.
                _c_body = compact_observation_body(
                    action.tool_name or "",
                    step.observation.content,
                    getattr(self, "_last_browser_surface", "") in (
                        "chat_composer", "chat_transcript", "chat_response"
                    ),
                    compact_browser=_compact_browser_observation_payload,
                )
                if _c_body is not None:
                    _raw_obs_len = len(step.observation.content)
                    step = ReActStep(
                        thought=step.thought,
                        action=step.action,
                        observation=Observation(
                            content=_c_body,
                            success=step.observation.success,
                            sub_results=getattr(step.observation, "sub_results", ()),
                            origin=getattr(step.observation, "origin", "tool"),
                        ),
                    )
                    logger.debug(
                        f"🗜️ Observation compactée: {_raw_obs_len} → {len(_c_body)} chars "
                        f"({action.tool_name or ''})"
                    )
            # 6. Ajouter à l'historique
            # Accumuler le nom de l'outil dans le set session (survit aux compactions)
            if action.tool_name:
                self._all_session_tools.add(action.tool_name)
                # N'ajouter aux outils réussis que si l'observation indique un succès réel
                if observation.success:
                    self._successful_session_tools.add(action.tool_name)
                    # parallel_tools agrège des sous-outils : propager les sous-outils
                    # RÉUSSIS (format obs « ✅ N. <tool>: … ») — sinon le guard
                    # anti-hallucination ne voit que « parallel_tools » et croit que
                    # mail_send/telegram_send_document n'ont pas tourné → faux positif
                    # → double-envoi (cf log 21/06).
                    if action.tool_name == "parallel_tools" and observation.content:
                        for _sub in re.findall(r"✅\s*\d+\.\s*([A-Za-z_]\w*)", observation.content):
                            self._successful_session_tools.add(_sub)
            self.history.append(step)

            # Mission artifacts must be visible before run_mission returns: the
            # lead may publish during this same loop. Only explicit file-producing
            # tools and existing paths under workspace are persisted.
            if self._is_mission_run and observation.success and self._orchestrator_enabled():
                try:
                    from src.runtime.peer_artifacts import persist_created_files
                    from src.utils.paths import WORKSPACE_DIR
                    _new_artifacts = persist_created_files(
                        self.task_orchestrator,
                        self.task_id,
                        [step],
                        base_dir=WORKSPACE_DIR,
                    )
                    if _new_artifacts:
                        logger.info(
                            "[MISSION ARTIFACTS] {} new artifact(s) persisted for {}",
                            len(_new_artifacts), self.task_id,
                        )
                except Exception as _artifact_exc:
                    logger.debug("[MISSION ARTIFACTS] persistence skipped: {}", _artifact_exc)

            _document_manifest_progress = False
            _document_workflow_progress = False
            if self._task_plan:
                try:
                    self._reconcile_document_plan_from_manifest(i)
                    self._reconcile_document_catalog_plan(i)
                    self._reconcile_document_workflow_plan(i)
                    from src.documents.delivery_manifest import (
                        manifest_has_new_proof,
                        manifest_progress_signature,
                        workflow_has_new_proof,
                    )
                    _manifest, _missing, _unverified = self._structured_document_delivery_manifest()
                    _current_manifest_signature = manifest_progress_signature(_manifest)
                    _document_manifest_progress = manifest_has_new_proof(
                        getattr(self, "_last_document_manifest_signature", ()),
                        _current_manifest_signature,
                    )
                    self._last_document_manifest_signature = _current_manifest_signature
                    _current_workflow_signature = self._document_workflow_progress_signature()
                    _document_workflow_progress = workflow_has_new_proof(
                        getattr(
                            self, "_last_document_workflow_progress_signature",
                            (0, "", "", "", "", ()),
                        ),
                        _current_workflow_signature,
                    )
                    self._last_document_workflow_progress_signature = (
                        _current_workflow_signature
                    )

                except Exception as _doc_plan_exc:
                    logger.debug("[PLAN DOCUMENT] reconciliation ignoree: {}", _doc_plan_exc)

            # Receipt persistence is a workflow invariant, not a plan-LLM
            # feature. Keep it active even when the model emits no TODO list.
            if action.tool_name in {
                "generate_studio_document", "generate_studio_documents",
            } and observation.success:
                _reference_id = self._ensure_document_delivery_reference()
                _pending_workflow = self._document_workflow_pending_action()
                if (
                    _reference_id
                    and getattr(_pending_workflow, "operation", "") == "open"
                    and not getattr(self, "_document_reference_announced", False)
                ):
                    self._document_reference_announced = True
                    step.observation.content += (
                        "\n\nWORKFLOW DOCUMENTAIRE: la livraison exacte est complete. "
                        "Ouvre maintenant son recu agrege avec "
                        f"`open_document_delivery(receipt_id='{_reference_id}')`, "
                        "puis poursuis la revision et la verification demandees."
                    )

            # 6.1 Guard: progression du plan TODO
            if self._task_plan:
                completed_count = sum(1 for t in self._task_plan if t.completed)
                if completed_count == self._last_completed_task_count:
                    # PG-1.a (run SkiLoc) : une MUTATION REUSSIE est une progression
                    # REELLE meme si le plan ne bouge pas (taches restantes souvent
                    # verrouillees PUBLISH-ONLY/BROWSER-ONLY pendant le debogage) :
                    # compteur remis a ZERO au lieu de compter le travail reel
                    # comme de la stagnation.
                    _pg1_mutation_ok = bool(
                        step.action
                        and (step.action.tool_name or "") in _PG1_MUTATION_TOOLS
                        and step.observation
                        and step.observation.success
                    )
                    if (
                        _pg1_mutation_ok
                        or _document_manifest_progress
                        or _document_workflow_progress
                    ):
                        self._iterations_without_progress = 0
                    else:
                        self._iterations_without_progress += 1
                        # Outil réussi (✅) = progression partielle, ralentir le compteur
                        if step.observation and step.observation.content and "\u2705" in step.observation.content:
                            self._iterations_without_progress = max(0, self._iterations_without_progress - 1)
                else:
                    self._iterations_without_progress = 0
                    self._last_completed_task_count = completed_count

                # Seuil dynamique: plans avec navigateur browser ou debug/test ont besoin de plus d'espace
                _has_browser = any(
                    h.action and h.action.tool_name
                    and h.action.tool_name.startswith("browser_")
                    for h in self.history
                )
                _has_debug = any(
                    h.action and h.action.tool_name
                    and h.action.tool_name in ("test_and_fix", "run_command", "edit_file", "grep_search")
                    for h in self.history
                )
                _needs_more_space = _has_browser or _has_debug
                # Fix E: Augmenter le seuil browser à 20 pour laisser le temps de changer de stratégie
                # (ex: Mistral bloque après 2 échanges → le LLM doit naviguer vers HuggingFace Chat)
                _guard_limit = 20 if _has_browser else (16 if _needs_more_space else 10)
                _warn_limit = 15 if _has_browser else (12 if _needs_more_space else 7)

                # Fix E: Réinitialiser le compteur si une navigation réussie vers une nouvelle URL
                # (changement de domaine = nouvelle stratégie = progression réelle)
                if (
                    _has_browser
                    and step.action
                    and step.action.tool_name == "browser_navigate"
                    and step.observation
                    and "✅" in (step.observation.content or "")
                    and self._iterations_without_progress > 0
                ):
                    # Navigation réussie vers un nouveau site = reset du compteur de stagnation
                    _nav_url = str((step.action.tool_args or {}).get("url", "")).lower()
                    _prev_urls = [
                        str((h.action.tool_args or {}).get("url", "")).lower()
                        for h in self.history[-8:]
                        if h.action and h.action.tool_name == "browser_navigate"
                    ]
                    # Si l'URL est différente des 8 dernières navigations → nouvelle stratégie
                    if _nav_url and _nav_url not in _prev_urls[:-1]:
                        logger.debug(
                            "[PLAN GUARD] Navigation vers nouveau site '{}' — reset compteur stagnation",
                            _nav_url[:60],
                        )
                        self._iterations_without_progress = 0

                if self._iterations_without_progress >= _guard_limit:
                    # PG-1.b (run SkiLoc) — en MISSION avec budget confortable et
                    # tests présents, UN sauvetage dirigé avant de couper : le
                    # FINAL forcé court-circuitait le PYTEST GATE et a tué SkiLoc
                    # avec 2 048 s de budget, à une itération de la victoire.
                    # Helper pur, 1 seul tir, hors mission strictement inchangé.
                    if self._is_mission_run and not getattr(self, "_no_progress_rescue_used", False):
                        try:
                            from src.subagents.mission_budget import (
                                mission_budget, no_progress_rescue_allowed,
                            )
                            _bud_pg1 = {}
                            if self._orchestrator_enabled():
                                _bud_pg1 = mission_budget(
                                    self.task_orchestrator.get_task(self.task_id) or {}
                                )
                            _tests_pg1 = self._mission_tests_present_for_gate()
                            if no_progress_rescue_allowed(
                                is_mission=True,
                                tests_present=bool(_tests_pg1),
                                gate_shots=getattr(self, "_pytest_gate_shots", 0),
                                remaining_s=_bud_pg1.get("remaining_s"),
                                ratio_used=_bud_pg1.get("ratio_used"),
                                already_rescued=False,
                            ):
                                self._no_progress_rescue_used = True
                                self._iterations_without_progress = 0
                                logger.warning(
                                    "[PLAN GUARD PG-1] stagnation en mission mais budget "
                                    "confortable et mutations réelles → sauvetage dirigé "
                                    "(unique). task={}", self.task_id)
                                query = (
                                    f"Requête originale: {original_query}\n\n"
                                    "🛟 STOP diagnostic — tu tournes en rond alors que ton "
                                    "budget est confortable et que tes corrections sont déjà "
                                    "écrites. Enchaîne MAINTENANT, dans cet ordre : "
                                    "1) relance `python -m pytest` depuis le dossier de la "
                                    "mission ; 2) si vert, publie via publish_mission_workspace ; "
                                    "3) PUIS conclus honnêtement. Aucune relecture supplémentaire."
                                )
                                _finish_iteration(status="ok", summary="plan_no_progress_rescue")
                                continue
                        except Exception as _pg1_exc:
                            logger.debug("[PLAN GUARD PG-1] rescue skip: {}", _pg1_exc)
                    logger.warning("[PLAN GUARD] Aucune progression en {} iterations, FINAL force", _guard_limit)
                    _finish_iteration(status="error", error=f"plan_no_progress_{_guard_limit}_iter")
                    done_desc = ", ".join(t.description for t in self._task_plan if t.completed)
                    # Inclure le dernier resultat d'outil si positif
                    last_obs_ctx = ""
                    if step.observation and step.observation.content and "\u2705" in step.observation.content:
                        last_obs_ctx = "\n\n" + step.observation.content[:500]
                    message = (
                        "⚠️ Je n'ai pas pu progresser sur mon plan. "
                        f"Voici ce que j'ai accompli : {done_desc}" if done_desc
                        else "⚠️ Je n'ai pas pu avancer sur le plan de travail."
                    )
                    message += last_obs_ctx
                    self._mark_task_failed(f"plan_no_progress_{_guard_limit}_iter")
                    return message

                if self._iterations_without_progress >= _warn_limit:
                    # Lot RF-9d : la DECISION sort ; la reconstruction de
                    # l'Observation reste ici (invariant 5).
                    plan_stag_msg = plan_stagnation_message(self._task_plan)
                    if step.observation:
                        step.observation = Observation(
                            content=(step.observation.content or "") + plan_stag_msg,
                            success=step.observation.success,
                            sub_results=getattr(step.observation, "sub_results", ()),
                            origin=getattr(step.observation, "origin", "tool"),
                        )

            # 7. Mettre à jour la requête avec l'observation (plus de contexte)
            obs_text = step.observation.content[:2000] if step.observation else "Pas d'observation"  # Augmenté

            if (
                action.tool_name == "write_file"
                and step.observation
                and not step.observation.success
                and (
                    "patch strict" in step.observation.content.lower()
                    or "fichier existant" in step.observation.content.lower()
                    or "fichier existe" in step.observation.content.lower()
                )
            ):
                query = (
                    f"Requête originale: {original_query}\n"
                    f"Observation: {obs_text}\n\n"
                    "Le fichier existe déjà. Action suivante obligatoire: utilise edit_file ou apply_patch "
                    "avec modification ciblée (pas write_file)."
                )
                _finish_iteration(status="ok", summary="write_file_to_patch_fallback")
                continue

            if action.tool_name == "read_file" and step.observation and "[...SUITE DISPONIBLE:" in step.observation.content:
                path_for_next = action.tool_args.get("path", "")
                current_end = action.tool_args.get("end_line")
                try:
                    current_end_int = int(current_end) if current_end is not None else 1000
                except Exception:
                    current_end_int = 1000
                next_start = current_end_int + 1
                next_end = next_start + 999
                query = (
                    f"Requête originale: {original_query}\n"
                    f"Observation de l'action précédente ({action.tool_name}): {obs_text}\n\n"
                    f"Le fichier est partiel. Continue la lecture avec read_file(path='{path_for_next}', "
                    f"start_line={next_start}, end_line={next_end}) ou passe à l'action suivante si le contexte est suffisant."
                )
                _finish_iteration(status="ok", summary="continue_paginated_read")
                continue

            # Pour les projets web, rappeler les fichiers créés et restants
            files_reminder = ""
            is_web_request = False
            web_request_checker = getattr(self, "_is_web_request", None)
            if callable(web_request_checker):
                try:
                    is_web_request = bool(web_request_checker(original_query))
                except Exception:
                    is_web_request = bool(ReActLoop._is_web_request(original_query))
            else:
                is_web_request = bool(ReActLoop._is_web_request(original_query))

            if is_web_request:
                # Lot RF-9d : la DECISION sort. `has_html/css/js` sont RENDUS et
                # non absorbes : ils sont relus ~700 lignes plus bas pour
                # adapter le hint de conclusion.
                _web_written = [
                    h.action.tool_args.get("path", "")
                    for h in self.history if h.action.tool_name == "write_file"
                ]
                has_html, has_css, has_js = web_files_present(_web_written)
                files_reminder = web_files_reminder(_web_written)
            
            # ── Post-succès create_project web : vérification runtime autonome ──
            _tool_args_for_project = action.tool_args if isinstance(action.tool_args, dict) else {}
            _is_create_project_web_success = (
                action.tool_name == "create_project"
                and observation.success
                and obs_text
                # 2.6.3 (run MiniQuiz §5) — JAMAIS en mission : la preuve navigateur
                # appartient au LEAD (serve_website + BROWSER GATE + truth-lock).
                and self._post_delegate_web_verify_allowed()
                and _looks_like_web_delegate_delivery(original_query, _tool_args_for_project, obs_text)
            )
            if _is_create_project_web_success:
                _web_project_path = _extract_existing_web_project_path(
                    _tool_args_for_project,
                    obs_text,
                    base_dir=Path.cwd(),
                )
                if _web_project_path is None:
                    query = (
                        f"Requête originale: {original_query}\n\n"
                        f"`create_project` a terminé avec succès :\n{obs_text[:2600]}\n\n"
                        "Le résultat ressemble à un projet web, mais aucun dossier projet existant "
                        "n'a été retrouvé dans le rapport. Ne finalise pas encore : retrouve le "
                        "dossier exact du projet, puis appelle `browser_verify_local_project` sur "
                        "ce dossier. Si la vérification échoue, appelle `delegate_task` pour corriger."
                    )
                    self._after_delegate_success = False
                    _finish_iteration(status="ok", summary="create_project_web_verify_needs_path")
                    continue
                try:
                    from src.tools.web_project_runtime_verifier import verify_web_project_runtime

                    _runtime_result = await verify_web_project_runtime(
                        _web_project_path,
                        expect_canvas=_delegate_delivery_expects_canvas(
                            original_query,
                            _tool_args_for_project,
                            obs_text,
                        ),
                    )
                    _runtime_report = _runtime_result.to_report(max_chars=5000)
                except Exception as _runtime_exc:
                    _runtime_result = None
                    _runtime_report = (
                        "Runtime verifier exception: "
                        f"{type(_runtime_exc).__name__}: {str(_runtime_exc)[:400]}"
                    )
                if not _runtime_result or not _runtime_result.passed:
                    query = _build_post_delegate_web_verify_failure_query(
                        original_query,
                        _web_project_path,
                        obs_text,
                        _runtime_report,
                    )
                    self._after_delegate_success = False
                    _finish_iteration(status="ok", summary="create_project_web_verify_failed")
                    continue
                self._update_plan_progress(
                    "browser_verify_local_project",
                    {"project_path": str(_web_project_path)},
                    _runtime_report,
                    i,
                )
                self._mark_web_runtime_plan_verified(i)
                _pending_business = self._pending_delegate_success_business_tasks()
                if _pending_business:
                    query = _build_post_delegate_continue_query(
                        original_query,
                        obs_text,
                        [t.description for t in _pending_business],
                        _runtime_report,
                    )
                    self._after_delegate_success = False
                else:
                    query = _build_post_delegate_web_verify_success_query(
                        original_query,
                        obs_text,
                        _runtime_report,
                    )
                    self._after_delegate_success = True
                _finish_iteration(status="ok", summary="create_project_web_verify_ok")
                continue

            # ── Post-succès create_mission : ACCUSÉ RÉCEPTION direct (déterministe) ──
            # La création de mission n'a AUCUNE mutation à « raconter » au tour suivant :
            # demander un FINAL au LLM ici déclenche le THOUGHT-leak régime A → 3 repairs →
            # final vide → fallback « Je n'ai pas trouvé de réponse pertinente. » (≈6375).
            # On rend un accusé STABLE, pré-rendu, sans round-trip LLM.
            # LOT 2.7 — l'ACK force-final est réservé au CHAT : dans une mission, il
            # tuait le lead avant contrat/delegate/tests (run NoteFlash 2026-07-02).
            # (create_mission est de toute façon REFUSÉ en mission désormais — ceinture
            # et bretelles si un chemin de création légitime réapparaissait.)
            if action.tool_name == "create_mission" and observation.success \
                    and not self._is_mission_run \
                    and "Mission lancée en arrière-plan" in (obs_text or ""):
                import re as _re_cm
                _m_cm = _re_cm.search(r"id:\s*(task_\w+)", obs_text or "")
                _mid_cm = _m_cm.group(1) if _m_cm else None
                # LOT M3 (demande utilisateur 2026-08-14) — l'accusé n'est plus une
                # phrase figée : il dit ce qu'elle a RETENU et comment elle va s'y
                # prendre. Toujours déterministe (aucun tour LLM) : le régime A
                # existe parce que DeepSeek leake son THOUGHT en réponse finale.
                # Il ne prétend RIEN sur les sous-agents : à cet instant le contrat
                # n'est pas posé et aucun worker n'existe.
                _args_cm = action.tool_args or {}
                try:
                    from src.subagents.mission_ack import build_mission_ack
                    from .final_guards import objective_requires_contract_protocol

                    _obj_cm = str(_args_cm.get("objective") or "")
                    _ack = build_mission_ack(
                        objective=_obj_cm,
                        mission_id=_mid_cm or "",
                        deadline=str(_args_cm.get("deadline") or ""),
                        multi_worker=bool(
                            objective_requires_contract_protocol(_obj_cm)
                            or objective_requires_contract_protocol(original_query or "")
                        ),
                    )
                except Exception as _e_ack:
                    logger.debug("[MISSION ACK] accusé enrichi indisponible: {}", _e_ack)
                    _ack = (
                        "✨ C'est lancé ! La mission tourne en arrière-plan"
                        + (f" (id : `{_mid_cm}`)" if _mid_cm else "")
                        + ".\n\nTu peux continuer à me parler — demande-moi l'avancement quand tu "
                        "veux (« alors, ça avance ? ») ou le résultat final."
                    )
                logger.info(
                    "[MISSION ACK] accusé réception direct (id={}) — pas de FINAL LLM "
                    "(anti thought-leak régime A).", _mid_cm)
                _finish_iteration(status="ok", summary="create_mission_direct_ack")
                return self._stream_and_return_final(_ack)

            # Le reçu a déjà ouvert les chemins exacts. Retour direct afin d'éviter
            # un tour LLM supplémentaire et les repairs THOUGHT vus sur « ouvre-les ».
            if action.tool_name == "open_document_delivery" and observation.success:
                try:
                    from src.documents.delivery_receipt import build_open_delivery_final

                    # `step.observation` may already be compacted for the LLM;
                    # the raw tool observation is still available here and is
                    # the authoritative JSON payload.
                    _open_payload = self._document_open_payload(observation)
                    if _open_payload is None:
                        raise ValueError("open_document_delivery returned invalid JSON")
                    _open_final = build_open_delivery_final(_open_payload)
                    _pending_after_open = self._document_workflow_pending_action()
                    if _pending_after_open is None:
                        logger.info(
                            "[DOCUMENT DELIVERY OPEN] retour direct exact receipt={} opened={}/{}",
                            _open_payload.get("receipt_id"), _open_payload.get("opened"),
                            _open_payload.get("requested"),
                        )
                        _finish_iteration(status="ok", summary="document_delivery_open_direct_final")
                        return self._stream_and_return_final(_open_final)
                    logger.info(
                        "[DOCUMENT WORKFLOW] ouverture receipt={} opened={}/{}; "
                        "suite={} conservee dans ReAct",
                        _open_payload.get("receipt_id"), _open_payload.get("opened"),
                        _open_payload.get("requested"),
                        getattr(_pending_after_open, "operation", "unknown"),
                    )
                except Exception as _open_exc:
                    logger.debug("[DOCUMENT DELIVERY OPEN] direct final skip: {}", _open_exc)

            # ── 5.7.4a — preuve de livraison : un write_file réussi sur le FICHIER
            # CIBLE de la mission pose `deadline_artifact_written`. Le PLAN GUARD
            # pourra alors autoriser un FINAL propre (sortie nette après partiel écrit,
            # au lieu de boucler jusqu'au cancel). On exige la VRAIE écriture, pas le
            # simple flag de steer (posé en amont, avant l'écriture).
            if (action.tool_name == "write_file" and observation.success
                    and self._is_mission_run and self._orchestrator_enabled()):
                try:
                    from src.subagents.mission_budget import extract_unambiguous_target_file as _etf
                    _recw = self.task_orchestrator.get_task(self.task_id) or {}
                    _tgt = _etf((_recw.get("metadata") or {}).get("objective")
                                or _recw.get("message_preview") or "")
                    if _tgt:
                        import os as _osw
                        _written_path = ((action.tool_args or {}).get("path", "") or "").lower()
                        _base = _osw.path.basename(_tgt).lower()
                        if _base and (_base in _written_path or _base in (obs_text or "").lower()):
                            self.task_orchestrator.set_task_metadata(
                                self.task_id, deadline_artifact_written=True)
                            # Mémorise le résumé d'écriture (chemin + taille) pour la
                            # finalisation déterministe ci-dessous (sans round-trip LLM).
                            self._mission_artifact_summary = (obs_text or "").strip()[:300]
                            # Basename cible mémorisé → sert à détecter la RELECTURE de
                            # l'artefact au point de finalisation (Cas 2, même si plan vide).
                            self._mission_target_base = _base
                            # Sanity-check qualité : le content écrit porte-t-il les
                            # stigmates d'un parsing pollué (`"` de tête, queue force_rewrite) ?
                            # → on ne clamera pas « vérifié » (message honnête, cf. finalize).
                            self._mission_artifact_malformed = deliverable_looks_malformed(
                                ((action.tool_args or {}).get("content", "") or ""))
                            logger.info(
                                "[5.7.4a] artefact cible écrit ({}) → sortie FINAL autorisée "
                                "task={}", _base, self.task_id)
                            # Réconciliation plan : le livrable CIBLE est sur disque →
                            # créditer les tâches de délégation (si délégation réussie) et
                            # de récupération/fusion (le livrable les prouve). La vérif reste
                            # au crédit réel par relecture (#3) ; side-effects externes exclus.
                            try:
                                _has_deleg = (
                                    self.execution_ledger.has_successful_action("delegate_and_wait")
                                    or self.execution_ledger.has_successful_action("delegate_task")
                                )
                                if reconcile_plan_on_artifact_delivery(
                                    self._task_plan, has_delegation_success=_has_deleg, iteration=i,
                                ):
                                    self._emit_plan_state(context_tool="write_file")
                            except Exception:
                                pass
                except Exception:
                    pass

            # ── Finalisation DÉTERMINISTE du LEAD (anti thought-leak post-complétion) ──
            # Quand le livrable CIBLE est sur disque ET que le plan métier est ENTIÈREMENT
            # terminé (toutes tâches completed → relecture/vérif faite cf. #3, ou pas de
            # vérif au plan ; et AUCUNE tâche à effet de bord laissée ouverte), on CONCLUT
            # sans FINAL LLM. Sinon DeepSeek leake son raisonnement (≈3 repairs, ~30 s) et
            # retarde le `done` → mission_status « en cours » alors que le fichier existe.
            # Mirror déterministe de MISSION ACK / MISSION DELIVERY.
            if (observation.success and self._is_mission_run and self._orchestrator_enabled()
                    and not getattr(self, "_mission_det_finalized", False)):
                try:
                    _recf = self.task_orchestrator.get_task(self.task_id) or {}
                    _meta_completion = _recf.get("metadata") or {}
                    _completion = self._mission_completion_evidence()
                    _artifact_ok = bool(
                        _meta_completion.get("deadline_artifact_written")
                        or _meta_completion.get("mission_published")
                        or self.execution_ledger.has_published()
                    )
                    # Cas 2 : la relecture de l'artefact CIBLE est une preuve de vérif
                    # indépendante du plan (worker sans plan / verify non crédité).
                    _tgt_base = getattr(self, "_mission_target_base", "") or ""
                    _reread_now = bool(
                        _tgt_base
                        and action.tool_name in ("read_file", "read_document")
                        and observation.success
                        and _tgt_base in ((action.tool_args or {}).get("path", "") or "").lower()
                    )
                    _legacy_finalizable = mission_deliverable_finalizable(
                        self._task_plan,
                        artifact_written=_artifact_ok,
                        target_reread=_reread_now,
                    )
                    if _legacy_finalizable or _completion.get("complete"):
                        self._mission_det_finalized = True
                        _note = getattr(self, "_mission_artifact_summary", "") or ""
                        if _completion.get("scope") == "worker":
                            _worker_files = list(_completion.get("assigned_files") or [])
                            _latest_worker_test = self.execution_ledger.last_test_outcome() or {}
                            if not _latest_worker_test:
                                _latest_worker_test = _meta_completion.get("last_test_outcome") or {}
                            _note = (
                                f"Worker: {len(_worker_files)}/{len(_worker_files)} fichiers "
                                "assignes remplis"
                            )
                            if _completion.get("tests_required"):
                                _note += (
                                    f" ; pytest vert ({int(_latest_worker_test.get('passed') or 0)} passed)"
                                )
                            _note += "."
                        if not _note and _meta_completion.get("published_workspace"):
                            _note = (
                                f"Livrable publie dans "
                                f"{_meta_completion.get('published_workspace')} "
                                f"({_meta_completion.get('published_files') and len(_meta_completion.get('published_files')) or 0} fichiers)."
                            )
                        # C — honnêteté : « vérifié » SEULEMENT si le sanity-check est passé.
                        _malformed = bool(getattr(self, "_mission_artifact_malformed", False))
                        # Titre du livrable (1er H1 Markdown relu) → message informatif SANS LLM.
                        _title = ""
                        _tm = re.search(r'(?m)^\s*#\s+(.+)$', obs_text or "")
                        if _tm:
                            _title = _tm.group(1).strip()[:120]
                        # Vérité de finalisation câblée sur le LEDGER : le chemin
                        # déterministe passe par la MÊME vérité que le FINAL LLM.
                        # Cf. run taskflow : « produit et vérifié 🎉 » alors que le
                        # ledger portait 7 tests rouges.
                        try:
                            _o_led = self.execution_ledger.last_test_outcome()
                            _has_green = self._current_green_test_proof()
                        except Exception:
                            _o_led, _has_green = None, False
                        _test_ran_not_green = bool((_o_led or {}).get("is_test_cmd")) and not _has_green
                        # P0.2 (cf. run PollApp multi-worker) : une suite de tests peut
                        # EXISTER sur disque (écrite par un worker → hors ledger du lead)
                        # alors que le lead n'a lancé aucun pytest. Dans ce cas le lead ne
                        # doit pas dire « vérifié structurellement ». Scan RESTREINT aux
                        # DOSSIERS D'ARTEFACTS de la mission (jamais large → sinon on verrait
                        # les tests de Lumena et on rétrograderait à tort).
                        _tests_expected_not_run = False
                        if (not _has_green and not _test_ran_not_green
                                and _completion.get("scope") != "worker"):
                            try:
                                import os as _os_p02
                                from src.reasoning.test_proof import (
                                    tests_present_in_dir as _tests_in_dir,
                                    tests_present_in_contract as _tests_in_contract,
                                )
                                _meta_f = _recf.get("metadata") or {}
                                _arts = _meta_f.get("artifacts") or []
                                _dirs = {
                                    _os_p02.path.dirname(str(_a))
                                    for _a in _arts if _a and _os_p02.path.dirname(str(_a))
                                }
                                # LOT 2.5 — le dossier de MISSION (2.1) est la source
                                # déterministe : les tests des workers n'apparaissent pas
                                # dans les artefacts du lead (delegate ne les persiste pas).
                                _mission_dir_25 = None
                                _mws_25 = str(_meta_f.get("mission_workspace") or "").strip()
                                if _mws_25:
                                    from src.utils.paths import WORKSPACE_DIR as _WS_25
                                    _mission_dir_25 = str(_WS_25 / _mws_25)
                                    _dirs.add(_mission_dir_25)
                                _tests_expected_not_run = any(_tests_in_dir(_d) for _d in _dirs)
                                # LOT 2.5 — source CONTRACTUELLE : contract.json déclare les
                                # fichiers (plus fiable que le disque — couvre un test promis
                                # par le contrat mais pas encore écrit au moment du scan).
                                if not _tests_expected_not_run and _mission_dir_25:
                                    _cj_25 = _os_p02.path.join(_mission_dir_25, "contract.json")
                                    if _os_p02.path.isfile(_cj_25):
                                        import json as _json_25
                                        with open(_cj_25, encoding="utf-8", errors="replace") as _fh_25:
                                            _tests_expected_not_run = _tests_in_contract(
                                                _json_25.load(_fh_25))
                            except Exception:
                                _tests_expected_not_run = False
                        # C0.4 (run FrigoZen) — FINALIZE branché sur le PYTEST GATE :
                        # des tests existent pour la mission et AUCUN pytest n'a tourné
                        # dans ce run → PAS de clôture avec bannière « non exécutés »
                        # tant que le gate a des tirs et qu'il reste des itérations.
                        # Relance dirigée (même filet que le FINAL LLM, cf. 2.10) — le
                        # lead FrigoZen a été coupé avec 23 min de budget, sans pytest,
                        # sans navigateur, sans publication.
                        _gate_shots_det = getattr(self, "_pytest_gate_shots", 0)
                        _defer_to_pytest_gate = bool(
                            _tests_expected_not_run
                            and _gate_shots_det < 2
                            and i < self.max_iterations - 2
                        )
                        if _defer_to_pytest_gate:
                            self._mission_det_finalized = False
                            self._pytest_gate_shots = _gate_shots_det + 1
                            self._pytest_gate_relaunched = True
                            logger.warning(
                                "[PYTEST GATE] FINALIZE déterministe intercepté : tests "
                                "présents sans aucun run pytest → relance dirigée {}/2. "
                                "task={}", self._pytest_gate_shots, self.task_id)
                            try:
                                observation.content = (
                                    str(observation.content or "")
                                    + "\n\n⛔ STOP — ne conclus PAS encore : des tests "
                                    "existent pour cette mission et AUCUN pytest n'a "
                                    "tourné dans ce run. Lance MAINTENANT `python -m "
                                    "pytest tests/ -v` via run_command (cwd = dossier de "
                                    "la mission). S'ils sont rouges, corrige LE CODE par "
                                    "MUTATION (edit_file) puis relance. Ensuite publie le "
                                    "livrable avec publish_mission_workspace et conclus "
                                    "honnêtement."
                                )
                            except Exception:
                                pass
                        # LOT 2.7 (run Converto) — FINALIZE branché sur le BROWSER
                        # GATE (patron C0.4) : Converto est sorti par plan_complet
                        # (étape navigateur créditée à tort) AVANT de servir — le
                        # gate navigateur ne vivait que sur la voie FINAL LLM.
                        elif (i < self.max_iterations - 2
                              and self._finalize_browser_gate_pending(_note, original_query)):
                            _web_where_det = self._finalize_browser_gate_pending(
                                _note, original_query)
                            self._mission_det_finalized = False
                            self._browser_gate_shots = getattr(
                                self, "_browser_gate_shots", 0) + 1
                            self._browser_gate_relaunched = True
                            logger.warning(
                                "[BROWSER GATE] FINALIZE déterministe intercepté : "
                                "livrable web sans action navigateur ({}) → relance "
                                "dirigée 1/1. task={}", _web_where_det, self.task_id)
                            try:
                                if self._truth_lock_interaction_flag():
                                    observation.content = (
                                        str(observation.content or "")
                                        + "\n\n⛔ STOP — livrable WEB interactif sans preuve "
                                        "stricte. Appelle `browser_verify_local_project` sur le "
                                        "dossier publié : il doit remplir les champs, soumettre "
                                        "et constater un changement DOM observable. Corrige puis "
                                        "relance en cas d'échec, ensuite seulement conclus."
                                    )
                                else:
                                    observation.content = (
                                        str(observation.content or "")
                                        + "\n\n⛔ STOP — livrable WEB et AUCUNE action "
                                        "navigateur (browser_*) dans ce run. SERS-le "
                                        "MAINTENANT avec l'outil serve_website("
                                        "directory='<dossier du livrable publié>', port=8081), "
                                        "puis browser_navigate sur l'URL retournée et "
                                        "CONTRÔLE le DOM (le flux demandé), ENFIN conclus "
                                        "honnêtement."
                                    )
                            except Exception:
                                pass
                        elif (i < self.max_iterations - 2
                              and self._finalize_interaction_gate_pending(
                                  _note, original_query
                              )):
                            _interaction_where_det = (
                                self._finalize_interaction_gate_pending(
                                    _note, original_query
                                )
                            )
                            self._mission_det_finalized = False
                            self._interaction_gate_shots = getattr(
                                self, "_interaction_gate_shots", 0
                            ) + 1
                            logger.warning(
                                "[INTERACTION GATE] FINALIZE after browser open ({}) "
                                "-> directed retry {}/{}. task={}",
                                _interaction_where_det,
                                self._interaction_gate_shots,
                                _MAX_INTERACTION_GATE_SHOTS,
                                self.task_id,
                            )
                            try:
                                observation.content = (
                                    str(observation.content or "")
                                    + "\n\nSTOP -- la page est ouverte, mais "
                                    "l'interaction demandee n'est pas encore prouvee. "
                                    "Execute le parcours exact demande (saisies, "
                                    "selection, clic), puis relis l'etat avec "
                                    "browser_dom_state ou browser_evaluate et constate "
                                    "le changement avant/apres. Ne conclus pas sur la "
                                    "seule navigation ou une capture."
                                )
                            except Exception:
                                pass
                        else:
                            # B — message déterministe MAIS chaleureux (voix Lumena), toujours sans LLM.
                            _det_final = build_mission_final_message(
                                _note, _title, malformed=_malformed,
                                has_green_test=_has_green, test_ran_not_green=_test_ran_not_green,
                                tests_expected_not_run=_tests_expected_not_run,
                            )
                            if _tests_expected_not_run:
                                logger.info(
                                    "[MISSION FINALIZE] tests présents mais non passés verts par le "
                                    "lead → PAS de « vérifié structurellement » (P0.2). task={}", self.task_id)
                            # Filet : neutralise tout claim « tests verts / vérifié » résiduel
                            # (ex. dans _note) sans preuve verte au ledger.
                            try:
                                _det_final, _det_lock = apply_mission_truth_lock(
                                    _det_final, has_green_test=_has_green, last_test_outcome=_o_led,
                                    has_browser_proof=self._current_browser_proof(),
                                    # 2.8.2 (run TriboBlog) — HARMONISATION : sans ces
                                    # 2 params, overclaim_delivery/note_tests_not_run
                                    # étaient morts à ce site FINALIZE aussi.
                                    tests_present_not_run=self._tests_present_but_not_run(),
                                    has_any_mutation=self.execution_ledger.has_any_mutation(),
                                    has_published=self.execution_ledger.has_published(),  # LOT E
                                    project_root=Path(__file__).resolve().parents[2],  # 2.11.E
                                    web_deliverable=self._truth_lock_web_flag(),  # M1
                                    file_deliverables_expected=self._mission_expects_file_deliverables(),  # H8
                                    unpublished_writes=self._mission_unpublished_writes(),  # Z24
                                    has_server_started=self._server_started_proof(),  # LOT 2.3
                                    browser_content_seen=self._browser_content_seen(),  # 2.7.4
                                    interaction_proven=self._truth_lock_interaction_proven(),
                                    interaction_required=self._truth_lock_interaction_flag(),
                                    objective_is_game=self._truth_lock_game_flag(),  # 2.13.A
                                    browser_runtime_failed=self._browser_runtime_failed_for_truth_lock(),  # M100.4
                                )
                                self._note_truth_lock_outcome(_det_lock)  # F1.b
                                if _det_lock.get("changed"):
                                    logger.warning(
                                        "[MISSION TRUTH-LOCK] FINALIZE déterministe rétrogradé "
                                        "honnêtement — détails={} task={}",
                                        {k: v for k, v in _det_lock.items() if v and k != "changed"},
                                        self.task_id)
                            except Exception:
                                pass
                            if _malformed:
                                logger.warning(
                                    "[MISSION FINALIZE] livrable potentiellement mal formé (parser) — "
                                    "message honnête (pas de « vérifié »). task={}", self.task_id)
                            logger.info(
                                "[MISSION FINALIZE] sortie déterministe (artefact écrit ; voie={} ; "
                                "plan={} tâches) — pas de FINAL LLM (anti thought-leak). task={}",
                                "reread" if _reread_now else "plan_complet",
                                len(self._task_plan or []), self.task_id,
                            )
                            try:
                                self.task_orchestrator.set_task_metadata(
                                    self.task_id,
                                    completion_proven=True,
                                    completion_proof=dict(_completion),
                                    terminal_reason_code="completed",
                                    terminal_reason_detail=(
                                        "worker termine: fichiers assignes remplis et preuves requises acquises"
                                        if _completion.get("scope") == "worker"
                                        else "cloture deterministe apres publication et preuves requises"
                                        if _completion.get("complete")
                                        else "cloture deterministe de l'artefact cible"
                                    ),
                                )
                            except Exception as exc:
                                logger.debug("[M106] completion persistence skipped: {}", exc)
                            _finish_iteration(status="ok", summary="mission_artifact_deterministic_final")
                            return self._stream_and_return_final(_det_final)
                except Exception:
                    pass

            # ── Post-succès mission_result : retour DIRECT (vrai, sans round-trip LLM) ──
            # Le result_summary du lead EST déjà la réponse finale polie. Lui demander
            # de « reformuler » (chemin générique) déclenche le régime catastrophique du
            # THOUGHT leak (3 repairs + fallback + cruft) à reformuler un texte déjà prêt.
            # On livre tel quel (remask anti-secret préservé).
            if action.tool_name == "mission_result" and observation.success:
                _mission_deliverable = extract_mission_deliverable(obs_text)
                if _mission_deliverable:
                    _mission_deliverable = self._remask_observed_masked_values(_mission_deliverable)
                    # PAS de truth-lock ici (P0.1, 2026-07-02) : DELIVERY RELAIE un
                    # résultat de mission déjà produit. Ce texte est DÉJÀ passé par le
                    # truth-lock à la production (FINAL LLM / FINALIZE déterministe, avec
                    # le ledger de la MISSION). Le re-juger ici avec le ledger du tour de
                    # relais (souvent vide, task≠mission) causait une FAUSSE rétrogradation
                    # d'un vert prouvé (« 16/16 verts » → « tests non exécutés » — run calc).
                    logger.info(
                        "[MISSION DELIVERY] mission_result livré en direct ({} chars) "
                        "— pas de reformulation LLM (anti thought-leak).",
                        len(_mission_deliverable),
                    )
                    _finish_iteration(status="ok", summary="mission_result_direct_final")
                    # skip explicite = seule exemption du chokepoint (relais P0.1).
                    return self._stream_and_return_final(
                        _mission_deliverable, skip_mission_truth_lock=True)

            # ── Post-succès delegate_task : chemin FINAL direct ──
            # Après un delegate_task ✅, le rapport du CodeAgent EST la vérification.
            # On force le chemin FINAL sans repasser par "continue" pour éviter
            # les tours perdus sur thought_leak / reformulation inutile.
            _is_delegate_success = (
                action.tool_name in ("delegate_task", "delegate_task_bg")
                and observation.success           # preuve structurelle, pas juste le badge ✅
                and obs_text
                and (obs_text.lstrip().startswith("✅") or "✅" in obs_text[:60])
                and _delegate_report_has_real_work(action.tool_name or "", obs_text)
            )
            if _is_delegate_success:
                # Réconcilier le plan avant le FINAL — contourne _MAX_COMPLETIONS_PER_CALL=2
                # pour les rapports CodeAgent qui couvrent plusieurs étapes d'un coup
                self._reconcile_plan_from_delegate_success(obs_text, i)
                _tool_args_for_delegate = action.tool_args if isinstance(action.tool_args, dict) else {}
                _web_delivery = (
                    action.tool_name == "delegate_task"
                    # 2.6.3 (run MiniQuiz §5) — JAMAIS en mission (workers NI lead) :
                    # ce vérifieur sert le dossier en statique → 404 structurels sur
                    # une app Flask → boucles de fausses corrections chez les workers.
                    and self._post_delegate_web_verify_allowed()
                    and _looks_like_web_delegate_delivery(original_query, _tool_args_for_delegate, obs_text)
                )
                if _web_delivery:
                    _web_project_path = _extract_existing_web_project_path(
                        _tool_args_for_delegate,
                        obs_text,
                        base_dir=Path.cwd(),
                    )
                    if _web_project_path is None:
                        query = (
                            f"Requête originale: {original_query}\n\n"
                            f"Le CodeAgent a terminé avec succès :\n{obs_text[:2600]}\n\n"
                            "Le résultat ressemble à un projet web, mais aucun project_path existant "
                            "n'a été retrouvé dans le rapport. Ne finalise pas encore : lis le rapport, "
                            "retrouve le dossier exact du projet, puis appelle `browser_verify_local_project` "
                            "sur ce dossier. Si la vérification échoue, appelle `delegate_task` pour corriger."
                        )
                        self._after_delegate_success = False
                        _finish_iteration(status="ok", summary="delegate_task_web_verify_needs_path")
                        continue
                    try:
                        from src.tools.web_project_runtime_verifier import verify_web_project_runtime

                        _runtime_result = await verify_web_project_runtime(
                            _web_project_path,
                            expect_canvas=_delegate_delivery_expects_canvas(
                                original_query,
                                _tool_args_for_delegate,
                                obs_text,
                            ),
                        )
                        _runtime_report = _runtime_result.to_report(max_chars=5000)
                    except Exception as _runtime_exc:
                        _runtime_result = None
                        _runtime_report = (
                            "Runtime verifier exception: "
                            f"{type(_runtime_exc).__name__}: {str(_runtime_exc)[:400]}"
                        )
                    if not _runtime_result or not _runtime_result.passed:
                        query = _build_post_delegate_web_verify_failure_query(
                            original_query,
                            _web_project_path,
                            obs_text,
                            _runtime_report,
                        )
                        self._after_delegate_success = False
                        _finish_iteration(status="ok", summary="delegate_task_web_verify_failed")
                        continue
                    self._update_plan_progress(
                        "browser_verify_local_project",
                        {"project_path": str(_web_project_path)},
                        _runtime_report,
                        i,
                    )
                    self._mark_web_runtime_plan_verified(i)
                    _pending_business = self._pending_delegate_success_business_tasks()
                    if _pending_business:
                        query = _build_post_delegate_continue_query(
                            original_query,
                            obs_text,
                            [t.description for t in _pending_business],
                            _runtime_report,
                        )
                        self._after_delegate_success = False
                    else:
                        query = _build_post_delegate_web_verify_success_query(
                            original_query,
                            obs_text,
                            _runtime_report,
                        )
                        self._after_delegate_success = True
                    _finish_iteration(status="ok", summary="delegate_task_web_verify_ok")
                    continue
                _pending_business = self._pending_delegate_success_business_tasks()
                if _pending_business:
                    query = _build_post_delegate_continue_query(
                        original_query,
                        obs_text,
                        [t.description for t in _pending_business],
                    )
                    self._after_delegate_success = False
                    _finish_iteration(status="ok", summary="delegate_task_success_continue_business")
                    continue
                query = (
                    f"Requête originale: {original_query}\n\n"
                    f"Le CodeAgent a terminé avec succès :\n{obs_text[:3000]}\n\n"
                    "INSTRUCTION : Rédige maintenant ta réponse finale à l'utilisateur en résumant "
                    "ce qui a été accompli. Utilise OBLIGATOIREMENT :\n"
                    "THOUGHT: (1 ligne)\n"
                    "ACTION: FINAL\n"
                    "ACTION_INPUT: [résumé clair de ce qui a été fait]"
                )
                self._after_delegate_success = True
                _finish_iteration(status="ok", summary="delegate_task_success_direct_final")
            else:
                # P5 — action_inline_risk : injecter rappel format si inline détecté au-delà du seuil
                _inline_hint = ""
                if _inline_reminder_thresh > 0 and 0 < self._action_inline_count <= _inline_reminder_thresh + 2:
                    _inline_hint = "\n\n⚠️ FORMAT: Écris ACTION: en début de ligne séparée (pas sur la même ligne que THOUGHT:)."
                # Adapter le hint de conclusion selon l'avancement réel de la tâche
                if is_web_request and (has_html or has_css or has_js):
                    # Des fichiers web ont été créés : rappeler quand conclure
                    _conclusion_hint = " Si tu as créé les 3 fichiers (HTML, CSS, JS), utilise ACTION: FINAL."
                elif is_web_request and action.tool_name in _read_only_tools:
                    # Tâche web mais en phase d'investigation (pas encore de fichiers) : orienter vers grep/ciblage
                    _conclusion_hint = " Utilise grep_search ou read_file ciblé pour trouver l'information, puis agis."
                else:
                    _conclusion_hint = ""
                query = f"""Requête originale: {original_query}
{files_reminder}
Observation de l'action précédente ({action.tool_name}): {obs_text}{_inline_hint}

Continue à répondre à la question initiale.{_conclusion_hint}"""
                _finish_iteration(status="ok", summary=f"tool={action.tool_name}")
        
        # Si on atteint la limite, retourner la dernière observation si elle existe
        last_obs = None
        for h in reversed(self.history):
            if h.observation and h.observation.content:
                last_obs = h.observation.content
                break
        
        if last_obs and ("Recherche" in last_obs or "💰" in last_obs):
            self._run_meta["agent_output_incomplete"] = True
            self._run_meta["agent_output_warning"] = "iteration_limit_reached_with_observation_fallback"
            if TELEMETRY_AVAILABLE:
                publish_trace(
                    stage="pipeline_error",
                    status="error",
                    mode="agent",
                    error=self._run_meta["agent_output_warning"],
                )
            message = f"📊 Voici ce que j'ai trouvé :\n\n{last_obs[:3000]}"
            self._mark_task_failed(self._run_meta["agent_output_warning"])
            return message

        self._run_meta["agent_output_incomplete"] = True
        self._run_meta["agent_output_warning"] = "iteration_limit_reached_without_final_answer"
        if TELEMETRY_AVAILABLE:
            publish_trace(
                stage="pipeline_error",
                status="error",
                mode="agent",
                error=self._run_meta["agent_output_warning"],
            )
        self._mark_task_failed(self._run_meta["agent_output_warning"])
        return "J'ai atteint la limite d'itérations. Voici ce que j'ai trouvé jusqu'ici."
    
    def clear_history(self):
        """Efface l'historique."""
        self.history.clear()
        self.action_history.clear()
        self._task_plan.clear()
        self._plan_emitted = False
        self._iterations_without_progress = 0
        self._last_completed_task_count = 0
        self._last_document_manifest_signature = ()
        self._last_document_workflow_progress_signature = (0, "", "")
        self._document_delivery_reference_id = ""
        self._document_delivery_reference_signature = ()
        self._document_reference_announced = False
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
