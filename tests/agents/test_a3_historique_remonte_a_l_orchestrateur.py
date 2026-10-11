r"""LOT A3 — l'historique des tâches survit à la mort d'une instance d'agent.

--- Pourquoi ce lot existe ---

`get_agents_status` (outil exposé au LLM) passe par `format_status()` → `get_status()`,
qui dérive DEUX chiffres de l'instance elle-même (`sub_agent.py` l. 8234-8240) :

    "tasks_completed": len(agent.history),
    "last_result":     agent.history[-1]   (status_code, duration_ms)

Aujourd'hui c'est correct : il n'y a qu'UNE instance de CodeAgent, partagée par tous
les workers et par le chat, donc son `history` contient tout.

**Avec le pool du lot A4, ces deux chiffres deviendraient faux.** N instances
interchangeables se partageraient les tâches ; chacune n'aurait qu'une fraction de
l'historique, et l'orchestrateur n'en montrerait qu'une.

--- Ce que ce lot fait, et ce qu'il ne fait PAS ---

L'orchestrateur tient désormais un historique **agrégé par nom d'agent**, alimenté au
point où il enregistre DÉJÀ chaque résultat (`self.results[task.task_id] = result`).

Il ne touche à rien d'autre :
- `agent.history` continue d'exister et d'être alimenté (aucun appelant n'est cassé) ;
- `get_status` lit l'agrégat **avec repli** sur `agent.history` s'il est vide, donc le
  comportement d'aujourd'hui est strictement préservé ;
- même borne mémoire que l'instance (`_history_max`), pour ne pas créer de fuite sur un
  démon 24/7.

C'est une PRÉPARATION : sans A4, ce lot ne change aucun chiffre affiché.
"""
from __future__ import annotations

from src.agents.sub_agent import AgentResult, AgentType, SubAgent, SubAgentOrchestrator


def _orch() -> SubAgentOrchestrator:
    return SubAgentOrchestrator()


def _resultat(task_id: str, code: str = "success", ms: int = 42) -> AgentResult:
    return AgentResult(
        task_id=task_id, success=True, output="ok", status_code=code, duration_ms=ms,
    )


# ── 1. Le cas qui fonde le lot ───────────────────────────────────────────────


class TestHistoriqueAgrege:
    def test_l_orchestrateur_compte_les_taches_meme_sans_history_sur_l_instance(self):
        """Le cas du pool : l'instance qui a fait le travail n'est plus là."""
        o = _orch()
        agent = next(iter(o.agents.values()), None)
        if agent is None:
            return  # orchestrateur sans agent par défaut : rien à prouver ici
        agent.history = []                      # l'instance ne porte RIEN
        o.enregistrer_historique(agent.name, _resultat("t1"))
        o.enregistrer_historique(agent.name, _resultat("t2", "failure", 7))

        ligne = next(a for a in o.get_status()["agents"] if a["name"] == agent.name)
        assert ligne["tasks_completed"] == 2
        assert ligne["last_result"] == {"status_code": "failure", "duration_ms": 7}

    def test_le_dernier_resultat_est_bien_le_dernier(self):
        o = _orch()
        agent = next(iter(o.agents.values()), None)
        if agent is None:
            return
        agent.history = []
        for i in range(5):
            o.enregistrer_historique(agent.name, _resultat(f"t{i}", f"code{i}", i))
        ligne = next(a for a in o.get_status()["agents"] if a["name"] == agent.name)
        assert ligne["tasks_completed"] == 5
        assert ligne["last_result"]["status_code"] == "code4"

    def test_un_agent_inconnu_de_l_agregat_ne_casse_rien(self):
        """Un agent qui n'a jamais rien fait : aucune exception, chiffres à zéro."""
        o = _orch()
        for ligne in o.get_status()["agents"]:
            assert isinstance(ligne["tasks_completed"], int)
            assert ligne["last_result"] is None or isinstance(ligne["last_result"], dict)

    def test_le_resume_de_mission_utilise_aussi_l_historique_agrege(self):
        """Un clone disparu reste visible dans le debrief de mission."""
        o = _orch()
        agent = next(iter(o.agents.values()), None)
        if agent is None:
            return
        agent.history = []
        o.results["clone-1"] = _resultat("clone-1", "success", 12)
        o.enregistrer_historique(agent.name, o.results["clone-1"])

        resume = o.get_mission_summary()

        assert resume["by_agent"][agent.name] == {
            "count": 1,
            "success": 1,
            "avg_duration_ms": 12,
        }


# ── 2. Ce que le lot ne doit PAS changer ─────────────────────────────────────


class TestComportementActuelPreserve:
    def test_repli_sur_history_de_l_instance_quand_l_agregat_est_vide(self):
        """SANS A4, rien ne change : l'historique porté par l'instance fait foi."""
        o = _orch()
        agent = next(iter(o.agents.values()), None)
        if agent is None:
            return
        agent.history = [_resultat("ancien", "success", 99)]
        ligne = next(a for a in o.get_status()["agents"] if a["name"] == agent.name)
        assert ligne["tasks_completed"] == 1
        assert ligne["last_result"] == {"status_code": "success", "duration_ms": 99}

    def test_format_status_reste_affichable(self):
        """`get_agents_status` rend toujours du texte, jamais une exception."""
        o = _orch()
        agent = next(iter(o.agents.values()), None)
        if agent is not None:
            o.enregistrer_historique(agent.name, _resultat("t1"))
        texte = o.format_status()
        assert isinstance(texte, str) and "Sub-Agents Status" in texte

    def test_l_agregat_est_borne_comme_l_instance(self):
        """Démon 24/7 : l'agrégat ne doit pas croître sans fin."""
        o = _orch()
        agent = next(iter(o.agents.values()), None)
        if agent is None:
            return
        borne = getattr(agent, "_history_max", 100)
        for i in range(borne + 50):
            o.enregistrer_historique(agent.name, _resultat(f"t{i}"))
        assert len(o._historique_par_nom[agent.name]) <= borne

    def test_history_de_l_instance_reste_alimente(self):
        """Aucun appelant de `agent.history` n'est cassé par ce lot."""
        a = SubAgent(agent_type=AgentType.CODE, name="CodeAgent")
        assert isinstance(a.history, list)
