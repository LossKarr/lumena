from pathlib import Path
import json
import sys

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.reasoning.react import (
    ReActLoop,
    _phase27_mcp_observation_guidance,
)


class _PromptTools:
    def __init__(self, desc: str):
        self._desc = desc
        self.tools = {}
        self._allowed_tools = None
        self.ide_context = {}

    def get_tools_description(self) -> str:
        return self._desc


async def _dummy_llm(_messages, **_kwargs):
    return "ACTION: FINAL\nACTION_INPUT: done"


def _prompt_for_tools(desc: str, query: str = "installe un MCP pour github") -> str:
    loop = ReActLoop(llm_chat_func=_dummy_llm, tools=_PromptTools(desc))
    return loop._build_react_prompt(query)


def test_prompt_has_no_mcp_section_when_phase26_tools_hidden():
    prompt = _prompt_for_tools("- read_file: lire un fichier")
    assert "## AUTONOMIE MCP" not in prompt
    assert "request_mcp_capability" not in prompt
    assert "request_mcp_ticket" not in prompt
    assert "run_mcp_autonomy" not in prompt


def test_prompt_injects_mcp_policy_when_capability_tool_visible():
    prompt = _prompt_for_tools(
        "- request_mcp_capability: verifier les capacites MCP\n"
        "- final_answer: reponse finale"
    )
    assert "## AUTONOMIE MCP" in prompt
    assert "appelle d'abord\n  `request_mcp_capability`" in prompt
    assert "je ne peux pas" in prompt
    assert "Ne dis jamais qu'un MCP est installe" in prompt
    assert "CodeAgent" in prompt


def test_prompt_injects_ticket_policy_only_when_ticket_tool_visible():
    prompt_without_ticket = _prompt_for_tools(
        "- request_mcp_capability: verifier les capacites MCP"
    )
    assert "I-CONFIRM-MCP-TICKET" not in prompt_without_ticket

    prompt_with_ticket = _prompt_for_tools(
        "- request_mcp_capability: verifier les capacites MCP\n"
        "- request_mcp_ticket: creer un ticket MCP pending"
    )
    assert "I-CONFIRM-MCP-TICKET" in prompt_with_ticket
    assert "ticket pending" in prompt_with_ticket
    assert "panel MCP" in prompt_with_ticket
    assert "live=true" in prompt_with_ticket
    assert "live=false" in prompt_with_ticket
    assert "dry-run" in prompt_with_ticket
    assert "ticket_proposed" in prompt_with_ticket
    assert "plan_create" in prompt_with_ticket
    assert "remplacer un ticket MCP" in prompt_with_ticket


def test_prompt_injects_run_and_resume_policy_when_visible():
    prompt = _prompt_for_tools(
        "- request_mcp_capability: verifier les capacites MCP\n"
        "- request_mcp_ticket: creer un ticket MCP pending\n"
        "- run_mcp_autonomy: piloter la boucle MCP\n"
        "- resume_mcp_task: reprendre apres approval"
    )
    assert "run_mcp_autonomy" in prompt
    assert "I-CONFIRM-MCP-AUTONOMY" in prompt
    assert "resume_mcp_task" in prompt
    assert "appelle l'outil cible" in prompt
    assert "c'est bon, reprends" in prompt
    assert "delegate_task" in prompt
    assert "CodeAgent" in prompt
    assert "creer le MCP local" in prompt


def test_prompt_never_instructs_react_to_execute_approved_action():
    prompt = _prompt_for_tools(
        "- request_mcp_capability: verifier les capacites MCP\n"
        "- request_mcp_ticket: creer un ticket MCP pending"
    )
    forbidden = [
        "execute_after_approval",
        "ApprovalQueue.approve",
        "ApprovalQueue.reject",
        "auto-install",
        "auto install",
    ]
    for token in forbidden:
        assert token not in prompt


def test_mcp_observation_guidance_for_missing_capability_requests_ticket():
    """Phase I-8 (Fix AH) : la guidance pointe run_mcp_autonomy (l'outil
    présent dans la liste du LLM) avec SA phrase. L'ancienne version
    pointait request_mcp_ticket (hors liste) avec I-CONFIRM-MCP-TICKET →
    le LLM transposait la mauvaise phrase sur run_mcp_autonomy (boucle
    confirmation_phrase_invalid observée runtime 2026-06-11 17:41)."""
    obs = json.dumps({
        "decision": "ok",
        "payload": {
            "recommendation_code": "needs_install_approval",
            "target_server_id": "github_srv",
        },
    })
    guidance = _phase27_mcp_observation_guidance("request_mcp_capability", obs)
    assert guidance is not None
    assert "run_mcp_autonomy" in guidance
    assert "I-CONFIRM-MCP-AUTONOMY" in guidance
    assert "request_mcp_ticket" not in guidance
    assert "Ne dis jamais que le MCP est installe" in guidance
    assert "live=true" in guidance
    assert "plan_create" in guidance
    assert "CodeAgent" in guidance


def test_mcp_observation_guidance_for_local_creation_prefers_materialization():
    obs = json.dumps({
        "decision": "ok",
        "payload": {
            "recommendation_code": "needs_local_creation",
            "target_server_id": "analyse_5116fb39",
        },
    })
    guidance = _phase27_mcp_observation_guidance("resume_mcp_task", obs)
    assert guidance is not None
    assert "Materialiser local MCP" in guidance
    assert "ne cree pas un nouveau ticket" in guidance
    assert "I-CONFIRM-MCP-TICKET" in guidance


def test_mcp_observation_guidance_for_ticket_pending_mentions_panel_and_ids():
    obs = json.dumps({
        "decision": "ok",
        "payload": {
            "recommendation_code": "ticket_proposed",
            "proposed_ticket_action_id": "0123456789abcdef0123456789abcdef",
            "target_server_id": "github_srv",
        },
    })
    guidance = _phase27_mcp_observation_guidance("request_mcp_ticket", obs)
    assert guidance is not None
    assert "Ticket MCP pending" in guidance
    assert "panel MCP" in guidance
    assert "ticket_id=0123456789abcdef0123456789abcdef" in guidance
    assert "server_id=github_srv" in guidance


def test_mcp_observation_guidance_ignores_non_mcp_or_malformed_payload():
    assert _phase27_mcp_observation_guidance("read_file", "{}") is None
    assert _phase27_mcp_observation_guidance("request_mcp_ticket", "not-json") is None
    assert _phase27_mcp_observation_guidance("request_mcp_ticket", "[]") is None
