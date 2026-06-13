"""Phase I-8 (Fix AC) — shim feuille pour la dérivation de tags.

L'orchestrator autonome (Phase 24) a une règle d'architecture stricte :
aucun import des modules machinerie (capability_resolver, proposal_planner,
install/activation, ...) — vérifiée par test_autonomous_orchestrator.py
(_FORBIDDEN_IMPORTS). La dérivation de tags est une fonction PURE sur
chaînes (tokenisation + stoplist), sans mutation ni I/O : ce shim l'expose
sans que la couche planification n'importe la machinerie du resolver.

L'implémentation canonique vit dans capability_resolver (elle partage le
tokenizer et la stoplist _ACTIONABLE_VERBS_TOOLS du resolver — source de
vérité unique, Fix Z). Import paresseux : résolu au premier appel.
"""
from __future__ import annotations

from typing import Tuple


def derive_capability_tags(text: str) -> Tuple[str, ...]:
    """Tokens discriminants d'un intent (cf. capability_resolver)."""
    from src.mcp.capability_resolver import (  # noqa: WPS433
        derive_capability_tags as _impl,
    )
    return _impl(text)


__all__ = ["derive_capability_tags"]
