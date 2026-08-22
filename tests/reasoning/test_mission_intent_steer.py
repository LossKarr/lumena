"""Steer d'intention MISSION (2026-07-01).

Bug (run petits-déjeuners) : « Crée une mission… » → Lumena, biaisée par la mémoire
d'échecs passés, fait le travail EN DIRECT et ignore la consigne explicite. Fix :
un détecteur pur + un nudge one-time côté LEAD qui rétablit la priorité de la
demande (pas de hard-route). Ces tests verrouillent le détecteur SERRÉ et la
règle de PRIORITÉ « mission > création de fichier direct » (#1 reviewer).
"""
from src.reasoning.prompt_builder import (
    is_explicit_mission_request,
    is_single_file_creation_request,
)


class TestExplicitMissionRequestDetector:
    def test_positifs(self):
        for q in (
            "Crée une mission avec échéance dans 3 minutes",
            "crée une mission de veille tech",
            "Lance une VRAIE mission en arrière-plan avec create_mission",
            "lancer une mission de recherche",
            "démarre une mission qui prépare un guide",
            "planifie une mission pour demain",
            "mets en place une mission de fond",
            "je veux une mission en tâche de fond",
            "crée-moi une mission stp",
        ):
            assert is_explicit_mission_request(q) is True, q

    def test_negatifs_identite_et_suivi(self):
        for q in (
            "quelle est ta mission de vie ?",
            "c'est quoi ta mission au juste",
            "alors la mission ?",
            "où en est la mission ?",
            "la mission est finie ?",
            "la mission est terminée",
            "mission accomplie !",
            "donne-moi le statut de la mission",
            "quel est l'avancement de la mission",
            "raconte-moi une histoire",   # pas de « mission » du tout
        ):
            assert is_explicit_mission_request(q) is False, q

    def test_vide(self):
        assert is_explicit_mission_request("") is False
        assert is_explicit_mission_request(None) is False

    def test_enregistre_variants(self):
        # Run NoteFlash 2026-07-02 : « Enregistre une mission » (le verbe CANONIQUE
        # de create_mission) n'était pas détecté → aucun nudge → le chat a routé
        # delegate_task/CodeAgent au lieu de la mission. Cas réel figé :
        noteflash = (
            "Enregistre une mission : construis NoteFlash, une mini-app de cartes "
            "de révision (flashcards), dans le workspace. Prends ton temps, la "
            "cohérence et la vérification réelle priment."
        )
        assert is_explicit_mission_request(noteflash) is True
        for q in (
            "enregistre une mission de veille",
            "enregistrer une mission pour ce soir",
            "enregistres une mission stp",
            "enregistre-moi une mission de fond",
            "Enregistre une vraie mission en arrière-plan",
        ):
            assert is_explicit_mission_request(q) is True, q

    def test_enregistre_faux_positifs(self):
        # Constat/suivi/hors-mission → jamais nudgé.
        for q in (
            "l'enregistrement de la mission a réussi",
            "la mission est bien enregistrée",
            "enregistre ce fichier dans workspace/notes.md",   # pas de « mission »
        ):
            assert is_explicit_mission_request(q) is False, q


class TestMissionWinsOverSingleFile:
    """#1 reviewer : « Crée une mission … dans workspace/x.md » matche les DEUX
    détecteurs. La règle de priorité (répliquée depuis react._run_internal) doit
    faire gagner la mission → pousser create_mission, pas write_file."""

    CONFLICT = ("Crée une mission avec échéance dans 3 minutes : prépare un guide "
                "dans workspace/soiree_cinema.md, puis relis le fichier avant de conclure.")

    def test_le_conflit_existe_bien(self):
        # Le cas piège : la demande contient à la fois « mission » et « workspace/.md ».
        assert is_explicit_mission_request(self.CONFLICT) is True

    def test_priorite_mission_ecrase_single_file(self):
        # Réplique EXACTE de la règle appliquée dans react._run_internal (l.~4401) :
        single_file = is_single_file_creation_request(self.CONFLICT)
        mission = is_explicit_mission_request(self.CONFLICT)
        if mission:
            single_file = False  # ← l'intention mission écrase la création directe
        assert mission is True and single_file is False

    def test_creation_fichier_seule_reste_intacte(self):
        # Non-régression : une vraie création de fichier SANS « mission » n'est pas nudgée
        # → single_file conservé, aucun steer mission.
        q = "génère un script python dans workspace/notes.py"
        assert is_explicit_mission_request(q) is False
        assert is_single_file_creation_request(q) is True
