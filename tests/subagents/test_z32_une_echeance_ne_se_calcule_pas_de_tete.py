"""LOT Z32 — une échéance ne se calcule pas de tête.

Run « Cartophare » (2026-08-19). L'utilisateur demande 90 minutes. Au log :

    20:48:28  create_mission … deadline: '2026-08-19T20:50:00'   ← lancée à 20:48
    20:48:36  budget remaining_s=83.5  ratio=0.09
    20:49:18  nudge budget 'half'      ← la moitié partie après UNE recherche
    20:51:33  remaining_s=-93.4        → échéance atteinte
    20:52:07  grâce épuisée → filet dur → mission ANNULÉE

**90 secondes au lieu de 90 minutes.** Et la mission était en train de réussir :

    20:50:47  cartophare\\test_phares.py .....  5 passed in 0.13s   exit:0

Sa pensée disait pourtant « L'échéance est de 90 minutes » : le fait était
compris, il s'est perdu à la conversion. C'est le motif du chantier appliqué au
temps.

**Ce n'est pas un accident** — mesuré deux fois sur deux le même jour :

    « prends 75 minutes » → lancée 17:55, échéance 18:30  =  35 min
    « prends 90 minutes » → lancée 20:48, échéance 20:50  =  90 secondes

et le corpus (108 missions à échéance) en contient une déjà EXPIRÉE à la
création (−227 min).

⚠️ CORRECTION FAITE PENDANT L'AUDIT, avant d'écrire une ligne. J'avais d'abord
conclu à un bug de fuseau, parce que ma mesure disait « 121 minutes » là où le
runtime voyait 90 secondes. Vérification : `mission_budget._iso` produit un ISO
**naïf local**, et le runtime le lit dans la même convention — il a raison. Le
2 h d'écart venait de MA lecture, qui comparait un `deadline_ts` naïf-local à un
`created_at` en UTC. Le défaut de fuseau est donc un problème d'AUDITABILITÉ
(phase 2), pas la cause des 90 secondes. La cause est bien l'arithmétique.
"""

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from src.reasoning.handlers.missions import _z32_deadline_from_duration
from src.subagents.mission_budget import normalize_deadline


# ══════════════════════════════════════════════════════════════════════════════
#  Phase 1 — une durée relative supprime le calcul
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("minutes", [90, "90", 75, 30, 1, 0.5, "90.0", "90,0"])
def test_une_duree_donne_exactement_cette_duree(minutes):
    """LE lot. Aucune interprétation possible : 90 vaut 90."""
    attendu = float(str(minutes).replace(",", "."))
    obtenu = datetime.fromisoformat(_z32_deadline_from_duration(minutes))
    ecart = (obtenu - datetime.now()).total_seconds() / 60.0
    assert abs(ecart - attendu) < 0.5


def test_le_cas_mesure_ne_peut_plus_se_reproduire():
    """« 90 minutes » ne peut plus devenir 90 secondes."""
    obtenu = datetime.fromisoformat(_z32_deadline_from_duration(90))
    assert (obtenu - datetime.now()).total_seconds() > 60 * 85


@pytest.mark.parametrize("mauvais", [0, -5, "abc", "", None, [], {}, "  "])
def test_une_duree_invalide_est_refusee(mauvais):
    """Refuser, c'est laisser le paramètre `deadline` reprendre la main —
    jamais poser une échéance absurde en silence."""
    assert _z32_deadline_from_duration(mauvais) is None


def test_une_duree_delirante_est_refusee():
    """Borne de garde : au-delà de 30 jours, c'est une erreur de saisie."""
    assert _z32_deadline_from_duration(60 * 24 * 31) is None
    assert _z32_deadline_from_duration(60 * 24 * 29) is not None


def test_la_convention_produite_est_celle_du_runtime():
    """Point non négociable : `mission_budget` travaille en ISO NAÏF LOCAL.
    Produire un aware ici rejouerait exactement le décalage de 2 h qui m'a
    trompé pendant l'audit."""
    produit = _z32_deadline_from_duration(90)
    assert datetime.fromisoformat(produit).tzinfo is None


def test_la_duree_traverse_normalize_deadline_sans_deriver():
    """La valeur produite doit survivre au normaliseur du runtime."""
    produit = _z32_deadline_from_duration(90)
    renormalise = normalize_deadline(produit)
    assert renormalise is not None
    ecart = abs(
        (datetime.fromisoformat(renormalise) - datetime.fromisoformat(produit))
        .total_seconds()
    )
    assert ecart < 2


# ══════════════════════════════════════════════════════════════════════════════
#  Phase 3 — une échéance déjà passée est refusée
# ══════════════════════════════════════════════════════════════════════════════


_MISSIONS = Path("src/reasoning/handlers/missions.py").read_text(encoding="utf-8")


def test_le_refus_d_une_echeance_expiree_existe():
    assert "Échéance déjà expirée" in _MISSIONS
    assert "_z32_delta_min <= 0" in _MISSIONS


def test_le_refus_dit_comment_corriger():
    """Un refus sans issue fait tourner le modèle en rond."""
    i = _MISSIONS.index("Échéance déjà expirée")
    assert "duree_minutes" in _MISSIONS[i:i + 500]


def test_une_echeance_courte_mais_future_passe():
    """On ne décide pas à la place de l'utilisateur de ce qui est « trop
    court » : seule l'échéance IMPOSSIBLE est refusée."""
    i = _MISSIONS.index("_z32_delta_min <= 0")
    bloc = _MISSIONS[i - 400:i + 100]
    assert "<= 0" in bloc
    assert "< 5" not in bloc and "< 10" not in bloc


def test_le_refus_arrive_avant_la_creation():
    """Refuser après avoir lancé la mission ne servirait à rien."""
    i_refus = _MISSIONS.index("Échéance déjà expirée")
    i_creation = _MISSIONS.index("create_and_launch(")
    assert i_refus < i_creation


# ══════════════════════════════════════════════════════════════════════════════
#  Phase 0 — l'échéance devient lisible
# ══════════════════════════════════════════════════════════════════════════════


def test_le_delta_en_minutes_est_journalise():
    """« échéance : 2026-08-19T20:50:00 » est exact et parfaitement inutile pour
    repérer qu'il reste 90 secondes. Le delta est la seule grandeur qui parle."""
    assert "[Z32] échéance reçue={!r} → interprétée={} → {:+.1f} min" in _MISSIONS


def test_la_source_de_l_echeance_est_journalisee():
    """Savoir si elle vient d'une durée ou d'un horodatage calculé, c'est savoir
    s'il faut soupçonner l'arithmétique."""
    i = _MISSIONS.index("[Z32] échéance reçue")
    assert "duree_minutes" in _MISSIONS[i:i + 400]
    assert "horodatage" in _MISSIONS[i:i + 400]


def test_la_journalisation_ne_peut_pas_casser_la_creation():
    i = _MISSIONS.index("_z32_delta_min = None")
    bloc = _MISSIONS[i:i + 1200]
    assert "except Exception" in bloc


# ══════════════════════════════════════════════════════════════════════════════
#  Phase 2 — le stockage devient auditable, sans casser le runtime
# ══════════════════════════════════════════════════════════════════════════════


_MANAGER = Path("src/subagents/manager.py").read_text(encoding="utf-8")


def test_un_champ_non_ambigu_est_ajoute():
    assert 'meta["deadline_utc"]' in _MANAGER
    assert ".astimezone().isoformat()" in _MANAGER


def test_deadline_ts_n_est_PAS_converti():
    """LE garde anti-régression du lot. `deadline_ts` est lu par les workers
    (héritage d'échéance) et par le préambule de contrat, en naïf local. Le
    convertir casserait le runtime pour réparer un problème de LECTURE."""
    i = _MANAGER.index('meta["deadline_ts"] = _dts')
    bloc = _MANAGER[i:i + 1400]
    assert 'meta["deadline_ts"] = _dts.astimezone' not in bloc
    assert _MANAGER.count('meta["deadline_ts"] = _dts') == 1


def test_le_champ_ajoute_designe_le_meme_instant():
    """Un champ auditable qui désigne un autre moment serait pire que rien."""
    naif = _z32_deadline_from_duration(90)
    aware = datetime.fromisoformat(naif).astimezone()
    assert aware.tzinfo is not None
    assert abs(
        aware.timestamp() - datetime.fromisoformat(naif).timestamp()
    ) < 1


def test_l_ajout_ne_peut_pas_casser_la_creation():
    i = _MANAGER.index('meta["deadline_utc"]')
    assert "except Exception" in _MANAGER[i - 200:i + 300]


# ══════════════════════════════════════════════════════════════════════════════
#  Le modèle doit SAVOIR que le paramètre existe
# ══════════════════════════════════════════════════════════════════════════════


def test_le_parametre_est_declare_dans_l_outil():
    """Un paramètre non déclaré au schéma n'existe pas pour le modèle : le lot
    entier serait inerte."""
    # Ancre sur la DECLARATION de l'outil, pas sur `handler_name=` du corps.
    i = _MISSIONS.index("Enregistre une mission et la lance en ARRIÈRE-PLAN")
    bloc = _MISSIONS[i:i + 2500]
    assert '"duree_minutes"' in bloc
    assert '"type": "number"' in bloc


def test_la_description_dit_de_ne_pas_calculer():
    # Ancre sur la DECLARATION de l'outil, pas sur `handler_name=` du corps.
    i = _MISSIONS.index("Enregistre une mission et la lance en ARRIÈRE-PLAN")
    bloc = _MISSIONS[i:i + 2500]
    assert "Ne calcule" in bloc and "JAMAIS" in bloc


def test_la_description_porte_les_chiffres_mesures():
    """Les deux cas réels valent mieux qu'une consigne abstraite."""
    # Ancre sur la DECLARATION de l'outil, pas sur `handler_name=` du corps.
    i = _MISSIONS.index("Enregistre une mission et la lance en ARRIÈRE-PLAN")
    bloc = _MISSIONS[i:i + 2500]
    assert "90 SECONDES" in bloc


def test_la_duree_prime_sur_l_horodatage():
    """Si le modèle fournit les deux, c'est le déterministe qui gagne."""
    i = _MISSIONS.index("_z32_from_duration = _z32_deadline_from_duration(duree_minutes)")
    bloc = _MISSIONS[i:i + 200]
    assert "deadline = _z32_from_duration" in bloc


def test_la_raison_du_lot_est_datee_dans_le_code():
    entete = _MISSIONS[_MISSIONS.index("LOT Z32 phase 1"):][:1600]
    assert "Cartophare" in entete
    assert "90 SECONDES" in entete
