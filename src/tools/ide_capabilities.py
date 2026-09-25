"""IDE capability snapshots from validated installation and authenticated state.

No launch, model registration or execution happens while discovering. Admission
here proves only availability/schema/effect, not host authorization.
"""
from __future__ import annotations

from dataclasses import dataclass
import json

from ..reasoning.external_tool_registry import (
    ExternalProviderSnapshot, ExternalToolError, ExternalToolSpec, PreparedExternalCall,
)
from ..reasoning.tool_semantics import (
    Availability, Confirmation, Idempotency, MissionPolicy, ModelExposure, ProviderKind,
    Risk, ToolEffect, ToolSemantics, validate_semantic_announcement,
)
from .ide_bridge import IDEBridge
from .ide_discovery import IDEDiscoveryService, IDEInstallation
from .ide_protocol import NegotiatedSession
from .ide_semantics import local_ide_semantics, resolve_ide_call


_CONDITIONAL_MODEL_COMMANDS = frozenset({"editor_find_replace", "git_sync", "terminal_run"})


@dataclass(frozen=True, slots=True)
class _IDEBinding:
    installation: IDEInstallation
    session: NegotiatedSession | None
    # LOT L5-3c-3 : le dossier sur lequel ce catalogue est ancre. `None` = la
    # PROPRIETAIRE (chat, utilisateur) ; un chemin = l'instance de cette mission.
    # `is_current` doit revalider contre LA MEME instance, pas contre n'importe
    # laquelle : sans ce champ, la proprietaire validerait un snapshot de mission.
    workspace: str | None = None


def _launch_spec(installation: IDEInstallation) -> ExternalToolSpec:
    # Keep the existing lifecycle schema/description, without running a handler.
    from ..reasoning.handlers.ide import get_ide_handler_defs

    legacy = next(item for item in get_ide_handler_defs() if item.name == "ide_launch")
    descriptor = ToolSemantics(
        tool_name="ide_launch", provider_kind=ProviderKind.NATIVE,
        provider_instance_id=installation.manifest_sha256, catalog_revision=installation.artifact_sha256,
        availability=Availability.READY, effect=ToolEffect.PROCESS_LAUNCH,
        model_exposure=ModelExposure.CONTEXTUAL, risk_floor=Risk.SYSTEM,
        confirmation=Confirmation.POLICY, mission_policy=MissionPolicy.FORBIDDEN,
        idempotency=Idempotency.IDEMPOTENT, sensitive_fields=frozenset({"workspace"}),
        proof_capabilities=frozenset(),
    )
    schema = {"type": "object", **legacy.parameters, "additionalProperties": False}
    return ExternalToolSpec(descriptor, legacy.description, json.dumps(schema, sort_keys=True))


class IDECapabilityService:
    provider_id = "lumena.ide"

    def __init__(self, discovery: IDEDiscoveryService, bridge: IDEBridge) -> None:
        self.discovery = discovery
        self.bridge = bridge

    def _ancrage(self, workspace: str | None) -> NegotiatedSession | None:
        """L'instance decrite par ce catalogue.

        LOT L5-3c-3 : sans `workspace`, la PROPRIETAIRE - comportement historique du
        chat et de l'utilisateur. Avec, l'instance ouverte sur ce dossier, et RIEN
        d'autre : un dossier sans IDE rend `None`, donc `launch_only`. Une mission
        ne se rabat JAMAIS sur la fenetre de l'utilisateur (regle du lot L5-2).
        """
        if workspace is None:
            return self.bridge.catalogue_snapshot()
        return self.bridge.snapshot_pour_workspace(workspace)

    def capture(self, *, workspace: str | None = None) -> ExternalProviderSnapshot:
        """`workspace` est NOMME et optionnel : les 19 appels existants tiennent."""
        installation = self.discovery.discover().installation
        if installation is None:
            return ExternalProviderSnapshot(self.provider_id, "unavailable", ())
        session = self._ancrage(workspace)
        binding = _IDEBinding(installation, session, workspace)
        if session is None:
            return ExternalProviderSnapshot(self.provider_id, "launch_only", (_launch_spec(installation),), binding)
        tools = []
        for command in session.commands:
            if not command["supported"]:
                continue
            local = local_ide_semantics(command["id"], instance_id=session.instance_id,
                                        revision=session.catalogue_hash, availability=Availability.READY)
            descriptor = validate_semantic_announcement(local, command["semantics"])
            resolver = resolve_ide_call if command["id"] in _CONDITIONAL_MODEL_COMMANDS else None
            spec = ExternalToolSpec(descriptor, command["description"],
                                    json.dumps(command["input_schema"], sort_keys=True), resolver)
            if spec.model_candidate:
                tools.append(spec)
        # The same generation must still be current after catalogue validation.
        if self._ancrage(workspace) is not session:
            return ExternalProviderSnapshot(self.provider_id, "degraded", ())
        return ExternalProviderSnapshot(self.provider_id, "ready", tuple(tools), binding)

    def is_current(self, snapshot: ExternalProviderSnapshot) -> bool:
        if type(snapshot) is not ExternalProviderSnapshot or snapshot.provider_id != self.provider_id:
            return False
        binding = snapshot.binding
        if type(binding) is not _IDEBinding:
            return False
        if self.discovery.discover().installation != binding.installation:
            return False
        # LOT L5-3c-3 : la revalidation interroge L'INSTANCE ANCREE. Interroger la
        # proprietaire validerait un snapshot de mission avec la fenetre de
        # l'utilisateur - exactement le melange que ce lot ferme.
        current = self._ancrage(binding.workspace)
        return current is binding.session if binding.session is not None else current is None

    def prepare(self, snapshot: ExternalProviderSnapshot, name: str, parameters: dict) -> PreparedExternalCall:
        if not self.is_current(snapshot):
            raise ExternalToolError("ide_snapshot_stale")
        spec = next((tool for tool in snapshot.tools if tool.name == name), None)
        if spec is None:
            raise ExternalToolError("external_tool_not_in_snapshot")
        return spec.prepare(parameters)

    def expected_session(self, snapshot: ExternalProviderSnapshot) -> NegotiatedSession:
        """Host dispatch must supply this to bridge.send_command AFTER guards."""
        if not self.is_current(snapshot) or snapshot.binding.session is None:
            raise ExternalToolError("ide_snapshot_stale")
        return snapshot.binding.session
