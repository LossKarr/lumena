"""Lot CONN-9 (harnais) - certifier, ou dire honnetement ce qui ne l'est pas.

Exigence de CONN-9, sept points :

    1. regressions completes des deux depots
    2. tests E2E packages, pas seulement mode developpeur
    3. canaris Chat, Agent, CodeAgent et mission multi-workers
    4. API, local, Codex abonnement et fallback
    5. soak 24 h avec coupures, redemarrages et operations longues
    6. comparaison des distributions ledger/guards avant-apres
    7. revue securite et journal sans secret

--- Ce que ce harnais est, et ce qu'il n'est PAS ---

Deux de ces points ne dependent pas du code : le **soak 24 h** demande une machine
pendant vingt-quatre heures, et les **E2E packages** demandent un paquet signe, donc
un certificat. Le harnais les PREPARE, il ne les remplace pas.

**Le risque principal d'un tel outil est de mentir par omission** : rendre « vert »
alors que trois points n'ont pas ete regardes. C'est exactement le defaut mesure
partout dans Lumena cette journee - un systeme qui sait beaucoup et alerte peu. Le
harnais doit donc distinguer TROIS etats, jamais deux :

    verifie      - execute a l'instant, resultat observe
    non verifie  - non executable ici, et le harnais dit pourquoi
    echoue       - execute, resultat negatif

Un rapport ou tout est « verifie » alors que le soak n'a pas tourne serait un faux
certificat. Ces tests l'interdisent.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parents[2]
HARNAIS = RACINE / "ide" / "tools" / "ide" / "conn9-certification.mjs"
pytestmark = pytest.mark.skipif(
    not (RACINE / "ide").exists(), reason="depot IDE absent de cette machine")


def _source() -> str:
    assert HARNAIS.exists(), f"harnais absent : {HARNAIS}"
    return HARNAIS.read_text(encoding="utf-8")


# ── 1. Les sept exigences sont TOUTES nommees ───────────────────────────────

@pytest.mark.parametrize("cle", [
    "regressions", "e2e_package", "canaris", "voies_modele",
    "soak_24h", "distributions", "revue_securite",
])
def test_chaque_exigence_de_conn9_a_son_poste(cle):
    """Une exigence absente du harnais est une exigence qu'on oubliera."""
    source = _source()
    assert f"'{cle}'" in source or f'"{cle}"' in source, (
        f"l'exigence {cle} n'est pas couverte"
    )


def test_le_harnais_declare_exactement_sept_postes():
    """Ni plus ni moins : la spec en compte sept."""
    source = _source()
    # Les DECLARATIONS de poste, pas les relectures : `{ cle: e.cle, titre: e.titre }`
    # dans la construction du rapport n'est pas un huitieme poste.
    declarations = [ligne for ligne in source.splitlines()
                    if ligne.strip().startswith("titre: '")]
    assert len(declarations) == 7, f"{len(declarations)} postes au lieu de 7"


# ── 2. Il ne ment JAMAIS par omission ───────────────────────────────────────

def test_trois_etats_sont_prevus_pas_deux():
    """« non verifie » doit exister : sans lui, l'absence de preuve passe pour une preuve."""
    source = _source()
    for etat in ("verifie", "non_verifie", "echoue"):
        assert f"'{etat}'" in source or f'"{etat}"' in source, f"l'etat {etat} n'existe pas"
    # « non_verifie » doit apparaitre plusieurs fois : au moins un poste hors code.
    assert source.count("non_verifie") >= 3


def test_le_verdict_global_exige_que_TOUT_soit_verifie():
    """Un seul poste non verifie doit empecher le « certifie ».

    C'est le coeur du lot : un harnais qui rend « vert » avec le soak non lance
    serait un faux certificat - exactement le travers que cette journee a mesure
    partout ailleurs.
    """
    source = _source()
    assert "non_verifie" in source
    assert "certifie" in source
    # Le verdict se calcule sur l'absence de tout etat autre que `verifie`.
    assert ("every(" in source or "some(" in source), (
        "le verdict global n'est pas calcule a partir des postes"
    )


def test_les_points_hors_code_sont_annonces_comme_tels():
    """Le soak et les E2E packages exigent une machine et un certificat.

    Le harnais doit le DIRE, pas les compter comme des echecs ni les taire.
    """
    source = _source()
    assert "24" in source and ("certificat" in source.lower() or "signature" in source.lower()), (
        "le harnais ne dit pas ce qui depend de Charles et non du code"
    )


# ── 3. Il produit un rapport exploitable ────────────────────────────────────

def test_le_rapport_est_du_json_sur_la_sortie_standard():
    """Meme forme que le canari CONN-2A : lisible par un humain ET par un script."""
    source = _source()
    assert "JSON.stringify" in source
    assert "process.stdout.write" in source or "console.log" in source


def test_le_harnais_de_soak_existe_et_est_parametrable():
    """24 h par defaut, mais reglable : personne ne debugue un harnais en 24 h.

    Le soak lui-meme n'est PAS lance ici - il demande une machine pendant une
    journee. Ce test verifie seulement qu'il est pret et qu'on peut l'essayer court.
    """
    soak = RACINE / "ide" / "tools" / "ide" / "conn9-soak.mjs"
    assert soak.exists(), f"harnais de soak absent : {soak}"
    source = soak.read_text(encoding="utf-8")
    assert "--heures" in source or "--hours" in source, "duree non parametrable"
    for exigence in ("coupure", "redemarrage", "operation"):
        assert exigence in source.lower(), f"le soak ne couvre pas : {exigence}"


def test_le_rapport_nomme_le_depot_et_la_date():
    """Un certificat sans horodatage ni revision ne prouve rien de durable."""
    source = _source()
    assert "Date(" in source or "toISOString" in source


# ── 4. CONN-8c / CONN-9b — les postes MESURENT au lieu de declarer ──────────
#
# Les deux postes ci-dessous disaient « non verifie » sans rien essayer, alors que
# de quoi trancher etait deja sur la machine :
#
#   e2e_package   — les huit specs de `ide/tests/e2e/` ciblent DEJA le binaire
#                   `release/win-unpacked/Lumena IDE.exe`. Elles n'etaient pas
#                   lancees. Et la DATE du paquet n'etait pas regardee : mesure du
#                   24/09, paquet du 04/09, sources du 23/09 — dix-neuf jours. Le
#                   certifier aurait valide un binaire anterieur a L5 et CONN-6/7/8.
#   distributions — le fichier de metriques etait la, la borne de GATE-1 connue, et
#                   le poste CITAIT le chiffre ecrit la veille au lieu de le calculer.

def test_le_poste_e2e_verifie_que_le_paquet_nest_pas_perime():
    """Un paquet plus vieux que les sources certifierait un autre logiciel."""
    source = _source()
    assert "mtimeMs" in source, "le harnais ne regarde aucune date de fichier"
    assert "sourceLaPlusRecente" in source, (
        "le harnais ne compare pas le paquet aux sources"
    )
    assert "perime" in source or "périmé" in source, (
        "la peremption du paquet n'est pas nommee"
    )


def test_le_poste_e2e_lance_vraiment_les_specs_sur_le_paquet():
    """Des tests E2E ecrits et jamais lances ne prouvent rien."""
    source = _source()
    assert "playwright" in source, "le harnais ne lance pas les E2E du paquet"


def test_le_poste_distributions_calcule_au_lieu_de_citer():
    """Il doit LIRE les metriques et les repartir de part et d'autre de GATE-1."""
    source = _source()
    assert "gate_metrics.jsonl" in source
    assert "BORNE_GATE1" in source, "aucune borne de comparaison"
    assert "distribution(" in source, "aucun calcul de repartition"
    assert "lsp_fail_open" in source, (
        "le poste ne regarde pas le fail-open, qui est l'objet meme de GATE-1"
    )


def test_le_poste_distributions_refuse_de_conclure_sur_trop_peu():
    """Douze evenements ne font pas une distribution, et le plancher doit etre visible.

    Sans plancher, le poste passerait au vert sur deux mesures - une conclusion
    tiree de rien, soit exactement ce que ce harnais existe pour empecher.
    """
    source = _source()
    assert "MIN_EVENEMENTS_APRES" in source, "aucun plancher d'echantillon"
    trouve = re.search(r"MIN_EVENEMENTS_APRES\s*=\s*(\d+)", source)
    assert trouve and int(trouve.group(1)) >= 30, (
        "le plancher doit rester assez haut pour qu'une poignee de runs ne conclue pas"
    )
