"""Lot IDE-1 - un outil MUET est pire qu'un outil qui echoue.

--- Ce que le run reel du 24 septembre 2026 a mesure ---

Charles demande a Lumena d'ouvrir son IDE sur un projet. Journal, 14:44:31 -> 14:45:09 :

    14:44:31  Thought: « je peux donc ouvrir le projet dedans »
    14:44:39  Observation:                      <-- VIDE. Huit secondes d'execution.
    14:44:42  list_windows  -> « Lumena IDE »   <-- la fenetre EXISTE, le lancement a REUSSI
    14:44:44  screenshot                        }
    14:44:48  ide_launch -> ide_snapshot_stale  }  cinq iterations a deviner
    14:44:51  screenshot + list_windows         }  ce que l'outil savait deja
    14:44:59  ide_launch -> ide_snapshot_stale  }
    14:45:09  « je refuse de te dire oui alors que je ne le vois pas de mes yeux »

Elle a eu raison de refuser. Mais elle n'aurait jamais du en arriver la.

--- Deux defauts distincts, l'un dans mon propre lot ---

**IDE-1a.** `_executer_cycle_de_vie` (ecrit pour CONN-7d) lisait le texte du resultat
avec `getattr(resultat, "content", "")`. Or `HandlerResult` porte son texte dans
`output`, et son message d'echec dans `error` — **il n'a pas d'attribut `content`**.
Le rendu etait donc TOUJOURS vide, succes comme echec. Le lanceur disait
« IDE Lumena lance; handshake confirme; authentifie; workspace: ... » et personne
ne le lisait.

**IDE-1b.** `ide_snapshot_stale` est tombe SEPT fois dans ce run, sans guidance : il
ne figurait pas parmi les 64 codes couverts par CONN-7a. Et ce n'est meme pas une
panne — c'est l'etat NORMAL apres un lancement reussi. `is_current()` compare la
session ancree a celle du snapshot ; un snapshot pris sans IDE connectee porte
`session is None` et ne reste courant que tant qu'aucune IDE ne l'est. Des que le
lancement aboutit, il se perime. **Le succes invalide son propre snapshot.**

Le mot « perime » a fait croire au modele qu'il avait echoue. La guidance doit dire
l'inverse : c'est bon signe, reprends une photo.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.reasoning.caller_context import REACT
from src.reasoning.handlers.contracts import HandlerResult
from src.reasoning.ide_error_guidance import GUIDES_ERREURS_IDE, expliquer_erreur_ide
from tests.reasoning.test_conn3c_live_catalogue import owner as owner, registry as registry  # noqa: F401
from tests.tools.test_conn3c_ide_capabilities import service as service  # noqa: F401

RACINE = Path(__file__).resolve().parents[2]


def _aucune_ide_connectee(service):
    service.bridge._connected = False
    return service


def _lanceur(monkeypatch, *, message_vide: bool = False):
    """Un lanceur qui reussit. `message_vide` simule un lanceur qui ne dit rien."""
    class _Lanceur:
        async def ensure_ready(self, workspace=None, **kwargs):
            from src.tools.ide_launcher import IDELaunchResult
            return IDELaunchResult(state="transport_ready", process_started=True,
                                   transport_connected=True, handshake_received=True)
    monkeypatch.setattr("src.tools.ide_launcher.get_ide_launcher", lambda: _Lanceur())


# ── 1. IDE-1a : le lancement PARLE ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_un_lancement_reussi_ne_rend_jamais_une_observation_vide(
        registry, service, owner, monkeypatch):
    """Le defaut exact du run : huit secondes d'execution, zero caractere rendu."""
    _aucune_ide_connectee(service)
    _lanceur(monkeypatch)

    observation = await registry._ide_tools.execute("ide_launch", {}, caller=REACT)

    assert observation.success is True, observation.content
    assert observation.content.strip(), (
        "observation VIDE sur un lancement reussi — c'est le defaut du 24/09 14:44:39"
    )


@pytest.mark.asyncio
async def test_le_message_du_lanceur_arrive_jusqu_au_modele(
        registry, service, owner, monkeypatch):
    """Pas seulement « non vide » : le texte REEL du lanceur, pas un substitut.

    Sans cette assertion, un repli generique ferait passer le test alors que
    l'information utile — reutilise ou lance, authentifie, quel workspace — serait
    encore perdue.
    """
    _aucune_ide_connectee(service)
    _lanceur(monkeypatch)

    observation = await registry._ide_tools.execute("ide_launch", {}, caller=REACT)

    texte = observation.content.lower()
    assert "handshake" in texte, f"le message du lanceur n'est pas transmis : {observation.content!r}"


def test_le_rail_ne_lit_plus_un_attribut_qui_n_existe_pas():
    """La cause racine, figee : `HandlerResult` n'a PAS de `content`.

    C'est ce qui rendait le vide. Le contrat est verifie sur la classe elle-meme,
    donc le test tombe aussi si quelqu'un AJOUTE un `content` un jour — auquel cas
    il faudra relire ce rail en connaissance de cause.
    """
    champs = set(getattr(HandlerResult, "__dataclass_fields__", {}))
    assert "output" in champs
    assert "content" not in champs, (
        "HandlerResult a gagne un `content` : verifier que le rail IDE lit le bon champ"
    )

    source = (RACINE / "src" / "reasoning" / "ide_tool_runtime.py").read_text(encoding="utf-8")
    debut = source.index("async def _executer_cycle_de_vie")
    corps = source[debut:source.index("async def execute", debut)]
    assert 'getattr(resultat, "content"' not in corps, (
        "le rail relit `content` sur le resultat du handler : le vide reviendrait"
    )
    assert "to_legacy_str" in corps, "le rail n'utilise pas le convertisseur prevu"


@pytest.mark.asyncio
async def test_meme_un_lanceur_muet_produit_une_issue_lisible(
        registry, service, owner, monkeypatch):
    """Dernier filet : si le lanceur ne dit rien, le rail dit au moins l'issue.

    Un outil qui rend le vide laisse le modele deviner ; et deviner, dans ce run,
    a coute cinq iterations.
    """
    _aucune_ide_connectee(service)

    class _Muet:
        async def ensure_ready(self, workspace=None, **kwargs):
            from src.tools.ide_launcher import IDELaunchResult
            return IDELaunchResult(state="transport_ready", process_started=True,
                                   transport_connected=True, handshake_received=True)

    monkeypatch.setattr("src.tools.ide_launcher.get_ide_launcher", lambda: _Muet())
    monkeypatch.setattr(
        "src.reasoning.handlers.ide._handle_ide_launch",
        lambda ctx, **kw: _reponse_vide(),
    )

    observation = await registry._ide_tools.execute("ide_launch", {}, caller=REACT)
    assert observation.content.strip(), "le rail rend encore du vide"


async def _reponse_vide():
    return HandlerResult(success=True, output="", handler_name="ide_launch")


# ── 2. IDE-1b : `ide_snapshot_stale` explique, et bien explique ──────────────

def test_le_code_tombe_sept_fois_a_desormais_une_guidance():
    """Il ne figurait pas parmi les 64 codes de CONN-7a. C'etait le plus frequent."""
    assert "ide_snapshot_stale" in GUIDES_ERREURS_IDE
    rendu = expliquer_erreur_ide("ide_snapshot_stale")
    assert rendu and "ide_snapshot_stale" in rendu


def test_la_guidance_dit_que_le_stale_suit_souvent_un_SUCCES():
    """Le coeur du lot : ce n'est pas une panne, c'est la consequence d'un succes.

    Si la guidance se contentait de dire « photo perimee », le modele continuerait
    a croire qu'il a echoue — et a relancer, comme il l'a fait deux fois.
    """
    cause, geste = GUIDES_ERREURS_IDE["ide_snapshot_stale"]
    assert "reussi" in cause.lower() or "succes" in cause.lower(), cause
    assert "ne relance pas" in geste.lower(), geste

    # ── LOT IDE-8 (24/09, 20 h) : cette exigence etait FAUSSE, et le reel l'a dit ──
    #
    # Ce test exigeait `ide__get_status` dans le geste, en croyant que c'etait la voie
    # de sortie. **`ide__get_status` est LUI-MEME derriere le meme snapshot** : il leve
    # le meme refus. Le run l'a prouve a la lettre - Lumena a lu le conseil, l'a compris
    # (« le message est enfin explicite »), l'a applique, et s'est fait refuser ; puis
    # « j'ai fait exactement ce que le message demandait et c'est ENCORE stale ».
    # 28 refus sur 32 iterations.
    #
    # L'exigence est donc INVERSEE : le geste ne doit nommer AUCUNE commande de l'IDE,
    # puisqu'elles passent toutes par `is_current`. IDE-6 fournit la vraie sortie (la
    # photo se reprend seule) et `lumena_ide`, natif, permet de REGARDER sans snapshot.
    # Le gel correspondant vit dans test_ide6_catalogue_perime_se_renouvelle.py.
    assert "ide__" not in geste, (
        "le geste renvoie vers une commande snapshot-gated, donc inatteignable : "
        f"{geste!r}"
    )
    assert "lumena_ide" in geste, geste


def test_l_echec_de_lancement_a_aussi_son_geste():
    assert "ide_launch_failed" in GUIDES_ERREURS_IDE
    cause, geste = GUIDES_ERREURS_IDE["ide_launch_failed"]
    assert "handshake" in cause.lower()
    assert geste.strip()
