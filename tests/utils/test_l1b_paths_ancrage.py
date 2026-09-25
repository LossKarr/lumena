"""Lot L1b-1 - les dossiers `LUMENA_*_DIR` ne dependent plus du dossier de lancement.

Decision de Charles du 15 septembre 2026 : « quand elle cree un truc, elle doit le
faire dans un sous-workspace, peu importe d'ou elle le fait ».

Mesures du 15 septembre : le `.env` reel contient `LUMENA_DATA_DIR=./data`,
`LUMENA_WORKSPACE_DIR=./workspace` et `LUMENA_UPLOADS_DIR=` (vide). `paths.py` les
lisait tels quels : relatif = dossier de lancement, vide = dossier courant. Les
points d'entree qui chargent `.env` avant `paths.py` (`lumena_ultime.py`,
`run_telegram.py`, `run_whatsapp.py`, `run_twitter.py`) en dependaient.

Regle : valeur absente ou vide -> defaut ; relative -> ancree sur la racine
Lumena ; absolue -> inchangee. Sans reglage (production web/bureau), les chemins
restent exactement ceux d'avant. `paths.py` est lu dans un sous-processus pour ne
jamais recharger le module de la suite de tests.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
CLES = ("LUMENA_DATA_DIR", "LUMENA_WORKSPACE_DIR", "LUMENA_LOGS_DIR", "LUMENA_BACKUPS_DIR",
        "LUMENA_GENERATED_IMAGES_DIR", "LUMENA_UPLOADS_DIR", "LUMENA_DOCUMENT_STUDIO_DIR",
        "LUMENA_UPDATES_DIR", "LUMENA_SESSIONS_DB")


# ── 1. Fonction d'ancrage ─────────────────────────────────────────────────────

def _ancrage():
    from src.utils.paths import _dir_from_env
    return _dir_from_env


def test_absente_donne_le_defaut(monkeypatch):
    monkeypatch.delenv("LUMENA_TEST_DIR", raising=False)
    assert _ancrage()("LUMENA_TEST_DIR", ROOT / "defaut") == ROOT / "defaut"


@pytest.mark.parametrize("valeur", ["", "   "])
def test_vide_donne_le_defaut(monkeypatch, valeur):
    monkeypatch.setenv("LUMENA_TEST_DIR", valeur)
    assert _ancrage()("LUMENA_TEST_DIR", ROOT / "defaut") == ROOT / "defaut"


@pytest.mark.parametrize("valeur,attendu", [
    ("./workspace", ROOT / "workspace"),
    ("workspace", ROOT / "workspace"),
    ("donnees/lumena", ROOT / "donnees" / "lumena"),
])
def test_relative_ancree_sur_la_racine(monkeypatch, valeur, attendu):
    monkeypatch.setenv("LUMENA_TEST_DIR", valeur)
    assert _ancrage()("LUMENA_TEST_DIR", ROOT / "defaut") == attendu


def test_absolue_inchangee(monkeypatch, tmp_path):
    monkeypatch.setenv("LUMENA_TEST_DIR", str(tmp_path / "ailleurs"))
    assert _ancrage()("LUMENA_TEST_DIR", ROOT / "defaut") == tmp_path / "ailleurs"


# ── 2. paths.py reel, lance depuis un dossier etranger ────────────────────────

_LECTURE = (
    "import json; from src.utils import paths as p; "
    "print(json.dumps({k: str(getattr(p, k)) for k in ("
    "'DATA_DIR', 'WORKSPACE_DIR', 'LOGS_DIR', 'BACKUPS_DIR', 'GENERATED_IMAGES_DIR', "
    "'RECEIVED_DOCS_DIR', 'DOCUMENT_STUDIO_DIR', 'UPDATES_DIR', 'SESSIONS_SQLITE')}))"
)


def _chemins(cwd: Path, reglages: dict) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in CLES}
    env.update(reglages)
    env["PYTHONPATH"] = str(ROOT)
    sortie = subprocess.run([sys.executable, "-c", _LECTURE], cwd=cwd, env=env, capture_output=True,
                            text=True, timeout=120, check=True)
    return {k: Path(v) for k, v in json.loads(sortie.stdout.strip().splitlines()[-1]).items()}


DEFAUTS = {
    "DATA_DIR": ROOT / "data",
    "WORKSPACE_DIR": ROOT / "workspace",
    "LOGS_DIR": ROOT / "data" / "logs",
    "BACKUPS_DIR": ROOT / "backups",
    "GENERATED_IMAGES_DIR": ROOT / "workspace" / "images",
    "RECEIVED_DOCS_DIR": ROOT / "data" / "received_documents",
    "DOCUMENT_STUDIO_DIR": ROOT / "data" / "document_studio",
    "UPDATES_DIR": ROOT / "data" / "updates",
    "SESSIONS_SQLITE": ROOT / "data" / "sessions.sqlite",
}


def test_sans_reglage_chemins_de_production_inchanges(tmp_path):
    """Caracterisation : web/bureau (aucune variable) = exactement les defauts."""
    assert _chemins(tmp_path, {}) == DEFAUTS


def test_reglages_du_env_reel_ancres_peu_importe_le_lancement(tmp_path):
    """`./data`, `./workspace` et un dossier d'uploads vide, lances hors du depot."""
    chemins = _chemins(tmp_path, {"LUMENA_DATA_DIR": "./data", "LUMENA_WORKSPACE_DIR": "./workspace",
                                  "LUMENA_UPLOADS_DIR": ""})
    assert chemins == DEFAUTS


def test_reglage_absolu_respecte(tmp_path):
    ailleurs = tmp_path / "espace"
    chemins = _chemins(tmp_path, {"LUMENA_WORKSPACE_DIR": str(ailleurs)})
    assert chemins["WORKSPACE_DIR"] == ailleurs
    assert chemins["GENERATED_IMAGES_DIR"] == ailleurs / "images"
    assert chemins["DATA_DIR"] == ROOT / "data"
