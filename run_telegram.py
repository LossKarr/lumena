"""
Run Lumena Telegram bot in manual debug mode.
Usage: python run_telegram.py
"""

import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# Load env vars
load_dotenv()

# Add repo root to import path
sys.path.insert(0, str(Path(__file__).parent))


async def main() -> None:
    print("LUMENA TELEGRAM BOT")
    print("=" * 40)
    print("Mode debug manuel: ne pas lancer en parallele de web/server.py")

    try:
        from src.channels.telegram_channel import TelegramChannel
        from src.core import LumenaCore
        from src.utils.file_lock import ProcessFileLock, default_lock_path
        from src.autonomy.daemon import LumenaDaemon

        # If web/server.py is already running, avoid parallel polling instance.
        web_lock_path = Path(
            os.getenv("LUMENA_INSTANCE_LOCK_PATH", str(default_lock_path("lumena_web.lock")))
        )
        web_probe = ProcessFileLock(web_lock_path, lock_name="lumena-web-probe")
        if not web_probe.acquire():
            holder = web_probe.read_lock_info()
            holder_pid = holder.get("pid", "unknown")
            print("\nERROR: web/server.py semble deja actif")
            print(f"lock: {web_lock_path} (pid={holder_pid})")
            print("Arrete le serveur web avant de lancer run_telegram.py")
            return
        web_probe.release()

        print("Loading Lumena...")
        daemon = LumenaDaemon()
        await daemon.start()
        print("Lumena + Daemon initialized")

        telegram = TelegramChannel()

        if not telegram.is_available:
            print("\nERROR: Telegram non disponible")
            print("Verifie TELEGRAM_TOKEN et python-telegram-bot")
            await daemon.stop()
            return

        async def process_message(msg):
            print(f"Message from {msg.username}: {msg.content[:50]}...")
            # daemon.chat() appelle user_interaction() → met à jour last_user_activity
            # et interrompt les actions autonomes en cours si besoin
            response = await daemon.chat(msg.content)
            print(f"Response: {response[:50]}...")
            return response

        telegram.set_message_callback(process_message)

        success = await telegram.start()
        if success:
            print("\n" + "=" * 40)
            print("BOT TELEGRAM + DAEMON DEMARRE")
            print("Envoie un message au bot sur Telegram")
            print("Ctrl+C pour arreter")
            print("=" * 40)
            try:
                while telegram.is_running:
                    await asyncio.sleep(1)
            except KeyboardInterrupt:
                print("\nStopping bot...")
                await telegram.stop()
                await daemon.stop()
        else:
            reason = telegram.last_error or "startup failed"
            print(f"ERROR: Echec demarrage Telegram ({reason})")
            await daemon.stop()

    except Exception as e:
        print(f"\nERROR: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nGoodbye")
