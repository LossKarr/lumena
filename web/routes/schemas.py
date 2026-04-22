"""Pydantic schemas for the Lumena Web API."""
from pydantic import BaseModel, ConfigDict, Field
from typing import Optional, List, Dict, Any


class ChatRequest(BaseModel):
    message: str
    use_agent: bool = False
    channel: str = "web"
    client: Optional[str] = None
    request_id: Optional[str] = None
    conversation_id: Optional[str] = None
    message_id: Optional[str] = None
    client_caps: Dict[str, Any] = Field(default_factory=dict)
    ide_session_id: Optional[str] = None
    workspace_policy: Optional[str] = "default"
    task_id: Optional[str] = None
    workspace_path: Optional[str] = None
    active_file_path: Optional[str] = None
    open_files: List[str] = Field(default_factory=list)
    attachments: List[Dict[str, Any]] = Field(default_factory=list)


class ChatResponse(BaseModel):
    model_config = ConfigDict(protected_namespaces=())
    response: str
    mood: Optional[str] = None
    timestamp: str
    tool_calls: list = Field(default_factory=list)  # Liste des outils utilises
    thinking_steps: list = Field(default_factory=list)  # Etapes de raisonnement
    provider_requested: str = "unknown"
    provider_used: str = "unknown"
    model_requested: str = "unknown"
    model_used: str = "unknown"
    fallback_used: bool = False
    fallback_reason: Optional[str] = None
    continuation_used: bool = False
    continuation_steps: int = 0
    finish_reason: Optional[str] = None
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    agent_output_incomplete: bool = False
    agent_output_warning: Optional[str] = None
    agent_repair_attempts: int = 0
    file_edits: List[Dict[str, Any]] = Field(default_factory=list)
    edit_session_id: Optional[str] = None
    undo_available: bool = False
    created_documents: List[Dict[str, Any]] = Field(default_factory=list)
    request_id: Optional[str] = None
    conversation_id: Optional[str] = None
    task_id: Optional[str] = None
    trace_id: Optional[str] = None


class FileEditItem(BaseModel):
    id: str
    trace_id: str
    turn_id: str
    task_id: Optional[str] = None
    session_id: str
    tool_name: str
    action: str
    file_path: str
    workspace_relative: Optional[str] = None
    additions: int = 0
    deletions: int = 0
    summary: str = ""
    diff_preview: List[str] = Field(default_factory=list)
    before_content: Optional[str] = None
    after_content: Optional[str] = None
    before_truncated: bool = False
    after_truncated: bool = False


class UndoEditsRequest(BaseModel):
    session_id: str
    file_path: Optional[str] = None


class TaskStartRequest(BaseModel):
    conversation_id: str
    channel: str = "web"
    message_preview: str = ""
    metadata: Dict[str, Any] = Field(default_factory=dict)
    task_id: Optional[str] = None


class SearchRequest(BaseModel):
    query: str
    n_results: int = 5


class MemoryRequest(BaseModel):
    query: str
    limit: int = 10
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
