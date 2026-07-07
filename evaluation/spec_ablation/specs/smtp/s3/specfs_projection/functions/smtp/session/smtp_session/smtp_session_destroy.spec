[PROMPT]
Implement function `smtp_session_destroy`. Responsibility: 销毁 session 及其 smtp_connection_t

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

- STRUCT `smtp_connection_t`
  role: 该函数读取或维护的结构化状态
```c
typedef struct smtp_connection smtp_connection_t;
```

- FUNC `smtp_connection_destroy`
  role: 被该函数调用以完成子步骤
```c
void smtp_connection_destroy(smtp_connection_t* conn);
```

[GUARANTEE]
```c
void smtp_session_destroy(smtp_session_t* session);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入参数：session(smtp_session_t*，可为 NULL，BORROWED)。

**Post-Condition**:
- 无返回值；释放本对象持有的内存、连接、队列或子对象，NULL 输入安全返回。

**Invariant**:
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 函数可能分配资源、消费缓冲、修改链表、写文件或更新网络状态，不承诺幂等。
- 调用方必须遵守 SIGNATURE.PARAMS 中的 NULLABLE 与 OWNERSHIP 约束。

**System Algorithm**:
- 销毁 session 及其 smtp_connection_t。
