"""
CallerContext — identité de l'agent appelant une ressource partagée.

Propagé à travers `ToolRegistry.execute()` pour permettre aux policy checks
de différencier ReAct, CodeAgent, Scheduler, etc.

Utilisation :
    from src.reasoning.caller_context import CallerContext, REACT, CODEAGENT

    await registry.execute("write_file", args, caller=REACT)
    await registry.execute("write_file", args, caller=CODEAGENT)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

CallerKind = Literal["react", "codeagent", "scheduler", "autonomy", "silent", "unknown"]


@dataclass(frozen=True)
class CallerContext:
    """Identité immuable de l'agent appelant un outil.

    Attributes:
        kind: Catégorie de l'agent.
        agent_id: Identifiant unique (optionnel, utile pour le tracing).
        trace_id: ID de trace distribuée (optionnel).
    """
    kind: CallerKind = "unknown"
    agent_id: Optional[str] = None
    trace_id: Optional[str] = None

    def __str__(self) -> str:
        return f"{self.kind}({self.agent_id or '-'})"


# ─── Instances pré-définies pour les appels courants ─────────────────
REACT = CallerContext(kind="react", agent_id="react-main")
CODEAGENT = CallerContext(kind="codeagent", agent_id="codeagent-main")
SCHEDULER = CallerContext(kind="scheduler", agent_id="scheduler")
AUTONOMY = CallerContext(kind="autonomy", agent_id="autonomy-loop")
SILENT = CallerContext(kind="silent", agent_id="silent-worker")
UNKNOWN = CallerContext(kind="unknown")
