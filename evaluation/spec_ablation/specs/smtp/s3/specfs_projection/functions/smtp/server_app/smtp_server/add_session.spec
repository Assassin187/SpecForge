[PROMPT]
Implement function `add_session`. Responsibility: 把新 session 头插到 server session 链表

[RELY]
- STRUCT `smtp_server_t`
  role: 该函数读取或维护的结构化状态
```c
typedef struct smtp_server smtp_server_t;
```

- STRUCT `smtp_session_node_t`
  role: 该函数读取或维护的结构化状态
```c
typedef struct smtp_session_node smtp_session_node_t;
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

[GUARANTEE]
```c
static int add_session(smtp_server_t* server, smtp_session_t* session);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入参数：server(smtp_server_t*，可为 NULL，BORROWED)；session(smtp_session_t*，可为 NULL，BORROWED)。

**Post-Condition**:
- 返回值表达把新 session 头插到 server session 链表的结果；成功、失败和特殊分支按当前 C 实现的返回码区分。

**Invariant**:
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 函数可能分配资源、消费缓冲、修改链表、写文件或更新网络状态，不承诺幂等。
- 调用方必须遵守 SIGNATURE.PARAMS 中的 NULLABLE 与 OWNERSHIP 约束。
- 维护 server->sessions：头插新节点

**System Algorithm**:
- 把新 session 头插到 server session 链表。
