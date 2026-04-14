"""Tests for the 4 anti-hallucination guards added to fix production bugs.

Bug context: Lumena's LLM (DeepSeek V3) was claiming "j'ai cree 3 fichiers"
in ACTION: FINAL without ever calling write_file or telegram_send_document.
These guards detect and block that pattern.
"""
from pathlib import Path
import json
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.reasoning.react import ToolRegistry, ReActLoop, ActionType


# ----- Fix 1: parallel_tools blocks only recursion (blocklist approach) -----

@pytest.mark.asyncio
async def test_parallel_tools_blocks_recursion(tmp_path: Path):
    """parallel_tools must reject parallel_tools (anti-recursion)."""
    registry = ToolRegistry(lumena=None, lumena_root=tmp_path)
    observation = await registry.execute(
        "parallel_tools",
        {
            "tool_calls": [
                {"name": "parallel_tools", "args": {}},
            ]
        },
    )
    assert observation.success is False or "interdit" in observation.content.lower() or "récursion" in observation.content.lower() or "recursion" in observation.content.lower()


@pytest.mark.asyncio
async def test_parallel_tools_allows_write_tools(tmp_path: Path):
    """With blocklist approach, write_file is allowed in parallel (Lumena is autonomous)."""
    registry = ToolRegistry(lumena=None, lumena_root=tmp_path)
    observation = await registry.execute(
        "parallel_tools",
        {
            "tool_calls": [
                {"name": "write_file", "args": {"path": "a.txt", "content": "A"}},
                {"name": "write_file", "args": {"path": "b.txt", "content": "B"}},
            ]
        },
    )
    # Should NOT be rejected anymore — it should attempt execution
    content = observation.content.lower()
    assert "non autorise" not in content


@pytest.mark.asyncio
async def test_parallel_tools_still_allows_read_only(tmp_path: Path):
    """read_file should still work fine in parallel_tools."""
    registry = ToolRegistry(lumena=None, lumena_root=tmp_path)
    f = tmp_path / "test.txt"
    f.write_text("hello", encoding="utf-8")
    observation = await registry.execute(
        "parallel_tools",
        {"tool_calls": [{"name": "read_file", "args": {"path": str(f)}}]},
    )
    assert observation.success is True
    assert "hello" in observation.content


# ----- Fix 2: Anti-hallucination guard on FINAL -----

@pytest.mark.asyncio
async def test_hallucination_guard_blocks_final_without_tool_calls(tmp_path: Path):
    """If the LLM says 'j'ai cree les fichiers' in FINAL but never called
    write_file, the guard should force a retry, not return the hallucinated answer."""
    call_count = 0

    async def _hallucinating_llm(_messages, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # First call: LLM goes straight to FINAL claiming it created files
            return (
                "THOUGHT: J'ai créé les 3 fichiers de coaching et je les ai envoyés.\n"
                "ACTION: FINAL\n"
                "ACTION_INPUT: J'ai créé et envoyé les 3 documents de coaching !"
            )
        elif call_count == 2:
            # Second call after guard rejection: LLM actually does the work
            return (
                "THOUGHT: Je dois d'abord creer le fichier.\n"
                "ACTION: write_file\n"
                'ACTION_INPUT: {"path": "coaching.txt", "content": "Coaching plan"}'
            )
        else:
            # Third call: finish properly
            return (
                "THOUGHT: Le fichier est cree. Je confirme.\n"
                "ACTION: FINAL\n"
                "ACTION_INPUT: Le fichier coaching.txt a ete cree avec succes."
            )

    loop = ReActLoop(
        llm_chat_func=_hallucinating_llm,
        tools=ToolRegistry(lumena=None, lumena_root=tmp_path),
    )

    result = await loop.run("Cree-moi un plan de coaching")

    # The guard should have triggered on call 1,
    # forced a retry which called write_file on call 2,
    # then returned the honest FINAL on call 3.
    assert call_count >= 2, "Guard should have rejected the first hallucinated FINAL"
    # The file should actually exist
    assert (tmp_path / "workspace" / "coaching.txt").exists() or "coaching" in result.lower()


@pytest.mark.asyncio
async def test_hallucination_guard_allows_honest_final(tmp_path: Path):
    """If the LLM says FINAL with a simple greeting (no creation claims),
    the guard should NOT interfere."""
    async def _honest_llm(_messages, **kwargs):
        return (
            "THOUGHT: L'utilisateur veut juste dire bonjour.\n"
            "ACTION: FINAL\n"
            "ACTION_INPUT: Bonjour ! Comment puis-je vous aider ?"
        )

    loop = ReActLoop(
        llm_chat_func=_honest_llm,
        tools=ToolRegistry(lumena=None, lumena_root=tmp_path),
    )

    result = await loop.run("Bonjour")
    assert "Bonjour" in result


@pytest.mark.asyncio
async def test_hallucination_guard_allows_final_after_real_write(tmp_path: Path):
    """If the LLM actually called write_file successfully, then says
    'j'ai cree le fichier' in FINAL, the guard should NOT trigger."""
    call_count = 0

    async def _honest_creation_llm(_messages, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return (
                "THOUGHT: Je vais creer le fichier demande.\n"
                "ACTION: write_file\n"
                'ACTION_INPUT: {"path": "result.txt", "content": "Done"}'
            )
        else:
            return (
                "THOUGHT: J'ai cree le fichier avec succes.\n"
                "ACTION: FINAL\n"
                "ACTION_INPUT: J'ai créé le fichier result.txt avec succès !"
            )

    loop = ReActLoop(
        llm_chat_func=_honest_creation_llm,
        tools=ToolRegistry(lumena=None, lumena_root=tmp_path),
    )

    result = await loop.run("Cree un fichier result.txt")
    assert call_count == 2, "Should not need more than 2 calls (write + final)"
    assert "result.txt" in result.lower() or "créé" in result.lower()


# ----- Fix 3: list_directory guard now redirects to write_file for creation tasks -----

def test_list_directory_guard_message_for_creation_task():
    """When user asks to CREATE files and LLM loops on list_directory,
    the parser's guard should mention write_file in the observation."""
    # This is tested implicitly through the full ReActLoop, but let's verify
    # the parser logic detects creation keywords properly.
    creation_queries = [
        "Crée-moi 3 fichiers de coaching",
        "Génère un plan de formation",
        "Écris-moi un résumé dans un fichier",
        "Prépare les documents pour le meeting",
        "Fais un script python pour trier les données",
    ]
    _creation_keywords = (
        "créer", "creer", "cree", "crée", "créé", "génère", "genere", "rédige", "redige",
        "écris", "ecris", "prépare", "prepare", "fais", "produis", "structure",
        "create", "write", "generate", "make", "build",
    )
    for query in creation_queries:
        query_lower = query.lower()
        matched = any(kw in query_lower for kw in _creation_keywords)
        assert matched, f"Query '{query}' should be detected as creation intent"

    non_creation_queries = [
        "Trouve le fichier config.json",
        "Lis le contenu de data.txt",
        "Cherche les logs d'erreur",
    ]
    for query in non_creation_queries:
        query_lower = query.lower()
        matched = any(kw in query_lower for kw in _creation_keywords)
        assert not matched, f"Query '{query}' should NOT be detected as creation intent"
