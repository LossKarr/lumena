"""
TEST COMPLET - Toutes les phases performance Lumena

Execute: python tests/test_all_phases.py
"""

import sys
import asyncio
from pathlib import Path
import pytest

# Ajouter le chemin parent
sys.path.insert(0, str(Path(__file__).parent.parent))


def test_phase1_multiedit():
    """Phase 1: MultiEdit"""
    print("\n=== PHASE 1: MultiEdit ===")
    try:
        from src.tools.apply_patch import multi_edit_file
        print("[OK] Import multi_edit_file")
        
        # Test avec edits vides
        result = multi_edit_file([])
        assert "Erreur" in result
        print("[OK] Gestion edits vides")
    except Exception as e:
        pytest.fail(f"Phase 1 échouée: {e}")


def test_phase2_parallel():
    """Phase 2: Parallel Tools"""
    print("\n=== PHASE 2: Parallel Tools ===")
    try:
        from src.reasoning.react import ToolRegistry
        
        tr = ToolRegistry()
        assert hasattr(tr, 'execute_parallel')
        print("[OK] execute_parallel existe")
        
        # Verifier signature
        import inspect
        sig = inspect.signature(tr.execute_parallel)
        assert 'tool_calls' in sig.parameters
        print("[OK] Signature correcte")
    except Exception as e:
        pytest.fail(f"Phase 2 échouée: {e}")


def test_phase3_session_memory():
    """Phase 3: Session Memory"""
    print("\n=== PHASE 3: Session Memory ===")
    try:
        from src.memory.session_memory import SessionMemory, get_session_memory
        
        mem = SessionMemory()
        print("[OK] SessionMemory instanciable")
        
        # Test ajout turn
        idx = mem.add_turn("user", "Bonjour!")
        assert idx == 0
        print("[OK] add_turn fonctionne")
        
        # Test decision
        mem.add_decision("Utiliser Python")
        assert len(mem.key_decisions) == 1
        print("[OK] add_decision fonctionne")
        
        # Test preferences
        mem.learn_preference("langue", "fr")
        assert mem.user_preferences.get("langue") == "fr"
        print("[OK] learn_preference fonctionne")
        
        # Test contexte
        ctx = mem.get_context()
        assert "recent_turns" in ctx
        print("[OK] get_context fonctionne")
        
        # Test singleton
        sm = get_session_memory()
        assert sm is not None
        print("[OK] Singleton fonctionne")
    except Exception as e:
        pytest.fail(f"Phase 3 échouée: {e}")


def test_phase4_treesitter():
    """Phase 4: Tree-sitter Parser"""
    print("\n=== PHASE 4: Tree-sitter Parser ===")
    try:
        from src.tools.tree_sitter_parser import MultiLanguageParser, parse_file_outline
        
        parser = MultiLanguageParser()
        print("[OK] MultiLanguageParser instanciable")
        
        # Test parsing Python (ce fichier meme)
        symbols = parser.parse_file(__file__)
        assert len(symbols) > 0
        print(f"[OK] Parse Python: {len(symbols)} symboles trouves")
        
        # Afficher les symboles
        for sym in symbols[:5]:
            print(f"    - {sym.kind}: {sym.name}")
    except Exception as e:
        pytest.fail(f"Phase 4 échouée: {e}")


def test_phase5_pty():
    """Phase 5: PTY Mode"""
    print("\n=== PHASE 5: PTY Mode ===")
    try:
        from src.tools.pty_session import PtyManager, get_pty_manager
        
        mgr = PtyManager()
        print("[OK] PtyManager instanciable")
        
        # Test list (vide au debut)
        sessions = mgr.list_sessions()
        assert isinstance(sessions, dict)
        print("[OK] list_sessions fonctionne")
        
        # Test singleton
        pm = get_pty_manager()
        assert pm is not None
        print("[OK] Singleton fonctionne")
    except Exception as e:
        pytest.fail(f"Phase 5 échouée: {e}")


def test_phase6_reranking():
    """Phase 6: Reranking"""
    print("\n=== PHASE 6: Reranking ===")
    try:
        from src.memory.chromadb_store import ChromaMemoryStore
        
        assert hasattr(ChromaMemoryStore, 'rerank')
        print("[OK] Methode rerank existe")
        
        # Verifier signature
        import inspect
        sig = inspect.signature(ChromaMemoryStore.rerank)
        params = list(sig.parameters.keys())
        assert 'query' in params
        assert 'memories' in params
        print("[OK] Signature rerank correcte")
    except Exception as e:
        pytest.fail(f"Phase 6 échouée: {e}")


def main():
    print("=" * 50)
    print("TEST COMPLET LUMENA - 6 PHASES PERFORMANCE")
    print("=" * 50)
    
    results = {
        "Phase 1 - MultiEdit": test_phase1_multiedit(),
        "Phase 2 - Parallel": test_phase2_parallel(),
        "Phase 3 - SessionMemory": test_phase3_session_memory(),
        "Phase 4 - TreeSitter": test_phase4_treesitter(),
        "Phase 5 - PTY": test_phase5_pty(),
        "Phase 6 - Reranking": test_phase6_reranking(),
    }
    
    print("\n" + "=" * 50)
    print("RESULTATS")
    print("=" * 50)
    
    passed = 0
    failed = 0
    
    for name, success in results.items():
        status = "[PASS]" if success else "[FAIL]"
        print(f"{status} {name}")
        if success:
            passed += 1
        else:
            failed += 1
    
    print(f"\nTotal: {passed}/{len(results)} phases OK")
    
    if failed == 0:
        print("\n*** TOUS LES TESTS PASSENT! ***")
        return 0
    else:
        print(f"\n*** {failed} PHASE(S) ECHOUEE(S) ***")
        return 1


if __name__ == "__main__":
    sys.exit(main())
