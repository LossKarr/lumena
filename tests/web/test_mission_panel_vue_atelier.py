"""Panel Missions — lot 2 : la vue « Atelier », et l'identite visuelle.

La vue C repond au manque du panneau actuel : il n'affichait que de la
telemetrie (`llm_request_start`, `agent_iteration_done · 17646ms`), jamais ce
que l'agent cherchait a faire. Ici la PENSEE est l'element principal.

--- Deux invariants verrouilles ici ---

1. **Purete.** Les vues ne touchent ni au DOM ni a `panels.js`. C'est ce qui les
   rend executables par node depuis pytest — et ce qui empeche le coeur de se
   reemmeler dans le fichier de 7 400 lignes.

2. **Identite.** Aucune couleur en dur dans la feuille du panneau : tout vient
   de `tokens.css`. C'est ce qui fait que le panneau suit le theme clair et le
   theme sombre de Lumena sans une ligne de plus. Le test ci-dessous fait
   rougir la suite si une valeur litterale reapparait — l'identite ne peut plus
   deriver par accident.
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
_VIEWS = _JS / "mission_views.js"
_MODEL = _JS / "mission_model.js"
_TREE = _JS / "mission_tree.js"
_CSS = _ROOT / "web" / "static" / "css" / "mission-panel.css"
_TOKENS = _ROOT / "web" / "static" / "css" / "tokens.css"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node indisponible")


def _rendu(missions_json: str, events_json: str = "[]", prefs: str = "null",
           now: str = "0") -> str:
    script = (
        f"const V = require({json.dumps(str(_VIEWS))});"
        f"const M = require({json.dumps(str(_MODEL))});"
        f"const T = require({json.dumps(str(_TREE))});"
        f"const t = T.buildMissionTree({missions_json});"
        f"const m = M.buildModel(t, {events_json}, {now});"
        f"process.stdout.write(V.renderWorkshop(m, {prefs}));"
    )
    # `text=True` seul decode avec le codec de la console Windows (cp1252) et
    # massacre les accents : « serialise » devenait « s?rialis? ». On impose UTF-8.
    res = subprocess.run(["node", "-e", script], capture_output=True, text=True,
                         encoding="utf-8", cwd=str(_ROOT), timeout=30)
    assert res.returncode == 0, f"node a echoue: {res.stderr or res.stdout}"
    return res.stdout


_LEAD = ("{task_id:'lead',state:'running',metadata:{objective:'Construis LogTriage',"
         "deadline_ts:'2026-08-29T12:00:00'}}")
_W1 = ("{task_id:'w1',state:'running',metadata:{parent_id:'lead',"
       "objective:'[Worker w_ia] contrat',allowed_files:['modele.py']}}")
_W2 = ("{task_id:'w2',state:'running',metadata:{parent_id:'lead',"
       "objective:'[Worker w_api] contrat',allowed_files:['api.py']}}")
_TOUS = f"[{_LEAD},{_W1},{_W2}]"
_EV = ("[{task_id:'w1',stage:'codeagent_iteration',thought:'Le lissage de Laplace.',"
       "iteration:6,max_iter:12,tool_name:'edit_lines'},"
       "{task_id:'w2',stage:'codeagent_wait_start'}]")


# ══════════════════════════════════════════════════════════════════════════
#  1. Purete des vues
# ══════════════════════════════════════════════════════════════════════════


def test_la_vue_est_du_javascript_valide():
    res = subprocess.run(["node", "--check", str(_VIEWS)], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr


@pytest.mark.parametrize("interdit", [
    "document.", "window.", "querySelector", "addEventListener", "localStorage",
])
def test_la_vue_ne_touche_AUCUN_dom(interdit):
    assert interdit not in _VIEWS.read_text(encoding="utf-8")


def test_la_vue_ne_depend_de_RIEN():
    """Notamment pas de panels.js : le coeur doit rester extractible.

    On retire les COMMENTAIRES avant de chercher : la premiere version de ce
    test trouvait le mot « panels » dans le commentaire du fichier qui explique
    justement qu'il n'en depend pas. Piege de la sous-chaine, deja rencontre
    quatre fois sur ce depot.
    """
    src = _VIEWS.read_text(encoding="utf-8")
    code = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    code = re.sub(r"^\s*//.*$", "", code, flags=re.M)
    assert "require(" not in code and "import " not in code
    assert "panels" not in code


# ══════════════════════════════════════════════════════════════════════════
#  2. La pensee est affichee — la raison d'etre du chantier
# ══════════════════════════════════════════════════════════════════════════


def test_LA_PENSEE_apparait_dans_le_rendu():
    assert "Le lissage de Laplace." in _rendu(_TOUS, _EV)


def test_un_worker_SANS_pensee_le_dit_au_lieu_de_laisser_un_vide():
    """Le libelle a ete RACCOURCI (lot 11) : vu au navigateur, cinq cartes sur
    onze etaient des placeholders aussi hauts qu'un vrai raisonnement, et
    l'ecran disait surtout du vide. Le fond ne change pas — un worker en file
    doit toujours expliquer POURQUOI il n'a rien a dire."""
    html = _rendu(_TOUS, _EV)
    assert "mp-thought-empty" in html
    assert "en attente du CodeAgent" in html, (
        "un worker en file doit expliquer POURQUOI il n'a rien a dire"
    )


def test_la_pensee_est_ECHAPPEE():
    """Elle est ecrite par un modele et injectee en HTML : non negociable."""
    ev = ("[{task_id:'w1',stage:'codeagent_iteration',"
          "thought:'<img src=x onerror=alert(1)>'}]")
    html = _rendu(_TOUS, ev)
    assert "<img src=x" not in html
    assert "&lt;img" in html


def test_l_objectif_est_ECHAPPE_aussi():
    m = "[{task_id:'lead',state:'running',metadata:{objective:'<script>x</script>'}}]"
    html = _rendu(m)
    assert "<script>x</script>" not in html


# ══════════════════════════════════════════════════════════════════════════
#  3. Ce que le panneau actuel ne montrait pas
# ══════════════════════════════════════════════════════════════════════════


def test_la_file_du_codeagent_est_visible():
    """Le verrou de serialisation cesse d'etre invisible."""
    html = _rendu(_TOUS, _EV, now="0")
    assert "mp-queue-bar" in html
    assert "sérialisé" in html


def test_le_compte_a_rebours_apparait():
    html = _rendu(_TOUS, _EV, now="Date.parse('2026-08-29T11:30:00')")
    assert "30:00" in html and "restant" in html


def test_une_echeance_DEPASSEE_est_signalee():
    html = _rendu(_TOUS, _EV, now="Date.parse('2026-08-29T12:30:00')")
    assert "is-late" in html and "dépassée" in html


def test_le_perimetre_de_chaque_worker_est_affiche():
    html = _rendu(_TOUS, _EV)
    assert "modele.py" in html and "api.py" in html


def test_le_lead_a_sa_propre_carte():
    assert ">Lead<" in _rendu(_TOUS, _EV)


def test_les_noms_de_workers_sont_lisibles():
    """« [Worker w_ia] … » doit s'afficher « w_ia », pas un id de 32 caracteres."""
    html = _rendu(_TOUS, _EV)
    assert ">w_ia<" in html and ">w_api<" in html


# ══════════════════════════════════════════════════════════════════════════
#  4. Preferences : chaque bloc est amovible
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("bloc,marqueur", [
    ("thought", "mp-thought"),
    ("perimeter", "mp-chip"),
    ("queue", "mp-queue-bar"),
    ("countdown", "mp-countdown"),
])
def test_chaque_bloc_peut_etre_masque(bloc, marqueur):
    prefs = f"{{blocks:{{{bloc}:false}}}}"
    assert marqueur not in _rendu(_TOUS, _EV, prefs=prefs)


def test_sans_preferences_TOUT_est_visible():
    html = _rendu(_TOUS, _EV)
    for m in ("mp-thought", "mp-chip", "mp-queue-bar", "mp-countdown"):
        assert m in html, m


# ══════════════════════════════════════════════════════════════════════════
#  5. Robustesse
# ══════════════════════════════════════════════════════════════════════════


def test_aucune_mission_rend_un_message_et_pas_une_erreur():
    assert "Aucune mission" in _rendu("[]")


def test_une_mission_SANS_worker_rend_quand_meme():
    html = _rendu(f"[{_LEAD}]")
    assert "Construis LogTriage" in html and "Lead" in html


def test_un_backend_sans_les_champs_neufs_rend_quand_meme():
    """Client a jour, serveur pas encore : le panneau doit tenir."""
    html = _rendu(_TOUS, "[]")
    assert "mp-mission" in html and "mp-thought-empty" in html


# ══════════════════════════════════════════════════════════════════════════
#  6. IDENTITE VISUELLE — le garde qui empeche la derive
# ══════════════════════════════════════════════════════════════════════════


_COULEUR_DURE = re.compile(
    r"(?<![\w-])(#[0-9a-fA-F]{3,8}\b|rgba?\([^)]*\)|hsla?\([^)]*\))")


def test_AUCUNE_couleur_en_dur_dans_la_feuille_du_panneau():
    """Sans ce test, une seule valeur litterale suffit a casser le theme clair.

    Tout doit passer par les tokens de `tokens.css` — c'est ce qui fait que le
    panneau suit l'identite de Lumena automatiquement.
    """
    css = _CSS.read_text(encoding="utf-8")
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)   # les commentaires ne comptent pas
    dures = _COULEUR_DURE.findall(css)
    assert not dures, f"couleurs en dur dans mission-panel.css : {sorted(set(dures))}"


def test_les_tokens_utilises_EXISTENT_vraiment():
    """Un `var(--typo)` silencieux rend un element invisible sans rien dire."""
    css = _CSS.read_text(encoding="utf-8")
    connus = set(re.findall(r"^\s*(--[\w-]+)\s*:", _TOKENS.read_text(encoding="utf-8"), re.M))
    utilises = set(re.findall(r"var\((--[\w-]+)", css))
    # Une propriete que la feuille definit ELLE-MEME (`--et`, le canal
    # d'etat) n'est pas un token de theme absent.
    locaux = set(re.findall(r"(--[\w-]+)\s*:", css))
    inconnus = sorted(utilises - connus - locaux)
    assert not inconnus, f"tokens inexistants dans tokens.css : {inconnus}"


def test_la_feuille_prevoit_les_ecrans_etroits_et_le_mouvement_reduit():
    css = _CSS.read_text(encoding="utf-8")
    assert "@media (max-width" in css
    assert "prefers-reduced-motion" in css


def test_la_densite_compacte_existe():
    """Preference reprise du patron `overview.js`, deja en place cote produit."""
    assert ".mp-compact" in _CSS.read_text(encoding="utf-8")
