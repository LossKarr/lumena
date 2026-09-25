"""LOT ORI-3 — un travail mort doit ETRE mort, pas seulement paraitre vieux.

--- Pourquoi ce lot existe ---

Le cycle normal d'un tour est `queued -> running -> checkpointed(xN) -> done`.
`mark_checkpoint` est pose pendant le tour (`react.py:2813/2830`,
`chat.py:1951/2292/2400`) et `mark_done` le clot (`chat.py:1781/2711`). Si le
processus meurt entre les deux, la tache reste **`checkpointed` pour toujours** :
aucun code ne peut garantir qu'un processus tue appelle sa cloture.

`reconcile_on_boot` (lot 0.d) traite deja ce cas pour les MISSIONS. Rien ne le
faisait pour les tours Agent et vocaux.

--- L'erreur que ces tests figent ---

Un premier correctif ecartait ces taches en comparant leur derniere mise a jour
a l'instant de demarrage du processus. Un second correctif, ecrit ensuite,
CLOTURAIT leurs orientations — or `mutate_task_metadata` fait
`record.updated_at = _now_iso()` (`task_orchestrator.py:399`). Les taches
redevenaient donc « fraiches » et repassaient le premier filtre : le second
correctif annulait le premier.

Lecon : ne pas DEDUIRE qu'un travail est mort, le MARQUER mort. Le test
`test_le_registre_ignore_un_tour_clos_meme_frachement_ecrit` fige exactement ce
piege.
"""
from __future__ import annotations

from src.runtime.task_orchestrator import TaskOrchestrator
from src.runtime.task_steering_store import TaskSteeringStore
from src.runtime.work_registry import ActiveWorkRegistry

OWNER = "local:owner"


def _tache(orch, kind: str, *, conversation: str = "conv-a", parent: str | None = None) -> str:
    meta = {"kind": kind, "owner_user_id": OWNER, "objective": "objectif"}
    if parent:
        meta["parent_id"] = parent
    record = orch.start_task(
        conversation_id=conversation, channel="web",
        message_preview="objectif", metadata=meta,
    )
    orch.mark_running(record.task_id)
    orch.mark_checkpoint(record.task_id, {"etape": "en cours"})
    return record.task_id


def _etat(orch, task_id: str) -> str:
    return str((orch.get_task(task_id) or {}).get("state"))


def test_un_tour_agent_interrompu_est_clos_au_demarrage():
    from src.runtime.steering_reconciliation import clore_travaux_interrompus

    orch = TaskOrchestrator(persistence_path=None)
    tour = _tache(orch, "agent_turn")
    assert _etat(orch, tour) == "checkpointed"

    clore_travaux_interrompus(orch)

    assert _etat(orch, tour) == "cancelled", (
        "un tour dont le processus a disparu reste presente comme du travail en cours"
    )


def test_un_tour_vocal_interrompu_est_clos_aussi():
    from src.runtime.steering_reconciliation import clore_travaux_interrompus

    orch = TaskOrchestrator(persistence_path=None)
    tour = _tache(orch, "voice_turn", conversation="voice:default:primary")
    clore_travaux_interrompus(orch)
    assert _etat(orch, tour) == "cancelled"


def test_les_orientations_du_tour_clos_sont_annulees():
    from src.runtime.steering_reconciliation import clore_travaux_interrompus

    orch = TaskOrchestrator(persistence_path=None)
    tour = _tache(orch, "agent_turn")
    store = TaskSteeringStore(orch)
    store.enqueue(tour, "add_constraint", text="en bleu")

    clore_travaux_interrompus(orch)

    assert [c["status"] for c in store.list(tour)] == ["cancelled"]


def test_une_mission_n_est_jamais_close():
    from src.runtime.steering_reconciliation import clore_travaux_interrompus

    orch = TaskOrchestrator(persistence_path=None)
    mission = _tache(orch, "mission", conversation="__missions__")
    store = TaskSteeringStore(orch)
    store.enqueue(mission, "add_constraint", text="en vert")

    clore_travaux_interrompus(orch)

    assert _etat(orch, mission) == "checkpointed", (
        "une mission reprend reellement au demarrage (lot 0.d) : ne pas la tuer"
    )
    assert [c["status"] for c in store.list(mission)] == ["pending"]


def test_un_worker_de_mission_n_est_jamais_clos():
    from src.runtime.steering_reconciliation import clore_travaux_interrompus

    orch = TaskOrchestrator(persistence_path=None)
    lead = _tache(orch, "mission", conversation="__missions__")
    worker = _tache(orch, "mission", conversation="__missions__", parent=lead)

    clore_travaux_interrompus(orch)

    assert _etat(orch, worker) == "checkpointed", "un worker porte le meme kind que son lead"


def test_le_registre_ignore_un_tour_clos_meme_frachement_ecrit():
    """LE test qui aurait attrape l'erreur : l'etat prime sur l'horodatage."""
    from src.runtime.steering_reconciliation import clore_travaux_interrompus

    orch = TaskOrchestrator(persistence_path=None)
    tour = _tache(orch, "agent_turn")
    TaskSteeringStore(orch).enqueue(tour, "add_constraint", text="en bleu")

    clore_travaux_interrompus(orch)

    # La cloture vient d'ecrire : `updated_at` est donc POSTERIEUR au demarrage
    # du processus. Un filtre fonde sur la fraicheur laisserait passer la tache.
    assert ActiveWorkRegistry(orch, owner_user_id=OWNER).active_ids() == [], (
        "le tour clos est encore presente comme travail actif : le composer "
        "affichera « Lumena travaille » sans travail"
    )


def test_deux_demarrages_de_suite_ne_changent_rien_de_plus():
    from src.runtime.steering_reconciliation import clore_travaux_interrompus

    orch = TaskOrchestrator(persistence_path=None)
    _tache(orch, "agent_turn")
    premier = clore_travaux_interrompus(orch)
    second = clore_travaux_interrompus(orch)

    assert premier["taches"], "le premier passage doit clore quelque chose"
    assert second == {"taches": [], "orientations": []}, (
        "un second demarrage ne doit rien re-cloturer"
    )


def test_une_tache_deja_terminale_n_est_pas_retouchee():
    from src.runtime.steering_reconciliation import clore_travaux_interrompus

    orch = TaskOrchestrator(persistence_path=None)
    fini = _tache(orch, "agent_turn")
    orch.mark_done(fini, result_summary="chat_done")

    clore_travaux_interrompus(orch)

    assert _etat(orch, fini) == "done", "une tache terminee ne doit pas devenir annulee"


def test_une_orientation_deja_terminale_n_est_pas_recomptee():
    """Recuperé de la suite ORI-2b, que ce fichier remplace."""
    from src.runtime.steering_reconciliation import clore_travaux_interrompus

    orch = TaskOrchestrator(persistence_path=None)
    tour = _tache(orch, "agent_turn")
    store = TaskSteeringStore(orch)
    commande = store.enqueue(tour, "add_constraint", text="deja traitee")
    store.transition(tour, commande["command_id"], "cancelled")

    resultat = clore_travaux_interrompus(orch)

    assert resultat["orientations"] == [], (
        "une orientation deja terminale ne doit pas etre comptee une seconde fois"
    )
    assert resultat["taches"] == [tour], "le tour lui-meme reste a clore"
