"""LOT Z29 — on ne demande pas au modèle de se corriger avec sa propre erreur.

Cause racine de Z28-A. Le run « Papier Cousu » a produit un site complet en
9 minutes, puis a été déclaré `failed` parce que la conclusion ne se formatait
pas : 3 « THOUGHT leaké » + 1 troncature, budget épuisé.

MESURES faites avant d'écrire une ligne, sur les données réelles :

  • 1019 réponses finales réellement livrées (corpus `training_pool`)
    → le détecteur n'en rejetterait que 6, soit **0,6 %**.
    Donc le détecteur n'est PAS trop sensible : les rejets étaient légitimes,
    le modèle produisait vraiment une intention vide.

  • 478 runs avec métadonnées ReAct → 71 réparés, 36 finis « incomplete ».
    **Une réparation sur deux échoue.** Ce n'est pas un accident du 19/08 :
    `final_answer_potentially_incomplete` apparaît 10 fois au corpus.

Le défaut n'est donc pas la détection, c'est la RÉPARATION — et son mécanisme
est à la ligne près :

    if thought.content and len(thought.content.strip()) > 80:
        _leaked_analysis = "Analyse déjà effectuée (réutilise-la…) : <pensée>"

La pensée rejetée valait « Le site est complet et vérifié, je livre le résultat
final avec les détails concrets. » — **84 caractères**, donc au-dessus du seuil
de 80. Elle était renvoyée au modèle avec « réutilise-la », juste avant de lui
reprocher d'avoir écrit une intention. Il obéissait à la première consigne : la
même phrase revient **mot pour mot** à 18:04:36, 18:04:58 et 18:12:55.

La leçon était déjà écrite dans le code (final_guards.py) : « le LLM leak le
THOUGHT en reformulant un texte déjà prêt : régime catastrophique 3 repairs +
fallback ». Elle n'avait été appliquée qu'à un cas étroit.
"""

from pathlib import Path

import pytest

from src.reasoning.final_guards import _looks_like_intention


_SRC = Path("src/reasoning/react.py").read_text(encoding="utf-8")

# La pensée exacte du run, au caractère près.
_PENSEE_DU_RUN = (
    "Le site est complet et vérifié, je livre le résultat final "
    "avec les détails concrets."
)


# ── Le fait mesuré qui justifie le lot ───────────────────────────────────────


def test_la_pensee_du_run_passait_bien_le_seuil_de_80():
    """Sans ça, le lot n'a pas lieu d'être : c'est parce qu'elle dépasse 80
    caractères qu'elle était réinjectée."""
    assert len(_PENSEE_DU_RUN) > 80


def test_la_pensee_du_run_est_bien_une_intention():
    """Et c'est ce que le nouveau test de NATURE reconnaît."""
    assert _looks_like_intention(_PENSEE_DU_RUN) is True


def test_une_vraie_analyse_reste_reinjectee():
    """LE garde anti-régression : la réinjection sert à ne pas perdre le
    raisonnement. Une analyse concrète doit continuer de passer."""
    analyse = (
        "J'ai lu les 6 fichiers du dossier. index.html fait 139 lignes et "
        "contient <canvas id='bookCanvas'>, script.js 457 lignes avec "
        "requestAnimationFrame, style.css 1291 lignes en variables CSS. "
        "Les 3 pages ont répondu 200 sur le port 8081."
    )
    assert len(analyse) > 80
    assert _looks_like_intention(analyse) is False


# ── Phase 0 — le rejet devient observable ────────────────────────────────────


def test_le_texte_rejete_est_desormais_journalise():
    """Ni l'utilisateur ni moi ne pouvions arbitrer un rejet : le log montrait
    la PENSÉE, jamais le texte refusé. Le garde jugeait un texte puis le jetait."""
    i = _SRC.index("LOT Z29 phase 0")
    bloc = _SRC[i:i + 1500]
    assert "[Z29] THOUGHT leak — cas={} len={} texte={!r}" in bloc
    assert "[:300]" in bloc


def test_le_cas_declencheur_est_nomme():
    """« leak » sans dire LEQUEL des 4 cas ne permet pas de corriger."""
    bloc = _SRC[_SRC.index("LOT Z29 phase 0"):][:1500]
    for cas in ("prefixe_reflexion", "intention",
                "answer_egale_thought", "final_sans_contenu"):
        assert cas in bloc


def test_le_cas_est_pose_en_meta_pour_survivre_au_run():
    bloc = _SRC[_SRC.index("LOT Z29 phase 0"):][:1500]
    assert '_run_meta["thought_leak_case"]' in bloc
    assert '_run_meta["thought_leak_len"]' in bloc


def test_la_phase_0_ne_change_aucune_decision():
    """Elle journalise, elle ne branche pas : aucun `continue`, aucun `return`,
    aucune écriture sur `_thought_leaked` dans son bloc."""
    i = _SRC.index("LOT Z29 phase 0")
    bloc = _SRC[i:_SRC.index("LOT Z29 phase 3", i)]
    code = [l.split("#", 1)[0] for l in bloc.splitlines()]
    assert not any("continue" in l or "return " in l for l in code)
    assert not any("_thought_leaked =" in l for l in code)


# ── Phase 1 — on ne lui rend plus son intention ──────────────────────────────


def test_le_seuil_est_devenu_un_test_de_nature():
    i = _SRC.index("LOT Z29 phase 1")
    bloc = _SRC[i:i + 1800]
    assert "not _looks_like_intention(_th_txt)" in bloc


def test_l_ancien_test_de_longueur_seul_a_disparu():
    """Si cette ligne revient, le lot est contourné sans qu'aucun test ne tombe."""
    assert "if thought.content and len(thought.content.strip()) > 80:" not in _SRC


def test_la_raison_est_datee_dans_le_code():
    entete = _SRC[_SRC.index("LOT Z29 phase 1"):][:1400]
    assert "84 caractères" in entete
    assert "18:04:36" in entete


# ── Phase 2 — les faits du run entier ────────────────────────────────────────


def test_le_journal_d_execution_est_donne_au_modele():
    i = _SRC.index("LOT Z29 phase 2")
    bloc = _SRC[i:i + 1400]
    assert "self.execution_ledger.summary()" in bloc
    assert "FAITS ÉTABLIS DANS CE RUN" in bloc


def test_les_faits_precedent_la_derniere_observation():
    """En queue, ils seraient lus après le contexte étroit qui a causé la dérive."""
    i_faits = _SRC.index("LOT Z29 phase 2")
    i_obs = _SRC.index("_last_obs_block = \"\"", i_faits)
    assert i_faits < i_obs


def test_la_derniere_observation_reste_disponible():
    """On ajoute un contexte large, on ne retire pas les chiffres concrets."""
    i = _SRC.index("LOT Z29 phase 2")
    bloc = _SRC[i:i + 2500]
    assert "RÉUTILISE CES CHIFFRES" in bloc


def test_le_journal_ne_peut_pas_faire_echouer_la_reparation():
    i = _SRC.index("LOT Z29 phase 2")
    bloc = _SRC[i:i + 700]
    assert "try:" in bloc and "except Exception:" in bloc


# ── Phase 3 — arrêter de demander quand ça ne marche pas ─────────────────────


def test_le_plafond_de_demandes_est_dur():
    i = _SRC.index("LOT Z29 phase 3")
    bloc = _SRC[i:i + 1600]
    assert "_max_tleak = 2" in bloc


def test_le_calcul_P5_mort_a_ete_retire():
    """LOT Z31 — audit de cohérence. Le plafond Z29 rendait les 4 branches du
    profil P5 identiques (4/3/2/2 → toutes 2). Un calcul inerte fait croire à un
    réglage qui n'existe plus : il a été retiré, pas laissé en place."""
    assert "_max_tleak = min(_max_tleak, 2)" not in _SRC
    assert 'getattr(self._model_profile, "thought_leak_risk"' not in _SRC


def test_le_retrait_du_reglage_est_documente():
    """Retirer le seul consommateur de `thought_leak_risk` sans le dire, c'est
    laisser le prochain lecteur croire que le champ agit encore."""
    entete = _SRC[_SRC.index("LOT Z31"):][:900]
    assert "SEUL consommateur" in entete
    assert "thought_leak_risk" in entete


def test_le_repli_deterministe_existe_toujours_apres_le_plafond():
    """Plafonner les demandes n'a de sens que si le repli produit une réponse."""
    i = _SRC.index("_max_tleak = 2")
    suite = _SRC[i:i + 16000]
    assert "elif _thought_leaked:" in suite
    assert "_strip_thought_leak_prefix" in suite
    assert "_synthesize_mission_response_from_evidence" in suite


def test_la_mesure_qui_justifie_le_plafond_est_dans_le_code():
    entete = _SRC[_SRC.index("LOT Z29 phase 3"):][:900]
    assert "71 runs" in entete
    assert "36" in entete


# ── Cohérence d'ensemble ─────────────────────────────────────────────────────


def test_les_quatre_phases_sont_presentes_et_uniques():
    for n in range(4):
        assert _SRC.count(f"LOT Z29 phase {n}") == 1


def test_les_phases_sont_dans_l_ordre_du_flux():
    positions = [_SRC.index(f"LOT Z29 phase {n}") for n in range(4)]
    # phase 0 (détection) → 3 (plafond) → 1 (réinjection) → 2 (faits)
    assert positions[0] < positions[3] < positions[1] < positions[2]
