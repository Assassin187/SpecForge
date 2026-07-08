[PROMPT]
Implement function `on_accept_cb`. Responsibility: TCP accept 回调：创建 session、加入链表并发送 220 greeting

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

- FUNC `smtp_session_create`
  role: 被该函数调用以完成子步骤
```c
smtp_session_t* smtp_session_create(int control_fd);
```

- FUNC `add_session`
  role: 被该函数调用以完成子步骤
```c
static int add_session(smtp_server_t* server, smtp_session_t* session);
```

- FUNC `smtp_tcp_server_close_client`
  role: 被该函数调用以完成子步骤
```c
int smtp_tcp_server_close_client(smtp_tcp_server_t* server, int fd);
```

- FUNC `queue_code`
  role: 被该函数调用以完成子步骤
```c
static int queue_code(smtp_server_t* server, smtp_session_t* session, int code, const char* text);
```

[GUARANTEE]
```c
static void on_accept_cb(void* user, int fd, const struct sockaddr_storage* peer, socklen_t peer_len);
```

[SPECIFICATION]
**Pre-Condition**:
- Trigger: tcp_server 接受新连接后调用；创建 smtp_session_t，失败则关闭 fd，成功后排队发送 '<hostname> ESMTP ready' 的 220 greeting。
- Precondition: 输入参数：user(void*，可为 NULL，BORROWED)；fd(int，不可为 NULL，BORROWED)；peer(const struct sockaddr_storage*，可为 NULL，BORROWED)；peer_len(socklen_t，不可为 NULL，BORROWED)；遵守单线程事件循环调用约束。
- Input: 输入参数：user(void*，可为 NULL，BORROWED)；fd(int，不可为 NULL，BORROWED)；peer(const struct sockaddr_storage*，可为 NULL，BORROWED)；peer_len(socklen_t，不可为 NULL，BORROWED)。

**Post-Condition**:
- State Change: 新增或拒绝一个连接/session，并相应更新 server 管理链表或 TCP client 集合；重复触发不得破坏状态一致性。
- Response: 无直接返回值；成功时新增 session/connection 并注册到上层容器，失败时关闭 fd 并清理临时对象。

**Invariant**:
- None specified.

**System Algorithm**:
- tcp_server 接受新连接后调用；创建 smtp_session_t，失败则关闭 fd，成功后排队发送 '<hostname> ESMTP ready' 的 220 greeting。
