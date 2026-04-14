"""
🧪 TESTS COMPLETS DE LUMENA
===========================
Ce script teste toutes les fonctionnalités de LUMENA.
"""
import sys
import asyncio
from pathlib import Path
from datetime import datetime

if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

# Ajouter le dossier lumena au path
lumena_root = Path(__file__).parent.parent
sys.path.insert(0, str(lumena_root))

# Compteurs
passed = 0
failed = 0


async def test(name, test_func):
    """Exécute un test."""
    global passed, failed
    try:
        result = await test_func() if asyncio.iscoroutinefunction(test_func) else test_func()
        if result:
            print(f"  ✅ {name}")
            passed += 1
            return True
        else:
            print(f"  ❌ {name}: Résultat False")
            failed += 1
            return False
    except Exception as e:
        print(f"  ❌ {name}: {e}")
        failed += 1
        return False


async def run_tests():
    """Exécute tous les tests."""
    print("=" * 70)
    print("🧪 TESTS COMPLETS DE LUMENA")
    print(f"   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)
    
    # ===============================
    # Tests Core
    # ===============================
    print("\n[1] Tests Core...")
    
    await test("Import LumenaCore", lambda: __import__("src.core").core.LumenaCore is not None)
    await test("Import Message", lambda: __import__("src.core").core.Message is not None)
    
    from src.core import LumenaCore
    core = LumenaCore()
    await test("Création LumenaCore", lambda: core is not None)
    await test("Greet fonctionne", lambda: len(core.greet()) > 0)
    
    # ===============================
    # Tests Personnalité
    # ===============================
    print("\n[2] Tests Personnalité...")
    
    from src.personality import LumenaPersonality, Mood
    p = LumenaPersonality()
    await test("Personnalité créée", lambda: p is not None)
    await test("Nom correct", lambda: p.name == "Lumena")
    await test("Moods disponibles", lambda: len(Mood) >= 5)
    await test("System prompt", lambda: len(p.get_system_prompt()) > 100)
    
    # ===============================
    # Tests Émotions
    # ===============================
    print("\n[3] Tests Émotions...")
    
    from src.emotion import EmotionManager, EmotionAnalyzer
    em = EmotionManager()
    await test("EmotionManager créé", lambda: em is not None)
    await test("Mood initial", lambda: em.get_mood() == "neutral")
    
    # Test analyse
    await test("Analyse compliment", lambda: em.process_user_message("Tu es géniale !") is not None)
    await test("Mood après compliment", lambda: em.get_mood() in ["happy", "proud", "touched"])
    await test("Stats émotionnelles", lambda: "happiness" in em.get_stats())
    
    # ===============================
    # Tests Raisonnement
    # ===============================
    print("\n[4] Tests Raisonnement (ReAct)...")
    
    from src.reasoning import ToolRegistry, ReActLoop
    tr = ToolRegistry()
    await test("ToolRegistry créé", lambda: tr is not None)
    await test("11 outils minimum", lambda: len(tr.tools) >= 11)
    await test("Outil web_search", lambda: "web_search" in tr.tools)
    await test("Outil screenshot", lambda: "screenshot" in tr.tools)
    await test("Outil click", lambda: "click" in tr.tools)
    await test("Outil type_text", lambda: "type_text" in tr.tools)
    
    # ===============================
    # Tests Mémoire
    # ===============================
    print("\n[5] Tests Mémoire (ChromaDB)...")
    
    from src.memory import LumenaMemory, CHROMADB_AVAILABLE
    await test("ChromaDB disponible", lambda: CHROMADB_AVAILABLE)
    
    memory = LumenaMemory(Path(__file__).parent / "data" / "test_memory")
    await test("Mémoire créée", lambda: memory is not None)
    
    memory.remember("Test souvenir", "semantic", 0.8)
    await test("Ajout souvenir", lambda: True)
    
    results = memory.recall("Test", limit=1)
    await test("Rappel souvenir", lambda: len(results) >= 1)
    
    memory.learn_fact("test_key", "test_value")
    await test("Apprentissage fait", lambda: memory.get_fact("test_key") == "test_value")
    
    # ===============================
    # Tests Autonomie
    # ===============================
    print("\n[6] Tests Autonomie...")
    
    from src.autonomy import CuriosityModule, GoalManager, LumenaScheduler, GoalType
    
    cm = CuriosityModule()
    await test("CuriosityModule créé", lambda: cm is not None)
    await test("Status curiosité", lambda: "boredom" in cm.get_status())
    await test("Pensée générée", lambda: len(cm.get_thought()) > 0)
    
    gm = GoalManager()
    await test("GoalManager créé", lambda: gm is not None)
    goal = gm.create_goal("Test Goal", "Description", GoalType.LEARNING)
    await test("Création objectif", lambda: goal is not None)
    
    scheduler = LumenaScheduler()
    await test("Scheduler créé", lambda: scheduler is not None)
    scheduler.setup_default_tasks()
    await test("Tâches par défaut", lambda: len(scheduler.get_pending_tasks()) >= 1)
    
    # ===============================
    # Tests Computer Use
    # ===============================
    print("\n[7] Tests Computer Use...")
    
    from src.computer_use import get_computer_use, PYAUTOGUI_AVAILABLE
    cu = get_computer_use()
    await test("ComputerUse créé", lambda: cu is not None)
    await test("PyAutoGUI dispo", lambda: PYAUTOGUI_AVAILABLE)
    await test("Position souris", lambda: cu.mouse.get_position() is not None)
    await test("Fenêtre active", lambda: cu.window.get_active_window() is not None)
    
    # ===============================
    # Tests Voice
    # ===============================
    print("\n[8] Tests Voice...")
    
    from src.voice.tts import LumenaTTS
    tts = LumenaTTS()
    await test("TTS créé", lambda: tts is not None)
    await test("Voix française", lambda: "fr-FR" in tts.voice)
    
    from src.voice.stt import LumenaSTT
    stt = LumenaSTT()
    await test("STT créé", lambda: stt is not None)
    
    # ===============================
    # Tests Learning
    # ===============================
    print("\n[10] Tests Learning...")
    
    from src.learning import SelfReflection, get_self_reflection
    reflection = get_self_reflection()
    await test("SelfReflection créé", lambda: reflection is not None)
    
    entry = reflection.write_entry("Test journal", "test")
    await test("Écriture journal", lambda: entry is not None)
    
    insight = reflection.generate_insight()
    await test("Génération insight", lambda: len(insight) > 0)
    
    # ===============================
    # Résumé
    # ===============================
    print("\n" + "=" * 70)
    print("📊 RÉSUMÉ DES TESTS")
    print("=" * 70)
    
    total = passed + failed
    print(f"""
  Total: {total}
  ✅ Passés: {passed}
  ❌ Échoués: {failed}
  
  Taux de réussite: {passed/total*100:.1f}%
""")
    
    if failed == 0:
        print("🎉 TOUS LES TESTS PASSENT !")
    else:
        print(f"⚠️ {failed} tests ont échoué")
    
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(run_tests())
