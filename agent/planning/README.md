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
- Protocol Profile 阶段最多重试 3 次；仍失败则报错退出。
- Architecture Search 每轮并发请求 3 个候选策略；高温轮失败后进入低温轮，仍无合法候选则报错退出。
- Implementation Plan Synthesis 使用 staged hybrid 模式：LLM 只生成当前子步骤 candidate/patch；不合法时使用该子步骤的 deterministic fallback，并继续由 validator/merger 审核。

每个 LLM stage 的 temperature、top_p、max_completion_tokens、max_retries 和 enable_thinking 可以在 `agent/planning/config.py` 的 `default_llm_stage_configs()` 中集中调整。可用 stage key 包括 `protocol_profile`、`architecture_candidate_high_variance`、`architecture_candidate_low_variance`、`architecture_ranking`、`implementation_plan_5_1` 到 `implementation_plan_5_6`（含 `implementation_plan_5_4a` 到 `implementation_plan_5_4g`）。

## 中间阶段续跑

`plan` 支持从顶层阶段续跑：

```bash
python3 -m agent.planning plan \
  --facts <protocol_facts.json> \
  --target-profile <target_profile.json> \
  --resume-from-stage architecture
```

`--resume-from-stage` 可选值：

- `planning_ir`
- `protocol_profile`
- `engineering_constraints`
- `architecture`
- `implementation_plan`
- `implementation_plan_5_1`（别名：`5.1` / `implementation_plan_skeleton`）
- `implementation_plan_5_2`（别名：`5.2` / `core_design`）
- `implementation_plan_5_3`（别名：`5.3` / `module_artifacts`）
- `implementation_plan_5_4a`（别名：`5.4a` / `type_inventory`）
- `implementation_plan_5_4b`（别名：`5.4b` / `function_inventory`）
- `implementation_plan_5_4c`（别名：`5.4c` / `function_signatures`）
- `implementation_plan_5_4d`（别名：`5.4d` / `function_behavior`）
- `implementation_plan_5_4e`（别名：`5.4e` / `wire_access_binding`）
- `implementation_plan_5_4f`（别名：`5.4f` / `calls_allowed`）
- `implementation_plan_5_5`（别名：`5.5` / `file_layout`）
- `implementation_plan_5_4g`（别名：`5.4g` / `runtime_entrypoint`）
- `implementation_plan_5_6`（别名：`5.6` / `dependency_repair`）
- `spec_blueprint`
- `specs_compile`

启用续跑时，Planning Agent 会在当前 facts/target 对应的默认输出根中寻找最近一次 run，校验指定阶段之前所需的中间产物、输入文件 hash 和 compatibility versions，然后把继承产物写入新的 run 目录。指定阶段本身会重新执行；`implementation_plan_5_x` 会继承并 merge 之前的 Step 5 子阶段 artifact，然后从指定子阶段继续。

也可以显式指定继承来源目录：

```bash
python3 -m agent.planning plan \
  --facts <protocol_facts.json> \
  --target-profile <target_profile.json> \
  --resume-from-stage 5.4d \
  --resume-source-dir /home/ljf/SpecForge/agent/planning/out/mqtt/broker__c__linux_epoll__minimum_v1/20260522_112210_186032
```

`--resume-source-dir` 必须和 `--resume-from-stage` 一起使用，目录必须包含 `_step_logs/`，并且 `_step_logs/` 中必须有指定续跑阶段之前所需的 artifact。该目录仍会经过输入 hash、compatibility versions 和 artifact validation 校验。

`plan` 也支持在指定阶段完成后正常停止：

```bash
python3 -m agent.planning plan \
  --facts <protocol_facts.json> \
  --target-profile <target_profile.json> \
  --stop-after-stage architecture
```

`--stop-after-stage` 接受与 `--resume-from-stage` 相同的阶段名和别名。停止 run 会写入已完成阶段的 artifact、`013_token_usage_summary.json`、`014_planning_validation_report.json` 和 manifest，manifest status 为 `stopped`。

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

- 创建 `ArtifactStore` 并初始化 run 目录、`_step_logs/` 与 `_agent_logs/`。
- 校验 `protocol_facts.json` 与 `target_profile.json` 路径存在。
- 校验 target profile 基础字段，并限制当前 coder-compatible compiler 只接受 `language=C`。
- 写入初始 manifest，记录输入路径、文件 hash、LLM 配置、prompt registry 版本、compatibility 版本、运行状态和 artifact path map。
- 若 preflight 已有 error，更新 manifest 为 failed 并直接退出；否则进入 target profile load。

LLM 参与：

- 不参与。

### Step 1: Facts Input Adapter / Canonical Planning IR

输入：

- `protocol_facts.json`
- `target_profile.json`

输出：

- `_step_logs/003_planning_ir.json`

具体操作：

- 加载 target profile，并转换为独立的 target directives namespace。
- 兼容读取 Facts Agent 当前输出格式 `protocol_facts/v2alpha1`。
- 校验 facts 顶层结构、关键 section 和协议 meta。
- 将 `evidence_index[]` 转为 evidence map，用于后续 traceability 压缩引用。
- 为协议事实生成稳定的 path-based `fact_id`。
- 建立 `normalization_index`：
  - `fact_id_by_path`
  - `evidence_refs_by_fact_id`
  - `field_id_by_message_and_name`
- 将 target directives 写入 `planning_ir.target_directives`，但不混入 `protocol_facts`。
- 将 facts 中的 open questions 和缺失、不确定、无证据的信息归一化写入 `unresolved_facts`。
- 写入 `_step_logs/003_planning_ir.json`，并运行 `validate_planning_ir`。

LLM 参与：

- 当前实现不参与。

### Step 2: Protocol Profile

输入：

- `planning_ir.json`

输出：

- `_step_logs/004_protocol_profile.json`
- `_step_logs/004_protocol_profile_patch_candidate.json`

具体操作：

- 先由规则层从 planning IR 推导 baseline protocol profile。
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
- 构造 profile patch prompt，允许 LLM 只提交 `protocol_profile_patch_candidate`。
- 对 LLM patch 做确定性校验，禁止新增协议事实、非法 capability、非法 surface 或越权字段。
- 将合法 patch 应用到 baseline profile；若 patch 后 profile 仍不合法，则把失败原因带入下一次 prompt。
- 写入 patch candidate 与最终 `_step_logs/004_protocol_profile.json`。
- 校验 enum、capability 覆盖和 traceability。

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

- 根据 protocol profile 确定性激活工程约束。
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
- 写入 `_step_logs/005_engineering_constraints.json`，并运行 `validate_constraints`。

LLM 参与：

- 不参与。

### Step 4: Architecture Search & Selection

输入：

- `planning_ir.json`
- `protocol_profile.json`
- `engineering_constraints.json`

输出：

- `_step_logs/006_architecture_context.json`
- `_step_logs/006_architecture_candidates.json`
- `_step_logs/006_architecture_ranking.json`
- `_step_logs/006_selected_architecture.json`

具体操作：

- 构造 compact `architecture_context`，只保留协议摘要、required capability ids、surface units、capability group hints、压缩 trace refs 和 engineering constraints。
- 以并发方式请求 3 个 strategy 的 LLM candidate：
  - `capability_clustered`
  - `layered_runtime_codec_semantic`
  - `minimal_scope`
- 第一轮使用高温候选；若没有任何合法 candidate，再发起低温 retry 轮。
- 对每个 LLM candidate 统一补齐 generation metadata：
  - strategy
  - generation request id
  - generation mode
- 在进入校验前强制清空 `module_graph_hints` 与所有 module 的 `dependency_hints`。
- 校验每个 required capability 被至少一个 module 覆盖。
- 校验 module capability 必须来自 `protocol_profile.required_capabilities`。
- 校验 constraint references 合法。
- 校验 architecture 阶段不携带 dependency hints；模块依赖关系留到 Implementation Plan Synthesis 的 Module Contract Planning 中通过 `imports_allowed` / `calls_allowed` 规划。
- 汇总所有合法 candidates，写入 `_step_logs/006_architecture_candidates.json`。
- 调用 LLM ranking prompt 对合法 candidates 打分和选择；ranking 不合法时使用 deterministic ranking fallback。
- 写入 `_step_logs/006_architecture_ranking.json` 和 `_step_logs/006_selected_architecture.json`。

LLM 参与：

- 强制参与，模式为 `candidate_generator`。
- LLM 只能生成模块级 architecture candidate 和 ranking。
- LLM 不允许生成 dependency hints、file、function、call graph、include graph 或代码。
- 每轮并发请求 3 个候选策略：`capability_clustered`、`layered_runtime_codec_semantic`、`minimal_scope`；高温轮全部失败后进入低温重试轮，仍失败则退出。

### Step 5: Implementation Plan Synthesis

输入：

- `planning_ir.json`
- `protocol_profile.json`
- `engineering_constraints.json`
- `selected_architecture.json`

输出：

- `_step_logs/007_implementation_plan.json`
- `_step_logs/007_5_1_plan_skeleton.json`
- `_step_logs/007_5_2_core_design_candidate.json`
- `_validation_reports/007_5_2_core_design_validation_report.json`
- `_step_logs/007_5_3_module_artifacts_candidate.json`
- `_validation_reports/007_5_3_module_artifacts_validation_report.json`
- `_step_logs/007_5_4a_type_inventory_candidate.json`
- `_validation_reports/007_5_4a_type_inventory_validation_report.json`
- `_step_logs/007_5_4b_function_inventory_candidate.json`
- `_validation_reports/007_5_4b_function_inventory_validation_report.json`
- `_step_logs/007_5_4c_function_signature_patch.json`
- `_validation_reports/007_5_4c_function_signature_validation_report.json`
- `_step_logs/007_5_4d_function_behavior_contract_patch.json`
- `_validation_reports/007_5_4d_function_behavior_validation_report.json`
- `_step_logs/007_5_4e_wire_access_binding_patch.json`
- `_validation_reports/007_5_4e_wire_access_binding_validation_report.json`
- `_step_logs/007_5_4f_calls_allowed_candidate.json`
- `_validation_reports/007_5_4f_calls_allowed_validation_report.json`
- `_step_logs/007_5_5_file_layout_candidate.json`
- `_validation_reports/007_5_5_file_layout_validation_report.json`
- `_step_logs/007_5_4g_runtime_entrypoint_candidate.json`
- `_validation_reports/007_5_4g_runtime_entrypoint_validation_report.json`
- `_step_logs/007_5_6_dependency_repair_patch.json`（仅 dependency validation 失败时）
- `_validation_reports/007_5_6_dependency_repair_validation_report.json`（仅 dependency validation 失败时）

具体操作：

- 5.1 由规则层生成 `implementation_plan/v1` skeleton：
  - 固定 `source_artifacts` / `source_artifact_refs`。
  - 建立 `id_namespace`，包括 module、capability、constraint、field、function/file id pattern。
  - 建立 `validation_targets`，明确 capability coverage、handler coverage、wire field coverage、dependency derivation only 和 blueprint no-new-semantics。
  - 初始化空 `module_artifacts`、core design、function contracts、file layout、wire/access mapping 和 `dependency_graph=null`。
  - 建立 `deterministic_indexes`，供后续子步骤 validator 和 fallback 使用。

- 5.2 生成 core design candidate：
  - `canonical_types`
  - `state_design`
  - `handler_matrix`
  - `resource_lifecycle`
  - `error_strategy`
  - `test_plan_seed`
  - 若 LLM candidate 不合法，使用 `fallback_core_design`。

- 5.3 生成 module artifacts candidate：
  - 从 selected architecture 和 accepted core design 派生 module artifact seeds。
  - 规划每个 module 的 C-facing `TYPE` / `FUNC` artifact inventory 和 generation order。
  - 将合法 candidate merge 为最终 `module_artifacts`。
  - 若 LLM candidate 不合法，使用 `fallback_module_artifacts`。

**有多少模块就调用多少次llm，每次的重试上限是 3**
- 5.4a 按 module 逐个生成 type inventory：
  - 覆盖 5.3 `TYPE` artifacts，并可补充 internal state、config、callback、event、view、owned buffer、result、enum、bitflag 等 API 边界所需类型。
  - 写入 `type_inventory`，public/public_header 类型同步到 `canonical_types`，供 signature 与 file/spec lowering 复用。
  - pointer/string/buffer 字段必须说明 ownership/lifetime；owned 类型必须说明 free/destroy 路径。
  - 每个 module candidate 独立校验、独立 fallback、逐步 merge 到 draft。

- 5.4b 按 module 逐个生成 function inventory：
  - 只允许在已存在 module 内创建 function id。
  - 覆盖 parser、serializer、handler、resource lifecycle、public API 等 function kind。
  - 优先复用 5.4a 的 type inventory，不为 `TYPE` artifacts 生成函数。
  - `coder_function_type` 只使用 `ALGORITHM` / `EVENT` / `ENTRYPOINT`；handler 不自动等于 `EVENT`。
  - 只有后续 behavior patch 能补齐 coder EVENT 所需 trigger/precondition/state_change/response/event_type 等字段时，才保留 `EVENT`，否则 lowering 为 `ALGORITHM`。
  - 每个 module candidate 独立校验、独立 fallback、逐步 merge 到 draft。

- 5.4c 按 module 逐个补全 C signature；module 内 function 数量超过 8 时按 batch=8 分批：
  - 写入 `signature` 与 `signature_dependencies`。
  - 不允许修改 5.4b 的函数集合、函数名或 API surface。
  - `type_ref` 只能引用 canonical `type_ids` 或 C/POSIX/network `system_type_ids`，禁止把 state/message/field id 当作 type。
  - 参数 `ownership` 使用 coder 可识别枚举：`BORROWED`、`OWNED`、`OWNED_BY_CALLER`、`TRANSFER`、`SHARED`、`UNKNOWN`。
  - 参数同时保留 planning 用的 `direction` 与 `passing_mode`，用于区分 by-value、pointer、out-param 等 C 调用形态。
  - 不合法时使用 deterministic signature fallback。

- 5.4d 按 module 逐个补全 behavior/internal dependency contract；module 内 function 数量超过 4 时按 batch=4 分批：
  - 写入 `behavior_contract`、`error_behavior`、`state_access`、`resource_access`、`internal_type_refs`、`service_requirements`。
  - `behavior_contract` 显式保存 preconditions、postconditions、idempotent、thread_safety；这些字段由 Step 8 lowering 为 coder `CONTRACT`。
  - `logic_kind=EVENT` 时必须同时提供完整 `event_contract`；否则 merger/fallback 保守降级为 `LOGIC`。
  - `service_requirements` 按 `external_runtime_service` / `cross_module_service` / `owned_responsibility` 分类；只有跨 module 服务进入 5.4f call planning。
  - 不允许修改 signature，也不直接生成 call edge。
  - 旧 `input_contract` / `output_contract` 由 5.4c signature 与 5.4d behavior 兼容生成。
  - 不合法时使用 deterministic behavior fallback。

- 5.4e 生成 wire/access binding patch：
  - 将 wire fields 绑定到 parser/serializer/handler function。
  - 填充 `wire_mapping_table` 与 `access_path_table`。
  - `access_path_entries` 必须包含可 lowering 为 coder `PATH/TYPE/ROLE` 的 `path`、`c_type`、`role`。
  - `wire_mapping_entries` 必须包含可 lowering 为 coder `PACKET/WIRE_FIELD/STRATEGY` 的字段；不能只保存 planning id。
  - 不允许新增 function 或修改 signature。
  - 不合法时使用 deterministic wire/access fallback。

- 5.4f 按 module 逐个生成 `calls_allowed` candidate；module 内 function 数量超过 4 时按 batch=4 分批：
  - 将 5.4d 的 `cross_module_service` requirements 解析为 concrete call edges。
  - 只允许引用已存在 function ids。
  - batch candidate 只覆盖当前 caller function 集合，聚合后再做全局 coverage 与 cycle 校验。
  - 校验跨 module 调用不能违反 selected architecture policy。
  - 禁止 self-call 和 prohibited cycle；无法解析的 service requirement 写入 unresolved。
  - 不合法时使用 deterministic calls fallback。

**根据已有函数集合进行文件分配**
- 5.5 在函数全集稳定后生成 C `source_header_pair` file layout：
  - 每个 file 只能归属已存在 module。
  - 每个 file item 是“一源一头 FILE_SPEC 单元”，`file_id` 使用源文件无后缀路径，例如 `file:mqtt/transport_runtime/transport_runtime`。
  - 只能分配 existing functions。
  - 填充 `source_path`、`header_path`、`exports`、`implements`、`imports_allowed` 和 traceability；`imports_allowed` 只引用其他 FILE_SPEC ids，不引用 `.h` / `.c` 或 `header:*`。
  - 不合法时使用 deterministic file layout fallback。

- 5.6 由规则层从 `signature_dependencies`、`state/resource access`、`calls_allowed` 和 `imports_allowed` 派生最终 `dependency_graph`。
- 若 dependency validation 失败，执行一次 LLM dependency repair patch；repair 后仍失败则使用 deterministic dependency fallback。
- 写入最终 `_step_logs/007_implementation_plan.json`，再运行 full implementation plan validator 与 dependency graph validator。
- 每个子步骤的 candidate/patch 都会写入对应 validation report；只有通过 stage validator 的输出才会被 merger 写入 draft。

LLM 参与：

- Hybrid staged candidate/patch generator。
- LLM 不允许返回完整 `implementation_plan/v1`。
- LLM 只能提出当前子步骤允许的 candidate/patch。
- LLM 不允许输出代码。
- LLM 不允许引入不存在的协议事实。
- LLM 不允许直接生成最终 `dependency_graph`。
- LLM candidate/patch 不合法时带原因重试，最多 3 次；仍失败则使用该子步骤 deterministic fallback。

### Step 6: Dependency Derivation & Validation

输入：

- `implementation_plan.json`

输出：

- `_validation_reports/008_dependency_validation_report.json`
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
- 通过 deterministic lowering 层把 planning IR 字段转换为 coder spec dialect；compiler 不允许把 planning-only 字段直接塞进 strict specs。
- 生成一个 `PROTOCOL_MODULE_SPEC`。
- 生成多个 `FILE_SPEC`。
- 生成多个 `FUNCTION_SPEC`。
- 生成 `coder_manifest.json` 作为索引和审计文件。
- 可生成非 `*_spec.json` sidecar，例如 `planning_traceability.json`、`planning_decisions.json`、`planning_ir_refs.json`，用于保存 traceability、capability/state/call planning 信息。
- strict specs 中不得出现 coder schema 不允许的顶层字段，例如 `TRACEABILITY`、`CAPABILITY_IDS`、`STATE_ACCESS`、`CALLS_ALLOWED`。
- 先用 `specs-example/specs_schema/*.json` 做 strict JSON Schema validation，再调用当前 Coder loader 做兼容性验证：
  - `agent.planning.validators.coder_schema.validate_coder_spec_bundle_against_schema()`
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

- `_validation_reports/014_planning_validation_report.json`

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
│   ├── 006_architecture_context.json
│   ├── 006_architecture_candidates.json
│   ├── 006_architecture_ranking.json
│   ├── 006_selected_architecture.json
│   ├── 007_5_1_plan_skeleton.json
│   ├── 007_5_2_core_design_candidate.json
│   ├── 007_5_3_module_artifacts_candidate.json
│   ├── 007_5_4a_type_inventory_candidate.json
│   ├── 007_5_4b_function_inventory_candidate.json
│   ├── 007_5_4c_function_signature_patch.json
│   ├── 007_5_4d_function_behavior_contract_patch.json
│   ├── 007_5_4e_wire_access_binding_patch.json
│   ├── 007_5_4f_calls_allowed_candidate.json
│   ├── 007_5_5_file_layout_candidate.json
│   ├── 007_5_4g_runtime_entrypoint_candidate.json
│   ├── 007_implementation_plan.json
│   ├── 010_spec_blueprint.json
│   └── 013_token_usage_summary.json
├── _validation_reports/
│   ├── 007_5_2_core_design_validation_report.json
│   ├── 007_5_3_module_artifacts_validation_report.json
│   ├── 007_5_4a_type_inventory_validation_report.json
│   ├── 007_5_4b_function_inventory_validation_report.json
│   ├── 007_5_4c_function_signature_validation_report.json
│   ├── 007_5_4d_function_behavior_validation_report.json
│   ├── 007_5_4e_wire_access_binding_validation_report.json
│   ├── 007_5_4f_calls_allowed_validation_report.json
│   ├── 007_5_5_file_layout_validation_report.json
│   ├── 007_5_4g_runtime_entrypoint_validation_report.json
│   ├── 008_dependency_validation_report.json
│   └── 014_planning_validation_report.json
├── coder_manifest.json
└── spec_bundle/
```

启用 LLM 时，还可能生成：

```text
<run>/_step_logs/004_protocol_profile_patch_candidate.json
<run>/_step_logs/007_5_6_dependency_repair_patch.json
<run>/_validation_reports/007_5_6_dependency_repair_validation_report.json
```

运行时会在控制台输出类似 Coder Agent 的阶段日志，例如：

```text
[agent.planning] stage=protocol_profile build start
[agent.planning] stage=architecture generation_request=1 strategy=capability_clustered temperature=0.7 start
[agent.planning] stage=implementation_plan substage=5.4a_type_inventory:semantic_core llm_attempt=1 prompt=type_inventory_candidate_prompt start
[agent.planning] stage=implementation_plan substage=5.4a_type_inventory:semantic_core llm_attempt=1 accepted
```

每次 LLM attempt 的 metadata 和 raw response 会写入 `_agent_logs/`，用于排查 JSON 解析失败、输出截断和 validation rejection。

`013_token_usage_summary.json` 会统计所有 LLM attempt 的 token 用量，并按阶段汇总：

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

从中间阶段续跑：

```bash
python3 -m agent planning plan \
  --facts agent/facts/gold_facts/mqtt_min/protocol_facts.json \
  --target-profile agent/planning/planning_target_profile_mqtt.json \
  --resume-from-stage implementation_plan
```

从 Step 5 子阶段续跑：

```bash
python3 -m agent planning plan \
  --facts agent/facts/gold_facts/mqtt_min/protocol_facts.json \
  --target-profile agent/planning/planning_target_profile_mqtt.json \
  --resume-from-stage 5.4d
```

完成 Step 4 架构生成后停止：

```bash
python3 -m agent planning plan \
  --facts agent/facts/gold_facts/mqtt_min/protocol_facts.json \
  --target-profile agent/planning/planning_target_profile_mqtt.json \
  --stop-after-stage architecture
```

续跑并指定本次新 run 的输出目录：

```bash
python3 -m agent planning plan \
  --facts agent/facts/gold_facts/mqtt_min/protocol_facts.json \
  --target-profile agent/planning/planning_target_profile_mqtt.json \
  --resume-from-stage implementation_plan \
  --output-dir /tmp/specforge_planning_resume
```

只跑 5.4a type inventory 阶段：
```bash
python3 -m agent planning plan \
  --facts agent/facts/gold_facts/mqtt_min/protocol_facts.json \
  --target-profile agent/planning/planning_target_profile_mqtt.json \
  --resume-from-stage 5.1 \
  --stop-after-stage 5.4a
```

`--resume-from-stage` 会自动从当前 facts/target 对应的默认输出根中选择最近一次 run 作为继承来源；`--output-dir` 只表示本次新 run 的写入位置，不表示 source run。

如果不想使用自动选择的最近一次 run，可以用 `--resume-source-dir <previous_run_dir>` 明确指定继承来源；该目录必须包含 `_step_logs/`，并通过当前 facts/target 的 hash 和 compatibility 校验。

运行需要环境变量 `ALI_API`。Protocol Profile 和 Architecture 属于 mandatory LLM 阶段，无法获得合法输出会失败退出；Implementation Plan Synthesis 属于 staged hybrid 阶段，子步骤 LLM 输出不合法时使用 deterministic fallback 继续推进。

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
