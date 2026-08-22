"""LOT N — ce qui est établi doit survivre jusqu'au bout.

Run HuffPack (2026-08-14). La mission a réussi : codec correct, 12/12 verts,
round-trip aléatoire sur 200 tirages, et — le plus important — des chiffres
HONNÊTES sur un résultat défavorable (50 Ko aléatoires : 51 200 → 52 489 octets,
le fichier grossit). Trois choses se sont pourtant perdues en route.

**N1 — le constat n'atteint pas l'utilisateur.** Les trois taux ont été calculés
à 03:11:27, puis jetés. Quand l'utilisateur a demandé « alors la dernière
mission », le récapitulatif — libre, bien formulé — n'a pu dire que « 12 passed »,
parce que les faits autoritatifs s'arrêtent à quatre compteurs. Le modèle s'était
promis « je livre le récapitulatif avec les chiffres concrets » ; il ne les a PAS
inventés (truth-lock tenu), il ne les avait plus.

**N2 — le périmètre se contourne par le shell.** Après un refus explicite, le
CodeAgent a écrit `test_structured_state.py` via `run_command`, en l'annonçant :
« seul canal non intercepté ». Le garde reposait sur une LISTE D'OUTILS, donc tout
outil hors liste passait. On regarde désormais le DISQUE : peu importe comment un
fichier naît.

**N3 — une lecture après délégation mentait.** Le CodeAgent remplit
`tests/test_huffpack.py` (79 lignes, prouvé par un pytest qui exécute les vrais
tests 8 s plus tard) ; le worker relit → « Cache hit » → il reçoit le STUB, conclut
que le CodeAgent a menti et tente une réparation inutile sur du travail correct.

Calibrage N1 sur les **113 commandes uniques réellement exécutées** (212 au total) :
49 tests/compilations, 21 `python -c`, 11 inspections, **3 exécutions de script**.
Le critère ne peut pratiquement pas faire de bruit.
"""
from __future__ import annotations

import pytest

from src.agents.sub_agent import (
    files_created_outside_perimeter,
    snapshot_mission_files,
)
from src.subagents.mission_measures import (
    command_is_measurement,
    format_measurements,
    merge_measurement,
    summarize_measurement_output,
)

# Sortie réelle du benchmark HuffPack.
_BENCH_OUT = (
    "Texte francais repetitif:\n"
    "  Taille originale : 7500 octets\n"
    "  Taille compressee : 3549 octets\n"
    "  Ratio (compresse/original en %) : 47.32%\n"
    "Octets aleatoires (50 Ko):\n"
    "  Taille originale : 51200 octets\n"
    "  Taille compressee : 52489 octets\n"
    "  Ratio (compresse/original en %) : 102.52%"
)


# ── N1 : quelles commandes sont des CONSTATS ────────────────────────────────

def test_the_exact_benchmark_command_is_a_measurement():
    """LE cas du lot : la commande dont la sortie n'est jamais arrivée."""
    assert command_is_measurement("python bench/benchmark.py") is True


@pytest.mark.parametrize(
    "command",
    [
        "python -m pytest tests/test_app.py -q",          # verdict déjà dans les faits
        "python -m py_compile bench/benchmark.py",
        "python -m ruff check tests/test_app.py",
        "python -m unittest discover",
        "pip install flask",
        "git status",
    ],
)
def test_tests_and_tooling_are_not_measurements(command):
    assert command_is_measurement(command) is False


@pytest.mark.parametrize(
    "command", ["dir", "ls -la", "cat core.py", "where python", "echo bonjour", "cd /tmp"]
)
def test_inspecting_is_not_measuring(command):
    assert command_is_measurement(command) is False


def test_inline_python_is_not_a_measurement():
    """`python -c` sert surtout aux smoke tests du CodeAgent (21 occurrences
    réelles) : trop ambigu pour valoir constat."""
    assert command_is_measurement('python -c "import ast; print(1)"') is False


def test_garbage_never_raises():
    for bad in (None, "", 42, "   ", "python"):
        assert command_is_measurement(bad) is False


# ── N1 : la sortie est gardée TELLE QUELLE ──────────────────────────────────

def test_the_real_numbers_are_preserved_verbatim():
    """Reformuler un chiffre mesuré serait la fabrication que ce lot combat."""
    out = summarize_measurement_output(_BENCH_OUT)
    assert "47.32%" in out and "68.69" not in out
    assert "102.52%" in out, "le résultat DÉFAVORABLE doit survivre comme les autres"
    assert "51200" in out and "52489" in out


def test_a_long_output_is_cut_and_says_so():
    out = summarize_measurement_output("ligne de mesure\n" * 400)
    assert len(out) <= 760
    assert "tronqu" in out


def test_empty_output_yields_nothing():
    assert summarize_measurement_output("") == ""
    assert summarize_measurement_output(None) == ""
    assert summarize_measurement_output("   \n  \n") == ""


# ── N1 : accumulation ───────────────────────────────────────────────────────

def test_rerunning_the_same_command_keeps_the_last_value():
    """Le lead rejoue le benchmark après correction : c'est la DERNIÈRE sortie
    qui reflète le code livré."""
    rows = merge_measurement(None, "python bench/benchmark.py", "ancien : 99%")
    rows = merge_measurement(rows, "python bench/benchmark.py", _BENCH_OUT)
    assert len(rows) == 1
    assert "47.32%" in rows[0]["output"] and "99%" not in rows[0]["output"]


def test_distinct_commands_accumulate():
    rows = merge_measurement(None, "python bench/benchmark.py", "A")
    rows = merge_measurement(rows, "python bench/stats.py", "B")
    assert [r["command"] for r in rows] == [
        "python bench/benchmark.py", "python bench/stats.py"
    ]


def test_the_list_is_bounded():
    rows = None
    for i in range(20):
        rows = merge_measurement(rows, f"python bench/b{i}.py", f"sortie {i}")
    assert len(rows) <= 5
    assert rows[-1]["output"] == "sortie 19"


def test_an_empty_output_adds_nothing():
    rows = merge_measurement(None, "python bench/benchmark.py", "")
    assert rows == []


def test_merge_survives_garbage():
    for bad in (None, [], "texte", [1, 2], [{"x": 1}]):
        assert isinstance(merge_measurement(bad, "python a.py", "ok"), list)


def test_formatting_shows_the_command_and_its_output():
    rows = merge_measurement(None, "python bench/benchmark.py", _BENCH_OUT)
    rendered = format_measurements(rows)
    assert "$ python bench/benchmark.py" in rendered
    assert "102.52%" in rendered


def test_formatting_nothing_is_empty():
    assert format_measurements(None) == ""
    assert format_measurements([]) == ""
    assert format_measurements([{"command": "x", "output": ""}]) == ""


# ── N1 : le branchement — un fait non lu est un fait perdu ──────────────────

def test_react_captures_the_measurement():
    import inspect

    from src.reasoning import react

    src = inspect.getsource(react)
    assert "command_is_measurement" in src and "mission_measurements" in src


def test_the_facts_expose_the_measurements():
    import inspect

    from src.reasoning.handlers import missions

    src = inspect.getsource(missions.mission_terminal_facts)
    assert "mission_measurements" in src
    assert '"measurements"' in src


def test_mission_status_text_carries_them():
    import inspect

    from src.reasoning.handlers import missions

    src = inspect.getsource(missions._mission_facts_text)
    assert "format_measurements" in src


def test_the_facts_text_really_contains_the_numbers():
    """Bout en bout sur un enregistrement de mission : les chiffres doivent
    apparaître dans le texte que le modèle reçoit."""
    from src.reasoning.handlers.missions import _mission_facts_text

    task = {
        "state": "done",
        "metadata": {
            "terminal_reason_code": "completed",
            "published_workspace": "workspace/huffpack",
            "mission_published": True,
            "mission_measurements": [
                {"command": "python bench/benchmark.py", "output": _BENCH_OUT}
            ],
        },
    }
    text = _mission_facts_text(task)
    assert "47.32%" in text and "102.52%" in text
    assert "publie=workspace/huffpack" in text


def test_a_mission_without_measurement_is_unchanged():
    """Non-régression : 25 missions sur 27 n'ont aucun constat — leur texte de
    faits ne doit pas bouger d'un caractère."""
    from src.reasoning.handlers.missions import _mission_facts_text

    task = {"state": "done", "metadata": {"terminal_reason_code": "completed"}}
    assert _mission_facts_text(task) == "cause=completed"


# ── N2 : le périmètre se juge sur le DISQUE ─────────────────────────────────

def test_the_exact_huffpack_bypass_is_caught():
    """`test_structured_state.py` créé par run_command après un refus."""
    before = frozenset({"huffpack/core.py", "CONTRAT.md"})
    after = before | {"test_structured_state.py"}
    assert files_created_outside_perimeter(
        before, after, ["huffpack/core.py", "huffpack/__init__.py"]
    ) == ["test_structured_state.py"]


def test_a_file_inside_the_perimeter_is_fine():
    before = frozenset({"CONTRAT.md"})
    after = before | {"huffpack/core.py"}
    assert files_created_outside_perimeter(
        before, after, ["huffpack/core.py"]
    ) == []


def test_several_bypasses_are_all_reported():
    before = frozenset()
    after = frozenset({"test_run_desktop.py", "test_structured_state.py", "app.py"})
    assert files_created_outside_perimeter(before, after, ["app.py"]) == [
        "test_run_desktop.py", "test_structured_state.py",
    ]


def test_no_perimeter_means_no_effect():
    """Hors mission, le CodeAgent doit se comporter exactement comme avant."""
    before, after = frozenset(), frozenset({"n_importe_quoi.py"})
    assert files_created_outside_perimeter(before, after, None) == []
    assert files_created_outside_perimeter(before, after, []) == []


def test_a_disappearing_file_is_never_reported():
    """Ce helper ne juge que les APPARITIONS — il ne supprime rien et ne
    s'occupe pas des fichiers retirés."""
    before = frozenset({"vieux.py"})
    assert files_created_outside_perimeter(before, frozenset(), ["app.py"]) == []


def test_comparison_survives_garbage():
    for bad in (None, "texte", 42):
        assert files_created_outside_perimeter(bad, bad, ["app.py"]) == []


def test_snapshot_of_a_missing_folder_is_empty_not_fatal():
    """Un instantané impossible ne doit JAMAIS faire échouer une action."""
    assert snapshot_mission_files(r"C:\dossier\qui\nexiste\pas") == frozenset()
    assert snapshot_mission_files(None) == frozenset()
    assert snapshot_mission_files("") == frozenset()


def test_snapshot_lists_files_and_ignores_noise(tmp_path):
    (tmp_path / "app.py").write_text("x", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "core.py").write_text("y", encoding="utf-8")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "app.cpython.pyc").write_text("z", encoding="utf-8")
    (tmp_path / ".backups").mkdir()
    (tmp_path / ".backups" / "app.py.bak").write_text("w", encoding="utf-8")

    snap = snapshot_mission_files(tmp_path)
    assert snap == frozenset({"app.py", "sub/core.py"})


def test_snapshot_detects_a_real_shell_write(tmp_path):
    """Le scénario complet, sur de vrais fichiers : ce qui compte est le disque,
    pas l'outil qui a écrit."""
    (tmp_path / "app.py").write_text("x", encoding="utf-8")
    before = snapshot_mission_files(tmp_path)
    (tmp_path / "parasite.py").write_text("cree hors perimetre", encoding="utf-8")
    after = snapshot_mission_files(tmp_path)
    assert files_created_outside_perimeter(
        before, after, ["app.py"], tmp_path
    ) == ["parasite.py"]


def test_the_guard_is_wired_around_the_action():
    import inspect

    from src.agents import sub_agent

    src = inspect.getsource(sub_agent)
    assert "snapshot_mission_files" in src
    assert "files_created_outside_perimeter" in src


def test_the_guard_warns_and_never_deletes():
    """Il signale, il n'efface pas : effacer le travail d'un agent sans preuve
    serait pire que le laisser."""
    import inspect

    from src.agents import sub_agent

    src = inspect.getsource(sub_agent)
    block = src.split("files_created_outside_perimeter")[-1][:2000]
    assert "HORS PÉRIMÈTRE" in block
    for destructive in ("os.remove", "unlink(", "shutil.rmtree"):
        assert destructive not in block


# ── N3 : une lecture après délégation dit la vérité du disque ───────────────

def test_delegation_invalidates_the_read_cache():
    import inspect

    from src.reasoning import tool_registry

    src = inspect.getsource(tool_registry)
    marker = src.split("_WRITE_TOOLS = {")[1].split("}")[0]
    assert '"delegate_task"' in marker
    assert '"delegate_and_wait"' in marker


def test_the_historic_write_tools_are_still_there():
    """Non-régression : la liste existante ne doit rien perdre."""
    import inspect

    from src.reasoning import tool_registry

    src = inspect.getsource(tool_registry)
    marker = src.split("_WRITE_TOOLS = {")[1].split("}")[0]
    for tool in ("write_file", "edit_file", "apply_patch", "str_replace",
                 "run_command", "apply_patches"):
        assert f'"{tool}"' in marker


# ── N1-bis : le constat était lu sur un attribut qui n'existe pas ───────────

def test_the_observation_field_the_capture_reads_actually_exists():
    """Run LogLens (2026-08-14) — la mission a MESURÉ pour de vrai :

        Lignes lues       : 29 579      Plus longue période sans erreur : 3h 13m 57s
        Lignes illisibles : 11 291      Temps de traitement : 0,174 s
        DEBUG 42,1% · INFO 55,1% · WARNING 2,1% · ERROR 0,6%

    …et l'utilisateur n'en a reçu AUCUN. Le log dit pourquoi, six fois :

        [N1] capture du constat ignorée: 'Observation' object has no attribute 'output'

    Je lisais `observation.output`. Le champ s'appelle `content`. L'AttributeError
    était avalée par le `except Exception` du site de capture — donc échec
    parfaitement silencieux : rien dans le comportement ne le trahissait, sauf
    l'absence des chiffres tout à la fin.
    """
    import dataclasses

    from src.reasoning.react import Observation

    champs = {f.name for f in dataclasses.fields(Observation)}
    assert "content" in champs
    assert "output" not in champs, "si ce champ apparaît un jour, revoir la capture N1"


def test_the_capture_site_reads_the_right_field():
    import inspect as _inspect

    from src.reasoning import react

    src = _inspect.getsource(react)
    bloc = src.split("command_is_measurement,")[1][:1800]
    assert "observation.content" in bloc
    assert "observation.output" not in bloc


def test_the_real_loglens_report_is_captured():
    """Bout en bout sur les chiffres réels du run."""
    from src.subagents.mission_measures import (
        command_is_measurement,
        merge_measurement,
    )

    cmd = "python run_analysis.py"
    sortie = (
        "LogLens - Rapport d'analyse\n"
        "Lignes lues          : 29579\n"
        "Lignes illisibles    : 11291\n"
        "  DEBUG    :   7702  42.1%\n"
        "  INFO     :  10080  55.1%\n"
        "Plus longue periode sans erreur : 3h 13m 57s\n"
        "Temps de traitement : 0.174s\n"
    )
    assert command_is_measurement(cmd) is True
    retenu = merge_measurement(None, cmd, sortie)
    assert retenu, "le constat mesuré doit être retenu"
    assert "29579" in str(retenu)
