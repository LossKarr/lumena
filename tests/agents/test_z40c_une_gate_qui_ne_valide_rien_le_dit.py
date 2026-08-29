"""Z40c — une gate qui n'a rien pu valider ne doit pas dire « valide ».

Defaut mesure sur le corpus REEL de production (`data/logs/codeagent/
gate_metrics.jsonl` + deux campagnes archivees) :

    197 executions de la Verification Gate
     92 + 3     gate_pass
     49 + 3     gate_fail
     50         lsp_fail_open        <-- 25,4 %
                dont timeout : 50 / 50   (zero exception)

**Une fois sur quatre, la gate a rendu `passed=True` sans avoir rien
valide.** Le CodeAgent recoit alors exactement le meme objet que pour une
validation reussie, et conclut `done`.

Le fait EXISTE : le timeout est journalise (`[gate] timeout — fail-open`) et
compte (`record_lsp_fail_open`). Il n'atteint simplement pas celui qui decide.
C'est le motif de fond du chantier, applique cette fois a une porte de qualite.

--- Ce que ce lot NE fait PAS ---

Il ne ferme PAS la gate. `passed` reste `True` sur fail-open : bloquer le
CodeAgent sur une panne d'infra reste le mauvais choix, et l'invariant 6 dit
qu'une exception ne doit pas devenir une autorisation — pas qu'elle doit
devenir un refus. Z40c ajoute un TROISIEME etat, `indetermine`, et le fait
remonter jusqu'au parent. La porte laisse passer, mais elle ne ment plus.

--- Les QUATRE chemins fail-open (mesures dans le code, pas supposes) ---

    1. `workspace is None or not workspace.exists()`  -> passed=True, AUCUNE metrique
    2. `asyncio.TimeoutError`                         -> passed=True, metrique
    3. `except Exception`                             -> passed=True, metrique
    4. `_run_detected_tests` timeout/exception        -> `return []`, donc zero erreur

Le chemin 1 est le plus silencieux des quatre : il ne laisse meme pas de trace
au compteur, donc il est INVISIBLE dans les 25,4 % mesures ci-dessus. Le vrai
taux est donc un plancher, pas une mesure.
"""

from __future__ import annotations

import asyncio
import pathlib
import tempfile

import pytest

from src.tools import verification_gate as vg
from src.tools.verification_gate import GateResult, run_gate


def _ws() -> pathlib.Path:
    return pathlib.Path(tempfile.mkdtemp(prefix="z40c_"))


# ══════════════════════════════════════════════════════════════════════════
#  1. Le troisieme etat existe, et il est FAUX par defaut
# ══════════════════════════════════════════════════════════════════════════


def test_un_resultat_de_gate_est_determine_par_defaut():
    """Le defaut doit rester « j'ai valide » : sinon tous les passages
    historiques deviendraient d'un coup des constats, et la banniere perdrait
    tout son sens en devenant permanente."""
    r = GateResult(passed=True)
    assert r.indetermine is False
    assert r.raison_indetermination == ""


def test_un_refus_reel_n_est_PAS_indetermine():
    """Une gate qui a vu de vraies erreurs a fait son travail. Confondre
    « refuse » et « pas pu juger » retirerait au CodeAgent le seul signal
    exploitable qu'il ait."""
    r = GateResult(passed=False, errors=["syntax error ligne 3"])
    assert r.indetermine is False


# ══════════════════════════════════════════════════════════════════════════
#  2. LE DEFAUT : les quatre chemins fail-open
# ══════════════════════════════════════════════════════════════════════════


def test_le_timeout_rend_un_resultat_INDETERMINE(monkeypatch):
    """Chemin 2 — 50 occurrences sur 50 dans le corpus reel."""

    async def _lent(*a, **k):
        await asyncio.sleep(10)

    monkeypatch.setattr(vg, "_do_validate", _lent)
    r = asyncio.run(run_gate(_ws(), ["a.py"], task_id="t", timeout=0.05))

    assert r.passed is True, "Z40c ne ferme pas la gate — elle laisse passer"
    assert r.indetermine is True, (
        "la gate a rendu « valide » apres un timeout : celui qui decide ne peut "
        "pas distinguer une validation d'une panne"
    )
    assert "timeout" in r.raison_indetermination.lower()


def test_une_exception_interne_rend_un_resultat_INDETERMINE(monkeypatch):
    """Chemin 3 — invariant 6 : une exception ne devient pas une
    autorisation. Ici elle ne devient pas non plus un refus : elle devient un
    CONSTAT."""

    async def _casse(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(vg, "_do_validate", _casse)
    r = asyncio.run(run_gate(_ws(), ["a.py"], task_id="t"))

    assert r.passed is True
    assert r.indetermine is True
    assert "boom" in r.raison_indetermination


def test_un_workspace_absent_rend_un_resultat_INDETERMINE():
    """Chemin 1 — le plus silencieux des quatre : il ne laisse aucune trace au
    compteur, donc il n'apparait meme pas dans les 25,4 % mesures."""
    r = asyncio.run(run_gate(None, ["a.py"], task_id="t"))
    assert r.passed is True
    assert r.indetermine is True, (
        "un workspace absent rend « valide » sans meme incrementer un compteur"
    )

    manquant = _ws() / "nexiste-pas"
    r2 = asyncio.run(run_gate(manquant, ["a.py"], task_id="t"))
    assert r2.passed is True
    assert r2.indetermine is True


def test_aucun_fichier_a_valider_rend_un_resultat_INDETERMINE():
    """Un workspace vide fait rendre `passed=True` avec zero fichier lu. Ce
    n'est pas une validation : c'est une absence de validation."""
    r = asyncio.run(run_gate(_ws(), [], task_id="t"))
    assert r.passed is True
    assert r.indetermine is True, (
        "zero fichier valide est rendu comme une validation reussie"
    )


def test_une_validation_REELLE_reste_determinee():
    """Le test qui protege le lot de lui-meme : si tout devenait indetermine,
    la banniere serait permanente et ne dirait plus rien."""
    ws = _ws()
    (ws / "ok.py").write_text("x = 1\n", encoding="utf-8")
    r = asyncio.run(run_gate(ws, ["ok.py"], task_id="t"))

    assert r.indetermine is False, (
        f"une validation reelle est marquee indeterminee : {r.raison_indetermination!r}"
    )


# ══════════════════════════════════════════════════════════════════════════
#  3. Le constat doit ATTEINDRE celui qui decide
# ══════════════════════════════════════════════════════════════════════════


def test_le_constat_se_formule_pour_l_appelant():
    """`format_feedback` sert aux ERREURS. Un constat n'est pas une erreur :
    il lui faut sa propre formulation, sinon il finirait melange aux erreurs
    de syntaxe dans le meme bloc « corrige ces problemes »."""
    r = GateResult(passed=True, indetermine=True, raison_indetermination="timeout 15.0s")
    note = r.note_indetermination()

    assert note, "le constat n'a aucune formulation"
    assert "timeout 15.0s" in note
    for attendu in ("VERIFICATION", "PAS"):
        assert attendu in note.upper(), f"le constat ne dit pas « {attendu} » : {note!r}"
    assert "corrige" not in note.lower(), (
        "le constat est formule comme une erreur a corriger — le CodeAgent va "
        "tenter de reparer une panne d'infra"
    )


def test_une_gate_determinee_ne_produit_AUCUNE_note():
    """Le bruit tue le signal : meme regle qu'en Z40a."""
    assert GateResult(passed=True).note_indetermination() == ""
    assert GateResult(passed=False, errors=["e"]).note_indetermination() == ""


def test_le_resume_du_codeagent_porte_le_constat():
    """LE test du lot.

    `_enrich_summary` existe explicitement — sa docstring le dit — « pour que
    le parent (ReAct loop) sache exactement ce qui a ete fait ». C'est donc la
    que le constat doit atterrir : c'est le seul canal qui remonte au parent.
    """
    from src.agents.sub_agent import CodeAgent

    agent = object.__new__(CodeAgent)
    agent._session_memory = {}
    agent._gate_indetermine = "timeout 15.0s"

    resume = agent._enrich_summary("Tache terminee.")

    assert "Tache terminee." in resume, "le resume d'origine a ete perdu"
    assert any(m in resume.lower() for m in ("pas pu", "non verifi", "indetermin")), (
        f"le parent ne sait pas que rien n'a ete valide : {resume!r}"
    )
    assert "timeout 15.0s" in resume, "la raison ne remonte pas"


def test_un_resume_sans_constat_reste_intact():
    """Une tache normalement validee ne doit pas se retrouver decoree."""
    from src.agents.sub_agent import CodeAgent

    agent = object.__new__(CodeAgent)
    agent._session_memory = {}

    resume = agent._enrich_summary("Tache terminee.")
    for marqueur in ("pas pu", "non verifi", "indetermin"):
        assert marqueur not in resume.lower(), (
            f"un resume normal porte la note « {marqueur} » : {resume!r}"
        )


# ══════════════════════════════════════════════════════════════════════════
#  4. Ce que le lot ne doit pas casser
# ══════════════════════════════════════════════════════════════════════════


def test_la_signature_de_run_gate_est_inchangee():
    """`run_gate` a deux sites d'appel dans `sub_agent.py`. Changer sa
    signature les toucherait tous les deux — ce ne serait plus un patch
    minimal."""
    import inspect

    sig = inspect.signature(run_gate)
    assert list(sig.parameters) == ["workspace", "modified_files", "task_id", "timeout"]


def test_la_gate_reste_fail_open_sur_les_quatre_chemins(monkeypatch):
    """Le garde-fou du lot : Z40c rend la panne VISIBLE, il ne la transforme
    pas en refus. Si un jour un de ces quatre chemins se met a rendre
    `passed=False`, le CodeAgent sera bloque par une panne d'infra — et ce
    test le dira."""

    async def _casse(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(vg, "_do_validate", _casse)

    resultats = [
        asyncio.run(run_gate(None, ["a.py"])),
        asyncio.run(run_gate(_ws() / "absent", ["a.py"])),
        asyncio.run(run_gate(_ws(), [])),
        asyncio.run(run_gate(_ws(), ["a.py"], timeout=5.0)),
    ]
    for i, r in enumerate(resultats, 1):
        assert r.passed is True, f"le chemin fail-open n°{i} bloque desormais le CodeAgent"


def test_le_compteur_historique_est_toujours_alimente(monkeypatch):
    """Les 50 `lsp_fail_open` du corpus restent comparables : le compteur ne
    doit pas etre remplace par le nouvel etat, il est double par lui."""
    vus: list[str] = []

    async def _casse(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(vg, "_do_validate", _casse)
    import src.utils.gate_metrics as gm

    monkeypatch.setattr(
        gm, "record_lsp_fail_open", lambda **kw: vus.append(str(kw.get("error")))
    )
    asyncio.run(run_gate(_ws(), ["a.py"], task_id="t"))

    assert vus, "le compteur lsp_fail_open historique n'est plus alimente"
