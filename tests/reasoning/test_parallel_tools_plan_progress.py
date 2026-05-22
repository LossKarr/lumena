"""Fix parallel_tools → plan tracker (2026-05-21).

Vérifie que la propagation des sous-outils parallel_tools coche les tâches
PROUVÉES (tool+args+observation) avec allow_fallback=False — sans completion
fantôme ni cascade.

Garde-fous testés :
- GF-1 : allow_fallback=False → seul le matching prouvé peut cocher (pas de
  fallback séquentiel ni auto-advance).
- GF-2 : N sous-outils → au max N completions, chacune prouvée.
"""

from __future__ import annotations

import pytest

from src.reasoning.react import ReActLoop, TaskItem


def _loop_with_plan(tasks: list[str]) -> ReActLoop:
    loop = ReActLoop(llm_chat_func=None)
    loop._task_plan = [TaskItem(description=t) for t in tasks]
    loop._last_auto_advance_iter = -1
    return loop


# ─── POSITIF : 2 sous-outils prouvés cochent leurs 2 étapes ─────────────


def test_parallel_subtools_complete_their_proven_steps():
    """parallel_tools(data_profile_file ×2) → les 2 étapes profile cochées."""
    loop = _loop_with_plan([
        "Profiler communes_a.csv",
        "Profiler communes_b.csv",
        "Présenter le rapport à l'utilisateur",
    ])
    # Simule la propagation faite par le call site (allow_fallback=False)
    loop._update_plan_progress(
        "data_profile_file",
        {"path": "C:/ws/downloads/datagouv/communes_a.csv"},
        "✅ Profil de communes_a.csv\nLignes : 10 | Colonnes : 5",
        1,
        allow_fallback=False,
    )
    loop._update_plan_progress(
        "data_profile_file",
        {"path": "C:/ws/downloads/datagouv/communes_b.csv"},
        "✅ Profil de communes_b.csv\nLignes : 20 | Colonnes : 6",
        1,
        allow_fallback=False,
    )
    assert loop._task_plan[0].completed, "Étape A doit être cochée par profile A"
    assert loop._task_plan[1].completed, "Étape B doit être cochée par profile B"
    # L'étape de présentation reste à FINAL (non couverte par les outils)
    assert not loop._task_plan[2].completed, "L'étape rapport ne doit PAS être cochée par un profile"


def test_parallel_subtool_completes_only_matching_step():
    """Un sous-outil ne coche QUE l'étape qu'il prouve, pas une autre."""
    loop = _loop_with_plan([
        "Profiler communes_a.csv",
        "Profiler communes_b.csv",
    ])
    # Seul le sous-outil A est propagé
    loop._update_plan_progress(
        "data_profile_file",
        {"path": "C:/ws/communes_a.csv"},
        "✅ Profil de communes_a.csv\nLignes : 10",
        1,
        allow_fallback=False,
    )
    assert loop._task_plan[0].completed, "A cochée"
    assert not loop._task_plan[1].completed, "B NE doit PAS être cochée par le profile de A"


# ─── NÉGATIF : sous-outil échoué non propagé ────────────────────────────


def test_failed_subtool_not_propagated_at_call_site_logic():
    """Le call site filtre sub.success ; ici on vérifie qu'un profile sans
    correspondance d'arg ne coche rien avec allow_fallback=False."""
    loop = _loop_with_plan([
        "Profiler communes_a.csv",
    ])
    # path ne matche pas la tâche (autre fichier) + pas de hint data_profile_file
    loop._update_plan_progress(
        "data_profile_file",
        {"path": "C:/ws/AUTRE_FICHIER.csv"},
        "✅ Profil de autre_fichier.csv\nLignes : 3",
        1,
        allow_fallback=False,
    )
    assert not loop._task_plan[0].completed, (
        "Sans arg match ET sans fallback, aucune étape ne doit être cochée"
    )


# ─── ANTI-CASCADE : 2 sous-outils, 4 étapes → max 2 cochées ─────────────


def test_no_cascade_only_proven_steps_completed():
    """2 sous-outils prouvés, 4 étapes au plan → seules les 2 prouvées cochées."""
    loop = _loop_with_plan([
        "Profiler communes_a.csv",
        "Profiler communes_b.csv",
        "Profiler communes_c.csv",  # aucun sous-outil pour celle-ci
        "Profiler communes_d.csv",  # aucun sous-outil pour celle-ci
    ])
    loop._update_plan_progress(
        "data_profile_file", {"path": "C:/ws/communes_a.csv"},
        "✅ Profil communes_a.csv\nLignes : 10", 1, allow_fallback=False,
    )
    loop._update_plan_progress(
        "data_profile_file", {"path": "C:/ws/communes_b.csv"},
        "✅ Profil communes_b.csv\nLignes : 20", 1, allow_fallback=False,
    )
    completed = [t.description for t in loop._task_plan if t.completed]
    assert len(completed) == 2, f"Exactement 2 étapes cochées, trouvé : {completed}"
    assert not loop._task_plan[2].completed
    assert not loop._task_plan[3].completed


# ─── GF-1 : allow_fallback=False désactive bien les fallbacks ───────────


def test_allow_fallback_false_blocks_sequential_and_auto_advance():
    """Avec allow_fallback=False, un outil sans matching prouvé ne coche RIEN
    (ni séquentiel, ni auto-advance), même sur une tâche générique."""
    loop = _loop_with_plan([
        "Faire quelque chose de générique",
    ])
    # get_time : trivial, aucun hint/arg/obs match avec la tâche
    loop._update_plan_progress(
        "get_time", {}, "✅ 2026-05-21 14:00:00", 1, allow_fallback=False,
    )
    assert not loop._task_plan[0].completed, (
        "Sans matching prouvé et fallback désactivé → aucune completion"
    )


def test_allow_fallback_true_still_works_for_normal_tools():
    """Rétro-compat : avec allow_fallback=True (défaut), le comportement
    historique d'auto-advance reste actif pour un outil non-parallel."""
    loop = _loop_with_plan([
        "Exécuter le script de build",
    ])
    # run_command a un hint "execut/run/lanc" → hint_match sur "Exécuter"
    loop._update_plan_progress(
        "run_command",
        {"command": "python build.py"},
        "✅ Build terminé avec succès",
        1,
        # allow_fallback par défaut = True
    )
    assert loop._task_plan[0].completed, (
        "run_command doit cocher 'Exécuter le script' (hint match, comportement historique)"
    )


# ─── BONUS call-site : vraie Observation + vrais SubToolResult ──────────


def _propagate_parallel(loop: ReActLoop, observation, iteration: int) -> None:
    """Réplique EXACTE de la logique du call-site (_run_internal ~6537) pour
    valider le contrat de données Observation.sub_results / SubToolResult.

    Si cette fonction diverge du call-site react.py, le test perd sa valeur :
    garder synchronisé.
    """
    if loop._task_plan and observation.success:
        _subs = getattr(observation, "sub_results", ()) or ()
        for _sub in _subs:
            if not getattr(_sub, "success", False):
                continue
            _sub_name = getattr(_sub, "tool_name", "") or ""
            if not _sub_name:
                continue
            loop._update_plan_progress(
                _sub_name,
                getattr(_sub, "args", {}) or {},
                getattr(_sub, "content", "") or "",
                iteration,
                allow_fallback=False,
            )


def test_callsite_with_real_observation_sub_results():
    """Vraie Observation portant 2 SubToolResult réussis → 2 étapes cochées."""
    from src.reasoning.react_config import Observation
    from src.reasoning.handlers.contracts import SubToolResult

    loop = _loop_with_plan([
        "Profiler communes_a.csv",
        "Profiler communes_b.csv",
        "Présenter le rapport",
    ])
    obs = Observation(
        content="parallel_tools: 2 sous-outils exécutés",
        success=True,
        sub_results=(
            SubToolResult(
                tool_name="data_profile_file",
                success=True,
                content="✅ Profil de communes_a.csv\nLignes : 10",
                args={"path": "C:/ws/communes_a.csv"},
            ),
            SubToolResult(
                tool_name="data_profile_file",
                success=True,
                content="✅ Profil de communes_b.csv\nLignes : 20",
                args={"path": "C:/ws/communes_b.csv"},
            ),
        ),
    )
    _propagate_parallel(loop, obs, iteration=1)
    assert loop._task_plan[0].completed
    assert loop._task_plan[1].completed
    assert not loop._task_plan[2].completed  # rapport → FINAL


def test_callsite_skips_failed_sub_results():
    """Un SubToolResult avec success=False ne doit PAS cocher sa tâche."""
    from src.reasoning.react_config import Observation
    from src.reasoning.handlers.contracts import SubToolResult

    loop = _loop_with_plan([
        "Profiler communes_a.csv",
        "Profiler communes_b.csv",
    ])
    obs = Observation(
        content="parallel_tools: 1 ok, 1 échec",
        success=True,
        sub_results=(
            SubToolResult(
                tool_name="data_profile_file",
                success=True,
                content="✅ Profil de communes_a.csv\nLignes : 10",
                args={"path": "C:/ws/communes_a.csv"},
            ),
            SubToolResult(
                tool_name="data_profile_file",
                success=False,  # échec → non propagé
                content="❌ communes_b.csv introuvable",
                args={"path": "C:/ws/communes_b.csv"},
            ),
        ),
    )
    _propagate_parallel(loop, obs, iteration=1)
    assert loop._task_plan[0].completed, "A réussi → coché"
    assert not loop._task_plan[1].completed, "B échoué → NON coché"


def test_callsite_empty_sub_results_no_crash():
    """Observation parallel_tools sans sub_results → ne plante pas, ne coche rien."""
    from src.reasoning.react_config import Observation

    loop = _loop_with_plan(["Profiler communes_a.csv"])
    obs = Observation(content="parallel_tools vide", success=True, sub_results=())
    _propagate_parallel(loop, obs, iteration=1)
    assert not loop._task_plan[0].completed
