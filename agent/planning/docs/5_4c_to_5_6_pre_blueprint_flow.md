# 5.4b 到 5.7 的 Pre-Specs 实际运行流程

本文记录当前代码中 Implementation Plan Synthesis 从 `5.4b` 到进入
`Coder Specs Compilation` 之前的实际运行过程。范围从
`implementation_plan_5_4b` 开始，经过 `5.4c`、`5.4d`、`5.4e`、`5.5a`、
`5.5b`、`5.6`、`5.7`，到写出 `007_implementation_plan.json` 与
`008_dependency_validation_report.json` 为止，不包含后续
`compile_spec_bundle()` specs compilation。

## 代码入口与公共机制

主入口是 `PlanningAgent.plan()` 中的 `implementation_plan` 阶段。`5.4b` 之前，
`draft` 已经合并了：

- `5.1` plan skeleton；
- `5.2a` core design；
- `5.2b` module artifacts；
- `5.3` type/data inventory；
- `5.4a` function inventory，并执行过 `reconcile_type_inventory_function_refs()` 与
  `5.4a.1_function_symbol_repair`。

从 `5.4b` 开始，各子阶段主要使用同一个 `stage_candidate()` helper：

1. 构造 stage-specific context。
2. 调用对应 prompt，要求 LLM 返回 JSON candidate 或 patch。
3. 失败时使用 `_retry_messages()` 把 deterministic validator 的 rejection reasons
   追加给下一次 LLM request。
4. 尝试次数来自 `PlanningConfig.llm_max_retries_for(thinking_stage)`，默认配置中
   `5.4b`、`5.4c`、`5.4d`、`5.4e`、`5.5a` 都是 3 次，`5.5b`、`5.6` 是 1 次。
5. 每次 LLM request 的 meta 写入 `_agent_logs`，token usage 写入
   `TokenUsageTracker`。
6. 如果所有 LLM attempts 都失败，使用 deterministic fallback candidate。
7. accepted candidate 或 fallback 写入 artifact；validation report 写入
   `_validation_reports`。
8. 如果最终 candidate 仍有 error diagnostics，diagnostics 会累积到全局
   `diagnostics`，但只有在 implementation plan 阶段末尾统一决定是否失败。

`stage_candidate()` 对普通子阶段没有 JSON-only retry 机制；JSON 解析失败会作为一次
LLM attempt 的 rejection。`5.4a`/`5.4b` controlled inventory 的 JSON retry 机制不用于
`5.4b` 之后。

Resume 行为也在同一个阶段内处理。如果 `resume_from_stage` 晚于某个 substage，
orchestrator 会读取 inherited artifact，调用对应 validator 校验，然后执行相同的
merge 函数把 inherited candidate 合并回 `draft`。这样后续阶段看到的 `draft` 与一次
完整运行的状态保持一致。

## 5.4b Function Signature Planning

### 阶段作用

`5.4b` 把 function inventory 中“需要哪些 functions”的事实，降低为 coder agent 可直接
使用的 C-level API surface。它主要决定每个 function 的 return type、params、
ownership、passing mode 和 signature-level type dependencies，为后续 behavior、
wire binding、call planning 与 file layout 提供稳定接口边界。这个阶段不推导协议行为，
只把已知 function responsibility 转换成可实现的函数签名。

### 输入

`5.4b` 的输入是合并到 `draft["function_contracts"]` 中的 function inventory。代码按
module 遍历 `draft["module_artifacts"]`，再筛选属于该 module 的
`function_contracts`。

每个 module 的 functions 被切成 batch：

- batch size 固定为 32；
- 如果 module 没有 functions，仍会创建一个空 batch；
- 每个 batch 的 expected ids 是该 batch 中的 `function_id` 集合。

每个 batch 使用 `build_function_signature_context()` 构造 context，包含：

- `schema_version = function_signature_context/v1`；
- `module_id`；
- `batch.index` 与 `batch.size`；
- 当前 batch 的 compact `functions` summary；
- `required_update_skeleton`，由 function 列表派生，约束必须更新哪些 function；
- `signature_normalization_policy`；
- `module_summary`；
- `signature_type_table`，包含当前 module 与 provider 的 compact type symbol table；
- provider public types；
- `global_public_symbol_names` 与 `signature_style_guide`，用于贴近 example specs 的 C API 风格并避免 public symbol 冲突；
- scoped `legal_id_universe`。

### LLM 输出与校验

prompt 是 `function_signature_patch_prompt`，期望 schema 为
`function_signature_patch/v1`。prompt 明确要求只 patch 当前 batch 的 C signatures 与
`signature_dependencies`，禁止新增 function/module/file，禁止 state、wire、
calls、dependency graph 或 code。prompt 还要求 `signature.raw` 采用 coder specs 风格：
无尾部分号、public API 使用稳定 module-owned symbol、parser/serializer 使用具体
buffer/cursor/out-param 形态，避免无依据的 generic `void*`。

validator 是 `validate_function_signature_patch(candidate, draft, expected_ids)`。也就是
LLM patch 只能覆盖当前 batch 的 expected function ids，且必须满足当前 draft 的合法
type/ref 约束。

validator 前会先运行 `normalize_function_signature_patch()`。它以 deterministic fallback
为 skeleton，丢弃 batch 外 update，补齐缺失 update，并 canonicalize `storage_class/raw`、
param ownership/passing mode、dependency scope、system owner 与 private source dependency。
因此 prompt 重点描述语义签名设计，而不是枚举和 storage class 的机械修补。

### Fallback

如果 LLM attempts 全部失败，调用：

`fallback_function_signatures(draft, module_id, batch, batch_index, batch_size=32)`。

fallback 使用 `_default_signature()` 生成保守 C signature：

- `*_create` 返回 module handle pointer；
- `*_destroy` 返回 `void`，参数为 `self`；
- parser 使用 `const uint8_t* buffer` 与 `size_t length`；
- serializer 使用 `uint8_t* buffer` 与 `size_t capacity`；
- handler 使用 `self` 与 `const void* message`；
- 其他函数默认返回 `int`，参数为 module handle `self`。

fallback 会规范 param ownership 与 passing mode，并生成空
`signature_dependencies` 与 `interface_type_declarations`。

### Merge 与输出

每个 batch accepted patch 通过 `merge_function_signatures()` 立即合并进 `draft`：

- 写入 function 的 `signature`；
- 规范 param `type_ref`、`ownership`、`passing_mode`；
- 写入 `signature_dependencies` 与 `interface_type_declarations`；
- 同步 `input_contract.params` 与 `output_contract.return_type`；
- 把 patch 的 unresolved questions 加入 draft；
- 在 `accepted_stage_artifacts` 中追加 `5.4b_function_signatures`。

每个 batch 的 artifact 使用 suffixed 文件名：

- `_agent_logs/007_5_4b_function_signature_patch__<module>__batch_<n>.json`
- `_validation_reports/007_5_4b_function_signature_validation_report__<module>__batch_<n>.json`

全部 batch 完成后，orchestrator 聚合：

- `schema_version = function_signature_patch/v1`
- `patch_id = patch:function_signatures:all_modules`
- `producer.stage = 5.4b_function_signatures`
- `function_signature_updates`
- `assumptions`
- `unresolved_questions`
- `batch.size = len(function_signature_updates)`

聚合 patch 会再次用全量 function ids 校验，并写入：

- `_step_logs/007_5_4b_function_signature_patch.json`
- `_validation_reports/007_5_4b_function_signature_validation_report.json`

## 5.4c Function Behavior Contract Planning

### 阶段作用

`5.4c` 在 signatures 固定后，为每个 function 补充 implementation-oriented behavior
contract。它把 protocol facts、module responsibilities 与 constraints 转换成输入输出语义、
preconditions、postconditions、state/resource access、error behavior 和 service
requirements。这个阶段的核心价值是让 downstream coder agent 知道每个 function 应该做什么、
不能做什么，以及哪些跨 module 或 runtime 能力需要后续 call planning 解析。

### 输入

`5.4c` 使用已经包含 signatures 的 `draft`。它同样按 module 遍历
`module_artifacts`，筛选该 module 的 `function_contracts`。

batch size 固定为 8；空 module 也会创建空 batch。每个 batch 使用
`build_function_behavior_context()` 构造 context，包含：

- `schema_version = function_behavior_context/v1`；
- `module_id`；
- `batch`；
- 当前 batch 的 `functions`；
- `required_update_skeleton`；
- `service_requirement_policy`；
- `module_summary`；
- `module_state_access_policy`；
- `module_resource_refs`；
- provider public API summary；
- `engineering_constraints`；
- scoped `legal_id_universe`，并加入 constraint ids。

`service_requirement_policy` 的关键约束是：cross-module service 需要显式建模；
provider 自己职责内的事情不应作为 service requirement 发出。

### LLM 输出与校验

prompt 是 `function_behavior_contract_patch_prompt`，期望 schema 为
`function_behavior_contract_patch/v1`。它要求为当前 batch patch：

- concise behavior contracts；
- state/resource access；
- internal type refs；
- service requirements。

prompt 禁止 signature changes、wire/access、callee/calls、dependency graph 与 code。

validator 是
`validate_function_behavior_contract_patch(candidate, draft, constraints, expected_ids)`。

### Fallback

fallback 是：

`fallback_function_behavior(draft, module_id, batch, batch_index, batch_size=8)`。

fallback 为每个 function 生成：

- `contract.input/action/output/preconditions/postconditions/invariants_used/idempotent/thread_safety`；
- 空 `event_contract`；
- module error ids 对应的 `_error_behavior()`；
- 如果 function 属于 state owner module，则添加 state `read_write` access；
- 空 resource/internal type/service requirements；
- `logic_kind = LOGIC`；
- 空 forbidden symbols。

### Merge 与输出

`merge_function_behavior()` 会更新 function：

- `behavior_contract`；
- `event_contract`；
- `state_access`；
- `resource_access`；
- `internal_type_refs`；
- `service_requirements`；
- `error_behavior`，转为 text；
- `preconditions` 与 `postconditions`；
- `logic_kind`：只有 patch 标为 `EVENT` 且 event_contract 完整时才保留 `EVENT`，
  否则降为 `LOGIC`；
- `forbidden_symbols`。

每个 batch 写入：

- `_agent_logs/007_5_4c_function_behavior_contract_patch__<module>__batch_<n>.json`
- `_validation_reports/007_5_4c_function_behavior_validation_report__<module>__batch_<n>.json`

聚合 patch 写入：

- `_step_logs/007_5_4c_function_behavior_contract_patch.json`
- `_validation_reports/007_5_4c_function_behavior_validation_report.json`

## 5.4d Wire Access Binding

### 阶段作用

`5.4d` 把 parser、serializer、handler 等 existing functions 绑定到具体 protocol
messages、wire fields 和 access paths。它的作用是把 protocol facts 中的 message/field
结构连接到 implementation plan 的函数职责上，使 coder agent 能够知道哪些 functions
负责读取、写入或处理哪些 wire-level data。这个阶段不新增 message 或 field，只建立已有
protocol structure 与已有 function contracts 之间的 traceable binding。

### 输入

`5.4d` 是全局单次 patch，不按 module/batch 拆分。它使用
`build_wire_access_binding_context(draft, planning_ir)`，context 包含：

- `schema_version = wire_access_binding_context/v1`；
- `codec_and_handler_functions`：从 accepted function summary 中筛选
  `parser`、`serializer`、`handler`；
- `field_summaries`：来自 `planning_ir`；
- `state_design`；
- `function_summary`；
- `legal_id_universe`，并加入 message ids 与 field ids。

### LLM 输出与校验

prompt 是 `wire_access_binding_patch_prompt`，期望 schema 为
`wire_access_binding_patch/v2`。任务是把现有 functions 绑定到 protocol wire fields 与
access paths。

prompt 禁止新增 function/message/field/state，禁止 calls/dependency graph/code。

validator 是 `validate_wire_access_binding_patch(candidate, draft, planning_ir)`。

### Fallback

fallback 是 `fallback_wire_access_binding(draft, planning_ir)`。它从
`planning_ir` 的 wire fields 与当前 function contracts 中的 parser/serializer 进行启发式绑定：

- 根据 `covers_field_ids`、`covers_message_ids` 和 function name/purpose 打分选择
  parser 或 serializer；
- 为 parse/serialize 方向生成 `wire_mapping_entries`；
- 生成 `access_path_entries`；
- 根据字段文本启发式选择 C type，例如 payload/bytes -> `uint8_t*`，
  string/text/topic -> `char*`，length/port -> `uint16_t`；
- 生成 `function_binding_updates`。

### Merge 与输出

`merge_wire_access_binding()` 会：

- 由 patch 的 `wire_mapping_entries` 生成 `draft["wire_mapping_table"]`；
- 由 `access_path_entries` 生成 `draft["access_path_table"]`；
- 按 `function_binding_updates` 写回每个 function 的 `wire_mapping` 与
  `access_paths`；
- 合并 unresolved questions；
- 追加 accepted stage artifact。

输出 artifact：

- `_step_logs/007_5_4d_function_wire_access_binding_patch.json`
- `_validation_reports/007_5_4d_function_wire_access_binding_validation_report.json`

## 5.4e Call Contract Planning

### 阶段作用

`5.4e` 把 `5.4c` 中抽象的 `service_requirements` 解析成允许的 concrete call edges。
它决定 caller 可以调用哪些 callee，并把跨 module service、external runtime service 或
module-internal helper 需求转成 `calls_allowed` 与 `call_contracts`。这个阶段生成的是
dependency graph 的输入之一，而不是最终 dependency graph；因此它必须保守处理 unresolved
service requirements，避免为了连通调用关系而发明不存在的 functions。

### 输入

`5.4e` 在 signatures、behavior contracts、wire binding 都稳定后运行。它按 module
遍历 functions，batch size 固定为 16。

每个 batch 先从 function 的 `service_requirements` 中收集 expected service ids，只包括：

- `cross_module_service`
- `external_runtime_service`

然后构造 `build_calls_allowed_context()`，包含：

- `schema_version = calls_allowed_context/v1`；
- `module_id`；
- `batch`；
- 全量 `function_summary`；
- 当前 scoped functions 的 `service_requirements`；
- 当前 module 可调用的 `callable_functions`；
- `required_call_update_caller_ids`；
- `required_call_update_skeleton`；
- `expected_cross_module_service_requirements`；
- `candidate_provider_functions`；
- `normalization_policy`；
- `module_artifacts`；
- `architecture_policy`，其中 `forbidden_cycles = True`；
- `legal_id_universe`。

### LLM 输出与校验

prompt 是 `calls_allowed_candidate_prompt`，期望 schema 为
`calls_allowed_candidate/v2`。任务是把 service requirements 解析成具体
`calls_allowed` edges。

prompt 禁止新增 function/file/module，禁止 imports/include graph/dependency graph/code。

validator 是：

`validate_calls_allowed_candidate(candidate, draft, selected_architecture,
expected_caller_ids, expected_service_requirement_ids, callable_function_ids)`。

这会把当前 batch 的 caller ids、service ids 和 callable function ids 作为局部硬约束。

validator 前会先运行 `normalize_calls_allowed_candidate()`。它以 deterministic skeleton
保证每个 expected caller 都有 update，丢弃 batch 外 caller、unknown/self/private
cross-module/out-of-callable callee，归一 cleanup binding，并把未 resolved 的 expected
service ids 自动放入 `unresolved_service_requirements`。

### Fallback

fallback 是 `fallback_calls_allowed(draft, batch, batch_index, batch_size=16)`。它非常保守：

- handler 允许调用同 module 的 state_machine/resource_lifecycle helpers；
- public_api 允许调用同 module 的 parser 与 handler；
- parser/serializer 默认不添加 calls；
- 无法解析的 service requirement ids 放入 `unresolved_service_requirements`。

### Merge 与输出

每个 batch 的 candidate 合并到 aggregate：

- `call_updates`
- `unresolved_service_requirements`
- `assumptions`
- `unresolved_questions`

聚合阶段会对 `unresolved_service_requirements` 去重排序，然后用全局 validator 再校验。

`merge_calls_allowed()` 会写回每个 function：

- `calls_allowed`：只保留 callee function ids；
- `call_contracts`：保留完整 call edge contract。

每个 batch 输出：

- `_agent_logs/007_5_4e_function_call_contracts_candidate__<module>__batch_<n>.json`
- `_validation_reports/007_5_4e_function_call_contracts_validation_report__<module>__batch_<n>.json`

聚合输出：

- `_step_logs/007_5_4e_function_call_contracts_candidate.json`
- `_validation_reports/007_5_4e_function_call_contracts_validation_report.json`

## 5.5a File Layout Planning

### 阶段作用

`5.5a` 把 module artifacts 与 function contracts 映射到 concrete source/header files。
它决定每个 file 的 path、responsibility、exports、implements 与 allowed imports，并为
每个 function 写入 file assignment。这个阶段让 protocol specs 从“模块与函数层面的工程计划”
进一步变成 coder agent 可落地生成代码的文件结构，同时为 `5.6` 的 dependency derivation
提供 include/import 边界。

### 输入

`5.5a` 是全局单次 candidate。`build_file_layout_context(draft, planning_ir, constraints)`
构造 context：

- `schema_version = file_layout_context/v1`；
- `module_artifacts`；
- `function_summary`；
- `target_language`，来自 target directives，默认 C；
- `layout_policy = source_header_pair`；
- `engineering_constraints`；
- `legal_id_universe`。

### LLM 输出与校验

prompt 是 `file_layout_candidate_prompt`，期望 schema 为 `file_layout_candidate/v2`。
任务是规划 C `source_header_pair` file layout，并把现有 functions 分配到 files。

prompt 禁止新增 function id/name、implementation details、call graph、dependency graph
和 code。

validator 是 `validate_file_layout_candidate(candidate, draft)`。

### Fallback

fallback 是 `fallback_file_layout(draft)`。它按 module 生成一对 C source/header：

- source path：`<protocol>/<module_id>/<module_id>.c`
- header path：`<protocol>/<module_id>/<module_id>.h`
- file id：`file:<protocol>/<module_id>/<module_id>`
- public functions 写入 `exports_function_ids`；
- module functions 全部写入 `implements_function_ids`；
- 根据 function signature dependencies 生成 `imports_allowed`。

同时为每个 function 生成 `function_file_assignments`。当前代码中 public function 的
`declaration_file_id` 使用同一个 file id；非 public function 为空。

### Merge 与输出

`merge_file_layout()` 会：

- 写入 `draft["file_layout"]["files"]`；
- 保留 `path/source_path/header_path/responsibility/exports/implements/imports_allowed`；
- 过滤掉非 `file:` import 和 self import；
- 把 function 的 `file_id` 与 `declared_in` 更新为 assignment 中的值；
- 合并 unresolved questions。

输出：

- `_step_logs/007_5_5a_file_layout_candidate.json`
- `_validation_reports/007_5_5a_file_layout_validation_report.json`

## 5.5b Runtime Entrypoint Planning

### 阶段作用

`5.5b` 为生成出来的 protocol implementation 补齐可运行入口。它选择 key flow module，
识别或补齐 create/start/run/destroy lifecycle functions，并规划 `main.c` 中的 source-only
runtime entrypoint。这个阶段不实现 protocol handler、parser 或 serializer 逻辑，只把已有
protocol flow 组织成可启动、可运行、可关闭的 runtime skeleton，保证 coder agent 不只生成
library-style modules，也能生成可执行程序入口。

### 输入

`5.5b` 在 file layout 之后运行，因为它需要把 runtime entrypoint 放入具体 file，并且
可能引用 key flow module 的 primary file。

`build_runtime_entrypoint_context(draft, planning_ir, selected_architecture)` 输出：

- `schema_version = runtime_entrypoint_context/v1`；
- protocol summary；
- selected modules；
- module artifacts；
- `key_flow_module_candidates`；
- existing lifecycle candidates；
- current file layout；
- `default_source_path = main.c`；
- `default_entrypoint_signature = int main(int argc, char** argv)`；
- `runtime_entrypoint_policy`；
- `legal_id_universe`。

`key_flow_module_candidates` 使用启发式打分：module id/name/purpose/capabilities 中出现
broker/server/client/flow/app 加分，`role_composition`、`semantic_dispatch` 加分，
support module 减分，existing public lifecycle functions 也加分。

### LLM 输出与校验

prompt 是 `runtime_entrypoint_candidate_prompt`，期望 schema 为
`runtime_entrypoint_candidate/v1`。任务是规划一个 source-only runtime entrypoint，
复用已有 lifecycle APIs，并把 protocol flow logic 留在 key flow module。

prompt 禁止 protocol handler logic、parser/serializer logic、新 protocol module、
dependency graph、include graph 和 code。

validator 是 `validate_runtime_entrypoint_candidate(candidate, draft)`。

默认 retries 只有 1 次；失败后直接 fallback。

### Fallback

fallback 是 `fallback_runtime_entrypoint(draft)`：

- 选出 key flow module；
- 为 create/start/run/destroy 找已有 lifecycle function id，找不到则使用
  `fn:<module_id>:<action>`；
- source path 固定为 `main.c`；
- entrypoint signature 固定为 `int main(int argc, char** argv)`；
- startup sequence 为 parse_args、create、start、run、destroy。

### Merge 与输出

`merge_runtime_entrypoint()` 会做较多 deterministic lowering-like 修正：

- 校验/修正 `key_flow_module_id`；
- 为 create/start/run/destroy 解析 lifecycle function ids；
- 如果缺少 lifecycle functions，会创建新的 public lifecycle function contracts；
- 如果已有 function 被选中，会强制补齐 public API visibility/export 信息；
- 新增或更新 `fn:<module_id>:runtime_entrypoint_main`，其
  `coder_function_type = ENTRYPOINT`，`visibility = internal`；
- main function 的 `calls_allowed` 指向 create/start/run/destroy；
- 新增或更新 source-only entry file，默认 file id 为 `file:main`，kind 为
  `source_only_entrypoint`；
- entry file 的 `imports_allowed` 指向 key flow module primary file；
- 对 lifecycle identification 的特定 unresolved question 做过滤。

输出：

- `_step_logs/007_5_5b_runtime_entrypoint_candidate.json`
- `_validation_reports/007_5_5b_runtime_entrypoint_validation_report.json`

## 5.6 Dependency Derivation 与 Repair

### 阶段作用

`5.6` 把前面阶段产生的 calls、file assignments、imports 与 visibility 降低成 final
dependency graph，并对 invalid dependency inputs 做受限 repair。它是进入
`Coder Specs Compilation` 前的结构一致性关口：如果 call graph、file layout 或 import
policy 之间存在冲突，`5.6` 会优先通过局部 repair 修正输入；仍无法修复时才使用 deterministic
last resort 移除无效 dependency inputs 并记录 unresolved questions。这个阶段不让 LLM
直接编造最终 dependency graph，而是保持 dependency graph 由 deterministic derivation 生成。

### 初次 dependency graph derivation

`5.6` 不先要求 LLM 生成 dependency graph。orchestrator 先调用：

`implementation_plan = finalize_dependency_graph(draft)`。

`finalize_dependency_graph()` 只是：

- deep copy draft；
- 写入 `dependency_graph = derive_dependency_graph(result)`；
- 移除 transient `accepted_stage_artifacts`。

随后运行 `validate_dependency_graph(implementation_plan)`。

### 条件触发 LLM repair

只有同时满足以下条件时才进入 LLM repair：

- 当前运行应该执行 `implementation_plan_5_6`；
- 初次 `validate_dependency_graph()` 有 error diagnostics。

如果 dependency graph 初次校验通过，则不会生成
`007_5_6_dependency_repair_patch.json`，也不会生成对应 validation report。

### Repair 输入

`build_dependency_repair_context(draft, dependency_errors)` 构造：

- `schema_version = dependency_repair_context/v1`；
- current files；
- function summary；
- dependency errors；
- allowed repair operations：
  `remove_call_edge`、`adjust_imports_allowed`、`lower_visibility`、
  `change_function_file_assignment`、`mark_unresolved`；
- `legal_id_universe`。

`dependency_errors` 由 diagnostics 转换而来，每条包含 code、path、message、severity、
`repairable = True`。

### LLM 输出、Fallback 与应用

prompt 是 `dependency_repair_patch_prompt`，期望 schema 为
`dependency_repair_patch/v1`。任务是只修复 invalid dependency inputs，禁止生成最终
dependency graph。

validator 是 `validate_dependency_repair_patch(candidate, draft)`。

默认 retries 为 1 次。失败时 fallback 是
`fallback_dependency_repair_patch(draft, dependency_errors)`，它不会主动改 edges 或
imports，只为每个 dependency error 生成 `mark_unresolved` repair action。

`apply_dependency_repair_patch()` 支持的实际修改：

- `remove_call_edge`：从 caller 的 `calls_allowed` 删除指定 callee；
- `adjust_imports_allowed`：对指定 file 增删 `imports_allowed`；
- `lower_visibility`：修改 function visibility；
- `change_function_file_assignment`：修改 function `file_id` 与 `declared_in`；
- `mark_unresolved`：向 `unresolved_questions` 追加 dependency repair 问题。

应用 patch 后再次 `finalize_dependency_graph()` 和 `validate_dependency_graph()`。

### Deterministic Last Resort

如果 LLM repair 或 fallback repair 后 dependency graph 仍有 errors，orchestrator 调用
`apply_deterministic_dependency_fallback()`：

- 清空所有 function 的 `calls_allowed`；
- 清空所有 file 的 `imports_allowed`；
- 把 dependency errors 追加到 `unresolved_questions`；
- 再次 `finalize_dependency_graph()`。

这一步是为了移除 invalid dependency inputs，让后续 full implementation plan validation
有机会继续检查剩余问题。

### 输出

如果触发 repair，会写入：

- `_agent_logs/007_5_6_dependency_repair_patch.json`
- `_validation_reports/007_5_6_dependency_repair_validation_report.json`

无论 repair 是否触发，implementation plan 阶段最终都会写入：

- `_step_logs/007_implementation_plan.json`
- `_validation_reports/008_dependency_validation_report.json`

随后进入 `5.7 Spec Readiness Validation`，运行：

- `validate_full_implementation_plan(implementation_plan, profile, planning_ir)`；
- `validate_dependency_graph(implementation_plan)`；
- `build_dependency_validation_report(implementation_plan, dependency_diags)`。

如果任何 accumulated diagnostics 中存在 error，Planning Agent 写出 failed
`014_planning_validation_report.json` 与 failed manifest，并在进入 Coder Specs
Compilation 前停止。只有 implementation plan 与 dependency graph 都通过，才会记录
`stage=implementation_plan substage=5.7_spec_readiness_validation build done` 与
`stage=implementation_plan build done`，并进入下一阶段
`stage=specs_compile start`。

## Artifact 位置汇总

普通 step artifacts 写入 `_step_logs`，validation reports 写入 `_validation_reports`。
`AGENT_LOG_ARTIFACT_KEYS` 中的 patch 类 artifact 写入 `_agent_logs`；本范围内包括：

- `function_signature_patch`
- `function_behavior_patch`
- `wire_access_binding_patch`
- `dependency_repair_patch`

因此各阶段主要 artifact 为：

| 阶段 | Candidate/Patch | Validation Report |
| --- | --- | --- |
| 5.4b batch | `_agent_logs/007_5_4b_function_signature_patch__<module>__batch_<n>.json` | `_validation_reports/007_5_4b_function_signature_validation_report__<module>__batch_<n>.json` |
| 5.4b aggregate | `_step_logs/007_5_4b_function_signature_patch.json` | `_validation_reports/007_5_4b_function_signature_validation_report.json` |
| 5.4c batch | `_agent_logs/007_5_4c_function_behavior_contract_patch__<module>__batch_<n>.json` | `_validation_reports/007_5_4c_function_behavior_validation_report__<module>__batch_<n>.json` |
| 5.4c aggregate | `_step_logs/007_5_4c_function_behavior_contract_patch.json` | `_validation_reports/007_5_4c_function_behavior_validation_report.json` |
| 5.4d | `_step_logs/007_5_4d_function_wire_access_binding_patch.json` | `_validation_reports/007_5_4d_function_wire_access_binding_validation_report.json` |
| 5.4e batch | `_agent_logs/007_5_4e_function_call_contracts_candidate__<module>__batch_<n>.json` | `_validation_reports/007_5_4e_function_call_contracts_validation_report__<module>__batch_<n>.json` |
| 5.4e aggregate | `_step_logs/007_5_4e_function_call_contracts_candidate.json` | `_validation_reports/007_5_4e_function_call_contracts_validation_report.json` |
| 5.5a | `_step_logs/007_5_5a_file_layout_candidate.json` | `_validation_reports/007_5_5a_file_layout_validation_report.json` |
| 5.5b | `_step_logs/007_5_5b_runtime_entrypoint_candidate.json` | `_validation_reports/007_5_5b_runtime_entrypoint_validation_report.json` |
| 5.6 repair, conditional | `_agent_logs/007_5_6_dependency_repair_patch.json` | `_validation_reports/007_5_6_dependency_repair_validation_report.json` |
| final implementation plan | `_step_logs/007_implementation_plan.json` | `_validation_reports/008_dependency_validation_report.json` |

## 进入 Coder Specs Compilation 的前置条件

进入 `Coder Specs Compilation` 前，必须满足：

- `007_implementation_plan.json` 已写出；
- `008_dependency_validation_report.json` 已写出；
- accumulated diagnostics 中没有 error；
- full implementation plan validation 通过；
- dependency graph validation 通过。

此时 `Coder Specs Compilation` 才会执行：

`compile_spec_bundle(implementation_plan, output_dir)`。

也就是说，5.4b 到 5.7 的职责是把 function inventory 逐步降低为 coder 可用的
implementation-oriented plan，包括 signatures、behavior、wire mapping、call graph
inputs、file layout、runtime entrypoint 和 derived dependency graph；而
`Coder Specs Compilation` 只消费已经通过 validation 的 final implementation plan，不再
补充这些工程语义。
