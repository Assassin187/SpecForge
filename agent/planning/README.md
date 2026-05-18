# Planning Agent

Planning Agent 位于 `agent/planning/`，是 SpecForge 三阶段流水线中的中间层：

```text
Facts Agent -> Planning Agent -> Coder Agent
```

它的作用不是简单改写格式，而是把 Facts Agent 提取出的 `protocol_facts.json` 转换为可执行的工程规划，并最终编译为当前 Coder Agent 可以直接读取的 `spec_bundle/`。

Planning Agent 的核心职责：

- 读取 Facts Agent 输出的协议事实。
- 读取目标实现配置 `target_profile.json`。
- 生成规范化的 planning IR。
- 推导协议 profile、工程约束、模块架构、实现计划、函数契约、依赖图和规格蓝图。
- 保留事实、证据、工程决策和最终 Coder specs 之间的 traceability。
- 输出 Coder Agent 当前可消费的 `spec_bundle/`。
- 强制使用 LLM 生成已接入阶段的候选，再用 deterministic validators 拦截 LLM 编造事实、非法依赖、非法函数、非法 Coder spec。

Planning Agent 运行时强制使用 LLM。LLM 不是可选增强，而是对应阶段的强制提案生成器：

- LLM 必须返回 JSON candidate 或 patch。
- candidate/patch 必须通过 deterministic validation。
- 如果 LLM 无输出、输出不是 JSON、schema 不合法或规则校验失败，会把失败原因追加到下一次 prompt 中重试。
- 每个 LLM 阶段最多重试 3 次。
- 3 次仍失败则该阶段报错退出。
- 不允许回退到 deterministic baseline。

## 当前流程

### Step 0: Preflight

输入：

- `protocol_facts.json` 路径
- `target_profile.json` 路径
- optional `--output-dir`
- Planning 配置

输出：

- `_step_logs/000_planning_run_manifest.json`
- `_agent_logs/000_stage_events.log`

具体操作：

- 校验输入文件是否存在。
- 校验 target profile 基础字段。
- 校验当前 target language 是否为 `C`。
- 初始化 run 目录。
- 记录输入文件 hash。
- 记录 compatibility version、prompt version、LLM 配置和 artifact 路径。

LLM 参与：

- 不参与。

### Step 1: Facts Input Adapter / Canonical Planning IR

输入：

- `protocol_facts.json`
- `target_profile.json`

输出：

- `_step_logs/003_planning_ir.json`

具体操作：

- 兼容读取 Facts Agent 当前输出格式 `protocol_facts/v2alpha1`。
- 校验 facts 顶层结构和关键 section。
- 将 `evidence_index[]` 转为 evidence map。
- 生成稳定的 path-based `fact_id`。
- 建立 `normalization_index`：
  - `fact_id_by_path`
  - `evidence_refs_by_fact_id`
  - `field_id_by_message_and_name`
- 将 target profile 转换到独立的 `target_directives` namespace。
- 不把 target profile 混入 `protocol_facts`。
- 将缺失、不确定或无证据的信息写入 `unresolved_facts`。

LLM 参与：

- 当前实现不参与。

### Step 2: Protocol Profile

输入：

- `planning_ir.json`

输出：

- `_step_logs/004_protocol_profile.json`
- `_step_logs/004_protocol_profile_patch_candidate.json`

具体操作：

- 从 planning IR 推导压缩协议画像。
- 推导字段包括：
  - `transport_shape`
  - `interaction_model`
  - `statefulness`
  - `routing_intensity`
  - `resource_intensity`
  - `failure_semantics`
  - `timing_model`
  - `required_capabilities`
  - `required_surface_units`
- 在 `required_capabilities` 和 surface units 中保留 source fact refs、target directive refs 和 evidence refs。
- 校验 enum、capability、traceability。

LLM 参与：

- 强制参与，模式为 `patch_generator`。
- LLM 只能生成 `protocol_profile_patch_candidate`。
- LLM 可建议修正 profile 分类字段和 capability additions。
- LLM 不允许生成 module、file、function、dependency graph 或新协议事实。
- LLM candidate 不合法时带原因重试，最多 3 次；仍失败则退出。

### Step 3: Engineering Constraint Activation

输入：

- `protocol_profile.json`

输出：

- `_step_logs/005_engineering_constraints.json`

具体操作：

- 根据 profile 确定性激活工程约束。
- 当前规则包括：
  - stream transport 需要 incremental decode。
  - timer facts 需要 timer manager。
  - routing/resource facts 需要资源所有权约束。
  - persistent state 需要 session store。
  - protocol error close 行为需要 connection termination policy。
  - Coder-facing specs 需要 canonical ownership。
- 每条 constraint 包含：
  - `constraint_id`
  - `triggered_by`
  - `affected_capabilities`
  - `obligation`
  - `severity`
  - `rationale`
  - `validation_rule`

LLM 参与：

- 不参与。

### Step 4: Architecture Search & Selection

输入：

- `planning_ir.json`
- `protocol_profile.json`
- `engineering_constraints.json`

输出：

- `_step_logs/006_architecture_candidates.json`
- `_step_logs/006_selected_architecture.json`
- `_step_logs/006_llm_architecture_candidates.json`

具体操作：

- 生成模块级 architecture candidates。
- 当前 deterministic baseline 会拆分为：
  - `transport_runtime`
  - `protocol_codec`
  - `semantic_core`
  - `resource_store`
  - `<protocol>_<target_role>_app`
  - 必要时补充 `support_capabilities`
- 校验每个 required capability 被模块覆盖。
- 校验 module capability 必须来自 `protocol_profile.required_capabilities`。
- 校验 constraint references 合法。
- 选择合法 architecture。

LLM 参与：

- 强制参与，模式为 `candidate_generator`。
- LLM 只能生成模块级 architecture candidate。
- LLM 不允许生成 file、function、call graph、include graph 或代码。
- LLM candidate 不合法时带原因重试，最多 3 次；仍失败则退出。

### Step 5: Implementation Plan Synthesis

输入：

- `planning_ir.json`
- `protocol_profile.json`
- `engineering_constraints.json`
- `selected_architecture.json`

输出：

- `_step_logs/007_implementation_plan.json`
- `_step_logs/007_implementation_plan_candidate.json`

具体操作：

- 生成 `module_contracts`。
- 生成 C source/header file layout。
- 生成 function contracts。
- 根据 target-scope surface units 生成 handler matrix。
- 从 `message_model.message_or_command_entries[*].fields[*]` 派生 wire field coverage。
- 生成：
  - `canonical_types`
  - `state_design`
  - `handler_matrix`
  - `resource_lifecycle`
  - `error_strategy`
  - `wire_mapping_table`
  - `access_path_table`
  - `test_plan`
  - `unresolved_questions`
- 将 parser/serializer function contracts 与 wire fields 建立覆盖关系。
- 校验 capability coverage、handler coverage、wire field coverage、state/access/dependency references。

LLM 参与：

- 强制参与，当前模式为 `primary_planner`。
- LLM 输出必须是完整 `implementation_plan/v1` candidate。
- LLM 可以提出 module/file/function contract 级工程设计。
- LLM 不允许输出代码。
- LLM 不允许引入不存在的协议事实。
- LLM 不允许绕过 dependency graph derivation。
- LLM candidate 不合法时带原因重试，最多 3 次；仍失败则退出。

### Step 6: Dependency Derivation & Validation

输入：

- `implementation_plan.json`

输出：

- `_step_logs/008_dependency_validation_report.json`
- `implementation_plan.dependency_graph`

具体操作：

- 从 `file_layout.files[*].imports_allowed` 派生 file/module dependency edges。
- 从 `function_contracts[*].calls_allowed` 派生 function/file/module dependency edges。
- 校验 dependency graph 不引用不存在的 module/file/function。
- 当前最终 dependency graph 由规则层派生，不由 LLM 直接生成。

LLM 参与：

- 当前实现不参与。

### Step 7: Spec Blueprint Lowering

输入：

- `implementation_plan.json`

输出：

- `_step_logs/010_spec_blueprint.json`

具体操作：

- 将 implementation plan 机械展开为 spec blueprint。
- 归一化 module/file/function trace ids。
- 透传 constraints、traceability、wire mapping、access path、dependency graph。
- 校验 blueprint 没有新增 module/file/function。

LLM 参与：

- 不参与。
- 该阶段禁止 LLM。
- 信息缺失或 blueprint 偷增工程语义时直接失败。

### Step 8: Coder-Compatible Specs Compilation

输入：

- `spec_blueprint.json`

输出：

- `spec_bundle/`
- `coder_manifest.json`

具体操作：

- 从 spec blueprint 确定性编译当前 Coder Agent 可读取的 specs。
- 生成一个 `PROTOCOL_MODULE_SPEC`。
- 生成多个 `FILE_SPEC`。
- 生成多个 `FUNCTION_SPEC`。
- 生成 `coder_manifest.json` 作为索引和审计文件。
- 调用当前 Coder loader 做兼容性验证：
  - `agent.coder.specs.load_spec_bundle_from_root()`

LLM 参与：

- 不参与。
- 该阶段禁止 LLM。
- specs 内容必须来自 spec blueprint，不能由 compiler 新增工程语义。

### Step 9: Planning Validation Report

输入：

- 所有已生成 artifacts
- 所有 diagnostics

输出：

- `_step_logs/014_planning_validation_report.json`

具体操作：

- 汇总 pipeline 结果。
- 汇总 facts compatibility status。
- 汇总 coder compatibility status。
- 汇总 diagnostics。
- 记录 artifact paths。

LLM 参与：

- 当前实现不参与。

## 输出目录

如果未指定 `--output-dir`，默认输出到：

```text
agent/planning/out/<protocol>/<target_slug>/<timestamp>/
```

一次成功运行会生成：

```text
<run>/
├── _agent_logs/
│   └── 000_stage_events.log
├── _step_logs/
│   ├── 000_planning_run_manifest.json
│   ├── 003_planning_ir.json
│   ├── 004_protocol_profile.json
│   ├── 005_engineering_constraints.json
│   ├── 006_architecture_candidates.json
│   ├── 006_selected_architecture.json
│   ├── 007_implementation_plan.json
│   ├── 008_dependency_validation_report.json
│   ├── 010_spec_blueprint.json
│   ├── 013_token_usage_summary.json
│   └── 014_planning_validation_report.json
├── coder_manifest.json
└── spec_bundle/
```

启用 LLM 时，还可能生成：

```text
<run>/_step_logs/004_protocol_profile_patch_candidate.json
<run>/_step_logs/006_llm_architecture_candidates.json
<run>/_step_logs/007_implementation_plan_candidate.json
```

运行时会在控制台输出类似 Coder Agent 的阶段日志，例如：

```text
[agent.planning] stage=protocol_profile build start
[agent.planning] stage=architecture llm_attempt=1 prompt=architecture_candidate_prompt start
[agent.planning] stage=architecture llm_attempt=1 accepted
```

每次 LLM attempt 的 metadata 和 raw response 会写入 `_agent_logs/`，用于排查 JSON 解析失败、输出截断和 validation rejection。

`013_token_usage_summary.json` 会统计所有 LLM attempt 的 token 用量。默认 no-LLM 模式下 total 为 0；启用 LLM 时会按阶段汇总：

- `protocol_profile`
- `architecture`
- `implementation_plan`

每个阶段包含 prompt tokens、completion tokens、total tokens、attempt 数、accepted attempt 数和是否触达 completion limit。

## 执行命令

以下命令均在仓库根目录 `/home/ljf/SpecForge` 下运行。

### 查看帮助

```bash
python3 -m agent planning --help
```

```bash
python3 -m agent planning plan --help
```

### 验证输入

```bash
python3 -m agent planning validate \
  --facts agent/facts/gold_facts/mqtt_min/protocol_facts.json \
  --target-profile agent/planning/planning_target_profile_mqtt.json
```

指定输出目录：

```bash
python3 -m agent planning validate \
  --facts agent/facts/gold_facts/mqtt_min/protocol_facts.json \
  --target-profile agent/planning/planning_target_profile_mqtt.json \
  --output-dir /tmp/specforge_planning_validate
```

### 运行 Planning Agent

指定输出目录：

```bash
python3 -m agent planning plan \
  --facts agent/facts/gold_facts/mqtt_min/protocol_facts.json \
  --target-profile agent/planning/planning_target_profile_mqtt.json \
  --output-dir /tmp/specforge_planning_smoke
```

运行规划：

```bash
python3 -m agent planning plan \
  --facts agent/facts/gold_facts/mqtt_min/protocol_facts.json \
  --target-profile agent/planning/planning_target_profile_mqtt.json
```

运行需要环境变量 `ALI_API`。已接入 LLM 的阶段必须成功获得合法 LLM candidate/patch；无法使用 LLM 或三次重试仍不合法时，pipeline 直接失败。

### 验证已有输出目录

```bash
python3 -m agent planning verify \
  --output-dir /tmp/specforge_planning_smoke
```

### 与参考 Coder specs 做回归比较

该命令不要求两个 spec bundle 完全一致，只检查两者都能被当前 Coder loader 读取，并报告模块/协议名等关键差异。

```bash
python3 -m agent planning compare \
  --output-dir /tmp/specforge_planning_smoke \
  --reference-spec-root specs-example/mqtt_specs
```

### 运行测试

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest \
  agent.planning.tests.test_preflight \
  agent.planning.tests.test_compatibility_discovery \
  agent.planning.tests.test_validators
```
