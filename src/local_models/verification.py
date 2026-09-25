"""Post-install verification that separates observed from claimed capabilities."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any

from .ollama_client import OllamaClient, OllamaClientError


@dataclass(frozen=True, slots=True)
class VerificationResult:
    status: str
    text_verified: bool
    claimed_capabilities: tuple[str, ...]
    observed_capabilities: tuple[str, ...]
    family: str
    context_window: int | None
    proof_digest: str
    error_code: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "text_verified": self.text_verified,
            "claimed_capabilities": list(self.claimed_capabilities),
            "observed_capabilities": list(self.observed_capabilities),
            "family": self.family,
            "context_window": self.context_window,
            "proof_digest": self.proof_digest,
            "error_code": self.error_code,
        }


def _show_metadata(show: dict[str, Any]) -> tuple[tuple[str, ...], str, int | None]:
    claimed = tuple(sorted({str(item).lower() for item in show.get("capabilities", []) if type(item) is str}))
    model_info = show.get("model_info") if type(show.get("model_info")) is dict else {}
    family = str(model_info.get("general.architecture") or "")[:128]
    contexts = [
        int(value)
        for key, value in model_info.items()
        if type(key) is str and key.endswith(".context_length") and type(value) in {int, float} and value > 0
    ]
    return claimed, family, max(contexts) if contexts else None


async def verify_local_model(client: OllamaClient, reference: str, *, max_output_tokens: int = 8) -> VerificationResult:
    try:
        show = await client.show(reference)
        claimed, family, context = _show_metadata(show)
        if "embedding" in claimed and "completion" not in claimed:
            payload = f"{reference}\0{family}\0{context}\0embedding-metadata"
            return VerificationResult(
                "partially_verified",
                False,
                claimed,
                ("metadata",),
                family,
                context,
                hashlib.sha256(payload.encode()).hexdigest(),
            )
        if max_output_tokens == 8:
            response = await client.generate_canary(reference)
        else:
            response = await client.generate_canary(reference, max_tokens=max_output_tokens)
        payload = f"{reference}\0{family}\0{context}\0{response}"
        return VerificationResult(
            "verified", True, claimed, ("text",), family, context, hashlib.sha256(payload.encode()).hexdigest()
        )
    except OllamaClientError as exc:
        return VerificationResult("incompatible", False, (), (), "", None, "", error_code=exc.code)
