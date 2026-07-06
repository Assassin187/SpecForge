# Planning Utility Baseline Runners

本目录实现 SpecForge 论文主实验的 planning utility 对照组 runner。目标是比较结构化 planning agent 对下游代码生成的贡献，而不是替换 Full SpecForge 主流程。

## Methods

本实验支持三种方法：

- `fs-direct-coder`
  - 输入 `protocol_facts.json`、`target_profile.json`、从 `minimum_v1` 抽取的最小功能需求，以及统一的 binary/argv runtime contract。
  - 先生成 `source_tree_skeleton.json`，只包含源码路径、文件类型和生成顺序，不包含模块职责、接口草图、函数列表、依赖关系或行为计划。
  - 再以 `.h/.c` 文件对为生成单位补全源码，`main.c` 单独生成，由 runner 写入 deterministic `Makefile`。
  - 随后执行 static checks、Bounded Generic C Repair、runtime start probe 和最小行为测试。

- `nl-plan-code`
  - 输入同上。
  - 先生成只含自然语言实现思路的 `nl_plan.md`；禁止 JSON/YAML、table-like specs、inventory、dependency graph、type ownership table、`FILE_SPEC`、`FUNCTION_SPEC` 等结构化 planning artifacts。
  - 将压缩后的 plan brief 加入 FS-Direct 的 source tree skeleton 和 pair-wise `.h/.c` completion prompt；skeleton 仍只包含 `path`、`kind`、`order`，`main.c` 仍作为单独 generation unit。
  - 后续直接复用 FS-Direct 的 static checks、Bounded Generic C Repair 和 smoke test，不生成或消费 `project_strategy.json`。
  - 该 baseline 用于检验自然语言工程计划是否足以替代结构化 implementation-oriented protocol specs。

- `full-specforge`
  - 保持现有流程：`protocol_facts.json + target_profile.json -> planning agent -> spec_bundle -> coder agent -> compile/repair/smoke`。
  - 本目录只通过 CLI adapter 调用现有 `agent.planning` 和 `agent.coder`，不修改 planner、spec compiler 或 coder schema。

两条 baseline 共用同一套 generation reliability 约束：已有 headers 通过 Clang AST 机械提取完整 public declarations，Clang 不可用或无法解析时回退到移除 comments/include guards 后的完整 header；header context 只按完整 declaration block 裁剪。Skeleton 和 pair completion 只接受严格 JSON object，单次请求使用 16,384 completion token 上限，deterministic parse/schema validation 失败时最多重新生成一次。该 retry 属于 generation validation，不计入 repair call budget。

## Bounded Generic C Repair

`fs-direct-coder` 和 `nl-plan-code` 默认在 generation 后运行新的 bounded generic C repair。该机制完全替代旧 source-level `.c` repair，并且可对已有 `coder_out/<protocol>` 项目目录独立调用。

边界：

- 只使用生成项目内 `.h`、`.c`、`Makefile`、compiler/linker diagnostics、runtime argv contract。
- 禁止读取 `spec_bundle`、`PROTOCOL_MODULE_SPEC`、`FILE_SPEC`、`FUNCTION_SPEC`、dependency graph、wire/access binding、gold specs、example specs 或 planning validators。
- 优先 deterministic 修复 C1/C2；最多 3 次 LLM 修 C2/C3，最多 2 次 link repair，最多 1 次 runtime startup repair，总 LLM repair calls 不超过 6。
- C4/C5 和需要 ownership、message model、state model 或 behavior contract 才能判断的问题记录为 planning-dependent remaining defects，不做协议语义重写。

## Anti-Leak Boundary

baseline runner 必须只使用 allowed inputs：

- sanitized `facts_view`
- `target_profile`
- `minimum_requirements`
- runtime contract，例如 `./smtp_server <port> <mail_store_dir>`

baseline 不得调用 planning agent，不得读取 `specs-example/`、`gold_specs/` 或 `spec_bundle/`，不得使用 SpecForge 的 `PROTOCOL_MODULE_SPEC`、`FILE_SPEC`、`FUNCTION_SPEC`、dependency graph、wire/access binding 或 validators。

`baseline_runner.py` 会对 allowed input snapshot、skeleton 和生成源码执行 forbidden term scan，并在 `summary.json` / `run_manifest.json` 中记录 `leakage_guard`。文件级 `planning_artifact_guard` 禁止 FS-Direct 生成任何 planning artifact，并禁止 NL-Plan 生成 `nl_plan.md` 以外的 planning artifact。NL-Plan 另以 `nl_plan_guard_status` / `nl_plan_guard` 记录非空计划文件是否成功生成；该 guard 不扫描计划内容。

## Supported Protocols

协议配置在 `configs.py` 中：

| protocol | facts | target profile | binary | argv contract |
| --- | --- | --- | --- | --- |
| `http` | `agent/facts/gold_facts/http_min/protocol_facts.json` | `evaluation/planning_utility/target_profiles/planning_target_profile_http.json` | `http_server` | `./http_server <port> <document_root>` |
| `mqtt` | `agent/facts/gold_facts/mqtt_min/protocol_facts.json` | `agent/planning/planning_target_profile_mqtt.json` | `mqtt_broker` | `./mqtt_broker <port>` |
| `coap` | `agent/facts/gold_facts/coap_min/protocol_facts.json` | `agent/planning/planning_target_profile_coap.json` | `coap_server` | `./coap_server <port>` |
| `smtp` | `agent/facts/gold_facts/smtp_min/protocol_facts.json` | `agent/planning/planning_target_profile_smtp_min.json` | `smtp_server` | `./smtp_server <port> <mail_store_dir>` |

## Usage

查看参数：

```bash
python3 -m evaluation.planning_utility.run_matrix --help
```

运行单个 baseline：

```bash
python3 -m evaluation.planning_utility.run_matrix \
  --protocol http \
  --method fs-direct-coder \
  --max-repair-calls 6 \
  --api-key-env ALI_API
```

运行 NL plan baseline：

```bash
python3 -m evaluation.planning_utility.run_matrix \
  --protocol smtp \
  --method nl-plan-code \
  --max-repair-calls 6 \
  --api-key-env ALI_API
```

`--max-repair-calls` 控制两条 baseline 的 bounded generic C repair，默认 6；`--max-repair-rounds` 仅影响 `full-specforge`。

独立修复已有项目。CLI 会先把 `--project-dir` 指向的已有 `coder_out/<protocol>` 项目复制到
`evaluation/planning_utility/out/<date>_<protocol>_after_repair_round_<N>/<timestamp>/<protocol>/<method>/coder_out/<protocol>`，然后只在副本上执行 repair。`round_<N>` 从 source project 的生成 run 路径中识别，`<timestamp>` 使用 repair 启动时间。原项目只作为代码来源，不会被改动；repair diagnostics、logs、summary 等报告和中间文件都写入新复制出的 repair run 文件夹。

```bash
python3 -m evaluation.planning_utility.repair_cli \
  --project-dir /path/to/coder_out/mqtt \
  --method fs-direct-coder \
  --protocol mqtt \
  --binary-name mqtt_broker \
  --argv-contract './mqtt_broker <port>' \
  --max-repair-calls 6 \
  --api-key-env ALI_API
```

批量修复已有项目：

```bash
python3 -m evaluation.planning_utility.repair_cli \
  --batch-input repair_projects.csv \
  --jobs 4 \
  --max-repair-calls 6 \
  --api-key-env ALI_API
```

`repair_projects.csv` 需要包含 `project_dir,method,protocol,binary_name,argv_contract`。批量模式会创建一个 common repair group 和本次时间戳层，拓扑与 baseline generation run 对齐：`out/<date>_<protocol>_after_repair_round_<N>/<timestamp>/<protocol>/<method>/coder_out/<protocol>`；同一批次中的多个 method 会放在同一个 `<timestamp>` 文件夹下，并在该时间戳文件夹顶层生成 `batch_repair_summary.json` 和 `batch_repair_summary.md`。`--dry-run` 会继续在临时副本上验证 repair，不写回 out 中的项目副本源码。

运行完整三方法、四协议矩阵：

```bash
python3 -m evaluation.planning_utility.run_matrix \
  --api-key-env ALI_API
```

复用已有 Full SpecForge planning output：

```bash
python3 -m evaluation.planning_utility.run_matrix \
  --method full-specforge \
  --protocol mqtt \
  --full-planning-dir mqtt=/path/to/existing/planning_run \
  --api-key-env ALI_API
```

## Output Layout

默认输出目录为 `evaluation/planning_utility/out/<run_id>/`：

```text
evaluation/planning_utility/out/<run_id>/
├── matrix_summary.json
├── matrix_summary.md
└── <protocol>/
    ├── fs-direct-coder/
    │   ├── allowed_inputs/
    │   ├── source_tree_skeleton.json
    │   ├── coder_out/
    │   │   ├── <protocol>/
    │   │   └── _agent_logs/
    │   │       ├── run_manifest.json
    │   │       └── repair_summary.json
    │   └── summary.json
    ├── nl-plan-code/
    │   ├── allowed_inputs/
    │   ├── nl_plan.md
    │   ├── source_tree_skeleton.json
    │   ├── coder_out/
    │   └── summary.json
    └── full-specforge/
        ├── allowed_inputs/
        ├── planning_run/
        ├── coder_out/
        └── summary.json
```

## Recorded Metrics

每个 `summary.json` 记录统一字段，便于论文表格聚合：

- method、protocol、input hashes、binary/argv contract
- `planning_status`
- `source_tree_skeleton_status`
- `pair_completion_status`
- `strategy_generation_status`
- `nl_plan_status`
- `nl_plan_guard_status`、`nl_plan_guard`
- `file_generation_status`
- `generated_pairs`
- `generated_files`
- `header_context_diagnostics`
- `json_retry_count`、`json_generation_attempts`、`failed_generation_unit`
- `planning_artifact_guard`
- `static_check_status`
- `compile_status`
- `repair_iterations`
- `repair_stop_reason`
- `repaired_files`
- `blocking_files`
- `repair_summary_path`
- `compile_before_repair`
- `compile_after_deterministic`
- `compile_after_llm`
- `link_after_repair`
- `runtime_start_status`
- `planning_dependent_remaining_count`
- `smoke_status`
- `verification_success`
- scenario pass/fail/skip counts
- LLM call usage、stage token usage、workflow token usage
- stage timings
- `failure_stage`、`failure_categories`、`main_diagnostic`
- `leakage_guard`

每个 `repair_summary.json` 额外记录 C1/C2/C3/C4/C5 before/fixed/remaining counts、header/source/link error before/after、deterministic/LLM patch counts、semantic risk、generated stub、wrapper、duplicate symbol、signature alignment、portability repair counts，以及 final root causes。批量入口会生成 `batch_repair_summary.json` 和 `batch_repair_summary.md`，展示哪些项目通过 C1/C2 repair 改善，哪些仍因 C3/C4/C5 planning-dependent defects 阻塞。

注意：baseline 的成功标准仍需区分 compile 和 behavior。`compile_status=passed` 只表示项目最终编译成功；`smoke_status=passed` 才表示最小行为测试通过。

## Tests

运行 planning utility 自测：

```bash
python3 -m unittest discover -s evaluation/planning_utility/tests -v
```

同时建议确认旧 minimum matrix runner 未受影响：

```bash
python3 -m unittest agent.coder.tests.test_minimum_matrix_runner -v
```
