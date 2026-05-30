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


@dataclass
class VoicePersona:
    tone: str = "calme, proche, lucide, efficace"
    pace: str = "naturel"
    emotion: str = "subtile"
    style_prompt: str = "Parle comme Lumena : claire, directe, chaleureuse sans exagérer."


@dataclass
class VoiceLocalEngines:
    xtts_reference: str = "models/xtts/lumena_voice.wav"
    piper_model: str = "fr_FR-siwis-low"


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

    def to_dict(self) -> Dict:
        return {
            "id": self.id, "label": self.label, "language": self.language,
            "persona": vars(self.persona),
            "local": vars(self.local),
            "cloud_mapping": vars(self.cloud_mapping),
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
