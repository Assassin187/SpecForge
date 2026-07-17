# Planning Typed Contract Closure Architecture Tranche 计划书

> 初次制定：2026-07-15
> 本次重写：2026-07-16
> 当前状态：`A0–A7 OFFLINE GATES PASSED；A8 READY`
> 当前步骤：`A8.1 唯一一次 Fresh Planning 尚未执行`
> 历史 Candidate-through / Final Root-fix：`DONE（NEGATIVE_STABILITY_RESULT）`
> 新授权性质：一次有限的 architecture-level adjustment，不是继续旧 prompt/root-fix 调参
> 核心对象：planning agent 的 typed executable contract closure
> Fresh 预算：冻结后恰好 1 次 viability planning；通过后固定同一 specs 做 3 次独立 coder replay

## 0. 文档定位与决策

### 0.1 当前决策

当前阶段有必要调整 planning 架构，但不重写 facts agent、Stage 1–3、canonical registry 或外部 specs schema。调整范围限定在：

```text
Stage 4 public/runtime inventory
-> Stage 5–7 typed interfaces/behavior
-> Stage 8 call/callback/runtime closure
-> deterministic specs projection
-> coder release gate
```

旧 tranche 已按预声明门槛得到 negative result，并永久停止 RF1–RF5 的增量 validator、repair 和开放式 prompt tuning 路线。2026-07-16 的新授权只允许执行本计划中的 typed contract architecture tranche；它不授权无限追加 fresh run、提高 repair rounds 或继续围绕旧 candidate 调参。

### 0.2 本版替代关系

本版将旧任务书 479 行 R0–R7 执行流水压缩为历史基线。旧执行细节仍由以下 artifacts 保存，不在本任务书重复：

- `agent/planning/out/candidate_through_stability_20260715/`
- `agent/planning/out/final_root_fix_pilot_20260715/`
- `evaluation/planning_utility/out/fixed_best_candidate_coder_3x_20260715/`

若文字结论与 machine-readable artifacts 冲突，以 `sequence_summary.json`、各 run `summary.json`、`implementability_report.json` 和原始 compiler logs 为准。

### 0.3 不可破坏的原则

- 保留 `candidate_through_eligible` 的历史定义，用于物化、归档和纵向比较；不得回写旧六次运行。
- 新增独立的 `coder_release_eligible`，正式 downstream admission 必须 fail-closed。
- Candidate 即使不具备 coder release 资格也必须保留 specs、diagnostics、unresolved partitions 和 usage，不得清空 artifacts 制造成功。
- 不人工修改 planning specs、generated C/H、repair candidate 或实验结果。
- 不复制 Gold-Full、`specs-example` inventory 或 MQTT-specific implementation logic。
- 不以 coder repair 猜测 planning 未声明的 public API、callback、ownership 或 lifecycle。
- 新架构复用现有 registry 和 typed relations，不新增一套平行 registry、平行 specs schema或兼容分支。

## 1. 已完成工作与冻结结论

### 1.1 两轮历史 tranche 压缩摘要

| 历史阶段 | 已完成能力 | 冻结结果 | 结论 |
| --- | --- | --- | --- |
| Candidate-through stability | physical nonempty specs 进入 coder；original/repair 分离；source hash preservation | materialization/validate/preservation `3/3`；qualification `0/3`；initial compile `0/3`；post-repair raw compile `1/3` | 解决“能否稳定物化并进入 coder”，未解决 implementability；唯一 raw pass 非 sound |
| Final Root-fix R0–R5 | canonical tagged/system C type、header completeness、opaque access、shared layout/ownership audit、unresolved isolation、repair fingerprint monotonicity、sound-build measurement | planning `157/157`、coder `33/33`、planning utility `36/36`；`compileall`/`diff --check` 通过；offline 阶段 0 fresh/0 model call | 错误检测、归因和阻断更早、更可信 |
| Final Root-fix R6–R7 | frozen 3-run planning→coder pilot | materialization/validate/preservation `3/3`；qualification `0/3`；reported contract-ready `1/3`；initial/final/sound `0/3`；repair `[0,1,0]` | RF2/RF3 仍跨 run 复现，compile stability 未收敛 |

已完成的 RF1–RF5 机制继续保留：

| 已完成 family | 已冻结机制 |
| --- | --- |
| RF1 type/header | shared C type parser 覆盖 typedef/tagged/system type；by-value completeness probe；`struct iovec -> sys/uio.h` |
| RF2 representation/ABI | foreign opaque dereference diagnostic；shared layout、invalid-free、incompatible declaration/pointer 进入 sound veto |
| RF3 provider graph | ordered typed provider、unknown callee/access service、runtime chain closed-world diagnostics |
| RF4 unresolved isolation | 无关 unresolved partition 不再全局短路 committed subset；bounded semantic slice/correction |
| RF5 repair/measurement | unknown external symbol `spec_contract_blocked`；fingerprint 无严格下降则 rollback；initial/final/sound/original hash machine-readable |

这些工作不再重做。本 tranche 要解决的是它们尚未解决的“typed semantic artifacts 如何成为 coder-facing executable contract 的唯一事实源”。

### 1.2 最近六次 M2 的统一基线

最近六次为 Candidate-through stability 的 S1–S3 与 Final Root-fix 的 R1–R3。统一结论：

```text
physical materialization / coder validate = 6/6
qualification_passed                    = 0/6
initial native compile                  = 0/6
post-repair native compile              = 1/6
known sound build                       = 0/6
```

唯一 native compile recovery 存在 shared layout conflict、invalid free 和 incompatible public declaration，不计 sound build。Final Root-fix 独立聚合为：contract-ready `1/3`、initial/final/sound `0/3`，两次 `spec_contract_blocked`、一次 `repair_stagnated`。

### 1.3 固定最佳 Candidate → Coder 3× 因果实验

从最近六次中固定相对最优的 `root_fix_run_1`：inventory `1/6/33`，specs tree SHA256：

`024690a6ba0787a4209797bca06a74fd4f920446cc6744e40bb3af215218477f`

它仍是 `candidate_only`，`planning_validation_passed=false`、`qualification_passed=false`。固定这份 specs 进行三次独立 coder generation：

| 指标 | Run 1 | Run 2 | Run 3 |
| --- | ---: | ---: | ---: |
| coder validate / source generation | passed | passed | passed |
| initial / final / sound build | failed | failed | failed |
| repair rounds | 0 | 0 | 0 |
| stop reason | `spec_contract_blocked` | `spec_contract_blocked` | `spec_contract_blocked` |
| coder tokens | 53,258 | 52,449 | 55,876 |
| specified functions defined exactly once | 31/33 | 29/33 | 26/33 |
| duplicate specified functions | 2 | 4 | 7 |

三个 source tree hash 各不相同，确认是独立生成；但三次完全一致地发明：

- `mqtt_transport_create`
- `mqtt_broker_create`
- `mqtt_transport_register_callbacks`
- `mqtt_broker_destroy`

共同根因位于 planning contract：`main_spec.json` 的 prose `LOGIC.ACTION` 要求 init/create/register/run/destroy，但 `RELY.FUNC=[]` 且 `CALL_CONTRACTS` 未物化。真实 API 是 `mqtt_transport_init(...)`、`mqtt_transport_run(void)`、`mqtt_transport_destroy(void)`、`mqtt_broker_context_create(...)` 和 `mqtt_broker_context_destroy(...)`。三次 coder 只能自行补全 lifecycle，并稳定补成同一个错误 API family。

### 1.4 RQ1 当前机制证据

26 个已保存 run 的统一 classifier 重算显示：

| Metric | M0 | M1 | M2 |
| --- | ---: | ---: | ---: |
| C2–C5 closure | 25.1% | 13.7% | **43.6%** |
| C3–C5 closure | 24.5% | 9.2% | **46.9%** |
| semantic grounding median | 20% | 20% | **90%** |
| sound build | 0/10 | 0/10 | 0/6 |

M2 已显示 structured specs 对 semantic grounding 和 bounded-repair contract closure 的机制优势，但 C3 API contract closure 为 `-8.0%`，executable direct call-path 中位数仍为 0，优势尚未转化为 sound implementation yield。本 tranche 的目标是闭合这条断点，不是继续证明 materialization 或 semantic grounding。

## 2. 当前确认的架构根因

### 2.1 Fail-open 链路

当前失败链路是：

```text
Stage 4 通过 role/name heuristic 产生 callback/runtime inventory，但 callback consumer/provider role未闭合
-> 任一早期 unresolved 将后续 allow_incomplete/preserve_nonfatal 全局打开
-> Stage 6 callback interface ABI validation可能被跳过
-> Stage 8 应产生 typed call_edges、callback_bindings、runtime_flow
-> main partition因 callback consumer/parameter错误而整体 rollback，或 invalid edge被过滤
-> final plan 仍保留 prose lifecycle，但 runtime_flow/typed edges 缺失
-> compiler 物化 main FUNCTION_SPEC，RELY.FUNC 与 CALL_CONTRACTS 为空
-> Candidate-through 仅凭 physical specs 放行 coder
-> coder 从 prose 猜 public lifecycle API
-> closed-world repair gate 正确阻断，但已经浪费一次完整 generation
```

问题不是 planning 没有 diagnostics。固定 candidate 的 `implementability_report.json` 已明确记录：

- Stage 4 callback role coverage missing；
- Stage 8 callback consumer ABI mismatch；
- `runtime_flow_missing`；
- `success=false`。

真正的架构问题是：typed diagnostics、qualification、readiness 汇总和 coder admission 没有共享同一个权威 predicate。

`root_fix_run_1` 的 Stage 8 main partition并非完全没有生成 lifecycle intent；它产生了正确方向的 main call edges和 runtime flow，但把 callback relation写成 `owner=main / consumer=main / consumer_parameter=on_accept`。真实 consumer应是 `mqtt_transport_init` 的 callback parameter。Typed validator正确拒绝该 relation，但 partition rollback后没有保留可投影的 runtime contract，后续 candidate materialization仍继续。这说明必须修复 validation scope与relation authority，而不是再增加一条自然语言 callback rule。

### 2.2 `compile_contract_ready` 已被证伪

Final Root-fix Run 1 在 `sequence_summary.json` 中被记录为 `compile_contract_ready=true`、compile-critical residual 为空；同一 run 却同时具有两个 unresolved partitions、`runtime_flow_missing`、`implementation_ready=false`，随后固定 coder 3× 为 `0/3`。

必须纠正此前“把 `runtime_flow_missing` 提升为 qualification-blocking error”的表述：它已经是 `level=error`，也已经使 qualification 为 false；缺陷在于它当前未进入 coder release / compile-contract 的 fail-closed 判定。新计划修复 severity classification、diagnostic propagation、readiness aggregation 和 adapter admission，不重复添加同义 diagnostic。

### 2.3 Contract authority 分散

目前同一 executable intent 分散在：

- Stage 4 `runtime_entrypoint` 和 callback role inventory；
- `lifecycle_matrix`；
- Stage 6 function signatures；
- Stage 7 prose `LOGIC/EVENT`；
- Stage 8 `call_edges`、`callback_bindings`、`runtime_flow`；
- compiler 生成的 `RELY.FUNC`、`CALL_CONTRACTS` 和 source/header dependencies。

这些字段已有足够表达能力，但还没有形成单一 authority 和 round-trip invariant。当前 Stage 8 normalization 中部分 invalid/unproven edges会被删除，导致错误被记录后仍可能留下 prose-only candidate，而不是一个明确不可 release 的 typed contract。

此外，`LLMStructuredPlanner` 目前以“全局是否存在任意 unresolved partition”决定后续 `allow_incomplete/preserve_nonfatal`。这不是严格的 partition isolation：一个 Stage 4 unresolved可能放宽无关 Stage 6/8 的 ABI与relation validation。新架构必须按当前 stage/partition显式控制 fallback，不能使用全局 unresolved boolean跳过后续完整性检查。

### 2.4 次要 coder robustness 问题

固定 specs 的三次 `mqtt_broker_core.c` 分别重复 2/4/7 个 specified functions。这是独立的 coder source-generation robustness 问题，并受到长且矛盾的 specs 放大。例如 `mqtt_broker_context_create(session_mgr, router)` 将输入标记为 borrowed，LOGIC 要求保存借用引用，RELY/CALL_CONTRACTS 又要求内部 create 新 manager/router。

该问题需要 non-semantic source integrity gate，但不能用它解释 main 的 3/3 固定 unknown lifecycle APIs，也不能由 repair 猜补 planning contract。

## 3. 唯一目标、完成定义与非目标

### 3.1 唯一目标

将现有 canonical registry、runtime inventory、typed call/callback relations 和 lifecycle/ownership data 收束为一个内部 Contract IR authority，使 coder-facing specs 只能由已闭合的 typed graph 确定性投影；未闭合 candidate 可以归档，但不能进入正式 coder generation。

这里的 Contract IR 不是新建平行 schema。它是以下既有字段的权威组合和不变量：

```text
runtime_entrypoint
lifecycle_matrix
canonical function/callback/type signatures
call_edges
callback_bindings
runtime_flow
argument providers / result usage / ownership / failure cleanup
```

### 3.2 完成定义

Architecture tranche 完成必须同时满足：

1. 每个 lifecycle action 对应一个 existing canonical function 和 typed edge；
2. 每个参数都有 ordered、type-compatible、ownership-compatible provider；
3. callback typedef → provider → consumer parameter → registration edge → user_data 完整闭合；
4. `main` 具有唯一 runtime flow、success order、result handling 和 failure cleanup；
5. Contract IR → `RELY.FUNC/CALL_CONTRACTS/dependencies` projection 无 drop，round-trip 等价；
6. prose 不得成为 public symbol、调用顺序或 ownership 的唯一来源；
7. ABI skeleton probe 通过；
8. `coder_release_eligible=true` 才允许正式 coder generation。

### 3.3 非目标

- 重写 facts extraction、Stage 1–3 或 protocol facts；
- 扩展外部 module/file/function specs schema；
- 复制 Gold-Full、gold inventory 或手工 MQTT implementation；
- runtime behavior、interoperability 或正式 RQ1；
- 继续抽样旧 candidate、补跑失败 viability run；
- 增加 coder repair rounds或允许 repair 发明 public API；
- 开放式 prompt wording tuning、增加重复上下文或用 token 换稳定性；
- 为追求通过而清除 qualification/semantic diagnostics。

## 4. 目标架构与准入语义

### 4.1 目标数据流

```text
protocol facts
-> Stage 1–7 + canonical registry
-> deterministic Contract Seed
-> Stage 8 bounded typed binding/closure
-> Contract Closure Gate
   -> fail: archive candidate + typed diagnostics; no coder release
   -> pass: deterministic specs projection
-> ABI skeleton compile/sound probe
-> coder_release_eligible
-> coder generation
```

`Contract Seed` 只从既有 canonical artifacts 推导：main identity、ordered startup/run/cleanup services、exact signatures、callback obligations、candidate providers、lifecycle ownership 和 required cleanup。唯一可推导的 edge/provider 由 deterministic logic完成；存在多个合法候选或事实不足时，Stage 8 只能在显式 allowed IDs 中选择，无法唯一选择则保留 blocking diagnostic，不能发明 symbol。

### 4.2 Candidate 与 Coder Release 分离

```text
candidate_through_eligible
  = physical specs 可物化、归档、离线诊断

coder_release_eligible
  = qualification_passed
  AND implementation_ready
  AND unresolved compile/release-critical diagnostics = 0
  AND Contract IR closure/round-trip passed
  AND ABI skeleton probe passed
```

Candidate-through 保持历史口径，只能作为显式 diagnostic experiment mode。正式 adapter 默认使用 `coder_release_eligible`；若显式运行 candidate-through 诊断实验，manifest 必须标记 `diagnostic_only=true`，不得混入 downstream success denominator。

## 5. 修改边界

### 5.1 主要修改范围

```text
agent/planning/planner.py
  Stage 4 runtime/callback inventory validation
  Stage 8 partition/normalization/merge/runtime-flow closure

agent/planning/implementability.py
  runtime/callback/provider/ownership release-critical diagnostics

agent/planning/compiler.py
  Contract IR -> RELY/CALL_CONTRACTS/dependency deterministic projection与round-trip

agent/planning/pipeline.py
  readiness aggregation、candidate/release分离、ABI probe orchestration

agent/planning/models.py
agent/planning/metrics.py
  仅在需要表达独立 artifact/qualification/release 状态时最小修改

agent/planning/validation_layers.py
  仅复用现有 layered diagnostic classification

agent/planning/tests/
```

Secondary coder/evaluation hardening：

```text
agent/coder/generation.py
agent/coder/specs.py（仅当 definition inventory 无法复用现有 bundle 时）
agent/coder/tests/
evaluation/planning_utility/full_specforge_adapter.py
evaluation/planning_utility/no_repair_analysis.py
evaluation/planning_utility/tests/
```

优先替换 silent filtering、重复 readiness predicates 和 free-form projection；不保留 legacy/strict 双路径，不新增 wrapper/adapter layer。

### 5.2 默认只读

```text
agent/facts/
specs_schema/
specs-example/
protocol-example/
Gold-Full artifacts
历史 planning/coder/generated source artifacts
MQTT obligation rubric
```

Planning prompts 默认只读。只有 Contract IR input/output contract 已在 deterministic fixtures 中冻结，而现有 Stage 8 prompt schema无法表达 allowed IDs 时，才允许一次结构性 replacement；必须删除过时字段说明，不得叠加第二套 instructions，且不得把它作为采样调优。

## Step A0：冻结证据与构造失败 Fixtures

**状态：DONE（0 fresh / 0 model call）**

### 任务

1. 冻结当前 revision、dirty diff hash、facts/schema/prompt/planning/coder/adapter hashes和 toolchain。
2. 冻结最近六次 planning、fixed-best specs hash、三次 coder source hash、summaries、prompts和 diagnostics。
3. 建立以下 protocol-agnostic fixtures：
   - structured runtime intent存在，但 main `RELY.FUNC/CALL_CONTRACTS` 为空；
   - main Stage 8 partition unresolved或缺失 `runtime_flow`；
   - callback typedef/provider/consumer/registration 中任一边缺失或 ABI mismatch；
   - borrowed constructor parameters 与内部 create/owned cleanup 矛盾；
   - invalid call edge被 normalization 删除后仍生成 prose-only entrypoint；
   - summary 错误地将上述 candidate 标记为 compile-contract ready；
   - generated source 对 specified public function 重复定义。
4. 以固定 `root_fix_run_1` 做 0-model offline replay，证明当前 release gate 会错误放行或 readiness 汇总矛盾。

### 门禁

```text
all fixtures reproduce before fix
historical hashes unchanged
fixed bad candidate remains a negative fixture, not a success target
no fresh planning/coder call
```

## Step A1：修复 Readiness Truth 与 Fail-closed Release

**状态：DONE**

### 最小修改

1. 保留 `runtime_flow_missing` 的 error/qualification 语义；将 runtime entrypoint/flow/call-chain/failure-cleanup、callback role/ABI、unknown callee/provider/access 和 ownership contradiction纳入 release-critical family。若继续复用 `compile_critical` 字段，则这些 code 必须标记为 true。
2. 统一 `_readiness_manifest_fields`、validated manifest、sequence/adaptor summary 的权威 predicate，禁止各自重算不同的 `compile_contract_ready`。
3. 明确输出 `artifact_success`、`qualification_passed`、`implementation_ready`、`coder_release_eligible` 和 `coder_release_blockers`，不再用单一 `success` 混合表达；`PlanningResult.success` 只表示 qualified/release-ready outcome，另以 `candidate_materialized` 表示可供诊断的 candidate。
4. `full_specforge_adapter` 默认只在 `coder_release_eligible=true` 后调用 coder；保留显式 diagnostic-only Candidate-through 模式用于历史/研究诊断。
5. 固定 bad candidate 必须在 coder LLM call 前被拒绝，并保留三个原始 blocker family。

### 门禁

- `root_fix_run_1` replay：`candidate_through_eligible=true`，但 `coder_release_eligible=false`。
- `runtime_flow_missing`、callback unresolved 和 ownership contradiction 任一存在时 release=false。
- positive closed fixture release=true；fatal/no-specs/coder-loader失败语义无 regression。
- readiness machine-readable 字段在 plan-time、validate-time和 adapter 中一致。

## Step A2：构造 Deterministic Contract Seed

**状态：DONE**

### 最小修改

1. 先修复 validation scope isolation：删除“任意 unresolved 即全局 `allow_incomplete/preserve_nonfatal`”的语义；fallback scope必须由当前已知失败 stage/partition显式传入，早期 unresolved不得跳过后续 Stage 6 ABI或 Stage 8 relation validation。
2. Stage 4 保留 callback type/function identity inventory，但 role/name substring不再作为 consumer/provider relation的权威来源。
3. 从 Stage 6 实际 function signature 中枚举每个 callback-typed parameter，建立 typed catalog：`callback_type_id -> consumer_function_id -> consumer_parameter -> ABI-compatible provider candidates`。Provider多解时形成 blocker，不猜测。
4. 从 Stage 4 `runtime_entrypoint`、`lifecycle_matrix`、canonical registry、Stage 5/6 types/signatures和 typed callback catalog构造 Contract Seed。
5. Seed 固定：main ID、top-level startup/run/cleanup顺序、callee exact signature、ordered parameters、allowed provider candidates、result contract、callback obligations、ownership和 cleanup obligations。
6. 唯一可推导的 service/edge/provider/callback binding确定性填充；ambiguous provider保留 allowed canonical IDs与 typed diagnostic，不生成 free-form name。
7. 不新增外部 schema；Seed 使用现有 internal plan/registry fields和序列化 artifact保存 provenance。

### 门禁

- closed-world callee coverage=100%；所有 callee均可由 registry exact resolve。
- ordered parameter coverage=100%；每个 provider有 type、source kind和 ownership。
- Stage 4 unresolved不会导致 Stage 6 callback ABI validation被跳过。
- callback catalog中的 consumer/parameter来自 exact signature，而不是 role/name猜测。
- runtime success order与 Stage 4 structured inventory一致。
- ambiguous fixture保留 blocker，不能由 deterministic code任意选择。

## Step A3：Stage 8 Constructive Typed Closure

**状态：DONE**

### 最小修改

1. Stage 8 以 Contract Seed和 typed callback catalog为输入，只能补全已声明 edge的 argument binding、result usage和 allowed callback/provider choice；不得重新发明 function identity、signature或 lifecycle family。
2. main partition必须原子提交：`runtime_flow`、全部 top-level direct edges、callback registration bindings、result handling和 failure cleanup缺一不可。
3. `runtime_flow.success_sequence` 从 structured runtime services确定性规范化，不依赖自由 prose顺序；failure cleanup与 lifecycle ownership一致。
4. callback typedef、provider ABI、consumer parameter、registration owner和 `user_data` source做同一关系内校验。
5. 删除 contract-critical edge 的 silent drop/filter行为；invalid edge保留为 rejected relation + typed diagnostic。不得通过删除最后一条错误 edge得到“空但可物化”的 main。
6. Local correction仍限同一 partition/slice一次；失败后保留 candidate并阻断 release，不扩张到全 plan prompt。

### 门禁

- exactly one main/runtime flow。
- top-level service均具有 `main -> callee` typed edge。
- callback closure=100%；例如 transport registration必须投影为 `owner=main -> consumer=mqtt_transport_init.<callback_parameter> -> provider=<ABI-compatible callback>`，callback provider不被伪装成 direct call。
- fallible startup/run均有 result handling与合法 cleanup。
- invalid/unresolved main partition不会输出 releaseable prose-only entrypoint。

## Step A4：Deterministic Specs Projection 与 Contract Round-trip

**状态：DONE**

### 最小修改

1. `RELY.FUNC`、`CALL_CONTRACTS`、callback provider metadata、source/header dependencies必须从同一 Contract IR投影，不允许由 prose单独补写。
2. Main 的 structured runtime services非空时，输出 `RELY.FUNC=[]` 或 `CALL_CONTRACTS=[]` 直接成为 projection error。
3. 增加 plan graph → specs → loader model round-trip检查：callee、signature、argument order/provider、result usage、callback和ownership不得丢失。
4. 增加 generic contradiction check：borrowed input不可同时被同一 constructor内部重新 create并覆盖；destroy responsibility必须与 lifecycle owner一致。
5. Compiler只完成 canonical artifact唯一决定的 lowering，不发明 accessor/API family，也不把 blocking gap降级成 prose assumption。

### 门禁

- required edge/projection drop count=0。
- main spec准确列出真实 init/create/run/destroy signatures及调用顺序。
- fixed bad candidate不能通过重新 materialize伪装成 closed contract。
- borrowed/owned contradiction fixture失败，合法 borrowed storage和owned creation fixtures通过。

## Step A5：ABI Skeleton Probe

**状态：DONE**

### 最小修改

1. 在 coder release 前，从 rendered headers和 Contract IR生成 evaluation-only C ABI skeleton/probe；它不是 protocol implementation，也不进入 coder source。
2. Probe覆盖 main direct calls、callback function-pointer assignments、ordered arguments、result variables、opaque/public type visibility和 cleanup signatures。
3. 使用与 coder相同 C11/toolchain flags执行 isolated compile/sound checks；保存 source、command、stdout/stderr和 hash。
4. Probe只验证 executable contract/ABI，不伪造 parser、routing或 protocol behavior。
5. `abi_skeleton_passed=false` 必须进入 `coder_release_blockers`。

### 门禁

- positive lifecycle/callback fixture clean compile且 sound。
- missing symbol、wrong arity、wrong return、callback mismatch、incomplete by-value type均失败。
- fixed bad candidate在 0 coder call前失败。
- probe结果可在 validate/resume/replay中确定性复现。

## Step A6：Coder Exactly-once Source Integrity Gate

**状态：DONE（secondary hardening）**

### 最小修改

1. 每个 source generation完成后，按 bundle/file/function specs检查每个 specified public function在所属 source中恰好定义一次。
2. duplicate、missing、wrong-source和明显 placeholder在 project compile前形成 `source_integrity_failed`。
3. 允许整个 project最多一次 targeted source regeneration，只针对首个失败 source；保留首稿、prompt、response hash和两次 usage。第二次仍失败或多个 source同时失败则停止，不进入 semantic repair。
4. Header仍由 deterministic renderer生成；integrity gate不得改写 function body、public signature或 protocol semantics。

### 门禁

- fixed 3× 中的 2/4/7 duplicate fixtures全部被 pre-compile捕获。
- valid helper/static function不误报。
- integrity retry预算可观察，原始首稿不可覆盖。
- main unknown public APIs仍由 planning release gate负责，不能被 source integrity掩盖。

## Step A7：Offline Regression、Pruning 与 Freeze

**状态：DONE（0 fresh / 0 production model call）**

### Required gates

```text
A0 historical/fixed-candidate replay
agent/planning full tests
agent/coder full tests
evaluation/planning_utility full tests
python -m compileall
git diff --check
historical artifact hash preservation
positive Contract IR -> specs -> ABI probe integration fixture
negative prose-only/callback/ownership/source-duplicate fixtures
```

Pruning pass必须删除被替换的 silent edge filtering、重复 readiness/compile-contract predicates和旧 fail-open adapter分支；不保留双路径。若净增超过50行，按 A1–A6逐项说明为何现有逻辑无法通过替换完成。

Freeze record至少保存：revision+dirty diff hash、taskbook hash、facts/schema/prompt/planning/coder/adapter hashes、model/sampling/toolchain、planning/coder budgets、test results、Contract IR/ABI probe版本和 output root。Freeze后 execution source不可修改。

## Step A8：一次 Fresh Viability + 固定 Specs Coder 3×

**状态：PENDING；仅 A0–A7 全部通过后执行**

### A0–A7 实际执行摘要（2026-07-16）

- A0 冻结了 revision、dirty diff、facts/planning/coder/adapter 与历史六次/fixed 3× evidence hashes；确认用户已有的 `test_contract_closure_analysis.py` 和 `recalculate_saved_rq1_metrics.py` hash 未改变。
- A1 将 candidate materialization、semantic qualification、implementation readiness 与 coder release 分离；adapter 默认 fail-closed，仅显式 `diagnostic_only` 可复现实验性 Candidate-through。
- A2 删除全局 unresolved 对后续 validation 的降级；callback consumer/parameter 改由 Stage 6 exact signature枚举，provider 使用 Stage 5 typedef + Stage 6 ABI-compatible candidate IDs。
- A3 删除 Stage 8 contract-critical edge silent drop；partition escape、unknown callee、direct callback-provider、invalid argument source与 missing prior-result均保留 typed blocker。
- A4 增加 structured runtime → main `RELY.FUNC/CALL_CONTRACTS` projection gate、plan→spec preservation检查与 borrowed→consumed ownership contradiction。
- A5 增加 evaluation-only C11 ABI skeleton；probe覆盖 rendered headers、main direct calls与 callback assignments，结果写入 manifest release predicate。
- A6 增加 coder exactly-once source integrity gate；duplicate/missing/wrong-source/placeholder在 compile前阻断，全 project最多一次 targeted regeneration且保存首稿。
- A7 离线门禁：planning `160/160`、coder `34/34`、planning utility `37/37`；`compileall`、`git diff --check`通过。固定 `root_fix_run_1` replay结果为 `candidate_through_eligible=true`、`coder_release_eligible=false`、`coder_validate_status=not_run`，未调用 coder。

本摘要仅证明 deterministic/offline architecture gates通过，不改变 A8 的单次 fresh预算与 negative stop条件。

### A8.1 恰好一次 Fresh Planning

执行一次：`fresh=true`、`resume=false`、`replay=false`，从 Stage 1 开始。失败占用唯一 slot，不补跑、不替换、不挑选。

进入 coder replay前必须全部满足：

```text
planning_validation_passed = true
qualification_passed = true
implementation_ready = true
coder_release_eligible = true
total unresolved partitions = 0
release-critical diagnostics = 0
runtime_flow / callback / provider / ownership closure = passed
Contract IR -> specs round-trip = passed
ABI skeleton sound probe = passed
manual edits = 0
planning hard ceiling met = true
```

任一失败：归档 candidate与 blocker，本 architecture viability tranche立即形成 negative result，不调用 coder，不补一次 planning。

### A8.2 固定同一 Specs 做恰好三次独立 Coder

若 A8.1 全部通过，立即冻结该 specs tree hash。三次 coder使用相同 specs、相同 toolchain/budget、独立 output目录和独立 LLM calls；不运行 Gold-Full。

每次固定流程：

```text
coder validate
-> independent source generation
-> exactly-once source integrity
-> immutable coder_original + initial clean compile
-> independent repair copy, <=3 bounded rounds
-> final clean compile + sound audit
-> original hash preservation
```

三次之间不得修改 planning、coder、adapter、prompt、facts、schema、budget或measurement code。fatal、generation/integrity/compile失败均占用一个 slot，不替换。

### A8.3 Viability 通过门槛

```text
fixed specs hash identical = 3/3
coder validate / original preservation = 3/3
unknown external lifecycle blockers = 0/3
source integrity accepted = 3/3
sound_build_passed >= 2/3
manual edits = 0
```

本次只判断 architecture viability，不证明 planning fresh stability。只有通过后，才允许另行制定 planning 3-run stability/behavior/formal RQ1计划；不得自动启动。

## 7. 预算、判定与停止条件

### 7.1 冻结预算

```text
planning fresh runs = 1
planning target <= 749726 tokens
planning hard ceiling <= 783804 tokens
single request input <= 64000
single response <= 16000
JSON repair <= 1 per failed response
semantic primary patch <= 1
semantic correction <= 1 for same selected slice
fixed-spec coder runs = 3
source-integrity regeneration <= 1 per project
coder repair <= 3 rounds per run
```

不得以提高 ceiling、增加上下文、补 run或提高 repair rounds换取通过。

### 7.2 Positive 结果

只有 A8.1 全门禁通过且 fixed-spec coder `sound_build >=2/3`，才能标记：

`ARCHITECTURE_VIABILITY_PASSED`

允许的结论仅为：typed contract closure 已能把一份 fresh planning产物转化为具有重复 coder utility的 specs，可以进入独立稳定性/behavior评估设计。

### 7.3 Negative 结果与永久停止

出现任一情况即标记：

`ARCHITECTURE_VIABILITY_FAILED`

- A0–A7 deterministic/offline gate无法通过；
- 唯一 fresh planning未达到 release gate；
- ABI skeleton失败；
- fixed-spec coder出现任何重复 unknown lifecycle API family；
- fixed-spec coder sound build `<=1/3`；
- 需要人工编辑、replacement run、提高 ceiling或扩大 prompt/repair预算。

Negative 后停止当前 planning architecture，不追加新的 prompt tranche、repair tranche或 fresh viability run。保留“structured planning具有 semantic grounding/contract-closure机制信号，但 executable utility未收敛”的研究结论。

## 8. 权威 Evidence 路径

```text
agent/planning/PLANNING_CURRENT_STATUS_REPORT.md
docs/specs_optimization/04_planning_vs_gold_oracle_diagnosis.md
docs/specs_optimization/05_specs_optimization_decision_report.md

agent/planning/out/candidate_through_stability_20260715/freeze_record.json
agent/planning/out/candidate_through_stability_20260715/sequence_summary.json
agent/planning/out/candidate_through_stability_20260715/stability_run_{1,2,3}/summary.json

agent/planning/out/final_root_fix_pilot_20260715/freeze_record.json
agent/planning/out/final_root_fix_pilot_20260715/sequence_summary.json
agent/planning/out/final_root_fix_pilot_20260715/root_fix_run_{1,2,3}/summary.json

agent/planning/out/final_root_fix_pilot_20260715/root_fix_run_1/planning_run/_planning/semantic_closure/implementability_report.json
agent/planning/out/final_root_fix_pilot_20260715/root_fix_run_1/planning_run/_planning/candidate_planning_package/specs/mqtt_main/main_spec.json

evaluation/planning_utility/out/fixed_best_candidate_coder_3x_20260715/REPORT.md
evaluation/planning_utility/out/fixed_best_candidate_coder_3x_20260715/coder_run_{1,2,3}/summary.json
evaluation/planning_utility/out/data/results.md
evaluation/planning_utility/out/data/rq1_recalculated_metrics.json
```

本计划的 `READY_TO_EXECUTE` 只表示历史证据、架构边界、修改顺序、预算和停止门槛已经预声明；在 A0–A8 实际完成前，不得表述为 planning stability、sound-build utility 或 RQ1 readiness 已修复。
