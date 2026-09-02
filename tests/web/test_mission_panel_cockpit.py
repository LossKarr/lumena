"""Contrats du cockpit Missions et de la Constellation progressive."""
from __future__ import annotations

import pytest
import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PANEL = ROOT / "web/static/js/mission_panel.js"
MODEL = ROOT / "web/static/js/mission_model.js"
VIEWS = ROOT / "web/static/js/mission_views.js"
SCENE = ROOT / "web/static/js/mission_scene.js"
PANELS = ROOT / "web/static/js/panels.js"
CSS = ROOT / "web/static/css/mission-panel.css"


def _node(expr: str):
    script = (
        f"require({json.dumps(str(PANEL))});"
        f"const P=globalThis.missionPanel;const out=({expr});"
        "process.stdout.write(JSON.stringify(out));"
    )
    result = subprocess.run(
        ["node", "-e", script], cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", timeout=15,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_preferences_du_cockpit_sont_normalisees_et_bornees():
    out = _node(
        "P.normalise({filter:'attention',query:'abc',selectedMission:'m1',selectedWorker:'w1'})"
    )
    assert out["filter"] == "attention"
    assert out["query"] == "abc"
    assert out["selectedMission"] == "m1"
    assert out["selectedWorker"] == "w1"


def test_filtrage_recherche_mission_worker_et_fichier():
    mission = {
        "id": "m1",
        "aggregate": "running",
        "objective": "Construire le tableau",
        "thought": "",
        "children": [{
            "id": "w1", "objective": "Worker API", "thought": "Analyse",
            "lastTool": "read_file", "perimeter": ["src/api.py"],
        }],
    }
    encoded = json.dumps(mission, ensure_ascii=False)
    assert _node(f"P.correspond({encoded},P.normalise({{query:'src/api.py'}}))") is True
    assert _node(f"P.correspond({encoded},P.normalise({{query:'introuvable'}}))") is False


def test_navigateur_nomine_selection_et_echappe_objectif():
    missions = [{
        "id": "m1", "aggregate": "failed", "objective": "<script>x</script>",
        "workers": {"done": 1, "total": 2}, "children": [], "deadlineLabel": "",
    }]
    html = _node(
        "P.rendreNavigateur(%s,P.normalise({selectedMission:'m1'}),'m1')"
        % json.dumps(missions)
    )
    assert 'data-mp-mission="m1"' in html
    assert 'aria-current="true"' in html
    assert "<script>" not in html and "&lt;script&gt;" in html


@pytest.mark.skip(reason="Constellation retiree du panneau (lot 17) — la couche 3D reste sur le disque, dormante : ce contrat redeviendra verifiable si la vue revient")
def test_constellation_garde_svg_et_ajoute_canvas_progressif():
    script = (
        f"require({json.dumps(str(MODEL))});"
        f"require({json.dumps(str(VIEWS))});"
        "const m=[{id:'m1',state:'running',aggregate:'running',objective:'Mission',"
        "children:[{id:'w1',state:'running',objective:'[Worker api] Test',thought:'p',"
        "iteration:1,maxIter:4,lastTool:'read_file',waitedMs:0,trail:[],logs:[],events:1,"
        "queueRank:0,perimeter:['a.py'],proofs:[]}],workers:{done:0,total:1},"
        "terminal:false,deadlineLabel:'',proofs:[],delivered:{published:[],artifacts:[]}}];"
        "process.stdout.write(globalThis.missionRenderView('constellation',m,{selectedWorker:'w1'}));"
    )
    result = subprocess.run(
        ["node", "-e", script], cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", timeout=15,
    )
    assert result.returncode == 0, result.stderr
    html = result.stdout
    assert "mp-scene-canvas" in html
    assert "mp-scene-fallback" in html and "<svg" in html
    assert 'data-mp-worker="w1"' in html
    assert "Worker sélectionné" in html


def test_scene_est_locale_progressive_et_disposable():
    source = SCENE.read_text(encoding="utf-8")
    assert "../vendor/three.module.min.js" in source
    assert "WebGLRenderer" in source
    assert "powerPreference: 'low-power'" in source
    assert "is-fallback" in source and "disposeInstance" in source
    assert "mission:worker-select" in source
    assert "data-mp-frame" in source
    assert "data-mp-canvas-color-pixels" in source


def test_le_flux_recent_amorce_le_journal_avant_le_sse():
    source = PANELS.read_text(encoding="utf-8")
    assert "/api/trace/recent?limit=1000" in source
    assert source.index("/api/trace/recent?limit=1000") < source.index("_initMissionStream();")


def test_journal_complet_et_battement_court_sont_distincts():
    source = MODEL.read_text(encoding="utf-8")
    assert "trail.length > 12" in source
    assert "history.length > 400" in source
    assert "logs:" in source
    assert "w.logs || w.trail" in VIEWS.read_text(encoding="utf-8")


def test_styles_couvrent_cockpit_scene_et_mobile():
    css = CSS.read_text(encoding="utf-8")
    for marker in (
        ".mp-command", ".mp-workspace", ".mp-nav-item", ".mp-stage",
        ".mp-scene-canvas", ".mp-scene-fallback", ".mp-node-pick",
    ):
        assert marker in css
    assert "@media (max-width: 760px)" in css


def test_scene_charge_avant_le_chassis():
    source = PANELS.read_text(encoding="utf-8")
    assert "mission_scene.js" in source
    assert source.index("mission_scene.js") < source.index("mission_panel.js")


def test_le_contenu_du_bouton_worker_ne_bloque_pas_sa_selection():
    source = PANELS.read_text(encoding="utf-8")
    assert "controleImbrique && controleImbrique !== cible" in source


def test_quitter_missions_libere_scene_et_rendus_differees():
    source = PANELS.read_text(encoding="utf-8")
    i = source.index("export function closeMissionStream()")
    block = source[i:i + 700]
    assert "clearTimeout(_mpRedrawT)" in block
    assert "clearTimeout(_missionRefreshT)" in block
    assert "window.missionScene.dispose" in block
