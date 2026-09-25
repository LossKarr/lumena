"""LOT 0 — LE MÉTIER D'UN WORKER EST UNE DONNÉE, PLUS UNE EXTENSION DE FICHIER.

═══════════════════════════════════════════════════════════════════════════════
  CE QUI A ÉTÉ MESURÉ AVANT D'ÉCRIRE UNE LIGNE
═══════════════════════════════════════════════════════════════════════════════

Lumena porte **52 familles d'outils** (`src/reasoning/handlers/*.py`). Son système
de délégation en reconnaissait **TROIS** — frontend, backend, tests — tous déduits
du SUFFIXE du fichier (`_role_rider`).

Conséquence, obtenue en APPELANT le code et non en le lisant :

    worker .py    → CODING + steer CodeAgent + rider backend    1 354 car.
    worker .css   → CODING + steer + rider frontend + brief     1 730 car.
    worker .md    → CODING seul                                   702 car.
    worker .csv   → CODING seul                                   702 car.

Un rédacteur recevait donc, mot pour mot :

    🛠️ DISCIPLINE DE CODAGE (agent dev, pas un rédacteur) :
    • Après CHAQUE mutation significative, EXÉCUTE (module/tests concernés)…

à quelqu'un dont le livrable est un README — sans module ni test à exécuter.

Sur le corpus d'exécution (`data/task_orchestrator_state.json`, 463 workers),
**66 % ne touchent JAMAIS le CodeAgent** : 149 mutent en ReAct direct, 156 ne
mutent rien du tout (recherche, lecture, analyse).

Sur les contrats du disque (`workspace/**/contract.json`, 537 workers), l'impact
immédiat est plus modeste et il faut le dire : **20 workers (4 %)** recevaient la
mauvaise discipline — ces contrats sont à 95 % du code. Le lot corrige ces 4 %
AUJOURD'HUI et ouvre les autres métiers pour la suite.

═══════════════════════════════════════════════════════════════════════════════
  LE FAIT EXISTAIT DÉJÀ — IL N'ATTEIGNAIT PAS LA DÉCISION
═══════════════════════════════════════════════════════════════════════════════

`_DOC_EXT` (`.md .markdown .txt .rst .adoc`) existe depuis I1 et sert :
  · à la génération de stub  (« Remplace INTÉGRALEMENT ce contenu ») ;
  · à `validate_contract`     (refuse `def`/`class` sur un `.md`) ;
  · à `inspect_worker_deliverables`.

**Jamais au choix de la discipline.** Le motif habituel : le fait est calculé, il
sert ailleurs, et il est ignoré au moment de décider.

Ce lot COMPLÈTE I1/I2/I3 — qui ont traité le stub et la validation. Il ajoute la
couche manquante : la CONSIGNE DE TRAVAIL.

═══════════════════════════════════════════════════════════════════════════════
  RÉTROCOMPATIBILITÉ — LA CONTRAINTE PRINCIPALE
═══════════════════════════════════════════════════════════════════════════════

Un worker de CODE et un worker d'EFFETS PURS reçoivent un texte IDENTIQUE au bit
près. C'est ce qui rend le lot sûr : 941 tests `tests/subagents/` passaient avant,
ils passent après, et seuls DEUX ont été réécrits — ceux qui figeaient le défaut
(voir `test_lot_a_forced_discipline.py`).
"""

from __future__ import annotations

import glob
import json
import pathlib

import pytest

from src.subagents import mission_contract as mc


# ══════════════════════════════════════════════════════════════════════════
#  1. RÉTROCOMPATIBILITÉ — ce qui ne doit PAS avoir bougé
# ══════════════════════════════════════════════════════════════════════════


def test_le_worker_de_CODE_est_inchange():
    """Le cas le plus fréquent (511 des 537 workers du disque) : zéro dérive."""
    bloc = mc.worker_discipline_block(["app.py"])
    assert mc.WORKER_CODING_DISCIPLINE in bloc
    assert "CODE PAR DÉLÉGATION" in bloc          # steer CodeAgent conservé
    assert "🔌 BACKEND" in bloc                    # rider conservé


def test_le_worker_FRONTEND_garde_ses_riders_et_son_brief():
    bloc = mc.worker_discipline_block(["style.css"], "BRIEF-ARTISTIQUE")
    assert "🎨 FRONTEND" in bloc and "🎨 STYLE" in bloc
    assert "BRIEF-ARTISTIQUE" in bloc              # LOT Z13 intact
    assert mc.WORKER_CODING_DISCIPLINE in bloc


def test_le_worker_d_EFFETS_PURS_est_inchange():
    """H4 — un porteur d'effets sans fichier garde exactement sa discipline."""
    assert mc.role_of_worker([], [{"owner": "w"}]) == mc.ROLE_ACTION
    assert mc.discipline_for_role(mc.ROLE_ACTION) is mc.WORKER_EFFECT_DISCIPLINE


def test_un_dev_qui_documente_reste_un_dev():
    """`app.py` + `README.md` → code. Le code prime : c'est un dev qui documente,
    pas un rédacteur qui code."""
    assert mc.role_of_worker(["app.py", "README.md"]) == mc.ROLE_CODE


# ══════════════════════════════════════════════════════════════════════════
#  2. LE DÉFAUT CORRIGÉ
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("fichier,role,marqueur", [
    ("README.md", mc.ROLE_DOCUMENT, "DISCIPLINE RÉDACTIONNELLE"),
    ("notes.txt", mc.ROLE_DOCUMENT, "DISCIPLINE RÉDACTIONNELLE"),
    ("prix.csv", mc.ROLE_DONNEES, "DISCIPLINE DONNÉES"),
    ("schema.sql", mc.ROLE_DONNEES, "DISCIPLINE DONNÉES"),
    ("logo.png", mc.ROLE_MEDIA, "DISCIPLINE MÉDIA"),
    ("demo.mp4", mc.ROLE_MEDIA, "DISCIPLINE MÉDIA"),
])
def test_chaque_metier_recoit_SA_consigne(fichier, role, marqueur):
    assert mc.role_of_worker([fichier]) == role
    bloc = mc.worker_discipline_block([fichier])
    assert marqueur in bloc
    assert mc.WORKER_CODING_DISCIPLINE not in bloc


def test_un_redacteur_ne_recoit_plus_agent_dev_pas_un_redacteur():
    """L'absurdité exacte que le lot supprime."""
    bloc = mc.worker_discipline_block(["rapport.md"])
    assert "agent dev, pas un rédacteur" not in bloc
    assert "EXÉCUTE (module/tests concernés)" not in bloc


def test_sans_fichier_ni_effet_la_consigne_reste_utile():
    """Ni code, ni doc, ni effet : discipline générale — jamais celle du codage,
    jamais rien du tout."""
    assert mc.role_of_worker([]) == mc.ROLE_GENERAL
    bloc = mc.worker_discipline_block([])
    assert "DISCIPLINE DE TRAVAIL" in bloc
    assert "Lumena complète" in bloc               # il a TOUS les outils du parent


# ══════════════════════════════════════════════════════════════════════════
#  3. LE CONTRAT FAIT FOI — c'est l'objet du lot
# ══════════════════════════════════════════════════════════════════════════


def test_le_role_declare_bat_l_extension():
    """Un `.md` produit par une veille est un travail de RECHERCHE, pas de rédaction.
    Seul le contrat peut le dire — l'extension ne le devinera jamais."""
    assert mc.role_of_worker(["etat_art.md"], None, "recherche") == mc.ROLE_RECHERCHE
    bloc = mc.worker_discipline_block(["etat_art.md"], role="recherche")
    assert "DISCIPLINE DE RECHERCHE" in bloc
    assert "cite-la" in bloc or "SOURCE" in bloc


@pytest.mark.parametrize("brut,attendu", [
    ("code", mc.ROLE_CODE), ("CODE", mc.ROLE_CODE), ("  dev  ", mc.ROLE_CODE),
    ("données", mc.ROLE_DONNEES), ("data", mc.ROLE_DONNEES),
    ("recherche", mc.ROLE_RECHERCHE), ("research", mc.ROLE_RECHERCHE),
    ("browser", mc.ROLE_NAVIGATEUR), ("redaction", mc.ROLE_DOCUMENT),
])
def test_les_alias_courants_sont_acceptes(brut, attendu):
    """Le lead écrit en langage naturel — casse, accents et synonymes tolérés."""
    assert mc.normalize_role(brut) == attendu


def test_un_role_inconnu_ne_casse_RIEN():
    """Une faute de frappe ne doit jamais être pire qu'un contrat sans rôle :
    on retombe sur la déduction par extension."""
    assert mc.normalize_role("codeur-fou") == ""
    assert mc.role_of_worker(["app.py"], None, "codeur-fou") == mc.ROLE_CODE
    assert mc.role_of_worker(["a.md"], None, "n'importe quoi") == mc.ROLE_DOCUMENT


def test_le_role_se_lit_dans_les_entrees_du_contrat():
    entries = [{"path": "r.md", "owner": "w", "role": "recherche"}]
    assert mc._role_declared_for(entries) == mc.ROLE_RECHERCHE
    assert mc._role_declared_for([{"path": "r.md"}]) == ""
    assert mc._role_declared_for([], [{"owner": "w", "role": "navigateur"}]) \
        == mc.ROLE_NAVIGATEUR


# ══════════════════════════════════════════════════════════════════════════
#  4. IDEMPOTENCE — le piège que ce lot a failli créer
# ══════════════════════════════════════════════════════════════════════════


def test_CHAQUE_discipline_porte_un_marqueur():
    """GARDE STRUCTURELLE.

    `inject_worker_discipline` (filet du LOT A) reposait sur le seul marqueur
    « DISCIPLINE DE CODAGE ». Dès qu'un worker reçoit la consigne de son métier, ce
    marqueur est absent → le filet réinjecterait le bloc une SECONDE fois.

    Ce test échouera le jour où quelqu'un ajoutera un rôle sans marqueur — avant que
    la double injection n'atteigne un run réel."""
    for role in mc.ROLES_CONNUS:
        bloc = mc.discipline_for_role(role)
        assert mc.porte_une_discipline(bloc), f"rôle {role} sans marqueur d'idempotence"


@pytest.mark.parametrize("fichiers,role", [
    (["app.py"], None), (["R.md"], None), (["d.csv"], None),
    ([], "recherche"), ([], None), (["x.png"], None),
])
def test_le_filet_est_idempotent_pour_TOUS_les_metiers(fichiers, role):
    txt = "[Worker w] objectif réécrit par le lead."
    une = mc.inject_worker_discipline(txt, fichiers, role)
    deux = mc.inject_worker_discipline(une, fichiers, role)
    assert une != txt                    # le filet s'arme
    assert deux == une                   # et une seule fois
    assert une.startswith(txt)           # l'objectif du lead est préservé


# ══════════════════════════════════════════════════════════════════════════
#  5. LE STEER RESTE RÉSERVÉ AU CODE
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("fichier", ["R.md", "d.csv", "logo.png"])
def test_pas_de_steer_CodeAgent_hors_du_code(fichier):
    """66 % des workers du corpus ne touchent jamais le CodeAgent. Leur dire
    « délègue au CodeAgent » serait un contresens."""
    bloc = mc.worker_discipline_block([fichier])
    assert "CODE PAR DÉLÉGATION" not in bloc


def test_pas_de_rider_frontend_sur_un_document():
    assert "🎨 FRONTEND" not in mc.worker_discipline_block(["guide.md"])


# ══════════════════════════════════════════════════════════════════════════
#  6. BOUT EN BOUT — le chemin réel de delegate_and_wait
# ══════════════════════════════════════════════════════════════════════════


def test_worker_objectives_donne_a_chacun_son_metier():
    contrat = {
        "project": "Veille",
        "files": [
            {"path": "etat_art.md", "owner": "w_veille", "role": "recherche",
             "desc": "état de l'art 2026, sources citées"},
            {"path": "app.py", "owner": "w_api", "desc": "API",
             "exports": ["def create_app():"]},
            {"path": "prix.csv", "owner": "w_data", "desc": "relevé tarifaire"},
        ],
        "effects": [{"owner": "w_mail", "action": "envoyer_email",
                     "desc": "envoie le rapport", "proof": "accusé"}],
    }
    par_owner = {}
    for o in mc.worker_objectives(contrat):
        marqueurs = [m for m in mc._MARQUEURS_DISCIPLINE if m in o["objective"]]
        assert len(marqueurs) == 1, f"{o['allowed_files']}: {marqueurs}"
        par_owner[tuple(o["allowed_files"])] = marqueurs[0]

    assert par_owner[("etat_art.md",)] == "DISCIPLINE DE RECHERCHE"
    assert par_owner[("app.py",)] == mc._DISCIPLINE_MARKER
    assert par_owner[("prix.csv",)] == "DISCIPLINE DONNÉES"
    assert par_owner[()] == "DISCIPLINE D'ACTION"


def test_le_PREAMBULE_ne_contredit_plus_le_stub_documentaire():
    """Défaut trouvé en lisant le message COMPLET d'un rédacteur, pas en relisant le
    code — et c'est une rechute de I1.

    Le préambule ordonnait « signatures EXACTES », « pas de réécriture totale », « NE
    modifie JAMAIS une signature ». Le stub documentaire posé par I1 dit l'inverse :
    « Remplace INTÉGRALEMENT ce contenu par le document final ». Le worker recevait
    donc deux ordres opposés dans le même message — et I1 a prouvé qu'il obéit au
    mauvais (un `.md` livré rempli de Python).

    Le CODE garde son préambule au bit près."""
    doc = mc.worker_objectives({"project": "G", "files": [
        {"path": "guide.md", "owner": "w", "desc": "Guide"}]})[0]["objective"]
    assert "signatures EXACTES" not in doc
    assert "NE modifie JAMAIS une signature" not in doc
    assert "REMPLACER INTÉGRALEMENT" in doc
    assert "CONTRAT DE MISSION" in doc      # le marqueur du préambule reste

    code = mc.worker_objectives({"project": "G", "files": [
        {"path": "app.py", "owner": "w", "desc": "API",
         "exports": ["def f():"]}]})[0]["objective"]
    assert mc.WORKER_CONTRACT_PREAMBLE in code


def test_le_contrat_accepte_le_champ_role():
    """`validate_contract` ne doit pas rejeter un contrat qui déclare des rôles."""
    contrat = {"project": "P", "files": [
        {"path": "r.md", "owner": "w", "role": "recherche", "desc": "veille"}]}
    assert mc.validate_contract(contrat) == []


# ══════════════════════════════════════════════════════════════════════════
#  6bis. UN RÔLE NON COMPRIS NE PASSE PLUS EN SILENCE
# ══════════════════════════════════════════════════════════════════════════


def test_un_role_mal_orthographie_est_SIGNALE_au_lead():
    """Le motif que ce dépôt combat depuis soixante lots.

    `normalize_role` rend "" sur un mot inconnu et la déduction par extension reprend
    la main. Sans avertissement, le lead écrit « redacteur », obtient `document` par
    hasard — ou `code` s'il s'était trompé de fichier — et n'apprend JAMAIS que son
    mot n'a pas été compris. Le fait était calculé et n'atteignait pas celui qui
    décide."""
    warn = mc.unknown_role_warning({"project": "P", "files": [
        {"path": "r.md", "owner": "w", "role": "redacteur"}]})
    assert warn
    assert "redacteur" in warn and "r.md" in warn
    for valeur in mc.ROLES_CONNUS:
        assert valeur in warn, "l'avertissement doit lister les valeurs acceptées"


def test_l_avertissement_NE_FAIT_PAS_DE_BRUIT():
    """AUD-017 — désaturation des gardes. Un avertissement qui se déclenche à tort
    dilue tous les autres. Rôle correct ou absent → silence total."""
    assert mc.unknown_role_warning({"files": [{"path": "a.py", "role": "code"}]}) == ""
    assert mc.unknown_role_warning({"files": [{"path": "a.py"}]}) == ""
    assert mc.unknown_role_warning({"files": [{"path": "a.py", "role": "Données"}]}) == ""
    assert mc.unknown_role_warning({}) == ""
    assert mc.unknown_role_warning(None) == ""


def test_un_role_inconnu_reste_NON_BLOQUANT():
    """C'est un avertissement, pas une erreur : le repli marche, la mission part."""
    contrat = {"project": "P", "files": [
        {"path": "r.md", "owner": "w", "role": "redacteur", "desc": "x"}]}
    assert mc.validate_contract(contrat) == []
    assert mc.worker_objectives(contrat)          # la délégation reste possible


def test_le_metier_est_ECRIT_dans_CONTRAT_md():
    """Le worker lit CONTRAT.md en premier : il doit y voir son métier. Et le lead
    doit pouvoir constater ce que sa déclaration — ou son omission — a produit."""
    md = mc.render_contract_md({"project": "P", "files": [
        {"path": "guide.md", "owner": "w_doc", "desc": "Guide"},
        {"path": "app.py", "owner": "w_api", "desc": "API"}]})
    assert "métier : `document`" in md
    # Le code est le cas par défaut : ne pas l'annoter (pas de bruit sur 95 % des
    # entrées des contrats réels).
    ligne_py = next(l for l in md.splitlines() if "app.py" in l)
    assert "métier" not in ligne_py


# ══════════════════════════════════════════════════════════════════════════
#  7. LE LEAD DOIT POUVOIR LE DÉCOUVRIR
# ══════════════════════════════════════════════════════════════════════════


def test_le_schema_de_l_outil_ENSEIGNE_le_role():
    """GARDE ANTI-CAPACITÉ-MORTE.

    `contract.effects` existe depuis H4 et n'est utilisé que par **3 contrats sur
    176** : le lead n'emploie pas ce qu'il ne connaît pas. Un champ `role` que rien
    n'annonce subirait le même sort.

    Ce test fige le fait que le schéma de `write_mission_contract` — le seul texte
    que le lead lit avant d'écrire son contrat — nomme le champ et ses valeurs."""
    src = (pathlib.Path(__file__).parents[2] / "src" / "reasoning" / "handlers"
           / "missions.py").read_text(encoding="utf-8")
    # `name="write_mission_contract"` apparaît aussi dans les `handler_name=` des
    # retours d'erreur, bien plus haut : c'est la DERNIÈRE occurrence qui ouvre la
    # déclaration du registre.
    i = src.rindex('name="write_mission_contract"')
    bloc = src[i:i + 8000]
    assert "`role?`" in bloc, "le schéma n'annonce pas le champ role"
    for valeur in ("document", "donnees", "recherche", "navigateur", "media"):
        assert valeur in bloc, f"valeur de rôle absente du schéma : {valeur}"


# ══════════════════════════════════════════════════════════════════════════
#  8. LE CORPUS RÉEL — la mesure qui a déclenché le lot
# ══════════════════════════════════════════════════════════════════════════


def _contrats_reels():
    out = []
    for f in glob.glob("workspace/**/contract.json", recursive=True):
        try:
            d = json.loads(pathlib.Path(f).read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        if isinstance(d, dict) and (d.get("files") or d.get("effects")):
            out.append(d)
    return out


@pytest.mark.skipif(not pathlib.Path("workspace").is_dir(),
                    reason="workspace absent de cette machine")
def test_aucun_worker_de_code_du_corpus_ne_change_de_discipline():
    """La preuve de non-régression, sur les contrats RÉELS et non sur des fixtures :
    tout worker possédant un fichier de code reste `code`."""
    contrats = _contrats_reels()
    if len(contrats) < 5:
        pytest.skip(f"corpus trop maigre : {len(contrats)} contrats")
    for d in contrats:
        grouped = mc.owners_map(d)
        effets = mc.effects_map(d)
        for owner, entries in grouped.items():
            # Le corpus est réel : certaines entrées sont malformées (pas de `path`).
            # On les ignore plutôt que de faire échouer la mesure dessus.
            mine = [str(e.get("path")) for e in entries
                    if isinstance(e, dict) and e.get("path")]
            if not mc._has_code_files(mine):
                continue
            declare = mc._role_declared_for(entries, effets.get(owner) or [])
            if declare:            # un rôle explicite a le droit de contredire
                continue
            assert mc.role_of_worker(mine, effets.get(owner) or []) == mc.ROLE_CODE
