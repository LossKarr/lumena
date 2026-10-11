r"""PHASE PRÉSENCE — la couture unique, et le budget qui la tient.

--- Pourquoi un composeur plutôt qu'un branchement par lot ---

Trois gels plafonnent `src/reasoning/react.py` à **9 718 lignes** : CONN-4d (nommé
`only_shrink`), CONN-5a et CONN-7a. Et le docstring de CONN-7a dit la discipline
attendue, noir sur blanc :

    « Première tentative : +3 lignes, deux gels tombés — la voie a été refaite plutôt
      que les gels relevés. »

Premier branchement de S1 : **+18 lignes, les trois gels tombés**. La voie a été
refaite. `react.py` appelle `bloc_presence(self)` sur une ligne qu'il avait DÉJÀ —
l'assemblage final de son contexte d'identité — et le nom voyage sur une ligne
d'import existante. Coût : **zéro ligne**. Les lots suivants (S4, S3) s'ajoutent dans
`presence.py`, sans jamais rouvrir `react.py`.

--- Le budget ---

Le prompt mesuré le 2026-10-09 faisait **5 295 tokens, dont 67 % de conversation** :
trois heartbeats transcrits en entier, neuf alertes disque périmées — pour une demande
qui était « génère moi un site web ». On remplace du bavardage périmé par de l'état
frais : **on n'empile pas**. 500 caractères pour toute la phase.
"""
from __future__ import annotations

import pathlib

import pytest

from src.telemetry.presence import BUDGET_CARACTERES, bloc_presence

RACINE = pathlib.Path(__file__).resolve().parents[2]


# -- 1. La couture est bien celle que react.py appelle -----------------------


class TestLaCoutureEstUnique:
    def test_le_nom_est_exporte_par_react_config(self):
        """`react_config.py` est déjà importé par `react.py` : le nom y transite."""
        from src.reasoning.react_config import bloc_presence as via_config

        assert callable(via_config)

    def test_react_py_appelle_le_composeur(self):
        """Un bloc que personne n'assemble est un bloc qui n'existe pas."""
        source = (RACINE / "src/reasoning/react.py").read_text(encoding="utf-8")
        assert "bloc_presence(self)" in source

    def test_react_py_n_a_PAS_grossi_pour_la_phase_PRESENCE(self):
        """Le gel des trois lots CONN, réaffirmé ici pour la phase PRÉSENCE.

        Si ce test tombe, la phase a recommencé à écrire dans `react.py` : la logique
        doit redescendre dans `src/telemetry/`, pas le plafond monter.
        """
        source = (RACINE / "src/reasoning/react.py").read_text(encoding="utf-8")
        assert len(source.splitlines()) <= 9718

    def test_un_seul_point_d_injection_dans_react_py(self):
        """Deux points d'injection = deux endroits où le budget peut déraper."""
        source = (RACINE / "src/reasoning/react.py").read_text(encoding="utf-8")
        assert source.count("bloc_presence(") == 1

    def test_les_vides_sont_filtres_a_l_assemblage(self):
        """`join(parts)` ne filtrait pas : un bloc absent aurait creusé un trou."""
        source = (RACINE / "src/reasoning/react.py").read_text(encoding="utf-8")
        assert "(*parts, bloc_presence(self)) if p" in source

    def test_aucun_lot_de_la_phase_n_importe_telemetry_dans_react_py(self):
        """Tout passe par `react_config.py`. Un import direct rouvrirait le fichier gelé."""
        source = (RACINE / "src/reasoning/react.py").read_text(encoding="utf-8")
        assert "telemetry.presence" not in source and "telemetry.etat_systeme" not in source


# -- 2. Le budget est une ligne rouge ----------------------------------------


class TestBudget:
    def test_le_budget_de_la_phase_est_de_500_caracteres(self):
        assert BUDGET_CARACTERES == 500

    def test_la_somme_des_plafonds_tient_dans_le_budget(self):
        """Chaque lot réserve sa part : la somme doit tenir SANS troncature."""
        from src.telemetry.activite_courante import LIMITE_CARACTERES as s2
        from src.telemetry.etat_systeme import LIMITE_CARACTERES as s1

        assert s1 + s2 <= BUDGET_CARACTERES

    def test_un_bloc_qui_deborde_perd_le_MOINS_urgent_pas_un_chiffre_coupe(self, monkeypatch):
        """Mieux vaut perdre un bloc entier qu'une mesure tronquée au milieu d'un chiffre."""
        monkeypatch.setattr("src.telemetry.presence.bloc_etat_systeme", lambda: "A" * 480)
        monkeypatch.setattr("src.telemetry.presence.bloc_activite", lambda _o, **_kw: "B" * 480)
        rendu = bloc_presence(None)
        assert len(rendu) <= BUDGET_CARACTERES
        assert "B" not in rendu, "l'état système, plus urgent, doit survivre"


# -- 3. Jamais bloquant, quoi qu'on lui passe --------------------------------


class TestJamaisBloquant:
    @pytest.mark.parametrize("boucle", [None, object(), 42, "pas une boucle"])
    def test_une_boucle_absente_ou_inattendue_ne_leve_pas(self, boucle):
        """Lecon du lot A4 : des tests instancient la boucle sans `__init__`.

        Un bloc d'affichage ne doit jamais être la raison d'un `AttributeError`.
        """
        assert isinstance(bloc_presence(boucle), str)

    def test_un_bloc_qui_leve_n_emporte_pas_les_autres(self, monkeypatch):
        monkeypatch.setattr("src.telemetry.presence.bloc_etat_systeme",
                            lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        monkeypatch.setattr("src.telemetry.presence.bloc_activite", lambda _o, **_kw: "[En cours] 1")
        assert bloc_presence(None) == "[En cours] 1"

    def test_tous_les_blocs_muets_AUCUNE_ligne_vide(self, monkeypatch):
        """Sur le corpus réel, S2 est muet la plupart du temps : zéro tâche vivante."""
        monkeypatch.setattr("src.telemetry.presence.bloc_etat_systeme", lambda: "")
        monkeypatch.setattr("src.telemetry.presence.bloc_activite", lambda _o, **_kw: "   ")
        assert bloc_presence(None) == ""

    def test_l_orchestrateur_est_lu_sur_la_boucle_par_getattr(self, monkeypatch):
        """La source de S2 est l'orchestrateur VIVANT, pas le JSON périmé sur disque."""
        vus = []
        monkeypatch.setattr("src.telemetry.presence.bloc_etat_systeme", lambda: "")
        monkeypatch.setattr("src.telemetry.presence.bloc_activite",
                            lambda o, **kw: vus.append((o, kw)) or "[En cours] 1")

        class _Boucle:
            task_orchestrator = "sentinelle"

        bloc_presence(_Boucle())
        assert vus == [("sentinelle", {"conversation_id": None, "owner_user_id": None})]

    def test_la_tache_courante_fournit_le_scope_exact(self, monkeypatch):
        vus = []
        monkeypatch.setattr("src.telemetry.presence.bloc_etat_systeme", lambda: "")
        monkeypatch.setattr(
            "src.telemetry.presence.bloc_activite",
            lambda o, **kw: vus.append((o, kw)) or "[En cours] 1",
        )

        class _Orchestrateur:
            def get_task(self, task_id):
                assert task_id == "task-courante"
                return {
                    "conversation_id": "conv-privee",
                    "metadata": {"owner_user_id": "owner-prive"},
                }

        class _Boucle:
            task_orchestrator = _Orchestrateur()
            task_id = "task-courante"

        bloc_presence(_Boucle())

        assert vus == [(
            _Boucle.task_orchestrator,
            {"conversation_id": "conv-privee", "owner_user_id": "owner-prive"},
        )]
