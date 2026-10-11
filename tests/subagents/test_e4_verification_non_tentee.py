r"""LOT E4 — « vérifié » devient une condition de clôture, pas un bonus.

--- Décision de Charles, 2026-10-07 ---

> « Lumena doit toujours rendre le travail terminé, testé et vérifié.
>   Jamais de travail partiel. »

Cette règle rend bloquant ce qui n'était jusqu'ici qu'un constat manquant.

--- Le défaut, mesuré ---

`closure_decision` sait dire « la vérification a ÉCHOUÉ » (`web_failed`), elle ne sait
pas dire « aucune vérification n'a EU LIEU » : `_web_failed` vaut `False` par défaut.

**Conséquence : une mission web que personne n'a jamais vérifiée sort
`_CLOSURE_CLEAN`** — « toutes les portes de clôture ont autorisé le résultat ».
Mesure du 02/09 : 2 leads sur 35 servent et ouvrent le navigateur ; **les 33 autres ne
sont signalés nulle part.**

C'est le même motif que la Verification Gate (Z40c) et que le lot 8(a) : il manque le
**troisième état** — ni `échec`, ni `propre`, mais **`non tenté`**.

--- Ce qui rend ce lot petit : le fait existe déjà ---

`browser_runtime.py` pose DEUX métadonnées distinctes, et personne ne les lit à la
clôture :

    web_runtime_verified = True   → vérifié
    web_runtime_failed   = True   → vérification échouée
    ni l'un ni l'autre            → NON TENTÉ

Encore le motif de Lumena : *le fait existe, il est calculé, personne ne le regarde.*

--- Portée volontairement étroite ---

`web_unattempted` ne s'arme **que** si le contrat porte un livrable web. Une mission qui
n'a rien de web n'a rien à vérifier : la traiter comme suspecte serait le défaut inverse
(saturation des gardes, AUD-017).

Priorité : **sous** les échecs avérés, qui nomment un défaut plus précis ; **au-dessus**
de `effects_unproven`, qui est le rattrapage générique.
"""
from __future__ import annotations

import json
import types

import pytest

from src.runtime.task_orchestrator import TaskOrchestrator
from src.subagents import runner as runner_mod
from src.subagents.runner import closure_decision


def _code(**kw) -> str:
    return closure_decision(**{"overclaim": False, "web_failed": False, **kw})[0]


# ── 1. Le cas qui fonde le lot ───────────────────────────────────────────────


def test_une_verification_jamais_tentee_ne_sort_plus_clean():
    """LE défaut : aujourd'hui cette mission sort `completed`."""
    assert _code(web_unattempted=True) == "completed_web_unattempted"


def test_le_detail_dit_NON_TENTEE_et_non_ECHOUEE():
    """Les deux états doivent être distinguables par l'utilisateur, pas seulement
    par le code : « échoué » et « jamais tenté » appellent des gestes différents."""
    _, detail = closure_decision(
        overclaim=False, web_failed=False, web_unattempted=True,
    )
    bas = detail.lower()
    assert "jamais" in bas or "non tentee" in bas or "aucune" in bas
    assert "echoue" not in bas.replace("echouee", "")


# ── 2. Priorités : un échec AVÉRÉ reste plus précis ──────────────────────────


def test_un_echec_avere_l_emporte_sur_le_non_tente():
    """Si la vérification a réellement échoué, c'est ce fait qu'on nomme."""
    assert _code(web_failed=True, web_unattempted=True) == "completed_web_unverified"


def test_un_echec_http_l_emporte_aussi():
    assert _code(web_http_failed=True, web_unattempted=True) == "completed_web_unverified"


def test_l_overclaim_l_emporte_sur_le_non_tente():
    """Une affirmation rétrogradée nomme un défaut plus précis."""
    assert _code(overclaim=True, web_unattempted=True) == "completed_with_unproven_claims"


def test_le_non_tente_l_emporte_sur_les_effets_non_prouves():
    """`effects_unproven` est le rattrapage générique : il passe en dernier."""
    assert _code(web_unattempted=True, effects_unproven=True) == "completed_web_unattempted"


# ── 3. Ce que le lot ne doit PAS changer (caractérisation) ──────────────────


@pytest.mark.parametrize("kw,attendu", [
    ({}, "completed"),
    ({"overclaim": True}, "completed_with_unproven_claims"),
    ({"web_failed": True}, "completed_web_unverified"),
    ({"web_http_failed": True}, "completed_web_unverified"),
    ({"web_failed": True, "overclaim": True}, "completed_web_unverified"),
    ({"effects_unproven": True}, "completed_effects_unproven"),
])
def test_tous_les_verdicts_existants_sont_inchanges(kw, attendu):
    """Gel : sans `web_unattempted`, `closure_decision` rend exactement ce qu'elle
    rendait avant le lot."""
    assert _code(**kw) == attendu


def test_le_parametre_est_optionnel():
    """Aucun appelant existant n'est cassé : le défaut est `False`."""
    assert closure_decision(overclaim=False, web_failed=False)[0] == "completed"


def test_une_mission_sans_livrable_web_reste_clean():
    """Portée étroite : `web_unattempted=False` pour une mission non-web, donc elle
    n'est jamais inquiétée (préserve AUD-017 — ne pas saturer les gardes)."""
    assert _code(web_unattempted=False) == "completed"


# ── 4. Le helper qui arme le fait (pur, donc testable sans runtime) ──────────

from src.subagents.mission_contract import porte_un_livrable_web


@pytest.mark.parametrize("files,attendu", [
    ([{"path": "index.html"}], True),
    ([{"path": "site/accueil.htm"}], True),
    ([{"path": "app.py"}, {"path": "static/index.html"}], True),
    ([{"path": "app.py"}], False),
    ([{"path": "notes.md"}, {"path": "data.csv"}], False),
    ([], False),
])
def test_detection_du_livrable_web(files, attendu):
    assert porte_un_livrable_web({"files": files}) is attendu


@pytest.mark.parametrize("data", [None, {}, {"files": None}, {"files": ["pas un dict"]}, "texte"])
def test_le_helper_ne_casse_sur_aucune_entree(data):
    """Un contrat malformé ne doit jamais faire tomber une clôture."""
    assert porte_un_livrable_web(data) is False


# ── 5. Intégration : la décision pure commande réellement le runner ─────────


@pytest.mark.asyncio
async def test_runner_ne_marque_jamais_done_un_livrable_web_non_verifie(
    tmp_path, monkeypatch,
):
    """Le bug réel : le code calculait E4, puis appelait quand même `mark_done`."""
    monkeypatch.setattr(runner_mod, "create_mission_registry", lambda core: object())
    monkeypatch.setattr("src.utils.paths.WORKSPACE_DIR", tmp_path)

    orch = TaskOrchestrator(persistence_path=str(tmp_path / "tasks.json"))
    mission = orch.start_task(
        conversation_id="__missions__",
        channel="mission",
        message_preview="Créer un site",
        metadata={"kind": "mission", "mission_workspace": "site-e4"},
    )
    workspace = tmp_path / "site-e4"
    workspace.mkdir()
    (workspace / "contract.json").write_text(
        json.dumps({"files": [{"path": "index.html"}]}),
        encoding="utf-8",
    )

    async def silent(objective, **kwargs):
        return "Site produit, mais aucune vérification navigateur n'a été exécutée."

    core = types.SimpleNamespace(
        task_orchestrator=orch,
        think_and_act_silent=silent,
    )

    out = await runner_mod.run_mission(
        core, mission_id=mission.task_id, objective="Créer un site",
    )
    record = orch.get_task(mission.task_id)

    assert out["status"] == "failed"
    assert record["state"] == "failed"
    assert record["result_summary"] == out["result"]
    assert record["metadata"]["terminal_reason_code"] == "completed_web_unattempted"
    assert record["metadata"]["completion_proven"] is False
    assert record["metadata"]["verification_required"] is True
    assert record["metadata"]["verification_reason"] == "web_runtime_unattempted"
