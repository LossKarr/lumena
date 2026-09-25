"""Voice V2 compatibility adapter for the shared runtime work registry."""
from src.runtime.work_registry import (
    ActiveWorkRegistry,
    WorkNotificationTracker,
    WorkSnapshot,
    classify_work_turn,
)

__all__ = [
    "ActiveWorkRegistry",
    "WorkNotificationTracker",
    "WorkSnapshot",
    "classify_work_turn",
]
