"""LOT S1 (2026-10-09) — LUMENA SAIT OU ELLE EN EST, MAINTENANT.

═══════════════════════════════════════════════════════════════════════════════
  ELLE CROYAIT AVOIR 16 Go. ELLE EN AVAIT 5,9.
═══════════════════════════════════════════════════════════════════════════════

Mesure du 2026-10-09. Dans son prompt, Lumena lisait :

    Disque C: — 96.6% utilisé (16.3 Go libres)        <- heartbeat du 17 AOUT

Et dans `data/ops/metrics.jsonl`, 15 minutes plus tot :

    {"disk_percent": 98.8, "disk_free_gb": 5.9, "ram_available_mb": 10965}

Elle ne se trompait pas : elle **recitait un souvenir** au lieu de regarder maintenant.
Ce fichier contient **952 mesures de sante** et **784 sondages de fournisseurs**,
rafraichis toutes les quelques minutes — et **aucune n'atteignait le modele**.

Une entite qui recite son passe a la place de son present n'est pas la : elle se
souvient d'avoir ete la.

--- Le patron suivi ---

Le bloc Emotion, deja injecte a chaque tour, en 67 caracteres :

    [Émotion] Humeur=neutral | Énergie=medium | PAD(+0.02,+0.12,+0.15)

Court, frais, structure. Ce module fait le meme pour le systeme.

--- Les trois regles, et pourquoi elles sont dures ---

1. **UN FAIT, JAMAIS UNE CONSIGNE.** Le bloc Emotion dit « Humeur=neutral », il ne dit
   pas « sois joyeuse ». Si ce bloc disait « pense a liberer de la place », Lumena
   deviendrait celle qui rappelle le disque a CHAQUE message — pire que le silence
   d'avant. Un test interdit 8 formulations d'ordre.
2. **JAMAIS BLOQUANT.** Fichier absent, corrompu, cle manquante, type inattendu :
   chaine vide, zero exception. Une donnee d'affichage ne fait pas echouer un tour.
3. **JAMAIS D'INVENTION.** Une mesure de plus d'une heure DIT son age. Mieux vaut un
   chiffre date qu'un chiffre presente comme actuel — c'est la regle de verite du
   produit, appliquee a lui-meme.

Module auto-contenu (stdlib) -> testable sans runtime.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

__all__ = ["bloc_etat_systeme", "LIMITE_CARACTERES"]

# Budget de la phase PRESENCE : 500 car. pour TOUS les blocs reunis. S1 en prend 200.
LIMITE_CARACTERES = 200

# Au-dela, une mesure n'est plus un etat : c'est un souvenir. On la date, ou on se tait.
_AGE_SILENCIEUX_MIN = 60        # en deca : on n'encombre pas le bloc avec l'age
_AGE_ABANDON_JOURS = 7          # au-dela : plus rien a dire d'utile

_CHEMIN_DEFAUT = Path("data/ops/metrics.jsonl")
_MAX_LIGNES = 400               # on ne lit que la fin du fichier : seul l'instant compte

# Mesure du 2026-10-09 : `metrics.jsonl` pesait 4,0 Mo, et il grossit a chaque sonde.
# Ce bloc est construit a CHAQUE tour de prompt : relire 4 Mo pour en garder 400 lignes
# est exactement l'« I/O lente » que la phase PRESENCE s'interdit. On se place donc en
# QUEUE du fichier, ce qui rend le cout independant de sa taille.
_MAX_OCTETS = 262_144           # 256 Kio : tres largement les 400 dernieres lignes


def _nombre(valeur: Any) -> Optional[float]:
    try:
        f = float(valeur)
        return f if f == f else None        # ecarte NaN
    except (TypeError, ValueError):
        return None


def _fr(valeur: float, decimales: int = 1) -> str:
    """Format francais : la virgule, comme partout ailleurs dans l'interface."""
    return f"{valeur:.{decimales}f}".replace(".", ",")


def _age(horodatage: Any) -> Optional[float]:
    """Age en minutes, ou None si l'horodatage est illisible."""
    try:
        return (datetime.now() - datetime.fromisoformat(str(horodatage))).total_seconds() / 60
    except (TypeError, ValueError):
        return None


def _age_lisible(minutes: float) -> str:
    if minutes < 120:
        return f"il y a {int(minutes)} min"
    if minutes < 60 * 48:
        return f"il y a {int(minutes // 60)} h"
    return f"il y a {int(minutes // 1440)} j"


def _dernieres(chemin: Path) -> Dict[str, Dict[str, Any]]:
    """Derniere entree de chaque handler. Lignes illisibles ignorees en silence."""
    resultat: Dict[str, Dict[str, Any]] = {}
    try:
        taille = chemin.stat().st_size
        with chemin.open("rb") as fichier:
            if taille > _MAX_OCTETS:
                fichier.seek(taille - _MAX_OCTETS)
                fichier.readline()   # cette premiere ligne est coupee : on la jette
            brut = fichier.read()
        lignes = brut.decode("utf-8", errors="replace").splitlines()
    except Exception:
        return resultat
    for ligne in lignes[-_MAX_LIGNES:]:
        ligne = ligne.strip()
        if not ligne:
            continue
        try:
            entree = json.loads(ligne)
        except Exception:
            continue                         # ligne corrompue : on passe
        if isinstance(entree, dict) and isinstance(entree.get("handler"), str):
            resultat[entree["handler"]] = entree
    return resultat


def bloc_etat_systeme(chemin: Optional[Path] = None) -> str:
    """Rend la ligne d'etat systeme a injecter, ou "" s'il n'y a rien de sur a dire.

    Appelee a chaque tour, a cote du bloc Emotion. Ne leve jamais.
    """
    try:
        source = Path(chemin) if chemin is not None else _CHEMIN_DEFAUT
        entrees = _dernieres(source)
        sante = entrees.get("runtime_health") or {}
        checks = ((sante.get("data") or {}).get("checks")) or {}
        if not isinstance(checks, dict):
            checks = {}

        minutes = _age(sante.get("timestamp"))
        if minutes is not None and minutes > _AGE_ABANDON_JOURS * 1440:
            return ""                        # trop vieux pour etre un etat

        morceaux: list[str] = []

        disque = _nombre(checks.get("disk_percent"))
        libre = _nombre(checks.get("disk_free_gb"))
        if disque is not None:
            alerte = " ⚠" if disque >= 95 else ""
            place = f" ({_fr(libre)} Go)" if libre is not None else ""
            morceaux.append(f"Disque {_fr(disque)} %{place}{alerte}")

        ram = _nombre(checks.get("ram_available_mb"))
        if ram is not None:
            morceaux.append(f"RAM {_fr(ram / 1024)} Go")

        retard = _nombre(checks.get("scheduler_overdue"))
        if retard is not None and retard > 0:
            morceaux.append(f"{int(retard)} tâche(s) en retard")

        # Les fournisseurs : elle doit savoir sur quoi elle tourne. C'est son plafond
        # le plus dur, et le seul qu'aucun lot de code ne levera.
        fournisseurs = ((entrees.get("provider_probe") or {}).get("data") or {}).get("providers")
        if isinstance(fournisseurs, dict) and fournisseurs:
            vivants = sum(
                1 for v in fournisseurs.values()
                if isinstance(v, dict) and v.get("healthy") and not v.get("in_cooldown")
            )
            morceaux.append(f"{vivants}/{len(fournisseurs)} fournisseurs OK")

        if not morceaux:
            return ""

        if minutes is not None and minutes >= _AGE_SILENCIEUX_MIN:
            morceaux.append(_age_lisible(minutes))

        bloc = "[Système] " + " | ".join(morceaux)
        # Anti-fuite : ce texte part au LLM. Aucun chemin systeme ne doit y passer —
        # les alertes d'auto-nettoyage en contiennent (`C:\...\__pycache__`), raison
        # pour laquelle on ne reprend JAMAIS `alerts` tel quel.
        if "\\" in bloc or "/Users/" in bloc or "/home/" in bloc:
            return ""
        return bloc[:LIMITE_CARACTERES].rstrip(" |")
    except Exception:
        return ""                            # regle 2 : jamais bloquant
