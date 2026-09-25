"""Conversational tools for the shared local-model manager.

Handlers return facts as compact JSON. They intentionally contain no canned
assistant reply: Lumena composes its own answer from these structured facts.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections import OrderedDict
from typing import Any, List

from .context import HandlerContext
from .contracts import HandlerResult
from .registry_v2 import HandlerDef
from ...local_models.identifiers import IdentifierError
from ...local_models.manager import LocalModelManagerError, get_local_model_manager
from ...local_models.ollama_client import OllamaClientError


def _manager():
    return get_local_model_manager()


_LAST_RESULTS: OrderedDict[tuple[int, str], list[str]] = OrderedDict()


def _result_key(ctx: HandlerContext) -> tuple[int, str]:
    return id(ctx), ctx.runtime_task_id or "direct"


def _remember_results(ctx: HandlerContext, references: list[str]) -> None:
    key = _result_key(ctx)
    _LAST_RESULTS[key] = references[:30]
    _LAST_RESULTS.move_to_end(key)
    while len(_LAST_RESULTS) > 256:
        _LAST_RESULTS.popitem(last=False)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _failure(name: str, exc: Exception) -> HandlerResult:
    code = getattr(exc, "code", str(exc))[:120] or "local_model_operation_failed"
    return HandlerResult.fail(
        code, output=_json({"ok": False, "error_code": code}), handler_name=name, status_code=code
    )


def _normalized(value: str) -> str:
    return "".join(char for char in unicodedata.normalize("NFKD", value.casefold()) if not unicodedata.combining(char))


def _explicit_user_mutation(ctx: HandlerContext, verbs: tuple[str, ...], reference: str) -> bool:
    if ctx.is_mission_run:
        return False
    query = _normalized(ctx.original_user_query or "")
    if not query or not any(re.search(rf"\b{re.escape(verb)}\w*\b", query) for verb in verbs):
        return False
    tokens = [token for token in re.split(r"[:/._-]+", _normalized(reference)) if len(token) >= 3]
    if any(token in query for token in tokens):
        return True
    remembered = _LAST_RESULTS.get(_result_key(ctx), [])
    if not remembered:
        return False
    normalized_reference = _normalized(reference)
    if "premier" in query:
        return _normalized(remembered[0]) == normalized_reference
    if "dernier" in query:
        return _normalized(remembered[-1]) == normalized_reference
    if "celui" in query and len(remembered) == 1:
        return _normalized(remembered[0]) == normalized_reference
    return False


async def search_local_models_handler(
    ctx: HandlerContext, query: str = "", source: str = "all", limit: int = 10
) -> HandlerResult:
    name = "search_local_models"
    try:
        result = await _manager().search(query, source=source, limit=max(1, min(limit, 30)), offset=0)
        _remember_results(
            ctx,
            [item["reference"]["canonical"] for item in result.get("models", []) if item.get("reference")],
        )
        return HandlerResult.ok(_json({"ok": True, **result}), handler_name=name)
    except (ValueError, OllamaClientError) as exc:
        return _failure(name, exc)


async def inspect_local_model_handler(ctx: HandlerContext, reference: str, source: str = "ollama") -> HandlerResult:
    name = "inspect_local_model"
    try:
        from ...local_models.identifiers import parse_model_reference

        ref = parse_model_reference(reference, source)
        installed = await _manager().client.list_installed()
        match = next(
            (
                item
                for item in installed
                if item.reference.pull_reference == ref.pull_reference or item.reference.canonical == ref.canonical
            ),
            None,
        )
        details = await _manager().client.show(ref.pull_reference) if match else None
        state = _manager().state_store.entry(ref)
        return HandlerResult.ok(
            _json(
                {
                    "ok": True,
                    "reference": ref.as_dict(),
                    "installed": match.as_dict() if match else None,
                    "lumena_state": state,
                    "ollama_details": details,
                }
            ),
            handler_name=name,
        )
    except (IdentifierError, OllamaClientError) as exc:
        return _failure(name, exc)


async def list_installed_local_models_handler(ctx: HandlerContext) -> HandlerResult:
    name = "list_installed_local_models"
    try:
        return HandlerResult.ok(_json({"ok": True, "models": await _manager().installed()}), handler_name=name)
    except OllamaClientError as exc:
        return _failure(name, exc)


async def recommend_local_model_handler(
    ctx: HandlerContext, intent: str = "general", query: str = "", source: str = "all", limit: int = 5
) -> HandlerResult:
    name = "recommend_local_model"
    try:
        result = await _manager().recommend(query, intent=intent, source=source, limit=max(1, min(limit, 10)))
        _remember_results(
            ctx,
            [
                item["model"]["reference"]["canonical"]
                for item in result.get("recommendations", [])
                if item.get("model", {}).get("reference")
            ],
        )
        return HandlerResult.ok(_json({"ok": True, **result}), handler_name=name)
    except (ValueError, OllamaClientError) as exc:
        return _failure(name, exc)


async def list_local_model_jobs_handler(ctx: HandlerContext, limit: int = 20) -> HandlerResult:
    jobs = [job.as_dict() for job in _manager().job_store.list(max(1, min(limit, 100)))]
    return HandlerResult.ok(_json({"ok": True, "jobs": jobs}), handler_name="list_local_model_jobs")


async def get_local_model_job_handler(ctx: HandlerContext, job_id: str) -> HandlerResult:
    name = "get_local_model_job"
    job = _manager().job_store.get(job_id)
    if job is None:
        return _failure(name, LocalModelManagerError("job_not_found"))
    return HandlerResult.ok(_json({"ok": True, "job": job.as_dict()}), handler_name=name)


async def install_local_model_handler(
    ctx: HandlerContext, reference: str, source: str = "ollama", enable_after_install: bool = True
) -> HandlerResult:
    name = "install_local_model"
    if not _explicit_user_mutation(ctx, ("install", "telecharg", "download", "pull", "ajout"), reference):
        return _failure(name, LocalModelManagerError("explicit_user_install_request_required"))
    try:
        job = _manager().install(
            reference, source=source, enable_after_install=enable_after_install, caller_kind="chat"
        )
        return HandlerResult.ok(
            _json({"ok": True, "accepted": True, "job": job.as_dict()}), handler_name=name, status_code="accepted"
        )
    except (IdentifierError, LocalModelManagerError) as exc:
        return _failure(name, exc)


async def enable_local_model_handler(ctx: HandlerContext, reference: str, source: str = "ollama") -> HandlerResult:
    name = "enable_local_model"
    if not _explicit_user_mutation(ctx, ("activ", "enable", "utilis"), reference):
        return _failure(name, LocalModelManagerError("explicit_user_enable_request_required"))
    try:
        return HandlerResult.ok(
            _json({"ok": True, "result": await _manager().enable(reference, source)}), handler_name=name
        )
    except (IdentifierError, LocalModelManagerError, OllamaClientError) as exc:
        return _failure(name, exc)


async def disable_local_model_handler(ctx: HandlerContext, reference: str, source: str = "ollama") -> HandlerResult:
    name = "disable_local_model"
    if not _explicit_user_mutation(ctx, ("desactiv", "disable", "retir"), reference):
        return _failure(name, LocalModelManagerError("explicit_user_disable_request_required"))
    try:
        current = getattr(getattr(ctx.lumena, "llm", None), "model_name", "")
        return HandlerResult.ok(
            _json({"ok": True, "result": _manager().disable(reference, source, current_model_key=current)}),
            handler_name=name,
        )
    except (IdentifierError, LocalModelManagerError) as exc:
        return _failure(name, exc)


async def select_local_model_handler(
    ctx: HandlerContext, reference: str, source: str = "ollama", role: str = "primary"
) -> HandlerResult:
    name = "select_local_model"
    if not _explicit_user_mutation(ctx, ("selection", "choisi", "met", "mets", "utilis", "principal"), reference):
        return _failure(name, LocalModelManagerError("explicit_user_select_request_required"))
    try:
        runtime_llm = getattr(getattr(ctx.lumena, "llm", None), "model_name", None)
        runtime_llm = getattr(ctx.lumena, "llm", None) if runtime_llm is not None else None
        result = await _manager().select(reference, source, runtime_llm=runtime_llm, role=role)
        return HandlerResult.ok(_json({"ok": True, "result": result}), handler_name=name)
    except (IdentifierError, LocalModelManagerError, OllamaClientError) as exc:
        return _failure(name, exc)


async def unload_local_model_handler(ctx: HandlerContext, reference: str, source: str = "ollama") -> HandlerResult:
    name = "unload_local_model"
    if not _explicit_user_mutation(ctx, ("decharg", "unload", "liber"), reference):
        return _failure(name, LocalModelManagerError("explicit_user_unload_request_required"))
    try:
        return HandlerResult.ok(
            _json({"ok": True, "result": await _manager().unload(reference, source)}), handler_name=name
        )
    except (IdentifierError, LocalModelManagerError, OllamaClientError) as exc:
        return _failure(name, exc)


async def verify_local_model_handler(ctx: HandlerContext, reference: str, source: str = "ollama") -> HandlerResult:
    name = "verify_local_model"
    if not _explicit_user_mutation(ctx, ("verif", "test", "controle"), reference):
        return _failure(name, LocalModelManagerError("explicit_user_verify_request_required"))
    try:
        result = await _manager().verify(reference, source)
        return HandlerResult.ok(_json({"ok": True, "result": result}), handler_name=name)
    except (IdentifierError, LocalModelManagerError, OllamaClientError) as exc:
        return _failure(name, exc)


async def prepare_delete_local_model_handler(
    ctx: HandlerContext, reference: str, source: str = "ollama"
) -> HandlerResult:
    name = "prepare_delete_local_model"
    if not _explicit_user_mutation(ctx, ("supprim", "effac", "delete", "remove"), reference):
        return _failure(name, LocalModelManagerError("explicit_user_delete_request_required"))
    try:
        current = getattr(getattr(ctx.lumena, "llm", None), "model_name", "")
        result = await _manager().prepare_delete(reference, source, current_model_key=current)
        return HandlerResult.ok(
            _json({"ok": True, "confirmation_required": True, **result}),
            handler_name=name,
            status_code="confirmation_required",
        )
    except (IdentifierError, LocalModelManagerError, OllamaClientError) as exc:
        return _failure(name, exc)


async def confirm_delete_local_model_handler(
    ctx: HandlerContext, reference: str, ticket: str, source: str = "ollama"
) -> HandlerResult:
    name = "confirm_delete_local_model"
    query = _normalized(ctx.original_user_query or "")
    if (
        ctx.is_mission_run
        or not re.search(r"\b(confirm\w*|oui\s+supprim\w*|delete\s+confirm\w*)\b", query)
        or not _explicit_user_mutation(ctx, ("supprim", "effac", "delete", "remove", "confirm"), reference)
    ):
        return _failure(name, LocalModelManagerError("human_delete_confirmation_required"))
    try:
        current = getattr(getattr(ctx.lumena, "llm", None), "model_name", "")
        result = await _manager().delete(reference, ticket, source, current_model_key=current, caller_kind="chat")
        return HandlerResult.ok(_json({"ok": True, "result": result}), handler_name=name)
    except (IdentifierError, LocalModelManagerError, OllamaClientError) as exc:
        return _failure(name, exc)


def get_local_model_handler_defs() -> List[HandlerDef]:
    source_schema = {"type": "string", "enum": ["ollama", "huggingface"], "default": "ollama"}
    reference_schema = {
        "type": "string",
        "description": "Identifiant exact retourné par la recherche, jamais une URL",
        "maxLength": 384,
    }
    return [
        HandlerDef(
            "search_local_models",
            (
                "Cherche des modèles Ollama et Hugging Face GGUF. "
                "Retourne uniquement des faits structurés et ne modifie rien."
            ),
            {
                "properties": {
                    "query": {"type": "string", "maxLength": 160},
                    "source": {"type": "string", "enum": ["all", "ollama", "huggingface"]},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 30},
                },
                "required": [],
            },
            search_local_models_handler,
            "local_models",
            "handlers.local_models",
        ),
        HandlerDef(
            "inspect_local_model",
            "Inspecte la présence, l'état Lumena et les métadonnées prouvées d'un modèle.",
            {"properties": {"reference": reference_schema, "source": source_schema}, "required": ["reference"]},
            inspect_local_model_handler,
            "local_models",
            "handlers.local_models",
        ),
        HandlerDef(
            "list_installed_local_models",
            "Liste les modèles réellement présents dans Ollama avec leurs états distincts.",
            {"properties": {}, "required": []},
            list_installed_local_models_handler,
            "local_models",
            "handlers.local_models",
        ),
        HandlerDef(
            "recommend_local_model",
            "Classe des modèles selon le matériel et le besoin, avec raisons, inconnues et confiance. Ne modifie rien.",
            {
                "properties": {
                    "intent": {"type": "string", "maxLength": 64},
                    "query": {"type": "string", "maxLength": 160},
                    "source": {"type": "string", "enum": ["all", "ollama", "huggingface"]},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 10},
                },
                "required": [],
            },
            recommend_local_model_handler,
            "local_models",
            "handlers.local_models",
        ),
        HandlerDef(
            "list_local_model_jobs",
            "Liste les installations et opérations locales avec leur état prouvé.",
            {"properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 100}}, "required": []},
            list_local_model_jobs_handler,
            "local_models",
            "handlers.local_models",
        ),
        HandlerDef(
            "get_local_model_job",
            "Relit un job précis avant d'affirmer sa réussite ou son échec.",
            {"properties": {"job_id": {"type": "string", "minLength": 32, "maxLength": 32}}, "required": ["job_id"]},
            get_local_model_job_handler,
            "local_models",
            "handlers.local_models",
        ),
        HandlerDef(
            "install_local_model",
            (
                "Installe un identifiant exact seulement quand la demande utilisateur courante "
                "l'autorise explicitement. Retourne un job à relire; ne jamais annoncer la "
                "réussite sur le seul accusé de réception."
            ),
            {
                "properties": {
                    "reference": reference_schema,
                    "source": source_schema,
                    "enable_after_install": {"type": "boolean", "default": True},
                },
                "required": ["reference"],
            },
            install_local_model_handler,
            "local_models",
            "handlers.local_models",
        ),
        HandlerDef(
            "enable_local_model",
            "Active dans Lumena un modèle déjà installé, seulement sur demande utilisateur explicite.",
            {"properties": {"reference": reference_schema, "source": source_schema}, "required": ["reference"]},
            enable_local_model_handler,
            "local_models",
            "handlers.local_models",
        ),
        HandlerDef(
            "disable_local_model",
            "Désactive un modèle dans Lumena sans supprimer ses fichiers, seulement sur demande utilisateur explicite.",
            {"properties": {"reference": reference_schema, "source": source_schema}, "required": ["reference"]},
            disable_local_model_handler,
            "local_models",
            "handlers.local_models",
        ),
        HandlerDef(
            "select_local_model",
            (
                "Affecte un modèle local vérifié et activé comme cerveau principal ou spécialisé, "
                "sur demande utilisateur explicite."
            ),
            {
                "properties": {
                    "reference": reference_schema,
                    "source": source_schema,
                    "role": {"type": "string", "enum": ["primary", "code", "vision", "web"]},
                },
                "required": ["reference"],
            },
            select_local_model_handler,
            "local_models",
            "handlers.local_models",
        ),
        HandlerDef(
            "unload_local_model",
            "Décharge un modèle de la RAM/VRAM sans le désactiver ni supprimer ses poids, sur demande explicite.",
            {"properties": {"reference": reference_schema, "source": source_schema}, "required": ["reference"]},
            unload_local_model_handler,
            "local_models",
            "handlers.local_models",
        ),
        HandlerDef(
            "verify_local_model",
            "Exécute les canaris bornés d'un modèle installé et retourne les capacités observées.",
            {"properties": {"reference": reference_schema, "source": source_schema}, "required": ["reference"]},
            verify_local_model_handler,
            "local_models",
            "handlers.local_models",
        ),
        HandlerDef(
            "prepare_delete_local_model",
            (
                "Analyse l'impact et crée un ticket court pour une suppression demandée "
                "explicitement. Cette étape ne supprime rien et exige ensuite une confirmation "
                "humaine distincte."
            ),
            {"properties": {"reference": reference_schema, "source": source_schema}, "required": ["reference"]},
            prepare_delete_local_model_handler,
            "local_models",
            "handlers.local_models",
        ),
        HandlerDef(
            "confirm_delete_local_model",
            (
                "Consomme un ticket de suppression à usage unique uniquement si le message "
                "utilisateur courant confirme explicitement la suppression. Relire le résultat "
                "avant d'affirmer l'absence."
            ),
            {
                "properties": {
                    "reference": reference_schema,
                    "ticket": {"type": "string", "minLength": 32, "maxLength": 128},
                    "source": source_schema,
                },
                "required": ["reference", "ticket"],
            },
            confirm_delete_local_model_handler,
            "local_models",
            "handlers.local_models",
        ),
    ]
