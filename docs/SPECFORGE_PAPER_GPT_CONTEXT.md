# SpecForge 论文写作 GPT 背景知识

更新时间：2026-06-16

用途：本文档面向网页端 GPT 项目，作为撰写 SpecForge 对应论文时的背景知识。它强调当前实现进度、研究定位、系统设计、可用证据、实验口径和不能夸大的边界。

## 1. 使用规则

当你作为论文写作助手讨论 SpecForge 时，必须遵守以下规则：

1. 使用 **technical documents**、**protocol facts**、**facts agent**、**planning agent**、**coder agent**、**protocol specs**、**protocol implementation** 这些术语。
2. 明确区分三类内容：
   - `protocol facts`：来自 RFC、标准、手册等 technical documents。
   - `inferred engineering decisions`：planning agent 在 target profile 和工程约束下推导出的实现选择。
   - `open assumptions`：facts 不完整、模糊或冲突时显式记录的假设和待确认问题。
3. 不要把 example specs、gold specs 或 protocol-example 代码里的 implementation detail 当作 protocol facts。
4. 不要声称 SpecForge 已经完成所有协议的端到端 compile/smoke，除非具体 artifact 和日志已经证明。
5. 当前核心贡献必须围绕 **planning agent**。facts agent 和 coder agent 是重要支撑，但论文主线不是单纯事实抽取，也不是直接 code generation。

推荐研究主张：

```text
Existing work has explored extracting protocol facts from technical documents and generating code from engineering specifications, but there remains a gap between these two stages. SpecForge addresses this gap by designing a planning agent that transforms protocol facts into actionable protocol specifications and implementation plans.
```

## 2. 项目定位

SpecForge 是一个面向 network protocol implementation 的三阶段 intelligent agent pipeline。目标是从 technical documents 出发，经过 structured fact extraction、implementation-oriented planning 和 spec-to-code generation，最终生成并修复 runnable protocol implementation。

整体 pipeline：

```text
Technical Documents
  -> Facts Agent
  -> protocol_facts.json
  -> Planning Agent
  -> implementation_plan + spec_bundle + coder_manifest.json
  -> Coder Agent
  -> generated protocol implementation
  -> compile + smoke/behavior validation
```

三个 agent 的职责边界：

| Agent | 输入 | 输出 | 职责边界 |
|---|---|---|---|
| facts agent | technical documents | `protocol_facts.json` | 抽取 evidence-backed protocol facts，不做 implementation planning，不生成代码。 |
| planning agent | `protocol_facts.json` + `target_profile.json` | `implementation_plan`、`dependency_graph`、`spec_bundle/`、sidecar、validation reports | 核心研究 artifact。分析 facts，推导工程结构，生成 coder-facing protocol specs。 |
| coder agent | planning 生成或手写的 `spec_bundle/` | C protocol implementation + compile/repair/smoke logs | 从 specs 生成代码，执行 compile/repair，运行最小行为测试。 |

SpecForge 的 central research contribution 是 planning agent。它填补的是 protocol facts 与 code generation 之间的 middle layer：把事实性协议知识转化为可执行、可校验、可追踪的工程规格。

## 3. 仓库结构与关键材料

核心目录：

```text
SpecForge/
├── agent/
│   ├── facts/       # technical documents -> protocol_facts.json
│   ├── planning/    # protocol_facts.json + target_profile.json -> spec_bundle/
│   ├── coder/       # spec_bundle/ -> generated C project
│   └── common/      # shared LLM/rerank utilities
├── docs/            # 当前任务状态、稳定化计划、诊断报告
├── specs-example/   # 手写/参考 specs，用于 coder usability 和 oracle diagnosis
├── protocol-example/# 手写协议实现示例
└── document/        # 协议 technical documents
```

当前重要文档：

| 文件 | 用途 |
|---|---|
| `docs/CURRENT_TASK_STATUS.md` | 最新 planning 稳定化进度和下一步。 |
| `docs/PLANNING_STABILIZATION_TASKS.md` | 中期目标、验收标准和分步任务。 |
| `docs/specs_optimization/05_specs_optimization_decision_report.md` | specs 字段边界、消融证据和当前路线决策。 |
| `agent/planning/README.md` | planning agent 当前阶段设计和 CLI 行为。 |
| `agent/facts/README.md` | facts agent 的输入、输出和抽取流程。 |
| `agent/coder/README.md` | coder agent 的 spec contract、generate/verify/test 流程。 |
| `agent/coder/protocol_behavior_val/README.md` | HTTP、CoAP、MQTT、SMTP 的 min smoke 场景。 |

当前可复用 artifact：

| Artifact | 状态与用途 |
|---|---|
| `agent/planning/out/mqtt_step2_dependency_report_rerun/spec_bundle` | 最新成功的 MQTT Step 2 specs_compile baseline，可作为下一步 coder generate/compile/smoke 输入。 |
| `agent/planning/out/mqtt_step1_main_rerun_20260616_1600/spec_bundle` | Step 1 successful baseline，可作为回退输入。 |
| `agent/planning/out/mqtt_step2_dependency_report_rerun/_validation_reports/014_planning_validation_report.json` | planning final validation success，facts/coder/schema/loader compatibility 均 passed。 |
| `agent/planning/out/mqtt_step2_dependency_report_rerun/_validation_reports/008_dependency_validation_report.json` | dependency report 已包含 provenance arrays。 |
| `agent/out/mqtt_specs_regression_20260610/_agent_logs/run_manifest.json` | 手写 MQTT specs 驱动 coder 生成并 compile success 的 evidence，repair rounds 为 0。 |

## 4. Facts Agent 当前实现

位置：

```text
agent/facts/
```

facts agent 当前目标是从同一协议的一组 `.txt` technical documents 中抽取结构化事实，输出：

```text
protocol_facts.json
run_manifest.json
_agent_logs/
```

当前 `protocol_facts.json` schema version：

```text
protocol_facts/v2alpha1
```

顶层字段：

```text
schema_version
protocol_meta
transport
interaction_model
message_model
state_model
routing_model
resource_model
error_and_limits
minimum_v1
planning_inputs
open_questions
evidence_index
```

抽取流程：

1. 加载 technical documents。
2. 文本归一化。
3. 按章节和段落切分 chunk。
4. 做 surface discovery。
5. 按 semantic category 做规则召回。
6. rerank 候选 chunk。
7. 在 token budget 内组装 context。
8. 分类别调用 LLM 做 JSON extraction。
9. 跨类别 reconciliation。
10. 汇总为 `protocol_facts.json`。
11. verifier 检查结构完整性和 planning sufficiency。

当前支持的 gold facts：

```text
agent/facts/gold_facts/mqtt/protocol_facts.json
agent/facts/gold_facts/mqtt_min/protocol_facts.json
agent/facts/gold_facts/coap_min/protocol_facts.json
```

当前限制：

- 主要支持 `.txt` technical documents。
- 不支持 PDF、扫描件和 OCR。
- 事实抽取依赖 LLM。
- `verify` 是结构和 planning sufficiency 检查，不是完整语义正确性判定器。

论文写作建议：

- facts agent 可以作为 pipeline 的 first stage 和 evidence-backed input producer 描述。
- 不应把 facts agent 写成论文唯一贡献。
- 如果讨论 evaluation，应把 facts correctness、evidence coverage 和 planning sufficiency 作为独立维度。

## 5. Planning Agent 当前实现

位置：

```text
agent/planning/
```

planning agent 是 SpecForge 的核心。它把 `protocol_facts.json` 和 `target_profile.json` 转换成 implementation-oriented artifacts，并最终 lower 成 coder agent 可读的 strict `spec_bundle/`。

当前 input formats：

```text
protocol_facts/v2alpha1
target_profile/v1
```

当前 coder-compatible compiler 主要面向：

```text
language = C
```

一次完整 planning run 的关键输出：

```text
planning_ir/v1
protocol_profile/v1
engineering_constraints/v1
selected_architecture/v1
implementation_plan/v1
dependency_graph/v1
spec_bundle/
coder_manifest.json
planning_traceability.json
planning_decisions.json
planning_ir_refs.json
validation reports
token usage summary
planning_run_manifest
```

### 5.1 设计原则

1. **facts 与 target directives 分离**

   `protocol_facts` 保存协议事实，`target_profile` 保存目标实现约束。planning 不把 target directives 混入 facts。

2. **LLM 生成 local candidate/patch，deterministic code 决定最终边界**

   LLM 输出必须是 JSON candidate 或 patch，并经过 schema validation、semantic validation、reference validation 和 coder compatibility validation。

3. **分阶段规划**

   planning agent 不让 LLM 一次生成完整 implementation plan，而是拆成 profile、constraints、architecture、implementation substages 和 specs compilation。

4. **traceability 优先**

   facts adapter 为事实生成稳定 `fact_id`，并通过 sidecar 保存 facts/evidence 到 decisions/specs 的引用链。

5. **coder usability 是硬约束**

   最终输出必须能被 coder agent 当前 strict schema 和 loader 读取，尤其要满足 module/file/function spec 的路径、签名、dependency 和 rendered header 约束。

### 5.2 主流程

Planning CLI 支持：

```text
validate
plan
verify
compare
```

主流程：

| 阶段 | 作用 | LLM 权限 |
|---|---|---|
| Step 0 Preflight | 校验输入路径、target profile、language support，初始化 manifest。 | 不参与。 |
| Step 1 Facts Input Adapter / Planning IR | 读取 facts，生成 normalized planning IR、fact ids、evidence map、unresolved facts。 | 不参与。 |
| Step 2 Protocol Profile | 推导 transport、interaction、statefulness、routing/resource、timing、failure semantics 和 required capabilities。 | 只能提交 `protocol_profile_patch_candidate`。 |
| Step 3 Engineering Constraints | 根据 profile 确定性激活 incremental decode、timer manager、session store、ownership 等约束。 | 不参与。 |
| Step 4 Architecture Search & Selection | 并发生成多个 architecture candidates，校验 capability coverage 后 ranking。 | 生成 architecture candidate 和 ranking，不能生成 dependency graph。 |
| Step 5 Implementation Plan Synthesis | 生成 core design、module/type/function inventory、signatures、behavior contracts、wire/access、calls、file layout、runtime entrypoint、dependency closure。 | 只生成当前 substage 的 candidate/patch。 |
| Step 6 Coder Specs Compilation | 从 implementation plan 编译 `PROTOCOL_MODULE_SPEC`、`FILE_SPEC`、`FUNCTION_SPEC` 和 sidecar。 | 不参与。 |
| Final Validation | 汇总 diagnostics、coder compatibility、token usage 和 artifacts。 | advisory text 不改变 pass/fail。 |

### 5.3 Step 5 子阶段

当前 Step 5 是 planning agent 的核心，也是论文应重点描述的部分：

| 子阶段 | 当前作用 |
|---|---|
| `5.1_plan_skeleton` | deterministic 生成 `implementation_plan/v1` skeleton、id namespace 和 validation targets。 |
| `5.2a_core_design` | 规划 canonical types、state design、handler matrix、resource lifecycle、error strategy、test plan seed。 |
| `5.2b_module_artifacts` | 从 selected architecture 和 core design 生成 module-level `TYPE` / `FUNC` artifact seeds。 |
| `5.3_type_data` | controlled inventory：deterministic planning space 先定义 type slots，LLM 只填语义，reconciliation 生成最终 type/data inventory。 |
| `5.4a_function_inventory` | controlled inventory：deterministic planning space 先定义 required function seeds，LLM annotate required seeds 并提出合法 optional helpers。 |
| `5.4a.1_function_symbol_repair` | deterministic 修复 internal duplicate C-facing function name，保留 public/exported API symbol。 |
| `5.4b_function_signatures` | 按 module/batch 补全 C signatures、params、ownership、passing mode 和 signature dependencies。 |
| `5.4c_function_behavior_contract` | 补全 behavior contract、error behavior、state/resource access、internal type refs 和 service requirements。 |
| `5.4d_wire_access_binding` | 把 protocol wire fields 绑定到 parser/serializer/handler functions、access paths 和 wire mapping table。 |
| `5.4e_call_contracts` | 把 cross-module service requirements 解析为 concrete `calls_allowed` edges。 |
| `5.5a_file_layout` | 将 functions 分配到 source/header file layout，生成 imports/export/implements 信息。 |
| `5.5b_runtime_entrypoint` | 规划 deployable target 的 runtime entrypoint，例如 `main.c` lifecycle。 |
| `5.6_dependency_closure` | 从 signatures、state/resource access、calls_allowed、imports_allowed 派生 dependency graph；必要时只允许有限 dependency repair patch。 |
| `5.7_spec_readiness` | 运行 full implementation plan validator 和 dependency graph validator，确认可以进入 specs compiler。 |

Controlled inventory 的意义：

- deterministic code 先生成 planning space、slots、required seeds、legal refs 和 forbidden refs。
- LLM 不直接决定 required identity、visibility、ownership、coverage 或 final IDs。
- JSON 非法只做 JSON retry，耗尽后可使用 empty semantic candidate 进入 deterministic reconciliation。
- reconciliation 固定 required slots/seeds，吸收合法语义，丢弃 illegal optional proposals，并写入 diagnostics。
- validator 阻断 coder-breaking 问题，例如 unknown refs、public/private leak、missing mandatory coverage、invalid ownership 和 uncovered lifecycle obligation。

这可以作为论文的方法贡献之一：把 LLM 从 unrestricted generator 收窄为 controlled semantic filler，用 deterministic validation 和 reconciliation 提升可复现性、可解释性和 downstream usability。

### 5.4 LLM 权限边界

LLM 可以生成：

```text
protocol_profile_patch_candidate
architecture candidates and ranking
core design candidate
module artifacts candidate
type filling candidate
function annotation candidate
function signature patch
function behavior contract patch
wire/access binding patch
calls allowed candidate
file layout candidate
runtime entrypoint candidate
dependency repair patch
assumptions / unresolved questions
```

LLM 不可以生成：

```text
final C code
final unrestricted implementation_plan
final unrestricted dependency_graph
new protocol facts
unknown references
cross-namespace references
coder strict spec 顶层非法字段
```

`dependency_graph` 必须主要由 deterministic code 从 `imports_allowed`、`calls_allowed`、`signature_dependencies`、`state_access` 和 `resource_access` 派生。fallback 可以生成有效 artifacts，但不能伪装成 accepted LLM candidate。

## 6. Coder Agent 当前实现

位置：

```text
agent/coder/
```

coder agent 是 pipeline 第三阶段，负责从 planning 生成或手写的 `spec_bundle/` 生成 C protocol implementation。

当前输入：

```text
PROTOCOL_MODULE_SPEC
FILE_SPEC
FUNCTION_SPEC
```

核心流程：

```text
load spec_bundle
-> normalize paths
-> build SpecBundle
-> static consistency validation
-> deterministic header generation
-> LLM source generation
-> deterministic main.c and Makefile generation
-> compile
-> source-level repair loop
-> behavior validation
-> run_manifest.json
```

生成策略：

| 产物 | 生成方式 | LLM 是否参与 |
|---|---|---|
| `.h` | deterministic renderer，根据 `HEADER.DATA` 和 `HEADER.INTERFACE` 生成 canonical public interface。 | 否 |
| 普通 `.c` | LLM source generation，prompt 包含 File Spec、Function Spec、dependency headers 和 consistency rules。 | 是 |
| `main.c` | deterministic template。 | 否 |
| `Makefile` | deterministic template。 | 否 |
| repair `.c` | LLM 读取当前文件、编译错误和 canonical headers 后修复。 | 是 |

当前 coder CLI：

```bash
python3 -m agent coder --spec-root <spec_bundle> validate
python3 -m agent coder --spec-root <spec_bundle> generate
python3 -m agent coder --spec-root <spec_bundle> --output-dir <out> verify
python3 -m agent coder test --protocol mqtt --project-dir <project_dir> --binary mqtt_broker
```

重要边界：

- Header、`main.c`、Makefile 不进入 LLM repair。
- 如果 compile error 指向 deterministic header，coder 会停止 repair 并记录 blocking header error，提示应修 specs 或 header lowering。
- 行为测试在 compile 成功后运行；当前文档描述中，行为测试失败不会触发 repair，也不会改变 `generate` 的成功退出状态。

当前协议行为测试支持：

| 协议 | slug | 角色 | min 场景数 |
|---|---|---|---|
| HTTP/1.1 | `http` | server | 9 |
| MQTT | `mqtt` | broker | 6 |
| CoAP | `coap` | server | 6 |
| SMTP | `smtp` | server | 8 |

这些测试是 minimum smoke，不覆盖完整 RFC。论文中应把它们描述为 minimum functional conformance 或 behavior smoke tests，而不是 full protocol compliance tests。

## 7. Protocol Specs 与 Artifact 边界

Planning agent 输出的 strict coder specs 主要包括：

```text
PROTOCOL_MODULE_SPEC
FILE_SPEC
FUNCTION_SPEC
```

strict specs 应保留的关键信息：

- identity、trace ids、module/file names、paths；
- `MODULES[].DEPENDENCIES` 和 generation order；
- `HEADER.DEPENDENCY`、`HEADER.SYSTEM_DEPENDENCY`；
- public `HEADER.DATA.TYPE_SPEC`；
- `HEADER.INTERFACE` 和 canonical signatures；
- `SOURCE.DEPENDENCY`、`SOURCE.DATA`、`SOURCE.INTERFACE`；
- function `SIGNATURE`、`RELY`、`LOGIC` 或 `EVENT`；
- behavior/action detail；
- call/access/forbidden/test constraints。

适合 sidecar 的信息：

- module/file `DOC_REF`；
- protocol/fact traceability；
- planning decision rationale；
- target directive refs；
- planning IR refs；
- 部分不被 coder generation 消费的 nested `ROLE`；
- function-level redundant `PUBLIC_SYMBOLS`。

适合 validator-only artifact 的信息：

- validation reports；
- dependency diagnostics；
- rendered header reports；
- oracle/diff manifests；
- repair statistics；
- candidate validation reports。

当前重要结论：

```text
不要整体压缩 coder-facing strict specs。
应保留 strict specs 主体，优先修复 deterministic lowering、dependency closure、public/system type closure 和 final readiness validation。
```

依据：

- `Gold-Full` specs 可以驱动 coder compile/smoke。
- `Gold-Min-Interface` 在 MQTT/CoAP 上明显退化。
- behavior detail、test vectors、calls/rely 对 coder usability 有价值。
- traceability 字段可下沉到 sidecar。
- 当前瓶颈不是 specs 过细，而是 lowering、closure 和 readiness validation 缺口。

## 8. 当前实现进度，2026-06-16

当前中期目标：

```text
让 planning 输出能稳定驱动 coder compile/smoke，
并在 MQTT、CoAP、SMTP 的最小功能集上复现。
```

### 8.1 已完成：Step 1 deterministic closure 与 readiness gate

Step 1 已完成。核心修复方向：

1. dependency fallback fail-closed：不再通过清空 `calls_allowed` / `imports_allowed` 制造 empty graph success。
2. cross-layer dependency diagnostics：`call_contracts`、`calls_allowed`、`signature_dependencies`、`imports_allowed`、dependency graph 不一致会 blocking。
3. HEADER/SOURCE dependency lowering 分离：public ABI/type closure 驱动 header deps，implementation calls/source include needs 驱动 source deps。
4. public type dependency lowering：public signatures、struct fields、typedef/alias、callback signatures 和 function pointer params 的 external type refs 会映射到 provider header。
5. system type registry：`size_t`、`ssize_t`、`uint*_t`、`bool`、`sockaddr_in`、`time_t` 等 public system type 会 lower 到 `HEADER.SYSTEM_DEPENDENCY`。
6. final coder compatibility strict gate：planning readiness 调用 `load_spec_bundle_from_root(..., validate_rendered_headers=True)`。
7. rendered header dummy translation unit compile 已接入 strict rendered header validation。
8. MQTT fresh planning 已进入 specs_compile 并产出 `coder_manifest.json` 和 `spec_bundle/`。

Step 1 验证记录：

```text
planning finalize stage=specs_compile status=success
000_planning_run_manifest.json status=success
014_planning_validation_report.json status=success
facts_compatibility_status=passed
coder_compatibility_status=passed
coder_schema_status=passed
coder_loader_status=passed
008_dependency_validation_report.json status=passed
planning verify: No diagnostics
coder validate: No diagnostics
unittest fallback: 153 tests passed
pytest 当前环境不可用: No module named pytest
```

可复用输出：

```text
agent/planning/out/mqtt_step1_main_rerun_20260616_1600/spec_bundle
```

### 8.2 已完成：Step 2 cross-layer dependency provenance

Step 2 已完成。核心目标是让 dependency/call/header/source/module dependency 表达不会互相漂移，并让 validation report 能解释每条 dependency/call edge 的来源。

已完成内容：

1. `dependency_validation_report/v1` 新增 `errors`、`warnings`、`dependency_sources`、`call_edge_sources`、`header_dep_sources`、`source_dep_sources`。
2. `errors/warnings` 增加 `stage`、`entity_kind`、`entity_id`、`source_fields`、`suggested_repair`。
3. `call_edge_sources` 解释 `calls_allowed`、`call_contracts` 与 `dependency_graph.function_edges` 的一致性。
4. `dependency_sources` 覆盖 dependency graph module/file/function edges，并追溯 `imports_allowed`、`signature_dependencies`、`calls_allowed`、`call_contracts`。
5. specs_compile 后从生成的 FILE_SPEC 读取 `HEADER.DEPENDENCY` / `SOURCE.DEPENDENCY`，回填 `header_dep_sources` / `source_dep_sources`。
6. coder loader 显式校验 `HEADER.DEPENDENCY` / `SOURCE.DEPENDENCY` 引用的 header path 必须存在。

Step 2 验证记录：

```text
unittest: 156 tests passed
compileall: passed
specs_compile resume output: agent/planning/out/mqtt_step2_dependency_report_rerun
resume planning finalize stage=specs_compile status=success
resume planning verify: No diagnostics
resume coder validate: No diagnostics
dependency report keys include:
  errors
  warnings
  dependency_sources
  call_edge_sources
  header_dep_sources
  source_dep_sources
```

Step 2 dependency report summary：

```text
module_edge_count=17
file_edge_count=19
function_edge_count=143
calls_allowed_count=143
call_contract_count=143
signature_dependency_count=83
imports_allowed_count=9
diagnostic_count=0
dependency_sources=179
call_edge_sources=143
source_dep_sources=15
header_dep_sources=0
```

注意：`header_dep_sources=0` 是因为该 MQTT bundle 当前没有 `HEADER.DEPENDENCY`，不是 report 字段缺失。

最新成功 baseline：

```text
agent/planning/out/mqtt_step2_dependency_report_rerun/spec_bundle
```

### 8.3 Fresh run 的当前失败边界

Step 2 fresh planning output：

```text
agent/planning/out/mqtt_step2_dependency_report_fresh
```

状态：

```text
fresh planning 未进入 specs_compile
failure reason: unrelated readiness_missing_runtime_error_test blocking failed
```

解释：

- 这个失败不是 Step 2 dependency report/provenance 缺口。
- fresh dependency report 已包含 plan-level provenance arrays。
- 它暴露的是新的 readiness 缺口，属于下一阶段需要处理的 blocker。

### 8.4 尚未完成：coder generate/compile/smoke

截至 2026-06-16，最新 `mqtt_step2_dependency_report_rerun/spec_bundle` 已经：

```text
planning verify passed
coder validate passed
schema/load/rendered header readiness passed
```

但尚未完成：

```text
coder generate
coder compile
MQTT minimum smoke tests
CoAP/SMTP minimum fresh planning reproduction
```

论文表述必须谨慎：

- 可以说：当前 planning stabilization 已使 MQTT baseline 进入 coder-valid `spec_bundle` 状态。
- 不应说：最新 planning output 已经生成并通过最终 MQTT broker smoke。
- 可以把下一步写为：run coder generate/compile/smoke using `agent/planning/out/mqtt_step2_dependency_report_rerun/spec_bundle`。

## 9. 已有实验与诊断证据

### 9.1 Gold/example specs 的 coder usability evidence

`specs-example/` 包含 MQTT、CoAP、HTTP、SMTP 的参考 specs。它们是 code-derived oracle，用于 coder usability、schema boundary 和 planning-vs-gold diagnosis，不是 protocol facts。

已记录的重要结论：

| 证据 | 含义 |
|---|---|
| MQTT/CoAP `Gold-Full` 均 compile + smoke pass | coder 主流程可用，rich specs 可驱动 implementation。 |
| `Gold-Sidecar-Only-Traceability` 对 MQTT/CoAP 无损 | module/file `DOC_REF` 等 traceability 可移出 strict specs。 |
| `Gold-No-Wire-Binding` 对 MQTT/CoAP 最终通过 | function-level `WIRE_MAPPING` 不是当前 hard requirement，但不能删除整体 wire/access 语义。 |
| `Gold-No-Behavior-Detail` 导致 MQTT compile failure、CoAP smoke degradation | behavior detail 影响 source generation 和 behavior correctness。 |
| `Gold-No-TestVectors` 使 CoAP smoke 明显退化 | test vectors 是协议敏感的 behavior anchors。 |
| `Gold-Min-Interface` 使 MQTT compile fail、CoAP smoke 退化 | aggressive strict spec compression 不成立。 |

来自 `agent/out/mqtt_specs_regression_20260610/_agent_logs/run_manifest.json` 的 MQTT 手写 specs evidence：

```text
compile_success=true
repair.stop_reason=compile_succeeded
repair.rounds_attempted=0
diagnostics=[]
workflow_token_usage.total_tokens=51199
```

论文中可以使用这些结果支持：

- specs 的结构和密度会影响 downstream code generation。
- traceability 和 validation artifacts 可以和 strict coder-facing specs 分层。
- behavior/test/call guidance 不是简单噪声，而是 coder usability 的关键信息。

### 9.2 Planning-vs-gold diagnosis

历史诊断显示，planning failure 主要来自 closure/validator 与 architecture/file-layout mismatch，而不是 strict specs 整体过重。

关键观察：

```text
HEADER.DEPENDENCY=0
HEADER.SYSTEM_DEPENDENCY=0
call_contracts 非空但 calls_allowed/function_edges 可能漂移
type/signature oracle 单独替换无法修复完整 bundle
P+GoldDependency 因 missing router failed
```

解释：

- 单独替换 type、signature 或 dependency 不能解决问题，说明 type/signature/dependency/file layout 强耦合。
- 因此正确路线是加强 deterministic lowering、closure 和 final readiness validation，而不是删减 specs 或强行复制 gold layout。

## 10. Evaluation 设计建议

论文 evaluation 应直接对应 planning agent 的贡献，不要只报告最终代码能否编译。

建议 research questions：

1. **RQ1: Planning correctness**

   planning agent 是否能 preserve protocol facts，并避免引入 unsupported protocol behavior？

2. **RQ2: Spec completeness and consistency**

   planning agent 是否能生成 coherent protocol specs，包括 message formats、state machines、transport、error handling、dependencies 和 public ABI？

3. **RQ3: Downstream coder utility**

   planning-generated specs 是否提高 coder compile success、减少 repair iterations、提升 smoke pass rate？

4. **RQ4: Traceability and uncertainty handling**

   planning agent 是否能保留 facts/evidence 到 engineering decisions/specs 的 traceability，并显式报告 missing/ambiguous/conflicting facts？

5. **RQ5: Stability across protocols**

   planning agent 是否能在 MQTT、CoAP、SMTP 等真实协议的 minimum profiles 上复现，而不是只 overfit 一个 toy protocol？

建议指标：

| 维度 | 指标 |
|---|---|
| Facts preservation | unsupported behavior count、fact coverage、evidence ref validity。 |
| Planning completeness | required capability coverage、message/state/transport/error coverage、unresolved questions quality。 |
| Spec consistency | schema pass、coder loader pass、rendered header pass、dependency graph consistency、public/system type closure。 |
| Downstream code generation | compile success、repair rounds、blocking header errors、source repair failures。 |
| Runtime behavior | min smoke pass count、interop scenario pass/skipped/failed。 |
| Reproducibility | run manifest completeness、token usage、artifact path stability、diagnostic specificity。 |
| Ablation impact | no-planning/coarse-planning/staged-validated-planning 对 compile/smoke/repair 的影响。 |

建议 baseline：

| Baseline | 含义 |
|---|---|
| Direct-doc-to-code | 直接把 technical documents 或摘要交给 coder。 |
| Facts-only-to-code | 只给 protocol facts，不经过 planning agent。 |
| Coarse planning | 只生成粗粒度模块和接口，不做 staged validation/dependency closure。 |
| Gold/example specs | code-derived oracle，用于上界和诊断，不能当 protocol facts。 |
| SpecForge planning | 当前 staged validated planning agent。 |

建议消融：

```text
without controlled inventory
without dependency provenance
without rendered header validation
without public/system type closure
without behavior contracts
without test vectors
without call contracts
sidecar-only traceability
minimal interface specs
```

## 11. 论文结构建议

可采用的论文结构：

1. **Introduction**
   - 背景：network protocols 从 technical documents 到 runnable implementation 很难。
   - 现有 work 常覆盖 document-to-facts 或 specs-to-code。
   - gap：缺少 facts-to-implementation-specification 的 planning layer。
   - SpecForge 提出 planning agent 作为 bridge。

2. **Motivating Example**
   - 以 MQTT minimum 为例。
   - 展示 facts 只说明 CONNECT、PUBLISH、SUBSCRIBE、topic matching、QoS0、session/lifecycle 等。
   - 解释 coder 需要的是 modules、types、headers、functions、dependencies、state access、error behavior、tests。
   - 说明 planning agent 的必要性。

3. **System Overview**
   - 三阶段 pipeline。
   - Artifact-first design。
   - facts/planning/coder 边界。

4. **Planning Agent Design**
   - planning IR。
   - protocol profile。
   - engineering constraints。
   - architecture search。
   - staged implementation plan synthesis。
   - controlled inventory。
   - deterministic dependency closure。
   - specs compilation and readiness validation。

5. **Protocol Specs**
   - strict coder specs 与 sidecar/validator-only artifacts 的分层。
   - module/file/function spec contract。
   - traceability 与 uncertainty 的保存方式。

6. **Implementation**
   - Python agent package。
   - Qwen LLM usage。
   - C target。
   - deterministic header/main/Makefile rendering。
   - source generation and repair loop。
   - behavior validators。

7. **Evaluation**
   - protocols：MQTT、CoAP、SMTP，可能附带 HTTP。
   - datasets：gold facts、technical documents、reference specs。
   - metrics：schema/load/header/dependency/compile/smoke/repair/traceability。
   - ablations：spec density、dependency closure、controlled inventory、validation gates。

8. **Discussion**
   - 不确定性处理。
   - overfitting 风险。
   - protocol coverage。
   - LLM nondeterminism 和 deterministic validation 的关系。

9. **Limitations**
   - 当前 facts agent 主要支持 `.txt`。
   - 当前 planning/coder 主要面向 C。
   - 当前 validation 是 minimum smoke，不是 full RFC conformance。
   - 最新 Step 2 output 尚未完成 coder generate/compile/smoke。
   - 多协议 fresh planning 仍在复现中。

## 12. 可直接使用的贡献表述

可以写：

```text
SpecForge introduces a planning agent that transforms evidence-backed protocol facts into implementation-oriented protocol specifications. The agent decomposes planning into protocol profiling, constraint activation, architecture selection, staged implementation plan synthesis, dependency closure, and coder-facing spec compilation. Instead of allowing an LLM to directly generate code or unrestricted specifications, SpecForge constrains LLM outputs to local candidates and patches, then uses deterministic reconciliation and validators to enforce reference integrity, dependency consistency, public type closure, and coder compatibility.
```

可以写：

```text
The planning agent is designed as the missing middle layer between protocol fact extraction and spec-driven code generation. Its output is not only prose guidance, but a structured spec bundle containing module, file, and function specifications, along with sidecar artifacts for traceability, decisions, and validation.
```

可以写：

```text
Our current implementation demonstrates that deterministic readiness gates are necessary. Earlier planning runs could appear successful while later failing in coder header/type/include validation. The stabilization work moves these failures into planning-time diagnostics through fail-closed dependency validation, public/system type closure, rendered header validation, and dependency provenance reports.
```

不要写：

```text
SpecForge has fully automated correct implementations for MQTT, CoAP, and SMTP.
```

更准确的写法：

```text
The current implementation has produced coder-valid MQTT spec bundles after planning stabilization, and the next step is to run coder generation, compilation, and minimum smoke validation from the latest Step 2 baseline. Multi-protocol reproduction for MQTT, CoAP, and SMTP remains the current engineering target.
```

不要写：

```text
Gold specs are protocol facts.
```

更准确的写法：

```text
Gold/example specs are code-derived oracle artifacts used for coder usability diagnosis and ablation, not sources of protocol facts.
```

## 13. 当前风险与开放问题

当前最重要 risks：

1. 最新 successful MQTT Step 2 baseline 还没有完成 coder generate/compile/smoke。
2. `mqtt_step2_dependency_report_fresh` 暴露 `readiness_missing_runtime_error_test` blocker，需要分类和修复。
3. CoAP/SMTP minimum 的 fresh planning 复现尚未完成。
4. LLM generation 仍存在 nondeterminism，论文实验需要多 run 或明确 single-run limitation。
5. Gold/example specs 是 implementation-derived oracle，不能被用于证明 protocol fact correctness。
6. minimum smoke tests 只能证明最小行为可用，不能证明 full RFC compliance。
7. 更严格 validation 会降低表面 success rate，但这是消除 false success 的正确方向。

下一步工程任务：

```text
1. 使用 agent/planning/out/mqtt_step2_dependency_report_rerun/spec_bundle 运行 MQTT coder generate/compile/smoke。
2. 若 MQTT coder compile/smoke 失败，分类为 specs 问题、coder source generation 问题、header/type closure 问题或 smoke behavior gap。
3. 修复 readiness_missing_runtime_error_test blocker。
4. 在 MQTT 通过后进入 CoAP/SMTP minimum fresh planning 复现。
5. 归档 planning run、spec_bundle、coder output、compile logs、smoke logs 和 token usage，作为论文 evaluation evidence。
```

## 14. 一句话总结

SpecForge 的目标不是把 technical documents 直接交给模型写代码，而是构建一个可验证的三阶段 protocol implementation pipeline：facts agent 提供 evidence-backed protocol facts，planning agent 将 facts 转化为 implementation-oriented protocol specs 和 engineering plans，coder agent 根据 specs 生成和修复 C protocol implementation。论文的核心应放在 planning agent，因为它承担了 protocol understanding 到 engineering specification 之间最关键、最容易被忽略的转换。
