"""Sign a Lumena component manifest with an encrypted external Ed25519 key."""
from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from src.runtime.component_manifest import canonical_component_manifest_bytes


class ComponentManifestSigningError(RuntimeError):
    pass


def sign_manifest(
    source: Path, private_key_path: Path, output: Path, *, key_id: str,
    password_env: str,
) -> dict:
    if output.exists():
        raise ComponentManifestSigningError("le manifeste signé existe déjà")
    password = os.getenv(password_env, "")
    if len(password) < 16:
        raise ComponentManifestSigningError("mot de passe de clé absent ou trop court")
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or "signature" in value:
            raise ValueError
        loaded = serialization.load_pem_private_key(
            private_key_path.read_bytes(), password=password.encode("utf-8"),
        )
    except Exception as exc:
        raise ComponentManifestSigningError("entrée ou clé de signature invalide") from exc
    if not isinstance(loaded, Ed25519PrivateKey):
        raise ComponentManifestSigningError("la clé de publication doit être Ed25519")
    value["signature"] = {
        "algorithm": "ed25519", "key_id": key_id,
        "value": base64.b64encode(
            loaded.sign(canonical_component_manifest_bytes(value))
        ).decode("ascii"),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8", newline="\n",
    )
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("private_key", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--key-id", required=True)
    parser.add_argument("--password-env", required=True)
    args = parser.parse_args()
    sign_manifest(
        args.source, args.private_key, args.output, key_id=args.key_id,
        password_env=args.password_env,
    )
    print(json.dumps({"output": str(args.output), "key_id": args.key_id}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
