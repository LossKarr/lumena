r"""LOT CTX-1 — la compaction du CodeAgent suit la fenêtre du MODÈLE ACTIF.

--- Le défaut, mesuré le 2026-10-09 ---

`_CONTEXT_WINDOW_TOKENS = 65536` était figé dans `sub_agent.py:1700`, et c'est lui qui
déclenche la compaction de l'historique du CodeAgent :

    _compact_ratio = 0.60 si mode "modify" sinon 0.85
    if _est_tokens > _CONTEXT_WINDOW_TOKENS * _compact_ratio:  → on compacte

Or les modèles réellement configurés chez Charles :

    deepseek-flash   contexte = 1 000 000      ← modèle PAR DÉFAUT
    deepseek-v4-pro  contexte = 1 000 000
    deepseek-v3      contexte =   128 000
    deepseek-reasoner contexte=   128 000  (sortie max 65 536 ← d'où venait le chiffre)

**Aucun modèle actif n'a une fenêtre de 65 536.** En mode `modify` — celui de toutes les
missions qui remplissent des stubs — la compaction se déclenchait donc à
**65 536 × 0,60 = 39 322 tokens**, soit **3,9 % de la capacité du modèle**.

Et la compaction est brutale : `head[:2]` + `tail[-6:]`, le reste résumé. Un CodeAgent
qui perd son historique tous les 39 k tokens doit **relire ce qu'il savait déjà**.

--- L'incohérence qui prouve que c'est un oubli, pas un choix ---

Dans LE MÊME FICHIER, 3 000 lignes plus bas (l. 4749), le CodeAgent calibre déjà la
troncature de ses observations sur le modèle réel :

    _max_ctx = int(getattr(llm, "context_window", 0) or 0)
    _budget = _compute_obs_limit(_max_ctx)

D'un côté il s'adapte, de l'autre il ignore. Ce lot aligne les deux.

--- Ce que la variable d'environnement devient ---

`LUMENA_CODE_AGENT_CONTEXT_WINDOW` cesse d'être la SOURCE et devient une **bride
volontaire** : si elle est posée, elle prime (pour borner le coût ou tester) ; sinon le
modèle décide.

⚠️ **Piège réel découvert en préparant ce lot** : la variable VIDE faisait
`int("")` → `ValueError` **au niveau module**, donc **Lumena ne démarrait plus**.
Un test ci-dessous fige ce cas.
"""
from __future__ import annotations

import pytest

from src.agents.sub_agent import _CONTEXT_WINDOW_TOKENS_DEFAUT, _fenetre_contexte_codeagent


class _LLM:
    """Double minimal : seul `context_window` compte ici."""

    def __init__(self, ctx):
        self.context_window = ctx


# ── 1. Le modèle décide ──────────────────────────────────────────────────────


class TestLeModeleDecide:
    @pytest.mark.parametrize("ctx,attendu", [
        (1_000_000, 1_000_000),   # deepseek-flash, le défaut de Charles
        (128_000, 128_000),       # deepseek-v3
        (200_000, 200_000),       # Claude
        (32_000, 32_000),         # Ollama
    ])
    def test_la_fenetre_vient_du_modele(self, monkeypatch, ctx, attendu):
        monkeypatch.delenv("LUMENA_CODE_AGENT_CONTEXT_WINDOW", raising=False)
        assert _fenetre_contexte_codeagent(_LLM(ctx)) == attendu

    def test_le_defaut_ne_bride_plus_un_modele_large(self, monkeypatch):
        """LE défaut que ce lot ferme : 1 M de contexte ne doit plus compacter à 39 k."""
        monkeypatch.delenv("LUMENA_CODE_AGENT_CONTEXT_WINDOW", raising=False)
        fenetre = _fenetre_contexte_codeagent(_LLM(1_000_000))
        seuil_modify = fenetre * 0.60
        assert seuil_modify > 500_000, (
            f"compaction a {seuil_modify:.0f} tokens pour un modele a 1 M — "
            "c'est le defaut d'origine"
        )


# ── 2. La bride volontaire ───────────────────────────────────────────────────


class TestBrideVolontaire:
    def test_la_bride_prime_sur_le_modele(self, monkeypatch):
        """Borner le coût volontairement reste possible — c'est le seul rôle qui
        reste à la variable."""
        monkeypatch.setenv("LUMENA_CODE_AGENT_CONTEXT_WINDOW", "65536")
        assert _fenetre_contexte_codeagent(_LLM(1_000_000)) == 65536

    def test_la_bride_s_applique_meme_sans_llm(self, monkeypatch):
        monkeypatch.setenv("LUMENA_CODE_AGENT_CONTEXT_WINDOW", "120000")
        assert _fenetre_contexte_codeagent(None) == 120000

    def test_une_bride_absurde_est_plancheree(self, monkeypatch):
        """Une bride trop basse rendrait le CodeAgent inutilisable (compaction à
        chaque tour). Plancher de sécurité."""
        monkeypatch.setenv("LUMENA_CODE_AGENT_CONTEXT_WINDOW", "10")
        assert _fenetre_contexte_codeagent(_LLM(1_000_000)) >= 4096


# ── 3. ⚠️ Le piège qui empêchait Lumena de démarrer ─────────────────────────


class TestValeursIllisibles:
    @pytest.mark.parametrize("mauvais", ["", "   ", "abc", "65k", "1_000_000x", "-5"])
    def test_une_bride_illisible_ne_casse_RIEN(self, monkeypatch, mauvais):
        """⚠️ AVANT ce lot : `LUMENA_CODE_AGENT_CONTEXT_WINDOW=` (vide) faisait
        `int("")` → ValueError AU NIVEAU MODULE → **Lumena ne démarrait plus**.

        Mesuré le 2026-10-09 en préparant ce lot. Une valeur illisible doit être
        ignorée, jamais fatale.
        """
        monkeypatch.setenv("LUMENA_CODE_AGENT_CONTEXT_WINDOW", mauvais)
        valeur = _fenetre_contexte_codeagent(_LLM(1_000_000))
        assert valeur == 1_000_000, "une bride illisible doit laisser le modèle décider"

    def test_importer_le_module_ne_leve_jamais(self, monkeypatch):
        """Le vrai scénario du crash : la variable est lue À L'IMPORT."""
        monkeypatch.setenv("LUMENA_CODE_AGENT_CONTEXT_WINDOW", "")
        import importlib

        import src.agents.sub_agent as mod

        importlib.reload(mod)      # ne doit PAS lever
        assert callable(mod._fenetre_contexte_codeagent)


# ── 4. Replis et caractérisation ─────────────────────────────────────────────


class TestReplis:
    @pytest.mark.parametrize("llm", [None, object(), _LLM(0), _LLM(None), _LLM("abc")])
    def test_sans_modele_lisible_on_retombe_sur_le_defaut(self, monkeypatch, llm):
        monkeypatch.delenv("LUMENA_CODE_AGENT_CONTEXT_WINDOW", raising=False)
        assert _fenetre_contexte_codeagent(llm) == _CONTEXT_WINDOW_TOKENS_DEFAUT

    def test_le_defaut_historique_est_conserve(self):
        """65 536 reste le repli : le comportement d'avant pour qui n'a pas de
        `context_window` déclaré."""
        assert _CONTEXT_WINDOW_TOKENS_DEFAUT == 65536

    def test_un_modele_a_65k_se_comporte_comme_avant(self, monkeypatch):
        """Caractérisation : ce lot ne change RIEN pour un modèle de 65 536."""
        monkeypatch.delenv("LUMENA_CODE_AGENT_CONTEXT_WINDOW", raising=False)
        assert _fenetre_contexte_codeagent(_LLM(65536)) == 65536
