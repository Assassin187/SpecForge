# SpecForge Planning 稳定化当前任务状态

## 0. 最新状态：Step 6 三协议 minimum matrix

更新时间：2026-06-18

当前步骤：

```text
Step 6：MQTT / CoAP / SMTP minimum 功能复现与矩阵化归档
```

本轮新增统一入口：

```text
tools/eval/run_minimum_matrix.py
docs/minimum_protocol_reproduction.md
```

本轮 facts 输入策略：

```text
不新增 gold_facts，不自行生成 facts。
MQTT 使用 agent/facts/gold_facts/mqtt_min/protocol_facts.json。
CoAP 使用 agent/facts/gold_facts/coap_min/protocol_facts.json。
SMTP 使用当前工作树已有 agent/facts/gold_facts/smtp_min/protocol_facts.json。
```

注意：

```text
当前工作树中 agent/facts/gold_facts/smtp_min/protocol_facts.json 是已有未跟踪输入，
且旧 agent/facts/gold_facts/smtp/protocol_facts.json 在 git status 中显示 deleted。
本轮仅引用当前已有 facts，不修改 facts 内容。
SMTP smtp_min facts 包含 AUTH LOGIN/PLAIN，且 MAIL/RCPT/DATA 依赖 authenticated；
因此 SMTP smoke 仍按现有 facts 保留 AUTH 流程，不把 no-AUTH 行为伪装成 protocol fact。
```

新增 target profile：

```text
agent/planning/planning_target_profile_coap.json
agent/planning/planning_target_profile_smtp_min.json
```

### Step 6 运行结果摘要

MQTT：

```text
facts_path: agent/facts/gold_facts/mqtt_min/protocol_facts.json
target_profile_path: agent/planning/planning_target_profile_mqtt.json
matrix_run: agent/eval_out/minimum_matrix/20260618_214348
planning_run_dir: agent/eval_out/minimum_matrix/20260618_214348/mqtt/planning_run
source_planning_run: agent/planning/out/mqtt/broker__c__linux_epoll__minimum_v1/20260618_171930_310767
spec_bundle: agent/eval_out/minimum_matrix/20260618_214348/mqtt/planning_run/spec_bundle
planning_status: passed_existing
readiness_status: passed
schema/load/rendered header/dummy TU status: passed via coder validate
coder_output_dir: agent/eval_out/minimum_matrix/20260618_214348/mqtt/coder_out
compile_status: failed
repair_iterations: 0 in this dry/run
smoke_status: not_run because compile failed
failure_stage: coder_generate
failure_categories: source_compile_failure
main_diagnostic: generated source compile failed; logs include missing ntohs/malloc/free declarations and mqtt_packet_t has no member v.
artifact_archived: partial:copied
```

CoAP：

```text
facts_path: agent/facts/gold_facts/coap_min/protocol_facts.json
target_profile_path: agent/planning/planning_target_profile_coap.json
full_matrix_run: agent/eval_out/minimum_matrix/20260618_214741
diagnostic_replay_run: agent/eval_out/minimum_matrix/20260618_222523
planning_run_dir: agent/eval_out/minimum_matrix/20260618_214741/coap/planning_run
spec_bundle: not produced
planning_status: failed
readiness_status: failed / not reached in original plan command
schema/load/rendered header/dummy TU status: not_run
coder_output_dir: agent/eval_out/minimum_matrix/20260618_214741/coap/coder_out
compile_status: not_run
repair_iterations: not_run
smoke_status: not_run
failure_stage: planning_plan / planning_verify replay
failure_categories: planning_stage_failure
main_diagnostic: readiness_missing_protocol_version; final implementation_plan lacks protocol version/spec_version metadata.
secondary_diagnostic: blocking_unresolved_questions; final implementation_plan still contains blocking unresolved questions.
observed_candidate_semantic_errors: uncovered_target_surface, wire_mapping_target_path_unknown/not_canonical, call_contract_unknown_value_ref.
artifact_archived: partial
```

SMTP：

```text
facts_path: agent/facts/gold_facts/smtp_min/protocol_facts.json
target_profile_path: agent/planning/planning_target_profile_smtp_min.json
input_validation_dir: agent/eval_out/minimum_matrix_input_check/smtp
planning_status: input validated only
readiness_status: not_run
schema/load/rendered header/dummy TU status: not_run
coder_output_dir: not_run
compile_status: not_run
repair_iterations: not_run
smoke_status: not_run
failure_stage: not_run in full closure this turn
failure_categories: execution_not_completed_this_turn
main_diagnostic: full SMTP planning/coder/smoke was not started after the long CoAP full run; reusable runner entry exists.
artifact_archived: input preflight manifest only
```

### Step 6 完成指标判定

```text
[x] 统一 matrix runner 已建立。
[x] 固定 summary JSON/MD 输出已建立。
[x] MQTT 得到 readiness/coder validate/compile/smoke 状态记录。
[x] CoAP 得到 full planning failure taxonomy 和 artifacts。
[x] SMTP facts/profile preflight 通过，且 smoke/facts 的 AUTH 边界已明确。
[x] 不存在 false planning success：CoAP planning failure 没有被标记为 pass。
[x] 不存在通过关闭 validator 或跳过 smoke 制造 success。
[x] readiness passed 的 MQTT bundle 没有在 coder loader/header 阶段失败，而是在 source compile 阶段失败。
[ ] 三协议均得到完整 compile/smoke 状态记录：SMTP full closure 未跑完。
[ ] 至少一个协议完成 compile + smoke：本轮未达到。
[ ] Step 6 完全完成：未达到，当前为 runner + MQTT dry/run + CoAP full planning failure + SMTP input validation。
```

下一轮入口：

```text
Step 7：稳定性回归与论文实验准备。
在进入 Step 7 前，建议先用同一 runner 继续执行 SMTP full closure，并修复 CoAP readiness_missing_protocol_version / blocking_unresolved_questions。
```

## 1. 当前总体阶段

当前目标：

```text
让 planning 输出能稳定驱动 coder compile/smoke，
并在 MQTT、CoAP、SMTP 的最小功能集上复现。
```

当前执行状态：

```text
Step 5 semantic actionability 修复继续推进；本轮专门修复 20260617 两次运行暴露的问题。
20260617_172331_047723 的 5.3 string_view/buffer_view 未声明 type ref blocker 已通过 targeted resume。
20260617_103648_329047 的 5.4e prose value_ref 已能在 stage validator 报 call_contract_unknown_value_ref。
MQTT fresh planning run 仍未完成：architecture generation request 长时间无新输出，被中断，未产出 implementation_plan/spec_bundle。
当前不能把 targeted resume 或 deterministic/unit 验证冒充为 fresh-run 完成。
```

说明：

- duplicate function name / lifecycle obligation 导致的第 0 类 fatal exit 已由用户说明完成修复。
- 本状态文件从 Step 1 开始记录。
- 后续每个 Codex 会话结束前必须更新本文件。

## 2. 已完成工作

### 2.1 specs 是否需要整体精简的诊断

状态：

```text
已完成
```

结论：

```text
当前不应整体压缩 coder-facing strict specs。
应保留 strict specs 主体，优先修复 dependency lowering、public/system type closure、final readiness validation。
```

已形成的判断：

1. `Gold-Full` specs 可以驱动 coder compile/smoke。
2. `Gold-Min-Interface` 在 MQTT/CoAP 上明显退化。
3. behavior detail、test vectors、calls/rely 对 coder usability 有价值。
4. module/file `DOC_REF` 可下沉到 sidecar。
5. function-level `WIRE_MAPPING` 可作为后续 optional/sidecar 候选，但不能删除整体 wire/access 语义。
6. 当前主要瓶颈不是 specs 过细，而是 lowering、closure、validator/readiness 缺口。

### 2.2 历史 Step 1 planning blocker

状态：

```text
已由 Step 1 deterministic validation / lowering / readiness gate 覆盖。
```

历史主要 blocker：

```text
1. dependency fallback 可能清空 calls_allowed/imports_allowed 后 false pass。
2. HEADER.DEPENDENCY 与 SOURCE.DEPENDENCY 来源混用。
3. HEADER.SYSTEM_DEPENDENCY 未稳定 lower。
4. public type refs 没有完整 header dependency closure。
5. final coder compatibility validation 可能跳过 rendered header validation。
6. calls_allowed/imports_allowed 为空但 call_contracts/signature_dependencies 非空时仍可能 passed。
7. file layout / architecture mapping 与 dependency closure 不一致。
8. type/signature oracle 单独替换无法修复 bundle，说明 type/signature/dependency/file layout 强耦合。
```

### 2.3 duplicate function name fatal exit

状态：

```text
用户说明已修复
```

后续不再作为本计划的 Step 0，但若回归出现，应作为 regression blocker 记录。

## 3. 当前待执行步骤

当前步骤：

```text
Step 5：semantic actionability（本轮已实现；fresh-run 指标未完成）
```

目标：

```text
在 closure/readiness 稳定后，提升 5.4a-5.4e 的语义质量。
behavior、wire/access、call contracts、test vectors 必须绑定 declared functions/types/resources/fields/access paths，
使 planning 输出更适合 coder 生成可编译、可 smoke 的实现。
```

涉及模块候选：

```text
agent/planning/stages/inventory_planning_space.py
agent/planning/stages/inventory_reconciliation.py
agent/planning/stages/implementation_plan_context.py
agent/planning/stages/implementation_plan_merger.py
agent/planning/stages/specs_compiler.py
agent/planning/validators/implementation_plan_stages.py
agent/planning/prompts/templates.py
相关 tests / fixtures
```

本步骤任务清单：

```text
[x] function_family_obligation_report/v1 接入 5.4a planning space / reconciliation / validator / orchestrator diagnostics。
[x] 5.4c behavior action binding validator 接入 declared state/resource/type/error/source/callee/wire/access refs。
[x] 5.4d wire/access closure validator 强化 required field、target path、skip/reject reason、type ref 检查。
[x] 5.4e call precision validator 强化 unknown/stale callee、cross-module/private、param binding、failure policy 检查。
[x] final readiness 增加 behavior/wire/call/test-vector 汇总检查。
[x] coder loader 增加 stale CALL_CONTRACTS、CALL_CONTRACTS/RELY drift、WIRE_MAPPING missing TEST_VECTORS diagnostics。
[x] 5.4c/5.4d/5.4e prompt rules 明确只能绑定 existing declared artifacts，不能输出 final dependency_graph。
[x] 5.3 declared view type closure 接入：`mqtt_string_view_t` / `mqtt_buffer_view_t` 进入 type planning space / reconciliation / lowering。
[x] 5.4e call binding context 增加 `allowed_value_bindings`，prose `value_ref` 在 candidate validator 阶段 blocking。
[x] specs compiler 修复 exported public `type_inventory` type lowering，避免只按 canonical_types 解析 `exports_type_ids`。
[x] Step 5 regression tests 通过。
[x] deterministic MQTT Step 5 spec bundle 可通过 coder validate，behavior/call/wire 字段可解释到 declared artifacts。
[ ] MQTT fresh planning output 可解释到 declared artifacts：本轮未完成，见失败检查。
[x] 当前状态文件更新。
```

## 4. Step 1 / Step 2 / Step 3 完成指标

只有同时满足以下条件，才能标记 Step 1 完成：

```text
[x] 新增 fail-closed dependency tests 通过。
[x] system type fixture 通过 rendered header validation。
[x] external public type fixture 通过 rendered header validation。
[x] final planning readiness 启用 strict rendered header gate。
[x] dummy header translation unit compile 已接入并由 strict rendered header validation 调用。
[x] MQTT fresh planning sample 进入 specs_compile 并产出 coder_manifest.json + spec_bundle/。
[x] 若 fresh MQTT planning 失败，失败原因是 blocking diagnostic，而非 silent false success。
[x] 本文件已更新，记录命令、结果、剩余 blocker。
```

只有同时满足以下条件，才能标记 Step 2 完成：

```text
[x] cross-layer consistency validator 已接入 5.7_spec_readiness 或 final validation。
[x] call_contracts/calls_allowed/signature_dependencies/imports_allowed/dependency_graph fixture tests 通过。
[x] MQTT planning 输出不再出现 call_contracts 非空但 function_edges=0 且 passed。
[x] validation report 可以解释 dependency/call edge 来源。
[x] specs_compile 后 validation report 可以解释 HEADER.DEPENDENCY / SOURCE.DEPENDENCY 来源。
[x] coder compatibility 显式 blocking unknown HEADER/SOURCE dependency header path。
[x] 当前状态文件已更新。
```

只有同时满足以下条件，才能标记 Step 3 完成：

```text
[x] file layout validator 已接入 readiness 或 final validation。
[x] role-to-file mapping report 可生成：009_layout_runtime_mapping_report.json。
[x] header path resolvability tests 通过。
[x] runtime entrypoint closure tests 通过。
[x] MQTT specs_compile resume 的 module/file/header/runtime dependency 可解释。
[x] docs/CURRENT_TASK_STATUS.md 已更新。
```

只有同时满足以下条件，才能标记 Step 4 完成：

```text
[x] public type obligation report 生成。
[x] signature type-ref closure validator 接入 5.4b 与 final readiness/coder compatibility。
[x] callback closure tests 通过。
[x] stale/private type ref tests 通过。
[x] MQTT sample validation 通过；deterministic MQTT spec bundle rendered header / dummy TU validation 通过。
[x] docs/CURRENT_TASK_STATUS.md 已更新。
```

只有同时满足以下条件，才能标记 Step 5 完成：

```text
[x] function family obligation validator 接入。
[x] behavior action binding validator 接入。
[x] wire/access closure validator 接入。
[x] call contract precision validator 接入。
[~] MQTT planning output 的 behavior/call/wire 字段能解释到 declared artifacts：
    deterministic Step 5 bundle 已验证通过；fresh planning run 本轮未完成。
[x] docs/CURRENT_TASK_STATUS.md 已更新。
```

## 5. 建议下一轮 Codex 会话入口

下一轮会话建议执行：

```text
请阅读 AGENT.md、PLANNING_STABILIZATION_TASKS.md 和 CURRENT_TASK_STATUS.md。
Step 5 代码与 deterministic/spec 验证已完成；fresh MQTT planning run 未完成。
下一轮入口固定为 Step 6：MQTT/CoAP/SMTP 最小功能复现。
进入 Step 6 前必须先重跑 MQTT fresh planning，确认 Step 5 fresh-run 指标补齐。
不要回退 Step 1/2/3/4/5 strict validators，不要压缩 strict schema，不要关闭 rendered header validation。
```

## 6. 会话记录摘要

本节保留后续执行需要的压缩历史与最近一轮任务记录。早期 Session 000-009 的逐轮修复细节已合并，避免干扰后续 coder compile/smoke 与多协议复现。

### Step 5 本轮记录

时间：

```text
2026-06-17
```

本轮目标：

```text
继续优化 semantic actionability，并修复两次 20260617 MQTT run 暴露出的具体 blocker：
1. 20260617_172331_047723：5.3 codec 中 string_view / buffer_view 泄漏为 undeclared type ref。
2. 20260617_103648_329047：5.4e/final readiness 中 param_bindings[].value_ref 使用 prose。
```

修改文件：

```text
agent/planning/stages/inventory_planning_space.py
agent/planning/stages/inventory_reconciliation.py
agent/planning/stages/implementation_plan_context.py
agent/planning/stages/implementation_plan_merger.py
agent/planning/stages/specs_compiler.py
agent/planning/prompts/templates.py
agent/planning/validators/implementation_plan_stages.py
agent/planning/tests/test_implementation_plan_stage_candidates.py
agent/planning/tests/test_coder_schema_lowering.py
docs/CURRENT_TASK_STATUS.md
```

新增/更新测试：

```text
1. required function family missing -> function_family_obligation_uncovered。
2. required function family covered -> pass/report covered。
3. behavior action unknown field/helper/source data -> blocking diagnostics。
4. behavior action declared field/function/type refs -> pass。
5. wire mapping target path unknown -> blocking。
6. skip/reject missing reason -> blocking。
7. forbidden cross-module call -> blocking。
8. call failure policy mismatch -> blocking。
9. missing smoke test vector anchor -> readiness diagnostic。
10. coder loader stale CALL_CONTRACTS / CALL_CONTRACTS-RELY drift / missing WIRE_MAPPING TEST_VECTORS diagnostics。
11. string_view / buffer_view without declared view type -> undeclared_view_type_alias。
12. declared mqtt_string_view_t / mqtt_buffer_view_t lower to TYPE_SPEC and coder compatibility passes。
13. 5.4e prose value_ref -> call_contract_unknown_value_ref。
14. declared caller param binding such as message -> message passes。
```

实际运行命令：

```text
python3 -m compileall -q agent/planning agent/coder tools
python3 -m unittest agent.planning.tests.test_implementation_plan_stage_candidates agent.planning.tests.test_coder_schema_lowering
python3 -m unittest agent.planning.tests.test_implementation_plan_stage_candidates agent.planning.tests.test_coder_schema_lowering agent.planning.tests.test_validators
python3 -m agent.planning plan --facts agent/facts/gold_facts/mqtt_min/protocol_facts.json --target-profile agent/planning/planning_target_profile_mqtt.json --output-dir agent/planning/out/mqtt_step5_semantic_actionability_fresh
python3 -m agent.planning plan --facts agent/facts/gold_facts/mqtt_min/protocol_facts.json --target-profile agent/planning/planning_target_profile_mqtt.json --resume-source-dir agent/planning/out/mqtt/broker__c__linux_epoll__minimum_v1/20260617_172331_047723 --resume-from-stage implementation_plan_5_3 --stop-after-stage implementation_plan_5_3 --output-dir agent/planning/out/mqtt_step5_resume_172331_type_fix
python3 -m agent.planning plan --facts agent/facts/gold_facts/mqtt_min/protocol_facts.json --target-profile agent/planning/planning_target_profile_mqtt.json --resume-source-dir agent/planning/out/mqtt/broker__c__linux_epoll__minimum_v1/20260617_103648_329047 --resume-from-stage implementation_plan_5_4e --stop-after-stage implementation_plan_5_7 --output-dir agent/planning/out/mqtt_step5_resume_103648_call_fix
python3 -m agent.planning verify --output-dir agent/planning/out/mqtt_step5_semantic_actionability_fresh
python3 -m agent.planning verify --output-dir agent/planning/out/mqtt_step5_resume_172331_type_fix
python3 -m agent coder --spec-root agent/planning/out/mqtt_step5_semantic_actionability_fresh/spec_bundle validate
python3 - <<'PY'
... load 20260617_103648_329047 old 5.4e candidate and run validate_calls_allowed_candidate(...)
PY
```

通过的检查：

```text
compileall 通过。
指定 unittest 三件套 197 tests 通过：
  agent.planning.tests.test_implementation_plan_stage_candidates
  agent.planning.tests.test_coder_schema_lowering
  agent.planning.tests.test_validators
20260617_172331_047723 targeted resume from 5.3 passed/stopped：
  codec final_inventory accepted；
  5.3 all_modules validation passed；
  mqtt_string_view_t / mqtt_buffer_view_t 出现在 codec planning space 与 final type inventory；
  原 unknown/stale string_view/buffer_view blocker 不再出现。
20260617_103648_329047 old 5.4e candidate 离线 validator：
  5 个 prose value_ref 均报 call_contract_unknown_value_ref；
  包括 client session、client ID、connection handle、decoded PUBLISH packet、client ID and topic filters。
```

失败的检查：

```text
MQTT fresh planning run：未完成。
实际停止点：architecture generation_request=1/2/3 start 后长时间无新 stdout，未产出 architecture_candidates / implementation_plan / spec_bundle。
fresh verify：失败，缺 architecture_candidates、implementation_plan、coder_manifest、spec_bundle 等 artifacts。
fresh coder validate：失败，spec_bundle 下无 PROTOCOL_MODULE_SPEC。
20260617_172331 targeted resume verify：失败是预期的 stopped output，缺 implementation_plan、dependency_validation_report、coder_manifest、spec_bundle。
20260617_103648 targeted resume from 5.4e：失败并停在 5.4a，因为旧 artifact 在当前 function family gate 下报 function_family_obligation_uncovered。
```

新增 diagnostics：

```text
function_family_obligation_uncovered
behavior_action_unknown_field
behavior_action_unknown_helper
behavior_action_unknown_state
behavior_action_unknown_resource
behavior_action_unknown_error
behavior_action_unknown_access_path
behavior_action_unknown_source_data
behavior_action_unbound
wire_mapping_target_path_unknown
wire_mapping_skip_reject_missing_reason
stale_type_ref
forbidden_cross_module_call
call_contract_failure_policy_mismatch
readiness_call_contract_failure_policy_mismatch
readiness_behavior_action_unbound
readiness_core_message_fields_without_access
coder_unknown_call_contract_callee
coder_call_contract_rely_drift
coder_missing_wire_mapping_test_vectors
undeclared_view_type_alias
missing_view_type
view_type_field_mismatch
call_contract_unknown_value_ref
```

新增风险：

```text
1. MQTT fresh-run Step 5 指标未补齐；下一轮进入 Step 6 前必须先重跑 fresh planning。
2. function family obligations 现在基于 owned_capabilities / deterministic seeds / decomposition ownership 派生；CoAP/SMTP 可能需要按协议 minimum scope 调整映射。
3. behavior explicit refs 依赖 field:/fn:/type:/state:/resource:/error:/access:/source_data: 约定，prompt 已强化，但 LLM 输出仍可能需要 repair。
4. strict diagnostics 增加后，旧 resume artifacts 可能需要从 5.3/5.4a/5.4c 重新生成，不能直接从更晚 substage 继承。
5. declared view type 改为 public C-facing view structs 后，下游 coder/source generation 需要使用 `.data` / `.len` 而不是裸 char* / uint8_t*。
```

Step 5 完成结论：

```text
代码接入、测试、deterministic/spec bundle 验证已完成。
两个 20260617 run 暴露的具体 blocker 均已被 targeted 验证覆盖。
fresh MQTT planning output 指标未完成，不能标记为完全 fresh-run complete。
下一轮入口：Step 6 MQTT/CoAP/SMTP 最小功能复现；进入前必须先补跑 MQTT fresh planning。
```

### Step 1 压缩记录

时间范围：

```text
2026-06-15 至 2026-06-16
```

目标：

```text
修复 P0 deterministic closure 与 readiness gate，使 planning final success 不再绕过 deterministic coder compatibility failure。
```

核心完成项：

```text
1. dependency fallback fail-closed：不再通过清空 calls_allowed/imports_allowed 制造 empty graph success。
2. cross-layer dependency diagnostics：call_contracts、calls_allowed、signature_dependencies、imports_allowed、dependency graph 不一致会 blocking。
3. HEADER.DEPENDENCY / SOURCE.DEPENDENCY lowering 分离：public ABI/type closure 驱动 header deps，implementation calls/source include needs 驱动 source deps。
4. public type dependency lowering：public signatures、struct fields、typedef/alias、callback signatures、function pointer params 的 external type refs 会映射到 provider header。
5. system type registry：size_t、ssize_t、uint*_t、bool、sockaddr_in、time_t 等 public system type 会 lower 到 HEADER.SYSTEM_DEPENDENCY。
6. final coder compatibility strict gate：planning readiness 调用 load_spec_bundle_from_root(..., validate_rendered_headers=True)。
7. rendered header dummy translation unit compile：rendered header 编译失败会成为 blocking diagnostic。
8. call_contracts.param_bindings.value_ref 支持保守 C literal/cast literal，非法 literal 仍 blocking。
9. type/signature/provider closure 修复后，MQTT fresh planning 已进入 specs_compile 并产出 coder_manifest.json + spec_bundle/。
```

关键验证命令：

```text
python3 -m agent planning plan --facts agent/facts/gold_facts/mqtt_min/protocol_facts.json --target-profile agent/planning/planning_target_profile_mqtt.json --output-dir agent/planning/out/mqtt_step1_main_rerun_20260616_1600
python3 -m agent planning verify --output-dir agent/planning/out/mqtt_step1_main_rerun_20260616_1600
python3 -m agent coder --spec-root agent/planning/out/mqtt_step1_main_rerun_20260616_1600/spec_bundle validate
python3 -m unittest agent.planning.tests.test_implementation_plan_stage_candidates agent.planning.tests.test_coder_schema_lowering
```

最终检查结果：

```text
planning finalize stage=specs_compile status=success。
000_planning_run_manifest.json status=success，diagnostics=[]。
014_planning_validation_report.json status=success。
facts_compatibility_status=passed。
coder_compatibility_status=passed。
coder_schema_status=passed。
coder_loader_status=passed。
008_dependency_validation_report.json status=passed，diagnostics=[]。
生成 coder_manifest.json 与 spec_bundle/。
planning verify：No diagnostics。
coder validate：No diagnostics。
unittest fallback：153 tests passed。
pytest 当前环境不可用：No module named pytest。
```

可复用输出基线：

```text
agent/planning/out/mqtt_step1_main_rerun_20260616_1600/spec_bundle
```

Step 1 结论：

```text
Step 1 completed。
当前不应继续在 Step 1 修复历史里消耗上下文。下一步应使用上述 spec_bundle 进入 MQTT coder generate/compile/smoke。
```

后续记录规则：

```text
后续会话只追加与当前阶段直接相关的简短记录：目标、修改文件、命令、结果、下一步。不要恢复逐轮 Step 1 细节。
```

### Step 2 压缩记录

时间：

```text
2026-06-16
```

目标：

```text
补齐 cross-layer dependency report/provenance，使 008_dependency_validation_report.json 可以解释 dependency/call/header/source edge 来源。
```

核心完成项：

```text
1. dependency_validation_report/v1 向后兼容新增 errors、warnings、dependency_sources、call_edge_sources、header_dep_sources、source_dep_sources。
2. errors/warnings 增加 stage、entity_kind、entity_id、source_fields、suggested_repair，不修改 PlanningDiagnostic dataclass。
3. call_edge_sources 解释 calls_allowed、call_contracts 与 dependency_graph.function_edges 的一致性，status 为 explained/missing_graph_edge/unexplained_graph_edge。
4. dependency_sources 覆盖 dependency_graph module/file/function edges，并追溯 imports_allowed、signature_dependencies、calls_allowed、call_contracts。
5. specs_compile 后从生成的 FILE_SPEC 读取 HEADER.DEPENDENCY / SOURCE.DEPENDENCY，回填 header_dep_sources / source_dep_sources。
6. coder loader 显式校验 HEADER.DEPENDENCY / SOURCE.DEPENDENCY 引用的 header path 必须存在，planning compatibility code 分别为 coder_unknown_header_dependency / coder_unknown_source_dependency。
```

关键验证命令：

```text
python3 -m unittest agent.planning.tests.test_implementation_plan_stage_candidates agent.planning.tests.test_coder_schema_lowering
python3 -m compileall agent/planning/stages/dependencies.py agent/planning/orchestrator.py agent/coder/specs.py
python3 -m agent planning verify --output-dir agent/planning/out/mqtt_step2_dependency_report_rerun
python3 -m agent coder --spec-root agent/planning/out/mqtt_step2_dependency_report_rerun/spec_bundle validate
python3 -m agent planning plan --facts agent/facts/gold_facts/mqtt_min/protocol_facts.json --target-profile agent/planning/planning_target_profile_mqtt.json --resume-source-dir agent/planning/out/mqtt_step1_main_rerun_20260616_1600 --resume-from-stage specs_compile --output-dir agent/planning/out/mqtt_step2_dependency_report_rerun
python3 -m agent planning plan --facts agent/facts/gold_facts/mqtt_min/protocol_facts.json --target-profile agent/planning/planning_target_profile_mqtt.json --output-dir agent/planning/out/mqtt_step2_dependency_report_fresh
```

最终检查结果：

```text
unittest：156 tests passed。
compileall passed。
specs_compile resume 输出：agent/planning/out/mqtt_step2_dependency_report_rerun。
resume planning finalize stage=specs_compile status=success。
resume planning verify：No diagnostics。
resume coder validate：No diagnostics。
resume dependency report keys 包含 errors、warnings、dependency_sources、call_edge_sources、header_dep_sources、source_dep_sources。
resume dependency_sources=179，call_edge_sources=143，source_dep_sources=15，header_dep_sources=0（该 MQTT bundle 当前无 HEADER.DEPENDENCY）。
fresh planning 输出：agent/planning/out/mqtt_step2_dependency_report_fresh。
fresh planning 未进入 specs_compile，因 unrelated readiness_missing_runtime_error_test blocking failed。
fresh dependency report 已包含 plan-level provenance arrays：dependency_sources=343，call_edge_sources=242。
```

Step 2 结论：

```text
Step 2 completed。
report/provenance 与 coder-facing dependency path validation 已完成。fresh run 的 failure 是 runtime error test readiness 缺口，不是 Step 2 dependency report 缺口。
```

### Step 3 压缩记录

时间：

```text
2026-06-16
```

目标：

```text
闭合 file layout 与 runtime mapping，使 role-to-file mapping、generated header path、runtime entrypoint lifecycle calls、MODULES[].DEPENDENCIES/FILES 互相可解释并 fail-closed。
```

修改文件：

```text
agent/planning/stages/implementation_plan_merger.py
agent/planning/stages/layout_runtime_mapping.py
agent/planning/orchestrator.py
agent/planning/validators/implementation_plan_stages.py
agent/planning/validators/coder_semantics.py
agent/coder/specs.py
agent/coder/generation.py
agent/planning/tests/test_implementation_plan_stage_candidates.py
agent/planning/tests/test_coder_schema_lowering.py
docs/CURRENT_TASK_STATUS.md
```

核心完成项：

```text
1. fallback_runtime_entrypoint() 只选择已存在 public lifecycle API；missing lifecycle id 现在 blocking。
2. merge_runtime_entrypoint() 不再隐式创建 create/start/run/destroy，只新增或复用 runtime main entrypoint。
3. app/broker seed 新增 *_start，并移除提前生成 main 的 artifact seed，避免 calls stage 把 main 填成 handler/router 逻辑。
4. 新增 layout_runtime_mapping_report/v1，输出 required roles 到 owning module/file/public API/dependency relationship 的映射。
5. merged role 会生成 explicit explanation；例如 router/topic/resource 由 router/topic/resource aliases 同时命中说明。
6. 5.7 readiness 与 specs_compile resume gate 接入 layout_runtime_mapping_report，blocking missing required role。
7. coder loader 将 MODULES[].FILES missing file 升级为 blocking，并新增 HEADER.PATH / SOURCE.PATH duplicate blocking diagnostics。
8. coder runtime rendering 优先读取 generated main.c FILE_SPEC、SOURCE.DEPENDENCY 和 entrypoint FUNCTION_SPEC；main 只 include resolved SOURCE.DEPENDENCY，并从 existing public lifecycle signatures 派生调用。
9. coder semantics 增加 runtime main lifecycle closure：unknown lifecycle function、non-lifecycle direct call、missing source dependency 均 blocking。
```

新增 diagnostics：

```text
layout_required_role_unmapped
layout_merged_role_missing_explanation
layout_module_file_unknown
layout_duplicate_header_path
layout_duplicate_source_path
runtime_entrypoint_unknown_lifecycle_function
runtime_entrypoint_non_lifecycle_call
runtime_entrypoint_missing_source_dependency
runtime_entrypoint_sequence_lifecycle_mismatch
```

新增/更新测试：

```text
1. missing HEADER/SOURCE dependency -> blocking。
2. existing generated header dependency -> pass。
3. unknown MODULES[].DEPENDENCIES -> blocking。
4. duplicate HEADER.PATH / SOURCE.PATH -> blocking。
5. MODULES[].FILES missing file -> blocking。
6. runtime entrypoint missing lifecycle function -> blocking。
7. runtime entrypoint using existing public lifecycle API -> pass。
8. runtime entrypoint non-lifecycle call -> blocking。
9. runtime entrypoint missing SOURCE.DEPENDENCY -> blocking。
10. merged role with explanation -> pass。
11. merged role without explanation -> warning。
```

实际运行命令：

```text
python3 -m compileall -q agent/planning agent/coder tools
python3 -m unittest agent.planning.tests.test_implementation_plan_stage_candidates agent.planning.tests.test_coder_schema_lowering
python3 -m agent planning validate --facts agent/facts/gold_facts/mqtt_min/protocol_facts.json --target-profile agent/planning/planning_target_profile_mqtt.json
python3 -m agent planning plan --facts agent/facts/gold_facts/mqtt_min/protocol_facts.json --target-profile agent/planning/planning_target_profile_mqtt.json --resume-source-dir agent/planning/out/mqtt_step2_dependency_report_rerun --resume-from-stage specs_compile --output-dir agent/planning/out/mqtt_step3_layout_runtime_resume
python3 -m agent planning verify --output-dir agent/planning/out/mqtt_step3_layout_runtime_resume
python3 -m agent coder --spec-root agent/planning/out/mqtt_step3_layout_runtime_resume/spec_bundle validate
python3 - <<'PY'
from pathlib import Path
from agent.planning.validators.coder_schema import validate_coder_spec_bundle_against_schema
spec_root = Path('agent/planning/out/mqtt_step3_layout_runtime_resume/spec_bundle')
diags = validate_coder_spec_bundle_against_schema(spec_root)
print(f'schema_errors={len([d for d in diags if d.level == "error"])} diagnostics={len(diags)}')
PY
python3 - <<'PY'
from pathlib import Path
from agent.coder.specs import load_spec_bundle_from_root
bundle = load_spec_bundle_from_root(Path('agent/planning/out/mqtt_step3_layout_runtime_resume/spec_bundle'), validate_rendered_headers=True)
print(f'loader_rendered_header_errors={len([d for d in bundle.diagnostics if d.level == "error"])} diagnostics={len(bundle.diagnostics)}')
PY
```

通过的检查：

```text
compileall passed。
unittest：165 tests passed。
MQTT planning validate：No diagnostics。
MQTT specs_compile resume：planning finalize stage=specs_compile status=success，coder_status=passed，No diagnostics。
009_layout_runtime_mapping_report.json：status=passed，required_role_count=8，mapped_role_count=8，merged_role_count=7。
MQTT resume module dependencies：unknown_deps=[]。
MQTT resume MODULES[].FILES：missing_files=[]。
MQTT resume HEADER/SOURCE paths：duplicate_headers={}，duplicate_sources={}。
MQTT resume main SOURCE.DEPENDENCY：['src/broker/broker.h']。
planning verify：No diagnostics。
coder validate：No diagnostics。
schema validation：schema_errors=0，diagnostics=0。
loader + rendered header dummy TU gate：loader_rendered_header_errors=0，diagnostics=0。
generated header dependency resolvability：generated_header_dependency_missing=0。
```

失败或未完成检查：

```text
python3 -m agent planning verify agent/planning/out/mqtt_step3_layout_runtime_resume
结果：失败，CLI 用法错误，缺少 --output-dir；随后已用正确命令重跑并通过。

python3 -m agent coder validate --spec-root agent/planning/out/mqtt_step3_layout_runtime_resume/spec_bundle
结果：失败，coder CLI global 参数位置错误；随后已用 python3 -m agent coder --spec-root ... validate 重跑并通过。

python3 -m agent planning plan --facts agent/facts/gold_facts/mqtt_min/protocol_facts.json --target-profile agent/planning/planning_target_profile_mqtt.json --output-dir agent/planning/out/mqtt_step3_layout_runtime_fresh
结果：人工中断，exit code 130。卡在 architecture generation 并发 LLM future 等待，无 Step 3 diagnostics；本轮以 MQTT validate + specs_compile resume + coder/schema/load gates 作为可复现验证。
```

补充验证：

```text
最新运行：agent/planning/out/mqtt/broker__c__linux_epoll__minimum_v1/20260616_231307_313900。
运行方式：从 20260616_194800_796436 以 implementation_plan_5_2b 续跑。
结果：run manifest status=failed，failure stage=specs_compile，failure code=validation_errors。
Step 3 相关结果：5.5a file layout validation passed；5.5b runtime entrypoint validation passed；008_dependency_validation_report.json status=passed，diagnostics=[]；009_layout_runtime_mapping_report.json status=passed，required_role_count=8，mapped_role_count=8，merged_role_count=7。
runtime closure 结果：broker_app 已生成 mqtt_broker_t、mqtt_broker_create、mqtt_broker_start、mqtt_broker_run、mqtt_broker_destroy；main entrypoint calls_allowed 指向上述 existing public lifecycle API。
剩余失败：coder_rendered_header_compile_error，根因是 src/protocol_codec/codec.h 与 src/protocol_codec/protocol_codec.h 的 public type/header declaration order 与 circular include 问题，例如 protocol_codec.h 使用 mqtt_packet_t / mqtt_bytes_t 时类型尚未声明。该问题属于 Step 4 type inventory 与 function signature/header declaration closure，不再归入 Step 3。
```

新增风险：

```text
1. layout_runtime_mapping_report 当前按固定 required roles 检查，后续 Step 4/不同 target profile 可能需要把 requiredness 与 target profile capability 做更细绑定。
2. render_main_c 现在从 existing lifecycle signatures 派生调用，但复杂参数绑定仍是保守启发式；更完整的 type/signature closure 留给 Step 4。
3. runtime stop action 暂未扩展 schema；当前覆盖 create/start/run/destroy。
4. 工作区另有非本轮相关改动：agent/coder/protocol_behavior_val/* 与 agent/docs/behavior_test_issue_analysis.md，未纳入 Step 3 修改范围。
```

Step 3 结论：

```text
Step 3 completed。
file layout、runtime entrypoint、module/file/header dependency 已形成可解释闭合；strict FILE_SPEC / FUNCTION_SPEC 顶层未加入 planning-only 字段。
下一轮入口：Step 4 type inventory 与 function signature closure。
```

### Step 4 压缩记录

时间：

```text
2026-06-17
```

目标：

```text
修复 5.3_type_data 与 5.4b_function_signatures 的 public ABI closure。
required type categories 从 protocol role、target profile、minimum scope 与当前 architecture 派生，不复制 gold/example type names。
public signature 必须可 lower 到 C header declaration；unknown/stale/private type refs 必须在 planning/readiness/coder compatibility 阶段被拦截。
```

修改文件：

```text
agent/planning/stages/coder_spec_lowering.py
agent/planning/stages/inventory_planning_space.py
agent/planning/stages/inventory_reconciliation.py
agent/planning/stages/implementation_plan_merger.py
agent/planning/orchestrator.py
agent/planning/validators/implementation_plan_stages.py
agent/planning/validators/coder_semantics.py
agent/coder/specs.py
agent/coder/header_recipes.py
agent/planning/tests/test_implementation_plan_stage_candidates.py
agent/planning/tests/test_coder_schema_lowering.py
docs/CURRENT_TASK_STATUS.md
```

核心完成项：

```text
1. 新增 public_type_obligation_report/v1，并由 5.3 reconciliation 输出为 007_5_3_public_type_obligation_report*.json sidecar；strict coder spec 顶层未增加 planning-only 字段。
2. public type obligations 覆盖 connection/session context、protocol message/packet、parser/decoder state、encoder buffer、router/topic/resource store、server/broker context、transport connection、callback table、timer/lifecycle handle、error/result type。
3. type inventory closure 覆盖 struct fields、alias、callback return/params、function pointer fields；public ABI unknown/stale/private refs blocking。
4. function signature closure 检查 SIGNATURE.RAW、SIGNATURE.RETURN、SIGNATURE.PARAMS canonical 一致。
5. public signature non-system type refs 必须解析为 same-header/local public type、provider public type、合法 opaque backing pointer 或 system type。
6. final readiness 扫描 final plan 中 signature、type refs、access path c_type、callback、alias、call contract signature drift。
7. coder loader 将 HEADER.INTERFACE / SOURCE.INTERFACE / FUNCTION_SPEC signature mismatch 升级为 blocking，并新增 FUNCTION_SPEC raw/structured consistency check。
8. coder semantics 按 HEADER.DEPENDENCY provider visibility 校验 public header ABI；callback typedef、callback struct field、function pointer field 全部参与 closure。
9. header renderer 支持 struct field 中的 anonymous/named function pointer declaration。
10. fallback function signatures 优先使用当前 module 已规划 public opaque_handle，避免 stale default handle type。
```

新增 diagnostics：

```text
public_type_obligation_uncovered（预留 report/readiness 分类）
public_signature_unknown_type
public_signature_private_type_leak
public_signature_missing_header_dependency
callback_type_unknown_ref
callback_type_private_ref
function_pointer_field_unknown_ref
stale_type_ref
cross_module_private_type_ref
signature_raw_structured_mismatch
header_source_function_signature_mismatch
call_contract_signature_mismatch
public_header_type_cycle
coder_public_signature_missing_header_dependency
coder_public_callback_unknown_type
coder_public_function_pointer_unknown_type
```

新增/更新测试：

```text
1. public type obligation report category status coverage。
2. public signature external type + provider header -> pass。
3. public signature external type missing provider header -> blocking。
4. public signature unknown type -> blocking。
5. public signature private type leak -> blocking。
6. callback typedef external connection/payload type -> provider dependency lower 成功，rendered header compile pass。
7. callback typedef 参数 unknown type -> blocking。
8. function pointer field 引用 external type -> HEADER.DEPENDENCY lower 成功，rendered header compile pass。
9. function pointer field unknown type -> blocking。
10. stale type_ref / public private callback param 不再 silent normalize。
11. RAW signature 与 structured signature 不一致 -> blocking。
12. HEADER.INTERFACE / SOURCE.INTERFACE / FUNCTION_SPEC signature mismatch -> blocking。
13. CALL_CONTRACTS callee signature mismatch -> blocking。
```

实际运行命令：

```text
python3 -m compileall -q agent/planning agent/coder tools
python3 -m unittest agent.planning.tests.test_implementation_plan_stage_candidates agent.planning.tests.test_coder_schema_lowering
python3 -m unittest agent.planning.tests.test_implementation_plan_stage_candidates agent.planning.tests.test_coder_schema_lowering agent.planning.tests.test_validators
python3 -m agent.planning validate --facts agent/facts/gold_facts/mqtt_min/protocol_facts.json --target-profile agent/planning/planning_target_profile_mqtt.json --output-dir agent/planning/out/mqtt_step4_type_signature_closure_validate
python3 -m agent.planning plan --facts agent/facts/gold_facts/mqtt_min/protocol_facts.json --target-profile agent/planning/planning_target_profile_mqtt.json --output-dir agent/planning/out/mqtt_step4_type_signature_closure_fresh
kill 2818449
python3 - <<'PY'
from pathlib import Path
import shutil
from agent.planning.adapters.facts_input import build_planning_ir
from agent.planning.adapters.target_profile import load_target_profile
from agent.planning.stages.protocol_profile import build_protocol_profile
from agent.planning.stages.constraints import activate_constraints
from agent.planning.stages.architecture import select_architecture
from agent.planning.tests.current_flow_fixtures import current_architecture_candidates, current_implementation_plan
from agent.planning.stages.specs_compiler import compile_spec_bundle
from agent.planning.validators.coder_compat import validate_coder_compatibility
from agent.coder.specs import load_spec_bundle_from_root
facts = Path('agent/facts/gold_facts/mqtt_min/protocol_facts.json')
target, _ = load_target_profile(Path('agent/planning/planning_target_profile_mqtt.json'))
planning_ir, _ = build_planning_ir(facts, target)
profile = build_protocol_profile(planning_ir)
constraints = activate_constraints(profile)
selected = select_architecture(current_architecture_candidates(planning_ir, profile, constraints), profile)
plan = current_implementation_plan(planning_ir, profile, constraints, selected)
out = Path('agent/planning/out/mqtt_step4_type_signature_closure_deterministic')
if out.exists():
    shutil.rmtree(out)
manifest, _ = compile_spec_bundle(plan, out)
diags = validate_coder_compatibility(manifest['spec_root'])
bundle = load_spec_bundle_from_root(manifest['spec_root'], validate_rendered_headers=True)
print(len([d for d in diags if d.level == 'error']))
print(len([d for d in bundle.diagnostics if d.level == 'error']))
PY
```

通过的检查：

```text
compileall passed。
Step 4 targeted unittest：174 tests passed。
planning/coder/validators unittest：187 tests passed。
MQTT planning validate：No diagnostics。
deterministic MQTT spec bundle：validate_coder_compatibility diagnostics=0 errors=0。
deterministic MQTT rendered header dummy TU gate：loader_errors=0。
输出 spec_root：agent/planning/out/mqtt_step4_type_signature_closure_deterministic/spec_bundle。
```

失败或未完成检查：

```text
fresh MQTT planning run：agent/planning/out/mqtt_step4_type_signature_closure_fresh。
结果：人工终止，进程 exit code 143。原因是 architecture generation 并发 LLM future 长时间无新输出；中断前无 Step 4 diagnostics。
补充验证：使用 deterministic MQTT current-flow plan 生成 spec_bundle，并完成 coder compatibility + rendered header dummy TU validation。
```

新增风险：

```text
1. public type obligation category requiredness 目前基于 role/profile/module capability text 与 planned type cues，后续 Step 5 可能需要把 semantic actionability 结果纳入 requiredness。
2. 合法 forward declaration policy 只允许 public opaque backing pointer；by-value concrete type、struct layout、enum、alias 不走 forward declare 兜底。
3. final readiness 只检查结构化 type refs / c_type / signatures / callback / alias / call contract signature，不解析任意 prose behavior 文本中的自然语言 type mentions。
4. fresh LLM planning run 本轮未完成，需下一轮或 CI 在外部 LLM 稳定时重跑。
```

Step 4 结论：

```text
Step 4 completed。
public type obligation report 已生成路径接入；signature/type-ref/callback/function-pointer/header ABI closure 已在 5.3、5.4b、5.7/coder compatibility 多层 fail-closed。
下一轮入口：Step 5 semantic actionability。
```

## 7. 风险与注意事项

### 7.1 更严格 validation 会暴露更多失败

预期现象：

```text
一些过去显示 success 的 planning run 会变成 failed。
```

这是正确结果。目标不是提高表面 success rate，而是消除 false success。

### 7.2 不要把 gold/example specs 当 protocol facts

gold/example specs 只能用于 coder usability 和 oracle diagnosis，不能作为 planning 必须复制的协议事实来源。

### 7.3 不要用清空字段修复 graph

禁止：

```text
calls_allowed=[]
imports_allowed=[]
dependency_graph.function_edges=[]
然后 validation passed
```

如果无法闭合，应 blocking。

### 7.4 不要过早进入 Step 6

Step 4 之后应进入 Step 5 semantic actionability。不要提前进入 Step 6 MQTT/CoAP/SMTP 多协议复现，否则会混入 semantic actionability 噪声。

## 8. 当前状态摘要

```text
当前完成度：Step 4 completed。
当前主线：下一轮进入 Step 5 semantic actionability。
当前最新验证：compileall passed；Step 4 targeted unittest 174 tests passed；planning/coder/validators unittest 187 tests passed；MQTT planning validate No diagnostics；deterministic MQTT spec bundle coder compatibility 与 rendered header dummy TU gate 均 0 errors。
当前最重要 blocker：fresh MQTT LLM planning run 本轮因 architecture generation 并发 LLM future 长时间无输出而人工终止；需在外部 LLM 稳定时重跑 fresh plan。
下一步：从 Step 5 semantic actionability 开始，不提前进入 Step 6 多协议复现。
```
