# Planning 产出 specs 缺陷报告

## 结论摘要

本报告记录两类已观察到的 downstream coder 失败根因：

1. MQTT planning 产出的 `spec_bundle` 虽然通过了当前 coder schema / loader / semantic validator，但仍包含会诱导 coder 生成不可编译 C 项目的规格缺陷。
2. CoAP 示例 specs 暴露出当前 spec schema 与 header lowering 能力不足，尤其是 C declarator、macro/const、system include 的表达能力缺口。

需要明确区分：此前的 header 被 repair 覆盖、9 MB compile error 被塞回 prompt、HTTP 400 重试放大，是 coder repair 流程缺陷；本文只记录“specs 本身提供给 coder 的信息不充分、不一致或不可编译”的缺陷。

## 证据路径

| 类型 | 路径 |
|---|---|
| MQTT 原始 planning specs | `agent/planning/out/mqtt/broker__c__linux_epoll__minimum_v1/20260609_214554_036173_t/spec_bundle/` |
| MQTT coder 生成工程 | `agent/out/mqtt_broker_20260610_102238/mqtt/` |
| MQTT 初次编译错误 | `agent/out/mqtt_broker_20260610_102238/_agent_logs/010_compile_stderr_0.txt` |
| CoAP 示例 specs | `specs-example/coap_specs/` |

## MQTT specs 缺陷

### M1. Public header 缺少 system include 依赖

`network.h` 暴露了 `ssize_t mqtt_network_read(...)` 和 `ssize_t mqtt_network_write(...)`，但对应 spec 没有足够信息让 deterministic header 生成 `<sys/types.h>` 或等价 typedef。

编译证据：

```text
./src/network/network.h:22:1: error: unknown type name 'ssize_t'; did you mean 'size_t'?
./src/network/network.h:23:1: error: unknown type name 'ssize_t'; did you mean 'size_t'?
```

规格证据：

- `src/network/network/network_spec.json` 中 public signature 使用 `ssize_t`。
- 当前 `HEADER.DEPENDENCY` 只表达项目内 header dependency，缺少 system include 的结构化表达。

影响：

- header 本身不可独立编译。
- coder repair 若未被限制，会误修 deterministic header。

建议：

- planning 输出应为 public signature 中的 non-ISO C / POSIX 类型生成显式 system include dependency。
- coder validator 应对每个 public header 做 `gcc -fsyntax-only`。

### M2. Private type 在 source 中存在，但 public/header 可见性不一致

编译错误显示 `mqtt_network_encode_buffer_t` 在 `network.c` 中被使用时未被声明：

```text
src/network/network.c:42:45: error: unknown type name 'mqtt_network_encode_buffer_t'
```

规格证据：

- `src/network/network/network_spec.json` 把 `mqtt_network_encode_buffer_t` 放在 `SOURCE.DATA`，visibility 为 private。
- 同一个 source 的 static helper `mqtt_network_encode_buffer_free(mqtt_network_encode_buffer_t* buf)` 依赖该类型。

可能根因：

- coder prompt 中 private `SOURCE.DATA` 与 function spec 的 rely/call contracts 没有形成足够强的“必须先定义 private type，再定义使用它的 helper”的约束。
- 当前 validator 只检查 public symbol / schema 兼容，不能发现 generated source 的 declaration-order 或 private type availability 风险。

建议：

- planning specs 对 private source-only types 增加明确的 declaration order 或 “must_define_before_use” 约束。
- coder source prompt 将 `SOURCE.DATA` 中的 private types 作为强制生成项，而不是背景信息。
- validator 至少对 deterministic header + generated source 执行 compile smoke。

### M3. `mqtt_connection_t` 与 `mqtt_network_t` 语义混淆

编译错误显示 coder 把 `struct mqtt_network* conn` 当作 connection object 使用：

```text
src/network/network.c:168:22: error: 'struct mqtt_network' has no member named 'closed'
src/network/network.c:172:13: error: 'struct mqtt_network' has no member named 'fd'
src/network/network.c:177:42: error: 'struct mqtt_network' has no member named 'recv_buf'
```

规格证据：

- network module 同时存在 `mqtt_network_t`、`mqtt_connection_t`、`struct mqtt_network` 等概念。
- 部分 internal helper signature 使用 `struct mqtt_network* conn`，例如 connection read/send/flush/close 路径。

问题判断：

- specs 没有稳定表达 module context type 与 connection handle type 的边界。
- helper 参数名 `conn` 与实际类型 `struct mqtt_network*` 冲突，诱导 coder 把 runtime context 当作 connection。

建议：

- planning 阶段应强制区分 `module_context_type`、`connection_handle_type`、`per_connection_state_type`。
- helper signature 中禁止使用与语义不一致的参数名，例如 `struct mqtt_network* conn`。
- validator 检查名称语义与 type role 的明显冲突：`conn/connection` 参数不应绑定到 module context struct。

### M4. Callback typedef 与调用形式不一致

编译错误：

```text
error: incompatible type for argument 1 of 'g_global_on_close'
note: expected 'mqtt_connection_t' but argument is of type 'mqtt_connection_t *'
```

规格层风险：

- callback type、registration function、handler call site 对 `mqtt_connection_t` 是 value 还是 pointer 没有统一。
- C 回调边界通常应使用 opaque pointer，但 specs 中存在 value/pointer 混用迹象。

建议：

- planning 对 callback typedef 增加 canonical signature source，并要求所有 register/call contract 引用同一签名。
- validator 检查 callback typedef 参数与 CALL_CONTRACTS / EVENT handler 参数的一致性。

### M5. Module dependency 与 call contracts 不一致

MQTT `mqtt_module_spec.json` 的 `GENERATION_ORDER` 为：

```json
["network", "protocol_codec", "timer", "session", "topic_router", "broker_app"]
```

但 module `DEPENDENCIES` 多处为空，而 function-level `CALL_CONTRACTS` 存在跨模块调用，例如 network handler 调用 topic router：

```json
"NAME": "mqtt_topic_router_subscribe"
"NAME": "mqtt_topic_router_route"
```

问题判断：

- module-level dependency graph 没有完整覆盖 function-level call graph。
- coder 只拿到局部依赖 header 时，可能无法获得跨模块类型和函数声明。
- 这类缺陷也会让 planning final report 出现“schema/loader passed，但 downstream codegen 不可编译”的假阳性。

建议：

- planning final validation 增加：每条跨模块 `CALL_CONTRACTS.NAME` 必须能映射到 exporting module，并反向更新 caller module `DEPENDENCIES` 与 file `SOURCE.DEPENDENCY`。
- 禁止 function-level call contract 指向未导入 header 的 public function。

### M6. Current validator 未覆盖 compile-level 可用性

当前 MQTT specs 通过了 coder compatibility，但 downstream 初次 make 直接失败。这说明 validator 覆盖的是 schema/load/public-symbol 级别，不覆盖：

- header 是否能独立编译；
- private source data 是否在 use 前定义；
- callback typedef 与 call site 是否一致；
- function-level call graph 是否反映到 module/file dependencies；
- type role 与 helper signature 是否语义一致。

建议：

- 在 planning final gate 中增加轻量 compile-oriented validation，不必调用 LLM：
  - render deterministic headers；
  - `gcc -fsyntax-only` 每个 header；
  - 检查 public signature 中所有 type 是否来自 primitive/system include/current header/imported header；
  - 检查 `CALL_CONTRACTS` 的 callee 是否可解析到本文件 static function 或 imported public function。

## CoAP specs 暴露出的 schema / lowering 缺陷

### C1. C array declarator 表达错误

CoAP specs 使用：

```json
{
  "NAME": "token",
  "TYPE": "uint8_t[COAP_MAX_TOKEN_LEN]"
}
```

当前 header renderer 采用 `TYPE NAME` 拼接，会生成非法 C：

```c
uint8_t[COAP_MAX_TOKEN_LEN] token;
```

正确形式应为：

```c
uint8_t token[COAP_MAX_TOKEN_LEN];
```

建议：

- schema 增加结构化字段，例如 `TYPE: "uint8_t"` + `ARRAY_LEN: "COAP_MAX_TOKEN_LEN"`。
- header renderer 不应要求 planning 把完整 C declarator 塞进 `TYPE` 字符串。

### C2. CONST/MACRO 信息不足

CoAP specs 中存在：

```json
{
  "NAME": "COAP_MAX_TOKEN_LEN",
  "KIND": "CONST"
}
```

但缺少 renderer 需要的 `TYPE` / `VALUE` 或 macro body，因此 header 不能稳定生成：

```c
#define COAP_MAX_TOKEN_LEN 8
```

建议：

- schema 允许 `CONST` / `MACRO` 携带 `VALUE`、`VALUE_TYPE`、`RENDER_AS`。
- planning 必须从 protocol facts 或 engineering decision 中写出常量值来源。

### C3. System include 无结构化表达

CoAP `udp_server` 需要 POSIX socket 类型和 API，但 specs 不能明确表达 `<sys/socket.h>` 等 system headers。

建议：

- file spec 增加 `SYSTEM_INCLUDE` 或 `HEADER.SYSTEM_DEPENDENCY`。
- validator 根据 public signatures 和 known POSIX symbols 检查 include coverage。

### C4. FORBIDDEN_SYMBOLS 覆盖不足

CoAP coder 曾生成不存在或不应使用的符号，例如：

- `COAP_OPTION_*`
- `COAP_METHOD_MASK_ALL`
- `coap_message_get_*`
- `coap_message_set_code`

问题判断：

- specs 对 allowed public API 的约束强于 forbidden API 的约束。
- 当 LLM 尝试补全“看起来合理”的协议 helper 时，缺少负约束阻止 hallucinated symbol。

建议：

- planning specs 为每个 module 生成 `FORBIDDEN_SYMBOLS`，覆盖已知不存在、未暴露、或被 scope 排除的接口。
- coder prompt 与 validator 同时使用 `FORBIDDEN_SYMBOLS`。

## 优先修复建议

### P0：让 planning final validation 能发现当前缺陷

- 增加 header syntax compile：render 每个 public header 后执行 `gcc -fsyntax-only`。
- 增加 public signature type resolution：每个 public signature 的 type 必须来自 primitive、当前 header data、imported header、system include。
- 增加 call contract resolution：`CALL_CONTRACTS.NAME` 必须可解析为本文件 private/static function 或 imported public function。
- 增加 callback signature consistency：typedef、register function、call contract、event handler 的参数必须一致。

### P1：补齐 spec schema 表达能力

- C array declarator：`TYPE + ARRAY_LEN`。
- CONST/MACRO：`VALUE` / `VALUE_TYPE` / `RENDER_AS`。
- system includes：区分项目 header dependency 与 system include。
- type role：明确 `module_context_type`、`connection_handle_type`、`per_connection_state_type`。

### P2：调整 planning prompt 与 normalizer

- 要求 planning 在 public signature 使用 POSIX 类型时补 system include。
- 要求 function-level cross-module call 自动回写 module/file dependencies。
- 禁止 helper signature 出现 type role 与 parameter name 明显冲突。
- 要求 callback typedef 成为唯一 canonical source，register/call contract 不得另写不一致签名。

## 与 coder repair 修复的边界

已实施的 coder repair P0 修复可以阻断以下放大问题：

- 不再 repair deterministic header；
- 不再把 `.c` implementation 写入 `.h`；
- compile diagnostics 进入 prompt 前会过滤和限流；
- 400 类不可恢复请求错误不再重复 retry。

但这些修复不会消除 specs 本身的缺陷。若 planning 继续产出缺失 system include、类型语义冲突、call dependency 不完整、array declarator 不可 lowering 的 specs，coder 会更早、更清晰地停止，而不是继续污染 header 或放大 prompt。

