# SMTP specs-example 汇总（基于 specs-example/smtp_specs）

> 说明：本文件由脚本从 SMTP 示例 specs 自动汇总生成；事实来源限定为 protocol-example/smtp 当前 C 实现和 README。

## 协议元信息（PROTOCOL_MODULE_SPEC）
- 协议：SMTP RFC5321-style example subset
- 角色：SERVER
- 默认端口：2525
- Scope：Local-receive SMTP server subset implemented by protocol-example/smtp: HELO/EHLO, AUTH LOGIN/PLAIN, MAIL FROM, RCPT TO, DATA, RSET, NOOP, VRFY, QUIT, local .eml storage only.

## 规格清单统计
- JSON 文件总数：78
- KIND 统计：FILE_SPEC=9, FUNCTION_SPEC=68, PROTOCOL_MODULE_SPEC=1

## 生成顺序（GENERATION_ORDER）
- network → protocol_text → auth → storage → session → server_app

## 模块概览（MODULES）
### network
- 角色：TCP/epoll 网络层：管理监听 socket、客户端 fd、可读/可写事件和连接输入/输出缓冲；不解析 SMTP 命令语义。
- 依赖模块：（无）
- 产物（ARTIFACTS）：
  - TYPE: smtp_connection_t — 非阻塞连接对象，封装输入行缓冲和输出队列
  - TYPE: smtp_tcp_server_t — epoll TCP server 运行时对象
  - TYPE: smtp_tcp_callbacks_t — accept/event/close 回调集合
  - FUNC: smtp_connection_pop_line — 从输入缓冲弹出 CRLF/LF 终止的单行文本
  - FUNC: smtp_tcp_server_run — 运行 epoll 事件循环并回调上层
- 关联源码文件（FILES）：
  - ../network/connection.h
  - ../network/connection.c
  - ../network/tcp_server.h
  - ../network/tcp_server.c

### protocol_text
- 角色：SMTP 文本协议层：解析命令行到 smtp_command_t，并格式化响应行/EHLO capabilities；不持有连接或 session 状态。
- 依赖模块：（无）
- 产物（ARTIFACTS）：
  - TYPE: smtp_command_kind_t — SMTP 示例支持的命令枚举
  - TYPE: smtp_command_t — 命令解析输出对象
  - FUNC: smtp_command_parse — 解析单行 SMTP command
  - FUNC: smtp_response_format — 格式化单行 SMTP response
  - FUNC: smtp_response_format_ehlo_caps — 格式化 EHLO capability 多行 response
- 关联源码文件（FILES）：
  - ../protocol/smtp_command.h
  - ../protocol/smtp_command.c
  - ../protocol/smtp_response.h
  - ../protocol/smtp_response.c

### auth
- 角色：SMTP AUTH helper：固定测试凭据、base64 解码、AUTH PLAIN blob 解析。只支持 LOGIN/PLAIN 所需能力。
- 依赖模块：（无）
- 产物（ARTIFACTS）：
  - FUNC: smtp_auth_validate — 验证 smtpuser/smtppass
  - FUNC: smtp_auth_decode_base64 — 解码 AUTH base64 payload
  - FUNC: smtp_auth_parse_plain_blob — 解析 AUTH PLAIN blob
- 关联源码文件（FILES）：
  - ../auth/smtp_auth.h
  - ../auth/smtp_auth.c

### storage
- 角色：本地投递存储层：创建 mail_root，把 envelope metadata 与 DATA 内容写入唯一 .eml 文件。
- 依赖模块：（无）
- 产物（ARTIFACTS）：
  - TYPE: smtp_mail_t — 待落盘邮件 envelope/data 视图
  - FUNC: smtp_mail_store_init — 确保 mail_root 目录存在
  - FUNC: smtp_mail_store_write — 写入 .eml 文件
- 关联源码文件（FILES）：
  - ../storage/mail_store.h
  - ../storage/mail_store.c

### session
- 角色：SMTP session 状态层：按连接维护 greeting、authentication、MAIL/RCPT transaction 和 DATA 收集状态。
- 依赖模块：network
- 产物（ARTIFACTS）：
  - TYPE: smtp_session_t — per-connection SMTP session 状态结构
  - TYPE: smtp_auth_state_t — AUTH continuation 状态枚举
  - FUNC: smtp_session_create — 创建 session 与连接对象
  - FUNC: smtp_session_reset_transaction — 清空 MAIL/RCPT/DATA 状态
  - FUNC: smtp_session_reset_auth_exchange — 清空 AUTH continuation 状态
- 关联源码文件（FILES）：
  - ../server/smtp_session.h
  - ../server/smtp_session.c

### server_app
- 角色：SMTP server 应用层：创建 TCP server，维护 session 链表，处理 HELO/EHLO/AUTH/MAIL/RCPT/DATA/RSET/NOOP/VRFY/QUIT，并调用 storage 落盘。
- 依赖模块：network, protocol_text, auth, storage, session
- 产物（ARTIFACTS）：
  - TYPE: smtp_server_t — SMTP server 进程级对象
  - FUNC: smtp_server_create — 初始化 mail_root、TCP server 与 callbacks
  - FUNC: handle_command — 核心 SMTP command dispatcher
  - FUNC: handle_auth — AUTH LOGIN/PLAIN 命令处理
  - FUNC: handle_data_line — DATA 内容行处理与落盘
  - FUNC: main — 进程入口
- 关联源码文件（FILES）：
  - ../server/smtp_server.h
  - ../server/smtp_server.c
  - ../main.c

## 文件与函数覆盖

### smtp/main
- 文件职责：SMTP server 进程入口：解析端口与 mail_root 参数，创建、启动并运行 server 生命周期
- Source：../main.c
- Function specs：2（public=0, private/static=2）

### smtp/network/connection
- 文件职责：SMTP TCP 连接抽象：维护非阻塞 socket 的输入行缓冲与输出队列
- Source：../network/connection.c
- Function specs：10（public=9, private/static=1）
- Header：../network/connection.h

### smtp/network/tcp_server
- 文件职责：基于 epoll 的 TCP server：监听、accept、事件转换、连接关闭和写兴趣更新
- Source：../network/tcp_server.c
- Function specs：13（public=7, private/static=6）
- Header：../network/tcp_server.h

### smtp/protocol_text/smtp_command
- 文件职责：SMTP 文本命令解析模块：把单行命令拆分为 verb/kind/arg
- Source：../protocol/smtp_command.c
- Function specs：3（public=2, private/static=1）
- Header：../protocol/smtp_command.h

### smtp/protocol_text/smtp_response
- 文件职责：SMTP 文本响应格式化模块：生成单行 code text 与 EHLO 多行 capability 响应
- Source：../protocol/smtp_response.c
- Function specs：2（public=2, private/static=0）
- Header：../protocol/smtp_response.h

### smtp/auth/smtp_auth
- 文件职责：SMTP AUTH helper：固定账号验证、base64 解码与 AUTH PLAIN blob 解析
- Source：../auth/smtp_auth.c
- Function specs：5（public=4, private/static=1）
- Header：../auth/smtp_auth.h

### smtp/storage/mail_store
- 文件职责：本地邮件落盘模块：确保 mail_root 目录存在并把 envelope metadata 与 DATA 写为 .eml 文件
- Source：../storage/mail_store.c
- Function specs：4（public=2, private/static=2）
- Header：../storage/mail_store.h

### smtp/session/smtp_session
- 文件职责：SMTP per-connection session 状态模块：维护 greeting、AUTH、transaction 和 DATA 收集状态
- Source：../server/smtp_session.c
- Function specs：4（public=4, private/static=0）
- Header：../server/smtp_session.h

### smtp/server_app/smtp_server
- 文件职责：SMTP server 应用层协调模块：管理 session 链表、命令分发、AUTH、DATA 收集、落盘和 TCP callbacks
- Source：../server/smtp_server.c
- Function specs：25（public=5, private/static=20）
- Header：../server/smtp_server.h

## 一致性规则与禁止符号

### CONSISTENCY_RULES
- C1: network 层不得解析 SMTP 命令、AUTH payload 或 DATA 内容；它只提供字节读取、行弹出、输出队列和 epoll 事件。
- C2: SMTP command 解析必须经过 smtp_command_parse；响应行必须通过 smtp_response_format 或 smtp_response_format_ehlo_caps 生成。
- C3: MAIL/RCPT/DATA 必须满足 greeted=true 且 authenticated=true；否则分别返回当前实现中的 503 或 530。
- C4: AUTH 只支持 LOGIN 和 PLAIN；其他机制返回 504，不得生成 STARTTLS、SASL 其他机制或外部用户数据库。
- C5: DATA mode 中单独一行 '.' 终止消息；以 '..' 开头的数据行必须去掉一个前导 dot；超过 SMTP_MAX_MESSAGE_SIZE 返回 552 并 reset transaction。
- C6: 本示例只做本地 .eml 落盘，不做 SMTP relay、DNS/MX lookup、队列重试或远端投递。

### FORBIDDEN_SYMBOLS
- smtp_packet_t (TYPE): SMTP 示例实现是文本行协议，没有二进制 packet 抽象
- smtp_decoder_feed (FUNC): SMTP 输入通过 smtp_connection_pop_line 和 smtp_command_parse 处理，不存在增量二进制 decoder
- smtp_encoder (TYPE): SMTP 响应由 smtp_response_format 文本格式化，不存在独立 encoder 对象
- STARTTLS (FEATURE): 当前示例未实现 TLS/STARTTLS
- smtp_relay (FEATURE): 当前示例只本地落盘接收邮件，不实现 relay/outbound delivery
- DNS_MX_LOOKUP (FEATURE): 当前示例不做 DNS/MX 查询
- external_user_database (FEATURE): 当前认证只使用 hard-coded smtpuser/smtppass
- AUTH_CRAM_MD5 (FEATURE): 当前 AUTH 只支持 LOGIN 和 PLAIN
