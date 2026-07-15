# Planning Final Root-fix Tranche 任务书

> 初次制定：2026-07-15  
> 本次重构：2026-07-15  
> 当前状态：`DONE（final negative stability result）`  
> 当前步骤：`R7 COMPLETED；永久停止本优化方向`  
> 历史 Candidate-through tranche：`DONE（3-run sequence archived）`  
> 本 tranche：`DONE（恰好 3 次 frozen fresh sequence 已归档）`  
> 核心对象：planning agent  
> 执行边界：修复 compile-critical contract root causes，并执行一次最终 frozen pilot；不执行正式 RQ1  
> 最终 fresh 预算：所有修改冻结后，恰好 3 次 independent fresh planning → coder sequence

## 0. 文档语义与硬约束

### 0.1 权威输入

每次继续本任务前必须读取：

1. 本任务书；
2. `PLANNING_CURRENT_STATUS_REPORT.md`；
3. `docs/specs_optimization/04_planning_vs_gold_oracle_diagnosis.md`；
4. `docs/specs_optimization/05_specs_optimization_decision_report.md`；
5. 历史 sequence 的 `freeze_record.json`、`sequence_summary.json` 和三个 run 的 diagnostics/repair logs；
6. 当前 `git status`、`git diff --stat` 与相关 diff。

若文字报告中的中间结论与最终 sequence artifacts 冲突，以 `sequence_summary.json`、每个 `summary.json` 和原始 compiler logs 为准。本版已压缩原 Step 0–8；旧执行细节由历史 artifacts 继续保存，不再在任务书重复。

旧任务书“三次结束后不再修复”的停止语义已完成其原 sequence。本次 root-fix 是用户在看到该 negative compile-stability result 后明确授权的唯一例外；它不授权无限追加 tranche。

### 0.2 执行规则

- 同一时刻只有一个 Step 为 `IN_PROGRESS`；完成门禁后才能进入下一 Step。
- R0–R5 只允许 historical replay、fixtures、unit/integration tests 和 deterministic compile diagnostics；不得启动 fresh planning，不得产生 planning model call。
- 所有代码、测试、measurement 修改在 R5 完成后冻结；只有冻结后才能进入 R6。
- R6 恰好启动 3 次 fresh planning：`fresh=true`、`resume=false`、`replay=false`，均从 Stage 1 开始并使用独立目录。
- fatal、无 specs、coder validate/generation/compile/sound-build failure 都占用一个 slot；不补跑、不替换、不挑选。
- 三次之间不得修改 planning、compiler、coder、adapter、prompt、facts、schema、budget、toolchain 或 measurement code。
- 不人工修改 planning specs、coder original code 或 repair code。
- R7 只归档和判定。无论结果正负，本任务均停止；后续正式 RQ1 必须使用独立实验计划。

Historical artifacts 全程只读。允许将其复制到临时目录做 0-fresh、0-model-call replay。

### 0.3 冻结的 Candidate-through 与 original-code contract

Candidate-through eligibility 保持上一 tranche 的定义：

```text
physical specs_root exists
module spec count >= 1
file spec count >= 1
function spec count >= 1
```

满足后，无论 `qualification_passed` 或 semantic diagnostic count，均调用 coder validate；qualification 不得改写。Coder validate passed 后，先以 `--skip-repair generate` 生成 `coder_original/`，记录 `.c/.h/Makefile` hashes 和 initial clean compile，再在独立 copy 中执行最多 3 rounds repair；repair 前后 original hashes 必须一致。

本 tranche 不把 Candidate-through 放宽为 readiness。新增并分别记录：

- `compile_contract_ready`：进入 coder 前不存在 unresolved compile-critical contract；
- `clean_compile_passed`：固定 clean build 的 return code 为 0；
- `sound_build_passed`：clean compile 通过，同时 shared ABI/type/ownership audit 通过且没有 high-risk warnings。

## 1. 已完成的 Candidate-through 稳定化基线

### 1.1 已完成修改压缩摘要

| 已完成能力 | 已冻结结果 |
| --- | --- |
| Candidate-through orchestration | qualification 与 downstream eligibility 解耦；physical nonempty specs 一律进入 coder validate |
| Coder evidence preservation | original generation、pre-repair diagnostics、独立 repair copy、source hash preservation 已形成统一 contract |
| Registry/materialization | create/destroy ABI 可唯一推导的 opaque handle 进入 canonical registry；旧 `type:lowering/..._t` invariant 已消失 |
| Implementability | 增加 direct-call signature、ordered typed argument provider 与 access diagnostics |
| Budget edge | preflight 阻止新 model call 后，最后 committed state 仍会 deterministic completion 并尝试 materialize |
| Offline gates | planning `151/151`、planning utility `34/34`、coder `29/29`，`compileall` 与 `git diff --check` 通过 |
| Frozen sequence | materialization `3/3`、coder validate `3/3`、original preserved `3/3`、final clean compile `1/3` |

上一 tranche 未修改 coder prompt，也未复制 `specs-example`、gold inventory 或 MQTT-specific implementation logic。其 root fix 解决的是“pipeline 能否稳定产出并进入 coder”，没有证明 specs 的 compile-critical contracts 已闭合。

### 1.2 Frozen 三次运行结果

| Run | Stages / partitions | Specs M/F/Fn | Qualified | Initial compile | Repair | Final compile | Planning tokens |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 11/11；15/16 | 1/7/29 | false | failed(rc=2) | 2/3；`compile_succeeded` | passed | 606486 |
| 2 | 11/11；16/18 | 1/6/33 | false | failed(rc=2) | 3/3；`max_rounds_exhausted` | failed | 684815 |
| 3 | 11/11；16/20 | 1/9/36 | false | failed(rc=2) | 3/3；`max_rounds_exhausted` | failed | 763558 |

Aggregate：physical specs `3/3`，无 deterministic internal invariant `3/3`，coder validate/original preservation `3/3`，qualification `0/3`，initial compile `0/3`，final compile `1/3`，repair rounds `[2,3,3]`。Run 3 超过 749726 target，但低于 783804 hard ceiling；三次 accounting 均 complete。

旧 adapter 的 `initial_compile_status` 读取 legacy flat key；权威初始结果位于 `pre_repair_diagnostics.json -> diagnostic_snapshot.build.returncode`，三次均为 2。该 measurement defect 未改变旧 sequence 分类，但必须在 R4 冻结前修复。

### 1.3 修复前后问题性质变化

```text
修复前：registry/materialization/control-flow deterministic failure
修复后：materialization stable；失败面下移到 C type、representation、provider graph 与 repair convergence
```

因此，当前问题与旧问题有实质区别：旧问题会阻止 specs/coder 路径发生；当前问题发生在已 materialize 的 specs 与跨文件 C contract 之间。它仍有一组可确定性修复的共同根因，但不能通过“已有 1/3 link pass”宣称稳定，也不值得继续开放式 prompt tuning。本 tranche 只做一次有明确停止条件的 root fix。

与旧 frozen Step 10 相比，materialized/coder-started 从 `2/3` 提升到 `3/3`，final compile 仍为 `1/3`：这证明上游 deterministic root fix 有效，但 compile contract rate 没有改善。同口径 pre-repair 下 M0、M1 均为 `0/10`，当前 M2 也为 `0/3`；历史 bounded repair 中 M0/M1 为 `0/10`，当前 M2 native repair 为 `1/3`，但 repair 算法/单位不同、M2 样本仅 3 且唯一 pass 不 sound。因此现阶段只能表述“观测到 M2 compile feasibility”，不能表述统计显著优于 M0/M1。

## 2. 当前 Root-cause Map

### 2.1 三个 run 的具体失效面

| Evidence | 具体问题 | 为什么不是随机单文件错误 | Root-fix layer |
| --- | --- | --- | --- |
| Run 1 | `network/connection.c`、decoder、encoder、broker 分别完整定义不同 layout 的 `struct buffer`；final stderr 还有 `-Wfree-nonheap-object` 和多处 `-Wincompatible-pointer-types` | linker 不校验跨 translation unit layout；embedded input buffer 被通用 destroy/free，当次“compile passed”是 structural false positive | type/representation/ownership audit |
| Run 2 | public API 以 value 返回 `struct iovec`，但 header 未声明 `sys/uio.h`；broker 连续调用不存在的 `mqtt_connection_*user_data`、`mqtt_session_lookup_by_id`、`mqtt_session_get_connection` | planning diagnostics 已包含 3 个 `call_argument_source_unresolved` 和 1 个 `runtime_call_chain_incomplete`；repair 只是在 closed-world 外改名猜 API | system header + provider/runtime graph closure |
| Run 3 | public transport API 返回 `struct mqtt_buffer_s *`，完整 layout 只存在于 private `.c`；decoder/broker 跨 owner 解引用 `data/len`，但 `TYPE_SPEC`、`ACCESS_PATHS`、`RELY.STRUCT` 未建立公开表示或 accessor | 旧 `mqtt_transport_t` registry failure 已修复；新失败暴露 tagged struct 未被 type parser/closure 覆盖 | tagged type + opaque/public access discipline |

Run 1 的 raw link success 不计为 sound build：不同 translation unit 对同一 shared tag 的认识不兼容，且 compiler 已明确给出 invalid-free/incompatible-pointer warnings。后续所有 readiness 判断以 `sound_build_passed` 为准，同时保留 raw compile rate 便于与旧结果对照。

### 2.2 当前代码机制

| 当前代码 | 机制缺口 | 对应后果 |
| --- | --- | --- |
| `agent/planning/compiler.py::_CUSTOM_TYPE` | regex 只提取 `*_t` | 漏掉 `struct/union/enum <tag>` 与外部 tagged type |
| `agent/planning/implementability.py::_TYPE_TOKEN` | 同样只识别 `*_t`，type compatibility 未完整表达 qualifiers/use mode | `struct buffer`、`struct mqtt_buffer_s` 不进入 canonical dependency/access checks |
| `agent/coder/generation.py::_collect_std_headers` | 仅覆盖 bool、size_t、fixed-width integer | `struct iovec` 等 system type 没有确定性 header mapping |
| `agent/planning/pipeline.py` unresolved branch | 任一 `unresolved_partitions` 即跳过 `_close_implementability`，并写 `semantic_patch_attempted=false` | 三个 run 均有 unresolved partition，已发现的 compile-critical diagnostics 未获得 bounded closure |
| `agent/coder/generation.py::_classify_repair_targets` | header error 不修、source error交给 LLM；undefined symbol 未与 canonical symbol set 对照 | shared header/API defect被误当作 source repair，模型反复发明 accessor |
| `agent/coder/generation.py::_repair_until_compiles` | 只在出现新 linker roots 时 rollback；不要求 error fingerprint/root count下降 | 算法保证最多 3 rounds 后停止，但不保证单调进展或收敛 |

### 2.3 Root families 与修复责任

```text
RF1  canonical C type universe + system header completeness
RF2  shared representation + opaque access + ownership/ABI
RF3  closed-world call argument/provider/callback/runtime graph
RF4  unresolved-partition isolation + compile-critical targeted closure
RF5  repair error-fingerprint monotonicity + sound-build measurement
```

RF1–RF4 属于 planning/compiler 的主要责任；RF5 只做 protocol-agnostic coder hardening 和 measurement，不用 coder 猜补 planning API。若完成本 tranche 后同一 RF 再次跨 run 复现，应视为该路线在当前架构下没有收敛，而不是继续加 prompt。

## 3. 唯一目标、完成定义与非目标

### 3.1 唯一目标

在不扩大 schema、不注入 MQTT-specific inventory、不大改 prompt 的前提下，使 compile-critical contracts 在 planning candidate 中可被完整识别、唯一闭合或明确阻断，并让 coder repair 只接受可证明减少 compile roots 的修改；随后用一次 frozen 3-run pilot 判断该优化是否收敛。

目标不是追求样本内 3/3，而是回答：对最近三次暴露的共同 root families 做一次确定性修复后，M2 是否达到足以进入独立 formal RQ1 的工程稳定性。

### 3.2 Compile-critical 与 sound build 定义

以下 family 默认是 compile-critical：unresolved/cross-file private type、missing system/header dependency、opaque representation/access gap、call argument provider unresolved、unresolved callee、callback mismatch、runtime call chain incomplete、shared ownership/lifecycle contradiction。诊断必须带 `authoritative_stage`、affected artifact IDs 和 recovery action。

`sound_build_passed=true` 必须同时满足：

1. clean build return code 为 0；
2. public headers 的 isolated include 与 by-value completeness probes 通过；
3. shared public/tagged type 只有一个 canonical complete representation，跨 translation unit audit 无冲突；
4. 不存在 `free-nonheap-object`、`incompatible-pointer-types`、implicit function declaration、incompatible public declaration 等 high-risk warnings；
5. original source hashes仍 preserved。

unused function/variable 等非安全 warning 继续记录，但不单独否决 sound build。Sound build 仍只是 compile/structural endpoint，不代表 runtime、MQTT behavior 或 interoperability correctness。

### 3.3 非目标

- 正式 RQ1 M0/M1/M2 replicates；
- runtime startup、behavior、interoperability 或 protocol correctness 优化；
- 多协议泛化结论；
- schema 扩展、gold/specs-example 复制或 MQTT-specific规则；
- 为达到 3/3 而增加 repair rounds、replacement runs 或 run 间修改；
- 大规模 prompt 重写、token 换稳定性或第三轮开放式调参；
- 把 qualification/diagnostic severity 改写为通过。

## 4. 修改边界、优先级与预算

### 4.1 默认允许修改

```text
agent/planning/compiler.py
agent/planning/implementability.py
agent/planning/pipeline.py
agent/planning/tests/
agent/coder/specs.py
agent/coder/generation.py
agent/coder/verifier.py（仅 sound-build audit 无法复用现有 compile path 时）
agent/coder/tests/
evaluation/planning_utility/full_specforge_adapter.py
evaluation/planning_utility/no_repair_analysis.py
evaluation/planning_utility/repair_diagnostics.py（仅复用现有 diagnostics 时）
evaluation/planning_utility/tests/test_planning_utility.py
evaluation/planning_utility/tests/test_no_repair_analysis.py
evaluation/planning_utility/README.md
本任务书与 PLANNING_CURRENT_STATUS_REPORT.md
```

只有 fixture 证明 canonical identity 不能在现有结构表达时，才最小修改 `registry.py` 或 `planner.py`。优先替换现有 `_CUSTOM_TYPE`、`_TYPE_TOKEN`、dependency completion 和 repair acceptance 逻辑，不新增平行 parser/repair pipeline。

### 4.2 默认只读

```text
agent/facts/
specs-example/
specs_schema/
protocol-example/
planning prompts
历史 planning/coder artifacts
历史 generated C/H
MQTT obligation rubric 与 behavior verifier
```

Prompt 是最后手段。若 deterministic fixtures 全部通过后仍能证明最早 authoritative stage 缺失必要结构，最多做一次 deletion/replacement 式、单 field-family 修改，并记录 before/after tokens；不得新增重复全量 context。默认预期本 tranche 无 prompt 修改。

### 4.3 Frozen token 与 repair budget

```text
planning target <= 749726
planning hard ceiling <= 783804
single request input <= 64000
single response <= 16000
JSON repair <= 1 per failed response
semantic primary patch <= 1 per fresh run
semantic correction <= 1 for the same selected slice
coder repair <= 3 rounds
```

超过 target 仍计入 run 并标记；hard ceiling 不提高。Budget preflight 阻止下一 call 时，必须保留最后 committed state 和 diagnostics。

## Step R0：冻结历史证据并提取 deterministic fixtures

**状态：COMPLETED（2026-07-15；0 fresh / 0 model call）**

### 任务

1. 记录当前 HEAD、dirty execution diff hash、facts/schema/prompt/coder/adapter hashes 与 toolchain。
2. 固定旧 `freeze_record.json`、`sequence_summary.json`、三个 planning specs、validated diagnostics、original/repair manifests 和 compile logs 的 hashes。
3. 建立五组 protocol-agnostic fixtures：
   - 同名 tagged struct 在多个 translation unit 出现不同 layout；
   - embedded/borrowed value 被 owning destructor/free；
   - public by-value `struct iovec` 缺少 required system header；
   - opaque tagged pointer 被 foreign owner 解引用且无 public TYPE_SPEC/accessor；
   - caller 需要 ID→session→connection，但 canonical provider graph 缺边并调用不存在 accessor。
4. 增加 pipeline fixture：存在一个无关 unresolved partition 时，committed artifacts 仍执行 compile-critical closure。
5. 增加 repair fixture：连续两轮错误 root 不下降、以及 undefined symbol 不在 canonical public symbol set。

### 完成门禁

```text
all fixtures reproduce the intended root family before fix
historical artifact hashes unchanged
no protocol-specific implementation names in generic test logic
no fresh directory and no model call
```

## Step R1：Canonical C Type 与 Public Header Closure

**状态：COMPLETED（2026-07-15）**

### 最小修改

1. 统一现有 type extraction/normalization，覆盖 typedef name、`struct/union/enum <tag>`、callback、system type、qualifier、pointer depth 和 array/function parameter spelling；不再以 `*_t` 作为 custom type 唯一入口。
2. 为每次 type use 标记 `pointer_only`、`by_value`、`field_storage` 或 `requires_complete_representation`；opaque pointer 可 forward declare，by-value/field/dereference 必须可证明 complete。
3. Type compatibility 允许 `T * -> const T *` 等安全 qualifier widening，不允许反向转换、不同 tag 或不同 pointer depth被误判兼容。
4. 建立小型 protocol-agnostic system-type→header mapping，至少覆盖当前已出现的 `struct iovec -> sys/uio.h`，并保留现有 stdint/stdbool/stddef/socket mappings；public signature/field 使用时 deterministic completion 写入 `HEADER.SYSTEM_DEPENDENCY`。
5. 扩展 rendered-header gate：除 isolated include TU 外，对 public by-value/field type生成 completeness probe；pointer-only opaque type不做 `sizeof`。
6. Coder loader 做 defensive validation；compiler不得用 private fake definition掩盖缺失 system header。

### Tests 与门禁

- `struct buffer`、`struct mqtt_buffer_s` 会进入 canonical type refs；ordinary identifiers 不误报。
- `struct iovec` by-value 缺 `sys/uio.h` 必须失败，completion 后 header check 通过。
- opaque pointer forward declaration通过；opaque by-value或foreign dereference失败。
- qualifier/pointer compatibility正反例通过。
- 所有错误包含 owner header、use mode、required header/type 和 authoritative stage。

## Step R2：Shared Representation、Access Provider 与 ABI Closure

**状态：COMPLETED（2026-07-15）**

### 最小修改

1. 同一 canonical shared type 只允许一个 complete layout owner；其他文件只能 include canonical public representation，或使用 opaque declaration。
2. 跨 owner 读取/写入 opaque state 必须二选一：
   - public `TYPE_SPEC` 提供 canonical complete representation；
   - type 保持 opaque，并由 owner提供 canonical typed accessor/data-view API。
3. Compiler 只能补全由现有 canonical artifacts 唯一推出的 dependency/visibility projection；不得发明 accessor、改变 ownership 或选择新的 API family。
4. Direct call、callback 和 runtime chain 的每个参数必须有顺序、type、pointer/value、nullability 与 ownership 可证明的 provider；router 若只返回 ID，则 ID→session→connection 的服务必须真实存在，或 authoritative stage 改为返回已有 typed handle。
5. 所有 external calls 必须属于 module/file public symbols、canonical function registry 和 `CALL_CONTRACTS/RELY.FUNC` 的 closed world；unknown accessor 直接形成 `spec_contract_gap`。
6. Lifecycle audit 区分 embedded、borrowed、owned heap resource；同一 destructor不能同时对 embedded address 与 owned pointer执行 free semantics。
7. 增加 generated-source structural audit：shared public tag 在非 owner source 中出现 complete redefinition或不一致 layout时，raw link success 也不得成为 sound build。

### Tests 与门禁

- Run 1 fixture 在 specs/structural audit阶段捕获 duplicate/incompatible representation 与 embedded free风险。
- Run 2 fixture捕获四个不存在的 access/provider services，不能通过改名消除。
- Run 3 fixture只能由 public TYPE_SPEC或真实 typed accessor contract闭合。
- 合法 opaque pass-through、callback dispatch 和 private local tag不误报。
- 每个 deterministic change有 provenance；每个 ambiguous gap保留 typed diagnostic。

## Step R3：隔离 Unresolved Partition 并执行 Compile-critical Closure

**状态：COMPLETED（2026-07-15）**

### 最小修改

1. 移除 `if any unresolved_partitions -> skip _close_implementability` 的全局短路；先对 committed subset统一执行 normalization、deterministic dependency/type completion 和 diagnostics。
2. Unresolved partition 只冻结自身 artifacts；不得阻止无关 committed modules/files/functions进入 semantic closure。
3. 对 diagnostics 分类 `compile_critical` 与 `noncritical`。从 RF1–RF3 中选择最早 `authoritative_stage` 的 bounded slice；每个 fresh run 最多一次 primary semantic patch，以及针对同一 slice 的一次 correction。无 model budget时保留 diagnostic并继续 materialize。
4. Targeted correction只能更新 diagnostic slice中的既有 canonical artifacts或显式新增其要求的 access service；必须经过 registry、closed-world、type/provider 和 patch scope validation。
5. 一次 correction 后仍 unresolved 时停止，不按 diagnostic groups继续扩张 calls，也不进行全 plan prompt重写；如实记录 `semantic_patch_attempted`、recovery result和 residual roots。
6. Candidate-through contract保持不变：physical specs非空仍进入 coder；但 `compile_contract_ready=false`，不得伪装 qualification/readiness。

### Tests 与门禁

- unrelated unresolved partition 不再使 committed subset 的 closure usage恒为0。
- 每个 fresh workflow最多一组 primary+correction；mock usage accounting准确。
- ambiguous accessor/provider不会被 compiler自行发明。
- budget preflight、resume/replay、fatal/no-specs和旧 materialization fixtures无 regression。

## Step R4：Repair 单调性与 Sound-build Measurement

**状态：COMPLETED（2026-07-15）**

### 最小修改

1. 将 compile output归一化为稳定 error fingerprints：path-independent root family、symbol/tag、owner与severity；保留 raw logs。
2. Repair 前先对 undefined external symbol与 canonical public symbol set做 closed-world 对照。若 symbol不存在，停止为 `spec_contract_blocked`，不让 source repair猜 accessor。
3. Header/type/ABI/provider root继续由 planning/specs负责；coder只修复 canonical contract允许的 `.c` local implementation error，不新增 repair-all/header rewrite fallback。
4. 每轮 candidate只有在 compile-root度量严格下降、且没有新增同级或更高 severity root family时才接受；否则 rollback。
5. 相同 fingerprint重复或一轮无严格进展时立即 `stagnation_stop`；仍保留 `max_repair_rounds=3` 作为上限，不以耗尽 rounds冒充收敛。
6. 优先在现有 planning-utility measurement/compile diagnostics path 增加 high-risk warning和shared-type structural audit，不侵入 source generation；分别记录 raw `compile_status`、`sound_build_passed` 与 `sound_build_diagnostics`。
7. 修复 adapter 对 nested `diagnostic_snapshot.build.returncode` 的读取，冻结 machine-readable字段：initial/final raw compile、fingerprints、sound diagnostics、stop reason、original hash preservation。

### Tests 与门禁

- 同 fingerprint不下降会 rollback并 `stagnation_stop`。
- 修复一个 root、未引入同级新 root时可接受；成功 compile保持现有路径。
- unknown accessor得到 `spec_contract_blocked`，不产生第二轮猜名。
- Run 1 fixture即使 return code 0，也因 ABI/invalid-free/incompatible-pointer audit判为 sound-build failed。
- 旧 initial compile measurement兼容读取准确，summary不再依赖 legacy flat key。

## Step R5：Offline Regressions、Pruning 与 Revision Freeze

**状态：COMPLETED（2026-07-15）**

### Required gates

```text
R0 historical fixtures replay in temp dirs
agent/planning full tests
evaluation/planning_utility full tests
agent/coder full tests
python -m compileall
git diff --check
historical artifact hash preservation
zero fresh runs and zero planning model calls
```

必须证明旧三个 defect被更早、同一 root family地识别：Run 1 为 representation/ownership unsound，Run 2 为 system-header/provider gap，Run 3 为 tagged opaque access gap。不得以复制历史最终 repair code作为 fixture pass 条件。

Pruning 时删除被替换的 `_t`-only分支、unresolved全局短路、重复 type regex、legacy measurement branch和无效 repair acceptance；不保留 strict/legacy 双路径。若实现净增超过50行，必须按 RF1–RF5逐项说明为何不能通过替换现有逻辑完成。

Freeze record 至少保存 revision+dirty diff hash、taskbook hash、facts/schema/prompt/planning/coder/adapter hashes、model/sampling/toolchain、token/repair budgets、tests结果和新 pilot output root。Freeze 后 execution source不可修改。

### R0–R5 执行摘要

| Gate | 结果 |
| --- | --- |
| Historical evidence | 三个 run 的 plan/specs、validated diagnostics、original/repair manifests 与 compile logs 分类聚合 hash 已冻结并复核不变 |
| RF1 | shared C type parser 已覆盖 typedef/tagged/system type；Stage 5/6/8、compiler、implementability 与 coder header gate 共用该语义；`struct iovec` deterministic 写入 `sys/uio.h` |
| RF2 | foreign opaque dereference形成 typed diagnostic；generated source 的 conflicting shared layout、invalid-free 与 incompatible declaration/pointer warning进入 sound-build veto |
| RF3 | ordered typed provider、unknown callee/access service 与 runtime chain 保持 closed-world；compiler不发明 accessor |
| RF4 | unresolved partition 不再全局短路 committed subset；只选择最早 compile-critical stage slice，最多 primary + 同 slice correction，slice 外 update 被拒绝 |
| RF5 | repair 以 path-independent fingerprints 为接受条件；unknown external symbol直接 `spec_contract_blocked`；无严格子集进展即 rollback并 `repair_stagnated` |
| Offline replay | Run 1=`representation/ownership unsound`；Run 2=`system-header/provider gap`；Run 3=`tagged opaque access gap` |
| Full tests | planning `157/157`；coder `33/33`；planning utility `36/36` |
| Static gates | `compileall`、`git diff --check`、historical hash preservation 全部通过 |
| Fresh/model calls | `0 / 0` |

本 tranche 的代码净增超过 50 行，原因按 RF 分离且不可由单点替换覆盖：RF1 需要一个共享 C type parser及三层 gate；RF2 需要 generated-source structural/high-risk audit；RF3/RF4 需要 provider diagnostics与 bounded slice scope validation；RF5 需要 fingerprint history、rollback和 adapter machine-readable measurement。已删除 `_t`-only parser入口、unresolved global short-circuit 与旧 repair acceptance，未保留 legacy/strict 双路径。

## Step R6：恰好三次 Final Frozen Fresh Pilot

**状态：COMPLETED（2026-07-15；恰好 3/3，无补跑）**

新 pilot 独立保存到：

```text
agent/planning/out/final_root_fix_pilot_20260715/
  freeze_record.json
  root_fix_run_1/
  root_fix_run_2/
  root_fix_run_3/
  sequence_summary.json
```

每次固定流程：

```text
fresh planning from Stage 1
-> archive plan/specs/diagnostics/usage
-> record compile_contract_ready
-> Candidate-through coder validate
-> immutable coder_original + initial clean compile
-> independent repair copy, <=3 rounds with monotonic gate
-> final clean compile
-> shared ABI/type/ownership + high-risk warning audit
-> verify original hashes
-> write per-run summary
```

三次均不运行 runtime/behavior smoke。每次必须记录 stage/partition survival、spec inventory、qualification、compile-critical roots、planning tokens、coder validate、initial/final fingerprints、accepted/rejected repair rounds、raw compile、sound build、source hashes和manual edit count。

## Step R7：最终判定与永久停止本优化方向

**状态：COMPLETED（2026-07-15；NEGATIVE_STABILITY_RESULT）**

第三次结束后只汇总，不修复、不补跑。分别报告：

```text
materialization stability
Candidate-through stability
compile-contract readiness
initial/final raw compile rate
sound-build rate
repair rounds and stagnation/spec-contract-blocked distribution
root-family recurrence
qualification distribution
token target/hard-ceiling distribution
```

满足以下全部条件，才标记“具备另行制定 formal RQ1 计划的工程条件”：

```text
physical nonempty specs = 3/3
Candidate-through coder validate/original preservation = 3/3
compile_contract_ready >= 2/3
sound_build_passed >= 2/3
RF1/RF2/RF3 中没有同一 root family 在 >=2 runs 重复
planning hard ceiling met = 3/3
manual edits = 0
```

`qualification_passed` 保持原值并单独报告；上述门槛只判断工程稳定性，不自动启动或宣称 formal RQ1。正式实验仍需单独冻结 protocol、baselines、replicates 和 behavior endpoint。

出现任一情况即形成 final negative stability result：

- `sound_build_passed <= 1/3`；
- RF1/RF2/RF3 同一协议无关 root family 在至少两次 run 复现；
- deterministic fixture/regression失效；
- 需要 run 间修改、提高 hard ceiling或手工编辑才能达到门槛。

无论 positive 或 negative，R7 后本 root-fix 任务都标记 `DONE`，不再启动额外 fresh、第三轮 prompt tuning或新 stability tranche。Negative 结果意味着在当前研究阶段接受“materialization优于旧版本，但 compile stability未收敛”的结论；positive 结果则冻结该 revision，后续只在独立 formal RQ1 中评估。

### R6–R7 最终执行结果

| Run | Stages / partitions | Specs M/F/Fn | Qualified | Contract ready | Initial compile | Repair / stop | Final compile | Sound build | Tokens | Root families |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | ---: | --- |
| 1 | 11/11；16/17 | 1/6/33 | false | true | failed | 0；`spec_contract_blocked` | failed | failed | 702824 | RF2 public ABI；RF3 unknown external provider |
| 2 | 11/11；16/17 | 1/6/29 | false | false | failed | 1；`repair_stagnated`、rollback | failed | failed | 675871 | RF2 callback/pointer ABI；RF3 provider/runtime chain |
| 3 | 11/11；13/16 | 1/5/31 | false | false | failed | 0；`spec_contract_blocked` | failed | failed | 636364 | RF2 pointer ABI；RF3 entrypoint/unknown provider |

最终聚合：materialization、coder validate、original hash preservation 均为 `3/3`；compile-contract ready=`1/3`；initial raw compile=`0/3`；final raw compile=`0/3`；sound build=`0/3`；qualification=`0/3`；target/hard ceiling=`3/3`。Repair rounds 为 `[0,1,0]`，其中两次 closed-world block、一次无单调进展 rollback。

判定为 `NEGATIVE_STABILITY_RESULT`：sound build `0/3 <= 1/3`，且 RF3 closed-world provider/runtime family 在至少两次 run 复现。Root-fix 证明“更早识别并阻断错误 repair”可以收敛，但没有证明 specs→code compile stability 收敛；不得追加 fresh 或继续 prompt tuning。Machine-readable 结果位于 `agent/planning/out/final_root_fix_pilot_20260715/sequence_summary.json`。

## 附录 A：新 Pilot Ledger 模板

| Run | Stages / partitions | Specs M/F/Fn | Qualified | Contract ready | Initial compile | Repair / stop | Final compile | Sound build | Tokens | Root families |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | pending | pending | pending | pending | pending | pending | pending | pending | pending | pending |
| 2 | pending | pending | pending | pending | pending | pending | pending | pending | pending | pending |
| 3 | pending | pending | pending | pending | pending | pending | pending | pending | pending | pending |

## 附录 B：权威 Evidence 路径

```text
agent/planning/out/candidate_through_stability_20260715/freeze_record.json
agent/planning/out/candidate_through_stability_20260715/sequence_summary.json
agent/planning/out/candidate_through_stability_20260715/stability_run_{1,2,3}/summary.json
agent/planning/out/candidate_through_stability_20260715/stability_run_{1,2,3}/logs/validated_diagnostics.json
agent/planning/out/candidate_through_stability_20260715/stability_run_{1,2,3}/coder_original/_agent_logs/pre_repair_diagnostics.json
agent/planning/out/candidate_through_stability_20260715/stability_run_{1,2,3}/coder_original/*_repair_*/_agent_logs/*repair_manifest.json
```

本任务书的 `READY_TO_EXECUTE` 只表示 root-cause、修改范围、验证顺序和停止门槛已预声明；在 R0–R7 实际完成前，不得把它表述为稳定性已修复。
