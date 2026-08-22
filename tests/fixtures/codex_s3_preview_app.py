"""Minimal no-lifespan app used only for Codex subscription visual QA."""

from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from web.routes import codex_subscription, config
from web.routes.deps import verify_admin_token
from src.llm.codex_collaboration import CodexCollaborationRegistry
from src.llm.codex_subscription import CodexSurface, OpenAIAccessMode


ROOT = Path(__file__).resolve().parents[2]
app = FastAPI()
app.dependency_overrides[verify_admin_token] = lambda: None
app.include_router(config.router)
app.include_router(codex_subscription.router)
app.mount("/static", StaticFiles(directory=ROOT / "web/static"), name="static")


class _VisualSupervisor:
    is_running = True

    def snapshot(self):
        return SimpleNamespace(
            state="running",
            pid=1,
            pending_requests=0,
            queued_notifications=0,
            dropped_notifications=0,
            restart_count=0,
            stderr_tail="",
            last_error="",
        )

    async def request(self, method, params=None, *, timeout=None):
        if method == "account/read":
            return {"account": {"type": "chatgpt", "plan": "Plus", "email": "qa@example.test"}}
        if method == "account/rateLimits/read":
            return {"rateLimits": {"primary": {"usedPercent": 18}}}
        if method == "model/list":
            return {
                "models": [
                    {"id": "account-default", "displayName": "Codex recommande", "isDefault": True},
                    {"id": "account-fast", "displayName": "Codex rapide"},
                ]
            }
        if method == "thread/list":
            return {
                "data": [
                    {
                        "id": "thr-qa-1",
                        "cwd": str(ROOT),
                        "name": "Audit interface Lumena",
                        "preview": "Verifier la configuration et les regressions visuelles.",
                        "status": {"type": "idle"},
                    },
                    {
                        "id": "thr-qa-2",
                        "cwd": str(ROOT),
                        "name": "Correction des tests",
                        "preview": "Une approbation utilisateur est attendue.",
                        "status": {"type": "active", "activeFlags": ["waitingOnApproval"]},
                    },
                ],
                "nextCursor": None,
            }
        if method == "thread/read":
            return {
                "thread": {
                    "id": params["threadId"],
                    "cwd": str(ROOT),
                    "name": "Audit interface Lumena",
                    "status": {"type": "idle"},
                    "turns": [],
                }
            }
        raise RuntimeError(f"Visual fixture method not implemented: {method}")


app.state.codex_app_server = _VisualSupervisor()
app.state.codex_collaboration_registry = CodexCollaborationRegistry(
    ROOT / "tests" / ".visual-codex-collaboration.json"
)

_visual_selection = {
    "access_mode": OpenAIAccessMode.CHATGPT_CODEX.value,
    "api_model": "visual-qa-model",
    "codex_model": "account-default",
}


def _visual_settings():
    return SimpleNamespace(
        access_mode=OpenAIAccessMode(_visual_selection["access_mode"]),
        enabled=True,
        default_model=_visual_selection["codex_model"],
        surfaces=(
            CodexSurface.CODEAGENT,
            CodexSurface.CHAT,
            CodexSurface.AGENT,
            CodexSurface.MISSIONS,
        ),
    )


def _visual_persist(updates):
    mode = updates.get("LUMENA_OPENAI_ACCESS_MODE")
    model = updates.get("LUMENA_CODEX_DEFAULT_MODEL")
    if mode:
        _visual_selection["access_mode"] = mode
    if model:
        _visual_selection["codex_model"] = model


async def _visual_api_switch(model_name):
    _visual_selection["api_model"] = model_name
    return {
        "success": True,
        "model": model_name,
        "display_name": "Visual QA",
        "message": "Modele API Visual QA actif",
    }


codex_subscription.load_codex_subscription_settings = _visual_settings
codex_subscription._persist_access_selection = _visual_persist
codex_subscription._switch_historical_api_model = _visual_api_switch


@app.get("/api/auth/config")
async def auth_config():
    return {"admin_token": ""}


@app.get("/api/models")
async def models():
    return {
        "models": [
            {
                "name": "visual-qa-model",
                "display_name": "Visual QA",
                "description": "Modele factice reserve a la previsualisation S3.",
                "provider": "test",
                "available": True,
                "current": True,
                "is_free": True,
                "is_local": True,
                "supports_image_generation": False,
            }
        ]
    }


@app.get("/api/visual-selection")
async def visual_selection():
    return dict(_visual_selection)


@app.post("/api/model/switch")
async def switch_model():
    return {"success": True, "model": "visual-qa-model"}


@app.get("/")
async def index():
    return FileResponse(ROOT / "web/index.html")
