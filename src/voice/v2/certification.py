"""Honest Voice V3 human and hardware certification records."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import uuid
from typing import Any, Dict, Iterable, Mapping


SCENARIOS = {
    "H1": "20 tours naturels sans outil",
    "H2": "20 commandes d'outils",
    "H3": "20 interruptions pendant parole",
    "H4": "hésitations, reprises et autocorrections",
    "H5": "TV active et musique",
    "H6": "deuxième personne dans la pièce",
    "H7": "volume haut avec double-talk",
    "H8": "débrancher/rebrancher micro et sortie",
    "H9": "tuer worker STT/TTS puis reprendre",
    "H10": "mission longue orientée plusieurs fois",
    "H11": "FR→EN→ES→FR, ponctuel puis durable",
    "H12": "traduction et code-switch sans mutation accidentelle",
    "H13": "20 minutes d'écoute et score de fatigue",
    "H14": "24 h de fonctionnement continu",
    "H15": "installation EXE propre sans modèles présents",
}
REQUIRED_PROFILES = frozenset({"development", "clean_exe", "cpu", "nvidia"})
REQUIRED_DEVICES = frozenset({
    "integrated_mic", "usb_mic", "bluetooth_headset",
    "integrated_speaker", "external_speaker",
})


class VoiceCertificationError(RuntimeError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def new_campaign(*, software_revision: str = "", notes: str = "") -> Dict[str, Any]:
    return {
        "schema_version": 1,
        "campaign_id": str(uuid.uuid4()),
        "created_at": _utc_now(),
        "status": "pending",
        "software_revision": str(software_revision),
        "notes": str(notes),
        "machines": [],
        "scenarios": {
            key: {"title": title, "status": "pending", "evidence": []}
            for key, title in SCENARIOS.items()
        },
        "critical_anomalies": [],
    }


def add_machine(
    report: Dict[str, Any], *, machine_id: str, profiles: Iterable[str],
    devices: Iterable[str], os_version: str, hardware: str,
    installer_sha256: str = "",
) -> None:
    identifier = str(machine_id).strip()
    if not identifier or any(item.get("machine_id") == identifier for item in report["machines"]):
        raise VoiceCertificationError("identifiant machine vide ou dupliqué")
    item = {
        "machine_id": identifier,
        "profiles": sorted({str(value).strip() for value in profiles if str(value).strip()}),
        "devices": sorted({str(value).strip() for value in devices if str(value).strip()}),
        "os_version": str(os_version).strip(),
        "hardware": str(hardware).strip(),
        "installer_sha256": str(installer_sha256).strip().lower(),
    }
    if "clean_exe" in item["profiles"] and not item["installer_sha256"]:
        raise VoiceCertificationError("SHA-256 installateur requis pour clean_exe")
    report["machines"].append(item)
    report["status"] = "pending"
    report.pop("final_attestation", None)


def record_scenario(
    report: Dict[str, Any], *, scenario_id: str, machine_id: str,
    result: str, tester: str, notes: str = "", iterations: int = 1,
    duration_s: float = 0.0, naturalness: float | None = None,
    intelligibility: float | None = None, minimum_phrase_score: float | None = None,
    fatigue: float | None = None, language_drift: bool = False,
    terminal_required: bool = False, critical_anomaly: bool = False,
) -> Dict[str, Any]:
    scenario = report.get("scenarios", {}).get(str(scenario_id).upper())
    if not isinstance(scenario, dict):
        raise VoiceCertificationError("scénario H1-H15 invalide")
    if not any(item.get("machine_id") == machine_id for item in report.get("machines", [])):
        raise VoiceCertificationError("machine absente de la campagne")
    outcome = str(result).lower()
    if outcome not in {"pass", "fail"}:
        raise VoiceCertificationError("résultat attendu: pass ou fail")
    if not str(tester).strip():
        raise VoiceCertificationError("testeur humain requis")
    evidence: Dict[str, Any] = {
        "machine_id": machine_id, "result": outcome, "tester": str(tester).strip(),
        "recorded_at": _utc_now(), "notes": str(notes),
        "iterations": max(0, int(iterations)), "duration_s": max(0.0, float(duration_s)),
        "language_drift": bool(language_drift), "terminal_required": bool(terminal_required),
        "critical_anomaly": bool(critical_anomaly),
    }
    for name, value in (
        ("naturalness", naturalness), ("intelligibility", intelligibility),
        ("minimum_phrase_score", minimum_phrase_score), ("fatigue", fatigue),
    ):
        if value is not None:
            score = float(value)
            if score < 1 or score > 5:
                raise VoiceCertificationError(f"score {name} hors intervalle 1..5")
            evidence[name] = score
    evidence["attestation_sha256"] = _digest(evidence)
    scenario["evidence"].append(evidence)
    scenario["status"] = "fail" if any(
        item["result"] == "fail" for item in scenario["evidence"]
    ) else "pass"
    report["status"] = "pending"
    report.pop("final_attestation", None)
    return evidence


def validate_campaign(report: Mapping[str, Any]) -> list[str]:
    failures: list[str] = []
    machines = report.get("machines", [])
    if len(machines) < 2:
        failures.append("au moins deux machines distinctes sont requises")
    if any({"cpu", "nvidia"}.issubset(set(item.get("profiles", []))) for item in machines):
        failures.append("les profils cpu-only et nvidia doivent être sur des machines distinctes")
    profiles = {p for item in machines for p in item.get("profiles", [])}
    devices = {d for item in machines for d in item.get("devices", [])}
    for missing in sorted(REQUIRED_PROFILES - profiles):
        failures.append(f"profil matériel manquant: {missing}")
    for missing in sorted(REQUIRED_DEVICES - devices):
        failures.append(f"périphérique manquant: {missing}")
    scenarios = report.get("scenarios", {})
    all_evidence: list[Mapping[str, Any]] = []
    for key in SCENARIOS:
        item = scenarios.get(key, {})
        evidence = item.get("evidence", []) if isinstance(item, dict) else []
        all_evidence.extend(evidence)
        for proof in evidence:
            unsigned = dict(proof)
            attestation = unsigned.pop("attestation_sha256", "")
            if attestation != _digest(unsigned):
                failures.append(f"{key} contient une attestation altérée")
        if item.get("status") != "pass" or not evidence:
            failures.append(f"{key} non signé PASS")
    for key in ("H1", "H2", "H3"):
        count = sum(int(item.get("iterations", 0)) for item in scenarios.get(key, {}).get("evidence", []))
        if count < 20:
            failures.append(f"{key} exige au moins 20 itérations")
    h13 = scenarios.get("H13", {}).get("evidence", [])
    if sum(float(item.get("duration_s", 0)) for item in h13) < 1200:
        failures.append("H13 exige au moins 20 minutes")
    h14 = scenarios.get("H14", {}).get("evidence", [])
    if sum(float(item.get("duration_s", 0)) for item in h14) < 86400:
        failures.append("H14 exige 24 heures réelles")
    h15_machines = {item.get("machine_id") for item in scenarios.get("H15", {}).get("evidence", [])}
    if not any("clean_exe" in item.get("profiles", []) and item.get("machine_id") in h15_machines for item in machines):
        failures.append("H15 doit être signé sur une machine clean_exe")
    natural = [float(item["naturalness"]) for item in all_evidence if "naturalness" in item]
    intelligible = [float(item["intelligibility"]) for item in all_evidence if "intelligibility" in item]
    minimums = [float(item["minimum_phrase_score"]) for item in all_evidence if "minimum_phrase_score" in item]
    fatigue = [float(item["fatigue"]) for item in h13 if "fatigue" in item]
    if not natural or sum(natural) / len(natural) < 4:
        failures.append("naturel moyen inférieur à 4/5 ou non mesuré")
    if not intelligible or sum(intelligible) / len(intelligible) < 4:
        failures.append("intelligibilité moyenne inférieure à 4/5 ou non mesurée")
    if not minimums or min(minimums) <= 2:
        failures.append("score minimal de phrase inférieur ou égal à 2/5 ou non mesuré")
    if not fatigue or sum(fatigue) / len(fatigue) > 2:
        failures.append("fatigue H13 supérieure à 2/5 ou non mesurée")
    if any(item.get("language_drift") for item in all_evidence):
        failures.append("dérive de langue détectée")
    if any(item.get("terminal_required") for item in all_evidence):
        failures.append("un scénario a nécessité un terminal")
    if any(item.get("critical_anomaly") for item in all_evidence) or report.get("critical_anomalies"):
        failures.append("anomalie critique présente")
    return failures


def verify_final_attestation(report: Mapping[str, Any]) -> bool:
    attestation = report.get("final_attestation")
    if not isinstance(attestation, dict) or report.get("status") != "certified":
        return False
    unsigned = dict(report)
    unsigned.pop("final_attestation", None)
    return attestation.get("report_sha256") == _digest(unsigned)


def finalize_campaign(report: Dict[str, Any], *, signer: str) -> Dict[str, Any]:
    failures = validate_campaign(report)
    if failures:
        report["status"] = "blocked"
        report["gate_failures"] = failures
        raise VoiceCertificationError("certification refusée: " + "; ".join(failures))
    if not str(signer).strip():
        raise VoiceCertificationError("signataire final requis")
    report["status"] = "certified"
    report.pop("gate_failures", None)
    unsigned = dict(report)
    unsigned.pop("final_attestation", None)
    report["final_attestation"] = {
        "signer": str(signer).strip(), "signed_at": _utc_now(),
        "report_sha256": _digest(unsigned),
        "kind": "human-attestation-checksum",
    }
    return report["final_attestation"]


def load_report(path: str | Path) -> Dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise VoiceCertificationError("rapport de certification invalide")
    return value


def save_report(path: str | Path, report: Mapping[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(target)

