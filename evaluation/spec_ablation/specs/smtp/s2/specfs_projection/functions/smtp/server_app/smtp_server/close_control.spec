[PROMPT]
Implement function `close_control`. Responsibility: 委托 TCP server 关闭控制连接并同步触发 session 销毁；返回后原 session 指针失效

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

- FUNC `smtp_tcp_server_close_client`
  role: 被该函数调用以完成子步骤
```c
int smtp_tcp_server_close_client(smtp_tcp_server_t* server, int fd);
```

[GUARANTEE]
```c
static void close_control(smtp_server_t* server, smtp_session_t* session);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入参数：server(smtp_server_t*，可为 NULL，BORROWED)；session(smtp_session_t*，可为 NULL，BORROWED)。

**Post-Condition**:
- 无返回值；成功关闭时 fd 从 tcp server 与 server session 链表移除，传入的 session 指针在本函数返回后立即失效。调用方必须立即 return，或使用保存的 fd 重新调用 find_session 后才能继续处理。

**Invariant**:
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 函数可能分配资源、消费缓冲、修改链表、写文件或更新网络状态，不承诺幂等。
- smtp_tcp_server_close_client 同步调用 on_close，on_close 可以在 close_control 返回前释放 session
- close_control 返回后禁止读取 session->control_fd、session->conn 或任何其他 session 字段
- 需要发送最终响应的调用方必须在 close_control 之前完成 queue 和 flush

**System Algorithm**:
- server 或 session 为 NULL 时直接返回；否则只调用 smtp_tcp_server_close_client(server->tcp, session->control_fd)。close_client 会在返回前同步触发 on_close，on_close 从 server 链表移除并销毁该 session。本函数不负责 queue、发送或 flush SMTP response。
