# Planning Specs 与示例 Specs 差距分析

生成时间：2026-05-20

本文记录当前 planning 流程产出的 MQTT `spec_bundle` 与现有示例 specs 之间的差异，并按对“能否直接输入 coder 生成可编译 protocol implementation”的影响程度排序。目标是作为后续修复 `planning agent -> coder agent` 兼容性的持久性记忆。

## 对比对象

- 当前 planning 输出：
  `/home/ljf/SpecForge/agent/planning/out/mqtt/broker__c__linux_epoll__minimum_v1/20260520_161430_860466/spec_bundle`
- 示例 specs：
  `/home/ljf/SpecForge/specs-example/mqtt_specs`
- coder schema：
  `/home/ljf/SpecForge/specs-example/specs_schema`
- 当前 specs compiler：
  `/home/ljf/SpecForge/agent/planning/stages/specs_compiler.py`

## 总体结论

当前 planning 输出已经具备 `PROTOCOL_MODULE_SPEC`、`FILE_SPEC`、`FUNCTION_SPEC` 三类文件，能被 `agent.coder.specs.load_spec_bundle_from_root()` 轻量加载；但它还不是 coder 当前 dialect 下的有效 specs。核心问题是：planning 输出更接近 planning IR 的实现意图清单，而示例 specs 是严格面向 coder generation 的工程规格。

严格 JSON Schema validation 结果：

| 项目 | 示例 specs | 当前 planning specs |
|---|---:|---:|
| `PROTOCOL_MODULE_SPEC` | 1 | 1 |
| `FILE_SPEC` | 11 | 5 |
| `FUNCTION_SPEC` | 89 | 29 |
| schema invalid files | 0 | 35 |
| schema errors | 0 | 632 |
| `HEADER.INTERFACE` 总数 | 57 | 0 |
| `SOURCE.INTERFACE` 总数 | 89 | 29 |
| 有 `TEST_VECTORS` 的 spec | 1 | 0 |
| 有 `ACCESS_PATHS` 的 spec | 4 | 2 |
| 有 `WIRE_MAPPING` 的 function spec | 1 | 2 |

## 1. 严格 schema dialect 不兼容

**重要性：最高。** 当前 planning specs 在 loader 层看似可读，但所有 35 个 specs 文件都无法通过 `specs_schema/*.json`。这意味着 coder 目前缺少可靠的机器契约，后续生成、校验、修复都可能基于不稳定字段运行。

主要差异：

- 示例 specs 完全满足 `module_spec_schema.json`、`file_spec_schema.json`、`function_spec_schema.json`。
- 当前 planning specs 使用了 schema 不允许的字段，例如：
  - `TRACEABILITY`
  - `CAPABILITY_IDS`
  - `STATE_ACCESS`
  - `CALLS_ALLOWED`
- 当前 planning specs 中部分字段 shape 与 schema 不同，例如：
  - `CALL_CONTRACTS` 使用 planning IR 风格字段，如 `callee_function_id`、`param_bindings`。
  - file `CONTRACT` 使用 `INPUT/OUTPUT/ERROR`，而 schema 需要 `PRECONDITION/POSTCONDITION/IDEMPOTENT/THREAD_SAFETY`。
  - event body 使用 `INPUT/ACTION/OUTPUT/INVARIANTS_USED`，而 schema 中 `EVENT` 需要 `TRIGGER/PRECONDITION/INPUT/ACTION/STATE_CHANGE/RESPONSE/EVENT_TYPE`。

简要修复建议：

- 在 `specs_compiler.py` 中加入明确的 lowering 层：`planning IR -> coder spec schema`。
- compiler 输出前必须执行 JSON Schema validation，任何 invalid spec 直接阻断 run。
- planning 专用字段不要直接混入 strict coder schema。可选方案：
  - 映射到 schema 已允许字段，如 `DOC_REF`、`ROLE`、`CONSISTENCY_RULES`、`TEST_VECTORS`。
  - 或扩展 coder schema 与 loader，正式支持 `TRACEABILITY` 等字段。
- 推荐先保持 coder schema 不变，优先让 planning compiler 输出完全兼容示例 specs dialect。

## 2. `HEADER.INTERFACE` 全部为空，public API 缺失

**重要性：最高。** coder 的 header renderer 以 `HEADER.INTERFACE` 为 public function declarations 的唯一来源。当前 planning 输出中 5 个 file spec 的 `HEADER.INTERFACE` 全部为空，导致生成 header 只有 opaque typedef，没有 public API。

示例 specs：

- `HEADER.INTERFACE` 总数为 57。
- 除 `main/main_spec.json` 外，每个工程模块都有 public functions。
- broker/app 层提供 `mqtt_broker_create`、`mqtt_broker_start`、`mqtt_broker_run`、`mqtt_broker_destroy` 等入口。

当前 planning specs：

- `HEADER.INTERFACE` 总数为 0。
- 所有 functions 都只出现在 `SOURCE.INTERFACE`。
- coder 渲染 `main.c` 时找不到 app create/start/run/destroy，只能 fallback 到不存在的 `mqtt_app_create(port)`。

简要修复建议：

- specs compiler 必须把 visibility 为 public 的 function 同步写入 `HEADER.INTERFACE`。
- `HEADER.INTERFACE.KIND` 必须是 `FUNC`。
- `HEADER.INTERFACE.FUNCTION_TYPE` 必须归一化为 coder 可接受值：`ALGORITHM`、`EVENT`、`EVENT_HANDLER`、`ENTRYPOINT`。
- 至少为 top-level broker/app module 生成稳定 public API：
  - `mqtt_broker_create`
  - `mqtt_broker_destroy`
  - `mqtt_broker_start`
  - `mqtt_broker_run`
  - `mqtt_broker_stop`
- 加一个 header render smoke test：对 planning 输出调用 `agent.coder.generation.render_header()`，检查 public API 不为空。

## 3. 没有可用的 broker/app 入口模块

**重要性：最高。** coder 当前会从 generation order 的末尾模块寻找 app file spec，并根据 header public interface 推导 main 函数。当前 planning 输出的末尾模块是 `role_composition`，但它没有 create/start/run/destroy 接口，因此不能形成 runnable broker。

示例 specs：

- 有 `broker_app` module。
- `FILES` 包含：
  - `../main.c`
  - `../broker/broker.h`
  - `../broker/broker.c`
- broker header 暴露 lifecycle API。
- `main.c` 能调用 broker lifecycle。

当前 planning specs：

- 没有 `main.c` file spec 或 module file entry。
- `role_composition` 只有 `dispatch_broker_role`，且不是 public app lifecycle。
- binary name fallback 为 `mqtt_app`，role 也只写成 `target`，没有 broker role。

简要修复建议：

- 在 planning blueprint 或 specs compiler 阶段显式生成 `broker_app` 或等价 top-level module。
- module spec 中 `PROTOCOL.ROLES` 应包含 `BROKER`，不要只写 `target`。
- 加入 `main.c` 到 `MODULES[].FILES`，或者保证 coder 能生成 main 并绑定到 broker public API。
- top-level module 的 header 必须包含 create/start/run/destroy 函数，供 `render_main_c()` 自动识别。

## 4. 模块拆分停留在概念层，不是 coder 工程层

**重要性：高。** 当前 planning 输出的模块名体现了协议理解维度，但没有形成 coder 需要的 C 工程边界和依赖链。

示例 specs 模块：

- `network`
- `protocol_codec`
- `session`
- `topic`
- `router`
- `broker_app`

这些模块具有明确依赖：

- `session -> network`
- `router -> topic/session/protocol_codec`
- `broker_app -> network/protocol_codec/session/router`

当前 planning specs 模块：

- `transport_runtime`
- `codec_framing`
- `semantic_state`
- `resource_routing`
- `role_composition`

所有 `DEPENDENCIES` 均为空，导致 coder 无法根据 generation order 建立 header dependency 与 implementation dependency。

简要修复建议：

- 在 planning agent 中保留 protocol analysis 模块，但 specs compiler 输出时应 lowering 到 implementation modules。
- MQTT minimum broker 推荐先对齐示例模块边界：
  - `transport_runtime` lowering 为 `network`
  - `codec_framing` lowering 为 `protocol_codec`
  - `semantic_state` + `resource_routing` lowering/拆分为 `session`、`topic`、`router`
  - `role_composition` lowering 为 `broker_app`
- 如果保留当前模块名，也必须补齐真实 dependencies，例如：
  - `semantic_state -> codec_framing/resource_routing`
  - `resource_routing -> codec_framing`
  - `role_composition -> transport_runtime/codec_framing/semantic_state/resource_routing`

## 5. `MODULES[].ARTIFACTS` 语义不对

**重要性：高。** 示例 specs 中 `ARTIFACTS` 是 coder 可见的工程符号清单；当前 planning specs 中 `ARTIFACTS` 只是 file spec 指针。

示例 specs：

- artifact kinds 包括 `TYPE`、`FUNC`。
- artifact name 是真实 public symbol，例如 `mqtt_connection_t`、`mqtt_decoder_feed`、`mqtt_encode_connack`。

当前 planning specs：

- 每个 module 只有一个 artifact：
  - `{"KIND": "FILE_SPEC", "PATH": "..."}`
- 但 schema 中 artifact kind 只允许：
  - `TYPE`
  - `FUNC`
  - `VAR`
  - `CONST`
  - `MACRO`

简要修复建议：

- specs compiler 生成 module artifact 时，必须从 file/header/function inventory 中抽取 public symbols。
- 删除 `FILE_SPEC` artifact kind。
- `FILES` 负责记录 file paths，`ARTIFACTS` 只记录 coder 需要知道的 public symbols。

## 6. function type 枚举未归一化

**重要性：高。** function spec schema 只接受三类：`ALGORITHM`、`EVENT`、`ENTRYPOINT`。当前 planning 输出直接使用了 function inventory 中的细粒度分类。

示例 specs function types：

- `ALGORITHM`
- `EVENT`
- `ENTRYPOINT`

当前 planning specs function types：

- `PARSER`
- `SERIALIZER`
- `HANDLER`
- `VALIDATOR`
- `INTERNAL_HELPER`
- `RESOURCE_LIFECYCLE`
- `ERROR_HELPER`

简要修复建议：

- 在 compiler 中建立映射表：
  - `PARSER`、`SERIALIZER`、`VALIDATOR`、`INTERNAL_HELPER`、`RESOURCE_LIFECYCLE`、`ERROR_HELPER` -> `ALGORITHM`
  - `HANDLER` -> `EVENT` 或 `ALGORITHM`，取决于是否有真实 event trigger/state change。
  - broker/app lifecycle public entrypoints -> `ENTRYPOINT` 或 `ALGORITHM`。
- 可把原始细粒度类型保存在 `ROLE` 文本或扩展字段中，但不能破坏 strict schema。

## 7. visibility 枚举大小写和取值不兼容

**重要性：高。** 当前 file specs 中 source interface 和 data item 使用了 schema 不接受的 visibility。

示例 schema 要求：

- `HEADER.DATA` / `SOURCE.DATA` 的 `VISIBILITY`：`PUBLIC` 或 `PRIVATE`
- `HEADER.INTERFACE` / `SOURCE.INTERFACE` 的 `VISIBILITY`：`public` 或 `private`

当前 planning specs 出现：

- `INTERNAL`
- `PRIVATE` 用在 interface visibility
- `internal` 经 `.upper()` 后变成 `INTERNAL`

简要修复建议：

- data visibility：
  - public -> `PUBLIC`
  - private/internal/static -> `PRIVATE`
- interface visibility：
  - public/exported -> `public`
  - private/internal/static -> `private`
- 不要把 function/file visibility 简单 `.upper()` 后写入所有位置。

## 8. interface `KIND` 使用了 `FUNCTION` 而不是 `FUNC`

**重要性：高。** file spec schema 要求 interface item 的 `KIND` 为常量 `FUNC`。当前 planning compiler 在 `_interface()` 中写入 `KIND: "FUNCTION"`。

简要修复建议：

- 将 `specs_compiler.py::_interface()` 输出的 `KIND` 改为 `FUNC`。
- 同步检查 module `ARTIFACTS` 中 function kind 也使用 `FUNC`。

## 9. parameter ownership 枚举不兼容

**重要性：中高。** 当前 function signature params 使用了小写或非 schema 值，导致 function spec invalid。

示例 schema 允许：

- `BORROWED`
- `OWNED`
- `OWNED_BY_CALLER`
- `TRANSFER`
- `SHARED`
- `UNKNOWN`

当前 planning specs 出现：

- `borrowed`
- `value`
- `transferred`

简要修复建议：

- 在 `_param_for_coder()` 中归一化：
  - `borrowed` -> `BORROWED`
  - `owned` -> `OWNED`
  - `owned_by_caller` -> `OWNED_BY_CALLER`
  - `transfer` / `transferred` -> `TRANSFER`
  - `shared` -> `SHARED`
  - `value` 对 C scalar 参数建议映射为 `BORROWED` 或 `UNKNOWN`；更好是扩展 planning IR 的 ownership 语义，区分 ownership 与 passing mode。
- 短期为了兼容 schema，`value` 可统一映射为 `BORROWED` 或 `UNKNOWN`，并在 `ROLE` 中说明 scalar by value。

## 10. `SOURCE.INTERFACE.CONTRACT` shape 不兼容

**重要性：中高。** 当前 planning 输出的 source contract 是 `INPUT/OUTPUT/ERROR`，而示例 specs 使用通用 contract。

示例 contract：

- `PRECONDITION`
- `POSTCONDITION`
- `IDEMPOTENT`
- `THREAD_SAFETY`

当前 contract：

- `INPUT`
- `OUTPUT`
- `ERROR`

简要修复建议：

- compiler 中将 `input_contract` lowering 为 `PRECONDITION` text items。
- 将 `output_contract` lowering 为 `POSTCONDITION` text items。
- 将 `error_behavior` 合并进 `POSTCONDITION` 或 `ROLE`。
- 默认：
  - `IDEMPOTENT: false`
  - `THREAD_SAFETY: "SINGLE_THREAD_ONLY"`
- 后续可由 planning agent 显式推断幂等性和线程安全。

## 11. `EVENT` body shape 不兼容

**重要性：中高。** planning 输出中 14 个 function spec 走了 `EVENT`，但 body 仍是 logic-like shape。

示例 `EVENT` 需要：

- `TRIGGER`
- `PRECONDITION`
- `INPUT`
- `ACTION`
- `STATE_CHANGE`
- `RESPONSE`
- `EVENT_TYPE`

当前 `EVENT` 包含：

- `INPUT`
- `ACTION`
- `OUTPUT`
- `INVARIANTS_USED`

简要修复建议：

- 如果无法生成完整 event shape，就把这些 functions 暂时归类为 `ALGORITHM` 并使用 `LOGIC`。
- 对真正 handler/event functions，compiler 需要从 planning behavior contract 中补齐：
  - trigger，例如 “decoded CONNECT packet”
  - precondition，例如 “session not connected”
  - state change，例如 “mark session connected”
  - response，例如 “send CONNACK”
  - event type，例如 `PACKET_RECEIVED`

## 12. 缺少 shared public data model

**重要性：中高。** coder 生成跨模块 C 代码时高度依赖 public structs/enums/typedefs。示例 specs 明确建模 packet、bytes、connection、session、topic tree、router 等 public symbols；当前 planning specs 基本只有每个概念模块的 opaque handle。

示例 specs 中关键 public data model：

- `mqtt_packet_t`
- `mqtt_packet_type_t`
- `mqtt_connect_payload_t`
- `mqtt_publish_payload_t`
- `mqtt_subscribe_payload_t`
- `mqtt_bytes_t`
- `mqtt_connection_t`
- `mqtt_session_t`
- `mqtt_topic_tree_t`
- callback typedefs

当前 planning specs：

- 每个 file 只有 `mqtt_<module>_t` opaque handle。
- `struct connect`、`struct publish`、`struct subscribe` 多为 source private/internal 类型。
- 没有形成跨模块可引用的 canonical packet representation。

简要修复建议：

- 在 specs compiler 中生成独立 packet/data file spec，例如 `protocol/mqtt_packet.h/c`。
- 将 protocol facts 中的 message fields lowering 到 `TYPE_SPEC`：
  - enum packet type
  - union payload
  - CONNECT/PUBLISH/SUBSCRIBE payload structs
- 所有 codec、semantic、router functions 共享 `mqtt_packet_t`，避免每个模块生成私有 message struct。

## 13. source private data 的 `TYPE_SPEC` 不完整或重复

**重要性：中。** 示例 specs 对重要 public types 有明确 `TYPE_SPEC`，或者明确 opaque。当前 planning specs 中 source data 包含空 struct、重复 struct、内部 visibility 不合规。

观察到的问题：

- `struct mqtt_<module>` 的 `FIELDS` 为空。
- `codec_framing` 中 `struct publish` 出现两次，分别作为 input/output structure。
- `SOURCE.DATA.VISIBILITY` 出现 `INTERNAL`，schema 不接受。

简要修复建议：

- 去重 source data declarations。
- 对 private implementation structs：
  - 如果 coder 可以自由设计内部字段，保留 opaque/private role 即可。
  - 如果跨函数需要共享状态，应输出具体 `TYPE_SPEC.FIELDS`。
- 所有 non-public visibility 统一为 `PRIVATE`。

## 14. `ACCESS_PATHS` 格式不兼容且覆盖不足

**重要性：中。** coder prompt 会把 `ACCESS_PATHS` 当作允许访问 public structs 的机器约束。当前 planning specs 的数量少、格式也不完全符合 schema。

示例 specs：

- 4 个 specs 含 `ACCESS_PATHS`。
- access path item 形如：
  - `PATH`
  - `TYPE`
  - `ROLE`

当前 planning specs：

- 只有 2 个 specs 含 `ACCESS_PATHS`。
- file-level `ACCESS_PATHS` 使用了：
  - `PATH`
  - `FIELD_ID`
  - `SOURCE`
- 这不符合 schema，需要 `TYPE`。

简要修复建议：

- compiler 输出 `ACCESS_PATHS` 时严格使用 `PATH/TYPE/ROLE`。
- 从 `access_path_table` 补全 public packet/session/router access paths。
- 对 `WIRE_MAPPING.TARGET` 必须能映射到已声明的 public access path。

## 15. `WIRE_MAPPING` 信息覆盖不稳定

**重要性：中。** 当前 planning specs 有 2 个 function spec 含 `WIRE_MAPPING`，示例只有 1 个，但示例的 mapping 与 public packet access paths 对齐，更适合约束 coder。

当前风险：

- 如果 `WIRE_MAPPING.TARGET` 指向不存在的 public `ACCESS_PATHS`，coder validation 会报 `unknown_wire_mapping_target`。
- 当前 shared packet model 缺失，wire mapping 即使存在也难以驱动正确代码。

简要修复建议：

- 先建立 canonical `mqtt_packet_t` 和 packet access paths。
- 再让 decoder/encoder function specs 输出 wire mapping。
- 对每个 `store_in_field` mapping 做 validation：
  - target path 存在
  - target type 与 field type 一致
  - parse/skip/reject 策略明确

## 16. 缺少 test vectors 与 conformance checks

**重要性：中。** 示例 specs 至少有 1 个 codec 相关 `TEST_VECTORS`，当前 planning specs 没有任何 `TEST_VECTORS`。这会削弱 coder repair 和 verifier 的反馈质量。

简要修复建议：

- 从 protocol facts 中抽取最低限度 MQTT wire examples：
  - CONNECT success -> CONNACK
  - SUBSCRIBE -> SUBACK
  - PINGREQ -> PINGRESP
  - QoS0 PUBLISH route
  - invalid Remaining Length
- 将 test vectors 放到 file/function spec 中，优先覆盖 codec functions。
- 后续 verifier 可利用这些 vectors 生成 unit/smoke tests。

## 17. 文件路径风格与工程布局不同

**重要性：中。** 示例 specs 使用 `../network/...`、`../protocol/...`、`../broker/...` 等路径，生成后形成 broker 工程结构。当前 planning specs 使用 `mqtt/<module>/<module>.h/c`，路径本身不一定错误，但和示例 coder assumptions 有偏差。

示例 layout：

- `network/connection.h/c`
- `network/tcp_server.h/c`
- `protocol/mqtt_packet.h/c`
- `protocol/mqtt_decoder.h/c`
- `protocol/mqtt_encoder.h/c`
- `broker/session.h/c`
- `topic/topic_tree.h/c`
- `router/message_router.h/c`
- `broker/broker.h/c`
- `main.c`

当前 layout：

- `mqtt/transport_runtime/transport_runtime.h/c`
- `mqtt/codec_framing/codec_framing.h/c`
- `mqtt/semantic_state/semantic_state.h/c`
- `mqtt/resource_routing/resource_routing.h/c`
- `mqtt/role_composition/role_composition.h/c`

简要修复建议：

- 短期建议对齐示例 layout，减少 coder prompt 和 verifier 的偏差。
- 如果保留 `mqtt/...` layout，需要确认：
  - include path 正确。
  - `render_makefile()` 能覆盖 `main.c`。
  - dependency headers 能被正确注入。
  - smoke test 不依赖示例路径。

## 18. `PROTOCOL` metadata 不够贴合目标

**重要性：中。** 示例 specs 明确 `NAME: MQTT`、`SPEC_VERSION: 3.1.1`、`ROLES: ["BROKER"]`。当前 planning specs 写为 `NAME: mqtt`、`SPEC_VERSION: spec_blueprint/v1`、`ROLES: ["target"]`。

影响：

- coder binary name fallback 变成 `mqtt_app`，不是 broker-oriented。
- role-specific generation 不能识别 broker/server。
- spec version 没有表达协议版本。

简要修复建议：

- `PROTOCOL.NAME` 使用规范协议名：`MQTT`。
- `PROTOCOL.SPEC_VERSION` 使用协议版本：`3.1.1`。
- `PROTOCOL.ROLES` 使用目标角色：`BROKER`。
- planning schema version 可放入 manifest 或 `CONSISTENCY_RULES`，不要替代 protocol version。

## 19. function specs 数量和粒度不足

**重要性：中。** 示例 specs 有 89 个 function spec，覆盖 public API、private helpers、codec primitives、topic matching、session manager、network lifecycle 等。当前 planning specs 只有 29 个，粒度更粗。

缺失较明显的 function groups：

- connection object accessors and buffer operations
- tcp server setup/accept/update interest/remove connection
- packet free / bytes free
- low-level encoder helpers
- topic matching helpers
- session manager CRUD
- broker callbacks and packet dispatcher

简要修复建议：

- function inventory 阶段继续拆细 implementation-needed helpers。
- 不要求机械达到 89 个，但每个 file spec 的 source interface 应足以让 coder 生成完整 C file，而不是只描述核心行为。
- 对复杂模块优先补足：
  - codec helpers
  - session manager
  - topic match/router
  - network epoll lifecycle

## 20. call contracts 使用 planning function ids，不是 C symbol contracts

**重要性：中。** 当前 `RELY.FUNC` 和 `CALL_CONTRACTS` 中常见 `func:validate_mqtt_string` 这类 planning id；coder 生成 C 代码需要真实 symbol name 和 signature。

示例 specs：

- `RELY.FUNC.NAME` 是可调用 C function symbol，例如 `read_string`、`mqtt_packet_free`。
- `CALL_CONTRACTS` 如有使用，应包含 `NAME`、`SIGNATURE` 等 coder schema 字段。

当前 planning specs：

- `callee_function_id` 不是直接可调用 symbol。
- call contract schema 与 coder schema 不一致。

简要修复建议：

- 在 compiler 中解析 function id 到 canonical C symbol。
- `RELY.FUNC.NAME` 只写 C symbol name。
- `CALL_CONTRACTS` 要么转换为 schema 允许的 `NAME/SIGNATURE/PARAMS/RETURN/OWNERSHIP/FAILURE`，要么暂时删除，避免 invalid。

## 21. traceability 保留方式破坏 strict schema

**重要性：中低。** 规划 agent 应保留 traceability，这是 SpecForge 的研究核心之一；但当前保留方式直接违反 schema。

简要修复建议：

- 短期：
  - 将 source fact ids 写入 `DOC_REF`。
  - 将 decision ids 写入 `CONSISTENCY_RULES` 或 role text。
- 中期：
  - 扩展 specs schema，正式增加 `TRACEABILITY`。
  - 同步更新 coder loader、validator、prompt builder。
- 不建议继续将 schema 不认识的 `TRACEABILITY` 直接写入 strict spec。

## 22. planning compiler 当前硬编码造成多处兼容性问题

**重要性：中低，但修复收益高。** 许多差异来自 `specs_compiler.py` 的局部硬编码。

当前关键问题位置：

- `_param_for_coder()`：没有 ownership enum normalization。
- `_interface()`：
  - `KIND` 写成 `FUNCTION`。
  - `FUNCTION_TYPE` 直接 `.upper()`。
  - `VISIBILITY` 直接 `.upper()`。
  - `CONTRACT` shape 使用 `INPUT/OUTPUT/ERROR`。
- `_function_spec()`：
  - `FUNCTION_TYPE` 直接 `.upper()`。
  - `body_key` 可能输出不合规 `EVENT`。
  - 直接写入 schema 不允许字段。
- `compile_spec_bundle()`：
  - `PROTOCOL.ROLES` 固定为 `["target"]`。
  - `ARTIFACTS` 写成 `FILE_SPEC`。
  - `HEADER.INTERFACE` 依赖 function visibility，但 visibility 可能不是 public，且 item shape 不合规。
  - `PUBLIC_SYMBOLS` 当前混入 string，但 schema 需要 object item。
  - file `ACCESS_PATHS` 使用 `FIELD_ID/SOURCE`，schema 不接受。

简要修复建议：

- 把 compiler 拆成几个小的 normalization helpers：
  - `normalize_function_type_for_coder`
  - `normalize_param_ownership_for_coder`
  - `normalize_interface_visibility_for_coder`
  - `normalize_data_visibility_for_coder`
  - `lower_contract_for_coder`
  - `lower_call_contract_for_coder`
  - `filter_or_map_traceability`
- 每个 helper 写单元测试。
- compiler 输出后立即跑 schema validation 和 coder loader validation。

## 推荐修复顺序

1. 加 schema validation 到 planning pipeline，先让不兼容显性失败。
2. 修 `specs_compiler.py` 的枚举与字段 shape：`KIND`、`FUNCTION_TYPE`、`VISIBILITY`、`OWNERSHIP`、`CONTRACT`、`EVENT/LOGIC`。
3. 移除或正式映射 schema 不允许字段：`TRACEABILITY`、`CAPABILITY_IDS`、`STATE_ACCESS`、`CALLS_ALLOWED`。
4. 补齐 `HEADER.INTERFACE` 和 top-level broker lifecycle API。
5. 将 `MODULES[].ARTIFACTS` 从 file pointers 改成 public `TYPE/FUNC` symbols。
6. 输出 `PROTOCOL` metadata：`MQTT`、`3.1.1`、`BROKER`。
7. 建立 shared public data model，尤其是 `mqtt_packet_t` 与 packet payload structs。
8. 修模块 dependencies 和 generation order。
9. 补齐 access paths 与 wire mapping 的交叉校验。
10. 增加最低限度 test vectors。
11. 将概念模块 lowering 到 coder 工程模块，或同步增强 coder 以支持当前模块风格。
12. 增加 end-to-end compatibility gate：schema validation -> coder loader -> header render -> main render -> generate dry-run。

## 建议新增的兼容性检查

后续 planning run 完成后应自动检查：

1. `jsonschema` strict validation：
   - module spec、file specs、function specs 全部通过。
2. coder loader validation：
   - `load_spec_bundle_from_root()` 无 error diagnostic。
3. public API validation：
   - 非 main file 的 `HEADER.INTERFACE` 不应全空。
   - top-level app/broker header 必须有 create/run/destroy 或 start/run/destroy。
4. artifact validation：
   - `MODULES[].ARTIFACTS` 只能是 `TYPE/FUNC/VAR/CONST/MACRO`。
5. trace validation：
   - 每个 function spec 的 parent trace id 存在对应 file spec。
6. dependency validation：
   - module dependencies 必须存在。
   - generation order 中 dependency 必须先于 dependent。
7. render smoke test：
   - `render_header()` 生成的 header 至少含 public declarations。
   - `render_main_c()` 不应调用 fallback 的不存在 symbol。
8. machine constraint validation：
   - `WIRE_MAPPING.TARGET` 必须存在于 public `ACCESS_PATHS`。
   - `CALL_CONTRACTS` signature 与 canonical signature 一致。

## 最短可行修复目标

如果目标是尽快让 planning specs 可以直接输入 coder，建议第一阶段不要追求完全超过示例 specs，而是达到以下最低标准：

- 35 个当前 spec 文件全部通过 strict schema。
- 每个 file spec 至少有合法 `SOURCE.INTERFACE`；public module 至少有 `HEADER.INTERFACE`。
- module spec 中 artifacts 全部是真实 public symbols。
- 有一个 broker/app public lifecycle API，可让 `main.c` 正确生成。
- `PROTOCOL.ROLES` 包含 `BROKER`。
- coder validate 无 error，header/main render smoke test 通过。

达到这些后，再逐步补 shared packet model、test vectors、wire mapping 与更细 function inventory。
