r"""LOT A6 — le lead connaît enfin son parallélisme RÉEL avant de découper.

--- Le défaut, constaté dans le run réel du 2026-10-07 à 23:29:02 ---

Après avoir posé son contrat, Lumena a lu ceci :

    ⏱️ Ce que ce découpage coûte : ton contrat crée 4 workers. Ils réfléchissent
    en parallèle, mais le CodeAgent est unique et sérialisé — ils coderont
    chacun leur tour. [...] soit environ 27 min de file d'attente ici.

**27 minutes de file annoncées. Zéro file réelle. Mission bouclée en 5 minutes.**

Le lot A4 (pool de CodeAgent, signé le 2026-10-08) a rendu cette note fausse. Laissée
en l'état, elle **dissuade de déléguer** alors que déléguer est devenu bien moins cher —
exactement ce que le plan du 02/09 avait anticipé : « le lot 2 invalide
`delegation_cost.py` ; recalculer la note DANS ce lot ».

--- Ce que ce lot donne au lead, et qu'il ignorait ---

1. **le parallélisme RÉEL** = `min(workers, pool CodeAgent, créneaux LLM)`.
   Ce n'est PAS le nombre de workers. Avec `PROVIDER_CONCURRENCY=2`, lancer 10
   workers de code produit 8 attentes, pas 10 exécutions.
2. **le coût en tokens** : un worker = un prompt complet (~16 k). Jamais chiffré.
3. **le couplage** des fichiers (déjà présent au LOT P1, conservé).
4. **les workers qui ne codent pas** (66 % des cas mesurés) : eux parallélisent
   vraiment, ils ne paient pas le créneau CodeAgent.

--- Pureté préservée ---

`delegation_cost.py` est auto-contenu (stdlib) et testable sans runtime : il REÇOIT
les plafonds, il ne les lit pas. C'est l'appelant (`handlers/missions.py`) qui les
résout. Ce lot ne change pas ce contrat.
"""
from __future__ import annotations

import pytest

from src.subagents.delegation_cost import delegation_cost_note


# ── 1. Le fait n°1 : le parallélisme réel ────────────────────────────────────


class TestParallelismeReel:
    def test_le_parallelisme_reel_est_annonce(self):
        """4 workers, 2 créneaux LLM → le lead doit lire « 2 », pas « 4 »."""
        note = delegation_cost_note(4, 4, parallelisme_reel=2)
        assert "2" in note
        assert "parallélisme réel" in note.lower() or "en parallèle" in note.lower()

    def test_le_goulot_est_NOMME(self):
        """Savoir qu'on est bridé ne suffit pas : il faut savoir PAR QUOI,
        sinon on ne peut pas le desserrer."""
        note = delegation_cost_note(6, 6, parallelisme_reel=2, plafond_llm=2, pool=10)
        bas = note.lower()
        assert "llm" in bas or "créneau" in bas

    def test_le_CodeAgent_n_est_PLUS_annonce_comme_serialise(self):
        """⚠️ LE défaut que ce lot ferme. A4 (2026-10-08) a supprimé la file
        d'attente du CodeAgent : l'annoncer encore serait un mensonge mesurable."""
        note = delegation_cost_note(4, 4, parallelisme_reel=2)
        bas = note.lower()
        assert "chacun leur tour" not in bas
        assert "unique et sérialisé" not in bas

    @pytest.mark.parametrize("workers,para,attendu_vagues", [
        (4, 2, 2),    # 4 workers, 2 creneaux -> 2 vagues
        (6, 2, 3),
        (3, 3, 1),    # autant de creneaux que de workers -> une seule vague
        (10, 2, 5),
    ])
    def test_le_nombre_de_vagues_est_juste(self, workers, para, attendu_vagues):
        note = delegation_cost_note(workers, workers, parallelisme_reel=para)
        if attendu_vagues > 1:
            assert str(attendu_vagues) in note

    def test_sans_plafond_fourni_la_note_reste_utilisable(self):
        """Rétrocompatibilité : l'appelant qui ne résout pas les plafonds doit
        quand même obtenir une note sensée (aucun appelant cassé)."""
        note = delegation_cost_note(4, 4)
        assert note
        assert "4 workers" in note


# ── 2. Le fait n°2 : le coût en tokens ───────────────────────────────────────


class TestCoutEnTokens:
    def test_le_cout_en_tokens_est_chiffre(self):
        """Un worker = un prompt complet (~16 k). Jamais dit au lead avant ce lot."""
        note = delegation_cost_note(4, 4, parallelisme_reel=2)
        assert "token" in note.lower()

    def test_le_cout_croit_avec_les_workers(self):
        n2 = delegation_cost_note(2, 4, parallelisme_reel=2)
        n8 = delegation_cost_note(8, 8, parallelisme_reel=2)
        import re

        def _k(t):
            # Le TOTAL, entre `**` — pas le `~16 k` du coût unitaire qui le précède.
            m = re.search(r"\*\*(\d+)\s*k tokens\*\*", t)
            return int(m.group(1)) if m else 0

        assert _k(n8) > _k(n2) > 0


# ── 3. Le fait n°4 : les workers qui ne codent pas ───────────────────────────


class TestWorkersSansCode:
    def test_les_workers_non_codeurs_sont_signales_comme_moins_chers(self):
        """66 % des workers mesurés ne touchent jamais le CodeAgent : un découpage
        recherche/rédaction est quasi gratuit là où du code fait la queue."""
        note = delegation_cost_note(4, 4, parallelisme_reel=2)
        bas = note.lower()
        assert "codent pas" in bas or "non-code" in bas or "ne code" in bas


# ── 4. Ce que le lot ne doit PAS changer (acquis du LOT P1) ─────────────────


class TestAcquisP1Preserves:
    @pytest.mark.parametrize("owners", [0, 1])
    def test_silencieux_a_zero_ou_un_worker(self, owners):
        """AUD-017 — ne pas saturer les gardes : sans découpage, pas de note."""
        assert delegation_cost_note(owners, 4, parallelisme_reel=2) == ""

    def test_un_worker_par_fichier_reste_signale(self):
        """Le cas mesuré à 40 % des contrats : acquis du LOT P1, conservé."""
        note = delegation_cost_note(5, 5, parallelisme_reel=2)
        assert "un worker par fichier" in note
        assert "même" in note and "owner" in note

    def test_un_contrat_deja_regroupe_n_est_pas_sermonne(self):
        note = delegation_cost_note(2, 4, parallelisme_reel=2)
        assert note
        assert "un worker par fichier" not in note

    @pytest.mark.parametrize("mauvais", [None, "abc", "", [], {}])
    def test_une_entree_illisible_ne_casse_jamais(self, mauvais):
        """Une note d'aide ne doit JAMAIS faire tomber une mission."""
        assert isinstance(delegation_cost_note(mauvais, 4), str)
        assert isinstance(delegation_cost_note(4, mauvais), str)
        assert isinstance(delegation_cost_note(4, 4, parallelisme_reel=mauvais), str)

    def test_le_module_reste_pur(self):
        """Il REÇOIT les plafonds, il ne les lit pas : testable sans runtime,
        et aucun import de `os`/`asyncio` ne doit apparaître."""
        import inspect

        import src.subagents.delegation_cost as mod

        src = inspect.getsource(mod)
        assert "import os" not in src
        assert "getenv" not in src
