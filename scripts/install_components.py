"""Install signed large components before Lumena's first launch."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from src.runtime.component_service import install_release_components
from src.voice.v2.component_install import (
    activate_installed_voice_pack, run_qwen_install_canary,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app-dir", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--trusted-keys", type=Path, required=True)
    parser.add_argument("--release-version", required=True)
    args = parser.parse_args()
    app_dir = args.app_dir.resolve()
    os.environ["LUMENA_COMPONENT_ROOT"] = str(app_dir)

    def progress(message: str) -> None:
        print(json.dumps({"event": message}, ensure_ascii=False), flush=True)

    result = install_release_components(
        root=app_dir, manifest_path=args.manifest,
        trusted_keys_path=args.trusted_keys,
        expected_release=args.release_version, progress=progress,
    )
    result["voice_pack"] = activate_installed_voice_pack(root=app_dir)
    result["qwen_canary"] = run_qwen_install_canary()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
