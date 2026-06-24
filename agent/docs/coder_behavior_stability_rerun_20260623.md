# coder 行为稳定性复测报告

> 生成日期：2026-06-23
> 范围：CoAP、HTTP/1.1、MQTT、SMTP 四份 `specs-example` 协议 specs
> 目的：使用当前版本 coder 串行生成四份协议实现，并在每份协议生成后执行对应协议行为测试，观察 coder 稳定性

---

## 一、结论摘要

本轮实验没有进入 LLM 代码生成、编译修复或协议行为测试阶段。四份协议 specs 在 `generate` 命令的前置 spec validation 阶段全部被 error 级 diagnostics 阻断，因此没有生成新的 project_dir、binary、`run_manifest.json` 或 `behavior_verification.json`。

这次结果与 2026-06-16 的上一次行为测试实验不可直接比较：上次实验已经生成并编译出协议实现，失败集中在 HTTP 与 SMTP 的 runtime behavior；本次实验的主要问题提前到 coder 输入校验层，属于当前 coder 与示例 specs 之间的契约不一致问题。

总体判断：

| 协议 | generate 结果 | 是否生成 binary | 独立 behavior test | 主要阻断原因 |
|------|---------------|-----------------|--------------------|--------------|
| CoAP | 失败，exit 1 | 否 | 未执行，无 binary | `CALL_CONTRACTS` 与 `RELY.FUNC` 不一致 |
| HTTP | 失败，exit 1 | 否 | 未执行，无 binary | `CALL_CONTRACTS` drift + 函数签名不一致 |
| MQTT | 失败，exit 1 | 否 | 未执行，无 binary | `CALL_CONTRACTS` 与 `RELY.FUNC` 不一致 |
| SMTP | 失败，exit 1 | 否 | 未执行，无 binary | `CALL_CONTRACTS` 与 `RELY.FUNC` 不一致 |

---

## 二、实验方法

在 `/home/ljf/SpecForge` 下按协议串行执行：

```bash
python3 -u -m agent coder --spec-root /home/ljf/SpecForge/specs-example/coap_specs --output-dir /home/ljf/SpecForge/agent/out/behavior_stability_20260623_coap generate
python3 -u -m agent coder --spec-root /home/ljf/SpecForge/specs-example/http_specs --output-dir /home/ljf/SpecForge/agent/out/behavior_stability_20260623_http generate
python3 -u -m agent coder --spec-root /home/ljf/SpecForge/specs-example/mqtt_specs --output-dir /home/ljf/SpecForge/agent/out/behavior_stability_20260623_mqtt generate
python3 -u -m agent coder --spec-root /home/ljf/SpecForge/specs-example/smtp_specs --output-dir /home/ljf/SpecForge/agent/out/behavior_stability_20260623_smtp generate
```

由于四条命令全部在 validation 阶段中止，`agent/out/behavior_stability_20260623_*` 下没有新生成的协议工程。独立的 `coder test` 子命令需要已编译 binary，因此本轮没有对旧 binary 进行替代测试，以避免把历史产物误记为本次生成结果。

---

## 三、validation diagnostics 统计

| 协议 | error | warning | diagnostics 分类 |
|------|-------|---------|------------------|
| CoAP | 31 | 0 | `call_contract_rely_drift`: 31 |
| HTTP | 50 | 0 | `call_contract_rely_drift`: 34；`header_source_function_signature_mismatch`: 14；`signature_raw_structured_mismatch`: 2 |
| MQTT | 48 | 0 | `call_contract_rely_drift`: 48 |
| SMTP | 43 | 12 | `call_contract_rely_drift`: 43；`missing_wire_mapping_test_vectors`: 12 |

关键现象：

- CoAP、MQTT 的阻断原因非常集中，均为 `CALL_CONTRACTS` 与 `RELY.FUNC` 名称集合不一致。
- SMTP 的 error 级阻断同样集中在 `call_contract_rely_drift`，另有 12 个 `WIRE_MAPPING` 缺少 `TEST_VECTORS` anchor 的 warning。
- HTTP 除 `call_contract_rely_drift` 外，还有较多函数签名不一致，说明 HTTP specs 自身存在更明确的结构性漂移。

---

## 四、问题分析

### 4.1 全局阻断：`CALL_CONTRACTS` 与 `RELY.FUNC` 的语义不一致

当前 validator 在 `agent/coder/specs.py` 中要求每个 Function Spec 的 `CALL_CONTRACTS` callee name 集合与 `RELY.FUNC` name 集合完全相等；只要不相等，就记录 error 级 `call_contract_rely_drift` 并阻断 generation。

抽样结果显示，现有 specs 中 `RELY.FUNC` 更像“函数依赖全集”，而 `CALL_CONTRACTS` 更像“需要显式传递返回值/副作用语义的关键 callee 子集”：

| 样例 | `RELY.FUNC` | `CALL_CONTRACTS` |
|------|-------------|------------------|
| CoAP `main` | `coap_server_create`, `coap_server_start`, `coap_server_run`, `coap_server_destroy` | 空 |
| MQTT `main` | `mqtt_broker_create`, `mqtt_broker_start`, `mqtt_broker_run`, `mqtt_broker_destroy` | 空 |
| HTTP `serve_file` | 8 个依赖函数，包括 `http_resolve_path`, `http_stat_path`, `http_response_send` 等 | 仅 `http_stat_path` |
| SMTP `queue_code` | `smtp_response_format`, `queue_raw` | 仅 `smtp_response_format` |

因此这里有两种可能解释：

1. 如果当前设计要求 `CALL_CONTRACTS` 是 exhaustive contract list，那么四份示例 specs 都没有完成新 schema 迁移，planning agent/spec 数据需要补齐每个 `RELY.FUNC` 的调用契约。
2. 如果当前设计允许 `CALL_CONTRACTS` 只覆盖语义敏感 callee，那么 coder validation 把“子集关系”升级成“集合相等”过严，导致本可生成的 specs 被前置阻断。

从稳定性角度看，这是 coder 流程层面的输入契约兼容性问题，不是 LLM 生成随机性导致的行为不稳定。它会让 behavior-level 稳定性实验完全无法启动。

### 4.2 HTTP 特有问题：函数签名在 File Spec 与 Function Spec 之间漂移

HTTP specs 还存在 14 个 `header_source_function_signature_mismatch` 和 2 个 `signature_raw_structured_mismatch`。这些更偏向 spec 信息自身的不一致，validator 在这里给出 error 是合理的。

典型样例：

| Trace ID | File `SOURCE.INTERFACE` | Function `SIGNATURE.RAW` |
|----------|--------------------------|---------------------------|
| `http/network/tcp_server/set_nonblocking` | `static int set_nonblocking(int fd)` | `static bool set_nonblocking(int fd)` |
| `http/network/tcp_server/add_client` | `static http_client_node_t* add_client(http_tcp_server_t* server, int fd)` | `static int add_client(http_tcp_server_t* server, int fd)` |
| `http/server/http_server/send_error` | `static void send_error(http_session_t* session, int status_code)` | `static int send_error(http_server_t* server, http_session_t* session, int code)` |
| `http/server/http_server/serve_file` | `static void serve_file(http_server_t* server, http_session_t* session, const char* resolved_path, bool is_head)` | `static int serve_file(http_server_t* server, http_session_t* session, const http_request_t* req, bool head_only)` |
| `http/server/http_server/on_accept_cb` | `static void on_accept_cb(void* user, int fd)` | `static void on_accept_cb(void* user, int fd, const struct sockaddr_storage* peer, socklen_t peer_len)` |

另两个 `signature_raw_structured_mismatch` 与 varargs 表达有关：

- `http_response_send_html` 的 `SIGNATURE.RAW` 包含 `...`，但 structured `PARAMS` 未表达 varargs。
- `queuefv` 的 `SIGNATURE.RAW` 是 `...`，File Spec 中却是 `va_list args`，Function Spec structured `PARAMS` 也缺少 varargs 表达。

这些问题应归为 planning/spec 信息质量问题：同一个函数的接口在 File Spec、Header/SOURCE interface、Function Spec 三处没有形成单一事实源。

### 4.3 SMTP warning：`WIRE_MAPPING` 缺少 `TEST_VECTORS`

SMTP 有 12 个 `missing_wire_mapping_test_vectors` warning。它们不是本轮中止的直接原因，但说明部分 parser/handler 的 wire-level 映射缺少可执行测试锚点。对 planning agent 来说，这会削弱 coder 对协议文本格式、状态推进和边界条件的约束。

这些 warning 应归为 spec 完备性问题，而非 coder 流程 bug；但它们会增加后续通过 behavior test 的不确定性。

---

## 五、与上一次行为问题的关系

上一次报告中的 HTTP 404/501/header 问题与 SMTP DATA/QUIT 问题都发生在“代码已生成且可运行”之后。本轮没有产生新代码，因此无法判断这些 runtime behavior 缺陷是否已经被当前 coder 修复、缓解或替换为新问题。

本轮真正暴露的是更前置的稳定性风险：

- coder validation 与示例 specs 的 schema/契约版本不同步；
- `CALL_CONTRACTS` 的设计语义没有在 planning 输出与 coder 校验之间统一；
- HTTP specs 中仍有接口签名的多源漂移。

这意味着当前版本 coder 的端到端稳定性瓶颈已经从“生成后行为偏差”前移到“生成前输入契约失败”。

---

## 六、归因结论

| 问题 | 归因 | 说明 |
|------|------|------|
| 四协议全部 `call_contract_rely_drift` | coder/spec 契约不一致 | 若 `CALL_CONTRACTS` 必须 exhaustive，则 specs 未迁移；若只应是关键契约子集，则 validator 过严 |
| HTTP 函数签名不一致 | spec 信息问题 | File Spec 与 Function Spec 对返回值、参数、callback 形态的描述不一致 |
| HTTP varargs structured mismatch | spec schema 表达问题 | `...` 与 structured `PARAMS` 缺少统一表示 |
| SMTP `WIRE_MAPPING` 无 `TEST_VECTORS` | spec 完备性问题 | 不阻断生成，但会削弱协议行为约束 |
| 本轮未执行 behavior test | coder 流程结果 | 无新 binary，不能对本次生成产物做协议行为验证 |

---

## 七、建议下一步

1. 先明确 `CALL_CONTRACTS` 的规划语义：是 `RELY.FUNC` 的完整调用契约列表，还是仅覆盖关键 callee 的增强语义信息。
2. 如果要求完整列表，应由 planning agent 或迁移脚本补齐四份 specs 的 `CALL_CONTRACTS`，并确保每个 callee 的 signature、return convention、side effects 可供 coder prompt 使用。
3. 如果允许关键子集，应将 `call_contract_rely_drift` 调整为 warning，或改为校验 `CALL_CONTRACTS ⊆ RELY.FUNC` 与 `CALL_CONTRACTS` callee 可解析，而不是要求集合相等。
4. 修正 HTTP specs 中 File `SOURCE.INTERFACE`、Header interface 与 Function `SIGNATURE` 的单一事实源，尤其是 `send_error`、`serve_file`、callback 形态和 varargs 表达。
5. 在 validation 全部通过后，再重复本次四协议串行生成实验，并将 behavior result 与 2026-06-16 的 HTTP/SMTP runtime 问题逐项对照。
