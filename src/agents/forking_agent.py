"""
🧠 LUMENA - Consciousness Forking

4 perspectives concurrentes débattent avant de répondre.
Réduit les angles morts du raisonnement single-shot.

Architecture :
  Demande → 4 forks parallèles (optimiste, paranoïaque, créatif, conservateur)
         → synthèse Socratique → consensus + dissensions
"""

from typing import Dict, Any, List, Optional
from dataclasses import dataclass
import asyncio
from loguru import logger

from .sub_agent import (
    SubAgent, AgentType, AgentTask, AgentResult, StatusCode,
    get_lumena,
)
from src.prompts.agents.forking_prompts import (
    SYNTHESIS_PROMPT,
)


@dataclass
class Fork:
    """Une perspective de raisonnement."""
    name: str
    emoji: str
    system_prompt: str


# ── Les 4 perspectives ──────────────────────────────────────

FORKS = [
    Fork(
        name="optimiste",
        emoji="🟢",
        system_prompt=(
            "Tu es la perspective OPTIMISTE de Lumena. "
            "Analyse la demande en supposant que la solution est simple et directe. "
            "Propose l'approche la plus rapide et pragmatique. "
            "Identifie les raccourcis possibles. "
            "Sois concis : 3-8 lignes max."
        ),
    ),
    Fork(
        name="paranoïaque",
        emoji="🔴",
        system_prompt=(
            "Tu es la perspective PARANOÏAQUE de Lumena. "
            "Analyse la demande en cherchant TOUS les risques, edge cases, et pièges. "
            "Suppose que tout peut échouer. Liste les dangers concrets. "
            "Sois concis : 3-8 lignes max."
        ),
    ),
    Fork(
        name="créatif",
        emoji="🟣",
        system_prompt=(
            "Tu es la perspective CRÉATIVE de Lumena. "
            "Analyse la demande en cherchant des approches non-conventionnelles. "
            "Propose au moins une alternative que personne n'aurait envisagée. "
            "Sois concis : 3-8 lignes max."
        ),
    ),
    Fork(
        name="conservateur",
        emoji="🔵",
        system_prompt=(
            "Tu es la perspective CONSERVATRICE de Lumena. "
            "Analyse la demande en minimisant les changements. "
            "Défends le statu quo si c'est raisonnable. Explique les coûts du changement. "
            "Sois concis : 3-8 lignes max."
        ),
    ),
]


class ForkingAgent(SubAgent):
    """
    Agent à conscience bifurquée.

    Exécute 4 perspectives en parallèle via LLM,
    puis synthétise un consensus enrichi.
    """

    def __init__(self, forks: Optional[List[Fork]] = None):
        super().__init__(
            agent_type=AgentType.GENERAL,
            name="ForkingAgent",
            tools=[],
        )
        self.forks = forks or FORKS

    async def _execute_task(self, task: AgentTask) -> AgentResult:
        """Lance les 4 forks en parallèle puis synthétise."""
        objective = task.description
        extra_context = task.context.get("forking_context", "")

        user_prompt = objective
        if extra_context:
            user_prompt += f"\n\nContexte : {extra_context}"

        try:
            core = get_lumena()
            llm = core.llm
        except Exception as e:
            return self._result_error(task, f"ForkingAgent: LLM inaccessible — {e}")

        # ── Phase 1 : 4 forks en parallèle ──────────────
        fork_coros = [
            self._run_fork(llm, fork, user_prompt)
            for fork in self.forks
        ]
        fork_raw = await asyncio.gather(*fork_coros, return_exceptions=True)

        fork_outputs: List[Dict[str, Any]] = []
        for fork, result in zip(self.forks, fork_raw):
            if isinstance(result, BaseException):
                logger.warning(f"🧠 Fork {fork.name} échoué: {result}")
                fork_outputs.append({
                    "name": fork.name,
                    "emoji": fork.emoji,
                    "output": f"[Fork échoué: {result}]",
                    "success": False,
                })
            else:
                fork_outputs.append({
                    "name": fork.name,
                    "emoji": fork.emoji,
                    "output": str(result),
                    "success": True,
                })

        valid_count = sum(1 for f in fork_outputs if f["success"])
        if valid_count < 2:
            return self._result_error(
                task,
                "ForkingAgent: moins de 2 forks ont réussi, synthèse impossible.",
                error_type="insufficient_forks",
            )

        # ── Phase 2 : Synthèse ──────────────────────────
        synthesis_input = self._build_synthesis_input(user_prompt, fork_outputs)

        try:
            synthesis = str(await llm.chat(
                messages=[
                    {"role": "system", "content": SYNTHESIS_PROMPT},
                    {"role": "user", "content": synthesis_input},
                ],
                temperature=0.4,
                max_tokens=getattr(llm, "max_output_tokens", 65536),
            ))
        except Exception as e:
            # Fallback : retourner les forks bruts sans synthèse
            raw = "\n\n".join(
                f"{f['emoji']} **{f['name']}**\n{f['output']}"
                for f in fork_outputs
            )
            return AgentResult(
                task_id=task.task_id,
                success=True,
                output=f"[Synthèse échouée — voici les 4 perspectives brutes]\n\n{raw}",
                status_code=StatusCode.PARTIAL,
                meta={"forks": fork_outputs, "synthesis_error": str(e)},
            )

        logger.info(
            f"🧠 ForkingAgent: {valid_count}/{len(self.forks)} forks → synthèse OK"
        )

        return AgentResult(
            task_id=task.task_id,
            success=True,
            output=synthesis,
            status_code=StatusCode.SUCCESS,
            meta={
                "forks": fork_outputs,
                "forks_succeeded": valid_count,
                "forks_total": len(self.forks),
            },
        )

    async def _run_fork(self, llm: Any, fork: Fork, user_prompt: str) -> str:
        """Exécute un fork individuel."""
        response = await llm.chat(
            messages=[
                {"role": "system", "content": fork.system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.7,
            max_tokens=getattr(llm, "max_output_tokens", 65536),
        )
        return str(response)

    @staticmethod
    def _build_synthesis_input(
        original_prompt: str,
        fork_outputs: List[Dict[str, Any]],
    ) -> str:
        """Construit le prompt de synthèse avec les 4 perspectives."""
        parts = [f"**Demande originale :**\n{original_prompt}\n"]
        for f in fork_outputs:
            status = "✅" if f["success"] else "❌"
            parts.append(
                f"**{f['emoji']} {f['name'].upper()}** {status}\n{f['output']}\n"
            )
        return "\n---\n".join(parts)
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the Apache License, Version 2.0
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
