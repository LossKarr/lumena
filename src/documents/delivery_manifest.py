"""Structured proof for documents produced by agent tools."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import posixpath
from typing import Any, Iterable


@dataclass(frozen=True)
class DocumentDeliveryProof:
    kind: str
    document_id: str
    filename: str
    path: str
    sha256: str
    template_id: str
    format: str
    size: int
    logo_id: str
    render_status: str
    render_verified: bool
    thumbnail_path: str = ""
    page_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "document_id": self.document_id,
            "filename": self.filename,
            "path": self.path,
            "sha256": self.sha256,
            "template_id": self.template_id,
            "format": self.format,
            "size": self.size,
            "logo_id": self.logo_id,
            "render_status": self.render_status,
            "render_verified": self.render_verified,
            "thumbnail_path": self.thumbnail_path,
            "page_count": self.page_count,
        }


def compact_generation_payload(result: dict[str, Any], *, kind: str) -> dict[str, Any]:
    """Reduce a Studio result to the stable evidence needed by ReAct."""
    record = result.get("record") if isinstance(result.get("record"), dict) else {}
    recipe = result.get("recipe") if isinstance(result.get("recipe"), dict) else {}
    render = result.get("render_proof") if isinstance(result.get("render_proof"), dict) else {}
    path = str(result.get("path") or "")
    try:
        page_count = int(render.get("page_count") or 0)
    except (TypeError, ValueError):
        page_count = 0
    return {
        "kind": str(kind or recipe.get("kind") or ""),
        "document_id": str(record.get("id") or ""),
        "filename": Path(path).name if path else str(record.get("filename") or ""),
        "path": path,
        "sha256": str(record.get("sha256") or ""),
        "template_id": str(record.get("template_id") or recipe.get("template_id") or ""),
        "format": str(record.get("format") or recipe.get("output_format") or ""),
        "size": int(record.get("size") or 0),
        "logo_id": str(recipe.get("logo_id") or render.get("logo_id") or ""),
        "render_status": str(render.get("status") or "not_checked"),
        "render_verified": bool(render.get("verified", False)),
        "thumbnail_path": str(render.get("thumbnail_path") or ""),
        "page_count": page_count,
    }


def compact_batch_observation(content: str, *, error_limit: int = 2600) -> str | None:
    """Keep exact batch progress and retry evidence in model-visible history."""
    try:
        raw = json.loads(str(content or "").strip())
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict) or not {
        "requested", "generated", "failed",
    }.issubset(raw):
        return None

    documents: list[dict[str, Any]] = []
    for item in raw.get("documents") or []:
        if not isinstance(item, dict):
            continue
        try:
            page_count = max(0, int(item.get("page_count") or 0))
        except (TypeError, ValueError):
            page_count = 0
        documents.append({
            "kind": str(item.get("kind") or ""),
            "filename": str(item.get("filename") or ""),
            "render_verified": bool(item.get("render_verified", False)),
            "page_count": page_count,
        })

    errors: list[dict[str, Any]] = []
    for item in raw.get("errors") or []:
        if not isinstance(item, dict):
            continue
        message = str(item.get("error") or "")
        if len(message) > error_limit:
            message = message[:error_limit] + "..."
        errors.append({
            "index": int(item.get("index") or 0),
            "kind": str(item.get("kind") or ""),
            "template_id": str(item.get("template_id") or ""),
            "error": message,
        })

    compact = {
        "requested": int(raw.get("requested") or 0),
        "generated": int(raw.get("generated") or 0),
        "failed": int(raw.get("failed") or 0),
        "receipt_id": str(raw.get("receipt_id") or ""),
        "documents": documents,
        "errors": errors,
    }
    if raw.get("phase"):
        compact["phase"] = str(raw.get("phase"))
    return json.dumps(compact, ensure_ascii=False, separators=(",", ":"))


def parse_generation_proof(content: str, *, fallback_kind: str = "") -> DocumentDeliveryProof | None:
    """Parse current compact payloads and the earlier full Studio result shape."""
    try:
        raw = json.loads(str(content or "").strip())
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    if "record" in raw or "render_proof" in raw:
        raw = compact_generation_payload(raw, kind=fallback_kind)
    path = str(raw.get("path") or "").strip()
    filename = str(raw.get("filename") or (Path(path).name if path else "")).strip()
    kind = str(raw.get("kind") or fallback_kind or "").strip()
    if not kind or not path or not filename:
        return None
    try:
        size = int(raw.get("size") or 0)
    except (TypeError, ValueError):
        size = 0
    try:
        page_count = int(raw.get("page_count") or 0)
    except (TypeError, ValueError):
        page_count = 0
    return DocumentDeliveryProof(
        kind=kind,
        document_id=str(raw.get("document_id") or ""),
        filename=filename,
        path=path,
        sha256=str(raw.get("sha256") or ""),
        template_id=str(raw.get("template_id") or ""),
        format=str(raw.get("format") or Path(filename).suffix.lstrip(".")),
        size=size,
        logo_id=str(raw.get("logo_id") or ""),
        render_status=str(raw.get("render_status") or "not_checked"),
        render_verified=bool(raw.get("render_verified", False)),
        thumbnail_path=str(raw.get("thumbnail_path") or ""),
        page_count=page_count,
    )


def build_multi_document_final(
    proofs: Iterable[DocumentDeliveryProof], *, requested_count: int, receipt_id: str = "",
) -> str:
    rows = list(proofs)
    verified = sum(1 for proof in rows if proof.render_verified)
    complete = bool(requested_count > 0 and len(rows) == requested_count)
    headline = (
        f"C'est pret. {len(rows)}/{requested_count} document(s) ont ete generes."
        if complete
        else (
            "Livraison documentaire incomplete. "
            f"{len(rows)}/{requested_count} document(s) ont ete generes."
        )
    )
    lines = [headline, f"Verification de rendu: {verified}/{len(rows)}."]
    logo_count = sum(1 for proof in rows if proof.logo_id)
    if logo_count:
        lines.append(f"Logo actif applique: {logo_count}/{len(rows)}.")
    if receipt_id:
        lines.append(
            f"Lot de livraison enregistre: `{receipt_id}`. "
            "Tu peux dire « ouvre-les » pour rouvrir exactement ces fichiers."
        )
    lines.append("")
    for index, proof in enumerate(rows, start=1):
        state = "rendu verifie" if proof.render_verified else "rendu non certifie"
        pages = f" - {proof.page_count} page(s)" if proof.page_count else ""
        lines.append(f"{index}. `{proof.filename}` - {state}{pages} - `{proof.path}`")
    if verified != len(rows):
        lines.extend([
            "",
            "Je confirme la generation des fichiers, mais pas la qualite visuelle des rendus non certifies.",
        ])
    return "\n".join(lines)


def build_document_workflow_final(
    proofs: Iterable[DocumentDeliveryProof],
    *,
    requested_count: int,
    receipt_id: str,
    opened: int,
    failed: int,
    target_ordinal: int,
    target: DocumentDeliveryProof,
    revised: DocumentDeliveryProof,
    changed_fields: dict[str, Any],
    verification_path: str,
    history_parent_id: str = "",
    exported_document_id: str = "",
    exported_path: str = "",
    library_document_ids: Iterable[str] = (),
) -> str:
    """Build the final receipt for an ordered generate/open/revise/verify run."""
    rows = list(proofs)
    verified = sum(1 for proof in rows if proof.render_verified)
    lines = [
        f"C'est termine. {len(rows)}/{requested_count} document(s) ont ete generes.",
        f"Verification de rendu: {verified}/{len(rows)}.",
        f"Recu exact: `{receipt_id}`.",
        f"Ouverture: {opened}/{requested_count}, {failed} echec(s).",
        (
            f"Document {target_ordinal} revise: `{target.filename}` "
            f"(`{target.document_id}`) -> `{revised.filename}` "
            f"(`{revised.document_id}`)."
        ),
    ]
    if changed_fields:
        rendered_fields = ", ".join(
            f"`{name}` = `{value}`" for name, value in changed_fields.items()
        )
        lines.append("Modification appliquee: " + rendered_fields + ".")
    lines.append(
        f"Verification: relecture confirmée de `{verification_path}` avec la nouvelle valeur."
    )
    if history_parent_id:
        lines.append(
            f"Historique: parent `{history_parent_id}` -> enfant `{revised.document_id}` confirme."
        )
    if exported_document_id:
        lines.append(
            f"Export: `{exported_document_id}`"
            + (f" - `{exported_path}`" if exported_path else "")
            + "."
        )
    library_ids = tuple(str(value) for value in library_document_ids if str(value))
    if library_ids:
        lines.append(
            f"Bibliotheque: {len(library_ids)}/{len(library_ids)} identifiant(s) exact(s) confirmes: "
            + ", ".join(f"`{value}`" for value in library_ids)
            + "."
        )
    lines.extend(["", "Documents generes:"])
    for index, proof in enumerate(rows, start=1):
        state = "rendu verifie" if proof.render_verified else "rendu non certifie"
        lines.append(f"{index}. `{proof.filename}` - {state} - `{proof.path}`")
    return "\n".join(lines)


def build_document_workflow_incomplete_final(
    proofs: Iterable[DocumentDeliveryProof],
    *,
    requested_count: int,
    receipt_id: str,
    opened: int,
    failed: int,
    target_ordinal: int,
    target: DocumentDeliveryProof,
    revised: DocumentDeliveryProof,
    changed_fields: dict[str, Any],
    pending_operation: str,
    verification_confirmed: bool = False,
) -> str:
    """Report proven progress without upgrading stored metadata to visual proof."""
    rows = list(proofs)
    verified = sum(1 for proof in rows if proof.render_verified)
    lines = [
        f"Livraison partiellement terminee: {len(rows)}/{requested_count} document(s) generes.",
        f"Verification de rendu initiale: {verified}/{len(rows)}.",
        f"Recu exact: `{receipt_id}`.",
        f"Ouverture: {opened}/{requested_count}, {failed} echec(s).",
        (
            f"Document {target_ordinal} revise: `{target.filename}` "
            f"(`{target.document_id}`) -> `{revised.filename}` "
            f"(`{revised.document_id}`)."
        ),
    ]
    if changed_fields:
        rendered_fields = ", ".join(
            f"`{name}` = `{value}`" for name, value in changed_fields.items()
        )
        lines.append("Modification enregistree: " + rendered_fields + ".")
    verification_line = (
        "Verification textuelle: CONFIRMEE; le cycle de vie documentaire reste incomplet."
        if verification_confirmed
        else (
            "Verification visuelle/textuelle: NON CONFIRMEE. La valeur modifiee "
            "n'a pas ete retrouvee dans une relecture de la nouvelle version."
        )
    )
    lines.extend([
        verification_line,
        f"Action restante non prouvee: {pending_operation}.",
        "",
        "Documents generes:",
    ])
    for index, proof in enumerate(rows, start=1):
        state = "rendu verifie" if proof.render_verified else "rendu non certifie"
        lines.append(f"{index}. `{proof.filename}` - {state} - `{proof.path}`")
    return "\n".join(lines)


def build_document_grounding_request(
    original_request: str,
    verified_receipt: str,
) -> str:
    """Ask Lumena for a natural final while keeping deterministic facts internal."""
    return (
        f"Requete originale: {str(original_request or '').strip()}\n\n"
        "PREUVES DOCUMENTAIRES DETERMINISTES (source de verite, pas un texte a "
        "recopier mot pour mot):\n"
        f"{str(verified_receipt or '').strip()}\n\n"
        "Redige maintenant TA reponse finale, libre et naturelle, avec ta voix "
        "habituelle. Explique simplement ce que tu as reellement fait et donne les "
        "noms/chemins utiles. N'invente aucun fichier, aucune verification ni action. "
        "Si une action ou un rendu n'est pas prouve, dis-le clairement. Ne commence "
        "pas par une formule automatique comme 'C'est pret', 'C'est termine' ou "
        "'Livraison documentaire'."
    )


def document_free_answer_is_grounded(
    answer: str,
    proofs: Iterable[DocumentDeliveryProof],
    *,
    missing: Iterable[str] = (),
    receipt_id: str = "",
    pending_operation: str = "",
) -> bool:
    """Minimal fail-safe for a free document final.

    Style remains entirely model-owned.  This validator only checks that the
    answer retained the exact identities needed to avoid an invented delivery.
    """
    text = str(answer or "").strip()
    folded = text.casefold()
    if not text:
        return False
    rows = tuple(proofs)
    if any(proof.filename.casefold() not in folded for proof in rows if proof.filename):
        return False
    missing_names = tuple(str(value or "").strip() for value in missing if str(value or "").strip())
    if any(name.casefold() not in folded for name in missing_names):
        return False
    pending_markers = {
        "open": ("ouvrir", "ouverture", "pas ouvert", "non ouvert"),
        "revise": ("modifier", "modification", "réviser", "revision", "révision"),
        "verify": ("vérifier", "verification", "vérification", "non vérifié"),
        "history": ("historique", "parent", "provenance"),
        "export": ("export", "convertir", "conversion"),
        "library_verify": ("bibliothèque", "bibliotheque", "enregistr"),
    }
    if pending_operation:
        markers = pending_markers.get(str(pending_operation), (str(pending_operation),))
        if not any(marker.casefold() in folded for marker in markers):
            return False
    if any(not proof.render_verified for proof in rows):
        caution = (
            "non verifie", "non vérifié", "non certifie", "non certifié",
            "a verifier", "à vérifier", "pas confirme", "pas confirmé",
        )
        if not any(marker in folded for marker in caution):
            return False
    return True


def manifest_progress_signature(
    proofs: Iterable[DocumentDeliveryProof],
) -> tuple[tuple[str, str, str, bool], ...]:
    """Return the stable evidence identity used by the ReAct progress guard."""
    return tuple(
        (
            proof.kind,
            proof.sha256 or proof.path,
            proof.render_status,
            proof.render_verified,
        )
        for proof in proofs
    )


def manifest_has_new_proof(
    previous: Iterable[tuple[str, str, str, bool]],
    current: Iterable[tuple[str, str, str, bool]],
) -> bool:
    """True only when the current manifest contains evidence not seen before."""
    return bool(set(current) - set(previous))


def _document_path_identity(value: Any) -> str:
    """Return a platform-independent identity for a delivered document path."""
    raw = str(value or "").strip().replace("\\", "/")
    if not raw:
        return ""
    return posixpath.normpath(raw).casefold()


def summarize_document_open_events(
    proofs: Iterable[DocumentDeliveryProof],
    events: Iterable[dict[str, Any]],
    *,
    requested_count: int,
) -> dict[str, Any] | None:
    """Summarize causally ordered opens against one exact delivery manifest.

    A single exact bundle remains authoritative. Several smaller receipts may
    also prove the open step, but only when their de-duplicated file union is
    exactly the current manifest. Reopening the same receipt adds no progress.
    """
    rows = list(proofs)
    if requested_count < 1 or len(rows) != requested_count:
        return None

    expected_order = [_document_path_identity(proof.path) for proof in rows]
    if any(not identity for identity in expected_order):
        return None
    expected = set(expected_order)
    if len(expected) != requested_count:
        return None

    opened_files: dict[str, dict[str, Any]] = {}
    receipt_ids: list[str] = []
    last_event_index = 0

    for raw_event in events:
        if not isinstance(raw_event, dict):
            continue
        try:
            requested = int(raw_event.get("requested") or 0)
            opened = int(raw_event.get("opened") or 0)
            failed = int(raw_event.get("failed") or 0)
        except (TypeError, ValueError):
            continue
        if failed != 0 or opened < 1 or requested < opened:
            continue

        files = raw_event.get("files")
        if not isinstance(files, list):
            files = []

        # Historical unit tests and old in-memory runs retained only counters.
        # Keep compatibility for one exact event; partial receipts require paths
        # before they can be safely combined.
        if not files:
            if requested == requested_count and opened == requested_count:
                event = dict(raw_event)
                event.update({
                    "requested": requested_count,
                    "opened": requested_count,
                    "failed": 0,
                    "complete": True,
                    "receipt_ids": tuple(filter(None, (
                        str(raw_event.get("receipt_id") or "").strip(),
                        str(raw_event.get("_receipt_id") or "").strip(),
                    )))[:1],
                })
                return event
            continue

        event_files: list[tuple[str, dict[str, Any]]] = []
        for item in files:
            if not isinstance(item, dict):
                event_files = []
                break
            identity = _document_path_identity(item.get("path"))
            if not identity or identity not in expected:
                event_files = []
                break
            event_files.append((identity, dict(item)))
        if not event_files or len(event_files) != opened:
            continue

        for identity, item in event_files:
            opened_files.setdefault(identity, item)
        receipt_id = str(
            raw_event.get("receipt_id") or raw_event.get("_receipt_id") or ""
        ).strip()
        if receipt_id and receipt_id not in receipt_ids:
            receipt_ids.append(receipt_id)
        last_event_index = max(
            last_event_index, int(raw_event.get("_event_index", 0) or 0),
        )

        if set(opened_files) == expected:
            return {
                "requested": requested_count,
                "opened": requested_count,
                "failed": 0,
                "complete": True,
                "receipt_id": receipt_ids[0] if len(receipt_ids) == 1 else "",
                "receipt_ids": tuple(receipt_ids),
                "files": [opened_files[identity] for identity in expected_order],
                "_event_index": last_event_index,
            }

    return {
        "requested": requested_count,
        "opened": len(opened_files),
        "failed": 0,
        "complete": False,
        "receipt_id": receipt_ids[0] if len(receipt_ids) == 1 else "",
        "receipt_ids": tuple(receipt_ids),
        "files": [opened_files[identity] for identity in expected_order if identity in opened_files],
        "_event_index": last_event_index,
    }


def workflow_progress_signature(
    open_summary: dict[str, Any] | None,
    *,
    revised_document_id: str = "",
    verification_path: str = "",
    history_document_id: str = "",
    export_document_id: str = "",
    library_document_ids: Iterable[str] = (),
) -> tuple[int, str, str, str, str, tuple[str, ...]]:
    """Return monotone document-workflow evidence for the plan guard."""
    return (
        int((open_summary or {}).get("opened") or 0),
        str(revised_document_id or "").strip(),
        _document_path_identity(verification_path),
        str(history_document_id or "").strip(),
        str(export_document_id or "").strip(),
        tuple(sorted(str(value).strip() for value in library_document_ids if str(value).strip())),
    )


def workflow_has_new_proof(
    previous: Iterable[Any],
    current: Iterable[Any],
) -> bool:
    """True when open, revision, or verification evidence strictly advances."""
    defaults = (0, "", "", "", "", ())
    before = (tuple(previous or defaults) + defaults)[:6]
    after = (tuple(current or defaults) + defaults)[:6]
    return bool(
        int(after[0] or 0) > int(before[0] or 0)
        or (str(after[1] or "") and str(after[1] or "") != str(before[1] or ""))
        or (str(after[2] or "") and str(after[2] or "") != str(before[2] or ""))
        or (str(after[3] or "") and str(after[3] or "") != str(before[3] or ""))
        or (str(after[4] or "") and str(after[4] or "") != str(before[4] or ""))
        or (tuple(after[5] or ()) and tuple(after[5] or ()) != tuple(before[5] or ()))
    )


__all__ = [
    "DocumentDeliveryProof",
    "build_document_workflow_final",
    "build_document_workflow_incomplete_final",
    "build_multi_document_final",
    "compact_batch_observation",
    "compact_generation_payload",
    "manifest_has_new_proof",
    "manifest_progress_signature",
    "parse_generation_proof",
    "summarize_document_open_events",
    "workflow_has_new_proof",
    "workflow_progress_signature",
]
