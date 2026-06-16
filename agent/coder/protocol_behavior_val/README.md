# Protocol Behavior Validation

对生成出的协议实现进行运行时冒烟测试（smoke test），验证生成的二进制是否满足协议的**最小功能（min 版本）**行为预期。

四个协议（HTTP/1.1、CoAP、MQTT、SMTP）均为 min 版本，仅覆盖各协议核心功能的最小可用子集（transport、codec/parser、codec/serializer、handler、error、integration），不包含完整 RFC 的所有命令/选项/错误码。测试目标是验证生成的二进制"能跑通最基本的协议交互"，而非协议完备性。

## 架构

```
__init__.py    — 注册表 + 分发入口（verify_protocol_behavior）
common.py      — 共享工具：端口分配、服务启停、TCP 等待
mqtt.py        — MQTT broker 冒烟测试
coap.py        — CoAP server 冒烟测试
http.py        — HTTP/1.1 server 冒烟测试
smtp.py        — SMTP server 冒烟测试
```

`verifier.py` 中的 `ProjectVerifier.verify_behavior()` 根据协议 slug 分发到本包的 `verify_protocol_behavior()`，由 `__init__.py` 中的 `_RUNNERS` 字典路由到对应协议的 `run()` 函数。

此外，CLI 的 `test` 子命令可以直接调用 `verify_protocol_behavior()`，跳过结构校验和编译，仅运行行为测试：

```bash
python3 -m agent coder test --protocol mqtt --project-dir <dir> --binary mqtt_broker
```

## 当前支持的协议

| 协议 | slug | 测试文件 | 被测角色 | 场景数 |
|------|------|----------|----------|--------|
| HTTP/1.1 | `http` | `http.py` | server | 9 |
| MQTT | `mqtt` | `mqtt.py` | broker | 6 |
| CoAP | `coap` | `coap.py` | server | 6 |
| SMTP | `smtp` | `smtp.py` | server | 8 |

其他协议会被跳过，记录 `"no verifier for protocol '<slug>'"`。

---

## HTTP/1.1（min） — 9 个测试场景

| # | 场景名 | 测试内容 | 对应功能特性 |
|---|--------|----------|-------------|
| 1 | `http_tcp_connect` | 通过 TCP socket 连接到服务器端口，发送 GET 请求并接收响应 | transport — TCP bind/listen/accept/read/write/close |
| 2 | `http_get_file` | GET `/hello` 返回 200 及文件内容 | parser — 解析 request line（method、path、version） |
| 3 | `http_head_no_body` | HEAD `/hello` 返回 200，Content-Length 非零但 body 为空 | method handler — GET 和 HEAD |
| 4 | `http_404_not_found` | GET `/nonexistent` 返回 404 Not Found | router — 根据 path 分发，未知路径返回 404 |
| 5 | `http_400_bad_request` | 发送畸形请求（`GARBAGE\r\n\r\n`），返回 400 Bad Request | error handling — malformed request |
| 6 | `http_501_unknown_method` | 发送 DELETE 请求，返回 501 Not Implemented | error handling — unsupported method |
| 7 | `http_response_headers` | 验证响应包含 Content-Length、Content-Type、Connection: close 头 | response serializer — 生成 status line 及关键 header |
| 8 | `http_connection_close` | 验证服务器在响应后主动关闭 TCP 连接 | lifecycle — 单连接处理一个请求后关闭 |
| 9 | `http_smoke_test` | 连续发送 5 次 GET 请求，验证全部返回正确结果 | integration — main loop 稳定接收并处理请求 |

### 功能覆盖对照表

| 功能类别 | 必须实现 | 验证点 | 对应场景 |
|----------|---------|--------|---------|
| transport | TCP socket bind/listen/accept/read/write/close | 客户端能连接 8080 端口并收到响应 | #1 `http_tcp_connect` |
| parser | 解析 request line：method、path、version | 能识别 GET /hello HTTP/1.1 | #2 `http_get_file` |
| header parser | 解析 header 行，至少识别 Host 和 Connection | 请求可携带 Host、Connection 头且被正常处理 | #2, #7 |
| router | 根据 path 分发到 resource handler | `/hello` 返回固定文本，未知路径返回 404 | #2, #4 |
| response serializer | 生成 status line、Content-Length、Content-Type、Connection header 和 body | curl 能正确显示响应 | #7 |
| method handler | 支持 GET 和 HEAD | HEAD 返回 header，但不返回 body | #2, #3 |
| error handling | malformed request、unsupported method、unknown path | 分别返回 400、501 或 404 | #4, #5, #6 |
| lifecycle | 单连接处理一个请求后关闭 | Connection: close 行为稳定 | #8 |
| integration | main loop 接收连接并处理请求 | smoke test 可连续执行多次 | #9 |

### 被测服务器要求

- 服务器二进制接受端口号作为第一个参数，文档根目录作为第二个参数（如 `./http_server 8080 /var/www`）
- 服务器对每个请求回复 `Connection: close` 并关闭 TCP 连接
- 测试框架会自动创建临时目录作为文档根目录，并在其中写入 `hello` 测试文件

### 需要的第三方工具

无需第三方工具，全部 9 个场景仅使用 Python 标准库（`http.client`、`socket`、`tempfile`）。

---

## CoAP（min） — 6 个测试场景

| # | 场景名 | 测试内容 |
|---|--------|----------|
| 1 | `coap_get_hello` | 发送 CoAP GET `/hello`（消息 ID `0x1234`），验证响应类型为 ACK（2）、响应码为 2.05 Content（69）、消息 ID 和 token 匹配、payload 为 `"hello from CoAP server"`、且包含 Content-Format 选项（12） |
| 2 | `coap_unknown_not_found` | 发送 CoAP GET `/unknown`，验证返回 4.04 Not Found（132） |
| 3 | `coap_malformed_survival` | 发送垃圾 UDP 数据报后，验证服务端未崩溃且后续正常请求仍返回正确响应 |
| 4 | `coap_extended_option` | 发送带有合法扩展选项（delta > 12）的 CoAP 请求，验证服务器未将其误判为畸形包而拒绝 |
| 5 | `coap_smoke_test` | 连续发送 5 次 CoAP GET `/hello`，验证全部返回正确的 2.05 Content |
| 6 | `coap_client_interop` | 使用 `coap-client-notls` 请求 `coap://127.0.0.1:{port}/hello`，验证第三方客户端能正确解析响应并收到预期 payload |

### 需要的第三方工具

| 工具 | 用途 | 是否必需 |
|------|------|----------|
| 无 | 场景 1~5 为自包含测试，仅使用 Python 标准库 socket | 必需（内置） |
| `coap-client-notls` | 场景 6 互操作测试 | 可选，不可用时该场景跳过 |

安装方式（Ubuntu/Debian）：

```bash
sudo apt install libcoap3-bin
```

---

## MQTT（min） — 6 个测试场景

| # | 场景名 | 测试内容 |
|---|--------|----------|
| 1 | `mqtt_cross_client_pubsub` | 两个客户端（订阅者 + 发布者）分别 CONNECT，订阅者订阅 `a/#`，发布者向 `a/b` 发送 QoS0 PUBLISH，验证订阅者收到转发的消息 |
| 2 | `mqtt_buffer_before_eof` | 客户端发送 PUBLISH 后立即发送 DISCONNECT（`\xe0\x00`）并关闭连接，验证 broker 在处理对端关闭之前已处理缓冲的数据包 |
| 3 | `mqtt_malformed_survival` | 发送 5 字节畸形数据（`\xff\xff\xff\xff\xff`）后关闭连接，随后用新的客户端正常 CONNECT，验证 broker 未因恶意输入崩溃 |
| 4 | `mqtt_disconnect_handler` | publisher 发送 DISCONNECT 后被正确清理；新 publisher 可正常连接并转发消息给订阅者，验证 broker DISCONNECT handler 正常工作 |
| 5 | `mqtt_smoke_test` | 订阅 `smoke/#` 后循环 5 轮 pub/sub，每轮使用唯一 client ID 的 publisher，验证所有消息正确转发无丢失 |
| 6 | `mqtt_mosquitto_interop` | 使用 `mosquitto_sub` 订阅主题，`mosquitto_pub` 发布消息，验证与第三方 MQTT 客户端的互操作性 |

### 需要的第三方工具

| 工具 | 用途 | 是否必需 |
|------|------|----------|
| 无 | 场景 1~5 为自包含测试，仅使用 Python 标准库 socket | 必需（内置） |
| `mosquitto_sub` | 场景 6 订阅端 | 可选，不可用时该场景跳过 |
| `mosquitto_pub` | 场景 6 发布端 | 可选，不可用时该场景跳过 |

安装方式（Ubuntu/Debian）：

```bash
sudo apt install mosquitto-clients
```

---

## SMTP（min） — 8 个测试场景

| # | 场景名 | 测试内容 | 对应功能特性 |
|---|--------|----------|-------------|
| 1 | `smtp_tcp_connect` | TCP 连接后接收 220 服务问候 | transport — TCP listen/accept |
| 2 | `smtp_helo_ehlo` | 发送 HELO 命令，验证返回 250 | codec/parser — 解析 HELO 命令 |
| 3 | `smtp_mail_from` | 在 HELO 之后发送 MAIL FROM，验证返回 250 | session/state — 状态推进到 mail_from |
| 4 | `smtp_rcpt_to` | 在 MAIL FROM 之后发送 RCPT TO，验证返回 250 | session/state — 状态推进到 rcpt_to |
| 5 | `smtp_data_delivery` | 发送 DATA → 收到 354 → 发送邮件正文（以 `\r\n.\r\n` 结束）→ 收到 250 → QUIT → 221 | handler — DATA 命令 + body 收集 + codec/serializer |
| 6 | `smtp_bad_sequence` | 三组乱序命令（MAIL before HELO、DATA before MAIL、DATA before RCPT），验证均返回 503 | session/state — 命令顺序错误时返回 503 |
| 7 | `smtp_unknown_command` | 发送无法识别的命令 GARBAGE，验证返回 500/502，且服务端仍存活可继续 QUIT | error — 未知命令返回 500/502 |
| 8 | `smtp_smoke_test` | 完整 SMTP 事务（HELO → MAIL FROM → RCPT TO → DATA → QUIT）+ 验证 mail store 目录产生新文件 | integration — main loop + message store |

### 功能覆盖对照表

| 功能类别 | 必须实现 | 验证点 | 对应场景 |
|----------|---------|--------|---------|
| transport | TCP listen/accept；按 CRLF 读取命令行 | 客户端可建立 SMTP 会话并收到 220 问候 | #1 `smtp_tcp_connect` |
| codec/parser | 解析 HELO/EHLO、MAIL FROM、RCPT TO、DATA、QUIT | 命令识别正确并返回预期状态码 | #2, #3, #4, #5 |
| codec/serializer | 输出 SMTP 三位状态码和文本响应 | 返回码序列符合预期（220/250/354/221） | #1~#8 |
| session/state | 维护 SMTP transaction 状态：greeted、mail_from、rcpt_to、data_mode | 命令顺序错误时返回 503 | #6 `smtp_bad_sequence` |
| handler | 各命令 handler；DATA body 收集 | 能保存一封完整邮件 | #5 `smtp_data_delivery` |
| error | unknown command 返回 500/502；bad sequence 返回 503 | 错误路径可验证 | #6, #7 |
| integration | server main loop + message store | smoke test 后能查看已保存邮件 | #8 `smtp_smoke_test` |

### 被测服务器要求

- 服务器二进制接受端口号作为第一个参数，mail store 目录作为第二个参数（如 `./smtp_server 2525 /var/mail`）
- 测试框架会自动创建临时目录作为 mail store，并在 smoke test 中验证邮件文件已写入

### 需要的第三方工具

无需第三方工具，全部 8 个场景仅使用 Python 标准库（`socket`、`tempfile`）。

---

## 添加新协议

1. 在本目录新建 `<protocol>.py`，实现 `run(project_dir, binary_name, scenarios)` 函数和 `EXPECTED_SCENARIOS` 元组
2. 在 `__init__.py` 的 `_RUNNERS` 字典中注册：`"<slug>": <module>`
3. 测试场景以 dict 形式追加到 `scenarios` 列表：`{"name": "...", "status": "passed|failed|skipped", "detail": "..."}`
4. 若第三方工具不可用，将场景标记为 `"skipped"` 而非 `"failed"`
5. 更新本 README，添加新协议的测试场景文档
