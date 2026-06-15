# SpecForge Planning 稳定化总体任务描述

## 1. 总体目标

本轮工程任务服务于 SpecForge 的中期目标：

```text
让 planning 输出能稳定驱动 coder compile/smoke，
并在 MQTT、CoAP、SMTP 的最小功能集上复现。
```

本任务不以整体压缩 specs schema 为目标。当前路线是保留 coder-facing strict specs 主体，优先修复 planning 到 specs 的 deterministic lowering、dependency/type/signature/header closure 和 final readiness validation。

## 2. 总体验收标准

完成本计划后，应满足以下标准。

### 2.1 Planning readiness 标准

planning final success 必须意味着：

```text
spec_bundle schema valid
coder loader valid
rendered headers valid
dummy header translation units compile
public type refs closed
system type refs closed
header dependencies resolvable
source dependencies resolvable
call/dependency graph consistent
```

不允许出现：

```text
planning success
但 coder 阶段立即因为 deterministic header/type/include 错误失败
```

### 2.2 Coder compile/smoke 标准

对每个目标协议：

- MQTT minimum
- CoAP minimum
- SMTP minimum

至少应有一个 fresh planning output 驱动 coder：

```text
compile pass
minimum smoke pass
```

建议稳定性门槛：

```text
每个协议连续或近连续运行 3 次 planning；
至少 2/3 能生成通过 final readiness 的 spec_bundle；
通过 final readiness 的 spec_bundle 不应在 coder loader/header 阶段失败；
每个协议至少 1 个 run 完成 compile + smoke。
```

如果暂时无法达到 2/3，应至少满足：

```text
所有失败都有明确分类 diagnostic；
不存在 false success；
不存在 silent dependency fallback；
不存在 coder 阶段才暴露的 deterministic header closure failure。
```

### 2.3 Artifact 归档标准

每个协议成功样例必须保留：

```text
planning run directory
spec_bundle/
coder_manifest.json
planning_traceability.json
planning_decisions.json
planning_ir_refs.json
validation reports
token usage summary
coder output directory
compile logs
smoke logs
run summary
```

## 3. 任务分步计划

---

# Step 1：修复 P0 deterministic closure 与 readiness gate

## 3.1 任务目标

让 planning 的 final success 不再绕过 deterministic coder compatibility failure。重点修复：

```text
5.6_dependency_closure
specs_compiler
5.7_spec_readiness
coder_compat validator
```

## 3.2 主要任务

### 3.2.1 dependency fallback fail closed

检查并修改：

```text
agent/planning/stages/dependencies.py
agent/planning/stages/implementation_plan_merger.py
相关 dependency validation report 生成逻辑
```

要求：

- fallback 不允许清空 `calls_allowed`、`imports_allowed` 后让 empty graph passed。
- 如果 dependency 输入不足，应生成 blocking diagnostic。
- 如果 `call_contracts` 非空但无法派生 call edges，不得 silent pass。
- 如果 `signature_dependencies` 非空但 imports/header deps 为空，不得 silent pass。

完成指标：

```text
存在单元测试或最小 fixture：
- call_contracts 非空 + calls_allowed 空 -> fail
- signature_dependencies 非空 + imports_allowed 空 -> fail
- fallback 清空边 -> fail
```

### 3.2.2 分离 header_public_deps 与 source_call_deps

检查并修改：

```text
agent/planning/stages/specs_compiler.py
agent/planning/stages/coder_spec_lowering.py
```

要求：

- `HEADER.DEPENDENCY` 只来自 public ABI/type closure。
- `SOURCE.DEPENDENCY` 来自 implementation calls、source-only deps 和 source include needs。
- 不得继续从同一个 `imports_allowed` 同时生成 header/source dependency。
- 输出 diagnostic 说明每个 dependency 的来源。

完成指标：

```text
对一个含 public external type 的 fixture：
- HEADER.DEPENDENCY 包含 provider header
- SOURCE.DEPENDENCY 不被错误提升为 HEADER.DEPENDENCY

对一个只在 source 调用外部函数的 fixture：
- SOURCE.DEPENDENCY 包含 callee provider
- HEADER.DEPENDENCY 不包含无关 source-only header
```

### 3.2.3 public type dependency lowering

要求从以下位置提取 public type refs：

```text
public function signatures
callback signatures
struct fields
typedef / alias
function pointer params
public header interfaces
```

并将 non-system external type 映射到 provider header。

完成指标：

```text
fixture 中 header A 的 public signature 引用 header B 的 type：
- A.HEADER.DEPENDENCY 包含 B
- rendered header validation pass

callback typedef 参数引用 external type：
- provider dependency 被 lower
- dummy header TU compile pass
```

### 3.2.4 system type registry

建立 deterministic registry，例如：

```text
size_t -> <stddef.h>
ssize_t -> <sys/types.h>
uint8_t / uint16_t / uint32_t / uint64_t -> <stdint.h>
bool -> <stdbool.h>
sockaddr / sockaddr_in -> <sys/socket.h>, <netinet/in.h>
time_t -> <time.h>
```

要求：

- registry 应集中定义，避免散落在 prompt 或 ad hoc renderer 中。
- `HEADER.SYSTEM_DEPENDENCY` 由 compiler deterministic lower。
- rendered header 中出现 system type 时必须能包含相应 system header。

完成指标：

```text
fixture 中 public signature 使用 ssize_t：
- HEADER.SYSTEM_DEPENDENCY 包含 sys/types.h
- rendered header validation pass
- dummy header TU compile pass
```

### 3.2.5 final rendered header validation

检查并修改：

```text
agent/planning/validators/coder_compat.py
agent/planning/validators/coder_semantics.py
agent/coder/specs.py
```

要求：

- final planning validation 不得使用弱化的 `validate_rendered_headers=False` 作为 success gate。
- 至少提供 strict mode：
  ```text
  load_spec_bundle_from_root(validate_rendered_headers=True)
  ```
- rendered header errors 必须成为 blocking diagnostics。

完成指标：

```text
构造缺失 ssize_t include 的 spec_bundle：
- planning final validation fail
- diagnostic 指向具体 header 和 missing system type
```

### 3.2.6 dummy header translation unit compile

要求：

- 对每个 rendered header 生成最小 `.c` 文件：
  ```c
  #include "path/to/header.h"
  int main(void) { return 0; }
  ```
  或不含 main 的 compile-only translation unit。
- 使用当前项目 include path 编译。
- 失败时 blocking。

完成指标：

```text
每个 FILE_SPEC.HEADER.PATH 均被编译检查
compile command 记录在 validation report
失败时 report 包含 header path、stderr、include path
```

## 3.3 Step 1 验收命令建议

根据仓库实际 CLI 调整，建议至少运行：

```bash
python -m compileall agent/planning agent/coder tools
python -m pytest <新增或相关测试>
python -m agent planning validate --facts <mqtt_facts> --target-profile <mqtt_profile>
python -m agent planning plan --facts <mqtt_facts> --target-profile <mqtt_profile>
```

如 planning 成功，继续：

```bash
python -m agent.coder --spec-root <planning_run>/spec_bundle --output-dir <coder_out> generate
```

## 3.4 Step 1 完成判定

必须同时满足：

```text
1. 新增 fail-closed dependency tests 通过。
2. system type fixture 通过 rendered header validation。
3. external public type fixture 通过 rendered header validation。
4. final planning readiness 启用 strict rendered header gate。
5. 旧 MQTT planning sample 的 header closure failure 不再被 final success 漏掉。
6. 当前任务状态文件已更新。
```

---

# Step 2：修复 cross-layer dependency consistency validator

## 4.1 任务目标

确保 planning 中多个 dependency/call 表达不会互相漂移。需要跨层检查：

```text
calls_allowed
call_contracts
imports_allowed
signature_dependencies
dependency_graph
RELY.FUNC
CALL_CONTRACTS
HEADER.DEPENDENCY
SOURCE.DEPENDENCY
MODULES[].DEPENDENCIES
```

## 4.2 主要任务

### 4.2.1 call graph consistency

规则：

```text
call_contracts 非空时，calls_allowed 不得全空。
RELY.FUNC 引用 callee 时，callee 必须存在。
RELY.FUNC / CALL_CONTRACTS / calls_allowed 至少应能解释 dependency_graph.function_edges。
dependency_graph.function_edges 不得引用 unknown function。
```

完成指标：

```text
fixture:
- call_contracts 非空 + calls_allowed 空 -> blocking
- RELY.FUNC unknown callee -> blocking
- dependency edge unknown callee -> blocking
```

### 4.2.2 imports/dependency consistency

规则：

```text
signature_dependencies 非空时，imports_allowed/header deps/source deps 不得无解释地全空。
SOURCE.DEPENDENCY 引用的 header 必须在 generated header path index 中存在。
HEADER.DEPENDENCY 引用的 header 必须在 generated header path index 中存在。
MODULES[].DEPENDENCIES 引用的 module 必须存在。
```

完成指标：

```text
fixture:
- unknown module dependency -> blocking
- unknown header path -> blocking
- signature dependency external type but no header dep -> blocking
```

### 4.2.3 diagnostic report

输出 machine-readable report：

```json
{
  "status": "passed|failed",
  "errors": [],
  "warnings": [],
  "dependency_sources": [],
  "call_edge_sources": [],
  "header_dep_sources": [],
  "source_dep_sources": []
}
```

完成指标：

```text
每个 blocking error 至少包含：
- code
- stage
- file/function/type/module
- source fields
- suggested repair direction
```

## 4.3 Step 2 完成判定

```text
1. cross-layer consistency validator 已接入 5.7_spec_readiness 或 final validation。
2. 相关 fixture tests 通过。
3. MQTT planning run 不再出现 call_contracts 非空但 function_edges=0 且 passed。
4. validation report 可以解释每条 dependency/call edge 来源。
5. 当前任务状态文件已更新。
```

---

# Step 3：修复 file layout 与 runtime architecture mapping

## 5.1 任务目标

让 planning architecture、file layout、runtime entrypoint 和 dependency closure 相互兼容。目标不是复制 gold layout，而是让 target profile 下的 protocol roles 可解释、可编译、可运行。

## 5.2 主要任务

### 5.2.1 role-to-file mapping explanation

每个核心 role 必须映射到明确 module/file：

```text
codec/parser/serializer
session/state
transport/network
router/topic/resource
broker/app/server
timer/lifecycle
error handling
runtime entrypoint
```

完成指标：

```text
planning report 中每个 required role 都有：
- owning module
- owning file
- exported public API if needed
- dependency relationship
- missing/merged role explanation
```

### 5.2.2 generated header path resolvability

规则：

```text
所有 HEADER.DEPENDENCY quoted includes 必须能在 current spec_bundle header index 中解析。
所有 SOURCE.DEPENDENCY headers 必须能解析。
不允许依赖 gold/example 中存在但当前 planning layout 中不存在的 header path。
```

完成指标：

```text
fixture:
- dependency 指向 missing header -> blocking
- dependency 指向 existing generated header -> pass
```

### 5.2.3 runtime entrypoint dependency closure

规则：

```text
main.c / runtime entrypoint 只能调用已经存在的 public lifecycle API。
entrypoint 不应直接持有协议内部复杂逻辑。
entrypoint 所需 module dependency 必须闭合。
```

完成指标：

```text
entrypoint references:
- all functions exist
- all functions public or explicitly entrypoint-visible
- required headers in SOURCE.DEPENDENCY
```

### 5.2.4 module dependency target existence

规则：

```text
MODULES[].DEPENDENCIES 中所有 module 必须存在。
merged role 必须显式说明，例如 topic_router 合并 topic/router。
```

完成指标：

```text
unknown module dependency -> blocking
merged role without explanation -> warning 或 blocking，视 target profile 要求决定
```

## 5.3 Step 3 完成判定

```text
1. file layout validator 接入 readiness。
2. role-to-file mapping report 生成。
3. header path resolvability test 通过。
4. runtime entrypoint closure test 通过。
5. MQTT fresh planning run 的 module/file/header dependency 可解释。
6. 当前任务状态文件已更新。
```

---

# Step 4：修复 type inventory 与 function signature closure

## 6.1 任务目标

在 dependency/header readiness 稳定后，修复 `5.3_type_data` 和 `5.4b_function_signatures` 的 public ABI 质量，使 public types、callbacks、manager/session/router/network types 和 function signatures 可闭合、可渲染、可调用。

## 6.2 主要任务

### 6.2.1 public type obligation coverage

根据 protocol role 和 target profile 派生 required type categories，而不是复制 gold type names。

示例类别：

```text
connection/session context
protocol message/packet
parser/decoder state
encoder buffer
router/topic/resource store
server/broker context
transport connection
callback table
timer/lifecycle handle
error/result type
```

完成指标：

```text
每个 required type category：
- covered by existing type，或
- explicitly marked not required with reason，或
- unresolved assumption
```

### 6.2.2 public signature type-ref closure

规则：

```text
每个 public signature 中的 non-system type 必须满足：
- same header declared
- HEADER.DEPENDENCY provider visible
- valid forward declaration policy
```

完成指标：

```text
fixture:
- public signature external type + provider header -> pass
- public signature unknown type -> blocking
- public signature private type leak -> blocking
```

### 6.2.3 callback/function pointer closure

规则：

```text
callback typedef 的 return type 和 param types 参与 type closure。
function pointer fields 参与 type closure。
callback struct fields 参与 type closure。
```

完成指标：

```text
callback typedef 使用 external connection type：
- provider dependency lower 成功
- rendered header compile pass
```

### 6.2.4 stale type ref rejection

规则：

```text
signature、behavior、access path、call contract 中引用未声明或已删除 type，应 blocking。
public API 不得引用其他 module private type。
```

完成指标：

```text
fixture:
- stale type ref -> blocking
- cross-module private type ref -> blocking
```

## 6.3 Step 4 完成判定

```text
1. public type obligation report 生成。
2. signature type-ref closure validator 接入 5.4b 或 5.7。
3. callback closure tests 通过。
4. stale/private type ref tests 通过。
5. MQTT fresh planning run 不再出现 rendered header unknown public type。
6. 当前任务状态文件已更新。
```

---

# Step 5：优化 semantic actionability

## 7.1 任务目标

在 closure/readiness 稳定后，再提升 5.4a-5.4e 的语义质量。重点不是增加字段数量，而是让行为、wire/access、call contracts 与 declared types/functions/resources 绑定。

## 7.2 主要任务

### 7.2.1 function family obligations

按协议最小功能和 role 派生函数族 obligations。

通用函数族：

```text
init/create/destroy lifecycle
parse/decode
serialize/encode
validate
handle/request/command
state transition
send/receive transport
error handling
timer/timeout
integration entrypoint
```

完成指标：

```text
每个 required family：
- 至少一个 function 覆盖，或
- explicit not required reason，或
- unresolved assumption
```

### 7.2.2 behavior action binding

规则：

```text
behavior ACTION 不应只是自然语言。
必须绑定至少一种 declared implementation object：
- state/resource
- source data
- access path
- callable function
- error/result type
- wire field
```

完成指标：

```text
fixture:
- behavior action references unknown field/helper -> blocking 或 repairable diagnostic
- behavior action binds declared state/access/callee -> pass
```

### 7.2.3 wire/access closure

规则：

```text
每个 required wire field:
- bound to valid ACCESS_PATH，或
- explicit skip/reject with reason
```

完成指标：

```text
wire mapping target unknown -> blocking
required wire field no binding and no skip reason -> blocking
```

### 7.2.4 call contract precision

规则：

```text
CALL_CONTRACTS callee 必须存在。
callee signature 必须匹配。
cross-module call 必须被 module boundary 允许。
call failure/ownership 约束应与 callee contract 兼容。
```

完成指标：

```text
unknown callee -> blocking
signature mismatch -> blocking
forbidden cross-module call -> blocking
```

### 7.2.5 test vector anchoring

最小功能 smoke 所需 test vectors 应能从 planning test plan lower 到 strict specs 或 validator-sidecar，并被 coder prompt 或 smoke harness 使用。

完成指标：

```text
每个协议 minimum smoke feature 至少有一条 test vector / scenario anchor。
删除 test vector 不应是默认优化方向。
```

## 7.3 Step 5 完成判定

```text
1. function family obligation validator 接入。
2. behavior action binding validator 接入。
3. wire/access closure validator 接入。
4. call contract precision validator 接入。
5. MQTT fresh planning output 的 behavior/call/wire 字段能解释到 declared artifacts。
6. 当前任务状态文件已更新。
```

---

# Step 6：MQTT / CoAP / SMTP 最小功能复现

## 8.1 任务目标

在完成前五步修复后，将流程扩展到三个协议的最小功能集，验证 planning 输出可以稳定驱动 coder compile/smoke。

## 8.2 协议最小功能建议

### MQTT minimum

建议覆盖：

```text
TCP listen/accept
CONNECT parse
CONNACK serialize
PINGREQ parse
PINGRESP serialize
DISCONNECT handling
basic session/context lifecycle
basic error handling
```

不覆盖：

```text
QoS 1/2
retain
will message
topic wildcard matching
persistent session
TLS/auth
full publish/subscribe semantics
```

### CoAP minimum

建议覆盖：

```text
UDP receive/send
CoAP header parse
GET request parse
2.05 Content response serialize
basic path option handling
message id/token echo
malformed request rejection
```

不覆盖：

```text
blockwise transfer
observe
DTLS
full option registry
proxying
resource discovery full semantics
```

### SMTP minimum

建议覆盖：

```text
TCP listen/accept
220 greeting
HELO/EHLO basic handling
MAIL FROM
RCPT TO
DATA
message body termination with <CRLF>.<CRLF>
250 / 354 / 500 style responses
QUIT
basic session state
```

不覆盖：

```text
TLS STARTTLS
AUTH
MIME parsing
mail relay
DNS/MX
queue persistence
spam filtering
advanced extensions
```

## 8.3 每个协议执行流程

每个协议按相同步骤执行：

```text
1. 准备或确认 protocol_facts minimum。
2. 准备 target_profile。
3. 运行 planning。
4. 检查 final validation report。
5. 检查 spec_bundle schema/load/rendered header。
6. 运行 coder。
7. 检查 compile。
8. 运行 minimum smoke。
9. 归档 artifacts。
10. 记录 failure taxonomy。
```

## 8.4 多协议完成指标

对每个协议：

```text
1. planning run 至少成功生成一个 strict-readiness-passed spec_bundle。
2. spec_bundle 通过 schema、loader、rendered header、dummy header TU。
3. coder compile pass。
4. minimum smoke pass。
5. 所有 artifacts 归档。
```

整体完成指标：

```text
MQTT: compile + smoke pass
CoAP: compile + smoke pass
SMTP: compile + smoke pass

且：
- 不存在 false planning success。
- 不存在 silent dependency fallback。
- 不存在 coder 阶段才暴露的 deterministic header closure failure。
- failure diagnostics 可用于 RQ3 validator evaluation。
```

---

# Step 7：稳定性回归与论文实验准备

## 9.1 任务目标

把修复后的 planning pipeline 变成可用于实验的稳定 baseline。

## 9.2 主要任务

### 9.2.1 repeated-run stability

每个协议运行至少 3 次 planning + coder。

记录：

```text
planning success rate
strict readiness pass rate
coder compile success rate
smoke success rate
repair iterations
downstream error count
token usage
```

完成指标：

```text
每个协议至少 2/3 strict readiness pass。
每个协议至少 1 个 compile + smoke pass。
所有失败均分类。
```

### 9.2.2 failure taxonomy

分类建议：

```text
schema_error
loader_error
rendered_header_error
dummy_header_compile_error
dependency_closure_error
type_closure_error
signature_closure_error
file_layout_error
behavior_actionability_error
wire_access_error
call_contract_error
coder_source_compile_error
smoke_behavior_error
llm_json_error
llm_candidate_semantic_error
```

完成指标：

```text
每个 failed run 都能归入一类或多类 failure taxonomy。
```

### 9.2.3 evaluation artifact manifest

为论文实验准备统一 manifest：

```json
{
  "protocol": "",
  "run_id": "",
  "facts_path": "",
  "target_profile_path": "",
  "planning_run": "",
  "spec_bundle": "",
  "coder_run": "",
  "planning_status": "",
  "readiness_status": "",
  "compile_status": "",
  "smoke_status": "",
  "repair_iterations": 0,
  "failure_categories": [],
  "notes": ""
}
```

完成指标：

```text
MQTT / CoAP / SMTP 的 successful runs 均有 manifest。
failed runs 也有 manifest。
```

## 9.3 Step 7 完成判定

```text
1. 三协议 repeated-run 结果完成。
2. 成功与失败 artifacts 归档。
3. 统一 manifest 生成。
4. 可直接进入 RQ1/RQ3 实验设计。
5. 当前任务状态文件更新为中期目标完成或列出剩余 blocker。
```

## 10. 每轮 Codex 会话结束时必须更新的内容

每次会话结束前，更新当前任务状态文件中的以下字段：

```text
当前步骤
本轮目标
已修改文件
已新增测试
已运行命令
通过的检查
失败的检查
新增 diagnostics
新增风险
下一轮入口
是否达到当前步骤完成指标
```

## 11. 总体 Do-Not-Do List

1. 不要整体压缩 strict specs。
2. 不要关闭 compatibility validation。
3. 不要清空 dependency graph 制造成功。
4. 不要让 LLM 直接生成 final dependency graph。
5. 不要复制 gold implementation detail 作为 protocol fact。
6. 不要为了单协议通过引入协议外硬编码。
7. 不要跳过当前任务状态文件更新。
