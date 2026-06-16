# SpecForge Planning 稳定化当前任务状态

## 1. 当前总体阶段

当前目标：

```text
让 planning 输出能稳定驱动 coder compile/smoke，
并在 MQTT、CoAP、SMTP 的最小功能集上复现。
```

当前执行状态：

```text
Step 2 completed：cross-layer dependency validator 已接入 readiness，dependency report 已补齐 machine-readable provenance arrays，coder-facing HEADER/SOURCE dependency path validation 已接入。
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
Step 2：修复 cross-layer dependency consistency validator（已完成）
```

目标：

```text
让 dependency/call/header/source/module dependency 表达不会互相漂移，并让 validation report 能解释每条 dependency/call edge 来源。
下一步进入 coder generate/compile/smoke，并准备 MQTT、CoAP、SMTP minimum 多协议复现。
```

涉及模块候选：

```text
agent/planning/stages/dependencies.py
agent/planning/orchestrator.py
agent/planning/validators/coder_compat.py
agent/coder/specs.py
相关 tests / fixtures
```

本步骤任务清单：

```text
[x] cross-layer dependency consistency validator 接入 5.7 / final validation
[x] dependency_validation_report 新增 errors / warnings / dependency_sources / call_edge_sources
[x] specs_compile 后回填 header_dep_sources / source_dep_sources
[x] coder loader 显式校验 HEADER.DEPENDENCY / SOURCE.DEPENDENCY header path 存在性
[x] Step 2 report fixture tests
[x] MQTT specs_compile resume 验证新 report provenance arrays
[x] 当前状态文件更新
```

## 4. Step 1 / Step 2 完成指标

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

## 5. 建议下一轮 Codex 会话入口

下一轮会话建议执行：

```text
请阅读 AGENT.md、PLANNING_STABILIZATION_TASKS.md 和 CURRENT_TASK_STATUS.md。
Step 2 已完成。下一轮从 MQTT coder generate/compile/smoke 开始，建议优先使用 agent/planning/out/mqtt_step2_dependency_report_rerun/spec_bundle 作为输入基线；也可回退使用 agent/planning/out/mqtt_step1_main_rerun_20260616_1600/spec_bundle。
不要回退 Step 1 strict validators，不要压缩 strict schema，不要关闭 rendered header validation。
若 MQTT coder compile/smoke 通过，再进入 CoAP/SMTP minimum fresh planning 复现。
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

### 7.4 不要过早进入多协议扩展

在 MQTT 的 coder compile/smoke 未验证前，不建议直接扩展 CoAP/SMTP。否则新协议会产生更多噪声，难以区分协议差异、coder compile 问题和 planning bug。

## 8. 当前状态摘要

```text
当前完成度：Step 2 completed。
当前主线：用 Step 2 successful specs_compile 输出驱动 coder generate/compile/smoke，并准备 MQTT、CoAP、SMTP minimum 复现。
当前最新验证：agent/planning/out/mqtt_step2_dependency_report_rerun 已进入 specs_compile，生成 coder_manifest.json + spec_bundle/，planning verify 与 coder validate 均无 diagnostics，008_dependency_validation_report.json 已包含 provenance arrays。
当前最重要 blocker：尚未执行 coder generate/compile/smoke，未知该 spec_bundle 是否能驱动最终协议实现编译和 smoke 通过。
下一步：使用 agent/planning/out/mqtt_step2_dependency_report_rerun/spec_bundle 运行 MQTT coder generate/compile/smoke。
```
