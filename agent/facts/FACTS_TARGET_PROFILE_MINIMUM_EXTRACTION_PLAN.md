# Facts Target-Profile-Scoped Minimum Extraction 计划书

> 初次制定：2026-07-15
> 当前状态：`COMPLETED`
> 当前步骤：`F1 IN_PROGRESS`
> 核心对象：facts agent
> 修改边界：仅允许修改 `agent/facts/` 下的代码、测试、配置与文档
> 首个 pilot：MQTT 3.1.1 minimum broker
> 权威 technical document：`document/MQTT_3.1.1.txt`
> 结构与语义 oracle：`agent/facts/gold_facts/mqtt_min/protocol_facts.json`，仅用于 facts 模块离线评价，不得进入抽取 prompt

## 0. 执行摘要

本次优化的目标是：让 facts agent 从协议原始 technical documents 中，围绕 target profile 指定的目标角色、必要能力和部署边界，抽取一个证据闭合、范围闭合的最小 protocol facts 集合。输出 `protocol_facts.json` 的顶层结构、各语义分区、关键 nested fields、item field shape 和 value type 必须与 MQTT minimum gold facts 兼容；内容不要求逐字一致，但需要在目标 surface、codec、state、routing、errors、limits、deferred features 和 planning inputs 上语义相近。

目标数据流为：

```text
technical documents + target profile
  -> document normalization / section index
  -> protocol surface discovery
  -> profile semantic projection
  -> required-capability seed resolution
  -> protocol dependency closure
  -> scoped retrieval and category extraction
  -> evidence / scope / consistency verification
  -> gold-compatible protocol_facts.json + run_manifest.json + _agent_logs/
  -> facts-only structural and semantic comparison against mqtt_min gold facts
```

这里的“最小”不是由 facts agent 自行猜测，也不是把完整标准中的任意 `MUST` 条款拼接起来。最小范围由 target profile 声明，facts agent 只负责从 technical documents 中解析目标能力对应的协议表面和依赖闭包。

本 tranche 完成后不调用 planning agent，不生成 protocol specs，也不进入 coder。最终只运行 facts 自身 verifier 和 facts-owned comparator，分析候选输出是否与 gold facts 结构兼容、语义相当，并明确仍存在的差异。

## 1. 职责边界与研究定位

### 1.1 三类输入/输出必须分离

| 信息类型 | 权威来源 | 负责阶段 | 示例 |
| --- | --- | --- | --- |
| protocol facts | technical documents | facts agent | CONNECT 必须先于其他 Control Packet；SUBSCRIBE 需要 SUBACK；Remaining Length 使用 varint |
| target directives | facts-owned target profile | facts agent 只用于 scope | target role 为 broker；只要求 QoS0；不要求 TLS 和 persistence |
| comparison oracle | frozen MQTT minimum gold facts | facts-owned offline comparator | 结构 fingerprint、minimum surface、semantic obligation rubric |

facts agent 不得把 target directive 伪装成 technical-document normative fact。主协议事实必须由原始文档 evidence 支持；profile 指定的 exclusions 和 implementation assumptions 必须使用可识别的 `profile_*` evidence ID，并在相同的 `evidence_index` item shape 中指向 facts-owned target profile。所有无法闭合的问题进入 `open_questions`，不得静默补齐。

### 1.2 本 tranche 不验证 planning

planning agent 仍是 SpecForge 的核心研究 artifact，但不属于本 tranche 的修改或验收范围。facts 输出未来仍应能作为 planning 输入；本次只通过与既有 gold facts 的结构和语义比较间接判断其 downstream utility，不运行 planning 来证明。

SpecForge 的核心研究主张保持不变：

> Existing work has explored extracting protocol facts from technical documents and generating code from engineering specifications, but there remains a gap between these two stages. SpecForge addresses this gap by designing a planning agent that transforms protocol facts into actionable protocol specifications and implementation plans.

### 1.3 facts-owned target profile 的权威边界

target profile 存放在 `agent/facts/target_profiles/`，由 facts CLI 读取。它是受控 scope input，不是 technical document，也不是 gold answer。它可以声明：

- target role；
- 必须完成的行为或 use cases；
- 可接受的协议子集与 conformance mode；
- QoS、session、persistence、security 等 feature constraints；
- language、runtime、资源与部署限制；
- runtime / behavior acceptance contract。

它不得直接携带来自 gold facts、`protocol-example` 或 `specs-example` 的事实条目、packet closure 答案、模块、函数、类型、文件清单和实现答案。

## 2. 当前基线与失败证据

### 2.1 2026-07-15 MQTT fresh run

实际执行：

```bash
python3 -m agent facts extract \
  --protocol-name mqtt \
  --doc document/MQTT_3.1.1.txt \
  --output-dir agent/facts/out/mqtt_eval_20260715
```

结果：

| 指标 | 观测值 |
| --- | ---: |
| document chunks | 104 |
| LLM calls | 27 |
| workflow tokens | 290347 |
| wall time | 约 12 分钟 |
| protocol facts size | 约 116 KB |
| verifier result | failed |
| verifier errors | 36 |
| traceability gaps | 29 |
| planning insufficiency errors | 5 |
| coverage gaps | 2 |

当前输出在 JSON 顶层结构上接近 `protocol_facts/v2alpha1`，但 nested item shape、scope semantics 和 traceability 都没有达到 MQTT minimum gold facts 的可比水平。

### 2.2 和 MQTT minimum oracle 的关键差异

Oracle 目标 packet surface：

```text
CONNECT, CONNACK, SUBSCRIBE, SUBACK,
PUBLISH, PINGREQ, PINGRESP, DISCONNECT
```

fresh run 的主 packet surface：

```text
CONNECT, CONNACK, PUBLISH, SUBSCRIBE,
PUBACK, PUBREC, PUBREL, PUBCOMP, UNSUBSCRIBE
```

主要偏差：

- 漏掉 `PINGREQ`、`PINGRESP`、`DISCONNECT` 和主 catalog 中的 `SUBACK`；
- 将 QoS1/QoS2 acknowledgement 与 transaction state 纳入 minimum；
- 引入 retained message、persistent session 和 Will Message lifecycle；
- 将 `Packet Identifier`、`Topic Name`、`Topic Filter`、`QoS Level` 混入 packet-level `must_support_surface`；
- `planning_inputs.required_codec_scope` 来自完整 `surface_catalog`，没有服从 minimum scope；
- 输出包含大量 `disc_e*` 引用，但最终 `evidence_index` 没有 discovery evidence。

按 packet 名称做粗粒度比较，主 surface precision 为 44.4%、recall 为 50.0%；`minimum_v1.must_support_surface` precision 为 50.0%、recall 为 62.5%。该指标仅用于定位范围偏差，不作为最终 semantic score。

### 2.3 当前代码根因

| Root cause | 当前机制 | 后果 |
| --- | --- | --- |
| RC1 facts 无 profile 输入 | facts CLI 只接收 protocol name、documents、output dir | facts 无法确定角色和功能边界 |
| RC2 现有外部 profile 不可复用 | 当前 profile 位于 planning utility，且只有 role、language、runtime、`scope=minimum_v1` 和 deployment constraints | facts-only 修改既不能改其契约，也不能表达 QoS0、keepalive、graceful disconnect、wildcard、Will 等能力 |
| RC3 minimum 定义循环 | prompt 要求“minimum viable implementation”，却没有 profile 或 viability contract | 模型把完整标准中的 mandatory obligations 当作 minimum |
| RC4 generic `MUST` retrieval | `minimum_boundary` 主要检索 `must/required/support/conformance` | MQTT 全文都高分，只得到随机 normative 切片而非 capability closure |
| RC5 surface discovery 不完整 | 统一 10k-char context 从 104 chunks 中取局部片段 | packet catalog 遗漏，但后续 flows 又引用遗漏 packet |
| RC6 category 各自抽取 | message、state、resource、errors 独立召回和 merge | QoS2 state、retained storage 等不相关内容重新进入 scoped output |
| RC7 evidence 装配缺陷 | category prompt 可复制 discovery refs，final payload 只汇总 category evidence | `disc_e*` 全部成为 unknown evidence refs |
| RC8 verifier 只检查非空和局部映射 | 没有 profile coverage、dependency closure、excluded-feature leakage 检查 | 结构看似丰富，但范围错误不能在 facts 阶段被阻止 |
| RC9 无 gold-compatible comparator | 当前只有通用 verifier，没有比较 nested key/type shape 和 semantic obligations 的工具 | 无法回答候选 facts 是否与 `mqtt_min` gold facts 结构和语义相当 |

## 3. 唯一目标、成功定义与非目标

### 3.1 唯一目标

仅在 `agent/facts/` 内建立一条 protocol-generic 的 target-profile-scoped facts pipeline，使 facts agent 能从原始 technical documents 抽取目标实现所需的最小 factual closure，并用 facts-owned comparator 判断其与 MQTT minimum gold facts 是否结构兼容、语义相当。

### 3.2 Definition of Done

首个 MQTT pilot 完成时必须同时满足：

1. facts extract 和 facts verify 均成功退出；
2. target profile、technical document、model、prompt 和代码 hash 全部进入 manifest；
3. 每个 required capability 都有 `satisfied`、`partially_satisfied` 或 `unresolved` 的显式 resolution；
4. required packet、paired response、shared framing/fields、state、error 和 limit closure 完整；
5. 所有 concrete protocol fact 都能解析到 `evidence_index`；
6. profile directive 和 technical-document evidence 不混淆；
7. excluded QoS1/QoS2、Will、retained、persistent-session 等内容不进入 required implementation obligations；
8. 若 profile 排除规范中的 mandatory behavior，输出明确标记 `intentional_subset` 和 normative conflict，不声称完整 protocol conformance；
9. `protocol_facts.json` 的 schema version、顶层 keys、nested required keys、container types 和 item field shape 与 gold facts contract 一致；
10. gold semantic comparator 的 critical obligation rubric 通过，并输出逐维度差异而非只给一个总分；
11. gold facts、example implementation 和 reference specs 从未进入 extraction prompt 或 production retrieval；
12. frozen 3-run MQTT pilot 达到 3/3 facts verifier、3/3 structural comparator 和 3/3 critical semantic rubric pass；
13. `git diff --name-only` 显示本 tranche 的所有新增/修改文件均位于 `agent/facts/`。

### 3.3 非目标

- 从单一标准文档自动猜测用户想实现什么；
- 生成完整 MQTT 3.1.1 protocol facts encyclopedia；
- 让 facts agent 决定 C module、type、API、event loop 或 storage implementation；
- 追求与 gold facts 的逐字相等、相同 evidence ID 或相同 list length；
- 把 intentional subset 宣称为完整 MQTT conformance；
- 修改或运行 planning、coder、planning utility、specs schema 或其他模块；
- 用 planning specs、compile 或 runtime 结果验证 facts；
- 通过 MQTT-specific packet 白名单硬编码通过 pilot；
- 将 `protocol-example`、`specs-example` 或 gold facts 注入模型上下文；
- 在 `protocol_facts.json` 顶层增加 gold 中不存在的新公共字段。

## 4. Target Profile v2 契约

### 4.1 当前契约为何不够

当前 `evaluation/planning_utility/target_profiles/planning_target_profile_mqtt.json` 不在允许修改范围内，而且 `scope: minimum_v1` 只是一个标签，不能决定 minimum 的行为闭包。因此在 `agent/facts/target_profiles/` 内新增 facts-owned profile；不修改、不导入也不覆盖 planning utility 的既有 profile。

### 4.2 建议结构

```json
{
  "schema_version": "target_profile/v2alpha1",
  "protocol_name": "mqtt",
  "target_role": "broker",
  "language": "C",
  "runtime": "Linux epoll",
  "scope_policy": {
    "mode": "minimal_runnable_subset",
    "conformance_mode": "intentional_subset"
  },
  "required_capabilities": [
    {
      "capability_id": "connection_establishment",
      "summary": "Accept an MQTT 3.1.1 client connection and acknowledge it."
    },
    {
      "capability_id": "qos0_publish_subscribe",
      "summary": "Register subscriptions and route QoS0 publications to matching subscribers."
    },
    {
      "capability_id": "keepalive_exchange",
      "summary": "Respond to a client keepalive request."
    },
    {
      "capability_id": "graceful_disconnect",
      "summary": "Handle an explicit client disconnect."
    }
  ],
  "feature_constraints": {
    "delivery_qos": [0],
    "topic_filter_wildcards": "required",
    "session_persistence": false,
    "retained_messages": false,
    "will_messages": false,
    "authentication": false,
    "unsubscribe": false
  },
  "deployment_constraints": {
    "memory_limit": "low",
    "persistence": false,
    "tls_mode": "none"
  },
  "runtime_contract": {
    "binary_name": "mqtt_broker",
    "argv_contract": "./mqtt_broker <port>",
    "transport": "tcp"
  }
}
```

以上是 profile 示例，不是 facts 输出，也不是 MQTT prompt 中的 packet inventory。`required_capabilities` 描述 observable behavior；具体需要哪些 MQTT Control Packets、字段、状态和错误路径，必须由 technical-document evidence 和 dependency closure 推导。

### 4.3 Facts-local loader

target-profile loader/validator 放在 `agent/facts/target_profile.py`，只由 facts CLI、extractor、verifier 和 facts tests 使用。不得为了复用而移动到 `agent/common/`，也不得修改 planning utility 的 loader。

loader 至少提供：

- JSON object、schema version 和 required fields 校验；
- stable SHA-256；
- facts-visible semantic projection；
- protocol name、role、scope policy 和 capability ID 唯一性检查；
- feature/deployment constraints 的类型检查；
- profile contradiction 检查，例如 `persistence=false` 与 required capability 中的 durable session 冲突。

采用新流程后，`facts validate` 和 `facts extract` 的 `--target-profile` 都应为 required argument。需要完整协议抽取时也必须提供显式 `scope_policy.mode=full_protocol`，不得保留无 profile 时由模型猜 minimum 的旧路径。planning CLI 保持不变。

## 5. 目标输出契约

### 5.1 `protocol_facts.json`

`protocol_facts.json` 保持 `protocol_facts/v2alpha1`，顶层 key set 与 MQTT minimum gold facts 一致：

- `protocol_meta`；
- `transport`；
- `interaction_model`；
- `message_model`；
- `state_model`；
- `routing_model`；
- `resource_model`；
- `error_and_limits`；
- `minimum_v1`；
- `planning_inputs`；
- `open_questions`；
- `evidence_index`。

不得在顶层增加 `scope_resolution`、`target_profile` 等 gold 中不存在的公共字段。profile path/hash、scope closure 和 comparator 结果写入 `run_manifest.json` 或 `_agent_logs/`。

`protocol_meta` 对齐 gold 的 item shape：`protocol_name`、`source_documents`、`document_count`、`fact_source_type`、`target_scope`。其中 source documents 只列原始 technical documents；`fact_source_type` 明确写成 technical-document/profile scoped extraction，不伪装成 example implementation。

结构一致的定义不是 list length 相同，而是：

- 相同的 required object keys；
- 相同的 object/list/scalar container type；
- 相同语义 collection 中的 item 使用相同 field names；
- `value` wrapper、`summary`、`name`、`condition`、`required_action` 等字段不互相替代；
- `evidence_refs` 始终是 string list；
- `planning_inputs` 的 required fields 和 value types 与 gold 一致。

例如当前 prompt 在 `minimum_v1.must_support_surface` 中生成 `reason`，而 gold 使用 `summary`；优化后必须统一为 `name + summary + evidence_refs`，不能仅因信息含义接近而判定结构通过。

`minimum_v1` 在新流程中的语义固定为“target-profile-scoped factual view”，不是 facts agent 自主提出的工程方案：

- `must_support_surface`：只放协议 surface unit，例如 packet、command、method、response；
- `must_support_state_behaviors`：只放 selected surface 的必要协议状态；
- `must_support_error_paths`：只放 selected capability 会触达的错误路径；
- `must_support_limits`：只放 selected codec/state 的固定边界；
- `may_defer_features`：记录 target profile 明确排除的功能及其协议含义，item shape 与 gold 一致；
- `implementation_assumptions`：记录 server-only、QoS0-only、in-memory 等 profile scope assumptions，item shape 与 gold 一致；
- normative behavior 由 technical-document evidence 支持；profile exclusions/assumptions 使用 `profile_*` evidence ID，不能伪装成文档原文结论。

### 5.2 `evidence_index` 的双来源约束

为保持 gold-compatible structure，不新增 evidence source schema。通过 evidence ID prefix 和 `doc_path` 区分：

```text
doc_*      -> doc_path 指向原始 technical document，支持 protocol claim
profile_*  -> doc_path 指向 agent/facts/target_profiles/...，支持 scope directive
```

两类 evidence item 都保持 gold 的 `evidence_id`、`doc_path`、`section_hint`、`chunk_id`、`excerpt` shape。verifier 必须阻止 main protocol claims 只引用 `profile_*`，也必须阻止 scope assumption 被错误解释成 full-conformance protocol fact。

capability resolutions、dependency edges、normative conflicts 和 closure status 只写到 `_agent_logs/scope_resolution.json`，供 facts verifier、comparator 和调试使用，不作为新的公共 output contract。

### 5.3 `run_manifest.json` 与比较报告

在当前 model、chunk、category 和 token usage 基础上增加：

- target profile path 和 SHA-256；
- facts-visible semantic projection hash；
- `_agent_logs/scope_resolution.json` path 和 SHA-256；
- profile schema version；
- scope mode / conformance mode；
- capability counts；
- included/excluded/unresolved counts；
- dependency-closure status；
- evidence-closure status；
- selected section/chunk counts；
- leakage-guard status。

facts-owned comparator 额外输出 `_agent_logs/gold_comparison.json` 和 `_agent_logs/gold_comparison.md`，至少包含：

- top-level/nested structural differences；
- missing/extra/wrong-type paths；
- minimum surface precision/recall；
- semantic rubric 分项结果；
- gold-only implementation details；
- candidate-only facts；
- final classification：`structurally_equivalent`、`semantically_comparable`、`not_comparable`。

## 6. 新抽取算法

### 6.1 Stage A：Document index 与 protocol surface discovery

1. 保留现有 normalization 和 chunking。
2. 优先从目录、section heading、protocol type/code table 和明确的 format section 建立 surface catalog。
3. surface discovery 只回答名称、类型、方向、section anchor 和粗粒度依赖，不一次抽取全部字段和状态。
4. catalog completeness 通过文档内 table/section 对照检查，不依赖单个 10k-char prompt 覆盖全文。

该阶段仍可发现完整协议 surface，但只形成轻量 index；它不等于最终 protocol facts 全量抽取。

### 6.2 Stage B：Profile semantic projection

facts agent 只读取 profile 中与协议语义相关的字段：

- target role；
- scope/conformance mode；
- required capabilities；
- feature constraints；
- 与协议行为直接相关的 deployment constraints。

language、具体 C module、source layout 等 engineering 信息不参与 document retrieval；facts 只把与 `planning_inputs` 结构兼容所需的 target constraints 物化为摘要，不生成工程设计。

### 6.3 Stage C：Capability seed resolution

模型使用 surface index 和少量 overview/interaction chunks，将每个 required capability 映射成候选协议 surface，但不能直接宣告最终 closure。每个映射必须输出：

- capability ID；
- candidate surface；
- actor/direction；
- supporting section/chunk；
- confidence；
- unresolved ambiguity。

MQTT 示例中，capability seed 可从技术文档推导为：

```text
connection_establishment -> CONNECT
qos0_publish_subscribe    -> PUBLISH + SUBSCRIBE
keepalive_exchange        -> PINGREQ
graceful_disconnect       -> DISCONNECT
```

这里不在通用代码里硬编码 MQTT 名称。

### 6.4 Stage D：Deterministic dependency closure

基于已抽取的协议关系反复闭包，直到 fixed point：

```text
request/command -> mandatory response
surface -> required framing and shared fields
surface -> preconditions and actor state
surface -> required error action
surface -> required identifier/code space
surface -> lifecycle cleanup
selected QoS/mode -> corresponding acknowledgement/transaction state
```

MQTT profile 的期望 closure 为：

```text
CONNECT    -> CONNACK
SUBSCRIBE  -> SUBACK
PUBLISH    -> QoS0 encode/decode and routing; no QoS1/2 ack closure
PINGREQ    -> PINGRESP
DISCONNECT -> connection/session cleanup
```

共享依赖至少包括 fixed header、Remaining Length、UTF-8、Topic Name、Topic Filter、SUBSCRIBE Packet Identifier 和 TCP stream incremental buffering。

若 profile constraint 与规范依赖冲突，不得静默删边；必须写入 `normative_conflicts` 或 `unresolved_scope_questions`。

### 6.5 Stage E：Scoped retrieval

用 closure 中的 exact surface names、section anchors、field names 和 normative IDs 代替 generic `must` 查询：

- 每个 selected surface 检索 format、fields、processing、response、errors；
- shared framing 单独检索一次；
- selected state/lifecycle 按依赖检索；
- explicitly excluded feature 只检索足以证明边界和冲突的少量 chunks；
- QoS2、retained、Will 等未被选中内容不得通过通用 state/resource query 回流。

### 6.6 Stage F：Scoped category extraction

保留现有八类语义结构，但每个 prompt 必须收到统一 scope envelope：

```json
{
  "required_capabilities": [],
  "included_surface": [],
  "allowed_shared_dependencies": [],
  "excluded_features": [],
  "selected_chunks": []
}
```

prompt 的硬约束：

- 只抽取 included surface 及其已批准 dependency；
- 不因为文档 chunk 提到相邻 feature 就扩张 scope；
- 新发现 dependency 必须作为 proposed edge 返回，由 closure stage 审核后再抽取；
- field、packet、state、resource 和 code space 必须进入各自 schema 分区；
- 每个 concrete item 只允许引用同一响应返回的 evidence ID 或 canonical shared evidence ID。

### 6.7 Stage G：Reconciliation 与 deterministic verification

LLM reconciliation 只提出 findings，不再负责掩盖或自动补齐结构错误。最终 verifier 决定是否成功。

必须增加的 deterministic checks：

- profile hash 和 semantic projection hash 一致；
- required capability 全部有 resolution；
- request/response、framing、field、state、error、cleanup closure 完整；
- `minimum_v1.must_support_surface` 全部映射主 surface catalog；
- required codec scope 只来自 minimum closure；
- excluded feature 不泄漏到 required facts 或 `planning_inputs.required_codec_scope`；
- packet/field/state/resource kind 不混用；
- 所有 evidence refs 存在且 excerpt 能定位到 selected chunk；
- discovery evidence 被纳入 canonical evidence index；
- flow surface names、state nodes 和 transition endpoints 全部可解析；
- intentional subset 的 normative conflict 被显式记录；
- 无 gold/example/specs leakage。

## 7. 修改边界

### 7.1 预计修改文件

```text
agent/facts/target_profile.py                  # facts-local loader/validator/projection
agent/facts/target_profiles/mqtt_min.json      # facts-owned MQTT target profile
agent/facts/cli.py                             # required --target-profile / compare command
agent/facts/models.py                          # scope/capability/dependency data
agent/facts/preprocess.py                      # exact surface/section scoped retrieval
agent/facts/prompts.py                         # profile-aware discovery/resolution/extraction
agent/facts/extraction.py                      # staged scope closure and output assembly
agent/facts/verifier.py                        # profile/scope/evidence closure gates
agent/facts/comparator.py                      # structural/semantic gold comparison
agent/facts/tests/                             # deterministic fixtures and facts-only tests
agent/facts/README.md                          # CLI/output/comparison contract
agent/facts/FACTS_TARGET_PROFILE_MINIMUM_EXTRACTION_PLAN.md
```

实现时先替换现有 minimum retrieval、evidence assembly 和 output normalization，不保留平行旧 pipeline。`comparator.py` 只用于 facts-owned离线评价，不得被 production extractor 或 prompt builder 导入。

### 7.2 默认只读

```text
document/MQTT_3.1.1.txt
protocol-example/
specs-example/
specs_schema/
agent/common/
agent/planning/
agent/coder/
evaluation/
历史 facts/planning/coder run artifacts
```

`agent/facts/gold_facts/mqtt_min/protocol_facts.json` 是唯一例外：只允许 `agent/facts/comparator.py` 和 facts comparator tests 只读。facts extractor、prompt builder、retriever、verifier 和 production runtime 不得读取 gold facts。

### 7.3 越界门禁

每个实施 step 完成后都必须运行：

```bash
git diff --name-only -- agent/facts
git diff --name-only
```

第二个结果中若出现不属于执行前 dirty-worktree baseline、且路径不在 `agent/facts/` 的新变化，本 tranche 立即停止并回退该越界修改。现有其他模块的用户改动只读保留，不纳入本 tranche diff。

## Step F0：冻结基线、契约与 leakage boundary

**状态：COMPLETED**

### 任务

1. 固定 2026-07-15 fresh run 的 facts、manifest、logs、token usage 和 36 个 verifier errors。
2. 记录当前 MQTT document、facts prompt、model 和代码 hash，并冻结待实现的 facts-owned profile contract requirements。
3. 在 facts tests 中建立 MQTT structural contract 和 semantic rubric，按 category shape、capability、surface、codec、state、routing、errors、limits、exclusions、planning inputs、traceability 评分。
4. 明确 gold facts 只由 facts-owned comparator 读取，增加 gold/example/specs path leakage fixture。
5. 冻结 facts-owned profile 的目标 path、schema 和 capability contract；具体文件 hash 在 F1 创建后记录。

### 完成门禁

```text
baseline artifacts immutable
rubric does not require exact prose or full JSON equality
gold/example/specs unavailable to extraction prompts
facts-only modification boundary recorded
gold structural contract and semantic rubric frozen
no fresh model call
```

## Step F1：Target Profile v2 与 Facts-Local Loader

**状态：COMPLETED**

### 任务

1. 在 `agent/facts/target_profile.py` 实现 facts-local loader，不修改或替换其他模块的 loader。
2. 在 `agent/facts/target_profiles/mqtt_min.json` 新增 MQTT profile，加入 required capabilities、scope/conformance policy 和 feature constraints。
3. facts validate/extract CLI 都要求 `--target-profile`。
4. manifest 记录 profile path、hash、schema version 和 visible projection hash。
5. target profile 不写入 extraction prompt 的 document evidence 区域，而是作为独立 scope envelope。

### Tests 与门禁

- missing/malformed/wrong-version profile 明确失败；
- duplicate capability ID、role/protocol mismatch、constraint contradiction 明确失败；
- facts semantic view 不含纯工程答案；
- profile exclusions 和 assumptions 生成可识别的 `profile_*` evidence；
- production facts 代码不导入 evaluation 或 planning；
- 所有修改仍位于 `agent/facts/`。

## Step F2：Surface Index 与 Capability Resolution

**状态：COMPLETED**

### 任务

1. 将 surface discovery 改成文档级轻量 index，覆盖 type/code table 和 packet/command sections。
2. 增加 capability-to-surface resolution artifact。
3. 以 section anchor 和 exact names 进行二次 retrieval。
4. 对 unresolved capability 保留候选和问题，不用最高分 chunk 猜答案。

### Tests 与门禁

- MQTT fixture 能从 technical document index 发现 14 类 MQTT Control Packet，而不是只发现当前 9 类；
- minimum broker capability seeds 可映射到 CONNECT、PUBLISH、SUBSCRIBE、PINGREQ、DISCONNECT；
- generic fixture 覆盖 request/response、command/reply 和 resource/method 风格协议；
- 通用实现中不存在 MQTT packet 常量表。

## Step F3：Dependency Closure 与 Scoped Retrieval

**状态：COMPLETED**

### 任务

1. 建立 typed dependency edges 和 fixed-point closure。
2. 从 technical-document facts 推导 mandatory paired surface、fields、state、errors 和 cleanup。
3. feature constraints 控制 QoS/mode-specific dependency branch。
4. excluded branch 只保留 boundary evidence，不进入详细 category extraction。
5. 记录 normative conflict 和 unresolved scope question。

### Tests 与门禁

- MQTT closure 包含 CONNACK、SUBACK、PINGRESP；
- QoS0 profile 不引入 PUBACK/PUBREC/PUBREL/PUBCOMP transaction state；
- PINGREQ 和 DISCONNECT 不再因 generic rerank 遗漏；
- 删除任一 mandatory response edge 时 verifier 必须失败；
- profile 排除 mandatory feature 时不能标记 full conformance。

## Step F4：Scoped Category Extraction 与 Evidence Closure

**状态：COMPLETED**

### 任务

1. 所有 category prompt 注入相同 scope envelope。
2. 替换 `minimum_boundary` 的 generic `MUST` retrieval。
3. category merge 前执行 kind、scope 和 evidence normalization。
4. discovery evidence 进入 canonical `evidence_index`，或将 copied discovery refs 重写为 category-owned evidence refs。
5. `planning_inputs` 仅从 scoped facts 和 minimum closure 派生。

### Tests 与门禁

- unknown evidence ref 数量为 0；
- field 不进入 packet-only surface；
- excluded-feature leakage 数量为 0；
- main facts、minimum view 和 planning inputs 使用同一 included surface；
- 任一抽取 task 失败时输出明确 incomplete，不生成表面成功的 facts。

## Step F5：Verifier、Gold-Compatible Normalization 与 Scope Log

**状态：COMPLETED**

### 任务

1. 写出 `_agent_logs/scope_resolution.json` 并纳入 manifest hash，但不改变 public facts contract。
2. 扩展 verifier 的 profile coverage、dependency closure、evidence closure 和 leakage gates。
3. 在 output assembly 后执行 gold-compatible deterministic normalization，统一 key names、item shape、container types 和 ordering policy。
4. verifier 不读取 gold；它只验证固定的 `protocol_facts/v2alpha1` contract 和 scope/evidence closure。

### MQTT acceptance rubric

| 维度 | 必须满足 |
| --- | --- |
| role / transport | broker/server role，TCP stream |
| minimum surface | CONNECT、CONNACK、SUBSCRIBE、SUBACK、PUBLISH、PINGREQ、PINGRESP、DISCONNECT |
| codec | fixed header、Remaining Length、必要 UTF-8/packet identifier、QoS0 PUBLISH |
| state | connection/session establishment、subscription lifecycle、incremental stream buffering |
| routing | Topic Name / Topic Filter matching 和 publish fanout |
| errors | pre-CONNECT、malformed selected packets、unsupported selected-mode behavior |
| limits | selected fields/framing 的固定边界 |
| exclusions | QoS1/2 state、Will、retained、persistence、auth/TLS、UNSUBSCRIBE 不进入 required scope |
| traceability | 100% required fact refs 可解析到 MQTT technical document chunks |

rubric 中源自 example implementation、而 technical document 和 profile 都无法支持的实现细节，不进入 verifier required set，留到 F6 comparison report 单独分类。

### 完成门禁

```text
facts verifier errors = 0
scope closure status = passed
evidence closure = 100%
required capability resolution = 100% or explicit blocking unresolved
public top-level key set unchanged
all required item shapes normalized
```

## Step F6：Facts-Owned Gold Structural/Semantic Comparator

**状态：COMPLETED**

### 任务

1. 在 `agent/facts/comparator.py` 实现候选 facts 与 frozen MQTT gold facts 的离线比较。
2. structural comparison 递归比较 required key paths、container types 和 collection item shapes，忽略具体文本、evidence ID、list ordering 和允许变化的 list length。
3. semantic comparison 使用 frozen rubric，比较目标 surface、paired messages、codec、state、routing、errors、limits、deferred features、assumptions 和 `planning_inputs`。
4. 对每个 missing/extra item 做 normalized name 和 semantic summary matching，保留匹配 confidence 和证据，不把简单 keyword overlap 当成通过。
5. 将差异分成 `critical_scope_mismatch`、`structural_mismatch`、`semantic_gap`、`acceptable_variation`、`gold_only_implementation_detail` 和 `candidate_only_supported_fact`。
6. 提供 facts CLI `compare --candidate ... --gold ... --out ...`，但 production extract 不自动读取 gold。
7. 输出机器可读 JSON 和人类可读 Markdown 报告。

### Tests 与门禁

- candidate/gold 顶层和 nested structural mismatch 可精确定位到 JSON path；
- `reason` vs `summary`、object vs list、missing nested key 等结构差异必须失败；
- prose 改写、evidence ID 不同和同义名称可被归为 acceptable variation；
- gold-only example implementation detail 不降低 technical-document factual score；
- comparator 从未被 extractor、prompts 或 verifier 导入；
- comparator 和 tests 以外的代码不得读取 gold facts。

## Step F7：Offline Regression 与 Frozen 3-run Pilot

**状态：COMPLETED**

### Phase A：0-model-call gates

必须先通过：

- target-profile loader tests；
- facts preprocess/extraction/verifier fixtures；
- facts comparator structural/semantic fixtures；
- facts package tests；
- `compileall`；
- `git diff --check`；
- facts-only path boundary check；
- gold/example/specs leakage scan。

### Phase B：一次 smoke extraction

允许一次 MQTT smoke run，用于发现接口或预算问题。smoke 后允许修复，但任何修改都会使之前 smoke 失效；修复完成后重新执行全部 Phase A gates。

### Phase C：冻结 3-run sequence

代码、profile、document、prompt、model、budget 和 evaluator 全部冻结后，恰好执行 3 次 independent fresh facts extraction。三次使用独立 output dir，不 resume、不复用 LLM response、不人工修 facts。

每次记录：

- verifier and closure status；
- semantic rubric；
- structural comparison；
- included/excluded surface；
- unknown evidence refs；
- open/unresolved scope questions；
- token usage 和 wall time；
- output hashes。

### 稳定性门禁

```text
facts verifier pass = 3/3
scope closure pass = 3/3
gold structural comparator pass = 3/3
critical MQTT rubric pass = 3/3
unknown evidence refs = 0/3
excluded-feature leakage = 0/3
gold/example/specs leakage = 0/3
```

token 不是 correctness gate。correctness 达标后，以 290347-token baseline 为参照，目标是 frozen sequence median 不高于 baseline 的 70%（203243 tokens），任何单次不高于 baseline；未达到成本目标时单独记录，不得用减少 required evidence 的方式换取低 token。

## Step F8：归档、决策与停止

**状态：COMPLETED**

### 任务

1. 写 freeze record、三次 run manifest hash、aggregate summary 和失败分类。
2. 对比 baseline、gold oracle 和三次 scoped extraction。
3. 分别报告 factual correctness、scope utility、traceability、stability 和 cost。
4. 判断候选 facts 是否与 gold facts 结构兼容、语义相当，并给出 remaining gaps；不运行或修改 planning/coder。

### Go 条件

- F7 所有 correctness/stability 门禁通过；
- 没有 protocol-specific production hardcoding；
- gold structural comparator 3/3 通过；
- critical semantic obligations 3/3 通过；
- candidate 与 gold 的差异均可归为 acceptable variation、gold-only implementation detail 或有文档支持的 candidate-only fact。

### No-Go 条件

- 任一 frozen run verifier 或 scope closure 失败；
- required packet/response 在多次运行中漂移；
- QoS1/2、retained、Will 等 excluded branch 重复泄漏；
- evidence closure 仍依赖 fallback excerpt 或 unknown refs；
- `protocol_facts.json` key/type/item shape 与 gold contract 不一致；
- profile directive 被当成 technical-document protocol fact；
- 需要 MQTT-specific packet inventory 才能通过。

无论 Go 或 No-Go，完成 F8 后本 tranche 停止。不得为追求正结果追加 replacement run 或在三次之间修改代码。

## 8. 风险与缓解

| 风险 | 影响 | 缓解 |
| --- | --- | --- |
| profile 太抽象 | capability 无法稳定映射 surface | 要求 observable behavior、显式 constraints；ambiguity 进入 blocking scope question |
| profile 太具体 | 变成隐藏 gold packet list | 禁止 packet closure、事实条目和模块/函数/文件答案；profile review 和 leakage scan |
| intentional subset 与规范 MUST 冲突 | 输出错误声称 conformant | `conformance_mode` + `normative_conflicts` 强制门禁 |
| surface index 不完整 | closure 起点缺失 | table/section coverage check，不依赖单 prompt |
| dependency graph 由 LLM 幻觉 | 错误扩张或删减 scope | 所有 edge 要 evidence，deterministic fixed-point 和 verifier |
| category prompt scope 漂移 | excluded feature 回流 | scope envelope、allowed dependency list、leakage validator |
| gold 结构约束被误用为内容模板 | 抽取结果看似相似但发生 leakage | comparator-only read boundary、prompt/workdir scan、hash audit |
| profile scope statement 混入规范事实 | factual correctness 虚高 | `doc_*`/`profile_*` evidence 分离与 verifier source rule |
| 修改越出 facts 模块 | 污染 planning 当前实验 | 每 step 执行 dirty-baseline-aware path boundary check |
| 只在 MQTT 有效 | 泛化不足 | protocol-generic fixtures，后续再用 CoAP/HTTP/SMTP profile 验证 |
| token 成本仍高 | 难扩展到长文档 | correctness 后再优化 staged index、exact retrieval 和 excluded-branch pruning |

## 9. 最终交付物

完成本计划应交付：

```text
versioned target profile contract
facts-local target-profile loader
profile-aware facts CLI
surface index and capability-resolution artifacts
deterministic dependency closure
gold-compatible scoped protocol_facts.json
internal scope resolution log
enhanced facts verifier
facts-owned gold structural/semantic comparator
gold comparison JSON/Markdown reports
frozen 3-run facts extraction artifacts
freeze_record.json
sequence_summary.json
final decision report
```

最终成功标准不是“facts 输出看起来像一份完整协议综述”，而是：从 technical documents 中抽出的事实集合与 facts-owned target profile 严格对齐，证据和依赖闭合，`protocol_facts.json` 与 MQTT minimum gold facts 结构兼容、语义相当，并能通过 facts-only comparison 清楚解释所有剩余差异。整个实现、测试和评价过程不得修改或运行 planning、coder、evaluation 等其他模块。
