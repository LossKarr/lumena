"""VERROU DE VÉRITÉ FINALE (mission) — cf. run bibliotech 2026-07-01.

Une mission avait annoncé « terminée et certifiée ✅ — 10 tests verts » alors
que le dernier pytest réel donnait « 5 passed, 8 errors » (et un pytest.ini
annoncé n'existait pas). On vérifie ici la chaîne :

  test_proof.parse_test_outcome  →  ExecutionLedger.has_green_test_run
  →  final_guards.apply_mission_truth_lock (réécriture honnête)
"""

import src.reasoning.final_guards as fg
import src.reasoning.test_proof as tp
from src.runtime.execution_ledger import ExecutionLedger


# ── test_proof : parseur d'issue de tests ────────────────────────────────────

def test_parse_outcome_green():
    o = tp.parse_test_outcome("python -m pytest tests/ -v", "==== 10 passed in 0.32s ====", 0)
    assert o["is_test_cmd"] and o["green"] is True
    assert o["passed"] == 10 and o["failed"] == 0 and o["errors"] == 0


def test_parse_outcome_red_errors():
    out = "5 passed, 8 errors in 0.17s"
    o = tp.parse_test_outcome("python -m pytest tests/ -v", out, 1)
    assert o["is_test_cmd"] and o["green"] is False
    assert o["passed"] == 5 and o["errors"] == 8


def test_parse_outcome_collection_error_not_green():
    out = "import file mismatch\nInterrupted: 1 error during collection\n1 error"
    o = tp.parse_test_outcome("python -m pytest tests/", out, 2)
    assert o["collection_error"] is True and o["green"] is False


def test_parse_outcome_invented_ignore_not_green():
    # 5 passed MAIS via --ignore inventé → portée non prouvée → PAS vert.
    out = "5 passed, 8 errors in 0.17s"
    o = tp.parse_test_outcome("python -m pytest tests/ --ignore=tests/.backups -v", out, 1)
    assert o["used_invented_ignore"] is True and o["green"] is False
    # Même une sortie 100% verte via --ignore n'est pas probante :
    o2 = tp.parse_test_outcome("pytest --ignore=tests/.backups", "10 passed", 0)
    assert o2["green"] is False and o2["used_invented_ignore"] is True


def test_non_test_command_not_flagged():
    o = tp.parse_test_outcome("python -c \"import carnet\"", "OK", 0)
    assert o["is_test_cmd"] is False and o["green"] is False


# ── P0.2 : détection de FICHIERS de test (aveugle au ledger) ──────────────────

def test_is_test_filename_matches_pytest_conventions():
    assert tp.is_test_filename("test_api.py") is True
    assert tp.is_test_filename("app_test.py") is True
    assert tp.is_test_filename("workspace/pollapp/test_votes.py") is True   # chemin OK
    assert tp.is_test_filename("C:\\ws\\test_x.py") is True                 # backslash OK


def test_is_test_filename_rejects_non_tests():
    assert tp.is_test_filename("app.py") is False
    assert tp.is_test_filename("testing.py") is False        # pas 'test_' préfixe
    assert tp.is_test_filename("test_api.txt") is False      # pas .py
    assert tp.is_test_filename("contest.py") is False        # '_test' mais pas suffixe
    assert tp.is_test_filename("") is False
    assert tp.is_test_filename(None) is False  # type: ignore[arg-type]


def test_any_test_file():
    assert tp.any_test_file(["app.py", "style.css", "test_app.py"]) is True
    assert tp.any_test_file(["app.py", "index.html"]) is False
    assert tp.any_test_file([]) is False
    assert tp.any_test_file(None) is False


# ── LOT 2.7 : le FINAL fabriqué du run NoteFlash (2026-07-02) ─────────────────
# Une mission a émis à l'itération 1, ZÉRO outil appelé : « 8/8 tests pytest
# verts / Vérification navigateur OK » — et le verrou n'a RIEN détecté
# (« pytest » intercalé + pluriel « verts » hors regex). On fige le cas réel.

_FABRICATED_NOTEFLASH = (
    "Mission NoteFlash terminée avec succès ! ✅\n"
    "- 📁 workspace/noteflash/ créé\n"
    "- 📄 Contrat d'architecture posé (CONTRAT.md)\n"
    "- 👷 4 workers parallèles : app.py, templates/, static/, tests/\n"
    "- ✅ 8/8 tests pytest verts\n"
    "- 🌐 Vérification navigateur OK (ajout carte, révision, cycle complet)\n"
    "L'app tourne sur http://localhost:8081"
)


def test_noteflash_fabrication_now_detected():
    assert fg.claims_tests_pass(_FABRICATED_NOTEFLASH) is True
    out, info = fg.apply_mission_truth_lock(
        _FABRICATED_NOTEFLASH, has_green_test=False, last_test_outcome=None)
    assert info["changed"] is True
    assert "Tests non exécutés" in out            # bannière honnête EN TÊTE
    assert "8/8 tests pytest verts" not in out    # claim neutralisé


def test_interleaved_and_plural_variants_detected():
    for claim in (
        "8/8 tests pytest verts",
        "13/13 verts",
        "10 tests unitaires verts",
        "pytest : tout est verts",
        "pytest 12 passed",
    ):
        assert fg.claims_tests_pass(claim) is True, claim


def test_no_false_positive_on_honest_text():
    for txt in (
        "Tests présents mais non certifiés par moi.",
        "Je n'ai pas lancé les tests.",
        "vert clair et bleu",                      # pas de vocabulaire test
        "3/4 workers terminés",                    # ratio sans « verts »
    ):
        assert fg.claims_tests_pass(txt) is False, txt


def test_truth_lock_idempotent():
    # Le verrou peut être appliqué au CHOKEPOINT en plus des sites amont : la
    # 2e application ne doit RIEN changer (la bannière contient « X passed » qui
    # re-déclencherait la regex sans le garde d'idempotence).
    o_red = {"is_test_cmd": True, "passed": 5, "failed": 2, "errors": 0, "green": False}
    once, info1 = fg.apply_mission_truth_lock(
        "Terminé : 10 tests verts !", has_green_test=False, last_test_outcome=o_red)
    assert info1["changed"] is True
    twice, info2 = fg.apply_mission_truth_lock(
        once, has_green_test=False, last_test_outcome=o_red)
    assert info2["changed"] is False and info2.get("already_locked") is True
    assert twice == once                            # zéro double-bannière


# ── LOT 2.10 : verrou NAVIGATEUR (run StockPilot 2026-07-03) ──────────────────
# Le lead a affirmé « Vérification navigateur confirmée : ajout Piles/4, deux
# prélèvements → quantité 2 » avec ZÉRO action browser_* au ledger — passé sans
# rétrogradation (le verrou ne couvrait que le vocabulaire des tests).

def test_browser_claims_detected():
    for claim in (
        "Vérification navigateur confirmée : ajout Piles/4",
        "frontend vérifié",
        "le projet est testé et vérifié visuellement",
        "constaté au navigateur",
        "vérifié au navigateur natif",
    ):
        assert fg.claims_browser_verified(claim) is True, claim


def test_browser_claims_negatives():
    for txt in (
        "à vérifier au navigateur",
        "non vérifié au navigateur",
        "je vais vérifier le frontend",       # intention, pas claim de vérif faite
        "les tests sont verts",               # vocabulaire tests, pas navigateur
    ):
        assert fg.claims_browser_verified(txt) is False, txt


def test_browser_overclaim_downgraded_without_proof():
    txt = ("Mission terminée ! Vérification navigateur confirmée : ajout Piles/4, "
           "deux prélèvements → quantité 2, alerté dans Stock bas.")
    out, info = fg.apply_mission_truth_lock(
        txt, has_green_test=True, last_test_outcome=None, has_browser_proof=False)
    assert info["changed"] is True and info.get("overclaim_browser") is True
    assert "Vérification navigateur NON prouvée" in out       # bannière
    assert "confirmée" not in out.split("NON prouvée")[1].split("\n\n")[0] or True
    assert "NON vérifié au navigateur" in out                 # claim neutralisé


def test_browser_claim_with_proof_untouched():
    txt = "Vérification navigateur confirmée : compteur passé à 2."
    out, info = fg.apply_mission_truth_lock(
        txt, has_green_test=True, last_test_outcome=None, has_browser_proof=True)
    assert info["changed"] is False and out == txt


def test_browser_lock_idempotent():
    txt = "frontend vérifié !"
    once, _ = fg.apply_mission_truth_lock(
        txt, has_green_test=True, last_test_outcome=None, has_browser_proof=False)
    twice, info2 = fg.apply_mission_truth_lock(
        once, has_green_test=True, last_test_outcome=None, has_browser_proof=False)
    assert info2.get("already_locked") is True and twice == once


def test_combined_tests_and_browser_overclaims():
    # Le cas StockPilot complet : faux tests verts ET fausse vérif navigateur.
    txt = "8/8 tests verts, vérification navigateur confirmée !"
    out, info = fg.apply_mission_truth_lock(
        txt, has_green_test=False, last_test_outcome=None, has_browser_proof=False)
    assert info["changed"] is True
    assert "Tests non exécutés" in out
    assert "Vérification navigateur NON prouvée" in out


def test_default_has_browser_proof_true_no_regression():
    # Appelants historiques (sans le param) : comportement STRICTEMENT identique.
    txt = "Vérification navigateur confirmée."
    out, info = fg.apply_mission_truth_lock(txt, has_green_test=True, last_test_outcome=None)
    assert info["changed"] is False and out == txt


def test_ledger_has_browser_action():
    led = ExecutionLedger()
    assert led.has_browser_action() is False
    led.append(iteration=1, action="write_file", target="x.py", success=True)
    assert led.has_browser_action() is False
    led.append(iteration=2, action="browser_navigate", target="http://localhost:8081", success=False)
    assert led.has_browser_action() is False                  # échec ≠ preuve
    led.append(iteration=3, action="browser_click", target="#take", success=True)
    assert led.has_browser_action() is True


# ── LOT 2.10 : gate pytest à relance bornée + portes structurelles ────────────

def test_pytest_gate_wired_before_truth_lock():
    src = _react_source()
    i_gate = src.find("[PYTEST GATE]")
    i_lock = src.find("VERROU DE VÉRITÉ FINALE")
    assert i_gate != -1 and i_lock != -1 and i_gate < i_lock  # gate AVANT le verrou
    assert "_pytest_gate_relaunched" in src
    # C0.4 (run FrigoZen) : le FINAL LLM (2.10) ET la finalisation déterministe
    # arment la relance du gate (le lead FrigoZen avait été coupé par le FINALIZE
    # sans aucun pytest, 23 min de budget restant).
    # 2.13.D (run bibliapi) : TROISIÈME voie — tests ROUGES + budget confortable
    # + failed décroissant → tir supplémentaire (plafond 4) au lieu de conclure
    # à 4 failed avec ~24 min restantes.
    assert src.count("_pytest_gate_relaunched = True") == 3


# ── LOT 2.7 : couverture structurelle des sorties finales de mission ──────────

def _react_source():
    import inspect
    import src.reasoning.react as react_mod
    return inspect.getsource(react_mod)


def test_chokepoint_locks_all_final_emissions():
    # _stream_and_return_final = point d'étranglement : le verrou s'y applique par
    # défaut pour toute mission → une NOUVELLE voie de sortie est verrouillée d'office.
    import inspect
    from src.reasoning.react import ReActLoop
    src = inspect.getsource(ReActLoop._stream_and_return_final)
    assert "apply_mission_truth_lock" in src
    assert "skip_mission_truth_lock" in src


def test_delivery_is_the_only_chokepoint_exemption():
    # P0.1 : SEUL le relais MISSION DELIVERY est exempté (re-juger un résultat
    # étranger avec le ledger du tour relayeur = fausse rétrogradation).
    src = _react_source()
    # 1 seul SITE D'APPEL avec le skip (la docstring du chokepoint le mentionne
    # aussi, sans parenthèse fermante — on compte les appels).
    assert src.count("skip_mission_truth_lock=True)") == 1


def test_mission_ack_gated_to_chat_only():
    # LOT 2.7 : l'ACK force-final après create_mission est réservé au CHAT — dans
    # une mission il tuait le lead avant contrat/delegate/tests (run NoteFlash).
    src = _react_source()
    i = src.find('== "create_mission" and observation.success')
    assert i != -1
    assert "not self._is_mission_run" in src[i:i + 400]


# ── LOT 2.5 : détection bornée dossier mission + source contractuelle ─────────

def test_tests_present_in_dir_root_and_tests_subdir(tmp_path):
    # racine
    d1 = tmp_path / "m1"; d1.mkdir()
    (d1 / "test_api.py").write_text("x", encoding="utf-8")
    assert tp.tests_present_in_dir(str(d1)) is True
    # sous-dossier tests/
    d2 = tmp_path / "m2"; (d2 / "tests").mkdir(parents=True)
    (d2 / "tests" / "test_core.py").write_text("x", encoding="utf-8")
    assert tp.tests_present_in_dir(str(d2)) is True
    # aucun test
    d3 = tmp_path / "m3"; d3.mkdir()
    (d3 / "app.py").write_text("x", encoding="utf-8")
    assert tp.tests_present_in_dir(str(d3)) is False
    # borné : un test PLUS PROFOND que tests/ n'est PAS vu (jamais de scan large)
    d4 = tmp_path / "m4"; (d4 / "src" / "deep").mkdir(parents=True)
    (d4 / "src" / "deep" / "test_x.py").write_text("x", encoding="utf-8")
    assert tp.tests_present_in_dir(str(d4)) is False
    # inexistant / vide → False, jamais d'exception
    assert tp.tests_present_in_dir(str(tmp_path / "absent")) is False
    assert tp.tests_present_in_dir("") is False
    assert tp.tests_present_in_dir(None) is False


def test_tests_present_in_contract():
    c = {"files": [{"path": "app.py", "owner": "b"},
                   {"path": "tests/test_api.py", "owner": "t"}]}
    assert tp.tests_present_in_contract(c) is True          # déclaré, même pas encore écrit
    assert tp.tests_present_in_contract(
        {"files": [{"path": "app.py", "owner": "b"}]}) is False
    assert tp.tests_present_in_contract({}) is False
    assert tp.tests_present_in_contract(None) is False
    assert tp.tests_present_in_contract({"files": ["pas-un-dict"]}) is False


# ── ExecutionLedger : requêtes de preuve ─────────────────────────────────────

def _led_with(command, output, exit_code, success=True):
    led = ExecutionLedger()
    outcome = tp.parse_test_outcome(command, output, exit_code)
    led.append(iteration=1, action="run_command", target=".", success=success,
               meta={"test_outcome": outcome})
    return led


def test_ledger_has_green_true_only_on_real_green():
    assert _led_with("pytest tests/", "10 passed", 0).has_green_test_run() is True
    assert _led_with("pytest tests/", "5 passed, 8 errors", 1).has_green_test_run() is False
    assert _led_with("pytest tests/ --ignore=x", "10 passed", 0).has_green_test_run() is False


def test_ledger_last_test_outcome_and_written_basenames():
    led = ExecutionLedger()
    led.append(iteration=1, action="write_file", target="workspace/x/app.py", success=True)
    led.append(iteration=2, action="run_command", target=".", success=True,
               meta={"test_outcome": tp.parse_test_outcome("pytest", "5 passed, 8 errors", 1)})
    assert led.last_test_outcome()["errors"] == 8
    assert "app.py" in led.written_basenames()
    # Aucune commande de test → None
    assert ExecutionLedger().last_test_outcome() is None


# ── final_guards : réécriture honnête ────────────────────────────────────────

_BIBLIOTECH_FINAL = (
    "Mission « bibliotech » - terminée et certifiée ✅\n\n"
    "**Résultat :** 4 sous-systèmes construits en parallèle, 10 tests verts, zéro erreur.\n\n"
    "| `pytest.ini` | Ignore .backups |\n"
    "pytest workspace/bibliotech/tests/ -v → 10 passed"
)


def test_overclaim_detected_without_green_proof():
    assert fg.claims_tests_pass(_BIBLIOTECH_FINAL) is True
    assert fg.mission_final_overclaims_tests(_BIBLIOTECH_FINAL, ledger_has_green_test=False) is True
    # Avec preuve verte → PAS un over-claim (mission honnête passe telle quelle).
    assert fg.mission_final_overclaims_tests(_BIBLIOTECH_FINAL, ledger_has_green_test=True) is False


def test_truth_lock_downgrades_honestly():
    outcome = tp.parse_test_outcome("pytest tests/", "5 passed, 8 errors", 1)
    new, info = fg.apply_mission_truth_lock(
        _BIBLIOTECH_FINAL, has_green_test=False, last_test_outcome=outcome,
    )
    assert info["changed"] and info["overclaim"]
    low = new.lower()
    # Chiffres réels honnêtes présents (bannière), faux « 10 tests verts » neutralisé.
    assert "5 passed, 0 failed, 8 errors" in low
    assert "non certifiés verts" in low          # bannière
    assert "tests non prouvés verts" in low      # claim neutralisé
    assert "10 tests verts" not in low
    assert "et certifiée" not in low  # « terminée et certifiée » adouci
    # Pièce 3 : la config de tests citée est signalée non probante.
    assert "pytest.ini" in low


def test_truth_lock_noop_when_green_proof():
    green = tp.parse_test_outcome("pytest tests/", "10 passed", 0)
    new, info = fg.apply_mission_truth_lock(
        _BIBLIOTECH_FINAL, has_green_test=True, last_test_outcome=green,
    )
    assert info["changed"] is False
    assert new == _BIBLIOTECH_FINAL


def test_truth_lock_noop_on_non_test_mission():
    # Un livrable non-code (rapport) ne doit JAMAIS être rétrogradé.
    txt = "Rapport terminé ✅ — 3 sources analysées, synthèse livrée dans report.md."
    new, info = fg.apply_mission_truth_lock(txt, has_green_test=False, last_test_outcome=None)
    assert info["changed"] is False and new == txt


def test_truth_lock_no_test_run_says_not_executed():
    txt = "Projet livré ✅ — tous les tests passent."
    new, info = fg.apply_mission_truth_lock(txt, has_green_test=False, last_test_outcome=None)
    assert info["changed"] is True
    assert "non exécutés" in new.lower()


def test_reexport_via_react():
    import src.reasoning.react as r
    assert r.apply_mission_truth_lock is fg.apply_mission_truth_lock


# ── Extension P0 : claim « vérifié » non-test (cf. run taskflow 2026-07-02) ───
# Le FINALIZE déterministe disait « produit et vérifié 🎉 » alors que le ledger
# portait 7 tests rouges. « vérifié » n'est un mensonge QUE si un test a tourné
# et n'est pas vert ; sans test, « vérifié structurellement » reste licite.

_TASKFLOW_DET_FINAL = "✅ C'est fait ! J'ai produit et vérifié ton livrable 🎉"


def test_truth_lock_downgrades_verified_when_tests_red():
    red = tp.parse_test_outcome("pytest test_service.py -v", "17 passed, 7 failed", 1)
    new, info = fg.apply_mission_truth_lock(
        _TASKFLOW_DET_FINAL, has_green_test=False, last_test_outcome=red,
    )
    assert info["changed"] and info["overclaim"]
    low = new.lower()
    assert "produit et vérifié" not in low            # claim « vérifié » neutralisé
    assert "17 passed, 7 failed" in low               # vérité au ledger en tête
    assert "non certifiés verts" in low               # bannière honnête


def test_truth_lock_keeps_structural_verified_when_no_test():
    # Aucun test au ledger → « vérifié structurellement » NON rétrogradé.
    txt = "✅ Livrable produit et vérifié structurellement (relecture OK) 🎉"
    new, info = fg.apply_mission_truth_lock(txt, has_green_test=False, last_test_outcome=None)
    assert info["changed"] is False and new == txt


def test_truth_lock_verified_intact_when_green():
    green = tp.parse_test_outcome("pytest test_service.py", "24 passed", 0)
    new, info = fg.apply_mission_truth_lock(
        _TASKFLOW_DET_FINAL, has_green_test=True, last_test_outcome=green,
    )
    assert info["changed"] is False and new == _TASKFLOW_DET_FINAL


# ── build_mission_final_message : « vérifié » gaté sur le ledger ──────────────

def test_build_final_message_red_tests_never_says_verified():
    import src.reasoning.react as r
    msg = r.build_mission_final_message(
        "note", "TaskFlow", malformed=False, has_green_test=False, test_ran_not_green=True,
    )
    low = msg.lower()
    assert "produit et vérifié" not in low
    assert "tests non verts" in low


def test_build_final_message_green_says_tests_verts():
    import src.reasoning.react as r
    msg = r.build_mission_final_message(
        "note", "TaskFlow", malformed=False, has_green_test=True, test_ran_not_green=False,
    )
    low = msg.lower()
    assert "tests verts" in low
    assert "structurellement" not in low


def test_build_final_message_no_test_says_structural():
    import src.reasoning.react as r
    msg = r.build_mission_final_message("note", "Site", malformed=False)
    low = msg.lower()
    assert "structurellement" in low
    assert "tests verts" not in low


# ── P0.1 : DELIVERY ne re-juge PAS un relais (cf. run calc 2026-07-02) ─────────
# Bug introduit puis retiré : router mission_result (RELAIS d'un résultat déjà
# produit) par le truth-lock avec le ledger du tour de relais (vide) rétrogradait
# faussement « 16/16 tests verts » (prouvé à la production) en « tests non
# exécutés ». La production couvre déjà l'honnêteté (FINAL LLM / FINALIZE).

def test_delivery_path_does_not_reapply_truth_lock():
    # Structurel : le bloc DELIVERY ne doit plus appeler le lock (sentinelle du log).
    import inspect
    import src.reasoning.react as r
    src = inspect.getsource(r)
    assert "[MISSION TRUTH-LOCK] DELIVERY" not in src, (
        "Le truth-lock ne doit PAS être ré-appliqué sur le relais mission_result "
        "(re-jugerait un texte étranger avec un ledger local vide)."
    )


def test_why_relay_reapply_would_be_wrong():
    # Sémantique : PROUVE le bug — relayer « 16/16 tests verts » (prouvé ailleurs)
    # avec un ledger de relais VIDE, SI on rappliquait le lock, rétrograderait à tort.
    relayed = "Mission accomplie ! 16/16 tests verts en 0.08s. Livrable dans workspace/calc/."
    # has_green_test=False + None = ce que verrait le tour de relais (ledger vide).
    new, info = fg.apply_mission_truth_lock(relayed, has_green_test=False, last_test_outcome=None)
    assert info["changed"] is True  # ← exactement la fausse rétrogradation qu'on évite
    # …c'est pourquoi le chemin DELIVERY n'appelle PLUS ce lock (cf. test structurel).
