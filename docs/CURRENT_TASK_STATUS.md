# SpecForge Planning 稳定化当前任务状态

## 1. 当前总体阶段

当前目标：

```text
让 planning 输出能稳定驱动 coder compile/smoke，
并在 MQTT、CoAP、SMTP 的最小功能集上复现。
```

当前执行状态：

```text
Step 1 partially completed：dependency fallback fail-closed 与 cross-layer dependency diagnostics 已完成
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

### 2.2 当前已知 planning blocker

状态：

```text
已分析
```

主要 blocker：

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
Step 1：修复 P0 deterministic closure 与 readiness gate
```

目标：

```text
让 planning final success 不再绕过 deterministic coder compatibility failure。
```

涉及模块候选：

```text
agent/planning/stages/dependencies.py
agent/planning/stages/implementation_plan_merger.py
agent/planning/stages/specs_compiler.py
agent/planning/stages/coder_spec_lowering.py
agent/planning/validators/coder_compat.py
agent/planning/validators/coder_semantics.py
agent/coder/specs.py
agent/coder/generation.py
agent/coder/header_recipes.py
相关 tests / fixtures
```

本步骤任务清单：

```text
[x] dependency fallback fail closed
[x] cross-layer dependency consistency diagnostics
[ ] 分离 header_public_deps 与 source_call_deps
[ ] public type dependency lowering
[ ] system type registry
[ ] final rendered header validation strict gate
[ ] dummy header translation unit compile
[x] Step 1 dependency fixture tests
[ ] MQTT sample validation rerun
[x] 当前状态文件更新
```

## 4. Step 1 完成指标

只有同时满足以下条件，才能标记 Step 1 完成：

```text
[x] 新增 fail-closed dependency tests 通过。
[ ] system type fixture 通过 rendered header validation。
[ ] external public type fixture 通过 rendered header validation。
[ ] final planning readiness 启用 strict rendered header gate。
[ ] dummy header translation unit compile 已接入或可由 readiness 调用。
[ ] 旧 MQTT planning sample 的 header closure failure 不再被 final success 漏掉。
[ ] 若 fresh MQTT planning 失败，失败原因是 blocking diagnostic，而非 silent false success。
[x] 本文件已更新，记录命令、结果、剩余 blocker。
```

## 5. 建议下一轮 Codex 会话入口

下一轮会话建议执行：

```text
请阅读 AGENT.md、PLANNING_STABILIZATION_TASKS.md 和 CURRENT_TASK_STATUS.md。
当前应执行 Step 1：修复 P0 deterministic closure 与 readiness gate。
先审计 dependencies.py、implementation_plan_merger.py、specs_compiler.py、coder_compat.py、coder/specs.py 中与 dependency fallback、HEADER/SOURCE dependency lowering、rendered header validation 相关的代码。
不要重写全流程，不要压缩 strict schema，不要关闭 validator。
完成后添加最小 fixture tests，并更新 CURRENT_TASK_STATUS.md。
```

## 6. 会话记录模板

每次 Codex 会话结束时，在本节追加一条记录。

### Session 000

日期：

```text
未开始
```

本轮目标：

```text
初始化三份任务文件，尚未执行代码修改。
```

已修改文件：

```text
AGENT.md
PLANNING_STABILIZATION_TASKS.md
CURRENT_TASK_STATUS.md
```

已运行命令：

```text
无
```

检查结果：

```text
无
```

未完成项：

```text
Step 1 尚未开始。
```

下一轮入口：

```text
从 Step 1 的 dependency fallback fail closed 与 rendered header validation 审计开始。
```

### Session 001

日期：

```text
2026-06-15
```

本轮目标：

```text
只执行 Step 1 的 dependency fallback fail-closed 与 cross-layer dependency consistency diagnostics。
```

已修改文件：

```text
agent/planning/stages/dependencies.py
agent/planning/stages/implementation_plan_merger.py
agent/planning/validators/dependencies.py
agent/planning/tests/test_implementation_plan_stage_candidates.py
docs/CURRENT_TASK_STATUS.md
```

已完成内容：

```text
1. apply_deterministic_dependency_fallback 不再清空 calls_allowed/imports_allowed。
2. dependency repair mark_unresolved 与 deterministic fallback 产生 blocking unresolved dependency diagnostic。
3. validate_dependency_graph 增加 call_contracts/calls_allowed/function_edges 一致性检查。
4. validate_dependency_graph 增加 signature_dependencies/imports_allowed/provider file 一致性检查。
5. validate_dependency_graph 增加 module_artifacts[].dependencies unknown module 检查。
6. dependency_validation_report summary 增加 calls_allowed、call_contracts、signature_dependencies、imports_allowed 计数。
```

已运行命令：

```text
python -m unittest agent.planning.tests.test_implementation_plan_stage_candidates.ImplementationPlanStageCandidateTests.test_dependency_validation_rejects_call_contracts_without_calls_allowed agent.planning.tests.test_implementation_plan_stage_candidates.ImplementationPlanStageCandidateTests.test_dependency_validation_rejects_signature_dependencies_without_imports agent.planning.tests.test_implementation_plan_stage_candidates.ImplementationPlanStageCandidateTests.test_dependency_validation_accepts_consistent_call_and_signature_graph agent.planning.tests.test_implementation_plan_stage_candidates.ImplementationPlanStageCandidateTests.test_dependency_fallback_preserves_inputs_and_marks_blocking
python -m unittest agent.planning.tests.test_implementation_plan_stage_candidates
python -m unittest agent.planning.tests.test_coder_schema_lowering
```

检查结果：

```text
新增 4 个 dependency regression tests 通过。
agent.planning.tests.test_implementation_plan_stage_candidates：115 tests passed。
agent.planning.tests.test_coder_schema_lowering：23 tests passed。
```

未完成项：

```text
1. HEADER.DEPENDENCY 与 SOURCE.DEPENDENCY 来源仍待分离。
2. public type dependency lowering 仍待补齐。
3. system type registry 与 HEADER.SYSTEM_DEPENDENCY lowering 仍待补齐。
4. final rendered header validation strict gate 仍待接入。
5. dummy header translation unit compile 仍待接入。
6. MQTT fresh/sample planning validation rerun 尚未执行。
```

下一轮入口：

```text
继续 Step 1 的 header_public_deps/source_call_deps 分离、public/system type closure、rendered header strict validation。
```

### Session 002

日期：

```text
2026-06-15
```

本轮目标：

```text
修复 review 发现的 signature dependency provider file 误推断问题。
```

已修改文件：

```text
agent/planning/validators/dependencies.py
agent/planning/tests/test_implementation_plan_stage_candidates.py
docs/CURRENT_TASK_STATUS.md
```

已完成内容：

```text
1. 不再用 module 的 first file 猜测 signature dependency provider file。
2. 只有 exports_type_ids 能明确定位 provider file 时才检查跨文件 imports_allowed。
3. 跨模块但无法定位 provider file 仍产生 blocking diagnostic。
4. 同模块未导出的 source-local signature dependency 不再被误判为缺 import。
```

已运行命令：

```text
python -m unittest agent.planning.tests.test_implementation_plan_stage_candidates.ImplementationPlanStageCandidateTests.test_dependency_validation_rejects_signature_dependencies_without_imports agent.planning.tests.test_implementation_plan_stage_candidates.ImplementationPlanStageCandidateTests.test_dependency_validation_accepts_consistent_call_and_signature_graph agent.planning.tests.test_implementation_plan_stage_candidates.ImplementationPlanStageCandidateTests.test_dependency_validation_allows_same_module_unexported_signature_dependency
python -m unittest agent.planning.tests.test_implementation_plan_stage_candidates agent.planning.tests.test_coder_schema_lowering
git diff --check
```

检查结果：

```text
定向 3 个 dependency tests passed。
agent.planning.tests.test_implementation_plan_stage_candidates + test_coder_schema_lowering：139 tests passed。
git diff --check clean。
```

未完成项：

```text
同 Session 001：header/source dependency lowering、public/system type closure、rendered header strict validation、dummy header TU compile、MQTT fresh/sample validation rerun 仍待后续处理。
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

在 MQTT 的 deterministic closure/readiness 未修复前，不建议直接扩展 CoAP/SMTP。否则新协议会产生更多噪声，难以区分协议差异和 planning bug。

## 8. 当前状态摘要

```text
当前完成度：Step 1 partially completed，dependency fallback/cross-layer dependency diagnostics 已完成。
当前主线：Step 1 P0 deterministic closure / readiness gate。
当前最重要 blocker：header/source dependency lowering、public/system type closure、rendered header strict validation 尚未闭合。
下一步：修复 HEADER.DEPENDENCY 与 SOURCE.DEPENDENCY 来源混用、system type registry、rendered header strict validation。
```
