"""Opt-in Codex decision brain for Lumena's existing ReAct runtime.

Codex produces exactly one structured ReAct decision per model call. Lumena
alone executes tools and keeps policies, mission context, ledger, truth locks,
task state and the final delivery chokepoint. The older whole-turn bridge is
kept below for compatibility tests, but is no longer wired into ``ReActLoop``.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping, Sequence

from loguru import logger

from src.llm.codex_app_server import (
    CodexAppServerConfig,
    CodexAppServerError,
    CodexAppServerSupervisor,
    CodexAppServerTimeout,
    codex_compatibility_config_overrides,
    get_shared_codex_app_server,
)
from src.llm.codex_mcp_bridge import LumenaCodexToolBridge
from src.llm.codex_subscription import (
    CodexSubscriptionGateway,
    CodexSubscriptionSettings,
    CodexSurface,
    OpenAIAccessMode,
    load_codex_subscription_settings,
)
from src.reasoning.react_config import Action, ActionType, Observation, ReActStep, Thought
from src.reasoning.test_proof import is_test_command, parse_test_outcome
from src.runtime.execution_ledger import _extract_proof, _extract_target
from src.utils.paths import ROOT_DIR


THREAD_START_METHOD = "thread/start"
TURN_START_METHOD = "turn/start"
TURN_STEER_METHOD = "turn/steer"
TURN_INTERRUPT_METHOD = "turn/interrupt"
THREAD_ARCHIVE_METHOD = "thread/archive"

_CONTROL_TOOLS = frozenset({"final_answer", "ask_user"})
_CODEX_RESPONSE_META: ContextVar[dict[str, Any] | None] = ContextVar(
    "lumena_codex_response_meta",
    default=None,
)
_CODEX_CODEAGENT_BRAIN: ContextVar[Any | None] = ContextVar(
    "lumena_codex_codeagent_brain",
    default=None,
)
# LOT Z34 phase 1 — `website` manquait, et ça a coûté une preuve.
#
# Run du 21/08, « genere moi un site web mais en motion design » :
#   sélection contextuelle : 84 outils (files, web, agents, system, project…)
#     → la catégorie `website` n'est PAS retenue
#   expansion Codex        : 260 outils — web → {browser, files, documents}
#     → jamais `website`
#   résultat : generate_website ABSENT, serve_website ABSENT
#
# Codex a trouvé `generate_website` via `discover_tools` (qui indexe les 732),
# a cherché « un outil générique pour appeler un outil découvert par son nom »,
# n'a rien trouvé, et a écrit le site à la main. Surtout : sans `serve_website`
# il n'a pas pu servir la preview, donc pas de `browser_navigate`, donc pas de
# preuve — le truth-lock a collé « Navigateur NON vérifié » sur un livrable
# pourtant correct (32 ko, 3 fichiers liés).
#
# Ajout CIBLÉ, pas massif : déclarer les 597 outils coûterait 78 k tokens de
# contexte À CHAQUE TOUR (mesuré). On relie `website` là où il a un sens —
# faire du web, ou mener un projet — et le reste passe par `invoke_tool`
# (phase 2), qui supprime le mur sans gonfler la déclaration.
_TOOL_TRANSITIONS: Mapping[str, frozenset[str]] = {
    "browser": frozenset({"files", "documents"}),
    "files": frozenset({"system", "mail"}),
    "web": frozenset({"browser", "files", "documents", "website"}),
    "website": frozenset({"browser", "files", "web"}),
    "mail": frozenset({"files", "social"}),
    "system": frozenset({"files", "mail"}),
    "project": frozenset({"git", "files", "codebase", "website"}),
    "social": frozenset({"web", "files"}),
    "automation": frozenset({"web", "system", "mail"}),
}


class CodexReActUnavailable(RuntimeError):
    """Selected Codex Agent/Mission surface cannot execute; never API-fallback."""


_REACT_DECISION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "thought": {"type": "string"},
        "action": {"type": "string"},
        "action_input": {"type": "string"},
    },
    "required": ["thought", "action", "action_input"],
}
_FORBIDDEN_BRAIN_ITEM_TYPES = frozenset(
    {
        "commandExecution",
        "fileChange",
        "mcpToolCall",
        "dynamicToolCall",
        "webSearch",
    }
)


def _decision_prompt(messages: Sequence[Mapping[str, Any]]) -> str:
    """Wrap the normal ReAct prompt without changing its historical content."""

    prompt = "\n\n".join(
        str(message.get("content", "") or "")
        for message in messages
        if isinstance(message, Mapping)
    ).strip()
    return (
        "Tu es uniquement le cerveau de decision d'une iteration ReAct Lumena.\n"
        "N'execute AUCUN outil Codex, MCP, shell, fichier ou recherche. Lumena "
        "executera elle-meme l'action apres validation de ses politiques.\n"
        "Retourne exactement l'objet JSON impose. `action` est le nom exact d'un "
        "outil visible dans le prompt, ou `FINAL`. `action_input` est une CHAINE: "
        "pour un outil elle contient son objet JSON encode; pour FINAL elle contient "
        "la reponse utilisateur. `thought` reste bref et ne contient aucun raisonnement "
        "cache detaille.\n\n"
        "=== PROMPT REACT LUMENA AUTORITAIRE ===\n"
        + prompt
    )


def _parse_codex_decision(value: str) -> str:
    """Convert a schema-constrained Codex answer to Lumena's native wire format."""

    text = str(value or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].lstrip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        payload = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise CodexReActUnavailable(
            "Codex n'a pas retourne une decision ReAct JSON valide"
        ) from exc
    if not isinstance(payload, Mapping):
        raise CodexReActUnavailable("La decision Codex ReAct n'est pas un objet JSON")
    thought = str(payload.get("thought", "") or "").strip()
    action = str(payload.get("action", "") or "").strip()
    action_input = payload.get("action_input", "")
    if not action:
        raise CodexReActUnavailable("La decision Codex ReAct ne contient aucune action")
    if not isinstance(action_input, str):
        action_input = json.dumps(action_input, ensure_ascii=False)
    return (
        f"THOUGHT: {thought or 'Je choisis la prochaine action utile.'}\n"
        f"ACTION: {action}\n"
        f"ACTION_INPUT: {action_input}"
    )


class CodexReActBrain:
    """API-shaped callable backed by one isolated Codex App Server process."""

    # ── Points d'extension ────────────────────────────────────────────────
    # `__call__` porte toute la machinerie : processus isole, sandbox
    # lecture-seule sans reseau, detection d'effet, archivage du thread. Le
    # CodeAgent a besoin de la MEME machinerie avec un contrat different
    # (texte libre au lieu d'une decision JSON schematisee), d'ou ces cinq
    # points plutot qu'une duplication du corps.
    _service_name: str = "lumena-react-brain"
    _output_schema: Any = _REACT_DECISION_SCHEMA   # None = texte libre (CodeAgent)
    _turn_timeout: float = 220.0

    def _build_prompt(self, messages: Sequence[Mapping[str, Any]]) -> str:
        return _decision_prompt(messages)

    def _parse_final(self, final_text: str) -> str:
        return _parse_codex_decision(final_text)

    def __init__(self, react: Any, settings: CodexSubscriptionSettings) -> None:
        self.react = react
        self.settings = settings
        self.supervisor: CodexAppServerSupervisor | None = None
        self.model = ""
        self._tempdir: tempfile.TemporaryDirectory[str] | None = None
        self._last_meta: dict[str, Any] = {
            "provider_requested": "openai-codex",
            "provider_used": "openai-codex",
            "model_requested": settings.default_model or "auto",
            "model_used": settings.default_model or "auto",
            "access_source_requested": "codex",
            "access_source_used": "codex",
            "billing_source": "chatgpt_subscription",
            "fallback_used": False,
            "fallback_reason": None,
            "fallback_attempts": [],
            "finish_reason": "pending",
        }

    def get_last_response_meta(self) -> dict[str, Any]:
        return dict(self._last_meta)

    async def _ensure_started(self) -> None:
        if self.supervisor is not None and self.supervisor.is_running:
            return
        shared = get_shared_codex_app_server()
        if shared is None or not shared.is_running:
            try:
                from src.llm.codex_app_server import ensure_shared_codex_app_server

                shared = await ensure_shared_codex_app_server()
            except Exception as exc:
                logger.debug("[Codex/ReAct brain] reouverture impossible: {}", exc)
                shared = None
        if shared is None or not shared.is_running:
            raise CodexReActUnavailable(
                "Aucune session Codex connectee (reouverture automatique tentee, "
                "sans succes). Ouvre Configuration > Acces OpenAI."
            )
        executable = str(shared.config.command[0]) if shared.config.command else ""
        if not executable:
            raise CodexReActUnavailable("Executable Codex introuvable")

        self._tempdir = tempfile.TemporaryDirectory(prefix="lumena-codex-react-")
        environment = dict(shared.config.environ or os.environ)
        overrides = codex_compatibility_config_overrides(environment)
        # The decision brain must never inherit user MCP servers. ReAct owns tools.
        overrides = {**overrides, "mcp_servers": {}}
        config = CodexAppServerConfig.from_executable(
            executable,
            cwd=self._tempdir.name,
            environ=environment,
            config_overrides=overrides,
            request_timeout_s=30,
            handshake_timeout_s=20,
            max_auto_restarts=1,
        )
        self.supervisor = CodexAppServerSupervisor(config)
        try:
            await self.supervisor.start()
            gateway = CodexSubscriptionGateway(self.supervisor)
            await gateway.require_chatgpt_account()
            models = await gateway.list_models()
            if not models:
                raise CodexReActUnavailable("Le compte Codex ne retourne aucun modele")
            self.model = _select_model(models, self.settings.default_model)
        except CodexReActUnavailable:
            await self.aclose()
            raise
        except Exception as exc:
            await self.aclose()
            raise CodexReActUnavailable(f"Session ChatGPT Codex inutilisable: {exc}") from exc

    async def __call__(
        self,
        messages: Sequence[Mapping[str, Any]],
        stop: Sequence[str] | None = None,
    ) -> str:
        del stop  # The JSON output schema is the authoritative stop contract.
        await self._ensure_started()
        assert self.supervisor is not None
        assert self._tempdir is not None
        thread_id = ""
        turn_id = ""
        final_text = ""
        effectful_items: list[str] = []
        try:
            thread_params: dict[str, Any] = {
                "cwd": self._tempdir.name,
                "approvalPolicy": "never",
                "sandbox": "read-only",
                "serviceName": self._service_name,
            }
            if self.model:
                thread_params["model"] = self.model
            started = await self.supervisor.request(
                THREAD_START_METHOD, thread_params, timeout=30
            )
            thread_id = _id_from_result(started, "thread")
            if not thread_id:
                raise CodexReActUnavailable("Codex n'a retourne aucun thread ReAct")
            turn_params: dict[str, Any] = {
                "threadId": thread_id,
                "input": [{"type": "text", "text": self._build_prompt(messages)}],
                "cwd": self._tempdir.name,
                "approvalPolicy": "never",
                "sandboxPolicy": {"type": "readOnly", "networkAccess": False},
            }
            if self._output_schema is not None:
                turn_params["outputSchema"] = self._output_schema
            if self.model:
                turn_params["model"] = self.model
            started_turn = await self.supervisor.request(
                TURN_START_METHOD, turn_params, timeout=30
            )
            turn_id = _id_from_result(started_turn, "turn")
            if not turn_id:
                raise CodexReActUnavailable("Codex n'a retourne aucun tour ReAct")

            async with asyncio.timeout(self._turn_timeout):
                while True:
                    if _cancel_requested(self.react):
                        raise asyncio.CancelledError
                    try:
                        notification = await self.supervisor.next_notification(timeout=1.0)
                    except CodexAppServerTimeout:
                        continue
                    if not _event_matches(
                        notification.params, thread_id=thread_id, turn_id=turn_id
                    ):
                        continue
                    params = (
                        notification.params
                        if isinstance(notification.params, Mapping)
                        else {}
                    )
                    if notification.method == "item/completed":
                        item = params.get("item")
                        if isinstance(item, Mapping):
                            item_type = str(item.get("type", "") or "")
                            if item_type == "agentMessage":
                                final_text = str(item.get("text", "") or final_text)
                            elif item_type in _FORBIDDEN_BRAIN_ITEM_TYPES:
                                effectful_items.append(item_type)
                    elif notification.method == "turn/completed":
                        turn = params.get("turn")
                        turn = turn if isinstance(turn, Mapping) else {}
                        status = str(turn.get("status", "") or "")
                        if status != "completed":
                            raise CodexReActUnavailable(
                                f"Tour Codex ReAct termine avec status={status}: {turn.get('error')}"
                            )
                        if effectful_items:
                            raise CodexReActUnavailable(
                                "Le cerveau Codex a tente d'executer hors de Lumena: "
                                + ", ".join(effectful_items)
                            )
                        decision = self._parse_final(final_text)
                        _record_codex_response_meta(
                            configured_model=self.settings.default_model,
                            selected_model=self.model,
                        )
                        self._last_meta = peek_codex_response_meta()
                        logger.debug(
                            "[Codex/ReAct brain] decision model={} task={}",
                            self.model or "server-default",
                            getattr(self.react, "task_id", None),
                        )
                        return decision
        except (asyncio.CancelledError, TimeoutError, CodexAppServerTimeout):
            await _interrupt_turn(self.supervisor, thread_id, turn_id)
            raise
        except CodexReActUnavailable:
            raise
        except CodexAppServerError as exc:
            raise CodexReActUnavailable(f"Codex App Server indisponible: {exc}") from exc
        finally:
            if thread_id and self.supervisor.is_running:
                try:
                    await self.supervisor.request(
                        THREAD_ARCHIVE_METHOD, {"threadId": thread_id}, timeout=5
                    )
                except Exception:
                    pass

    async def aclose(self) -> None:
        supervisor, self.supervisor = self.supervisor, None
        if supervisor is not None:
            await supervisor.stop()
        tempdir, self._tempdir = self._tempdir, None
        if tempdir is not None:
            try:
                tempdir.cleanup()
            except OSError:
                pass


class CodexCodeAgentBrain(CodexReActBrain):
    """Cerveau Codex pour la boucle CodeAgent HISTORIQUE, contrat `llm.chat`.

    Pourquoi cette classe existe
    ----------------------------
    Le rail `run_codeagent_with_codex_subscription` remplacait TOUTE la boucle
    CodeAgent par un tour Codex autonome : prompts, outils, tests, retries et
    garde-fous de Lumena etaient contournes, et chaque delegation rouvrait une
    session (`account/read` puis `model/list`, 30 s chacun au timeout).

    Ici, Codex ne fournit que le TEXTE de chaque decision, exactement comme
    `MultiProviderLLM.chat()`. La boucle historique reste seule proprietaire des
    outils, du perimetre, des tests, des reprises et des preuves.

    Deux differences avec le cerveau ReAct, et seulement deux :
      * une enveloppe schema-contraint transporte le texte brut attendu par le
        parseur CodeAgent, au lieu du schema d'action ReAct ;
      * un tour plus long — une iteration CodeAgent reflechit davantage qu'une
        decision ReAct (3 min 39 mesurees sur un run reel).

    L'isolation est identique et non negociable : dossier temporaire en lecture
    seule, sans reseau, sans MCP. Toute tentative d'effet cote Codex
    (`commandExecution`, `fileChange`, appel MCP, recherche web) fait echouer le
    tour — les outils passent par `ToolRegistry`, jamais autrement.
    """

    _service_name = "lumena-codeagent-brain"
    _output_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {"response": {"type": "string"}},
        "required": ["response"],
    }
    _turn_timeout = 600.0                # une iteration CodeAgent est longue

    def __init__(self, react: Any, settings: CodexSubscriptionSettings) -> None:
        super().__init__(react, settings)
        self._chat_lock = asyncio.Lock()
        self._closing = False

    def _build_prompt(self, messages: Sequence[Mapping[str, Any]]) -> str:
        """Transmet le prompt CodeAgent tel quel, sans le reecrire.

        Le prompt historique porte deja son format d'action, ses exemples et
        ses garde-fous. Y superposer des consignes creerait deux contrats
        concurrents. On ajoute uniquement l'interdiction d'agir hors de Lumena,
        que l'isolation impose de toute facon.
        """
        prompt = "\n\n".join(
            str(message.get("content", "") or "")
            for message in messages
            if isinstance(message, Mapping)
        ).strip()
        return (
            "Tu es le cerveau de decision d'une iteration du CodeAgent Lumena.\n"
            "N'execute AUCUN outil Codex, MCP, shell, fichier ou recherche : "
            "Lumena executera elle-meme l'action que tu choisis.\n"
            "Le transport exige un objet JSON avec une unique cle `response`. "
            "Place dans `response` le TEXTE EXACT attendu par le prompt ci-dessous "
            "(action JSON, plan, jugement ou resume selon l'appel). N'ajoute aucun "
            "markdown ni commentaire autour.\n\n"
            "=== PROMPT CODEAGENT LUMENA AUTORITAIRE ===\n"
            + prompt
        )

    def _parse_final(self, final_text: str) -> str:
        """Extrait le texte transporte; le CodeAgent garde son propre parseur."""
        try:
            payload = json.loads(str(final_text or "").strip())
        except (TypeError, ValueError) as exc:
            raise CodexReActUnavailable(
                "Codex n'a pas retourne l'enveloppe CodeAgent JSON attendue"
            ) from exc
        if not isinstance(payload, Mapping):
            raise CodexReActUnavailable(
                "L'enveloppe Codex CodeAgent n'est pas un objet JSON"
            )
        response = payload.get("response")
        if not isinstance(response, str) or not response.strip():
            raise CodexReActUnavailable(
                "L'enveloppe Codex CodeAgent ne contient aucune reponse"
            )
        return response.strip()

    # ── Contrat `llm.chat` attendu par la boucle historique ───────────────
    # `sub_agent.py` appelle uniquement `llm.chat(messages=, temperature=,
    # max_tokens=)` sur l'objet rendu par `_get_llm(task)` — verifie sur ses
    # 7 points d'appel. `max_output_tokens` est lu par defaut a la ligne 3492.
    max_output_tokens: int = 65536

    @property
    def model_name(self) -> str:
        return self.model or self.settings.default_model or "codex-subscription"

    async def chat(
        self,
        messages: Sequence[Mapping[str, Any]],
        temperature: float = 0.2,
        max_tokens: int | None = None,
        **_ignored: Any,
    ) -> str:
        """Une decision CodeAgent, rendue en texte.

        `temperature` et `max_tokens` sont acceptes pour respecter le contrat
        d'appel, mais l'App Server ne les expose pas : les ignorer silencieusement
        serait le defaut que ce lot corrige, donc on le dit ici plutot que nulle
        part.
        """
        del temperature, max_tokens
        if self._closing:
            raise CodexReActUnavailable("Le cerveau Codex CodeAgent est deja ferme")
        async with self._chat_lock:
            if self._closing:
                raise CodexReActUnavailable("Le cerveau Codex CodeAgent est deja ferme")
            return await self(messages)

    async def aclose(self) -> None:
        """Attend l'appel actif puis ferme exactement une fois le processus prive."""
        self._closing = True
        async with self._chat_lock:
            await super().aclose()


class CodexTextBrain(CodexCodeAgentBrain):
    """API-shaped text model backed by the configured Codex subscription.

    This adapter is used only as an alternate *model access source*.  It cannot
    execute Codex tools: the same read-only, no-network, no-MCP isolation as the
    ReAct decision brain remains authoritative.  Consequently any tool syntax
    in the answer is still parsed and executed by the calling Lumena runtime.
    """

    _service_name = "lumena-codex-text-backend"
    _turn_timeout = 600.0

    def _build_prompt(self, messages: Sequence[Mapping[str, Any]]) -> str:
        rendered: list[str] = []
        for message in messages:
            if not isinstance(message, Mapping):
                continue
            role = str(message.get("role", "user") or "user").strip().upper()
            content = str(message.get("content", "") or "")
            rendered.append(f"[{role}]\n{content}")
        return (
            "Tu fournis uniquement la reponse modele demandee par Lumena.\n"
            "N'execute AUCUN outil Codex, MCP, shell, fichier ou recherche. "
            "Si le prompt demande un format d'action, respecte-le textuellement: "
            "Lumena seule validera et executera l'action.\n"
            "Le transport exige un objet JSON avec une unique cle `response`; "
            "place-y le texte exact de la reponse, sans commentaire autour.\n\n"
            "=== MESSAGES LUMENA AUTORITAIRES ===\n"
            + "\n\n".join(rendered)
        )


async def chat_with_codex_rescue(
    messages: Sequence[Mapping[str, Any]],
    *,
    requested_model: str = "",
    owner: Any = None,
    settings: CodexSubscriptionSettings | None = None,
) -> dict[str, Any]:
    """Attempt one text call through Codex, never through the paid API.

    ``access_mode`` describes the selected primary source, so API mode is valid
    here.  Rescue eligibility is instead explicit and requires a previously
    configured Codex model.  Quota is checked before starting the model turn.
    """

    resolved = settings or load_codex_subscription_settings()
    if not resolved.rescue_configured:
        raise CodexReActUnavailable("Secours Codex non configure")
    effective = replace(
        resolved,
        access_mode=OpenAIAccessMode.CHATGPT_CODEX,
        # The subscription model is an explicit user choice. The failed API
        # model is useful trace context, not permission to overwrite it.
        default_model=str(resolved.default_model or requested_model).strip(),
    )
    brain = CodexTextBrain(owner, effective)
    try:
        await brain._ensure_started()
        assert brain.supervisor is not None
        quota = await CodexSubscriptionGateway(brain.supervisor).read_rate_limits()
        if quota.exhausted:
            raise CodexReActUnavailable("Quota de l'abonnement Codex epuise")
        text = await brain.chat(messages=messages)
        meta = brain.get_last_response_meta()
        return {
            "text": text,
            "provider_used": "openai-codex",
            "model_used": brain.model_name,
            "finish_reason": meta.get("finish_reason") or "stop",
            "prompt_tokens": meta.get("prompt_tokens"),
            "completion_tokens": meta.get("completion_tokens"),
            "access_source": "codex",
            "billing_source": "chatgpt_subscription",
        }
    finally:
        await brain.aclose()


def get_active_codex_codeagent_brain() -> CodexCodeAgentBrain | None:
    """Retourne le cerveau lie a la tache courante, jamais un nouvel objet."""
    brain = _CODEX_CODEAGENT_BRAIN.get()
    return brain if isinstance(brain, CodexCodeAgentBrain) else None


@asynccontextmanager
async def codex_codeagent_brain_scope(
    agent: Any,
    *,
    settings: CodexSubscriptionSettings | None = None,
):
    """Installe le cerveau Codex sur UNE delegation CodeAgent, puis le retire.

    Rend `(actif, cerveau)`. Hors abonnement Codex, rend `(False, None)` et le
    chemin API historique reste strictement inchange.
    """
    resolved = settings or load_codex_subscription_settings()
    if not should_route_codeagent_to_codex_brain(settings=resolved):
        yield False, None
        return
    brain = CodexCodeAgentBrain(agent, resolved)
    token = _CODEX_CODEAGENT_BRAIN.set(brain)
    try:
        yield True, brain
    finally:
        _CODEX_CODEAGENT_BRAIN.reset(token)
        try:
            await brain.aclose()
        except Exception as exc:  # noqa: BLE001 — la fermeture ne doit jamais masquer l'erreur utile
            logger.debug("[Codex/CodeAgent brain] fermeture: {}", exc)


def should_route_codeagent_to_codex_brain(
    *,
    settings: CodexSubscriptionSettings | None = None,
) -> bool:
    """Vrai quand la surface CodeAgent est confiee a l'abonnement Codex.

    `enabled` porte le mode d'acces (abonnement contre API) et
    `surface_requested` la surface : les deux sont necessaires, comme dans
    `should_route_codeagent_to_codex`.
    """
    resolved = settings or load_codex_subscription_settings()
    return bool(resolved.enabled) and resolved.surface_requested(
        CodexSurface.CODEAGENT
    )


@asynccontextmanager
async def codex_react_brain_scope(
    react: Any,
    *,
    settings: CodexSubscriptionSettings | None = None,
):
    """Temporarily replace only ReAct's model callback on opted-in surfaces."""

    resolved = settings or load_codex_subscription_settings()
    if not should_route_react_to_codex(
        is_mission_run=bool(react._is_mission_run), settings=resolved
    ):
        yield False
        return
    brain = CodexReActBrain(react, resolved)
    previous_chat = react.llm_chat
    previous_meta_getter = react.llm_meta_getter
    previous_marker = bool(getattr(react, "_codex_react_brain_run", False))
    react.llm_chat = brain
    react.llm_meta_getter = brain.get_last_response_meta
    react._codex_react_brain_run = True
    try:
        yield True
    finally:
        react.llm_chat = previous_chat
        react.llm_meta_getter = previous_meta_getter
        react._codex_react_brain_run = previous_marker
        await brain.aclose()


@asynccontextmanager
async def _dedicated_codex_turn_scope():
    """Concurrency scope for Agent/Mission runs owning a private App Server.

    Chat keeps the shared notification lock.  Agent and Mission runs create a
    dedicated supervisor and queue below, so serializing them globally only
    destroys worker parallelism without protecting shared state.
    """

    yield


def reset_codex_response_meta() -> None:
    """Clear request-local Codex attribution before starting a Lumena call."""

    _CODEX_RESPONSE_META.set(None)


def consume_codex_response_meta() -> dict[str, Any]:
    """Return and clear request-local Codex attribution for the completed call."""

    meta = _CODEX_RESPONSE_META.get()
    _CODEX_RESPONSE_META.set(None)
    return dict(meta) if isinstance(meta, Mapping) else {}


def peek_codex_response_meta() -> dict[str, Any]:
    """Return request-local Codex attribution without consuming it.

    AgentService needs the authoritative provider before it persists memory and
    telemetry.  The web route remains the sole consumer so retry boundaries and
    response metadata keep their existing semantics.
    """

    meta = _CODEX_RESPONSE_META.get()
    return dict(meta) if isinstance(meta, Mapping) else {}


def _record_codex_response_meta(
    *, configured_model: str, selected_model: str
) -> None:
    requested = str(configured_model or selected_model or "auto")
    used = str(selected_model or "server-default")
    model_fallback = bool(configured_model and selected_model != configured_model)
    _CODEX_RESPONSE_META.set(
        {
            "provider_requested": "openai-codex",
            "provider_used": "openai-codex",
            "model_requested": requested,
            "model_used": used,
            "access_source_requested": "codex",
            "access_source_used": "codex",
            "billing_source": "chatgpt_subscription",
            "fallback_used": model_fallback,
            "fallback_reason": "codex_model_unavailable" if model_fallback else None,
            "fallback_attempts": [],
            "continuation_used": False,
            "continuation_steps": 0,
            "finish_reason": "stop",
            "prompt_tokens": None,
            "completion_tokens": None,
        }
    )


def should_route_react_to_codex(
    *, is_mission_run: bool, settings: CodexSubscriptionSettings
) -> bool:
    surface = CodexSurface.MISSIONS if is_mission_run else CodexSurface.AGENT
    return settings.surface_requested(surface)


def _toml_literal(value: Any) -> str:
    """JSON scalar/list syntax is a valid, safely escaped TOML subset here."""

    return json.dumps(value, ensure_ascii=False)


def build_codex_tool_app_server_command(
    executable: str,
    *,
    python_executable: str,
    project_root: str | Path,
    tool_timeout_s: float,
    config_overrides: Mapping[str, Any] | None = None,
) -> tuple[str, ...]:
    """Build an ephemeral MCP configuration without writing Codex config files."""

    root = str(Path(project_root).resolve())
    settings = tuple((config_overrides or {}).items()) + (
        ("mcp_servers.lumena.command", python_executable),
        ("mcp_servers.lumena.args", ["-m", "src.llm.codex_mcp_bridge"]),
        ("mcp_servers.lumena.cwd", root),
        (
            "mcp_servers.lumena.env_vars",
            [
                "LUMENA_CODEX_BRIDGE_HOST",
                "LUMENA_CODEX_BRIDGE_PORT",
                "LUMENA_CODEX_BRIDGE_TOKEN",
            ],
        ),
        ("mcp_servers.lumena.required", True),
        ("mcp_servers.lumena.enabled", True),
        ("mcp_servers.lumena.default_tools_approval_mode", "approve"),
        ("mcp_servers.lumena.startup_timeout_sec", 15),
        ("mcp_servers.lumena.tool_timeout_sec", max(30, int(tool_timeout_s))),
    )
    command: list[str] = [str(executable)]
    for key, value in settings:
        command.extend(("--config", f"{key}={_toml_literal(value)}"))
    command.append("app-server")
    return tuple(command)


def _id_from_result(result: Any, key: str) -> str:
    if not isinstance(result, Mapping):
        return ""
    nested = result.get(key)
    if isinstance(nested, Mapping):
        return str(nested.get("id", "") or "")
    return str(result.get(f"{key}Id", "") or "")


def _event_matches(params: Any, *, thread_id: str, turn_id: str) -> bool:
    if not isinstance(params, Mapping):
        return False
    event_thread = str(params.get("threadId", "") or "")
    event_turn = str(params.get("turnId", "") or "")
    turn = params.get("turn")
    if isinstance(turn, Mapping):
        event_turn = event_turn or str(turn.get("id", "") or "")
    return (not event_thread or event_thread == thread_id) and (
        not event_turn or event_turn == turn_id
    )


def _select_model(models: Sequence[Any], configured: str) -> str:
    available = {str(item.model_id): item for item in models if item.model_id}
    if configured and configured in available:
        return configured
    default = next((item.model_id for item in models if item.is_default), "")
    return default or (next(iter(available)) if available else "")


def _visible_tool_names(react: Any) -> frozenset[str]:
    """Mirror ReAct's contextual filter and safe category transitions."""

    registry = react.tools
    schemas = registry.get_tools_schema()
    all_names = {
        str((schema.get("function") or {}).get("name", "") or "")
        for schema in schemas
        if isinstance(schema, Mapping)
    }
    configured = getattr(registry, "_allowed_tools", None)
    if configured is None:
        return frozenset(name for name in all_names if name and name not in _CONTROL_TOOLS)
    allowed = {str(name) for name in configured if str(name) in all_names}
    if not getattr(registry, "_allowed_tools_hard", False):
        categories = {
            str(getattr(registry, "_tool_modules", {}).get(name, "") or "")
            for name in allowed
        }
        expanded_categories = set(categories)
        for category in tuple(categories):
            expanded_categories.update(_TOOL_TRANSITIONS.get(category, ()))
        for name, category in getattr(registry, "_tool_modules", {}).items():
            if category in expanded_categories and name in all_names:
                allowed.add(name)
    return frozenset(name for name in allowed if name and name not in _CONTROL_TOOLS)


def _resolve_execution_root(react: Any) -> Path:
    registry = react.tools
    mission_workspace = str(react._mission_workspace_meta() or "").strip()
    if mission_workspace:
        base = Path(getattr(registry, "default_workspace_root", ROOT_DIR / "workspace"))
        candidate = (base / mission_workspace).resolve()
        if candidate.is_dir():
            return candidate
    runtime = getattr(react, "runtime_ctx", None)
    for value in (
        getattr(runtime, "resolved_workspace", None),
        getattr(runtime, "workspace_path", None),
        getattr(getattr(registry, "_v2_context", None), "runtime_root", None),
    ):
        if value:
            candidate = Path(value).resolve()
            if candidate.is_dir():
                return candidate
    return ROOT_DIR.resolve()


def _prepare_handler_context(react: Any) -> None:
    context = getattr(react.tools, "_v2_context", None)
    if context is None:
        return
    elapsed = max(0.0, asyncio.get_running_loop().time() - react._loop_start_time)
    context.budget_seconds = max(0.0, float(react.timeout_seconds or 600) - elapsed)
    context.runtime_task_id = react.task_id or None
    context.is_mission_run = bool(react._is_mission_run)
    context.mission_workspace = react._mission_workspace_meta()
    context.mission_allowed_files = react._mission_allowed_files_meta()
    context.original_user_query = str(getattr(react, "_original_query", "") or "")


def _cancel_requested(react: Any) -> bool:
    # `getattr` et non un acces direct : le meme cerveau sert desormais au
    # CodeAgent, dont le SubAgent ne porte ni `task_id` ni orchestrateur.
    # Strictement plus permissif — aucun changement pour ReAct.
    task_id = getattr(react, "task_id", None)
    orchestrator = getattr(react, "task_orchestrator", None)
    if not task_id or not orchestrator:
        return False
    try:
        return bool(orchestrator.is_cancel_requested(task_id))
    except Exception:
        return False


def _record_tool_observation(
    react: Any,
    name: str,
    arguments: dict[str, Any],
    observation: Observation,
    duration_s: float,
) -> None:
    """Project MCP tool calls into the same history and ledger as native ReAct."""

    iteration = max(
        int(getattr(react, "_current_iteration", 0) or 0),
        len(getattr(react, "history", ()) or ()),
    )
    react._current_iteration = iteration
    action = Action(ActionType.TOOL_CALL, tool_name=name, tool_args=arguments)
    step_callback = getattr(react, "step_callback", None)
    if callable(step_callback):
        try:
            step_callback(name, dict(arguments))
        except Exception as exc:
            logger.debug("[Agent/Codex] step callback ignore: {}", exc)
    react.history.append(
        ReActStep(
            thought=Thought("Codex a selectionne un outil Lumena expose pour ce run."),
            action=action,
            observation=observation,
        )
    )
    try:
        react._record_document_catalog_evidence(action, observation)
        react._record_document_workflow_evidence(action, observation)
    except Exception:
        pass
    meta: dict[str, Any] = {"duration_ms": round(duration_s * 1000, 1), "via": "codex_mcp"}
    if name in {"run_command", "run_shell", "exec_command"}:
        command = str(arguments.get("command", "") or "")
        meta["command"] = command[:200]
        if is_test_command(command):
            meta["test_outcome"] = parse_test_outcome(
                command,
                str(getattr(observation, "content", "") or ""),
                getattr(observation, "exit_code", None),
            )
    target = _extract_target(name, arguments)
    proof = _extract_proof(
        name,
        str(getattr(observation, "content", "") or ""),
        bool(getattr(observation, "success", False)),
    )
    react.execution_ledger.append(
        iteration=iteration,
        action=name,
        target=target,
        success=bool(getattr(observation, "success", False)),
        proof=proof,
        meta=meta,
    )
    for sub in getattr(observation, "sub_results", ()) or ():
        react.execution_ledger.append(
            iteration=iteration,
            action=sub.tool_name,
            target=_extract_target(sub.tool_name, sub.args),
            success=bool(sub.success),
            proof=_extract_proof(sub.tool_name, sub.content, sub.success),
            meta={"duration_ms": 0.0, "via": "codex_mcp_parallel"},
        )
    test_outcome = meta.get("test_outcome")
    if isinstance(test_outcome, dict) and react.task_id and react.task_orchestrator:
        try:
            react.task_orchestrator.set_task_metadata(
                react.task_id,
                last_test_outcome=dict(test_outcome),
                tests_green=bool(test_outcome.get("green")),
            )
        except Exception:
            pass
    try:
        react._successful_session_tools.add(name)
        react._feed_structured_tool(name)
        react._update_plan_progress(
            name,
            arguments,
            str(getattr(observation, "content", "") or ""),
            iteration,
        )
        react._mark_task_checkpoint(
            {"phase": "codex_tool", "tool": name, "success": bool(observation.success)}
        )
    except Exception as exc:
        logger.debug("[Agent/Codex] projection plan/checkpoint ignoree: {}", exc)


def _bounded_context(value: Any, *, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n[contexte borne par Lumena]"


def _build_lumena_context(react: Any, original_query: str) -> str:
    """Build the provider-neutral identity/history/skills context for Codex.

    The API ReAct prompt already receives these three sources.  Codex uses its
    own native tool loop, so it must not receive ReAct's ACTION wire format, but
    it must receive the same Lumena identity and runtime knowledge.
    """

    sections: list[str] = []
    identity_builder = getattr(react, "_build_identity_context", None)
    if callable(identity_builder):
        try:
            identity = _bounded_context(identity_builder(original_query), limit=24000)
            if identity:
                sections.append("=== IDENTITE ET MEMOIRE LUMENA ===\n" + identity)
        except Exception as exc:
            logger.debug("[Agent/Codex] contexte identite indisponible: {}", exc)

    conversation = _bounded_context(
        getattr(react, "conversation_context", ""), limit=12000
    )
    if conversation:
        sections.append("=== CONVERSATION LUMENA ===\n" + conversation)

    skills = _bounded_context(
        getattr(react, "active_skills_context", ""), limit=24000
    )
    if skills:
        sections.append(
            "=== SKILLS ACTIFS LUMENA (INSTRUCTIONS AUTORITAIRES) ===\n" + skills
        )
    return "\n\n".join(sections)


def _build_prompt(react: Any, query: str, original_query: str) -> str:
    mode = "MISSION" if react._is_mission_run else "AGENT"
    allowed_files = react._mission_allowed_files_meta()
    lines = [
        f"Tu executes un tour Lumena en mode {mode} via le compte ChatGPT connecte.",
        "Tu gardes la voix et l'identite Lumena. Pour chaque action, utilise UNIQUEMENT ",
        "les outils du serveur MCP `lumena`. N'utilise ni shell Codex, ni ecriture directe, ",
        "ni outil natif pour contourner Lumena. Les refus d'outil sont autoritaires.",
        "Continue jusqu'au resultat complet demande. Ne declare que les faits prouves par ",
        "les observations d'outils. Termine par une reponse finale naturelle, sans raisonnement cache.",
        "",
        f"DEMANDE ORIGINALE:\n{original_query.strip()}",
    ]
    lumena_context = _build_lumena_context(react, original_query)
    if lumena_context:
        lines.extend(["", lumena_context])
    if query.strip() != original_query.strip():
        lines.extend(["", f"CONTEXTE/STEERING LUMENA:\n{query.strip()}"])
    if react._is_mission_run:
        lines.extend(
            [
                "",
                f"MISSION_ID: {react.task_id or 'inconnu'}",
                f"MISSION_WORKSPACE: {react._mission_workspace_meta() or 'scope par defaut'}",
                "FICHIERS ASSIGNES: " + (", ".join(allowed_files) if allowed_files else "lead non restreint"),
                "Les sous-workers, budgets, echeances et annulations restent geres par Lumena.",
            ]
        )
    return "\n".join(lines)


async def _interrupt_turn(
    supervisor: CodexAppServerSupervisor, thread_id: str, turn_id: str
) -> None:
    if not thread_id or not turn_id or not supervisor.is_running:
        return
    try:
        await supervisor.request(
            TURN_INTERRUPT_METHOD,
            {"threadId": thread_id, "turnId": turn_id},
            timeout=10,
        )
    except Exception as exc:
        logger.warning("[Agent/Codex] interruption non confirmee: {}", exc)


async def _mission_deadline_action(
    react: Any,
    supervisor: CodexAppServerSupervisor,
    thread_id: str,
    turn_id: str,
    *,
    steered: bool,
) -> str:
    if not react._is_mission_run or not react.task_id or not react.task_orchestrator:
        return "none"
    try:
        from src.subagents.mission_budget import (
            deadline_hard_net_fires,
            mission_budget,
            mission_budget_finalize,
        )

        record = react.task_orchestrator.get_task(react.task_id) or {}
        metadata = record.get("metadata") or {}
        budget = mission_budget(record)
        remaining = budget.get("remaining_s")
        if not budget.get("has_deadline") or not isinstance(remaining, (int, float)):
            return "none"
        grace = max(
            0.0,
            float(os.getenv("LUMENA_MISSION_DEADLINE_GRACE_S", "120") or 120),
        )
        if remaining <= 0 and not steered:
            decision = mission_budget_finalize(budget, grace_s=grace)
            instruction = decision[1] if decision and decision[0] == "finalize" else ""
            if instruction:
                await supervisor.request(
                    TURN_STEER_METHOD,
                    {
                        "threadId": thread_id,
                        "expectedTurnId": turn_id,
                        "input": [{"type": "text", "text": instruction}],
                    },
                    timeout=15,
                )
                react.task_orchestrator.set_task_metadata(
                    react.task_id, deadline_steered=True
                )
                return "steered"
        completion_evidence: dict[str, Any] = {}
        completion_probe = getattr(react, "_mission_completion_evidence", None)
        if callable(completion_probe):
            try:
                completion_evidence = dict(completion_probe() or {})
            except Exception as exc:
                logger.debug(
                    "[Agent/Codex] preuve de completion mission indisponible: {}", exc
                )
        completion_proven = bool(completion_evidence.get("complete"))
        artifact_written = bool(metadata.get("deadline_artifact_written"))
        if deadline_hard_net_fires(
            steered=bool(steered or metadata.get("deadline_steered")),
            remaining_s=remaining,
            grace_s=grace,
            artifact_written=artifact_written,
            completion_proven=completion_proven,
        ):
            react.task_orchestrator.set_task_metadata(
                react.task_id,
                deadline_expired=True,
                terminal_reason_code="deadline_expired",
                completion_proof=completion_evidence,
            )
            react.task_orchestrator.cancel_task(react.task_id, propagate=True)
            return "cancel"
        if (
            (artifact_written or completion_proven)
            and remaining <= -grace
            and not metadata.get("deadline_net_disarmed")
        ):
            react.task_orchestrator.set_task_metadata(
                react.task_id,
                deadline_net_disarmed=True,
                completion_proof=completion_evidence,
            )
    except Exception as exc:
        logger.debug("[Agent/Codex] budget mission non evalue: {}", exc)
    return "none"


async def run_react_with_codex_subscription(
    react: Any,
    query: str,
    original_query: str,
    *,
    settings: CodexSubscriptionSettings,
    timeout_s: float | None = None,
) -> str:
    shared = get_shared_codex_app_server()
    if shared is None or not shared.is_running:
        # LOT Z33 phases 1 & 2 — avant d'abandonner, on tente de ROUVRIR. La
        # session est un processus local : elle meurt au redemarrage de Lumena
        # (21/08 02:33:57) ou si le processus tombe en cours de run. L'auth,
        # elle, survit sur disque — il n'y a donc qu'un processus a relancer.
        try:
            from src.llm.codex_app_server import ensure_shared_codex_app_server
            shared = await ensure_shared_codex_app_server()
        except Exception as _cx_exc:
            logger.debug("[Z33] reouverture session Codex impossible: {}", _cx_exc)
            shared = None
        if shared is None or not shared.is_running:
            raise CodexReActUnavailable(
                "Aucune session Codex connectee (reouverture automatique tentee, "
                "sans succes). Ouvre Configuration > Acces OpenAI."
            )
        logger.info("[Z33] session Codex rouverte a chaud — le run continue")
    executable = str(shared.config.command[0]) if shared.config.command else ""
    if not executable:
        raise CodexReActUnavailable("Executable Codex introuvable dans la session connectee")
    allowed_tools = _visible_tool_names(react)
    if not allowed_tools:
        raise CodexReActUnavailable("Aucun outil Lumena autorise pour ce run")
    _prepare_handler_context(react)
    workspace = _resolve_execution_root(react)
    bounded_timeout = max(30.0, float(timeout_s or react.timeout_seconds or 600))
    agent_id = "codex-mission" if react._is_mission_run else "codex-agent"
    bridge = LumenaCodexToolBridge(
        react.tools,
        allowed_tools=allowed_tools,
        agent_id=agent_id,
        before_call=lambda: _prepare_handler_context(react),
        after_call=lambda name, args, obs, duration: _record_tool_observation(
            react, name, args, obs, duration
        ),
        cancel_requested=lambda: _cancel_requested(react),
    )
    thread_id = ""
    turn_id = ""
    final_text = ""
    supervisor: CodexAppServerSupervisor | None = None
    deadline_steered = False
    async with _dedicated_codex_turn_scope():
        async with bridge:
            endpoint = bridge.endpoint
            environment = dict(shared.config.environ or os.environ)
            environment.update(
                {
                    "LUMENA_CODEX_BRIDGE_HOST": endpoint.host,
                    "LUMENA_CODEX_BRIDGE_PORT": str(endpoint.port),
                    "LUMENA_CODEX_BRIDGE_TOKEN": endpoint.token,
                }
            )
            command = build_codex_tool_app_server_command(
                executable,
                python_executable=sys.executable,
                project_root=ROOT_DIR,
                tool_timeout_s=bounded_timeout,
                config_overrides=codex_compatibility_config_overrides(environment),
            )
            supervisor = CodexAppServerSupervisor(
                CodexAppServerConfig(
                    command=command,
                    cwd=str(ROOT_DIR),
                    environ=environment,
                    request_timeout_s=30,
                    handshake_timeout_s=20,
                    max_auto_restarts=1,
                )
            )
            try:
                await supervisor.start()
                gateway = CodexSubscriptionGateway(supervisor)
                await gateway.require_chatgpt_account()
                models = await gateway.list_models()
                if not models:
                    raise CodexReActUnavailable("Le compte Codex ne retourne aucun modele")
                model = _select_model(models, settings.default_model)
                service_name = "lumena-mission" if react._is_mission_run else "lumena-agent"
                thread_params: dict[str, Any] = {
                    "cwd": str(workspace),
                    "approvalPolicy": "never",
                    "sandbox": "read-only",
                    "serviceName": service_name,
                }
                if model:
                    thread_params["model"] = model
                started = await supervisor.request(
                    THREAD_START_METHOD, thread_params, timeout=30
                )
                thread_id = _id_from_result(started, "thread")
                if not thread_id:
                    raise CodexReActUnavailable("Codex n'a retourne aucun thread Agent")
                prompt = _build_prompt(react, query, original_query)
                turn_params: dict[str, Any] = {
                    "threadId": thread_id,
                    "input": [{"type": "text", "text": prompt}],
                    "cwd": str(workspace),
                    "approvalPolicy": "never",
                    "sandboxPolicy": {
                        "type": "readOnly",
                        "networkAccess": False,
                    },
                }
                if model:
                    turn_params["model"] = model
                result = await supervisor.request(TURN_START_METHOD, turn_params, timeout=30)
                turn_id = _id_from_result(result, "turn")
                if not turn_id:
                    raise CodexReActUnavailable("Codex n'a retourne aucun tour Agent")
                async with asyncio.timeout(bounded_timeout):
                    while True:
                        if _cancel_requested(react):
                            raise asyncio.CancelledError
                        deadline_action = await _mission_deadline_action(
                            react,
                            supervisor,
                            thread_id,
                            turn_id,
                            steered=deadline_steered,
                        )
                        deadline_steered = deadline_steered or deadline_action == "steered"
                        if deadline_action == "cancel":
                            raise asyncio.CancelledError
                        try:
                            notification = await supervisor.next_notification(timeout=1.0)
                        except CodexAppServerTimeout:
                            continue
                        if not _event_matches(
                            notification.params, thread_id=thread_id, turn_id=turn_id
                        ):
                            continue
                        params = notification.params if isinstance(notification.params, Mapping) else {}
                        if notification.method == "item/completed":
                            item = params.get("item")
                            if isinstance(item, Mapping) and item.get("type") == "agentMessage":
                                final_text = str(item.get("text", "") or final_text)
                        elif notification.method == "turn/completed":
                            turn = params.get("turn")
                            turn = turn if isinstance(turn, Mapping) else {}
                            status = str(turn.get("status", "") or "")
                            if status != "completed":
                                raise CodexReActUnavailable(
                                    f"Tour Codex termine avec status={status}: {turn.get('error')}"
                                )
                            if not final_text.strip():
                                raise CodexReActUnavailable("Codex a termine sans reponse finale")
                            logger.info(
                                "[Agent/Codex] tour termine surface={} model={} tools={} task={}",
                                service_name,
                                model or "server-default",
                                react.execution_ledger.size,
                                react.task_id,
                            )
                            _record_codex_response_meta(
                                configured_model=settings.default_model,
                                selected_model=model,
                            )
                            return final_text.strip()
            except (asyncio.CancelledError, TimeoutError, CodexAppServerTimeout):
                if supervisor is not None:
                    await _interrupt_turn(supervisor, thread_id, turn_id)
                raise
            except CodexAppServerError as exc:
                raise CodexReActUnavailable(f"Codex App Server indisponible: {exc}") from exc
            finally:
                if supervisor is not None:
                    await supervisor.stop()


async def maybe_run_codex_surface(
    react: Any,
    query: str,
    original_query: str,
    *,
    settings: CodexSubscriptionSettings | None = None,
) -> str | None:
    """Return None for the historical ReAct path, or a truth-locked Codex final."""

    resolved = settings or load_codex_subscription_settings()
    if not should_route_react_to_codex(
        is_mission_run=bool(react._is_mission_run), settings=resolved
    ):
        return None
    result = await run_react_with_codex_subscription(
        react,
        query,
        original_query,
        settings=resolved,
    )
    previous = bool(getattr(react, "_codex_tool_bridge_run", False))
    react._codex_tool_bridge_run = True
    try:
        return react._stream_and_return_final(result)
    finally:
        react._codex_tool_bridge_run = previous
