"""Runtime missions — lectrices pures extraites de `react.py` (lot RF-6a).

Ce module ne DECIDE que sur un instantane : il ne mute jamais l'etat du
`ReActLoop`. Les trois methodes qui mutent `self`
(`_nudge_unpublished_writes`, `_mission_overwrite_gate`,
`_chat_mission_intent_gate`) restent dans `react.py` — invariant 5, perimetre
de RF-6b.

**Ce module n'importe JAMAIS `react.py`** (invariant 2).

L'entree `EntreeMission` est un contrat d'etat a champs `Callable`, tous
PARESSEUX : les tests du depot construisent `object.__new__(ReActLoop)`, ou les
attributs sont ABSENTS. Precalculer une valeur avait fait tomber 54 tests en
RF-4, par `AttributeError` levee avant tout garde.

Les appels sortants redescendent sur l'INSTANCE (`etat.X()`), jamais en appel
direct de module : les tests du depot monkeypatchent l'instance, et un appel
direct ferait perdre le patch **en silence** — 17 tests etaient tombes ainsi
en RF-7a.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from loguru import logger

from src.reasoning.plan_progress import (
    mission_evidence_finalizable,
    worker_evidence_finalizable,
)
from src.reasoning.react_config import Observation
# Meme objet que celui que `react.py` importe sous ce nom : identite preservee
# (invariant 12). C'est un frozenset, donc partage sans risque.
from src.runtime.execution_ledger import MUTATION_TOOLS as _LEDGER_MUTATION_TOOLS


@dataclass(frozen=True)
class EntreeMission:
    """Contrat d'etat du runtime missions — 4 lectures, 10 appels."""

    # ── les 4 attributs d'etat, en lecture paresseuse ──
    task_id: Callable[[], Any]
    orchestrateur: Callable[[], Any]
    ledger: Callable[[], Any]
    plan_taches: Callable[[], Any]
    # ── dispatch d'instance : ces appels DOIVENT redescendre sur l'objet ──
    est_run_mission: Callable[[], bool]
    #: forme STRICTE : leve sur un etat incomplet, la ou la precedente rend
    #: False. Les deux existent dans le code d'origine (invariant 3).
    est_run_mission_strict: Callable[[], bool]
    requete_originale: Callable[[], str]
    fichiers_autorises: Callable[[], list]
    tests_presents: Callable[[], str]
    est_worker_delegue: Callable[[], bool]
    orchestrateur_actif: Callable[[], bool]
    preuve_tests_verts: Callable[[], bool]
    preuve_navigateur: Callable[[], Any]
    drapeau_web: Callable[[], bool]
    drapeau_interaction: Callable[[], bool]
    drapeau_jeu: Callable[[], bool]
    interaction_prouvee: Callable[[], bool]
    #: `_objective_wants_browser` est defini DANS `react.py` : l'importer
    #: violerait l'invariant 2, il passe donc par l'entree.
    objectif_veut_navigateur: Callable[[Any], bool]
    #: RF-6b — lectures propres aux trois gates.
    ecrits_non_publies: Callable[[], list]
    dossier_mission: Callable[[], str]
    outils_ecriture_p2b: Callable[[], Any]
    chemin_ecriture_existe: Callable[[str], bool]
    vise_livrable_existant: Callable[[str, str, bool], bool]


def rf6a_is_mission_run(etat) -> bool:
    """True si cette boucle ReAct tourne DANS une mission (sous-agent), pas le chat.
    Le chat n'a pas de task_id (cf. logs `task=-`) ; une mission est un TaskRecord
    kind='mission' (cf. manager.create_mission). Double verrou → jamais le chat.
    Sert à relâcher le PLAN GUARD : en mission le seul contrat est le LIVRABLE, pas
    le plan AUTO-généré du worker (tâches-passerelle + matching outil↔tâche imparfait)."""
    if not etat.task_id() or not etat.orchestrateur():
        return False
    try:
        rec = etat.orchestrateur().get_task(etat.task_id())
        return bool(rec and (rec.get("metadata") or {}).get("kind") == "mission")
    except Exception:
        return False


def rf6a_mission_workspace_meta(etat) -> str:
    """LOT 2.1 — sous-dossier de scope mission lu dans la meta (posé par
    delegate_and_wait). "" hors mission ou si absent → résolution actuelle.
    Lu par tour (non caché) car le lead pose sa propre meta EN COURS de run."""
    if not etat.task_id() or not etat.orchestrateur():
        return ""
    try:
        rec = etat.orchestrateur().get_task(etat.task_id())
        return str(((rec or {}).get("metadata") or {}).get("mission_workspace") or "").strip()
    except Exception:
        return ""


def rf6a_mission_unpublished_writes(etat) -> list:
    """LOT Z24 — fichiers ecrits APRES la publication, donc hors livrable.

    Run « jeu 3D » (2026-08-19) : `write_file jeu-3d-monde-ouvert/README.md`
    a l'iteration 26, APRES le publish. Le livrable publie ne contient que
    CONTRAT.md, contract.json, index.html, script.js, style.css — et la
    mission a conclu « completed », sans un mot sur le README que l'objectif
    exigeait (« index.html, styles, scripts, instructions »).

    Deux faits deterministes, deja persistes cote a cote, jamais croises.

    Bornes (sous-detecter plutot que crier au loup) :
      - missions uniquement, et seulement apres une publication REUSSIE ;
      - une ecriture DANS le dossier publie est sur le disque, donc livree
        (meme doctrine DISK-GROUNDED que 2.12.C) : elle ne compte pas ;
      - basenames uniques, liste plafonnee.

    Les ecritures des SOUS-AGENTS ont leur propre ledger et ne figurent pas
    ici : un oubli de worker echappe a ce garde. Sous-detection assumee —
    c'est le bon sens de l'erreur, et `_cited_test_config` documente deja le
    cout inverse (« faux fantomes ») d'un croisement ledger trop large.
    """
    if not etat.est_run_mission():
        return []
    try:
        entries = etat.ledger().writes_after_last_publish()
    except Exception:
        return []
    if not entries:
        return []
    ws = ""
    try:
        rec = etat.orchestrateur().get_task(etat.task_id()) or {}
        ws = str((rec.get("metadata") or {}).get("published_workspace") or "").strip()
    except Exception:
        ws = ""
    ws_norm = ws.replace("\\", "/").strip("/").lower()
    out: list = []
    for e in entries:
        raw = str(e.target or "").replace("\\", "/").strip()
        if not raw:
            continue
        # Ecrit DANS le dossier publie → present sur le disque, donc livre.
        if ws_norm and raw.strip("/").lower().startswith(ws_norm + "/"):
            continue
        base = os.path.basename(raw)
        if base and base not in out:
            out.append(base)
    return out[:8]


def rf6a_mission_routing_objective(etat) -> str:
    """Semantic mission objective, excluding lead/worker protocol prose."""
    if not etat.est_run_mission_strict() or not etat.task_id() or not etat.orchestrateur():
        return ""
    try:
        record = etat.orchestrateur().get_task(etat.task_id()) or {}
        metadata = record.get("metadata") or {}
        return str(
            metadata.get("routing_objective")
            or metadata.get("objective")
            or ""
        ).strip()
    except Exception:
        return ""


def rf6a_mission_tests_present_for_gate(etat) -> str:
    """LOT 2.10 — des tests existent-ils pour CE run de mission ? Retourne une
    courte justification (pour le log/steer) ou "". Sources bornées : fichiers
    écrits pendant le run (ledger), dossier mission (2.1), contract.json (2.2).
    Jamais de scan large (garde-fou P0.2)."""
    try:
        from src.reasoning.test_proof import (
            any_test_file, tests_present_in_dir, tests_present_in_contract,
        )
        if any_test_file(etat.ledger().written_basenames()):
            return "fichiers de test écrits pendant ce run"
        if etat.task_id() and etat.orchestrateur():
            rec = etat.orchestrateur().get_task(etat.task_id()) or {}
            mws = str((rec.get("metadata") or {}).get("mission_workspace") or "").strip()
            if mws:
                import json as _j210
                import os as _os210
                from src.utils.paths import WORKSPACE_DIR as _ws210
                d = str(_ws210 / mws)
                if tests_present_in_dir(d):
                    return f"tests dans {mws}"
                cj = _os210.path.join(d, "contract.json")
                if _os210.path.isfile(cj):
                    with open(cj, encoding="utf-8", errors="replace") as fh:
                        if tests_present_in_contract(_j210.load(fh)):
                            return "tests déclarés au contrat"
    except Exception:
        return ""
    return ""


def rf6a_mission_web_present_for_gate(etat) -> str:
    """LOT D — un livrable WEB existe-t-il pour CE run ? Retourne une courte
    justification (log/steer) ou "". Sources bornées, comme _mission_tests_
    present_for_gate (jamais de scan large, garde-fou P0.2) : fichiers écrits
    pendant le run (ledger), dossier mission (2.1), contract.json (2.2)."""
    _WEB = (".html", ".htm", ".js")
    try:
        import os as _osW
        for b in etat.ledger().written_basenames():  # déjà en minuscules
            if b.endswith(_WEB):
                return "page web écrite pendant ce run"
        if etat.task_id() and etat.orchestrateur():
            rec = etat.orchestrateur().get_task(etat.task_id()) or {}
            mws = str((rec.get("metadata") or {}).get("mission_workspace") or "").strip()
            if mws:
                from src.utils.paths import WORKSPACE_DIR as _wsW
                d = str(_wsW / mws)
                if _osW.path.isdir(d):
                    for name in _osW.listdir(d):
                        if name.lower().endswith(_WEB):
                            return f"page web dans {mws}"
                cj = _osW.path.join(d, "contract.json")
                if _osW.path.isfile(cj):
                    import json as _jW
                    with open(cj, encoding="utf-8", errors="replace") as fh:
                        data = _jW.load(fh)
                    for f in (data.get("files") or []):
                        if str(f.get("path") or "").lower().endswith(_WEB):
                            return "page web déclarée au contrat"
        # M6-colmatage (run MiniQuiz 2026-07-06, mission 1) — fabrication à
        # l'itération 1 SANS AUCUNE mutation : ledger vide, pas de dossier
        # mission, pas de contrat → les deux sources ci-dessus rendent "" et
        # le mensonge « ✅ Navigateur : titre visible » sortait sans bannière.
        # 3e source : l'OBJECTIF de mission demande explicitement un livrable
        # web (borné mission). 2.9.A : négation-aware — « pas de navigateur »
        # / « API sans interface » ne comptent PLUS comme objectif web.
        if etat.est_run_mission() and etat.objectif_veut_navigateur(
            etat.requete_originale()
        ):
            return "objectif web explicite"
    except Exception:
        return ""
    return ""


def rf6a_mission_js_present_for_gate(etat) -> str:
    """LOT 2.4 (run MotDuJour) — un livrable JS existe-t-il pour CE run ?
    Sources bornées (patron _mission_web_present_for_gate, garde-fou P0.2) :
    fichiers écrits au ledger, contract.json de la mission. "" sinon."""
    try:
        import os as _osJ
        for b in etat.ledger().written_basenames():
            if b.endswith((".js", ".mjs")):
                return "JS écrit pendant ce run"
        if etat.task_id() and etat.orchestrateur():
            rec = etat.orchestrateur().get_task(etat.task_id()) or {}
            mws = str((rec.get("metadata") or {}).get("mission_workspace") or "").strip()
            if mws:
                from src.utils.paths import WORKSPACE_DIR as _wsJ
                cj = _osJ.path.join(str(_wsJ / mws), "contract.json")
                if _osJ.path.isfile(cj):
                    import json as _jJ
                    with open(cj, encoding="utf-8", errors="replace") as fh:
                        data = _jJ.load(fh)
                    for f in (data.get("files") or []):
                        if str(f.get("path") or "").lower().endswith((".js", ".mjs")):
                            return "JS déclaré au contrat"
    except Exception:
        return ""
    return ""


def rf6a_worker_codeagent_first_gate(etat, tool_name: str, tool_args: Optional[dict] = None):
    """Require one real CodeAgent attempt before a contract worker hand-codes.

    LOT I already injects ``CODE PAR DÉLÉGATION`` into every contracted code
    worker, but the RévizIA/AtelierAir runtime runs proved that a model can
    ignore that prompt and call ``edit_file`` directly.  This narrow gate
    turns the documented policy into an invariant without removing the
    fallback: as soon as ``delegate_task`` (or its background variant) has
    actually been attempted, successfully or not, direct mutation is allowed.

    It is intentionally inert for the chat, the mission lead, non-code
    workers, legacy workers without the marker, and CodeAgent itself.
    """
    normalized_tool = str(tool_name or "")
    if normalized_tool in {"delegate_task", "delegate_task_bg"}:
        return None
    if normalized_tool not in _LEDGER_MUTATION_TOOLS:
        return None
    if not etat.est_run_mission():
        return None
    code_ext = (".py", ".html", ".css", ".js", ".ts", ".jsx", ".tsx", ".vue", ".svelte")
    owned = list(etat.fichiers_autorises() or [])
    is_lead = not owned
    if is_lead:
        # LOT Z1b — DÉCISION UTILISATEUR (2026-08-15) : « il faudrait qu'il
        # utilise le CodeAgent si c'est du dev ». Le garde sortait ici, et
        # informer le lead n'a rien changé — mesuré deux fois :
        #   HuffPack  50 read_file · 5 éditions à la main → 12 tests ROUGES
        #   Cadence   20 read_file · 6 éditions          → 14/15, 1 cas limite
        # Le lot Z1 lui avait pourtant ajouté « Ne PAS découper ≠ coder à la
        # main » : consigne reçue (vérifiée au log), zéro delegate_task. C'est
        # exactement la leçon du LOT I côté workers — un prompt se contourne,
        # un rail tient.
        # Le lead n'a pas de fichiers assignés : on juge donc le fichier qu'il
        # VISE. Un .md, un .json, un .csv passent — seul le CODE est concerné.
        cible = ""
        for cle in ("file_path", "path", "filename"):
            valeur = (tool_args or {}).get(cle)
            if valeur:
                cible = str(valeur)
                break
        if not cible.lower().endswith(code_ext):
            return None
    else:
        if not any(str(path or "").lower().endswith(code_ext) for path in owned):
            return None
        objective = str(etat.requete_originale() or "")
        if "CODE PAR DÉLÉGATION" not in objective:
            return None
    attempted = any(
        entry.action in {"delegate_task", "delegate_task_bg"}
        for entry in etat.ledger().recent(max(1, etat.ledger().size))
    )
    if attempted:
        return None
    logger.warning(
        "[CODEAGENT-FIRST] mutation '{}' refusée avant delegate_task. task={} "
        "role={} files={}",
        tool_name, etat.task_id(), "lead" if is_lead else "worker", owned or cible,
    )
    if is_lead:
        return Observation(
            content=(
                "⛔ CODEAGENT-FIRST — écrire du code passe par le CodeAgent. "
                "Appelle d'abord `delegate_task(description='<ce qu'il y a à "
                "coder>', agent_type='code')` : il a le harnais (plan, édition "
                "ciblée, exécution, réparation) que tu n'as pas en direct. "
                "Une mutation directe redevient autorisée APRÈS cette tentative "
                "réelle — si le CodeAgent échoue, reprends immédiatement "
                "toi-même avec edit_file/apply_patch. Tu restes responsable de "
                "VÉRIFIER par une exécution et de conclure."
            ),
            success=False,
        )
    return Observation(
        content=(
            "⛔ CODEAGENT-FIRST — tu es un worker de code contractuel. Appelle "
            "d'abord `delegate_task(description='remplis tes fichiers selon "
            "CONTRAT.md', agent_type='code')`. Une mutation directe ne devient "
            "autorisée qu'APRÈS cette tentative réelle ; si CodeAgent échoue, "
            "reprends immédiatement toi-même avec edit_file/apply_patch afin de "
            "livrer toutes les exigences."
        ),
        success=False,
    )


def rf6a_mission_completion_evidence(etat) -> Dict[str, Any]:
    """Authoritative complete-only proof snapshot for a mission run."""
    facts: Dict[str, Any] = {
        "complete": False,
        "scope": "",
        "delivery_proven": False,
        "delegation_complete": False,
        "tests_required": False,
        "tests_green": False,
        "browser_required": False,
        "browser_proven": False,
    }
    if not etat.est_run_mission_strict() or not etat.orchestrateur_actif():
        return facts
    try:
        rec = etat.orchestrateur().get_task(etat.task_id()) or {}
        meta = rec.get("metadata") or {}
        owned = list(etat.fichiers_autorises() or [])
        if owned:
            from src.reasoning.test_proof import any_test_file
            from src.subagents.mission_contract import inspect_worker_deliverables
            from src.utils.paths import WORKSPACE_DIR

            mission_workspace = str(meta.get("mission_workspace") or "").strip()
            inspected = inspect_worker_deliverables(
                WORKSPACE_DIR / mission_workspace,
                owned,
            ) if mission_workspace else {
                "ready": False,
                "assigned": owned,
                "missing": [],
                "stubs": [],
                "invalid": ["mission_workspace"],
            }
            latest_test = etat.ledger().last_test_outcome()
            if not isinstance(latest_test, dict):
                latest_test = meta.get("last_test_outcome") or {}
            tests_green = bool(latest_test.get("green"))
            if etat.ledger().has_source_mutation():
                tests_green = etat.preuve_tests_verts()
            facts.update({
                "scope": "worker",
                "delivery_proven": bool(inspected.get("ready")),
                "delegation_complete": bool(inspected.get("ready")),
                "tests_required": bool(any_test_file(owned)),
                "tests_green": tests_green,
                "browser_required": False,
                "browser_proven": True,
                "assigned_files": list(inspected.get("assigned") or []),
                "missing_files": list(inspected.get("missing") or []),
                "stub_files": list(inspected.get("stubs") or []),
                "invalid_files": list(inspected.get("invalid") or []),
            })
            facts["complete"] = worker_evidence_finalizable(
                etat.plan_taches(),
                assigned_files_ready=facts["delivery_proven"],
                tests_required=facts["tests_required"],
                tests_green=facts["tests_green"],
            )
            return facts

        facts["scope"] = "lead"
        facts["delivery_proven"] = bool(
            meta.get("mission_published") or etat.ledger().has_published()
        )

        children = list(meta.get("children") or [])
        if not children:
            facts["delegation_complete"] = True
        else:
            progress = meta.get("last_delegate_progress") or {}
            facts["delegation_complete"] = bool(
                progress.get("total") == len(children)
                and progress.get("done") == len(children)
                and not progress.get("failed")
                and not progress.get("cancelled")
                and not progress.get("timed_out")
            )

        facts["tests_required"] = bool(etat.tests_presents())
        latest_test = etat.ledger().last_test_outcome()
        if not isinstance(latest_test, dict):
            latest_test = meta.get("last_test_outcome") or {}
        facts["tests_green"] = bool(latest_test.get("green"))
        if etat.ledger().has_source_mutation():
            facts["tests_green"] = etat.preuve_tests_verts()

        facts["browser_required"] = bool(etat.drapeau_web())
        if facts["browser_required"]:
            if etat.drapeau_interaction() or etat.drapeau_jeu():
                facts["browser_proven"] = etat.interaction_prouvee()
            else:
                facts["browser_proven"] = bool(
                    etat.preuve_navigateur()
                    or (
                        meta.get("web_runtime_verified")
                        and not etat.ledger().has_source_mutation()
                    )
                )
        else:
            facts["browser_proven"] = True

        facts["complete"] = mission_evidence_finalizable(
            etat.plan_taches(),
            delivery_proven=facts["delivery_proven"],
            delegation_complete=facts["delegation_complete"],
            tests_required=facts["tests_required"],
            tests_green=facts["tests_green"],
            browser_required=facts["browser_required"],
            browser_proven=facts["browser_proven"],
        )
        return facts
    except Exception as exc:
        logger.debug("[M106] completion evidence unavailable: {}", exc)
        return facts


def rf6a_mission_allowed_files_meta(etat) -> list:
    """LOT 2.3 — liste des fichiers assignés à CE worker (meta `allowed_files`,
    posée par delegate_and_wait). [] hors mission ou si absent → aucune restriction."""
    if not etat.task_id() or not etat.orchestrateur():
        return []
    try:
        rec = etat.orchestrateur().get_task(etat.task_id())
        files = ((rec or {}).get("metadata") or {}).get("allowed_files")
        return list(files) if isinstance(files, (list, tuple)) else []
    except Exception:
        return []


def rf6a_mission_worker_delivered(etat) -> bool:
    """I3 — ce worker a-t-il rempli TOUS ses fichiers assignés ? (fait DISQUE)

    Run « comparatif vectoriel » (2026-08-13) : `w_qdrant` a été marqué
    `failed` sur `final_answer_potentially_incomplete` — sa phrase de
    conclusion était tronquée — alors que `rapport_qdrant.md` était **écrit,
    complet et exact** (ses données se retrouvent intégralement dans le
    comparatif final). Le verdict jugeait la FORME du final, jamais le
    TRAVAIL accompli.

    `inspect_worker_deliverables` répond déjà à la question, sur des faits
    durs : chaque fichier assigné existe, est non vide, et n'est plus le stub
    du contrat. Il n'y a donc aucune porte ouverte à la fabrication.

    False dès qu'un doute existe (pas un worker, pas de périmètre, pas de
    workspace) → comportement historique conservé.
    """
    try:
        if not etat.est_run_mission():
            return False
        owned = list(etat.fichiers_autorises() or [])
        if not owned:
            return False  # lead, ou porteur d'EFFETS (rien à inspecter ici)
        meta = ((etat.orchestrateur().get_task(etat.task_id()) or {})
                .get("metadata") or {})
        mws = str(meta.get("mission_workspace") or "").strip()
        if not mws:
            return False
        from src.subagents.mission_contract import inspect_worker_deliverables
        from src.utils.paths import WORKSPACE_DIR as _WS_I3
        return bool(
            inspect_worker_deliverables(_WS_I3 / mws, owned).get("ready")
        )
    except Exception:
        return False


def rf6a_mission_lead_delivered(etat) -> list:
    """LOT Z28 — le LEAD a-t-il produit quelque chose qui existe sur le disque ?

    I3 sauve un WORKER dont tous les fichiers assignés sont remplis. Mais
    `_mission_worker_delivered()` sort sur `if not owned: return False` — un
    lead n'a JAMAIS de fichiers assignés, donc I3 ne le couvre jamais.

    Run « Papier Cousu » (2026-08-19), mesuré :

        17:59  6 fichiers écrits (index/savoir-faire/contact/css/js/README)
        18:02  serveur lancé, 95 % des classes CSS couvertes
        18:02→18:03  les 3 pages ouvertes et vues au navigateur
        18:04  ACTION: final ×4 → « THOUGHT leaké » 1/3, 2/3, 3/3
        18:04  état = FAILED (final_answer_potentially_incomplete)

    Le site était complet, sombre, avec l'animation et le README. Le verdict
    jugeait la MISE EN FORME de la conclusion, jamais le travail. Après
    45 lots à empêcher d'affirmer un succès non prouvé, c'est l'inverse :
    annoncer un échec qui n'a pas eu lieu.

    Fait DISQUE, aucune inférence : on rassemble les chemins que le run a
    laissés (cibles de mutations + chemins rangés dans les `proof`, cf. Z28
    côté ledger + workspace publié/mission), on ne garde que ceux qui
    EXISTENT et ne sont pas vides.

    ⚠️ Le repérage par cibles seules ne suffisait pas, et c'est mesuré :
    `create_project` reçoit `description`/`project_name`, jamais un `path` —
    `_extract_target` renvoie None. Sans le volet ledger de Z28, cette
    méthode aurait renvoyé [] sur le run même qu'elle doit sauver.

    Liste vide dès qu'un doute existe (pas une mission, un worker, rien sur
    le disque) → l'état `failed` historique est conservé.
    """
    try:
        if not etat.est_run_mission():
            return []
        if list(etat.fichiers_autorises() or []):
            return []  # worker : c'est I3 qui décide, pas nous
        led = etat.ledger()
        if not led.successful_mutations():
            return []  # rien n'a été fait : l'échec est réel
    except Exception:
        return []

    from src.utils.paths import WORKSPACE_DIR as _WS_Z28

    candidats: list = []

    def _ajoute(brut) -> None:
        txt = str(brut or "").strip()
        if txt:
            candidats.append(txt)

    try:
        for e in led.successful_mutations():
            _ajoute(e.target)
            if e.proof:
                from src.runtime.execution_ledger import _PATH_IN_TEXT_RE
                for m in _PATH_IN_TEXT_RE.finditer(str(e.proof)):
                    _ajoute(next((g for g in m.groups() if g), ""))
    except Exception:
        pass
    try:
        meta = ((etat.orchestrateur().get_task(etat.task_id()) or {})
                .get("metadata") or {})
        _ajoute(meta.get("published_workspace"))
        _ajoute(meta.get("mission_workspace"))
    except Exception:
        pass

    vus: list = []
    for brut in candidats:
        for base in (Path(brut), _WS_Z28 / brut, Path.cwd() / brut):
            try:
                if not base.exists():
                    continue
                if base.is_dir():
                    if not any(base.iterdir()):
                        continue
                elif base.stat().st_size <= 0:
                    continue
            except Exception:
                continue
            chemin = str(base)
            if chemin not in vus:
                vus.append(chemin)
            break
    return vus[:6]


def rf6a_mission_expects_file_deliverables(etat):
    """H8 — cette mission attend-elle des livrables FICHIERS ? (None = inconnu)

    Lu du contrat : `files` non vide ⇒ True ; contrat d'EFFETS purs ⇒ False.
    `None` quand il n'y a pas de contrat — les gardes gardent alors leur
    comportement historique, aucune mission existante n'est affectée.

    Sert à éteindre les gardes conçus pour le CODE sur une mission d'actions :
    au run n°3, un mémo décrivant `pyproject.toml` (« publication sur PyPI »)
    a déclenché la bannière « Non publié » sur une mission qui n'avait rien à
    publier.
    """
    try:
        if not (etat.task_id() and etat.orchestrateur()):
            return None
        meta = ((etat.orchestrateur().get_task(etat.task_id()) or {})
                .get("metadata") or {})
        mws = str(meta.get("mission_workspace") or "").strip()
        if not mws:
            return None
        from src.utils.paths import WORKSPACE_DIR as _WS_H8
        cj = _WS_H8 / mws / "contract.json"
        if not cj.exists():
            return None
        data = json.loads(cj.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        return bool(data.get("files"))
    except Exception:
        return None


def rf6a_is_worker_run(etat) -> bool:
    """H4 — « ce run est-il un worker (pas le lead) ? », la vraie question.

    Historiquement posée `if etat.fichiers_autorises():`, ce qui
    définissait un worker par ses FICHIERS. Un porteur d'effets purs n'en a
    aucun : neuf gardes réservés au lead (BROWSER GATE, CONTRACT GATE, verdicts
    runtime web, gate JS, flags jeu/interaction) se retournaient contre lui.

    Sûr par construction : un worker de code a déjà un périmètre (inchangé), le
    lead n'a jamais de `parent_id` (inchangé). Seul le cas neuf bascule.
    """
    return bool(etat.fichiers_autorises()) or etat.est_worker_delegue()


def rf6a_is_delegated_worker(etat) -> bool:
    """H4 — CE run est-il un worker délégué (par opposition au lead) ?

    La question était jusqu'ici posée sous la forme `if allowed_files:`, ce qui
    revenait à dire « un worker, c'est quelqu'un qui possède des fichiers ».
    Un porteur d'EFFETS purs n'en possède aucun (`allowed_files: []`) : il était
    donc pris pour le lead, et recevait des politiques qui ne le concernent pas.
    Prouvé au run `veille_python_313` (2026-08-13) : `w_recherche` a reçu la
    bannière « Navigateur NON vérifié » — policy réservée au TOP-LEAD — sur une
    mission de veille documentaire.

    Le fait déterministe est `metadata.parent_id`, posé par `delegate_and_wait`
    pour TOUT enfant, avec ou sans fichiers. Défensif : False sur erreur (on ne
    transforme jamais un lead en worker par accident).
    """
    if not etat.task_id() or not etat.orchestrateur():
        return False
    try:
        meta = ((etat.orchestrateur().get_task(etat.task_id()) or {})
                .get("metadata") or {})
        return bool(meta.get("parent_id") or meta.get("delegation_owner"))
    except Exception:
        return False


# ══════════════════════════════════════════════════════════════════════════
#  Lot RF-6b — DECISIONS des trois gates mission
#
#  Les trois methodes d'origine ont la meme forme :
#
#      <gardes>          -> None
#      if tirs >= 1      -> None
#      <decision metier> -> None si aucune raison
#      tirs += 1                          <-- MUTATION
#      logger.warning(...)
#      return Observation(...)            <-- ou affectation du guidance
#
#  Seule la partie AU-DESSUS de la mutation est extraite. Les fonctions
#  ci-dessous rendent le CONTENU, jamais une `Observation` : si elles la
#  construisaient, elle naitrait AVANT la mutation — et son horodatage avec
#  (invariant 16).
# ══════════════════════════════════════════════════════════════════════════


def rf6b_decision_nudge_ecrits_non_publies(etat, deja_tire: bool):
    """Z24 — faut-il prevenir MAINTENANT que des fichiers sont hors livrable ?

    Rend `(liste, guidance)` ou None. Le drapeau `_z24_nudged` et
    l'affectation de `_pending_loop_guidance` restent dans `ReActLoop`.
    """
    if deja_tire:
        return None
    try:
        manquants = etat.ecrits_non_publies()
    except Exception:
        return None
    if not manquants:
        return None
    liste = ", ".join(f"`{m}`" for m in manquants)
    return liste, (
        f"\u26a0\ufe0f {liste} : tu as écrit ce(s) fichier(s) APRÈS avoir publié. "
        "La publication a figé un instantané — ils ne sont donc PAS dans le "
        "livrable, et l'utilisateur ne les verra pas.\n\n"
        "Si tu veux les livrer, rappelle `publish_mission_workspace`. Sinon, "
        "dis-le explicitement dans ta réponse finale. Ne conclus pas en "
        "laissant croire qu'ils sont livrés."
    )


def rf6b_decision_ecrasement_livrable(etat, tool_name, tool_args, tirs: int):
    """P2b — l'outil reecrit-il en place un livrable deja livre ?

    Rend `(cible, dossier, contenu)` ou None. `_overwrite_gate_shots` reste
    dans `ReActLoop`.
    """
    if not etat.est_run_mission_strict():
        return None
    if tool_name not in etat.outils_ecriture_p2b():
        return None
    if tirs >= 1:
        return None
    ws = etat.dossier_mission()
    if not ws:
        return None
    args = tool_args or {}
    target = str(args.get("path") or args.get("file_path") or "").strip()
    if not target:
        return None
    exists = etat.chemin_ecriture_existe(target)
    if not etat.vise_livrable_existant(target, ws, exists):
        return None
    return target, ws, (
        f"\u26d4 `{target}` existe déjà et se trouve HORS de ton dossier de "
        f"mission (`{ws}`). L'écrire en place, c'est modifier un livrable "
        "livré sans filet : si tu échoues ensuite, il reste cassé.\n\n"
        f"Copie d'abord ce que tu dois modifier dans `{ws}`, travaille "
        "là-bas, vérifie par une exécution réelle, puis publie avec "
        "`publish_mission_workspace` quand c'est vert.\n\n"
        "(Redirection unique : si tu rappelles cet outil, il s'exécutera.)"
    )


def rf6b_decision_intention_mission_chat(etat, tool_name, tirs: int):
    """O2 — au CHAT, l'utilisateur a-t-il demande une MISSION a echeance ?

    Rend le contenu ou None. `_chat_mission_gate_shots` reste dans `ReActLoop`.
    """
    if etat.est_run_mission_strict():
        return None
    if tool_name == "create_mission":
        return None
    if tirs >= 1:
        return None
    from .final_guards import chat_requests_background_mission

    if not chat_requests_background_mission(etat.requete_originale() or ""):
        return None
    return (
        "\u26d4 L'utilisateur a demandé une MISSION avec une échéance — donc un "
        "travail en arrière-plan, avec son propre budget de temps et ses "
        "sous-agents. Ne le fais PAS ici au fil de la conversation.\n\n"
        "Appelle `create_mission(objective=..., deadline=...)` en reprenant "
        "l'objectif et l'échéance tels qu'il les a écrits, puis annonce-lui "
        "que c'est lancé et TERMINE ton tour.\n\n"
        "(Redirection unique : si tu rappelles cet outil ensuite, il "
        "s'exécutera.)"
    )
