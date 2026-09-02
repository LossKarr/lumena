"""Run LogTriage (2026-08-29) — servir un front ne prouve pas que l'app marche.

Le contrat demandait une API FastAPI dans `api.py`. `serve_website` a servi le
dossier en mode STATIQUE. L'agent a navigue, clique « Analyser », et la vision a
lu « aucune anomalie trouvee » sur un log qui contenait un CRITICAL et un ERROR :
le `fetch` de la page tombait sur un 404, faute de backend.

La tache de plan « servir le site et verifier au navigateur » etait donc
STRUCTURELLEMENT improuvable — et rien ne le disait.

Le fait etait pourtant AFFICHE : `serve_website` ecrivait
« ⚙️ Mode: statique (fichiers seuls) ». Il n'etait juste jamais rapproche de
« ce projet a une API ». Encore le meme motif.

--- Pourquoi `_flask_entry` ne suffisait pas ---

Il ne regarde que `app.py` contenant `Flask(` ou `create_app`. FastAPI, Express,
NestJS et tout backend nomme autrement passaient au travers sans un mot.

--- Ancrage sur des FAITS ---

`unserved_backend` lit des NOMS DE SYMBOLES (`FastAPI(`, `express()`,
`APIRouter(`), pas du vocabulaire. On ne devine aucune intention : soit le
fichier declare une API, soit il n'en declare pas.
"""

from __future__ import annotations

import pytest

from src.tools.website_builder import unserved_backend


@pytest.fixture()
def site(tmp_path):
    (tmp_path / "index.html").write_text("<html><body></body></html>", encoding="utf-8")
    (tmp_path / "style.css").write_text("body{}", encoding="utf-8")
    (tmp_path / "app.js").write_text("console.log(1)", encoding="utf-8")
    return tmp_path


# ══════════════════════════════════════════════════════════════════════════
#  1. Le cas du run
# ══════════════════════════════════════════════════════════════════════════


def test_LE_cas_du_run_fastapi_servi_en_statique_est_signale(site):
    (site / "api.py").write_text(
        "from fastapi import FastAPI\napp = FastAPI()\n", encoding="utf-8")
    assert unserved_backend(site, "static") == "api.py"


@pytest.mark.parametrize("nom,contenu", [
    ("api.py", "from fastapi import FastAPI\napp = FastAPI()"),
    ("serveur.py", "from flask import Flask\napp = Flask(__name__)"),
    ("main.py", "from fastapi import APIRouter\nr = APIRouter()"),
    ("backend.py", "def create_app():\n    pass"),
    ("server.js", "const app = express()"),
    ("index.js", "const e = require('express')"),
])
def test_les_backends_courants_sont_tous_vus(site, nom, contenu):
    (site / nom).write_text(contenu, encoding="utf-8")
    assert unserved_backend(site, "static") == nom


# ══════════════════════════════════════════════════════════════════════════
#  2. Aucun faux positif
# ══════════════════════════════════════════════════════════════════════════


def test_un_site_purement_statique_ne_declenche_rien(site):
    assert unserved_backend(site, "static") == ""


def test_un_script_python_qui_n_est_PAS_une_api_ne_declenche_rien(site):
    (site / "outils.py").write_text(
        "def additionner(a, b):\n    return a + b\n", encoding="utf-8")
    (site / "genere.py").write_text(
        "import json\nprint(json.dumps({'a': 1}))\n", encoding="utf-8")
    assert unserved_backend(site, "static") == ""


def test_un_dossier_vide_ne_declenche_rien(tmp_path):
    assert unserved_backend(tmp_path, "static") == ""


def test_un_chemin_inexistant_ne_leve_pas(tmp_path):
    assert unserved_backend(tmp_path / "nulle-part", "static") == ""


# ══════════════════════════════════════════════════════════════════════════
#  3. Quand le backend TOURNE, on se tait
# ══════════════════════════════════════════════════════════════════════════


def test_en_mode_flask_le_backend_tourne_donc_aucun_avertissement(site):
    (site / "app.py").write_text(
        "from flask import Flask\napp = Flask(__name__)\n", encoding="utf-8")
    assert unserved_backend(site, "flask") == ""


def test_seule_la_RACINE_est_regardee(site):
    """Un backend enfoui dans un sous-dossier n'est pas ce que le serveur sert."""
    sous = site / "vendor"
    sous.mkdir()
    (sous / "api.py").write_text("from fastapi import FastAPI\napp = FastAPI()", encoding="utf-8")
    assert unserved_backend(site, "static") == ""


# ══════════════════════════════════════════════════════════════════════════
#  4. L'avertissement atteint bien l'observation
# ══════════════════════════════════════════════════════════════════════════


def test_l_avertissement_est_pose_dans_la_sortie_de_serve_website():
    """Sans ca, le fait serait calcule puis jete — le defaut qu'on ferme."""
    import ast
    import pathlib

    src = (pathlib.Path(__file__).resolve().parents[2]
           / "src" / "tools" / "website_builder.py").read_text(encoding="utf-8")
    arbre = ast.parse(src)
    appels = [n for n in ast.walk(arbre)
              if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "unserved_backend"]
    assert appels, "unserved_backend n'est appele nulle part : la mesure ne sert a rien"
    assert "BACKEND NON LANCÉ" in src, "l'avertissement n'apparait pas dans la sortie"
    assert "NE PROUVE PAS" in src, (
        "l'avertissement doit dire explicitement que la verification navigateur "
        "ne prouve rien ici"
    )
