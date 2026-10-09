# RQ1 四协议行为范围、独立测试场景与异常／边界分类

整理日期：2026-10-09（UTC+8）。本文对应 SpecForge 与 MetaGPT 三轮四协议对比中的 MQTT、CoAP、HTTP/1.1、SMTP 功能子集。行为范围依据共同输入的 `TASK.md` 和 `REQUIREMENTS.md`；场景依据四个独立验收器的 `TEST_IDS`、`SCENARIOS` 及实际断言。两套系统使用同一场景清单；独立验收结果不反馈给生成或内部修复。

本文是对已有测试的事后描述性分类，未新增测试、重跑验收或修改交付。分类依据测试内容，不依赖某个系统是否通过。实验结果见 [SpecForge 与 MetaGPT 对比表](specforge_metagpt_comparison.md)。

## 1. 共同实验范围及计数规则

四项任务均要求独立、多文件的 Linux C99 项目，使用 GCC 与 make 构建，交付实现、头文件、Makefile、README、开发测试及规定名称的可执行文件。范围由编号需求选择，协议规则依据随任务提供的标准原文。公共类型、接口、所有权和处理路径需在实现前规划；不引入现有协议服务器作为实现，HTTP 任务还明确排除现有 HTTP 解析库。验收使用本机回环网络，普通构建与 ASan/UBSan 构建分别执行固定套件。本文的异常与边界场景通过率取普通独立验收，以便与主表的场景通过率、完整任务成功使用相同模式。

| 协议 | 规则来源及任务角色 | 启动约定 | 每次全部场景数 | 每次异常／边界场景数 | 三轮异常／边界计划项数 |
| --- | --- | --- | --- | --- | --- |
| MQTT 3.1.1 | MQTT 3.1.1 标准；TCP broker | `./mqtt_broker <port>` | 16 | 11 | 33 |
| CoAP | RFC 7252；IPv4 UDP server | `./coap_server <port>` | 10 | 7 | 21 |
| HTTP/1.1 | RFC 9112 / RFC 9110；IPv4 TCP origin server | `./http_server <port>` | 13 | 11 | 33 |
| SMTP | RFC 5321；IPv4 TCP 本地邮件捕获 server | `./smtp_server <port> <mail-dir>` | 13 | 11 | 33 |
| 合计 | 每轮四项独立任务 | — | 52 | 40 | 120 |

分类标记如下：

- **A（异常）**：场景显式检查畸形或不支持的输入、非法命令顺序、错误回复政策，或存储失败后的结果与状态。
- **B（边界）**：场景显式检查零长度输入／字段、长度或编码界点、匹配结构的临界情况、分片／合并输入、EOF／终止、重复请求，以及连接／事务生命周期边界上的状态清理和隔离。
- **A+B**：同一场景同时含上述两类断言。计数时仍只计一个场景。
- **常规**：检查合法输入的基本业务路径、常规格式兼容或外部客户端互通；普通二进制内容保真、大小写兼容和正常转义本身不单独作为异常／边界依据。

只要场景含明确的 A 或 B 断言，就纳入该指标，即使同一场景还检查普通功能。以下各行的分类依据指出实际触发条件。分类单位是验收器中的完整场景 ID，不是报文数、请求数或断言数；场景内部任一必需断言失败，该场景即不通过。

异常与边界场景通过率 = 全部尝试中通过的 A／B／A+B 场景数 ÷ 全部尝试中计划的 A／B／A+B 场景数。分母包含所有生成尝试；构建失败、规定运行文件缺失、未运行或环境阻塞均不计通过。总体使用通过项数与计划项数分别求和，不对协议百分比取简单平均。每种构建模式分别计数，不把普通与 sanitizer 结果合并成双倍分母。

前次讨论的 32 类场景是初步分组。逐项核定后补入 8 类已有明确断言的场景：MQTT 的 `mqtt_disconnect_handler`、`mqtt_exact_binary_payload`；CoAP 的 `coap_binary_put_get`；HTTP 的 `http_value_put_get_contract`、`http_post_echo_both_framings`、`http_head_semantics`；SMTP 的 `smtp_greeting_helo_ehlo`、`smtp_command_case_and_ehlo_reset`。正式统计采用本文件的 40 类清单和三轮 120 项分母。

## 2. MQTT 3.1.1 TCP broker

输入：[TASK.md](../../cases/mqtt_min/TASK.md)、[REQUIREMENTS.md](../../cases/mqtt_min/REQUIREMENTS.md)。独立验收器：[mqtt_check.py](../../evaluation/mqtt_check.py)。需求编号均指该协议自己的 REQUIREMENTS。

### 2.1 采用的行为范围

- R02–R03：匿名 CONNECT，非空 ASCII Client ID、Clean Session=1、无 Will；CONNECT 必须首先出现，重复 CONNECT 关闭该连接。支持一项或多项 QoS 0 订阅，SUBACK 回显非零 Packet Identifier，并按过滤器顺序返回结果。
- R04–R06：QoS 0 发布与跨客户端路由；保持报文、主题和任意二进制／空载荷。主题与过滤器区分大小写；支持 `+`、`#`、空层级和 `#` 的零层级匹配。
- R07–R09：区分不完整、完整及无效报文，并记录消费字节数；按序处理分帧、合帧和 EOF 前已缓冲的完整帧；支持 PINGREQ/PINGRESP，并继续处理后续业务流量。
- R10–R12：DISCONNECT／TCP 断开后清理订阅与连接状态；畸形报文只影响出错连接，其他客户端继续服务；明确缓冲区及连接资源所有权，正常 SIGINT/SIGTERM 退出无 ASan、UBSan 或泄漏诊断。

验收客户端使用匿名、Clean Session=1、无 Will、QoS 0、RETAIN=0。排除 QoS 1/2、保留消息、Will、持久会话、UNSUBSCRIBE、认证、TLS、完整 MQTT 合规性及空闲 keepalive 超时调度；PING 交换属于范围内行为。

### 2.2 独立行为测试场景

| 场景 ID | 实际检查的行为 | 主要关联需求 | 分类及依据 |
| --- | --- | --- | --- |
| `mqtt_cross_client_pubsub` | 一客户端订阅、另一客户端发布，收到准确主题与载荷 | R03–R04 | 常规：基本跨客户端发布订阅 |
| `mqtt_buffer_before_eof` | 最后一次 PUBLISH 后立即写侧 shutdown，订阅者仍收到已缓冲报文 | R07–R08 | B：完整输入与 EOF 的交界 |
| `mqtt_malformed_survival` | 不完整／畸形 CONNECT 使该客户端关闭，随后正常发布订阅仍可用 | R02、R11 | A：畸形输入拒绝及服务继续可用 |
| `mqtt_disconnect_handler` | 一订阅者 DISCONNECT 后，其他发布者与订阅者仍能完成路由 | R04、R10 | B：连接终止不破坏其他连接的状态 |
| `mqtt_smoke_test` | 四轮正常 CONNECT、SUBSCRIBE、PUBLISH、DISCONNECT | R02–R04、R10 | 常规：重复执行合法业务循环 |
| `mqtt_mosquitto_interop` | 使用 mosquitto_sub/pub 以 MQTT 3.1.1、QoS 0 完成文本载荷传递 | R02–R04 | 常规：外部客户端互通 |
| `mqtt_connect_semantics` | CONNECT 前发送 PINGREQ、已连接客户端重复 CONNECT 均关闭；新客户端仍可连接 | R02、R11 | A：非法连接状态／命令顺序 |
| `mqtt_suback_contract` | 多过滤器订阅回显 `0x1245`，有序返回 QoS 0 结果，并验证每项路由 | R03–R04 | 常规：合法多过滤器订阅与确认 |
| `mqtt_exact_binary_payload` | 大小写不同主题不匹配；精确保留 NUL、高位字节及空载荷 | R04–R06 | B：显式零长度载荷 |
| `mqtt_plus_filter_boundaries` | `+` 匹配一个层级，包括空层级；不匹配缺失或额外层级 | R05 | B：空层级及单层匹配的结构界点 |
| `mqtt_hash_filter_boundaries` | `#` 匹配零层级及多层级，不匹配相似前缀的另一个主题层级 | R05 | B：零层级及前缀／层级边界 |
| `mqtt_fragmented_input` | 分段 CONNECT、SUBSCRIBE、PUBLISH；拆分多字节 Remaining Length；完整前不产生结果 | R07 | B：帧、长度编码及 TCP 读取边界 |
| `mqtt_coalesced_input` | 一次发送两个 PUBLISH 和 PINGREQ，有序处理并返回准确结果 | R07、R09 | B：多个帧共用一次输入的边界 |
| `mqtt_ping_exchange` | 准确 PINGRESP 后仍可路由下一条 PUBLISH | R09、R04 | 常规：合法 PING 与后续业务 |
| `mqtt_disconnect_subscription_cleanup` | 断开后复用 Client ID，不继承旧订阅；重新订阅后恢复路由 | R10 | B：断开／重连的订阅状态边界 |
| `mqtt_invalid_packet_connection_isolation` | 超长 Remaining Length、错误 SUBSCRIBE 固定头标志、CONNECT 前 PUBLISH 仅关闭坏连接；正常连接继续收消息 | R02、R11 | A+B：畸形／非法顺序与连接隔离 |

纳入异常／边界的场景共 11 类；三轮每系统计划 33 项。

## 3. CoAP IPv4 UDP server

输入：[TASK.md](../../cases/coap_min/TASK.md)、[REQUIREMENTS.md](../../cases/coap_min/REQUIREMENTS.md)。独立验收器：[coap_check.py](../../evaluation/coap_check.py)。

### 3.1 采用的行为范围

- R02–R05：CoAP version 1、消息类型／码、网络序 Message ID、0–8 字节 Token、payload marker、option delta/length 扩展编码、重复 Uri-Path；路径区分大小写；处理 Uri-Host、Uri-Port、Content-Format，忽略未知 elective option，拒绝未知 critical option。CON 使用即时 piggybacked ACK；NON 使用匹配 Token 和服务器分配的 Message ID，并发回请求端点。
- R06–R07：`GET /hello` 返回 2.05、Content-Format 0 和准确 `hello`；共享内存资源 `/value` 初始为空，Content-Format 42 的 PUT 最多保存 1024 字节，返回 2.04；GET 返回准确二进制数据，空 PUT 清空资源。
- R08–R10：同一来源端点与 Message ID 的重复 CON 在 EXCHANGE_LIFETIME 内重放原响应，不重复执行请求，也不让旧 PUT 覆盖后续更新。空 CON 返回相同 Message ID 的空 RST；无关空 ACK/RST 不改变资源。未知资源返回 4.04，不支持的方法返回 4.05。
- R11–R12：检查头部、Token、option、保留值、payload marker 及数据报截断；畸形输入不使服务器崩溃、污染状态或中断其他端点；正常退出释放输入、资源、回复及重复响应存储，无 sanitizer 诊断。

使用直接单播回环 UDP；应用载荷最多 1024 字节，资源只存内存。资源名称、容量和严格 PUT 去重政策是任务约定。排除 separate response、服务器主动发送 CON 及重传、Observe、Block-wise、DTLS/TLS、代理、组播、资源发现、持久化和完整 CoAP 合规性。

### 3.2 独立行为测试场景

| 场景 ID | 实际检查的行为 | 主要关联需求 | 分类及依据 |
| --- | --- | --- | --- |
| `coap_con_get` | CON GET /hello 的 ACK、关联字段、2.05、Content-Format 0 和载荷 | R04、R06 | 常规：合法 CON GET |
| `coap_non_get` | NON GET /hello 的响应类型、Token、Message ID 和载荷 | R05–R06 | 常规：合法 NON GET |
| `coap_binary_put_get` | /value 初始为空，PUT/GET 精确保留二进制；空 PUT 清空；接受准确 1024 字节 | R02、R07 | B：零长度及容量上限 |
| `coap_endpoint_tokens` | Token 长度 0、1、8；相同 Message ID／Token 来自不同端点时分别关联，并正确更新共享资源 | R02、R04–R05、R08 | B：Token 长度端点及去重键的端点隔离 |
| `coap_option_boundaries` | 未知 elective option 的扩展 delta/length，长度 13、269；未知／大小写不同路径返回 4.04 | R03、R10 | A+B：扩展编码界点及未知资源 |
| `coap_duplicate_con` | 旧 PUT 重发时字节级重放旧响应，且不覆盖后续 PUT 的新值 | R07–R08 | B：重复请求的执行／重放状态边界 |
| `coap_empty_con_reset` | 空 CON 返回准确空 RST；空 ACK/RST 后合法请求继续可用 | R09 | B：空消息及不改变资源状态的控制消息 |
| `coap_error_responses` | 未知资源 4.04、不支持方法 4.05、未知 critical option 4.02，随后正常 GET 仍可用 | R03、R10 | A：规定的错误响应与服务连续性 |
| `coap_malformed_survival` | 空／短数据报、超界 Token、错误版本、无效 option／payload marker、大型畸形报文后正常 GET 仍可用 | R11 | A+B：畸形输入及编码／长度边界 |
| `coap_libcoap_interop` | coap-client-notls 完成 GET /hello、二进制 PUT /value、GET /value | R04、R06–R07 | 常规：外部客户端互通；二进制值本身不作边界分类依据 |

纳入异常／边界的场景共 7 类；三轮每系统计划 21 项。

## 4. HTTP/1.1 IPv4 TCP origin server

输入：[TASK.md](../../cases/http11_min/TASK.md)、[REQUIREMENTS.md](../../cases/http11_min/REQUIREMENTS.md)。独立验收器：[http11_check.py](../../evaluation/http11_check.py)。

### 4.1 采用的行为范围

- R02–R03：HTTP/1.1 请求行、origin-form target 和 CRLF 头部；方法／路径区分大小写，头名称及适用 token 不区分大小写；允许字段值两侧空白和合法未知字段；必须恰有一个有效非空 Host。`GET /hello` 返回准确 `hello`，query 不改变路由，路径不进行百分号解码或归一化。
- R04–R06：`/value` 跨连接共享、初始为空；PUT 最多保存 1024 个解码后的字节并返回无正文／Content-Length／Transfer-Encoding 的 204，仅完整有效请求可提交；GET 保留二进制／空值。POST /echo 返回准确输入、不修改 /value；HEAD 返回对应 GET 的状态和元数据且无正文，包括错误响应。2xx/4xx 含有效 Date，并保证回复类型、长度和 framing。
- R07–R10：单一有效非负十进制 Content-Length，包括零；无 CL/TE 时无正文。支持 chunked 的十六进制长度、zero chunk、扩展和允许的 trailer，对元数据和解码后内容限长。支持分片、合并、流水线和 EOF 前完整请求；默认持久连接；Connection: close 回复完成后关闭，不处理其后的请求；断开／停滞客户端不阻塞其他客户端。
- R11–R13：未知资源 404、已知资源不允许的方法 405+Allow、未实现方法 501；应用错误不改变资源并消费已定界正文。畸形语法／Host／framing 返回 400 并关闭；重复／列表 CL 或 CL+TE 拒绝；最终 coding 非 chunked 返回 400，前置不支持 coding 返回 501；过大正文 413、target 414、头／trailer 适当 4xx、不支持版本 505。明确输入、正文、资源、回复和连接所有权，处理部分写出与断开，正常退出无 sanitizer 诊断。

使用直接回环 TCP、HTTP/1.1 origin-form；GET/HEAD 不带请求正文。解码后正文最多 1024 字节、target 8192 字节、头与 trailer 各 16384 字节；/value 只存内存，重启清空。资源、限长及保守 framing 拒绝政策是任务约定。排除 HTTP/1.0、HTTP/2/3、TLS、代理、CONNECT、升级／WebSocket、100-continue、chunked 响应、其他 transfer coding、DELETE/OPTIONS/TRACE 应用实现、压缩、Range／条件请求、缓存、DNS、虚拟主机、文件服务、cookie／session、认证、持久化和完整 HTTP 合规性。

### 4.2 独立行为测试场景

| 场景 ID | 实际检查的行为 | 主要关联需求 | 分类及依据 |
| --- | --- | --- | --- |
| `http_get_hello_contract` | /hello 的状态、正文、类型、长度、Date；头名称大小写、空白、未知字段及带端口 Host | R02–R03、R06 | 常规：合法 GET 与常规头格式兼容 |
| `http_query_case_and_decoding` | query 不改变路由；大小写不同、额外斜杠、编码后的斜杠等路径返回 404 | R02–R03 | A：不满足精确路由的请求及错误回复 |
| `http_value_put_get_contract` | 初始空值、二进制 PUT/GET、准确 1024 字节、空 PUT 清空、204 无正文及 framing 字段 | R04、R06 | B：零长度及解码后容量上限 |
| `http_post_echo_both_framings` | CL 与 chunked 的二进制 echo；两种 framing 的空正文及无 CL/TE 请求；不改变 /value | R05、R07–R08 | B：零长度及无 framing 字段的正文边界 |
| `http_head_semantics` | HEAD /hello、/value 的 GET 元数据；404/405 错误响应无正文；后续 GET framing 完整 | R06、R11 | A：错误 HEAD 回复的状态、Allow 及无正文规则 |
| `http_content_length_edges` | 未收完声明正文不回复，严格消费 CL 后处理后续请求，提前 EOF 的 PUT 不改变值 | R04、R07、R09 | B：不完整／完整正文及 EOF 提交边界 |
| `http_chunked_decoding_details` | 十六进制大小写、扩展、trailer、zero chunk、跨读取边界的 chunk、准确 1024 个解码字节 | R08 | B：zero chunk、分片及解码后上限 |
| `http_fragmentation_and_pipeline` | 逐字节请求、三个合并流水线请求按序回复，完整 PUT 后立即 EOF 仍提交并回复 | R04、R09 | B：读取、请求边界及 EOF |
| `http_persistent_and_connection_close` | 默认持久连接、列表／大小写形式的 close、close 后流水线请求不执行、404 后连接仍可用 | R04、R10–R11 | A+B：应用错误及关闭后的请求执行边界 |
| `http_method_and_path_errors` | 405+Allow、501、404；带正文错误请求后仍可继续，且原 /value 不变 | R11 | A：方法／资源错误政策及资源不变性 |
| `http_malformed_rejection_policy` | 缺失／重复／空 Host、obs-fold、冒号前空白、无效／重复／列表 CL、CL+TE、错误 coding/chunk/version 均按政策拒绝并关闭 | R02、R12 | A：畸形输入、冲突 framing 及不支持版本 |
| `http_size_limits` | 1025 字节 CL/chunked 正文、8193 字节 target、过大头被拒绝；服务器仍可用且值不变 | R12 | A+B：超界输入与规定的限长错误政策 |
| `http_http_client_interop` | Python http.client 完成 GET、二进制 PUT/GET、POST echo | R03–R05 | 常规：外部客户端互通；普通二进制值不单独纳入 |

纳入异常／边界的场景共 11 类；三轮每系统计划 33 项。

## 5. SMTP IPv4 TCP 本地邮件捕获 server

输入：[TASK.md](../../cases/smtp_min/TASK.md)、[REQUIREMENTS.md](../../cases/smtp_min/REQUIREMENTS.md)。独立验收器：[smtp_check.py](../../evaluation/smtp_check.py)。

### 5.1 采用的行为范围

- R02–R03：220 greeting 标识 smtp.example.test；命令／回复使用 CRLF 和三位回复码，命令名不区分大小写；EHLO/HELO 接受有效域名或 IPv4 literal，初始化会话；重复 greeting 清除未完成事务，不查 DNS、不宣告扩展。
- R04–R05：MAIL FROM 使用 ASCII dot-string 邮箱或 null reverse-path；要求已初始化且无未完成事务。MAIL 后接受多个 RCPT，支持 alice、bob 及 qualified／unqualified postmaster；普通 local-part 区分大小写，域名和保留 postmaster 不区分；保持收件人顺序和重复项，550 拒绝未知收件人但保留已接受项。
- R06–R07：有发送者及至少一个收件人才允许 DATA；读至单点结束行、去掉一个透明点，保留其他 7-bit 内容、CRLF、空 DATA 和空行；看似命令的 DATA 行仍是内容。每封完整消息生成一个不覆盖已有捕获的唯一 UTF-8 JSON，含 mail_from、rcpt_to、准确 data；保存成功才返回 250，失败返回 451 且无完成捕获。
- R08–R10：处理分片命令／DATA／terminator 和合并行；EOF 前完整输入继续处理，不完整命令或 DATA 不产出邮件。支持 RSET、NOOP、VRFY、QUIT 的规定状态变化；完整成功或被拒 DATA 后重置事务，连接与捕获保持隔离，新连接不继承事务状态。
- R11–R12：未知命令 500、无效参数 501、非法顺序 503、识别但未实现命令 502、不支持 MAIL/RCPT 参数 555；超量收件人 452、超长命令 500、超长 DATA／行在读至 terminator 后 552 并重置；无法恢复的 framing 错误只关闭坏客户端，可行时回复 421。明确缓冲、地址、DATA、文件和连接所有权，处理部分写出，正常退出无 sanitizer 诊断。

使用直接回环 TCP、7-bit ASCII DATA、CRLF、dot-string 邮箱，无协商扩展。命令／回复行最多 512 字节（含 CRLF），DATA 行最多 1000 字节（含 CRLF，不含额外透明点）；每事务最多 100 个收件人和 65536 个去透明点后的 DATA 字节。身份、收件人、JSON 格式及事务容量是任务约定。排除邮件中继／外发、DNS/MX、投递／重试队列、状态通知、生产持久性／崩溃恢复、RFC 5322 验证、MIME、Received/Return-Path 插入、认证、TLS/STARTTLS、SMTPUTF8、8BITMIME、BINARYMIME/BDAT、协商 PIPELINING、邮件列表、quoted／国际化 local-part、source route 和完整 SMTP 合规性。

### 5.2 独立行为测试场景

| 场景 ID | 实际检查的行为 | 主要关联需求 | 分类及依据 |
| --- | --- | --- | --- |
| `smtp_greeting_helo_ehlo` | greeting 身份、EHLO、IPv4 literal HELO、缺参 EHLO 的 501 和重复 EHLO | R02–R03、R11 | A：无效 greeting 参数的错误回复 |
| `smtp_command_case_and_ehlo_reset` | 混合大小写命令；HELO 清除旧 envelope，旧事务 DATA 返回 503，随后新事务成功 | R02–R03、R06、R10 | A+B：会话重置及重置后的非法 DATA |
| `smtp_envelope_recipient_rules` | 初始化／MAIL／RCPT／DATA 顺序、嵌套 MAIL 拒绝、未知收件人、地址大小写、postmaster、重复收件人与保留原 envelope | R04–R06、R11 | A+B：非法顺序／收件人及失败后的 envelope 状态 |
| `smtp_data_capture_record` | 正常 DATA 完成时准确保存发送者、收件人与正文，并在最终 250 前写入 JSON | R06–R07 | 常规：合法邮件捕获及内容保真 |
| `smtp_dot_transparency_and_empty_data` | 去透明点、命令样正文保持内容、空 DATA、null reverse-path 的准确 JSON | R04、R06–R07 | B：空 DATA 与空反向路径 |
| `smtp_fragmented_and_coalesced_input` | 逐字节 EHLO、拆分 CRLF、合并 RCPT/DATA、拆分 terminator 与紧随其后的 QUIT | R08 | B：行、DATA 结束标记与输入读取边界 |
| `smtp_rset_noop_vrfy_quit` | NOOP 保留事务、VRFY 252／缺参 501、RSET 后 DATA 503、QUIT 丢弃未完成事务 | R09、R11 | A+B：错误参数、事务重置和终止边界 |
| `smtp_transaction_reuse_and_isolation` | 同连接连续事务、新连接 DATA/MAIL 503、不继承旧状态，三个捕获文件名不重复 | R07、R10–R11 | A+B：非法顺序及跨事务／连接状态隔离 |
| `smtp_reply_code_matrix` | 未知／未实现命令、缺参 greeting、错误 MAIL/RCPT、扩展参数按 500/502/501/555 回复，随后正常 DATA 成功 | R11 | A：完整错误回复矩阵 |
| `smtp_size_limits` | 513 字节命令、第 101 个收件人、1001 字节 DATA 行、超过 65536 字节 DATA 按规定拒绝、重置并继续服务 | R10–R11 | A+B：超限输入与拒绝后的事务状态 |
| `smtp_eof_and_aborted_data` | 完整缓冲事务后写侧 EOF 仍捕获／回复；未完成 DATA 的 EOF 不捕获，服务继续 | R08 | B：完整／中断事务与 EOF 的交界 |
| `smtp_storage_failure` | 将邮件目录设为不可写，返回 451、没有捕获，NOOP 仍成功 | R07 | A：实际存储失败及会话继续可用 |
| `smtp_smtplib_interop` | smtplib 发送多收件人邮件，准确捕获地址和正常去透明点后的正文 | R04–R07 | 常规：外部客户端互通与合法内容转义 |

纳入异常／边界的场景共 11 类；三轮每系统计划 33 项。`smtp_storage_failure` 在 root 下会标记环境阻塞，因为权限限制不足以可靠触发写失败；此状态不计通过。

## 6. 分类的适用范围及来源校验

各场景包含多个断言和报文，表中的“主要关联需求”用于说明测试目标，不声称穷尽关联关系或证明需求的全部条件。行为范围包含任务要求的资源清理及 sanitizer 义务；异常／边界指标只统计以上场景在普通模式中的结果，不把 sanitizer 日志或生成自测另计为行为场景。固定套件通过也不代表完整协议合规、生产可用、长期运行或压力测试通过。

四份当前验收器的 SHA-256 与留存的独立报告相同；SpecForge 未进入验收的第二轮 CoAP 没有对应执行报告。以下指纹固定本文采用的场景及断言版本：

| 验收器 | SHA-256 |
| --- | --- |
| [mqtt_check.py](../../evaluation/mqtt_check.py) | `e9b2d6d24b3b9944fa5c34679d0fd68272cae1394f3f9357c2c1118d104a975e` |
| [coap_check.py](../../evaluation/coap_check.py) | `e670e0aeca51e2555a28f5ebb177b8e2e6fff16cc77b5631a5e79145bf67c6df` |
| [http11_check.py](../../evaluation/http11_check.py) | `c325e83ecdb30d438a6d11d16c96f6ed8955ffee2615d634ae435da7512a1931` |
| [smtp_check.py](../../evaluation/smtp_check.py) | `5060e461e1b8cce954722cef327b8211e114271a0bace5babe88d11b44dc486c` |
