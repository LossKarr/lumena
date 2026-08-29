"""Runtime navigateur — les lectrices d'etat.

Lot RF-7a du plan `plans/REFACTOR_REACT_DEDICATED_PLAN_2026-08-27.md`.

--- Pourquoi RF-7 s'arrete ici, et pourquoi c'est MESURE ---

Le plan decrit RF-7 comme « proprietes d'etat ET gates » vers ce module. La
mesure dit autre chose :

    16 attributs d'etat navigateur — ecrits par `_run_internal` : les 16.
    Zero exception.

L'etat navigateur n'est donc pas extractible sans ouvrir `_run_internal`
(perimetre RF-9, bloque par le §18). Ce qui reste extractible, ce sont les
LECTRICES : douze methodes qui consomment cet etat sans jamais l'ecrire.

C'est exactement la strategie que le plan prescrit pour RF-7 : « extraire
d'abord les decisions a partir d'un snapshot immutable ; appliquer les
mutations dans react.py ». Ici il n'y a meme aucune mutation a laisser.

--- Quatre de ces douze sont des GARDES ---

L'invariant 7 exige qu'ils restent fail-closed. La matrice du lot mesure
explicitement, pour chaque scenario, si le garde REFUSE ou LAISSE PASSER :
**30 passages contre 10 refus** sur la reference.

--- Le motif des deux formes, SIXIEME occurrence ---

`_is_mission_run` est lu par `getattr(self, "_is_mission_run", False)` dans
`_post_delegate_web_verify_allowed` et par `self._is_mission_run` — qui LEVE —
dans `_current_browser_proof`. Une seule forme ne peut pas rendre les deux :
l'entree porte `est_run_mission()` et `est_run_mission_strict()`.

--- Ce module n'importe PAS `react.py` (invariant 2) ---

`_LP_UNPROVABLE_CLOSED_TOOLS` reste dans `react.py` : le fichier de tests de
RF-1 l'inscrit explicitement dans `CONSTANTES_RESTEES`. Elle est passee en
VALEUR (`frozenset`, donc identite preservee — invariant 12).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

from loguru import logger

from .delegate_strategy import _post_delegate_web_verify_enabled


@dataclass(frozen=True)
class EntreeNavigateur:
    """Contrat d'etat du runtime navigateur, sans `self`.

    ZERO mutation : ce sous-lot n'en a aucune. Tout est appelable — les
    lectures restent paresseuses, et les boucles construites par
    `object.__new__(ReActLoop)` continuent de fonctionner.
    """

    # -- etat lu --
    obtenir_ledger: Callable[[], Any]
    obtenir_historique: Callable[[], Any]
    obtenir_task_id: Callable[[], Any]
    obtenir_orchestrateur: Callable[[], Any]
    obtenir_exec_state: Callable[[], Any]
    tirs_gate_navigateur: Callable[[], Any]
    tirs_gate_interaction: Callable[[], Any]
    url_page_courante: Callable[[], Any]
    url_preview_indemontrable: Callable[[], Any]
    marqueur_echec_runtime: Callable[[], Any]
    marqueur_verifie_runtime: Callable[[], Any]

    # -- les DEUX formes de `_is_mission_run` --
    est_run_mission: Callable[[], Any]
    est_run_mission_strict: Callable[[], Any]

    # -- appels INTERNES, redescendus sur l'instance (dispatch preserve) --
    pages_jamais_ouvertes: Callable[[], Any]
    interaction_prouvee: Callable[[], Any]
    preuve_navigateur_courante: Callable[[], Any]
    intention_verif_navigateur: Callable[..., Any]
    verif_navigateur_mission: Callable[..., Any]
    runtime_verifie_truth_lock: Callable[[], Any]

    # -- sorties hors sous-lot --
    est_run_worker: Callable[[], Any]
    web_present_pour_gate: Callable[[], Any]
    drapeau_interaction: Callable[[], Any]
    drapeau_jeu: Callable[[], Any]

    # -- valeurs : constantes qui restent chez `react.py` --
    #    `_LP_UNPROVABLE_CLOSED_TOOLS` : exigee par le test de RF-1.
    #    `_MAX_INTERACTION_GATE_SHOTS` : un consommateur reste dans `_run_internal`.
    outils_fermes_preview: Callable[[], Any]
    max_tirs_gate_interaction: Callable[[], Any]


def _post_delegate_web_verify_allowed(e) -> bool:
    """2.6.3 (run MiniQuiz §5) — le vérifieur web post-delegate ne tire JAMAIS
        en mission. Il sert le dossier en STATIQUE → 404 structurels sur une app
        Flask → il a poussé les 3 workers dans un chaos de « corrections » (port
        du contrat muté via shell, tests pollués, serveurs fantômes). En mission,
        la preuve navigateur appartient au LEAD : serve_website (voie 2.5) +
        browser_* réels, gardés par BROWSER GATE et truth-lock."""
    if e.est_run_mission():
        return False
    try:
        return bool(_post_delegate_web_verify_enabled())
    except Exception:
        return True


def _current_browser_proof(e) -> bool:
    """Mission browser proof is stale after a later source mutation."""
    try:
        if e.est_run_mission_strict():
            return e.obtenir_ledger().has_fresh_browser_action()
        return e.obtenir_ledger().has_browser_action()
    except Exception:
        return False


def _browser_verify_intent(text: str) -> bool:
    """LOT D — le texte exprime-t-il une INTENTION de vérifier au navigateur ?
        Mêmes marqueurs que plan_progress.browser_verify_task_blocks (nom navigateur
        + verbe de vérif). Pur/testable."""
    d = (text or "").lower()
    if not any(m in d for m in ("navigateur", "browser", "naviguer")):
        return False
    return any(v in d for v in ("vérif", "verif", "test", "valid", "confirm",
                                "s'assur", "assur", "contrôl", "controle"))


def _mission_browser_verify_pending(e, answer: str, original_query: str) -> str:
    """LOT D — faut-il relancer pour la jambe navigateur ? Retourne la justif web
        (non vide) si : livrable WEB présent ET aucune action browser_* réussie au
        ledger ET (intention navigateur dans l'objectif OU claim navigateur dans le
        FINAL). "" sinon. Déclencheur validé revue : objectif OU claim (le lead peut
        conclure « frontend fonctionnel ✅ » sans que l'objectif ait dit « vérifie »)."""
    # LOT Z11 (décision utilisateur 2026-08-16) — AVANT les sorties anticipées.
    # Le gate ci-dessous se satisfait d'UNE navigation : ouvrir une page sur
    # deux le contentait. Mesuré sur les 3 runs web multi-pages, sans
    # exception : la page testée est à 100 % de style, la page jamais ouverte
    # à 4 %, 50 %, 31 %. Z7 nomme pourtant la page fautive — Tanière a
    # corrigé, Marée a ignoré. Un constat seul ne suffit pas ici.
    _z11 = e.pages_jamais_ouvertes()
    if _z11:
        return _z11
    interaction_required = e.drapeau_interaction()
    if interaction_required:
        if e.interaction_prouvee():
            return ""  # verdict strict positif → interaction réellement exercée
    elif e.preuve_navigateur_courante():
        return ""  # simple inspection demandée : preuve browser historique suffisante
    # LOT D-fix (run CoVoit'Éco 2026-07-04) — la vérif navigateur est le job du
    # TOP-LEAD (il assemble, SERT l'app, puis navigue), JAMAIS d'un sous-worker
    # délégué : l'app n'est pas servie pendant son run isolé. Un sous-worker a un
    # périmètre `allowed_files` ; le lead non. Sans ce garde, le boilerplate
    # « navigateur PARTAGÉ » + « test » des objectifs workers faisait sur-
    # déclencher le gate sur w_backend/w_tests (bruit : 1 relance inutile chacun).
    if e.est_run_worker():  # H4 : périmètre OU parent (worker d'effets)
        return ""  # sous-worker délégué → pas de vérif navigateur ici
    web = e.web_present_pour_gate()
    if not web:
        return ""
    from src.reasoning.final_guards import claims_browser_verified
    if (interaction_required or e.intention_verif_navigateur(original_query)
            or claims_browser_verified(answer)):
        return web
    return ""


def _pages_never_opened_reason(e) -> str:
    """LOT Z11 — les pages HTML produites que ce run n'a JAMAIS ouvertes.

        Sources bornées, patron `_mission_web_present_for_gate` : les basenames
        écrits au ledger et le `contract.json` de la mission pour le PRODUIT ;
        l'historique des `browser_navigate` pour le VU. Aucune I/O nouvelle.

        Inerte hors mission, chez un worker délégué, et dès qu'il y a moins de
        deux pages — le cas mono-page est déjà couvert par la jambe navigateur
        du LOT D, et ce garde ne parle que du multi-pages, seul cas mesuré.
        """
    try:
        if e.est_run_worker():
            return ""
        produced: list = []
        for b in e.obtenir_ledger().written_basenames():
            if str(b).lower().endswith((".html", ".htm")):
                produced.append(str(b))
        if e.obtenir_task_id() and e.obtenir_orchestrateur():
            rec = e.obtenir_orchestrateur().get_task(e.obtenir_task_id()) or {}
            mws = str((rec.get("metadata") or {}).get("mission_workspace") or "").strip()
            if mws:
                import json as _jZ
                import os as _osZ

                from src.utils.paths import WORKSPACE_DIR as _wsZ

                cj = _osZ.path.join(str(_wsZ / mws), "contract.json")
                if _osZ.path.isfile(cj):
                    with open(cj, encoding="utf-8", errors="replace") as fh:
                        data = _jZ.load(fh)
                    for f in (data.get("files") or []):
                        p = str(f.get("path") or "")
                        if p.lower().endswith((".html", ".htm")):
                            produced.append(p)
        if len(produced) < 2:
            return ""
        visited: list = []
        for item in e.obtenir_historique():
            act = getattr(item, "action", None)
            if not act or getattr(act, "tool_name", "") != "browser_navigate":
                continue
            url = (getattr(act, "tool_args", None) or {}).get("url")
            if url:
                visited.append(str(url))
        from src.reasoning.plan_progress import (
            pages_never_opened,
            unseen_pages_reason,
        )

        manquantes = pages_never_opened(produced, visited)
        if not manquantes:
            return ""
        logger.warning(
            "[Z11] pages jamais ouvertes: {} (produites={}, naviguées={})",
            ", ".join(manquantes), len(set(produced)), len(visited),
        )
        return unseen_pages_reason(manquantes)
    except Exception as _exc_z11:
        logger.debug("[Z11] pages non vues non calculées: {}", _exc_z11)
        return ""


def _finalize_browser_gate_pending(e, note: str, original_query: str) -> str:
    """LOT 2.7 (run Converto 2026-07-06) — le FINALIZE déterministe doit-il être
        intercepté pour la jambe navigateur ? Retourne la justif web ("" sinon).
        Converto est sorti par `voie=plan_complet` (étape navigateur créditée à
        TORT au plan) : le BROWSER GATE ne vivait que sur la voie FINAL LLM —
        même trou que le PYTEST GATE avant C0.4. Borné : 1 tir partagé avec le
        gate du FINAL (même compteur `_browser_gate_shots`)."""
    if e.tirs_gate_navigateur() >= 1:
        return ""
    # ── LOT Z15 — une page ouverte ne vaut pas le site vérifié ────────────
    # Run « Verdure 2 » (2026-08-16) : la mission a ouvert `localhost:8081`
    # (= index.html), donc `_current_browser_proof()` est devenu vrai, donc
    # ce garde s'est tu — sans jamais demander à Z11 s'il RESTAIT des pages.
    # `devis.html`, l'espace client où vit toute la logique, n'a jamais été
    # regardée, et la mission a conclu proprement.
    #
    # Z11 avait pourtant été placé AVANT les sorties anticipées de
    # `_mission_browser_verify_pending`. Le trou était un cran plus haut :
    # l'APPELANT a les siennes. Même motif, même erreur, autre étage — et
    # mon test structurel ne regardait que l'étage du dessous.
    #
    # DÉCISION UTILISATEUR (2026-08-16) : « la vérification web du projet
    # doit se faire par le parent une fois les workers finis ; il doit
    # vraiment naviguer, scanner, vérifier — c'est le filet de sécurité ».
    # Les workers ne voient chacun que leur fichier ; le lead est le seul à
    # pouvoir regarder le résultat comme un utilisateur le verrait.
    try:
        _unseen = e.pages_jamais_ouvertes()
        if _unseen:
            return _unseen
    except Exception:
        pass
    # The basic browser gate only owns the first successful page opening.
    # An explicit form/game interaction has its own bounded gate below.
    if e.preuve_navigateur_courante():
        return ""
    try:
        return e.verif_navigateur_mission(note or "", original_query or "")
    except Exception:
        return ""


def _finalize_interaction_gate_pending(
    e, note: str, original_query: str
) -> str:
    """Return the web reason when an explicit UI interaction still lacks proof.

        This is deliberately separate from ``_browser_gate_shots``: opening the
        page and exercising a requested form/game flow are two distinct proofs.
        The interaction gate gets a small bounded action budget because a real
        form flow takes several ReAct iterations (inputs, click, DOM read).
        """
    if (
        e.tirs_gate_interaction()
        >= e.max_tirs_gate_interaction()
    ):
        return ""
    try:
        if not e.drapeau_interaction():
            return ""
        if e.interaction_prouvee():
            return ""
        if not e.preuve_navigateur_courante():
            return ""  # the basic browser gate must open the page first
        return e.verif_navigateur_mission(
            note or "", original_query or ""
        )
    except Exception:
        return ""


def _browser_content_seen(e) -> Optional[str]:
    """2.7.4 (run MiniPanier) — concaténation des observations qui LISENT la
        page (browser_navigate + son enrichissement vision, browser_get_content,
        browser_dom_state, browser_screenshot). EXCLUT les échos d'ACTION
        (type/click) : « Tape "Pommes" » prouve la saisie, pas l'affichage — c'est
        exactement la confusion qui a laissé passer le surclaim MiniPanier.
        None si aucune lecture de page (le verrou reste inerte). Défensif."""
    _CONTENT_TOOLS = {
        "browser_navigate", "browser_get_content", "browser_dom_state",
        "browser_screenshot", "browser_read", "browser_extract",
    }
    try:
        chunks = []
        for h in e.obtenir_historique():
            if not (h.action and h.observation):
                continue
            if (h.action.tool_name or "") in _CONTENT_TOOLS:
                c = h.observation.content or ""
                if c:
                    chunks.append(c)
        return "\n".join(chunks) if chunks else None
    except Exception:
        return None


def _browser_runtime_failed_for_truth_lock(e) -> bool:
    """Latest strict verifier failure, including persisted recovery state."""
    try:
        if e.est_run_worker():  # H4 : périmètre OU parent (worker d'effets)
            return False
        marker = e.marqueur_echec_runtime()
        if marker is not None:
            return bool(marker)
        if e.obtenir_task_id() and e.obtenir_orchestrateur():
            rec = e.obtenir_orchestrateur().get_task(e.obtenir_task_id()) or {}
            return bool((rec.get("metadata") or {}).get("web_runtime_failed"))
    except Exception:
        pass
    return False


def _browser_runtime_verified_for_truth_lock(e) -> bool:
    """Latest positive strict runtime verdict, persisted across mission recovery."""
    try:
        if e.est_run_worker():  # H4 : périmètre OU parent (worker d'effets)
            return False
        marker = e.marqueur_verifie_runtime()
        if marker is not None:
            return bool(marker)
        if e.obtenir_task_id() and e.obtenir_orchestrateur():
            rec = e.obtenir_orchestrateur().get_task(e.obtenir_task_id()) or {}
            meta = (rec.get("metadata") or {})
            if "web_runtime_verified" in meta:
                return bool(meta.get("web_runtime_verified"))
        return e.obtenir_ledger().has_successful_action(
            "browser_verify_local_project"
        )
    except Exception:
        return False


def _local_preview_unprovable_gate(e, tool_name: str):
    """LOT Z23 — l'inspection est close, la mission continue.

        Run « jeu 3D monde ouvert » (2026-08-19), au log :

            02:30:18  [LOCAL PREVIEW] escalade browser_evaluate (streak=3)
            02:30:23  interruption sans preuve interactive (streak=4)
                      ← DERNIERE LIGNE DU LOG

        5,6 secondes. Le garde reclamait UNE assertion, elle l'a fournie, elle
        n'a rien demontre — et le code faisait `return` : le run entier mourait
        a 18 minutes, sans echeance, laissant sur place le README que
        l'objectif demandait. Le meme degat est deja decrit en en-tete de
        `_local_preview_loop_decision` pour le run Cadran (« conclu a 7 min 19
        sur 60 »), ou seul le cas de l'appel mal forme avait ete repare.

        Or ce `return` n'apportait AUCUNE honnetete : le truth-lock bannerise
        « interaction NON prouvee » sur l'OBJECTIF et le ledger, quel que soit
        le texte du final (doctrine 2.13.A). Il doublait un mecanisme qui marche
        en payant la completude.

        Ce gate le remplace : le constat ferme la relecture de CETTE preview
        (sinon on retombe sur le rebouclage infini du run memo) et rien d'autre.
        Inerte ailleurs, inerte sur une autre page.
        """
    try:
        guards = getattr(e.obtenir_exec_state(), "guards", None)
        if not getattr(guards, "local_preview_interaction_unprovable", False):
            return None
        if (tool_name or "") not in e.outils_fermes_preview():
            return None
        # Borne de portee : le constat vaut pour LA preview jugee, pas pour
        # toute page que la mission ouvrira ensuite.
        _url = str(e.url_page_courante() or "")
        if _url != str(e.url_preview_indemontrable() or ""):
            return None
    except Exception:
        return None
    from .react_config import Observation as _ObsZ23

    return _ObsZ23(
        content=(
            "⛔ Constat ACQUIS sur cette preview locale : la validation "
            "interactive n'y est pas prouvable (assertion deja tentee, sans "
            "resultat probant). Ce constat est definitif pour ce run — "
            "l'inspecter a nouveau ne le changera pas.\n\n"
            "Ce n'est PAS un echec de mission : le reste de ce qui t'a ete "
            "demande t'attend. Termine-le (fichiers annonces, livrables, "
            "verifications hors navigateur), puis conclus en enoncant ce "
            "constat tel quel — sans jamais affirmer l'interactif."
        ),
        success=False,
        origin="local_preview_unprovable",
    )


def _truth_lock_interaction_proven(e) -> bool:
    """Strong proof for games and generic form/interface workflows."""
    try:
        if (e.est_run_mission_strict() and e.obtenir_ledger().has_source_mutation()
                and not e.preuve_navigateur_courante()):
            return False
    except Exception:
        return False
    _exec_state = e.obtenir_exec_state()
    _guards = getattr(_exec_state, "guards", None)
    local_assertion = bool(
        getattr(_guards, "local_preview_interaction_proven", False)
    )
    if not local_assertion and e.obtenir_task_id() and e.obtenir_orchestrateur():
        try:
            rec = e.obtenir_orchestrateur().get_task(e.obtenir_task_id()) or {}
            local_assertion = bool(
                (rec.get("metadata") or {}).get("browser_interaction_verified")
            )
        except Exception:
            pass
    if e.drapeau_jeu():
        return local_assertion
    if e.drapeau_interaction():
        return local_assertion or e.runtime_verifie_truth_lock()
    return local_assertion
