"""LEDGER guard — cœur de décision PUR, extrait de react.py.

Le guard ExecutionLedger (FINAL + heuristiques H2/H3) est profondément couplé à
l'instance ReActLoop (ledger, exec_state, history, flux de contrôle). On applique
donc le pattern « decision-core » déjà validé pour `hallucination_retry_query` :

- Ici : la **détection** (patterns de claim), les **helpers** (outils réussis,
  cible H3) et les **3 fonctions de décision** — toutes PURES, renvoient la
  requête de retry (`str`) ou `None`.
- Côté react : la **coquille** — calcul des booléens (appels ledger,
  `mission_expects_mutation`), puis effets de bord (log, flags, `history.pop()`,
  `_finish_iteration`, `continue`).

Le module ne dépend QUE de la stdlib → aucun import circulaire avec react.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, List, Optional

# Patterns « claim d'action » du LEDGER guard (texte brut, déjà normalisé en
# minuscules + apostrophes ASCII côté react). Conservés à l'identique — on NE
# fusionne PAS avec _HALLUCINATION_CLAIM_PATTERNS (ce serait un changement de
# comportement, réservé à une éventuelle phase « correction »).
_LEDGER_CLAIM_PATTERNS = (
    "j'ai créé", "j'ai crée", "j'ai envoyé", "j'ai envoye",
    "j'ai écrit", "j'ai modifié", "j'ai configuré", "j'ai planifié",
    "j'ai enregistré", "j'ai sauvegardé", "j'ai généré",
    "c'est fait", "c'est envoyé", "c'est créé",
    "i created", "i wrote", "i sent", "i saved", "i configured",
    "fichier créé", "fichier écrit", "message envoyé",
    # Phase I-8 (Fix AF) : formes passives + « avec succès » +
    # install/activation/test — trous observés runtime
    # 2026-06-11 04:34 (« installé et testé avec succès » /
    # « a été installé » sans AUCUN outil appelé).
    "j'ai installé", "j'ai installe", "j'ai activé",
    "j'ai active", "j'ai testé", "j'ai teste",
    "j'ai déployé", "j'ai deploye",
    "a été installé", "a ete installe",
    "a été créé", "a ete cree",
    "a été configuré", "a ete configure",
    "a été activé", "a ete active",
    "a été testé", "a ete teste",
    "a été envoyé", "a ete envoye",
    "a été généré", "a ete genere",
    "a été déployé", "a ete deploye",
    "installé avec succès", "installe avec succes",
    "activé avec succès", "active avec succes",
    "créé avec succès", "cree avec succes",
    "configuré avec succès", "configure avec succes",
    "testé avec succès", "teste avec succes",
    "envoyé avec succès", "envoye avec succes",
    "installé et testé", "installe et teste",
    "installé et activé", "installe et active",
    "test effectué", "test effectue",
    "test réussi", "test reussi",
    "i installed", "successfully installed",
    "installed and tested", "installed successfully",
)


def ledger_text_claims_action(final_text_normalized: str) -> bool:
    """True si le texte FINAL (déjà lower + apostrophes ASCII) affirme une action."""
    return any(p in final_text_normalized for p in _LEDGER_CLAIM_PATTERNS)


def compute_effective_successful_tools(history: Iterable[Any]) -> List[str]:
    """Liste des outils RÉELLEMENT réussis, en dépliant `parallel_tools` sur ses
    sous-outils. Pur : ne lit que la structure des steps (action/observation)."""
    eff: List[str] = []
    for _h in history:
        if not (_h.action and _h.observation and _h.observation.success):
            continue
        _tn = _h.action.tool_name or ""
        if _tn == "parallel_tools":
            _subs = getattr(_h.observation, "sub_results", ()) or ()
            for _sub in _subs:
                if not getattr(_sub, "success", False):
                    continue
                _sn = getattr(_sub, "tool_name", "") or ""
                if _sn:
                    eff.append(_sn)
            # pas de sub_results -> agrégateur ignoré (pas ajouté)
        elif _tn:
            eff.append(_tn)
    return eff


def extract_h3_target_hint(original_query: str) -> Optional[str]:
    """Extrait une cible explicite (#salon ou fichier.ext) de la requête. Pur."""
    _channel_match = re.search(r'#([\w\-]{2,32})', original_query)
    if _channel_match:
        return _channel_match.group(1)
    _file_match = re.search(
        r'[\w\-]+\.(py|js|ts|html|css|json|md|txt|yaml|toml)', original_query
    )
    if _file_match:
        return _file_match.group(0)
    return None


def ledger_final_guard_query(
    *,
    claims_action: bool,
    runtime_claim: bool,
    has_any_mutation: bool,
    readonly_exoneration: bool,
    real_action_done: bool,
    original_query: str,
    led_tools: List[str],
) -> Optional[str]:
    """FINAL guard : claim d'action mais AUCUNE mutation ledger (et pas exonéré).
    Retourne la requête de retry, ou None si on ne bloque pas."""
    if (claims_action and not runtime_claim
            and not has_any_mutation
            and not readonly_exoneration
            and not real_action_done):
        return (
            f"Requête originale: {original_query}\n\n"
            "⛔ Tu as déclaré avoir accompli une action (création, envoi, écriture…) "
            "mais le journal d'exécution ne contient AUCUNE mutation réussie.\n\n"
            f"Outils exécutés avec succès: {', '.join(led_tools)}\n\n"
            "Tu DOIS appeler l'outil approprié et ATTENDRE le résultat "
            "avant de conclure avec FINAL."
        )
    return None


def ledger_h2_guard_query(
    *,
    claims_action: bool,
    runtime_claim: bool,
    has_any_mutation: bool,
    expected_family_nonempty: bool,
    has_mutation_in_expected_family: bool,
    original_query: str,
    guard_intent: Any,
    led_tools: List[str],
) -> Optional[str]:
    """H2 : des mutations existent mais AUCUNE dans la famille d'intent attendue."""
    if (claims_action and not runtime_claim and has_any_mutation
            and expected_family_nonempty
            and not has_mutation_in_expected_family):
        return (
            f"Requête originale: {original_query}\n\n"
            f"⛔ Tu as déclaré avoir agi pour une tâche '{guard_intent}' "
            f"mais aucun outil de la catégorie attendue n'a été exécuté.\n\n"
            f"Outils exécutés: {', '.join(led_tools)}\n\n"
            "Appelle l'outil approprié avant de conclure."
        )
    return None


def ledger_h3_guard_query(
    *,
    claims_action: bool,
    runtime_claim: bool,
    has_any_mutation: bool,
    target_hint: Optional[str],
    has_mutation_for_target: bool,
    original_query: str,
    led_tools: List[str],
) -> Optional[str]:
    """H3 (repair léger) : cible explicite mentionnée mais aucune mutation pour elle."""
    if (claims_action and not runtime_claim and has_any_mutation
            and target_hint
            and not has_mutation_for_target):
        return (
            f"Requête originale: {original_query}\n\n"
            f"⚠️ Tu affirmes avoir agi, et une mutation a bien eu lieu, "
            f"mais aucune action ne semble concerner la cible « {target_hint} ».\n\n"
            f"Outils exécutés: {', '.join(led_tools)}\n\n"
            "Vérifie que tu as bien traité la bonne cible, "
            "puis agis dessus si ce n'est pas encore fait avant de conclure."
        )
    return None
