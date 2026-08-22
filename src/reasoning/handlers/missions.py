"""Lot 3 — Outils de mission pour Lumena (catégorie `missions`, toujours-disponible).

Permet à Lumena de **se donner des missions elle-même** dans n'importe quelle
conversation : `create_mission` (crée + lance en arrière-plan via un sous-agent
« Lumena complète »), `list/status/result/cancel`.

⚠️ Aucun lien avec le CodeAgent (`src/agents/sub_agent.py`). L'exécution passe par
`src/subagents/` (manager → runner → `think_and_act_silent` isolé).

Anti-récursion = **garde de profondeur** : `create_mission` n'est autorisé que si la
profondeur courante < `LUMENA_MISSION_MAX_DEPTH` (défaut 1). La profondeur courante
est lue via `ctx.runtime_task_id` (propagé par la boucle ReAct). Chat = profondeur 0.
"""
from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime, timezone
from typing import Any, List

from loguru import logger

from .context import HandlerContext
from .contracts import HandlerResult
from .registry_v2 import HandlerDef

_H = "missions"

# A1 (run FitLog) — règle de chemins injectée par delegate_and_wait dans CHAQUE
# objectif worker d'une mission avec workspace : les workers ne doivent JAMAIS
# recopier le chemin long missions/<task_id>/ (32 hex = hallucination garantie).
_MISSION_PATHS_RULE = (
    "📍 Chemins : tu es DÉJÀ dans le dossier de la mission — utilise UNIQUEMENT des "
    "chemins RELATIFS (ex. CONTRAT.md, storage.py). Ne préfixe JAMAIS par "
    "missions/<id>/ ni workspace/ et ne recopie jamais l'identifiant de la mission."
)


_TERMINAL_REASON_LABELS = {
    "completed": "travail termine avec preuves requises",
    "deadline_expired": "echeance depassee apres la grace",
    "user_cancelled": "annulation demandee par l'utilisateur",
    "parent_cancelled": "annulation propagee par la mission parente",
    "cancelled": "annulation cooperative",
    "failed": "echec d'execution",
    "provider_error": "fournisseur LLM indisponible ou quota refuse",
    "shutdown": "arret du service",
}


def mission_terminal_facts(task: Any) -> dict:
    """Return persisted, authoritative lifecycle facts for one mission.

    The chat must never infer a cancellation reason from prose or memory. This
    helper only exposes orchestrator state and metadata written by runtime gates.
    """
    record = task if isinstance(task, dict) else {}
    meta = record.get("metadata") or {}
    state = str(record.get("state") or "unknown")
    code = str(meta.get("terminal_reason_code") or "").strip()
    if not code:
        if meta.get("deadline_expired"):
            code = "deadline_expired"
        elif state == "done":
            code = "completed"
        elif state == "failed":
            code = "failed"
        elif state == "cancelled":
            code = "cancelled"
    detail = str(meta.get("terminal_reason_detail") or "").strip()
    if not detail and code == "failed":
        detail = str(record.get("last_error") or "").strip()

    test_outcome = meta.get("last_test_outcome")
    if not isinstance(test_outcome, dict):
        test_outcome = None
    progress = meta.get("last_delegate_progress")
    if not isinstance(progress, dict):
        progress = {}
    published_files = meta.get("published_files")
    if not isinstance(published_files, list):
        published_files = []
    artifacts = meta.get("artifacts")
    if not isinstance(artifacts, list):
        artifacts = []
    # LOT N1 — les CONSTATS mesurés (sortie d'un script exécuté par le lead).
    measurements = meta.get("mission_measurements")
    if not isinstance(measurements, list):
        measurements = []
    measurements = [m for m in measurements if isinstance(m, dict) and m.get("output")]
    return {
        "measurements": measurements,
        "state": state,
        "reason_code": code or None,
        "reason": _TERMINAL_REASON_LABELS.get(code, code or "raison non enregistree"),
        "reason_detail": detail or None,
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at"),
        "deadline_ts": meta.get("deadline_ts"),
        "published": bool(meta.get("mission_published")),
        "published_workspace": meta.get("published_workspace"),
        "published_files": published_files,
        "tests_green": bool(meta.get("tests_green")),
        "last_test_outcome": test_outcome,
        "web_runtime_verified": bool(meta.get("web_runtime_verified")),
        # F2 — symétrie : l'ÉCHEC de la vérification runtime web était persisté
        # pendant le run mais absent des faits de mission. Le chat pouvait donc
        # annoncer « navigateur=prouve » et jamais l'inverse (cause AUD-013).
        "web_runtime_failed": bool(meta.get("web_runtime_failed")),
        "browser_interaction_verified": bool(meta.get("browser_interaction_verified")),
        "delegate_progress": progress,
        "artifacts": artifacts,
    }


def _mission_facts_text(task: Any, *, compact: bool = False) -> str:
    facts = mission_terminal_facts(task)
    parts = [f"cause={facts['reason_code'] or 'non_enregistree'}"]
    if facts["published_workspace"]:
        parts.append(f"publie={facts['published_workspace']}")
    outcome = facts["last_test_outcome"] or {}
    if outcome.get("is_test_cmd"):
        if outcome.get("green"):
            parts.append(f"tests=verts ({outcome.get('passed') or '?'} passed)")
        else:
            parts.append(
                f"tests=non_verts ({outcome.get('failed') or 0} failed, "
                f"{outcome.get('errors') or 0} errors)"
            )
    if facts["web_runtime_verified"] or facts["browser_interaction_verified"]:
        parts.append("navigateur=prouve")
    elif facts["web_runtime_failed"]:
        parts.append("navigateur=echec_runtime")
    progress = facts["delegate_progress"]
    if progress.get("total"):
        parts.append(f"workers={progress.get('done')}/{progress.get('total')}")
    if not compact and facts["reason_detail"]:
        parts.append(f"detail={facts['reason_detail']}")
    line = " | ".join(parts)
    # LOT N1 — le constat mesuré vient APRÈS les compteurs, sur ses propres lignes :
    # c'est du texte brut (des chiffres), il ne doit pas être aplati dans le « | ».
    if not compact:
        try:
            from src.subagents.mission_measures import format_measurements

            block = format_measurements(facts.get("measurements"))
            if block:
                line += "\nConstats mesures (sortie reelle, non reformulee) :\n" + block
        except Exception as exc:  # pragma: no cover - guidance jamais bloquante
            logger.debug("[N1] constats non joints: {}", exc)
    return line


def _max_depth() -> int:
    try:
        return max(1, min(8, int(os.getenv("LUMENA_MISSION_MAX_DEPTH", "1"))))
    except (ValueError, TypeError):
        return 1


def _core(ctx: HandlerContext) -> Any:
    return getattr(ctx, "lumena", None)


def _ensure_mission_workspace(orch: Any, lead_id: str, lead_meta: dict) -> str:
    """LOT 2.1/2.2 — dossier de mission ISOLÉ du lead : réutilise s'il existe déjà
    dans la meta, sinon l'attribue et le PERSISTE. Partagé par write_mission_contract
    ET delegate_and_wait → même dossier garanti.

    LOT 2.8 (run BudgetBuddy) : chemin COURT `missions/<task_id>` — l'ancien format
    `missions/<slug-40-chars>_<task_id>` était si long que les modèles le recopiaient
    partout (et le résolveur le re-préfixait → missions/x/missions/x). Les workers
    doivent manipuler des chemins RELATIFS au dossier (app.py, tests/test_api.py)."""
    ws = str((lead_meta or {}).get("mission_workspace") or "").strip()
    if ws or not lead_id:
        return ws
    ws = f"missions/{lead_id}"
    try:
        orch.set_task_metadata(lead_id, mission_workspace=ws)
    except Exception:
        return ""
    return ws


def _current_depth(ctx: HandlerContext) -> int:
    """Profondeur de la mission DANS laquelle on tourne (0 = chat)."""
    rt_id = getattr(ctx, "runtime_task_id", None)
    if not rt_id:
        return 0
    core = _core(ctx)
    orch = getattr(core, "task_orchestrator", None) if core else None
    if orch is None:
        return 0
    try:
        task = orch.get_task(rt_id)
    except Exception:
        return 0
    meta = (task or {}).get("metadata") or {}
    if meta.get("kind") != "mission":
        return 0
    try:
        return int(meta.get("depth") or 1)
    except (ValueError, TypeError):
        return 1


def _manager(ctx: HandlerContext):
    from src.subagents.manager import get_mission_manager
    return get_mission_manager(_core(ctx))


# ── Handlers ────────────────────────────────────────────────────────────────────

def _z32_deadline_from_duration(duree_minutes: Any) -> Optional[str]:
    """LOT Z32 phase 1 — une durée ne s'interprète pas, un horodatage si.

    Mesuré deux fois sur deux, le même jour :

        « prends 75 minutes »  → lancée 17:55, échéance posée 18:30  = 35 min
        « prends 90 minutes »  → lancée 20:48, échéance posée 20:50  = 90 SECONDES

    Le second a tué la mission « Cartophare » à 3 min 39 alors qu'elle venait de
    produire 5 tests pytest VERTS. Sa pensée disait pourtant « L'échéance est de
    90 minutes » : le fait était compris, il s'est perdu à la conversion.

    Demander un horodatage absolu à un modèle, c'est lui demander un calcul qu'il
    n'a aucune raison de réussir — il ne connaît de façon fiable ni l'heure
    courante ni le fuseau. `90` ne s'interprète pas.

    Rend un ISO NAÏF LOCAL, la convention de `mission_budget._iso` — surtout pas
    une autre, sous peine de rejouer le bug de deux heures.
    """
    try:
        minutes = float(str(duree_minutes).strip().replace(",", "."))
    except (TypeError, ValueError):
        return None
    if minutes <= 0 or minutes > 60 * 24 * 30:  # borne de garde : 30 jours
        return None
    from datetime import datetime as _dt, timedelta as _td
    return (_dt.now() + _td(minutes=minutes)).replace(microsecond=0).isoformat()


async def create_mission_handler(
    ctx: HandlerContext,
    objective: str,
    deadline: str = "",
    duree_minutes: Any = None,
) -> HandlerResult:
    core = _core(ctx)
    if core is None or getattr(core, "task_orchestrator", None) is None:
        return HandlerResult.fail("Système de missions indisponible.", handler_name="create_mission")
    if not objective or not objective.strip():
        return HandlerResult.fail("objective requis.", handler_name="create_mission")
    depth = _current_depth(ctx)
    # LOT 2.7 (run NoteFlash 2026-07-02) — REFUS DUR : une mission ne crée JAMAIS
    # une autre mission via cet outil. Le lead qui le faisait (« poupée russe »)
    # réécrivait l'objectif avec un mensonge (« Contrat déjà posé ») et se faisait
    # tuer par l'accusé de réception avant contrat/delegate/tests. Le SEUL chemin
    # pour paralléliser en mission est delegate_and_wait (qui passe par le manager
    # interne, pas par ce handler → non affecté).
    if depth >= 1:
        return HandlerResult.fail(
            "⛔ Tu ES déjà une mission — ne crée pas de mission-poupée-russe. "
            "Fais le travail ICI : pour du code multi-fichiers, pose le contrat via "
            "write_mission_contract puis délègue tes workers via delegate_and_wait ; "
            "sinon fais-le directement avec tes outils.",
            handler_name="create_mission",
        )
    # ── LOT Z32 phase 1 — la durée relative prime sur l'horodatage calculé ─────
    _z32_from_duration = _z32_deadline_from_duration(duree_minutes)
    if _z32_from_duration:
        deadline = _z32_from_duration

    # ── LOT Z32 phases 0 & 3 — voir l'échéance, refuser l'impossible ──────────
    # Phase 0 : personne ne voyait le problème. Le log disait « échéance :
    # 2026-08-19T20:50:00 » — exact, et parfaitement inutile pour repérer qu'il
    # restait 90 secondes. On journalise le DELTA, la seule grandeur qui parle.
    _z32_delta_min = None
    if deadline:
        try:
            from datetime import datetime as _dtz
            from src.subagents.mission_budget import normalize_deadline as _nd
            _norm = _nd(deadline)
            if _norm:
                _z32_delta_min = (
                    _dtz.fromisoformat(_norm) - _dtz.now()
                ).total_seconds() / 60.0
                logger.info(
                    "[Z32] échéance reçue={!r} → interprétée={} → {:+.1f} min "
                    "(source={})",
                    deadline, _norm, _z32_delta_min,
                    "duree_minutes" if _z32_from_duration else "horodatage",
                )
        except Exception as _z32_exc:
            logger.debug("[Z32] lecture échéance impossible: {}", _z32_exc)

    # Phase 3 : une échéance DÉJÀ PASSÉE n'est pas un choix, c'est une faute de
    # frappe — le corpus en contient une (−227 min à la création). On refuse,
    # avec de quoi corriger. Une échéance simplement courte passe : décider à la
    # place de l'utilisateur de ce qui est « trop court » ne nous revient pas.
    if _z32_delta_min is not None and _z32_delta_min <= 0:
        return HandlerResult.fail(
            f"⛔ Échéance déjà expirée ({deadline!r} → {_z32_delta_min:.0f} min). "
            "La mission mourrait avant d'avoir commencé. Utilise "
            "`duree_minutes=<nombre>` — c'est déterministe, aucun calcul d'heure "
            "à faire.",
            handler_name="create_mission",
        )

    meta: dict = {"depth": depth + 1}
    try:
        from src.runtime.context import get_current_runtime_context
        _runtime_ctx = get_current_runtime_context()
        if _runtime_ctx is not None and getattr(_runtime_ctx, "channel", "") == "voice":
            meta["source_channel"] = "voice"
            meta["source_conversation_id"] = getattr(_runtime_ctx, "conversation_id", None)
    except Exception:
        pass
    try:
        mid = _manager(ctx).create_and_launch(
            objective.strip(),
            deadline=(deadline or None),
            metadata=meta,
        )
    except Exception as e:
        logger.warning("[create_mission] échec: {}", e)
        return HandlerResult.fail(f"Erreur création mission: {e}", handler_name="create_mission")
    # LOT P2a (run HuffPack v2, 2026-08-14) — le dossier de mission n'était attribué
    # que par `write_mission_contract` et `delegate_and_wait`. Une mission qui
    # travaille SEULE n'en avait donc aucun : elle écrivait là où l'objectif
    # pointait — c'est-à-dire dans le livrable de production. HuffPack v1 (publié,
    # 12/12 verts) a été écrasé DEUX FOIS de cette façon en une heure, malgré une
    # consigne explicite « travaille dans une copie de mission ».
    #
    # Le reste de la chaîne existe déjà et s'applique tout seul :
    # `_mission_workspace_meta()` lit cette clé, `_prepare_handler_context`
    # l'injecte à chaque tour, `_resolve_execution_root` en fait la racine.
    # INERTE par construction tant que le dossier n'existe pas physiquement —
    # `_resolve_execution_root` exige `candidate.is_dir()` et retombe sinon sur
    # la résolution actuelle. Les missions d'effets (mail, PDF, recherche) ne
    # créent pas ce dossier : elles ne changent donc pas de comportement.
    try:
        _orch = getattr(core, "task_orchestrator", None)
        if _orch is not None and mid:
            _orch.set_task_metadata(mid, mission_workspace=f"missions/{mid}")
    except Exception as _ws_exc:
        logger.debug("[P2a] dossier de mission non attribué: {}", _ws_exc)
    return HandlerResult.ok(
        f"✅ Mission lancée en arrière-plan (id: {mid}). Annonce à l'utilisateur que c'est lancé "
        f"et TERMINE ton tour (ACTION: FINAL). NE lance PAS mission_status/mission_result "
        f"maintenant — la mission tourne seule ; tu la suivras QUAND l'utilisateur le demandera.",
        handler_name="create_mission",
    )


def _is_worker_task(m: Any) -> bool:
    """LOT K — une tâche de WORKER n'est pas une mission : c'est une sous-tâche
    interne, déléguée par un lead. Elle porte `metadata.parent_id` (même critère
    que `_is_worker_run`, lot H4-b) ; une mission de l'utilisateur n'en a jamais.
    Au run MemoNest, 184 tâches worker noyaient une vingtaine de vraies missions."""
    if not isinstance(m, dict):
        return False
    return bool((m.get("metadata") or {}).get("parent_id"))


def _mission_created_ts(m: Any) -> float:
    """LOT K — instant de création, en secondes, pour TRIER. `created_at` est
    stocké en ISO-8601 (`2026-08-13T18:24:24.728437+00:00`) mais n'était ni trié
    ni affiché. 0.0 si absent/illisible : la tâche part en fin de liste plutôt
    que de faire échouer le tri. Pur."""
    if not isinstance(m, dict):
        return 0.0
    raw = str(m.get("created_at") or "").strip()
    if not raw:
        return 0.0
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return 0.0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _mission_when_text(m: Any) -> str:
    """LOT K — date lisible (`13/08 18:24`) affichée sur CHAQUE ligne. Sans elle,
    le modèle ne peut PAS répondre « quelle est la dernière mission ? » : il devine
    (run du 13/08 — il a répondu EcoPilot alors que MemoNest venait de finir). "" si
    inconnue. Pur."""
    ts = _mission_created_ts(m)
    if ts <= 0:
        return ""
    try:
        return datetime.fromtimestamp(ts).strftime("%d/%m %H:%M")
    except (ValueError, OSError, OverflowError):
        return ""


def select_missions_for_listing(items: Any, limit: int = 15) -> tuple:
    """LOT K — rend (missions_à_afficher, nb_masquées) : workers exclus, PLUS
    RÉCENTE EN TÊTE, coupé à `limit`.

    La borne n'est pas cosmétique : la liste brute pesait 16 545 caractères et la
    compaction d'observation la réduisait à 831 — la mission la plus récente était
    donc JETÉE avant que le modèle la voie. On coupe nous-mêmes, par le bon bout.
    Pur, ne lève jamais."""
    rows = [m for m in (items or []) if isinstance(m, dict) and not _is_worker_task(m)]
    rows.sort(key=_mission_created_ts, reverse=True)
    if limit and limit > 0 and len(rows) > limit:
        return rows[:limit], len(rows) - limit
    return rows, 0


async def list_missions_handler(ctx: HandlerContext) -> HandlerResult:
    core = _core(ctx)
    if core is None:
        return HandlerResult.fail("Système de missions indisponible.", handler_name="list_missions")
    try:
        items = _manager(ctx).list_missions()
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}", handler_name="list_missions")
    if not items:
        return HandlerResult.ok("Aucune mission.", handler_name="list_missions")
    rows, hidden = select_missions_for_listing(items)
    if not rows:
        return HandlerResult.ok("Aucune mission.", handler_name="list_missions")
    lines: List[str] = []
    for m in rows:
        meta = m.get("metadata") or {}
        obj = meta.get("objective") or m.get("message_preview") or ""
        suffix = ""
        if m.get("state") in _TERMINAL:
            suffix = " | " + _mission_facts_text(m, compact=True)
        when = _mission_when_text(m)
        when_txt = f"({when}) " if when else ""
        lines.append(
            f"- {when_txt}{m.get('task_id')} · [{m.get('state')}] {str(obj)[:80]}{suffix}"
        )
    head = "Missions (de la plus RÉCENTE à la plus ancienne) :"
    tail = f"\n… et {hidden} mission(s) plus ancienne(s)." if hidden else ""
    return HandlerResult.ok(head + "\n" + "\n".join(lines) + tail, handler_name="list_missions")


async def mission_status_handler(ctx: HandlerContext, mission_id: str) -> HandlerResult:
    core = _core(ctx)
    if core is None:
        return HandlerResult.fail("Système de missions indisponible.", handler_name="mission_status")
    task = _manager(ctx).get_mission(mission_id)
    if not task:
        return HandlerResult.fail(f"Mission {mission_id!r} inconnue.", handler_name="mission_status")
    meta = task.get("metadata") or {}
    extra = " · à relire (needs_review)" if meta.get("needs_review") else ""
    state = task.get("state")
    if state in _ACTIVE:
        return HandlerResult.ok(
            f"Mission {mission_id} : EN COURS (état technique={state}){extra}. "
            f"Elle travaille encore — NE la relance PAS et ne crée AUCUNE mission de "
            f"« finalisation ». Dis simplement à l'utilisateur que ça tourne ; il pourra "
            f"redemander dans quelques minutes.",
            handler_name="mission_status",
        )
    # Lot 5.7.4/E — statut FIDÈLE : si la mission a été finalisée/coupée à l'échéance,
    # on expose le partiel réel (dernier delegate report) au lieu d'un faux décompte.
    _prog = meta.get("last_delegate_progress") or {}
    # 5.7.4b — si un artefact cible a été ÉCRIT sur disque, le livrable existe : on
    # l'annonce comme PRODUIT (au temps imparti) au lieu de « PARTIEL » (flag posé au
    # steer, avant l'écriture) → plus de statut faussement pessimiste sur un fichier complet.
    if meta.get("deadline_artifact_written"):
        _partial = " — livrable PRODUIT ✅ (au temps imparti)"
    elif meta.get("partial_due_to_deadline"):
        _partial = " — livrable PARTIEL (échéance atteinte)"
    else:
        _partial = ""
    _prog_txt = f" · délégation {_prog.get('done')}/{_prog.get('total')} terminée(s)" if _prog.get("total") else ""
    return HandlerResult.ok(
        f"Mission {mission_id} : état={state}{extra}{_partial}{_prog_txt}. "
        f"Faits autoritatifs: {_mission_facts_text(task)}",
        handler_name="mission_status",
    )


async def mission_result_handler(ctx: HandlerContext, mission_id: str) -> HandlerResult:
    core = _core(ctx)
    if core is None:
        return HandlerResult.fail("Système de missions indisponible.", handler_name="mission_result")
    task = _manager(ctx).get_mission(mission_id)
    if not task:
        return HandlerResult.fail(f"Mission {mission_id!r} inconnue.", handler_name="mission_result")
    state = task.get("state")
    if state in _ACTIVE:
        return HandlerResult.ok(
            f"Mission {mission_id} pas encore terminée — EN COURS (état technique={state}). "
            f"NE la relance PAS, ne crée pas de mission de finalisation : dis à l'utilisateur "
            f"que ça tourne, le résultat arrive.",
            handler_name="mission_result",
        )
    if state != "done":  # failed / cancelled
        _meta = task.get("metadata") or {}
        _prog = _meta.get("last_delegate_progress") or {}
        _extra = ""
        if _prog.get("total"):
            _fail = f", {_prog.get('failed')} en échec" if _prog.get("failed") else ""
            _extra = f" Sous-tâches : {_prog.get('done')}/{_prog.get('total')} terminée(s){_fail}."
        # 5.7.4b — artefact écrit : le livrable existe sur disque même si l'état technique
        # n'est pas « done » (cas limite). On l'annonce PRODUIT, sans le qualifier de « sans
        # résultat » qui serait faux.
        if _meta.get("deadline_artifact_written"):
            return HandlerResult.ok(
                f"Mission {mission_id} : livrable PRODUIT ✅ au temps imparti (état technique={state})."
                f"{_extra} Le fichier cible a été écrit. "
                f"Faits autoritatifs: {_mission_facts_text(task)}. "
                "Annonce-les honnêtement, sans inventer la cause.",
                handler_name="mission_result",
            )
        if _meta.get("mission_published"):
            return HandlerResult.ok(
                f"Mission {mission_id} : livrable publié, mais clôture non certifiée "
                f"(état technique={state}).{_extra} "
                f"Faits autoritatifs: {_mission_facts_text(task)}. "
                "Ne transforme pas la publication seule en succès complet.",
                handler_name="mission_result",
            )
        _part = " (livrable partiel à l'échéance)" if _meta.get("partial_due_to_deadline") else ""
        return HandlerResult.ok(
            f"Mission {mission_id} terminée sans résultat complet (état={state}){_part}.{_extra} "
            f"Faits autoritatifs: {_mission_facts_text(task)}. "
            f"Annonce honnêtement l'état RÉEL ci-dessus — n'invente ni cause ni décompte.",
            handler_name="mission_result",
        )
    summary = task.get("result_summary") or "(pas de résumé)"
    artifacts = (task.get("metadata") or {}).get("artifacts") or []
    art_txt = f"\n📦 {len(artifacts)} livrable(s) : {', '.join(artifacts[:10])}" if artifacts else ""
    return HandlerResult.ok(
        f"Résultat de {mission_id} :\n{summary}{art_txt}\n"
        f"Faits autoritatifs: {_mission_facts_text(task)}",
        handler_name="mission_result",
    )


async def cancel_mission_handler(ctx: HandlerContext, mission_id: str) -> HandlerResult:
    core = _core(ctx)
    if core is None:
        return HandlerResult.fail("Système de missions indisponible.", handler_name="cancel_mission")
    try:
        orch = getattr(core, "task_orchestrator", None)
        if orch is not None:
            orch.set_task_metadata(
                mission_id,
                terminal_reason_code="user_cancelled",
                terminal_reason_detail="annulation demandee depuis l'outil cancel_mission",
            )
        out = _manager(ctx).cancel_mission(mission_id)
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}", handler_name="cancel_mission")
    if out.get("success"):
        return HandlerResult.ok(
            f"Mission {mission_id} annulée (elle s'arrête au prochain checkpoint, jamais en plein milieu).",
            handler_name="cancel_mission",
        )
    return HandlerResult.fail(out.get("message", "échec annulation"), handler_name="cancel_mission")


_TERMINAL = {"done", "failed", "cancelled"}


def _mission_workspace_idle_s(ctx: Any, mission_ws: str) -> Optional[float]:
    """H2 — secondes depuis la dernière écriture dans le dossier de mission.

    Signal du TRAVAIL RÉEL des workers : `updated_at` d'une tâche ne bouge qu'aux
    transitions d'état, alors qu'un worker occupé écrit des fichiers en continu.
    Le run SuiviDepenses a montré le coût de cette confusion : trois workers
    actifs déclarés « sans progrès », puis doublés par leur propre lead.

    Retourne `None` si le dossier est introuvable ou vide (⇒ pas de signal, on
    retombe sur l'ancien comportement). Ne lève jamais : un signal manquant ne
    doit pas casser une délégation.

    Ignore les dossiers de cache (`__pycache__`, `.pytest_cache`, `.backups`) :
    leur mtime bouge sans qu'aucun worker ne produise de livrable.
    """
    if not mission_ws:
        return None
    try:
        import time as _t
        from pathlib import Path as _P
        _fg = getattr(ctx, "file_guardrails", None)
        if _fg is not None:
            root = _P(_fg._workspace_root()) / mission_ws
        else:
            from src.utils.paths import WORKSPACE_DIR as _WS
            root = _P(_WS) / mission_ws
        if not root.is_dir():
            return None
        _skip = ("__pycache__", ".pytest_cache", ".backups", ".lumena_backups", ".git")
        newest = 0.0
        for p in root.rglob("*"):
            if any(s in p.parts for s in _skip):
                continue
            try:
                if p.is_file():
                    newest = max(newest, p.stat().st_mtime)
            except OSError:
                continue
        if newest <= 0:
            return None
        return max(0.0, _t.time() - newest)
    except Exception as exc:
        logger.debug("[H2] activité disque du dossier mission illisible: {}", exc)
        return None


def _stall_threshold_s() -> float:
    """Ancienneté au-delà de laquelle un worker non terminal est dit « sans progrès »."""
    try:
        return max(30.0, float(os.getenv("LUMENA_WORKER_STALL_S", "300")))
    except (TypeError, ValueError):
        return 300.0


def classify_pending_workers(
    records: Any,
    now_iso: str,
    threshold_s: float,
    *,
    workspace_idle_s: Optional[float] = None,
) -> List[dict]:
    """F3.b (M9) — décrit l'état RÉEL des workers non terminaux. Fonction pure.

    ⚠️ H2 (run SuiviDepenses, 2026-08-12) — CORRECTION D'UN FAUX POSITIF DE MA PART.
    La v1 ne regardait que `updated_at` de la tâche. Or ce champ ne bouge qu'aux
    TRANSITIONS d'état : un worker dont le CodeAgent tourne 719 secondes n'écrit
    rien dans sa tâche. Les 3 workers ont donc été déclarés « sans progrès » alors
    qu'ils codaient — `w_frontend` a terminé 3 minutes plus tard. Le lead a cru
    ses workers bloqués, a repris leur périmètre, et a écrit `app.py` pendant que
    le CodeAgent de `w_backend` l'écrivait aussi.

    `workspace_idle_s` (optionnel) apporte le signal du TRAVAIL RÉEL : secondes
    écoulées depuis la dernière modification du dossier de mission. Si le dossier
    bouge, quelqu'un travaille — et comme on ne peut pas savoir QUI, on n'accuse
    personne. Non fourni ⇒ comportement v1 (rétrocompatible).

    À l'expiration de l'attente du lead, tous les enfants restants étaient rendus
    d'un même bloc « les workers continuent ». L'UI n'affichait qu'« en cours », y
    compris quand le vrai état était « en file, jamais démarré » ou « plus aucune
    progression depuis 20 minutes ».

    On ne ment JAMAIS sur l'état : un worker qui progresse encore n'est pas déclaré
    mort. Trois situations distinctes, toutes fondées sur des faits de
    l'orchestrateur (`state`, `updated_at`) et du disque :
      • `queued`       → jamais démarré (pas de créneau) ;
      • `stalled`      → non terminal et aucune mise à jour depuis `threshold_s` ;
      • `working`      → non terminal mais progression récente.

    Aucun nouvel état de tâche n'est introduit (la machine à états reste
    queued/running/waiting_io/checkpointed/done/failed/cancelled) : c'est un
    CONSTAT joint, pas une transition. Défensif : un timestamp illisible fait
    retomber sur `working` (on préfère taire un doute qu'accuser à tort).
    """
    out: List[dict] = []
    try:
        ref = datetime.fromisoformat(str(now_iso))
    except Exception:
        return out
    # H2 — le dossier de mission a bougé récemment ⇒ du travail est en cours.
    # On ne sait pas QUI code, donc on n'accuse aucun worker de stagner.
    _workspace_active = (
        workspace_idle_s is not None and workspace_idle_s < threshold_s
    )
    for rec in (records or []):
        if not isinstance(rec, dict):
            continue
        state = str(rec.get("state") or "")
        if state in _TERMINAL:
            continue
        age = None
        try:
            stamp = str(rec.get("updated_at") or rec.get("created_at") or "")
            age = (ref - datetime.fromisoformat(stamp)).total_seconds()
        except Exception:
            age = None
        if state == "queued":
            kind = "queued"
        elif _workspace_active:
            # H2 — le disque bouge : quelqu'un code. On ne déclare personne bloqué.
            kind = "working"
        elif age is not None and age >= threshold_s:
            kind = "stalled"
        else:
            kind = "working"
        out.append({
            "task_id": str(rec.get("task_id") or ""),
            "state": state,
            "kind": kind,
            "idle_s": int(age) if age is not None else None,
        })
    return out
# États « en vol » : la mission tourne (ou vient de sauvegarder un checkpoint, ou sera
# reprise au boot). checkpointed N'EST PAS un blocage → le chat ne doit JAMAIS relancer
# une mission de « finalisation » dessus (bug observé : doublon créé sur checkpointed).
_ACTIVE = {"queued", "running", "waiting_io", "checkpointed"}


def _fusion_excerpt() -> int:
    """Longueur de l'aperçu de CHAQUE worker injecté dans l'observation de fusion rendue
    au lead. 300 était trop court → le lead n'avait pas le contenu et fouillait le disque.
    Configurable via LUMENA_MISSION_FUSION_EXCERPT_CHARS (défaut 1500). Le contenu entier
    reste accessible via mission_result(<id>)."""
    try:
        return max(200, int(os.getenv("LUMENA_MISSION_FUSION_EXCERPT_CHARS", "1500") or 1500))
    except (ValueError, TypeError):
        return 1500


def _objective_text(o: Any) -> str:
    """Extrait un objectif texte propre d'un item (str OU dict {objective/description/...})."""
    if isinstance(o, dict):
        txt = str(o.get("objective") or o.get("description") or o.get("task") or "").strip()
        ctx = o.get("context")
        if ctx:
            txt = f"{txt}\n(Contexte : {ctx})" if txt else str(ctx)
        return txt.strip()
    return str(o).strip()


def _coerce_objectives_list(objectives: Any) -> List[Any]:
    """Ramène l'entrée à une liste d'items bruts (str/dict), en parsant le cas où le
    LLM passe la liste sous forme de CHAÎNE (repr/JSON). Aucun aplatissement → préserve
    la structure `{objective, allowed_files}` pour LOT 2.3."""
    if isinstance(objectives, str):
        s = objectives.strip()
        parsed = None
        if s.startswith("[") and s.endswith("]"):
            import ast
            try:
                parsed = ast.literal_eval(s)
            except (ValueError, SyntaxError):
                parsed = None
        objectives = parsed if isinstance(parsed, (list, tuple)) else [objectives]
    if not isinstance(objectives, (list, tuple)):
        return []
    return list(objectives)


def _objective_allowed_files(o: Any) -> List[str]:
    """LOT 2.3 — fichiers que ce worker a le droit d'écrire, extraits d'un item dict
    (`allowed_files`/`files`/`owns`). Chaîne simple → [] → worker non restreint."""
    if isinstance(o, dict):
        v = o.get("allowed_files") or o.get("files") or o.get("owns")
        if isinstance(v, (list, tuple)):
            return [str(x).strip() for x in v if str(x).strip()]
    return []


def _normalize_objectives(objectives: Any) -> List[str]:
    """Accepte une liste de chaînes OU de dicts (le LLM passe souvent
    `[{'objective': ...}]`), ou une chaîne (éventuellement une liste sérialisée) → liste propre."""
    return [t for t in (_objective_text(o) for o in _coerce_objectives_list(objectives)) if t]


def _normalize_objectives_struct(objectives: Any) -> List[dict]:
    """LOT 2.3 — variante STRUCTURÉE : préserve `allowed_files` par objectif.
    Retourne `[{text, allowed_files}]` (text non vide filtré)."""
    out: List[dict] = []
    for o in _coerce_objectives_list(objectives):
        text = _objective_text(o)
        if not text:
            continue
        out.append({"text": text, "allowed_files": _objective_allowed_files(o)})
    return out


def _contract_delegation_specs(contract_data: Any) -> tuple[List[dict], str]:
    """Build the canonical worker plan and stable identity for a contract."""
    if not isinstance(contract_data, dict):
        return [], ""
    try:
        import hashlib
        import json
        from src.subagents.mission_contract import (
            effects_map, owners_map, worker_objectives,
        )

        # H4 (TEST RÉEL veille_python_313) — `owners_map` ne connaît que les
        # FICHIERS. Sur un contrat d'effets purs : owners=[] ≠ 2 objectifs → specs
        # vides, fingerprint vide → `delegation_owner` JAMAIS posé → la clôture H4.b
        # aurait déclaré tous les effets non prouvés, et H3 n'aurait rien protégé.
        # Même union, et surtout MÊME ORDRE, que `worker_objectives`.
        _file_owners = list(owners_map(contract_data))
        owners = _file_owners + [
            o for o in effects_map(contract_data) if o not in _file_owners
        ]
        canonical = worker_objectives(contract_data)
        if not owners or len(owners) != len(canonical):
            return [], ""
        specs = [
            {
                "text": str(item.get("objective") or "").strip(),
                "allowed_files": list(item.get("allowed_files") or []),
                "owner": str(owner),
            }
            for owner, item in zip(owners, canonical)
        ]
        raw = json.dumps(
            contract_data, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        return specs, hashlib.sha256(raw.encode("utf-8")).hexdigest()
    except Exception:
        return [], ""


def _worker_routing_objective(
    text: str, contract_data: Any, allowed_files: Any,
) -> str:
    """Return the worker business objective without injected protocol prose."""
    if isinstance(contract_data, dict):
        wanted = {
            str(path).replace("\\", "/").strip()
            for path in (allowed_files or [])
            if str(path).strip()
        }
        lines: List[str] = []
        for entry in contract_data.get("files") or []:
            if not isinstance(entry, dict):
                continue
            path = str(entry.get("path") or "").replace("\\", "/").strip()
            if wanted and path not in wanted:
                continue
            desc = str(entry.get("desc") or entry.get("description") or "").strip()
            exports = entry.get("api") or entry.get("exports") or entry.get("signatures") or []
            if isinstance(exports, str):
                exports = [exports]
            details = " ".join(str(value).strip() for value in exports if str(value).strip())
            semantic = " ".join(value for value in (path, desc, details) if value)
            if semantic:
                lines.append(semantic)
        if lines:
            return "\n".join(lines)
    return str(text or "").strip()


# Priorité 1 (2026-06-30) — steering anti-contention navigateur. En délégation
# PARALLÈLE (≥2 workers), tous partagent le MÊME navigateur Playwright (singleton,
# onglet actif partagé). Pour LIRE du web, `web_fetch`/`web_search_brave` (HTTP, sans
# état) tournent en parallèle sans se gêner ; `browser_*` est sérialisé. On guide donc
# les workers vers le HTTP par défaut, navigateur en dernier recours. Pur/testable.
_PARALLEL_BROWSER_STEER = (
    "\n\n⚙️ Tu travailles EN PARALLÈLE avec d'autres sous-agents sur un navigateur "
    "PARTAGÉ. Pour LIRE du contenu web, utilise `web_fetch` ou `web_search_brave` "
    "(HTTP direct, sans état partagé — vous tournez en parallèle sans vous gêner). "
    "N'utilise `browser_navigate`/`browser_get_content` (navigateur partagé, sérialisé) "
    "QU'en dernier recours : page nécessitant un login ou un rendu JavaScript."
)


def apply_parallel_browser_steering(objective: str, *, worker_count: int) -> str:
    """Ajoute le steering anti-contention navigateur à un objectif de worker, UNIQUEMENT
    en délégation parallèle (≥2 workers). Sinon renvoie l'objectif inchangé. Pur/testable."""
    if worker_count < 2 or not objective:
        return objective
    return objective + _PARALLEL_BROWSER_STEER


async def delegate_and_wait_handler(
    ctx: HandlerContext, objectives: Any, timeout: float = 1200.0
) -> HandlerResult:
    """Lead : crée N sous-missions (workers), attend leur fin (bornée) puis fusionne.

    S'utilise DANS une mission (pas au chat → on ne bloque jamais le tour de chat).
    Nécessite `LUMENA_MISSION_MAX_DEPTH≥2` (sinon refus par la garde de profondeur).
    """
    core = _core(ctx)
    if core is None or getattr(core, "task_orchestrator", None) is None:
        return HandlerResult.fail("Système de missions indisponible.", handler_name="delegate_and_wait")
    # LOT 2.3 — objectifs STRUCTURÉS (préservent `allowed_files` par worker) au lieu
    # d'un aplatissement précoce en List[str].
    objs = _normalize_objectives_struct(objectives)
    if not objs:
        return HandlerResult.fail("objectives requis (liste non vide).", handler_name="delegate_and_wait")

    depth = _current_depth(ctx)
    if depth == 0:
        return HandlerResult.fail(
            "delegate_and_wait s'utilise DANS une mission. Au chat, utilise create_mission "
            "(elle tourne en arrière-plan sans bloquer la conversation).",
            handler_name="delegate_and_wait",
        )
    max_depth = _max_depth()
    if depth >= max_depth:
        return HandlerResult.fail(
            f"⛔ Profondeur {depth} : impossible de déléguer plus loin (max {max_depth}). "
            f"Fais le travail directement.",
            handler_name="delegate_and_wait",
        )

    lead_id = getattr(ctx, "runtime_task_id", None)
    orch = core.task_orchestrator
    # Lot 5.7.3b — propagation du budget temporel : les workers héritent de l'échéance
    # du lead (même budget absolu) → ils reçoivent le préambule calme (5.7.2) et savent
    # qu'ils doivent finir avant. Sans échéance lead → rien (comportement identique).
    _lead_meta = (orch.get_task(lead_id) or {}).get("metadata") or {} if lead_id else {}
    _lead_deadline = _lead_meta.get("deadline")
    _lead_deadline_ts = _lead_meta.get("deadline_ts")

    # LOT 2.1 — dossier de mission ISOLÉ partagé par TOUS les workers de ce lead :
    # évite les collisions inter-workers (style.css vs styles.css, app.js réécrit 3×)
    # ET un workspace sale d'un run précédent. Concurrence-safe (donnée de meta, pas
    # d'état de classe). Posé sur le lead (pour qu'il intègre au même endroit en 2.5)
    # puis hérité par les enfants — même patron que deadline_ts.
    _mission_ws = _ensure_mission_workspace(orch, lead_id, _lead_meta)

    # LOT 2.2 — si un CONTRAT existe dans le dossier de mission (posé par
    # write_mission_contract), chaque worker reçoit la consigne contrat en préfixe :
    # lire CONTRAT.md, remplir les stubs SANS toucher aux signatures. Pas de contrat
    # → objectifs inchangés (missions non-code strictement identiques).
    _contract_preamble = ""
    _contract_data = None  # 2.13.C — contenu de contract.json (spec par owner)
    try:
        if _mission_ws:
            _fg = getattr(ctx, "file_guardrails", None)
            if _fg is not None:
                _mission_dir = _fg._workspace_root() / _mission_ws
            else:
                from src.utils.paths import WORKSPACE_DIR as _WS_DIR
                _mission_dir = _WS_DIR / _mission_ws
            from src.subagents.mission_contract import CONTRACT_JSON, WORKER_CONTRACT_PREAMBLE
            if (_mission_dir / CONTRACT_JSON).is_file():
                _contract_preamble = WORKER_CONTRACT_PREAMBLE + "\n\n"
                # 2.13.C (run bibliapi) — charger le contrat UNE fois : la spec
                # exacte (exports/imports par owner) sera injectée par worker,
                # hors de portée de la réécriture des objectifs par le lead.
                try:
                    import json as _json_213
                    _contract_data = _json_213.loads(
                        (_mission_dir / CONTRACT_JSON).read_text(encoding="utf-8")
                    )
                except Exception:
                    _contract_data = None
    except Exception:
        _contract_preamble = ""
        _contract_data = None

    # In a contract mission, contract.json owns workers and write scopes. This
    # prevents a prose rewrite by the lead from silently dropping allowed_files.
    _contract_specs, _contract_fingerprint = _contract_delegation_specs(_contract_data)
    if _contract_specs:
        objs = _contract_specs
    try:
        mgr = _manager(ctx)
        child_ids: List[str] = []
        existing_by_owner = {}
        if lead_id and _contract_fingerprint:
            for child in orch.get_children(lead_id):
                child_meta = child.get("metadata") or {}
                if child_meta.get("delegation_contract_fingerprint") != _contract_fingerprint:
                    continue
                owner = str(child_meta.get("delegation_owner") or "").strip()
                if owner and owner not in existing_by_owner:
                    existing_by_owner[owner] = child
        _wc = len(objs)
        for st in objs:
            owner = str(st.get("owner") or "").strip()
            existing = existing_by_owner.get(owner) if owner else None
            if existing is not None:
                child_ids.append(str(existing.get("task_id")))
                continue
            _txt = st["text"]
            _routing_objective = _worker_routing_objective(
                _txt, _contract_data, st.get("allowed_files") or [],
            )
            # LOT 2.2 — consigne contrat en préfixe (sauf si l'objectif la porte déjà,
            # cas des objectifs générés par write_mission_contract).
            if _contract_preamble and "CONTRAT DE MISSION" not in _txt:
                _txt = _contract_preamble + _txt
            # A1 (run FitLog) — règle chemins RELATIFS injectée DÉTERMINISTIQUEMENT
            # par enfant : le lead paraphrase les objectifs et y remet le chemin long
            # missions/<id>/ (id de 32 hex → hallucination afee→af1e). Idempotent.
            if _mission_ws and "chemins RELATIFS" not in _txt:
                _txt = _txt + "\n\n" + _MISSION_PATHS_RULE
            # LOT A (run PostuloTrack) — le lead RÉÉCRIT souvent l'objectif de zéro →
            # la discipline de codage + le steer de délégation CodeAgent (qui ne vivaient
            # que dans worker_objectives) se perdaient. On les FORCE-injecte ici, au point
            # d'injection déterministe par worker : idempotent (skip si déjà présent) et
            # worker de CODE seulement. Hors de portée de la réécriture du lead.
            try:
                from src.subagents.mission_contract import inject_worker_discipline
                _txt = inject_worker_discipline(_txt, st.get("allowed_files") or [])
            except Exception:
                pass
            # 2.13.C (run bibliapi) — le CONTRAT est la SEULE source de spec :
            # exports/imports EXACTS du contrat injectés par worker (matching
            # déterministe par allowed_files), au même point que le LOT A.
            # Idempotent (marqueur) ; pas de contrat → strictement inchangé.
            if _contract_data is not None:
                try:
                    from src.subagents.mission_contract import (
                        WORKER_SPEC_MARK, worker_spec_block,
                    )
                    if WORKER_SPEC_MARK not in _txt:
                        _spec_213 = worker_spec_block(
                            _contract_data, st.get("allowed_files") or []
                        )
                        if _spec_213:
                            _txt = _txt + "\n\n" + _spec_213
                except Exception:
                    pass
            # Priorité 1 — en parallèle (≥2 workers), guide vers le HTTP stateless
            # (web_fetch/web_search_brave) plutôt que le navigateur partagé. Le steering
            # ne s'applique qu'au TEXTE (jamais aux allowed_files).
            obj = apply_parallel_browser_steering(_txt, worker_count=_wc)
            meta = {"depth": depth + 1}
            if _routing_objective:
                meta["routing_objective"] = _routing_objective[:4000]
            if lead_id:
                meta["parent_id"] = lead_id
            if _lead_deadline_ts:
                meta["deadline_ts"] = _lead_deadline_ts
                if _lead_deadline:
                    meta["deadline"] = _lead_deadline
            if _mission_ws:
                meta["mission_workspace"] = _mission_ws  # LOT 2.1 — dossier partagé hérité
            if st.get("allowed_files"):
                meta["allowed_files"] = list(st["allowed_files"])  # LOT 2.3 — périmètre worker
            if _contract_fingerprint and owner:
                meta["delegation_contract_fingerprint"] = _contract_fingerprint
                meta["delegation_owner"] = owner
            cid = mgr.create_mission(obj, metadata=meta)
            mgr.launch(cid, obj)
            child_ids.append(cid)
    except Exception as e:
        logger.warning("[delegate_and_wait] création échec: {}", e)
        return HandlerResult.fail(f"Erreur création des sous-missions: {e}", handler_name="delegate_and_wait")

    # Attente BORNÉE (poll → rend la main : les workers tournent dans leur propre pool).
    try:
        timeout = max(1.0, float(timeout))
    except (ValueError, TypeError):
        timeout = 1200.0
    # B0.4c (run PlantCare) — le lead avait choisi timeout=300 s pour 4 workers
    # de CODE → retour partiel 2/4 → course lead/workers (le lead éditait pendant
    # qu'ils écrivaient). En mission sous CONTRAT, plancher à 600 s.
    if _contract_preamble and timeout < 600.0:
        timeout = 600.0
    # 2.6.4 (run MiniQuiz §5) — sous CONTRAT, attendre les workers jusqu'à
    # l'échéance du lead (marge 120 s) plutôt que rendre un PARTIEL : le lead a
    # reçu « 1/3 terminée, les workers continuent », a publié à 01:02 pendant que
    # w_frontend/w_tests mutaient les fichiers jusqu'à 01:08 → livrable divergent.
    # Les workers héritent de deadline_ts : ils se terminent AVANT cette borne.
    # H1 — `deadline_ts` est une chaîne ISO, pas un epoch : l'ancien
    # `float(_lead_deadline_ts)` levait ValueError, avalée par l'except, donc ce
    # relèvement n'a JAMAIS tiré. Le lead expirait au plancher de 600 s même avec
    # 29 min de budget, puis reprenait le travail de workers encore actifs (run
    # SuiviDepenses du 2026-08-12 → course sur app.py).
    if _contract_preamble and _lead_deadline_ts:
        try:
            from src.subagents.mission_budget import seconds_until_deadline
            _left_264 = seconds_until_deadline(_lead_deadline_ts)
            if _left_264 is not None:
                _remaining_264 = _left_264 - 120.0
                if _remaining_264 > timeout:
                    timeout = _remaining_264
                    logger.info(
                        "[2.6.4] attente workers relevée à {:.0f}s (échéance lead "
                        "moins 120s de marge d'intégration)", timeout,
                    )
        except Exception as _exc_264:
            # Ne JAMAIS avaler en silence : un garde-fou muet est un garde-fou mort.
            logger.warning("[2.6.4] relèvement d'attente impossible: {}", _exc_264)
    deadline = time.monotonic() + timeout
    timed_out = False
    cancelled = False
    while True:
        states = [(orch.get_task(cid) or {}).get("state") for cid in child_ids]
        if all(s in _TERMINAL for s in states):
            break
        if lead_id and orch.is_cancel_requested(lead_id):
            cancelled = True
            break
        if time.monotonic() >= deadline:
            timed_out = True
            break
        await asyncio.sleep(0.5)

    # Fusion des résultats + union des artefacts.
    lines: List[str] = []
    all_artifacts: List[str] = []
    blocked_issues: List[tuple] = []  # F3.a — (auteur du blocage, fichier visé)
    child_records: List[dict] = []    # F3.b — constat d'état des workers restants
    done = failed = 0
    for cid in child_ids:
        t = orch.get_task(cid) or {}
        child_records.append(t)  # F3.b
        meta = t.get("metadata") or {}
        state = t.get("state")
        obj = meta.get("objective") or t.get("message_preview") or ""
        summ = t.get("result_summary") or "(pas encore de résumé)"
        arts = meta.get("artifacts") or []
        all_artifacts.extend(arts)
        if state == "done":
            done += 1
        elif state == "failed":
            failed += 1
        # F3.a — un worker a buté sur un fichier qui n'est pas le sien. Fait
        # déterministe enregistré par le garde de périmètre, pas une déclaration
        # du modèle. On garde l'auteur pour ne jamais se router à soi-même.
        for _blocked in (meta.get("blocked_out_of_scope") or [])[:20]:
            blocked_issues.append((str(meta.get("delegation_owner") or "").strip(),
                                   str(_blocked)))
        lines.append(f"- [{state}] {str(obj)[:80]} (id: {cid})\n  → {str(summ)[:_fusion_excerpt()]}")

    if lead_id:
        try:
            current_meta = (orch.get_task(lead_id) or {}).get("metadata") or {}
            previous_ids = list(current_meta.get("children") or [])
            orch.set_task_metadata(
                lead_id,
                children=list(dict.fromkeys(previous_ids + list(child_ids))),
            )
        except Exception:
            pass
        # Lot 5.7.4/E — mémorise un état STRUCTURÉ de la délégation. Après un cancel
        # deadline + compaction, le rendu de statut s'appuie là-dessus au lieu de
        # reconstruire un faux « 2/6 » depuis un list_missions tronqué.
        try:
            orch.set_task_metadata(lead_id, last_delegate_progress={
                "done": int(done), "total": int(len(child_ids)), "failed": int(failed),
                "timed_out": bool(timed_out), "cancelled": bool(cancelled),
            })
        except Exception:
            pass

    header = f"Délégation : {done}/{len(child_ids)} terminée(s)"
    if failed:
        header += f", {failed} en échec"
    if timed_out:
        header += " ⏳ (délai dépassé — résultat PARTIEL, les workers continuent)"
    if cancelled:
        header += " (mission annulée — attente interrompue)"
    art_txt = ""
    if all_artifacts:
        uniq = list(dict.fromkeys(all_artifacts))
        art_txt = f"\n📦 {len(uniq)} livrable(s) : {', '.join(uniq[:10])}"
    # Steering anti-scavenge : le lead a les livrables ICI → il fusionne directement
    # au lieu de re-fouiller le disque (list_directory/find_files) ou de reconstruire.
    footer = (
        "\n\n➡️ Ce sont les LIVRABLES des workers. Fusionne-les DIRECTEMENT à partir d'ici "
        "(la version longue de chacun est conservée via mission_result(<id>) si l'aperçu "
        "ne suffit pas). NE va PAS chercher de fichiers sur le disque ni reconstruire ce que "
        "les workers ont déjà produit."
    )
    # 2.6.4 — résultat PARTIEL : le steering « fusionne directement » invitait le
    # lead à publier pendant que les workers mutaient encore les fichiers. STOP net.
    if timed_out:
        footer = (
            "\n\n⛔ RÉSULTAT PARTIEL — des workers travaillent ENCORE sur les fichiers "
            "de la mission. NE publie PAS maintenant (publish_mission_workspace sera "
            "REFUSÉ tant qu'ils tournent) et ne conclus aucun « succès ». Suis-les via "
            "mission_status(<id enfant>) puis reprends l'intégration (pytest → publish → "
            "serve_website → navigateur) quand TOUS sont terminés — ils s'arrêtent "
            "d'eux-mêmes au plus tard à l'échéance de la mission."
        )
        # F3.b (M9) — « les workers continuent » masquait trois situations très
        # différentes. On dit laquelle, sur des faits (state + updated_at), sans
        # jamais déclarer mort un worker qui progresse encore.
        try:
            # H2 — signal du TRAVAIL RÉEL : le dossier de mission a-t-il bougé
            # récemment ? Un worker dont le CodeAgent tourne 12 minutes ne touche
            # pas à sa tâche, mais il écrit des fichiers. L'I/O est ici, la
            # décision reste dans la fonction pure.
            _ws_idle_s = _mission_workspace_idle_s(ctx, _mission_ws)
            pending = classify_pending_workers(
                child_records, datetime.now(timezone.utc).isoformat(), _stall_threshold_s(),
                workspace_idle_s=_ws_idle_s,
            )
            _stalled = [p for p in pending if p["kind"] == "stalled"]
            _queued = [p for p in pending if p["kind"] == "queued"]
            _details = []
            for p in _stalled:
                _details.append(
                    f"- `{p['task_id']}` — AUCUNE progression depuis {p['idle_s']}s "
                    f"(état `{p['state']}`) : probablement bloqué."
                )
            for p in _queued:
                _details.append(
                    f"- `{p['task_id']}` — jamais démarré (en file, aucun créneau libre)."
                )
            if _details:
                footer += (
                    "\n\n🩺 ÉTAT RÉEL DES WORKERS RESTANTS :\n" + "\n".join(_details[:10]) +
                    # H2 — l'ancien message disait « reprends leur périmètre
                    # toi-même » : au run SuiviDepenses, le lead l'a fait pendant
                    # que les workers écrivaient encore les MÊMES fichiers. Un
                    # conseil de garde-fou peut créer le dégât qu'il veut éviter.
                    "\n➡️ NE REPRENDS PAS leurs fichiers tant qu'ils ne sont pas "
                    "terminés : tu écrirais par-dessus leur travail en cours. "
                    "Relance-les (`mission_status(<id>)` pour suivre), OU annule-les "
                    "explicitement (`cancel_mission(<id>)`) avant de reprendre leur "
                    "périmètre, OU conclus honnêtement en disant ce qui n'a pas été fait."
                )
            if _stalled or _queued:
                logger.warning(
                    "[delegate_and_wait] workers non terminaux : {} sans progres, {} en file",
                    len(_stalled), len(_queued),
                )
        except Exception as _exc_m9:
            logger.debug("[F3.b] constat workers ignore: {}", _exc_m9)
    # LOT 2.5 — consigne d'INTÉGRATION au moment exact où le lead décide de la suite :
    # si des tests existent (dossier mission 2.1, contrat 2.2, ou artefacts workers),
    # il doit les LANCER avant de finaliser. Gate d'honnêteté, pas blocage : s'il ne
    # les lance pas, sa clôture dira « tests présents non certifiés » (P0.2/2.5 react).
    tests_hint = ""
    try:
        _has_tests = False
        _mdir = None
        if _mission_ws:
            _fg25 = getattr(ctx, "file_guardrails", None)
            if _fg25 is not None:
                _mdir = _fg25._workspace_root() / _mission_ws
            else:
                from src.utils.paths import WORKSPACE_DIR as _WS25
                _mdir = _WS25 / _mission_ws
            from src.reasoning.test_proof import tests_present_in_dir, tests_present_in_contract
            _has_tests = tests_present_in_dir(str(_mdir))
            if not _has_tests:
                from src.subagents.mission_contract import CONTRACT_JSON as _CJ25
                _cj = _mdir / _CJ25
                if _cj.is_file():
                    import json as _j25
                    _has_tests = tests_present_in_contract(
                        _j25.loads(_cj.read_text(encoding="utf-8", errors="replace")))
        if not _has_tests and all_artifacts:
            from src.reasoning.test_proof import any_test_file as _atf25
            _has_tests = _atf25(all_artifacts)
        if _has_tests:
            _where = f"`workspace/{_mission_ws}`" if _mission_ws else "le dossier du livrable"
            tests_hint = (
                f"\n\n🧪 INTÉGRATION OBLIGATOIRE : des tests existent dans cette mission. "
                f"Lance `python -m pytest` dans {_where} et corrige LE CODE par MUTATION "
                "jusqu'au vert AVANT de finaliser — ne réécris JAMAIS les tests "
                "contractuels (si le contrat est erroné, dis-le au rapport). Pour livrer "
                "ailleurs : publish_mission_workspace (copie déterministe, tests inclus) "
                "— jamais de recopie manuelle fichier par fichier. Si le livrable est "
                "web : sers-le avec l'OUTIL serve_website(directory='<dossier>', "
                "port=8081) puis vérifie au "
                "navigateur (browser_navigate + contrôle du DOM). Ne dis JAMAIS « tests "
                "verts » sans run vert réel — sinon ta clôture dira « tests présents non "
                "certifiés »."
            )
    except Exception:
        tests_hint = ""
    # F3.a — les blocages hors périmètre remontent EN ÉVIDENCE, jamais noyés dans un
    # résumé tronqué. L'owner cible est lu au contrat : le lead sait à qui confier la
    # correction au lieu de la deviner (vrille MiniQuiz).
    issues_txt = ""
    try:
        if blocked_issues:
            seen_issue = set()
            issue_lines = []
            for _author, _file in blocked_issues:
                if not _file or (_author, _file) in seen_issue:
                    continue
                seen_issue.add((_author, _file))
                _target = ""
                if _contract_data is not None:
                    from src.subagents.mission_contract import owner_of_path
                    _target = owner_of_path(_contract_data, _file)
                if _target and _target == _author:
                    continue  # son propre fichier : pas une issue à router
                _who = f" → owner : **{_target}**" if _target else " → owner inconnu au contrat"
                _from = f" (signalé par {_author})" if _author else ""
                issue_lines.append(f"- `{_file}`{_who}{_from}")
            if issue_lines:
                issues_txt = (
                    "\n\n⚠️ BLOCAGES INTER-WORKERS — un worker a eu besoin d'un fichier "
                    "qui n'est pas le sien :\n" + "\n".join(issue_lines[:10]) +
                    "\n➡️ Traite-les AVANT de conclure : relance le worker owner sur ce "
                    "fichier, ou corrige-le toi-même si tu intègres. Ne clôture pas en "
                    "laissant un blocage non traité."
                )
    except Exception:
        issues_txt = ""
    return HandlerResult.ok(
        header + " :\n" + "\n".join(lines) + art_txt + issues_txt + footer + tests_hint,
        handler_name="delegate_and_wait",
    )


async def write_mission_contract_handler(
    ctx: HandlerContext, contract: Any = None, project: str = "",
    files: Any = None, effects: Any = None,
) -> HandlerResult:
    """LOT 2.2 — le LEAD écrit le contrat machine + stubs AVANT de déléguer.

    Écrit dans le dossier ISOLÉ de la mission (2.1) : `contract.json` (machine),
    `CONTRAT.md` (lisible) et un STUB par fichier (signatures EXACTES, corps TODO).
    Retourne les objectifs structurés `{objective, allowed_files}` prêts pour
    delegate_and_wait (périmètres appliqués par le garde 2.3).
    S'utilise DANS une mission uniquement (même règle que delegate_and_wait).
    N'écrase JAMAIS un fichier existant non vide.
    """
    # H4-bis (TEST RÉEL 2026-08-13, mission veille_python_313) — le modèle a passé
    # `effects` en argument TOP-LEVEL au lieu de l'imbriquer dans `contract` : le
    # registre a retiré l'arg inconnu, l'outil a répondu « paramètre requis
    # manquant : contract », et une itération a été perdue. Le repli coûte trois
    # lignes et supprime un aller-retour à CHAQUE mission d'effets. Même patron
    # « params optionnels, zéro régression » que le LOT I : si `contract` est
    # fourni, RIEN ne change.
    if contract is None and (files is not None or effects is not None):
        contract = {}
        if files is not None:
            contract["files"] = files
        if effects is not None:
            contract["effects"] = effects
        if project:
            contract["project"] = project
    core = _core(ctx)
    if core is None or getattr(core, "task_orchestrator", None) is None:
        return HandlerResult.fail("Système de missions indisponible.", handler_name="write_mission_contract")
    if _current_depth(ctx) == 0:
        return HandlerResult.fail(
            "write_mission_contract s'utilise DANS une mission (c'est le lead qui pose "
            "le contrat avant delegate_and_wait). Au chat, utilise create_mission.",
            handler_name="write_mission_contract",
        )
    from src.subagents.mission_contract import (
        CONTRACT_JSON, CONTRACT_MD, derive_project_name, parse_contract,
        validate_contract, generate_stub, render_contract_md, worker_objectives,
        web_root_route_warning, flask_static_root_warning,
        missing_shared_stylesheet_warning,
    )
    # LOT 2.10 (run StockPilot) — erreur GUIDANTE : le modèle avait passé un
    # tableau MARKDOWN, reçu « contrat illisible » sec, et BYPASSÉ l'outil (contrat
    # + stubs à la main → rail 2.2/2.3 contourné). Un retry réussi vaut mieux
    # qu'un bypass : on donne l'exemple exact et on interdit le fallback manuel.
    _retry_guide = (
        "\n\n➡️ RAPPELLE write_mission_contract avec un OBJET JSON STRICT — pas de "
        "markdown, pas de tableau, pas de prose. N'écris PAS contract.json ni les "
        "stubs à la main : c'est l'outil qui les génère (et il rend les objectifs "
        "workers avec leurs périmètres). Garde le champ \"project\" (nom du "
        "livrable publié).\n"
        "RÈGLES du contrat (sinon les workers codent des coquilles vides) :\n"
        "• \"exports\" = signatures COMPLÈTES avec def : 'def add(nom: str, capacite: "
        "int) -> dict' — JAMAIS un nom nu comme 'add'.\n"
        "• \"desc\" = le COMPORTEMENT attendu du fichier (ex. « create() REFUSE si "
        "chevauchement », « précharge 3 salles ») — il est transmis TEL QUEL au worker.\n"
        "Exemple :\n"
        '{"project": "MonProjet", '
        '"files": [{"path": "orders.py", "owner": "w_orders", '
        '"desc": "create() REFUSE si le creneau chevauche une resa existante ; bords exclus", '
        '"exports": ["def create(room_id: int, date: str, debut: str, fin: str, nom: str) -> dict | None"], '
        '"imports": ["from rooms import get_by_id"]}, '
        '{"path": "tests/test_orders.py", "owner": "w_tests"}]}'
        "\n"
        "• Si la mission ne produit PAS de fichier (envoyer, poster, chercher, "
        "déployer, réserver, mémoriser…), décris des EFFETS au lieu de fichiers — "
        "`proof` = à quoi on verra que c'est fait, il est OBLIGATOIRE :\n"
        '{"project": "Veille", '
        '"effects": [{"owner": "w_rech", "action": "recherche_web", '
        '"target": "python 3.14", "desc": "3 sources fiables et datees", '
        '"proof": "3 URLs citees avec date"}, '
        '{"owner": "w_notif", "action": "poster_slack", "target": "#veille", '
        '"desc": "poster la synthese", "proof": "id du message poste"}]}'
    )
    data, parse_errors = parse_contract(contract)
    if parse_errors:
        return HandlerResult.fail(
            "Contrat illisible : " + " ".join(parse_errors) + _retry_guide,
            handler_name="write_mission_contract")
    if project and not data.get("project"):
        data["project"] = str(project)
    errors = validate_contract(data)
    if errors:
        return HandlerResult.fail(
            "Contrat invalide :\n- " + "\n- ".join(errors) + _retry_guide,
            handler_name="write_mission_contract")

    lead_id = getattr(ctx, "runtime_task_id", None)
    orch = core.task_orchestrator
    lead_meta = (orch.get_task(lead_id) or {}).get("metadata") or {} if lead_id else {}
    mission_ws = _ensure_mission_workspace(orch, lead_id, lead_meta)
    fg = getattr(ctx, "file_guardrails", None)
    if not mission_ws or fg is None:
        return HandlerResult.fail(
            "Impossible de déterminer le dossier de mission (mission_workspace).",
            handler_name="write_mission_contract",
        )
    # C0.6 (run FrigoZen) — contrat sans nom de projet (le retry du lead ne repasse
    # pas toujours `project`) : dérive un nom lisible depuis l'objectif de la
    # mission, sinon la publication retombera sur `livrable_<hex>`.
    if not str(data.get("project") or "").strip():
        try:
            _obj_c06 = str(lead_meta.get("objective") or "")
            if not _obj_c06 and lead_id:
                _obj_c06 = str((orch.get_task(lead_id) or {}).get("message_preview") or "")
            _derived_c06 = derive_project_name(_obj_c06)
            if _derived_c06:
                data["project"] = _derived_c06
        except Exception:
            pass

    import json as _json

    # Hardening (note de revue 2.2) : si un contract.json existe DÉJÀ et diffère du
    # nouveau contrat → FAIL propre. Sinon on conserverait les anciens fichiers sur
    # disque tout en retournant des objectifs issus du NOUVEAU contrat (divergence
    # silencieuse). Contrat identique → idempotent (re-création de stubs manquants OK).
    _existing_cj = fg._workspace_root() / mission_ws / CONTRACT_JSON
    if _existing_cj.exists():
        try:
            _old = _json.loads(_existing_cj.read_text(encoding="utf-8", errors="replace"))
        except (ValueError, TypeError):
            _old = None
        if _old != data:
            return HandlerResult.fail(
                "⛔ Un contrat DIFFÉRENT existe déjà dans cette mission "
                f"(`{mission_ws}/{CONTRACT_JSON}`). Les stubs sur disque correspondent à "
                "l'ANCIEN contrat — écrire de nouveaux objectifs créerait une divergence. "
                "Relis CONTRAT.md et réutilise le contrat existant, ou repars d'une "
                "nouvelle mission si le contrat doit changer.",
                handler_name="write_mission_contract",
            )

    written: List[str] = []
    skipped: List[str] = []

    def _write(rel: str, content: str) -> None:
        target = fg._workspace_root() / mission_ws / rel
        if target.exists() and target.read_text(encoding="utf-8", errors="replace").strip():
            skipped.append(rel)  # jamais d'écrasement d'un fichier non vide
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        written.append(rel)

    try:
        _write(CONTRACT_JSON, _json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        _write(CONTRACT_MD, render_contract_md(data))
        for entry in data.get("files") or []:
            _write(str(entry["path"]).replace("\\", "/"),
                   generate_stub(entry, all_files=data.get("files") or (),
                                 project=str(data.get("project") or "")))
    except Exception as e:
        return HandlerResult.fail(f"Erreur écriture contrat/stubs: {e}",
                                  handler_name="write_mission_contract")

    objs = worker_objectives(data)
    parts = [f"📜 Contrat posé dans `{mission_ws}/` — {len(written)} fichier(s) écrit(s)"
             + (f", {len(skipped)} existant(s) conservé(s) ({', '.join(skipped)})" if skipped else "")
             + f" : {', '.join(written)}."]
    # LOT 2.2 (run MotDuJour) — guidance route racine : contrat web sans GET / =
    # app inaccessible même servie. Additif, jamais bloquant.
    _root_warn = web_root_route_warning(data)
    if _root_warn:
        parts.append(_root_warn)
    # 2.7.3 (run MiniPanier) — collision liens relatifs / Flask static_url_path.
    _flask_static_warn = flask_static_root_warning(data)
    if _flask_static_warn:
        parts.append(_flask_static_warn)
    # 2.9.B (run TriboBlog2) — HTML déclaré sans .css → style créé hors contrat,
    # jamais lié par le stub → site nu. Invite à déclarer la feuille au contrat.
    _css_warn = missing_shared_stylesheet_warning(data)
    if _css_warn:
        parts.append(_css_warn)
    # LOT L2 (run MemoNest) — les trois avertissements ci-dessus regardent le
    # CONTRAT ; aucun ne le compare à ce qui était DEMANDÉ. MemoNest exigeait une
    # page d'accueil et une vérif navigateur, le contrat n'a déclaré que 5 `.py` :
    # le frontend a été improvisé après publication, HTML et CSS écrits en deux
    # passes → classes divergentes → page sans mise en page. Import LOCAL :
    # `react` importe déjà `mission_contract`, l'inverse ferait un cycle.
    try:
        from src.subagents.mission_contract import objective_expects_ui_warning

        from ..react import _objective_wants_browser

        _ui_warn = objective_expects_ui_warning(
            data, _objective_wants_browser(str(lead_meta.get("objective") or ""))
        )
        if _ui_warn:
            parts.append(_ui_warn)
    except Exception as _e_l2:  # guidance additive : ne casse JAMAIS la pose du contrat
        logger.debug("[L2] avertissement UI ignoré: {}", _e_l2)
    # LOT P1 — le lead décide son découpage sans savoir ce qu'il coûte : le
    # CodeAgent est un singleton sérialisé, donc les workers CODENT chacun leur
    # tour. On lui donne le chiffre au moment où il peut encore regrouper.
    try:
        from src.subagents.delegation_cost import delegation_cost_note

        _files_p1 = [f for f in (data.get("files") or []) if isinstance(f, dict)]
        _owners_p1 = {
            str(f.get("owner") or "").strip()
            for f in _files_p1
            if str(f.get("owner") or "").strip()
        }
        _cost_note = delegation_cost_note(len(_owners_p1), len(_files_p1))
        if _cost_note:
            parts.append(_cost_note)
    except Exception as _e_p1:  # guidance additive : ne casse JAMAIS le contrat
        logger.debug("[P1] note de coût ignorée: {}", _e_p1)
    parts.append(
        "➡️ Délègue MAINTENANT avec ces objectifs (chaque worker reçoit son périmètre "
        "allowed_files — il ne pourra écrire QUE ses fichiers) :\n"
        "delegate_and_wait(objectives=" + _json.dumps(objs, ensure_ascii=False) + ")"
    )
    return HandlerResult.ok("\n\n".join(parts), handler_name="write_mission_contract")


async def publish_mission_workspace_handler(
    ctx: HandlerContext, target: str = "",
) -> HandlerResult:
    """A2 (Phase A, run FitLog) — publie le dossier de mission vers une cible du
    workspace en UNE copie déterministe (code-side, shutil).

    Le run FitLog est mort en recopiant les fichiers à la main (Copy-Item bloqué →
    read/write LLM fichier par fichier → contenus réinventés/dégradés, tests jamais
    copiés, anti-boucle). Ici : zéro LLM dans la copie, tests INCLUS, `.backups`/
    caches exclus. S'utilise DANS une mission uniquement (c'est le lead qui publie).
    """
    import shutil
    from pathlib import Path, PurePosixPath

    _H_NAME = "publish_mission_workspace"
    core = _core(ctx)
    if core is None or getattr(core, "task_orchestrator", None) is None:
        return HandlerResult.fail("Système de missions indisponible.", handler_name=_H_NAME)
    if _current_depth(ctx) == 0:
        return HandlerResult.fail(
            "publish_mission_workspace s'utilise DANS une mission (c'est le lead qui "
            "publie le livrable final).", handler_name=_H_NAME)
    lead_id = getattr(ctx, "runtime_task_id", None)
    orch = core.task_orchestrator
    lead_meta = (orch.get_task(lead_id) or {}).get("metadata") or {} if lead_id else {}

    # 2.6.4 (run MiniQuiz §5) — pas de publication pendant que des workers mutent
    # encore le dossier de mission : le lead a publié à 01:02 sur un delegate_and_wait
    # PARTIEL (1/3) et les workers ont continué d'éditer tests/index.html jusqu'à
    # 01:08 → livrable publié ≠ dossier mission. Échappatoire : échéance dépassée
    # (les workers sont annulés à l'échéance ; on ne bloque pas la clôture).
    try:
        _pending_264 = []
        for _cid_264 in list(lead_meta.get("children") or []):
            _t_264 = orch.get_task(_cid_264)
            # Enfant introuvable (purgé) → traité comme terminal : on ne bloque
            # jamais la publication sur un id qu'on ne peut plus observer.
            if _t_264 and _t_264.get("state") not in _TERMINAL:
                _pending_264.append(_cid_264)
    except Exception:
        _pending_264 = []
    if _pending_264:
        _past_deadline_264 = False
        try:
            # H1 — même bug qu'en 2.6.4 : `float()` sur une chaîne ISO. Ici l'except
            # rendait le garde PLUS strict que voulu (publication toujours refusée
            # tant qu'un worker tourne). Corrigé : après l'échéance, la publication
            # redevient possible — les workers s'arrêtent au plus tard à cette borne.
            from src.subagents.mission_budget import seconds_until_deadline
            _left_pub = seconds_until_deadline(lead_meta.get("deadline_ts"))
            _past_deadline_264 = _left_pub is not None and _left_pub <= 0
        except Exception as _exc_pub:
            logger.warning("[publish] lecture d'échéance impossible: {}", _exc_pub)
            _past_deadline_264 = False
        if not _past_deadline_264:
            _ids_264 = ", ".join(str(c)[-8:] for c in _pending_264[:5])
            return HandlerResult.fail(
                f"⛔ Publication refusée : {len(_pending_264)} worker(s) travaillent "
                f"ENCORE sur les fichiers de la mission ({_ids_264}) — publier "
                "maintenant copierait un livrable en cours de mutation. Attends-les "
                "(mission_status(<id>)) puis rappelle publish_mission_workspace quand "
                "tout est terminé ; ils s'arrêtent au plus tard à l'échéance.",
                handler_name=_H_NAME)

    mission_ws = _ensure_mission_workspace(orch, lead_id, lead_meta)
    fg = getattr(ctx, "file_guardrails", None)
    if not mission_ws or fg is None:
        return HandlerResult.fail(
            "Impossible de déterminer le dossier de mission (mission_workspace).",
            handler_name=_H_NAME)
    ws_root = fg._workspace_root().resolve()
    src_dir = (ws_root / mission_ws).resolve()
    if not src_dir.is_dir():
        return HandlerResult.fail(
            f"Dossier de mission introuvable: {mission_ws}", handler_name=_H_NAME)

    # Cible : relative au workspace ; défaut = nom du projet du contrat.
    raw_target = str(target or "").replace("\\", "/").strip()
    if raw_target.lower().startswith("workspace/"):
        raw_target = raw_target[len("workspace/"):]
    # Absolu détecté AVANT le strip des slashs (sinon "/evil" devient "evil").
    _abs_requested = raw_target.startswith("/") or (len(raw_target) > 1 and raw_target[1] == ":")
    raw_target = raw_target.strip("/")
    if not raw_target:
        try:
            import json as _json
            _data = _json.loads((src_dir / "contract.json").read_text(encoding="utf-8"))
            raw_target = str(_data.get("project") or "").strip()
        except Exception:
            raw_target = ""
    if not raw_target:
        # C0.6 (run FrigoZen) — dernier filet lisible : nom dérivé de l'objectif de
        # la mission (« FrigoZen ») plutôt qu'un `livrable_<hex>` technique.
        try:
            from src.subagents.mission_contract import derive_project_name as _dpn_c06
            _obj_pub = str(lead_meta.get("objective") or "")
            if not _obj_pub and lead_id:
                _obj_pub = str((orch.get_task(lead_id) or {}).get("message_preview") or "")
            raw_target = _dpn_c06(_obj_pub)
        except Exception:
            raw_target = ""
    if not raw_target:
        raw_target = f"livrable_{str(lead_id or 'mission')[-8:]}"
    _pp = PurePosixPath(raw_target)
    if _abs_requested or _pp.is_absolute() or ".." in _pp.parts or (len(raw_target) > 1 and raw_target[1] == ":"):
        return HandlerResult.fail(
            "Cible invalide (chemin absolu ou traversal). Donne un chemin RELATIF au "
            'workspace, ex. target="fitlog".', handler_name=_H_NAME)
    dest = (ws_root / raw_target).resolve()
    try:
        dest.relative_to(ws_root)
    except ValueError:
        return HandlerResult.fail(
            "Cible hors du workspace — donne un chemin RELATIF au workspace.",
            handler_name=_H_NAME)
    if dest == src_dir or src_dir in dest.parents:
        return HandlerResult.fail(
            "La cible est le dossier de mission lui-même — publie vers un dossier "
            'du workspace, ex. target="fitlog".', handler_name=_H_NAME)
    if dest == ws_root / "missions" or (ws_root / "missions") in dest.parents:
        return HandlerResult.fail(
            "La cible est dans l'arbre des missions — publie vers un dossier public "
            'du workspace, ex. target="fitlog".', handler_name=_H_NAME)

    _EXCLUDED_DIRS = {".backups", "__pycache__", ".pytest_cache"}

    # 2.8.4 (run VentesReport) — chemins DÉCLARÉS au contrat : sert à distinguer un
    # vrai livrable d'un fichier POUBELLE que le CodeAgent a semé en luttant contre
    # le guard de périmètre (`test_log_fixes.py` 0 octet, jamais au contrat). On ne
    # touche PAS aux sorties générées légitimes (csv/txt/json) : elles ont du contenu.
    _contract_paths: set = set()
    _contract_data_cp: dict = {}
    try:
        import json as _json_cp
        _cj = _json_cp.loads((src_dir / "contract.json").read_text(encoding="utf-8"))
        _contract_data_cp = _cj if isinstance(_cj, dict) else {}
        for _cf in (_cj.get("files") or []):
            _p = str((_cf or {}).get("path") or "").replace("\\", "/").strip()
            if _p:
                _contract_paths.add(_p)
    except Exception:
        _contract_paths = set()
        _contract_data_cp = {}

    # Contractual web bundles are checked before the first copy. This prevents
    # undeclared duplicate entrypoints and broken HTML/CSS/JS links from becoming
    # the published deliverable while leaving non-contract missions unchanged.
    if _contract_data_cp:
        try:
            from src.subagents.mission_web_bundle import validate_contract_web_bundle
            _bundle_report = validate_contract_web_bundle(src_dir, _contract_data_cp)
            _bundle_errors = list(_bundle_report.get("errors") or [])
        except Exception as exc:
            _bundle_errors = [f"verification_bundle_indisponible: {type(exc).__name__}: {exc}"]
        if _bundle_errors:
            return HandlerResult.fail(
                "Publication refusee : bundle web contractuel incoherent. "
                "Corrige les fichiers dans le dossier de mission puis relance la publication. "
                + " | ".join(_bundle_errors[:12]),
                handler_name=_H_NAME,
            )

    def _is_junk_zero_byte(relpath: str, full: Path) -> bool:
        """2.8.4 — 0 octet + NON déclaré au contrat + pas un __init__.py = poubelle."""
        base = relpath.rsplit("/", 1)[-1].lower()
        if base == "__init__.py" or relpath in _contract_paths:
            return False
        try:
            return full.is_file() and full.stat().st_size == 0
        except Exception:
            return False

    # LOT E (run CéramiShop) : les backups d'édition (`orders.py.bak_062053`) ne
    # doivent pas polluer le livrable — l'auto-backup avant edit les sème dans le
    # dossier mission. Exclusion par motif de fichier (pas seulement les dossiers).
    def _is_excluded_file(name: str) -> bool:
        low = name.lower()
        return ".bak" in low or low.endswith(".tmp") or low.endswith(".swp")

    def _ignore(dirpath: str, names: List[str]) -> List[str]:
        out = [n for n in names if n in _EXCLUDED_DIRS or _is_excluded_file(n)]
        # 2.8.4 — junk 0 octet hors contrat (relpath calculé depuis src_dir).
        for n in names:
            full = Path(dirpath) / n
            if full.is_file():
                rel = str(full.relative_to(src_dir)).replace("\\", "/")
                if _is_junk_zero_byte(rel, full) and n not in out:
                    out.append(n)
        return out

    try:
        # ── LOT Z17 — publier n'autorise pas à détruire ────────────────────────
        # Run « Verdure 2 » (2026-08-16) : l'objectif disait `workspace/verdure2/`,
        # le lead a appelé `publish_mission_workspace()` SANS argument, la
        # destination a donc été tirée de `contract.project` = « Verdure » →
        # `workspace/verdure/`. Ce dossier contenait le livrable de la VEILLE.
        # `dirs_exist_ok=True` l'a recouvert sans un mot. Le travail d'une autre
        # mission a disparu, et personne ne l'a su avant que je compare les runs.
        #
        # On n'interdit pas l'écrasement — republier au même endroit est normal et
        # le LOT Z8 l'EXIGE même après une correction tardive. On refuse seulement
        # qu'il soit SILENCIEUX et IRRÉVERSIBLE : tout fichier dont le contenu va
        # changer est d'abord archivé sous `.backups/<nom>.<horodatage>`, le même
        # mécanisme que celui d'`edit_file`. Republier à l'identique n'archive
        # rien (contenu égal), donc le cas courant ne coûte rien.
        _ecrases: list[str] = []
        try:
            if dest.is_dir():
                from datetime import datetime as _dtZ17

                _stamp = _dtZ17.now().strftime("%Y%m%d_%H%M%S")
                for _s in src_dir.rglob("*"):
                    if not _s.is_file():
                        continue
                    _rel = _s.relative_to(src_dir)
                    if any(p in _EXCLUDED_DIRS for p in _rel.parts[:-1]):
                        continue
                    _d = dest / _rel
                    if not _d.is_file():
                        continue  # nouveau fichier : rien à préserver
                    try:
                        if _d.read_bytes() == _s.read_bytes():
                            continue  # republication à l'identique
                    except Exception:
                        pass
                    _bdir = dest / ".backups"
                    _bdir.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(_d, _bdir / f"{_rel.name}.{_stamp}")
                    _ecrases.append(str(_rel).replace("\\", "/"))
        except Exception as _exc_z17:
            logger.debug("[Z17] archivage avant publication ignoré : {}", _exc_z17)
        shutil.copytree(src_dir, dest, dirs_exist_ok=True, ignore=_ignore)
    except Exception as e:
        return HandlerResult.fail(f"Erreur de publication: {e}", handler_name=_H_NAME)

    # A mission can intentionally create a document through Document Studio,
    # whose managed output directory is outside the isolated mission folder.
    # Include only artifacts explicitly persisted on this task, existing under
    # workspace, and never rescan/copy arbitrary workspace files.
    _external_copied: List[str] = []
    for _raw_artifact in list(lead_meta.get("artifacts") or []):
        try:
            _artifact = Path(str(_raw_artifact))
            if not _artifact.is_absolute():
                _artifact = ws_root / _artifact
            _artifact = _artifact.resolve()
            _artifact.relative_to(ws_root)
            if not _artifact.is_file():
                continue
            if _artifact == src_dir or src_dir in _artifact.parents:
                continue
            if _artifact == dest or dest in _artifact.parents:
                continue
            if _is_excluded_file(_artifact.name) or _artifact.stat().st_size == 0:
                continue
            _target = dest / _artifact.name
            if _target.exists() and _target.resolve() != _artifact:
                _target = dest / "artifacts" / _artifact.name
            _target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(_artifact, _target)
            _external_copied.append(
                str(_target.relative_to(dest)).replace("\\", "/")
            )
        except (OSError, ValueError, TypeError):
            continue

    copied: List[str] = []
    for _root, _dirs, _files in os.walk(src_dir):
        _dirs[:] = [d for d in _dirs if d not in _EXCLUDED_DIRS]
        for _f in _files:
            _full = Path(_root) / _f
            _rel = str(_full.relative_to(src_dir)).replace("\\", "/")
            if _is_excluded_file(_f) or _is_junk_zero_byte(_rel, _full):
                continue
            copied.append(_rel.replace("\\", "/"))
    copied.extend(_external_copied)
    copied = list(dict.fromkeys(copied))
    copied.sort()
    rel_dest = str(dest.relative_to(ws_root)).replace("\\", "/")
    # M106: publication is a durable mission fact, not an in-memory ledger hint.
    if lead_id:
        try:
            orch.set_task_metadata(
                lead_id,
                mission_published=True,
                published_workspace=f"workspace/{rel_dest}",
                published_files=copied,
                published_at=datetime.now(timezone.utc).isoformat(),
            )
        except Exception as exc:
            logger.debug("[M106] publication metadata persistence skipped: {}", exc)
    # LOT Q (run Fibrance) — une page peut passer TOUS les gardes, réussir son
    # clic de vérification, et arriver illisible : 12 classes du HTML sur 15
    # n'avaient aucune règle CSS. Le fait est vérifiable sur le disque publié ;
    # il n'était simplement jamais calculé. On le calcule, on le dit, on ne
    # bloque rien.
    _style_note = ""
    try:
        from src.subagents.style_coverage import style_coverage_note

        _style_note = style_coverage_note(dest)
    except Exception as _exc_q:
        logger.debug("[Q] mesure de style ignorée: {}", _exc_q)
    return HandlerResult.ok(
        f"📦 Livrable publié : {len(copied)} fichier(s) copiés de `{mission_ws}/` vers "
        f"`workspace/{rel_dest}/` (copie déterministe, tests inclus) : {', '.join(copied)}."
        # LOT Z17 — un archivage muet ne protège personne : si cette publication
        # a recouvert le travail d'une autre mission, la mission DOIT le lire et
        # pouvoir en rendre compte. Le run Verdure 2 a écrasé le Verdure de la
        # veille sans que rien ne l'indique nulle part.
        + (
            f"\n♻️ **{len(_ecrases)} fichier(s) existants ont été RECOUVERTS** — "
            f"leur version précédente est archivée dans "
            f"`workspace/{rel_dest}/.backups/` : {', '.join(_ecrases[:6])}"
            + (f" (+{len(_ecrases) - 6})" if len(_ecrases) > 6 else "")
            + ". Si ce dossier appartenait à un AUTRE livrable, dis-le dans ton "
            "rapport — et republie sous un nom distinct avec "
            "`publish_mission_workspace(target='<nom>')`."
            if _ecrases else ""
        )
        + _style_note
        + "\n"
        + _publish_next_steps(rel_dest, copied),
        handler_name=_H_NAME,
    )


def _publish_next_steps(rel_dest: str, copied: List[str]) -> str:
    """LOT 2.6 (run Converto 2026-07-06) — la prochaine étape après publication,
    avec la SYNTAXE D'APPEL EXACTE quand le livrable est web. L'ancien message
    (« puis sers et vérifie au navigateur si c'est du web ») ne nommait pas
    l'outil : le lead a conclu 2× « serve_website n'est pas dans ma liste » et la
    jambe navigateur n'a jamais eu lieu. C'est la DERNIÈRE chose que le lead lit
    avant de conclure. Pur/testable."""
    is_web = any(str(f).lower().endswith((".html", ".htm")) for f in copied or ())
    steps = (
        f"➡️ Prochaine étape : lance `python -m pytest` avec cwd=workspace/{rel_dest} "
        "(corrige par MUTATION si rouge)"
    )
    if is_web:
        steps += (
            ".\n🌐 Livrable WEB : SERS-le MAINTENANT avec l'outil "
            f"serve_website(directory='workspace/{rel_dest}', port=8081), puis "
            "browser_navigate sur l'URL retournée et CONTRÔLE le DOM (le flux "
            "demandé) AVANT de conclure."
        )
    else:
        steps += ", puis sers et vérifie au navigateur si c'est du web."
    return steps


# ── HandlerDefs ─────────────────────────────────────────────────────────────────

def get_missions_handler_defs() -> List[HandlerDef]:
    """Retourne les outils de mission (catégorie `missions`)."""
    return [
        HandlerDef(
            name="create_mission",
            description=(
                "Enregistre une mission et la lance en ARRIÈRE-PLAN, exécutée par un sous-agent "
                "(une Lumena complète) — sans bloquer la conversation. "
                "QUAND : « enregistre une mission pour faire X », un travail long à faire de ton côté. "
                "N'impose PAS de dossier cible dans l'objectif (pas de « dans workspace/xxx/ ») : la "
                "mission travaille dans son dossier dédié et publiera le livrable à la fin. "
                "Après l'avoir créée, annonce-le et TERMINE ton tour (FINAL) ; ne la suis PAS dans le "
                "même tour — tu utiliseras mission_status/mission_result quand l'utilisateur demandera."
            ),
            parameters={
                "properties": {
                    "objective": {"type": "string", "description": "Ce que la mission doit accomplir (détaillé)."},
                    "duree_minutes": {
                        "type": "number",
                        "description": (
                            "PRÉFÉRÉ quand l'utilisateur donne une DURÉE (« prends 90 minutes », "
                            "« 2 heures ») : passe le nombre de minutes, RIEN d'autre. Ne calcule "
                            "JAMAIS l'horodatage toi-même — deux missions ont été tuées comme ça "
                            "(75 min devenues 35, 90 min devenues 90 SECONDES)."
                        ),
                    },
                    "deadline": {
                        "type": "string",
                        "description": (
                            "Échéance en texte libre quand l'utilisateur donne un MOMENT et non une "
                            "durée (« ce soir », « avant 18h »). Pour une durée, utilise "
                            "`duree_minutes`."
                        ),
                    },
                },
                "required": ["objective"],
            },
            handler=create_mission_handler,
            category=_H,
            source_module="handlers.missions",
        ),
        HandlerDef(
            name="list_missions",
            description="Liste les missions (en cours, passées) avec leur état.",
            parameters={"properties": {}, "required": []},
            handler=list_missions_handler,
            category=_H,
            source_module="handlers.missions",
        ),
        HandlerDef(
            name="mission_status",
            description="État d'une mission par son id.",
            parameters={
                "properties": {"mission_id": {"type": "string", "description": "Id de la mission."}},
                "required": ["mission_id"],
            },
            handler=mission_status_handler,
            category=_H,
            source_module="handlers.missions",
        ),
        HandlerDef(
            name="mission_result",
            description="Résultat + livrables d'une mission terminée.",
            parameters={
                "properties": {"mission_id": {"type": "string", "description": "Id de la mission."}},
                "required": ["mission_id"],
            },
            handler=mission_result_handler,
            category=_H,
            source_module="handlers.missions",
        ),
        HandlerDef(
            name="cancel_mission",
            description="Annule une mission (annulation coopérative : s'arrête au prochain checkpoint).",
            parameters={
                "properties": {"mission_id": {"type": "string", "description": "Id de la mission."}},
                "required": ["mission_id"],
            },
            handler=cancel_mission_handler,
            category=_H,
            source_module="handlers.missions",
        ),
        HandlerDef(
            name="delegate_and_wait",
            description=(
                "DANS une mission : découpe le travail en plusieurs sous-missions exécutées EN "
                "PARALLÈLE par des sous-agents (workers), attend qu'elles finissent, puis te rend "
                "leurs résultats fusionnés. QUAND : un gros travail décomposable en parties "
                "indépendantes. À NE PAS utiliser au chat (là, utilise create_mission)."
            ),
            parameters={
                "properties": {
                    "objectives": {
                        "type": "array",
                        # LOT 2.3 — items string OU objet structuré : sans cette
                        # déclaration, les modèles ne produisent jamais les périmètres.
                        "items": {
                            "type": ["string", "object"],
                            "properties": {
                                "objective": {"type": "string", "description": "Objectif du worker."},
                                "allowed_files": {
                                    "type": "array", "items": {"type": "string"},
                                    "description": "Fichiers (relatifs au dossier mission) que CE worker "
                                                   "a le droit d'écrire — il ne pourra écrire QU'eux.",
                                },
                            },
                        },
                        "description": (
                            "Objectifs des sous-missions (un par worker) : chaînes simples, ou objets "
                            "{objective, allowed_files} pour borner l'écriture de chaque worker à SES "
                            "fichiers (recommandé pour du code multi-fichiers — cf. write_mission_contract)."
                        ),
                    },
                    "timeout": {"type": "number", "description": "Délai max d'attente en secondes (défaut 1200)."},
                },
                "required": ["objectives"],
            },
            handler=delegate_and_wait_handler,
            category=_H,
            source_module="handlers.missions",
        ),
        HandlerDef(
            name="write_mission_contract",
            description=(
                "DANS une mission, AVANT delegate_and_wait, DÈS QUE tu délègues à "
                "plusieurs workers — quel que soit le domaine : pose le CONTRAT machine "
                "→ écrit contract.json + CONTRAT.md (+ un STUB par fichier, signatures "
                "figées) dans le dossier de la mission, et te rend les objectifs "
                "structurés prêts pour delegate_and_wait (chaque worker borné à SA part). "
                "Deux natures de livrables, combinables : `files` pour le CODE (évite la "
                "dérive d'API entre workers parallèles) et `effects` pour tout le RESTE "
                "(mail envoyé, message Slack, site déployé, recherche menée, réservation, "
                "entrée mémoire, action MCP…). Une mission SANS aucun fichier se "
                "contractualise avec `effects` seuls — c'est ce qui donne à chaque worker "
                "un périmètre non ambigu et une preuve à ramener."
            ),
            parameters={
                "properties": {
                    "contract": {
                        "type": ["object", "string"],
                        "description": (
                            "Contrat machine : {project, files?: [{path, owner, "
                            "api|exports: [signatures EXACTES, ex. 'def create_app():'], "
                            "imports?: [lignes d'import inter-fichiers EXACTES — elles "
                            "seront écrites EN DUR dans le stub], desc|description}], "
                            "effects?: [{owner, action, target?, desc, proof}], "
                            "shared_api?: {référence commune, structure libre}, notes?}. "
                            "`files` et/ou `effects` — au moins l'un des deux. Tout fichier "
                            ".py NON-test doit déclarer api/exports (ou no_public_api: true "
                            "s'il n'a vraiment aucune API). Un `effect` décrit un livrable "
                            "NON-fichier : `action` = le verbe ('envoyer_email', "
                            "'poster_slack', 'deployer_site', 'recherche_web', 'reserver'), "
                            "`target` = sur quoi, `desc` = ce qui doit être accompli, "
                            "`proof` = à QUOI on verra que c'est fait ('id du message', "
                            "'URL qui répond 200', 'accusé d'envoi') — la preuve est "
                            "OBLIGATOIRE, sans elle la mission ne peut être clôturée que "
                            "sur parole. Un effet = un seul owner. Objet ou JSON."
                        ),
                    },
                    "project": {"type": "string", "description": "Nom du projet (optionnel)."},
                    # H4-bis — tolérance de forme : `files`/`effects` passés à plat
                    # sont repliés dans `contract`. Préfère `contract`.
                    "files": {
                        "type": "array",
                        "description": (
                            "Optionnel — raccourci : liste des fichiers si tu ne "
                            "passes pas d'objet `contract` complet."
                        ),
                    },
                    "effects": {
                        "type": "array",
                        "description": (
                            "Optionnel — raccourci : liste des effets si tu ne "
                            "passes pas d'objet `contract` complet."
                        ),
                    },
                },
                "required": [],
            },
            handler=write_mission_contract_handler,
            category=_H,
            source_module="handlers.missions",
        ),
        HandlerDef(
            name="publish_mission_workspace",
            description=(
                "DANS une mission, à la FIN : publie le livrable en copiant le dossier de "
                "mission ENTIER (tests inclus) vers un dossier public du workspace, en UNE "
                "copie déterministe. NE recopie JAMAIS les fichiers à la main "
                "(read_file+write_file un par un = contenus dégradés). Sans target : nom "
                "du projet du contrat."
            ),
            parameters={
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "Dossier cible RELATIF au workspace (ex. \"fitlog\"). "
                                       "Optionnel : défaut = nom du projet du contrat.",
                    },
                },
                "required": [],
            },
            handler=publish_mission_workspace_handler,
            category=_H,
            source_module="handlers.missions",
        ),
    ]
