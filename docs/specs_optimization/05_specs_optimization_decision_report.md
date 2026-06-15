# Step 5: Specs Optimization Decision Report

## 1. Executive Summary

本报告基于 Step 1-4 的证据，决定 SpecForge 当前是否应优化 specs 结构、如何调整字段边界，以及下一阶段应优先优化哪些 planning stages。

最终结论：

1. 当前证据不足以证明 coder-facing strict specs 整体过细或过重。更强证据指向 dependency/header/type closure、specs compiler lowering 和 final coder compatibility validation 的缺口。
2. 立即路线应选择 **Option 1: 保留当前 specs 结构，优先修 validator/lowering**。
3. 同时应局部吸收 **Option 2**：将明确不被 coder generation 消费、且 degradation 已显示无损的 traceability 字段下沉到 sidecar。
4. **Option 3** 应作为后续方向：保留 rich planning internal IR，由 compiler 按 `target_profile` 或 `specs_density` 输出 minimal/standard/rich 不同密度视图。
5. 不应现在一次性压缩 strict schema、重写 planning/coder、关闭 coder compatibility validation，或通过删除 dependency graph 让 validation 假通过。

对用户提出的 9 个问题的直接回答：

| question | decision |
|---|---|
| 当前是否有足够证据证明 coder-facing strict specs 过细或过重 | 没有整体性证据。只有局部字段边界证据，例如 `DOC_REF` 可下沉，function-level `WIRE_MAPPING` 暂非 hard requirement。 |
| 哪些字段应保留在 strict specs | identity/routing、module/file layout、public ABI/header、source dependency、source data/interface、canonical signatures、behavior/action detail、call/access/forbidden/test constraints。 |
| 哪些字段应移动到 sidecar | module/file `DOC_REF`、protocol/fact traceability、decision rationale、部分 non-consumed nested `ROLE`、function `PUBLIC_SYMBOLS` 等与 generation 无直接关系的 traceability/detail。 |
| 哪些字段应仅作为 validator-only artifact | validation reports、dependency diagnostics、rendered header reports、repair statistics、candidate validation reports、diff/oracle manifests。 |
| 哪些字段应从 planning internal IR lower 到 coder specs | `header_public_deps`、`source_call_deps`、`system_deps`、public type closure、canonical public signatures、source data declarations、bounded behavior/call/wire/test projections。 |
| planning 当前主要瓶颈在哪些 stage | `5.6_dependency_closure`、`specs_compiler`、`5.7_spec_readiness`、`coder_compat validator`，其次是 `5.5a_file_layout`、`5.3_type_data`、`5.4b_function_signatures`。 |
| 是否优先修 schema、compiler、dependency lowering、validator，还是 LLM substages | 优先修 dependency lowering、specs compiler、validator/readiness；schema 只做边界小调整；LLM substages 在 closure blocker 修复后再优化。 |
| 后续如何分阶段修改 | 先修 hard blockers，再做最小复现实验，然后调整 schema boundary，最后做 stage-level LLM 优化。 |
| 哪些修改不应该现在做 | 不重写 planning/coder；不关闭 compatibility validation；不删除 dependency 让 graph 通过；不让 LLM 直接生成 final dependency graph；不把 example specs 的 code-derived detail 当 protocol fact。 |

## 2. Evidence Summary from Step 1-4

### 2.1 Step 1: Artifact Boundary Audit

Step 1 显示字段不能只按 dataclass 是否显式建模判断。`PUBLIC_SYMBOLS`、`ACCESS_PATHS`、`CALL_CONTRACTS`、`FORBIDDEN_SYMBOLS`、`WIRE_MAPPING`、`TEST_VECTORS` 等字段通过 `raw` 被 prompt 或 validator 消费。

强证据：

- `HEADER.DEPENDENCY`、`HEADER.SYSTEM_DEPENDENCY`、`HEADER.DATA.TYPE_SPEC`、`HEADER.INTERFACE.SIGNATURE`、`SOURCE.DEPENDENCY` 与当前 header/dependency failure 直接相关。
- compiler 当前将同一 `imports_allowed` 投影到 `SOURCE.DEPENDENCY` 和 `HEADER.DEPENDENCY`，没有分离 source call dependency 与 public header/type dependency。
- dependency fallback 会清空 `calls_allowed` 和 `imports_allowed`，导致 empty dependency graph 假通过。
- planning final coder compatibility validation 使用 `validate_rendered_headers=False`，无法拦截 missing `ssize_t` 等 deterministic header compile failure。
- `planning_decisions.json`、`planning_ir_refs.json` 是 validator-sidecars；`planning_traceability.json` 当前是 pure sidecar。

### 2.2 Step 2: Diagnostic Toolkit Construction

Step 2 提供了 `degrade_specs.py`、`compare_specs.py`、`oracle_substitute.py` 三类工具，并明确 fail-closed 策略。

强证据：

- 工具能生成 seven degradation profiles，并保留 schema/loader/header validation。
- `P+GoldDependency` 不清空、不伪造 missing dependency。它在 `broker_app` 依赖 planning bundle 中不存在的 gold `router` 时 fail closed。
- oracle manifest 明确 `source_kind=gold_spec_code_derived_oracle` 与 `not_protocol_fact=true`，避免将 example implementation detail 当 protocol fact。

### 2.3 Step 3: Gold Specs Degradation Evaluation

Step 3 对 MQTT、CoAP 的 14 个 protocol/profile 组合执行 schema、loader、rendered header、bounded coder generation、compile/repair 和 smoke。

关键结果：

| evidence | interpretation |
|---|---|
| MQTT/CoAP `Gold-Full` 均 compile + 4/4 smoke pass | coder 主流程可用，example specs 可驱动 implementation。 |
| 全部 profiles 均通过 schema/load/rendered header | 删除 behavior/wire/calls/test vectors 的早期问题不在 loader/header 层。 |
| `Gold-Sidecar-Only-Traceability` 对 MQTT/CoAP 均无损 | module/file `DOC_REF` 更适合 sidecar。 |
| `Gold-No-Wire-Binding` 对 MQTT/CoAP 均最终通过 | 在保留 `ACCESS_PATHS` 时，function-level `WIRE_MAPPING` 暂非 coder hard requirement。 |
| `Gold-No-Behavior-Detail` 造成 MQTT compile failure、CoAP smoke degradation | behavior detail 对 source generation 和 behavior correctness 有实际价值。 |
| `Gold-No-TestVectors` 使 CoAP 0/4 smoke | test vectors 至少对部分协议 behavior anchoring 重要。 |
| `Gold-Min-Interface` 使 MQTT compile fail、CoAP 0/4 smoke | aggressive strict spec compression 不成立。 |

这些结果提供的是 single-run LLM coder experiment 证据，受 nondeterminism 影响，但足以反对立即一刀切压缩 strict specs。

### 2.4 Step 4: Planning-vs-Gold + Oracle Diagnosis

Step 4 的 MQTT planning-vs-gold diff 和三组 oracle substitution 显示，当前 planning failure 更接近 closure/validator 与 architecture/file-layout mismatch，而不是 specs 结构整体过重。

关键结果：

| evidence | interpretation |
|---|---|
| planning comparison status 为 `degraded_input`，原因是 rendered header validation 已有 3 个 errors | planning validation success 与 coder-compatible readiness 脱节。 |
| planning `HEADER.DEPENDENCY=0`、`HEADER.SYSTEM_DEPENDENCY=0` | public/system type header closure 未 lower。 |
| 64 个 functions 有 `call_contracts`，但 104 个 functions 的 `calls_allowed` 全空，9 个 files 的 `imports_allowed` 全空 | dependency fallback 清空边后 validation 仍 passed。 |
| `P+GoldDependency` 因 missing `router` failed | gold dependency 无法直接 graft 到 planning architecture，瓶颈含 file-layout/module mapping。 |
| `P+GoldType`、`P+GoldSignature` 均 failed | 单独修 type 或 signature 不足，必须闭合 type/signature/dependency/readiness。 |
| CoAP planning `spec_bundle` 未发现 | Step 4 stage-level diagnosis 完整覆盖 MQTT，CoAP 只能基于 gold degradation 提供协议差异信号。 |

## 3. Decision Options

| option | changes | required files/modules | validation changes | expected benefit | risk | when to choose |
|---|---|---|---|---|---|---|
| Option 1: 保留当前 specs 结构，只修 validator/lowering | 保留 strict schema 主体；修 dependency fallback、header/source dependency separation、public/system type lowering、rendered header validation | `agent/planning/stages/specs_compiler.py`, `agent/planning/stages/dependencies.py`, `agent/planning/stages/implementation_plan_merger.py`, `agent/planning/validators/coder_compat.py`, `agent/coder/specs.py` | dependency validation fail closed；final readiness 启用 rendered header validation；新增 dummy header translation unit | 先解决 deterministic blockers，避免把 closure bug 误判成 schema 过重 | 更严格 validator 会让当前成功 planning runs 转为 failed，需要更新 failure handling | Gold-Full 明显优于 minimal；删除 behavior/test vectors/calls 导致退化；failure 集中在 dependency/header/system type/final validation |
| Option 2: 压缩 coder-facing strict specs，保留 planning sidecar | strict specs 只保留 coder 硬消费字段；traceability、部分 wire/detail 下沉 sidecar；sidecar 保留完整 planning 语义 | schema files, `specs_compiler.py`, `coder_spec_lowering.py`, coder prompts, validators | validator 从 sidecar 检查 planning completeness；coder prompt 只读 stable strict fields | 降低 strict specs 噪声，减少 traceability/detail 对 coder 的不稳定影响 | 若过早移动 behavior/test vectors/calls，会降低 compile/smoke/interop；可能破坏 raw prompt consumer | Gold-Min-Interface 与 Gold-Full 接近、删除 behavior/wire/calls 无损、detail 主要用于 traceability/validation 时选择 |
| Option 3: 按协议或 target profile 选择 specs density | planning internal IR 保持 rich；compiler 根据 `target_profile` 输出 minimal/standard/rich strict specs 和 sidecar | `coder_spec_lowering.py`, `specs_compiler.py`, target profile config, prompts, validators | 每个 density 都有 schema/load/header/smoke acceptance；validator 根据 density 调整 required projections | 兼顾 MQTT/CoAP 差异，避免单一 schema 密度 overfit 某协议 | 引入 profile 复杂度；过早做会掩盖当前 closure blockers | 不同协议字段敏感度稳定不同，且 hard blockers 已修复后选择 |

推荐决策：**立即采用 Option 1；同时只做 Option 2 的低风险字段下沉；将 Option 3 作为后续 target-profile 设计，不进入当前 blocker 修复。**

## 4. Final Decision and Recommended Route

### 4.1 Immediate Fixes

必须先修 hard blockers，顺序如下：

1. `dependency fallback` fail closed  
   不允许 fallback 清空 `calls_allowed`、`imports_allowed` 后让 dependency graph 因无边而 passed。若 inputs 不可用，应产生 blocking diagnostic。

2. `header_public_deps` 与 `source_call_deps` 分离  
   不再从同一 `imports_allowed` 同时生成 `HEADER.DEPENDENCY` 与 `SOURCE.DEPENDENCY`。`HEADER.DEPENDENCY` 只来自 public ABI/type closure；`SOURCE.DEPENDENCY` 来自 implementation calls 和 source-only dependencies。

3. public type dependency lowering  
   从 public signatures、callback signatures、struct fields、typedef/alias、function pointer params 派生 provider header deps。不能只看 source imports。

4. system type registry  
   建立 `ssize_t`、`size_t`、fixed-width integers、socket/system types 到 `HEADER.SYSTEM_DEPENDENCY` 的 deterministic mapping。

5. final rendered header validation  
   `coder_compat` final readiness 应启用 rendered header validation，至少等价于 `load_spec_bundle_from_root(validate_rendered_headers=True)` 的 blocking gate。

6. dummy header translation unit  
   对每个 rendered header 生成最小 translation unit 并编译，提前暴露 include path、system include、public type closure 问题。

### 4.2 Short-term Experiments

只做最小复现实验，不扩展新实验矩阵：

1. 修复 Immediate Fixes 后，重跑当前 MQTT planning sample 的 deterministic validation，确认 rendered header errors 变为 planning-stage failures 或被修复。
2. 重跑 `P+GoldDependency` only after architecture mapping 或 dependency lowering 修复，验证 oracle failure 是否从 unknown module dependency 转为更具体 closure diagnostics。
3. 对 `DOC_REF` sidecar-only 和 `no_wire_binding` 做 1 次 targeted sanity rerun，确认低风险 boundary adjustment 未破坏 coder usability。
4. 若出现 CoAP planning `spec_bundle`，补同样的 planning-vs-gold diff；没有则不阻塞 implementation phase。

### 4.3 Schema Boundary Adjustment

当前不做大 schema rewrite，只做边界决策：

- strict specs 应保留：`KIND`、trace ids、module/file names、paths、`MODULES[].FILES`、`GENERATION_ORDER`、`HEADER.DATA.TYPE_SPEC`、`HEADER.INTERFACE`、`HEADER.DEPENDENCY`、`HEADER.SYSTEM_DEPENDENCY`、`SOURCE.DEPENDENCY`、`SOURCE.DATA`、`SOURCE.INTERFACE`、canonical signatures、behavior/action detail、call/access/forbidden/test constraints。
- sidecar 应承载：module/file `DOC_REF`、protocol/fact traceability、planning decision rationale、target directive refs、planning IR refs、部分 non-consumed nested `ROLE`、function `PUBLIC_SYMBOLS`。
- validator-only 应保留：schema/load/rendered-header reports、dependency validation reports、oracle/diff manifests、repair statistics、candidate validation reports。
- planning internal IR 应保持 rich：protocol facts、canonical type graph、function inventory、behavior contracts、wire/access binding、call graph、dependency graph、unresolved assumptions。compiler 再 deterministic lower 到 coder specs。

### 4.4 Stage-level Optimization Plan

Stage 优先级不从 LLM substages 开始，而从 deterministic closure 开始：

1. P0: `5.6_dependency_closure`, `specs_compiler`, `5.7_spec_readiness`, `coder_compat validator`  
   症状：empty `HEADER.DEPENDENCY`、empty `HEADER.SYSTEM_DEPENDENCY`、empty `calls_allowed/imports_allowed`、rendered header failure 被 final validation 漏掉。  
   补强点：fail-closed dependency diagnostics、public/system type closure、rendered header compile。

2. P1: `5.5a_file_layout` and runtime architecture mapping  
   症状：`topic_router`/`timer` 与 gold `topic`/`router` 不可直接对齐，oracle dependency graft failed。  
   补强点：role-to-file mapping explanation、generated header path resolvability、entrypoint dependency closure。

3. P2: `5.3_type_data`, `5.4b_function_signatures`  
   症状：missing public manager/router/tcp callback types，signature exact overlap 低，type/signature oracle 单独无效。  
   补强点：public type obligation coverage、signature type-ref closure、callback/function pointer type handling。

4. P3: `5.4a` to `5.4e` semantic substages  
   症状：behavior/call fields 数量不少，但 actionability 和 precision 不足；`WIRE_MAPPING` 与 access paths 不闭合；call contracts 与 dependency graph 脱节。  
   补强点：behavior actions 绑定 declared data/resources/functions，wire fields 绑定 valid access paths，call contracts 校验 callee existence 和 module boundary。

### 4.5 Do-Not-Do List

- 不要一次性重写 planning/coder。
- 不要关闭 coder compatibility validation。
- 不要通过删除依赖、清空 `calls_allowed/imports_allowed` 让 graph 通过。
- 不要让 LLM 直接生成 final dependency graph；final graph 必须由 structured IR deterministic lowering 并被 validator 检查。
- 不要把 example specs 里的 code-derived detail 当作 protocol fact。
- 不要现在把 behavior、test vectors、calls/rely 一刀切移出 strict specs。
- 不要把 `Gold-Min-Interface` 当成目标 profile；它在 MQTT/CoAP 均显示明显退化。

## 5. Field Placement Recommendation

| field/category | keep in strict specs? | move to sidecar? | validator-only? | rationale | evidence |
|---|---|---|---|---|---|
| `KIND`, trace ids, module/file names, paths | yes | no | no | spec discovery、file routing、function linkage、generation output 均依赖 | Step 1 loader/generation audit |
| `PROTOCOL.NAME`, `ROLES`, `DEFAULT_PORT` | yes | no | no | binary/verifier/runtime defaults 依赖 | Step 1 consumer audit |
| `PROTOCOL.SCOPE` | no, unless prompt starts consuming it | yes | no | 当前 coder 无直接 consumer，更像 scope/traceability metadata | Step 1 sidecar candidate |
| module/file `DOC_REF` | no | yes | no | sidecar-only traceability 对 MQTT/CoAP 均 compile + smoke pass | Step 3 `Gold-Sidecar-Only-Traceability` |
| `MODULES[].DEPENDENCIES` | yes | no | no | generation order 和 module dependency prompt 依赖 | Step 1; Step 4 dependency failure |
| `MODULES[].ARTIFACTS`, module/file `PUBLIC_SYMBOLS` | yes for current validator contract | possible later as validator-sidecar | no | 当前用于 public API semantic gate；可在 validator-sidecar 方案成熟后移动 | Step 1 `coder_semantics` audit |
| function `PUBLIC_SYMBOLS` | no | yes or omit | no | compiler 不生成，coder 当前不消费，且与 file/module public symbols 重复 | Step 1 sidecar/omit candidate |
| `HEADER.DEPENDENCY` | yes | no | no | rendered quoted includes 和 public type visibility 依赖 | Step 1; Step 4 `HEADER.DEPENDENCY=0` |
| `HEADER.SYSTEM_DEPENDENCY` | yes | no | no | `ssize_t` 等 system types 需要 deterministic include | Step 1 compile log; Step 4 `network.h` error |
| public `HEADER.DATA.TYPE_SPEC` | yes | no | no | public ABI/header rendering 依赖 | Step 1 renderer audit; Step 4 P+GoldType |
| nested field/enum `ROLE` | no for current coder | yes | no | renderer 不消费，主要是 explanation/traceability | Step 1 sidecar candidate |
| `HEADER.INTERFACE` and canonical signatures | yes | no | no | public ABI 和 source/header consistency 依赖 | Step 1; Step 4 P+GoldSignature |
| `SOURCE.DEPENDENCY` | yes | no | no | source prompt 和 repair dependency headers 依赖 | Step 1 consumer audit |
| `SOURCE.DATA.TYPE_SPEC/ROLE/VISIBILITY` | yes, strengthen consumer | no | no | 当前 under-consumed，但 missing private data/type 会导致 source failures | Step 1; Step 4 source compile symptoms |
| `SOURCE.INTERFACE.CONTRACT` | yes, strengthen consumer | no | no | schema required 且包含 pre/post/ownership/thread-safety contract | Step 1 under-consumed list |
| `SIGNATURE.RETURN/PARAMS/NULLABLE/OWNERSHIP` | yes | no | no | structured checks 与 future deterministic validation 需要，不能只保留 `RAW` | Step 1 duplicate canonical risk |
| `RELY.FUNC`, function/file `CALL_CONTRACTS` | yes for now | no | no | 删除不阻断 compile，但 MQTT interop timeout，CoAP repair signal 显示质量价值 | Step 3 `Gold-No-Calls`; Step 4 call graph drift |
| `RELY.STRUCT/VAR` | yes, strengthen consumer | no | no | 目前 under-consumed，但应帮助 type/data dependency closure | Step 1 recommendation |
| `LOGIC` / `EVENT` behavior detail | yes | no | no | 删除导致 MQTT compile failure 和 CoAP smoke degradation | Step 3 `Gold-No-Behavior-Detail` |
| function `WIRE_MAPPING` | optional strict or validator-sidecar later | yes, after access projection stabilizes | no | 当前删除无损，但 only because `ACCESS_PATHS` retained；不应马上整体删除 wire/access semantics | Step 3 `Gold-No-Wire-Binding`; Step 4 access-path gap |
| `ACCESS_PATHS` | yes | no | no | prompt 和 wire target validation 依赖，是 `WIRE_MAPPING` 可弱化的前提 | Step 1; Step 3 profile design |
| `FORBIDDEN_SYMBOLS` | yes | no | no | hallucination guard 和 loader conflict checks 使用 | Step 1 prompt/loader audit |
| `TEST_VECTORS` | yes for standard/rich profile | maybe for minimal profile later | no | CoAP 删除后 0/4 smoke；协议差异明显 | Step 3 `Gold-No-TestVectors` |
| `planning_decisions.json`, `planning_ir_refs.json` | no | yes, as validator-sidecar | no | validator 消费，不应进入 generation prompt | Step 1 sidecar audit |
| `planning_traceability.json` | no | yes, pure sidecar | no | 当前无 core consumer，用于 research traceability | Step 1 sidecar audit |
| validation reports, diff/oracle manifests, repair stats | no | no | yes | 诊断和 reproducibility artifact，不是 coder input | Step 1/2 artifact classes |

## 6. Stage Optimization Priority

| priority | target stage/module | issue | recommended action | expected benefit | risk |
|---|---|---|---|---|---|
| P0 | `5.6_dependency_closure`, `implementation_plan_merger.py`, `dependencies.py` | `calls_allowed/imports_allowed` 被清空后 empty graph passed | fail closed；比较 `call_contracts`、`calls_allowed`、`RELY.FUNC`、function edges；禁止无诊断清空 | 消除 dependency false pass | 会暴露更多 planning failures |
| P0 | `specs_compiler.py` | source/header dependency 混用，`HEADER.DEPENDENCY=0` | 分离 `header_public_deps` 与 `source_call_deps`；按 public ABI/type graph lower | 修复 missing declaration 和 over/under include | 可能暴露 include cycle，需要 cycle policy |
| P0 | `specs_compiler.py` | `HEADER.SYSTEM_DEPENDENCY` 未生成 | 建立 system type registry 并 lower system includes | 修复 `ssize_t` 等 deterministic header failure | system type portability policy 需要稳定 |
| P0 | `coder_compat validator`, `5.7_spec_readiness` | final planning validation 跳过 rendered header validation | 启用 rendered header validation 和 dummy translation unit compile | 将 coder-stage header failure 前移到 planning readiness | 当前 success runs 可能转 failed |
| P1 | `5.5a_file_layout`, `5.5b_runtime_entrypoint` | planning architecture 与 gold layout/module mapping 不兼容 | 增加 role-to-file mapping explanation、entrypoint dependency closure、header path resolvability checks | 降低 oracle graft 和 downstream coder mismatch | 不应强制复制 example layout |
| P2 | `5.3_type_data` | public type coverage 缺 key callback/manager/tree types | 增加 public type obligation coverage 与 callback/field type refs closure | 提升 public ABI completeness | overfit gold names 的风险 |
| P2 | `5.4b_function_signatures` | signature overlap 低，public signatures 引用未闭合 types | 校验 public signature type refs 是否 same-header、header dep 或 system type 可见 | 减少 stale signature/type drift | 需要 C type parser 或 conservative extraction |
| P3 | `5.4a_function_inventory` | function family coverage 缺 parser/serializer/handler/network/session families | 从 protocol role 和 target profile 派生 required function family obligations | 提高 coder implementation coverage | family names 不能直接来自 gold |
| P3 | `5.4c_function_behavior_contract` | behavior fields 非空但 actionability 弱 | 将 actions 绑定 declared state/resources/callable functions | 减少 hallucinated helpers 和错误 struct fields | 需要 structured behavior IR |
| P3 | `5.4d_wire_access_binding` | `WIRE_MAPPING` 数量接近但 access path closure 弱 | 每个 wire strategy 绑定 valid access path 或 explicit skip/reject | 提升 wire behavior grounding | 过严可能拒绝 intentionally ignored fields |
| P3 | `5.4e_call_contracts` | call contracts 多但 precision 弱，dependency graph 脱节 | 校验 callee existence、module boundary、signature consistency | 减少 interop 和 include/call drift | precision policy 可能需 protocol-specific tuning |

## 7. Risks and Open Assumptions

- CoAP planning output 缺失，因此 stage-level oracle diagnosis 主要基于 MQTT；CoAP 只通过 gold degradation 提供 protocol sensitivity evidence。
- Step 3 每个 profile 只做 single-run LLM generation，compile/smoke 结果存在 nondeterminism。其结论适合指导优先级，不适合单独作为 schema 删除依据。
- Gold/example specs 是 code-derived oracle，不是 protocol facts。它们可用于 coder usability 和 coverage diagnosis，不能直接成为 planning agent 应复制的 implementation detail。
- `WIRE_MAPPING` 删除无损的结论依赖 `ACCESS_PATHS` 被保留；不能推导为 wire/access 信息整体可删除。
- 更严格的 readiness validation 会把当前一些 `success` planning runs 改判为 failed。这是预期行为，不应通过关闭 validation 回避。
- Option 3 的 density profile 需要在 hard blockers 修复后再设计，否则 profile 复杂度会掩盖 lowering/validator 缺陷。

## 8. Completion Statement

Step 5 的决策是：当前不执行大规模 specs schema 压缩；先保留 strict specs 主体并修复 dependency lowering、public/system type closure 和 final readiness validation。低风险的 traceability 字段可以下沉到 sidecar；rich planning internal IR 应继续保留，并由 deterministic compiler lower 为 coder-compatible specs。后续 implementation phase 应从 Immediate Fixes 开始，而不是从 LLM substages 或 broad schema rewrite 开始。
