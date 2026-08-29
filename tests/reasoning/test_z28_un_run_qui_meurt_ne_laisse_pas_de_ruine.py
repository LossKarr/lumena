"""LOT Z28 — un run qui meurt ne laisse pas de ruines derrière lui.

Run « Papier Cousu » (2026-08-19). La mission a produit, en 9 minutes, un site
de 6 fichiers (74 ko), sombre, avec l'animation canvas et le README demandés,
et a ouvert les 3 pages au navigateur. Puis :

    18:04  ACTION: final ×4 → « THOUGHT leaké » 1/3, 2/3, 3/3
    18:04  état = FAILED — final_answer_potentially_incomplete

Elle est morte en laissant DEUX ruines, mesurées toutes les deux au log :

**A. un verdict qui contredit le disque.** Le site était complet ; Lumena a
annoncé un échec. Après 45 lots à l'empêcher d'affirmer un succès non prouvé,
c'est l'inverse — et c'est aussi destructeur : l'utilisateur a failli jeter un
site fini. Le remède existait déjà (lot I3) mais s'arrête aux WORKERS :
`_mission_worker_delivered()` sort sur `if not owned: return False`, et un lead
n'a jamais de fichiers assignés.

**B. un navigateur attaché à une boucle morte.**

    18:02:48  Playwright démarré avec profil 'lumena'   ← par la MISSION
    18:05     la mission meurt, personne n'arrête le navigateur
    18:07:56  browser_navigate depuis le chat
    18:11:56  sans réponse après 240s — reset BR-1
    18:11:56  Erreur arrêt Playwright: Future attached to a DIFFERENT LOOP

**4 minutes de gel.** BR-1 rattrape — c'est pourquoi la session a repris — mais
après coup.

⚠️ Le volet A a dû être corrigé PENDANT l'audit : `create_project` reçoit
`description`/`project_name`, jamais un `path`, donc `_extract_target` renvoie
None. Repérer le livrable par les seules cibles du ledger aurait rendu ce lot
INERTE sur le run qu'il doit sauver. Le chemin existe pourtant — il est dans
l'observation (« Projet créé via CodeAgent dans `…` ») — il n'était simplement
jamais rangé. C'est le motif du chantier, une fois de plus.
"""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.reasoning.react import ReActLoop
from src.runtime.execution_ledger import ExecutionLedger, _extract_proof
from src.tools import playwright_browser as _pb


_OBS_PROJET = (
    "OK Projet cree via CodeAgent dans `{}`\n\n"
    "- index.html : hero anime avec canvas\n"
    "- README.md : instructions de lancement"
)


# ══════════════════════════════════════════════════════════════════════════════
#  A — un livrable prouvé n'est pas un échec
# ══════════════════════════════════════════════════════════════════════════════


@pytest.fixture()
def livrable(tmp_path):
    """Le dossier tel qu'il est sur le disque après le run."""
    d = tmp_path / "papier-cousu"
    d.mkdir()
    for nom, taille in (("index.html", 7787), ("style.css", 29269),
                        ("script.js", 16751), ("README.md", 1520)):
        (d / nom).write_text("x" * taille, encoding="utf-8")
    return d


def _ledger_du_run(dossier) -> ExecutionLedger:
    led = ExecutionLedger()
    led.append(iteration=1, action="create_project", target=None, success=True,
               proof=_extract_proof("create_project",
                                    _OBS_PROJET.format(dossier.as_posix()), True))
    led.append(iteration=2, action="serve_website", target=None, success=True)
    return led


def _lead(ledger=None, *, mission=True, owned=(), meta=None):
    orch = SimpleNamespace(get_task=lambda _t: {"metadata": dict(meta or {})})
    return SimpleNamespace(
        _is_mission_run=mission,
        execution_ledger=ledger if ledger is not None else ExecutionLedger(),
        _mission_allowed_files_meta=lambda: list(owned),
        task_orchestrator=orch,
        task_id="task_4d9b96",
    )


def _delivered(agent):
    return ReActLoop._mission_lead_delivered(agent)


# ── Le chemin doit d'abord ARRIVER au ledger ─────────────────────────────────


def test_le_chemin_du_projet_est_desormais_range(livrable):
    """La correction trouvée en audit : sans elle, tout le volet A est inerte."""
    p = _extract_proof("create_project", _OBS_PROJET.format(livrable.as_posix()), True)
    assert p is not None
    assert livrable.as_posix() in p


@pytest.mark.parametrize("cas", [
    ("create_project", "Projet cree, sans aucun chemin.", True),
    ("create_project", "OK dans `C:/x/y`", False),
    ("browser_navigate", "OK dans `C:/x/y`", True),
])
def test_la_preuve_reste_conservatrice(cas):
    assert _extract_proof(*cas) is None


# ── Le cas mesuré ────────────────────────────────────────────────────────────


def test_le_livrable_du_run_est_reconnu(livrable):
    """LE lot : 4 fichiers sur le disque, la mission n'est plus un échec."""
    assert _delivered(_lead(_ledger_du_run(livrable))) == [str(livrable)]


def test_sans_mutation_l_echec_reste_reel():
    """La borne qui protège : rien n'a été fait → `failed` est la vérité."""
    led = ExecutionLedger()
    led.append(iteration=1, action="read_file", target="a.md", success=True)
    assert _delivered(_lead(led)) == []


def test_une_mutation_ratee_ne_prouve_rien(livrable):
    led = ExecutionLedger()
    led.append(iteration=1, action="create_project", target=str(livrable), success=False)
    assert _delivered(_lead(led)) == []


def test_un_dossier_vide_ne_compte_pas(tmp_path):
    """« Le dossier existe » ne suffit pas — D3 crée des arbres vides."""
    vide = tmp_path / "fantome"
    vide.mkdir()
    led = ExecutionLedger()
    led.append(iteration=1, action="create_project", target=str(vide), success=True)
    assert _delivered(_lead(led)) == []


def test_un_chemin_qui_n_existe_pas_ne_compte_pas():
    led = ExecutionLedger()
    led.append(iteration=1, action="write_file",
               target="/introuvable/vraiment/nulle-part.txt", success=True)
    assert _delivered(_lead(led)) == []


def test_le_workspace_de_mission_inexistant_est_ignore(livrable):
    """Mesuré : `mission_workspace` valait `missions/task_4d9b…`, un dossier qui
    n'a JAMAIS existé — le lead avait écrit ailleurs."""
    agent = _lead(_ledger_du_run(livrable),
                  meta={"mission_workspace": "missions/task_4d9b96_inexistant"})
    assert _delivered(agent) == [str(livrable)]


# ── Les bornes ───────────────────────────────────────────────────────────────


def test_hors_mission_inerte(livrable):
    assert _delivered(_lead(_ledger_du_run(livrable), mission=False)) == []


def test_un_worker_reste_a_I3(livrable):
    """Un worker a un périmètre : c'est I3 qui tranche, pas Z28. Deux juges sur
    le même run se contrediraient."""
    agent = _lead(_ledger_du_run(livrable), owned=["index.html"])
    assert _delivered(agent) == []


def test_ne_leve_jamais():
    assert _delivered(SimpleNamespace()) == []
    assert _delivered(SimpleNamespace(_is_mission_run=True)) == []
    casse = _lead(ExecutionLedger())
    casse.execution_ledger = SimpleNamespace(
        successful_mutations=lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    assert _delivered(casse) == []


# ── La réponse : des faits, pas la prose du modèle ───────────────────────────


def _reponse(agent, artefacts):
    return ReActLoop._truncated_but_delivered_answer(agent, artefacts)


def test_la_reponse_dit_que_c_est_la_CONCLUSION_qui_a_echoue(livrable):
    txt = _reponse(_lead(_ledger_du_run(livrable)), [str(livrable)])
    assert "tronquée" in txt
    assert "pas la mission" in txt


def test_la_reponse_nomme_les_fichiers_reellement_presents(livrable):
    txt = _reponse(_lead(_ledger_du_run(livrable)), [str(livrable)])
    for nom in ("index.html", "README.md", "style.css"):
        assert nom in txt


def test_la_reponse_porte_le_journal_d_execution(livrable):
    """Les faits du ledger, pas un résumé inventé."""
    txt = _reponse(_lead(_ledger_du_run(livrable)), [str(livrable)])
    assert "create_project" in txt


def test_la_reponse_ne_transmet_pas_la_pensee_qui_a_fuite(livrable):
    """LE point non négociable : à ce stade, `answer` contient la pensée
    leakée — parfois du charabia. La réponse se construit sans elle."""
    import inspect
    sig = inspect.signature(ReActLoop._truncated_but_delivered_answer)
    assert list(sig.parameters) == ["self", "artefacts"]


def test_la_reponse_ne_leve_jamais():
    agent = _lead(ExecutionLedger())
    assert _reponse(agent, []) != ""
    assert _reponse(agent, ["/chemin/qui/n/existe/pas"]) != ""


# ── Le branchement ───────────────────────────────────────────────────────────


# Lot RF-6a : le corps de cette methode — et sa docstring, qui PORTE la
# raison datee du lot — a ete deplace vers `mission_runtime.py`. Le test
# suit son texte ; son intention est inchangee, mot pour mot.
# La preuve COMPORTEMENTALE de l'extraction est la matrice RF-6a :
# 476 comparaisons valeur-par-valeur, 476 identiques.
_SRC = (Path("src/reasoning/react.py").read_text(encoding="utf-8")
        + Path("src/reasoning/mission_runtime.py").read_text(encoding="utf-8"))


def test_z28_est_branche_apres_I3_et_avant_le_mark_failed():
    i_i3 = _SRC.index("final_truncated_but_delivered")
    i_z28 = _SRC.index("_z28_artefacts = self._mission_lead_delivered()")
    i_fail = _SRC.index('_finish_iteration(status="error", error=self._run_meta["agent_output_warning"])')
    assert i_i3 < i_z28 < i_fail


def test_le_run_livre_ne_passe_plus_par_mark_task_failed():
    i_z28 = _SRC.index("_z28_artefacts = self._mission_lead_delivered()")
    bloc = _SRC[i_z28:i_z28 + 1200]
    i_ret = bloc.index("return self._truncated_but_delivered_answer(_z28_artefacts)")
    assert "_mark_task_failed" not in bloc[:i_ret]


def test_la_raison_du_lot_est_datee_dans_le_code():
    entete = _SRC[_SRC.index("LOT Z28 — le LEAD a-t-il produit"):][:2400]
    assert "Papier Cousu" in entete
    assert "create_project" in entete


# ══════════════════════════════════════════════════════════════════════════════
#  B — un navigateur ne survit pas à la boucle qui l'a créé
# ══════════════════════════════════════════════════════════════════════════════


class _FauxNavigateur:
    def __init__(self, loop=None):
        self._owner_loop = loop
        self.headless = True
        self.is_running = False
        self.stoppe = False

    async def stop(self):
        self.stoppe = True


def _boucle(*, closed=False, running=False):
    return SimpleNamespace(is_closed=lambda: closed, is_running=lambda: running)


def test_une_boucle_fermee_est_morte():
    assert _pb._owner_loop_is_dead(_FauxNavigateur(_boucle(closed=True))) is True


def test_une_boucle_arretee_est_morte():
    """Le cas du run « Papier Cousu » : la mission a fini, sa boucle ne tourne
    plus, mais l'objet boucle existe encore."""
    assert _pb._owner_loop_is_dead(_FauxNavigateur(_boucle(running=False))) is True


def test_une_boucle_VIVANTE_n_est_pas_morte():
    """LE garde anti-régression du lot. Une mission et le chat peuvent tourner
    en parallèle sur deux boucles vivantes : réinitialiser à chaque alternance
    les ferait s'entretuer. La condition est MORTE, jamais « autre »."""
    assert _pb._owner_loop_is_dead(_FauxNavigateur(_boucle(running=True))) is False


def test_sans_boucle_connue_on_ne_touche_a_rien():
    """Navigateur jamais démarré → comportement historique."""
    assert _pb._owner_loop_is_dead(_FauxNavigateur(None)) is False
    assert _pb._owner_loop_is_dead(SimpleNamespace()) is False


def test_ne_leve_jamais_sur_une_boucle_cassee():
    casse = _FauxNavigateur(SimpleNamespace(
        is_closed=lambda: (_ for _ in ()).throw(RuntimeError("x"))))
    assert _pb._owner_loop_is_dead(casse) is False


# ── L'effet sur le singleton ─────────────────────────────────────────────────


def test_le_navigateur_herite_d_une_boucle_morte_est_lache(monkeypatch):
    perime = _FauxNavigateur(_boucle(closed=True))
    monkeypatch.setattr(_pb, "_playwright_browser", perime, raising=False)
    neuf = _pb.get_playwright_browser(headless=True)
    assert neuf is not perime


def test_le_navigateur_d_une_boucle_vivante_est_conserve(monkeypatch):
    vivant = _FauxNavigateur(_boucle(running=True))
    monkeypatch.setattr(_pb, "_playwright_browser", vivant, raising=False)
    assert _pb.get_playwright_browser(headless=True) is vivant


def test_on_n_attend_JAMAIS_l_instance_perimee(monkeypatch):
    """C'est l'attente qui a coûté 240 s. On lâche la référence, point."""
    perime = _FauxNavigateur(_boucle(closed=True))
    monkeypatch.setattr(_pb, "_playwright_browser", perime, raising=False)
    _pb.get_playwright_browser(headless=True)
    assert perime.stoppe is False


def test_aucun_await_dans_le_chemin_de_liberation():
    src = Path("src/tools/playwright_browser.py").read_text(encoding="utf-8")
    i = src.index("if _playwright_browser is not None and _owner_loop_is_dead(")
    # On juge le CODE, pas les commentaires (qui, eux, parlent de l'attente).
    code = [l.split("#", 1)[0] for l in src[i:i + 700].splitlines()]
    assert not any("await" in l for l in code)


def test_la_boucle_proprietaire_est_memorisee_au_demarrage():
    src = Path("src/tools/playwright_browser.py").read_text(encoding="utf-8")
    i_set = src.index("self._owner_loop = asyncio.get_running_loop()")
    i_start = src.index("self._start_inner(), timeout=BROWSER_START_TIMEOUT_S")
    assert i_set < i_start, "la boucle doit être retenue AVANT le démarrage"


def test_la_raison_du_volet_B_est_datee_dans_le_code():
    src = Path("src/tools/playwright_browser.py").read_text(encoding="utf-8")
    entete = src[src.index("LOT Z28 — la boucle qui possède"):][:1600]
    assert "18:11:56" in entete
    assert "DIFFERENT LOOP" in entete
