"""L'abonnement Codex comme CERVEAU de la boucle CodeAgent historique.

Le defaut ferme ici
-------------------
Le rail `run_codeagent_with_codex_subscription` remplacait TOUTE la boucle
CodeAgent par un tour Codex autonome. Consequences mesurees sur des runs reels :

  * prompts, outils, tests, reprises et garde-fous de Lumena contournes ;
  * une session rouverte a CHAQUE delegation —
        account/read timed out after 30.0s
        model/list  timed out after 30.0s
    soit 60 s perdues avant la premiere ligne de code ;
  * et le run « SaaS complet » abandonne apres ~30 s.

Le contrat qui remplace : un seul moteur — la boucle historique — et
l'abonnement ne fournit que le TEXTE de chaque decision, exactement comme
`MultiProviderLLM.chat()`.
"""

import asyncio
import json
from types import SimpleNamespace

import pytest

from src.llm.codex_subscription import (
    CodexSubscriptionSettings,
    CodexSurface,
    OpenAIAccessMode,
)
from src.llm.execution_router import (
    _REACT_DECISION_SCHEMA,
    CodexCodeAgentBrain,
    CodexReActBrain,
    get_active_codex_codeagent_brain,
    codex_codeagent_brain_scope,
    should_route_codeagent_to_codex_brain,
)


def _settings(surfaces=(CodexSurface.CODEAGENT,)):
    return CodexSubscriptionSettings(
        access_mode=OpenAIAccessMode.CHATGPT_CODEX,
        default_model="gpt-test",
        surfaces=frozenset(surfaces),
    )


# ══════════════════════════════════════════════════════════════════════════
#  Le contrat d'appel : la boucle historique ne connait que `llm.chat(...)`
# ══════════════════════════════════════════════════════════════════════════


def test_le_contrat_chat_est_respecte():
    """`sub_agent.py` appelle `llm.chat(messages=, temperature=, max_tokens=)`
    sur ses 7 points d'appel. Rien d'autre ne doit etre requis."""
    brain = CodexCodeAgentBrain(SimpleNamespace(), _settings())
    assert callable(brain.chat)
    assert brain.max_output_tokens == 65536
    assert brain.model_name  # lu par les logs de la boucle


@pytest.mark.asyncio
async def test_chat_rend_le_texte_brut_de_la_decision(monkeypatch):
    """Le CodeAgent possede son PROPRE parseur d'action.

    Le transport schema-contraint est retire avant ce parseur : il recoit donc
    toujours le texte historique, jamais l'enveloppe technique.
    """
    brain = CodexCodeAgentBrain(SimpleNamespace(), _settings())
    attendu = 'THOUGHT: j ecris le fichier\n{"action": "write_file", "path": "app.py"}'

    async def _faux_tour(self, messages):
        return self._parse_final(json.dumps({"response": attendu}))

    monkeypatch.setattr(CodexCodeAgentBrain, "__call__", _faux_tour, raising=False)
    rendu = await brain.chat(messages=[{"role": "user", "content": "code"}])
    assert rendu == attendu


@pytest.mark.asyncio
async def test_temperature_et_max_tokens_sont_acceptes_sans_casser(monkeypatch):
    """L'App Server ne les expose pas. Les refuser casserait les 7 appels ;
    les ignorer en silence serait le defaut que ce lot corrige — d'ou la
    docstring explicite de `chat`."""
    brain = CodexCodeAgentBrain(SimpleNamespace(), _settings())
    async def _faux_tour(self, messages):
        return "ok"

    monkeypatch.setattr(CodexCodeAgentBrain, "__call__", _faux_tour, raising=False)
    rendu = await brain.chat(
        messages=[{"role": "user", "content": "x"}],
        temperature=0.1,
        max_tokens=2000,
    )
    assert rendu == "ok"


# ══════════════════════════════════════════════════════════════════════════
#  Les deux SEULES differences avec le cerveau ReAct
# ══════════════════════════════════════════════════════════════════════════


def test_le_transport_codeagent_est_schema_contraint_sans_changer_son_format():
    """Le wrapper empêche Codex d'agir; `response` garde le format historique."""
    assert CodexReActBrain._output_schema is _REACT_DECISION_SCHEMA
    assert CodexCodeAgentBrain._output_schema["required"] == ["response"]


def test_l_enveloppe_est_retiree_avant_le_parseur_historique():
    brain = CodexCodeAgentBrain(SimpleNamespace(), _settings())
    action = '{"action":"write_file","path":"app.py","content":"ok"}'
    wire = json.dumps({"response": action})
    assert brain._parse_final(wire) == action


@pytest.mark.parametrize("wire", ["", "{}", '{"response":""}', "[]"])
def test_une_enveloppe_invalide_echoue_proprement(wire):
    brain = CodexCodeAgentBrain(SimpleNamespace(), _settings())
    with pytest.raises(RuntimeError):
        brain._parse_final(wire)


def test_le_tour_codeagent_est_plus_long():
    """Une iteration CodeAgent reflechit plus qu'une decision ReAct :
    3 min 39 mesurees sur le run du SaaS."""
    assert CodexCodeAgentBrain._turn_timeout > CodexReActBrain._turn_timeout


# ══════════════════════════════════════════════════════════════════════════
#  L'isolation, elle, est IDENTIQUE et non negociable
# ══════════════════════════════════════════════════════════════════════════


def test_le_service_est_distinct_mais_la_machinerie_partagee():
    """Deux noms de service pour distinguer les traces, une seule machinerie
    d'isolation — donc aucune divergence possible entre les deux cerveaux."""
    assert CodexCodeAgentBrain._service_name != CodexReActBrain._service_name
    assert issubclass(CodexCodeAgentBrain, CodexReActBrain)
    # L'anti-contournement n'est PAS redefini : il est herite tel quel.
    assert "__call__" not in CodexCodeAgentBrain.__dict__


def test_le_prompt_interdit_d_agir_hors_de_lumena():
    brain = CodexCodeAgentBrain(SimpleNamespace(), _settings())
    prompt = brain._build_prompt([{"role": "user", "content": "PROMPT HISTORIQUE"}])
    assert "N'execute AUCUN outil" in prompt
    # Le prompt historique du CodeAgent est transmis TEL QUEL, jamais reecrit.
    assert "PROMPT HISTORIQUE" in prompt


# ══════════════════════════════════════════════════════════════════════════
#  Le routage
# ══════════════════════════════════════════════════════════════════════════


def test_routage_actif_uniquement_sur_la_surface_codeagent():
    assert should_route_codeagent_to_codex_brain(settings=_settings()) is True
    assert (
        should_route_codeagent_to_codex_brain(settings=_settings((CodexSurface.CHAT,)))
        is False
    )


def test_le_mode_api_reste_un_no_op():
    """Hors abonnement, le chemin historique doit etre STRICTEMENT inchange."""
    api = CodexSubscriptionSettings(
        access_mode=OpenAIAccessMode.API,
        default_model="",
        surfaces=frozenset(),
    )
    assert should_route_codeagent_to_codex_brain(settings=api) is False


# ══════════════════════════════════════════════════════════════════════════
#  Le marqueur, et pourquoi il est ce qu'il est
# ══════════════════════════════════════════════════════════════════════════


def test_le_marqueur_de_tache_est_serialisable():
    """C'est la contrainte qui a dicte la conception : un cerveau passe comme
    OBJET ne franchit pas la frontiere du lancement en arriere-plan."""
    contexte = {"_codex_brain": True, "autre": "valeur"}
    json.dumps(contexte)  # leve si un objet s'y glisse


def test_get_llm_rend_le_cerveau_codex_quand_la_tache_le_demande(monkeypatch):
    """Point d'injection unique : `_get_llm(task)`, utilise par les 7 appels
    de la boucle historique."""
    from src.agents.sub_agent import SubAgent, AgentType

    monkeypatch.setenv("LUMENA_OPENAI_ACCESS_MODE", "chatgpt_codex")
    monkeypatch.setenv("LUMENA_CODEX_SURFACES", "codeagent")
    agent = SubAgent.__new__(SubAgent)
    agent.name = "CodeAgent"
    agent.agent_type = AgentType.CODE
    tache = SimpleNamespace(context={"_codex_brain": True})

    with pytest.raises(RuntimeError, match="sans scope"):
        agent._get_llm(tache)


@pytest.mark.asyncio
async def test_un_seul_cerveau_est_partage_et_ferme_par_tache(monkeypatch):
    from src.agents.sub_agent import SubAgent, AgentType

    agent = SubAgent.__new__(SubAgent)
    agent.name = "CodeAgent"
    agent.agent_type = AgentType.CODE
    tache = SimpleNamespace(context={"_codex_brain": True})
    fermes = []

    async def fake_close(self):
        fermes.append(self)

    monkeypatch.setattr(CodexCodeAgentBrain, "aclose", fake_close)
    async with codex_codeagent_brain_scope(agent, settings=_settings()) as (active, brain):
        assert active is True
        assert brain is not None
        assert get_active_codex_codeagent_brain() is brain
        assert agent._get_llm(tache) is brain
        assert agent._get_llm(tache) is brain

    assert get_active_codex_codeagent_brain() is None
    assert fermes == [brain]


@pytest.mark.asyncio
async def test_le_codeagent_possede_le_scope_et_publie_le_moteur(monkeypatch):
    """La boucle complete, pas le handler, possede la duree de vie du cerveau."""
    from src.agents.sub_agent import AgentResult, AgentTask, AgentType, CodeAgent

    monkeypatch.setenv("LUMENA_OPENAI_ACCESS_MODE", "chatgpt_codex")
    monkeypatch.setenv("LUMENA_CODEX_SURFACES", "codeagent")
    agent = CodeAgent()
    vus = []
    fermes = []

    async def fake_loop(self, task):
        vus.extend([self._get_llm(task), self._get_llm(task)])
        return AgentResult(task_id=task.task_id, success=True, output="ok")

    async def fake_close(self):
        fermes.append(self)

    monkeypatch.setattr(CodeAgent, "_iterative_code_loop", fake_loop)
    monkeypatch.setattr(CodexCodeAgentBrain, "aclose", fake_close)
    task = AgentTask(
        task_id="codex-scope",
        description="Construis une application web complete",
        agent_type=AgentType.CODE,
        context={"_codex_brain": True},
    )

    result = await agent._execute_task(task)

    assert len(vus) == 2 and vus[0] is vus[1]
    assert fermes == [vus[0]]
    assert result.meta["engine"] == "codex_subscription"
    assert result.meta["fallback_used"] is False


@pytest.mark.asyncio
async def test_l_apprentissage_post_succes_finit_avant_la_fermeture(monkeypatch):
    from src.agents.sub_agent import AgentResult, AgentTask, AgentType, CodeAgent

    monkeypatch.setenv("LUMENA_OPENAI_ACCESS_MODE", "chatgpt_codex")
    monkeypatch.setenv("LUMENA_CODEX_SURFACES", "codeagent")
    agent = CodeAgent()
    ordre = []

    async def fake_loop(self, task):
        async def post_success():
            await asyncio.sleep(0)
            assert self._get_llm(task) is get_active_codex_codeagent_brain()
            ordre.append("post-success")

        pending = asyncio.create_task(post_success())
        self._codex_post_success_tasks.append(pending)
        return AgentResult(task_id=task.task_id, success=True, output="ok")

    async def fake_close(self):
        ordre.append("close")

    monkeypatch.setattr(CodeAgent, "_iterative_code_loop", fake_loop)
    monkeypatch.setattr(CodexCodeAgentBrain, "aclose", fake_close)
    task = AgentTask(
        task_id="codex-post-success",
        description="Construis app.py",
        agent_type=AgentType.CODE,
        context={"_codex_brain": True},
    )

    result = await agent._execute_task(task)

    assert result.success is True
    assert ordre == ["post-success", "close"]


@pytest.mark.asyncio
@pytest.mark.parametrize("agent_class_name", ["DebugAgent", "RefactorAgent"])
async def test_debug_et_refactor_partagent_le_meme_scope_codex(
    monkeypatch, agent_class_name
):
    """Les variantes du CodeAgent ne doivent pas contourner le cerveau Codex."""
    from src.agents import sub_agent as module
    from src.agents.sub_agent import AgentResult, AgentTask, AgentType

    monkeypatch.setenv("LUMENA_OPENAI_ACCESS_MODE", "chatgpt_codex")
    monkeypatch.setenv("LUMENA_CODEX_SURFACES", "codeagent")
    agent = getattr(module, agent_class_name)()
    vus = []

    async def fake_explicit(_task):
        return None

    async def fake_loop(task):
        vus.append(agent._get_llm(task))
        return AgentResult(task_id=task.task_id, success=True, output="ok")

    monkeypatch.setattr(agent, "_execute_explicit_tool", fake_explicit)
    monkeypatch.setattr(agent, "_iterative_code_loop", fake_loop)
    task = AgentTask(
        task_id=f"codex-{agent_class_name.lower()}",
        description="Corrige puis verifie app.py",
        agent_type=AgentType.DEBUG,
        context={"_codex_brain": True},
    )

    result = await agent._execute_task(task)

    assert len(vus) == 1
    assert result.meta["engine"] == "codex_subscription"
    assert result.meta["fallback_used"] is False


@pytest.mark.asyncio
async def test_le_scope_ferme_aussi_sur_exception(monkeypatch):
    agent = SimpleNamespace()
    fermes = []

    async def fake_close(self):
        fermes.append(self)

    monkeypatch.setattr(CodexCodeAgentBrain, "aclose", fake_close)
    with pytest.raises(ValueError, match="boom"):
        async with codex_codeagent_brain_scope(agent, settings=_settings()) as (_, brain):
            raise ValueError("boom")
    assert fermes == [brain]


@pytest.mark.asyncio
async def test_les_retries_codex_n_escaladent_jamais_vers_une_api(monkeypatch):
    from src.agents.sub_agent import (
        AgentResult,
        AgentTask,
        AgentType,
        StatusCode,
        SubAgentOrchestrator,
    )

    class AlwaysFails:
        name = "CodeAgent"

        async def execute(self, task):
            return AgentResult(
                task_id=task.task_id,
                success=False,
                output="codex indisponible",
                status_code=StatusCode.ERROR,
            )

    orchestrator = SubAgentOrchestrator.__new__(SubAgentOrchestrator)
    orchestrator.results = {}
    orchestrator.pending_tasks = {}
    orchestrator.get_agent = lambda _kind: AlwaysFails()
    orchestrator._save_to_disk = lambda: None
    monkeypatch.setattr(
        "src.llm.providers.check_api_key",
        lambda _provider: (_ for _ in ()).throw(AssertionError("fallback API appele")),
    )
    task = AgentTask(
        task_id="no-api-fallback",
        description="Construis app.py",
        agent_type=AgentType.CODE,
        context={"_codex_brain": True},
    )

    result = await orchestrator.execute_task(task, max_retries=2)

    assert result.success is False
    assert "_best_model" not in task.context


def test_sans_marqueur_rien_ne_change(monkeypatch):
    """Le chemin API historique ne doit voir AUCUNE difference."""
    from src.agents.sub_agent import SubAgent, AgentType

    agent = SubAgent.__new__(SubAgent)
    agent.name = "CodeAgent"
    agent.agent_type = AgentType.CODE
    coeur = SimpleNamespace(llm=SimpleNamespace(model_name="deepseek-chat"))
    monkeypatch.setattr("src.agents.sub_agent.get_lumena", lambda: coeur)

    llm = agent._get_llm(SimpleNamespace(context={}))
    assert llm is coeur.llm
