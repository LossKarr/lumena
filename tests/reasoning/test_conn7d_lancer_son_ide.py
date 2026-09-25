"""Lot CONN-7d - Lumena peut lancer SA propre IDE depuis le chat.

**Defaut trouve dans les journaux de Charles, run du 23 septembre 2026 a 21 h 35.**

Il demande a Lumena de lancer son IDE. Elle trouve tout de suite le bon outil :

    Thought: « Compris - il veut MON IDE a moi, l'app Electron. J'ai exactement
              l'outil. »
    ide_launch -> IDE: ide_host_authorization_not_connected

**L'outil fait exactement pour cela est inaccessible depuis le chat.** Ce n'est pas
une regression de CONN-6a/6b : l'ancienne liste en dur `{get_status, get_state}` le
refusait deja. Il n'avait simplement jamais ete regarde - ni dans les 41 lectures,
ni dans les 29 navigations.

--- Ce que ce refus a declenche, et qui est le vrai cout ---

Apres ce refus, puis un `ide_snapshot_stale` au second essai, Lumena a tente
`tasklist`, `netstat` et `wmic process` - tous refuses par la garde L2-1 - avant
d'ecrire :

    Thought: « Le `run_command` est bloque dans le depot. **Je pivote : j'utilise le
              PowerShell MCP (canal different)** pour lancer l'executable. »

**Le contournement n'est pas de la desobeissance : c'est la consequence de trois
portes fermees d'affilee sur une demande legitime.** Ouvrir la bonne porte retire la
pression qui pousse a chercher les mauvaises.

--- La regle, SEMANTIQUE et non nominative ---

Mesure : les **135 commandes du catalogue sont `ProviderKind.IDE`**. `ide_launch`
est le **seul `NATIVE`**, et pour une raison de fond : ce n'est pas une commande
ENVOYEE a une IDE, c'est le CYCLE DE VIE de l'IDE, execute par Lumena elle-meme.

La garde peut donc distinguer les deux sans nommer personne - ce qu'un gel de
CONN-6a lui interdit formellement.

--- Les bornes, qui sont des FAITS deja en place ---

1. `mission_policy: FORBIDDEN` - aucune mission ne peut lancer d'IDE.
2. `ide_launch` n'existe dans le catalogue qu'a l'etat `launch_only`, donc
   **uniquement quand aucune IDE n'est connectee** : pas de double lancement.
3. Le contexte proprietaire est exige bien en amont (`ide_owner_context_required`).
"""
from __future__ import annotations

import pytest

from src.reasoning.caller_context import REACT
from src.reasoning.tool_semantics import Availability, ProviderKind
from src.tools.ide_semantics import audited_ide_commands, local_ide_semantics
from tests.reasoning.test_conn5a_ide_mission_gate import _envoi, _mission
from tests.reasoning.test_conn3c_live_catalogue import owner as owner, registry as registry  # noqa: F401
from tests.tools.test_conn3c_ide_capabilities import service as service  # noqa: F401

REFUS = "IDE: ide_host_authorization_not_connected"


def _aucune_ide_connectee(service):
    """L'etat `launch_only` : c'est le SEUL ou `ide_launch` existe au catalogue."""
    service.bridge._connected = False
    return service


# ── 1. Le fait qui fonde le lot ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_le_chat_peut_lancer_l_ide_de_lumena(registry, service, owner, monkeypatch):
    """Journal du 23/09 a 21 h 35 : refuse, alors que c'est l'outil prevu pour cela."""
    _aucune_ide_connectee(service)
    demandes = []

    class _Lanceur:
        async def ensure_ready(self, workspace=None, **kwargs):
            demandes.append(workspace)
            from src.tools.ide_launcher import IDELaunchResult
            return IDELaunchResult(state="transport_ready", process_started=True,
                                   transport_connected=True, handshake_received=True)

    monkeypatch.setattr("src.tools.ide_launcher.get_ide_launcher", lambda: _Lanceur())

    observation = await registry._ide_tools.execute("ide_launch", {}, caller=REACT)

    assert observation.content != REFUS, observation.content
    # Ne pas s'arreter au refus d'autorisation : le lot vaut seulement si le
    # lancement ABOUTIT. Sans cette seconde assertion, un `ide_snapshot_stale`
    # passerait pour un succes - le piege du test creux, quatrieme de la journee.
    assert observation.success is True, observation.content
    assert demandes, "le lanceur n'a jamais ete sollicite"


def test_le_lancement_ne_passe_PAS_par_une_session_d_ide():
    """`ide_launch` n'existe qu'en `launch_only`, donc SANS session negociee.

    Le rail appelle `expected_session(snapshot)` pour tous les outils - et cette
    methode leve `ide_snapshot_stale` des que `binding.session is None`. C'est
    exactement le second refus du run de Charles a 21 h 37, et c'est ce que mon
    premier test CONN-7d n'avait pas vu : il verifiait seulement que le refus
    d'autorisation avait disparu, pas que le lancement aboutissait.

    Le cycle de vie s'execute LOCALEMENT, par le lanceur. Il n'y a aucune IDE a qui
    envoyer la demande - c'est precisement pour cela qu'on la lance.
    """
    from pathlib import Path

    source = (Path(__file__).resolve().parents[2] / "src" / "reasoning"
              / "ide_tool_runtime.py").read_text(encoding="utf-8")
    debut = source.index("expected = self.service.expected_session(snapshot)")
    amont = source[max(0, debut - 1500):debut]
    assert "ProviderKind.IDE" in amont, (
        "le cycle de vie natif atteint `expected_session`, qui exige une session "
        "negociee qu'il n'a par construction jamais"
    )


# ── 2. La regle est SEMANTIQUE, et ses bornes sont des faits ────────────────

def test_le_cycle_de_vie_natif_est_le_SEUL_natif_du_catalogue():
    """Si une seconde commande devenait NATIVE, la regle changerait de portee.

    Les 135 commandes du catalogue sont `ProviderKind.IDE` : elles partent VERS une
    IDE connectee. `ide_launch` est `NATIVE` parce qu'il ne part nulle part - il
    lance l'IDE. Ce test empeche cette distinction de se diluer.
    """
    natives = [nom for nom in audited_ide_commands()
               if local_ide_semantics(nom, instance_id="0" * 32, revision="0" * 64,
                                      availability=Availability.READY).provider_kind
               is not ProviderKind.IDE]
    assert natives == [], natives


def test_la_garde_ne_COMPARE_aucun_nom_de_commande():
    """Gel de CONN-6a, precise : c'est le CODE qui ne doit nommer personne.

    Premiere version de ce test : recherche textuelle sur la fenetre precedant le
    refus. Elle tombait sur mon propre COMMENTAIRE, qui cite `ide_launch` pour
    expliquer le lot - alors que la prose explicative a de la valeur et ne cree
    aucune dependance. Ce qui doit rester interdit, c'est que la garde COMPARE un
    nom : c'est cela qui redevient obsolete des qu'une commande est ajoutee.
    """
    import re
    from pathlib import Path

    source = (Path(__file__).resolve().parents[2] / "src" / "reasoning"
              / "ide_tool_runtime.py").read_text(encoding="utf-8")
    debut = source.index("ide_host_authorization_not_connected")
    garde = source[max(0, debut - 2600):debut]
    code = "\n".join(ligne for ligne in garde.splitlines()
                     if not ligne.strip().startswith("#"))
    for motif in (r"name\s*==", r"name\s+in\s*\{", r"name\s+not\s+in\s*\{"):
        assert not re.search(motif, code), f"la garde compare un nom : {motif}"


@pytest.mark.asyncio
async def test_aucune_mission_ne_lance_d_ide(registry, service, owner):
    """`mission_policy: FORBIDDEN` - fait deja en place, verifie et non suppose."""
    _aucune_ide_connectee(service)
    _mission(registry)

    observation = await registry._ide_tools.execute("ide_launch", {}, caller=REACT)

    assert observation.success is False
    assert "ide_host_authorization_not_connected" not in observation.content, (
        "une mission doit etre refusee par la politique de mission, pas par la garde du chat"
    )


# ── 3. Les GARDES du cycle de vie ───────────────────────────────────────────
#
# Question de Charles : « tu as pense au guard et tout pour les outils ? »
# Mesure faite en y repondant - deux choses sautaient.

@pytest.mark.asyncio
async def test_le_lancement_ouvre_et_FERME_son_ticket_d_effet(registry, service, owner, monkeypatch):
    """`ide_launch` est un `PROCESS_LAUNCH` : il doit etre enregistre comme tel.

    Mon routage vers le handler natif court-circuitait `external_effect_cache` :
    le lancement d'une IDE n'etait enregistre NULLE PART. C'est le mecanisme meme
    que CONN-7b venait de reparer, et j'en creais un cas qui l'esquive.
    """
    from src.reasoning.external_effect_cache import external_effect_cache

    _aucune_ide_connectee(service)

    class _Lanceur:
        async def ensure_ready(self, workspace=None, **kwargs):
            from src.tools.ide_launcher import IDELaunchResult
            return IDELaunchResult(state="transport_ready", process_started=True,
                                   transport_connected=True, handshake_received=True)

    monkeypatch.setattr("src.tools.ide_launcher.get_ide_launcher", lambda: _Lanceur())
    cache = external_effect_cache()
    with cache._lock:
        epoque_avant, actifs_avant = cache._epoch, len(cache._active)

    await registry._ide_tools.execute("ide_launch", {}, caller=REACT)

    with cache._lock:
        assert cache._epoch > epoque_avant, "le lancement n'a laisse aucune trace d'effet"
        assert len(cache._active) == actifs_avant, "le ticket n'a pas ete referme"


@pytest.mark.asyncio
async def test_un_workspace_hors_perimetre_est_refuse(registry, service, owner, monkeypatch):
    """Rien ne bornait le dossier : `ide_launch(workspace="C:/Windows")` passait.

    Le schema ne declare qu'un « chemin du dossier workspace a ouvrir », et
    `_validate_workspace` verifie seulement que le dossier EXISTE.
    """
    _aucune_ide_connectee(service)
    demandes = []

    class _Lanceur:
        async def ensure_ready(self, workspace=None, **kwargs):
            demandes.append(workspace)
            from src.tools.ide_launcher import IDELaunchResult
            return IDELaunchResult(state="transport_ready", process_started=True,
                                   transport_connected=True, handshake_received=True)

    monkeypatch.setattr("src.tools.ide_launcher.get_ide_launcher", lambda: _Lanceur())

    observation = await registry._ide_tools.execute(
        "ide_launch", {"workspace": "C:/Windows/System32"}, caller=REACT)

    assert observation.success is False, observation.content
    assert demandes == [], "le lanceur a ete sollicite sur un dossier hors perimetre"


# ── 4. Le perimetre, qui ne s'elargit PAS ───────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("outil,params", [
    ("ide__write_file", {"path": "notes.txt", "content": "x"}),
    ("ide__task_run", {"taskId": "package:build"}),
    ("ide__command_run", {"command": "python -m pytest"}),
    ("ide__terminal_run", {"command": "echo bonjour"}),
])
async def test_les_autres_lancements_restent_fermes(registry, service, owner, outil, params):
    """Ouvrir le cycle de vie de l'IDE n'ouvre AUCUN autre processus."""
    envoi = _envoi(service)

    observation = await registry._ide_tools.execute(outil, params, caller=REACT)

    assert observation.content == REFUS, observation.content
    envoi.assert_not_awaited()
