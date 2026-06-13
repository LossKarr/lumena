from src.tools.code_validator import validate_project


def _codes(report):
    return {issue.code for issue in report.issues}


def test_shader_template_calls_are_not_js_functions():
    report = validate_project({
        "index.html": "<script src='js/engine.js'></script>",
        "js/engine.js": """
const material = {
  fragmentShader: `
    void main() {
      gl_FragColor = vec4(max(pow(max(0.5, 0.0), 2.0), 0.0));
    }
  `
};
function initEngine() {}
initEngine();
""",
    })

    assert not any(
        issue.code == "JS_UNDEFINED_FUNCTION" and "max()" in issue.message
        for issue in report.issues
    )


def test_css_var_with_fallback_is_valid():
    report = validate_project({
        "index.html": "<link rel='stylesheet' href='style.css'>",
        "style.css": ".project-image { background-image: var(--img, none); }",
    })

    assert "CSS_UNDEFINED_VAR" not in _codes(report)


def test_dynamic_dom_ids_satisfy_get_element_by_id():
    report = validate_project({
        "index.html": "<script src='app.js'></script><main class='main-panel'></main>",
        "app.js": """
const panel = document.createElement('section');
panel.id = 'missions-panel';
document.querySelector('.main-panel').appendChild(panel);
const existing = document.getElementById('missions-panel');
""",
    })

    assert not any(
        issue.code == "XREF_JS_MISSING_ID" and "missions-panel" in issue.message
        for issue in report.issues
    )


def test_typeof_function_guard_allows_callable_variable():
    report = validate_project({
        "index.html": "<script src='render.js'></script>",
        "render.js": """
let unsubscribe = null;
function init() {
  unsubscribe = State.subscribe(() => {});
}
function destroy() {
  if (typeof unsubscribe === 'function') {
    unsubscribe();
  }
}
""",
    })

    assert not any(
        issue.code == "JS_UNDEFINED_FUNCTION" and "unsubscribe()" in issue.message
        for issue in report.issues
    )


def test_missing_html_assets_still_fail():
    report = validate_project({
        "index.html": """
<link rel='stylesheet' href='style.css'>
<script src='world_3d.js'></script>
""",
    })

    codes = _codes(report)
    assert "HTML_MISSING_STYLE" in codes
    assert "HTML_MISSING_SCRIPT" in codes
