# SpecForge Planning Rewrite Progress

## 当前目标

在 `agent/planning` 中实现一个通用的协议实现 planning agent，将结构化 `protocol_facts.json`
转换为 coder-facing protocol specs。当前先稳定跑通 MQTT minimum scope，但 planning 代码、
阶段、IR、artifact boundary 和提示词接口面向通用的 protocol facts -> protocol specs 任务。

## 禁止修改范围

- 不修改 `agent/facts`
- 不修改 `agent/coder`
- 不修改 `specs-example`
- 不修改输入 `protocol_facts.json`
- 不修改参考 `mqtt_specs`
- 不修改 coder loader/schema/facts schema/facts 生成逻辑

## 只读契约分析

### Facts 输入契约

已只读查看：

- `/home/ljf/SpecForge/agent/facts/gold_facts/mqtt_min/protocol_facts.json`

输入是一个 JSON object，关键顶层字段包括：

- `protocol_meta`: 协议名、source documents、fact source type、target scope。
- `transport`: network stack、connection model、channels、runtime implications。
- `interaction_model`: interaction style、roles、interaction units、core flows。
- `message_model`: framing、surface catalog、message/command entries、field constraints。
- `state_model`: states、transitions、timers/constants、invariants。
- `routing_model`: dispatch keys、dispatch targets、matching rules。
- `resource_model`: resource objects、lifecycle rules、persistence scope。
- `error_and_limits`: error matrix、limits、security。
- `minimum_v1`: minimum supported surface/state/errors/limits/deferred features/assumptions。
- `planning_inputs`: facts agent 提供的 planning checklist、axes、risks、trigger facts。
- `open_questions`: facts 不足或 policy 未决项。
- `evidence_index`: evidence id 到 source document/chunk/excerpt 的索引。

规划实现必须把 `Protocol facts`、`Inferred engineering decisions`、`Open assumptions`
分开保存，不能把 engineering decision 或 assumption 写回 facts。

### Coder-facing specs 结构契约

已只读查看：

- `/home/ljf/SpecForge/specs-example/mqtt_specs`
- `/home/ljf/SpecForge/specs-example/specs_schema/module_spec_schema.json`
- `/home/ljf/SpecForge/specs-example/specs_schema/file_spec_schema.json`
- `/home/ljf/SpecForge/specs-example/specs_schema/function_spec_schema.json`
- `/home/ljf/SpecForge/agent/coder/specs.py`
- `/home/ljf/SpecForge/agent/coder/generation.py`
- `/home/ljf/SpecForge/agent/coder/header_recipes.py`
- `/home/ljf/SpecForge/agent/coder/README.md`

允许从参考 specs 中归纳的契约：

- spec root 下递归存在唯一 `KIND == "PROTOCOL_MODULE_SPEC"`。
- module spec 顶层包含 `PROTOCOL`、`MODULES`、`GENERATION_ORDER`、`CONSISTENCY_RULES`。
- file spec 顶层包含 `FILE`、`SOURCE`，普通 C 模块还包含 `HEADER`。
- `FILE.TRACE_ID` 是 function spec parent，function trace id 通过最后一个 `/` 归属到 file trace id。
- `SOURCE.INTERFACE[*].TRACE_ID` 必须能链接到 function spec。
- `HEADER.DATA` 描述 public type/const/macro，header renderer 会 deterministic lowering。
- `HEADER.INTERFACE` 描述 public function declarations。
- function spec 包含 `SIGNATURE`、`RELY`，并通过 `LOGIC` 或 `EVENT` 描述行为。
- `CALL_CONTRACTS`、`PUBLIC_SYMBOLS`、`ACCESS_PATHS`、`FORBIDDEN_SYMBOLS`、`TEST_VECTORS`
  是可选 machine constraints。

禁止从参考 specs 中复制或硬编码的实例内容：

- MQTT module/file/type/function inventory。
- behavior contracts、dependency graph、handler matrix。
- 资源生命周期设计和测试向量文本。

### 可由 compiler 确定性处理的结构

- spec dialect kind 和顶层字段。
- trace id/path/id 规范化。
- module/file/function spec 文件组织。
- `MODULES[*].FILES` 与 file specs 的关系。
- function spec 文件路径。
- schema-required 的空数组/空对象。
- 从 plan 中的 function/type ownership 派生 public symbols、access paths、module artifacts。

### 必须由 planning 生成的实例级信息

- module decomposition、responsibilities、boundaries、dependencies。
- file decomposition 和 header/source ownership。
- public/private types、functions、signatures。
- parser/serializer、transport/runtime、state、routing、entrypoint 责任。
- behavior contracts、lifecycle、error handling、test-relevant behavior。
- engineering decisions、open assumptions、traceability。

## Artifact 设计

`agent/planning` 包内计划创建：

- `facts.py`: facts loading、hashing、fact refs/evidence refs extraction。
- `knowledge.py`: normalized characteristics 和 activated generic engineering rules。
- `planner.py`: 多阶段 LLM Structured Planning、stage prompt、stage catalog、final plan assembly。
- `compiler.py`: Implementation Plan -> coder-facing specs lowering。
- `validation.py`: schema-shape、reference isolation、anti-hardcoding、facts sensitivity、
  plan-to-spec preservation、fact/decision/assumption separation、structural checks。
- `cli.py`: `plan` 和 `validate` 子命令。
- `README.md`: 运行、恢复、验证、扩展说明。
- `tests/`: planning package tests。

运行输出目录计划：

```text
<out>/
├── <protocol>_specs/
│   ├── <protocol>_module_spec.json
│   ├── SUMMARY.md
│   └── ... FILE_SPEC / FUNCTION_SPEC ...
└── _planning/
    ├── normalized_characteristics.json
    ├── activated_engineering_rules.json
    ├── structured_planning_stages.json
    ├── structured_planning_usage.json
    ├── stage_logs/
    ├── selected_architecture.json
    ├── implementation_plan.json
    ├── engineering_decisions.json
    ├── open_assumptions.json
    ├── diagnostics.json
    └── run_manifest.json
```

正常 planning 路径不得读取 `specs-example/mqtt_specs`。

## 已完成事项

- 已读取 goal objective。
- 已完成 facts 输入契约只读分析。
- 已完成 coder-facing specs/schema/loader/header renderer 只读分析。
- 已完成目录与 artifact boundary 设计。
- 已初始化本进度文件。
- 已实现 planning package 入口、CLI、pipeline。
- 已实现 facts loading/hash/fact refs。
- 已实现 protocol characteristics normalization。
- 已实现 generic engineering rule activation。
- 已删除 `LocalStructuredPlanner` 的固定化 module/file/type/function 生成逻辑。
- 已实现多阶段 LLM Structured Planning：
  - `scope_fact_inventory`
  - `architecture_boundaries`
  - `module_file_plan`
  - `public_artifact_inventory`
  - `type_and_access_path_design`
  - `function_interface_design`
  - `function_behavior_design`
  - `function_call_contract_closure`
  - `function_test_vector_design`
  - `dependency_closure`
  - `final_plan_assembly`
- 已实现 stage prompt、stage catalog 和 `structured_planning_stages.json` 输出。
- 已实现每个 LLM stage 的运行日志、prompt/response/artifact 落盘和 token usage 记录。
- 已实现 `function_behavior_design` 的 module/file 分区执行。
- 已实现 stage JSON repair：parse 失败后使用现有 LLM client 修复 JSON syntax，并单独记录 repair token。
- 已实现 `compile_specs` 输入规范化：将 final assembled artifact 的 file/type/function 表达转换为 compiler IR，
  并根据 planned type owner 与 public signature 派生 header dependency。
- 已实现 `--resume-from <stage_id>`：复用目标 stage 之前的 artifacts，从目标 stage 继续执行。
- 已实现 `--resume-from compile_specs`：复用全部 11 个 stage artifacts，不调用 LLM，重新生成并验证 specs。
- 已实现 deterministic specs compiler。
- 已实现 validation checks：
  - schema-shape
  - reference isolation
  - anti-hardcoding scan
  - facts sensitivity
  - plan-to-spec preservation
  - fact/decision/assumption separation
  - structural consistency
- 已实现 README。
- 已实现 `unittest` tests。
- 当前真实 planning run 需要 LLM API；不再支持无 LLM 的 deterministic local 生成。
- 测试通过 mock staged LLM 输出验证 pipeline/compiler/validation，不作为 production fallback。

## 已创建或修改文件

- `agent/planning/PLANNING_REWRITE_PROGRESS.md`
- `agent/planning/README.md`
- `agent/planning/__init__.py`
- `agent/planning/__main__.py`
- `agent/planning/cli.py`
- `agent/planning/compiler.py`
- `agent/planning/facts.py`
- `agent/planning/knowledge.py`
- `agent/planning/models.py`
- `agent/planning/pipeline.py`
- `agent/planning/planner.py`
- `agent/planning/validation.py`
- `agent/planning/tests/test_pipeline.py`

## 已运行命令

- `sed -n ... goal-objective.md`
- `find /home/ljf/SpecForge/...`
- `python3 -m json.tool ...specs_schema...`
- `python3` 只读检查 facts/specs keys
- `sed -n ... agent/coder/specs.py`
- `sed -n ... agent/coder/generation.py`
- `sed -n ... agent/coder/header_recipes.py`
- `git -C /home/ljf/SpecForge status --short`
- `python3 -m compileall -q /home/ljf/SpecForge/agent/planning`
- `python3 -m agent.planning --help`
- `python3 -m agent.planning stages`
- `python3 -m unittest discover -s /home/ljf/SpecForge/agent/planning/tests`
- 手动中断真实 LLM planning 进程，确认 KeyboardInterrupt 后进程退出。
- `find /home/ljf/SpecForge/agent/planning -type d -name __pycache__ -prune -exec rm -rf {} +`
- `python3` 开发期 JSON Schema validation against `/home/ljf/SpecForge/specs-example/specs_schema`

## 测试结果

- `python3 -m compileall -q /home/ljf/SpecForge/agent/planning`: pass
- `python3 -m agent.planning stages`: pass，列出 11 个 Structured Planning 子步骤
- `python3 -m unittest discover -s /home/ljf/SpecForge/agent/planning/tests`: pass，10 tests
- JSON Schema validation: tests 通过 mocked staged LLM fixture 覆盖 compiler 输出
- Stage logging: 已加入 `_planning/stage_logs/<NN_stage>/prompt.json`、`response.raw.txt`、
  `artifact.json`、`stage_manifest.json` 和 `_planning/structured_planning_usage.json`
- Resume tests: mocked LLM 覆盖全部 11 个 stage 恢复点和 `compile_specs` 无模型恢复点。
- 已对既有真实 MQTT stage artifacts 执行 `--resume-from compile_specs`：0 errors，1 warning；
  warning 为 `mqtt_connection_queue_outbound` 有 `WIRE_MAPPING` 但缺少 test vector anchor。
- 本轮没有发起新的真实 LLM 调用。

## 当前阻塞问题

无。

## 下一步任务

1. 下次真实 LLM planning 时补齐 wire-mapped function 的 test vector anchor。
2. 接入第二种协议时新增 facts fixture，用 facts sensitivity 测试确认 module/rule/plan 会随 facts 改变。
3. 后续可把 schema-shape check 扩展为 JSON Schema full validation，但正常 planning 路径仍应避免依赖参考 `mqtt_specs`。
