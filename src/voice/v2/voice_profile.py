"""VoiceProfile — l'identité vocale unique de Lumena (V2.2/V2 §2).

Règle produit : « Lumena n'a pas plusieurs voix. Lumena a une voix, et plusieurs
moteurs capables de l'incarner. » Ce profil EST la voix ; les providers ne sont
que des moteurs paramétrés par lui.

Pur Python, aucun I/O audio. Chargement tolérant : si le fichier de profil est
absent/illisible, on retombe sur `LUMENA_DEFAULT` (comportement par défaut stable).
"""
from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Dict, Optional, Union
import re


@dataclass
class VoicePersona:
    tone: str = "calme, proche, lucide, efficace"
    pace: str = "naturel"
    emotion: str = "subtile"
    style_prompt: str = "Parle comme Lumena : claire, directe, chaleureuse sans exagérer."
    pronunciations: Dict[str, str] = field(default_factory=lambda: {
        "Lumena": "Louména",
        "MCP": "M C P",
        "API": "A P I",
        "JSON": "jé son",
        "pytest": "paï test",
        "ReAct": "ré acte",
    })
    prosody: Dict[str, Dict[str, float]] = field(default_factory=lambda: {
        "greeting": {"rate": 1.00, "energy": 1.02},
        "explanation": {"rate": 0.98, "energy": 1.00},
        "question": {"rate": 1.00, "energy": 1.00},
        "success": {"rate": 1.02, "energy": 1.03},
        "warning": {"rate": 0.94, "energy": 0.98},
        "error": {"rate": 0.92, "energy": 0.96},
    })


@dataclass
class VoiceLocalEngines:
    xtts_reference: str = "models/xtts/lumena_voice.wav"
    piper_model: str = "fr_FR-siwis-medium"
    piper_models: Dict[str, str] = field(default_factory=lambda: {
        "fr": "fr_FR-siwis-medium",
    })

    def piper_model_for(self, language: str) -> Optional[str]:
        code = str(language or "fr").strip().lower().split("-")[0]
        configured = self.piper_models.get(code)
        if configured:
            return configured
        return self.piper_model if code == "fr" else None


@dataclass
class VoiceCloudMapping:
    openai_voice: str = ""     # à choisir après benchmark
    gemini_voice: str = ""     # Kore / Puck après test
    xai_voice: str = ""        # après test
    nvidia_reference: str = ""  # sample consentant dédié


@dataclass
class VoiceProfile:
    id: str = "lumena_default"
    label: str = "Lumena"
    language: str = "fr"
    persona: VoicePersona = field(default_factory=VoicePersona)
    local: VoiceLocalEngines = field(default_factory=VoiceLocalEngines)
    cloud_mapping: VoiceCloudMapping = field(default_factory=VoiceCloudMapping)
    reference_consent_confirmed: bool = False
    reference_rights_note: str = ""

    def to_dict(self) -> Dict:
        return {
            "id": self.id, "label": self.label, "language": self.language,
            "persona": vars(self.persona),
            "local": vars(self.local),
            "cloud_mapping": vars(self.cloud_mapping),
            "reference_consent_confirmed": self.reference_consent_confirmed,
            "reference_rights_note": self.reference_rights_note,
        }

    @classmethod
    def from_dict(cls, d: Dict) -> "VoiceProfile":
        return cls(
            id=d.get("id", "lumena_default"),
            label=d.get("label", "Lumena"),
            language=d.get("language", "fr"),
            persona=VoicePersona(**{**vars(VoicePersona()), **(d.get("persona") or {})}),
            local=VoiceLocalEngines(**{**vars(VoiceLocalEngines()), **(d.get("local") or {})}),
            cloud_mapping=VoiceCloudMapping(**{**vars(VoiceCloudMapping()), **(d.get("cloud_mapping") or {})}),
            reference_consent_confirmed=bool(d.get("reference_consent_confirmed", False)),
            reference_rights_note=str(d.get("reference_rights_note") or ""),
        )

    def xtts_reference_exists(self) -> bool:
        return Path(self.local.xtts_reference).exists()

    def for_language(self, language: str) -> "VoiceProfile":
        """Return an immutable-style per-generation projection."""
        code = str(language or self.language).strip().lower().split("-")[0]
        return self if code == self.language else replace(self, language=code)


# Profil par défaut — la voix de référence de Lumena.
LUMENA_DEFAULT = VoiceProfile()

_last_profile_status: Dict[str, object] = {
    "ok": True,
    "source": "default",
    "path": None,
    "recovered": False,
    "error": None,
}


def _set_profile_status(**updates: object) -> None:
    _last_profile_status.update(updates)


def get_voice_profile_status() -> Dict[str, object]:
    """Return a copy of the last profile load/save diagnostic."""
    return dict(_last_profile_status)


def _read_profile_file(path: Path) -> VoiceProfile:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("le profil vocal doit etre un objet JSON")
    return VoiceProfile.from_dict(payload)


def load_profile(path: Union[str, Path, None] = None) -> VoiceProfile:
    """Load a profile, recovering the last durable backup when possible."""
    if not path:
        _set_profile_status(ok=True, source="default", path=None, recovered=False, error=None)
        return LUMENA_DEFAULT
    p = Path(path)
    if not p.exists():
        _set_profile_status(
            ok=True, source="default_missing", path=str(p), recovered=False, error=None,
        )
        return LUMENA_DEFAULT
    try:
        profile = _read_profile_file(p)
        _set_profile_status(
            ok=True, source="primary", path=str(p), recovered=False, error=None,
        )
        return profile
    except Exception as primary_error:
        backup = p.with_suffix(p.suffix + ".bak")
        if backup.exists():
            try:
                profile = _read_profile_file(backup)
                _set_profile_status(
                    ok=True,
                    source="backup",
                    path=str(p),
                    recovered=True,
                    error=str(primary_error),
                )
                return profile
            except Exception as backup_error:
                error = f"primary: {primary_error}; backup: {backup_error}"
        else:
            error = str(primary_error)
        _set_profile_status(
            ok=False, source="default_corrupt", path=str(p), recovered=False, error=error,
        )
        return LUMENA_DEFAULT


def save_profile(profile: VoiceProfile, path: Union[str, Path]) -> None:
    """Persist a profile atomically and retain the previous valid generation."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    backup = p.with_suffix(p.suffix + ".bak")
    payload = json.dumps(profile.to_dict(), ensure_ascii=False, indent=2) + "\n"
    try:
        with tmp.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        # Verify the temporary generation before replacing the live profile.
        _read_profile_file(tmp)
        if p.exists():
            # Keep only a known-good predecessor. A corrupt live file must not
            # replace an already valid backup.
            try:
                _read_profile_file(p)
            except Exception:
                pass
            else:
                shutil.copy2(p, backup)
        os.replace(tmp, p)
        _set_profile_status(
            ok=True, source="saved", path=str(p), recovered=False, error=None,
        )
    except Exception as exc:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        _set_profile_status(
            ok=False, source="save_error", path=str(p), recovered=False, error=str(exc),
        )
        raise


def classify_dialogue_act(text: str) -> str:
    value = (text or "").strip().lower()
    if not value:
        return "explanation"
    if any(token in value for token in ("erreur", "échoué", "echec", "impossible")):
        return "error"
    if any(token in value for token in ("attention", "prudence", "⚠")):
        return "warning"
    if any(token in value for token in ("c'est fait", "terminé", "termine", "réussi", "reussi")):
        return "success"
    if value.endswith("?"):
        return "question"
    if any(value.startswith(token) for token in ("bonjour", "salut", "coucou")):
        return "greeting"
    return "explanation"


def apply_pronunciations(text: str, profile: VoiceProfile) -> str:
    """Apply the profile dictionary only to the spoken projection."""
    result = text or ""
    for source, spoken in (profile.persona.pronunciations or {}).items():
        if not source or not spoken:
            continue
        result = re.sub(rf"(?<!\w){re.escape(source)}(?!\w)", spoken, result, flags=re.IGNORECASE)
    return result
