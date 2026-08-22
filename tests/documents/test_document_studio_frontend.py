from pathlib import Path


ROOT = Path(__file__).parents[2]


def test_frontend_uses_the_global_admin_token_binding():
    source = (ROOT / "web" / "static" / "js" / "document-studio.js").read_text(encoding="utf-8")

    assert "typeof ADMIN_TOKEN!=='undefined'" in source
    assert "const token=currentAdminToken()" in source
    assert "if(window.ADMIN_TOKEN)h.Authorization" not in source


def test_visual_runtime_exercises_real_bearer_auth():
    source = (ROOT / "tests" / "documents" / "visual_runtime.py").read_text(encoding="utf-8")

    assert 'LUMENA_ADMIN_TOKEN"] = "document-studio-visual-token"' in source
    assert "dependency_overrides" not in source


def test_frontend_cache_versions_load_the_fixed_module():
    main = (ROOT / "web" / "static" / "js" / "main.js").read_text(encoding="utf-8")
    index = (ROOT / "web" / "index.html").read_text(encoding="utf-8")

    assert "./document-studio.js?v=14" in main
    assert 'href="/static/css/document-studio.css?v=9"' in index
    assert 'src="/static/js/main.js?v=29"' in index


def test_document_studio_style_uses_lumena_design_tokens():
    source = (ROOT / "web" / "static" / "css" / "document-studio.css").read_text(
        encoding="utf-8"
    )

    assert ".ds-shell{overflow:hidden;border:1px solid var(--border)" in source
    assert "border-radius:var(--radius-lg)" in source
    assert ".ds-tab.active{color:var(--accent)}" in source
    assert ".ds-model-card:hover,.ds-doc-item:hover{border-color:var(--border-strong)" in source


def test_document_studio_exposes_logo_library_and_visual_editor():
    index = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    source = (ROOT / "web" / "static" / "js" / "document-studio.js").read_text(encoding="utf-8")

    assert 'data-ds-view="logos"' in index
    assert 'id="ds-logo-grid"' in index
    assert 'data-ds-editor-mode="visual"' in index
    assert 'data-ds-editor-mode="advanced"' in index
    assert 'id="ds-visual-fields"' in index
    assert "function renderVisualFields()" in source
    assert "function handleDesignInput(event)" in source
    assert "function uploadLogo(event)" in source


def test_visual_editor_exposes_per_template_free_logo_positioning():
    source = (ROOT / "web" / "static" / "js" / "document-studio.js").read_text(encoding="utf-8")
    styles = (ROOT / "web" / "static" / "css" / "document-studio.css").read_text(encoding="utf-8")

    assert "function ensureLogoPositionControls()" in source
    assert "logo_layout:'flow'" in source
    assert "logo_x_pct:0" in source
    assert "logo_y_mm:0" in source
    assert "function setLogoPositionFromPointer(event)" in source
    assert ".ds-logo-position-pad" in styles
    assert ".ds-editor [hidden]{display:none!important}" in styles


def test_builtin_templates_allow_draft_design_before_clone():
    source = (ROOT / "web" / "static" / "js" / "document-studio.js").read_text(encoding="utf-8")

    assert "function handleDesignInput(event){\n  if(!activeTemplate)return;" in source
    assert "activeTemplate.read_only||(visualDesign.logo_layout" not in source
    assert "Modèle dupliqué avec vos réglages" in source


def test_generated_documents_have_a_visual_version_editor():
    source = (ROOT / "web" / "static" / "js" / "document-studio.js").read_text(encoding="utf-8")
    styles = (ROOT / "web" / "static" / "css" / "document-studio.css").read_text(encoding="utf-8")

    assert "d.metadata?.studio_generation" in source
    assert "function bindGeneratedRevisionEditor" in source
    assert "/revise/preview" in source
    assert "replace_data:true" in source
    assert "data-doc-apply-revision" in source
    assert ".ds-revision-fields" in styles
    assert ".ds-revision-preview" in styles


def test_logo_controls_use_direct_segmented_buttons_instead_of_native_selects():
    source = (ROOT / "web" / "static" / "js" / "document-studio.js").read_text(encoding="utf-8")
    styles = (ROOT / "web" / "static" / "css" / "document-studio.css").read_text(encoding="utf-8")

    assert "function ensureLogoChoiceButtons()" in source
    assert "data-ds-logo-choice" in source
    assert "handleDesignInput({type:'change',target:select})" in source
    assert "function syncLogoChoiceButtons()" in source
    assert "nativeSelect.closest('.dark-select')" in source
    assert "controlRoot.replaceWith(select)" in source
    assert "select.type='hidden'" in source
    assert "enhanceLogoChoice('ds-design-font','Typographie'" in source
    assert "enhanceLogoChoice('ds-design-density','Densité'" in source
    assert ".ds-choice-native" in styles
    assert ".ds-choice-button.active" in styles


def test_custom_templates_have_a_dedicated_view():
    index = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    source = (ROOT / "web" / "static" / "js" / "document-studio.js").read_text(encoding="utf-8")

    assert 'data-ds-view="custom"' in index
    assert 'id="ds-custom-grid"' in index
    assert 'id="ds-custom-count"' in index
    assert "const integrated=models.filter(m=>m.read_only" in source
    assert "const custom=models.filter(m=>!m.read_only)" in source
    assert "['ds-model-grid','ds-custom-grid']" in source


def test_custom_template_import_has_a_review_and_publish_wizard():
    index = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    source = (ROOT / "web" / "static" / "js" / "document-studio.js").read_text(encoding="utf-8")
    styles = (ROOT / "web" / "static" / "css" / "document-studio.css").read_text(encoding="utf-8")

    assert 'id="ds-template-import-open"' in index
    assert 'id="ds-template-import-wizard"' in index
    assert 'id="ds-template-import-fields"' in index
    assert 'id="ds-template-import-preview"' in index
    assert "async function createTemplateDraft" in source
    assert "async function persistTemplateDraft" in source
    assert "async function publishTemplateDraft" in source
    assert "/template-imports/${encodeURIComponent(templateDraft.id)}/preview" in source
    assert ".ds-import-wizard.open" in styles


def test_native_select_changes_update_visual_design_in_electron():
    source = (ROOT / "web" / "static" / "js" / "document-studio.js").read_text(encoding="utf-8")

    assert "control.addEventListener('input',handleDesignInput)" in source
    assert "control.addEventListener('change',handleDesignInput)" in source
    assert "event?.type==='change'" in source
    assert "clearTimeout(previewTimer);renderDraftPreview()" in source
    assert "function designSummary()" in source
    assert "À jour · ${designSummary()}" in source
