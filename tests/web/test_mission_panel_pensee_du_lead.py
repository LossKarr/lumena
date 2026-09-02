"""Panel Missions — lot 14 : LA PENSEE DU LEAD N'ATTEIGNAIT PAS L'ECRAN.

La carte « Lead » de l'Atelier etait vide sur TOUTES les missions, depuis le
premier jour du chantier. Pas par manque de donnee : la pensee du lead est
parsee a chaque iteration de la boucle ReAct (`thought, action =
self._parse_response(response)`) et ecrite au log en `debug`. Elle n'etait
simplement jamais publiee sur le flux que le panneau ecoute.

Troisieme occurrence du meme motif dans la seule soiree :

    le ledger          etait sur le disque ET transmis  -> la vue disait « non expose »
    l'etat propre      calcule trois lignes plus haut   -> jamais passe au decideur
    la pensee du lead  parsee ET loguee                 -> jamais publiee

Le correctif est UNE emission, calquee sur celle du CodeAgent (lot 0.b) : meme
`stage`, meme `task_id`, meme plafond de 400 caracteres — pour que le modele du
panneau n'ait rien de special a faire. `react.py` n'est pas refactorise : un
appel est ajoute a cote de la ligne qui parse deja la pensee.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).parents[2]
_JS = _ROOT / "web" / "static" / "js"
_REACT = _ROOT / "src" / "reasoning" / "react.py"
_SUB = _ROOT / "src" / "agents" / "sub_agent.py"

# Les tests de source lisent des fichiers ; ceux de la section 3 ont besoin
# de node. Le marqueur est pose sur le MODULE : une suite a moitie verte
# sans node dirait moins que rien.
pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                               reason="node indisponible")


def _source_react() -> str:
    """Sans les commentaires : le piege de la sous-chaine m'a pris six fois."""
    src = _REACT.read_text(encoding="utf-8")
    return re.sub(r"^\s*#.*$", "", src, flags=re.M)


# ══════════════════════════════════════════════════════════════════════════
#  1. LE LEAD PUBLIE — au bon endroit, sous garde
# ══════════════════════════════════════════════════════════════════════════


def test_la_pensee_du_lead_est_PUBLIEE_et_pas_seulement_loguee():
    src = _source_react()
    i = src.index("thought, action = self._parse_response(response)")
    bloc = src[i:i + 1600]
    assert "publish_trace(" in bloc, (
        "la pensee du lead est parsee ici et n'atteint jamais le flux : la "
        "carte « Lead » du panneau reste vide sur toutes les missions"
    )
    assert "thought=" in bloc


def test_l_emission_est_juste_APRES_le_parsing():
    """Plus loin, elle pourrait etre sautee par un `continue` ou un `return`."""
    src = _source_react()
    i = src.index("thought, action = self._parse_response(response)")
    j = src.index("publish_trace(", i)
    assert j - i < 700, f"{j - i} caracteres entre le parsing et l'emission"


def test_rien_n_est_emis_HORS_mission():
    """Sans `task_id` il n'y a personne pour ecouter, et le panneau jette de
    toute facon les evenements qui n'en ont pas."""
    src = _source_react()
    i = src.index("thought, action = self._parse_response(response)")
    j = src.index("publish_trace(", i)
    garde = src[i:j]
    assert "self.task_id" in garde, "on emet meme hors mission"
    assert "TELEMETRY_AVAILABLE" in garde


def test_l_emission_ne_peut_PAS_faire_tomber_la_boucle():
    """Le bus de trace est un confort ; la boucle ReAct est le produit."""
    src = _source_react()
    i = src.index("thought, action = self._parse_response(response)")
    j = src.index("publish_trace(", i)
    assert "try:" in src[i:j]
    assert "except Exception:" in src[j:j + 700]


def test_la_pensee_est_PLAFONNEE_comme_celle_du_CodeAgent():
    """400 caracteres : un raisonnement de 8 ko sature le flux SSE."""
    src = _source_react()
    i = src.index("thought, action = self._parse_response(response)")
    assert "[:400]" in src[i:i + 1600]


def test_react_py_n_a_PAS_ete_refactorise():
    """Contrainte utilisateur permanente. Ce lot ajoute un appel, point."""
    src = _REACT.read_text(encoding="utf-8")
    assert src.count("thought, action = self._parse_response(response)") == 1
    assert "for i in range(self.max_iterations):" in src


# ══════════════════════════════════════════════════════════════════════════
#  2. MEME FORME QUE LE CODEAGENT — pour que le panneau n'ait rien a savoir
# ══════════════════════════════════════════════════════════════════════════


def test_le_lead_et_le_CODEAGENT_parlent_la_meme_langue():
    """Si les deux `stage` divergeaient, le modele du panneau devrait
    apprendre un deuxieme dialecte pour rien."""
    lead = _source_react()
    i = lead.index("thought, action = self._parse_response(response)")
    assert 'stage="codeagent_iteration"' in lead[i:i + 1600]
    sub = re.sub(r"^\s*#.*$", "", _SUB.read_text(encoding="utf-8"), flags=re.M)
    assert 'stage="codeagent_iteration"' in sub


@pytest.mark.parametrize("champ", ["task_id=", "thought=", "iteration=", "max_iter="])
def test_l_emission_porte_les_memes_CHAMPS(champ):
    src = _source_react()
    i = src.index("thought, action = self._parse_response(response)")
    assert champ in src[i:i + 1600], champ


def test_l_iteration_est_comptee_a_partir_de_UN():
    """`i` est indexe a zero ; un humain lit « itération 1/12 », pas 0/12."""
    src = _source_react()
    i = src.index("thought, action = self._parse_response(response)")
    assert "iteration=i + 1" in src[i:i + 1600]


# ══════════════════════════════════════════════════════════════════════════
#  3. BOUT EN BOUT — l'evenement arrive-t-il vraiment sur la carte Lead ?
# ══════════════════════════════════════════════════════════════════════════
#
#  C'est la seule verification qui compte : les tests ci-dessus prouvent que
#  l'emission existe, celui-ci prouve qu'elle SERT.


def _rendu(corps: str):
    script = (
        "require(%s);require(%s);" % tuple(
            json.dumps(str(_JS / n)) for n in ("mission_model.js", "mission_views.js"))
        + "const B=globalThis.buildMissionModel, V=globalThis.missionRenderView;"
        + "process.stdout.write(JSON.stringify((function(){%s})()));" % corps
    )
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True,
                       encoding="utf-8", cwd=str(_ROOT), timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


_ARBRE = [{"mission": {"task_id": "mission_1", "state": "running",
                       "metadata": {"objective": "Construis le site"}},
           "children": [{"mission": {"task_id": "w1", "state": "running",
                                     "metadata": {"objective": "[Worker api] x"}},
                         "children": []}]}]
_PENSEE = ("Les cinq workers ont fini leurs fichiers mais aucun n’a lancé le serveur. "
           "J’intègre d’abord, je sers ensuite, et je ne conclus qu’après avoir vu la "
           "page — un site qui n’a jamais été ouvert n’est pas un site vérifié.")


def test_l_evenement_du_lead_remplit_la_pensee_de_la_MISSION():
    """Le modele indexe par `task_id` : l'evenement du lead porte celui de la
    mission, donc il atterrit sur la mission elle-meme."""
    ev = [{"task_id": "mission_1", "stage": "codeagent_iteration",
           "thought": _PENSEE, "iteration": 4, "max_iter": 12,
           "tool_name": "serve_website"}]
    m = _rendu("return B(%s, %s, 0)[0];" % (json.dumps(_ARBRE), json.dumps(ev)))
    assert m["thought"] == _PENSEE
    assert m["iteration"] == 4 and m["maxIter"] == 12


def test_la_carte_LEAD_affiche_enfin_quelque_chose():
    """Le defaut visible : elle disait « aucun raisonnement transmis » sur
    toutes les missions, y compris quand le lead reflechissait."""
    ev = [{"task_id": "mission_1", "stage": "codeagent_iteration",
           "thought": _PENSEE, "iteration": 4, "max_iter": 12}]
    h = _rendu("const m=B(%s, %s, 0);return V('workshop', m, null);"
               % (json.dumps(_ARBRE), json.dumps(ev)))
    i = h.index(">Lead<")
    carte = h[h.rindex("<article", 0, i):h.index("</article>", i)]
    assert "n’a lancé le serveur" in carte, "la pensee du lead n'arrive pas sur sa carte"
    assert "aucun raisonnement transmis" not in carte


def test_la_pensee_du_lead_ne_DEBORDE_pas_sur_les_workers():
    """Deux emetteurs, deux `task_id` : rien ne doit se melanger."""
    ev = [{"task_id": "mission_1", "stage": "codeagent_iteration", "thought": "LEAD"},
          {"task_id": "w1", "stage": "codeagent_iteration", "thought": "WORKER"}]
    m = _rendu("return B(%s, %s, 0)[0];" % (json.dumps(_ARBRE), json.dumps(ev)))
    assert m["thought"] == "LEAD"
    assert m["children"][0]["thought"] == "WORKER"


def test_sans_evenement_la_carte_Lead_dit_toujours_son_SILENCE():
    """La degradation gracieuse ne doit pas disparaitre avec le correctif :
    un backend pas encore a jour n'emettra rien."""
    h = _rendu("const m=B(%s, [], 0);return V('workshop', m, null);" % json.dumps(_ARBRE))
    assert "aucun raisonnement transmis" in h


def test_la_pensee_du_lead_est_ECHAPPEE():
    ev = [{"task_id": "mission_1", "stage": "codeagent_iteration",
           "thought": "<script>alert(1)</script>"}]
    h = _rendu("const m=B(%s, %s, 0);return V('workshop', m, null);"
               % (json.dumps(_ARBRE), json.dumps(ev)))
    assert "<script>" not in h and "&lt;script&gt;" in h
