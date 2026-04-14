"""
Run Lumena Twitter/X bot in manual debug mode.
Usage: python run_twitter.py

Requires env vars:
    TWITTER_BEARER_TOKEN        — For reading (mentions, timeline, search)
    TWITTER_API_KEY             — For writing (tweets, replies, likes)
    TWITTER_API_SECRET
    TWITTER_ACCESS_TOKEN
    TWITTER_ACCESS_TOKEN_SECRET
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
    print("LUMENA TWITTER/X BOT")
    print("=" * 40)
    print("Mode debug manuel")

    try:
        from src.channels.twitter_channel import TwitterChannel
        from src.autonomy.daemon import LumenaDaemon

        print("Loading Lumena...")
        daemon = LumenaDaemon()
        await daemon.start()
        print("Lumena + Daemon initialized")

        twitter = TwitterChannel()

        if not twitter.is_available:
            print("\nERROR: Twitter non disponible")
            print("Vérifier:")
            if not os.getenv("TWITTER_BEARER_TOKEN"):
                print("  - TWITTER_BEARER_TOKEN manquant")
            if not os.getenv("TWITTER_API_KEY"):
                print("  - TWITTER_API_KEY manquant (nécessaire pour écrire)")
            print("  - pip install tweepy>=4.14.0")
            await daemon.stop()
            return

        async def process_mention(msg):
            print(f"Mention from @{msg.username}: {msg.content[:80]}...")
            response = await daemon.chat(msg.content)
            # Tronquer pour Twitter (280 chars - @username - espace)
            max_len = 280 - len(msg.username) - 2
            if len(response) > max_len:
                response = response[:max_len - 3] + "..."
            print(f"Response: {response[:80]}...")
            return response

        twitter.set_message_callback(process_mention)

        success = await twitter.start()
        if success:
            status = twitter.get_runtime_status()
            print("\n" + "=" * 40)
            print("BOT TWITTER/X DÉMARRÉ")
            print(f"Écriture: {'✅' if status['can_write'] else '❌ (read-only)'}")
            print(f"Polling mentions: toutes les {status['poll_interval']}s")
            print("Ctrl+C pour arrêter")
            print("=" * 40)
            try:
                while twitter.is_running:
                    await asyncio.sleep(1)
            except KeyboardInterrupt:
                print("\nStopping bot...")
                await twitter.stop()
                await daemon.stop()
        else:
            reason = twitter.last_error or "startup failed"
            print(f"ERROR: Échec démarrage Twitter ({reason})")
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
