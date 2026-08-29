"""Runtime de progression du plan ReAct.

Lot RF-4 du plan `plans/REFACTOR_REACT_DEDICATED_PLAN_2026-08-27.md`.
Le corps de `ReActLoop._update_plan_progress` (847 lignes) vit ici. `react.py`
conserve une coquille qui porte la sortie anticipee, la seule mutation de
`self` et l'emission de l'etat du plan.

--- Ce module n'importe PAS `react.py` (invariant 2) ---

Les 36 noms libres du corps viennent tous d'ailleurs : `plan_progress` (15),
`plan_evidence` (11), `browser_reasoning` (3), `react_config`,
`delegate_strategy`, `hallucination_guard`, plus la bibliotheque standard.
Deux attaches a `react.py` subsistaient, et AUCUNE des deux n'est un `self.X` :

- `ReActLoop._document_route_for_run(self)` — `self` passe en argument nu a une
  methode non liee ;
- `ReActLoop._document_plan_required_kinds(task.description)` — appel sur la
  CLASSE, sans `self` du tout, donc invisible a un balayage de `self`. C'est la
  fermeture de noms libres qui l'a trouve, pas le balayage d'attributs.

Les deux passent en appelables ; `_document_plan_required_kinds` reste un
`@staticmethod` de `ReActLoop` (invariant 13) et ses autres consommateurs ne
bougent pas.

--- Pourquoi des appelables et pas seulement des valeurs ---

Deux raisons, mesurees et non supposees.

**1. Les instances de test n'ont pas ces attributs.** Une vingtaine de tests
construisent la boucle par `object.__new__(ReActLoop)` : `__init__` n'est jamais
appele, et `tools`, `task_id`, `execution_ledger`, `task_orchestrator` sont
REELLEMENT absents. L'original ne les touchait jamais sur ces scenarios, tous
etant lus au fond de branches gardees. Les lire a la construction de l'entree
levait un `AttributeError` avant tout garde : 54 tests ciblés sont tombes.
Chaque lecture garde donc sa forme d'origine ET sa paresse.

**2. Trois des etats lus ne sont pas des attributs mais des `property` :**

- `_is_mission_run` interroge `task_orchestrator.get_task(task_id)`, et il est
  lu DERRIERE un court-circuit `elif`. Le pre-calculer ferait un acces
  orchestrateur a chaque appel, y compris quand la branche n'est jamais
  atteinte ;
- `_last_auto_advance_iter` appelle `_ensure_exec_state()` en lecture ET en
  ecriture, via un setter. La forme du descripteur fait partie du contrat
  (invariant 13) : lire et ecrire passent donc par des appelables definis dans
  `react.py`, ce qui laisse la mutation chez `ReActLoop` (invariant 5) ;
- `_orchestrator_enabled` est une methode.

`self` n'est jamais passe a ce module.

--- Ce module ne recree AUCUNE regle ---

`plan_progress.py` et `plan_evidence.py` restent les moteurs purs. Ce module
les orchestre, il ne les duplique pas.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List

from loguru import logger

from .browser_reasoning import (
    _browser_observation_has_failure,
    _browser_observation_is_auxiliary_action,
    _looks_like_chat_transcript,
)
from .delegate_strategy import _is_post_codeagent_closure_task
from .hallucination_guard import _HINT_ONLY_PROOF_REQUIRED_TOOLS
from .plan_evidence import (
    _BUSINESS_ACTION_STARTERS_NORMALIZED,
    _EXPLORATION_TOOLS_STRICT,
    _SEQ_FALLBACK_BLOCKLIST,
    _normalize_guard_token,
    classify_observation,
    evaluate_task_proof,
    has_sufficient_proof,
    is_verify_task,
    is_peer_delegation_success as _is_peer_delegation_success,
    task_completion_status,
    verify_satisfied_by_artifact_read,
)
from .plan_progress import (
    _BROWSER_PLAN_PASSIVE_TOOLS,
    _READ_ONLY_DISCOVERY_PLAN_TOOLS,
    _browser_passive_tool_can_complete_task,
    _read_only_discovery_tool_can_complete_task,
    artifact_target_task_blocks,
    browser_interaction_task_blocks,
    browser_verify_task_blocks,
    correction_task_blocks_readonly,
    delegation_task_blocks,
    document_plan_tool_can_complete_task,
    publish_task_blocks,
    pytest_execution_task,
    pytest_plan_task_proven,
    sourced_web_research_task_proven,
    tool_explicit_task_blocks,
)
from .react_config import _TOOL_COMPLETION_HINTS


@dataclass(frozen=True)
class EntreeProgressionPlan:
    """Tout ce dont le corps a besoin, sans `self`.

    Valeurs pour ce qui est deja calcule ; appelables pour ce qui doit rester
    paresseux (`est_run_mission`, `obtenir_route_document`) ou passer par un
    descripteur (`lire_derniere_avance`, `definir_derniere_avance`).
    """

    tool_name: str
    tool_args: Dict[str, Any]
    observation_content: str
    iteration: int
    allow_fallback: bool

    task_plan: List[Any]

    obtenir_outils: Callable[[], Any]
    obtenir_task_id: Callable[[], Any]
    obtenir_ledger: Callable[[], Any]
    obtenir_ledger_optionnel: Callable[[], Any]
    obtenir_orchestrateur: Callable[[], Any]

    est_run_mission: Callable[[], bool]
    orchestrateur_actif: Callable[[], bool]
    lire_derniere_avance: Callable[[], Any]
    definir_derniere_avance: Callable[[Any], None]
    obtenir_route_document: Callable[[], Any]
    types_documents_requis: Callable[[str], Any]


def appliquer_progression_plan(e: EntreeProgressionPlan) -> None:
    """Coche les taches du plan completees par l'outil execute.

    Corps deplace quasi verbatim depuis `ReActLoop._update_plan_progress`.
    La sortie anticipee sur plan vide et l'emission de l'etat restent dans la
    coquille de `react.py`.
    """
    tool_name = e.tool_name
    tool_args = e.tool_args
    observation_content = e.observation_content
    iteration = e.iteration
    allow_fallback = e.allow_fallback


    try:
        _doc_route_for_plan = e.obtenir_route_document()
        _compound_document_workflow = len(
            tuple(getattr(_doc_route_for_plan, "workflow_actions", ()) or ())
        ) > 1
    except Exception:
        _compound_document_workflow = False

    # Signaux d'échec : si l'observation contient un marqueur d'erreur, ne rien cocher
    obs_lower = (observation_content or "").lower()
    _fail, _overridden = classify_observation(observation_content)
    # ❌ seul n'est PAS un marqueur d'échec — voir plan_evidence._FAIL_MARKERS.
    observation_has_failure = _fail and not _overridden
    observation_has_failure = observation_has_failure or _browser_observation_has_failure(
        tool_name,
        observation_content,
    )

    hints = _TOOL_COMPLETION_HINTS.get(tool_name, [])
    tool_lower = tool_name.lower()
    tool_module_category = ""
    tool_semantic_category = ""
    try:
        tool_module_category = e.obtenir_outils().get_tool_module_category(tool_name)
        tool_semantic_category = e.obtenir_outils().get_tool_semantic_category(tool_name)
    except Exception:
        pass
    # Guard 5 pré-calculé : si l'outil est exploratoire, aucune tâche métier
    # ne peut être marquée par aucune voie (sem, seq, auto).
    _is_exploration_for_guard5 = tool_name in _EXPLORATION_TOOLS_STRICT

    # #3 (2026-06-30) — RELECTURE D'ARTEFACT : une lecture (read_file/read_document)
    # qui relit un fichier RÉELLEMENT écrit avant (mutation réussie dans le ledger)
    # est une preuve de VÉRIFICATION légitime. Lien read→write par basename (le write
    # cible un chemin relatif « workspace/x.md », le read un chemin absolu → on compare
    # le nom de fichier). Sinon : le garde-fou « lecture seule ≠ preuve » reste actif.
    _artifact_reread = False
    if (tool_name in ("read_file", "read_document")
            and not observation_has_failure
            and observation_content and observation_content.strip()
            and isinstance(tool_args, dict)):
        _rp = str(tool_args.get("path") or tool_args.get("file_path") or "").strip()
        _read_base = _rp.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        if _read_base and len(_read_base) >= 4:
            _led = e.obtenir_ledger_optionnel()
            if _led is not None and _led.has_mutation_for_target_hint(_read_base):
                _artifact_reread = True
            # #3b (2026-07-01) — FILET MISSION : l'execution_ledger est in-memory
            # et clear() à chaque _run_internal ; il ne survit PAS au checkpoint/resume
            # du worker de mission (run soirée-cinéma : write iter1 absent du ledger à
            # la relecture → verify SKIP → plan 1/2 → FINALIZE bloqué). Le signal fiable
            # est deadline_artifact_written, porté par task_orchestrator (SURVIT au
            # checkpoint, cf. 5.7.4a). Si la mission a CONFIRMÉ l'écriture de son artefact
            # cible et que la relecture porte sur CE fichier, la vérification est légitime.
            elif e.est_run_mission() and e.orchestrateur_actif():
                try:
                    from src.subagents.mission_budget import extract_target_file as _etf
                    _rec_rr = e.obtenir_orchestrateur().get_task(e.obtenir_task_id()) or {}
                    _meta_rr = _rec_rr.get("metadata") or {}
                    if _meta_rr.get("deadline_artifact_written"):
                        _tgt_rr = _etf(_meta_rr.get("objective")
                                       or _rec_rr.get("message_preview") or "")
                        if _tgt_rr:
                            _tgt_base = _tgt_rr.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
                            if _tgt_base and _tgt_base.lower() == _read_base.lower():
                                _artifact_reread = True
                except Exception:
                    pass

    _any_matched = False
    _has_specific_match = False  # True si au moins un arg/tool/obs match (pas juste hint)
    _completed_this_call = 0  # Limite le nombre de complétion par appel
    _MAX_COMPLETIONS_PER_CALL = 2  # garde-fou: un outil complète au max 2 tâches
    _SUBMIT_VERBS = ("soumett", "soumettre", "submit", "envoyer le formulaire",
                     "envoyer le form", "valider le formulaire", "valider le form",
                     "cliquer sur soumettre", "cliquer sur envoyer")
    _FINAL_ONLY_STARTS = (
        "confirmer à", "confirmer que", "confirmer le", "confirmer les",
        "rapporter", "informer l'", "informer le", "signaler le", "signaler les",
        "résumer les résultats", "résumer le résultat",
        "afficher le résultat", "afficher les résultats",
        "répondre à l'utilisateur",
    )
    _RESULT_CAPTURE_MARKERS = (
        "screenshot du résultat", "screenshot du resultat",
        "capture du résultat", "capture du resultat",
        "screenshot final", "capture finale",
        "screenshot du résultat final", "capture du résultat final",
    )
    _STRICT_SUBMIT_SUCCESS_MARKERS = (
        "soumis", "soumise", "soumission", "submitted",
        "envoyé", "envoyee", "envoyée", "envoye",
        "confirmation", "confirmé", "confirmee", "confirmée", "confirmed",
        "httpbin.org/post", "merci", "success", "réussi", "reussi",
        "formulaire envoy", "form submitted", "inscription réussie",
        "compte créé", "account created",
    )
    _STRICT_CAPTURE_SUCCESS_MARKERS = (
        "screenshot", "capture", "📸", ".png", ".jpg", ".jpeg", ".webp",
    )
    _CHAT_INTERACTION_MARKERS = (
        "interagir avec l'ia", "interagir avec une ia",
        "échanger avec l'ia", "echanger avec l'ia",
        "échanger avec une ia", "echanger avec une ia",
        "parler avec l'ia", "parler avec une ia",
        "discuter avec l'ia", "discuter avec une ia",
        "envoyer un message", "obtenir une réponse", "obtenir une reponse",
    )
    _CHAT_CONFIRM_MARKERS = (
        "confirmer l'échange", "confirmer l'echange",
        "échange réussi", "echange réussi", "echange reussi",
        "échange avec l'ia réussi", "echange avec l'ia reussi",
        "confirmer la réponse", "confirmer la reponse",
    )
    _STRICT_CHAT_SUCCESS_MARKERS = (
        "réponse", "reponse", "assistant", "a répondu", "a repondu",
        "message envoyé", "message envoye", "envoyé", "envoye",
        "reply", "responded", "conversation", "new message",
    )

    def _strip_plan_prefix(_desc_lower: str) -> str:
        return re.sub(
            r"^\s*(?:étape|etape|step)\s*\d+\s*[:\-]\s*",
            "",
            (_desc_lower or "").strip(),
            flags=re.IGNORECASE,
        ).strip()

    def _is_chat_interaction_task(_desc_lower: str) -> bool:
        _desc_guard = _strip_plan_prefix(_desc_lower)
        return any(marker in _desc_guard for marker in _CHAT_INTERACTION_MARKERS)

    def _is_final_only_task(_desc_lower: str) -> bool:
        _desc_guard = _strip_plan_prefix(_desc_lower)
        if any(_desc_guard.startswith(fos) for fos in _FINAL_ONLY_STARTS):
            return True
        return any(marker in _desc_guard for marker in _CHAT_CONFIRM_MARKERS)

    def _requires_strict_proof(_desc_lower: str) -> bool:
        _desc_guard = _strip_plan_prefix(_desc_lower)
        if any(sv in _desc_guard for sv in _SUBMIT_VERBS):
            return True
        if _is_final_only_task(_desc_guard):
            return True
        if any(marker in _desc_guard for marker in _RESULT_CAPTURE_MARKERS):
            return True
        return _is_chat_interaction_task(_desc_guard)

    def _has_strict_plan_proof(_desc_lower: str, _obs_lower: str) -> bool:
        _desc_guard = _strip_plan_prefix(_desc_lower)
        # P2P — une délégation de mission à un pair est prouvée par SON accusé
        # (« mission lancée / réf. ta- »), même si le nom de tâche contient le
        # mot « submit » (« via submit_peer_task ») qui sinon la ferait passer
        # pour une soumission de formulaire → FINAL bloqué → re-soumissions.
        if _is_peer_delegation_success(tool_name, observation_content or ""):
            return True
        if _browser_observation_is_auxiliary_action(tool_name, observation_content or ""):
            return False
        if _is_final_only_task(_desc_guard):
            return False
        if any(sv in _desc_guard for sv in _SUBMIT_VERBS):
            return any(marker in _obs_lower for marker in _STRICT_SUBMIT_SUCCESS_MARKERS)
        if any(marker in _desc_guard for marker in _RESULT_CAPTURE_MARKERS):
            return any(marker in _obs_lower for marker in _STRICT_CAPTURE_SUCCESS_MARKERS)
        if _is_chat_interaction_task(_desc_guard):
            if any(marker in _obs_lower for marker in _STRICT_CHAT_SUCCESS_MARKERS):
                return True
            return _looks_like_chat_transcript(observation_content or "")
        return True

    for task in e.task_plan:
        if task.completed:
            continue
        desc_lower = task.description.lower()
        desc_guard = _strip_plan_prefix(desc_lower)

        # Guard 5 : outil exploratoire ne peut jamais cocher une tâche métier
        if _is_exploration_for_guard5:
            _task_desc_norm = _normalize_guard_token(desc_lower)
            if any(
                _task_desc_norm == starter or _task_desc_norm.startswith(starter + " ")
                for starter in _BUSINESS_ACTION_STARTERS_NORMALIZED
            ):
                logger.debug(
                    "[PLAN] Guard 5 (sémantique): tâche métier '{}' non marquable par {} (iter {})",
                    task.description, tool_name, iteration,
                )
                continue  # ignore ce task, passe au suivant

        hint_match = any(h in desc_lower for h in hints)
        tool_match = tool_lower in desc_lower
        arg_match = False
        for key in ("path", "file_path", "url", "query", "code", "filename", "caption"):
            val = str(tool_args.get(key, ""))
            if val and len(val) > 3:
                short = val.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
                if short.lower() in desc_lower:
                    arg_match = True
                    break

        # Si échec détecté, ne pas marquer même avec hint/tool/arg match
        if observation_has_failure:
            continue

        # Guard SUBMIT-ONLY : les tâches de soumission ne peuvent être marquées
        # que par un clic (browser_click_index), pas par une saisie (browser_type_index).
        # Cause réelle : "formulaire" dans les hints de browser_type_index faisait matcher
        # "Soumettre le formulaire" alors qu'on était encore en train de remplir des champs.
        if tool_name == "browser_type_index" and any(sv in desc_guard for sv in _SUBMIT_VERBS):
            logger.debug(
                "[PLAN] Guard SUBMIT-ONLY: '{}' non marquable par browser_type_index (iter {})",
                task.description, iteration,
            )
            continue

        # Guard FINAL-ONLY : les tâches de rapport/confirmation ne doivent être
        # marquées qu'au moment du FINAL, pas par un outil browser.
        # Elles commencent par confirmer/rapporter/informer/signaler.
        if tool_name.startswith("browser_") and _is_final_only_task(desc_guard):
            logger.debug(
                "[PLAN] Guard FINAL-ONLY: '{}' non marquable par {} — réservé à FINAL (iter {})",
                task.description, tool_name, iteration,
            )
            continue

        # Guard CORRECTION-ONLY : une tâche « corriger / réparer / debug » ne
        # peut PAS être créditée par un outil de LECTURE (lire = diagnostiquer,
        # pas corriger). Seule une mutation (edit_file/write_file/apply_patch…)
        # la marque. Sinon FINALIZE prématuré avec des tests encore rouges.
        if correction_task_blocks_readonly(tool_name, desc_guard):
            logger.debug(
                "[PLAN] Guard CORRECTION-ONLY: '{}' non marquable par {} "
                "(lecture ≠ correction) (iter {})",
                task.description, tool_name, iteration,
            )
            continue

        # LOT 2.7 — Guard TOOL-EXPLICIT : une tâche qui NOMME un outil précis
        # (« … via write_mission_contract », « lance pytest ») n'est créditée
        # QUE par cet outil (run NoteFlash : create_mission avait coché
        # « Poser le contrat via write_mission_contract »).
        if tool_explicit_task_blocks(tool_name, desc_guard):
            logger.debug(
                "[PLAN] Guard TOOL-EXPLICIT: '{}' non marquable par {} (iter {})",
                task.description, tool_name, iteration,
            )
            continue

        if delegation_task_blocks(tool_name, desc_guard):
            logger.debug(
                "[PLAN] Guard DELEGATION-ONLY: '{}' non marquable par {} (iter {})",
                task.description, tool_name, iteration,
            )
            continue

        # C0.3b — Guard PUBLISH-ONLY : une tâche « publier le livrable » n'est
        # créditée QUE par publish_mission_workspace (run FrigoZen : cochée par
        # le write_file de style.css → FINALIZE prématuré).
        if publish_task_blocks(tool_name, desc_guard):
            logger.debug(
                "[PLAN] Guard PUBLISH-ONLY: '{}' non marquable par {} (iter {})",
                task.description, tool_name, iteration,
            )
            continue

        # LOT E — Guard BROWSER-ONLY : une tâche « vérifier le navigateur » n'est
        # créditée QUE par une action browser_* réelle (run CéramiShop : cochée
        # sur une pensée fabriquée avant tout browser_navigate).
        if browser_verify_task_blocks(tool_name, desc_guard):
            logger.debug(
                "[PLAN] Guard BROWSER-ONLY: '{}' non marquable par {} (iter {})",
                task.description, tool_name, iteration,
            )
            continue

        if browser_interaction_task_blocks(tool_name, desc_guard):
            logger.debug(
                "[PLAN] Guard INTERACTION-PROOF: '{}' non marquable par {} "
                "sans verifier strict/evaluate (iter {})",
                task.description, tool_name, iteration,
            )
            continue

        if artifact_target_task_blocks(tool_name, desc_guard, tool_args or {}):
            logger.debug(
                "[PLAN] Guard ARTIFACT-TARGET: '{}' incompatible avec la cible de {} "
                "(iter {})", task.description, tool_name, iteration,
            )
            continue

        if not document_plan_tool_can_complete_task(
            tool_name,
            task.description,
            tool_kind=str((tool_args or {}).get("kind", "")),
            required_kinds=e.types_documents_requis(task.description),
            compound_workflow=_compound_document_workflow,
        ):
            logger.debug(
                "[PLAN] Document batch/verify: '{}' attend le manifest complet (iter {})",
                task.description, iteration,
            )
            continue

        # Fallback: observation de succes + mot du nom d'outil dans la description
        # GF-1 : obs_match est une heuristique FAIBLE. Désactivée pour les sous-outils
        # parallel_tools (allow_fallback=False) : "profile" ⊂ "profiler X" matcherait
        # TOUTES les tâches "Profiler …" sans discriminer le fichier → cascade.
        obs_match = False
        if allow_fallback and not (hint_match or tool_match or arg_match) and observation_content:
            if "\u2705" in observation_content or "succes" in obs_lower or "créé" in obs_lower or "envoyé" in obs_lower:
                tool_words = tool_lower.replace("_", " ").split()
                if any(tw in desc_lower for tw in tool_words if len(tw) > 2):
                    obs_match = True

        is_specific = arg_match or tool_match or obs_match
        _proof_for_task = has_sufficient_proof(
            tool_name,
            observation_content,
            task.description,
            tool_module_category,
            tool_semantic_category,
        )
        if pytest_execution_task(task.description):
            _proof_for_task = _proof_for_task or pytest_plan_task_proven(
                task.description,
                tool_name,
                e.obtenir_ledger().last_test_outcome(),
            )
        if hint_match and not is_specific and tool_name in _HINT_ONLY_PROOF_REQUIRED_TOOLS and not _proof_for_task:
            logger.debug(
                "[PLAN] Hint-only bloqué: '{}' non marquable par {} sans preuve spécifique (iter {})",
                task.description, tool_name, iteration,
            )
            continue
        if hint_match or is_specific:
            if not sourced_web_research_task_proven(
                tool_name, task.description, observation_content
            ):
                logger.debug(
                    "[PLAN] Recherche sourcée sans URL suffisante: '{}' non marquable par {} (iter {})",
                    task.description, tool_name, iteration,
                )
                continue
            if (
                tool_name in _READ_ONLY_DISCOVERY_PLAN_TOOLS
                and not _read_only_discovery_tool_can_complete_task(tool_name, task.description)
            ):
                logger.debug(
                    "[PLAN] Outil découverte hors périmètre: '{}' non marquable par {} (iter {})",
                    task.description, tool_name, iteration,
                )
                continue
            if (
                tool_name in _BROWSER_PLAN_PASSIVE_TOOLS
                and not _browser_passive_tool_can_complete_task(tool_name, task.description)
            ):
                logger.debug(
                    "[PLAN] Browser passif hors périmètre: '{}' non marquable par {} (iter {})",
                    task.description, tool_name, iteration,
                )
                continue
            if (
                tool_name in _BROWSER_PLAN_PASSIVE_TOOLS
                and not _proof_for_task
            ):
                logger.debug(
                    "[PLAN] Browser passif sans preuve: '{}' non marquable par {} (iter {})",
                    task.description, tool_name, iteration,
                )
                continue
            # Hint-only (pas d'arg/tool/obs spécifique) → max 1 tâche par itération
            if not is_specific and _any_matched and not _has_specific_match:
                continue
            # Garde-fou: empêcher un seul outil de compléter trop de tâches d'un coup
            # (évite que edit_website marque 4 tâches "completed" à iter 4)
            if _completed_this_call >= _MAX_COMPLETIONS_PER_CALL:
                logger.debug(
                    "[PLAN] Limite {} completions atteinte, skip '{}' (iter {})",
                    _MAX_COMPLETIONS_PER_CALL, task.description, iteration,
                )
                break
            # Verify-gate : une tâche de vérification exige une preuve réelle.
            # Présence de fichiers, hint de tool, ou ✅ générique ne suffisent pas.
            # #3 : EXCEPTION — relecture d'un artefact réellement écrit avant.
            if (
                is_verify_task(desc_lower) and not _proof_for_task
                and not verify_satisfied_by_artifact_read(
                    tool_name, task.description, artifact_reread=_artifact_reread,
                )
            ):
                logger.debug(
                    "[PLAN] Verify-gate (sem): '{}' non marquable par {} — preuve insuffisante (iter {})",
                    task.description, tool_name, iteration,
                )
                continue
            if _requires_strict_proof(desc_lower) and not _proof_for_task:
                logger.debug(
                    "[PLAN] Strict-proof (sem): '{}' non marquable par {} — preuve insuffisante (iter {})",
                    task.description, tool_name, iteration,
                )
                continue
            if _requires_strict_proof(desc_lower) and not _has_strict_plan_proof(desc_lower, obs_lower):
                logger.debug(
                    "[PLAN] Strict-proof content (sem): '{}' non marquable par {} — observation insuffisante (iter {})",
                    task.description, tool_name, iteration,
                )
                continue
            _proof = evaluate_task_proof(task.description, tool_name, observation_content)
            task.completed = True
            task.completed_at_iteration = iteration
            task.completed_by_tool = tool_name
            task.completion_status = task_completion_status(
                tool_name, desc_lower, tool_semantic_category, tool_module_category,
            )
            task.completion_evidence = _proof.evidence_summary
            task.completion_confidence = _proof.confidence
            logger.info("[PROOF] '{}' — {} ({})", task.description[:50], _proof.evidence_kind, _proof.confidence)
            _any_matched = True
            _completed_this_call += 1
            if is_specific:
                _has_specific_match = True

    # ── Fallback séquentiel ────────────────────────────────────────────────────
    # Si aucun match sémantique n'a été trouvé mais l'outil a réussi (pas d'erreur),
    # marquer la PREMIÈRE tâche non complétée qui matche par mots-clés de l'outil.
    # Le break est DANS le if pour ne pas bloquer sur une tâche non-matchante
    # (ex: étape 2 "Identifier X" ne contient pas "scan" → continuer vers étape 3).
    _seq_matched = False
    # Les outils purement info/inspection ne peuvent pas cocher une tâche métier
    # via le fallback séquentiel : "config" dans get_lumena_config matcherait
    # faussement "Configurer les rôles" sans que rien n'ait été fait.
    _seq_fallback_blocked = tool_name in _SEQ_FALLBACK_BLOCKLIST
    if (
        allow_fallback  # GF-1 : désactivé pour les sous-outils parallel_tools
        and not _any_matched
        and not observation_has_failure
        and not _seq_fallback_blocked
        and not _browser_observation_is_auxiliary_action(tool_name, observation_content or "")
    ):
        tool_words = {w for w in tool_lower.replace("_", " ").split() if len(w) > 2}
        for task in e.task_plan:
            if not task.completed:
                desc_lower = task.description.lower()
                if tool_words and any(tw in desc_lower for tw in tool_words):
                    if not sourced_web_research_task_proven(
                        tool_name, task.description, observation_content
                    ):
                        logger.debug(
                            "[PLAN] Recherche sourcée seq sans URL suffisante: '{}' non marquable par {} (iter {})",
                            task.description, tool_name, iteration,
                        )
                        break
                    if not document_plan_tool_can_complete_task(
                        tool_name,
                        task.description,
                        tool_kind=str((tool_args or {}).get("kind", "")),
                        required_kinds=e.types_documents_requis(task.description),
                        compound_workflow=_compound_document_workflow,
                    ):
                        logger.debug(
                            "[PLAN] Document seq: '{}' attend le manifest complet (iter {})",
                            task.description, iteration,
                        )
                        break
                    if (
                        tool_name in _READ_ONLY_DISCOVERY_PLAN_TOOLS
                        and not _read_only_discovery_tool_can_complete_task(tool_name, task.description)
                    ):
                        logger.debug(
                            "[PLAN] Outil découverte seq hors périmètre: '{}' non marquable par {} (iter {})",
                            task.description, tool_name, iteration,
                        )
                        break
                    if tool_name.startswith("browser_") and _is_final_only_task(desc_lower):
                        logger.debug(
                            "[PLAN] Browser seq FINAL-ONLY: '{}' non marquable par {} (iter {})",
                            task.description, tool_name, iteration,
                        )
                        break
                    if (
                        tool_name in _BROWSER_PLAN_PASSIVE_TOOLS
                        and not _browser_passive_tool_can_complete_task(tool_name, task.description)
                    ):
                        logger.debug(
                            "[PLAN] Browser seq hors périmètre: '{}' non marquable par {} (iter {})",
                            task.description, tool_name, iteration,
                        )
                        break
                    if tool_name.startswith("browser_") and not has_sufficient_proof(
                        tool_name,
                        observation_content,
                        task.description,
                        tool_module_category,
                        tool_semantic_category,
                    ):
                        logger.debug(
                            "[PLAN] Browser seq sans preuve: '{}' non marquable par {} (iter {})",
                            task.description, tool_name, iteration,
                        )
                        break
                    # Verify-gate : le fallback séquentiel n'est pas une preuve réelle.
                    if is_verify_task(desc_lower) and not has_sufficient_proof(
                        tool_name,
                        observation_content,
                        task.description,
                        tool_module_category,
                        tool_semantic_category,
                    ):
                        logger.debug(
                            "[PLAN] Verify-gate (seq): '{}' non marquable par {} — preuve insuffisante (iter {})",
                            task.description, tool_name, iteration,
                        )
                        break
                    if _requires_strict_proof(desc_lower) and not has_sufficient_proof(
                        tool_name,
                        observation_content,
                        task.description,
                        tool_module_category,
                        tool_semantic_category,
                    ):
                        logger.debug(
                            "[PLAN] Strict-proof (seq): '{}' non marquable par {} — preuve insuffisante (iter {})",
                            task.description, tool_name, iteration,
                        )
                        break
                    if _requires_strict_proof(desc_lower) and not _has_strict_plan_proof(desc_lower, obs_lower):
                        logger.debug(
                            "[PLAN] Strict-proof content (seq): '{}' non marquable par {} — observation insuffisante (iter {})",
                            task.description, tool_name, iteration,
                        )
                        break
                    _proof = evaluate_task_proof(task.description, tool_name, observation_content)
                    task.completed = True
                    task.completed_at_iteration = iteration
                    task.completed_by_tool = f"{tool_name}:seq"
                    task.completion_status = task_completion_status(
                        tool_name, desc_lower, tool_semantic_category, tool_module_category,
                    )
                    task.completion_evidence = _proof.evidence_summary
                    task.completion_confidence = _proof.confidence
                    _seq_matched = True
                    logger.debug(
                        "[PLAN] Fallback séquentiel: '{}' marquée via {} (iter {})",
                        task.description, tool_name, iteration,
                    )
                    break

    # ── Fallback avancement automatique ────────────────────────────────────────
    # Si AUCUN match (ni sémantique, ni par mots-clés) mais l'outil a réussi,
    # avancer la première tâche non complétée. Le LLM exécute les tâches en ordre
    # du plan ; si le tool a réussi sans erreur, il a très probablement avancé le plan.
    # Condition : obs non vide + outil non trivial (pas juste wait/memory_add).
    # Exception : un outil "trivial" qui a des hints matchant la tâche du plan
    # n'est PAS trivial dans ce contexte (ex: memory_search quand le plan dit "rechercher").
    # CODE_READ : désactivé — en mode analyse, seul le LLM peut marquer les tâches
    # complétées (via hint/tool/arg match). L'auto-avancement désynchronise le plan
    # et provoque des blocages PLAN GUARD sur des tâches marquées par erreur.
    _is_read_only_mode = False  # v2: mode lecture seule supprimé
    _next_auto_task = next((t for t in e.task_plan if not t.completed), None)
    _auto_next_is_closure_task = bool(
        _next_auto_task
        and _is_post_codeagent_closure_task(_next_auto_task.description)
    )
    _TRIVIAL_TOOLS = {
        # Lecture / navigation fichiers
        "wait", "memory_add", "read_file", "list_files", "list_dir",
        "search_files", "search_code", "list_directory", "find_files",
        "grep_search", "search_in_code", "view_file_outline",
        # Mail info-only
        "mail_list_accounts", "mail_inbox", "mail_check", "memory_search",
        "mail_account_upsert",
        # Config / inspection système — ne représentent aucune action métier
        "get_lumena_config", "get_system_info", "health_check",
        "get_weather", "get_time", "provider_info",
        # Listing modèles / ressources
        "list_image_models", "ionos_list_sites", "ionos_list_files",
    }

    def _trivial_tool_matches_next_task() -> bool:
        """Return True if a trivial tool's hints match the next uncompleted task."""
        hints = _TOOL_COMPLETION_HINTS.get(tool_name, [])
        if not hints:
            return False
        for task in e.task_plan:
            if not task.completed:
                desc_lower = task.description.lower()
                return any(h in desc_lower for h in hints)
        return False

    if (
        allow_fallback  # GF-1 : désactivé pour les sous-outils parallel_tools
        and not _any_matched
        and not _seq_matched
        and not observation_has_failure
        and not _is_read_only_mode
        and not _auto_next_is_closure_task
        and not _browser_observation_is_auxiliary_action(tool_name, observation_content or "")
    ):
        # Garde: max 1 auto-avancement par itération (parallel_tools peut appeler
        # _update_plan_progress N fois dans la même itération → sans garde, N tâches
        # sont marquées completed d'un coup sans rapport avec le contenu réel)
        if e.lire_derniere_avance() == iteration:
            pass  # déjà auto-avancé cette itération
        # Garde 1b: parallel_tools agrège plusieurs sous-outils et ne doit jamais
        # auto-avancer à lui seul une tâche métier via ce fallback générique.
        elif tool_name == "parallel_tools":
            pass
        # Garde 2: pas d'auto-avancement trop tôt (itération 0) sauf si
        # l'observation contient un marqueur de succès explicite (✅)
        elif iteration < 1 and "\u2705" not in (observation_content or ""):
            pass
        # Garde 3: l'observation doit être substantielle (pas juste "OK" ou vide)
        elif (
            observation_content
            and len(observation_content.strip()) >= 10
            and (
                tool_name not in _TRIVIAL_TOOLS
                or _trivial_tool_matches_next_task()
                # Un outil de vérification (health_check, run_command…) avec preuve
                # réelle n'est pas trivial même s'il figure dans _TRIVIAL_TOOLS.
                or has_sufficient_proof(
                    tool_name,
                    observation_content,
                    "",
                    tool_module_category,
                    tool_semantic_category,
                )
                # #3 : relecture d'un artefact écrit avant = vérification légitime,
                # même si read_file est « trivial » (le verify-gate ci-dessous tranche).
                or _artifact_reread
            )
        ):
            # Garde 4: si la tâche mentionne explicitement un nom d'outil différent
            # du tool actuel, ne PAS auto-avancer (ex: tâche dit "check_web_project"
            # mais le tool est "run_command" → pas de lien causal)
            import re as _re_plan
            for task in e.task_plan:
                if not task.completed:
                    desc_lower = task.description.lower()
                    if not sourced_web_research_task_proven(
                        tool_name, task.description, observation_content
                    ):
                        logger.debug(
                            "[PLAN] Recherche sourcée auto sans URL suffisante: '{}' non marquable par {} (iter {})",
                            task.description, tool_name, iteration,
                        )
                        break
                    if not document_plan_tool_can_complete_task(
                        tool_name,
                        task.description,
                        tool_kind=str((tool_args or {}).get("kind", "")),
                        required_kinds=e.types_documents_requis(task.description),
                        compound_workflow=_compound_document_workflow,
                    ):
                        logger.debug(
                            "[PLAN] Document auto: '{}' attend le manifest complet (iter {})",
                            task.description, iteration,
                        )
                        break
                    if (
                        tool_name in _READ_ONLY_DISCOVERY_PLAN_TOOLS
                        and not _read_only_discovery_tool_can_complete_task(tool_name, task.description)
                    ):
                        logger.debug(
                            "[PLAN] Outil découverte auto hors périmètre: '{}' non marquable par {} (iter {})",
                            task.description, tool_name, iteration,
                        )
                        break
                    if (
                        tool_name in _BROWSER_PLAN_PASSIVE_TOOLS
                        and not _browser_passive_tool_can_complete_task(tool_name, task.description)
                    ):
                        logger.debug(
                            "[PLAN] Browser auto hors périmètre: '{}' non marquable par {} (iter {})",
                            task.description, tool_name, iteration,
                        )
                        break
                    if tool_name.startswith("browser_") and not has_sufficient_proof(
                        tool_name,
                        observation_content,
                        task.description,
                        tool_module_category,
                        tool_semantic_category,
                    ):
                        logger.debug(
                            "[PLAN] Browser auto sans preuve: '{}' non marquable par {} (iter {})",
                            task.description, tool_name, iteration,
                        )
                        break

                    # Guard 5 : un outil d'exploration ne peut pas auto-avancer
                    # une tâche métier (premier mot = verbe d'action).
                    # Ex : run_command("cd") ne peut pas cocher "Déléguer …".
                    if (
                        tool_name in _EXPLORATION_TOOLS_STRICT
                        and any(
                            _normalize_guard_token(desc_lower) == starter
                            or _normalize_guard_token(desc_lower).startswith(starter + " ")
                            for starter in _BUSINESS_ACTION_STARTERS_NORMALIZED
                        )
                    ):
                        logger.debug(
                            "[PLAN] Guard 5: tâche métier '{}' non marquable par outil exploratoire {} (iter {})",
                            task.description, tool_name, iteration,
                        )
                        break

                    # Guard 4 : la description référence un outil spécifique qui
                    # n'est pas le tool courant → l'auto-avancement est illégitime
                    _tool_refs = _re_plan.findall(r'\b([a-z][a-z0-9]*(?:_[a-z0-9]+)+)\b', desc_lower)
                    if _tool_refs and tool_name.lower() not in _tool_refs:
                        logger.debug(
                            "[PLAN] Auto-avancement bloqué: '{}' référence {} mais tool={} (iter {})",
                            task.description, _tool_refs, tool_name, iteration,
                        )
                        break
                    # Verify-gate : l'auto-avancement générique ne constitue pas
                    # une preuve pour les étapes de vérification fonctionnelle.
                    # #3 : EXCEPTION — une relecture d'artefact réellement écrit avant
                    # (read_file/read_document sur le fichier muté) EST la vérification.
                    if (
                        is_verify_task(desc_lower)
                        and not has_sufficient_proof(
                            tool_name,
                            observation_content,
                            task.description,
                            tool_module_category,
                            tool_semantic_category,
                        )
                        and not verify_satisfied_by_artifact_read(
                            tool_name, task.description, artifact_reread=_artifact_reread,
                        )
                    ):
                        logger.debug(
                            "[PLAN] Verify-gate (auto): '{}' non marquable par {} — preuve insuffisante (iter {})",
                            task.description, tool_name, iteration,
                        )
                        break
                    if _requires_strict_proof(desc_lower) and not has_sufficient_proof(
                        tool_name,
                        observation_content,
                        task.description,
                        tool_module_category,
                        tool_semantic_category,
                    ):
                        logger.debug(
                            "[PLAN] Strict-proof (auto): '{}' non marquable par {} — preuve insuffisante (iter {})",
                            task.description, tool_name, iteration,
                        )
                        break
                    if _requires_strict_proof(desc_lower) and not _has_strict_plan_proof(desc_lower, obs_lower):
                        logger.debug(
                            "[PLAN] Strict-proof content (auto): '{}' non marquable par {} — observation insuffisante (iter {})",
                            task.description, tool_name, iteration,
                        )
                        break
                    # Guard SUBMIT-ONLY (fallback) : même restriction que le
                    # chemin principal — browser_type_index ne peut jamais marquer
                    # une tâche de soumission.
                    if tool_name == "browser_type_index" and any(
                        sv in desc_lower for sv in _SUBMIT_VERBS
                    ):
                        logger.debug(
                            "[PLAN] Guard SUBMIT-ONLY (auto): '{}' non marquable par browser_type_index (iter {})",
                            task.description, iteration,
                        )
                        break
                    # Guard FINAL-ONLY (fallback) : les tâches de rapport/confirmation
                    # ne sont pas marquables par des outils browser.
                    if tool_name.startswith("browser_") and _is_final_only_task(desc_lower):
                        logger.debug(
                            "[PLAN] Guard FINAL-ONLY (auto): '{}' non marquable par {} — réservé à FINAL (iter {})",
                            task.description, tool_name, iteration,
                        )
                        break
                    # Guard CORRECTION-ONLY (fallback) : idem chemin principal —
                    # une lecture ne crédite jamais une tâche de correction.
                    if correction_task_blocks_readonly(tool_name, desc_lower):
                        logger.debug(
                            "[PLAN] Guard CORRECTION-ONLY (auto): '{}' non marquable par {} "
                            "(lecture ≠ correction) (iter {})",
                            task.description, tool_name, iteration,
                        )
                        break
                    # LOT 2.7 — Guard TOOL-EXPLICIT (fallback auto) : idem chemin
                    # principal — un autre outil ne coche jamais une tâche qui
                    # nomme explicitement write_mission_contract/pytest/etc.
                    if tool_explicit_task_blocks(tool_name, desc_lower):
                        logger.debug(
                            "[PLAN] Guard TOOL-EXPLICIT (auto): '{}' non marquable par {} "
                            "(iter {})",
                            task.description, tool_name, iteration,
                        )
                        break
                    if delegation_task_blocks(tool_name, desc_lower):
                        logger.debug(
                            "[PLAN] Guard DELEGATION-ONLY (auto): '{}' non marquable "
                            "par {} (iter {})",
                            task.description, tool_name, iteration,
                        )
                        break
                    # C0.3b — Guard PUBLISH-ONLY (fallback) : idem chemin principal.
                    if publish_task_blocks(tool_name, desc_lower):
                        logger.debug(
                            "[PLAN] Guard PUBLISH-ONLY (auto): '{}' non marquable par {} "
                            "(iter {})",
                            task.description, tool_name, iteration,
                        )
                        break
                    # LOT E — Guard BROWSER-ONLY (fallback) : idem chemin principal.
                    if browser_verify_task_blocks(tool_name, desc_lower):
                        logger.debug(
                            "[PLAN] Guard BROWSER-ONLY (auto): '{}' non marquable par {} "
                            "(iter {})",
                            task.description, tool_name, iteration,
                        )
                        break
                    if browser_interaction_task_blocks(tool_name, desc_lower):
                        logger.debug(
                            "[PLAN] Guard INTERACTION-PROOF (auto): '{}' non marquable "
                            "par {} (iter {})", task.description, tool_name, iteration,
                        )
                        break
                    if artifact_target_task_blocks(
                        tool_name, desc_lower, tool_args or {}
                    ):
                        logger.debug(
                            "[PLAN] Guard ARTIFACT-TARGET (auto): '{}' incompatible avec "
                            "la cible de {} (iter {})", task.description, tool_name, iteration,
                        )
                        break
                    _proof = evaluate_task_proof(task.description, tool_name, observation_content)
                    task.completed = True
                    task.completed_at_iteration = iteration
                    task.completed_by_tool = f"{tool_name}:auto"
                    task.completion_status = task_completion_status(
                        tool_name, desc_lower, tool_semantic_category, tool_module_category,
                    )
                    task.completion_evidence = _proof.evidence_summary
                    task.completion_confidence = _proof.confidence
                    e.definir_derniere_avance(iteration)
                    logger.debug(
                        "[PLAN] Fallback auto-avancement: '{}' marquée via {} (iter {})",
                        task.description, tool_name, iteration,
                    )
                    break
