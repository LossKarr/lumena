from src.training.personal.routing import PersonalModelRouter


def test_principal_remains_default_and_automatic_falls_back() -> None:
    router = PersonalModelRouter()
    principal = router.choose(mode="principal", principal_model="deepseek-flash", personal_model="lumena-model-1.0.0", personal_available=True)
    assert principal.selected_model == "deepseek-flash"
    automatic = router.choose(mode="automatic", principal_model="deepseek-flash", personal_model="lumena-model-1.0.0", personal_available=True, capability_scores={"code": 0.4}, required_capability="code")
    assert automatic.selected_model == "deepseek-flash"
    assert automatic.fallback_model == ""


def test_automatic_personal_route_keeps_one_bounded_fallback() -> None:
    decision = PersonalModelRouter().choose(mode="automatic", principal_model="deepseek-flash", personal_model="lumena-model-1.0.0", personal_available=True, capability_scores={"general": 0.91})
    assert decision.selected_model == "lumena-model-1.0.0"
    assert decision.fallback_model == "deepseek-flash"
