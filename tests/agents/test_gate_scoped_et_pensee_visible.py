"""Trois defauts fermes le 2026-08-29, tous trouves sur des runs reels.

--- 1. Mon propre correctif Z40c fuyait entre les taches ---

Z40c a ajoute `self._gate_indetermine` pour qu'une gate non concluante remonte
au parent au lieu de disparaitre. J'ai ajoute l'ECRITURE et oublie l'EXPIRATION.

Le CodeAgent est un singleton partage par TOUS les workers (LOT 2.12.A). Une
gate expiree chez un worker collait donc sa banniere « VERIFICATION NON
CONCLUANTE » a tous les resumes suivants, y compris pour du travail reellement
verifie. Le champ appartient a `_reset_task_scoped_state` (LOT 2.10/2.11.A),
dont le commentaire decrit deja exactement ce defaut sur d'autres champs.

--- 2. La validation statique mourait avec les tests ---

`_do_validate` fait DEUX choses : la statique, puis les tests auto-detectes.
Les deux etaient sous UN SEUL `wait_for`. Quand les tests debordaient, le
verdict statique DEJA CALCULE partait avec eux et la gate rendait
« rien n'a ete verifie ».

Mesure (run RelevéBank) : 3 timeouts sur 3 a 15 s — alors que pyright
s'initialisait en 0,48 s. Le budget ne partait donc pas au demarrage du serveur
de langage, mais dans la phase d'apres.

--- 3. La pensee du worker n'atteignait jamais l'interface ---

`_iter_thought` etait extrait, journalise (`[CodeAgent] 💭 ...`), puis
`_progress_data` etait construit sans lui. Le panneau Missions ne pouvait
afficher que des compteurs.

Motif commun aux trois : le fait existait, etait calcule, souvent affiche — et
jete avant d'atteindre celui qui decide, ou celui qui regarde.
"""

from __future__ import annotations

import ast
import asyncio
import pathlib

import pytest

RACINE = pathlib.Path(__file__).resolve().parents[2]
SUB = RACINE / "src" / "agents" / "sub_agent.py"
GATE = RACINE / "src" / "tools" / "verification_gate.py"


# ══════════════════════════════════════════════════════════════════════════
#  1. Le drapeau de gate meurt avec sa tache
# ══════════════════════════════════════════════════════════════════════════


def _corps_reset() -> str:
    arbre = ast.parse(SUB.read_text(encoding="utf-8"))
    for n in ast.walk(arbre):
        if isinstance(n, ast.FunctionDef) and n.name == "_reset_task_scoped_state":
            return ast.dump(n)
    raise AssertionError("_reset_task_scoped_state introuvable")


def test_le_drapeau_de_gate_est_remis_a_zero_par_tache():
    assert "_gate_indetermine" in _corps_reset(), (
        "le constat de gate non concluante survit d'une tache a l'autre sur le "
        "CodeAgent singleton : il bannerisera du travail correctement verifie"
    )


def test_le_reset_purge_bien_TOUT_l_etat_task_scoped():
    """Non-regression des lots 2.10 et 2.11.A, qui partagent cet invariant."""
    corps = _corps_reset()
    for champ in ("_allowed_files", "_task_workspace_root", "edits_done", "errors_seen"):
        assert champ in corps, f"{champ} n'est plus purge entre deux taches"


def test_le_reset_est_appele_AVANT_chaque_tache():
    src = SUB.read_text(encoding="utf-8")
    arbre = ast.parse(src)
    appels = [
        n for n in ast.walk(arbre)
        if isinstance(n, ast.Call)
        and getattr(n.func, "attr", "") == "_reset_task_scoped_state"
    ]
    assert appels, "le reset n'est appele nulle part"


# ══════════════════════════════════════════════════════════════════════════
#  2. Le verdict statique survit a des tests trop lents
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_des_tests_trop_lents_ne_detruisent_PAS_la_validation_statique(monkeypatch, tmp_path):
    """Le coeur du lot : avant, ce cas rendait « rien n'a ete verifie »."""
    import src.tools.verification_gate as G

    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")

    class _Rapport:
        issues: list = []

    async def _statique_rapide(files, ws):
        return _Rapport()

    async def _tests_interminables(ws, mf):
        await asyncio.sleep(30)
        return []

    monkeypatch.setattr("src.tools.code_validator.validate_project_async",
                        _statique_rapide, raising=False)
    monkeypatch.setattr(G, "_run_detected_tests", _tests_interminables)

    res = await G.run_gate(tmp_path, ["a.py"], task_id="t", timeout=2.0)

    assert res.indetermine is True, "le doute doit etre dit"
    assert "statique OK" in res.raison_indetermination, res.raison_indetermination
    assert "tests" in res.raison_indetermination.lower()


@pytest.mark.asyncio
async def test_une_ERREUR_statique_bloque_toujours_sans_attendre_les_tests(monkeypatch, tmp_path):
    """Aucun affaiblissement : la statique garde son pouvoir de refus."""
    import src.tools.verification_gate as G

    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")

    class _Issue:
        def __init__(self):
            self.severity = type("S", (), {"value": "error"})()

        def __str__(self):
            return "erreur statique de test"

    class _Rapport:
        issues = [_Issue()]

    async def _statique(files, ws):
        return _Rapport()

    async def _tests_jamais_appeles(ws, mf):
        raise AssertionError("les tests ne doivent pas tourner apres une erreur statique")

    monkeypatch.setattr("src.tools.code_validator.validate_project_async", _statique, raising=False)
    monkeypatch.setattr(G, "_run_detected_tests", _tests_jamais_appeles)

    res = await G.run_gate(tmp_path, ["a.py"], task_id="t", timeout=5.0)
    assert res.passed is False
    assert res.indetermine is False, "un refus net n'est pas un doute"


@pytest.mark.asyncio
async def test_le_chemin_nominal_reste_identique(monkeypatch, tmp_path):
    import src.tools.verification_gate as G

    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")

    class _Rapport:
        issues: list = []

    monkeypatch.setattr("src.tools.code_validator.validate_project_async",
                        lambda f, w: _reponse(_Rapport()), raising=False)
    monkeypatch.setattr(G, "_run_detected_tests", lambda w, m: _reponse([]))

    res = await G.run_gate(tmp_path, ["a.py"], task_id="t", timeout=5.0)
    assert res.passed is True and res.indetermine is False


async def _reponse(v):
    return v


def test_les_tests_ont_un_budget_PLUS_PETIT_que_la_statique():
    """La statique est rapide et sure ; c'est elle qui doit avoir la part large."""
    import src.tools.verification_gate as G

    assert 0.0 < G._TESTS_BUDGET_RATIO < 0.5, G._TESTS_BUDGET_RATIO


def test_les_quatre_chemins_fail_open_disent_TOUJOURS_leur_doute():
    """Non-regression Z40c : aucun `passed=True` muet ne revient."""
    src = GATE.read_text(encoding="utf-8")
    arbre = ast.parse(src)
    muets = []
    for n in ast.walk(arbre):
        if not (isinstance(n, ast.Call) and getattr(n.func, "id", "") == "GateResult"):
            continue
        kw = {k.arg: k.value for k in n.keywords}
        passed = kw.get("passed")
        if isinstance(passed, ast.Constant) and passed.value is True:
            if "indetermine" not in kw and "errors" not in kw:
                muets.append(n.lineno)
    assert not muets, f"GateResult(passed=True) sans doute ni erreurs, lignes {muets}"


# ══════════════════════════════════════════════════════════════════════════
#  3. La pensee atteint l'interface
# ══════════════════════════════════════════════════════════════════════════


def _progress_data() -> ast.Dict:
    arbre = ast.parse(SUB.read_text(encoding="utf-8"))
    for n in ast.walk(arbre):
        if (isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "_progress_data" for t in n.targets)
                and isinstance(n.value, ast.Dict)):
            return n.value
    raise AssertionError("_progress_data introuvable")


def test_la_pensee_est_dans_le_payload_de_progression():
    cles = {k.value for k in _progress_data().keys if isinstance(k, ast.Constant)}
    assert "thought" in cles, (
        "la pensee est extraite et journalisee mais n'atteint pas l'UI : le "
        "panneau Missions ne peut afficher que des compteurs"
    )


def test_le_payload_garde_TOUTES_ses_cles_historiques():
    cles = {k.value for k in _progress_data().keys if isinstance(k, ast.Constant)}
    for c in ("iteration", "max_iter", "pct", "last_action", "last_path"):
        assert c in cles, f"{c} a disparu du payload"


def test_la_pensee_est_bornee():
    """Elle transite a chaque iteration : elle ne doit pas gonfler le payload."""
    for kv, v in zip(_progress_data().keys, _progress_data().values):
        if isinstance(kv, ast.Constant) and kv.value == "thought":
            assert isinstance(v, ast.Subscript), "la pensee n'est pas tronquee"
            return
    raise AssertionError("cle 'thought' absente")
