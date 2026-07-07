[PROMPT]
Implement function `handle_auth`. Responsibility: 从 smtp_command_t.arg 中解析 AUTH mechanism token 和可选 initial response，支持 LOGIN/PLAIN

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

- STRUCT `smtp_command_t`
  role: 该函数读取或维护的结构化状态
```c
typedef struct smtp_command {
    smtp_command_kind_t kind;
    char arg[1024];
    bool has_arg;
} smtp_command_t;
```

- FUNC `begin_login_auth`
  role: 被该函数调用以完成子步骤
```c
static void begin_login_auth(smtp_server_t* server, smtp_session_t* session);
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
  role: AUTH 参数或 Base64/blob 格式错误后清理 continuation 状态
```c
void smtp_session_reset_auth_exchange(smtp_session_t* session);
```

[GUARANTEE]
```c
static void handle_auth(smtp_server_t* server, smtp_session_t* session, const smtp_command_t* cmd);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入参数：server(smtp_server_t*，可为 NULL，BORROWED)；session(smtp_session_t*，可为 NULL，BORROWED)；cmd(const smtp_command_t*，可为 NULL，BORROWED)。

**Post-Condition**:
- 无返回值；成功的 AUTH LOGIN 无 initial response 产生 username challenge，成功的 AUTH LOGIN initial response 产生 password challenge，成功的 AUTH PLAIN 由 finish_auth_if_valid 产生 235 或 535。语法/Base64/blob 格式错误返回 501；unsupported mechanism 返回 504；重复 AUTH 返回 503。

**Invariant**:
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 函数可能分配资源、消费缓冲、修改链表、写文件或更新网络状态，不承诺幂等。
- 调用方必须遵守 SIGNATURE.PARAMS 中的 NULLABLE 与 OWNERSHIP 约束。
- 维护 session->auth_state：设置 LOGIN/PLAIN continuation
- 维护 session->auth_login_user：LOGIN initial response 或 username continuation 成功后保存 username
- 维护 session->authenticated：已认证则拒绝重复 AUTH
- 遵循 AUTH.mechanism 映射规则：cmd->arg 的第一个非空白 token 是 mechanism，大小写无关规整为 LOGIN 或 PLAIN；其他机制返回 504
- 遵循 AUTH_LOGIN.initial_response 映射规则：LOGIN token 后的剩余非空白文本才是可选 initial username base64，随后等待 password
- 遵循 AUTH_PLAIN.initial_response 映射规则：PLAIN token 后的剩余非空白文本才是可选 PLAIN blob，缺省则发送空 334 challenge
- cmd->has_arg=true 只表示存在 AUTH 参数，不表示存在 initial_response
- LOGIN 和 PLAIN mechanism token 不得被当作 base64 payload 解码
- initial_response 只存在于 mechanism token 后面的剩余文本

**System Algorithm**:
- server、session 或 cmd 无效时直接返回。若 session->authenticated 已为 true，则排队 503 并保持认证状态不变。若 cmd->has_arg 为 false，或 cmd->arg 去除首尾 ASCII whitespace 后为空，则排队 501，不进入 continuation。否则从 cmd->arg 起始处读取第一个非空白 token 作为 mechanism，并大小写无关比较；只有该 mechanism token 后的剩余非空白文本才是 optional initial_response。禁止把 LOGIN 或 PLAIN mechanism token 本身传给 smtp_auth_decode_base64 或 decode_b64_to_text。AUTH LOGIN 无 initial_response 时调用 begin_login_auth，进入 SMTP_AUTH_STATE_LOGIN_WAIT_USER 并发送 334 VXNlcm5hbWU6。AUTH LOGIN <initial_response> 时只把 <initial_response> 作为 base64 username 解码；成功后保存 session->auth_login_user、设置 SMTP_AUTH_STATE_LOGIN_WAIT_PASS 并发送 334 UGFzc3dvcmQ6，解码失败时排队 501 并调用 smtp_session_reset_auth_exchange。AUTH PLAIN 无 initial_response 时设置 SMTP_AUTH_STATE_PLAIN_WAIT_BLOB 并发送空 334 challenge。AUTH PLAIN <initial_response> 时只把 <initial_response> 作为 base64 PLAIN blob 解码并调用 smtp_auth_parse_plain_blob；Base64 或 blob 格式错误排队 501，成功则调用 finish_auth_if_valid，凭据不匹配由 finish_auth_if_valid 返回 535。其他 mechanism 排队 504，且不得进入 continuation。
