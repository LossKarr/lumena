from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PANELS = (ROOT / "web/static/js/panels.js").read_text(encoding="utf-8")
CODEX = (ROOT / "web/static/js/codex-subscription.js").read_text(encoding="utf-8")
INDEX = (ROOT / "web/index.html").read_text(encoding="utf-8")
CSS = (ROOT / "web/static/css/config-panel.css").read_text(encoding="utf-8")
CONFIG = (ROOT / "web/routes/config.py").read_text(encoding="utf-8")


def test_config_panel_sends_only_tracked_changes_and_skips_disabled_fields():
    assert "Object.fromEntries(_cfgDrafts.entries())" in PANELS
    assert "if(!el?.dataset?.cfg||el.disabled)return" in PANELS
    assert "document.querySelectorAll('[data-cfg]')" not in PANELS[PANELS.index("export async function saveConfig"):]


def test_config_drafts_survive_group_navigation_and_reload_after_success():
    assert "const _cfgDrafts=new Map()" in PANELS
    assert "_captureRenderedConfig()" in PANELS
    assert "await loadConfig({force:true})" in PANELS
    assert "Abandonner les modifications non sauvegardées" in PANELS
    assert "preserveDrafts" in PANELS
    assert "if(!preserveDrafts)_cfgDrafts.clear()" in PANELS


def test_secret_mask_is_never_submitted_as_a_change():
    assert "el.dataset.secret==='1'&&el.readOnly&&!el.dataset.dirty" in PANELS
    assert "el.dataset.original" in PANELS
    assert "el.value=_cfgBaseline.get(el.dataset.cfg)" in PANELS


def test_errors_and_double_submit_are_handled_explicitly():
    assert "async function _cfgResponsePayload" in PANELS
    assert "if(!response.ok)throw new Error" in PANELS
    assert "_cfgDrafts.has(el.dataset.cfg)&&!el.disabled" in PANELS
    assert "if(_cfgSaving)return" in PANELS
    assert "button.disabled=saving" in PANELS


def test_config_panel_exposes_decimal_step_hints_and_restart_state():
    assert "it.step||'1'" in PANELS
    assert "cfg-field-hint" in PANELS
    assert "cfg-restart-badge" in PANELS
    assert ".cfg-field-row.dirty" in CSS
    assert ".cfg-nav-item.dirty" in CSS
    assert "Les changements compatibles sont appliqués immédiatement" in INDEX
    assert 'role="status" aria-live="polite"' in INDEX


def test_number_renderer_omits_max_when_schema_has_no_ceiling():
    assert "it.max!==undefined" in PANELS
    for key in ("LUMENA_MAX_REACT_ITERATIONS", "LUMENA_CODE_AGENT_MAX_ITER"):
        start = CONFIG.index(f'{{"key": "{key}"')
        entry = CONFIG[start:CONFIG.index("},", start) + 2]
        assert '"max"' not in entry


def test_codex_unavailable_catalog_preserves_configured_model():
    assert "configuredOption" in CODEX
    assert "(configure)" in CODEX
    assert "_emitConfigChange" in CODEX
    assert "_selectMode(mode,false)" in CODEX
    assert "_setSurface('missions',configuredSurfaces.includes('missions'),false)" in CODEX
