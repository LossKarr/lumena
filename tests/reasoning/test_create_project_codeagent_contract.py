import inspect

from src.reasoning.handlers import project


def test_create_project_codeagent_prompt_uses_workspace_root_contract():
    src = inspect.getsource(project.create_project_handler)

    assert "ton workspace actif EST DÉJÀ le dossier de sortie" in src
    assert "Écris les fichiers à la racine" in src
    assert "N'utilise jamais le préfixe" in src
    assert "ReAct/Playwright fera la vérification navigateur" in src
