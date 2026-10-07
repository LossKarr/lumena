from src.training.personal.policy import PersonalLearningPolicy


def test_learning_and_cloud_consent_are_separate() -> None:
    disabled = PersonalLearningPolicy()
    assert disabled.capture_decision(source_surface="web").reason_code == "personal_learning_disabled"
    local = PersonalLearningPolicy(learning_enabled=True, local_capture_enabled=True)
    assert local.capture_decision(source_surface="web").allowed is True
    assert local.cloud_judge_decision().allowed is False


def test_exclusions_and_internal_activity_are_fail_closed() -> None:
    policy = PersonalLearningPolicy(
        learning_enabled=True,
        local_capture_enabled=True,
        excluded_projects=frozenset({"private"}),
    )
    assert policy.capture_decision(source_surface="web", project_id="private").allowed is False
    assert policy.capture_decision(source_surface="web", internal=True).reason_code == "internal_activity_excluded"
    assert policy.capture_decision(source_surface="unknown").reason_code == "surface_excluded"
