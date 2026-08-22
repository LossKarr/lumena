"""Isolated FastAPI app used only for Document Studio visual certification."""
import asyncio
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from web.routes import document_studio
from src.documents.studio import DocumentStudio


ROOT = Path(__file__).parents[2]
os.environ["LUMENA_ADMIN_TOKEN"] = "document-studio-visual-token"
os.environ["LUMENA_SETUP_COMPLETE"] = "1"
app = FastAPI()
visual_studio = DocumentStudio(
    ROOT / "artifacts" / "document-studio" / "visual-runtime-data",
    builtin_root=ROOT / "assets" / "templates",
    output_root=ROOT / "artifacts" / "document-studio" / "visual-runtime-output",
)
if not visual_studio.logos.list_logos():
    visual_studio.logos.add(
        (ROOT / "web" / "static" / "branding" / "lumena-logo.png").read_bytes(),
        filename="lumena-logo.png",
        name="Lumena",
    )
document_studio.get_document_studio = lambda: visual_studio
app.include_router(document_studio.router)
app.mount("/static", StaticFiles(directory=ROOT / "web" / "static"), name="static")


@app.on_event("startup")
async def seed_revisable_document():
    if any(record.metadata.get("studio_generation") for record in visual_studio.library.list()):
        return
    visual_template = visual_studio.catalog.get("devis")
    visual_data = visual_studio.catalog.read_sample_data(visual_template)
    visual_data["numero"] = "VISUAL-REVISION-2026"
    await visual_studio.generate(
        kind="devis",
        output_format="html",
        data=visual_data,
        filename="devis-revisable",
    )


@app.get("/")
async def index():
    return FileResponse(ROOT / "web" / "index.html")


@app.get("/api/setup/status")
async def setup_status():
    return {"setup_complete": True, "setup_only_mode": False}


@app.get("/api/status")
async def status():
    return {"status": "online", "model": "visual-test", "provider": "local"}


@app.get("/api/health")
async def health():
    return {"status": "online"}


@app.get("/api/tools")
async def tools():
    return {"tools": []}


@app.get("/api/voice/status")
async def voice_status():
    return {"running": False}


@app.get("/api/trace/recent")
async def recent_trace():
    return {"events": []}


@app.get("/api/trace/stream")
async def trace_stream():
    async def keepalive():
        while True:
            yield ": document-studio-visual\n\n"
            await asyncio.sleep(30)

    return StreamingResponse(keepalive(), media_type="text/event-stream")


@app.get("/api/auth/config")
async def auth_config():
    return {"admin_token": "document-studio-visual-token"}


@app.get("/api/models")
async def models():
    return {
        "models": [{
            "name": "visual-test", "display_name": "Document Studio Test",
            "description": "Runtime visuel isole", "provider": "local",
            "available": True, "current": True, "is_local": True,
            "is_free": True, "supports_image_generation": False,
        }]
    }


@app.post("/api/model/switch")
async def model_switch():
    return {"ok": True}
