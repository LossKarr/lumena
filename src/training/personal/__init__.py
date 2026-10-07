"""Canonical personal-model training domain for Lumena.

The package is intentionally independent from the historical training scripts.
Public contracts are introduced lot by lot and legacy callers are migrated
through explicit adapters.
"""

from .legacy_gate import legacy_auto_retrain_enabled

__all__ = ["legacy_auto_retrain_enabled"]
