"""P-1 — Benchmark baseline CodeAgent.

Mesure les perfs des briques critiques (hors LLM) pour détecter les régressions
de performance. Exécute N itérations de chaque micro-op et écrit un rapport
JSON dans `data/logs/codeagent/benchmark_<ts>.json`.

Utilisation:
    python scripts/bench_codeagent.py                 # run standard (1000 iter)
    python scripts/bench_codeagent.py --iter 5000     # run plus long
    python scripts/bench_codeagent.py --compare       # compare au baseline précédent
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.utils.paths import LOGS_DIR  # noqa: E402


def _bench(fn, iterations: int) -> dict:
    durations = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        fn()
        durations.append((time.perf_counter() - t0) * 1000)  # ms
    return {
        "iterations": iterations,
        "mean_ms": round(statistics.mean(durations), 4),
        "median_ms": round(statistics.median(durations), 4),
        "p95_ms": round(sorted(durations)[int(len(durations) * 0.95)], 4),
        "min_ms": round(min(durations), 4),
        "max_ms": round(max(durations), 4),
    }


def bench_fuzzy_replace():
    from src.tools.fuzzy_replace import fuzzy_replace
    content = "def hello():\n    print('world')\n    return 1\n" * 50
    fuzzy_replace(content, "print('world')", "print('LUMENA')")


def bench_env_context():
    from src.utils.env_context import build_env_context_block
    build_env_context_block(str(ROOT))


def bench_french_errors():
    from src.utils.french_errors import translate_error
    translate_error("FileNotFoundError: No such file or directory: '/tmp/x.txt'")


def bench_model_temperature():
    from src.utils.model_temperatures import get_model_temperature
    get_model_temperature("deepseek-reasoner")
    get_model_temperature("claude-opus-4")
    get_model_temperature("gpt-4.1")


def bench_truncation_save(tmp_dir: Path):
    from src.utils.truncation_save import save_and_truncate
    big = "x" * 10000
    save_and_truncate(big, task_id="bench", iteration=1, threshold=5000,
                      head_chars=1000, tail_chars=500)


def bench_action_parsing():
    # Parse d'un bloc d'action typique (regex heavy-lifting)
    import re
    raw = 'ACTION: edit_file\nARGS: {"path": "src/foo.py", "content": "def x():\\n    pass\\n"}'
    re.search(r"ACTION:\s*(\w+)", raw)
    re.search(r'ARGS:\s*(\{.*\})', raw, re.DOTALL)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--iter", type=int, default=1000, help="Itérations par bench")
    parser.add_argument("--compare", action="store_true", help="Compare au dernier baseline")
    args = parser.parse_args()

    out_dir = LOGS_DIR / "codeagent"
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[P-1] Benchmark CodeAgent ({args.iter} iter/bench)")
    results = {
        "timestamp": datetime.now().isoformat(),
        "iterations": args.iter,
        "benches": {
            "fuzzy_replace": _bench(bench_fuzzy_replace, args.iter),
            "env_context": _bench(bench_env_context, args.iter),
            "french_errors": _bench(bench_french_errors, args.iter),
            "model_temperature": _bench(bench_model_temperature, args.iter),
            "action_parsing": _bench(bench_action_parsing, args.iter),
            "truncation_save": _bench(lambda: bench_truncation_save(out_dir), max(args.iter // 10, 50)),
        },
    }

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_file = out_dir / f"benchmark_{ts}.json"
    out_file.write_text(json.dumps(results, indent=2), encoding="utf-8")

    # Lien symbolique / copie vers baseline.json
    baseline_file = out_dir / "benchmark_baseline.json"

    # Affichage résultats
    print(f"\n{'Bench':<25} {'mean':>10} {'median':>10} {'p95':>10}")
    print("-" * 60)
    for name, stats in results["benches"].items():
        print(f"{name:<25} {stats['mean_ms']:>8.3f}ms {stats['median_ms']:>8.3f}ms {stats['p95_ms']:>8.3f}ms")

    if args.compare and baseline_file.exists():
        print("\n[COMPARAISON vs baseline]")
        prev = json.loads(baseline_file.read_text(encoding="utf-8"))
        for name, stats in results["benches"].items():
            if name in prev.get("benches", {}):
                prev_mean = prev["benches"][name]["mean_ms"]
                curr_mean = stats["mean_ms"]
                delta_pct = ((curr_mean - prev_mean) / prev_mean * 100) if prev_mean > 0 else 0
                marker = "[UP]" if delta_pct > 10 else ("[DN]" if delta_pct < -10 else "    ")
                print(f"{marker} {name:<25} {prev_mean:>8.3f} -> {curr_mean:>8.3f}ms ({delta_pct:+.1f}%)")

    # Écrit aussi en baseline si pas déjà présent
    if not baseline_file.exists():
        baseline_file.write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"\n[OK] Baseline initialisee : {baseline_file.name}")

    print(f"\n[OK] Rapport ecrit : {out_file.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
