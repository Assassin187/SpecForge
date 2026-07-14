from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT_ROOT = REPO_ROOT / "evaluation" / "spec_ablation" / "out"
DEFAULT_SUMMARY = DEFAULT_OUT_ROOT / "spec_ablation_protocol_summary.md"
DEFAULT_JSON = DEFAULT_OUT_ROOT / "spec_ablation_initial_diagnostic_taxonomy.json"
DEFAULT_CSV = DEFAULT_OUT_ROOT / "spec_ablation_initial_diagnostic_taxonomy_runs.csv"
DEFAULT_PNG = DEFAULT_OUT_ROOT / "spec_ablation_initial_diagnostic_taxonomy_heatmap.png"
DEFAULT_SVG = DEFAULT_OUT_ROOT / "spec_ablation_initial_diagnostic_taxonomy_heatmap.svg"

PROTOCOLS = ("http", "mqtt", "coap", "smtp")
SETTINGS = ("S1", "S2", "S3", "S4")
RUN_PATTERN = re.compile(r"_(S[1-4])_round_\d+$")
SOURCE_ERROR_PATTERN = re.compile(r"^.+?:\d+:\d+: (?:fatal )?error: (.+)$")
IMPLICIT_FUNCTION_PATTERN = re.compile(r"implicit declaration of function [‘'`](.+?)[’'`]")
QUOTED_IDENTIFIER_PATTERN = re.compile(r"[‘'`](.+?)[’'`]")

SECTION_START = "<!-- initial-diagnostic-taxonomy:start -->"
SECTION_END = "<!-- initial-diagnostic-taxonomy:end -->"
SCHEMA_VERSION = "spec_ablation_initial_diagnostic_taxonomy/v1"


@dataclass(frozen=True)
class Category:
    key: str
    short_label: str
    description: str


CATEGORIES = (
    Category(
        "local_c_portability",
        "Local C / portability",
        "标准库/POSIX include、feature macro、platform declaration 或本地 C build 问题",
    ),
    Category(
        "type_visibility_layout",
        "Type visibility / layout",
        "项目自定义 type 不可见、不完整，或表达式不具备预期 struct/union layout",
    ),
    Category(
        "field_schema_hallucination",
        "Field/schema hallucination",
        "访问 canonical type 中不存在的 struct/union member",
    ),
    Category(
        "api_signature_drift",
        "API/signature drift",
        "function/helper declaration、参数或返回类型不一致",
    ),
    Category(
        "symbol_visibility_hallucination",
        "Symbol visibility / hallucination",
        "项目 symbol、protocol constant 或 helper 未声明/未定义，或被模型臆造",
    ),
    Category(
        "syntax_expression",
        "Syntax / expression",
        "语法、常量表达式或 statement 结构错误",
    ),
    Category(
        "other",
        "Other primary diagnostic",
        "未匹配以上冻结规则的 primary compiler/linker diagnostic",
    ),
)
CATEGORY_BY_KEY = {item.key: item for item in CATEGORIES}

# These names are compiler-visible only when the correct standard/POSIX header,
# feature macro, or platform declaration is present. Missing declarations are
# local C/build defects rather than project-specific symbol hallucinations.
PORTABILITY_FUNCTIONS = {
    "accept4",
    "asprintf",
    "calloc",
    "clock_gettime",
    "free",
    "inet_ntop",
    "malloc",
    "realloc",
    "send",
    "snprintf",
    "sscanf",
    "strcasecmp",
    "strdup",
}
PORTABILITY_IDENTIFIERS = {
    "bool",
    "errno",
    "INET6_ADDRSTRLEN",
    "int8_t",
    "int16_t",
    "int32_t",
    "int64_t",
    "size_t",
    "ssize_t",
    "uint8_t",
    "uint16_t",
    "uint32_t",
    "uint64_t",
}


@dataclass(frozen=True)
class Diagnostic:
    category: str
    message: str
    raw_line: str


@dataclass(frozen=True)
class RunResult:
    protocol: str
    setting: str
    run_name: str
    manifest_path: str
    initial_compile_log: str
    category_counts: dict[str, int]
    diagnostics: list[Diagnostic]


def _quoted_identifier(message: str) -> str:
    match = QUOTED_IDENTIFIER_PATTERN.search(message)
    return match.group(1) if match else ""


def classify_message(message: str) -> str:
    lowered = message.lower()

    if "has no member named" in lowered:
        return "field_schema_hallucination"
    if "conflicting types for" in lowered or any(
        phrase in lowered
        for phrase in (
            "too few arguments to function",
            "too many arguments to function",
            "incompatible type for argument",
            "makes pointer from integer without a cast",
            "makes integer from pointer without a cast",
            "called object is not a function",
        )
    ):
        return "api_signature_drift"
    if any(
        phrase in lowered
        for phrase in (
            "request for member",
            "invalid use of incomplete",
            "has incomplete type",
            "storage size of",
        )
    ):
        return "type_visibility_layout"
    if "unknown type name" in lowered:
        identifier = _quoted_identifier(message)
        return "local_c_portability" if identifier in PORTABILITY_IDENTIFIERS else "type_visibility_layout"
    if "implicit declaration of function" in lowered:
        match = IMPLICIT_FUNCTION_PATTERN.search(message)
        identifier = match.group(1) if match else ""
        return "local_c_portability" if identifier in PORTABILITY_FUNCTIONS else "symbol_visibility_hallucination"
    if "undeclared (first use" in lowered or lowered.endswith(" undeclared"):
        identifier = _quoted_identifier(message)
        if identifier in PORTABILITY_IDENTIFIERS:
            return "local_c_portability"
        if identifier.endswith("_t"):
            return "type_visibility_layout"
        return "symbol_visibility_hallucination"
    if any(
        phrase in lowered
        for phrase in (
            "expected ",
            "stray ",
            "lvalue required",
            "invalid operands",
            "duplicate case value",
            "case label does not reduce",
            "else without a previous if",
            "missing terminating",
        )
    ):
        return "syntax_expression"
    if "no such file or directory" in lowered:
        return "local_c_portability"
    return "other"


def classify_line(line: str) -> Diagnostic | None:
    stripped = line.strip()
    if "undefined reference to" in stripped:
        message = stripped.split("undefined reference to", 1)[1].strip()
        return Diagnostic("symbol_visibility_hallucination", message, stripped)

    match = SOURCE_ERROR_PATTERN.match(stripped)
    if not match:
        return None
    message = match.group(1).strip()
    return Diagnostic(classify_message(message), message, stripped)


def parse_initial_compile_log(path: Path) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        diagnostic = classify_line(line)
        if diagnostic is not None:
            diagnostics.append(diagnostic)
    return diagnostics


def discover_runs(out_root: Path, *, expected_replicates: int | None = 10) -> list[RunResult]:
    results: list[RunResult] = []
    cell_counts: Counter[tuple[str, str]] = Counter()

    for protocol in PROTOCOLS:
        protocol_root = out_root / protocol
        if not protocol_root.is_dir():
            raise FileNotFoundError(f"missing protocol output directory: {protocol_root}")
        for run_dir in sorted(protocol_root.iterdir()):
            match = RUN_PATTERN.search(run_dir.name)
            manifest_path = run_dir / "_agent_logs" / "run_manifest.json"
            if not run_dir.is_dir() or match is None or not manifest_path.is_file():
                continue

            setting = match.group(1)
            compile_logs = sorted((run_dir / "_agent_logs").glob("*compile_stderr_0.txt"))
            if len(compile_logs) != 1:
                raise ValueError(f"expected one initial compile log in {run_dir}, found {len(compile_logs)}")
            diagnostics = parse_initial_compile_log(compile_logs[0])
            counts = Counter(item.category for item in diagnostics)
            results.append(
                RunResult(
                    protocol=protocol,
                    setting=setting,
                    run_name=run_dir.name,
                    manifest_path=manifest_path.relative_to(REPO_ROOT).as_posix(),
                    initial_compile_log=compile_logs[0].relative_to(REPO_ROOT).as_posix(),
                    category_counts={item.key: counts[item.key] for item in CATEGORIES},
                    diagnostics=diagnostics,
                )
            )
            cell_counts[(protocol, setting)] += 1

    if expected_replicates is not None:
        expected = {(protocol, setting): expected_replicates for protocol in PROTOCOLS for setting in SETTINGS}
        actual = {key: cell_counts[key] for key in expected}
        if actual != expected:
            raise ValueError(f"formal run matrix mismatch: expected={expected}, actual={actual}")
    return sorted(results, key=lambda item: (PROTOCOLS.index(item.protocol), SETTINGS.index(item.setting), item.run_name))


def aggregate_runs(results: Iterable[RunResult]) -> list[dict[str, object]]:
    grouped: dict[tuple[str, str], list[RunResult]] = {
        (protocol, setting): [] for protocol in PROTOCOLS for setting in SETTINGS
    }
    for result in results:
        grouped[(result.protocol, result.setting)].append(result)

    aggregates: list[dict[str, object]] = []
    for protocol in PROTOCOLS:
        for setting in SETTINGS:
            runs = grouped[(protocol, setting)]
            affected = {
                category.key: sum(run.category_counts[category.key] > 0 for run in runs)
                for category in CATEGORIES
            }
            instances = {
                category.key: sum(run.category_counts[category.key] for run in runs)
                for category in CATEGORIES
            }
            aggregates.append(
                {
                    "protocol": protocol,
                    "setting": setting,
                    "runs": len(runs),
                    "runs_with_any_primary_diagnostic": sum(bool(run.diagnostics) for run in runs),
                    "initially_clean_runs": sum(not run.diagnostics for run in runs),
                    "affected_runs": affected,
                    "diagnostic_instances": instances,
                }
            )
    return aggregates


def write_json(path: Path, results: list[RunResult], aggregates: list[dict[str, object]]) -> None:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "counting_unit": {
            "heatmap": "runs affected by at least one diagnostic in the category",
            "raw_instances": "primary compiler/linker diagnostic lines; overlapping causal chains are not deduplicated",
        },
        "taxonomy": [asdict(item) for item in CATEGORIES],
        "formal_run_count": len(results),
        "aggregates": aggregates,
        "runs": [
            {
                **{key: value for key, value in asdict(result).items() if key != "diagnostics"},
                "diagnostics": [asdict(item) for item in result.diagnostics],
            }
            for result in results
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_csv(path: Path, results: list[RunResult]) -> None:
    fieldnames = [
        "protocol",
        "setting",
        "run_name",
        "initial_compile_log",
        "primary_diagnostic_instances",
        *[item.key for item in CATEGORIES],
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for result in results:
            writer.writerow(
                {
                    "protocol": result.protocol,
                    "setting": result.setting,
                    "run_name": result.run_name,
                    "initial_compile_log": result.initial_compile_log,
                    "primary_diagnostic_instances": len(result.diagnostics),
                    **result.category_counts,
                }
            )


def _cell(aggregate: dict[str, object], category_key: str) -> str:
    affected = aggregate["affected_runs"][category_key]  # type: ignore[index]
    instances = aggregate["diagnostic_instances"][category_key]  # type: ignore[index]
    return f"{affected}/{aggregate['runs']} ({instances})"


def markdown_section(aggregates: list[dict[str, object]]) -> str:
    lines = [
        SECTION_START,
        "## Initial Compile Diagnostics Taxonomy",
        "",
        "本节由 `python3 -m evaluation.spec_ablation.initial_diagnostic_taxonomy` 从 160 个正式 run 的 "
        "`*_compile_stderr_0.txt` 只读聚合生成。分析不会重新编译或修改 generated code。",
        "",
        "计数口径：下表每个 taxonomy 单元格为 `受影响 runs / 10 (raw diagnostic instances)`；热力图另按 setting "
        "聚合四个协议的 40 个 run，显示 affected-run percentage。该口径避免由同一上游错误产生的大量 cascading "
        "diagnostics 主导颜色；括号内保留未经 root-cause 去重的 primary compiler/linker diagnostic lines。"
        "`collect2` 和 `make` 的汇总行不重复计数。",
        "",
        "| Protocol + setting | Any diagnostic runs | Local C / portability | Type visibility / layout | Field/schema hallucination | API/signature drift | Symbol visibility / hallucination | Syntax / expression | Other |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for aggregate in aggregates:
        protocol = str(aggregate["protocol"]).upper()
        setting = aggregate["setting"]
        cells = [_cell(aggregate, item.key) for item in CATEGORIES]
        lines.append(
            f"| {protocol} {setting} | {aggregate['runs_with_any_primary_diagnostic']}/{aggregate['runs']} | "
            + " | ".join(cells)
            + " |"
        )

    lookup = {(item["protocol"], item["setting"]): item for item in aggregates}
    mqtt_fields = [
        lookup[("mqtt", setting)]["affected_runs"]["field_schema_hallucination"]  # type: ignore[index]
        for setting in SETTINGS
    ]
    coap_s3_clean = lookup[("coap", "S3")]["initially_clean_runs"]
    other_instances = sum(item["diagnostic_instances"]["other"] for item in aggregates)  # type: ignore[index]
    lines.extend(
        [
            "",
            "关键观察：",
            "",
            f"- MQTT `Field/schema hallucination` 影响 run 数呈 `{mqtt_fields[0]} -> {mqtt_fields[1]} -> {mqtt_fields[2]} -> {mqtt_fields[3]}`，"
            "用于观察 interface grounding 和 Full-SpecForge 对 nonexistent member access 的约束作用。",
            f"- CoAP S3 有 {coap_s3_clean}/10 个 run 未出现任何 primary initial compile diagnostic，与其无需 repair 的过程记录一致。",
            f"- `Other` raw instances 合计为 {other_instances}；若非零，应在论文使用前逐项审计并决定是否扩展冻结 taxonomy。",
            "- 本表描述 diagnostic shape，不把 raw compiler lines 解释为独立 semantic bugs 或因果 effect size。",
            "",
            "Artifacts：",
            "",
            "- `spec_ablation_initial_diagnostic_taxonomy.json`：逐 run diagnostic 原文、分类与聚合；",
            "- `spec_ablation_initial_diagnostic_taxonomy_runs.csv`：160 行逐 run 分类计数；",
            "- `spec_ablation_initial_diagnostic_taxonomy_heatmap.png` / `.svg`：按 setting 聚合的 affected-run percentage heatmap；"
            "为适配单栏版面，图中省略 `Local C / portability` 列。",
            SECTION_END,
        ]
    )
    return "\n".join(lines)


def replace_marked_section(document: str, section: str) -> str:
    pattern = re.compile(re.escape(SECTION_START) + r".*?" + re.escape(SECTION_END), re.DOTALL)
    if pattern.search(document):
        return pattern.sub(section, document)
    return document.rstrip() + "\n\n" + section + "\n"


def update_summary(path: Path, section: str) -> None:
    current = path.read_text(encoding="utf-8")
    updated = replace_marked_section(current, section)
    path.write_text(updated, encoding="utf-8")


def render_heatmap(aggregates: list[dict[str, object]], png_path: Path, svg_path: Path) -> None:
    import matplotlib as mpl
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib.patches import Rectangle

    mpl.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": ["Noto Sans CJK SC", "DejaVu Sans"],
            "axes.unicode_minus": False,
            "figure.facecolor": "white",
            "axes.facecolor": "white",
            "savefig.facecolor": "white",
        }
    )

    plot_category_keys = (
        "type_visibility_layout",
        "field_schema_hallucination",
        "api_signature_drift",
        "symbol_visibility_hallucination",
        "syntax_expression",
    )
    affected_rates: list[list[float]] = []
    for setting in SETTINGS:
        setting_aggregates = [item for item in aggregates if item["setting"] == setting]
        total_runs = sum(int(item["runs"]) for item in setting_aggregates)
        affected_rates.append(
            [
                100
                * sum(int(item["affected_runs"][key]) for item in setting_aggregates)  # type: ignore[index]
                / total_runs
                for key in plot_category_keys
            ]
        )
    affected = np.array(affected_rates, dtype=float)
    row_labels = [*SETTINGS[:3], "S4\n(Full)"]
    column_labels = [
        "Type /\nlayout",
        "Field /\nschema",
        "API /\nsignature",
        "Symbol /\nvisibility",
        "Syntax /\nexpression",
    ]

    fig, ax = plt.subplots(figsize=(3.5, 2.35))
    image = ax.imshow(affected, cmap="RdYlGn_r", vmin=0, vmax=100, aspect="auto")

    for row in range(affected.shape[0]):
        for column in range(affected.shape[1]):
            value = affected[row, column]
            color = "white" if value <= 15 or value >= 75 else "#172033"
            ax.text(
                column,
                row,
                f"{value:.1f}%",
                ha="center",
                va="center",
                fontsize=6.8,
                fontweight="semibold",
                color=color,
            )

    ax.set_xticks(range(len(column_labels)), labels=column_labels)
    ax.set_yticks(range(len(row_labels)), labels=row_labels)
    ax.tick_params(axis="x", labelsize=6.2, length=0, pad=4)
    ax.tick_params(axis="y", labelsize=7, length=0, pad=4)
    ax.set_xticks(np.arange(-0.5, len(plot_category_keys), 1), minor=True)
    ax.set_yticks(np.arange(-0.5, len(SETTINGS), 1), minor=True)
    ax.grid(which="minor", color="white", linewidth=1.2)
    ax.tick_params(which="minor", bottom=False, left=False)
    ax.axvline(3.5, color="white", linewidth=3.5, zorder=3)
    ax.add_patch(
        Rectangle(
            (-0.5, 2.5),
            len(plot_category_keys),
            1,
            fill=False,
            edgecolor="#c026d3",
            linewidth=1.2,
            zorder=4,
        )
    )
    ax.get_yticklabels()[3].set_fontweight("bold")
    ax.get_yticklabels()[3].set_color("#a21caf")
    ax.get_yticklabels()[3].set_multialignment("center")
    for spine in ax.spines.values():
        spine.set_visible(False)

    colorbar = fig.colorbar(image, ax=ax, orientation="horizontal", fraction=0.055, pad=0.22, aspect=35)
    colorbar.set_ticks([0, 25, 50, 75, 100])
    colorbar.ax.tick_params(labelsize=6, length=0)
    colorbar.outline.set_visible(False)
    fig.subplots_adjust(left=0.12, right=0.995, top=0.98, bottom=0.27)
    fig.savefig(png_path, dpi=300, bbox_inches="tight")
    fig.savefig(svg_path, bbox_inches="tight")
    plt.close(fig)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Classify saved RQ2 initial compile diagnostics without modifying runs")
    parser.add_argument("--out-root", type=Path, default=DEFAULT_OUT_ROOT)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--json-out", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--csv-out", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--png-out", type=Path, default=DEFAULT_PNG)
    parser.add_argument("--svg-out", type=Path, default=DEFAULT_SVG)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    results = discover_runs(args.out_root)
    aggregates = aggregate_runs(results)
    write_json(args.json_out, results, aggregates)
    write_csv(args.csv_out, results)
    update_summary(args.summary, markdown_section(aggregates))
    render_heatmap(aggregates, args.png_out, args.svg_out)
    print(f"Analyzed {len(results)} formal runs")
    print(f"Summary: {args.summary}")
    print(f"JSON: {args.json_out}")
    print(f"CSV: {args.csv_out}")
    print(f"Heatmap: {args.png_out}")
    print(f"Heatmap SVG: {args.svg_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
