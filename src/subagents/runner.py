"""Lot 1.2 — Runner de mission : exécute une mission par un sous-agent « Lumena complète ».

⚠️ NE PAS CONFONDRE avec le **CodeAgent** (`src/agents/sub_agent.py`, dev-only,
`delegate_to_agent`/orchestrateur). Ici, un « sous-agent de mission » = un appel à
**`core.think_and_act_silent`** (Lumena entière, 707 outils) sur un registre ISOLÉ.
Ce module n'importe et n'appelle **JAMAIS** le CodeAgent.

Garanties :
- registre **isolé** par mission (factory Lot 0.b) → zéro course avec le chat ;
- cycle de vie via `TaskOrchestrator` (`mark_running`/`mark_done`/`mark_failed`) ;
- **annulation coopérative** : on n'écrase jamais un état `cancelled` par `done` ;
- **jamais fatal** : toute exception → mission `failed`, pas de propagation.
"""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from loguru import logger

from src.subagents.registry_factory import create_mission_registry


# ── F2 — décision de clôture (fonction PURE, testable hors runner) ─────────────
# Une mission peut être terminée sans être PROUVÉE. Deux faits, tous deux déjà
# calculés ailleurs pendant le run et jusqu'ici ignorés au moment de clore :
#   • `overclaim`   — le verrou de vérité a rétrogradé une affirmation faute de
#                     preuve au ledger (F1.b, cause AUD-014) ;
#   • `web_failed`  — le vérificateur runtime web a réellement échoué (route
#                     principale morte, 4xx/5xx same-origin…), verdict déjà
#                     persisté par `_set_web_runtime_verification_state`
#                     (cause AUD-013 : pytest vert, `/` en 404, mission `done`).
# La mission reste `done` dans les deux cas — le travail existe et doit rester
# consultable. C'est l'ÉTAT qui devient fidèle, pas le verdict qui devient punitif.
_CLOSURE_CLEAN = (
    "completed",
    "toutes les portes de cloture ont autorise le resultat",
)
_CLOSURE_UNPROVEN_CLAIM = (
    "completed_with_unproven_claims",
    "livrable rendu, mais au moins une affirmation a ete retrogradee "
    "faute de preuve au ledger (verrou de verite)",
)
_CLOSURE_WEB_UNVERIFIED = (
    "completed_web_unverified",
    "livrable web rendu, mais la verification runtime a echoue "
    "(page principale ou integration non fonctionnelle)",
)
_CLOSURE_WEB_UNVERIFIED_AND_CLAIM = (
    "completed_web_unverified",
    "livrable web rendu, mais la verification runtime a echoue ET au moins une "
    "affirmation a ete retrogradee faute de preuve au ledger",
)


_CLOSURE_EFFECTS_UNPROVEN = (
    "completed_effects_unproven",
    "livrable rendu, mais au moins un EFFET contractuel n'a pas de porteur "
    "arrive a terme (action non prouvee realisee)",
)


_EFFECTS_UNPROVEN_BANNER = (
    "⚠️ **Effet(s) non réalisé(s)** — le contrat de cette mission engageait des "
    "actions dont le porteur n'est jamais arrivé à terme : {owners}. Ce qui suit "
    "ne couvre PAS ces effets, même si le texte n'en parle pas."
)


_NOT_PUBLISHED_BANNER = (
    "📦 **Livrable non publié** — {count} fichier(s) ont été produits mais "
    "`publish_mission_workspace` n'a jamais tourné. Ils sont ici : `{path}` "
    "(dossier de travail de la mission). Rien n'a été copié vers un dossier "
    "de livraison."
)

_WORKER_REPORT_BANNER = (
    "ℹ️ **Ce bilan est le rapport d'un sous-agent**, pas la conclusion de la "
    "mission : celle-ci n'a pas produit de réponse finale. Les chemins qu'il "
    "cite sont relatifs à son dossier de travail."
)


def annotate_unpublished_deliverable(text: Any, count: Any, path: Any) -> str:
    """LOT Z9 — porte le fait « ce travail n'est pas sorti de l'atelier » DANS le
    texte livré. Jumeau exact de `annotate_unproven_effects` (H6), pour les
    FICHIERS au lieu des EFFETS.

    Run « décision voiture » (2026-08-15), le test le plus exigeant de la série :
    Lumena a décidé seule de son organisation, choisi seule le format, fait
    20 recherches web réelles, produit un comparateur interactif de 658 lignes
    sans aucune dépendance, sources citées. Puis :

        mission_published = None
        workspace/decision_voiture_2026/          → n'existe pas
        missions/task_d8c25fef…/decision_voiture_2026/  → le travail est là

    Le fait était connu du système, posé en métadonnée, vérifiable sur le
    disque — et absent du seul endroit que l'utilisateur lit.

    Mesuré sur le corpus : **95 missions leads ont produit des fichiers, 17 ont
    publié, 78 non — et AUCUN résumé ne le disait.** Pour 17 d'entre elles le
    livrable est réellement introuvable ailleurs que dans `missions/`.

    Constat, jamais blocage : ne pas publier peut être légitime (brouillon,
    échec partiel, mission d'analyse). Ce qui ne l'est pas, c'est de ne pas le
    dire. Pur. Texte inchangé si rien n'a été produit ou si la mission a publié.
    """
    body = str(text or "")
    try:
        n = int(count or 0)
    except Exception:
        n = 0
    chemin = str(path or "").strip()
    if n <= 0 or not chemin:
        return body
    banner = _NOT_PUBLISHED_BANNER.format(count=n, path=chemin)
    if banner[:40] in body:      # idempotent (re-clôture après reprise)
        return body
    return f"{banner}\n\n{body}" if body.strip() else banner


def annotate_worker_report_fallback(text: Any, is_fallback: Any) -> str:
    """LOT Z9b — dire que le bilan vient d'un sous-agent, pas de la mission.

    Même run : le lead a émis `Action: final`, son texte n'a jamais abouti, et
    le `result_summary` est devenu le rapport brut du CodeAgent — « ✅ codeAgent
    terminé (259.7s, 18 itérations) ». C'est le mécanisme H7, resté ⬜ non
    vérifié trois semaines, qui s'est déclenché pour la première fois.

    Il fonctionne : sans lui, l'utilisateur n'aurait rien reçu du tout. Mais
    rien ne distingue ce rapport d'une vraie conclusion, et ses chemins sont
    relatifs au dossier du sous-agent. Indiscernable = trompeur.

    Pur. Texte inchangé quand le bilan est bien celui de la mission.
    """
    body = str(text or "")
    if not is_fallback or not body.strip():
        return body
    if _WORKER_REPORT_BANNER[:40] in body:
        return body
    return f"{_WORKER_REPORT_BANNER}\n\n{body}"


def annotate_unproven_effects(text: Any, owners: Any) -> str:
    """H6 — porte le fait « cet effet n'a pas eu lieu » DANS le texte livré.

    Le run `veille_python_313` a livré ses 3 sources sans dire nulle part que la
    synthèse en mémoire n'avait pas été faite : `completion_proven=False` était
    fidèle, le TEXTE ne l'était pas — et c'est le texte que l'utilisateur lit.
    Même patron que les bannières du truth-lock : additif, en tête, jamais de
    réécriture du contenu.

    Pur. Texte inchangé si aucun owner en défaut.
    """
    body = str(text or "")
    names = [str(o).strip() for o in (owners or []) if str(o or "").strip()]
    if not names:
        return body
    banner = _EFFECTS_UNPROVEN_BANNER.format(owners=", ".join(names))
    if banner[:40] in body:      # idempotent (re-clôture après reprise)
        return body
    return f"{banner}\n\n{body}" if body.strip() else banner


def closure_decision(
    *, overclaim: bool, web_failed: bool, web_http_failed: bool = False,
    effects_unproven: bool = False,
) -> tuple:
    """Retourne `(terminal_reason_code, terminal_reason_detail)` pour une mission
    arrivée au bout de son run sans annulation ni échec déclaré.

    Pur : aucune I/O, aucun état. `completion_proven` vaut False dès qu'un des
    deux faits est vrai — c'est l'appelant qui le persiste.

    Priorité : l'échec runtime web l'emporte sur l'overclaim textuel. Un
    vérificateur qui a réellement échoué est un fait plus dur qu'une formulation
    rétrogradée ; le détail mentionne alors les deux.
    """
    # H5 — un échec HTTP same-origin observé au navigateur vaut échec de
    # vérification web, quel que soit l'outil qui l'a vu. Le test réel du
    # 2026-08-13 a livré une mission `completed` avec sa page d'accueil en 404 :
    # le lead avait vérifié avec `browser_navigate` (qui l'a VU et affiché) au
    # lieu de `browser_verify_local_project`, seul pourvoyeur de `web_runtime_failed`.
    _web = bool(web_failed or web_http_failed)
    if _web and overclaim:
        return _CLOSURE_WEB_UNVERIFIED_AND_CLAIM
    if _web:
        return _CLOSURE_WEB_UNVERIFIED
    if overclaim:
        return _CLOSURE_UNPROVEN_CLAIM
    # H4 — un effet contractuel dont le porteur n'a jamais terminé ne peut pas
    # être prouvé réalisé. Priorité BASSE : les faits ci-dessus sont plus précis
    # (ils nomment le défaut), celui-ci rattrape le cas non-code où aucun d'eux
    # ne s'applique — une mission « envoie le mail » n'a ni web ni pytest.
    if effects_unproven:
        return _CLOSURE_EFFECTS_UNPROVEN
    return _CLOSURE_CLEAN


# ── Profil « lead » (Lot 5 — D) ─────────────────────────────────────────────────
# Quand la délégation est POSSIBLE (profondeur courante < MAX_DEPTH), on cadre le
# sous-agent comme un « lead » : il PEUT confier des sous-tâches indépendantes à des
# sous-agents (delegate_and_wait). Steering, pas forçage — il garde parallel_tools &
# tous les outils, et choisit selon la nature de la tâche.
_LEAD_PREFIX = (
    "[Mode mission] Tu es un agent de mission (une Lumena complète) qui travaille en "
    "arrière-plan. Si ce travail se décompose en sous-tâches INDÉPENDANTES demandant "
    "CHACUNE un vrai raisonnement multi-étapes (plusieurs recherches + analyse + rédaction "
    "par sujet, etc.), tu PEUX les confier à des sous-agents EN PARALLÈLE via "
    "delegate_and_wait([...]) puis fusionner leurs résultats. Sinon, fais-le directement "
    "(parallel_tools suffit pour de simples appels d'outils).\n"
    # LOT Z1 — « Sinon, fais-le directement » se lisait « code à la main ». Deux
    # décisions distinctes étaient confondues : DÉCOUPER en sous-agents, et CODER
    # avec l'outil de code. Sur HuffPack, le lead a jugé — à raison — qu'il n'y
    # avait rien à découper, puis a écrit 50 read_file et 5 éditions manuelles ;
    # le livrable est ressorti à 12 tests rouges. Il avait pourtant `delegate_task`
    # dans les mains : la consigne CODE PAR DÉLÉGATION n'est injectée qu'aux
    # workers contractuels, jamais à lui. On l'informe — steering, pas forçage :
    # le garde CODEAGENT-FIRST sort sur `not owned` avant même de lire ce texte,
    # donc le lead reste libre de son choix.
    "⚙️ Ne PAS découper ≠ coder à la main. Même seul, pour ÉCRIRE du code tu peux "
    "déléguer au CodeAgent via delegate_task(description='...', agent_type='code') "
    "— il a le harnais (plan, édition ciblée, exécution, réparation). Tu restes "
    "responsable de VÉRIFIER par une exécution réelle et de conclure.\n"
    "⚠️ Pour un livrable CODE multi-fichiers : pose D'ABORD le contrat via "
    "write_mission_contract (fichiers, owners, signatures d'API exactes) — il crée les "
    "stubs et te rend les objectifs avec le périmètre de chaque worker ; délègue ENSUITE "
    "avec ces objectifs. Un contrat en prose ne suffit pas : sans stubs ni périmètres, "
    "les workers inventent des API incompatibles.\n\nMission :\n"
)


def _max_depth() -> int:
    try:
        return max(1, min(8, int(os.getenv("LUMENA_MISSION_MAX_DEPTH", "1"))))
    except (ValueError, TypeError):
        return 1


def _mission_depth(core: Any, mission_id: str) -> int:
    orch = getattr(core, "task_orchestrator", None)
    if orch is None:
        return 1
    try:
        task = orch.get_task(mission_id) or {}
        return max(1, int((task.get("metadata") or {}).get("depth") or 1))
    except (ValueError, TypeError, Exception):
        return 1


def _delegation_possible(core: Any, mission_id: str) -> bool:
    """True si CE sous-agent peut encore déléguer (profondeur courante < MAX_DEPTH)."""
    return _mission_depth(core, mission_id) < _max_depth()


def _is_top_lead(core: Any, mission_id: str) -> bool:
    """True UNIQUEMENT pour un lead de 1er niveau (depth==1) qui peut déléguer.

    Seul le top lead reçoit le profil « lead » → un worker (depth≥2) n'est PAS
    encouragé à re-déléguer → pas de cascade de délégation (anti-explosion).
    """
    depth = _mission_depth(core, mission_id)
    return depth == 1 and depth < _max_depth()


# Plancher d'exécution d'un top-lead (H.2) : couvre une délégation par défaut
# (delegate_and_wait = 1200 s) + une marge d'intégration (600 s). Borné à 1 h.
_LEAD_TIMEOUT_FLOOR_S = 1800.0
_MISSION_TIMEOUT_CAP_S = 3600.0


def _effective_lead_timeout(core: Any, mission_id: str, timeout: float) -> float:
    """Budget d'exécution EFFECTIF d'une mission (helper PUR, testable sans async).

    Deux relèvements CUMULATIFS, jamais un raccourcissement (max monotone), bornés 1 h :

    - **B0.1 (run PlantCare)** : si la mission a une échéance (`deadline_ts`), on s'aligne
      sur le temps restant + 120 s de finalisation. Le plafond fixe 600 s avait TUÉ un lead
      en plein travail alors que sa mission avait 20 min de budget.
    - **H.2 (run BiblioFlux)** : INVARIANT INVERSÉ — le lead vivait moins longtemps que
      l'attente qu'il subissait. `delegate_and_wait` attend ses workers jusqu'à 1200 s
      tandis que le lead était plafonné à 600 s → il mourait TOUJOURS en pleine délégation,
      avant d'intégrer (pytest global / navigateur / publication). Un top-lead a pour métier
      de déléguer PUIS d'intégrer → plancher 1800 s (1200 délégation + 600 intégration).
      Filet de SÉCURITÉ, pas signal de fin (la finalisation d'échéance 5.7.4 reste le vrai
      déclencheur de clôture). Gated top-lead : les sous-workers gardent leur budget hérité.

    Sans échéance ET hors top-lead → `timeout` inchangé (comportement strictement identique).
    """
    try:
        timeout = float(timeout)
    except (ValueError, TypeError):
        timeout = 600.0

    orch = getattr(core, "task_orchestrator", None)
    # B0.1 — uplift échéance
    try:
        if orch is not None:
            rec = orch.get_task(mission_id) or {}
            if ((rec.get("metadata") or {}).get("deadline_ts")):
                from src.subagents.mission_budget import mission_budget
                rem = (mission_budget(rec) or {}).get("remaining_s")
                if rem is not None and float(rem) > 0:
                    timeout = max(timeout, min(float(rem) + 120.0, _MISSION_TIMEOUT_CAP_S))
    except Exception:
        pass

    # H.2 — plancher top-lead (tire uniquement quand la délégation est possible)
    try:
        if _is_top_lead(core, mission_id):
            timeout = max(timeout, min(_LEAD_TIMEOUT_FLOOR_S, _MISSION_TIMEOUT_CAP_S))
    except Exception:
        pass

    return timeout


async def run_mission(
    core: Any,
    *,
    mission_id: str,
    objective: str,
    timeout: float = 600.0,
    allowed_tools: Optional[list] = None,
) -> Dict[str, Any]:
    """Exécute la mission `mission_id` (déjà créée dans le `TaskOrchestrator`).

    Retourne `{mission_id, status, result, artifacts}`. `status` ∈
    `done`/`failed`/`cancelled`. Ne lève jamais.
    """
    orch = getattr(core, "task_orchestrator", None)
    artifacts: List[str] = []
    result = ""

    # Annulée AVANT lancement → on ne démarre pas (gate le futur).
    if orch is not None and orch.is_cancel_requested(mission_id):
        try:
            current = orch.get_task(mission_id) or {}
            meta = current.get("metadata") or {}
            if not meta.get("terminal_reason_code"):
                orch.set_task_metadata(
                    mission_id,
                    terminal_reason_code="cancelled",
                    terminal_reason_detail="annulation demandee avant le lancement",
                    terminal_at=datetime.now(timezone.utc).isoformat(),
                )
        except Exception:
            pass
        return {"mission_id": mission_id, "status": "cancelled", "result": "", "artifacts": []}

    registry = create_mission_registry(core)  # registre ISOLÉ (pas celui du chat)
    _recovery_mode = False
    # LOT I.1 — porte `category:agents` : un worker de mission tourne en caller=react et se
    # voit refuser toute la catégorie 'agents' (delegate_task→CodeAgent, process_status) faute
    # de workspace résolu (`[category:agents] Refus - workspace_path requis`). On tague SON
    # registre (mission uniquement) avec le dossier de la mission → la catégorie passe. Le
    # registre du chat n'a pas cet attribut → comportement du chat strictement inchangé.
    try:
        _mw_meta = None
        if orch is not None:
            _run_meta = ((orch.get_task(mission_id) or {}).get("metadata") or {})
            _mw_meta = _run_meta.get("mission_workspace")
            _recovery_mode = bool(_run_meta.get("recovery_required"))
        from src.utils.paths import WORKSPACE_DIR as _WS_I1
        registry._mission_workspace_abs = str((_WS_I1 / _mw_meta) if _mw_meta else _WS_I1)
    except Exception:
        pass
    if orch is not None:
        orch.mark_running(mission_id)

    # Lot 4.1 — contexte de trace PROPRE à la mission (force=True) → tous ses steps d'outils
    # sont publiés sur le TraceBus taggés `task_id=mission_id` → streamables en SSE par carte,
    # comme le mode agent. Contextvars par-tâche asyncio → zéro contamination du chat.
    _trace_tokens = None
    try:
        from src.telemetry import push_trace_context
        _trace_tokens = push_trace_context(
            task_id=mission_id, channel="mission", mode="agent", force=True,
        )
    except Exception:
        _trace_tokens = None

    # Profil « lead » : cadrage de délégation UNIQUEMENT si la profondeur l'autorise
    # (sinon objectif brut → comportement identique, zéro régression au flag défaut).
    prompt = objective
    try:
        if _is_top_lead(core, mission_id):  # profil lead SEULEMENT au 1er niveau (anti-cascade)
            prompt = _LEAD_PREFIX + objective
    except Exception:
        prompt = objective

    # Lot 5.7.2 — conscience temporelle CALME : si la mission a une échéance normalisée,
    # on préfixe un cadrage anti-stress (budget = aide à prioriser, jamais bâcler).
    # Pas d'échéance → "" → prompt identique (zéro régression).
    try:
        if orch is not None:
            _md = (orch.get_task(mission_id) or {}).get("metadata") or {}
            _dts = _md.get("deadline_ts")
            if _dts:
                from src.subagents.mission_budget import mission_budget_preamble
                _pre = mission_budget_preamble(_dts)
                if _pre:
                    prompt = _pre + prompt
    except Exception:
        pass

    # Budget d'exécution effectif (uplift échéance B0.1 + plancher top-lead H.2).
    timeout = _effective_lead_timeout(core, mission_id, timeout)

    # #4 — cette mission devient l'owner navigateur (exclusivité sticky sur sa session).
    from src.subagents.resource_lease import (
        set_browser_owner, clear_browser_owner, get_browser_exclusivity,
    )
    _browser_token = set_browser_owner(mission_id)

    # F1 — preuves du run (out-param, un dict par mission). Le chat conservait son
    # run_meta, la mission le jetait : le runner ne pouvait juger que la FORME du
    # résultat, jamais les EFFETS. Rempli au mieux, jamais requis.
    proof: dict = {}

    try:
        result = await core.think_and_act_silent(
            prompt,
            timeout=timeout,
            allowed_tools=allowed_tools,
            allowed_tools_hard=_recovery_mode,
            allow_when_busy=True,
            artifacts_out=artifacts,
            tool_registry=registry,
            task_orchestrator=orch,
            task_id=mission_id,
            proof_out=proof,
        )
    except asyncio.CancelledError:
        raise  # annulation de tâche asyncio (shutdown) → doit se propager
    except SystemExit as exc:
        # Cancel COOPÉRATIF émis par la boucle ReAct (raise SystemExit). C'est une
        # ANNULATION de mission, PAS un crash — on l'absorbe (sinon SystemExit
        # s'échappe de la tâche asyncio et tue tout l'event loop / l'app).
        logger.info("[mission {}] annulée (cancel coopératif)", mission_id)
        if orch is not None:
            try:
                raw_reason = str(exc or "task_cancelled")
                current = orch.get_task(mission_id) or {}
                meta = current.get("metadata") or {}
                reason_code = str(meta.get("terminal_reason_code") or "").strip()
                if not reason_code:
                    reason_code = (
                        "deadline_expired"
                        if raw_reason == "mission_deadline_grace_expired"
                        else "cancelled"
                    )
                orch.set_task_metadata(
                    mission_id,
                    terminal_reason_code=reason_code,
                    terminal_reason_detail=raw_reason,
                    terminal_at=datetime.now(timezone.utc).isoformat(),
                )
            except Exception:
                pass
        return {"mission_id": mission_id, "status": "cancelled", "result": "",
                "artifacts": list(artifacts)}
    except Exception as exc:
        logger.warning("[mission {}] échec: {}", mission_id, exc)
        if orch is not None:
            try:
                detail = str(exc)[:800]
                if isinstance(exc, asyncio.TimeoutError) or detail.startswith("mission_timeout:"):
                    reason_code = "timeout"
                elif detail.startswith("llm_provider_error:"):
                    reason_code = "provider_error"
                elif detail.startswith("mission_react_"):
                    reason_code = "react_failure"
                else:
                    reason_code = "failed"
                # mark_failed owns the state transition. Persist the specific
                # cause afterwards so its generic default cannot overwrite it.
                orch.mark_failed(mission_id, detail)
                orch.set_task_metadata(
                    mission_id,
                    terminal_reason_code=reason_code,
                    terminal_reason_detail=detail,
                    terminal_at=datetime.now(timezone.utc).isoformat(),
                )
            except Exception:
                pass
        return {"mission_id": mission_id, "status": "failed", "result": "", "artifacts": list(artifacts)}
    finally:
        # Libère l'exclusivité navigateur de la mission (idempotent, owner-checké,
        # no-op si elle n'a jamais navigué) — sinon navigateur verrouillé jusqu'au timeout.
        try:
            await get_browser_exclusivity().release_owner(mission_id)
        except Exception:
            pass
        clear_browser_owner(_browser_token)
        if _trace_tokens is not None:
            try:
                from src.telemetry import pop_trace_context
                pop_trace_context(_trace_tokens)
            except Exception:
                pass

    # Annulée PENDANT le run → ne PAS écraser l'état `cancelled` par `done`.
    if orch is not None and orch.is_cancel_requested(mission_id):
        try:
            current = orch.get_task(mission_id) or {}
            meta = current.get("metadata") or {}
            if not meta.get("terminal_reason_code"):
                orch.set_task_metadata(
                    mission_id,
                    terminal_reason_code="cancelled",
                    terminal_reason_detail="annulation demandee pendant l'execution",
                    terminal_at=datetime.now(timezone.utc).isoformat(),
                )
        except Exception:
            pass
        return {"mission_id": mission_id, "status": "cancelled", "result": str(result),
                "artifacts": list(artifacts)}

    # ReAct may already have declared an authoritative failure (for example an
    # iteration limit without FINAL). Never overwrite that state with `done`.
    if orch is not None:
        try:
            current = orch.get_task(mission_id) or {}
            if current.get("state") == "failed":
                meta = current.get("metadata") or {}
                detail = str(
                    current.get("last_error")
                    or meta.get("terminal_reason_detail")
                    or "echec de mission"
                )[:800]
                reason_code = (
                    "iteration_limit"
                    if "iteration_limit_reached_without_final_answer" in detail
                    else str(meta.get("terminal_reason_code") or "failed")
                )
                if artifacts:
                    orch.set_task_metadata(mission_id, artifacts=list(artifacts))
                orch.set_task_metadata(
                    mission_id,
                    terminal_reason_code=reason_code,
                    terminal_reason_detail=detail,
                    terminal_at=datetime.now(timezone.utc).isoformat(),
                    completion_proven=False,
                )
                return {
                    "mission_id": mission_id,
                    "status": "failed",
                    "result": str(result),
                    "artifacts": list(artifacts),
                }
        except Exception:
            pass

    # An empty string is not a mission delivery. This closes silent incomplete
    # exits without changing non-mission callers of think_and_act_silent.
    if not str(result or "").strip():
        detail = "empty_result: mission terminee sans livrable final"
        if orch is not None:
            try:
                orch.mark_failed(mission_id, detail)
                orch.set_task_metadata(
                    mission_id,
                    terminal_reason_code="empty_result",
                    terminal_reason_detail=detail,
                    terminal_at=datetime.now(timezone.utc).isoformat(),
                    completion_proven=False,
                )
            except Exception:
                pass
        return {"mission_id": mission_id, "status": "failed", "result": "",
                "artifacts": list(artifacts)}

    if orch is not None:
        if artifacts:
            try:
                orch.set_task_metadata(mission_id, artifacts=list(artifacts))
            except Exception:
                pass
        if _recovery_mode:
            try:
                orch.set_task_metadata(
                    mission_id,
                    recovery_required=False,
                    recovery_completed=True,
                    needs_review=False,
                )
            except Exception:
                pass
        # F1.b — l'ÉTAT terminal doit dire la vérité, pas seulement le message.
        # Le truth-lock rétrogradait déjà les affirmations non prouvées DANS LE TEXTE,
        # puis jetait l'information : la mission était classée `completed` sans réserve
        # (cause AUD-014 — publication annoncée sur un chemin inexistant, état `done`
        # impeccable). On garde `done` (le travail existe et doit rester consultable),
        # mais la clôture n'est plus déclarée prouvée.
        # Pas de pré-cap : l'orchestrateur applique LE cap unique (_result_summary_cap).
        # F2 — le verdict runtime web est déjà persisté pendant le run par
        # `_set_web_runtime_verification_state` ; personne ne le lisait à la clôture.
        _web_failed = False
        _web_http_failed = False
        _meta_now: Dict[str, Any] = {}
        try:
            _meta_now = (orch.get_task(mission_id) or {}).get("metadata") or {}
            _web_failed = bool(_meta_now.get("web_runtime_failed"))
            # H5 — échec HTTP same-origin vu par N'IMPORTE quel outil navigateur.
            _web_http_failed = bool(_meta_now.get("web_http_failures"))
        except Exception:
            pass
        _overclaim = bool(proof.get("mission_truth_lock_overclaim"))
        # H4 — effets contractuels sans porteur arrivé à terme. Les missions
        # non-code n'ont ni pytest ni web : sans ce fait, `effects` deviendrait
        # une porte de sortie vers la clôture sur parole, exactement ce que tout
        # ce chantier ferme.
        _eff_unproven: list = []
        try:
            _mw = (_meta_now or {}).get("mission_workspace")
            if _mw:
                from src.utils.paths import WORKSPACE_DIR as _WS_H4
                _cj = _WS_H4 / _mw / "contract.json"
                if _cj.exists():
                    from src.subagents.mission_contract import unproven_effect_owners
                    _eff_unproven = unproven_effect_owners(
                        json.loads(_cj.read_text(encoding="utf-8")),
                        orch.get_children(mission_id) or [],
                    )
        except Exception as _e_h4:
            logger.debug("[mission {}] effets non evalues : {}", mission_id, _e_h4)
        if _eff_unproven:
            logger.warning(
                "[mission {}] effets contractuels sans porteur termine : {}",
                mission_id, ", ".join(_eff_unproven),
            )
        _code, _detail = closure_decision(
            overclaim=_overclaim, web_failed=_web_failed,
            web_http_failed=_web_http_failed,
            effects_unproven=bool(_eff_unproven),
        )
        if _code != "completed":
            logger.warning(
                "[mission {}] cloture NON prouvee ({}) : overclaim={} web_runtime_failed={}",
                mission_id, _code, _overclaim, _web_failed,
            )
            orch.set_task_metadata(
                mission_id,
                terminal_reason_code=_code,
                terminal_reason_detail=_detail,
                terminal_at=datetime.now(timezone.utc).isoformat(),
                completion_proven=False,
            )
        else:
            orch.set_task_metadata(
                mission_id,
                terminal_reason_code=_code,
                terminal_reason_detail=_detail,
                terminal_at=datetime.now(timezone.utc).isoformat(),
            )
        # H6 — le fait était calculé, journalisé… et jeté avant d'être livré.
        # L'utilisateur lit le texte, pas les métadonnées.
        result = annotate_unproven_effects(result, _eff_unproven)
        # LOT Z9 — même geste que H6, pour les FICHIERS : la mission a-t-elle
        # produit du travail qui n'est jamais sorti de son dossier ? Le fait est
        # sur le DISQUE (lot N : le périmètre se juge sur le disque, pas sur une
        # déclaration) et la métadonnée dit si `publish_mission_workspace` a
        # tourné. Mesuré : 78 missions sur 95 n'avaient jamais publié, aucune ne
        # le disait.
        try:
            if not (_meta_now or {}).get("mission_published"):
                _mw_z9 = (_meta_now or {}).get("mission_workspace")
                if _mw_z9:
                    from src.utils.paths import WORKSPACE_DIR as _WS_Z9

                    _dir_z9 = _WS_Z9 / _mw_z9
                    if _dir_z9.is_dir():
                        _fichiers_z9 = [
                            f for f in _dir_z9.rglob("*")
                            if f.is_file()
                            and not any(
                                p in {"__pycache__", ".backups", "node_modules"}
                                or p.startswith(".")
                                for p in f.parts
                            )
                            and f.name not in {"CONTRAT.md", "contract.json"}
                        ]
                        result = annotate_unpublished_deliverable(
                            result, len(_fichiers_z9), f"workspace/{_mw_z9}"
                        )
        except Exception as _exc_z9:
            logger.debug("[Z9] état de publication non annoté: {}", _exc_z9)
        # LOT Z9b — le bilan est-il celui de la mission, ou le rapport recopié
        # d'un sous-agent (voie H7) ? Indiscernable jusqu'ici, et c'est ce qui
        # rend le résumé trompeur : ses chemins sont ceux du sous-agent.
        try:
            result = annotate_worker_report_fallback(
                result, bool((_meta_now or {}).get("final_from_worker_report"))
            )
        except Exception as _exc_z9b:
            logger.debug("[Z9b] provenance du bilan non annotée: {}", _exc_z9b)
        orch.mark_done(mission_id, result_summary=str(result))
    return {"mission_id": mission_id, "status": "done", "result": str(result),
            "artifacts": list(artifacts)}
