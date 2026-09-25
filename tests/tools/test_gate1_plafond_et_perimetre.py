"""Lot GATE-1 - la gate de validation tient son plafond et son perimetre.

**Defaut trouve en RUN REEL le 23 septembre 2026.** Charles demande une ressource
FiveM de 8 fichiers. Le CodeAgent la cree proprement en 2 minutes. Puis Lumena
reste **muette 19 minutes** (16:45:06 -> 17:04:01, `gate_fail` au journal), et
conclut par un ECHEC portant sur des fichiers d'un AUTRE projet :

    [ERROR] cinema-motion-studio/script.js:1 - XREF_JS_MISSING_ID
    [ERROR] 2026-08-10/motionride.../node_modules/@remotion/studio/dist/...js:83
    [ERROR] 2026-08-10/motionride.../node_modules/@remotion/.../chunk-ptnd65a9.js:12963

--- Les trois causes, mesurees ---

1. **`code_validator.py` l.972 lit TOUT le workspace.** `project_dir.rglob("*")`
   suivi de `read_text` sur chaque fichier, avec pour seul filtre « pas de
   composant commencant par un point ». Mesure : **48 668 fichiers, 1,23 Go, dont
   90 % de `node_modules`** - lus en memoire pour valider 8 fichiers.
   `_do_validate` l.160 exclut pourtant DEJA `node_modules` dans son fallback :
   deux endroits, deux regles, elles ont diverge.
2. **Le perimetre est le workspace, pas le projet.** D'ou l'echec sur
   `cinema-motion-studio`, sans aucun rapport avec la tache.
3. **Le plafond ne tient pas.** `run_gate` enveloppe l'appel dans
   `asyncio.wait_for(timeout=15)`, mais `validate_project_async` l.1101 appelle
   `validate_project` en SYNCHRONE - commentaire a l'appui : « (synchrone,
   rapide) ». Sans point d'`await`, `wait_for` ne peut RIEN interrompre : mesure
   reelle **1 187 s, soit 79 fois le plafond annonce**.

Et le code le savait : le commentaire Z40c de `sub_agent.py` releve « 50 fail-open
sur 197 executions au corpus reel, dont **50/50 par timeout a ce timeout de 15 s** ».
**Un quart des executions reelles tombait deja sur ce defaut.**

--- Pourquoi le plafond passe en premier ---

Les exclusions et le perimetre reduisent la PROBABILITE du gel ; seul un plafond
qui coupe vraiment le rend impossible. Un garde-fou qui ne garde pas est pire que
pas de garde-fou : il fait croire que la borne existe.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

# Marque posee par test, jamais globalement : trois tests sont synchrones.
asynchrone = pytest.mark.asyncio


def _projet(racine: Path) -> Path:
    """Un workspace realiste : un projet edite, un autre projet, des node_modules."""
    edite = racine / "projects" / "lm_petcompanion"
    (edite / "nui").mkdir(parents=True)
    (edite / "server.lua").write_text("local x = 1\n", encoding="utf-8")
    (edite / "nui" / "app.js").write_text("document.getElementById('app');\n", encoding="utf-8")
    (edite / "nui" / "index.html").write_text('<div id="app"></div>\n', encoding="utf-8")

    autre = racine / "cinema-motion-studio"
    autre.mkdir()
    (autre / "script.js").write_text("document.getElementById('film-bg');\n", encoding="utf-8")

    lourd = racine / "vieux-projet" / "node_modules" / "@remotion" / "studio" / "dist"
    lourd.mkdir(parents=True)
    (lourd / "chunk-abc.js").write_text("document.getElementById('render-modal-button');\n", encoding="utf-8")
    for nom in ("dist", "build", "out", "coverage", "vendor", "__pycache__"):
        sous = racine / "vieux-projet" / nom
        sous.mkdir(parents=True, exist_ok=True)
        (sous / "bundle.js").write_text("document.getElementById('absent-partout');\n", encoding="utf-8")
    return edite


# ── GATE-1c : le plafond doit COUPER ────────────────────────────────────────

@asynchrone
async def test_le_plafond_coupe_une_validation_synchrone_trop_longue(tmp_path, monkeypatch):
    """Aujourd'hui `wait_for` ne peut rien interrompre : la mesure reelle a fait 79x."""
    from src.tools import code_validator, verification_gate

    _projet(tmp_path)

    def validation_lente(files, project_dir=None, **_):
        time.sleep(3.0)  # SYNCHRONE, comme la vraie
        return code_validator.ValidationReport(files_checked=0)

    monkeypatch.setattr(code_validator, "validate_project", validation_lente)

    debut = time.monotonic()
    resultat = await verification_gate.run_gate(
        tmp_path, ["projects/lm_petcompanion/server.lua"], task_id="t", timeout=0.5)
    ecoule = time.monotonic() - debut

    assert ecoule < 2.0, f"le plafond n'a pas coupe : {ecoule:.1f}s pour un timeout de 0,5s"
    assert resultat.indetermine, "un depassement doit etre annonce, jamais passe pour une validation"


@asynchrone
async def test_l_event_loop_reste_vivant_pendant_la_validation(tmp_path, monkeypatch):
    """Une validation synchrone gele TOUT le processus : le chat, les missions, le reste."""
    from src.tools import code_validator, verification_gate

    _projet(tmp_path)
    battements = []

    def validation_lente(files, project_dir=None, **_):
        time.sleep(1.5)
        return code_validator.ValidationReport(files_checked=0)

    monkeypatch.setattr(code_validator, "validate_project", validation_lente)

    async def horloge():
        while True:
            await asyncio.sleep(0.1)
            battements.append(time.monotonic())

    # Le compte doit etre releve QUAND la gate finit, pas apres : avec `gather`,
    # l'horloge rattraperait ses battements une fois l'event loop degele et le
    # test passerait en prouvant exactement RIEN. Erreur commise en l'ecrivant.
    tic = asyncio.create_task(horloge())
    try:
        await verification_gate.run_gate(
            tmp_path, ["projects/lm_petcompanion/server.lua"], task_id="t", timeout=3.0)
        pendant = len(battements)
    finally:
        tic.cancel()
        await asyncio.gather(tic, return_exceptions=True)

    assert pendant >= 8, (
        f"l'event loop a ete gele : {pendant} battements pendant ~1,5 s au lieu de ~15 - "
        "le chat et les missions s'arretent pendant une validation"
    )


# ── GATE-1a : les dossiers de dependances ne sont JAMAIS lus ────────────────

def test_les_dossiers_de_dependances_sont_exclus(tmp_path):
    from src.tools.code_validator import validate_project

    _projet(tmp_path)
    rapport = validate_project(
        {"projects/lm_petcompanion/server.lua": "local x = 1\n"}, tmp_path)

    vus = " ".join(str(i.file_path) for i in rapport.issues)
    for interdit in ("node_modules", "/dist/", "/build/", "/out/", "coverage", "vendor", "__pycache__"):
        assert interdit not in vus, f"{interdit} a ete lu et valide : {vus[:300]}"


def test_une_seule_definition_des_exclusions():
    """Deux listes separees divergent - c'est deja arrive entre la gate et le validateur."""
    from src.tools.code_validator import EXCLUDED_DIRECTORIES

    for attendu in ("node_modules", "dist", "build", "out", "coverage", "vendor", "__pycache__"):
        assert attendu in EXCLUDED_DIRECTORIES, attendu

    # La gate doit PARTAGER le filtre, pas recopier la liste : c'est
    # `is_excluded_path` qui fait foi, la constante n'etant que sa source.
    source = Path("src/tools/verification_gate.py").read_text(encoding="utf-8")
    assert "is_excluded_path" in source, (
        "la gate maintient sa propre liste au lieu de partager celle du validateur"
    )
    assert '"node_modules"' not in source, (
        "la gate renomme des dossiers en dur : la liste va rediverger"
    )


# ── GATE-1b : le perimetre est le PROJET, pas le workspace ──────────────────
#
# Mesure faite avant d'ecrire : les AUTRES appelants de `validate_project`
# (`project.py`, `remotion.py`, `website.py`) passent deja `project_dir=base_dir`,
# le dossier du projet. Leur scan est donc legitime et borne. **Seule la gate
# transmet le workspace entier** (`self._task_workspace_root`). La correction
# appartient donc a la GATE, pas au validateur - c'est aussi ce qui ne casse rien.

@asynchrone
async def test_la_gate_ne_valide_pas_un_autre_projet_du_workspace(tmp_path):
    """L'echec reel portait sur `cinema-motion-studio`, etranger a la tache."""
    from src.tools.verification_gate import run_gate

    _projet(tmp_path)
    resultat = await run_gate(
        tmp_path, ["projects/lm_petcompanion/nui/app.js"], task_id="t", timeout=30.0)

    vus = " ".join(resultat.errors + resultat.warnings)
    assert "cinema-motion-studio" not in vus, f"un projet etranger a ete valide : {vus[:300]}"
    assert "node_modules" not in vus, f"des dependances ont ete validees : {vus[:300]}"


@asynchrone
async def test_le_contexte_du_projet_edite_reste_disponible(tmp_path):
    """Le perimetre se resserre, mais le XREF doit garder le HTML voisin du JS.

    Sans cela, restreindre casserait la verification croisee que la gate existe
    pour faire : `app.js` doit voir `index.html` du MEME projet.
    """
    from src.tools.verification_gate import run_gate

    edite = _projet(tmp_path)
    (edite / "nui" / "app.js").write_text(
        "document.getElementById('absent-ici');\n", encoding="utf-8")

    resultat = await run_gate(
        tmp_path, ["projects/lm_petcompanion/nui/app.js"], task_id="t", timeout=30.0)

    vus = " ".join(resultat.errors + resultat.warnings)
    assert "absent-ici" in vus, (
        "le XREF ne voit plus le HTML du projet : le perimetre est trop etroit"
    )


@asynchrone
async def test_sans_fichier_modifie_la_gate_ne_scanne_pas_tout(tmp_path):
    """Le fallback ne doit pas redevenir la porte d'entree du workspace entier."""
    from src.tools.verification_gate import run_gate

    _projet(tmp_path)
    resultat = await run_gate(tmp_path, [], task_id="t", timeout=30.0)

    vus = " ".join(resultat.errors + resultat.warnings)
    assert "node_modules" not in vus, vus[:300]


# ── GATE-1d : le budget du LSP tient DANS celui de la gate ──────────────────
#
# Mesure du 23 septembre 2026, apres avoir rendu le plafond effectif :
#
#     validate_project (statique) :  0,00 s
#     validate_project_async      : 31,69 s
#
# Les 31 s ne viennent pas du scan mais du LSP. `pyright` s'initialise en
# **0,3 s**, puis `wait_diagnostics` attend un evenement qui **n'arrive jamais si
# le fichier est propre** - donc le timeout entier. Mesure isolee : 20,86 s pour
# rendre « 0 diagnostic(s) ».
#
# Et `code_validator` impose `lsp_check_project(timeout=15.0)` EN DUR, alors que
# la gate entiere dispose de 15 a 20 s : le budget du composant egale celui de son
# appelant, donc le depassement est garanti par construction.
#
# Le constat etait deja ecrit dans `verification_gate.py`, date du 29 aout :
# « 3 timeouts sur 3, alors que pyright s'initialisait en 0,48 s - le budget ne
# partait pas au demarrage du serveur de langage, mais bien dans la phase
# d'apres. » Jamais corrige.

@asynchrone
async def test_le_budget_du_lsp_est_strictement_inferieur_a_celui_de_la_gate(tmp_path):
    from src.tools import code_validator

    vu = {}

    async def lsp_espion(project_dir, files=None, timeout=20.0):
        vu["timeout"] = timeout
        return []

    import src.tools.lsp_client as lsp
    original = lsp.lsp_check_project
    lsp.lsp_check_project = lsp_espion
    try:
        (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
        await code_validator.validate_project_async(
            {"a.py": "x = 1\n"}, tmp_path, lsp_timeout=4.0)
    finally:
        lsp.lsp_check_project = original

    assert vu.get("timeout") == 4.0, (
        f"le budget de la gate n'atteint pas le LSP : {vu.get('timeout')} au lieu de 4,0"
    )


@asynchrone
async def test_la_gate_rend_la_main_dans_son_budget_avec_un_lsp_lent(tmp_path):
    """Le cas reel : une validation propre qui depassait 20 s a cause du LSP."""
    import src.tools.lsp_client as lsp
    from src.tools.verification_gate import run_gate

    async def lsp_lent(project_dir, files=None, timeout=20.0):
        await asyncio.sleep(timeout)  # exactement ce que fait `wait_diagnostics`
        return []

    original = lsp.lsp_check_project
    lsp.lsp_check_project = lsp_lent
    try:
        _projet(tmp_path)
        debut = time.monotonic()
        await run_gate(tmp_path, ["projects/lm_petcompanion/server.lua"],
                       task_id="t", timeout=6.0)
        ecoule = time.monotonic() - debut
    finally:
        lsp.lsp_check_project = original

    assert ecoule < 7.5, f"la gate a depasse son budget de 6 s : {ecoule:.1f}s"
