r"""Lot PROV-1 - un fournisseur définitivement refusé cesse d'être retenté.

--- Ce que les logs des deux machines ont mesuré (24-25/09) ---

| Erreur | Machine A | Machine B | Nature |
|---|---|---|---|
| Moonshot HTTP 429 | 21 | 6 | quota OU limitation de debit |
| xAI HTTP 403 | 7 | 2 | permission refusee — DEFINITIF |
| NVIDIA NIM HTTP 410 | 7 | 2 | Gone — DEFINITIF |

**18 tentatives perdues** sur les seuls codes definitifs, et les MEMES sur les deux
instances : rien ne desarme un fournisseur mort, il est retente a chaque message.

--- Le diagnostic, et mes deux erreurs en route ---

Tout le mecanisme existe (`provider_quota.py` : `marquer_quota_epuise`, `quota_epuise`,
`raison_quota`, signatures de quota) et `choisir_escalade` consulte bien `quota_epuise`.
**Mais `marquer_quota_epuise` n'est appelee par personne** hors de son module.

J'ai d'abord dit que `error_msg = str(e)` (`multi_provider.py` l.1393) jetait le corps
HTTP. Puis, voyant les logs afficher le JSON, j'ai annonce le contraire. **Les deux fois
j'ai raisonne au lieu de mesurer.** La mesure tranche :

    403 -> str(e) contient le code ? True   | contient le CORPS ? False
    410 -> str(e) contient le code ? True   | contient le CORPS ? False

Le corps visible dans les journaux y arrive par le `logger.error` de CHAQUE provider
(l.2280, 2371, 2437, 2532, 2750...), qui le lit, l'affiche et le jette. Il n'atteint
jamais `error_msg`. Mais **le code HTTP, lui, est dans `str(e)`** — et c'est tout ce
qu'il faut ici.

--- Le perimetre, reduit a ce qui est prouvable ---

Ce lot desarme sur **403** et **410** uniquement : des faits non ambigus.

**Le 429 reste dehors, deliberement.** `provider_quota.py` le dit lui-meme : « une
limitation de debit arrive elle aussi en 429, mais elle est transitoire et ne doit jamais
desarmer un fournisseur sain ». Et aucune des trois erreurs reelles n'est reconnue par
les signatures de quota existantes. Elargir sans posseder un corps complet risquerait de
desarmer un fournisseur en bonne sante — le contraire du but.
"""
from __future__ import annotations

import httpx
import pytest

from src.llm import provider_quota as PQ


@pytest.fixture(autouse=True)
def _etat_propre():
    """`_epuises` est un etat de MODULE : sans nettoyage, un test contamine le suivant."""
    with PQ._verrou:
        PQ._epuises.clear()
    yield
    with PQ._verrou:
        PQ._epuises.clear()


def _erreur(code: int, corps: str = "{}") -> httpx.HTTPStatusError:
    requete = httpx.Request("POST", "https://api.exemple.test/v1/chat")
    reponse = httpx.Response(code, request=requete, text=corps)
    try:
        reponse.raise_for_status()
    except httpx.HTTPStatusError as exc:
        return exc
    raise AssertionError("cette reponse aurait du lever")


# -- 1. Les codes definitifs desarment --------------------------------------

@pytest.mark.parametrize("code", [402, 403, 410])
def test_un_refus_definitif_est_reconnu(code):
    assert PQ.ressemble_a_un_acces_definitivement_refuse(str(_erreur(code))) is True


def test_le_fournisseur_est_reellement_desarme():
    """Le bout du fil : `choisir_escalade` consulte `quota_epuise`."""
    assert PQ.quota_epuise("xai") is False
    PQ.marquer_quota_epuise("xai", "HTTP 403 permission refusee")
    assert PQ.quota_epuise("xai") is True
    assert "403" in (PQ.raison_quota("xai") or "")


def test_le_desarmement_ne_touche_QUE_le_fournisseur_vise():
    PQ.marquer_quota_epuise("nvidia", "HTTP 410")
    assert PQ.quota_epuise("nvidia") is True
    assert PQ.quota_epuise("moonshot") is False


def test_le_402_du_run_du_29_09_est_reconnu():
    """Mesure du 29/09 a 01 h 39, en pleine session vocale :

        ❌ Erreur deepseek (HTTPStatusError): Client error '402 Payment Required'
        🔄 Fallback vers mistral/mistral-large-latest...

    Les credits DeepSeek etaient epuises. Un 402 est aussi definitif qu'un 403 pour la
    session en cours : insister ne fait que payer le round-trip a chaque message.

    « Definitif » s'entend ici au sens de `marquer_quota_epuise` : desarme POUR CETTE
    SESSION. Recharger le compte et redemarrer rearme le fournisseur — c'est le bon
    niveau de granularite, et c'est deja le comportement du mecanisme.
    """
    message = str(_erreur(402, '{"error":{"message":"Insufficient Balance"}}'))
    assert PQ.ressemble_a_un_acces_definitivement_refuse(message) is True


# -- 2. Ce qui ne doit PAS desarmer -----------------------------------------

def test_le_429_ne_desarme_PAS():
    """Decision de perimetre, ecrite dans `provider_quota.py` : une limitation de debit
    arrive aussi en 429 et elle est TRANSITOIRE. Desarmer dessus couperait un
    fournisseur sain — l'inverse du but du lot."""
    assert PQ.ressemble_a_un_acces_definitivement_refuse(str(_erreur(429))) is False


@pytest.mark.parametrize("code", [500, 502, 503, 504, 408])
def test_les_erreurs_transitoires_ne_desarment_pas(code):
    """5xx = le fournisseur a un souci passager. 408 = timeout. On retente."""
    assert PQ.ressemble_a_un_acces_definitivement_refuse(str(_erreur(code))) is False


@pytest.mark.parametrize("code", [400, 401, 404, 422])
def test_les_autres_4xx_ne_desarment_pas(code):
    """401 peut etre une cle a renouveler, 400/422 une requete malformee de NOTRE cote,
    404 un modele mal nomme. Aucun ne prouve que le fournisseur est mort."""
    assert PQ.ressemble_a_un_acces_definitivement_refuse(str(_erreur(code))) is False


def test_un_message_vide_ne_desarme_rien():
    assert PQ.ressemble_a_un_acces_definitivement_refuse("") is False
    assert PQ.ressemble_a_un_acces_definitivement_refuse(None) is False


def test_le_code_doit_etre_un_code_HTTP_pas_un_nombre_qui_traine():
    """« Erreur sur la ligne 403 du fichier » ne doit pas desarmer un fournisseur."""
    assert PQ.ressemble_a_un_acces_definitivement_refuse(
        "Erreur de parsing a la ligne 403 du fichier config") is False
    assert PQ.ressemble_a_un_acces_definitivement_refuse(
        "token budget 410 depasse") is False


# -- 3. Ce que le lot ne doit PAS changer -----------------------------------

def test_la_detection_de_QUOTA_reste_intacte():
    """Deux mecanismes distincts : le quota (corps explicite) et l'acces definitivement
    refuse (code HTTP). Le premier ne doit pas bouger."""
    assert PQ.ressemble_a_un_quota_epuise("insufficient_quota") is True
    assert PQ.ressemble_a_un_quota_epuise("credit_balance_exhausted") is True
    assert PQ.ressemble_a_un_quota_epuise("rate limit exceeded") is False


def test_les_erreurs_reelles_du_25_09_restent_non_reconnues_comme_quota():
    """Mesure consignee : aucune des trois n'est un quota au sens des signatures. C'est
    POUR CELA que ce lot passe par le code HTTP et non par le corps."""
    for fragment in ('{"error":{"message":"Your account org-d7f7a',
                     '{"code":"permission-denied","error":"Your newly ',
                     '{"type":"about:blank","title":"Gone","sta'):
        assert PQ.ressemble_a_un_quota_epuise(fragment) is False, fragment


# -- 4. Le branchement : sans lui, le lot est du code mort ------------------

def test_multi_provider_APPELLE_bien_la_detection():
    """Le defaut d'origine est precisement qu'une detection complete n'etait appelee par
    personne. Ce test refuse que le lot reproduise ce motif."""
    import inspect

    from src.llm import multi_provider

    source = inspect.getsource(multi_provider)
    assert "ressemble_a_un_acces_definitivement_refuse" in source, (
        "la detection n'est appelee nulle part : PROV-1 serait du code mort, "
        "exactement comme `marquer_quota_epuise` avant ce lot"
    )
    assert "marquer_quota_epuise" in source, source[:1] and "marquer_quota_epuise absent"
