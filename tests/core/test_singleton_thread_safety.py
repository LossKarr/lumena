"""
🧪 Test de thread-safety des singletons (Phase 5.2)

Vérifie que les getters singleton retournent toujours la même instance
même sous charge concurrente.
"""

import threading
import pytest
from concurrent.futures import ThreadPoolExecutor, as_completed


class TestSingletonThreadSafety:
    """Tests de thread-safety pour les singletons critiques."""
    
    def test_get_tool_system_thread_safe(self):
        """Vérifie que get_tool_system() est thread-safe."""
        from src.tools.tool_system import get_tool_system
        
        instances = []
        errors = []
        
        def get_instance():
            try:
                ts = get_tool_system()
                instances.append(id(ts))
            except Exception as e:
                errors.append(str(e))
        
        # Lancer 50 threads simultanément
        threads = []
        for _ in range(50):
            t = threading.Thread(target=get_instance)
            threads.append(t)
        
        for t in threads:
            t.start()
        
        for t in threads:
            t.join()
        
        # Vérifier qu'il n'y a pas d'erreur
        assert len(errors) == 0, f"Erreurs: {errors}"
        
        # Toutes les instances doivent être identiques
        assert len(set(instances)) == 1, "Plusieurs instances créées!"
    
    def test_get_emotion_manager_thread_safe(self):
        """Vérifie que get_emotion_manager() est thread-safe."""
        from src.emotion import get_emotion_manager
        
        instances = []
        
        def get_instance():
            em = get_emotion_manager()
            instances.append(id(em))
        
        with ThreadPoolExecutor(max_workers=20) as executor:
            futures = [executor.submit(get_instance) for _ in range(100)]
            for future in as_completed(futures):
                future.result()
        
        # Toutes les instances doivent être identiques
        assert len(set(instances)) == 1, "Plusieurs instances créées!"
    
    def test_get_session_memory_thread_safe(self):
        """Vérifie que get_session_memory() est thread-safe."""
        from src.memory.session_memory import get_session_memory
        
        instances = []
        
        def get_instance():
            sm = get_session_memory()
            instances.append(id(sm))
        
        threads = [threading.Thread(target=get_instance) for _ in range(30)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        assert len(set(instances)) == 1, "Plusieurs instances créées!"
    
    def test_get_orchestrator_thread_safe(self):
        """Vérifie que get_orchestrator() est thread-safe."""
        from src.agents.sub_agent import get_orchestrator
        
        instances = []
        
        def get_instance():
            o = get_orchestrator()
            instances.append(id(o))
        
        with ThreadPoolExecutor(max_workers=20) as executor:
            futures = [executor.submit(get_instance) for _ in range(50)]
            for future in as_completed(futures):
                future.result()
        
        assert len(set(instances)) == 1, "Plusieurs instances créées!"
    
    def test_get_skill_loader_thread_safe(self):
        """Vérifie que get_skill_loader() est thread-safe."""
        from src.skills.loader import get_skill_loader
        
        instances = []
        
        def get_instance():
            sl = get_skill_loader()
            instances.append(id(sl))
        
        threads = [threading.Thread(target=get_instance) for _ in range(30)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        assert len(set(instances)) == 1, "Plusieurs instances créées!"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
