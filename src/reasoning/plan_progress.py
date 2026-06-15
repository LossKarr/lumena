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
})


def _browser_passive_tool_can_complete_task(tool_name: str, task_desc: str) -> bool:
    """Autorise seulement certaines tâches de plan pour les outils browser passifs."""
    desc = (task_desc or "").lower()
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


# ── Auto-complétion « synthèse » : tâches réalisées par le FINAL lui-même ────
# (résumer/présenter/informer/confirmer…) — SAUF si la tâche implique un effet
# de bord réel (envoi mail/discord, déploiement, génération de doc…), auquel cas
# elle exige une vraie action et n'est PAS cochée par le FINAL seul.
_SYNTH_KW = {
    "synthétis", "synthetis", "résumer", "resumer", "récapitul", "recapitul",
    "synthèse", "synthese", "conclur", "répondre", "repondre",
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
}
_SYNTH_SIDE_EFFECT_BLOCK_KW = {
    "email", "mail", "courriel", "telegram", "whatsapp",
    "discord", "pdf", "docx", "xlsx", "zip", "archive",
    "upload", "déployer", "deployer", "déploi", "deploi",
    "publier", "poster", "envoyer", "envoie", "envoi", "send", "joindre",
    "attacher",
}


def final_fulfills_task(description: str) -> bool:
    """True si la tâche est réalisée par le FINAL lui-même (synthèse/rapport)
    et N'implique PAS d'effet de bord (envoi/déploiement/génération de doc)."""
    dl = (description or "").lower()
    return (
        any(_kw in dl for _kw in _SYNTH_KW)
        and not any(_kw in dl for _kw in _SYNTH_SIDE_EFFECT_BLOCK_KW)
    )
