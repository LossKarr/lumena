"""Normalized omnichannel request envelope."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Dict, Optional
import os
import uuid


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _normalize_channel(value: Optional[str]) -> str:
    normalized = (value or "web").strip().lower()
    allowed = {"web", "ide", "telegram", "discord", "api", "whatsapp"}
    if os.getenv("LUMENA_WEB_ONLY", "").strip().lower() in {"1", "true", "yes", "on"}:
        return "web"
    if normalized not in allowed:
        return "web"
    return normalized


@dataclass(frozen=True)
class ChannelEnvelope:
    channel: str
    client: str
    request_id: str
    conversation_id: str
    message_id: str
    task_id: Optional[str] = None
    client_caps: Dict[str, Any] = field(default_factory=dict)
    conversation_source: str = "explicit"
    ts_utc: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    schema_version: int = 1

    @classmethod
    def from_request(
        cls,
        *,
        channel: Optional[str],
        client: Optional[str],
        request_id: Optional[str],
        conversation_id: Optional[str],
        message_id: Optional[str],
        task_id: Optional[str],
        client_caps: Optional[Dict[str, Any]],
    ) -> "ChannelEnvelope":
        return cls(
            channel=_normalize_channel(channel),
            client=(client or "unknown").strip() or "unknown",
            request_id=(request_id or _new_id("req")).strip(),
            conversation_id=(conversation_id or _new_id("conv")).strip(),
            message_id=(message_id or _new_id("msg")).strip(),
            task_id=(task_id or "").strip() or None,
            client_caps=dict(client_caps or {}),
            conversation_source="explicit" if (conversation_id or "").strip() else "generated",
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def with_conversation(
        self,
        *,
        conversation_id: str,
        conversation_source: str,
    ) -> "ChannelEnvelope":
        resolved_id = str(conversation_id or "").strip() or self.conversation_id
        resolved_source = str(conversation_source or "").strip() or self.conversation_source
        return replace(
            self,
            conversation_id=resolved_id,
            conversation_source=resolved_source,
        )


@dataclass
class _ContinuityRecord:
    conversation_id: str
    first_seen_utc: str
    last_seen_utc: str
    last_channel: str
    last_client: str
    last_request_id: str
    last_task_id: Optional[str] = None
    hits: int = 0


class ChannelContinuityRegistry:
    """
    Runtime continuity map for omnichannel requests.

    Resolution order:
    1) explicit conversation id
    2) task_id continuity
    3) client/session continuity
    4) generated envelope conversation id
    """

    def __init__(self, max_records: int = 10000) -> None:
        self._lock = Lock()
        self._max_records = max(100, int(max_records))
        self._task_index: Dict[str, str] = {}
        self._session_index: Dict[str, str] = {}
        self._records: Dict[str, _ContinuityRecord] = {}
        self._source_hits: Dict[str, int] = {
            "explicit": 0,
            "task": 0,
            "client_session": 0,
            "generated": 0,
        }
        self._task_rebinds = 0
        self._session_rebinds = 0

    @staticmethod
    def _session_key(client: str, client_caps: Dict[str, Any]) -> Optional[str]:
        normalized_client = str(client or "").strip().lower()
        if not normalized_client:
            return None
        raw_session = (
            client_caps.get("session_id")
            or client_caps.get("session")
            or client_caps.get("conversation_slot")
            or ""
        )
        session = str(raw_session).strip().lower()
        if not session:
            return normalized_client
        return f"{normalized_client}::{session}"

    def _ensure_record_locked(self, envelope: ChannelEnvelope) -> _ContinuityRecord:
        record = self._records.get(envelope.conversation_id)
        now_utc = datetime.now(timezone.utc).isoformat()
        if record is None:
            record = _ContinuityRecord(
                conversation_id=envelope.conversation_id,
                first_seen_utc=now_utc,
                last_seen_utc=now_utc,
                last_channel=envelope.channel,
                last_client=envelope.client,
                last_request_id=envelope.request_id,
                last_task_id=envelope.task_id,
                hits=0,
            )
            self._records[envelope.conversation_id] = record
        return record

    def _touch_record_locked(self, envelope: ChannelEnvelope) -> None:
        record = self._ensure_record_locked(envelope)
        record.last_seen_utc = datetime.now(timezone.utc).isoformat()
        record.last_channel = envelope.channel
        record.last_client = envelope.client
        record.last_request_id = envelope.request_id
        if envelope.task_id:
            record.last_task_id = envelope.task_id
        record.hits += 1

    def _bind_indexes_locked(self, envelope: ChannelEnvelope) -> None:
        if envelope.task_id:
            current = self._task_index.get(envelope.task_id)
            if current and current != envelope.conversation_id:
                self._task_rebinds += 1
            self._task_index[envelope.task_id] = envelope.conversation_id

        session_key = self._session_key(envelope.client, envelope.client_caps)
        if session_key:
            current = self._session_index.get(session_key)
            if current and current != envelope.conversation_id:
                self._session_rebinds += 1
            self._session_index[session_key] = envelope.conversation_id

    def _prune_locked(self) -> None:
        if len(self._records) <= self._max_records:
            return
        overflow = len(self._records) - self._max_records
        ordered = sorted(
            self._records.values(),
            key=lambda item: item.last_seen_utc,
        )
        to_drop = {item.conversation_id for item in ordered[:overflow]}
        for conversation_id in to_drop:
            self._records.pop(conversation_id, None)

        self._task_index = {
            task_id: conversation_id
            for task_id, conversation_id in self._task_index.items()
            if conversation_id in self._records
        }
        self._session_index = {
            key: conversation_id
            for key, conversation_id in self._session_index.items()
            if conversation_id in self._records
        }

    def resolve(self, envelope: ChannelEnvelope) -> ChannelEnvelope:
        with self._lock:
            resolved = envelope
            source = "generated"

            if envelope.conversation_source == "explicit":
                source = "explicit"
            elif envelope.task_id and envelope.task_id in self._task_index:
                resolved = envelope.with_conversation(
                    conversation_id=self._task_index[envelope.task_id],
                    conversation_source="task",
                )
                source = "task"
            else:
                session_key = self._session_key(envelope.client, envelope.client_caps)
                if session_key and session_key in self._session_index:
                    resolved = envelope.with_conversation(
                        conversation_id=self._session_index[session_key],
                        conversation_source="client_session",
                    )
                    source = "client_session"

            if source == "explicit":
                resolved = resolved.with_conversation(
                    conversation_id=resolved.conversation_id,
                    conversation_source="explicit",
                )
            elif source == "generated":
                resolved = resolved.with_conversation(
                    conversation_id=resolved.conversation_id,
                    conversation_source="generated",
                )

            self._source_hits[source] = int(self._source_hits.get(source, 0)) + 1
            self._bind_indexes_locked(resolved)
            self._touch_record_locked(resolved)
            self._prune_locked()
            return resolved

    def get_conversation(self, conversation_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            record = self._records.get(conversation_id)
            return asdict(record) if record else None

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "records_total": len(self._records),
                "task_links": len(self._task_index),
                "session_links": len(self._session_index),
                "task_rebinds": self._task_rebinds,
                "session_rebinds": self._session_rebinds,
                "source_hits": dict(self._source_hits),
                "max_records": self._max_records,
            }
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU General Public License v3.0 (GPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
