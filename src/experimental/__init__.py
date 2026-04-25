"""
Modules en statut PENDING-INTEGRATION.

Ces modules sont fonctionnels et testés, mais pas branchés dans le pipeline principal.
Ils restent dans leur emplacement d'origine pour ne pas casser les imports existants.

Statut : pending-integration (non canonique en production)

| Module                             | Emplacement canonique                    | Branchement prévu          |
|------------------------------------|------------------------------------------|----------------------------|
| SessionManager                     | src/agents/session_manager.py            | P10 multi-instance         |
| SessionMemory                      | src/memory/session_memory.py             | P10 multi-instance         |
| EmbeddingCache                     | src/memory/embedding_cache.py            | P8 si goulot mesuré        |
| MultiLanguageParser (tree-sitter)  | src/tools/tree_sitter_parser.py          | P8 view_outline            |

Voir PLAN_RUNTIME_DETERMINISTE.md §1.3 et §P8 pour les critères de reintégration.
"""
