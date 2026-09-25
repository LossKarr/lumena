from pathlib import Path

from src.skills.loader import SkillLoader


ROOT = Path(__file__).resolve().parents[2]
EXPECTED_TOOLS = {
    "search_local_models",
    "inspect_local_model",
    "list_installed_local_models",
    "recommend_local_model",
    "list_local_model_jobs",
    "get_local_model_job",
    "install_local_model",
    "enable_local_model",
    "disable_local_model",
    "select_local_model",
    "unload_local_model",
    "verify_local_model",
    "prepare_delete_local_model",
    "confirm_delete_local_model",
}


def _loader() -> SkillLoader:
    loader = SkillLoader(base_dirs=[ROOT / "skills"])
    loader.load_all()
    return loader


def test_local_model_manager_skill_loads_with_every_conversational_tool():
    skill = _loader().get_skill("local-model-manager")

    assert skill is not None
    assert set(skill.apply_to) == EXPECTED_TOOLS


def test_local_model_manager_skill_matches_ollama_and_hugging_face_requests():
    loader = _loader()

    ollama = loader.match_skills("installe le modèle qwen dans Ollama", max_results=3)
    hugging_face = loader.match_skills("cherche un modèle Hugging Face GGUF pour coder", max_results=3)

    assert ollama and ollama[0].name == "local-model-manager"
    assert hugging_face and hugging_face[0].name == "local-model-manager"


def test_local_model_manager_context_preserves_proof_and_delete_guards():
    context = _loader().build_active_skills_context(
        query="supprime ce modèle local Ollama",
        max_results=1,
        max_chars=20_000,
    )

    assert "`local-model-manager`" in context
    assert "accepted" in context
    assert "nouveau message humain" in context
    assert "confirm_delete_local_model" in context
    assert "Ne fabrique jamais la confirmation" in context
