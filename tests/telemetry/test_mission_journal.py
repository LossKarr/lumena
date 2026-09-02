"""Journal de mission — lot A : ce qui reste quand la mission est finie.

═══════════════════════════════════════════════════════════════════════════════
  CE QUI A ETE MESURE AVANT D'ECRIRE UNE LIGNE
═══════════════════════════════════════════════════════════════════════════════

Corpus reel, `data/task_orchestrator_state.json`, 670 taches :

    taches gardant une trace du raisonnement  ......  0 / 670
    taches avec `checkpoint_history`  .............  669 / 670  (max 40 entrees)

`checkpoint_history` garde la charpente — phase, iteration, ledger — jamais le
raisonnement. Celui-ci ne vivait que sur le flux SSE et dans l'anneau serveur
de 500 evenements : perdu au redemarrage, perdu des que l'anneau tourne.

Taille attendue, mesuree sur les ledgers reels (mediane 6 actions par tache,
p90 24, max 166) : ~3 Ko, 13 Ko et 89 Ko. L'etat persiste pese deja 11,9 Mo —
le journal complet de toutes les taches mesurees en pese 3,8.

═══════════════════════════════════════════════════════════════════════════════
  CE QUE CES TESTS PROTEGENT VRAIMENT
═══════════════════════════════════════════════════════════════════════════════

Trois classes, par ordre de gravite :

  1. LA TRAVERSEE DE CHEMIN. Un `task_id` vient du reseau et sert de nom de
     fichier. Le lot Z40a de ce depot etait exactement ce defaut. La porte est
     une liste blanche : un identifiant douteux est REFUSE, jamais nettoye.

  2. LE SILENCE. Ce module est accroche au bus de traces, lui-meme sur le
     chemin de chaque iteration d'agent. Une erreur d'ecriture ne doit JAMAIS
     remonter : un disque plein ne tue pas une mission.

  3. LA BORNE. Sans elle, une mission emballee remplit le disque.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from src.telemetry import mission_journal as J


@pytest.fixture()
def racine(tmp_path, monkeypatch):
    """Isole le journal dans un dossier jetable, sans toucher aux donnees."""
    monkeypatch.setenv("LUMENA_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(J, "_racine", lambda: tmp_path / "missions")
    # Opt-in EXPLICITE : le conftest coupe le journal pour toute la suite
    # afin qu'aucun test n'ecrive dans les donnees reelles. Ici on le
    # rallume, et sa racine est deja detournee vers `tmp_path`.
    monkeypatch.setenv("LUMENA_MISSION_JOURNAL", "1")
    return tmp_path / "missions"


def _ev(**kw):
    b = {"task_id": "task_abc", "seq": 1, "ts": "2026-09-01T12:00:00Z",
         "stage": "codeagent_iteration", "status": "ok"}
    b.update(kw)
    return b


# ══════════════════════════════════════════════════════════════════════════
#  1. LA TRAVERSEE DE CHEMIN — la classe la plus grave
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("mauvais", [
    "../../etc/passwd",
    "..\\..\\windows\\system32",
    "task/../../secret",
    "a/b",
    "a\\b",
    "C:/absolu",
    "/absolu",
    "tache avec espaces",
    "point.point",
    "",
    "   ",
    "x" * 81,
])
def test_un_identifiant_DOUTEUX_est_refuse_pas_nettoye(racine, mauvais):
    """La difference entre une porte et un tamis : on refuse, on ne repare pas.

    Le lot Z40a de ce depot etait exactement une traversee de chemin."""
    assert J.chemin_journal(mauvais) is None
    assert J.grave(_ev(task_id=mauvais)) is False


def test_AUCUN_fichier_n_est_cree_hors_du_dossier(racine, tmp_path):
    """La preuve par le disque, pas par la valeur de retour."""
    for mauvais in ("../evade", "..\\evade", "a/b/c"):
        J.grave(_ev(task_id=mauvais))
    evades = [p for p in tmp_path.rglob("*") if p.is_file() and p.suffix == ".jsonl"
              and p.parent != racine]
    assert not evades, f"fichiers ecrits hors du dossier : {evades}"


@pytest.mark.parametrize("bon", ["task_abc", "task_" + "a1b2" * 8, "A-B_c9", "x"])
def test_un_identifiant_REEL_passe(racine, bon):
    """Les identifiants du runtime sont des `task_<hex32>`."""
    assert J.chemin_journal(bon) is not None
    assert J.grave(_ev(task_id=bon)) is True


def test_un_task_id_ABSENT_ne_grave_rien(racine):
    assert J.grave({"stage": "x"}) is False
    assert J.grave(_ev(task_id=None)) is False


# ══════════════════════════════════════════════════════════════════════════
#  2. LE SILENCE — jamais fatal, jamais bloquant
# ══════════════════════════════════════════════════════════════════════════


def test_un_disque_qui_refuse_n_emporte_PAS_la_mission(racine, monkeypatch):
    """Ce module est sur le chemin de chaque iteration d'agent."""
    def explose(*a, **k):
        raise OSError("disque plein")
    monkeypatch.setattr(Path, "open", explose)
    assert J.grave(_ev(thought="ça compte")) is False   # pas d'exception


def test_une_entree_qui_n_est_PAS_un_dict_ne_casse_rien(racine):
    for mauvais in (None, "texte", 42, [], object()):
        assert J.grave(mauvais) is False


def test_relire_un_journal_INEXISTANT_rend_une_liste_vide(racine):
    assert J.lis("task_jamais_vu") == []
    assert J.existe("task_jamais_vu") is False


def test_une_ligne_TRONQUEE_est_sautee_pas_fatale(racine):
    """Un arret brutal coupe la derniere ligne — c'est justement le moment ou
    l'on a le plus besoin du journal."""
    J.grave(_ev(seq=1, thought="premiere"))
    J.grave(_ev(seq=2, thought="deuxieme"))
    chemin = J.chemin_journal("task_abc")
    with chemin.open("a", encoding="utf-8") as f:
        f.write('{"seq": 3, "thought": "coupee au mil')
    lignes = J.lis("task_abc")
    assert len(lignes) == 2
    assert lignes[-1]["thought"] == "deuxieme"


def test_le_journal_se_COUPE_par_variable_d_environnement(racine, monkeypatch):
    monkeypatch.setenv("LUMENA_MISSION_JOURNAL", "0")
    assert J.actif() is False
    assert J.grave(_ev()) is False


def test_il_est_ACTIF_par_defaut(racine, monkeypatch):
    """« Tout doit etre enregistre » n'a pas de sens en option desactivee.

    Ce test efface la variable LUI-MEME : la suite entiere la force a « 0 »
    pour ne pas polluer les donnees reelles, donc verifier le defaut demande
    de la retirer explicitement."""
    monkeypatch.delenv("LUMENA_MISSION_JOURNAL", raising=False)
    assert J.actif() is True


# ══════════════════════════════════════════════════════════════════════════
#  3. CE QUI EST GRAVE, ET CE QUI NE L'EST PAS
# ══════════════════════════════════════════════════════════════════════════


def test_le_raisonnement_SURVIT(racine):
    """Le seul but du module."""
    pensee = ("Les cinq workers ont fini leurs fichiers mais aucun n’a lancé le "
              "serveur. J’intègre d’abord, je sers ensuite.")
    J.grave(_ev(thought=pensee, tool_name="serve_website", iteration=9, max_iter=20))
    l = J.lis("task_abc")[0]
    assert l["thought"] == pensee
    assert l["tool_name"] == "serve_website"
    assert l["iteration"] == 9 and l["max_iter"] == 20


def test_l_ordre_est_CHRONOLOGIQUE(racine):
    """On relit une mission dans le sens ou elle s'est deroulee."""
    for i in range(5):
        J.grave(_ev(seq=i, thought="etape %d" % i))
    assert [l["thought"] for l in J.lis("task_abc")] == ["etape %d" % i for i in range(5)]


def test_les_missions_ne_se_MELANGENT_pas(racine):
    J.grave(_ev(task_id="task_un", thought="UN"))
    J.grave(_ev(task_id="task_deux", thought="DEUX"))
    assert [l["thought"] for l in J.lis("task_un")] == ["UN"]
    assert [l["thought"] for l in J.lis("task_deux")] == ["DEUX"]


def test_les_champs_de_CONVERSATION_ne_sont_pas_graves(racine):
    """`trace_id`, `turn_id`, `request_id`, `conversation_id` identifient un
    tour de dialogue, pas un moment de mission — et le fichier est deja indexe
    par la mission."""
    J.grave(_ev(trace_id="t", turn_id="u", request_id="r", conversation_id="c",
                thought="utile"))
    l = J.lis("task_abc")[0]
    for parasite in ("trace_id", "turn_id", "request_id", "conversation_id"):
        assert parasite not in l, parasite
    assert l["thought"] == "utile"


def test_la_pensee_est_PLAFONNEE_comme_dans_le_direct(racine):
    """Un journal qui en dirait plus que l'ecran serait une seconde verite."""
    J.grave(_ev(thought="x" * 5000))
    assert len(J.lis("task_abc")[0]["thought"]) == 400


@pytest.mark.parametrize("champ,cap", [("summary", 300), ("error", 300)])
def test_les_autres_champs_longs_sont_plafonnes(racine, champ, cap):
    J.grave(_ev(**{champ: "y" * 4000}))
    assert len(J.lis("task_abc")[0][champ]) == cap


def test_les_champs_VIDES_ne_gonflent_pas_le_fichier(racine):
    J.grave(_ev(thought=None, summary="", error=None, tool_name=None))
    l = J.lis("task_abc")[0]
    assert "thought" not in l and "summary" not in l and "error" not in l


def test_un_evenement_SANS_contenu_utile_n_est_pas_grave(racine):
    """Un `task_id` seul ne raconte rien."""
    assert J.grave({"task_id": "task_abc"}) is False


def test_l_accentuation_survit_au_disque(racine):
    """Le raisonnement est en francais : un journal en `\\u00e9` serait illisible
    a la main, et ce fichier doit rester lisible a la main."""
    J.grave(_ev(thought="échéance dépassée — j’intègre"))
    brut = J.chemin_journal("task_abc").read_text(encoding="utf-8")
    assert "échéance dépassée" in brut
    assert "\\u00e9" not in brut


# ══════════════════════════════════════════════════════════════════════════
#  4. LA BORNE
# ══════════════════════════════════════════════════════════════════════════


def test_le_journal_TOURNE_au_lieu_de_gonfler(racine, monkeypatch):
    monkeypatch.setattr(J, "_MAX_OCTETS", 2000)
    for i in range(200):
        J.grave(_ev(seq=i, thought="ligne %03d " % i + "z" * 60))
    courant = J.chemin_journal("task_abc")
    tourne = courant.with_suffix(".1.jsonl")
    assert tourne.exists(), "aucune rotation : le fichier gonfle sans borne"
    assert courant.stat().st_size < 2000 + 500
    assert tourne.stat().st_size < 2000 + 500


def test_la_rotation_garde_au_plus_DEUX_fichiers(racine, monkeypatch):
    monkeypatch.setattr(J, "_MAX_OCTETS", 500)
    for i in range(300):
        J.grave(_ev(seq=i, thought="z" * 80))
    fichiers = sorted(p.name for p in racine.glob("task_abc*"))
    assert len(fichiers) == 2, fichiers


def test_apres_rotation_on_relit_les_DEUX_fichiers_dans_l_ordre(racine, monkeypatch):
    monkeypatch.setattr(J, "_MAX_OCTETS", 900)
    for i in range(60):
        J.grave(_ev(seq=i, thought="etape %02d " % i + "w" * 40))
    lignes = J.lis("task_abc")
    seqs = [l["seq"] for l in lignes]
    assert seqs == sorted(seqs), "la chronologie est rompue apres rotation"
    assert len(lignes) > 10
    assert J.stats("task_abc")["rotated"] is True


def test_la_relecture_est_BORNEE_et_garde_la_FIN(racine):
    """C'est la conclusion d'une mission qu'on vient chercher en premier."""
    for i in range(50):
        J.grave(_ev(seq=i, thought="etape %d" % i))
    lignes = J.lis("task_abc", limit=10)
    assert len(lignes) == 10
    assert lignes[-1]["thought"] == "etape 49"


def test_les_statistiques_ne_relisent_PAS_le_contenu(racine):
    J.grave(_ev(thought="x" * 300))
    s = J.stats("task_abc")
    assert s["exists"] is True and s["bytes"] > 0 and s["rotated"] is False
    assert J.stats("../evade") == {"exists": False, "bytes": 0, "rotated": False}


# ══════════════════════════════════════════════════════════════════════════
#  5. L'ACCROCHAGE — un seul site, et pas dans les fichiers a ecrivain unique
# ══════════════════════════════════════════════════════════════════════════


_TRACE = Path(__file__).parents[2] / "src" / "telemetry" / "trace_bus.py"
_REACT = Path(__file__).parents[2] / "src" / "reasoning" / "react.py"
_SUB = Path(__file__).parents[2] / "src" / "agents" / "sub_agent.py"


def test_le_journal_est_accroche_UNE_seule_fois():
    """`TraceBus.publish` est le point par lequel passe chaque evenement, y
    compris ceux dont le `task_id` vient du contexte. Un seul site suffit."""
    src = _TRACE.read_text(encoding="utf-8")
    assert src.count("mission_journal") == 1


def test_l_accrochage_est_APRES_le_fan_out(racine):
    """Un abonne vivant ne doit pas attendre le disque."""
    src = _TRACE.read_text(encoding="utf-8")
    assert src.index("stale_ids.append") < src.index("mission_journal")


def test_l_accrochage_ne_peut_PAS_faire_tomber_le_bus():
    src = _TRACE.read_text(encoding="utf-8")
    i = src.index("mission_journal")
    fenetre = src[max(0, i - 300):i + 200]
    assert "try:" in fenetre and "except Exception:" in fenetre


def test_react_et_sub_agent_ne_sont_PAS_touches():
    """Contrainte utilisateur permanente : un seul ecrivain a la fois sur ces
    fichiers. Accrocher le journal au bus les evite entierement."""
    for f in (_REACT, _SUB):
        assert "mission_journal" not in f.read_text(encoding="utf-8"), f.name


def test_le_journal_ne_vit_PAS_dans_l_espace_de_travail_publie(racine):
    """`publish_mission_workspace` livre le workspace au client : y ecrire de
    la telemetrie la livrerait avec le produit."""
    chemin = J.chemin_journal("task_abc")
    assert "workspace" not in str(chemin).lower()
    assert chemin.parent.name == "missions"


def test_l_import_est_PARESSEUX():
    """`trace_bus` est importe tres tot au demarrage : un cycle d'import y
    serait fatal."""
    src = J.__file__ and Path(J.__file__).read_text(encoding="utf-8")
    i = src.index("def _racine")
    assert "from src.utils.paths import DATA_DIR" in src[i:i + 300]
    entete = src[:src.index("def actif")]
    assert "from src.utils.paths" not in entete
