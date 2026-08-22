"""Proof-carrying document delivery boundary."""
from __future__ import annotations

from dataclasses import asdict
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any, Callable

from .document_library import DocumentLibrary
from .provenance import DocumentTransformation


class DocumentDeliveryService:
    def __init__(self, export_root: Path, library: DocumentLibrary):
        self.export_root = Path(export_root)
        self.export_root.mkdir(parents=True, exist_ok=True)
        self.library = library
        self._connectors: dict[str, Callable[..., dict[str, Any]]] = {}

    def register_connector(self, name: str, sender: Callable[..., dict[str, Any]]) -> None:
        if not name or not callable(sender):
            raise ValueError("connector name and callable are required")
        self._connectors[name] = sender

    def capabilities(self) -> list[str]:
        return ["local_export", *sorted(self._connectors)]

    def export_local(self, document_id: str, filename: str = "") -> dict[str, Any]:
        record = self._record(document_id)
        safe_name = Path(filename or record.filename).name
        if not safe_name or safe_name in {".", ".."}:
            raise ValueError("invalid export filename")
        target = self._available_target(self.export_root / safe_name)
        fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".part", dir=self.export_root)
        os.close(fd)
        temporary = Path(tmp_name)
        try:
            shutil.copy2(record.path, temporary)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return self._record_delivery(record, "local_export", {"path": str(target), "proof": str(target)})

    def deliver(self, document_id: str, connector: str, **kwargs: Any) -> dict[str, Any]:
        record = self._record(document_id)
        sender = self._connectors.get(connector)
        if sender is None:
            raise KeyError(connector)
        result = sender(record=record, **kwargs)
        if not result.get("success") or not result.get("proof"):
            raise RuntimeError(result.get("error") or "connector did not return delivery proof")
        return self._record_delivery(record, connector, result)

    def _record_delivery(self, record, connector: str, result: dict[str, Any]) -> dict[str, Any]:
        transformation = DocumentTransformation.create(
            document_id=record.id, operation="deliver", input_sha256=record.sha256,
            output_sha256=record.sha256, details={"connector": connector, **result},
        )
        self.library.add_transformation(transformation)
        return {"success": True, "connector": connector, "proof": result["proof"], "transformation": asdict(transformation)}

    def _record(self, document_id: str):
        record = self.library.get(document_id)
        if record is None:
            raise KeyError(document_id)
        if not Path(record.path).is_file():
            raise FileNotFoundError(record.path)
        return record

    @staticmethod
    def _available_target(requested: Path) -> Path:
        if not requested.exists():
            return requested
        for index in range(1, 10_000):
            candidate = requested.with_name(f"{requested.stem}-{index}{requested.suffix}")
            if not candidate.exists():
                return candidate
        raise RuntimeError("no available export filename")
