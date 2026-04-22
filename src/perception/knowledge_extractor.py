"""
knowledge_extractor.py — Extraction d'entités et relations par regex.

Surpasse spaCy sur: légèreté, zéro dépendance ML, adapté au contexte français.
Détecte: EMAIL, URL, DATE (FR + ISO + texte), AMOUNT, SIRET/SIREN, PHONE,
         ORG (suffixes légaux), PERSON (titres civils), PERCENT, CONCEPT (fréquence).

Usage:
    extractor = KnowledgeExtractor()
    entities = extractor.extract_entities(text, source="mon_document.pdf")
    triples  = extractor.extract_triples(text, source="mon_document.pdf")
    summary  = extractor.summarize_entities(entities)
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class Entity:
    text: str
    entity_type: str   # EMAIL | URL | DATE_FR | DATE_ISO | DATE_TEXT | AMOUNT | SIRET |
                       # SIREN | PHONE | ORG_SUFFIX | PERSON | PERCENT | CONCEPT
    confidence: float = 0.85
    context: str = ""  # 60 chars autour de la détection


@dataclass
class KnowledgeTriple:
    subject: str
    relation: str
    obj: str
    entity_type: str = "RELATION"   # sujType→objType ou "RELATION"
    confidence: float = 0.6
    source: str = ""

    def to_dict(self) -> Dict:
        return {
            "subject": self.subject,
            "relation": self.relation,
            "object": self.obj,
            "entity_type": self.entity_type,
            "confidence": self.confidence,
            "source": self.source,
        }


class KnowledgeExtractor:
    """
    Extracteur d'entités et relations basé uniquement sur regex + heuristiques.
    Aucune dépendance externe (pas de spaCy, pas de transformers).
    """

    # ─── Patterns regex ─────────────────────────────────────────────────────

    _PATTERNS: Dict[str, re.Pattern] = {
        "EMAIL": re.compile(
            r"\b[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,10}\b"
        ),
        "URL": re.compile(
            r"https?://[^\s\"'<>\]\[)]{4,100}"
        ),
        "DATE_FR": re.compile(
            r"\b(?:0?[1-9]|[12]\d|3[01])[/\-.](?:0?[1-9]|1[0-2])[/\-.](?:19|20)\d{2}\b"
        ),
        "DATE_ISO": re.compile(
            r"\b(?:19|20)\d{2}[-/](?:0[1-9]|1[0-2])[-/](?:0[1-9]|[12]\d|3[01])\b"
        ),
        "DATE_TEXT": re.compile(
            r"\b(?:janvier|février|mars|avril|mai|juin|juillet|août|septembre|octobre|novembre|décembre)"
            r"\s+\d{4}\b",
            re.IGNORECASE,
        ),
        "AMOUNT": re.compile(
            r"(?:€|\$|£|CHF)\s*\d[\d\s.,]*"
            r"|\b\d[\d\s.,]+\s*(?:€|\$|£|euros?|dollars?|CHF)\b",
            re.IGNORECASE,
        ),
        "SIRET": re.compile(
            r"\b\d{3}[\s.]?\d{3}[\s.]?\d{3}[\s.]?\d{5}\b"
        ),
        "SIREN": re.compile(
            r"\b\d{3}[\s.]?\d{3}[\s.]?\d{3}\b"
        ),
        "PHONE": re.compile(
            r"\b(?:\+33|0033|0)[1-9](?:[\s.\-]?\d{2}){4}\b"
        ),
        "ORG_SUFFIX": re.compile(
            r"\b[A-ZÀ-Ü][a-zA-Zà-ÿ\-&']{1,25}"
            r"(?:\s+(?:de\s+la|de\s+l\'|du|des?|le|la|les|et|&)?\s*"
            r"[A-ZÀ-Ü][a-zA-Zà-ÿ\-&']{1,25}){0,3}"
            r"\s+(?:SAS|SARL|SA\b|SASU|EURL|SCI|SNC|Ltd\.?|Inc\.?|Corp\.?|GmbH|EIRL|EI\b)"
        ),
        "PERCENT": re.compile(
            r"\b\d{1,3}(?:[.,]\d{1,2})?\s*%"
        ),
        "PERSON": re.compile(
            r"\b(?:M\.|Mme\.?|Mr\.?|Dr\.?|Me\.?|Prof\.?)\s+"
            r"[A-ZÀ-Ü][a-zà-ü]+(?:[\s\-][A-ZÀ-Ü][a-zà-ü]+)+"
        ),
    }

    # Patterns de relation syntaxique (français)
    _RELATION_PATTERNS = [
        (
            re.compile(
                r"([A-ZÀ-Ü][a-zà-ü\s]+)\s+(?:est|sont|a été)\s+([^,.\n]{5,60})",
                re.IGNORECASE,
            ),
            "est",
        ),
        (
            re.compile(
                r"([A-ZÀ-Ü][a-zà-ü\s]+)\s+(?:travaille|dirige|gère|emploie)\s+"
                r"(?:chez|pour|à|au|aux)\s+([^,.\n]{5,60})",
                re.IGNORECASE,
            ),
            "travaille_pour",
        ),
        (
            re.compile(
                r"([A-ZÀ-Ü][a-zà-ü\s]+)\s+(?:a signé|a conclu|a passé)\s+"
                r"(?:un |une |le |la )?(?:contrat|accord|convention)\s+avec\s+([^,.\n]{5,60})",
                re.IGNORECASE,
            ),
            "contrat_avec",
        ),
        (
            re.compile(
                r"([A-ZÀ-Ü][a-zà-ü\s]+)\s+(?:appartient à|dépend de|fait partie de)\s+"
                r"([^,.\n]{5,60})",
                re.IGNORECASE,
            ),
            "appartient_à",
        ),
    ]

    # ─── Public API ─────────────────────────────────────────────────────────

    def extract_entities(self, text: str, source: str = "") -> List[Entity]:
        """Extrait toutes les entités nommées du texte."""
        entities: List[Entity] = []
        seen: set = set()

        for entity_type, pattern in self._PATTERNS.items():
            for match in pattern.finditer(text):
                val = match.group(0).strip()
                if not val or val in seen:
                    continue
                # Éviter les SIREN qui sont en fait des SIRET déjà capturés
                if entity_type == "SIREN":
                    if any(val in e.text and e.entity_type == "SIRET" for e in entities):
                        continue
                seen.add(val)
                start = max(0, match.start() - 30)
                end = min(len(text), match.end() + 30)
                ctx = text[start:end].replace("\n", " ")
                entities.append(Entity(
                    text=val,
                    entity_type=entity_type,
                    confidence=0.85,
                    context=ctx,
                ))

        # Concepts par fréquence (noms propres multi-mots répétés)
        concepts = self._extract_concepts(text, seen)
        entities.extend(concepts)

        return entities

    def extract_triples(self, text: str, source: str = "") -> List[KnowledgeTriple]:
        """Extrait des triplets sujet-relation-objet."""
        triples: List[KnowledgeTriple] = []
        entities = self.extract_entities(text, source)

        # 1. Co-occurrence intra-phrase
        sentences = re.split(r"(?<=[.!?])\s+", text)
        for sent in sentences:
            present = [e for e in entities if e.text in sent]
            if len(present) >= 2:
                for i in range(len(present) - 1):
                    for j in range(i + 1, len(present)):
                        e1, e2 = present[i], present[j]
                        triples.append(KnowledgeTriple(
                            subject=e1.text,
                            relation="co-occur",
                            obj=e2.text,
                            entity_type=f"{e1.entity_type}→{e2.entity_type}",
                            confidence=0.55,
                            source=source,
                        ))

        # 2. Relations syntaxiques
        for pattern, relation in self._RELATION_PATTERNS:
            for match in pattern.finditer(text):
                subj = match.group(1).strip().rstrip()
                obj = match.group(2).strip().rstrip(" ,.")
                if 3 < len(subj) < 80 and 3 < len(obj) < 80:
                    triples.append(KnowledgeTriple(
                        subject=subj,
                        relation=relation,
                        obj=obj,
                        entity_type="RELATION",
                        confidence=0.75,
                        source=source,
                    ))

        return triples

    def summarize_entities(self, entities: List[Entity]) -> Dict[str, List[str]]:
        """Retourne un dict {type: [valeurs]} pour affichage."""
        result: Dict[str, List[str]] = {}
        for e in entities:
            result.setdefault(e.entity_type, [])
            if e.text not in result[e.entity_type]:
                result[e.entity_type].append(e.text)
        return result

    # ─── Private helpers ────────────────────────────────────────────────────

    def _extract_concepts(self, text: str, exclude: set) -> List[Entity]:
        """
        Extrait des noms propres multi-mots capitalisés répétés (heuristique = concept important).
        Seuil: doit apparaître ≥ 2 fois, longueur > 5 chars.
        """
        pattern = re.compile(r"\b[A-ZÀ-Ü][a-zà-ü]+(?:[\s\-][A-ZÀ-Ü][a-zà-ü]+)+\b")
        counter: Counter = Counter(m.group(0) for m in pattern.finditer(text))

        concepts: List[Entity] = []
        for concept, count in counter.most_common(30):
            if concept in exclude:
                continue
            if len(concept) <= 5 or count < 2:
                continue
            concepts.append(Entity(
                text=concept,
                entity_type="CONCEPT",
                confidence=min(0.9, 0.35 + count * 0.08),
            ))
        return concepts
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
