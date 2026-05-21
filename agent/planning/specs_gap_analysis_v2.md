# Planning Specs Gap Analysis v2

生成时间：2026-05-21

本文是第二份独立 gap 分析。旧文件 `specs_gap_analysis.md` 只作为历史背景；本文件重新基于当前 planning 流程代码、2026-05-21 15:44 最新一轮产物，以及 `specs-example/mqtt_specs` 示例 specs 做对照分析。

## 对比对象

- 最新 planning 产物：
  `/home/ljf/SpecForge/agent/planning/out/mqtt/broker__c__linux_epoll__minimum_v1/20260521_154436_105118`
- 最新 `spec_bundle`：
  `/home/ljf/SpecForge/agent/planning/out/mqtt/broker__c__linux_epoll__minimum_v1/20260521_154436_105118/spec_bundle`
- 示例 specs：
  `/home/ljf/SpecForge/specs-example/mqtt_specs`
- 输入 facts：
  `/home/ljf/SpecForge/agent/facts/gold_facts/mqtt_min/protocol_facts.json`
- target profile：
  `/home/ljf/SpecForge/agent/planning/planning_target_profile_mqtt.json`
- 关键 planning 代码：
  `orchestrator.py`、`stages/implementation_plan_merger.py`、`stages/specs_compiler.py`、`stages/coder_spec_lowering.py`、`validators/implementation_plan*.py`、`validators/coder_semantics.py`
- 关键 coder 代码：
  `agent/coder/specs.py`、`agent/coder/prompts.py`、`agent/coder/generation.py`

## 总体判断

当前最新 bundle 已经跨过了最基础的兼容性门槛：strict JSON Schema validation、`agent.coder.specs.load_spec_bundle_from_root()`、planning-side coder semantic gate 全部通过，`014_planning_validation_report.json` 为 `status=success`，且 `error_count=0`、`warning_count=0`。

但这不等于“已经足够让 coder agent 稳定生成 runnable MQTT broker”。当前剩余 gap 的性质已经从旧版本的 schema dialect 问题，转为更高层的 implementation-oriented spec 质量问题：缺少应用入口、模块依赖没有暴露、wire/access mapping 语义错误、call graph 太稀疏、event semantics 被 lowering 丢失、test oracle 缺失，以及 validation gate 对这些问题过于宽松。

换句话说，当前 planning agent 已能生成“loader 可读”的 specs，但还没有稳定生成“coder 可据此实现、编译、运行、互操作”的 protocol specs。

## 核心统计对比

| 项目 | 示例 specs | 最新 planning specs |
|---|---:|---:|
| `PROTOCOL_MODULE_SPEC` | 1 | 1 |
| `FILE_SPEC` | 11 | 4 |
| `FUNCTION_SPEC` | 89 | 30 |
| module 数量 | 6 | 5 |
| `MODULES[].DEPENDENCIES` 总数 | 8 | 0 |
| implementation `dependency_graph.module_edges` | 不适用 | 6 |
| `HEADER.INTERFACE` 总数 | 57 | 8 |
| public `SOURCE.INTERFACE` 总数 | 58 | 8 |
| `HEADER.DATA` 总数 | 19 | 8 |
| module artifacts 总数 | 31 | 16 |
| module `FUNC` artifacts | 18 | 8 |
| module `TYPE` artifacts | 13 | 8 |
| `FUNCTION_TYPE=ALGORITHM` | 55 | 14 |
| `FUNCTION_TYPE=EVENT` | 33 | 0 |
| `FUNCTION_TYPE=ENTRYPOINT` | 1 | 16 |
| 有 `TEST_VECTORS` 的 function spec | 1 | 0 |
| 有 `WIRE_MAPPING` 的 function spec | 1 | 2 |
| 有 `ACCESS_PATHS` 的 file spec | 1 | 1 |
| schema / loader / semantic diagnostics | 0 | 0 |

最新 planning specs 的 public surface 主要集中在 `codec_framing`：

- public functions：`decode_fixed_header`、`decode_connect`、`decode_subscribe`、`decode_publish`、`encode_connack`、`encode_suback`、`encode_pingresp`、`encode_publish`
- public types：`mqtt_codec_framing_t`、`fixed_header`、`connect`、`subscribe`、`publish`

示例 specs 的 public surface 则覆盖完整 broker implementation boundary：

- `network`：connection、TCP server、epoll callback types
- `protocol_codec`：packet model、decoder、encoder、bytes wrapper
- `session`：session object、session manager、session send
- `topic`：topic tree、topic matching
- `router`：message router
- `broker_app`：broker lifecycle 和 `main`

## 已经解决或不再是当前主要 gap

### 1. Strict schema dialect 兼容已经解决

最新 bundle 有 4 个 `FILE_SPEC`、30 个 `FUNCTION_SPEC`、1 个 `PROTOCOL_MODULE_SPEC`，全部 schema-valid 且 coder loader 可加载。旧 gap 中的非法顶层字段、枚举大小写、`CONTRACT` shape、`ACCESS_PATHS` shape 等问题当前不是主要阻塞。

### 2. Public codec header function 和 public type lowering 已有实质进展

`codec_framing_spec.json` 现在有 8 个 public header functions，并且 public signatures 引用的 `struct fixed_header*`、`struct connect*`、`struct subscribe*`、`struct publish*` 已由 canonical types lowering 到 `HEADER.DATA`。

代码路径上，`specs_compiler._data_declarations()` 会消费 `public_api_policy.expected_public_type_roles` 与 `file_layout.exports_type_ids`，再通过 `lower_canonical_type_to_header_data()` 生成 schema-compatible `TYPE_SPEC`。`planning_ir_refs.json` 的 `unresolved_lowering=[]`，说明这一轮 public type lowering 没有遗留 unresolved lowering issue。

### 3. Public artifacts 已不再为空

`mqtt_module_spec.json` 的 `codec_framing.ARTIFACTS` 已包含 8 个 `FUNC` 和 5 个 `TYPE`。`MODULE_SPEC.PUBLIC_SYMBOLS` 也有 16 个 public symbols。旧版本“全部只有 opaque handle”或“没有 public FUNC artifact”的问题已经改善。

## 当前仍存在的 gap

### Gap 1：缺少 runnable broker / application boundary

**严重级别：P0。**

当前产物没有 `main.c`、没有 `broker_app` 模块、没有 `mqtt_broker_create/start/run/stop/destroy` 这类 broker lifecycle API，也没有 network callback 到 protocol handler 的组合层。`role_composition` 虽在 `GENERATION_ORDER` 里，但 `FILES=[]`、`ARTIFACTS=[]`，并且没有任何 function spec。

最新 `implementation_plan.unresolved_questions` 里已经出现多个 blocking question，例如 `role_composition_no_functions_defined`、`role_composition_no_functions`、`role_comp_no_funcs`。但最终 validation report 仍然 success，说明当前流程把“role composition 无实现”记录成 unresolved，却没有把它升级为阻塞性 planning failure。

对 coder 的影响：

- coder 没有进程入口；
- 没有 broker 对外生命周期；
- 没有从 TCP accept/data/close 事件到 decoder、session、router 的组合逻辑；
- 最终更像一个 codec library fragment，而不是 target profile 要求的 `broker` implementation。

示例 specs 中，`broker_app` 明确依赖 `network`、`protocol_codec`、`session`、`router`，并公开 `mqtt_broker_t`、`mqtt_broker_create`、`mqtt_broker_run`、`main` 等 artifacts。当前产物完全没有对应结构。

### Gap 2：`dependency_graph` 已推导，但没有 lowering 到 `MODULES[].DEPENDENCIES`

**严重级别：P0。**

最新 `008_dependency_validation_report.json` 显示 dependency derivation 已通过，并且有：

- `module_edge_count=6`
- `file_edge_count=6`
- `function_edge_count=4`

但最终 `mqtt_module_spec.json` 中 5 个 module 的 `DEPENDENCIES` 全部是空数组。代码根因很直接：`derive_dependency_graph()` 会从 `imports_allowed`、`signature_dependencies`、`state_access`、`calls_allowed` 推导 `dependency_graph.module_edges`；但 `specs_compiler.compile_spec_bundle()` 写 module spec 时只读取 `module.get("dependencies", [])`，没有消费 `dependency_graph.module_edges`。

这造成两个问题：

- coder loader 的 module-order validation 只看 `MODULES[].DEPENDENCIES`，因此看不到真实跨模块依赖；
- `GENERATION_ORDER` 可能与真实依赖不一致也不会暴露。例如 dependency graph 中有 `semantic_state -> resource_routing` 的 `state_access` edge，但当前 `GENERATION_ORDER` 是 `semantic_state` 早于 `resource_routing`。

示例 specs 则把工程依赖直接暴露在 module spec 中：`session -> network`、`router -> topic/session/protocol_codec`、`broker_app -> network/protocol_codec/session/router`。

### Gap 3：wire/access mapping 语义明显错误，但 validation 没有拦住

**严重级别：P0。**

当前 `decode_fixed_header_spec.json` 和 `encode_connack_spec.json` 都带有 14 条 `WIRE_MAPPING`。表面上覆盖了所有 facts fields，但内容语义是错的：

- CONNECT、SUBSCRIBE、PUBLISH 的字段都挂到了 `decode_fixed_header` / `encode_connack`；
- 多个不同 field 的 `TARGET` 都变成 `fixed_header.packet_type`；
- `WIRE_FIELD` 使用的是 `fact:message_model_message_or_command_entries_*` 这类 fact id，而不是 coder 更需要的 wire field name；
- `ACCESS_PATHS.TYPE` 全部是 `"unknown"`，没有从 canonical type fields lowering 出真实 C type。

代码层有两个直接原因：

- `fallback_wire_access_binding()` 取第一个 parser 和第一个 serializer，把所有 wire fields 绑定到这两个函数；
- `merge_wire_access_binding()` 在写 function-level mapping 时，对同一个 function 使用 `update.access_path_ids[0]`，导致所有 mapping 使用同一个 access path；同时把 `field` 写成 `field_id`，丢失了 patch 里的 `wire_field`。

validator 当前只检查 field 覆盖、function id 合法、message id 合法、`path/c_type` 非空等形状约束。由于 `"unknown"` 也是非空字符串，且 `fixed_header.packet_type` 是一个 public access path，错误 mapping 能通过 schema、loader 和 semantic gate。

这会直接误导 coder：它会被要求在解析 CONNECT/PUBLISH 字段时写 `fixed_header.packet_type`，从而生成协议语义错误的 parser/serializer。

### Gap 4：public data model 仍然不够实现导向

**严重级别：P1。**

当前 public canonical types 是：

- `fixed_header`
- `connect`
- `subscribe`
- `publish`

它们解决了 public header declaration completeness，但与示例 specs 的 implementation-facing data model 仍有明显差距。示例中有：

- `mqtt_packet_type_t` enum，包含 `MQTT_PKT_CONNECT`、`MQTT_PKT_PUBLISH`、`MQTT_PKT_SUBSCRIBE`、`MQTT_PKT_PINGREQ` 等具体 packet constants；
- `mqtt_connect_payload_t`
- `mqtt_publish_payload_t`
- `mqtt_subscribe_topic_t`
- `mqtt_subscribe_payload_t`
- `mqtt_packet_t`，用 `type + union v` 统一承载不同 packet payload；
- `mqtt_packet_free()`；
- 细粒度 `ACCESS_PATHS`，如 `mqtt_packet_t.v.publish.payload_len`、`mqtt_packet_t.v.subscribe.topics[i].qos`。

当前 types 还存在 C symbol 风险：`connect` 是过于通用的 typedef name，和 POSIX `connect()` 位于 C 普通标识符命名空间，后续只要 header 组合包含 socket API，就有潜在冲突。示例 specs 通过 `mqtt_*` 前缀规避了这个问题。

当前 facts 里 `protocol_meta` 没有显式 `protocol_version`，message model 也只抽取了 4 个 message entries。这里不能要求 planning agent 凭空发明完整 MQTT 3.1.1 model；但 planning agent 至少需要把已知 fields 组织成 coder-friendly、命名稳定、可组合的 packet data model，而不是只暴露裸 message structs。

### Gap 5：internal call graph / service graph 太稀疏

**严重级别：P1。**

当前 `dependency_graph.function_edges` 只有 4 条：

- `create_client_session -> initialize_session_store`
- `destroy_client_session -> cleanup_session_store`
- `add_subscription -> check_resource_limits`
- `match_and_route_publish -> encode_publish`

关键 broker flow 没有被表达出来：

- `epoll_event_loop` 没有调用 `accept_new_connection`、`read_from_socket`、`write_to_socket`；
- transport 层没有调用 decoder；
- `dispatch_connect` 没有调用 session create / CONNACK encode / write；
- `dispatch_subscribe` 没有调用 subscription add / SUBACK encode / write；
- `dispatch_publish_in` 没有调用 routing / outbound encode / write；
- `trigger_connection_close` 没有连接到 `close_connection`；
- `manage_keepalive_timer` 没有和 runtime timeout source 建立清晰 call/service relation。

`agent/coder/prompts.py` 会把 `CALL_CONTRACTS`、`ACCESS_PATHS`、`WIRE_MAPPING`、`TEST_VECTORS` 作为 machine constraints 传给 coder。当前 call contracts 这么少，意味着 coder 只能靠自然语言 `ROLE` 和 `LOGIC.ACTION` 猜模块协作，容易偏离 planning decision。

示例 specs 的 broker flow 更明确：network callbacks 驱动 decoder，decoder 回调进入 `handle_packet`，再按 packet type 调用 session/router/encoder/send。当前 planning specs 没有把这条端到端路径机器化。

### Gap 6：模块和文件粒度仍是 conceptual module，不是可直接落地的工程边界

**严重级别：P1。**

当前 modules/files：

- `transport_runtime.h/c`
- `codec_framing.h/c`
- `semantic_state.h/c`
- `resource_routing.h/c`
- `role_composition` 空模块

示例 modules/files：

- `network/connection`
- `network/tcp_server`
- `protocol/mqtt_packet`
- `protocol/mqtt_decoder`
- `protocol/mqtt_encoder`
- `broker/session`
- `broker/session_manager`
- `topic/topic_tree`
- `router/message_router`
- `broker/broker`
- `main`

当前的 `resource_routing` 同时吞掉 session store、topic registry、routing dispatch；`transport_runtime` 同时吞掉 connection 和 TCP server；`codec_framing` 同时吞掉 packet model、decoder、encoder。对于最小 demo 可以接受较粗模块，但对 coder 生成 C implementation 来说，这会让 private state、ownership、include boundary、测试粒度、repair 粒度全部变差。

这也是 planning agent 的研究核心问题之一：不能只把 facts 聚类成概念模块，还要把事实转成 coder 可执行的 module/file/function decomposition。

### Gap 7：`EVENT` semantics 在 lowering 过程中丢失

**严重级别：P1。**

最新 `implementation_plan` 里大量 functions 的 `logic_kind` 是 `EVENT`，且有完整 `event_contract`。但最终 30 个 `FUNCTION_SPEC` 中 `EVENT=0`，反而有 16 个 `ENTRYPOINT`。

代码层原因是 `normalize_function_type_for_coder()` 只在 `coder_function_type == "EVENT"` 且 event contract 完整时输出 `EVENT`；如果 `coder_function_type == "ENTRYPOINT"` 则直接输出 `ENTRYPOINT`，否则输出 `ALGORITHM`。它没有把 `logic_kind=EVENT` 作为 lowering 信号。

结果是：

- handler、resource lifecycle、timer callback 这类事件语义在 coder specs 中变成 `ENTRYPOINT` 或 `ALGORITHM`；
- function spec 的 `EVENT.TRIGGER/PRECONDITION/STATE_CHANGE/RESPONSE` 被降成普通 `LOGIC`；
- coder 更难区分 process entrypoint、public API、internal event handler、private helper。

示例 specs 中只有 `main` 是 `ENTRYPOINT`，而 callback、connection/session lifecycle 等大量函数是 `EVENT`。当前 16 个 `ENTRYPOINT` 明显不符合这个语义分布。

### Gap 8：test plan 没有 lowering 为 coder 可用 `TEST_VECTORS`

**严重级别：P1。**

当前 `implementation_plan.test_plan` 已经有 5 个测试种子：

- valid CONNECT
- invalid protocol
- publish routing
- keepalive enforcement
- subscribe lifecycle

但最终 `spec_bundle` 中没有任何 `TEST_VECTORS`。`agent/coder/prompts.py` 已经会读取 file/function `TEST_VECTORS` 并传给 coder；`agent/coder/specs.py` 也会把 protocol codec 缺少 vectors 当作 warning 的一种情况。但 `specs_compiler.py` 当前没有把 `implementation_plan.test_plan` 或 function/file test seeds lowering 到 strict specs。

这会影响评估闭环：planning agent 产物虽然提到了测试想法，但 coder agent 不能把它们当作机器约束，也无法据此生成或验证 conformance checks。

### Gap 9：protocol metadata / scope 丢失

**严重级别：P2。**

当前 module spec：

```json
{
  "NAME": "mqtt",
  "SPEC_VERSION": "unspecified",
  "ROLES": ["BROKER"]
}
```

示例 module spec：

```json
{
  "NAME": "MQTT",
  "SPEC_VERSION": "3.1.1",
  "ROLES": ["BROKER"],
  "SCOPE": "MQTT 3.1.1 Broker subset only; ..."
}
```

输入 facts 的 `protocol_meta.target_scope` 包含 `minimal MQTT 3.1.1 broker subset for planning-agent debugging`，但 `lower_protocol_meta_for_coder()` 只查 `protocol_version/spec_version/version` 等字段，没有消费 `target_scope`，也没有输出 `SCOPE`。

这不是 schema blocker，但会让 coder 和评估脚本少掉重要上下文：当前目标是 MQTT 3.1.1 的 broker subset，而不是 generic `mqtt`。

### Gap 10：validation gate 对“可运行性”和“工程完整性”过于宽松

**严重级别：P1。**

最新 `014_planning_validation_report.json` 为 0 error / 0 warning，但人工对照仍能看到多个 P0/P1 gap。这说明当前 validation 主要覆盖了以下内容：

- JSON schema shape；
- coder loader shape；
- public function 是否在 header；
- public signature type 是否可解析；
- public artifacts 是否存在；
- dependency graph 是否引用合法 id；
- wire fields 是否“被某个 parser/serializer 覆盖”。

它没有覆盖或没有足够严格覆盖：

- target role 为 `broker` 时必须有 runnable entrypoint 或 broker lifecycle public API；
- `role_composition` 这种 required capability module 不能是 empty module；
- `implementation_plan.unresolved_questions[*].blocking=true` 不能在最终 report 中静默通过；
- `dependency_graph.module_edges` 必须反映到 `MODULES[].DEPENDENCIES` 或 sidecar 中被 coder 明确消费；
- `WIRE_MAPPING.TARGET` 必须与对应 packet/field 的 access path 一致，而不是只要求 target 是某个 public path；
- `ACCESS_PATHS.TYPE` 不能是 `"unknown"`；
- `logic_kind=EVENT` 不能在 lowering 中被全部吞掉；
- `test_plan` 必须进入 specs 或 validation report 说明未 lower；
- module/file decomposition 必须覆盖 target surface units 到可执行 flow，而不是只覆盖 capability ids。

这类 validation blind spot 很关键，因为它会让 pipeline 显示“success”，但下游 coder 仍可能生成不可运行或语义错误的 implementation。

### Gap 11：error handling、timer、session lifecycle 仍停留在意图层

**严重级别：P2。**

Protocol profile 和 engineering constraints 已激活：

- `timer_manager_required`
- `persistent_session_store`
- `connection_close_error_policy`
- `routing_resource_split`
- `canonical_ownership_required`

实现计划里也有 `manage_keepalive_timer`、`trigger_connection_close`、`initialize_session_store`、`cleanup_session_store` 等函数。但这些函数没有形成完整 ownership/call/service flow：

- keepalive 是否只存储还是强制 timeout，仍是 open question；
- decode failure 是忽略还是关闭连接，也仍是 open question；
- connection close path 没有和 transport close / session cleanup / subscription cleanup 明确绑定；
- session manager 与 topic registry 没有对外 API，也没有和 broker flow 连接。

这些问题不一定全部属于 minimum_v1 的必须实现范围，但 planning specs 需要明确区分“本轮支持”“显式 deferred”“blocking unresolved”。当前部分信息被放进 `unresolved_questions`，但没有进入最终 coder-facing spec 或 failure policy。

## 代码层根因汇总

### 1. Planning pipeline 已分阶段，但后段 compiler 只消费了部分 planning 语义

Step 5 生成了 implementation plan，Step 6 推导 dependency graph，Step 7 建 blueprint，Step 8 deterministic compile specs。但 `specs_compiler.py` 目前主要消费 modules/files/functions/canonical_types/access_path_table，对以下 planning 语义消费不足：

- `dependency_graph`
- `test_plan`
- blocking `unresolved_questions`
- `logic_kind=EVENT`
- richer state/resource lifecycle
- capability-to-call coverage

这导致 planning IR/implementation plan 中有些信息存在，但最终 coder specs 看不到或看不懂。

### 2. deterministic fallback 保证了覆盖，但没有保证语义质量

`fallback_wire_access_binding()` 的目标是避免 LLM patch 不合法时流程中断，但它用“第一个 parser / 第一个 serializer”覆盖所有 fields。这个 fallback 对 schema coverage 有用，对 protocol semantics 有害。类似地，role composition 的 function inventory 可以合法返回空数组，但这对 runnable broker 是不可接受的。

### 3. validators 更偏 shape validation，缺少 domain/usefulness validation

当前 validators 能拦非法 id、非法 enum、缺失 public type、缺失 header declaration，但不判断：

- CONNECT fields 是否绑定到 CONNECT parser；
- PUBLISH payload 是否有 length；
- generic type name 是否会和 C/POSIX symbol 冲突；
- required role composition 是否有函数；
- broker target 是否有 `main` 或 broker lifecycle API；
- test plan 是否真正进入 coder contract。

这些不是“发明协议行为”，而是把已有 facts、target profile 和 example-derived coder expectations 转成 implementation spec 完整性规则。

## 建议修复顺序

### 第一优先级：先让 success 真正代表 coder 可用

1. 在 final planning validation 中把 blocking `unresolved_questions` 升级为 error，至少对 `target_kind=module` 且 required capability owner 为空实现的情况报错。
2. 增加 target role validation：`target_role=broker` 时必须有 application/broker boundary，至少满足 `main` 或 broker lifecycle public API 二者之一；如果明确 scope 不包含 runnable app，必须在 target profile/spec sidecar 中显式声明。
3. 将 `dependency_graph.module_edges` lowering 到 `MODULES[].DEPENDENCIES`，或提供 coder loader 会读取的等价 dependency sidecar；同时校验 `GENERATION_ORDER` 与这些 dependencies 一致。
4. 修复 `wire_access_binding` fallback 和 merge：按 message/function role 匹配 parser/serializer，保留 `wire_field`，每条 mapping 使用自己的 `access_path_id`，并从 canonical type fields lowering `c_type`。
5. 收紧 wire/access validator：禁止 `ACCESS_PATHS.TYPE="unknown"`；校验 `PACKET+WIRE_FIELD` 对应的 `TARGET` 必须是同一 message field 的 access path。

### 第二优先级：补齐 broker implementation skeleton

1. 让 `role_composition` 不再是空模块，或把它明确合并为 `broker_app` / coordinator module。
2. 生成最小 broker lifecycle：create/destroy/start/run/stop 或 main，二者至少有一个可作为 coder entry boundary。
3. 补齐 network callback -> decoder -> semantic dispatch -> session/router -> encoder/send 的 call contracts。
4. 将 session/topic/router 从 `resource_routing` 中拆成更清晰的 file-level units，或至少在同一 module 内生成更具体的 files/functions。

### 第三优先级：提高 specs 的工程语义密度

1. 把 `logic_kind=EVENT` 和完整 `event_contract` lowering 为 coder `EVENT`，把 `ENTRYPOINT` 保留给 process/public entrypoints。
2. 从 `implementation_plan.test_plan` 生成 file/function `TEST_VECTORS` 或 conformance sidecar，并确保 coder prompt 消费。
3. 改善 canonical type naming：优先生成 protocol-prefixed public C symbols，例如 `mqtt_fixed_header_t`、`mqtt_connect_payload_t`，避免 `connect` 这类 C namespace collision。
4. 补充 packet enum/constants 和 packet container。若 facts 不足，应输出明确 unresolved/deferred，而不是生成过浅类型后认为 complete。
5. 将 `target_scope`、protocol version、scope boundary lowering 到 `PROTOCOL` 或 sidecar，避免 `SPEC_VERSION=unspecified` 长期存在。

## 当前结论

最新一轮的 planning agent 产物已经证明了 compiler/loader compatibility 可以闭环，这是很好的基础。但从 SpecForge 的研究目标看，当前最重要的 gap 已经变成“planning agent 是否能把 protocol facts 转成足够工程化、可执行、可验证的 specs”。

当前最需要修的是：让 validation gate 对 runnable broker boundary、dependency lowering、wire/access correctness 和 blocking unresolved questions 变得更严格。否则后续会继续出现一种危险状态：planning report 全绿，但 coder agent 拿到的 specs 仍缺少实现 MQTT broker 所需的关键结构。
