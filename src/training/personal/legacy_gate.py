"""Safety policy for the historical autonomous retraining pipeline."""

from __future__ import annotations

import os
from collections.abc import Mapping


_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})


def legacy_auto_retrain_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Return whether the legacy deploy-capable retrainer has explicit opt-in.

    The safe default is disabled.  This gate is kept separate so every entry
    point can share the same policy while the canonical pipeline is built.
    """

    source = os.environ if env is None else env
    return str(source.get("LUMENA_LEGACY_AUTO_RETRAIN_ENABLE", "")).strip().lower() in _TRUE_VALUES
