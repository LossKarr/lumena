"""F1.a — `think_and_act_silent(proof_out=…)` : les preuves du run remontent.

Cause (AUD-012 / AUD-014) : le chat conserve `react.get_run_meta()` dans
`_last_agent_meta` (agent_service.py:2279) tandis que la mission le JETAIT
(`return result or ""`). Le runner ne recevait qu'une chaîne et ne pouvait donc
juger que sa FORME (non vide), jamais les EFFETS — d'où une mission `done` avec
« Je n'ai pas trouvé de réponse pertinente. » alors que le ledger prouvait trois
artefacts, et un `overclaim` du truth-lock sans conséquence sur l'état terminal.

Contrat de ce lot :
- `proof_out=None` → comportement INCHANGÉ au caractère près (zéro régression) ;
- `proof_out={}` → preuves du ledger + drapeaux du truth-lock ;
- collecte FAIL-OPEN : une erreur de preuve ne fait jamais échouer une mission
  qui a réussi (la preuve est un bonus, pas une dépendance) ;
- sorties BORNÉES : pas de dump du ledger complet dans l'état de tâche.
"""
from __future__ import annotations

import types

import pytest

from src.core_services import agent_service as svc_mod
from src.runtime.execution_ledger import ExecutionLedger


class _FakeLLM:
    def __init__(self):
        self.chat_calls = 0

    async def chat(self, messages, stop=None):
        self.chat_calls += 1
        return "ok"

    def get_last_response_meta(self):
        return {}


def _fake_registry(tag="core"):
    return types.SimpleNamespace(
        tag=tag,
        _allowed_tools=None,
        _tools_desc_cache=None,
        _observation_cache={},
        _caller_set_allowed=False,
        _allowed_tools_hard=False,
        _outside_access_grant=None,
        _v2_context=None,
    )


def _make_service():
    core_reg = _fake_registry()
    core = types.SimpleNamespace(
        llm=_FakeLLM(),
        _tool_registry=core_reg,
        mcp_react_integration=None,
    )
    return svc_mod.AgentService(core), core


def _ledger_with_work():
    """Ledger RÉEL (classe pure) portant une mutation réussie + une publication."""
    led = ExecutionLedger()
    led.append(
        iteration=1,
        action="write_file",
        target="workspace/demo/rapport.md",
        success=True,
        proof="✅ Fichier écrit: rapport.md (120 c)",
    )
    led.append(
        iteration=2,
        action="publish_mission_workspace",
        target="workspace/demo",
        success=True,
        proof="✅ Publié: workspace/demo",
    )
    return led


def _install_react(monkeypatch, *, ledger=None, run_meta=None):
    """Installe un ReActLoop factice porteur d'un ledger et d'un run_meta."""

    class _FakeReact:
        def __init__(self, llm_chat, tools, **kwargs):
            self.history = []
            if ledger is not None:
                self.execution_ledger = ledger
            if run_meta is not None:
                self._run_meta = run_meta

        async def run(self, task):
            return "livrable produit"

    monkeypatch.setattr(svc_mod, "ReActLoop", _FakeReact)
    monkeypatch.setattr(svc_mod, "REASONING_AVAILABLE", True, raising=False)


# ── Zéro régression : sans le param, rien ne change ──────────────────────────

@pytest.mark.asyncio
async def test_without_proof_out_behaviour_is_unchanged(monkeypatch):
    _install_react(monkeypatch, ledger=_ledger_with_work())
    svc, _ = _make_service()
    assert await svc.think_and_act_silent("tâche", allow_when_busy=True) == "livrable produit"


@pytest.mark.asyncio
async def test_react_without_ledger_still_works(monkeypatch):
    """Les appelants historiques passent des boucles sans `execution_ledger`."""
    _install_react(monkeypatch, ledger=None)
    svc, _ = _make_service()
    proof: dict = {}
    assert await svc.think_and_act_silent(
        "tâche", allow_when_busy=True, proof_out=proof,
    ) == "livrable produit"
    # Pas de ledger → pas de preuve, mais aucune exception et le run aboutit.
    assert "ledger" not in proof


# ── Le canal de preuve porte bien les effets ─────────────────────────────────

@pytest.mark.asyncio
async def test_proof_out_carries_ledger_evidence(monkeypatch):
    _install_react(monkeypatch, ledger=_ledger_with_work())
    svc, _ = _make_service()
    proof: dict = {}
    await svc.think_and_act_silent("tâche", allow_when_busy=True, proof_out=proof)

    assert proof["has_any_mutation"] is True
    assert proof["has_published"] is True
    assert "write_file" in proof["successful_actions"]
    assert "publish_mission_workspace" in proof["successful_actions"]
    assert "rapport.md" in proof["written_basenames"]


@pytest.mark.asyncio
async def test_proof_out_reports_absence_of_effects(monkeypatch):
    """Un run sans mutation doit le DIRE — c'est ce qui distingue un vrai
    livrable d'une réponse de politesse."""
    _install_react(monkeypatch, ledger=ExecutionLedger())
    svc, _ = _make_service()
    proof: dict = {}
    await svc.think_and_act_silent("tâche", allow_when_busy=True, proof_out=proof)

    assert proof["has_any_mutation"] is False
    assert proof["has_published"] is False
    assert proof["successful_actions"] == []


@pytest.mark.asyncio
async def test_proof_out_uses_compact_projection_not_full_dump(monkeypatch):
    """La projection doit rester compacte : elle finira dans l'état de tâche."""
    led = ExecutionLedger()
    for i in range(60):
        led.append(
            iteration=i,
            action="write_file",
            target=f"workspace/demo/f{i}.md",
            success=True,
            proof="✅ ok",
        )
    _install_react(monkeypatch, ledger=led)
    svc, _ = _make_service()
    proof: dict = {}
    await svc.think_and_act_silent("tâche", allow_when_busy=True, proof_out=proof)

    assert proof["ledger"]["total_actions"] == 60
    # `recent` est une fenêtre, pas le ledger entier.
    assert len(proof["ledger"]["recent"]) <= 10
    assert "entries" not in proof["ledger"]
    # Les listes sont bornées.
    assert len(proof["written_basenames"]) <= 40
    assert len(proof["successful_actions"]) <= 40


# ── Drapeaux du truth-lock ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_proof_out_forwards_truth_lock_flags(monkeypatch):
    _install_react(
        monkeypatch,
        ledger=_ledger_with_work(),
        run_meta={
            "mission_truth_lock_applied": True,
            "mission_truth_lock_overclaim": True,
            "agent_output_incomplete": False,
            "un_champ_non_whitelisté": "ignoré",
        },
    )
    svc, _ = _make_service()
    proof: dict = {}
    await svc.think_and_act_silent("tâche", allow_when_busy=True, proof_out=proof)

    assert proof["mission_truth_lock_applied"] is True
    assert proof["mission_truth_lock_overclaim"] is True
    assert proof["agent_output_incomplete"] is False
    # Whitelist stricte : aucun champ non prévu ne fuit dans l'état de tâche.
    assert "un_champ_non_whitelisté" not in proof


@pytest.mark.asyncio
async def test_absent_truth_lock_flags_are_not_invented(monkeypatch):
    """Sans drapeau posé, on n'invente pas de valeur par défaut : l'absence de
    preuve n'est pas une preuve d'absence, c'est au lecteur de décider."""
    _install_react(monkeypatch, ledger=_ledger_with_work(), run_meta={})
    svc, _ = _make_service()
    proof: dict = {}
    await svc.think_and_act_silent("tâche", allow_when_busy=True, proof_out=proof)

    assert "mission_truth_lock_overclaim" not in proof


# ── Fail-open : la preuve ne peut jamais casser la mission ───────────────────

# ── Le wrapper LumenaCore doit relayer, sinon la mission casse en vrai ───────

def test_core_wrapper_relays_every_service_parameter():
    """Trouvé par la campagne, pas par les tests : le runner appelle
    `core.think_and_act_silent` (wrapper `LumenaCore`), JAMAIS le service
    directement. Un paramètre ajouté au seul service fait échouer la mission
    entière en `TypeError` — invisible aux tests, qui utilisent un core factice.

    Cet invariant est générique : il protège tout ajout futur, pas seulement
    `proof_out`.
    """
    import inspect
    from src.core import LumenaCore
    from src.core_services.agent_service import AgentService

    service_params = set(inspect.signature(AgentService.think_and_act_silent).parameters)
    wrapper_params = set(inspect.signature(LumenaCore.think_and_act_silent).parameters)
    missing = service_params - wrapper_params
    assert not missing, (
        f"LumenaCore.think_and_act_silent ne relaie pas : {sorted(missing)}. "
        "Toute mission échouerait en TypeError."
    )


@pytest.mark.asyncio
async def test_core_wrapper_actually_forwards_proof_out(monkeypatch):
    """Accepter le paramètre ne suffit pas : il doit ARRIVER au service."""
    from src.core import LumenaCore

    seen = {}

    async def _fake_service_call(task, timeout=120.0, allowed_tools=None, **kw):
        seen.update(kw)
        return "ok"

    core = LumenaCore.__new__(LumenaCore)
    core._agent_svc = types.SimpleNamespace(think_and_act_silent=_fake_service_call)

    sentinel: dict = {}
    await LumenaCore.think_and_act_silent(core, "tâche", proof_out=sentinel)
    assert seen.get("proof_out") is sentinel


@pytest.mark.asyncio
async def test_proof_collection_failure_never_breaks_the_run(monkeypatch):
    class _ExplodingLedger:
        def checkpoint_projection(self):
            raise RuntimeError("ledger corrompu")

        def __getattr__(self, name):
            raise RuntimeError("ledger corrompu")

    _install_react(monkeypatch, ledger=_ExplodingLedger())
    svc, _ = _make_service()
    proof: dict = {}

    # La mission a réussi : elle DOIT rendre son livrable malgré la panne de preuve.
    assert await svc.think_and_act_silent(
        "tâche", allow_when_busy=True, proof_out=proof,
    ) == "livrable produit"
