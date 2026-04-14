from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


CRITICAL_TESTS = [
    "tests/test_runtime_context.py",
    "tests/test_workspace_policy.py",
    "tests/test_task_orchestrator.py",
    "tests/test_channel_envelope.py",
    "tests/test_api_chat_metadata.py",
    "tests/test_api_tasks_endpoints.py",
    "tests/test_omnichannel_resume.py",
    "tests/test_trace_stream_api.py",
    "tests/test_slo_monitor.py",
    "tests/test_status_slo.py",
    "tests/test_cursor_ide_local_integration.py",
    "tests/test_tool_system.py",
    "tests/test_react_loop_guards.py",
    "tests/test_react_file_guardrails.py",
    "tests/test_core_runtime_context_bridge.py",
    "tests/test_file_edits_collector.py",
]


def _run_once(project_root: Path, *, full: bool, timeout: int) -> int:
    if full:
        cmd = [sys.executable, "-m", "pytest", "-q", "--tb=short",
               f"--timeout={timeout}"]
    else:
        cmd = [sys.executable, "-m", "pytest", "-q", "--tb=short",
               f"--timeout={timeout}", *CRITICAL_TESTS]
    print(f"\n{'='*60}")
    print("Running:", " ".join(cmd))
    print(f"{'='*60}")
    result = subprocess.run(cmd, cwd=str(project_root))
    return result.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description="Lumena CI phase gate")
    parser.add_argument("--full", action="store_true",
                        help="Run FULL test suite (not just critical tests)")
    parser.add_argument("--runs", type=int, default=1,
                        help="Number of consecutive runs (stability proof)")
    parser.add_argument("--timeout", type=int, default=15,
                        help="Per-test timeout in seconds")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent.parent
    mode = "FULL SUITE" if args.full else "CRITICAL SUBSET"
    print(f"Phase gate: {mode} × {args.runs} run(s), timeout={args.timeout}s")

    for i in range(args.runs):
        print(f"\n>>> Run {i+1}/{args.runs}")
        rc = _run_once(project_root, full=args.full, timeout=args.timeout)
        if rc != 0:
            print(f"\nPhase gate FAILED on run {i+1}. Merge should be blocked.")
            return rc

    print(f"\nPhase gate PASSED ({args.runs} run(s), {mode}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
