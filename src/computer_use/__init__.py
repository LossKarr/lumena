"""
🌟 LUMENA - Module Computer Use

Permet à LUMENA de contrôler l'ordinateur de manière autonome.
"""

from .controller import (
    ComputerUse,
    ComputerController,
    get_controller,
    get_computer_use,
    ScreenCapture,
    MouseController,
    KeyboardController,
    WindowController,
    ScreenRegion,
    ClickAction,
    TypeAction,
    PYAUTOGUI_AVAILABLE,
    SCREENSHOT_AVAILABLE,
)

from .vision import (
    VisionModule,
    get_vision,
    ScreenAnalyzer,
    TextRegion,
    UIElement,
)

from .automation import (
    AppAction,
    AppAutomation,
    AppAutomationRegistry,
    get_app_registry,
    NotepadAutomation,
    BrowserAutomation,
    FileExplorerAutomation,
)

from .cu_agent_loop import (
    CUAgentLoop,
    CUAction,
    CUStepResult,
    CUTaskResult,
    get_cu_agent_loop,
)

from .dom_indexer import (
    DOMIndexer,
    DOMElement,
    DOMSnapshot,
    get_dom_indexer,
    render_set_of_mark,
)

__all__ = [
    # Controller
    "ComputerUse",
    "ComputerController",
    "get_controller",
    "get_computer_use",
    "ScreenCapture",
    "MouseController",
    "KeyboardController",
    "WindowController",
    "ScreenRegion",
    "ClickAction",
    "TypeAction",
    "PYAUTOGUI_AVAILABLE",
    "SCREENSHOT_AVAILABLE",
    
    # Vision
    "VisionModule",
    "get_vision",
    "ScreenAnalyzer",
    "TextRegion",
    "UIElement",
    
    # Automation
    "AppAction",
    "AppAutomation",
    "AppAutomationRegistry",
    "get_app_registry",
    "NotepadAutomation",
    "BrowserAutomation",
    "FileExplorerAutomation",
    
    # CU Agent Loop
    "CUAgentLoop",
    "CUAction",
    "CUStepResult",
    "CUTaskResult",
    "get_cu_agent_loop",
    
    # DOM Indexer
    "DOMIndexer",
    "DOMElement",
    "DOMSnapshot",
    "get_dom_indexer",
    "render_set_of_mark",
]
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU General Public License v3.0 (GPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
