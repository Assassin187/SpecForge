[PROMPT]
Implement function `handle_command`. Responsibility: 按 smtp_command_t.kind 分发 SMTP 命令并维护 greeting/auth/transaction/DATA 状态

[RELY]
- STRUCT `smtp_server_t`
  role: 该函数读取或维护的结构化状态
```c
typedef struct smtp_server smtp_server_t;
```

- STRUCT `smtp_session_t`
  role: 该函数读取或维护的结构化状态
```c
typedef struct smtp_session {
    int control_fd;
    smtp_connection_t* conn;
    bool greeted;
    bool authenticated;
    char helo_name[256];
    char auth_user[256];
    smtp_auth_state_t auth_state;
    char auth_login_user[256];
    char mail_from[512];
    bool has_mail_from;
    char rcpt_to[SMTP_MAX_RECIPIENTS][512];
    size_t rcpt_count;
    bool in_data_mode;
    char message_buf[SMTP_MAX_MESSAGE_SIZE + 1];
    size_t message_len;
    bool message_too_large;
} smtp_session_t;
```

- STRUCT `smtp_command_t`
  role: 该函数读取或维护的结构化状态
```c
typedef struct smtp_command {
    smtp_command_kind_t kind;
    char arg[1024];
    bool has_arg;
} smtp_command_t;
```

- FUNC `handle_auth`
  role: 被该函数调用以完成子步骤
```c
static void handle_auth(smtp_server_t* server, smtp_session_t* session, const smtp_command_t* cmd);
```

- FUNC `ensure_ready_for_mail`
  role: 被该函数调用以完成子步骤
```c
static int ensure_ready_for_mail(smtp_server_t* server, smtp_session_t* session);
```

- FUNC `parse_mailbox_arg`
  role: 被该函数调用以完成子步骤
```c
static int parse_mailbox_arg(const char* arg, const char* key, char* out, size_t out_len);
```

- FUNC `smtp_session_reset_transaction`
  role: 被该函数调用以完成子步骤
```c
void smtp_session_reset_transaction(smtp_session_t* session);
```

- FUNC `smtp_session_reset_auth_exchange`
  role: 被该函数调用以完成子步骤
```c
void smtp_session_reset_auth_exchange(smtp_session_t* session);
```

- FUNC `smtp_response_format_ehlo_caps`
  role: 被该函数调用以完成子步骤
```c
int smtp_response_format_ehlo_caps(char* out, size_t out_len, const char* hostname, size_t max_size);
```

- FUNC `queue_code`
  role: 被该函数调用以完成子步骤
```c
static int queue_code(smtp_server_t* server, smtp_session_t* session, int code, const char* text);
```

- FUNC `queue_raw`
  role: 被该函数调用以完成子步骤
```c
static int queue_raw(smtp_server_t* server, smtp_session_t* session, const char* text);
```

- FUNC `flush_control_now`
  role: 被该函数调用以完成子步骤
```c
static int flush_control_now(smtp_server_t* server, smtp_session_t* session);
```

- FUNC `close_control`
  role: 被该函数调用以完成子步骤
```c
static void close_control(smtp_server_t* server, smtp_session_t* session);
```

[GUARANTEE]
```c
static void handle_command(smtp_server_t* server, smtp_session_t* session, const smtp_command_t* cmd);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入参数：server(smtp_server_t*，可为 NULL，BORROWED)；session(smtp_session_t*，可为 NULL，BORROWED)；cmd(const smtp_command_t*，可为 NULL，BORROWED)。

**Post-Condition**:
- 无返回值；每个命令至多排队一个对应的单行响应或一组 EHLO capabilities。MAIL/RCPT/DATA 只在前置状态满足时推进 transaction；UNKNOWN 不关闭连接；QUIT 在尽力同步 flush 221 后关闭连接并使 session 失效。

**Invariant**:
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 函数可能分配资源、消费缓冲、修改链表、写文件或更新网络状态，不承诺幂等。
- 维护 session->greeted：HELO/EHLO 成功后置 true
- 维护 session->mail_from：MAIL FROM 成功后写入
- 维护 session->rcpt_to[i]：RCPT TO 成功后追加
- 维护 session->rcpt_count：RCPT 成功后递增
- 维护 session->in_data_mode：DATA 成功后置 true
- 遵循 SMTP_COMMAND.HELO 映射规则：需要 arg；成功写 helo_name/greeted 并返回 250
- 遵循 SMTP_COMMAND.EHLO 映射规则：需要 arg；成功返回 EHLO capabilities
- 遵循 SMTP_COMMAND.MAIL FROM 映射规则：需要 greeting/auth 且 FROM: mailbox 语法合法
- 遵循 SMTP_COMMAND.RCPT TO 映射规则：需要 MAIL FROM 且 TO: mailbox 合法，最多 SMTP_MAX_RECIPIENTS
- 遵循 SMTP_COMMAND.DATA 映射规则：需要 sender 和至少一个 recipient；成功进入 DATA mode 并返回 354
- AUTH 分发必须保留 cmd->arg 的完整 AUTH 参数并委托 handle_auth 解析 mechanism 与 optional initial response
- MAIL FROM 保存新 sender 后不得调用会清空 mail_from/has_mail_from 的 smtp_session_reset_transaction
- UNKNOWN 只排队 500，不改变连接与 session 生命周期
- QUIT 的 queue 221、flush、close 顺序不可交换，close 后必须立即结束函数
- close_control 返回后 session 已失效，任何字段访问均为禁止行为

**System Algorithm**:
- server、session 或 cmd 无效时直接返回。HELO/EHLO 需要非空 arg，成功后保存 helo_name、设置 greeted=true，并清理旧 transaction 与 AUTH continuation；HELO 排队 250，EHLO 排队 capabilities。AUTH 分支只识别 SMTP_CMD_AUTH 并原样调用 handle_auth(server, session, cmd)；不得在 handle_command 内预先拆分、改写、解码或丢弃 cmd->arg。MAIL 先通过 ensure_ready_for_mail 验证 greeting/auth，再把合法 FROM: 地址解析到 session->mail_from；解析成功后设置 has_mail_from=true，并只清空旧 rcpt_count、in_data_mode、message_len 和 message_too_large，不得在保存新 sender 后调用 smtp_session_reset_transaction，也不得把 session->mail_from 自复制给自身。RCPT 要求已存在 MAIL FROM，把合法 TO: 地址追加到 session->rcpt_to[rcpt_count] 并递增 rcpt_count。DATA 要求 sender 和至少一个 recipient，成功后进入 DATA mode、清空 message length/size flag 并排队 354。RSET 清理 transaction 与 AUTH continuation后排队 250；NOOP 排队 250；VRFY 排队 252；UNKNOWN 排队 500 且保持连接可继续处理。QUIT 必须严格依次 queue_code(221)、flush_control_now、close_control，然后立即 return；close_control 后禁止访问 session、检查 pending 或继续执行 switch。
