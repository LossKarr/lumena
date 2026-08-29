"""Lecture et synthese d'OBSERVATIONS — decisions pures, sans etat.

EXTRAIT DE `react.py` le 2026-08-27 par le lot RF-2 du plan
`plans/REFACTOR_REACT_DEDICATED_PLAN_2026-08-27.md`.

Deplacement quasi verbatim : noms, signatures, corps et ordre identiques.
Aucune correction, aucun renommage, aucune valeur par defaut modifiee.

Ce module repond a une seule question, sous plusieurs formes : **que dit cette
observation ?** Est-elle tabulaire, est-ce un resultat de tests, faut-il en
reparer le final, quelle guidance MCP en tirer, quelle reponse en synthetiser,
et la lecture piétine-t-elle ?

Ce qu'il ne fait PAS :

  * il ne prend jamais `self` et n'importe jamais `react.py` (invariant 2) ;
  * il n'a AUCUNE dependance projet : seulement `typing`, `pathlib`, `json`
    et `re`. C'est ce qui le rend testable isolement ;
  * il ne decide rien sur les livrables de mission. `mission_write_path_exists`
    et `mission_write_targets_existing_deliverable` sont restees dans
    `react.py` : ce sont les auxiliaires de `ReActLoop._mission_overwrite_gate`,
    et leur deplacement appartient au lot RF-6.

Les 12 symboles sont reexportes par `react.py` : les 7 fonctions sont toutes
importees ailleurs dans le depot, et toutes appelees plus bas dans `react.py`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Tuple


# V2.3 fix prod 2026-05-19 : marqueurs d'observation outil "tabulaire riche"
# qui peut servir de fallback FINAL si le LLM ne fait que des promesses.
_TABULAR_OBS_MARKERS: tuple = (
    # markdown table
    "\n|---", "|---|", "\n| ",
    # data_workbench outputs
    "Profil de `", "Lignes :", "Colonnes :", "Encoding :",
    "Lignes scannées", "Matched :", "Retournées", "Valeurs distinctes",
    "Group by :", "Agrégation",
    # datagouv outputs
    "datasets trouvés", "resources téléchargeables", "Téléchargé :",
    "format détecté", "Hash MD5",
)


def _obs_looks_tabular(obs_content: str) -> bool:
    """True si l'observation contient un livrable structuré exploitable.

    Détecte les sorties data_workbench / datagouv qui peuvent servir de
    réponse de secours si le LLM échoue à produire un FINAL exploitable.
    """
    if not obs_content or len(obs_content.strip()) < 80:
        return False
    n_markers = sum(1 for m in _TABULAR_OBS_MARKERS if m in obs_content)
    return n_markers >= 2


_TEST_RESULT_TOOL_NAMES: frozenset = frozenset({
    "run_command", "run_shell", "exec_command", "process_status",
})
_TEST_RESULT_RE = re.compile(
    r"(?im)(?:^|\s)(?:\d+\s+passed|\d+\s+failed|\d+\s+error(?:s)?|"
    r"tests?\s+passed|tests?\s+failed)(?:\s|$)"
)


def _obs_looks_like_test_result(obs_content: str, tool_name: str) -> bool:
    """True pour une preuve d'execution de tests, verte ou rouge.

    Ce signal ne sert qu'au dernier fallback anti-THOUGHT : apres epuisement des
    reformulations, mieux vaut livrer le resultat pytest reel que retourner une
    reponse vide. Le tool gate evite de prendre une phrase LLM pour une preuve.
    """
    if (tool_name or "").strip().lower() not in _TEST_RESULT_TOOL_NAMES:
        return False
    content = (obs_content or "").strip()
    if len(content) < 20:
        return False
    return bool(_TEST_RESULT_RE.search(content))


def _should_repair_incomplete_final(
    *,
    stagnation_streak: int,
    plan_business_complete: bool,
    document_free_grounded: bool,
    looks_incomplete: bool,
) -> bool:
    """Gate the generic final repair with stronger run-scoped proof."""
    return bool(
        stagnation_streak == 0
        and not plan_business_complete
        and not document_free_grounded
        and looks_incomplete
    )


_PHASE27_MCP_LOOP_TOOLS: frozenset = frozenset({
    "request_mcp_capability",
    "request_mcp_ticket",
    "run_mcp_autonomy",
    "resume_mcp_task",
    # Phase I-8 (Fix AL) : add_mcp était le SEUL outil du flux sans
    # guidance — après `mcp_added` le LLM ne savait pas qu'il fallait
    # enchaîner run_mcp_autonomy (observé runtime 2026-06-11 22:44 :
    # errance discover_tools/python -c/API au lieu d'install+activate).
    "add_mcp",
})


def _phase27_mcp_observation_guidance(tool_name: str, observation_content: str) -> Optional[str]:
    """Return safe conversational guidance after a Phase 26 MCP loop tool.

    The guidance is deterministic and read-only. It never executes approvals,
    installs, activations, subprocesses, or catalog mutations.
    """
    if tool_name not in _PHASE27_MCP_LOOP_TOOLS:
        return None
    if not observation_content:
        return None
    try:
        data = json.loads(observation_content)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    payload = data.get("payload")
    if not isinstance(payload, dict):
        payload = {}
    decision = str(data.get("decision") or payload.get("mapped_decision") or "").strip()
    recommendation = str(payload.get("recommendation_code") or "").strip()
    ticket_id = payload.get("proposed_ticket_action_id")
    target_server_id = payload.get("target_server_id")

    if recommendation in {
        "use_existing",
        "already_applied",
        "autonomy_ready_to_use",
        "resume_ready_to_use",
    }:
        return (
            "MCP_LOOP_GUIDANCE: La capacite MCP semble deja disponible. "
            "Continue la tache avec les outils visibles, sans creer de ticket. "
            "Si un target_tool_name est fourni, appelle cet outil pour finir."
        )
    if recommendation == "mcp_github_no_package":
        # Phase I-8 (Fix AS) : repo GitHub sans package npm/PyPI dans le
        # README — Lumena n'installe jamais depuis les sources.
        return (
            "MCP_LOOP_GUIDANCE: Le README de ce repo GitHub ne mentionne "
            "aucun package npm/PyPI installable. Lumena n'installe PAS "
            "depuis les sources (registres uniquement, securite). Demande "
            "a l'utilisateur le nom EXACT du package (npm:<nom> ou "
            "pypi:<nom>) — n'invente JAMAIS un nom de package, et ne tente "
            "JAMAIS git clone / pip / npm en shell."
        )
    if recommendation in {"mcp_added", "mcp_target_resolved"}:
        # Phase I-8 (Fix AL) : add_mcp ne fait QUE cataloguer/resoudre.
        # L'install + l'activation passent par run_mcp_autonomy.
        if recommendation == "mcp_target_resolved":
            return (
                "MCP_LOOP_GUIDANCE: Cible resolue (dry-run, AUCUNE mutation). "
                "Pour cataloguer reellement: add_mcp avec live=true et "
                "confirmation_phrase=\"I-CONFIRM-ADD-MCP\" generee TOI-MEME."
            )
        next_hint = ""
        if payload.get("approval_ticket_id"):
            next_hint = (
                " Un ticket d'approbation a ete cree: demande a "
                "l'utilisateur de l'approuver dans MCP > Approbations puis "
                "de dire 'fait'."
            )
        else:
            next_hint = (
                " AUCUN ticket a approuver (entree deja au catalogue ou "
                "auto-acceptee) — ne demande PAS d'approbation a "
                "l'utilisateur."
            )
        return (
            "MCP_LOOP_GUIDANCE: Le package est au catalogue mais N'EST PAS "
            "installe ni active — add_mcp ne fait que cataloguer."
            + next_hint +
            " Etape suivante OBLIGATOIRE: appelle run_mcp_autonomy("
            "intent=\"utiliser <nom du package>\", live=true, "
            "confirmation_phrase=\"I-CONFIRM-MCP-AUTONOMY\") qui enchaine "
            "install + activation + enregistrement des tools. "
            "JAMAIS pip/npm install en shell. Ne dis jamais que le MCP est "
            "installe ou actif avant observation explicite."
        )
    if recommendation == "needs_local_creation":
        return (
            "MCP_LOOP_GUIDANCE: Une creation locale MCP est necessaire. "
            "Si un ticket mcp_local_create vient d'etre approuve dans le panel, "
            "ne cree pas un nouveau ticket: dis a l'utilisateur de cliquer "
            "`Materialiser local MCP` dans MCP > Approvals/Decisions recentes, "
            "puis de reprendre la demande. Sinon, appelle request_mcp_ticket "
            "avec confirmation_phrase=\"I-CONFIRM-MCP-TICKET\" et live=true. "
            "Ne dis jamais que le MCP est installe ou actif avant observation "
            "explicite."
        )
    if recommendation in {
        "needs_install_approval",
        "needs_activation_approval",
        "needs_catalog_approval",
    }:
        # Phase I-8 (Fix AH) : guidance vers run_mcp_autonomy (l'outil que
        # le LLM A dans sa liste) avec SA phrase. L'ancienne guidance
        # pointait request_mcp_ticket (hors liste) avec I-CONFIRM-MCP-TICKET
        # → DeepSeek transposait la mauvaise phrase sur run_mcp_autonomy
        # (observe runtime 2026-06-11 17:41, boucle confirmation_phrase_invalid).
        # Phase I-8 (Fix AU.2) : guidance DIRECTIVE. L'ancien « Si
        # l'utilisateur veut continuer » poussait DeepSeek a redemander
        # un 'oui' alors que la demande initiale EST le consentement
        # (observe runtime 2026-06-12 10:36 : install duckduckgo jamais
        # lancee, l'utilisateur a du re-confirmer pour rien). Le gate
        # humain reel est le ticket panel — le pipeline le redemandera
        # lui-meme si necessaire.
        return (
            "MCP_LOOP_GUIDANCE: Une action MCP est necessaire et la "
            "demande de l'utilisateur EST deja son accord. Rappelle "
            "MAINTENANT run_mcp_autonomy avec le MEME intent, live=true et "
            "confirmation_phrase=\"I-CONFIRM-MCP-AUTONOMY\" (la phrase "
            "EXACTE de run_mcp_autonomy — pas une autre). "
            "GENERE cette phrase TOI-MEME dans l'appel d'outil — ne demande "
            "JAMAIS a l'utilisateur de la taper, et ne lui redemande PAS "
            "un 'oui' : si une approbation humaine est requise, le systeme "
            "creera un ticket et te le dira. Ne dis jamais que "
            "le MCP est installe ou actif avant observation explicite. "
            "N'utilise pas plan_create ni CodeAgent pour remplacer "
            "le flux MCP."
        )
    if recommendation in {"ticket_would_be_proposed", "autonomy_would_run"}:
        return (
            "MCP_LOOP_GUIDANCE: Un ticket MCP serait cree en mode live. "
            "Explique a l'utilisateur qu'une confirmation/admin UI est requise. "
            "Ne tente aucune installation ni activation silencieuse."
        )
    if recommendation in {"ticket_proposed", "waiting_approval", "autonomy_ticket_created"}:
        suffix = ""
        if isinstance(ticket_id, str) and ticket_id:
            suffix += f" ticket_id={ticket_id}."
        if isinstance(target_server_id, str) and target_server_id:
            suffix += f" server_id={target_server_id}."
        return (
            "MCP_LOOP_GUIDANCE: Ticket MCP pending. Dis a l'utilisateur de "
            "l'approuver dans le panel MCP (MCP > Approbations) puis de te "
            "dire simplement 'fait'. A ce moment-la, rappelle run_mcp_autonomy "
            "avec le MEME intent qu'au depart, live=true et "
            "confirmation_phrase=\"I-CONFIRM-MCP-AUTONOMY\" generee TOI-MEME "
            "(ne demande JAMAIS a l'utilisateur de taper une phrase). "
            "L'approbation du catalogue suffit : install et activation "
            f"s'enchainent ensuite automatiquement.{suffix}"
        )
    if recommendation in {
        "blocked",
        "no_safe_path",
        "phase24_unavailable",
        "phase25_unavailable",
        "live_requirements_not_met",
        "confirmation_phrase_invalid",
        "caller_kind_not_allowed",
        "code_agent_out_of_scope",
    } or decision == "blocked":
        return (
            "MCP_LOOP_GUIDANCE: Aucun chemin MCP safe n'a ete trouve. "
            "Reponds honnetement avec le blocage utile, sans inventer de "
            "capacite ni promettre une installation."
        )
    return None


def _synthesize_response_from_observation(
    obs_content: str, tool_name: str, original_query: str
) -> Optional[str]:
    """Construit une réponse FINAL minimale à partir d'une observation outil.

    Utilisé uniquement quand le LLM échoue à reformuler une réponse après
    plusieurs repairs. La réponse est explicitement marquée comme un fallback
    pour que l'utilisateur sache que c'est une synthèse automatique.
    """
    if not (
        _obs_looks_tabular(obs_content)
        or _obs_looks_like_test_result(obs_content, tool_name)
    ):
        return None
    # Bornes : 6 KB max
    body = obs_content.strip()
    if len(body) > 6000:
        body = body[:6000] + "\n\n[…contenu tronqué…]"
    tool_label = tool_name or "outil"
    return (
        f"Voici le résultat de `{tool_label}` :\n\n"
        f"{body}\n\n"
        f"_(Réponse générée à partir de la dernière observation outil — "
        f"le LLM n'a pas produit de synthèse exploitable.)_"
    )


def _synthesize_mission_response_from_evidence(
    evidence: Sequence[Tuple[str, str, bool]],
) -> Optional[str]:
    """Construit un bilan de mission depuis plusieurs preuves outil réussies.

    Ce filet n'est utilisé qu'après épuisement des repairs anti-THOUGHT. Il ne
    transforme jamais une simple navigation ou une observation en succès :
    chaque section exige l'outil autoritatif correspondant et une observation
    marquée comme réussie.
    """
    selected: Dict[str, str] = {}
    for tool_name, raw_observation, success in evidence:
        if not success:
            continue
        tool = str(tool_name or "").strip().lower()
        observation = str(raw_observation or "").strip()
        if not observation:
            continue

        if tool == "publish_mission_workspace":
            first_block = re.split(
                r"\n\s*(?:➡️|🌐|Prochaine étape)", observation, maxsplit=1
            )[0]
            selected["publication"] = first_block[:1400].strip()
        elif tool == "run_command" and _obs_looks_like_test_result(observation, tool):
            summaries = [
                line.strip()
                for line in observation.splitlines()
                if re.search(r"\b(?:passed|failed|errors?)\b", line, re.IGNORECASE)
            ]
            if summaries:
                selected["tests"] = summaries[-1][:500]
        elif tool in {"generate_studio_document", "generate_studio_documents"}:
            studio = observation
            try:
                payload = json.loads(observation)
                if isinstance(payload, dict):
                    filename = str(payload.get("filename") or "document")
                    size = payload.get("size")
                    verified = payload.get("render_verified") is True
                    studio = (
                        f"{filename} — rendu {'vérifié' if verified else 'généré'}"
                        + (f" — {size} octets" if isinstance(size, (int, float)) else "")
                    )
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
            selected["studio"] = studio[:800].strip()
        elif (
            tool == "browser_verify_local_project"
            and "runtime web verify: ok" in observation.lower()
        ):
            useful = []
            for line in observation.splitlines():
                stripped = line.strip()
                if (
                    "Runtime web verify: OK" in stripped
                    or stripped.startswith("URL:")
                    or stripped.startswith("- title:")
                    or stripped.startswith("Project:")
                ):
                    useful.append(stripped)
            selected["browser"] = "\n".join(useful)[:900].strip()
        elif tool == "delegate_and_wait":
            # H7 (TEST RÉEL n°2, mission `uv` 2026-08-13) — ne garder que la
            # PREMIÈRE LIGNE revenait à ne garder que l'en-tête « Délégation :
            # 2/2 terminée(s) : » et à jeter ce qui suit : **les rapports des
            # workers**, que l'observation présente pourtant comme « les
            # LIVRABLES des workers ». Pour une mission de CODE, le livrable est
            # sur le disque et le bilan pouvait rester maigre ; pour une mission
            # d'EFFETS (H4), ce rapport EST le livrable — le mémo enregistré en
            # mémoire n'apparaissait nulle part dans le bilan rendu.
            # Même cap que « publication », qui garde déjà un bloc entier.
            _deleg = observation.strip()
            # Le footer de steering s'adresse au LEAD pendant le run ; il n'a
            # aucun sens dans un bilan livré à l'utilisateur.
            for _mark in ("\n\n➡️ Ce sont les LIVRABLES", "\n\n⛔ RÉSULTAT PARTIEL"):
                _cut = _deleg.find(_mark)
                if _cut > 0:
                    _deleg = _deleg[:_cut].strip()
            selected["delegation"] = _deleg[:1400].strip()

    if not selected:
        return None

    labels = (
        ("publication", "Livraison"),
        ("tests", "Tests"),
        ("studio", "Document Studio"),
        ("browser", "Navigateur"),
        ("delegation", "Délégation"),
    )
    parts = ["Mission terminée. Voici le bilan factuel issu des preuves enregistrées :"]
    for key, label in labels:
        value = selected.get(key)
        if value:
            parts.append(f"**{label}**\n{value}")
    parts.append(
        "_(Bilan de secours construit depuis les observations outil réussies : "
        "aucune étape non prouvée n'est déclarée terminée.)_"
    )
    return "\n\n".join(parts)[:6000]
# LOT P3 (run HuffPack v2, 2026-08-14) — le compteur de RELECTURE n'était, lui,
# jamais remis à zéro : il montait sur toute la durée de la mission, et trois
# relectures du même fichier suffisaient à FORCER LE FINAL. Or forcer un FINAL
# dans un tour de chat est bénin (on rend une réponse) ; dans une MISSION, c'est
# la tuer.
#
# Timeline exacte du run :
#   04:36  write_file  core.py          → écriture RÉUSSIE
#   04:37  pytest      12 passed        → le codec est réparé
#   04:38  edit_file   test_huffpack.py → écriture RÉUSSIE (4 tests ajoutés)
#   04:38  pytest      16 passed, 1 failed  → un seuil à ajuster, rien de plus
#   04:42  ⚠️ read_file stagnation — forçage FINAL (relectures=3)
#          budget restant : 4 775 s = 79 MINUTES · aucun rapport, aucune publication
#
# Deux écritures réussies s'étaient intercalées sans faire redescendre le
# compteur. C'est mot pour mot le défaut que PG-1.a a corrigé pour le compteur
# de progression du plan (SkiLoc : « FINAL forcé avec 2 048 s de budget restant,
# à une itération de la victoire »). On applique ici la même règle, plus un
# garde-fou : tant qu'il reste du budget, une mission est REDIRIGÉE, pas achevée.
_READ_STAGNATION_BUDGET_FLOOR_S: float = 300.0


def read_stagnation_action(
    *,
    is_mission_run: bool,
    budget_remaining_s: float,
    shots_used: int,
) -> str:
    """« redirect » ou « end » face à une stagnation de lecture. Pur/testable.

    Hors mission : toujours « end » — le comportement historique du chat ne
    bouge pas d'un pouce. En mission : « redirect » une seule fois, et
    uniquement s'il reste réellement du temps de travail.
    """
    if not is_mission_run:
        return "end"
    if int(shots_used or 0) >= 1:
        return "end"
    try:
        remaining = float(budget_remaining_s)
    except (TypeError, ValueError):
        return "end"
    return "redirect" if remaining > _READ_STAGNATION_BUDGET_FLOOR_S else "end"


# ══════════════════════════════════════════════════════════════════════════
#  Lot RF-9a — INGESTION D'OBSERVATION (premiere feuille de `_run_internal`)
#
#  Le §15 du plan nomme six feuilles extractibles ; « ingestion d'observation »
#  en fait partie. L'audit AST a trouve 91 blocs PURS dans `_run_internal` ; le
#  plus gros groupe coherent etait la compaction des observations volumineuses
#  (l. 8737-8838, 102 lignes, 11 lectures / 11 ecritures).
#
#  ⚠️ Le critere du §15 n'est PAS le nombre de lignes — `react.py` etait deja
#  dans la cible annoncee (8 500-10 000). C'est : « une reduction
#  supplementaire n'est acceptee que si elle diminue REELLEMENT l'etat
#  partage ». Cette feuille emporte six locales qui ne servaient qu'a elle.
#
#  RESTENT dans la boucle, parce que ce sont des EFFETS : la reconstruction du
#  `ReActStep` et le `logger.debug` de compaction.
#
#  ⚠️ `_compact_browser_observation_payload` vit dans `browser_reasoning.py`.
#  L'importer casserait le contrat que RF-2 s'est donne — « aucune dependance
#  projet » — et son propre test l'a attrape. Il passe donc en PARAMETRE.
# ══════════════════════════════════════════════════════════════════════════

# Deplacee de `react.py` par RF-9a AVEC SA RAISON : la separer de son en-tete
# aurait laisse dans `react.py` un commentaire orphelin decrivant une
# constante absente — et prive celle-ci de ce qui la justifie.
# LOT Z12 — outils dont l'observation PORTE DU CONTENU À LIRE (fichiers, contrat,
# résultats groupés). Source UNIQUE : cette liste sert au seuil de déclenchement
# de la compaction ET à la stratégie appliquée quand ce seuil est franchi.
#
# Elle existe parce que la même notion était écrite DEUX FOIS et que les deux
# copies ont divergé. Le seuil (8000) contenait déjà `read_files_batch`,
# `parallel_tools` et `write_mission_contract` — ajoutés par B0.3 (run PlantCare)
# et C0.1 (run FrigoZen) — mais la STRATÉGIE ne connaissait que `read_file`,
# `search_in_code`, `grep_search`, `find_files`. Conséquence : ces trois outils
# étaient protégés jusqu'à 8000 caractères… puis réduits à 800 au lieu de 3000.
# Un seuil élevé leur donnait une fausse sécurité : plus le contenu était gros,
# plus la perte était totale.
#
# Run « Rustine » (2026-08-16) : le lead avait identifié la cause exacte du
# défaut de style (« le CSS utilise des classes françaises, les HTML des classes
# anglaises ») et cherchait les classes à corriger. Ses `read_files_batch`
# rendaient 8437 et 8669 chars — juste au-dessus du seuil — et il en recevait
# 830 : le `<head>` et le pied de page, jamais le corps. Dix itérations de
# relecture, puis mort par PLAN GUARD anti-stagnation, livrable à 0 % de style.
#
# Les 11 compactions de tout le corpus portent EXACTEMENT sur les 3 outils mal
# classés (read_files_batch 6×, write_mission_contract 3×, grep_batch 2×) et
# ZÉRO sur ceux qui étaient dans les deux listes.
_OBS_FILE_READ_TOOLS: frozenset = frozenset({
    "read_file", "read_files_batch", "search_in_code", "grep_search",
    "grep_batch", "find_files", "parallel_tools", "write_mission_contract",
})


def _extract_anchor_facts(text: str) -> str:
    """Extrait les faits structures cles d'une observation avant compaction.

    Retourne une ligne « 📌 Ancres: ... » ou "" si rien de notable.
    Couverture : snowflakes Discord (17-20 chiffres), patterns
    guild_id=/channel_id=/server_id=, chemins Windows.

    Deplacee de `react.py` par RF-9a : elle n'y avait qu'UN seul appelant, dans
    cette feuille meme.
    """
    facts: list[str] = []

    # Snowflake IDs Discord (17-20 chiffres, pas dans un chemin)
    for m in re.finditer(r'(?<![/\\.\d])\b(\d{17,20})\b(?![/\\.\d])', text):
        facts.append(m.group(1))

    # guild_id=... / channel_id=... (valeur alphanumerique ou entre quotes)
    for m in re.finditer(
        r'\b(?:guild_id|channel_id|server_id)\s*[=:]\s*[`"\']?(\w{6,})[`"\']?',
        text, re.IGNORECASE,
    ):
        facts.append(f"{m.group(0).split('=')[0].split(':')[0].strip()}={m.group(1)}")

    # Chemins Windows (C:\...) — juste le segment racine pour ne pas gonfler
    for m in re.finditer(r'[A-Za-z]:\\(?:[^\s\n"\']{3,60})', text):
        facts.append(m.group(0))

    if not facts:
        return ""
    uniques: list[str] = []
    for f in facts:
        if f not in uniques:
            uniques.append(f)
    return "📌 Ancres: " + " | ".join(uniques[:6]) + "\n"


def thought_is_stagnant(
    thought_content: str,
    previous_thoughts,
    original_query: str,
    loop_risk: str,
) -> bool:
    """Lot RF-9b — 2.1 Détection de stagnation de pensée (thoughts quasi-identiques).

    Deplacee de `_run_internal` : quatre entrees, une decision booleenne. Les
    mutations de l'historique (`append`, troncature a 5) restent dans la boucle.

    Deux detections, conservees telles quelles :

      1. recouvrement de vocabulaire sur les DEUX pensees precedentes, seuil
         adaptatif ;
      2. prefixe commun sur trois pensees + la courante.
    """
    if not thought_content:
        return False
    _current_words = set(thought_content.lower().split())
    _is_stagnant = False
    if len(previous_thoughts) >= 2:
        _last_words = set(previous_thoughts[-1].lower().split())
        if _current_words and _last_words:
            _overlap = len(_current_words & _last_words) / max(len(_current_words | _last_words), 1)
            _prev_words = set(previous_thoughts[-2].lower().split())
            _overlap2 = len(_current_words & _prev_words) / max(len(_current_words | _prev_words), 1)
            # Seuil adaptatif : 65% si requête courte (≤5 mots), 80% sinon
            # P5 — modèles à loop_risk élevé : seuil abaissé pour détection plus tôt
            _q_words = len(original_query.split())
            _base_thresh = 0.65 if _q_words <= 5 else 0.80
            _thresh = (
                _base_thresh - 0.10 if loop_risk == "high" else
                _base_thresh - 0.05 if loop_risk == "medium" else
                _base_thresh
            )
            if _overlap > _thresh and _overlap2 > _thresh:
                _is_stagnant = True
    # Détection secondaire : 3+ actions read-only consécutives sur même sujet
    if not _is_stagnant and len(previous_thoughts) >= 3:
        _recent_3 = list(previous_thoughts[-3:]) + [thought_content]
        _common_prefix = set(_recent_3[0].lower().split()[:15])
        _all_share = all(
            len(_common_prefix & set(t.lower().split()[:15])) / max(len(_common_prefix), 1) > 0.60
            for t in _recent_3[1:]
        )
        if _all_share:
            _is_stagnant = True
    return _is_stagnant


#: Lot RF-9c — mots-cles de requete -> outils pertinents, pour l'indice donne
#: au modele quand il piétine. Deplace de `_run_internal`.
_STAG_KW_MAP = [
    (("pdf", "rapport", "document", "facture", "devis"),
     ["generate_studio_document", "generate_studio_documents", "list_document_models", "create_pdf", "create_docx", "create_invoice_pdf", "create_from_template"]),
    (("site", "web", "html", "page"),
     ["create_project", "generate_website", "write_file"]),
    (("image", "photo", "capture"),
     ["generate_image", "screenshot", "screenshot_analyze"]),
    (("mail", "email", "courriel"),
     ["send_email", "mail_send"]),
]


def stagnation_tool_hint(original_query: str, available_tools) -> str:
    """Lot RF-9c — quels outils NOMMER au modele qui piétine ?

    Quand la boucle detecte une stagnation, elle ne dit pas seulement « tu
    piétines » : elle nomme les outils pertinents pour la requete, **parmi ceux
    reellement disponibles**. Suggerer un outil absent du registre enverrait le
    modele contre un mur.

    Rend l'indice pret a concatener, ou "" s'il n'y a rien de pertinent.
    """
    _q_low = (original_query or "").lower()
    _stag_relevant: list = []
    for _kws, _tools in _STAG_KW_MAP:
        if any(k in _q_low for k in _kws):
            _stag_relevant.extend(t for t in _tools if t in (available_tools or {}))
    if not _stag_relevant:
        return ""
    return (
        " Outils disponibles pour cette tâche : "
        + ", ".join(f"`{t}`" for t in _stag_relevant[:5])
        + ". Utilise-les directement."
    )


#: Lot RF-9c — verbes qui signalent une demande de CREATION (pas de recherche).
_CREATION_KEYWORDS = (
    "créer", "creer", "cree", "crée", "créé", "génère", "genere", "rédige", "redige",
    "écris", "ecris", "prépare", "prepare", "fais", "produis", "structure",
    "create", "write", "generate", "make", "build",
)


def repeated_listing_reminder(already_created: bool, original_query: str) -> str:
    """Lot RF-9c — que repondre a un `list_directory` repete ?

    Trois issues distinctes, conservees telles quelles :

      * creation deja faite -> le listage est de la navigation legitime ;
      * l'utilisateur demande de CREER -> ordonner d'arreter d'explorer ;
      * l'utilisateur CHERCHE -> ordonner l'honnetete, et surtout ne JAMAIS
        inventer un fichier absent.

    Rend le texte a concatener ; la concatenation elle-meme reste dans la
    boucle.
    """
    if already_created:
        # Création déjà faite — list_directory est de la navigation légitime
        return (
            "\n\n⚠️ RAPPEL: tu as déjà exploré ce chemin. "
            "Avance vers l'étape suivante."
        )
    # Détecter si la requête demande de CRÉER des fichiers (pas de les chercher)
    query_lower = (original_query or "").lower()
    user_wants_creation = any(kw in query_lower for kw in _CREATION_KEYWORDS)
    if user_wants_creation:
        return (
            "\n\n⚠️ STOP EXPLORATION: tu as DEJA explore ce chemin et l'utilisateur "
            "te demande de CREER des fichiers. Arrete list_directory MAINTENANT.\n"
            "ACTION OBLIGATOIRE: utilise write_file pour creer chaque fichier demandé "
            "(un par un, PAS parallel_tools). Puis utilise telegram_send_document ou send_whatsapp_document si "
            "l'utilisateur veut les recevoir."
        )
    return (
        "\n\n⚠️ RAPPEL: tu as DEJA explore ce chemin. "
        "Si le fichier cherche n'est PAS la, DIS-LE HONNETEMENT a l'utilisateur avec ACTION: FINAL. "
        "NE CREE PAS de fichier invente. Ne refais PAS list_directory sur un chemin deja vu."
    )


def plan_stagnation_message(task_plan) -> str:
    """Lot RF-9d — que dire au modele qui n'avance plus sur son plan ?

    Nomme la prochaine tache non faite quand il y en a une, et propose
    TOUJOURS la sortie honnete : sans « termine avec FINAL si la tache est
    impossible », le garde enfermerait le modele dans une boucle qu'il ne peut
    pas finir.
    """
    next_task = next((t for t in (task_plan or []) if not t.completed), None)
    msg = (
        "\n\n[SYSTEME] ATTENTION: Aucune progression sur ton plan depuis plusieurs iterations. "
        "Passe a l'action suivante ou termine avec FINAL si la tache est impossible."
    )
    if next_task:
        msg += f"\nPROCHAINE TACHE A FAIRE: {next_task.description}"
    return msg


def web_files_present(written_paths) -> tuple:
    """Lot RF-9d — quels types de fichiers web ont ete ecrits ? `(html, css, js)`.

    ⚠️ Ces trois drapeaux sont lus ~700 lignes PLUS BAS dans `_run_internal`
    (« Adapter le hint de conclusion selon l'avancement reel »). Une premiere
    version de ce lot les avait supprimes de la boucle en croyant qu'ils lui
    etaient locaux : la mesure disait pourtant qu'ils ne disparaissaient PAS.
    Ils sont donc RENDUS, pas absorbes.
    """
    created_files = list(written_paths or [])
    return (
        any(".html" in f for f in created_files),
        any(".css" in f for f in created_files),
        any(".js" in f for f in created_files),
    )


def web_files_reminder(written_paths) -> str:
    """Lot RF-9d — quels fichiers web manquent encore ?

    Rend le rappel pret a concatener. `written_paths` = les chemins deja ecrits
    par `write_file` durant ce run.
    """
    created_files = list(written_paths or [])
    has_html, has_css, has_js = web_files_present(created_files)
    return f"""
Fichiers web créés: {', '.join(created_files) if created_files else 'Aucun'}
Fichiers web potentiellement manquants: {'index.html ' if not has_html else ''}{'style.css ' if not has_css else ''}{'script.js' if not has_js else ''}
"""


def phantom_channels(claim_channels, actual_channels) -> set:
    """Lot RF-9d — DISCORD COUNT GUARD : quels salons le FINAL a-t-il INVENTES ?

    Rend les salons revendiques dont aucun envoi n'a reussi. Le `#` et la casse
    sont normalises des deux cotes.
    """
    if not claim_channels:
        return set()
    _claimed_set = {c.lower().strip("#").strip() for c in claim_channels}
    return _claimed_set - set(actual_channels or set())


def workspace_path_from_query(query: str, root) -> Optional[str]:
    """Lot RF-9d — un chemin de workspace est-il ecrit dans la requete ?

    Rend le chemin **s'il existe sur le disque**, sinon None. Le garde est
    la : un chemin plausible mais absent n'est pas un projet.
    """
    import os as _os

    _esc_qm = re.search(
        r'([A-Za-z]:\\[^\s]+?[\\/]workspace[\\/][\w\-]+)', query or "",
    )
    if not _esc_qm:
        _esc_qm = re.search(r'(workspace[\\/][\w\-]+)', query or "")
    if not _esc_qm:
        return None
    _cand = _esc_qm.group(1)
    if not _os.path.isabs(_cand) and root:
        _cand = _os.path.join(str(root), _cand)
    return _cand if _os.path.isdir(_cand) else None


def observation_compact_limit(tool_name: str, *, is_chat_surface: bool) -> int:
    """Seuil de compaction, par type d'outil.

    Le modele a deja vu l'observation complete — on stocke une version compacte
    pour que les futures iterations ne soient pas noyees dans du contenu stale.
    """
    if tool_name == "delegate_and_wait":
        # Les LIVRABLES des workers doivent rester INTACTS pour que le lead
        # fusionne sans re-fouiller le disque (sinon le fix excerpt est gâché).
        return 20000
    if tool_name in _OBS_FILE_READ_TOOLS:
        # B0.3 (run PlantCare) : read_files_batch et parallel_tools étaient
        # ABSENTS de cette liste → compactés à ~830 chars → les workers
        # relisaient les mêmes fichiers en boucle (w_tests mort sans écrire).
        # C0.1 (run FrigoZen) : l'observation de write_mission_contract PORTE
        # les objectifs contractuels (allowed_files) — compactée à 830 chars,
        # le lead re-rédigeait ses propres objectifs divergents du contrat.
        return 8000
    if tool_name in ("browser_get_content", "browser_evaluate"):
        # Fix A: Pour les surfaces chat, augmenter la limite pour ne pas tronquer la conversation
        return 4000 if is_chat_surface else 1800
    return 3000


def compact_observation_body(
    tool_name: str,
    content: str,
    is_chat_surface: bool,
    *,
    compact_browser=None,
) -> Optional[str]:
    """LA decision typee de la feuille : le corps compacte, ou None.

    `None` signifie « pas de compaction » — l'observation passe telle quelle.
    Aucun effet : la reconstruction du `ReActStep` et le journal restent dans
    `_run_internal`.

    `compact_browser` est injecte par `react.py` (RF-1) : ce module n'a aucune
    dependance projet, contrat pose par RF-2.
    """
    if not content:
        return None
    raw_len = len(content)
    if raw_len <= observation_compact_limit(
        tool_name, is_chat_surface=is_chat_surface
    ):
        return None

    anchor = _extract_anchor_facts(content)
    if compact_browser is not None:
        browser_compacted = compact_browser(
            tool_name, content, is_chat_surface=is_chat_surface,
        )
        if browser_compacted is not None:
            return browser_compacted

    if tool_name == "generate_studio_documents":
        from src.documents.delivery_manifest import compact_batch_observation

        body = compact_batch_observation(content)
        if body is not None:
            return body
        head, tail = content[:500], content[-300:]
        return (
            f"{anchor}{head}\n"
            f"[...{raw_len - 800} chars compactes...]\n"
            f"{tail}"
        )

    if tool_name in (
        "delegate_task", "create_project", "generate_website",
        "write_website_files", "website_build",
    ):
        # Résultats de délégation : garder début (statut) + fin (conclusion)
        head, tail = content[:600], content[-200:]
        return (
            f"{anchor}{head}\n[...{raw_len - 800} chars compactés — "
            f"contenu disponible sur demande...]\n{tail}"
        )

    if tool_name in ("run_command", "execute_code", "dev_run_fix"):
        # Sorties de commandes : garder début (env) + fin (résultat/erreur)
        head, tail = content[:400], content[-400:]
        return (
            f"{anchor}{head}\n[...sortie tronquée ({raw_len} chars)...]\n{tail}"
        )

    if tool_name in _OBS_FILE_READ_TOOLS:
        # Lectures fichiers : seuil élevé atteint → garder 3000 chars (début)
        # Pas d'ancre ici : le contenu brut est déjà préservé intégralement
        #
        # LOT Z12 — cette liste était écrite EN DUR et ne connaissait que
        # `read_file`/`search_in_code`/`grep_search`/`find_files`. Elle
        # partage désormais `_OBS_FILE_READ_TOOLS` avec le seuil ci-dessus :
        # un outil protégé jusqu'à 8000 chars ne peut plus se retrouver
        # réduit à 800 dès qu'il les dépasse.
        return (
            content[:3000]
            + f"\n[...{raw_len - 3000} chars omis — relire avec plage de lignes si nécessaire...]"
        )

    head, tail = content[:500], content[-300:]
    return (
        f"{anchor}{head}\n[...{raw_len - 800} chars compactés...]\n{tail}"
    )
