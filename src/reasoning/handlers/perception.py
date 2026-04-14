"""
perception.py — Handlers d'intelligence documentaire pour Lumena.

Surpasse RAGFlow sur:
- Ingestion native dans la mémoire ChromaDB avec citations de source
- Knowledge Graph automatique extrait de chaque document ingéré
- Résumé structuré avec table des matières + entités clés
- Chunking sémantique (header/table/text/list), pas token-based

Handlers exposés:
    ingest_document    — lit + chunk + KG + stocke en mémoire avec citations
    kg_search          — recherche dans le Knowledge Graph
    document_summary   — résumé structuré + entités + ToC
    chunk_document     — prévisualise le découpage avant ingestion
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

from .context import HandlerContext
from .contracts import HandlerResult
from .registry_v2 import HandlerDef


# ─── ingest_document ─────────────────────────────────────────────────────────

async def ingest_document_handler(
    ctx: HandlerContext,
    path: str,
    tags: str = "",
    chunk_size: int = 1200,
    extract_entities: bool = True,
) -> HandlerResult:
    """
    Ingère un document dans la mémoire de Lumena.

    Pipeline:
    1. Localise le fichier (workspace / rglob fallback)
    2. Lecture intelligente → chunks sémantiques (DocumentReader)
    3. Chaque chunk → memory.remember() avec citation source complète
    4. Extraction KG: entités + relations → Knowledge Graph persisté

    Surpasse RAGFlow: pas de modèles ML, provenance immédiate, intégration mémoire native.
    """
    try:
        from ...perception.document_reader import DocumentReader

        p = _resolve_path(ctx, path)
        if p is None:
            return HandlerResult.fail(
                f"❌ Fichier non trouvé: {path}", handler_name="ingest_document"
            )

        reader = DocumentReader()
        if chunk_size != 1200:
            reader.MAX_CHUNK_CHARS = max(200, min(4000, int(chunk_size)))

        chunks = reader.read(str(p))
        if not chunks:
            return HandlerResult.fail(
                f"❌ Aucun contenu extractible dans {p.name}",
                handler_name="ingest_document",
            )

        memory = getattr(ctx.lumena, "memory", None)
        if memory is None:
            return HandlerResult.fail(
                "❌ Module mémoire non disponible", handler_name="ingest_document"
            )

        source_name = p.name
        tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []

        # Importance par type de chunk
        _importance: Dict[str, float] = {
            "header": 0.80,
            "table": 0.85,
            "slide": 0.75,
            "list": 0.70,
            "text": 0.65,
            "image_page": 0.30,
        }

        stored = 0
        skipped = 0
        type_counts: Dict[str, int] = {}

        for chunk in chunks:
            if not chunk.content.strip() or len(chunk.content) < 30:
                skipped += 1
                continue

            # ── Citation de source ───────────────────────────────────────
            citation_parts = [f"[Source: {source_name}"]
            if chunk.page:
                citation_parts.append(f"p.{chunk.page}")
            if chunk.section:
                citation_parts.append(f"§ {chunk.section[:50]}")
            citation_parts.append(f"type:{chunk.chunk_type}]")
            citation = " ".join(citation_parts)

            content_with_citation = f"{citation}\n{chunk.content}"
            importance = _importance.get(chunk.chunk_type, 0.65)

            memory.remember(
                content_with_citation,
                memory_type="document",
                importance=importance,
                metadata={
                    "source_file": str(p),
                    "source_name": source_name,
                    "page": chunk.page,
                    "section": chunk.section,
                    "chunk_type": chunk.chunk_type,
                    "tags": tag_list,
                },
            )
            stored += 1
            type_counts[chunk.chunk_type] = type_counts.get(chunk.chunk_type, 0) + 1

        # ── Knowledge Graph ──────────────────────────────────────────────
        kg_triples = 0
        if extract_entities:
            try:
                from ...memory.knowledge_graph import get_knowledge_graph
                kg = get_knowledge_graph()
                kg_triples = kg.ingest_from_chunks(chunks, source=source_name)
            except Exception as kg_exc:
                logger.warning(f"[ingest_document] KG extraction skipped: {kg_exc}")

        types_str = ", ".join(f"{k}:{v}" for k, v in sorted(type_counts.items()))
        summary = (
            f"✅ **Document ingéré: {source_name}**\n\n"
            f"📊 Statistiques:\n"
            f"- **{stored}** chunks stockés en mémoire (avec citations de source)\n"
            f"- **{skipped}** fragments ignorés (trop courts)\n"
            f"- Types de chunks: {types_str}\n"
            f"- **{kg_triples}** relations Knowledge Graph extraites\n\n"
            f"🔍 Chaque chunk inclut: `[Source: {source_name}]`, page, section, type\n"
            f"💡 Utilisez `memory_search` pour retrouver ce contenu avec citations complètes.\n"
            f"💡 Utilisez `kg_search` pour explorer les entités extraites."
        )
        return HandlerResult.ok(summary, handler_name="ingest_document")

    except Exception as exc:
        logger.exception(f"[ingest_document] Erreur: {exc}")
        return HandlerResult.fail(f"❌ Erreur ingestion: {exc}", handler_name="ingest_document")


# ─── kg_search ───────────────────────────────────────────────────────────────

async def kg_search_handler(
    ctx: HandlerContext,
    query: str,
    entity_type: str = "",
    limit: int = 10,
) -> HandlerResult:
    """
    Recherche dans le Knowledge Graph de Lumena.
    Retourne entités, relations extraites et documents sources.
    """
    try:
        from ...memory.knowledge_graph import get_knowledge_graph

        kg = get_knowledge_graph()
        stats = kg.get_stats()

        if stats["nodes"] == 0:
            return HandlerResult.ok(
                "🕸️ Le Knowledge Graph est vide.\n"
                "Utilisez `ingest_document` pour ingérer des documents et alimenter le KG.",
                handler_name="kg_search",
            )

        results = kg.search(
            query=query,
            entity_type=entity_type or None,
            limit=int(limit),
        )

        if not results:
            sources = kg.get_sources()
            src_str = ", ".join(sources[:5]) if sources else "aucun"
            return HandlerResult.ok(
                f"🔍 Aucune entité trouvée pour \"{query}\""
                + (f" (type: {entity_type})" if entity_type else "")
                + f"\n📊 KG: {stats['nodes']} entités, {stats['edges']} relations"
                + f"\n📁 Sources ingérées: {src_str}",
                handler_name="kg_search",
            )

        output = f"🕸️ **Knowledge Graph — \"{query}\"** ({len(results)} résultat(s))\n\n"
        for i, r in enumerate(results, 1):
            src_str = ", ".join(r["sources"][:3]) if r["sources"] else "inconnu"
            output += f"{i}. **{r['entity']}** `[{r['type']}]` — score: {r['score']:.2f}\n"
            output += f"   📁 {src_str}\n"
            for rel in r.get("relations", [])[:2]:
                output += f"   🔗 {rel['triple']}\n"
            output += "\n"

        output += f"📊 KG total: {stats['nodes']} entités, {stats['edges']} relations"
        return HandlerResult.ok(output, handler_name="kg_search")

    except Exception as exc:
        return HandlerResult.fail(f"❌ Erreur kg_search: {exc}", handler_name="kg_search")


# ─── document_summary ────────────────────────────────────────────────────────

async def document_summary_handler(
    ctx: HandlerContext,
    path: str,
) -> HandlerResult:
    """
    Génère un résumé structuré d'un document:
    - Format, taille, pages
    - Table des matières (headers détectés)
    - Nombre de tableaux
    - Entités clés: dates, montants, emails, orgs, personnes
    """
    try:
        from ...perception.document_reader import DocumentReader
        from ...perception.knowledge_extractor import KnowledgeExtractor

        p = _resolve_path(ctx, path)
        if p is None:
            return HandlerResult.fail(
                f"❌ Fichier non trouvé: {path}", handler_name="document_summary"
            )

        reader = DocumentReader()
        chunks = reader.read(str(p))

        if not chunks:
            return HandlerResult.fail(
                f"❌ Aucun contenu extractible dans {p.name}",
                handler_name="document_summary",
            )

        headers = [c for c in chunks if c.chunk_type == "header"]
        tables = [c for c in chunks if c.chunk_type == "table"]
        text_chunks = [c for c in chunks if c.chunk_type == "text"]
        image_pages = [c for c in chunks if c.chunk_type == "image_page"]
        unique_pages = len({c.page for c in chunks if c.page > 0})
        total_chars = sum(len(c.content) for c in chunks)

        # Extraction entités sur les 20 premiers chunks
        full_text = "\n".join(c.content for c in chunks[:20])
        extractor = KnowledgeExtractor()
        entities = extractor.extract_entities(full_text, source=p.name)
        entity_summary = extractor.summarize_entities(entities)

        output = f"📄 **Résumé structuré: {p.name}**\n\n"
        output += f"**Format:** {p.suffix.upper().lstrip('.')} | "
        output += f"**Pages:** {unique_pages} | "
        output += f"**Taille:** {total_chars:,} caractères\n\n"

        output += "**Structure:**\n"
        output += f"- {len(headers)} titres/sections\n"
        output += f"- {len(tables)} tableaux\n"
        output += f"- {len(text_chunks)} blocs de texte\n"
        if image_pages:
            output += f"- {len(image_pages)} pages-images (OCR requis)\n"
        output += "\n"

        if headers:
            output += "**Table des matières:**\n"
            for h in headers[:12]:
                level = len(h.content) - len(h.content.lstrip("#"))
                indent = "  " * max(0, level - 1)
                title = h.content.lstrip("#").strip()
                output += f"{indent}• {title}\n"
            if len(headers) > 12:
                output += f"  … (+{len(headers) - 12} sections)\n"
            output += "\n"

        # Entités priorisées
        priority = ["AMOUNT", "DATE_FR", "DATE_ISO", "DATE_TEXT",
                    "ORG_SUFFIX", "PERSON", "EMAIL", "SIRET", "PHONE", "PERCENT"]
        entity_lines = []
        for etype in priority:
            vals = entity_summary.get(etype, [])
            if vals:
                display = ", ".join(vals[:5])
                if len(vals) > 5:
                    display += f" (+{len(vals) - 5})"
                entity_lines.append(f"- **{etype}**: {display}")

        if entity_lines:
            output += "**Entités détectées:**\n"
            output += "\n".join(entity_lines)

        return HandlerResult.ok(output, handler_name="document_summary")

    except Exception as exc:
        logger.exception(f"[document_summary] Erreur: {exc}")
        return HandlerResult.fail(
            f"❌ Erreur document_summary: {exc}", handler_name="document_summary"
        )


# ─── chunk_document ──────────────────────────────────────────────────────────

async def chunk_document_handler(
    ctx: HandlerContext,
    path: str,
    chunk_size: int = 1200,
) -> HandlerResult:
    """
    Prévisualise le découpage sémantique d'un document sans l'ingérer.
    Utile pour vérifier la qualité de l'indexation avant ingestion.
    """
    try:
        from ...perception.document_reader import DocumentReader

        p = _resolve_path(ctx, path)
        if p is None:
            return HandlerResult.fail(
                f"❌ Fichier non trouvé: {path}", handler_name="chunk_document"
            )

        reader = DocumentReader()
        if chunk_size != 1200:
            reader.MAX_CHUNK_CHARS = max(200, min(4000, int(chunk_size)))

        chunks = reader.read(str(p))

        output = f"🔍 **Prévisualisation chunks: {p.name}** ({len(chunks)} chunks)\n\n"
        for i, chunk in enumerate(chunks[:15], 1):
            preview = chunk.content[:100].replace("\n", " ")
            if len(chunk.content) > 100:
                preview += "…"
            page_info = f" p.{chunk.page}" if chunk.page else ""
            sect_info = f" §{chunk.section[:25]}" if chunk.section else ""
            output += f"{i}. **[{chunk.chunk_type}{page_info}{sect_info}]** {preview}\n"

        if len(chunks) > 15:
            output += f"\n… et {len(chunks) - 15} autres chunks non affichés"

        return HandlerResult.ok(output, handler_name="chunk_document")

    except Exception as exc:
        return HandlerResult.fail(
            f"❌ Erreur chunk_document: {exc}", handler_name="chunk_document"
        )


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _resolve_path(ctx: HandlerContext, path: str) -> Optional[Path]:
    """Résout un chemin de fichier: absolu, workspace, ou rglob fallback."""
    p = Path(path)

    if p.is_absolute() and p.exists():
        return p

    # Chercher dans workspace/
    from ...utils.paths import WORKSPACE_DIR
    workspace = WORKSPACE_DIR
    if workspace.exists():
        candidates = list(workspace.rglob(p.name))
        if candidates:
            return candidates[0]

    # Chercher récursivement dans lumena root
    candidates = list(ctx.lumena_root.rglob(p.name))
    if candidates:
        return candidates[0]

    return None


# ─── Registration ─────────────────────────────────────────────────────────────

def get_perception_handler_defs() -> List[HandlerDef]:
    """Retourne toutes les définitions de handlers perception pour le registre V2."""
    return [
        HandlerDef(
            name="ingest_document",
            description=(
                "Ingère un document (PDF/DOCX/XLSX/PPTX/TXT/MD) dans la mémoire de Lumena: "
                "lecture intelligente, chunking sémantique (header/table/texte), "
                "citations de source automatiques, extraction Knowledge Graph. "
                "Utiliser pour: 'mémorise ce PDF', 'apprends ce contrat', 'ingère ce fichier', "
                "'indexe ce document', 'retiens ce rapport'."
            ),
            parameters={
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Chemin vers le fichier à ingérer",
                    },
                    "tags": {
                        "type": "string",
                        "description": "Tags de classification (ex: 'contrat,2024,client')",
                    },
                    "chunk_size": {
                        "type": "integer",
                        "description": "Taille max des chunks en caractères (défaut: 1200)",
                    },
                    "extract_entities": {
                        "type": "boolean",
                        "description": "Extraire entités dans le Knowledge Graph (défaut: true)",
                    },
                },
                "required": ["path"],
            },
            handler=ingest_document_handler,
            category="documents",
            source_module="handlers.perception",
        ),
        HandlerDef(
            name="kg_search",
            description=(
                "Recherche dans le Knowledge Graph de Lumena: entités, relations, sources. "
                "Retourne entités extraites de documents ingérés + leurs relations. "
                "Utiliser pour: 'qui est X dans mes docs', 'trouve les montants', "
                "'liste les organisations mentionnées', 'quelles dates dans le KG'."
            ),
            parameters={
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Entité ou concept à rechercher",
                    },
                    "entity_type": {
                        "type": "string",
                        "description": (
                            "Filtrer par type: PERSON, ORG_SUFFIX, DATE_FR, DATE_ISO, "
                            "AMOUNT, EMAIL, URL, SIRET, PHONE, CONCEPT"
                        ),
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Nombre max de résultats (défaut: 10)",
                    },
                },
                "required": ["query"],
            },
            handler=kg_search_handler,
            category="memory",
            source_module="handlers.perception",
        ),
        HandlerDef(
            name="document_summary",
            description=(
                "Génère un résumé structuré d'un document: format, taille, table des matières, "
                "tableaux détectés, entités clés (dates, montants, emails, organisations, personnes). "
                "Utiliser pour: 'analyse ce document', 'que contient ce PDF', "
                "'résume ce fichier', 'extrais les infos clés'."
            ),
            parameters={
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Chemin vers le document à analyser",
                    },
                },
                "required": ["path"],
            },
            handler=document_summary_handler,
            category="documents",
            source_module="handlers.perception",
        ),
        HandlerDef(
            name="chunk_document",
            description=(
                "Prévisualise comment un document serait découpé en chunks sémantiques. "
                "Affiche les 15 premiers chunks avec leur type (header/table/text) et section. "
                "Utile avant d'ingérer pour vérifier la qualité du découpage."
            ),
            parameters={
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Chemin vers le document",
                    },
                    "chunk_size": {
                        "type": "integer",
                        "description": "Taille max des chunks en caractères (défaut: 1200)",
                    },
                },
                "required": ["path"],
            },
            handler=chunk_document_handler,
            category="documents",
            source_module="handlers.perception",
        ),
    ]
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the Apache License, Version 2.0
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
