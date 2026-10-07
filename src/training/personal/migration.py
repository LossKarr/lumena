"""Read-only migration of historical Lumena training JSONL files."""

from __future__ import annotations

import hashlib
import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .contracts import ExperienceState, LearningExperienceV1, new_id, utc_now
from .experience_store import ExperienceStore
from .redaction import redact_value


@dataclass(slots=True)
class MigrationReport:
    source_files: int = 0
    source_lines: int = 0
    imported: int = 0
    duplicates: int = 0
    invalid: int = 0
    blocked: int = 0
    source_hashes: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_files": self.source_files,
            "source_lines": self.source_lines,
            "imported": self.imported,
            "duplicates": self.duplicates,
            "invalid": self.invalid,
            "blocked": self.blocked,
            "source_hashes": dict(self.source_hashes),
        }


def _normalise_messages(entry: dict[str, Any]) -> list[dict[str, str]]:
    raw = entry.get("messages") or entry.get("conversations") or []
    messages = []
    role_map = {"human": "user", "user": "user", "gpt": "assistant", "assistant": "assistant", "system": "system", "tool": "tool"}
    for item in raw:
        if not isinstance(item, dict):
            continue
        role = role_map.get(str(item.get("role", item.get("from", ""))).lower())
        content = item.get("content", item.get("value", ""))
        if role and isinstance(content, str) and content.strip():
            messages.append({"role": role, "content": content})
    if not messages or not any(item["role"] == "user" for item in messages) or not any(item["role"] == "assistant" for item in messages):
        raise ValueError("historical_messages_invalid")
    return messages


def _quality_state(meta: dict[str, Any]) -> ExperienceState:
    flag = str(meta.get("quality_flag", "")).lower()
    if flag in {"negative_feedback", "negative_explicit", "incomplete"}:
        return ExperienceState.REJECTED
    if meta.get("judge_score") is not None or meta.get("judged_by") or meta.get("judge_model"):
        return ExperienceState.ACCEPTED if float(meta.get("judge_score", 10) or 0) >= 6.5 else ExperienceState.REJECTED
    return ExperienceState.CANDIDATE


class HistoricalTrainingMigrator:
    def __init__(self, store: ExperienceStore) -> None:
        self.store = store

    def migrate(self, sources: Iterable[Path], *, owner_scope: str = "owner:local") -> MigrationReport:
        report = MigrationReport()
        for source in sorted((Path(path) for path in sources), key=lambda path: str(path)):
            if not source.is_file():
                continue
            raw_bytes = source.read_bytes()
            report.source_files += 1
            report.source_hashes[source.name] = hashlib.sha256(raw_bytes).hexdigest()
            for raw_line in raw_bytes.decode("utf-8", errors="replace").splitlines():
                if not raw_line.strip():
                    continue
                report.source_lines += 1
                try:
                    entry = json.loads(raw_line)
                    if not isinstance(entry, dict):
                        raise ValueError("historical_entry_invalid")
                    meta = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
                    messages = _normalise_messages(entry)
                    redacted = redact_value({"messages": messages, "metadata": meta}).value
                    goal = next((item["content"] for item in redacted["messages"] if item["role"] == "user"), "historical import")
                    provider = str(meta.get("provider", meta.get("teacher_provider", "")))[:64]
                    model = str(meta.get("model", meta.get("teacher_model", "")))[:128]
                    experience = LearningExperienceV1(
                        experience_id=new_id("exp"),
                        owner_scope=owner_scope,
                        source_surface=str(meta.get("source_surface", meta.get("channel", "historical")))[:64] or "historical",
                        mode=str(meta.get("mode", "chat"))[:64] or "chat",
                        created_at=str(meta.get("timestamp", entry.get("created_at", utc_now()))),
                        goal=goal[:4000],
                        public_context={"migration_source_hash": report.source_hashes[source.name]},
                        messages=tuple(redacted["messages"]),
                        teacher_provider=provider,
                        teacher_model=model,
                        quality_state=_quality_state(meta),
                        privacy_state="redacted",
                        license_policy=str(meta.get("license_policy", "user_owned")),
                        semantic_cluster=hashlib.sha256(goal.lower().encode("utf-8")).hexdigest()[:16],
                    )
                    result = self.store.append(experience)
                    if result.appended:
                        report.imported += 1
                    else:
                        report.duplicates += 1
                except ValueError as exc:
                    if "sensitive" in str(exc) or "privacy" in str(exc):
                        report.blocked += 1
                    else:
                        report.invalid += 1
                except (json.JSONDecodeError, TypeError, KeyError):
                    report.invalid += 1
        return report

    @staticmethod
    def preview(sources: Iterable[Path], *, owner_scope: str = "owner:local") -> MigrationReport:
        """Run the exact importer against an ephemeral store; source files stay read-only."""
        with tempfile.TemporaryDirectory(prefix="lumena-personal-migration-preview-") as temporary:
            return HistoricalTrainingMigrator(ExperienceStore(Path(temporary))).migrate(sources, owner_scope=owner_scope)
