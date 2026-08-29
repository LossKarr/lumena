"""Run du 2026-08-29 — deux defauts du CodeAgent en mission.

--- A. Un chemin relatif a la RACINE, pas au workspace ---

    [CodeAgent] ecriture hors perimetre refusee:
      ['workspace/missions/task_.../parse.py'] (autorises: ['parse.py'])

Le chemin designait trait pour trait un fichier AUTORISE. Il n'etait pas
absolu, donc la reduction `relative_to(workspace_root)` ne tirait pas ; puis
le garde LOT J-b (« des que le chemin porte un dossier, egalite stricte »)
le refusait sec.

--- B. Un revert doit etre IMPUTABLE ---

`parse.py` a coute 325,1 s et 13 iterations, contre 66,8 s et 5 pour
`analyse.py`. `run_tests` trouvait rouge un `test_releve.py` encore a l'etat
de STUB — le fichier d'un AUTRE worker, qui n'avait pas fini — et l'auto-revert
annulait une edition CORRECTE. Quatre fois. Le CodeAgent l'a dit lui-meme :
« l'edit_lines avait bien modifie mais l'auto-revert a reagi a un echec de
tests non lies ».

Un test hors du perimetre du worker ne prouve RIEN sur son edition.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from src.agents.sub_agent import _write_within_perimeter

RACINE = pathlib.Path(__file__).resolve().parents[2]
SUB = RACINE / "src" / "agents" / "sub_agent.py"


@pytest.fixture()
def ws(tmp_path):
    d = tmp_path / "workspace" / "missions" / "task_abc"
    d.mkdir(parents=True)
    return d


# ══════════════════════════════════════════════════════════════════════════
#  A. Le chemin relatif a la racine est ramene a sa forme attendue
# ══════════════════════════════════════════════════════════════════════════


def test_LE_cas_du_run_un_chemin_relatif_a_la_racine_est_accepte(ws):
    assert _write_within_perimeter(
        "workspace/missions/task_abc/parse.py", {"parse.py"}, str(ws)) is True


def test_les_formes_deja_supportees_le_restent(ws):
    assert _write_within_perimeter("parse.py", {"parse.py"}, str(ws)) is True
    assert _write_within_perimeter(str(ws / "parse.py"), {"parse.py"}, str(ws)) is True


def test_hors_perimetre_reste_hors_perimetre(ws):
    assert _write_within_perimeter(
        "workspace/missions/task_abc/autre.py", {"parse.py"}, str(ws)) is False


def test_LOT_J_b_tient_un_dossier_precis_reste_exige(ws):
    """Le doublon a la racine qui avait tue la mission NoteFlow doit rester refuse,
    y compris sous sa forme relative a la racine Lumena."""
    autorises = {"noteflow/static/style.css"}
    assert _write_within_perimeter("static/style.css", autorises, str(ws)) is False
    assert _write_within_perimeter(
        "workspace/missions/task_abc/static/style.css", autorises, str(ws)) is False
    assert _write_within_perimeter(
        "workspace/missions/task_abc/noteflow/static/style.css", autorises, str(ws)) is True


def test_aucune_evasion_possible(ws):
    for mauvais in ("../../../etc/passwd", "../parse.py", "/etc/passwd"):
        assert _write_within_perimeter(mauvais, {"parse.py"}, str(ws)) is False, mauvais


def test_hors_mission_zero_effet(ws):
    """`allowed_files` vide → le CodeAgent est strictement inchange."""
    assert _write_within_perimeter("n_importe/quoi.py", None, str(ws)) is True
    assert _write_within_perimeter("n_importe/quoi.py", set(), str(ws)) is True


def test_sans_workspace_root_rien_ne_change(ws):
    assert _write_within_perimeter(
        "workspace/missions/task_abc/parse.py", {"parse.py"}, None) is False


# ══════════════════════════════════════════════════════════════════════════
#  B. L'auto-revert n'annule que sur un echec IMPUTABLE
# ══════════════════════════════════════════════════════════════════════════
#
#  Le bloc est inline dans la boucle du CodeAgent : on le lit en AST.


def _bloc_revert():
    arbre = ast.parse(SUB.read_text(encoding="utf-8"))
    for n in ast.walk(arbre):
        if isinstance(n, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "_tests_imputables" for t in n.targets):
            return n
    return None


def test_le_revert_est_conditionne_a_l_imputabilite():
    assert _bloc_revert() is not None, (
        "l'auto-revert n'evalue plus l'imputabilite de l'echec"
    )


def test_l_imputabilite_se_juge_sur_le_PERIMETRE_du_worker():
    lu = ast.dump(_bloc_revert())
    assert "_write_within_perimeter" in lu, lu
    assert "_perim_bonus" in lu, lu


def test_le_perimetre_consulte_est_bien_celui_du_worker():
    """`_perim_bonus` doit venir de `self._allowed_files`, pas d'ailleurs."""
    arbre = ast.parse(SUB.read_text(encoding="utf-8"))
    src = [
        n.value for n in ast.walk(arbre)
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "_perim_bonus" for t in n.targets)
    ]
    assert src, "`_perim_bonus` n'est assigne nulle part"
    assert "_allowed_files" in ast.dump(src[0]), ast.dump(src[0])


def test_hors_mission_l_auto_revert_est_INCHANGE():
    """`(not _perim) or ...` : sans perimetre, toujours imputable → revert comme avant."""
    v = _bloc_revert().value
    assert isinstance(v, ast.BoolOp) and isinstance(v.op, ast.Or), ast.dump(v)
    premier = ast.dump(v.values[0])
    assert "UnaryOp" in premier and "_perim" in premier, premier


def test_le_rollback_est_bien_DANS_la_branche_imputable():
    arbre = ast.parse(SUB.read_text(encoding="utf-8"))
    gardes = [n for n in ast.walk(arbre)
              if isinstance(n, ast.If) and isinstance(n.test, ast.Name)
              and n.test.id == "_tests_imputables"]
    assert gardes, "aucun garde `if _tests_imputables:`"
    g = gardes[0]
    corps = ast.dump(ast.Module(body=g.body, type_ignores=[]))
    assert "_rollback_session" in corps, "le rollback a quitte la branche gardee"
    assert g.orelse, "l'echec hors perimetre n'est pas rapporte"
    sinon = ast.dump(ast.Module(body=g.orelse, type_ignores=[]))
    assert "_rollback_session" not in sinon, (
        "la branche hors perimetre annule quand meme le travail"
    )
