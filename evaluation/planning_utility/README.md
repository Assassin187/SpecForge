# Planning Utility Baseline Runners

本目录实现 SpecForge 论文主实验的 planning utility 对照组 runner。目标是比较结构化 planning agent 对下游代码生成的贡献，而不是替换 Full SpecForge 主流程。

## Methods

本实验支持三种方法：

- `direct-code-agent`
  - 输入 `protocol_facts.json`、`target_profile.json`、从 `minimum_v1` 抽取的最小功能需求，以及统一的 binary/argv runtime contract。
  - 先生成轻量 `project_strategy.json`，包含模块划分、文件清单、接口草图和 entrypoint/build 约束。
  - 再按文件逐个生成 `.h`、`.c`、`main.c`，由 runner 写入 deterministic `Makefile`。
  - 随后执行 static checks、compile、source-level `.c` repair，编译成功后运行最小行为测试。

- `nl-plan-code`
  - 输入同上。
  - 先生成 `nl_plan.md`，描述协议角色、模块划分、数据结构、函数职责、解析逻辑、状态处理和错误处理。
  - 再基于 `nl_plan.md` 生成 `project_strategy.json`，并复用同一套分文件生成、编译修复和 smoke test 流程。
  - 该 baseline 用于检验自然语言工程计划是否足以替代结构化 implementation-oriented protocol specs。

- `full-specforge`
  - 保持现有流程：`protocol_facts.json + target_profile.json -> planning agent -> spec_bundle -> coder agent -> compile/repair/smoke`。
  - 本目录只通过 CLI adapter 调用现有 `agent.planning` 和 `agent.coder`，不修改 planner、spec compiler 或 coder schema。

## Anti-Leak Boundary

baseline runner 必须只使用 allowed inputs：

- sanitized `facts_view`
- `target_profile`
- `minimum_requirements`
- runtime contract，例如 `./smtp_server <port> <mail_store_dir>`

baseline 不得调用 planning agent，不得读取 `specs-example/`、`gold_specs/` 或 `spec_bundle/`，不得使用 SpecForge 的 `PROTOCOL_MODULE_SPEC`、`FILE_SPEC`、`FUNCTION_SPEC`、dependency graph、wire/access binding 或 validators。

`baseline_runner.py` 会对生成物和 allowed input snapshot 执行 forbidden term scan，并在 `summary.json` / `run_manifest.json` 中记录 `leakage_guard`。

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
  --method direct-code-agent \
  --api-key-env ALI_API
```

运行 NL plan baseline：

```bash
python3 -m evaluation.planning_utility.run_matrix \
  --protocol smtp \
  --method nl-plan-code \
  --max-repair-rounds 3 \
  --api-key-env ALI_API
```

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
    ├── direct-code-agent/
    │   ├── allowed_inputs/
    │   ├── project_strategy.json
    │   ├── coder_out/
    │   │   ├── <protocol>/
    │   │   └── _agent_logs/run_manifest.json
    │   └── summary.json
    ├── nl-plan-code/
    │   ├── allowed_inputs/
    │   ├── nl_plan.md
    │   ├── project_strategy.json
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
- `strategy_generation_status`
- `nl_plan_status`
- `file_generation_status`
- `static_check_status`
- `compile_status`
- `repair_iterations`
- `repair_stop_reason`
- `repaired_files`
- `blocking_files`
- `smoke_status`
- `verification_success`
- scenario pass/fail/skip counts
- LLM call usage、stage token usage、workflow token usage
- stage timings
- `failure_stage`、`failure_categories`、`main_diagnostic`
- `leakage_guard`

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

