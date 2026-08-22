"""mission_contract.py — Contrat machine + stubs pour les missions multi-workers (LOT 2.2).

PUR, stdlib uniquement. AUCUN import de react/handlers/CodeAgent → aucun cycle,
zéro toucher à `src/agents/sub_agent.py` (doctrine LOT 2 : on RECRÉE autour des
missions les garanties du CodeAgent, on ne l'extrait pas).

Raison d'être (runs PollApp 2026-07-02) : un contrat en PROSE transmis aux workers
ne contraint rien — `test_api.py` importait `reset_options` que le worker `app.py`
n'a jamais défini. Un worker LLM isolé paraphrase une prose ; il ne dérive pas
d'une SIGNATURE FIGÉE présente dans un stub qu'il doit REMPLIR.

Chaîne : le lead fournit un contrat machine → `validate_contract` → stubs réels
(`generate_stub`, signatures exactes, corps TODO) + `CONTRAT.md` lisible
(`render_contract_md`) → objectifs structurés `{text, allowed_files}` prêts pour
`delegate_and_wait` (`worker_objectives`) → le périmètre est APPLIQUÉ par 2.3.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

CONTRACT_JSON = "contract.json"
CONTRACT_MD = "CONTRAT.md"

# C0.6 (run FrigoZen) — nom de projet lisible dérivé de l'objectif de mission.
# Le 2e appel de contrat (retry) n'avait pas repassé `project` → contract.json
# sans nom → publication fallback `livrable_905eee42` (illisible pour l'humain).
# Heuristique volontairement stricte : premier token CamelCase (≥ 2 bosses),
# la forme quasi systématique des noms de produits demandés (FrigoZen,
# BudgetBuddy, StockPilot, FitLog, PlantCare…). Aucun match → "".
_CAMEL_NAME_RE = re.compile(r"\b[A-ZÀ-Þ][a-zà-ÿ0-9]+(?:[A-ZÀ-Þ][A-Za-zà-ÿ0-9]*)+\b")


def derive_project_name(text: str) -> str:
    """Premier nom CamelCase de l'objectif (« FrigoZen ») ou "". Pur/testable."""
    m = _CAMEL_NAME_RE.search(str(text or ""))
    return m.group(0) if m else ""

# Consigne injectée dans l'objectif de CHAQUE worker quand un contrat existe.
# A1 (run FitLog) : chemins RELATIFS uniquement — un worker qui recopie
# missions/<id>/ finit par halluciner l'identifiant (afee→af1e) et se désaxe.
WORKER_CONTRACT_PREAMBLE = (
    "📜 CONTRAT DE MISSION : lis d'abord CONTRAT.md (tu es DÉJÀ dans le dossier "
    "de la mission — utilise UNIQUEMENT des chemins RELATIFS comme CONTRAT.md ou "
    "storage.py, ne recopie JAMAIS missions/<id>/ ni workspace/). "
    "Tes fichiers existent déjà en STUBS avec les signatures EXACTES : REMPLIS-les "
    "via edit_file/apply_patch (pas de réécriture totale), NE modifie JAMAIS une "
    "signature, n'invente AUCUNE API hors contrat. N'écris QUE tes fichiers assignés."
)


# ── LOT G (run FidéliBar 2026-07-04) — discipline de codage du worker ─────────────
# Le worker recevait le CONTRAT (préambule ci-dessus) mais pas la discipline de DEV
# que le CodeAgent a déjà dans ses prompts (src/prompts/agents/sub_agent_prompts.py).
# Symptômes FidéliBar : dizaines de read_file en boucle, endpoints inventés
# (/api/clients vs /api/customers), IDs incohérents (search-btn vs btn-search),
# worker de tests qui mocke la logique produit pour verdir un bug. Ce bloc est
# AGNOSTIQUE du provider (le tuning modèle vit ailleurs : P5 model_profile +
# codeagent/*.txt) — un seul bloc commun, PAS de matrice provider×worker.
WORKER_CODING_DISCIPLINE = (
    "🛠️ DISCIPLINE DE CODAGE (agent dev, pas un rédacteur) :\n"
    "• Budget d'exploration : 3 lectures MAX avant une première mutation ; après une "
    "mutation, relis SEULEMENT la zone utile — jamais le fichier entier en boucle.\n"
    "• Édite petit : edit_file/apply_patch ciblés, ne réécris pas un fichier entier "
    "pour changer quelques lignes.\n"
    "• Après CHAQUE mutation significative, EXÉCUTE (module/tests concernés) avant de "
    "conclure — ne devine pas que « ça marche ».\n"
    "• Si un test OU le contrat contredit ton code → corrige TON code, JAMAIS le test "
    "ni le contrat.\n"
    "• Ne conclus PAS (FINAL) sans une mutation RÉELLE et réussie dans ton périmètre ; "
    "n'annonce « fini / vérifié / publié » que si c'est prouvé par une exécution."
)

_RIDER_FRONTEND = (
    "🎨 FRONTEND : les id/class du HTML DOIVENT correspondre EXACTEMENT aux "
    "getElementById/querySelector du JS ; les URL de fetch() du JS DOIVENT "
    "correspondre EXACTEMENT aux routes exposées par le backend (cf. contrat) ; pas "
    "de addEventListener en double sur le même élément.\n"
    # LOT Z1 — cette consigne nommait le JS et le backend, jamais le CSS. Le worker
    # JS ouvrait donc index.html pour relever les id (le tri marche à tous les
    # coups) ; le worker CSS ne l'ouvrait jamais et inventait son vocabulaire.
    # Mesuré : fibrance 3/15 classes stylées (20 %), cadran 2/5 (40 %) — tuiles
    # sans fond ni bordure, menu en puces. Là où un SEUL agent écrit tout, le même
    # travail donne 95 à 100 %. Le rail existait, il s'arrêtait une case trop tôt.
    "🎨 STYLE : AVANT d'écrire du CSS, LIS le fichier HTML et relève ses `class` "
    "réelles. Chaque sélecteur `.maclasse` de ton CSS DOIT exister tel quel dans le "
    "HTML — pas un synonyme (`.stats-tile` pour `.stat-card`, `.gallery-grid` pour "
    "`.gallery`). Un CSS dont les classes ne se rencontrent pas produit une page "
    "qui s'affiche mais ne ressemble à rien."
)

_RIDER_BACKEND = (
    "🔌 BACKEND : les routes publiques que tu exposes DOIVENT correspondre à l'API "
    "partagée du contrat (mêmes chemins, mêmes noms) ; si le contrat dit qu'un état "
    "est persisté, PERSISTE-le réellement (pas d'état en mémoire perdu entre deux "
    "appels)."
)

_RIDER_TESTS = (
    "🧪 TESTS : teste le COMPORTEMENT du contrat. Ne mocke JAMAIS la logique produit "
    "pour faire passer un test ; les mocks sont autorisés UNIQUEMENT pour le réseau, "
    "le temps, ou les I/O externes. Si un test échoue à cause d'un bug produit hors "
    "de ton périmètre, REMONTE-le — ne le masque pas."
)


# LOT I.4-min (run PostuloTrack) — le worker de mission EST une Lumena complète, mais on
# le forçait à coder à la main (deepseek en ReAct brut → narre au lieu d'écrire → échec).
# La vraie Lumena, elle, délègue le code au CodeAgent (harnais prouvé). On rend ce réflexe
# au worker : pour écrire du CODE, il délègue via delegate_task → CodeAgent (borné à SES
# fichiers par le périmètre I.2, servi dans le dossier mission par I.1). Repli hand-coding.
_DELEGATE_CODE_STEER = (
    "⚙️ CODE PAR DÉLÉGATION : pour ÉCRIRE/REMPLIR tes fichiers de code, délègue au CodeAgent "
    "via delegate_task(description='remplis <ton fichier> selon CONTRAT.md', agent_type='code') "
    "— il code dans le dossier mission, borné à TES fichiers, puis te rend le résultat. Tu "
    "restes responsable de VÉRIFIER (lance pytest) et de conclure. Repli UNIQUEMENT si la "
    "délégation échoue : édite toi-même (edit_file/apply_patch)."
)

_CODE_EXT = (".py", ".html", ".css", ".js", ".ts", ".jsx", ".tsx", ".vue", ".svelte")


def design_brief_for_contract(data: Any) -> str:
    """LOT Z13 — la DIRECTION ARTISTIQUE que le contrat doit transmettre.

    Lumena possède `generate_website`, qui produit des sites « niveau agence » à
    partir de `build_design_directives()` : palette validée WCAG 2.1 AA, variables
    CSS complètes (ombres, rayons, transitions), typographie choisie. Le skill
    `frontend-design` route d'ailleurs explicitement vers cet outil.

    **Une mission ne peut pas s'en servir.** Le contrat fige `styles.css` comme un
    fichier assigné à un propriétaire ; `generate_website` produirait un site
    entier, incompatible avec un périmètre d'un seul fichier. Mesuré sur le run
    Fournil : `frontend-design` chargé (41.5), `theme-factory` (47.5),
    `generate_website` **zéro appel**. Le worker CSS improvise.

    Ce n'est pas un bug : c'est une conséquence non voulue du contrat, qui a
    résolu la cohérence entre workers en excluant tout outil produisant un
    ensemble d'un seul coup. Z13 ne casse pas le contrat — il lui fait porter la
    direction, en appelant la même fonction pure que le générateur.

    ⚠️ La détection de domaine a besoin de TOUTES les descriptions : mesuré sur
    Fournil, `project` seul donne « E-commerce Green », `project` + la desc du CSS
    donne « AI/Chatbot Purple », et l'agrégat complet donne **« Bakery Warm
    Brown »** — la bonne. Les mots utiles (« boulangerie », « viennoiseries »)
    vivent dans les descriptions des PAGES, pas dans celle du style.

    Pur au sens du contrat (aucune I/O) ; rend "" si la génération est
    indisponible ou échoue — l'injection est alors strictement inerte.
    """
    if not isinstance(data, dict):
        return ""
    morceaux: List[str] = [str(data.get("project") or "")]
    for entree in (data.get("files") or []):
        if isinstance(entree, dict):
            morceaux.append(_entry_desc(entree))
    for entree in (data.get("effects") or []):
        if isinstance(entree, dict):
            morceaux.append(str(entree.get("desc") or entree.get("description") or ""))
    sujet = " ".join(m for m in morceaux if m).strip()
    if not sujet:
        return ""
    try:
        from src.tools.website_builder import build_design_directives

        return str(build_design_directives(sujet) or "").strip()
    except Exception:
        # Génération indisponible (import, dépendance) → contrat inchangé.
        return ""


def _owns_stylesheet(mine: List[str]) -> bool:
    """Ce worker porte-t-il la feuille de style ? LOT Z13.

    Les directives font ~3400 caractères : les donner à TOUS les workers frontend
    noierait les prompts de celui qui écrit le HTML ou le JS. Seul le propriétaire
    du CSS décide de la direction visuelle — c'est lui, et lui seul, qui la reçoit.
    """
    import os as _os

    return any(_os.path.basename(str(p)).lower().endswith(".css") for p in (mine or []))


def _role_rider(mine: List[str], design_brief: str = "") -> str:
    """Riders de codage ciblés selon le RÔLE déduit des extensions des fichiers du
    worker (`mine`). Un worker mixte (ex. app.py + templates) cumule les riders.
    Retourne "" si aucun rôle reconnu (zéro bruit).

    LOT Z13 — `design_brief` s'ajoute au rider frontend UNIQUEMENT pour le
    propriétaire de la feuille de style. Vide (défaut) → comportement historique
    strictement inchangé."""
    import os as _os
    is_frontend = is_backend = is_test = False
    for p in mine:
        b = _os.path.basename(str(p)).lower()
        if b.endswith((".html", ".css", ".js")):
            is_frontend = True
        elif b.endswith(".py"):
            if b.startswith("test_") or b.endswith("_test.py"):
                is_test = True
            else:
                is_backend = True
    riders: List[str] = []
    if is_frontend:
        riders.append(_RIDER_FRONTEND)
        if design_brief and _owns_stylesheet(mine):
            riders.append(str(design_brief))
    if is_backend:
        riders.append(_RIDER_BACKEND)
    if is_test:
        riders.append(_RIDER_TESTS)
    return ("\n" + "\n".join(riders)) if riders else ""


# ── LOT A (run PostuloTrack 2026-07-05) — la discipline DOIT atteindre le worker ──
# Le lead RÉÉCRIT souvent les objectifs de delegate_and_wait de zéro (dérive d'objectifs)
# → la discipline G, le steer de délégation I.4 et les riders, qui ne vivaient QUE dans
# `worker_objectives()`, se perdaient (le worker recevait le texte libre du lead). On
# extrait un bloc réutilisable, FORCE-injecté à la délégation (missions.py), hors de
# portée de la réécriture du lead. Marqueur d'idempotence = "DISCIPLINE DE CODAGE".
_DISCIPLINE_MARKER = "DISCIPLINE DE CODAGE"


def _has_code_files(paths: Any) -> bool:
    """True si le worker possède au moins un fichier de CODE (extensions _CODE_EXT)."""
    return any(str(p).lower().endswith(_CODE_EXT) for p in (paths or []))


def worker_discipline_block(allowed_files: Any, design_brief: str = "") -> str:
    """Bloc discipline réutilisable : `WORKER_CODING_DISCIPLINE` (toujours) + steer de
    délégation CodeAgent (`_DELEGATE_CODE_STEER`, si code) + rider(s) de rôle. Commence
    toujours par le marqueur d'idempotence. Utilisé par `worker_objectives()` ET
    force-injecté à la délégation par LOT A."""
    parts = [WORKER_CODING_DISCIPLINE]
    if _has_code_files(allowed_files):
        parts.append(_DELEGATE_CODE_STEER)
    return "\n".join(parts) + _role_rider(list(allowed_files or []), design_brief)


def inject_worker_discipline(text: str, allowed_files: Any) -> str:
    """LOT A — ajoute le bloc discipline à un objectif worker s'il est ABSENT (idempotent)
    et que le worker a du CODE. C'est le rempart contre la dérive d'objectifs : quoi que
    le lead écrive, le worker de code reçoit la discipline + le steer de délégation.
    Objectif généré (déjà le marqueur) ou worker non-code → texte inchangé."""
    if not _has_code_files(allowed_files):
        return text
    if _DISCIPLINE_MARKER in (text or ""):
        return text
    return (text or "") + "\n\n" + worker_discipline_block(allowed_files)


# ── validation ──────────────────────────────────────────────────────────────────

def parse_contract(raw: Any) -> Tuple[Dict[str, Any], List[str]]:
    """Accepte dict OU chaîne JSON (le LLM passe souvent du JSON sérialisé).
    Retourne (contrat, erreurs) — erreurs non vides = inutilisable."""
    if isinstance(raw, dict):
        return raw, []
    if isinstance(raw, str):
        s = raw.strip()
        try:
            data = json.loads(s)
        except (ValueError, TypeError):
            try:
                import ast
                data = ast.literal_eval(s)
            except (ValueError, SyntaxError):
                return {}, ["contract illisible : ni JSON ni littéral Python valide."]
        if isinstance(data, dict):
            return data, []
        return {}, ["contract doit être un objet {project, files:[...]}."]
    return {}, ["contract requis (objet ou chaîne JSON)."]


def _is_safe_rel_path(p: str) -> bool:
    s = (p or "").strip().replace("\\", "/")
    if not s or s.startswith("/") or (len(s) >= 2 and s[1] == ":"):
        return False
    return ".." not in s.split("/")


# LOT Z18 — segments que le dossier de mission fournit DÉJÀ. Les répéter dans un
# path de contrat crée un sous-dossier fantôme (`missions/<id>/workspace/…`) que
# le résolveur des workers rend vers le workspace GLOBAL — donc introuvable.
_MISSION_RESERVED_HEADS: frozenset = frozenset({"workspace", "missions"})


def _mission_reserved_prefix(p: str) -> bool:
    """Le chemin commence-t-il par un segment réservé ?

    On ne teste que le PREMIER segment : `static/workspace.css` et
    `app/missions.py` sont parfaitement légitimes — 174 chemins du disque sont
    des sous-dossiers valides et doivent le rester.
    """
    s = str(p or "").strip().replace("\\", "/").lstrip("/")
    tete = s.split("/", 1)[0].lower() if "/" in s else ""
    return tete in _MISSION_RESERVED_HEADS


def _mission_strip_reserved(p: str) -> str:
    """Le chemin corrigé à proposer au lead — un refus qui ne dit pas quoi écrire
    coûte une itération pour rien. `missions/<id>/app.py` → `app.py`."""
    s = str(p or "").strip().replace("\\", "/").lstrip("/")
    while "/" in s and s.split("/", 1)[0].lower() in _MISSION_RESERVED_HEADS:
        s = s.split("/", 1)[1]
        # `missions/<id>/…` : l'identifiant de tâche suit le segment réservé.
        if "/" in s and s.split("/", 1)[0].lower().startswith("task_"):
            s = s.split("/", 1)[1]
    return s or p


# ── Normalisation des clés (run BudgetBuddy 2026-07-03) ─────────────────────────
# Le vocabulaire SPONTANÉ des modèles est `exports`/`imports`/`description` (+
# `shared_api` top-level) — le module ne lisait que `api`/`desc` → CONTRAT.md sans
# une seule signature et stubs .py VIDES (l'anti-dérive était une coquille). On
# normalise via des getters purs ; l'ancien format `api`/`desc` reste identique.

def _entry_api(entry: Dict[str, Any]) -> List[str]:
    """Signatures du fichier : `api` | `exports` | `signatures`."""
    v = entry.get("api") or entry.get("exports") or entry.get("signatures")
    if not isinstance(v, (list, tuple)):
        return []
    return [str(a).strip() for a in v if str(a).strip()]


def _is_real_signature(sig: str) -> bool:
    """F.1 (run RéservaSalle 2026-07-04) — un export .py est une VRAIE signature
    (produit un stub fonctionnel) ou une constante de module ; PAS un nom nu.

    Un nom nu (`get_all`) tombe dans la branche « variable » de `_py_stub` et
    génère `get_all  # SIGNATURE FIGÉE` — un stub non-fonctionnel où le worker
    n'a rien à remplir, donc réinvente sa propre API (run RéservaSalle : 5 stubs
    vides → chevauchement jamais implémenté, signatures divergentes)."""
    s = (sig or "").strip()
    if s.startswith(("def ", "async def ", "class ")):
        return True
    # Constante de module annoncée par le contrat : `NOM = valeur` (stub Python
    # valide). Le `=` distingue une affectation d'un nom nu.
    return "=" in s


_JS_EXT = (".js", ".jsx", ".ts", ".tsx", ".mjs")

# M1-clôture (run MiniQuiz 2026-07-06) — les contrats portent des signatures JS
# HYBRIDES (`function load_question() -> void`, `submit_answer(answer: str)`) que
# `_js_stub` copiait littéralement en tête d'un bloc « SIGNATURE FIGÉE — NE PAS
# MODIFIER » : le CodeAgent remplissait les corps en GARDANT la signature invalide
# → `node --check static/script.js` rouge, livrable web mort-né.
_JS_SIG_RE = re.compile(
    r"^(?P<prefix>async\s+)?(?:function\s+)?(?P<name>[A-Za-z_$][\w$]*)\s*\((?P<args>[^()]*)\)$"
)
_JS_ARROW_RE = re.compile(
    r"^(?:const|let|var)\s+[A-Za-z_$][\w$]*\s*=\s*(?:async\s*)?\([^()]*\)\s*=>"
)


def _normalize_js_signature(sig: str) -> str:
    """Signature JS VALIDE (`function name(args)`) dérivée d'une forme hybride,
    ou "" si impossible (nom nu, syntaxe exotique). Pur/testable.

    Accepte : `function f(a)`, `f(a)`, `async function f(a)`,
    `function f(a) -> type` (annotation retour supprimée),
    `f(answer: str, n: int = 3)` (annotations de params supprimées, défauts gardés).
    Une arrow-function `const f = (a) => ...` est gardée telle quelle si déjà valide.
    """
    s = str(sig or "").strip().rstrip("{").rstrip().rstrip(";").rstrip()
    if not s:
        return ""
    if _JS_ARROW_RE.match(s):
        # forme flèche déjà valide (`const f = (a) =>` + bloc {} du stub = JS légal)
        return s
    # annotation de retour Python-esque : `... -> void` / `-> dict {a, b}`
    s = re.sub(r"\s*->\s*[^()]*$", "", s).rstrip()
    m = _JS_SIG_RE.match(s)
    if not m:
        return ""
    args: List[str] = []
    for raw_arg in m.group("args").split(","):
        a = raw_arg.strip()
        if not a:
            continue
        # `answer: str` / `n: int = 3` → `answer` / `n = 3`
        am = re.match(r"^([A-Za-z_$][\w$]*)\s*:\s*[^=]+?(\s*=\s*.+)?$", a)
        if am:
            a = am.group(1) + (am.group(2) or "")
        args.append(a)
    prefix = "async " if m.group("prefix") else ""
    return f"{prefix}function {m.group('name')}({', '.join(args)})"


def _entry_desc(entry: Dict[str, Any]) -> str:
    """Description du fichier : `desc` | `description`."""
    return str(entry.get("desc") or entry.get("description") or "").strip()


def _entry_imports(entry: Dict[str, Any]) -> List[str]:
    """Imports inter-fichiers IMPOSÉS par le contrat (lignes exactes)."""
    v = entry.get("imports")
    if not isinstance(v, (list, tuple)):
        return []
    return [str(a).strip() for a in v if str(a).strip()]


def _is_test_path(p: str) -> bool:
    base = (p or "").replace("\\", "/").rsplit("/", 1)[-1].lower()
    return base.startswith("test_") or base.endswith("_test.py")


def _py_api_exempt(entry: Dict[str, Any]) -> bool:
    """Un .py peut légitimement n'exposer AUCUNE API : fichier de test (il en
    consomme), `__init__.py`, ou porte EXPLICITE `no_public_api`/`internal`."""
    path = str(entry.get("path") or "")
    base = path.replace("\\", "/").rsplit("/", 1)[-1].lower()
    return (_is_test_path(path) or base == "__init__.py"
            or bool(entry.get("no_public_api")) or bool(entry.get("internal")))


def _effect_key(e: Dict[str, Any]) -> str:
    """Identité d'un effet : action + cible, normalisées. Sert à détecter les
    doublons entre owners (deux workers qui enverraient le MÊME mail)."""
    action = str(e.get("action") or "").strip().lower()
    target = str(e.get("target") or "").strip().lower()
    return f"{action}::{target}"


def _validate_effects(effects: Any) -> List[str]:
    """H4 — erreurs sur `contract.effects` ([] = valide ou absent).

    Un effet, c'est ce qu'un worker doit FAIRE quand le livrable n'est pas un
    fichier : envoyer le mail, poster sur Slack, déployer le site, réserver la
    salle, écrire en mémoire. Même exigence que pour les fichiers — un owner
    unique, une description qui engage, et surtout une PREUVE nommée : sans
    preuve attendue, la clôture retombe sur le récit du modèle, c'est-à-dire
    exactement la faille que tout ce chantier ferme.
    """
    errors: List[str] = []
    if effects is None:
        return errors
    if not isinstance(effects, list):
        return ["contract.effects doit être une liste de "
                "{owner, action, desc, target?, proof}."]
    seen: Dict[str, str] = {}
    for i, e in enumerate(effects):
        if not isinstance(e, dict):
            errors.append(f"effects[{i}] doit être un objet "
                          "{owner, action, desc, proof}.")
            continue
        if not str(e.get("owner") or "").strip():
            errors.append(f"effects[{i}].owner requis (nom du worker qui réalisera "
                          "cet effet).")
        if not str(e.get("action") or "").strip():
            errors.append(
                f"effects[{i}].action requis : le VERBE de l'effet, ex. "
                "'envoyer_email', 'poster_slack', 'deployer_site', 'reserver', "
                "'ecrire_memoire'."
            )
        if not str(e.get("desc") or "").strip():
            errors.append(
                f"effects[{i}].desc requis : ce qui doit être accompli, assez "
                "précis pour qu'un autre worker puisse le vérifier."
            )
        if not str(e.get("proof") or "").strip():
            errors.append(
                f"effects[{i}].proof requis : à QUOI on verra que c'est fait, "
                "ex. 'id du message Slack', 'accusé d'envoi du mail', 'URL "
                "publique qui répond 200', 'entrée mémoire relisible'. Un effet "
                "sans preuve attendue ne peut pas être clôturé autrement qu'en "
                "croyant le worker sur parole."
            )
        key = _effect_key(e)
        owner = str(e.get("owner") or "").strip()
        if key != "::" and key in seen and seen[key] != owner:
            errors.append(
                f"effects[{i}] '{e.get('action')}' sur '{e.get('target')}' est "
                f"déjà porté par '{seen[key]}' — un effet = UN SEUL owner, sinon "
                "l'action est exécutée deux fois (mail envoyé en double, "
                "réservation dupliquée)."
            )
        elif key != "::":
            seen.setdefault(key, owner)
    return errors


def validate_contract(data: Dict[str, Any]) -> List[str]:
    """Erreurs de structure, claires et actionnables ([] = valide)."""
    errors: List[str] = []
    if not isinstance(data, dict):
        return ["contract doit être un objet."]
    # H4 — un contrat peut décrire des FICHIERS, des EFFETS, ou les deux.
    # 82 % des outils de Lumena ne produisent aucun fichier (mail, Slack, IONOS,
    # n8n, navigateur, MCP…). Exiger `files` rendait toute mission d'effets purs
    # incontractualisable — donc sans périmètre, sans owners et sans coordination.
    files = data.get("files")
    effects = data.get("effects")
    _has_files = isinstance(files, list) and files
    _has_effects = isinstance(effects, list) and effects
    if not _has_files and not _has_effects:
        errors.append(
            "contract.files et/ou contract.effects requis : `files` = liste de "
            "{path, owner, api?, desc?} pour les livrables FICHIERS ; `effects` = "
            "liste de {owner, action, desc, target?, proof?} pour les livrables "
            "d'ACTION (mail envoyé, message posté, site déployé, réservation…). "
            "Une mission sans fichier reste contractualisable."
        )
        return errors
    errors.extend(_validate_effects(effects))
    if not _has_files:
        return errors  # contrat d'effets purs : rien à valider côté fichiers
    seen_paths = set()
    for i, f in enumerate(files):
        if not isinstance(f, dict):
            errors.append(f"files[{i}] doit être un objet {{path, owner, ...}}.")
            continue
        path = str(f.get("path") or "").strip()
        if not path:
            errors.append(f"files[{i}].path requis.")
        elif not _is_safe_rel_path(path):
            errors.append(f"files[{i}].path '{path}' invalide : chemin RELATIF au "
                          "dossier de mission, sans '..' ni absolu.")
        elif _mission_reserved_prefix(path):
            # ── LOT Z18 — le contrat s'interdisait ce qu'il interdit aux workers ──
            # Run « Pelage » (2026-08-16) : le lead a écrit
            # `workspace/pelage/donnees.js` au lieu de `donnees.js`. Chemin relatif
            # sans `..`, donc ACCEPTÉ. Les stubs sont partis dans
            # `missions/<id>/workspace/pelage/`, et les workers — à qui le prompt
            # dit pourtant « ne recopie JAMAIS missions/<id>/ ni workspace/ » — ont
            # dû écrire `workspace/…`, que leur résolveur rend vers le workspace
            # GLOBAL. Introuvable, toujours.
            #
            # Coût mesuré : ~75 itérations passées à chercher des fichiers, un
            # worker tué par le PLAN GUARD après 16 tours sans progression, le
            # CodeAgent réduit à `cmd /c copy` puis `shutil.copy` (4 fichiers créés
            # hors périmètre), et AUCUNE publication en 33 minutes.
            #
            # Rare mais total : sur les 447 chemins des 100 contrats du disque,
            # 5 seulement sont fautifs — les 5 de Pelage. Les 174 sous-dossiers
            # légitimes (`tests/`, `static/`…) restent valides : on ne regarde que
            # le PREMIER segment, donc `static/workspace.css` passe.
            _propre = _mission_strip_reserved(path)
            errors.append(
                f"files[{i}].path '{path}' : un chemin de contrat est relatif au "
                f"DOSSIER DE MISSION — il ne doit jamais commencer par "
                f"`workspace/` ni `missions/`. Le dossier de mission EST déjà le "
                f"workspace des workers ; ce préfixe crée un sous-dossier fantôme "
                f"que personne ne retrouve ensuite. Écris : '{_propre}'."
            )
        elif path.replace("\\", "/") in seen_paths:
            # 2.8.1 (run MotCompteur) — message GUIDANT : le lead avait mis cli.py
            # 2× (un owner pour count_words, un pour l'entry-point). Le sec « en
            # double » ne dit pas quoi faire → il a tourné en rond puis abandonné.
            errors.append(
                f"files[{i}].path '{path}' en double — un fichier = UN SEUL owner. "
                "Mets tout le contenu de ce fichier sous un seul owner (une fonction "
                "publique + un point d'entrée peuvent cohabiter dans le même .py), "
                "OU sépare en 2 fichiers distincts (ex. core.py + cli.py)."
            )
        else:
            seen_paths.add(path.replace("\\", "/"))
        if not str(f.get("owner") or "").strip():
            errors.append(f"files[{i}].owner requis (nom du worker qui écrira ce fichier).")
        for key in ("api", "exports", "signatures", "imports"):
            v = f.get(key)
            if v is not None and not isinstance(v, list):
                errors.append(f"files[{i}].{key} doit être une liste (chaînes).")
        # Durcissement (run BudgetBuddy) : un .py NON-test sans signatures = le
        # danger exact qui a vidé les stubs → erreur ACTIONNABLE. Portes explicites :
        # tests, __init__.py, ou `no_public_api`/`internal: true`.
        if (path and path.lower().endswith(".py") and not _py_api_exempt(f)):
            apis = _entry_api(f)
            if not apis:
                # 2.8.1 (run MotCompteur) — un point d'entrée CLI (`cli.py`,
                # `main.py`, `__main__.py`, `run.py`) n'a souvent AUCUNE API
                # publique réutilisable : le message met `no_public_api` EN TÊTE
                # pour ce cas (au lieu de le noyer en fin), sinon le lead itère
                # 3× sans trouver et la mission abandonne.
                _base = path.replace("\\", "/").rsplit("/", 1)[-1].lower()
                _looks_cli = _base in ("cli.py", "main.py", "__main__.py", "run.py") or "cli" in _base
                if _looks_cli:
                    errors.append(
                        f"files[{i}] '{path}' ressemble à un point d'entrée CLI sans "
                        "API publique réutilisable → ajoute `no_public_api: true` sur "
                        "cette entrée du contrat (c'est la porte prévue pour exactement "
                        "ce cas). Si au contraire il expose une fonction importable, "
                        "déclare-la dans `exports: ['def …']`."
                    )
                else:
                    errors.append(
                        f"files[{i}] '{path}' : un fichier .py doit déclarer ses signatures "
                        "(api/exports: ['def …']) — c'est l'objet même du contrat. Si ce "
                        "fichier n'a VRAIMENT aucune API publique (point d'entrée, script), "
                        "pose `no_public_api: true`."
                    )
            else:
                # F.1 (run RéservaSalle) : refuser les NOMS NUS — ils génèrent des
                # stubs vides (`get_all  # …`) où le worker doit tout réinventer.
                bad = [s for s in apis if not _is_real_signature(s)]
                if bad:
                    errors.append(
                        f"files[{i}] '{path}' : exports {bad} ne sont pas des signatures. "
                        "Écris des signatures COMPLÈTES avec def, ex. "
                        "'def add(nom: str, capacite: int) -> dict' (ou 'NOM = valeur' pour "
                        "une constante). Un nom nu produit un stub NON-fonctionnel où le "
                        "worker réinvente sa propre API → dérive garantie."
                    )
        # I2 (run comparatif vectoriel 2026-08-13) — un livrable RÉDACTIONNEL qui
        # déclare des signatures de fonction : le worker écrit alors du CODE dans
        # un document. `comparatif_bases_vectorielles.md` a été livré rempli de
        # `def generer_tableau(...)` et de `from rapport_chromadb import …`, et le
        # lead a dû créer un fichier HORS contrat pour livrer le vrai tableau.
        if path and path.lower().endswith(_DOC_EXT):
            _code_like = [
                s for s in _entry_api(f)
                if re.match(r"^\s*(?:async\s+def|def|class|function|import|from)\s",
                            str(s))
            ]
            if _code_like:
                errors.append(
                    f"files[{i}] '{path}' est un document, pas du code : "
                    f"{_code_like} sont des signatures de fonction. Un document "
                    "n'expose aucune API — décris son CONTENU dans `desc`, et "
                    "utilise `exports` (facultatif) pour lister ses SECTIONS "
                    "attendues en clair (ex. ['Tableau comparatif', 'Sources', "
                    "'Méthode']). Sinon le worker écrit du code dans un .md."
                )
        # M1-clôture (run MiniQuiz) : un export JS doit être NORMALISABLE en
        # `function name(args)` — sinon le stub généré ne compile pas et le
        # worker le fige (« SIGNATURE FIGÉE — NE PAS MODIFIER »).
        if path and path.lower().endswith(_JS_EXT):
            bad_js = [s for s in _entry_api(f) if not _normalize_js_signature(s)]
            if bad_js:
                errors.append(
                    f"files[{i}] '{path}' : exports {bad_js} ne sont pas des signatures "
                    "JS exploitables. Écris une signature de fonction COMPLÈTE, ex. "
                    "'function load_question()' ou 'function submit_answer(answer)'. "
                    "Un nom nu produit un stub qui ne compile pas."
                )
        # LOT 2.8 (run Converto 2026-07-06) — le lead avait mis un champ
        # `signature` (hors vocabulaire) : le contrat AVAIT L'AIR riche mais le
        # système l'ignorait en silence → CONTRAT.md vide de sens. Erreur
        # GUIDANTE spécifique (revue utilisateur) plutôt qu'un silence.
        _sig_field = f.get("signature")
        if _sig_field:
            errors.append(
                f"files[{i}] '{path}' : `signature` n'est pas un champ contractuel — "
                "il est IGNORÉ. Utilise `desc` pour le COMPORTEMENT attendu du fichier, "
                "et `exports` (liste) pour les signatures de fonctions."
            )
        # LOT 2.8 — chaque fichier doit porter du SENS : une desc OU des
        # signatures. Le contrat Converto (owners seuls) a laissé 3 workers
        # inventer un convertisseur multi-unités hors-sujet. Exemption :
        # __init__.py (aucun sens à exiger).
        base_28 = path.replace("\\", "/").rsplit("/", 1)[-1].lower() if path else ""
        if (path and base_28 != "__init__.py"
                and not _entry_desc(f) and not _entry_api(f) and not _sig_field):
            errors.append(
                f"files[{i}] '{path}' : ajoute une `desc` — le COMPORTEMENT attendu "
                "du fichier (ex. \"champ Celsius, bouton Convertir, affiche '212.0 °F'\"). "
                "Sans desc ni exports, le worker INVENTE sa propre interprétation."
            )
    return errors


def web_root_route_warning(data: Dict[str, Any]) -> str:
    """LOT 2.2 (run MotDuJour 2026-07-06) — GUIDANCE (jamais un refus) : un contrat
    web (html + backend .py) dont aucun export/desc backend ne sert la racine `/`
    produit une app inaccessible même servie (MotDuJour : /api/* seulement →
    browser_navigate serait tombé sur 404). Retourne "" si tout va bien. Pur."""
    files = data.get("files") or [] if isinstance(data, dict) else []
    has_html = any(
        str((f or {}).get("path") or "").lower().endswith((".html", ".htm"))
        for f in files if isinstance(f, dict)
    )
    backend = [
        f for f in files
        if isinstance(f, dict)
        and str(f.get("path") or "").lower().endswith(".py")
        and not _is_test_path(str(f.get("path") or ""))
    ]
    if not has_html or not backend:
        return ""
    hay = " ".join(
        (_entry_desc(f) + " " + " ".join(_entry_api(f))) for f in backend
    ).lower()
    if "index" in hay or re.search(r"""get\s+/(?:\s|['"]|$)|route\s*\(\s*['"]/['"]""", hay):
        return ""
    return (
        "⚠️ Aucun export backend ne sert la racine `/` — pour une app web, expose "
        "une route GET / qui sert index.html (sinon la preview/browser_navigate "
        "tombera sur 404 à la racine)."
    )


def flask_static_root_warning(data: Dict[str, Any]) -> str:
    """2.7.3 (run MiniPanier) — GUIDANCE (jamais un refus) : collision entre NOS
    rails. Le stub HTML (2.1) fige les liens frères en RELATIF (`href="style.css"`)
    → servis à la racine `/style.css` ; mais une app Flask `Flask(static_folder=
    'static')` sert ses statiques sous `/static/` → 404 → CSS/JS jamais chargés,
    page cassée à l'écran (le run MiniPanier). Quand le contrat a un backend Flask
    ET une page HTML dans `static/`, on impose la config qui réconcilie les deux.
    "" si non concerné. Pur."""
    files = data.get("files") or [] if isinstance(data, dict) else []
    html_in_static = any(
        str((f or {}).get("path") or "").replace("\\", "/").lower().startswith("static/")
        and str((f or {}).get("path") or "").lower().endswith((".html", ".htm"))
        for f in files if isinstance(f, dict)
    )
    backend = [
        f for f in files
        if isinstance(f, dict)
        and str(f.get("path") or "").lower().endswith(".py")
        and not _is_test_path(str(f.get("path") or ""))
    ]
    if not html_in_static or not backend:
        return ""
    hay = " ".join(
        (_entry_desc(f) + " " + " ".join(_entry_api(f))) for f in backend
    ).lower()
    if "flask" not in hay:
        return ""
    # Déjà correctement guidé par le contrat lui-même → ne pas répéter.
    if "static_url_path" in hay:
        return ""
    return (
        "⚠️ App Flask + page dans `static/` : ta page référence style.css/script.js "
        "en chemins RELATIFS (servis à la racine `/style.css`), mais Flask sert ses "
        "statiques sous `/static/` par défaut → 404, la page se chargera SANS style "
        "ni JS. Le backend DOIT créer l'app avec "
        "`Flask(__name__, static_folder='static', static_url_path='')` (statiques "
        "servis à la racine) — sinon /style.css et /script.js seront introuvables."
    )


def objective_expects_ui_warning(data: Dict[str, Any], objective_wants_ui: bool) -> str:
    """LOT L2 (run MemoNest, 2026-08-13) — GUIDANCE : l'OBJECTIF réclame une
    interface, le contrat ne déclare AUCUN fichier HTML.

    Les trois avertissements voisins raisonnent sur le seul contenu du contrat ;
    ils sont donc muets quand il n'y a **rien** à quoi se raccrocher :
    `missing_shared_stylesheet_warning` exige un HTML déjà déclaré pour parler du
    CSS. Personne ne comparait le contrat à ce qui était DEMANDÉ.

    Conséquence prouvée : MemoNest — un SaaS dont l'objectif exigeait « page
    d'accueil publique » et « VÉRIFIE AU NAVIGATEUR » — a reçu un contrat de
    5 fichiers `.py`, zéro template. Le lead a découvert le manque APRÈS la
    publication et a écrit templates/ et static/ lui-même, en deux passes
    séparées : les classes du HTML (`features-grid`, `feature-card`) ne
    correspondaient à aucune règle du CSS (`stats-grid`, `stat-card`) → page
    livrée sans mise en page.

    `objective_wants_ui` est fourni par l'appelant (`_objective_wants_browser`,
    corrigé par le lot L1) : ce module ne peut pas importer `react` sans cycle.
    Avertissement ADDITIF, jamais bloquant — comme ses trois voisins. "" si non
    concerné. Pur."""
    if not objective_wants_ui or not isinstance(data, dict):
        return ""
    files = data.get("files") or []
    paths = [
        str((f or {}).get("path") or "").replace("\\", "/").lower()
        for f in files
        if isinstance(f, dict)
    ]
    if any(p.endswith((".html", ".htm")) for p in paths):
        return ""
    # Aucun fichier déclaré ⇒ ce n'est pas un contrat de CODE : soit une mission
    # d'EFFETS (H4 lui donne `effects`), soit un contrat vide que `validate_contract`
    # refusera de toute façon. Réclamer un template n'aide dans aucun des deux cas.
    if not paths:
        return ""
    return (
        "⚠️ L'OBJECTIF de cette mission demande une INTERFACE (page, navigateur), "
        "mais ton contrat ne déclare AUCUN fichier `.html`. Les workers ne "
        "produiront donc pas de frontend, et tu devras l'écrire toi-même après "
        "coup — hors contrat, hors périmètre, sans owner : c'est ainsi qu'on "
        "obtient un HTML et un CSS qui ne se correspondent pas. DÉCLARE les pages "
        "MAINTENANT avec leur owner (ex. `{\"path\": \"templates/index.html\", "
        "\"owner\": \"w_web\", \"no_public_api\": true}`) et la feuille de style "
        "qui va avec. Si ce livrable n'a réellement aucune interface, ignore cet "
        "avertissement."
    )


def missing_shared_stylesheet_warning(data: Dict[str, Any]) -> str:
    """2.9.B (run TriboBlog2) — GUIDANCE : le contrat déclare du HTML mais AUCUN
    fichier `.css`. Conséquence prouvée : le lead crée style.css APRÈS le contrat
    (hors périmètre), le stub HTML (2.1) n'a donc AUCUN `.css` frère à lier → les
    pages sortent sans `<link rel="stylesheet">` → site NU. On invite à déclarer la
    feuille de style DANS le contrat pour que le stub injecte le lien d'office.
    "" si non concerné. Pur."""
    files = data.get("files") or [] if isinstance(data, dict) else []
    paths = [str((f or {}).get("path") or "").replace("\\", "/").lower()
             for f in files if isinstance(f, dict)]
    has_html = any(p.endswith((".html", ".htm")) for p in paths)
    has_css = any(p.endswith(".css") for p in paths)
    if not has_html or has_css:
        return ""
    return (
        "⚠️ Ton contrat déclare des pages HTML mais AUCUN fichier `.css`. Si tu comptes "
        "un style partagé, DÉCLARE-le dans le contrat (ex. `{\"path\": \"style.css\", "
        "\"owner\": \"w_style\", \"no_public_api\": true}`) : le stub HTML liera alors "
        "`<link rel=\"stylesheet\" href=\"style.css\">` d'office dans chaque page. "
        "Sinon, un style.css créé APRÈS le contrat ne sera lié par aucune page → site NU."
    )


# ── génération des stubs ────────────────────────────────────────────────────────

_FROZEN_MARK = "SIGNATURE FIGÉE PAR LE CONTRAT — NE PAS MODIFIER"


def _py_stub(entry: Dict[str, Any]) -> str:
    lines = [f'"""{_entry_desc(entry) or entry["path"]} — stub de contrat.',
             "",
             f"{_FROZEN_MARK}. Remplis les corps (via edit_file), sans changer les",
             'signatures ni ajouter d\'API publique hors contrat."""', ""]
    # Imports inter-fichiers IMPOSÉS par le contrat, écrits EN DUR : l'API
    # croisée n'est plus une consigne, c'est du code (verrou anti-dérive max).
    imports = _entry_imports(entry)
    if imports:
        lines.append(f"# Imports IMPOSÉS par le contrat — {_FROZEN_MARK}.")
        lines.extend(imports)
        lines.append("")
    apis = _entry_api(entry)
    if not apis:
        lines.append("# TODO (worker) : implémenter selon CONTRAT.md")
    for sig in apis:
        s = sig.rstrip(":").rstrip()
        if s.startswith(("def ", "async def ", "class ")):
            lines.append(f"{s}:")
            body_indent = "    "
            lines.append(f'{body_indent}"""{_FROZEN_MARK}."""')
            if s.startswith("class "):
                lines.append(f"{body_indent}# TODO (worker) : implémenter selon CONTRAT.md")
            else:
                lines.append(f"{body_indent}raise NotImplementedError('TODO worker — cf. CONTRAT.md')")
            lines.append("")
        else:
            # constante / variable de module annoncée par le contrat
            lines.append(f"{s}  # {_FROZEN_MARK}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _js_stub(entry: Dict[str, Any]) -> str:
    lines = [f"// {_entry_desc(entry) or entry['path']} — stub de contrat.",
             f"// {_FROZEN_MARK}.", ""]
    apis = _entry_api(entry)
    if not apis:
        lines.append("// TODO (worker) : implémenter selon CONTRAT.md")
    for sig in apis:
        # M1-clôture (run MiniQuiz) : émettre du JS VALIDE — `function f() -> void`
        # copié tel quel produisait un stub qui ne compile pas, figé par le marqueur.
        s = _normalize_js_signature(sig) or sig.rstrip("{").rstrip()
        lines.append(f"{s} {{")
        lines.append("  // TODO (worker) : implémenter selon CONTRAT.md")
        lines.append("}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _html_stub(entry: Dict[str, Any], all_files: Any = (), project: str = "") -> str:
    anchors = "\n".join(
        f"  <!-- ANCRE CONTRAT : {a} -->" for a in _entry_api(entry)
    ) or "  <!-- TODO (worker) : implémenter selon CONTRAT.md -->"
    # LOT 2.1 (run MotDuJour 2026-07-06) — title = description VERBATIM du contrat
    # (onglet illisible) → nom du projet, fallback basename.
    html_path = str(entry.get("path") or "").replace("\\", "/")
    base = html_path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    title = (project or "").strip() or base or "index"
    # LOT 2.1 — le stub DOIT relier les fichiers frères du contrat : MotDuJour a
    # livré 143 lignes de CSS jamais chargées (aucun <link> — le worker ne l'a
    # pas ajouté, le stub non plus) → page nue au navigateur. Liens FIGÉS comme
    # les imports imposés des stubs .py.
    html_dir = html_path.rsplit("/", 1)[0] if "/" in html_path else ""
    links, scripts = [], []
    for f in all_files or ():
        p = str((f.get("path") if isinstance(f, dict) else f) or "").replace("\\", "/")
        d = p.rsplit("/", 1)[0] if "/" in p else ""
        if d != html_dir or p == html_path:
            continue
        rel = p.rsplit("/", 1)[-1]
        if p.lower().endswith(".css"):
            links.append(f'  <link rel="stylesheet" href="{rel}">')
        elif p.lower().endswith((".js", ".mjs")):
            scripts.append(f'  <script src="{rel}" defer></script>')
    assets = ""
    if links or scripts:
        assets = ("\n  <!-- Liens IMPOSÉS par le contrat (fichiers frères) — "
                  f"{_FROZEN_MARK}. -->\n" + "\n".join(links + scripts))
    return ("<!DOCTYPE html>\n<html lang=\"fr\">\n<head>\n  <meta charset=\"utf-8\">\n"
            f"  <title>{title}</title>\n"
            f"  <!-- {_FROZEN_MARK} : conserve les ancres ci-dessous -->"
            + assets + "\n</head>\n<body>\n" + anchors + "\n</body>\n</html>\n")


def _css_stub(entry: Dict[str, Any]) -> str:
    sections = "\n\n".join(
        f"/* SECTION CONTRAT : {a} */\n/* TODO (worker) */" for a in _entry_api(entry)
    ) or "/* TODO (worker) : implémenter selon CONTRAT.md */"
    return f"/* {_entry_desc(entry) or entry['path']} — stub de contrat. {_FROZEN_MARK}. */\n\n{sections}\n"


_DOC_EXT = (".md", ".markdown", ".txt", ".rst", ".adoc")

# Marqueur d'un document RESTÉ au stub. Doit être une phrase que le worker fait
# disparaître en rédigeant (on lui demande de tout remplacer) — c'est le pendant
# documentaire de `raise NotImplementedError` pour le code.
_DOC_STUB_MARK = "Remplace INTÉGRALEMENT ce contenu"


def _doc_stub(entry: Dict[str, Any]) -> str:
    """I1 — stub d'un livrable RÉDACTIONNEL (.md/.txt/.rst).

    Run `comparatif vectoriel` (2026-08-13) : le fallback générique écrivait
    `# {desc} — stub de contrat. SIGNATURE FIGÉE PAR LE CONTRAT — NE PAS
    MODIFIER.` suivi des `exports`. Dans un document, `#` n'est pas un
    commentaire mais un TITRE : le worker recevait donc un fichier qui lui
    ordonnait de ne pas modifier des signatures Python. Le consolidateur a obéi
    et a écrit `def generer_tableau(rapports: dict)` + `from rapport_chromadb
    import …` dans `comparatif_bases_vectorielles.md`. Le livrable contractuel
    était inutilisable, et le lead a dû créer un fichier HORS contrat pour livrer.

    Un document n'a pas de signature à figer : il a un SUJET et un PLAN.
    """
    desc = _entry_desc(entry)
    title = str(entry.get("path") or "document").replace("\\", "/").rsplit("/", 1)[-1]
    for ext in _DOC_EXT:
        if title.lower().endswith(ext):
            title = title[: -len(ext)]
            break
    lines = [f"# {title.replace('_', ' ').replace('-', ' ').strip() or 'Document'}", ""]
    if desc:
        lines += ["> À PRODUIRE — attendu de ce document :", f"> {desc}", ""]
    # Les `exports` d'un document ne sont pas des signatures : ce sont, au mieux,
    # les sections attendues. On les rend comme un PLAN, jamais comme du code figé.
    sections = [s for s in _entry_api(entry) if str(s).strip()]
    if sections:
        lines.append("## Plan attendu")
        lines += [f"- {s}" for s in sections]
        lines.append("")
    lines += [
        f"_({_DOC_STUB_MARK} par le document final : c'est un "
        "livrable RÉDACTIONNEL, pas du code — n'écris ni `def`, ni `import`, "
        "ni signature de fonction ici.)_",
        "",
    ]
    return "\n".join(lines)


def generate_stub(entry: Dict[str, Any], all_files: Any = (), project: str = "") -> str:
    """Stub réel par extension : signatures EXACTES du contrat, corps TODO.

    `all_files`/`project` (optionnels, défaut = comportement historique) servent
    au stub HTML : liens <link>/<script> vers les fichiers frères + title propre."""
    path = str(entry.get("path") or "").lower()
    if path.endswith(".py"):
        return _py_stub(entry)
    if path.endswith((".js", ".jsx", ".ts", ".tsx", ".mjs")):
        return _js_stub(entry)
    if path.endswith((".html", ".htm")):
        return _html_stub(entry, all_files=all_files, project=project)
    if path.endswith((".css", ".scss", ".less")):
        return _css_stub(entry)
    if path.endswith(_DOC_EXT):
        return _doc_stub(entry)
    desc = _entry_desc(entry) or entry.get("path") or ""
    apis = "\n".join(f"- {a}" for a in _entry_api(entry))
    return f"# {desc} — stub de contrat. {_FROZEN_MARK}.\n{apis}\n# TODO (worker)\n"


def inspect_worker_deliverables(workspace_root: Any, allowed_files: Any) -> Dict[str, Any]:
    """Inspect the files owned by one mission worker.

    A worker delivery is ready only when every assigned path exists, is non-empty,
    and no longer equals (or contains the active markers of) its generated contract
    stub. Paths are resolved below the mission workspace and traversal fails closed.
    """
    root = Path(workspace_root).resolve()
    assigned: List[str] = []
    missing: List[str] = []
    stubs: List[str] = []
    invalid: List[str] = []

    contract: Dict[str, Any] = {}
    try:
        raw_contract = json.loads((root / CONTRACT_JSON).read_text(encoding="utf-8"))
        if isinstance(raw_contract, dict):
            contract = raw_contract
    except Exception:
        contract = {}
    contract_files = contract.get("files") or []
    entries = {
        str(entry.get("path") or "").replace("\\", "/").strip("/"): entry
        for entry in contract_files
        if isinstance(entry, dict) and str(entry.get("path") or "").strip()
    }

    prefixes = (
        f"workspace/missions/{root.name}/",
        f"missions/{root.name}/",
        f"{root.name}/",
    )
    for raw_path in allowed_files or []:
        rel = str(raw_path or "").strip().replace("\\", "/")
        for prefix in prefixes:
            if rel.lower().startswith(prefix.lower()):
                rel = rel[len(prefix):]
                break
        rel = rel.lstrip("./")
        if not rel or rel in assigned:
            continue
        assigned.append(rel)

        rel_path = Path(rel)
        if rel_path.is_absolute() or rel_path.drive or ".." in rel_path.parts:
            invalid.append(rel)
            continue
        target = (root / rel_path).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            invalid.append(rel)
            continue
        if not target.is_file() or target.stat().st_size <= 0:
            missing.append(rel)
            continue

        try:
            content = target.read_text(encoding="utf-8", errors="replace")
        except Exception:
            content = ""
        marker_stub = any(
            marker in content
            for marker in ("raise NotImplementedError", "TODO worker", "TODO (worker)",
                           # I3 — le stub DOCUMENTAIRE (I1) n'a ni `raise` ni
                           # `TODO` : sans ce marqueur, un rapport resté au stub
                           # passait pour un livrable dès qu'un espace le rendait
                           # différent du stub régénéré (`exact_stub` en échec).
                           _DOC_STUB_MARK)
        )
        entry = entries.get(rel)
        exact_stub = False
        if entry is not None:
            try:
                expected = generate_stub(
                    entry,
                    all_files=contract_files,
                    project=str(contract.get("project") or ""),
                )
                exact_stub = content.replace("\r\n", "\n").strip() == expected.replace(
                    "\r\n", "\n"
                ).strip()
            except Exception:
                exact_stub = False
        if marker_stub or exact_stub:
            stubs.append(rel)

    ready = bool(assigned) and not missing and not stubs and not invalid
    return {
        "ready": ready,
        "assigned": assigned,
        "missing": missing,
        "stubs": stubs,
        "invalid": invalid,
    }


# ── rendu lisible ───────────────────────────────────────────────────────────────

def render_contract_md(data: Dict[str, Any]) -> str:
    """CONTRAT.md : la version HUMAINE/worker du contrat machine."""
    proj = str(data.get("project") or "mission").strip()
    lines = [f"# Contrat de mission — {proj}", "",
             "Chaque worker REMPLIT ses stubs (signatures figées) et n'écrit QUE ses "
             "fichiers assignés. Toute API inter-fichiers est listée ici — n'en invente aucune autre.", ""]
    for f in data.get("files") or []:
        lines.append(f"## `{f.get('path')}` — owner : **{f.get('owner')}**")
        desc = _entry_desc(f)
        if desc:
            lines.append(desc)
        apis = _entry_api(f)
        if apis:
            lines.append("")
            lines.append("API (signatures figées) :")
            lines.extend(f"- `{a}`" for a in apis)
        imports = _entry_imports(f)
        if imports:
            lines.append("")
            lines.append("Imports IMPOSÉS (déjà écrits dans le stub) :")
            lines.extend(f"- `{a}`" for a in imports)
        lines.append("")
    # H4 — les effets sont des livrables au même titre que les fichiers : ils
    # figurent au CONTRAT.md, avec leur preuve attendue, sinon le lead les
    # « intègre » sans jamais savoir ce qu'il devait constater.
    for owner, eff in effects_map(data).items():
        lines.append(f"## Effets — owner : **{owner}**")
        for e in eff:
            target = str(e.get("target") or "").strip()
            head = f"- **{e.get('action')}**" + (f" → `{target}`" if target else "")
            d = str(e.get("desc") or "").strip()
            lines.append(head + (f" : {d}" if d else ""))
            proof = str(e.get("proof") or "").strip()
            if proof:
                lines.append(f"  - preuve attendue : {proof}")
        lines.append("")
    # `shared_api` (structure LIBRE fournie par le lead) : rendue telle quelle,
    # sans interprétation — c'est la référence croisée que tous les workers lisent.
    shared = data.get("shared_api")
    if shared:
        lines += ["## API partagée (référence commune, NE PAS dévier)", "", "```json",
                  json.dumps(shared, ensure_ascii=False, indent=2), "```", ""]
    notes = str(data.get("notes") or "").strip()
    if notes:
        lines += ["## Notes", notes, ""]
    return "\n".join(lines)


# ── objectifs structurés pour delegate_and_wait ─────────────────────────────────

def owners_map(data: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    """owner → liste de ses entrées fichier (ordre du contrat préservé)."""
    out: Dict[str, List[Dict[str, Any]]] = {}
    for f in data.get("files") or []:
        owner = str(f.get("owner") or "").strip()
        if owner:
            out.setdefault(owner, []).append(f)
    return out


def effects_map(data: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    """H4 — owner → liste de ses effets (ordre du contrat préservé)."""
    out: Dict[str, List[Dict[str, Any]]] = {}
    if not isinstance(data, dict):
        return out
    for e in data.get("effects") or []:
        if not isinstance(e, dict):
            continue
        owner = str(e.get("owner") or "").strip()
        if owner:
            out.setdefault(owner, []).append(e)
    return out


WORKER_EFFECT_DISCIPLINE = (
    "🎬 DISCIPLINE D'ACTION (tu produis un EFFET, pas du code) :\n"
    "• Tu es une Lumena complète : tu as les MÊMES outils que le parent "
    "(mail, Slack, navigateur, recherche, mémoire, planificateur, MCP…). "
    "Utilise l'outil qui réalise vraiment l'action.\n"
    "• N'écris PAS de script pour faire ce qu'un outil fait directement, et ne "
    "code pas un livrable qu'on ne t'a pas demandé.\n"
    "• Agis d'abord, décris ensuite : une action non tentée n'est jamais un "
    "succès partiel.\n"
    "• Ne conclus PAS (FINAL) sur une intention. Soit tu cites la PREUVE "
    "obtenue, soit tu dis clairement que l'effet n'a pas été réalisé et "
    "pourquoi. Un rapport qui laisse croire que c'est fait est la pire issue."
)


def effects_brief(entries: Any) -> str:
    """H4 — bloc d'objectif pour des effets. Pur, `""` si rien.

    L'exigence de preuve est répétée ici parce que c'est au moment d'AGIR que le
    worker doit savoir ce qu'il devra ramener — pas au moment de conclure, où il
    est déjà trop tard pour l'avoir capturé.
    """
    rows = [e for e in (entries or []) if isinstance(e, dict)]
    if not rows:
        return ""
    lines = [
        "🎯 Tes EFFETS à produire (livrables NON-fichier — ils comptent autant "
        "qu'un fichier) :"
    ]
    for e in rows:
        action = str(e.get("action") or "").strip()
        target = str(e.get("target") or "").strip()
        head = f"- **{action}**" + (f" → `{target}`" if target else "")
        desc = str(e.get("desc") or "").strip()
        if desc:
            head += f" : {desc}"
        lines.append(head)
        proof = str(e.get("proof") or "").strip()
        if proof:
            lines.append(f"  PREUVE à ramener : {proof}")
    lines.append(
        "Réalise ces effets avec les outils de Lumena (mail, Slack, navigateur, "
        "MCP, mémoire, planificateur… tu as les mêmes que le parent). Dans ton "
        "rapport final, cite la preuve OBTENUE pour chaque effet, ou dis "
        "explicitement qu'il n'a PAS été réalisé — jamais un résumé qui laisse "
        "croire que c'est fait."
    )
    return "\n".join(lines)


def unproven_effect_owners(
    data: Dict[str, Any], children: Any, done_states: Any = None
) -> List[str]:
    """H4 — owners d'effets dont le worker n'a PAS terminé (ordre du contrat).

    Le pendant, côté effets, de ce que le ledger fait pour les fichiers. Aucune
    heuristique de texte : si le worker chargé d'envoyer le mail n'a jamais
    atteint un état terminal réussi, le mail n'est pas prouvé envoyé — que le
    récit final l'affirme ou non. Un worker `done` rend la main au truth-lock,
    qui reste seul juge du CONTENU de ce qu'il rapporte.

    `[]` s'il n'y a pas d'effets, pas de contrat, ou si tous les porteurs ont
    terminé — la clôture propre reste le cas normal.
    """
    by_owner = effects_map(data)
    if not by_owner:
        return []
    kids = [c for c in (children or []) if isinstance(c, dict)]
    if not kids:
        # Mission menée SEULE par le lead (aucune délégation) : le contrat n'a
        # jamais été mis en vigueur, et c'est le truth-lock qui juge son récit.
        # Sans cette porte, on condamnerait un travail réellement fait.
        return []
    ok = {str(s).lower() for s in (done_states or ("done",))}
    finished = set()
    for c in kids:
        if not isinstance(c, dict):
            continue
        owner = str((c.get("metadata") or {}).get("delegation_owner") or "").strip()
        if owner and str(c.get("state") or "").lower() in ok:
            finished.add(owner)
    return [o for o in by_owner if o not in finished]


def owner_of_path(data: Dict[str, Any], path: Any) -> str:
    """F3.a — owner déclaré au contrat pour un chemin donné ; `""` si inconnu.

    Sert à router une issue : un worker a buté sur un fichier qui n'est pas le sien,
    le contrat sait à qui il appartient. Aucune heuristique sur le texte du modèle.

    Match exact d'abord (chemin relatif normalisé), puis par basename — même logique
    de tolérance que le garde de périmètre 2.3, qui accepte les deux formes. Le
    contrat garantit déjà « un fichier = UN SEUL owner » (validation à l'écriture),
    donc le match par basename ne peut pas être ambigu entre deux owners… sauf si
    deux dossiers portent le même nom de fichier : dans ce cas on refuse de deviner.
    """
    want = str(path or "").replace("\\", "/").strip().lstrip("./").strip("/")
    if not want or not isinstance(data, dict):
        return ""
    entries = data.get("files") or []
    base_matches = []
    for f in entries:
        if not isinstance(f, dict):
            continue
        p = str(f.get("path") or "").replace("\\", "/").strip().lstrip("./").strip("/")
        owner = str(f.get("owner") or "").strip()
        if not p or not owner:
            continue
        if p == want:
            return owner
        if p.rsplit("/", 1)[-1] == want.rsplit("/", 1)[-1]:
            base_matches.append(owner)
    # Un seul candidat par basename → routable. Plusieurs → ambigu, on ne devine pas.
    uniq = set(base_matches)
    return base_matches[0] if len(uniq) == 1 else ""


def live_owner_of_path(
    path: Any, data: Dict[str, Any], children: Any, terminal_states: Any = None
) -> str:
    """H3 — nom du worker **encore vivant** qui possède ce fichier, `""` sinon.

    Le lease de ressources (`resource_key_for` → `files:<chemin>`) sérialise deux
    écritures SIMULTANÉES : il évite la corruption, pas la divergence. Rien
    n'empêchait le lead d'écrire `app.py` *entre* deux écritures du worker qui en
    est l'owner — c'est exactement ce qui s'est produit au run SuiviDepenses :

        23:52:28  « Je reprends le périmètre moi-même »
        23:53:12  le lead édite app.py … le CodeAgent de w_backend l'écrit aussi
        23:55:03  w_frontend termine, w_tests continue jusqu'à 00:02

    Ce qui manquait n'est pas un verrou d'instant mais une **propriété dans la
    durée** : tant que `w_backend` n'est pas terminal, `app.py` est à lui.

    Pur : aucune I/O. `children` = enregistrements de tâches (dicts) portant
    `state` et `metadata.delegation_owner`. Conservateur : sans contrat, sans
    owner identifiable ou sans enfant vivant, retourne `""` — on ne bloque jamais
    sur un doute, sous peine d'empêcher le lead d'intégrer.
    """
    try:
        owner = owner_of_path(data, path)
        if not owner:
            return ""
        terminal = set(terminal_states or ("done", "failed", "cancelled"))
        for child in (children or []):
            if not isinstance(child, dict):
                continue
            if str(child.get("state") or "") in terminal:
                continue
            meta = child.get("metadata") or {}
            if str(meta.get("delegation_owner") or "").strip() == owner:
                return owner
        return ""
    except Exception:
        return ""


def worker_objectives(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """`[{objective, allowed_files}]` — un worker par owner, périmètre = SES fichiers.

    Format directement passable à delegate_and_wait (qui pose allowed_files en meta
    enfant → appliqué par le garde 2.3).

    H4 — un owner peut porter des fichiers, des EFFETS (livrables non-fichier), ou
    les deux ; on itère sur l'union. Un porteur d'effets purs sort avec
    `allowed_files: []` : il ne possède aucun fichier, et H3 l'empêche d'écrire
    ceux des workers encore vivants."""
    result: List[Dict[str, Any]] = []
    grouped = owners_map(data)
    by_effect = effects_map(data)
    all_paths = [str(f.get("path")) for f in (data.get("files") or []) if f.get("path")]
    # H4 — un owner peut porter des fichiers, des effets, ou les deux. On itère
    # sur l'UNION en préservant l'ordre du contrat (fichiers d'abord, puis les
    # owners qui n'apparaissent que dans `effects`).
    owners = list(grouped.keys()) + [o for o in by_effect if o not in grouped]
    # LOT Z13 — la direction artistique, calculée UNE FOIS pour tout le contrat
    # (déterministe : même contrat → même palette) et remise au seul propriétaire
    # de la feuille de style. Vide si le contrat n'a rien à styler ou si la
    # génération est indisponible → objectifs strictement inchangés.
    _design_brief = design_brief_for_contract(data)
    for owner in owners:
        entries = grouped.get(owner, [])
        mine = [str(e["path"]) for e in entries]
        others = [p for p in all_paths if p not in mine]
        desc_lines = []
        for e in entries:
            d = _entry_desc(e)
            apis = _entry_api(e)
            imports = _entry_imports(e)
            desc_lines.append(f"- {e['path']}" + (f" : {d}" if d else "")
                              + (f" (API : {', '.join(apis)})" if apis else "")
                              + (f" (imports IMPOSÉS, déjà dans le stub : "
                                 f"{', '.join(imports)})" if imports else ""))
        eff = by_effect.get(owner) or []
        # H4 — un worker d'effets PURS ne doit pas recevoir la discipline de
        # CODAGE (« ne conclus pas sans une mutation réelle ») : elle le
        # pousserait à écrire du code au lieu d'envoyer le mail. Cas strictement
        # nouveau (aucun fichier + des effets) — tout worker possédant un fichier
        # garde exactement le bloc historique.
        _disc = WORKER_EFFECT_DISCIPLINE if (eff and not mine) \
            else worker_discipline_block(mine, _design_brief)   # LOT A : discipline + steer + rider(s)
        text = (
            f"[Worker {owner}] {WORKER_CONTRACT_PREAMBLE}\n"
            f"{_disc}\n"
        )
        if desc_lines:
            text += ("Tes fichiers (stubs déjà créés) :\n" + "\n".join(desc_lines)
                     + (f"\nNE touche PAS aux autres fichiers ({', '.join(others)}) "
                        "— ils appartiennent à d'autres workers." if others else ""))
        if eff:
            if desc_lines:
                text += "\n"
            text += effects_brief(eff)
        result.append({"objective": text, "allowed_files": mine})
    return result


# ── 2.13.C — le contrat est la SEULE source de spec des workers ──────────────
# (run bibliapi 2026-07-09) : le lead a RÉÉCRIT les objectifs workers en
# contredisant le contrat (`import storage` vs stub `from storage import …` ;
# seed « 2 livres » + `id==3` inventés par w_tests) → 4 passed / 4 failed.
# Le LOT A force la DISCIPLINE au point déterministe de delegate_and_wait ;
# ce bloc force la SPEC au même endroit : exports/imports EXACTS du contrat,
# hors de portée de la paraphrase du lead.

WORKER_SPEC_MARK = "SPEC CONTRACTUELLE (source de vérité)"


def worker_spec_block(data: Dict[str, Any], allowed_files: Any) -> str:
    """Bloc de spec EXACTE pour UN worker, extrait de contract.json. Pur.

    Matching DÉTERMINISTE par fichiers : entrées du contrat dont le `path` est
    dans `allowed_files` (jamais par le texte de l'objectif). Si aucun match
    (lead ayant délégué sans périmètre — jamais vu en run, filet quand même) :
    bloc COMPLET compact de tous les fichiers, déterministe, jamais muet.
    "" si le contrat n'a pas de `files`.
    """
    files = data.get("files") or []
    if not isinstance(files, list) or not files:
        return ""
    wanted = {
        str(p).replace("\\", "/").strip()
        for p in (allowed_files or [])
        if str(p).strip()
    }
    mine = [
        f for f in files
        if isinstance(f, dict)
        and str(f.get("path") or "").replace("\\", "/").strip() in wanted
    ]
    scope = mine if mine else [f for f in files if isinstance(f, dict)]
    if not scope:
        return ""
    lines = [
        f"📜 {WORKER_SPEC_MARK} — en cas de CONFLIT entre ton objectif et ce "
        "bloc, le CONTRAT prime :",
    ]
    for e in scope:
        lines.append(f"### `{e.get('path')}`")
        d = _entry_desc(e)
        if d:
            lines.append(f"- rôle : {d}")
        for a in _entry_api(e):
            lines.append(f"- export EXACT (signature figée) : `{a}`")
        for im in _entry_imports(e):
            lines.append(f"- import IMPOSÉ (ligne exacte, déjà dans le stub) : `{im}`")
        if e.get("no_public_api"):
            lines.append("- pas d'API publique (asset/entry-point)")
    lines.append(
        "⚠️ N'invente NI seed NI données de départ hors contrat ; n'ajoute pas "
        "d'exports non listés ; ne renomme et ne reformule AUCUNE signature."
    )
    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
