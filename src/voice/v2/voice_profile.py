"""VoiceProfile — l'identité vocale unique de Lumena (V2.2/V2 §2).

Règle produit : « Lumena n'a pas plusieurs voix. Lumena a une voix, et plusieurs
moteurs capables de l'incarner. » Ce profil EST la voix ; les providers ne sont
que des moteurs paramétrés par lui.

Pur Python, aucun I/O audio. Chargement tolérant : si le fichier de profil est
absent/illisible, on retombe sur `LUMENA_DEFAULT` (comportement par défaut stable).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
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


# Profil par défaut — la voix de référence de Lumena.
LUMENA_DEFAULT = VoiceProfile()


def load_profile(path: Union[str, Path, None] = None) -> VoiceProfile:
    """Charge un profil ; retombe sur LUMENA_DEFAULT si absent/illisible (no-op safe)."""
    if not path:
        return LUMENA_DEFAULT
    p = Path(path)
    if not p.exists():
        return LUMENA_DEFAULT
    try:
        return VoiceProfile.from_dict(json.loads(p.read_text(encoding="utf-8")))
    except Exception:
        return LUMENA_DEFAULT


def save_profile(profile: VoiceProfile, path: Union[str, Path]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(profile.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


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
