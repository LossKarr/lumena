"""Le lead reste observable dans les quatre vues, meme sans worker.

Une mission solo possedait deja sa pensee et ses traces dans le modele, mais
Atelier reconstruisait un objet incomplet tandis que Ruban, Controle et
Constellation ne rendaient que les enfants. Le cas reel `checkpointed` avec
zero worker produisait donc trois ecrans presque vides.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[2]
JS = ROOT / "web" / "static" / "js"
CSS = ROOT / "web" / "static" / "css" / "mission-panel.css"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node indisponible")

TREE = [{
    "mission": {
        "task_id": "mission_solo",
        "state": "checkpointed",
        "metadata": {"objective": "Creer un script Python robuste"},
    },
    "children": [],
}]

EVENTS = [
    {
        "task_id": "mission_solo",
        "stage": "codeagent_iteration",
        "thought": "Tous les tests sont concluants, je prepare la revue.",
        "iteration": 9,
        "max_iter": 10,
        "tool_name": "run_command",
        "summary": "pytest: 12 passed",
        "status": "ok",
        "ts": "2026-09-01T12:00:00Z",
    },
    {
        "task_id": "mission_solo",
        "stage": "tool_result",
        "tool_name": "write_file",
        "summary": "workspace/scripts/password_generator.py",
        "status": "ok",
        "ts": "2026-09-01T12:00:01Z",
    },
]

PREFS = {
    "blocks": {
        "thought": True,
        "perimeter": True,
        "queue": True,
        "countdown": True,
        "rawlog": True,
    },
    "selectedWorker": "",
}


def _node(body: str):
    script = (
        "require(%s);require(%s);"
        "const B=globalThis.buildMissionModel,V=globalThis.missionRenderView;"
        "process.stdout.write(JSON.stringify((function(){%s})()));"
        % (
            json.dumps(str(JS / "mission_model.js")),
            json.dumps(str(JS / "mission_views.js")),
            body,
        )
    )
    proc = subprocess.run(
        ["node", "-e", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(ROOT),
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _render(view: str) -> str:
    return _node(
        "const m=B(%s,%s,Date.now());return V(%s,m,%s);"
        % (json.dumps(TREE), json.dumps(EVENTS), json.dumps(view), json.dumps(PREFS))
    )


def test_le_modele_conserve_les_faits_du_lead_solo():
    model = _node("return B(%s,%s,Date.now())[0];" %
                  (json.dumps(TREE), json.dumps(EVENTS)))
    assert model["children"] == []
    assert model["thought"].startswith("Tous les tests")
    assert model["events"] == 2
    assert len(model["logs"]) == 2


@pytest.mark.parametrize("view", ["workshop", "ribbon", "control"])
def test_chaque_vue_montre_l_activite_du_lead_sans_worker(view):
    html = _render(view)
    assert "Tous les tests sont concluants" in html
    assert "write_file" in html
    assert "password_generator.py" in html
    assert "Activité récente · 2" in html


def test_atelier_ne_reconstruit_plus_un_lead_sans_journal():
    html = _render("workshop")
    assert "Journal brut · 2" in html
    assert 'data-mp-worker="mission_solo"' not in html


def test_ruban_a_une_piste_et_un_resume_pour_le_lead():
    html = _render("ribbon")
    assert "Lead de mission" in html
    assert '<span class="mp-lane-name mp-mono">Lead</span>' in html
    assert "mp-log" not in html, "le ruban garde une activite courte, pas le journal brut"


def test_controle_montre_le_lead_avant_les_workers_absents():
    html = _render("control")
    assert "Exécution · Lead" in html
    assert "mp-lead-row" in html
    assert "Aucun worker" not in html
def test_les_traces_du_lead_sont_echappees_dans_toutes_les_vues():
    events = [{
        "task_id": "mission_solo",
        "stage": "tool_result",
        "tool_name": "write_file",
        "summary": "<img src=x onerror=alert(1)>",
    }]
    rendered = _node(
        "const m=B(%s,%s,Date.now());return %s.map(v=>V(v,m,%s));"
        % (
            json.dumps(TREE),
            json.dumps(events),
            json.dumps(["workshop", "ribbon", "control"]),
            json.dumps(PREFS),
        )
    )
    assert all("<img" not in html and "&lt;img" in html for html in rendered)


@pytest.mark.parametrize("selector", [
    ".mp-live",
    ".mp-ribbon-lead",
    ".mp-lead-row",
    ".mp-scene-readout",
])
def test_chaque_nouveau_bloc_visible_est_style(selector):
    assert selector in CSS.read_text(encoding="utf-8")
