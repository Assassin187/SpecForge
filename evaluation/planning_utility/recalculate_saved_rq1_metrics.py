"""Recalculate RQ1 metrics from saved projects without generation or repair."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import statistics
import sys
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any

from .contract_closure_analysis import analyze_contract_closure
from .repair_diagnostics import CATEGORIES, create_diagnostic_snapshot, iter_project_files


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RUBRIC = PROJECT_ROOT / "evaluation/planning_utility/target_profiles/mqtt_obligation_rubric.json"
BASELINE_GLOB = "evaluation/planning_utility/out/20260706_mqtt_after_repair_round_*/*/mqtt/*/coder_out/_agent_logs/repair_summary.json"
M3_SEQUENCES = (
    ("S", PROJECT_ROOT / "agent/planning/out/candidate_through_stability_20260715", "stability_run_*"),
    ("R", PROJECT_ROOT / "agent/planning/out/final_root_fix_pilot_20260715", "root_fix_run_*"),
    (
        "T",
        PROJECT_ROOT / "evaluation/planning_utility/out/planning_coder_10run_collection_20260716/historical",
        "run_07_*",
    ),
    (
        "N",
        PROJECT_ROOT / "evaluation/planning_utility/out/planning_coder_10run_collection_20260716/new_experiments",
        "run_08_replacement_*",
    ),
    (
        "N",
        PROJECT_ROOT / "evaluation/planning_utility/out/planning_coder_10run_collection_20260716/new_experiments",
        "run_0[9]_*",
    ),
    (
        "N",
        PROJECT_ROOT / "evaluation/planning_utility/out/planning_coder_10run_collection_20260716/new_experiments",
        "run_10_*",
    ),
)
OBLIGATION_METRICS = (
    "required_obligation_realization",
    "executable_call_path_closure",
    "semantic_grounding_closure",
)


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _source_hashes(project: Path) -> dict[str, str]:
    return {
        path.relative_to(project).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in iter_project_files(project)
    }


def _source_kloc(project: Path) -> float:
    lines = 0
    for path in iter_project_files(project):
        if path.suffix == ".c":
            lines += len(path.read_text(encoding="utf-8", errors="ignore").splitlines())
    return lines / 1000.0


def _snapshot(project: Path, binary: str) -> dict[str, Any]:
    before = _source_hashes(project)
    with tempfile.TemporaryDirectory(prefix="specforge_rq1_recalc_") as raw:
        copied = Path(raw) / "project"
        shutil.copytree(
            project,
            copied,
            ignore=shutil.ignore_patterns(".git", ".repair", "_agent_logs", "__pycache__", "*.orig", "*.rej"),
        )
        snapshot = create_diagnostic_snapshot(copied, binary)
    if before != _source_hashes(project):
        raise RuntimeError(f"source changed during read-only analysis: {project}")
    sound = snapshot["sound_build"]
    return {
        "category_counts": snapshot["category_counts"],
        "diagnostic_total": len(snapshot["root_causes"]),
        "native_compile_passed": int(snapshot["build"].get("returncode", 1)) == 0,
        "sound_build_passed": bool(sound["passed"]),
        "sound_diagnostic_codes": sound["diagnostic_codes"],
        "source_kloc": _source_kloc(project),
        "source_hash_preserved": True,
    }


def _obligations(project: Path, binary: str, rubric: dict[str, Any]) -> dict[str, Any]:
    result = analyze_contract_closure(project, binary, obligation_rubric=rubric)
    return {
        name: {
            "status": result["metrics"][name]["status"],
            "passed": result["metrics"][name]["passed"],
            "total": result["metrics"][name]["total"],
            "rate": result["metrics"][name]["rate"],
        }
        for name in OBLIGATION_METRICS
    }


def _baseline_runs() -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = []
    for path in PROJECT_ROOT.glob(BASELINE_GLOB):
        data = _load(path)
        round_match = re.search(r"after_repair_round_(\d+)", path.as_posix())
        if not round_match:
            raise ValueError(f"cannot derive baseline round from {path}")
        method = str(data["method"])
        prefix = "FS" if method == "fs-direct-coder" else "NL"
        runs.append(
            {
                "run_id": f"{prefix}{int(round_match.group(1)):02d}",
                "method": method,
                "pre_project": Path(data["source_project_dir"]),
                "post_project": Path(data["repair_project_dir"]),
                "binary_name": str(data["binary_name"]),
                "repair_calls": int(data.get("llm_repair_calls", 0)),
                "repair_tokens": int((data.get("workflow_token_usage") or {}).get("total_tokens", 0)),
                "repair_stop_reason": str(data.get("repair_stop_reason", "")),
                "saved_native_compile_success": data.get("link_after_repair") == "passed",
                "evidence_path": path,
            }
        )
    return runs


def _m3_runs() -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = []
    for prefix, root, pattern in M3_SEQUENCES:
        for run_dir in sorted(root.glob(pattern)):
            manifests = sorted(run_dir.glob("coder_original/mqtt_repair_*/_agent_logs/*repair_manifest.json"))
            if len(manifests) != 1:
                raise ValueError(f"expected one repair manifest under {run_dir}, found {len(manifests)}")
            path = manifests[0]
            data = _load(path)
            number_match = re.match(r"run_(\d+)_", run_dir.name)
            number = int(number_match.group(1)) if number_match else int(run_dir.name.rsplit("_", 1)[-1])
            repair = data.get("repair") or {}
            runs.append(
                {
                    "run_id": f"M3-{prefix}{number}",
                    "method": "full-specforge",
                    "pre_project": Path(data["source_project_dir"]),
                    "post_project": Path(data["project_dir"]),
                    "binary_name": str(data["binary_name"]),
                    "repair_calls": len(data.get("llm_call_usage") or []),
                    "repair_tokens": int((data.get("workflow_token_usage") or {}).get("total_tokens", 0)),
                    "repair_rounds": int(repair.get("rounds_attempted", 0)),
                    "repair_stop_reason": str(repair.get("stop_reason", "")),
                    "saved_native_compile_success": bool(data.get("compile_success")),
                    "evidence_path": path,
                }
            )
    return runs


def discover_runs() -> list[dict[str, Any]]:
    runs = [*_baseline_runs(), *_m3_runs()]
    counts = defaultdict(int)
    for run in runs:
        counts[run["method"]] += 1
        for key in ("pre_project", "post_project", "evidence_path"):
            if not run[key].exists():
                raise FileNotFoundError(run[key])
    expected = {"fs-direct-coder": 10, "nl-plan-code": 10, "full-specforge": 10}
    if dict(counts) != expected:
        raise ValueError(f"unexpected run matrix: {dict(counts)}")
    return sorted(runs, key=lambda item: (item["method"], item["run_id"]))


def _distribution(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)
    quartiles = statistics.quantiles(ordered, n=4, method="inclusive") if len(ordered) > 1 else [ordered[0]] * 3
    return {
        "median": statistics.median(ordered),
        "q1": quartiles[0],
        "q3": quartiles[2],
        "min": ordered[0],
        "max": ordered[-1],
    }


def _aggregate(method: str, runs: list[dict[str, Any]]) -> dict[str, Any]:
    categories: dict[str, Any] = {}
    for category in CATEGORIES:
        before = sum(run["pre"]["classifier"]["category_counts"][category] for run in runs)
        after = sum(run["post"]["classifier"]["category_counts"][category] for run in runs)
        categories[category] = {
            "affected_projects_before": sum(
                run["pre"]["classifier"]["category_counts"][category] > 0 for run in runs
            ),
            "before": before,
            "after": after,
            "closure_rate": (before - after) / before if before else None,
        }

    def family(names: tuple[str, ...]) -> dict[str, Any]:
        before = sum(categories[name]["before"] for name in names)
        after = sum(categories[name]["after"] for name in names)
        return {"before": before, "after": after, "closure_rate": (before - after) / before if before else None}

    obligations: dict[str, Any] = {}
    for metric in OBLIGATION_METRICS:
        before_rates = [run["pre"]["obligations"][metric]["rate"] for run in runs]
        after_rates = [run["post"]["obligations"][metric]["rate"] for run in runs]
        gains = [after - before for before, after in zip(before_rates, after_rates)]
        obligations[metric] = {
            "pre": _distribution(before_rates),
            "post": _distribution(after_rates),
            "gain": _distribution(gains),
            "pre_pooled": {
                "passed": sum(run["pre"]["obligations"][metric]["passed"] for run in runs),
                "total": sum(run["pre"]["obligations"][metric]["total"] for run in runs),
            },
            "post_pooled": {
                "passed": sum(run["post"]["obligations"][metric]["passed"] for run in runs),
                "total": sum(run["post"]["obligations"][metric]["total"] for run in runs),
            },
        }

    post_densities = [
        sum(run["post"]["classifier"]["category_counts"][name] for name in ("C2", "C3", "C4", "C5"))
        / max(run["post"]["classifier"]["source_kloc"], 0.001)
        for run in runs
    ]
    calls = sum(run["repair_calls"] for run in runs)
    tokens = sum(run["repair_tokens"] for run in runs)
    native_recoveries = sum(
        not run["pre"]["classifier"]["native_compile_passed"]
        and run["post"]["classifier"]["native_compile_passed"]
        for run in runs
    )
    return {
        "method": method,
        "n": len(runs),
        "categories": categories,
        "C2_C5": family(("C2", "C3", "C4", "C5")),
        "C3_C5": family(("C3", "C4", "C5")),
        "initial_native_compile_success": sum(run["pre"]["classifier"]["native_compile_passed"] for run in runs),
        "post_native_compile_success": sum(run["post"]["classifier"]["native_compile_passed"] for run in runs),
        "post_sound_build_success": sum(run["post"]["classifier"]["sound_build_passed"] for run in runs),
        "native_compile_recoveries": native_recoveries,
        "repair_calls": calls,
        "repair_tokens": tokens,
        "native_recoveries_per_call": native_recoveries / calls if calls else None,
        "native_recoveries_per_100k_tokens": native_recoveries / (tokens / 100000) if tokens else None,
        "post_C2_C5_diagnostics_per_kloc": _distribution(post_densities),
        "obligations": obligations,
    }


def recalculate(rubric_path: Path) -> dict[str, Any]:
    rubric = _load(rubric_path)
    analyzed: list[dict[str, Any]] = []
    analysis_cache: dict[tuple[str, tuple[tuple[str, str], ...]], dict[str, Any]] = {}
    runs = discover_runs()
    phase_total = len(runs) * 2
    phase_number = 0
    for raw in runs:
        run = {
            key: (str(value) if isinstance(value, Path) else value)
            for key, value in raw.items()
            if key not in {"pre_project", "post_project"}
        }
        for phase in ("pre", "post"):
            phase_number += 1
            project = raw[f"{phase}_project"]
            run[f"{phase}_project"] = str(project)
            cache_key = (raw["binary_name"], tuple(sorted(_source_hashes(project).items())))
            cache_hit = cache_key in analysis_cache
            print(
                f"[{phase_number}/{phase_total}] {raw['run_id']} {phase} cache={'hit' if cache_hit else 'miss'}",
                file=sys.stderr,
                flush=True,
            )
            if not cache_hit:
                analysis_cache[cache_key] = {
                    "classifier": _snapshot(project, raw["binary_name"]),
                    "obligations": _obligations(project, raw["binary_name"], rubric),
                }
            run[phase] = analysis_cache[cache_key]
        analyzed.append(run)
    by_method = defaultdict(list)
    for run in analyzed:
        by_method[run["method"]].append(run)
    return {
        "schema_version": "planning_utility_saved_rq1_recalculation/v1",
        "analysis_scope": "saved projects only; no planning, generation, or repair calls",
        "rubric_path": str(rubric_path),
        "run_count": len(analyzed),
        "runs": analyzed,
        "aggregates": {
            method: _aggregate(method, runs)
            for method, runs in sorted(by_method.items())
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rubric", type=Path, default=DEFAULT_RUBRIC)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    payload = recalculate(args.rubric.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
