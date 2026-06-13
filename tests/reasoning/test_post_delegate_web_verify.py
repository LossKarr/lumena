from pathlib import Path

from src.reasoning.react import (
    ReActLoop,
    _build_post_delegate_continue_query,
    _build_post_delegate_web_verify_failure_query,
    _build_post_delegate_web_verify_success_query,
    _delegate_delivery_expects_canvas,
    _extract_existing_web_project_path,
    _is_post_codeagent_conditional_correction_task,
    _is_post_codeagent_synthesis_task,
    _looks_like_web_delegate_delivery,
    _verify_report_has_preview_server_mime_error,
)
from src.reasoning.react_config import TaskItem


def test_extract_existing_web_project_path_from_delegate_context(tmp_path: Path):
    project = tmp_path / "workspace" / "demo"
    project.mkdir(parents=True)
    (project / "index.html").write_text("<!doctype html>", encoding="utf-8")

    found = _extract_existing_web_project_path(
        {"context": {"workspace_path": str(project)}},
        "CodeAgent done",
        base_dir=tmp_path,
    )

    assert found == project.resolve()


def test_extract_existing_web_project_path_from_create_project_output_dir(tmp_path: Path):
    project = tmp_path / "workspace" / "site-demo"
    project.mkdir(parents=True)
    (project / "index.html").write_text("<!doctype html>", encoding="utf-8")

    found = _extract_existing_web_project_path(
        {"output_dir": str(project).replace("\\", "/")},
        "✅ Projet créé avec succès",
        base_dir=tmp_path,
    )

    assert found == project.resolve()


def test_detects_web_delegate_delivery():
    assert _looks_like_web_delegate_delivery(
        "Crée un jeu 3D en HTML/CSS/JS",
        {"description": "open world three.js", "context": {}},
        "✅ codeAgent terminé (12.0s, 4 itérations)",
    )


def test_detects_canvas_expectation():
    assert _delegate_delivery_expects_canvas(
        "Crée un jeu open world 3D",
        {"description": "Three.js canvas"},
        "",
    )


def test_detects_3d_game_canvas_without_canvas_word():
    assert _delegate_delivery_expects_canvas(
        "Crée un jeu open world 3D complet en HTML/CSS/JS",
        {"description": "exploration 3D avec terrain et joueur"},
        "",
    )


def test_ignores_moodboard_canvas_metaphor():
    assert not _delegate_delivery_expects_canvas(
        "Crée un studio de moodboard avec une zone principale type canvas pour déplacer des cartes",
        {"description": "moodboard avec cartes drag and drop"},
        "",
    )


def test_detects_html_canvas_drawing_expectation():
    assert _delegate_delivery_expects_canvas(
        "Crée un outil de dessin canvas HTML",
        {"description": "canvas 2d drawing"},
        "",
    )


def test_failure_query_forces_redelegation(tmp_path: Path):
    query = _build_post_delegate_web_verify_failure_query(
        "Corrige le site",
        tmp_path,
        "✅ codeAgent terminé (12.0s, 4 itérations)",
        "Errors:\n- console_error: boom",
    )

    assert "delegate_task" in query
    assert "ne finalise pas" in query
    assert "console_error: boom" in query


def test_preview_server_mime_failure_query_does_not_force_redelegation(tmp_path: Path):
    report = (
        "Errors:\n"
        "- preview_server_mime_error: Refused to execute script from 'http://localhost:8080/js/main.js' "
        "because its MIME type ('application/json') is not executable, and strict MIME type checking is enabled."
    )

    query = _build_post_delegate_web_verify_failure_query(
        "Cree le site",
        tmp_path,
        "codeAgent termine (12.0s, 4 iterations)",
        report,
    )

    assert _verify_report_has_preview_server_mime_error(report)
    assert "browser_verify_local_project" in query
    assert "reecrire les fichiers JS" in query
    assert "Appelle maintenant `delegate_task`" not in query


def test_success_query_allows_final():
    query = _build_post_delegate_web_verify_success_query(
        "Crée le site",
        "✅ codeAgent terminé (12.0s, 4 itérations)",
        "Runtime web verify: OK",
    )

    assert "ACTION: FINAL" in query
    assert "Vérification navigateur autonome après CodeAgent : OK" in query


def test_web_runtime_verify_marks_browser_runtime_plan_steps():
    loop = ReActLoop(llm_chat_func=lambda *_args, **_kwargs: "")
    loop._task_plan = [
        TaskItem(description="Étape 1: Créer le projet avec create_project", completed=True),
        TaskItem(description="Étape 2: Vérifier que tous les fichiers existent", completed=True),
        TaskItem(description="Étape 3: Lancer un serveur local et tester dans le navigateur"),
        TaskItem(description="Étape 4: Vérifier la console, les interactions, le localStorage"),
        TaskItem(description="Étape 5: Résumé final"),
    ]

    marked = loop._mark_web_runtime_plan_verified(iteration=3)

    assert marked == 2
    assert loop._task_plan[2].completed is True
    assert loop._task_plan[2].completed_by_tool == "browser_verify_local_project"
    assert loop._task_plan[3].completed is True
    assert loop._task_plan[3].completion_confidence == "strong"
    assert loop._task_plan[4].completed is False


def test_web_runtime_verify_marks_conditional_fix_but_keeps_business_followups():
    loop = ReActLoop(llm_chat_func=lambda *_args, **_kwargs: "")
    loop._task_plan = [
        TaskItem(description="Creer le site avec CodeAgent", completed=True),
        TaskItem(description="Tester dans le navigateur avec Playwright"),
        TaskItem(description="Corriger si necessaire"),
        TaskItem(description="Generer le PDF de presentation"),
        TaskItem(description="Envoyer le PDF par email"),
        TaskItem(description="Donner le resume final"),
    ]

    marked = loop._mark_web_runtime_plan_verified(iteration=4)
    pending = [task.description for task in loop._pending_delegate_success_business_tasks()]

    assert marked == 2
    assert loop._task_plan[1].completed is True
    assert loop._task_plan[2].completed is True
    assert loop._task_plan[2].completion_status == "not_applicable"
    assert loop._task_plan[3].completed is False
    assert loop._task_plan[4].completed is False
    assert pending == [
        "Generer le PDF de presentation",
        "Envoyer le PDF par email",
    ]


def test_post_codeagent_synthesis_does_not_swallow_email_or_pdf_tasks():
    assert _is_post_codeagent_synthesis_task("Donner le resume final")
    assert _is_post_codeagent_conditional_correction_task("Corriger si necessaire")
    assert not _is_post_codeagent_synthesis_task("Envoyer le resume final par email")
    assert not _is_post_codeagent_synthesis_task("Generer un PDF puis donner le resume")


def test_continue_query_does_not_force_final_when_business_tasks_remain():
    query = _build_post_delegate_continue_query(
        "Cree un site puis envoie le par email",
        "CodeAgent done",
        ["Envoyer le site par email"],
        "Runtime web verify: OK",
    )

    assert "Ne finalise pas encore" in query
    assert "ACTION: FINAL" not in query
    assert "Envoyer le site par email" in query
