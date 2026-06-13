"""
Todo interne legere pour CodeAgent.

Inspiree du `todowrite` d'OpenCode, mais sans stockage durable ni nouvelle UI:
elle aide seulement la boucle CodeAgent a garder un etat court et utile.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


TodoStatus = Literal["pending", "in_progress", "completed", "blocked"]


@dataclass
class CodeAgentTodoItem:
    content: str
    status: TodoStatus = "pending"
    priority: str = "medium"


@dataclass
class CodeAgentTodoState:
    items: list[CodeAgentTodoItem] = field(default_factory=list)
    last_render: str = ""

    def reset(self) -> None:
        self.items.clear()
        self.last_render = ""

    def set_plan(self, steps: Any) -> str:
        normalized = _normalize_steps(steps)
        if not normalized:
            return ""
        self.items = [
            CodeAgentTodoItem(content=text, status="in_progress" if idx == 0 else "pending")
            for idx, text in enumerate(normalized)
        ]
        return self.render(changed_only=False)

    def observe_action(self, action_type: str, *, path: str = "", success: bool = True) -> str:
        if not self.items:
            return ""
        if not success:
            current = self._current()
            if current:
                current.status = "blocked"
            return self.render(changed_only=True)

        current = self._current()
        if action_type in {"write_file", "edit_file", "edit_lines", "str_replace", "insert_at_anchor", "apply_patch", "apply_patches"}:
            if current:
                current.status = "completed"
            self._start_next()
        elif action_type in {"run_tests", "lint"}:
            if current and "test" in current.content.lower():
                current.status = "completed"
            self._start_next()
        elif action_type == "done":
            for item in self.items:
                if item.status in {"pending", "in_progress"}:
                    item.status = "completed"

        return self.render(changed_only=True)

    def render(self, *, changed_only: bool = True) -> str:
        if not self.items:
            return ""
        lines = ["TODO_CODEAGENT:"]
        for idx, item in enumerate(self.items[:8], start=1):
            lines.append(f"{idx}. [{item.status}] {item.content[:120]}")
        if len(self.items) > 8:
            lines.append(f"... {len(self.items) - 8} autre(s) etape(s)")
        rendered = "\n".join(lines)
        if changed_only and rendered == self.last_render:
            return ""
        self.last_render = rendered
        return rendered

    def _current(self) -> CodeAgentTodoItem | None:
        for item in self.items:
            if item.status == "in_progress":
                return item
        for item in self.items:
            if item.status == "pending":
                item.status = "in_progress"
                return item
        return None

    def _start_next(self) -> None:
        if any(item.status == "in_progress" for item in self.items):
            return
        for item in self.items:
            if item.status == "pending":
                item.status = "in_progress"
                return


def _normalize_steps(steps: Any) -> list[str]:
    if not isinstance(steps, list):
        return []
    normalized: list[str] = []
    for step in steps:
        if isinstance(step, str):
            text = step.strip()
        elif isinstance(step, dict):
            text = str(step.get("title") or step.get("content") or step.get("description") or step.get("file") or "").strip()
        else:
            text = str(step).strip()
        if text:
            normalized.append(text)
    return normalized[:12]


__all__ = ["CodeAgentTodoItem", "CodeAgentTodoState"]
