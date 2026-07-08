[PROMPT]
Implement function `handle_auth_continuation`. Responsibility: 处理 AUTH LOGIN/PLAIN 的后续 base64 行

[RELY]
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

- FUNC `decode_b64_to_text`
  role: 被该函数调用以完成子步骤
```c
static int decode_b64_to_text(const char* b64, char* out, size_t out_len);
```

- FUNC `smtp_auth_decode_base64`
  role: 被该函数调用以完成子步骤
```c
int smtp_auth_decode_base64(const char* input, unsigned char* out, size_t out_cap, size_t* out_len);
```

- FUNC `smtp_auth_parse_plain_blob`
  role: 被该函数调用以完成子步骤
```c
int smtp_auth_parse_plain_blob(const unsigned char* blob, size_t blob_len, char* out_user, size_t out_user_len, char* out_pass, size_t out_pass_len);
```

- FUNC `finish_auth_if_valid`
  role: 被该函数调用以完成子步骤
```c
static void finish_auth_if_valid(smtp_server_t* server, smtp_session_t* session, const char* user, const char* pass);
```

- FUNC `queue_code`
  role: 被该函数调用以完成子步骤
```c
static int queue_code(smtp_server_t* server, smtp_session_t* session, int code, const char* text);
```

- FUNC `smtp_session_reset_auth_exchange`
  role: 被该函数调用以完成子步骤
```c
void smtp_session_reset_auth_exchange(smtp_session_t* session);
```

[GUARANTEE]
```c
static void handle_auth_continuation(smtp_server_t* server, smtp_session_t* session, const char* line);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入参数：server(smtp_server_t*，可为 NULL，BORROWED)；session(smtp_session_t*，可为 NULL，BORROWED)；line(const char*，可为 NULL，BORROWED)。

**Post-Condition**:
- 无返回值；LOGIN username 成功时发送 password challenge，LOGIN password 或 PLAIN blob 成功时由 finish_auth_if_valid 发送 235 或 535；Base64/blob 格式错误返回 501；非 continuation 状态返回 503。错误路径均清理 AUTH continuation。

**Invariant**:
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 函数可能分配资源、消费缓冲、修改链表、写文件或更新网络状态，不承诺幂等。
- 维护 session->auth_state：决定 continuation 分支
- 维护 session->auth_login_user：LOGIN username 暂存
- 遵循 AUTH_LOGIN.username_or_password_line 映射规则：根据 auth_state 把单独 base64 payload 行解释为 username 或 password
- 遵循 AUTH_PLAIN.plain_blob_line 映射规则：根据 auth_state 把单独 base64 payload 行解释为 PLAIN blob
- AUTH continuation 行不包含 AUTH verb、LOGIN/PLAIN mechanism 或额外命令参数
- Base64/blob 格式错误和非 continuation 状态均必须调用 smtp_session_reset_auth_exchange

**System Algorithm**:
- server、session 或 line 无效时直接返回。若 session->auth_state 为 SMTP_AUTH_STATE_LOGIN_WAIT_USER，则当前 line 必须是单独的 Base64 username payload，不包含 AUTH verb 或 LOGIN mechanism；decode_b64_to_text 成功后保存 session->auth_login_user、切换到 SMTP_AUTH_STATE_LOGIN_WAIT_PASS 并发送 334 UGFzc3dvcmQ6，失败时排队 501 并调用 smtp_session_reset_auth_exchange。若 session->auth_state 为 SMTP_AUTH_STATE_LOGIN_WAIT_PASS，则当前 line 必须是单独的 Base64 password payload；decode_b64_to_text 成功后调用 finish_auth_if_valid，失败时排队 501 并调用 smtp_session_reset_auth_exchange。若 session->auth_state 为 SMTP_AUTH_STATE_PLAIN_WAIT_BLOB，则当前 line 必须是单独的 Base64 PLAIN blob payload；解码和 smtp_auth_parse_plain_blob 均成功后调用 finish_auth_if_valid，任一格式错误排队 501 并调用 smtp_session_reset_auth_exchange。若 auth_state 不是 continuation 状态，则排队 503 并调用 smtp_session_reset_auth_exchange。
