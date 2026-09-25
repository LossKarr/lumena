"""LOT 10 — LUMENA SIGNALE SES PROPRES BRIDES.

═══════════════════════════════════════════════════════════════════════════════
  LE PLAFOND QUI BRIDE LE PLUS ÉTAIT LE SEUL À N'ÊTRE NULLE PART
═══════════════════════════════════════════════════════════════════════════════

Mesuré sur le dépôt le 03/09 : `LUMENA_PROVIDER_CONCURRENCY` apparaissait
**une seule fois** dans tout `src/` + `web/` — sa lecture depuis l'environnement,
`multi_provider.py:37`. Absent du panel de configuration, donc inconnu de
`update_lumena_config`. Aucune trace d'attente.

⚠ PRÉCISION DUE À LA RÉGRESSION. La clé n'était pas totalement inconnue du
dépôt : elle figurait dans `.env.example`, **en ligne commentée**, section
« LLM (avancé) » — l'inventaire des paramètres lus mais non exposés. Le dépôt
savait donc qu'elle existait, la classait « avancé », et ne la montrait nulle
part où quelqu'un aurait pu la régler. C'est une version plus forte du défaut,
pas plus faible.

Ses trois frères y figuraient pourtant depuis longtemps :

    LUMENA_MISSION_CONCURRENCY          config.py:145
    LUMENA_MISSION_WORKER_CONCURRENCY   config.py:148
    LUMENA_MISSION_MAX_DEPTH            config.py:151

Et le sémaphore est **global au processus, un seul point d'acquisition**
(`multi_provider._chat_provider_result`) : le chat, le heartbeat, le lead d'une
mission, chacun de ses workers et chacun de leurs CodeAgents le partagent.

Conséquence : on monte « Missions — workers en parallèle » de 2 à 6, rien
n'accélère, et rien n'explique pourquoi.

═══════════════════════════════════════════════════════════════════════════════
  L'UNION DES INTERVALLES, JAMAIS LA SOMME
═══════════════════════════════════════════════════════════════════════════════

Deux workers bloqués 30 s **en même temps** coûtent 30 s à la mission, pas 60.
Une somme cumulée ferait dire au constat le double de la vérité — et un
avertissement qui exagère est un avertissement qu'on apprend à ignorer.

═══════════════════════════════════════════════════════════════════════════════
  CONSTAT, JAMAIS ACTION
═══════════════════════════════════════════════════════════════════════════════

Lumena sait modifier sa configuration : l'outil existe. Elle ne le fait pas ici.
Ces plafonds protègent le quota du fournisseur, le LLM local et la machine —
c'est une décision utilisateur. Même doctrine que les mises à jour.
"""

from __future__ import annotations

import asyncio
import inspect
import re

import pytest

from src.subagents import runner as R
from src.telemetry import provider_wait as PW


# ══════════════════════════════════════════════════════════════════════════
#  1. LA MESURE — union des intervalles, et rien d'autre
# ══════════════════════════════════════════════════════════════════════════


def test_deux_attentes_SIMULTANEES_ne_comptent_qu_une_fois():
    """LE CŒUR DE LA MESURE. Sommer donnerait le double de la vérité."""
    compteur = PW.ouvrir_compteur()
    try:
        PW.entrer_en_attente()
        PW.entrer_en_attente()
        compteur["_depuis"] -= 30.0          # on simule 30 s écoulées
        PW.sortir_d_attente()
        assert compteur["attente_s"] == 0.0, (
            "l'intervalle s'est fermé alors qu'un appel attend encore"
        )
        PW.sortir_d_attente()
        assert 29.0 < compteur["attente_s"] < 32.0, compteur["attente_s"]
        assert compteur["appels_bloques"] == 2   # deux appels, un seul intervalle
    finally:
        PW.fermer_compteur()


def test_deux_attentes_SUCCESSIVES_s_additionnent():
    """Garde inverse : l'union ne doit pas non plus SOUS-estimer."""
    compteur = PW.ouvrir_compteur()
    try:
        for _ in range(2):
            PW.entrer_en_attente()
            compteur["_depuis"] -= 10.0
            PW.sortir_d_attente()
        assert 19.0 < compteur["attente_s"] < 22.0, compteur["attente_s"]
    finally:
        PW.fermer_compteur()


def test_sans_compteur_ouvert_rien_n_est_compte():
    """Les appels du CHAT ne doivent jamais être attribués à une mission."""
    PW.fermer_compteur()
    PW.entrer_en_attente()      # ne doit pas lever
    PW.sortir_d_attente()
    assert PW.compteur_courant() is None


def test_une_sortie_orpheline_ne_casse_rien():
    compteur = PW.ouvrir_compteur()
    try:
        PW.sortir_d_attente()
        PW.sortir_d_attente()
        assert compteur["attente_s"] == 0.0
        assert compteur["_en_attente"] == 0
    finally:
        PW.fermer_compteur()


def test_l_attente_ne_depasse_jamais_la_duree_du_run():
    """Propriété qui rend le constat comparable à la durée : c'est tout l'intérêt
    de l'union."""
    compteur = PW.ouvrir_compteur()
    try:
        for _ in range(5):
            PW.entrer_en_attente()
        compteur["_depuis"] -= 12.0
        for _ in range(5):
            PW.sortir_d_attente()
        assert compteur["attente_s"] < 15.0, "5 appels ont produit 5× l'intervalle"
    finally:
        PW.fermer_compteur()


def test_le_compteur_est_HERITE_par_les_taches_filles():
    """Un worker est une tâche asyncio créée APRÈS l'ouverture : il doit écrire
    dans le compteur du lead. C'est ce qui rend la mesure globale à la mission."""

    async def scenario():
        compteur = PW.ouvrir_compteur()

        async def worker():
            PW.entrer_en_attente()
            PW.sortir_d_attente()

        await asyncio.gather(worker(), worker())
        return compteur

    compteur = asyncio.run(scenario())
    assert compteur["appels_bloques"] == 2, (
        "les workers n'ont pas hérité du compteur du lead"
    )


def test_le_plafond_est_lu_depuis_l_environnement(monkeypatch):
    monkeypatch.setenv("LUMENA_PROVIDER_CONCURRENCY", "6")
    assert PW.plafond_provider() == 6
    monkeypatch.setenv("LUMENA_PROVIDER_CONCURRENCY", "n'importe quoi")
    assert PW.plafond_provider() == 2      # défaut, jamais une exception


# ══════════════════════════════════════════════════════════════════════════
#  2. LE CONSTAT — deux conditions, pas une (AUD-017)
# ══════════════════════════════════════════════════════════════════════════


def test_le_constat_parle_quand_la_bride_est_reelle():
    out = R.annotate_provider_ceiling("BILAN", 400.0, 1080.0, appels=7)
    assert "LUMENA_PROVIDER_CONCURRENCY" in out
    assert "6 min 40 s" in out and "18 min" in out
    assert "BILAN" in out                     # le bilan n'est jamais réécrit


@pytest.mark.parametrize(
    "attente, duree, pourquoi",
    [
        (30.0, 100.0, "30 s : sous le plancher absolu de 60 s"),
        (70.0, 3600.0, "70 s sur 1 h : 2 % du run, pas une bride"),
        (400.0, 0.0, "durée inconnue : rien à comparer"),
        (0.0, 1080.0, "aucune attente"),
    ],
)
def test_le_constat_se_TAIT_quand_ce_serait_du_bruit(attente, duree, pourquoi):
    """AUD-017 — un avertissement qui se déclenche à tort dilue tous les autres.
    Les deux conditions doivent être réunies, jamais une seule."""
    assert R.annotate_provider_ceiling("BILAN", attente, duree) == "BILAN", pourquoi


def test_le_constat_ne_DECIDE_jamais_a_la_place_de_l_utilisateur():
    """Lumena sait écrire sa config. Ces plafonds protègent le quota, le LLM
    local et la machine : c'est une décision utilisateur."""
    out = R.annotate_provider_ceiling("BILAN", 400.0, 1080.0)
    assert "Constat" in out
    assert "ta décision" in out
    src = inspect.getsource(R.annotate_provider_ceiling)
    for interdit in ("update_lumena_config", "os.environ[", "setenv", "putenv"):
        assert interdit not in src, f"le constat écrit la config : {interdit}"


def test_le_constat_nomme_le_reglage_ET_sa_valeur():
    """Un constat qui dit « c'est bridé » sans nommer quoi ne sert à rien."""
    out = R.annotate_provider_ceiling("BILAN", 400.0, 1080.0, plafond=4)
    assert "LUMENA_PROVIDER_CONCURRENCY=4" in out
    assert "configuration" in out.lower()


def test_le_constat_explique_le_piege_du_reglage_voisin():
    """Le défaut concret : on monte « workers en parallèle » et rien n'accélère."""
    out = R.annotate_provider_ceiling("BILAN", 400.0, 1080.0)
    assert "workers en parall" in out


def test_le_constat_est_idempotent():
    """Re-clôture après reprise : la bannière ne doit pas s'empiler."""
    une = R.annotate_provider_ceiling("BILAN", 400.0, 1080.0)
    deux = R.annotate_provider_ceiling(une, 400.0, 1080.0)
    assert deux.count("LUMENA_PROVIDER_CONCURRENCY") == 1


def test_le_constat_est_PUR_et_ne_leve_jamais():
    for mauvais in (None, 123, object()):
        R.annotate_provider_ceiling(mauvais, "pas un nombre", None)


def test_les_durees_sont_lisibles():
    assert R._duree_lisible(45) == "45 s"
    assert R._duree_lisible(400) == "6 min 40 s"
    assert R._duree_lisible(4000) == "1 h 06 min"
    assert R._duree_lisible("nawak") == "?"


# ══════════════════════════════════════════════════════════════════════════
#  3. LE PARAMÈTRE EXISTE ENFIN DANS LE PANEL
# ══════════════════════════════════════════════════════════════════════════


def _table_config():
    import web.routes.config as C

    for nom in dir(C):
        valeur = getattr(C, nom)
        if isinstance(valeur, (list, tuple)) and valeur and isinstance(valeur[0], dict):
            if any(str(e.get("key", "")).startswith("LUMENA_") for e in valeur):
                return list(valeur)
    pytest.skip("table de configuration introuvable")


def test_le_plafond_provider_est_dans_la_table():
    entrees = {e.get("key"): e for e in _table_config()}
    assert "LUMENA_PROVIDER_CONCURRENCY" in entrees, (
        "le plafond qui bride le plus reste invisible dans l'UI"
    )
    e = entrees["LUMENA_PROVIDER_CONCURRENCY"]
    assert e["type"] == "number" and e["default"] == "2"
    assert e["min"] == 1 and e["max"] >= 4
    # Redémarrage requis : les sémaphores sont créés une fois par boucle.
    assert e.get("restart") is True


def test_ses_trois_freres_sont_toujours_la():
    """Non-régression : ajouter le quatrième ne doit pas déloger les autres."""
    cles = {e.get("key") for e in _table_config()}
    for frere in (
        "LUMENA_MISSION_CONCURRENCY",
        "LUMENA_MISSION_WORKER_CONCURRENCY",
        "LUMENA_MISSION_MAX_DEPTH",
    ):
        assert frere in cles, frere


def test_le_hint_dit_le_piege_pas_seulement_la_definition():
    entrees = {e.get("key"): e for e in _table_config()}
    hint = entrees["LUMENA_PROVIDER_CONCURRENCY"]["hint"]
    assert "workers en parall" in hint      # le réglage voisin qu'on monte à tort
    assert "quota" in hint or "429" in hint  # le coût de le monter


# ══════════════════════════════════════════════════════════════════════════
#  4. L'INSTRUMENTATION EST BIEN AU CHOKEPOINT UNIQUE
# ══════════════════════════════════════════════════════════════════════════


def test_le_semaphore_n_a_toujours_qu_UN_point_d_acquisition():
    """Toute la mesure repose là-dessus. Si un second `async with` apparaît un
    jour sans instrumentation, l'attente devient sous-estimée en silence."""
    import src.llm.multi_provider as MP

    src = inspect.getsource(MP)
    acquisitions = re.findall(r"async with _get_provider_semaphore\(|async with _sema_pw:", src)
    assert len(acquisitions) == 1, (
        f"{len(acquisitions)} acquisitions du sémaphore provider — "
        "la mesure du lot 10 n'en couvre qu'une"
    )


def test_l_attente_est_tracee_sur_le_modele_du_codeagent():
    import src.llm.multi_provider as MP

    src = inspect.getsource(MP.MultiProviderLLM._chat_provider_result)
    assert "provider_wait_start" in src and "provider_wait_end" in src
    assert "duration_ms" in src
    # On ne trace QUE si l'appel est réellement mis en file (pas de bruit).
    assert ".locked()" in src


def test_l_instrumentation_ne_peut_pas_casser_l_appel_LLM():
    """Défensif de bout en bout — même règle que `codeagent_wait_start`."""
    import src.llm.multi_provider as MP

    src = inspect.getsource(MP.MultiProviderLLM._chat_provider_result)
    i = src.index("_en_file_pw")
    bloc = src[i:]
    assert bloc.count("except Exception") >= 4, (
        "une émission de trace peut empêcher l'appel LLM"
    )


# ══════════════════════════════════════════════════════════════════════════
#  5. LE BRANCHEMENT DANS LA MISSION
# ══════════════════════════════════════════════════════════════════════════


def test_le_compteur_est_ouvert_AVANT_le_lancement_du_lead():
    """Posé après coup, les tâches déjà créées n'en hériteraient pas."""
    src = inspect.getsource(R.run_mission)
    i_ouvre = src.index("ouvrir_compteur")
    i_lead = src.index("Profil « lead »")
    assert i_ouvre < i_lead


def test_le_constat_rejoint_la_chaine_des_annotate_existants():
    src = inspect.getsource(R.run_mission)
    assert "annotate_provider_ceiling" in src
    # …et reste derrière les gardes de vérité déjà en place.
    assert src.index("annotate_unpublished_deliverable") < src.index(
        "annotate_provider_ceiling"
    )
    assert src.index("annotate_provider_ceiling") < src.index("mark_done")


def test_la_mesure_ne_fait_jamais_echouer_la_mission():
    """Doctrine Z23 : un garde ne tue pas un run."""
    src = inspect.getsource(R.run_mission)
    i = src.index("ouvrir_compteur")
    assert "except Exception" in src[i - 300:i + 300]
    j = src.index("annotate_provider_ceiling")
    assert "except Exception" in src[j:j + 600]
    assert "finally:" in src[j:j + 900]      # le compteur se referme toujours
