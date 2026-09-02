"""Contrats visuels et fonctionnels de la carte 3D des missions."""
import pytest

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCENE = (ROOT / "web/static/js/mission_scene.js").read_text(encoding="utf-8")
VIEWS = (ROOT / "web/static/js/mission_views.js").read_text(encoding="utf-8")
PANELS = (ROOT / "web/static/js/panels.js").read_text(encoding="utf-8")


def test_scene_represente_un_systeme_operationnel_et_non_des_primitives_demo():
    for marker in (
        "DodecahedronGeometry", "TorusGeometry", "GridHelper",
        "PolarGridHelper", "CatmullRomCurve3", "TubeGeometry",
        "flowPackets", "LEAD", "CODEAGENT",
    ):
        assert marker in SCENE
    assert "BoxGeometry(0.72" not in SCENE
    assert "data-mp-scene-quality', 'operational'" in SCENE
    assert "Carte 3D opérationnelle" in SCENE


@pytest.mark.skip(reason="Constellation retiree du panneau (lot 17) — la couche 3D reste sur le disque, dormante : ce contrat redeviendra verifiable si la vue revient")
def test_scene_expose_les_etats_reels_et_les_flux_des_workers():
    for field in ("state:", "aggregate:", "events:", "lastTool:", "queueRank:"):
        assert field in VIEWS
    assert "worker.state === 'running'" in SCENE
    assert "worker.queueRank || worker.state === 'waiting'" in SCENE
    assert "mission:worker-select" in SCENE


@pytest.mark.skip(reason="Constellation retiree du panneau (lot 17) — la couche 3D reste sur le disque, dormante : ce contrat redeviendra verifiable si la vue revient")
def test_rendu_est_local_progressif_accessible_et_compatible():
    assert "../vendor/three.module.min.js" in SCENE
    assert "powerPreference: 'low-power'" in SCENE
    assert "prefers-reduced-motion: reduce" in SCENE
    assert "mp-scene-fallback" in VIEWS and "<svg" in VIEWS
    assert "typeof ctx.roundRect === 'function'" in SCENE
    assert "ctx.rect(" in SCENE
    assert "data-mp-canvas-color-pixels" in SCENE


def test_rendu_physique_et_nettoyage_gpu_sont_configures():
    assert "outputColorSpace = THREE.SRGBColorSpace" in SCENE
    assert "toneMapping = THREE.ACESFilmicToneMapping" in SCENE
    assert "m.map.dispose" in SCENE
    assert "renderer.forceContextLoss" in SCENE
    assert "mission_scene.js?v=13" in PANELS


def test_camera_cadre_le_contenu_selon_la_largeur_disponible():
    assert "sceneCenterX = payload.workers.length ? 0.3 : 1.2" in SCENE
    assert "contentWidth = payload.workers.length ? 8.1 : 5.7" in SCENE
    assert "camera.aspect" in SCENE and "byWidth" in SCENE
    assert "zoomOffset" in SCENE and "applyCamera()" in SCENE


def test_composition_responsive_preserve_la_largeur_de_la_carte():
    css = (ROOT / "web/static/css/mission-panel.css").read_text(encoding="utf-8")
    assert "height: clamp(500px, 66vh, 680px)" in css
    assert "@media (max-width: 1400px)" in css
    assert "max-height: 420px" in css


def test_distribution_evite_un_worker_cache_derriere_le_lead():
    assert "var angle = Math.PI + index * Math.PI * 2" in SCENE
    assert "Math.cos(angle) * 0.7" in SCENE
    assert "Math.sin(angle) < -0.25" in SCENE
