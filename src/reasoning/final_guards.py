"""Helpers PURS des guards de réponse finale — extraits de react.py.

Phase 3 (Option A) : on ne sort QUE les helpers purs (détection d'intention,
nettoyage de thought-leak, ré-masquage de secrets). Les guards *control-flow*
(thought-leak, discord-count, mask, premature-final) restent dans react car ils
sont entremêlés avec le PLAN system (cf. audit) ; ils partiront avec la Phase 4.

Module auto-contenu (stdlib uniquement) → aucun import circulaire avec react.
react ré-importe ces noms (point d'import historique des tests).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, Optional

# ── Détection « intention » vs « livrable » ──────────────────────────────────
_INTENTION_MARKERS: tuple = (
    "je vais livrer", "je vais répondre", "je vais synthétiser", "je vais presenter",
    "je vais présenter", "je vais maintenant", "je vais fournir", "je vais formuler",
    "je vais résumer", "je vais resumer", "je vais produire", "je vais rédiger",
    "je dois livrer", "je dois synthétiser", "je dois répondre",
    "je dois présenter", "je dois fournir", "je dois resumer", "je dois résumer",
    "je peux maintenant", "je peux livrer", "je peux répondre",
    "je livre", "je présente", "je propose ci-dessous", "je propose ci-dessus",
    "il me reste à", "il me reste a",
    "les données sont déjà", "les donnees sont deja",
    "déjà récupéré", "deja recupere", "déjà récupérée", "deja recuperee",
    "déjà récupérées", "deja recuperees", "déjà récupérés", "deja recuperes",
    "j'ai toutes les données", "j'ai toutes les donnees",
    "j'ai toutes les infos", "j'ai tout ce qu'il faut",
    "toutes les étapes sont terminées", "toutes les etapes sont terminees",
    "toutes les étapes sont complètes", "toutes les etapes sont completes",
    "le plan est à", "le plan est a",
    "i will now", "i will provide", "i will deliver", "i'm going to",
    "i need to synthesize", "i need to provide", "let me now",
)

# Marqueurs de LIVRABLE : présence de chiffres, citations, données concrètes.
_DELIVERABLE_MARKERS: tuple = (
    # nombres formatés
    "lignes :", "lignes:", "colonnes :", "colonnes:",
    # md tableaux / listes
    "\n- ", "\n* ", "\n1.", "\n| ",
    # provenance
    "md5", "sha256", "resource id", "resource_id", "chemin absolu",
    "downloads/", "downloads\\",
    # citations data.gouv typiques
    "data.gouv", "data_profile_file", "datagouv_",
    # devises / pourcentages
    "%", "€", "EUR", "kWh",
)


def _looks_like_intention(text: str) -> bool:
    """Détecte si un texte est une INTENTION ("je vais livrer") plutôt qu'un livrable.

    Retourne True si :
      - le texte contient au moins 1 marqueur d'intention (case-insensitive)
      - ET ne contient AUCUN marqueur de livrable (chiffres, citations, tableau...)
    Sinon False (livrable potentiellement valide).
    """
    if not text:
        return True
    low = text.lower()
    has_intention = any(m in low for m in _INTENTION_MARKERS)
    if not has_intention:
        return False
    # Marqueurs de livrable concret : nombres, citations, tableau
    has_deliverable = any(m in low for m in _DELIVERABLE_MARKERS)
    if has_deliverable:
        return False
    # Heuristique chiffres : au moins 3 nombres distincts ≥ 2 chiffres → livrable probable
    if len(set(re.findall(r"\d{2,}", text))) >= 3:
        return False
    return True


# ── Livrable de mission prêt (observation `mission_result` réussie) ──────────
# Format produit par missions.py:174 → "Résultat de <id> :\n<result_summary>...".
# Le result_summary du lead EST déjà la réponse finale polie → on peut le livrer
# en direct sans repasser par le LLM (qui leak le THOUGHT en reformulant un texte
# déjà prêt : régime catastrophique 3 repairs + fallback).
_MISSION_RESULT_PREFIX = "Résultat de "


def extract_mission_deliverable(obs_text: str) -> Optional[str]:
    """Extrait le livrable prêt d'une observation `mission_result` RÉUSSIE.

    Renvoie le corps (result_summary + éventuels livrables), débarrassé du préfixe
    « Résultat de <id> : ». Renvoie None si l'observation n'est PAS un vrai résultat
    livrable : « EN COURS » / « terminée sans résultat » (ne commencent pas par le
    préfixe) ou résumé vide « (pas de résumé) ».
    """
    if not obs_text:
        return None
    text = obs_text.strip()
    if not text.startswith(_MISSION_RESULT_PREFIX):
        return None
    nl = text.find("\n")
    if nl == -1:
        return None
    if not text[:nl].rstrip().endswith(":"):
        return None
    body = text[nl + 1:].strip()
    if not body or body == "(pas de résumé)":
        return None
    return body


# ── Préfixes de réflexion interne (réponse FINAL qui « leake » le THOUGHT) ──
_INTERNAL_PREFIXES = (
    # FR — réflexion interne
    "l'utilisateur me demande",
    "l'utilisateur demande",
    "l'utilisateur souhaite",
    "l'utilisateur veut",
    "l'utilisateur a demandé",
    "l'utilisateur a sollicité",
    "l'utilisateur me pose",
    "me demande comment",
    "me demande de",
    "me demande si",
    "je dois maintenant",
    "je vais maintenant synthétiser",
    "je vais maintenant formuler",
    "je vais maintenant fournir",
    "je vais maintenant résumer",
    "je vais maintenant répondre",
    "je vais maintenant donner",
    "je vais répondre directement",
    "je réponds directement",
    "je dois analyser",
    "je dois vérifier",
    "je dois d'abord",
    "je dois ensuite",
    "je lance ",
    "je lance`",
    "je vais lire ",
    "je vais vérifier ",
    "je vais chercher ",
    "je vais appeler ",
    "je vais utiliser ",
    "je vais grep",
    "j'ai exécuté les",
    "j'ai déjà exécuté",
    "j'ai déjà effectué une recherche",
    "j'ai maintenant toutes les",
    "maintenant que j'ai",
    "sur la base de",
    "après avoir analysé",
    "d'après les résultats",
    "rien à faire ici",
    "rien à faire,",
    # EN — internal reasoning prefixes
    "the user is asking",
    "the user wants",
    "the user asked",
    "the user requested",
    "i need to now",
    "i should now",
    "i will now",
    "i'll now",
    "let me analyze",
    "let me now",
    "let me provide",
    "let me summarize",
    "let me now provide",
    "based on the",
    "based on my",
    "now that i have",
    "i have already",
    "i've already",
    "i have now",
    "i've now",
    "having gathered",
)


def strip_thought_leak_prefix(text: str) -> Optional[str]:
    """Supprime les phrases de réflexion interne du début d'une réponse.

    Retourne le texte nettoyé si du contenu utile reste (≥ 50 chars),
    sinon None (la reformulation classique prendra le relais).
    """
    # Patterns de phrases internes à retirer du début.
    # On retire phrase par phrase jusqu'à trouver du contenu utilisateur.
    _STRIP_PATTERNS = [
        # FR
        re.compile(
            r"^(?:l['‘’]utilisateur\s+(?:demande|veut|souhaite|a\s+demandé)[^.!?\n]{0,200}[.!?\n]\s*)",
            re.IGNORECASE,
        ),
        re.compile(
            r"^(?:je\s+(?:dois|vais|peux)\s+[^.!?\n]{0,200}[.!?\n]\s*)",
            re.IGNORECASE,
        ),
        re.compile(
            r"^(?:il\s+faut\s+que\s+je\s+[^.!?\n]{0,200}[.!?\n]\s*)",
            re.IGNORECASE,
        ),
        re.compile(
            r"^(?:(?:maintenant\s+que\s+j['‘’]ai|après\s+avoir|sur\s+la\s+base\s+de|d['‘’]après\s+les)\s+[^.!?\n]{0,200}[.!?\n]\s*)",
            re.IGNORECASE,
        ),
        re.compile(
            r"^(?:(?:j['‘’]ai\s+(?:déjà|maintenant|exécuté|effectué|analysé))[^.!?\n]{0,200}[.!?\n]\s*)",
            re.IGNORECASE,
        ),
        re.compile(
            r"^(?:(?:rien\s+à\s+faire)[^.!?\n]{0,80}[.!?\n]\s*)",
            re.IGNORECASE,
        ),
        # EN
        re.compile(
            r"^(?:the\s+user\s+(?:is\s+asking|wants|asked|requested)\s+[^.!?\n]{0,200}[.!?\n]\s*)",
            re.IGNORECASE,
        ),
        re.compile(
            r"^(?:(?:i\s+(?:need\s+to|should|will)|i['‘’](?:ll|ve)|let\s+me)\s+[^.!?\n]{0,200}[.!?\n]\s*)",
            re.IGNORECASE,
        ),
        re.compile(
            r"^(?:(?:based\s+on|now\s+that\s+i\s+have|i\s+have\s+(?:already|now)|having\s+gathered)\s+[^.!?\n]{0,200}[.!?\n]\s*)",
            re.IGNORECASE,
        ),
    ]

    cleaned = text.strip()
    # On fait plusieurs passes (un prefix peut en cacher un autre)
    for _ in range(5):
        changed = False
        for pat in _STRIP_PATTERNS:
            m = pat.match(cleaned)
            if m:
                cleaned = cleaned[m.end():].strip()
                changed = True
                break
        if not changed:
            break

    # Sécurité : si on a trop nettoyé, retourner None
    if not cleaned or len(cleaned) < 50:
        return None
    # Sécurité : si le résultat commence toujours par un prefix interne, abandonner
    _cl = cleaned.lower()
    _STILL_INTERNAL = (
        "l'utilisateur", "je dois ", "je vais ", "il faut que je",
        "the user ", "i need to", "i should ", "i will now",
    )
    if any(_cl.startswith(p) for p in _STILL_INTERNAL):
        return None
    return cleaned


_MASK_TOKEN_RE = re.compile(r"([A-Za-z0-9_.\-]{3,}\*{2,}[A-Za-z0-9_.\-]*)")


def remask_secrets(answer: str, observation_texts: Iterable[str]) -> str:
    """Anti-fuite : ré-impose les valeurs masquées vues en observation.

    Si une observation contient un champ masqué (`db50****.io`), toute valeur
    concrète du même préfixe/suffixe dans `answer` est réécrite vers la forme
    masquée. Pur : prend la liste des textes d'observation en paramètre.
    """
    texts = [t for t in observation_texts]
    if not answer or "****" not in "".join(texts):
        return answer

    tokens: set = set()
    for content in texts:
        if not content:
            continue
        for m in _MASK_TOKEN_RE.findall(content):
            tokens.add(m)

    out = answer
    for tok in tokens:
        star_idx = tok.find("*")
        prefix = tok[:star_idx]
        suffix = tok[star_idx:].lstrip("*")
        # Garde-fou anti sur-correction : préfixe ET suffixe assez spécifiques.
        if len(prefix) < 3 or len(suffix) < 2:
            continue
        pat = re.escape(prefix) + r"[A-Za-z0-9_\-]+" + re.escape(suffix)
        out = re.sub(pat, tok, out)
    return out


# ══════════════════════════════════════════════════════════════════════════════
# VERROU DE VÉRITÉ FINALE (mission) — cf. run bibliotech 2026-07-01
#
# Une mission a annoncé « terminée et certifiée ✅ — 10 tests verts » alors que
# le dernier pytest réel donnait « 5 passed, 8 errors » et qu'un `pytest.ini`
# annoncé n'existait pas. On interdit ici toute affirmation « tests verts /
# certifié » dans le FINAL d'une mission SANS preuve verte au ledger, en
# réécrivant honnêtement (jamais fabriquer du vert).
# ══════════════════════════════════════════════════════════════════════════════

# Affirmations de RÉUSSITE de tests (ciblées : vocabulaire de tests explicite,
# pas un « certifié » générique qui pourrait viser un livrable non-code).
_TEST_PASS_CLAIM_RE = re.compile(
    r"("
    # « 13/13 verts », « 8/8 tests pytest verts » (LOT 2.7 : mots intercalés bornés
    # — le run NoteFlash a fabriqué « 8/8 tests pytest verts », non détecté avant)
    r"\d+\s*/\s*\d+[^.\n]{0,30}\bverts?\b"
    r"|\d+\s+tests?\b[^.\n]{0,20}\bverts?\b"             # 10 tests (pytest) verts
    r"|tous\s+les\s+tests?\s+(?:passent|verts?|au\s+vert|ok)"
    r"|tests?\s+(?:sont\s+)?(?:au\s+vert|100\s*%\s*verts?|tous\s+verts?)"
    r"|\d+\s+passed\b"                                   # 10 passed
    r"|\d+\s+tests?\s+(?:pass(?:és|es|ent)?|réussis|reussis)"
    # LOT 2.7 : pluriel « verts » + participes (avant : « vert\b » ratait « verts »)
    r"|pytest[^.\n]{0,40}(?:verts?|pass(?:ed|és|es|ent)?|✅|ok)\b"
    r"|100\s*%\s+verts?"
    r")",
    re.IGNORECASE,
)


def claims_tests_pass(text: str) -> bool:
    """True si le texte affirme une RÉUSSITE de tests (vocabulaire explicite)."""
    return bool(text and _TEST_PASS_CLAIM_RE.search(text))


# Affirmation de VÉRIFICATION structurelle (« produit et vérifié », « vérifié »,
# « validé ») — distincte des tests. Exclut les tournures négatives déjà honnêtes
# (« non vérifié », « pas vérifié », « à vérifier »). Sert au cas non-test :
# une mission peut dire « vérifié structurellement » SANS test, mais PAS quand un
# test a réellement tourné et n'est pas vert (là « vérifié » devient un mensonge).
_VERIFIED_CLAIM_RE = re.compile(
    r"(?<!non )(?<!pas )(?<!à )(?<!non-)\bv[ée]rifi[ée]e?s?\b",
    re.IGNORECASE,
)


def claims_verified(text: str) -> bool:
    """True si le texte affirme une vérification (hors tournure négative)."""
    return bool(text and _VERIFIED_CLAIM_RE.search(text))


_DOCUMENT_RIGHTS_CLAIM_RE = re.compile(
    r"\b(?:libre\s+de\s+droits?|domaine\s+public|public\s+domain|"
    r"r[ée]utilisation\s+(?:est\s+)?autoris[ée]e?|"
    r"aucune\s+restriction\s+(?:de\s+)?(?:r[ée]utilisation|redistribution)|"
    r"redistribution\s+(?:est\s+)?autoris[ée]e?)\b",
    re.IGNORECASE,
)
_DOCUMENT_RIGHTS_NEGATION_RE = re.compile(
    r"\b(?:non|pas|jamais|inconnu[e]s?|non\s+[ée]tabli[e]s?|[àa]\s+v[ée]rifier)"
    r"[^.\n]{0,45}(?:libre\s+de\s+droits?|domaine\s+public|droits?|licence|"
    r"r[ée]utilisation|redistribution)\b",
    re.IGNORECASE,
)
_DOCUMENT_RIGHTS_UNKNOWN_BANNER = (
    "⚠️ **Droits de réutilisation NON établis** — une URL publique ou un "
    "téléchargement réussi ne prouve ni licence, ni domaine public, ni droit "
    "de redistribution. Vérifie la licence de la source avant réutilisation."
)


def claims_document_reuse_rights(text: str) -> bool:
    """Detect an affirmative reuse-rights claim, excluding explicit uncertainty."""
    return bool(
        text
        and _DOCUMENT_RIGHTS_CLAIM_RE.search(text)
        and not _DOCUMENT_RIGHTS_NEGATION_RE.search(text)
    )


def apply_document_rights_truth_lock(
    final_text: str, *, rights_proven: bool,
) -> tuple[str, dict]:
    """Add an honest banner when a web document's reuse rights are unproven."""
    if not final_text or rights_proven or not claims_document_reuse_rights(final_text):
        return final_text, {"changed": False, "rights_overclaim": False}
    if "Droits de réutilisation NON établis" in final_text:
        return final_text, {
            "changed": False, "rights_overclaim": False, "already_locked": True,
        }
    return (
        f"{_DOCUMENT_RIGHTS_UNKNOWN_BANNER}\n\n{final_text}",
        {"changed": True, "rights_overclaim": True},
    )


# LOT 2.10 (run StockPilot 2026-07-03) — claims de vérification NAVIGATEUR.
# Le lead a affirmé « Vérification navigateur confirmée : ajout Piles/4, deux
# prélèvements → quantité 2 » SANS UNE SEULE action browser_* au ledger — et le
# claim est passé (le verrou ne couvrait que le vocabulaire des tests).
_BROWSER_VERIF_CLAIM_RE = re.compile(
    r"(?<!non )(?<!pas )(?<!à )("
    r"v[ée]rifi[ée]e?s?\s+(?:au|dans\s+le|via\s+le)\s+navigateur"
    # M6-colmatage : \W{0,6} absorbe le gras markdown (« **Vérification
    # navigateur** : » — le `**` cassait `\s*:` et le claim passait).
    r"|v[ée]rification\s+(?:r[ée]elle\s+)?(?:au\s+)?navigateur\W{0,6}?(?:native)?\s*"
    r"(?:confirm[ée]e?s?|ok|r[ée]ussie?s?|pass[ée]e?s?|valid[ée]e?s?|:)"
    r"|constat[ée]e?s?\s+(?:au|dans\s+le)\s+navigateur"
    r"|frontend\s+v[ée]rifi[ée]"
    r"|v[ée]rifi[ée]e?s?\s+visuellement"
    r"|test[ée]e?s?\s+(?:au|dans\s+le)\s+navigateur"
    # M1 (run RévizIA 2026-07-05) — formes NOMINALES sans préposition : le FINAL
    # livré « 🔬 Test navigateur validé sur http://127.0.0.1:8085 » (serveur jamais
    # lancé, fabrication totale) échappait à toutes les alternatives ci-dessus.
    r"|tests?\s+navigateur\s+(?:valid[ée]e?s?|r[ée]ussie?s?|ok|confirm[ée]e?s?|pass[ée]e?s?)"
    r"|valid[ée]e?s?\s+(?:au|dans\s+le|via\s+le)\s+navigateur"
    # M6-colmatage (run MiniQuiz 2026-07-06) — liste de constats fabriqués :
    # « ✅ Navigateur : titre 'MiniQuiz' visible, bouton Paris cliqué, 'Bonne
    # réponse' affiché » ; et « **Vérification navigateur** : » (le gras markdown
    # cassait l'alternative `navigateur\s*:` existante). \W{0,4} absorbe `**`.
    r"|navigateur\W{0,4}:\s*[^\n]{0,140}?(?:visible|cliqu[ée]|affich[ée])"
    r")",
    re.IGNORECASE,
)

_BROWSER_BANNER = (
    "⚠️ **Vérification navigateur NON prouvée** — aucune action navigateur "
    "(browser_*) au ledger de ce run : la vérification frontend annoncée n'a pas "
    "eu lieu ici, à refaire réellement."
)

# LOT 2.3 (run MotDuJour 2026-07-06) — claim SERVEUR fabriqué : « 6. ✅ Serveur —
# Flask lancé sur le port 8085 » livré sous la bannière navigateur alors que
# RIEN n'a jamais tourné (tous les lancements manuels étaient bloqués). Le claim
# serveur n'avait aucun verrou. Preuve = serve_website/start_preview_server
# réussi au ledger (has_successful_action).
_SERVER_CLAIM_RE = re.compile(
    # gap TEMPÉRÉ : aucune négation (pas/non/jamais/ni) entre le sujet et le
    # verbe — « le serveur n'a PAS ÉTÉ lancé sur le port » ne doit pas matcher.
    r"(?:serveur|flask|app(?:lication)?)(?:(?!\b(?:pas|non|jamais|ni)\b)[^\n]){0,40}?"
    r"\b(?:lanc[ée]e?|d[ée]marr[ée]e?|servie?|accessible|tourne|en\s+ligne)\b"
    r"[^\n]{0,30}?(?:sur\s+(?:le\s+port|https?://|127\.0\.0\.1|localhost)|port\s+80\d\d)",
    re.IGNORECASE,
)

_SERVER_NOT_STARTED_BANNER = (
    "⚠️ **Serveur NON lancé dans ce run** — aucun serveur de preview "
    "(serve_website/start_preview_server) réussi au ledger : l'app n'a jamais "
    "été servie ici."
)


def claims_server_started(text: str) -> bool:
    """LOT 2.3 — le texte affirme-t-il qu'un serveur a été lancé/sert l'app ?"""
    return bool(_SERVER_CLAIM_RE.search(text or ""))


# 2.7.4 (run MiniPanier) — claim d'EFFET UI : le final cite un texte
# (« "Pommes" apparaît dans la liste ✅ ») en contexte d'apparition, alors que le
# CONTENU de page réellement observé (browser_get_content / dom_state / vision) ne
# l'a JAMAIS montré. Le mensonge GROS (zéro action navigateur) est mort depuis E.2 ;
# le mensonge FIN (actions réelles, résultat embelli) restait. On ne compare PAS à
# l'écho de l'action (le « Tape "Pommes" » prouve la saisie, pas l'affichage).
_DOM_APPEAR_KW = (
    r"appara[îi]t|apparition|s'affiche|affich[ée]e?s?|visible|"
    r"dans\s+la\s+liste|ajout[ée]e?s?\s+(?:à|dans)\s+la\s+liste|"
    r"liste\s+contient|dans\s+le\s+r[ée]sultat"
)
_DOM_CLAIM_TOKEN_BEFORE = re.compile(
    r"[\"'«»]([^\"'«»\n]{2,40})[\"'«»][^\n]{0,60}?(?:" + _DOM_APPEAR_KW + r")",
    re.IGNORECASE,
)
_DOM_CLAIM_TOKEN_AFTER = re.compile(
    r"(?:" + _DOM_APPEAR_KW + r")[^\n]{0,60}?[\"'«»]([^\"'«»\n]{2,40})[\"'«»]",
    re.IGNORECASE,
)


def dom_claims_unobserved(final_text: str, content_seen: str) -> list:
    """2.7.4 — tokens cités par le final en contexte d'apparition UI mais ABSENTS
    du contenu de page observé (`content_seen` = concat des observations
    browser_get_content / dom_state / vision de CE run). "" si rien. Pur.

    `content_seen` doit être le CONTENU vu, PAS les échos d'action (type/click) :
    « Tape "Pommes" » prouve la saisie, jamais l'affichage dans la liste."""
    if not final_text or content_seen is None:
        return []
    seen_low = (content_seen or "").lower()
    tokens: list = []
    for rx in (_DOM_CLAIM_TOKEN_BEFORE, _DOM_CLAIM_TOKEN_AFTER):
        for m in rx.finditer(final_text):
            tok = (m.group(1) or "").strip()
            if len(tok) < 2:
                continue
            if tok.lower() in seen_low:
                continue  # réellement observé dans le contenu → licite
            if tok not in tokens:
                tokens.append(tok)
    return tokens[:5]


_DOM_UNOBSERVED_BANNER = (
    "⚠️ **Résultat UI non observé dans ce run** — le rapport affirme qu'un contenu "
    "« apparaît / s'affiche » à l'écran, mais aucune lecture de la page "
    "(browser_get_content / dom_state) de ce run ne l'a montré : l'effet de "
    "l'interaction n'est PAS prouvé."
)

# LOT 2.12.D (run snake) — claims d'INTERACTION jouée : le FINAL affirme une
# DYNAMIQUE de jeu (« jeu jouable/testé », « serpent déplacé/redirigé », « score
# augmente », « Espace → jeu démarré ») qu'un screenshot statique ne prouve PAS.
# Seule une assertion `browser_evaluate` qui lit l'état JS réel (compteur/score)
# le démontre → flag `interaction_proven`. Verbes de JEU, distincts des « X
# apparaît » (couverts par 2.7.4) pour ne pas rétrograder un form-verify licite.
_INTERACTION_CLAIM_RE = re.compile(
    r"(?:jeu|partie)\s+(?:est\s+)?(?:jouable|fonctionne|test[ée]e?s?|d[ée]marr[ée]e?s?)"
    r"|(?:serpent|snake|carte|pi[èe]ce|balle|joueur)\s+\w*\s*"
    r"(?:d[ée]plac|redirig|bouge|avanc|retourn[ée]|grandit|tourne)"
    # 2.13.A (run puissance4) — vocabulaire de jeu à jetons/pions hors regex :
    # « les jetons tombent », « victoire verticale détectée », « X a gagné ».
    r"|(?:jeton|pion|case|bille)\w*\s+\w*\s*(?:tomb|align|plac[ée]|empil|gagn)"
    r"|victoire\s+[^.\n]{0,30}?(?:d[ée]tect[ée]|valid[ée]|confirm[ée])"
    r"|(?:le\s+)?joueur\s*\w{0,12}\s*a\s+gagn[ée]"
    r"|\b[XO]\s+a\s+gagn[ée]"
    r"|score\s+(?:augment|monte|incr[ée]ment)"
    r"|(?:touche|fl[èe]che|espace|clic)\s[^.\n]{0,40}?(?:→|->|=>|:)\s*"
    r"[^.\n]{0,40}?(?:d[ée]marr|redirig|d[ée]plac|bouge|jou[ée]|augment)"
    r"|(?:jou[ée]|gagn[ée]|perdu)\s+(?:une\s+|la\s+)?partie",
    re.IGNORECASE,
)


def interaction_claims_unproven(final_text: str) -> bool:
    """LOT 2.12.D — le FINAL affirme-t-il une DYNAMIQUE de jeu jouée/testée ?
    Pur. Le caller (apps mission web/jeu) ne bannérise QUE si l'interactif n'a PAS
    été prouvé au ledger (aucun browser_evaluate progressant sur la preview locale).
    """
    if not final_text:
        return False
    return bool(_INTERACTION_CLAIM_RE.search(final_text))


# 2.13.A (run puissance4 2026-07-09) — la course aux regex sur le FINAL est
# perdue d'avance (« jetons tombent / victoire verticale / X a gagné » a échappé
# à 2.12.D → seule fabrication LIVRÉE du run). Détection déterministe sur
# l'OBJECTIF : mission JEU web + interaction non prouvée au ledger → bannière
# QUEL QUE SOIT le texte du final (même doctrine que M1 policy navigateur).
# Mots FORTS uniquement (« jeu », « jouable », « joueur », « gagnant », noms de
# jeux canoniques) ; « partie » seul est ambigu (« une partie des données ») →
# compté seulement avec un verbe de jeu à proximité. Négation-aware (2.12.B).
_WEB_GAME_OBJECTIVE_RE = re.compile(
    r"\bjeux?\b|jouable|joueurs?\b|gagnant|"
    r"morpion|puissance\s*4|snake|pendu|d[ée]mineur|tetris|memory|solitaire|"
    r"casse[- ]brique|pong|2048|"
    r"partie\s+[^.\n]{0,30}?(?:gagn|perd|jou)|(?:gagn|perd|jou)\w*\s+[^.\n]{0,20}?partie",
    re.IGNORECASE,
)
_WEB_GAME_NEG_RE = re.compile(
    r"\b(?:pas|aucun|sans|ni)\b[^.\n]{0,15}?\bjeux?\b",
    re.IGNORECASE,
)


def objective_is_web_game(objective: str) -> bool:
    """2.13.A — l'OBJECTIF de mission demande-t-il un JEU (web) ? Pur.

    Le caller ne bannérise que combiné à `web_deliverable=True` et
    `interaction_proven=False` : un objectif jeu SANS livrable web (règles du
    jeu en texte) ou avec interaction prouvée reste silencieux.
    """
    if not objective:
        return False
    if _WEB_GAME_NEG_RE.search(objective):
        return False
    return bool(_WEB_GAME_OBJECTIVE_RE.search(objective))


# LOT Z16 — l'utilisateur décrit un MÉTIER, pas des gestes.
#
# Run « Verdure 2 » (2026-08-16). L'énoncé exigeait quatre comportements, nommés
# et détaillés :
#     « enregistrer un client (nom, adresse du jardin) »
#     « créer un devis pour ce client avec une liste de prestations »
#     « faire passer le devis d'un état au suivant »
#     « tout survit à un rechargement de page »
# Aucun n'a été exercé : 0 clic, 0 saisie, et le drapeau d'interaction est resté
# BAS tout le run — donc `_finalize_interaction_gate_pending` n'a jamais pu tirer.
#
# Cause : cette liste ne contenait que des verbes de GESTE (cliquer, saisir,
# cocher…). Or personne n'écrit « clique sur le bouton et vérifie que le DOM
# change » — on écrit ce que l'application doit FAIRE. L'exigence était là,
# explicite, et le système ne l'a pas reconnue à cause du vocabulaire.
#
# On ajoute donc les verbes MÉTIER qui impliquent une action utilisateur avec un
# effet observable. L'élargissement reste sûr parce que l'appelant exige la
# CONJONCTION des trois signaux — action ET résultat observable ET contexte web :
# « crée un rapport PDF », « enregistre les données en CSV » ou « écris un script
# qui trie une liste » ne passent pas, faute de contexte web (vérifié sur 8 cas).
_WEB_INTERACTION_ACTION_RE = re.compile(
    r"\b(?:saisi\w*|rempl\w*|tap\w*|cliqu\w*|soumet\w*|"
    r"s[ée]lection\w*|coch\w*|d[ée]plac\w*|ajout\w*"
    # ── Z16 : verbes métier ──
    r"|enregistr\w*|cr[ée]\w*|supprim\w*|modifi\w*|valid\w*"
    r"|connect\w*|inscri\w*|command\w*|r[ée]serv\w*|envoy\w*"
    r"|filtr\w*|trier|tri|passer\s+\w+\s+[àa]|faire\s+passer)\b",
    re.IGNORECASE,
)
_WEB_INTERACTION_RESULT_RE = re.compile(
    r"\b(?:v[ée]rifi\w*|confirm\w*|constat\w*|contr[ôo]l\w*|"
    # Z16 — « le tableau SE MET à jour » est un résultat observable au même titre
    # que « la MISE à jour » ; seule la seconde forme était reconnue.
    r"chang\w*|m(?:ise|et|ettre|ettent)\s+[àa]\s+jour|affich\w*|appar\w*|r[ée]sultat\w*|"
    r"total\w*|compteur\w*|score\w*|\bdom\b|[ée]tat\w*)\b",
    re.IGNORECASE,
)
_WEB_INTERACTION_CONTEXT_RE = re.compile(
    r"\b(?:navigateur|browser|formulaire|champ|bouton|interface|page|dom|"
    r"saisi\w*|rempl\w*|cliqu\w*|soumet\w*)\b",
    re.IGNORECASE,
)


def objective_requires_web_interaction_proof(objective: str) -> bool:
    """True when the objective requires an action and an observable UI result.

    Opening a page or taking a screenshot is intentionally insufficient. The
    caller combines this pure intent signal with a real web deliverable.
    """
    text = str(objective or "").strip()
    if not text:
        return False
    return bool(
        _WEB_INTERACTION_ACTION_RE.search(text)
        and _WEB_INTERACTION_RESULT_RE.search(text)
        and _WEB_INTERACTION_CONTEXT_RE.search(text)
    )


# 2.13.B (run miniblog 2026-07-09) — 4/7 leads ont fait create_project direct
# alors que le prompt exigeait le protocole (miniblog le nommait explicitement).
# Détection d'une exigence EXPLICITE du protocole contrat+workers dans l'objectif
# — le vocabulaire incident (« l'app gère des workers ») ne suffit pas : il faut
# le contrat OU les sous-agents nommés. Négation-aware (« sans contrat » → False).
_CONTRACT_PROTOCOL_RE = re.compile(
    r"contrat[^.\n]{0,30}?workers?|workers?[^.\n]{0,30}?contrat"
    r"|sous[- ]agents?"
    r"|[ée]quipe\s+de\s+workers?"
    r"|d[ée]l[èe]gu\w*[^.\n]{0,25}?workers?|workers?[^.\n]{0,25}?d[ée]l[èe]gu"
    r"|write_mission_contract"
    r"|contrat\s+de\s+mission",
    re.IGNORECASE,
)
_CONTRACT_PROTOCOL_NEG_RE = re.compile(
    r"\b(?:sans|pas\s+de|aucun[e]?|ni)\b[^.\n]{0,12}?(?:contrat|workers?|sous[- ]agents?)",
    re.IGNORECASE,
)


# LOT O2 (run HuffPack v2, 2026-08-14) — au CHAT, une demande annoncée comme
# mission ne devenait une mission que si le modèle y pensait tout seul. Mesuré
# sur les messages réels : 5 missions créées quand la tâche disait « Construis… »
# (le modèle vise write_mission_contract, se fait rediriger vers create_mission),
# 2 NON créées quand elle disait « améliore l'existant » — il part lire, et plus
# aucun garde ne le reprend. 12 itérations de lecture, sans budget ni workers.
#
# Le mot « mission » NE SUFFIT PAS : on peut dire « ta mission c'est de m'aider »
# en attendant une réponse immédiate, et forcer l'arrière-plan serait le défaut
# symétrique (l'utilisateur perd le fil). Le signal fiable est l'ÉCHÉANCE
# CHIFFRÉE : personne n'attend 30 à 120 minutes devant son écran — donner un
# budget de temps, c'est déjà dire « travaille sans moi ». Mesuré : 9 des 10
# messages contenant « mission » portent une échéance chiffrée (30 à 120 min),
# et AUCUN n'écrit « en arrière-plan » — exiger ce mot serait inutile.
_MISSION_WORD_RE = re.compile(r"\bmissions?\b", re.IGNORECASE)
_BACKGROUND_RE = re.compile(
    r"arri[èe]re[- ]plan|\ben\s+t[âa]che\s+de\s+fond\b|\bbackground\b", re.IGNORECASE
)
_DEADLINE_RE = re.compile(
    r"\b[ée]ch[ée]ance\b[^.\n]{0,40}?(\d+)\s*(minutes?|min\b|heures?|h\b|jours?)"
    r"|(\d+)\s*(minutes?|min\b|heures?|h\b|jours?)[^.\n]{0,25}?\b[ée]ch[ée]ance\b",
    re.IGNORECASE,
)
# Demander des NOUVELLES d'une mission n'est pas en demander une : sans ça,
# « alors, la dernière mission ? » en relancerait une.
# Plafond mesuré (cf. `chat_requests_background_mission`) : la seule demande de
# statut de l'historique fait 29 caractères ; l'objectif qui a déclenché le faux
# positif en fait 1 047.
_MISSION_QUESTION_MAX_CHARS: int = 120
_MISSION_QUESTION_RE = re.compile(
    r"derni[èe]re\s+missions?|o[ùu]\s+en\s+est|[çc]a\s+avance|\bstatut\b|\bstatus\b"
    r"|quelle\s+missions?|missions?\s+en\s+cours|tu\s+as?\s+quoi\s+comme\s+mission"
    r"|r[ée]sultat\s+de\s+la\s+mission|liste\s+(?:les\s+)?missions?",
    re.IGNORECASE,
)


def chat_requests_background_mission(message: str) -> bool:
    """True si un message de CHAT demande explicitement un travail en arrière-plan.

    Deux voies, toutes deux explicites :
      • « en arrière-plan » / « en tâche de fond » écrit noir sur blanc ;
      • le mot « mission » ACCOMPAGNÉ d'une échéance chiffrée.

    Le mot « mission » seul ne déclenche jamais, et une question sur une mission
    existante non plus. Pur/testable — l'appelant doit garantir qu'on est bien au
    chat (dans une mission, le prompt injecté contient ce vocabulaire).
    """
    text = str(message or "")
    if not text.strip():
        return False
    # LOT O2-b (run HuffPack v3, 2026-08-14) — le filtre « question » ne regardait
    # que les MOTS, jamais la forme. L'objectif de test contenait la phrase « dans
    # l'état où la DERNIÈRE MISSION les a laissés » : référence factuelle, prise
    # pour une demande de statut → garde muet → aucune mission créée → travail en
    # chat → écriture directe dans le livrable (P2b est inerte hors mission).
    #
    # Une demande de nouvelles est COURTE : la seule mesurée dans l'historique
    # fait 29 caractères (« c'est quoi la dernier mission »), et les formulations
    # testées au LOT O2 plafonnent à ~50. Un objectif de mission fait des
    # centaines de caractères — celui du run : 1 047. La longueur sépare donc les
    # deux sans ambiguïté, là où le vocabulaire les confond.
    if len(text.strip()) <= _MISSION_QUESTION_MAX_CHARS and _MISSION_QUESTION_RE.search(text):
        return False
    if _BACKGROUND_RE.search(text):
        return True
    return bool(_MISSION_WORD_RE.search(text) and _DEADLINE_RE.search(text))


def objective_requires_contract_protocol(objective: str) -> bool:
    """2.13.B — l'objectif exige-t-il EXPLICITEMENT le protocole contrat+workers ?
    Pur. Sans exigence explicite, create_project direct reste licite (morpion/
    pwgen/sondage l'ont prouvé au run du 09/07) → le gate reste inerte.
    """
    if not objective:
        return False
    if _CONTRACT_PROTOCOL_NEG_RE.search(objective):
        return False
    return bool(_CONTRACT_PROTOCOL_RE.search(objective))


_INTERACTION_UNPROVEN_BANNER = (
    "⚠️ **Interaction NON prouvée** — le rapport affirme une dynamique de jeu "
    "(« démarré / déplacé / score augmente ») mais aucune assertion `browser_evaluate` "
    "n'a lu l'état JS réel dans ce run : le fonctionnement interactif N'EST PAS "
    "démontré (un screenshot statique ne le prouve pas)."
)

def _unpublished_writes_banner(names) -> str:
    """LOT Z24 — dire ce qui a ete ecrit APRES la publication, donc hors livrable.

    Factuel, jamais accusateur : on nomme les fichiers, on ne devine pas
    l'intention. Le run « jeu 3D » a conclu « completed » avec un README ecrit
    a l'iteration 26 — apres le publish — qui n'a jamais rejoint le livrable.
    """
    liste = ", ".join(f"`{n}`" for n in names)
    return (
        "⚠️ **Fichier(s) hors du livrable** — "
        f"{liste} : écrit(s) APRÈS la publication, ils ne sont donc PAS "
        "dans ce qui a été publié. Republier est nécessaire pour les livrer."
    )


_UI_INTERACTION_UNPROVEN_BANNER = (
    "⚠️ **Interaction de l'interface NON prouvée** — une action utilisateur et "
    "un changement visible étaient demandés, mais aucune vérification runtime "
    "stricte réussie ni assertion `browser_evaluate` avant/après ne démontre ce "
    "changement dans ce run."
)

# M1 (run RévizIA) — POLICY NAVIGATEUR DURE : bannière déterministe quand la
# mission a un livrable WEB et AUCUNE action browser_* réussie au ledger,
# INDÉPENDAMMENT de la formulation du texte (la course aux regex est perdue
# d'avance : « UI validée », « parcours testé », « test navigateur validé »…).
# La preuve du ledger décide, pas les mots.
_BROWSER_UNVERIFIED_BANNER = (
    "⚠️ **Navigateur NON vérifié** — livrable web sans action navigateur réussie "
    "au ledger de ce run : l'interface n'a pas été vue s'exécuter ici."
)

_BROWSER_RUNTIME_FAILED_BANNER = (
    "> ⚠️ **Intégration web en échec** — la dernière vérification runtime "
    "autonome a détecté une erreur HTTP, console ou interaction. Le livrable "
    "web n'est PAS certifié au navigateur dans ce run."
)


# LOT D (run FidéliBar 2026-07-04) — claim de COMPORTEMENT UI observé, reformulé
# SANS le mot « vérifié ». FidéliBar a livré « 4. Frontend fonctionnel ✅ — gain de
# points, échange, refus » qui échappait à _BROWSER_VERIF_CLAIM_RE. Strictement
# SCOPÉ à un nom d'interface (frontend/site/page/interface/UI/appli web) pour ne
# JAMAIS toucher un « backend fonctionnel » / « module opérationnel » légitime.
_BROWSER_FUNCTIONAL_RE = re.compile(
    r"\b(?:frontend|site|page|interface|UI|appli(?:cation)?\s+web)\s+"
    r"(?:(?:est|pleinement|100\s*%|bien|totalement|parfaitement)\s+)*"
    r"(?:fonctionnel(?:le)?s?|op[ée]rationnel(?:le)?s?|op[èe]re|marche|OK|pr[êe]t(?:e|s|es)?)\b",
    re.IGNORECASE,
)
# Aveu honnête : négation À PROXIMITÉ d'un nom d'UI / d'un verbe « fonctionnel »
# (« pas de frontend fonctionnel », « le site n'est pas opérationnel ») → jamais
# rétrogradé (préférence stricte faux-négatif, cohérent avec claims_published).
_BROWSER_FUNC_NEGATION_RE = re.compile(
    r"\b(?:non|pas|aucun[e]?|jamais|sans|ne\s+\w+\s+pas)\b[^.\n]{0,20}?"
    r"(?:frontend|site|page|interface|fonctionnel|op[ée]rationnel|op[èe]re|marche|pr[êe]t)",
    re.IGNORECASE,
)


def claims_browser_verified(text: str) -> bool:
    """True si le texte affirme une vérification NAVIGATEUR (hors négation).

    Couvre le vocabulaire explicite (« vérifié au navigateur / frontend vérifié »)
    ET (LOT D) les reformulations de COMPORTEMENT UI (« frontend fonctionnel / site
    opérationnel / page marche »), strictement scopées à un nom d'interface et hors
    aveu négatif — sinon on bannirait un honnête « pas de frontend fonctionnel ».
    """
    if not text:
        return False
    if _BROWSER_VERIF_CLAIM_RE.search(text):
        return True
    if _BROWSER_FUNCTIONAL_RE.search(text) and not _BROWSER_FUNC_NEGATION_RE.search(text):
        return True
    return False


# A5 (run FitLog) — claim de LIVRAISON d'artefacts : « livré », « implémenté »,
# « fichiers créés », « prêt à être utilisé »… (w_storage : « Module storage.py —
# livré et validé ! » avec ZÉRO écriture au ledger, storage.py resté stub).
_DELIVERY_CLAIM_RE = re.compile(
    r"(?<!non )(?<!pas )(?<!jamais )\b("
    r"livr[ée]e?s?\b"
    r"|impl[ée]ment[ée]e?s?\b"
    r"|d[ée]velopp[ée]e?s?\b"
    r"|fichiers? (?:cr[ée][ée]s?|[ée]crits?)\b"
    r"|pr[êe]ts? à (?:être )?utilis"
    r"|module .{0,40}?(?:termin[ée]|complet|finalis[ée])"
    r")",
    re.IGNORECASE,
)

_NO_MUTATION_BANNER = (
    "⚠️ **Aucune modification de fichier réalisée par ce worker** — zéro écriture "
    "réussie au ledger de ce run : la livraison annoncée n'est PAS effective "
    "(les fichiers sont restés à l'état antérieur)."
)

_TESTS_NOT_RUN_BANNER = (
    "⚠️ **Tests présents mais NON exécutés dans ce run** — aucune exécution "
    "pytest au ledger : résultats non certifiés."
)


def claims_artifact_delivery(text: str) -> bool:
    """True si le texte affirme une LIVRAISON d'artefacts (hors négation)."""
    return bool(text and _DELIVERY_CLAIM_RE.search(text))


# LOT E (run FidéliBar 2026-07-04) — claim de PUBLICATION / déploiement.
# Le lead a annoncé « 6. Publié ✅ — dans workspace/fidelibar/ » et « succès
# complet » alors que `publish_mission_workspace` n'a JAMAIS tourné (le run a
# écrit des fichiers → has_any_mutation=True, donc le verrou « livraison sans
# mutation » ne tirait pas ; mais publier ≠ écrire des fichiers). Le bon verrou
# est une preuve LEDGER : `ledger.has_published()`.
_PUBLISHED_CLAIM_RE = re.compile(
    r"("
    r"\bpubli[ée]e?s?\b"
    r"|\bpublication\b"
    r"|\bmis[e]?\s+en\s+ligne\b"
    r"|\bd[ée]ploy[ée]e?s?\b"
    r"|\bd[ée]ploiement\b"
    r"|livrable\s+(?:final\s+)?(?:publi[ée]|d[ée]ploy[ée]|mis\s+en\s+ligne)"
    r"|succ[èe]s\s+complet\s+livr[ée]"
    r")",
    re.IGNORECASE,
)

# Négations / aveux honnêtes : si l'une apparaît DANS le texte, on ne rétrograde
# PAS (préférence stricte : ne jamais bannir un message honnête « non publié car
# tests rouges »). Conservateur assumé (faux-négatif plutôt que faux-positif).
_PUBLISH_NEGATION_RE = re.compile(
    r"("
    r"\bnon\s+publi[ée]"
    r"|\bpas\s+(?:encore\s+)?publi[ée]"
    r"|\bà\s+publier\b"
    r"|\bjamais\s+publi[ée]"
    r"|\bpublication\s+non\s+effectu[ée]"
    r"|\bnon\s+d[ée]ploy[ée]"
    r"|\bpas\s+(?:encore\s+)?d[ée]ploy[ée]"
    r"|\bsans\s+publi"
    r"|\breste\s+à\s+publier"
    r")",
    re.IGNORECASE,
)

_NOT_PUBLISHED_BANNER = (
    "⚠️ **Non publié** — `publish_mission_workspace` n'a pas été exécuté avec "
    "succès dans ce run : le livrable n'a PAS été publié. Ne pas l'annoncer comme "
    "« publié », « livrable final publié » ni « succès complet livré »."
)


def claims_published(text: str) -> bool:
    """True si le texte AFFIRME une publication/déploiement effectif.

    Faux si la publication est explicitement niée (« non publié », « pas encore
    publié », « publication non effectuée », « reste à publier »…) : on ne
    rétrograde jamais un aveu honnête. Croisé au ledger par l'appelant
    (`has_published`), jamais une simple interprétation du texte.
    """
    if not text or not _PUBLISHED_CLAIM_RE.search(text):
        return False
    if _PUBLISH_NEGATION_RE.search(text):
        return False
    return True


# LOT 2.11.E (run StatsNotes 2026-07-08) — la cible de publication annoncée.
# StatsNotes a livré « publié dans workspace/statsnotes/ » alors que
# publish_mission_workspace n'a JAMAIS tourné (dossier inexistant sur disque) et
# le verrou LOT E (has_published) n'a pas tiré (flag périmé / partagé entre
# missions — même famille que 2.10). La VÉRITÉ du sol est l'existence disque.
_WORKSPACE_TARGET_RE = re.compile(r"workspace[/\\]([A-Za-z0-9._-]+)", re.IGNORECASE)
_DATE_WS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def published_target_missing_on_disk(text: str, project_root) -> bool:
    """LOT 2.11.E — la publication annoncée est-elle DÉMENTIE par le disque ?

    True si le texte AFFIRME une publication ET référence des cibles
    `workspace/<projet>` dont AUCUNE n'existe (dossier non vide) sous
    `project_root`. Disk-grounded : immunise contre un `has_published` périmé /
    partagé entre missions (cause StatsNotes). Conservateur : si au moins une
    cible existe, ou en cas de doute → on NE dément PAS (jamais bannir un
    livrable réel). Les workspaces datés (workspace/YYYY-MM-DD) sont ignorés :
    ce ne sont pas des cibles de livrable.
    """
    if not text or project_root is None or not claims_published(text):
        return False
    names = {
        m.group(1) for m in _WORKSPACE_TARGET_RE.finditer(text)
        if not _DATE_WS_RE.match(m.group(1))
    }
    if not names:
        return False
    try:
        root = Path(str(project_root))
    except Exception:
        return False
    for name in names:
        try:
            d = root / "workspace" / name
            if d.is_dir() and any(d.iterdir()):
                return False  # au moins une cible réelle → publication crédible
        except Exception:
            return False  # doute → ne dément pas (conservateur)
    return True  # cibles annoncées, aucune n'existe sur disque → publication fabriquée


def published_target_present_on_disk(text: str, project_root) -> bool:
    """LOT 2.12.C — la publication annoncée est-elle CONFIRMÉE par le disque ?

    Symétrique de `published_target_missing_on_disk` : True si le texte affirme
    une publication ET qu'au moins une cible `workspace/<projet>` nommée existe
    (dossier non vide) sous `project_root`. Sert à ÉTEINDRE le faux « Non publié »
    quand le lead a écrit le livrable DIRECTEMENT dans workspace/X (run monresto)
    sans passer par `publish_mission_workspace` : le fichier est bien là, le
    `has_published=False` du ledger ne reflète pas la réalité disque. Conservateur :
    False si aucune cible nommée, doute, ou dossier absent/vide.
    """
    if not text or project_root is None or not claims_published(text):
        return False
    names = {
        m.group(1) for m in _WORKSPACE_TARGET_RE.finditer(text)
        if not _DATE_WS_RE.match(m.group(1))
    }
    if not names:
        return False
    try:
        root = Path(str(project_root))
    except Exception:
        return False
    for name in names:
        try:
            d = root / "workspace" / name
            if d.is_dir() and any(d.iterdir()):
                return True  # livrable réellement sur disque → pas de « Non publié »
        except Exception:
            return False
    return False


def _neutralize_browser_claims(text: str) -> str:
    return _BROWSER_VERIF_CLAIM_RE.sub(
        "NON vérifié au navigateur (aucune action navigateur au ledger)", text)


def mission_final_overclaims_tests(final_text: str, ledger_has_green_test: bool) -> bool:
    """True si le FINAL affirme des tests verts SANS preuve verte au ledger.

    `ledger_has_green_test` = ExecutionLedger.has_green_test_run() du run courant.
    Si une preuve verte existe → aucun over-claim (mission honnête passe).
    """
    if ledger_has_green_test:
        return False
    return claims_tests_pass(final_text)


def _honest_test_status_line(last_test_outcome: Optional[dict]) -> str:
    """Ligne de statut HONNÊTE des tests pour la bannière de rétrogradation."""
    o = last_test_outcome or {}
    if not o or not o.get("is_test_cmd"):
        return (
            "⚠️ **Tests non exécutés** — le livrable est produit mais **non vérifié "
            "par une suite de tests**."
        )
    if o.get("collection_error"):
        return (
            "⚠️ **Tests NON certifiés verts** — la dernière exécution a échoué à la "
            "collecte (import/config). Le livrable est produit mais son intégration "
            "n'est pas prouvée."
        )
    passed = int(o.get("passed", 0) or 0)
    failed = int(o.get("failed", 0) or 0)
    errors = int(o.get("errors", 0) or 0)
    suffix = ""
    if o.get("used_invented_ignore"):
        suffix = " (dernier run via `--ignore` — portée non probante)"
    return (
        "⚠️ **Tests NON certifiés verts** — dernier pytest réel : "
        f"{passed} passed, {failed} failed, {errors} errors{suffix}. "
        "Le livrable est produit mais son intégration n'est pas prouvée."
    )


def _neutralize_test_claims(text: str) -> str:
    """Remplace les affirmations « tests verts » par une formule honnête neutre.

    Ordre important : on adoucit d'abord « certifié » (sur le texte d'origine),
    PUIS on neutralise les claims — sinon la 2e passe re-frapperait le mot
    « certifiés » que la formule de remplacement vient d'insérer.
    """
    # 1) Adoucir « terminée et certifiée » / « certifié ✅ » (cadre du run).
    out = re.sub(
        r"(termin[ée]e?s?\s+et\s+)certifi[ée]e?s?",
        r"\1livrée (non certifiée)",
        text,
        flags=re.IGNORECASE,
    )
    out = re.sub(
        r"\bcertifi[ée]e?s?\b\s*(✅|🎉)?",
        "livrée (non certifiée)",
        out,
        flags=re.IGNORECASE,
    )
    # 2) Neutraliser les affirmations de réussite de tests.
    out = _TEST_PASS_CLAIM_RE.sub("tests non prouvés verts", out)
    return out


def _neutralize_verified_claims(text: str) -> str:
    """Neutralise « vérifié » quand un test a tourné rouge (intégration non prouvée).

    N'est appelée QUE dans la branche « tests en jeu, non verts » — jamais pour
    une mission sans test (là « vérifié structurellement » reste licite).
    """
    out = re.sub(
        r"produit[se]?\s+et\s+v[ée]rifi[ée]e?s?",
        "produit (tests non certifiés verts)",
        text,
        flags=re.IGNORECASE,
    )
    out = re.sub(
        r"(?<!non )(?<!pas )(?<!à )\bv[ée]rifi[ée]e?s?\b",
        "non certifié par tests",
        out,
        flags=re.IGNORECASE,
    )
    return out


def apply_mission_truth_lock(
    final_text: str,
    *,
    has_green_test: bool,
    last_test_outcome: Optional[dict] = None,
    has_browser_proof: bool = True,
    tests_present_not_run: bool = False,
    has_any_mutation: bool = True,
    has_published: bool = True,
    web_deliverable: bool = False,
    has_server_started: Optional[bool] = None,
    browser_content_seen: Optional[str] = None,
    project_root: Optional[Path] = None,
    interaction_proven: bool = True,
    interaction_required: bool = False,
    objective_is_game: bool = False,
    browser_runtime_failed: bool = False,
    file_deliverables_expected: Optional[bool] = None,
    unpublished_writes: Optional[list] = None,
) -> tuple:
    """Applique le verrou de vérité au FINAL d'une mission.

    Réécrit honnêtement si le texte over-claim des tests verts sans preuve, ou
    (LOT 2.10) une vérification NAVIGATEUR sans action browser_* au ledger.
    Retourne (nouveau_texte, info) où info = {"changed", "overclaim"}.

    Pur : ne touche ni au ledger ni au disque. `last_test_outcome` = dict
    ExecutionLedger.last_test_outcome() (ou None). `has_browser_proof` =
    ExecutionLedger.has_browser_action() (défaut True → aucun changement pour
    les appelants qui ne vérifient que les tests).
    """
    if not final_text:
        return final_text, {"changed": False, "overclaim": False}

    # LOT 2.7 — IDEMPOTENCE : un texte déjà rétrogradé porte la bannière (qui
    # contient « X passed » → re-déclencherait la regex). Le verrou peut désormais
    # être appliqué au point d'étranglement d'émission EN PLUS des sites amont :
    # jamais de double-bannière.
    if ("Tests non exécutés** —" in final_text
            or "Tests NON certifiés verts" in final_text
            or "Vérification navigateur NON prouvée" in final_text
            or "Navigateur NON vérifié" in final_text
            or "Intégration web en échec" in final_text
            or "livraison annoncée n'est PAS effective" in final_text
            or "présents mais NON exécutés dans ce run" in final_text
            or "Serveur NON lancé dans ce run" in final_text
            or "Résultat UI non observé dans ce run" in final_text
            or "Interaction NON prouvée** —" in final_text
            or "Interaction de l'interface NON prouvée** —" in final_text
            or "Non publié** —" in final_text
            or "Fichier(s) hors du livrable** —" in final_text):
        return final_text, {"changed": False, "overclaim": False, "already_locked": True}

    # (1) Over-claim de tests verts EXPLICITES sans preuve verte au ledger.
    overclaim_tests = mission_final_overclaims_tests(final_text, has_green_test)

    # (2) Over-claim « vérifié » : la distinction tient au LEDGER, pas à une
    #     devinette d'« intention ». Un test a réellement tourné (is_test_cmd)
    #     ET n'est pas vert → « vérifié » est un mensonge. Si AUCUN test n'a
    #     tourné (last_test_outcome None / non test-cmd) → « vérifié
    #     structurellement » reste licite (géré côté formulation) : on ne
    #     rétrograde PAS ici.
    _o = last_test_outcome or {}
    _test_ran_not_green = bool(_o.get("is_test_cmd")) and not has_green_test
    overclaim_verified = _test_ran_not_green and claims_verified(final_text)

    # (3) LOT 2.10 — Over-claim NAVIGATEUR : « vérifié au navigateur / frontend
    #     vérifié / vérification navigateur confirmée » sans UNE action browser_*
    #     réussie au ledger de CE run (run StockPilot : claim entièrement fabriqué).
    overclaim_browser = (not has_browser_proof) and claims_browser_verified(final_text)

    # (3b) M100.4 — une action navigateur réussie plus tôt dans le run ne doit
    # pas masquer l'échec de la DERNIÈRE vérification runtime stricte. Ce signal
    # vient de browser_verify_local_project et prime sur la formulation du FINAL.
    note_browser_runtime_failed = bool(browser_runtime_failed)
    if note_browser_runtime_failed:
        # La bannière runtime est plus précise et évite une double bannière avec
        # l'ancien verrou fondé sur les claims.
        overclaim_browser = False

    # (4) A5 — claim de LIVRAISON avec ZÉRO mutation au ledger (run FitLog :
    #     w_storage « livré et validé ! » sans une seule écriture réussie).
    overclaim_delivery = (not has_any_mutation) and claims_artifact_delivery(final_text)

    # (4b) LOT E (run FidéliBar) — claim de PUBLICATION sans publish_mission_workspace
    #      réussi au ledger de CE run. « Publié ✅ dans workspace/x » / « succès
    #      complet livré » alors que la publication déterministe n'a jamais tourné.
    #      Preuve LEDGER (has_published), pas une devinette : publier ≠ écrire des
    #      fichiers (has_any_mutation peut être True sans qu'aucun publish n'ait eu lieu).
    overclaim_published = (not has_published) and claims_published(final_text)
    # H8 (TEST RÉEL n°3, mission `pyproject.toml` 2026-08-13) — le mémo DÉCRIVAIT
    # `pyproject.toml`, dont une section sert à « la publication sur PyPI » : le
    # mot est apparu dans le CONTENU DOCUMENTAIRE du livrable et a été lu comme
    # une revendication de publication. La mission PARLE de publication, elle n'en
    # revendique pas une.
    # Plutôt que de courir après la regex (course perdue d'avance, cf. M1), on pose
    # un fait déterministe : une mission d'EFFETS purs — contrat sans aucun `files` —
    # n'a RIEN à publier, `publish_mission_workspace` ne la concerne pas.
    # None (appelants existants, missions sans contrat) → strictement inchangé.
    if file_deliverables_expected is False:
        overclaim_published = False
    # LOT 2.12.C — DISK-GROUNDED symétrique (run monresto) : le lead a écrit le
    # livrable DIRECTEMENT dans workspace/X (existe non-vide sur disque) sans passer
    # par publish_mission_workspace → has_published=False, mais « Non publié » serait
    # un FAUX (le fichier est là, consultable). Si le disque confirme la cible nommée,
    # on éteint l'over-claim. project_root=None → inerte.
    if overclaim_published and project_root is not None \
            and published_target_present_on_disk(final_text, project_root):
        overclaim_published = False
    # LOT 2.11.E — durcissement DISK-GROUNDED (run StatsNotes) : même si le ledger
    # dit has_published=True (flag possiblement périmé/partagé entre missions), la
    # VÉRITÉ est l'existence disque. Si le final annonce « publié dans workspace/X »
    # et que X n'existe pas sous project_root → c'est une fausse publication.
    # project_root=None (appelants existants) → inerte, comportement inchangé.
    if (not overclaim_published) and project_root is not None \
            and published_target_missing_on_disk(final_text, project_root):
        overclaim_published = True

    # (5) A5 — des tests EXISTENT dans la mission mais AUCUN n'a tourné dans ce
    #     run : bannière déterministe (indépendante des claims — couvre les FINAL
    #     sortis par les voies de réparation qui contournaient le gate pytest).
    #     Si l'over-claim tests a déjà déclenché, la bannière honnête de tests le
    #     dit déjà → pas de doublon.
    note_tests_not_run = (
        bool(tests_present_not_run)
        and not (last_test_outcome or {}).get("is_test_cmd")
        and not (overclaim_tests or overclaim_verified)
    )

    # (6) B0.4b (run PlantCare) — un pytest a TOURNÉ dans ce run et n'était PAS
    #     vert → statut honnête DÉTERMINISTE, indépendant des claims (w_schedule :
    #     « Tests passés au vert » après un exit 4, formulation sans chiffres →
    #     hors regex). La vérité du ledger prime sur la formulation.
    note_tests_not_green = (
        _test_ran_not_green
        and not (overclaim_tests or overclaim_verified)
    )

    # (7) M1 (run RévizIA) — POLICY NAVIGATEUR DURE : livrable WEB dans la mission
    #     et AUCUNE action browser_* réussie au ledger → note honnête DÉTERMINISTE,
    #     indépendante des claims (« Test navigateur validé » — forme nominale hors
    #     regex — a livré une fabrication totale : serveur jamais lancé). Si
    #     l'over-claim navigateur (3) a déjà tiré, sa bannière le dit déjà → pas
    #     de doublon. Défaut web_deliverable=False → appelants existants intacts.
    note_browser_unverified = (
        bool(web_deliverable)
        and not has_browser_proof
        and not overclaim_browser
        and not note_browser_runtime_failed
    )

    # (8) LOT 2.3 (run MotDuJour) — claim « serveur lancé/app servie sur … » sans
    #     serve_website/start_preview_server réussi au ledger. `None` = preuve
    #     inconnue (appelants existants) → inchangé.
    overclaim_server = (has_server_started is False) and claims_server_started(final_text)

    # (9) 2.7.4 (run MiniPanier) — claim d'EFFET UI (« X apparaît/s'affiche »)
    #     dont le texte cité est ABSENT du contenu de page observé de ce run.
    #     `browser_content_seen=None` (appelants existants) → inerte. Mission web
    #     uniquement (web_deliverable).
    _dom_unobserved_tokens = (
        dom_claims_unobserved(final_text, browser_content_seen)
        if (web_deliverable and browser_content_seen is not None) else []
    )
    note_dom_unobserved = bool(_dom_unobserved_tokens)

    # (10) 2.12.D (run snake) — claim d'INTERACTION jouée (« jeu démarré / serpent
    #      redirigé / score augmente ») sans preuve `browser_evaluate` au ledger.
    #      `interaction_proven=True` par défaut (appelants existants + runs où
    #      l'interactif a bien été prouvé) → inerte. Mission web/jeu uniquement.
    #      2.13.A (run puissance4) — couche DÉTERMINISTE : l'OBJECTIF demandait un
    #      JEU web (`objective_is_game`, posé par le caller via
    #      objective_is_web_game) → bannière quel que soit le texte du final ; la
    #      regex sur le texte redevient un simple second filet.
    note_game_interaction_unproven = (
        web_deliverable and (not interaction_proven)
        and (bool(objective_is_game) or interaction_claims_unproven(final_text))
    )
    note_ui_interaction_unproven = (
        web_deliverable and bool(interaction_required) and (not interaction_proven)
        and not bool(objective_is_game)
        and not note_browser_unverified
        and not note_browser_runtime_failed
    )
    note_interaction_unproven = (
        note_game_interaction_unproven or note_ui_interaction_unproven
    )

    # (5) LOT Z24 — publier fige un instantané : ce qui s'écrit APRÈS est hors
    #     livrable tant qu'on ne republie pas. Fait DÉTERMINISTE (ledger + liste
    #     publiée), aucune lecture d'intention. Liste vide / None → inerte.
    _unpub = [str(n) for n in (unpublished_writes or []) if str(n).strip()]
    note_unpublished_writes = bool(_unpub)

    if not (overclaim_tests or overclaim_verified or overclaim_browser
            or overclaim_delivery or overclaim_published
            or note_tests_not_run or note_tests_not_green
            or note_browser_unverified or note_browser_runtime_failed or overclaim_server
            or note_dom_unobserved or note_interaction_unproven
            or note_unpublished_writes):
        return final_text, {"changed": False, "overclaim": False}

    # Bannières additives A5 (pas de neutralisation de texte : la bannière en
    # tête contredit explicitement le claim, preuve ledger à l'appui).
    _extra_banners = []
    if overclaim_delivery:
        _extra_banners.append(_NO_MUTATION_BANNER)
    if overclaim_published:
        _extra_banners.append(_NOT_PUBLISHED_BANNER)
    if note_browser_unverified:
        _extra_banners.append(_BROWSER_UNVERIFIED_BANNER)  # M1 policy dure
    if note_browser_runtime_failed:
        _extra_banners.append(_BROWSER_RUNTIME_FAILED_BANNER)  # M100.4
    if overclaim_server:
        _extra_banners.append(_SERVER_NOT_STARTED_BANNER)  # LOT 2.3
    if note_dom_unobserved:
        _extra_banners.append(_DOM_UNOBSERVED_BANNER)  # 2.7.4
    if note_interaction_unproven:
        _extra_banners.append(
            _INTERACTION_UNPROVEN_BANNER
            if note_game_interaction_unproven
            else _UI_INTERACTION_UNPROVEN_BANNER
        )
    if note_unpublished_writes:
        _extra_banners.append(_unpublished_writes_banner(_unpub))
    if note_tests_not_run:
        _extra_banners.append(_TESTS_NOT_RUN_BANNER)
    if note_tests_not_green:
        _extra_banners.append(_honest_test_status_line(last_test_outcome))

    neutralized = final_text
    if overclaim_tests:
        neutralized = _neutralize_test_claims(neutralized)
    if overclaim_verified:
        neutralized = _neutralize_verified_claims(neutralized)
    if overclaim_browser:
        neutralized = _neutralize_browser_claims(neutralized)
    if not (overclaim_tests or overclaim_verified):
        # Pas d'over-claim de tests : bannières ciblées uniquement.
        _banners = list(_extra_banners)
        if overclaim_browser:
            _banners.insert(0, _BROWSER_BANNER)
        return ("\n\n".join(_banners) + f"\n\n{neutralized}",
                {"changed": True,
                 "overclaim": bool(overclaim_browser or overclaim_delivery
                                   or overclaim_published),
                 "overclaim_browser": overclaim_browser,
                 "overclaim_delivery": overclaim_delivery,
                 "overclaim_published": overclaim_published,
                 "tests_not_run_note": note_tests_not_run,
                 "tests_not_green_note": note_tests_not_green,
                 "browser_unverified_note": note_browser_unverified,
                  "browser_runtime_failed_note": note_browser_runtime_failed,
                  "overclaim_server": overclaim_server,
                  "dom_unobserved_note": note_dom_unobserved,
                  "interaction_unproven_note": note_interaction_unproven,
                      "unpublished_writes_note": note_unpublished_writes})
    banner = _honest_test_status_line(last_test_outcome)
    if overclaim_browser:
        banner += "\n\n" + _BROWSER_BANNER
    for _eb in _extra_banners:
        banner += "\n\n" + _eb
    # Pièce 3 — config/contournement de tests invoqué (pytest.ini, conftest.py,
    # --ignore…) : sous une certification non prouvée, il est lui aussi non
    # probant. On le dit plutôt que de laisser croire qu'il « fait passer » les
    # tests. (Pas de croisement ledger : les fichiers écrits par les sous-agents
    # n'y figurent pas côté lead → faux « fantômes ».)
    cited = _cited_test_config(final_text)
    if cited or (last_test_outcome or {}).get("used_invented_ignore"):
        parts = sorted(cited)
        if (last_test_outcome or {}).get("used_invented_ignore"):
            parts.append("--ignore")
        banner += (
            "\n\n> ⚠️ Toute config/contournement de tests cité"
            f" ({', '.join(dict.fromkeys(parts))}) n'est PAS une preuve : "
            "la portée réelle des tests reste non certifiée."
        )
    # Bannière EN TÊTE (vue en premier = honnêteté maximale).
    new_text = f"{banner}\n\n{neutralized}"
    return new_text, {"changed": True, "overclaim": True,
                      "overclaim_tests": overclaim_tests,
                      "overclaim_verified": overclaim_verified,
                      "overclaim_browser": overclaim_browser,
                      "overclaim_published": overclaim_published,
                      "browser_unverified_note": note_browser_unverified,
                      "browser_runtime_failed_note": note_browser_runtime_failed,
                      "overclaim_server": overclaim_server,
                      "dom_unobserved_note": note_dom_unobserved,
                      "interaction_unproven_note": note_interaction_unproven,
                      "unpublished_writes_note": note_unpublished_writes}


_TEST_CONFIG_RE = re.compile(
    r"\b(pytest\.ini|conftest\.py|setup\.cfg|tox\.ini|pyproject\.toml)\b",
    re.IGNORECASE,
)


def _cited_test_config(text: str) -> set:
    """Fichiers de config de tests cités dans le FINAL (pytest.ini, conftest.py…)."""
    if not text:
        return set()
    return {m.group(1).lower() for m in _TEST_CONFIG_RE.finditer(text)}
