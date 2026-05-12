# Planning Agent

这个目录实现了 `planning agent`，它位于多 agent 流水线的中间层：

1. `facts`: 协议文档 -> `protocol_facts.json`
2. `planning`: `protocol_facts.json + target_profile.json` -> 工程实现计划 + coder-compatible `SpecBundle` + 派生设计决策记录
3. `coder`: `SpecBundle` -> 代码工程

它不是简单的格式转换器，而是一个 **Evidence-Grounded Protocol Implementation Planner**：

- 以上游 `facts` 的协议语义为证据基础
- 由 LLM 主导生成候选架构、工程实现计划和文件布局，规则层负责通用能力抽取、约束校验、依赖/文件图归一化和稳定 spec 编译
- 通过 `SpecBlueprint` 展开层把已验证的工程计划编译成当前 `coder` 可以直接消费的 spec

## 两阶段架构

Planning agent 现在显式分为两个阶段：

1. **工程规划阶段**：`protocol_facts.json -> implementation_plan_v2.json`
   - 消费 facts agent 输出的协议事实、状态、资源、错误和 minimum scope。
  - 产出模块边界、状态/资源所有权、handler matrix、依赖图、文件布局、错误策略、测试义务和 unresolved questions。
   - 这一阶段体现 SpecForge 的核心研究贡献：facts agent/planning layer 把 protocol facts 转换为可实施的工程结构，而不是简单摘要。

2. **Spec 展开阶段**：`implementation_plan_v2.json -> spec_blueprint.json -> spec_bundle/`
   - `SpecBlueprint` 是工程决策到 coder specs 的稳定中间 IR。
   - Blueprint 表达 modules、files、types、functions、helpers、wire mappings、access paths、call contracts、dependency-derived rely context 和 test vectors。
   - 每个 blueprint item 必须保留 `evidence_refs`、`decision_refs` 或 `profile_refs`，保证 traceability。

`design_decisions.json` 不再是独立规划阶段，也不是 `implementation_plan_v2` 或 `SpecBlueprint` 的输入；它从已固化的 implementation plan、架构评审和规则激活中派生出来，用作审计记录和 traceability 摘要。

当前 spec 展开不读取外部示例 specs，也不包含任何特定协议或特定版本的模板分支。`SpecBlueprint` 只由 `protocol_facts.json`、`target_profile.json` 和 `implementation_plan_v2.json` 推导出来。LLM 是工程规划阶段的必需主导者；规则路径只作为约束、校验和编译层使用。

## 当前实现状态

当前 V1 已经实现了可运行闭环：

- 输入：
  - `protocol_facts.json`
  - `target_profile.json`
- 输出：
  - `_agent_logs/`
    - 记录 LLM prompt、LLM response、归一化中间调试信息和 `000_stage_events.log`。
  - `_step_logs/`
    - 记录第 3 步到第 12 步的步骤结束产物，并附带 run manifest、token usage summary 和 verification report。
  - `spec_bundle/`
    - 最终给 coder agent 消费的协议 specs。
  - `_step_logs/007_implementation_plan.json`
  - `_step_logs/007_implementation_plan_v2.json`
    - 其中包含 `dependency_graph`，作为逻辑依赖的权威来源。
    - 其中包含 `file_layout`，作为文件拓扑和 include 依赖投影的权威来源。
  - `_step_logs/010_spec_blueprint.json`
  - `_step_logs/010_expansion_candidates.json`
  - `_step_logs/012_design_decisions.json`
    - 从已接受的 implementation plan 派生，作为审计记录，不参与主流程决策。
  - `_step_logs/013_run_manifest.json`
  - `_step_logs/014_planning_verification_report.json`

并且可以直接用现有 `coder.specs.load_spec_bundle()` 做兼容性验证。

## 输入契约

### 1. Facts 输入

当前直接消费 `facts agent` 输出的 `protocol_facts/v2alpha1`：

- `transport`
- `interaction_model`
- `message_model`
- `state_model`
- `routing_model`
- `resource_model`
- `error_and_limits`
- `minimum_v1`
- `open_questions`
- `evidence_index`

### 2. Target Profile 输入

`target_profile.json` 当前要求至少包含：

```json
{
  "target_role": "broker",
  "language": "C",
  "runtime": "Linux epoll",
  "scope": "minimum_v1",
  "deployment_constraints": {
    "memory_limit": "low",
    "persistence": false,
    "tls_mode": false
  }
}
```

当前 V1 的 `spec compiler` 只输出当前 `coder` 兼容的 C 风格 spec，因此：

- 内部 planning 逻辑仍然是通用的
- 但最终编译阶段当前只支持 `language = C`

## 主流程总览

`PlanningAgent.plan()` 是当前主链路入口。一次完整运行会创建一个独立输出目录，并把所有中间产物、最终 specs、验证报告和 agent 日志放在同一个 run 目录中：

```text
agent/planning/out/<protocol>/<target_profile>/<timestamp>/
├── _agent_logs/
│   ├── 000_stage_events.log
│   └── ...
├── _step_logs/
│   ├── 003_planning_ir.json
│   ├── 004_protocol_profile.json
│   ├── 005_expert_activations.json
│   ├── 006_candidate_architectures.json
│   ├── 006_architecture_review.json
│   ├── 007_implementation_plan.json
│   ├── 007_implementation_plan_v2.json
│   ├── 008_dependency_graph.json
│   ├── 009_file_layout.json
│   ├── 010_spec_blueprint.json
│   ├── 010_expansion_candidates.json
│   ├── 012_design_decisions.json
│   ├── 013_run_manifest.json
│   ├── 013_token_usage_summary.json
│   └── 014_planning_verification_report.json
└── spec_bundle/
```

从研究语义上看，当前流程分成两大阶段：

- **工程规划阶段**：facts 被归一化、分析、匹配专家规则，形成候选架构和 `implementation_plan_v2.json`。
- **Spec 展开阶段**：工程计划被展开为 `SpecBlueprint`，再被无 LLM 的 compiler 编译为 coder-compatible `spec_bundle/`。

从代码执行顺序看，当前共有 14 个主流程步骤，另有一个派生 artifact 写出步骤。每个步骤都会在 `_agent_logs/000_stage_events.log` 里记录 `stage=...` 事件，便于复现实验和定位失败点。

## 详细流程

### 0. 输出目录和日志初始化

入口：`planner.py::PlanningAgent.__init__()` 和 `planner.py::PlanningAgent.plan()`。

输入是 `facts_path`、`target_profile_path` 和可选 `output_dir`。如果没有显式传入 `output_dir`，planning 会读取 facts 中的 `protocol_meta.protocol_name`，再结合 target profile 的 slug，生成：

```text
agent/planning/out/<protocol>/<target_profile>/<timestamp>/
```

随后初始化 `_agent_logs/` 和 `_step_logs/`。其中 `_agent_logs/000_stage_events.log` 记录阶段事件，其他编号文件保存 LLM prompt、LLM response 和归一化调试信息；`_step_logs/` 保存步骤结束后的结构化产物。

这一层不做协议推理，只负责保证一次运行的 artifact 边界清晰，避免不同实验输出相互覆盖。

### 1. 输入验证

入口：`planner.py::validate_inputs()`。

主要检查：

- `protocol_facts.json` 路径是否存在。
- `target_profile.json` 路径是否存在。
- target profile 是否能被 `ir.py::load_target_profile()` 解析。
- 当前 spec compiler 是否支持目标语言。目前只支持 `language = C`。
- facts 是否能被 `ir.py::build_planning_ir()` 初步归一化。
- 如果配置了 LLM client，并且没有跳过检查，则执行 LLM self-check。

输出是 `PlanningDiagnostic` 列表。只要出现 `level = error`，`plan()` 会直接中止，不继续生成下游 artifact。LLM self-check 失败目前是 warning，不阻断规则路径运行。

### 2. Target Profile 加载

入口：`ir.py::load_target_profile()`。

target profile 描述本轮 planning 的实现目标，而不是协议事实本身。典型字段包括：

- `target_role`: 例如 `broker`
- `language`: 例如 `C`
- `runtime`: 例如 `Linux epoll`
- `scope`: 例如 `minimum_v1`
- `deployment_constraints`: 例如是否启用持久化、TLS、低内存约束

输出对象会保留原始 `raw` 数据，同时生成稳定 `slug`，用于默认输出路径。

### 3. Planning IR 构建

入口：`ir.py::build_planning_ir()`。

这是 facts 到 planning 语义的第一层转换。它不决定具体文件和函数，而是把上游 protocol facts 归一化为后续阶段更容易消费的结构。

主要处理：

- 读取 `protocol_meta`，确定协议名。
- 归一化 target role 和 protocol role 的关系。
- 从 `minimum_v1.must_support_surface` 提取最小闭环 surface。
- 汇总 message/state/resource/error/routing/transport 相关 facts。
- 建立 `evidence_by_id` 和 traceability index。
- 整理 `open_questions`，区分 blocking、assumable、deferrable。

输出 artifact：

```text
_step_logs/003_planning_ir.json
```

这个 artifact 是后续所有分析、规则激活、架构生成和 traceability 的共同基础。

### 4. Protocol Profile 分析

入口：`analyzer.py::analyze_protocol_profile()`。

这一步把细粒度 facts/IR 压缩成工程规划需要的协议画像。它回答的是“这个协议实现大体像什么系统”，而不是“每个函数怎么写”。

当前 profile 包括：

- `transport_shape`: stream/datagram 等传输形态。
- `interaction_model`: client-server、pub-sub、request-response 等交互模型。
- `statefulness`: 是否强状态，以及状态复杂度。
- `routing_intensity`: 是否需要 topic/router/dispatch 等路由结构。
- `resource_intensity`: session、subscription、connection 等资源压力。
- `failure_semantics`: 错误后关闭连接、返回错误包、忽略等策略倾向。
- `timing_model`: 是否依赖 timer、keepalive、retransmission。
- `minimum_scope`: 本轮最小闭环范围。

输出 artifact：

```text
_step_logs/004_protocol_profile.json
```

> **批注**：当前所有库都被激活，后续补充多个协议各自对应的激活内容
### 5. 专家规则激活

入口：`knowledge_base.py::load_rules()` 和 `rule_engine.py::activate_rules()`。

这一步根据 protocol profile 激活显式专家知识。规则不是最终 specs，而是工程规划约束和建议。

典型规则包括：

- stream transport 需要 input buffer 和 incremental decoder。
- pub-sub/routing 强度高时需要 router/resource store。
- session/resource 明显时需要明确 owner 和生命周期。
- close-on-error 语义需要统一错误策略。
- public type 需要 canonical owner，避免 coder 生成重复类型。

输出 artifact：

```text
_step_logs/005_expert_activations.json
```

这些 activation 会进入架构生成、架构评审和 implementation plan。`design_decisions.json` 只在主流程完成后引用这些 activation 生成审计记录。

### 6. 候选架构生成与评审

入口：`architecture.py::generate_candidate_architectures()`、`scorer.py::lint_candidate_architectures()` 和 `scorer.py::judge_candidate_architectures()`。

候选架构采用 “Generator LLM + deterministic lint + Judge LLM” 的方式：

- 必须配置可用 LLM；无 LLM 时 planning 中止并返回 `llm_required_missing`。
- Generator LLM 分三次独立请求生成候选，每次只生成一个候选架构。
- 规则激活表达 required capabilities，而不是要求生成同名模块。
- deterministic lint 检查 schema、模块数量失控、capability 覆盖和 capability token；模块名 grounding 只作为非阻断 warning/telemetry。
- 模块数量硬约束为 2-12；5-7 只是常规 preferred range，由 Judge LLM 判断数量是否合理。
- Judge LLM 根据协议适配度、role composition 归属、能力所有权语义、模块内聚性、边界划分、coder 可用性和 traceability 选择候选。
- 如果 Judge LLM 拒绝全部候选，planning 中止并返回 `architecture_judge_rejected_all`。

输出 artifact：

```text
_step_logs/006_candidate_architectures.json
_step_logs/006_architecture_review.json
```

`006_candidate_architectures.json` 保存 `candidates` 和 `lint_results`；`006_architecture_review.json` 保存 Judge 的结构化评分、问题列表和最终 selected candidate。`plan()` 只接受 Judge 明确 `accept` 且通过硬 lint 的候选。

### 7. 工程实现计划生成

入口：`planner.py::_build_implementation_plan()`。

这一步生成 `implementation_plan/v2alpha3`。它是第一阶段的正式输出，也是第二阶段 SpecBlueprint 展开的输入。

当前 implementation plan 包括：

- `target_profile`: 本轮实现目标。
- `protocol_name`: 协议名。
- `protocol_description`: interaction、transport、statefulness 摘要。
- `scope_decisions`: minimum surface、deferred features、assumptions。
- `module_graph`: 模块、职责、依赖和实现路径。
- `dependency_graph`: module/function/data 逻辑依赖的权威结构化来源。
- `file_layout`: module 内部文件展开、function placement 和 file-level include edge 的权威结构化来源。
- `canonical_types`: public type 的唯一 owner。
- `state_design`: 状态节点、状态迁移和不变量。
- `handler_matrix`: minimum surface 到 handler module/function 的映射。
- `resource_lifecycle`: 资源对象和生命周期规则。
- `error_strategy`: 错误策略和 error matrix。
- `test_plan`: unit/integration obligations。
- `traceability`: 派生 decision refs 和 evidence 规模。
- `unresolved_questions`: 从 facts 继承的开放问题。

这一层刻意不再直接拼 `FUNCTION_SPEC`。它只描述实现级语义和工程边界，例如“需要哪些模块、谁拥有状态、哪些 handler 必须覆盖、模块如何依赖、错误如何处理”。

输出 artifact：

```text
_step_logs/007_implementation_plan.json
_step_logs/007_implementation_plan_v2.json
```

当前两个文件内容相同；`007_implementation_plan_v2.json` 是新两阶段架构的正式名称，`007_implementation_plan.json` 保留相同内容用于运行内索引和兼容性语义。

### 8. 依赖图生成

入口：`dependency_graph.py::build_dependency_graph()`。

这一步嵌入 `implementation_plan_v2` 固化过程。它以 selected architecture、module graph、handler matrix、canonical types 和 `implementation_plan.traceability.decision_ids` 为输入，生成 `implementation_plan_v2.dependency_graph`：

- `module_edges`: 模块之间的 consumer/provider 关系。
- `interface_contracts`: provider 暴露给 consumer 的 public symbols、public type 和能力契约。
- `function_edges`: lifecycle 或 handler 函数对其他 public functions 的依赖。
- `data_edges`: 函数对 provider public handle/type 的依赖。

生成策略是 LLM 提议加 deterministic normalization。LLM 输出只作为候选边；规则层会补齐 capability/handler 推导出的必要边，过滤未知模块、自依赖和成环边，并把最终边回填到 `module_graph[*].dependencies` 和 `module_graph[*].artifacts`。候选架构中的 `dependencies` 只作为 hint，不再是权威来源。

输出 artifact：

```text
_step_logs/008_dependency_graph.json
```

### 9. 文件布局生成

入口：`file_layout.py::build_file_layout()`。

这一步嵌入 `implementation_plan_v2` 固化过程，发生在 `dependency_graph` 之后、`SpecBlueprint` 之前。它以 selected architecture、module graph、handler matrix、canonical types 和 `dependency_graph` 为输入，生成 `implementation_plan_v2.file_layout`：

- `files`: 每个模块内部的 source/header owner 文件节点；简单模块可以保持单文件，复杂模块会按 handler、decode/encode、state/store、routing/runtime/error 等职责拆分。
- `file_edges`: 从 dependency graph 投影出的 file-level include/use 关系，包含 `include_scope`、required symbols 和 source graph edge refs。
- `function_placement`: 每个 generated lifecycle/handler function 唯一落到一个 source unit。
- `unresolved_layout_questions`: 文件布局层无法可靠决策的问题。

生成策略是 LLM 提议加 deterministic normalization。LLM 只能拆分模块内部文件，不能改变模块边界、能力归属、handler 名称或 dependency graph 语义。`file_edges` 不再由 LLM 枚举，而是在 normalization 成功后由系统根据 `files`、`function_placement` 和 `dependency_graph` 确定性派生。若 LLM 在重试预算内仍无法给出有效 `files` 和 `function_placement`，planning 会以 `file_layout_invalid_llm_output` 失败，不走 fallback。

`file_layout` 会回填 `module_graph[*].files` 和 `module_graph[*].artifacts`。从这一阶段开始，一个模块可以对应多个 `FILE_SPEC`，但仍允许简单模块只有一个 source/header pair。

输出 artifact：

```text
_step_logs/009_file_layout.json
```

### 10. SpecBlueprint 展开

入口：`spec_blueprint.py::build_spec_blueprint()`。

这是第二阶段的第一步。它把 implementation plan 展开成稳定中间 IR：`SpecBlueprint`。

Blueprint 表达的是 coder specs 的结构蓝图，但还不是最终 spec 文件。它包含：

- modules
- files
- types
- functions
- helper contracts
- wire mappings
- access paths
- call contracts
- test vectors
- generation order
- traceability metadata

当前展开流程是统一的、事实驱动的：

1. 从 `implementation_plan_v2.module_graph` 生成 blueprint modules。
2. 从 `implementation_plan_v2.file_layout` 生成 blueprint files 和 function placement。
3. 从 `implementation_plan_v2.canonical_types` 生成每个模块的 public opaque type。
4. 从 `implementation_plan_v2.dependency_graph` 和 `file_layout.file_edges` 生成 source/header dependencies、function `RELY`、call contracts 和 dependency projections。
5. 从模块边界生成 lifecycle helper functions，例如 create/destroy。
6. 从 `implementation_plan_v2.handler_matrix` 生成 surface handler functions。
7. 将 generated functions 回填到对应 source file，并把 public declarations 回填到模块 public header owner。
8. 将 `implementation_plan.traceability.decision_ids` 和 facts evidence refs 写入 blueprint traceability。

当前内置 profile：

- `generic_c_from_plan`：通用 C profile。它只依赖当前 run 的工程计划，不读取任何协议专用模板。

每个 blueprint item 必须至少包含一种来源：

- `evidence_refs`
- `decision_refs`
- `profile_refs`

输出 artifact：

```text
_step_logs/010_spec_blueprint.json
_step_logs/010_expansion_candidates.json
```

`010_expansion_candidates.json` 记录本次 blueprint 的生成来源、是否被选中和 coverage。当前 LLM candidate blueprint 尚未成为主路径，verifier 仍是最终接受门。

### 11. Spec Bundle 编译

入口：`spec_compiler.py::compile_spec_bundle()`。

这一步纯规则、无 LLM。它把 `_step_logs/010_spec_blueprint.json` 编译成当前 coder loader 可直接读取的 spec bundle。

输出 spec 类型包括：

- `PROTOCOL_MODULE_SPEC`
- `FILE_SPEC`
- `FUNCTION_SPEC`

编译策略：

- 从 blueprint modules 编译 `PROTOCOL_MODULE_SPEC`。
- 从 blueprint files 编译 `FILE_SPEC`。
- 从 blueprint functions 编译 `FUNCTION_SPEC`。
- 根据 trace id 推导 spec 文件目录层级。
- 每次编译前会清空当前 run 下的 `spec_bundle/`，避免旧 specs 残留污染本次结果。

输出目录：

```text
spec_bundle/
```

典型层级由 `implementation_plan_v2.module_graph[*].path` 和 trace id 推导，例如：

```text
spec_bundle/
├── <protocol>_module_spec.json
├── <module_a>/
├── <module_b>/
└── <module_c>/
```

### 12. 派生设计决策记录

入口：`decision_graph.py::derive_design_decisions()`。

这一步不参与主流程决策，也不作为 `implementation_plan_v2`、`dependency_graph` 或 `SpecBlueprint` 的输入。它在工程计划和 spec bundle 已生成后，从 implementation plan、selected architecture、architecture review、profile 和 rule activations 派生 `_step_logs/012_design_decisions.json`，用于审计、论文分析和人工检查。

派生记录覆盖：

- target scope
- runtime model
- architecture selection
- state ownership
- error policy
- handler coverage
- routing strategy（如果 facts 中存在 routing dispatch keys）
- dependency graph

输出 artifact：

```text
_step_logs/012_design_decisions.json
```

### 13. Run Manifest 和 Token Usage 写入

入口：`planner.py::PlanningAgent.plan()` 中的 manifest 生成逻辑。

`_step_logs/013_run_manifest.json` 是一次 planning run 的索引文件，记录：

- 使用的模型名。
- facts path。
- target profile path。
- output dir。
- 所有 artifact 路径。
- `step_artifacts`: 按本 README 第 0-14 步组织的步骤结束产物索引；第 8 步和第 9 步的 `dependency_graph`/`file_layout` 虽然嵌入在 `implementation_plan_v2` 中，也会在 `_step_logs` 中写出对应的 `008_dependency_graph.json` 和 `009_file_layout.json` 产物。
- selected architecture。
- design decision 数量。
- rule activation 数量。
- 每次 LLM 调用的 token usage。
- 按 stage 聚合的 token usage。
- workflow 总 token usage。

输出 artifact：

```text
_step_logs/013_run_manifest.json
_step_logs/013_token_usage_summary.json
```

注意：`_step_logs/014_planning_verification_report.json` 在下一步才生成，所以 verifier 完成后 `plan()` 会再次更新 `_step_logs/013_run_manifest.json`，把 verification report 路径补进去。

### 14. 验证与 Verification Report

入口：`verifier.py::verify_output_dir()` 和 `planner.py::_build_verification_report()`。

当前 verifier 校验：

- 必要 artifact 是否存在。
- `implementation_plan` 中 canonical type 是否只有一个 owner。
- module graph 是否无环。
- `dependency_graph` 是否非空（多模块时）、无环且只引用已知模块/函数/类型。
- `file_layout` 是否覆盖所有模块、文件、function placement 和 file edges。
- `module_graph[*].dependencies`、blueprint source/header dependencies、function `RELY` 和 dependency projections 是否与 `dependency_graph`/`file_layout` 一致。
- `minimum_v1` surface 是否全部落到 `handler_matrix`。
- `spec_blueprint.kind` 是否为 `SPEC_BLUEPRINT`。
- blueprint modules/files/functions 是否具有 traceability。
- `spec_bundle/` 是否能被 `coder.specs.load_spec_bundle()` 成功加载。

Planning 还会生成聚合报告：

```text
_step_logs/014_planning_verification_report.json
```

报告内容包括：

- implementation plan schema。
- expansion profile。
- minimum surface、handler matrix、blueprint item 数量。
- generated spec count。
- diagnostics。
- acceptance 状态。

当前 `planning_verification_report.json` 中的 `end_to_end_coder_compile_smoke` 仍记录为 `not_run_by_planning_verifier`。也就是说，planning verifier 只保证 specs 能被 coder loader 接受；真正的 coder 生成、编译和 MQTT smoke test 仍应由外部端到端实验脚本触发。

## 命令行

在 `~/SpecForge` 下运行：

```bash
python3 -m agent planning validate \
  --facts ~/SpecForge/agent/facts/out/mqtt/protocol_facts.json \
  --target-profile agent/planning/planning_target_profile_mqtt.json \
  --skip-llm-check

# 使用facts生成的协议事实
python3 -m agent planning plan \
  --facts ~/SpecForge/agent/facts/out/mqtt/protocol_facts.json \
  --target-profile agent/planning/planning_target_profile_mqtt.json

# 使用标准协议事实
python3 -m agent planning plan \
  --facts ~/SpecForge/agent/facts/gold_facts/mqtt/protocol_facts.json \
  --target-profile agent/planning/planning_target_profile_mqtt.json

python3 -m agent planning verify \
  --output-dir ~/SpecForge/agent/planning/out/<protocol>/<target_profile>/<timestamp>
```

## LLM 必需行为

当前实现不再支持完整离线降级：

- `validate --skip-llm-check` 仍可用于本地输入结构检查。
- `plan` 必须有可用 LLM client；没有设置 `ALI_API` 时会中止并报告 `llm_required_missing`。
- 规则/启发式代码只用于通用能力归纳、诊断、测试和 spec 编译，不再生成可继续执行的 planning fallback。

这保持了 SpecForge 的研究定位：facts agent/planning layer 不是规则模板展开器，而是由 LLM 主导的协议事实分析和工程规划 agent。

## 目录结构

```text
planning/
├── README.md
├── __init__.py
├── __main__.py
├── cli.py
├── models.py
├── ir.py
├── analyzer.py
├── knowledge_base.py
├── rule_engine.py
├── architecture.py
├── scorer.py
├── decision_graph.py
├── planner.py
├── spec_blueprint.py
├── spec_compiler.py
├── verifier.py
└── prompts.py
```

## 当前限制

- `spec_compiler` 当前只输出 `coder` 兼容的 C 风格 spec
- 架构生成、决策补充、implementation plan 精修目前都支持 LLM 补充，但在无模型时主要依赖启发式
- 目前尚未引入正式 JSON Schema，只是采用代码内结构约束和 verifier 校验
- 当前 generic spec 展开粒度来自 `implementation_plan_v2`，不会借助协议专用模板；要提高函数粒度，应优化工程计划和通用展开规则
- 当前 verifier 记录 e2e coder compile/smoke 为 `not_run_by_planning_verifier`；端到端运行仍应由外部实验脚本触发

## 已验证状态

当前已经完成的本地验证：

- `python3 -m compileall ~/SpecForge/agent/planning`
- `python3 -m agent planning validate ...`
- 使用 MQTT facts + C/broker target profile 直接运行 `PlanningAgent.plan()`
- `python3 -m agent planning verify --output-dir /tmp/planning_mqtt_out`

也就是说，planning 主链路当前已经能够：

`facts -> planning artifacts -> coder-compatible spec -> verifier pass`
