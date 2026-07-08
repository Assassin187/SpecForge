[PROMPT]
Implement function `finish_auth_if_valid`. Responsibility: 验证凭据并设置 authenticated/auth_user，发送 235 或 535，最后清理 AUTH continuation

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

- FUNC `smtp_auth_validate`
  role: 被该函数调用以完成子步骤
```c
int smtp_auth_validate(const char* user, const char* pass);
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
static void finish_auth_if_valid(smtp_server_t* server, smtp_session_t* session, const char* user, const char* pass);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入参数：server(smtp_server_t*，可为 NULL，BORROWED)；session(smtp_session_t*，可为 NULL，BORROWED)；user(const char*，可为 NULL，BORROWED)；pass(const char*，可为 NULL，BORROWED)。

**Post-Condition**:
- 无返回值；副作用为验证凭据并设置 authenticated/auth_user，发送 235 或 535，最后清理 AUTH continuation。

**Invariant**:
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 函数可能分配资源、消费缓冲、修改链表、写文件或更新网络状态，不承诺幂等。
- 维护 session->authenticated：成功 true，失败 false
- 维护 session->auth_user：成功时保存 user

**System Algorithm**:
- 验证凭据并设置 authenticated/auth_user，发送 235 或 535，最后清理 AUTH continuation。
