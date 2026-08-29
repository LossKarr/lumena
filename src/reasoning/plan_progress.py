"""Orchestration du PLAN system — helpers PURS de complétion de tâches.

Le moteur de PREUVE sémantique (capacités, verify-task, evaluate_task_proof…)
vit déjà dans `plan_evidence.py`. Ce module reçoit, par sous-phases, les helpers
d'orchestration plan extraits de react.py (déménagement pur / decision-core).

Phase 4A : garde-fous « périmètre outil ↔ tâche » — quels outils browser passifs
ou read-only ont le DROIT de cocher quelle tâche de plan.

Module auto-contenu (stdlib uniquement) → aucun import circulaire avec react.
react ré-importe ces noms (point d'import historique des tests).
"""
from __future__ import annotations

# Outils browser « passifs » (lecture/observation, pas une action métier).
_BROWSER_PLAN_PASSIVE_TOOLS: frozenset = frozenset({
    "browser_navigate", "browser_dom_state", "browser_screenshot",
    "browser_screenshot_labels", "browser_page_info", "browser_get_content",
    "browser_get_text", "browser_frames", "browser_frame_content",
    "browser_scroll", "browser_wait_for",
})

# Outils read-only de découverte (web/temps/santé) — ne cochent une tâche
# que si la tâche est elle-même de l'ordre du read-only/observation.
_READ_ONLY_DISCOVERY_PLAN_TOOLS: frozenset = frozenset({
    "web_fetch",
    "web_search",
    "web_search_brave",
    "browser_search_google",
    "get_time",
    "health_check",
    "process_status",
    "list_document_models",
})

_PLAN_ACCENT_TRANSLATION = str.maketrans({
    "à": "a", "â": "a", "ä": "a", "á": "a",
    "ç": "c",
    "é": "e", "è": "e", "ê": "e", "ë": "e",
    "î": "i", "ï": "i", "í": "i",
    "ô": "o", "ö": "o", "ó": "o",
    "ù": "u", "û": "u", "ü": "u", "ú": "u",
    "ÿ": "y",
})


def _fold_plan_text(value: str) -> str:
    return (value or "").lower().translate(_PLAN_ACCENT_TRANSLATION)


# Tâches de CORRECTION : « lire = diagnostiquer, pas corriger ». Une telle tâche
# ne peut être créditée que par une MUTATION (edit/write/patch), jamais par une
# lecture. Cf. run taskflow 2026-07-02 : read_files_batch a marqué « Corriger les
# erreurs » completed → FINALIZE prématuré avec 7 tests encore rouges.
_CORRECTION_TASK_VERBS: tuple = (
    "corrig", "réparer", "reparer", "répare", "repare",
    "debug", "déboguer", "deboguer",
    "résoudre l'err", "resoudre l'err", "résoudre les err", "resoudre les err",
    "faire passer les test", "réparer les test", "reparer les test",
    "remplir", "compléter", "completer", "implémenter", "implementer",
)
_READONLY_PLAN_TOOLS: frozenset = frozenset({
    "read_file", "read_files_batch", "read_document",
    "list_directory", "list_dir", "grep_search", "find_files",
})


def correction_task_blocks_readonly(tool_name: str, task_desc: str) -> bool:
    """True si (tâche de correction) ET (outil de LECTURE) → NE PAS créditer.

    Substring-based : le préfixe « Étape N: » n'affecte pas la détection
    (« corrig » ⊂ « étape 4: corriger les erreurs »). Pur/testable.
    """
    if tool_name not in _READONLY_PLAN_TOOLS:
        return False
    d = (task_desc or "").lower()
    return any(v in d for v in _CORRECTION_TASK_VERBS)


# ── LOT 2.7 — tâches « tool-explicit » (run NoteFlash 2026-07-02) ────────────────
# Le plan du lead disait « Poser le contrat via write_mission_contract » et la tâche
# a été créditée par text_inference sur un appel… create_mission. Règle : une tâche
# qui NOMME explicitement un outil (ou pytest) ne peut être créditée QUE par cet
# outil (ou, pour pytest, par un outil qui exécute réellement une commande).
_TOOL_EXPLICIT_NAMES: frozenset = frozenset({
    "write_mission_contract", "delegate_and_wait", "create_mission",
    "delegate_task", "browser_navigate", "start_preview_server", "serve_website",
    "browser_verify_local_project",
    "list_document_models", "generate_studio_document", "generate_studio_documents",
    "revise_studio_document", "open_document_delivery", "create_pdf",
    "create_invoice_pdf", "create_from_template",
})
_PYTEST_ALLOWED_TOOLS: frozenset = frozenset({
    "run_command", "run_tests", "execute_command", "bash", "run_python",
})

_PYTEST_EXECUTION_MARKERS: tuple = (
    "execut", "lanc", "run ", "faire tourner", "passer pytest",
    "pytest vert", "pytest jusqu", "relancer pytest",
)

# ── LOT Z40b (run du 2026-08-28) — « Verifier que les tests passent » ────────────
# Le bilan affichait `[OK] Verifier que les tests passent` : la case avait ete
# cochee par le FINAL, jamais par une execution. Mesure : `final_fulfills_task`
# rend True (le mot « verifi » est dans `_SYNTH_KW`) et les CINQ bloqueurs de
# `final_requires_operational_proof` rendent False.
#
# L'intitule n'avait NI le mot « pytest » NI un marqueur d'execution. Il etait
# donc au pire des deux mondes : impossible a crediter par un vrai run vert
# (non reconnu), et auto-credite par la prose (rien ne le bloquait).
#
# ⚠️ LECON Z3b, respectee ici. La premiere version de `browser_verify_task_blocks`
# allongeait une liste de mots (« filtre », « tri », « bouton ») et bloquait a
# tort « trier les resultats du benchmark ». Sa docstring en tire la regle :
# **on ne devine pas l'intention avec du vocabulaire.**
#
# Z40b ne porte donc AUCUN verbe. Il s'ancre sur l'OBJET — le mot « test » suivi,
# dans la MEME proposition, de son ISSUE. « Ecrire les tests unitaires » ne
# matche pas : aucune issue n'est nommee. « Trier les resultats du benchmark »
# non plus : aucun test n'est nomme.
_PYTEST_OUTCOME_WORDS: tuple = (
    "passent", "passe", "passer", "vert", "verts",
    "reussissent", "reussi", "reussis", "sans erreur", "sans echec",
)
#: Fenetre, en caracteres, entre le mot « test » et son issue. Au-dela, les deux
#: mots appartiennent en pratique a deux idees differentes.
_PYTEST_OUTCOME_WINDOW: int = 60


def _mot_present(folded: str, mot: str, debut: int, fin: int) -> bool:
    """True si `mot` apparait comme MOT ENTIER dans `folded[debut:fin]`.

    Ecrit sans `re` : ce module n'importe volontairement aucune regex, et une
    dependance nouvelle pour un seul predicat serait un elargissement gratuit
    du perimetre (invariant 11).
    """
    fenetre = folded[max(0, debut):fin]
    pos = fenetre.find(mot)
    while pos != -1:
        avant = fenetre[pos - 1] if pos > 0 else " "
        apres_i = pos + len(mot)
        apres = fenetre[apres_i] if apres_i < len(fenetre) else " "
        if not avant.isalnum() and not apres.isalnum():
            return True
        pos = fenetre.find(mot, pos + 1)
    return False


def _test_outcome_task(folded: str) -> bool:
    """True quand l'intitule nomme des TESTS *et* leur ISSUE."""
    depart = 0
    while True:
        pos = folded.find("test", depart)
        if pos == -1:
            return False
        depart = pos + 4
        avant = folded[pos - 1] if pos > 0 else " "
        if avant.isalnum():  # « pytest » est un mot a lui seul, traite plus haut
            continue
        # La fenetre s'arrete a la fin de la proposition : une issue citee dans
        # la phrase SUIVANTE ne parle pas de ces tests-la.
        fin = pos + _PYTEST_OUTCOME_WINDOW
        for coupure in (".", ";", "\n"):
            c = folded.find(coupure, pos)
            if c != -1:
                fin = min(fin, c)
        if any(_mot_present(folded, m, pos, fin) for m in _PYTEST_OUTCOME_WORDS):
            return True


def pytest_execution_task(description: str) -> bool:
    """True only when a plan task asks to RUN pytest, not to write its tests."""
    folded = _fold_plan_text(description)
    if "pytest" in folded and any(marker in folded for marker in _PYTEST_EXECUTION_MARKERS):
        return True
    # Run 2026-08-29 — l'ANGLE MORT SYMETRIQUE de Z40b. L'intitule
    # « Verifier le resultat avec pytest (run_command) » a ete refuse QUATRE
    # fois (« preuve insuffisante ») alors que pytest avait tourne et rendu
    # 10 passed : le marqueur « run » exige une espace, et « run_command »
    # n'en a pas. La tache est restee in-progress sur une mission cloturee.
    #
    # Z40b a ferme le sens « la prose coche sans preuve » ; il laissait ouvert
    # le sens inverse — une preuve REELLE qui ne coche pas.
    #
    # Ancrage sur un FAIT, pas sur du vocabulaire (lecon Z3b) : l'intitule
    # nomme pytest ET nomme l'outil qui execute des commandes. On ne devine
    # aucune intention, on lit deux noms propres.
    if "pytest" in folded and any(
            _contains_explicit_tool_name(folded, t) for t in _PYTEST_ALLOWED_TOOLS):
        return True
    # Z40b — l'issue des tests est elle-meme une demande d'execution : on ne
    # constate pas qu'un test « passe » sans l'avoir fait tourner.
    return _test_outcome_task(folded)


def pytest_plan_task_proven(task_desc: str, tool_name: str, test_outcome) -> bool:
    """True when a parsed test verdict proves an explicit pytest plan task."""
    if not pytest_execution_task(task_desc):
        return False
    if (tool_name or "").lower() not in _PYTEST_ALLOWED_TOOLS:
        return False
    outcome = test_outcome if isinstance(test_outcome, dict) else {}
    if not outcome.get("is_test_cmd") or not outcome.get("ran_something"):
        return False
    folded = _fold_plan_text(task_desc)
    # Z40b — LE PIEGE DU LOT. Elargir la reconnaissance sans elargir cette
    # exigence rendrait le garde PLUS FAIBLE qu'avant : un run ROUGE
    # crediterait « les tests passent ». Les deux moities vont ensemble.
    #
    # « passer pytest » est exclu : dans le vocabulaire d'origine du module,
    # c'est un marqueur d'EXECUTION (« faire passer pytest »), pas une exigence
    # d'issue. L'inclure durcirait un intitule historique.
    _issue_exigee = _test_outcome_task(folded) and "passer pytest" not in folded
    requires_green = "vert" in folded or "jusqu" in folded or _issue_exigee
    return bool(outcome.get("green")) if requires_green else True


def _contains_explicit_tool_name(text: str, name: str) -> bool:
    """Match a tool token without confusing singular/plural prefixes."""
    start = 0
    token_chars = "abcdefghijklmnopqrstuvwxyz0123456789_"
    while True:
        index = text.find(name, start)
        if index < 0:
            return False
        before_ok = index == 0 or text[index - 1] not in token_chars
        end = index + len(name)
        after_ok = end == len(text) or text[end] not in token_chars
        if before_ok and after_ok:
            return True
        start = index + 1


def document_workflow_task_blocks(tool_name: str, task_desc: str) -> bool:
    """Reserve compound document workflow tasks for their proof-bearing tools."""
    desc = _fold_plan_text(task_desc)
    if not any(token in desc for token in (
        "document", "modele", "fichier", "pdf", "lot", "livraison", "version",
        "devis", "facture", "contrat", "attestation", "commande",
    )):
        return False
    tool = (tool_name or "").lower()
    if any(token in desc for token in ("ouvr", "affich")):
        return tool != "open_document_delivery"
    if any(token in desc for token in ("modif", "revis", "mettre a jour", "remplac")):
        return tool not in {"revise_studio_document", "apply_document_edit"}
    if (
        any(token in desc for token in ("verif", "valid", "control"))
        and any(token in desc for token in ("revision", "nouvelle version", "version modif"))
    ):
        return tool not in {"revise_studio_document", "apply_document_edit"}
    if (
        any(token in desc for token in ("gener", "creer", "produi"))
        and any(token in desc for token in (
            "document", "modele", "pdf", "lot", "livraison",
            "devis", "facture", "contrat", "attestation", "commande",
        ))
    ):
        generation_tools = {
            "generate_studio_document", "generate_studio_documents",
            "document_manifest", "create_pdf", "create_invoice_pdf",
            "create_docx", "create_xlsx", "create_pptx", "create_csv",
            "create_html", "create_markdown", "create_from_template",
        }
        if "document studio" in desc:
            generation_tools = {
                "generate_studio_document", "generate_studio_documents",
                "document_manifest",
            }
        return tool not in generation_tools
    return False


def document_workflow_task_operation(task_desc: str) -> str:
    """Classify proof-bearing post-generation tasks in compound workflows.

    The generic plan tracker only sees one successful tool call. It cannot
    decide that ``opened=4`` satisfies "open 34", nor that an arbitrary
    revision targets the requested ordinal. ReAct uses this pure classifier to
    reserve these tasks for its quantitative workflow reconciler.
    """
    desc = _fold_plan_text(task_desc)
    if not desc:
        return ""
    if (
        any(token in desc for token in ("verif", "valid", "control", "confirm"))
        and any(token in desc for token in ("bibliotheque", "library", "index"))
    ):
        return "library_verify"
    if any(token in desc for token in (
        "historique", "parent/enfant", "parent enfant", "provenance",
        "transformation",
    )):
        return "history"
    if any(token in desc for token in ("export", "convert", "transform")) and any(
        token in desc for token in (
            "document", "fichier", "pdf", "html", "docx", "xlsx", "pptx", "csv",
            "version",
        )
    ):
        return "export"
    # "Relire le document modifie" describes a verification. The adjective
    # must not let a successful revision credit its own proof step.
    if any(token in desc for token in ("verif", "valid", "control", "relire", "inspect")):
        if any(token in desc for token in (
            "document", "fichier", "pdf", "version", "revision", "bilan",
        )):
            return "verify"
    if any(token in desc for token in ("modif", "revis", "mettre a jour", "remplac")):
        return "revise"
    if any(token in desc for token in ("ouvr", "affich")) and any(
        token in desc for token in ("document", "fichier", "pdf", "lot", "livraison")
    ):
        return "open"
    return ""


def tool_explicit_task_blocks(tool_name: str, task_desc: str) -> bool:
    """True si la tâche NOMME explicitement un outil précis et que `tool_name`
    n'est PAS cet outil → NE PAS créditer (chemin principal ET fallback auto).

    Couvre aussi « pytest » : exécution réelle requise (jamais une lecture).
    Pur/testable."""
    d = (task_desc or "").lower()
    if not d:
        return False
    tn = (tool_name or "").lower()
    if document_workflow_task_blocks(tn, d):
        return True
    for named in _TOOL_EXPLICIT_NAMES:
        # Boundaries matter because `generate_studio_document` is a strict
        # prefix of `generate_studio_documents`.
        if _contains_explicit_tool_name(d, named) and tn != named:
            return True
    if pytest_execution_task(d) and tn not in _PYTEST_ALLOWED_TOOLS:
        return True
    return False


# ── C0.3b — tâches de PUBLICATION du livrable (run FrigoZen 2026-07-04) ──────────
# « Publier le livrable et faire le rapport final » a été cochée par le write_file
# de style.css (fallback auto-avancement) → plan « complet » → FINALIZE prématuré.
# Règle : une tâche de publication du LIVRABLE n'est créditée QUE par
# publish_mission_workspace. Contexte requis (livrable/livraison/workspace) pour ne
# pas bloquer les publications métier (tweet, article…) créditées par leur outil.
# LOT Z3b (run Sentinelle, 2026-08-15) — « Publier et rapporter » a été marquée
# FAITE par un `browser_evaluate`, alors que rien n'était publié. Le garde était
# pourtant appelé : il exigeait un mot de contexte que cette description n'a pas.
# Un lead qui écrit « Publier et rapporter » parle évidemment du livrable — le
# contexte est dans la mission, pas dans l'intitulé de l'étape. Les mots de
# publication EXTERNE (`_EXTERNAL_PUBLISH_WORDS`) restent la seule échappatoire,
# et c'est bien eux qui portent le risque de faux positif.
_PUBLISH_CONTEXT_WORDS: tuple = (
    "livrable", "livraison", "workspace", "bilan",
    "site", "projet", "resultat", "résultat", "rapport", "rapporter", "app",
)
_EXTERNAL_PUBLISH_WORDS: tuple = (
    "tweet", "twitter", "article", "linkedin", "facebook", "instagram",
    "email", "mail", "telegram", "discord", "slack",
)


def publish_task_blocks(tool_name: str, task_desc: str) -> bool:
    """True si (tâche « publier le livrable ») ET (outil ≠ publish_mission_workspace)
    → NE PAS créditer (chemin principal ET fallback auto). Pur/testable."""
    d = (task_desc or "").lower()
    if "publi" not in d:
        return False
    if any(w in d for w in _EXTERNAL_PUBLISH_WORDS):
        return False
    if not any(w in d for w in _PUBLISH_CONTEXT_WORDS):
        return False
    return (tool_name or "").lower() != "publish_mission_workspace"


# ── LOT E — tâches de VÉRIFICATION NAVIGATEUR (run CéramiShop 2026-07-04) ────────
# « Vérifier le navigateur » a été cochée sur une pensée FABRIQUÉE (« flux complet
# vérifié ✅ ») 25 s AVANT le premier browser_navigate — lui-même bloqué par le
# SSRF. Aucun garde n'exigeait une preuve navigateur (contrairement au PYTEST GATE
# pour les tests). Règle : une tâche qui exige de VÉRIFIER dans le NAVIGATEUR n'est
# créditée QUE par une action `browser_*` réussie (les outils browser_* incluent
# browser_navigate/click ET browser_verify_local_project — tous préfixés browser_,
# donc tous comptés par execution_ledger.has_browser_action()).
# LOT Z3b — « Vérifier : filtre masquer serveurs OK » a été marquée FAITE par un
# simple `browser_navigate`, alors que la case n'avait jamais été cochée. Le garde
# n'a rien dit : il exigeait le mot « navigateur » dans l'intitulé. Or une étape de
# vérification d'interface ne se nomme jamais ainsi — elle dit « vérifier le tri »,
# « vérifier le filtre », « vérifier le thème ». Ce sont les VERBES d'interface qui
# la trahissent, pas le nom du navigateur.
_BROWSER_MARKERS: tuple = ("navigateur", "browser", "naviguer")


def browser_verify_task_blocks(tool_name: str, task_desc: str) -> bool:
    """True si (tâche de vérif NAVIGATEUR) ET (outil non-browser) → NE PAS créditer
    (chemin principal ET fallback auto). Pur/testable.

    LOT Z3b (run Sentinelle, 2026-08-15) — « Vérifier : filtre masquer serveurs OK »
    a été marquée FAITE par un `browser_navigate`. La case n'avait jamais été
    cochée. Ce garde ne disait rien pour deux raisons cumulées : l'intitulé ne
    contenait pas le mot « navigateur », et `browser_navigate` commence par
    `browser_` donc passait le dernier test.

    ⚠️ La première tentative de correctif fut d'allonger `_BROWSER_MARKERS` avec
    « filtre », « tri », « bouton »… : mauvais réflexe. Ces mots vivent aussi
    hors interface — « trier les résultats du benchmark » via `run_command` s'est
    retrouvé bloqué à tort. On ne devine pas l'intention avec du vocabulaire.

    La règle juste ne demande aucune liste : **naviguer n'est pas vérifier.**
    Ouvrir, faire défiler ou attendre, c'est se DÉPLACER — on change d'endroit,
    on ne constate rien. Une tâche qui dit « vérifier / tester / valider » exige
    donc un outil qui OBSERVE (`browser_dom_state`, `browser_get_content`) ou qui
    AGIT (`browser_evaluate`, un clic).

    ⚠️ Distinction apprise en cassant un test : ma première version bloquait TOUS
    les outils passifs, y compris `browser_dom_state`. Or lire le DOM prouve
    parfaitement qu'un compte a été créé — `test_browser_dom_state_matches_verifier`
    le garantit depuis longtemps, et il avait raison. Seul le déplacement pur est
    sans valeur probante.
    """
    d = (task_desc or "").lower()
    tool = (tool_name or "").lower()

    if not any(m in d for m in _BROWSER_MARKERS):
        return False
    # Exige une intention de vérification/test (pas un simple mot « navigateur »).
    if not any(v in d for v in ("vérif", "verif", "test", "valid", "confirm", "s'assur", "assur")):
        return False
    return not tool.startswith("browser_")


_INTERACTION_ACTION_MARKERS: tuple = (
    "saisi", "rempl", "taper", "cliquer", "soumet", "selection",
    "cocher", "ajouter",
)
# LOT M2 (run CaveÀVin 2026-08-14) — les marqueurs ci-dessus sont tous des VERBES.
# Un plan écrit les étapes au SUBSTANTIF : « inscription → connexion → ajout →
# liste ». « ajout » ne matche pas « ajouter », donc le garde ne s'est pas reconnu
# et `browser_navigate` (ouvrir la page d'accueil) a coché la vérification du
# parcours entier. Ces formes n'élargissent le garde QUE dans un contexte UI —
# hors UI le comportement est strictement inchangé, sans quoi une tâche « ajouter
# une ligne au CSV et vérifier » deviendrait incochable (elle exigerait
# browser_evaluate). Mesuré sur les 272 descriptions de tâches réelles :
# 8 → 10 bloquantes, les 2 gagnées sont MemoNest et CaveÀVin, 0 perdue.
_INTERACTION_ACTION_NOUNS: tuple = (
    "ajout", "inscription", "connexion", "saisie", "soumission",
    "creation", "connecter", "inscrire",
)
_UI_CONTEXT_MARKERS: tuple = (
    "navigateur", "browser", "naviguer", "page", "formulaire",
    "interface", "site", "web",
    # LOT Z3b (run Sentinelle) — « Vérifier : filtre masquer serveurs OK » a été
    # marquée FAITE par un simple `browser_navigate` : la case n'avait jamais été
    # cochée. Le garde INTERACTION-PROOF n'a rien dit parce que cette description
    # n'était pas reconnue comme un contexte d'interface — elle ne contient ni
    # « page » ni « navigateur ». Une étape d'UI ne se nomme jamais ainsi : elle
    # dit le COMPOSANT (« le filtre », « le tri », « le bouton », « le thème »).
    # Usage unique (browser_interaction_task_blocks) : élargir ici ne touche
    # aucun autre garde.
)
# Un parcours nommé : « inscription → connexion → ajout ». Chaque flèche annonce
# une étape que l'agent doit RÉELLEMENT franchir.
_JOURNEY_SEPARATORS: tuple = ("→", "->", "=>", " puis ", " ensuite ")


def task_names_a_journey(task_desc: str) -> bool:
    """True si la description enchaîne des étapes (flèche, « puis », « ensuite »).

    Pur/testable. Sert à exiger une preuve d'interaction FORTE : un parcours ne se
    coche pas en ouvrant la première page.
    """
    raw = str(task_desc or "")
    folded = _fold_plan_text(raw)
    return any(sep in raw or sep in folded for sep in _JOURNEY_SEPARATORS)


def task_has_ui_context(task_desc: str) -> bool:
    """True si la tâche parle d'une surface d'interface (navigateur, page, form…)."""
    folded = _fold_plan_text(task_desc)
    return any(marker in folded for marker in _UI_CONTEXT_MARKERS)
_INTERACTION_RESULT_MARKERS: tuple = (
    "verif", "confirm", "constat", "controle", "chang", "mise a jour",
    "affich", "appar", "resultat", "total", "compteur", "score", "dom",
)
_STRONG_INTERACTION_TOOLS: frozenset = frozenset({
    "browser_evaluate", "browser_verify_local_project",
})


def browser_interaction_task_blocks(tool_name: str, task_desc: str) -> bool:
    """Require a strong runtime proof for an action + observable-result task.

    LOT M2 — dans un contexte d'INTERFACE, deux élargissements (et deux seulement) :
    un parcours nommé (« inscription → connexion → ajout ») exige la preuve forte,
    et les étapes écrites au substantif comptent comme des actions. Hors interface,
    la règle historique s'applique mot pour mot : élargir là-bas rendrait des tâches
    non-web INCOCHABLES, puisque ce garde réclame `browser_evaluate`.
    """
    desc = _fold_plan_text(task_desc)
    strong = (tool_name or "").lower() in _STRONG_INTERACTION_TOOLS

    if task_has_ui_context(task_desc):
        if task_names_a_journey(task_desc):
            return not strong
        if (
            any(
                token in desc
                for token in _INTERACTION_ACTION_MARKERS + _INTERACTION_ACTION_NOUNS
            )
            and any(token in desc for token in _INTERACTION_RESULT_MARKERS)
        ):
            return not strong
        return False

    if not (
        any(token in desc for token in _INTERACTION_ACTION_MARKERS)
        and any(token in desc for token in _INTERACTION_RESULT_MARKERS)
    ):
        return False
    return not strong


_ARTIFACT_WRITE_TOOLS: frozenset = frozenset({
    "write_file", "edit_file", "create_file", "apply_patch", "apply_patches",
    "insert_at_anchor", "edit_by_lines", "str_replace", "multi_edit_file",
    "create_csv", "create_pdf", "create_docx", "create_xlsx", "create_pptx",
    "create_markdown", "create_html",
})
_ARTIFACT_EXTENSIONS: tuple = (
    ".csv", ".md", ".pdf", ".html", ".htm", ".css", ".js", ".py",
    ".json", ".xlsx", ".docx", ".pptx", ".txt",
)


def artifact_target_task_blocks(tool_name: str, task_desc: str, tool_args: dict) -> bool:
    """Block plan credit when explicit task and write-target extensions conflict."""
    if (tool_name or "").lower() not in _ARTIFACT_WRITE_TOOLS:
        return False
    task_exts = {ext for ext in _ARTIFACT_EXTENSIONS if ext in _fold_plan_text(task_desc)}
    if not task_exts or not isinstance(tool_args, dict):
        return False
    target_text = " ".join(
        str(tool_args.get(key) or "").lower()
        for key in ("path", "file_path", "output_path", "destination", "filename")
    )
    target_exts = {ext for ext in _ARTIFACT_EXTENSIONS if ext in target_text}
    return bool(target_exts and task_exts.isdisjoint(target_exts))


def _browser_passive_tool_can_complete_task(tool_name: str, task_desc: str) -> bool:
    """Autorise seulement certaines tâches de plan pour les outils browser passifs."""
    desc = _fold_plan_text(task_desc)
    if tool_name == "browser_navigate":
        return any(tok in desc for tok in (
            "naviguer", "aller", "ouvrir", "accéder", "acceder", "visiter",
            "vérifier", "verifier", "accessible", "opérationnel", "operationnel",
        ))
    if tool_name in {
        "browser_dom_state", "browser_screenshot", "browser_screenshot_labels",
        "browser_page_info", "browser_get_content", "browser_get_text",
        "browser_frames", "browser_frame_content",
    }:
        # Exclure les tâches qui mentionnent des contextes non-browser
        if any(excl in desc for excl in ("email", "mail", "spam", "sms", "téléphone", "telephone", "appel")):
            return False
        return any(tok in desc for tok in (
            "trouver", "identifier", "repérer", "reperer", "inspecter",
            "voir", "lire", "analyser", "localiser", "détecter", "detecter",
            "vérifier", "verifier", "confirmer",
        ))
    if tool_name == "browser_scroll":
        return any(tok in desc for tok in ("scroller", "scroll", "charger plus"))
    return False


def _read_only_discovery_tool_can_complete_task(tool_name: str, task_desc: str) -> bool:
    desc = (task_desc or "").lower()
    if tool_name == "list_document_models":
        return any(tok in desc for tok in (
            "lister", "liste", "consulter", "identifier", "choisir", "selectionner",
            "modeles", "modeles documentaires",
        )) and not any(tok in desc for tok in (
            "generer", "creer", "produire", "rediger", "ecrire", "livrer",
        ))
    if tool_name == "get_time":
        return any(tok in desc for tok in ("heure", "date", "horaire", "time"))
    if tool_name in {"health_check", "process_status"}:
        return any(tok in desc for tok in (
            "statut", "status", "santé", "sante", "health",
            "vérifier", "verifier", "accessible", "opérationnel", "operationnel",
            "disponible", "fonctionne", "running", "alive", "check",
            "lancer", "démarrer", "demarrer", "serveur", "server", "port",
        ))
    if tool_name in {"web_fetch", "web_search", "web_search_brave", "browser_search_google"}:
        if any(tok in desc for tok in ("échanger", "echanger", "discussion", "conversation", "discuter", "parler", "envoyer")):
            return False
        return any(tok in desc for tok in (
            "vérifier", "verifier", "chercher", "rechercher", "trouver",
            "identifier", "inspecter", "lire", "consulter", "analyser",
            "comparer", "regarder",
        ))
    return True


def sourced_web_research_task_proven(
    tool_name: str, task_desc: str, observation_content: str
) -> bool:
    """Require concrete source URLs before completing a sourced research task.

    This applies to every tool, including delegated reports and parallel_tools:
    prose saying that research happened is not evidence. A delegated worker can
    still satisfy the task when its report contains the requested URLs.
    """
    desc = _fold_plan_text(task_desc)
    if not any(token in desc for token in (
        "recherch", "chercher", "trouver", "identifier", "compar",
    )):
        return True
    if not any(token in desc for token in (
        "sourc", "url", "web", "internet", "institutionnel",
    )):
        return True

    # Read the first quantity after the research verb, avoiding an "Étape 3"
    # prefix. Written French quantities cover the normal plan vocabulary.
    start = max(desc.find("recherch"), desc.find("chercher"), desc.find("trouver"))
    tail = desc[start:] if start >= 0 else desc
    words = tail.replace(":", " ").replace("-", " ").split()
    word_numbers = {"un": 1, "une": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5}
    required = 1
    for word in words[:8]:
        cleaned = word.strip(".,;()[]{}")
        if cleaned.isdigit():
            required = max(1, min(5, int(cleaned)))
            break
        if cleaned in word_numbers:
            required = word_numbers[cleaned]
            break

    urls: set[str] = set()
    for raw in (observation_content or "").replace("\n", " ").split():
        candidate = raw.strip("<>[](){}.,;:'\"")
        if candidate.startswith(("https://", "http://")):
            urls.add(candidate)
    if len(urls) < required:
        return False

    requires_page_read = any(token in desc for token in (
        "reellement consulte", "pages consulte", "pages lues",
        "sources consulte", "ouvrir les sources", "lire les pages",
    ))
    if not requires_page_read:
        return True
    tool = (tool_name or "").lower()
    if tool in {"web_fetch", "browser_navigate", "browser_get_content", "browser_read"}:
        return True
    proof_text = _fold_plan_text(observation_content)
    return any(marker in proof_text for marker in (
        "web_fetch", "browser_navigate", "browser_get_content", "browser_read",
    ))


# ── Auto-complétion « synthèse » : tâches réalisées par le FINAL lui-même ────
# (résumer/présenter/informer/confirmer…) — SAUF si la tâche implique un effet
# de bord réel (envoi mail/discord, déploiement, génération de doc…), auquel cas
# elle exige une vraie action et n'est PAS cochée par le FINAL seul.
_SYNTH_KW = {
    "synthétis", "synthetis", "résumer", "resumer", "récapitul", "recapitul",
    "synthèse", "synthese", "conclur", "répondre", "repondre",
    "fournir le bilan", "donner le bilan", "bilan final",
    "fournir une réponse", "présenter les résultats", "presenter les resultats",
    "confirm", "valider", "vérifi", "verifi",
    "informer", "inform", "notifier", "communiquer", "communique",
    "avertir", "signaler", "dire à", "dire a",
    # V2.1 fix prod 2026-05-19 : tâches "présenter le rapport / réponse à l'utilisateur"
    # Logs montraient une étape 5 "Présenter le rapport complet à l'utilisateur"
    # qui restait SKIP malgré 4 tool steps complétés et un Action: final.
    "présenter le", "presenter le",
    "présenter la", "presenter la",
    "présenter au", "presenter au",
    "présenter à", "presenter a",
    "rapport final", "rapport complet",
    "résumé final", "resume final",
    "donner le résumé", "donner le resume",
    "à l'utilisateur", "a l'utilisateur",
    "donner la réponse", "donner la reponse",
    "afficher", "exposer", "expliquer",
    "livrer", "remettre", "transmettre",
    "écrire la réponse", "ecrire la reponse",
    # Lot 5 (2026-06-26) — tâches de CLÔTURE des missions/leads : « Finaliser »,
    # « Rendre le résultat final », « Livrer le texte final » sont réalisées par le
    # FINAL lui-même. Observé runtime : le lead/workers bouclaient des dizaines de
    # fois sur PLAN GUARD car ces étapes restaient SKIP. (Side-effect block reste actif.)
    "finalis",
    "rendre le résultat", "rendre le resultat", "rendre la réponse", "rendre la reponse",
    "rendre le rapport", "rendre le texte", "rendre le résumé", "rendre le resume",
    "rendre le livrable", "résultat final", "resultat final", "texte final",
    # Lot 5 (B′, 2026-06-26) — tâches "passerelle" des plans AUTO-générés des workers :
    # « Retourner le rapport via FINAL » est littéralement réalisée PAR le FINAL.
    "retourner le rapport", "retourner le résultat", "retourner le resultat",
    "retourner la réponse", "retourner la reponse", "via final", "rapport via final",
    # 4E (2026-06-15) — tâches d'INTERACTION UTILISATEUR : demander une
    # approbation/validation ou attendre un retour humain sont réalisées par
    # le FINAL lui-même (le message à l'utilisateur EST le livrable). Observé
    # runtime : un MCP non-curated bouclait car « demander approbation manuelle »
    # restait SKIP → PLAN GUARD bloquait un FINAL pourtant légitime.
    # Clés multi-mots volontaires (pas de verbe nu) → zéro sur-match ; le
    # garde-fou _SYNTH_SIDE_EFFECT_BLOCK_KW reste actif (mail/discord/deploy…).
    "demander approbation", "demander l'approbation", "demander une approbation",
    "demander l approbation", "demander la validation", "demander validation",
    "demander confirmation", "demander la confirmation",
    "demander l'autorisation", "demander autorisation",
    "approbation manuelle", "approbation requise", "approbation utilisateur",
    "attendre approbation", "attendre l'approbation", "attendre la validation",
    "attendre le feu vert", "attendre la confirmation", "attendre le retour",
    # #2 (2026-06-30) — tâches de RÉDACTION des plans AUTO-générés des workers :
    # « Rédiger le paragraphe structuré », « Synthétiser en un paragraphe » sont
    # réalisées PAR le texte FINAL du worker (son livrable EST sa réponse). Observé
    # runtime (jeux_cultes) : ces étapes restaient SKIP → `[PLAN GUARD] FINAL premature
    # bloque` → boucles + THOUGHT leaks. ⚠️ PAS de « écri » nu : trop large (« écrire le
    # fichier… » doit passer par write_file). Le garde _SYNTH_SIDE_EFFECT_BLOCK_KW
    # (renforcé avec fichier/workspace/.md…) garde « Rédiger un guide dans workspace/x.md » → False.
    "rédig", "redig", "rédaction", "redaction",
    "paragraphe", "section", "texte structuré", "texte structure",
}
_SYNTH_SIDE_EFFECT_BLOCK_KW = {
    "email", "mail", "courriel", "telegram", "whatsapp",
    "discord", "pdf", "docx", "xlsx", "zip", "archive",
    "upload", "déployer", "deployer", "déploi", "deploi",
    "publier", "poster", "envoyer", "envoie", "envoi", "send", "joindre",
    "attacher",
    # #2 (2026-06-30) — une tâche qui NOMME un fichier de sortie exige une vraie
    # écriture (write_file/create_*), JAMAIS un simple FINAL. Indispensable depuis
    # l'ajout des clés de rédaction ci-dessus : sans ça, « Rédiger un guide dans
    # workspace/jeux_cultes.md » (qui contient « rédig ») serait faussement cochée.
    "fichier", "workspace", "sauvegarder", "enregistrer", "write_file",
    ".md", ".txt", ".json", ".csv", ".html", ".yaml", ".yml",
}


def final_requires_operational_proof(description: str) -> bool:
    """Return True when prose alone cannot complete the plan task.

    FINAL is a valid deliverable for synthesis and reporting tasks. It is not
    execution evidence for a named tool, a publication, browser verification,
    or an interactive browser result.
    """
    return any((
        tool_explicit_task_blocks("FINAL", description),
        delegation_task_blocks("FINAL", description),
        publish_task_blocks("FINAL", description),
        browser_verify_task_blocks("FINAL", description),
        browser_interaction_task_blocks("FINAL", description),
    ))


def final_fulfills_task(description: str) -> bool:
    """True si la tâche est réalisée par le FINAL lui-même (synthèse/rapport)
    et N'implique PAS d'effet de bord (envoi/déploiement/génération de doc)."""
    dl = (description or "").lower()
    if final_requires_operational_proof(description):
        return False
    return (
        any(_kw in dl for _kw in _SYNTH_KW)
        and not any(_kw in dl for _kw in _SYNTH_SIDE_EFFECT_BLOCK_KW)
    )


# ── Lot 5 (B′) — PLAN GUARD : relaxation en contexte MISSION ───────────────────────
# Le plan d'un worker/lead de mission est AUTO-généré (échafaudage du LLM), jamais un
# contrat utilisateur. Le PLAN GUARD existe pour empêcher l'abandon du plan de
# l'UTILISATEUR — il n'a guère de légitimité sur un plan que le worker s'est inventé.
# Conséquences observées (log 2026-06-26) : tâches "passerelle" sans outil dédié
# (« Récupérer les résultats fusionnés ») + mauvais rattachement outil↔tâche
# (« Étape 2 : Deep research » SKIP alors que deep_research a tourné) → FINAL bloqué
# en boucle. On relâche SI la mission a vraiment travaillé (livrable OU recherche) ;
# un worker qui FINAL sans RIEN avoir fait reste nudgé.
_MISSION_DELIVERABLE_TOOLS = frozenset({
    "write_file", "create_file", "edit_file", "append_file",
    "create_pdf", "create_docx", "create_xlsx", "create_pptx",
    "generate_studio_document",
    "generate_studio_documents",
    "apply_document_edit", "convert_library_document", "export_library_document",
    "revise_studio_document",
    "create_document", "create_site", "create_website", "generate_image",
    "delegate_and_wait",
})
_MISSION_RESEARCH_TOOLS = frozenset({
    "web_search", "web_search_brave", "deep_research", "web_fetch",
    "web_crawl", "browser_search_google", "read_file", "read_document",
})


def mission_progress_proven(successful_tools) -> bool:
    """True si la mission a produit un livrable OU mené une vraie recherche/lecture.
    Sert au PLAN GUARD : un worker qui a réellement travaillé peut conclure ; un
    worker qui FINAL sans RIEN avoir exécuté reste bloqué (anti-hallucination)."""
    if not successful_tools:
        return False
    s = set(successful_tools)
    return bool((_MISSION_DELIVERABLE_TOOLS | _MISSION_RESEARCH_TOOLS) & s)


# Tâches de « suivi d'une mission de fond » (poll status/result). Quand une mission
# a été LANCÉE ce tour, ces étapes ne sont PAS du travail à faire maintenant (le
# sous-agent tourne seul) → on les auto-complète pour ne pas forcer le chat à
# baby-sitter (Lot 5 — C). Ciblé : ne matche QUE le vocabulaire de suivi de mission.
_MISSION_TRACK_KW = (
    "mission_status", "mission_result", "suivre la mission",
    "suivre l'avancement", "suivi de la mission", "statut de la mission",
)


def is_mission_tracking_task(description: str) -> bool:
    """True si la tâche consiste à SUIVRE une mission de fond (poll status/result)."""
    dl = (description or "").lower()
    return any(_kw in dl for _kw in _MISSION_TRACK_KW)


# Tâches d'un plan qui consistent à DÉLÉGUER / lancer des sous-agents. Une délégation
# RÉUSSIE (delegate_and_wait) les accomplit littéralement → le PLAN GUARD ne doit plus
# les voir comme SKIP. Vocabulaire non ambigu ; « worker » exige un verbe pour éviter
# de matcher du vocabulaire métier (un "worker thread" dans un sujet de recherche, etc.).
_DELEGATION_KW = (
    "delegate_and_wait", "déléguer", "deleguer", "delegate",
    "sous-agent", "sous agent", "sous-mission", "sous mission",
)
_DELEGATION_WORKER_VERBS = (
    "lancer", "créer", "creer", "déléguer", "deleguer", "parallèle", "parallele", "parallel",
)


def delegation_task_fulfilled(description: str) -> bool:
    """True si la tâche = « lancer/déléguer à des sous-agents/workers » (réalisée par
    un delegate_and_wait réussi). « Lancer le serveur » → False ; « Lancer 3
    sous-agents en parallèle » → True. `worker` n'est retenu qu'avec un verbe d'action."""
    dl = (description or "").lower()
    if any(_kw in dl for _kw in _DELEGATION_KW):
        return True
    if "worker" in dl and any(_v in dl for _v in _DELEGATION_WORKER_VERBS):
        return True
    return False


_DELEGATION_PROOF_TOOLS: frozenset = frozenset({
    "delegate_and_wait", "delegate_task", "delegate_task_bg", "create_mission",
})


def delegation_task_blocks(tool_name: str, task_desc: str) -> bool:
    """A delegation plan step requires a successful delegation tool call."""
    if not delegation_task_fulfilled(task_desc):
        return False
    return (tool_name or "").lower() not in _DELEGATION_PROOF_TOOLS


def document_catalog_task_origin(description: str) -> str:
    """Return the exact catalogue origin named by a listing plan task."""
    desc = (description or "").lower()
    if not any(token in desc for token in (
        "list", "catalog", "recens", "sélectionn", "selectionn", "récupér", "recuper",
    )):
        return ""
    if any(token in desc for token in ("personnalis", "custom")):
        return "custom"
    if any(token in desc for token in ("intégr", "integr", "natif", "builtin", "de base")):
        return "builtin"
    return ""


def document_plan_tool_can_complete_task(
    tool_name: str,
    task_desc: str,
    *,
    tool_kind: str = "",
    required_kinds: tuple[str, ...] = (),
    compound_workflow: bool = False,
) -> bool:
    """Prevent one document call from completing a batch or verification task."""
    if compound_workflow and document_workflow_task_operation(task_desc):
        return False
    if (
        compound_workflow
        and tool_name in {"list_document_models", "list_templates"}
        and document_catalog_task_origin(task_desc)
    ):
        # Exact origin/limit/sort evidence is reconciled centrally by ReAct.
        return False
    if tool_name not in {
        "generate_studio_document", "generate_studio_documents", "revise_studio_document", "create_pdf",
        "create_invoice_pdf", "create_from_template",
    }:
        return True
    # A batch reports one exact sub-result per document. Its outer success may
    # be partial, so only the manifest reconciler may credit plan tasks.
    if tool_name == "generate_studio_documents":
        return False
    desc = (task_desc or "").lower()
    if any(token in desc for token in (
        "verif", "vérif", "valid", "control", "contrôl", "relire", "inspect",
    )):
        return False
    if len(required_kinds) > 1:
        return False
    if (
        tool_name in {"generate_studio_document", "revise_studio_document"}
        and required_kinds
        and tool_kind
    ):
        return required_kinds[0] == tool_kind.strip().lower().replace("-", "_")
    return True


# Side-effects EXTERNES : jamais bradés par la seule livraison d'un fichier. Miroir
# local de _EXTERNAL_SIDE_EFFECT_KW (plan_evidence) — plan_progress reste stdlib pur
# (pas d'import cross-module), donc on duplique ce petit set volontairement.
_FINALIZE_SIDE_EFFECT_KW: frozenset = frozenset({
    "mail", "email", "e-mail", "envoy", "expédi", "expedi", "déploi", "deploi",
    "publi", "push", "slack", "discord", "telegram", "tweet", "poster",
})


def mission_deliverable_finalizable(
    task_plan, *, artifact_written: bool, target_reread: bool = False
) -> bool:
    """True si le LEAD d'une mission peut CONCLURE de façon DÉTERMINISTE (sans FINAL LLM).

    Le livrable CIBLE doit être sur disque (`artifact_written`). Ensuite, deux voies :
      - Cas 1 (historique) : plan NON-VIDE entièrement complété (relecture/vérif créditée
        cf. #3, ou aucune tâche de vérif au plan).
      - Cas 2 (2026-07-01) : le livrable a été RELU (`target_reread`) — preuve réelle de
        vérification, INDÉPENDANTE de l'état/existence du plan. Couvre le worker qui
        n'émet AUCUN plan (run thés) ou dont la tâche de vérif n'a pas été créditée.

    Garde-fou dans TOUS les cas : on ne conclut JAMAIS si une tâche à EFFET DE BORD
    EXTERNE (envoi mail, déploiement, publication…) reste ouverte — le fichier sur
    disque ne prouve pas cet envoi.

    But : éviter que le modèle (DeepSeek) leake son raisonnement en FINAL après coup —
    ce qui retarde le `done` et rend `mission_status` trompeur. Conservateur : sans
    relecture ET sans plan complet ⇒ False (on laisse le FINAL LLM normal).
    Duck-typé : `task_plan` = itérable d'objets ayant `.completed`/`.description`.
    """
    if not artifact_written:
        return False
    # Garde-fou effet de bord externe : une tâche « envoyer/publier… » non cochée bloque.
    for t in task_plan or []:
        if not getattr(t, "completed", False):
            _desc = (getattr(t, "description", "") or "").lower()
            if any(kw in _desc for kw in _FINALIZE_SIDE_EFFECT_KW):
                return False
    # Cas 1 : plan non-vide entièrement complété.
    if task_plan and all(getattr(t, "completed", False) for t in task_plan):
        return True
    # Cas 2 : livrable écrit ET relu (indépendant du plan).
    return bool(target_reread)


def mission_evidence_finalizable(
    task_plan,
    *,
    delivery_proven: bool,
    delegation_complete: bool,
    tests_required: bool,
    tests_green: bool,
    browser_required: bool,
    browser_proven: bool,
) -> bool:
    """Allow multi-file closure from authoritative proofs instead of stale plan ticks.

    This complements the historical single-target path. It stays complete-only:
    publication, delegation, required tests and required browser proof must all be
    satisfied. Unresolved external side effects still block closure; publication is
    the sole exception because `delivery_proven` is its authoritative proof.
    """
    if not delivery_proven or not delegation_complete:
        return False
    if tests_required and not tests_green:
        return False
    if browser_required and not browser_proven:
        return False

    for task in task_plan or []:
        if getattr(task, "completed", False):
            continue
        desc = (getattr(task, "description", "") or "").lower()
        external = [kw for kw in _FINALIZE_SIDE_EFFECT_KW if kw in desc]
        if not external:
            continue
        if all(kw == "publi" for kw in external):
            continue
        return False
    return True


def worker_evidence_finalizable(
    task_plan,
    *,
    assigned_files_ready: bool,
    tests_required: bool,
    tests_green: bool,
) -> bool:
    """Allow a contracted worker to close from disk and test evidence.

    Worker plans are model-generated scaffolding and can retain stale ticks after
    CodeAgent has filled every owned file. The owned files are the worker delivery;
    a green run is additionally mandatory when the worker owns tests. External side
    effects remain non-negotiable and therefore still block closure.
    """
    if not assigned_files_ready:
        return False
    if tests_required and not tests_green:
        return False
    for task in task_plan or []:
        if getattr(task, "completed", False):
            continue
        desc = (getattr(task, "description", "") or "").lower()
        if any(kw in desc for kw in _FINALIZE_SIDE_EFFECT_KW):
            return False
    return True


# ── LOT Z11 — la page qu'on ne regarde pas est la page qu'on bâcle ───────────
#
# Mesuré sur les TROIS runs web multi-pages, sans exception :
#
#     Palier   app.html    100 %  ·  index.html    4 %   (0 ouverture)
#     Tanière  espace.html 100 %  ·  index.html   50 %   (0 ouverture)
#     Marée    espace.html 100 %  ·  index.html   31 %   (0 ouverture, 9 sur espace)
#
# La mission ouvre la page où sont les fonctionnalités, la teste à fond, et ne
# regarde jamais la page publique. Le lot D exige DÉJÀ une jambe navigateur, mais
# il se satisfait d'UNE navigation : ouvrir une page sur deux le contente.
#
# Z7 rend pourtant le fait, nommément (« index.html n'a que 4/13 de ses classes
# stylées »). Sur Tanière la mission a corrigé ; sur Marée elle a ignoré. Un
# constat seul ne suffit donc pas — DÉCISION UTILISATEUR (2026-08-16) : forcer
# l'ouverture de CHAQUE page produite, comme pour le CodeAgent en Z1b.

_PAGE_SUFFIXES: tuple = (".html", ".htm")


_PAGE_INDEX_DEFAUT: str = "index.html"


def _page_key(chemin: str) -> str:
    """Nom de fichier seul, minuscule, sans query ni ancre.

    Une page est produite comme `maree/index.html` et ouverte comme
    `http://localhost:8081/index.html?v=2#top` : seul le basename les relie.

    CORRECTIF (run Fournil, 2026-08-16) — une URL qui se termine par `/` sert
    `index.html` : tout serveur statique le fait, y compris `serve_website`. La
    mission avait ouvert `http://localhost:8081/` — donc bien la page d'accueil —
    et ce basename vide ne correspondait à rien. Le garde a répondu « index.html
    ET commande.html jamais ouvertes » : faux des deux côtés, donc muet là où il
    aurait dû bloquer (`commande.html` n'a réellement jamais été ouverte).
    """
    brut = str(chemin or "").strip().replace("\\", "/")
    for coupure in ("?", "#"):
        if coupure in brut:
            brut = brut.split(coupure, 1)[0]
    if not brut:
        return ""
    # URL : isoler le CHEMIN, sinon `http://localhost:8081` (sans slash final,
    # forme tout aussi courante) rendrait « localhost:8081 » comme nom de page.
    if "://" in brut:
        apres_schema = brut.split("://", 1)[1]
        chemin_url = apres_schema.split("/", 1)[1] if "/" in apres_schema else ""
        if not chemin_url.strip("/"):
            return _PAGE_INDEX_DEFAUT
        brut = chemin_url
    nom = brut.rsplit("/", 1)[-1].lower()
    # Racine servie (`…/`, `…/sous/`) → page d'accueil : tout serveur statique
    # sert `index.html`, y compris `serve_website`.
    return nom or _PAGE_INDEX_DEFAUT


def pages_never_opened(produced: object, visited: object) -> list:
    """Les pages HTML produites qu'aucune navigation n'a ouvertes.

    Pur : deux listes en entrée, une liste triée en sortie. L'appelant fournit
    ce qu'il a écrit (ledger, contrat) et ce qu'il a navigué (historique).

    Rend `[]` dès qu'il n'y a rien à reprocher — aucune page produite, une seule
    page (le lot D suffit alors), ou toutes vues.
    """
    pages = {
        _page_key(p) for p in (produced or [])
        if _page_key(p).endswith(_PAGE_SUFFIXES)
    }
    if len(pages) < 2:
        # Une page unique est déjà couverte par la jambe navigateur du LOT D :
        # ce garde ne parle QUE du cas multi-pages, celui qu'on a mesuré.
        return []
    vues = {_page_key(u) for u in (visited or [])}
    return sorted(pages - vues)


def unseen_pages_reason(manquantes: object) -> str:
    """La justification à rendre au gate navigateur. Vide si rien ne manque."""
    restantes = [str(p) for p in (manquantes or []) if str(p).strip()]
    if not restantes:
        return ""
    liste = ", ".join(f"`{p}`" for p in restantes[:6])
    reste = len(restantes) - 6
    if reste > 0:
        liste += f" (+{reste})"
    return (
        f"page(s) jamais ouverte(s) au navigateur : {liste} — une page que "
        "personne ne regarde est une page que personne ne corrige"
    )
