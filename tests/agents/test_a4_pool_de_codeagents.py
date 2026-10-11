r"""LOT A4 — N CodeAgent travaillent en parallèle, chacun restant séquentiel.

--- Le défaut, mesuré ---

`get_agent()` rend le PREMIER agent du type : une instance unique partagée par tous les
workers de mission ET par le chat. `execute()` est sérialisé par `self._exec_lock`
(LOT 2.12.A). Les workers réfléchissent en parallèle, mais **ils codent chacun leur
tour** — mesuré en run réel le 02/09 : **23 minutes de file pour 4 fichiers**.

--- La doctrine de ce lot : on ne retire PAS le verrou ---

```
AVANT :  1 instance × 1 verrou          =  1 tâche à la fois
APRÈS :  N instances × 1 verrou chacune =  N tâches à la fois
```

Chaque instance reste **strictement séquentielle en interne**. Aucune des 38 fuites
d'état inventoriées en A1 ne peut donc se produire *entre workers simultanés* : une
instance ne traite jamais deux tâches à la fois, et `_reset_task_scoped_state` continue
de jouer son rôle entre deux tâches de la même instance.

**On ne corrige pas l'isolation : on l'évite.**

--- Pourquoi c'est sûr (vérifié au code le 07/10) ---

- `CodeAgent()` / `SubAgent.__init__` sont **légers** : aucun LLM créé, aucun fichier
  chargé (le LLM vient de `core.llm`, partagé) ;
- l'état global de module se limite à `_active_delegates` et `_bg_agent_tasks`, **indexés
  par `task_id`** → pas de collision ;
- `reflexion_store`, `success_store` et `WorldModel` sont **déjà sous `RLock`** ;
- **zéro ligne de la logique de codage n'est touchée** ;
- `LUMENA_CODEAGENT_POOL=1` rend le comportement **identique à avant**, instance
  comprise → retour arrière immédiat.

--- Règle de dimensionnement (décision du 07/10) ---

Le pool est **global** (chat + leads + workers + sous-workers y puisent) et ne doit
jamais dépasser `LUMENA_PROVIDER_CONCURRENCY` : avec un seul modèle pour toute la
mission, une instance sans créneau LLM attendrait de toute façon.
"""
from __future__ import annotations

import asyncio

import pytest

from src.agents.sub_agent import AgentType, ResearchAgent, SubAgentOrchestrator, _taille_pool_agents


def _orch() -> SubAgentOrchestrator:
    return SubAgentOrchestrator()


def _canonique(o: SubAgentOrchestrator):
    return o.get_agent(AgentType.CODE)


# ── 1. Le réglage ────────────────────────────────────────────────────────────


class TestTaillePool:
    def test_defaut_dix(self, monkeypatch):
        """Décision de Charles du 07/10 : 10 CodeAgent possibles d'origine."""
        monkeypatch.delenv("LUMENA_CODEAGENT_POOL", raising=False)
        assert _taille_pool_agents() == 10

    @pytest.mark.parametrize("valeur,attendu", [
        ("1", 1), ("4", 4), ("64", 64),
        ("0", 1), ("-5", 1),          # plancher : jamais moins d'une instance
        ("999", 64),                  # plafond de sécurité
        ("", 10), ("abc", 10),        # valeur illisible → défaut
    ])
    def test_bornes_et_valeurs_illisibles(self, monkeypatch, valeur, attendu):
        monkeypatch.setenv("LUMENA_CODEAGENT_POOL", valeur)
        assert _taille_pool_agents() == attendu


# ── 2. POOL=1 : comportement IDENTIQUE à avant le lot ────────────────────────


class TestRetourArriere:
    @pytest.mark.asyncio
    async def test_pool_a_un_rend_l_instance_canonique_elle_meme(self, monkeypatch):
        """Le test qui garantit le retour arrière : à 1, c'est le MÊME objet qu'avant."""
        monkeypatch.setenv("LUMENA_CODEAGENT_POOL", "1")
        o = _orch()
        agent = _canonique(o)
        if agent is None:
            pytest.skip("pas de CodeAgent enregistré")
        async with o.instance_pour(agent) as inst:
            assert inst is agent

    @pytest.mark.asyncio
    async def test_pool_a_un_ne_cree_aucune_instance(self, monkeypatch):
        monkeypatch.setenv("LUMENA_CODEAGENT_POOL", "1")
        o = _orch()
        agent = _canonique(o)
        if agent is None:
            pytest.skip("pas de CodeAgent enregistré")
        async with o.instance_pour(agent):
            pass
        assert not o._pools_agents


# ── 3. Le cas qui fonde le lot : N en parallèle ──────────────────────────────


class TestParallelismeReel:
    @pytest.mark.asyncio
    async def test_quatre_emprunts_simultanes_donnent_quatre_instances(self, monkeypatch):
        """4 workers qui codent EN MÊME TEMPS — c'est la demande de Charles."""
        monkeypatch.setenv("LUMENA_CODEAGENT_POOL", "4")
        o = _orch()
        agent = _canonique(o)
        if agent is None:
            pytest.skip("pas de CodeAgent enregistré")

        vus = []

        async def _emprunte():
            async with o.instance_pour(agent) as inst:
                vus.append(id(inst))
                await asyncio.sleep(0.02)   # tient l'instance pendant que les autres empruntent

        await asyncio.gather(*[_emprunte() for _ in range(4)])
        assert len(set(vus)) == 4, "les 4 emprunts simultanés doivent donner 4 instances"

    @pytest.mark.asyncio
    async def test_l_agent_canonique_fait_partie_du_pool(self, monkeypatch):
        """Il n'est pas mis au rebut : il est la première instance disponible."""
        monkeypatch.setenv("LUMENA_CODEAGENT_POOL", "3")
        o = _orch()
        agent = _canonique(o)
        if agent is None:
            pytest.skip("pas de CodeAgent enregistré")
        async with o.instance_pour(agent) as inst:
            assert inst is agent

    @pytest.mark.asyncio
    async def test_toutes_les_instances_portent_le_meme_nom(self, monkeypatch):
        """Indispensable au lot A3 : l'historique est agrégé PAR NOM."""
        monkeypatch.setenv("LUMENA_CODEAGENT_POOL", "3")
        o = _orch()
        agent = _canonique(o)
        if agent is None:
            pytest.skip("pas de CodeAgent enregistré")

        noms = set()

        async def _emprunte():
            async with o.instance_pour(agent) as inst:
                noms.add(inst.name)
                assert inst.agent_type == agent.agent_type
                await asyncio.sleep(0.01)

        await asyncio.gather(*[_emprunte() for _ in range(3)])
        assert noms == {agent.name}

    @pytest.mark.asyncio
    async def test_le_pool_est_borne(self, monkeypatch):
        """Au-delà de N, on ATTEND — le pool borne la charge, il ne la multiplie pas."""
        monkeypatch.setenv("LUMENA_CODEAGENT_POOL", "2")
        o = _orch()
        agent = _canonique(o)
        if agent is None:
            pytest.skip("pas de CodeAgent enregistré")

        simultanes = 0
        maximum = 0

        async def _emprunte():
            nonlocal simultanes, maximum
            async with o.instance_pour(agent):
                simultanes += 1
                maximum = max(maximum, simultanes)
                await asyncio.sleep(0.02)
                simultanes -= 1

        await asyncio.gather(*[_emprunte() for _ in range(6)])
        assert maximum == 2, f"{maximum} emprunts simultanés pour un pool de 2"

    @pytest.mark.asyncio
    async def test_un_clone_actif_est_visible_dans_le_statut(self, monkeypatch):
        monkeypatch.setenv("LUMENA_CODEAGENT_POOL", "3")
        o = _orch()
        agent = _canonique(o)
        if agent is None:
            pytest.skip("pas de CodeAgent enregistré")

        async with o.instance_pour(agent):
            ligne = next(a for a in o.get_status()["agents"] if a["name"] == agent.name)
            assert ligne["status"] == "running"
            assert ligne["active_instances"] == 1
            assert ligne["pool_capacity"] == 3

        ligne = next(a for a in o.get_status()["agents"] if a["name"] == agent.name)
        assert ligne["active_instances"] == 0

    @pytest.mark.asyncio
    async def test_le_reglage_codeagent_ne_clone_pas_les_autres_agents(self, monkeypatch):
        monkeypatch.setenv("LUMENA_CODEAGENT_POOL", "4")
        o = _orch()
        agent = ResearchAgent()
        async with o.instance_pour(agent) as instance:
            assert instance is agent
        assert agent.name not in o._pools_agents


# ── 4. L'instance revient TOUJOURS au pool ───────────────────────────────────


class TestRestitution:
    @pytest.mark.asyncio
    async def test_l_instance_revient_apres_usage_normal(self, monkeypatch):
        monkeypatch.setenv("LUMENA_CODEAGENT_POOL", "2")
        o = _orch()
        agent = _canonique(o)
        if agent is None:
            pytest.skip("pas de CodeAgent enregistré")
        async with o.instance_pour(agent):
            pass
        assert o._pools_agents[agent.name].qsize() == 2

    @pytest.mark.asyncio
    async def test_l_instance_revient_MEME_sur_exception(self, monkeypatch):
        """Sans ce `finally`, une tâche qui plante épuiserait le pool silencieusement."""
        monkeypatch.setenv("LUMENA_CODEAGENT_POOL", "2")
        o = _orch()
        agent = _canonique(o)
        if agent is None:
            pytest.skip("pas de CodeAgent enregistré")
        with pytest.raises(RuntimeError):
            async with o.instance_pour(agent):
                raise RuntimeError("boum")
        assert o._pools_agents[agent.name].qsize() == 2

    @pytest.mark.asyncio
    async def test_l_instance_revient_sur_annulation(self, monkeypatch):
        """Une mission annulée ne doit pas fuir une instance."""
        monkeypatch.setenv("LUMENA_CODEAGENT_POOL", "2")
        o = _orch()
        agent = _canonique(o)
        if agent is None:
            pytest.skip("pas de CodeAgent enregistré")

        async def _annulee():
            async with o.instance_pour(agent):
                await asyncio.sleep(10)

        t = asyncio.create_task(_annulee())
        await asyncio.sleep(0.02)
        t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t
        assert o._pools_agents[agent.name].qsize() == 2


# ── 5. Ce que le lot ne doit PAS changer ─────────────────────────────────────


class TestInvariantsPreserves:
    @pytest.mark.asyncio
    async def test_chaque_instance_garde_son_verrou(self, monkeypatch):
        """La doctrine du lot : on ne retire pas `_exec_lock`."""
        monkeypatch.setenv("LUMENA_CODEAGENT_POOL", "3")
        o = _orch()
        agent = _canonique(o)
        if agent is None:
            pytest.skip("pas de CodeAgent enregistré")

        async def _emprunte():
            async with o.instance_pour(agent) as inst:
                assert isinstance(inst._exec_lock, asyncio.Lock)
                await asyncio.sleep(0.01)

        await asyncio.gather(*[_emprunte() for _ in range(3)])

    @pytest.mark.asyncio
    async def test_les_clones_ne_polluent_pas_le_registre_des_agents(self, monkeypatch):
        """`self.agents` reste la liste des agents NOMMÉS, pas celle des instances."""
        monkeypatch.setenv("LUMENA_CODEAGENT_POOL", "5")
        o = _orch()
        agent = _canonique(o)
        if agent is None:
            pytest.skip("pas de CodeAgent enregistré")
        avant = len(o.agents)
        async with o.instance_pour(agent):
            pass
        assert len(o.agents) == avant

    @pytest.mark.asyncio
    async def test_chaque_instance_a_son_etat_task_scoped(self, monkeypatch):
        """Le gain du pool : deux tâches concurrentes ne se clobbent plus."""
        monkeypatch.setenv("LUMENA_CODEAGENT_POOL", "2")
        o = _orch()
        agent = _canonique(o)
        if agent is None:
            pytest.skip("pas de CodeAgent enregistré")

        pret = asyncio.Event()

        async def _un():
            async with o.instance_pour(agent) as inst:
                inst._allowed_files = frozenset({"a.py"})
                pret.set()
                await asyncio.sleep(0.05)
                assert inst._allowed_files == frozenset({"a.py"}), "périmètre écrasé !"

        async def _deux():
            await pret.wait()
            async with o.instance_pour(agent) as inst:
                inst._allowed_files = frozenset({"b.py"})
                await asyncio.sleep(0.01)

        await asyncio.gather(_un(), _deux())


# ── 6. Le cas trouvé par le run de signature A4 (2026-10-08) ─────────────────


class TestOrchestrateurSansInit:
    """`SubAgentOrchestrator.__new__(...)` SANS `__init__` : un patron réellement
    utilisé dans le dépôt (`tests/llm/test_codex_codeagent_brain.py`), qui ne pose
    que les 4 attributs dont il a besoin.

    Mon pool supposait que `__init__` avait tourné → `AttributeError: '_pools_boucle_id'`
    au full run. Tout ce fichier est défensif pour cette raison exacte (85 `getattr`) ;
    le pool devait l'être aussi.
    """

    @pytest.mark.asyncio
    async def test_un_orchestrateur_nu_ne_casse_pas(self, monkeypatch):
        monkeypatch.setenv("LUMENA_CODEAGENT_POOL", "4")
        from src.agents.sub_agent import AgentType, SubAgent

        o = SubAgentOrchestrator.__new__(SubAgentOrchestrator)   # PAS d'__init__
        o.agents = {}
        agent = SubAgent(agent_type=AgentType.CODE, name="CodeAgent")

        async with o.instance_pour(agent) as inst:
            assert inst is not None
        assert o._pools_agents["CodeAgent"].qsize() == 4

    @pytest.mark.asyncio
    async def test_un_orchestrateur_nu_a_POOL_1_rend_l_agent(self, monkeypatch):
        monkeypatch.setenv("LUMENA_CODEAGENT_POOL", "1")
        from src.agents.sub_agent import AgentType, SubAgent

        o = SubAgentOrchestrator.__new__(SubAgentOrchestrator)
        agent = SubAgent(agent_type=AgentType.CODE, name="CodeAgent")
        async with o.instance_pour(agent) as inst:
            assert inst is agent


class TestConfigurationProduit:
    def test_le_pool_est_expose_dans_le_schema(self):
        from web.routes.config import _CONFIG_SCHEMA

        entree = next(e for e in _CONFIG_SCHEMA if e["key"] == "LUMENA_CODEAGENT_POOL")
        assert entree["default"] == "10"
        assert entree["min"] == 1
        assert entree["max"] == 64
        assert entree["restart"] is True

    def test_le_pool_est_documente_dans_env_example(self):
        from pathlib import Path

        texte = (Path(__file__).parents[2] / ".env.example").read_text(encoding="utf-8")
        assert "LUMENA_CODEAGENT_POOL=10" in texte
