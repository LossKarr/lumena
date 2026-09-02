"""Journal de mission — ce qui reste quand la mission est finie.

═══════════════════════════════════════════════════════════════════════════════
  LE TROU QUE CE MODULE FERME
═══════════════════════════════════════════════════════════════════════════════

Mesure sur le corpus reel (`data/task_orchestrator_state.json`, 670 taches) :

    taches contenant une trace de la pensee de l'agent  ....  0 / 670

Le raisonnement n'existait QUE sur le flux SSE et dans l'anneau serveur de 500
evenements. Au redemarrage, ou des que l'anneau tourne, il etait perdu
definitivement. Une mission terminee ne pouvait plus etre rouverte : le
panneau montrait des cartes vides et des compteurs figes.

`checkpoint_history` (present sur 669/670, jusqu'a 40 entrees) garde la phase,
l'iteration et le ledger — la charpente, jamais le raisonnement.

═══════════════════════════════════════════════════════════════════════════════
  LES DECISIONS, ET POURQUOI
═══════════════════════════════════════════════════════════════════════════════

**Un fichier par mission, en ajout pur.** L'etat des taches pese deja 11,9 Mo
et il est reecrit EN ENTIER a chaque persistance : y ajouter les pensees
mettrait le raisonnement sur le chemin chaud. Un `append` par mission est en
O(1) et ne touche a rien d'autre.

**Dans `data/missions/`, PAS dans l'espace de travail de la mission.** Le
workspace est publie au client par `publish_mission_workspace` : y ecrire de la
telemetrie la livrerait avec le produit. Le journal est une trace d'exploitation,
il vit avec les logs.

**JSONL.** C'est deja l'idiome du depot — `data/logs/codeagent/metrics.jsonl`,
`gate_metrics.jsonl`. Une ligne par evenement, lisible a la main, tolerant a la
troncature : une ligne corrompue ne perd pas le fichier.

**Borne par ROTATION, pas par date.** « Une mission close reste rouvrable pour
toujours » est le but ; c'est donc le volume qu'on borne, pas la memoire. Un
fichier plein bascule en `.1` et repart. Pire cas : deux fois la borne par
mission. Mesure : mediane 6 actions par tache, p90 24, max 166 — soit ~3 Ko,
13 Ko et 89 Ko. La borne est un filet, pas un chemin courant.

**Identifiant valide AVANT tout acces disque.** Un `task_id` arrive du reseau
et sert de nom de fichier. La lecon Z40a de ce depot etait exactement une
traversee de chemin ; ici la porte est une liste blanche stricte, pas un
nettoyage.

**Jamais bloquant, jamais fatal.** Le journal est un confort ; la boucle de
l'agent est le produit. Toute erreur d'ecriture est avalee. Un disque plein ne
doit pas tuer une mission.
"""

from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

# ── Reglages ─────────────────────────────────────────────────────────────────

#: Liste blanche STRICTE. Un `task_id` sert de nom de fichier : il ne se
#: nettoie pas, il se refuse. Les identifiants reels sont des `task_<hex32>`.
_ID_VALIDE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")

#: Au-dela, le journal bascule en `.1` et repart. Deux fois cela au pire.
_MAX_OCTETS = 1_048_576

#: Champs conserves. On jette `trace_id`, `turn_id`, `request_id` et
#: `conversation_id` : ils identifient un tour de conversation, pas un moment
#: de mission, et le fichier est deja indexe par la mission.
_CHAMPS = (
    "seq", "ts", "stage", "status", "mode",
    "tool_name", "summary", "thought", "error",
    "iteration", "max_iter", "duration_ms",
)

#: Plafonds. Identiques a ceux du flux live pour que l'archive rejouee soit
#: EXACTEMENT ce que l'ecran a montre — un journal qui en dirait plus que le
#: direct serait une seconde verite.
_PLAFONDS = {"thought": 400, "summary": 300, "error": 300, "stage": 80,
             "tool_name": 80, "status": 40, "mode": 40}

_verrou = threading.Lock()


def actif() -> bool:
    """`LUMENA_MISSION_JOURNAL=0` coupe tout. Actif par defaut : « tout doit
    etre enregistre » n'a pas de sens en option par defaut."""
    return (os.getenv("LUMENA_MISSION_JOURNAL", "1") or "1").strip() not in (
        "0", "false", "no", "off"
    )


def _racine() -> Path:
    """Import PARESSEUX de `paths` : `trace_bus` est importe tres tot dans le
    demarrage, et un cycle d'import y serait fatal."""
    from src.utils.paths import DATA_DIR
    return Path(DATA_DIR) / "missions"


def chemin_journal(task_id: str) -> Optional[Path]:
    """Chemin du journal, ou None si l'identifiant n'est pas recevable.

    Aucun nettoyage : un identifiant qui ne passe pas la liste blanche est
    REFUSE. C'est la difference entre une porte et un tamis.
    """
    tid = str(task_id or "").strip()
    if not _ID_VALIDE.match(tid):
        return None
    return _racine() / f"{tid}.jsonl"


def _reduit(event: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for cle in _CHAMPS:
        val = event.get(cle)
        if val is None or val == "":
            continue
        if isinstance(val, str):
            cap = _PLAFONDS.get(cle)
            if cap and len(val) > cap:
                val = val[:cap]
        out[cle] = val
    return out


def _fait_tourner(chemin: Path) -> None:
    """Le plein bascule en `.1`. On garde donc au plus deux fichiers : le
    debut d'une mission (son plan) survit un tour de plus que son milieu."""
    precedent = chemin.with_suffix(".1.jsonl")
    try:
        if precedent.exists():
            precedent.unlink()
        chemin.rename(precedent)
    except Exception:
        # Rotation impossible (fichier verrouille, droits) : on prefere
        # arreter d'ecrire plutot que gonfler sans borne.
        try:
            chemin.unlink()
        except Exception:
            pass


def grave(event: Dict[str, Any]) -> bool:
    """Ajoute un evenement au journal de sa mission. Rend True si ecrit.

    JAMAIS d'exception vers l'appelant : ce module est accroche au bus de
    traces, qui est lui-meme sur le chemin de chaque iteration d'agent.
    """
    if not actif() or not isinstance(event, dict):
        return False
    chemin = chemin_journal(event.get("task_id"))
    if chemin is None:
        return False
    ligne = _reduit(event)
    if not ligne:
        return False
    try:
        with _verrou:
            chemin.parent.mkdir(parents=True, exist_ok=True)
            if chemin.exists() and chemin.stat().st_size >= _MAX_OCTETS:
                _fait_tourner(chemin)
            with chemin.open("a", encoding="utf-8") as f:
                f.write(json.dumps(ligne, ensure_ascii=False) + "\n")
                # Vidage explicite : « tout doit etre enregistre » ne tolere
                # pas de perdre la fin sur un arret brutal.
                f.flush()
        return True
    except Exception:
        return False


def lis(task_id: str, limit: int = 2000) -> List[Dict[str, Any]]:
    """Relit un journal, du plus ANCIEN au plus recent.

    Concatene le fichier tourne puis le courant, pour que la chronologie soit
    continue. Une ligne illisible est SAUTEE, pas fatale : un journal tronque
    par un arret brutal doit rester exploitable — c'est meme le cas ou on en a
    le plus besoin.
    """
    chemin = chemin_journal(task_id)
    if chemin is None:
        return []
    out: List[Dict[str, Any]] = []
    for cible in (chemin.with_suffix(".1.jsonl"), chemin):
        try:
            if not cible.exists():
                continue
            with cible.open("r", encoding="utf-8") as f:
                for brut in f:
                    brut = brut.strip()
                    if not brut:
                        continue
                    try:
                        obj = json.loads(brut)
                    except Exception:
                        continue          # ligne tronquee : on saute
                    if isinstance(obj, dict):
                        out.append(obj)
        except Exception:
            continue
    if limit and len(out) > limit:
        # On garde la FIN : c'est la conclusion d'une mission qu'on vient
        # chercher en premier.
        out = out[-int(limit):]
    return out


def existe(task_id: str) -> bool:
    chemin = chemin_journal(task_id)
    if chemin is None:
        return False
    try:
        return chemin.exists() or chemin.with_suffix(".1.jsonl").exists()
    except Exception:
        return False


def inventaire() -> Dict[str, Any]:
    """Tout ce qui est archive, en UN seul balayage de repertoire.

    Mesure : 8,7 ms pour 5 000 fichiers avec leurs tailles, contre 17,9 ms
    pour 670 `exists()` un par un. Le panneau annote sa liste de missions a
    partir de ce dictionnaire au lieu d'interroger le disque par mission.

    Les fichiers tournes (`<id>.1.jsonl`) sont recolles a leur mission : un
    journal en deux morceaux reste UN journal.
    """
    out: Dict[str, int] = {}
    total = 0
    try:
        racine = _racine()
        if not racine.is_dir():
            return {"entries": {}, "files": 0, "bytes": 0}
        for entree in os.scandir(racine):
            nom = entree.name
            if not nom.endswith(".jsonl") or not entree.is_file():
                continue
            base = nom[:-len(".jsonl")]
            if base.endswith(".1"):
                base = base[:-2]
            try:
                taille = entree.stat().st_size
            except Exception:
                continue
            out[base] = out.get(base, 0) + taille
            total += taille
    except Exception:
        return {"entries": {}, "files": 0, "bytes": 0}
    return {"entries": out, "files": len(out), "bytes": total}


def purge(ids_vivants: Optional[List[str]] = None) -> Dict[str, Any]:
    """Supprime les journaux ORPHELINS — ceux dont la tache n'existe plus.

    Un journal orphelin ne peut plus etre rouvert depuis le panneau : il n'y a
    plus de mission a deplier. C'est du poids mort, et c'est la SEULE chose
    qu'on supprime.

    On ne purge PAS par date : « une mission close reste rouvrable pour
    toujours » est le but de ce module. Borner par l'age reviendrait a decider
    a la place de l'utilisateur que son passe ne compte plus.

    `ids_vivants = None` ne supprime rien : sans liste de reference, tout
    journal pourrait etre legitime. Ne rien faire est le bon defaut quand on
    ne sait pas.
    """
    if ids_vivants is None:
        return {"removed": 0, "bytes": 0, "kept": inventaire()["files"]}
    vivants = {str(x) for x in ids_vivants}
    enleves, octets = 0, 0
    inv = inventaire()
    try:
        racine = _racine()
        for tid, taille in inv["entries"].items():
            if tid in vivants:
                continue
            for cible in (racine / f"{tid}.jsonl", racine / f"{tid}.1.jsonl"):
                try:
                    if cible.exists():
                        cible.unlink()
                except Exception:
                    continue
            enleves += 1
            octets += taille
    except Exception:
        pass
    return {"removed": enleves, "bytes": octets, "kept": inv["files"] - enleves}


def stats(task_id: str) -> Dict[str, Any]:
    """Poids et presence, sans relire le contenu."""
    chemin = chemin_journal(task_id)
    if chemin is None:
        return {"exists": False, "bytes": 0, "rotated": False}
    tourne = chemin.with_suffix(".1.jsonl")
    try:
        octets = (chemin.stat().st_size if chemin.exists() else 0)
        octets += (tourne.stat().st_size if tourne.exists() else 0)
        return {
            "exists": chemin.exists() or tourne.exists(),
            "bytes": octets,
            "rotated": tourne.exists(),
        }
    except Exception:
        return {"exists": False, "bytes": 0, "rotated": False}
