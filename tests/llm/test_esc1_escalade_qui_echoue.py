"""LOT ESC-1 — une escalade qui echoue revient au modele qui marchait.

--- Le fait fondateur, releve dans un run reel du 21 septembre 2026 ---

Un CodeAgent produisait un site sous `deepseek-flash` depuis six minutes. Son
premier essai a fini en `status=error`, la politique de retry a escalade vers
`gpt-5.4-mini`, et cette escalade est tombee sur un compte OpenAI **sans
credits** (429 `insufficient_quota`, trois fois), puis sur un abonnement Codex
epuise. Le repli a ensuite suivi `MODEL_FALLBACKS['gpt-5.4-mini']`, qui ne
contient qu'une entree : `nvidia-gpt-oss-20b`.

Resultat : on escalade pour obtenir un modele PLUS FORT, et on atterrit sur un
modele plus FAIBLE que celui qui fonctionnait. `deepseek-flash`, disponible et
productif, n'est jamais reconsidere.

--- Trois causes mesurees ---

1. `check_api_key` ne verifie que la presence d'une variable d'environnement
   (`providers.py`) : une cle valide sans credit passe pour disponible.
2. Le desarmement « solde epuise » existe, mais pour Z.AI SEULEMENT
   (`_zai_balance_exhausted`). OpenAI n'a pas d'equivalent.
3. `provider_health` et son disjoncteur (CLOSED/HALF_OPEN, cooldown) existent
   deja dans `multi_provider.py` — l'escalade ne les consulte jamais.
"""
from __future__ import annotations


def test_un_provider_a_sec_est_memorise_pour_la_session():
    """Le desarmement ne doit plus etre reserve a un seul fournisseur."""
    from src.llm.provider_quota import marquer_quota_epuise, quota_epuise, reinitialiser

    reinitialiser()
    assert quota_epuise("openai") is False
    marquer_quota_epuise("openai", "credit_balance_exhausted")
    assert quota_epuise("openai") is True, (
        "un compte sans credit reste considere comme disponible : l'escalade y "
        "retournera a chaque tentative"
    )
    assert quota_epuise("deepseek") is False, "le desarmement ne doit pas deborder"
    reinitialiser()


def test_l_escalade_ignore_un_provider_a_sec():
    """Escalader vers un compte vide n'est pas une escalade, c'est une panne."""
    from src.llm.escalation_policy import choisir_escalade

    retenu = choisir_escalade(1, disponible=lambda modele: False)
    assert retenu is None, (
        "l'escalade retient un candidat alors qu'aucun n'est reellement utilisable"
    )
    retenu = choisir_escalade(1, disponible=lambda modele: modele.startswith("claude"))
    assert retenu is not None and retenu.startswith("claude"), (
        "l'escalade doit pouvoir sauter un fournisseur a sec au profit d'un autre"
    )


def test_une_escalade_ratee_revient_au_modele_d_origine():
    """Sans candidat, on garde ce qui marchait — on ne descend pas plus bas."""
    from src.llm.escalation_policy import modele_apres_escalade

    assert modele_apres_escalade(origine="deepseek-flash", escalade=None) == "deepseek-flash", (
        "faute de candidat, le modele qui produisait du travail doit etre conserve"
    )
    assert modele_apres_escalade(origine="deepseek-flash", escalade="claude-opus-4.6") == "claude-opus-4.6"
    assert modele_apres_escalade(origine=None, escalade=None) is None


def test_un_rate_limit_passager_ne_desarme_jamais_un_fournisseur():
    """Distinguer « trop de requetes » de « plus de credit ».

    Les deux arrivent en 429. Desarmer sur le premier couperait un fournisseur
    sain pour toute la session — un degat pire que le defaut corrige ici.
    """
    from src.llm.provider_quota import ressemble_a_un_quota_epuise

    # Credit reellement epuise : signatures relevees dans les logs de production.
    assert ressemble_a_un_quota_epuise('{"code":"insufficient_quota"}') is True
    assert ressemble_a_un_quota_epuise("You have no credits remaining.") is True
    assert ressemble_a_un_quota_epuise("Your credit balance is too low to access the API") is True
    assert ressemble_a_un_quota_epuise('"code":"credit_balance_exhausted"') is True
    assert ressemble_a_un_quota_epuise('{"type":"exceeded_current_quota_error"}') is True
    assert ressemble_a_un_quota_epuise('{"code":"1113"}') is True

    # Limitation de debit : transitoire, le fournisseur reste utilisable.
    assert ressemble_a_un_quota_epuise("Rate limit reached for gpt-5.4-mini") is False
    assert ressemble_a_un_quota_epuise("Too Many Requests") is False
    assert ressemble_a_un_quota_epuise("") is False
    assert ressemble_a_un_quota_epuise(None) is False


def test_le_repli_d_une_escalade_ne_descend_pas_sous_son_point_de_depart():
    """Escalader puis retomber plus bas qu'au depart n'a aucun sens.

    Mesure du 21 septembre : `gpt-5.4-mini` n'avait qu'un seul repli,
    `nvidia-gpt-oss-20b`, et les deux modeles Anthropic d'escalade n'en avaient
    AUCUN. Un modele capable doit figurer dans ce repli, avant les petits.
    """
    from src.llm.providers import get_model_fallbacks

    for modele in ("gpt-5.4-mini", "gpt-5.4", "claude-sonnet-4.6", "claude-opus-4.6"):
        replis = get_model_fallbacks(modele)
        assert replis, f"{modele} n'a aucun repli : un echec le fait tomber hors catalogue"
        assert any("deepseek" in r for r in replis), (
            f"le repli de {modele} ne contient aucun modele capable : {replis}"
        )
        premier = replis[0]
        assert "deepseek" in premier, (
            f"le premier repli de {modele} est {premier!r} : on descend avant d'avoir "
            "essaye un modele de meme niveau"
        )
