r"""LOT A2 — l'état task-scoped du CodeAgent ne fuit plus d'une tâche à la suivante.

--- Ce que l'inventaire A1 a mesuré (2026-10-08) ---

`src/agents/sub_agent.py` porte **70** attributs `self.*`. 38 sont des candidats
task-scoped. La quasi-totalité est déjà réarmée dans `_single_code_attempt` — donc
**par tentative**, ce qui est plus strict qu'un réarmement par tâche.

**Trois seulement fuient réellement**, et ce sont ceux de ce lot :

| attribut | écrit | pourquoi ça fuit |
|---|---|---|
| `_z36_refusals` | l.3927 `= _z36_used + 1` | incrémenté, **jamais remis à zéro** |
| `_last_reflexion_ts` | l.2821 `= now` | l'anti-spam de 60 s **traverse les tâches** |
| `_out_of_scope_created` | l.4429 | **accumulé** : `set(getattr(...)) | set(nouveaux)` |

--- Pourquoi ça compte, même SANS parallélisme ---

Le CodeAgent est un **singleton partagé** par tous les workers de mission et par le
chat. Ces trois états survivent donc d'une tâche à l'autre AUJOURD'HUI, en séquentiel :

- un worker « use » le quota de refus Z36 d'un autre ;
- une tâche B démarrée 30 s après une réflexion de la tâche A ne peut pas en générer
  (contribue au constat du 07/10 : `reflexions.jsonl` figé depuis le 17 août) ;
- une tâche B qui ne crée rien hors périmètre hérite de la liste de la tâche A.

C'est le motif exact des correctifs 2.10, 2.11.A et Z40c — chacun avait réparé **un**
attribut après constat en production. Ce lot traite la classe entière.

--- Les valeurs de réarmement ne sont pas choisies au hasard ---

Chacune est **le défaut déjà utilisé par le `getattr` qui lit l'attribut** :

    l.3923   int(getattr(self, "_z36_refusals", 0) or 0)        → 0
    l.2818   getattr(self, "_last_reflexion_ts", 0.0)           → 0.0
    l.4430   set(getattr(self, "_out_of_scope_created", []) …)  → []

Réarmer à ces valeurs est donc, par construction, indiscernable d'un agent neuf.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from src.agents.sub_agent import AgentType, SubAgent


def _agent() -> SubAgent:
    return SubAgent(agent_type=AgentType.CODE, name="CodeAgent")


# ── 1. Les trois fuites mesurées en A1 ────────────────────────────────────────


class TestLesTroisFuitesSontFermees:
    def test_z36_refusals_ne_survit_pas_a_la_tache(self):
        """Un worker ne doit pas consommer le quota de refus Z36 d'un autre."""
        a = _agent()
        a._z36_refusals = 3
        a._reset_task_scoped_state()
        assert int(getattr(a, "_z36_refusals", 0) or 0) == 0

    def test_last_reflexion_ts_ne_survit_pas_a_la_tache(self):
        """L'anti-spam de 60 s est par TACHE, pas par instance : sinon une tâche
        qui démarre juste après une réflexion ne peut pas en générer."""
        import time

        a = _agent()
        a._last_reflexion_ts = time.time()
        a._reset_task_scoped_state()
        assert float(getattr(a, "_last_reflexion_ts", 0.0) or 0.0) == 0.0

    def test_out_of_scope_created_ne_survit_pas_a_la_tache(self):
        """Il est ACCUMULE (`set(ancien) | set(nouveau)`) : sans reset, une tâche
        hérite des fichiers hors périmètre de la précédente."""
        a = _agent()
        a._out_of_scope_created = ["/tmp/intrus.py", "/tmp/autre.py"]
        a._reset_task_scoped_state()
        assert list(getattr(a, "_out_of_scope_created", []) or []) == []


# ── 2. Ce que le lot ne doit PAS changer (caractérisation) ───────────────────


class TestLesAcquisDe210Et211SontIntacts:
    def test_perimetre_et_workspace_restent_reinitialises(self):
        """LOT 2.10 — l'acquis d'origine ne bouge pas."""
        a = _agent()
        a._allowed_files = frozenset({"cli.py"})
        a._task_workspace_root = Path(tempfile.mkdtemp())
        a._reset_task_scoped_state()
        assert a._allowed_files is None
        assert a._task_workspace_root is None

    def test_gate_indetermine_reste_efface(self):
        """Z40c (2026-08-29) — la bannière d'une gate expirée ne colle plus aux
        résumés suivants."""
        a = _agent()
        a._gate_indetermine = "la gate n'a pas pu valider"
        a._reset_task_scoped_state()
        assert a._gate_indetermine == ""

    def test_rapport_de_fin_reste_task_scoped(self):
        """LOT 2.11.A — `edits_done` / `errors_seen` ne mentent plus sur ce qui a
        été touché."""
        a = _agent()
        mem = getattr(a, "_session_memory", None)
        if not isinstance(mem, dict):
            return  # SubAgent de base : pas de mémoire de session
        mem["edits_done"] = ["app.py"]
        mem["errors_seen"] = ["boom"]
        a._reset_task_scoped_state()
        assert a._session_memory["edits_done"] == []
        assert a._session_memory["errors_seen"] == []

    def test_reset_reste_idempotent_sur_agent_neuf(self):
        """Aucun effet de bord : deux resets d'affilée sur un agent neuf."""
        a = _agent()
        a._reset_task_scoped_state()
        a._reset_task_scoped_state()
        assert a._allowed_files is None
        assert int(getattr(a, "_z36_refusals", 0) or 0) == 0


# ── 3. Gel d'exhaustivité (modèle CONN-7a) ───────────────────────────────────

# Les 38 candidats task-scoped relevés par l'inventaire A1 du 2026-10-08
# (`plans/A1_INVENTAIRE_ETAT_2026-10-08.md`). Un 39e attribut fait TOMBER ce test,
# pour forcer l'examen au lieu de le laisser arriver en silence.
_CANDIDATS_A1 = {
    # Les 3 déjà réarmés avant ce lot (2.10 / Z40c) : ils sont écrits AILLEURS que
    # dans le reset, donc ils font partie de l'inventaire au même titre.
    "_allowed_files", "_gate_indetermine", "_task_workspace_root",
    "_applied_reflexion_ids", "_applied_success_ids", "_architect_plan_cursor",
    "_architect_plan_items", "_architect_plan_last_state", "_attempt_profile",
    "_auto_eval_triggered", "_cmd_fail_count", "_codex_post_success_tasks",
    "_current_iter", "_derniere_redirection_chemin", "_done_soft_gate_used",
    "_edit_fail_for_path", "_edit_restricted_files", "_edited_files", "_force_done",
    "_gate_retries_used", "_grep_zero_repeats", "_last_reflexion_ts",
    "_out_of_scope_created", "_plan_mode_read_only", "_read_count_per_file",
    "_read_identical_args_state", "_redirect_count", "_reflexion_generated_count",
    "_resolved_intent", "_self_repair_count", "_self_repair_count_per_file",
    "_session_memory", "_session_memory_last_used", "_success_generated_count",
    "_syntax_clean_snapshot", "_tools_used_this_task", "_write_counts",
    "_z36_refusals", "current_task", "history", "status",
}


def _attributs_self_ecrits() -> set[str]:
    """Tous les `self.X = …` de sub_agent.py, hors __init__ et hors le reset."""
    import ast

    chemin = Path(__file__).resolve().parents[2] / "src" / "agents" / "sub_agent.py"
    arbre = ast.parse(chemin.read_text(encoding="utf-8"))

    exclus: set[int] = set()
    for n in ast.walk(arbre):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in (
            "__init__", "_reset_task_scoped_state",
        ):
            exclus.update(range(n.lineno, getattr(n, "end_lineno", n.lineno) + 1))

    zones_agent: set[int] = set()
    for n in ast.walk(arbre):
        if isinstance(n, ast.ClassDef) and n.name in ("SubAgent", "CodeAgent"):
            zones_agent.update(range(n.lineno, getattr(n, "end_lineno", n.lineno) + 1))

    trouves: set[str] = set()
    for n in ast.walk(arbre):
        cibles = n.targets if isinstance(n, ast.Assign) else (
            [n.target] if isinstance(n, (ast.AugAssign, ast.AnnAssign)) else []
        )
        for c in cibles:
            if (
                isinstance(c, ast.Attribute)
                and isinstance(c.value, ast.Name)
                and c.value.id == "self"
                and c.lineno not in exclus
                and c.lineno in zones_agent
            ):
                trouves.add(c.attr)
    return trouves


class TestGelExhaustivite:
    def test_aucun_etat_task_scoped_nouveau_sans_examen(self):
        """Si un attribut apparaît sans passer par A1, ce test tombe — exprès.

        Il ne juge PAS que le nouvel attribut est un défaut : il force seulement
        à décider s'il doit mourir avec la tâche ou survivre.
        """
        nouveaux = _attributs_self_ecrits() - _CANDIDATS_A1
        assert not nouveaux, (
            f"Attribut(s) `self.*` non inventorié(s) : {sorted(nouveaux)}. "
            "Décide s'ils sont task-scoped (→ _reset_task_scoped_state) ou "
            "agent-scoped, puis mets à jour _CANDIDATS_A1 et "
            "plans/A1_INVENTAIRE_ETAT_2026-10-08.md."
        )

    def test_les_trois_fuites_sont_bien_dans_l_inventaire(self):
        """Garde-fou du garde-fou : la liste gelée contient ce que A2 corrige."""
        for attr in ("_z36_refusals", "_last_reflexion_ts", "_out_of_scope_created"):
            assert attr in _CANDIDATS_A1
