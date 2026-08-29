"""Runtime documentaire — lectrices et normaliseuses.

Lot RF-5a du plan `plans/REFACTOR_REACT_DEDICATED_PLAN_2026-08-27.md`.
Dix-sept methodes de lecture et de normalisation quittent `ReActLoop` pour ce
module. `react.py` conserve dix-sept reexports minces : les 283 sites d'appel
existants, dont 196 dans les tests, continuent d'ecrire
`ReActLoop._document_x(...)` sans changer d'une virgule.

--- Pourquoi ce sous-lot est le plus sur des quatre ---

Mesure de l'audit RF-5a, sur le code d'origine :

| | |
|---|---|
| mutations de `self` | **0** |
| appels sortant du sous-lot | **0** |
| noms libres hors module | **0** |

L'ilot est ferme. Les seuls appels entre ces methodes restent entre elles.

--- Le contrat d'etat tient en DEUX noms ---

Quatre des dix-sept prennent `self`, mais aucune ne lit un attribut par la
forme `self.X` : elles passent toutes par `getattr(self, ...)`, invisible a un
balayage d'attributs — la meme forme qui avait masque `_is_mission_run` en
RF-4. Les etats reellement lus sont exactement deux :

- `history` -> parametre `historique` ;
- `_document_workflow_evidence` -> parametre `preuves_workflow`.

Ils sont passes en VALEURS explicites. `self` n'est jamais donne a ce module,
et le contrat d'etat est visible dans les signatures au lieu d'etre enfoui
dans des `getattr`.

--- Ce que ces methodes etaient deja ---

Les tests du depot les appellent sur la CLASSE, en passant un sac d'etat
quelconque :

    requested, delivered, missing = ReActLoop._structured_document_delivery_progress(state)

Elles etaient donc deja des fonctions deguisees. Ce lot ne les transforme pas :
il constate ce qu'elles sont.

--- Ce module n'importe PAS `react.py` (invariant 2) ---

Ses seules dependances sont la bibliotheque standard et
`..documents.document_intent`.
"""

from __future__ import annotations

import ast
import json
import os
import re
import unicodedata
from dataclasses import dataclass, replace
from types import SimpleNamespace
from typing import Any, Callable, Dict, Optional

from loguru import logger

from .plan_evidence import _normalize_guard_token
from .plan_progress import (
    document_catalog_task_origin,
    document_workflow_task_blocks,
    document_workflow_task_operation,
    final_fulfills_task,
)
from ..documents.document_intent import (
    DocumentRoute,
    resolve_document_route,
    STUDIO_BYPASS_TOOLS,
    normalize_document_kind,
)


def _document_tool_events(historique):
    """Yield document calls, including structured parallel sub-results."""
    for step in historique:
        action = getattr(step, "action", None)
        if action is None:
            continue
        name = getattr(action, "tool_name", "") or ""
        observation = getattr(step, "observation", None)
        if name in {"parallel_tools", "generate_studio_documents"}:
            for sub in getattr(observation, "sub_results", ()) if observation else ():
                yield (
                    getattr(sub, "tool_name", "") or "",
                    getattr(sub, "args", {}) or {},
                    bool(getattr(sub, "success", False)),
                    True,
                    getattr(sub, "content", "") or "",
                )
            continue
        yield (
            name,
            getattr(action, "tool_args", {}) or {},
            bool(getattr(observation, "success", False)) if observation is not None else False,
            observation is not None,
            getattr(observation, "content", "") or "" if observation is not None else "",
        )


def _document_catalog_evidence_key(args: Optional[Dict[str, Any]]) -> tuple[str, int, str]:
    values = args or {}
    try:
        limit = int(values.get("limit") or 0)
    except (TypeError, ValueError):
        limit = 0
    return (
        str(values.get("origin") or "").strip().lower(),
        limit,
        str(values.get("sort") or "").strip().lower(),
    )


def _document_catalog_rows(content: Any) -> tuple[dict, ...]:
    try:
        payload = json.loads(content) if isinstance(content, str) else content
    except (TypeError, ValueError, json.JSONDecodeError):
        return ()
    rows = payload.get("models") if isinstance(payload, dict) else None
    return tuple(row for row in rows if isinstance(row, dict)) if isinstance(rows, list) else ()


def _document_parallel_calls(tool_args: Optional[Dict[str, Any]]) -> tuple[tuple[str, dict], ...]:
    """Return normalized nested calls without executing or mutating them."""
    calls = (tool_args or {}).get("tool_calls", [])
    if isinstance(calls, str):
        try:
            calls = json.loads(calls)
        except (TypeError, ValueError, json.JSONDecodeError):
            return ()
    normalized = []
    name_keys = {"name", "tool_name", "tool", "action"}
    arg_keys = ("args", "arguments", "tool_args", "parameters", "input", "params")
    for raw in calls if isinstance(calls, list) else ():
        if not isinstance(raw, dict):
            continue
        name = str(
            raw.get("name") or raw.get("tool_name")
            or raw.get("tool") or raw.get("action") or ""
        ).strip()
        args = next(
            (raw.get(key) for key in arg_keys if isinstance(raw.get(key), dict)),
            None,
        )
        if args is None:
            args = {
                key: value for key, value in raw.items()
                if key not in name_keys and key not in arg_keys
            }
        normalized.append((name, dict(args)))
    return tuple(normalized)


def _duplicate_document_mutation(
    primary_name: str,
    primary_args: Optional[Dict[str, Any]],
    queued_name: str,
    queued_args: Optional[Dict[str, Any]],
) -> bool:
    """Reject only byte-for-byte equivalent document mutations in one turn."""
    document_mutations = {
        "generate_studio_document",
        "generate_studio_documents",
        "revise_studio_document",
        "apply_document_edit",
    }
    if primary_name != queued_name or primary_name not in document_mutations:
        return False
    try:
        primary = json.dumps(
            primary_args or {}, ensure_ascii=False, sort_keys=True, default=str,
        )
        queued = json.dumps(
            queued_args or {}, ensure_ascii=False, sort_keys=True, default=str,
        )
    except (TypeError, ValueError):
        return False
    return primary == queued


def _document_open_payload(observation) -> dict[str, Any] | None:
    """Parse the authoritative raw open payload before visible compaction."""
    content = getattr(observation, "content", None)
    try:
        payload = json.loads(content) if isinstance(content, str) else content
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return dict(payload) if isinstance(payload, dict) else None


def _document_revision_patch(args: Optional[Dict[str, Any]]) -> dict[str, Any]:
    raw = (args or {}).get("data", {})
    if isinstance(raw, dict):
        return dict(raw)
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        try:
            parsed = ast.literal_eval(raw)
        except (ValueError, SyntaxError):
            return {}
    return dict(parsed) if isinstance(parsed, dict) else {}


def _document_revision_changed_fields(record: Any) -> dict[str, Any]:
    """Return the authoritative parent-child diff, with legacy arg fallback."""
    if not isinstance(record, dict):
        return {}
    changed = record.get("changed_fields")
    if isinstance(changed, dict) and changed:
        return dict(changed)
    return _document_revision_patch(record.get("args", {}))


def _document_patch_scalar_values(value: Any) -> tuple[str, ...]:
    values: list[str] = []
    if isinstance(value, dict):
        for child in value.values():
            values.extend(_document_patch_scalar_values(child))
    elif isinstance(value, (list, tuple)):
        for child in value:
            values.extend(_document_patch_scalar_values(child))
    elif value is not None and not isinstance(value, (dict, list, tuple)):
        text = str(value).strip()
        if text:
            values.append(text)
    return tuple(values)


def _document_paths_match(left: str, right: str) -> bool:
    if not left or not right:
        return False
    return os.path.normcase(os.path.normpath(str(left))) == os.path.normcase(
        os.path.normpath(str(right))
    )


def _document_verification_text(value: Any) -> str:
    """Normalize PDF line wrapping without weakening value matching."""
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = re.sub(r"(?<=\w)-\s+(?=\w)", "-", text)
    return re.sub(r"\s+", " ", text).strip().casefold()


def _latest_document_batch_proofs(historique, preuves_workflow):
    """Aggregate newest proof per template across bounded batch retries."""
    from src.documents.delivery_manifest import parse_generation_proof

    newest = {}
    for step in reversed(historique):
        action = getattr(step, "action", None)
        if getattr(action, "tool_name", "") != "generate_studio_documents":
            continue
        observation = getattr(step, "observation", None)
        if observation is None:
            continue
        for sub in getattr(observation, "sub_results", ()):
            if not bool(getattr(sub, "success", False)):
                continue
            args = getattr(sub, "args", {}) or {}
            proof = parse_generation_proof(
                getattr(sub, "content", "") or "",
                fallback_kind=normalize_document_kind(str(args.get("kind", ""))),
            )
            if proof is not None:
                identity = proof.template_id or proof.document_id or proof.path
                if identity and identity not in newest:
                    newest[identity] = proof
    # Run-scoped evidence is captured before compaction and therefore wins
    # over history-derived records for the same template.
    store = preuves_workflow
    cached = store.get("batch_proofs", {}) if isinstance(store, dict) else {}
    if isinstance(cached, dict):
        newest.update(cached)
    return tuple(newest.values())


def _document_web_rights_evidence(historique) -> tuple[bool, bool]:
    """Return (web-document action observed, explicit reuse rights proven)."""
    relevant = False
    accepted = {"licensed", "public_domain", "permission_granted"}
    for name, _args, success, observed, content in _document_tool_events(historique):
        if name not in {"inspect_document_source", "download_document"} \
                or not (success and observed):
            continue
        relevant = True
        try:
            payload = json.loads(content) if isinstance(content, str) else content
        except (TypeError, ValueError):
            payload = {}
        candidates = [payload] if isinstance(payload, dict) else []
        record = payload.get("record") if isinstance(payload, dict) else None
        if isinstance(record, dict):
            candidates.extend([record, record.get("metadata", {})])
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            status = str(candidate.get("rights_status", "")).strip().lower()
            evidence = str(candidate.get("rights_evidence", "")).strip()
            if status in accepted and evidence:
                return True, True
    return relevant, False


def _nested_document_bypass(
    tool_name: str, tool_args: Optional[Dict[str, Any]] = None,
) -> str:
    if tool_name in STUDIO_BYPASS_TOOLS:
        return tool_name
    if tool_name != "parallel_tools":
        return ""
    calls = (tool_args or {}).get("tool_calls", [])
    if isinstance(calls, str):
        try:
            calls = json.loads(calls)
        except (TypeError, ValueError):
            calls = []
    for call in calls if isinstance(calls, list) else []:
        if isinstance(call, dict) and str(call.get("name", "")) in STUDIO_BYPASS_TOOLS:
            return str(call["name"])
    return ""


def _studio_attempted_kinds(historique, studio_tool: str, route: DocumentRoute) -> tuple[str, ...]:
    attempted: list[str] = []
    requested = route.requested_kinds
    for name, args, _success, _observed, _content in _document_tool_events(historique):
        if name != studio_tool:
            continue
        kind = normalize_document_kind(str((args or {}).get("kind", "")))
        if not kind and len(requested) == 1:
            kind = requested[0]
        if kind:
            attempted.append(kind)
    return tuple(attempted)


def _merge_mission_document_evidence(free_answer: str, evidence: str) -> str:
    """Keep a mission's free report while attaching exact document proof."""
    answer = str(free_answer or "").strip()
    proof = str(evidence or "").strip()
    if not answer:
        # A document capability never becomes the mission's voice. Keeping
        # this empty lets the normal FINAL/thought-leak repair obtain a
        # complete free mission report on the next turn. Returning `proof`
        # here used to short-circuit that repair and replace mixed mission
        # results with the canned Document Studio receipt.
        return ""
    if not proof or proof in answer:
        return answer
    return f"{answer}\n\nPreuves documentaires:\n{proof}"


def _document_plan_required_kinds(task_desc: str) -> tuple[str, ...]:
    from src.documents.document_intent import document_kinds_mentioned

    kinds = list(document_kinds_mentioned(task_desc))
    # Plan-only abbreviation. Ordinary user prose containing lowercase
    # "bc" must not alter the deterministic document router.
    if re.search(r"(?<!\w)BC(?!\w)", str(task_desc or "")) and "bon_commande" not in kinds:
        kinds.append("bon_commande")
    return tuple(kinds)


# ══════════════════════════════════════════════════════════════════════════
#  RF-5b — la racine du graphe documentaire : route et catalogue
# ══════════════════════════════════════════════════════════════════════════
#
# Six methodes. `_document_route_for_run` est le HUB : 14 appels internes a la
# famille, 5 depuis le reste de `ReActLoop`, plus l'appelable
# `obtenir_route_document` que RF-4 lui passe deja. Le mini-plan l'avait range
# en RF-5d par THEME ; par DEPENDANCE, c'est la racine, et six des huit
# methodes annoncees pour RF-5b en dependaient. Le sous-lot a donc ete
# recompose : la racine, plus ce qui n'a besoin que d'elle.
#
# --- Deux sorties que ce module ne peut pas absorber ---
#
# `_emit_plan_state` reste chez `ReActLoop` (RF-4 l'a garde dans sa coquille),
# et `_mission_routing_objective` appartient a la famille MISSION, donc a RF-6
# — bloque par le §18 du plan tant que le chantier CodeAgent n'est pas
# stabilise. Les deux passent en appelables.
#
# --- Toutes les lectures sont PARESSEUSES ---
#
# Le depot construit des `ReActLoop` par `object.__new__` : `runtime_ctx`,
# `task_id`, `task_orchestrator`, `_original_query`, `_task_plan` y sont
# absents. Les lire a la construction de l'entree les rendrait obligatoires —
# le defaut qui avait casse 54 tests en RF-4.
#
# `_document_catalog_evidence` est lu sous DEUX formes, avec deux defauts
# differents (`None` et `{}`). Une seule valeur ne peut pas rendre les deux :
# il y a donc deux appelables, exactement comme le `execution_ledger` de RF-4.


# Lot RF-5d2 : cette constante avait UN SEUL consommateur — la methode qui part
# avec ce lot. Elle la suit donc ici, et `react.py` la reexporte (invariant 4).
_MISSION_PROACTIVE_DOCUMENT_TOOLS = (
    "list_document_models",
    "generate_studio_document", "generate_studio_documents",
    "create_pdf", "create_docx", "create_xlsx", "create_pptx",
    "create_markdown",
)


@dataclass(frozen=True)
class EntreeDocumentCatalogue:
    """Contrat d'etat de la racine documentaire, sans `self`.

    Tout est appelable : rien n'est evalue avant d'etre reellement atteint, et
    les deux ecritures restent portees par des fermetures definies dans
    `ReActLoop` (invariant 5).
    """

    obtenir_runtime_ctx: Callable[[], Any]
    est_run_mission: Callable[[], bool]
    obtenir_task_id: Callable[[], Any]
    obtenir_orchestrateur: Callable[[], Any]
    obtenir_requete_originale: Callable[[], Any]
    obtenir_historique: Callable[[], Any]
    obtenir_plan: Callable[[], Any]

    obtenir_route_cache: Callable[[], Any]
    definir_route_cache: Callable[[Any], None]
    obtenir_preuves_catalogue: Callable[[], Any]
    obtenir_preuves_catalogue_ou_vide: Callable[[], Any]
    definir_preuves_catalogue: Callable[[Any], None]

    objectif_routage_mission: Callable[[], Any]
    emettre_etat_plan: Callable[..., None]


def _document_route_for_run(e, query: Optional[str] = None) -> DocumentRoute:
    """Return the single mode-aware document decision for this run.

        AgentService normally injects this immutable route. Direct ReAct
        callers get one deterministic fallback from their runtime mode, then
        reuse it for the entire run.
        """
    runtime_ctx = e.obtenir_runtime_ctx()
    mode = getattr(runtime_ctx, "mode", "agent") if runtime_ctx is not None else "agent"
    is_mission = bool(
        e.est_run_mission()
        or (
            e.obtenir_task_id()
            and e.obtenir_orchestrateur()
        )
    )
    route = e.obtenir_route_cache()
    if isinstance(route, DocumentRoute) and not is_mission:
        return route
    routing_query = (
        e.objectif_routage_mission() if is_mission else ""
    )
    if not routing_query:
        routing_query = query if query is not None else (
            e.obtenir_requete_originale() or ""
        )
    route = resolve_document_route(routing_query, mode=mode)
    if is_mission and route.owns_run:
        route = replace(route, owns_run=False)
    e.definir_route_cache(route)
    return route


def _record_document_catalog_evidence(e, action, observation) -> None:
    """Keep exact successful catalog rows before history compaction."""
    if observation is None:
        return
    evidence = e.obtenir_preuves_catalogue()
    if not isinstance(evidence, dict):
        evidence = {}
        e.definir_preuves_catalogue(evidence)

    candidates = []
    name = getattr(action, "tool_name", "") or ""
    if name == "list_document_models":
        candidates.append((
            getattr(action, "tool_args", {}) or {},
            bool(getattr(observation, "success", False)),
            getattr(observation, "content", "") or "",
        ))
    elif name == "parallel_tools":
        for sub in getattr(observation, "sub_results", ()):
            if getattr(sub, "tool_name", "") == "list_document_models":
                candidates.append((
                    getattr(sub, "args", {}) or {},
                    bool(getattr(sub, "success", False)),
                    getattr(sub, "content", "") or "",
                ))

    for args, success, content in candidates:
        # Selection-by-count requires the unfiltered compact catalogue.
        # A kind-specific response with the same origin/limit/sort is not
        # equivalent and must never populate the exact selection cache.
        if str((args or {}).get("kind") or "").strip():
            continue
        rows = _document_catalog_rows(content)
        if success and rows:
            evidence[_document_catalog_evidence_key(args)] = rows


def _document_catalog_selection_groups(e) -> tuple[tuple[dict, ...], ...]:
    """Return exact catalogue rows grouped in the user's requested order."""
    route = _document_route_for_run(e)
    selections = route.selections or (() if not route.is_catalog_selection else (
        SimpleNamespace(
            origin=route.selection_origin,
            limit=route.selection_limit,
            sort=route.selection_sort,
        ),
    ))
    events = tuple(_document_tool_events(e.obtenir_historique()))
    groups: list[tuple[dict, ...]] = []
    for selection in selections:
        key = _document_catalog_evidence_key({
            "origin": selection.origin,
            "limit": selection.limit,
            "sort": selection.sort,
        })
        cache = e.obtenir_preuves_catalogue_ou_vide()
        selected: tuple[dict, ...] = tuple(cache.get(key, ())) if isinstance(cache, dict) else ()
        fallback: tuple[dict, ...] = ()
        for name, args, success, observed, content in (() if selected else reversed(events)):
            if name != "list_document_models" or not (success and observed):
                continue
            parsed = _document_catalog_rows(content)
            if not parsed:
                continue
            if not fallback:
                fallback = parsed
            if _document_catalog_evidence_key(args) == key:
                selected = parsed
                break
        # Historical single-selection tests/callers did not always retain
        # list arguments. Preserve that compatibility only for one group.
        if not selected and len(selections) == 1:
            selected = fallback
        groups.append(selected[:selection.limit])
    return tuple(groups)


def _document_catalog_selection_models(e) -> tuple[dict, ...]:
    """Compatibility view flattening the exact ordered catalogue groups."""
    return tuple(
        row
        for group in _document_catalog_selection_groups(e)
        for row in group
    )


def _document_expected_template_ids(e) -> tuple[str, ...]:
    route = _document_route_for_run(e)
    rows = _document_catalog_selection_models(e)
    return tuple(
        str(row.get("id") or "").strip()
        for row in rows[:route.requested_count]
        if str(row.get("id") or "").strip()
    )


def _reconcile_document_catalog_plan(e, iteration: int) -> int:
    """Credit catalogue plan tasks only from their exact successful listing."""
    if not e.obtenir_plan():
        return 0
    route = _document_route_for_run(e)
    selections = tuple(getattr(route, "selections", ()) or ())
    if len(selections) <= 1:
        return 0
    evidence = e.obtenir_preuves_catalogue_ou_vide()
    if not isinstance(evidence, dict):
        return 0

    changed = 0
    for selection in selections:
        key = _document_catalog_evidence_key({
            "origin": selection.origin,
            "limit": selection.limit,
            "sort": selection.sort,
        })
        rows = tuple(evidence.get(key, ()))
        if len(rows) < selection.limit:
            continue
        for task in e.obtenir_plan():
            if task.completed or document_catalog_task_origin(task.description) != selection.origin:
                continue
            task.completed = True
            task.completed_at_iteration = iteration
            task.completed_by_tool = "list_document_models"
            task.completion_status = "verified"
            task.completion_evidence = (
                f"catalogue exact origin={selection.origin}, limit={selection.limit}, "
                f"sort={selection.sort}"
            )
            task.completion_confidence = "strong"
            changed += 1
            logger.info(
                "[PLAN DOCUMENT CATALOG] '{}' - {}",
                task.description[:70], task.completion_evidence,
            )
            break
    if changed:
        e.emettre_etat_plan(context_tool="list_document_models")
    return changed


# ══════════════════════════════════════════════════════════════════════════
#  RF-5c — la verite de livraison
# ══════════════════════════════════════════════════════════════════════════
#
# Cinq methodes, 208 lignes. Ilot ferme calcule par le GRAPHE de dependance,
# pas par les noms : `_structured_document_delivery_manifest` et
# `_document_delivery_truth_required` sont des feuilles, les trois autres ne
# dependent que du manifeste.
#
# Le mini-plan en annoncait six. `_document_workflow_pending_action` a ete
# ecartee : elle depend de `_document_workflow_proof_state` (261 lignes,
# RF-5d), ce qui aurait porte la cloture de 208 a 435 lignes. Troisieme
# correction de composition du chantier — et la premiere que le graphe attrape
# AVANT qu'une ligne soit ecrite.
#
# --- Ce sous-lot porte le truth-lock de livraison ---
#
# Invariant du plan : *une livraison n'est annoncee que pour des fichiers
# existants et verifies*. `_ensure_document_delivery_reference` refuse de
# persister des qu'il manque une piece, qu'une preuve n'est pas verifiee, ou
# que le nombre de pages est insuffisant. Son `except` rend "" — jamais une
# reference : une exception ne devient jamais une autorisation (invariant 6).
#
# --- Pourquoi une entree qui en CONTIENT une autre ---
#
# Ces methodes appellent `_document_route_for_run` et
# `_document_expected_template_ids`, qui prennent l'entree CATALOGUE de RF-5b.
# Plutot que d'elargir `EntreeDocumentCatalogue` — dont le contrat a 14 champs
# est fige et teste — l'entree de RF-5c la PORTE dans un champ `catalogue`.
# Chaque sous-lot garde ainsi son contrat.


@dataclass(frozen=True)
class EntreeLivraisonDocument:
    """Contrat d'etat de la verite de livraison, sans `self`.

    Les trois ecritures du truth-lock passent par des fermetures definies dans
    `react.py` (invariant 5). Toutes les lectures sont paresseuses et gardent
    le defaut exact de leur site d'origine.
    """

    catalogue: "EntreeDocumentCatalogue"

    obtenir_historique: Callable[[], Any]
    obtenir_preuves_workflow: Callable[[], Any]

    obtenir_reference_id: Callable[[], Any]
    definir_reference_id: Callable[[Any], None]
    obtenir_reference_signature: Callable[[], Any]
    definir_reference_signature: Callable[[Any], None]
    obtenir_cible_workflow: Callable[[], Any]
    definir_cible_workflow: Callable[[Any], None]


def _structured_document_delivery_progress(e) -> tuple[int, int, tuple[str, ...]]:
    """Return the number of requested documents proven as generated."""
    from collections import Counter

    route = _document_route_for_run(e.catalogue)
    if route.is_catalog_selection:
        manifest, missing, _unverified = (
            _structured_document_delivery_manifest(e)
        )
        return route.requested_count, len(manifest), missing
    requested = route.requested_kinds
    if not requested:
        return 0, 0, ()
    remaining = Counter(requested)
    delivered = 0
    generic_successes = 0
    delivery_tools = {
        "create_pdf", "create_invoice_pdf", "create_from_template",
        "create_docx", "create_xlsx", "create_pptx", "create_csv",
        "create_html", "create_markdown",
    }
    for name, args, success, observed, _content in _document_tool_events(e.obtenir_historique()):
        if not (success and observed):
            continue
        if name == "generate_studio_document":
            kind = normalize_document_kind(str((args or {}).get("kind", "")))
            if kind and remaining[kind] > 0:
                remaining[kind] -= 1
                delivered += 1
        elif name in delivery_tools:
            generic_successes += 1
    missing = [kind for kind in requested for _ in range(remaining[kind])]
    generic_used = min(generic_successes, len(missing))
    delivered += generic_used
    return len(requested), min(delivered, len(requested)), tuple(missing[generic_used:])


def _structured_document_delivery_manifest(e):
    """Return exact Studio proofs in the order requested by the user."""
    from collections import Counter, defaultdict, deque
    from src.documents.delivery_manifest import parse_generation_proof

    route = _document_route_for_run(e.catalogue)
    if route.is_catalog_selection:
        expected_ids = _document_expected_template_ids(e.catalogue)
        proofs = _latest_document_batch_proofs(e.obtenir_historique(), e.obtenir_preuves_workflow())
        by_template = {}
        for proof in proofs:
            if proof.template_id and proof.template_id not in by_template:
                by_template[proof.template_id] = proof
        ordered = []
        missing = []
        for template_id in expected_ids:
            if template_id in by_template:
                ordered.append(by_template[template_id])
            else:
                missing.append(template_id)
        if len(expected_ids) < route.requested_count:
            missing.extend(
                f"catalog_selection_{index}"
                for index in range(len(expected_ids) + 1, route.requested_count + 1)
            )
        unverified = tuple(
            proof.template_id or proof.kind
            for proof in ordered
            if (
                not proof.render_verified
                or (
                    route.minimum_pages > 0
                    and proof.page_count < route.minimum_pages
                )
            )
        )
        return tuple(ordered), tuple(missing), unverified
    buckets = defaultdict(deque)

    def add_proof(proof) -> None:
        canonical_kind = normalize_document_kind(proof.kind)
        if not canonical_kind:
            return
        buckets[canonical_kind].append(proof)

    store = e.obtenir_preuves_workflow()
    cached_batch = store.get("batch_proofs", {}) if isinstance(store, dict) else {}
    for proof in cached_batch.values() if isinstance(cached_batch, dict) else ():
        add_proof(proof)
    generation_events = (
        store.get("generation_events", []) if isinstance(store, dict) else []
    )
    for event in generation_events if isinstance(generation_events, list) else ():
        proof = event.get("proof") if isinstance(event, dict) else None
        if proof is not None:
            add_proof(proof)

    # Run-scoped evidence is captured before history compaction.  When it
    # exists, it is the sole source of the initial delivery manifest.
    # Otherwise retain compatibility with history-only tests/old runs.
    # Revisions are deliberately excluded: children belong to the lifecycle
    # proof, never to the immutable generation manifest.
    have_run_scoped_generation = bool(cached_batch) or bool(generation_events)
    if not have_run_scoped_generation:
        allows_revision_as_origin = (
            route.operation == "revise"
            and not any(
                getattr(action, "operation", "") == "generate"
                for action in getattr(route, "workflow_actions", ())
            )
        )
        for name, args, success, observed, content in _document_tool_events(e.obtenir_historique()):
            if (
                name != "generate_studio_document"
                and not (allows_revision_as_origin and name == "revise_studio_document")
            ) or not (success and observed):
                continue
            fallback_kind = normalize_document_kind(str((args or {}).get("kind", "")))
            if not fallback_kind and len(route.requested_kinds) == 1:
                fallback_kind = route.requested_kinds[0]
            proof = parse_generation_proof(content, fallback_kind=fallback_kind)
            if proof is not None:
                add_proof(proof)

    # A retry supersedes an earlier failed/unverified rendering for the
    # same requested slot. Keep only the newest N proofs, where N is the
    # number of times that kind was actually requested.
    requested_counts = Counter(route.requested_kinds)
    for kind, bucket in buckets.items():
        while len(bucket) > requested_counts[kind]:
            bucket.popleft()

    ordered = []
    missing = []
    for kind in route.requested_kinds:
        if buckets[kind]:
            ordered.append(buckets[kind].popleft())
        else:
            missing.append(kind)
    unverified = tuple(
        proof.kind
        for proof in ordered
        if (
            not proof.render_verified
            or (
                route.minimum_pages > 0
                and proof.page_count < route.minimum_pages
            )
        )
    )
    return tuple(ordered), tuple(missing), unverified


def _ensure_document_delivery_reference(e) -> str:
    """Persist the exact run manifest as soon as it becomes complete."""
    route = _document_route_for_run(e.catalogue)
    manifest, missing, unverified = _structured_document_delivery_manifest(e)
    if (
        missing or unverified or route.requested_count < 1
        or len(manifest) != route.requested_count
    ):
        return ""
    from src.documents.delivery_manifest import manifest_progress_signature

    signature = manifest_progress_signature(manifest)
    existing = str(e.obtenir_reference_id() or "")
    if (
        existing
        and e.obtenir_reference_signature() == signature
    ):
        return existing
    try:
        from src.documents.document_delivery_bundle import save_delivery_reference
        from src.documents.studio import get_document_studio

        receipt = save_delivery_reference(
            get_document_studio().root,
            manifest,
            requested_count=route.requested_count,
        )
        reference_id = str(receipt.get("id") or "")
        if reference_id:
            e.definir_reference_id(reference_id)
            e.definir_reference_signature(signature)
        return reference_id
    except Exception as exc:
        logger.warning("[DOCUMENT DELIVERY RECEIPT] persistence impossible: {}", exc)
        return ""


def _document_workflow_target(e):
    """Return the exact manifest proof targeted by the revision ordinal."""
    cached = e.obtenir_cible_workflow()
    if cached is not None:
        return cached
    route = _document_route_for_run(e.catalogue)
    manifest, missing, _unverified = _structured_document_delivery_manifest(e)
    revision = next(
        (
            action for action in getattr(route, "workflow_actions", ())
            if getattr(action, "operation", "") == "revise"
        ),
        None,
    )
    ordinal = int(getattr(revision, "target_ordinal", 0) or 0)
    target = (
        manifest[ordinal - 1]
        if not missing and len(manifest) == route.requested_count
        and ordinal and ordinal <= len(manifest)
        else None
    )
    if target is not None:
        e.definir_cible_workflow(target)
    return target


def _document_delivery_truth_required(route: DocumentRoute, requested_count: int) -> bool:
    """Require exact Studio evidence for every actionable structured request."""
    return bool(requested_count >= 1 and route.requires_studio)


# ══════════════════════════════════════════════════════════════════════════
#  RF-5d1 — les deux racines du workflow documentaire
# ══════════════════════════════════════════════════════════════════════════
#
# `_record_document_workflow_evidence` (220 l.) porte les QUATRE dernieres
# mutations de la famille ; `_document_workflow_proof_state` (261 l.) est
# l'etat de preuve que tout le reste de RF-5d consomme.
#
# Ilot ferme calcule par le graphe : leurs 11 appels sur la classe pointent
# TOUS vers des methodes deja extraites par RF-5a, RF-5b et RF-5c. Zero
# sortant residuel.
#
# --- Pourquoi RF-5d a ete coupe en deux ---
#
# RF-5d entier fait 977 lignes et contient `_structured_document_tool_gate`,
# une PORTE que l'invariant 7 oblige a rester fail-closed. Le graphe offrait
# une coupure nette : les deux racines d'abord (481 l., toutes les mutations),
# les sept methodes qui en dependent ensuite (496 l.). Chaque moitie est
# fermee et prouvable seule.
#
# --- Le motif des deux defauts, troisieme occurrence ---
#
# `_document_workflow_evidence` est lu avec le defaut `None` a un endroit et
# `{}` a un autre. Une seule valeur ne peut pas rendre les deux. C'est deja
# arrive au `execution_ledger` (RF-4) et au `_document_catalog_evidence`
# (RF-5b) ; le motif est desormais cherche systematiquement.


@dataclass(frozen=True)
class EntreeWorkflowDocument:
    """Contrat d'etat des racines du workflow, sans `self`.

    L'entree PORTE celle de la livraison (RF-5c), qui porte elle-meme celle du
    catalogue (RF-5b). Chaque sous-lot garde son contrat intact : les trois
    ecritures du truth-lock restent celles de RF-5c, et seule la quatrieme
    mutation — le magasin de preuves — est ajoutee ici.
    """

    livraison: "EntreeLivraisonDocument"
    obtenir_preuves_workflow_ou_none: Callable[[], Any]
    definir_preuves_workflow: Callable[[Any], None]


def _record_document_workflow_evidence(e, action, observation) -> None:
    """Capture proof-bearing workflow results before visible compaction."""
    if observation is None or not bool(getattr(observation, "success", False)):
        return
    store = e.obtenir_preuves_workflow_ou_none()
    if not isinstance(store, dict):
        store = {
            "batch_proofs": {}, "generation_events": [],
            "open_events": [], "revision_events": [], "revision_records": [],
            "verification_events": [], "history_events": [],
            "export_events": [], "library_events": [], "event_counter": 0,
        }
        e.definir_preuves_workflow(store)
    name = getattr(action, "tool_name", "") or ""
    args = getattr(action, "tool_args", {}) or {}
    content = getattr(observation, "content", "") or ""
    event_index = int(store.get("event_counter", 0) or 0) + 1
    store["event_counter"] = event_index

    from src.documents.delivery_manifest import parse_generation_proof

    if name == "generate_studio_documents":
        # A new successful generation supersedes any target frozen from a
        # previous attempt.  Keeping the old target made a later, valid
        # revision invisible to the workflow proof.
        e.livraison.definir_cible_workflow(None)
        e.livraison.definir_reference_id("")
        e.livraison.definir_reference_signature(())
        # A batch is authoritative over any earlier unitary attempt.  The
        # Horizon Vert run regenerated one document only to obtain a
        # receipt; keeping both generations made the opened parent drift.
        store["generation_events"] = []
        proofs = store.setdefault("batch_proofs", {})
        route = _document_route_for_run(e.livraison.catalogue)
        if route.requested_count == 1:
            proofs.clear()
        for sub in getattr(observation, "sub_results", ()):
            if not bool(getattr(sub, "success", False)):
                continue
            sub_args = getattr(sub, "args", {}) or {}
            proof = parse_generation_proof(
                getattr(sub, "content", "") or "",
                fallback_kind=normalize_document_kind(str(sub_args.get("kind", ""))),
            )
            if proof is None:
                continue
            identity = proof.template_id or proof.document_id or proof.path
            if identity:
                proofs[identity] = proof
        store["last_generation_event_index"] = event_index
        try:
            payload = json.loads(content) if isinstance(content, str) else content
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = {}
        receipt_id = str(
            payload.get("receipt_id") or ""
            if isinstance(payload, dict)
            else ""
        ).strip()
        if receipt_id:
            manifest, missing, unverified = (
                _structured_document_delivery_manifest(e.livraison)
            )
            route = _document_route_for_run(e.livraison.catalogue)
            if (
                not missing and not unverified
                and len(manifest) == route.requested_count
            ):
                from src.documents.delivery_manifest import (
                    manifest_progress_signature,
                )

                # The receipt emitted by generate_studio_documents is the
                # authoritative reference for this exact manifest.  Do not
                # replace it with a synthetic bundle before the open step.
                e.livraison.definir_reference_id(receipt_id)
                e.livraison.definir_reference_signature(
                    manifest_progress_signature(manifest)
                )
        return

    if name == "generate_studio_document":
        e.livraison.definir_cible_workflow(None)
        e.livraison.definir_reference_id("")
        e.livraison.definir_reference_signature(())
        proof = parse_generation_proof(
            content,
            fallback_kind=normalize_document_kind(str(args.get("kind", ""))),
        )
        if proof is not None:
            route = _document_route_for_run(e.livraison.catalogue)
            if route.requested_count == 1:
                store.setdefault("batch_proofs", {}).clear()
            store.setdefault("generation_events", []).append({
                "proof": proof,
                "_event_index": event_index,
            })
            store["last_generation_event_index"] = event_index
        return

    if name == "open_document_delivery":
        payload = _document_open_payload(observation)
        if isinstance(payload, dict):
            event = dict(payload)
            event["_event_index"] = event_index
            event["_receipt_id"] = str(args.get("receipt_id") or "")
            store.setdefault("open_events", []).append(event)
        return

    if name == "open_file":
        route = _document_route_for_run(e.livraison.catalogue)
        manifest, missing, unverified = (
            _structured_document_delivery_manifest(e.livraison)
        )
        path = str(args.get("path") or args.get("file_path") or "")
        if (
            route.requested_count == 1
            and len(manifest) == 1
            and not missing
            and not unverified
            and _document_paths_match(path, manifest[0].path)
        ):
            receipt_id = str(
                e.livraison.obtenir_reference_id() or ""
            )
            store.setdefault("open_events", []).append({
                "receipt_id": receipt_id,
                "_receipt_id": receipt_id,
                "requested": 1,
                "opened": 1,
                "failed": 0,
                "files": [{
                    "filename": manifest[0].filename,
                    "path": manifest[0].path,
                }],
                "_event_index": event_index,
            })
        return

    if name == "revise_studio_document":
        target = _document_workflow_target(e.livraison)
        proof = parse_generation_proof(
            content,
            fallback_kind=str(getattr(target, "kind", "") or ""),
        )
        if proof is not None:
            try:
                payload = json.loads(content) if isinstance(content, str) else content
            except (TypeError, ValueError, json.JSONDecodeError):
                payload = {}
            changed_fields = (
                dict(payload.get("changed_fields") or {})
                if isinstance(payload, dict)
                else {}
            )
            store.setdefault("revision_events", []).append((dict(args), proof))
            store.setdefault("revision_records", []).append({
                "args": dict(args), "proof": proof,
                "changed_fields": changed_fields,
                "_event_index": event_index,
            })
            if str(args.get("output_format") or "").strip():
                record = (
                    dict(payload.get("record") or {})
                    if isinstance(payload, dict) else {}
                )
                store.setdefault("export_events", []).append({
                    "args": dict(args), "record": record, "proof": proof,
                    "_event_index": event_index,
                })
        return

    if name == "read_document":
        store.setdefault("verification_events", []).append({
            "args": dict(args), "content": str(content), "_event_index": event_index,
        })
        return

    if name == "get_document_history":
        try:
            payload = json.loads(content) if isinstance(content, str) else content
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = {}
        if isinstance(payload, dict):
            store.setdefault("history_events", []).append({
                "args": dict(args), "payload": dict(payload),
                "_event_index": event_index,
            })
        return

    if name in {"convert_library_document", "export_library_document"}:
        try:
            payload = json.loads(content) if isinstance(content, str) else content
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = {}
        if isinstance(payload, dict):
            record = payload.get("record")
            store.setdefault("export_events", []).append({
                "args": dict(args),
                "record": dict(record) if isinstance(record, dict) else dict(payload),
                "_event_index": event_index,
            })
        return

    if name in {"search_document_library", "get_document_record"}:
        try:
            payload = json.loads(content) if isinstance(content, str) else content
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = {}
        records = []
        if name == "search_document_library" and isinstance(payload, dict):
            records = payload.get("documents") or []
        elif isinstance(payload, dict):
            records = [payload]
        normalized_records = [dict(row) for row in records if isinstance(row, dict)]
        if normalized_records:
            store.setdefault("library_events", []).append({
                "args": dict(args), "records": normalized_records,
                "_event_index": event_index,
            })


def _document_workflow_proof_state(e) -> dict[str, Any]:
    """Return causally ordered open, revision and reread evidence."""
    route = _document_route_for_run(e.livraison.catalogue)
    store = e.livraison.obtenir_preuves_workflow()
    if not isinstance(store, dict):
        store = {}

    open_events = list(store.get("open_events", ()))
    revision_records = list(store.get("revision_records", ()))
    verification_events = list(store.get("verification_events", ()))
    history_events = list(store.get("history_events", ()))
    export_events = list(store.get("export_events", ()))
    library_events = list(store.get("library_events", ()))

    # Compatibility for tests and old in-memory runs that only have history.
    if not open_events or not revision_records or not verification_events:
        from src.documents.delivery_manifest import parse_generation_proof

        load_history_opens = not open_events
        load_history_revisions = not revision_records
        load_history_verifications = not verification_events

        for sequence, (name, args, success, observed, content) in enumerate(
            _document_tool_events(e.livraison.obtenir_historique()), start=1,
        ):
            if not (success and observed):
                continue
            if name == "open_document_delivery" and load_history_opens:
                try:
                    payload = json.loads(content) if isinstance(content, str) else content
                except (TypeError, ValueError, json.JSONDecodeError):
                    payload = None
                if isinstance(payload, dict):
                    event = dict(payload)
                    event["_event_index"] = sequence
                    event["_receipt_id"] = str((args or {}).get("receipt_id") or "")
                    open_events.append(event)
            elif name == "revise_studio_document" and load_history_revisions:
                target = _document_workflow_target(e.livraison)
                proof = parse_generation_proof(
                    content,
                    fallback_kind=str(getattr(target, "kind", "") or ""),
                )
                if proof is not None:
                    try:
                        payload = json.loads(content) if isinstance(content, str) else content
                    except (TypeError, ValueError, json.JSONDecodeError):
                        payload = {}
                    revision_records.append({
                        "args": dict(args or {}), "proof": proof,
                        "changed_fields": (
                            dict(payload.get("changed_fields") or {})
                            if isinstance(payload, dict)
                            else {}
                        ),
                        "_event_index": sequence,
                    })
            elif name == "read_document" and load_history_verifications:
                verification_events.append({
                    "args": dict(args or {}), "content": str(content),
                    "_event_index": sequence,
                })

    from src.documents.delivery_manifest import summarize_document_open_events

    generation_index = int(store.get("last_generation_event_index", 0) or 0)
    expected_receipt = str(
        e.livraison.obtenir_reference_id() or ""
    ).strip()
    causal_open_events = []
    for event in open_events:
        if not isinstance(event, dict):
            continue
        event_index = int(event.get("_event_index", 0) or 0)
        if generation_index and event_index <= generation_index:
            continue
        payload_receipt = str(event.get("receipt_id") or "").strip()
        argument_receipt = str(event.get("_receipt_id") or "").strip()
        if (
            payload_receipt
            and argument_receipt
            and payload_receipt != argument_receipt
        ):
            continue
        event_receipt = payload_receipt or argument_receipt
        if expected_receipt and event_receipt != expected_receipt:
            continue
        causal_open_events.append(event)

    open_summary = summarize_document_open_events(
        _structured_document_delivery_manifest(e.livraison)[0],
        causal_open_events,
        requested_count=route.requested_count,
    )
    exact_open = (
        open_summary
        if isinstance(open_summary, dict) and bool(open_summary.get("complete"))
        else None
    )

    target = _document_workflow_target(e.livraison)
    open_index = int((exact_open or {}).get("_event_index", 0) or 0)
    exact_revision = None
    for record in revision_records:
        args = record.get("args", {}) if isinstance(record, dict) else {}
        if (
            target is not None
            and str(args.get("document_id") or "") == target.document_id
            and int(record.get("_event_index", 0) or 0) > open_index
        ):
            exact_revision = record

    exact_verification = None
    if exact_revision is not None:
        revision_index = int(exact_revision.get("_event_index", 0) or 0)
        proof = exact_revision.get("proof")
        patch = _document_revision_changed_fields(exact_revision)
        expected_values = _document_patch_scalar_values(patch)
        for event in verification_events:
            args = event.get("args", {}) if isinstance(event, dict) else {}
            path = str(args.get("path") or args.get("file_path") or "")
            content = str(event.get("content") or "") if isinstance(event, dict) else ""
            normalized_content = _document_verification_text(content)
            if (
                int(event.get("_event_index", 0) or 0) > revision_index
                and proof is not None
                and _document_paths_match(path, proof.path)
                and expected_values
                and all(
                    _document_verification_text(value)
                    in normalized_content
                    for value in expected_values
                )
            ):
                exact_verification = event
                break

    actions = tuple(getattr(route, "workflow_actions", ()) or ())
    requested_operations = {
        str(getattr(action, "operation", "") or "") for action in actions
    }
    revised_proof = (
        exact_revision.get("proof") if isinstance(exact_revision, dict) else None
    )
    verification_index = int(
        (exact_verification or {}).get("_event_index", 0) or 0
    )

    exact_history = None
    if revised_proof is not None:
        for event in history_events:
            if not isinstance(event, dict):
                continue
            payload = event.get("payload", {})
            document = payload.get("document", {}) if isinstance(payload, dict) else {}
            metadata = document.get("metadata", {}) if isinstance(document, dict) else {}
            parent_id = str(
                (document.get("parent_id") if isinstance(document, dict) else "")
                or (metadata.get("parent_id") if isinstance(metadata, dict) else "")
                or ""
            )
            if (
                int(event.get("_event_index", 0) or 0) > verification_index
                and str((document or {}).get("id") or "") == revised_proof.document_id
                and target is not None
                and parent_id == target.document_id
            ):
                exact_history = event
                break

    history_floor = int((exact_history or {}).get("_event_index", 0) or 0)
    export_floor = history_floor if "history" in requested_operations else verification_index
    export_action = next(
        (
            action for action in actions
            if getattr(action, "operation", "") == "export"
        ),
        None,
    )
    expected_export_format = str(
        getattr(export_action, "output_format", "") or ""
    ).strip().lower()
    exact_export = None
    if revised_proof is not None:
        for event in export_events:
            if not isinstance(event, dict):
                continue
            args = event.get("args", {})
            record = event.get("record", {})
            proof = event.get("proof")
            record_id = str(
                (record.get("id") if isinstance(record, dict) else "")
                or getattr(proof, "document_id", "") or ""
            )
            record_format = str(
                (record.get("format") if isinstance(record, dict) else "")
                or getattr(proof, "format", "")
                or args.get("output_format") or ""
            ).strip().lower().lstrip(".")
            parent_id = str(
                (record.get("parent_id") if isinstance(record, dict) else "")
                or (
                    (record.get("metadata") or {}).get("parent_id")
                    if isinstance(record, dict) and isinstance(record.get("metadata"), dict)
                    else ""
                )
                or args.get("document_id") or ""
            )
            if (
                int(event.get("_event_index", 0) or 0) > export_floor
                and record_id
                and parent_id == revised_proof.document_id
                and (
                    not expected_export_format
                    or record_format == expected_export_format
                )
            ):
                exact_export = event
                break

    expected_library_ids = {
        value for value in (
            str(getattr(target, "document_id", "") or ""),
            str(getattr(revised_proof, "document_id", "") or ""),
            str(
                ((exact_export or {}).get("record") or {}).get("id")
                or getattr((exact_export or {}).get("proof"), "document_id", "")
                or ""
            ),
        ) if value
    }
    library_floor = int((exact_export or {}).get("_event_index", 0) or 0)
    observed_library_ids: set[str] = set()
    latest_library_index = 0
    for event in library_events:
        if not isinstance(event, dict):
            continue
        event_index = int(event.get("_event_index", 0) or 0)
        if event_index <= library_floor:
            continue
        latest_library_index = max(latest_library_index, event_index)
        for record in event.get("records", ()):
            if isinstance(record, dict) and record.get("id"):
                observed_library_ids.add(str(record["id"]))
    exact_library = None
    if expected_library_ids and expected_library_ids.issubset(observed_library_ids):
        exact_library = {
            "document_ids": tuple(sorted(expected_library_ids)),
            "_event_index": latest_library_index,
        }

    return {
        "open": exact_open,
        "open_progress": open_summary,
        "target": target,
        "revision": exact_revision,
        "verification": exact_verification,
        "history": exact_history,
        "export": exact_export,
        "library_verify": exact_library,
    }


# ══════════════════════════════════════════════════════════════════════════
#  RF-5d2 — la PORTE documentaire et la reconciliation
# ══════════════════════════════════════════════════════════════════════════
#
# Sept methodes, 496 lignes, **zero mutation** : elles sont toutes parties avec
# RF-5d1. L'enjeu de ce lot est ailleurs.
#
# --- Une PORTE, et l'invariant 7 l'oblige a rester fail-closed ---
#
# `_structured_document_tool_gate` (260 l.) rend `None` pour laisser passer, ou
# une `Observation` portant sa consigne de refus. Huit sites de refus.
#
# La matrice du lot enregistre explicitement PASSE ou REFUSE pour chaque
# scenario et exige que les DEUX familles soient peuplees : une porte qui
# laisserait tout passer rendrait une matrice « verte » sans que personne ne le
# voie. Mesure de reference : **9 passages contre 6 refus**.
#
# --- La frontiere avec RF-4 ---
#
# Les deux `_reconcile_*` ECRIVENT dans le plan que `react_plan_runtime.py`
# fait progresser, et emettent son etat. Elles n'en deviennent pas
# proprietaires : l'ecriture passe par l'appelable `emettre_etat_plan` deja
# porte par l'entree catalogue de RF-5b, et `react.py` reste le seul a definir
# ce que « emettre » veut dire.
#
# --- Le motif des deux formes, QUATRIEME et CINQUIEME occurrences ---
#
# `_task_plan` est lu par `getattr(self, "_task_plan", None)` a un endroit et
# `self._task_plan` a un autre : la premiere tolere l'absence, la seconde leve.
#
# `_is_mission_run` de meme : `_force_mission_proactive_document_tools` le lit
# EN DIRECT alors que toute la famille passe par `getattr`. Sur une boucle
# construite par `object.__new__`, la lecture directe leve un `AttributeError`
# la ou la forme gardee rendrait `False`.
#
# Apres `execution_ledger` (RF-4), `_document_catalog_evidence` (RF-5b) et
# `_document_workflow_evidence` (RF-5d1), le motif est cherche
# systematiquement — et il a encore rapporte deux fois ici.


@dataclass(frozen=True)
class EntreePorteDocument:
    """Contrat d'etat de la porte et de la reconciliation, sans `self`.

    Aucune mutation : ce sous-lot n'en a plus. L'entree PORTE celle du
    workflow (RF-5d1), qui porte la livraison (RF-5c), qui porte le catalogue
    (RF-5b). Chaque contrat reste intact et emboite.

    Aucun raccourci `@property` : quatre tests des sous-lots precedents exigent
    que ce module ne contienne AUCUN `self`, et une property en introduirait un.
    Les chemins d'acces sont donc ecrits en entier (`e.workflow.livraison...`).

    Les trois champs propres correspondent aux acces que les entrees
    precedentes ne couvraient pas : les DEUX formes strictes (`_task_plan` et
    `_is_mission_run`, qui levent au lieu de tolerer) et le registre d'outils.
    """

    workflow: "EntreeWorkflowDocument"

    obtenir_plan_strict: Callable[[], Any]
    est_run_mission_strict: Callable[[], Any]
    obtenir_outils: Callable[[], Any]


def _force_mission_proactive_document_tools(e) -> list[str]:
    """Keep bounded document creation available to leads and workers."""
    if not e.est_run_mission_strict() or not hasattr(e.obtenir_outils(), "force_allow_tools"):
        return []
    return e.obtenir_outils().force_allow_tools(_MISSION_PROACTIVE_DOCUMENT_TOOLS)


def _document_workflow_progress_signature(e) -> tuple:
    """Return monotone workflow evidence for document-only stagnation checks."""
    from src.documents.delivery_manifest import workflow_progress_signature

    state = _document_workflow_proof_state(e.workflow)
    revision = state.get("revision") or {}
    revised = revision.get("proof")
    verification = state.get("verification") or {}
    verification_args = verification.get("args", {}) if isinstance(verification, dict) else {}
    history = state.get("history") or {}
    history_payload = history.get("payload", {}) if isinstance(history, dict) else {}
    history_document = (
        history_payload.get("document", {}) if isinstance(history_payload, dict) else {}
    )
    export = state.get("export") or {}
    export_record = export.get("record", {}) if isinstance(export, dict) else {}
    export_proof = export.get("proof") if isinstance(export, dict) else None
    library = state.get("library_verify") or {}
    return workflow_progress_signature(
        state.get("open_progress"),
        revised_document_id=str(getattr(revised, "document_id", "") or ""),
        verification_path=str(
            verification_args.get("path") or verification_args.get("file_path") or ""
        ),
        history_document_id=str(
            history_document.get("id") if isinstance(history_document, dict) else ""
        ),
        export_document_id=str(
            (export_record.get("id") if isinstance(export_record, dict) else "")
            or getattr(export_proof, "document_id", "") or ""
        ),
        library_document_ids=tuple(library.get("document_ids", ()))
        if isinstance(library, dict) else (),
    )


def _document_workflow_pending_action(e):
    """Return the first unproved post-generation action for this run."""
    route = _document_route_for_run(e.workflow.livraison.catalogue)
    actions = tuple(getattr(route, "workflow_actions", ()) or ())
    if not actions:
        return None
    manifest, missing, unverified = _structured_document_delivery_manifest(e.workflow.livraison)
    proof_state = _document_workflow_proof_state(e.workflow)
    for action in actions:
        operation = getattr(action, "operation", "")
        if operation == "generate":
            if missing or unverified or len(manifest) != route.requested_count:
                return action
            continue
        if operation == "open":
            if proof_state["open"] is None:
                return action
            continue
        if operation == "revise":
            if proof_state["revision"] is None:
                return action
            continue
        if operation == "verify":
            if proof_state["verification"] is None:
                return action
            continue
        if operation == "history":
            if proof_state["history"] is None:
                return action
            continue
        if operation == "export":
            if proof_state["export"] is None:
                return action
            continue
        if operation == "library_verify":
            if proof_state["library_verify"] is None:
                return action
            continue
    return None


def _document_final_fulfills_plan_task(e, task_desc: str) -> bool:
    """Reserve multi-document verification tasks for exact render proofs."""
    if not final_fulfills_task(task_desc):
        return False
    route = _document_route_for_run(e.workflow.livraison.catalogue)
    desc = _normalize_guard_token(task_desc)
    if "bilan" in desc and route.has_pending_post_actions:
        return _document_workflow_pending_action(e) is None
    if route.requested_count < 2:
        return True
    is_verification = any(
        token in desc for token in ("verif", "valid", "control", "relire", "inspect")
    )
    if not is_verification:
        return True
    _manifest, missing, unverified = _structured_document_delivery_manifest(e.workflow.livraison)
    return not missing and not unverified


def _reconcile_document_plan_from_manifest(e, iteration: int) -> int:
    """Complete document batch tasks only from exact generation proofs."""
    from collections import Counter

    if not e.workflow.livraison.catalogue.obtenir_plan():
        return 0
    route = _document_route_for_run(e.workflow.livraison.catalogue)
    if route.requested_count < 2:
        return 0
    manifest, missing, unverified = _structured_document_delivery_manifest(e.workflow.livraison)
    delivered = Counter(proof.kind for proof in manifest)
    all_render_verified = not missing and not unverified
    changed = 0
    for task in e.obtenir_plan_strict():
        if task.completed:
            continue
        desc = task.description or ""
        desc_lower = _normalize_guard_token(desc)
        if (
            len(getattr(route, "workflow_actions", ()) or ()) > 1
            and document_workflow_task_operation(desc)
        ):
            continue
        if document_workflow_task_blocks("document_manifest", desc):
            continue
        verify_task = any(
            token in desc_lower
            for token in ("verif", "valid", "control", "relire", "inspect")
        )
        if verify_task:
            if not all_render_verified:
                continue
            evidence = "tous les rendus documentaires demandes sont certifies"
            status = "verified"
        else:
            explicit_batch = "generate_studio_documents" in desc_lower
            explicit_fallback = any(
                name in desc_lower
                for name in ("create_pdf", "create_invoice_pdf", "create_from_template")
            )
            if all_render_verified and explicit_batch:
                evidence = "manifest complet du batch Document Studio"
                status = "created"
            elif all_render_verified and explicit_fallback:
                evidence = "fallback non requis: Document Studio a livre le lot complet"
                status = "not_required"
            elif all_render_verified and route.is_catalog_selection and any(
                token in desc_lower
                for token in ("gener", "cre", "produ", "redig", "ecri", "livr")
            ):
                evidence = (
                    f"selection catalogue exacte: {len(manifest)}/"
                    f"{route.requested_count} rendus certifies"
                )
                status = "created"
            else:
                required = _document_plan_required_kinds(desc)
                if not required or not any(
                    token in desc_lower
                    for token in ("gener", "cre", "produ", "redig", "ecri", "livr")
                ):
                    continue
                needed = Counter(required)
                if any(delivered[kind] < count for kind, count in needed.items()):
                    continue
                evidence = "manifest exact: " + ", ".join(required)
                status = "created"
        task.completed = True
        task.completed_at_iteration = iteration
        task.completed_by_tool = "document_manifest"
        task.completion_status = status
        task.completion_evidence = evidence
        task.completion_confidence = "strong"
        changed += 1
        logger.info("[PLAN DOCUMENT] '{}' - {}", task.description[:70], evidence)
    if changed:
        e.workflow.livraison.catalogue.emettre_etat_plan(context_tool="document_manifest")
    return changed


def _reconcile_document_workflow_plan(e, iteration: int) -> int:
    """Credit compound post-actions only after their exact proof is complete."""
    if not e.workflow.livraison.catalogue.obtenir_plan():
        return 0
    route = _document_route_for_run(e.workflow.livraison.catalogue)
    actions = tuple(getattr(route, "workflow_actions", ()) or ())
    if len(actions) <= 1:
        return 0
    action_names = [str(getattr(item, "operation", "") or "") for item in actions]
    pending = _document_workflow_pending_action(e)
    pending_name = str(getattr(pending, "operation", "") or "")
    if pending_name and pending_name in action_names:
        completed_operations = set(action_names[:action_names.index(pending_name)])
    elif pending_name:
        completed_operations = set()
    else:
        completed_operations = set(action_names)

    target = _document_workflow_target(e.workflow.livraison)
    proof_state = _document_workflow_proof_state(e.workflow)
    revision_record = proof_state.get("revision") or {}
    revised_proof = revision_record.get("proof")
    evidence_by_operation = {
        "open": (
            f"ouverture exacte {route.requested_count}/{route.requested_count}, "
            "aucun echec"
        ),
        "revise": (
            f"revision de la cible exacte {target.document_id} vers "
            f"{revised_proof.document_id}"
            if target is not None and revised_proof is not None
            else "revision cible exacte"
        ),
        "verify": "nouvelle version relue apres revision et valeur confirmee",
        "history": "relation parent/enfant exacte confirmee par l'historique",
        "export": "export enfant cree depuis la version revisee",
        "library_verify": "tous les identifiants attendus retrouves dans la bibliotheque",
    }
    changed = 0
    for task in e.obtenir_plan_strict():
        if task.completed:
            continue
        operation = document_workflow_task_operation(task.description)
        if operation not in completed_operations:
            continue
        task.completed = True
        task.completed_at_iteration = iteration
        task.completed_by_tool = "document_workflow_proof"
        task.completion_status = (
            "verified"
            if operation in {"open", "verify", "history", "library_verify"}
            else "updated"
        )
        task.completion_evidence = evidence_by_operation[operation]
        task.completion_confidence = "strong"
        changed += 1
        logger.info(
            "[PLAN DOCUMENT WORKFLOW] '{}' - {}",
            task.description[:70], task.completion_evidence,
        )
    if changed:
        e.workflow.livraison.catalogue.emettre_etat_plan(context_tool="document_workflow_proof")
    return changed


def _structured_document_tool_gate(
    e, tool_name: str, tool_args: Optional[Dict[str, Any]] = None,
):
    """Require one Studio attempt per requested structured document."""
    route = _document_route_for_run(e.workflow.livraison.catalogue)
    if not route.requires_studio or not route.owns_run:
        return None
    from src.documents.document_settings import get_document_settings
    from .react_config import Observation as _StudioObservation

    workflow_actions = tuple(getattr(route, "workflow_actions", ()) or ())
    if len(workflow_actions) > 1 and tool_name == "revise_studio_document":
        pending = _document_workflow_pending_action(e)
        pending_name = str(getattr(pending, "operation", "") or "")
        if pending_name in {"generate", "open"}:
            if pending_name == "open":
                guidance = (
                    f"Ouvre d'abord le bundle exact avec `open_document_delivery`: "
                    f"la preuve attendue est {route.requested_count}/{route.requested_count}, "
                    "failed=0."
                )
            else:
                guidance = "Termine d'abord la generation certifiee du lot exact."
            return _StudioObservation(
                content=(
                    "Ordre du workflow documentaire refuse: la revision ne peut pas "
                    f"preceder l'etape `{pending_name}`. {guidance}"
                ),
                success=False,
                origin="document_policy",
            )

    document_settings = get_document_settings()
    if (
        tool_name == "generate_studio_documents"
        and route.requested_count > document_settings.workflow_max_documents
    ):
        return _StudioObservation(
            content=(
                f"Workflow documentaire refuse: {route.requested_count} documents demandes, "
                f"maximum configure {document_settings.workflow_max_documents}. "
                "Reduis la selection ou augmente le plafond Documents dans Configuration "
                "sans depasser la limite dure de 100."
            ),
            success=False,
            origin="document_policy",
        )

    bypass_tool = _nested_document_bypass(tool_name, tool_args)
    if route.operation == "revise" and (
        bypass_tool
        or tool_name in {"generate_studio_document", "generate_studio_documents"}
    ):
        refused = bypass_tool or tool_name
        logger.warning(
            "[DOCUMENT STUDIO GATE] {} refuse: une revision ne recree jamais le document",
            refused,
        )
        return _StudioObservation(
            content=(
                f"`{refused}` refuse: la demande est une revision. Retrouve la reference "
                "exacte, puis appelle `revise_studio_document`. Si le nom exact est absent "
                "ou ambigu, demande confirmation; ne genere jamais un nouveau document de "
                "remplacement."
            ),
            success=False,
            origin="document_policy",
        )

    if route.is_catalog_selection:
        selections = route.selections or (
            SimpleNamespace(
                origin=route.selection_origin,
                limit=route.selection_limit,
                sort=route.selection_sort,
            ),
        )
        expected_catalog_keys = {
            _document_catalog_evidence_key({
                "origin": selection.origin,
                "limit": selection.limit,
                "sort": selection.sort,
            })
            for selection in selections
        }
        exact_catalog_calls = ", puis ".join(
            f"list_document_models(origin='{selection.origin}', "
            f"limit={selection.limit}, sort='{selection.sort}')"
            for selection in selections
        )

        if tool_name == "parallel_tools":
            nested = _document_parallel_calls(tool_args)
            invalid = [
                name or "<outil sans nom>" for name, args in nested
                if name != "list_document_models"
                or _document_catalog_evidence_key(args) not in expected_catalog_keys
                or bool(str(args.get("kind") or "").strip())
            ]
            if invalid:
                return _StudioObservation(
                    content=(
                        "Workflow catalogue en deux phases: `parallel_tools` peut seulement "
                        "lister les catalogues exacts. Aucune generation, ouverture ou "
                        "revision documentaire ne peut y etre imbriquee. Appelle `"
                        + exact_catalog_calls.replace(", puis ", "`, puis `")
                        + "`, attends leurs resultats, puis appelle "
                        "`generate_studio_documents` directement et sequentiellement. "
                        "Sous-appel refuse: " + ", ".join(invalid)
                    ),
                    success=False,
                    origin="document_policy",
                )
            return None

        if tool_name == "list_document_models":
            actual_key = _document_catalog_evidence_key(tool_args)
            if (
                actual_key not in expected_catalog_keys
                or bool(str((tool_args or {}).get("kind") or "").strip())
            ):
                return _StudioObservation(
                    content=(
                        "Parametres catalogue incorrects pour cette requete. Appelle `"
                        + exact_catalog_calls.replace(", puis ", "`, puis `")
                        + "` exactement; ne change ni origin, ni limit, ni sort."
                    ),
                    success=False,
                    origin="document_policy",
                )
            return None

        catalog_groups = _document_catalog_selection_groups(e.workflow.livraison.catalogue)
        expected_ids = tuple(
            str(row.get("id") or "").strip()
            for group in catalog_groups
            for row in group
            if str(row.get("id") or "").strip()
        )
        if tool_name == "generate_studio_documents":
            if (
                len(catalog_groups) != len(selections)
                or any(len(group) != selection.limit for group, selection in zip(catalog_groups, selections))
                or len(expected_ids) != route.requested_count
            ):
                calls = ", puis ".join(
                    f"list_document_models(origin='{selection.origin}', "
                    f"limit={selection.limit}, sort='{selection.sort}')"
                    for selection in selections
                ) or (
                    f"list_document_models(origin='{route.selection_origin}', "
                    f"limit={route.selection_limit}, sort='{route.selection_sort}')"
                )
                return _StudioObservation(
                    content=(
                        "Selection documentaire non prouvee. Appelle d'abord `"
                        + calls.replace(", puis ", "`, puis `") + "`, "
                        "puis reutilise exactement les template_id retournes."
                    ),
                    success=False,
                    origin="document_policy",
                )
            raw_requests = (tool_args or {}).get("requests", [])
            if isinstance(raw_requests, str):
                try:
                    raw_requests = json.loads(raw_requests)
                except (TypeError, ValueError, json.JSONDecodeError):
                    raw_requests = []
            actual_ids = tuple(
                str(item.get("template_id") or "").strip()
                for item in raw_requests
                if isinstance(item, dict)
            ) if isinstance(raw_requests, list) else ()
            proven = {
                proof.template_id
                for proof in _latest_document_batch_proofs(e.workflow.livraison.obtenir_historique(), e.workflow.livraison.obtenir_preuves_workflow())
                if proof.template_id
            }
            pending_group_index = next((
                index for index, group in enumerate(catalog_groups)
                if any(str(row.get("id") or "").strip() not in proven for row in group)
            ), None)
            remaining_group = () if pending_group_index is None else tuple(
                str(row.get("id") or "").strip()
                for row in catalog_groups[pending_group_index]
                if str(row.get("id") or "").strip()
                and str(row.get("id") or "").strip() not in proven
            )
            expected_batch = remaining_group[:len(actual_ids)]
            if (
                not actual_ids
                or len(actual_ids) > document_settings.batch_size
                or actual_ids != expected_batch
            ):
                group_label = (
                    str(selections[pending_group_index].origin)
                    if pending_group_index is not None else "termine"
                )
                return _StudioObservation(
                    content=(
                        f"Batch refuse: utilise au maximum {document_settings.batch_size} "
                        f"template_id encore manquants du groupe {group_label}, "
                        "dans l'ordre exact de ce groupe sans passer au suivant. "
                        "Prochain lot attendu: "
                        + ", ".join(remaining_group[:document_settings.batch_size])
                    ),
                    success=False,
                    origin="document_policy",
            )
            return None
        blocked_selection_tools = {
            "generate_studio_document", "find_files", "list_directory",
            "grep_search", "read_file", "read_files_batch",
            "search_document_library", "list_templates",
        }
        if bypass_tool or tool_name in blocked_selection_tools:
            refused = bypass_tool or tool_name
            return _StudioObservation(
                content=(
                    f"`{refused}` refuse pour cette selection de catalogue. Utilise "
                    "`list_document_models`, puis un seul `generate_studio_documents` "
                    "avec exactement les template_id retournes."
                ),
                success=False,
                origin="document_policy",
            )
        return None

    if not bypass_tool:
        return None
    studio_tool = (
        "revise_studio_document"
        if route.operation == "revise"
        else "generate_studio_document"
    )
    pending = list(route.requested_kinds)
    for kind in _studio_attempted_kinds(e.workflow.livraison.obtenir_historique(), studio_tool, route):
        if kind in pending:
            pending.remove(kind)
    if not pending:
        return None
    logger.warning(
        "[DOCUMENT STUDIO GATE] {} refuse; types sans tentative Studio={}",
        bypass_tool,
        pending,
    )
    return _StudioObservation(
        content=(
            f"Fallback `{bypass_tool}` refuse: {len(pending)} type(s) restent sans "
            f"tentative Studio: {', '.join(pending)}. Appelle "
            "`list_document_models(kind='<type>')`, puis "
            "`generate_studio_documents(requests=[...])` une fois pour le lot, ou "
            "`generate_studio_document(kind='<type>', data={...})` pour chaque type. "
            "Le modele par defaut, sa mise en page et le logo actif seront appliques. "
            "Le fallback historique ne sera disponible qu'apres une tentative Studio "
            "pour chaque type demande."
        ),
        success=False,
        origin="document_policy",
    )
