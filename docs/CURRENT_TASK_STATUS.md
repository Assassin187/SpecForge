# SpecForge Planning 稳定化当前任务状态

## 1. 当前总体阶段

当前目标：

```text
让 planning 输出能稳定驱动 coder compile/smoke，
并在 MQTT、CoAP、SMTP 的最小功能集上复现。
```

当前执行状态：

```text
Step 3 completed：planning architecture、file layout、runtime entrypoint、MODULES/FILES、HEADER/SOURCE dependency 已建立 fail-closed mapping/closure validation。
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
Step 3：File Layout 与 Runtime Mapping 闭合（已完成）
```

目标：

```text
让 planning architecture、file layout、runtime entrypoint 和 dependency closure 相互兼容。
目标不是复制 gold/example layout，而是让当前 planning 自己生成的 target-profile-specific module/file/runtime structure 可解释、可编译、可被 coder 使用。
```

涉及模块候选：

```text
agent/planning/stages/implementation_plan_merger.py
agent/planning/stages/layout_runtime_mapping.py
agent/planning/orchestrator.py
agent/planning/validators/implementation_plan_stages.py
agent/planning/validators/coder_semantics.py
agent/coder/specs.py
agent/coder/generation.py
相关 tests / fixtures
```

本步骤任务清单：

```text
[x] runtime stage 不再隐式创建 create/start/run/destroy lifecycle function
[x] app/broker artifact seed 显式包含 *_start，并移除提前生成 main 的 seed
[x] runtime entrypoint candidate/final readiness 校验 lifecycle function existence/public API/import closure
[x] 新增 layout_runtime_mapping_report/v1 并写入 009_layout_runtime_mapping_report.json
[x] layout/runtime mapping report 接入 5.7 readiness 与 specs_compile resume gate
[x] coder loader blocking unknown MODULES[].FILES、unknown MODULES[].DEPENDENCIES、duplicate HEADER/SOURCE path
[x] coder runtime main rendering 改为读取 generated main.c FILE_SPEC、SOURCE.DEPENDENCY 与 existing lifecycle signatures
[x] Step 3 regression tests
[x] MQTT validate + specs_compile resume 验证 layout/runtime/header/module closure
[x] 当前状态文件更新
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

## 5. 建议下一轮 Codex 会话入口

下一轮会话建议执行：

```text
请阅读 AGENT.md、PLANNING_STABILIZATION_TASKS.md 和 CURRENT_TASK_STATUS.md。
Step 3 已完成。下一轮固定进入 Step 4：type inventory 与 function signature closure。
建议优先使用 agent/planning/out/mqtt_step3_layout_runtime_resume/spec_bundle 与 009_layout_runtime_mapping_report.json 作为输入基线。
不要修改 Step 5 semantic actionability，也不要提前进入 Step 6 多协议复现。
不要回退 Step 1/2/3 strict validators，不要压缩 strict schema，不要关闭 rendered header validation。
```

## 6. 会话记录摘要

本节只保留后续执行需要的 Step 1 压缩历史。早期 Session 000-009 的逐轮修复细节已合并，避免干扰后续 coder compile/smoke 与多协议复现。

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

### 7.4 不要过早进入 Step 5 / Step 6

Step 3 之后应先做 Step 4 type inventory 与 function signature closure。不要提前进入 Step 5 semantic actionability 或 Step 6 MQTT/CoAP/SMTP 多协议复现，否则会混入 type/signature closure 噪声。

## 8. 当前状态摘要

```text
当前完成度：Step 3 completed。
当前主线：下一轮进入 Step 4 type inventory 与 function signature closure。
当前最新验证：agent/planning/out/mqtt/broker__c__linux_epoll__minimum_v1/20260616_231307_313900 从 implementation_plan_5_2b 续跑后，Step 3 相关 file layout、runtime entrypoint、dependency、layout/runtime mapping reports 均通过；run 失败于 specs_compile 的 rendered header compile gate。
当前最重要 blocker：Step 4 type inventory 与 function signature/header declaration closure 尚未闭合，最新失败集中在 protocol_codec public header circular include/type declaration order。
下一步：从 Step 4 type inventory 与 function signature closure 开始，不提前进入 Step 5 semantic actionability 或 Step 6 多协议复现。
```
