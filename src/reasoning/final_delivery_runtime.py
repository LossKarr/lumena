"""Livraison finale et verrou de verite — extrait de `react.py` (lot RF-8).

Ce module ne DECIDE que sur un instantane. Restent dans `react.py`, parce
qu'elles portent un EFFET que `self.X = ...` ne montre pas (angle mort corrige
pendant ce lot) :

    `_note_truth_lock_outcome`   mute `self._run_meta[...]`
    `_empty_final_fallback`      appelle `_mark_task_failed(...)`
    `_stream_and_return_final`   `_mark_task_done(...)` **et le STREAMING**

Le §14 du plan exige que « le streaming et la latence voix ne regressent pas » :
l'animation de frappe et son exemption voix ne bougent donc pas d'un pouce.

Restent aussi `_final_repair_attempts`, `_premature_final_retries` et
`_ledger_final_guard_used` — des paires **property + setter** dont le setter
ecrit dans `exec_state.repairs`.

**Ce module n'importe JAMAIS `react.py`** (invariant 2). Les appels
redescendent sur l'INSTANCE : un appel direct ferait perdre les monkeypatchs
des tests, en silence (17 tests tombes ainsi en RF-7a).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from loguru import logger

from src.reasoning.final_guards import apply_mission_truth_lock


@dataclass(frozen=True)
class EntreeFinal:
    """Contrat d'etat de la livraison finale, sans `self`."""

    task_id: Callable[[], Any]
    ledger: Callable[[], Any]
    requete_originale: Callable[[], str]
    est_run_mission: Callable[[], bool]
    pont_codex: Callable[[], bool]
    est_run_worker: Callable[[], bool]
    web_present: Callable[[], str]
    preuve_tests_verts: Callable[[], bool]
    preuve_navigateur: Callable[[], Any]
    tests_non_lances: Callable[[], bool]
    attend_des_fichiers: Callable[[], Any]
    ecrits_non_publies: Callable[[], list]
    preuve_serveur: Callable[[], bool]
    dom_observe: Callable[[], bool]
    interaction_prouvee: Callable[[], bool]
    drapeau_interaction: Callable[[], bool]
    drapeau_jeu: Callable[[], bool]
    drapeau_web: Callable[[], bool]
    navigateur_en_panne: Callable[[], bool]
    #: MUTE `_run_meta` : reste dans `ReActLoop`, appelee par dispatch d'instance.
    noter_verdict: Callable[[dict], None]
    racine_projet: Callable[[], Any]
    #: `build_mission_final_message` est DEFINIE dans `react.py` : l'importer
    #: violerait l'invariant 2, elle passe donc par l'entree.
    construire_bilan_mission: Callable[..., str]


def rf8_truth_lock_mission_message(etat, message: str, *, origine: str = "") -> str:
    """LOT RF-8-FIX-1 — le verrou de verite, a UNE SEULE place.

    Les ~20 arguments de `apply_mission_truth_lock` etaient ecrits en toutes
    lettres dans `_stream_and_return_final`. Deux voies de sortie mission ne
    passaient pas par ce goulot et rendaient donc la parole du modele SANS
    verrou :

        l.7008  voie I3  (2026-08-13) — `if self._mission_worker_delivered():`
        l.7029  voie Z28 (2026-08-19) — apres `_mark_task_failed(...)`

    Or ce qu'elles rendent vaut `answer if answer else "..."` : la parole du
    MODELE, pas un constat fabrique par un garde. Mesure sur la phrase
    « Les 8 tests pytest sont VERTS et le module est publie » sans aucune
    preuve au ledger : le verrou pose deux bannieres et reecrit la
    revendication ; ces deux voies la livraient intacte.

    C'est le defaut que le lot 2.7 avait ferme — « un FINAL fabrique
    "8/8 tests pytest verts" emis sans passer par le verrou » — reintroduit
    par deux lots POSTERIEURS. I3 et Z28 avaient raison sur le fond : un
    worker dont les fichiers sont remplis a fait son travail. Leur defaut
    est d'avoir laisse passer la PAROLE en sauvant l'ETAT.

    Recopier les 20 arguments a chaque site aurait laisse le verrou deriver
    d'une voie a l'autre : ils vivent donc ici, et ici seulement.

    Ne touche NI au statut de tache, NI au streaming, NI aux metadonnees :
    sur la voie Z28, marquer `done` contredirait le `_mark_task_failed` qui
    vient d'etre pose.
    """
    if not message:
        return message
    if not (etat.est_run_mission()
            or etat.pont_codex()):
        return message
    try:
        _locked, _info = apply_mission_truth_lock(
            message,
            has_green_test=etat.preuve_tests_verts(),
            last_test_outcome=etat.ledger().last_test_outcome(),
            has_browser_proof=etat.preuve_navigateur(),
            # A5 (run FitLog) — preuves au LEDGER, plus au prompt : couvre
            # TOUTES les voies de sortie (repair thought-leak/tronqué
            # comprises — w_tests avait conclu sans pytest, gate éteint
            # par le plafond d'itérations).
            tests_present_not_run=etat.tests_non_lances(),
            has_any_mutation=etat.ledger().has_any_mutation(),
            # LOT E (run FidéliBar) — « publié » n'est licite qu'avec un
            # publish_mission_workspace réussi au ledger de ce run.
            has_published=etat.ledger().has_published(),
            # LOT 2.11.E — disk-grounded : « publié dans workspace/X » où X
            # n'existe pas sur disque = fausse publication (run StatsNotes).
            project_root=etat.racine_projet(),
            # M1 (run RévizIA) — policy navigateur dure (top-lead web).
            web_deliverable=etat.drapeau_web(),
            file_deliverables_expected=etat.attend_des_fichiers(),  # H8
            unpublished_writes=etat.ecrits_non_publies(),  # Z24
            has_server_started=etat.preuve_serveur(),  # LOT 2.3
            browser_content_seen=etat.dom_observe(),  # 2.7.4
            interaction_proven=etat.interaction_prouvee(),
            interaction_required=etat.drapeau_interaction(),
            objective_is_game=etat.drapeau_jeu(),  # 2.13.A
            browser_runtime_failed=etat.navigateur_en_panne(),  # M100.4
        )
        etat.noter_verdict(_info)  # F1.b
        if _info.get("changed"):
            logger.warning(
                "[MISSION TRUTH-LOCK] {} — rétrogradé honnêtement "
                "(preuves ledger insuffisantes ; voie non verrouillée en "
                "amont). détails={} task={}",
                origine or "CHOKEPOINT",
                {k: v for k, v in _info.items() if v and k != "changed"},
                etat.task_id())
            return _locked
    except Exception as _exc:
        # Invariant 6 : une exception ne devient ni une autorisation ni une
        # mutilation du message. Comportement historique du goulot.
        logger.debug("[MISSION TRUTH-LOCK] {} skip: {}", origine, _exc)
    return message


def rf8_truth_lock_web_flag(etat) -> bool:
    """M1 (run RévizIA) — policy navigateur DURE : True si CE run est le TOP-LEAD
    d'une mission à livrable WEB. Passé au truth-lock (`web_deliverable=`) : sans
    action browser_* réussie au ledger, TOUT final reçoit la bannière « Navigateur
    NON vérifié », indépendamment de la formulation (« Test navigateur validé » —
    forme nominale hors regex — avait livré une fabrication totale : serveur
    jamais lancé). Scope top-lead (même raison que le BROWSER GATE / D-fix) : un
    sous-worker isolé ne peut pas vérifier au navigateur — la vérité incombe au
    lead. Défensif : False sur toute erreur (jamais de fausse rétrogradation)."""
    try:
        # H4 — un porteur d'EFFETS purs n'a pas d'`allowed_files` : le test
        # historique le prenait pour le lead et lui collait cette policy
        # (run veille_python_313 : bannière navigateur sur une veille).
        if etat.est_run_worker():
            return False  # sous-worker délégué → pas son job
        return bool(etat.web_present())
    except Exception:
        return False


def rf8_truth_lock_game_flag(etat) -> bool:
    """2.13.A (run puissance4) — True si l'OBJECTIF de CE run demande un JEU web.
    Passé au truth-lock (`objective_is_game=`) : combiné à web_deliverable et
    interaction_proven=False, la bannière « Interaction NON prouvée » tire QUEL
    QUE SOIT le texte du final (la course aux regex sur le final est perdue —
    « jetons tombent / X a gagné » avait échappé à 2.12.D). Même scope top-lead
    que _truth_lock_web_flag. Défensif : False sur toute erreur."""
    try:
        if etat.est_run_worker():  # H4 : périmètre OU parent (worker d'effets)
            return False  # sous-worker délégué → pas son job
        from .final_guards import objective_is_web_game
        return objective_is_web_game(etat.requete_originale())
    except Exception:
        return False


def rf8_truth_lock_interaction_flag(etat) -> bool:
    """Whether the top-lead objective requires an observable UI state change."""
    try:
        if etat.est_run_worker():  # H4 : périmètre OU parent (worker d'effets)
            return False
        if not etat.web_present():
            return False
        from .final_guards import objective_requires_web_interaction_proof
        return objective_requires_web_interaction_proof(
            etat.requete_originale()
        )
    except Exception:
        return False


# ══════════════════════════════════════════════════════════════════════════
#  Lot RF-8b — DECISIONS dont l'EFFET reste dans `ReActLoop`
#
#  Ces deux fonctions ne journalisent pas et ne construisent rien qui date :
#  dans l'original, la mutation precede le log, et l'invariant 16 exige que
#  l'ordre observable ne change pas. `react.py` garde donc mutation, log et
#  retour dans l'ordre d'origine.
# ══════════════════════════════════════════════════════════════════════════


def rf8b_verdict_a_memoriser(info, deja_vu: bool) -> dict:
    """F1.b — quelles cles poser dans `_run_meta` pour ce verdict ?

    Rend un dict des cles a ecrire (vide si rien). L'ECRITURE reste dans
    `ReActLoop` (invariant 5).

    Le drapeau est cumulatif : un site aval qui ne detecte rien ne doit jamais
    effacer un overclaim vu en amont — d'ou `deja_vu`, qui porte la presence
    prealable de la cle.

    On distingue volontairement `overclaim` de `changed` : une simple note
    honnete (« tests non executes ») modifie le texte sans etre une faute de
    cloture. Seule l'affirmation fausse compte.
    """
    if not isinstance(info, dict):
        return {}
    a_poser: dict = {}
    if info.get("overclaim"):
        a_poser["mission_truth_lock_overclaim"] = True
    elif not deja_vu:
        a_poser["mission_truth_lock_overclaim"] = False
    if info.get("changed"):
        a_poser["mission_truth_lock_applied"] = True
    return a_poser


def rf8b_decision_final_vide(etat) -> tuple:
    """F1.b — en mission, un FINAL vide ne devient JAMAIS une phrase de politesse.

    Rend `(message, marquer_echec, ecrits, publie)`. Le `_mark_task_failed` et
    les deux `logger.warning` restent dans `ReActLoop`, dans l'ordre d'origine.

    Hors mission : comportement historique strictement inchange (le chat garde
    sa formule).

    En mission (AUD-012 / AUD-008) : un `answer` vide sur le chemin de SUCCES
    produisait « Je n'ai pas trouve de reponse pertinente. » — une chaine NON
    VIDE, qui franchissait donc la porte `empty_result` du runner.

    Deux issues, toutes deux honnetes :
      - le ledger porte des preuves -> bilan DETERMINISTE (aucun appel LLM,
        donc aucun risque de fuite THOUGHT reintroduit) ;
      - le ledger est vide -> rien n'a ete produit : echec honnete.

    Defensif : toute erreur retombe sur la formule historique — ce garde-fou ne
    doit jamais transformer une mission reussie en exception.
    """
    _historique = "Je n'ai pas trouvé de réponse pertinente."
    try:
        if not etat.est_run_mission():
            return _historique, False, [], False
        led = etat.ledger()
        written = sorted(led.written_basenames())
        published = led.has_published()
        if not (written or published or led.has_any_mutation()):
            # Aucune preuve d'effet : ce n'est pas une livraison.
            return (
                "⚠️ La mission s'est terminée sans produire de réponse finale, "
                "et aucune action n'a laissé de trace vérifiable. "
                "Rien n'a été livré — il faut relancer le travail.",
                True, written, published,
            )
        _bits = []
        if written:
            _bits.append("Fichiers écrits : " + ", ".join(written[:12]))
        if published:
            _bits.append("Publication effectuée (publish_mission_workspace).")
        return (
            etat.construire_bilan_mission(
                "\n".join(_bits), "",
                malformed=False,
                has_green_test=etat.preuve_tests_verts(),
                test_ran_not_green=False,
                tests_expected_not_run=etat.tests_non_lances(),
            ),
            False, written, published,
        )
    except Exception:
        return _historique, False, [], False
