"""RF-2 — preuves dediees de l'extraction de la lecture d'observations.

Lot RF-2 du plan `plans/REFACTOR_REACT_DEDICATED_PLAN_2026-08-27.md` :
12 symboles (7 fonctions pures + 5 constantes) ont quitte `react.py` pour
`src/reasoning/observation_synthesis.py`.

Deux choses que ces tests protegent au-dela de l'identite des reexports :

  * **La frontiere avec RF-6.** `mission_write_path_exists` et
    `mission_write_targets_existing_deliverable` etaient dans le perimetre
    LITTERAL du plan (« existence des livrables »), mais l'audit les a exclues :
    ce sont les auxiliaires de `ReActLoop._mission_overwrite_gate`, qui lit
    `self._mission_workspace_meta()`. Les mettre dans un module de synthese
    d'observation aurait cree un proprietaire faux. Un test verifie qu'elles
    sont bien restees.
  * **Le risque principal nomme par le plan** : ne pas confondre « meme outil »
    et « meme progression ». Une signature differente doit remettre les
    compteurs a zero.

Et le garde issu de l'incident RF-1 : les imports intra-paquet doivent etre
relatifs, sinon un processus lance depuis la racine du depot meurt en silence.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parents[2]
REACT = RACINE / "src" / "reasoning" / "react.py"
NOUVEAU = RACINE / "src" / "reasoning" / "observation_synthesis.py"

FONCTIONS = (
    "_obs_looks_tabular",
    "_obs_looks_like_test_result",
    "_should_repair_incomplete_final",
    "_phase27_mcp_observation_guidance",
    "_synthesize_response_from_observation",
    "_synthesize_mission_response_from_evidence",
    "read_stagnation_action",
    # ── Lot RF-9a (2026-08-28) — premiere feuille de `_run_internal` ──
    # « Ingestion d'observation » est l'une des six feuilles nommees par le
    # §15. La compaction des observations volumineuses (l. 8737-8838 de
    # `react.py`, 102 lignes, 11 lectures / 11 ecritures) vient ici, avec les
    # deux dependances qui ne servaient QU'A ELLE dans `react.py`.
    "_extract_anchor_facts",
    "observation_compact_limit",
    "compact_observation_body",
    # ── Lot RF-9b (2026-08-28) — deuxieme feuille de `_run_internal` ──
    # « Detection de stagnation de pensee » : 4 entrees, une decision
    # booleenne, ONZE locales retirees de la boucle. Choisie sur le critere du
    # §15 (etat partage), pas sur le nombre de lignes : une feuille de 93
    # lignes n'en retirait que 3.
    "thought_is_stagnant",
    # ── Lot RF-9c (2026-08-28) — deux feuilles de relance ──
    # Choisies au critere du §15 (etat partage) : 5 + 4 locales retirees de la
    # boucle, la ou la feuille de 93 lignes n'en retirait que 3.
    "stagnation_tool_hint",
    "repeated_listing_reminder",
    # ── Lot RF-9d (2026-08-28) — dernieres feuilles de `_run_internal` ──
    # Le detecteur strict ne trouvait plus que 19 feuilles pures ; ces quatre
    # etaient les seules a etre de vraies DECISIONS. `web_files_present` REND
    # les trois drapeaux au lieu de les absorber : ils sont relus ~700 lignes
    # plus bas dans la boucle.
    "plan_stagnation_message",
    "web_files_present",
    "web_files_reminder",
    "phantom_channels",
    "workspace_path_from_query",
)
CONSTANTES = (
    "_TABULAR_OBS_MARKERS",
    "_TEST_RESULT_TOOL_NAMES",
    "_TEST_RESULT_RE",
    "_PHASE27_MCP_LOOP_TOOLS",
    "_READ_STAGNATION_BUDGET_FLOOR_S",
    # RF-9a — deplacee de `react.py`, qui la reexporte (invariants 4 et 12).
    "_OBS_FILE_READ_TOOLS",
)

# RF-9c — `_STAG_KW_MAP` et `_CREATION_KEYWORDS` sont dans le module mais PAS
# dans `TOUS` : elles etaient des VARIABLES LOCALES de `_run_internal`, jamais
# des symboles de module de `react.py`. Il n'y a donc rien a reexporter — la
# liste `TOUS` ne recense que les symboles qui ont voyage d'un module a l'autre.
CONSTANTES_NEUVES_RF9C = ("_STAG_KW_MAP", "_CREATION_KEYWORDS")
TOUS = FONCTIONS + CONSTANTES

# Restent dans react.py : perimetre de RF-6.
LAISSEES_A_RF6 = ("mission_write_path_exists", "mission_write_targets_existing_deliverable")


def _symboles_du_module() -> set[str]:
    noms: set[str] = set()
    for n in ast.parse(NOUVEAU.read_text(encoding="utf-8")).body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            noms.add(n.name)
        elif isinstance(n, ast.Assign):
            noms.update(t.id for t in n.targets if isinstance(t, ast.Name))
        elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
            noms.add(n.target.id)
    return noms


# ══════════════════════════════════════════════════════════════════════════
#  1. Contenu du module
# ══════════════════════════════════════════════════════════════════════════


def test_le_module_contient_exactement_les_12_symboles():
    # RF-9a : 12 -> 16 ; RF-9b : 16 -> 17 ; RF-9c : 17 -> 19 (deux constantes NEUVES, hors reexport) ; RF-9d : 19 -> 24. Mises a jour DELIBEREES
    # d'un contrat structurel, chacune avec sa raison inscrite ci-dessus.
    assert NOUVEAU.exists(), "observation_synthesis.py absent"
    # RF-9c : les constantes NEUVES (jamais symboles de `react.py`) sont
    # exclues de la comparaison — elles n'ont rien a reexporter.
    trouves = _symboles_du_module() - set(CONSTANTES_NEUVES_RF9C)
    assert trouves == set(TOUS), (
        f"manquants={sorted(set(TOUS) - trouves)} en_trop={sorted(trouves - set(TOUS))}"
    )


def test_aucune_fonction_ne_prend_self():
    fautes = [
        n.name for n in ast.parse(NOUVEAU.read_text(encoding="utf-8")).body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and n.args.args and n.args.args[0].arg == "self"
    ]
    assert fautes == [], f"fonctions prenant self : {fautes}"


def test_le_module_n_a_aucune_dependance_projet():
    """Mesure de l'audit : seulement `typing`, `pathlib`, `json`, `re`.
    C'est ce qui rend ce module testable isolement, sans demarrer Lumena."""
    freres = {"documents", "runtime", "utils", "tools", "agents", "memory",
              "context", "llm", "subagents", "reasoning", "config", "prompts", "src"}
    fautes = []
    for n in ast.parse(NOUVEAU.read_text(encoding="utf-8")).body:
        if isinstance(n, ast.ImportFrom):
            racine = (n.module or "").split(".")[0]
            if n.level > 0 or racine in freres:
                fautes.append(ast.unparse(n))
        elif isinstance(n, ast.Import):
            if n.names[0].name.split(".")[0] in freres:
                fautes.append(ast.unparse(n))
    assert fautes == [], f"dependance projet inattendue : {fautes}"


# ══════════════════════════════════════════════════════════════════════════
#  2. Identite des reexports
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("nom", TOUS)
def test_chaque_symbole_est_reexporte_et_identique(nom: str):
    """Une COPIE casserait les monkeypatchs en silence."""
    from src.reasoning import observation_synthesis, react

    assert hasattr(react, nom), f"{nom} n'est plus accessible depuis react.py"
    assert hasattr(observation_synthesis, nom), f"{nom} absent du module extrait"
    assert getattr(react, nom) is getattr(observation_synthesis, nom), (
        f"reexport divergent pour {nom}"
    )


@pytest.mark.parametrize("nom", FONCTIONS)
def test_les_fonctions_pointent_vers_le_nouveau_module(nom: str):
    from src.reasoning import react

    assert getattr(react, nom).__module__ == "src.reasoning.observation_synthesis"


# ══════════════════════════════════════════════════════════════════════════
#  3. La frontiere avec RF-6
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("nom", LAISSEES_A_RF6)
def test_les_auxiliaires_de_mission_restent_dans_react(nom: str):
    """Decision d'audit RF-2. Ces deux fonctions servent
    `ReActLoop._mission_overwrite_gate` et appartiennent au perimetre RF-6.
    Les deplacer ici aurait cree un proprietaire faux, puis oblige a les
    redeplacer — deux mouvements du meme code."""
    from src.reasoning import react

    assert hasattr(react, nom)
    assert getattr(react, nom).__module__ == "src.reasoning.react", (
        f"{nom} a ete deplace hors de react.py — c'est le perimetre de RF-6"
    )
    assert nom not in _symboles_du_module()


# ══════════════════════════════════════════════════════════════════════════
#  4. Aucun cycle, imports relatifs, aucune exception elargie
# ══════════════════════════════════════════════════════════════════════════


def test_le_module_n_importe_pas_react():
    source = NOUVEAU.read_text(encoding="utf-8")
    fautes = [
        l.strip() for l in source.splitlines()
        if re.search(r"(from\s+\S*reasoning\.react\s+import|from\s+\.react\s+import|import\s+\S*reasoning\.react)", l)
    ]
    assert fautes == [], f"cycle vers react.py : {fautes}"


def test_aucun_except_baseexception():
    source = NOUVEAU.read_text(encoding="utf-8")
    fautes = [f"l.{i}" for i, l in enumerate(source.splitlines(), 1) if "except BaseException" in l]
    assert fautes == [], f"except BaseException : {fautes}"


def test_le_module_s_importe_sans_src_sur_le_chemin():
    """Garde issu de l'INCIDENT RF-1 : un import absolu vers un paquet frere
    passe sous pytest et tue un processus lance depuis la racine du depot."""
    import subprocess
    import sys

    r = subprocess.run(
        [sys.executable, "-c",
         "import src.reasoning.observation_synthesis as m; print(len(dir(m)))"],
        cwd=str(RACINE), capture_output=True, text=True, timeout=180,
    )
    assert r.returncode == 0, (
        "le module ne s'importe pas depuis la racine du depot :\n" + (r.stderr or "")[-1200:]
    )


# ══════════════════════════════════════════════════════════════════════════
#  5. Le risque principal nomme par le plan
# ══════════════════════════════════════════════════════════════════════════


def test_une_observation_vide_n_est_ni_tabulaire_ni_un_resultat_de_tests():
    from src.reasoning import react

    assert react._obs_looks_tabular("") is False
    assert react._obs_looks_like_test_result("", tool_name="") is False


def test_un_resultat_pytest_est_reconnu_comme_tel():
    """Sans quoi la synthese de reponse traiterait une sortie de tests comme du
    texte libre — et un final prefabrique fuirait a la place du resultat.

    Le detecteur est volontairement garde par le NOM D'OUTIL : `run_command`,
    `run_shell`, `exec_command`, `process_status`. Une phrase du modele qui
    ressemble a une sortie pytest n'est pas une preuve. Mon premier jet de ce
    test passait `tool_name="run_tests"` — un nom qui n'est PAS dans la liste,
    et le detecteur refusait a raison.
    """
    from src.reasoning import react

    sortie = "===== 12 passed, 1 failed in 3.4s ====="
    assert react._obs_looks_like_test_result(sortie, tool_name="run_command") is True
    # Le garde d'outil : meme texte, outil non probant -> refus.
    assert react._obs_looks_like_test_result(sortie, tool_name="final_answer") is False
    # Le garde de longueur : trop court pour etre une preuve.
    assert react._obs_looks_like_test_result("1 passed", tool_name="run_command") is False


# ══════════════════════════════════════════════════════════════════════════
#  6. Guidance MCP — preuves COMPORTEMENTALES
# ══════════════════════════════════════════════════════════════════════════
#
# Trois tests de `tests/mcp/test_phase_i8_noncurated_autonomy.py` lisaient le
# TEXTE SOURCE de `react.py` pour verifier cette guidance. La fonction ayant
# demenage, ils ont ete repointes vers `observation_synthesis.py`.
#
# Le plan l'autorise, mais seulement « en ajoutant d'abord une preuve
# comportementale equivalente ». Les voici : elles APPELLENT la fonction au
# lieu de chercher une chaine, donc elles survivraient a un futur
# deplacement — contrairement a celles qu'elles doublent.


def _guidance(recommandation: str, **extra) -> str:
    import json as _json

    from src.reasoning import react

    charge = {"recommendation_code": recommandation, **extra}
    return react._phase27_mcp_observation_guidance(
        "run_mcp_autonomy", _json.dumps({"payload": charge})
    ) or ""


@pytest.mark.parametrize(
    "recommandation",
    ["needs_install_approval", "needs_activation_approval", "needs_catalog_approval"],
)
def test_guidance_approbation_pointe_vers_un_outil_que_le_llm_possede(recommandation):
    """Fix AH (runtime 2026-06-11) : la guidance pointait `request_mcp_ticket`,
    HORS de la liste d'outils du LLM, avec la phrase du ticket. DeepSeek
    transposait cette phrase sur `run_mcp_autonomy` et bouclait sur
    `confirmation_phrase_invalid`."""
    texte = _guidance(recommandation)
    assert "Une action MCP est necessaire" in texte
    assert "run_mcp_autonomy" in texte
    assert "I-CONFIRM-MCP-AUTONOMY" in texte
    assert "request_mcp_ticket" not in texte, (
        "la guidance repointe un outil hors de la liste du LLM — regression Fix AH"
    )


@pytest.mark.parametrize("recommandation", ["needs_install_approval"])
def test_guidance_approbation_ne_demande_jamais_la_phrase_a_l_utilisateur(recommandation):
    """Fix AD : demander la phrase a l'utilisateur relancait un tour de
    conversation inutile — la demande initiale EST le consentement."""
    texte = _guidance(recommandation)
    assert "TOI-MEME" in texte
    assert "JAMAIS a l'utilisateur de la taper" in texte


@pytest.mark.parametrize(
    "recommandation", ["ticket_proposed", "waiting_approval", "autonomy_ticket_created"]
)
def test_guidance_ticket_pending_reprend_avec_le_meme_intent(recommandation):
    """Reprendre avec un intent court re-declenchait le churn."""
    texte = _guidance(recommandation, proposed_ticket_action_id="t-42",
                      target_server_id="srv-1")
    assert "Ticket MCP pending" in texte
    assert "run_mcp_autonomy" in texte
    assert "MEME intent" in texte
    assert "ne demande JAMAIS a l'utilisateur de taper" in texte
    assert "ticket_id=t-42" in texte and "server_id=srv-1" in texte


def test_la_guidance_refuse_un_outil_hors_boucle_mcp():
    """Garde de perimetre : la guidance ne doit pas se declencher sur
    n'importe quel outil."""
    import json as _json

    from src.reasoning import react

    charge = _json.dumps({"payload": {"recommendation_code": "needs_install_approval"}})
    assert react._phase27_mcp_observation_guidance("read_file", charge) is None
    assert react._phase27_mcp_observation_guidance("run_mcp_autonomy", "") is None
    assert react._phase27_mcp_observation_guidance("run_mcp_autonomy", "pas du json") is None


def test_les_constantes_restent_non_vides():
    """Une constante vidée par un deplacement rendrait son detecteur muet,
    sans qu'aucune exception ne soit levee."""
    from src.reasoning import react

    for nom in CONSTANTES:
        valeur = getattr(react, nom)
        assert valeur is not None, f"{nom} est None"
        if hasattr(valeur, "__len__"):
            assert len(valeur) > 0, f"{nom} est vide"
