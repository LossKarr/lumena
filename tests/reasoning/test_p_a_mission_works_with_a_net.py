"""LOT P — une mission qui travaille SEULE doit avoir un filet.

Run HuffPack v2 (2026-08-14). La mission part bien (LOT O2 prouvé : gate →
`create_mission`), répare le codec cassé, écrit ses tests… puis meurt.

Timeline exacte :

    04:36  write_file  core.py            → écriture RÉUSSIE
    04:37  pytest      12 passed          → codec RÉPARÉ
    04:38  edit_file   test_huffpack.py   → écriture RÉUSSIE (4 tests ajoutés)
    04:38  pytest      16 passed, 1 failed → un seuil à ajuster, rien d'autre
    04:42  ⚠️ read_file stagnation — forçage FINAL (relectures=3)
           budget restant : 4 775 s = 79 MINUTES
           puis : aucun rapport, aucune publication, plus une ligne jusqu'à 14:33

Deux défauts, mesurés :

**P3 — le compteur de relecture ne redescendait jamais.** Deux écritures réussies
s'étaient intercalées sans le faire bouger. C'est mot pour mot ce que PG-1.a a
corrigé pour le compteur de progression du plan (SkiLoc : « FINAL forcé avec
2 048 s de budget restant, à une itération de la victoire »). Et surtout : forcer
un FINAL dans un tour de chat rend une réponse ; dans une mission, ça la TUE.

**P2 — une mission solo n'a pas de dossier à elle.** `create_mission` posait
`depth` mais jamais `mission_workspace` : seuls `write_mission_contract` et
`delegate_and_wait` l'attribuaient. Une mission qui travaille seule écrivait donc
là où l'objectif pointait — dans le livrable de production. HuffPack v1 (publié,
12/12 verts) a été écrasé DEUX FOIS en une heure, malgré la consigne explicite
« travaille dans une copie de mission ».

Mesuré sur les écritures réelles : **23 sur 104 (22 %)** visent `workspace/<projet>`
hors dossier de mission, dont les 3 écrasements successifs de `core.py`.
"""
from __future__ import annotations

import inspect

import pytest

from src.reasoning.react import (
    mission_write_path_exists,
    mission_write_targets_existing_deliverable,
    read_stagnation_action,
)


# ── P2b-bis : le garde s'était tu parce que MON calcul de chemin était faux ──

def test_the_path_the_guard_failed_to_recognise(tmp_path):
    """Run HuffPack v4 (2026-08-14) — la mission a écrit QUATRE fois dans
    `workspace/huffpack/huffpack/core.py` sans que P2b ne dise rien, et le
    livrable est ressorti cassé (5 passed, 12 failed).

    Cause : je préfixais un chemin DÉJÀ préfixé.

        default_workspace_root : …/lumena/workspace
        chemin du modèle       : workspace/huffpack/huffpack/core.py
        mon calcul             : …/workspace/workspace/huffpack/…  → absent
        le vrai fichier        : …/workspace/huffpack/…            → présent

    Les modèles écrivent les trois formes indifféremment ; un fichier trouvé
    sous n'importe quel ancrage plausible est un fichier qui existe.
    """
    # Nom volontairement unique : le test ne doit rien devoir à l'état du dépôt.
    ws = tmp_path / "workspace"
    (ws / "p2bis_projet" / "pkg").mkdir(parents=True)
    (ws / "p2bis_projet" / "pkg" / "core.py").write_text("x", encoding="utf-8")

    # Forme « nue », ancrée sur le workspace : c'est celle qui marchait déjà.
    assert mission_write_path_exists(
        "p2bis_projet/pkg/core.py", workspace_root=ws
    ) is True

    # Forme PRÉFIXÉE — celle du run : l'ancien calcul doublait le préfixe et
    # concluait « absent ». On la retrouve désormais via la racine projet.
    assert mission_write_path_exists(
        "workspace/p2bis_projet/pkg/core.py", workspace_root=tmp_path
    ) is True


def test_an_absolute_path_is_checked_as_is(tmp_path):
    f = tmp_path / "core.py"
    f.write_text("x", encoding="utf-8")
    assert mission_write_path_exists(str(f)) is True
    assert mission_write_path_exists(str(tmp_path / "absent.py")) is False


def test_a_new_file_is_never_seen_as_existing(tmp_path):
    assert mission_write_path_exists(
        "projet_neuf/app.py", workspace_root=tmp_path
    ) is False


@pytest.mark.parametrize("bad", ["", "   ", None, 42, []])
def test_path_probe_never_raises(bad):
    assert mission_write_path_exists(bad) is False


def test_the_gate_uses_the_multi_anchor_probe():
    """Le garde ne doit plus refaire le calcul à la main."""
    from src.reasoning.react import ReActLoop

    # Lot RF-6b : la DECISION de ce gate a ete deplacee vers
    # `mission_runtime.py` ; `ReActLoop` ne garde que la mutation, le log
    # et la construction de l'Observation (invariants 5 et 16). Ce test lit
    # donc le source la ou il vit desormais — intention mot pour mot
    # inchangee. Preuve COMPORTEMENTALE adossee : matrice RF-6b, 51 valeurs
    # comparees (retours ET etat mute), 51 identiques.
    from src.reasoning.mission_runtime import (
        rf6b_decision_ecrasement_livrable as _decision,
    )

    src = inspect.getsource(_decision)
    assert "etat.chemin_ecriture_existe(" in src  # RF-6b : nom suivi du rebindage, intention inchangee (self.X -> etat.Y())
    assert "default_workspace_root) / target" not in src


# ── P3 : une mission avec du budget n'est pas achevée ───────────────────────

def test_the_exact_huffpack_case_would_be_redirected():
    """4 775 s restantes — 79 minutes de travail perdues."""
    assert read_stagnation_action(
        is_mission_run=True, budget_remaining_s=4775.0, shots_used=0
    ) == "redirect"


def test_chat_behaviour_is_untouched():
    """Hors mission, forcer un FINAL est bénin : on rend une réponse. Ce
    comportement historique ne doit pas bouger d'un pouce."""
    for remaining in (4775.0, 600.0, 10.0, 0.0):
        assert read_stagnation_action(
            is_mission_run=False, budget_remaining_s=remaining, shots_used=0
        ) == "end", remaining


def test_a_mission_at_the_end_of_its_budget_still_ends():
    """Sans temps utile, rediriger ne servirait qu'à brûler des itérations."""
    for remaining in (300.0, 299.0, 120.0, 1.0, 0.0):
        assert read_stagnation_action(
            is_mission_run=True, budget_remaining_s=remaining, shots_used=0
        ) == "end", remaining


def test_the_redirect_fires_only_once():
    """Sinon une mission vraiment bloquée tournerait jusqu'à l'échéance."""
    assert read_stagnation_action(
        is_mission_run=True, budget_remaining_s=4775.0, shots_used=1
    ) == "end"


@pytest.mark.parametrize("bad", [None, "x", -1, float("nan")])
def test_a_broken_budget_never_keeps_the_run_alive(bad):
    """En cas de doute, on garde le comportement historique."""
    assert read_stagnation_action(
        is_mission_run=True, budget_remaining_s=bad, shots_used=0
    ) == "end"


def test_a_successful_write_resets_the_read_counters():
    """Relire un fichier qu'on vient de modifier, c'est vérifier son travail —
    pas stagner. Même règle que PG-1.a."""
    from src.reasoning import react

    src = inspect.getsource(react)
    block = src.split("LOT P3 — une MUTATION RÉUSSIE")[1][:900]
    assert "_PG1_MUTATION_TOOLS" in block
    assert "_read_file_reread_counter.clear()" in block
    assert "_read_file_path_counter.clear()" in block


def test_the_redirect_is_wired_and_does_not_end_the_run():
    from src.reasoning import react

    src = inspect.getsource(react)
    assert "read_stagnation_action(" in src
    block = src.split('== "redirect"')[1][:2600]
    assert "_read_stagnation_shots" in block
    assert "read_stagnation_redirect" in block
    # La branche de redirection rend la main à la boucle au lieu de clore le run.
    assert "continue" in block
    head = block[: block.index("continue")]
    assert "_mark_task_done" not in head, "la redirection ne doit PAS clore la mission"


# ── P2b : un livrable déjà livré ne se réécrit pas en place ─────────────────

def test_the_exact_overwritten_files_are_caught():
    ws = "missions/task_8f8d852edefa4d60af38dd81de0af488"
    for target in (
        "workspace/huffpack/huffpack/core.py",
        "workspace/huffpack/tests/test_huffpack.py",
    ):
        assert mission_write_targets_existing_deliverable(
            target, ws, exists=True
        ) is True, target


def test_writing_inside_its_own_folder_is_free():
    ws = "missions/task_abc"
    for target in (
        "missions/task_abc/core.py",
        "missions/task_abc/tests/test_x.py",
        "workspace/missions/task_abc/app.py",
    ):
        assert mission_write_targets_existing_deliverable(
            target, ws, exists=True
        ) is False, target


def test_a_brand_new_file_is_free():
    """Missions d'effets — PDF, CSV, page neuve : rien ne change pour elles."""
    for target in (
        "workspace/rapport.pdf",
        "workspace/sonocal/public/index.html",
        "workspace/nouveau_projet/app.py",
    ):
        assert mission_write_targets_existing_deliverable(
            target, "missions/task_abc", exists=False
        ) is False, target


def test_without_a_mission_folder_it_is_inert():
    """Hors mission, ou mission sans dossier : aucun effet."""
    for ws in ("", "   ", None):
        assert mission_write_targets_existing_deliverable(
            "workspace/huffpack/core.py", ws, exists=True
        ) is False


def test_garbage_never_raises():
    for target in ("", None, 42):
        assert isinstance(
            mission_write_targets_existing_deliverable(
                target, "missions/task_abc", exists=True
            ),
            bool,
        )


def test_the_gate_is_inert_outside_a_mission():
    from src.reasoning.react import ReActLoop

    # Lot RF-6b : la DECISION de ce gate a ete deplacee vers
    # `mission_runtime.py` ; `ReActLoop` ne garde que la mutation, le log
    # et la construction de l'Observation (invariants 5 et 16). Ce test lit
    # donc le source la ou il vit desormais — intention mot pour mot
    # inchangee. Preuve COMPORTEMENTALE adossee : matrice RF-6b, 51 valeurs
    # comparees (retours ET etat mute), 51 identiques.
    from src.reasoning.mission_runtime import (
        rf6b_decision_ecrasement_livrable as _decision,
    )

    src = inspect.getsource(_decision)
    assert "if not etat.est_run_mission_strict():" in src  # RF-6b : nom suivi du rebindage, intention inchangee (self.X -> etat.Y())
    # RF-6b : noms suivis du rebindage, ORDRE verifie a l'identique.
    assert src.index("est_run_mission_strict") < src.index("dossier_mission")


def test_the_gate_redirects_once_and_tells_where_to_work():
    from src.reasoning.react import ReActLoop

    # Lot RF-6b : la DECISION de ce gate a ete deplacee vers
    # `mission_runtime.py` ; `ReActLoop` ne garde que la mutation, le log
    # et la construction de l'Observation (invariants 5 et 16). Ce test lit
    # donc le source la ou il vit desormais — intention mot pour mot
    # inchangee. Preuve COMPORTEMENTALE adossee : matrice RF-6b, 51 valeurs
    # comparees (retours ET etat mute), 51 identiques.
    from src.reasoning.mission_runtime import (
        rf6b_decision_ecrasement_livrable as _decision,
    )

    src = inspect.getsource(_decision)
    assert "_overwrite_gate_shots" in src and ">= 1" in src
    assert "publish_mission_workspace" in src
    assert "Redirection unique" in src


def test_the_gate_is_wired_in_the_cascade():
    from src.reasoning import react

    src = inspect.getsource(react)
    assert "_mission_overwrite_gate(" in src
    assert "_ovw_obs is not None" in src


# ── P2a : la mission solo reçoit un dossier ────────────────────────────────

def test_create_mission_now_assigns_a_workspace():
    """Le reste de la chaîne existe déjà : `_mission_workspace_meta` le lit,
    `_prepare_handler_context` l'injecte, `_resolve_execution_root` en fait la
    racine. Il ne manquait que l'attribution."""
    from src.reasoning.handlers import missions

    src = inspect.getsource(missions.create_mission_handler)
    assert "mission_workspace" in src
    assert 'f"missions/{mid}"' in src


def test_assigning_the_workspace_never_breaks_the_creation():
    """Une mission doit se lancer même si la métadonnée échoue."""
    from src.reasoning.handlers import missions

    src = inspect.getsource(missions.create_mission_handler)
    block = src.split("LOT P2a")[1]
    assert "try:" in block and "except Exception" in block


def test_the_other_two_paths_still_assign_it():
    """Non-régression : le contrat et la délégation continuent de poser le
    dossier (ils partagent `_ensure_mission_workspace`)."""
    from src.reasoning.handlers import missions

    src = inspect.getsource(missions)
    assert src.count("_ensure_mission_workspace(orch, lead_id") >= 2
