from src.reasoning.react import _browser_evaluate_proves_interaction


def test_lumagrid_click_and_state_read_proves_interaction():
    script = (
        "const cell = document.querySelector('[data-row=\"2\"][data-col=\"2\"]'); "
        "cell.click(); return {clicked: '2,2', states: {'2,2': false}, "
        "activeCount: 20, counter: 'Coups : 1'};"
    )
    observation = (
        "\u2705 JS execute\n"
        "\u2192 {'clicked': '2,2', 'states': {'2,2': False}, "
        "'activeCount': 20, 'counter': 'Coups : 1'}"
    )

    assert _browser_evaluate_proves_interaction(script, observation) is True


def test_lumagrid_reset_and_counter_read_proves_interaction():
    script = (
        "const reset = [...document.querySelectorAll('button')]"
        ".find(b => b.textContent.includes('Reinitialiser')); reset.click(); "
        "return {clickedReset: true, counterText: 'Coups : 0', activeCount: 25};"
    )
    observation = (
        "\u2705 JS execute\n"
        "\u2192 {'clickedReset': True, 'counterText': 'Coups : 0', 'activeCount': 25}"
    )

    assert _browser_evaluate_proves_interaction(script, observation) is True


def test_static_initial_state_read_is_not_interaction_proof():
    script = (
        "return {activeCount: document.querySelectorAll('.on').length, "
        "counter: document.querySelector('#counter').textContent};"
    )
    observation = "\u2705 JS execute\n\u2192 {'activeCount': 25, 'counter': 'Coups : 0'}"

    assert _browser_evaluate_proves_interaction(script, observation) is False


def test_click_without_observed_dynamic_state_is_not_proof():
    script = "document.querySelector('button').click(); return {clicked: true};"
    observation = "\u2705 JS execute\n\u2192 {'clicked': True}"

    assert _browser_evaluate_proves_interaction(script, observation) is False


def test_javascript_error_is_not_interaction_proof():
    script = "document.querySelector('.missing').click(); return {counter: 1};"
    observation = "TypeError: Cannot read properties of null"

    assert _browser_evaluate_proves_interaction(script, observation) is False


def test_clickrush_prefixed_score_keys_are_dynamic_interaction_proof():
    script = (
        "const target = document.querySelector('#target'); "
        "const scoreInitial = Number(document.querySelector('#score').textContent); "
        "target.click(); target.click(); target.click(); "
        "const scoreApres3Clics = Number(document.querySelector('#score').textContent); "
        "document.querySelector('#reset').click(); "
        "return {scoreInitial, scoreApres3Clics, "
        "scoreApresReset: Number(document.querySelector('#score').textContent)};"
    )
    observation = (
        "\u2705 JS execute\n\u2192 "
        '{"scoreInitial":0,"scoreApres3Clics":3,"scoreApresReset":0}'
    )

    assert _browser_evaluate_proves_interaction(script, observation) is True
