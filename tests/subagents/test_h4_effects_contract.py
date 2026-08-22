"""H4 — le contrat sait décrire un livrable qui n'est PAS un fichier.

Constat d'inventaire (2026-08-13) : sur 596 outils natifs + ~127 outils MCP,
**82 % ne produisent ni code ni document**. Or `validate_contract` exigeait
`files` non vide — donc une mission « cherche, envoie le mail, poste sur Slack »
était *incontractualisable* : pas de contrat, pas d'owners, pas de périmètre,
pas de coordination. Les missions non-code retombaient sur la seule prose du
lead, c'est-à-dire sur rien.

Le contrat accepte désormais `effects: [{owner, action, target?, desc, proof}]`,
seuls ou combinés aux fichiers. `proof` est OBLIGATOIRE : un effet sans preuve
attendue ne peut être clôturé qu'en croyant le worker sur parole — la faille
exacte que tout ce chantier ferme.

Et la clôture en tient compte (H4.b) : un effet dont le porteur n'a jamais
terminé n'est pas prouvé réalisé. Sans ce verrou, `effects` aurait été une
porte de sortie vers la clôture sur récit.
"""
from __future__ import annotations

from src.subagents.mission_contract import (
    effects_brief,
    effects_map,
    render_contract_md,
    unproven_effect_owners,
    validate_contract,
    worker_objectives,
)
from src.subagents.runner import closure_decision

_EFFECTS = {
    "project": "VeilleTech",
    "effects": [
        {"owner": "w_rech", "action": "recherche_web", "target": "python 3.14",
         "desc": "3 sources datées", "proof": "3 URLs citées"},
        {"owner": "w_notif", "action": "poster_slack", "target": "#veille",
         "desc": "poster la synthèse", "proof": "id du message"},
    ],
}


# ── Une mission sans aucun fichier est enfin contractualisable ───────────────

def test_effects_only_contract_is_valid():
    assert validate_contract(_EFFECTS) == []


def test_files_only_contract_still_valid():
    """Le cas historique ne bouge pas — zéro régression sur le code."""
    assert validate_contract(
        {"files": [{"path": "app.py", "owner": "w", "exports": ["def run() -> None"]}]}
    ) == []


def test_a_contract_with_neither_is_still_refused():
    errs = validate_contract({"project": "x"})
    assert len(errs) == 1
    assert "effects" in errs[0] and "files" in errs[0]


def test_the_refusal_message_teaches_both_shapes():
    """Leçon MotCompteur/RéservaSalle : une erreur non guidante fait boucler le lead."""
    msg = validate_contract({"project": "x"})[0]
    assert "action" in msg and "proof" in msg


# ── La preuve attendue est obligatoire ──────────────────────────────────────

def test_effect_without_proof_is_refused():
    errs = validate_contract({"effects": [{"owner": "w", "action": "a", "desc": "d"}]})
    assert any("proof" in e for e in errs)


def test_effect_requires_owner_action_and_desc():
    errs = validate_contract({"effects": [{}]})
    for field in ("owner", "action", "desc", "proof"):
        assert any(field in e for e in errs), field


def test_same_effect_cannot_have_two_owners():
    """Sinon l'action part deux fois : mail en double, réservation dupliquée."""
    errs = validate_contract({"effects": [
        {"owner": "w1", "action": "mail", "target": "jean@x.fr", "desc": "d", "proof": "p"},
        {"owner": "w2", "action": "mail", "target": "jean@x.fr", "desc": "d", "proof": "p"},
    ]})
    assert any("UN SEUL owner" in e for e in errs)


def test_same_action_on_different_targets_is_fine():
    assert validate_contract({"effects": [
        {"owner": "w1", "action": "mail", "target": "jean@x.fr", "desc": "d", "proof": "p"},
        {"owner": "w2", "action": "mail", "target": "luc@x.fr", "desc": "d", "proof": "p"},
    ]}) == []


def test_garbage_effects_never_raise():
    assert validate_contract({"effects": "pas une liste"})
    assert validate_contract({"files": [{"path": "a.py", "owner": "w",
                                        "no_public_api": True}],
                              "effects": ["pas un objet"]})


# ── Chaque owner d'effet devient un worker, avec sa preuve à ramener ─────────

def test_each_effect_owner_becomes_a_worker():
    objs = worker_objectives(_EFFECTS)
    assert len(objs) == 2


def test_effect_worker_has_no_file_perimeter():
    """Il ne possède aucun fichier : `[]`, et H3 l'empêchera de piétiner ceux
    des autres workers vivants."""
    assert [o["allowed_files"] for o in worker_objectives(_EFFECTS)] == [[], []]


def test_worker_is_told_what_proof_to_bring_back():
    text = worker_objectives(_EFFECTS)[1]["objective"]
    assert "id du message" in text
    assert "PREUVE" in text


def test_worker_is_told_it_may_use_every_tool():
    """« En mission ils peuvent utiliser tous les outils de Lumena. »"""
    text = worker_objectives(_EFFECTS)[0]["objective"]
    assert "MCP" in text and "Slack" in text


def test_worker_is_forbidden_to_imply_success():
    text = worker_objectives(_EFFECTS)[0]["objective"]
    assert "n'a PAS été réalisé" in text


def test_effect_worker_is_not_told_to_code():
    """La discipline de CODAGE (« ne conclus pas sans une mutation réelle »)
    pousserait un porteur d'effet à écrire un script au lieu d'envoyer le mail."""
    text = worker_objectives(_EFFECTS)[0]["objective"]
    assert "DISCIPLINE DE CODAGE" not in text
    assert "DISCIPLINE D'ACTION" in text


def test_effect_worker_is_told_it_has_every_tool_of_the_parent():
    text = worker_objectives(_EFFECTS)[0]["objective"]
    assert "MÊMES outils que le parent" in text


def test_effect_worker_must_not_conclude_on_an_intention():
    text = worker_objectives(_EFFECTS)[0]["objective"]
    assert "intention" in text and "PREUVE" in text


def test_a_worker_owning_files_keeps_the_coding_discipline():
    """Périmètre du fix : STRICTEMENT le cas neuf (aucun fichier + des effets)."""
    mixed = {
        "files": [{"path": "app.py", "owner": "w_api", "exports": ["def run() -> None"]}],
        "effects": [{"owner": "w_api", "action": "deployer", "target": "ionos",
                     "desc": "d", "proof": "p"}],
    }
    assert "DISCIPLINE DE CODAGE" in worker_objectives(mixed)[0]["objective"]


def test_a_file_only_worker_keeps_the_coding_discipline():
    data = {"files": [{"path": "a.py", "owner": "w", "exports": ["def f() -> int"]}]}
    assert "DISCIPLINE DE CODAGE" in worker_objectives(data)[0]["objective"]


def test_a_worker_can_hold_both_files_and_effects():
    mixed = {
        "files": [{"path": "app.py", "owner": "w_api", "exports": ["def run() -> None"]}],
        "effects": [{"owner": "w_api", "action": "deployer", "target": "ionos",
                     "desc": "mettre en ligne", "proof": "URL qui répond 200"}],
    }
    assert validate_contract(mixed) == []
    objs = worker_objectives(mixed)
    assert len(objs) == 1
    assert objs[0]["allowed_files"] == ["app.py"]
    assert "app.py" in objs[0]["objective"] and "deployer" in objs[0]["objective"]


def test_file_workers_are_unchanged_by_the_new_field():
    """Un contrat 100 % fichiers produit exactement ce qu'il produisait."""
    data = {"files": [{"path": "a.py", "owner": "w1", "exports": ["def f() -> int"]},
                      {"path": "b.py", "owner": "w2", "exports": ["def g() -> int"]}]}
    objs = worker_objectives(data)
    assert [o["allowed_files"] for o in objs] == [["a.py"], ["b.py"]]
    assert "EFFETS" not in objs[0]["objective"]


# ── Helpers purs ────────────────────────────────────────────────────────────

def test_effects_map_groups_by_owner():
    assert list(effects_map(_EFFECTS)) == ["w_rech", "w_notif"]


def test_effects_map_tolerates_garbage():
    assert effects_map({}) == {}
    assert effects_map(None) == {}
    assert effects_map({"effects": ["x", {"action": "a"}]}) == {}


def test_effects_brief_is_empty_without_effects():
    assert effects_brief([]) == ""
    assert effects_brief(None) == ""


def test_contract_md_shows_effects_and_their_proof():
    md = render_contract_md(_EFFECTS)
    assert "poster_slack" in md and "id du message" in md and "w_notif" in md


def test_contract_md_still_renders_a_file_contract():
    md = render_contract_md({"files": [{"path": "a.py", "owner": "w",
                                        "exports": ["def f() -> int"]}]})
    assert "`a.py`" in md and "def f() -> int" in md


# ── H4.b — un effet sans porteur arrivé à terme n'est pas prouvé ────────────

def _kid(owner, state):
    return {"state": state, "metadata": {"delegation_owner": owner}}


def test_all_owners_finished_leaves_a_clean_closure():
    kids = [_kid("w_rech", "done"), _kid("w_notif", "done")]
    assert unproven_effect_owners(_EFFECTS, kids) == []


def test_a_failed_carrier_makes_its_effect_unproven():
    kids = [_kid("w_rech", "done"), _kid("w_notif", "failed")]
    assert unproven_effect_owners(_EFFECTS, kids) == ["w_notif"]


def test_a_never_delegated_owner_is_unproven():
    """Le lead a délégué 1 worker sur 2 et conclu : le second effet n'existe pas."""
    assert unproven_effect_owners(_EFFECTS, [_kid("w_rech", "done")]) == ["w_notif"]


def test_a_solo_mission_is_not_condemned():
    """Aucune délégation = contrat jamais mis en vigueur ; c'est le truth-lock
    qui juge le récit du lead. Sans cette porte on condamnerait du vrai travail."""
    assert unproven_effect_owners(_EFFECTS, []) == []
    assert unproven_effect_owners(_EFFECTS, None) == []


def test_a_file_only_contract_is_never_judged_on_effects():
    data = {"files": [{"path": "a.py", "owner": "w1"}]}
    assert unproven_effect_owners(data, [_kid("w1", "failed")]) == []


def test_unproven_owners_never_raise():
    assert unproven_effect_owners(None, [_kid("w", "done")]) == []
    assert unproven_effect_owners(_EFFECTS, ["pas un dict"]) == []
    assert unproven_effect_owners(_EFFECTS, [{"state": "done"}]) == ["w_rech", "w_notif"]


def test_closure_reports_unproven_effects():
    code, detail = closure_decision(
        overclaim=False, web_failed=False, effects_unproven=True
    )
    assert code == "completed_effects_unproven"
    assert "EFFET" in detail


def test_a_proven_effects_mission_closes_clean():
    """Le risque du lot est de sur-bloquer les missions non-code."""
    assert closure_decision(
        overclaim=False, web_failed=False, effects_unproven=False
    )[0] == "completed"


def test_more_precise_facts_keep_priority():
    """Web et overclaim nomment le défaut ; l'effet non prouvé est le filet."""
    assert closure_decision(
        overclaim=False, web_failed=True, effects_unproven=True
    )[0] == "completed_web_unverified"
    assert closure_decision(
        overclaim=True, web_failed=False, effects_unproven=True
    )[0] == "completed_with_unproven_claims"


def test_closure_signature_stays_backward_compatible():
    assert closure_decision(overclaim=False, web_failed=False)[0] == "completed"


# ── Le chaînon que seul le TEST RÉEL a révélé ───────────────────────────────
# Run `veille_python_313` (2026-08-13) : le contrat d'effets était posé, les deux
# workers lancés… mais `_contract_delegation_specs` dérivait ses owners de
# `owners_map()`, qui ne connaît que les FICHIERS. Sur un contrat d'effets purs :
# `owners == []` ≠ 2 objectifs → specs vides, fingerprint vide → **`delegation_owner`
# jamais posé sur les enfants**. Conséquence : H4.b aurait déclaré TOUS les effets
# non prouvés (le faux positif exact que les gardes anti-sur-blocage visaient), et
# H3 n'aurait protégé personne. Les 36 tests ci-dessus ne l'ont pas vu : ils
# testent `worker_objectives` isolément, jamais la chaîne de délégation.

def test_delegation_plan_knows_effect_owners():
    from src.reasoning.handlers.missions import _contract_delegation_specs

    specs, fingerprint = _contract_delegation_specs(_EFFECTS)
    assert [s["owner"] for s in specs] == ["w_rech", "w_notif"]
    assert fingerprint, "sans fingerprint, delegation_owner n'est jamais posé"


def test_effect_workers_get_an_empty_perimeter_in_the_plan():
    from src.reasoning.handlers.missions import _contract_delegation_specs

    specs, _ = _contract_delegation_specs(_EFFECTS)
    assert [s["allowed_files"] for s in specs] == [[], []]


def test_delegation_plan_pairs_owner_and_perimeter_in_a_mixed_contract():
    """Le zip owners↔objectifs doit garder l'ORDRE de `worker_objectives`,
    sinon un worker reçoit le périmètre d'un autre."""
    from src.reasoning.handlers.missions import _contract_delegation_specs

    mixed = {
        "files": [{"path": "a.py", "owner": "w_api", "exports": ["def f() -> int"]}],
        "effects": [{"owner": "w_dep", "action": "deployer", "desc": "d", "proof": "p"}],
    }
    specs, _ = _contract_delegation_specs(mixed)
    assert [(s["owner"], s["allowed_files"]) for s in specs] == [
        ("w_api", ["a.py"]), ("w_dep", []),
    ]


def test_file_only_delegation_plan_is_unchanged():
    from src.reasoning.handlers.missions import _contract_delegation_specs

    data = {"files": [{"path": "a.py", "owner": "w1", "exports": ["def f() -> int"]},
                      {"path": "b.py", "owner": "w2", "exports": ["def g() -> int"]}]}
    specs, fingerprint = _contract_delegation_specs(data)
    assert [(s["owner"], s["allowed_files"]) for s in specs] == [
        ("w1", ["a.py"]), ("w2", ["b.py"]),
    ]
    assert fingerprint


# ── H4-bis : tolérance de forme, mesurée en run réel ─────────────────────────
# Le modèle a passé `effects` en argument TOP-LEVEL au lieu de l'imbriquer dans
# `contract` → arg retiré par le registre → « paramètre requis manquant » → une
# itération perdue avant qu'il ne se corrige seul.

def test_toplevel_effects_are_folded_into_the_contract():
    import inspect

    from src.reasoning.handlers import missions as M

    params = inspect.signature(M.write_mission_contract_handler).parameters
    assert "effects" in params and "files" in params


def test_the_retry_guide_teaches_the_effects_shape():
    """Un lead de mission non-code qui se trompe ne doit pas être renvoyé vers
    un exemple 100 % code."""
    import inspect

    from src.reasoning.handlers import missions as M

    src = inspect.getsource(M.write_mission_contract_handler)
    assert '"effects"' in src and "proof" in src
