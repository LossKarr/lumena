"""
Run Lumena WhatsApp channel in standalone debug mode.
Usage: python run_whatsapp.py

Starts a minimal FastAPI server with the WhatsApp webhook endpoint.
This mode is for testing/debug only — in production, use web/server.py.
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
    print("LUMENA WHATSAPP CHANNEL")
    print("=" * 40)
    print("Mode debug manuel: ne pas lancer en parallele de web/server.py")

    try:
        from src.channels.whatsapp_channel import WhatsAppChannel
        from src.core import LumenaCore
        from src.utils.file_lock import ProcessFileLock, default_lock_path
        from src.autonomy.daemon import LumenaDaemon

        # If web/server.py is already running, avoid parallel instance.
        web_lock_path = Path(
            os.getenv("LUMENA_INSTANCE_LOCK_PATH", str(default_lock_path("lumena_web.lock")))
        )
        web_probe = ProcessFileLock(web_lock_path, lock_name="lumena-web-probe")
        if not web_probe.acquire():
            holder = web_probe.read_lock_info()
            holder_pid = holder.get("pid", "unknown")
            print("\nERROR: web/server.py semble deja actif")
            print(f"lock: {web_lock_path} (pid={holder_pid})")
            print("Arrete le serveur web avant de lancer run_whatsapp.py")
            return
        web_probe.release()

        print("Loading Lumena...")
        daemon = LumenaDaemon()
        await daemon.start()
        print("Lumena + Daemon initialized")

        whatsapp = WhatsAppChannel()

        if not whatsapp.is_available:
            print("\nERROR: WhatsApp non disponible")
            print("Verifie WHATSAPP_ACCESS_TOKEN et WHATSAPP_PHONE_NUMBER_ID")
            await daemon.stop()
            return

        async def process_message(msg):
            print(f"Message from {msg.user_id}: {msg.content[:50]}...")
            response = await daemon.chat(msg.content)
            print(f"Response: {response[:50]}...")
            return response

        whatsapp.set_message_callback(process_message)

        success = await whatsapp.start()
        if not success:
            reason = whatsapp.last_error or "startup failed"
            print(f"ERROR: Echec demarrage WhatsApp ({reason})")
            await daemon.stop()
            return

        print("\n" + "=" * 40)
        print("WHATSAPP CHANNEL + DAEMON DEMARRE")
        print("Demarrage du serveur webhook...")
        print("=" * 40)

        # Start a minimal FastAPI server for the webhook
        import uvicorn
        from fastapi import FastAPI

        app = FastAPI(title="Lumena WhatsApp Debug")

        @app.get("/api/whatsapp/webhook")
        async def verify_webhook(request):
            from starlette.requests import Request
            from starlette.responses import PlainTextResponse
            mode = request.query_params.get("hub.mode")
            token = request.query_params.get("hub.verify_token")
            challenge = request.query_params.get("hub.challenge")
            result = whatsapp.verify_webhook(mode, token, challenge)
            if result is not None:
                return PlainTextResponse(result)
            return PlainTextResponse("Forbidden", status_code=403)

        @app.post("/api/whatsapp/webhook")
        async def receive_webhook(request):
            from starlette.requests import Request
            from starlette.responses import JSONResponse
            body = await request.json()
            signature = request.headers.get("X-Hub-Signature-256", "")
            raw_body = await request.body()
            if whatsapp.app_secret and not whatsapp.validate_signature(raw_body, signature):
                return JSONResponse({"error": "Invalid signature"}, status_code=403)
            asyncio.create_task(whatsapp.handle_webhook(body))
            return JSONResponse({"status": "ok"})

        @app.get("/api/whatsapp/status")
        async def get_status():
            return whatsapp.get_runtime_status()

        host = os.getenv("LUMENA_HOST", "127.0.0.1")
        port = int(os.getenv("LUMENA_PORT", "8099"))
        print(f"Webhook URL: http://{host}:{port}/api/whatsapp/webhook")
        print("Configure cette URL + /api/whatsapp/webhook dans Meta Developer Console")
        print("Ctrl+C pour arreter")

        config = uvicorn.Config(app, host=host, port=port, log_level="info")
        server = uvicorn.Server(config)

        try:
            await server.serve()
        except KeyboardInterrupt:
            pass
        finally:
            print("\nStopping WhatsApp...")
            await whatsapp.stop()
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
