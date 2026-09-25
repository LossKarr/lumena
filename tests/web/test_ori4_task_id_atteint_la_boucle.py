"""LOT ORI-4 — le `task_id` du stream doit atteindre la boucle ReAct.

--- Pourquoi tout le chantier d'orientation etait inoperant ---

`react.py` garde le checkpoint de steering derriere une condition :

    def _orchestrator_enabled(self) -> bool:
        return bool(self.task_orchestrator and self.task_id)

Run reel du 21 septembre, tour de 122 s avec QUATRE iterations et une
orientation deposee apres la deuxieme : elle est restee `pending`, `delivery`
vide. Le log donne la raison, trois fois :

    [REACT LATENCY] ... previous_tool=create_project task=None

`self.task_id` valait None, donc `_orchestrator_enabled()` etait faux, donc le
bloc entier — verification d'annulation ET remise des orientations — etait
saute. Aucune orientation ne pouvait etre remise, quel que soit le nombre
d'iterations.

--- La fuite ---

Le stream passe `task_id` a `_call_lumena_with_auto_resume_on_timeout`, qui ne
s'en sert que pour la reprise apres timeout. Les deux couches suivantes,
`_call_lumena_with_step_timeout_and_retry` et `_call_lumena_with_context`, ne
l'ont pas dans leur signature : il est jete. Or `think_and_act` l'accepte
(`core.py:803`), tout comme `task_orchestrator` (`core.py:802`).

Le fait existait des deux cotes ; personne ne faisait le lien.
"""
from __future__ import annotations

import pytest

from web.routes import deps


class _LumenaFactice:
    """Enregistre ce que la couche web transmet reellement."""

    def __init__(self) -> None:
        self.recu: dict = {}

    async def think_and_act(
        self, query, source_channel="web", ide_context=None,
        task_orchestrator=None, task_id=None, **_ignores,
    ):
        self.recu = {
            "query": query,
            "source_channel": source_channel,
            "task_orchestrator": task_orchestrator,
            "task_id": task_id,
        }
        return "reponse"


@pytest.mark.asyncio
async def test_le_task_id_atteint_la_boucle_react(monkeypatch):
    from web.routes.chat import _call_lumena_with_context

    faux = _LumenaFactice()
    monkeypatch.setattr(deps, "lumena", faux)

    await _call_lumena_with_context(
        "think_and_act", "fais une page", "web", {}, task_id="task_abc123",
    )

    assert faux.recu.get("task_id") == "task_abc123", (
        "le task_id est perdu avant la boucle : `_orchestrator_enabled()` sera "
        "faux et AUCUNE orientation ne sera jamais remise"
    )


@pytest.mark.asyncio
async def test_un_appel_sans_task_id_reste_valide(monkeypatch):
    """Retrocompatibilite : les doublures anciennes ne recevaient qu'un message."""
    from web.routes.chat import _call_lumena_with_context

    faux = _LumenaFactice()
    monkeypatch.setattr(deps, "lumena", faux)

    await _call_lumena_with_context("think_and_act", "bonjour", "web", {})

    assert faux.recu.get("task_id") is None
    assert faux.recu.get("query") == "bonjour"


def test_la_chaine_d_appel_declare_le_task_id_a_chaque_etage():
    """Un seul etage qui l'oublie suffit a rendre l'orientation inoperante."""
    import inspect

    from web.routes import chat

    for nom in (
        "_call_lumena_with_context",
        "_call_lumena_with_step_timeout_and_retry",
        "_call_lumena_with_auto_resume_on_timeout",
    ):
        fonction = getattr(chat, nom)
        assert "task_id" in inspect.signature(fonction).parameters, (
            f"{nom} ne transporte pas le task_id : la chaine est rompue a cet etage"
        )
