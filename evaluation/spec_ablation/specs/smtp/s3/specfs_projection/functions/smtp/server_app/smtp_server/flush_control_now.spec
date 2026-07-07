[PROMPT]
Implement function `flush_control_now`. Responsibility: 立即 flush session 输出队列并根据 pending 状态更新写兴趣

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

- FUNC `smtp_connection_flush`
  role: 被该函数调用以完成子步骤
```c
int smtp_connection_flush(smtp_connection_t* conn);
```

- FUNC `smtp_connection_has_pending`
  role: 被该函数调用以完成子步骤
```c
bool smtp_connection_has_pending(const smtp_connection_t* conn);
```

- FUNC `smtp_tcp_server_update_interest`
  role: 被该函数调用以完成子步骤
```c
int smtp_tcp_server_update_interest(smtp_tcp_server_t* server, int fd, bool want_write);
```

[GUARANTEE]
```c
static int flush_control_now(smtp_server_t* server, smtp_session_t* session);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入参数：server(smtp_server_t*，可为 NULL，BORROWED)；session(smtp_session_t*，可为 NULL，BORROWED)。

**Post-Condition**:
- flush 失败返回 -1；flush 未报错时返回 smtp_tcp_server_update_interest 的结果。返回 0 表示写兴趣已与真实 pending 状态一致，返回 -1 表示更新失败。无论返回值如何，本函数都不结束 session 生命周期。

**Invariant**:
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 函数可能分配资源、消费缓冲、修改链表、写文件或更新网络状态，不承诺幂等。
- 调用方必须遵守 SIGNATURE.PARAMS 中的 NULLABLE 与 OWNERSHIP 约束。
- smtp_connection_flush 返回 1 表示仍有 pending 数据，不是 fatal error，必须保持 EPOLLOUT
- 只有 close_control 或 tcp server close callback 可以结束 session 生命周期，本函数不得隐式关闭

**System Algorithm**:
- server 或 session 无效时返回 -1。调用 smtp_connection_flush(session->conn)；返回值小于 0 时直接返回 -1，本函数不得关闭连接、调用 close_control 或销毁 session。flush 返回 0 或 1 后，调用 smtp_connection_has_pending 获取真实 pending 状态，并把该 bool 传给 smtp_tcp_server_update_interest：pending 为 true 时保留 EPOLLOUT，全部发送后取消 EPOLLOUT。
