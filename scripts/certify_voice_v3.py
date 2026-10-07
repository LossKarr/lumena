"""Create and sign an honest Voice V3 H1-H15 certification report."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.voice.v2.certification import (  # noqa: E402
    VoiceCertificationError, add_machine, finalize_campaign, load_report,
    new_campaign, record_scenario, save_report, validate_campaign,
    verify_final_attestation,
)


def _csv(value: str) -> list[str]:
    return [item.strip() for item in str(value).split(",") if item.strip()]


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Certification humaine Voice V3")
    sub = root.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("--report", required=True)
    init.add_argument("--revision", default="")
    init.add_argument("--notes", default="")

    machine = sub.add_parser("add-machine")
    machine.add_argument("--report", required=True)
    machine.add_argument("--id", required=True)
    machine.add_argument("--profiles", required=True, help="CSV: development,clean_exe,cpu,nvidia")
    machine.add_argument("--devices", required=True, help="CSV des micros/sorties testés")
    machine.add_argument("--os", required=True)
    machine.add_argument("--hardware", required=True)
    machine.add_argument("--installer-sha256", default="")

    record = sub.add_parser("record")
    record.add_argument("--report", required=True)
    record.add_argument("--scenario", required=True)
    record.add_argument("--machine", required=True)
    record.add_argument("--result", choices=("pass", "fail"), required=True)
    record.add_argument("--tester", required=True)
    record.add_argument("--notes", default="")
    record.add_argument("--iterations", type=int, default=1)
    record.add_argument("--duration-s", type=float, default=0)
    record.add_argument("--naturalness", type=float)
    record.add_argument("--intelligibility", type=float)
    record.add_argument("--minimum-phrase-score", type=float)
    record.add_argument("--fatigue", type=float)
    record.add_argument("--language-drift", action="store_true")
    record.add_argument("--terminal-required", action="store_true")
    record.add_argument("--critical-anomaly", action="store_true")

    check = sub.add_parser("check")
    check.add_argument("--report", required=True)
    final = sub.add_parser("finalize")
    final.add_argument("--report", required=True)
    final.add_argument("--signer", required=True)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    path = Path(args.report)
    try:
        if args.command == "init":
            save_report(path, new_campaign(software_revision=args.revision, notes=args.notes))
            print(f"Rapport créé: {path}")
            return 0
        report = load_report(path)
        if args.command == "add-machine":
            add_machine(
                report, machine_id=args.id, profiles=_csv(args.profiles),
                devices=_csv(args.devices), os_version=args.os, hardware=args.hardware,
                installer_sha256=args.installer_sha256,
            )
            save_report(path, report)
            print(f"Machine ajoutée: {args.id}")
            return 0
        if args.command == "record":
            evidence = record_scenario(
                report, scenario_id=args.scenario, machine_id=args.machine,
                result=args.result, tester=args.tester, notes=args.notes,
                iterations=args.iterations, duration_s=args.duration_s,
                naturalness=args.naturalness, intelligibility=args.intelligibility,
                minimum_phrase_score=args.minimum_phrase_score, fatigue=args.fatigue,
                language_drift=args.language_drift,
                terminal_required=args.terminal_required,
                critical_anomaly=args.critical_anomaly,
            )
            save_report(path, report)
            print(f"{args.scenario.upper()} enregistré: {evidence['attestation_sha256']}")
            return 0
        if args.command == "check":
            failures = validate_campaign(report)
            if failures:
                print("Certification incomplète:")
                for failure in failures:
                    print(f"- {failure}")
                return 2
            if report.get("status") == "certified" and not verify_final_attestation(report):
                print("Certification altérée: checksum final invalide", file=sys.stderr)
                return 2
            print("Toutes les gates sont satisfaites; finalisation possible.")
            return 0
        attestation = finalize_campaign(report, signer=args.signer)
        save_report(path, report)
        print(f"Certification signée: {attestation['report_sha256']}")
        return 0
    except (OSError, ValueError, VoiceCertificationError) as exc:
        print(f"Erreur: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

