"""H8 — la bannière « Non publié » ne vise pas une mission qui n'a rien à publier.

TEST RÉEL n°3 (mission `pyproject.toml`, 2026-08-13). Mission d'EFFETS purs :
chercher 2 sources, écrire un mémo en mémoire. Aucun fichier, donc rien à publier.
Le bilan livré portait pourtant :

    ⚠️ **Non publié** — `publish_mission_workspace` n'a pas été exécuté avec succès

Cause : le mémo **décrivait** `pyproject.toml`, dont une section sert à « la
publication sur PyPI ». Le mot est apparu dans le CONTENU DOCUMENTAIRE du
livrable et `claims_published()` l'a lu comme une revendication. La mission
PARLE de publication ; elle n'en revendique pas une.

Durcir la regex serait la course perdue d'avance déjà constatée en M1. On pose
un fait déterministe à la place : **un contrat sans aucun `files` décrit une
mission d'effets, que `publish_mission_workspace` ne concerne pas.**

Même principe que H4-b : les gardes conçus pour le CODE sont inertes sur les
missions d'actions.
"""
from __future__ import annotations

from src.reasoning.final_guards import apply_mission_truth_lock

# Le texte du run : « publication » y est un SUJET, pas une revendication.
_BODY = (
    "`pyproject.toml` regroupe la configuration de build, l'installation locale "
    "du package, la gestion des dépendances et la publication sur PyPI. "
    "Mission recherche accomplie !"
)


def _lock(**kw):
    return apply_mission_truth_lock(
        _BODY, has_green_test=True, has_published=False, **kw
    )


def test_an_effects_mission_is_not_told_it_failed_to_publish():
    text, info = _lock(file_deliverables_expected=False)
    assert "Non publié" not in text


def test_a_file_mission_still_gets_the_warning():
    """Le risque du lot est d'éteindre le garde pour tout le monde : un vrai
    livrable fichier non publié doit toujours être signalé."""
    text, _ = _lock(file_deliverables_expected=True)
    assert "Non publié" in text


def test_existing_callers_are_unchanged():
    """`None` = mission sans contrat : comportement historique, strictement."""
    assert "Non publié" in _lock()[0]
    assert "Non publié" in _lock(file_deliverables_expected=None)[0]


def test_a_real_publication_claim_is_still_caught_on_a_file_mission():
    text, info = apply_mission_truth_lock(
        "Livrable final publié dans workspace/monsite, succès complet livré.",
        has_green_test=True, has_published=False, file_deliverables_expected=True,
    )
    assert "Non publié" in text and info["overclaim"] is True


def test_the_effects_mission_keeps_its_other_guards():
    """H8 n'éteint QUE la publication : un over-claim de tests reste rétrogradé."""
    text, info = apply_mission_truth_lock(
        "Tous les tests passent, mission accomplie.",
        has_green_test=False, has_published=False,
        file_deliverables_expected=False,
    )
    assert info["overclaim"] is True


def test_h8_changes_nothing_but_the_publication_verdict():
    """La garantie centrale du lot, énoncée comme une comparaison : sur un même
    texte, seul le verdict de publication doit différer."""
    claim = "Tous les tests passent et le livrable final est publié."
    kw = {"has_green_test": False, "has_published": False}
    without = apply_mission_truth_lock(claim, **kw)[0]
    with_effects = apply_mission_truth_lock(
        claim, file_deliverables_expected=False, **kw
    )[0]
    assert "Non publié" in without and "Non publié" not in with_effects
    # Tout le reste survit à l'identique : l'over-claim de tests est rétrogradé
    # dans les DEUX cas, et le corps du message est conservé de part et d'autre.
    assert "Tests non exécutés" in without and "Tests non exécutés" in with_effects
    assert "le livrable final est publié." in without
    assert "le livrable final est publié." in with_effects


def test_a_clean_effects_report_stays_untouched():
    text, info = apply_mission_truth_lock(
        "Mémo enregistré en mémoire. Deux sources citées.",
        has_green_test=True, has_published=False,
        file_deliverables_expected=False,
    )
    assert info["changed"] is False
    assert info["overclaim"] is False


# ── Audit systématique de la classe entière ─────────────────────────────────
# Plutôt que de découvrir ces faux positifs un par un, run après run : une
# mission d'effets produit souvent un livrable DOCUMENTAIRE dont le SUJET croise
# le vocabulaire des gardes (« publie », « tests », « navigateur », « déploie »).
# Le sujet n'est pas une revendication.

_SUBJECT_TRAPS = [
    ("mémo sur pytest",
     "Mémo : pytest permet de lancer tous les tests du projet et de vérifier "
     "que tout passe."),
    ("mémo sur le web",
     "Mémo : un site web statique se sert avec nginx ; on ouvre la page dans le "
     "navigateur pour vérifier."),
    ("mémo sur git",
     "Mémo : `git push` publie les modifications sur le dépôt distant."),
    ("veille déploiement",
     "Mémo : le déploiement continu publie automatiquement le livrable en "
     "production."),
    ("mail envoyé",
     "Le mail a été envoyé et le fichier joint créé pour l'équipe."),
]


def test_no_guard_fires_on_a_documentary_deliverable():
    """Aucune de ces formulations n'est une revendication de la MISSION."""
    for label, body in _SUBJECT_TRAPS:
        _, info = apply_mission_truth_lock(
            body, has_green_test=False, has_published=False,
            has_any_mutation=False, has_browser_proof=False,
            file_deliverables_expected=False,
        )
        assert info["overclaim"] is False, label


def test_two_of_them_were_falsely_accused_before_h8():
    """Mesure de ce que le lot corrige — et garde-fou si `file_deliverables_expected`
    cessait d'être transmis : ces cas se remettraient à accuser."""
    accused = [
        label for label, body in _SUBJECT_TRAPS
        if apply_mission_truth_lock(
            body, has_green_test=False, has_published=False,
            has_any_mutation=False, has_browser_proof=False,
        )[1]["overclaim"]
    ]
    assert accused == ["mémo sur git", "veille déploiement"]


def test_a_healthy_effects_report_triggers_nothing():
    body = (
        "Mémo enregistré en mémoire via memory_add. Deux sources citées. "
        "Le mail de synthèse a été envoyé et le message posté sur #veille."
    )
    text, info = apply_mission_truth_lock(
        body, has_green_test=False, has_published=False, has_any_mutation=False,
        has_browser_proof=False, file_deliverables_expected=False,
    )
    assert info == {"changed": False, "overclaim": False}
    assert text == body


# ── Le fait est lu du contrat ───────────────────────────────────────────────

def test_the_flag_is_read_from_the_contract(tmp_path, monkeypatch):
    import json
    import types

    from src.reasoning.react import ReActLoop

    ws = tmp_path / "missions" / "task_x"
    ws.mkdir(parents=True)
    monkeypatch.setattr("src.utils.paths.WORKSPACE_DIR", tmp_path)

    loop = object.__new__(ReActLoop)
    loop.task_id = "task_x"
    loop.task_orchestrator = types.SimpleNamespace(
        get_task=lambda _i: {"metadata": {"mission_workspace": "missions/task_x"}}
    )

    (ws / "contract.json").write_text(
        json.dumps({"effects": [{"owner": "w", "action": "a", "desc": "d",
                                 "proof": "p"}]}), encoding="utf-8"
    )
    assert loop._mission_expects_file_deliverables() is False

    (ws / "contract.json").write_text(
        json.dumps({"files": [{"path": "app.py", "owner": "w"}]}), encoding="utf-8"
    )
    assert loop._mission_expects_file_deliverables() is True


def test_no_contract_means_unknown(tmp_path, monkeypatch):
    import types

    from src.reasoning.react import ReActLoop

    (tmp_path / "missions" / "task_x").mkdir(parents=True)
    monkeypatch.setattr("src.utils.paths.WORKSPACE_DIR", tmp_path)
    loop = object.__new__(ReActLoop)
    loop.task_id = "task_x"
    loop.task_orchestrator = types.SimpleNamespace(
        get_task=lambda _i: {"metadata": {"mission_workspace": "missions/task_x"}}
    )
    assert loop._mission_expects_file_deliverables() is None


def test_outside_a_mission_it_is_unknown():
    import types

    from src.reasoning.react import ReActLoop

    loop = object.__new__(ReActLoop)
    loop.task_id = "task_x"
    loop.task_orchestrator = types.SimpleNamespace(
        get_task=lambda _i: {"metadata": {}}
    )
    assert loop._mission_expects_file_deliverables() is None


def test_a_broken_contract_never_raises(tmp_path, monkeypatch):
    import types

    from src.reasoning.react import ReActLoop

    ws = tmp_path / "missions" / "task_x"
    ws.mkdir(parents=True)
    (ws / "contract.json").write_text("{pas du json", encoding="utf-8")
    monkeypatch.setattr("src.utils.paths.WORKSPACE_DIR", tmp_path)
    loop = object.__new__(ReActLoop)
    loop.task_id = "task_x"
    loop.task_orchestrator = types.SimpleNamespace(
        get_task=lambda _i: {"metadata": {"mission_workspace": "missions/task_x"}}
    )
    assert loop._mission_expects_file_deliverables() is None
