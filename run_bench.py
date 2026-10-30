import subprocess
import time
import re
import json
import statistics
from pathlib import Path

CS292_DIR = Path(r"..\..\cs292\cs292-lean-main").resolve()
LAKE_BIN = Path.home() / ".elan" / "bin" / "lake.exe"

FILES = [
    "Course/L05_ComprehensiveRecap.lean",
    "Course/L06_FP_Primer.lean",
]

WARMUP_RUNS = 1
MEASURED_RUNS = 3


def parse_profile_output(output: str) -> dict[str, float]:
    """Parse 'cumulative profiling times:' block into {category: time_ms}."""
    metrics = {}
    in_block = False
    for line in output.splitlines():
        if line.strip().startswith("cumulative profiling times:"):
            in_block = True
            continue
        if in_block:
            # Lines look like: "\timport 3.13s" or "\telaboration 160ms"
            m = re.match(r"^\s+(.+?)\s+([0-9.]+)(ms|s)\s*$", line)
            if m:
                cat, val, unit = m.group(1), float(m.group(2)), m.group(3)
                ms = val * 1000.0 if unit == "s" else val
                metrics[cat] = ms
            elif line.strip() == "":
                continue
            else:
                in_block = False
    return metrics


def run_once(rel_file: str) -> tuple[float, dict[str, float], str]:
    cmd = [str(LAKE_BIN), "env", "lean", "--profile", rel_file]
    t0 = time.perf_counter()
    proc = subprocess.run(
        cmd,
        cwd=str(CS292_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    wall_ms = (time.perf_counter() - t0) * 1000.0
    metrics = parse_profile_output(proc.stdout)
    return wall_ms, metrics, proc.stdout


def main():
    all_results = {}
    for rel_file in FILES:
        print(f"\n=======================================================")
        print(f"Benchmarking: {rel_file}")
        print(f"=======================================================")

        runs = []
        for w in range(WARMUP_RUNS):
            print(f"  [Warmup {w+1}/{WARMUP_RUNS}] running...", flush=True)
            w_wall_ms, w_metrics, raw = run_once(rel_file)
            print(f"  [Warmup {w+1}/{WARMUP_RUNS}] end-to-end: {w_wall_ms:.1f} ms (import: {w_metrics.get('import', 0):.1f} ms, elaboration: {w_metrics.get('elaboration', 0):.1f} ms)")
            runs.append({"is_warmup": True, "wall_ms": w_wall_ms, "metrics": w_metrics})

        for i in range(MEASURED_RUNS):
            print(f"  [Run {i+1}/{MEASURED_RUNS}] running...", flush=True)
            wall_ms, metrics, raw = run_once(rel_file)
            print(f"  [Run {i+1}/{MEASURED_RUNS}] end-to-end: {wall_ms:.1f} ms (import: {metrics.get('import', 0):.1f} ms, elaboration: {metrics.get('elaboration', 0):.1f} ms)")
            runs.append({"is_warmup": False, "wall_ms": wall_ms, "metrics": metrics})

        measured_runs = [r for r in runs if not r["is_warmup"]]
        # Compute summary stats over measured_runs only
        all_cats = sorted({k for r in measured_runs for k in r["metrics"].keys()})
        summary = {
            "wall_ms_mean": statistics.mean(r["wall_ms"] for r in measured_runs),
            "wall_ms_stdev": statistics.stdev(r["wall_ms"] for r in measured_runs) if len(measured_runs) > 1 else 0.0,
            "categories": {},
        }
        print(f"\n--- Summary for {rel_file} (n={MEASURED_RUNS} measured runs) ---")
        print(f"  {'End-to-End Wall Clock':<30}: {summary['wall_ms_mean']:8.2f} ms ± {summary['wall_ms_stdev']:6.2f} ms")
        for cat in all_cats:
            vals = [r["metrics"].get(cat, 0.0) for r in measured_runs]
            mean_v = statistics.mean(vals)
            std_v = statistics.stdev(vals) if len(vals) > 1 else 0.0
            summary["categories"][cat] = {"mean_ms": mean_v, "stdev_ms": std_v, "runs_ms": vals}
            print(f"  {cat:<30}: {mean_v:8.2f} ms ± {std_v:6.2f} ms")

        all_results[rel_file] = {"runs": runs, "summary": summary}

    out_path = Path("benchmark_l05_l06.json")
    out_path.write_text(json.dumps(all_results, indent=2), encoding="utf-8")
    print(f"\nSaved raw and summary data to {out_path.resolve()}")


if __name__ == "__main__":
    main()

