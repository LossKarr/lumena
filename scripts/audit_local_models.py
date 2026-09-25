"""Read-only inventory and explicit canary for the configured Ollama daemon."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.local_models.identifiers import IdentifierError, parse_model_reference  # noqa: E402
from src.local_models.ollama_client import OllamaClient, OllamaClientError  # noqa: E402
from src.local_models.verification import verify_local_model  # noqa: E402


async def _run(args: argparse.Namespace) -> int:
    client = OllamaClient()
    if args.canary:
        reference = parse_model_reference(args.canary, "ollama")
        result = await verify_local_model(client, reference.pull_reference, max_output_tokens=args.max_output_tokens)
        print(json.dumps({"reference": reference.as_dict(), "verification": result.as_dict()}, ensure_ascii=False))
        return 0 if result.status in {"verified", "partially_verified"} else 2

    installed = await client.list_installed()
    print(json.dumps({"models": [model.as_dict() for model in installed]}, ensure_ascii=False))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--list-only", action="store_true", help="List installed models without loading them.")
    mode.add_argument("--canary", metavar="MODEL", help="Verify one already installed Ollama model.")
    parser.add_argument("--max-output-tokens", type=int, choices=range(1, 33), default=8)
    args = parser.parse_args()
    try:
        return asyncio.run(_run(args))
    except (IdentifierError, OllamaClientError) as exc:
        print(json.dumps({"error_code": getattr(exc, "code", str(exc))[:120]}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
