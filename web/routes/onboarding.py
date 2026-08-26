"""First-run onboarding API. It never mutates Lumena's business runtime."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from src.runtime.onboarding_state import OnboardingStateStore, PROOF_STEPS
from src.runtime.user_profile import get_user_data_dir
from src.utils.paths import DATA_DIR
from web.routes import deps, setup

router = APIRouter(prefix="/api", tags=["onboarding"])


class ProgressRequest(BaseModel):
    step: str = Field(min_length=1, max_length=64)
    event: str | None = Field(default=None, max_length=64)


class SkipRequest(BaseModel):
    steps: list[str] | None = None
    dismiss: bool = True


class GoalRequest(BaseModel):
    goal: str = Field(min_length=1, max_length=32)


def _user_id(request: Request) -> str:
    value = (request.headers.get("X-Lumena-User") or "local:owner").strip()
    return value[:128] or "local:owner"


def _setup_completed() -> bool:
    return setup._is_setup_complete()


def _store(request: Request) -> OnboardingStateStore:
    """Scope state to the active profile without changing single-user paths."""
    user_dir = get_user_data_dir(_user_id(request), data_dir=DATA_DIR)
    return OnboardingStateStore(user_dir / "onboarding" / "state.json")


def _public(state: dict) -> dict:
    return {key: value for key, value in state.items() if "token" not in key.lower() and "secret" not in key.lower()}


@router.get("/onboarding/status")
async def onboarding_status(request: Request):
    return _public(_store(request).load(user_id=_user_id(request), setup_completed=_setup_completed()))


@router.post("/onboarding/start", dependencies=[Depends(deps.verify_admin_token)])
async def onboarding_start(request: Request):
    return _public(_store(request).start(user_id=_user_id(request), setup_completed=_setup_completed()))


@router.post("/onboarding/progress", dependencies=[Depends(deps.verify_admin_token)])
async def onboarding_progress(body: ProgressRequest, request: Request):
    event_map = {
        "mode_selected": "mode_choice",
        "chat_response_received": "first_message",
        "agent_progress_observed": "work_progress",
    }
    proven_step = event_map.get(body.event or "")
    if body.step in PROOF_STEPS and proven_step != body.step:
        raise HTTPException(status_code=409, detail="Cette etape exige un evenement applicatif reel.")
    try:
        state = _store(request).progress(
            body.step,
            user_id=_user_id(request),
            proven=proven_step == body.step,
            setup_completed=_setup_completed(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _public(state)


@router.post("/onboarding/skip", dependencies=[Depends(deps.verify_admin_token)])
async def onboarding_skip(body: SkipRequest, request: Request):
    try:
        state = _store(request).skip(
            body.steps,
            dismiss=body.dismiss,
            user_id=_user_id(request),
            setup_completed=_setup_completed(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _public(state)


@router.post("/onboarding/goal", dependencies=[Depends(deps.verify_admin_token)])
async def onboarding_goal(body: GoalRequest, request: Request):
    try:
        state = _store(request).select_goal(
            body.goal,
            user_id=_user_id(request),
            setup_completed=_setup_completed(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _public(state)


@router.post("/onboarding/complete", dependencies=[Depends(deps.verify_admin_token)])
async def onboarding_complete(request: Request):
    return _public(_store(request).complete(user_id=_user_id(request), setup_completed=_setup_completed()))


@router.post("/onboarding/reset", dependencies=[Depends(deps.verify_admin_token)])
async def onboarding_reset(request: Request):
    return _public(_store(request).reset(user_id=_user_id(request), setup_completed=_setup_completed()))


__all__ = ["router"]
