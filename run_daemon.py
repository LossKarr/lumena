"""
🌟 LUMENA - Daemon 24/7 (Point d'entrée)

Script pour lancer le daemon en mode autonome.
"""

import asyncio
import sys
from pathlib import Path

# Ajouter le dossier parent au path
sys.path.insert(0, str(Path(__file__).parent))

from src.autonomy.daemon import run_daemon

if __name__ == "__main__":
    print("🌟 LUMENA Daemon 24/7 - Démarrage...")
    print("=" * 50)
    try:
        asyncio.run(run_daemon())
    except KeyboardInterrupt:
        print("\n👋 Daemon arrêté par l'utilisateur")
    except Exception as e:
        print(f"❌ Erreur daemon: {e}")
        input("Appuyez sur Entrée pour fermer...")
