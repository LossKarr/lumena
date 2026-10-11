"""LOT S2 (2026-10-09) — LUMENA SAIT CE QU'ELLE FAIT PENDANT QU'ELLE TE PARLE.

===============================================================================
  UNE ENTITE QUI OUBLIE SES PROPRES ACTES EN COURS N'A PAS D'UNITE.
===============================================================================

Aujourd'hui, si une mission tourne en arriere-plan, Lumena l'ignore en te repondant.
`autonomy_activity_summary` existe — mais il faut qu'elle PENSE a l'appeler. C'est le
motif des 60 lots : le fait existe, il est mesure, et il ne decide de rien.

--- Ce que le corpus REEL a impose comme choix, mesure le 2026-10-09 ---

1. **La source est l'orchestrateur VIVANT, pas le JSON sur disque.**
   `data/task_orchestrator_state.json` pesait 1,8 Mo et datait de la VEILLE
   (`saved_at` = 2026-10-08T16:40). Ses 174 taches : 163 `done`, 11 `cancelled`,
   **zero en cours**. Une tache qui demarre n'y apparait qu'apres sauvegarde. Lire ce
   fichier aurait reproduit exactement le defaut que S1 vient de corriger : reciter un
   instantane perime. L'orchestrateur, lui, est deja un attribut de la boucle ReAct.

2. **Le ledger d'activite ne dit pas ce qu'on croit.**
   Sur ses 1 487 evenements, **1 201 (81 %) sont un sondage `peer_delegate` repete** :
   toujours la meme description « Rediger un rapport », logguee DEUX fois par tentative
   (`executed` ET `failed`, a la meme seconde), quelques fois par jour depuis juillet.
   Annoncer naivement la derniere ligne du ledger aurait fait dire a Lumena qu'elle
   venait de rediger un rapport. Les vrais actes autonomes (`action_*`) avaient 5 JOURS.
   On ne garde donc que les `action_*`, et seulement s'ils sont RECENTS.

--- Les regles, heritees de S1 ---

1. **Un FAIT, jamais une consigne.** Aucun ordre de comportement : elle ne doit pas
   devenir celle qui annonce ses taches de fond a chaque message.
2. **Rien en cours -> le bloc DISPARAIT.** Pas de ligne vide, pas de « 0 tache ».
3. **Jamais bloquant, jamais d'invention.** Un age au-dela du seuil se tait.
4. **Lecture seule.** Le ledger est append-only ; ce module ne l'ecrit jamais.

Module auto-contenu (stdlib) -> testable sans runtime.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

__all__ = ["bloc_activite", "LIMITE_CARACTERES", "ETATS_EN_COURS"]

# Budget de la phase PRESENCE : 500 car. pour TOUS les blocs. S1 en prend 200, S2 180.
LIMITE_CARACTERES = 180

# Les quatre etats non terminaux de `TaskState` (src/runtime/task_orchestrator.py:16).
# Les trois autres — done, failed, cancelled — sont de l'HISTOIRE, pas de la presence.
ETATS_EN_COURS = ("running", "waiting_io", "checkpointed", "queued")

_MAX_NOMS = 3                   # au-dela, on compte au lieu d'enumerer
_AGE_ACTE_MAX_MIN = 24 * 60     # un acte autonome plus vieux qu'un jour n'est plus « en cours »

_CHEMIN_LEDGER = Path("data/autonomy/activity_ledger.jsonl")
_MAX_OCTETS = 262_144           # meme parade que S1 : le ledger pesait 470 Ko et grossit


def _age_minutes(horodatage: Any) -> Optional[float]:
    """Age en minutes, ou None si illisible. Tolere les horodatages avec fuseau."""
    try:
        marque = datetime.fromisoformat(str(horodatage))
    except (TypeError, ValueError):
        return None
    maintenant = datetime.now(marque.tzinfo) if marque.tzinfo else datetime.now()
    return (maintenant - marque).total_seconds() / 60


def _age_lisible(minutes: float) -> str:
    if minutes < 60:
        return f"il y a {int(minutes)} min"
    return f"il y a {int(minutes // 60)} h"


_LIBELLES_TYPES = {
    "mission": "mission",
    "codeagent": "tâche de code",
    "agent_turn": "demande agent",
    "chat_turn": "réponse chat",
    "video": "rendu vidéo",
    "remotion": "rendu vidéo",
}


def _libelle_neutre(tache: Dict[str, Any]) -> str:
    """Décrit une tâche uniquement avec des valeurs internes en liste blanche.

    `message_preview`, `objective`, les titres et les identifiants sont du contenu
    utilisateur ou corrélable : aucun ne doit entrer dans le prompt système.
    """
    meta = tache.get("metadata") if isinstance(tache.get("metadata"), dict) else {}
    type_tache = str(meta.get("kind") or "").strip().lower()
    if type_tache in _LIBELLES_TYPES:
        return _LIBELLES_TYPES[type_tache]
    return "mission" if str(tache.get("channel") or "").lower() == "mission" else "tâche"


def _taches_en_cours(
    orchestrateur: Any,
    conversation_id: Optional[str],
    owner_user_id: Optional[str],
) -> List[Dict[str, Any]]:
    """Tâches vivantes du seul périmètre appelant, lues en mémoire.

    Sans identifiant de conversation, le comportement est volontairement fermé :
    mieux vaut taire une activité que révéler celle d'une autre conversation.
    """
    conv_id = str(conversation_id or "").strip()
    if not conv_id:
        return []
    lister = getattr(orchestrateur, "get_conversation_tasks", None)
    if not callable(lister):
        return []
    try:
        toutes = lister(conv_id, limit=200)
    except Exception:
        return []
    if not isinstance(toutes, list):
        return []
    owner = str(owner_user_id or "").strip()
    resultat: List[Dict[str, Any]] = []
    for tache in toutes:
        if not isinstance(tache, dict) or tache.get("state") not in ETATS_EN_COURS:
            continue
        if owner:
            meta = tache.get("metadata") if isinstance(tache.get("metadata"), dict) else {}
            requester = meta.get("requester") if isinstance(meta.get("requester"), dict) else {}
            task_owner = str(meta.get("owner_user_id") or requester.get("owner_user_id") or "").strip()
            if task_owner != owner:
                continue
        resultat.append(tache)
    return resultat


def _dernier_acte_autonome(chemin: Path) -> Optional[str]:
    """Le dernier acte autonome REEL et RECENT, ou None.

    On ignore les `peer_delegation_*` : 81 % du ledger, et un sondage repete.
    """
    try:
        taille = chemin.stat().st_size
        with chemin.open("rb") as fichier:
            if taille > _MAX_OCTETS:
                fichier.seek(taille - _MAX_OCTETS)
                fichier.readline()               # cette ligne est coupee : on la jette
            lignes = fichier.read().decode("utf-8", errors="replace").splitlines()
    except Exception:
        return None

    for ligne in reversed(lignes):
        ligne = ligne.strip()
        if not ligne:
            continue
        try:
            evenement = json.loads(ligne)
        except Exception:
            continue
        if not isinstance(evenement, dict):
            continue
        if str(evenement.get("event_type") or "") != "action_completed":
            continue                             # seuls les actes ABOUTIS comptent
        minutes = _age_minutes(evenement.get("timestamp"))
        if minutes is None or minutes > _AGE_ACTE_MAX_MIN or minutes < 0:
            return None                          # trop vieux : on se TAIT
        # `description` peut provenir d'une demande utilisateur. On n'injecte dans le
        # système que le fait contrôlé qu'un acte a abouti, jamais son texte libre.
        return f"dernier acte autonome terminé, {_age_lisible(minutes)}"
    return None


def bloc_activite(
    orchestrateur: Any = None,
    chemin_ledger: Optional[Path] = None,
    *,
    conversation_id: Optional[str] = None,
    owner_user_id: Optional[str] = None,
) -> str:
    """Rend la ligne d'activite a injecter, ou "" s'il n'y a rien en cours.

    Appelee a chaque tour, a cote de l'etat systeme. Ne leve jamais.
    """
    try:
        morceaux: List[str] = []

        taches = _taches_en_cours(orchestrateur, conversation_id, owner_user_id)
        if taches:
            missions = sum(1 for t in taches if t.get("channel") == "mission")
            if len(taches) <= _MAX_NOMS:
                morceaux.append(f"{len(taches)} en cours : " + " · ".join(_libelle_neutre(t) for t in taches))
            else:
                morceaux.append(f"{len(taches)} tâche(s) en cours")
            if missions:
                morceaux.append(f"dont {missions} mission(s)")

        acte = _dernier_acte_autonome(Path(chemin_ledger) if chemin_ledger is not None else _CHEMIN_LEDGER)
        if acte:
            morceaux.append(acte)

        if not morceaux:
            return ""                            # regle 2 : rien en cours -> le bloc disparait

        bloc = "[En cours] " + " | ".join(morceaux)
        # Anti-fuite : ce texte part au LLM, et `message_preview` vient de l'utilisateur.
        if "\\" in bloc or "/Users/" in bloc or "/home/" in bloc:
            return ""
        return bloc[:LIMITE_CARACTERES].rstrip(" |·")
    except Exception:
        return ""                                # regle 3 : jamais bloquant
