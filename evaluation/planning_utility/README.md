# RQ1: MQTT Planning Utility Evaluation

## 1. 实验目标

本实验用于回答 SpecForge 的 RQ1：

```text
RQ1: For the MQTT minimum broker profile, do implementation-oriented protocol specs
produced by the planning agent improve the downstream utility of generated implementations?
```

中文表述为：

```text
在 MQTT minimum broker profile 上，planning agent 将 protocol facts 转换为
implementation-oriented protocol specs 后，是否能够提高 coder agent 生成实现的
可编译性、可修复性和行为正确性？
```

该问题直接对应 SpecForge 的核心研究贡献。现有工作分别研究了从 technical documents 中抽取 protocol facts，以及从 engineering specifications 生成代码；SpecForge 关注两者之间缺失的中间层，即 planning agent 如何把 factual protocol knowledge 转换为 coder 可执行的工程结构。

因此，本实验不把 planning agent 当作普通 summarizer，而是检验其生成的 schema-constrained protocol specs 是否能够为下游 coder 提供以下工程价值：

```text
protocol behavior decomposition
message and state modeling
module/file/function ownership
public ABI and dependency closure
wire and access grounding
cross-function contracts
negative constraints
implementation order
test obligations
```

本实验比较三种从相同协议输入到 runnable C implementation 的方法：

```text
M0 FS-Direct-Coder
    facts-derived compact implementation context -> direct code generation

M1 NL-Plan-Code
    protocol facts -> natural-language engineering plan -> code generation

M2 Full-SpecForge
    protocol facts -> planning agent -> structured protocol specs -> coder agent
```

核心比较为：

1. M0 vs. M1：自然语言工程计划相对 direct coding 是否提供额外价值。
2. M1 vs. M2：结构化、schema-constrained protocol specs 相对自然语言计划是否提供额外价值。
3. M0 vs. M2：完整 SpecForge planning pipeline 相对无显式 planning artifact 的总体收益。

## 2. 研究假设

本实验预注册以下方向性假设：

```text
H1:
M2 的 end-to-end success rate 高于 M0 和 M1。

H2:
M2 的 required behavior scenario pass rate 高于 M0 和 M1。

H3:
M2 的 initial/final compile success rate 高于 M0 和 M1，
并减少 API contract、type ownership 和 protocol model 相关缺陷。

H4:
M1 可能优于 M0，但自然语言 plan 不足以稳定替代
schema-constrained module/file/function specs。

H5:
M2 可能消耗更多 planning tokens，但其 successful implementation yield
和 behavior-correct yield 更高。
```

H1 和 H2 是主要假设。H3 用于解释失败机制，H4 用于区分“有无 plan”和“plan 的表示形式”，H5 用于分析质量与成本之间的 trade-off。所有假设均限定于当前 MQTT minimum profile。

## 3. 实验范围与分析边界

本实验从已有 protocol facts 开始，不评价 facts agent 从 technical documents 中抽取事实的准确性。当前实验只使用冻结的 MQTT minimum facts：`agent/facts/gold_facts/mqtt_min/protocol_facts.json`。因此，实验结论只涉及 MQTT 上的 planning 和 downstream coding utility。

实验终点是 minimum profile 下的 runnable protocol implementation：

```text
protocol facts
-> planning treatment
-> C project generation
-> compile/repair
-> runtime start
-> minimum behavior verification
```

实验不声称生成代码满足完整 RFC compliance。当前 behavior verifier 检查的是 minimum functional conformance、错误路径和有限 interop 场景。

本实验也不同于 `evaluation/spec_ablation`：

- `planning_utility` 比较完整 planning method 对 downstream implementation 的效用。
- `spec_ablation` 从同一套 reference specs 确定性投影不同 coder-facing views，用于更严格地隔离 specification form。

由于 M0/M1 与 M2 使用的 lowering 和 repair pipeline 不完全相同，本实验的主结论应表述为：

```text
完整 planning-to-specs-to-code pipeline 的系统级效用。
```

不能仅凭本实验把 M2 的全部收益归因于某一个 schema 字段、JSON 格式或 planning prompt。字段级和 specification-form 归因应由 `spec_ablation` 实验完成。

## 4. 实验对象

当前论文实验只覆盖 MQTT minimum profile：

| protocol | 被测角色 | transport | facts | target profile | binary / argv contract |
| --- | --- | --- | --- | --- | --- |
| MQTT | broker | TCP | `agent/facts/gold_facts/mqtt_min/protocol_facts.json` | `evaluation/planning_utility/target_profiles/planning_target_profile_mqtt.json` | `./mqtt_broker <port>` |

MQTT 用于检验以下 implementation challenges：

```text
MQTT:
  binary framing, variable-length fields, broker session state, pub/sub routing
```

`configs.py` 和 runner 当前只注册 MQTT，避免其他协议被误纳入本实验的样本、统计分析或论文结论。

## 5. 实验变量

### 5.1 Independent variable

主要 independent variable 是 planning method，共三个 levels：

```text
M0 = fs-direct-coder
M1 = nl-plan-code
M2 = full-specforge
```

### 5.2 Controlled inputs

MQTT 的三种方法固定使用同一份：

```text
protocol_facts.json
target_profile.json
minimum_v1 requirements
binary name
argv contract
transport role
behavior verifier
```

`input_hashes.json` 记录 protocol facts 和 target profile 的 SHA-256。只有 input hashes 一致的 runs 才能进入同一比较组。

target profile 是 evaluation-owned controlled input。M0/M1 的 generation prompt 可以看到它；M2 planning agent 只接收 `protocol_facts.json`，其 manifest 必须记录 `target_profile_visible_to_planner=false`。因此该 profile 用于固定实验角色和运行约束，而不是向 M2 planner 注入额外语义。

### 5.3 Experimental unit

一个实验单元定义为：

```text
one method × one independent MQTT end-to-end generation run
```

一次 run 必须拥有独立 output directory、完整 generation logs、compile/repair artifacts 和 behavior verification result。JSON validation retry 和 repair call 属于该 run 内部步骤，不是新的实验样本。

### 5.4 Blocking factors

分析时至少按以下因素分层：

```text
replicate index
model version
facts/profile hash
runtime/toolchain environment
```

由于实验对象固定为 MQTT，protocol 不再是 blocking factor。replicate index 和 execution environment 用于控制 LLM/API 时间波动；所有结果均按三种 method 分组报告。

## 6. M0: FS-Direct-Coder

### 6.1 设计目标

M0 表示没有显式 planning artifact 的 direct coding baseline。它用于回答：

```text
仅依赖 facts-derived compact implementation context、minimum requirements
和 runtime contract，是否足以生成可编译且行为正确的多文件 C 协议实现？
```

M0 不调用 planning agent，也不生成 `nl_plan.md` 或 structured protocol specs。

### 6.2 Model-visible input

runner 首先从公共 input source 构造 sanitized `allowed_inputs`：

```text
facts_view
target_profile
minimum_requirements
runtime_contract
input_hashes
```

但 M0 的 source-tree 和 pair-completion prompt 实际使用的是 compact implementation view：

```text
protocol_meta
target_profile
minimum_requirements
runtime_contract
facts_schema_version, if present
```

也就是说，M0 与其他方法共享相同 facts source，但并非把完整 `facts_view` 原样塞入每个 code-generation prompt。该限制用于形成无 planning 的紧凑 baseline，也意味着 M0 vs. M2 是 method-level comparison，而不是严格的 equal-visible-token representation comparison。

### 6.3 Generation flow

M0 的生成流程为：

```text
compact implementation view
-> source_tree_skeleton.json
-> sequential .h/.c pair completion
-> main.c completion
-> deterministic Makefile
-> static checks
-> Bounded Generic C Repair
-> runtime start probe
-> behavior verification
```

`source_tree_skeleton.json` 只能包含：

```json
{
  "files": [
    {"path": "module/name.h", "kind": "header", "order": 0},
    {"path": "module/name.c", "kind": "source", "order": 1},
    {"path": "main.c", "kind": "main", "order": 2}
  ]
}
```

禁止加入 role、function inventory、type inventory、dependency graph、ownership 或 behavior plan。每个非 `main.c` source 必须与同 stem header 成对出现。

### 6.4 Pair-wise code generation

M0 以 `.h/.c` pair 为 generation unit，按 skeleton order 顺序生成。`main.c` 单独生成。对于已生成 headers，runner 优先通过 Clang AST 提取完整 public declarations；Clang 不可用或解析失败时，回退到移除 comments/include guards 后的完整 header text。

header context 的约束为：

```text
preserve complete declaration blocks
do not split struct/enum/function declarations
maximum header context: 256 KiB
maximum source prompt: 4 MiB
```

需要明确的是，M0/M1 的 `.h` 由 LLM 在 pair completion 中生成；只有后续 header declaration extraction 和 `Makefile` rendering 是 deterministic。它们不使用 Full SpecForge 的 schema-driven deterministic header renderer。

## 7. M1: NL-Plan-Code

### 7.1 设计目标

M1 表示自然语言 planning baseline。它用于回答：

```text
如果先让模型把 protocol facts 转换为自然语言 engineering plan，
但不生成 machine-readable module/file/function specs，是否足以改善下游代码生成？
```

M1 与 M0 复用相同的 skeleton、pair completion、Makefile、static checks、Bounded Generic C Repair 和 behavior verifier。两者的主要增量差异是 `nl_plan.md` 及其 code-generation brief。

### 7.2 Natural-language plan input

NL planner 可见的 semantic view 为：

```text
sanitized facts_view, excluding duplicated minimum_v1
target_profile
minimum_requirements
runtime_contract
```

plan 应讨论 runtime behavior、主要 components、数据表示、parsing、state、handlers、error paths、resource ownership 和 minimum tests，但必须保持自然语言形式。

明确禁止：

```text
JSON/YAML specs
Markdown tables
project_strategy.json
module/function inventories
type ownership tables
dependency graphs
exact C prototypes
complete struct/enum definitions
PROTOCOL_MODULE_SPEC
FILE_SPEC
FUNCTION_SPEC
```

### 7.3 Code-generation brief

NL planning prompt 要求 `nl_plan.md` 以 `Code-generation brief` 结束，且该节最多包含 20 个 concise bullets。后续 skeleton 和 pair completion 只消费压缩后的 brief：

```text
preferred source:
  Code-generation brief section

fallback:
  first 60 lines of nl_plan.md

maximum visible brief:
  2,400 characters
```

因此，M1 测试的是“自然语言计划经压缩后对 coder 的指导作用”，而不是让每个 generation unit 读取完整 plan。

### 7.4 Guard boundary

当前 `nl_plan_guard` 只验证 `nl_plan.md` 存在且非空；prompt 负责禁止 structured artifacts，但 guard 不对 plan 内容做 deterministic schema/content classification。

`planning_artifact_guard` 允许 M1 仅生成 `nl_plan.md`，并禁止额外的 inventory、dependency graph、ownership table 或 behavior contract 文件。论文归档时应同时保留原始 prompt/response，以便人工抽查 plan 是否违反自然语言边界。

## 8. M2: Full-SpecForge

### 8.1 设计目标

M2 是完整 SpecForge setting。它用于回答：

```text
planning agent 生成的 implementation-oriented、schema-constrained protocol specs，
是否能比 direct coding 和自然语言 planning 更稳定地驱动 coder agent？
```

### 8.2 Planning flow

M2 的完整流程为：

```text
planning plan --facts ... --out ...
-> planning validate --run-dir ...
-> inspect physical specs and module/file/function inventory
-> coder validate
-> coder --skip-repair generate into coder_original
-> hash original sources and compile a temporary no-repair copy
-> repair the original project into a separate copy, with at most 3 rounds
-> final clean compile
```

`planning validate` 在临时副本上执行，避免覆盖 plan-time manifest 和 diagnostics。只要 physical specs 中至少各有一个 `PROTOCOL_MODULE_SPEC`、`FILE_SPEC` 和 `FUNCTION_SPEC`，无论 `qualification_passed`、planning validate return code 或 fatal 标记如何，都进入 coder validate。`qualification_passed` 保留原值，不能把 candidate-only 伪装为 qualified。Coder validate passed 后才生成原始代码；repair 只操作独立副本。本 workflow 的 endpoint 是 final clean compile，behavior smoke 记录为 `not_run_out_of_scope`。

planning agent 的预期产物包括：

```text
SUMMARY.md
PROTOCOL_MODULE_SPEC
per-file FILE_SPEC
per-function FUNCTION_SPEC
generation order
dependency and public-symbol constraints
wire/access mappings
call contracts
forbidden symbols
test vectors
TRACE_ID / DOC_REF / TRACE_REFS where supported
```

这些 artifacts 不是仅供阅读的报告，而是 coder agent 直接消费的 engineering contract。

### 8.3 Coder flow

Full SpecForge 使用现有 coder pipeline：

```text
load and validate manifest.specs_root
-> deterministic header rendering
-> LLM source generation, including main.c
-> deterministic Makefile
-> compile
-> existing source-level repair
-> behavior verification
```

M2 不修改 planning prompt、spec compiler、coder schema、coder generation prompt 或 coder repair prompt。这样可保证 M2 代表实际 Full SpecForge system，而不是为实验单独构造的简化版本。

### 8.4 Reusing an existing planning run

`--full-planning-dir protocol=PATH` 可复制并复用已有 planning output。复用模式适合：

```text
coder-only diagnosis
fixed-spec repeated code generation
planning artifact debugging
```

但 fresh planning 与 reused planning 不能混入同一个 end-to-end planning utility estimate。复用 run 的 `planning_status` 为 `passed_existing`，必须单独标记和报告；其 planning token/time 也不能按 0 计入 fresh end-to-end cost。

## 9. 三种方法的流程对比

| stage | M0 FS-Direct-Coder | M1 NL-Plan-Code | M2 Full-SpecForge |
| --- | --- | --- | --- |
| facts source | gold protocol facts | same | same |
| explicit planning artifact | none | `nl_plan.md` | structured protocol specs |
| planning representation | none | prose + brief | schema-constrained JSON specs |
| source layout | LLM skeleton | LLM skeleton guided by brief | module spec generation order |
| headers | LLM-generated pair headers | same | deterministic from specs |
| source files | LLM pair completion | same + plan brief | existing coder from full specs |
| `main.c` | separate LLM unit | same | existing coder generation |
| `Makefile` | deterministic | deterministic | deterministic |
| generation validation | strict JSON + one retry | same | existing planning/coder validators |
| repair | Bounded Generic C Repair | same | existing coder repair |
| behavior verifier | shared | shared | shared |

该表同时定义了实验的 interpretation boundary：M0 vs. M1 较接近 planning-artifact 增量比较；M1 vs. M2 和 M0 vs. M2 则是完整 method/pipeline comparison。

## 10. Anti-Leak 与输入隔离

### 10.1 Baseline allowed inputs

M0/M1 只允许使用：

```text
sanitized facts_view
target_profile
minimum_requirements
runtime_contract
previously generated project headers/source context
compiler/linker/runtime diagnostics during repair
```

facts sanitizer 会移除 source-document local paths，例如 `specs-example`、`protocol-example` 和本地 absolute paths。

### 10.2 Forbidden inputs

M0/M1 禁止读取或调用：

```text
planning agent
structured protocol specs
specs-example
gold_specs
PROTOCOL_MODULE_SPEC
FILE_SPEC
FUNCTION_SPEC
planning dependency graph
wire/access bindings
planning validators
```

`leakage_guard` 扫描 `allowed_inputs/`、`source_tree_skeleton.json` 和 generated project。`planning_artifact_guard` 检查 baseline output 中是否出现禁止的 planning artifacts。

一个 baseline run 只有在以下 guards 均通过时才可进入主分析：

```text
leakage_guard.status == passed
planning_artifact_guard.status == passed
nl_plan_guard_status == passed, for M1
```

guard failure 属于 treatment contamination，不应计作普通 code-generation failure。

## 11. Generation Reliability Controls

M0/M1 共享以下 reliability controls：

```text
model: qwen3-max-2026-01-23, as recorded in run_manifest.json
temperature: 0.2
top_p: 0.2
streaming: enabled
JSON completion limit: 16,384 tokens
JSON retry: at most one retry per generation unit
```

Skeleton 和 pair completion 必须返回 strict JSON object。deterministic parser/schema validation 失败时，允许从头重新生成一次。该 retry 只用于修复 output serialization/schema failure，不计入 repair budget，但必须通过以下字段报告：

```text
json_retry_count
json_generation_attempts
failed_generation_unit
```

M2 使用 planning/coder 各自冻结的模型参数。论文运行时必须同时记录 planning 与 coder 的实际 model identifier、temperature、top_p、completion limit 和 API date，不能只引用 baseline manifest。

## 12. Repair 设计

### 12.1 M0/M1: Bounded Generic C Repair

M0/M1 generation 后执行 Bounded Generic C Repair。repair 只能读取 generated project 内的 `.h`、`.c`、`Makefile`、compiler/linker diagnostics 和 runtime argv contract。

repair 顺序为：

```text
source-tree completeness check
-> clean build + header/source syntax checks
-> deterministic C1/C2 repairs
-> bounded LLM source/header repairs
-> bounded link repairs
-> runtime start probe
-> at most one runtime startup repair
-> behavior verification
```

总 LLM repair calls 上限为 6。实现中的全局 call budget 同时约束 source/header、link 和 runtime repair；其中 source/header repair 最多 3 次，link repair 最多 2 次，runtime startup repair 最多 1 次。

repair 接受 patch 的基本条件包括：

```text
patch scope limited to target or allowed same-stem pair
project remains inside generated tree
no tests/logs/spec artifacts modified
build/root-cause count improves
large semantic rewrite rejected
stub/TODO rejected where disallowed
```

### 12.2 C1-C5 defect taxonomy

Bounded repair 使用以下 diagnostic taxonomy：

| category | meaning | typical examples | generic repair policy |
| --- | --- | --- | --- |
| C1 | Local C/build defects | missing standard include、syntax、feature macro、Makefile flag | deterministic/LLM repairable |
| C2 | Header/declaration visibility | non-self-contained header、missing public declaration/header | deterministic/LLM repairable |
| C3 | API contract drift | signature、argument、return、linkage、lifecycle mismatch | only low-risk/mechanically clear cases |
| C4 | Type/ownership drift | struct layout、opaque boundary、duplicate concrete type、ownership mismatch | planning-dependent by default |
| C5 | Protocol model drift | packet/message fields、parser/serializer/handler semantics | planning-dependent; behavior rewrite forbidden |

C4/C5 及无法从局部 declarations 机械推断的 C3 被记录为 `planning_dependent_remaining`，不通过 generic repair 注入协议设计。

### 12.3 M2 repair boundary

M2 使用现有 coder 的 source-level repair，预算由 `--max-repair-rounds` 控制；M0/M1 使用 `--max-repair-calls`。二者不是同一个 repair algorithm，`repair_iterations` 也不是严格相同的计量单位。

因此，实验应分成两个互补 tracks：

```text
Track A — Pre-repair generation quality:
  比较 generation completion、initial compile success 和统一离线 diagnostics。
  该 track 更接近 planning output 对首次 code generation 的直接影响。

Track B — Native end-to-end system utility:
  各方法使用其预定义的完整 pipeline 和冻结 repair budget，
  比较 final compile、behavior 和 end-to-end success。
  该 track 衡量真实系统方法的最终效用。
```

不要把 M0/M1 的 `llm_repair_calls` 与 M2 的 `repair rounds` 直接做均值显著性比较。若需要跨方法比较 repair cost，应统一换算为实际 LLM repair call count、repair tokens 和 wall-clock time。

## 13. Runtime 与 Behavior Verification

编译成功后，M0/M1 先执行 runtime start probe：

```text
MQTT:
  broker process remains alive and its TCP port accepts a connection
```

随后三种方法调用同一个 `agent.coder.protocol_behavior_val` MQTT verifier。当前 minimum scenarios 为：

| protocol | required self-contained scenarios | optional interop scenarios | total |
| --- | ---: | ---: | ---: |
| MQTT | 5 | 1 (`mosquitto_pub/sub`) | 6 |

5 个 required scenarios 分别验证 cross-client pub/sub、EOF 前 buffered packet 处理、malformed packet 后存活、DISCONNECT cleanup 和重复 pub/sub smoke。optional `mosquitto_pub/sub` interop tool 缺失时记录 `skipped`，不能当作 `passed`，也不能使 required behavior rate 的分母变化。

## 14. 实验执行协议

### 14.1 Publication run matrix

建议论文主实验对 MQTT 的每种 method 至少执行 10 次独立 fresh runs：

```text
1 MQTT profile × 3 methods × 10 replicates = 30 runs
```

当前 `run_matrix.py` 每次对选定 cell 执行一次 run；replicates 通过多次调用 CLI 获得。每个 replicate 必须保存独立 timestamped output，不允许覆盖或挑选最优结果。

如果成本限制无法完成 10 次，应报告实际 `n`，给出置信区间，并避免只用单次 run 得出稳定性结论。

### 14.2 Run ordering

`run_matrix.py` 当前按 method 顺序串行执行 MQTT runs。正式实验应在外层调度中随机化或轮换 method order，避免 API load、time-of-day 或 model service drift 与 method 固定顺序混淆。

每个 replicate 内应保证：

```text
same facts/profile hashes
same code revision
same compiler/tool versions
same behavior verifier revision
same model snapshot
same repair budgets
same optional interop tool availability
```

### 14.3 Failure and rerun policy

以下结果计为方法失败，不允许静默 rerun：

```text
planning validation/generation failure
strict JSON generation exhausted after one retry
source-tree/static-check failure
compile failure after allowed repair
runtime startup failure
required behavior failure
```

只有预先定义的 infrastructure failure 可排除并重跑，例如 API authentication outage、network transport failure、disk failure 或 experiment harness crash。所有排除都必须保留原 run、记录原因，并在结果中报告 exclusion count。

optional interop dependency 缺失只产生 `skipped`，不是 infrastructure exclusion。

### 14.4 Fresh vs. cached planning

主 end-to-end analysis 只使用 fresh M2 planning runs。复用 `--full-planning-dir` 的结果进入单独的 fixed-planning/coder-variance analysis，用于回答：

```text
在 protocol specs 固定时，coder generation 本身有多大随机波动？
```

## 15. 指标

### 15.1 Primary metrics

#### End-to-end success

每个 run 定义：

```text
E2E_success = 1
iff applicable generation/planning guards pass
and final compile passes
and runtime startup passes where applicable
and every required behavior scenario passes
```

optional interop `skipped` 不影响 required E2E，但 interop failure 必须单独报告。论文主表按 method/protocol 报告：

```text
successful runs / total valid runs
success rate
95% confidence interval
```

#### Required behavior scenario pass rate

```text
required_behavior_pass_rate =
passed required scenarios / (passed required scenarios + failed required scenarios)
```

未进入 behavior stage 的 run 不能从分母中消失。run-level analysis 中应将其 required behavior outcome 记为失败；scenario-level诊断表可另行标记为 `not_run_due_to_upstream_failure`。

#### Final compile success

```text
final_compile_success = 1 iff compile_status == passed
```

该指标衡量允许 repair 后是否得到可链接 binary，但不能替代 behavior correctness。

### 15.2 Secondary metrics

```text
initial compile success
runtime_start_status
optional interop pass/fail/skip
generation completion rate
planning/readiness/protocol-specs validation success, for M2
JSON retry rate
failed generation unit
repaired file count
actual LLM repair call count
repair token usage
planning-dependent remaining defect count
failure stage and failure category
generated source/header LoC
workflow token usage
stage token usage
wall-clock time
```

### 15.3 Diagnostic metrics

RQ1 的 supplementary mechanism analysis 使用冻结的
`target_profiles/mqtt_obligation_rubric.json`，对三种方法统一计算：

```text
required obligation realization
executable call-path closure
semantic grounding closure
```

这些指标的分母来自 evaluation-owned MQTT obligations/capabilities，而不是各工程自行生成的
API/type 数量。它们用于解释 planning 如何影响 code-level engineering closure，不能替代
compile、runtime behavior 或 E2E primary endpoints。原有 header/API/dependency/ownership 指标仅作为
code-internal diagnostics 保留。

M0/M1 的 `repair_summary.json` 自动记录：

```text
C1-C5 before/fixed/remaining counts
header/source/link errors before and after repair
deterministic and LLM patch counts
semantic risk count
generated stub/wrapper count
duplicate symbol repairs
signature alignment repairs
portability repairs
final root causes
```

这些 C1-C5 counts 当前不是 M2 adapter 的统一输出。若用于三方法横向比较，必须在保存的 pre-repair projects 上运行同一版本的 read-only diagnostic snapshot，并冻结 classifier revision。否则 C1-C5 只能用于 M0/M1 内部失败分析。M2 的 generate-only 产物可用以下只读命令生成统一 snapshot、specified-function definition coverage、placeholder 和 near-empty source 报告；命令在临时副本中编译并校验原始源码 hash 不变：

```bash
python3 -m evaluation.planning_utility.no_repair_analysis \
  --project-dir <coder-project-dir> \
  --specs-root <planning-manifest-specs-root> \
  --binary-name mqtt_broker
```

### 15.4 Cost metrics

推荐报告：

```text
total prompt tokens
total completion tokens
total tokens
planning tokens
code-generation tokens
repair tokens
elapsed seconds
tokens per successful run
tokens per passed required scenario
```

当前 M0/M1 `summary.json` 聚合 generation + bounded repair usage。M2 adapter 的 `workflow_token_usage` 主要来自 coder manifest，不能自动代表 planning + coder 的完整总成本。M2 的 end-to-end cost 必须再从 `planning_run` artifacts 聚合 planning usage；若无法可靠聚合，应把 planning token cost 标为 missing，而不是按 0 计算。

## 16. 统计分析方法

### 16.1 Main effect reporting

对三个 primary metrics，至少报告：

```text
per-method raw numerator/denominator
per-method MQTT success rate
per-replicate outcome matrix
95% confidence interval
```

由于只有 MQTT，不计算 cross-protocol macro-average 或 pooled protocol rate。behavior scenario 指标应同时报告 run-level required behavior success 和各 MQTT scenario 的 pass/fail counts，避免仅汇总全部 scenario 后掩盖某一固定场景的系统性失败。

### 16.2 Pairwise comparisons

预注册 comparisons 为：

```text
M0 vs. M1
M1 vs. M2
M0 vs. M2
```

对 binary run-level outcomes，报告各 method 的 exact binomial confidence interval，并对预注册 method pairs 使用 Fisher exact test；也可使用仅包含 method fixed effect 的 logistic model 作为补充。对 token、time、defect counts 等偏态指标，报告 median、IQR 和 bootstrap confidence interval。

主要 effect sizes 为：

```text
absolute risk difference
relative risk or odds ratio
median token/time difference
median defect-count difference
```

三个 planned pairwise comparisons 使用 Holm correction。统计显著性不能替代 effect size、raw outcomes 和跨 replicates 的稳定性。由于没有跨协议复现证据，统计结论必须限定在 MQTT minimum profile。

### 16.3 Failure-stage analysis

每个失败 run 按最早 blocking stage 归类：

```text
input/guard
planning
spec validation/readiness
source-tree generation
code generation
static/header validation
compile/link
runtime start
behavior
```

failure-stage distribution 用于解释方法为何失败，但不应把 downstream 未执行 stages 重复计为新的失败。

## 17. 公平性控制与可复现性

正式实验需要冻结并归档：

```text
git commit and dirty-worktree patch
facts/profile files and SHA-256 hashes
planning/coder/baseline prompts
model identifiers and decoding parameters
compiler, libc, make, Clang and Python versions
OS/container image
behavior verifier revision
repair budgets
optional interop tool versions
all run manifests and raw logs
```

`matrix_summary.md` 只用于快速浏览，论文统计必须从 machine-readable `summary.json`、`run_manifest.json` 和 `repair_summary.json` 聚合，不能从终端输出人工抄录。

所有 methods 必须使用同一 behavior verifier revision。行为测试不能读取 method label，也不能因 method 改变 expected result。

## 18. 结果解释

### 18.1 M0 vs. M1

如果 M1 优于 M0，可说明把 protocol facts 先转换为自然语言 engineering guidance 对 code generation 有帮助。该收益来自 NL planning stage 和 brief，而不能归因于 structured specs、schema validation 或 deterministic header lowering。

如果 M1 没有明显优于 M0，可能说明自然语言 plan 在压缩为 2,400-character brief 后不足以维持跨文件 API、type ownership 和 protocol state consistency；也可能说明 plan quality 波动抵消了 planning 收益。

### 18.2 M1 vs. M2

如果 M2 优于 M1，可支持以下系统级结论：schema-constrained protocol specs、validation/closure、structured engineering semantics 和 spec-driven coder pipeline 的组合，比自然语言计划更适合驱动多文件协议实现。

该比较不能单独证明某个字段有效，也不能解释为“JSON 优于 Markdown”。M2 同时改变 information structure、validation、header lowering 和 repair context。

### 18.3 M0 vs. M2

该比较衡量 SpecForge planning pipeline 相对 compact direct-coding baseline 的总体收益，是研究主张最直接的 end-to-end evidence。

若 M2 主要提高 compile success 但没有提高 behavior pass rate，只能说明 specs 改善了工程闭合或 ABI 一致性，不能说明 protocol semantics 已被正确实现。

若 M2 同时提高 behavior pass rate，并减少 C3/C4/C5 defects，才更有力地支持 planning agent 将 protocol facts 转换为 actionable engineering contracts 的研究主张。

## 19. 有效性威胁

第一，当前实验只覆盖 MQTT minimum profile。结果能够支持三种 planning methods 在 MQTT 上的相对比较，但不能直接推广到 text protocols、UDP protocols 或其他 network protocol families。跨协议 generalizability 需要独立实验验证。

第二，实验使用 gold MQTT protocol facts，因此隔离了 facts extraction noise。结果不能证明 facts agent 能从任意 technical documents 生成同等质量的输入。

第三，M0 的 code-generation prompt 使用 compact implementation view，而 M1 的 plan stage 和 M2 planning stage 能够读取更丰富的 facts。该设置衡量完整 method utility，不是 equal-visible-information 的纯表示实验。

第四，M0/M1 由 LLM 生成 headers，M2 从 specs deterministic render headers。M2 的收益可能同时来自 planning quality 和 deterministic lowering。该混淆应通过 pre-repair analysis 与 `spec_ablation` 补充解释。

第五，M0/M1 和 M2 使用不同 repair algorithms 和预算单位。raw repair iterations 不可直接比较，native end-to-end result 只能解释为 system-level utility。

第六，M1 的 guard 当前只验证 plan 非空，不机械判定 plan 是否包含被禁止的 structured content。需要保留 prompt/response 并进行 contamination audit。

第七，当前 behavior tests 只覆盖 MQTT minimum profile，不等价于完整 MQTT specification conformance。通过全部 scenarios 只能表述为 MQTT minimum behavior success。

第八，MQTT interop 依赖外部 `mosquitto_pub/sub` CLI。环境缺失会产生 `skipped`，因此 interop 必须与 required self-contained scenarios 分开统计。

第九，LLM 服务可能随时间发生 nondeterministic variation 或 backend drift。必须固定 model snapshot、记录时间，并通过多次独立 runs 和 method-order rotation 缓解。

第十，复用已有 planning output 会移除 planning variance 和部分 planning cost。fresh 与 reused runs 必须分开分析。

第十一，M2 adapter 当前没有把 planning token usage 自动合并进 coder token usage。若直接比较 `workflow_token_usage`，会低估 M2 end-to-end cost。

## 20. 推荐论文表述

英文表述：

```text
To evaluate the downstream utility of the planning agent, we compare three methods
that start from the same protocol-fact and target-profile sources: a compact direct
coding baseline without an explicit planning artifact (FS-Direct-Coder), a
natural-language planning baseline (NL-Plan-Code), and the full SpecForge pipeline,
which produces schema-constrained module-, file-, and function-level protocol specs
before code generation. We evaluate these methods on the MQTT minimum broker profile,
using final compilation, required MQTT behavior scenarios, and end-to-end success as
primary outcomes. The comparison is intentionally system-level: the full
pipeline includes spec validation and deterministic header lowering, whereas the
baselines use pair-wise header/source generation and bounded generic C repair.
Accordingly, field-level attribution is studied separately through specification-form
ablation. Because the present experiment is limited to MQTT, we do not claim
cross-protocol generalizability.
```

中文表述：

```text
为评估 planning agent 的下游效用，我们比较三种从相同 protocol facts 和 target profile
来源出发的方法：不生成显式 planning artifact 的 compact direct-coding baseline
（FS-Direct-Coder）、自然语言 planning baseline（NL-Plan-Code），以及在代码生成前产生
schema-constrained module/file/function-level protocol specs 的完整 SpecForge pipeline。
实验只覆盖 MQTT minimum broker profile，并以 final compile、required MQTT behavior scenarios
和 end-to-end success 为主要指标。该比较是 system-level evaluation：完整 pipeline 包含
spec validation 和 deterministic header lowering，而 baseline 使用 pair-wise header/source
generation 和 Bounded Generic C Repair。因此，单字段贡献由独立的 specification-form
ablation 实验分析，当前结果不主张具有跨协议 generalizability。
```

## 21. 运行方式

查看参数：

```bash
python3 -m evaluation.planning_utility.run_matrix --help
```

运行单个 M0 baseline：

```bash
python3 -m evaluation.planning_utility.run_matrix \
  --protocol mqtt \
  --method fs-direct-coder \
  --max-repair-calls 6 \
  --api-key-env ALI_API
```

运行单个 M1 baseline：

```bash
python3 -m evaluation.planning_utility.run_matrix \
  --protocol mqtt \
  --method nl-plan-code \
  --max-repair-calls 6 \
  --api-key-env ALI_API
```

运行单个 fresh M2：

```bash
python3 -m evaluation.planning_utility.run_matrix \
  --protocol mqtt \
  --method full-specforge \
  --max-repair-rounds 3 \
  --api-key-env ALI_API
```

运行单次 MQTT 三方法矩阵：

```bash
python3 -m evaluation.planning_utility.run_matrix \
  --protocol mqtt \
  --max-repair-calls 6 \
  --max-repair-rounds 3 \
  --api-key-env ALI_API
```

复用已有 Full SpecForge planning output：

```bash
python3 -m evaluation.planning_utility.run_matrix \
  --protocol mqtt \
  --method full-specforge \
  --full-planning-dir mqtt=/path/to/existing/planning_run \
  --api-key-env ALI_API
```

执行 MQTT planning，并让 coder 只生成代码，不执行项目 build、repair 和 behavior checks：

```bash
evaluation/planning_utility/commands/run_mqtt_planning_coder_generate_only.sh
```

该脚本默认读取 `ALI_API`；可通过 `API_KEY_ENV` 指定其他 API key 环境变量名。

## 22. 独立 Repair 诊断

对已有 M0/M1 project 副本执行 Bounded Generic C Repair：

```bash
python3 -m evaluation.planning_utility.repair_cli \
  --project-dir /path/to/coder_out/mqtt \
  --method fs-direct-coder \
  --protocol mqtt \
  --binary-name mqtt_broker \
  --argv-contract './mqtt_broker <port>' \
  --max-repair-calls 6 \
  --api-key-env ALI_API
```

CLI 先复制 source project，再只修改副本。原始 generated project 不会被覆盖。

批量 repair：

```bash
python3 -m evaluation.planning_utility.repair_cli \
  --batch-input repair_projects.csv \
  --jobs 4 \
  --max-repair-calls 6 \
  --api-key-env ALI_API
```

CSV 必须包含：

```text
project_dir,method,protocol,binary_name,argv_contract
```

`--dry-run` 在临时副本中验证 repair，不把修改后的源码写入 output tree。

## 23. Output Layout

默认 matrix 输出目录为：

```text
evaluation/planning_utility/out/<timestamp>/
├── matrix_summary.json
├── matrix_summary.md
└── mqtt/
    ├── fs-direct-coder/
    │   ├── allowed_inputs/
    │   ├── source_tree_skeleton.json
    │   ├── coder_out/
    │   │   ├── mqtt/
    │   │   └── _agent_logs/
    │   │       ├── run_manifest.json
    │   │       └── repair_summary.json
    │   └── summary.json
    ├── nl-plan-code/
    │   ├── allowed_inputs/
    │   ├── nl_plan.md
    │   ├── source_tree_skeleton.json
    │   ├── coder_out/
    │   └── summary.json
    └── full-specforge/
        ├── allowed_inputs/
        ├── planning_run/
        │   ├── mqtt_specs/                 # qualified run
        │   └── _planning/
        │       ├── run_manifest.json
        │       └── candidate_planning_package/specs/
        ├── coder_validate/
        ├── coder_original/
        │   ├── mqtt/
        │   ├── mqtt_repair_<timestamp>/mqtt/
        │   └── _agent_logs/
        │       ├── run_manifest.json
        │       ├── pre_repair_source_hashes.json
        │       ├── pre_repair_diagnostics.json
        │       └── post_repair_original_source_hashes.json
        ├── logs/
        └── summary.json
```

repair-only batch output 额外生成：

```text
batch_repair_summary.json
batch_repair_summary.md
```

## 24. Recorded Fields

各方法均可从 output artifacts 中获得：

```text
method / protocol
binary name / argv contract
planning and generation statuses
compile and smoke statuses
verification_success / scenario_counts
failure_stage / failure_categories / main_diagnostic
LLM call and token usage
repaired and blocking files
```

其中 input hashes 位于各方法的 `allowed_inputs/input_hashes.json`。M0/M1 另有 leakage/planning-artifact guards，其 stage timings 位于 `summary.json` / `run_manifest.json`；M2 目前通过 `command_log` 的 `started_at` / `ended_at` 记录各 CLI stage 的时间边界。

M0/M1 还记录：

```text
source_tree_skeleton_status
pair_completion_status
generated_pairs / generated_files
header_context_diagnostics
json_retry_count / json_generation_attempts
compile_before_repair / compile_after_deterministic / compile_after_llm
runtime_start_status
planning_dependent_remaining_count
C1-C5 repair metrics
```

M1 额外记录 `nl_plan_status`、`nl_plan_guard_status` 和 `nl_plan_guard`。M2 额外记录 `planning_run_status`、`qualification_passed`、`specs_generated`、`coder_loader_passed`、`fatal`、`fatal_reason_code`、`nonfatal_no_specs_count`、planning/readiness、coder validation 和 command logs。

## 25. Tests

运行 planning utility 自测：

```bash
python3 -m unittest discover -s evaluation/planning_utility/tests -v
```

同时确认既有 minimum matrix runner 未受影响：

```bash
python3 -m unittest agent.coder.tests.test_minimum_matrix_runner -v
```

README-only 更新不改变 runner behavior；上述 tests 用于验证文档所描述的 protocol configs、input sanitization、strict JSON retry、header extraction、guards、repair 和 output manifests 仍与实现一致。
