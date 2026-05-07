# Planning Agent

这个目录实现了 `planning agent`，它位于多 agent 流水线的中间层：

1. `facts`: 协议文档 -> `protocol_facts.json`
2. `planning`: `protocol_facts.json + target_profile.json` -> 规划中间产物 + coder-compatible `SpecBundle`
3. `coder`: `SpecBundle` -> 代码工程

它不是简单的格式转换器，而是一个 **Evidence-Grounded Protocol Implementation Planner**：

- 以上游 `facts` 的协议语义为证据基础
- 通过规则系统和 LLM 补充推理生成工程决策
- 最终编译成当前 `coder` 可以直接消费的 spec

## 当前实现状态

当前 V1 已经实现了可运行闭环：

- 输入：
  - `protocol_facts.json`
  - `target_profile.json`
- 输出：
  - `planning_ir.json`
  - `protocol_profile.json`
  - `expert_activations.json`
  - `candidate_architectures.json`
  - `design_decisions.json`
  - `implementation_plan.json`
  - `spec_bundle/`
  - `run_manifest.json`

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

## 八阶段流程

### 1. `ir.py`

将 `protocol_facts.json` 归一化为 `planning_ir`：

- 统一角色语义
- 提取最小实现所需的 surface/state/resource/error 视图
- 建立 `traceability_index`
- 将 `open_questions` 划分为：
  - `blocking`
  - `assumable`
  - `deferrable`

### 2. `analyzer.py`

构建 `protocol_profile`：

- `transport_shape`
- `interaction_model`
- `statefulness`
- `routing_intensity`
- `resource_intensity`
- `failure_semantics`
- `timing_model`
- `minimum_scope`

### 3. `knowledge_base.py` + `rule_engine.py`

通过显式规则库激活专家知识：

- stream transport -> `input_buffer` + `incremental_decoder`
- strong routing + resources -> `router` + `resource_store`
- persistent state -> `session_store` / recovery policy
- timer driven -> `timer_manager`
- close-on-error -> shared protocol error policy
- canonical ownership -> 唯一 public type owner

输出 `expert_activations.json`。

### 4. `architecture.py` + `scorer.py`

生成并评分候选架构：

- 默认会先生成启发式候选
- 如果配置了 `ALI_API`，会尝试使用 LLM 生成/补充候选
- 当前评分是规则硬指标 + coder 兼容度偏好的组合

输出 `candidate_architectures.json`。

### 5. `decision_graph.py`

这是当前实现里最重要的一层之一，已经按“规则 + LLM 混合合成”实现：

- 先根据前面阶段的确定性结果生成 `base decisions`
  - target scope
  - runtime model
  - architecture selection
  - state ownership
  - error policy
  - handler coverage
  - routing strategy
- 这些基础决策会显式记录：
  - `decision_id`
  - `decision_type`
  - `selected_option`
  - `origin`
  - `source_steps`
  - `expert_rule_ids`
  - `downstream_spec_impact`
- 如果配置了 `ALI_API`，再调用 LLM 对这些已有决策做补充：
  - 丰富 `rationale`
  - 补充更清晰的 `downstream_spec_impact`
  - 必要时细化 `source_steps`

因此第五步现在不是纯 LLM，也不是纯规则，而是：

**前序/当前规则决策记录 + LLM 解释补强 + 固定结构合成**

### 6. `planner.py`

生成 `implementation_plan.json`，当前包括：

- `target_profile`
- `protocol_description`
- `scope_decisions`
- `module_graph`
- `canonical_types`
- `state_design`
- `handler_matrix`
- `resource_lifecycle`
- `error_strategy`
- `test_plan`
- `traceability`
- `file_plan`
- `function_plan`
- `unresolved_questions`

其中：

- `canonical_types` 用来保证公共类型唯一归属
- `handler_matrix` 用来保证 `minimum_v1` surface 全覆盖
- `file_plan` / `function_plan` 用来让 spec compiler 纯规则编译

### 7. `spec_compiler.py`

纯规则、无 LLM，将 `implementation_plan` 编译成当前 `coder` 所需的：

- `PROTOCOL_MODULE_SPEC`
- `FILE_SPEC`
- `FUNCTION_SPEC`

输出路径默认在：

```text
<planning_output_dir>/spec_bundle/
```

### 8. `verifier.py`

当前校验包括：

- artifact 完整性
- canonical type 唯一 owner
- module graph 无环
- `minimum_v1` surface 是否全部落到 `handler_matrix`
- coder compatibility：
  - 直接调用 `coder.specs.load_spec_bundle()`

## 命令行

在 `~/SpecForge` 下运行：

```bash
python3 -m agent planning validate \
  --facts ~/SpecForge/agent/facts/out/mqtt/protocol_facts.json \
  --target-profile /tmp/planning_target_profile_mqtt.json \
  --skip-llm-check

python3 -m agent planning plan \
  --facts ~/SpecForge/agent/facts/out/mqtt/protocol_facts.json \
  --target-profile /tmp/planning_target_profile_mqtt.json \
  --output-dir /tmp/planning_mqtt_out

python3 -m agent planning plan \
  --facts ~/SpecForge/agent/facts/gold_facts/mqtt/protocol_facts.json \
  --target-profile /tmp/planning_target_profile_mqtt.json \
  --output-dir /tmp/planning_mqtt_out

python3 -m agent planning verify \
  --output-dir /tmp/planning_mqtt_out
```

## 离线/降级行为

当前实现支持离线降级：

- 如果没有设置 `ALI_API`
  - `validate` 仍可运行
  - `plan` 会退化为规则/启发式模式
  - 第 4 步和第 5 步不会失败，只是跳过 LLM 补充

这让 planning 在本地网络不稳定或离线时仍然可以完成最小闭环。

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
├── spec_compiler.py
├── verifier.py
└── prompts.py
```

## 当前限制

- `spec_compiler` 当前只输出 `coder` 兼容的 C 风格 spec
- 架构生成、决策补充、implementation plan 精修目前都支持 LLM 补充，但在无模型时主要依赖启发式
- 目前尚未引入正式 JSON Schema，只是采用代码内结构约束和 verifier 校验
- 当前生成的 spec 目标是先保证结构正确和下游兼容，不追求一步到位覆盖所有协议细节

## 已验证状态

当前已经完成的本地验证：

- `python3 -m compileall ~/SpecForge/agent/planning`
- `python3 -m agent planning validate ...`
- 使用 MQTT facts + C/broker target profile 直接运行 `PlanningAgent.plan()`
- `python3 -m agent planning verify --output-dir /tmp/planning_mqtt_out`

也就是说，planning 主链路当前已经能够：

`facts -> planning artifacts -> coder-compatible spec -> verifier pass`
