"""Lot 5.7 — Budget temporel des missions (logique PURE, testable, `now` injectable).

Principe (cf. feedback utilisateur) : le temps est un BUDGET CALME, jamais un fouet.
On donne à la mission de quoi prioriser sans la stresser ; on ne lui demande JAMAIS
de bâcler. La mission livre le travail complet demandé ou déclare explicitement son
échec après de vraies stratégies alternatives ; un partiel n'est jamais un succès.

Aucun effet de bord, aucun DOM, aucune dépendance lourde → 100% testable.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

# Extensions de LIVRABLE reconnues (doc/artefact) — restreint pour ne PAS matcher
# du code ou des fragments d'URL. Sert au contrat « artefact disque » (Lot 5.7.4a).
_TARGET_EXT = "md|markdown|txt|csv|json|html|htm|pdf|docx|xlsx|pptx|ics|yaml|yml"
_TARGET_FILE_RE = re.compile(
    r"((?:[\w\-]+/)*[\w\-]+\.(?:" + _TARGET_EXT + r"))\b", re.IGNORECASE
)


def extract_target_file(text: Any) -> Optional[str]:
    """Détecte un fichier livrable NOMMÉ dans un objectif (ex. `workspace/x.md`).

    Renvoie le chemin tel quel, ou None si l'objectif ne nomme aucun fichier
    (→ missions « texte » : pas de contrat d'artefact disque, comportement inchangé).
    Pur/déterministe : verrouillé par tests.
    """
    if not text:
        return None
    m = _TARGET_FILE_RE.search(str(text))
    return m.group(1) if m else None


def extract_unambiguous_target_file(text: Any) -> Optional[str]:
    """Return a target only when exactly one distinct deliverable is named."""
    if not text:
        return None
    matches = list(dict.fromkeys(
        match.group(1) for match in _TARGET_FILE_RE.finditer(str(text))
    ))
    return matches[0] if len(matches) == 1 else None


def deadline_final_exit_allowed(
    *, partial_due_to_deadline: bool, target_file: Optional[str], artifact_written: bool,
) -> bool:
    """Indique si l'échéance peut relâcher le FINAL d'une mission incomplète.

    La politique complete-only renvoie volontairement toujours False : ni un
    sous-ensemble écrit ni un brouillon texte ne prouvent toutes les exigences.
    """
    # Complete-only policy: a partial artifact never relaxes the PLAN guard.
    return False


def deadline_hard_net_fires(
    *, steered: bool, remaining_s: Any, grace_s: float, artifact_written: bool,
    completion_proven: bool = False,
) -> bool:
    """Indique si le filet dur coopératif doit arrêter la mission.

    Un artefact partiel ne désarme pas ce filet. Une mission complète sort
    normalement avant la fin de la grâce ; sinon l'état terminal est un échec
    explicite ou une annulation, jamais un succès partiel.
    """
    # A complete, authoritative proof set disarms the hard net. A partial
    # artifact remains insufficient and keeps the historical behavior below.
    if completion_proven:
        return False
    if not steered:
        return False
    if not isinstance(remaining_s, (int, float)):
        return False
    if remaining_s > -max(0.0, float(grace_s)):
        return False
    # A partial artifact does not disarm the hard deadline. A complete mission
    # exits before this point; otherwise the terminal state is an explicit
    # failure/cancellation, never a partial success.
    return True

def pytest_gate_extra_shot_allowed(
    *, shots: int, failed_now: Any, failed_prev: Any,
    remaining_s: Any, ratio_used: Any,
) -> bool:
    """2.13.D (run bibliapi 2026-07-09) — un tir PYTEST GATE supplémentaire
    (au-delà des 2 fixes) est-il justifié ? Pur/déterministe.

    bibliapi a conclu à 4 failed avec ~24 min de budget restant : le gate à
    2 tirs fixes gâchait un budget confortable. Tir supplémentaire SEULEMENT si :
      - il reste du rouge (`failed_now > 0`) ;
      - le budget est CONFORTABLE (`ratio_used < 0.6` OU `remaining_s > 300`) —
        mission sans échéance (None/None) = pas de pression → autorisé ;
      - le dernier tir a PROGRESSÉ (`failed_now < failed_prev` strictement ;
        prev inconnu = 1er tir rouge → autorisé une fois) ;
      - plafond DUR : `shots < 4`.
    Stagnation, budget court ou plafond → False : final honnête actuel (les
    bannières truth-lock disent la vérité), jamais de boucle infinie.
    Doctrine 5.7 : budget calme — on OFFRE du temps disponible, zéro stress.
    """
    if shots >= 4:
        return False
    if not isinstance(failed_now, (int, float)) or failed_now <= 0:
        return False
    # Progrès : prev connu → strictement décroissant ; prev inconnu → 1re chance.
    if isinstance(failed_prev, (int, float)) and failed_now >= failed_prev:
        return False
    # Budget confortable : l'un OU l'autre suffit ; mission sans deadline = OK.
    _ratio_ok = (not isinstance(ratio_used, (int, float))) or ratio_used < 0.6
    _remaining_ok = (not isinstance(remaining_s, (int, float))) or remaining_s > 300
    return _ratio_ok or _remaining_ok


def no_progress_rescue_allowed(
    *, is_mission: bool, tests_present: bool, gate_shots: int,
    remaining_s: Any, ratio_used: Any, already_rescued: bool,
) -> bool:
    """PG-1.b (run SkiLoc 2026-07-12) — le FINAL forcé « aucune progression »
    doit-il être remplacé par UN sauvetage dirigé ? Pur/déterministe.

    SkiLoc : le plan guard a coupé la mission avec 2 048 s de budget (ratio
    0,15), 5 s après un tir PYTEST GATE accordé, alors que le lead venait de
    corriger (mutations réelles) — il ne restait qu'à relancer pytest et
    publier. Sauvetage SEULEMENT si :
      - mission (le garde anti-boucle chat/navigateur reste inchangé) ;
      - jamais sauvé (1 seul tir — pas de boucle infinie) ;
      - des tests existent (il y a quelque chose de concret à relancer) ;
      - shots gate < 4 (même plafond dur que 2.13.D) ;
      - budget CONFORTABLE (mêmes seuils que pytest_gate_extra_shot_allowed ;
        mission sans échéance = pas de pression → autorisé).
    """
    if not is_mission or already_rescued:
        return False
    if not tests_present:
        return False
    if gate_shots >= 4:
        return False
    _ratio_ok = (not isinstance(ratio_used, (int, float))) or ratio_used < 0.6
    _remaining_ok = (not isinstance(remaining_s, (int, float))) or remaining_s > 300
    return _ratio_ok or _remaining_ok


# Expressions vagues FR → heure du jour (calmes, non couvertes par les parsers existants).
_KEYWORD_TIMES = (
    ("fin de la journée", (18, 0)),
    ("fin de journée", (18, 0)),
    ("ce soir", (20, 0)),
    ("cette nuit", (23, 0)),
    ("dans la nuit", (23, 0)),
    ("cet après-midi", (15, 0)),
    ("cet apres-midi", (15, 0)),
    ("ce matin", (10, 0)),
    ("à midi", (12, 0)),
    ("ce midi", (12, 0)),
)

# Mots interdits dans le cadrage temporel (anti-stress). Verrouillés par test.
_STRESS_WORDS = ("vite", "dépêch", "depech", "plus que", "urgent", "presse", "accélèr",
                 "acceler", "grouille", "au plus vite")


def _import_parsers():
    try:
        from src.tools.task_scheduler import _parse_delay, _parse_run_at
        return _parse_delay, _parse_run_at
    except Exception:
        return None, None


def _iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat()


def _parse_iso(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
    except (ValueError, TypeError):
        return None
    # Robustesse TZ : created_at vient de l'orchestrateur en AWARE UTC
    # (datetime.now(timezone.utc)), alors que `now`/deadline_ts sont NAÏFS locaux
    # (datetime.now()). Mélanger les deux dans une soustraction lève TypeError
    # (« can't subtract offset-naive and offset-aware ») → avalé en amont, tout le
    # budget mission mourait silencieusement (5.7.3/5.7.4 jamais déclenchés). On
    # ramène tout AWARE → naïf LOCAL (heure murale) pour rester comparable au `now`
    # naïf. Naïf inchangé → tests déterministes préservés.
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt


def normalize_deadline(text: Any, *, now: Optional[datetime] = None) -> Optional[str]:
    """Normalise une échéance en langage libre → timestamp ISO, ou None si non reconnue.

    Couvre : relatif explicite (« dans 2h »), mots calmes (« ce soir », « midi »…),
    absolu (« 18:00 », « demain à 9h », « lundi 8h », ISO), durée nue (« 30min »).
    `now` injectable → déterministe. None → on garde le texte brut, aucune échéance imposée.
    """
    if text is None or not str(text).strip():
        return None
    now = now or datetime.now()

    # 0) ISO-8601 STRICT (échéance posée par la MACHINE, ex. create_mission reçoit
    #    '2026-07-05T12:00:00'). Lot H (run BiblioFlux) : le lead a été TUÉ à 600 s en
    #    pleine délégation car cet ISO renvoyait None ici → deadline_ts absent → l'uplift
    #    budget (runner B0.1) ne tirait jamais. `_parse_run_at` n'essaie que des formats à
    #    ESPACE (strptime) ; il ratait le séparateur 'T'. fromisoformat gère 'T', l'espace,
    #    les secondes, la tz ('Z', ±HH:MM) — sur le texte BRUT (casse préservée). Strict :
    #    « demain 12h », « 18:00 », « dans 2h » lèvent ValueError → tombent dans la logique
    #    naturelle ci-dessous (zéro régression). Ne capte QUE l'ISO réel.
    raw = str(text).strip()
    try:
        dt_iso = datetime.fromisoformat(raw)
        if dt_iso.tzinfo is not None:
            dt_iso = dt_iso.astimezone().replace(tzinfo=None)  # aware→naïf local (cf. _parse_iso)
        return _iso(dt_iso)
    except (ValueError, TypeError):
        pass

    s = str(text).strip().lower()
    parse_delay, parse_run_at = _import_parsers()

    # 1) Explicitement RELATIF (« dans 2h », « dans 30 minutes »).
    if ("dans " in s or s.startswith("dans")) and parse_delay is not None:
        d = parse_delay(s)
        if d is not None and d.total_seconds() > 0:
            return _iso(now + d)

    # 2) Mots vagues calmes → heure du jour (ou demain si déjà passée).
    for kw, (h, m) in _KEYWORD_TIMES:
        if kw in s:
            target = now.replace(hour=h, minute=m, second=0, microsecond=0)
            if target <= now:
                target += timedelta(days=1)
            return _iso(target)

    # 3) ABSOLU (« 18:00 », « demain à 9h », « lundi 8h », « 2026-06-30 18:00 »).
    if parse_run_at is not None:
        dt = parse_run_at(s, now=now)
        if dt is not None:
            return _iso(dt)

    # 4) Durée NUE sans « dans » (« 30min », « 2 jours »).
    if parse_delay is not None:
        d = parse_delay(s)
        if d is not None and d.total_seconds() > 0:
            return _iso(now + d)

    return None


def seconds_until_deadline(
    deadline_ts: Any, *, now: Optional[datetime] = None
) -> Optional[float]:
    """LOT H1 — secondes restantes avant `deadline_ts`, ou `None` si illisible.

    `deadline_ts` est une chaîne **ISO-8601** (posée par `manager.create_mission`
    via `normalize_deadline`), JAMAIS un nombre. Deux appelants la traitaient
    pourtant comme un epoch :

        float(deadline_ts) - time.time()      # ValueError, avalée par un except

    Conséquences observées au run SuiviDepenses (2026-08-12) :
    - `delegate_and_wait` (2.6.4) ne relevait jamais son attente jusqu'à
      l'échéance : le lead expirait à 600 s alors qu'il avait ~29 min de budget,
      puis reprenait le travail de ses workers encore actifs → course sur les
      mêmes fichiers ;
    - le garde de publication (`publish_mission_workspace`) tombait dans son
      `except` et refusait TOUJOURS tant qu'un worker tournait — plus strict que
      voulu, donc sans dommage, mais faux pour la même raison.

    Réutilise `_parse_iso` (même normalisation de fuseau que tout le budget
    mission) : mélanger un `datetime` aware et un naïf lève `TypeError` et tuait
    déjà silencieusement 5.7.3/5.7.4 par le passé.

    Négatif = échéance dépassée. `now` injectable → test déterministe.
    """
    end = _parse_iso(deadline_ts)
    if end is None:
        return None
    return (end - (now or datetime.now())).total_seconds()


def mission_budget(record: Any, *, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Budget temporel d'une mission depuis `created_at` + `metadata.deadline_ts`.

    Accepte un dict (`to_dict()`) ou un objet (attributs). Renvoie toujours un dict :
    {has_deadline, deadline_ts, elapsed_s, remaining_s, ratio_used}.
    remaining_s peut être négatif (échéance dépassée). ratio_used ∈ [0,1].
    """
    now = now or datetime.now()
    if isinstance(record, dict):
        md = record.get("metadata") or {}
        created = record.get("created_at")
    else:
        md = getattr(record, "metadata", None) or {}
        created = getattr(record, "created_at", None)

    out: Dict[str, Any] = {
        "has_deadline": False, "deadline_ts": None,
        "elapsed_s": None, "remaining_s": None, "ratio_used": None,
    }
    start = _parse_iso(created)
    if start is not None:
        out["elapsed_s"] = max(0.0, (now - start).total_seconds())

    dts = md.get("deadline_ts")
    end = _parse_iso(dts)
    if end is not None:
        out["has_deadline"] = True
        out["deadline_ts"] = str(dts)
        out["remaining_s"] = (end - now).total_seconds()
        if start is not None and end > start:
            span = (end - start).total_seconds()
            out["ratio_used"] = min(1.0, max(0.0, (now - start).total_seconds() / span))
    return out


# Nudges d'auto-gestion (CALMES, qualité d'abord). Émis UNE fois par palier franchi.
_NUDGE_HALF = (
    "ℹ️ Point de repère : tu as utilisé environ la moitié de ton temps — tout va bien. "
    "Réévalue le chemin critique et délègue en parallèle ce qui peut l'être afin de "
    "compléter toutes les exigences avec le soin voulu. Continue à ton rythme, qualité d'abord."
)
_NUDGE_LOW = (
    "ℹ️ Le temps se réduit : concentre-toi sur les exigences encore non prouvées, pivote "
    "hors des impasses et délègue en parallèle si c'est pertinent. La qualité et la "
    "complétude restent obligatoires ; un blocage externe persistant doit finir en échec explicite."
)


def mission_budget_nudge(budget: Dict[str, Any], *, already=()):
    """Renvoie `(key, texte)` du nudge d'auto-gestion à émettre, ou None.

    Paliers (sur `ratio_used`) : « half » (~50 %) puis « low » (~80 %). Émis UNE fois
    chacun (`already` = clés déjà émises, persistées dans metadata.budget_nudges).
    CALME et orienté qualité — jamais d'injonction de rapidité (verrouillé par test).
    """
    if not budget or not budget.get("has_deadline"):
        return None
    ratio = budget.get("ratio_used")
    if ratio is None:
        return None
    already = set(already or ())
    if ratio >= 0.8 and "low" not in already:
        return ("low", _NUDGE_LOW)
    if ratio >= 0.5 and "half" not in already and "low" not in already:
        return ("half", _NUDGE_HALF)
    return None


# Steer de finalisation à l'échéance (CALME, qualité d'abord — JAMAIS un couperet brutal).
_FINALIZE_TEXT = (
    "⏱ L'échéance de cette mission est atteinte. Reviens immédiatement aux exigences "
    "encore non prouvées et choisis la voie la plus directe pour produire le livrable "
    "COMPLET avec la qualité demandée. Ne remplace jamais une action obligatoire par "
    "une affirmation dans FINAL. Si une dépendance externe rend objectivement la "
    "livraison complète impossible après un vrai changement de stratégie, conclus par "
    "un échec explicite et factuel ; ne présente jamais une livraison incomplète comme un succès."
)


def mission_budget_finalize(budget: Dict[str, Any], *, grace_s: float = 120.0):
    """Décision de fin de temps, en 2 étages (anti-couperet) :

    - `("finalize", texte)`  : échéance atteinte (remaining ≤ 0) → on POUSSE une
      dernière stratégie directe vers le livrable complet, on ne coupe PAS.
    - `("cancel", None)`     : échéance + grâce dépassées (remaining ≤ -grace_s) →
      filet dur (la mission a ignoré la finalisation pendant toute la grâce).
    - `None`                 : avant l'échéance, ou pas d'échéance.

    `grace_s` = fenêtre laissée pour finaliser dignement avant le filet.
    """
    if not budget or not budget.get("has_deadline"):
        return None
    remaining = budget.get("remaining_s")
    if remaining is None:
        return None
    grace = max(0.0, float(grace_s))
    if remaining <= -grace:
        return ("cancel", None)
    if remaining <= 0:
        return ("finalize", _FINALIZE_TEXT)
    return None


def _human_label(end: datetime, now: datetime) -> str:
    """Libellé lisible et CALME de l'échéance (sans compte à rebours anxiogène)."""
    hm = end.strftime("%H:%M")
    if end.date() == now.date():
        return f"aujourd'hui {hm}"
    if end.date() == (now + timedelta(days=1)).date():
        return f"demain {hm}"
    return f"{end.strftime('%d/%m')} à {hm}"


def mission_budget_preamble(deadline_ts: Any, *, now: Optional[datetime] = None) -> str:
    """Cadrage temporel CALME injecté dans le prompt de mission, ou "" si pas d'échéance.

    Anti-stress (verrouillé par test) : aucune injonction de rapidité. Le temps aide à
    prioriser, déléguer et pivoter afin de livrer toutes les exigences avec qualité.
    """
    if not deadline_ts:
        return ""
    end = _parse_iso(deadline_ts)
    if end is None:
        return ""
    now = now or datetime.now()
    label = _human_label(end, now)
    return (
        f"⏱ Tu as jusqu'à {label} pour cette mission. Travaille à ton rythme, sans bâcler — "
        "le temps sert à bien prioriser, pas à négliger la qualité. Si le temps se réduit, "
        "délègue en parallèle et pivote hors des impasses pour compléter toutes les exigences. "
        "Si une dépendance externe reste objectivement impossible après ces pivots, déclare "
        "un échec explicite et factuel.\n\n"
    )
