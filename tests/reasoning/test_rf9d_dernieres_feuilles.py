"""RF-9d — les dernières feuilles pures de `_run_internal`.

Fin de RF-9. Après RF-9a/b/c, l'audit AST au détecteur STRICT ne trouve plus
que 19 feuilles pures, la meilleure rendant 2 locales. Ce lot prend les quatre
qui sont de vraies décisions, et laisse celles qui n'en sont pas.

--- Ce que ce lot NE prend PAS, et pourquoi ---

**l. 7481** (`_cmd_str`, `_exit_code`) — le bloc MUTE un dictionnaire local
(`_led_meta["command"] = ...`) et son parsing est **déjà délégué** à
`test_proof.parse_test_outcome`. Il ne reste aucune décision à en sortir : ce
serait une enveloppe autour d'un appel existant.

--- Les quatre décisions extraites ---

    plan_stagnation_message(task_plan)                 -> str
    web_files_reminder(written_paths)                  -> str
    phantom_channels(claim_channels, actual_channels)  -> set
    workspace_path_from_query(query, root)             -> Optional[str]

Toutes rendent une valeur. Les mutations restent dans la boucle : la
reconstruction de l'`Observation`, le compteur de retry, l'affectation de
`_esc_project_path`.

--- Le détecteur s'est trompé TROIS fois pendant RF-9 ---

    version 1 :  91 feuilles « pures »
    version 2 :  39   — il ignorait les E/S (`open`, `makedirs`) et les logs
    version 3 :  19   — il ignorait les attributs IMBRIQUÉS
                        (`self.tools._v2_context.x = ...`) et les appels de
                        méthodes d'instance

Deux feuilles classées « pures » écrivaient un fichier sur disque ; deux autres
mutaient un contexte externe. Ce lot repose sur la version 3.
"""

from __future__ import annotations

import ast
import inspect
import pathlib

import pytest


RACINE = pathlib.Path(__file__).resolve().parents[2]
REACT = RACINE / "src" / "reasoning" / "react.py"
MODULE = RACINE / "src" / "reasoning" / "observation_synthesis.py"

FEUILLES = (
    "plan_stagnation_message",
    "web_files_reminder",
    "phantom_channels",
    "workspace_path_from_query",
)


def _run_internal():
    arbre = ast.parse(REACT.read_text(encoding="utf-8"))
    cls = next(n for n in arbre.body
               if isinstance(n, ast.ClassDef) and n.name == "ReActLoop")
    return next(n for n in cls.body if getattr(n, "name", "") == "_run_internal")


class _Tache:
    def __init__(self, description, completed=False):
        self.description = description
        self.completed = completed


# ══════════════════════════════════════════════════════════════════════════
#  1. Les quatre decisions existent et sont PURES
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("nom", FEUILLES)
def test_la_decision_existe(nom):
    import src.reasoning.observation_synthesis as m

    assert callable(getattr(m, nom, None)), f"{nom} n'existe pas"


@pytest.mark.parametrize("nom", FEUILLES)
def test_la_decision_ne_MUTE_rien(nom):
    arbre = ast.parse(MODULE.read_text(encoding="utf-8"))
    fn = next(n for n in arbre.body
              if isinstance(n, ast.FunctionDef) and n.name == nom)
    interdits = []
    for x in ast.walk(fn):
        if isinstance(x, (ast.Await, ast.Global, ast.Nonlocal)):
            interdits.append(type(x).__name__)
        if isinstance(x, (ast.Attribute, ast.Subscript)) and isinstance(x.ctx, ast.Store):
            interdits.append("mutation")
        if isinstance(x, ast.Call) and getattr(x.func, "attr", "") in (
                "debug", "info", "warning", "error", "append", "add"):
            # `append`/`add` sur une liste LOCALE sont admis ; on ne vise que
            # les mutations d'objets recus en parametre.
            base = getattr(x.func, "value", None)
            if isinstance(base, ast.Name) and base.id in {
                a.arg for a in fn.args.args
            }:
                interdits.append("mutation du parametre " + base.id)
    assert not interdits, f"{nom} n'est pas pure : {sorted(set(interdits))}"


# ══════════════════════════════════════════════════════════════════════════
#  2. Message de stagnation de plan
# ══════════════════════════════════════════════════════════════════════════


def test_le_message_de_stagnation_nomme_la_prochaine_tache():
    from src.reasoning.observation_synthesis import plan_stagnation_message

    msg = plan_stagnation_message([
        _Tache("ecrire le module", completed=True),
        _Tache("publier le livrable"),
    ])
    assert "Aucune progression" in msg
    assert "PROCHAINE TACHE A FAIRE: publier le livrable" in msg


def test_sans_tache_restante_le_message_reste_generique():
    """Toutes les taches faites : on avertit, mais on ne nomme rien."""
    from src.reasoning.observation_synthesis import plan_stagnation_message

    msg = plan_stagnation_message([_Tache("tout est fait", completed=True)])
    assert "Aucune progression" in msg
    assert "PROCHAINE TACHE" not in msg


def test_un_plan_vide_ne_nomme_aucune_tache():
    from src.reasoning.observation_synthesis import plan_stagnation_message

    assert "PROCHAINE TACHE" not in plan_stagnation_message([])


def test_le_message_propose_TOUJOURS_la_sortie_honnete():
    """« ou termine avec FINAL si la tache est impossible » — sans cette porte,
    le garde enfermerait le modele dans une boucle qu'il ne peut pas finir."""
    from src.reasoning.observation_synthesis import plan_stagnation_message

    assert "FINAL si la tache est impossible" in plan_stagnation_message([])


# ══════════════════════════════════════════════════════════════════════════
#  3. Rappel des fichiers web
# ══════════════════════════════════════════════════════════════════════════


def test_le_rappel_liste_les_fichiers_ecrits():
    from src.reasoning.observation_synthesis import web_files_reminder

    r = web_files_reminder(["index.html", "style.css"])
    assert "index.html" in r and "style.css" in r


@pytest.mark.parametrize("ecrits,manquant", [
    ([], "index.html"),
    (["index.html"], "style.css"),
    (["index.html", "style.css"], "script.js"),
])
def test_le_rappel_nomme_ce_qui_MANQUE(ecrits, manquant):
    from src.reasoning.observation_synthesis import web_files_reminder

    assert manquant in web_files_reminder(ecrits)


def test_rien_ne_manque_quand_les_trois_sont_la():
    from src.reasoning.observation_synthesis import web_files_reminder

    r = web_files_reminder(["a/index.html", "b/style.css", "c/script.js"])
    ligne = [l for l in r.splitlines() if "manquants" in l][0]
    assert ligne.strip().endswith("manquants:"), f"il manque encore quelque chose : {ligne!r}"


def test_sans_aucun_fichier_le_rappel_le_dit():
    from src.reasoning.observation_synthesis import web_files_reminder

    assert "Aucun" in web_files_reminder([])


# ══════════════════════════════════════════════════════════════════════════
#  4. Salons fantomes — le garde qui compte
# ══════════════════════════════════════════════════════════════════════════


def test_un_salon_INVENTE_est_detecte():
    """DISCORD COUNT GUARD : le FINAL pretend avoir poste dans un salon ou
    aucun envoi n'a reussi."""
    from src.reasoning.observation_synthesis import phantom_channels

    assert phantom_channels(["#general", "#annonces"], {"general"}) == {"annonces"}


def test_aucun_fantome_quand_tout_correspond():
    from src.reasoning.observation_synthesis import phantom_channels

    assert phantom_channels(["#general"], {"general"}) == set()


def test_le_diese_et_la_casse_sont_normalises():
    from src.reasoning.observation_synthesis import phantom_channels

    assert phantom_channels(["#General ", "ANNONCES"], {"general", "annonces"}) == set()


def test_sans_revendication_il_n_y_a_pas_de_fantome():
    from src.reasoning.observation_synthesis import phantom_channels

    assert phantom_channels([], {"general"}) == set()


# ══════════════════════════════════════════════════════════════════════════
#  5. Chemin de workspace extrait de la requete
# ══════════════════════════════════════════════════════════════════════════


def test_un_chemin_absolu_est_extrait(tmp_path):
    from src.reasoning.observation_synthesis import workspace_path_from_query

    d = tmp_path / "workspace" / "monprojet"
    d.mkdir(parents=True)
    q = f"reprends le travail dans {d}"
    assert workspace_path_from_query(q, str(tmp_path)) == str(d)


def test_un_chemin_relatif_est_resolu_sur_la_racine(tmp_path):
    from src.reasoning.observation_synthesis import workspace_path_from_query

    (tmp_path / "workspace" / "monprojet").mkdir(parents=True)
    got = workspace_path_from_query("continue workspace/monprojet", str(tmp_path))
    assert got is not None and got.endswith("monprojet")


def test_un_dossier_INEXISTANT_n_est_PAS_rendu(tmp_path):
    """Le garde : on ne rend qu'un chemin qui EXISTE sur le disque."""
    from src.reasoning.observation_synthesis import workspace_path_from_query

    assert workspace_path_from_query("continue workspace/jamais-cree", str(tmp_path)) is None


def test_une_requete_sans_chemin_rend_None(tmp_path):
    from src.reasoning.observation_synthesis import workspace_path_from_query

    assert workspace_path_from_query("bonjour comment vas-tu", str(tmp_path)) is None


# ══════════════════════════════════════════════════════════════════════════
#  6. Les MUTATIONS restent dans la boucle — §15
# ══════════════════════════════════════════════════════════════════════════


def test_la_reconstruction_de_l_observation_reste_dans_la_boucle():
    src = REACT.read_text(encoding="utf-8")
    i = src.index("plan_stagnation_message(")
    bloc = src[i:i + 700]
    assert "Observation(" in bloc, "la reconstruction a quitte la boucle"


def test_le_compteur_de_retry_reste_dans_la_boucle():
    src = REACT.read_text(encoding="utf-8")
    i = src.index("phantom_channels(")
    bloc = src[i:i + 900]
    assert "_premature_final_retries" in bloc, (
        "le compteur de retry a quitte la boucle"
    )


def test_le_squelette_de_la_boucle_est_INTACT():
    ri = _run_internal()
    conts = sum(1 for x in ast.walk(ri) if isinstance(x, ast.Continue))
    rets = sum(1 for x in ast.walk(ri) if isinstance(x, ast.Return))
    trys = sum(1 for x in ast.walk(ri) if isinstance(x, ast.Try))
    # 77 -> 78 : lot panel missions 14. La pensee du LEAD est parsee dans
    # cette boucle depuis toujours et n'etait ecrite qu'au log `debug` : la
    # carte « Lead » du panneau Missions restait vide sur TOUTES les
    # missions. L'emission ajoutee est defensive (le bus de trace ne doit
    # jamais faire tomber la boucle), d'ou un `try` de plus. Elle n'ajoute
    # ni `continue`, ni `return`, ni import local.
    assert (conts, rets, trys) == (48, 33, 78), (
        f"le squelette a bouge : continue={conts} return={rets} try={trys}"
    )


# ══════════════════════════════════════════════════════════════════════════
#  7. LE critere du §15 — clotures
# ══════════════════════════════════════════════════════════════════════════


def test_les_locales_des_feuilles_ont_DISPARU():
    ri = _run_internal()
    locales = {x.id for x in ast.walk(ri)
               if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Store)}
    # `plan_stag_msg` et `_phantom` RECOIVENT la decision : ils restent.
    # `has_html/css/js` sont relus ~700 lignes plus bas : ils restent aussi,
    # RENDUS par `web_files_present` et non absorbes.
    for mort in ("next_task", "created_files", "_claimed_set", "_cand", "_esc_qm"):
        assert mort not in locales, (
            f"`{mort}` vit encore dans la boucle : la feuille n'a pas emporte "
            f"son etat"
        )


def test_l_etat_partage_a_DIMINUE():
    ri = _run_internal()
    locales = {x.id for x in ast.walk(ri)
               if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Store)}
    # Le cliquet monte de DEUX le 2026-08-29, avec sa raison : le correctif du
    # FINAL perdu introduit `_repare` (le contenu rendu par la reparation) et
    # `_garde` (la reponse d'origine sauvegardee). Toutes deux vivent sur
    # quelques lignes ; elles reparent une perte de reponse VISIBLE par
    # l'utilisateur. Le cliquet sert a empecher la REPOUSSE silencieuse de
    # l'etat partage, pas a interdire une correction — mais il ne doit jamais
    # bouger sans que la raison soit ecrite ici.
    assert len(locales) <= 641, (
        f"{len(locales)} locales — RF-9c en avait laisse 644, ces quatre "
        f"feuilles doivent en emporter cinq (plafond releve a 641 le 2026-08-29)"
    )
