# SpecForge 网页端 GPT 背景知识包

本文档用于给网页端 GPT 或协作者提供 SpecForge 的项目背景。它合并了旧版项目描述和后续 planning 重构状态，并按当前代码更新到 `agent/planning/` 的实际实现。

更新时间：2026-06-09

## 1. 项目定位

SpecForge 是一个面向 network protocol implementation 的三阶段 agent pipeline，目标是从 technical documents 出发，经过 structured fact extraction、engineering planning 和 coder-facing specs compilation，最终驱动 coder agent 生成可运行的 protocol implementation。

整体 pipeline：

```text
Technical Documents
  -> Facts Agent
  -> protocol_facts.json
  -> Planning Agent
  -> spec_bundle/ + coder_manifest.json
  -> Coder Agent
  -> generated protocol implementation
```

三个 agent 的职责边界：

- **facts agent**：读取 RFC、标准、手册等 technical documents，抽取 evidence-backed `protocol facts`。
- **planning agent**：分析 `protocol facts` 和 `target_profile.json`，生成 implementation-oriented `protocol specs`、工程计划、模块/类型/函数契约和 coder-compatible spec bundle。
- **coder agent**：读取 `spec_bundle/`，生成 C protocol implementation，并通过 compile/repair loop 修复实现。

SpecForge 的核心研究贡献是 **planning agent**。它不是 summarizer，也不是 code generator，而是位于 factual protocol knowledge 和 concrete code 之间的分析、决策和规格生成层。

研究定位可以概括为：

> Existing work has explored extracting protocol facts from technical documents and generating code from engineering specifications, but there remains a gap between these two stages. SpecForge addresses this gap by designing a planning agent that transforms protocol facts into actionable protocol specifications and implementation plans.

## 2. 当前目录视图

核心目录：

```text
SpecForge/
├── agent/
│   ├── facts/       # technical documents -> protocol_facts.json
│   ├── planning/    # protocol_facts.json + target_profile.json -> spec_bundle/
│   ├── coder/       # spec_bundle/ -> generated C implementation
│   └── common/      # shared utilities
├── specs-example/   # coder 可读取的示例 specs 与 schema
└── document/        # 协议文档输入
```

三阶段之间主要通过文件 artifact 连接，而不是直接共享内存对象：

- facts 输出 `protocol_facts.json`
- planning 读取 `protocol_facts.json` 和 `target_profile.json`
- planning 输出 `spec_bundle/`、`coder_manifest.json`、sidecar 和 validation reports
- coder 读取 `spec_bundle/` 并生成 C project

这种 artifact-first 设计便于 traceability、resume、verification 和 evaluation。

## 3. Facts Agent

位置：

```text
SpecForge/agent/facts/
```

facts agent 是 pipeline 的第一阶段，负责把 technical documents 转换为结构化 `protocol facts`。它不直接生成代码，也不做 implementation planning。

当前主要输入是一组同一协议的 `.txt` technical documents。核心输出是：

```text
protocol_facts.json
```

当前 facts format：

```text
protocol_facts/v2alpha1
```

重要顶层字段包括：

- `protocol_meta`
- `transport`
- `interaction_model`
- `message_model`
- `state_model`
- `routing_model`
- `resource_model`
- `error_and_limits`
- `minimum_v1`
- `planning_inputs`
- `open_questions`
- `evidence_index`

facts agent 当前采用 hybrid extraction workflow：

1. 加载并规范化 technical documents。
2. 按章节和段落切分 chunk。
3. 做 surface discovery，识别协议表面和关键词。
4. 按 semantic category 做规则召回。
5. 用 rerank 阶段重排候选 chunk。
6. 在 token budget 内组装 prompt context。
7. 按类别调用 LLM 做 JSON extraction。
8. 做 cross-category reconciliation。
9. 汇总为 `protocol_facts.json`。
10. 用 verifier 检查结构完整性和 planning sufficiency。

关键代码：

- `agent/facts/document_loader.py`：加载文档并做文本归一化。
- `agent/facts/preprocess.py`：chunk 构造、章节切分、规则召回。
- `agent/facts/prompts.py`：surface discovery、category extraction、rerank、reconciliation prompt。
- `agent/facts/extraction.py`：主抽取流程、上下文装配、证据归一化、输出落盘。
- `agent/facts/verifier.py`：校验 `protocol_facts.json` 是否满足后续 planning 需要。

当前已有 gold facts 示例：

- `agent/facts/gold_facts/mqtt/protocol_facts.json`
- `agent/facts/gold_facts/mqtt_min/protocol_facts.json`
- `agent/facts/gold_facts/coap_min/protocol_facts.json`

## 4. Planning Agent

位置：

```text
SpecForge/agent/planning/
```

planning agent 是 SpecForge 的核心 artifact。它把 facts agent 输出的 factual protocol knowledge 转换为 coder agent 可消费的 engineering structure。

它的职责是：

- 消费 `protocol_facts.json`。
- 读取目标实现约束 `target_profile.json`。
- 识别 message formats、state machines、transport requirements、error handling、interoperability constraints。
- 区分 facts、inferred engineering decisions 和 open assumptions。
- 生成 module/file/type/function-level implementation plan。
- 派生 dependency graph，而不是让 LLM 直接编造 dependency graph。
- 编译 strict coder specs 和 sidecar，输出 coder-compatible `spec_bundle/`。
- 尽量保留从 facts/evidence 到 decisions/specs 的 traceability。

当前 input formats：

```text
protocol_facts/v2alpha1
target_profile/v1
```

当前 coder-compatible compiler 主要面向：

```text
language = C
```

### 4.1 当前输出

一次完整 run 会输出：

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

历史文档中提到过独立 `spec_blueprint/v1`；当前代码路径已经不把它作为实际输出阶段。现在 `agent/planning/stages/specs_compiler.py` 直接从 Step 5 的 `implementation_plan` lowering 到 coder specs、`coder_manifest.json` 和 sidecar。

默认输出目录：

```text
agent/planning/out/<protocol>/<target_slug>/<timestamp>/
```

### 4.2 核心设计原则

1. **facts 与 target directives 分离**

   `protocol_facts` 保留 facts agent 的协议事实；`target_directives` 独立保存 target profile，不混入 facts。

2. **LLM 生成局部 proposal，deterministic code 负责边界**

   LLM 必须返回 JSON candidate 或 patch。所有 LLM 输出都要经过 schema validation 和 semantic validation，不能直接成为最终 artifact。

3. **分阶段规划**

   planning agent 不让 LLM 一次生成完整 `implementation_plan/v1`。它把问题拆成 profile、constraints、architecture、implementation substage、specs compilation 等阶段。

4. **traceability 优先**

   facts adapter 为事实生成稳定 `fact_id`，保留 evidence refs、fact refs 和 target directive refs。strict coder specs 不接受 planning-only 顶层字段时，traceability 通过 sidecar 保存。

5. **downstream coder usability 优先**

   最终 specs 必须能被当前 coder agent 读取，尤其要满足 `PROTOCOL_MODULE_SPEC`、`FILE_SPEC`、`FUNCTION_SPEC` 的 strict schema 和 loader constraints。

### 4.3 主流程

当前主入口是：

```text
agent/planning/orchestrator.py
```

CLI 支持：

```text
validate
plan
verify
compare
```

整体流程：

1. **Step 0: Preflight**
   - 校验输入路径、target profile、language support。
   - 创建 run manifest、step logs、agent logs 和 validation reports。

2. **Step 1: Facts Input Adapter / Planning IR**
   - 读取 `protocol_facts/v2alpha1`。
   - 生成 `planning_ir/v1`。
   - 为 facts 生成 path-derived `fact_id`。
   - 建立 evidence map、field id normalization index 和 target directives namespace。

3. **Step 2: Protocol Profile**
   - 规则层先推导 baseline profile。
   - LLM 只能提交 `protocol_profile_patch_candidate`。
   - 输出 transport shape、interaction model、statefulness、routing/resource intensity、failure semantics、timing model、required capabilities 和 required surface units。

4. **Step 3: Engineering Constraints**
   - 根据 profile 确定性激活工程约束。
   - 例子包括 incremental decode、timer manager、session store、connection termination policy、canonical ownership。

5. **Step 4: Architecture Search & Selection**
   - 并发请求多个 LLM architecture candidates。
   - 当前 strategy 包括 `capability_clustered`、`layered_runtime_codec_semantic`、`minimal_scope`。
   - validator 检查 capability coverage、constraint refs 和 module boundary。
   - ranking 可由 LLM 给出；不合法时使用 deterministic ranking fallback。

6. **Step 5: Implementation Plan Synthesis**
   - 当前最复杂、也是 planning agent 的核心阶段。
   - 逐步生成 core design、module artifacts、type/data inventory、function inventory、C signatures、behavior contracts、wire/access binding、calls allowed、file layout、runtime entrypoint 和 dependency closure。

7. **Step 6: Coder Specs Compilation**
   - 从 Step 5 的 `implementation_plan` 直接编译 `spec_bundle/`。
   - 生成 strict coder specs、`coder_manifest.json` 和 sidecar。
   - 使用 coder schema 和 coder loader 做兼容性验证。

8. **Final Validation Report**
   - 汇总 diagnostics、coder compatibility、token usage、artifact paths 和 manifest status。

### 4.4 Implementation Plan Synthesis 当前结构

Implementation Plan Synthesis 采用 staged hybrid workflow。LLM 只生成当前 substage 的 candidate/patch；deterministic validator、reconciler、merger 和 fallback 决定是否接纳。

当前阶段编号以 `agent/planning/README.md` 和 `orchestrator.py` 为准：

| Stage | 当前作用 |
|---|---|
| `5.1_plan_skeleton` | deterministic 生成 `implementation_plan/v1` skeleton、id namespace、validation targets 和 deterministic indexes。 |
| `5.2a_core_design` | 生成 core design，包括 canonical types、state design、handler matrix、resource lifecycle、error strategy、test plan seed。 |
| `5.2b_module_artifacts` | 从 selected architecture 和 core design 生成 module-level `TYPE` / `FUNC` artifact seeds。 |
| `5.3_type_data` | controlled inventory：按 module 构建 type planning space，LLM 只填 slot semantics，deterministic reconciliation 生成 type/data inventory。 |
| `5.4a_function_inventory` | controlled inventory：按 module 构建 function planning space，LLM 只 annotate required seeds 并提出 module-local optional helpers，deterministic reconciliation 生成 function inventory。 |
| `5.4a.1_function_symbol_repair` | deterministic 修复 internal duplicate C-facing function name，保留 public/exported API symbol。 |
| `5.4b_function_signatures` | 按 module/batch 补全 C signatures、params、ownership、passing mode 和 signature dependencies。 |
| `5.4c_function_behavior_contract` | 补全 behavior contract、error behavior、state/resource access、internal type refs 和 service requirements。 |
| `5.4d_wire_access_binding` | 把 protocol wire fields 绑定到 parser/serializer/handler functions、access paths 和 wire mapping table。 |
| `5.4e_call_contracts` | 把 cross-module service requirements 解析为 concrete `calls_allowed` edges。 |
| `5.5a_file_layout` | 将已有 functions 分配到 source/header file layout，生成 imports/export/implements 信息。 |
| `5.5b_runtime_entrypoint` | 规划 deployable target 的 runtime entrypoint，例如 `main.c` lifecycle。 |
| `5.6_dependency_closure` | 从 signatures、state/resource access、calls_allowed、imports_allowed 派生 dependency graph；必要时只允许一次 dependency repair patch。 |
| `5.7_spec_readiness` | 运行 full implementation plan validator 和 dependency graph validator，确认可进入 specs compiler。 |

### 4.5 Controlled Inventory 重构

重构前的 Round 8 目标是提升 planning output 的 semantic density，同时控制 LLM 越权和不稳定性。当前代码中的关键结果是 `5.3` 和 `5.4a` 的 controlled inventory 设计：

- deterministic code 先生成 planning space、slots、seeds、legal refs 和 forbidden refs。
- LLM 只做 semantic filling/annotation，不直接决定 required identity、visibility、ownership、coverage 或 final IDs。
- JSON 非法只做 JSON retry；耗尽后可用 empty semantic candidate 进入 deterministic reconciliation。
- reconciliation 固定 required slots/seeds，吸收合法语义，丢弃 illegal optional proposals，并写入 diagnostics。
- validator 阻断 coder-breaking 问题，例如 unknown refs、public/private leak、missing mandatory coverage、invalid ownership、uncovered lifecycle obligation。
- richness/quality 问题进入 diagnostics/reporting，不再触发旧 repair patch、full retry 或 quality repair 主流程。

这项重构的意义是：把 LLM 从“自由生成 inventory”收窄成“为 deterministic planning space 填语义”，让最终 type/function inventory 更可复现、更容易验证，也更适合 downstream coder。

### 4.6 LLM 权限边界

LLM 可以生成：

- `protocol_profile_patch_candidate`
- architecture candidates 和 ranking
- core design candidate
- module artifacts candidate
- type filling candidate
- function annotation candidate
- function signature patch
- function behavior contract patch
- wire/access binding patch
- calls allowed candidate
- file layout candidate
- runtime entrypoint candidate
- dependency repair patch
- assumptions / unresolved questions

LLM 不可以生成：

- final C code
- final unrestricted `implementation_plan`
- final unrestricted `dependency_graph`
- new protocol facts
- unknown references
- cross-namespace references
- coder strict spec 顶层非法字段

`dependency_graph` 必须主要由 deterministic code 从 `imports_allowed`、`calls_allowed`、`signature_dependencies`、`state_access` 和 `resource_access` 派生。fallback 可以用于生成有效 artifacts，但不能伪装成 accepted LLM candidate。

### 4.7 Coder-Compatible Specs Compilation

`agent/planning/stages/specs_compiler.py` 负责从 `implementation_plan` 生成：

- 一个 `PROTOCOL_MODULE_SPEC`
- 多个 `FILE_SPEC`
- 多个 `FUNCTION_SPEC`
- `coder_manifest.json`
- `planning_traceability.json`
- `planning_decisions.json`
- `planning_ir_refs.json`

关键约束：

- strict specs 中不得出现 coder schema 不允许的 planning-only 顶层字段，例如 `TRACEABILITY`、`CAPABILITY_IDS`、`STATE_ACCESS`、`CALLS_ALLOWED`。
- planning-only semantics 通过 lowering 进入 coder dialect 或 sidecar。
- public C symbol、signature raw、header/source consistency 必须稳定。
- wire mapping 会下沉 smoke-level `TEST_VECTORS` 到 wire-facing function specs。
- canonical public type tree 会派生 `FORBIDDEN_SYMBOLS`，减少 coder 对不存在公共字段的臆造。
- specs compiler 不调用 LLM，也不新增 Step 5 中没有的工程语义。

兼容性验证包括：

- `agent.planning.validators.coder_schema.validate_coder_spec_bundle_against_schema()`
- `agent.coder.specs.load_spec_bundle_from_root()`

### 4.8 Resume / Stop / Logging

planning CLI 支持从顶层阶段或 Step 5 子阶段续跑：

```bash
python3 -m agent planning plan \
  --facts agent/facts/gold_facts/mqtt_min/protocol_facts.json \
  --target-profile agent/planning/planning_target_profile_mqtt.json \
  --resume-from-stage 5.4d
```

也支持指定阶段后停止：

```bash
python3 -m agent planning plan \
  --facts agent/facts/gold_facts/mqtt_min/protocol_facts.json \
  --target-profile agent/planning/planning_target_profile_mqtt.json \
  --stop-after-stage architecture
```

run 目录中：

- `_step_logs/` 保存阶段产物和 deterministic sidecar。
- `_agent_logs/` 保存 LLM attempts、patch candidates、rejection reasons、controlled inventory attempt summary。
- `_validation_reports/` 保存阶段 validator 报告。
- `013_token_usage_summary.json` 汇总 token usage。
- `014_planning_validation_report.json` 汇总最终结果。

## 5. 重构状态与当前质量判断

历史 Round 7/8 工作证明：重构后的 planning pipeline 能产出 coder-ready artifacts，但 semantic density、reference integrity、downstream coverage 和 token cost 仍是主要问题。

Round 8 的核心脉络：

- **Round 8A**：analysis-only，对比 old planning outputs、example specs 和当前 successful outputs，发现 module/file/function/type density 与 example-spec-level 仍有明显差距。
- **Round 8B**：扩展 planning space 和 prompt/context guidance，提升 type/function semantic density，但不引入硬性 function-count gate。
- **Round 8C**：尝试 non-blocking inventory quality warning 和 bounded repair，改善 type coverage，但暴露 generation variance。
- **Round 8D**：深化 behavior contracts、wire/access binding、calls_allowed 和 specs lowering，提升 downstream semantics。
- **Round 8E**：做 stability-gated acceptance；fresh live evidence 约为 `4/6` success，未达到 freeze 标准。

旧 Round 8E 报告中的重要结论仍可作为风险背景：

- 成功 run 可以通过 coder loader 和 strict manifest validation。
- `behavior_contract` 和 traceability 相对稳定。
- `wire/access` 和 `calls_allowed` 有提升但波动明显。
- module/file/function/type density 仍低于手写 `specs-example/mqtt_specs`。
- `LLM_JSON_INVALID`、`SEMANTIC_LLM_CANDIDATE_REQUIRED`、`FUNCTION_UNKNOWN_TYPE_REF`、`TYPE_CROSS_MODULE_PRIVATE_FIELD_REF` 等问题曾导致部分 live run 失败。
- token cost 偏高，热点集中在 behavior、calls_allowed、signature、inventory 等语义密集阶段。

需要注意：旧报告称 `planning_v2` 是独立 runtime，且使用旧 stage 编号。当前代码已经更新为 `agent/planning/` 下的 staged pipeline，且 `5.3/5.4a` controlled inventory 已替代旧的 type/function inventory repair 主流程。

当前可以概括为：

```text
Planning Agent 已具备端到端生成 coder-compatible specs 的能力，
但还不应被视为 semantic-enhanced baseline 已冻结。
```

## 6. Coder Agent

位置：

```text
SpecForge/agent/coder/
```

coder agent 是 pipeline 的第三阶段，负责从 planning agent 生成的 `spec_bundle/` 生成 C protocol implementation。

它当前以 C 语言 MQTT broker/server 形态作为主要落地示例。核心关注点是：输入规格是否结构完整、跨文件接口是否一致、能否生成可编译工程，以及 compile/repair loop 能否修复实现。

coder agent 输入 spec root，包含：

- `PROTOCOL_MODULE_SPEC`
- `FILE_SPEC`
- `FUNCTION_SPEC`

这些 specs 可来自 planning compiler，也可来自 `specs-example/mqtt_specs` 中的手写参考。

当前生成策略：

- `.h`：本地 deterministic rendering，避免 LLM 随意改 public interface。
- 普通 `.c`：LLM generation，输入 File Spec、Function Spec、dependency headers 和 consistency rules。
- `main.c`：本地模板生成。
- `Makefile`：本地模板生成。
- repair：LLM 读取当前文件、编译错误和依赖 header 后修复指定文件。

关键代码：

- `agent/coder/specs.py`：读取和校验 `PROTOCOL_MODULE_SPEC`、`FILE_SPEC`、`FUNCTION_SPEC`。
- `agent/coder/models.py`：定义 `SpecBundle`、`FileSpec`、`FunctionSpec` 等内部模型。
- `agent/coder/generation.py`：生成 header/source/main/Makefile，编译并 repair。
- `agent/coder/prompts.py`：source generation 和 repair prompt。
- `agent/coder/header_recipes.py`：公共类型和 header rendering。
- `agent/coder/verifier.py`：生成工程后的结构检查、编译检查、MQTT smoke test。

planning agent 输出质量会直接影响 coder agent 成功率。常见失败来源包括公共类型不一致、函数签名偏差、跨文件调用约定不稳定、规格中缺少 wire/access/test vector 细节。

## 7. 三阶段接口关系

SpecForge 的关键设计点是让每个阶段输出可验证 artifact：

```text
facts agent
  output: protocol_facts.json
  verified by: facts verifier

planning agent
  input: protocol_facts.json + target_profile.json
  output: spec_bundle/ + coder_manifest.json + sidecar
  verified by: planning validators + coder schema + coder loader

coder agent
  input: spec_bundle/
  output: generated C project
  verified by: spec diagnostics + compile + smoke tests
```

这种结构使 evaluation 可以分层进行：

- facts 是否 factual correct、有 evidence、有足够 planning information。
- planning 是否 preserved facts、处理 ambiguity、生成 coherent specs。
- coder 是否能从 specs 生成可编译、可运行代码。
- planning 是否减少 coder repair iterations 和 compile failures。

## 8. 网页端 GPT 讨论边界

讨论 SpecForge 时应遵守以下边界：

1. **planning agent 是核心贡献**

   facts 和 coder 都重要，但研究主线应围绕 facts -> protocol specs 的 planning gap。

2. **不要把 protocol facts 当作 implementation decisions**

   facts 是 technical documents 中抽取出的协议事实；implementation decisions 是 planning agent 在 target constraints 下推导出的工程选择。

3. **不要让 LLM 越权**

   LLM 可以提出 candidate/patch，但不能直接成为事实来源，不能直接生成最终 dependency graph，也不能直接生成 final code。

4. **uncertainty 必须显式化**

   缺失、模糊、冲突的信息应进入 assumptions 或 unresolved questions，而不是被模型补成看似合理的协议行为。

5. **每个新增 artifact 都要可验证**

   新 stage 或 output shape 应同步考虑 schema、validator、merger、fallback、traceability、resume behavior 和 tests。

6. **coder compatibility 是 planning 输出的硬约束**

   planning specs 最终必须 lower 到 coder 当前接受的 strict specs。planning-only 字段不能直接塞入 coder strict spec 顶层。

7. **evaluation 应贯穿三阶段**

   不只看最终代码是否编译，也要看 facts correctness、planning completeness、spec consistency、traceability 和 repair iteration reduction。

## 9. 当前可讨论的设计问题

适合继续讨论的问题：

- facts agent 的 evidence quality 如何量化。
- `protocol_facts/v2alpha1` 是否需要更正式的 JSON Schema。
- planning IR 应保留多少原始 facts 结构，多少 normalization index。
- protocol profile 的分类字段是否足以驱动 architecture 和 implementation planning。
- `5.3/5.4a` controlled inventory 的 planning space 是否还应压缩或扩展。
- type/data inventory 和 function inventory 的 output shape 是否过重。
- assumptions/unresolved questions 如何跨 stage 传播并影响 coder specs。
- dependency graph 应该从哪些 planning signals 派生，哪些不应由 LLM 直接决定。
- sidecar 与 coder strict specs 的边界是否足够清晰。
- coder repair loop 是否应该结合 compile diagnostics、spec diagnostics 和 planning traceability。
- evaluation benchmark 如何覆盖真实协议，而不是只围绕 MQTT minimum 优化。

## 10. 当前开发优先级

短期：

- 稳定 Step 5 的 reference integrity，尤其是 type refs、public/private boundary、function signature dependencies。
- 降低 `LLM_JSON_INVALID` 和 required candidate failure。
- 保持 controlled inventory 的 deterministic reconciliation 边界。
- 改善 calls_allowed、wire/access coverage 的 repeated-run stability。
- 保持 coder-compatible lowering 的严格边界。

中期：

- 建立 planning agent evaluation benchmark。
- 量化 planning 对 coder success rate、compile errors、repair iterations 的改善。
- 对比不同 planning strategy：无 planning、粗粒度 planning、staged validated planning。
- 扩展到 MQTT 之外的协议，例如 CoAP、FTP、SMTP、Redis RESP、HTTP subset。

长期：

- 形成可复用的 `protocol facts -> engineering specs` planning methodology。
- 把 traceability、uncertainty handling、validator-guided LLM planning 作为论文和系统贡献重点。
- 支持更多 protocol roles、transport shapes、state models 和 target runtimes。

## 11. 一句话总结

SpecForge 的目标不是把文档直接交给模型写代码，而是构建一个可验证的三阶段 protocol implementation pipeline：facts agent 提供 evidence-backed protocol facts，planning agent 将 facts 转化为 implementation-oriented protocol specs，coder agent 根据 specs 生成和修复 C implementation。其中 planning agent 是核心研究贡献，因为它承担了 protocol understanding 到 engineering specification 之间最关键的转换。
