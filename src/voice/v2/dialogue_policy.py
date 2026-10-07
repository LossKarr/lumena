"""Micro-dialogue opérationnel : bref, contextuel et jamais présenté comme résultat."""
from __future__ import annotations


class VoiceDialoguePolicy:
    """Choisit uniquement les acquittements locaux qui masquent une latence réelle.

    Les réponses, explications et bilans restent produits par Lumena. Ces messages ne
    prétendent jamais qu'une action a réussi ; ils décrivent seulement un état prouvé.
    """

    _MESSAGES = {
        "fr": {
            "working": "D'accord, je m'en occupe.",
            "steer": "D'accord, j'intègre cette précision au prochain point sûr.",
            "pause": "D'accord, je mets le travail en pause au prochain point sûr.",
            "resume": "D'accord, je reprends le travail.",
            "cancel_requested": "D'accord, j'annule au prochain point sûr.",
        },
        "en": {
            "working": "Okay, I'm on it.",
            "steer": "Okay, I'll apply that change at the next safe checkpoint.",
            "pause": "Okay, I'll pause at the next safe checkpoint.",
            "resume": "Okay, I'm resuming the work.",
            "cancel_requested": "Okay, I'll cancel at the next safe checkpoint.",
        },
        "es": {
            "working": "De acuerdo, me encargo.",
            "steer": "De acuerdo, aplicaré ese cambio en el próximo punto seguro.",
            "pause": "De acuerdo, pausaré el trabajo en el próximo punto seguro.",
            "resume": "De acuerdo, reanudo el trabajo.",
            "cancel_requested": "De acuerdo, cancelaré en el próximo punto seguro.",
        },
    }

    def message(self, intent: str, language: str = "fr") -> str:
        lang = str(language or "fr").split("-", 1)[0].lower()
        catalog = self._MESSAGES.get(lang, self._MESSAGES["fr"])
        return catalog.get(intent, catalog["working"])

