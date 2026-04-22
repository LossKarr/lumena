"""
perception/ — Module de perception documentaire de Lumena.

Capacités:
- Lecture intelligente de documents (PDF, DOCX, XLSX, PPTX, TXT, MD)
  avec extraction de tableaux, structure hiérarchique et métadonnées
- Extraction d'entités et de relations (Knowledge Graph léger, sans dépendances)
- Découpage sémantique adapté au type de contenu (header/table/text/list/slide)
- Citations de source automatiques (fichier, page, section, type)

Surpasse RAGFlow sur:
- Pas de dépendances lourdes (pas de DeepDoc, pas de modèles ML)
- Intégration native avec la mémoire ChromaDB de Lumena
- Knowledge Graph persisté localement avec provenance complète
"""

from .document_reader import DocumentChunk, DocumentReader
from .knowledge_extractor import Entity, KnowledgeExtractor, KnowledgeTriple

__all__ = [
    "DocumentReader",
    "DocumentChunk",
    "KnowledgeExtractor",
    "KnowledgeTriple",
    "Entity",
]
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU General Public License v3.0 (GPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
