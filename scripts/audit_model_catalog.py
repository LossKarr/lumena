"""Audit local ou canari réseau borné des catalogues modèles Lumena.

Le mode par défaut est déterministe et sans réseau. Le mode ``--canary``
appelle uniquement le modèle demandé, sans fallback. Aucun mode n'affiche ni
ne sérialise les clés API.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def build_report() -> dict[str, Any]:
    from src.llm.providers import (
        AVAILABLE_MODELS,
        MODEL_ALIASES,
        MODEL_CATALOG_REVISION,
        MODEL_FALLBACKS,
        validate_model_catalog,
    )
    from src.services.image_gen import _MODEL_CATALOG, _PROVIDER_FALLBACK_ORDER
    from web.routes.config import _CONFIG_SCHEMA

    errors = list(validate_model_catalog())
    selectable_text = {name for name, model in AVAILABLE_MODELS.items() if model.is_selectable()}
    selectable_image = {name for name, info in _MODEL_CATALOG.items() if info.selectable}
    auto_image = {name for name, info in _MODEL_CATALOG.items() if info.selectable and info.auto_eligible}

    if len(_PROVIDER_FALLBACK_ORDER) != len(set(_PROVIDER_FALLBACK_ORDER)):
        errors.append("image: duplicate automatic fallback")
    invalid_image_fallbacks = set(_PROVIDER_FALLBACK_ORDER) - auto_image
    for name in sorted(invalid_image_fallbacks):
        errors.append(f"image: fallback {name!r} is not selectable and auto eligible")

    schema = {entry["key"]: entry for entry in _CONFIG_SCHEMA}
    for key, entry in schema.items():
        if entry.get("type") != "select" or key == "LUMENA_BRAIN_IMAGE_GEN":
            continue
        model_options = set(entry.get("options", [])) & set(AVAILABLE_MODELS)
        for name in sorted(model_options - selectable_text):
            errors.append(f"config: {key} exposes non-selectable model {name!r}")
    image_options = set(schema["LUMENA_BRAIN_IMAGE_GEN"]["options"]) - {"auto"}
    for name in sorted(image_options - selectable_image):
        errors.append(f"config: image selector exposes non-selectable model {name!r}")

    lifecycle_counts = Counter(model.lifecycle.value for model in AVAILABLE_MODELS.values())
    image_lifecycle_counts = Counter(info.lifecycle for info in _MODEL_CATALOG.values())
    return {
        "catalog_revision": MODEL_CATALOG_REVISION,
        "network_used": False,
        "credentials_serialized": False,
        "status": "ok" if not errors else "error",
        "errors": errors,
        "text": {
            "total": len(AVAILABLE_MODELS),
            "selectable": len(selectable_text),
            "aliases": len(MODEL_ALIASES),
            "fallback_roots": len(MODEL_FALLBACKS),
            "lifecycle": dict(sorted(lifecycle_counts.items())),
        },
        "image": {
            "total": len(_MODEL_CATALOG),
            "selectable": len(selectable_image),
            "auto_eligible": len(auto_image),
            "fallbacks": len(_PROVIDER_FALLBACK_ORDER),
            "lifecycle": dict(sorted(image_lifecycle_counts.items())),
        },
    }


async def run_text_canary(model_name: str, *, max_cost_usd: float, max_output_tokens: int) -> dict[str, Any]:
    """Call exactly one text model, without the public fallback chain."""
    from src.llm.model_access import _redact_trace_reason
    from src.llm.multi_provider import MultiProviderLLM
    from src.llm.providers import MODEL_CATALOG_REVISION, get_model_config

    config = get_model_config(model_name)
    if config is None or not config.is_selectable():
        raise ValueError(f"model_not_selectable:{model_name}")
    if config.supports_image_generation:
        raise ValueError("image_canary_requires_the_image_service")
    if max_output_tokens < 16 or max_output_tokens > 32:
        raise ValueError("max_output_tokens_must_be_between_16_and_32")
    pricing = config.pricing
    if pricing is None or pricing.output_per_million is None:
        if not config.is_free():
            raise ValueError("pricing_unknown_canary_refused")
        estimated_cap = 0.0
    else:
        input_rate = pricing.input_per_million
        if input_rate is None:
            raise ValueError("input_pricing_unknown_canary_refused")
        estimated_cap = (64 * input_rate + max_output_tokens * pricing.output_per_million) / 1_000_000
    if max_cost_usd < 0 or estimated_cap > max_cost_usd:
        raise ValueError(f"cost_cap_too_low:required_at_least={estimated_cap:.8f}")

    llm = MultiProviderLLM(model_name=model_name)
    started = time.perf_counter()
    try:
        result = await llm._chat_provider_result(
            config.provider,
            [{"role": "user", "content": "Reply with exactly: OK"}],
            temperature=0.0,
            max_tokens=max_output_tokens,
            model=config.model_id,
        )
        resolved = str(result.get("model_used") or config.model_id)
        return {
            "catalog_revision": MODEL_CATALOG_REVISION,
            "status": "passed" if str(result.get("text") or "").strip() else "transport_error",
            "requested_model": model_name,
            "requested_provider": config.provider.value,
            "resolved_model": resolved,
            "resolved_provider": str(result.get("provider_used") or config.provider.value),
            "fallback_enabled": False,
            "max_output_tokens": max_output_tokens,
            "estimated_cost_cap_usd": round(estimated_cap, 8),
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            "prompt_tokens": result.get("prompt_tokens"),
            "completion_tokens": result.get("completion_tokens"),
            "finish_reason": result.get("finish_reason"),
            "content_recorded": False,
            "credentials_serialized": False,
        }
    except Exception as exc:
        from src.llm.model_access import classify_model_failure
        from src.llm.provider_quota import quota_epuise

        failure_kind = classify_model_failure(exc).value
        unavailable = quota_epuise(config.provider) or failure_kind in {"auth", "quota"}
        return {
            "catalog_revision": MODEL_CATALOG_REVISION,
            "status": "unavailable_for_account" if unavailable else "transport_error",
            "requested_model": model_name,
            "requested_provider": config.provider.value,
            "resolved_model": None,
            "resolved_provider": None,
            "fallback_enabled": False,
            "max_output_tokens": max_output_tokens,
            "estimated_cost_cap_usd": round(estimated_cap, 8),
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            "error": _redact_trace_reason(str(exc)),
            "failure_kind": "quota" if quota_epuise(config.provider) else failure_kind,
            "content_recorded": False,
            "credentials_serialized": False,
        }
    finally:
        await llm.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--list-only", action="store_true", help="Audit local gratuit (mode par défaut).")
    modes.add_argument("--canary", metavar="MODEL", help="Canari texte direct, sans fallback.")
    parser.add_argument("--max-cost-usd", type=float, help="Plafond obligatoire pour un canari.")
    parser.add_argument("--max-output-tokens", type=int, default=16, help="16 à 32 tokens (défaut : 16).")
    parser.add_argument("--output", type=Path, help="Écrit aussi le rapport JSON à cet emplacement.")
    args = parser.parse_args()
    if args.canary:
        if args.max_cost_usd is None:
            parser.error("--canary exige --max-cost-usd")
        try:
            report = asyncio.run(run_text_canary(
                args.canary,
                max_cost_usd=args.max_cost_usd,
                max_output_tokens=args.max_output_tokens,
            ))
        except ValueError as exc:
            parser.error(str(exc))
    else:
        report = build_report()
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 0 if report["status"] in {"ok", "passed"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
