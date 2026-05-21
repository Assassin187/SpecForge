# Planning Specs 与示例 Specs 差距分析

生成时间：2026-05-20

本文记录当前 planning 流程产出的 MQTT `spec_bundle` 与现有示例 specs 之间的差异，并按对“能否直接输入 coder 生成可编译 protocol implementation”的影响程度排序。目标是作为后续修复 `planning agent -> coder agent` 兼容性的持久性记忆。

## 对比对象

- 当前 planning 输出：
  `/home/ljf/SpecForge/agent/planning/out/mqtt/broker__c__linux_epoll__minimum_v1/20260521_154436_105118/spec_bundle`
- 上一轮 planning 输出：
  `/home/ljf/SpecForge/agent/planning/out/mqtt/broker__c__linux_epoll__minimum_v1/20260521_122233_184179/spec_bundle`
- 历史 planning 输出：
  `/home/ljf/SpecForge/agent/planning/out/mqtt/broker__c__linux_epoll__minimum_v1/20260521_111408_138774/spec_bundle`
- 历史 planning 输出：
  `/home/ljf/SpecForge/agent/planning/out/mqtt/broker__c__linux_epoll__minimum_v1/20260520_205352_313760/spec_bundle`
- 示例 specs：
  `/home/ljf/SpecForge/specs-example/mqtt_specs`
- coder schema：
  `/home/ljf/SpecForge/specs-example/specs_schema`
- 当前 specs compiler：
  `/home/ljf/SpecForge/agent/planning/stages/specs_compiler.py`

## 总体结论

### 2026-05-21 16:13 最新产物复核

复核对象：

`/home/ljf/SpecForge/agent/planning/out/mqtt/broker__c__linux_epoll__minimum_v1/20260521_154436_105118/spec_bundle`

本轮 Public Type Lowering & Header Type Completeness 修复后，最新 bundle 已经解决上一轮最突出的问题：public functions 已经不再引用“header 中完全不可见”的 `struct fixed_header*`、`struct connect*`、`struct subscribe*`、`struct publish*`。这些 expected public type roles 已经被 compiler 消费并 lowering 到 `codec_framing_spec.json` 的 public `HEADER.DATA`，同时进入 `mqtt_module_spec.json` 的 `codec_framing.ARTIFACTS`。

最新核心统计：

| 项目 | 2026-05-21 12:22 | 2026-05-21 15:44 |
|---|---:|---:|
| `FILE_SPEC` | 4 | 4 |
| `FUNCTION_SPEC` | 26 | 30 |
| schema invalid files | 0 | 0 |
| schema errors | 0 | 0 |
| loader errors | 0 | 0 |
| planning coder semantic errors | 0 | 0 |
| `HEADER.INTERFACE` 总数 | 3 | 8 |
| `SOURCE.INTERFACE` 总数 | 26 | 30 |
| public source functions | 3 | 8 |
| private source functions | 23 | 22 |
| module artifacts | 7 | 16 |
| module artifact `FUNC` | 3 | 8 |
| module artifact `TYPE` | 4 | 8 |
| public canonical `TYPE_SPEC` | 0 | 4 |
| `unresolved_lowering` | 未记录 type 缺失 | 0 |

已解决或进一步改善的 gap：

- Public header 可编译性明显提升。`codec_framing_spec.json` 现在有 8 个 public header functions：`decode_fixed_header`、`decode_connect`、`decode_subscribe`、`decode_publish`、`encode_connack`、`encode_suback`、`encode_pingresp`、`encode_publish`。
- 上一轮缺失的 public signature types 已进入 `HEADER.DATA`：`fixed_header`、`connect`、`subscribe`、`publish` 均有 public `TYPE_SPEC: STRUCT`。当前 public signatures 中出现的非内建 refs 都能解析到同文件 public `HEADER.DATA` 或 runtime/builtin types。
- `expected_public_type_roles` 已被真正消费。`planning_decisions.json` 中 `codec_framing.public_api_policy.expected_public_type_roles` 仍为 `type:fixed_header`、`type:connect`、`type:subscribe`、`type:publish`，并新增/保留了 `resolved_public_type_roles`，全部解析到对应 public type。
- `MODULES[].ARTIFACTS` 更有用。`codec_framing.ARTIFACTS` 已从上一轮 3 个 `FUNC` 扩展为 8 个 `FUNC` + 5 个 `TYPE`，其中真实 public API types 包括 `fixed_header`、`connect`、`subscribe`、`publish`，不再只有 opaque module handle。
- semantic gate 没有对当前 bundle 报错是合理结果。`planning_ir_refs.json` 的 `unresolved_lowering=[]`，`014_planning_validation_report.json` 显示 `status=success`、`coder_schema_status=passed`、`coder_loader_status=passed`、`error_count=0`、`warning_count=0`。
- strict schema 与 coder loader 兼容继续保持：schema invalid files、schema errors、loader errors 均为 0。

仍未解决的 gap：

- Gap 3 仍未解决：本轮没有生成 `main.c`，也没有 broker/app lifecycle public API；这符合本轮非目标，但仍影响 coder 直接生成 runnable broker。
- Gap 4、17 仍未解决：`mqtt_module_spec.json` 中 `MODULES[].DEPENDENCIES` 仍为空。public type 可见性已改善，但模块依赖关系仍没有显式暴露给 coder。
- Gap 15 仍未根治：wire mapping / semantic target 聚合问题不属于本轮修复范围，当前仍需要后续单独处理。
- Gap 16 仍未解决：仍没有 `TEST_VECTORS`。
- Gap 19 只改善了一部分：function specs 从 26 增至 30，public API 从 3 增至 8，但整体 function inventory 仍远少于示例 specs，connection/session/topic/router/broker app helper 族仍不足。
- `role_composition` 仍然没有 files/artifacts。这一模块目前不会破坏 public type lowering，但对最终 runnable composition 仍是后续问题。

新增观察：

- 当前 type lowering 是针对现有 canonical type table 的 deterministic consumption，不是 protocol-specific hardcode。从产物看，`fixed_header/connect/subscribe/publish` 的出现来自 `canonical_types` 和 `public_api_policy.expected_public_type_roles`，不是由 compiler 猜 MQTT 类型。
- Opaque module handles 仍作为 public `TYPE` artifacts 存在：`mqtt_codec_framing_t`、`mqtt_transport_runtime_t`、`mqtt_semantic_state_t`、`mqtt_resource_routing_t`。这本身可接受，但后续如果 coder 需要实例化或管理这些 handles，还需要 lifecycle/API ownership 继续补齐。
- Public canonical structs 已生成，但字段语义仍偏基础 lowering。它们解决了 header type declaration completeness，不等于已经补齐完整 shared packet/data model。

### 2026-05-21 15:15 最新产物复核

复核对象：

`/home/ljf/SpecForge/agent/planning/out/mqtt/broker__c__linux_epoll__minimum_v1/20260521_122233_184179/spec_bundle`

本轮 public API signal 修复后，旧的 P0 现象已经明显改善：最新 bundle 不再是 `HEADER.INTERFACE=0`，也不再是所有 source functions 都 private。`codec_framing` 已经通过 5.3 `public_api_policy`、5.4a function visibility/exported signal、5.5 file exports、specs compiler lowering 生成了 public header declarations 和 module `FUNC` artifacts。

最新核心统计：

| 项目 | 2026-05-21 11:14 | 2026-05-21 12:22 |
|---|---:|---:|
| `PROTOCOL_MODULE_SPEC` | 1 | 1 |
| `FILE_SPEC` | 5 | 4 |
| `FUNCTION_SPEC` | 28 | 26 |
| schema invalid files | 0 | 0 |
| schema errors | 0 | 0 |
| loader errors | 0 | 0 |
| planning coder semantic errors | 0 | 0 |
| `HEADER.INTERFACE` 总数 | 0 | 3 |
| `SOURCE.INTERFACE` 总数 | 28 | 26 |
| public source functions | 0 | 3 |
| module dependencies | 0 | 0 |
| module artifacts | 5 | 7 |
| module artifact `FUNC` | 0 | 3 |
| module artifact `TYPE` | 5 | 4 |
| 有 `TEST_VECTORS` 的 spec | 0 | 0 |
| 有 `ACCESS_PATHS` 的 spec | 3 | 4 |
| 有 `WIRE_MAPPING` 的 function spec | 2 | 0 |

已解决或进一步改善的 gap：

- Gap 2 的 P0 现象部分解决：`HEADER.INTERFACE` 不再全空。`codec_framing_spec.json` 现在有 3 个 public functions：`decode_fixed_header`、`encode_connect`、`encode_publish`。
- Gap 5 明显改善：`MODULES[].ARTIFACTS` 不再只有 opaque `TYPE`，`codec_framing.ARTIFACTS` 已包含 3 个 `FUNC` artifact，且没有发现 `FILE_SPEC` artifact。
- Step 5 public API signal 已经贯通一段：`planning_decisions.json` 中 `codec_framing.public_api_policy.exposes_public_api=true`，其 expected public function roles 被 lowering 到 public `HEADER.INTERFACE`。
- strict schema、coder loader、planning validation report 继续通过。`014_planning_validation_report.json` 显示 `status=success`、`coder_schema_status=passed`、`coder_loader_status=passed`、diagnostics 为 0。
- 其他模块不暴露 public API 已经有结构化 reason：`transport_runtime`、`semantic_state`、`resource_routing`、`role_composition` 均为 `exposes_public_api=false`，并带 `no_public_api_reason`。

仍未解决的 gap：

- Gap 12、13 仍是当前最关键残留：public functions 的 signatures 暴露了 public-ish C types，但这些 types 没有进入 `HEADER.DATA`。例如：
  - `decode_fixed_header(... struct fixed_header* out_header, ...)`
  - `encode_connect(const struct connect* msg, ...)`
  - `encode_publish(const struct publish* msg, ...)`
  但 public `HEADER.DATA` 只有 `mqtt_codec_framing_t`、`mqtt_transport_runtime_t`、`mqtt_semantic_state_t`、`mqtt_resource_routing_t` 这些 opaque module handles，没有 `struct fixed_header`、`struct connect`、`struct publish` 或等价 `TYPE_SPEC`。
- Gap 3 仍未解决：没有 `main.c`，没有 broker/app lifecycle public API。`role_composition` 仍在 `GENERATION_ORDER` 末尾，但 `FILES=[]`、`ARTIFACTS=[]`、没有 public entrypoint。
- Gap 4、17 仍未解决：`mqtt_module_spec.json` 中 `MODULES[].DEPENDENCIES` 仍全为空；dependency validation report 虽然有 derived edge count，但 module spec 没把依赖暴露给 coder。
- Gap 15 仍未根治：implementation plan 里 `WIRE_MAPPING`/`ACCESS_PATHS` 仍有明显语义问题，`ACCESS_PATHS.c_type` 大量为 `unknown`，多个 field mapping 被聚合到 `access:fixed_header:packet_type`。
- Gap 16 仍未解决：没有 `TEST_VECTORS`。
- Gap 19 仍未解决：function 数量从 28 降到 26，仍远少于示例 specs 的 89 个，connection/session/topic/router/broker app helper 族仍不足。

新增或暴露的新问题：

- Semantic gate 对 expected public type roles 仍有盲区。`codec_framing.public_api_policy.expected_public_type_roles` 明确列出 `fixed_header`、`connect`、`subscribe`、`publish`，但最终没有对应 public `HEADER.DATA`/`ARTIFACTS`，validation report 仍为 0 error。
- Public API 已经从“完全没有”变成“有函数但缺类型”。这比旧 P0 前进一步，但对 C header renderer 来说仍可能生成不可编译 header，因为 public declarations 引用了未声明 struct。
- `planning_decisions.json` 的 shape 变成 `items` map，而不是之前分析脚本预期的 `modules/functions` lists。当前 semantic gate 能通过，但后续工具和人工分析需要统一读取 sidecar 的实际结构，避免误判 public intent count 为 0。
- `SOURCE.INTERFACE` 中 `semantic_state` 的 `dispatch_publish_in` 等仍是 `FUNCTION_TYPE: ENTRYPOINT`，但 visibility 是 private/internal，且没有 header declaration。这不一定违反本轮 public API policy，但会继续削弱 coder 对 runnable broker boundary 的发现能力。
- `canonical_types` 已经有 `fixed_header/connect/subscribe/publish`，但 specs compiler 没有把它们 lowering 到 public `HEADER.DATA`。下一轮应优先打通 `expected_public_type_roles -> canonical_types -> HEADER.DATA TYPE_SPEC -> MODULES[].ARTIFACTS TYPE -> public signature type validation`。

下一轮优先级建议：

1. 收紧 semantic gate：`expected_public_type_roles` 非空时，必须有对应 public `HEADER.DATA` 或 module `TYPE` artifact；public function signature 引用的 `struct X` 必须在 header 可见。
2. 修 specs compiler 的 public type lowering：从 `canonical_types` deterministic 生成 schema-compatible `HEADER.DATA` `TYPE_SPEC`，不要在 compiler 里用协议名、模块名、函数名猜类型。
3. 保持本轮边界：不要为了补 public types 引入 MQTT/broker/main 特例；broker lifecycle、`main.c`、test vectors、wire mapping 语义可作为后续独立轮次处理。

### 2026-05-21 11:14 最新产物复核

本轮 specs compiler 与 compatibility validation 修改后，最新 bundle 继续保持 strict schema 和 loader 兼容，并且通过新增的 planning-side coder semantic gate。需要注意的是：semantic gate 通过不等于 public API gap 已解决，因为当前 `SOURCE.INTERFACE` 里没有任何 function 被标注为 `public`，所以 gate 没有看到“应公开但未进 header”的结构化信号。

最新与示例 specs 的核心统计：

| 项目 | 示例 specs | 2026-05-20 20:53 | 2026-05-21 11:14 |
|---|---:|---:|---:|
| `PROTOCOL_MODULE_SPEC` | 1 | 1 | 1 |
| `FILE_SPEC` | 11 | 4 | 5 |
| `FUNCTION_SPEC` | 89 | 30 | 28 |
| schema invalid files | 0 | 0 | 0 |
| schema errors | 0 | 0 | 0 |
| loader errors | 0 | 0 | 0 |
| planning coder semantic errors | 示例不适用 | 4 | 0 |
| `HEADER.INTERFACE` 总数 | 57 | 0 | 0 |
| `SOURCE.INTERFACE` 总数 | 89 | 30 | 28 |
| public source functions | 58 | 0 | 0 |
| module dependencies | 8 | 0 | 0 |
| module artifacts | 31 | 4 | 5 |
| module artifact `FUNC` | 18 | 0 | 0 |
| module artifact `TYPE` | 13 | 4 | 5 |
| 有 `TEST_VECTORS` 的 spec | 1 | 0 | 0 |
| 有 `ACCESS_PATHS` 的 spec | 4 | 3 | 3 |
| 有 `WIRE_MAPPING` 的 function spec | 1 | 2 | 2 |

已解决或进一步改善的 gap：

- Gap 1：strict schema dialect 兼容继续保持。最新 5 个 `FILE_SPEC`、28 个 `FUNCTION_SPEC`、1 个 `PROTOCOL_MODULE_SPEC` 均 schema-valid，loader errors 为 0。
- Gap 5：`MODULES[].ARTIFACTS` 不再出现 `FILE_SPEC`，并且现在由 strict header data 抽取，每个模块至少有一个 public opaque handle type artifact。这比上一版的 artifacts 语义更稳定，但还不够有用。
- Gap 6、11：function type lowering 有进展。最新不再全部降为 `ALGORITHM`，出现 2 个 `ENTRYPOINT` 和 1 个 schema-valid `EVENT`，其中 `handle_epoll_event` 带完整 `TRIGGER/PRECONDITION/INPUT/ACTION/STATE_CHANGE/RESPONSE/EVENT_TYPE`。
- Gap 3 的一小部分：`role_composition` 不再是空模块，现在有 `role_composition.h/c` 和两个 source functions：`route_client_message_to_broker`、`bind_broker_response_to_client`。
- Gap 7、8、9、10、14：枚举、interface kind、ownership、contract shape、`ACCESS_PATHS` shape 仍保持 schema-compatible。

仍未解决的 gap：

- Gap 2：`HEADER.INTERFACE` 仍为 0。所有 28 个 functions 仍只在 `SOURCE.INTERFACE`，且 visibility 全为 `private`。coder header renderer 仍不会得到任何 public function declaration。
- Gap 3：仍没有可用 app/main lifecycle。`role_composition` 虽然不再为空，但没有 public API，也没有 `main.c`，因此还不能形成 runnable broker/application boundary。
- Gap 4、17：模块边界和 layout 仍是概念模块：`transport_runtime`、`codec_framing`、`semantic_state`、`resource_routing`、`role_composition`；依赖仍全为空，没有对齐示例的 `network/protocol/session/topic/router/broker_app` 工程依赖链。
- Gap 12、13：shared public data model 仍缺失，且比上一版更“干净但更空”。最新 `HEADER.DATA` 只有 5 个 opaque module handles，没有 `mqtt_packet_t`、payload structs、fixed header/connect/subscribe/publish public type model。
- Gap 15：`WIRE_MAPPING` 语义问题仍在。`decode_fixed_header_spec.json` 仍把 CONNECT/SUBSCRIBE/PUBLISH 多个 fields 的 `TARGET` 聚合到 `fixed_header.packet_type`，schema 合法但协议语义错误。
- Gap 16：仍没有 `TEST_VECTORS`。
- Gap 18：`PROTOCOL.SPEC_VERSION` 仍是 `unspecified`。`PROTOCOL.NAME` 最新为 facts 原值 `mqtt`，与示例 `MQTT` 不一致；这是移除协议特例后的结果，不是 schema 错误，但会影响与示例 dialect 的一致性。
- Gap 19：function 数量仍不足，最新 28 个，比示例 89 个少很多，也比上一版 30 个少 2 个；缺少 connection/session/topic/router 等工程 helper 族。

新增或暴露的新问题：

- Compatibility gate 出现“无 public signal 即通过”的盲区：最新 bundle 的 semantic errors 为 0，但这是因为没有任何 `SOURCE.INTERFACE` 被标为 public，不代表 header public API 已满足。后续需要在 Step 5 prompt/schema 中强制 public API ownership 标注，或让 validation 对 `public_api_policy.exposes_public_api` 但无 public FUNC 的模块报 warning/error。
- Artifacts 现在语义一致但信息量不足：5 个 modules 的 artifacts 都只有 `mqtt_<module>_t` opaque TYPE，没有任何 `FUNC`，不能达到“关键函数和结构清单”的设计目标。
- `ENTRYPOINT`/`EVENT` function 仍是 private source-only：`accept_new_connection`、`close_connection`、`handle_epoll_event` 的 function spec 类型更准确，但 file spec 没有把它们公开或纳入 artifacts，coder 无法从 module boundary 发现它们。
- `PROTOCOL.NAME` 从 `MQTT` 变成 `mqtt`，说明 protocol metadata lowering 已经协议无关，但缺少 canonical display-name normalization。建议后续从 `protocol_facts.protocol_meta` 增加规范名字段，而不是在 compiler 中恢复 MQTT 特例。
- 示例 specs 在新增 semantic gate 下会报 artifacts 不全，这说明当前 gate 是针对新生成 bundle 的更强一致性检查；如果要把示例 specs 也作为 semantic baseline，需要先更新示例 `MODULES[].ARTIFACTS`，或把该检查限定为 planning-generated bundles。

### 2026-05-20 20:53 新版复核

第五阶段提示词与 specs compile 重构后，当前 bundle 已经解决了最底层的 coder schema dialect 问题：`agent.coder.specs.load_spec_bundle_from_root()` 可加载，且 4 个 `FILE_SPEC`、30 个 `FUNCTION_SPEC`、1 个 `PROTOCOL_MODULE_SPEC` 全部通过 `specs_schema` strict validation。旧版 35 个 invalid spec / 632 个 schema errors 已清零。

但这次修复主要解决了“schema 合法性”，还没有解决“能让 coder 生成 runnable broker”的工程完整性。最关键的残留问题是所有 `HEADER.INTERFACE` 仍为空，30 个 functions 全部只出现在 `SOURCE.INTERFACE` 且 `VISIBILITY: private`，因此 header 没有 public API，`main.c`/broker lifecycle 仍无从绑定。

新版与示例 specs 的核心统计：

| 项目 | 示例 specs | 旧 planning specs | 当前 planning specs |
|---|---:|---:|---:|
| `PROTOCOL_MODULE_SPEC` | 1 | 1 | 1 |
| `FILE_SPEC` | 11 | 5 | 4 |
| `FUNCTION_SPEC` | 89 | 29 | 30 |
| schema invalid files | 0 | 35 | 0 |
| schema errors | 0 | 632 | 0 |
| loader diagnostics | 0 | 未统计 | 0 |
| `HEADER.INTERFACE` 总数 | 57 | 0 | 0 |
| `SOURCE.INTERFACE` 总数 | 89 | 29 | 30 |
| public source functions | 58 | 0 | 0 |
| module dependencies | 8 | 0 | 0 |
| module artifacts | 31 | 5 | 4 |
| 有 `TEST_VECTORS` 的 spec | 1 | 0 | 0 |
| 有 `ACCESS_PATHS` 的 spec | 4 | 31 | 3 |
| 有 `WIRE_MAPPING` 的 function spec | 1 | 29 | 2 |

已解决或基本解决的 gap：

- Gap 1：严格 schema dialect 不兼容。已解决；planning 专用字段已移到 `planning_decisions.json`、`planning_traceability.json`、`planning_ir_refs.json` sidecar，核心 specs 不再混入 schema 禁止字段。
- Gap 5：`MODULES[].ARTIFACTS` 使用 `FILE_SPEC` kind。已解决 schema 违规；当前 artifacts 只保留 `TYPE`，但数量和语义仍不足。
- Gap 6：function type 枚举未归一化。已解决 schema 层问题；当前全部 lowering 为 `ALGORITHM`。
- Gap 7：visibility 枚举大小写和取值不兼容。已解决 schema 层问题；data 使用 `PUBLIC/PRIVATE`，interface 使用 `private`。
- Gap 8：interface `KIND` 使用 `FUNCTION`。已解决；当前 `SOURCE.INTERFACE.KIND` 全部为 `FUNC`。
- Gap 9：parameter ownership 枚举不兼容。已解决；当前只出现 `BORROWED`、`OWNED_BY_CALLER`、`OWNED`。
- Gap 10：`SOURCE.INTERFACE.CONTRACT` shape 不兼容。已解决；当前 contract 使用 `PRECONDITION/POSTCONDITION/IDEMPOTENT/THREAD_SAFETY`。
- Gap 11：`EVENT` body shape 不兼容。已规避；当前没有 `EVENT` function，全部用 `ALGORITHM + LOGIC`，schema 合法但损失了 handler/event 语义。
- Gap 14：`ACCESS_PATHS` 格式不兼容。已解决格式问题；当前 items 使用 `PATH/TYPE/ROLE`，但覆盖质量仍不足。
- Gap 18：`PROTOCOL` metadata 的 `NAME` 和 `ROLES`。已部分解决；当前为 `NAME: MQTT`、`ROLES: [BROKER]`，但 `SPEC_VERSION` 仍是 `unspecified`。

仍未解决的 gap：

- Gap 2、3：仍缺 public API 和可用 broker/app 入口；当前没有 `main.c`，没有 `mqtt_broker_create/start/run/stop/destroy`，`role_composition` 仍是空 files 模块。
- Gap 4、17：模块仍停留在概念层，且 layout 仍是 `mqtt/<module>/<module>.h/c`，没有对齐示例的 `network/protocol/broker/topic/router/main` 工程边界。
- Gap 12、13：shared public data model 仍不够；当前只有 opaque module handles 与 `fixed_header/connect/subscribe/publish` 这类裸 type 名，缺少 `mqtt_packet_t`、payload structs、session/router/network public types。
- Gap 15、16、19：wire mapping、test vectors、function 粒度仍明显不足；尤其 `WIRE_MAPPING` 虽然 schema 合法，但 semantic target 质量有新风险。

新增问题：

- `role_composition` 出现在 `GENERATION_ORDER` 中，但 `FILES: []`、`ARTIFACTS: []`、`DOC_REF: []`，作为最后模块会继续误导 coder 的 app 模块发现逻辑。
- 所有 functions 被强制降级为 private `ALGORITHM`，解决了 schema validation，却删除了 public API、entrypoint 与 event handler 信号；这是从“非法但有意图”变成“合法但不可调用”。
- `WIRE_MAPPING` 出现错误 target 聚合：例如 `decode_fixed_header_spec.json` 中多个 CONNECT/SUBSCRIBE/PUBLISH fields 都写到 `fixed_header.packet_type`，虽然 schema 合法，但协议语义错误。
- `ACCESS_PATHS` 的 `TYPE` 多为 `unknown`，且没有绑定到 declared public `TYPE_SPEC`，coder 仍难以生成可共享的 C struct 访问路径。
- sidecar trace ids 出现重复 `mqtt/mqtt/...` 前缀；不影响 coder loader，但会削弱 planning traceability 与人工调试可读性。

# 初始差距分析

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
