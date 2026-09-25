"""Lot L3-3 - une tache A EFFET ne se coche pas sans preuve d'effet.

Mesure du 16 septembre 2026, sur DEUX runs reels de production (aucune hypothese) :

RUN A (16:19, site-vitrine-2026) - plan de 6 taches, bilan annonce **6/6**,
et **zero fichier ecrit** (verifie sur disque : `script.js` date toujours du 01/09).
  - « Reecrire script.js dans le meme esprit » cochee par un `read_file`.
    `correction_task_blocks_readonly` aurait du la bloquer : il connait deja
    `read_files_batch` et `read_file` (`_READONLY_PLAN_TOOLS`), mais son
    vocabulaire `_CORRECTION_TASK_VERBS` ignore « reecrire ».
  - « Lire app.js » cochee par `read_files_batch`, absent de
    `_EXPLORATION_TOOLS_STRICT` alors que ses voisins y sont tous.

RUN B (16:26, CarroSaaS) - ANALYSE ECARTEE, et la trace de l'erreur est gardee
ici volontairement. J'avais conclu au mensonge : trois gardes refusent
« Verifier le rendu » (`Guard BROWSER-ONLY` l.426, `Verify-gate (sem)` l.547,
`Browser seq sans preuve` l.652) et la tache ressort `[OK]` au bilan. C'etait
FAUX. `react.py:9040` fait `continue` SANS rien cocher quand
`_runtime_result.passed` est faux, et cette branche journalise
`create_project_web_verify_failed` : cette trace est ABSENTE du run. La
verification runtime a donc reussi (Playwright demarre deux fois, DOM inspecte),
et `_mark_web_runtime_plan_verified` - atteint uniquement sur succes - a coche la
tache LEGITIMEMENT, avec une preuve que les gardes textuels ne voient pas.
Mon « aucun serveur, aucune console » venait d'un comptage d'outils incomplet
(le motif `🔧 Outil X appelé` ne voit pas les appels internes).
Regle qui en sort : une absence dans un log ne prouve rien tant qu'on n'a pas
verifie que la trace attendue serait ecrite.

Corpus : 14 demandes d'ecriture sur 175 (8 %) se terminent `done` avec **zero
mutation reussie** au ledger (`task_orchestrator_state.json`, 680 taches).

Ce que ce lot NE fait PAS : il ne touche ni au ledger (le harnais de test le
laisse vide : s'y adosser decocherait 12 scenarios geles RF-4), ni a
`has_sufficient_proof` (kind-aware, pessimiste sur les taches de creation :
meme mesure, 12 gels). Mesure de l'impact retenu : **0 gel RF-4 casse**.
"""
from __future__ import annotations

import pytest

from src.reasoning.plan_evidence import _EXPLORATION_TOOLS_STRICT, get_tool_capabilities
from src.reasoning.plan_progress import correction_task_blocks_readonly
from src.reasoning.react_config import TaskItem


def _boucle(descriptions):
    """Une vraie ReActLoop, comme le harnais gele RF-4 (pas un object.__new__)."""
    from src.reasoning.react import ReActLoop

    b = ReActLoop(llm_chat_func=lambda *a, **kw: None)
    b._task_plan = [TaskItem(description=d) for d in descriptions]
    b._plan_emitted = True
    b._emit_plan_state = lambda **kw: None
    return b


def _faites(b):
    return [t.description for t in b._task_plan if t.completed]


def _restantes(b):
    return [t.description for t in b._task_plan if not t.completed]


# ── 1. RUN A : « reecrire » est une tache a effet ───────────────────────────

def test_reecrire_est_un_verbe_de_correction():
    """Le garde existant connait « corriger » et « reparer », pas « reecrire »."""
    assert correction_task_blocks_readonly(
        "read_file", "Reecrire script.js dans le meme esprit"
    ) is True


def test_une_lecture_ne_coche_pas_une_reecriture():
    """RUN A : `read_file` a coche « Reecrire script.js » — 0 octet ecrit."""
    b = _boucle(["Reecrire script.js dans le meme esprit"])
    b._update_plan_progress("read_file", {"path": "script.js"},
                            "// contenu du fichier, 139 lignes", 4)
    assert "Reecrire script.js dans le meme esprit" in _restantes(b)


# ── 2. RUN A : read_files_batch est un outil de lecture ─────────────────────

def test_read_files_batch_est_un_outil_d_exploration():
    """Ses voisins y sont tous ; son absence est un trou, pas un choix."""
    assert "read_files_batch" in _EXPLORATION_TOOLS_STRICT


def test_read_files_batch_ne_declare_pas_savoir_ecrire():
    """Il herite FILE_WRITE du module `files` faute d'override, comme read_file en a un."""
    caps = get_tool_capabilities("read_files_batch", "files", "files")
    noms = {str(c).replace("ProofCapability.", "") for c in caps}
    assert "FILE_WRITE" not in noms, f"un outil de LECTURE declare ecrire : {noms}"


# ── 3. Ce que le lot ne doit PAS changer (caracterisation) ──────────────────

def test_une_ecriture_prouvee_reste_cochee():
    """Non-regression : le scenario gele `01_ecriture_prouvee` ne bouge pas."""
    b = _boucle(["Creer le fichier index.html"])
    b._update_plan_progress("create_file", {"path": "index.html"},
                            "Fichier cree : index.html (231 octets)", 1)
    assert "Creer le fichier index.html" in _faites(b)


def test_une_tache_de_lecture_reste_cochee_par_une_lecture():
    """Pas de sur-blocage : « Lire » demande bien une lecture, et rien d'autre."""
    b = _boucle(["Lire app.js et style.css dans logtriage"])
    b._update_plan_progress("read_files_batch",
                            {"paths": ["logtriage/app.js", "logtriage/style.css"]},
                            "read_files_batch: 2/2 lu(s)", 2)
    assert "Lire app.js et style.css dans logtriage" in _faites(b)


def test_une_verification_navigateur_prouvee_reste_cochee():
    """Le garde vise l'ECHEC, pas l'outil : une vraie preuve continue de cocher."""
    b = _boucle(["Verifier que le site est operationnel"])
    b._update_plan_progress(
        "browser_verify_local_project",
        {"project_path": "workspace/site"},
        "## Runtime web verify: OK\nHTTP 200 OK\nPage loaded\nDOM ready_state: complete",
        4,
    )
    assert "Verifier que le site est operationnel" in _faites(b)
