import pytest
from unittest.mock import AsyncMock

from src.computer_use.automation import AppAutomation
from src.computer_use.vision import VisionModule


def test_scale_coordinates_handles_padding_and_scaling():
    vision = VisionModule.__new__(VisionModule)

    # LLM voit une image paddée: coord(120,80) avec padding (20,10)
    # Coord utile = (100,70), puis upscale x2 (scale=0.5) => (200,140)
    x, y = vision.scale_coordinates_to_screen(
        120,
        80,
        0.5,
        pad_offset_x=20,
        pad_offset_y=10,
    )

    assert x == 200
    assert y == 140


@pytest.mark.asyncio
async def test_app_automation_close_awaits_focus(monkeypatch):
    app = AppAutomation("dummy")

    app.focus = AsyncMock(return_value=True)
    app.computer.close_window = AsyncMock(return_value=True)

    ok = await app.close()

    assert ok is True
    app.focus.assert_awaited_once()
    app.computer.close_window.assert_awaited_once()
