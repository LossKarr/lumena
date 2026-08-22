"""I1/I2 — un livrable RÉDACTIONNEL n'est pas du code.

Run « comparatif vectoriel » (2026-08-13, `task_0ce081e4…`). Mission de recherche
pure : 5 workers, 5 fichiers `.md`. Le lead a déclaré des `exports` Python sur ces
documents, et le générateur de stubs a produit, dans
`comparatif_bases_vectorielles.md` :

    # Tableau comparatif final des 4 bases… — stub de contrat.
    SIGNATURE FIGÉE PAR LE CONTRAT — NE PAS MODIFIER.
    - def comparatif() -> str: retourne le tableau markdown complet

En Markdown, `#` n'est pas un commentaire : c'est un TITRE. Le worker recevait
donc un document lui ordonnant de ne pas modifier des signatures Python. Il a
obéi — le livrable contractuel a été rendu rempli de `def generer_tableau(...)`
et de `from rapport_chromadb import rapport_chromadb`.

Conséquence mesurée : **le livrable du contrat était inutilisable**, et le lead a
créé `comparatif.md` HORS contrat pour livrer le vrai tableau. La mission s'est
close `completed`, sans réserve.

Deux verrous : le stub documentaire (I1) et le refus, à l'écriture du contrat,
de déclarer des signatures sur un document (I2).
"""
from __future__ import annotations

from src.subagents.mission_contract import generate_stub, validate_contract

# Le contrat EXACT du run.
_RUN_ENTRY = {
    "path": "comparatif_bases_vectorielles.md",
    "owner": "w_consolidateur",
    "desc": "Tableau comparatif final des 4 bases. N'utiliser QUE les données "
            "des rapports des workers.",
    "exports": ["def comparatif() -> str: retourne le tableau markdown complet",
                "def generer_tableau(rapports: dict) -> str"],
}


# ── I2 : le contrat refuse des signatures sur un document ───────────────────

def test_the_exact_contract_of_the_run_is_refused():
    errors = validate_contract({"files": [_RUN_ENTRY]})
    assert any("document, pas du code" in e for e in errors)


def test_the_error_teaches_the_right_shape():
    """Leçon MotCompteur/RéservaSalle : une erreur non guidante fait boucler."""
    msg = [e for e in validate_contract({"files": [_RUN_ENTRY]})
           if "document" in e][0]
    assert "desc" in msg and "SECTIONS" in msg


def test_plain_sections_are_accepted():
    assert validate_contract({"files": [{
        "path": "comparatif.md", "owner": "w",
        "desc": "Tableau comparatif des 4 bases",
        "exports": ["Tableau comparatif", "Sources détaillées", "Méthode"],
    }]}) == []


def test_a_document_without_exports_is_fine():
    assert validate_contract({"files": [
        {"path": "rapport.md", "owner": "w", "desc": "Rapport factuel"}]}) == []


def test_every_documentary_extension_is_covered():
    for ext in (".md", ".markdown", ".txt", ".rst", ".adoc"):
        errors = validate_contract({"files": [
            {"path": f"doc{ext}", "owner": "w", "exports": ["def f() -> int"]}]})
        assert any("document, pas du code" in e for e in errors), ext


def test_import_lines_are_caught_too():
    errors = validate_contract({"files": [{
        "path": "notes.md", "owner": "w",
        "exports": ["from rapport_chromadb import rapport_chromadb"]}]})
    assert any("document, pas du code" in e for e in errors)


def test_python_files_are_unaffected():
    """Le risque du lot : casser la validation du CODE, qui EXIGE des signatures."""
    assert validate_contract({"files": [
        {"path": "app.py", "owner": "w", "exports": ["def run() -> None"]}]}) == []


def test_a_python_file_without_signature_is_still_refused():
    errors = validate_contract({"files": [{"path": "app.py", "owner": "w"}]})
    assert errors and any("signatures" in e for e in errors)


# ── I1 : le stub d'un document est un document ──────────────────────────────

def _stub(entry=None):
    return generate_stub(entry or {
        "path": "comparatif.md", "owner": "w",
        "desc": "Tableau comparatif des 4 bases",
        "exports": ["Tableau comparatif", "Sources", "Méthode"],
    })


def test_the_document_stub_never_freezes_anything():
    """« SIGNATURE FIGÉE — NE PAS MODIFIER » dans un document dit au worker de
    ne pas écrire le document."""
    assert "FIGÉE" not in _stub()
    assert "NE PAS MODIFIER" not in _stub()


def test_the_document_stub_tells_the_worker_to_replace_everything():
    assert "Remplace INTÉGRALEMENT" in _stub()


def test_the_document_stub_forbids_code_explicitly():
    out = _stub()
    assert "pas du code" in out and "`def`" in out


def test_the_subject_is_carried_over():
    assert "Tableau comparatif des 4 bases" in _stub()


def test_sections_become_a_plan():
    out = _stub()
    assert "## Plan attendu" in out and "- Sources" in out


def test_the_title_comes_from_the_filename():
    out = generate_stub({"path": "rapport_qdrant.md", "owner": "w"})
    assert out.startswith("# rapport qdrant")


def test_a_document_without_desc_still_produces_a_document():
    out = generate_stub({"path": "notes.md", "owner": "w"})
    assert out.startswith("#")
    assert "def " not in out


def test_code_stubs_are_untouched():
    """Zéro régression sur les stubs de CODE, qui doivent rester figés."""
    py = generate_stub({"path": "app.py", "owner": "w",
                        "exports": ["def run() -> None"]})
    assert "def run() -> None" in py
    js = generate_stub({"path": "app.js", "owner": "w",
                        "exports": ["function go()"]})
    assert "function go" in js


def test_unknown_extensions_keep_the_historical_fallback():
    """`.json`, `.yml`… gardent le stub générique — hors périmètre de ce lot."""
    out = generate_stub({"path": "conf.yml", "owner": "w", "desc": "config"})
    assert "stub de contrat" in out
