"""
knowledge_graph.py — Knowledge Graph léger pour Lumena (GraphRAG sans NetworkX).

Stockage JSON: data/memory/kg/graph.json
- Nœuds: entités avec type, sources, métadonnées
- Arêtes: relations typées avec confiance et provenance
- Recherche: lexicale + type + score

Avantage vs RAGFlow KG:
- Zéro dépendance (pas de NetworkX, pas de Neo4j)
- Provenance complète: chaque entité/relation sait d'où elle vient
- Fusion automatique des doublons (case-insensitive)
- Persisté localement, portable, lisible par humain

Usage:
    from src.memory.knowledge_graph import get_knowledge_graph
    kg = get_knowledge_graph()
    kg.add_triple("Alice Dupont", "travaille_pour", "Acme SAS", source="contrat.pdf")
    results = kg.search("Alice")
    kg.ingest_from_chunks(chunks, source="rapport_annuel.pdf")
"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger
from ..utils.persistence import atomic_write_json


class KnowledgeGraph:
    """
    Graphe de connaissances léger, persisté en JSON.

    Structure interne:
        nodes: {id → {text, type, sources, metadata, created}}
        edges: [{subject_id, relation, object_id, confidence, source, timestamp}]
        _index: {text.lower() → id}  (recalculé au chargement)
    """

    def __init__(self, graph_path: Optional[Path] = None) -> None:
        if graph_path is None:
            from src.utils.paths import MEMORY_DIR
            graph_path = MEMORY_DIR / "kg" / "graph.json"

        self.graph_path = Path(graph_path)
        self.graph_path.parent.mkdir(parents=True, exist_ok=True)

        self._nodes: Dict[str, Dict[str, Any]] = {}
        self._edges: List[Dict[str, Any]] = []
        self._index: Dict[str, str] = {}   # text.lower() → node_id

        self._load()

    # ─── Persistence ────────────────────────────────────────────────────────

    def _load(self) -> None:
        if not self.graph_path.exists():
            return
        try:
            data = json.loads(self.graph_path.read_text(encoding="utf-8"))
            self._nodes = data.get("nodes", {})
            self._edges = data.get("edges", [])
            self._rebuild_index()
            logger.debug(
                f"[KG] Chargé: {len(self._nodes)} entités, {len(self._edges)} relations"
            )
        except Exception as exc:
            logger.warning(f"[KG] Impossible de charger {self.graph_path}: {exc} — graphe vide")

    def _save(self) -> None:
        try:
            payload = {"nodes": self._nodes, "edges": self._edges}
            atomic_write_json(self.graph_path, payload)
        except Exception as exc:
            logger.error(f"[KG] Sauvegarde échouée: {exc}")

    def _rebuild_index(self) -> None:
        self._index = {
            node["text"].lower(): nid
            for nid, node in self._nodes.items()
        }

    # ─── Write operations ────────────────────────────────────────────────────

    def add_entity(
        self,
        text: str,
        entity_type: str,
        source: str = "",
        metadata: Optional[Dict] = None,
    ) -> str:
        """Ajoute ou met à jour une entité. Retourne son ID."""
        key = text.lower().strip()
        if key in self._index:
            nid = self._index[key]
            node = self._nodes[nid]
            if source and source not in node.get("sources", []):
                node.setdefault("sources", []).append(source)
            return nid

        nid = str(uuid.uuid4())[:8]
        self._nodes[nid] = {
            "text": text.strip(),
            "type": entity_type,
            "sources": [source] if source else [],
            "metadata": metadata or {},
            "created": datetime.now().isoformat(),
        }
        self._index[key] = nid
        return nid

    def add_triple(
        self,
        subject: str,
        relation: str,
        obj: str,
        subject_type: str = "CONCEPT",
        object_type: str = "CONCEPT",
        confidence: float = 0.7,
        source: str = "",
    ) -> None:
        """Ajoute un triplet sujet-relation-objet dans le graphe."""
        sid = self.add_entity(subject, subject_type, source)
        oid = self.add_entity(obj, object_type, source)

        # Dédupliquer: même sujet + même relation + même objet
        for edge in self._edges:
            if (
                edge["subject_id"] == sid
                and edge["relation"] == relation
                and edge["object_id"] == oid
            ):
                return

        self._edges.append({
            "subject_id": sid,
            "relation": relation,
            "object_id": oid,
            "confidence": confidence,
            "source": source,
            "timestamp": datetime.now().isoformat(),
        })

    # ─── Read operations ─────────────────────────────────────────────────────

    def search(
        self,
        query: str,
        entity_type: Optional[str] = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """
        Recherche des entités correspondant à la requête.
        Retourne: [{entity, type, score, sources, relations}]
        """
        query_lower = query.lower().strip()
        query_words = set(query_lower.split())
        results = []

        for nid, node in self._nodes.items():
            if entity_type and node["type"] != entity_type:
                continue

            text_lower = node["text"].lower()
            score = 0.0

            if text_lower == query_lower:
                score = 1.0
            elif query_lower in text_lower or text_lower in query_lower:
                score = 0.8
            else:
                words = set(text_lower.split())
                overlap = query_words & words
                if overlap:
                    score = len(overlap) / max(len(query_words), len(words))

            if score >= 0.2:
                related = self._related_edges(nid)
                results.append({
                    "entity": node["text"],
                    "type": node["type"],
                    "score": score,
                    "sources": node.get("sources", []),
                    "relations": related[:5],
                })

        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:limit]

    def _related_edges(self, node_id: str) -> List[Dict]:
        related = []
        for edge in self._edges:
            if edge["subject_id"] == node_id or edge["object_id"] == node_id:
                subj = self._nodes.get(edge["subject_id"], {}).get("text", "?")
                obj_ = self._nodes.get(edge["object_id"], {}).get("text", "?")
                related.append({
                    "triple": f"{subj} —[{edge['relation']}]→ {obj_}",
                    "confidence": edge["confidence"],
                    "source": edge.get("source", ""),
                })
        return related

    def get_stats(self) -> Dict[str, int]:
        return {
            "nodes": len(self._nodes),
            "edges": len(self._edges),
            "entity_types": len({n["type"] for n in self._nodes.values()}),
        }

    def get_sources(self) -> List[str]:
        """Retourne la liste des documents sources ingérés."""
        sources: set = set()
        for node in self._nodes.values():
            sources.update(node.get("sources", []))
        return sorted(sources)

    # ─── Bulk ingestion ──────────────────────────────────────────────────────

    def ingest_from_chunks(self, chunks: list, source: str = "") -> int:
        """
        Ingère automatiquement entités et relations depuis des chunks DocumentChunk.
        Retourne le nombre de triplets ajoutés.
        """
        try:
            from ..perception.knowledge_extractor import KnowledgeExtractor
        except ImportError:
            logger.warning("[KG] KnowledgeExtractor non disponible")
            return 0

        extractor = KnowledgeExtractor()
        count = 0

        for chunk in chunks:
            text = chunk.content if hasattr(chunk, "content") else str(chunk)
            if len(text) < 20:
                continue

            triples = extractor.extract_triples(text, source=source)
            for triple in triples:
                # Décomposer le type combiné "TYPESUBJ→TYPEOBJ"
                parts = triple.entity_type.split("→", 1)
                stype = parts[0] if parts[0] else "CONCEPT"
                otype = parts[1] if len(parts) > 1 and parts[1] else "CONCEPT"

                self.add_triple(
                    triple.subject,
                    triple.relation,
                    triple.obj,
                    subject_type=stype,
                    object_type=otype,
                    confidence=triple.confidence,
                    source=source,
                )
                count += 1

        if count > 0:
            self._save()

        return count


# ─── Singleton ───────────────────────────────────────────────────────────────

_kg_instance: Optional[KnowledgeGraph] = None
_kg_lock = threading.Lock()


def get_knowledge_graph() -> KnowledgeGraph:
    """Retourne l'instance singleton du Knowledge Graph."""
    global _kg_instance
    if _kg_instance is None:
        with _kg_lock:
            if _kg_instance is None:
                _kg_instance = KnowledgeGraph()
    return _kg_instance
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU General Public License v3.0 (GPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
