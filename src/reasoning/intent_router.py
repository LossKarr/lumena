"""
🧭 LUMENA — Intent Router (v2)

Source **unique** de vérité pour la classification et le routage des requêtes
utilisateur.  Remplace la logique éparpillée :

    - react.py::_maybe_auto_route_codeagent (guards regex + FAST-ROUTE)
    - react.py::_classify_intent_llm        (appelé en dernier, souvent bypassé)
    - project_registry.py::_detect_intent   (matching par mot-clé)

─── Principes de conception ──────────────────────────────────────────────────

1. **LLM d'abord, regex en secours.**
   Le langage humain (négation, passé composé, ironie, français familier)
   mérite un LLM, pas une cascade de regex.  Les regex ne servent qu'en
   fallback quand le LLM est indisponible (offline, rate-limit).

2. **Cascade de providers fiable.**
   Utilise la chaîne `MultiProviderLLM` existante (pas de duplication réseau).
   Ordre : modèle par défaut → Gemini Flash → DeepSeek → Ollama → regex.

3. **Cache + TTL.**
   Même question dans les 120s = même décision.  LRU borné.

4. **Décision explicable et loguée.**
   Chaque décision porte un `reason` + `source`, et est persistée dans
   `data/logs/routing.jsonl` pour audit et fine-tuning futur.

5. **Zéro dépendance nouvelle.**
   Tout passe par les providers déjà configurés.  Le fallback regex est
   minimal (~40 lignes) et ne couvre que les cas évidents.

─── Intents ──────────────────────────────────────────────────────────────────

    CODE_WRITE  — Créer/modifier/corriger du code ou un projet (→ CodeAgent full)
    CODE_READ   — Analyser/inspecter/donner avis sur un projet, SANS modifier
                  (→ CodeAgent mode lecture, Architect + Reasoner skipped)
    BROWSE      — Visiter/analyser un site web, scraper (→ ReAct + browser)
    TOOL        — Invocation d'outil spécifique (mail, scheduler, config)
    RESEARCH    — Apprentissage autonome, veille marché (→ ReAct + web)
    CHAT        — Conversation, opinion, explication pure (→ ReAct minimal)

Le routeur retourne un `RouteDecision` consommé par `react.py`.  La
translation vers `CodeAgent`/`ReAct` reste de la responsabilité de react.py.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

from loguru import logger

from ..utils.paths import DATA_DIR

# ──────────────────────────────────────────────────────────────────────────
# Dataclass de sortie
# ──────────────────────────────────────────────────────────────────────────


INTENTS = ("CODE_WRITE", "CODE_READ", "BROWSE", "TOOL", "RESEARCH", "CHAT")


@dataclass
class RouteDecision:
    """Résultat d'une classification.  Consommé par react.py."""

    intent: str                       # ∈ INTENTS
    confidence: float                 # 0.0 – 1.0
    reason: str = ""                  # courte justification (log/audit)
    source: str = "llm"               # llm | regex | cache | override
    readonly: bool = False            # True si intent=CODE_READ
    latency_ms: int = 0
    raw_response: str = ""            # brut du LLM (debug)

    def to_log_dict(self, query: str) -> dict[str, Any]:
        d = asdict(self)
        d["query"] = query[:500]
        d["ts"] = time.time()
        return d


# ──────────────────────────────────────────────────────────────────────────
# Cache TTL borné (pas de dépendance externe)
# ──────────────────────────────────────────────────────────────────────────

_CACHE_TTL_S = 120.0
_CACHE_MAX = 32
_cache: dict[int, tuple[float, RouteDecision]] = {}


def _cache_get(key: int) -> Optional[RouteDecision]:
    entry = _cache.get(key)
    if entry is None:
        return None
    ts, decision = entry
    if (time.time() - ts) > _CACHE_TTL_S:
        _cache.pop(key, None)
        return None
    return decision


def _cache_put(key: int, decision: RouteDecision) -> None:
    if len(_cache) >= _CACHE_MAX:
        # Drop le plus ancien
        oldest = min(_cache.items(), key=lambda kv: kv[1][0])[0]
        _cache.pop(oldest, None)
    _cache[key] = (time.time(), decision)


def clear_cache() -> None:
    """Vider le cache (tests + admin)."""
    _cache.clear()


# ──────────────────────────────────────────────────────────────────────────
# Prompt LLM
# ──────────────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """Tu es un classifieur d'intentions pour Lumena. Tu reçois un message utilisateur en français (parfois avec fautes/négligé) et tu retournes UNIQUEMENT un JSON strict sur une seule ligne :

{"intent":"<INTENT>","confidence":<0.0-1.0>,"reason":"<10 mots max>"}

INTENTS possibles (choisis UN seul) :

• CODE_WRITE  → Écrire/créer/modifier/corriger du code, un fichier, un projet, un site.
                Mots-clés : "crée", "fais", "ajoute", "modifie", "corrige", "change", "refactor", "supprime".
                L'utilisateur ORDONNE une action MAINTENANT.

• CODE_READ   → Analyser/lire/inspecter/donner un avis sur du code existant, SANS le modifier.
                Mots-clés : "analyse", "regarde", "dis-moi ce que tu penses", "donne ton avis", "est-ce bien",
                "tu es d'accord", "explique", "audite", "vérifie si".
                IMPORTANT : si l'utilisateur dit explicitement "ne modifie pas", "ne touche à rien",
                "juste analyse", "sans modifier" → TOUJOURS CODE_READ, même si le mot "modifie" apparaît.
                Le passé composé ("j'ai modifié", "a été changé") décrit un état, pas un ordre.

• BROWSE      → Visiter un site web, analyser une page en ligne, scraper une URL externe.
                Mots-clés : "va sur", "visite", "ouvre le site", URL présente.

• TOOL        → Action d'outil ciblé : envoyer un mail, planifier, configurer un service (Stripe, n8n,
                WhatsApp, Telegram), gérer credentials, déployer.
                Aussi : opérations fichiers simples (zipper, compresser, extraire une archive, renommer,
                déplacer, copier un fichier/dossier), conversions, ouverture de fichiers.

• RESEARCH    → Apprentissage autonome, veille, recherche web généraliste, actualités, marchés financiers.
                Mots-clés : "apprends", "étudie", "recherche", "actualités", "bourse", "crypto",
                "fais une recherche", "cherche sur", "trouve des infos".

• CHAT        → Conversation pure : opinion, avis non-code, explication générale, discussion, humour,
                questions sur Lumena elle-même.

Règles de décision :
1. Si ambigu entre CODE_WRITE et CODE_READ → regarde la DERNIÈRE phrase, elle prime (c'est l'instruction).
2. Négation proche d'un verbe de modif ("ne modifie rien") = CODE_READ, peu importe le reste.
3. Si la requête mentionne un projet/site/fichier mais demande UNE OPINION ou UNE ANALYSE = CODE_READ.
4. Confidence : 0.9+ si clair, 0.7 si ambigu mais tranché, 0.5 si vraiment flou.

Exemples :
"crée-moi un site vitrine pour ma boulangerie" → {"intent":"CODE_WRITE","confidence":0.95,"reason":"ordre de création"}
"analyse juste mon site, ne modifie rien, dis si c'est bien" → {"intent":"CODE_READ","confidence":0.98,"reason":"analyse explicite sans modif"}
"j'ai modifié le header, tu trouves ça comment ?" → {"intent":"CODE_READ","confidence":0.95,"reason":"demande d'avis sur modif passée"}
"corrige le bug du footer dans blog.html" → {"intent":"CODE_WRITE","confidence":0.95,"reason":"correction demandée"}
"qu'est-ce que tu penses de mon site ?" → {"intent":"CODE_READ","confidence":0.9,"reason":"demande d'opinion"}
"envoie un mail à papa" → {"intent":"TOOL","confidence":0.95,"reason":"envoi mail"}
"zippe le dossier" → {"intent":"TOOL","confidence":0.95,"reason":"opération fichier zip"}
"compresse cette archive" → {"intent":"TOOL","confidence":0.95,"reason":"compression fichier"}
"tu peux le zipper ?" → {"intent":"TOOL","confidence":0.95,"reason":"opération fichier zip"}
"renomme ce fichier" → {"intent":"TOOL","confidence":0.93,"reason":"opération fichier simple"}
"va sur lemonde.fr et résume la une" → {"intent":"BROWSE","confidence":0.95,"reason":"analyse web externe"}
"apprends-moi le trading" → {"intent":"RESEARCH","confidence":0.9,"reason":"apprentissage"}
"fais une recherche sur les hippopotames" → {"intent":"RESEARCH","confidence":0.95,"reason":"recherche web explicite"}
"tu vas bien ?" → {"intent":"CHAT","confidence":0.98,"reason":"conversation"}

Réponds UNIQUEMENT avec le JSON, rien d'autre, pas de markdown, pas de commentaire."""


_JSON_RE = re.compile(r"\{[^{}]*\"intent\"[^{}]*\}", re.DOTALL)


def _parse_llm_response(raw: str) -> Optional[RouteDecision]:
    """Parse une réponse LLM en RouteDecision.  None si invalide."""
    if not raw:
        return None
    # Enlever fences markdown éventuels
    txt = raw.strip()
    if txt.startswith("```"):
        txt = re.sub(r"^```(?:json)?\s*", "", txt)
        txt = re.sub(r"\s*```$", "", txt)
    # Chercher le premier objet JSON valide
    m = _JSON_RE.search(txt)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    intent = str(obj.get("intent", "")).strip().upper()
    if intent not in INTENTS:
        return None
    try:
        conf = float(obj.get("confidence", 0.7))
    except (TypeError, ValueError):
        conf = 0.7
    conf = max(0.0, min(1.0, conf))
    reason = str(obj.get("reason", ""))[:120]
    return RouteDecision(
        intent=intent,
        confidence=conf,
        reason=reason,
        source="llm",
        readonly=(intent == "CODE_READ"),
        raw_response=raw[:500],
    )


# ──────────────────────────────────────────────────────────────────────────
# Fallback regex (minimal, offline-only)
# ──────────────────────────────────────────────────────────────────────────

_RE_READONLY_HARD = re.compile(
    r"\b(ne\s+modifi(?:e|ez)\s+(?:rien|pas)|sans\s+modifi(?:er|cation)|"
    r"juste\s+analys|uniquement\s+analys|ne\s+touche\s+(?:[aà]|pas)|"
    r"analyse\s+seul|regarde\s+juste|dis[\s\-]moi\s+(?:ce\s+que|si\s+tu|ton\s+avis))\b",
    re.IGNORECASE,
)
_RE_READ_VERBS = re.compile(
    r"\b(analyse[rz]?|regarde[rz]?|consulte[rz]?|v[eé]rifie[rz]?|audite[rz]?|"
    r"inspect\w*|explique[rz]?|r[eé]sume[rz]?|pense[rsz]?|pens[eé][es]?|"
    r"qu[e']?\s*(?:en\s+)?pense[rsz]?|"
    r"avis|opinion|d'accord|daccord|trouve[rz]?\s+[çc]a)\b",
    re.IGNORECASE,
)
# ─── Verbes d'écriture ───────────────────────────────────────────────
# Liste "brute" : n'importe quel verbe de mutation (match large).
# Utile UNIQUEMENT pour les cas évidents (une seule phrase, verbe en début).
_RE_WRITE_VERBS = re.compile(
    r"\b(cr[eé][eé]?[sz]?|fais|fait|ajoute[rz]?|modifi[eit][eé]?[rz]?|modite[rz]?|change[rz]?|"
    r"remplace[rz]?|supprime[rz]?|retire[rz]?|corrige[rz]?|r[eé]pare[rz]?|"
    r"fixe[rz]?|refactor\w*|d[eé]veloppe[rz]?|code[rz]?|[eé]cri[rst]?|re[-\s]?modifi\w*|re[-\s]?modite\w*|"
    r"finis|finir|termine[rz]?|terminer|ach[eè]ve[rz]?|achever|compl[eè]te[rz]?|compl[eé]ter|"
    r"pondr[eé]?[rsz]?|ponds?|balanc[eé]?[rsz]?|balance[rz]?|fabriqu[eé]?[rsz]?|tap[eé]?[rsz]?)\b",
    re.IGNORECASE,
)

# Version "impérative" : verbe en POSITION D'ORDRE, pas dans une proposition
# descriptive (« quand je fais X… » = description, pas un ordre).
# Conditions acceptées :
#   - début de phrase (^)
#   - après une ponctuation forte (. ! ?) suivie d'un espace
#   - après une virgule (« …bug, corrige le script »)
#   - après un marqueur de politesse (stp, lumena, tu peux, pourrais-tu, merci de…)
_POLITENESS = (
    r"stp|svp|lumena|s'il\s+te\s+pla[iî]t|s'il\s+vous\s+pla[iî]t|"
    r"merci\s+de|peux[-\s]tu|pourrais[-\s]tu|pourriez[-\s]vous|"
    r"tu\s+peux|tu\s+pourrais|il\s+faut|il\s+faudrai[st]|faut\s+que|"
    r"on\s+peut[-\s]tu|vas[-\s]y"
)
_WRITE_VERB_CORE = (
    r"cr[eé][eé]?[sz]?|fais|ajoute[rz]?|modifi[eit][eé]?[rz]?|modite[rz]?|"
    r"change[rz]?|remplace[rz]?|supprime[rz]?|retire[rz]?|"
    r"corrige[rz]?|r[eé]pare[rz]?|fixe[rz]?|refactor\w*|"
    r"d[eé]veloppe[rz]?|code[rz]?|[eé]cri[rst]?|re[-\s]?modifi\w*|re[-\s]?modite\w*|"
    r"finis|finir|termine[rz]?|terminer|ach[eè]ve[rz]?|achever|"
    r"compl[eè]te[rz]?|compl[eé]ter|impl[eé]mente[rz]?|impl[eé]menter|g[eé]n[eé]r[eé]?[rz]?|"
    r"build|installe[rz]?|installer|d[eé]ploie[rz]?|d[eé]ployer|"
    # Verbes français familiers fréquents en conversation naturelle
    r"pondr[eé]?[rsz]?|ponds?|"           # "pondre un script", "ponds-moi ça"
    r"balanc[eé]?[rsz]?|balance[rz]?|"    # "balance-moi un fichier"
    r"fabriqu[eé]?[rsz]?|fabrique[rz]?|"  # "fabrique-moi une API"
    r"tap[eé]?[rsz]?"                      # "tape un script", "taper du code"
)
_RE_WRITE_IMPERATIVE = re.compile(
    # Déclencheur : début de phrase, ponctuation forte, virgule, ou marqueur de politesse
    # Le groupe optionnel (?:(?:que?\s+)?(?:tu|vous|on)\s+) absorbe "que tu", "tu", "vous"
    # après le marqueur — couvre "il faut que tu X", "faut que tu X", "tu peux X", etc.
    r"(?:^|[.!?]\s+|,\s+|\b(?:" + _POLITENESS + r")[\s,-]+(?:(?:que?\s+)?(?:tu|vous|on)\s+)?)"
    r"(?:me\s+|moi\s+|nous\s+|vas[-\s]tu\s+)?"
    # \w{0,3} absorbe les suffixes de conjugaison : "corriges"→s, "finisses"→ses, "modifies"→s
    r"(?:" + _WRITE_VERB_CORE + r")\w{0,3}\b",
    re.IGNORECASE,
)

# ─── Marqueurs de feedback / observation ─────────────────────────────
# L'utilisateur DÉCRIT un état ou un problème, il ne donne pas d'ordre.
# Ex : « quand je joue le jeu marche pas », « j'ai un bug », « ça plante »
_RE_FEEDBACK_MARKERS = re.compile(
    r"(?:"
    r"(?:^|\b)quand\s+je\b|"
    r"(?:^|\b)quand\s+tu\b|"
    r"(?:^|\b)lorsque\s+je\b|"
    r"(?:^|\b)[çc]a\s+(?:marche|fonctionne|fait|va|plante|bug(?:ue)?)\s+(?:pas|plus|mal)?|"
    r"(?:^|\b)[çc]a\s+plante\b|"
    r"(?:^|\b)ne\s+(?:marche|fonctionne|va)\s+pas|"
    r"(?:^|\b)j['e]\s*ai\s+un\s+(?:bug|probl[eè]me|soucis?|erreur)|"
    r"(?:^|\b)il\s+y\s+a\s+un\s+(?:bug|probl[eè]me|soucis?|erreur)|"
    r"(?:^|\b)y\s+a\s+un\s+(?:bug|probl[eè]me|soucis?|erreur)|"
    r"(?:^|\b)le\s+\w+\s+ne\s+(?:marche|fonctionne|s['e]affiche|appara[iî]t)|"
    r"(?:^|\b)(?:reste|s['e]\s*affiche|appara[iî]t|s['e]\s*ouvre)\s+(?:pas|plus)\b|"
    r"(?:^|\b)bloqu[eé]\b|"
    r"(?:^|\b)plante\b"
    r")",
    re.IGNORECASE,
)
_RE_BROWSE = re.compile(
    r"\b(https?://|www\.|va\s+sur|visite[rz]?|ouvre\s+le\s+site|scrape[rz]?)\b",
    re.IGNORECASE,
)
_RE_TOOL = re.compile(
    r"\b(envoie[rz]?\s+(?:un\s+)?(?:mail|email|message)|planifie[rz]?|"
    r"stripe|n8n|whatsapp|telegram\s+(?:envoi|send)|webhook|deploy[er]*|"
    r"sftp|ftp|ionos|configure[rz]?)\b",
    re.IGNORECASE,
)
_RE_RESEARCH = re.compile(
    r"\b(apprends?|apprendre|[eé]tudie[rz]?|actualit[eé]s?|tendances?|"
    r"march[eé]s?\s+financ|crypto|forex|bourse|trading|veille|"
    r"fai[rst]\s+une?\s+recherche|faire\s+une?\s+recherche|"
    r"effectue[rz]?\s+une?\s+recherche|cherche\s+sur|recherche\s+sur|"
    r"trouve\s+des\s+infos?)\b",
    re.IGNORECASE,
)


def _regex_fallback(query: str) -> RouteDecision:
    """Classification de secours par heuristiques.  Utilisé SEULEMENT si LLM KO."""
    q = query or ""
    # 1. Ordre explicite de lecture seule → CODE_READ (prime sur tout)
    if _RE_READONLY_HARD.search(q):
        return RouteDecision(
            intent="CODE_READ", confidence=0.85,
            reason="read-only explicite (regex)", source="regex", readonly=True,
        )
    # 2. URL / browse
    if _RE_BROWSE.search(q):
        return RouteDecision(
            intent="BROWSE", confidence=0.8,
            reason="URL/visite web (regex)", source="regex",
        )
    # 3. Outil ciblé
    if _RE_TOOL.search(q):
        return RouteDecision(
            intent="TOOL", confidence=0.75,
            reason="outil ciblé (regex)", source="regex",
        )
    # 4. Recherche / apprentissage
    if _RE_RESEARCH.search(q):
        return RouteDecision(
            intent="RESEARCH", confidence=0.75,
            reason="recherche/veille (regex)", source="regex",
        )
    # 5. Détection feedback/observation + impératif
    has_imperative_write = bool(_RE_WRITE_IMPERATIVE.search(q))
    has_read = bool(_RE_READ_VERBS.search(q))
    is_feedback = bool(_RE_FEEDBACK_MARKERS.search(q))

    # 5a. Feedback + impératif explicite → CODE_WRITE (ex: « …bug, corrige X »)
    if is_feedback and has_imperative_write:
        return RouteDecision(
            intent="CODE_WRITE", confidence=0.8,
            reason="feedback + impératif explicite (regex)", source="regex",
        )
    # 5b. Feedback + lecture → CODE_READ (ex: « j'ai un bug, regarde »)
    if is_feedback and has_read and not has_imperative_write:
        return RouteDecision(
            intent="CODE_READ", confidence=0.75,
            reason="feedback + demande de lecture (regex)", source="regex", readonly=True,
        )
    # 5c. Feedback pur (observation utilisateur) → CHAT (pas d'action)
    if is_feedback:
        return RouteDecision(
            intent="CHAT", confidence=0.75,
            reason="feedback/observation sans ordre (regex)", source="regex",
        )
    # 6. Impératif de lecture (sans feedback) → CODE_READ
    if has_read and not has_imperative_write:
        return RouteDecision(
            intent="CODE_READ", confidence=0.7,
            reason="verbe d'analyse sans écriture (regex)", source="regex", readonly=True,
        )
    # 7. Impératif d'écriture (sans feedback) → CODE_WRITE
    if has_imperative_write:
        return RouteDecision(
            intent="CODE_WRITE", confidence=0.75,
            reason="verbe impératif d'écriture (regex)", source="regex",
        )
    # 8. Ambigu → CHAT (le plus safe : pas d'escalade)
    return RouteDecision(
        intent="CHAT", confidence=0.4,
        reason="ambigu (regex fallback)", source="regex",
    )


# ──────────────────────────────────────────────────────────────────────────
# Logging JSONL (audit + fine-tuning)
# ──────────────────────────────────────────────────────────────────────────

_LOG_PATH = DATA_DIR / "logs" / "routing.jsonl"
_LOG_MAX_BYTES = 5 * 1024 * 1024  # 5 MB rolling


def _log_decision(query: str, decision: RouteDecision) -> None:
    """Persiste la décision dans data/logs/routing.jsonl (best-effort)."""
    try:
        _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        # Rotation simple si > 5 MB
        if _LOG_PATH.exists() and _LOG_PATH.stat().st_size > _LOG_MAX_BYTES:
            _LOG_PATH.rename(_LOG_PATH.with_suffix(".jsonl.1"))
        with _LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(decision.to_log_dict(query), ensure_ascii=False) + "\n")
    except Exception as e:
        logger.debug("[intent_router] Log failed (non-bloquant): {}", e)


# ──────────────────────────────────────────────────────────────────────────
# Point d'entrée unique
# ──────────────────────────────────────────────────────────────────────────


async def classify(
    query: str,
    *,
    llm_chat: Optional[Any] = None,
    context: Optional[Dict[str, Any]] = None,
    timeout_s: float = 5.0,
) -> RouteDecision:
    """Classifie une requête utilisateur.

    Args:
        query:      texte de l'utilisateur
        llm_chat:   coroutine ``llm_chat(messages=[...]) -> str`` (ReAct l'injecte)
        context:    contexte éventuel (non utilisé pour l'instant, réservé)
        timeout_s:  timeout du LLM (le regex fallback prend le relais si expiré)

    Returns:
        RouteDecision (toujours, jamais None).
    """
    q = (query or "").strip()
    if not q:
        return RouteDecision(intent="CHAT", confidence=1.0, reason="empty", source="override")

    # 1) Cache — normaliser avant hash pour que "Zippe" == "zippe" == "ZIPPE"
    key = hash(q[:500].strip().lower())
    cached = _cache_get(key)
    if cached is not None:
        logger.debug("[intent_router] cache hit: {} ({:.2f})", cached.intent, cached.confidence)
        return cached

    # 2) Regex FIRST — si un pattern clair matche, pas besoin d'appeler le LLM
    #    Gain : ~2s de latence en moins pour la majorité des requêtes.
    #    Le LLM n'est appelé que quand c'est ambigu (write+read mélangés, etc.)
    t0 = time.perf_counter()
    regex_decision = _regex_fallback(q)
    _is_clear_regex = regex_decision.confidence >= 0.75  # pattern clair
    _is_chat_default = regex_decision.intent == "CHAT" and regex_decision.confidence < 0.5  # ambigu

    decision: Optional[RouteDecision] = None

    if _is_clear_regex and not _is_chat_default:
        # Pattern clair (CODE_WRITE, BROWSE, TOOL, RESEARCH, CODE_READ) → pas besoin de LLM
        decision = regex_decision
    elif llm_chat is not None:
        # Ambigu ou CHAT par défaut → LLM pour trancher
        try:
            messages = [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": q[:500]},
            ]
            raw = await asyncio.wait_for(llm_chat(messages), timeout=timeout_s)
            decision = _parse_llm_response(raw or "")
            if decision is None:
                logger.warning(
                    "[intent_router] LLM réponse non parsable, fallback regex. raw={!r}",
                    (raw or "")[:200],
                )
        except asyncio.TimeoutError:
            logger.warning("[intent_router] LLM timeout {}s → fallback regex", timeout_s)
        except Exception as e:
            logger.warning("[intent_router] LLM échoué ({}) → fallback regex", e)

    # 3) Fallback regex si LLM n'a rien donné
    if decision is None:
        decision = regex_decision

    decision.latency_ms = int((time.perf_counter() - t0) * 1000)

    # 4) Cache + log (key déjà normalisé en minuscules ci-dessus)
    _cache_put(key, decision)
    _log_decision(q, decision)

    # 5) Reliability metrics
    try:
        from ..utils.reliability_metrics import get_metrics as _get_rm
        _get_rm().record_routing(
            intent=decision.intent,
            source=decision.source,
            confidence=decision.confidence,
        )
    except Exception:
        pass

    logger.info(
        "[intent_router] {} conf={:.2f} src={} lat={}ms reason={!r}",
        decision.intent, decision.confidence, decision.source,
        decision.latency_ms, decision.reason,
    )
    return decision


# ──────────────────────────────────────────────────────────────────────────
# Helpers pour react.py
# ──────────────────────────────────────────────────────────────────────────


def should_route_to_codeagent(decision: RouteDecision) -> bool:
    """True si la décision réclame CodeAgent (write OR read)."""
    return decision.intent in ("CODE_WRITE", "CODE_READ")


def codeagent_intent_mode(decision: RouteDecision) -> str:
    """Retourne la valeur à propager dans context['intent'] pour le CodeAgent.

    - CODE_WRITE → 'modify' (compat existante, déclenche Architect)
    - CODE_READ  → 'read'   (nouveau, skip Architect + Reasoner)
    """
    if decision.intent == "CODE_READ":
        return "read"
    return "modify"
