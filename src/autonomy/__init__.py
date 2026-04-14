"""
🌟 LUMENA - Module d'Autonomie

Ce module contient tout ce qui permet à LUMENA de fonctionner
de manière autonome 24/7.
"""

from .curiosity import (
    CuriosityModule,
    get_curiosity_module,
    AutonomousAction,
    ActionType,
)

from .goals import (
    GoalManager,
    get_goal_manager,
    Goal,
    GoalType,
    GoalPriority,
    GoalStatus,
)

from .scheduler import (
    LumenaScheduler,
    get_scheduler,
    ScheduledTask,
    TaskFrequency,
    TaskStatus,
)

from .daemon import (
    LumenaDaemon,
    get_daemon,
    run_daemon,
)

from .heartbeat import (
    HeartbeatSystem,
    HeartbeatConfig,
    HeartbeatTask,
    get_heartbeat,
)

__all__ = [
    # Curiosité
    "CuriosityModule",
    "get_curiosity_module",
    "AutonomousAction",
    "ActionType",
    
    # Objectifs
    "GoalManager",
    "get_goal_manager",
    "Goal",
    "GoalType",
    "GoalPriority",
    "GoalStatus",
    
    # Scheduler
    "LumenaScheduler",
    "get_scheduler",
    "ScheduledTask",
    "TaskFrequency",
    "TaskStatus",
    
    # Daemon
    "LumenaDaemon",
    "get_daemon",
    "run_daemon",
    
    # Heartbeat
    "HeartbeatSystem",
    "HeartbeatConfig",
    "HeartbeatTask",
    "get_heartbeat",
]
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the Apache License, Version 2.0
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
